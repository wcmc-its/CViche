"""The M2 research-support contract: bucket rules, field rules, rendered rows.

Companion to `test_stage6_grant_number_only_sparsity.py`, which pins the
sparsity guard alone. This file pins the rest of section M2 -- the twenty gaps
named in the review of PR #734 (thread 3932451691) plus the explicit rendering
contract asked for in thread 3932312407 item 8.

Two levels, deliberately:

* the module-level classifiers (`filter_role_effort_headers`,
  `match_effort_for_title`, `apply_effort_to_grants`, `rebucket_grants_by_status`,
  `reclassify_past_m2a_grants`, `resolve_pi_name`, `normalize_percent_effort`)
  take plain data and return plain data, so a bucket or effort decision is
  pinned without building a DOCX;
* every claim about what a reader of the finished CV actually sees goes through
  `_create_grant_table` / `_fill_research_support` and is read back off the
  generated document -- never off a stdout line. A log message is not the
  behaviour; the document is.

The generator fixture is the same minimal `__new__` shell the sparsity file
uses: a blank `docx.Document` plus the three WCM funding headers as plain
paragraphs, which is all `_fill_research_support` looks for. No template file,
no DB, no LLM.

Every person named below is invented -- "Ada Testowner" for the CV owner,
"Jane Smith" for an extracted PI. This module's own subject is that a grant's
values are CV content, so its fixtures do not get to be an exception: a real
researcher's name checked into a test is the same disclosure whether or not
their grants came with it.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_research_support_contract.py -p no:cacheprovider
"""

import ast
import logging
import sys
from pathlib import Path

import docx
import pytest
from docx.table import Table

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.sections import research_support  # noqa: E402
from unified_pipeline.stage6.sections.research_support import (  # noqa: E402
    UnsupportedRebucketTargetError,
    apply_effort_to_grants,
    claim_goal_rows,
    fill_major_goals_from_text,
    filter_role_effort_headers,
    match_effort_for_title,
    normalize_percent_effort,
    parse_major_goals,
    rebucket_grants_by_status,
    reclassify_past_m2a_grants,
    resolve_pi_name,
)
from unified_pipeline.stage6.normalization import grant_status_rebucket_target  # noqa: E402
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402

CURRENT = 'Current Research Funding'
COMPLETED = 'Past (Completed) Funding'
PENDING = 'Pending Funding'
HEADERS = (CURRENT, COMPLETED, PENDING)

# Every date rule below is judged against this year, passed in rather than read
# off the clock, so the boundary cases stay true after 2026 (thread 3932312407
# item 7).
TEST_YEAR = 2026


def _generator(emit_comments=False):
    """A generator with just enough state to build one table or fill one section."""
    gen = WCMTemplateGenerator.__new__(WCMTemplateGenerator)
    gen.doc = docx.Document()
    gen.verbose = False
    gen.stats = {'tables_populated': 0, 'entries_inserted': 0, 'comments_added': 0}
    gen.emit_comments = emit_comments
    gen._comments = []
    gen._comment_id = 1
    gen._overflow_entries = []
    gen._declined_grant_entries = []  # #839: _create_grant_table's decline path appends here
    return gen


def _sectioned_generator(emit_comments=False):
    """A generator whose document already carries the three WCM funding headers."""
    gen = _generator(emit_comments=emit_comments)
    for header in HEADERS:
        gen.doc.add_paragraph(header)
    return gen


def _entry(code='M2A', text='grant', **fields):
    return {'text': text, 'taxonomy_code': code, 'extracted_fields': dict(fields)}


def _cells(table):
    return {row.cells[0].text: row.cells[1].text for row in table.rows}


def _rows(table):
    return [(row.cells[0].text, row.cells[1].text) for row in table.rows]


def _header_positions(gen):
    """Body index of each WCM funding header paragraph."""
    body = list(gen.doc.element.body)
    positions = {}
    for para in gen.doc.paragraphs:
        text = para.text.strip()
        if text in HEADERS and text not in positions:
            positions[text] = body.index(para._element)
    return positions


def _elements_under(gen, header):
    """Body elements between one funding header and the next one."""
    body = list(gen.doc.element.body)
    positions = _header_positions(gen)
    start = positions[header]
    later = [pos for pos in positions.values() if pos > start]
    end = min(later) if later else len(body)
    return body[start + 1:end]


def _tables_under(gen, header):
    return [Table(el, gen.doc) for el in _elements_under(gen, header)
            if el.tag.endswith('}tbl')]


def _tags_under(gen, header):
    return [el.tag.split('}')[-1] for el in _elements_under(gen, header)]


def _titles_under(gen, header):
    return [_cells(table).get('Project title:') for table in _tables_under(gen, header)]


# --- item 1: status-based rebucketing decides the bucket ------------------------

def test_under_review_status_moves_an_m2a_grant_to_pending():
    """An M2A grant whose status reads "Under review" renders under Pending.

    `grant_status_rebucket_target` was called on every grant and its answer was
    never asserted anywhere -- only the stdout line was. Pins the resulting
    bucket instead (review thread 3932451691 item 1).
    """
    gen = _sectioned_generator()
    gen._fill_research_support(
        {'M2A': [_entry('M2A', title='Under Review Project', agency='NIH',
                        status='Under review', start_date='01/2026')]},
        current_year=TEST_YEAR)

    assert _titles_under(gen, CURRENT) == []
    assert _titles_under(gen, COMPLETED) == []
    assert _titles_under(gen, PENDING) == ['Under Review Project']


def test_under_review_status_moves_an_m2b_grant_to_pending():
    """The rebucketer reads M2B as a source too, not only M2A.

    Same gap as above from the other side: a completed-funding record whose own
    status says it is still under review belongs under Pending (review thread
    3932451691 item 1).
    """
    gen = _sectioned_generator()
    gen._fill_research_support(
        {'M2B': [_entry('M2B', title='Resubmitted Project', agency='NIH',
                        status='Under review', start_date='01/2020')]},
        current_year=TEST_YEAR)

    assert _titles_under(gen, COMPLETED) == []
    assert _titles_under(gen, PENDING) == ['Resubmitted Project']


# --- item 2: a past end date moves the grant in the DOCUMENT --------------------

def test_past_m2a_grant_is_rendered_under_completed_funding():
    """The past-end-date rule is pinned on the document, not the stdout line.

    The existing verbose test asserts "Reclassified to M2B..." reaches stdout,
    which a refactor could keep printing while rendering the grant in the wrong
    place. This asserts where the table lands (review thread 3932451691 item 2).
    """
    gen = _sectioned_generator()
    gen._fill_research_support(
        {'M2A': [_entry('M2A', title='Ended Project Study', agency='NIH',
                        start_date='01/2018', end_date='06/2020')]},
        current_year=TEST_YEAR)

    assert _titles_under(gen, CURRENT) == []
    assert _titles_under(gen, COMPLETED) == ['Ended Project Study']


# --- item 3: open-ended end dates are never reclassified ------------------------

@pytest.mark.parametrize('end_date', ['Present', 'current', 'ONGOING', '', 'TBD'])
def test_current_end_date_does_not_move_m2a_grant(end_date):
    """"Present"/"current"/"ongoing"/"" keep a grant in Current Research Funding.

    Without a test, a refactor of the date parser could silently file every
    active grant under Past (Completed) Funding (review thread 3932451691
    item 3).

    What this pins is the outcome, not one branch: an open-ended end date is
    held out of the reclassifier twice over -- by the `OPEN_ENDED_END_DATES`
    membership test and, since none of those four literals contains a
    four-digit year, by the `\\d{4}` search behind it. No input can tell the
    two apart while the literal list stays digit-free, so removing either one
    leaves this test green. "TBD" is the second guard on its own: not in the
    literal list, still no year to parse.
    """
    gen = _sectioned_generator()
    gen._fill_research_support(
        {'M2A': [_entry('M2A', title='Ongoing Project Study', agency='NIH',
                        start_date='01/2019', end_date=end_date)]},
        current_year=TEST_YEAR)

    assert _titles_under(gen, CURRENT) == ['Ongoing Project Study']
    assert _titles_under(gen, COMPLETED) == []


