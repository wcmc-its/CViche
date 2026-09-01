"""Targeted tests for stage3b/classify.py functions not already covered by
test_stage_3b_batch_decomposition.py / test_stage_3b_batch_index_mapping.py /
test_stage_3b_fallback_surfacing.py (#522 review follow-up).

Two things every classification-shaped function here shares, because the
model is called with response_format={"type": "json_object"} and no schema:

- A code the model returns is untrusted input and must be checked against
  the actual taxonomy before it is persisted as a real classification (a
  hallucinated/typo'd code must fall back, never pass through).
- A confidence the model returns must be clamped to [0.0, 1.0], not merely
  coerced to *some* float.

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
from unified_pipeline.stage3b.context import TaxonomyContext  # noqa: E402


def _taxonomy(codes=("H", "T", "S1", "M1", "M2A")):
    return {"codes": [{"code": c, "label": c} for c in codes]}


def _context(codes=("H",)):
    return TaxonomyContext(meta_section={
        "title": "SECTION",
        "taxonomy_options": [{"code": c, "confidence": 0.9} for c in codes],
    })


def _entry(text, hierarchy=("HONORS AND AWARDS",)):
    return {"text": text, "hierarchy": list(hierarchy)}


def _llm_response(classifications, **extra):
    body = {
        "content": json.dumps({"classifications": classifications}),
        "prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001,
        "model": "test-model",
    }
    body.update(extra)
    return body


# ---------------------------------------------------------------------------
# _build_taxonomy_ref_for_batch
# ---------------------------------------------------------------------------

def test_no_suggested_codes_uses_full_taxonomy():
    """No hierarchy suggestions -> every code in the taxonomy is rendered,
    with no context_codes to give disambiguation notes to."""
    codes, ref = classify._build_taxonomy_ref_for_batch(TaxonomyContext(), _taxonomy())
    assert codes == []
    for c in ("H", "T", "S1", "M1", "M2A"):
        assert f"{c}:" in ref


def test_one_family_five_or_fewer_uses_filtered_taxonomy():
    """A single suggested family (<=5 families total) filters the rendered
    taxonomy down to that family plus the always-included H/T."""
    codes, ref = classify._build_taxonomy_ref_for_batch(
        _context(["S1"]), _taxonomy(("H", "T", "S1", "M1", "M2A"))
    )
    assert codes == ["S1"]
    assert "S1:" in ref and "H:" in ref and "T:" in ref
    assert "M1:" not in ref and "M2A:" not in ref


def test_more_than_five_families_uses_full_taxonomy():
    """Six distinct suggested families crosses the "<=5" cutoff, so the
    filter is skipped entirely and every code is rendered."""
    six_families = ["S1", "H", "M1", "D1", "Q1", "T1"]
    codes, ref = classify._build_taxonomy_ref_for_batch(
        _context(six_families), _taxonomy(("H", "T", "S1", "M1", "D1", "Q1", "T1", "M2A"))
    )
    assert set(codes) == set(six_families)
    for c in ("H", "T", "S1", "M1", "D1", "Q1", "T1", "M2A"):
        assert f"{c}:" in ref


def test_suggested_codes_are_passed_as_context_codes_for_disambiguation():
    """Suggested codes get full disambiguation notes (WATCH OUT/KEY RULES),
    not just a bare listing."""
    taxonomy = {"codes": [
        {"code": "S1", "label": "Original research", "common_confusions": ["Confused with S2"]},
        {"code": "H", "label": "Honors"},
        {"code": "T", "label": "Other"},
    ]}
    _, ref = classify._build_taxonomy_ref_for_batch(_context(["S1"]), taxonomy)
    assert "WATCH OUT" in ref
    assert "Confused with S2" in ref


def test_empty_taxonomy_does_not_crash():
    """A taxonomy with no "codes" key at all renders an empty reference
    instead of raising."""
    codes, ref = classify._build_taxonomy_ref_for_batch(_context(["S1"]), {})
    assert codes == ["S1"]
    assert ref == ""


def test_taxonomy_context_raising_propagates():
    """This call has no try/except around it -- a broken TaxonomyContext
    must fail loudly here, not be silently swallowed into an empty result."""
    class _BrokenContext:
        def get_all_suggested_codes(self):
            raise RuntimeError("context lookup exploded")

    with pytest.raises(RuntimeError, match="context lookup exploded"):
        classify._build_taxonomy_ref_for_batch(_BrokenContext(), _taxonomy())


def test_malformed_taxonomy_entries_are_skipped_not_a_crash():
    """A non-empty taxonomy with corrupt entries (missing "code", missing
    "label", non-string "code") must not crash _build_taxonomy_ref_for_batch
    itself, not just its delegate: build_taxonomy_codes_for_prompt skips and
    logs each malformed entry (see its docstring), and this drives that same
    malformed taxonomy through _build_taxonomy_ref_for_batch rather than
    calling the delegate directly (test_stage3b_taxonomy_render.py already
    covers the delegate in isolation)."""
    taxonomy = {"codes": [
        {"label": "No code"},  # missing "code"
        {"code": "S1"},  # missing "label"
        {"code": 123, "label": "Non-string code"},
        {"code": "H", "label": "Honors"},
    ]}
    codes, ref = classify._build_taxonomy_ref_for_batch(_context(["H"]), taxonomy)
    assert codes == ["H"]
    assert ref == "H: Honors"


def test_context_returns_malformed_codes_does_not_crash():
    """get_all_suggested_codes() returning malformed entries (empty string,
    a non-string) alongside a valid code must not crash on `c[0]`
    (IndexError for "", TypeError for 123) -- those entries are filtered
    before the family lookup, and the function still returns a usable
    taxonomy_ref built from the surviving valid code."""
    class _MalformedCodesContext:
        def get_all_suggested_codes(self):
            return ["", 123, "S1"]

    codes, ref = classify._build_taxonomy_ref_for_batch(
        _MalformedCodesContext(), _taxonomy(("H", "T", "S1"))
    )
    assert codes == ["S1"]
    assert "S1:" in ref


# ---------------------------------------------------------------------------
# classify_entries_batch / _classify_one_batch -- taxonomy + confidence
# validation on LLM output (untrusted input)
# ---------------------------------------------------------------------------

def test_unknown_taxonomy_code_falls_back_instead_of_persisting(monkeypatch, caplog):
    """The LLM answering with a code that doesn't exist in the taxonomy must
    not be persisted as a real classification -- it's a hallucination."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        [{"index": 0, "code": "ZZ99", "confidence": 0.9}]
    ))

    with caplog.at_level(logging.WARNING, logger=classify.logger.name):
        results, stats = classify.classify_entries_batch(
            [_entry("Dean's Award, 2015")], _context(["H"]), _taxonomy()
        )

    assert results[0]["taxonomy_code"] == "H"  # falls back to the suggested code, not ZZ99
    # Tagged distinctly from a clean "llm" classification -- CODING_STANDARDS.md
    # #5.3/#5.10: a rejected/degraded result must be visible in the artifact
    # itself, not only in a log line the artifact's own consumer never reads.
    assert results[0]["classification_source"] == "llm_invalid_code"
    assert stats["invalid_code_entries"] == 1
    assert stats["llm_classified"] == 0
    assert any("unknown taxonomy code" in r.getMessage() for r in caplog.records)


