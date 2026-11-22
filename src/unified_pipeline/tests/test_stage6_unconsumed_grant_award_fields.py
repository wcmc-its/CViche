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
import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6 import fan_out  # noqa: E402
from unified_pipeline.stage6.sections.mentoring import (  # noqa: E402
    _HEADING_VOCABULARY,
    HEADINGS_INTERRUPTED_KEY,
    MenteeRecord,
    _normalize_mentee,
    mark_interrupted_mentee_headings,
)
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _generator():
    """A generator with just enough state to build one table."""
    gen = WCMTemplateGenerator.__new__(WCMTemplateGenerator)
    gen.doc = docx.Document()
    gen.verbose = False
    gen.emit_comments = False  # the constructor default; a narrative would otherwise reach it
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


@pytest.mark.parametrize(("mentee_name", "mentee_level"), [
    ("Brian Wood, DO", "DO"),
    ("Brian Wood, M.D., Ph.D.", "M.D., Ph.D."),
    ("Brian Wood, MD, MPH", "MD, MPH"),
])
def test_a_level_the_name_already_shows_is_not_prefixed(mentee_name, mentee_level):
    # #2088: "DO - Fictional Emergency Medicine" repeated the Name row's degree.
    rows = _mentee(mentee_name=mentee_name, mentee_level=mentee_level,
                   site_position="Fictional Emergency Medicine")
    assert rows["Site/Position:"] == "Fictional Emergency Medicine"


def test_a_level_that_adds_to_the_name_is_still_prefixed():
    rows = _mentee(mentee_name="Brian Wood, PhD", mentee_level="PhD student",
                   site_position="Fictional Genetics Program")
    assert rows["Site/Position:"] == "PhD student - Fictional Genetics Program"
    rows = _mentee(mentee_name="Brian Wood", mentee_level="MD", site_position="Fictional Pediatrics")
    assert rows["Site/Position:"] == "MD - Fictional Pediatrics"
    # Case counts in the Name: an honorific "Ms." or a surname "Do" is no degree.
    rows = _mentee(mentee_name="Ms. Jane Do", mentee_level="MS", site_position="Fictional Biology")
    assert rows["Site/Position:"] == "MS - Fictional Biology"
    rows = _mentee(mentee_name="Jane Do", mentee_level="DO", site_position="Fictional Medicine")
    assert rows["Site/Position:"] == "DO - Fictional Medicine"


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


@pytest.mark.parametrize("visa", ["J-1 Research Scholar", "J1 research scholar", "H-1B", "F-1"])
def test_a_visa_category_in_funding_source_is_no_award_and_joins_site_position(visa):
    # #1715 WFOZBS-09: stage 4 filed "(J-1 Research Scholar)" as funding_source,
    # and it printed as "Awards: J-1 Research Scholar".
    rows = _mentee(research_focus="Melioidosis vaccines", site_position="Member (co-advisor)",
                   funding_source=visa)
    assert rows["Project/Accomplishments:"] == "Melioidosis vaccines"
    assert rows["Site/Position:"] == f"Member (co-advisor), {visa}"


def test_a_visa_category_site_position_already_holds_is_not_repeated():
    rows = _mentee(site_position="Bogor University, Member, J-1 Research scholar",
                   funding_source="J-1 Research Scholar")
    assert rows["Site/Position:"] == "Bogor University, Member, J-1 Research scholar"
    assert "Awards" not in rows["Project/Accomplishments:"]


@pytest.mark.parametrize("funding", ["M-1 summer externships", "NIH F31", "K01", "T32 Fellowship"])
def test_a_funding_source_that_only_looks_like_a_visa_is_still_an_award(funding):
    assert _mentee(funding_source=funding)["Project/Accomplishments:"] == f"Awards: {funding}"


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


def test_an_inline_supervision_value_beats_the_level_inference():
    # #1973, YTPMZK: the label and its value share one cell, no pipe.
    entry = {"text": "Name Jane Roe\tCurrent Position\tType of Supervision Clinical",
             "extracted_fields": {"mentee_level": "PhD"}}
    assert _normalize_mentee(entry, ongoing=True).supervision_type == "Clinical"
    entry["text"] = "Name Jane Roe\nType of Supervision: Teaching\tNext cell"
    assert _normalize_mentee(entry, ongoing=True).supervision_type == "Teaching"


def test_a_tab_after_the_template_label_is_an_inline_value():
    # #1973, 9TUVGW: the template label, a tab, then the value.
    entry = {"text": "Type of Supervision (Research, clinical, teaching, leadership)\tResearch and career advice",
             "extracted_fields": {"mentee_level": "Resident"}}
    assert _normalize_mentee(entry, ongoing=True).supervision_type == "Research and career advice"


def test_a_template_label_split_by_its_value_yields_the_value():
    # #1973, IZABPD: the cell read "(Research, <value>\tclinical, teaching, leadership)".
    entry = {"text": "Current Position Dentist\tType of Supervision (Research, Clinical\tclinical, teaching, leadership)",
             "extracted_fields": {"mentee_level": "PhD"}}
    assert _normalize_mentee(entry, ongoing=True).supervision_type == "Clinical"


@pytest.mark.parametrize("text", [
    "Project/Accomplishments:\tCurrent Position:\tType of Supervision:",  # B2RRRA: label, no value
    "Type of Supervision Type of Supervision ((rresearch, clinical, teaching, leadership)esearch",  # EH4XXA
    "Type of Supervision (Research, clinical, teaching, leadership)\t",
    "Type of Supervision (research, clinical, teaching, leadership) |",
    "Type of Supervision\tCurrent Position:",  # next label, not a value
    "Type of Supervision: N/A",
    "Type of Supervision -",
])
def test_a_supervision_label_without_a_value_falls_back_to_inference(text):
    entry = {"text": text, "extracted_fields": {"mentee_level": "Resident"}}
    assert _normalize_mentee(entry, ongoing=True).supervision_type == "Clinical"


def test_the_pipe_row_still_wins_over_an_inline_value():
    entry = {"text": "Type of Supervision Clinical\nType of Supervision (Research) | Research",
             "extracted_fields": {}}
    assert _normalize_mentee(entry, ongoing=True).supervision_type == "Research"


