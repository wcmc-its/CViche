"""Honors-table rendering fixes (#229): the multi-award fallback used to put
citation blobs in the name cell, leak state abbreviations/cities into the
Organization column, leave the year column empty, and duplicate the org
inside the name. Fixtures mirror the 2Q1_ZQ fused H entry with fictional
content.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_honors_render.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _render_honors(entries):
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    gen.doc.add_paragraph("H. HONORS AND AWARDS")
    table = gen.doc.add_table(rows=1, cols=3)
    for i, header in enumerate(["Name of award", "Organization",
                                "Date awarded (yyyy)"]):
        table.rows[0].cells[i].text = header
    gen._fill_honors(entries)
    return [[c.text for c in r.cells] for r in table.rows[1:]]


_FUSED_LINES = [
    # 2Q1_ZQ row-1 shape: citation blob with venue/city/state/month-year tail;
    # stage 4 extracted clean fields for this first award only.
    "Groundbreaking Widget Design Award – Runner-up Presentation. "
    "Alice Smith, Bob Jones. Groundbreaking Widget Design. "
    "Fictional Services University Education Day, Bethesda, MD, August 2025.",
    # leading year + org duplicated as the trailing comma segment
    "2020 AICT Outstanding Article Award, Association for Imaginary "
    "Communication and Technology (AICT)",
    # leading year RANGE
    "2015-2017 Imaginary Resources National Scholar Fellowship, "
    "The Fictional Benefactor Foundation",
    # org followed by a bare city segment
    "2013 Jerrold Q. Fake Fellowship, Made-up University, Bloomington",
]

_FUSED_ENTRY = {
    "taxonomy_code": "H",
    "text": "\n".join(_FUSED_LINES),
    "extracted_fields": {
        "award_name": "Groundbreaking Widget Design Award – Runner-up Presentation",
        "granting_body": "Fictional Services University Education Day",
        "date": "2025",
    },
}


def test_fused_entry_renders_clean_rows():
    rows = _render_honors([_FUSED_ENTRY])
    assert len(rows) == 4

    # Row 1: stage-4 fields used verbatim — no citation blob, no 'MD' org
    assert rows[0] == ["Groundbreaking Widget Design Award – Runner-up Presentation",
                       "Fictional Services University Education Day", "2025"]

    # Row 2: leading year extracted, org not duplicated in the name cell
    assert rows[1][0] == "AICT Outstanding Article Award"
    assert rows[1][1] == ("Association for Imaginary Communication and "
                          "Technology (AICT)")
    assert rows[1][2] == "2020"

    # Row 3: leading year range extracted
    assert rows[2][0] == "Imaginary Resources National Scholar Fellowship"
    assert rows[2][1] == "The Fictional Benefactor Foundation"
    assert rows[2][2].startswith("2015")

    # Row 4: org is the university, not the city; city tail stripped from name
    assert rows[3][0] == "Jerrold Q. Fake Fellowship"
    assert rows[3][1] == "Made-up University"
    assert rows[3][2] == "2013"


def test_single_award_trailing_year_with_period():
    entry = {"taxonomy_code": "H",
             "text": "Best Poster Award, Imaginary Society of Things, 2021.",
             "extracted_fields": {}}
    rows = _render_honors([entry])
    assert rows == [["Best Poster Award", "Imaginary Society of Things", "2021"]]


def test_org_extraction_never_returns_state_abbrev_or_city():
    gen = WCMTemplateGenerator(verbose=False)
    # an institutional-keyword segment wins over later city/state segments
    # (the old single-pass returned 'MD' / 'Bloomington' first, #229)
    assert gen._extract_organization_from_award(
        "Best Talk Award, Made-up University, Bloomington, MD") == \
        "Made-up University"
    # no IKW anywhere: state abbrevs and digit segments rejected, proper-noun
    # fallback still works
    assert gen._extract_organization_from_award(
        "Some Prize, Weill Cornell, NY") == "Weill Cornell"


def test_split_award_year_shapes():
    gen = WCMTemplateGenerator(verbose=False)
    assert gen._split_award_year("2020 Great Award, Some Org") == (
        "Great Award, Some Org", "2020")
    assert gen._split_award_year("2015-2017 Long Fellowship") == (
        "Long Fellowship", "2015-2017")
    assert gen._split_award_year("Venue Day, Bethesda, August 2025.") == (
        "Venue Day, Bethesda", "2025")
    assert gen._split_award_year("No year here at all") == (
        "No year here at all", "")


def test_892_a_pii_withheld_award_leaves_no_row_for_the_entry():
    """The PII pass dropped award_name (an O-1 visa); the cut-text remnant,
    the USCIS org and the year must not render as an honors row."""
    from unified_pipeline.stage6.pii_pass import run_pii_pass
    from unified_pipeline.stage_6_word_template import (
        RENDER_ROUTED_CODES, TAXONOMY_TO_SECTION)
    entry = {"taxonomy_code": "H",
             "text": "2019 Extraordinary Ability in Sciences, O-1 Visa | "
                     "U.S. Citizen & Immigration Service (USCIS)",
             "extracted_fields": {
                 "award_name": "Extraordinary Ability in Sciences, O-1 Visa",
                 "granting_body": "U.S. Citizen & Immigration Service (USCIS)",
                 "date": "2019"}}
    keeper = {"taxonomy_code": "H", "text": "",
              "extracted_fields": {"award_name": "Teaching Award",
                                   "granting_body": "Example University",
                                   "date": "2018"}}
    run_pii_pass({"H": [entry, keeper]}, routed_codes=RENDER_ROUTED_CODES,
                 section_names=TAXONOMY_TO_SECTION)
    assert _render_honors([entry, keeper]) == [
        ["Teaching Award", "Example University", "2018"]]


def test_org_not_fabricated_from_the_award_name_itself():
    """#887: with no granting body in the text, an award named after a
    college or university must not become its own organization."""
    gen = WCMTemplateGenerator(verbose=False)
    # the award name minus its trailing award word (no digit, no role word:
    # only the equals-the-award-name guard can catch this one)
    assert gen._extract_organization_from_award(
        "College of Education Outstanding Thesis Award") == ""
    # a digit in the built organization (a year is the date column's)
    assert gen._extract_organization_from_award(
        "2016 College of Education 2015 Outstanding Thesis Award") == ""
    assert gen._extract_organization_from_award(
        "College of Education 2015 Outstanding Thesis Award") == ""
    # a role word ends the built organization
    assert gen._extract_organization_from_award(
        "University of Western Ontario Representative, "
        "Ontario Undergraduate Student Alliance") == ""


def test_org_extraction_still_finds_a_real_grantor_after_887_guards():
    gen = WCMTemplateGenerator(verbose=False)
    # a grantor that is a PREFIX of the award name (not the whole name)
    assert gen._extract_organization_from_award(
        "Society of Fictional Medicine Mentoring Award") == \
        "Society of Fictional Medicine"
    assert gen._extract_organization_from_award(
        "Best Talk Award, Made-up University") == "Made-up University"


def test_single_award_with_no_granting_body_renders_empty_org_887():
    entry = {"taxonomy_code": "H",
             "text": "College of Education 2015 Outstanding Thesis Award",
             "extracted_fields": {
                 "award_name": "College of Education 2015 Outstanding "
                               "Thesis Award",
                 "granting_body": None, "date": "2016"}}
    rows = _render_honors([entry])
    assert rows == [["College of Education 2015 Outstanding Thesis Award",
                     "", "2016"]]


# --------------------------------------------------------------------------
# #1245 / EBYSBC class E15: dates under keys outside the H schema, and one
# stage-4 award record split into several rows. Every value is invented.
# --------------------------------------------------------------------------

def _h_entry(text, **fields):
    return {"taxonomy_code": "H", "text": text, "extracted_fields": fields}


def test_a_start_end_range_renders_when_the_schema_date_is_empty():
    """KDAZOM-04 / VVRTUC-03 / OTBUCZ-01 shape: stage 4 put the range under
    start_date/end_date and left `date` empty; the text's "2011-13" is not a
    year the text fallback can read, so the cell used to be empty."""
    rows = _render_honors([
        _h_entry("Imaginary Teaching Prize, Fictional Board 2011-13",
                 award_name="Imaginary Teaching Prize",
                 granting_body="Fictional Board", date=None,
                 start_date="2011", end_date="2013"),
        _h_entry("Pretend Honor Roll, Made-up School 2017-",
                 award_name="Pretend Honor Roll", granting_body="Made-up School",
                 start_date="2017-09", end_date="present"),
    ])
    assert rows == [
        ["Pretend Honor Roll", "Made-up School", "2017-Present"],
        ["Imaginary Teaching Prize", "Fictional Board", "2011-2013"],
    ]


def test_an_end_date_with_no_start_closes_the_range_date_opens():
    """XWNZWW-09 shape: `date` plus an `end_date` and no `start_date` is one range. Next to a
    start_date of its own, `date` is the award's date and wins."""
    rows = _render_honors([
        _h_entry("Named in Pretend Directory 2001-2004",
                 award_name="Named in Pretend Directory", granting_body="Imaginary Press",
                 date="2001", end_date="2004"),
        _h_entry("2016 Made-up Fellow, Fictional Institute (2016-2017)",
                 award_name="Made-up Fellow", granting_body="Fictional Institute",
                 date="2016", start_date="2016", end_date="2017"),
    ])
    assert rows == [
        ["Made-up Fellow", "Fictional Institute", "2016"],
        ["Named in Pretend Directory", "Imaginary Press", "2001-2004"],
    ]


