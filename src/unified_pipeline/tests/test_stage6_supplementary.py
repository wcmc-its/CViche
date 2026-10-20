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
import json
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.shared import Twips  # noqa: E402

from unified_pipeline.stage6 import supplementary as sp  # noqa: E402
from unified_pipeline.stage6.fan_out import (  # noqa: E402
    FANNED_OUT_FROM,
    LAST_STAGE4_RECORD,
)

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


def test_the_codes_are_teaching_grants_appointments_service_honors_mentees_degrees_and_leadership():
    assert sorted(sp.SUBPOINT_CODES) == ["B1", "D1", "D2", "D3", "H", "K1", "K2", "K3", "K4",
                                         "K5", "M2A", "M2B", "M2C", "M2D", "N3A", "N3B", "O",
                                         "Q1", "Q2"]
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


def test_plan_records_the_code_each_entry_is_planned_under_and_its_index():
    # The group an entry is planned under is the code it rendered as: a K2
    # rerouted into N3B keeps its stored code, but rendered as a mentee.
    lines = _mentee_tables("Ana Li")
    trainee = dict(_postdoc(), taxonomy_code="K2", element_idx_start=1477)
    (planned,) = sp.plan_subpoints({"N3B": [trainee]}, _haystack(), lines,
                                   {"N3B": sp.SectionSpan(0, None)})
    assert (planned.code, planned.element_idx_start) == ("N3B", 1477)


def test_a_placement_reads_back_from_its_sidecar_record():
    placement = sp.SubpointPlacement(1477, "N3B", (PROSE, OTHER_PROSE))
    assert sp.SubpointPlacement.from_record(json.loads(json.dumps(placement._asdict()))) == placement
    # A record missing a key, or holding a stray shape, reads as empty, not as text.
    assert sp.SubpointPlacement.from_record({"paragraphs": PROSE}) == sp.SubpointPlacement(None, "", ())
    assert sp.SubpointPlacement.from_record({"paragraphs": [7]}).paragraphs == ("7",)


def test_plan_finds_no_anchor_outside_the_section():
    line = _candidate("Associate Professor | Example University | 2010-2015", position=50)
    assert sp.plan_subpoints({"D1": [_appointment()]}, _haystack(line.text), [line],
                             {"D1": sp.SectionSpan(1, 10)}) == []


def test_plan_skips_a_code_with_no_section_and_a_code_out_of_scope():
    line = _candidate("Associate Professor | Example University | 2010-2015", position=5)
    entry = _appointment()
    assert sp.plan_subpoints({"D1": [entry]}, _haystack(line.text), [line], {}) == []
    assert sp.plan_subpoints({"Q4D": [{**entry, "taxonomy_code": "Q4D"}]}, _haystack(line.text),
                             [line], {"Q4D": sp.SectionSpan(0, None)}) == []


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


def _mentee_tables(*names):
    """Anchor candidates of one two-row mentee table per name, each table at
    the same level."""
    doc = Document()
    for name in names:
        table = doc.add_table(rows=2, cols=2)
        for row, (label, value) in zip(table.rows, [("Name:", name),
                                                    ("Site/Position:", "Postdoctoral Associate")],
                                       strict=True):
            row.cells[0].text, row.cells[1].text = label, value
    return list(sp.anchor_candidates(doc))


def _postdoc(name="Ana Li"):
    return _entry(f"{name}, Postdoctoral Associate\t{PROSE}", code="N3B", mentee_name=name,
                  mentee_level="Postdoctoral Associate")


def test_plan_places_a_mentee_only_in_the_table_naming_it():
    # The first table's level row holds every token the entry renders: only
    # its Name: row says it is another mentee's.
    lines = _mentee_tables("Casey Sample", "Ana Li")
    spans = {"N3B": sp.SectionSpan(0, None)}
    (planned,) = sp.plan_subpoints({"N3B": [_postdoc()]}, _haystack(), lines, spans)
    assert planned.anchor is lines[3]
    # Stage 4's spacing is not the cell's: the row is read with its spaces collapsed.
    (planned,) = sp.plan_subpoints({"N3B": [_postdoc(" Ana \n Li ")]}, _haystack(), lines, spans)
    assert planned.anchor is lines[3]
    # `name` is read before `mentee_name`, as the section reads it.
    by_name = _postdoc("Casey Sample")
    by_name["extracted_fields"]["name"] = "Ana Li"
    (planned,) = sp.plan_subpoints({"N3B": [by_name]}, _haystack(), lines, spans)
    assert planned.anchor is lines[3]
    for nobody in (_postdoc("Jordan Example"), {**_postdoc(), "extracted_fields": None}):
        assert sp.plan_subpoints({"N3B": [nobody]}, _haystack(), lines, spans) == []


