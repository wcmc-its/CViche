"""Regression guard for issue #659 (five of ten sites): entry.get('extracted_fields', {})
crashes on an explicit None -- plus the behaviour each touched section
writer is expected to produce once it no longer crashes (#739 review).

`dict.get(key, default)` only substitutes `default` when the key is absent --
an entry that carries `extracted_fields: None` explicitly still gets `None`
back, and the very next `.get()` call on it raises `AttributeError: 'NoneType'
object has no attribute 'get'`. `licensure.py` and `postdoc_training.py`
already use the defended form `entry.get('extracted_fields') or {}`; this
fixes the same idiom in mentoring.py (the N3B ongoing-mentorship rule and
the mentee normalizer, which the two table-fill loops now go through),
other_education.py and patents.py.

research_support.py (4 sites) and formatting/values.py (1 site) are the
remaining five of the ten sites #659 names; they land in sibling PRs (#659
closes only once all three land -- see PR body).

Every test drives the real `_fill_*` section-writer entrypoint (or the
module-level classifier it delegates to) on a minimal document and asserts
where the content landed, not merely that the call returned. Fixtures are
fictional.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_extracted_fields_none_safety.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.table import Table  # noqa: E402
from docx.text.paragraph import Paragraph  # noqa: E402

from unified_pipeline.stage6.sections.mentoring import (  # noqa: E402
    _normalize_mentee,
    _partition_mentoring_entries,
)
from unified_pipeline.stage6.sections.other_education import (  # noqa: E402
    _normalize_other_education_entry,
)
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


def _new_generator() -> WCMTemplateGenerator:
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    return gen


def _body_after(doc, heading_text: str, count: int) -> list[tuple[str, str]]:
    """The `count` body elements following the paragraph whose stripped text
    equals `heading_text`, as ('p', text) or ('tbl', text of row 0 cell 1) --
    the cell that carries a mentee's name in a mentee table."""
    body = list(doc.element.body)
    for para in doc.paragraphs:
        if para.text.strip().lower() == heading_text.lower():
            start = body.index(para._element)
            break
    else:
        raise AssertionError(f"no paragraph {heading_text!r} in document")
    shape = []
    for element in body[start + 1:start + 1 + count]:
        if element.tag == qn('w:sectPr'):
            break
        if element.tag == qn('w:tbl'):
            table = Table(element, doc)
            shape.append(('tbl', table.rows[0].cells[1].text))
        else:
            shape.append(('p', Paragraph(element, doc).text))
    return shape


def _mentoring_doc() -> WCMTemplateGenerator:
    gen = _new_generator()
    gen.doc.add_paragraph("MENTORING")
    gen.doc.add_paragraph("Current Mentees:")
    gen.doc.add_paragraph("Past Mentees:")
    return gen


def _mentee(code: str, name: str, **fields) -> dict:
    return {'taxonomy_code': code, 'text': f"{name} mentee entry",
            'extracted_fields': {'mentee_name': name, **fields}}


# --- mentoring: extracted_fields=None at every #659 site ------------------------

@pytest.mark.parametrize("code, heading", [("N3A", "Current Mentees:"),
                                           ("N3B", "Past Mentees:")])
def test_mentoring_none_extracted_fields_renders_as_a_summary_line(code, heading):
    """An N3A or N3B entry with extracted_fields=None used to crash -- N3B in
    the ongoing-mentorship rule (mentoring.py:90 pre-fix), both codes in the
    table-fill loops (:161/:182 pre-fix). With no fields it names no mentee,
    so it is an aggregate summary: rendered as a plain line directly under
    its heading, with its text intact."""
    gen = _mentoring_doc()
    entry = {'taxonomy_code': code, 'text': 'Ph.D. Graduated: 38',
             'extracted_fields': None}

    gen._fill_mentoring({code: [entry]})

    assert _body_after(gen.doc, heading, 1) == [('p', 'Ph.D. Graduated: 38')]
    assert len(gen.doc.tables) == 0
    assert gen.stats['entries_inserted'] == 1


def test_normalize_mentee_tolerates_none_extracted_fields():
    """The table-fill loops read fields through `_normalize_mentee`; an
    explicit None yields an empty record (no name), never an AttributeError."""
    record = _normalize_mentee({'taxonomy_code': 'N3A', 'extracted_fields': None})
    assert record.name == ''
    assert record.site_position == ''
    assert record.mentoring_period == ''


