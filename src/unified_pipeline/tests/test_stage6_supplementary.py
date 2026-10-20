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


def test_the_codes_are_teaching_grants_appointments_service_honors_mentees_degrees_leadership_committees_and_talks():
    assert sorted(sp.SUBPOINT_CODES) == ["B1", "D1", "D2", "D3", "H", "K1", "K2", "K3", "K4",
                                         "K5", "M2A", "M2B", "M2C", "M2D", "N3A", "N3B", "O",
                                         "P", "Q1", "Q2", "R"]
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


_COMMITTEE = "Example Curriculum Steering Committee"
_COMMITTEE_ROW = [f"{_COMMITTEE}, Norvale University", "Member", "2008-2012"]


def _committee_rows(*rows):
    """A line naming the committee and institution, then a committee table,
    one row of cells per item; and their anchor candidates."""
    doc = Document()
    doc.add_paragraph(f"{_COMMITTEE}, Norvale University")
    table = doc.add_table(rows=len(rows), cols=3)
    for row, cells in zip(table.rows, rows, strict=True):
        for cell, text in zip(row.cells, cells, strict=True):
            cell.text = text
    return table, list(sp.anchor_candidates(doc))


def _committee(text=f"{_COMMITTEE}, Norvale University, Member, 2008-2012\t{PROSE}", **fields):
    return _entry(text, code="P", **{"committee_name": _COMMITTEE, "institution": "Norvale University",
                                     "role": "Member", "start_date": "2008", "end_date": "2012",
                                     **fields})


def test_plan_places_a_committee_only_under_its_one_own_row():
    # One committee in two terms: the rows differ only in their dates, which
    # are digits, and words alone take the first row holding them.
    table, lines = _committee_rows([*_COMMITTEE_ROW[:2], "2016-2020"], _COMMITTEE_ROW)
    entry, spans = _committee(), {"P": sp.SectionSpan(0, None)}
    assert sp.best_anchor(sp._value_tokens(entry, "P"), lines) is lines[1]

    def written(recorded):
        return lambda e: recorded if e is entry else []

    (planned,) = sp.plan_subpoints({"P": [entry]}, _haystack(), lines, spans,
                                   rows_written=written([table.rows[1]._tr]))
    assert planned.anchor is lines[2] and planned.paragraphs == (PROSE,)
    assert (planned.code, planned.entry_id) == ("P", id(entry))
    # Both rows recorded for it (a list of records, a re-parsed block): which
    # one a line is about cannot be shown. No row recorded for it, a paragraph
    # outside any table, or no ledger: none either.
    stray = Document().add_paragraph(_COMMITTEE)._p
    for rows_written in (written([row._tr for row in table.rows]), written([]), written([stray]),
                         None):
        assert sp.plan_subpoints({"P": [entry]}, _haystack(), lines, spans,
                                 rows_written=rows_written) == []


def test_plan_takes_a_committees_one_own_row_whatever_its_words():
    # An acronym and a short role hold too few distinctive words for the word
    # test, and the writer's record alone says the row is the entry's.
    table, lines = _committee_rows(["IRB, NVU", "Chair", "2010"])
    entry = _entry(f"IRB, NVU, Chair, 2010\t{PROSE}", code="P", committee_name="IRB",
                   institution="NVU", role="Chair", start_date="2010")
    spans = {"P": sp.SectionSpan(0, None)}
    assert sp.best_anchor(sp._value_tokens(entry, "P"), lines[1:]) is None
    (planned,) = sp.plan_subpoints({"P": [entry]}, _haystack(), lines, spans,
                                   rows_written=lambda e: [table.rows[0]._tr])
    assert planned.anchor is lines[1] and planned.paragraphs == (PROSE,)


