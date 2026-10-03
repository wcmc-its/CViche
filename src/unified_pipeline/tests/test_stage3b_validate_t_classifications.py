"""Coverage-gap tests for `validate_t_classifications` (stage3b/classify.py).

PR #644 review named 24 cases for this function specifically. 9 were already
covered and 1 was partially covered by the tests in test_stage3b_classify.py
(lines 226-330 there) -- this file adds the remaining 14 (13 fully uncovered
+ 1 partial) without touching or duplicating those.

One recurring confound in the existing suite is worth flagging here too:
every existing validate_t_classifications test builds its LLM stub as
``_llm_response(None) | {"content": ...}``, which always overwrites the
"content" key that _llm_response's default {"classifications": [...]} wrapper
produces -- so that wrapper key is never actually sent through this function
by any existing test, even though a sibling test exists for "results". The
"classifications"-wrapper test below deliberately does NOT override content,
so the function receives that key unmodified.

Self-contained: call_llm stubbed at stage3b.classify (#496), no network/DB.
"""
import json
import logging
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage3b.classify as classify  # noqa: E402

# Confidence value observed in this suite as clearly out of the [0.0, 1.0]
# range _normalize_confidence enforces; used to test the clamp-to-default
# path via validate_t_classifications's own call site (not a sibling's).
_OUT_OF_RANGE_CONFIDENCE = 7.0


def _taxonomy(codes=("H", "T", "S1", "M1", "M2A")):
    return {"codes": [{"code": c, "label": c} for c in codes]}


def _t_entry(text="Some miscellaneous item"):
    return {"text": text, "hierarchy": ["OTHER"], "taxonomy_code": "T"}


def _llm_response(classifications, **extra):
    body = {
        "content": json.dumps({"classifications": classifications}),
        "prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001,
        "model": "test-model",
    }
    body.update(extra)
    return body


# ---------------------------------------------------------------------------
# Positive: multiple T entries, alternate wrapper shapes, partial success
# ---------------------------------------------------------------------------

def test_multiple_t_entries_are_each_reviewed_and_reclassified_independently(monkeypatch):
    """Every existing test sends exactly one T entry. Two T entries, each
    reclassified to a DIFFERENT code, proves entry_index mapping keys off
    the actual index rather than conflating results across entries."""
    entries = [_t_entry("Item A"), _t_entry("Item B")]
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps([
            {"entry_index": 0, "new_code": "S1", "confidence": 0.9, "reasoning": "research"},
            {"entry_index": 1, "new_code": "M2A", "confidence": 0.7, "reasoning": "membership"},
        ])
    })

    updated, stats = classify.validate_t_classifications(entries, _taxonomy())

    assert stats["t_entries_reviewed"] == 2
    assert stats["t_entries_reclassified"] == 2
    assert updated[0]["taxonomy_code"] == "S1"
    assert updated[1]["taxonomy_code"] == "M2A"


def test_wrapped_entries_key_response(monkeypatch):
    """Mirrors the existing "results"-key test, but for the "entries" key --
    no test in the suite constructs this wrapper shape."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps({"entries": [{"entry_index": 0, "new_code": "S1", "confidence": 0.9}]})
    })
    updated, stats = classify.validate_t_classifications([_t_entry()], _taxonomy())
    assert updated[0]["taxonomy_code"] == "S1"
    assert stats["t_entries_reclassified"] == 1


def test_wrapped_classifications_key_response_via_unoverridden_default_content(monkeypatch):
    """_llm_response's own default content is {"classifications": [...]} --
    every OTHER test in the suite overrides "content" and so never actually
    sends this key through validate_t_classifications. Passing the
    classifications list straight through (no content override) exercises
    the "classifications" branch for real."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        [{"entry_index": 0, "new_code": "S1", "confidence": 0.9}]
    ))
    updated, stats = classify.validate_t_classifications([_t_entry()], _taxonomy())
    assert updated[0]["taxonomy_code"] == "S1"
    assert stats["t_entries_reclassified"] == 1


