"""Run independent LLM batches on a thread pool, results in submission order.

Every LLM stage used to run its batches back to back; stage 4 alone was ~35%
of a run's wall time and stage 3b another ~15% (#881). The batches inside a
stage are independent of one another, so the only thing a pool has to get
right is everything *around* the call: the per-run context, the result
order, and what happens to the queue when one call fails.
"""

import contextvars
import logging
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TypeVar

from unified_pipeline.llm.retry import _get_llm_config_int

T = TypeVar("T")

logger = logging.getLogger(__name__)


def map_in_order(
    fn: Callable[..., T],
    args: Iterable[tuple],
    workers: int,
    on_result: Callable[[int, T], None] | None = None,
) -> list[T]:
    """Call ``fn(*a)`` for each ``a`` in ``args`` on ``workers`` threads; return
    the results in submission order, so a caller's output is byte-identical to
    the serial loop it replaced.

    ``args`` may be any iterable, including a generator -- it is consumed
    exactly once, up front, so the same materialised list backs whichever
    path below runs.

    Each call runs in a copy of the caller's contextvars.Context: prompt_logger
    scopes its per-run log directory (#580) with a ContextVar, and a bare pool
    thread starts with an empty context and would write into the shared flat
    directory instead. The first exception cancels the queued calls and
    re-raises; calls already in flight finish, since a thread cannot be
    interrupted, so at most ``workers`` complete after a failure.

    ``on_result(index, result)``, when given, fires once per call on the
    CALLING thread -- never on a pool thread -- so a caller that prints
    through a thread-routed stdout (the orchestrator's ``_RoutedStdout``,
    which dispatches ``write()`` by ``threading.get_ident()``) stays routed:
    pool threads are never registered with it, so anything printed from one
    bypasses the run's capture entirely. In the pool path ``on_result`` fires
    in completion order, not submission order -- the returned list is still
    in submission order regardless.

    ``workers=1`` is a true serial loop: no pool, no threads, one context.
    """
    arg_list = list(args)

    if workers < 1:
        raise ValueError(
            f"map_in_order: workers must be >= 1, got {workers}. "
            f"Pass workers=1 for serial execution."
        )

    if workers == 1:
        ctx = contextvars.copy_context()
        results = []
        for i, a in enumerate(arg_list):
            result = ctx.run(fn, *a)
            results.append(result)
            if on_result is not None:
                on_result(i, result)
        return results

    with ThreadPoolExecutor(max_workers=workers) as pool:
        # Each submission gets its own context snapshot, copied at submit
        # time rather than shared, so one call's contextvars mutations never
        # bleed into a sibling. Copying is O(len(context)) per item --
        # microseconds -- against the seconds an LLM call takes, so N
        # copies for N batches is not a cost worth avoiding.
        futures = [pool.submit(contextvars.copy_context().run, fn, *a) for a in arg_list]
        future_index = {future: i for i, future in enumerate(futures)}
        results: list[T] = [None] * len(futures)  # type: ignore[list-item]
        try:
            for future in as_completed(futures):
                result = future.result()
                idx = future_index[future]
                results[idx] = result
                if on_result is not None:
                    on_result(idx, result)
            return results
        except BaseException:
            # Count calls that finished before the failure so the caller
            # knows how much work is being thrown away, not just that some
            # is. A future that is done() and not cancelled() may itself
            # have failed; only count ones whose .result() does not raise.
            completed = 0
            for future in futures:
                if future.done() and not future.cancelled():
                    try:
                        future.result()
                    except BaseException:  # noqa: BLE001 -- a failed future just isn't counted
                        continue
                    completed += 1
            if completed > 0:
                logger.warning(
                    "map_in_order: %d/%d calls completed before the failure; "
                    "their results are discarded",
                    completed,
                    len(futures),
                )
            pool.shutdown(wait=False, cancel_futures=True)
            raise


def make_progress_printer(
    format_lines: Callable[[int, int, T], list[str]],
) -> Callable[[int, T], None]:
    """Build a map_in_order ``on_result`` callback that prints one block per
    finished call: ``format_lines(done, index, result)``, joined into ONE
    ``print`` so two calls' lines cannot splice.

    ``done`` counts completions (1, 2, 3... in call order), so an ``[N/M]``
    line built from it stays monotonic however the pool orders completions;
    ``index`` is the submission position, for looking up which input
    finished. The closure's counter needs no lock: map_in_order fires
    ``on_result`` only on the calling thread, one call at a time (see its
    docstring; pinned by test_on_result_runs_on_the_calling_thread).
    Stages 2, 3b and 5d share this (#923).
    """
    done = 0

    def on_result(index: int, result: T) -> None:
        nonlocal done
        done += 1
        print("\n".join(format_lines(done, index, result)))

    return on_result


def workers_from_config(key: str, default: int = 4) -> int:
    """The deployment knob for a stage's pool width.

    Resolution order matches every other LLM knob (`llm/retry.py`): an env
    var named ``key`` wins, then the ``llm`` yaml section's ``key``, then
    ``default``. Default of 4: stages are I/O-bound (LLM round trips, not
    CPU), and 4 keeps one run under half of `llm/retry.py`'s per-pod
    semaphore (``CVICHE_MAX_CONCURRENT_LLM_CALLS``, default 8), so up to
    three concurrent runs on a pod each still get a slot. Raise the two
    together.
    """
    return _get_llm_config_int(key, default, min_value=1)


def make_batches(items: list[T], batch_size: int) -> list[list[T]]:
    """Split ``items`` into consecutive chunks of at most ``batch_size``.

    The one place the ceiling-division/slice pattern lives; stage 4 switches
    to it in #882, stages 2 and 1b migrate with their own PRs.
    """
    if batch_size < 1:
        raise ValueError(f"make_batches: batch_size must be >= 1, got {batch_size}")
    return [items[i : i + batch_size] for i in range(0, len(items), batch_size)]