def _inferred_supervision(mentee_level="", site_position=""):
    fields = {"mentee_name": "Jane Roe", "mentee_level": mentee_level, "site_position": site_position}
    return _normalize_mentee({"text": "Jane Roe", "extracted_fields": fields}, ongoing=True).supervision_type


@pytest.mark.parametrize("site_position", [
    "Acme Pharma Institute",         # "ma" inside a word (#2066, OXCCQM)
    "Programs Office",               # "ms" inside a word
    "Northfield Medical Center, AMI",
    "Department of Pharmacology",
    "Primary Advisor",
])
def test_a_level_word_inside_another_word_infers_nothing(site_position):
    assert _inferred_supervision(site_position=site_position) == ""


@pytest.mark.parametrize("mentee_level", ["MS1", "MS3", "MS1-4", "MS-4", "ms-2 student", "MA-3",
                                          "Freshman", "Undergraduate major"])
def test_a_medical_student_year_or_a_major_is_not_a_masters_degree(mentee_level):
    assert _inferred_supervision(mentee_level) == ""


@pytest.mark.parametrize(("mentee_level", "expected"), [
    ("PhD student", "Research"),
    ("Predoctoral Trainee", "Research"),
    ("MSTP Student", "Research"),
    ("Postdoctoral Fellow", "Research"),
    ("Clinical fellow", "Research"),       # fellow is tried before clinical
    ("Surgery residents", "Clinical"),
    ("Vice President", ""),                # "resident" must start a word
    ("Preclinical student", ""),
    ("MS", "Research"),
    ("MSc candidate", "Research"),
    ("MSPH", "Research"),
    ("MSW", "Research"),
    ("MS-Epidemiology", "Research"),       # only a hyphen and a DIGIT is a student year (#2194)
    ("Masters student", "Research"),
])
def test_level_words_infer_the_supervision_type(mentee_level, expected):
    assert _inferred_supervision(mentee_level) == expected


@pytest.mark.parametrize("mentee_level", ["Ph.D.", "Ph. D.", "Ph D (joint)", "M.S.", "M.A. (Psychology)",
                                          "M.Sc. student", "M.S.P.H.", "B.S., M.S."])
def test_a_dotted_degree_in_the_level_is_research(mentee_level):
    assert _inferred_supervision(mentee_level) == "Research"


def test_a_resident_with_a_dotted_phd_stays_clinical():
    assert _inferred_supervision("M.D., Ph.D., PGY-2 surgery resident") == "Clinical"


def test_a_dotted_degree_in_site_position_counts_only_at_its_start():
    assert _inferred_supervision(site_position="Ph.D., Genetics Program") == "Research"
    assert _inferred_supervision(site_position="P.I. John Doe, Ph.D., Genetics") == ""


def test_site_position_naming_a_phd_backs_up_an_uninformative_level():
    # The level is a department, so it implies nothing; the site names the PhD.
    assert _inferred_supervision("Molecular Biology", "PhD student") == "Research"
    assert _inferred_supervision("Biostatistics", "Thesis Committee") == "Research"
    # Only the research-degree words are read there: a state is not a degree.
    assert _inferred_supervision("MPH Student", "Riverside Hospital, MA") == ""
    assert _inferred_supervision("MPH Student", "Clinical Research Unit") == ""


@pytest.mark.parametrize("site_position", [
    "Department of Clinical and Health Psychology",   # #2066: a department
    "Dept. of Psychology (Clinical)",
    "Paper published in Fictional Clinical Practice",  # a journal (ZDKMKD-05)
    "Assistant Clinical Professor",                    # a job title
    "Clinical Research Center",
    "Fictional University (Springfield, MA)",          # a state, not a degree
    "Fictional Pharma, Springfield, MA",
    "Fictional College, Jackson, MS",
])
def test_with_no_level_a_site_clinical_or_state_infers_nothing(site_position):
    assert _inferred_supervision(site_position=site_position) == ""


@pytest.mark.parametrize(("site_position", "expected"), [
    ("Clinical Mentor", "Clinical"),
    ("Clinical Education Advising", "Clinical"),
    ("Clinical Supervisor, Fictional Clinic", "Clinical"),
    ("Internal Medicine Resident", "Clinical"),
    ("Clinical fellow", "Research"),
    ("MS", "Research"),
    ("Fictional Masters in Medical Science Program", "Research"),
])
def test_with_no_level_a_site_naming_the_supervision_still_infers_it(site_position, expected):
    assert _inferred_supervision(site_position=site_position) == expected


# --- what the CV states outside a labelled field: role cells and sub-headings ---
# (#2155, #985)

def _stated(text="Jane Roe", hierarchy=("Mentoring",), context_heading=None, **fields):
    fields.setdefault("mentee_name", "Jane Roe")
    entry = {"text": text, "hierarchy": list(hierarchy), "extracted_fields": fields}
    if context_heading is not None:
        entry["context_heading"] = context_heading
    return _normalize_mentee(entry, ongoing=False)


@pytest.mark.parametrize(("text", "expected"), [
    ("Jane Roe   2019 - 2022   Surgery Resident\tFictional Hospital\tResearch mentor",
     "Research (Research mentor)"),
    ("Jane Roe | 2019 - 2022 | Clinical supervisor", "Clinical (Clinical supervisor)"),
    ("Jane Roe\nResearch supervisor and thesis advisor.", "Research (Research supervisor and thesis advisor)"),
    ("Jane Roe  2019-2021  Research mentor and Co-advisor for Dissertation",
     "Research (Research mentor and Co-advisor for Dissertation)"),
])
def test_an_unlabelled_role_cell_beats_the_level_guess(text, expected):
    # A resident's level alone reads "Clinical"; the line's own role cell says otherwise.
    assert _stated(text, mentee_level="Surgery Resident").supervision_type == expected


def test_an_unlabelled_role_cell_naming_no_category_joins_the_inferred_type():
    record = _stated("Jane Roe, MS  2012-2014  Co –advisor for Master’s dissertation", mentee_level="MS")
    assert record.supervision_type == "Research (Co –advisor for Master’s dissertation)"
    assert _stated("Jane Roe\tThesis advisor").supervision_type == "Thesis advisor"


