"""Regression guard for #642 review: lookup.py's two verbose-gated print()s
(:158, :185) and stage_5b_institution_enrichment.py's own verbose-gated
prints must all route through the project logger, not stdout.

Follows this repo's established print->logger conversion pattern (see
test_stage6_bibliography_round2.py): assert the record lands on the
module's own logger via caplog, and that capsys sees nothing on stdout.
Both sites keep their existing ``if verbose:`` gate unchanged -- ``verbose``
is a live, CLI-plumbed parameter here, not decorative -- so these tests
drive verbose=True and confirm the gate still suppresses at verbose=False.

    python3 -m pytest src/unified_pipeline/tests/test_stage5b_print_to_logger.py -p no:cacheprovider

Self-contained: no DB, no network, no real LLM call.
"""

import json
import logging
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _fake_llm_result(content="{}"):
    return {
        "content": content,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost": 0.0042,
        "model": "sentinel-print-to-logger",
        "provider": "bedrock",
        "finish_reason": "stop",
        "latency_ms": 12,
    }


def test_lookup_institutions_llm_logs_via_project_logger_not_print(monkeypatch, caplog, capsys):
    from unified_pipeline.stage5b import lookup as s5b_lookup

    monkeypatch.setattr(
        s5b_lookup, "call_llm",
        lambda **kw: _fake_llm_result('{"INST-0001": {"city": "Ithaca"}}'),
    )
    batch = [("INST-0001", "Cornell University", "B1")]

    with caplog.at_level(logging.INFO, logger="unified_pipeline.stage5b.lookup"):
        results, cost, _model = s5b_lookup.lookup_institutions_llm(batch, None, verbose=True)

    assert results == {"INST-0001": {"city": "Ithaca"}}
    assert any(
        record.levelno == logging.INFO and "LLM batch: 1 institutions" in record.message
        for record in caplog.records
    )
    assert any(
        record.levelno == logging.INFO
        and "LLM resolved 1 institutions" in record.message
        and "0.0042" in record.message
        for record in caplog.records
    )
    # Nothing went to stdout -- the print() is gone, not just quieter.
    assert capsys.readouterr().out == ""


def test_lookup_institutions_llm_quiet_mode_logs_nothing(monkeypatch, caplog, capsys):
    """The if verbose: gate itself is unchanged -- verbose=False must still
    suppress these two log lines exactly as it suppressed the old prints."""
    from unified_pipeline.stage5b import lookup as s5b_lookup

    monkeypatch.setattr(
        s5b_lookup, "call_llm",
        lambda **kw: _fake_llm_result('{"INST-0001": {"city": "Ithaca"}}'),
    )
    batch = [("INST-0001", "Cornell University", "B1")]

    with caplog.at_level(logging.INFO, logger="unified_pipeline.stage5b.lookup"):
        s5b_lookup.lookup_institutions_llm(batch, None, verbose=False)

    assert not any(
        "LLM batch:" in record.message or "LLM resolved" in record.message
        for record in caplog.records
    )
    assert capsys.readouterr().out == ""


def test_run_stage5b_verbose_logs_via_project_logger_not_print(monkeypatch, caplog, capsys, tmp_path):
    from unified_pipeline import stage_5b_institution_enrichment as s5b
    from unified_pipeline.stage5b import cache as s5b_cache
    from unified_pipeline.stage5b import lookup as s5b_lookup

    # Isolate the on-disk cache so this test can't read or write the real one.
    monkeypatch.setattr(s5b_cache, "CACHE_FILE", tmp_path / "institution_cache.json")
    monkeypatch.setattr(s5b_cache, "OLD_CACHE_FILE", tmp_path / "ror_cache.json")
    monkeypatch.setattr(
        s5b_lookup, "call_llm",
        lambda **kw: _fake_llm_result('{"INST-0001": {"city": "Ithaca", "state": "New York"}}'),
    )

    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps({
        "document_uid": "test-uid",
        "entries": [{
            "taxonomy_code": "B1",
            "extracted_fields": {"institution": "Cornell University"},
        }],
    }))
    output_path = tmp_path / "output.json"

    with caplog.at_level(logging.INFO, logger="unified_pipeline.stage_5b_institution_enrichment"):
        s5b.run_stage5b(
            str(input_path), output_path=str(output_path),
            verbose=True, refresh_cache=True,
        )

    assert any(
        "Total entries: 1" in record.message for record in caplog.records
    )
    assert any(
        "LLM batches: 1" in record.message for record in caplog.records
    )
    assert any(
        "Saved to:" in record.message for record in caplog.records
    )
    # Nothing went to stdout -- the print() is gone, not just quieter.
    assert capsys.readouterr().out == ""


