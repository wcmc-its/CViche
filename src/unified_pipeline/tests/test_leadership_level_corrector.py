"""Tests for core/validators/leadership_level_corrector.py.

The corrector recodes a stage-3b O (Institutional Leadership) entry to P
(Institutional Administrative Activities) when its text names a committee or
service role and no executive title. #1707: it no longer demotes a program
director or a "Co-Director, <program>", which the stage-3b prompt's rule 17
codes O. All entry texts are synthetic.
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.validators.leadership_level_corrector import (  # noqa: E402
    apply_leadership_level_corrections,
    correct_leadership_level,
)


def _entry(text: str, code: str = "O") -> dict:
    return {"taxonomy_code": code, "text": text, "element_idx_start": 7}


# #1707: training-program and clinical-program directors keep the model's O.
_STAYS_O = (
    "Interim Program Director, Example Residency Program",
    "Associate Program Director, Example Internal Medicine Residency Program",
    "Assistant Residency Program Director\tExample Medical Center",
    "Example Fellowship Program Director    2020-present",
    "Program Director of Example Simulation     2008- Present",
    "Program Director,\tExample Outpatient Clinic\tExample University",
    "Program Director, Example T32 Training Program",
    "Co-Director, Example Resident Education",
    "Co-Director, Example Fellowship Program",
    "Co-Director, NIH (T32HD000000) Example Training Program",
    "Co-Director, Example University Center for Example Studies",
    # Executive titles were never demoted; still pinned.
    "Department Chair, Department of Example Medicine",
    "Vice Chair for Research, Department of Example",
)


@pytest.mark.parametrize("text", _STAYS_O)
def test_program_and_executive_directors_stay_o(text):
    out = correct_leadership_level(_entry(text))
    assert out["taxonomy_code"] == "O"
    assert "leadership_level_correction" not in out
    assert "original_taxonomy_code" not in out


# The demotions the corrector exists for are kept.
_DEMOTED_TO_P = (
    ("Member, Example Task Force", "Task Force"),
    ("Chair, Curriculum Committee (2019-present)", "Chair, Curriculum Committee"),
    ("Search Committee Chair 2009 Example University", "Committee Chair"),
    ("Treasurer, Example Faculty Practice Plan", "Treasurer"),
    ("Example Track Director, Example Graduate Program", "Track Director"),
    ("Director of Epidemiology Track, Example Graduate Program", "Director of Epidemiology Track"),
    ("Example Doctoral Program Coordinator, 2017-2019", "Coordinator"),
)


@pytest.mark.parametrize(("text", "matched"), _DEMOTED_TO_P)
def test_committee_and_service_roles_still_demoted_to_p(text, matched):
    out = correct_leadership_level(_entry(text))
    assert out["taxonomy_code"] == "P"
    assert out["original_taxonomy_code"] == "O"
    assert out["leadership_level_correction"]["from"] == "O"
    assert out["leadership_level_correction"]["to"] == "P"
    assert f"'{matched}'" in out["leadership_level_correction"]["reason"]


def test_non_o_entry_is_untouched():
    entry = _entry("Member, Example Task Force", code="P")
    assert correct_leadership_level(entry) is entry


def test_apply_counts_only_the_demotions():
    entries = [
        _entry("Interim Program Director, Example Residency Program"),
        _entry("Co-Director, Example Resident Education"),
        _entry("Member, Example Task Force"),
        _entry("Program Director, Example Residency Program", code="K3"),
    ]
    corrected, stats = apply_leadership_level_corrections(entries)
    assert [e["taxonomy_code"] for e in corrected] == ["O", "O", "P", "K3"]
    assert stats["o_entries_reviewed"] == 3
    assert stats["corrections_applied"] == 1
    assert stats["correction_details"][0]["text_preview"] == "Member, Example Task Force"