@pytest.mark.parametrize("field", sp._ADMINISTRATIVE_ROW_FIELDS)
def test_a_committee_rows_fallback_fields_count_as_its_rendered_values(field):
    # The row prints them when the schema's committee_name is empty, though
    # `_RENDERED_FIELDS` cannot list them. Another code's are its own.
    entry = _entry("Member", code="P", **{field: "Estuarine Sediment Geochemistry"})
    assert {"estuarine", "sediment", "geochemistry"} <= sp._value_tokens(entry, "P")
    assert not {"estuarine"} & sp._value_tokens(dict(entry, taxonomy_code="O"), "O")
    assert not {"estuarine"} & sp._value_tokens(dict(entry, taxonomy_code="Q2"), "Q2")


def test_the_committee_fallback_fields_are_the_writers_off_schema_names():
    # The writer's precedence, schema field first, and the two names the
    # schema does not define.
    assert sp.ADMINISTRATIVE_ACTIVITY_FIELDS == ("committee_name", "committee", "activity")
    assert sp._ADMINISTRATIVE_ROW_FIELDS == ("committee", "activity")


@pytest.mark.parametrize("segments, index, read", [
    # The last of two records, its line's two segments pairing with them: its
    # own. Neither names a committee body (`_names_another_committee`).
    (["Chair line", f"Office line, {PROSE}"], 1, f"Office line, {PROSE}"),
    # The last of two records on a line of three segments: none of it.
    (["Chair line", PROSE, "Office line"], 1, ""),
    # An earlier record: the line fan-out gave it, whatever it holds.
    ([f"Chair line, {PROSE}", "Office line"], 0, f"Chair line, {PROSE}\tOffice line"),
])
def test_a_committee_reads_its_prose_from_its_own_line(segments, index, read):
    entry = _degree_record("\t".join(segments), index, 2)
    entry["taxonomy_code"] = "P"
    assert sp._prose_text("P", entry, {}) == read


_ADMISSIONS = "Example Admissions Committee, Norvale University"


@pytest.mark.parametrize("text, own", [
    # One committee and its years, then its remit: its own line.
    (f"{_COMMITTEE}, Norvale University, Member, 2008-2012\t{PROSE}", True),
    # The role and years in a parenthesis, the writer's repair shape.
    (f"{_COMMITTEE} (Member 2008-2012)\t{PROSE}", True),
    # A run of committees each followed by its years, stage 2's tabs between
    # them: one line, so the writer prints one row, of the last.
    (f"{_ADMISSIONS}, 2001-2004\t{OTHER_PROSE}\t{_COMMITTEE}, Norvale University, 2008-2012"
     f"\t{PROSE}", False),
    # Two committees in a pipe table, the years in a parenthesis and a column.
    (f"{_ADMISSIONS} (Chair 2001-2004) | {OTHER_PROSE}\n{_COMMITTEE} | 2008-2012 | {PROSE}", False),
    # Each opening with its years (`_holds_dated_records`).
    (f"2001-2004 {_ADMISSIONS}\t{OTHER_PROSE}\t2008-2012 {_COMMITTEE}\t{PROSE}", False),
], ids=["one_record", "parenthetical", "trailing_years", "pipe_rows", "leading_years"])
def test_a_committee_line_holding_several_dated_records_offers_none(text, own):
    """Which committee a prose line is about cannot be told: the others'
    records would be offered as the extracted one's prose, under its row."""
    entry = _committee(text)
    assert sp._own_committee_line(entry) == (text if own else "")
    table, lines = _committee_rows(_COMMITTEE_ROW)
    planned = sp.plan_subpoints({"P": [entry]}, _haystack(), lines, {"P": sp.SectionSpan(0, None)},
                                rows_written=lambda e: [table.rows[0]._tr])
    assert [p.paragraphs for p in planned] == ([(PROSE,)] if own else [])


def test_only_a_committee_reads_its_parts_for_several_records():
    # The writer's own test, per line, sees one record on a tab-joined run,
    # and so do the degree and leadership rules, which read date-led parts only.
    # The other record names no committee: its years alone say it is one.
    text = (f"Example Admissions Office, Norvale University, 2001-2004\t{OTHER_PROSE}"
            f"\t{_COMMITTEE}, Norvale University, 2008-2012")
    assert not sp._looks_like_multiple_records([text])
    own_name = sp._committee_name_tokens(_committee(text)["extracted_fields"])
    assert not any(sp._names_another_committee(part, own_name) for part in text.split("\t"))
    assert sp._own_committee_line(_committee(text)) == ""
    for code in ("O", "B1"):
        assert sp._prose_text(code, dict(_committee(text), taxonomy_code=code), {}) == text


