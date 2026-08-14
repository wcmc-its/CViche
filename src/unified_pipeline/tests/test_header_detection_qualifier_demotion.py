"""FIX #399: bare qualifier words demoted off level 1 in detect_section_headers.

KNOWN_CV_HEADERS_SET (cv_headers.py) lists bare organizational qualifiers --
"selected", "representative", "major", "significant" -- because they show up
as short bold labels ahead of a sub-list ("Selected Publications", "Major
Grants"). In detect_section_headers, a short bold line matching that set
earns a +0.45 confidence boost on vocabulary alone. When the bold line isn't
immediately followed by a plain paragraph in the elements array (e.g. a blank
line separates the label from its list, a common real-world layout), the
'bold_before_plain' branch never fires to set level=2, so the word keeps the
loop's default level=1 and gets promoted to a spurious top-level section --
fragmenting the real parent section (e.g. "Selected" splitting "Publications"
in two).

The temporal terms ("current", "past", "active", "completed", "pending",
"ongoing") already avoid this because they are ALSO listed in
KNOWN_SUBSECTION_TERMS, so _enforce_hierarchy_consistency demotes any
level-1 occurrence back down. #399 adds "selected", "representative",
"major", "significant" to that same set, following the identical mechanism.

Deliberately exercises the full detect_section_headers() pipeline (scoring +
_enforce_hierarchy_consistency), not the hierarchy helper directly -- #399's
bug is in the interaction between the two, and testing _enforce_hierarchy_
consistency alone would miss the vocabulary-driven level=1 misdetection that
triggers it.

Confirmed this fixture fails on unfixed code: reverting only the
KNOWN_SUBSECTION_TERMS addition in cv_headers.py (leaving this file as-is)
reproduces both qualifiers landing at level 1 --

    AssertionError: {'Publications': 1, 'Representative': 1, 'Selected': 1}
    assert 1 in (2, 3)

-- i.e. exactly the #399 symptom. See the PR body's Verification section for
the full pytest transcript of that run.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_header_detection_qualifier_demotion.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import pytest

# Redundant with conftest.py (which puts src/ on sys.path for the whole
# directory before collection) but left in place on purpose -- see
# conftest.py's docstring: harmless, and it's what keeps this file runnable
# standalone via the __main__ block below.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.segmentation.header_detection import detect_section_headers  # noqa: E402

QUALIFIER_TERMS = ["Selected", "Representative", "Major", "Significant"]


def _paragraph(text, bold=False, style="Normal", alignment=None):
    """Build a 'paragraph' element in the shape extract_paragraph_metadata returns."""
    return {
        "type": "paragraph",
        "text": text,
        "style": style,
        "outline_level": None,
        "list_level": None,
        "num_fmt": None,
        "indent_left": 0.0,
        "indent_first": 0.0,
        "alignment": alignment,
        "bold": bold,
        "italic": False,
        "underline": False,
        "font_size": None,
        "font_color": "#000000",
    }


def _empty():
    """A blank line between a bold label and its list, as extract_unified_elements
    emits for a paragraph with no text. This is what keeps the label's next
    sibling from being a bold==False paragraph, so the 'bold_before_plain'
    branch (which would otherwise set level=2 outright) never fires -- the
    exact real-world layout #399 fragments on."""
    return {"type": "empty", "text": "", "is_empty": True}


def _citation(text):
    return _paragraph(text, bold=False)


def _structure(qualifier):
    elements = [
        _paragraph("Publications", bold=True),
        _paragraph(qualifier, bold=True),
        _empty(),
        _citation("Smith J, Doe A. Some citation title. Journal. 2020."),
    ]
    return {"elements": elements}


def _levels(headers):
    return {h["text"]: h["level"] for h in headers}


@pytest.mark.parametrize("qualifier", QUALIFIER_TERMS)
def test_qualifier_demoted_off_level_1(qualifier):
    headers = detect_section_headers(_structure(qualifier))
    levels = _levels(headers)

    assert levels["Publications"] == 1, "the real parent section must stay level 1"
    # Publications (L1) is the only preceding non-subsection header, so the
    # demoted qualifier's exact expected level is L2 -- not merely "not L1".
    assert levels[qualifier] == 2, levels


@pytest.mark.parametrize("qualifier", QUALIFIER_TERMS)
def test_demotion_signal_recorded(qualifier):
    # The demotion should be visible in 'signals' for debugging, same as the
    # already-working temporal terms (e.g. "Current", "Past").
    headers = detect_section_headers(_structure(qualifier))
    by_text = {h["text"]: h for h in headers}

    assert "demoted_subsection" in by_text[qualifier]["signals"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
