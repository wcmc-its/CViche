"""Regression guard for issue #547 (the silent-failure half) and the render
contracts of the three section writers #739 reworked: a mispositioned patent
table and a skipped postdoc section both used to report success, and the
mentoring/other-education/patents writers had no test pinning where their
content lands.

`patents.py:157-164` (pre-fix line numbers) caught a repositioning failure
with a bare ``except (ValueError, IndexError): pass`` and still incremented
both counters -- so a table stranded at the end of the document, instead of
under its heading, was invisible three times over: nothing logged, the stats
claimed a normal render, and the content is present so a text-only render
gate sees no change either. `postdoc_training.py:309-317` (pre-fix line
numbers) returned silently -- twice -- when the POSTDOCTORAL/TRAINING
heading or its table could not be found in the template, dropping every
postdoc entry with no record of any kind.

Both now emit `logger.warning` naming the section and the entry count,
mirroring `service.py`'s `_fill_journal_reviewing` pattern (:691-698, added
by 993642bf). Every body splice now goes through one helper, `_insert_after`
(stage6/formatting/docx.py), anchored on an element rather than a paragraph
index; a detached anchor raises `DetachedAnchorError`.

Judgement call (disclosed in the PR body): on a reposition failure, patents.py
increments a `tables_misplaced` counter instead of `tables_populated`, so the
latter keeps meaning "landed where it should have"; `entries_inserted` still
increments either way because the content is present in the document. The
cursor is advanced only when the table was placed.

Tests marked "real template" open `key_files/wcm_cv_template_faculty_october_2022_final.docx`
the way `generate()` does and assert on body order around the section
headings, so the synthetic fixtures cannot drift from the shipped structure.
Fixtures are fictional.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_silent_render_failures.py -p no:cacheprovider
"""

import logging
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402
from docx.table import Table  # noqa: E402
from docx.text.paragraph import Paragraph  # noqa: E402

from unified_pipeline.stage6.formatting import DetachedAnchorError, _insert_after  # noqa: E402
from unified_pipeline.stage6.sections.mentoring import (  # noqa: E402
    MenteeRecord,
    N1_HEADING,
    N2_HEADING,
    _program_leadership_line,
    _training_grant_is_sparse,
    _training_grant_rows,
)
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402

MENTORING_LOGGER = 'unified_pipeline.stage6.sections.mentoring'
PATENTS_LOGGER = 'unified_pipeline.stage6.sections.patents'
POSTDOC_LOGGER = 'unified_pipeline.stage6.sections.postdoc_training'


def _new_generator() -> WCMTemplateGenerator:
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document()
    return gen


def _template_generator() -> WCMTemplateGenerator:
    """A generator over the real WCM template, the way `generate()` opens it."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    return gen


def _warnings(caplog, logger_name: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records
            if r.levelno == logging.WARNING and r.name == logger_name]


def _body_after(doc, heading_text: str, count: int) -> list[tuple[str, str]]:
    """The `count` body elements following the paragraph whose stripped text
    equals `heading_text` (case-insensitive), as ('p', text) or
    ('tbl', text of row 0 cell 1) -- a mentee's name in a mentee table, a
    patent's first value in a patent table."""
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


def _mentee(code: str, name: str, **fields) -> dict:
    return {'taxonomy_code': code, 'text': f"{name} mentee entry",
            'extracted_fields': {'mentee_name': name, **fields}}


# --- shared insertion helper ---------------------------------------------------

def test_insert_after_moves_the_element_to_follow_the_anchor():
    """`add_paragraph`/`add_table` append at the end of the body; the helper
    MOVES the new element (one copy, no duplicate left behind)."""
    doc = Document()
    anchor = doc.add_paragraph("anchor")
    doc.add_paragraph("between")
    moved = doc.add_paragraph("moved")

    _insert_after(anchor._p, moved._p)

    assert [p.text for p in doc.paragraphs] == ["anchor", "moved", "between"]


def test_insert_after_rejects_a_detached_anchor():
    """An anchor removed from the body has nowhere for a sibling to go;
    the helper says so by name rather than lxml's root-element TypeError."""
    doc = Document()
    anchor = doc.add_paragraph("anchor")
    anchor._p.getparent().remove(anchor._p)

    with pytest.raises(DetachedAnchorError):
        _insert_after(anchor._p, OxmlElement('w:p'))


# --- mentoring: ordering, anchors, stats ----------------------------------------

