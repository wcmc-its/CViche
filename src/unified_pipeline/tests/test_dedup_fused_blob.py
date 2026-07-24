"""Regression tests for issue #227: distinct records dropped into a fused
multi-record table blob.

When stage 2 captures a whole layout table atomically (#208), a distinct single
record is fully token-contained in the resulting blob merely because the blob
swallowed it. `_drop_is_safe`'s token-containment clause used to treat that as a
true duplicate and drop the record (C0ZGFW: 35 invited presentations + 3 teaching
records lost this way). The fix declines to vouch that drop when the KEPT entry
is a fused multi-record blob (many record-lines).

Pure functions: no LLM, no DB, no PII. Fixtures are synthesized. Run with:
    python3 -m pytest src/unified_pipeline/tests/test_dedup_fused_blob.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_6_word_template import (  # noqa: E402
    deduplicate_entries,
    _drop_is_safe,
    _record_lines,
    _squash,
    DEDUP_FUSED_BLOB_RECORD_LINES,
)

# Six distinct presentation records.
RECORDS = [
    "Rocky Mountain Society Hospital Medicine Ultrasound Course Denver Colorado",
    "American College Physicians National Meeting Precourse Ultrasound Philadelphia",
    "Society General Internal Medicine Blind Ultrasound Workshop Demonstration Boston",
    "Digestive Disease Week Ultrasound Workshop Hepatologist Panel Speaker Chicago",
    "Nephrology Kidney Week Instructor Handson Teaching Ultrasound Session Seattle",
    "Minnesota Regional Ultrasound Course Faculty Lecturer Cardiac Imaging Rochester",
]


def _blob_line(record: str) -> str:
    # Interleave each record's words with stopwords + tab cells so the blob
    # TOKEN-contains the record (every significant word present) but the record
    # is NOT a whitespace-squashed substring of it (so the verbatim clause of
    # _drop_is_safe does not fire — this is the real dedup path).
    ws = record.split()
    return "\t".join([" at ".join(ws[:4]), " the ".join(ws[4:]),
                      "padding to exceed sixty characters here indeed yes"])


def _fused_blob() -> dict:
    text = "Title\tInstitution/Location\tDates\n" + "\n".join(_blob_line(r) for r in RECORDS)
    return {"text": text}


def test_fused_blob_records_are_not_dropped():
    """The bug: all 6 distinct records were dropped as 'contained' in the blob.
    After the fix, the blob AND every record survive."""
    blob = _fused_blob()
    assert len(_record_lines(blob["text"])) >= DEDUP_FUSED_BLOB_RECORD_LINES

    group = [dict(blob)] + [{"text": r} for r in RECORDS]
    kept = deduplicate_entries([dict(e) for e in group])
    kept_squashed = {_squash(e["text"]) for e in kept}

    for r in RECORDS:
        assert _squash(r) in kept_squashed, f"record dropped into fused blob: {r!r}"
    assert len(kept) == len(group)


def test_single_record_true_duplicate_still_dropped():
    """No regression: a token-contained near-duplicate of a SINGLE-record entry
    (kept is not a fused blob) is still deduped."""
    kept = {"text": "Alpha Beta Gamma Delta Epsilon Zeta Eta Theta extra detail "
                    "words here for length exceeding sixty characters plus more"}
    dup = {"text": "Alpha Beta Gamma Delta Epsilon Zeta"}
    assert len(_record_lines(kept["text"])) < DEDUP_FUSED_BLOB_RECORD_LINES
    assert _drop_is_safe(dup, kept) is True

    out = deduplicate_entries([dict(kept), dict(dup)])
    assert len(out) == 1


def test_verbatim_contained_dropped_even_in_blob():
    """No over-loosening: a record that IS a verbatim (squashed) substring of a
    blob is a true duplicate and must still drop (the F1 licensure case)."""
    blob = _fused_blob()
    first_line = blob["text"].split("\n", 1)[1].split("\n", 1)[0]
    dup = {"text": first_line}  # exact copy of one blob line -> verbatim contained
    assert _squash(dup["text"]) in _squash(blob["text"])
    assert _drop_is_safe(dup, blob) is True
