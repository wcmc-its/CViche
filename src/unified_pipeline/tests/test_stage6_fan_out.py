"""#983: a multi-record entry fans out into one entry per record.

`stage6/fan_out.py` is pure, so every case below is a dict in, dicts out. The
wiring (that `_group_entries_by_code` calls it before the PII pass and dedup)
is pinned in `test_stage6_render_drops.py`, next to the other tests of that
method, and the dedup guard for its children in `test_stage6_dedup_safety.py`.

Every value is invented. The shapes are the ones the 148-CV render set
carries: web199's H `awards`, web218's P `committees` / `entries`, web205's O
`additional_roles`, web241's N3B `mentees`, web181's K2 `mentees(name, year)`
and web228's K4 `sessions`.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_fan_out.py -p no:cacheprovider
"""
import copy
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage4.schemas import FIELD_SCHEMAS  # noqa: E402
from unified_pipeline.stage6 import fan_out  # noqa: E402
from unified_pipeline.stage6.fan_out import (  # noqa: E402
    FANNED_OUT_FROM,
    fan_out_multi_record_entries,
)

# The drift guard the stage-6 package cannot import stage4 for: the codes this
# file builds entries for still have the fields the fixtures rely on.
_SCHEMA_FIELDS_USED = {
    'H': {'award_name', 'granting_body', 'date'},
    'P': {'committee_name', 'role', 'institution', 'start_date', 'end_date'},
    'O': {'leadership_role', 'institution', 'start_date', 'end_date'},
    'N3B': {'mentee_name', 'mentee_level', 'start_date', 'end_date'},
    'K2': {'teaching_role', 'start_date', 'end_date'},
    'K4': {'activity_title', 'date'},
}


def _fan(*entries):
    return fan_out_multi_record_entries(list(entries), FIELD_SCHEMAS)


def _award(name, body='Hollis College', date='1986'):
    return {'award_name': name, 'granting_body': body, 'date': date}


def _honors(text, awards, **scalars):
    return {'taxonomy_code': 'H', 'text': text, 'element_idx_start': 7,
            'extracted_fields': {'awards': awards, **scalars}}


_THREE_HONORS = _honors(
    '1986    Kappa Delta Honor Society, Hollis College\tBeta Sigma Honor Society, '
    'Hollis College\tPhi Rho History Honor Society, Hollis College',
    [_award('Kappa Delta Honor Society'), _award('Beta Sigma Honor Society'),
     _award('Phi Rho History Honor Society')])


def test_schema_fields_the_fixtures_use_still_exist():
    for code, fields in _SCHEMA_FIELDS_USED.items():
        assert fields <= set(FIELD_SCHEMAS[code]['fields']), code


