"""
Tests for LLM type-drift hardening across stages 3b and 5b.

Stage 3b classifies entries with an unstructured LLM call (json_object, no
schema), so a confidence can come back as a stringified number and a code as
null. Stage 4's owner-location inference stores primary_location raw, so it can
come back as a non-dict. These all crash downstream string/number/dict
consumers. These tests pin the defensive contracts.

Fast unit tests -- no LLM calls, no DB, no sample CV.
"""
import sys
from pathlib import Path

# tests/ -> backend/ -> web_interface/ -> project_root/ -> src/
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from unified_pipeline.stage_3b_entry_classifier import (
    _safe_float,
    detect_duplicates,
)
from unified_pipeline.stage_5b_institution_enrichment import _build_owner_context


# ---- A: confidence coercion (_safe_float) ----

def test_safe_float_parses_stringified_number():
    assert _safe_float("0.65", 0.5) == 0.65
    # The actual crash site: "0.65" < 0.7 used to raise TypeError(str < float)
    assert _safe_float("0.65", 1.0) < 0.7


def test_safe_float_passes_through_numbers():
    assert _safe_float(0.4, 0.5) == 0.4
    assert _safe_float(1, 0.5) == 1.0


def test_safe_float_falls_back_on_non_numeric():
    assert _safe_float("high", 0.5) == 0.5
    assert _safe_float(None, 0.5) == 0.5
    assert _safe_float([], 0.5) == 0.5
    # bool is not treated as numeric (True would otherwise become 1.0)
    assert _safe_float(True, 0.5) == 0.5


# ---- B: None taxonomy_code no longer crashes the duplicate detector ----

def test_detect_duplicates_tolerates_none_taxonomy_code():
    # Two near-identical entries so the similarity branch (which compares codes
    # via .startswith) runs; one has a null code from the LLM.
    text = "Adverse Drug Events in Pediatrics, co-investigator, 2019-2021."
    entries = [
        {"text": text, "taxonomy_code": None, "taxonomy_confidence": 0.5},
        {"text": text, "taxonomy_code": "M2A", "taxonomy_confidence": 0.9},
    ]
    deduped, dup_info = detect_duplicates(entries)
    # The point is that it does not raise AttributeError on None.startswith.
    assert isinstance(deduped, list)


# ---- C: malformed primary_location / locations no longer crash owner context ----

def test_owner_context_tolerates_string_primary_location():
    # LLM returned a bare string instead of the instructed object.
    result = _build_owner_context({
        "inference_success": True,
        "primary_location": "New York City",
        "locations": "not-a-list",
    })
    assert isinstance(result, str)  # no AttributeError on str.get / str slicing


def test_owner_context_skips_non_dict_location_items():
    result = _build_owner_context({
        "inference_success": True,
        "primary_location": {"institution": "WCM", "city": "NYC", "state": "NY"},
        "locations": [{"city": "NYC"}, "garbage", {"city": "Boston", "state": "MA"}],
    })
    assert "WCM" in result
    assert "Boston, MA" in result  # the valid trailing dict still contributes


def test_owner_context_well_formed_input_unchanged():
    result = _build_owner_context({
        "inference_success": True,
        "primary_location": {"institution": "WCM", "city": "NYC", "state": "NY"},
        "locations": [{"city": "NYC", "state": "NY"}],
    })
    assert "Currently at WCM" in result
