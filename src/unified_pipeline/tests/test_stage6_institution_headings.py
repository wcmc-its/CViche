"""#985: an institution sub-heading fills the empty cells of the rows under it.

`stage6/institution_headings.py` is pure, so every case below is entries in,
entries and groups out, with stage 4's own weak heading-shape test passed in
as stage 6 passes it. The wiring (that `_group_entries_by_code` runs the pass
on the routed entries) is pinned in `test_stage6_render_drops.py`, next to the
other tests of that method; the last test here renders a whole document.

Every value is invented. The shapes are the ones the #985 census found in the
stored runs: an institution line over committees (WZSMUO 331), a society line
over its offices (FLYBMX 298-341), a mentee list's institution line (WZSMUO
166-198), an unextracted name line (NJIKGI 212), and the near-misses the pass
must leave alone.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_institution_headings.py -p no:cacheprovider
"""
import copy
import json
from collections import defaultdict

import pytest
from docx import Document

from unified_pipeline import stage_6_word_template as stage6
from unified_pipeline.stage4.context_headings import _is_heading_shaped
from unified_pipeline.stage6 import institution_headings as ih

SERVICE = ('Institutional Service',)


def _entry(code: str, text: str, el: int, hierarchy: tuple[str, ...] = SERVICE, **fields) -> dict:
    return {'taxonomy_code': code, 'text': text, 'element_idx_start': el, 'element_idx_end': el,
            'hierarchy': list(hierarchy), 'extracted_fields': dict(fields)}


def _committee(name: str, el: int, code: str = 'P', **fields) -> dict:
    """A dated committee row, as a CV writes one: never heading-shaped."""
    return _entry(code, f'{name}, 2001-2003', el, committee_name=name, start_date='2001', **fields)


def _office(role: str, el: int, code: str = 'Q1', **fields) -> dict:
    """A dated office in a society, as a CV writes one: a role and its dates."""
    return _entry(code, f'{role}, 2001-2003', el, role=role, start_date='2001', end_date='2003', **fields)


def _at(entry: dict, hierarchy: tuple[str, ...]) -> dict:
    return {**entry, 'hierarchy': list(hierarchy)}


def _coded(*entries: dict) -> list[tuple[dict, str]]:
    return [(entry, entry['taxonomy_code']) for entry in entries]


def _consume(*entries: dict) -> tuple[dict[str, list[dict]], list[dict]]:
    coded = _coded(*entries)
    groups: dict[str, list[dict]] = defaultdict(list)
    for entry, code in coded:
        groups[code].append(entry)
    warnings: list[dict] = []
    ih.consume_institution_headings(coded, groups, _is_heading_shaped, warnings=warnings)
    return groups, warnings


def _filled(entry: dict, key: str) -> object:
    return entry['extracted_fields'].get(key)


# ----------------------------------------------------------------- the shapes

def test_an_institution_line_fills_the_committees_under_it_and_is_consumed() -> None:
    heading = _entry('P', 'Example University', 10, institution='Example University')
    first, second = _committee('Admissions Committee', 11), _committee('Space Task Force', 12)
    groups, _ = _consume(heading, first, second)
    assert _filled(first, 'institution') == _filled(second, 'institution') == 'Example University'
    assert groups['P'] == [first, second]


def test_a_society_line_fills_the_organization_of_its_offices_and_memberships() -> None:
    heading = _entry('I', 'Northfield Society of Invented Pharmacy', 20,
                     organization='Northfield Society of Invented Pharmacy')
    office = _entry('Q1', 'President, 2007-2009', 21, role='President', start_date='2007', end_date='2009')
    member = _entry('I', 'Member, 2007-2018', 22, membership_type='Member', start_date='2007', end_date='2018')
    groups, _ = _consume(heading, office, member)
    assert _filled(office, 'organization') == _filled(member, 'organization') == heading['text']
    assert groups['I'] == [member]


def test_a_mentee_list_institution_line_fills_the_mentee_institution() -> None:
    hierarchy = ('Graduate Student Mentorship', 'Thesis Advisor')
    heading = _entry('N3B', 'Example University', 30, hierarchy, site_position='Example University')
    past = _entry('N3B', 'A. Student, Invented Science Program, Ph.D. 2002', 31, hierarchy,
                  mentee_name='A. Student', site_position='Invented Science Program')
    current = _entry('N3A', 'B. Student, Invented Science Program', 32, hierarchy,
                     mentee_name='B. Student', site_position='Invented Science Program')
    groups, _ = _consume(heading, past, current)
    assert _filled(past, 'institution') == _filled(current, 'institution') == 'Example University'
    assert groups['N3B'] == [past]


def test_a_trailing_colon_is_not_part_of_the_name() -> None:
    heading = _entry('K2', 'Example University:', 40, institution='Example University')
    rows = [_entry('N3B', f'Fellow {n}, MD', 41 + n, mentee_name=f'Fellow {n}') for n in range(2)]
    groups, _ = _consume(heading, *rows)
    assert [_filled(row, 'institution') for row in rows] == ['Example University'] * 2
    assert 'K2' not in groups


def test_rows_on_a_sub_level_of_the_heading_are_its_rows() -> None:
    heading = _entry('P', 'Example University', 50, ('Administrative Duties',),
                     institution='Example University')
    programs = ('Administrative Duties', 'Example University Programs')
    committees = ('Administrative Duties', 'Example University Committees')
    rows = [_entry('O', 'Director, Combined Program, 1997-2001', 52, programs, leadership_role='Director'),
            _at(_committee('Steering Committee', 53), programs),
            _at(_committee('Search Committee', 56), committees),
            _at(_committee('Planning Committee', 57), committees)]
    _consume(heading, *rows)
    assert [_filled(row, 'institution') for row in rows] == ['Example University'] * 4


def test_an_unextracted_name_line_is_a_heading_when_a_row_under_it_names_it() -> None:
    heading = _entry('Q2', 'Exampleton', 60)
    rows = [_committee('Provost committee on work life', 62),
            _committee('Exampleton advisory board', 64, institution='Exampleton'),
            _committee('Faculty welfare subcommittee', 66)]
    groups, _ = _consume(heading, *rows)
    assert [_filled(row, 'institution') for row in rows] == ['Exampleton'] * 3
    assert 'Q2' not in groups


