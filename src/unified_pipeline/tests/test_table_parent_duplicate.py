"""Regression tests for issue #418: a whole-table parent entry emitted alongside
its own split rows.

Stage 2 splits a table element into per-row entries ("109.4", "109.5", ...). The
LLM can ALSO return the same element as a bare whole-table span ("109..109").
`remove_subset_delimiters` was meant to collapse that, but `normalize_idx` maps a
bare parent to the POINT (109, 0), so a row at (109, 4) is never contained by it
and both survive -- the table is emitted, and rendered, twice.

The parent may NOT simply be dropped. Measured over the S3 corpus, 44 of 59 such
parents carry at least one record that never became a sibling row, so an
unconditional drop loses content. The parent is therefore removed only when every
one of its content lines is already present in its own rows.

Pure function: no LLM, no DB, no network, no PII. Run with:

    python3 -m pytest src/unified_pipeline/tests/test_table_parent_duplicate.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.docx_structure_extractor import (  # noqa: E402
    _flatten_table_content_text,
)
from unified_pipeline.stage_2_entry_extraction import (  # noqa: E402
    get_element_text,
    join_row_cells,
    remove_subset_delimiters,
    row_cell_texts,
)

ROWS = [
    "Introduction to POCUS | Weill Cornell Medicine, New York, NY | 2024",
    "Advanced Lung Ultrasound | Mount Sinai Medical Center, New York, NY | 2023",
    "Critical Care Ultrasound Course | Westchester Medical Center | 2022",
]


def _d(start, end=None, text=""):
    return {
        "element_idx_start": start,
        "element_idx_end": end if end is not None else start,
        "text": text,
    }


def _starts(delims):
    return [d["element_idx_start"] for d in delims]


def test_fully_covered_table_parent_is_dropped():
    """The #418 case: parent repeats exactly what its own rows already carry."""
    blob = _d(109, text="Title\tDates\n" + "\n".join(ROWS))
    rows = [_d(f"109.{i + 1}", text=t) for i, t in enumerate(ROWS)]

    kept = remove_subset_delimiters([blob] + rows)

    assert _starts(kept) == ["109.1", "109.2", "109.3"]
    assert 109 not in _starts(kept)


def test_long_header_label_blocks_the_drop_known_limitation():
    """Documents a deliberate limitation, not desired behaviour.

    A template header cell longer than the 12-char floor ('Institution/Location')
    is not present in any row entry, so strict coverage refuses the drop and the
    table still double-renders. This is why the fix is a no-op on C0ZGFW. Fixing
    it means exempting the header row from coverage, which risks exempting real
    content when the first line is not a header -- not taken without a corpus gate
    proving it safe. If that gate is done, invert this test.
    """
    blob = _d(109, text="Title\tInstitution/Location\tDates\n" + "\n".join(ROWS))
    rows = [_d(f"109.{i + 1}", text=t) for i, t in enumerate(ROWS)]

    assert 109 in _starts(remove_subset_delimiters([blob] + rows))


def test_parent_with_uncovered_record_is_kept():
    """The loss guard. A record present ONLY in the parent must survive.

    This is the real corpus shape: the splitter emits some rows but not all, so
    the parent still holds the orphans. Dropping it here would have lost a $2.5M
    grant on MKEQKW and four records on C0ZGFW.
    """
    orphan = "The Mednet - expert consultant | remote | 2025 to present"
    blob = _d(109, text="\n".join(ROWS + [orphan]))
    rows = [_d(f"109.{i + 1}", text=t) for i, t in enumerate(ROWS)]

    kept = remove_subset_delimiters([blob] + rows)

    assert 109 in _starts(kept), "parent holding an uncovered record must be kept"
    assert len(kept) == 4


def test_parent_kept_when_no_rows_were_split_out():
    """No sub-rows for that element => nothing to compare against, so keep it."""
    kept = remove_subset_delimiters(
        [_d(109, text="\n".join(ROWS)), _d("77.1", text="unrelated row text here")]
    )

    assert 109 in _starts(kept)


def test_prose_composite_still_wins_over_its_subparts():
    """Existing behaviour for prose spans is unchanged: largest span wins."""
    kept = remove_subset_delimiters(
        [_d(19, 25, "composite span"), _d(19, 19, "part one"), _d(20, 20, "part two")]
    )

    assert _starts(kept) == [19]
    assert len(kept) == 1


def test_each_table_resolved_independently():
    """A covered parent goes; an uncovered parent on another table stays."""
    covered = [_d(109, text="\n".join(ROWS))] + [
        _d(f"109.{i + 1}", text=t) for i, t in enumerate(ROWS)
    ]
    uncovered = [
        _d(114, text="POCUS mentor | SHM SIG Workshop | 2025"),
        _d("114.1", text="something else entirely, unrelated"),
    ]

    kept = _starts(remove_subset_delimiters(covered + uncovered))

    assert 109 not in kept
    assert 114 in kept


def test_header_only_lines_do_not_block_the_drop():
    """Short template labels are below the 12-char floor and must not count."""
    blob = _d(109, text="Title\tDates\n(yyyy)\n" + "\n".join(ROWS))
    rows = [_d(f"109.{i + 1}", text=t) for i, t in enumerate(ROWS)]

    assert 109 not in _starts(remove_subset_delimiters([blob] + rows))


def test_empty_input_is_passed_through():
    assert remove_subset_delimiters([]) == []


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all passed")


def test_parent_of_multi_paragraph_first_cell_rows_is_dropped_and_rows_kept():
    """#488: a row whose cell 0 spans lines gets its trailing columns on cell
    0's FIRST paragraph. The parent's text is built by the same join, so its
    lines are found in the rows and the parent drops -- not the fixed row."""
    table = [
        [{"text": "Visiting Faculty Program\n- Ultrasound training course"},
         {"text": "Fictional City, Country"}, {"text": "November 2024 to present"}],
        [{"text": "Another Program Entry"}, {"text": "Imaginary Place"},
         {"text": "2020 to 2022"}],
    ]
    parent = _d(7, text=_flatten_table_content_text(table))
    rows = [_d(f"7.{i}", text=join_row_cells(row_cell_texts(r)))
            for i, r in enumerate(table)]

    assert _starts(remove_subset_delimiters([parent] + rows)) == ["7.0", "7.1"]


def test_legacy_table_element_text_uses_the_same_row_join():
    table = [[{"text": "Title\n- a"}, {"text": "2024"}]]
    assert get_element_text({"type": "table", "data": table}) == \
        _flatten_table_content_text(table) == "Title | 2024\n- a"
