"""Behaviour tests for stage-2 pure helpers not already covered elsewhere (#704).

Covers: get_hierarchy_path, build_element_index_map, get_element_text,
split_merged_row_into_pseudo_rows, extract_leaf_sections_with_boundaries,
collect_header_indices, collect_header_info, remove_subset_delimiters,
recover_unclaimed_table_rows, _dedup_idx_key, filter_extraction_noise.

The first seven of those have NO existing coverage anywhere in the test
suite. The last four (remove_subset_delimiters, recover_unclaimed_table_rows,
_dedup_idx_key, filter_extraction_noise) already have thorough behavioural
coverage in test_table_parent_duplicate.py, test_table_row_recovery.py and
test_stage2_noise_filter.py respectively -- the tests added here for them use
different fixtures/scenarios (malformed keys, vacuous-coverage edge case,
non-overlapping bare spans, mixed batches) rather than repeating those files,
so that this file's own coverage run (see packet instructions) still clears
the per-function bar independent of the sibling files.

detect_entries_for_section and run_stage_2 are out of scope for this file (a
sibling packet covers them) -- not tested here.

No LLM: none of the functions in scope call call_llm, so nothing is stubbed.
No network, no PII: every fixture below is synthetic.

Untestable in isolation: none of the in-scope functions have argparse/__main__
gated code; every statement in their bodies is reachable from pure inputs.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage2_helpers_behaviour.py -p no:cacheprovider
"""

import sys
from pathlib import Path

# Ensure the repo's ``src`` directory is importable regardless of cwd/rootdir.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline import stage_2_entry_extraction as stage2  # noqa: E402

get_hierarchy_path = stage2.get_hierarchy_path
build_element_index_map = stage2.build_element_index_map
get_element_text = stage2.get_element_text
split_merged_row_into_pseudo_rows = stage2.split_merged_row_into_pseudo_rows
extract_leaf_sections_with_boundaries = stage2.extract_leaf_sections_with_boundaries
collect_header_indices = stage2.collect_header_indices
collect_header_info = stage2.collect_header_info
remove_subset_delimiters = stage2.remove_subset_delimiters
recover_unclaimed_table_rows = stage2.recover_unclaimed_table_rows
_dedup_idx_key = stage2._dedup_idx_key
filter_extraction_noise = stage2.filter_extraction_noise


# --------------------------------------------------------- get_hierarchy_path

def test_get_hierarchy_path_appends_stripped_text_to_new_list():
    node = {"text": "  Publications  "}
    assert get_hierarchy_path(node) == ["Publications"]


def test_get_hierarchy_path_defaults_to_fresh_list_each_call():
    """current_path=None must not be a shared mutable default -- two
    independent calls must not see each other's appended text."""
    first = get_hierarchy_path({"text": "A"})
    second = get_hierarchy_path({"text": "B"})
    assert first == ["A"]
    assert second == ["B"]


def test_get_hierarchy_path_blank_text_leaves_path_unchanged():
    existing = ["PARENT"]
    result = get_hierarchy_path({"text": "   "}, existing)
    assert result == ["PARENT"]


def test_get_hierarchy_path_appends_onto_provided_path():
    existing = ["GRANTS"]
    result = get_hierarchy_path({"text": "Awarded"}, existing)
    assert result == ["GRANTS", "Awarded"]


# ----------------------------------------------------- build_element_index_map

def test_build_element_index_map_uses_unified_idx_when_present():
    doc = {"elements": [{"unified_idx": 3, "idx": 99, "type": "paragraph"}]}
    result = build_element_index_map(doc)
    assert result == {3: {"unified_idx": 3, "idx": 99, "type": "paragraph"}}


def test_build_element_index_map_falls_back_to_idx():
    doc = {"elements": [{"idx": 7, "type": "paragraph", "text": "x"}]}
    result = build_element_index_map(doc)
    assert 7 in result
    assert result[7]["text"] == "x"


def test_build_element_index_map_keeps_table_prefixed_string_keys():
    doc = {"elements": [{"idx": "table_12", "type": "table"}]}
    result = build_element_index_map(doc)
    assert set(result.keys()) == {"table_12"}