def test_plan_places_a_mentee_naming_no_one_in_no_table():
    # Not even one whose Name: row is blank.
    lines = _mentee_tables("")
    nameless = _postdoc()
    del nameless["extracted_fields"]["mentee_name"]
    assert sp.plan_subpoints({"N3B": [nameless]}, _haystack(), lines,
                             {"N3B": sp.SectionSpan(0, None)}) == []


def _record_of_line(name, text, index, count):
    """Record `index` of `count` that fan-out made of one source line: the
    last keeps the whole line (`LAST_STAGE4_RECORD`), the others their own."""
    entry = dict(_postdoc(name), text=text, element_idx_start=7)
    marker = {"key": "stage4_records", "index": index, "count": count}
    if index == count - 1:
        entry[LAST_STAGE4_RECORD] = {**marker, "text": None}
    else:
        entry[FANNED_OUT_FROM] = marker
    return entry


def test_plan_reads_the_last_record_of_a_paired_line_from_its_own_segment():
    # The first record's prose is withheld with it (#892): the last record,
    # which keeps the whole line, does not offer it under its own table.
    first_line, last_line = f"Ana Li, Postdoctoral Associate, {PROSE}", "Casey Sample, Postdoc"
    first = _record_of_line("Ana Li", first_line, 0, 2)
    first["_pii_dropped_fields"] = ["site_position"]
    first["_pii_withheld"] = True  # the pass sets both
    lines = _mentee_tables("Ana Li", "Casey Sample")
    spans = {"N3B": sp.SectionSpan(0, None)}
    last = _record_of_line("Casey Sample", f"{first_line}\t{last_line}", 1, 2)
    assert sp.plan_subpoints({"N3B": [first, last]}, _haystack(), lines, spans) == []
    # Prose in its own segment, the line's last, is its own.
    last = _record_of_line("Casey Sample", f"{first_line}\t{last_line}, {OTHER_PROSE}", 1, 2)
    (planned,) = sp.plan_subpoints({"N3B": [first, last]}, _haystack(), lines, spans)
    assert planned.anchor is lines[2] and planned.paragraphs == (f"{last_line}, {OTHER_PROSE}",)


def _degree_rows(*rows):
    """A line naming the field and institution, then a degrees table, one
    row of cells per item; and their anchor candidates."""
    doc = Document()
    doc.add_paragraph("Example Marine Studies at Norvale University")
    table = doc.add_table(rows=len(rows), cols=3)
    for row, cells in zip(table.rows, rows, strict=True):
        for cell, text in zip(row.cells, cells, strict=True):
            cell.text = text
    return table, list(sp.anchor_candidates(doc))


def _master(text=f"M.S., Example Marine Studies, Norvale University, 2001\t{PROSE}"):
    return _entry(text, code="B1", degree="M.S.", discipline="Example Marine Studies",
                  institution="Norvale University", year="2001")


def test_plan_places_a_degree_only_under_a_row_written_for_it():
    # The line and the Ph.D. row hold every distinctive word the M.S. row
    # does: words alone take the first, and only the writer's record says
    # which row is whose.
    table, lines = _degree_rows(["Ph.D., Example Marine Studies", "Norvale University", "2005"],
                                ["M.S., Example Marine Studies", "Norvale University", "2001"])
    entry, spans = _master(), {"B1": sp.SectionSpan(0, None)}
    assert sp.best_anchor(sp._value_tokens(entry, "B1"), lines) is lines[0]

    def written(recorded):
        return lambda e: recorded if e is entry else []

    # The education writer records the row's first-cell paragraph.
    first_cell = table.rows[1].cells[0].paragraphs[0]._p
    for recorded in ([table.rows[1]._tr], [first_cell]):
        (planned,) = sp.plan_subpoints({"B1": [entry]}, _haystack(), lines, spans,
                                       rows_written=written(recorded))
        assert planned.anchor is lines[2] and planned.paragraphs == (PROSE,)
    # No row recorded for it, a paragraph outside any table, or no ledger: none.
    stray = Document().add_paragraph("M.S., Example Marine Studies")._p
    for rows_written in (written([]), written([stray]), None):
        assert sp.plan_subpoints({"B1": [entry]}, _haystack(), lines, spans,
                                 rows_written=rows_written) == []


