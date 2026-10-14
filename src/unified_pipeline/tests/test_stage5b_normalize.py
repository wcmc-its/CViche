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
    is_delivered_online,
    is_generic_institution_name,
    is_likely_internal_unit,
    names_city,
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
        "Clinical Pharmacist 2018-2021\tExample Health Network\tOrlando, Florida", "D2")
    assert (out["city"], out["state"], out["country_code"]) == ("Orlando", "Florida", "US")
    assert out["location_source"] == LOCATION_SOURCE_ENTRY_TEXT
    assert out["llm_location"] == "Tampa, FL"


def test_reconcile_replaces_a_foreign_lookup_with_the_stated_us_city():
    out = reconcile_location_with_source(
        _lookup("Lille", "Hauts-de-France", "France", "FR"),
        "Example Institutes, Bethesda, MD", "2016-2018\tExample Institutes, Bethesda, MD", "C")
    assert (out["city"], out["state"], out["country"]) == ("Bethesda", "Maryland", "United States")
    assert out["llm_location"] == "Lille, Hauts-de-France, France"


def test_reconcile_keeps_a_city_the_entry_names():
    result = _lookup("Saint Louis", "Missouri")
    assert reconcile_location_with_source(
        result, "Example University", "MD, Example University, St. Louis, MO", "B1") is result


def test_reconcile_keeps_a_city_for_a_distinct_name_the_entry_says_nothing_about():
    # The enrichment 5b exists to make: "Example University" -> its city.
    result = _lookup("Providence", "Rhode Island")
    assert reconcile_location_with_source(
        result, "Example University", "Teacher Training\t2021 - present", "B2") is result


def test_reconcile_withholds_a_city_guessed_for_a_generic_name():
    # ZDCXIV/FLYBMX/KJJVVO el 30: "University Hospital" with no city got the
    # owner's current city.
    out = reconcile_location_with_source(
        _lookup("Gainesville", "Florida"), "University Hospital",
        "Postgraduate Year-1 Pharmacy Residency, University Hospital \u2014 06/30/2007", "C")
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
        out = reconcile_location_with_source(_lookup("Boston", "Massachusetts"), institution, text, "B2")
        assert out["city"] == "", institution


def test_reconcile_keeps_a_city_when_only_part_of_a_course_was_online():
    result = _lookup("Providence", "Rhode Island")
    assert reconcile_location_with_source(
        result, "Example University",
        "Teacher Training\tcoursework online and an additional 115 hours in person", "B2") is result
    assert reconcile_location_with_source(
        result, "Example School of Business",
        "flipped classroom model of online coursework followed by in-residence learning", "B2") is result


def test_reconcile_withholds_when_the_entry_states_several_other_cities():
    out = reconcile_location_with_source(
        _lookup("Boston", "Massachusetts"), "Example Hospital; Example Clinic",
        "Example Hospital, Manhasset, NY;\tExample Clinic, Albany, NY\tResident 1989-1992", "C")
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
        "Fellow\tExample Program\tExampleton, MD, PhD",                   # ... after a one-word name
        "Ph.D., Psychology, Ohio State University",                        # a state starting a name
        "Fellowship, MS Society (Summer 2015)",                            # MS not ending a segment
        "Example University, NY, NY",                                       # all-caps, no city word
    ):
        assert stated_us_locations("", text) == [], text


def test_stated_us_locations_reads_an_institution_field_that_starts_with_the_city():
    assert stated_us_locations("Springfield, Illinois", "") == [("Springfield", "Illinois")]


def test_stated_us_locations_reads_a_state_followed_by_usa():
    assert stated_us_locations("", "Fellowship\tExample Institute\tDenver, CO USA") == [
        ("Denver", "Colorado")]


def test_stated_us_locations_counts_two_spellings_of_one_place_once():
    text = "Example Hospital, Saint Louis, Missouri; Example Clinic, St. Louis, MO"
    assert stated_us_locations("", text) == [("Saint Louis", "Missouri")]
    out = reconcile_location_with_source(_lookup("Kansas City", "Missouri"), "Example Hospital", text, "C")
    assert (out["city"], out["location_source"]) == ("Saint Louis", LOCATION_SOURCE_ENTRY_TEXT)


def test_names_city_is_false_for_a_city_with_no_words():
    assert names_city("", "-") is False


# A person's name with a degree that is also a state code ("<Name>, MD") is
# not "City, State" (web068 el 115 shape; names invented).