def test_partial_response_applies_one_entry_and_rejects_the_other_independently(monkeypatch):
    """Every existing reclassifications list has exactly one item. A single
    response array with one valid entry and one rejected (unknown code)
    entry must apply the valid one AND leave the rejected one at T, in the
    same call -- proving outcomes are independent, not all-or-nothing."""
    entries = [_t_entry("Item A"), _t_entry("Item B")]
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps([
            {"entry_index": 0, "new_code": "S1", "confidence": 0.8, "reasoning": "ok"},
            {"entry_index": 1, "new_code": "BOGUS", "confidence": 0.7, "reasoning": "nah"},
        ])
    })

    updated, stats = classify.validate_t_classifications(entries, _taxonomy())

    assert updated[0]["taxonomy_code"] == "S1"
    assert updated[1]["taxonomy_code"] == "T"
    assert stats["t_entries_reclassified"] == 1


# ---------------------------------------------------------------------------
# Negative: malformed LLM response shapes
# ---------------------------------------------------------------------------

def test_invalid_json_content_is_caught_and_entries_are_left_unchanged(monkeypatch, caplog):
    """No test sets content to unparseable text. json.loads must raise here
    and be caught by the function's own except branch -- same recovery path
    as the LLM-raises test, but triggered by a parse failure instead."""
    original = [_t_entry()]
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": "not json"
    })

    with caplog.at_level(logging.ERROR, logger=classify.logger.name):
        updated, stats = classify.validate_t_classifications(original, _taxonomy())

    assert updated == original
    assert stats["t_entries_reclassified"] == 0
    assert "error" in stats
    assert any("T-validation failed" in r.getMessage() for r in caplog.records)


def test_empty_json_object_response_yields_no_reclassifications(monkeypatch, caplog):
    """content="{}" is a literal empty object (zero keys) -- distinct from
    the existing "unrecognized wrapper key" test, which uses a non-empty
    dict with an unrelated key. Same else-branch, but only this input
    actually constructs the empty-dict case the reviewer named."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": "{}"
    })

    with caplog.at_level(logging.WARNING, logger=classify.logger.name):
        updated, stats = classify.validate_t_classifications([_t_entry()], _taxonomy())

    assert updated[0]["taxonomy_code"] == "T"
    assert stats["t_entries_reclassified"] == 0
    assert any("keys=[]" in r.getMessage() for r in caplog.records)


def test_non_list_results_value_is_treated_as_no_reclassifications(monkeypatch, caplog):
    """{"results": "not-a-list"} must hit the `if not isinstance(reclassifications,
    list)` guard and log a warning, not iterate over the string's characters."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps({"results": "not-a-list"})
    })

    with caplog.at_level(logging.WARNING, logger=classify.logger.name):
        updated, stats = classify.validate_t_classifications([_t_entry()], _taxonomy())

    assert updated[0]["taxonomy_code"] == "T"
    assert stats["t_entries_reclassified"] == 0
    assert any("not a list" in r.getMessage() for r in caplog.records)


def test_string_entry_index_is_rejected(monkeypatch):
    """The only entry_index types exercised elsewhere are 0 and True. A
    string index must be rejected as malformed, not compared with `<`."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps([{"entry_index": "0", "new_code": "S1", "confidence": 0.9}])
    })
    updated, stats = classify.validate_t_classifications([_t_entry()], _taxonomy())
    assert updated[0]["taxonomy_code"] == "T"
    assert stats["t_entries_reclassified"] == 0


def test_negative_entry_index_is_rejected_not_indexed_from_the_end(monkeypatch):
    """entry_index=-1 must fail the `0 <= entry_idx` guard, not silently
    index the last entry via Python's negative-index semantics."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps([{"entry_index": -1, "new_code": "S1", "confidence": 0.9}])
    })
    updated, stats = classify.validate_t_classifications([_t_entry()], _taxonomy())
    assert updated[0]["taxonomy_code"] == "T"
    assert stats["t_entries_reclassified"] == 0