def test_build_element_index_map_drops_non_int_non_table_indices():
    """A float or a bare (non-'table_') string idx is neither branch and is
    silently excluded from the map."""
    doc = {"elements": [
        {"idx": 3.5, "type": "paragraph"},
        {"idx": "loose", "type": "paragraph"},
        {"idx": 4, "type": "paragraph"},
    ]}
    result = build_element_index_map(doc)
    assert set(result.keys()) == {4}


def test_build_element_index_map_empty_doc_structure_returns_empty_map():
    assert build_element_index_map({}) == {}


# ----------------------------------------------------------- get_element_text

def test_get_element_text_paragraph_strips_whitespace():
    assert get_element_text({"type": "paragraph", "text": "  hi  "}) == "hi"


def test_get_element_text_table_content_strips_pre_flattened_text():
    assert get_element_text({"type": "table_content", "text": " flat \n"}) == "flat"


def test_get_element_text_table_flattens_rows_tab_and_newline_joined():
    element = {
        "type": "table",
        "data": [
            [{"text": "Name"}, {"text": "Year"}],
            [{"text": "Smith"}, {"text": "2020"}],
        ],
    }
    assert get_element_text(element) == "Name\tYear\nSmith\t2020"


def test_get_element_text_unknown_type_falls_back_to_stripped_text():
    assert get_element_text({"type": "mystery", "text": " misc "}) == "misc"


def test_get_element_text_missing_type_falls_back_to_stripped_text():
    assert get_element_text({"text": " no type key "}) == "no type key"


# ------------------------------------------- split_merged_row_into_pseudo_rows

def test_split_merged_row_rejects_non_list_input():
    assert split_merged_row_into_pseudo_rows("not a list") is None


def test_split_merged_row_rejects_single_cell_row():
    assert split_merged_row_into_pseudo_rows([{"text": "only one cell"}]) is None


def test_split_merged_row_rejects_when_not_all_cells_share_segment_count():
    row = [
        {"text": "Award A\n\nAward B"},   # 2 segments
        {"text": "Just one blob"},          # 1 segment
    ]
    assert split_merged_row_into_pseudo_rows(row) is None


def test_split_merged_row_rejects_single_segment_per_cell():
    """segment_counts[0] <= 1 means there's nothing to split -- ordinary row."""
    row = [{"text": "Just a title"}, {"text": "Just a date"}]
    assert split_merged_row_into_pseudo_rows(row) is None


def test_split_merged_row_single_newline_is_not_a_row_boundary():
    """A soft-wrapped single '\\n' inside a cell (e.g. a title that wrapped
    across two lines in the source docx) must NOT be treated as a
    merged-row boundary -- only a blank-line ('\\n\\n+') separator counts.
    Two cells that each carry exactly one bare newline are still ONE row of
    text, not two phantom rows zipped together."""
    row = [{"text": "Award Alpha\ncontinued"}, {"text": "2019\nNIH"}]
    assert split_merged_row_into_pseudo_rows(row) is None


def test_split_merged_row_zips_matching_segment_counts_into_pseudo_rows():
    row = [
        {"text": "Award Alpha\n\nAward Beta"},
        {"text": "2019\n\n2021"},
    ]
    result = split_merged_row_into_pseudo_rows(row)
    assert result == [["Award Alpha", "2019"], ["Award Beta", "2021"]]


def test_split_merged_row_accepts_non_dict_cells_via_str_coercion():
    row = ["Alpha\n\nBeta", "1\n\n2"]
    result = split_merged_row_into_pseudo_rows(row)
    assert result == [["Alpha", "1"], ["Beta", "2"]]


# ------------------------------------------- extract_leaf_sections_with_boundaries

def _b(hierarchy, start, end, has_children=False):
    return {
        "hierarchy": hierarchy,
        "element_idx_start": start,
        "element_idx_end": end,
        "has_children": has_children,
    }


def test_extract_leaf_sections_plain_leaves_pass_through_unchanged():
    boundaries = [_b(["A"], 1, 5), _b(["B"], 6, 10)]
    result = extract_leaf_sections_with_boundaries(boundaries, [])
    assert result == [(["A"], 1, 5), (["B"], 6, 10)]


