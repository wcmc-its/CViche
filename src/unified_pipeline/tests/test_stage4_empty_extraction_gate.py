"""#322: needs_llm_recovery must catch total-extraction loss on short entries.

The legacy gate (coverage <30% AND text >=200 chars AND date/structure markers)
misses entries where extraction produced nothing at all from 50-200-char text
(board certifications, languages, role lines). These cases are lifted verbatim
from the 28-CV corpus dry-run in #322.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_4_field_extractor import needs_llm_recovery  # noqa: E402


def _entry(text, fields, coverage=0.0):
    return {
        "text": text,
        "extracted_fields": fields,
        "extraction_coverage": {"extraction_coverage_percent": coverage},
    }


# --- New trigger: empty extraction on substantive short text ---------------

def test_empty_fields_short_entry_triggers():
    # ngfnyq: ABIM board cert, 51 chars, no fields extracted -- was lost silently
    e = _entry("ABIM Board Certification in Internal Medicine, 2006", {})
    assert needs_llm_recovery(e)


def test_empty_fields_no_date_no_structure_triggers():
    # chhfnq K2: no year, no tabs/pipes/multi-newline -- legacy gate blind spot
    e = _entry(
        "Assisted professors of General Psychology and Clinical Psychology with grading.",
        {},
    )
    assert needs_llm_recovery(e)


def test_all_falsy_field_values_count_as_empty():
    e = _entry(
        "English (Native); French (Fluent); Egyptian Colloquial Arabic (Beginning)",
        {"language": None, "proficiency": ""},
    )
    assert needs_llm_recovery(e)


# --- Non-triggers: the new check must stay narrow --------------------------

def test_short_junk_below_floor_does_not_trigger():
    e = _entry("Curriculum Vitae", {})
    assert not needs_llm_recovery(e)


def test_populated_fields_do_not_trigger():
    # DEA cert: dates extracted (ISO-normalized so token coverage reads 0%) --
    # a coverage-metric artifact, not extraction loss; must NOT retry.
    e = _entry(
        "Controlled Substance Registration (DEA) Certificate\tActive from June 13th 2024 to October 31st 2026",
        {"issue_date": "2024-06-13", "expiration_date": "2026-10-31"},
    )
    assert not needs_llm_recovery(e)


# --- Legacy gate behavior unchanged ----------------------------------------

def test_legacy_long_low_coverage_with_dates_still_triggers():
    e = _entry("2019 " + "x" * 250, {"title": "something"}, coverage=10.0)
    assert needs_llm_recovery(e)


def test_legacy_high_coverage_still_ignored():
    e = _entry("2019 " + "x" * 250, {"title": "something"}, coverage=95.0)
    assert not needs_llm_recovery(e)
