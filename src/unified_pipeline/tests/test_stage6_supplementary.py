"""Supplementary prose as tracked-deleted sub-points (#1205), stage6/supplementary.py.

What is offered (prose the document provably lacks and that holds no rendered
field value), where it goes (under the entry's own line, in the entry's own
section), and the markup: a deletion whose paragraph mark or row is deleted
too, so accepting all changes leaves the document as if the pass never ran,
and rejecting them shows the prose under its line. All fixture text is
synthetic.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_supplementary.py -p no:cacheprovider
"""

import copy
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402

from unified_pipeline.stage6 import supplementary as sp  # noqa: E402

_DATE = "2026-10-08T00:00:00Z"

PROSE = ("Responsible for coordinating the departmental curriculum committee "
         "and supervising graduate trainees")
OTHER_PROSE = "Developed laboratory teaching materials for undergraduate physiology courses"


def _haystack(*lines):
    return sp._Haystack("\x00".join(sp._squash(line) for line in lines),
                        [sp._tokens(line) for line in lines])


def _entry(text, code="D1", **fields):
    return {"taxonomy_code": code, "text": text, "extracted_fields": fields}


def _appointment(text=f"Associate Professor\tExample University\t{PROSE}"):
    return _entry(text, title="Associate Professor", institution="Example University",
                  start_date="2010", end_date="2015")


def _deleted_marks(element):
    return [d for d in element.iter(qn("w:del")) if d.get(qn("w:author")) == sp.SUBPOINT_AUTHOR]


# --- the switch ---------------------------------------------------------------

@pytest.mark.parametrize("value, on", [("1", True), (" 1 ", True), ("", True), (None, True),
                                       ("true", True), ("yes", True), (1, True), (True, True),
                                       ("0", False), (" 0 ", False), ("false", False),
                                       ("No", False), ("OFF", False), (0, False), (False, False)])
def test_the_flag_is_on_unless_turned_off(value, on):
    # On by default since the owner's 2026-10-09 review (#1205); "0" is the
    # ops switch, and an unquoted YAML false/no/0 reads as off too.
    assert sp.SUBPOINT_FLAG_DEFAULT == "1"
    assert sp.subpoints_enabled(value) is on


def test_the_codes_are_teaching_grants_appointments_and_extramural_service_only():
    assert sorted(sp.SUBPOINT_CODES) == ["D1", "D2", "D3", "K1", "K2", "K3", "K4", "K5",
                                         "M2A", "M2B", "M2C", "M2D", "Q1", "Q2"]
    # The owner decision's exclusions stay out.
    assert not sp.SUBPOINT_CODES & {"A", "T", "M1", "E", "G", "J", "N1", "N4", "S0", "L1",
                                    "L2", "F1", "Q4D", "B2"}


# --- what is prose --------------------------------------------------------------

def test_prose_fragments_split_on_tabs_newlines_and_pipes():
    text = f"Associate Professor | Example University\n2010-2015\t{PROSE}"
    assert sp.prose_fragments(text) == [PROSE]


@pytest.mark.parametrize("fragment", [
    "Associate Professor",                                 # under SUBPOINT_MIN_TOKENS
    "2010 - 2015 Associate Professor of Medicine, Example University Hospital",  # a record
    "May 2010 - present Visiting Professor, Example Institute of Science",
    'Email: HYPERLINK "mailto:someone@example.org" someone@example.org',
    "Contact the example programme office at someone@example.org for details",
    "Recording available at https://example.org/webinar/example-session-recording",
    "Recording available at www.example.org/webinar/example-session-recording",
])
def test_short_lines_dated_record_lines_and_contact_lines_are_not_prose(fragment):
    assert sp.prose_fragments(fragment) == []


def test_a_paragraph_broken_at_printed_lines_is_offered_whole():
    lines = ["I taught the anatomy laboratory for the", "graduate physiology students of the",
             "department and wrote the course manual."]
    assert sp.prose_fragments("\t".join(lines)) == [" ".join(lines)]