# --- item 4: a future end date stays current ------------------------------------

def test_future_m2a_end_date_remains_current():
    """The other side of the boundary: an end date after `current_year` stays M2A.

    Judged against the injected year, so the test does not start failing when
    the wall clock passes 2030 (review thread 3932451691 item 4).
    """
    gen = _sectioned_generator()
    gen._fill_research_support(
        {'M2A': [_entry('M2A', title='Future Project Study', agency='NIH',
                        start_date='01/2026', end_date='2030-12')]},
        current_year=TEST_YEAR)

    assert _titles_under(gen, CURRENT) == ['Future Project Study']
    assert _titles_under(gen, COMPLETED) == []


def test_end_date_in_the_current_year_remains_current():
    """The comparison is `end_year < current_year`, so this year is still current."""
    gen = _sectioned_generator()
    gen._fill_research_support(
        {'M2A': [_entry('M2A', title='This Year Project', agency='NIH',
                        start_date='01/2026', end_date=f'06/{TEST_YEAR}')]},
        current_year=TEST_YEAR)

    assert _titles_under(gen, CURRENT) == ['This Year Project']


# --- item 5: grant_number in Award Source, never twice --------------------------

def test_grant_number_is_appended_to_award_source_when_absent():
    """A grant number nowhere else in the record is appended to Award Source.

    The WCM block has no grant-number row, so the identifier rides in Award
    Source; production-visible and previously untested (review thread
    3932451691 item 5).
    """
    fields = {'agency': 'NIH', 'title': 'Cancer Immunology Project',
              'grant_number': 'R01 CA123456'}
    table = _generator()._create_grant_table(fields, 'M2A')
    assert _cells(table)['Award Source:'] == 'NIH (R01 CA123456)'


def test_grant_number_alone_becomes_the_award_source_when_there_is_no_agency():
    """With no agency at all the identifier is the whole Award Source value."""
    fields = {'grant_number': 'R01 CA123456', 'title': 'Cancer Immunology Project'}
    table = _generator()._create_grant_table(fields, 'M2A')
    assert _cells(table)['Award Source:'] == 'R01 CA123456'


def test_grant_number_already_in_the_agency_is_not_duplicated():
    """`_create_grant_table`'s Award Source guard: a number already inside the
    agency text is not re-appended."""
    fields = {'agency': 'NIH R01 CA123456', 'title': 'Cancer Immunology Project',
              'grant_number': 'R01 CA123456'}
    table = _generator()._create_grant_table(fields, 'M2A')
    assert _cells(table)['Award Source:'] == 'NIH R01 CA123456'


def test_grant_number_already_in_the_title_is_not_duplicated():
    """The same guard reads the title too, and is case-folded, not case-sensitive."""
    fields = {'agency': 'NIH', 'title': 'Cancer Immunology Project (r01 ca123456)',
              'grant_number': 'R01 CA123456'}
    table = _generator()._create_grant_table(fields, 'M2A')
    assert _cells(table)['Award Source:'] == 'NIH'


# --- #839: a decline appends to _declined_grant_entries -------------------------

def test_declined_sparse_entry_is_appended_to_declined_grant_entries():
    """No title, no substantive info, no grant-number info: `_create_grant_table`
    returns None and records the entry so `generate()` can still route it to
    the Appendix instead of dropping it outright."""
    gen = _generator()
    fields = {}
    entry = {'text': 'DECLINED_SPARSE_TOKEN', 'taxonomy_code': 'M2A', 'extracted_fields': fields}
    table = gen._create_grant_table(fields, 'M2A', entry)
    assert table is None
    assert gen._declined_grant_entries == [entry]


def test_non_declined_entry_is_not_appended_to_declined_grant_entries():
    """A well-formed grant renders a table and leaves the decline list empty."""
    gen = _generator()
    fields = {'agency': 'NIH', 'title': 'Cancer Immunology Project'}
    entry = {'text': 'Cancer Immunology Project', 'taxonomy_code': 'M2A', 'extracted_fields': fields}
    table = gen._create_grant_table(fields, 'M2A', entry)
    assert table is not None
    assert gen._declined_grant_entries == []


def test_declined_entry_with_no_entry_arg_is_not_appended():
    """The `isinstance(entry, dict)` guard's negative path (verify_r2 NOTE 2):
    `entry=None` still declines (returns None) but must not append -- there
    is nothing dict-shaped to append. Kills the mutant that drops the guard
    and appends unconditionally."""
    gen = _generator()
    table = gen._create_grant_table({}, 'M2A', None)
    assert table is None
    assert gen._declined_grant_entries == []


# --- item 6: PI auto-fill from the CV owner -------------------------------------

def test_principal_investigator_role_auto_fills_cv_owner():
    """A Principal Investigator role with no extracted PI name names the CV owner.

    Driven through `_fill_research_support` so the owner resolution wire
    (`_get_cv_owner_name`) is covered as well (review thread 3932451691 item 6).
    """
    gen = _sectioned_generator()
    gen._fill_research_support(
        {'M2A': [_entry('M2A', title='Owner Led Project', agency='NIH',
                        pi_role='Principal Investigator', start_date='01/2019',
                        end_date='Present')]},
        cv_owner={'first_name': 'Ada', 'last_name': 'Testowner'},
        current_year=TEST_YEAR)

    cells = _cells(_tables_under(gen, CURRENT)[0])
    assert cells['Name of Principal Investigator:'] == 'Ada Testowner'
    assert cells['Your role:'] == 'Principal Investigator'


def test_non_pi_role_does_not_auto_fill_cv_owner():
    """The auto-fill needs both "principal" and "investigator"; a Co-I gets nothing."""
    assert resolve_pi_name({}, '', 'Co-Investigator', 'Ada Testowner') == ''
    assert resolve_pi_name({}, '', 'Principal Investigator', 'Ada Testowner') == 'Ada Testowner'


def test_extracted_pi_name_beats_the_cv_owner():
    """An extracted PI name wins over the owner auto-fill."""
    assert resolve_pi_name({'pi_name': 'Jane Smith'}, '', 'Principal Investigator',
                           'Ada Testowner') == 'Jane Smith'


def test_null_role_with_a_cv_owner_does_not_crash():
    """A JSON-null `role` reached the owner auto-fill as None and raised there.

    `_create_grant_table` reads the role as
    `fields.get('pi_role') or fields.get('role', '')`, which yields None -- not
    '' -- on a record that carries `role` present and JSON-null while `pi_role`
    is falsy. `resolve_pi_name` then called `None.lower()`, aborting the whole
    stage-6 run for that CV. Both ends are pinned: the classifier direct, and
    the caller wire that manufactures the None. The PI cell stays empty, which
    is what a falsy role rendered before, so nothing that renders today moves.
    """
    assert resolve_pi_name({'role': None}, '', None, 'Ada Testowner') == ''

    table = _generator()._create_grant_table(
        {'title': 'Null Role Project', 'agency': 'NIH', 'role': None},
        'M2A', owner_name='Ada Testowner')
    assert _cells(table)['Name of Principal Investigator:'] == ''


# --- item 7: PI extraction from a pipe-delimited source row ---------------------

