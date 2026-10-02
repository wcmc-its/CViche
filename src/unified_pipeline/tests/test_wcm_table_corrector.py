"""Issue #1235: the board-certification signature needs more than the board's name.

Synthetic rows only. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_wcm_table_corrector.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.validators.wcm_table_corrector import (  # noqa: E402
    apply_wcm_table_corrections,
)


def _code_after(text, hierarchy, code="I"):
    entries = [{"text": text, "hierarchy": hierarchy, "taxonomy_code": code}]
    apply_wcm_table_corrections(entries)
    return entries[0]["taxonomy_code"]


@pytest.mark.parametrize(
    "text,hierarchy,code",
    [
        ("Diplomate, American Board of Example Medicine, 1999", ["Memberships"], "I"),
        ("Board Certified, American Board of Example Medicine, 2001", ["Education"], "H"),
        ("Board-Certified Specialist by the American Board of Example Therapy", ["Clinical"], "H"),
        ("American Board of Example Medicine, Certificate No. 12345", ["Societies"], "I"),
        ("Recertification, American Board of Example Medicine, 2015-2025", ["Education"], "H"),
        ("American Board of Example Medicine, 1999", ["Education", "Certifications"], "I"),
        ("Certificate # A12345 Example Board", ["Licensure"], "I"),
    ],
)
def test_genuine_board_certification_still_becomes_f2(text, hierarchy, code):
    assert _code_after(text, hierarchy, code) == "F2"


@pytest.mark.parametrize(
    "text,hierarchy,code",
    [
        ("American Board of Example Medicine, Exam Committee, Example Service Award, 2016", ["Awards"], "H"),
        ("American Board of Example Medicine, Example Service Award, 2016", ["Education"], "H"),
        ("American Board of Example Medicine", ["Professional Societies"], "I"),
        ("American Board of Example Medicine: Member", ["Membership in Professional Organizations"], "I"),
        ("American Board of Example Medicine, 2019", ["Honors and Awards"], "H"),
        ("American Board of Example Medicine", ["Committees"], "C"),
    ],
)
def test_board_name_alone_or_award_context_is_not_flipped(text, hierarchy, code):
    assert _code_after(text, hierarchy, code) == code


def test_empty_table_header_row_is_not_promoted():
    assert _code_after("Full Name of Board  Certificate #", ["Certifications"]) == "I"
