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

The #1655 tests at the end run the real extraction over a synthetic .docx
built in-test, with `call_llm` stubbed in stage 4 and stage 6, and render the
result through stage 6's Personal Data table.
"""

import json
import sys
import threading
import time
from pathlib import Path

import pytest
from botocore.exceptions import ConnectTimeoutError, ReadTimeoutError
from docx import Document

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import unified_pipeline.stage4.extraction as extraction  # noqa: E402
import unified_pipeline.stage4.owner_name as owner_name  # noqa: E402
import unified_pipeline.stage_6_word_template as stage_6_word_template  # noqa: E402
from unified_pipeline.core import prompt_logger  # noqa: E402
from unified_pipeline.core.batch_pool import workers_from_config  # noqa: E402
from unified_pipeline.tests.test_stage4_owner_side_channel import (  # noqa: E402
    LETTERHEAD,
    letterhead_docx,
)

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


def test_extract_fields_batch_stamps_the_entries_of_a_group_the_fallback_served(monkeypatch):
    """#1174: a served call is a success, so only this stamp records it. The
    stamp is on the entries of the group the fallback answered, not on other
    groups, and a normal result leaves no key."""
    from unified_pipeline.llm_provenance import (
        FALLBACK_SERVED_KEY,
        STAGE4_ENTRY_FALLBACK_KEY,
    )

    entries = [
        {"text": "hello world", "taxonomy_code": "A1", "element_idx_start": 0, "element_idx_end": 0},
        {"text": "other group", "taxonomy_code": "B1", "element_idx_start": 1, "element_idx_end": 1},
    ]

    def fake_call_llm(*, messages, **_kwargs):
        reply = {"content": json.dumps({"entries": [{"entry_index": 0, "note": "x"}]}),
                 "cost": 0.0, "total_tokens": 0}
        if "hello world" in messages[1]["content"]:
            reply[FALLBACK_SERVED_KEY] = "example.fallback-model-1"
        return reply

    monkeypatch.setattr(extraction, "call_llm", fake_call_llm)

    result = extraction.extract_fields_batch(entries, 0, 1)

    by_code = {e["taxonomy_code"]: e for e in result["entries"]}
    assert by_code["A1"][STAGE4_ENTRY_FALLBACK_KEY] == "example.fallback-model-1"
    assert STAGE4_ENTRY_FALLBACK_KEY not in by_code["B1"]


# --- #1243: reply items whose entry_index is past the group size --------------

def _d1_at(text, idx):
    return {"text": text, "taxonomy_code": "D1", "element_idx_start": idx, "element_idx_end": idx}


def _llm_returning(items):
    return lambda **_kwargs: {"content": json.dumps({"entries": items}), "cost": 0.0, "total_tokens": 0}


def test_extract_fields_batch_folds_out_of_range_items_into_a_one_entry_group(monkeypatch, caplog):
    """RCBKFG CAOACN: the model numbered the records of the group's one entry
    0..N-1, and only index 0 was kept. Every item is now a record of entry 0,
    in index order; the scalar fields are the last, as for any multi-record
    entry, and nothing is stamped because nothing was lost."""
    from unified_pipeline.stage4.schemas import (
        STAGE4_RECORDS_KEY,
        STAGE4_RECORDS_RETURNED_KEY,
        STAGE4_UNPLACED_ITEMS_KEY,
    )

    monkeypatch.setattr(extraction, "call_llm", _llm_returning([
        {"entry_index": 0, "title": "Rank A", "start_date": "2001"},
        {"entry_index": 2, "title": "Rank C", "start_date": "2003"},
        {"entry_index": 1, "title": "Rank B", "start_date": "2002"},
    ]))

    with caplog.at_level("WARNING"):
        [entry] = extraction.extract_fields_batch([_d1_at("Rank A 2001 Rank B 2002 Rank C 2003", 0)], 0, 1)["entries"]

    fields = entry["extracted_fields"]
    assert [r["title"] for r in fields[STAGE4_RECORDS_KEY]] == ["Rank A", "Rank B", "Rank C"]
    assert fields["title"] == "Rank C"
    assert entry[STAGE4_RECORDS_RETURNED_KEY] == 3
    assert STAGE4_UNPLACED_ITEMS_KEY not in entry
    assert "fit no entry" not in caplog.text


def test_extract_fields_batch_one_entry_group_with_no_index_0_still_extracts(monkeypatch):
    """A one-entry reply that numbers from 1 used to leave the entry with no
    match at all; its one item is that entry's."""
    monkeypatch.setattr(extraction, "call_llm", _llm_returning([{"entry_index": 1, "title": "Rank A"}]))

    [entry] = extraction.extract_fields_batch([_d1_at("Rank A", 0)], 0, 1)["entries"]

    assert entry["extraction_success"] is True
    assert entry["extracted_fields"]["title"] == "Rank A"


def test_extract_fields_batch_stamps_a_larger_group_with_items_no_entry_can_take(monkeypatch, caplog):
    """In a 2+-entry group the owner of an out-of-range item is unknowable: it
    is not guessed into an entry, but a warning names it and every entry of
    the group, matched or not, carries the count. Another group's entries are
    not stamped."""
    from unified_pipeline.stage4.schemas import STAGE4_UNPLACED_ITEMS_KEY

    def fake_call_llm(*, messages, **_kwargs):
        if "Other group" in messages[1]["content"]:
            return _llm_returning([{"entry_index": 0, "degree": "Other group"}])()
        return _llm_returning([
            {"entry_index": 0, "title": "Rank A"},
            {"entry_index": -1, "title": "Rank X"},
            {"entry_index": 5, "title": "Rank Y"},
        ])()

    monkeypatch.setattr(extraction, "call_llm", fake_call_llm)
    entries = [_d1_at("Rank A", 0), _d1_at("Rank B", 1),
               {"text": "Other group", "taxonomy_code": "B1", "element_idx_start": 2, "element_idx_end": 2}]

    with caplog.at_level("WARNING"):
        result = extraction.extract_fields_batch(entries, 0, 1)["entries"]

    by_text = {e["text"]: e for e in result}
    assert by_text["Rank A"]["extracted_fields"]["title"] == "Rank A"
    assert by_text["Rank B"]["extraction_error"] == extraction.NO_MATCHING_EXTRACTION
    assert [by_text[t].get(STAGE4_UNPLACED_ITEMS_KEY) for t in ("Rank A", "Rank B", "Other group")] == [2, 2, None]
    assert "2 reply item(s) at entry_index [-1, 5] fit no entry of the 2-entry group" in caplog.text