def test_extract_leaf_sections_emits_gap_before_first_child():
    boundaries = [
        _b(["PUBLICATIONS"], 20, 40, has_children=True),
        _b(["PUBLICATIONS", "Book Chapters"], 25, 40),
    ]
    result = extract_leaf_sections_with_boundaries(boundaries, [])
    assert result == [
        (["PUBLICATIONS"], 21, 24),
        (["PUBLICATIONS", "Book Chapters"], 25, 40),
    ]


def test_extract_leaf_sections_no_gap_when_child_immediately_follows():
    boundaries = [
        _b(["X"], 10, 30, has_children=True),
        _b(["X", "Y"], 11, 30),
    ]
    result = extract_leaf_sections_with_boundaries(boundaries, [])
    assert result == [(["X", "Y"], 11, 30)]


def test_extract_leaf_sections_first_child_start_takes_the_minimum():
    """Children out of document order: first_child_start must track the
    smallest start seen, not the first one processed."""
    boundaries = [
        _b(["P"], 0, 50, has_children=True),
        _b(["P", "C2"], 30, 40),
        _b(["P", "C1"], 10, 29),
    ]
    result = extract_leaf_sections_with_boundaries(boundaries, [])
    assert result == [
        (["P"], 1, 9),
        (["P", "C2"], 30, 40),
        (["P", "C1"], 10, 29),
    ]


def test_extract_leaf_sections_skips_boundary_with_missing_indices():
    boundaries = [_b(["Z"], None, 5), _b(["Z"], 6, None)]
    assert extract_leaf_sections_with_boundaries(boundaries, []) == []


def test_extract_leaf_sections_parent_with_no_start_index_never_registered():
    """A has_children boundary whose own element_idx_start is None is never
    added to parent_sections (it also fails the later None-index check), so
    it produces no leaf/gap output at all -- distinct from the ordinary
    'missing indices' skip above, this exercises the has_children branch's
    own None guard, not the plain-leaf one."""
    boundaries = [_b(["ORPHAN"], None, None, has_children=True)]
    assert extract_leaf_sections_with_boundaries(boundaries, []) == []


def test_extract_leaf_sections_child_of_unregistered_parent_treated_as_plain_leaf():
    """A child boundary (hierarchy depth > 1) whose parent path was never
    registered as a has_children boundary at all (no 'PARENT' entry exists
    in section_boundaries) must not be looked up as a gap candidate -- it
    passes straight through as an ordinary leaf, unaffected by the parent
    machinery."""
    boundaries = [_b(["PARENT", "Child"], 5, 10)]
    result = extract_leaf_sections_with_boundaries(boundaries, [])
    assert result == [(["PARENT", "Child"], 5, 10)]


def test_extract_leaf_sections_first_child_start_keeps_earlier_minimum():
    """Children already in document order (increasing start): once the
    first child sets first_child_start, a LATER child with a larger start
    must not overwrite it -- the 'or start_idx < current_first' disjunct's
    false side."""
    boundaries = [
        _b(["P"], 0, 50, has_children=True),
        _b(["P", "C1"], 10, 29),
        _b(["P", "C2"], 30, 40),
    ]
    result = extract_leaf_sections_with_boundaries(boundaries, [])
    assert result == [
        (["P"], 1, 9),
        (["P", "C1"], 10, 29),
        (["P", "C2"], 30, 40),
    ]


# ------------------------------------------------------- collect_header_indices

def test_collect_header_indices_recurses_and_skips_missing_or_none_idx():
    hierarchy = [
        {"text": "A", "element_idx": 1, "children": [
            {"text": "A1", "element_idx": 2, "children": []},
            {"text": "A2", "element_idx": None},
            {"text": "A3"},
        ]},
        {"text": "B", "element_idx": 5},
    ]
    assert collect_header_indices(hierarchy) == {1, 2, 5}


def test_collect_header_indices_empty_hierarchy_returns_empty_set():
    assert collect_header_indices([]) == set()


