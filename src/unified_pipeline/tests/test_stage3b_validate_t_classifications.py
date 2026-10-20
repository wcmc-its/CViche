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


# ---------------------------------------------------------------------------
# Notes are never offered for reclassification (#312, EBYSBC E11)
# ---------------------------------------------------------------------------

def _indexed(idx, text, code="T", **extra):
    return {"element_idx_start": idx, "text": text, "hierarchy": ["OTHER"], "taxonomy_code": code, **extra}


def _reclassify_everything_to_h(monkeypatch, seen):
    def fake(**kw):
        seen.append(kw["messages"][1]["content"])
        return _llm_response(None) | {"content": json.dumps([
            {"entry_index": i, "new_code": "H", "confidence": 0.9, "reasoning": "honor"} for i in range(10)
        ])}
    monkeypatch.setattr(classify, "call_llm", fake)


def test_bare_url_pointer_line_and_running_header_stay_t_unasked(monkeypatch):
    entries = [
        _indexed(0, "Jane Example, MD", code="A"),
        _indexed(5, "http://example.org/program.html"),
        _indexed(6, "Mentoring Prizes - see section 7"),
        _indexed(7, "Jane Example, MD"),
        _indexed(8, "2031 Example Prize for teaching"),
    ]
    seen = []
    _reclassify_everything_to_h(monkeypatch, seen)

    updated, stats = classify.validate_t_classifications(entries, _taxonomy(("A", "H", "T")))

    assert stats["t_entries_reviewed"] == 1
    assert [e["taxonomy_code"] for e in updated] == ["A", "T", "T", "T", "H"]
    assert "example.org" not in seen[0] and "see section" not in seen[0]


def test_a_t_set_by_the_header_pin_is_not_offered(monkeypatch):
    entries = [_indexed(0, "Opening line", code="A"),
               _indexed(3, "Wrote an invited example article (see publication #12).", pre_pin_code="S2")]
    monkeypatch.setattr(classify, "call_llm", lambda **kw: pytest.fail("no T entry should reach the LLM"))

    updated, stats = classify.validate_t_classifications(entries, _taxonomy(("A", "H", "T", "S2")))

    assert stats["t_entries_reviewed"] == 0
    assert updated[1]["taxonomy_code"] == "T"


def test_the_opening_line_itself_is_still_reviewed(monkeypatch):
    entries = [_indexed(0, "Jane Example, MD"), _indexed(4, "2031 Example Prize")]
    seen = []
    _reclassify_everything_to_h(monkeypatch, seen)

    _updated, stats = classify.validate_t_classifications(entries, _taxonomy(("H", "T")))

    assert stats["t_entries_reviewed"] == 2


def test_running_header_is_compared_with_the_lowest_indexed_line_not_the_first_listed(monkeypatch):
    # The list is out of document order: the opening line is element 0, listed second.
    entries = [
        _indexed(9, "Jane Example, MD"),
        _indexed(0, "Jane Example, MD", code="A"),
        _indexed(4, "2031 Example Prize"),
    ]
    seen = []
    _reclassify_everything_to_h(monkeypatch, seen)

    updated, stats = classify.validate_t_classifications(entries, _taxonomy(("A", "H", "T")))

    assert stats["t_entries_reviewed"] == 1
    assert [e["taxonomy_code"] for e in updated] == ["T", "A", "H"]


# ---------------------------------------------------------------------------
# A mentee row's unnumbered rest joins its numbered row (#1702)
# ---------------------------------------------------------------------------

_MENTEE_ROW = "30.Jane Example,\tMD\t2031-present\tAttending physician,\tExample Clinic"
_ROW_REST = "Example Consortium Rowan Placeholder\tScholar Award\tAttending physician,\tExample Clinic"
_FRAGMENT_REASONING = "Fragment describing a mentee's award and current position, fits mentees"