def test_a_row_naming_the_heading_institution_or_a_unit_of_it_keeps_it_and_the_run_goes_on() -> None:
    heading = _entry('Q2', 'Exampleton', 70, organization='Exampleton')
    unit = _committee('Exampleton Cancer Center search committee', 72, institution='Exampleton Cancer Center')
    rows = [_committee('Fernhollow Panels', 71), unit, _committee('Brindle Group', 73)]
    _consume(heading, *rows)
    assert [_filled(row, 'institution') for row in rows] == ['Exampleton', 'Exampleton Cancer Center', 'Exampleton']
    society = _entry('I', 'Example Society of Imaginary Medicine (ESIM)', 80,
                     organization='Example Society of Imaginary Medicine (ESIM)')
    rows = [_committee('Program Committee', 81, code='Q2'),
            _committee('Other ESIM Roles', 82, code='Q2', organization='ESIM'),
            _committee('Audit Committee', 83, code='Q2')]
    _consume(society, *rows)
    assert [_filled(row, 'organization') for row in rows] == [society['text'], 'ESIM', society['text']]


def test_an_alias_and_reordered_connectives_still_read_as_the_name() -> None:
    """WZSMUO 354's shape: stage 4 reorders a name with its parenthesised
    alias, and drops or adds a connective; the text is still only the name,
    and the rows take stage 4's spelling of it, which has the same words."""
    heading = _entry('P', 'Examplefield (University of Examplefield, UEF)', 10,
                     institution='Examplefield University (UEF)')
    rows = [_committee('Admissions Committee', 11), _committee('Search Committee', 12)]
    _consume(heading, *rows)
    assert [_filled(row, 'institution') for row in rows] == ['Examplefield University (UEF)'] * 2


@pytest.mark.parametrize('text, fields, filled', [
    # AOJEQD 71: a lettered sub-heading; stage 4 left the letter out.
    ('A. Example Division, Example University', {'institution': 'Example Division, Example University'},
     'Example Division, Example University'),
    # SDEBQJ 346: a heading in capitals; stage 4 wrote the name as a name.
    ('EXAMPLE DEPARTMENT (Invented Sciences)', {'institution': 'Example Department of Invented Sciences'},
     'Example Department of Invented Sciences'),
    # A field with a word the text lacks is not the text's spelling.
    ('example university', {'institution': 'Example University Hospital'}, 'example university'),
])
def test_the_rows_take_stage_4s_spelling_of_the_name_when_it_has_the_texts_words(
        text: str, fields: dict, filled: str) -> None:
    rows = [_committee('Admissions Committee', 11), _committee('Search Committee', 12)]
    _consume(_entry('P', text, 10, **fields), *rows)
    assert [_filled(row, 'institution') for row in rows] == [filled] * 2


def test_restating_and_blank_fields_do_not_make_a_heading_a_record() -> None:
    """A heading stage 5c formatted (WZSMUO 132's `formatted_text`), or with a
    blank field stage 4 left, is still only a name."""
    heading = _entry('P', 'Example University', 10, institution='Example University',
                     formatted_text='Example University', formatting_source='stage_5c_llm',
                     role='  ', notes=[], extra={})
    rows = [_committee('Admissions Committee', 11), _committee('Search Committee', 12)]
    _consume(heading, *rows)
    assert [_filled(row, 'institution') for row in rows] == ['Example University'] * 2


def test_fanned_out_records_of_one_element_are_one_run() -> None:
    """WYMVGU 930: stage 6's fan-out gives each record of an entry its own
    child, all on the entry's element; a row may also span several elements."""
    heading = _entry('I', 'Example Section of Imaginary Research', 930,
                     organization='Example Section of Imaginary Research')
    rows = [_office('Session co-chair', 931), _office('Poster judge', 931),
            {**_office('Vice-President', 932), 'element_idx_end': 934}, _office('President', 935)]
    _consume(heading, *rows)
    assert [_filled(row, 'organization') for row in rows] == [heading['text']] * 4


def test_rows_of_one_element_on_a_sub_level_set_no_negative_spacing() -> None:
    """The first step measured can be between two fanned-out records of one
    element; it counts as adjacent, so the next adjacent row stays in the run."""
    heading = _entry('P', 'Example University', 50, ('Duties',), institution='Example University')
    level = ('Duties', 'Example University Committees')
    rows = [_at(_committee('Admissions Committee', 52), level), _at(_committee('Search Committee', 52), level),
            _at(_committee('Planning Committee', 53), level)]
    _consume(heading, *rows)
    assert [_filled(row, 'institution') for row in rows] == ['Example University'] * 3


def test_a_mentee_list_run_ends_at_a_committee_row() -> None:
    hierarchy = ('Mentoring',)
    heading = _entry('N3B', 'Example University', 30, hierarchy, site_position='Example University')
    mentees = [_entry('N3B', f'Student {n}, Invented Science Program', 31 + n, hierarchy, mentee_name=f'Student {n}')
               for n in range(2)]
    after = [_at(_committee('Admissions Committee', 33), hierarchy), _at(_committee('Search Committee', 34), hierarchy)]
    _consume(heading, *mentees, *after)
    assert [_filled(row, 'institution') for row in (*mentees, *after)] == ['Example University'] * 2 + [None] * 2


def test_the_heading_bound_is_stage_4s() -> None:
    from unified_pipeline.stage4 import context_headings
    assert ih.MAX_HEADING_CHARS == context_headings.MAX_HEADING_CHARS


def test_an_unextracted_dated_line_is_not_a_heading_even_when_a_row_names_it() -> None:
    _unchanged(_entry('Q2', 'Exampleton 2005', 60), _committee('Provost committee', 62),
               _committee('Exampleton advisory board', 64, institution='Exampleton'),
               _committee('Faculty welfare subcommittee', 66))


def test_entries_without_integer_element_indexes_are_still_read() -> None:
    for index in ('table_3', None):
        heading = {**_entry('P', 'Example University', 10, institution='Example University'),
                   'element_idx_start': index, 'element_idx_end': index}
        rows = [{**_committee('Admissions Committee', 0), 'element_idx_start': None, 'element_idx_end': None},
                {**_committee('Search Committee', 0), 'element_idx_start': 'table_4', 'element_idx_end': 'table_4'}]
        _consume(heading, *rows)
        assert [_filled(row, 'institution') for row in rows] == ['Example University'] * 2


def test_a_leadership_row_naming_its_organization_keeps_it() -> None:
    """Leadership prints `institution` or `organization`, so an O row holding
    either names its own; a blank one is not a name."""
    heading = _entry('P', 'Example University', 10, institution='Example University')
    rows = [_committee('Admissions Committee', 11),
            _entry('O', 'Chair, Example University Senate, 2001', 12, leadership_role='Chair',
                   organization='Example University Senate'),
            _committee('Search Committee', 13, institution='   ')]
    _consume(heading, *rows)
    assert [_filled(row, 'institution') for row in rows] == ['Example University', None, 'Example University']
    assert _filled(rows[1], 'organization') == 'Example University Senate'


