"""`stage6.parsing.entry_card` (#2019): one free-form K or L entry read as a
card -- its cells as lines in source order, the date where the source has it,
and the span of its title to bold -- or None so the section keeps its old
rendering.

Every case below states its card AND checks the invariant the issue makes
non-negotiable: the lines hold exactly the text's words, none lost or added.
Names and places are invented.
"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.parsing import (  # noqa: E402
    entry_card as entry_card_module,
)
from unified_pipeline.stage6.parsing.entry_card import (  # noqa: E402
    CELL_JOIN,
    MAX_HEAD_WORDS,
    MAX_ORG_GAP_CHARS,
    MAX_UNDATED_DETAIL_ITEMS,
    MAX_WORDS_BEFORE_ANCHOR,
    EntryCard,
    date_runs,
    entry_card,
    find_span,
    title_span,
    words,
)


def _card(text, **fields):
    card = entry_card(text, fields)
    if card is not None:
        assert words(" ".join(card.lines)) == words(text), "a card changed the entry's words"
    return card


def _bold(card):
    """The text a card bolds, or None."""
    return card.lines[0][slice(*card.title)] if card.title else None


# --- the layouts ---------------------------------------------------------------

def test_the_issue_shape_is_a_card_with_its_date_inline():
    """Paul's review (2026-10-27): the date stays where the source has it,
    and only the title is bold."""
    text = ("Attending Surgeon, Resident Teaching, Northfield Health\t2025–Sep 2026\t"
            "Orthopaedic Surgery Residency; clinical and operative teaching of PGY-5 residents")
    card = _card(text, role="Attending Surgeon, Resident Teaching", institution="Northfield Health")
    assert card.lines == (
        "Attending Surgeon, Resident Teaching, Northfield Health, 2025–Sep 2026",
        "Orthopaedic Surgery Residency; clinical and operative teaching of PGY-5 residents")
    assert _bold(card) == "Attending Surgeon, Resident Teaching"
    assert card.title == (0, len("Attending Surgeon, Resident Teaching"))


def test_head_org_and_date_alone_are_a_one_line_card():
    card = _card("Clinic Lead, Harrowgate Hospital, 2015 – 2018",
                 leadership_role="Clinic Lead", institution="Harrowgate Hospital")
    assert card == EntryCard(("Clinic Lead, Harrowgate Hospital, 2015 – 2018",), (0, 11),
                             has_details=False)


def test_a_head_with_details_and_no_org_or_date_keeps_its_one_cell_line():
    card = _card("Course Director, Estuarine Biology: weekly seminar for second-year students",
                 role="Course Director")
    assert card.lines == ("Course Director, Estuarine Biology: weekly seminar for second-year students",)
    assert _bold(card) == "Course Director"
    assert card.has_details


def test_the_title_is_bold_not_the_role_around_it():
    # "Role of Title": a title field outranks a role field for the bold.
    card = _card("Co-Director of Introduction to Tidal Science, Example University, 2012-2019",
                 role="Co-Director", course_title="Introduction to Tidal Science",
                 institution="Example University")
    assert card.lines == ("Co-Director of Introduction to Tidal Science, Example University, 2012-2019",)
    assert _bold(card) == "Introduction to Tidal Science"


def test_a_presenter_list_before_the_title_is_never_bold():
    """KH2YMW's shape (#2019 review): a K4 workshop line opens with its
    presenters. The bold covers the title field's span alone."""
    text = ("Avery Quill, Bram Ostrander, Cleo Marchetti.  Leadership Challenges in Rural "
            "Care\tExample Society Annual Meeting\t2021")
    card = _card(text, activity_title="Leadership Challenges in Rural Care")
    assert card.lines == ("Avery Quill, Bram Ostrander, Cleo Marchetti.  Leadership Challenges "
                          "in Rural Care", "Example Society Annual Meeting, 2021")
    assert _bold(card) == "Leadership Challenges in Rural Care"
    assert "," not in _bold(card)


def test_a_leading_date_cell_joins_line_1_in_front():
    card = _card("2020\tCharge Nurse, Example ICU\tNight shift precepting of new graduates",
                 clinical_role="Charge Nurse")
    assert card.lines == ("2020" + CELL_JOIN + "Charge Nurse, Example ICU",
                          "Night shift precepting of new graduates")
    assert _bold(card) == "Charge Nurse"


def test_a_date_cell_joins_the_line_before_it():
    card = _card("Lecturer of Topic A\tExample Clinic\t2019\tTwo lectures a year", role="Lecturer")
    assert card.lines == ("Lecturer of Topic A", "Example Clinic, 2019", "Two lectures a year")