@pytest.mark.parametrize("text", [
    "Jane Roe\tMentor",                                   # states no type of supervision
    "Jane Roe\tAdvisor",
    "Jane Roe\tMentor/Advisor",
    "Jane Roe\tFaculty mentor",
    "Jane Roe\tMentor: John Doe, PhD",                    # someone else's mentor
    "Jane Roe\tResearch mentor at the Fictional Institute for two years",
    "Jane Roe, research mentor",                          # not a cell of its own
    "Jane Roe\tClinical Research Coordinator",
    "Jane Roe\tResearch Mentorship Award 2020",
])
def test_a_cell_that_is_not_only_a_role_phrase_is_not_read(text):
    assert _stated(text).supervision_type == ""
    assert _stated(text, mentee_level="Resident").supervision_type == "Clinical"


def test_a_stated_value_still_beats_the_unlabelled_role_cell():
    record = _stated("Jane Roe\tResearch mentor", supervision_type="Teaching")
    assert record.supervision_type == "Teaching (Research mentor)"
    record = _stated("Jane Roe\tType of Supervision | Leadership\tResearch mentor")
    assert record.supervision_type == "Leadership (Research mentor)"
    # A labelled role line that names the category replaces the guess the same way.
    record = _stated("Jane Roe\tRole: Research Mentor", mentee_level="Resident")
    assert record.supervision_type == "Research (Research Mentor)"


@pytest.mark.parametrize("role", [
    "Research Mentor on the Fictional Scholars Board",   # more than a role phrase
    "Not a research mentor",
    "Research and clinical mentor",                       # names both
    "Research mentor, clinical supervisor",
    "Clinical mentor for research",
])
def test_a_role_that_is_not_only_one_category_of_role_names_no_category(role):
    # The guess from the level stands; the labelled role is still shown beside it.
    record = _stated(f"Jane Roe\tRole: {role}", mentee_level="Resident")
    assert record.supervision_type == f"Clinical ({role})"


@pytest.mark.parametrize(("hierarchy", "expected"), [
    (("Mentoring", "Past Research Supervision", "Ph.D. Students"), "Research"),
    (("Mentoring", "Research Supervision", "Completed"), "Research"),
    (("Mentoring", "Current Research Supervision", "Past and Present", "B. Nursing Fellows."),
     "Research"),
    (("FORMER AND CURRENT RESEARCH MENTEES",), "Research"),
    (("Mentoring", "Clinical Rotations"), "Clinical"),
    (("Mentoring", "Research Advisees:"), "Research"),
    (("Mentoring", "Residents and Fellows Clinical Supervision"), "Clinical"),
    (("Mentoring", "Master of Public Health Students Research Mentoring"), "Research"),
    (("Research Trainees (Fictional University and Imaginary College of Medicine)",), "Research"),
    (("Mentoring", "Clinical Preceptor (MD, PhD)"), "Clinical"),
    (("Teaching", "Dental Student Research Mentor"), "Research"),
    (("RESEARCH MENTORING",), "Research"),
    (("Research Trainees (Fictional University)",), "Research"),
    (("Mentoring", "Clinical Supervision"), "Clinical"),
    (("Mentoring", "3. Graduate/Postgraduate Research Mentorship"), "Research"),
    (("Mentoring", "Faculty Research Supervision"), "Research"),   # the category, though no level
    (("Mentoring", "Faculty Clinical Supervision"), "Clinical"),
    (("Mentoring", "Junior Faculty Research Mentoring"), "Research"),
    (("Research Mentoring", "Junior Faculty Mentees"), "Research"),  # a level heading is passed over
])
def test_a_heading_naming_the_kind_of_supervision_fills_the_type(hierarchy, expected):
    assert _stated(hierarchy=hierarchy).supervision_type == expected
    # ...and beats the guess from the level.
    assert _stated(hierarchy=hierarchy, mentee_level="Surgery Resident").supervision_type == expected


def test_no_heading_word_is_in_two_classes():
    # One code per word: a word listed twice would silently keep the later class.
    listed = [word for words in _HEADING_VOCABULARY.values() for word in words]
    assert len(listed) == len(set(listed))


def test_a_stamped_context_heading_is_read_before_the_hierarchy():
    record = _stated(hierarchy=("MENTORING",), context_heading="Research Advisor")
    assert record.supervision_type == "Research"
    record = _stated(hierarchy=("Clinical Mentoring",), context_heading="Research Advisor")
    assert record.supervision_type == "Research"


