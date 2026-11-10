"""Stage 3b's grant->position corrector (`core/validators/grant_position_corrector.py`).

The corrector recodes an M2A/M2B entry whose text names a leadership or
position role ("Medical Director", "Division Chief") and no grant indicator.
#2004: a programme-project core, a full "National Institutes of Health", a
"funded" line, a letter-prefixed grant number ("RR-00125") and an NIH
institute acronym were not grant indicators, so three real grants that also
named a director's role left Research Funding for a leadership section.

Invented text only. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_grant_position_corrector.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.validators.grant_position_corrector import (  # noqa: E402
    apply_grant_position_corrections,
    correct_grant_to_position,
)

# A role the corrector reads as a position, and nothing it reads as a grant.
_ROLE = 'Medical Director, Example Core, Norvale University 1998-2003'


def _code(text, code='M2B'):
    return correct_grant_to_position({'taxonomy_code': code, 'text': text})['taxonomy_code']


def test_a_position_with_no_grant_indicator_is_still_corrected():
    assert _code(_ROLE) == 'L3'


@pytest.mark.parametrize('indicator', [
    'Program Project Core B',
    'program project',
    'National Institutes of Health',
    'National Institute of Health',
    'funded by the Example Foundation',
    'RR-00125',
    'HL-123456',
    'NIAID',
    'NIDDK',
    'NICHHD',
])
def test_a_grant_indicator_keeps_a_position_worded_grant(indicator):
    assert _code(f'{_ROLE}, {indicator}') == 'M2B'


@pytest.mark.parametrize('not_an_indicator', [
    'rr-00125',            # a grant number is matched in capitals only
    'RR-125',              # too few digits for a grant number
    'niaid',               # an institute acronym is matched in capitals only
    'NICU',                # an intensive-care unit, not an NIH institute
    'unfunded service',    # "funded" as a whole word only
    'Program Projects Office',  # not "Program Project" alone
])
def test_text_that_only_resembles_an_indicator_is_still_corrected(not_an_indicator):
    assert _code(f'{_ROLE}, {not_an_indicator}') == 'L3'


@pytest.mark.parametrize('text, code', [
    ('Executive Vice Chair, Administration', 'O'),
    ('Chief, Division of General Pediatrics', 'O'),
    ('Medical Director, Inpatient Pediatrics', 'L3'),
])
def test_the_module_docstring_examples_still_correct(text, code):
    assert _code(text, 'M2A') == code


def test_the_modules_own_examples_still_correct():
    """The five examples the module runs as its `__main__`: four positions
    recoded, one R01 grant kept."""
    entries = [
        {'taxonomy_code': 'M2A', 'text': '2010-Present Executive Vice Chair, Administration - Example Hospital'},
        {'taxonomy_code': 'M2A', 'text': '2003-Present Chief Division, General Pediatrics - Example Hospital'},
        {'taxonomy_code': 'M2A', 'text': '2014-2017 Site Chief, Pediatrics Annex - Example Hospital'},
        {'taxonomy_code': 'M2A', 'text': 'R01 CA123456 (PI: Smith) 07/2022-06/2027 NIH/NCI $2,500,000 Example'},
        {'taxonomy_code': 'M2A', 'text': '1999-2010 Director, Pediatric Graduate Medical Education - Example Hospital'},
    ]
    corrected, stats = apply_grant_position_corrections(entries)

    assert [e['taxonomy_code'] for e in corrected] == ['O', 'D2', 'O', 'M2A', 'D2']
    assert stats['corrections_applied'] == 4
