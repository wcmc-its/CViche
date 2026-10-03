"""stage3b/fragment_guard.py (EBYSBC E29/E8, #986/#985): T-validation may not
recode a fragment or a group sub-heading into a renderable code, and still
recodes a content-bearing line.

Self-contained: no LLM, no network, synthetic text only.
"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage3b.fragment_guard import (  # noqa: E402
    REASON_NO_CONTENT_WORDS,
    REASON_REASONING_SAYS_FRAGMENT,
    t_recode_refusal,
)

_NEUTRAL_REASONING = "Award entry under Honors"


# --- rule 1: no content words -------------------------------------------------

@pytest.mark.parametrize("text", [
    "47.", "1955- 1956.", "2037-", "2041 – 2043", "03/1971-09/74",
    "March 2019", "June 3rd, 2011", "2001 to present", "Sept 1999",
    "September 2004 - Sep 2006", "",
])
def test_line_without_content_words_is_refused_whatever_the_reasoning(text):
    assert t_recode_refusal(text, "H", _NEUTRAL_REASONING) == REASON_NO_CONTENT_WORDS


def test_no_content_rule_applies_to_exempt_codes_too():
    """A bare date is not a position or a grant either."""
    assert t_recode_refusal("02/1966-11/1967", "M2B", _NEUTRAL_REASONING) == REASON_NO_CONTENT_WORDS
    assert t_recode_refusal("1990-1992", "D3", _NEUTRAL_REASONING) == REASON_NO_CONTENT_WORDS


@pytest.mark.parametrize("text", ["MD", "Vice Treasurer", "Exampleton Prize 2004"])
def test_a_single_real_word_is_content(text):
    assert t_recode_refusal(text, "H", _NEUTRAL_REASONING) is None


# --- rule 2: reasoning says fragment, text is shaped like one ------------------

@pytest.mark.parametrize("text, reasoning", [
    ("Exampleton University", "Institution fragment under Administrative Positions"),
    ("Sample Clinic Annex", "Institution fragment under Committees section"),
    ("Arts;1985.", "Fragment continuation of an award entry"),
    ("Sample Teaching Hospital:", "Institution header within teaching section"),
    ("Mentor Role:", "Header label for mentee category"),
    ("Department Service:", "Section header for department service"),
    ("Northfield", "Under Teaching section, likely sub-header grouping courses"),
    ("May 2nd, 2012 - Exampleville, Freedonia",
     "Entry falls under COURSES; despite missing a title, the date/location fragment is a lecture"),
])
def test_short_line_the_reasoning_calls_a_fragment_or_header_is_refused(text, reasoning):
    assert t_recode_refusal(text, "O", reasoning) == REASON_REASONING_SAYS_FRAGMENT


def test_lower_case_sentence_tail_is_refused_even_when_long():
    text = "status to a permanent post for early-career researchers from groups that are underrepresented"
    assert t_recode_refusal(text, "H", "Fragment describing a promotion") == REASON_REASONING_SAYS_FRAGMENT


def test_long_capitalised_line_is_not_fragment_shaped():
    text = "Co-Investigator: Example Study of Long-Term Outcomes in a Sample Cohort, Sample Foundation"
    assert t_recode_refusal(text, "R", "Grant support entry fragment") is None


def test_line_opening_with_a_year_is_a_dated_record_head():
    assert t_recode_refusal("2000 Office of Sample Affairs, Agency", "Q2",
                            "Fragment referencing agency service") is None


@pytest.mark.parametrize("reasoning", [
    "Despite being a fragment, this is a presentation title with a date",
    "Despite being formatted as a fragment, it represents an invited talk",
    "Despite header formatting, this entry describes a specific award",
    "Though header-like, it points to an outreach activity",
    "Header-like fragment naming an outreach activity",
    "Formatted as a fragment, but names a specific talk",
])
def test_reasoning_that_argues_for_real_content_does_not_count(reasoning):
    assert t_recode_refusal("Example Talk Title", "R", reasoning) is None


@pytest.mark.parametrize("reasoning", [
    "Listed under reviewer header context, likely a reviewing role",
    "Journal name listed under reviewer header, an ad hoc review",
    "Located under the INVITED PRESENTATIONS header, a talk title",
])
def test_a_header_named_as_the_lines_parent_does_not_count(reasoning):
    assert t_recode_refusal("Example Society Journal", "Q4D", reasoning) is None


@pytest.mark.parametrize(
    "code", ["A", "B1", "B2", "C", "I", "D1", "D3", "M2", "M2A", "M2B", "M2C", "M2D"])
def test_codes_built_from_pieces_are_exempt_from_the_reasoning_rule(code):
    assert t_recode_refusal("Exampleton University", code,
                            "Institution fragment under Experience") is None


def test_neutral_reasoning_on_a_short_content_line_is_not_refused():
    assert t_recode_refusal("Example Society", "Q2", "Committee service for this society") is None


def test_a_header_word_past_the_reasoning_lead_does_not_count():
    """Only the first few lead words may precede 'header'; a fifth means the
    reasoning is describing something else, not naming the line a header."""
    assert t_recode_refusal("Example Society", "Q2",
                            "Example Society Board Service Header") == REASON_REASONING_SAYS_FRAGMENT
    assert t_recode_refusal("Example Society", "Q2",
                            "Committee role for the society header") is None


def test_header_word_as_a_compound_prefix_does_not_count():
    assert t_recode_refusal("Example Society", "Q2", "Header-based layout, a committee role") is None


def test_long_line_starting_with_closing_punctuation_is_a_sentence_tail():
    text = ") and the Example Regional Board for Sample Outreach Programs in Northfield County"
    assert t_recode_refusal(text, "H", "Fragment of an award entry") == REASON_REASONING_SAYS_FRAGMENT
