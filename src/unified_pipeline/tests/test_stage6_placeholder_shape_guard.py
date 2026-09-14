"""#836: shape-guard both "remove the template placeholder table" call sites.

`research_support.py`'s per-bucket removal and `mentoring.py`'s
`_remove_template_table_after` each called an unbounded forward scan
(`_find_table_after_paragraph` / `_first_table_after`, both in
`stage_6_word_template.py`) and deleted whatever `w:tbl` it returned, with no
check that the table was its own. In the WCM template only `Current Research
Funding` has a placeholder of its own -- `Past (Completed) Funding` and
`Pending Funding` do not -- so those two steps always reached into a LATER
section: N2's "Institutional Training Grants and Mentored Trainee Grants"
placeholder, then one of the two mentee placeholder tables, deleted on every
render regardless of entry count.

The fix, mirrored from `leadership.py`'s `_looks_like_leadership_table` (#664
item 2): validate the table's own shape -- its row-0, cell-0 text -- before
deleting it. `_looks_like_funding_placeholder` (research_support.py) checks
for the "Award Source:" prefix M2's placeholder and every freshly built grant
table both carry, but NOT N2's own placeholder, whose row-0 label reads
"Award Source (funding agency, type of grant):" -- no colon immediately
after "Source". `_looks_like_mentee_placeholder` (mentoring.py) checks for an
EXACT "name" match, not a substring: other tables' row-0 cell-0 contain
"name" without being mentee placeholders ("Name of Committee", "Name of
award").

Three levels, same split `test_stage6_leadership_round2.py`'s
`TestTableShapeValidation` uses:

* pure guard functions, called directly with hand-built oxml/python-docx
  tables (positive, negative, empty-table, no-cells);
* synthetic `Document()` fixtures modelled on that same class -- a
  wrong-shaped table right after the real heading is untouched, the real
  placeholder is still removed;
* the real WCM template, opened the way `generate()` does, so the fixtures
  above cannot drift from the shipped table shapes.

Every person/entry named below is invented.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_placeholder_shape_guard.py -p no:cacheprovider
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402
from docx.oxml import OxmlElement  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402

from unified_pipeline.stage6.sections.mentoring import (  # noqa: E402
    _looks_like_mentee_placeholder,
)
from unified_pipeline.stage6.sections.research_support import (  # noqa: E402
    _looks_like_funding_placeholder,
)
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402

CURRENT = 'Current Research Funding'
COMPLETED = 'Past (Completed) Funding'
PENDING = 'Pending Funding'

# N2's real template label (lead-verified, `key_files/...` paragraph order):
# no colon right after "Source", so a `startswith('award source:')` test
# must not match it.
N2_LABEL = 'Award Source (funding agency, type of grant):'


def _table_element(rows: list[list[str]]):
    """A bare `w:tbl` oxml element with the given row/cell text, via a
    throwaway `Document()` (mirrors `_first_table_after`'s return shape --
    an oxml element, not a `docx.table.Table`)."""
    doc = Document()
    n_cols = max((len(r) for r in rows), default=1)
    table = doc.add_table(rows=len(rows), cols=n_cols)
    for r, row_values in enumerate(rows):
        for c, value in enumerate(row_values):
            table.rows[r].cells[c].text = value
    return table._tbl


def _empty_table_element():
    """A `w:tbl` with no `w:tr` at all -- python-docx can't build this
    (`add_table(rows=0, ...)` still needs a row), so it's built by hand."""
    tbl = OxmlElement('w:tbl')
    tbl.append(OxmlElement('w:tblPr'))
    tbl.append(OxmlElement('w:tblGrid'))
    return tbl


def _row_with_no_cells_table_element():
    """A `w:tbl` with one `w:tr` that has no `w:tc` children."""
    tbl = OxmlElement('w:tbl')
    tbl.append(OxmlElement('w:tblPr'))
    tbl.append(OxmlElement('w:tblGrid'))
    tbl.append(OxmlElement('w:tr'))
    return tbl


def _real_template_generator() -> WCMTemplateGenerator:
    """A generator over the real WCM template, the way `generate()` opens
    it -- so these tests cannot drift from the shipped table shapes."""
    gen = WCMTemplateGenerator(verbose=False)
    gen.doc = Document(gen.template_path)
    return gen


def _table_labels(gen: WCMTemplateGenerator) -> list[str]:
    return [t.rows[0].cells[0].text.strip() for t in gen.doc.tables]


