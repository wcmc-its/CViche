"""Stage 4 extracts grant/award identifiers that stage 6 must actually render.

Corpus census over the 100 distinct rendered CVs, before this fix:

    grant_number    537 extracted, 528 absent from the render (49 CVs)
    awards            5 extracted,   5 absent               ( 2 CVs)
    funding_source  182 extracted, 128 absent               (18 CVs)

`grant_number` appeared exactly once in the 9.4k-line renderer -- in
_IDENTIFYING_FIELDS, which feeds dedup, not output -- and `awards` appeared
nowhere at all, so both were extracted and then dropped on the floor.

Neither value gets a new row: the WCM faculty template fixes the grant block at
eight rows and the mentee block at six. The identifier goes in Award Source,
whose own label reads "(funding agency ...; type of grant)", and the mentee's
awards go in Project/Accomplishments, whose template footnote reads "Optional:
List publications, awards, grants ... arising directly from the mentoring
activity."

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_unconsumed_grant_award_fields.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import docx

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.sections.mentoring import _normalize_mentee  # noqa: E402
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _generator():
    """A generator with just enough state to build one table."""
    gen = WCMTemplateGenerator.__new__(WCMTemplateGenerator)
    gen.doc = docx.Document()
    gen.verbose = False
    return gen


def _cells(table):
    return {row.cells[0].text: row.cells[1].text for row in table.rows}


def _grant(**fields):
    fields.setdefault("title", "Lysyl oxidase and pressure overload")
    return _cells(_generator()._create_grant_table(fields, "M2B"))


def _mentee(**fields):
    fields.setdefault("mentee_name", "Brian Wood")
    gen = _generator()
    anchor = gen.doc.add_paragraph("Past Mentees:")._element
    record = _normalize_mentee({"extracted_fields": fields})
    return _cells(gen._create_mentee_table(record, anchor))


# --- the grant identifier rides in Award Source --------------------------------

def test_grant_number_is_appended_to_the_award_source():
    # web107, real values: extracted at stage 4, present through stage 5d, and
    # absent from the rendered docx.
    rows = _grant(agency="NIH", grant_number="2P20RR018766-09-Kapusta")
    assert rows["Award Source:"] == "NIH (2P20RR018766-09-Kapusta)"


def test_grant_number_stands_alone_when_no_agency_was_extracted():
    rows = _grant(agency="", grant_number="R01HL123456")
    assert rows["Award Source:"] == "R01HL123456"


def test_grant_number_already_in_the_agency_is_not_repeated():
    # web136's agency really is "American Association of Critical Care Nurses
    # (AACN)" with grant_number "AACN" -- appending would print it twice.
    rows = _grant(agency="American Association of Critical Care Nurses (AACN)",
                  grant_number="AACN")
    assert rows["Award Source:"] == "American Association of Critical Care Nurses (AACN)"


def test_grant_number_already_in_the_title_is_not_repeated():
    rows = _grant(title="T-32 Post-doctoral training in implementation science",
                  agency="NIMH", grant_number="T-32")
    assert rows["Award Source:"] == "NIMH"


def test_award_source_is_untouched_when_no_grant_number_was_extracted():
    assert _grant(agency="NIH")["Award Source:"] == "NIH"


# --- the mentee's awards ride in Project/Accomplishments ------------------------

def test_mentee_awards_are_appended_to_project_accomplishments():
    rows = _mentee(research_focus="Renal denervation in resistant hypertension",
                   awards="AGS Presidential Poster Award, 2019")
    assert rows["Project/Accomplishments:"] == (
        "Renal denervation in resistant hypertension\n"
        "Awards: AGS Presidential Poster Award, 2019")


def test_funding_source_is_the_fallback_when_stage_4_used_that_key():
    # web131's Brian Wood: stage 4 wrote the award to funding_source, not awards.
    rows = _mentee(research_focus="Sepsis outcomes",
                   funding_source="Pillsbury Award")
    assert rows["Project/Accomplishments:"] == "Sepsis outcomes\nAwards: Pillsbury Award"


def test_mentee_award_stands_alone_when_no_project_was_extracted():
    assert _mentee(awards="AGS Award")["Project/Accomplishments:"] == "Awards: AGS Award"


def test_project_is_untouched_when_no_award_was_extracted():
    assert _mentee(research_focus="Sepsis outcomes")["Project/Accomplishments:"] == \
        "Sepsis outcomes"


def test_award_already_in_the_project_text_is_not_repeated():
    rows = _mentee(research_focus="Sepsis outcomes, AGS Award 2019",
                   awards="AGS Award 2019")
    assert rows["Project/Accomplishments:"] == "Sepsis outcomes, AGS Award 2019"


def test_a_role_line_fills_type_of_supervision_when_nothing_else_does():
    # #983: "Role: MPH Advisor" folded into the mentee entry had no field or row.
    entry = {"text": "Jane Roe, 2019-2021\tRole: MPH Advisor", "extracted_fields": {"mentee_name": "Jane Roe"}}
    assert _normalize_mentee(entry).supervision_type == "MPH Advisor"


def test_a_role_line_is_added_to_the_inferred_supervision_type():
    entry = {"text": "Jane Roe, 2019-2021\tRole: PhD Advisor", "extracted_fields": {"mentee_level": "PhD"}}
    assert _normalize_mentee(entry).supervision_type == "Research (PhD Advisor)"


def test_a_role_line_is_added_to_an_extracted_supervision_type():
    entry = {"text": "Jane Roe\nRole: Thesis Advisor", "extracted_fields": {"supervision_type": "Research"}}
    assert _normalize_mentee(entry).supervision_type == "Research (Thesis Advisor)"
    entry = {"text": "Jane Roe\tRole: research", "extracted_fields": {"supervision_type": "Research"}}
    assert _normalize_mentee(entry).supervision_type == "Research"


def test_without_a_role_line_supervision_type_is_inferred_as_before():
    entry = {"text": "Jane Roe, PhD student; her role: none", "extracted_fields": {"mentee_level": "PhD"}}
    assert _normalize_mentee(entry).supervision_type == "Research"
