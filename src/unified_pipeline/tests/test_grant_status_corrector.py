"""Issue #981: stage 3b's grant status corrector reads date ranges and headings correctly.

Synthetic rows only. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_grant_status_corrector.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.validators import grant_status_corrector  # noqa: E402
from unified_pipeline.core.validators.grant_status_corrector import (  # noqa: E402
    apply_grant_status_corrections,
    correct_grant_status,
    extract_year_range,
)

TEST_YEAR = 2026


@pytest.fixture(autouse=True)
def _pinned_year(monkeypatch):
    """The corrector compares against a module-level year read at import."""
    monkeypatch.setattr(grant_status_corrector, 'CURRENT_YEAR', TEST_YEAR)


def _grant(code, text, *hierarchy):
    return {'taxonomy_code': code, 'text': text, 'hierarchy': list(hierarchy)}


@pytest.mark.parametrize('text, expected', [
    # The #981 shapes: the end date's month read as a two-digit year.
    ('03/01/2024-\n12/31/2028 | R01 Example Grant', (2024, 2028)),
    ('12/01/2019–11/30/2021 Example Grant', (2019, 2021)),
    ('6/1/2022-5/31/2025 (NCE) Role: Co-Investigator', (2022, 2025)),
    # The shapes that already worked.
    ('2019-2021 Example Grant', (2019, 2021)),
    ('01/2019-12/2021 Example Grant', (2019, 2021)),
    ('2019-21 Example Grant', (2019, 2021)),
    ('2019 - present Example Grant', (2019, TEST_YEAR + 1)),
    ('Example Grant $50,000 awarded 2020', (2020, 2020)),
    ('Example Grant with no dates at all', None),
    # Two digits after a hyphen that are a month or day, not a year (#981).
    ('03/01/2024-\n12/31/28 Example Grant', None),
    # A range none of the patterns reads is no range at all: its start year is
    # not a single-year grant (#981; web46's typo, the two-digit-year end above).
    ('03/01/2024-12/31/28 Example Grant $50,000', None),
    ('04/01/2022-03/312027 Example Grant $1,000,000', None),
    # A month-name end is read, not left to the single-year guess (web210).
    ('Mar 2022-Apr 2023 | Example Grant | $50,000', (2022, 2023)),
    ('2019 - Sept. 2021 Example Grant', (2019, 2021)),
    ('Oct 2014 - Oct 2025 Example Grant $50,000', (2014, 2025)),
    # An open end written "– date" is still running (#981; web36's contracts).
    ('04/08/2021 – date  Example Contract  Award: $2,638,299', (2021, TEST_YEAR + 1)),
    ('2019-Date Example Grant', (2019, TEST_YEAR + 1)),
    # "date" as a word after the dash only, not "update" or "dated".
    ('2019-dated memo $50,000', (2019, 2019)),
    # An end year running into the next label is still the end (XLYVYA).
    ('07/01/2021 - 06/30/2026Total costs: $2,000,000', (2021, 2026)),
    # A year inside a hyphenated award number is not an unread range.
    ('Award Number: HSCNO-2020-LIFT-001, $31,504', (2020, 2020)),
])
def test_extract_year_range(text, expected):
    assert extract_year_range(text) == expected


def test_a_current_grant_with_a_split_date_range_is_not_filed_as_past():
    """web207: "03/01/2024-<newline>12/31/2028" read as 2024-2012 forced M2B."""
    entry = _grant('M2A', '03/01/2024-\n12/31/2028 | R01 Example Grant',
                   'Grants and Contracts Received')

    assert correct_grant_status(entry)['taxonomy_code'] == 'M2A'


def test_an_open_ended_contract_written_to_date_stays_current():
    """web36: "04/08/2021 – date" with an award amount read as the single year
    2021 and forced M2B; the contract is still running."""
    entry = _grant('M2A', '04/08/2021 – date  Example Contract  Award: $2,638,299',
                   'Grants and Salary Support')

    assert correct_grant_status(entry)['taxonomy_code'] == 'M2A'


def test_a_range_with_a_typo_is_not_read_as_its_start_year():
    """web46: "04/01/2022-03/312027" fell through to the single-year guess, 2022."""
    entry = _grant('M2A', '04/01/2022-03/312027 Example Grant $1,000,000',
                   'Research Support', 'Present')

    assert correct_grant_status(entry)['taxonomy_code'] == 'M2A'


def test_a_completed_grant_coded_current_still_moves_to_completed():
    """The rule the range fix must not weaken: an ended range is Past."""
    entry = _grant('M2A', '12/01/2019-11/30/2021 | R01 Example Grant', 'Grants')

    corrected = correct_grant_status(entry)

    assert corrected['taxonomy_code'] == 'M2B'
    assert corrected['status_correction']['from'] == 'M2A'


def test_a_completed_grant_coded_completed_with_a_running_range_becomes_current():
    entry = _grant('M2B', '06/01/2019-03/31/2027 | R35 Example Grant', 'Grants')

    assert correct_grant_status(entry)['taxonomy_code'] == 'M2A'


@pytest.mark.parametrize('heading', [
    ['NOT FUNDED'],
    ['GRANT SUPPORT', 'Non-funded applications'],
    ['Pending applications'],
    ['GRANTS', 'GRANT APPLICATIONS IN REVIEW'],
    ['Grants', 'Declined'],
    ['Grants', 'Withdrawn'],
])
def test_a_pending_application_with_ended_dates_stays_pending(heading):
    """Dates under a pending heading are the proposed period, not an award's."""
    entry = _grant('M2C', '2019-2021 Example Application', *heading)

    assert correct_grant_status(entry)['taxonomy_code'] == 'M2C'