def test_pi_name_is_extracted_from_four_part_pipe_text():
    """The trailing cell of "Agency | Amount | Dates | PI Name" is read as the PI.

    Several heuristics stacked in one branch and none of them were covered
    (review thread 3932451691 item 7).
    """
    raw = 'NIH | $100,000 | 2019-2021 | Jane Smith'
    assert resolve_pi_name({}, raw, '', '') == 'Jane Smith'


def test_pi_name_accepts_a_middle_initial():
    """"First M. Last" is the second form the whitespace pattern admits."""
    raw = 'NIH | $100,000 | 2019-2021 | Jane M. Smith'
    assert resolve_pi_name({}, raw, '', '') == 'Jane M. Smith'


@pytest.mark.parametrize('raw, reason', [
    ('NIH | $100,000 | Jane Smith', 'fewer than four pipe-separated parts'),
    ('NIH | NIH | NIH | NIH', 'every part identical (merged source cells)'),
    ('NIH | $1 | 2019 | Grant Smith', 'trailing cell carries a project keyword'),
    ('NIH | $1 | 2019 | Bartholomewmaximilianconstantine Vandersteenhuysenrichards',
     'trailing cell is 50 characters or longer'),
    ('NIH | $1 | 2019 | 2019-2021', 'trailing cell is digits and separators only'),
    ('NIH | $1 | 2019 | Smith, Jane', '"Last, First" is not a form the pattern accepts'),
])
def test_pi_name_is_not_extracted_from_a_rejected_trailing_cell(raw, reason):
    """Each documented reject of the pipe parser leaves the PI name empty.

    The guards overlap by construction -- a digits-only cell also fails the name
    pattern -- so each case pins the outcome the parser is documented to
    produce, not which single guard produced it. "Smith, Jane" is the comment
    fix from thread 3932312407 item 4: the comma form is explicitly out of
    scope, and this is the regression test that says so (review thread
    3932451691 item 7).
    """
    assert resolve_pi_name({}, raw, '', '') == '', reason


# --- item 8: percent-effort extraction, parsed and applied ----------------------

def test_role_effort_header_is_parsed_into_the_effort_lookup():
    """The reviewer's own worked example, asserted on the parsed lookup.

    "Individual's role in project including percent effort" is a source-table
    header, not a grant: its body lines become the effort lookup and the row
    itself is dropped from the entries to render (review thread 3932451691
    item 8).
    """
    lookup = {}
    entries = [{'text': "Individual's role in project including percent effort\n"
                        'Project Alpha 0.01\n'
                        'Project Beta .08FTE\n'
                        'Project Gamma 25'}]
    kept, messages = filter_role_effort_headers(entries, lookup)

    assert kept == []
    assert lookup == {'project alpha': '1%', 'project beta': '8%', 'project gamma': '25%'}
    assert messages == ['  Filtered role/effort header entry, extracted 3 effort values']


@pytest.mark.parametrize('raw, expected', [
    ('0.01', '1%'),
    ('.08', '8%'),
    ('25', '25%'),
    ('0.015', '1.5%'),   # truncated to "1%" before the fix
    ('1.5', '1.5%'),     # truncated to "1%" before the fix
    ('1', '100%'),       # at the fractional ceiling: still read as a fraction
    ('100', '100%'),
])
def test_percent_effort_keeps_its_stated_precision(raw, expected):
    """Fractional percentages survive the conversion instead of being truncated.

    The two `int()` calls this replaced truncated on both branches:
    `int(0.015 * 100)` rendered a 1.5% effort as "1%", and `int(1.5)` rendered
    a 1.5% effort as "1%" as well (review thread 3932312407 item 3, thread
    3932451691 item 8).
    """
    assert normalize_percent_effort(raw) == expected


@pytest.mark.parametrize('raw', ['0', '0.0', '-1', '101', '150', 'abc', ''])
def test_out_of_range_percent_effort_is_dropped(raw):
    """A figure outside (0%, 100%] is not a percent effort and is not rendered.

    Before the fix "0" rendered as "0%" and "150" as "150%" (review thread
    3932312407 item 3).
    """
    assert normalize_percent_effort(raw) is None


@pytest.mark.parametrize('raw', ['nan', 'NaN', '-NaN', 'sNaN'])
def test_a_nan_effort_figure_is_discarded_rather_than_raised(raw):
    """A NaN got past the constructor guard and raised off the next comparison.

    `Decimal('abc')` raises InvalidOperation from the constructor and is caught;
    `Decimal('NaN')` constructs, and then `value <= FRACTIONAL_EFFORT_CEILING`
    signals the same InvalidOperation out of the function. The docstring
    promised None for anything unparseable, so this pins the promise for the
    input that broke it. `PROJECT_EFFORT_LINE_RE` cannot produce these strings,
    so no rendered byte depends on it.
    """
    assert normalize_percent_effort(raw) is None


@pytest.mark.parametrize('raw', ['Infinity', '-Infinity'])
def test_an_infinite_effort_figure_needs_no_extra_guard(raw):
    """Infinity is why the NaN guard is `is_nan()` and not `is_finite()`.

    Unlike NaN it compares without signalling, so the range check already
    discards it in both signs; pinned so a later widening of that guard is a
    deliberate choice rather than an accident.
    """
    assert normalize_percent_effort(raw) is None


@pytest.mark.parametrize('raw', ['150', '-1', 'abc'])
def test_a_discarded_effort_figure_is_never_written_to_the_log(raw, caplog):
    """The discard is logged structurally: what happened, never which figure.

    The figure is a cell of the CV's own role/effort table, so it is CV
    content. The out-of-range branch used to log the value itself -- "Discarded
    an out-of-range percent effort figure: 150" -- in a module whose docstring
    states the no-PII logging rule and whose other logger calls carry counts
    and key names only.

    Values chosen so the assertion cannot pass by accident: none of them is a
    substring of the range the message names.
    """
    with caplog.at_level(logging.DEBUG, logger=research_support.__name__):
        assert normalize_percent_effort(raw) is None

    assert caplog.records, 'the discard is still expected to be logged'
    for record in caplog.records:
        assert raw not in record.getMessage()


def test_extracted_effort_reaches_the_rendered_percent_effort_cell():
    """The header's effort figure lands in the matching grant's rendered cell.

    The end-to-end half of item 8: parsing the lookup is not the behaviour, the
    populated "Your percent (%) effort:" row is (review thread 3932451691
    item 8).
    """
    gen = _sectioned_generator()
    gen._fill_research_support(
        {'M2A': [
            {'text': "Individual's role in project including percent effort\n"
                     'Project Alpha 0.01\n'
                     'Project Beta .08FTE'},
            _entry('M2A', title='Project Alpha', agency='NIH', start_date='01/2019',
                   end_date='Present'),
            _entry('M2A', title='Project Beta', agency='NSF', start_date='01/2019',
                   end_date='Present'),
        ]},
        current_year=TEST_YEAR)

    rendered = {_cells(t)['Project title:']: _cells(t)['Your percent (%) effort:']
                for t in _tables_under(gen, CURRENT)}
    assert rendered == {'Project Alpha': '1%', 'Project Beta': '8%'}


def test_role_effort_header_row_is_not_rendered_as_a_grant():
    """The header row itself leaves no table behind."""
    gen = _sectioned_generator()
    gen._fill_research_support(
        {'M2A': [{'text': "Individual's role in project including percent effort\n"
                          'Project Alpha 0.01'}]},
        current_year=TEST_YEAR)

    assert _tables_under(gen, CURRENT) == []


# --- item 9: an already-extracted effort wins -----------------------------------

def test_extracted_percent_effort_takes_precedence():
    """A percent effort already on the record is never overwritten by the lookup.

    `if not title or fields.get('percent_effort'): continue` -- the lookup only
    fills a gap (review thread 3932451691 item 9).
    """
    entries = [_entry('M2A', title='Project Alpha', percent_effort='12%')]
    apply_effort_to_grants(entries, {'project alpha': '8%'})
    assert entries[0]['extracted_fields']['percent_effort'] == '12%'