def test_a_dates_list_renders_every_date_it_holds():
    """HZGJFM-02 (a list of spans) and BZZNRL-06 (a '; ' string): the text
    fallback kept only the last year, or none for a "1993-94" span."""
    rows = _render_honors([
        _h_entry("Pretend Top Clinician 1993-94, 2008-2009",
                 award_name="Pretend Top Clinician", date=None,
                 dates=[{"start_date": "1993", "end_date": "1994"},
                        {"start_date": "2008", "end_date": "2009"}]),
        _h_entry("Imaginary Teaching Award, 2012, 2013",
                 award_name="Imaginary Teaching Award", date=None,
                 dates="2012; 2013"),
        _h_entry("Fictional Prize",
                 award_name="Fictional Prize", additional_dates="2008; 2007"),
    ])
    assert [row[2] for row in rows] == [
        "1993-1994, 2008-2009", "2012, 2013", "2008, 2007"]


def test_each_line_of_one_award_won_in_several_years_keeps_its_own_year():
    """ZDCXIV-03: every line naming stage 4's award rendered stage 4's one
    date. ZDCXIV-02: the organization's own line above them rendered as an
    award of its own."""
    rows = _render_honors([_h_entry(
        "Fictional College of Pretend Studies\n"
        "Made-up Faculty Award — 06/11/2019\n"
        "Made-up Faculty Award — 05/14/2018",
        award_name="Made-up Faculty Award",
        granting_body="Fictional College of Pretend Studies",
        date="2019-06-11", additional_dates="2018-05-14")])
    assert rows == [
        ["Made-up Faculty Award", "Fictional College of Pretend Studies", "2019"],
        ["Made-up Faculty Award", "Fictional College of Pretend Studies", "2018"],
    ]