def test_mentoring_tables_render_in_order_with_spacing_between():
    """Reverse insertion is the fragile part: three current mentees must come
    out as A -> spacing -> B -> spacing -> C under "Current Mentees:", each
    table carrying its own mentee's name."""
    gen = _new_generator()
    gen.doc.add_paragraph("MENTORING")
    gen.doc.add_paragraph("Current Mentees:")
    gen.doc.add_paragraph("Past Mentees:")
    entries_by_code = {'N3A': [_mentee('N3A', 'Alice A'),
                               _mentee('N3A', 'Bob B'),
                               _mentee('N3A', 'Carol C')]}

    gen._fill_mentoring(entries_by_code)

    assert _body_after(gen.doc, "Current Mentees:", 7) == [
        ('tbl', 'Alice A'), ('p', ''),
        ('tbl', 'Bob B'), ('p', ''),
        ('tbl', 'Carol C'), ('p', ''),
        ('p', 'Past Mentees:'),
    ]


def test_mentoring_replaces_the_template_placeholder_tables():
    """The template ships one placeholder table under each heading; each is
    removed exactly once and only the rendered tables remain."""
    gen = _new_generator()
    gen.doc.add_paragraph("MENTORING")
    gen.doc.add_paragraph("Current Mentees:")
    gen.doc.add_table(rows=6, cols=2).rows[0].cells[0].text = "Name"
    gen.doc.add_paragraph("Past Mentees:")
    gen.doc.add_table(rows=6, cols=2).rows[0].cells[0].text = "Name"

    gen._fill_mentoring({'N3A': [_mentee('N3A', 'Alice A')],
                         'N3B': [_mentee('N3B', 'Bob B', end_date='2014')]})

    assert [t.rows[0].cells[1].text for t in gen.doc.tables] == ['Alice A', 'Bob B']
    assert gen.stats['tables_populated'] == 2


def test_mentoring_missing_every_heading_logs_warning_with_count(caplog):
    gen = _new_generator()
    gen.doc.add_paragraph("Some Unrelated Section")
    entries_by_code = {'N3A': [_mentee('N3A', 'Alice A')],
                       'N4': [{'taxonomy_code': 'N4', 'text': 'An outcome.',
                               'extracted_fields': {}}]}

    with caplog.at_level(logging.WARNING, logger=MENTORING_LOGGER):
        gen._fill_mentoring(entries_by_code)

    assert [w.getMessage() for w in _warnings(caplog, MENTORING_LOGGER)] == [
        "Mentoring: none of 'Current Mentees:', 'Past Mentees:' or MENTORING "
        "found in template; 2 entries not rendered"]
    assert len(gen.doc.tables) == 0


def test_mentoring_missing_one_heading_logs_warning_and_renders_the_other(caplog):
    """Only "Current Mentees:" exists: the past group is reported with its
    count, the current group still renders."""
    gen = _new_generator()
    gen.doc.add_paragraph("Current Mentees:")
    entries_by_code = {'N3A': [_mentee('N3A', 'Alice A')],
                       'N3B': [_mentee('N3B', 'Bob B', end_date='2014'),
                               {'taxonomy_code': 'N3B', 'text': 'Completed: 27',
                                'extracted_fields': {}}]}

    with caplog.at_level(logging.WARNING, logger=MENTORING_LOGGER):
        gen._fill_mentoring(entries_by_code)

    assert [w.getMessage() for w in _warnings(caplog, MENTORING_LOGGER)] == [
        "Mentoring: 'Past Mentees:' heading not found in template; "
        "2 entries not rendered"]
    assert _body_after(gen.doc, "Current Mentees:", 2) == [('tbl', 'Alice A'), ('p', '')]


def test_mentoring_missing_mentoring_header_logs_warning_for_outcomes(caplog):
    gen = _new_generator()
    gen.doc.add_paragraph("Current Mentees:")
    gen.doc.add_paragraph("Past Mentees:")
    entries_by_code = {'N4': [{'taxonomy_code': 'N4', 'text': 'An outcome.',
                               'extracted_fields': {}}]}

    with caplog.at_level(logging.WARNING, logger=MENTORING_LOGGER):
        gen._fill_mentoring(entries_by_code)

    assert [w.getMessage() for w in _warnings(caplog, MENTORING_LOGGER)] == [
        "Mentoring: MENTORING heading not found in template; "
        "1 outcome lines not rendered"]