def test_plan_takes_a_degrees_one_own_row_whatever_its_words():
    # "PhD", a four-letter name and a year hold no distinctive word: the
    # word test finds no anchor, and the writer's record alone says the row
    # is the entry's (#1205 review: the prose reached the document in
    # neither arm).
    table, lines = _degree_rows(["PhD", "Vaux", "2010"])
    entry = _entry(f"PhD, Vaux, 2010\t{PROSE}", code="B1", degree="PhD", institution="Vaux",
                   year="2010")
    spans = {"B1": sp.SectionSpan(0, None)}
    assert sp.best_anchor(sp._value_tokens(entry, "B1"), lines[1:]) is None
    (planned,) = sp.plan_subpoints({"B1": [entry]}, _haystack(), lines, spans,
                                   rows_written=lambda e: [table.rows[0]._tr])
    assert planned.anchor is lines[1] and planned.paragraphs == (PROSE,)
    # Two rows recorded for one entry: its words choose between them.
    table, lines = _degree_rows(["M.S.", "Norvale University", "2001"],
                                ["M.S., Example Marine Studies", "Norvale University", "2001"])
    (planned,) = sp.plan_subpoints({"B1": [_master()]}, _haystack(), lines, spans,
                                   rows_written=lambda e: [row._tr for row in table.rows])
    assert planned.anchor is lines[2]
    # Any other code still needs the words, with one line in scope or more.
    line = _candidate("Unrelated rendered line", position=0)
    assert sp.plan_subpoints({"D1": [_appointment()]}, _haystack(), [line],
                             {"D1": sp.SectionSpan(0, None)}) == []


@pytest.mark.parametrize("fields, planned", [
    ({"degree": "M.S."}, 1),
    # Its row is a field or an institution alone (EBYSBC VGHNZD 1861: a
    # thesis citation coded B1): its prose is of no degree there.
    ({"degree": None}, 0),
    ({"degree": "  "}, 0),
    ({}, 0),
])
def test_plan_gives_an_entry_naming_no_degree_no_sub_point(fields, planned):
    table, lines = _degree_rows(["Example Marine Studies", "Norvale University", "2001"])
    entry = _master()
    entry["extracted_fields"] = {**{k: v for k, v in entry["extracted_fields"].items()
                                    if k != "degree"}, **fields}
    assert len(sp.plan_subpoints({"B1": [entry]}, _haystack(), lines,
                                 {"B1": sp.SectionSpan(0, None)},
                                 rows_written=lambda e: [table.rows[0]._tr])) == planned
    # Fields that are not a mapping name no degree either.
    entry["extracted_fields"] = ["M.S."]
    assert not sp._names_degree(entry)


@pytest.mark.parametrize("field", sp._DEGREE_CELL_FIELDS)
def test_a_degrees_cell_fields_count_as_its_rendered_values(field):
    # The degree's cell prints them, though `_RENDERED_FIELDS` cannot list
    # them (no B1 schema field is any of them). Another code's are its own.
    entry = _entry("M.S.", code="B1", degree="M.S.", **{field: "Estuarine Sediment Geochemistry"})
    assert {"estuarine", "sediment", "geochemistry"} <= sp._value_tokens(entry, "B1")
    assert not {"estuarine"} & sp._value_tokens(dict(entry, taxonomy_code="H"), "H")


def _degree_record(text, index, count):
    """Record `index` of `count` that fan-out made of one source line."""
    entry = dict(_master(text), element_idx_start=7)
    marker = {"key": "stage4_records", "index": index, "count": count}
    if index == count - 1:
        entry[LAST_STAGE4_RECORD] = {**marker, "text": None}
    else:
        entry[FANNED_OUT_FROM] = marker
    return entry