def test_a_long_name_up_to_the_bound_is_a_heading_and_past_it_is_not() -> None:
    def outcome(name: str) -> list[object]:
        rows = [_committee('Admissions Committee', 11), _committee('Search Committee', 12)]
        _consume(_entry('P', name, 10, institution=name), *rows)
        return [_filled(row, 'institution') for row in rows]

    at_bound = 'Example ' + 'x' * (ih.MAX_HEADING_CHARS - len('Example '))
    assert outcome(at_bound) == [at_bound] * 2
    assert outcome(at_bound + 'y') == [None] * 2


def test_consuming_without_a_warnings_list_still_fills() -> None:
    heading = _entry('P', 'Example University', 10, institution='Example University')
    rows = [_committee('Admissions Committee', 11), _committee('Search Committee', 12)]
    groups = {'P': [heading, *rows]}
    ih.consume_institution_headings(_coded(heading, *rows), groups, _is_heading_shaped)
    assert groups == {'P': rows} and _filled(rows[0], 'institution') == 'Example University'


def test_the_warning_names_element_indexes_and_no_text() -> None:
    heading = _entry('P', 'Example University', 10, institution='Example University')
    _, warnings = _consume(heading, _committee('Admissions Committee', 11), _committee('Space Task Force', 12))
    assert warnings == [{
        'check': ih.CONSUMED_HEADING_CHECK, 'code': None, 'section': None,
        'message': 'institution sub-heading moved into the rows under it: 1 heading, 2 rows filled',
        'evidence': ['element_idx_start 10 filled 11, 12'], 'severity': 'INFO',
        ih.CONSUMED_HEADINGS_KEY: [[10, [11, 12]]]}]
    assert ih.heading_fills(json.loads(json.dumps(warnings))) == {10: [11, 12]}


@pytest.mark.parametrize('warnings', [
    [],
    [{'check': 'another_check', ih.CONSUMED_HEADINGS_KEY: [[10, [11]]]}],
    [{'check': ih.CONSUMED_HEADING_CHECK, ih.CONSUMED_HEADINGS_KEY: [[10, 11], [12], 'x']}],
    ['not a record', {'check': ih.CONSUMED_HEADING_CHECK}],
    [{'check': ih.CONSUMED_HEADING_CHECK, ih.CONSUMED_HEADINGS_KEY: 5}],
    [{'check': ih.CONSUMED_HEADING_CHECK, ih.CONSUMED_HEADINGS_KEY: True}],
    [{'check': ih.CONSUMED_HEADING_CHECK, ih.CONSUMED_HEADINGS_KEY: {'10': [11]}}],
    [{'check': ih.CONSUMED_HEADING_CHECK, ih.CONSUMED_HEADINGS_KEY: '10,11'}],
    [{'check': ih.CONSUMED_HEADING_CHECK, ih.CONSUMED_HEADINGS_KEY: [[[10], [11]]]}],
    [{'check': ih.CONSUMED_HEADING_CHECK, ih.CONSUMED_HEADINGS_KEY: [[10, [[11]]]]}],
    [{'check': ih.CONSUMED_HEADING_CHECK, ih.CONSUMED_HEADINGS_KEY: [[10, [11, {'el': 12}]]]}],
    [{'check': ih.CONSUMED_HEADING_CHECK, ih.CONSUMED_HEADINGS_KEY: [], ih.KEPT_HEADINGS_KEY: 5}],
])
def test_heading_fills_reads_nothing_from_other_or_malformed_records(warnings: list) -> None:
    assert ih.heading_fills(warnings) == {} and ih.consumed_headings(warnings) == frozenset()


def test_heading_fills_reads_the_well_formed_pairs_beside_malformed_ones() -> None:
    """A malformed pair is skipped, not the record: the doctor keeps every
    fill it can read."""
    warnings = [{'check': ih.CONSUMED_HEADING_CHECK,
                 ih.CONSUMED_HEADINGS_KEY: [[[10], [11]], [20, [21]], [30, [[31]]]],
                 ih.KEPT_HEADINGS_KEY: [[{'el': 40}, [41]], [50, [51, 52]]]}]
    assert ih.heading_fills(warnings) == {20: [21], 50: [51, 52]}
    assert ih.consumed_headings(warnings) == frozenset({20})


def test_a_filled_row_gets_its_own_fields_dict() -> None:
    shared = {'committee_name': 'Admissions Committee'}
    first = {**_committee('Admissions Committee', 11), 'extracted_fields': shared}
    second = {**_committee('Admissions Committee', 12), 'extracted_fields': shared}
    _consume(_entry('P', 'Example University', 10, institution='Example University'), first, second)
    assert shared == {'committee_name': 'Admissions Committee'}
    assert first['extracted_fields'] is not second['extracted_fields']


def test_a_heading_that_is_its_codes_only_entry_leaves_no_empty_group() -> None:
    groups, _ = _consume(_entry('Q2', 'Example Medical Group', 10, organization='Example Medical Group'),
                         _committee('Board Committee', 11), _committee('Strategy Committee', 12))
    assert sorted(groups) == ['P']


# ------------------------------------------------------------- the near-misses

def _unchanged(*entries: dict) -> None:
    before = copy.deepcopy(list(entries))
    groups, warnings = _consume(*entries)
    assert list(entries) == before and warnings == []
    assert sum(len(group) for group in groups.values()) == len(entries)


def test_a_one_line_record_with_an_institution_and_a_date_is_not_a_heading() -> None:
    _unchanged(_entry('I', 'Example Society of Imaginary Surgery 2002-2018', 10,
                      organization='Example Society of Imaginary Surgery', start_date='2002'),
               _entry('Q1', 'President, 2014-2015', 11, role='President', start_date='2014'),
               _entry('Q1', 'Treasurer, 2010-2012', 12, role='Treasurer', start_date='2010'))


def test_rows_that_each_name_their_own_institution_are_left_alone() -> None:
    _unchanged(_entry('P', 'Example University', 10, institution='Example University'),
               _committee('Cancer Committee, Westbrook Hospital', 11, institution='Westbrook Hospital'),
               _committee('IRB, Eastfield Clinic', 12, institution='Eastfield Clinic'))


def test_a_heading_over_one_row_is_left_alone() -> None:
    _unchanged(_entry('P', 'Example University', 10, institution='Example University'),
               _committee('Admissions Committee', 11))


@pytest.mark.parametrize('flag', ['is_fragment', 'is_duplicate'])
@pytest.mark.parametrize('line', [
    _entry('P', 'Other Example Hospital', 123, institution='Other Example Hospital'),
    _committee('Other Example Hospital guidelines', 123),
])
def test_a_line_stage_3b_dropped_ends_the_run_before_it(line: dict, flag: str) -> None:
    """VQFSDI 126: an institution line 3b flagged as a fragment of the row
    above. Stage 4 skips it, so the run must not reach the rows below it."""
    heading = _entry('P', 'Example University', 120, institution='Example University')
    first, second = _committee('Admissions Committee', 121), _committee('Search Committee', 122)
    fragment = {**copy.deepcopy(line), flag: True}
    after = [_committee('Guidelines Committee', 124), _committee('Fundraising Committee', 125)]
    _consume(heading, first, second, fragment, *after)
    assert [_filled(row, 'institution') for row in (first, second, *after)] == ['Example University'] * 2 + [None] * 2


