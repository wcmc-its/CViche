"""Coverage-gap tests for `stage3b/classify.py::reconnect_fragments` (PR #644
review). Split out from test_stage3b_classify.py so the reply on the PR can
point at one file per gap-closing pass without touching the tests already
there -- same pattern as test_stage3b_detect_duplicates.py.

Every case here was named by the reviewer and is either UNCOVERED or PARTIAL
per the coverage audit -- see the case-to-test table in the PR reply. Cases
already covered in test_stage3b_classify.py (no candidates, reconnect to
previous, standalone confirmed, LLM raises, boolean index) are NOT repeated
here.

The audit's key finding: test_stage3b_classify.py's one shared fixture,
_fragment_setup() ("Columbus, OH" at confidence 0.4, index 1 of 2), bakes
THREE separate trigger signals into one fixture -- is_location_only,
is_low_conf, and "sits at the last index" -- so no existing test isolates
any one of them, or the candidate-detection step itself, from the others.
Below, each detection-signal fixture holds every OTHER signal constant at a
value that would NOT trigger candidacy on its own (confidence >= 0.7,
unremarkable non-location/non-dollar/non-institution text) so that only the
signal under test can be responsible for the entry becoming a candidate.

Self-contained: call_llm stubbed at stage3b.classify (#496), no network/DB.
"""
import json
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage3b.classify as classify  # noqa: E402


def _llm_response(content_obj):
    return {
        "content": json.dumps(content_obj),
        "prompt_tokens": 10, "completion_tokens": 5, "cost": 0.001,
        "model": "test-model",
    }


def _fragment_setup():
    """Same canonical shape as test_stage3b_classify.py's _fragment_setup:
    one D1 position followed by an orphaned "Columbus, OH" fragment at
    index 1 -- i.e. also the last index. Reused here (not imported, so this
    file stays self-contained) for cases that specifically want THAT
    combination: the stats-reporting test, and the last-index "next"-guard
    test, which needs a fragment that genuinely has no next_entry."""
    return [
        {"text": "Assistant Professor, Ohio State University", "taxonomy_code": "D1",
         "taxonomy_confidence": 0.9, "hierarchy": ["POSITIONS"]},
        {"text": "Columbus, OH", "taxonomy_code": "T", "taxonomy_confidence": 0.4,
         "hierarchy": ["POSITIONS"]},
    ]


# ---------------------------------------------------------------------------
# Candidate-detection signals, isolated one at a time (classify.py:739-747)
# ---------------------------------------------------------------------------

def _location_only_high_confidence_fragment():
    """confidence 0.8 is >= the 0.7 low-confidence cutoff, so is_low_conf is
    False here -- only is_location_only can make this a candidate."""
    return [
        {"text": "Assistant Professor, Ohio State University", "taxonomy_code": "D1",
         "taxonomy_confidence": 0.9, "hierarchy": ["POSITIONS"]},
        {"text": "Columbus, OH", "taxonomy_code": "T", "taxonomy_confidence": 0.8,
         "hierarchy": ["POSITIONS"]},
    ]


def test_location_only_fragment_is_detected_and_reconnected_at_high_confidence(monkeypatch):
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        {"fragments": [{"index": 1, "belongs_to": "previous", "reasoning": "location"}]}
    ))
    entries, stats = classify.reconnect_fragments(_location_only_high_confidence_fragment())
    # fragments_reviewed proves it was picked up as a CANDIDATE in the first
    # place -- not just that a reconnection decision was later honored.
    assert stats["fragments_reviewed"] == 1
    assert entries[1]["taxonomy_code"] == "D1"
    assert stats["fragments_reconnected"] == 1


def _dollar_only_fragment():
    """confidence 0.9 keeps is_low_conf False; the text contains no
    University/College/Institute/Center/Hospital keyword and doesn't match
    the location regex -- only is_dollar_only can trigger candidacy."""
    return [
        {"text": "Principal Investigator, NIH R01 Grant Award", "taxonomy_code": "M2A",
         "taxonomy_confidence": 0.9, "hierarchy": ["GRANTS"]},
        {"text": "$1,326,480, Ohio Department of Medicaid", "taxonomy_code": "T",
         "taxonomy_confidence": 0.9, "hierarchy": ["GRANTS"]},
    ]


