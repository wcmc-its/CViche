"""Checks for the affiliation fallback pool behind the location inference (#426).

Only the deterministic ranking is covered here -- the LLM query it feeds is
exercised by the pipeline itself.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from unified_pipeline.stage_4_field_extractor import (  # noqa: E402
    _entry_end_year,
    _owner_affiliation_lines,
)


def _entry(code, **fields):
    return {'taxonomy_code': code, 'extracted_fields': fields}


def test_end_year_ranks_present_above_any_year():
    assert _entry_end_year({'end_date': 'present'}) == 9999
    assert _entry_end_year({'end_date': 'Current'}) == 9999
    assert _entry_end_year({'end_date': '2019'}) == 2019
    assert _entry_end_year({'dates_attended_end_date': 'June 2003'}) == 2003
    # No parseable date sorts last rather than blowing up.
    assert _entry_end_year({}) == 0
    assert _entry_end_year({'end_date': None}) == 0


def test_employer_is_treated_as_current_even_without_a_date():
    lines = _owner_affiliation_lines([
        _entry('E', employer='Weill Cornell Medicine, New York, NY'),
        _entry('D1', institution='Somewhere Else, Boston, MA', end_date='2015'),
    ])
    body = '\n'.join(lines[1:])
    assert body.index('Weill Cornell') < body.index('Somewhere Else')
    assert '[current]' in lines[1]


def test_recency_outranks_frequency():
    """A long past post must not beat a current one just by appearing more often."""
    entries = [_entry('D1', institution='Old Hospital, Chicago, IL', end_date='2010')] * 8
    entries.append(_entry('D1', institution='New Hospital, New York, NY', end_date='present'))
    lines = _owner_affiliation_lines(entries)
    assert 'New Hospital' in lines[1], lines
    # Frequency still shows up, it just does not drive the order.
    assert '(x8)' in '\n'.join(lines)


def test_frequency_breaks_ties_at_equal_recency():
    entries = [_entry('K1', institution='Weill Cornell Medicine, New York, NY')] * 5
    entries.append(_entry('K1', institution='Visiting Clinic, Newark, NJ'))
    lines = _owner_affiliation_lines(entries)
    assert 'Weill Cornell' in lines[1], lines


def test_conference_and_funder_fields_are_not_harvested():
    """A talk's venue or a grant's agency is not where the CV owner works."""
    lines = _owner_affiliation_lines([
        _entry('R', location='Kyoto, Japan', host_organization='Some Society'),
        _entry('S8', location='Vienna, Austria'),
        _entry('M2A', agency='National Institutes of Health'),
        _entry('S9', venue='Lancet'),
    ])
    assert lines == []


def test_empty_and_malformed_entries_are_survivable():
    assert _owner_affiliation_lines([]) == []
    assert _owner_affiliation_lines([{'taxonomy_code': 'D1'}]) == []
    assert _owner_affiliation_lines([{'taxonomy_code': 'D1', 'extracted_fields': None}]) == []
    # Not a dict -- seen in the wild when an entry fails extraction.
    assert _owner_affiliation_lines([{'taxonomy_code': 'D1', 'extracted_fields': []}]) == []
    # Too short to be a real affiliation.
    assert _owner_affiliation_lines([_entry('D1', institution='-')]) == []


def test_pool_is_capped():
    entries = [_entry('D1', institution=f'Institution Number {i}, City {i}, NY') for i in range(40)]
    lines = _owner_affiliation_lines(entries, limit=15)
    assert len(lines) == 16  # header + 15


if __name__ == '__main__':
    for name, fn in sorted(globals().items()):
        if name.startswith('test_'):
            fn()
            print(f'ok  {name}')
    print('all checks passed')