def test_a_line_stage_4_dropped_ends_the_run_at_the_gap_it_leaves() -> None:
    """VQFSDI 126: the fragment is not in stage 4's output at all, only the
    element index it held is missing between two rows that were adjacent."""
    heading = _entry('P', 'Example University', 120, institution='Example University')
    first, second = _committee('Admissions Committee', 121), _committee('Search Committee', 122)
    after = [_committee('Guidelines Committee', 124), _committee('Fundraising Committee', 125)]
    _consume(heading, first, second, *after)
    assert [_filled(row, 'institution') for row in (first, second, *after)] == ['Example University'] * 2 + [None] * 2


def test_rows_a_blank_line_apart_are_one_run() -> None:
    heading = _entry('Q2', 'Exampleton', 172, organization='Exampleton')
    rows = [_committee(f'Committee {n}', 174 + 2 * n, code='Q2') for n in range(3)]
    _consume(heading, *rows)
    assert [_filled(row, 'organization') for row in rows] == ['Exampleton'] * 3


def test_a_heading_whose_next_entries_sit_under_another_leaf_is_left_alone() -> None:
    _unchanged(_entry('P', 'Example University', 10, ('Administrative Duties', 'Programs'),
                      institution='Example University'),
               _committee('Admissions Committee', 12) | {'hierarchy': ['Administrative Duties', 'Committees']},
               _committee('Search Committee', 13) | {'hierarchy': ['Administrative Duties', 'Committees']})


@pytest.mark.parametrize('code, fields', [
    ('K1', {'course_title': 'Neurobiology', 'role': 'Lecturer'}),
    ('R', {'title': 'Invited talk', 'date': '2001'}),
    ('D1', {'title': 'Professor', 'start_date': '2001'}),
])
def test_a_row_whose_renderer_prints_no_filled_institution_gets_nothing(code: str, fields: dict) -> None:
    _unchanged(_entry('P', 'Example University', 10, institution='Example University'),
               _entry(code, 'A row of another kind, 2001', 11, **fields),
               _committee('Admissions Committee', 12), _committee('Search Committee', 13))


@pytest.mark.parametrize('name', ['Medical Center', 'University', 'School of Medicine', 'Board of Directors',
                                  'Department of Medicine', 'College of Pharmacy', 'Health System'])
def test_a_name_of_a_kind_of_institution_is_not_a_heading(name: str) -> None:
    _unchanged(_entry('P', name, 10, institution=name),
               _committee('Admissions Committee', 11), _committee('Search Committee', 12))


def test_an_appointment_line_is_never_a_heading() -> None:
    _unchanged(_entry('D3', 'Example University', 10, organization='Example University'),
               _entry('O', 'Director, Example Program', 11, leadership_role='Director'),
               _entry('O', 'Chair, Example Council', 12, leadership_role='Chair'))


def test_a_row_naming_another_institution_ends_the_run() -> None:
    heading = _entry('I', 'Example Society of Imaginary Medicine', 60,
                     organization='Example Society of Imaginary Medicine')
    rows = [_committee('Research Committee', 61, code='Q2'), _committee('Program Committee', 62, code='Q2'),
            _committee('Liaison Committee, Westbrook Academy', 63, code='Q2', organization='Westbrook Academy'),
            _committee('Finance Committee', 64, code='Q2')]
    _consume(heading, *rows)
    assert [_filled(row, 'organization') for row in rows] == [heading['text']] * 2 + ['Westbrook Academy', None]


@pytest.mark.parametrize('ender', [
    _entry('Q2', 'Council on Invented Affairs', 63, committee_name='Council on Invented Affairs'),
    _entry('T', 'Meeting Location', 63, description='Meeting Location'),
    _committee('Hospital Committee', 63),
    _entry('Q2', 'A row stage 4 extracted nothing from', 63),
])
def test_a_sub_heading_another_family_t_or_an_unextracted_row_ends_the_run(ender: dict) -> None:
    heading = _entry('I', 'Example Society of Imaginary Medicine', 60,
                     organization='Example Society of Imaginary Medicine')
    rows = [_committee('Research Committee', 61, code='Q2'), _committee('Program Committee', 62, code='Q2')]
    after = [_committee('Finance Committee', 64, code='Q2'), _committee('Audit Committee', 65, code='Q2')]
    _consume(heading, *rows, ender, *after)
    assert [_filled(row, 'organization') for row in (*rows, *after)] == [heading['text']] * 2 + [None] * 2


def test_the_next_heading_ends_the_run_and_starts_its_own() -> None:
    """The next heading here is not heading-shaped (a comma), so only being a
    heading ends the first run."""
    first = _entry('Q2', 'Example Society of Imaginary Medicine', 60,
                   organization='Example Society of Imaginary Medicine')
    second = _entry('Q2', 'Westbrook Academy, Eastfield', 63, organization='Westbrook Academy, Eastfield')
    rows = [_committee('Research Committee', 61, code='Q2'), _committee('Program Committee', 62, code='Q2')]
    after = [_committee('Finance Committee', 64, code='Q2'), _committee('Audit Committee', 65, code='Q2')]
    groups, warnings = _consume(first, *rows, second, *after)
    assert [_filled(row, 'organization') for row in (*rows, *after)] == [first['text']] * 2 + [second['text']] * 2
    assert groups['Q2'] == [*rows, *after]
    assert warnings[0]['evidence'] == ['element_idx_start 60 filled 61, 62', 'element_idx_start 63 filled 64, 65']
    assert warnings[0][ih.CONSUMED_HEADINGS_KEY] == [[60, [61, 62]], [63, [64, 65]]]
    assert warnings[0]['message'].endswith(': 2 headings, 4 rows filled')


def test_a_unit_heading_under_a_heading_starts_its_own_run() -> None:
    """A sub-heading naming a unit of the heading's institution is a heading:
    the first run ends there, and the rows under it take the unit's name."""
    first = _entry('Q2', 'Exampleton', 60, organization='Exampleton')
    unit = _entry('P', 'Exampleton Cancer Center, Main Campus', 63,
                  institution='Exampleton Cancer Center, Main Campus')
    rows = [_committee('Fernhollow panels', 61), _committee('Brindle group', 62)]
    after = [_committee('Search committee', 64), _committee('Program committee', 65)]
    _, warnings = _consume(first, *rows, unit, *after)
    assert [_filled(row, 'institution') for row in (*rows, *after)] == ['Exampleton'] * 2 + [unit['text']] * 2
    assert warnings[0]['evidence'] == ['element_idx_start 60 filled 61, 62', 'element_idx_start 63 filled 64, 65']


