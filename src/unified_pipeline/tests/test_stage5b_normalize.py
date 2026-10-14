"""Regression tests for stage5b/normalize.py (#523 review follow-up)."""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage5b.normalize import (
    LOCATION_SOURCE_ENTRY_TEXT,
    LOCATION_SOURCE_WITHHELD,
    format_location,
    is_likely_internal_unit,
    normalize_institution_name,
    reconcile_location_with_source,
    stated_us_locations,
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


# --- reconcile_location_with_source (#1257) ----------------------------------
# The lookup never sees the entry text. Its city stands only when the entry
# does not contradict it, and is dropped for a name that identifies no one
# place or a course taken online. Institution names here are invented.

def _lookup(city, state, country="United States", code="US"):
    return {"cleaned_name": "Example Health Network", "official_name": "Example Health Network",
            "city": city, "state": state, "country": country, "country_code": code}


def test_reconcile_replaces_a_city_the_entry_text_contradicts():
    # TXTATQ D2 el 123 shape: the text's last column names a city; the
    # lookup, which never saw it, answered another city in the same state.
    out = reconcile_location_with_source(
        _lookup("Tampa", "Florida"), "Example Health Network",
        "Clinical Pharmacist 2018-2021\tExample Health Network\tOrlando, Florida")
    assert (out["city"], out["state"], out["country_code"]) == ("Orlando", "Florida", "US")
    assert out["location_source"] == LOCATION_SOURCE_ENTRY_TEXT
    assert out["llm_location"] == "Tampa, FL"


def test_reconcile_replaces_a_foreign_lookup_with_the_stated_us_city():
    out = reconcile_location_with_source(
        _lookup("Lille", "Hauts-de-France", "France", "FR"),
        "Example Institutes, Bethesda, MD", "2016-2018\tExample Institutes, Bethesda, MD")
    assert (out["city"], out["state"], out["country"]) == ("Bethesda", "Maryland", "United States")


def test_reconcile_keeps_a_city_the_entry_names():
    result = _lookup("Saint Louis", "Missouri")
    assert reconcile_location_with_source(
        result, "Example University", "MD, Example University, St. Louis, MO") is result


def test_reconcile_keeps_a_city_for_a_distinct_name_the_entry_says_nothing_about():
    # The enrichment 5b exists to make: "Example University" -> its city.
    result = _lookup("Providence", "Rhode Island")
    assert reconcile_location_with_source(
        result, "Example University", "Teacher Training\t2021 - present") is result


def test_reconcile_withholds_a_city_guessed_for_a_generic_name():
    # ZDCXIV/FLYBMX/KJJVVO el 30: "University Hospital" with no city got the
    # owner's current city.
    out = reconcile_location_with_source(
        _lookup("Gainesville", "Florida"), "University Hospital",
        "Postgraduate Year-1 Pharmacy Residency, University Hospital \u2014 06/30/2007")
    assert (out["city"], out["state"], out["country"], out["country_code"]) == ("", "", "", "")
    assert out["location_source"] == LOCATION_SOURCE_WITHHELD
    assert out["llm_location"] == "Gainesville, FL"
    assert out["cleaned_name"] == "Example Health Network"


def test_reconcile_withholds_a_city_for_a_course_taken_online():
    for institution, text in (
        ("Example Academy of Psychiatry, online", "Waiver Training\tMay 2020\tExample Academy of Psychiatry, online"),
        ("Example Center for Faculty Development", "Example Center for Faculty Development Program (online) 2021"),
        ("Example Education Center, Virtual via Zoom", "Summit, Example Education Center, Virtual via Zoom (May 2020)"),
        ("Example Society", "2017-2018 Online course, Example Society \u201cCurriculum Design\u201d"),
    ):
        out = reconcile_location_with_source(_lookup("Boston", "Massachusetts"), institution, text)
        assert out["city"] == "", institution


def test_reconcile_keeps_a_city_when_only_part_of_a_course_was_online():
    result = _lookup("Providence", "Rhode Island")
    assert reconcile_location_with_source(
        result, "Example University",
        "Teacher Training\tcoursework online and an additional 115 hours in person") is result
    assert reconcile_location_with_source(
        result, "Example School of Business",
        "flipped classroom model of online coursework followed by in-residence learning") is result


def test_reconcile_withholds_when_the_entry_states_several_other_cities():
    out = reconcile_location_with_source(
        _lookup("Boston", "Massachusetts"), "Example Hospital; Example Clinic",
        "Example Hospital, Manhasset, NY;\tExample Clinic, Albany, NY\tResident 1989-1992")
    assert out["city"] == "" and out["location_source"] == LOCATION_SOURCE_WITHHELD


def test_stated_us_locations_reads_a_segment_standing_city_and_state():
    assert stated_us_locations(
        "Example Medical School, Example City, NJ",
        "1984     MD, Example Medical School,        Piscataway, New Jersey") == [
        ("Example City", "New Jersey"), ("Piscataway", "New Jersey")]
    assert stated_us_locations("", "Attending | Example Group\nNew York, NY | 09/2022-present") == [
        ("New York", "New York")]
    assert stated_us_locations("", "Fellowship\tExample Institute\tDenver, Colorado 2006-2008") == [
        ("Denver", "Colorado")]


def test_stated_us_locations_ignores_institutions_people_and_credentials():
    for text in (
        "Associate Professor, Biology Department, Example College, MA.",   # an institution
        "Jane Q. Example, MD\tAssistant Professor of Medicine",          # a name heading the text
        "Fellow\tExample Program\tPI: Jane Example, MD",                  # not a segment of its own
        "Fellow\tExample Program\tJane Example, MD, PhD",                 # credentials follow
        "Ph.D., Psychology, Ohio State University",                        # a state starting a name
        "Fellowship, MS Society (Summer 2015)",                            # MS not ending a segment
        "Example University, NY, NY",                                       # all-caps, no city word
    ):
        assert stated_us_locations("", text) == [], text