# --------------------------------------------------------- collect_header_info

def test_collect_header_info_builds_paths_and_skips_missing_idx():
    hierarchy = [
        {"text": "SECTION", "element_idx": 1, "children": [
            {"text": "Sub", "element_idx": 2, "children": [
                {"text": "", "element_idx": 3},
            ]},
            {"text": "NoIdx", "children": [
                {"text": "Deep", "element_idx": 9},
            ]},
        ]},
    ]
    info = collect_header_info(hierarchy)
    assert info == {
        1: ["SECTION"],
        2: ["SECTION", "Sub"],
        3: ["SECTION", "Sub"],
        9: ["SECTION", "NoIdx", "Deep"],
    }


def test_collect_header_info_empty_hierarchy_returns_empty_dict():
    assert collect_header_info([]) == {}


# ---------------------------------------------------- remove_subset_delimiters

def _d(start, end=None, text=""):
    return {
        "element_idx_start": start,
        "element_idx_end": end if end is not None else start,
        "text": text,
    }


def test_remove_subset_delimiters_int_span_subset_is_dropped():
    kept = remove_subset_delimiters([_d(5, 10, "big"), _d(6, 8, "small")])
    assert [d["element_idx_start"] for d in kept] == [5]


def test_remove_subset_delimiters_exact_duplicate_span_collapsed_to_one():
    """Same start AND end (an exact re-emit, start == kept_start) must still
    collapse -- the containment check uses >=, not >, on the start bound."""
    kept = remove_subset_delimiters([_d(9, 12, "dup one"), _d(9, 12, "dup two")])
    assert len(kept) == 1


def test_remove_subset_delimiters_non_overlapping_bare_spans_both_kept():
    """One delimiter kept because it is NOT a subset of the other."""
    kept = remove_subset_delimiters([_d(1, 3, "first"), _d(4, 8, "second")])
    assert [d["element_idx_start"] for d in kept] == [1, 4]


def test_remove_subset_delimiters_same_start_largest_span_wins():
    """Two delimiters that SHARE a start index (the composite-entry case the
    docstring names: '19-25 AND its sub-parts 19-19') must be sorted largest
    span first, so the small one is absorbed as a subset of the big one
    rather than the big one being absorbed by (or merely ordered after) the
    small one. Fed in small-first order so a sort key that ignores span size
    would leave both in the result instead of dropping the small one."""
    kept = remove_subset_delimiters([_d(5, 5, "small"), _d(5, 10, "big")])
    assert [(d["element_idx_start"], d["element_idx_end"]) for d in kept] == [(5, 10)]


def test_remove_subset_delimiters_dotted_subrow_span_size_uses_sub_index_diff():
    """Two ROWS of the same composite parent (dotted idx, shared main '60'):
    span_size for a same-parent pair must measure the SUB-index difference
    (end[1] - start[1]), not the (always-zero) main-index difference --
    otherwise the wider row range never outranks the narrower point-row on
    sort, and the point that the range already contains survives alongside
    it instead of being dropped as a subset."""
    kept = remove_subset_delimiters([_d("60.1", "60.1", "point"), _d("60.1", "60.3", "range")])
    assert [(d["element_idx_start"], d["element_idx_end"]) for d in kept] == [("60.1", "60.3")]


def test_remove_subset_delimiters_table_prefixed_index_sorts_and_survives():
    kept = remove_subset_delimiters([_d("table_5", text="whole table blob")])
    assert [d["element_idx_start"] for d in kept] == ["table_5"]


def test_remove_subset_delimiters_table_index_not_swallowed_by_unrelated_span():
    """A `table_N` delimiter normalizes to a distinct, large sort key
    (1_000_000 + N) that keeps it out of an unrelated low-numbered
    paragraph span's containment range. Fed alongside a bare span (0, 3)
    that sorts first (as the larger, earlier-starting entry), the table
    entry must NOT be swallowed as a subset just because a broken
    normalize_idx could collapse every table_* index to the same point."""
    kept = remove_subset_delimiters(
        [_d(0, 3, "prose span"), _d("table_2", text="whole table blob")]
    )
    assert [d["element_idx_start"] for d in kept] == [0, "table_2"]