@pytest.mark.parametrize("segments, index, read", [
    # The last of two records, its line's two segments pairing with them: its own.
    (["Ph.D. line", f"M.S. line, {PROSE}"], 1, f"M.S. line, {PROSE}"),
    # The last of two records on a line of three segments: none of it.
    (["Ph.D. line", PROSE, "M.S. line"], 1, ""),
    # An earlier record: the line fan-out gave it, whatever it holds.
    ([f"Ph.D. line, {PROSE}", "M.S. line"], 0, f"Ph.D. line, {PROSE}\tM.S. line"),
])
def test_a_degree_reads_its_prose_from_its_own_line(segments, index, read):
    entry = _degree_record("\t".join(segments), index, 2)
    assert sp._own_record_line(entry) == read
    assert sp._prose_text("B1", entry, {}) == read
    assert sp._prose_text("D1", entry, {}) is None


_ROLE = "Director, Example Outreach Programme"


def _role_rows(*rows):
    """A line naming the role and institution, then a leadership table, one
    row of cells per item; and their anchor candidates."""
    doc = Document()
    doc.add_paragraph(f"{_ROLE} at Norvale University")
    table = doc.add_table(rows=len(rows), cols=3)
    for row, cells in zip(table.rows, rows, strict=True):
        for cell, text in zip(row.cells, cells, strict=True):
            cell.text = text
    return table, list(sp.anchor_candidates(doc))


def _leadership_role(text=f"2008-2012\t{_ROLE}, Norvale University\t{PROSE}", **fields):
    return _entry(text, code="O", **{"leadership_role": _ROLE, "institution": "Norvale University",
                                     "start_date": "2008", "end_date": "2012", **fields})


def test_plan_places_a_leadership_role_only_under_a_row_written_for_it():
    # One title held twice at one institution: the rows differ only in their
    # dates, which are digits, and words alone take the first line holding them.
    table, lines = _role_rows([_ROLE, "Norvale University", "2016-2020"],
                              [_ROLE, "Norvale University", "2008-2012"])
    entry, spans = _leadership_role(), {"O": sp.SectionSpan(0, None)}
    assert sp.best_anchor(sp._value_tokens(entry, "O"), lines) is lines[0]

    def written(recorded):
        return lambda e: recorded if e is entry else []

    (planned,) = sp.plan_subpoints({"O": [entry]}, _haystack(), lines, spans,
                                   rows_written=written([table.rows[1]._tr]))
    assert planned.anchor is lines[2] and planned.paragraphs == (PROSE,)
    assert (planned.code, planned.entry_id) == ("O", id(entry))
    # No row recorded for it, a paragraph outside any table, or no ledger: none.
    stray = Document().add_paragraph(_ROLE)._p
    for rows_written in (written([]), written([stray]), None):
        assert sp.plan_subpoints({"O": [entry]}, _haystack(), lines, spans,
                                 rows_written=rows_written) == []


def test_plan_takes_a_roles_one_own_row_whatever_its_words():
    # "Dean" and an acronym hold no distinctive word: the word test finds no
    # anchor, and the writer's record alone says the row is the entry's.
    table, lines = _role_rows(["Dean", "NVU", "2010"])
    entry = _entry(f"2010\tDean, NVU\t{PROSE}", code="O", leadership_role="Dean",
                   institution="NVU", start_date="2010")
    spans = {"O": sp.SectionSpan(0, None)}
    assert sp.best_anchor(sp._value_tokens(entry, "O"), lines[1:]) is None
    (planned,) = sp.plan_subpoints({"O": [entry]}, _haystack(), lines, spans,
                                   rows_written=lambda e: [table.rows[0]._tr])
    assert planned.anchor is lines[1] and planned.paragraphs == (PROSE,)
    # Several rows recorded for one entry (a flattened table the writer split):
    # its words choose between them.
    table, lines = _role_rows(["Chair, Example Admissions Committee", "Norvale University", "2001"],
                              [_ROLE, "Norvale University", "2008-2012"])
    (planned,) = sp.plan_subpoints({"O": [_leadership_role()]}, _haystack(), lines, spans,
                                   rows_written=lambda e: [row._tr for row in table.rows])
    assert planned.anchor is lines[2]


