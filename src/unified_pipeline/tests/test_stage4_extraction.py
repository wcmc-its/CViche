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

import json
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from openai import APITimeoutError

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import unified_pipeline.stage4.extraction as extraction  # noqa: E402
from unified_pipeline.core import prompt_logger  # noqa: E402
from unified_pipeline.core.batch_pool import workers_from_config  # noqa: E402

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

    def slow_early_batches(entries, batch_idx, total, cv_owner_name, cancel_check=None):
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

    def record_context(entries, batch_idx, total, cv_owner_name, cancel_check=None):
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

    def run_cancel_check():
        nonlocal checks
        with lock:
            checks += 1
            if checks > cancel_after:
                cancelled.set()
                raise Cancelled()

    def llm_batch(entries, batch_idx, total, cv_owner_name, cancel_check=None):
        nonlocal started_after_cancel
        # The pool must hand the run's cancel_check to every batch, or the
        # between-group and between-retry checks inside never fire.
        assert cancel_check is run_cancel_check
        if cancelled.is_set():
            with lock:
                started_after_cancel += 1
        time.sleep(0.01)
        return _batch_result(entries)

    monkeypatch.setattr(extraction, "extract_fields_batch", llm_batch)

    with pytest.raises(Cancelled):
        extraction.extract_fields_from_mapped_entries(
            _entries(n), batch_size=1, workers=workers, cancel_check=run_cancel_check,
        )

    # cancel_check gates every task before its LLM call, so once it has raised
    # no further batch reaches extract_fields_batch. In-flight ones finish.
    assert started_after_cancel == 0


def test_a_failing_batch_drops_the_queued_batches(monkeypatch):
    _stub_owner(monkeypatch)
    n, workers, failing = 40, 2, 2
    calls = 0
    lock = threading.Lock()

    def explode_on_one(entries, batch_idx, total, cv_owner_name, cancel_check=None):
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

    def one_failed(entries, batch_idx, total, cv_owner_name, cancel_check=None):
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

    def track(entries, batch_idx, total, cv_owner_name, cancel_check=None):
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
    # map_in_order's own check (core/batch_pool.py, #882); pinned so a future
    # caller cannot silently pass 0 and hang.
    _stub_owner(monkeypatch)
    with pytest.raises(ValueError, match="workers must be >= 1"):
        extraction.extract_fields_from_mapped_entries(_entries(1), workers=0)


def test_workers_config_knob_is_read_from_env(monkeypatch):
    # STAGE4_BATCH_WORKERS itself is bound once, at import time, so it can't
    # observe an env var set by a test -- this pins the reader it's built
    # from instead: workers_from_config("CVICHE_STAGE4_BATCH_WORKERS").
    monkeypatch.setenv("CVICHE_STAGE4_BATCH_WORKERS", "2")
    assert workers_from_config("CVICHE_STAGE4_BATCH_WORKERS") == 2


# ---------------------------------------------------------------------------
# batch_size validation
# ---------------------------------------------------------------------------

def test_batch_size_below_one_raises_value_error(monkeypatch):
    _stub_owner(monkeypatch)
    with pytest.raises(ValueError, match="batch_size must be >= 1"):
        extraction.extract_fields_from_mapped_entries(_entries(1), batch_size=0)


# ---------------------------------------------------------------------------
# partial_success
# ---------------------------------------------------------------------------

def test_partial_success_is_false_when_no_batch_failed(monkeypatch):
    _stub_owner(monkeypatch)
    monkeypatch.setattr(
        extraction, "extract_fields_batch",
        lambda entries, batch_idx, total, cv_owner_name, cancel_check=None: _batch_result(entries),
    )

    out = extraction.extract_fields_from_mapped_entries(_entries(4), batch_size=2, workers=2)

    assert out["partial_success"] is False
    assert out["stats"]["failed_batches"] == 0


# ---------------------------------------------------------------------------
# "no valid entries" early-out
# ---------------------------------------------------------------------------