def _mentee_list(row_text=_MENTEE_ROW, rest_text=_ROW_REST, row_code="N3A", row_hierarchy=("Mentees",)):
    row = {"text": row_text, "hierarchy": list(row_hierarchy), "taxonomy_code": row_code}
    rest = {"text": rest_text, "hierarchy": ["Mentees"], "taxonomy_code": "T"}
    return [row, rest]


def _recode_rest(monkeypatch, new_code="N3B", reasoning=_FRAGMENT_REASONING):
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {"content": json.dumps([
        {"entry_index": 1, "new_code": new_code, "confidence": 0.8, "reasoning": reasoning}])})


def _validate(entries):
    return classify.validate_t_classifications(entries, _taxonomy(("H", "T", "N3", "N3A", "N3B", "N4")))


def test_mentee_row_rest_called_a_fragment_joins_its_numbered_row(monkeypatch):
    _recode_rest(monkeypatch)
    next_row = {"text": "2031 Example Teaching Award", "hierarchy": ["Mentees"], "taxonomy_code": "H"}

    updated, stats = _validate([*_mentee_list(), next_row])

    rest = updated[1]
    assert rest["is_fragment"] is True and rest["fragment_merged"] is True
    assert rest["fragment_of"] == 0
    assert rest["fragment_reasoning"] == (
        f"[T-validation: continues the numbered row before it] {_FRAGMENT_REASONING}")
    assert rest["taxonomy_code"] == "N3B"
    assert updated[0]["text"] == f"{_MENTEE_ROW} {_ROW_REST}"
    assert updated[2]["text"] == next_row["text"]
    assert stats["t_entries_reclassified"] == 1


def test_fragmentary_counts_as_the_reasoning_calling_it_a_fragment(monkeypatch):
    _recode_rest(monkeypatch, reasoning="Fragmentary line: a mentee's award and position")

    updated, _ = _validate(_mentee_list())

    assert updated[1].get("is_fragment") is True


def test_fragment_anywhere_in_the_reasoning_counts(monkeypatch):
    _recode_rest(monkeypatch, reasoning="Award and position continuing mentee row 30, a fragment")

    updated, _ = _validate(_mentee_list())

    assert updated[1].get("is_fragment") is True


@pytest.mark.parametrize("rest_text", [
    "Example Scholar Award (2016-2020)\tAttending physician",
    "4, Example Lane\tAttending physician",
], ids=["numbered-span-later", "street-number"])
def test_a_rest_with_digits_but_no_row_number_still_joins_the_row(monkeypatch, rest_text):
    _recode_rest(monkeypatch)

    updated, _ = _validate(_mentee_list(rest_text=rest_text))

    assert updated[1].get("is_fragment") is True
    assert updated[0]["text"] == f"{_MENTEE_ROW} {rest_text}"


@pytest.mark.parametrize("row_text", [
    "7) Jane Example, PhD",
    "  12. Jane Example, PhD",
    "101. Jane Example, PhD",
    "\t12. Jane Example, PhD",
])
def test_each_row_number_shape_takes_the_rest(monkeypatch, row_text):
    _recode_rest(monkeypatch)

    updated, _ = _validate(_mentee_list(row_text=row_text))

    assert updated[1].get("is_fragment") is True
    assert updated[0]["text"].endswith(_ROW_REST)


@pytest.mark.parametrize("row_code, new_code", [("N3A", "N3A"), ("N3B", "N3B"), ("N3", "N3"), ("N3B", "N3A")])
def test_every_mentee_code_pairing_folds(monkeypatch, row_code, new_code):
    _recode_rest(monkeypatch, new_code=new_code)

    updated, _ = _validate(_mentee_list(row_code=row_code))

    assert updated[1].get("is_fragment") is True
    assert updated[0]["text"].endswith(_ROW_REST)


