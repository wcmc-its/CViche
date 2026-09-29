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


def test_is_likely_internal_unit_standalone_institutes_are_not_internal_units():
    # #678: standalone institutes whose own name is an "Institute for X"
    # pattern and mentions no parent institution.
    for name in [
        "Institute for Advanced Study",
        "institute for advanced study",
        "Institute for Advanced Study  ",
        "Institute for Advanced Study , Princeton",
        "Institute for Advanced Study, Princeton, NJ",
        "Institute for Systems Biology",
        "Institute for Health Metrics and Evaluation",
        "Institute for Defense Analyses",
    ]:
        assert not is_likely_internal_unit(name), f"expected standalone: {name!r}"


def test_is_likely_internal_unit_allowlist_is_exact_not_a_prefix_match():
    # The allowlist must not widen into "any institute": a sibling name that
    # only shares the first words with an allowlisted one stays internal.
    for name in [
        "Institute for Advanced Study of Aging",
        "Institute for Cancer Research",
        "Institute for Systems Biology Core Facility",
    ]:
        assert is_likely_internal_unit(name), f"expected internal unit: {name!r}"


def test_format_location_us_drops_country_and_abbreviates_state():
    assert format_location("New York", "New York", "United States", "US") == "New York, NY"


def test_format_location_country_code_is_case_insensitive():
    assert format_location("New York", "New York", "United States", "us") == "New York, NY"


def test_format_location_non_us_keeps_country():
    assert format_location("Toronto", "Ontario", "Canada", "CA") == "Toronto, Ontario, Canada"
