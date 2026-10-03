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
    record = _normalize_mentee({"extracted_fields": fields}, ongoing=True)
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


# --- the mentee's site_position is not cut to its level (class E14, EBYSBC) ------

def test_a_site_position_that_contains_the_level_renders_whole():
    # Rendered "Fellow" and lost the fellowship type: 12 rows on one CV.
    rows = _mentee(mentee_level="Fellow", site_position="Fictional Nephrology Fellow")
    assert rows["Site/Position:"] == "Fictional Nephrology Fellow"


def test_a_site_position_that_contains_the_level_in_another_case_renders_whole():
    rows = _mentee(mentee_level="Assistant Professor",
                   site_position="assistant professor, Fictional Mentoring Committee")
    assert rows["Site/Position:"] == "assistant professor, Fictional Mentoring Committee"


def test_a_site_position_and_a_level_that_differ_still_combine():
    rows = _mentee(mentee_level="PhD", site_position="Thesis")
    assert rows["Site/Position:"] == "PhD - Thesis"


def test_a_level_that_is_only_part_of_a_word_is_not_contained():
    # "Postdoc" inside "Postdoctoral advisor" is another word, so the two
    # combine as values that differ rather than the level being dropped.
    rows = _mentee(mentee_level="Postdoc", site_position="Postdoctoral advisor")
    assert rows["Site/Position:"] == "Postdoc - Postdoctoral advisor"


def test_either_value_alone_renders_as_it_is():
    assert _mentee(mentee_level="Fellow")["Site/Position:"] == "Fellow"
    assert _mentee(site_position="Fictional Fellow")["Site/Position:"] == "Fictional Fellow"


# --- an institution or advisor under an off-schema key joins Site/Position ------
# (class E14, EBYSBC HFAJCC-09 / ZCTARO-07: no renderer read either key)

def test_an_off_schema_institution_joins_site_position():
    rows = _mentee(mentee_level="BSc", site_position="Summer Project",
                   institution="Fictional Lakeside University")
    assert rows["Site/Position:"] == "BSc - Summer Project, Fictional Lakeside University"


def test_an_institution_alone_fills_site_position():
    assert _mentee(institution="Fictional Lakeside University")["Site/Position:"] == (
        "Fictional Lakeside University")


def test_an_off_schema_advisor_joins_site_position_with_its_label():
    rows = _mentee(site_position="Fictional Biology", advisor="Dana Quill")
    assert rows["Site/Position:"] == "Fictional Biology, Advisor: Dana Quill"
    rows = _mentee(site_position="Fictional Biology", advisors=["Dana Quill", "Lee Marsh"])
    assert rows["Site/Position:"] == "Fictional Biology, Advisors: Dana Quill; Lee Marsh"


def test_institution_then_advisor_in_that_order():
    rows = _mentee(site_position="Fictional Biology", advisor="Dana Quill",
                   institution="Fictional Lakeside University")
    assert rows["Site/Position:"] == (
        "Fictional Biology, Fictional Lakeside University, Advisor: Dana Quill")


def test_a_value_site_position_already_holds_is_not_repeated():
    rows = _mentee(site_position="Fictional Biology, Advisor: Dana Quill", advisor="dana quill",
                   institution="Fictional Biology")
    assert rows["Site/Position:"] == "Fictional Biology, Advisor: Dana Quill"


def test_a_blank_or_non_text_extra_adds_nothing():
    for blank in ("", "   ", None, {"name": "Dana Quill"}, [], [None, ""], 7):
        rows = _mentee(site_position="Fictional Biology", advisor=blank, institution=blank)
        assert rows["Site/Position:"] == "Fictional Biology", blank


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
    assert _normalize_mentee(entry, ongoing=True).supervision_type == "MPH Advisor"


def test_a_role_line_is_added_to_the_inferred_supervision_type():
    entry = {"text": "Jane Roe, 2019-2021\tRole: PhD Advisor", "extracted_fields": {"mentee_level": "PhD"}}
    assert _normalize_mentee(entry, ongoing=True).supervision_type == "Research (PhD Advisor)"


def test_a_role_line_is_added_to_an_extracted_supervision_type():
    entry = {"text": "Jane Roe\nRole: Thesis Advisor", "extracted_fields": {"supervision_type": "Research"}}
    assert _normalize_mentee(entry, ongoing=True).supervision_type == "Research (Thesis Advisor)"
    entry = {"text": "Jane Roe\tRole: research", "extracted_fields": {"supervision_type": "Research"}}
    assert _normalize_mentee(entry, ongoing=True).supervision_type == "Research"


def test_the_template_supervision_row_beats_the_level_inference():
    # FINSIS: a resident's table row read "Research + Teaching"; inference said "Clinical".
    entry = {"text": "Name | Jane Roe\nSite/Position | Cornell - Resident\n"
                     "Type of Supervision (Research, clinical, teaching, leadership) | Research + Teaching",
             "extracted_fields": {"mentee_level": "Resident"}}
    assert _normalize_mentee(entry, ongoing=True).supervision_type == "Research + Teaching"


def test_an_empty_template_supervision_row_falls_back_to_inference():
    entry = {"text": "Name | Jane Roe\nType of Supervision (Research, clinical, teaching, leadership) | \n"
                     "Name | Next Mentee",
             "extracted_fields": {"mentee_level": "Resident"}}
    assert _normalize_mentee(entry, ongoing=True).supervision_type == "Clinical"


def test_without_a_role_line_supervision_type_is_inferred_as_before():
    entry = {"text": "Jane Roe, PhD student; her role: none", "extracted_fields": {"mentee_level": "PhD"}}
    assert _normalize_mentee(entry, ongoing=True).supervision_type == "Research"
