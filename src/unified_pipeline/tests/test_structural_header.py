"""Issue #1709: the structural-header timestamp check recodes only real document timestamps to T.

Synthetic rows only. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_structural_header.py -p no:cacheprovider
"""

import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.validators.structural_header import (  # noqa: E402
    MAX_TIMESTAMP_CHARS,
    StructuralHeaderValidator,
    apply_structural_corrections,
    is_timestamp,
)

_ASOF_CITATION = "Asofa J, Doe B: Cell interactions in a response. J Example 1:10-20, 1971."
_REVISED_CITATION = "Revised criteria for a disease. J Example 2001;1:1-5."


@pytest.mark.parametrize("text", [
    # Every keyword alternative.
    "Updated: January 2024",
    "Revised May 2019",
    "Last updated 8/2/2024",
    "Last revised 2023",
    "Last modified: 3/1/2022",
    "As of August 10, 2022",
    "Current as of March 2024",
    "Version 3",
    "Version: 2.1",
    # "Date" between the keyword and the date.
    "Revised Date:   06/04/21  Page",
    "Revised Date2020",
    # A dashed date after a colon is not a volume:pages range.
    "Updated: 12-03-2024",
    # Every month abbreviation.
    "Updated Jan 2024",
    "Updated Feb 2024",
    "Updated Mar 2024",
    "Updated Apr 2024",
    "Updated May 2024",
    "Updated Jun 2024",
    "Updated Jul 2024",
    "Updated Aug 2024",
    "Updated Sep 2024",
    "Updated Oct 2024",
    "Updated Nov 2024",
    "Updated Dec 2024",
    # Upper case.
    "UPDATED: JANUARY 2024",
])
def test_timestamps(text):
    assert is_timestamp(text)


@pytest.mark.parametrize("text", [
    # The keyword must be a whole word.
    _ASOF_CITATION,
    "Asof 2024",
    "Updatedness 2024",
    "Versions 2 and 3",
    "Revisedly 2024",
    "Version1 Software, consultant 2019",
    # A date must follow the keyword.
    _REVISED_CITATION,
    "Revised master's level course for a new program",
    "Updated guidelines",
    "As of now",
    "Version control",
    "Revised Date: unknown",
    # Too long to be a timestamp line.
    "As of 2020, chaired the departmental committee on curriculum review and planning",
    # A citation's volume:pages range, with a hyphen or an en dash.
    "Updated 2015 guidelines. J Example 2016;12:1-5.",
    "Updated 2015 guidelines. J Example 2016;12:1–5.",
    "Updated 2015 guidelines. J Example 2016;12 : 10 - 15.",
])
def test_not_timestamps(text):
    assert not is_timestamp(text)


def test_length_cap_boundary():
    at_cap = "Updated 2024" + "." * (MAX_TIMESTAMP_CHARS - len("Updated 2024"))
    assert is_timestamp(at_cap)
    assert not is_timestamp(at_cap + ".")


@pytest.mark.parametrize(("text", "code"), [
    (_ASOF_CITATION, "S2"),
    (_REVISED_CITATION, "S1"),
])
def test_citations_keep_their_code(text, code):
    entries, stats = apply_structural_corrections([{"text": text, "taxonomy_code": code}])
    assert entries[0]["taxonomy_code"] == code
    assert "structural_correction" not in entries[0]
    assert stats["corrections_made"] == 0


@pytest.mark.parametrize("text", ["Updated: January 2024", "Last revised 2023", "As of March 2024"])
def test_timestamps_become_t(text):
    entries, stats = apply_structural_corrections([{"text": text, "taxonomy_code": "A"}])
    assert entries[0]["taxonomy_code"] == "T"
    assert entries[0]["structural_correction"]["original_code"] == "A"
    assert stats["corrections_made"] == 1


def test_pre_classification_guidance_uses_the_same_check():
    validator = StructuralHeaderValidator()
    assert validator.analyze("As of March 2024").recommend_sections == ["T"]
    assert not validator.analyze(_ASOF_CITATION).recommend_sections
