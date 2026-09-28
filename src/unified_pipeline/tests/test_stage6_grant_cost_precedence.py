"""A $0 award reaches the rendered grant table (#481, round-2 review point 4).

The reviewer's words were "Zero is a valid monetary value and should not be
treated as missing". Making `_format_currency` render "$0" is only half of
that: `_create_grant_table` chose the field with

    costs = fields.get('annual_direct_costs') or fields.get('total_funding', '')

so a numeric zero in `annual_direct_costs` was discarded by `or` before the
formatter was ever called, and the row fell back to `total_funding` (or blank).
The defect the reviewer described stayed alive on the real path.

These tests exercise the call site rather than the helper: the smallest real
unit containing the line is `_create_grant_table` itself, which needs only a
bare generator with a `docx` document, so it is called directly and the
rendered "Annual direct costs:" cell is read back off the table.

Measured scope: `annual_direct_costs` appears in no CV of the 66-CV local farm
and there are 0 zero-valued funding entries, so this changes no corpus output
today -- it is a correctness fix on a path the corpus does not exercise.

`total_funding` is written as a string here because that is the shape stage 4
emits; passing an int trips an unrelated pre-existing `AttributeError` in the
cross-field duplication check above (`total_funding.strip()`, line 285), which
is out of this PR's scope.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_grant_cost_precedence.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import docx
import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402

_COSTS_ROW = 'Annual direct costs:'
_TOTAL_ROW = 'Total award:'


def _costs_row(**fields):
    """Render one grant table and return its costs row as (label, value).

    The costs row is the third of the eight; its label is "Annual direct
    costs:" unless only a total award amount is present (#982).
    """
    gen = WCMTemplateGenerator.__new__(WCMTemplateGenerator)
    gen.doc = docx.Document()
    gen.verbose = False
    fields.setdefault('title', 'Lysyl oxidase and pressure overload')
    fields.setdefault('agency', 'NIH')
    table = gen._create_grant_table(fields, 'M2B')
    costs = table.rows[2]
    return costs.cells[0].text, costs.cells[1].text


def _costs_cell(**fields):
    """The costs row's value, asserting it sits under the "Annual" label."""
    label, value = _costs_row(**fields)
    assert label == _COSTS_ROW
    return value


@pytest.mark.parametrize('zero', [0, 0.0])
def test_zero_annual_direct_costs_renders_and_does_not_fall_through(zero):
    """The case the reviewer named. `or` dropped the zero and rendered the
    fallback field instead; the row now says what the grant says."""
    assert _costs_cell(annual_direct_costs=zero, total_funding='250000') == '$0'


def test_none_annual_direct_costs_falls_back_to_total_funding():
    """An explicitly-null field is absent, not zero -- the fallback must
    survive the fix, under the total label (#982)."""
    assert _costs_row(annual_direct_costs=None, total_funding='250000') \
        == (_TOTAL_ROW, '$250,000')


def test_absent_annual_direct_costs_falls_back_to_total_funding():
    assert _costs_row(total_funding='250000') == (_TOTAL_ROW, '$250,000')


def test_both_absent_renders_an_empty_cell():
    assert _costs_cell() == ''


def test_blank_annual_direct_costs_falls_back_to_total_funding():
    """Blank string, the shape stage 4 actually emits for a field it looked for
    and did not find."""
    assert _costs_row(annual_direct_costs='   ', total_funding='250000') \
        == (_TOTAL_ROW, '$250,000')


def test_present_annual_direct_costs_still_wins_over_total_funding():
    """The precedence itself is unchanged for a non-zero value."""
    assert _costs_cell(annual_direct_costs=14876, total_funding='250000') == '$14,876'