def test_remove_subset_delimiters_empty_input_passed_through():
    assert remove_subset_delimiters([]) == []


def test_remove_subset_delimiters_plain_numeric_string_index_no_dot():
    """A bare numeric-string idx (no '.', not 'table_') hits the else branch
    of normalize_idx and still participates in ordinary subset detection."""
    kept = remove_subset_delimiters([_d("5", "10", "big"), _d("6", "8", "small")])
    assert [d["element_idx_start"] for d in kept] == ["5"]


def test_remove_subset_delimiters_fully_covered_parent_is_dropped():
    rows_text = [
        "Excellence in Teaching Award | Example Medical College | 2021",
        "Outstanding Mentor Award | Department of Medicine | 2022",
    ]
    blob = _d(60, text="Award\tInstitution\tYear\n" + "\n".join(rows_text))
    rows = [_d(f"60.{i + 1}", text=t) for i, t in enumerate(rows_text)]

    kept = remove_subset_delimiters([blob] + rows)

    assert [d["element_idx_start"] for d in kept] == ["60.1", "60.2"]
    assert 60 not in [d["element_idx_start"] for d in kept]


def test_remove_subset_delimiters_bare_span_unrelated_to_any_split_table_kept():
    """sub_row_parents is non-empty (table 60 was split), but a bare span for
    an unrelated table (114, no '114.x' rows at all) must not be touched by
    the redundant-parent check -- main not in sub_row_parents -> False."""
    split_table = [_d(60, text="parent"), _d("60.1", text="Some row text here")]
    unrelated = _d(114, text="Unrelated Award | Some Sponsor | 2020")

    kept = remove_subset_delimiters(split_table + [unrelated])

    assert 114 in [d["element_idx_start"] for d in kept]


def test_remove_subset_delimiters_short_only_parent_is_not_vacuously_dropped():
    """#855: content_lines() drops every line under 12 chars, so a
    table-parent whose full text is entirely short cells (e.g. 'PI',
    '2020') yields an empty set; all(x for x in <empty>) is vacuously True,
    so is_redundant_table_parent must not treat that as a PROVEN duplicate
    -- nothing was actually checked against the sibling rows."""
    blob = _d(50, text="PI\n2020\nWCM")
    row = _d("50.1", text="totally unrelated row content that is unverified")
    kept = remove_subset_delimiters([blob, row])
    assert 50 in [d["element_idx_start"] for d in kept]


def test_remove_subset_delimiters_mixed_subindexed_start_bare_end_both_rows_survive():
    """#854: the LLM returns a sub-indexed start ("9.1") paired with a bare
    int end (9). normalize_idx maps that bare end to (9, 0), which sorts
    BELOW the start's own (9, 1) -- the delimiter's "end" then reads as
    earlier than its "start", so it tested as fully contained inside the
    sibling row "9.0"/"9.0" and was silently dropped. Both rows must
    survive, and the raw element_idx_end value (9) must be left unchanged
    -- normalization corrects only the sort/containment key, not the
    delimiter's own data."""
    delimiters = [
        _d("9.1", 9, "2021 | Second row"),
        _d("9.0", "9.0", "2020 | First row"),
    ]
    kept = remove_subset_delimiters(delimiters)
    assert [(d["element_idx_start"], d["element_idx_end"]) for d in kept] == [
        ("9.0", "9.0"),
        ("9.1", 9),
    ]


def test_remove_subset_delimiters_plain_int_start_and_end_unchanged():
    """Negative case for #854's fix: a delimiter that is bare on BOTH ends
    must not be touched by the mixed-shape normalization -- ordinary int
    spans behave exactly as before."""
    kept = remove_subset_delimiters([_d(9, 9, "solo"), _d(20, 25, "other")])
    assert [(d["element_idx_start"], d["element_idx_end"]) for d in kept] == [
        (9, 9),
        (20, 25),
    ]