def test_extracted_percent_effort_takes_precedence_in_the_rendered_cell():
    """The same precedence, read off the document rather than the record."""
    gen = _sectioned_generator()
    gen._fill_research_support(
        {'M2A': [
            {'text': "Individual's role in project including percent effort\n"
                     'Project Alpha .08FTE'},
            _entry('M2A', title='Project Alpha', agency='NIH', percent_effort='12%',
                   start_date='01/2019', end_date='Present'),
        ]},
        current_year=TEST_YEAR)

    assert _cells(_tables_under(gen, CURRENT)[0])['Your percent (%) effort:'] == '12%'


# --- item 10: project-name matching, both directions, no false positives --------

def test_effort_matches_the_exactly_named_project_in_both_directions():
    """Neither of a pair of prefix-related project names takes the other's effort.

    The old rule was substring-only and took the first hit in insertion order,
    so "Project Alpha Extended" was handed "Project Alpha"'s 1% -- a wrong
    percent effort on a rendered CV, not merely a testing gap (review thread
    3932451691 item 10 / thread 3932312407 item 6).
    """
    lookup = {'project alpha': '1%', 'project alpha extended': '8%'}
    assert match_effort_for_title('project alpha', lookup) == '1%'
    assert match_effort_for_title('project alpha extended', lookup) == '8%'


def test_prefix_related_projects_render_their_own_percent_effort():
    """The same pair, end to end, read off the two generated tables."""
    gen = _sectioned_generator()
    gen._fill_research_support(
        {'M2A': [
            {'text': "Individual's role in project including percent effort\n"
                     'Project Alpha 0.01\n'
                     'Project Alpha Extended .08FTE'},
            _entry('M2A', title='Project Alpha', agency='NIH', start_date='01/2019',
                   end_date='Present'),
            _entry('M2A', title='Project Alpha Extended', agency='NIH',
                   start_date='01/2019', end_date='Present'),
        ]},
        current_year=TEST_YEAR)

    rendered = {_cells(t)['Project title:']: _cells(t)['Your percent (%) effort:']
                for t in _tables_under(gen, CURRENT)}
    assert rendered == {'Project Alpha': '1%', 'Project Alpha Extended': '8%'}


def test_a_single_substring_match_is_still_used():
    """Substring matching survives as the explicit fallback, not as the first rule.

    A source table really does write "Project Alpha" in the effort header and
    "Project Alpha: aims 1-3" in the grant list.
    """
    assert match_effort_for_title('project alpha: aims 1-3',
                                  {'project alpha': '1%'}) == '1%'


def test_ambiguous_substring_match_assigns_no_effort():
    """Two project names substring-matching one title assign nothing at all.

    Guessing between them is how the wrong percentage reached a CV in the first
    place (review thread 3932451691 item 10).
    """
    lookup = {'alpha': '1%', 'alpha study': '8%'}
    assert match_effort_for_title('alpha study of xyz', lookup) is None


# --- item 11: a numeric role is a percent effort --------------------------------

@pytest.mark.parametrize('role', ['10', '10%', '0.5', '0.5%'])
def test_numeric_pi_role_is_treated_as_percent_effort(role):
    """A `pi_role` that is only a number is the misextracted percent effort.

    The role cell empties and the effort cell takes the raw value (review
    thread 3932451691 item 11).
    """
    fields = {'title': 'Numeric Role Project', 'pi_role': role, 'start_date': '01/2019'}
    cells = _cells(_generator()._create_grant_table(fields, 'M2A'))
    assert cells['Your percent (%) effort:'] == role
    assert cells['Your role:'] == ''


@pytest.mark.parametrize('role', ['10 percent', 'approximately 10%', 'Co-Investigator'])
def test_descriptive_role_stays_a_role(role):
    """Anything that is not purely numeric stays in the role cell, untouched."""
    fields = {'title': 'Descriptive Role Project', 'pi_role': role, 'start_date': '01/2019'}
    cells = _cells(_generator()._create_grant_table(fields, 'M2A'))
    assert cells['Your role:'] == role
    assert cells['Your percent (%) effort:'] == ''


def test_numeric_role_conversion_does_not_run_when_an_effort_exists():
    """With a percent effort already extracted the role is left alone."""
    fields = {'title': 'Numeric Role Project', 'pi_role': '10',
              'percent_effort': '12%', 'start_date': '01/2019'}
    cells = _cells(_generator()._create_grant_table(fields, 'M2A'))
    assert cells['Your role:'] == '10'
    assert cells['Your percent (%) effort:'] == '12%'


# --- item 12: title fallbacks ---------------------------------------------------

@pytest.mark.parametrize('field', ['title', 'trial_title', 'study_title', 'text'])
def test_grant_title_fallbacks(field):
    """Each of the four title keys alone produces the "Project title:" value.

    Clinical-trial records arrive under `trial_title` / `study_title` / `text`
    rather than `title`, and none of those paths was covered (review thread
    3932451691 item 12).
    """
    fields = {field: 'Longitudinal Cohort Study', 'start_date': '01/2019'}
    cells = _cells(_generator()._create_grant_table(fields, 'M2A'))
    assert cells['Project title:'] == 'Longitudinal Cohort Study'


def test_title_precedence_is_title_then_trial_then_study_then_text():
    """The four keys are an `or` chain, in that order."""
    fields = {'title': 'A Title Long Enough', 'trial_title': 'A Trial Title',
              'study_title': 'A Study Title', 'text': 'Some Raw Text',
              'start_date': '01/2019'}
    cells = _cells(_generator()._create_grant_table(fields, 'M2A'))
    assert cells['Project title:'] == 'A Title Long Enough'

    del fields['title']
    assert _cells(_generator()._create_grant_table(fields, 'M2A'))['Project title:'] \
        == 'A Trial Title'
    del fields['trial_title']
    assert _cells(_generator()._create_grant_table(fields, 'M2A'))['Project title:'] \
        == 'A Study Title'
    del fields['study_title']
    assert _cells(_generator()._create_grant_table(fields, 'M2A'))['Project title:'] \
        == 'Some Raw Text'


# --- item 13: agency fallbacks --------------------------------------------------

@pytest.mark.parametrize('field', ['agency', 'funding_source', 'sponsor'])
def test_award_source_agency_fallbacks(field):
    """Each of the three agency keys alone produces the Award Source value.

    Industry-sponsored records use `sponsor` and some extractions use
    `funding_source`; neither path was covered (review thread 3932451691
    item 13).
    """
    fields = {'title': 'Sponsored Cohort Study', field: 'Acme Pharmaceuticals',
              'start_date': '01/2019'}
    cells = _cells(_generator()._create_grant_table(fields, 'M2A'))
    assert cells['Award Source:'] == 'Acme Pharmaceuticals'


def test_agency_precedence_is_agency_then_funding_source_then_sponsor():
    """The three keys are an `or` chain, in that order."""
    fields = {'title': 'Sponsored Cohort Study', 'agency': 'NIH',
              'funding_source': 'Foundation', 'sponsor': 'Acme',
              'start_date': '01/2019'}
    assert _cells(_generator()._create_grant_table(fields, 'M2A'))['Award Source:'] == 'NIH'
    del fields['agency']
    assert _cells(_generator()._create_grant_table(fields, 'M2A'))['Award Source:'] \
        == 'Foundation'
    del fields['funding_source']
    assert _cells(_generator()._create_grant_table(fields, 'M2A'))['Award Source:'] == 'Acme'


# --- item 14: funding fallback --------------------------------------------------

