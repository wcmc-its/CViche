"""Tests for `reconcile_date_range` in `unified_pipeline/stage4/coercion.py` (#556).

Stage 4 accepted the LLM's start_date/end_date verbatim and never reconciled
them against the source text it just extracted from. A closed range whose end
the model dropped (e.g. "2018-2020" -> start_date="2018", end_date=None)
reached stage 6 in exactly the shape it uses for a genuinely ongoing entry, so
a finished mentorship or grant rendered as "{start}-Present" -- a factually
false statement about the CV owner.

Covers, per the ticket's four required conditions (schema declares both
dates; end_date empty; exactly one closed range; no present/ongoing/current
marker), a positive control that fails on `dev` before the fix, one negative
test per condition, the two-range ambiguity case, and the pinned
disagreeing-start behaviour (skip the whole repair rather than partially
apply it -- see `reconcile_date_range`'s docstring).

Self-contained: no LLM calls, no I/O, no PII. The two off-farm examples from
the issue (FSMB Foundation Grant, run EHMQSQ's mentee) are reproduced as
fixtures below, not read from S3.
"""
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage4.coercion import (  # noqa: E402
    DATE_RANGE_TAXONOMY_CODES,
    apply_regex_post_processing,
    reconcile_date_range,
)


# --- positive control: the collapsed {start_date, end_date: None} shape ----
# with a single closed range in the text is repaired. This mirrors the
# real EHMQSQ mentee example from #556 (a mentoring code, N1, whose schema
# declares both dates): text "2018-2020" extracted as start_date="2018",
# end_date=None, rendering "2018-Present". FAILS on `dev` (no reconciliation
# step exists there at all).

def test_positive_control_dropped_end_is_restored_from_closed_range():
    fields = {"mentee_name": "Redacted", "start_date": "2018", "end_date": None}
    text = "Mentee: Redacted, 2018-2020, research mentorship"

    updated, reformatted = apply_regex_post_processing(text, fields, "N1")

    assert updated["start_date"] == "2018"
    assert updated["end_date"] == "2020"
    assert reformatted["end_date"] == {
        "original": None,
        "reformatted": "2020",
        "reason": "Restored end_date from a single closed date range in source text",
    }
    # start_date already agreed with the text, so it is not re-reported as a
    # repair (see disagreeing-start test below for the case that would be).
    assert "start_date" not in reformatted


def test_positive_control_wired_through_both_pipeline_call_sites_shape():
    # apply_regex_post_processing is the shared function both stage4/
    # extraction.py call sites (the recovery path and the main path) invoke;
    # this pins that reconcile_date_range fires through that one function,
    # for a fully-blank start/end pair too (no disagreement possible).
    fields = {"title": "Grant X", "start_date": None, "end_date": None}
    text = "Grant X, 2025-2026, PI"

    updated, reformatted = apply_regex_post_processing(text, fields, "M2A")

    assert updated["start_date"] == "2025"
    assert updated["end_date"] == "2026"
    assert reformatted["start_date"]["reformatted"] == "2025"
    assert reformatted["end_date"]["reformatted"] == "2026"


# --- condition 1: schema must declare both start_date and end_date ---------

def test_condition_schema_without_both_dates_blocks_repair():
    # S1 is a publication code -- no S code is in DATE_RANGE_TAXONOMY_CODES,
    # which is what keeps citation year spans and page ranges out of the
    # repair path.
    assert "S1" not in DATE_RANGE_TAXONOMY_CODES
    fields = {"start_date": None, "end_date": None}
    text = "pp. 2015-2018"

    updated, reformatted = apply_regex_post_processing(text, fields, "S1")

    assert updated["end_date"] is None
    assert reformatted == {}


# --- condition 2: end_date must be empty ------------------------------------

def test_condition_populated_end_date_blocks_repair():
    fields = {"start_date": "2018", "end_date": "2019"}  # already populated, even if "wrong"
    text = "2018-2020"

    updated, reformatted = apply_regex_post_processing(text, fields, "N1")

    assert updated["end_date"] == "2019"  # left alone, not overwritten
    assert reformatted == {}


# --- condition 3: exactly one closed range (two ranges = ambiguous) --------

def test_condition_two_ranges_is_ambiguous_and_blocks_repair():
    fields = {"start_date": None, "end_date": None}
    text = "First appointment 2010-2012, then reappointed 2015-2018"

    updated, reformatted = apply_regex_post_processing(text, fields, "D1")

    assert updated["start_date"] is None
    assert updated["end_date"] is None
    assert reformatted == {}


def test_condition_no_range_at_all_blocks_repair():
    fields = {"start_date": "2020", "end_date": None}
    text = "Ongoing committee service, no dates given"

    updated, reformatted = apply_regex_post_processing(text, fields, "D1")

    assert updated["end_date"] is None
    assert reformatted == {}


# --- condition 4: no present/ongoing/current marker anywhere in the text ---

@pytest.mark.parametrize(
    "marker_text",
    [
        "2020-2022, currently the acting chair",
        "2020-2022 to present",
        "2020-2022, this role is current",
        "2020-2022, presents continuing work",
    ],
)
def test_condition_present_marker_blocks_repair(marker_text):
    fields = {"start_date": "2020", "end_date": None}

    updated, reformatted = apply_regex_post_processing(marker_text, fields, "D1")

    assert updated["end_date"] is None
    assert reformatted == {}