def test_a_line_keeps_its_own_trailing_year_or_date_column():
    """The same rule for the other two ways a line dates itself: a trailing
    year (`_split_award_year`) and a '|' date column (the parsed cell)."""
    for text in ("Made-up Faculty Award, 2019\nMade-up Faculty Award, 2018",
                 "Made-up Faculty Award | 2019\nMade-up Faculty Award | 2018"):
        rows = _render_honors([_h_entry(
            text, award_name="Made-up Faculty Award", date="2019",
            additional_dates="2018")])
        assert [row[2] for row in rows] == ["2019", "2018"], text


def test_the_granting_body_line_is_matched_whatever_its_case_or_full_stop():
    rows = _render_honors([_h_entry(
        "FICTIONAL COLLEGE OF PRETEND STUDIES.\n"
        "Made-up Faculty Award — 06/11/2019\n"
        "Made-up Faculty Award — 05/14/2018",
        award_name="Made-up Faculty Award",
        granting_body="Fictional College of Pretend Studies",
        date="2019-06-11", additional_dates="2018-05-14")])
    assert [row[0] for row in rows] == ["Made-up Faculty Award"] * 2


def test_one_award_written_over_several_lines_is_one_row():
    """ZDCXIV-02: "Award", then "Organization — date", or a poster title and
    its authors above the dated line, rendered one row per line."""
    rows = _render_honors([
        _h_entry("Imaginary Pharmacy Scholarship\n"
                 "Fictional University, College of Pretend — 07/09/2010",
                 award_name="Imaginary Pharmacy Scholarship",
                 granting_body="Fictional University, College of Pretend",
                 date="2010-07-09"),
        _h_entry("Pretend Poster Prize Runner-up\n"
                 "A Made-up Study of Widget Costs\n"
                 "Doe J, Roe R, Poe Q\n"
                 "Imaginary Congress of Widgets, Nowhere — 03/2012",
                 award_name="Pretend Poster Prize Runner-up",
                 granting_body="Imaginary Congress of Widgets", date="2012"),
        _h_entry("Imaginary Counseling Contest\nFirst Prize — 02/2009",
                 award_name="First Prize, Imaginary Counseling Contest",
                 granting_body="Pretend Society", date="2009-02"),
    ])
    assert rows == [
        ["Pretend Poster Prize Runner-up", "Imaginary Congress of Widgets", "2012"],
        ["Imaginary Pharmacy Scholarship",
         "Fictional University, College of Pretend", "2010"],
        ["First Prize, Imaginary Counseling Contest", "Pretend Society", "2009"],
    ]


