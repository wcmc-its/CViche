"""`core.batch_pool.map_in_order` (#881): the thread pool every LLM stage's
independent batches run on. What a pool can break, and what each test pins:

- output order -- results come back in submission order, never completion
  order, so a caller's artifact is byte-identical to its old serial loop;
- contextvars -- prompt_logger's per-run log directory (#580) rides a
  ContextVar a bare pool thread does not inherit; every call runs in a copy
  of the caller's context;
- failure -- the first exception cancels the queued calls rather than
  letting the pool drain every batch at the caller's expense;
- `workers=1` is a plain serial loop, not a one-worker pool.

    python3 -m pytest src/unified_pipeline/tests/test_batch_pool.py -p no:cacheprovider

No network, no LLM, no files.
"""

import contextvars
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from unified_pipeline.core import prompt_logger  # noqa: E402
from unified_pipeline.core.batch_pool import (  # noqa: E402
    make_batches,
    map_in_order,
    workers_from_config,
)


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
    started = set()
    lock = threading.Lock()

    def explode_on_one(i):
        nonlocal calls
        with lock:
            calls += 1
            started.add(i)
        time.sleep(0.02)
        if i == failing:
            raise RuntimeError("provider blew up")
        return i

    with pytest.raises(RuntimeError, match="provider blew up"):
        map_in_order(explode_on_one, [(i,) for i in range(n)], workers=workers)

    # At most workers*2 already in flight or dequeued before the cancel, plus
    # slack for scheduling jitter. Anything well beyond that means
    # cancel_futures=True is not working.
    assert calls <= workers * 3, (
        f"Expected at most {workers * 3} calls, got {calls}. "
        f"cancel_futures=True may not be working."
    )
    # The failing call must have run
    assert failing in started


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


@pytest.mark.parametrize("bad_workers", [0, -1, -100])
def test_workers_below_one_is_rejected(bad_workers):
    # map_in_order's own validation, so the error names map_in_order and the
    # bad value rather than surfacing ThreadPoolExecutor's unrelated message.
    with pytest.raises(ValueError, match="workers must be >= 1"):
        map_in_order(lambda i: i, [(0,)], workers=bad_workers)


def test_original_exception_propagates_unchanged():
    """The pool must not wrap or replace the exception raised by fn."""

    class CustomError(Exception):
        def __init__(self):
            super().__init__("exact message")
            self.custom_attr = "preserved"

    def raises(_):
        raise CustomError()

    with pytest.raises(CustomError) as exc_info:
        map_in_order(raises, [(0,)], workers=2)

    # Type preserved -- not wrapped in ExecutionException or similar
    assert type(exc_info.value) is CustomError
    # Message preserved
    assert str(exc_info.value) == "exact message"
    # Custom attributes preserved
    assert exc_info.value.custom_attr == "preserved"


def test_keyboard_interrupt_cancels_pool_and_propagates():
    """BaseException subclasses must cancel the pool and re-raise, not just Exception."""
    calls = 0
    lock = threading.Lock()

    def count_and_maybe_interrupt(i):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.02)
        if i == 1:
            raise KeyboardInterrupt()
        return i

    with pytest.raises(KeyboardInterrupt):
        map_in_order(
            count_and_maybe_interrupt,
            [(i,) for i in range(20)],
            workers=2,
        )

    assert calls < 10, "Pool should have been cancelled on KeyboardInterrupt"


def test_empty_args_returns_empty_list():
    """map_in_order with no work items must return [] not raise."""
    result = map_in_order(lambda: None, [], workers=4)
    assert result == []


def test_generator_args_are_fully_consumed():
    """args may be a generator -- must not be consumed twice."""

    def gen():
        yield (1,)
        yield (2,)
        yield (3,)

    result = map_in_order(lambda x: x * 2, gen(), workers=2)
    assert result == [2, 4, 6]