def test_partition_tolerates_none_extracted_fields_in_ongoing_rule():
    """The N3B ongoing-mentorship rule is the first #659 site reached; a None
    entry is neither moved nor dropped -- it partitions as a past summary."""
    entry = {'taxonomy_code': 'N3B', 'text': 'Completed: 27', 'extracted_fields': None}
    partition = _partition_mentoring_entries({'N3B': [entry]})
    assert partition.past_summaries == (entry,)
    assert partition.moved_to_current == 0
    assert partition.current == () and partition.past == ()


# --- N1/N2: extracted_fields=None (#529) -----------------------------------------

def _n1_n2_doc() -> WCMTemplateGenerator:
    gen = _new_generator()
    gen.doc.add_paragraph("MENTORING")
    gen.doc.add_paragraph(
        "Leadership and mentoring in programs (Describe activity; include dates)")
    gen.doc.add_paragraph(
        "Institutional Training Grants and Mentored Trainee Grants")
    return gen


def test_n1_none_extracted_fields_renders_as_its_raw_text():
    """`_fill_mentoring` reads `entry.get('extracted_fields') or {}`, the
    #659-defended idiom, before handing fields to `_program_leadership_line`;
    an explicit None degrades to the entry's text, same as an entry with no
    fields at all -- never an AttributeError."""
    gen = _n1_n2_doc()
    entry = {'taxonomy_code': 'N1', 'text': 'Directed a mentoring initiative.',
             'extracted_fields': None}

    gen._fill_mentoring({'N1': [entry]})

    assert _body_after(gen.doc, "Leadership and mentoring in programs "
                       "(Describe activity; include dates)", 1) == [
        ('p', 'Directed a mentoring initiative.')]
    assert gen.stats['entries_inserted'] == 1


def test_n2_none_extracted_fields_renders_as_its_raw_text():
    """Same idiom for N2: a None `extracted_fields` reads as sparse (no
    title/agency/grant_number), so it degrades to a plain line, not a
    crash and not an empty table."""
    gen = _n1_n2_doc()
    entry = {'taxonomy_code': 'N2', 'text': 'A training grant with no fields.',
             'extracted_fields': None}

    gen._fill_mentoring({'N2': [entry]})

    assert _body_after(gen.doc, "Institutional Training Grants and "
                       "Mentored Trainee Grants", 1) == [
        ('p', 'A training grant with no fields.')]
    assert len(gen.doc.tables) == 0
    assert gen.stats['entries_inserted'] == 1


# --- mentoring: Past -> Current migration ---------------------------------------

@pytest.mark.parametrize("fields", [
    {'start_date': '2019', 'end_date': 'present'},
    {'start_date': '2019', 'end_date': 'Present'},
    {'start_date': '2019', 'end_date': '2019-present'},
    {'start_date': '2019', 'end_date': 'ongoing'},
    {'start_date': '2019', 'end_date': 'current'},
    {'start_date': '2019', 'end_date': 'now'},
    {'start_date': '2019'},                    # start but no end at all
    {'start_date': '2019', 'end_date': ''},    # start but empty end
    {'start_date': '2019', 'end_date': None},  # start but null end
], ids=lambda f: repr(f.get('end_date', '<absent>')))
def test_partition_moves_ongoing_past_mentee_to_current(fields):
    entry = _mentee('N3B', 'Ada Lovelace', **fields)
    partition = _partition_mentoring_entries({'N3B': [entry]})
    assert partition.current == (entry,)
    assert partition.past == ()
    assert partition.moved_to_current == 1


@pytest.mark.parametrize("fields", [
    {'start_date': '2015', 'end_date': '2019'},
    {'end_date': '2019'},
    {},                                        # no dates at all: not ongoing
], ids=lambda f: repr(f))
def test_partition_keeps_ended_or_undated_past_mentee_in_past(fields):
    entry = _mentee('N3B', 'Ada Lovelace', **fields)
    partition = _partition_mentoring_entries({'N3B': [entry]})
    assert partition.past == (entry,)
    assert partition.current == ()
    assert partition.moved_to_current == 0


def test_ongoing_past_mentee_renders_under_current_mentees():
    """The migration is visible in the document: the N3B entry's table sits
    under "Current Mentees:" and nothing sits under "Past Mentees:"."""
    gen = _mentoring_doc()
    entries_by_code = {
        'N3B': [_mentee('N3B', 'Ada Lovelace', start_date='2019', end_date='present'),
                _mentee('N3B', 'Grace Hopper', start_date='2010', end_date='2014')],
    }

    gen._fill_mentoring(entries_by_code)

    assert _body_after(gen.doc, "Current Mentees:", 2) == [
        ('tbl', 'Ada Lovelace'), ('p', '')]
    assert _body_after(gen.doc, "Past Mentees:", 2) == [
        ('tbl', 'Grace Hopper'), ('p', '')]


