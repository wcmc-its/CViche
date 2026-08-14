"""Employment Status entries route to the row their OWN label names (#571).

`_fill_employment_status` used to write every entry into the first paragraph
containing 'employer' -- on the WCM template always "Name of Current
Employer(s):" -- so a Position/Title entry and a Dates entry both overwrote the
employer row, last write wins, and the surviving value was filed under a label
that is not its own. It also counted every one of those writes as inserted.

These tests drive the real fill path (`_fill_passthrough_sections`) against a
real python-docx Document, once with the issue's synthetic multi-row block and
once with the committed October-2022 WCM template itself.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_employment_status_routing.py -p no:cacheprovider
"""

import logging
import sys
from pathlib import Path

import docx
import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.sections.passthrough import PassthroughSection  # noqa: E402
from unified_pipeline.stage_6_word_template import TEMPLATE_PATH  # noqa: E402


class _StubGenerator(PassthroughSection):
    """Just enough of WCMTemplateGenerator to drive the passthrough fill."""

    def __init__(self, doc):
        self.doc = doc
        self.verbose = False
        self.stats = {"entries_inserted": 0, "tables_populated": 0}

    def _find_paragraph_with_text(self, search_text):
        for i, para in enumerate(self.doc.paragraphs):
            if search_text.lower() in para.text.lower():
                return i
        return None


def _entry(text: str) -> dict:
    return {"text": text, "hierarchy": ["E. EMPLOYMENT STATUS"], "extracted_fields": {}}


def _block_document():
    """The issue's reproduction block: header plus three labelled rows."""
    doc = docx.Document()
    for line in (
        "E. EMPLOYMENT STATUS",
        "Name of Current Employer(s):",
        "Position/Title:",
        "Dates of Employment:",
    ):
        doc.add_paragraph(line)
    return doc


def _texts(doc) -> list[str]:
    return [p.text for p in doc.paragraphs]


def test_each_entry_lands_in_its_own_row():
    doc = _block_document()
    generator = _StubGenerator(doc)

    generator._fill_passthrough_sections([
        _entry("Name of Current Employer(s): Weill Cornell Medicine"),
        _entry("Position/Title: Professor of Medicine"),
        _entry("Dates of Employment: 2011-Present"),
    ])

    assert "Name of Current Employer(s):\tWeill Cornell Medicine" in _texts(doc)
    assert "Position/Title:\tProfessor of Medicine" in _texts(doc)
    assert "Dates of Employment:\t2011-Present" in _texts(doc)
    assert generator.stats["entries_inserted"] == 3


def test_dates_entry_does_not_overwrite_the_employer_row():
    """The exact defect: a lone Dates entry used to land in the employer row."""
    doc = _block_document()
    generator = _StubGenerator(doc)

    generator._fill_passthrough_sections([_entry("Dates of Employment: 2011-Present")])

    texts = _texts(doc)
    assert "Name of Current Employer(s):" in texts  # untouched
    assert "Dates of Employment:\t2011-Present" in texts


def test_loosely_worded_source_label_still_reaches_the_employer_row():
    """Corpus wording: 'Name of Employer(s)' differs from the template's label."""
    doc = _block_document()
    generator = _StubGenerator(doc)

    generator._fill_passthrough_sections(
        [_entry("Name of Employer(s):  Weill Cornell Medical College")])

    assert "Name of Current Employer(s):\tWeill Cornell Medical College" in _texts(doc)
    assert generator.stats["entries_inserted"] == 1


def test_unknown_label_is_not_written_not_counted_and_is_logged(caplog):
    doc = _block_document()
    generator = _StubGenerator(doc)

    with caplog.at_level(logging.WARNING):
        generator._fill_passthrough_sections([_entry("Favorite Color: blue")])

    assert _texts(doc) == [
        "E. EMPLOYMENT STATUS",
        "Name of Current Employer(s):",
        "Position/Title:",
        "Dates of Employment:",
    ]
    assert generator.stats["entries_inserted"] == 0
    assert any("Favorite Color" in record.getMessage() for record in caplog.records)


def test_row_missing_from_template_is_not_counted_and_is_logged(caplog):
    """A matching label class with no row in the window must not fake a write."""
    doc = docx.Document()
    doc.add_paragraph("E. EMPLOYMENT STATUS")
    doc.add_paragraph("Name of Current Employer(s):")
    for _ in range(15):  # push the look-alike row beyond the scan window
        doc.add_paragraph("filler")
    doc.add_paragraph("Date of preparation:")
    generator = _StubGenerator(doc)

    with caplog.at_level(logging.WARNING):
        generator._fill_passthrough_sections(
            [_entry("Dates of Employment: 2011-Present")])

    assert generator.stats["entries_inserted"] == 0
    assert not any("2011-Present" in p.text for p in doc.paragraphs)
    assert any("Dates of Employment" in record.getMessage() for record in caplog.records)


@pytest.mark.skipif(not TEMPLATE_PATH.exists(), reason="WCM template not checked out")
def test_real_wcm_template_routes_employer_and_status_rows():
    """On the committed October-2022 template, the two real rows fill independently."""
    doc = docx.Document(str(TEMPLATE_PATH))
    generator = _StubGenerator(doc)

    generator._fill_passthrough_sections([
        _entry("Name of Employer(s): Weill Cornell Medical College"),
        _entry("Employment Status: Full-time salaried by Weill Cornell"),
    ])

    texts = _texts(doc)
    assert "Name of Current Employer(s):\tWeill Cornell Medical College" in texts
    assert ("Current Employment Status (Please choose one, list here, delete the "
            "others):\tFull-time salaried by Weill Cornell") in texts
    assert generator.stats["entries_inserted"] == 2