def test_remove_subset_delimiters_dotted_start_and_end_unchanged():
    """Negative case for #854's fix: a delimiter whose end is ITSELF
    sub-indexed ("9.0") must not be touched -- the fix only fires when the
    end is bare."""
    kept = remove_subset_delimiters(
        [_d("9.0", "9.0", "row"), _d("9.1", "9.1", "other row")]
    )
    assert [(d["element_idx_start"], d["element_idx_end"]) for d in kept] == [
        ("9.0", "9.0"),
        ("9.1", "9.1"),
    ]


def test_remove_subset_delimiters_true_subset_of_dotted_range_still_removed():
    """Negative case for #854's fix: a genuine subset ("9.1".."9.1" fully
    inside "9.0".."9.3") must still be dropped -- the fix does not weaken
    ordinary dotted-range containment."""
    parent = _d("9.0", "9.3", "whole range")
    child = _d("9.1", "9.1", "contained point")
    kept = remove_subset_delimiters([child, parent])
    assert [(d["element_idx_start"], d["element_idx_end"]) for d in kept] == [
        ("9.0", "9.3"),
    ]


# ------------------------------------------------ recover_unclaimed_table_rows

def _row(idx, text, table_index=2, parent_idx=9):
    return {
        "idx": idx, "type": "table_row", "full_text": text,
        "table_index": table_index, "row_index": idx, "parent_idx": parent_idx,
    }


def test_recover_unclaimed_table_rows_skips_claimed_and_non_row_elements():
    elements = [
        _row("9.1", "Adjunct Faculty Award"),
        _row("9.2", "already claimed, must not recur"),
        {"idx": 40, "type": "paragraph", "text": "prose, not a row"},
    ]
    recovered = recover_unclaimed_table_rows(elements, claimed_row_keys={"9.2"})
    assert len(recovered) == 1
    assert recovered[0]["element_idx_start"] == "9.1"
    assert recovered[0]["text"] == "Adjunct Faculty Award"
    assert recovered[0]["table_index"] == 2
    assert recovered[0]["confidence"] == 0.5


def test_recover_unclaimed_table_rows_no_unclaimed_rows_returns_empty():
    elements = [_row("9.1", "claimed already")]
    assert recover_unclaimed_table_rows(elements, claimed_row_keys={"9.1"}) == []


def test_recover_unclaimed_table_rows_separator_only_row_not_recovered():
    """A row of empty cells flattens to a separator string with no alnum
    content and must not be recovered as a phantom entry."""
    elements = [_row("9.3", " | - | "), _row("9.4", "real content here")]
    recovered = recover_unclaimed_table_rows(elements, claimed_row_keys=set())
    assert [r["element_idx_start"] for r in recovered] == ["9.4"]


# ---------------------------------------------------------------- _dedup_idx_key

def test_dedup_idx_key_malformed_strings_fall_back_to_string_identity():
    assert _dedup_idx_key("22.abc") == ("22.abc", 0.0)
    assert _dedup_idx_key("table_x") == ("table_x", 0.0)
    assert _dedup_idx_key("plain-noise") == ("plain-noise", 0.0)


def test_dedup_idx_key_plain_numeric_string_without_dot_converts():
    assert _dedup_idx_key("42") == (42.0, 0.0)


# ------------------------------------------------------- filter_extraction_noise

def test_filter_extraction_noise_mixed_batch_drops_empty_and_dup_keeps_rest():
    entries = [
        {"element_type": "paragraph", "text": "", "element_idx_start": 1},
        {"element_type": "table_row", "text": "Grant Title Here", "element_idx_start": 2},
        {"element_type": "table_row", "text": "Grant Title Here", "element_idx_start": 2},
        {"element_type": "header", "text": "", "element_idx_start": 3},
    ]
    kept = filter_extraction_noise(entries)
    assert [(e["element_type"], e["element_idx_start"]) for e in kept] == [
        ("table_row", 2), ("header", 3),
    ]


def test_filter_extraction_noise_no_drops_returns_all_entries_untouched():
    entries = [
        {"element_type": "paragraph", "text": "unique one", "element_idx_start": 1},
        {"element_type": "paragraph", "text": "unique two", "element_idx_start": 2},
    ]
    assert filter_extraction_noise(entries) == entries