def test_a_fragment_mostly_holding_field_values_is_the_record_not_prose():
    entry = _entry("Example Foundation, Principal Investigator 2005-2007\t" + PROSE, code="M2B",
                   agency="Example Foundation", pi_role="Principal Investigator")
    assert sp.unrendered_prose(entry, "M2B", _haystack("Unrelated rendered line")) == [PROSE]


def test_prose_stage_4_put_in_a_field_no_renderer_reads_is_still_offered():
    # `duties` is no D1 rendered field (`_RENDERED_FIELDS`), so holding the
    # prose there does not make it the record: it reaches the page nowhere.
    entry = _appointment()
    entry["extracted_fields"]["duties"] = PROSE
    assert sp.unrendered_prose(entry, "D1", _haystack("Associate Professor | Example University")) \
        == [PROSE]


def test_a_rendered_fragment_is_not_offered():
    assert sp.unrendered_prose(_appointment(), "D1", _haystack(PROSE)) == []


def test_an_absent_fragment_is_offered():
    assert sp.unrendered_prose(_appointment(), "D1",
                               _haystack("Associate Professor | Example University")) == [PROSE]


def test_a_code_rendered_from_its_text_judges_every_scalar_field():
    # K2 has no `_RENDERED_FIELDS` row: all scalar fields count as values.
    entry = _entry("Clinical Preceptor, Example Teaching Hospital, Medical Students\t" + PROSE,
                   code="K2", role="Clinical Preceptor", institution="Example Teaching Hospital",
                   learners="Medical Students")
    assert sp.unrendered_prose(entry, "K2", _haystack("Unrelated rendered line")) == [PROSE]


def test_a_label_table_vouches_for_a_line_whose_facts_sit_in_its_rows():
    doc = Document()
    table = doc.add_table(rows=3, cols=2)
    for row, (label, value) in zip(table.rows, [("Agency:", "Example Health Institute"),
                                                ("Role:", "Principal Investigator"),
                                                ("Title:", "Example Pathways Research")],
                                   strict=True):
        row.cells[0].text, row.cells[1].text = label, value
    line = "Example Health Institute Research Award, Principal Investigator 1990-1994"
    rows = ["Agency: | Example Health Institute", "Role: | Principal Investigator"]
    haystack = sp.rendered_haystack(rows, doc)
    assert sp._record_rendered(line, haystack.text, haystack.line_tokens) is True
    plain = sp._Haystack(haystack.text, [sp._tokens(row) for row in rows])
    assert sp._record_rendered(line, plain.text, plain.line_tokens) is False


# --- where it goes -----------------------------------------------------------------

def _candidate(text, position=0, paragraph=None):
    return sp.AnchorCandidate(paragraph, text, sp._tokens(text), position)


def test_best_anchor_takes_the_line_holding_most_of_the_entry():
    tokens = sp._tokens("Associate Professor Example University")
    lines = [_candidate("Associate Professor of Surgery"),
             _candidate("Associate Professor | Example University | 2010-2015"),
             _candidate("Example University Hospital")]
    assert sp.best_anchor(tokens, lines) is lines[1]


def test_best_anchor_keeps_the_first_of_two_equal_lines():
    tokens = sp._tokens("Associate Professor Example University")
    lines = [_candidate("Associate Professor Example University"),
             _candidate("Associate Professor Example University")]
    assert sp.best_anchor(tokens, lines) is lines[0]


def test_best_anchor_needs_half_the_tokens():
    tokens = sp._tokens("Associate Professor Example University Hospital")
    assert sp.best_anchor(tokens, [_candidate("Associate Professor")]) is None


def test_best_anchor_needs_two_shared_tokens():
    # One token is all of the entry's, but one word is no anchor (an owner's
    # surname matched the Personal Data name line).
    assert sp.best_anchor({"examplename"}, [_candidate("Name: Examplename")]) is None
    assert sp.SUBPOINT_ANCHOR_MIN_TOKENS == 2


def test_best_anchor_needs_two_shared_tokens_even_at_half_the_entry():
    tokens = sp._tokens("Associate Examplename")
    assert sp.best_anchor(tokens, [_candidate("Name: Examplename")]) is None
    assert sp.best_anchor(tokens, [_candidate("Associate Examplename")]) is not None


