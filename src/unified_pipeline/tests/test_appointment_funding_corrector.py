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
from unified_pipeline.core.validators.hierarchy_mismatch_flagger import (  # noqa: E402
    is_funding_code,
    is_position_code,
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
    # Each FUNDING_SHAPED alternative as the only evidence on the row.
    "Research study\t2018-2020\t$250,000",
    "R01 HL000000\t2014-2019",
    "Career development\t2016-2021\tK23",
    "Outcomes study\t2012-2014\tNIH",
    "Comparative effectiveness study\t2019-2022\tPCORI",
    "Materials study\t2019-2022\tNSF",
    "Quality study\t2019-2022\tAHRQ",
    "Contracts No. 4471\t2019-2021",
    "Multicenter trial\t2016-2020\t10% indirect cost",
    "Project Number 12345\t2024-2029",
    "Project # 4471\t2019-2021",
    "Contract No 4471\t2019-2021",
    "Contract # 4471\t2019-2021",
    "Award No. 4471\t2019-2021",
    "Grant #4471\t2019-2021",
    "Outcomes study\t2012-2014\tRole: PI",
    "Multicenter aneurysm trial\t2015-2019\tRole: MPI",
    "Thrombectomy registry\t2020-2023\tRole: Dual-PI",
    "Multicenter trial\t2016-2020\tRole: Co-I",
    "Multicenter trial\t2016-2020\tRole: CoI",
    "Multicenter trial\t2016-2020\tPrincipal Investigator",
    "Multicenter trial\t2016-2020\tCo-Investigator",
    "Multicenter trial\t2016-2020\t10% direct costs",
    # Two distinct funding words.
    "Early Career Award, Example Foundation\t2012\tgrant",
    "Visiting scientist\t2014\tsponsored by the agency",
    "Research scholar\t2014\tfunded; investigator",
    "Research associate\t2014\tsponsorship from the Example Awards Program",
    "Visiting scholar\t2014\tawarded by the agency",
    "Research scholar\t2014\tgrant funding",
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
    # One funding word is ordinary appointment prose, not evidence.
    "Assistant Professor\t2015\tfunded by departmental start-up",
    "Visiting Fellow\t2014\tSponsored by Dr. Lee",
    "Research Administrator, Grants and Contracts Office\t2016",
    "Liaison, Example agency\t2019-2021",
    "Research associate\t2014\tExample Awards Program",
    "Research Investigator\t2018-2020",
    # The same word twice is still one word.
    "Grants Manager, Grant Office\t2012",
    # Lower-case acronyms and roles are not roles or funders.
    "Lab manager, pi lab\t2012",
    "Staff, coi disclosure office\t2012",
    "Visiting scientist, nih campus\t2012",
    # A number word must be the whole word.
    "Contract Notice reviewer\t2012",
    "Project Numbering lead\t2012",
    # Lower-case mechanism-shaped tokens are not NIH mechanisms.
    "Research fellow, p53 lab\t2012",
    # Funders that are also employers.
    "EIS Officer, CDC\t2012",
    "Staff physician, DOD\t2012",
])
def test_no_funding_evidence(text):
    assert not has_funding_evidence(text)


@pytest.mark.parametrize("code", ["D", "D1", "D2", "D3"])
def test_position_codes(code):
    """Pins the flagger's position family that is_positions_heading relies on."""
    assert is_position_code(code)
    assert not is_funding_code(code)


@pytest.mark.parametrize("code", ["M2", "M2A", "M2B", "M2C", "M2D"])
def test_funding_codes(code):
    """Pins the flagger's funding family; M2D (patents) shares the M2 section."""
    assert is_funding_code(code)
    assert not is_position_code(code)


@pytest.mark.parametrize("code", ["O", "P", "L3", "T", "Q2", "N3", "M1"])
def test_other_codes_are_neither(code):
    assert not is_position_code(code)
    assert not is_funding_code(code)


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
    detail = stats["correction_details"][0]
    assert detail["element_idx"] == 3
    assert detail["correction"] == out[0]["appointment_funding_correction"]
    assert detail["text_preview"] == _APPLICANT[:100]


def test_text_preview_is_capped_at_100_chars():
    long_text = _APPLICANT + " " + "x" * 200
    _, stats = apply_appointment_funding_corrections([_entry(long_text)])
    assert stats["correction_details"][0]["text_preview"] == long_text[:100]


def test_none_text_and_hierarchy_are_tolerated():
    """A row with no text under a positions heading has no evidence -> T; no hierarchy -> untouched."""
    out, stats = apply_appointment_funding_corrections([
        {"text": None, "taxonomy_code": "M2B", "hierarchy": ["ACADEMIC APPOINTMENTS"]},
        {"text": _APPLICANT, "taxonomy_code": "M2B", "hierarchy": None},
    ])
    assert [e["taxonomy_code"] for e in out] == ["T", "M2B"]
    assert stats["correction_details"][0]["text_preview"] == ""


def test_first_original_code_is_kept():
    """An entry an earlier corrector already recoded keeps its true original code."""
    entry = _entry(_APPLICANT, code="M2B")
    entry["original_taxonomy_code"] = "M2C"
    assert correct_appointment_funding(entry)["original_taxonomy_code"] == "M2C"


def test_an_earlier_pass_is_not_recounted():
    """A row that already carries the correction key from a previous pass is not a new correction."""
    earlier, _ = apply_appointment_funding_corrections([_entry(_APPLICANT)])
    out, stats = apply_appointment_funding_corrections(earlier)
    assert out[0]["taxonomy_code"] == "T"
    assert stats["corrections_applied"] == 0