class TestFanOut:
    def test_one_child_per_record_in_order(self):
        children = _fan(_THREE_HONORS)
        assert [c['extracted_fields']['award_name'] for c in children] == [
            'Kappa Delta Honor Society', 'Beta Sigma Honor Society',
            'Phi Rho History Honor Society']
        assert 'awards' not in children[0]['extracted_fields']

    def test_text_is_the_matching_tab_segment_when_the_counts_line_up(self):
        children = _fan(_THREE_HONORS)
        assert [c['text'] for c in children] == [
            '1986    Kappa Delta Honor Society, Hollis College',
            'Beta Sigma Honor Society, Hollis College',
            'Phi Rho History Honor Society, Hollis College']

    def test_text_is_built_from_the_record_when_a_record_wrapped(self):
        # Four tab segments for two records: the second award wrapped across
        # two lines, so pairing by position would give it the wrong tail.
        parent = _honors(
            '1988    Gamma Omega Honorary, Ashby University\tGraduate School\t'
            'Award Winner, Trenton Society\tStudent Workshop',
            [_award('Gamma Omega Honorary', 'Ashby University Graduate School', '1988'),
             _award('Award Winner, Trenton Society Student Workshop', 'Trenton Society', '1988')])
        children = _fan(parent)
        assert [c['text'] for c in children] == [
            'Gamma Omega Honorary | Ashby University Graduate School | 1988',
            'Award Winner, Trenton Society Student Workshop | Trenton Society | 1988']

    def test_provenance_names_the_list_and_position(self):
        children = _fan(_THREE_HONORS)
        assert [c[FANNED_OUT_FROM] for c in children] == [
            {'key': 'awards', 'index': 0, 'count': 3},
            {'key': 'awards', 'index': 1, 'count': 3},
            {'key': 'awards', 'index': 2, 'count': 3}]

    def test_item_wins_over_parent_scalar_and_scalars_are_inherited(self):
        parent = _honors('Alpha Prize, Hollis College\tBeta Prize, Hollis College',
                         [{'award_name': 'Alpha Prize', 'date': '1990'},
                          {'award_name': 'Beta Prize'}],
                         date='1985', granting_body='Hollis College')
        first, second = _fan(parent)
        assert first['extracted_fields'] == {
            'date': '1990', 'granting_body': 'Hollis College', 'award_name': 'Alpha Prize'}
        assert second['extracted_fields']['date'] == '1985'

    def test_other_entry_keys_are_copied_and_the_parent_is_not_mutated(self):
        original = copy.deepcopy(_THREE_HONORS)
        children = _fan(_THREE_HONORS)
        assert _THREE_HONORS == original
        assert all(c['element_idx_start'] == 7 and c['taxonomy_code'] == 'H'
                   for c in children)
        children[0]['extracted_fields']['award_name'] = 'changed'
        assert children[1]['extracted_fields']['award_name'] == 'Beta Sigma Honor Society'

    def test_entries_around_a_fanned_one_keep_their_place_and_identity(self):
        before = {'taxonomy_code': 'H', 'text': 'Solo Prize', 'extracted_fields': {'award_name': 'Solo Prize'}}
        after = {'taxonomy_code': 'T', 'text': 'anything', 'extracted_fields': {}}
        out = _fan(before, _THREE_HONORS, after)
        assert len(out) == 5
        assert out[0] is before and out[-1] is after

    def test_parent_remark_and_formatter_keys(self):
        parent = _honors('Alpha Prize, Hollis College\tBeta Prize, Hollis College',
                         [_award('Alpha Prize'), _award('Beta Prize')],
                         notes='Entry contains two tab-separated honors')
        children = _fan(parent)
        assert all('notes' not in c['extracted_fields'] for c in children)