# --- #1575: a group reply gives one entry another entry's values ---------------

_GRANT_A = "Example Cancer Institute R01CA000001 (MPI, Effort: 20%) Total award: $326,020/year 04/21/17-03/31/23"
_GRANT_B = "Example Foundation award (PI, Effort: 30%) Total award: $30,000/year 07/01/1997-06/30/1999"
_GRANT_C = "Example Society pilot grant (PI) Total award: $150,000 04/01/98-12/31/02"


def _m2b_at(text, idx):
    return {"text": text, "taxonomy_code": "M2B", "element_idx_start": idx, "element_idx_end": idx}


def _grant_group():
    return [_m2b_at(_GRANT_A, 10), _m2b_at(_GRANT_C, 11), _m2b_at(_GRANT_B, 12)]


# UVZNIC 592's shape: entry 0 keeps its own grant number and title, but its
# dates, amount, effort and role are entry 2's.
_CONTAMINATED_REPLY = [
    {"entry_index": 0, "grant_number": "R01CA000001", "pi_role": "PI", "start_date": "1997-07-01",
     "end_date": "1999-06-30", "total_funding": "$30,000/year", "percent_effort": "30%"},
    {"entry_index": 1, "pi_role": "PI", "start_date": "1998-04-01", "end_date": "2002-12-31",
     "total_funding": "$150,000"},
    {"entry_index": 2, "pi_role": "PI", "start_date": "1997-07-01", "end_date": "1999-06-30",
     "total_funding": "$30,000/year", "percent_effort": "30%"},
]

_ALONE_REPLY = [
    {"entry_index": 0, "grant_number": "R01CA000001", "pi_role": "MPI", "start_date": "2017-04-21",
     "end_date": "2023-03-31", "total_funding": "$326,020/year", "percent_effort": "20%"},
]


def _llm_by_group_size(replies_by_size, calls, fail_alone=False):
    """A call_llm stub answering by how many entries the prompt holds, so the
    group call and an entry's call of its own get different replies."""
    def fake_call_llm(*, messages, **_kwargs):
        size = messages[1]["content"].count("\n[Entry ")
        calls.append(size)
        if fail_alone and size == 1:
            raise RuntimeError("provider error")
        return {"content": json.dumps({"entries": replies_by_size[size]}), "cost": 0.01 * size,
                "total_tokens": 100 * size}
    return fake_call_llm


def test_an_entry_given_another_entrys_values_is_extracted_again_alone(monkeypatch, caplog):
    """UVZNIC 592: an active R01 rendered another grant's 1997-99 dates,
    amount, effort and role. The borrowed values are not kept: the entry is
    extracted again in a call of its own and carries that call's fields, with
    the stamp naming what the group reply borrowed. The other entries keep
    the group's fields, and the batch's cost counts both calls."""
    from unified_pipeline.stage4.schemas import STAGE4_BORROWED_FIELDS_KEY

    calls = []
    monkeypatch.setattr(extraction, "call_llm",
                        _llm_by_group_size({3: _CONTAMINATED_REPLY, 1: _ALONE_REPLY}, calls))

    with caplog.at_level("WARNING"):
        result = extraction.extract_fields_batch(_grant_group(), 0, 1)

    by_idx = {e["element_idx_start"]: e for e in result["entries"]}
    fields = by_idx[10]["extracted_fields"]
    assert (fields["start_date"], fields["end_date"]) == ("2017-04-21", "2023-03-31")
    assert (fields["total_funding"], fields["percent_effort"], fields["pi_role"]) == ("$326,020/year", "20%", "MPI")
    assert by_idx[10][STAGE4_BORROWED_FIELDS_KEY] == ["end_date", "percent_effort", "start_date", "total_funding"]
    assert by_idx[12]["extracted_fields"]["start_date"] == "1997-07-01"
    assert STAGE4_BORROWED_FIELDS_KEY not in by_idx[11] and STAGE4_BORROWED_FIELDS_KEY not in by_idx[12]
    assert calls == [3, 1]
    assert result["cost"] == pytest.approx(0.04)
    assert result["tokens"] == 400
    assert "held another entry's end_date, percent_effort, start_date, total_funding" in caplog.text


def test_when_the_call_of_its_own_fails_the_borrowed_values_are_still_not_kept(monkeypatch):
    """The re-extraction failing does not bring the borrowed values back: the
    entry keeps the group's fields with them None (the regex pass may refill
    one from the entry's own text) and is stamped with the call's error. The
    group's own call succeeded, so the batch is not failed."""
    from unified_pipeline.stage4.schemas import STAGE4_REEXTRACT_ERROR_KEY

    monkeypatch.setattr(extraction, "call_llm",
                        _llm_by_group_size({3: _CONTAMINATED_REPLY}, [], fail_alone=True))

    result = extraction.extract_fields_batch(_grant_group(), 0, 1)

    entry = next(e for e in result["entries"] if e["element_idx_start"] == 10)
    values = {str(value) for value in entry["extracted_fields"].values()}
    assert not values & {"1997-07-01", "1999-06-30", "$30,000/year", "30%"}
    assert entry["extracted_fields"]["grant_number"] == "R01CA000001"
    assert entry[STAGE4_REEXTRACT_ERROR_KEY] == extraction.LLM_PROVIDER_ERROR
    assert result["success"] is True