def test_run_stage5b_quiet_mode_logs_nothing(monkeypatch, caplog, capsys, tmp_path):
    """verbose=False (the CLI's --quiet) must still suppress every one of
    these lines exactly as it suppressed the old prints."""
    from unified_pipeline import stage_5b_institution_enrichment as s5b
    from unified_pipeline.stage5b import cache as s5b_cache
    from unified_pipeline.stage5b import lookup as s5b_lookup

    monkeypatch.setattr(s5b_cache, "CACHE_FILE", tmp_path / "institution_cache.json")
    monkeypatch.setattr(s5b_cache, "OLD_CACHE_FILE", tmp_path / "ror_cache.json")
    monkeypatch.setattr(
        s5b_lookup, "call_llm",
        lambda **kw: _fake_llm_result('{"INST-0001": {"city": "Ithaca", "state": "New York"}}'),
    )

    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps({
        "document_uid": "test-uid",
        "entries": [{
            "taxonomy_code": "B1",
            "extracted_fields": {"institution": "Cornell University"},
        }],
    }))
    output_path = tmp_path / "output.json"

    with caplog.at_level(logging.INFO, logger="unified_pipeline.stage_5b_institution_enrichment"):
        s5b.run_stage5b(
            str(input_path), output_path=str(output_path),
            verbose=False, refresh_cache=True,
        )

    assert caplog.records == []
    assert capsys.readouterr().out == ""


# --- run_stage5b: failed/partial batch surfacing (#700) -----------------------
#
# A batch-level LLM failure (lookup_institutions_llm returns None -- see
# lookup.py's `except Exception` at the bottom of the function) used to be
# logged and swallowed, leaving institution_enrichment_stats indistinguishable
# from a run where every batch actually succeeded. These tests pin: failed
# batches and unresolved institutions are counted and written to the stats
# dict, a partial failure is logged as a warning (not silently absorbed), and
# a run whose every batch fails AND enriched nothing raises instead of
# emitting a normal-looking artifact with zero institutions enriched. Unlike
# stage 3b's total-failure guard, a batch that succeeds but resolves no
# match is not itself a failure here (a legitimate negative, cached as
# None) -- zero enriched alone does not trip this guard.

def _institution_entries(n, code="B1"):
    return [
        {"taxonomy_code": code, "extracted_fields": {"institution": f"Institution {i:03d}"}}
        for i in range(n)
    ]


def _isolate_cache(monkeypatch, tmp_path):
    from unified_pipeline.stage5b import cache as s5b_cache
    monkeypatch.setattr(s5b_cache, "CACHE_FILE", tmp_path / "institution_cache.json")
    monkeypatch.setattr(s5b_cache, "OLD_CACHE_FILE", tmp_path / "ror_cache.json")


def test_run_stage5b_all_batches_fail_raises_no_output(monkeypatch, tmp_path):
    """Zero successful institution lookups in a nonempty run -> raise, no output."""
    from unified_pipeline import stage_5b_institution_enrichment as s5b
    from unified_pipeline.stage5b import lookup as s5b_lookup

    _isolate_cache(monkeypatch, tmp_path)

    def _boom(**kwargs):
        raise RuntimeError("ValidationException: invalid model identifier")

    monkeypatch.setattr(s5b_lookup, "call_llm", _boom)

    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps({
        "document_uid": "test-uid",
        "entries": _institution_entries(1),
    }))
    output_path = tmp_path / "output.json"

    with pytest.raises(RuntimeError, match="zero successful institution lookups"):
        s5b.run_stage5b(
            str(input_path), output_path=str(output_path),
            verbose=False, refresh_cache=True,
        )

    assert not output_path.exists()


