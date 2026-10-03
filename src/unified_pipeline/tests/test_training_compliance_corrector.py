"""core/validators/training_compliance_corrector.py: P -> B2 for trainings received.

#312 (EBYSBC KDAZOM-10): a committee, board or council named after a training
topic is service on that body, not a course taken. Synthetic rows only.
"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.validators.training_compliance_corrector import (  # noqa: E402
    apply_training_compliance_corrections,
)


def _code_after(text, code="P"):
    (entry,), _stats = apply_training_compliance_corrections([{"text": text, "taxonomy_code": code}])
    return entry["taxonomy_code"]


@pytest.mark.parametrize("text", [
    "2031 Radiation Safety training, Example University",
    "Title IX Non-Discrimination Training, 2031",
    "2031 HIPAA Training (annual)",
])
def test_trainings_received_become_b2(text):
    assert _code_after(text) == "B2"


@pytest.mark.parametrize("text", [
    "Radiation Safety Committee member, Example Hospital, 2031-2040",
    "Example Hospital Radiation Safety Board, member 2031",
    "2031 Implicit Bias Council, Example School of Medicine",
])
def test_service_on_a_named_body_stays_p(text):
    assert _code_after(text) == "P"


def test_facilitated_training_stays_p():
    assert _code_after("Facilitator, Unconscious Bias training, 2031") == "P"


def test_only_p_rows_are_reviewed():
    assert _code_after("2031 HIPAA Training", code="K4") == "K4"