_KESTREL = "Example Admissions Appeals Committee, Kestrel Medical Society"


@pytest.mark.parametrize("text", [
    # An undated committee before the extracted one, which carries the years.
    # It shares a word of the extracted name ("Example"), not all of them.
    f"Member, {_KESTREL}\t{_COMMITTEE}, Norvale University, Member, 2008-2012\t{PROSE}",
    # Two undated ones, a panel among them.
    f"Chair, Example Formulary Review Panel, Osprey Regional Hospital\tMember, {_KESTREL}"
    f"\t{_COMMITTEE}, Norvale University, Member, 2008-2012\t{PROSE}",
    # One dated one, the extracted committee's years in its fields only.
    f"Member, {_KESTREL} (2001-2004)\tMember, {_COMMITTEE}, Norvale University\t{PROSE}",
], ids=["undated_sibling", "two_undated_siblings", "dated_sibling_own_years_in_fields"])
def test_a_committee_line_naming_another_committee_offers_none(text):
    """At most one part is dated, so the writer's #660 test reads one record
    and one row prints, of the committee stage 4 extracted: the other
    committee's record would be offered as its prose, under its row."""
    entry = _committee(text)
    assert not sp._looks_like_multiple_records(text.split("\t"))
    assert sp._own_committee_line(entry) == ""
    table, lines = _committee_rows(_COMMITTEE_ROW)
    assert sp.plan_subpoints({"P": [entry]}, _haystack(), lines, {"P": sp.SectionSpan(0, None)},
                             rows_written=lambda e: [table.rows[0]._tr]) == []


@pytest.mark.parametrize("prose", [
    # It names its own committee again, every word of the name.
    f"As secretary to the {_COMMITTEE}, {PROSE.lower()}",
    # "the Committee" refers back to the one the line named: all its words are the name's.
    f"{PROSE} for the Committee",
], ids=["names_its_own", "refers_back"])
def test_a_committee_line_naming_only_its_own_committee_offers_its_prose(prose):
    text = f"{_COMMITTEE}, Norvale University, Member, 2008-2012\t{prose}"
    entry = _committee(text)
    assert sp._own_committee_line(entry) == text
    table, lines = _committee_rows(_COMMITTEE_ROW)
    (planned,) = sp.plan_subpoints({"P": [entry]}, _haystack(), lines,
                                   {"P": sp.SectionSpan(0, None)},
                                   rows_written=lambda e: [table.rows[0]._tr])
    assert planned.paragraphs == (prose,)


@pytest.mark.parametrize("field", ["committee", "activity"])
def test_a_committees_own_name_is_read_from_the_fields_its_row_prints(field):
    # Stage 4's off-schema names, which the row prints when the schema's is empty.
    text = f"Member, {_COMMITTEE}, Norvale University, 2008-2012\t{PROSE}"
    assert sp._own_committee_line(_entry(text, code="P", **{field: _COMMITTEE})) == text
    other = f"Member, {_KESTREL}\t{text}"
    assert sp._own_committee_line(_entry(other, code="P", **{field: _COMMITTEE})) == ""


@pytest.mark.parametrize("body", ["Committee", "Subcommittee", "Council", "Board", "Panel",
                                  "Commission", "Senate", "Task Force", "Taskforce",
                                  "Working Group", "Workgroup"])
def test_a_part_naming_a_committee_body_not_the_entrys_own_is_another_record(body):
    part = f"Member, Example Admissions {body}, Kestrel Medical Society"
    assert sp._names_another_committee(part, {"curriculum", "steering"})
    assert not sp._names_another_committee(part, {"admissions"})
    # In lower case it is no name, nor is a word that only begins with one.
    assert not sp._names_another_committee(part.lower(), {"curriculum", "steering"})
    assert not sp._names_another_committee(part.replace(body, f"{body}ist"), {"curriculum"})