def test_out_of_range_entry_index_is_rejected_not_indexerror(monkeypatch):
    """entry_index=99 against a single-entry list must be rejected by the
    `entry_idx < len(updated_entries)` guard instead of raising IndexError."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps([{"entry_index": 99, "new_code": "S1", "confidence": 0.9}])
    })
    updated, stats = classify.validate_t_classifications([_t_entry()], _taxonomy())
    assert updated[0]["taxonomy_code"] == "T"
    assert stats["t_entries_reclassified"] == 0


def test_invalid_confidence_sent_through_this_function_clamps_to_default(monkeypatch):
    """Every confidence value sent to validate_t_classifications elsewhere in
    the suite is already valid. An out-of-range value sent through THIS
    function's own call site must still clamp to 0.5, not just via
    classify_entries_batch's separate call site (already tested there)."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps([{
            "entry_index": 0, "new_code": "S1",
            "confidence": _OUT_OF_RANGE_CONFIDENCE, "reasoning": "ok",
        }])
    })
    updated, stats = classify.validate_t_classifications([_t_entry()], _taxonomy())
    assert updated[0]["taxonomy_code"] == "S1"
    assert updated[0]["taxonomy_confidence"] == 0.5


def test_malformed_non_dict_reclassification_object_is_skipped(monkeypatch, caplog):
    """A non-dict item in the reclassifications list (a bare string, the
    same shape used elsewhere for classify_entries_batch's malformed-object
    tests) must be skipped and counted, not crash on `.get`."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps(["not even a dict"])
    })

    with caplog.at_level(logging.WARNING, logger=classify.logger.name):
        updated, stats = classify.validate_t_classifications([_t_entry()], _taxonomy())

    assert updated[0]["taxonomy_code"] == "T"
    assert stats["t_entries_reclassified"] == 0
    assert any("1 malformed" in r.getMessage() for r in caplog.records)


def test_non_string_reasoning_falls_back_to_empty_string(monkeypatch):
    """reasoning=12345 (not a string) must hit the `if not isinstance(reasoning,
    str): reasoning = ""` fallback -- the raw value must not be embedded in
    the persisted classification_reasoning text."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps([{"entry_index": 0, "new_code": "S1", "confidence": 0.9, "reasoning": 12345}])
    })
    updated, stats = classify.validate_t_classifications([_t_entry()], _taxonomy())
    assert updated[0]["taxonomy_code"] == "S1"
    assert "12345" not in updated[0]["classification_reasoning"]
    assert updated[0]["classification_reasoning"] == "[T-validation reclassified from T] "


# ---------------------------------------------------------------------------
# Statistics on the success path (existing suite only checks
# t_entries_reclassified; t_entries_reviewed/cost/input_tokens/output_tokens
# are never asserted together on a real reclassify-something call)
# ---------------------------------------------------------------------------

def test_full_success_path_reports_reviewed_count_cost_and_token_stats(monkeypatch):
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps([{"entry_index": 0, "new_code": "S1", "confidence": 0.85, "reasoning": "ok"}]),
        "prompt_tokens": 42,
        "completion_tokens": 17,
        "cost": 0.0123,
    })

    _, stats = classify.validate_t_classifications([_t_entry()], _taxonomy())

    assert stats["t_entries_reviewed"] == 1
    assert stats["t_entries_reclassified"] == 1
    assert stats["input_tokens"] == 42
    assert stats["output_tokens"] == 17
    assert stats["cost"] == 0.0123


# ---------------------------------------------------------------------------
# Duplicate entry_index: UNSPECIFIED behavior in the source (no dedup guard
# exists at all). This pins what the current code actually does rather than
# inventing a spec -- see the notes returned alongside this file for the
# open question to raise with the reviewer.
# ---------------------------------------------------------------------------

