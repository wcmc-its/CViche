"""`core.batch_pool.map_in_order` (#881): the thread pool every LLM stage's
independent batches run on. What a pool can break, and what each test pins:

- output order -- results come back in submission order, never completion
  order, so a caller's artifact is byte-identical to its old serial loop;
- contextvars -- prompt_logger's per-run log directory (#580) rides a
  ContextVar a bare pool thread does not inherit; every call runs in a copy
  of the caller's context;
- failure -- the first exception cancels the queued calls rather than
  letting the pool drain every batch at the caller's expense;
- `workers=1` is a plain serial loop.

    python3 -m pytest src/unified_pipeline/tests/test_batch_pool.py -p no:cacheprovider

No network, no LLM, no files.
"""

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from unified_pipeline.core import prompt_logger  # noqa: E402
from unified_pipeline.core.batch_pool import map_in_order  # noqa: E402


def test_results_are_in_submission_order_even_when_later_calls_finish_first():
    n = 6

    def slow_early(i):
        time.sleep((n - i) * 0.01)  # call 0 finishes last
        return i * 10

    assert map_in_order(slow_early, [(i,) for i in range(n)], workers=3) == [i * 10 for i in range(n)]


def test_calls_run_inside_the_callers_run_id_context_on_pool_threads():
    seen: list[tuple[str | None, bool]] = []
    lock = threading.Lock()
    main = threading.current_thread()

    def record(i):
        with lock:
            seen.append((prompt_logger._current_run_id.get(), threading.current_thread() is main))
        return i

    token = prompt_logger.set_current_run_id("run-881")
    try:
        map_in_order(record, [(i,) for i in range(4)], workers=2)
    finally:
        prompt_logger.reset_current_run_id(token)

    assert all(run_id == "run-881" for run_id, _ in seen), seen
    # The context really had to cross a thread boundary.
    assert not any(on_main for _, on_main in seen), seen


def test_first_failure_drops_the_queued_calls():
    n, workers, failing = 40, 2, 2
    calls = 0
    lock = threading.Lock()

    def explode_on_one(i):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.02)
        if i == failing:
            raise RuntimeError("provider blew up")
        return i

    with pytest.raises(RuntimeError, match="provider blew up"):
        map_in_order(explode_on_one, [(i,) for i in range(n)], workers=workers)

    # Without shutdown(cancel_futures=True) the pool would drain all 40 calls
    # after the failure. With it, only the ones dequeued before the caller
    # saw the exception run.
    assert calls < n // 2, calls


def test_workers_one_is_strictly_serial_and_ordered():
    order: list[int] = []
    in_flight = peak = 0
    lock = threading.Lock()

    def track(i):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
            order.append(i)
        time.sleep(0.005)
        with lock:
            in_flight -= 1
        return i

    map_in_order(track, [(i,) for i in range(5)], workers=1)

    assert order == [0, 1, 2, 3, 4]
    assert peak == 1


def test_multiple_args_are_passed_through():
    assert map_in_order(lambda a, b: a + b, [(1, 2), (3, 4)], workers=2) == [3, 7]


def test_workers_below_one_is_rejected():
    # ThreadPoolExecutor's own check; pinned so a future hand-rolled pool
    # cannot silently accept 0 and hang.
    with pytest.raises(ValueError, match="max_workers"):
        map_in_order(lambda i: i, [(0,)], workers=0)