@pytest.mark.parametrize("part, other", [
    # The name wrapped onto the next part, or typed with a typo: most of its words.
    ("Committee, Norvale University, 2008-2012", False),
    ("Elected member of Example Curriculum Steerng Committee, Norvale University", False),
    # Another committee shares the word for its kind and one more ("Example"), no more.
    ("Member, Example Admissions Appeals Committee, Kestrel Medical Society", True),
    # A committee on a subject is named with it.
    ("Member, Committee on the Admissions Appeals, Kestrel Medical Society", True),
    # The part holds the whole name: its record, and a sub-committee one of its details.
    (f"Member, {_COMMITTEE} (Example Admissions Subcommittee)", False),
], ids=["wrapped", "typo", "another", "committee_on", "sub_committee"])
def test_another_committee_shares_no_more_than_half_its_words_with_the_entrys(part, other):
    own_name = sp._committee_name_tokens({"committee_name": _COMMITTEE})
    assert sp._names_another_committee(part, own_name) is other


def test_a_committee_on_a_subject_is_named_with_every_word_of_it():
    own_name = sp._committee_name_tokens({"committee_name": "Kestrel Admissions Committee"})
    assert sp._names_another_committee("Committee on Admissions Appeals Review", own_name)


def test_a_committee_line_naming_a_committee_its_fields_do_not_name_offers_none():
    # Stage 4 named no committee, or only in a list of bare names: whose the
    # named one is cannot be shown. A line naming none is its own.
    named = f"Member, {_KESTREL}, 2001-2004\t{PROSE}"
    plain = f"Member, Example Admissions Office, 2001-2004\t{PROSE}"
    for fields in ({"role": "Member", "start_date": "2001"}, {"committee_name": [_KESTREL]}):
        assert sp._own_committee_line(_entry(named, code="P", **fields)) == ""
        assert sp._own_committee_line(_entry(plain, code="P", **fields)) == plain
    assert sp._own_committee_line({"taxonomy_code": "P", "text": plain}) == plain


@pytest.mark.parametrize("field", ["committee_name", "committee", "activity"])
def test_a_committee_list_of_several_records_offers_none_though_one_names_no_committee(field):
    """The writer prints no row for a record naming no committee (#256): a
    list of a nameless record and a named one, under any of its committee
    fields, prints one row, of the named one, and the other's text would be
    offered as its prose. No part names a committee body here, so the list
    alone says the entry holds two."""
    programme = "Example Clerkship Steering Programme"
    text = f"Member, 2001-2004, {OTHER_PROSE}\t{programme}, Norvale University, Member"
    named = {"committee_name": programme, "institution": "Norvale University", "role": "Member"}
    nameless = {"role": "Member", "start_date": "2001", "end_date": "2004"}
    entry = _entry(text, code="P", **{field: [nameless, named]})
    assert sp._own_committee_line(entry) == ""
    table, lines = _committee_rows([f"{programme}, Norvale University", "Member", ""])
    assert sp.plan_subpoints({"P": [entry]}, _haystack(), lines, {"P": sp.SectionSpan(0, None)},
                             rows_written=lambda e: [table.rows[0]._tr]) == []
    # A list of one record is one record.
    assert sp._own_committee_line(_entry(text, code="P", **{field: [named]})) == text


def test_plan_offers_no_prose_of_a_committee_the_pii_pass_withheld():
    table, lines = _committee_rows(_COMMITTEE_ROW)
    spans = {"P": sp.SectionSpan(0, None)}
    entry = _committee()
    (planned,) = sp.plan_subpoints({"P": [entry]}, _haystack(), lines, spans,
                                   rows_written=lambda e: [table.rows[0]._tr])
    assert planned.paragraphs == (PROSE,)

    entry["_pii_withheld"] = True
    assert sp.plan_subpoints({"P": [entry]}, _haystack(), lines, spans,
                             rows_written=lambda e: [table.rows[0]._tr]) == []


