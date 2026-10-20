"""Every WCM template slot stays, and an empty one says N/A (#1942).

Synthetic documents only; the end-to-end cases render the real template with
invented entries.
"""
import json

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from unified_pipeline.stage6.empty_slots import (
    APPENDIX_HEADING,
    NOT_APPLICABLE,
    fill_empty_slots,
    is_record_table,
    snapshot_template,
)
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator


def _body_texts(doc):
    """('p', text) / ('tbl', first-row texts) for every body element."""
    out = []
    for el in doc.element.body.iterchildren():
        if el.tag == qn('w:p'):
            out.append(('p', Paragraph(el, doc).text))
        elif el.tag == qn('w:tbl'):
            out.append(('tbl', [[''.join(t.text or '' for t in tc.iter(qn('w:t')))
                                 for tc in tr.iterchildren(qn('w:tc'))]
                                for tr in el.iterchildren(qn('w:tr'))]))
    return out


def _template(*paragraphs):
    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    return doc


def _after(doc, heading, count):
    body = _body_texts(doc)
    i = body.index(('p', heading))
    return body[i + 1:i + 1 + count]


def _insert_after_heading(doc, heading, text):
    para = next(p for p in doc.paragraphs if p.text == heading)
    new = doc.add_paragraph(text)
    para._p.addnext(new._p)


# --- paragraph slots ----------------------------------------------------------

def test_an_empty_paragraph_slot_gets_one_na_line_under_its_heading():
    doc = _template("Pending Funding", "Please summarize as for current projects.", "",
                    "Patents & Inventions")
    snapshot = snapshot_template(doc.element.body)

    counts = fill_empty_slots(doc.element.body, snapshot)

    assert _after(doc, "Pending Funding", 2) == [('p', NOT_APPLICABLE),
                                                ('p', "Please summarize as for current projects.")]
    assert _after(doc, "Patents & Inventions", 1) == [('p', NOT_APPLICABLE)]
    assert (counts.paragraphs, counts.tables) == (2, 0)


def test_a_slot_with_rendered_content_gets_no_na():
    doc = _template("Pending Funding", "Please summarize as for current projects.",
                    "Patents & Inventions")
    snapshot = snapshot_template(doc.element.body)
    _insert_after_heading(doc, "Pending Funding", "2024 Synthetic Foundation, pending")

    fill_empty_slots(doc.element.body, snapshot)

    assert _after(doc, "Pending Funding", 1) == [('p', "2024 Synthetic Foundation, pending")]
    assert ('p', NOT_APPLICABLE) not in _body_texts(doc)[:3]


def test_content_after_the_instruction_line_still_counts_as_the_slots_own():
    """The scope runs to the next heading, not to the instruction line."""
    doc = _template("Clinical Leadership", "Include year(s), leadership role.", "RESEARCH")
    snapshot = snapshot_template(doc.element.body)
    _insert_after_heading(doc, "Include year(s), leadership role.", "Director, Synthetic Clinic")

    fill_empty_slots(doc.element.body, snapshot)

    assert ('p', NOT_APPLICABLE) not in _body_texts(doc)


def test_an_added_blank_spacer_is_not_content():
    doc = _template("Abstracts (optional)", "", "Other (media, podcasts, etc.):")
    snapshot = snapshot_template(doc.element.body)
    _insert_after_heading(doc, "Abstracts (optional)", "")

    fill_empty_slots(doc.element.body, snapshot)

    assert _after(doc, "Abstracts (optional)", 1) == [('p', NOT_APPLICABLE)]


def test_an_added_picture_with_no_text_is_content():
    doc = _template("Abstracts (optional)", "", "Other (media, podcasts, etc.):")
    snapshot = snapshot_template(doc.element.body)
    _insert_after_heading(doc, "Abstracts (optional)", "")
    picture = next(p for p in doc.paragraphs[1:] if p.text == "")
    picture.add_run()._r.append(OxmlElement('w:drawing'))

    fill_empty_slots(doc.element.body, snapshot)

    assert _after(doc, "Abstracts (optional)", 1) != [('p', NOT_APPLICABLE)]


def test_a_changed_template_line_counts_as_content():
    doc = _template("Patents & Inventions", "Please include inventors.", "MENTORING")
    snapshot = snapshot_template(doc.element.body)
    doc.paragraphs[1].text = "Synthetic Widget, US 0,000,000"

    fill_empty_slots(doc.element.body, snapshot)

    assert ('p', NOT_APPLICABLE) not in _body_texts(doc)


def test_content_under_the_next_heading_does_not_fill_the_slot_above():
    doc = _template("Books:", "", "Chapters:", "")
    snapshot = snapshot_template(doc.element.body)
    _insert_after_heading(doc, "Chapters:", "1. Doe J. A synthetic chapter.")

    fill_empty_slots(doc.element.body, snapshot)

    assert _after(doc, "Books:", 1) == [('p', NOT_APPLICABLE)]
    assert _after(doc, "Chapters:", 1) == [('p', "1. Doe J. A synthetic chapter.")]