def test_a_rest_under_another_sub_list_stays_its_own_entry(monkeypatch):
    _recode_rest(monkeypatch)
    entries = _mentee_list(row_hierarchy=("Mentees", "Postdoctoral fellows"))
    entries[1]["hierarchy"] = ["Mentees", "Graduate students"]

    updated, _ = _validate(entries)

    assert "is_fragment" not in updated[1]
    assert updated[0]["text"] == _MENTEE_ROW


@pytest.mark.parametrize("case", [
    {"reasoning": "Describes a mentee's award and current position"},
    {"reasoning": "Fragile X research of a mentee, award and position"},
    {"reasoning": "Continuation piece of the previous mentee row"},
    {"new_code": "H"},
    {"new_code": "N4"},
    {"row_code": "H"},
    {"row_code": None},
    {"row_hierarchy": ("Other mentees",)},
    {"row_text": "Jane Example, MD\t2031-present"},
    {"row_text": "2031. Jane Example, MD"},
    {"row_text": "10:30 Jane Example, MD"},
    {"row_text": ") Jane Example, MD"},
    {"row_text": "Dr. Jane Example, MD"},
    {"row_text": None},
    {"rest_text": "31.Rowan Placeholder\tMS\t2031-present"},
], ids=["no-fragment-word", "frag-without-fragment", "piece-not-fragment", "not-a-mentee-code", "other-n-code", "row-not-mentee", "row-without-code",
        "other-hierarchy", "row-unnumbered", "row-opens-with-a-year", "row-opens-with-a-time",
        "row-opens-with-punctuation", "row-opens-with-a-title",
        "row-without-text", "rest-numbered"])
def test_a_recode_outside_the_numbered_row_shape_stays_its_own_entry(monkeypatch, case):
    _recode_rest(monkeypatch, **{k: case[k] for k in ("new_code", "reasoning") if k in case})
    entries = _mentee_list(**{k: case[k] for k in ("row_text", "rest_text", "row_code", "row_hierarchy")
                              if k in case})
    row_text = entries[0]["text"]

    updated, _ = _validate(entries)

    assert "is_fragment" not in updated[1]
    assert updated[1]["taxonomy_code"] == case.get("new_code", "N3B")
    assert updated[0]["text"] == row_text


def test_a_rest_the_merge_refuses_keeps_its_recode_untagged(monkeypatch):
    """An organisation-only line is a sub-heading as often as a continuation,
    so `merge_into_parent` refuses it; tagged but unmerged, its text would be
    dropped by stage 4 (#1365 R2), so the tags come off again."""
    _recode_rest(monkeypatch)

    updated, _ = _validate(_mentee_list(rest_text="Example Children's Hospital"))

    assert {"is_fragment", "fragment_of", "fragment_reasoning"}.isdisjoint(updated[1])
    assert updated[1]["taxonomy_code"] == "N3B"
    assert updated[0]["text"] == _MENTEE_ROW


def test_the_first_entry_never_joins_the_last(monkeypatch):
    rest = {"text": _ROW_REST, "hierarchy": ["Mentees"], "taxonomy_code": "T"}
    row = {"text": _MENTEE_ROW, "hierarchy": ["Mentees"], "taxonomy_code": "N3A"}
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {"content": json.dumps([
        {"entry_index": 0, "new_code": "N3B", "confidence": 0.8, "reasoning": _FRAGMENT_REASONING}])})

    updated, _ = _validate([rest, row])

    assert "is_fragment" not in updated[0]
    assert updated[1]["text"] == _MENTEE_ROW


def test_a_rest_without_text_joins_without_crashing(monkeypatch):
    _recode_rest(monkeypatch)
    entries = _mentee_list()
    del entries[1]["text"]

    updated, _ = _validate(entries)

    assert updated[1].get("is_fragment") is True
    assert updated[0]["text"].strip() == _MENTEE_ROW


# ---------------------------------------------------------------------------
# A line between mentee rows n and n + 1 joins row n (#1702: YIXQFB 2413, 2564)
# ---------------------------------------------------------------------------