@pytest.mark.parametrize("hierarchy", [
    ("Mentoring",),
    ("Mentoring", "Clinical Fellows"),                    # a level, not a kind of supervision
    ("Mentoring", "Research Associates"),                 # a job
    ("Research",),                                        # the CV's research section
    ("Mentoring", "Clinical and Research Mentoring"),     # names both
    ("Research Mentoring", "Clinical Research Mentoring and Clinical Supervision"),
    ("Research Mentoring", "Thesis Committees"),          # committee service: nothing is read
    ("Research Supervision", "Comprehensive Exams of Doctoral Students"),
    ("Teaching", "Research Methods Course"),
    ("Mentoring", "Non-research Advisees"),               # negates the category
    ("Mentoring", "Non-Clinical Supervision"),
    ("Mentoring", "Non clinical mentoring"),
    ("Mentoring", "Nonclinical mentoring"),
    ("Mentoring", "Not research mentoring"),
    ("Mentoring", "Pre-clinical Supervision"),            # another category altogether
    ("Mentoring", "Post-clinical Supervision"),
    ("Mentoring", "Pre clinical Supervision"),
    ("Mentoring", "Post research mentoring"),
    ("Mentoring", "Other than research mentoring"),
    ("Mentoring", "Bench-research Mentees"),
    ("Mentoring", "Non-research and Research Mentoring"),
    ("Mentoring", "Research Training Grants"),            # a list of something else
    ("Mentoring", "Research Advisory Boards"),
    ("Mentoring", "Research Mentees' Awards"),
    ("Mentoring", "Clinical Training Sites"),
    ("Mentoring", "Clinical Rotation Sites"),
    ("Mentoring", "Research Training Program Committee"),
    ("Mentoring", "Clinical Trainers"),                   # not a whole supervision word
    ("Mentoring", "Research Supervisory Roles"),
    ("Mentoring", "Teaching and Research Supervision"),   # joined to another activity
    ("Mentoring", "Career and Research Mentoring"),
    ("Mentoring", "Education & Research Mentorship"),
    ("Mentoring", "Research Mentoring and Teaching"),
    ("Mentoring", "Research Mentoring/Teaching"),
    ("Mentoring", "Clinical Supervision, Teaching"),
    ("Mentoring", "Research or Clinical Supervision"),
    ("Mentoring", "Students and Research Mentoring"),
    ("Mentoring", "Research Fellows Mentoring"),          # the category word qualifies the level
    ("Mentoring", "Fellows' Research Mentoring"),         # a possessive is not a level word
    ("Mentoring", "Research Mentees' Supervision"),
    ("Mentoring", "Clinical Residents Supervision"),
    ("Mentoring", "Mentees excluding research trainees"), # a word outside the vocabulary
    ("Mentoring", "Trainees without research supervision"),
    ("Mentoring", "Advising outside of research supervision"),
    ("Mentoring", "No research mentees"),
    ("Mentoring", "All mentees except clinical trainees"),
    ("Mentoring", "Lack of Research Mentoring"),
    ("Mentoring", "Research Supervision of Staff"),
    ("Mentoring", "Research Mentoring 2019"),
    ("Mentoring", "Research Mentoring - Bench"),
    ("Mentoring", "Research Training"),
    ("Mentoring", "Research Trainees (not mine)"),        # a parenthetical that is none of the allowed kinds
    ("Mentoring", "Research Trainees (excluding fellows)"),
    ("Mentoring", "Research Trainees (2019)"),
    ("Mentoring", "Research Trainees (Declined Offers)"),
    ("Mentoring", "Clinical Supervision (Research)"),
    ("Research Mentoring", "Laboratory training"),       # only a level or status heading is passed over
    ("Research Mentoring", "Teaching Assistants"),
    ("Research Supervision", "Chair, Theses and Dissertations"),
    ("Research Mentoring", "Exemplary Projects"),
    ("Research Mentoring", "Non-research Advisees"),
    ("Research Mentoring", "Fellows (declined)"),
    ("Mentoring", "Research Mentees (Not At This School)"),  # a parenthetical that takes something out
    ("Mentoring", "Research Trainees (No Fictional University)"),
    ("Mentoring", "Research Trainees (Non Fictional University)"),
    ("Mentoring", "Research Trainees (Except Fictional College)"),
    ("Mentoring", "Research Trainees (Excluding Fictional Hospital)"),
    ("Mentoring", "Research Trainees (Without Fictional Institute)"),
    ("Mentoring", "Research Trainees (Other University)"),
    ("Research Mentoring", "Fellows (Not Advised At This Institute)"),
    ("Mentoring", "Research Mentors"),                    # a plural role noun: the owner's own mentors
    ("Mentoring", "Clinical Supervisors"),
    ("Mentoring", "Research Advisors"),
    ("Mentoring", "Research Advisers"),
    ("Mentoring", "Clinical Preceptors"),
    ("Research Mentoring", "Postdoctoral Mentors"),
    ("Research Mentoring", "Faculty Advising"),           # states no level, so it is not passed over
    ("Research Mentoring", "Faculty Supervision"),
])
def test_a_heading_that_names_no_single_category_changes_nothing(hierarchy):
    assert _stated(hierarchy=hierarchy).supervision_type == ""
    assert _stated(hierarchy=hierarchy, mentee_level="Resident").supervision_type == "Clinical"


def test_a_stated_value_beats_a_heading_category():
    hierarchy = ("Mentoring", "Research Supervision")
    assert _stated(hierarchy=hierarchy, supervision_type="Teaching").supervision_type == "Teaching"
    record = _stated("Jane Roe\tType of Supervision: Clinical", hierarchy=hierarchy)
    assert record.supervision_type == "Clinical"
    record = _stated("Jane Roe\tClinical supervisor", hierarchy=hierarchy)
    assert record.supervision_type == "Clinical (Clinical supervisor)"


@pytest.mark.parametrize(("heading", "expected"), [
    ("Fictional Faculty Sleep Medicine Mentor", "Fictional Faculty Sleep Medicine Mentor"),
    ("Honors Thesis Advisor", "Honors Thesis Advisor"),
    ("IX. CAPSTONE DIRECTOR OR SUPERVISOR", "CAPSTONE DIRECTOR OR SUPERVISOR"),
    ("Nursing Student Preceptor:", "Nursing Student Preceptor"),
])
def test_a_role_heading_fills_a_type_that_would_be_blank(heading, expected):
    assert _stated(hierarchy=("Mentoring", heading)).supervision_type == expected


@pytest.mark.parametrize("heading", [
    "Faculty Advisor", "Faculty Mentor", "Faculty Preceptor", "Faculty Supervisor",
    "Graduate Faculty Advisor", "Faculty Research Mentor", "Faculty Clinical Supervisor",
    "Senior Faculty Co-Mentor",
])
def test_a_role_noun_after_faculty_is_the_owners_role_not_a_level_or_category(heading):
    # "Faculty Advisor" may be the owner advising students. The heading
    # prints as the role it is, where the type would be blank, and no more.
    for hierarchy in (("Mentoring", heading), ("Research Mentoring", heading)):
        record = _stated(hierarchy=hierarchy, site_position="Fictional University")
        assert (record.site_position, record.supervision_type) == ("Fictional University", heading)
        assert _stated(hierarchy=hierarchy).site_position == ""
        assert _stated(hierarchy=hierarchy, mentee_level="Resident").supervision_type == "Clinical"


@pytest.mark.parametrize(("heading", "category"), [
    ("Faculty Advising", ""), ("Faculty Advisees", ""), ("Faculty Advisee", ""),
    ("Faculty Supervision", ""), ("Faculty Precepting", ""),
    ("Faculty Research Supervision", "Research"), ("Faculty Clinical Supervision", "Clinical"),
    ("Junior Faculty Research Advising", "Research"), ("Past Faculty Advisees", ""),
])
def test_advising_or_supervision_after_faculty_is_the_owners_activity_not_a_level(heading, category):
    # "Faculty Advising" is the ordinary term for advising of students BY a
    # faculty member. Only a mentoring word keeps "faculty" a level.
    hierarchy = ("Mentoring", heading)
    record = _stated(hierarchy=hierarchy, site_position="Fictional University")
    assert (record.site_position, record.supervision_type) == ("Fictional University", category)
    assert _stated(hierarchy=hierarchy).site_position == ""


