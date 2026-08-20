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
import re
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage_3b_entry_classifier as stage_3b  # noqa: E402
# call_llm is stubbed at the module that actually calls it: the #522 split
# moved every classification call site into stage3b/classify.py, so patching
# the facade's copy would no longer intercept anything (#496).
import unified_pipeline.stage3b.classify as stage3b_classify  # noqa: E402
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

    monkeypatch.setattr(stage3b_classify, "call_llm", call)
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
        "invalid_code_entries": 0,
        "classification_rules_version": "2.6.0",
    }

    # The malformed object in batch 4 is logged, same as before the split.
    warned = [r for r in caplog.records if "malformed classification" in r.getMessage()]
    assert len(warned) == 1
    assert "skipped 1" in warned[0].getMessage()


def test_classify_entries_batch_is_under_the_ratchet_threshold():
    """The actual concern #604 exists for: classify_entries_batch itself,
    not just its helpers, must drop under check_function_size.py's 200-line
    THRESHOLD -- #522's file split alone would not have shrunk it."""
    # classify_entries_batch lives in stage3b/classify.py since the #522 split.
    module_path = _SRC / "unified_pipeline" / "stage3b" / "classify.py"
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


def test_classify_entries_batch_empty_input(monkeypatch):
    """entries=[] is a common boundary case for batch-processing code -- must
    not invoke call_llm at all and must return zeroed stats without raising."""
    def _boom(**kwargs):
        raise AssertionError("call_llm must not be invoked for an empty entries list")

    monkeypatch.setattr(stage3b_classify, "call_llm", _boom)

    results, stats = classify_entries_batch([], _context(), TAXONOMY, batch_size=BATCH_SIZE)

    assert results == []
    assert stats == {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost": 0.0,
        "model": None,
        "classification_rules_version": "2.6.0",
        "entries_classified": 0,
        "llm_batches": 0,
        "failed_batches": 0,
        "llm_classified": 0,
        "fallback_entries": 0,
        "empty_entries": 0,
        "invalid_code_entries": 0,
    }


def test_classify_entries_batch_size_zero_raises(monkeypatch):
    """batch_size=0 is passed straight into range(0, len(entries), batch_size)
    (classify.py:279), which itself raises ValueError("range() arg 3 must not
    be zero") before any entry is touched. Pin that boundary explicitly
    rather than leaving it as an unasserted implicit side effect of the
    stdlib call."""
    def _boom(**kwargs):
        raise AssertionError("call_llm must not be invoked when batch_size=0")

    monkeypatch.setattr(stage3b_classify, "call_llm", _boom)

    with pytest.raises(ValueError):
        classify_entries_batch(
            [_entry("Award A"), _entry("Award B")], _context(), TAXONOMY, batch_size=0
        )


def test_classify_entries_batch_size_negative_raises(monkeypatch):
    """A negative batch_size used to make range(0, len(entries), batch_size)
    an EMPTY range under Python's negative-step semantics (start=0 already >=
    stop), so the for loop in classify_entries_batch silently skipped every
    entry with no error at all -- arguably more dangerous than batch_size=0,
    which at least raised. classify_entries_batch now guards `batch_size <= 0`
    explicitly (before either boundary reaches range()), so both raise the
    same way: pinned here as the negative counterpart to
    test_classify_entries_batch_size_zero_raises above."""
    def _boom(**kwargs):
        raise AssertionError("call_llm must not be invoked when batch_size<0")

    monkeypatch.setattr(stage3b_classify, "call_llm", _boom)

    with pytest.raises(ValueError):
        classify_entries_batch(
            [_entry("Award A"), _entry("Award B")], _context(), TAXONOMY, batch_size=-1
        )


def _echo_call_llm(monkeypatch):
    """Classifies every entry index found in the prompt as ("H", 0.9) -- a
    generic well-formed stub for tests that only care about batching shape,
    not response content. Reads indices back out of the "[N] (Section: ...)"
    lines _classify_one_batch builds into the user message."""
    def call(**kwargs):
        user_msg = kwargs["messages"][1]["content"]
        indices = [int(m) for m in re.findall(r"^\[(\d+)\]", user_msg, flags=re.MULTILINE)]
        return {
            "content": json.dumps({"classifications": [
                {"index": i, "code": "H", "confidence": 0.9} for i in indices
            ]}),
            "prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001,
            "model": "gpt-5.1-mini-echo",
        }

    monkeypatch.setattr(stage3b_classify, "call_llm", call)