def test_confidence_above_one_clamps_to_default(monkeypatch):
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        [{"index": 0, "code": "S1", "confidence": 5.0}]
    ))
    results, _ = classify.classify_entries_batch(
        [_entry("A publication")], _context(["S1"]), _taxonomy()
    )
    assert results[0]["taxonomy_confidence"] == 0.5


def test_confidence_below_zero_clamps_to_default(monkeypatch):
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        [{"index": 0, "code": "S1", "confidence": -0.2}]
    ))
    results, _ = classify.classify_entries_batch(
        [_entry("A publication")], _context(["S1"]), _taxonomy()
    )
    assert results[0]["taxonomy_confidence"] == 0.5


def test_string_confidence_converts_correctly(monkeypatch):
    """The LLM call uses response_format={"type": "json_object"} with no
    schema, so a numeric confidence can come back stringified. _safe_float
    must parse it, not just accept values that are already float/int."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        [{"index": 0, "code": "S1", "confidence": "0.85"}]
    ))
    results, _ = classify.classify_entries_batch(
        [_entry("A publication")], _context(["S1"]), _taxonomy()
    )
    assert results[0]["taxonomy_confidence"] == 0.85
    assert results[0]["classification_source"] == "llm"


def test_confidence_non_numeric_falls_back_to_default(monkeypatch):
    """Distinct from test_confidence_above_one/below_zero_clamps_to_default,
    which use in-range-type floats merely out of the [0, 1] bound: this
    supplies a confidence _safe_float cannot parse into a float AT ALL, and
    must still fall back to the default rather than raising or persisting
    garbage."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        [{"index": 0, "code": "S1", "confidence": "high"}]
    ))
    results, _ = classify.classify_entries_batch(
        [_entry("A publication")], _context(["S1"]), _taxonomy()
    )
    assert results[0]["taxonomy_confidence"] == 0.5
    assert results[0]["classification_source"] == "llm"