@pytest.mark.parametrize("heading", [
    "Research and Graduate Student Supervision", "Research and Graduate Student Mentoring",
    "Research and Junior Faculty Mentoring", "Clinical and Medical Student Supervision",
    "Research & Medical Student Advising", "Graduate and Research Student Mentoring",
    "Research and Graduate Students", "Clinical and Graduate Faculty",
])
def test_a_category_joined_to_a_level_qualifier_states_no_level(heading):
    # An activity beside a level: the rows under it may be either kind.
    assert _stated(hierarchy=("Mentoring", heading)).site_position == ""


@pytest.mark.parametrize(("heading", "level"), [
    ("Research and Clinical Fellows", "Research and Clinical Fellows"),
    ("Adult and Pediatric Cardiology Fellows", "Adult and Pediatric Cardiology Fellows"),
])
def test_two_qualifiers_of_one_kind_joined_by_and_are_one_level(heading, level):
    assert _stated(hierarchy=("Mentoring", heading)).site_position == level


@pytest.mark.parametrize("hierarchy", [
    ("Mentoring", "Advisor"),                             # one word says nothing
    ("PhD Advisor", "Completed"),                         # only the innermost heading
    ("Mentoring", "Thesis Committee Advisor"),
    ("Mentoring", "Advisor, Fictional Scholars Program"),
    ("Mentoring", "Awards won as mentor"),
    ("Mentoring", "Trainees for whom I served as primary mentor"),
    ("Mentoring", "Postdoctoral Mentors"),                # a plural is no role of the owner's
    ("Mentoring", "Faculty Advisors"),
])
def test_a_heading_that_is_not_a_role_phrase_fills_nothing(hierarchy):
    assert _stated(hierarchy=hierarchy).supervision_type == ""


def test_a_role_heading_never_replaces_or_joins_a_type():
    hierarchy = ("Mentoring", "PhD Advisor")
    assert _stated(hierarchy=hierarchy, mentee_level="PhD").supervision_type == "Research"
    assert _stated(hierarchy=hierarchy, supervision_type="Teaching").supervision_type == "Teaching"


@pytest.mark.parametrize(("heading", "level"), [
    ("Postdoctoral Fellow", "Postdoctoral Fellow"),
    ("Undergraduate students", "Undergraduate students"),
    ("C. Post-doctoral Fellows.", "Post-doctoral Fellows"),
    ("Ph.D. students", "Ph.D. students"),
    ("Ph.D. Advising", "Ph.D."),
    ("Dental Student Research Mentor", "Dental Student"),
    ("Master's Students Advised", "Master's Students"),
    ("Doctoral Students (DrPH)", "Doctoral Students"),
    ("Former Graduate Students", "Graduate Students"),
    ("Mentor to Fellows", "Fellows"),
    ("Mentored faculty", "Faculty"),
    ("Adult and Pediatric Cardiology Fellows", "Adult and Pediatric Cardiology Fellows"),
    ("Master of Science and Master of Public Health Students",
     "Master of Science and Master of Public Health Students"),
    ("Doctor of Physical Therapy Students (DPT) (Mentor/Advisor)", "Doctor of Physical Therapy Students"),
    ("Undergraduate Advisees (role: thesis advisor):", "Undergraduate"),
    ("Nursing students (degree awarded)", "Nursing students"),
    ("Supervisor: Pharmacy Students", "Pharmacy Students"),
    ("Advisor to Dental Students (Clinical)", "Dental Students"),
    ("Past and Present Dental Students", "Dental Students"),
    ("Senior Faculty Mentees", "Senior Faculty"),
    ("Faculty Mentorship", "Faculty"),
    ("Faculty mentoring", "Faculty"),
    ("Junior Faculty Mentoring", "Junior Faculty"),
    ("Junior Faculty Mentees", "Junior Faculty"),
    ("Faculty Research Mentoring", "Faculty"),
    ("Faculty Mentored", "Faculty"),
    ("Residents Trained", "Residents"),
    ("Pharmacy Student Precepting", "Pharmacy Student"),
    ("Medical Student Clinical Supervision", "Medical Student"),
    ("Fellows (Fictional Knoll University)", "Fellows"),  # "no" inside a word takes nothing out
    ("Family Medicine Resident Mentoring", "Family Medicine Resident"),
    ("Radiology Resident Mentor", "Radiology Resident"),
    ("Fellows (Fictional University Hospital)", "Fellows"),
    ("PRE-DOCTORAL", "PRE-DOCTORAL"),
    ("High School Students:", "High School Students"),
])
def test_a_heading_naming_one_trainee_level_fills_a_missing_level(heading, level):
    record = _stated(hierarchy=("Mentoring", heading), site_position="Fictional University")
    assert record.site_position == f"{level} - Fictional University"
    assert _stated(hierarchy=("Mentoring", heading)).site_position == level