def test_stated_us_locations_reads_a_name_and_a_state_code_degree_as_a_person():
    for degree in ("MD", "MA"):
        for text in (
            f"Research Assistant, John Example, {degree}, Surgery Department, Example University, 2013",
            f"Mentor (Jane Q. Example, {degree})",
            f"2009 – | Jane Example, {degree} | Attending Physician",
            f"Mentors: Jane Example, MD, PhD; John Example, {degree}\tProject: Example",
        ):
            assert stated_us_locations("", text) == [], text


def test_stated_us_locations_keeps_a_one_word_city_or_a_spelled_out_state_with_a_degree_code():
    assert stated_us_locations("", "Fellow\tExample Hospital, Baltimore, MD | 2010-2012") == [
        ("Baltimore", "Maryland")]
    assert stated_us_locations("", "Fellow\tExample Hospital, Boston, MA") == [("Boston", "Massachusetts")]
    assert stated_us_locations("", "Fellow\tExample Hospital, Jackson, MS") == [("Jackson", "Mississippi")]
    assert stated_us_locations("", "Fellow\tExample Hospital, Pittsburgh, PA") == [("Pittsburgh", "Pennsylvania")]
    assert stated_us_locations("", "Fellow\tExample Institute, Silver Spring, Maryland") == [
        ("Silver Spring", "Maryland")]
    # Two words and a degree code that end the line are a place, not a name.
    assert stated_us_locations("Example Institute, Silver Spring, MD", "") == [("Silver Spring", "Maryland")]
    assert stated_us_locations("", "Fellow\tExample Clinic\tWind Gap, MD.\nAttending 2010") == [
        ("Wind Gap", "Maryland")]
    assert stated_us_locations("", "Fellow\tExample Clinic\tWind Gap, MD\nAttending 2010") == [
        ("Wind Gap", "Maryland")]
    # MS and PA are not read as degrees: no CV in the corpus needed them.
    assert stated_us_locations("", "Fellow\tWind Gap, PA | Attending") == [("Wind Gap", "Pennsylvania")]
    assert stated_us_locations("", "Fellow\tWind Gap, MS | Attending") == [("Wind Gap", "Mississippi")]
    # Two words with a non-degree code are a city as before.
    assert stated_us_locations("", "Fellow\tExample Hospital, New Haven, CT") == [("New Haven", "Connecticut")]


def test_reconcile_keeps_the_lookup_city_when_the_text_names_a_person_with_a_degree():
    result = _lookup("New Orleans", "Louisiana")
    assert reconcile_location_with_source(
        result, "Example University",
        "Research Assistant studying example cells in example patients, John Example, MD, "
        "Surgery Department, Example University, 1/2013-2/2014.", "C") is result


# "Online" puts only a course the owner took nowhere: an appointment that
# mentions an online course happened at the institution.

def test_reconcile_withholds_for_online_only_in_a_course_entry():
    for code, text in (
        ("D1", "Director, Online Courses in Example Studies, Example University, 2015-2020"),
        ("D3", "Supervisor: Jane Example | Online course development with Example University."),
        ("C", "Example Academy of Psychiatry, online"),
    ):
        result = _lookup("New York", "New York")
        assert reconcile_location_with_source(result, "Example University", text, code) is result, code
        out = reconcile_location_with_source(result, "Example University", text, "B2")
        assert out["location_source"] == LOCATION_SOURCE_WITHHELD, code


# One test per alternative, character and word the #1257 patterns keep, so
# each is pinned (every one is used by at least one CV in the corpus).

def test_stated_us_locations_reads_a_city_after_each_segment_delimiter():
    for text in (
        "Fellow, Exampleton, OH", "Fellow,Exampleton, OH", "Fellow\tExampleton, OH",
        "Fellow\nExampleton, OH", "Fellow | Exampleton, OH", "Fellow; Exampleton, OH",
        "Fellow (Exampleton, OH)",
    ):
        assert stated_us_locations("", text) == [("Exampleton", "Ohio")], text
    assert stated_us_locations("", "Fellow at Exampleton, OH") == []


def test_stated_us_locations_city_words():
    assert stated_us_locations("", "Fellow\tSt. Exampleton, OH") == [("St. Exampleton", "Ohio")]
    assert stated_us_locations("", "Fellow\tExampleton-Weston, OH") == [("Exampleton-Weston", "Ohio")]
    assert stated_us_locations("", "Fellow\tNew Example City, OH") == [("New Example City", "Ohio")]
    # Up to three words: a fourth means the segment is not just a city.
    assert stated_us_locations("", "Fellow\tBig New Example City, OH") == []