@pytest.mark.parametrize('heading', [
    ['NOT FUNDED'],
    ['Pending applications'],
    ['Grants', 'Submitted'],
    ['Grants', 'Unfunded'],
    ['Grants', 'Declined'],
    ['Grants', 'Withdrawn'],
    ['Grants', 'Under review'],
    ['GRANTS', 'GRANT APPLICATIONS AWAITING FINAL ADMINISTRATIVE APPROVAL'],
    # EBYSBC E7: an "applied" heading named fifteen applications.
    ['GRANTS APPLIED'],
    ['Research', 'Grant Application'],
    ['Research', 'Grant Applications'],
    ['Proposals Submitted'],
])
def test_each_pending_heading_word_shields_a_grant_from_date_rules(heading):
    """An M2A whose range ended would flip to M2B on dates alone; not under these."""
    entry = _grant('M2A', '2019-2021 Example Application', *heading)

    assert correct_grant_status(entry)['taxonomy_code'] == 'M2A'


def test_a_pending_application_with_a_dollar_amount_stays_pending():
    """A requested budget in a pending section is not evidence of an award."""
    entry = _grant('M2C', 'Example Application $250,000', 'Pending applications')

    assert correct_grant_status(entry)['taxonomy_code'] == 'M2C'


def test_a_pending_application_with_running_dates_and_an_amount_is_not_promoted():
    entry = _grant('M2C', '2026-2029 Example Application $250,000', 'Pending applications')

    assert correct_grant_status(entry)['taxonomy_code'] == 'M2C'


def test_a_grant_coded_current_or_completed_under_a_pending_heading_is_left_alone():
    """Only stage 6's heading rule may move it; a proposed period decides nothing."""
    running = _grant('M2B', '2026-2029 Example Application', 'GRANT APPLICATIONS IN REVIEW')
    ended = _grant('M2A', '2019-2021 Example Application', 'Pending')

    assert correct_grant_status(running)['taxonomy_code'] == 'M2B'
    assert correct_grant_status(ended)['taxonomy_code'] == 'M2A'


def test_an_m2c_under_a_funded_heading_with_ended_dates_still_becomes_completed():
    """2015_W: "GRANT FUNDING: > Funded" grants the LLM coded M2C, one ended
    2023. No pending word, so dev's Rule 1 holds."""
    entry = _grant('M2C', '01/01/2022-05/31/2023 Example Award',
                   'GRANT SUPPORT', 'GRANT FUNDING:', 'Funded')

    corrected = correct_grant_status(entry)

    assert corrected['taxonomy_code'] == 'M2B'
    assert corrected['status_correction']['from'] == 'M2C'