def test_a_record_that_is_another_entrys_is_removed_from_the_entry(monkeypatch):
    """UVZNIC 559: the reply gave entry 1 a second record that was entry 2's
    mentee and gave entry 2 nothing. The record is removed from entry 1, which
    is stamped with the count; entry 2 is left to the recovery pass as any
    unmatched entry is. Removing a record calls nothing more."""
    from unified_pipeline.stage4.schemas import (
        STAGE4_BORROWED_FIELDS_KEY,
        STAGE4_FOREIGN_RECORDS_KEY,
        STAGE4_RECORDS_KEY,
    )

    calls = []
    entries = [{"text": t, "taxonomy_code": "N3B", "element_idx_start": i, "element_idx_end": i}
               for i, t in enumerate(["Mentee A, 2016-2018", "Mentee B, 2017-18", "Mentee C (2021-2022)"])]
    monkeypatch.setattr(extraction, "call_llm", _llm_by_group_size({3: [
        {"entry_index": 0, "mentee_name": "Mentee A", "start_date": "2016", "end_date": "2018"},
        {"entry_index": 1, "mentee_name": "Mentee B", "start_date": "2017", "end_date": "2018"},
        {"entry_index": 1, "mentee_name": "Mentee C", "start_date": "2021", "end_date": "2022"},
    ]}, calls))

    result = extraction.extract_fields_batch(entries, 0, 1)

    by_idx = {e["element_idx_start"]: e for e in result["entries"]}
    assert by_idx[1]["extracted_fields"]["mentee_name"] == "Mentee B"
    assert STAGE4_RECORDS_KEY not in by_idx[1]["extracted_fields"]
    assert by_idx[1][STAGE4_FOREIGN_RECORDS_KEY] == 1
    assert STAGE4_BORROWED_FIELDS_KEY not in by_idx[1]
    assert by_idx[2]["extraction_error"] == extraction.NO_MATCHING_EXTRACTION
    assert calls == [3]


def test_a_reply_whose_values_are_each_entrys_own_is_kept_as_is(monkeypatch):
    """The control: values each entry states itself, even ones another entry
    also states (both grants run to 1999 here), are kept, and no entry is
    stamped or called again."""
    from unified_pipeline.stage4.schemas import STAGE4_BORROWED_FIELDS_KEY

    calls = []
    entries = [_m2b_at("Grant one (PI, Effort: 10%) $50,000 1995-1999", 0),
               _m2b_at("Grant two (PI, Effort: 30%) $30,000 1997-1999", 1)]
    monkeypatch.setattr(extraction, "call_llm", _llm_by_group_size({2: [
        {"entry_index": 0, "start_date": "1995", "end_date": "1999", "total_funding": "$50,000",
         "percent_effort": "10%"},
        {"entry_index": 1, "start_date": "1997", "end_date": "1999", "total_funding": "$30,000",
         "percent_effort": "30%"},
    ]}, calls))

    result = extraction.extract_fields_batch(entries, 0, 1)

    assert calls == [2]
    assert [e["extracted_fields"]["total_funding"] for e in result["entries"]] == ["$50,000", "$30,000"]
    assert not any(STAGE4_BORROWED_FIELDS_KEY in e for e in result["entries"])


def test_a_year_from_the_entrys_own_context_heading_is_its_own(monkeypatch):
    """The prompt shows a stamped entry under its heading (#985), so a year
    the heading states is the entry's own, even when another entry states it
    too."""
    from unified_pipeline.stage4.schemas import STAGE4_BORROWED_FIELDS_KEY

    calls = []
    entries = [{**_d1_at("Visiting lecturer", 0), "context_heading": "Example University, 2019"},
               _d1_at("Lecturer, 2019-2020", 1)]
    monkeypatch.setattr(extraction, "call_llm", _llm_by_group_size({2: [
        {"entry_index": 0, "title": "Visiting lecturer", "start_date": "2019"},
        {"entry_index": 1, "title": "Lecturer", "start_date": "2019", "end_date": "2020"},
    ]}, calls))

    result = extraction.extract_fields_batch(entries, 0, 1)

    assert calls == [2]
    assert result["entries"][0]["extracted_fields"]["start_date"] == "2019"
    assert STAGE4_BORROWED_FIELDS_KEY not in result["entries"][0]


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


@pytest.mark.parametrize("rule", [
    "- A SESSION, PANEL, SYMPOSIUM or WORKSHOP entry (e.g., one you moderated or chaired) "
    "names a title, often in quotes: committee_name = that title (without the quotes)",
    "- Example: \"Moderator, Society for Example Medicine Annual Meeting, 'Advances in Example Care'\"",
    '     * committee_name = "Advances in Example Care"',
    '     * role = "Moderator"',
    '     * organization = "Society for Example Medicine Annual Meeting"',
    "- Never leave committee_name null when the entry names such a title, "
    'and never put the title in a key not listed above (e.g., "topic")',
])
def test_q2_prompt_puts_a_session_panel_or_workshop_title_in_committee_name(rule):
    """#1346: the EBYSBC batch lost 14 Q2 session titles in 6 CVs: stage 4 left
    committee_name null on moderated or chaired sessions, or put the title under
    an off-schema `topic` key. Each rule line is asserted alone so dropping any
    one of them fails here."""
    prompt = _prompt("Q2", [{"text": "Moderator, Invented Meeting, 'Invented Session'"}])
    assert rule in prompt


# --- #985: sub-heading context reaches the stage 4 prompt ---------------------