@pytest.mark.parametrize("field", sp._LEADERSHIP_ROW_FIELDS)
def test_a_leadership_rows_fallback_fields_count_as_its_rendered_values(field):
    # The row prints them when the schema's role or institution is empty,
    # though `_RENDERED_FIELDS` cannot list them. Another code's are its own.
    entry = _entry("Dean", code="O", **{field: "Estuarine Sediment Geochemistry"})
    assert {"estuarine", "sediment", "geochemistry"} <= sp._value_tokens(entry, "O")
    assert not {"estuarine"} & sp._value_tokens(dict(entry, taxonomy_code="H"), "H")
    assert not {"estuarine"} & sp._value_tokens(dict(entry, taxonomy_code="B1"), "B1")


@pytest.mark.parametrize("segments, index, read", [
    # The last of two records, its line's two segments pairing with them: its own.
    (["Chair line", f"Director line, {PROSE}"], 1, f"Director line, {PROSE}"),
    # The last of two records on a line of three segments: none of it.
    (["Chair line", PROSE, "Director line"], 1, ""),
    # An earlier record: the line fan-out gave it, whatever it holds.
    ([f"Chair line, {PROSE}", "Director line"], 0, f"Chair line, {PROSE}\tDirector line"),
])
def test_a_leadership_role_reads_its_prose_from_its_own_line(segments, index, read):
    entry = _degree_record("\t".join(segments), index, 2)
    entry["taxonomy_code"] = "O"
    assert sp._prose_text("O", entry, {}) == read


_CHAIR = "Chair, Example Admissions Committee"


@pytest.mark.parametrize("text, records", [
    # A bare date column is a neighbouring part's date, not a record.
    (f"2008-2012\t{_ROLE}, Norvale University\t{PROSE}", False),
    (f"2005-2010 {_ROLE}, Norvale University\t{PROSE}", False),
    (f"2001-2004 {_CHAIR}, Norvale University\t{OTHER_PROSE}\n"
     f"2005-2010 {_ROLE}, Norvale University\t{PROSE}", True),
    (f"2001 MS, Example Marine Studies\t{OTHER_PROSE}\t2005 PhD, Example Marine Studies", True),
], ids=["date_column", "one_record", "two_roles", "two_degrees"])
def test_a_line_holds_dated_records_when_two_parts_open_with_their_dates(text, records):
    assert sp._holds_dated_records(text) is records


@pytest.mark.parametrize("code, first, second, fields, row", [
    # Two roles on one line, each opening with its dates and followed by its
    # prose; stage 4 returned the second (#1205 review, probe P12).
    ("O", f"2001-2004 {_CHAIR}, Norvale University", f"2005-2010 {_ROLE}, Norvale University",
     {"leadership_role": _ROLE, "institution": "Norvale University", "start_date": "2005",
      "end_date": "2010"},
     [_ROLE, "Norvale University", "2005-2010"]),
    # Two degrees, the same way.
    ("B1", "2001 MS, Example Marine Studies, Norvale University",
     "2005 PhD, Example Marine Studies, Norvale University",
     {"degree": "PhD", "discipline": "Example Marine Studies", "institution": "Norvale University",
      "year": "2005"},
     ["PhD, Example Marine Studies", "Norvale University", "2005"]),
], ids=["O", "B1"])
def test_plan_offers_no_prose_from_a_line_of_two_dated_records(code, first, second, fields, row):
    # Which record a prose line is about cannot be told: the first record's
    # would go under the second's row, with the second's own.
    table, lines = _role_rows(row)
    spans = {code: sp.SectionSpan(0, None)}

    def plan(text):
        return sp.plan_subpoints({code: [_entry(text, code=code, **fields)]}, _haystack(), lines,
                                 spans, rows_written=lambda e: [table.rows[0]._tr])

    (planned,) = plan(f"{second}\t{PROSE}")
    assert planned.anchor is lines[1] and planned.paragraphs == (PROSE,)
    two_records = f"{first}\t{OTHER_PROSE}\t{second}\t{PROSE}"
    assert sp._own_record_line(_entry(two_records, code=code, **fields)) == ""
    assert plan(two_records) == []