class TestWhatIsNotFannedOut:
    def _unchanged(self, entry):
        assert _fan(entry) == [entry]

    def test_a_single_record_list(self):
        self._unchanged(_honors('Alpha Prize, Hollis College', [_award('Alpha Prize')]))

    def test_a_list_of_strings(self):
        self._unchanged(_honors('Alpha Prize\tBeta Prize', ['Alpha Prize', 'Beta Prize']))

    def test_the_schemas_own_field(self):
        # `award_name` IS a schema field: a list there is stage 4's value, not a
        # record list under a foreign key.
        self._unchanged({'taxonomy_code': 'H', 'text': 'Alpha Prize Beta Prize',
                         'extracted_fields': {'award_name': [_award('Alpha Prize'), _award('Beta Prize')]}})

    def test_a_list_holding_an_empty_record(self):
        self._unchanged(_honors('Beta Prize, Hollis College\tBeta Prize', [{}, _award('Beta Prize')]))

    def test_records_that_share_no_key_with_the_schema(self):
        # web181's K2 `mentees: [{name, year}]`: nothing the K2 renderer reads.
        self._unchanged({'taxonomy_code': 'K2', 'text': 'Mentored Ana Cruz 2019\tLee Park 2020',
                         'extracted_fields': {'mentees': [{'name': 'Ana Cruz', 'year': '2019'},
                                                          {'name': 'Lee Park', 'year': '2020'}]}})

    def test_records_carrying_a_key_no_renderer_reads(self):
        # web228's K4 `sessions`: `title` is the session's own name.
        sessions = [{'date': '2020-04-22', 'title': 'Definition and Clinical Evaluation'},
                    {'date': '2020-04-29', 'title': 'Complexity and Impact'}]
        self._unchanged({'taxonomy_code': 'K4',
                         'text': '2020 Practice Improvement Project\t2020-04-22 Definition and Clinical '
                                 'Evaluation\t2020-04-29 Complexity and Impact',
                         'extracted_fields': {'activity_title': 'Practice Improvement Project',
                                              'sessions': sessions}})

    def test_an_entry_a_stage5_formatter_already_rendered_whole(self):
        parent = _honors('Alpha Prize, Hollis College\tBeta Prize, Hollis College',
                         [_award('Alpha Prize'), _award('Beta Prize')],
                         formatted_text='- Alpha Prize\n- Beta Prize')
        self._unchanged(parent)

    def test_an_entry_with_two_record_lists(self):
        entry = {'taxonomy_code': 'P', 'text': 'Alpha Board\tBeta Board\tGamma Board\tDelta Board',
                 'extracted_fields': {
                     'committees': [{'committee_name': 'Alpha Board'}, {'committee_name': 'Beta Board'}],
                     'entries': [{'committee_name': 'Gamma Board'}, {'committee_name': 'Delta Board'}]}}
        self._unchanged(entry)

    def test_a_code_with_no_schema_and_a_non_mapping_field_bag(self):
        self._unchanged({'taxonomy_code': 'ZZ', 'text': 't', 'extracted_fields': {'x': [{'a': 1}, {'a': 2}]}})
        self._unchanged({'taxonomy_code': 'H', 'text': 't', 'extracted_fields': None})
        self._unchanged({'taxonomy_code': 'H', 'text': 't'})


_COMMITTEES = [
    {'committee_name': 'Alpha Curriculum Oversight Council', 'role': 'Member',
     'institution': 'Ashby University'},
    {'committee_name': 'Beta Admissions Selection Council', 'role': 'Chair',
     'institution': 'Ashby University'}]


