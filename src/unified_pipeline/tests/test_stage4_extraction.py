"""#881: `stage4.extraction.extract_fields_from_mapped_entries` runs its
batches on a thread pool (`_extract_batches`) instead of one after another.

What parallelism can break, and what each test pins:

- output order -- results are reassembled in batch order, not completion
  order, so the artifact is byte-identical to the serial loop's;
- contextvars -- prompt_logger's per-run log directory (#580) rides a
  ContextVar that a bare pool thread does not inherit; every task runs in a
  copy of the caller's context;
- cancellation -- a cancel raised in one task drops the queued ones rather
  than letting the pool drain every batch at the caller's expense;
- accounting -- cost/token totals and failed_batches sum over every batch
  no matter which thread ran it;
- `workers=1` is the pre-#881 serial loop.

    python3 -m pytest src/unified_pipeline/tests/test_stage4_extraction.py -p no:cacheprovider

No network, no LLM: `extract_fields_batch`, `extract_cv_owner_name` and
`infer_cv_owner_location` are stubbed on the `extraction` module attribute
the loop reads. Synthetic entries only.
"""

import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import unified_pipeline.stage4.extraction as extraction  # noqa: E402
from unified_pipeline.core import prompt_logger  # noqa: E402

_NO_OWNER = {
    "first_name": "", "middle_name": "", "last_name": "",
    "suffix": "", "full_name": "", "full_name_with_credentials": "",
}


def _entries(n: int) -> list[dict]:
    # Same element_idx everywhere so the post-loop element_idx sort (stable)
    # cannot hide a batch-order regression.
    return [{"text": f"entry number {i:03d}", "element_idx": 0, "taxonomy_code": "A1"} for i in range(n)]


def _stub_owner(monkeypatch) -> None:
    monkeypatch.setattr(extraction, "extract_cv_owner_name", lambda *a, **k: dict(_NO_OWNER))
    monkeypatch.setattr(extraction, "infer_cv_owner_location", lambda entries: {})


def _batch_result(entries, cost=0.01, tokens=10, success=True):
    return {
        "entries": [dict(e, extraction_success=True) for e in entries],
        "cost": cost, "tokens": tokens,
        "cache_read_tokens": 1, "cache_write_tokens": 2,
        "success": success, "failed_groups": 0 if success else 1,
    }


def test_results_are_in_batch_order_even_when_later_batches_finish_first(monkeypatch):
    _stub_owner(monkeypatch)
    n = 6

    def slow_early_batches(entries, batch_idx, total, cv_owner_name):
        time.sleep((n - batch_idx) * 0.01)  # batch 0 finishes last
        return _batch_result(entries)

    monkeypatch.setattr(extraction, "extract_fields_batch", slow_early_batches)

    out = extraction.extract_fields_from_mapped_entries(_entries(n), batch_size=1, workers=3)

    assert [e["text"] for e in out["entries"]] == [f"entry number {i:03d}" for i in range(n)]


def test_pool_threads_run_inside_the_callers_run_id_context(monkeypatch):
    _stub_owner(monkeypatch)
    seen: list[tuple[str | None, bool]] = []
    lock = threading.Lock()
    main = threading.current_thread()

    def record_context(entries, batch_idx, total, cv_owner_name):
        with lock:
            seen.append((prompt_logger._current_run_id.get(), threading.current_thread() is main))
        return _batch_result(entries)

    monkeypatch.setattr(extraction, "extract_fields_batch", record_context)
    token = prompt_logger.set_current_run_id("run-881")
    try:
        extraction.extract_fields_from_mapped_entries(_entries(4), batch_size=1, workers=2)
    finally:
        prompt_logger.reset_current_run_id(token)

    assert all(run_id == "run-881" for run_id, _ in seen), seen
    # The context really had to cross a thread boundary -- a pool thread, not
    # the caller, ran every batch.
    assert not any(on_main for _, on_main in seen), seen