def test_no_valid_entries_early_out_never_calls_extract_fields_batch(monkeypatch):
    _stub_owner(monkeypatch)
    calls: list[int] = []
    monkeypatch.setattr(
        extraction, "extract_fields_batch",
        lambda *a, **k: calls.append(1) or _batch_result([]),
    )
    entries = [
        {"text": "", "element_idx": 0, "taxonomy_code": "A1"},
        {"text": "ab", "element_idx": 1, "taxonomy_code": "A1"},
    ]

    out = extraction.extract_fields_from_mapped_entries(entries, batch_size=5)

    assert calls == []  # attempted 0 -- the early-out never reaches the pool
    assert len(out["entries"]) == 2  # both entries come back skipped
    assert out["success"] is True


# ---------------------------------------------------------------------------
# skip-reason tagging
# ---------------------------------------------------------------------------

def test_skip_reason_tagging_empty_vs_minimal_text(monkeypatch):
    _stub_owner(monkeypatch)

    def never_called(*a, **k):
        raise AssertionError("extract_fields_batch must not run -- every entry is skipped")

    monkeypatch.setattr(extraction, "extract_fields_batch", never_called)
    entries = [
        {"text": "", "element_idx": 0, "taxonomy_code": "A1"},
        {"text": "abc", "element_idx": 1, "taxonomy_code": "A1"},
    ]

    out = extraction.extract_fields_from_mapped_entries(entries, batch_size=5)

    reasons = {e["text"]: e["skip_reason"] for e in out["entries"]}
    assert reasons[""] == "empty_text"
    assert reasons["abc"] == "empty_or_minimal_text"


# ---------------------------------------------------------------------------
# needs_llm_recovery
# ---------------------------------------------------------------------------

def test_needs_llm_recovery_true_on_total_extraction_loss():
    # >=50 chars of original text but every extracted field is falsy --
    # sufficient on its own regardless of the length/date/structure checks.
    entry = {
        "text": "x" * 60,
        "extracted_fields": {"a": None, "b": ""},
        "extraction_coverage": {},
    }
    assert extraction.needs_llm_recovery(entry) is True


def test_needs_llm_recovery_false_when_text_too_short():
    entry = {
        "text": "short",
        "extracted_fields": {"a": "short"},
        "extraction_coverage": {"extraction_coverage_percent": 10.0},
    }
    assert extraction.needs_llm_recovery(entry) is False


def test_needs_llm_recovery_true_when_low_coverage_and_has_dates():
    text = (
        "This is a long entry with plenty of content padding to exceed the "
        "two hundred character floor required before the coverage check even "
        "runs, and it mentions the year 2015 somewhere in the middle of it."
    )
    assert len(text) >= 200
    entry = {
        "text": text,
        "extracted_fields": {"note": "2015"},
        "extraction_coverage": {"extraction_coverage_percent": 5.0},
    }
    assert extraction.needs_llm_recovery(entry) is True


# ---------------------------------------------------------------------------
# attempt_llm_recovery -- one test per exception path it distinguishes
# ---------------------------------------------------------------------------

def _one_recovery_entry() -> list[dict]:
    return [{
        "taxonomy_code": "A1",
        "text": "some messy table text",
        "element_idx_start": 0,
        "element_idx_end": 0,
    }]


def test_attempt_llm_recovery_timeout_sets_llm_timeout_error(monkeypatch):
    def boom(**kwargs):
        raise APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))

    monkeypatch.setattr(extraction, "call_llm", boom)

    result = extraction.attempt_llm_recovery(_one_recovery_entry())

    assert result["entries"][0]["llm_recovery_error"] == extraction.LLM_TIMEOUT
    assert result["cost"] == 0.0


def test_attempt_llm_recovery_json_decode_error_sets_response_invalid(monkeypatch):
    def not_json(**kwargs):
        return {"content": "not valid json", "cost": 0.02, "total_tokens": 7}

    monkeypatch.setattr(extraction, "call_llm", not_json)

    result = extraction.attempt_llm_recovery(_one_recovery_entry())

    assert result["entries"][0]["llm_recovery_error"] == extraction.LLM_RESPONSE_INVALID
    assert result["cost"] == pytest.approx(0.02)  # the failed call's spend is still counted


