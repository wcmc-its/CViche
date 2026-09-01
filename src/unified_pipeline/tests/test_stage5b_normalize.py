"""Regression tests for stage5b/normalize.py (#523 review follow-up)."""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage5b.normalize import (
    format_location,
    is_likely_internal_unit,
    normalize_institution_name,
)


def test_normalize_institution_name_is_idempotent():
    cases = [
        "Univ. of Michigan Med. Ctr.",
        "Dept. of Surgery, Mass Gen Hosp.",
        "Weill Cornell Medicine",
        "",
    ]
    for name in cases:
        once = normalize_institution_name(name)
        twice = normalize_institution_name(once)
        assert once == twice, f"not idempotent: {name!r} -> {once!r} -> {twice!r}"


def test_is_likely_internal_unit_positive_cases():
    for name in [
        "Department of Surgery",
        "Center for Cancer Research",
        "Centre for Global Health",
        "Office of Research Administration",
    ]:
        assert is_likely_internal_unit(name), f"expected internal unit: {name!r}"


def test_is_likely_internal_unit_negative_cases():
    # Standalone organizations and government agencies that happen to start
    # with an internal-unit prefix but name a major institution.
    for name in [
        "Harvard University",
        "Cleveland Clinic",
        "Massachusetts General Hospital",
        "Centers for Disease Control and Prevention",
    ]:
        assert not is_likely_internal_unit(name), f"expected NOT internal unit: {name!r}"


def test_is_likely_internal_unit_known_false_positive_standalone_institutes():
    # Confirmed false positive (see #523 review): a standalone institute
    # whose name doesn't mention any major_indicators gets misclassified as
    # an internal unit and silently skipped from lookup. Pre-existing
    # heuristic behavior, not something this test suite has tuned a fix for
    # yet -- pinned here so a future fix has a failing case to turn green,
    # and so this doesn't regress further. See #678.
    assert is_likely_internal_unit("Institute for Advanced Study")


def test_format_location_us_drops_country_and_abbreviates_state():
    assert format_location("New York", "New York", "United States", "US") == "New York, NY"


def test_format_location_country_code_is_case_insensitive():
    assert format_location("New York", "New York", "United States", "us") == "New York, NY"


def test_format_location_non_us_keeps_country():
    assert format_location("Toronto", "Ontario", "Canada", "CA") == "Toronto, Ontario, Canada"