_TALK = "Example Advances in Coastal Wetland Hydrology"
_TALK_ROW = [_TALK, "Example Grand Rounds, Norvale University", "2016"]


def _talk_rows(*rows):
    """A line naming the talk and its venue, then a talk table, one row of
    cells per item; and their anchor candidates."""
    doc = Document()
    doc.add_paragraph(f"{_TALK}, Norvale University")
    table = doc.add_table(rows=len(rows), cols=3)
    for row, cells in zip(table.rows, rows, strict=True):
        for cell, text in zip(row.cells, cells, strict=True):
            cell.text = text
    return table, list(sp.anchor_candidates(doc))


def _talk(text=f"{_TALK}, Example Grand Rounds, Norvale University, 2016\t{PROSE}", **fields):
    return _entry(text, code="R", **{"title": _TALK, "event_name": "Example Grand Rounds",
                                     "location": "Norvale University", "date": "2016", **fields})


def _plan_talk(entry, *rows):
    table, lines = _talk_rows(*(rows or (_TALK_ROW,)))
    return sp.plan_subpoints({"R": [entry]}, _haystack(), lines, {"R": sp.SectionSpan(0, None)},
                             rows_written=lambda e: [table.rows[0]._tr] if e is entry else [])


def test_plan_places_a_talk_only_under_its_own_row():
    # One talk given twice: the rows differ only in their years, which are
    # digits, and words alone take the first row holding them.
    table, lines = _talk_rows([*_TALK_ROW[:2], "2019"], _TALK_ROW)
    entry, spans = _talk(), {"R": sp.SectionSpan(0, None)}
    assert sp.best_anchor(sp._value_tokens(entry, "R"), lines) is lines[1]

    def written(recorded):
        return lambda e: recorded if e is entry else []

    (planned,) = sp.plan_subpoints({"R": [entry]}, _haystack(), lines, spans,
                                   rows_written=written([table.rows[1]._tr]))
    assert planned.anchor is lines[2] and planned.paragraphs == (PROSE,)
    assert (planned.code, planned.entry_id) == ("R", id(entry))
    # No row recorded for it, a paragraph outside any table, or no ledger: none.
    stray = Document().add_paragraph(_TALK)._p
    for rows_written in (written([]), written([stray]), None):
        assert sp.plan_subpoints({"R": [entry]}, _haystack(), lines, spans,
                                 rows_written=rows_written) == []


def test_plan_takes_a_talks_one_own_row_whatever_its_words():
    # An acronym title at an acronym's venue holds no distinctive word.
    entry = _entry(f"IBD, NVU, 2016\t{PROSE}", code="R", title="IBD", location="NVU", date="2016")
    table, lines = _talk_rows(["IBD", "NVU", "2016"])
    assert sp.best_anchor(sp._value_tokens(entry, "R"), lines[1:]) is None
    (planned,) = sp.plan_subpoints({"R": [entry]}, _haystack(), lines, {"R": sp.SectionSpan(0, None)},
                                   rows_written=lambda e: [table.rows[0]._tr])
    assert planned.anchor is lines[1] and planned.paragraphs == (PROSE,)


@pytest.mark.parametrize("field", sp._PRESENTATION_ROW_FIELDS)
def test_a_talk_rows_fallback_fields_count_as_its_rendered_values(field):
    # The row prints them when the schema's are empty, though
    # `_RENDERED_FIELDS` cannot list them. Another code's are its own.
    entry = _entry("Invited talk", code="R", **{field: "Estuarine Sediment Geochemistry"})
    assert {"estuarine", "sediment", "geochemistry"} <= sp._value_tokens(entry, "R")
    assert not {"estuarine"} & sp._value_tokens(dict(entry, taxonomy_code="H"), "H")