def test_mentoring_single_heading_fallback_renders_both_groups():
    """A template with only a MENTORING header (neither mentee heading):
    both groups land under it, current above past, nothing dropped."""
    gen = _new_generator()
    gen.doc.add_paragraph("MENTORING")
    entries_by_code = {'N3A': [_mentee('N3A', 'Alice A')],
                       'N3B': [_mentee('N3B', 'Bob B', end_date='2014')]}

    gen._fill_mentoring(entries_by_code)

    assert _body_after(gen.doc, "MENTORING", 4) == [
        ('tbl', 'Alice A'), ('p', ''), ('tbl', 'Bob B'), ('p', '')]


def test_mentoring_stats_count_only_tables_that_were_built():
    """A record with no name renders nothing and moves no counter; a named
    one moves both. The line writer counts entries_inserted on its own."""
    gen = _new_generator()
    anchor = gen.doc.add_paragraph("Current Mentees:")._element

    assert gen._create_mentee_table_with_spacing(MenteeRecord(name=''), anchor) is None
    assert gen.stats['tables_populated'] == 0
    assert gen.stats['entries_inserted'] == 0

    assert gen._create_mentee_table_with_spacing(MenteeRecord(name='Alice A'), anchor) is not None
    assert gen.stats['tables_populated'] == 1
    assert gen.stats['entries_inserted'] == 1

    gen._insert_mentoring_line("Completed: 27", anchor)
    assert gen.stats['tables_populated'] == 1
    assert gen.stats['entries_inserted'] == 2


def test_mentoring_detached_anchor_raises_instead_of_stranding_content():
    """The old `except (ValueError, IndexError): pass` left the paragraph at
    the end of the document and still counted it. A cursor that no longer
    points into the body is now a loud failure, and nothing is counted."""
    gen = _new_generator()
    anchor = gen.doc.add_paragraph("Current Mentees:")._element
    anchor.getparent().remove(anchor)

    with pytest.raises(DetachedAnchorError):
        gen._insert_mentoring_line("Completed: 27", anchor)
    with pytest.raises(DetachedAnchorError):
        gen._create_mentee_table_with_spacing(MenteeRecord(name='Alice A'), anchor)
    assert gen.stats['entries_inserted'] == 0
    assert gen.stats['tables_populated'] == 0


def test_mentoring_real_template_body_order():
    """Real template: summary line, then the mentee tables with spacing,
    directly under each heading and above the template's own "Duplicate
    table below as needed" instruction; the two placeholder tables are gone;
    N4 sits under MENTORING."""
    gen = _template_generator()
    template_tables = len(gen.doc.tables)
    entries_by_code = {
        'N3A': [_mentee('N3A', 'Alice A', start_date='2022'),
                _mentee('N3A', 'Bob B', start_date='2023'),
                {'taxonomy_code': 'N3A', 'text': 'Current Ph.D. Students: 2',
                 'extracted_fields': {}}],
        'N3B': [_mentee('N3B', 'Carol C', start_date='2015', end_date='2019')],
        'N4': [{'taxonomy_code': 'N4', 'text': 'One mentee now leads a lab.',
                'extracted_fields': {}}],
    }

    gen._fill_mentoring(entries_by_code)

    current = _body_after(gen.doc, "Current Mentees:", 6)
    assert current[:5] == [('p', 'Current Ph.D. Students: 2'),
                           ('tbl', 'Alice A'), ('p', ''),
                           ('tbl', 'Bob B'), ('p', '')]
    assert current[5][0] == 'p' and current[5][1].startswith('Duplicate table below')
    past = _body_after(gen.doc, "Past Mentees:", 3)
    assert past[:2] == [('tbl', 'Carol C'), ('p', '')]
    assert past[2][1].startswith('Duplicate table below')
    assert _body_after(gen.doc, "MENTORING", 1) == [('p', 'One mentee now leads a lab.')]
    # Two placeholders out, three mentee tables in.
    assert len(gen.doc.tables) == template_tables - 2 + 3
    assert gen.stats['tables_populated'] == 3
    assert gen.stats['entries_inserted'] == 5


# --- N1/N2: pure builders (#529) -------------------------------------------------

def test_program_leadership_line_full_fields_joined_with_dates():
    fields = {'role': 'Director', 'program_name': 'Scholars Program',
              'institution': 'Test University', 'start_date': '2019',
              'end_date': '2022'}
    assert _program_leadership_line(fields, 'raw text') == \
        'Director, Scholars Program, Test University (2019-2022)'


def test_program_leadership_line_degrades_role_only():
    assert _program_leadership_line({'role': 'Director'}, 'raw text') == 'Director'


