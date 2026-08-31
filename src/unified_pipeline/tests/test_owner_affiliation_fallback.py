"""Checks for the affiliation fallback pool behind the location inference (#426).

Most of this file covers the deterministic ranking (`_rank_owner_affiliations`)
and its thin renderer (`_owner_affiliation_lines`). One test
(`test_infer_cv_owner_location_prompt_carries_extracted_affiliations`) drives
the actual LLM-calling `infer_cv_owner_location()` end to end, mocking only
the LLM boundary.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from unified_pipeline.stage_4_field_extractor import (  # noqa: E402
    _entry_end_year,
    _owner_affiliation_lines,
)
from unified_pipeline.stage4 import owner_name  # noqa: E402
from unified_pipeline.stage4.owner_name import (  # noqa: E402
    CURRENT_POSITION_YEAR,
    _rank_owner_affiliations,
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


def test_end_year_survives_malformed_or_unrecognized_values():
    """Text that isn't a year at all must sort last, not raise or misparse."""
    assert _entry_end_year({'end_date': 'unknown'}) == 0
    assert _entry_end_year({'end_date': 'N/A'}) == 0
    assert _entry_end_year({'end_date': 'TBD'}) == 0
    assert _entry_end_year({'end_date': 'ongoing'}) == 0
    # No 4-digit run in the string at all.
    assert _entry_end_year({'end_date': 'March 3, 22'}) == 0
    assert _entry_end_year({'dates_attended_end_date': 'unknown'}) == 0
    # Malformed but still contains a recognizable 4-digit year -- the regex
    # fallback grabs it rather than failing closed.
    assert _entry_end_year({'end_date': '13/45/2020'}) == 2020


def test_end_year_word_boundary_matches_present_and_current_case_insensitively():
    """Positive direction: the word-boundary regex still catches every real
    spelling of an ongoing position."""
    assert _entry_end_year({'end_date': 'present'}) == CURRENT_POSITION_YEAR
    assert _entry_end_year({'end_date': 'Present'}) == CURRENT_POSITION_YEAR
    assert _entry_end_year({'end_date': 'Current'}) == CURRENT_POSITION_YEAR
    assert _entry_end_year({'end_date': 'CURRENT'}) == CURRENT_POSITION_YEAR
    assert _entry_end_year({'end_date': 'Present, expected through 2027'}) == CURRENT_POSITION_YEAR


def test_end_year_word_boundary_rejects_substring_false_positives():
    """Negative direction (review item 3): a bare `'present' in text` /
    `'current' in text` substring test also matches unrelated words that
    happen to contain those letters. The word-boundary regex must not.
    """
    assert _entry_end_year({'end_date': 'currently unavailable'}) == 0
    # 'representative' contains the substring 'present' (...rep-RESENT-ative).
    assert _entry_end_year({'end_date': 'representative position'}) == 0
    # 'concurrent' contains the substring 'current' (con-CURRENT).
    assert _entry_end_year({'end_date': 'concurrent enrollment, ended 2019'}) == 2019


def test_end_year_prefers_end_date_over_dates_attended_end_date():
    """Review item 4: end_date has explicit precedence over
    dates_attended_end_date when both are populated -- pin the contract."""
    assert _entry_end_year({'end_date': '2020', 'dates_attended_end_date': '2005'}) == 2020
    assert _entry_end_year({
        'end_date': 'present', 'dates_attended_end_date': '2005',
    }) == CURRENT_POSITION_YEAR


def test_organization_and_address_fields_are_harvested():
    """Review item 5: all four allow-listed fields are harvested, not just
    employer/institution (exercised elsewhere in this file)."""
    lines = _owner_affiliation_lines([
        _entry('E', organization='Weill Cornell Physician Organization, New York, NY'),
        _entry('A', address='1300 York Avenue, New York, NY'),
    ])
    body = '\n'.join(lines)
    assert 'Weill Cornell Physician Organization' in body
    assert '1300 York Avenue' in body


def test_fields_outside_the_allow_list_are_not_harvested():
    """Complements test_organization_and_address_fields_are_harvested: a talk's
    venue or a grant's agency is not where the CV owner works, even though
    'venue'/'agency' sit right next to the allow-listed fields on the entry."""
    lines = _owner_affiliation_lines([
        _entry('R', location='Kyoto, Japan', host_organization='Some Society'),
        _entry('S9', venue='Lancet'),
        _entry('M2A', agency='National Institutes of Health'),
    ])
    assert lines == []


def test_limit_zero_returns_no_lines():
    """Review item 6 (limit=0): read, don't invent, the real contract.
    `sorted(...)[:0]` yields an empty ranked list, so the renderer returns []
    -- not even the header line.

    This is the one input where the renderer split changed the output: the
    pre-split code fell through to `lines = [header]` and returned that
    header-only list. No production caller passes limit=0 (the sole call site
    uses the default 15), so nothing in the pipeline changes -- but the delta
    is real and is pinned here rather than left implicit.
    """
    entries = [_entry('D1', institution=f'Institution {i}, City {i}, NY') for i in range(5)]
    assert _owner_affiliation_lines(entries, limit=0) == []
    assert _rank_owner_affiliations(entries, limit=0) == []