def test_an_open_range_keeps_its_dash():
    """"2015-" is a range still running: printing "2015" would close it."""
    for text in ("Clinic Lead, Harrowgate Hospital, 2015-", "Clinic Lead, Harrowgate Hospital, 2015 –",
                 "Clinic Lead\tHarrowgate Hospital\t2015-"):
        card = _card(text, leadership_role="Clinic Lead", institution="Harrowgate Hospital")
        assert card.lines[-1].endswith(("2015-", "2015 –")), text


def test_date_text_rewrites_the_date_where_it_stands():
    card = entry_card("2013-\tUniversity Course on Violence (U, G, spring)",
                      {"course_title": "University Course on Violence"},
                      date_text=lambda date: date + "Present")
    assert card.lines == ("2013-Present, University Course on Violence (U, G, spring)",)
    assert _bold(card) == "University Course on Violence"


def test_a_field_differing_in_case_or_spacing_anchors_but_is_not_bold():
    """The anchor may match loosely; the bold is verbatim or nothing."""
    card = _card("COURSE  DIRECTOR, Example University, 2010", role="Course Director",
                 institution="Example University")
    assert card.lines == ("COURSE  DIRECTOR, Example University, 2010",)
    assert card.title is None


def test_a_list_valued_field_of_one_string_anchors():
    card = _card("Preceptor, Example Clinic, 2019", role=["Preceptor"], institution="Example Clinic")
    assert _bold(card) == "Preceptor"


def test_a_punctuation_only_cell_is_not_a_line():
    card = _card("Preceptor, Example Clinic\t2019\t)", role="Preceptor", institution="Example Clinic")
    assert card.lines == ("Preceptor, Example Clinic, 2019",)


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
    pytest.param("One two three four five six seven Preceptor, Example Clinic, 2019",
                 {"role": "Preceptor"}, id="seven_words_before_the_anchor"),
    pytest.param("Grand  Rounds   Preceptor, 2019", {"role": "Preceptor"}, id="column_gap_in_head"),
    pytest.param("Preceptor (Example Clinic, 2019", {"role": "Preceptor"}, id="unbalanced_head"),
    pytest.param("Course Director, Estuarine Biology, 2010-2018\t"
                 "Module Leader, Sediment Laboratory, 2012-2014",
                 {"role": "Course Director"}, id="second_record_in_the_details"),
    pytest.param("Topics\t" + "\t".join(f"Topic {n}" for n in range(MAX_UNDATED_DETAIL_ITEMS + 1)),
                 {"activity_title": "Topics"}, id="undated_head_of_a_list_of_peers"),
    pytest.param(" ".join(["Word"] * MAX_HEAD_WORDS) + " Preceptor more, Example Clinic, 2019",
                 {"role": "Preceptor"}, id="head_too_long"),
    pytest.param("Preceptor " + " ".join(["word"] * MAX_HEAD_WORDS) + ", Example Clinic, 2019",
                 {"role": "Preceptor"}, id="head_too_long_after_its_anchor"),
    pytest.param("Course Director\tIntro Biology\tExample University\t2019",
                 {"course_title": "Course Director Intro Biology"}, id="head_anchor_across_a_tab"),
    pytest.param("Course\tDirector of things, Example U, 2019", {"role": "Course Director"},
                 id="head_anchor_split_by_a_tab"),
    pytest.param("Course Director | Intro Biology | 2019",
                 {"course_title": "Course Director Intro Biology"},
                 id="head_anchor_across_a_cell_join"),
    pytest.param("Lead, Example Initiatives, 2022-2024\tSelected Initiatives:\t"
                 "Built a referral workflow\tWrote a clinic manual",
                 {"project_name": "Lead"}, id="detail_label_heads_a_sub_list"),
    pytest.param("Lead, Example Initiatives, 2022-2024 | Scope: | Oversaw two clinics",
                 {"project_name": "Lead"}, id="detail_label_heads_a_sub_list_across_a_cell_join"),
    pytest.param("2019\tLecturer, Topic A", {"course_title": "2019 Lecturer"},
                 id="anchor_starts_inside_the_leading_date"),
    pytest.param("Head Preceptor) Example, Clinic, 2019", {"role": "Preceptor"},
                 id="head_closes_a_bracket_it_never_opened"),
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


def test_the_word_check_refuses_a_rule_that_drops_a_word(monkeypatch):
    """The last word on "no word lost or added": whatever the line rule
    does, a card whose lines' words are not the text's is not a card."""
    text = "Preceptor, Example Clinic, 2019\tWeekly clinic sessions"
    assert _card(text, role="Preceptor") is not None
    monkeypatch.setattr(entry_card_module, "_trimmed_span",
                        lambda text, start, end, keep: (start, max(start, end - 3)))
    assert _card(text, role="Preceptor") is None