def test_dollar_only_fragment_is_detected_and_reconnected(monkeypatch):
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        {"fragments": [{"index": 1, "belongs_to": "previous", "reasoning": "budget line"}]}
    ))
    entries, stats = classify.reconnect_fragments(_dollar_only_fragment())
    assert stats["fragments_reviewed"] == 1
    assert entries[1]["taxonomy_code"] == "M2A"
    assert stats["fragments_reconnected"] == 1


def _institution_name_fragment():
    """confidence 0.9 keeps is_low_conf False; "Ohio State University" has
    <=5 words, contains "University", and contains none of
    Professor/Director/Chair/Fellow -- only is_institution_fragment can
    trigger candidacy."""
    return [
        {"text": "Postdoctoral Fellow, Cleveland Clinic Foundation", "taxonomy_code": "D1",
         "taxonomy_confidence": 0.9, "hierarchy": ["POSITIONS"]},
        {"text": "Ohio State University", "taxonomy_code": "T",
         "taxonomy_confidence": 0.9, "hierarchy": ["POSITIONS"]},
    ]


def test_institution_fragment_is_detected_and_reconnected(monkeypatch):
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        {"fragments": [{"index": 1, "belongs_to": "previous", "reasoning": "bare institution name"}]}
    ))
    entries, stats = classify.reconnect_fragments(_institution_name_fragment())
    assert stats["fragments_reviewed"] == 1
    assert entries[1]["taxonomy_code"] == "D1"
    assert stats["fragments_reconnected"] == 1


def _low_confidence_only_fragment():
    """confidence 0.4 is < the 0.7 cutoff; the text matches none of the
    location/dollar/institution patterns -- only is_low_conf can trigger
    candidacy."""
    return [
        {"text": "Editorial Board Member, Journal of Internal Medicine", "taxonomy_code": "D1",
         "taxonomy_confidence": 0.9, "hierarchy": ["SERVICE"]},
        {"text": "Continuing medical education credits completed", "taxonomy_code": "T",
         "taxonomy_confidence": 0.4, "hierarchy": ["SERVICE"]},
    ]


def test_low_confidence_alone_triggers_candidacy_without_pattern_match(monkeypatch):
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        {"fragments": [{"index": 1, "belongs_to": "previous", "reasoning": "low confidence"}]}
    ))
    entries, stats = classify.reconnect_fragments(_low_confidence_only_fragment())
    assert stats["fragments_reviewed"] == 1
    assert entries[1]["taxonomy_code"] == "D1"
    assert stats["fragments_reconnected"] == 1


# ---------------------------------------------------------------------------
# Position guards: first index, last index, and the "next" branch
# (classify.py:749-750 detection guard; :858-865 reconnect-to-next branch)
# ---------------------------------------------------------------------------

def _fragment_at_first_index():
    """The T fragment is entries[0] -- prev_entry can only resolve via
    `entries[i - 1] if i > 0 else None`, so this is the only fixture in the
    suite where that ternary's False branch runs."""
    return [
        {"text": "Portland, OR", "taxonomy_code": "T", "taxonomy_confidence": 0.3,
         "hierarchy": ["POSITIONS"]},
        {"text": "Clinical Instructor, Oregon Health Sciences University", "taxonomy_code": "D1",
         "taxonomy_confidence": 0.9, "hierarchy": ["POSITIONS"]},
    ]