@pytest.mark.parametrize('field', ['annual_direct_costs', 'total_funding'])
def test_annual_direct_costs_fallbacks(field):
    """Either funding key alone fills the "Annual direct costs:" row.

    Untested before, and the two keys are read twice in opposite precedence
    inside the renderer (review thread 3932451691 item 14).
    """
    fields = {'title': 'Funded Cohort Study', field: '100000', 'start_date': '01/2019'}
    cells = _cells(_generator()._create_grant_table(fields, 'M2A'))
    assert cells['Annual direct costs:'] == '$100,000'


def test_annual_direct_costs_wins_when_both_funding_keys_are_present():
    """`annual_direct_costs` is what the row is for; `total_funding` is the fallback."""
    fields = {'title': 'Funded Cohort Study', 'annual_direct_costs': '100000',
              'total_funding': '500000', 'start_date': '01/2019'}
    cells = _cells(_generator()._create_grant_table(fields, 'M2A'))
    assert cells['Annual direct costs:'] == '$100,000'


# --- item 15: currency formatting -----------------------------------------------

@pytest.mark.parametrize('value, expected', [
    ('100000', '$100,000'),
    ('$100,000', '$100,000'),
    ('100000.50', '$100,000.50'),
    ('100,000', '$100,000'),
    ('not a number', 'not a number'),
])
def test_annual_direct_costs_are_formatted(value, expected):
    """The rendered costs cell follows `_format_currency`'s real contract.

    Whole numbers lose their decimals, an already-$-prefixed value is passed
    through verbatim, and an unparseable value is rendered as it arrived rather
    than dropped (`stage6/formatting/values.py:101-134`). Expected strings are
    read off that implementation, not invented (review thread 3932451691
    item 15).
    """
    fields = {'title': 'Funded Cohort Study', 'annual_direct_costs': value,
              'start_date': '01/2019'}
    cells = _cells(_generator()._create_grant_table(fields, 'M2A'))
    assert cells['Annual direct costs:'] == expected


# --- item 16: cross-field duplication cleanup -----------------------------------

def test_title_duplicated_into_agency_clears_the_award_source():
    """When stage 4 puts the title in `agency` too, Award Source renders empty.

    Untested branch; the same content in two rows reads as two facts (review
    thread 3932451691 item 16).
    """
    fields = {'title': 'Repeated Content Study', 'agency': 'repeated content study',
              'start_date': '01/2019'}
    cells = _cells(_generator()._create_grant_table(fields, 'M2A'))
    assert cells['Award Source:'] == ''
    assert cells['Project title:'] == 'Repeated Content Study'


def test_title_duplicated_into_funding_clears_the_costs_cell():
    """The funding half of the same cleanup zeroes the costs row."""
    fields = {'title': 'Repeated Content Study', 'total_funding': 'Repeated Content Study',
              'annual_direct_costs': 'Repeated Content Study', 'start_date': '01/2019'}
    cells = _cells(_generator()._create_grant_table(fields, 'M2A'))
    assert cells['Annual direct costs:'] == ''
    assert cells['Project title:'] == 'Repeated Content Study'


def test_rendering_does_not_mutate_the_callers_extracted_fields():
    """Rendering leaves the caller's record exactly as it arrived.

    The duplicate-funding cleanup used to write `fields['total_funding'] = ''`
    and `fields['annual_direct_costs'] = ''` straight into the pipeline record,
    making rendering non-idempotent and changing what later consumers of the
    entry saw (thread 3932312407 item 1, review thread 3932451691 item 16).
    """
    fields = {'title': 'Repeated Content Study', 'total_funding': 'Repeated Content Study',
              'annual_direct_costs': 'Repeated Content Study', 'start_date': '01/2019'}
    submitted = dict(fields)

    _generator()._create_grant_table(fields, 'M2A', {'text': 'x', 'taxonomy_code': 'M2A',
                                                    'extracted_fields': fields})

    assert fields == submitted


def test_section_fill_does_not_mutate_the_callers_entries():
    """The same immutability one level up, across the whole section fill.

    Classification writes `percent_effort` and `reclassification_note`, both on
    this section's own copies (`copy_entries_for_render`).
    """
    entries_by_code = {
        'M2A': [
            {'text': "Individual's role in project including percent effort\n"
                     'Project Alpha 0.01'},
            _entry('M2A', title='Project Alpha', agency='NIH', start_date='01/2018',
                   end_date='06/2020'),
        ],
    }
    submitted = [dict(e, extracted_fields=dict(e.get('extracted_fields') or {}))
                 if e.get('extracted_fields') is not None else dict(e)
                 for e in entries_by_code['M2A']]

    gen = _sectioned_generator()
    gen._fill_research_support(entries_by_code, current_year=TEST_YEAR)

    assert entries_by_code['M2A'] == submitted


# --- item 17: the optional major-goals row --------------------------------------

@pytest.mark.parametrize('goals, present', [
    ('1234567890', False),   # exactly 10 characters: absent
    ('12345678901', True),   # 11 characters: present
])
def test_major_goals_row_boundary(goals, present):
    """`len(goals.strip()) > 10` is the row's boundary, pinned on both sides.

    A strict `>` -- an off-by-one here either drops a real goals row or renders
    a stub one (review thread 3932451691 item 17).
    """
    fields = {'title': 'Goals Boundary Study', 'major_goals': goals,
              'start_date': '01/2019'}
    labels = [label for label, _ in _rows(_generator()._create_grant_table(fields, 'M2A'))]
    assert ('Major project goals:' in labels) is present


@pytest.mark.parametrize('field', ['major_goals', 'description', 'narrative'])
def test_major_goals_fallback_order(field):
    """Each of the three goals keys alone fills the optional row."""
    fields = {'title': 'Goals Fallback Study', field: 'Aim 1: characterise the cohort',
              'start_date': '01/2019'}
    cells = _cells(_generator()._create_grant_table(fields, 'M2A'))
    assert cells['Major project goals:'] == 'Aim 1: characterise the cohort'


def test_major_goals_precedence_is_goals_then_description_then_narrative():
    """The three keys are an `or` chain, in that order."""
    fields = {'title': 'Goals Precedence Study', 'major_goals': 'Stated major goals',
              'description': 'A description here', 'narrative': 'A narrative here',
              'start_date': '01/2019'}
    assert _cells(_generator()._create_grant_table(fields, 'M2A'))['Major project goals:'] \
        == 'Stated major goals'
    del fields['major_goals']
    assert _cells(_generator()._create_grant_table(fields, 'M2A'))['Major project goals:'] \
        == 'A description here'
    del fields['description']
    assert _cells(_generator()._create_grant_table(fields, 'M2A'))['Major project goals:'] \
        == 'A narrative here'


# --- #958: major goals from the source text -------------------------------------

_GOAL = 'Map the pollinator corridors of the Example Valley'


@pytest.mark.parametrize('text, expected', [
    # The plain label, tab-separated, as the last line of a table-form grant.
    (f'Award Source: | Example Fund\nThe major goals of this project are:\t{_GOAL}', _GOAL),
    # The WCM template's own label, in a goals row of its own.
    (f'(Optional - The major goals of this project are): | {_GOAL}', _GOAL),
    (f'(Optional - The major goals of this project are:) | {_GOAL}', _GOAL),
    (f'The major goals of this project are: | {_GOAL}', _GOAL),
    (f'The major goals of this project are | {_GOAL}', _GOAL),
    (f'The major goals of this project are\t{_GOAL}', _GOAL),
    (f'THE MAJOR GOALS OF THIS PROJECT ARE: {_GOAL}', _GOAL),
    # No separator: the phrase opens the faculty member's sentence, kept whole.
    ('Example Study\tThe major goals of this project are to map the corridors.',
     'The major goals of this project are to map the corridors.'),
    # ...and a paragraph-form grant's next field, the role, is not the goal.
    ('The major goals of this project are to map the corridors.\tRole: PI',
     'The major goals of this project are to map the corridors.'),
    (f'The major goals of this project are: {_GOAL}\tYour role: Co-PI', _GOAL),
    # Any other tab is a wrapped source line and stays, verbatim.
    ('The major goals of this project are:  Survey the valley; oversaw\tfield work  ',
     'Survey the valley; oversaw\tfield work'),
    # The goal ends with its line.
    (f'The major goals of this project are: {_GOAL}\nAnnual direct costs: | $5,000', _GOAL),
])
def test_major_goals_are_parsed_verbatim_from_the_source_text(text, expected):
    assert parse_major_goals(text) == expected