def test_a_list_whose_only_date_is_on_the_extracted_award_keeps_every_award():
    """The guard on the one-row reading: stage 4 extracted the last award of
    a fused list, and that award's own line is the only dated one. A curly
    quote in the source must not hide that line (stage 4 wrote a straight
    one), which would collapse the list into its last award. The year is
    written in free text as well as in a '|' date column: the column alone
    is caught earlier, by the date-cell guard, and would not reach this one."""
    for dated_line in ("Doe’s Neighbourhood Service Prize | 2015",
                       "Doe’s Neighbourhood Service Prize, 2015"):
        rows = _render_honors([_h_entry(
            "Pretend Mentoring Certificate\n"
            "Imaginary Service Medal\n" + dated_line,
            award_name="Doe's Neighbourhood Service Prize", date="2015")])
        assert [row[0] for row in rows] == [
            "Pretend Mentoring Certificate", "Imaginary Service Medal",
            "Doe's Neighbourhood Service Prize"], dated_line
        assert rows[2][2] == "2015"


def test_an_undated_list_is_still_read_as_a_list():
    rows = _render_honors([_h_entry(
        "Pretend Teaching Prize\nImaginary Service Medal",
        award_name="Pretend Teaching Prize")])
    assert [row[0] for row in rows] == [
        "Pretend Teaching Prize", "Imaginary Service Medal"]


def test_a_list_naming_years_the_record_does_not_have_stays_a_list():
    """Stage 4's award is on an undated line here, so only the dates tie
    the other lines to it -- and they are not the record's own."""
    rows = _render_honors([_h_entry(
        "Pretend Teaching Prize\nImaginary Service Medal 2014\n"
        "Fictional Research Cup 2011",
        award_name="Pretend Teaching Prize", date="2016")])
    assert sorted(row[0] for row in rows) == [
        "Fictional Research Cup", "Imaginary Service Medal",
        "Pretend Teaching Prize"]