_ROW_13 = "13. Jane Example, MD"
_ROW_13_POSITION = "Attending\tPhysician, Example\tChildren's Hosp."
_ROW_14 = "14. Rowan Placeholder, MD"


def _mentee(text, code="N3B", hierarchy=("Mentees",)):
    return {"text": text, "hierarchy": list(hierarchy), "taxonomy_code": code}


def _recode(monkeypatch, entry_index, reasoning=_FRAGMENT_REASONING):
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {"content": json.dumps([
        {"entry_index": entry_index, "new_code": "N3B", "confidence": 0.8, "reasoning": reasoning}])})


def _row_13_split(next_row=_ROW_14, award_code="N4", award_hierarchy=("Mentees",), row_code="N3B"):
    """YIXQFB's row 13 as stage 2 cut it: the name, the dates and site, an
    award line coded N4, then the current position (entry 3, still T)."""
    return [
        _mentee(_ROW_13, row_code),
        _mentee("2010-2020\tPediatric GI Fellow,\tExample School of Medicine"),
        _mentee("2011-Example travel\taward", award_code, award_hierarchy),
        _mentee(_ROW_13_POSITION, "T"),
        _mentee(next_row, "N3A"),
    ]


def test_a_rest_past_an_award_line_joins_the_row_it_sits_inside(monkeypatch):
    _recode(monkeypatch, 3)
    entries = _row_13_split()
    between = [dict(e) for e in entries[1:3]]

    updated, _ = _validate(entries)

    assert updated[3]["is_fragment"] is True and updated[3]["fragment_merged"] is True
    assert updated[3]["fragment_of"] == 0
    assert updated[0]["text"] == f"{_ROW_13} {_ROW_13_POSITION}"
    assert updated[1:3] == between
    assert updated[4]["text"] == _ROW_14


@pytest.mark.parametrize("case", [
    {"next_row": "15. Rowan Placeholder, MD"},
    {"next_row": "1. Rowan Placeholder, MD"},
    {"next_row": "Rowan Placeholder, MD"},
    {"award_hierarchy": ("Awards",)},
    {"row_code": "H"},
], ids=["next-row-skips-a-number", "next-list-restarts", "no-next-row-number",
        "another-hierarchy-between", "row-not-mentee"])
def test_a_rest_not_inside_consecutive_mentee_rows_stays_its_own_entry(monkeypatch, case):
    _recode(monkeypatch, 3)

    updated, _ = _validate(_row_13_split(**case))

    assert "is_fragment" not in updated[3]
    assert updated[3]["taxonomy_code"] == "N3B"
    assert updated[0]["text"] == _ROW_13


def test_an_organisation_line_between_two_rows_joins_the_first(monkeypatch):
    """YIXQFB 2564: row 27's employer wraps onto its own line before row 28.
    Between consecutive rows it is no sub-heading, so the guard is waived."""
    _recode(monkeypatch, 1)
    row_27 = "27. Jane Example,\tMD\tAssistant Prof\tExample State Medical\tSchool"
    entries = [_mentee(row_27), _mentee("Example Children's Hospital", "T"),
               _mentee("28. Rowan Placeholder,\tPhD", "N3A")]

    updated, _ = _validate(entries)

    assert updated[1]["is_fragment"] is True and updated[1]["fragment_of"] == 0
    assert updated[0]["text"] == f"{row_27} Example Children's Hospital"


def test_the_waiver_keeps_the_other_merge_guards(monkeypatch):
    _recode(monkeypatch, 1)
    row = "27. Jane Example, MD\tCurrent position: Instructor, Example College"
    entries = [_mentee(row), _mentee("Current position: Fellow, Example Hospital", "T"),
               _mentee("28. Rowan Placeholder, PhD", "N3A")]

    updated, _ = _validate(entries)

    assert {"is_fragment", "fragment_of", "fragment_reasoning"}.isdisjoint(updated[1])
    assert updated[0]["text"] == row
