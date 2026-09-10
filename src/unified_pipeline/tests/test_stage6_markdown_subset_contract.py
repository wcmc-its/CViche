"""`_strip_markdown_for_word` handles a SUBSET of markdown, as a contract.

The name promises more than the implementation delivers: it removes bold
markers and a "- " bullet prefix, and does nothing at all about links,
emphasis, inline code, escapes, ATX headers or ordered lists. The review
offered a rename or a documented, tested subset; the subset is documented in
the function's own docstring and pinned here, because renaming would churn
all five files `git grep _strip_markdown_for_word` names -- its definition
in `normalization/rendering.py`, `normalization/__init__.py`,
`sections/teaching.py`, `stage_6_word_template.py` and this one.

Every assertion below is what the function does TODAY. The point of the file
is that the unsupported constructs are passed through deliberately, not by
accident: pass-through is the safe direction, because an unrecognised
construct then renders as literal characters instead of having content cut
out of it, and a future change that starts stripping one of them has to come
here and say so.

    python3 -m pytest src/unified_pipeline/tests/test_stage6_markdown_subset_contract.py -p no:cacheprovider

Self-contained: no DB, no template, no python-docx, no PII.
"""

import sys
from collections import Counter
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage6.normalization.rendering import (  # noqa: E402
    _strip_markdown_for_word,
)


# --------------------------------------------------------------------------
# The supported subset
# --------------------------------------------------------------------------

def test_bold_markers_are_stripped_anywhere_in_the_line():
    assert _strip_markdown_for_word("**Course:** Intro to X") == "Course: Intro to X"
    assert _strip_markdown_for_word("Role **PI** on the grant") == "Role PI on the grant"


def test_a_dash_space_bullet_prefix_is_stripped():
    assert _strip_markdown_for_word("- a sub bullet") == "a sub bullet"


def test_the_stage_5c_notes_marker_is_stripped_with_the_bullet():
    assert _strip_markdown_for_word("- Notes: something") == "something"
    assert _strip_markdown_for_word("- Notes:something") == "something"


def test_lines_are_stripped_blank_lines_dropped_and_the_rest_rejoined():
    assert _strip_markdown_for_word("  a  \n\n  b  ") == "a; b"
    assert _strip_markdown_for_word("  a  \n\n  b  ",
                                    preserve_newlines=True) == "a\nb"


def test_empty_input_is_empty_output():
    assert _strip_markdown_for_word("") == ""
    assert _strip_markdown_for_word(None) == ""


# --------------------------------------------------------------------------
# Everything outside the subset, passed through as written
# --------------------------------------------------------------------------

def test_links_are_not_touched():
    assert _strip_markdown_for_word(
        "[text](http://example.com)") == "[text](http://example.com)"


def test_emphasis_is_not_touched():
    assert _strip_markdown_for_word(
        "*emphasis* and _under_") == "*emphasis* and _under_"


def test_inline_code_is_not_touched():
    assert _strip_markdown_for_word("`inline code`") == "`inline code`"


def test_escaped_bold_markers_keep_their_backslashes():
    """The bold regex needs two ADJACENT asterisks, and an escape puts a
    backslash between them, so nothing matches and the text -- backslashes
    included -- reaches Word verbatim."""
    text = r"\*\*not bold\*\*"
    assert _strip_markdown_for_word(text) == text


def test_atx_headers_are_not_stripped_despite_the_old_docstring():
    """The docstring claimed "Headers: # Header -> Header". No code has ever
    done that; this pins the truth so the claim cannot come back."""
    assert _strip_markdown_for_word("# Header") == "# Header"
    assert _strip_markdown_for_word("## Header two") == "## Header two"


def test_ordered_list_markers_are_not_stripped():
    assert _strip_markdown_for_word("1. ordered item") == "1. ordered item"


def test_a_dash_with_no_following_space_is_not_a_bullet():
    assert _strip_markdown_for_word("-item") == "-item"


def test_a_nested_bullet_is_flattened_not_preserved():
    """Each line is stripped BEFORE the "- " test, so indentation is gone by
    the time the prefix is recognised: a sub-bullet comes out at the same
    level as its parent, and the two are joined like any other two lines."""
    assert _strip_markdown_for_word("- parent\n  - child") == "parent; child"


def test_an_unbalanced_bold_run_is_left_as_found():
    """Not an accident worth hiding: "**a*b**" has an asterisk inside, which
    the bold pattern excludes, so it survives untouched -- while "***x***"
    loses only its inner pair."""
    assert _strip_markdown_for_word("**a*b**") == "**a*b**"
    assert _strip_markdown_for_word("***triple***") == "*triple*"


# --------------------------------------------------------------------------
# Content preservation (#735 review item 8): every alphanumeric character
# of the input survives into the output, for both the supported subset AND
# every unsupported construct -- pass-through preserves everything by
# definition, and the two supported transforms (bold-marker removal, the
# "- " bullet prefix) remove only delimiter punctuation, never a letter or
# digit. The one deliberate exception is the stage-5c "Notes:" structural
# marker, which is content-shaped but is documentation markup, not CV text,
# and is excluded from this battery -- it is pinned on its own terms by
# `test_the_stage_5c_notes_marker_is_stripped_with_the_bullet` above.
# --------------------------------------------------------------------------

_CONTENT_PRESERVATION_CASES = (
    "**bold text** and normal",          # supported: bold markers
    "- a sub bullet",                    # supported: bullet prefix
    "[link text](http://example.com/path)",  # unsupported: links
    "*emphasis* and _under_",            # unsupported: emphasis
    "`inline code`",                     # unsupported: inline code
    "\\*\\*escaped\\*\\*",               # unsupported: escapes
    "# Header\n## sub header",           # unsupported: ATX headers
    "1. ordered\n2. items",              # unsupported: ordered lists
    "-no-space-bullet",                  # unsupported: dash w/ no space
    "- parent\n  - nested child",        # unsupported: nested lists
    "**a*b**",                           # unsupported: unbalanced bold
    "***triple***",                      # supported bold nested in itself
)


def test_every_alphanumeric_character_of_the_input_survives_the_output():
    """The property, not one example, for both `preserve_newlines` values.
    Counted as a multiset so a repeated character cannot go missing behind
    an identical one -- the same technique
    `test_the_pair_parser_keeps_every_alphabetic_character_of_its_input`
    uses for author names."""
    for text in _CONTENT_PRESERVATION_CASES:
        expected = Counter(c for c in text if c.isalnum())
        for preserve_newlines in (False, True):
            output = _strip_markdown_for_word(text, preserve_newlines=preserve_newlines)
            actual = Counter(c for c in output if c.isalnum())
            assert actual == expected, (
                f'{text!r} (preserve_newlines={preserve_newlines}) lost '
                f'{dict(expected - actual)} and gained {dict(actual - expected)}'
            )


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_"):
            _fn()
            print(f"ok  {_name}")
    print("all checks passed")