@pytest.mark.parametrize("num_entries,batch_size,expected_llm_batches", [
    (2, 2, 1),  # exactly one full batch
    (3, 2, 2),  # one full batch + a single-entry batch
    (5, 2, 3),  # 2 + 2 + 1: partial final batch, the classic off-by-one
])
def test_classify_entries_batch_boundary_and_partial_final_batch(
    monkeypatch, num_entries, batch_size, expected_llm_batches
):
    """Every pre-existing test uses an entry count that's an exact multiple
    of batch_size (8 / 2); the shorter-final-slice path was never exercised.
    Also pins that results stay in original input order across batches."""
    _echo_call_llm(monkeypatch)
    entries = [_entry(f"Award {i}") for i in range(num_entries)]

    results, stats = classify_entries_batch(entries, _context(), TAXONOMY, batch_size=batch_size)

    assert len(results) == num_entries
    assert stats["llm_batches"] == expected_llm_batches
    assert stats["llm_classified"] == num_entries
    for i, r in enumerate(results):
        assert r["classification_source"] == "llm"
        assert r["text"] == f"Award {i}"


def test_classify_entries_batch_handles_unparseable_llm_json(monkeypatch, caplog):
    """Every existing 'failure' test raises directly from call_llm. Nothing
    exercises json.loads(content) itself raising on genuinely invalid JSON --
    it's wrapped by the same try/except, so it should behave identically to
    a raised exception: batch counted failed, both entries fall back."""
    def call(**kwargs):
        return {
            "content": "not valid json {{{",
            "prompt_tokens": 50, "completion_tokens": 10, "cost": 0.001,
            "model": "gpt-5.1-mini-bad-json",
        }

    monkeypatch.setattr(stage3b_classify, "call_llm", call)

    with caplog.at_level(logging.ERROR, logger=stage_3b.logger.name):
        results, stats = classify_entries_batch(
            [_entry("Award A"), _entry("Award B")], _context(), TAXONOMY, batch_size=2
        )

    assert len(results) == 2
    assert all(r["classification_source"] == "fallback" for r in results)
    assert all(r["taxonomy_code"] == "H" for r in results)
    assert stats["failed_batches"] == 1
    assert stats["llm_batches"] == 1
    # Stats are never assigned on this path -- json.loads raised before the
    # llm_result fields were read -- so they stay at _BatchStats' defaults,
    # same as a raised call_llm exception.
    assert stats["input_tokens"] == 0
    assert stats["model"] is None


@pytest.mark.parametrize("response_json", [
    {"foo": "bar"},           # no "classifications" key at all
    {"classifications": []},  # key present but empty
], ids=["missing-key", "empty-list"])
def test_classify_entries_batch_handles_missing_or_empty_classifications(monkeypatch, response_json):
    """Distinct from the malformed-individual-object case: here the JSON
    parses fine and the top-level shape is fine, there's just nothing to map
    back to entries. Both should behave as a clean fallback, not a failure --
    the response was successfully parsed, so tokens/cost/model ARE recorded."""
    def call(**kwargs):
        return {
            "content": json.dumps(response_json),
            "prompt_tokens": 40, "completion_tokens": 8, "cost": 0.0008,
            "model": "gpt-5.1-mini-noclass",
        }

    monkeypatch.setattr(stage3b_classify, "call_llm", call)

    results, stats = classify_entries_batch(
        [_entry("Award A"), _entry("Award B")], _context(), TAXONOMY, batch_size=2
    )

    assert len(results) == 2
    assert all(r["classification_source"] == "fallback" for r in results)
    assert stats["failed_batches"] == 0
    assert stats["llm_batches"] == 1
    assert stats["fallback_entries"] == 2
    assert stats["model"] == "gpt-5.1-mini-noclass"


