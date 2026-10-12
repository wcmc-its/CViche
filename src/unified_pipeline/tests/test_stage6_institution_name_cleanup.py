"""`_get_cleaned_institution_name` and `_strip_org_tail` (#735 review items 4, 5).

Neither function had any direct test coverage before this file. Both live in
`normalization/institutions.py` and share nothing but an input domain --
institution/organization names -- so they are pinned together here.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_institution_name_cleanup.py -p no:cacheprovider

Self-contained: no DB, no template, no python-docx, no PII -- every string
below is synthetic.
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization.institutions import (  # noqa: E402
    _get_cleaned_institution_name,
    _strip_org_tail,
)

# --------------------------------------------------------------------------
# item 4: _get_cleaned_institution_name
# --------------------------------------------------------------------------

def test_cleaned_name_present_is_returned_as_is() -> None:
    entry = {"institution_enrichment": {
        "cleaned_name": "Weill Cornell Medicine", "official_name": "WCM"}}
    assert _get_cleaned_institution_name(entry) == "Weill Cornell Medicine"


def test_empty_cleaned_name_falls_back_to_official_name() -> None:
    """Not belt and braces: stage 5b routinely returns an empty
    `cleaned_name` beside a correctly populated `official_name`."""
    entry = {"institution_enrichment": {
        "cleaned_name": "", "official_name": "Weill Cornell Medicine"}}
    assert _get_cleaned_institution_name(entry) == "Weill Cornell Medicine"


def _official(official: str, raw: str) -> dict:
    return {"institution_enrichment": {"cleaned_name": "", "official_name": official},
            "extracted_fields": {"institution": raw}}


def test_official_name_naming_another_place_is_not_used() -> None:
    """Stage 5b matched an acronym to an unrelated institution: the CV's own
    value renders instead (the caller's fallback)."""
    entry = _official("Institut Supérieur de Lumière Numérique",
                      "International Society for Kestrel Therapy (ISKT), online")
    assert _get_cleaned_institution_name(entry) is None


def test_official_name_in_another_script_is_not_used() -> None:
    assert _get_cleaned_institution_name(_official("كلية طب", "Kestrel Medical College")) is None


def test_official_name_sharing_a_distinctive_word_is_used() -> None:
    entry = _official("Kestrel Harbor University", "Kestrel Harbor Univ., Springfield")
    assert _get_cleaned_institution_name(entry) == "Kestrel Harbor University"


def test_official_name_is_used_when_the_entry_has_no_institution() -> None:
    entry = {"institution_enrichment": {"official_name": "Kestrel Harbor University"}}
    assert _get_cleaned_institution_name(entry) == "Kestrel Harbor University"


def test_cleaned_name_is_not_checked_against_the_raw_value() -> None:
    """cleaned_name is the raw value without its location, so a name of
    generic words only ("University Hospital") still applies."""
    entry = _cleaned("University Hospital, Springfield, IL", "University Hospital")
    assert _get_cleaned_institution_name(entry) == "University Hospital"


def test_both_empty_returns_none() -> None:
    entry = {"institution_enrichment": {"cleaned_name": "", "official_name": ""}}
    assert _get_cleaned_institution_name(entry) is None


def test_institution_enrichment_none_returns_none() -> None:
    """#559: the value may be present and explicitly None -- the `or {}`
    guard is what keeps `.get` from raising on it."""
    assert _get_cleaned_institution_name({"institution_enrichment": None}) is None


def test_institution_enrichment_key_absent_returns_none() -> None:
    assert _get_cleaned_institution_name({}) is None


def test_whitespace_only_cleaned_name_is_returned_verbatim_not_as_a_fallback() -> None:
    """A non-empty-but-blank string is truthy, so the `if cleaned:` guard
    does not treat it as absent -- it is returned as-is, whitespace and all,
    and `official_name` is never consulted. Documented here as the actual
    behaviour, not the more defensive one a reader might assume."""
    entry = {"institution_enrichment": {
        "cleaned_name": "   ", "official_name": "Weill Cornell Medicine"}}
    assert _get_cleaned_institution_name(entry) == "   "


def test_non_mapping_enrichment_is_treated_as_absent() -> None:
    """#743: a list- or str-valued institution_enrichment (a malformed
    stage-5b LLM output) used to raise AttributeError on `.get`, which
    failed every section that reads it; `_render_section` then sent that
    section to the Appendix. It must fall back to the
    no-enrichment result instead."""
    assert _get_cleaned_institution_name({"institution_enrichment": ["a"]}) is None
    assert _get_cleaned_institution_name({"institution_enrichment": "abc"}) is None


# --------------------------------------------------------------------------
# #1257: cleaned_name may drop only the location
# --------------------------------------------------------------------------

def _cleaned(raw: str, cleaned: str, city: str = "Springfield",
             state: str = "Illinois", country: str = "United States") -> dict:
    return {"institution_enrichment": {"cleaned_name": cleaned, "city": city,
                                       "state": state, "country": country},
            "extracted_fields": {"institution": raw}}


@pytest.mark.parametrize(("raw", "cleaned", "city", "state", "country"), [
    # A trailing "City, ST": the state is an acronym of 5b's state.
    ("Kestrel College of Medicine, Springfield, IL",
     "Kestrel College of Medicine", "Springfield", "Illinois", "United States"),
    # Parenthesised, with a ZIP code and a country acronym.
    ("Kestrel University (Springfield, IL 62701, USA)",
     "Kestrel University", "Springfield", "Illinois", "United States"),
    # A joining word and an accent-folded city.
    ("Kestrel Medical School at Leon",
     "Kestrel Medical School", "León", "", "Nicaragua"),
    # A country's long form.
    ("Kestrel Institute, Lanzhou, The Peoples Republic of China",
     "Kestrel Institute", "Lanzhou", "", "China"),
    # An acronym of the city ("OKC") and of the institution itself.
    ("Kestrel Health Center (KHC), OKC, Oklahoma",
     "Kestrel Health Center", "Oklahoma City", "Oklahoma", "United States"),
    # A second city before 5b's state is a city too.
    ("Kestrel Hospital, Harbor Town, CT",
     "Kestrel Hospital", "Bayport", "Connecticut", "United States"),
    # A word the cleaned name already holds names nothing new.
    ("Kestrel and Bayport Clinics, Kestrel and Bayport, CA",
     "Kestrel and Bayport Clinics", "Bayport", "California", "United States"),
])
def test_cleaned_name_dropping_only_location_is_kept(
        raw: str, cleaned: str, city: str, state: str, country: str) -> None:
    assert _get_cleaned_institution_name(
        _cleaned(raw, cleaned, city, state, country)) == cleaned


@pytest.mark.parametrize(("raw", "cleaned", "expected"), [
    # A department after the school, then the location (TXTATQ shape).
    ("Kestrel College of Pharmacy, Department of Pharmacy Practice, Springfield, IL",
     "Kestrel College of Pharmacy",
     "Kestrel College of Pharmacy, Department of Pharmacy Practice"),
    # A division and department before the school.
    ("Division of Trauma, Department of Surgery, Kestrel College of Medicine",
     "Kestrel College of Medicine",
     "Division of Trauma, Department of Surgery, Kestrel College of Medicine"),
    # An online platform in parentheses.
    ("Kestrel University (Coursera)", "Kestrel University",
     "Kestrel University (Coursera)"),
    # A platform in parentheses, then the location: the paren is kept.
    ("Kestrel University (Coursera), Springfield, IL", "Kestrel University",
     "Kestrel University (Coursera)"),
    # A parent-university prefix before a spaced dash (WIANVH shape).
    ("KHSU - Kestrel Medical School, Springfield, IL", "Kestrel Medical School",
     "KHSU - Kestrel Medical School"),
    # Two institutions joined by a hyphen (YYVHNN shape).
    ("Kestrel-University of Springfield, Springfield, Illinois",
     "University of Springfield", "Kestrel-University of Springfield"),
    # An acronym whose later letters happen to occur in the city, in
    # order ("Springfield" holds a P and a D), but whose first does not
    # start it, is not the city's.
    ("Kestrel University (XPD), Springfield, IL", "Kestrel University",
     "Kestrel University (XPD)"),
    # A delivery mode after a slash.
    ("Kestrel Academy / Virtual via Zoom", "Kestrel Academy",
     "Kestrel Academy / Virtual via Zoom"),
])
def test_cleaned_name_dropping_a_qualifier_renders_the_raw_value(
        raw: str, cleaned: str, expected: str) -> None:
    assert _get_cleaned_institution_name(_cleaned(raw, cleaned)) == expected


def test_leading_location_part_is_cut() -> None:
    entry = _cleaned("Springfield, Kestrel Academy, Department of Music",
                     "Kestrel Academy")
    assert _get_cleaned_institution_name(entry) == (
        "Kestrel Academy, Department of Music")


def test_cleaned_name_not_in_the_raw_value_is_kept() -> None:
    """An expanded abbreviation ("Depts." -> "Departments") is 5b's
    rewording, not a drop: there is no raw span to restore around."""
    entry = _cleaned("Dept. of Music, Kestrel Univ., Springfield, IL",
                     "Department of Music, Kestrel University")
    assert _get_cleaned_institution_name(entry) == (
        "Department of Music, Kestrel University")


def test_cleaned_name_with_no_raw_value_is_kept() -> None:
    entry = {"institution_enrichment": {"cleaned_name": "Kestrel University"},
             "extracted_fields": {"institution": None}}
    assert _get_cleaned_institution_name(entry) == "Kestrel University"


# --------------------------------------------------------------------------
# item 5: _strip_org_tail
# --------------------------------------------------------------------------

def test_org_at_the_end_is_stripped() -> None:
    assert _strip_org_tail(
        "Best Research Award, Weill Cornell Medicine", "Weill Cornell Medicine"
    ) == "Best Research Award"


def test_org_plus_one_short_comma_led_city_tail_is_stripped() -> None:
    assert _strip_org_tail(
        "Best Research Award, Indiana University, Bloomington",
        "Indiana University",
    ) == "Best Research Award"


def test_org_plus_a_two_word_city_tail_is_stripped() -> None:
    assert _strip_org_tail(
        "Best Research Award, Indiana University, Ann Arbor",
        "Indiana University",
    ) == "Best Research Award"


def test_org_in_the_middle_is_not_stripped() -> None:
    """Conservative by design: only strips at end-of-string. The org here
    appears twice, once mid-string and once trailing a non-comma word that
    does not fit the city-tail shape, so the whole-string anchor never
    matches and nothing is removed."""
    name = "Weill Cornell Medicine Award, Weill Cornell Medicine Committee"
    assert _strip_org_tail(name, "Weill Cornell Medicine") == name


def test_empty_org_returns_name_unchanged() -> None:
    assert _strip_org_tail("Best Research Award", "") == "Best Research Award"


def test_name_equal_to_org_falls_back_to_the_original_name() -> None:
    """Stripping the whole name would leave an empty string; the `stripped
    or name` fallback returns the original name instead of nothing."""
    assert _strip_org_tail("Indiana University", "Indiana University") == (
        "Indiana University"
    )


def test_an_org_containing_regex_metacharacters_is_matched_literally() -> None:
    """`org` is `re.escape`d before being spliced into the pattern -- a "+"
    or "." in the organization name must not be read as a quantifier or a
    wildcard."""
    assert _strip_org_tail(
        "Award, Prof. A+B University", "Prof. A+B University"
    ) == "Award"


def test_an_org_that_is_the_object_of_the_last_phrase_is_not_stripped() -> None:
    """#1412 (RCBKFG KUUKNJ 139): an org the name ends on after "of" or
    "from" is part of the name, not a tail. Stripping it left the name
    ending in a dangling preposition."""
    name = "Distinguished Alumnus of Made-up Medical College, Narnia"
    assert _strip_org_tail(name, "Made-up Medical College") == name
    name = "Fellowship from the Imaginary Foundation"
    assert _strip_org_tail(name, "Imaginary Foundation") == name
    name = "Prize awarded by Imaginary Society"
    assert _strip_org_tail(name, "Imaginary Society") == name
    # #1437 (HJPBEM 271/273 shape): "at" rendered the name as "... room at".
    name = "First prize of the mock room at Imaginary Residents Meeting (IRM)"
    assert _strip_org_tail(name, "Imaginary Residents Meeting (IRM)") == name


def test_trailing_stray_punctuation_after_the_org_is_absorbed() -> None:
    assert _strip_org_tail(
        "Award, Indiana University, .", "Indiana University"
    ) == "Award"


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_"):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
