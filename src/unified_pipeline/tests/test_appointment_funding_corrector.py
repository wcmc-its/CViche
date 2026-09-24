"""Issue #946 item 5: an appointments-section row with no funding evidence never lands in M2.

Synthetic rows only. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_appointment_funding_corrector.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.validators.appointment_funding_corrector import (  # noqa: E402
    apply_appointment_funding_corrections,
    correct_appointment_funding,
    has_funding_evidence,
    is_positions_heading,
)

_APPLICANT = "2020-21  Applicant for Instructor of Medicine\tExample State University School of Medicine"


def _entry(text, code="M2C", hierarchy=("ACADEMIC APPOINTMENTS",)):
    return {"text": text, "taxonomy_code": code, "hierarchy": list(hierarchy), "element_idx_start": 3}


@pytest.mark.parametrize("code", ["M2", "M2A", "M2B", "M2C"])
def test_unevidenced_m2_under_appointments_goes_to_appendix(code):
    out = correct_appointment_funding(_entry(_APPLICANT, code=code))
    assert out["taxonomy_code"] == "T"
    assert out["original_taxonomy_code"] == code
    assert out["appointment_funding_correction"] == {
        "from": code, "to": "T", "reason": out["appointment_funding_correction"]["reason"]}


@pytest.mark.parametrize("heading", [
    ("ACADEMIC APPOINTMENTS",),
    ("PROFESSIONAL POSITIONS & EMPLOYMENT", "Academic Appointments"),
    ("Administrative Appointments",),
])
def test_positions_headings(heading):
    assert is_positions_heading(list(heading))


@pytest.mark.parametrize("heading", [
    ("RESEARCH POSITIONS AND FUNDING",),   # expects both D and M2
    ("GRANTS",),
    ("HONORS AND AWARDS",),
    (),
])
def test_non_positions_headings(heading):
    assert not is_positions_heading(list(heading))


def test_m2_under_a_funding_heading_is_untouched():
    out = correct_appointment_funding(_entry(_APPLICANT, hierarchy=("RESEARCH POSITIONS AND FUNDING",)))
    assert out["taxonomy_code"] == "M2C"


@pytest.mark.parametrize("text", [
    "Seed Grant, Example Institute\t2010-2011\tRole: PI",
    "Early Career Award, Example Foundation\t2012",
    "Industry Sponsored Research\t2015-2019\tRole: MPI",
    "Role: Co-I\tMulticenter trial\t2016-2020",
    "Multicenter aneurysm trial\t2015-2019\tRole: MPI",
    "Thrombectomy registry\t2020-2023\tRole: Dual-PI",
    "Outcomes study\t2012-2014\tRole: PI",
    "Project Number 12345\t2024-2029",
    "Example agency contract\t2019-2021",
    "Research study\t2018-2020\t$250,000",
    "R01 HL000000\t2014-2019",
    "Funded by the State\t2011-2013",
])
def test_funding_evidence_keeps_m2(text):
    assert has_funding_evidence(text)
    out = correct_appointment_funding(_entry(text, code="M2B"))
    assert out["taxonomy_code"] == "M2B"
    assert "appointment_funding_correction" not in out


@pytest.mark.parametrize("text", [
    _APPLICANT,
    "2009-2014  Research project on tendon healing",
    "2018-present  Core Faculty, Emergency Medicine Residency",
])
def test_no_funding_evidence(text):
    assert not has_funding_evidence(text)


def test_non_grant_codes_are_untouched():
    """M2D (patents) and position codes are not this corrector's decision."""
    for code in ("M2D", "D1", "T"):
        assert correct_appointment_funding(_entry(_APPLICANT, code=code))["taxonomy_code"] == code


def test_input_entry_is_not_mutated():
    entry = _entry(_APPLICANT)
    correct_appointment_funding(entry)
    assert entry["taxonomy_code"] == "M2C"


def test_apply_counts_and_details():
    entries = [_entry(_APPLICANT), _entry("Seed Grant\t2010\tRole: PI", code="M2B")]
    out, stats = apply_appointment_funding_corrections(entries)
    assert [e["taxonomy_code"] for e in out] == ["T", "M2B"]
    assert stats["corrections_applied"] == 1
    assert stats["correction_details"][0]["element_idx"] == 3