def test_classify_entries_batch_malformed_indices_do_not_cross_contaminate(monkeypatch):
    """Duplicate/out-of-range/negative/non-integer indices must never let one
    entry receive another entry's classification data (#520/#521's silent-
    failure class), even though none of them raise.

    class_by_idx is a dict keyed by the raw "index" value the LLM returned
    (classify.py:185-189), looked up as class_by_idx.get(orig_idx) where
    orig_idx is always a plain non-negative int from enumerate(batch_entries).
    So the classic "Python treats -1 as a valid sequence index" hazard does
    NOT apply here -- dict.get(-1) never coincidentally resolves to the last
    entry the way list[-1] indexing would; this test pins that explicitly
    below instead of leaving it implicit in "entry 1 falls back".

    Duplicate index=0 objects are pinned as last-write-wins: that is plain
    dict overwrite semantics from an unconditional `class_by_idx[idx] = c`
    with no existing-key check, not a documented contract. If ambiguous
    duplicate indices from the LLM should instead be rejected/fall back
    (arguably the safer read of an inconsistent response), that check
    belongs in classify.py -- this test only pins what the code does today.
    """
    def call(**kwargs):
        return {
            "content": json.dumps({"classifications": [
                {"index": 0, "code": "H", "confidence": 0.9},
                {"index": 0, "code": "T", "confidence": 0.99},  # duplicate index
                {"index": 99, "code": "X", "confidence": 0.9},  # out of range
                {"index": -1, "code": "Y", "confidence": 0.9},  # negative
                {"index": "1", "code": "Z", "confidence": 0.9},  # string, not int
            ]}),
            "prompt_tokens": 30, "completion_tokens": 10, "cost": 0.001,
            "model": "gpt-5.1-mini-idx",
        }

    monkeypatch.setattr(stage3b_classify, "call_llm", call)

    results, stats = classify_entries_batch(
        [_entry("Award A"), _entry("Award B")], _context(), TAXONOMY, batch_size=2
    )

    assert len(results) == 2
    # Entry 0: only ever sees index=0 objects (last one wins, see docstring)
    # -- never the out-of-range/negative/string-indexed data.
    assert results[0]["classification_source"] == "llm"
    assert results[0]["taxonomy_code"] == "T"
    assert results[0]["taxonomy_confidence"] == 0.99
    # Entry 1: none of index=99 (out of range), index=-1 (negative -- proven
    # NOT to alias the last entry the way list[-1] would), or index="1"
    # (string, not int) match int orig_idx=1, so it falls back instead of
    # being corrupted by any of that data.
    assert results[1]["classification_source"] == "fallback"
    assert results[1]["taxonomy_code"] != "Y"  # would mean -1 aliased entry 1
    assert results[1]["taxonomy_code"] != "Z"  # would mean "1" aliased entry 1


def test_classify_entries_batch_bool_and_float_indices_alias_int_lookup(monkeypatch):
    """The string-index case above proves a non-int "index" is safely
    rejected -- but that does NOT generalize to every non-int type. Python's
    dict keys compare by == with matching hashes, and True == 1 and
    0.0 == 0 both hold, so class_by_idx.get(orig_idx) resolves a key stored
    as a bool or float exactly as it would resolve a plain int key.
    classify.py never coerces or type-checks "index" before using it as a
    dict key (classify.py:185-189), so this is real, reachable behavior for
    any LLM response that emits a JSON float (or unlikely but valid JSON
    boolean) where an int was expected -- not a hypothetical."""
    def call(**kwargs):
        return {
            "content": json.dumps({"classifications": [
                {"index": True, "code": "H", "confidence": 0.9},  # aliases int 1
                {"index": 0.0, "code": "T", "confidence": 0.8},   # aliases int 0
            ]}),
            "prompt_tokens": 20, "completion_tokens": 5, "cost": 0.0005,
            "model": "gpt-5.1-mini-alias",
        }

    monkeypatch.setattr(stage3b_classify, "call_llm", call)

    results, stats = classify_entries_batch(
        [_entry("Award A"), _entry("Award B")], _context(), TAXONOMY, batch_size=2
    )

    assert results[0]["classification_source"] == "llm"
    assert results[0]["taxonomy_code"] == "T"  # 0.0 aliased orig_idx=0
    assert results[1]["classification_source"] == "llm"
    assert results[1]["taxonomy_code"] == "H"  # True aliased orig_idx=1


def test_classify_entries_batch_uses_index_not_position(monkeypatch):
    """Every existing mock returns classifications in the same order as the
    input entries, so a naive positional zip(entries, classifications) --
    ignoring "index" entirely -- would pass unnoticed. Return them reversed
    to prove index-based lookup is actually happening."""
    def call(**kwargs):
        return {
            "content": json.dumps({"classifications": [
                {"index": 1, "code": "T", "confidence": 0.8},
                {"index": 0, "code": "H", "confidence": 0.9},
            ]}),
            "prompt_tokens": 30, "completion_tokens": 10, "cost": 0.001,
            "model": "gpt-5.1-mini-rev",
        }

    monkeypatch.setattr(stage3b_classify, "call_llm", call)

    results, stats = classify_entries_batch(
        [_entry("Award A"), _entry("Award B")], _context(), TAXONOMY, batch_size=2
    )

    assert results[0]["taxonomy_code"] == "H"
    assert results[1]["taxonomy_code"] == "T"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