@pytest.mark.parametrize('text', [
    'The major goals of this project are: |',
    '(Optional - The major goals of this project are):',
    'The major goals of this project are\nAward Source: | Example Fund',
    'Award Source: | Example Fund\nProject title: | Example Study',
    '',
    None,
])
def test_an_empty_or_absent_goals_label_is_no_goal(text):
    """An empty label renders nothing -- and never borrows the next line."""
    assert parse_major_goals(text) is None


def test_a_grant_gains_the_goal_stated_in_its_own_text():
    grant = _entry('M2B', text=f'Award Source: | Example Fund\n'
                               f'The major goals of this project are:\t{_GOAL}',
                   title='Example Corridor Study', agency='Example Fund')

    fill_major_goals_from_text([grant])

    assert grant['extracted_fields']['major_goals'] == _GOAL


def test_a_stage4_goal_is_not_replaced_by_the_text():
    grant = _entry('M2B', text=f'The major goals of this project are: {_GOAL}',
                   major_goals='Goal as stage 4 extracted it')

    fill_major_goals_from_text([grant])

    assert grant['extracted_fields']['major_goals'] == 'Goal as stage 4 extracted it'


def _grant(start, end, text='grant', **fields):
    entry = _entry('M2B', text=text, title='Example Corridor Study',
                   agency='Example Fund', start_date='01/2019', **fields)
    entry.update(element_idx_start=start, element_idx_end=end)
    return entry


def _goal_row(parent_idx, goal=_GOAL):
    return {'text': f'The major goals of this project are: | {goal}'.rstrip(),
            'taxonomy_code': 'T', 'element_type': 'table_row', 'recovered_row': True,
            'parent_idx': parent_idx, 'extracted_fields': {}}


@pytest.mark.parametrize('parent_idx', [236, 237, 238])
def test_a_goals_row_inside_the_grant_range_is_claimed_by_that_grant(parent_idx):
    """Both range ends included: the real rows hang off the grant's LAST element."""
    grant, row = _grant(236, 238), _goal_row(parent_idx)
    submitted = dict(row)

    claimed = claim_goal_rows([grant], [row])

    assert claimed == [(row, grant)]
    assert grant['extracted_fields']['major_goals'] == _GOAL
    assert row == submitted


@pytest.mark.parametrize('parent_idx', [235, 239])
def test_a_goals_row_outside_every_grant_range_is_left_alone(parent_idx):
    grant = _grant(236, 238)

    assert claim_goal_rows([grant], [_goal_row(parent_idx)]) == []
    assert 'major_goals' not in grant['extracted_fields']


def test_a_goals_row_inside_two_grant_ranges_is_ambiguous_and_left_alone():
    first, second = _grant(236, 238), _grant(238, 240)

    assert claim_goal_rows([first, second], [_goal_row(238)]) == []
    assert 'major_goals' not in first['extracted_fields']


def test_a_goals_row_does_not_replace_a_different_goal_the_grant_already_has():
    grant = _grant(236, 238, major_goals='A different stated goal')

    assert claim_goal_rows([grant], [_goal_row(238)]) == []
    assert grant['extracted_fields']['major_goals'] == 'A different stated goal'


def test_a_goals_row_repeating_the_grant_own_goal_is_claimed():
    """A5IZ6Q's shape: the grant's text already carries the row's goal."""
    grant = _grant(236, 238, text=f'The major goals of this project are:\t{_GOAL}')
    row = _goal_row(238)
    fill_major_goals_from_text([grant])

    assert claim_goal_rows([grant], [row]) == [(row, grant)]


def test_an_empty_goals_row_is_not_claimed():
    grant = _grant(236, 238)

    assert claim_goal_rows([grant], [_goal_row(238, goal='')]) == []
    assert 'major_goals' not in grant['extracted_fields']


def test_a_goals_line_with_no_parent_table_is_not_claimed():
    row = _goal_row(238)
    del row['parent_idx']

    assert claim_goal_rows([_grant(236, 238)], [row]) == []


@pytest.mark.parametrize('start, end', [(None, 240), (236, None), ('236', '238')])
def test_a_grant_without_an_integer_element_range_claims_nothing(start, end):
    assert claim_goal_rows([_grant(start, end)], [_goal_row(238)]) == []


def test_section_fill_renders_the_claimed_goal_and_returns_the_row():
    grant, row = _grant(236, 238), _goal_row(238)
    t_entries = [row]

    gen = _sectioned_generator()
    claimed = gen._fill_research_support({'M2B': [grant], 'T': t_entries},
                                         current_year=TEST_YEAR)

    assert claimed == [row] and claimed[0] is row
    assert _cells(_tables_under(gen, COMPLETED)[0])['Major project goals:'] == _GOAL
    assert 'major_goals' not in grant['extracted_fields']


@pytest.mark.parametrize('code, header', [
    ('M2A', CURRENT), ('M2B', COMPLETED), ('M2C', PENDING)])
def test_section_fill_renders_the_goal_from_the_grant_own_text(code, header):
    grant = _grant(236, 238, text=f'The major goals of this project are:\t{_GOAL}')
    grant['taxonomy_code'] = code

    gen = _sectioned_generator()
    claimed = gen._fill_research_support({code: [grant]}, current_year=TEST_YEAR)

    assert claimed == []
    assert _cells(_tables_under(gen, header)[0])['Major project goals:'] == _GOAL


def test_a_row_claimed_by_a_declined_grant_is_not_returned():
    """A grant too sparse to render took no goal with it: the row stays Appendix."""
    sparse = _entry('M2B', text='x')
    sparse.update(element_idx_start=236, element_idx_end=238)

    gen = _sectioned_generator()
    claimed = gen._fill_research_support({'M2B': [sparse], 'T': [_goal_row(238)]},
                                         current_year=TEST_YEAR)

    assert claimed == []
    assert gen._declined_grant_entries


def test_a_row_claimed_by_a_grant_under_a_missing_header_is_not_returned():
    gen = _generator()  # no funding headers: nothing renders
    claimed = gen._fill_research_support({'M2B': [_grant(236, 238)], 'T': [_goal_row(238)]},
                                         current_year=TEST_YEAR)

    assert claimed == []


# --- item 18: grant duration ----------------------------------------------------

def test_grant_duration_uses_start_and_end_dates():
    """M2 dates render mm/yy-mm/yy, the format the WCM template asks for."""
    gen = _generator()
    assert gen._format_grant_duration({'start_date': '07/2019', 'end_date': '06/2021'},
                                      'M2A') == '07/19-06/21'


def test_grant_duration_falls_back_to_date():
    """Clinical-trial records carry `date` instead of `start_date`."""
    gen = _generator()
    assert gen._format_grant_duration({'date': '07/2019', 'end_date': '06/2021'},
                                      'M2A') == '07/19-06/21'
    assert gen._format_grant_duration({'date': '07/2019'}, 'M2A') == '07/19-Present'