def test_the_talk_row_fields_are_the_writers_cells_and_their_off_schema_names():
    # The writer's precedence per cell, and the names the schema does not define.
    from unified_pipeline.stage6 import formatting
    from unified_pipeline.stage6.sections import presentations

    assert sp.PRESENTATION_TITLE_FIELDS == ("title", "presentation_title")
    assert sp.PRESENTATION_VENUE_FIELDS == ("institution", "location", "venue")
    assert sp.PRESENTATION_EVENT_FIELDS == ("event_name", "conference_name")
    assert sp.PRESENTATION_DATE_FIELDS == ("year", "date", "start_date")
    assert sp._PRESENTATION_ROW_FIELDS == ("presentation_title", "institution", "venue",
                                           "conference_name", "year", "start_date", "end_date")
    assert presentations._TALK_DATE_KEYS is sp.PRESENTATION_DATE_FIELDS
    assert presentations._TALK_EVENT_KEYS is sp.PRESENTATION_EVENT_FIELDS
    # The Dates cell's fields are the ones its builder reads.
    assert sp.PRESENTATION_DATE_FIELDS is formatting.PRESENTATION_DATE_FIELDS
    assert sp.PRESENTATION_END_DATE_FIELD is formatting.PRESENTATION_END_DATE_FIELD


@pytest.mark.parametrize("segments, index, read", [
    # The last of two records, its line's two segments pairing with them: its own.
    (["Wetland line", f"Estuary line, {PROSE}"], 1, f"Estuary line, {PROSE}"),
    # The last of two records on a line of three segments: none of it.
    (["Wetland line", PROSE, "Estuary line"], 1, ""),
    # An earlier record: the line fan-out gave it, whatever it holds.
    ([f"Wetland line, {PROSE}", "Estuary line"], 0, f"Wetland line, {PROSE}\tEstuary line"),
])
def test_a_talk_reads_its_prose_from_its_own_line(segments, index, read):
    entry = _degree_record("\t".join(segments), index, 2)
    entry["taxonomy_code"] = "R"
    assert sp._prose_text("R", entry, {}) == read


_OTHER_TALK = "Estuary Sediment Transport in Tidal Marshes, Kestrel Institute"


@pytest.mark.parametrize("text, own", [
    # One talk and its year, then its audience: its own line.
    (f"{_TALK}, Norvale University, 2016\t{PROSE}", True),
    # Its year opens the line, and its full date closes it: one talk.
    (f"2016 {_TALK}, Norvale University, March 2016\t{PROSE}", True),
    # Two talks, each ending in its year, stage 2's tabs between them.
    (f"{_OTHER_TALK}, 2014\t{OTHER_PROSE}\t{_TALK}, Norvale University, 2016\t{PROSE}", False),
    # Each opening with its year (`_holds_dated_records`'s shape).
    (f"2014 {_OTHER_TALK}\t{OTHER_PROSE}\t2016 {_TALK}\t{PROSE}", False),
    # Each opening with its year, in one paragraph: one part.
    (f"2014 {_OTHER_TALK}: {OTHER_PROSE} 2016 {_TALK}, Norvale University. {PROSE}", False),
    # The same with a month before each year.
    (f"May 2014 {_OTHER_TALK}. {OTHER_PROSE} March 2016 {_TALK}. {PROSE}", False),
    # Each ending in its year in brackets.
    (f"{_OTHER_TALK}, Osprey Bay (2014)\t{_TALK}, Norvale University (2016).\t{PROSE}", False),
], ids=["one_talk", "year_then_date", "trailing_years", "leading_years", "one_paragraph",
        "month_years", "bracketed_years"])
def test_a_talk_line_holding_several_dated_talks_offers_none(text, own):
    """Which talk a prose line is about cannot be told: the other's text
    would be offered as the extracted one's prose, under its row."""
    entry = _talk(text)
    assert sp._own_talk_line(entry, "R") == (text if own else "")
    assert [p.paragraphs for p in _plan_talk(entry)] == ([(PROSE,)] if own else [])


