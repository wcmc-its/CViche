"""Tests for the WCM-template instruction boilerplate detector (issue #141).

Pure-function tests: no DB, no FastAPI app, no backend conftest. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_template_boilerplate.py -p no:cacheprovider
"""

import sys
from pathlib import Path

# Ensure the repo's ``src`` directory is importable regardless of cwd/rootdir,
# so this test does not depend on the backend package layout or its conftest.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.template_boilerplate import is_template_instruction  # noqa: E402


# Actual boilerplate that leaked into run B2RRRA. MUST be detected (return True).
POSITIVES = [
    "When preparing the WCM CV template, below, please keep the following in mind",
    "Please include title/audience/dates as applicable for each prompt below",
    "Didactic teaching (lectures, seminars, tutorials,)",
    "Clinical teaching (bedside teaching, teaching rounds, teaching in operating room, "
    "precepting in clinic, morning report, etc.)",
    "Duplicate table below as needed. For each funding vehicle, please include the following:",
    "Please list trainees and faculty that you have formally supervised",
    "Full Name of Board | Certificate # (indicate if board eligible)",
    "Name of Committee | Role (i.e., member, secretary, etc.)",
]

# Real CV content. MUST NOT be dropped (return False).
NEGATIVES = [
    "M.D. | Boston University School of Medicine | 08/1997-06/2001",
    "Fellow, OB/GYN – PGY 5-7 Urogynecology & Reconstructive Pelvic Surgery",
    "American Board of Obstetrics & Gynecologists: Female Pelvic Medicine and "
    "Reconstructive Surgery",
    "Northwell Health – Staten Island University Hospital",
]


def test_positives_are_detected():
    for text in POSITIVES:
        assert is_template_instruction(text) is True, f"expected DROP for: {text!r}"


def test_negatives_are_kept():
    for text in NEGATIVES:
        assert is_template_instruction(text) is False, f"expected KEEP for: {text!r}"


def test_empty_and_none_safe():
    assert is_template_instruction("") is False
    assert is_template_instruction(None) is False
    assert is_template_instruction("   ") is False


def test_bullet_and_list_prefixes_normalized():
    # Leading bullets / list numbers / asterisks must not defeat detection.
    assert is_template_instruction("• Didactic teaching (lectures, seminars, tutorials,)") is True
    assert is_template_instruction("1. Please list trainees and faculty that you have formally supervised") is True
    assert is_template_instruction("* Duplicate table below as needed. For each funding vehicle, please include the following:") is True


def test_section_headers_are_not_dropped():
    # Top-level WCM section headers must be KEPT (not in the drop set).
    for header in ["PERSONAL DATA", "EDUCATION", "BIBLIOGRAPHY", "RESEARCH", "MENTORING"]:
        assert is_template_instruction(header) is False, f"section header dropped: {header!r}"


def test_pipe_cell_match_requires_known_cell():
    # A pipe-joined string with a known template cell is dropped...
    assert is_template_instruction("Full Name of Board | Some real data") is True
    # ...but a pipe-joined string of purely real content is kept.
    assert is_template_instruction("M.D. | Harvard | 2001") is False


def _apply_layer1_filter(entries):
    """Mirror the Layer 1 (stage 2) filter: drop entries whose text is boilerplate."""
    return [e for e in entries if not is_template_instruction(e.get("text", ""))]


def test_layer1_integration_filter():
    """Synthesize a mixed entries list and apply the Layer 1 filter logic."""
    positive_entries = [
        {"text": t, "element_type": "table_row", "element_idx_start": i, "hierarchy": ["RESEARCH"]}
        for i, t in enumerate(POSITIVES)
    ]
    negative_entries = [
        {"text": t, "element_type": "table_row", "element_idx_start": 100 + i, "hierarchy": ["EDUCATION"]}
        for i, t in enumerate(NEGATIVES)
    ]
    # Also include a section-header entry, which must be kept.
    header_entry = {"text": "EDUCATION", "element_type": "header", "element_idx_start": 5, "hierarchy": []}

    entries = positive_entries + negative_entries + [header_entry]
    kept = _apply_layer1_filter(entries)
    kept_texts = {e["text"] for e in kept}

    # All positives dropped.
    for t in POSITIVES:
        assert t not in kept_texts, f"positive should have been dropped: {t!r}"
    # All negatives kept.
    for t in NEGATIVES:
        assert t in kept_texts, f"negative should have been kept: {t!r}"
    # Section header kept.
    assert "EDUCATION" in kept_texts
    # Total kept = negatives + header.
    assert len(kept) == len(NEGATIVES) + 1


def test_entries_without_text_key_do_not_crash():
    entries = [{"element_type": "break", "element_idx_start": 0}, {"text": "EDUCATION"}]
    kept = _apply_layer1_filter(entries)
    # The entry lacking "text" is treated as empty -> kept; header kept too.
    assert len(kept) == 2