def test_program_leadership_line_degrades_program_and_institution_only():
    fields = {'program_name': 'Scholars Program', 'institution': 'Test University'}
    assert _program_leadership_line(fields, 'raw text') == \
        'Scholars Program, Test University'


def test_program_leadership_line_dates_only_falls_back_to_text():
    """None of role/program_name/institution present -> the entry's own
    `text`, even with both dates present (#529 round 2, F6): a bare
    "(2019-2022)" carries less than the Appendix line it replaces."""
    fields = {'start_date': '2019', 'end_date': '2022'}
    assert _program_leadership_line(fields, 'Directed a mentoring initiative.') == \
        'Directed a mentoring initiative.'


def test_program_leadership_line_all_five_keys_empty_falls_back_to_text():
    assert _program_leadership_line({}, '  Directed a mentoring initiative.  ') == \
        'Directed a mentoring initiative.'


def test_program_leadership_line_none_values_are_safe():
    fields = {'role': None, 'program_name': None, 'institution': None,
              'start_date': None, 'end_date': None}
    assert _program_leadership_line(fields, 'raw text') == 'raw text'
    assert _program_leadership_line(fields, None) == ''


def test_training_grant_rows_full_fields():
    fields = {'agency': 'National Test Institute', 'grant_number': 'T32-999',
              'role': 'Mentor', 'grant_title': 'Test Training Program',
              'start_date': '2018', 'end_date': '2021'}
    assert _training_grant_rows(fields) == [
        ('Award Source (funding agency, type of grant):',
         'National Test Institute (T32-999) (Mentor)'),
        ('Project title:', 'Test Training Program'),
        ('Duration of support (mm/yyyy-mm/yyyy):', '2018-2021'),
    ]


def test_training_grant_rows_grant_number_already_in_agency_is_not_repeated():
    fields = {'agency': 'National Test Institute T32-999', 'grant_number': 'T32-999'}
    assert _training_grant_rows(fields)[0] == (
        'Award Source (funding agency, type of grant):',
        'National Test Institute T32-999')


def test_training_grant_rows_title_precedence_grant_title_then_title():
    assert _training_grant_rows({'grant_title': 'A', 'title': 'B'})[1] == \
        ('Project title:', 'A')
    assert _training_grant_rows({'title': 'B'})[1] == ('Project title:', 'B')


def test_training_grant_rows_none_values_are_safe():
    fields = {'agency': None, 'grant_number': None, 'role': None,
              'grant_title': None, 'title': None, 'start_date': None, 'end_date': None}
    assert _training_grant_rows(fields) == [
        ('Award Source (funding agency, type of grant):', ''),
        ('Project title:', ''),
        ('Duration of support (mm/yyyy-mm/yyyy):', ''),
    ]


def test_training_grant_is_sparse_true_when_no_identifying_field():
    assert _training_grant_is_sparse({}) is True
    assert _training_grant_is_sparse({'role': 'Mentor'}) is True


def test_training_grant_is_sparse_false_when_any_identifying_field_present():
    assert _training_grant_is_sparse({'grant_number': 'T32-1'}) is False
    assert _training_grant_is_sparse({'agency': 'A'}) is False
    assert _training_grant_is_sparse({'grant_title': 'A'}) is False


# --- N1/N2: rendering into their template slots (#529) ---------------------------

def _n1(**fields) -> dict:
    text = fields.pop('text', 'N1 entry')
    return {'taxonomy_code': 'N1', 'text': text, 'extracted_fields': fields}


def _n2(**fields) -> dict:
    text = fields.pop('text', 'N2 entry')
    return {'taxonomy_code': 'N2', 'text': text, 'extracted_fields': fields}


def test_n1_missing_anchor_falls_back_to_mentoring_header(caplog):
    """Blank Document() with only MENTORING: warning emitted, the entry
    still renders (as a fallback line), nothing raised (#529)."""
    gen = _new_generator()
    gen.doc.add_paragraph("MENTORING")

    with caplog.at_level(logging.WARNING, logger=MENTORING_LOGGER):
        gen._fill_mentoring({'N1': [_n1(role='Director')]})

    assert [w.getMessage() for w in _warnings(caplog, MENTORING_LOGGER)] == [
        "Mentoring: 'Leadership and mentoring in programs (Describe "
        "activity; include dates)' heading not found in template; "
        "1 entries not rendered"]
    assert _body_after(gen.doc, "MENTORING", 1) == [('p', 'Director')]


