"""Regression guards for the stage-2 extraction noise filter (#211).

Grounded in run 89HQVQ: stage 2 emitted 215 entries of which 4 content
entries were exact duplicates (same element, conflicting hierarchies — incl.
a 2,152-char 8-grant mega-entry emitted once per sibling H2), the GRANTS
header record itself appeared twice, and one content paragraph was empty.
Every duplicate rides through the 3b/4/5 LLM stages and is paid for twice.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage2_noise_filter.py -p no:cacheprovider

Self-contained: pure-function tests, no DB, no LLM.
"""

import sys
from pathlib import Path

# Ensure the repo's ``src`` directory is importable regardless of cwd/rootdir.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_2_entry_extraction import (  # noqa: E402
    _dedup_idx_key,
    filter_extraction_noise,
)


def _entry(text, etype="paragraph", start=0, hierarchy=None):
    return {
        "text": text,
        "element_type": etype,
        "element_idx_start": start,
        "element_idx_end": start,
        "hierarchy": hierarchy or ["SECTION"],
    }


# ------------------------------------------------------------- empties

def test_empty_content_entry_dropped():
    kept = filter_extraction_noise([_entry(""), _entry("   "), _entry("real")])
    assert [e["text"] for e in kept] == ["real"]


def test_empty_break_and_header_records_kept():
    # 89HQVQ: 24 of the 25 empty-text entries were structural 'break' records.
    entries = [
        _entry("", etype="break", start=5),
        _entry("", etype="break", start=9),
        _entry("GRANTS", etype="header", start=27),
    ]
    assert filter_extraction_noise(entries) == entries


# ----------------------------------------------------------- duplicates

def test_mega_entry_duplicate_collapsed_first_copy_wins():
    """The 89HQVQ failure: one table cell emitted under BOTH sibling H2s,
    with mixed idx representations (float 30.0 vs int 30)."""
    fsmb = "Federation of State Medical Boards (FSMB) Foundation Grant | ..."
    a = _entry(fsmb, etype="table", start=30.0,
               hierarchy=["GRANTS", "Grants Awarded"])
    b = _entry(fsmb, etype="table", start=30,
               hierarchy=["GRANTS", "Grants Under Review & Submitted"])
    kept = filter_extraction_noise([a, b])
    assert kept == [a]  # first copy in document order wins


def test_duplicate_header_record_collapsed():
    # 89HQVQ: the GRANTS header record itself was emitted twice at idx 27.
    a = _entry("GRANTS", etype="header", start=27)
    b = _entry("GRANTS", etype="header", start=27)
    assert filter_extraction_noise([a, b]) == [a]


def test_same_text_different_index_kept():
    """Repeated names/lines at different positions are REAL content — 89HQVQ
    lists the same mentee under two degree programs."""
    a = _entry("Ryan Costantino", start=22)
    b = _entry("Ryan Costantino", start=24)
    assert filter_extraction_noise([a, b]) == [a, b]


def test_subrow_indices_not_conflated():
    # "22.2" and "22.7" are distinct sub-rows; same text there must survive.
    a = _entry("Present", etype="table_row", start="22.2")
    b = _entry("Present", etype="table_row", start="22.7")
    assert filter_extraction_noise([a, b]) == [a, b]


def test_whitespace_variants_are_duplicates():
    a = _entry("PhD:  Instructional   Systems Technology", start=9)
    b = _entry("PhD: Instructional Systems Technology", start=9)
    assert len(filter_extraction_noise([a, b])) == 1


# ---------------------------------------------------------- idx key

def test_idx_key_normalizes_mixed_representations():
    assert _dedup_idx_key(30) == _dedup_idx_key(30.0) == _dedup_idx_key("30.0")
    assert _dedup_idx_key("22.2") != _dedup_idx_key("22.7")
    assert _dedup_idx_key("table_3") == _dedup_idx_key("table_3")
    assert _dedup_idx_key("table_3") != _dedup_idx_key("table_4")
    assert _dedup_idx_key(None) == _dedup_idx_key(None)  # total, never raises
