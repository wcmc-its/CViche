"""`stage6.parsing.entry_card` (#2019): one free-form K or L entry read as a
card -- bold head, organization, right-tabbed date, detail lines -- each slot a
span of the entry's own text, or None so the section keeps its old rendering.

Every case below states its card AND checks the invariant the issue makes
non-negotiable: the slots hold exactly the text's words, none lost or added.
Names and places are invented.
"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.parsing.entry_card import (  # noqa: E402
    LAYOUT_ONE_LINE,
    LAYOUT_WITH_DETAILS,
    MAX_HEAD_WORDS,
    MAX_UNDATED_DETAIL_ITEMS,
    MAX_WORDS_BEFORE_ANCHOR,
    EntryCard,
    date_runs,
    entry_card,
    find_span,
    words,
)


def _card(text, **fields):
    card = entry_card(text, fields)
    if card is not None:
        slots = " ".join([card.head, card.org, card.date, *card.details])
        assert words(slots) == words(text), "a card changed the entry's words"
    return card


# --- the layouts ---------------------------------------------------------------

def test_the_issue_shape_is_a_card_with_details():
    text = ("Attending Surgeon, Resident Teaching, Northfield Health\t2025–Sep 2026\t"
            "Orthopaedic Surgery Residency; clinical and operative teaching of PGY-5 residents")
    card = _card(text, role="Attending Surgeon, Resident Teaching", institution="Northfield Health")
    assert card == EntryCard(
        head="Attending Surgeon, Resident Teaching", org="Northfield Health", date="2025–Sep 2026",
        details=("Orthopaedic Surgery Residency; clinical and operative teaching of PGY-5 residents",))
    assert card.layout == LAYOUT_WITH_DETAILS


def test_head_org_and_date_alone_are_a_one_line_card():
    card = _card("Clinic Lead, Harrowgate Hospital, 2015 – 2018",
                 leadership_role="Clinic Lead", institution="Harrowgate Hospital")
    assert card == EntryCard("Clinic Lead", "Harrowgate Hospital", "2015 – 2018", ())
    assert card.layout == LAYOUT_ONE_LINE


def test_a_head_with_details_and_no_org_or_date_is_a_card():
    card = _card("Course Director, Estuarine Biology: weekly seminar for second-year students",
                 role="Course Director")
    assert card == EntryCard("Course Director", "", "",
                             ("Estuarine Biology: weekly seminar for second-year students",))


def test_an_anchor_inside_its_segment_takes_the_whole_segment_as_head():
    # "Role of Title": the role anchors, the head runs to the next separator.
    card = _card("Co-Director of Introduction to Tidal Science, Example University, 2012-2019",
                 course_title="Introduction to Tidal Science", institution="Example University")
    assert card.head == "Co-Director of Introduction to Tidal Science"
    assert (card.org, card.date) == ("Example University", "2012-2019")


def test_a_leading_date_is_the_cards_date_and_the_head_starts_after_it():
    card = _card("2020\tCharge Nurse, Example ICU\tNight shift precepting of new graduates",
                 clinical_role="Charge Nurse")
    assert (card.head, card.date) == ("Charge Nurse", "2020")
    assert card.details == ("Example ICU", "Night shift precepting of new graduates")


def test_an_open_range_keeps_its_dash():
    """"2015-" is a range still running: printing "2015" would close it."""
    for text, date in [("Clinic Lead, Harrowgate Hospital, 2015-", "2015-"),
                       ("Clinic Lead, Harrowgate Hospital, 2015 –", "2015 –")]:
        assert _card(text, leadership_role="Clinic Lead", institution="Harrowgate Hospital").date == date


def test_a_field_differing_in_case_or_spacing_anchors_but_the_source_is_printed():
    card = _card("COURSE  DIRECTOR, Example University, 2010", role="Course Director",
                 institution="Example University")
    assert card.head == "COURSE  DIRECTOR"


def test_a_list_valued_field_of_one_string_anchors():
    card = _card("Preceptor, Example Clinic, 2019", role=["Preceptor"], institution="Example Clinic")
    assert card.head == "Preceptor"


def test_an_org_not_right_after_the_head_stays_in_the_details():
    card = _card("Lecturer, Pharmacology for Nurses, Example University, 2018",
                 role="Lecturer", institution="Example University")
    assert (card.head, card.org, card.date) == ("Lecturer", "", "2018")
    assert card.details == ("Pharmacology for Nurses, Example University",)


def test_a_punctuation_only_cell_is_not_a_detail_line():
    card = _card("Preceptor, Example Clinic\t2019\t)", role="Preceptor", institution="Example Clinic")
    assert card.details == ()


# --- the fallbacks: None, and the section renders as before ------------------------

@pytest.mark.parametrize("text, fields", [
    pytest.param("Lectures on tidal science to first-year students", {"role": "Mentor"},
                 id="no_anchor_in_the_text"),
    pytest.param("Lectures on tidal science", {}, id="no_fields"),
    pytest.param("Preceptor, Example Clinic\n2019", {"role": "Preceptor"}, id="multi_line"),
    pytest.param("Preceptor", {"role": "Preceptor"}, id="head_is_the_whole_entry"),
    pytest.param("2019\t2020", {"role": "2019"}, id="date_only_head"),
    pytest.param("Notes: Preceptor, Example Clinic, 2019", {"role": "Preceptor"},
                 id="anchor_after_a_strong_separator"),
    pytest.param("A " * (MAX_WORDS_BEFORE_ANCHOR + 1) + "Preceptor, Example Clinic, 2019",
                 {"role": "Preceptor"}, id="too_many_words_before_the_anchor"),
    pytest.param("Grand  Rounds   Preceptor, 2019", {"role": "Preceptor"}, id="column_gap_in_head"),
    pytest.param("Preceptor (Example Clinic, 2019", {"role": "Preceptor"}, id="unbalanced_head"),
    pytest.param("Course Director, Estuarine Biology, 2010-2018\t"
                 "Module Leader, Sediment Laboratory, 2012-2014",
                 {"role": "Course Director"}, id="second_record_in_the_details"),
    pytest.param("Topics\t" + "\t".join(f"Topic {n}" for n in range(MAX_UNDATED_DETAIL_ITEMS + 1)),
                 {"activity_title": "Topics"}, id="undated_head_of_a_list_of_peers"),
    pytest.param(" ".join(["Word"] * MAX_HEAD_WORDS) + " Preceptor more, Example Clinic, 2019",
                 {"role": "Preceptor"}, id="head_too_long"),
    pytest.param("", {"role": "Preceptor"}, id="empty_text"),
])
def test_text_that_is_not_a_card_returns_none(text, fields):
    assert _card(text, **fields) is None


def test_an_anchor_glued_inside_a_word_is_not_found():
    assert find_span("Preceptorship, Example Clinic", "Preceptor") is None
    assert find_span("Preceptor, Example Clinic", "Preceptor") == (0, 9)


@pytest.mark.parametrize("text, runs", [
    ("2010-2018", ["2010-2018"]),
    ("August 2020 – June 2022", ["August 2020 – June 2022"]),
    ("March 2021, May 2022 and June 2023", ["March 2021, May 2022 and June 2023"]),
    ("2015-; ongoing", ["2015-", "ongoing"]),
    ("Room 12", []),
])
def test_date_runs_merge_adjacent_tokens(text, runs):
    assert [text[a:b] for a, b in date_runs(text)] == runs