def test_n2_missing_anchor_falls_back_to_mentoring_header(caplog):
    """Same fallback shape as N1, for the table-shaped code (#529)."""
    gen = _new_generator()
    gen.doc.add_paragraph("MENTORING")

    with caplog.at_level(logging.WARNING, logger=MENTORING_LOGGER):
        gen._fill_mentoring({'N2': [_n2(agency='National Test Institute')]})

    assert [w.getMessage() for w in _warnings(caplog, MENTORING_LOGGER)] == [
        "Mentoring: 'Institutional Training Grants and Mentored Trainee "
        "Grants' heading not found in template; 1 entries not rendered"]
    assert len(gen.doc.tables) == 1
    assert gen.doc.tables[0].rows[0].cells[1].text == 'National Test Institute'


def test_n1_real_template_three_lines_in_order_with_partial_fields():
    """Real template: three N1 entries land after "Leadership and
    mentoring in programs..." in input order, each degrading to whatever
    fields it has (#529)."""
    gen = _template_generator()
    entries_by_code = {
        'N1': [
            _n1(role='Director'),
            _n1(program_name='Scholars Program', institution='Test University'),
            _n1(start_date='2019', end_date='2022'),
        ],
    }

    gen._fill_mentoring(entries_by_code)

    assert _body_after(gen.doc, N1_HEADING, 3) == [
        ('p', 'Director'),
        ('p', 'Scholars Program, Test University'),
        ('p', 'Directed a mentoring program before 2019 records began.'),
    ]
    assert gen.stats['entries_inserted'] == 3


def test_n2_real_template_tables_in_order_placeholder_removed_sparse_as_line():
    """Real template: two N2 entries -> placeholder table gone, two 3-row
    tables land AFTER the instruction paragraph ("Duplicate table below
    as needed...") in input order with a spacer between -- heading ->
    instruction -> tables, the template's own order (#529 round 2, F1;
    was between the heading and the instruction before this fix). A
    third, sparse entry renders as one plain line and adds no table."""
    gen = _template_generator()
    template_tables = len(gen.doc.tables)
    entries_by_code = {
        'N2': [
            _n2(grant_title='Test Training Program A', agency='National Test Institute',
                grant_number='T32-100', role='Mentor', start_date='2018', end_date='2021'),
            _n2(title='Test Training Program B', agency='Regional Test Foundation',
                start_date='2020', end_date='2023'),
            _n2(text='A sparse training-grant line with no identifying field.'),
        ],
    }

    gen._fill_mentoring(entries_by_code)

    n2_region = _body_after(gen.doc, N2_HEADING, 6)
    assert n2_region[0] == ('tbl', 'National Test Institute (T32-100) (Mentor)')
    assert n2_region[1] == ('p', '')
    assert n2_region[2] == ('tbl', 'Regional Test Foundation')
    assert n2_region[3] == ('p', '')
    assert n2_region[4] == (
        'p', 'A sparse training-grant line with no identifying field.')
    assert n2_region[5][0] == 'p' and n2_region[5][1].startswith('Duplicate table below')
    body = list(gen.doc.element.body)
    n2_idx = next(i for i, el in enumerate(body) if el.tag == qn('w:p')
                  and Paragraph(el, gen.doc).text.strip() == N2_HEADING)
    table_a = Table(body[n2_idx + 1], gen.doc)
    assert [row.cells[1].text for row in table_a.rows] == [
        'National Test Institute (T32-100) (Mentor)', 'Test Training Program A', '2018-2021']
    # One placeholder out, two grant tables in; the sparse entry built none.
    assert len(gen.doc.tables) == template_tables - 1 + 2
    assert gen.stats['tables_populated'] == 2
    assert gen.stats['entries_inserted'] == 3


def test_n1_n2_only_entries_still_render_when_no_n3_n4_content():
    """N1/N2 must not depend on the N3/N4 partition being non-empty -- a CV
    with ONLY N1/N2 content must not hit `_fill_mentoring`'s early return
    for an empty mentee/outcome partition (#529)."""
    gen = _template_generator()
    gen._fill_mentoring({'N1': [_n1(role='Director')],
                         'N2': [_n2(agency='National Test Institute')]})

    assert _body_after(gen.doc, N1_HEADING, 1) == [('p', 'Director')]
    assert _body_after(gen.doc, N2_HEADING, 1) == [('tbl', 'National Test Institute')]


