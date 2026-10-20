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

import pytest

# Add src/ to path so unified_pipeline.core is importable.
# tests/ -> backend/ -> web_interface/ -> project_root/ -> src/
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from unified_pipeline.core.validators.prose_mentee_corrector import (  # noqa: E402
    apply_bare_mentee_resolution,
    apply_prose_mentee_corrections,
)

MENTOR_SECTION = ["ADVISING & MENTORING"]
# The run's year, pinned so a lone-year case does not move with the clock.
RUN_YEAR = 2026


def _entry(text, code="K2", hierarchy=None):
    return {"text": text, "taxonomy_code": code, "hierarchy": hierarchy if hierarchy is not None else MENTOR_SECTION}


def _apply_one(entry):
    entries, stats = apply_prose_mentee_corrections([entry], RUN_YEAR)
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


# --- a lone past year is past (#1342, XELRLZ 250/258/260) ------------------

def test_lone_past_year_becomes_n3b():
    e, _ = _apply_one(_entry(
        "2016 Mentor Unofficial dissertation advisor for Ana Doe; attended advisory meetings."))
    assert e["taxonomy_code"] == "N3B"


def test_every_stated_year_past_becomes_n3b():
    e, _ = _apply_one(_entry(
        "2015 Mentor Co-mentored Lee Roe during his project (poster at SfN March 25, 2016)"))
    assert e["taxonomy_code"] == "N3B"


@pytest.mark.parametrize("text", [
    "2026 Mentor for Ana Doe, dissertation advising",          # the run's year
    "Mentor for Ana Doe since 2016",                           # since
    "Mentor for Ana Doe from 2016",                            # from
    "Mentor for Ana Doe 2016 onwards",                         # onwards
    "Mentor for Ana Doe 2016 until now",                       # until now
    "2016 - Mentor for Ana Doe",                               # year, dash, no end year
    "Mentor for Ana Doe (2016-",                               # dangling dash
    "Mentor for Ana Doe 2016, current",                        # open-period word
    "Mentor for Ana Doe",                                      # no year at all
    "Mentor for Ana Doe 2016 till date",
    "Mentor for Ana Doe 2016 to now",
    "Mentor for Ana Doe 2016 (continuing)",
    "Mentor for Ana Doe 2016 to date",
    "Mentor for Ana Doe 2016 ongoing",
    "Mentor for Ana Doe 2016 to the present",
    "Mentor for Ana Doe 2016 until present",
    "Mentor for Ana Doe 2016 pres.",
    "Mentor for Ana Doe 2016, currently",
    "Mentor for Ana Doe 2016, still in the lab",
    "Mentor for Ana Doe 2016, active",
    "Mentor for Ana Doe 2016 to today",
    "Mentor for Ana Doe 2016, thesis in progress",
    "Mentor for Ana Doe 2016 onward",
    "Mentor for Ana Doe 2016 until date",
    "Mentor for Ana Doe 2016 til date",
], ids=["run-year", "since", "from", "onwards", "until-now", "spaced-dash", "dangling-dash", "current", "no-year",
        "till-date", "to-now", "continuing", "to-date", "ongoing", "to-the-present", "until-present", "pres",
        "currently", "still", "active", "today", "in-progress", "onward", "until-date", "til-date"])
def test_a_year_that_may_be_open_stays_n3a(text):
    e, _ = _apply_one(_entry(text))
    assert e["taxonomy_code"] == "N3A"


def test_an_open_word_after_an_end_year_stays_past_as_before():
    # The lone-year vocabulary reads only text with no end-dated range; an
    # end-dated one is read as it was ("2014-2016 onwards" was N3B).
    e, _ = _apply_one(_entry("Mentor for Ana Doe 2014-2016 onwards"))
    assert e["taxonomy_code"] == "N3B"


def test_the_run_year_is_an_input():
    entry = _entry("2025 Mentor for Ana Doe, dissertation advising")
    (current,), _ = apply_prose_mentee_corrections([dict(entry)], 2025)
    (past,), _ = apply_prose_mentee_corrections([dict(entry)], 2026)
    assert (current["taxonomy_code"], past["taxonomy_code"]) == ("N3A", "N3B")


# --- stats / non-mutation ---------------------------------------------------

def test_stats_shape_and_no_spurious_corrections():
    entries = [
        _entry("Mentor for Dr. Jane Smith, present."),          # fires
        _entry("Freshman Mentor and Teaching Assistant"),       # no
        _entry("Tumor board, 2014 - present", code="K2"),       # no
    ]
    out, stats = apply_prose_mentee_corrections(entries, RUN_YEAR)
    assert stats["entries_checked"] == 3
    assert stats["corrections_made"] == 1
    assert len(stats["correction_details"]) == 1
    assert stats["correction_details"][0]["corrected_to"] == "N3A"


# --- bare N3 -> N3A/N3B (#1574) ---------------------------------------------
# Synthetic names only; the shapes are the YUYVIG ones (LTTYWI-06, RLADNC-07,
# IZIXVF-01) and the corpus lines the step must leave alone.

TEACHING = ["Teaching Responsibilities", "Clinical Service Teaching"]


def _resolve(entries):
    out, stats = apply_bare_mentee_resolution(entries)
    return [e["taxonomy_code"] for e in out], stats


def test_bare_names_after_a_past_mentee_run_take_n3b():
    # LTTYWI-06: one trainee list, the first names coded N3B, the rest bare
    # N3, under a teaching heading with no mentoring word in it.
    entries = [
        _entry("Participated in the residency training of:", code="K2", hierarchy=TEACHING),
        _entry("Alma Quist", code="N3B", hierarchy=TEACHING),
        _entry("Boris Ulam", code="N3", hierarchy=TEACHING),
        _entry("Cora Van Dyke", code="N3", hierarchy=TEACHING),
    ]
    codes, stats = _resolve(entries)
    assert codes == ["K2", "N3B", "N3B", "N3B"]
    assert stats["corrections_made"] == 2
    assert entries[2]["bare_mentee_resolution"] == {"original_code": "N3", "resolved_to": "N3B"}
    assert entries[2]["classification_reasoning"].startswith("[bare-mentee resolved N3->N3B]")


