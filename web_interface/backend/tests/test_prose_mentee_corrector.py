"""
Tests for the prose named-mentee corrector (core/validators/prose_mentee_corrector.py).

Fast, deterministic, no LLM. The corrector promotes a K-family teaching code to
N3A/N3B only when an entry under an advising/mentoring section names an
individual mentee. The central risk is a false positive on a mentoring *role*
that names no individual (legitimately K2), so most of these tests pin that the
corrector does NOT fire.
"""
import sys
from pathlib import Path

# Add src/ to path so unified_pipeline.core is importable.
# tests/ -> backend/ -> web_interface/ -> project_root/ -> src/
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from unified_pipeline.core.validators.prose_mentee_corrector import (  # noqa: E402
    apply_prose_mentee_corrections,
)

MENTOR_SECTION = ["ADVISING & MENTORING"]


def _entry(text, code="K2", hierarchy=None):
    return {"text": text, "taxonomy_code": code, "hierarchy": hierarchy if hierarchy is not None else MENTOR_SECTION}


def _apply_one(entry):
    entries, stats = apply_prose_mentee_corrections([entry])
    return entries[0], stats


# --- fires (true positives) -------------------------------------------------

def test_named_mentee_past_becomes_n3b():
    # The real EHMQSQ case: a named mentee, term ended.
    e, stats = _apply_one(_entry(
        "TEACH Program 2022 - May 2023  Mentor. Provided feedback to mentee Dr. Jane Smith."
    ))
    assert e["taxonomy_code"] == "N3B"
    assert stats["corrections_made"] == 1
    assert e["prose_mentee_correction"]["original_code"] == "K2"


def test_named_mentee_current_becomes_n3a():
    e, _ = _apply_one(_entry(
        "Research mentor for Dr. Alex Lee, 2021 - present. Weekly supervision."
    ))
    assert e["taxonomy_code"] == "N3A"


def test_mentee_keyword_with_title():
    e, _ = _apply_one(_entry("Directly supervised mentee Dr. Robilotti throughout the fellowship."))
    assert e["taxonomy_code"] in ("N3A", "N3B")


def test_mentored_named_individual():
    e, _ = _apply_one(_entry("Mentored Sarah Chen on her thesis, 2018 - 2020."))
    assert e["taxonomy_code"] == "N3B"


def test_fires_on_any_k_family_source():
    for code in ("K1", "K2", "K3", "K4", "K5"):
        e, _ = _apply_one(_entry("Mentor for Dr. Pat Doe, ongoing.", code=code))
        assert e["taxonomy_code"] == "N3A", f"expected fire from {code}"


# --- does NOT fire (false-positive guards) ----------------------------------

def test_bare_mentor_role_without_name_stays_k2():
    # Real adjudicated gold K2: a role label, no named individual.
    e, stats = _apply_one(_entry("2005-2007  Freshman Mentor and Teaching Assistant"))
    assert e["taxonomy_code"] == "K2"
    assert stats["corrections_made"] == 0


def test_mentoring_activity_without_name_stays_k2():
    e, _ = _apply_one(_entry(
        "Faculty coach, 2021 - present. Provided over 70 hours of peer coaching to faculty."
    ))
    assert e["taxonomy_code"] == "K2"


def test_group_program_without_name_stays_k2():
    e, _ = _apply_one(_entry(
        "Peer mentoring program, 2018 - present. Facilitated group mentoring to design careers."
    ))
    assert e["taxonomy_code"] == "K2"


def test_capitalized_non_name_is_not_a_mentee():
    # "mentee Program" must not look like a named individual.
    e, _ = _apply_one(_entry("Designed the Mentee Program curriculum and rubric, 2019."))
    assert e["taxonomy_code"] == "K2"


def test_section_gate_blocks_non_mentoring_section():
    # Same named-mentee text, but outside an advising/mentoring section -> no fire.
    e, _ = _apply_one(_entry(
        "Provided feedback to mentee Dr. Jane Smith.",
        hierarchy=["CLINICAL ACTIVITIES"],
    ))
    assert e["taxonomy_code"] == "K2"


def test_already_n3_is_untouched():
    for code in ("N3A", "N3B"):
        e, stats = _apply_one(_entry("Mentor for Dr. Jane Smith, present.", code=code))
        assert e["taxonomy_code"] == code
        assert stats["corrections_made"] == 0


def test_non_teaching_code_is_untouched():
    # Only the K family is in the allow-list.
    e, _ = _apply_one(_entry("Mentor for Dr. Jane Smith, 2019 - 2021.", code="D1"))
    assert e["taxonomy_code"] == "D1"


# --- stats / non-mutation ---------------------------------------------------

def test_stats_shape_and_no_spurious_corrections():
    entries = [
        _entry("Mentor for Dr. Jane Smith, present."),          # fires
        _entry("Freshman Mentor and Teaching Assistant"),       # no
        _entry("Tumor board, 2014 - present", code="K2"),       # no
    ]
    out, stats = apply_prose_mentee_corrections(entries)
    assert stats["entries_checked"] == 3
    assert stats["corrections_made"] == 1
    assert len(stats["correction_details"]) == 1
    assert stats["correction_details"][0]["corrected_to"] == "N3A"