def test_a_row_naming_a_numbered_part_of_the_heading_ends_the_run() -> None:
    """A row's number is a word of its name: "Example Society 2" is not
    "Example Society of Imaginary Medicine", so the run stops there."""
    heading = _entry('I', 'Example Society of Imaginary Medicine', 60,
                     organization='Example Society of Imaginary Medicine')
    rows = [_committee('Research committee', 61, code='Q2'), _committee('Program committee', 62, code='Q2'),
            _committee('Liaison committee', 63, code='Q2', organization='Example Society 2'),
            _committee('Finance committee', 64, code='Q2')]
    _consume(heading, *rows)
    assert [_filled(row, 'organization') for row in rows] == [heading['text']] * 2 + ['Example Society 2', None]


def test_an_unextracted_name_line_no_row_names_is_left_alone() -> None:
    _unchanged(_entry('Q2', 'Exampleton', 60), _committee('Provost committee', 62),
               _committee('Faculty welfare subcommittee', 64))


def test_a_cell_of_its_own_row_is_not_a_heading() -> None:
    """HVLDGS 1878: stage 2 split a table row's first cell off as an entry."""
    _unchanged(_entry('Q2', 'Example Science Fair', 10, organization='Example Science Fair'),
               _committee('Example Science Fair\tFair Contact', 10),
               _committee('Fair Contact and Organizer', 11))


def test_a_heading_with_more_than_its_name_is_not_a_heading() -> None:
    """Even with a row under it naming the same institution, which would
    corroborate a line stage 4 extracted nothing from."""
    _unchanged(_entry('P', 'Example University', 10, institution='Example University', role='Member'),
               _committee('Admissions Committee', 11), _committee('Search Committee', 12),
               _committee('Example University Senate', 13, institution='Example University'))


@pytest.mark.parametrize('text', [
    'Example University\tMain Campus', 'Example University | Main Campus',
    'Example University\nMain Campus', 'Example University (Main Campus',
])
def test_a_line_with_a_tab_pipe_break_or_open_parenthesis_is_not_a_name(text: str) -> None:
    _unchanged(_entry('P', text, 10, institution=text),
               _committee('Admissions Committee', 11), _committee('Search Committee', 12))


@pytest.mark.parametrize('code', ['T', 'D3'])
def test_a_t_or_appointment_line_is_not_a_heading_even_when_a_row_names_it(code: str) -> None:
    """T is stage 4's stamp's (#1047); an appointment is positions'. Neither
    with the name extracted nor without it."""
    for fields in ({'institution': 'Exampleton'}, {}):
        _unchanged(_entry(code, 'Exampleton', 10, **fields),
                   _committee('Admissions Committee', 11), _committee('Search Committee', 12),
                   _committee('Exampleton advisory board', 13, institution='Exampleton'))


def test_an_unextracted_name_of_a_kind_of_institution_is_not_a_heading() -> None:
    _unchanged(_entry('Q2', 'Medical Center', 10), _committee('Admissions Committee', 11),
               _committee('Search Committee', 12), _committee('Medical Center board', 13, institution='Medical Center'))


def test_a_name_field_holding_a_list_is_not_a_name() -> None:
    _unchanged(_entry('P', 'Example University', 10, institution=['Example University']),
               _committee('Admissions Committee', 11), _committee('Search Committee', 12))


def test_a_row_with_no_fields_dict_or_a_placeholder_name_ends_the_run() -> None:
    heading = _entry('P', 'Example University', 10, institution='Example University')
    rows = [_committee('Admissions Committee', 11), _committee('Search Committee', 12)]
    for ender in ({**_committee('Space Committee', 13), 'extracted_fields': None},
                  _committee('Space Committee', 13, institution='-')):
        after = [_committee('Planning Committee', 14), _committee('Budget Committee', 15)]
        _consume(copy.deepcopy(heading), *copy.deepcopy(rows), ender, *after)
        assert [_filled(row, 'institution') for row in after] == [None, None]


def test_a_text_with_words_its_name_fields_lack_is_not_a_heading() -> None:
    _unchanged(_entry('Q1', 'Other Example Leadership Roles', 10, organization='Example'),
               _entry('Q1', 'Chair, 2010-2011', 11, role='Chair'),
               _entry('Q1', 'Vice Chair, 2009-2010', 12, role='Vice Chair'))


SOCIETY = 'Example Society of Invented Care'


def _society(el: int = 10) -> dict:
    return _entry('I', SOCIETY, el, organization=SOCIETY)


def _member(el: int, kind: str = 'Member', **fields) -> dict:
    """A dated membership line under a society line, with no organization of its own."""
    return _entry('I', f'{kind}, 2001-2005', el, membership_type=kind, start_date='2001', end_date='2005', **fields)


# The rows a renderer prints its own text for while the filled field is blank
# (O with no role, `leadership.py`; Q2 with no committee name and Q1 with no
# organization, `service.py`; I with no organization, `memberships.py`), each
# with a heading of the same family and the two plain rows that make it a run.
_FALLBACK_ROWS = {
    'O': (_entry('P', 'Example University', 10, institution='Example University'),
          _entry('O', 'Fernhollow Program Head 2004-2006', 12, start_date='2004', end_date='2006'),
          [_entry('O', f'Director, {name} Program, 2001-2003', el, leadership_role=f'Director, {name} Program',
                  start_date='2001', end_date='2003') for el, name in ((11, 'Example'), (13, 'Invented'))]),
    'Q2': (_society(), _entry('Q2', 'Member, Fernhollow Steering Group, 2010-2012', 12, role='Member',
                              start_date='2010', end_date='2012'),
           [_committee('Program Committee', 11, code='Q2', end_date='2003'),
            _committee('Audit Committee', 13, code='Q2', end_date='2003')]),
    'Q1': (_society(), _office('Treasurer', 12) | {'text': 'Treasurer, Fernhollow Chapter, 2001-2003'},
           [_office('President', 11), _office('Secretary', 13)]),
    'I': (_society(), _entry('I', 'Fellow, Fernhollow Section, 2006-2010', 12, membership_type='Fellow',
                             start_date='2006', end_date='2010'),
          [_member(11), _entry('I', 'Fellow, 2010-2015', 13, membership_type='Fellow', start_date='2010',
                               end_date='2015')]),
}


