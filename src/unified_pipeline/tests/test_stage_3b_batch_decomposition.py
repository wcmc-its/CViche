"""Equivalence guard for the classify_entries_batch decomposition (#604).

classify_entries_batch was 218 lines after #522's prompt hoist -- still over
scripts/check_function_size.py's 200-line THRESHOLD. #604 splits it into
_build_taxonomy_ref_for_batch (the once-per-call setup) and
_classify_one_batch (the per-batch loop body), with classify_entries_batch
reduced to looping and merging each batch's own results/stats.

This is real classification-affecting code, not a pure relocation -- #520 and
#521 both previously lived in this exact function and were exactly this class
of silent bug (index-mapping drift, an unguarded malformed-object crash). So
this test drives ONE classify_entries_batch call through four batches, each
exercising a different path through the loop body in a single run, and pins
the merged (all_results, stats) tuple:

    batch 1 (2 entries, no text)  -> empty_entry path, call_llm never invoked
    batch 2 (2 entries)           -> normal well-formed LLM classification
    batch 3 (2 entries)           -> one well-formed + one malformed object
    batch 4 (2 entries)           -> call_llm raises, both entries fall back

The failing batch is deliberately LAST: that is the arrangement that catches
a merge bug where a batch's own (empty/None) contribution overwrites the
outer accumulator instead of being folded into it -- e.g. ``observed_model``
must keep the last model a *successful* call reported even though the very
last batch in the run failed and reported no model at all (#459). A
mutation that drops the previous value on merge passes every existing
stage_3b test but fails this one.
"""
import ast
import json
import logging
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage_3b_entry_classifier as stage_3b  # noqa: E402
from unified_pipeline.stage_3b_entry_classifier import (  # noqa: E402
    TaxonomyContext,
    classify_entries_batch,
    load_taxonomy,
)

TAXONOMY = load_taxonomy()
BATCH_SIZE = 2  # forces exactly 4 batches over the 8 entries below
_RATCHET_THRESHOLD = 200  # scripts/check_function_size.py's THRESHOLD


def _context(codes=("H",)):
    return TaxonomyContext(meta_section={
        "title": "HONORS AND AWARDS",
        "taxonomy_options": [{"code": c, "confidence": 0.9} for c in codes],
    })


def _entry(text):
    return {"text": text, "hierarchy": ["HONORS AND AWARDS"]}


def _empty():
    return {"text": "  ", "hierarchy": ["HONORS AND AWARDS"]}


def _four_batch_entries():
    return [
        # batch 1: all-empty -> empty_entry path, no LLM call
        _empty(), _empty(),
        # batch 2: normal well-formed LLM classification
        _entry("Dean's Award for Excellence, 2015"), _entry("Teaching Prize, 2018"),
        # batch 3: one well-formed classification + one malformed object
        _entry("Committee Member, Curriculum Committee"), _entry("Society Fellow, 2021"),
        # batch 4: call_llm raises -> both fall back
        _entry("Grant Review Panel, NIH, 2019"), _entry("Editorial Board Member, 2020"),
    ]


def _four_batch_call_llm(monkeypatch):
    """Serves a different canned response shape on each of the 3 LLM calls
    this run makes (batch 1 makes none). Returns the call counter so tests
    can assert exactly 3 calls happened."""
    calls = {"n": 0}

    def call(**kwargs):
        calls["n"] += 1
        n = calls["n"]
        if n == 1:  # batch 2: normal well-formed response
            return {
                "content": json.dumps({"classifications": [
                    {"index": 0, "code": "H", "confidence": 0.9, "reasoning": "clear award"},
                    {"index": 1, "code": "T", "confidence": 0.75},
                ]}),
                "prompt_tokens": 120, "completion_tokens": 30, "cost": 0.002,
                "model": "gpt-5.1-mini-A",
            }
        if n == 2:  # batch 3: one well-formed, one malformed (no "index")
            return {
                "content": json.dumps({"classifications": [
                    {"index": 0, "code": "B1", "confidence": 0.8},
                    {"code": "T", "confidence": 0.5},  # malformed: missing "index"
                ]}),
                "prompt_tokens": 90, "completion_tokens": 15, "cost": 0.0015,
                "model": "gpt-5.1-mini-B",
            }
        if n == 3:  # batch 4: raises -- the LAST batch in the run
            raise RuntimeError("ThrottlingException: rate exceeded")
        raise AssertionError(f"unexpected extra call_llm invocation #{n}")

    monkeypatch.setattr(stage_3b, "call_llm", call)
    return calls


