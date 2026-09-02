"""Regression tests for #561: `_deduplicate_repeated_content` collapsed any
two-segment pipe-separated title to its first half.

`matching_count >= len(parts) * 0.5` is a tautology at `len(parts) == 2`:
`matching_count` always counts the first segment against itself, so the
comparison is `1 >= 1.0` regardless of whether the second segment matches
at all. Every two-segment title was truncated. At four-or-more segments the
same off-by-a-boundary accepted an exact half, discarding a distinct
remainder ("A | A | B | C" -> "A").

Sole caller: `stage6/sections/research_support.py:275`, applied to a grant
title after it is resolved from `title` / `trial_title` / `study_title` /
`text` -- so a merged table cell that repeats a grant's title several times
collapses correctly, but a genuinely two-part title silently lost its
second half with no warning anywhere.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage6_grant_title_dedup.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization.text import _deduplicate_repeated_content  # noqa: E402


# --------------------------------------------------------------------------
# The bug, in its sharpest form: two distinct segments, no repetition, the
# second one silently dropped.
# --------------------------------------------------------------------------

def test_two_distinct_segments_both_survive():
    """#561's headline case: two unrelated segments, no repetition at all --
    was truncated to the first ('1 >= 1.0' tautology at len(parts) == 2)."""
    text = 'Genomic Determinants of Response | Multi-omic Profiling of Resistance'
    assert _deduplicate_repeated_content(text) == text


def test_exactly_half_repeated_does_not_collapse():
    """The n=4 boundary case the original review raised: two segments
    repeat, two are distinct -- 'exactly half' must not collapse either."""
    text = 'A | A | B | C'
    assert _deduplicate_repeated_content(text) == text


def test_three_distinct_segments_are_unaffected():
    text = 'R01 CA123456 | 09/2021-08/2026 | Principal Investigator'
    assert _deduplicate_repeated_content(text) == text


# --------------------------------------------------------------------------
# The behaviour worth keeping: an ALL-repeated merged cell still collapses.
# This is the regression guard -- tightening the threshold to "every
# segment identical" must not stop catching the shape the function was
# written for.
# --------------------------------------------------------------------------

def test_all_segments_identical_still_collapses():
    text = 'Title .08FTE | Title .08FTE | Title .08FTE'
    assert _deduplicate_repeated_content(text) == 'Title .08FTE'


def test_two_identical_segments_still_collapse():
    text = 'Same Grant Title | Same Grant Title'
    assert _deduplicate_repeated_content(text) == 'Same Grant Title'


def test_identical_segments_collapse_case_and_whitespace_insensitively():
    text = 'Grant  Title | grant title | GRANT TITLE'
    assert _deduplicate_repeated_content(text) == 'Grant  Title'


# --------------------------------------------------------------------------
# Non-pipe / single-segment inputs are untouched (unchanged behaviour).
# --------------------------------------------------------------------------

def test_no_separator_present_returns_input_unchanged():
    assert _deduplicate_repeated_content('A single title, no pipes') == (
        'A single title, no pipes'
    )


def test_empty_string_returns_empty_string():
    assert _deduplicate_repeated_content('') == ''