# --- disagreeing-start behaviour, as pinned by the ticket: skip the whole --
# repair rather than partially apply it. Reproduces the real FSMB Foundation
# Grant example from #556: source text "2025-2026" was extracted as
# start_date=2026 (the model took the range's END as the start), end_date
# empty. The repair is intentionally NOT applied here -- overwriting a value
# the model already committed to is a bigger step than filling in a blank
# one, and skipping leaves the fields exactly as extracted with no
# reformatted_fields entry (no false report of a repair).

def test_disagreeing_start_skips_the_whole_repair():
    fields = {
        "title": "FSMB Foundation Grant",
        "agency": "FSMB",
        "start_date": "2026",
        "end_date": None,
    }
    text = "FSMB Foundation Grant, 2025-2026, PI"

    updated, reformatted = apply_regex_post_processing(text, fields, "M2A")

    assert updated["start_date"] == "2026"  # unchanged, not corrected to 2025
    assert updated["end_date"] is None      # unchanged, not filled from the match
    assert reformatted == {}


# --- direct unit coverage of reconcile_date_range itself -------------------

def test_reconcile_date_range_direct_call_no_taxonomy_gate():
    # apply_regex_post_processing does the taxonomy_code gate; the function
    # itself has no such gate (mirrors _normalize_pmid/_normalize_orcid's
    # style, where the caller decides whether to invoke the helper at all).
    updated = {"start_date": None, "end_date": None}
    reformatted = {}

    reconcile_date_range("Role held 2012-2014", updated, reformatted)

    assert updated == {"start_date": "2012", "end_date": "2014"}
    assert set(reformatted) == {"start_date", "end_date"}


def test_reconcile_date_range_empty_text_is_a_no_op():
    updated = {"start_date": None, "end_date": None}
    reformatted = {}

    reconcile_date_range("", updated, reformatted)

    assert updated == {"start_date": None, "end_date": None}
    assert reformatted == {}


# --- condition 5: the matched range must be a plausible calendar-year span -

def test_condition_implausible_range_blocks_repair():
    # A course code shaped like "NNNN-NNNN" (the real gated shape from
    # 2054_Opresko_Cv's K3 entries, round-1 finding 1) is not a year range;
    # start > end and both fall outside the plausible bounds.
    fields = {"start_date": None, "end_date": None}
    text = "Completed MSELCT 5130-1020, no dates listed"

    updated, reformatted = apply_regex_post_processing(text, fields, "K3")

    assert updated["end_date"] is None
    assert reformatted == {}


def test_condition_reversed_range_in_bounds_blocks_repair():
    # Both halves are in-bounds calendar years, but reversed (start > end) --
    # the `start_year <= end_year` half of the plausibility check, not the
    # bounds half, is what blocks this one.
    fields = {"start_date": None, "end_date": None}
    text = "Fellowship 2020-2018"

    updated, reformatted = apply_regex_post_processing(text, fields, "D1")

    assert updated["end_date"] is None
    assert reformatted == {}


def test_condition_ordered_but_out_of_bounds_range_blocks_repair():
    # Both halves are correctly ordered, but fall below _MIN_PLAUSIBLE_YEAR --
    # the bounds half of the plausibility check, not the ordering half, is
    # what blocks this one.
    fields = {"start_date": None, "end_date": None}
    text = "Historical appointment 1850-1860"

    updated, reformatted = apply_regex_post_processing(text, fields, "D1")

    assert updated["end_date"] is None
    assert reformatted == {}


def test_condition_ongoing_marker_blocks_repair():
    # The parametrized present-marker cases above never exercise the bare
    # "ongoing" alternative in _PRESENT_MARKER_PATTERN itself.
    fields = {"start_date": "2020", "end_date": None}
    text = "2020-2022, ongoing"

    updated, reformatted = apply_regex_post_processing(text, fields, "D1")

    assert updated["end_date"] is None
    assert reformatted == {}


def test_condition_digit_glued_identifier_blocks_repair():
    # "HL001950-2020" (a grant-number shape) is not a date range: the digits
    # immediately preceding the hyphen are glued to more digits on their
    # left, so CLOSED_DATE_RANGE_PATTERN's digit-boundary guards
    # ((?<!\d) / (?!\d)) must keep this from matching as "1950-2020" at all.
    fields = {"start_date": None, "end_date": None}
    text = "Grant HL001950-2020 renewal"

    updated, reformatted = apply_regex_post_processing(text, fields, "M2A")

    assert updated["end_date"] is None
    assert reformatted == {}


# --- drift guard: DATE_RANGE_TAXONOMY_CODES vs. the live schema -----------

def test_date_range_taxonomy_codes_matches_schema_derived_codes():
    # DATE_RANGE_TAXONOMY_CODES is hand-kept (coercion.py may not import
    # schemas.py -- see the constant's own docstring), so nothing enforces
    # it stays in sync with schemas.get_active_schemas() as codes are added,
    # removed, or have their declared fields changed. This test may import
    # both modules even though coercion.py itself cannot (#556 round-1
    # finding 4).
    from unified_pipeline.stage4 import schemas

    active = schemas.get_active_schemas()
    schema_derived = {
        code
        for code, schema in active.items()
        if "start_date" in schema.get("fields", []) and "end_date" in schema.get("fields", [])
    }

    assert set(DATE_RANGE_TAXONOMY_CODES) == schema_derived