def test_four_batch_shapes_produce_exact_expected_results_and_stats(monkeypatch, caplog):
    calls = _four_batch_call_llm(monkeypatch)

    with caplog.at_level(logging.WARNING, logger=stage_3b.logger.name):
        results, stats = classify_entries_batch(
            _four_batch_entries(), _context(), TAXONOMY, batch_size=BATCH_SIZE
        )

    assert calls["n"] == 3, "batch 1 (all-empty) must never call call_llm"
    assert len(results) == 8

    # Batch 1: empty_entry path, no LLM call.
    for r in results[0:2]:
        assert r["classification_source"] == "empty_entry"
        assert r["taxonomy_code"] == "H"
        assert r["taxonomy_confidence"] == 0.0
        assert "classification_reasoning" not in r

    # Batch 2: normal well-formed LLM classification, both entries mapped
    # by their own index.
    assert results[2]["classification_source"] == "llm"
    assert results[2]["taxonomy_code"] == "H"
    assert results[2]["taxonomy_confidence"] == 0.9
    assert results[2]["classification_reasoning"] == "clear award"
    assert results[3]["classification_source"] == "llm"
    assert results[3]["taxonomy_code"] == "T"
    assert results[3]["taxonomy_confidence"] == 0.75
    assert results[3]["classification_reasoning"] is None

    # Batch 3: one well-formed classification, one malformed object (no
    # "index") that falls back instead of crashing the run (#521).
    assert results[4]["classification_source"] == "llm"
    assert results[4]["taxonomy_code"] == "B1"
    assert results[4]["taxonomy_confidence"] == 0.8
    assert results[5]["classification_source"] == "fallback"
    assert results[5]["taxonomy_code"] == "H"
    assert results[5]["taxonomy_confidence"] == 0.5

    # Batch 4: call_llm raised -> both entries fall back to the primary code.
    for r in results[6:8]:
        assert r["classification_source"] == "fallback"
        assert r["taxonomy_code"] == "H"
        assert r["taxonomy_confidence"] == 0.5

    # Stats: merged across all four batches, matching the six accumulators
    # classify_entries_batch used to thread through the loop by hand.
    assert stats == {
        "input_tokens": 120 + 90,
        "output_tokens": 30 + 15,
        "total_tokens": (120 + 90) + (30 + 15),
        "cost": pytest.approx(0.002 + 0.0015),
        # Last model a SUCCESSFUL call reported -- batch 4 (the LAST batch in
        # the run) failed and reported no model, and must not blank this
        # back to None (#459).
        "model": "gpt-5.1-mini-B",
        "entries_classified": 8,
        "llm_batches": 3,
        "failed_batches": 1,
        "llm_classified": 3,   # 2 from batch 2 + 1 from batch 3
        "fallback_entries": 3,  # 1 from batch 3 + 2 from batch 4
        "empty_entries": 2,    # batch 1
    }

    # The malformed object in batch 4 is logged, same as before the split.
    warned = [r for r in caplog.records if "malformed classification" in r.getMessage()]
    assert len(warned) == 1
    assert "skipped 1" in warned[0].getMessage()


def test_classify_entries_batch_is_under_the_ratchet_threshold():
    """The actual concern #604 exists for: classify_entries_batch itself,
    not just its helpers, must drop under check_function_size.py's 200-line
    THRESHOLD -- #522's file split alone would not have shrunk it."""
    module_path = _SRC / "unified_pipeline" / "stage_3b_entry_classifier.py"
    tree = ast.parse(module_path.read_text())
    func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "classify_entries_batch"
    )
    span = func.end_lineno - func.lineno + 1
    assert span < _RATCHET_THRESHOLD, (
        f"classify_entries_batch is {span} lines, expected under {_RATCHET_THRESHOLD}"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
