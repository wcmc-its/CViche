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

    Pinned rather than fixed, on a measurement
    (`scripts/measure_normalization_claims.py --only taxonomy`): across
    every stage that stores JSON, exactly 2 values begin with a bracketed
    token at all, one distinct token between them, and it does not match
    this shape -- 0 false positives to fix. If that measurement ever comes
    back non-zero, `_strip_taxonomy_code` says what to switch to (the
    TAXONOMY_TO_SECTION key set) and this test is where the change lands."""
    assert _strip_taxonomy_code("[R01] Mechanism of injury") == (
        "Mechanism of injury"
    )
    # and the same token mid-line is still safe, which is what bounds the cost
    assert _strip_taxonomy_code("Grant [R01] awarded 2020") == (
        "Grant [R01] awarded 2020"
    )


def test_a_bracketed_non_code_token_is_left_alone():
    """The farm's only leading bracketed token, verbatim. The measurement
    script reports it by shape (`Aaaaaa`, a capitalised six-letter word) so
    that its output carries no CV text; the literal lives here."""
    assert _strip_taxonomy_code("[Editor] Journal of Examples") == (
        "[Editor] Journal of Examples"
    )


# --------------------------------------------------------------------------
# Boundary negatives on `_TAXONOMY_CODE_PREFIX`'s own shape (#735 review
# item 6): `^\s*\[[A-Z]\d{1,2}[A-Z]?\]\s+` -- one letter, 1-2 digits, an
# optional trailing letter, brackets, then required trailing whitespace,
# anchored at the true start of the string (no re.MULTILINE).
# --------------------------------------------------------------------------

def test_a_two_letter_prefix_does_not_match() -> None:
    """The shape wants exactly one leading letter, not a two-letter code."""
    text = "[AB12] two-letter prefix"
    assert _strip_taxonomy_code(text) == text


def test_three_digits_does_not_match() -> None:
    """`\\d{1,2}` caps at two digits; a third digit leaves no room for the
    closing bracket where the regex expects it."""
    text = "[A123] three digits"
    assert _strip_taxonomy_code(text) == text


def test_lowercase_letters_do_not_match() -> None:
    text = "[m2b] lowercase"
    assert _strip_taxonomy_code(text) == text


def test_no_brackets_does_not_match() -> None:
    text = "M2B no brackets here"
    assert _strip_taxonomy_code(text) == text


def test_a_code_not_at_the_true_start_is_left_alone() -> None:
    """No re.MULTILINE: `^` anchors to the start of the whole string, not the
    start of a line, so a code preceded by other text on the same line is
    not a leading prefix at all."""
    text = "Foo [M2B] not at start"
    assert _strip_taxonomy_code(text) == text


def test_a_code_with_nothing_following_it_does_not_match() -> None:
    """Trailing `\\s+` is required, so a code with nothing after the closing
    bracket -- not even a line ending in whitespace -- does not match."""
    text = "[M2B]"
    assert _strip_taxonomy_code(text) == text


def test_doubled_leading_codes_only_strip_the_first() -> None:
    """The anchor is a single `^`, so only the code at the true start goes;
    a second bracketed code right behind it is ordinary surviving text, not
    a second prefix to strip."""
    assert _strip_taxonomy_code("[M2B] [D1] doubled codes") == "[D1] doubled codes"


if __name__ == "__main__":
    test_strips_leaked_taxonomy_codes()
    test_leaves_legitimate_content_untouched()
    test_a_leading_grant_mechanism_is_stripped_by_the_shape_match()
    test_a_bracketed_non_code_token_is_left_alone()
    test_a_two_letter_prefix_does_not_match()
    test_three_digits_does_not_match()
    test_lowercase_letters_do_not_match()
    test_no_brackets_does_not_match()
    test_a_code_not_at_the_true_start_is_left_alone()
    test_a_code_with_nothing_following_it_does_not_match()
    test_doubled_leading_codes_only_strip_the_first()
    print("OK")