_SPOUSE = "Zelphine Qorvath"


@pytest.mark.parametrize("protect", [
    # The PII pass dropped a field named for protected data ("spouse_name"):
    # it marks the entry withheld and lists no `_pii_dropped_fields`, and the
    # prose names the spouse with no label (#1205 review, probe P7).
    lambda e: e.update(_pii_withheld=True, text=e["text"] + f", with my spouse {_SPOUSE}"),
    # Prose the full Personal Data / Appendix scope withholds, which a routed
    # code's scope leaves in the text.
    lambda e: e.update(text=e["text"] + f"\tMarried ({_SPOUSE}), co-founded the outreach clinics"),
    # A phone number in the middle of a line, which no scope withholds (probe P9).
    lambda e: e.update(text=e["text"] + ". Home phone: 212-555-0147"),
], ids=["withheld", "full_scope", "home_phone"])
def test_plan_offers_no_prose_of_an_entry_holding_protected_data(protect):
    table, lines = _role_rows([_ROLE, "Norvale University", "2008-2012"])
    spans = {"O": sp.SectionSpan(0, None)}
    entry = _leadership_role()
    (planned,) = sp.plan_subpoints({"O": [entry]}, _haystack(), lines, spans,
                                   rows_written=lambda e: [table.rows[0]._tr])
    assert planned.paragraphs == (PROSE,)

    protect(entry)
    assert sp.plan_subpoints({"O": [entry]}, _haystack(), lines, spans,
                             rows_written=lambda e: [table.rows[0]._tr]) == []


def test_only_a_mentee_code_is_placed_over_both_mentee_headings():
    # The section files a past mentee whose range is open under Current Mentees.
    lines = _mentee_tables("Ana Li")
    planned = sp.plan_subpoints({"N3B": [_postdoc()]}, _haystack(), lines,
                                {"N3A": sp.SectionSpan(0, 5), "N3B": sp.SectionSpan(5, None)})
    assert [p.anchor for p in planned] == [lines[1]]
    line = _candidate("Associate Professor | Example University | 2010-2015", position=0)
    assert sp.plan_subpoints({"D1": [_appointment()]}, _haystack(line.text), [line],
                             {"N3A": sp.SectionSpan(0, 5), "D1": sp.SectionSpan(5, None)}) == []


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
    return sp.SubPoint(_candidate(paragraph.text, paragraph=paragraph), texts, 0, "D1", 0)


def _plain_entry_paragraph(doc, text, left_twips):
    para = doc.add_paragraph(text)
    para.paragraph_format.left_indent = Twips(left_twips)
    return para


@pytest.mark.parametrize(("anchor_left", "expected_left"), [(1080, "1800"), (1800, "2520")])
def test_a_sub_point_under_an_unbulleted_entry_goes_one_indent_step_deeper(anchor_left,
                                                                           expected_left):
    # #1943: a teaching entry has no w:numPr, only a left indent on the
    # bullet-text grid, so the sub-point deepens that indent instead of ilvl.
    doc = Document()
    anchor = _plain_entry_paragraph(doc, "Course Director, Example Course", anchor_left)
    doc.add_paragraph("Next line")

    sp.write_subpoint(_subpoint(anchor, PROSE), sp.Revision(40, _DATE))

    new = anchor._p.getnext()
    assert new.find(f"{qn('w:pPr')}/{qn('w:numPr')}") is None
    assert new.find(f"{qn('w:pPr')}/{qn('w:ind')}").get(qn("w:left")) == expected_left
    assert anchor._p.pPr.find(qn("w:ind")).get(qn("w:left")) == str(anchor_left)


def test_a_sub_point_under_an_off_grid_indent_keeps_the_anchor_indent():
    # Only the unbulleted-entry grid deepens; any other indent is copied as is.
    doc = Document()
    anchor = _plain_entry_paragraph(doc, "Course Director, Example Course", 720)
    doc.add_paragraph("Next line")

    sp.write_subpoint(_subpoint(anchor, PROSE), sp.Revision(40, _DATE))

    assert anchor._p.getnext().find(f"{qn('w:pPr')}/{qn('w:ind')}").get(qn("w:left")) == "720"


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