def test_run_stage5b_all_batches_fail_but_cache_enriched_does_not_raise(monkeypatch, tmp_path, caplog):
    """Every LLM batch fails, but a cache hit already enriched one entry: the
    artifact is not empty, so the run completes with the failure counted and
    warned rather than throwing the cached enrichment away."""
    from unified_pipeline import stage_5b_institution_enrichment as s5b
    from unified_pipeline.stage5b import lookup as s5b_lookup

    _isolate_cache(monkeypatch, tmp_path)
    cached = {"source": "llm", "cleaned_name": "Institution 000", "city": "Ithaca",
              "state": "NY", "country": "United States", "country_code": "US"}
    real_lookup = s5b.cache.lookup
    monkeypatch.setattr(
        s5b.cache, "lookup",
        lambda key, raw_key=None: (True, cached) if "000" in key else real_lookup(key, raw_key),
    )

    def _boom(**kwargs):
        raise RuntimeError("ValidationException: invalid model identifier")

    monkeypatch.setattr(s5b_lookup, "call_llm", _boom)

    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps({
        "document_uid": "test-uid",
        "entries": _institution_entries(2),
    }))
    output_path = tmp_path / "output.json"

    with caplog.at_level(
        logging.WARNING, logger="unified_pipeline.stage_5b_institution_enrichment"
    ):
        result_path = s5b.run_stage5b(
            str(input_path), output_path=str(output_path), verbose=False,
        )

    stats = json.loads(Path(result_path).read_text())["institution_enrichment_stats"]
    assert stats["entries_enriched"] == 1
    assert stats["llm_batches"] == 1
    assert stats["failed_batches"] == 1
    assert stats["institutions_unresolved"] == 1
    assert any("1 of 1" in r.getMessage() for r in caplog.records)


def test_run_stage5b_partial_failure_counted_and_warned(monkeypatch, tmp_path, caplog):
    """12 distinct institutions form two batches (BATCH_SIZE=10): the first
    batch's call raises, the second succeeds. The run completes, and the
    output stats say so -- both llm_batches/failed_batches/
    institutions_unresolved on the artifact, and a run-level warning log."""
    from unified_pipeline import stage_5b_institution_enrichment as s5b
    from unified_pipeline.stage5b import lookup as s5b_lookup

    _isolate_cache(monkeypatch, tmp_path)

    calls = {"n": 0}

    def _first_batch_fails(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("ThrottlingException: rate exceeded")
        return _fake_llm_result("{}")

    monkeypatch.setattr(s5b_lookup, "call_llm", _first_batch_fails)

    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps({
        "document_uid": "test-uid",
        "entries": _institution_entries(12),
    }))
    output_path = tmp_path / "output.json"

    with caplog.at_level(
        logging.WARNING, logger="unified_pipeline.stage_5b_institution_enrichment"
    ):
        result_path = s5b.run_stage5b(
            str(input_path), output_path=str(output_path),
            verbose=False, refresh_cache=True,
        )

    assert calls["n"] == 2
    stats = json.loads(Path(result_path).read_text())["institution_enrichment_stats"]
    assert stats["llm_batches"] == 2
    assert stats["failed_batches"] == 1
    assert stats["institutions_unresolved"] == 10

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "1 of 2" in warnings[0].getMessage()
    assert "10 institutions unresolved" in warnings[0].getMessage()


def test_run_stage5b_zero_llm_batches_does_not_raise(monkeypatch, tmp_path):
    """No institution needs an LLM lookup at all (e.g. every one already has
    city/state from the original CV extraction, so uncached_count stays 0) --
    llm_batches == failed_batches == 0, and the run must NOT raise.

    Pins the boundary of _raise_or_warn_on_batch_failures: the guard has to
    be `llm_batches > 0 and failed_batches == llm_batches`, not just
    `failed_batches == llm_batches`, which is vacuously true at 0 == 0 and
    would misfire on every zero-LLM-batch run -- every institution served
    from cache, or a CV with no B-code institutions at all."""
    from unified_pipeline import stage_5b_institution_enrichment as s5b
    from unified_pipeline.stage5b import lookup as s5b_lookup

    _isolate_cache(monkeypatch, tmp_path)

    def _fail_if_called(**kwargs):
        raise AssertionError("call_llm must not be invoked when nothing is uncached")

    monkeypatch.setattr(s5b_lookup, "call_llm", _fail_if_called)

    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps({
        "document_uid": "test-uid",
        "entries": [{
            "taxonomy_code": "B1",
            "extracted_fields": {
                "institution": "Institution 000",
                "city": "Ithaca",
                "state": "New York",
            },
        }],
    }))
    output_path = tmp_path / "output.json"

    result_path = s5b.run_stage5b(
        str(input_path), output_path=str(output_path),
        verbose=False, refresh_cache=False,
    )

    stats = json.loads(Path(result_path).read_text())["institution_enrichment_stats"]
    assert stats["llm_batches"] == 0
    assert stats["failed_batches"] == 0
    assert stats["institutions_unresolved"] == 0