def test_a_list_sharing_one_year_on_its_own_line_keeps_every_award():
    """The years-on-their-own-lines shape (#229): one bare year, before or
    after the awards, can be every award's. Every date the text names is
    the record's and stage 4's award line is undated, so without the
    date-cell guard this read as one award and lost the other line."""
    for text in ("2015\nPretend Medal\nImaginary Prize",
                 "Pretend Medal\nImaginary Prize\n2015"):
        rows = _render_honors([_h_entry(
            text, award_name="Imaginary Prize", date="2015")])
        assert [row[0] for row in rows] == ["Pretend Medal", "Imaginary Prize"]
        assert [row[2] for row in rows] == ["2015", "2015"]


def test_a_list_with_a_row_dated_in_its_own_column_keeps_every_award():
    """A tab- or '|'-joined row whose last cell is its date is an award with
    its own date column (the module's delimiter contract), not a
    continuation line of stage 4's award."""
    for text in ("Pretend Medal\t2015\nImaginary Prize",
                 "Pretend Medal | 2015\nImaginary Prize"):
        rows = _render_honors([_h_entry(
            text, award_name="Imaginary Prize", date="2015")])
        assert [row[0] for row in rows] == ["Pretend Medal", "Imaginary Prize"]


def test_a_pipe_cell_that_is_not_a_date_does_not_keep_one_award_split():
    """A '|' shape the parser does not recognise (a leading date) hands its
    second cell over as a "year". That cell is a note, not a date cell, so
    the award, its note and its citation line stay one row."""
    rows = _render_honors([_h_entry(
        "2012 | Pretend supervisor of the winning entry\n"
        "Pretend Poster Prize, Imaginary Congress\n"
        "Doe J, Roe R. A Made-up Study. Imaginary Congress 2012",
        award_name="Pretend Poster Prize",
        granting_body="Imaginary Congress", date="2012")])
    assert rows == [["Pretend Poster Prize", "Imaginary Congress", "2012"]]


def test_an_entry_with_no_extracted_award_is_never_read_as_one_award():
    """With no stage-4 award there is no line to tie the others to: the
    one-award reading would put the whole raw text, newline and all, into
    one name cell."""
    rows = _render_honors([_h_entry(
        "Pretend Medal\nImaginary Prize 2015", award_name=None, date="2015")])
    assert [row[0] for row in rows] == ["Pretend Medal", "Imaginary Prize"]
    assert rows[1][2] == "2015"


def test_a_fanned_out_child_is_one_row_with_its_own_dates():
    """ZCTARO-04: fan-out gives the first record the built line "Award | Org |
    YYYY-MM | YYYY-MM", which the '|' parser split into four rows, and the
    parent kept the first award's year from the raw text."""
    from unified_pipeline.stage4.schemas import STAGE4_RECORDS_KEY
    first = {"award_name": "Pretend Investigator",
             "granting_body": "Imaginary Foundation, Nowhere",
             "date": None, "start_date": "1998-03", "end_date": "2002-08"}
    last = {"award_name": "Senior Made-up Scholar",
            "granting_body": "Fictional Coalition",
            "date": None, "start_date": "2003-09", "end_date": "2007-05"}
    entry = {"taxonomy_code": "H", "element_idx_start": 7,
             "text": "1998.03 - 2002.08 Pretend Investigator, Imaginary "
                     "Foundation, Nowhere 2003.09 - 2007.05 Senior "
                     "Made-up Scholar, Fictional Coalition",
             "extracted_fields": {**last, STAGE4_RECORDS_KEY: [first, last]}}
    grouped = WCMTemplateGenerator(verbose=False)._group_entries_by_code([entry])
    assert _render_honors(grouped["H"]) == [
        ["Senior Made-up Scholar", "Fictional Coalition", "2003-2007"],
        ["Pretend Investigator", "Imaginary Foundation, Nowhere", "1998-2002"],
    ]


def test_a_date_only_fragment_keeps_an_empty_name_cell():
    """A stage-2 date continuation coded H: the range now comes from stage
    4's fields, and the fragment is still peeled off the name cell rather
    than repeated there."""
    rows = _render_honors([_h_entry("1971- 1972.", award_name=None,
                                    start_date="1971", end_date="1972")])
    assert rows == [["", "", "1971-1972"]]