@pytest.mark.parametrize('code', sorted(_FALLBACK_ROWS))
def test_a_row_whose_renderer_prints_its_own_text_in_the_blank_cell_is_not_filled(code: str) -> None:
    heading, row, plain = copy.deepcopy(_FALLBACK_ROWS[code])
    _consume(heading, plain[0], row, plain[1])
    assert _filled(row, ih.FILL_FIELD_BY_CODE[code]) is None
    assert [_filled(other, ih.FILL_FIELD_BY_CODE[code]) for other in plain] == [heading['text']] * 2


@pytest.mark.parametrize('heading, row', [
    # memberships prints an organization stage 4 filed under an alias
    (_society(), _member(12, institution='Westbrook Academy')),
    (_society(), _member(12, society='Westbrook Academy')),
    (_society(), _member(12, organization_name='Westbrook Academy')),
    # Q1 prints the program or journal a rerouted row names (`EXTRAMURAL_ORGANIZATION_FIELDS`)
    (_society(), _office('Director', 12, program_name='Westbrook Fellowship Program')),
    (_society(), _office('Editor', 12, journal_name='Westbrook Journal')),
    # a value that is not a string is still the row's own
    (_society(), _committee('Liaison Committee', 12, code='Q2', end_date='2003',
                            organization=['Westbrook Academy', 'Eastfield Board'])),
    (_entry('P', 'Example University', 10, institution='Example University'),
     _committee('Board', 12, institution={'name': 'Westbrook Hospital'})),
    # a name its renderer does not print still says the row is another institution's
    (_entry('P', 'Example University', 10, institution='Example University'),
     _committee('Board', 12, organization='Westbrook Hospital')),
])
def test_a_row_naming_its_own_organization_under_any_field_keeps_it_and_ends_the_run(heading: dict, row: dict) -> None:
    code = row['taxonomy_code']
    plain = {'I': _member, 'Q1': lambda el: _office('President', el), 'Q2': lambda el: _committee('Audit Committee', el, code='Q2', end_date='2003'),
             'P': lambda el: _committee('Search Committee', el)}[code]
    before = copy.deepcopy(row['extracted_fields'])
    rows = [plain(11), row, plain(13), plain(14)]
    _consume(heading, *rows)
    assert row['extracted_fields'] == before
    assert [_filled(other, ih.FILL_FIELD_BY_CODE[code]) for other in rows[2:]] == [None, None]


@pytest.mark.parametrize('row', [
    # a list of records under the committee name: each record prints its own institution
    _entry('P', 'Fernhollow Panel 2001-2002; Brindle Panel 2003-2004', 11,
           committee_name=[{'committee_name': 'Fernhollow Panel', 'start_date': '2001'},
                           {'committee_name': 'Brindle Panel', 'start_date': '2003'}]),
    # a block the renderer re-parses one row per line
    _entry('P', 'Fernhollow Panel, 2001-2002\nBrindle Panel, 2003-2004\nOstrava Panel, 2005', 11,
           role='Member', start_date='2001'),
    _entry('O', 'Director, Fernhollow, 2001\nDirector, Brindle, 2002\nDirector, Ostrava, 2003', 11,
           leadership_role='Director', start_date='2001'),
    # a role-only row: P has no committee name to append the institution to
    _entry('P', 'Quality Coordinator, 1999-2003', 11, role='Quality Coordinator',
           start_date='1999', end_date='2003'),
])
def test_a_heading_over_rows_that_print_no_entry_level_institution_is_left_alone(row: dict) -> None:
    second = {**copy.deepcopy(row), 'element_idx_start': 12, 'element_idx_end': 12}
    _unchanged(_entry('P', 'Example University', 10, institution='Example University'), row, second)


@pytest.mark.parametrize('code', ['G', 'H'])
def test_a_line_of_a_code_whose_bare_name_is_a_record_is_never_a_heading(code: str) -> None:
    """FVQANK 378: a hospital affiliation that is only a name is a whole
    Other Institutional Affiliations row."""
    _unchanged(_entry(code, 'Example Medical Group', 10, organization='Example Medical Group'),
               _committee('Board Committee', 11), _committee('Strategy Committee', 12))


@pytest.mark.parametrize('own_rows', [False, True])
def test_rows_on_a_sub_level_named_after_another_institution_are_not_its_rows(own_rows: bool) -> None:
    duties = ('Administrative Duties',)
    heading = _entry('P', 'Example University', 10, duties, institution='Example University')
    first = [_at(_committee('Admissions Committee', 11), duties), _at(_committee('Search Committee', 12), duties)]
    first = first if own_rows else []
    level = ('Administrative Duties', 'Westbrook Hospital Committees')
    other = [_at(_committee('Quality Committee', 14), level), _at(_committee('Safety Committee', 15), level)]
    _consume(heading, *first, *other)
    assert [_filled(row, 'institution') for row in (*first, *other)] == ['Example University'] * len(first) + [None] * 2


def test_a_sub_level_named_by_the_headings_initials_is_its_own() -> None:
    """WZSMUO 331: its sub-levels name the heading's university by initials."""
    heading = _entry('P', 'U of Exampleton', 50, ('Administrative Duties',), institution='U of Exampleton')
    level = ('Administrative Duties', 'U of E Committees')
    rows = [_at(_committee('Search Committee', 52), level), _at(_committee('Planning Committee', 53), level)]
    _consume(heading, *rows)
    assert [_filled(row, 'institution') for row in rows] == ['U of Exampleton'] * 2


@pytest.mark.parametrize('sibling', [
    _entry('P', 'St. Westbrook Hospital', 13, committee_name='St. Westbrook Hospital'),
    _entry('P', 'Westbrook Hospital, Eastfield', 13, committee_name='Westbrook Hospital'),
])
def test_an_undated_name_line_stage_4_filed_as_a_committee_ends_the_run(sibling: dict) -> None:
    """VQFSDI's stale-stamp class (#1365 R1): the next institution's line,
    which a period or a comma keeps from reading as heading-shaped."""
    heading = _entry('P', 'Example University', 10, institution='Example University')
    rows = [_committee('Admissions Committee', 11), _committee('Search Committee', 12)]
    after = [_committee('Quality Committee', 14), _committee('Safety Committee', 15)]
    _consume(heading, *rows, sibling, *after)
    assert [_filled(row, 'institution') for row in (*rows, sibling, *after)] == ['Example University'] * 2 + [None] * 3


def test_an_undated_row_with_a_role_is_still_a_row_of_the_run() -> None:
    heading = _entry('P', 'Example University', 10, institution='Example University')
    rows = [_committee('Admissions Committee', 11),
            _entry('P', 'Member, Brindle Committee', 12, role='Member', committee_name='Brindle Committee'),
            _committee('Search Committee', 13)]
    _consume(heading, *rows)
    assert [_filled(row, 'institution') for row in rows] == ['Example University'] * 3