def test_limit_one_returns_header_plus_a_single_line():
    """Review item 6 (limit=1)."""
    entries = [
        _entry('D1', institution='Weill Cornell Medicine, New York, NY', end_date='present'),
        _entry('D1', institution='Somewhere Else, Boston, MA', end_date='2010'),
    ]
    lines = _owner_affiliation_lines(entries, limit=1)
    assert len(lines) == 2  # header + 1
    assert 'Weill Cornell' in lines[1]
    assert len(_rank_owner_affiliations(entries, limit=1)) == 1


def test_limit_greater_than_available_affiliations_returns_all_of_them():
    """Review item 6 (limit > number of affiliations): does not pad or error,
    just returns everything found."""
    entries = [_entry('D1', institution=f'Institution {i}, City {i}, NY') for i in range(5)]
    lines = _owner_affiliation_lines(entries, limit=1000)
    assert len(lines) == 6  # header + all 5
    assert len(_rank_owner_affiliations(entries, limit=1000)) == 5


def test_owner_affiliation_lines_renders_header_marker_and_count():
    """The thin renderer's exact output format, decoupled here from the
    ranking scenarios below (review item 9)."""
    lines = _owner_affiliation_lines([
        _entry('E', employer='Weill Cornell Medicine, New York, NY'),
    ])
    assert lines[0] == (
        'AFFILIATIONS STATED ACROSS THE CV (most recent first, with how often each appears):'
    )
    assert lines[1] == '  - Weill Cornell Medicine, New York, NY (x1) [current]'


def test_owner_affiliation_lines_pool_is_capped_with_header():
    entries = [_entry('D1', institution=f'Institution Number {i}, City {i}, NY') for i in range(40)]
    lines = _owner_affiliation_lines(entries, limit=15)
    assert len(lines) == 16  # header + 15


def test_rank_employer_is_treated_as_current_even_without_a_date():
    """Structured counterpart of the old lines-based version of this check
    (review item 9): assert on the (value, count, is_current) records instead
    of string-searching the rendered prompt text."""
    ranked = _rank_owner_affiliations([
        _entry('E', employer='Weill Cornell Medicine, New York, NY'),
        _entry('D1', institution='Somewhere Else, Boston, MA', end_date='2015'),
    ])
    values = [r.value for r in ranked]
    assert values.index('Weill Cornell Medicine, New York, NY') < values.index('Somewhere Else, Boston, MA')
    assert ranked[0].is_current is True


def test_rank_recency_outranks_frequency():
    """A long past post must not beat a current one just by appearing more
    often (structured version of review item 9)."""
    entries = [_entry('D1', institution='Old Hospital, Chicago, IL', end_date='2010')] * 8
    entries.append(_entry('D1', institution='New Hospital, New York, NY', end_date='present'))
    ranked = _rank_owner_affiliations(entries)
    assert ranked[0].value == 'New Hospital, New York, NY'
    assert ranked[0].is_current is True
    # Frequency still shows up in the data, it just does not drive the order.
    old = next(r for r in ranked if r.value == 'Old Hospital, Chicago, IL')
    assert old.count == 8
    assert old.is_current is False


def test_rank_frequency_breaks_ties_at_equal_recency():
    entries = [_entry('K1', institution='Weill Cornell Medicine, New York, NY')] * 5
    entries.append(_entry('K1', institution='Visiting Clinic, Newark, NJ'))
    ranked = _rank_owner_affiliations(entries)
    assert ranked[0].value == 'Weill Cornell Medicine, New York, NY'
    assert ranked[0].count == 5


def test_rank_conference_and_funder_fields_are_not_harvested():
    """A talk's venue or a grant's agency is not where the CV owner works."""
    ranked = _rank_owner_affiliations([
        _entry('R', location='Kyoto, Japan', host_organization='Some Society'),
        _entry('S8', location='Vienna, Austria'),
        _entry('M2A', agency='National Institutes of Health'),
        _entry('S9', venue='Lancet'),
    ])
    assert ranked == []


def test_rank_empty_and_malformed_entries_are_survivable():
    assert _rank_owner_affiliations([]) == []
    assert _rank_owner_affiliations([{'taxonomy_code': 'D1'}]) == []
    assert _rank_owner_affiliations([{'taxonomy_code': 'D1', 'extracted_fields': None}]) == []
    # Not a dict -- seen in the wild when an entry fails extraction.
    assert _rank_owner_affiliations([{'taxonomy_code': 'D1', 'extracted_fields': []}]) == []
    # Too short to be a real affiliation.
    assert _rank_owner_affiliations([_entry('D1', institution='-')]) == []