# --- mentoring: summary and N4 outcome lines ------------------------------------

def test_mentoring_summary_lines_sit_above_the_tables():
    """Aggregate N3A/N3B lines land directly under their heading, above the
    mentee tables, in input order."""
    gen = _mentoring_doc()
    entries_by_code = {
        'N3A': [_mentee('N3A', 'Ada Lovelace'),
                {'taxonomy_code': 'N3A', 'text': 'Current Ph.D. Students: 12',
                 'extracted_fields': {'mentee_name': None}},
                {'taxonomy_code': 'N3A', 'text': 'Current Postdocs: 3',
                 'extracted_fields': {}}],
    }

    gen._fill_mentoring(entries_by_code)

    assert _body_after(gen.doc, "Current Mentees:", 4) == [
        ('p', 'Current Ph.D. Students: 12'),
        ('p', 'Current Postdocs: 3'),
        ('tbl', 'Ada Lovelace'),
        ('p', ''),
    ]
    assert gen.stats['tables_populated'] == 1
    assert gen.stats['entries_inserted'] == 3


def test_n4_outcomes_render_under_the_mentoring_header():
    """N4 lines -- routed as N4, or rewritten to N3A by the mismatch corrector
    with the original stashed -- go under MENTORING, not under either mentee
    heading, in input order (N4 first, then the reclaimed N3A)."""
    gen = _mentoring_doc()
    entries_by_code = {
        'N4': [{'taxonomy_code': 'N4', 'text': 'Three mentees now hold faculty posts.',
                'extracted_fields': {}}],
        'N3A': [{'taxonomy_code': 'N3A', 'taxonomy_code_original': 'N4',
                 'text': 'Two mentees won K awards.', 'extracted_fields': {}}],
    }

    gen._fill_mentoring(entries_by_code)

    assert _body_after(gen.doc, "MENTORING", 2) == [
        ('p', 'Three mentees now hold faculty posts.'),
        ('p', 'Two mentees won K awards.'),
    ]
    assert _body_after(gen.doc, "Current Mentees:", 1) == [('p', 'Past Mentees:')]
    assert len(gen.doc.tables) == 0


# --- other education / patents: extracted_fields=None -------------------------

def test_other_education_none_extracted_fields_does_not_raise():
    """A B2 entry with extracted_fields=None used to crash the fill loop at
    other_education.py:85. With no fields it has no program and no
    institution, so it is skipped and the table keeps only its header row."""
    gen = _new_generator()
    gen.doc.add_paragraph("OTHER EDUCATIONAL")
    gen.doc.add_table(rows=1, cols=3)

    entries = [{'taxonomy_code': 'B2', 'text': 'Some training program',
                'extracted_fields': None}]

    gen._fill_other_education(entries)  # must not raise AttributeError

    assert len(gen.doc.tables[0].rows) == 1


def test_patents_none_extracted_fields_does_not_raise():
    """An M2D entry with extracted_fields=None used to crash the fill loop
    at patents.py:80. With no title and no patent number it is sparse, so
    no table is built."""
    gen = _new_generator()
    gen.doc.add_paragraph("Patents & Inventions")

    entries = [{'taxonomy_code': 'M2D', 'text': 'A patent with no fields',
                'extracted_fields': None}]

    gen._fill_patents(entries)  # must not raise AttributeError

    assert len(gen.doc.tables) == 0


# --- other education: date precedence and attribution ----------------------------

def _b2_doc() -> WCMTemplateGenerator:
    gen = _new_generator()
    gen.doc.add_paragraph("OTHER EDUCATIONAL")
    header = gen.doc.add_table(rows=1, cols=3).rows[0].cells
    header[0].text, header[1].text, header[2].text = "Description", "Institution", "Dates"
    return gen


def _insertions(cell) -> list[tuple[str, str]]:
    """(author, text) of every tracked insertion in a cell, in order."""
    return [(ins.get(qn('w:author')),
             ''.join(t.text or '' for t in ins.iter(qn('w:t'))))
            for ins in cell._tc.iter(qn('w:ins'))]


