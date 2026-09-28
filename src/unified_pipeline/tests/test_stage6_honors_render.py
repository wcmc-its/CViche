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
