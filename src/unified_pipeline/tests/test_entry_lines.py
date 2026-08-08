"""Tests for `entry_lines`, the segmenter that replaced ten copy-pasted copies
of `[l.strip() for l in text.split('\\n') if l.strip()]` across stage 6 (#476).

This is a pure consolidation: `entry_lines` must be EXACTLY equivalent to the
idiom it replaced, including on the ugly inputs (None, whitespace-only, CRLF,
trailing separators). If it is not, ten section renderers change behaviour at
once, silently.

The second half pins the property that motivated the refactor: `entry_lines`
sees only newlines, so it returns one blob for the 21.8% of corpus entries whose
parts are separated by tab or '|'. That is deliberate preserved behaviour, and
the test exists so that switching a call site to `entry_fragments` is a
conscious, separately-gated act rather than an accident.

    python3 -m pytest src/unified_pipeline/tests/test_entry_lines.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.render_check import entry_fragments, entry_lines  # noqa: E402


def _old_idiom(text):
    """Verbatim copy of what stage 6 did in ten places before #476."""
    return [l.strip() for l in str(text or "").split("\n") if l.strip()]


CASES = [
    None,
    "",
    "   ",
    "\n",
    "\n\n\n",
    "single line",
    "  padded  ",
    "a\nb",
    "a\n\nb",
    "  a  \n  b  ",
    "a\nb\n",
    "\na\nb",
    "a\r\nb",          # CRLF: '\r' survives the split and is removed by strip()
    "a\tb",            # tab is NOT a separator here
    "a | b",           # pipe is NOT a separator here
    "Program\tOchsner Clinic Foundation\t2009 - 2018",
    "Role | Organization | 2013 - present",
    "Title\n- child one\n- child two",
    "2013 – present",
    "Ochsner Medical Center—Westbank",
]


def test_exactly_equivalent_to_the_replaced_idiom():
    for case in CASES:
        assert entry_lines(case) == _old_idiom(case), f"diverged on {case!r}"


def test_handles_none_and_empty_without_raising():
    assert entry_lines(None) == []
    assert entry_lines("") == []
    assert entry_lines("   \n  \n ") == []


def test_strips_and_drops_empties():
    assert entry_lines("  a  \n\n  b  \n") == ["a", "b"]


def test_newline_only_by_design():
    """The property behind #476: tab- and pipe-separated content is ONE line.

    If this test starts failing because someone taught `entry_lines` about more
    separators, that is a rendering change across all ten call sites and needs
    its own corpus gate -- not a passing test edit.
    """
    tabbed = "Program\tOchsner Clinic Foundation\t2009 - 2018"
    assert entry_lines(tabbed) == [tabbed]
    assert len(entry_fragments(tabbed)) == 3

    piped = "Role | Organization | 2013 - present"
    assert entry_lines(piped) == [piped]
    assert len(entry_fragments(piped)) == 3


def test_does_not_split_on_dashes():
    """Date ranges and hyphenated place names must survive intact.

    2,492 corpus entries contain ' - '/' — ', overwhelmingly date ranges, and
    336 carry a bare em-dash inside an institution name. Splitting on either
    tears real content in half.
    """
    for intact in [
        "6/1/2004 – present",
        "Member, 2013 – present",
        "1998 - 2002",
        "Ochsner Medical Center—Westbank",
        "University Medical Center—New Orleans",
    ]:
        assert entry_lines(intact) == [intact]