def test_missing_confidence_key_gets_default(monkeypatch):
    """No "confidence" key at all (as opposed to a present-but-invalid one)
    must default the same way."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        [{"index": 0, "code": "S1"}]
    ))
    results, _ = classify.classify_entries_batch(
        [_entry("A publication")], _context(["S1"]), _taxonomy()
    )
    assert results[0]["taxonomy_confidence"] == 0.5
    assert results[0]["classification_source"] == "llm"


def test_missing_code_key_falls_back_and_is_tagged_invalid(monkeypatch):
    """A classification object with "index" present but "code" absent must
    coalesce to the suggested fallback code -- and, since the LLM did not
    actually supply a usable code, must be tagged distinctly from a clean
    classification rather than persisted as classification_source "llm"
    (CODING_STANDARDS.md #5.3/#5.10: a degraded result has to be visible in
    the artifact itself, not only in a log line)."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        [{"index": 0, "confidence": 0.9}]
    ))
    results, stats = classify.classify_entries_batch(
        [_entry("A publication")], _context(["S1"]), _taxonomy()
    )
    assert results[0]["taxonomy_code"] == "S1"
    assert results[0]["classification_source"] == "llm_invalid_code"
    assert stats["invalid_code_entries"] == 1
    assert stats["llm_classified"] == 0


def test_entry_with_non_string_text_degrades_instead_of_crashing(monkeypatch):
    """An entry whose "text" field is present but non-string (e.g. an
    explicit None) must not crash _classify_one_batch's bare .strip() calls
    (classify.py's _entry_text guard) -- it degrades to the same
    "empty_entry" treatment a genuinely blank/absent text already gets, and
    the rest of the batch classifies normally instead of the whole batch
    crashing over one malformed entry."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        [{"index": 1, "code": "H", "confidence": 0.9}]
    ))
    entries = [
        {"text": None, "hierarchy": ["HONORS AND AWARDS"]},
        _entry("Dean's Award, 2015"),
    ]
    results, _ = classify.classify_entries_batch(entries, _context(["H"]), _taxonomy())

    assert results[0]["classification_source"] == "empty_entry"
    assert results[1]["classification_source"] == "llm"
    assert results[1]["taxonomy_code"] == "H"


def test_batch_size_larger_than_entries_still_classifies_everything(monkeypatch):
    """A single oversized batch is just one batch, not an error."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        [{"index": 0, "code": "H", "confidence": 0.9}, {"index": 1, "code": "H", "confidence": 0.8}]
    ))
    results, stats = classify.classify_entries_batch(
        [_entry("Award A"), _entry("Award B")], _context(["H"]), _taxonomy(), batch_size=1000
    )
    assert stats["llm_batches"] == 1
    assert [r["taxonomy_code"] for r in results] == ["H", "H"]


# ---------------------------------------------------------------------------
# group_entries_by_hierarchy
# ---------------------------------------------------------------------------

