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