class TestTextCoverage:
    """Every token of the entry's text must be held by a field the renderer
    writes: a child renders from those fields alone, so any other token is
    content the output would no longer have."""

    _RECORDS = ('Alpha Curriculum Oversight Council member{a}\t'
                'Beta Admissions Selection Council chair{b}')

    def _committee_entry(self, text, committees=_COMMITTEES, **scalars):
        return {'taxonomy_code': 'P', 'text': text,
                'extracted_fields': {'committees': committees, **scalars}}

    def test_text_the_fields_hold_is_fanned_out(self):
        assert len(_fan(self._committee_entry(self._RECORDS.format(a='', b='')))) == 2

    def test_prose_around_the_records_keeps_the_entry_whole(self):
        prose = ('I serve on the Alpha Curriculum Oversight Council as a member and I chair the '
                 'Beta Admissions Selection Council, which reviews proposals every semester')
        entry = self._committee_entry(prose)
        assert _fan(entry) == [entry]

    @pytest.mark.parametrize('qualifier', ['(weekly)', '(1-3 committees/yr)', 'UMB'])
    def test_one_word_no_field_holds_keeps_the_entry_whole(self, qualifier):
        # No tolerance: web218's "(1-3 committees/yr)" and "UMB" were lost.
        entry = self._committee_entry(self._RECORDS.format(a=' ' + qualifier, b=''))
        assert _fan(entry) == [entry]

    def test_a_field_the_renderer_never_writes_holds_nothing(self):
        # web240's "Neuroscience Training Program": every item carries
        # `institution`, the P renderer never writes it.
        text = ('Alpha Curriculum Oversight Council member, Ashby University\t'
                'Beta Admissions Selection Council chair, Ashby University')
        entry = self._committee_entry(text)
        assert 'institution' in FIELD_SCHEMAS['P']['fields']
        assert _fan(entry) == [entry]

    def test_a_key_outside_the_schema_holds_nothing(self):
        entry = self._committee_entry(self._RECORDS.format(a=' weekly', b=''), cadence='weekly')
        assert _fan(entry) == [entry]

    def test_a_word_the_text_repeats_must_be_held_as_often(self):
        # 'Council' once in the fields, twice in the text.
        text = 'Alpha Board member\tBeta Board chair\tCouncil Council'
        entry = self._committee_entry(text, [{'committee_name': 'Alpha Board', 'role': 'member'},
                                             {'committee_name': 'Beta Board', 'role': 'chair'}],
                                      role='Council')
        assert _fan(entry) == [entry]

    def test_a_scalar_counts_once_per_child(self):
        # The scalar `granting_body` is written on each of the two children,
        # so a text that names it twice is covered.
        entry = _honors('Alpha Prize, Hollis College\tBeta Prize, Hollis College',
                        [{'award_name': 'Alpha Prize'}, {'award_name': 'Beta Prize'}],
                        granting_body='Hollis College')
        assert len(_fan(entry)) == 2

    def test_connectives_are_not_counted(self):
        text = 'Chair of the Board\tMember of Alpha in Beta for Gamma on a Delta at an Epsilon to Zeta and Eta'
        entry = self._committee_entry(text, [{'committee_name': 'Board', 'role': 'Chair'},
                                             {'committee_name': 'Alpha Beta Gamma Delta Epsilon Zeta Eta',
                                              'role': 'Member'}])
        assert len(_fan(entry)) == 2

    def test_a_year_no_field_holds_keeps_the_entry_whole(self):
        entry = self._committee_entry(self._RECORDS.format(a=' 1999', b=''))
        assert _fan(entry) == [entry]

    def test_a_year_a_field_holds_is_fine(self):
        entry = self._committee_entry(self._RECORDS.format(a=' 1999', b=''), start_date='1999')
        assert len(_fan(entry)) == 2

    def test_a_month_the_date_column_does_not_show_keeps_the_entry_whole(self):
        # P's date column is `yyyy`: "07/2009" is written as "2009".
        entry = self._committee_entry(self._RECORDS.format(a=' 07/2009', b=''), start_date='07/2009')
        assert _fan(entry) == [entry]

    def test_a_date_range_the_column_shows_in_full_is_fine(self):
        entry = self._committee_entry(self._RECORDS.format(a=' 2009 to 2011', b=''),
                                      start_date='2009-07', end_date='2011-03')
        assert len(_fan(entry)) == 2

    def test_a_code_the_renderer_map_does_not_name_is_never_fanned_out(self, monkeypatch):
        entry = self._committee_entry(self._RECORDS.format(a='', b=''))
        monkeypatch.setattr(fan_out, '_RENDERED_FIELDS', {})
        assert _fan(entry) == [entry]


class TestChildrenShareNothingWithTheirParent:
    def test_nested_values_are_copies(self):
        parent = _honors('Alpha Prize, Hollis College\tBeta Prize, Hollis College',
                         [dict(_award('Alpha Prize'), description=['first']), _award('Beta Prize')],
                         venue=['hall'])
        parent['hierarchy'] = ['HONORS']
        first, second = _fan(parent)
        first['hierarchy'].append('changed')
        first['extracted_fields']['description'].append('changed')
        first['extracted_fields']['venue'].append('changed')
        assert second['hierarchy'] == ['HONORS'] and parent['hierarchy'] == ['HONORS']
        assert parent['extracted_fields']['awards'][0]['description'] == ['first']
        assert second['extracted_fields']['venue'] == ['hall']
        assert parent['extracted_fields']['venue'] == ['hall']


