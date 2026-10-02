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

import pytest
from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError

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


def _stub_owner_billing(monkeypatch, cost=0.25, prompt_tokens=30, completion_tokens=12):
    """An owner-name stub that bills `usage` the way the real call does."""
    def billed(document_uid, mapped_entries, docx_path=None, usage=None):
        usage.add({"cost": cost, "prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens})
        return dict(_NO_OWNER)

    monkeypatch.setattr(extraction, "extract_cv_owner_name", billed)
    monkeypatch.setattr(extraction, "infer_cv_owner_location", lambda entries: {})


def test_owner_name_call_cost_and_tokens_fold_into_stage_totals(monkeypatch):
    """#1177: the owner-name call's cost was discarded, so stage 4 reported low."""
    _stub_owner_billing(monkeypatch)
    monkeypatch.setattr(
        extraction, "extract_fields_batch",
        lambda entries, batch_idx, total, cv_owner_name, cancel_check=None:
            _batch_result(entries, cost=0.5, tokens=100),
    )

    out = extraction.extract_fields_from_mapped_entries(_entries(4), batch_size=2, workers=1)

    assert out["total_cost"] == pytest.approx(0.5 * 2 + 0.25)
    assert out["total_tokens"] == 100 * 2 + 42


def test_owner_name_cost_survives_the_no_valid_entries_early_out(monkeypatch):
    _stub_owner_billing(monkeypatch)
    entries = [{"text": "", "element_idx": 0, "taxonomy_code": "A1"}]

    out = extraction.extract_fields_from_mapped_entries(entries, batch_size=5)

    assert out["total_cost"] == pytest.approx(0.25)
    assert out["total_tokens"] == 42


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


@pytest.mark.parametrize("timeout_error", [
    ReadTimeoutError(endpoint_url="https://bedrock.example.invalid"),
    ConnectTimeoutError(endpoint_url="https://bedrock.example.invalid"),
], ids=["read_timeout", "connect_timeout"])
def test_attempt_llm_recovery_timeout_sets_llm_timeout_error(monkeypatch, timeout_error):
    # #953: a Bedrock call that times out after botocore's own internal
    # retries are exhausted raises botocore.exceptions.ReadTimeoutError or
    # ConnectTimeoutError directly (neither is a botocore.exceptions.ClientError,
    # so _call_with_retry never catches/retries it -- it propagates straight
    # here). The old `except openai.APITimeoutError` here was dead on the
    # live Bedrock path; this pins the exception Bedrock actually raises.
    def boom(**kwargs):
        raise timeout_error

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


@pytest.mark.parametrize("timeout_error", [
    ReadTimeoutError(endpoint_url="https://bedrock.example.invalid"),
    ConnectTimeoutError(endpoint_url="https://bedrock.example.invalid"),
], ids=["read_timeout", "connect_timeout"])
def test_extract_fields_batch_timeout_sets_extraction_error(monkeypatch, timeout_error):
    # #953: same botocore-timeout coverage as
    # test_attempt_llm_recovery_timeout_sets_llm_timeout_error, for the
    # extraction (not recovery) call site's own `except` branch.
    entries = [
        {"text": "hello world", "taxonomy_code": "A1", "element_idx_start": 0, "element_idx_end": 0},
    ]

    def boom(**kwargs):
        raise timeout_error

    monkeypatch.setattr(extraction, "call_llm", boom)

    result = extraction.extract_fields_batch(entries, 0, 1)

    assert result["success"] is False
    assert len(result["entries"]) == 1
    assert result["entries"][0]["extraction_error"] == extraction.LLM_TIMEOUT
    assert result["entries"][0]["extraction_success"] is False


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


def _d1_entry():
    return {"text": "Assistant Professor, Ohio State University, 2010-2015",
            "taxonomy_code": "D1", "element_idx_start": 0}


@pytest.mark.parametrize("call", ["recovery", "batch"])
def test_llm_outage_propagates_but_other_errors_degrade(monkeypatch, call):
    """A provider outage past the budget fails the run (#810); any other
    call_llm error still degrades to LLM_PROVIDER_ERROR on the entries."""
    from unified_pipeline.llm.retry import LLMOutageError

    def run():
        if call == "recovery":
            return extraction.attempt_llm_recovery([_d1_entry()])["entries"]
        return extraction.extract_fields_batch([_d1_entry()], 0, 1)["entries"]

    if call == "batch":
        # A degraded entry goes on to the recovery pass, whose own re-raise
        # would otherwise stand in for the batch site's.
        monkeypatch.setattr(extraction, "attempt_llm_recovery",
                            lambda entries, cancel_check=None: {"entries": entries, "cost": 0.0, "tokens": 0})

    def outage(**kw):
        raise LLMOutageError("provider down", seconds_waited=1800.0)

    monkeypatch.setattr(extraction, "call_llm", outage)
    with pytest.raises(LLMOutageError):
        run()

    def blip(**kw):
        raise RuntimeError("connection reset")

    monkeypatch.setattr(extraction, "call_llm", blip)
    entries = run()
    error_field = "llm_recovery_error" if call == "recovery" else "extraction_error"
    assert [e[error_field] for e in entries] == [extraction.LLM_PROVIDER_ERROR]


@pytest.mark.parametrize("code", ["M2A", "M2B", "M2C"])
def test_grant_prompt_names_status_and_notes_in_fields_guide_and_rules(code):
    """#982: the prompt the LLM actually receives for a grant bucket lists
    status/notes, describes them in the field guide, and says when to leave them null."""
    schema = extraction.get_field_schema(code)
    prompt = extraction.build_extraction_prompt(
        [{"text": "Grant X | Update: withdrawn"}], schema, code)
    assert "status" in prompt.split("**Fields to Extract**:")[1].splitlines()[0]
    assert "- status:" in prompt and "- notes:" in prompt
    assert "status = the grant's status only when the entry itself states one" in prompt


@pytest.mark.parametrize("rule", [
    "- status = the grant's status only when the entry itself states one",
    "- notes = a labelled remark no other field holds",
])
def test_grant_prompt_rules_block_carries_the_status_and_notes_rule_lines(rule):
    """#982: each rule line is asserted alone, so dropping only `notes =` from the
    M2 block fails here even though the field guide still names `- notes:`."""
    schema = extraction.get_field_schema("M2A")
    prompt = extraction.build_extraction_prompt([{"text": "Grant X"}], schema, "M2A")
    assert rule in prompt


# --- #985: sub-heading context reaches the stage 4 prompt ---------------------

#: sha256 of build_extraction_prompt for two unstamped entries on origin/dev
#: (measured before the change); pins the byte-identical-prompt contract.
#: M2A re-measured for #291, whose one added line (the clinical-trial field
#: mapping in the grant instructions) is the only difference from before.
_UNSTAMPED_PROMPT_SHA256 = {
    "M2A": "7d08216a2d2936f486270c1a275265d2dc0a207e0137b44aa98e1130cd661232",
    "K1": "ef35c4fa2a36b6a8fda487c77bf95d130dbda5f3c561c13cf074ad32546a58b7",
}


def _prompt(code, entries):
    return extraction.build_extraction_prompt(entries, extraction.get_field_schema(code), code)


@pytest.mark.parametrize("code", ["M2A", "K1"])
def test_an_unstamped_batch_prompt_is_byte_identical_to_origin_dev(code):
    import hashlib
    prompt = _prompt(code, [{"text": "Alpha course"}, {"text": "Beta course"}])
    assert hashlib.sha256(prompt.encode()).hexdigest() == _UNSTAMPED_PROMPT_SHA256[code]


def test_an_empty_context_heading_is_treated_as_unstamped():
    plain = _prompt("K1", [{"text": "Alpha course"}])
    assert _prompt("K1", [{"text": "Alpha course", "context_heading": ""}]) == plain


def test_a_stamped_entry_shows_under_and_the_instruction_appears_once_after_the_entries():
    prompt = _prompt("K1", [{"text": "Alpha course", "context_heading": "Course Director"},
                            {"text": "Beta course"}])
    assert "[Entry 0] (under: Course Director):\nAlpha course" in prompt
    assert "[Entry 1]:\nBeta course" in prompt            # unstamped sibling untouched
    assert prompt.count(extraction.CONTEXT_HEADING_INSTRUCTION) == 1
    assert prompt.index("Beta course") < prompt.index("10. **Sub-heading context**")
    assert prompt.index("10. **Sub-heading context**") < prompt.index("Return JSON")


def test_grant_instructions_map_clinical_trial_fields_onto_grant_fields():
    # #291: trials file as M2A/M2B, so the grant prompt must say where the
    # NCT number and sponsor go -- a field the grant table does not name is
    # dropped at render.
    prompt = _prompt("M2B", [{"text": "Site PI, invented Phase II trial, NCT00000000"}])
    assert "grant_number = its NCT or protocol number" in prompt
    assert "agency = its sponsor" in prompt
    for code in ("M2C", "M2D"):  # a pending application or a patent is never a trial
        assert "CLINICAL TRIAL" not in _prompt(code, [{"text": "Invented entry"}])


def test_the_instruction_follows_the_code_specific_rules_block():
    prompt = _prompt("M2A", [{"text": "Alpha", "context_heading": "Funded"}])
    assert prompt.index("- notes = a labelled remark") < prompt.index("10. **Sub-heading context**")


def test_removing_the_stamp_and_instruction_restores_the_unstamped_prompt_exactly():
    stamped = _prompt("M2A", [{"text": "Alpha", "context_heading": "Funded"}, {"text": "Beta"}])
    restored = stamped.replace(" (under: Funded)", "").replace(extraction.CONTEXT_HEADING_INSTRUCTION, "")
    assert restored == _prompt("M2A", [{"text": "Alpha"}, {"text": "Beta"}])


def test_the_instruction_says_fill_missing_never_override_and_do_not_misplace():
    text = extraction.CONTEXT_HEADING_INSTRUCTION
    assert "when the entry text itself omits them" in text
    assert "Never override what the entry text states" in text
    assert "Do not copy X into a field it does not describe" in text
    # The live A/B (#985): "Co-directed" rows became the heading's "Course
    # Director", and an activity-kind heading was copied as a role.
    assert "even as a verb or a qualifier, that role wins over X" in text
    assert '"Co-directed with ..." under "Course Director" is role "Co-Director"' in text
    assert "never copy X verbatim when it only names a kind of activity" in text


def test_extract_fields_from_mapped_entries_sends_a_stamped_entrys_heading(monkeypatch):
    """Wire: a `context_heading` already on the entry (process_cv stamps it, over
    the unfiltered list) survives batching and reaches the prompt. The
    orchestrator itself does not stamp: it only ever sees the filtered list."""
    _stub_owner(monkeypatch)
    sent = []

    def fake_llm(stage, messages, **kwargs):
        sent.append(messages[-1]["content"])
        return {"content": '{"entries": []}', "cost": 0.0, "total_tokens": 0}

    monkeypatch.setattr(extraction, "call_llm", fake_llm)
    entries = [
        {"text": "Survey course, lecturer", "taxonomy_code": "K1", "hierarchy": ["Teaching"], "element_idx_start": 1,
         "context_heading": "Northgate University"},
        {"text": "Other course", "taxonomy_code": "K2", "hierarchy": ["Elsewhere"], "element_idx_start": 2},
        {"text": "Northgate University:", "taxonomy_code": "T", "hierarchy": ["Teaching"], "element_idx_start": 0},
        {"text": "Third course", "taxonomy_code": "K3", "hierarchy": ["Teaching"], "element_idx_start": 3},
    ]
    before = json.dumps(entries)
    extraction.extract_fields_from_mapped_entries(entries, workers=1)
    assert json.dumps(entries) == before                     # caller's list untouched
    k1 = [p for p in sent if "**Classification**: K1" in p]
    assert k1 and "(under: Northgate University):\nSurvey course, lecturer" in k1[0]
    assert extraction.CONTEXT_HEADING_INSTRUCTION in k1[0]
    for code in ("K2", "K3"):
        other = [p for p in sent if f"**Classification**: {code}" in p]
        assert other and "(under:" not in other[0] and extraction.CONTEXT_HEADING_INSTRUCTION not in other[0]


# ---------------------------------------------------------------------------
# #759 -- the field schema an entry is extracted under is its OWN code's
# ---------------------------------------------------------------------------

def test_extract_fields_batch_prompts_each_code_with_its_own_schema(monkeypatch):
    # A section-I (memberships) entry and a K1 (didactic teaching) entry in
    # ONE batch. The I group's prompt must carry section I's fields and none of
    # K1's teaching-shaped ones, and the reply it gets back must land on the I
    # entry -- the 2082 farm artifact held I-coded entries whose keys were
    # course_code/institution/role (#759).
    entries = [
        {"text": "2019-present Fellow, Northgate Society of Widgetry",
         "taxonomy_code": "I", "element_idx_start": 0, "element_idx_end": 0},
        {"text": "2021 Intro to Widgetry (WGT 101), Hollis College",
         "taxonomy_code": "K1", "element_idx_start": 1, "element_idx_end": 1},
    ]
    prompts: list[str] = []

    def fake_call_llm(**kwargs):
        prompt = kwargs["messages"][-1]["content"]
        prompts.append(prompt)
        if "WGT 101" in prompt:
            fields = {"entry_index": 0, "course_title": "Intro to Widgetry", "institution": "Hollis College"}
        else:
            fields = {"entry_index": 0, "organization": "Northgate Society of Widgetry",
                      "membership_type": "Fellow", "start_date": "2019", "end_date": "present"}
        return {"content": json.dumps({"entries": [fields]}), "cost": 0.0, "total_tokens": 0,
                "cache_read_tokens": 0, "cache_write_tokens": 0}

    monkeypatch.setattr(extraction, "call_llm", fake_call_llm)

    result = extraction.extract_fields_batch(entries, 0, 1)

    assert len(prompts) == 2
    membership_prompt = next(p for p in prompts if "Northgate" in p)
    teaching_prompt = next(p for p in prompts if "WGT 101" in p)
    membership_fields = membership_prompt.split("**Fields to Extract**: ")[1].split("\n")[0].split(", ")
    teaching_fields = teaching_prompt.split("**Fields to Extract**: ")[1].split("\n")[0].split(", ")
    assert {"organization", "membership_type"} <= set(membership_fields)
    assert not {"institution", "role", "course_title"} & set(membership_fields)
    assert {"course_title", "institution", "role"} <= set(teaching_fields)
    assert "organization" not in teaching_fields
    by_code = {e["taxonomy_code"]: e for e in result["entries"]}
    assert by_code["I"]["extracted_fields"]["organization"] == "Northgate Society of Widgetry"
    assert "institution" not in by_code["I"]["extracted_fields"]
    assert by_code["K1"]["extracted_fields"]["course_title"] == "Intro to Widgetry"


def test_invalid_code_is_quarantined_before_extraction_and_counted_in_stats(monkeypatch):
    # #651: the 3b -> 4 boundary. The batch stub sees the re-coded entry, so
    # the invalid code never selects a schema; stats and the artifact name it.
    _stub_owner(monkeypatch)
    seen = []

    def spy(entries, batch_idx, total, cv_owner_name, cancel_check=None):
        seen.extend(e["taxonomy_code"] for e in entries)
        return _batch_result(entries)

    monkeypatch.setattr(extraction, "extract_fields_batch", spy)
    entries = [
        {"text": "valid entry text", "element_idx": 0, "taxonomy_code": "B1"},
        {"text": "bad entry text", "element_idx": 1, "taxonomy_code": "ZZ9"},
    ]

    out = extraction.extract_fields_from_mapped_entries(entries, batch_size=10, workers=1)

    assert sorted(seen) == ["B1", "T"]
    bad = next(e for e in out["entries"] if e["text"] == "bad entry text")
    assert bad["original_taxonomy_code"] == "ZZ9"
    assert bad["taxonomy_code_quarantine_reason"] == "invalid_taxonomy_code"
    assert out["stats"]["invalid_code_entries"] == 1
    assert out["stats"]["invalid_taxonomy_codes"] == {"'ZZ9'": 1}


def test_all_valid_codes_report_zero_invalid_in_stats(monkeypatch):
    _stub_owner(monkeypatch)
    monkeypatch.setattr(extraction, "extract_fields_batch",
                        lambda entries, *a, **k: _batch_result(entries))
    out = extraction.extract_fields_from_mapped_entries(
        [{"text": "valid entry text", "element_idx": 0, "taxonomy_code": "B1"}],
        batch_size=10, workers=1)
    assert out["stats"]["invalid_code_entries"] == 0
    assert out["stats"]["invalid_taxonomy_codes"] == {}


def test_invalid_code_entries_stat_sums_repeats_of_the_same_bad_code(monkeypatch):
    # invalid_code_entries counts ENTRIES (3), not distinct codes (1).
    _stub_owner(monkeypatch)
    monkeypatch.setattr(extraction, "extract_fields_batch",
                        lambda entries, *a, **k: _batch_result(entries))
    entries = [
        {"text": f"bad entry {i}", "element_idx": i, "taxonomy_code": "ZZ9"}
        for i in range(3)
    ]
    out = extraction.extract_fields_from_mapped_entries(entries, batch_size=10, workers=1)
    assert out["stats"]["invalid_code_entries"] == 3
    assert out["stats"]["invalid_taxonomy_codes"] == {"'ZZ9'": 3}


def test_postdoc_training_prompt_asks_for_a_role_and_keeps_the_other_fields():
    """#946: the C prompt names `role` in its field list and guide, and the five
    fields it already extracted stay in their old order ahead of it (the config
    marks department, mentor and narrative extract=false)."""
    prompt = _prompt("C", [{"text": "2017-2021 Zorblax Hospital Residency\tChief Resident"}])
    field_line = prompt.split("**Fields to Extract**:")[1].splitlines()[0]
    listed = [name.strip() for name in field_line.split(",")]
    assert listed == ["training_type", "specialty", "institution", "start_date", "end_date", "role"]
    assert "- role: " in prompt


# ---------------------------------------------------------------------------
# A reply that holds several items for ONE entry keeps every item. Each used to
# overwrite the one before it in both parsers, so only the last survived.
# Invented values.
# ---------------------------------------------------------------------------

from unified_pipeline.stage4.schemas import (  # noqa: E402
    STAGE4_RECORDS_KEY,
    STAGE4_RECORDS_RETURNED_KEY,
)

_MULTI_TEXT = "Glade Board chair, Fern Council member and Moss Panel member at Ashby University"
_SINGLE_TEXT = "Member, Heron Committee, Ashby University"


def _p_entries() -> list[dict]:
    return [
        {"text": _MULTI_TEXT, "taxonomy_code": "P", "element_idx_start": 4, "element_idx_end": 4},
        {"text": _SINGLE_TEXT, "taxonomy_code": "P", "element_idx_start": 5, "element_idx_end": 5},
    ]


_MULTI_ITEMS = [
    {"committee_name": "Glade Board", "role": "Chair", "institution": "Ashby University"},
    {"committee_name": "Fern Council", "role": "Member", "institution": "Ashby University"},
    {"committee_name": "Moss Panel", "role": "Member", "institution": "Ashby University"},
]
_SINGLE_ITEM = {"committee_name": "Heron Committee", "role": "Member", "institution": "Ashby University"}


def _reply(content: dict, cost: float = 0.01) -> dict:
    return {"content": json.dumps(content), "cost": cost, "total_tokens": 3}


def _batch_reply_with_three_items_for_entry_0(**kwargs) -> dict:
    return _reply({"entries": [
        *({"entry_index": 0, **item} for item in _MULTI_ITEMS),
        {"entry_index": 1, **_SINGLE_ITEM},
    ]})


def test_validate_raw_extractions_keeps_every_item_per_entry_in_reply_order():
    grouped = extraction._validate_raw_extractions([
        {"entry_index": 0, "role": "first"},
        {"entry_index": 1, "role": "other"},
        {"role": "no index"},  # malformed -- dropped
        {"entry_index": 0, "role": "second"},
    ], "P")
    assert grouped == {0: [{"role": "first"}, {"role": "second"}], 1: [{"role": "other"}]}
    with pytest.raises(KeyError):  # a plain dict: a missing index is not an empty list
        grouped[2]


def test_the_records_keys_are_a_persisted_artifact_contract():
    # Written into <uid>_fields.json and read back by stage 6 (and by any
    # later reader of a stored run), so the names are pinned, not just shared.
    assert (STAGE4_RECORDS_KEY, STAGE4_RECORDS_RETURNED_KEY) == (
        "stage4_records", "stage4_records_returned")


def test_batch_reply_with_several_items_for_one_entry_keeps_them_all(monkeypatch):
    monkeypatch.setattr(extraction, "call_llm", _batch_reply_with_three_items_for_entry_0)
    multi, _ = extraction.extract_fields_batch(_p_entries(), 0, 1)["entries"]

    fields = multi["extracted_fields"]
    assert [r["committee_name"] for r in fields[STAGE4_RECORDS_KEY]] == [
        "Glade Board", "Fern Council", "Moss Panel"]
    # The entry's own scalars stay the last item, which is all it kept before.
    assert {k: v for k, v in fields.items() if k != STAGE4_RECORDS_KEY} == _MULTI_ITEMS[-1]
    assert multi[STAGE4_RECORDS_RETURNED_KEY] == 3


def test_multi_record_coverage_is_measured_over_every_record(monkeypatch):
    monkeypatch.setattr(extraction, "call_llm", _batch_reply_with_three_items_for_entry_0)
    multi, _ = extraction.extract_fields_batch(_p_entries(), 0, 1)["entries"]
    last_only = extraction.calculate_unextracted_content(_MULTI_TEXT, _MULTI_ITEMS[-1])
    union = multi["extraction_coverage"]
    assert union["extraction_coverage_percent"] > last_only["extraction_coverage_percent"]
    assert "glade" in last_only["unextracted_words"]
    assert "glade" not in union["unextracted_words"]


def test_a_single_item_entry_is_exactly_what_it_was_before(monkeypatch):
    monkeypatch.setattr(extraction, "call_llm", _batch_reply_with_three_items_for_entry_0)
    _, single = extraction.extract_fields_batch(_p_entries(), 0, 1)["entries"]
    expected = {
        **_p_entries()[1],
        "extracted_fields": _SINGLE_ITEM,
        "extraction_success": True,
        "extraction_coverage": extraction.calculate_unextracted_content(_SINGLE_TEXT, _SINGLE_ITEM),
    }
    assert json.dumps(single) == json.dumps(expected)


def test_every_record_is_cleaned_not_only_the_last(monkeypatch):
    # normalize_dates splits a range on each item; an earlier one used to be
    # dropped before any cleaning ran, so this pins that it is cleaned too.
    def reply(**kwargs):
        return _reply({"entries": [
            {"entry_index": 0, "committee_name": "Glade Board", "dates": "1999-2001"},
            {"entry_index": 0, "committee_name": "Fern Council", "dates": "2002-2004"},
        ]})

    monkeypatch.setattr(extraction, "call_llm", reply)
    (entry,) = extraction.extract_fields_batch(_p_entries()[:1], 0, 1)["entries"]
    first, last = entry["extracted_fields"][STAGE4_RECORDS_KEY]
    assert (first["start_date"], first["end_date"]) == ("1999", "2001")
    assert (last["start_date"], last["end_date"]) == ("2002", "2004")


def test_only_the_last_record_is_offered_the_entry_text(monkeypatch):
    # The text's one closed range is filled into the last record, as it was
    # into the one record the entry kept before, and into no earlier record.
    entry = {"text": "Fern Council; Glade Board 2001-2005", "taxonomy_code": "P",
             "element_idx_start": 0, "element_idx_end": 0}

    def reply(**kwargs):
        return _reply({"entries": [{"entry_index": 0, "committee_name": "Fern Council",
                                    "role": ["Chair", "Member"]},
                                   {"entry_index": 0, "committee_name": "Glade Board"}]})

    monkeypatch.setattr(extraction, "call_llm", reply)
    (out,) = extraction.extract_fields_batch([entry], 0, 1)["entries"]
    first, last = out["extracted_fields"][STAGE4_RECORDS_KEY]
    assert first == {"committee_name": "Fern Council", "role": "Chair; Member"}  # coerced, not dated
    assert (last["start_date"], last["end_date"]) == ("2001", "2005")
    assert sorted(out["reformatted_fields"]) == ["end_date", "start_date"]


def test_records_that_cover_the_entry_skip_the_recovery_call(monkeypatch):
    # The last item alone covers a fifth of a long dated entry, which sends it
    # to recovery; the five items together cover all of it.
    records = [{"committee_name": f"{name} Committee", "description": f"{detail} oversight", "start_date": "2015"}
               for name, detail in [("Glade", "budgetary"), ("Fern", "curricular"), ("Moss", "editorial"),
                                    ("Heron", "procedural"), ("Wren", "technical")]]
    text = "; ".join(f"{r['committee_name']}, {r['description']}, 2015 to present" for r in records)
    text += " -- standing appointments"
    entry = {"text": text, "taxonomy_code": "P", "element_idx_start": 0, "element_idx_end": 0}
    assert len(text) >= 200
    last_alone = {"text": text, "extracted_fields": records[-1],
                  "extraction_coverage": extraction.calculate_unextracted_content(text, records[-1])}
    assert extraction.needs_llm_recovery(last_alone)
    calls = []

    def reply(**kwargs):
        calls.append(kwargs)
        return _reply({"entries": [{"entry_index": 0, **record} for record in records]})

    monkeypatch.setattr(extraction, "call_llm", reply)
    (out,) = extraction.extract_fields_batch([entry], 0, 1)["entries"]
    assert not extraction.needs_llm_recovery(out)
    assert len(calls) == 1
    assert out[STAGE4_RECORDS_RETURNED_KEY] == 5


def test_recovery_reply_with_several_items_for_one_id_keeps_them_all(monkeypatch):
    entry = {**_p_entries()[0], STAGE4_RECORDS_RETURNED_KEY: 9}

    def reply(**kwargs):
        return _reply({"recovered_entries": [
            {"entry_id": "4_4", "fields": item} for item in _MULTI_ITEMS[:2]]})

    monkeypatch.setattr(extraction, "call_llm", reply)
    (out,) = extraction.attempt_llm_recovery([entry])["entries"]
    assert [r["committee_name"] for r in out["extracted_fields"][STAGE4_RECORDS_KEY]] == [
        "Glade Board", "Fern Council"]
    assert out["extracted_fields"]["committee_name"] == "Fern Council"
    assert out[STAGE4_RECORDS_RETURNED_KEY] == 2
    assert out["llm_recovery_applied"] is True


def test_a_single_item_recovery_drops_the_count_an_earlier_pass_wrote(monkeypatch):
    entry = {**_p_entries()[0], STAGE4_RECORDS_RETURNED_KEY: 3,
             "extracted_fields": {**_MULTI_ITEMS[-1], STAGE4_RECORDS_KEY: _MULTI_ITEMS}}

    def reply(**kwargs):
        return _reply({"recovered_entries": [{"entry_id": "4_4", "fields": _SINGLE_ITEM}]})

    monkeypatch.setattr(extraction, "call_llm", reply)
    (out,) = extraction.attempt_llm_recovery([entry])["entries"]
    assert STAGE4_RECORDS_RETURNED_KEY not in out
    assert out["extracted_fields"] == _SINGLE_ITEM
    assert list(out)[-1] == "llm_recovery_applied"


def test_each_finished_batch_prints_a_progress_bar_line(monkeypatch, capsys, progress_patterns):
    """The web progress bar sat at its 50% placeholder for all of stage 4:
    no line matched orchestrator.PROGRESS_PATTERNS until the loop ended.
    Each finished batch now prints ``[done/total]``, read the way
    orchestrator.py reads it (first matching pattern wins)."""
    _stub_owner(monkeypatch)
    monkeypatch.setattr(extraction, "extract_fields_batch", lambda entries, *a, **k: _batch_result(entries))

    extraction.extract_fields_from_mapped_entries(_entries(3), batch_size=1, workers=2)

    seen = []
    for line in capsys.readouterr().out.splitlines():
        match = next((m for p in progress_patterns if (m := p.search(line))), None)
        if match:
            seen.append((int(match.group(1)), int(match.group(2))))
    assert seen == [(1, 3), (2, 3), (3, 3)]