def test_bare_name_before_a_run_takes_the_following_code():
    entries = [
        _entry("Dana Whit", code="N3", hierarchy=TEACHING),
        _entry("Ezra Holm, PhD student, 2001-2005", code="N3B", hierarchy=TEACHING),
    ]
    assert _resolve(entries)[0] == ["N3B", "N3B"]


def test_numbered_names_under_mentoring_heading_default_to_n3a():
    # RLADNC-07: no N3A/N3B sibling, no dates, a mentoring heading.
    heading = ["N. MENTORING", "Junior Faculty Mentees"]
    entries = [
        _entry("Formally mentored research projects", code="K2", hierarchy=heading),
        _entry("1. Farid Oster - Primary Mentor for the Leadership Fellowship Program", code="N3", hierarchy=heading),
        _entry("2. Gemma Rao Patel", code="N3", hierarchy=heading),
        _entry("3. Hugo O'Neal, MD, Faculty member", code="N3", hierarchy=heading),
    ]
    assert _resolve(entries)[0] == ["K2", "N3A", "N3A", "N3A"]


def test_own_dates_outrank_the_run():
    # A closed range is past, an open one current, whatever the neighbours say.
    closed_after_current = [
        _entry("Ivo Lind, MS student, 2015 - present", code="N3A"),
        _entry("Jill Moss: thesis project (November 2007- June 2008)", code="N3"),
    ]
    assert _resolve(closed_after_current)[0] == ["N3A", "N3B"]
    open_after_past = [
        _entry("Lena Ortiz (2010-2012)", code="N3B"),
        _entry("Kurt Nash, PhD student, 2023 - .", code="N3"),
    ]
    assert _resolve(open_after_past)[0] == ["N3B", "N3A"]


def test_neighbour_resolved_in_the_same_pass_does_not_settle_the_run():
    # The run is read off the codes stage 3b produced, so the result does not
    # depend on the order entries are visited in.
    entries = [
        _entry("Mia Park (1999-2001)", code="N3"),
        _entry("Nils Quade", code="N3"),
    ]
    assert _resolve(entries)[0] == ["N3B", "N3A"]


def test_staff_heading_keeps_n3():
    # IZIXVF-01: technical staff and lab managers are not mentees, even after
    # an N3B run under the same heading.
    for heading in (
        ["5. Laboratory Staff", "C. Technical Staff and Laboratory Managers."],
        ["Research Staff"],
        ["Lab Managers"],
        ["Technicians Supervised"],
        ["Personnel Supervised"],
        ["Employees"],
    ):
        entries = [
            _entry("15. Omar Pike, PGR", code="N3B", hierarchy=heading),
            _entry("16. Pia Quinn, PGR/Lab manager", code="N3", hierarchy=heading),
        ]
        codes, stats = _resolve(entries)
        assert codes == ["N3B", "N3"], heading
        assert stats["corrections_made"] == 0


def test_lines_that_name_nobody_keep_n3():
    for text in (
        "Name Dates Current Position",
        "Name Degree, Years, Current Position",
        "Honors Theses:",
        "Student Name   Program     Role",
        "Doctoral Advising:",
        "Graduate Students and Postdoctoral Associates:",
        "Louisiana State University",
        "At Purdue University:",
        "Not applicable",
        "N/A",
        "Ravi",
        "2014 – 2016",
        "Supervision of honors thesis projects, 1995-2004.",
        "I have mentored the following residents on research projects:",
        "Thesis Advisor, 2015-2018",
        "Primary Mentor: weekly meetings",
        "Journal Club Facilitator, 2019-present",
    ):
        codes, _ = _resolve([_entry(text, code="N3"), _entry("Sara Tull (2001-2003)", code="N3B")])
        assert codes[0] == "N3", text


def test_no_mentoring_heading_and_no_run_keeps_n3():
    # A name under a flattened heading with no N3A/N3B entry next to it.
    heading = ["Personal Data"]
    entries = [
        _entry("Tom Ueda", code="N3", hierarchy=heading),
        _entry("Committee member, Honors Thesis", code="K2", hierarchy=heading),
        _entry("Una Voss, 2001-2003", code="N3B", hierarchy=heading),
    ]
    assert _resolve(entries)[0] == ["N3", "K2", "N3B"]


def test_run_stops_at_a_different_heading():
    entries = [
        _entry("Vera Wolf", code="N3B", hierarchy=["Past Mentees"]),
        _entry("Will Xu", code="N3", hierarchy=TEACHING),
    ]
    assert _resolve(entries)[0] == ["N3B", "N3"]


def test_run_does_not_step_over_another_heading():
    # A bare N3 under another heading ends the run, even though it is N3 too.
    entries = [
        _entry("Xena Yost", code="N3B", hierarchy=TEACHING),
        _entry("Yuri Zell", code="N3", hierarchy=["Personal Data"]),
        _entry("Zoe Abbot", code="N3", hierarchy=TEACHING),
    ]
    assert _resolve(entries)[0] == ["N3B", "N3", "N3"]


def test_only_bare_n3_is_touched():
    entries = [_entry("Yara Zane", code=code) for code in ("N3A", "N3B", "K2", "N4", "T")]
    codes, stats = _resolve(entries)
    assert codes == ["N3A", "N3B", "K2", "N4", "T"]
    assert stats["corrections_made"] == 0