@pytest.mark.parametrize("fields, text, dates, from_text", [
    ({'start_date': '2017-08', 'end_date': '2021-07', 'year': '2019'},
     'Certificate, August 2020', '08/17-07/21', False),
    ({'start_date': '2017-08', 'year': '2019'}, 'Certificate, August 2020',
     '08/17-Present', False),
    ({'end_date': '2021-07', 'year': '2019'}, 'Certificate, August 2020',
     '07/21', False),
    ({'year': '2019'}, 'Certificate, August 2020', '2019', False),
    ({'year_awarded': '2018'}, 'Certificate, August 2020', '2018', False),
    ({}, 'Certificate in Epidemiology, August 2020', '2020', True),
    ({}, 'Certificate 2019-2021', '2021', True),
    ({}, 'Certificate in Epidemiology', '', False),
], ids=['range-beats-year-and-text', 'start-only-beats-year', 'end-only-beats-year',
        'year-beats-text', 'year_awarded-beats-text', 'text-month-year',
        'text-range-takes-end-year', 'nothing'])
def test_b2_date_precedence(fields, text, dates, from_text):
    """start/end -> year (year, then year_awarded) -> raw text, each step
    winning outright; only the raw-text step is marked as inferred."""
    record = _normalize_other_education_entry(
        {'taxonomy_code': 'B2', 'text': text,
         'extracted_fields': {'program_name': 'Certificate', **fields}})
    assert (record.dates, record.dates_from_text) == (dates, from_text)


def test_b2_text_extracted_year_renders_as_a_tracked_insertion():
    gen = _b2_doc()
    gen._fill_other_education([
        {'taxonomy_code': 'B2', 'text': 'Certificate in Epidemiology, August 2020',
         'extracted_fields': {'program_name': 'Certificate in Epidemiology'}}])

    row = gen.doc.tables[0].rows[1]
    assert row.cells[0].text == 'Certificate in Epidemiology'
    assert _insertions(row.cells[2]) == [('Text Extraction', '2020')]
    assert _insertions(row.cells[0]) == []


def test_b2_field_year_renders_as_plain_text():
    gen = _b2_doc()
    gen._fill_other_education([
        {'taxonomy_code': 'B2', 'text': 'Certificate in Epidemiology, August 2020',
         'extracted_fields': {'program_name': 'Certificate in Epidemiology',
                              'year': '2019'}}])

    row = gen.doc.tables[0].rows[1]
    assert row.cells[2].text == '2019'
    assert _insertions(row.cells[2]) == []


def test_b2_enriched_location_is_attributed_inside_the_institution_cell():
    """Stage-5b enrichment supplies the cleaned name (plain text) and the
    city/state, which is appended as a tracked insertion after it."""
    gen = _b2_doc()
    gen._fill_other_education([
        {'taxonomy_code': 'B2', 'text': 'Certificate in Biostatistics, Duke University, 2019',
         'extracted_fields': {'program_name': 'Certificate in Biostatistics',
                              'institution': 'Duke University, Durham', 'year': '2019'},
         'institution_enrichment': {'cleaned_name': 'Duke University', 'city': 'Durham',
                                    'state': 'North Carolina', 'country_code': 'US'}}])

    cell = gen.doc.tables[0].rows[1].cells[1]
    plain_runs = ''.join(r.text for r in cell.paragraphs[0].runs)
    assert plain_runs == 'Duke University'
    assert _insertions(cell) == [('Institution Enrichment', ', Durham, NC')]


def test_b2_extracted_location_is_plain_text():
    gen = _b2_doc()
    gen._fill_other_education([
        {'taxonomy_code': 'B2', 'text': 'Certificate, Some College',
         'extracted_fields': {'program_name': 'Certificate', 'institution': 'Some College',
                              'location': 'Boston, MA'}}])

    cell = gen.doc.tables[0].rows[1].cells[1]
    assert cell.text == 'Some College, Boston, MA'
    assert _insertions(cell) == []


def test_b2_institution_none_sentinel_is_blank():
    gen = _b2_doc()
    gen._fill_other_education([
        {'taxonomy_code': 'B2', 'text': 'Certificate',
         'extracted_fields': {'program_name': 'Certificate', 'institution': 'none'}}])

    assert gen.doc.tables[0].rows[1].cells[1].text == ''


# --- patents: row counts, ordering, instruction removal --------------------------

def _patent(text: str, **fields) -> dict:
    return {'taxonomy_code': 'M2D', 'text': text, 'extracted_fields': fields}