def test_one_hierarchy_produces_a_single_group():
    """Every entry sharing exactly ONE non-empty hierarchy path, isolated
    from test_multiple_hierarchies_produce_separate_groups_and_same_hierarchy_merges
    below, whose fixture deliberately uses TWO hierarchy values (that's how
    it also proves "same hierarchy merges") and so never puts the function
    through an input where only one group is ever formed."""
    entries = [_entry("A", ["HONORS"]), _entry("B", ["HONORS"])]
    groups = classify.group_entries_by_hierarchy(entries)
    assert list(groups.keys()) == ["HONORS"]
    assert len(groups["HONORS"]) == 2


def test_multiple_hierarchies_produce_separate_groups_and_same_hierarchy_merges():
    entries = [
        _entry("A", ["HONORS"]),
        _entry("B", ["SERVICE"]),
        _entry("C", ["HONORS"]),
    ]
    groups = classify.group_entries_by_hierarchy(entries)
    assert set(groups.keys()) == {"HONORS", "SERVICE"}
    assert len(groups["HONORS"]) == 2
    assert len(groups["SERVICE"]) == 1


def test_empty_hierarchy_groups_under_no_hierarchy_key():
    groups = classify.group_entries_by_hierarchy([_entry("A", [])])
    assert list(groups.keys()) == ["(no hierarchy)"]


def test_hierarchy_none_does_not_crash():
    """entry.get("hierarchy", []) returns None (not the default) when the
    key is present with an explicit None value -- must not crash."""
    groups = classify.group_entries_by_hierarchy([{"text": "A", "hierarchy": None}])
    assert list(groups.keys()) == ["(no hierarchy)"]


def test_non_string_hierarchy_values_do_not_crash():
    """" > ".join() raises TypeError on a non-string element; hierarchy
    values are coerced to str rather than assumed to already be strings."""
    groups = classify.group_entries_by_hierarchy([{"text": "A", "hierarchy": [2020, "Awards"]}])
    assert "2020 > Awards" in groups


# ---------------------------------------------------------------------------
# validate_t_classifications
# ---------------------------------------------------------------------------

def _t_entry(text="Some miscellaneous item"):
    return {"text": text, "hierarchy": ["OTHER"], "taxonomy_code": "T"}


def test_t_entry_reclassified_and_input_list_not_mutated(monkeypatch):
    original = [_t_entry()]
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps([{"entry_index": 0, "new_code": "S1", "confidence": 0.85, "reasoning": "it's research"}])
    })

    updated, stats = classify.validate_t_classifications(original, _taxonomy())

    assert updated[0]["taxonomy_code"] == "S1"
    assert updated[0]["taxonomy_confidence"] == 0.85
    assert stats["t_entries_reclassified"] == 1
    # The caller's own list/dicts are untouched -- validate_t_classifications
    # returns copies, it does not mutate in place.
    assert original[0]["taxonomy_code"] == "T"
    assert updated is not original
    assert updated[0] is not original[0]


def test_t_entry_confirmed_stays_t_and_input_not_mutated(monkeypatch):
    original = [_t_entry()]
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps([{"entry_index": 0, "new_code": "T", "confidence": 0.6, "reasoning": "truly misc"}])
    })

    updated, stats = classify.validate_t_classifications(original, _taxonomy())

    assert updated[0]["taxonomy_code"] == "T"
    assert "confirmed" in updated[0]["classification_reasoning"]
    assert stats["t_entries_reclassified"] == 0
    assert original[0].get("classification_reasoning") is None


def test_wrapped_results_key_response(monkeypatch):
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps({"results": [{"entry_index": 0, "new_code": "S1", "confidence": 0.9}]})
    })
    updated, stats = classify.validate_t_classifications([_t_entry()], _taxonomy())
    assert updated[0]["taxonomy_code"] == "S1"
    assert stats["t_entries_reclassified"] == 1


def test_unrecognized_wrapper_object_is_treated_as_no_reclassifications(monkeypatch, caplog):
    """The removed "first dict value" fallback used to guess at an unexpected
    shape instead of treating it as invalid -- pin the safe behavior."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps({"totally_unexpected_key": [{"entry_index": 0, "new_code": "S1"}]})
    })

    with caplog.at_level(logging.WARNING, logger=classify.logger.name):
        updated, stats = classify.validate_t_classifications([_t_entry()], _taxonomy())

    assert updated[0]["taxonomy_code"] == "T"
    assert stats["t_entries_reclassified"] == 0
    assert any("none of results/entries/classifications" in r.getMessage() for r in caplog.records)


def test_boolean_entry_index_is_rejected(monkeypatch):
    """bool is a subclass of int -- entry_index=True must not be treated as
    a valid integer index into entries."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps([{"entry_index": True, "new_code": "S1", "confidence": 0.9}])
    })
    updated, stats = classify.validate_t_classifications([_t_entry()], _taxonomy())
    assert updated[0]["taxonomy_code"] == "T"
    assert stats["t_entries_reclassified"] == 0


