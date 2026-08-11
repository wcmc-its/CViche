"""Tests for INSTITUTION_CACHE key scoping (#582).

INSTITUTION_CACHE is keyed on institution name alone, but the LLM prompt
disambiguates using the CV owner's location history -- a name-only key let
one owner's answer for an ambiguous name (e.g. "OU College of Medicine")
render into a different owner's CV. _institution_cache_key folds a hash of
the owner context into the key; these pin that directly, since it is the
exact function run_stage5b calls at the real cache lookup/write site.
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_5b_institution_enrichment import _institution_cache_key


def test_different_owner_contexts_never_collide():
    key_oklahoma = _institution_cache_key(
        "OU College of Medicine", "Currently at University of Oklahoma in Norman, OK"
    )
    key_ohio = _institution_cache_key(
        "OU College of Medicine", "Currently at Ohio University in Athens, OH"
    )
    assert key_oklahoma != key_ohio


def test_same_owner_context_still_dedupes():
    a = _institution_cache_key("Duke University Medical Center", "Currently at Duke in Durham, NC")
    b = _institution_cache_key("Duke University Medical Center", "Currently at Duke in Durham, NC")
    assert a == b


def test_missing_owner_context_is_still_a_valid_stable_key():
    # cv_owner_location can be None / inference_success False -- _build_owner_context
    # returns a fixed sentinel string in that case, which must still produce a
    # deterministic key so unrelated CVs with no context dedupe among themselves.
    a = _institution_cache_key("Mayo Clinic", "No location context available for CV owner.")
    b = _institution_cache_key("Mayo Clinic", "No location context available for CV owner.")
    assert a == b


def test_key_is_case_and_whitespace_insensitive_on_the_name_only():
    a = _institution_cache_key("Duke University", "same context")
    b = _institution_cache_key("  duke university  ", "same context")
    assert a == b


if __name__ == "__main__":
    test_different_owner_contexts_never_collide()
    test_same_owner_context_still_dedupes()
    test_missing_owner_context_is_still_a_valid_stable_key()
    test_key_is_case_and_whitespace_insensitive_on_the_name_only()
    print("OK")