# --- boundaries and cells (#2019 review) -------------------------------------------

def _words(n):
    return " ".join(f"word{i}" for i in range(n))


def test_a_head_of_twelve_words_is_a_card_and_thirteen_is_not():
    """A head over 12 words is a sentence, not a title (#2019 review). The
    numbers are literal so a change to the cap fails here."""
    assert MAX_HEAD_WORDS == 12
    for n, is_card in [(12, True), (13, False)]:
        head = "Preceptor " + _words(n - 1)
        assert (_card(f"{head}, Example Clinic, 2019", role="Preceptor") is not None) is is_card, n


def test_an_org_four_separator_characters_from_the_head_shares_line_1_and_five_does_not():
    assert MAX_ORG_GAP_CHARS == 4
    for gap, lines in [(",  \t", ("Preceptor, Example Clinic, 2019",)),
                       (",   \t", ("Preceptor", "Example Clinic, 2019"))]:
        card = _card(f"Preceptor{gap}Example Clinic\t2019", role="Preceptor",
                     institution="Example Clinic")
        assert card.lines == lines, repr(gap)


def test_an_org_never_spans_a_cell_break():
    card = _card("Preceptor\tExample\tClinic\t2019", role="Preceptor", institution="Example Clinic")
    assert card.lines == ("Preceptor", "Example", "Clinic, 2019")


def test_an_undated_head_takes_three_detail_items_and_no_more():
    def text(n):
        return "Topics\t" + "\t".join(f"Topic {chr(65 + i)}" for i in range(n))
    assert MAX_UNDATED_DETAIL_ITEMS == 3
    assert len(_card(text(3), activity_title="Topics").lines) == 4
    assert _card(text(4), activity_title="Topics") is None


def test_a_bracketed_date_stays_in_its_brackets():
    """"(2015-2019)" is the source's aside: it is not the card's date."""
    card = _card("Preceptor, Example Clinic\t(2015-2019)", role="Preceptor",
                 institution="Example Clinic")
    assert card.lines == ("Preceptor, Example Clinic", "(2015-2019)")


def test_stage2s_cell_join_splits_lines_and_never_prints():
    """Stage 2 joins a table row's cells with " | ": each cell is a line,
    as a tab cell is, and the join never prints."""
    card = _card("Lecturer of Topic A | Example Clinic | 2019 | Two lectures a year",
                 role="Lecturer")
    assert card.lines == ("Lecturer of Topic A", "Example Clinic, 2019", "Two lectures a year")
    assert all("|" not in line for line in card.lines)


def test_a_leading_date_after_leading_whitespace_is_still_the_cards_date():
    card = _card("  2020\tStaff Nurse, Example ICU", clinical_role="Staff Nurse")
    assert card.lines == ("2020, Staff Nurse, Example ICU",)


def test_a_colon_ending_the_head_does_not_read_as_a_sub_list_label():
    card = _card("Course Director:\tIntro Biology\t2019", role="Course Director")
    assert card.lines == ("Course Director", "Intro Biology, 2019")


# --- title_span: the bold, for cards and every other K/L first line ---------------

@pytest.mark.parametrize("line, fields, bold", [
    pytest.param("Lecturer, Intro Biology, 2019", {"role": "Lecturer"}, "Lecturer", id="role"),
    pytest.param("Lecturer, Intro Biology, 2019", {"role": "Lecturer", "course_title": "Intro Biology"},
                 "Intro Biology", id="title_before_role"),
    pytest.param("Lecturer, intro biology, 2019", {"course_title": "Intro Biology"}, None,
                 id="not_verbatim"),
    pytest.param("Lecturers, 2019", {"role": "Lecturer"}, None, id="inside_a_word"),
    pytest.param("2019, Lecturer", {"role": "2019"}, None, id="a_date_is_no_title"),
    pytest.param("A | B, 2019", {"course_title": "A | B"}, None, id="across_a_cell_join"),
    pytest.param("Lecturer", {}, None, id="no_fields"),
    pytest.param("Lecturer", None, None, id="fields_none"),
])
def test_title_span_bolds_a_verbatim_title_or_role_only(line, fields, bold):
    span = title_span(line, fields)
    assert (line[slice(*span)] if span else None) == bold


def test_title_span_refuses_a_field_longer_than_a_title():
    """12 words bold; 13 is a sentence (`MAX_HEAD_WORDS`), literal here."""
    for n, bolds in [(12, True), (13, False)]:
        title = _words(n)
        assert (title_span(title + ", 2019", {"course_title": title}) is not None) is bolds, n