def test_mentoring_no_n1_n2_or_n3_n4_entries_leaves_the_region_byte_identical():
    """No-op contract (#529): with nothing to render at all, the MENTORING
    -> Mentees body region is untouched at the XML level, not just visually."""
    from lxml import etree

    def region(doc):
        body = list(doc.element.body)
        start = next(i for i, el in enumerate(body) if el.tag == qn('w:p')
                     and Paragraph(el, doc).text.strip() == 'MENTORING')
        end = next(i for i, el in enumerate(body) if el.tag == qn('w:p')
                   and Paragraph(el, doc).text.strip() == 'Mentees')
        return [etree.tostring(el) for el in body[start:end]]

    gen = _template_generator()
    before = region(gen.doc)

    gen._fill_mentoring({})

    assert region(gen.doc) == before


# --- other education: template structure, three-column rendering -----------------

OTHER_EDUCATION_LOGGER = 'unified_pipeline.stage6.sections.other_education'


def _b2(program: str, **fields) -> dict:
    return {'taxonomy_code': 'B2', 'text': f"{program} entry",
            'extracted_fields': {'program_name': program, **fields}}


def test_other_education_missing_heading_logs_warning_with_count(caplog):
    gen = _new_generator()
    gen.doc.add_paragraph("Some Unrelated Section")

    with caplog.at_level(logging.WARNING, logger=OTHER_EDUCATION_LOGGER):
        gen._fill_other_education([_b2('Certificate A'), _b2('Certificate B')])

    assert [w.getMessage() for w in _warnings(caplog, OTHER_EDUCATION_LOGGER)] == [
        "Other Educational Experiences: section heading not found in template; "
        "2 entries not rendered"]
    assert gen.stats['tables_populated'] == 0
    assert gen.stats['entries_inserted'] == 0


def test_other_education_missing_table_logs_warning_with_count(caplog):
    """The heading exists but no table follows it: template structure,
    not empty data, and reported as such."""
    gen = _new_generator()
    gen.doc.add_paragraph("OTHER EDUCATIONAL")
    gen.doc.add_paragraph("No table follows this heading.")

    with caplog.at_level(logging.WARNING, logger=OTHER_EDUCATION_LOGGER):
        gen._fill_other_education([_b2('Certificate A')])

    assert [w.getMessage() for w in _warnings(caplog, OTHER_EDUCATION_LOGGER)] == [
        "Other Educational Experiences: table not found after section heading; "
        "1 entries not rendered"]
    assert gen.stats['tables_populated'] == 0


@pytest.mark.parametrize("columns", [2, 4])
def test_other_education_wrong_column_count_logs_warning_and_writes_nothing(caplog, columns):
    """The writer always builds program | institution | dates; a grid of any
    other width would land content in the wrong column, so it is refused."""
    gen = _new_generator()
    gen.doc.add_paragraph("OTHER EDUCATIONAL")
    table = gen.doc.add_table(rows=1, cols=columns)

    with caplog.at_level(logging.WARNING, logger=OTHER_EDUCATION_LOGGER):
        gen._fill_other_education([_b2('Certificate A')])

    assert [w.getMessage() for w in _warnings(caplog, OTHER_EDUCATION_LOGGER)] == [
        f"Other Educational Experiences: expected a 3-column table, found "
        f"{columns} columns; 1 entries not rendered"]
    assert len(table.rows) == 1
    assert gen.stats['tables_populated'] == 0
    assert gen.stats['entries_inserted'] == 0


def test_other_education_real_template_three_column_rows():
    """Real template: the B2 table has three columns; the header row survives,
    the placeholder row is cleared, entries land most-recent-first with
    program | institution, location | dates, and a sparse entry (no program,
    no institution) is skipped and not counted."""
    gen = _template_generator()
    entries = [
        _b2('Certificate in Epidemiology', institution='Some College',
            location='Boston, MA', start_date='2017-08', end_date='2018-05'),
        _b2('Workshop on Grant Writing', institution='Another Institute', year='2021'),
        {'taxonomy_code': 'B2', 'text': 'stray line', 'extracted_fields': {'year': '2020'}},
    ]

    gen._fill_other_education(entries)

    table = gen._find_table_after_paragraph(
        gen._find_paragraph_with_text("OTHER EDUCATIONAL"))
    assert len(table.columns) == 3
    assert [c.text for c in table.rows[0].cells] == [
        'Description', 'Institution, city and state',
        'Dates attended (mm/yy – mm/yy)']
    assert [[c.text for c in row.cells] for row in table.rows[1:]] == [
        ['Workshop on Grant Writing', 'Another Institute', '2021'],
        ['Certificate in Epidemiology', 'Some College, Boston, MA', '08/17-05/18'],
    ]
    assert gen.stats['tables_populated'] == 1
    assert gen.stats['entries_inserted'] == 2