def test_duplicate_entry_index_current_behavior_first_applied_reclassification_wins(monkeypatch):
    """Two reclass objects target the SAME entry_index. There is no dedup
    guard in the source, so this documents observed behavior rather than a
    designed contract: once the first reclassification moves the entry off
    "T", the second one's `old_code == "T"` check no longer holds, so it
    becomes a silent no-op -- this is first-applied-wins, not the more
    obviously-expected "last item in the list wins". If the source changes
    this is exactly the kind of case that should go red; it is not a
    guarantee this function is documented to provide."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps([
            {"entry_index": 0, "new_code": "S1", "confidence": 0.9, "reasoning": "first"},
            {"entry_index": 0, "new_code": "M2A", "confidence": 0.8, "reasoning": "second"},
        ])
    })

    updated, stats = classify.validate_t_classifications([_t_entry()], _taxonomy())

    assert updated[0]["taxonomy_code"] == "S1"
    assert stats["t_entries_reclassified"] == 1


def test_m1_proposal_keeps_t_with_a_distinct_tag(monkeypatch):
    """T-validation must never recode T to M1: stage 6 renders M1 only through
    the stage 4.5 research summary, so a recoded website or project note was
    lost (AUTOPSY-s7ab-batch-2026-10-02 class 11). The entry keeps T, is not
    counted as reclassified, and its reasoning says why -- not "confirmed",
    which stage 6's Appendix filter reads as a structural-drop signal."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps([
            {"entry_index": 0, "new_code": "M1", "confidence": 0.9, "reasoning": "lab site"},
            {"entry_index": 1, "new_code": "S1", "confidence": 0.9, "reasoning": "paper"},
        ])
    })

    updated, stats = classify.validate_t_classifications(
        [_t_entry("Item A"), _t_entry("Item B")], _taxonomy())

    assert updated[0]["taxonomy_code"] == "T"
    assert updated[0]["classification_reasoning"] == "[T-validation: M1 not allowed, kept T] lab site"
    assert updated[0]["t_validation_applied"] is True
    assert updated[1]["taxonomy_code"] == "S1"
    assert stats["t_entries_reclassified"] == 1


def test_fragment_recode_keeps_t_and_a_content_recode_still_applies(monkeypatch):
    """EBYSBC E29/E8 (#986, #985): a date tail and an employer sub-heading the
    model's own reasoning calls a fragment stay T with a refusal tag; a
    content-bearing line in the same response is still recoded."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps([
            {"entry_index": 0, "new_code": "H", "confidence": 0.8, "reasoning": "Date fragment"},
            {"entry_index": 1, "new_code": "S1", "confidence": 0.8,
             "reasoning": "Institution fragment under Committees"},
            {"entry_index": 2, "new_code": "H", "confidence": 0.9, "reasoning": "Named prize"},
        ])
    })

    updated, stats = classify.validate_t_classifications(
        [_t_entry("1971- 1972."), _t_entry("Example University"),
         _t_entry("Exampleton Prize for Teaching, Example Society")], _taxonomy())

    assert [e["taxonomy_code"] for e in updated] == ["T", "T", "H"]
    assert updated[0]["classification_reasoning"] == (
        "[T-validation: H refused, no content words, kept T] Date fragment")
    assert updated[1]["classification_reasoning"].startswith(
        "[T-validation: S1 refused, reasoning calls it a fragment or header, kept T]")
    assert updated[0]["t_validation_applied"] is True
    assert stats["t_entries_reclassified"] == 1


def test_llm_outage_propagates_instead_of_returning_entries_unchanged(monkeypatch):
    """A provider outage past the budget fails the run (#810); only other
    errors fall back to returning the entries unchanged."""
    from unified_pipeline.llm.retry import LLMOutageError

    def outage(**kw):
        raise LLMOutageError("provider down", seconds_waited=1800.0)

    monkeypatch.setattr(classify, "call_llm", outage)
    with pytest.raises(LLMOutageError):
        classify.validate_t_classifications([_t_entry()], _taxonomy())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
