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


# --- #1577 shape 3: a signature on a line whose own words name another credential's home ---

def _entry_after(text, hierarchy, code):
    entries = [{"text": text, "hierarchy": hierarchy, "taxonomy_code": code}]
    apply_wcm_table_corrections(entries)
    return entries[0]


@pytest.mark.parametrize(
    "text,hierarchy,code,expected",
    [
        # A licensing exam the board signature reads as a board: B2, not F2.
        ("Diplomate, National Board of Medical Examiners, 2031", ["Education"], "H", "B2"),
        ("Certifications:   National Board of Medical Examiners  2031", ["Experience"], "I", "B2"),
        # Stage 3b's credential pin made these B2; the corrector leaves them there.
        ("Diplomate, National Board of Medical Examiners, 2031", ["Education"], "B2", "B2"),
        ("ATLS Certificate # 12345, 2031", ["Licensure"], "B2", "B2"),
        ("American Board of Example Surgery Part I written exam, passed 2031", ["Licensure"], "B2", "B2"),
        # A licence signature on an ECFMG line: B2, not F1.
        ("ECFMG Certificate (required for medical licensure), 2031", ["Credentials"], "T", "B2"),
        # Lines that are what the signature says still get its code.
        ("Medical License number 12345, Example State", ["Licensure"], "T", "F1"),
        ("Diplomate, American Board of Example Medicine, 2031", ["Memberships"], "I", "F2"),
    ],
)
def test_a_signature_defers_to_the_lines_credential_home_1577(text, hierarchy, code, expected):
    entry = _entry_after(text, hierarchy, code)
    assert entry["taxonomy_code"] == expected
    if expected != code:
        assert entry["wcm_table_correction"]["original_code"] == code
        if expected == "B2":
            assert "homed B2 as" in entry["wcm_table_correction"]["reason"]
    else:
        assert "wcm_table_correction" not in entry