def test_plan_places_an_entry_under_its_own_line_in_its_own_section():
    lines = [_candidate("Associate Professor | Example University | 2010-2015", position=5),
             _candidate("Associate Professor Example University, elsewhere", position=50)]
    planned = sp.plan_subpoints({"D1": [_appointment()]}, _haystack(lines[0].text), lines,
                                {"D1": sp.SectionSpan(1, 10)})
    assert [(p.anchor, p.paragraphs) for p in planned] == [(lines[0], (PROSE,))]


def test_plan_finds_no_anchor_outside_the_section():
    line = _candidate("Associate Professor | Example University | 2010-2015", position=50)
    assert sp.plan_subpoints({"D1": [_appointment()]}, _haystack(line.text), [line],
                             {"D1": sp.SectionSpan(1, 10)}) == []


def test_plan_skips_a_code_with_no_section_and_a_code_out_of_scope():
    line = _candidate("Associate Professor | Example University | 2010-2015", position=5)
    entry = _appointment()
    assert sp.plan_subpoints({"D1": [entry]}, _haystack(line.text), [line], {}) == []
    assert sp.plan_subpoints({"H": [{**entry, "taxonomy_code": "H"}]}, _haystack(line.text),
                             [line], {"H": sp.SectionSpan(0, None)}) == []


def test_plan_offers_a_fragment_once():
    line = _candidate("Associate Professor | Example University | 2010-2015", position=5)
    planned = sp.plan_subpoints({"D1": [_appointment(), _appointment()]}, _haystack(line.text),
                                [line], {"D1": sp.SectionSpan(0, None)})
    assert len(planned) == 1


def test_plan_skips_an_entry_whose_line_is_a_row_of_an_excluded_table():
    doc = Document()
    excluded, other = doc.add_table(rows=1, cols=3), doc.add_table(rows=1, cols=3)
    for table in (excluded, other):
        for cell, text in zip(table.rows[0].cells, ["Associate Professor", "Example University",
                                                    "2010-2015"], strict=True):
            cell.text = text
    (in_excluded, elsewhere) = [_candidate(c.text, position=i, paragraph=c.paragraph)
                                for i, c in enumerate(sp.anchor_candidates(doc))]
    args = ({"D1": [_appointment(), _appointment()]}, _haystack(in_excluded.text),
            [in_excluded, elsewhere], {"D1": sp.SectionSpan(0, None)})
    assert len(sp.plan_subpoints(*args)) == 1
    assert sp.plan_subpoints(*args, excluded_tables=[excluded._tbl]) == []
    (planned,) = sp.plan_subpoints(*args, excluded_tables=[other._tbl])
    assert planned.anchor is in_excluded


def test_section_span_bounds():
    span = sp.SectionSpan(3, 7)
    assert [span.holds(_candidate("x", position=i)) for i in (2, 3, 6, 7)] == [False, True, True, False]
    assert sp.SectionSpan(3, None).holds(_candidate("x", position=10_000))


def test_anchor_candidates_read_a_row_as_one_line_and_stop_at_the_appendix():
    doc = Document()
    doc.add_paragraph("Academic Appointments")
    table = doc.add_table(rows=1, cols=3)
    for cell, text in zip(table.rows[0].cells, ["Associate Professor", "Example University",
                                                "2010-2015"], strict=True):
        cell.text = text
    appendix = doc.add_paragraph("T. APPENDIX")
    doc.add_paragraph("Associate Professor Example University 2010-2015")
    found = list(sp.anchor_candidates(doc, appendix._p))
    assert [c.text for c in found] == ["Academic Appointments",
                                       "Associate Professor Example University 2010-2015"]
    assert found[1].paragraph._p is table.rows[0].cells[0].paragraphs[0]._p
    assert [c.position for c in found] == [0, 1]


# --- the markup ------------------------------------------------------------------------

def _numbered_paragraph(doc, text, level="0"):
    para = doc.add_paragraph(text)
    ppr = para._p.get_or_add_pPr()
    numpr = ppr._add_numPr()
    numpr.get_or_add_ilvl().val = int(level)
    numpr.get_or_add_numId().val = 1
    return para


