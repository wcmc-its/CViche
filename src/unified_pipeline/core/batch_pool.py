"""Run independent LLM batches on a thread pool, results in submission order.

Every LLM stage used to run its batches back to back; stage 4 alone was ~35%
of a run's wall time and stage 3b another ~15% (#881). The batches inside a
stage are independent of one another, so the only thing a pool has to get
right is everything *around* the call: the per-run context, the result
order, and what happens to the queue when one call fails.
"""

import contextvars
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor
from typing import TypeVar

T = TypeVar("T")


def map_in_order(fn: Callable[..., T], args: Iterable[tuple], workers: int) -> list[T]:
    """Call ``fn(*a)`` for each ``a`` in ``args`` on ``workers`` threads; return
    the results in submission order, so a caller's output is byte-identical to
    the serial loop it replaced.

    Each call runs in a copy of the caller's contextvars.Context: prompt_logger
    scopes its per-run log directory (#580) with a ContextVar, and a bare pool
    thread starts with an empty context and would write into the shared flat
    directory instead. The first exception cancels the queued calls and
    re-raises; calls already in flight finish, since a thread cannot be
    interrupted, so at most ``workers`` complete after a failure. ``workers=1``
    is a plain serial loop.
    """
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(contextvars.copy_context().run, fn, *a) for a in args]
        try:
            return [future.result() for future in futures]
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