def test_context_mutations_in_one_call_do_not_leak_to_sibling_calls():
    """Each call runs in its own context copy -- mutations must not bleed."""
    seen_values: dict[int, str | None] = {}
    lock = threading.Lock()

    _var: contextvars.ContextVar[str | None] = contextvars.ContextVar("test_var", default=None)

    def mutate_and_record(i):
        # Each call mutates the ContextVar
        _var.set(f"call-{i}")
        time.sleep(0.01)
        with lock:
            seen_values[i] = _var.get()
        return i

    map_in_order(mutate_and_record, [(i,) for i in range(4)], workers=4)

    # Each call should only see its own mutation
    for i, val in seen_values.items():
        assert val == f"call-{i}", f"Call {i} saw {val!r} -- context leaked from another call"


def test_fn_returning_none_is_valid():
    """None is a legitimate return value, not a signal of failure."""
    result = map_in_order(lambda i: None, [(0,), (1,), (2,)], workers=2)
    assert result == [None, None, None]


def test_all_calls_fail_first_exception_propagates():
    """When every call fails the first exception must still propagate cleanly."""

    def always_fail(i):
        time.sleep(0.01 * i)
        raise ValueError(f"fail-{i}")

    with pytest.raises(ValueError, match="fail-0"):
        map_in_order(always_fail, [(i,) for i in range(10)], workers=3)


def test_on_result_runs_on_the_calling_thread():
    main = threading.current_thread()
    callback_threads = []
    lock = threading.Lock()

    def slow(i):
        time.sleep(0.01)
        return i

    def on_result(_i, _result):
        with lock:
            callback_threads.append(threading.current_thread() is main)

    map_in_order(slow, [(i,) for i in range(6)], workers=4, on_result=on_result)

    assert callback_threads and all(callback_threads), callback_threads


def test_on_result_fires_in_completion_order_not_submission_order():
    n = 6
    callback_indices: list[int] = []
    lock = threading.Lock()

    def slow_early(i):
        time.sleep((n - i) * 0.02)  # call 0 finishes last, call n-1 finishes first
        return i

    def on_result(i, _result):
        with lock:
            callback_indices.append(i)

    result = map_in_order(slow_early, [(i,) for i in range(n)], workers=n, on_result=on_result)

    # Returned list is always in submission order.
    assert result == list(range(n))
    # But the callback saw completion order -- not ascending.
    assert callback_indices != sorted(callback_indices), callback_indices
    assert sorted(callback_indices) == list(range(n))


def test_fast_path_runs_on_calling_thread_and_calls_on_result_in_order():
    main = threading.current_thread()
    callback_indices: list[int] = []

    def record(i):
        assert threading.current_thread() is main
        return i * 2

    def on_result(i, result):
        callback_indices.append(i)

    result = map_in_order(record, [(i,) for i in range(4)], workers=1, on_result=on_result)

    assert result == [0, 2, 4, 6]
    assert callback_indices == [0, 1, 2, 3]


def test_workers_from_config_reads_env_var(monkeypatch):
    monkeypatch.setenv("CVICHE_TEST_BATCH_POOL_WORKERS", "7")
    assert workers_from_config("CVICHE_TEST_BATCH_POOL_WORKERS", default=4) == 7


def test_workers_from_config_ignores_garbage(monkeypatch):
    monkeypatch.setenv("CVICHE_TEST_BATCH_POOL_WORKERS", "abc")
    assert workers_from_config("CVICHE_TEST_BATCH_POOL_WORKERS", default=4) == 4


def test_workers_from_config_floors_at_default_not_zero(monkeypatch):
    monkeypatch.setenv("CVICHE_TEST_BATCH_POOL_WORKERS", "0")
    assert workers_from_config("CVICHE_TEST_BATCH_POOL_WORKERS", default=4) == 4


def test_make_batches_splits_by_batch_size():
    assert make_batches([1, 2, 3, 4, 5, 6, 7], 3) == [[1, 2, 3], [4, 5, 6], [7]]


def test_make_batches_empty_items_returns_empty_list():
    assert make_batches([], 3) == []


def test_make_batches_rejects_batch_size_below_one():
    with pytest.raises(ValueError):
        make_batches([1, 2, 3], 0)