def test_a_row_naming_only_a_kind_of_unit_keeps_it_and_the_run_goes_on() -> None:
    """WZSMUO 345/346: a row naming only a kind of unit under a university names
    a unit of it, not another institution."""
    heading = _entry('P', 'Example University', 10, institution='Example University')
    rows = [_committee('Search Committee', 11), _committee('Education Committee', 12, institution='School of Nursing'),
            _committee('Planning Committee', 13)]
    _consume(heading, *rows)
    assert [_filled(row, 'institution') for row in rows] == ['Example University', 'School of Nursing',
                                                             'Example University']


def test_a_society_line_over_offices_only_keeps_its_membership_row() -> None:
    """CHXRBM 129, EBZFDN 286, RBPXCD 155: the offices and committees render
    in Service, so the society line is the only Memberships row naming it."""
    heading = _society()
    rows = [_office('President', 11), _committee('Program Committee', 12, code='Q2', end_date='2003')]
    groups, warnings = _consume(heading, *rows)
    assert [_filled(row, 'organization') for row in rows] == [SOCIETY] * 2
    assert groups['I'] == [heading]
    assert warnings[0][ih.CONSUMED_HEADINGS_KEY] == [] and warnings[0][ih.KEPT_HEADINGS_KEY] == [[10, [11, 12]]]
    assert warnings[0]['message'].endswith(': 1 society heading kept as its own row, 2 rows filled')
    assert warnings[0]['evidence'] == ['element_idx_start 10 kept, filled 11, 12']
    assert ih.heading_fills(warnings) == {10: [11, 12]} and ih.consumed_headings(warnings) == frozenset()


def test_a_society_line_a_membership_row_names_is_consumed() -> None:
    heading = _society()
    rows = [_member(11), _office('President', 12)]
    groups, warnings = _consume(heading, *rows)
    assert 'I' in groups and heading not in groups['I']
    assert ih.consumed_headings(warnings) == frozenset({10})


def test_rows_that_already_print_the_name_count_and_are_not_filled() -> None:
    """WZSMUO 166: stage 4 already wrote the institution line into each
    mentee's Site/Position."""
    hierarchy = ('Graduate Student Mentorship', 'Thesis Advisor')
    heading = _entry('N3B', 'Example University', 30, hierarchy, site_position='Example University')
    rows = [_entry('N3B', f'{name}, Invented Science Program, Ph.D. 2002', el, hierarchy, mentee_name=name,
                   site_position='Invented Science Program, Example University', end_date='2002')
            for el, name in ((31, 'A. Student'), (32, 'B. Student'))]
    groups, warnings = _consume(heading, *rows)
    assert [_filled(row, 'institution') for row in rows] == [None, None]
    assert groups['N3B'] == rows
    assert warnings[0][ih.CONSUMED_HEADINGS_KEY] == [[30, []]]
    assert warnings[0]['evidence'] == ['element_idx_start 30 filled none']


def test_heading_fills_reads_kept_headings_and_consumed_headings_does_not() -> None:
    warnings = [{'check': ih.CONSUMED_HEADING_CHECK, ih.CONSUMED_HEADINGS_KEY: [[10, [11]]],
                 ih.KEPT_HEADINGS_KEY: [[20, [21, 22]], 'x']}]
    assert ih.heading_fills(warnings) == {10: [11], 20: [21, 22]}
    assert ih.consumed_headings(warnings) == frozenset({10})


def test_the_record_keys_are_the_sidecar_contract() -> None:
    """The doctor reads these keys back from stored render-warnings sidecars,
    so they are spelled out here, not read from the module."""
    consumed = _consume(_entry('P', 'Example University', 10, institution='Example University'),
                        _committee('Admissions Committee', 11), _committee('Search Committee', 12))[1]
    kept = _consume(_society(), _office('President', 11), _office('Treasurer', 12))[1]
    assert consumed[0]['check'] == 'institution_heading_consumed' and consumed[0]['consumed_headings'] == [[10, [11, 12]]]
    assert kept[0]['kept_headings'] == [[10, [11, 12]]] and kept[0]['consumed_headings'] == []
    assert 'kept_headings' not in consumed[0]


# Each renderer path `_fill_outcome` reads, one row at a time, under a heading
# with two plain rows that print the name: the row's own outcome is the case.
_PLAIN_ROWS = {
    'O': lambda: [_entry('O', 'Director, Example Program, 2001-2003', el, leadership_role='Director, Example Program',
                         start_date='2001', end_date='2003') for el in (11, 13)],
    'P': lambda: [_committee('Admissions Committee', 11), _committee('Search Committee', 13)],
    'Q1': lambda: [_office('President', 11), _office('Secretary', 13)],
    'Q2': lambda: [_committee('Program Committee', 11, code='Q2', end_date='2003'),
                   _committee('Audit Committee', 13, code='Q2', end_date='2003')],
    'I': lambda: [_member(11), _entry('I', 'Fellow, 2010-2015', 13, membership_type='Fellow', start_date='2010',
                                      end_date='2015')],
    'N3B': lambda: [_entry('N3B', f'{name}, Invented Science Program, Ph.D. 2002', el, mentee_name=name,
                           site_position='Invented Science Program', end_date='2002')
                    for el, name in ((11, 'A. Student'), (13, 'B. Student'))],
}


def _row_outcome(row: dict) -> object:
    """The value filled on `row` under a heading of its family with two plain rows."""
    code = row['taxonomy_code']
    family_heading = {'O': 'P', 'P': 'P', 'Q1': 'I', 'Q2': 'I', 'I': 'I', 'N3B': 'N3B'}[code]
    name = SOCIETY if family_heading == 'I' else 'Example University'
    heading = _entry(family_heading, name, 10, **{ih.FILL_FIELD_BY_CODE.get(family_heading, 'institution'): name})
    if family_heading == 'N3B':
        heading = _entry('N3B', name, 10, site_position=name)
    first, last = _PLAIN_ROWS[code]()
    _consume(heading, first, row, last)
    assert [_filled(plain, ih.FILL_FIELD_BY_CODE[code]) for plain in (first, last)] == [name] * 2
    return _filled(row, ih.FILL_FIELD_BY_CODE[code])


def _split(entry: dict) -> dict:
    """`entry` as a record stage 6 fanned out of a multi-record entry."""
    return {**entry, 'fanned_out_from': 9}


