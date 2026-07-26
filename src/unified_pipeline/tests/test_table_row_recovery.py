"""Regression tests for issue #420: table rows lost before they become entries.

Stage 2 presents each table row to the model as "parent.row" and looks the answer
back up by string key. The model replies in JSON, where those indices are
NUMBERS: "114.10" parses to the float 114.1, str() renders it back as "114.1",
and the lookup lands on row 1. Row 10 is unreachable -- and so is every row whose
index ends in a zero. C0ZGFW element 114 lost rows 10/20/30/40/50 and element 109
lost row 10; their content survived only inside the whole-table blob, which is
what made that blob load-bearing and #227's dedup drop a real content loss.

`recover_unclaimed_table_rows` is the deterministic backstop: any row no
delimiter claimed is emitted as its own entry.

Pure function: no LLM, no DB, no network, no PII. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_table_row_recovery.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage_2_entry_extraction import (  # noqa: E402
    recover_unclaimed_table_rows,
)


def _row(idx, text, rtype="table_row"):
    return {"idx": idx, "type": rtype, "full_text": text, "table_index": 5,
            "row_index": idx, "parent_idx": 114}


def test_trailing_zero_row_is_recovered():
    """The #420 mechanism: str(114.10) == '114.1', so row 10 is never claimed."""
    elements = [_row("114.1", "SGIM workshop"), _row("114.10", "POCUS mentor")]
    # what the delimiter loop could actually resolve: float 114.10 -> "114.1"
    claimed = {str(114.10)}

    assert claimed == {"114.1"}, "precondition: the float collision is real"

    recovered = recover_unclaimed_table_rows(elements, claimed)

    assert [r["element_idx_start"] for r in recovered] == ["114.10"]
    assert recovered[0]["text"] == "POCUS mentor"
    assert recovered[0]["recovered_row"] is True


def test_every_multiple_of_ten_is_recovered():
    """C0ZGFW element 114's actual loss pattern: rows 10, 20, 30, 40, 50."""
    elements = [_row(f"114.{i}", f"record {i}") for i in range(1, 60)]
    claimed = {str(float(f"114.{i}")) for i in range(1, 60)}

    recovered = recover_unclaimed_table_rows(elements, claimed)
    lost = sorted(int(r["element_idx_start"].split(".")[1]) for r in recovered)

    assert lost == [10, 20, 30, 40, 50]


def test_claimed_rows_are_not_duplicated():
    elements = [_row("114.1", "SGIM workshop"), _row("114.2", "DDW workshop")]

    assert recover_unclaimed_table_rows(elements, {"114.1", "114.2"}) == []


def test_blank_rows_are_not_recovered():
    """Empty rows carry nothing downstream and would just cost LLM calls."""
    elements = [_row("114.3", "   "), _row("114.4", "")]

    assert recover_unclaimed_table_rows(elements, set()) == []


def test_separator_only_rows_are_not_recovered():
    """A row of empty cells flattens to '|' -- non-empty by len(), but noise.

    Observed live on C0ZGFW as recovered entry '100.3' with text '|'.
    """
    elements = [_row("100.3", "|"), _row("100.4", "| |"), _row("100.5", " - | - ")]

    assert recover_unclaimed_table_rows(elements, set()) == []


def test_non_row_elements_are_ignored():
    elements = [
        _row("114.1", "a real row"),
        {"idx": 40, "type": "paragraph", "text": "prose that is not a table row"},
        {"idx": "table_110", "type": "table", "text": "whole table"},
    ]

    recovered = recover_unclaimed_table_rows(elements, set())

    assert [r["element_idx_start"] for r in recovered] == ["114.1"]


def test_recovered_rows_are_marked_and_lower_confidence():
    """Downstream should be able to tell structural recovery from model output."""
    recovered = recover_unclaimed_table_rows([_row("114.10", "POCUS mentor")], set())

    assert recovered[0]["recovered_row"] is True
    assert recovered[0]["confidence"] < 1.0
    assert recovered[0]["element_type"] == "table_row"
    assert recovered[0]["parent_idx"] == 114


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")