@pytest.mark.parametrize("hierarchy", [
    ("Mentoring",),
    ("Mentoring", "Students"),                            # the section's name, not a level
    ("STUDENT SUPERVISION",),
    ("Mentoring", "Other Students"),
    ("Mentoring", "Residents and fellows"),               # a mixed list
    ("Mentoring", "Residents or fellows"),
    ("Mentoring", "Students and Residents, former"),
    ("Mentoring", "Undergraduate/graduate students"),
    ("Mentoring", "Doctoral Students and Laboratory Technicians"),
    ("Mentoring", "Graduate Student Committees"),
    ("Mentoring", "Advisory Committee of Graduate Student"),
    ("Mentoring", "Prizes and Awards Won by Students"),
    ("Mentoring", "Research Fellows Trained, since 2001"),
    ("Mentoring", "Fictional Faculty Sleep Medicine Mentor"),
    ("Mentoring", "Program Advisor for MHA Students (list follows):"),
    ("Mentoring", "Research Trainees (Fictional University)"),
    ("Postdoctoral Fellows", "Laboratory Staff"),         # a parent group's level
    ("Postdoctoral Fellows", "Thesis Committees"),
    ("Mentoring", "Adult and Pediatric Fictional Fellows"),  # a word outside the vocabulary
    ("Mentoring", "No Fellows"),
    ("Mentoring", "Excluding Fellows"),
    ("Mentoring", "Non-Faculty Mentees"),
    ("Mentoring", "Visiting Scholars and Fellows"),
    ("Mentoring", "Fellows' Publications"),
    ("Mentoring", "Fellows' Research Mentoring"),
    ("Mentoring", "Fellows’ Mentoring"),
    ("Mentoring", "Awards to Fellows"),
    ("Mentoring", "Former Fellows now Faculty"),
    ("Mentoring", "Fellows Mentoring and Teaching"),
    ("Mentoring", "Teaching and Fellows Mentoring"),
    ("Mentoring", "Fellows 2019"),
    ("Mentoring", "Residents Fellows"),                   # two heads
    ("Mentoring", "Doctoral and Postdoctoral"),           # "and" joins qualifiers of one head only
    ("Mentoring", "Doctoral/Postdoctoral Research Mentorship"),
    ("Mentoring", "Fellows (declined)"),                  # a parenthetical of no allowed kind
    ("Mentoring", "Postdoctoral Fellows (not supervised)"),
    ("Mentoring", "Postdoctoral Fellows (Declined)"),
    ("Mentoring", "Postdoctoral Fellows (and technicians)"),
    ("Mentoring", "Faculty Advisor"),                     # the owner's role
    ("Mentoring", "Graduate Faculty Advisor"),
    ("Mentoring", "Faculty Research Mentor"),
    ("Mentoring", "Faculty Advising"),                    # ...or the owner's activity
    ("Mentoring", "Faculty Advisees"),
    ("Mentoring", "Faculty Supervision"),
    ("Mentoring", "Faculty Precepting"),
    ("Mentoring", "Faculty Research Supervision"),
    ("Mentoring", "Faculty Clinical Supervision"),
    ("Mentoring", "Faculty Research Advised"),         # a participle follows "faculty" directly
    ("Mentoring", "Postdoctoral Mentors"),                # a plural role noun: the owner's own mentors
    ("Mentoring", "Resident Advisors"),
    ("Mentoring", "Fellow Supervisors"),
    ("Mentoring", "Student Preceptors"),
    ("Mentoring", "Ph.D. Advisers"),
    ("Mentoring", "Fellows (Not Advised At This Institute)"),  # a parenthetical that takes something out
    ("Mentoring", "Fellows (No Fictional University)"),
    ("Mentoring", "Fellows (Non Fictional University)"),
    ("Mentoring", "Fellows (Except Fictional College)"),
    ("Mentoring", "Fellows (Excluding Fictional Hospital)"),
    ("Mentoring", "Fellows (Without Fictional Institute)"),
    ("Mentoring", "Fellows (Other University)"),
])
def test_a_heading_that_names_no_single_level_leaves_the_cell_alone(hierarchy):
    record = _stated(hierarchy=hierarchy, site_position="Fictional University")
    assert record.site_position == "Fictional University"
    assert _stated(hierarchy=hierarchy).site_position == ""


def test_an_extracted_level_beats_the_heading_level():
    record = _stated(hierarchy=("Mentoring", "Undergraduate students"),
                     mentee_level="PhD candidate", site_position="Fictional University")
    assert record.site_position == "PhD candidate - Fictional University"
    assert _stated(hierarchy=("Mentoring", "Fellows"), mentee_level="Resident").site_position == "Resident"


@pytest.mark.parametrize("fields", [
    {"site_position": "Postdoctoral Fellow, Fictional Institute"},
    {"site_position": "Fictional University, visiting student"},
    {"mentee_name": "Jane Roe (resident)", "site_position": "Fictional Hospital"},
    {"site_position": "Fictional Board Exam Office"},
    {"site_position": "Fictional Examinations Unit"},
])
def test_a_row_that_already_names_a_level_gains_none_from_the_heading(fields):
    record = _stated(hierarchy=("Mentoring", "Postdoctoral Fellows"), **fields)
    assert record.site_position == fields["site_position"]


def test_a_word_that_only_starts_like_exam_is_not_an_examination():
    record = _stated(hierarchy=("Mentoring", "Postdoctoral Fellows"), site_position="Example Lab")
    assert record.site_position == "Postdoctoral Fellows - Example Lab"
    # ...nor is such a heading examination service, which nothing is read from.
    assert _stated(hierarchy=("Mentoring", "Example Thesis Advisor")).supervision_type == "Example Thesis Advisor"
    assert _stated(hierarchy=("Mentoring", "Exam Thesis Advisor")).supervision_type == ""


def test_a_degree_the_name_shows_leaves_room_for_the_heading_level():
    # The Name row carries the degree, so the cell printed "MD" and no level.
    hierarchy = ("Mentoring", "Surgery Resident Research Mentor")
    record = _stated(hierarchy=hierarchy, mentee_name="Jane Roe, MD", mentee_level="MD")
    assert (record.name, record.site_position) == ("Jane Roe, MD", "Surgery Resident")
    assert record.supervision_type == "Research"
    record = _stated(hierarchy=hierarchy, mentee_name="Jane Roe, MD", mentee_level="MD",
                     site_position="Fictional Hospital")
    assert record.site_position == "Surgery Resident - Fictional Hospital"
    # With no level-naming heading the cell is what it was (#2088).
    record = _stated(mentee_name="Jane Roe, MD", mentee_level="MD")
    assert record.site_position == "MD"


def test_a_heading_level_does_not_feed_the_supervision_guess():
    # "Residents" as a heading states a level, not that the supervision was clinical.
    record = _stated(hierarchy=("Mentoring", "Residents"))
    assert (record.site_position, record.supervision_type) == ("Residents", "")


# --- a heading the mentee's own sub-heading interrupts is not read ---------------

_LIST = ("Mentoring", "Master's Research Students")


def _listed(index, code="N3B", hierarchy=_LIST, text="Jane Roe (Fictional University)", **extra):
    return {"element_idx_start": index, "element_idx_end": index, "taxonomy_code": code,
            "hierarchy": list(hierarchy), "text": text,
            "extracted_fields": {"mentee_name": "Jane Roe", "site_position": "Fictional University"},
            **extra}