def test_a_section_heading_ends_the_scope():
    doc = _template("Other education/outreach activities (community education)", "",
                    "CLINICAL PRACTICE, INNOVATION, and LEADERSHIP")
    snapshot = snapshot_template(doc.element.body)
    doc.add_paragraph("Something the next section rendered")

    fill_empty_slots(doc.element.body, snapshot)

    assert _after(doc, "Other education/outreach activities (community education)", 1) == [
        ('p', NOT_APPLICABLE)]


def test_a_table_slot_heading_ends_the_scope():
    """N2's tables render under its own heading, not under N1's above it."""
    doc = _template("Leadership and mentoring in programs (Describe activity)", "",
                    "Institutional Training Grants and Mentored Trainee Grants")
    snapshot = snapshot_template(doc.element.body)
    _insert_after_heading(doc, "Institutional Training Grants and Mentored Trainee Grants",
                          "Synthetic T32, 2019-2024")

    fill_empty_slots(doc.element.body, snapshot)

    assert _after(doc, "Leadership and mentoring in programs (Describe activity)", 1) == [
        ('p', NOT_APPLICABLE)]


def test_a_template_table_ends_the_scope():
    doc = _template("Pending Funding")
    _column_table(doc)
    snapshot = snapshot_template(doc.element.body)
    doc.add_paragraph("Content of whatever the table belongs to")

    fill_empty_slots(doc.element.body, snapshot)

    assert _after(doc, "Pending Funding", 1) == [('p', NOT_APPLICABLE)]


def test_na_is_the_literal_wcm_asks_for_in_the_body_font():
    doc = _template("Pending Funding")
    snapshot = snapshot_template(doc.element.body)

    fill_empty_slots(doc.element.body, snapshot)

    run = doc.paragraphs[1].runs[0]
    assert run.text == "N/A"
    assert (run.font.name, run.font.size.pt) == ("Arial", 11)


def test_a_rendered_line_reading_like_a_heading_is_not_a_slot():
    doc = _template("Chapters:", "", "Reviews and Editorials:")
    snapshot = snapshot_template(doc.element.body)
    _insert_after_heading(doc, "Chapters:", "Books:")

    fill_empty_slots(doc.element.body, snapshot)

    assert _after(doc, "Books:", 1) == [('p', '')]
    assert _after(doc, "Reviews and Editorials:", 1) == [('p', NOT_APPLICABLE)]


def test_the_appendix_ends_the_last_slot_and_nothing_after_it_is_a_slot():
    doc = _template("Other (media, podcasts, etc.):", "")
    snapshot = snapshot_template(doc.element.body)
    doc.add_paragraph(APPENDIX_HEADING)
    doc.add_paragraph("1. Something unplaced")

    counts = fill_empty_slots(doc.element.body, snapshot)

    assert _after(doc, "Other (media, podcasts, etc.):", 1) == [('p', NOT_APPLICABLE)]
    assert counts.paragraphs == 1


def test_a_heading_after_the_appendix_is_left_alone():
    doc = _template(APPENDIX_HEADING, "Books:", "")
    snapshot = snapshot_template(doc.element.body)

    counts = fill_empty_slots(doc.element.body, snapshot)

    assert counts.paragraphs == 0
    assert ('p', NOT_APPLICABLE) not in _body_texts(doc)


# --- tables -------------------------------------------------------------------

def _column_table(doc):
    table = doc.add_table(rows=2, cols=3)
    for cell, text in zip(table.rows[0].cells, ("Title", "Institution", "Dates"), strict=True):
        cell.text = text
    return table


def test_an_unfilled_column_header_table_gets_na_in_its_first_blank_row():
    doc = Document()
    table = _column_table(doc)
    snapshot = snapshot_template(doc.element.body)

    counts = fill_empty_slots(doc.element.body, snapshot)

    assert [c.text for c in table.rows[1].cells] == [NOT_APPLICABLE, '', '']
    assert [c.text for c in table.rows[0].cells] == ["Title", "Institution", "Dates"]
    assert counts.tables == 1


def test_a_filled_table_is_left_alone():
    doc = Document()
    table = _column_table(doc)
    snapshot = snapshot_template(doc.element.body)
    table.rows[1].cells[0].text = "Synthetic Fellow"

    fill_empty_slots(doc.element.body, snapshot)

    assert [c.text for c in table.rows[1].cells] == ["Synthetic Fellow", '', '']


def test_a_table_with_an_added_row_counts_as_filled():
    doc = Document()
    table = _column_table(doc)
    snapshot = snapshot_template(doc.element.body)
    table.add_row()

    counts = fill_empty_slots(doc.element.body, snapshot)

    assert counts.tables == 0
    assert NOT_APPLICABLE not in [c.text for row in table.rows for c in row.cells]


def test_a_table_cleared_to_its_header_gets_its_blank_row_back_with_na():
    """Honors and Invitations to Speak clear the template's blank row before
    writing theirs; with nothing to write, the header was all that was left."""
    doc = Document()
    table = _column_table(doc)
    snapshot = snapshot_template(doc.element.body)
    table._tbl.remove(table.rows[1]._tr)

    counts = fill_empty_slots(doc.element.body, snapshot)

    assert [[c.text for c in row.cells] for row in table.rows] == [
        ["Title", "Institution", "Dates"], [NOT_APPLICABLE, '', '']]
    assert counts.tables == 1


