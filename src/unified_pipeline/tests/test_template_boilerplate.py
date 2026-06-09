"""Tests for the WCM-template instruction boilerplate detector (issue #141).

Pure-function tests: no DB, no FastAPI app, no backend conftest. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_template_boilerplate.py -p no:cacheprovider

The detector is PRECISION-BIASED: dropping real CV content is the cardinal sin,
so the bulk of these tests pin the false-positive surface (NEGATIVES) that an
adversarial review of the first implementation found to be over-broad.
"""

import sys
from pathlib import Path

import pytest

# Ensure the repo's ``src`` directory is importable regardless of cwd/rootdir,
# so this test does not depend on the backend package layout or its conftest.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.template_boilerplate import (  # noqa: E402
    is_template_instruction,
    filter_template_instructions,
)


# --- Boilerplate that leaked into run B2RRRA. MUST be detected (return True). ---
POSITIVES = [
    # Full directive sentences (exact match).
    "When preparing the WCM CV template, below, please keep the following in mind",
    "Duplicate table below as needed. For each funding vehicle, please include the following:",
    # Parenthetical subcategory prompts (exact match).
    "Didactic teaching (lectures, seminars, tutorials,)",
    "Clinical teaching (bedside teaching, teaching rounds, teaching in operating room, "
    "precepting in clinic, morning report, etc.)",
    "Research Activities: In a paragraph or bullet points (up to 300 words), briefly "
    "highlight your various research interests",
    # Field-label row whose distinctive cell (>= the exact-match floor) is template text.
    "Name of Committee | Role (i.e., member, secretary, etc.)",
    # Verbatim FRAGMENTS of long template paragraphs the faculty left in
    # (caught by reverse-containment, not exact match).
    "Please include title/audience/dates as applicable for each prompt below",
    "Please list trainees and faculty that you have formally supervised",
]

# --- Real CV content. MUST NOT be dropped (return False). ---
# This list is the heart of the suite: each group is a false-positive class the
# review reproduced against the first implementation.
NEGATIVES = [
    # Plain real entries.
    "M.D. | Boston University School of Medicine | 08/1997-06/2001",
    "Fellow, OB/GYN – PGY 5-7 Urogynecology & Reconstructive Pelvic Surgery",
    "American Board of Obstetrics & Gynecologists: Female Pelvic Medicine and "
    "Reconstructive Surgery",
    "Northwell Health – Staten Island University Hospital",
    # Pipe-joined REAL table rows whose last cell collides with a short template
    # label (Teaching/National/Regional/Administrative/Research/State). Real data
    # in the other cells must keep the row.
    "Professor of Surgery | Weill Cornell | Teaching",
    "Grant Reviewer | NIH CSR | National",
    "Awardee | American Heart Association | Regional",
    "Smith, J | Harvard | Research",
    "Director | Dept of Medicine | Administrative",
    "License # | State | Date",
    # Bare single-word entries that collide with template field labels.
    "Teaching", "Clinical", "Administrative", "Research",
    "International", "National", "Regional",
    "Total", "Organization", "Title", "Mentees", "Description", "State", "Number",
    # Bibliography category headings faculty legitimately use (protected).
    "Peer-reviewed Research Articles", "Reviews and Editorials",
    "Non-peer-reviewed Research Publications", "Books", "Chapters",
    # Real prose that merely opens with a directive-sounding word.
    "Please include me in the cc list.",
    "Include year-over-year revenue growth in the model",
    "List trainees mentored: 14 PhD students",
    "Number the entries were 200 in total",
]


@pytest.mark.parametrize("text", POSITIVES)
def test_positives_are_detected(text):
    assert is_template_instruction(text) is True, f"expected DROP for: {text!r}"


@pytest.mark.parametrize("text", NEGATIVES)
def test_negatives_are_kept(text):
    assert is_template_instruction(text) is False, f"expected KEEP for: {text!r}"