def _patents_doc() -> WCMTemplateGenerator:
    gen = _new_generator()
    gen.doc.add_paragraph("Patents & Inventions")
    gen.doc.add_paragraph("MENTORING")
    return gen


def _labels(table) -> list[str]:
    return [row.cells[0].text for row in table.rows]


def test_patents_all_eight_attributes_render_exactly_eight_rows_in_order():
    gen = _patents_doc()
    gen._fill_patents([_patent(
        'full patent', title='Widget for Doing Things', patent_number='US1234567',
        inventors='A. Inventor, B. Inventor', filing_date='2019-03-01',
        issue_date='2021-07-15', status='Issued', assignee='Some University',
        narrative='A device that does the things a widget should do.')])

    table = gen.doc.tables[0]
    assert _labels(table) == [
        'Title of invention:', 'Patent number:', 'Inventors:', 'Status:',
        'Filing date:', 'Issue date:', 'Assignee:', 'Description:']
    assert [row.cells[1].text for row in table.rows] == [
        'Widget for Doing Things', 'US1234567', 'A. Inventor, B. Inventor', 'Issued',
        '03/2019', '07/2021', 'Some University',
        'A device that does the things a widget should do.']


@pytest.mark.parametrize("fields, labels", [
    ({'title': 'Widget'}, ['Title of invention:']),
    ({'patent_number': 'US1', 'inventors': 'A. Inventor'},
     ['Patent number:', 'Inventors:']),
    ({'title': 'Widget', 'narrative': 'too short'}, ['Title of invention:']),
    ({'title': 'Widget', 'narrative': 'abcdefghij'}, ['Title of invention:']),
    ({'title': 'Widget', 'narrative': 'abcdefghijk'},
     ['Title of invention:', 'Description:']),
    ({'title': 'Widget', 'status': 'Pending', 'assignee': 'Some University'},
     ['Title of invention:', 'Status:', 'Assignee:']),
], ids=['title-only', 'number-and-inventors', 'narrative-9-chars-dropped',
        'narrative-10-chars-dropped', 'narrative-11-chars-kept', 'title-status-assignee'])
def test_patents_sparse_records_render_only_the_rows_they_have(fields, labels):
    gen = _patents_doc()
    gen._fill_patents([_patent('sparse patent', **fields)])

    assert len(gen.doc.tables) == 1
    assert _labels(gen.doc.tables[0]) == labels


@pytest.mark.parametrize("fields", [
    {'inventors': 'A. Inventor'},
    {'status': 'Pending', 'filing_date': '2019-03-01'},
    {},
], ids=['inventors-only', 'status-and-filing-only', 'no-fields'])
def test_patents_without_title_or_number_render_no_table(fields):
    gen = _patents_doc()
    gen._fill_patents([_patent('sparse patent', **fields)])

    assert len(gen.doc.tables) == 0
    assert gen.stats['entries_inserted'] == 0


def test_patents_multiple_records_keep_order_and_add_no_trailing_spacing():
    """Three entries, the middle one sparse: the two rendered tables come out
    most recent first with one spacing paragraph between them and none after
    the last -- the spacing is counted against rendered patents, not input."""
    gen = _patents_doc()
    gen._fill_patents([
        _patent('older', title='Older Widget', year='2019'),
        _patent('sparse', inventors='Nobody Named', year='2020'),
        _patent('newer', title='Newer Widget', year='2021'),
    ])

    assert _body_after(gen.doc, "Patents & Inventions", 4) == [
        ('tbl', 'Newer Widget'), ('p', ''), ('tbl', 'Older Widget'), ('p', 'MENTORING')]
    assert gen.stats['tables_populated'] == 2
    assert gen.stats['entries_inserted'] == 2


def test_patents_instruction_paragraph_is_blanked_and_left_below_the_tables():
    gen = _new_generator()
    gen.doc.add_paragraph("Patents & Inventions")
    instruction = gen.doc.add_paragraph(
        "Please include inventors, title of invention and patent number.")
    gen.doc.add_paragraph("MENTORING")

    gen._fill_patents([_patent('one', title='Widget')])

    assert instruction.text == ''
    body = list(gen.doc.element.body)
    assert body.index(instruction._element) == body.index(gen.doc.tables[0]._tbl) + 1
    assert _body_after(gen.doc, "Patents & Inventions", 3) == [
        ('tbl', 'Widget'), ('p', ''), ('p', 'MENTORING')]
