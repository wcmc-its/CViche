"""Issue #1912: a one-letter expected code of C (appointment, fellowship
headings) stripped to '' and matched every taxonomy code.

Synthetic rows only. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_hierarchy_mismatch_flagger.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.validators.hierarchy_mismatch_flagger import (  # noqa: E402
    code_matches_expected,
    flag_hierarchy_mismatches,
)


@pytest.mark.parametrize("code", ["S1", "Q2", "O", "M2B"])
def test_one_letter_c_does_not_match_an_unrelated_code(code):
    assert not code_matches_expected(code, ["C"])


@pytest.mark.parametrize("code", ["C", "C1", "C2"])
def test_one_letter_c_still_matches_its_own_family(code):
    assert code_matches_expected(code, ["C"])


@pytest.mark.parametrize("code, expected", [
    ("M2", ["M2A"]),
    ("Q4", ["Q4C"]),
    ("N3", ["N3B"]),
    # No family rule covers S8, so only the stem match can pass these.
    ("S8", ["S8A"]),
    ("S8", ["S8B"]),
    ("S8", ["S8C"]),
    ("D2", ["D", "C"]),
])
def test_stem_and_family_matches_are_kept(code, expected):
    assert code_matches_expected(code, expected)


def test_publication_under_appointments_heading_is_flagged():
    entry = {"text": "Doe J. A paper. J Example. 2020.", "taxonomy_code": "S1",
             "hierarchy": ["Administrative Appointments"], "element_idx_start": 7}
    _, stats = flag_hierarchy_mismatches([entry])
    assert stats["entries_flagged"] == 1
    assert entry["hierarchy_mismatch_flag"] is True


def test_position_under_appointments_heading_is_not_flagged():
    entry = {"text": "2019-  Associate Professor", "taxonomy_code": "D1",
             "hierarchy": ["Academic Appointments"], "element_idx_start": 8}
    _, stats = flag_hierarchy_mismatches([entry])
    assert stats["entries_flagged"] == 0
    assert "hierarchy_mismatch_flag" not in entry