def _m2a_entry(title: str, agency: str) -> dict:
    return {'text': title, 'taxonomy_code': 'M2A',
            'extracted_fields': {'title': title, 'agency': agency,
                                  'start_date': '01/2020', 'end_date': 'Present'}}


def _mentee(name: str) -> dict:
    return {'taxonomy_code': 'N3A', 'text': f'{name} mentee entry',
            'extracted_fields': {'mentee_name': name}}


# --- pure guard: _looks_like_funding_placeholder ---------------------------------

def _as_table(tbl_element):
    """Wrap a bare `w:tbl` element back into a `docx.table.Table` -- the
    guard's real call site (`existing_table` in `research_support.py`) is
    already a `Table`, not a bare element, unlike mentoring's site."""
    from docx.table import Table
    return Table(tbl_element, Document())


def test_funding_guard_accepts_the_templates_own_placeholder_label():
    table = _as_table(_table_element([[
        'Award Source: (funding agency – federal, foundation, industry; type of grant)*',
        '',
    ]]))
    assert _looks_like_funding_placeholder(table) is True


def test_funding_guard_accepts_a_freshly_built_grant_tables_own_label():
    table = _as_table(_table_element([['Award Source:', 'NIH']]))
    assert _looks_like_funding_placeholder(table) is True


def test_funding_guard_rejects_n2s_placeholder_label():
    # The exact defect: N2's label contains "Award Source" but not the
    # colon-terminated "Award Source:" prefix M2's own placeholder has.
    table = _as_table(_table_element([[N2_LABEL, '']]))
    assert _looks_like_funding_placeholder(table) is False


def test_funding_guard_rejects_a_mentee_placeholder_label():
    table = _as_table(_table_element([['Name', '']]))
    assert _looks_like_funding_placeholder(table) is False


def test_funding_guard_rejects_an_empty_table():
    table = _as_table(_empty_table_element())
    assert _looks_like_funding_placeholder(table) is False


def test_funding_guard_rejects_a_row_with_no_cells():
    table = _as_table(_row_with_no_cells_table_element())
    assert _looks_like_funding_placeholder(table) is False


# --- pure guard: _looks_like_mentee_placeholder -----------------------------------

def test_mentee_guard_accepts_the_templates_own_name_label():
    table = _table_element([['Name', '']])
    assert _looks_like_mentee_placeholder(table) is True


def test_mentee_guard_is_case_and_whitespace_insensitive():
    table = _table_element([['  NAME  ', '']])
    assert _looks_like_mentee_placeholder(table) is True


def test_mentee_guard_rejects_name_of_committee():
    # A real template table (Section P) whose row-0 cell-0 CONTAINS "name"
    # without being a mentee placeholder -- the substring test this guard
    # replaces would have matched it.
    table = _table_element([['Name of Committee', '', '']])
    assert _looks_like_mentee_placeholder(table) is False


def test_mentee_guard_rejects_name_of_award():
    table = _table_element([['Name of award', '', '']])
    assert _looks_like_mentee_placeholder(table) is False


def test_mentee_guard_rejects_the_funding_placeholder_label():
    table = _table_element([['Award Source:', '']])
    assert _looks_like_mentee_placeholder(table) is False


def test_mentee_guard_rejects_an_empty_table():
    assert _looks_like_mentee_placeholder(_empty_table_element()) is False


def test_mentee_guard_rejects_a_row_with_no_cells():
    assert _looks_like_mentee_placeholder(_row_with_no_cells_table_element()) is False


# --- synthetic Document() fixtures, modelled on TestTableShapeValidation ---------

def test_wrong_shaped_table_after_current_funding_is_untouched_and_the_grant_still_renders():
    gen = WCMTemplateGenerator(verbose=False)
    doc = Document()
    doc.add_paragraph(CURRENT)
    sentinel_table = doc.add_table(rows=1, cols=3)
    sentinel_table.rows[0].cells[0].text = 'SENTINEL-DO-NOT-TOUCH'
    sentinel_table.rows[0].cells[1].text = 'Role'
    sentinel_table.rows[0].cells[2].text = 'Dates'
    doc.add_paragraph(COMPLETED)
    doc.add_paragraph(PENDING)
    gen.doc = doc

    gen._fill_research_support(
        {'M2A': [_m2a_entry('Sentinel-Adjacent Project', 'NIH')], 'M2B': [], 'M2C': []},
        current_year=2026)

    rows = [tuple(c.text for c in r.cells) for r in sentinel_table.rows]
    assert rows == [('SENTINEL-DO-NOT-TOUCH', 'Role', 'Dates')]
    # The grant table still rendered, right after the untouched sentinel.
    labels_after = [t.rows[0].cells[0].text for t in gen.doc.tables]
    assert 'Award Source:' in labels_after


