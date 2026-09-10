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

import docx

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization.content import _deduplicate_repeated_content  # noqa: E402
from unified_pipeline.stage_6_word_template import WCMTemplateGenerator  # noqa: E402


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


# --------------------------------------------------------------------------
# Round-2 review response: the tests above did not distinguish the rule the
# fix actually applies (`matching_count == len(parts)`) from the "strict
# majority" alternative the issue also mentions (`> len(parts) * 0.5`) --
# every case above passes under both. A 3-of-4 case separates them: strict
# majority would collapse it and lose the distinct fourth segment.
# --------------------------------------------------------------------------

def test_three_of_four_identical_does_not_collapse():
    text = 'A | A | A | B'
    assert _deduplicate_repeated_content(text) == text


# --------------------------------------------------------------------------
# #735 review item 12: through its ACTUAL caller. Every test above calls
# `_deduplicate_repeated_content` directly; none exercises the one real call
# site, `_create_grant_table`'s `title = _deduplicate_repeated_content(title)`
# (`stage6/sections/research_support.py:275`). The harness reuses
# `test_stage6_grant_cost_precedence.py`'s pattern: `_create_grant_table`
# needs only a bare generator with a `docx` document, so it is called
# directly and the rendered "Project title:" cell is read back off the
# table.
# --------------------------------------------------------------------------

_TITLE_ROW = 'Project title:'
_AWARD = 'Cancer Genomics Pilot Award'


def _rendered_title(title: str) -> str:
    """Render one grant table and return its "Project title:" value."""
    gen = WCMTemplateGenerator.__new__(WCMTemplateGenerator)
    gen.doc = docx.Document()
    gen.verbose = False
    table = gen._create_grant_table({'title': title, 'agency': 'NIH'}, 'M2B')
    return {row.cells[0].text: row.cells[1].text for row in table.rows}[_TITLE_ROW]


def test_the_caller_collapses_three_identical_pipe_segments_to_one():
    """"X | X | X" rendered through the real grant table -> "X", the shape
    a merged table cell repeating itself produces."""
    assert _rendered_title(f'{_AWARD} | {_AWARD} | {_AWARD}') == _AWARD


def test_the_caller_leaves_a_two_segment_title_untouched():
    """#561 through the real call site: "X | Y" is meaningfully different
    content and must render whole, not truncated to "X"."""
    title = f'{_AWARD} | NIH R01 Supplement'
    assert _rendered_title(title) == title


def test_the_caller_leaves_a_three_segment_title_with_one_difference_untouched():
    """Two matching segments and one that doesn't still fails the "every
    segment identical" bar, through the real call site."""
    title = f'{_AWARD} | {_AWARD} | Different Award Name'
    assert _rendered_title(title) == title


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_"):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