def test_run_stage5b_all_batches_succeed_stats_are_zero(monkeypatch, tmp_path):
    """Regression pin: the new fields are always present, at zero when
    nothing failed -- not just added on the failure path."""
    from unified_pipeline import stage_5b_institution_enrichment as s5b
    from unified_pipeline.stage5b import lookup as s5b_lookup

    _isolate_cache(monkeypatch, tmp_path)
    monkeypatch.setattr(s5b_lookup, "call_llm", lambda **kw: _fake_llm_result("{}"))

    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps({
        "document_uid": "test-uid",
        "entries": _institution_entries(1),
    }))
    output_path = tmp_path / "output.json"

    result_path = s5b.run_stage5b(
        str(input_path), output_path=str(output_path),
        verbose=False, refresh_cache=True,
    )

    stats = json.loads(Path(result_path).read_text())["institution_enrichment_stats"]
    assert stats["llm_batches"] == 1
    assert stats["failed_batches"] == 0
    assert stats["institutions_unresolved"] == 0


def test_institution_enrichment_stats_empty_dict_not_counted_as_enriched():
    """No code writes `institution_enrichment: {}` today (5b always writes a
    7-key dict; stage 6 positions.py only copies a truthy mapping). Pin that
    {} and None/absent both count as unenriched, in case a writer ever does."""
    from unified_pipeline import stage_5b_institution_enrichment as s5b

    entries = [
        {"taxonomy_code": "B1", "institution_enrichment": {}},
        {"taxonomy_code": "B1", "institution_enrichment": None},
        {"taxonomy_code": "B1"},
        {"taxonomy_code": "B1", "institution_enrichment": {"source": "llm"}},
    ]

    stats = s5b._build_institution_enrichment_stats(
        entries, institution_entries=4, cached_count=0, uncached_count=0,
        llm_calls=0, llm_batches=0, failed_batches=0, institutions_unresolved=0,
        total_cost=0.0, observed_model=None,
    )

    assert stats["entries_enriched"] == 1


def test_run_stage5b_enriches_c3_fellowship_entries(monkeypatch, tmp_path):
    """#626: a C3 (Fellowship Training) entry reaches the LLM lookup and gets
    institution_enrichment written, like its C/C1/C2 siblings."""
    from unified_pipeline import stage_5b_institution_enrichment as s5b
    from unified_pipeline.stage5b import cache as s5b_cache
    from unified_pipeline.stage5b import lookup as s5b_lookup

    monkeypatch.setattr(s5b_cache, "CACHE_FILE", tmp_path / "institution_cache.json")
    monkeypatch.setattr(s5b_cache, "OLD_CACHE_FILE", tmp_path / "ror_cache.json")
    monkeypatch.setattr(
        s5b_lookup, "call_llm",
        lambda **kw: _fake_llm_result('{"INST-0001": {"city": "Ithaca", "state": "New York"}}'),
    )

    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps({
        "document_uid": "test-uid",
        "entries": [{
            "taxonomy_code": "C3",
            "extracted_fields": {"institution": "Example Fellowship Hospital"},
        }],
    }))
    output_path = tmp_path / "output.json"

    s5b.run_stage5b(str(input_path), output_path=str(output_path), refresh_cache=True)

    out = json.loads(output_path.read_text())
    assert out["institution_enrichment_stats"]["entries_processed"] == 1
    assert out["institution_enrichment_stats"]["entries_enriched"] == 1
    assert out["entries"][0]["institution_enrichment"]["city"] == "Ithaca"