def _accept_all(body):
    """Word's Accept All Changes on this module's markup: a deleted row goes,
    deleted runs go, and a paragraph whose mark is deleted merges into the
    next one (which keeps its own properties)."""
    body = copy.deepcopy(body)
    for tr in [tr for tr in body.iter(qn("w:tr")) if tr.find(f"{qn('w:trPr')}/{qn('w:del')}") is not None]:
        tr.getparent().remove(tr)
    for d in [d for d in body.iter(qn("w:del")) if d.getparent().tag not in (qn("w:rPr"), qn("w:trPr"))]:
        d.getparent().remove(d)
    for p in [p for p in body.iter(qn("w:p")) if p.find(f"{qn('w:pPr')}/{qn('w:rPr')}/{qn('w:del')}") is not None]:
        nxt = p.getnext()
        assert nxt is not None and nxt.tag == qn("w:p"), "a deleted mark with nothing to merge into"
        assert not [c for c in p if c.tag != qn("w:pPr")], "deleted paragraph kept content"
        p.getparent().remove(p)
    return body


def _texts(body):
    return ["".join(t.text or "" for t in p.iter(qn("w:t"))) for p in body.iter(qn("w:p"))]


def _reject_all_texts(body):
    return ["".join(t.text or "" for t in p.iter(qn("w:t"), qn("w:delText")))
            for p in body.iter(qn("w:p"))]


def _subpoint(paragraph, *texts):
    return sp.SubPoint(_candidate(paragraph.text, paragraph=paragraph), texts, 0)


@pytest.mark.parametrize("anchor_kind", ["bullet", "citation"])
def test_a_body_sub_point_is_a_deleted_paragraph_one_level_deeper(anchor_kind):
    doc = Document()
    anchor = _numbered_paragraph(doc, "Course Director, Example Course" if anchor_kind == "bullet"
                                 else "1. Author A. Example article. J Example. 2010;1:1-2.")
    doc.add_paragraph("Next line")
    before = _texts(doc.element.body)
    revision = sp.Revision(40, _DATE)

    sp.write_subpoint(_subpoint(anchor, PROSE, OTHER_PROSE), revision)

    new = anchor._p.getnext()
    assert new.find(f"{qn('w:pPr')}/{qn('w:numPr')}/{qn('w:ilvl')}").get(qn("w:val")) == "1"
    assert len(_deleted_marks(new)) == 2          # the mark and the run
    assert list(new.iter(qn("w:t"))) == []
    assert _reject_all_texts(doc.element.body)[1:3] == [PROSE, OTHER_PROSE]
    assert _texts(_accept_all(doc.element.body)) == before
    assert revision.next_id == 44
    ids = [d.get(qn("w:id")) for d in _deleted_marks(doc.element.body)]
    assert ids == ["40", "41", "42", "43"]
    assert {d.get(qn("w:date")) for d in _deleted_marks(doc.element.body)} == {_DATE}


def test_a_second_sub_point_under_one_line_follows_the_first():
    doc = Document()
    anchor = doc.add_paragraph("Course Director, Example Course")
    doc.add_paragraph("Next line")
    revision = sp.Revision(0, _DATE)
    sp.write_subpoint(_subpoint(anchor, PROSE), revision)
    sp.write_subpoint(_subpoint(anchor, OTHER_PROSE), revision)
    assert _reject_all_texts(doc.element.body)[:4] == [
        "Course Director, Example Course", PROSE, OTHER_PROSE, "Next line"]


def _followed_by(doc, neighbour):
    """A bullet anchor with a table or the section properties straight after it."""
    anchor = _numbered_paragraph(doc, "Course Director, Example Course")
    if neighbour == "table":
        table = doc.add_table(rows=1, cols=1)
        table.rows[0].cells[0].text = "Table cell text"
        doc.add_paragraph("After the table")
    return anchor


