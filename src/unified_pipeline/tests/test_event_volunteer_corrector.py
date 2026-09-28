"""Issue #946 item 4: extramural event medical volunteering is Appendix (T), not P.

Synthetic rows only. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_event_volunteer_corrector.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.validators.event_volunteer_corrector import (  # noqa: E402
    apply_event_volunteer_corrections,
    correct_event_volunteer,
)

_HEADING = ["PROFESSIONAL DEVELOPMENT AND LEADERSHIP EXPERIENCES"]


def _entry(text, code="P"):
    return {"text": text, "taxonomy_code": code, "hierarchy": list(_HEADING), "element_idx_start": 7}


@pytest.mark.parametrize("text", [
    "2021 Riverside 50 Miler\tMedical Volunteer",
    "2019   Lakeshore Criterium Cup, Springfield, IL\tMedical Volunteer",
    "2017-2019   City Marathon, Springfield, IL   Medical Volunteer, Course Tent",
    "2017   Open Golf Tournament, Springfield, IL\tMedical Volunteer",
    "2016   Volunteer physician, State Special Games",
    "2015   Medical coverage for the County Triathlon",
])
def test_event_medical_volunteer_p_goes_to_appendix(text):
    out = correct_event_volunteer(_entry(text))
    assert out["taxonomy_code"] == "T"
    assert out["original_taxonomy_code"] == "P"
    assert out["event_volunteer_correction"]["from"] == "P"
    assert out["event_volunteer_correction"]["to"] == "T"


@pytest.mark.parametrize("event", [
    "City Marathon", "Harbor Marathons", "Valley 100 Miler", "County Triathlon", "Lake Triathlons",
    "Coastal Ironman", "Hill Race", "Charity Races", "River Regatta", "Spring Cycling Classic",
    "Open Tournament", "Junior Tournaments", "Harbor Cup", "Summer Games", "State Championship",
    "Masters Championships", "Special Olympics",
])
def test_each_event_word_triggers(event):
    """Every EVENT_WORDS alternative is load-bearing on its own."""
    assert correct_event_volunteer(_entry(f"2018   {event}\tMedical Volunteer"))["taxonomy_code"] == "T"


@pytest.mark.parametrize("role", [
    "Medical Volunteer", "Volunteer Physician", "Volunteer Medical Staff",
    "Medical Coverage for", "Medical coverage at",
])
def test_each_volunteer_role_triggers(role):
    """Every MEDICAL_VOLUNTEER_ROLE alternative is load-bearing on its own."""
    assert correct_event_volunteer(_entry(f"2018   Harbor Cup\t{role}"))["taxonomy_code"] == "T"


def test_none_text_is_left_alone():
    out, stats = apply_event_volunteer_corrections([{"text": None, "taxonomy_code": "P"}])
    assert out[0]["taxonomy_code"] == "P"
    assert stats["corrections_applied"] == 0


@pytest.mark.parametrize("text", [
    # A committee seat at an event is a committee, not a volunteer role (#946: out of scope).
    "2019-2021  City Marathon Medical Committee, Springfield, IL\tMember",
    # A QI project row from the same section.
    "2018-2019  Academy for Quality and Safety Improvement\tProject Member",
    # An event word with no medical-volunteer role.
    "Race around the Table, Facilitator\t9/2019",
    # A medical-volunteer role with no event.
    "2020   Medical Volunteer, Community Free Clinic",
    # Medical coverage that is insurance or a call schedule, not volunteering.
    "2019   Medical coverage, State Games employee benefits",
    # An event word inside an institutional body is internal service.
    "2018   Student Games Planning Committee\tMedical Volunteer",
    "2018   Cycling Safety Task Force\tMedical Volunteer",
    "2018   Hospital Race Advisory Council\tMedical Volunteer",
    "2018   Marathon Working Group\tMedical Volunteer",
    "2018   Harbor Cup Steering Panel\tMedical Volunteer",
    "2018   Olympics Board\tMedical Volunteer",
    # Each institutional-body word alone.
    "2018   Marathon Committees\tMedical Volunteer",
    "2018   Marathon Council\tMedical Volunteer",
    "2018   Marathon Panel\tMedical Volunteer",
    "2018   Marathon Advisory\tMedical Volunteer",
    "2018   Marathon Steering\tMedical Volunteer",
    # "coverage for" must be whole words.
    "2019   Medical coverage format, State Games",
])
def test_rows_missing_either_signal_stay_p(text):
    out = correct_event_volunteer(_entry(text))
    assert out["taxonomy_code"] == "P"
    assert "event_volunteer_correction" not in out


def test_only_p_is_reviewed():
    """A Q2 (external service) event row is not this corrector's decision."""
    out = correct_event_volunteer(_entry("2016 Volunteer medical staff, State Special Games", code="Q2"))
    assert out["taxonomy_code"] == "Q2"


@pytest.mark.parametrize("certified", ["board certified", "Board-certified", "board - certified"])
def test_board_certified_is_not_an_institutional_body(certified):
    text = f"2018   Harbor Cup\tMedical Volunteer ({certified} EM physician)"
    assert correct_event_volunteer(_entry(text))["taxonomy_code"] == "T"


def test_input_entry_is_not_mutated():
    entry = _entry("2021 Riverside 50 Miler\tMedical Volunteer")
    correct_event_volunteer(entry)
    assert entry["taxonomy_code"] == "P"
    assert "original_taxonomy_code" not in entry


def test_apply_counts_and_details():
    entries = [
        _entry("2021 Riverside 50 Miler\tMedical Volunteer"),
        _entry("2019-2021  City Marathon Medical Committee\tMember"),
    ]
    out, stats = apply_event_volunteer_corrections(entries)
    assert [e["taxonomy_code"] for e in out] == ["T", "P"]
    assert stats["corrections_applied"] == 1
    assert stats["correction_details"][0]["element_idx"] == 7
    detail = stats["correction_details"][0]
    assert detail["correction"] == out[0]["event_volunteer_correction"]
    assert detail["text_preview"] == "2021 Riverside 50 Miler\tMedical Volunteer"


def test_text_preview_is_capped_at_100_chars():
    long_text = "2021 Riverside 50 Miler\tMedical Volunteer " + "x" * 200
    _, stats = apply_event_volunteer_corrections([_entry(long_text)])
    assert stats["correction_details"][0]["text_preview"] == long_text[:100]


def test_first_original_code_is_kept():
    """An entry an earlier corrector already recoded (O -> P) keeps its true original code."""
    entry = _entry("2021 Riverside 50 Miler\tMedical Volunteer")
    entry["original_taxonomy_code"] = "O"
    assert correct_event_volunteer(entry)["original_taxonomy_code"] == "O"


def test_an_earlier_pass_is_not_recounted():
    """A row that already carries the correction key from a previous pass is not a new correction."""
    earlier, _ = apply_event_volunteer_corrections([_entry("2021 Riverside 50 Miler\tMedical Volunteer")])
    out, stats = apply_event_volunteer_corrections(earlier)
    assert out[0]["taxonomy_code"] == "T"
    assert stats["corrections_applied"] == 0