def test_unknown_new_code_falls_back_to_t(monkeypatch, caplog):
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps([{"entry_index": 0, "new_code": "NOPE", "confidence": 0.9}])
    })
    with caplog.at_level(logging.WARNING, logger=classify.logger.name):
        updated, stats = classify.validate_t_classifications([_t_entry()], _taxonomy())
    assert updated[0]["taxonomy_code"] == "T"
    assert stats["t_entries_reclassified"] == 0
    assert any("unknown taxonomy" in r.getMessage() for r in caplog.records)
    # Reads as a rejected hallucination in the artifact, not as genuine
    # T-validation agreement -- the two are different outcomes.
    assert "unknown code rejected" in updated[0]["classification_reasoning"]
    assert "confirmed" not in updated[0]["classification_reasoning"]


def test_llm_raise_leaves_entries_untouched_and_logs_via_logger(monkeypatch, caplog):
    def _boom(**kw):
        raise RuntimeError("provider outage")
    monkeypatch.setattr(classify, "call_llm", _boom)

    original = [_t_entry()]
    with caplog.at_level(logging.ERROR, logger=classify.logger.name):
        updated, stats = classify.validate_t_classifications(original, _taxonomy())

    assert updated == original
    assert stats["t_entries_reclassified"] == 0
    assert stats["cost"] == 0.0
    assert any("T-validation failed" in r.getMessage() for r in caplog.records)


def test_no_t_entries_short_circuits_without_calling_llm(monkeypatch):
    monkeypatch.setattr(classify, "call_llm", lambda **kw: (_ for _ in ()).throw(
        AssertionError("call_llm must not be invoked when there are no T entries")
    ))
    updated, stats = classify.validate_t_classifications(
        [{"text": "x", "taxonomy_code": "H"}], _taxonomy()
    )
    assert stats == {"t_entries_reviewed": 0, "t_entries_reclassified": 0, "cost": 0.0}


# ---------------------------------------------------------------------------
# reconnect_fragments
# ---------------------------------------------------------------------------

def _fragment_setup():
    """One position entry followed by an orphaned location fragment classified
    as T -- the canonical fragment shape from the docstring examples."""
    return [
        {"text": "Assistant Professor, Ohio State University", "taxonomy_code": "D1",
         "taxonomy_confidence": 0.9, "hierarchy": ["POSITIONS"]},
        {"text": "Columbus, OH", "taxonomy_code": "T", "taxonomy_confidence": 0.4,
         "hierarchy": ["POSITIONS"]},
    ]


def test_fragment_reconnected_to_previous(monkeypatch):
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps({"fragments": [{"index": 1, "belongs_to": "previous", "reasoning": "location"}]})
    })
    entries, stats = classify.reconnect_fragments(_fragment_setup())
    assert entries[1]["taxonomy_code"] == "D1"
    assert entries[1]["is_fragment"] is True
    assert stats["fragments_reconnected"] == 1


def test_fragment_confirmed_standalone_is_not_reconnected(monkeypatch):
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps({"fragments": [{"index": 1, "belongs_to": "standalone", "reasoning": "unrelated"}]})
    })
    entries, stats = classify.reconnect_fragments(_fragment_setup())
    assert entries[1]["taxonomy_code"] == "T"
    assert stats["fragments_reconnected"] == 0


def test_no_fragment_candidates_short_circuits(monkeypatch):
    monkeypatch.setattr(classify, "call_llm", lambda **kw: (_ for _ in ()).throw(
        AssertionError("call_llm must not be invoked with no fragment candidates")
    ))
    entries, stats = classify.reconnect_fragments([{"text": "x", "taxonomy_code": "H"}])
    assert stats == {"fragments_reviewed": 0, "fragments_reconnected": 0, "cost": 0.0}