def test_n2s_exact_label_after_past_funding_is_not_removed():
    gen = WCMTemplateGenerator(verbose=False)
    doc = Document()
    doc.add_paragraph(CURRENT)
    doc.add_paragraph(COMPLETED)
    n2_table = doc.add_table(rows=1, cols=2)
    n2_table.rows[0].cells[0].text = N2_LABEL
    doc.add_paragraph(PENDING)
    gen.doc = doc

    gen._fill_research_support({'M2A': [], 'M2B': [], 'M2C': []}, current_year=2026)

    assert n2_table._tbl in [t._tbl for t in gen.doc.tables]
    assert n2_table.rows[0].cells[0].text == N2_LABEL


def test_mentoring_name_of_committee_after_current_mentees_is_untouched_while_name_is_removed():
    gen = WCMTemplateGenerator(verbose=False)
    doc = Document()
    doc.add_paragraph('MENTORING')
    doc.add_paragraph('Current Mentees:')
    foreign_table = doc.add_table(rows=1, cols=3)
    foreign_table.rows[0].cells[0].text = 'Name of Committee'
    name_table = doc.add_table(rows=6, cols=2)
    name_table.rows[0].cells[0].text = 'Name'
    doc.add_paragraph('Past Mentees:')
    gen.doc = doc

    gen._fill_mentoring({'N3A': [_mentee('Ada Testowner')]})

    # `_first_table_after` returns the FIRST table below the anchor -- the
    # foreign one -- so with the guard in place nothing under Current
    # Mentees: gets removed at all; both placeholder tables survive and the
    # real mentee table is inserted directly under the heading, ahead of them.
    remaining_labels = [t.rows[0].cells[0].text for t in gen.doc.tables]
    assert remaining_labels.count('Name of Committee') == 1
    assert remaining_labels.count('Name') == 1
    assert any(c.text == 'Ada Testowner' for t in gen.doc.tables for c in t.rows[0].cells)


def test_guard_is_safe_on_an_empty_table_in_the_render_flow():
    # (d) an empty (no-row) table right after a heading: the guard must
    # return False rather than raising, so the removal step simply finds
    # nothing of its own shape and moves on.
    gen = WCMTemplateGenerator(verbose=False)
    doc = Document()
    doc.add_paragraph(CURRENT)
    doc.add_paragraph(COMPLETED)
    doc.add_paragraph(PENDING)
    gen.doc = doc

    # Splice a rowless table element directly into the body (python-docx's
    # own `add_table` always creates at least one row).
    current_idx = next(i for i, p in enumerate(gen.doc.paragraphs)
                        if p.text.strip() == CURRENT)
    anchor = gen.doc.paragraphs[current_idx]._element
    anchor.addnext(_empty_table_element())

    gen._fill_research_support(
        {'M2A': [_m2a_entry('Post-Empty-Table Project', 'NIH')], 'M2B': [], 'M2C': []},
        current_year=2026)

    # No exception, and the grant still rendered.
    assert any(t.rows and t.rows[0].cells[0].text == 'Award Source:' for t in gen.doc.tables)


# --- real WCM template integration ------------------------------------------------

def test_real_template_no_entries_removes_only_m2_own_placeholder():
    gen = _real_template_generator()
    before = _table_labels(gen)

    gen._fill_research_support({}, None, 'synthetic-uid', current_year=2026)

    after = _table_labels(gen)
    assert len(after) == len(before) - 1
    assert N2_LABEL in after
    assert after.count('Name') == 2
    assert not any(label.startswith('Award Source:') for label in after)


def test_real_template_one_entry_leaves_n2_and_mentee_placeholders_in_place():
    gen = _real_template_generator()

    gen._fill_research_support(
        {'M2A': [_m2a_entry('Synthetic Study of Widgets', 'NIH')]},
        None, 'synthetic-uid', current_year=2026)

    after = _table_labels(gen)
    assert 'Award Source:' in after
    assert N2_LABEL in after
    assert after.count('Name') == 2