def test_built_text_leaves_out_empty_values():
    parent = _honors('Alpha Prize\tBeta\tPrize',
                     [{'award_name': 'Alpha Prize', 'granting_body': '', 'date': '1988'},
                      {'award_name': 'Beta Prize', 'granting_body': None, 'date': '1989'}])
    assert [c['text'] for c in _fan(parent)] == ['Alpha Prize | 1988', 'Beta Prize | 1989']


_LATER_POSTS = [
    {'leadership_role': 'Leader, Genomics Program', 'start_date': '2015', 'end_date': '2022'},
    {'leadership_role': 'Deputy Director', 'start_date': '2022', 'end_date': 'present'}]


class TestParentOwnRecord:
    """web205's O: the Co-Leader post is in the parent's scalars, the later
    posts are `additional_roles`. web36's P stage-5d shape repeats the first
    post in both."""

    def _o(self, text, **scalars):
        return {'taxonomy_code': 'O', 'text': text,
                'extracted_fields': {'additional_roles': _LATER_POSTS, **scalars}}

    def test_one_more_segment_than_records_means_the_parent_is_a_record(self):
        parent = self._o('2012- Co-Leader, Genomics Program\t2015-2022 Leader, Genomics Program\t'
                         '2022- Deputy Director',
                         leadership_role='Co-Leader, Genomics Program', start_date='2012')
        out = _fan(parent)
        assert [c['extracted_fields']['leadership_role'] for c in out] == [
            'Co-Leader, Genomics Program', 'Leader, Genomics Program', 'Deputy Director']
        assert out[0]['text'] == '2012- Co-Leader, Genomics Program'
        assert out[0]['extracted_fields']['start_date'] == '2012'
        assert [c[FANNED_OUT_FROM]['count'] for c in out] == [3, 3, 3]

    def test_as_many_segments_as_records_means_the_list_holds_them_all(self):
        parent = self._o('2015-2022 Leader, Genomics Program\t2022- Deputy Director',
                         leadership_role='Leader, Genomics Program', start_date='2015')
        out = _fan(parent)
        assert len(out) == 2

    def test_no_identity_value_means_no_extra_record(self):
        # `mentee_level` is shared context, not a record of its own, even when
        # the text has a header segment ("NIH T32 (Post-graduate):").
        parent = {'taxonomy_code': 'N3B',
                  'text': 'Post-graduate:\tAna Cruz (2013)\tLee Park (2014)',
                  'extracted_fields': {'mentee_level': 'Post-graduate', 'funding_source': 'Fellowship',
                                       'mentees': [{'mentee_name': 'Ana Cruz', 'start_date': '2013'},
                                                   {'mentee_name': 'Lee Park', 'start_date': '2014'}]}}
        out = _fan(parent)
        assert [c['extracted_fields']['mentee_name'] for c in out] == ['Ana Cruz', 'Lee Park']
        assert all(c['extracted_fields']['mentee_level'] == 'Post-graduate' for c in out)

    def test_a_parent_an_item_repeats_is_not_emitted_twice(self):
        parent = {'taxonomy_code': 'P',
                  'text': 'Interim Director, North Wing\tInterim Director, South Wing',
                  'extracted_fields': {'role': 'Interim Director', 'additional_roles': [
                      {'role': 'Interim Director', 'committee_name': 'North Wing'},
                      {'role': 'Interim Director', 'committee_name': 'South Wing'}, ]}}
        assert len(_fan(parent)) == 2

    def test_counts_that_say_neither_fall_back_to_the_repeat_test(self):
        # Four segments for two records: a wrapped line, so not decisive.
        distinct = self._o('2012- Co-Leader, Genomics\tProgram\t2015-2022 Leader, Genomics\tProgram',
                           leadership_role='Co-Leader, Genomics Program', start_date='2012')
        assert len(_fan(distinct)) == 3
        repeated = self._o('2015-2022 Leader, Genomics\tProgram\t2022- Deputy\tDirector',
                           leadership_role='Deputy Director', start_date='2022')
        assert len(_fan(repeated)) == 2


    def test_a_date_the_items_do_not_repeat_is_not_an_identity(self):
        # Four segments for two records (a wrapped line, counts undecided) and
        # a parent whose only scalars are dates that differ from both items':
        # the dates are context, so no extra record.
        parent = {'taxonomy_code': 'P',
                  'text': 'Alpha Board of\tGovernors\tBeta Board of\tTrustees',
                  'extracted_fields': {'start_date': '2010', 'committees': [
                      {'committee_name': 'Alpha Board of Governors', 'start_date': '2016'},
                      {'committee_name': 'Beta Board of Trustees', 'start_date': '2017'}]}}
        assert len(_fan(parent)) == 2

    @pytest.mark.parametrize('blank', [None, '', '   ', [], {}])
    def test_a_blank_identity_field_is_not_an_identity(self, blank):
        # One header segment more than records, and a parent whose only
        # identity-shaped scalar is blank: not a record of its own.
        parent = {'taxonomy_code': 'P', 'text': 'Council:\tAlpha Board\tBeta Board',
                  'extracted_fields': {'committee_name': blank, 'role': 'Council', 'committees': [
                      {'committee_name': 'Alpha Board'}, {'committee_name': 'Beta Board'}]}}
        assert len(_fan(parent)) == 2