def test_a_talk_with_no_date_kept_and_one_dated_part_is_one_talk():
    # The Dates cell prints nothing, and one part opens with a date: one talk.
    text = f"2016 {_TALK}, Norvale University\t{PROSE}"
    entry = _entry(text, code="R", title=_TALK, location="Norvale University")
    assert sp._talk_dates(entry["extracted_fields"], "R") == 0
    assert sp._own_talk_line(entry, "R") == text


@pytest.mark.parametrize("dates, own", [
    # The Dates cell prints both years: the two dated parts are this talk's.
    ({"date": "2014; 2016"}, True),
    ({"date": "2016", "additional_dates": ["2014"]}, True),
    ({"year": None, "date": None, "additional_dates": ["2014", "2016"]}, True),
    # It prints one: the other dated part is a talk the row does not print.
    ({"date": "2016"}, False),
    # Three dated parts, two dates printed: one more talk than the row prints.
    ({"date": "2014; 2016"}, "three"),
], ids=["date_list", "further_span", "spans_only", "one_date", "one_too_many"])
def test_one_talk_given_on_several_dates_is_one_record(dates, own):
    parts = [f"2014 {_TALK}, Kestrel Institute", f"2016 {_TALK}, Norvale University", PROSE]
    if own == "three":
        parts.insert(2, f"2018 {_OTHER_TALK}")
    text = "\t".join(parts)
    entry = _talk(text, **dates)
    entry["extracted_fields"] = {k: v for k, v in entry["extracted_fields"].items() if v is not None}
    assert sp._own_talk_line(entry, "R") == (text if own is True else "")


@pytest.mark.parametrize("fields, dates", [
    ({}, 0),
    ({"date": "2016"}, 1),
    ({"year": "2016", "date": "2010"}, 1),
    ({"date": "2014; 2016"}, 2),
    # A list repeating one year prints it once.
    ({"date": "2016-03-01; 2016-05-02"}, 1),
    ({"start_date": "2016", "additional_dates": ["2014", "2018"]}, 3),
    # A further span repeating the talk's own date is not printed again.
    ({"date": "2016", "additional_dates": ["2016"]}, 1),
    # The string "None" is no date.
    ({"year": "None", "additional_dates": ["2016"]}, 1),
    ({"year": "None"}, 0),
])
def test_a_talks_dates_are_counted_as_its_dates_cell_prints_them(fields, dates):
    from unified_pipeline.stage6.sections import presentations

    assert sp._talk_dates(fields, "R") == dates
    cell = presentations._talk_date_cell(fields)
    assert (len(cell.split(", ")) if cell else 0) == dates


@pytest.mark.parametrize("dates", [
    {"date": "2016", "additional_dates": ["2016"]},
    {"year": "None", "date": None, "additional_dates": ["2016"]},
], ids=["span_repeats_its_date", "none_string"])
def test_a_date_its_dates_cell_does_not_print_marks_no_talk_of_its_own(dates):
    # The cell prints one date, so the other dated talk is not this one.
    text = "\t".join([f"{_OTHER_TALK}, 2014", f"{_TALK}, Norvale University, 2016", PROSE])
    entry = _talk(text, **dates)
    entry["extracted_fields"] = {k: v for k, v in entry["extracted_fields"].items() if v is not None}
    assert sp._own_talk_line(entry, "R") == ""
    assert _plan_talk(entry) == []


@pytest.mark.parametrize("line, records", [
    ("Example lecture, Norvale University, 2016", 1),
    ("2016 Example lecture, Norvale University", 1),
    # A year inside the meeting's name: one record.
    ("Example Summit 2016, Norvale University", 1),
    ("2014 Example lecture\t2016 Example seminar", 2),
    ("Example lecture, 2014\tExample seminar, 2016", 2),
    ("2014 Example lecture, Kestrel Institute 2016 Example seminar, Norvale University", 2),
    ("May 2014 Example lecture. March 2016 Example seminar. April 2018 Example panel", 3),
    # A numeric month before each year (EBYSBC DPEHSZ 227's shape).
    ("11/2014 Example lecture, Kestrel Institute 1/2016 Example seminar", 2),
    # A range opens one record, whatever its two years.
    ("2010 - 2012 Example lecture series", 1),
    # A year in brackets ending a part, before a full stop or not; a range in
    # brackets, anywhere.
    ("Example lecture, Norvale University (2016)", 1),
    ("Example lecture, Kestrel Institute (2014)\tExample seminar, Norvale University (2016).", 2),
    ("Example lecture series (2010-2012)\tExample seminar, 2016", 2),
    # A bracketed year inside a part does not end it.
    ("Example lecture (2014), Kestrel Institute\tExample seminar, 2016", 1),
])
def test_a_talk_lines_dates_mark_its_records(line, records):
    assert sp._talk_records(line) == records