def test_boolean_fragment_index_is_rejected(monkeypatch):
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(None) | {
        "content": json.dumps({"fragments": [{"index": True, "belongs_to": "previous"}]})
    })
    entries, stats = classify.reconnect_fragments(_fragment_setup())
    assert entries[1]["taxonomy_code"] == "T"
    assert stats["fragments_reconnected"] == 0


def test_llm_raise_logs_via_logger_not_print(monkeypatch, caplog):
    def _boom(**kw):
        raise RuntimeError("provider outage")
    monkeypatch.setattr(classify, "call_llm", _boom)

    with caplog.at_level(logging.ERROR, logger=classify.logger.name):
        entries, stats = classify.reconnect_fragments(_fragment_setup())

    assert stats["fragments_reconnected"] == 0
    assert any("fragment reconnection failed" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# detect_duplicates
# ---------------------------------------------------------------------------

def _dup_entry(text, code="S1"):
    return {"text": text, "taxonomy_code": code, "hierarchy": ["PUBLICATIONS"]}


def test_two_identical_entries_flagged_duplicate():
    entries = [
        _dup_entry("A randomized controlled trial of a new intervention for asthma"),
        _dup_entry("A randomized controlled trial of a new intervention for asthma"),
    ]
    updated, pairs = classify.detect_duplicates(entries)
    assert len(pairs) == 1
    assert updated[1]["is_duplicate"] is True
    assert "is_duplicate" not in updated[0]


def test_dissimilar_entries_are_not_flagged():
    entries = [
        _dup_entry("A randomized controlled trial of a new intervention for asthma"),
        _dup_entry("Editorial board member, Journal of Epidemiology, 2019 to present"),
    ]
    _, pairs = classify.detect_duplicates(entries)
    assert pairs == []


def test_short_text_excluded_from_comparison():
    """< 20 chars is excluded even if textually identical to another entry,
    so two short identical fragments must not be flagged."""
    entries = [_dup_entry("Short one"), _dup_entry("Short one")]
    _, pairs = classify.detect_duplicates(entries)
    assert pairs == []


def test_near_duplicates_with_different_first_100_chars_are_detected():
    """Regression test for the prefix-bucketing bug: two entries whose
    normalized text differs at the very start (a prepended clause) but whose
    overall content is otherwise near-identical used to land in different
    first-100-char buckets and NEVER be compared, silently missing a real
    duplicate."""
    shared_tail = "a randomized trial of a new asthma intervention in children across three centers"
    entries = [
        _dup_entry("Preliminary report: " + shared_tail),
        _dup_entry("Final results of " + shared_tail),
    ]
    # Confirm the two texts really do differ at the start (which is what put
    # them in different buckets under the old first-100-char grouping), or
    # this test would not exercise the bug it targets.
    assert entries[0]["text"][:20] != entries[1]["text"][:20]

    _, pairs = classify.detect_duplicates(entries, similarity_threshold=0.85)
    assert len(pairs) == 1, "near-duplicates with different prefixes must still be compared"


def test_three_identical_entries_produce_two_pairs_and_keep_one_original():
    text = "A randomized controlled trial of a new intervention for pediatric asthma"
    entries = [_dup_entry(text), _dup_entry(text), _dup_entry(text)]
    updated, pairs = classify.detect_duplicates(entries)

    assert len(pairs) == 2
    assert updated[0].get("is_duplicate") is not True  # the original survives
    assert updated[1]["is_duplicate"] is True
    assert updated[2]["is_duplicate"] is True


def test_m2_preferred_over_t_when_marking_the_duplicate():
    text = "Funded research grant description that is long enough to compare properly here"
    entries = [_dup_entry(text, code="T"), _dup_entry(text, code="M2A")]
    updated, _ = classify.detect_duplicates(entries)
    # The T-coded entry is the worse classification -- it gets marked as the
    # duplicate, leaving the better-classified M2A entry as the survivor.
    assert updated[0].get("is_duplicate") is True
    assert "is_duplicate" not in updated[1]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