def _marked(entries):
    mark_interrupted_mentee_headings([(entry, entry["taxonomy_code"]) for entry in entries])
    return [entry["element_idx_start"] for entry in entries if entry.get(HEADINGS_INTERRUPTED_KEY)]


def test_mentees_after_a_line_of_another_kind_lose_the_heading_above_it():
    # The K2 line is a sub-heading stage 1b left as an entry: the mentees
    # after it are the honours students, not master's students.
    entries = [_listed(10), _listed(11),
               _listed(12, code="K2", text="Undergraduate Honours Programme"),
               _listed(13), _listed(14, code="N3A")]
    assert _marked(entries) == [13, 14]
    before, after = (_normalize_mentee(entry, ongoing=False) for entry in (entries[1], entries[3]))
    assert before.site_position == "Master's Research Students - Fictional University"
    assert after.site_position == "Fictional University"


def test_an_interrupted_heading_gives_no_supervision_type_either():
    path = ("Mentoring", "Research Supervision")
    entries = [_listed(10, hierarchy=path), _listed(11, code="T", hierarchy=path, text="Clinic staff"),
               _listed(12, hierarchy=path)]
    assert _marked(entries) == [12]
    assert _normalize_mentee(entries[0], ongoing=False).supervision_type == "Research"
    assert _normalize_mentee(entries[2], ongoing=False).supervision_type == ""


def test_source_order_is_read_from_the_element_index_not_the_list():
    # A recovered table row is appended at the end of the entries; row 4 of
    # table 5 still precedes row 10.
    entries = [_listed("5.2"), _listed("5.10"), _listed("5.4", code="T", text="Attending physicians")]
    assert _marked(entries) == ["5.10"]
    # The first interrupting line counts, whichever the list holds last.
    entries = [_listed(10), _listed(12, code="K2"), _listed(20), _listed(30, code="T")]
    assert _marked(entries) == [20]


@pytest.mark.parametrize("other", [
    _listed(11, code="N4", text="Roe J. Poster, Fictional Society meeting"),   # the mentee's outcome
    _listed(11, code="K2", hierarchy=("Mentoring", "Doctoral Students")),       # another list
    _listed(11, code="K2", hierarchy=("Mentoring",)),
    _listed(13, code="K2"),                                                     # after the mentees
])
def test_a_line_that_does_not_interrupt_the_list_marks_nothing(other):
    assert _marked([_listed(10), other, _listed(12)]) == []


def test_a_mentee_with_its_own_stamped_heading_is_not_marked():
    # Stage 4 stamped the T sub-heading on the mentees under it, so their
    # innermost heading is known.
    entries = [_listed(10), _listed(11, code="T", text="Summer students"),
               _listed(12, context_heading="Summer students"), _listed(13)]
    assert _marked(entries) == [13]


def test_an_entry_with_no_comparable_index_interrupts_or_is_interrupted():
    assert _marked([_listed(10), _listed(None, code="K2"), _listed(12)]) == [10, 12]
    assert _marked([_listed(10), _listed(11, code="K2"), _listed("recovered")]) == ["recovered"]
    assert _marked([_listed(10), _listed("recovered")]) == []


def test_grouping_marks_the_interrupted_mentees():
    # The wire: `_group_entries_by_code` is what `generate()` renders from.
    entries = [_listed(10), _listed(11, code="K2", text="Undergraduate Honours Programme"), _listed(12)]
    groups = WCMTemplateGenerator(verbose=False)._group_entries_by_code(entries)
    levels = {entry["element_idx_start"]: _normalize_mentee(entry, ongoing=False).site_position
              for entry in groups["N3B"]}
    assert levels == {10: "Master's Research Students - Fictional University", 12: "Fictional University"}


def test_the_template_goals_row_joins_the_project():
    # #829, YDSVDB: no field holds the revised template's goals row; all 5 were lost.
    entry = {"text": "Name | Jane Roe\nProject/Accomplishments** | Archive move\n"
                     "Goals/expected Outcomes | Better archive access\n"
                     "Type of Supervision (Research, clinical, teaching, leadership) | Lead",
             "extracted_fields": {"research_focus": "Archive move"}}
    assert _normalize_mentee(entry, ongoing=True).project == "Archive move\nGoals: Better archive access"
    entry["extracted_fields"] = {}
    assert _normalize_mentee(entry, ongoing=True).project == "Goals: Better archive access"


def test_the_template_current_position_row_fills_an_n3a_mentee():
    # MOZYFL: the N3A schema has no current_position, so every Current Mentees row was blank.
    entry = {"text": "Name | Jane Roe\nCurrent Position | Simulation Fellowship Director\n"
                     "Type of Supervision (Research, clinical, teaching, leadership) | Teaching",
             "extracted_fields": {"mentee_name": "Jane Roe"}}
    assert _normalize_mentee(entry, ongoing=True).current_position == "Simulation Fellowship Director"
    entry["extracted_fields"]["current_position"] = "Attending"
    assert _normalize_mentee(entry, ongoing=True).current_position == "Attending"
    for blank in ("Current Position | \nType of Supervision | Lead", "Current Position | N/A",
                  "Current Position\tType of Supervision Clinical"):
        assert _normalize_mentee({"text": blank, "extracted_fields": {}}, ongoing=True).current_position == ""


def test_without_a_role_line_supervision_type_is_inferred_as_before():
    entry = {"text": "Jane Roe, PhD student; her role: none", "extracted_fields": {"mentee_level": "PhD"}}
    assert _normalize_mentee(entry, ongoing=True).supervision_type == "Research"


# --- #1574: a bare N3 rendered as a mentee keeps its thesis and its note ------
# The N3 schema writes `thesis_title` and `narrative`, which the N3A/N3B
# schemas do not; the Appendix line printed both (YUYVIG RLADNC, EBYSBC
# MQSUIC). Invented values.

def test_n3_thesis_title_is_the_project():
    rows = _mentee(thesis_title="Example thesis on widgets")
    assert rows["Project/Accomplishments:"] == "Example thesis on widgets"