@pytest.mark.parametrize('heading', [
    ['Scholarship and Research'],
    ['Grants'],
    ['Research Support', 'Applied Grants'],
    [],
])
def test_an_application_is_not_filed_as_completed_on_its_dates_alone(heading):
    """EBYSBC E7: an old application is not a completed award. With no heading
    that files it as awarded, the LLM's M2C stands, requested amount or not."""
    entry = _grant('M2C', '1/1/2009-12/31/2009 Example Application $100,000', *heading)

    corrected = correct_grant_status(entry)

    assert corrected['taxonomy_code'] == 'M2C'
    assert 'status_correction' not in corrected


@pytest.mark.parametrize('heading', [
    ['Research Support', 'Active'],
    ['Current Grants'],
    ['Ongoing Support'],
    ['Past and Present Support'],
    ['Past Grants'],
    ['Previous Support'],
    ['Grant Support', 'Prior Funding'],
    ['Completed Research Support'],
    ['Funded Research'],
    ['Grants Awarded'],
])
def test_an_m2c_under_an_awarded_heading_with_ended_dates_becomes_completed(heading):
    """The heading is the corroboration the dates alone lack: the grant was awarded."""
    entry = _grant('M2C', '2015 - 2021 Example Award $1,000,000', *heading)

    corrected = correct_grant_status(entry)

    assert corrected['taxonomy_code'] == 'M2B'
    assert corrected['status_correction']['from'] == 'M2C'


def test_an_m2a_still_moves_to_completed_on_its_dates_alone():
    """Only an application needs the heading; a current-coded grant that ended
    is completed under any heading that is not a pending one."""
    entry = _grant('M2A', '2015 - 2021 Example Award', 'Scholarship and Research')

    assert correct_grant_status(entry)['taxonomy_code'] == 'M2B'


@pytest.mark.parametrize('text', [
    '*Title: Example\n*Status of Support: Pending\n09/2022 - 08/2027 $3,258,091',
    'Example Application\nStatus: Under review\n2026-2029 $250,000',
    'Example Application\nStatus: In review\n2026-2029 $250,000',
    'Example Application\nStatus: Not funded\n2019-2021',
    'Example Application\nStatus: Non-funded\n2019-2021',
    'Example Application\nStatus: Unfunded\n2019-2021',
    'Example Application\nStatus: Submitted\n2026-2029 $250,000',
    'Example Application\nStatus: Awaiting\n2026-2029 $250,000',
    'Example Application\nStatus: Declined\n2019-2021',
    'Example Application\nSTATUS: WITHDRAWN\n2019-2021',
])
def test_an_explicit_pending_status_line_shields_a_grant_under_a_silent_heading(text):
    """web30: a pending application the extractor filed under "Refereed
    articles" must not be promoted to Current on its proposed dates."""
    entry = _grant('M2C', text, 'Refereed articles')

    assert correct_grant_status(entry)['taxonomy_code'] == 'M2C'


@pytest.mark.parametrize('text', [
    'Example Grant\nStatus: Active\n2019-2021',
    'Example Grant\nStatus: Pendingx\n2019-2021',
    'Example Grant\nSubstatus: Pending\n2019-2021',
    'Example Grant\nStatus Pending\n2019-2021',
])
def test_a_status_line_that_is_not_pending_shields_nothing(text):
    entry = _grant('M2A', text, 'Grants')

    assert correct_grant_status(entry)['taxonomy_code'] == 'M2B'


def test_a_heading_word_must_be_a_whole_word():
    """"Impending" is not "pending": the heading guard does not fire."""
    entry = _grant('M2A', '2019-2021 Example Grant', 'Impending Renewals')

    assert correct_grant_status(entry)['taxonomy_code'] == 'M2B'


def test_apply_grant_status_corrections_counts_only_real_changes():
    entries = [
        _grant('M2C', '2019-2021 Example Application', 'NOT FUNDED'),
        _grant('M2A', '12/01/2019-11/30/2021 Example Grant', 'Grants'),
        {'taxonomy_code': 'A', 'text': 'not a grant', 'hierarchy': []},
    ]

    corrected, stats = apply_grant_status_corrections(entries)

    assert [e['taxonomy_code'] for e in corrected] == ['M2C', 'M2B', 'A']
    assert stats['total_grants'] == 2
    assert stats['corrections_applied'] == 1
