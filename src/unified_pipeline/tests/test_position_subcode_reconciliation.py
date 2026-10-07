"""Regression guard for issue #161: cross-code appointment fragmentation.

When field extraction splits one appointment into separate entries (an
employer/date header on one line, the bare role title on another), Stage 3b
classifies each fragment in isolation. A context-poor title like "Nurse
Practitioner" then gets a different position subcode (D3) than the rest of its
group (D2), so it lands in the wrong output table and can't be reassembled by
the Stage 6 within-code merge (#156 / PR #160).

The deterministic reconciler moves a context-poor position fragment to the
subcode shared by its nearest position-family neighbours on both sides.

These tests assert GENERAL properties on a person-agnostic synthetic group and
on the de-identified position-family entries from run I5NKUG (no personal
identifiers — only role titles, institutions and dates).

    python3 -m pytest src/unified_pipeline/tests/test_position_subcode_reconciliation.py -p no:cacheprovider
"""

import json
import sys
from pathlib import Path

# Ensure the repo's ``src`` directory is importable regardless of cwd/rootdir.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.validators.position_subcode_reconciler import (  # noqa: E402
    apply_position_subcode_reconciliation,
    is_context_poor_position,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "I5NKUG_position_entries.json"


def _e(idx, code, text):
    """Build a minimal classified entry (person-agnostic)."""
    return {"element_idx_start": idx, "taxonomy_code": code, "text": text}


# --- real-run regression (de-identified I5NKUG position entries) ------------

def _load_fixture_entries():
    assert FIXTURE.exists(), f"missing committed fixture: {FIXTURE}"
    return json.load(open(FIXTURE, encoding="utf-8"))["entries"]


def test_i5nkug_stray_d3_reconciled_to_d2():
    """The bare 'Nurse Practitioner' (D3) embedded among NYP D2 rows -> D2."""
    entries = _load_fixture_entries()
    entries, stats = apply_position_subcode_reconciliation(entries)

    np_rows = [e for e in entries if (e.get("text") or "").strip() == "Nurse Practitioner"]
    assert len(np_rows) == 1, "fixture should contain exactly one bare 'Nurse Practitioner' fragment"
    np_row = np_rows[0]

    assert np_row["taxonomy_code"] == "D2", "stray fragment should be reconciled to the surrounding D2 group"
    assert np_row.get("original_taxonomy_code") == "D3"
    corr = np_row.get("position_subcode_reconciliation")
    assert corr and corr["from"] == "D3" and corr["to"] == "D2"
    assert stats["corrections_applied"] == 1


def test_i5nkug_only_the_stray_changed_and_no_d3_remains():
    """Exactly one entry changes; no stray position subcode survives."""
    before = {(_idx(e)): e["taxonomy_code"] for e in _load_fixture_entries()}
    entries, _ = apply_position_subcode_reconciliation(_load_fixture_entries())
    after = {(_idx(e)): e["taxonomy_code"] for e in entries}

    changed = [k for k in before if before[k] != after[k]]
    assert len(changed) == 1, f"only the stray fragment should change, changed={changed}"

    # The NYP/WCMC appointment block is now a uniform D2 run (no orphan D3).
    assert "D3" not in set(after.values()), "no stray D3 fragment should remain in this all-hospital block"


def _idx(e):
    v = e.get("element_idx_start")
    return v if isinstance(v, int) else e.get("element_idx_end", 0)


# --- predicate ---------------------------------------------------------------

def test_context_poor_predicate():
    assert is_context_poor_position(_e(1, "D2", "Nurse Practitioner")) is True
    assert is_context_poor_position(_e(1, "D2", "Staff Nurse")) is True
    # Carries its own date context -> not a fragment.
    assert is_context_poor_position(
        _e(1, "D2", "New York Presbyterian Hospital, New York (September 2004-February 2013)")
    ) is False
    # Carries its own institution context -> not a fragment.
    assert is_context_poor_position(_e(1, "D2", "Mount Sinai Hospital")) is False
    # Long prose line -> not a bare title.
    assert is_context_poor_position(
        _e(1, "D2", "Led a multidisciplinary team responsible for advanced endoscopy across two campuses")
    ) is False
    # Not a position-family code -> never a candidate.
    assert is_context_poor_position(_e(1, "H", "Nurse Practitioner")) is False


# --- synthetic, person-agnostic rule + guards -------------------------------

def test_synthetic_sandwiched_fragment_is_reconciled():
    entries = [
        _e(1, "D2", "Acme Hospital, Springfield, IL (2015-present)"),
        _e(2, "D3", "Attending Physician"),
        _e(3, "D2", "Cardiology Service (2016-2020)"),
    ]
    entries, stats = apply_position_subcode_reconciliation(entries)
    assert entries[1]["taxonomy_code"] == "D2"
    assert stats["corrections_applied"] == 1


def test_guard_neighbors_disagree_no_change():
    """A genuine subsection boundary (neighbours differ) is left untouched."""
    entries = [
        _e(1, "D1", "Beta University, Boston, MA (2010-2014)"),
        _e(2, "D3", "Research Fellow"),
        _e(3, "D2", "Gamma Hospital, New York (2014-2018)"),
    ]
    entries, stats = apply_position_subcode_reconciliation(entries)
    assert entries[1]["taxonomy_code"] == "D3"
    assert stats["corrections_applied"] == 0


def test_guard_edge_fragment_not_reconciled():
    """A fragment at the start/end of the run lacks two-sided evidence."""
    head = [
        _e(1, "D3", "Staff Scientist"),
        _e(2, "D2", "Acme Hospital (2015-2018)"),
        _e(3, "D2", "Beta Clinic (2018-2020)"),
    ]
    head, stats = apply_position_subcode_reconciliation(head)
    assert head[0]["taxonomy_code"] == "D3"
    assert stats["corrections_applied"] == 0


def test_guard_self_contained_entry_not_reconciled():
    """An entry with its own dates+institution is not a fragment, so untouched."""
    entries = [
        _e(1, "D2", "Acme Hospital (2015-present)"),
        _e(2, "D3", "Consultant, Beta Institute, Chicago, IL (2016-2019)"),
        _e(3, "D2", "Gamma Hospital (2019-2021)"),
    ]
    entries, stats = apply_position_subcode_reconciliation(entries)
    assert entries[1]["taxonomy_code"] == "D3"
    assert stats["corrections_applied"] == 0


def test_no_change_when_fragment_already_matches_group():
    entries = [
        _e(1, "D2", "Acme Hospital (2015-present)"),
        _e(2, "D2", "Staff Nurse"),
        _e(3, "D2", "Medical/Surgical Unit (2016-2020)"),
    ]
    entries, stats = apply_position_subcode_reconciliation(entries)
    assert [e["taxonomy_code"] for e in entries] == ["D2", "D2", "D2"]
    assert stats["corrections_applied"] == 0