def test_stated_us_locations_reads_the_comma_between_city_and_state_loosely():
    assert stated_us_locations("", "Fellow\tExampleton ,OH") == [("Exampleton", "Ohio")]
    assert stated_us_locations("", "Fellow\tExampleton,  OH") == [("Exampleton", "Ohio")]
    assert stated_us_locations("", "Fellow\tExampleton OH") == []


def test_stated_us_locations_needs_the_state_to_end_its_segment():
    for after in (", 2010", "\tFellow", "\nFellow", " | Fellow", ")", ". Fellow", "; Fellow",
                  " 2010", " — 2010", "  USA", ""):
        assert stated_us_locations("", f"Fellow\tExampleton, OH{after}") == [("Exampleton", "Ohio")], after
    assert stated_us_locations("", "Fellow\tExampleton, OH Fellow") == []
    # "USA" must be the whole word, after a space.
    assert stated_us_locations("", "Fellow\tExampleton, OH USC") == []
    assert stated_us_locations("", "Fellow\tExampleton, OHUSA") == []


def test_stated_us_locations_skips_a_city_followed_by_a_credential():
    assert stated_us_locations("", "Fellow\tExampleton, OH, PhD") == []
    assert stated_us_locations("", "Fellow\tExampleton, OH, FACS") == []
    # Only a credential straight after the state counts.
    assert stated_us_locations("", "Fellow\tExampleton, OH, Fellow, PhD") == [("Exampleton", "Ohio")]
    # A word that only starts like a credential is not one.
    for after in (", Philadelphia", ", FALL 2015"):
        assert stated_us_locations("", f"Fellow\tExampleton, OH{after}") == [("Exampleton", "Ohio")], after


def test_stated_us_locations_skips_an_institution_word_before_the_state():
    for word in ("Center", "College", "Health", "Hospital", "Inc.", "Institute", "Medicine", "University"):
        assert stated_us_locations("", f"Fellow\tExample {word}, OH") == [], word


def test_stated_us_locations_keeps_one_city_name_in_two_states_apart():
    assert stated_us_locations("", "Fellow\tExampleton, IL; Exampleton, MA") == [
        ("Exampleton", "Illinois"), ("Exampleton", "Massachusetts")]


def test_names_city_matches_whole_words_only():
    assert names_city("Fellow, Exampleton, OH", "Exampleton")
    assert not names_city("Fellow, Exampletonville, OH", "Exampleton")


def test_is_generic_institution_name_word_by_word():
    for word in ("Center", "Children", "Health", "Hospital", "Medical", "Of", "Pharmacy",
                 "Public", "S", "School", "The", "University"):
        assert is_generic_institution_name(word), word
    assert is_generic_institution_name("The Children's Hospital")
    assert is_generic_institution_name("") is False
    assert not is_generic_institution_name("Example Hospital")


def test_is_delivered_online_shapes():
    for text in (
        "Example Course, online", "Example Course,online", "Example Course (online)",
        "Example Course (online )", "Example Course, Virtual", "Example Course, Virtual via Zoom",
        "Example Course, Online\tand more", "Example online course", "Example Online Courses 2019",
    ):
        assert is_delivered_online("", text, "B2"), text
        assert is_delivered_online(text, "", "B2"), text
    for text in (
        "Example Course online", "Example Course, online and in person",
        "online coursework followed by in-residence learning", "Example Course, remote",
    ):
        assert not is_delivered_online("", text, "B2"), text
    assert not is_delivered_online("", "Example Course, online", None)


def test_reconcile_leaves_a_result_with_no_city_alone():
    for city in ("", None):
        result = {**_lookup("", "Ohio"), "city": city}
        assert reconcile_location_with_source(result, "Example Hospital", "Fellow\tExampleton, OH", "C") is result


def test_reconcile_looks_for_the_city_in_the_institution_and_the_text_separately():
    result = _lookup("Exampleville", "Ohio")
    # Named only in the institution field, or only in the text: stands.
    assert reconcile_location_with_source(
        result, "Example Hospital, Exampleville", "Fellow\tOtherton, OH", "C") is result
    assert reconcile_location_with_source(
        result, "Example Hospital", "Fellow, Exampleville\tOtherton, OH", "C") is result