@pytest.mark.parametrize('row, filled', [
    # O: a role over two lines is still one row with an Institution cell
    (_entry('O', 'Director, Example Program\n2001-2003', 12, leadership_role='Director', start_date='2001',
            end_date='2003'), True),
    # O: a division stage 4 filed the heading's name under already prints it
    (_entry('O', 'Director, 2001-2003', 12, leadership_role='Director', division_department='Example University',
            start_date='2001', end_date='2003'), False),
    # P: no committee name, but the line names the activity the institution joins
    (_entry('P', 'Chair, Faculty Search for Dean, 2001-2003', 12, role='Chair', start_date='2001',
            end_date='2003'), True),
    # P: a committee name over two lines is still one row
    (_entry('P', 'Planning Committee\nannual retreat, 2001-2003', 12, committee_name='Planning Committee',
            start_date='2001', end_date='2003'), True),
    # Q1: no role and no organization prints the whole text in the Organization cell
    (_entry('Q1', 'Fernhollow Liaison, 2001-2003', 12, start_date='2001', end_date='2003'), False),
    # Q2: a committee merged into the role is split out, so the Organization cell takes the fill
    (_entry('Q2', 'Chair, Fernhollow Committee, Annual Meeting, 2001-2003', 12, role='Chair, Fernhollow Committee',
            start_date='2001', end_date='2003'), True),
    # Q2: a role and its dates only: the committee cell, empty before, takes the name
    (_entry('Q2', 'Member, 2001-2003', 12, role='Member', start_date='2001', end_date='2003'), True),
    (_split(_entry('Q2', 'Member, 2001-2003', 12, role='Member', start_date='2001', end_date='2003')), True),
    # Q2: a committee row over two lines renders its board lines with its fields
    (_entry('Q2', 'Steering Committee\nworking session, 2001-2003', 12, committee_name='Steering Committee',
            start_date='2001', end_date='2003'), True),
    # Q2: an ad hoc grant review renders under Grant Reviewing, which prints no fill
    (_entry('Q2', 'Ad hoc grant reviewer, Example Study Section, 2001-2003', 12, role='Reviewer',
            committee_name='Example Study Section', start_date='2001', end_date='2003'), False),
    # I: a source table's header row, which memberships drops, must not render with the name,
    # even where its words are the name's and a membership type
    (_entry('I', 'Society\tMember', 12, membership_type='Member'), False),
    # N3B: a line naming no mentee is a summary line, which prints no Site/Position
    (_entry('N3B', 'Ph.D. students advised: 12', 12, count='12'), False),
    # N3B: a Site/Position that is the name already prints it
    (_entry('N3B', 'C. Student, Ph.D. 2004', 12, mentee_name='C. Student', site_position='Example University',
            end_date='2004'), False),
])
def test_a_row_is_filled_only_on_a_renderer_path_that_prints_the_fill(row: dict, filled: bool) -> None:
    value = _row_outcome(copy.deepcopy(row))
    assert (value is not None) is filled


def test_a_row_naming_its_own_organization_as_a_dict_of_blanks_keeps_it() -> None:
    """A value that is not blank is the row's own, even one that prints as nothing."""
    row = _committee('Board', 12, institution={'note': None})
    _consume(_entry('P', 'Example University', 10, institution='Example University'), _committee('Search Committee', 11),
             row, _committee('Planning Committee', 13))
    assert row['extracted_fields']['institution'] == {'note': None}


def test_an_undated_line_naming_only_kinds_of_unit_is_a_row_of_the_run() -> None:
    """Only a line naming a particular institution ends the run as the next
    group's name line (`_is_bare_name_line`)."""
    heading = _entry('P', 'Example University', 10, institution='Example University')
    rows = [_committee('Search Committee', 11),
            _entry('P', 'Department of Nursing, Medical Center', 12,
                   committee_name='Department of Nursing, Medical Center'),
            _committee('Planning Committee', 13)]
    _consume(heading, *rows)
    assert [_filled(row, 'institution') for row in rows] == ['Example University'] * 3


# ------------------------------------------------------------ a rendered page

def test_the_rendered_committee_rows_carry_the_institution_and_no_heading_row(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(stage6, 'call_llm', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('no LLM in tests')))
    entries = [
        {'taxonomy_code': 'A', 'element_idx_start': 0, 'text': 'Name: Pat Example, MD', 'extracted_fields': {}},
        _entry('P', 'Quillmoor University', 10, institution='Quillmoor University'),
        _committee('Fernhollow Admissions Committee', 11),
        _committee('Fernhollow Search Committee', 12),
    ]
    source = tmp_path / 'TESTIH_fields.json'
    source.write_text(json.dumps({'document_uid': 'TESTIH', 'entries': entries}))
    out = stage6.run_stage6(str(source), str(tmp_path / 'out.docx'), verbose=False)
    document = Document(out)
    rows = [' | '.join(cell.text.strip() for cell in row.cells) for table in document.tables for row in table.rows]
    lines = [p.text.strip() for p in document.paragraphs] + rows
    assert sum('Fernhollow Admissions Committee, Quillmoor University' in row for row in rows) == 1
    assert sum('Fernhollow Search Committee, Quillmoor University' in row for row in rows) == 1
    assert not [line for line in lines if 'Quillmoor' in line and 'Fernhollow' not in line]
    sidecar = json.loads((tmp_path / 'TESTIH_render_warnings.json').read_text())
    assert [w['check'] for w in sidecar['warnings'] if w['check'] == ih.CONSUMED_HEADING_CHECK] == [
        ih.CONSUMED_HEADING_CHECK]


def _rendered_rows(tmp_path, monkeypatch, uid: str, entries: list[dict]) -> list[str]:
    """Every table row of the document stage 6 renders from `entries`, cells
    joined by ' | ', with LLM calls raising."""
    monkeypatch.setattr(stage6, 'call_llm', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('no LLM in tests')))
    owner = {'taxonomy_code': 'A', 'element_idx_start': 0, 'text': 'Name: Pat Example, MD', 'extracted_fields': {}}
    source = tmp_path / f'{uid}_fields.json'
    source.write_text(json.dumps({'document_uid': uid, 'entries': [owner, *entries]}))
    document = Document(stage6.run_stage6(str(source), str(tmp_path / f'{uid}.docx'), verbose=False))
    return [' | '.join(cell.text.strip() for cell in row.cells) for table in document.tables for row in table.rows]


@pytest.mark.parametrize('code', sorted(_FALLBACK_ROWS))
def test_the_rendered_row_that_prints_its_own_text_keeps_its_words(tmp_path, monkeypatch, code: str) -> None:
    """One per renderer that prints a row's own text in the cell a fill would
    take (leadership, service boards, extramural leadership, memberships):
    the row still prints its own words, and the plain rows the heading's name."""
    heading, row, plain = copy.deepcopy(_FALLBACK_ROWS[code])
    rows = _rendered_rows(tmp_path, monkeypatch, f'TESTFB{code}', [heading, plain[0], row, plain[1]])
    own = [line for line in rows if 'Fernhollow' in line]
    assert own and not any(heading['text'] in line for line in own)
    assert sum(heading['text'] in line for line in rows if 'Fernhollow' not in line) >= 2
