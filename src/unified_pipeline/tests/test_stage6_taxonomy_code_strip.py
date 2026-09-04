"""Regression guard for taxonomy-code leaks in rendered bullets (issue #251).

Stage-3b classification codes (M2B, D1, S6, N3A …) sometimes ride at the front
of an entry's raw text. When such an entry falls through to the bullet-render
path, the bracketed code leaked verbatim into the faculty-facing document —
observed in real runs as "• [M2B] Project title: …" and "• [D1] Visiting
Professor …". ``_strip_taxonomy_code`` removes a leading bracketed code before
the bullet is composed. This test pins that behaviour and, importantly, that it
does NOT eat legitimate leading-bracket content.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_taxonomy_code_strip.py -p no:cacheprovider

Self-contained: no DB, no template, no python-docx needed.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import _strip_taxonomy_code


def test_strips_leaked_taxonomy_codes():
    assert _strip_taxonomy_code("[M2B] Project title: Measuring Quality") == "Project title: Measuring Quality"
    assert _strip_taxonomy_code("[D1] Visiting Professor, Leiden") == "Visiting Professor, Leiden"
    assert _strip_taxonomy_code("[S6] 19. Ronny FMH, Black MA") == "19. Ronny FMH, Black MA"
    assert _strip_taxonomy_code("  [N3A] Ahmed Hassan") == "Ahmed Hassan"  # leading space tolerated


def test_leaves_legitimate_content_untouched():
    # numbered citation, not a taxonomy code (starts with a digit)
    assert _strip_taxonomy_code("[1] Smith J, et al.") == "[1] Smith J, et al."
    # a code-shaped token mid-line is not a leading prefix
    assert _strip_taxonomy_code("Grant [R01] awarded 2020") == "Grant [R01] awarded 2020"
    # no bracket at all
    assert _strip_taxonomy_code("Chief, Division of Infectious Diseases") == "Chief, Division of Infectious Diseases"
    # empty / falsy
    assert _strip_taxonomy_code("") == ""


def test_a_leading_grant_mechanism_is_stripped_by_the_shape_match():
    """The measured cost of shape-matching instead of allow-listing (#735
    review). "[R01]" fits `[A-Z]\\d{1,2}[A-Z]?` as exactly as "[D1]" does,
    so a bullet that opens with a grant mechanism loses it.

    Pinned rather than fixed, on a measurement: across the farm's 412
    stage-3b/4/5/5b/5c/5d artifacts (1,326,667 string values) exactly 2
    values begin with a bracketed token at all, both "[Editor]", and
    neither matches this shape -- 0 false positives to fix. If that
    measurement ever comes back non-zero, `_strip_taxonomy_code` says what
    to switch to (the TAXONOMY_TO_SECTION key set) and this test is where
    the change lands."""
    assert _strip_taxonomy_code("[R01] Mechanism of injury") == (
        "Mechanism of injury"
    )
    # and the same token mid-line is still safe, which is what bounds the cost
    assert _strip_taxonomy_code("Grant [R01] awarded 2020") == (
        "Grant [R01] awarded 2020"
    )


def test_a_bracketed_non_code_token_is_left_alone():
    """The only leading bracketed token that actually occurs in the farm."""
    assert _strip_taxonomy_code("[Editor] Journal of Examples") == (
        "[Editor] Journal of Examples"
    )


if __name__ == "__main__":
    test_strips_leaked_taxonomy_codes()
    test_leaves_legitimate_content_untouched()
    test_a_leading_grant_mechanism_is_stripped_by_the_shape_match()
    test_a_bracketed_non_code_token_is_left_alone()
    print("OK")