def test_rank_pool_is_capped():
    entries = [_entry('D1', institution=f'Institution Number {i}, City {i}, NY') for i in range(40)]
    ranked = _rank_owner_affiliations(entries, limit=15)
    assert len(ranked) == 15


def test_location_inference_sees_extracted_fields(monkeypatch):
    """The call site must run after extraction, not before it.

    Every check above hands _owner_affiliation_lines entries that already carry
    extracted_fields. At runtime nothing upstream produces that key -- stage 3b
    does not emit it and stage 4 is what fills it in -- so inferring before the
    extraction loop fed this function bare entries and it returned an empty pool
    on every real CV. The unit checks could not see that, because they supply
    the fields themselves. This one asserts the ordering instead.

    Rebinds on `stage4.extraction`, not the `stage_4_field_extractor` facade:
    `extract_fields_from_mapped_entries` resolves its callees through the
    extraction module's globals, so a stub bound on the facade's re-export is a
    second binding the call never sees (#498 split, the #496 lesson). Uses the
    pytest `monkeypatch` fixture (review item 7) instead of hand-saving and
    restoring the module globals -- it restores them automatically even if an
    assertion fails partway through.
    """
    from unified_pipeline.stage4 import extraction as s4

    mapped = [
        {'taxonomy_code': 'D1', 'text': 'Professor of Surgery, Weill Cornell Medicine',
         'element_idx_start': 0, 'element_idx_end': 0},
        {'taxonomy_code': 'E', 'text': 'Employment Status: full time',
         'element_idx_start': 1, 'element_idx_end': 1},
    ]
    seen = {}

    def fake_batch(batch, batch_idx, num_batches, model=None, cv_owner_name=None):
        out = []
        for e in batch:
            out.append({**e, 'extracted_fields': {
                'institution': 'Weill Cornell Medicine, New York, NY',
                'end_date': 'present',
            }})
        return {'success': True, 'entries': out, 'cost': 0.0, 'tokens': 0}

    def fake_infer(entries, model=None):
        seen['with_fields'] = sum(1 for e in entries if e.get('extracted_fields'))
        seen['total'] = len(entries)
        return {'inference_success': False}

    monkeypatch.setattr(s4, 'extract_fields_batch', fake_batch)
    monkeypatch.setattr(s4, 'infer_cv_owner_location', fake_infer)
    monkeypatch.setattr(s4, 'extract_cv_owner_name', lambda uid, entries: {'last_name': ''})

    s4.extract_fields_from_mapped_entries(mapped, document_uid='TEST')

    assert seen, 'infer_cv_owner_location was never called'
    assert seen['with_fields'] == seen['total'] == 2, (
        f"location inference saw {seen['with_fields']}/{seen['total']} entries carrying "
        'extracted_fields -- it is running before extraction again'
    )


def test_infer_cv_owner_location_prompt_carries_extracted_affiliations(monkeypatch):
    """Review item 8: exercise the real infer_cv_owner_location(), mocking
    only the LLM boundary.

    test_location_inference_sees_extracted_fields above replaces
    infer_cv_owner_location() itself to check pool-building order, and the
    _rank_owner_affiliations tests exercise the ranking directly -- neither
    actually runs this function's own body. This drives the real production
    path end to end and asserts that an affiliation extracted upstream is what
    actually reaches the LLM prompt.

    infer_cv_owner_location resolves call_llm through owner_name's OWN module
    globals at call time, so the stub is bound on
    unified_pipeline.stage4.owner_name, not on the stage_4_field_extractor
    facade re-export -- a stub bound on the facade copy is a second binding
    this call never sees (the #496 lesson).
    """
    captured_prompts = []

    def fake_call_llm(**kwargs):
        captured_prompts.append(kwargs['messages'][-1]['content'])
        location = {
            'institution': 'Weill Cornell Medicine',
            'city': 'New York',
            'state': 'NY',
            'country': 'USA',
            'confidence': 1.0,
        }
        return {
            'content': json.dumps({
                'locations': [location],
                'metro_area': 'New York City',
                'primary_location': location,
            }),
            'total_tokens': 42,
            'cost': 0.001,
        }

    monkeypatch.setattr(owner_name, 'call_llm', fake_call_llm)

    mapped_entries = [{
        'taxonomy_code': 'D1',
        # The raw text deliberately omits the institution: the positions
        # renderer falls back to e['text'][:150] when field harvesting finds
        # nothing, so an institution that also appears in `text` would let this
        # assertion pass even if the extracted_fields path broke entirely.
        'text': 'Professor of Surgery',
        'extracted_fields': {
            'title': 'Professor of Surgery',
            'institution': 'Weill Cornell Medicine, New York, NY',
            'start_date': '2015',
            'end_date': 'present',
        },
    }]

    result = owner_name.infer_cv_owner_location(mapped_entries)

    assert captured_prompts, 'call_llm was never invoked'
    assert 'Weill Cornell Medicine' in captured_prompts[0]
    assert result['inference_success'] is True
    assert result['primary_location']['institution'] == 'Weill Cornell Medicine'