def test_grant_duration_with_no_end_date_reads_as_present():
    """An open-ended grant renders "-Present", and an empty record renders nothing."""
    gen = _generator()
    assert gen._format_grant_duration({'start_date': '07/2019'}, 'M2A') == '07/19-Present'
    assert gen._format_grant_duration({}, 'M2A') == ''


@pytest.mark.parametrize('code', ['M2A', 'M2B', 'M2C'])
def test_grant_duration_is_identical_across_the_three_m2_codes(code):
    """M2A/M2B/M2C share one date format, so the bucket cannot change the string.

    `DATE_FORMATS` maps all three to 'mm/yy' (`stage6/formatting/dates.py`), so
    this pins what the code really does rather than assuming the buckets differ
    (review thread 3932451691 item 18).
    """
    fields = {'start_date': '07/2019', 'end_date': '06/2021'}
    assert _generator()._format_grant_duration(fields, code) == '07/19-06/21'


# --- item 19: the reclassification note reaches the document --------------------

def test_reclassification_note_is_attached_as_a_document_comment():
    """The past-date note is attached to the table, not merely printed.

    `_add_entry_comments` is what makes the note observable to a reviewer
    opening the CV; the existing test only read stdout (review thread
    3932451691 item 19).
    """
    gen = _sectioned_generator(emit_comments=True)
    gen._fill_research_support(
        {'M2A': [_entry('M2A', title='Ended Project Study', agency='NIH',
                        start_date='01/2018', end_date='06/2020')]},
        current_year=TEST_YEAR)

    notes = [c['text'] for c in gen._comments if c['author'] == 'Reclassification']
    assert notes == ['Reclassified from Current (M2A) to Completed (M2B): '
                     f'end date 06/2020 is before {TEST_YEAR}']
    assert gen.stats['comments_added'] == 1

    # The comment must hang off the grant's own table, not off some other
    # paragraph: the reference run lives inside the reclassified table's XML.
    table = _tables_under(gen, COMPLETED)[0]
    assert 'commentReference' in table._element.xml


def test_status_rebucket_note_is_attached_as_a_document_comment():
    """The status rule's note reaches the document by the same path."""
    gen = _sectioned_generator(emit_comments=True)
    gen._fill_research_support(
        {'M2A': [_entry('M2A', title='Under Review Project', agency='NIH',
                        status='Under review', start_date='01/2026')]},
        current_year=TEST_YEAR)

    notes = [c['text'] for c in gen._comments if c['author'] == 'Reclassification']
    assert notes == ["Reclassified to Pending (M2C): status is 'Under review'"]


def test_no_reclassification_leaves_no_reclassification_comment():
    """A grant nothing moved carries no note -- the comment is not unconditional."""
    gen = _sectioned_generator(emit_comments=True)
    gen._fill_research_support(
        {'M2A': [_entry('M2A', title='Ongoing Project Study', agency='NIH',
                        start_date='01/2019', end_date='Present')]},
        current_year=TEST_YEAR)

    assert [c for c in gen._comments if c['author'] == 'Reclassification'] == []


# --- item 20: structural cases --------------------------------------------------

def test_missing_section_header_renders_nothing_and_does_not_raise():
    """A document without the WCM funding headers is skipped, not crashed into."""
    gen = _generator()  # no header paragraphs at all
    gen._fill_research_support(
        {'M2A': [_entry('M2A', title='Homeless Project Study', agency='NIH',
                        start_date='01/2019')]},
        current_year=TEST_YEAR)

    assert gen.doc.tables == []
    assert gen.stats['tables_populated'] == 0


def test_existing_template_table_is_removed_even_when_the_category_is_empty():
    """The blank template table under a header goes even with no grants to add.

    Leaving it behind ships an empty two-column stub in the finished CV.
    """
    gen = _generator()
    gen.doc.add_paragraph(CURRENT)
    placeholder = gen.doc.add_table(rows=1, cols=2)
    placeholder.cell(0, 0).text = 'Award Source:'
    gen.doc.add_paragraph(COMPLETED)
    gen.doc.add_paragraph(PENDING)

    gen._fill_research_support({'M2A': [], 'M2B': [], 'M2C': []}, current_year=TEST_YEAR)

    assert gen.doc.tables == []


def test_existing_template_table_is_replaced_by_the_grant_tables():
    """With grants to render the placeholder still goes and only real tables remain."""
    gen = _generator()
    gen.doc.add_paragraph(CURRENT)
    placeholder = gen.doc.add_table(rows=1, cols=2)
    placeholder.cell(0, 0).text = 'Award Source:'
    gen.doc.add_paragraph(COMPLETED)
    gen.doc.add_paragraph(PENDING)

    gen._fill_research_support(
        {'M2A': [_entry('M2A', title='Replacement Project Study', agency='NIH',
                        start_date='01/2019', end_date='Present')]},
        current_year=TEST_YEAR)

    assert _titles_under(gen, CURRENT) == ['Replacement Project Study']
    assert len(gen.doc.tables) == 1


def test_empty_category_renders_no_table():
    """An empty bucket contributes nothing under its header."""
    gen = _sectioned_generator()
    gen._fill_research_support({'M2A': [], 'M2B': [], 'M2C': []}, current_year=TEST_YEAR)

    assert gen.doc.tables == []
    assert gen.stats['entries_inserted'] == 0


def test_grants_are_sorted_reverse_chronologically():
    """Within a bucket the most recent grant renders first."""
    gen = _sectioned_generator()
    gen._fill_research_support(
        {'M2A': [
            _entry('M2A', title='Older Project Study', agency='NIH',
                   start_date='01/2015', end_date='12/2028'),
            _entry('M2A', title='Newer Project Study', agency='NIH',
                   start_date='01/2021', end_date='12/2030'),
        ]},
        current_year=TEST_YEAR)

    assert _titles_under(gen, CURRENT) == ['Newer Project Study', 'Older Project Study']


def test_spacing_paragraph_sits_between_grants_but_not_after_the_last():
    """Three grants produce table, spacer, table, spacer, table -- and stop."""
    gen = _sectioned_generator()
    gen._fill_research_support(
        {'M2A': [_entry('M2A', title=f'Spaced Project {n} Study', agency='NIH',
                        start_date='01/2019', end_date=f'12/20{30 - n}')
                 for n in (1, 2, 3)]},
        current_year=TEST_YEAR)

    assert _tags_under(gen, CURRENT) == ['tbl', 'p', 'tbl', 'p', 'tbl']


def test_a_single_grant_gets_no_spacing_paragraph():
    """The "except the last one" rule means one grant gets no spacer at all."""
    gen = _sectioned_generator()
    gen._fill_research_support(
        {'M2A': [_entry('M2A', title='Lonely Project Study', agency='NIH',
                        start_date='01/2019', end_date='Present')]},
        current_year=TEST_YEAR)

    assert _tags_under(gen, CURRENT) == ['tbl']


def test_each_grant_table_lands_after_its_own_section_header():
    """Every bucket's tables sit between its own header and the next one.

    Body order is the only thing that decides which WCM heading a grant appears
    under (review thread 3932451691 item 20).
    """
    gen = _sectioned_generator()
    gen._fill_research_support(
        {
            'M2A': [_entry('M2A', title='Current Project Study', agency='NIH',
                           start_date='01/2019', end_date='Present')],
            'M2B': [_entry('M2B', title='Completed Project Study', agency='NIH',
                           start_date='01/2010', end_date='12/2012')],
            'M2C': [_entry('M2C', title='Pending Project Study', agency='NIH',
                           start_date='01/2027')],
        },
        current_year=TEST_YEAR)

    assert _titles_under(gen, CURRENT) == ['Current Project Study']
    assert _titles_under(gen, COMPLETED) == ['Completed Project Study']
    assert _titles_under(gen, PENDING) == ['Pending Project Study']