#: sha256 of build_extraction_prompt for two unstamped entries on origin/dev
#: (measured before the change); pins the byte-identical-prompt contract.
#: M2A re-measured for #291, whose one added line (the clinical-trial field
#: mapping in the grant instructions) is the only difference from before.
#: Both re-measured for #1243, whose rules 1 and 6 (MULTI_RECORD_INSTRUCTION,
#: TAB_COLUMNS_INSTRUCTION) are the only lines that differ from before. M2A
#: re-measured again for the one-work guard appended to its rule 1
#: (SINGLE_WORK_INSTRUCTION): dropping it gives 1b33f2df... back.
#: M2A re-measured for #1403 on top of #1243, whose added unlabelled-title
#: line is the only difference: dropping it gives the #1243 hash 0ab6854a...
#: back. The pi_role line names the owner, so this owner-less prompt does not
#: carry it. M2A re-measured for the EOAHMI recheck (#1403 items b/c), whose
#: two added pi_name lines are the only difference: dropping them gives
#: 108969062c... back.
_UNSTAMPED_PROMPT_SHA256 = {
    "M2A": "d69c439ddab70048345703c9eb14caefa71eae7de2378d68b88192fc2c4eb8a3",
    "K1": "984c639c769e8093d4fbdf99a04ba3a4895b14fee3756c23b923fb917a8bb416",
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


# --- #1403 (EBYSBC E32): stage-4 field rules --------------------------------

_OWNER = {"last_name": "Owner", "full_name": "Ann B. Owner"}


@pytest.mark.parametrize("code", ["M2A", "M2B", "M2C"])
def test_grant_prompt_carries_the_unlabelled_title_rule(code):
    # NDXXAD 411: an unlabelled program name was dropped, title null.
    rule = ("- When there is no \"Title:\" label, an unlabelled name of the project or program "
            "that comes before the labelled parts")
    assert rule in _prompt(code, [{"text": "Invented grant; Doe (PI)."}])


@pytest.mark.parametrize("code", ["M2A", "M2B", "M2C"])
def test_grant_prompt_names_the_owner_in_the_pi_role_rule(code):
    # Wave-4 A/B: told only that another person's "(PI)" is not the owner's
    # role, a model that cannot tell who the owner is cleared pi_role on
    # RVROVQ 9/9 "P.I.: <owner>", XELRLZ "<owner> (PI)" and CXRYCF "PI: <owner>".
    prompt = extraction.build_extraction_prompt(
        [{"text": "Invented grant; Doe (PI)."}], extraction.get_field_schema(code), code, _OWNER)
    rule = prompt.split("- pi_role = ", 1)[1].split("\n", 1)[0]
    assert rule.startswith('the role on this grant of the CV owner, "Ann B. Owner" (surname "Owner"). ')
    for label in ('"P.I.: <name>"', '"PI: <name>"', '"PI <name>"', '"<name> (PI)"', '"Principal Investigator: <name>"'):
        assert label in rule
    # The owner's own label sets the role: the case the A/B regressed.
    assert 'When that label names the CV owner (in full, by surname, or with initials), pi_role = "PI"' in rule
    # Someone else's label does not (XELRLZ 170, the #1403 target).
    assert "When a PI label names someone else, that person is pi_name, and pi_role is only a role the entry states for the CV owner" in rule
    assert "if it states none, leave pi_role null. " in rule
    # EOAHMI DUTAVD 186: a plural "Investigators - <other>, <owner>" label gave pi_role null.
    assert ('A plural role label that lists the CV owner among others (e.g., "Investigators - <name>, <name>") '
            'is the owner\'s role, in the singular (pi_role = "Investigator").') in rule
    # NDXXAD 638: "Secured the ... grant" gained an invented "PI".
    assert rule.endswith('A verb such as "secured", "led" or "established" is not a stated role: '
                         'with no role stated for the CV owner, leave pi_role null')
    assert prompt.index("- If no PI name is found") < prompt.index("- pi_role = ") < prompt.index("- status = ")


@pytest.mark.parametrize("code", ["M2A", "M2B", "M2C"])
def test_grant_prompt_keeps_an_unlabelled_collaborator_out_of_pi_name(code):
    # EOAHMI JIJRSN 150/152/154/156: "<title>, with Dr. <collaborator>" made the
    # collaborator pi_name; 562: the investigator who "initiated" a trial the
    # owner was "Principal Investigator in" became pi_name.
    rule = ('- A person the entry names without a PI label, such as a "with Dr. <name>" collaborator or the '
            'investigator who "initiated" a study, is not pi_name; put a collaborator in co_investigators')
    for owner in (None, _OWNER):  # not tied to the owner: it is a pi_name rule
        prompt = extraction.build_extraction_prompt(
            [{"text": "Invented grant, with Dr. A. Example."}], extraction.get_field_schema(code), code, owner)
        assert rule in prompt
        assert prompt.index("- Do NOT put the project title") < prompt.index(rule) < prompt.index("- If no PI name is found")


@pytest.mark.parametrize("code", ["M2A", "M2B", "M2C"])
def test_grant_prompt_takes_the_first_listed_author_as_pi_name(code):
    # EOAHMI QTATUP 529..562: an author-list grant batch left pi_name null on
    # all 9 and listed the first author (the owner on 529/533/537) only as a
    # co-investigator.
    prompt = _prompt(code, [{"text": "Doe AB, Roe CD. Invented title. $1,000 (Invented sponsor)"}])
    rule = ('- An entry that opens with an author list and has no PI label (e.g., "<name> AB, <name> CD. <title>. '
            '$<amount> (<sponsor>)"): pi_name = the first-listed author, co_investigators = the other authors')
    assert rule in prompt
    assert prompt.index("- A person the entry names without a PI label") < prompt.index(rule) < prompt.index("- If no PI name is found")


def test_grant_pi_role_rule_is_left_out_when_the_owner_is_unknown():
    # Nothing to compare a PI label against: keep the pre-#1403 prompt.
    for owner in (None, {}, {"last_name": ""}):
        assert extraction.grant_owner_role_rule(owner) == ""
        assert "- pi_role = " not in extraction.build_extraction_prompt(
            [{"text": "Invented grant"}], extraction.get_field_schema("M2B"), "M2B", owner)


def test_grant_pi_role_rule_names_a_surname_only_owner_once():
    rule = extraction.grant_owner_role_rule({"last_name": "Owner"})
    assert 'of the CV owner, "Owner". ' in rule
    assert "(surname" not in rule
    assert '(e.g., "Owner (Co-I)", "Mentor")' in rule


def test_extract_fields_batch_sends_the_owner_named_pi_role_rule_to_the_llm(monkeypatch):
    """Wire: the cv_owner_name extract_fields_batch is given reaches the grant prompt."""
    prompts: list[str] = []

    def fake_call_llm(**kwargs):
        prompts.append(kwargs["messages"][-1]["content"])
        return _reply({"entries": [{"entry_index": 0, "pi_role": "PI"}]})

    monkeypatch.setattr(extraction, "call_llm", fake_call_llm)
    extraction.extract_fields_batch(
        [{"text": "Invented Agency\tInvented title\tP.I.: Ann B. Owner", "taxonomy_code": "M2B",
          "element_idx_start": 0, "element_idx_end": 0}], 0, 1, cv_owner_name=_OWNER)
    assert prompts and extraction.grant_owner_role_rule(_OWNER) in prompts[0]


def test_past_mentee_prompt_puts_the_mentoring_period_school_in_site_position():
    # EQADVR 509/516/517: the college of the mentoring year went to current_position.
    prompt = _prompt("N3B", [{"text": "a) Invented Mentee, Senior, Example College, 2000"}])
    assert ("site_position = where and in what the mentee was mentored DURING the mentoring: the school or "
            "institution, and the program, project, fellowship or committee the entry names for that period") in prompt
    # Wave-4 A/B: MYNQRA's "MS, Thesis committee" rows lost their Site/Position
    # text when the rule named only a school or institution.
    assert '"MS, Thesis committee" → site_position = "Thesis committee"' in prompt
    assert "current_position = where the mentee is NOW, only when the entry says so" in prompt
    # EOAHMI LOOTTE 237..342: under the site_position rule the degree line
    # ("B.S., <institution>, 2003") fell out of every field.
    degree = ('- mentee_level = the mentee\'s degree or level. When the entry gives a degree line (e.g., "B.S., '
              'Example University, 2003", "PhD, Example University"), mentee_level = that whole line and the level '
              'or role of the mentoring period (e.g., "Predoc") goes in site_position; never drop the degree line')
    assert degree in prompt
    assert prompt.index("- site_position = ") < prompt.index(degree) < prompt.index("- current_position = ")
    assert "Do NOT put the institution of the mentoring period in current_position" in prompt
    assert prompt.index("PAST MENTEES (N3B)") < prompt.index("Return JSON")


def test_abstract_prompt_puts_the_owner_first_on_a_co_presented_talk():
    # XWNZWW 752..1210: "co-presented with X" listed only X as authors.
    prompt = extraction.build_extraction_prompt(
        [{"text": "\"Invented Talk,\" co-presented with A. Example, at an invented meeting."}],
        extraction.get_field_schema("S8"), "S8", {"last_name": "Owner"})
    rule = "- Co-presented: when the entry says it was \"co-presented with\" other people"
    assert rule in prompt
    assert "authors = the CV owner's name first, then those co-presenters" in prompt
    # A sub-point of instruction 9 (target_name), which names the owner.
    assert prompt.index("9. **target_name**") < prompt.index(rule) < prompt.index("Return JSON")
    for code in ("S1", "R"):  # R's authors are extract:false; S1 has no co-presenter
        assert "Co-presented:" not in _prompt(code, [{"text": "Invented entry"}])


def test_other_positions_prompt_takes_a_consulting_topic_as_the_title():
    # OIYKZE 87/91/95: consulting lines kept organization and year only.
    prompt = _prompt("D3", [{"text": "2020 Example University\tInvented Topic\tA. Contact, Director"}])
    assert "OTHER POSITIONS (D3)" in prompt
    assert "title = the project topic, organization = the client organization" in prompt
    assert "Do not leave title null when the entry names the work done" in prompt
    assert "Never put the client contact's name or job title in title" in prompt


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


# --- #1243: a multi-record entry is asked for one item per record -------------

@pytest.mark.parametrize("code", ["K1", "M2A", "N3A", "Q2", "I", "D2", "S1"])
def test_every_batch_prompt_opens_its_rules_with_the_multi_record_rule(code):
    prompt = _prompt(code, [{"text": "Alpha\tBeta"}])
    rules = prompt.split("**Instructions**:\n")[1]
    assert rules.startswith(extraction.multi_record_rule(code) + "\n2. Use null")
    assert rules.startswith(extraction.MULTI_RECORD_INSTRUCTION)
    assert "\n" + extraction.TAB_COLUMNS_INSTRUCTION + "\n7. Only extract" in rules


def test_the_multi_record_rule_asks_for_one_item_per_record_under_one_entry_index():
    # EBYSBC (#1243): 17 of 40 CVs lost a second mentee, role, rank or talk
    # because the reply held one item for an entry that holds several.
    text = extraction.MULTI_RECORD_INSTRUCTION
    assert text.startswith("1. For each entry, extract all available fields. ")
    assert 'Return one item per record, each with the same "entry_index"' in text
    assert "repeat in every item a value the records share" in text
    assert "Never join two records' values into one field" in text
    assert "never keep only the first, the last or the parent record" in text
    # The bound on over-splitting: one citation or grant is still one item.
    assert "An entry about one thing is one item, even when it lists several authors" in text


@pytest.mark.parametrize("code", ["S1", "S4", "S8", "M2A", "M2B", "M2C", "M2D", "T"])
def test_a_citation_patent_or_grant_prompt_keeps_one_work_as_one_item(code):
    # Wave-4 A/B: NDXXAD 360, one S8 abstract given as a poster at one meeting
    # and a talk at another, became two items with the same title and authors.
    rules = _prompt(code, [{"text": "Alpha. Poster at Meeting A 2016, and oral presentation at Meeting B 2016."}]
                    ).split("**Instructions**:\n")[1]
    assert rules.startswith(extraction.MULTI_RECORD_INSTRUCTION + extraction.SINGLE_WORK_INSTRUCTION + "\n2. Use null")


@pytest.mark.parametrize("code", ["K1", "M1", "N3A", "N3B", "Q2", "Q4D", "I", "D2", "P", "R", "C"])
def test_a_list_of_records_prompt_does_not_carry_the_one_work_guard(code):
    # The 12/13 cited multi-record wins (MYNQRA 111/112 N3A, RVROVQ 129 R,
    # MRJDWE 103 Q2, ...) are on these codes; the guard must not reach them.
    assert extraction.SINGLE_WORK_INSTRUCTION not in _prompt(code, [{"text": "Alpha\tBeta"}])


def test_the_one_work_guard_says_one_item_for_one_work_and_splits_only_separate_titles():
    text = extraction.SINGLE_WORK_INSTRUCTION
    assert text.startswith(" Here one citation, abstract, poster, chapter, patent or grant is one work: ")
    assert "when the entry gives the same work at several venues, meetings, presentations or dates" in text
    assert "return ONE item and put every venue or date in that item's fields" in text
    assert "Never return two items with the same title." in text
    # MYNQRA 63 (two patents) and EQGGRB 144 (two chapters) still split.
    assert text.endswith("Split only works that each have their own title.")


def test_extract_fields_batch_sends_the_one_work_guard_to_the_llm(monkeypatch):
    """Wire: an abstract batch's prompt as call_llm receives it."""
    prompts: list[str] = []

    def fake_call_llm(**kwargs):
        prompts.append(kwargs["messages"][-1]["content"])
        return _reply({"entries": [{"entry_index": 0, "title": "Alpha"}]})

    monkeypatch.setattr(extraction, "call_llm", fake_call_llm)
    extraction.extract_fields_batch(
        [{"text": "Alpha. Poster at Meeting A 2016, and oral presentation at Meeting B 2016.",
          "taxonomy_code": "S8", "element_idx_start": 0, "element_idx_end": 0}], 0, 1)
    assert prompts and extraction.MULTI_RECORD_INSTRUCTION + extraction.SINGLE_WORK_INSTRUCTION in prompts[0]


# --- #1445: the stage-4 over-splits #1406 left (EOAHMI recheck) ---------------

def _rule1(code):
    rules = _prompt(code, [{"text": "Lecturer: Alpha Series 2015, 2016\tTopic A\tTopic B"}]).split("**Instructions**:\n")[1]
    return rules.split("\n2. Use null")[0]


@pytest.mark.parametrize("code, guard", [("K4", "ONE_SERIES_INSTRUCTION"), ("R", "ONE_SERIES_INSTRUCTION"),
                                         ("K3", "CONTEXT_LINE_INSTRUCTION")])
def test_a_lecture_talk_or_program_prompt_appends_its_guard_to_rule_1(code, guard):
    # JBUVYV 346 (K4) and QTATUP 980 (R) over-split one series; JBUVYV 337 (K3)
    # made a context line its own item.
    assert _rule1(code) == extraction.MULTI_RECORD_INSTRUCTION + getattr(extraction, guard)


@pytest.mark.parametrize("code", ["K1", "K2", "K5", "O", "P", "Q1", "Q2", "N3A", "D2", "I", "S1", "S8", "M2A", "T"])
def test_other_prompts_carry_neither_the_series_nor_the_context_line_guard(code):
    # The cited #1406 wins (MYNQRA 111/112 N3A, RVROVQ 171 Q2, AQCLHS 63 P, ...)
    # sit on these codes; their rule 1 stays as it was.
    rule = _rule1(code)
    assert extraction.ONE_SERIES_INSTRUCTION not in rule
    assert extraction.CONTEXT_LINE_INSTRUCTION not in rule
    assert rule == extraction.MULTI_RECORD_INSTRUCTION + (
        extraction.SINGLE_WORK_INSTRUCTION if code.startswith(extraction.SINGLE_WORK_CODE_PREFIXES) else "")


def test_the_series_guard_keeps_one_series_as_one_item_and_the_audience_in_every_item():
    text = extraction.ONE_SERIES_INSTRUCTION
    # JBUVYV 346: 4 topics x 3 years became 12 items; the fix is 4, one per topic.
    assert text.startswith(" Never return an item for each combination of two lists (e.g. each topic in each year): ")
    assert ('when an entry gives years for a series and then lists its topics, return one item per topic, each with '
            'every year in its date (e.g. "2015; 2016; 2017"). ') in text
    # The first wording merged QTATUP 800 (5 cities) and 838 (4 titles) into 1.
    assert 'Each distinct title, and each place with its own date, is still its own item. ' in text
    assert "ONE item" not in text
    # QTATUP 980: the seven dated items all lost "to health care practitioners".
    assert text.endswith('When one talk is given on several dates, keep in every item the words that say how '
                         'and to whom it was given (e.g. "a teleconference series to nurses").')


def test_the_context_line_guard_keeps_a_line_without_role_or_date_in_its_program():
    # JBUVYV 337: a line naming only the program's council became a role-less item.
    assert extraction.CONTEXT_LINE_INSTRUCTION == (
        " A line that only names a body the program belongs to (e.g. a council), with no role or date of its own, "
        "is context for that program's item, not a new item.")


def test_extract_fields_batch_sends_the_series_guard_to_the_llm(monkeypatch):
    """Wire: a K4 batch's prompt as call_llm receives it."""
    prompts: list[str] = []

    def fake_call_llm(**kwargs):
        prompts.append(kwargs["messages"][-1]["content"])
        return _reply({"entries": [{"entry_index": 0, "activity_title": "Alpha Series"}]})

    monkeypatch.setattr(extraction, "call_llm", fake_call_llm)
    extraction.extract_fields_batch(
        [{"text": "Lecturer: Alpha Series 2015, 2016\tTopic A\tTopic B",
          "taxonomy_code": "K4", "element_idx_start": 0, "element_idx_end": 0}], 0, 1)
    assert prompts and extraction.MULTI_RECORD_INSTRUCTION + extraction.ONE_SERIES_INSTRUCTION in prompts[0]


def test_the_tab_rule_keeps_columns_but_starts_a_new_item_for_a_new_record():
    # TAUBPU-shaped D2 row: a hospital post, a tab, then a concurrent faculty
    # rank. The old rule read every tab as a column of one record.
    text = extraction.TAB_COLUMNS_INSTRUCTION
    assert text.startswith("6. Tab-separated values: If text contains tabs (\\t) or pipe characters (|), "
                           "these indicate table columns - extract each column as a separate field value, "
                           "not as merged text. ")
    assert text.endswith("A column that starts another record (e.g. a second role or person "
                         "with its own date) is a new item under rule 1.")


def test_extract_fields_batch_sends_the_multi_record_rules_to_the_llm(monkeypatch):
    """Wire: the prompt call_llm receives, not only the builder's output."""
    prompts: list[str] = []

    def fake_call_llm(**kwargs):
        prompts.append(kwargs["messages"][-1]["content"])
        return _reply({"entries": [{"entry_index": 0, "role": "Member"}]})

    monkeypatch.setattr(extraction, "call_llm", fake_call_llm)
    extraction.extract_fields_batch(
        [{"text": "Member, Alpha Committee 2019\tChair, Beta Committee 2021",
          "taxonomy_code": "P", "element_idx_start": 0, "element_idx_end": 0}], 0, 1)
    assert prompts and all(extraction.MULTI_RECORD_INSTRUCTION in p and extraction.TAB_COLUMNS_INSTRUCTION in p
                           for p in prompts)


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


# --- a second record under an off-schema key (#1245) -------------------------

def _one_item_reply(code_text: str, code: str, item: dict):
    entry = {"text": code_text, "taxonomy_code": code, "element_idx_start": 0, "element_idx_end": 0}

    def reply(**kwargs):
        return _reply({"entries": [{"entry_index": 0, **item}]})
    return entry, reply


def test_a_numbered_schema_field_is_a_second_record_that_fans_out(monkeypatch):
    # A two-column memberships row: the model kept the second column under
    # `organization_2`, which no renderer reads.
    from unified_pipeline.stage4.schemas import FIELD_SCHEMAS
    from unified_pipeline.stage6.fan_out import fan_out_multi_record_entries
    entry, reply = _one_item_reply("Glade Society 1999-2001 Fern Guild", "I", {
        "organization": "Glade Society", "start_date": "1999", "end_date": "2001",
        "organization_2": "Fern Guild"})
    monkeypatch.setattr(extraction, "call_llm", reply)
    (out,) = extraction.extract_fields_batch([entry], 0, 1)["entries"]

    first, second = out["extracted_fields"][STAGE4_RECORDS_KEY]
    assert first == {"organization": "Glade Society", "start_date": "1999", "end_date": "2001"}
    # The parent's dates belong to the first column only.
    assert second == {"organization": "Fern Guild"}
    assert STAGE4_RECORDS_RETURNED_KEY not in out  # the model returned one item
    children = fan_out_multi_record_entries([out], FIELD_SCHEMAS, records_key=STAGE4_RECORDS_KEY)
    assert [c["extracted_fields"]["organization"] for c in children] == ["Glade Society", "Fern Guild"]


def test_an_object_sharing_schema_keys_is_the_parent_at_another_venue(monkeypatch):
    entry, reply = _one_item_reply("Heron talk, Ashby Dinner, 2016; Wren Seminar, 2017", "R", {
        "title": "Heron talk", "location": "Ashby", "date": "2016",
        "additional_presentation": {"location": "Wren Seminar", "date": "2017"}})
    monkeypatch.setattr(extraction, "call_llm", reply)
    (out,) = extraction.extract_fields_batch([entry], 0, 1)["entries"]

    first, second = out["extracted_fields"][STAGE4_RECORDS_KEY]
    assert (first["title"], first["location"]) == ("Heron talk", "Ashby")
    assert "additional_presentation" not in first
    assert (second["title"], second["location"]) == ("Heron talk", "Wren Seminar")
    assert "2017" in (second.get("date"), second.get("start_date"))


def test_other_offschema_values_are_left_as_they_were(monkeypatch):
    # A one-fact key, an object sharing no schema key and a record list are
    # not a second record here: a list is fan-out's to split (#1187).
    item = {"organization": "Glade Society", "honors": "Fellow", "contact": {"phone": "x"},
            "roles": [{"organization": "Fern Guild"}]}
    entry, reply = _one_item_reply("Glade Society, Fellow", "I", item)
    monkeypatch.setattr(extraction, "call_llm", reply)
    (out,) = extraction.extract_fields_batch([entry], 0, 1)["entries"]
    assert STAGE4_RECORDS_KEY not in out["extracted_fields"]
    assert {k: out["extracted_fields"][k] for k in item} == item


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


def test_year_group_lines_are_re_dated_after_the_batches_are_reassembled(monkeypatch):
    # Class E26: the pass needs document order across batches, so the two
    # entries go into different batches and the later batch finishes first.
    _stub_owner(monkeypatch)
    fields = {"2016:  03-16: Example lecture A": {"date": "2016-03-16"},
              "09-14: Example lecture B": {"start_date": "2014-09"}}
    entries = [{"text": text, "element_idx_start": idx, "element_idx_end": idx,
                "hierarchy": ["Teaching"], "taxonomy_code": "K1"}
               for idx, text in enumerate(fields)]

    def llm_batch(batch, batch_idx, total, cv_owner_name, cancel_check=None):
        time.sleep((total - batch_idx) * 0.01)
        return _batch_result([dict(e, extracted_fields=dict(fields[e["text"]])) for e in batch])

    monkeypatch.setattr(extraction, "extract_fields_batch", llm_batch)

    out = extraction.extract_fields_from_mapped_entries(entries, batch_size=1, workers=2)

    assert [e["extracted_fields"] for e in out["entries"]] == [
        {"date": "2016-03-16"}, {"start_date": "2016-09-14"}]
    assert out["stats"]["entries_reformatted"] == 1


# ---------------------------------------------------------------------------
# #1655: the owner's contact when it lives only in the page header.
# ---------------------------------------------------------------------------

_HOME_PHONE = "(212) 555-0199"
_HOME_LINE = f"Home phone: {_HOME_PHONE}"
_HEADER_CONTACT_FIELDS = {
    "name": "Quinn Synthetic, MD, PhD",
    "email": "quinn.synthetic@example.org",
    "phone": "(212) 555-0100",
    "address": "100 Example Avenue, Room 5, Testville, NY 10000",
}
_BODY_ENTRY = {"text": "MD, Example University, 2001", "taxonomy_code": "B1",
               "element_idx_start": 0, "element_idx_end": 0, "hierarchy": ["Education"]}
_W_T = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t"


def _fake_stage4_llm(calls):
    """call_llm for stage 4's field extraction: the letterhead entry gets the
    synthetic contact fields (and the home phone, when its line was sent), a
    body entry its education fields."""
    def fake_call_llm(**kwargs):
        prompt = kwargs["messages"][-1]["content"]
        calls.append(prompt)
        if "quinn.synthetic@example.org" in prompt:
            item = {"entry_index": 0, **_HEADER_CONTACT_FIELDS}
            if _HOME_LINE in prompt:
                item["home_phone"] = _HOME_PHONE
        else:
            item = {"entry_index": 0, "degree": "MD", "institution": "Example University", "year": "2001"}
        return {"content": json.dumps({"entries": [item]}), "cost": 0.25, "total_tokens": 100,
                "cache_read_tokens": 3, "cache_write_tokens": 4}
    return fake_call_llm


def _fake_owner_llm(**kwargs):
    found = "Quinn Synthetic" in kwargs["messages"][-1]["content"]
    reply = {key: "" for key in _NO_OWNER}
    if found:
        reply.update(first_name="Quinn", last_name="Synthetic", full_name="Quinn Synthetic",
                     full_name_with_credentials="Quinn Synthetic, MD, PhD")
    return {"content": json.dumps(reply), "cost": 0.0, "total_tokens": 0}


def _letterhead_stage4(monkeypatch, tmp_path, header=LETTERHEAD, body=(_BODY_ENTRY,)):
    calls: list[str] = []
    monkeypatch.setattr(extraction, "call_llm", _fake_stage4_llm(calls))
    monkeypatch.setattr(owner_name, "call_llm", _fake_owner_llm)
    monkeypatch.setattr(extraction, "infer_cv_owner_location", lambda entries: {})
    out = extraction.extract_fields_from_mapped_entries(
        [dict(e) for e in body], document_uid="web991", docx_path=letterhead_docx(tmp_path, header=header),
        workers=1)
    return out, calls


def _render_personal_data(monkeypatch, tmp_path, stage4_out) -> tuple[dict[str, str], str]:
    """Stage 6 over a stage-4 result, no LLM: the Personal Data table's
    rows (label -> value) and every w:t of the rendered body."""
    def no_llm(**kwargs):
        raise RuntimeError("stage 6 must not reach an LLM in this test")
    monkeypatch.setattr(stage_6_word_template, "call_llm", no_llm)
    stage4_json, rendered = tmp_path / "web991_fields.json", tmp_path / "web991.docx"
    stage4_json.write_text(json.dumps({"document_uid": "web991", **stage4_out}))
    generator = stage_6_word_template.WCMTemplateGenerator(verbose=False)
    generator._reconsider_appendix_entries = lambda: None
    generator.generate(str(stage4_json), str(rendered), research_summary_path=None)
    doc = Document(str(rendered))
    table = next(t for t in doc.tables if any("Work email:" in c.text for r in t.rows for c in r.cells))
    rows = {row.cells[0].text.strip().lower(): row.cells[1].text.strip() for row in table.rows}
    return rows, "\n".join(t.text or "" for t in doc.element.body.iter(_W_T))


def test_header_contact_reaches_stage4_fields_and_the_personal_data_table(monkeypatch, tmp_path):
    """The wire, docx -> stage 4 -> stage 6: a letterhead only in the
    first-page header, a body that opens with no contact and holds a
    25-paragraph content control. The owner's name, office phone, office
    address and work email reach the Personal Data table."""
    out, calls = _letterhead_stage4(monkeypatch, tmp_path)

    assert out["cv_owner"]["last_name"] == "Synthetic"
    first = out["entries"][0]
    assert first["taxonomy_code"] == "A"
    assert first[owner_name.OWNER_CONTACT_SOURCE_KEY] == owner_name.OWNER_CONTACT_SOURCE_HEADER_FOOTER
    assert {k: first["extracted_fields"].get(k) for k in _HEADER_CONTACT_FIELDS} == _HEADER_CONTACT_FIELDS
    assert len(calls) == 2  # the body group, then the letterhead entry
    assert out["total_cost"] == pytest.approx(0.5)  # the letterhead call is accounted
    assert out["total_tokens"] == 200
    assert (out["cache_read_tokens"], out["cache_write_tokens"]) == (6, 8)

    rows, everything = _render_personal_data(monkeypatch, tmp_path, out)

    assert rows["office address:"] == _HEADER_CONTACT_FIELDS["address"]
    assert rows["office telephone:"] == _HEADER_CONTACT_FIELDS["phone"]
    assert rows["work email:"] == _HEADER_CONTACT_FIELDS["email"]
    assert "Quinn Synthetic, MD, PhD" in everything


def test_home_phone_in_the_header_is_withheld_as_body_home_contact_is(monkeypatch, tmp_path):
    """#821 holds for the letterhead entry: the home number stage 4 extracts
    from it renders nowhere, while the work email still does."""
    out, _ = _letterhead_stage4(monkeypatch, tmp_path, header=[*LETTERHEAD, _HOME_LINE])
    assert out["entries"][0]["extracted_fields"]["home_phone"] == _HOME_PHONE

    rows, everything = _render_personal_data(monkeypatch, tmp_path, out)

    assert rows["work email:"] == _HEADER_CONTACT_FIELDS["email"]
    assert "555-0199" not in everything


def test_body_contact_means_no_header_contact_call(monkeypatch, tmp_path):
    """A body A entry that holds contact: the letterhead is not extracted,
    so no extra LLM call and no second contact entry."""
    body_contact = {"text": "Email: quinn.synthetic@example.org", "taxonomy_code": "A",
                    "element_idx_start": 0, "element_idx_end": 0}

    out, calls = _letterhead_stage4(monkeypatch, tmp_path, body=(body_contact,))

    assert len(calls) == 1
    assert [e.get(owner_name.OWNER_CONTACT_SOURCE_KEY) for e in out["entries"]] == [None]


def test_header_without_contact_means_no_header_contact_call(monkeypatch, tmp_path):
    calls: list[str] = []
    monkeypatch.setattr(extraction, "call_llm", _fake_stage4_llm(calls))
    monkeypatch.setattr(owner_name, "call_llm", _fake_owner_llm)
    monkeypatch.setattr(extraction, "infer_cv_owner_location", lambda entries: {})

    out = extraction.extract_fields_from_mapped_entries(
        [dict(_BODY_ENTRY)], document_uid="web991", workers=1,
        docx_path=letterhead_docx(tmp_path, header=["Quinn Synthetic, MD", "Curriculum Vitae"]))

    assert len(calls) == 1
    assert len(out["entries"]) == 1


def test_body_has_owner_contact_reads_a_entries_and_their_records():
    def a_entry(fields, code="A"):
        return {"taxonomy_code": code, "extracted_fields": fields}

    has = extraction._body_has_owner_contact
    assert has([a_entry({"email": "x@example.org"})])
    assert has([a_entry({"office_phone": "212-555-0100"})])
    assert has([a_entry({"name": "Q", extraction.STAGE4_RECORDS_KEY: [{"address": "1 Example Ave"}]})])
    assert not has([a_entry({"email": "", "phone": None, "address": {}, "home_address": []})])
    assert not has([a_entry({"name": "Quinn Synthetic"})])
    assert not has([a_entry({"email": "x@example.org"}, code="B1")])
    assert not has([{"taxonomy_code": "A"}])