def test_fragment_at_first_index_is_detected_despite_no_previous_entry(monkeypatch):
    """No prev_entry exists at index 0, so candidacy can only come from
    next_entry being present. A hallucinated "previous" decision at index 0
    must be rejected too (idx > 0 is False), leaving the fragment
    untouched."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        {"fragments": [{"index": 0, "belongs_to": "previous", "reasoning": "h"}]}
    ))
    entries, stats = classify.reconnect_fragments(_fragment_at_first_index())
    # Candidate detection worked with prev_entry=None -- proves the i>0
    # ternary at index 0 didn't crash or exclude the entry.
    assert stats["fragments_reviewed"] == 1
    assert stats["fragments_reconnected"] == 0
    assert entries[0]["taxonomy_code"] == "T"
    assert "is_fragment" not in entries[0]


def _fragment_with_next_entry_not_last():
    """Three entries: the T fragment sits at index 1, which has BOTH a
    prev_entry (index 0) and a next_entry (index 2), and index 1 is not
    len(entries)-1 -- so a "next" decision here exercises the reconnect-to-
    next branch without also tripping the last-index guard."""
    return [
        {"text": "Visiting Scholar, University of Michigan", "taxonomy_code": "D1",
         "taxonomy_confidence": 0.9, "hierarchy": ["POSITIONS"]},
        {"text": "Ann Arbor, MI", "taxonomy_code": "T", "taxonomy_confidence": 0.5,
         "hierarchy": ["POSITIONS"]},
        {"text": "Chair, Department of Surgery", "taxonomy_code": "D1",
         "taxonomy_confidence": 0.9, "hierarchy": ["POSITIONS"]},
    ]


def test_fragment_reconnected_to_next(monkeypatch):
    """The belongs_to == "next" branch (classify.py:858-865) has zero test
    evidence anywhere in the repo -- this pins it directly."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        {"fragments": [{"index": 1, "belongs_to": "next", "reasoning": "introduces next entry"}]}
    ))
    entries, stats = classify.reconnect_fragments(_fragment_with_next_entry_not_last())

    assert entries[1]["taxonomy_code"] == "D1"  # inherited from next_entry, not prev_entry
    assert entries[1]["fragment_of"] == 2
    assert entries[1]["is_fragment"] is True
    assert stats["fragments_reconnected"] == 1
    # Neighbors are untouched -- only the fragment entry itself mutates.
    assert "is_fragment" not in entries[0]
    assert "is_fragment" not in entries[2]


def test_fragment_at_last_index_rejects_hallucinated_next_decision(monkeypatch):
    """_fragment_setup's T fragment is at len(entries)-1: next_entry
    resolves to None at detection time, and a "next" decision from the LLM
    must be rejected by the `idx < len(entries) - 1` guard rather than
    raising or silently reconnecting to a nonexistent next entry.

    "input_tokens" in stats / "error" not in stats is the part that pins
    the GUARD specifically: entries[idx + 1] on a genuinely-last index is
    always out of range, so if the guard were simply missing this would hit
    the except block's crash-fallback (no "input_tokens", an "error" key)
    instead of finishing normally -- the same fragments_reconnected == 0 and
    unmutated entry would otherwise also be produced by that crash, so
    those two assertions alone would not tell "cleanly rejected" apart from
    "silently crashed"."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        {"fragments": [{"index": 1, "belongs_to": "next", "reasoning": "hallucinated"}]}
    ))
    entries, stats = classify.reconnect_fragments(_fragment_setup())

    assert stats["fragments_reconnected"] == 0
    assert entries[1]["taxonomy_code"] == "T"
    assert "is_fragment" not in entries[1]
    assert "input_tokens" in stats
    assert "error" not in stats


# ---------------------------------------------------------------------------
# Stats on a real (non-short-circuit) reconnection run
# ---------------------------------------------------------------------------

def test_reconnect_stats_report_reviewed_tokens_and_cost(monkeypatch):
    """test_fragment_reconnected_to_previous (test_stage3b_classify.py) only
    checks fragments_reconnected on this path -- fragments_reviewed,
    input_tokens, output_tokens, and cost are never asserted there."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        {"fragments": [{"index": 1, "belongs_to": "previous", "reasoning": "location"}]}
    ))
    _, stats = classify.reconnect_fragments(_fragment_setup())

    assert stats["fragments_reviewed"] == 1
    assert stats["fragments_reconnected"] == 1
    assert stats["input_tokens"] == 10
    assert stats["output_tokens"] == 5
    assert stats["cost"] == 0.001


# ---------------------------------------------------------------------------
# LLM-output validation: malformed JSON/decision shapes (untrusted input)
# ---------------------------------------------------------------------------