def test_empty_and_none_safe():
    assert is_template_instruction("") is False
    assert is_template_instruction(None) is False
    assert is_template_instruction("   ") is False


def test_bullet_and_list_prefixes_normalized():
    # Leading bullets / list numbers / asterisks must not defeat detection.
    assert is_template_instruction("• Didactic teaching (lectures, seminars, tutorials,)") is True
    assert is_template_instruction(
        "1. Please list trainees and faculty that you have formally supervised"
    ) is True
    assert is_template_instruction(
        "* Duplicate table below as needed. For each funding vehicle, please include the following:"
    ) is True


def test_section_headers_are_not_dropped():
    for header in ["PERSONAL DATA", "EDUCATION", "BIBLIOGRAPHY", "RESEARCH", "MENTORING"]:
        assert is_template_instruction(header) is False, f"section header dropped: {header!r}"


def test_containment_with_appended_faculty_text():
    # A full template sentence with extra faculty text appended -> still dropped
    # (containment of the known sentence inside the entry).
    text = ("Duplicate table below as needed. For each funding vehicle, please "
            "include the following: (we currently hold 3 grants)")
    assert is_template_instruction(text) is True


def test_pipe_rows_only_drop_on_a_distinctive_template_cell():
    # Drops when a cell is a distinctive (long) template label...
    assert is_template_instruction("Name of Committee | Role (i.e., member, secretary, etc.)") is True
    # ...but a real row whose only colliding cell is a SHORT generic label is kept...
    assert is_template_instruction("Professor of Surgery | Weill Cornell | Teaching") is False
    # ...and a purely real row is kept.
    assert is_template_instruction("M.D. | Harvard | 2001") is False


def test_layer1_integration_filter():
    """Mirror the Layer 1 (stage 2) filter on a synthesized mixed entries list."""
    def layer1(entries):
        return [e for e in entries if not is_template_instruction(e.get("text", ""))]

    positive_entries = [
        {"text": t, "element_type": "table_row", "element_idx_start": i, "hierarchy": ["RESEARCH"]}
        for i, t in enumerate(POSITIVES)
    ]
    negative_entries = [
        {"text": t, "element_type": "table_row", "element_idx_start": 100 + i, "hierarchy": ["EDUCATION"]}
        for i, t in enumerate(NEGATIVES)
    ]
    header_entry = {"text": "EDUCATION", "element_type": "header", "element_idx_start": 5, "hierarchy": []}

    kept = layer1(positive_entries + negative_entries + [header_entry])
    kept_texts = {e["text"] for e in kept}

    for t in POSITIVES:
        assert t not in kept_texts, f"positive should have been dropped: {t!r}"
    for t in NEGATIVES:
        assert t in kept_texts, f"negative should have been kept: {t!r}"
    assert "EDUCATION" in kept_texts
    assert len(kept) == len(NEGATIVES) + 1


def test_layer3_appendix_filter_and_collapse():
    """Layer 3 backstop semantics via filter_template_instructions: boilerplate
    is removed, real content kept, and an all-boilerplate set collapses to []."""
    # Mixed -> only negatives survive.
    survivors = filter_template_instructions(POSITIVES + NEGATIVES)
    assert set(survivors) == set(NEGATIVES)
    # All-boilerplate -> empty (drives the empty-Appendix early-return path).
    assert filter_template_instructions(POSITIVES) == []
    # All-real -> unchanged.
    assert filter_template_instructions(NEGATIVES) == list(NEGATIVES)


def test_entries_without_text_key_do_not_crash():
    def layer1(entries):
        return [e for e in entries if not is_template_instruction(e.get("text", ""))]

    entries = [{"element_type": "break", "element_idx_start": 0}, {"text": "EDUCATION"}]
    kept = layer1(entries)
    # The entry lacking "text" is treated as empty -> kept; header kept too.
    assert len(kept) == 2
