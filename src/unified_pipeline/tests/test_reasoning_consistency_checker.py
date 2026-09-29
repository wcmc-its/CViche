"""Tests for core/validators/reasoning_consistency_checker.py (#626).

This module had no test file; this is its first. Drives the same entry point
stage 3b calls (apply_reasoning_corrections), not just the code list.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.validators.reasoning_consistency_checker import (
    apply_reasoning_corrections,
)


def test_reasoning_pointing_at_c3_records_c3_conflict_on_generic_c():
    entries = [{
        "text": "Clinical Fellow, Example Hospital, 2019-2021",
        "taxonomy_code": "C",
        "classification_reasoning": "A clinical fellowship, so C3 is appropriate.",
    }]
    corrected, stats = apply_reasoning_corrections(entries)
    # A parent->child refinement scores 0.75, below the 0.80 auto-apply bar,
    # so the code is kept but the C3 suggestion is now recorded.
    assert corrected[0]["taxonomy_code"] == "C"
    assert stats["conflicts_found"] == 1
    assert corrected[0]["reasoning_conflict_detected"]["suggested_code"] == "C3"


def test_reasoning_pointing_at_c3_corrects_a_mismatched_sibling():
    entries = [{
        "text": "Fellow in Cardiology, Example Hospital",
        "taxonomy_code": "C2",
        "classification_reasoning": "Fellowship rather than residency; should be C3.",
    }]
    corrected, _ = apply_reasoning_corrections(entries)
    assert corrected[0]["taxonomy_code"] == "C3"