def test_invalid_json_content_falls_back_without_crashing(monkeypatch):
    """content that isn't valid JSON must raise inside json.loads and land
    in the except block's fallback stats -- not propagate."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw:
        _llm_response(None) | {"content": "not valid json {{{"})
    entries, stats = classify.reconnect_fragments(_fragment_setup())

    assert stats["fragments_reconnected"] == 0
    assert "error" in stats
    # The exception fallback dict never carries these -- their absence is
    # what distinguishes this path from a real (non-exceptional) return.
    assert "input_tokens" not in stats
    assert entries[1]["taxonomy_code"] == "T"


def test_missing_fragments_key_defaults_to_no_reconnections(monkeypatch):
    """A JSON body with no "fragments" key must hit
    result.get("fragments", []) and finish normally with zero
    reconnections -- not raise, and not be confused with the invalid-JSON
    exception path."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response({"unexpected": "shape"}))
    entries, stats = classify.reconnect_fragments(_fragment_setup())

    assert stats["fragments_reconnected"] == 0
    assert "input_tokens" in stats  # reached the real success return, not the except fallback
    assert "error" not in stats
    assert entries[1]["taxonomy_code"] == "T"


def test_non_dict_decision_in_fragments_list_is_skipped(monkeypatch):
    """A bare string inside "fragments" must be skipped by the
    `if not isinstance(decision, dict): continue` guard -- without it,
    decision.get(...) would raise and this would land in the except
    fallback instead of finishing normally."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        {"fragments": ["not-a-decision-object"]}
    ))
    entries, stats = classify.reconnect_fragments(_fragment_setup())

    assert stats["fragments_reconnected"] == 0
    assert "input_tokens" in stats
    assert "error" not in stats
    assert entries[1]["taxonomy_code"] == "T"


def test_out_of_range_index_is_rejected(monkeypatch):
    """index=999 is a genuine int (not a bool) but fails the
    `0 <= idx < len(entries)` bound -- distinct from the boolean-index case
    already covered. Must be silently skipped, not raise IndexError."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        {"fragments": [{"index": 999, "belongs_to": "previous", "reasoning": "h"}]}
    ))
    entries, stats = classify.reconnect_fragments(_fragment_setup())

    assert stats["fragments_reconnected"] == 0
    assert "input_tokens" in stats  # no IndexError -> reached the real success return
    assert "error" not in stats
    assert entries[1]["taxonomy_code"] == "T"


def test_unrecognized_belongs_to_value_is_ignored(monkeypatch):
    """A belongs_to value outside {"previous", "next", "standalone"} must
    fall through all three branches with NO mutation at all -- not even the
    fragment_reasoning annotation the "standalone" branch would add."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        {"fragments": [{"index": 1, "belongs_to": "sideways", "reasoning": "h"}]}
    ))
    entries, stats = classify.reconnect_fragments(_fragment_setup())

    assert stats["fragments_reconnected"] == 0
    assert entries[1]["taxonomy_code"] == "T"
    assert "fragment_reasoning" not in entries[1]
    assert "is_fragment" not in entries[1]


def test_non_string_reasoning_is_coerced_to_empty_string(monkeypatch):
    """reasoning=12345 (present, but not a string) must be coerced to "" by
    `if not isinstance(reasoning, str): reasoning = ""` -- distinct from
    test_boolean_fragment_index_is_rejected, which omits the "reasoning" key
    entirely (the missing-key default path, not this coercion path)."""
    monkeypatch.setattr(classify, "call_llm", lambda **kw: _llm_response(
        {"fragments": [{"index": 1, "belongs_to": "previous", "reasoning": 12345}]}
    ))
    entries, stats = classify.reconnect_fragments(_fragment_setup())

    assert entries[1]["fragment_reasoning"] == ""
    assert entries[1]["taxonomy_code"] == "D1"
    assert stats["fragments_reconnected"] == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))


def test_llm_outage_propagates_instead_of_returning_entries_unchanged(monkeypatch):
    """A provider outage past the budget fails the run (#810); only other
    errors fall back to returning the entries unchanged."""
    from unified_pipeline.llm.retry import LLMOutageError

    def outage(**kw):
        raise LLMOutageError("provider down", seconds_waited=1800.0)

    monkeypatch.setattr(classify, "call_llm", outage)
    with pytest.raises(LLMOutageError):
        classify.reconnect_fragments(_fragment_setup())