# --- patents: reposition failure ------------------------------------------------

def _patent(text: str, **fields) -> dict:
    return {'taxonomy_code': 'M2D', 'text': text, 'extracted_fields': fields}


def test_patents_detached_cursor_logs_warning_counts_misplaced_and_holds_cursor(caplog):
    """The real failure mode: `_add_spacing_paragraph` (shared, on the
    generator) removes its paragraph from the body and re-inserts it with
    `body.insert`; when that insert fails it swallows the error and returns
    the now-DETACHED element, which the section had been adopting as its
    cursor. Forcing the FIRST `body.insert` call to raise (and only that
    one) reproduces exactly that -- and, unlike an always-raising mock,
    lets a second table's own insert succeed if the code ever hands it a
    chance to. It must not get that chance: once the cursor is detached,
    `_place_patent_table` must keep failing (and keep reporting) for every
    later table instead of quietly recovering on the next placement it
    happens to try.

    The first table is placed (before any spacing exists). The one forced
    `body.insert` failure detaches the spacing paragraph after it, so every
    later table finds a detached cursor: each is logged with its ordinal
    and the caught exception, counted in tables_misplaced, and left at the
    document end -- immediately after the previous one, with no spacing
    paragraph between them, because the cursor never became attached again.

    This pins the regression a cursor-advances-on-failure bug would cause:
    if the guard were removed, the second table's own spacing attempt would
    reach the now-unmocked `body.insert`, succeed, and the third table
    would land correctly spaced after the second -- one warning and
    `tables_populated == 2` instead of two warnings and `tables_populated
    == 1`, with a spacing paragraph between Widget B and Widget C that must
    not exist here."""
    gen = _new_generator()
    gen.doc.add_paragraph("Patents & Inventions")

    real_insert = gen.doc.element.body.insert
    calls = {'n': 0}

    def _raise_once(*args, **kwargs):
        calls['n'] += 1
        if calls['n'] == 1:
            raise ValueError("forced for test")
        return real_insert(*args, **kwargs)

    gen.doc.element.body.insert = _raise_once

    entries = [_patent('newest', title='Widget A', year='2021'),
               _patent('middle', title='Widget B', year='2020'),
               _patent('oldest', title='Widget C', year='2019')]

    with caplog.at_level(logging.WARNING, logger=PATENTS_LOGGER):
        gen._fill_patents(entries)  # must not raise

    warnings = _warnings(caplog, PATENTS_LOGGER)
    assert [w.getMessage() for w in warnings] == [
        "Patents & Inventions: table reposition failed for entry 2 of 3; "
        "table left at document end instead of under the section heading",
        "Patents & Inventions: table reposition failed for entry 3 of 3; "
        "table left at document end instead of under the section heading",
    ]
    # exc_info=True on the warning call -- the caught DetachedAnchorError
    # must reach the log, not just its message text.
    assert all(w.exc_info is not None and isinstance(w.exc_info[1], DetachedAnchorError)
               for w in warnings)

    # Content not lost: all three tables exist, the first under the heading.
    assert [t.rows[0].cells[1].text for t in gen.doc.tables] == [
        'Widget A', 'Widget B', 'Widget C']
    assert _body_after(gen.doc, "Patents & Inventions", 1) == [('tbl', 'Widget A')]
    # Widget C lands directly after Widget B with no spacing between them --
    # the cursor never recovered, so no spacing insert was ever attempted
    # for either. A guard that advanced the cursor on failure would let the
    # forced insert fail once and then heal, spacing C correctly after B.
    assert _body_after(gen.doc, "Patents & Inventions", 3) == [
        ('tbl', 'Widget A'), ('tbl', 'Widget B'), ('tbl', 'Widget C')]
    # Failure stats: placed vs misplaced are distinct; entries_inserted counts both.
    assert gen.stats['tables_populated'] == 1
    assert gen.stats['tables_misplaced'] == 2
    assert gen.stats['entries_inserted'] == 3


def test_patents_success_stats_have_no_misplaced_count():
    gen = _new_generator()
    gen.doc.add_paragraph("Patents & Inventions")

    gen._fill_patents([_patent('a', title='Widget A', year='2021'),
                       _patent('b', title='Widget B', year='2020')])

    assert gen.stats['tables_populated'] == 2
    assert gen.stats['entries_inserted'] == 2
    assert gen.stats.get('tables_misplaced', 0) == 0