def test_attempt_llm_recovery_validation_error_sets_response_invalid(monkeypatch):
    def wrong_shape(**kwargs):
        # Missing the required "entry_id" on the one recovered entry.
        return {
            "content": json.dumps({"recovered_entries": [{"fields": {}}]}),
            "cost": 0.01,
            "total_tokens": 5,
        }

    monkeypatch.setattr(extraction, "call_llm", wrong_shape)

    result = extraction.attempt_llm_recovery(_one_recovery_entry())

    assert result["entries"][0]["llm_recovery_error"] == extraction.LLM_RESPONSE_INVALID
    assert result["cost"] == pytest.approx(0.01)


def test_attempt_llm_recovery_generic_error_sets_provider_error(monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("provider blew up")

    monkeypatch.setattr(extraction, "call_llm", boom)

    result = extraction.attempt_llm_recovery(_one_recovery_entry())

    assert result["entries"][0]["llm_recovery_error"] == extraction.LLM_PROVIDER_ERROR


# ---------------------------------------------------------------------------
# extract_fields_batch -- real implementation, call_llm stubbed
# (doubles as the reply evidence for threads 4044649899/4044657601/4044660879:
# a malformed LLM item is dropped with a warning, not raised)
# ---------------------------------------------------------------------------

def test_extract_fields_batch_real_implementation_drops_malformed_item(monkeypatch, caplog):
    entries = [
        {"text": "hello world", "taxonomy_code": "A1", "element_idx_start": 0, "element_idx_end": 0},
        {"text": "second entry text here", "taxonomy_code": "A1", "element_idx_start": 1, "element_idx_end": 1},
    ]

    def fake_call_llm(**kwargs):
        return {
            "content": json.dumps({"entries": [
                {"entry_index": 0, "note": "hello world"},
                {"entry_index": 1, "note": "second entry text here"},
                {"note": "missing its entry_index"},  # malformed -- dropped
            ]}),
            "cost": 0.02,
            "total_tokens": 42,
            "cache_read_tokens": 1,
            "cache_write_tokens": 2,
        }

    monkeypatch.setattr(extraction, "call_llm", fake_call_llm)

    with caplog.at_level("WARNING"):
        result = extraction.extract_fields_batch(entries, 0, 1)

    assert result["cost"] == pytest.approx(0.02)
    assert result["tokens"] == 42
    assert result["cache_read_tokens"] == 1
    assert result["cache_write_tokens"] == 2
    assert result["success"] is True
    assert len(result["entries"]) == 2
    by_text = {e["text"]: e for e in result["entries"]}
    assert by_text["hello world"]["extracted_fields"]["note"] == "hello world"
    assert by_text["second entry text here"]["extracted_fields"]["note"] == "second entry text here"
    assert "dropped one malformed LLM entry" in caplog.text


def test_extract_fields_batch_cancels_between_taxonomy_groups(monkeypatch):
    calls = {"n": 0}

    def fake_call_llm(**kwargs):
        calls["n"] += 1
        return {
            "content": json.dumps({"entries": []}),
            "cost": 0.0, "total_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
        }

    monkeypatch.setattr(extraction, "call_llm", fake_call_llm)

    class Cancelled(Exception):
        pass

    checks = {"n": 0}

    def cancel_check():
        checks["n"] += 1
        if checks["n"] == 2:
            raise Cancelled()

    entries = [
        {"text": "entry one", "taxonomy_code": "A1", "element_idx_start": 0, "element_idx_end": 0},
        {"text": "entry two", "taxonomy_code": "B2", "element_idx_start": 1, "element_idx_end": 1},
    ]

    with pytest.raises(Cancelled):
        extraction.extract_fields_batch(entries, 0, 1, cancel_check=cancel_check)

    # The second group's cancel_check() raises before its call_llm -- the
    # first group's call already went through.
    assert calls["n"] == 1


def test_attempt_llm_recovery_forwards_cancel_check_into_call_llm(monkeypatch):
    recorded_kwargs = {}

    def fake_call_llm(**kwargs):
        recorded_kwargs.update(kwargs)
        return {
            "content": json.dumps({
                "recovered_entries": [{"entry_id": "0_0", "fields": {"note": "recovered"}}],
                "recovery_notes": "",
            }),
            "cost": 0.01,
            "total_tokens": 5,
        }

    monkeypatch.setattr(extraction, "call_llm", fake_call_llm)

    sentinel = object()
    extraction.attempt_llm_recovery(_one_recovery_entry(), cancel_check=sentinel)

    assert recorded_kwargs["cancel_check"] is sentinel


def _long_low_coverage_entry() -> dict:
    # >=200 chars (crosses needs_llm_recovery's length floor) with a year in
    # it (satisfies the has_dates check), so a main-extraction response that
    # extracts an unrelated field leaves coverage near zero without tripping
    # the total-extraction-loss shortcut (which needs `extracted_fields`
    # entirely falsy).
    text = "Long messy entry with plenty of padding text " * 5 + "seen in 2015."
    assert len(text) >= 200
    return {
        "text": text,
        "taxonomy_code": "A1",
        "element_idx_start": 0,
        "element_idx_end": 0,
    }


def test_extract_fields_batch_cancels_before_recovery_call(monkeypatch):
    calls = {"n": 0}

    def fake_call_llm(**kwargs):
        calls["n"] += 1
        # Main-extraction response: a field that shares no words with the
        # entry's text, so extraction coverage is near zero and
        # needs_llm_recovery is True.
        return {
            "content": json.dumps({"entries": [{"entry_index": 0, "note": "zzz"}]}),
            "cost": 0.0, "total_tokens": 0,
            "cache_read_tokens": 0, "cache_write_tokens": 0,
        }

    monkeypatch.setattr(extraction, "call_llm", fake_call_llm)

    class Cancelled(Exception):
        pass

    checks = {"n": 0}

    def cancel_check():
        checks["n"] += 1
        if checks["n"] == 2:
            raise Cancelled()

    with pytest.raises(Cancelled):
        extraction.extract_fields_batch(
            [_long_low_coverage_entry()], 0, 1, cancel_check=cancel_check,
        )

    # Second cancel_check() invocation (before the recovery group's call)
    # raises -- the recovery call_llm must never have started.
    assert calls["n"] == 1


def test_extract_fields_batch_forwards_cancel_check_into_recovery_call_llm(monkeypatch):
    recorded_kwargs = {}
    calls = {"n": 0}

    def fake_call_llm(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # Main-extraction call: unrelated field -> low coverage.
            return {
                "content": json.dumps({"entries": [{"entry_index": 0, "note": "zzz"}]}),
                "cost": 0.0, "total_tokens": 0,
                "cache_read_tokens": 0, "cache_write_tokens": 0,
            }
        # Recovery call.
        recorded_kwargs.update(kwargs)
        return {
            "content": json.dumps({
                "recovered_entries": [{"entry_id": "0_0", "fields": {"note": "recovered"}}],
                "recovery_notes": "",
            }),
            "cost": 0.01,
            "total_tokens": 5,
        }

    monkeypatch.setattr(extraction, "call_llm", fake_call_llm)

    def cancel_check():
        pass  # never raises

    extraction.extract_fields_batch(
        [_long_low_coverage_entry()], 0, 1, cancel_check=cancel_check,
    )

    assert calls["n"] == 2
    assert recorded_kwargs["cancel_check"] is cancel_check


# ---------------------------------------------------------------------------
# calculate_unextracted_content
# ---------------------------------------------------------------------------

def test_calculate_unextracted_content_full_coverage():
    result = extraction.calculate_unextracted_content(
        "Chief Resident at Example Hospital",
        {"role": "Chief Resident", "institution": "Example Hospital"},
    )

    assert result["extraction_coverage_percent"] == 100.0
    assert result["unextracted_words"] == []


def test_calculate_unextracted_content_partial_coverage():
    result = extraction.calculate_unextracted_content(
        "Attending Physician in Cardiology at Example Hospital",
        {"role": "Attending Physician"},
    )

    assert result["extraction_coverage_percent"] < 100.0
    assert "cardiology" in result["unextracted_words"]


# ---------------------------------------------------------------------------
# add_target_names fallback
# ---------------------------------------------------------------------------

def test_add_target_names_fallback_from_raw_text():
    entries = [{
        "taxonomy_code": "S1",
        "text": "Smith J, Doe A. Title of the paper describing findings.",
        "extracted_fields": {},
    }]

    out = extraction.add_target_names(entries, "Smith")

    assert out[0]["extracted_fields"]["target_name"] == "Smith J"
