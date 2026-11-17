"""The blank paragraphs around the template's headings are evened out (#2140).

Synthetic documents only.
"""
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from unified_pipeline.stage6.blank_lines import (
    BLANKS_BEFORE_SECTION,
    MAX_BLANK_RUN,
    even_out_blank_lines,
)
from unified_pipeline.stage6.empty_slots import APPENDIX_HEADING, snapshot_template


def _template(*paragraphs):
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    return doc


def _texts(doc):
    return [''.join(t.text or '' for t in el.iter(qn('w:t'))) if el.tag == qn('w:p') else 'TBL'
            for el in doc.element.body.iterchildren() if el.tag != qn('w:sectPr')]


def _write_after(doc, anchor, *texts):
    """Add *texts* as new paragraphs directly under the paragraph reading *anchor*."""
    at = next(p for p in doc.paragraphs if p.text == anchor)._p
    for text in texts:
        new = doc.add_paragraph(text)._p
        at.addnext(new)
        at = new


def _evened(doc, snapshot):
    even_out_blank_lines(doc.element.body, snapshot)
    return _texts(doc)


def test_na_and_content_sit_one_blank_under_a_slot_heading():
    doc = _template("Books:", "", "", "Chapters:", "", "", "Abstracts")
    snapshot = snapshot_template(doc.element.body)
    _write_after(doc, "Books:", "N/A")            # as fill_empty_slots writes it
    _write_after(doc, "Chapters:", "", "", "1. A synthetic chapter")
    _write_after(doc, "Abstracts", "1. A synthetic abstract")

    assert _evened(doc, snapshot) == [
        "Books:", "", "N/A", "", "",
        "Chapters:", "", "1. A synthetic chapter", "", "",
        "Abstracts", "", "1. A synthetic abstract"]


def test_a_slot_heading_over_a_table_or_its_own_instruction_is_left_alone():
    doc = _template("Pending Funding", "Please summarize as for current projects.",
                    "Past (Completed) Funding")
    doc.add_table(rows=1, cols=1)
    snapshot = snapshot_template(doc.element.body)

    assert even_out_blank_lines(doc.element.body, snapshot) == 0
    assert _texts(doc) == ["Pending Funding", "Please summarize as for current projects.",
                           "Past (Completed) Funding", "TBL"]


def test_a_section_heading_gets_the_templates_two_blanks_above_it():
    doc = _template("EDUCATION", "", "", "RESEARCH", "", "", "", "MENTORING", "", "HONORS, AWARDS")
    snapshot = snapshot_template(doc.element.body)
    # A writer that consumed RESEARCH's gap, and three blanks left above MENTORING.
    for p in doc.paragraphs[1:3]:
        p._p.getparent().remove(p._p)
    _write_after(doc, "EDUCATION", "a last line of content")

    assert _evened(doc, snapshot) == [
        "EDUCATION", "", "a last line of content", "", "",
        "RESEARCH", "", "",
        "MENTORING", "", "",
        "HONORS, AWARDS"]
    assert BLANKS_BEFORE_SECTION == 2


def test_research_support_is_a_subsection_and_keeps_its_one_blank():
    doc = _template("RESEARCH", "", "Research Support: ", "", "Current Research Funding")
    snapshot = snapshot_template(doc.element.body)

    assert even_out_blank_lines(doc.element.body, snapshot) == 0


def test_content_directly_under_a_section_heading_gets_one_blank():
    doc = _template("MENTORING", "", "Mentees")
    snapshot = snapshot_template(doc.element.body)
    _write_after(doc, "MENTORING", "A line a writer placed straight under the heading")

    assert _evened(doc, snapshot)[:3] == [
        "MENTORING", "", "A line a writer placed straight under the heading"]


def test_no_run_of_blanks_is_longer_than_the_cap():
    doc = _template("Faculty Curriculum Vitae Template", "", "", "", "", "Name:")
    snapshot = snapshot_template(doc.element.body)

    assert _evened(doc, snapshot) == ["Faculty Curriculum Vitae Template", "", "", "Name:"]
    assert MAX_BLANK_RUN == 2


def test_a_section_break_paragraph_is_never_removed_as_a_third_blank():
    doc = _template("A line", "", "", "", "Another line")
    section_break = doc.paragraphs[3]._p
    section_break.get_or_add_pPr().append(OxmlElement("w:sectPr"))
    snapshot = snapshot_template(doc.element.body)

    assert even_out_blank_lines(doc.element.body, snapshot) == 0
    assert section_break.getparent() is not None


def test_a_blank_that_carries_a_section_break_or_a_deletion_is_not_a_spacer():
    doc = _template("EDUCATION", "", "", "", "RESEARCH")
    blanks = doc.paragraphs[1:4]
    blanks[0]._p.get_or_add_pPr().append(OxmlElement("w:sectPr"))
    deleted = OxmlElement("w:del")
    blanks[2]._p.append(deleted)
    snapshot = snapshot_template(doc.element.body)

    even_out_blank_lines(doc.element.body, snapshot)

    body = list(doc.element.body.iterchildren(qn("w:p")))
    assert blanks[0]._p in body and blanks[2]._p in body
    # The deletion paragraph is content, so RESEARCH gets its own two blanks under it.
    assert _texts(doc) == ["EDUCATION", "", "", "", "", "", "RESEARCH"]


def test_a_content_line_reading_like_a_heading_is_not_one():
    doc = _template("EDUCATION", "")
    snapshot = snapshot_template(doc.element.body)
    _write_after(doc, "EDUCATION", "RESEARCH", "Books:", "a line")

    assert _evened(doc, snapshot) == ["EDUCATION", "", "RESEARCH", "Books:", "a line", ""]


def test_nothing_at_or_below_the_appendix_is_touched():
    doc = _template("EDUCATION", "", APPENDIX_HEADING, "", "", "", "RESEARCH", "Books:")
    snapshot = snapshot_template(doc.element.body)
    _write_after(doc, "Books:", "N/A")
    before = _texts(doc)

    assert even_out_blank_lines(doc.element.body, snapshot) == 0
    assert _texts(doc) == before


def test_an_added_blank_copies_its_neighbour():
    doc = _template("Books:", "")
    spacer = doc.paragraphs[1]
    spacer.style = doc.styles["No Spacing"]
    snapshot = snapshot_template(doc.element.body)
    _write_after(doc, "Books:", "N/A")

    even_out_blank_lines(doc.element.body, snapshot)

    added = doc.paragraphs[1]
    assert added.text == "" and added._p is not spacer._p
    assert added.style.name == "No Spacing"