@pytest.mark.parametrize('status, expected_code', [
    ('Not funded', 'M2C'),      # kept under Pending rather than dropped
    ('Under review', 'M2C'),
    ('Completed', 'M2B'),
    ('Awarded', None),          # no move: an award is not a rebucketing signal
    ('', None),
    (None, None),
])
def test_grant_status_rebucket_target_branches(status, expected_code):
    """Each branch of the status rule, including the no-move default.

    The rule the whole rebucketer is built on and it had no direct test
    (review thread 3932451691 item 20).
    """
    target, note = grant_status_rebucket_target(status)
    assert target == expected_code
    assert (note is not None) is (expected_code is not None)


def test_not_funded_grants_are_kept_under_pending_with_a_review_note():
    """"Not funded" is filed under Pending with a note, never silently dropped."""
    gen = _sectioned_generator(emit_comments=True)
    gen._fill_research_support(
        {'M2A': [_entry('M2A', title='Declined Project Study', agency='NIH',
                        status='Not funded', start_date='01/2026')]},
        current_year=TEST_YEAR)

    assert _titles_under(gen, PENDING) == ['Declined Project Study']
    notes = [c['text'] for c in gen._comments if c['author'] == 'Reclassification']
    assert notes and 'confirm whether to keep this entry on the CV' in notes[0]


def test_rebucketing_leaves_a_grant_alone_when_the_status_names_its_own_bucket():
    """A completed status on an M2B grant is not a move."""
    entry = _entry('M2B', title='Completed Project Study', status='Completed')
    current, completed, pending, messages = rebucket_grants_by_status([], [entry], [])

    assert (current, completed, pending) == ([], [entry], [])
    assert messages == []


def test_unsupported_rebucket_target_raises_a_named_error(monkeypatch):
    """An unrenderable bucket code fails with a domain error, not a bare KeyError.

    `bucket_lists` holds only M2B and M2C. Before the guard, a status rule
    returning anything else died on `bucket_lists[target]` naming neither the
    code nor the grant (thread 3932312407 item 2). Preventive: the real rule
    cannot return M2D today, so the rule is stubbed to prove the guard.

    The message identifies the grant by its position, never by its title: it
    reaches a traceback and from there the pod logs, and a grant title is CV
    content. That is this module's stated logging rule, and an exception is
    the one place it would be easy to break by accident.
    """
    monkeypatch.setattr(research_support, 'grant_status_rebucket_target',
                        lambda status: ('M2D', 'stub note') if status else (None, None))
    entry = _entry('M2A', title='Misrouted Project Study', status='Under review')

    with pytest.raises(UnsupportedRebucketTargetError) as excinfo:
        rebucket_grants_by_status([entry], [], [])

    message = str(excinfo.value)
    assert 'M2D' in message
    assert 'M2A' in message and '#0' in message
    assert 'Misrouted Project Study' not in message


def test_reclassify_past_m2a_grants_leaves_its_inputs_alone():
    """The pure classifier returns new lists rather than editing the caller's."""
    entry = _entry('M2A', title='Ended Project Study', end_date='06/2020')
    m2a = [entry]
    m2b = []

    current, completed, messages = reclassify_past_m2a_grants(m2a, m2b, TEST_YEAR)

    assert m2a == [entry] and m2b == []
    assert current == [] and completed == [entry]
    assert messages == ["  Reclassified to M2B: 'Ended Project Study...' (ended 2020)"]


# --- thread 3932312407 item 8: the rendering contract ---------------------------

_CONTRACT_FIELDS = {
    'agency': 'National Institutes of Health',
    'title': 'Metabolic Reprogramming in Heart Failure',
    'annual_direct_costs': '100000',
    'non_financial_support': 'Core facility access',
    'start_date': '07/2019',
    'end_date': '06/2021',
    'pi_name': 'Jane Smith',
    'pi_role': 'Principal Investigator',
    'percent_effort': '20%',
}

_CONTRACT_ROWS = [
    ('Award Source:', 'National Institutes of Health'),
    ('Project title:', 'Metabolic Reprogramming in Heart Failure'),
    ('Annual direct costs:', '$100,000'),
    ('Non-financial support:', 'Core facility access'),
    ('Duration of support:', '07/19-06/21'),
    ('Name of Principal Investigator:', 'Jane Smith'),
    ('Your role:', 'Principal Investigator'),
    ('Your percent (%) effort:', '20%'),
]


def test_grant_table_is_exactly_the_eight_wcm_rows_in_order():
    """The WCM grant block: eight rows, these labels, this order, these values.

    The row list is written inline in `_create_grant_table`, so a field change
    could add, drop or reorder a row of the WCM structure with nothing failing
    (thread 3932312407 item 8).
    """
    table = _generator()._create_grant_table(dict(_CONTRACT_FIELDS), 'M2A')

    assert len(table.rows) == 8
    assert _rows(table) == _CONTRACT_ROWS


def test_major_goals_is_the_ninth_row_and_only_when_present():
    """The optional ninth row appends after the eight; nothing else shifts."""
    fields = dict(_CONTRACT_FIELDS, major_goals='Aim 1: characterise the cohort')
    table = _generator()._create_grant_table(fields, 'M2A')

    assert len(table.rows) == 9
    assert _rows(table) == _CONTRACT_ROWS + [
        ('Major project goals:', 'Aim 1: characterise the cohort')]


def test_absent_values_render_as_empty_cells_not_missing_rows():
    """A sparse-but-admissible grant still renders all eight rows, some empty."""
    fields = {'title': 'Minimal Project Study', 'start_date': '01/2019'}
    table = _generator()._create_grant_table(fields, 'M2A')

    assert [label for label, _ in _rows(table)] == [label for label, _ in _CONTRACT_ROWS]
    assert _cells(table)['Non-financial support:'] == ''


def _fields_get_keys():
    """Every literal key this module reads off a `fields` mapping."""
    tree = ast.parse(Path(research_support.__file__).read_text(encoding='utf-8'))
    keys = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == 'get'
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == 'fields'
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            keys.add(node.args[0].value)
    return keys


def test_every_field_the_module_reads_is_declared_on_the_grant_record_type():
    """A `fields.get('x')` that `GrantFields` does not name makes the diagnostic lie.

    `CONSUMED_GRANT_FIELDS` is derived from `GrantFields` and drives the
    debug line that reports which stage-4 keys reached no row. A slot added to
    `_create_grant_table` without its key added to the record type would leave
    that key reported as dropped forever -- the diagnostic reporting a loss
    that is not happening, which is worse than no diagnostic. Nothing enforced
    the pairing before; this does, off the module's own source.
    """
    keys = _fields_get_keys()

    assert keys, 'the AST walk found no fields.get(...) calls at all'
    assert keys <= research_support.CONSUMED_GRANT_FIELDS, (
        'read but not declared on GrantFields: '
        f'{sorted(keys - research_support.CONSUMED_GRANT_FIELDS)}')


def test_the_grant_record_type_declares_nothing_the_module_never_reads():
    """The other direction: a declared key no reader wants is dead contract.

    All 24 keys, `status` included, are reached through a `fields.get(...)` in
    this module -- `status` from the bucket rules rather than from a rendered
    row. A key left on the record type after its reader is deleted would go on
    suppressing that key's line in the unconsumed-fields diagnostic, silently.
    """
    declared_but_unread = research_support.CONSUMED_GRANT_FIELDS - _fields_get_keys()

    assert declared_but_unread == set(), sorted(declared_but_unread)
    assert len(research_support.CONSUMED_GRANT_FIELDS) == 24