def test_no_llm_call_starts_after_a_cancel(monkeypatch):
    _stub_owner(monkeypatch)
    n, workers, cancel_after = 40, 2, 3
    checks = 0
    cancelled = threading.Event()
    started_after_cancel = 0
    lock = threading.Lock()

    class Cancelled(Exception):
        pass

    def cancel_check():
        nonlocal checks
        with lock:
            checks += 1
            if checks > cancel_after:
                cancelled.set()
                raise Cancelled()

    def llm_batch(entries, batch_idx, total, cv_owner_name):
        nonlocal started_after_cancel
        if cancelled.is_set():
            with lock:
                started_after_cancel += 1
        time.sleep(0.01)
        return _batch_result(entries)

    monkeypatch.setattr(extraction, "extract_fields_batch", llm_batch)

    with pytest.raises(Cancelled):
        extraction.extract_fields_from_mapped_entries(
            _entries(n), batch_size=1, workers=workers, cancel_check=cancel_check,
        )

    # cancel_check gates every task before its LLM call, so once it has raised
    # no further batch reaches extract_fields_batch. In-flight ones finish.
    assert started_after_cancel == 0


def test_a_failing_batch_drops_the_queued_batches(monkeypatch):
    _stub_owner(monkeypatch)
    n, workers, failing = 40, 2, 2
    calls = 0
    lock = threading.Lock()

    def explode_on_one(entries, batch_idx, total, cv_owner_name):
        nonlocal calls
        with lock:
            calls += 1
        time.sleep(0.02)
        if batch_idx == failing:
            raise RuntimeError("provider blew up")
        return _batch_result(entries)

    monkeypatch.setattr(extraction, "extract_fields_batch", explode_on_one)

    with pytest.raises(RuntimeError, match="provider blew up"):
        extraction.extract_fields_from_mapped_entries(_entries(n), batch_size=1, workers=workers)

    # Without shutdown(cancel_futures=True) the pool would drain all 40
    # batches -- 40 paid LLM calls after the run had already failed. With it,
    # only the ones dequeued before the caller saw the failure run.
    assert calls < n // 2, calls


def test_totals_sum_over_every_batch_across_threads(monkeypatch):
    _stub_owner(monkeypatch)

    def one_failed(entries, batch_idx, total, cv_owner_name):
        return _batch_result(entries, cost=0.5, tokens=100, success=(batch_idx != 2))

    monkeypatch.setattr(extraction, "extract_fields_batch", one_failed)

    out = extraction.extract_fields_from_mapped_entries(_entries(10), batch_size=2, workers=4)

    assert out["stats"]["batches_processed"] == 5
    assert out["total_cost"] == pytest.approx(2.5)
    assert out["total_tokens"] == 500
    assert out["cache_read_tokens"] == 5 and out["cache_write_tokens"] == 10
    assert out["stats"]["failed_batches"] == 1 and out["partial_success"] is True
    assert len(out["entries"]) == 10


def test_workers_one_is_strictly_serial_and_ordered(monkeypatch):
    _stub_owner(monkeypatch)
    order: list[int] = []
    in_flight = 0
    peak = 0
    lock = threading.Lock()

    def track(entries, batch_idx, total, cv_owner_name):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
            order.append(batch_idx)
        time.sleep(0.005)
        with lock:
            in_flight -= 1
        return _batch_result(entries)

    monkeypatch.setattr(extraction, "extract_fields_batch", track)

    extraction.extract_fields_from_mapped_entries(_entries(5), batch_size=1, workers=1)

    assert order == [0, 1, 2, 3, 4]
    assert peak == 1


def test_workers_below_one_is_rejected(monkeypatch):
    # ThreadPoolExecutor's own check; pinned so a future hand-rolled pool
    # cannot silently accept 0 and hang.
    _stub_owner(monkeypatch)
    with pytest.raises(ValueError, match="max_workers"):
        extraction.extract_fields_from_mapped_entries(_entries(1), workers=0)