@pytest.mark.parametrize("neighbour", ["table", "sectPr"])
def test_a_sub_point_before_a_table_or_the_section_end_keeps_its_last_mark(neighbour):
    doc = Document()
    anchor = _followed_by(doc, neighbour)
    before = _texts(doc.element.body)
    assert anchor._p.getnext().tag == qn("w:tbl" if neighbour == "table" else "w:sectPr")

    sp.write_subpoint(_subpoint(anchor, PROSE, OTHER_PROSE), sp.Revision(0, _DATE))

    first, last = anchor._p.getnext(), anchor._p.getnext().getnext()
    assert first.find(f"{qn('w:pPr')}/{qn('w:rPr')}/{qn('w:del')}") is not None
    assert last.find(f"{qn('w:pPr')}/{qn('w:rPr')}/{qn('w:del')}") is None
    assert last.find(f"{qn('w:pPr')}/{qn('w:numPr')}") is None
    assert _reject_all_texts(doc.element.body)[1:3] == [PROSE, OTHER_PROSE]
    accepted = _accept_all(doc.element.body)
    # One empty plain paragraph is left, not an empty bullet, and the table text is its own.
    assert _texts(accepted) == before[:1] + [""] + before[1:]
    assert accepted.findall(qn("w:p"))[1].find(f"{qn('w:pPr')}/{qn('w:numPr')}") is None


def test_a_second_sub_point_before_a_table_deletes_the_first_ones_kept_mark():
    doc = Document()
    anchor = _followed_by(doc, "table")
    before = _texts(doc.element.body)
    revision = sp.Revision(0, _DATE)
    sp.write_subpoint(_subpoint(anchor, PROSE), revision)
    sp.write_subpoint(_subpoint(anchor, OTHER_PROSE), revision)
    assert _reject_all_texts(doc.element.body)[:3] == [
        "Course Director, Example Course", PROSE, OTHER_PROSE]
    first = anchor._p.getnext()
    assert first.find(f"{qn('w:pPr')}/{qn('w:rPr')}/{qn('w:del')}") is not None
    assert first.find(f"{qn('w:pPr')}/{qn('w:numPr')}/{qn('w:ilvl')}").get(qn("w:val")) == "1"
    assert _texts(_accept_all(doc.element.body)) == before[:1] + [""] + before[1:]


def _appointments_table(doc):
    table = doc.add_table(rows=3, cols=3)
    for row, title in zip(table.rows, ["Title", "Associate Professor", "Assistant Professor"],
                          strict=True):
        row.cells[0].text, row.cells[1].text, row.cells[2].text = title, "Example University", "2010"
    doc.add_paragraph("After the table")
    return table


def test_a_table_sub_point_is_a_deleted_row_spanning_the_table_under_its_row():
    doc = Document()
    table = _appointments_table(doc)
    before = _texts(doc.element.body)

    sp.write_subpoint(_subpoint(table.rows[1].cells[0].paragraphs[0], PROSE), sp.Revision(0, _DATE))

    rows = table._tbl.findall(qn("w:tr"))
    assert len(rows) == 4
    new = rows[2]
    assert new.find(f"{qn('w:trPr')}/{qn('w:del')}").get(qn("w:author")) == sp.SUBPOINT_AUTHOR
    cells = new.findall(qn("w:tc"))
    assert len(cells) == 1
    assert cells[0].find(f"{qn('w:tcPr')}/{qn('w:gridSpan')}").get(qn("w:val")) == "3"
    assert list(new.iter(qn("w:t"))) == []
    assert "".join(t.text for t in new.iter(qn("w:delText"))) == PROSE
    assert _texts(_accept_all(doc.element.body)) == before


def test_a_label_table_takes_its_sub_point_at_its_end():
    doc = Document()
    table = doc.add_table(rows=3, cols=2)
    for row, label in zip(table.rows, ["Title:", "Agency:", "Notes:"], strict=True):
        row.cells[0].text, row.cells[1].text = label, "Example value"
    sp.write_subpoint(_subpoint(table.rows[0].cells[0].paragraphs[0], PROSE), sp.Revision(0, _DATE))
    rows = table._tbl.findall(qn("w:tr"))
    assert rows[-1].find(f"{qn('w:trPr')}/{qn('w:del')}") is not None
    assert [r.find(f"{qn('w:trPr')}/{qn('w:del')}") is None for r in rows[:3]] == [True] * 3


def test_a_table_whose_rows_are_not_all_labels_is_not_a_label_table():
    doc = Document()
    table = _appointments_table(doc)
    assert sp._is_label_table(table._tbl) is False