@pytest.mark.parametrize('key,expected', [('start_date', True), ('date', True), ('year', True),
                                          ('end_date', True), ('role', False), ('mentee_name', False)])
def test_date_keys_never_count_as_a_parents_identity(key, expected):
    from unified_pipeline.stage6.fan_out import _is_date_key
    assert _is_date_key(key) is expected


# --- the renderer map, checked against the renderers themselves ---------------

def _rendered_markers(tmp_path, code):
    """The schema fields of `code` whose marker reaches the .docx when one entry
    of that code is rendered with a unique marker in every field."""
    import json

    from docx import Document

    from unified_pipeline.stage_6_word_template import WCMTemplateGenerator

    markers = {name: 'Zq' + name.replace('_', 'x') + 'Z'
               for name in FIELD_SCHEMAS[code]['fields'] if name != 'narrative'}
    entry = {'taxonomy_code': code, 'element_idx_start': 0, 'text': ' '.join(markers.values()),
             'extracted_fields': dict(markers)}
    source, target = tmp_path / 'in.json', tmp_path / 'out.docx'
    source.write_text(json.dumps({'document_uid': 'TESTAA', 'entries': [entry]}))
    generator = WCMTemplateGenerator(verbose=False)
    generator.generate(str(source), str(target), research_summary_path=None)
    document = Document(str(target))
    body = ' '.join(t.text or '' for t in document.element.body.iter(
        '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}t'))
    return {name for name, marker in markers.items() if marker in body}


@pytest.mark.parametrize('code', sorted(fan_out._RENDERED_FIELDS))
def test_rendered_fields_match_what_each_section_writes(tmp_path, code):
    """`_RENDERED_FIELDS[code]` is exactly the set of fields the code's section
    writes: a field the map claims but the renderer skips would let the
    coverage test pass on content the output drops (web240's `institution`),
    and one it omits would needlessly keep entries whole."""
    assert _rendered_markers(tmp_path, code) == set(fan_out._RENDERED_FIELDS[code])


def test_the_renderer_map_names_only_schema_fields_and_never_narrative():
    for code, names in fan_out._RENDERED_FIELDS.items():
        assert names <= set(FIELD_SCHEMAS[code]['fields']), code
        assert 'narrative' not in names, code


def test_every_code_with_a_schema_is_in_the_renderer_map_except_personal_data():
    assert set(FIELD_SCHEMAS) - set(fan_out._RENDERED_FIELDS) == {'A'}