@pytest.mark.parametrize("line, another", [
    # Its own title, quoted, punctuated or cased differently.
    (f"“{_TALK.lower()}.” Example Grand Rounds", False),
    (f'"{_TALK}", Example Grand Rounds', False),
    # Its meeting, quoted.
    (f"{_TALK}, “Example Grand Rounds”", False),
    # A word in quotes is no title.
    (f"{_TALK}, a “Student Choice” lecture", False),
    # Another title, in either kind of quotes, of three words or more.
    (f"“{_TALK}”. “Estuary Sediment Transport in Tidal Marshes”", True),
    (f'{_TALK}. "Estuary Sediment Transport in Tidal Marshes", Kestrel Institute', True),
    (f"{_TALK}. “Tidal Marsh Sediments”", True),
    # Half its words the talk's own: another; more than half: its own.
    (f"{_TALK}. “Coastal Wetland Restoration Funding”", True),
    ("“Coastal Wetland Hydrology Funding Review”", False),
], ids=["own_lower", "own_straight", "own_meeting", "short_quote", "another_curly",
        "another_straight", "another_three_words", "half_shared", "most_shared"])
def test_a_quoted_title_not_the_talks_own_is_another_talk(line, another):
    assert sp._quotes_another_talk(line, _talk()["extracted_fields"]) is another


def test_a_quoted_title_of_a_talk_with_no_title_or_meeting_cannot_be_shown_its_own():
    line = f"“{_TALK}”, Norvale University"
    assert sp._quotes_another_talk(line, {"location": "Norvale University"})
    assert not sp._quotes_another_talk("Norvale University seminar", {})


@pytest.mark.parametrize("fields, listed", [
    ({"additional_presentations": [{"title": "Estuary Sediment Transport", "date": "2014"}]}, True),
    ({"stage4_records": [{"location": "Kestrel Institute"}, {"event_name": "Example Seminar"}]}, True),
    # A record naming its venue alone is a talk too.
    ({"additional_presentations": [{"location": "Kestrel Institute", "date": "2014"}]}, True),
    # Date spans are no talks, nor is a list of strings.
    ({"additional_dates": [{"start_date": "2014", "end_date": "2015"}]}, False),
    ({"date": ["2014", "2016"]}, False),
    ({"title": _TALK}, False),
])
def test_a_list_of_talks_under_a_field_is_several_records(fields, listed):
    assert sp._lists_talks(fields) is listed
    text = f"{_TALK}, Norvale University, 2016\t{PROSE}"
    assert sp._own_talk_line(_talk(text, **fields), "R") == ("" if listed else text)


def test_only_a_talk_reads_its_line_for_several_talks():
    # The degree and leadership rules read date-led parts only, and the
    # committee rule names no talk: a line of two talks in one paragraph is
    # one part to each of them.
    text = f"2014 {_OTHER_TALK}: {OTHER_PROSE} 2016 {_TALK}, Norvale University. {PROSE}"
    assert sp._own_talk_line(_talk(text), "R") == ""
    for code in ("O", "B1", "P"):
        assert sp._prose_text(code, dict(_talk(text), taxonomy_code=code), {}) == text


def test_plan_offers_no_prose_of_a_talk_the_pii_pass_withheld():
    entry = _talk()
    (planned,) = _plan_talk(entry)
    assert planned.paragraphs == (PROSE,)

    entry["_pii_withheld"] = True
    assert _plan_talk(entry) == []


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