def test_research_focus_still_wins_over_a_thesis_title():
    rows = _mentee(research_focus="Example focus", thesis_title="Example thesis")
    assert rows["Project/Accomplishments:"] == "Example focus"


def test_n3_narrative_follows_the_project():
    rows = _mentee(thesis_title="Example thesis", narrative="Example scholar program note")
    assert rows["Project/Accomplishments:"] == "Example thesis\nExample scholar program note"
    assert _mentee(narrative="Example note")["Project/Accomplishments:"] == "Example note"


def test_narrative_already_in_the_project_is_not_repeated():
    rows = _mentee(research_focus="Example thesis, example note", narrative="example note")
    assert rows["Project/Accomplishments:"] == "Example thesis, example note"


# --- a past mentee's off-schema outcome rides in Project/Accomplishments --------
# #1245 (pilot JNATFN, 8 of 12 N3B entries): stage 4 kept each mentee's
# accomplishment under `outcome`, which no row read, and the cell rendered
# blank. Invented values.

def test_an_off_schema_outcome_fills_project_accomplishments():
    assert _mentee(outcome="First authored an abstract")["Project/Accomplishments:"] == \
        "First authored an abstract"


def test_the_outcome_follows_the_project_and_precedes_the_awards():
    rows = _mentee(research_focus="Example focus", outcome="Co-authored a chapter",
                   awards="Example Award")
    assert rows["Project/Accomplishments:"] == (
        "Example focus\nCo-authored a chapter\nAwards: Example Award")


def test_an_outcome_the_project_already_holds_is_not_repeated():
    rows = _mentee(research_focus="Example focus; co-authored a chapter",
                   outcome="Co-authored a chapter")
    assert rows["Project/Accomplishments:"] == "Example focus; co-authored a chapter"


def test_a_list_outcome_is_joined_and_a_non_text_one_adds_nothing():
    assert _mentee(outcome=["An abstract", "A chapter"])["Project/Accomplishments:"] == \
        "An abstract; A chapter"
    assert _mentee(outcome={"type": "abstract"})["Project/Accomplishments:"] == ""


# --- a folded mentee's other records' lines ride in Project/Accomplishments ----
# #2067 (ZDKMKD): fan-out folds a mentee's accomplishment records into the
# head record's table and hands over each one's line as the CV writes it.
# Invented values.

def _folded_project(lines, **fields):
    entry = {"extracted_fields": {"mentee_name": "Wren Talbot", **fields},
             fan_out.FOLDED_MENTEE_LINES_KEY: lines}
    return _normalize_mentee(entry, ongoing=True).project


def test_folded_lines_follow_everything_the_cell_already_writes_in_order():
    assert _folded_project(["Poster shown at Ashby Meeting (2022)", "Abstract sent to Orchard Review (2023)"],
                           research_focus="Example focus", awards="Example Award") == (
        "Example focus\nAwards: Example Award\n"
        "Poster shown at Ashby Meeting (2022)\nAbstract sent to Orchard Review (2023)")


def test_folded_lines_stand_alone_in_an_empty_cell():
    assert _folded_project(["Poster shown at Ashby Meeting (2022)"]) == "Poster shown at Ashby Meeting (2022)"


def test_a_folded_line_the_cell_already_holds_is_not_repeated():
    assert _folded_project(["poster shown at ashby meeting (2022)", "Manuscript in preparation (2024)"],
                           research_focus="Poster shown at Ashby Meeting (2022)") == (
        "Poster shown at Ashby Meeting (2022)\nManuscript in preparation (2024)")


def test_no_folded_lines_leave_the_cell_as_it_was():
    assert _folded_project([], research_focus="Example focus") == "Example focus"
    assert _normalize_mentee({"extracted_fields": {"mentee_name": "Wren Talbot"}}, ongoing=True).project == ""


# --- #2004: a class-of Mentoring Period keeps the period for the #1205 planner --

#: A mentee line whose expected completion is the year its period renders.
_CLASS_LINE = ("Fenwick, Robin: Gait variability in example runners. "
               "(Thesis reader) Expected Graduation June 2024.")


def _class_record(text=_CLASS_LINE, **fields):
    fields.setdefault("end_date", "2024-06")
    return _normalize_mentee({"text": text, "extracted_fields": {
        "mentee_name": "Fenwick, Robin", "site_position": "Thesis reader", **fields}}, ongoing=False)


def test_a_class_of_mentoring_period_keeps_the_period_it_stands_in_for():
    record = _class_record()

    assert (record.mentoring_period, record.planner_period) == ("Expected Graduation June 2024", "2024")


@pytest.mark.parametrize("text, fields, period", [
    # no phrase: the cell prints the period
    ("Fenwick, Robin: Gait variability in example runners.", {}, "2024"),
    # a phrase whose year the period does not render alone
    (_CLASS_LINE, {"start_date": "2021"}, "2021-2024"),
])
def test_a_mentoring_period_printed_as_itself_leaves_the_planner_nothing(text, fields, period):
    record = _class_record(text, **fields)

    assert (record.mentoring_period, record.planner_period) == (period, "")


def _period_paragraph(table):
    (row,) = [row for row in table.rows if row.cells[0].text == "Mentoring Period:"]
    return row.cells[1].paragraphs[0]


def test_the_planner_reads_a_class_of_period_cell_as_the_period():
    gen = _generator()
    gen._planner_lines = {}
    table = gen._create_mentee_table(_class_record(), gen.doc.add_paragraph("Past Mentees:")._element)

    para = _period_paragraph(table)
    assert para.text == "Expected Graduation June 2024"  # the cell prints the phrase
    assert gen._planner_lines == {para._p: ("Expected Graduation June 2024", ("2024",))}


@pytest.mark.parametrize("record", [
    _class_record("Fenwick, Robin: Gait variability in example runners."),
    # a record built with no planner period: its cell prints the period
    MenteeRecord(name="Fenwick, Robin", mentoring_period="2024"),
])
def test_a_period_cell_printed_as_itself_is_left_to_the_planner_as_it_prints(record):
    gen = _generator()
    gen._planner_lines = {}
    table = gen._create_mentee_table(record, gen.doc.add_paragraph("Past Mentees:")._element)

    assert _period_paragraph(table).text == "2024"
    assert gen._planner_lines == {}
