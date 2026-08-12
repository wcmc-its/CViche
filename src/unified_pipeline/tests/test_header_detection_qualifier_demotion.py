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

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_header_detection_qualifier_demotion.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.segmentation.header_detection import detect_section_headers  # noqa: E402


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


def _structure():
    elements = [
        _paragraph("Publications", bold=True),
        _paragraph("Selected", bold=True),
        _empty(),
        _citation("Smith J, Doe A. Some citation title. Journal. 2020."),
        _paragraph("Representative", bold=True),
        _empty(),
        _citation("Doe A, Smith J. Another citation title. Journal. 2021."),
    ]
    return {"elements": elements}


def _levels(headers):
    return {h["text"]: h["level"] for h in headers}


def test_selected_and_representative_demoted_off_level_1():
    headers = detect_section_headers(_structure())
    levels = _levels(headers)

    assert levels["Publications"] == 1, "the real parent section must stay level 1"
    assert levels["Selected"] in (2, 3), levels
    assert levels["Representative"] in (2, 3), levels


def test_demotion_signal_recorded():
    # The demotion should be visible in 'signals' for debugging, same as the
    # already-working temporal terms (e.g. "Current", "Past").
    headers = detect_section_headers(_structure())
    by_text = {h["text"]: h for h in headers}

    assert "demoted_subsection" in by_text["Selected"]["signals"]
    assert "demoted_subsection" in by_text["Representative"]["signals"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")