def test_patents_missing_heading_logs_warning_with_count(caplog):
    gen = _new_generator()
    gen.doc.add_paragraph("Some Unrelated Section")

    with caplog.at_level(logging.WARNING, logger=PATENTS_LOGGER):
        gen._fill_patents([_patent('a', title='Widget A'), _patent('b', title='Widget B')])

    assert [w.getMessage() for w in _warnings(caplog, PATENTS_LOGGER)] == [
        "Patents & Inventions: section heading not found in template; "
        "2 entries not rendered"]
    assert len(gen.doc.tables) == 0


def test_patents_render_twice_replaces_the_first_render():
    """Rendering the section again into the same document used to append a
    second set of tables; the previously rendered tables (and the spacing
    between them) are now cleared first, so the body shape is identical."""
    gen = _new_generator()
    gen.doc.add_paragraph("Patents & Inventions")
    gen.doc.add_paragraph("Please include inventors, title of invention and patent number.")
    gen.doc.add_paragraph("MENTORING")
    entries = [_patent('a', title='Widget A', year='2021'),
               _patent('b', title='Widget B', year='2020')]

    gen._fill_patents(entries)
    first = _body_after(gen.doc, "Patents & Inventions", 6)
    gen._fill_patents(entries)

    assert first == [('tbl', 'Widget A'), ('p', ''), ('tbl', 'Widget B'),
                     ('p', ''), ('p', 'MENTORING')]
    assert _body_after(gen.doc, "Patents & Inventions", 6) == first
    assert len(gen.doc.tables) == 2


def test_patents_real_template_body_order():
    """Real template: the tables sit directly under the heading, most recent
    first, one spacing paragraph between them, the blanked instruction line
    and the template's two blank paragraphs below, then MENTORING."""
    gen = _template_generator()
    template_tables = len(gen.doc.tables)

    gen._fill_patents([_patent('older', title='Older Widget', patent_number='US1',
                               issue_date='2019-05-01', year='2019'),
                       _patent('newer', title='Newer Widget', patent_number='US2',
                               issue_date='2021-11-01', year='2021')])

    assert _body_after(gen.doc, "Patents & Inventions", 7) == [
        ('tbl', 'Newer Widget'), ('p', ''), ('tbl', 'Older Widget'),
        ('p', ''), ('p', ''), ('p', ''), ('p', 'MENTORING')]
    assert len(gen.doc.tables) == template_tables + 2
    assert gen.stats['tables_populated'] == 2
    assert gen.stats.get('tables_misplaced', 0) == 0


# --- postdoc training: missing template structure --------------------------------

def test_postdoc_missing_heading_logs_warning(caplog):
    """A template with neither "POSTDOCTORAL" nor "TRAINING" anywhere used to
    return silently, dropping every entry with no diagnostic (postdoc_training.py,
    first bare return)."""
    gen = _new_generator()
    gen.doc.add_paragraph("Some Unrelated Section")

    entries_by_code = {
        'C1': [{'taxonomy_code': 'C1', 'text': 'A postdoc research entry',
                'extracted_fields': {'institution': 'Some Hospital'}}],
    }

    with caplog.at_level(logging.WARNING, logger=POSTDOC_LOGGER):
        gen._fill_postdoc_training(entries_by_code)  # must not raise

    warnings = _warnings(caplog, POSTDOC_LOGGER)
    assert len(warnings) == 1, "expected exactly one warning, got: %r" % (
        [r.message for r in warnings],)
    message = warnings[0].getMessage()
    assert 'Postdoctoral Training' in message
    assert '1 entries not rendered' in message
    assert len(gen.doc.tables) == 0


def test_postdoc_missing_table_logs_warning(caplog):
    """A template whose POSTDOCTORAL heading exists but has no table after
    it used to return silently (postdoc_training.py, second bare return)."""
    gen = _new_generator()
    gen.doc.add_paragraph("POSTDOCTORAL TRAINING")
    gen.doc.add_paragraph("No table follows this heading.")

    entries_by_code = {
        'C1': [{'taxonomy_code': 'C1', 'text': 'A postdoc research entry',
                'extracted_fields': {'institution': 'Some Hospital'}}],
    }

    with caplog.at_level(logging.WARNING, logger=POSTDOC_LOGGER):
        gen._fill_postdoc_training(entries_by_code)  # must not raise

    warnings = _warnings(caplog, POSTDOC_LOGGER)
    assert len(warnings) == 1, "expected exactly one warning, got: %r" % (
        [r.message for r in warnings],)
    message = warnings[0].getMessage()
    assert 'Postdoctoral Training' in message
    assert '1 entries not rendered' in message
