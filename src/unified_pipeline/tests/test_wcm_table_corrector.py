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
        ("Diplomate, American Board of Example Medicine, 2031", ["Memberships"], "I"),
        ("Board Certified, American Board of Example Medicine, 2032", ["Education"], "H"),
        ("Board-Certified Specialist by the American Board of Example Therapy", ["Clinical"], "H"),
        ("American Board of Example Medicine, Certificate No. 12345", ["Societies"], "I"),
        ("Recertification, American Board of Example Medicine, 2033-2043", ["Education"], "H"),
        ("American Board of Example Medicine, 2031", ["Education", "Certifications"], "I"),
        ("Certificate # A12345 Example Board", ["Licensure"], "I"),
        ("Diplomat | American Board of Example Medicine | 8/2031-present", ["Professional Memberships"], "I"),
        ("Certified by the American Board of Example Medicine", ["Professional Memberships"], "I"),
        ("American Board of Example Medicine, certified 2034", ["Professional Memberships"], "I"),
    ],
)
def test_genuine_board_certification_still_becomes_f2(text, hierarchy, code):
    assert _code_after(text, hierarchy, code) == "F2"


@pytest.mark.parametrize(
    "text,hierarchy,code",
    [
        ("American Board of Example Medicine, Exam Committee, Example Service Award, 2037", ["Awards"], "H"),
        ("American Board of Example Medicine, Example Service Award, 2037", ["Education"], "H"),
        ("American Board of Example Medicine", ["Professional Societies"], "I"),
        ("American Board of Example Medicine: Member", ["Membership in Professional Organizations"], "I"),
        ("American Board of Example Medicine, 2038", ["Honors and Awards"], "H"),
        ("American Board of Example Medicine", ["Committees"], "C"),
        ("Board Certification Examination Committee, American Board of Example Medicine, 2036", ["Honors and Awards"], "H"),
        ("American Board of Example Medicine, Recertification Examination Committee, 2035-2039", ["Committees"], "I"),
        ("American Board of Example Medicine Service Awards, 2037", ["Education"], "H"),
        ("Diplomatic liaison, American Board of Example Medicine", ["Education"], "I"),
    ],
)
def test_board_name_alone_or_award_context_is_not_flipped(text, hierarchy, code):
    assert _code_after(text, hierarchy, code) == code


def test_empty_table_header_row_is_not_promoted():
    assert _code_after("Full Name of Board  Certificate #", ["Certifications"]) == "I"


# --- #312 (EBYSBC E11): board abbreviations, the national board, and B2 rows ---

@pytest.mark.parametrize(
    "text,hierarchy,code",
    [
        ("ABIM, Example Subspecialty, 2031, 2041", ["Experience"], "I"),
        ("ABXY, Example Subspecialty, 2031", ["Experience"], "I"),
        ("Certifications: Diplomate, National Board of Example Examiners, 2031", ["Experience"], "I"),
        ("2031 American Board of Example Medicine", ["Educational History"], "B2"),
        # A short leading parenthetical (the diplomate mark) before the abbreviation.
        ("(D)ABXY - Certified in Example Specialty, 2031-current", ["Service", "Community"], "H"),
    ],
)
def test_board_abbreviation_national_board_and_b2_rows_become_f2(text, hierarchy, code):
    assert _code_after(text, hierarchy, code) == "F2"


@pytest.mark.parametrize(
    "text,hierarchy,code",
    [
        ("Example Residency Program, 2031-2033, ABIM short-track pathway", ["Training"], "C"),
        ("Abim Example Society, 2031", ["Memberships"], "I"),
        ("2031 American Board of Example Medicine review course", ["Courses"], "B2"),
        ("ABIM Example Award, 2031", ["Honors"], "H"),
        # The abbreviation is matched case-sensitively: an ordinary word is no board.
        ("About Example Topics, 2031", ["Experience"], "I"),
        # A board-review row is not a certification, course word or not.
        ("2031 American Board of Example Medicine item review panel", ["Educational History"], "B2"),
        # A course row naming a board, without the word "review", is not a certification either.
        ("2031 American Board of Example Medicine preparation course", ["Educational History"], "B2"),
        # A longer parenthetical before the abbreviation is not the diplomate mark.
        ("(Example) ABXY Example Society, 2031", ["Experience"], "I"),
    ],
)
def test_board_abbreviation_mid_line_or_in_award_context_is_not_flipped(text, hierarchy, code):
    assert _code_after(text, hierarchy, code) == code