def test_the_restored_row_is_the_template_s_blank_one_not_what_was_written_into_it():
    doc = Document()
    table = _column_table(doc)
    snapshot = snapshot_template(doc.element.body)
    table.rows[1].cells[0].text = "Written then discarded"
    table._tbl.remove(table.rows[1]._tr)

    fill_empty_slots(doc.element.body, snapshot)

    assert [c.text for c in table.rows[1].cells] == [NOT_APPLICABLE, '', '']


def test_a_grant_block_gets_na_in_its_first_answer():
    doc = Document()
    table = doc.add_table(rows=3, cols=2)
    for row, label in zip(table.rows, ("Award Source: (funding agency)", "Project title:",
                                        "Your role*:"), strict=True):
        row.cells[0].text = label
    snapshot = snapshot_template(doc.element.body)

    fill_empty_slots(doc.element.body, snapshot)

    assert [row.cells[1].text for row in table.rows] == [NOT_APPLICABLE, '', '']


def test_a_form_table_gets_no_na():
    """Personal Data's label | value rows are a form, not a list of records."""
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    table.rows[0].cells[0].text = "Office address:"
    table.rows[1].cells[0].text = "Office telephone:"
    snapshot = snapshot_template(doc.element.body)

    counts = fill_empty_slots(doc.element.body, snapshot)

    assert counts.tables == 0
    assert [row.cells[1].text for row in table.rows] == ['', '']


def test_record_table_shapes():
    assert is_record_table((("Title", "Dates"), ("", "")))
    assert is_record_table((("Name", ""), ("Site/Position", "")))
    assert is_record_table((("Award Source (funding agency):", ""), ("Project title:", "")))
    assert not is_record_table((("Office address:", ""), ("Cell phone:", "")))
    assert not is_record_table((("Activity", "Percent"), ("Teaching", ""), ("Total", "100%")))
    assert not is_record_table((("When preparing the WCM CV",),))
    assert not is_record_table(())


# --- the real template, end to end --------------------------------------------

def _render(tmp_path, entries):
    gen = WCMTemplateGenerator(verbose=False, recover_unrendered_records=False)
    gen._reconsider_appendix_entries = lambda: []
    input_path = tmp_path / "in.json"
    output_path = tmp_path / "out.docx"
    input_path.write_text(json.dumps({"document_uid": "T1942", "entries": entries}))
    gen.generate(str(input_path), str(output_path), research_summary_path=None)
    return Document(str(output_path))


_OWNER = {"text": "Name: A. Researcher", "taxonomy_code": "A", "extracted_fields": {},
          "element_idx_start": 0}
_PATENT = {"text": "Synthetic Widget, US Patent 0,000,001, 2020", "taxonomy_code": "M2D",
           "element_idx_start": 10,
           "extracted_fields": {"title": "Synthetic Widget", "patent_number": "0,000,001",
                                "date": "2020"}}


def test_real_template_keeps_every_placeholder_and_marks_the_empty_slots(tmp_path):
    doc = _render(tmp_path, [_OWNER, _PATENT])
    body = _body_texts(doc)

    def first_after(prefix):
        i = next(n for n, (kind, text) in enumerate(body)
                 if kind == 'p' and text.strip().startswith(prefix))
        return body[i + 1]

    assert first_after("Past (Completed) Funding") == ('p', NOT_APPLICABLE)
    assert first_after("Pending Funding") == ('p', NOT_APPLICABLE)
    assert first_after("Clinical Leadership") == ('p', NOT_APPLICABLE)
    assert first_after("Leadership and mentoring in programs") == ('p', NOT_APPLICABLE)
    assert first_after("Books:") == ('p', NOT_APPLICABLE)
    # The patent rendered, so its slot says nothing of N/A.
    assert first_after("Patents & Inventions") != ('p', NOT_APPLICABLE)
    assert any("Synthetic Widget" in cell for kind, rows in body if kind == 'tbl'
               for row in rows for cell in row)
    # Every record placeholder is still there, with N/A in its first answer.
    tables = [rows for kind, rows in body if kind == 'tbl']
    grant = [rows for rows in tables if rows[0][0].startswith("Award Source: (")]
    training = [rows for rows in tables if rows[0][0].startswith("Award Source (")]
    mentees = [rows for rows in tables if rows[0][0] == "Name"]
    assert [rows[0][1] for rows in grant + training + mentees] == [NOT_APPLICABLE] * 4
    other_positions = next(rows for rows in tables
                           if rows[0][0] == "Title" and rows[1][0] == NOT_APPLICABLE)
    assert other_positions[1][1:] == ['', '']
    # Forms get none: Personal Data, Percent Effort.
    assert not any(NOT_APPLICABLE in row for rows in tables
                   if rows[0][0].startswith(("Office address", "Weill Cornell Activity"))
                   for row in rows)
