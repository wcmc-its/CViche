"""Behaviour tests for stage-2 pure helpers not already covered elsewhere (#704).

Covers: get_hierarchy_path, build_element_index_map, get_element_text,
split_merged_row_into_pseudo_rows, extract_leaf_sections_with_boundaries,
collect_header_indices, collect_header_info, remove_subset_delimiters,
recover_unclaimed_table_rows, _dedup_idx_key, filter_extraction_noise,
fold_labelled_detail_entries (#986).

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

import pytest  # noqa: E402
from docx import Document  # noqa: E402

from unified_pipeline import stage_2_entry_extraction as stage2  # noqa: E402
from unified_pipeline.core.docx_structure_extractor import extract_unified_elements  # noqa: E402

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


def test_get_element_text_table_flattens_rows_pipe_and_newline_joined():
    element = {
        "type": "table",
        "data": [
            [{"text": "Name"}, {"text": "Year"}],
            [{"text": "Smith"}, {"text": "2020"}],
        ],
    }
    assert get_element_text(element) == "Name | Year\nSmith | 2020"


def test_get_element_text_unknown_type_falls_back_to_stripped_text():
    assert get_element_text({"type": "mystery", "text": " misc "}) == "misc"


def test_get_element_text_missing_type_falls_back_to_stripped_text():
    assert get_element_text({"text": " no type key "}) == "no type key"


def test_get_element_text_next_paragraph_dob_scrub_not_readable_from_data(tmp_path):
    # Reader-level regression (#847 residual round 4): a "Date of Birth:"
    # label paragraph directly followed by a table -- the pre-LLM scrub
    # (core/docx_structure_extractor.py::_scrub_pre_llm_pii_next_element)
    # used to update only that table element's pre-flattened `text`, never
    # its per-cell `data`. This branch reads `data` (tab/newline-joined),
    # not `text` -- so the raw DOB still came out here even after the
    # element's own `text` field had already been scrubbed clean.
    doc = Document()
    doc.add_paragraph("Date of Birth:")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "01/02/1970"
    table.cell(0, 1).text = "Example City"
    docx_path = tmp_path / "next_element_table_dob.docx"
    doc.save(str(docx_path))

    elements = extract_unified_elements(str(docx_path))["elements"]
    table_element = next(e for e in elements if e.get("data"))

    out = get_element_text(table_element)
    assert "01/02/1970" not in out
    assert out == "[withheld] | Example City"
    # ...and the same element's flattened `text` (extract_text_from_docx's reader).
    assert "01/02/1970" not in table_element["text"]


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


def test_parent_gap_ends_at_the_next_header_in_document_order():
    # S1 reproducer 3 (#916): P > N > G, P > D. N starts at 1, so P's gap
    # (start_idx + 1 = 1) is not > next header start (1) -- no ["P"]
    # pseudo-leaf. N's own gap likewise collapses (next header G at 2 is
    # not > N's start_idx + 1 = 2). Only the true leaves G and D survive.
    boundaries = [
        _b(["P"], 0, 9, has_children=True),
        _b(["P", "N"], 1, 4, has_children=True),
        _b(["P", "N", "G"], 2, 3),
        _b(["P", "D"], 8, 9),
    ]
    result = extract_leaf_sections_with_boundaries(boundaries, [])
    assert result == [(["P", "N", "G"], 2, 3), (["P", "D"], 8, 9)]


def test_parent_gap_stops_at_an_out_of_order_foreign_section():
    # P has_children and starts at 0; its own mapped child C starts at 10,
    # but a foreign section Q (unrelated hierarchy) sits at 5 -- the gap
    # must stop before Q, not run all the way to C.
    boundaries = [
        _b(["P"], 0, 20, has_children=True),
        _b(["P", "C"], 10, 20),
        _b(["Q"], 5, 9),
    ]
    result = extract_leaf_sections_with_boundaries(boundaries, [])
    assert result == [(["P"], 1, 4), (["P", "C"], 10, 20), (["Q"], 5, 9)]


def test_parent_with_a_child_at_its_own_start_gets_no_gap():
    # Stage 1a mapped a parent header and a child header to the SAME
    # paragraph, so C starts at the same element_idx as its parent P (#916).
    # The old `other_start > start_idx` comparison made C invisible to the
    # gap rule (a strict `>` excludes an equal start), so P got a
    # [start+1 .. next-1] gap that duplicated C's whole leaf range. Once
    # same-start boundaries count, first_child_start == start_idx, the
    # `> start_idx + 1` guard fails, and no ["P"] pseudo-leaf is emitted.
    boundaries = [
        _b(["P"], 5, 9, has_children=True),
        _b(["P", "C"], 5, 8),
        _b(["D"], 9, 9),
    ]
    result = extract_leaf_sections_with_boundaries(boundaries, [])
    assert result == [(["P", "C"], 5, 8), (["D"], 9, 9)]


def test_two_parents_at_the_same_start_emit_exactly_one_gap():
    # Round 2 of #916: two has_children boundaries mapped to the SAME start
    # (P listed before Q). The round-1 `>=` rule let each same-start parent
    # suppress the OTHER's gap, so with two parents at the same start
    # neither emitted one and elements 6-7 (P's own content before the
    # first real child C@8) were extracted by nobody. The earlier-listed
    # boundary (P) now owns the tie and emits the gap; Q, listed later,
    # emits none.
    boundaries = [
        _b(["P"], 5, 9, has_children=True),
        _b(["Q"], 5, 9, has_children=True),
        _b(["C"], 8, 8),
        _b(["K"], 9, 9),
    ]
    result = extract_leaf_sections_with_boundaries(boundaries, [])
    assert result == [(["P"], 6, 7), (["C"], 8, 8), (["K"], 9, 9)]


def test_a_duplicated_parent_record_emits_one_gap():
    # The same parent dict appears twice in section_boundaries (equal
    # values, distinct objects) -- e.g. stage 1a mapped it twice. List
    # POSITION, not equality, must distinguish "self" from "the other
    # same-start record": `first_index_at_start` records the index of the
    # first boundary seen at each start, and a boundary is suppressed when
    # its own index differs from that recorded index. Equality can't do
    # this job -- both copies compare equal, so an equality-keyed "first
    # one wins" check can't tell which occurrence is which; this is the
    # only test with two distinct, equal dicts at the same start, so it is
    # the sole killer of an equality-based regression here.
    parent = _b(["P"], 5, 9, has_children=True)
    boundaries = [parent, dict(parent), _b(["C"], 8, 8)]
    result = extract_leaf_sections_with_boundaries(boundaries, [])
    assert result == [(["P"], 6, 7), (["C"], 8, 8)]


def test_extract_leaf_sections_first_child_start_keeps_earlier_minimum():
    """The parent's gap ends at the earliest header start after the
    parent's own start, over ALL boundaries -- whichever position that
    header holds in `section_boundaries`'s list order. Here C1 (the
    earliest-starting header after P) also happens to come first in the
    list, so the gap ends at C1's start regardless of C2's later, larger
    start."""
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


def test_remove_subset_delimiters_kept_side_mixed_delimiter_uses_normalized_key():
    """#854, kept side: the containment check normalizes the KEPT delimiter
    too. A mixed ("9.1", 9) row already kept would otherwise read as the
    empty span (9,1)..(9,0), and an exact duplicate of it, ("9.1", "9.1"),
    would test as not-contained and survive as a second copy."""
    delimiters = [
        _d("9.1", 9, "2021 | Second row"),
        _d("9.1", "9.1", "2021 | Second row"),
    ]
    kept = remove_subset_delimiters(delimiters)
    assert len(kept) == 1


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


# ------------------------------------------------- fold_labelled_detail_entries (#986)

fold_labelled_detail_entries = stage2.fold_labelled_detail_entries
_HIER = ["Mentoring"]


def _fold_el(idx, left=0.0, etype="paragraph"):
    return {"unified_idx": idx, "type": etype, "text": f"line {idx}",
            "indent_left": left}


def _fold_entry(start, end=None, text="x", etype="paragraph", hier=None, conf=0.9):
    return {"element_idx_start": start, "element_idx_end": start if end is None else end,
            "element_type": etype, "confidence": conf, "text": text,
            "hierarchy": _HIER if hier is None else hier}


def _fold(entries, elements):
    return fold_labelled_detail_entries(entries, build_element_index_map({"elements": elements}))


def _spans(entries):
    return [(e["element_idx_start"], e["element_idx_end"]) for e in entries]


@pytest.mark.parametrize("label", ["project:", "role:"])
def test_fold_label_line_joins_parent_keeping_parent_fields(label):
    entries = [_fold_entry(3, text="2016 Ana Cruz, Graduate Student", conf=0.7),
               _fold_entry(4, text=label.upper() + " Study of tides", etype="break")]
    out = _fold(entries, [_fold_el(3), _fold_el(4)])
    assert len(out) == 1
    assert out[0] == {"element_idx_start": 3, "element_idx_end": 4, "element_type": "paragraph",
                      "confidence": 0.7, "hierarchy": _HIER,
                      "text": "2016 Ana Cruz, Graduate Student\t" + label.upper() + " Study of tides"}


def test_fold_does_not_mutate_input_entries():
    entries = [_fold_entry(3, text="2016 Ana Cruz"), _fold_entry(4, text="Project: tides")]
    _fold(entries, [_fold_el(3), _fold_el(4)])
    assert entries[0]["text"] == "2016 Ana Cruz" and entries[0]["element_idx_end"] == 3


@pytest.mark.parametrize("indent", [dict(), dict(left=0.5), dict(left=1.5), dict(left=4.5)])
def test_fold_indent_alone_never_folds_adjacent_sibling_lines(indent):
    # A student list or course list: same-shape lines, however far indented
    # under the line above. Fusing them made 3b code several records as one.
    entries = [_fold_entry(3, text="2016 Ana Cruz"), _fold_entry(4, text="Kim Lee"),
               _fold_entry(5, text="Lee Park"), _fold_entry(6, text="Kim Ortiz")]
    els = [_fold_el(3)] + [_fold_el(i, **indent) for i in (4, 5, 6)]
    assert _spans(_fold(entries, els)) == [(3, 3), (4, 4), (5, 5), (6, 6)]


def test_fold_label_line_folds_at_any_indent_including_flush():
    entries = [_fold_entry(3, text="2016 Ana Cruz"), _fold_entry(4, text="Project: tides")]
    for el4 in (_fold_el(4), _fold_el(4, left=1.5)):
        assert _spans(_fold(entries, [_fold_el(3), el4])) == [(3, 4)]


def test_fold_line_with_its_own_year_is_never_folded():
    entries = [_fold_entry(3, text="Lab Mentorship"), _fold_entry(4, text="Project: tides 2015")]
    assert _spans(_fold(entries, [_fold_el(3), _fold_el(4)])) == [(3, 3), (4, 4)]


def test_fold_requires_the_very_next_element():
    entries = [_fold_entry(3, text="2016 Ana Cruz"), _fold_entry(5, text="Project: tides")]
    assert _spans(_fold(entries, [_fold_el(3), _fold_el(4), _fold_el(5)])) == [(3, 3), (5, 5)]


def test_fold_never_crosses_a_hierarchy_boundary():
    entries = [_fold_entry(3, text="2016 Ana Cruz", hier=["A"]), _fold_entry(4, text="Project: tides", hier=["B"])]
    assert _spans(_fold(entries, [_fold_el(3), _fold_el(4)])) == [(3, 3), (4, 4)]


def test_fold_never_folds_into_or_out_of_a_header_or_empty_break():
    els = [_fold_el(3), _fold_el(4)]
    header_parent = [_fold_entry(3, text="MENTORING", etype="header"), _fold_entry(4, text="Project: tides")]
    assert _spans(_fold(header_parent, els)) == [(3, 3), (4, 4)]
    empty_parent = [_fold_entry(3, text="", etype="break"), _fold_entry(4, text="Project: tides")]
    assert _spans(_fold(empty_parent, els)) == [(3, 3), (4, 4)]
    header_child = [_fold_entry(3, text="2016 Ana Cruz"), _fold_entry(4, text="Project: tides", etype="header")]
    assert _spans(_fold(header_child, els)) == [(3, 3), (4, 4)]


def test_fold_leaves_table_rows_and_multi_element_lines_alone():
    els = [_fold_el(3), _fold_el(4), _fold_el(5)]
    rows = [_fold_entry(3, text="2016 Ana Cruz", etype="table_row"), _fold_entry(4, text="Project: tides", etype="table_row")]
    assert _spans(_fold(rows, els)) == [(3, 3), (4, 4)]
    multi = [_fold_entry(3, text="2016 Ana Cruz"), _fold_entry(4, 5, text="Project: tides")]
    assert _spans(_fold(multi, els)) == [(3, 3), (4, 5)]
    table_cell = [_fold_el(3), _fold_el(4, etype="table_content")]
    entries = [_fold_entry(3, text="2016 Ana Cruz"), _fold_entry(4, text="Project: tides")]
    assert _spans(_fold(entries, table_cell)) == [(3, 3), (4, 4)]


def test_fold_string_row_indices_pass_through_last_without_error():
    row = _fold_entry("22.2", text="Project: tides", etype="table_row")
    entries = [row, _fold_entry(3, text="2016 Ana Cruz"), _fold_entry(4, text="Project: tides")]
    out = _fold(entries, [_fold_el(3), _fold_el(4)])
    assert _spans(out) == [(3, 4), ("22.2", "22.2")]


def test_fold_unassigned_break_lines_fold_like_entries():
    # The detector missed a whole section: every line is a non-empty "break".
    entries = [_fold_entry(3, text="2016 Ana Cruz", etype="break"),
               _fold_entry(4, text="Project: tides", etype="break"),
               _fold_entry(5, text="2015 Lee Park", etype="break"),
               _fold_entry(6, text="Project: kelp", etype="break")]
    out = _fold(entries, [_fold_el(i) for i in range(3, 7)])
    assert _spans(out) == [(3, 4), (5, 6)]
    assert [e["element_type"] for e in out] == ["break", "break"]


def test_fold_chain_of_labels_extends_the_one_parent():
    entries = [_fold_entry(3, text="2008 Ana Cruz"), _fold_entry(4, text="Project: a"),
               _fold_entry(5, text="Project: b"), _fold_entry(6, text="2007 Lee Park")]
    out = _fold(entries, [_fold_el(i) for i in range(3, 7)])
    assert _spans(out) == [(3, 5), (6, 6)]
    assert out[0]["text"] == "2008 Ana Cruz\tProject: a\tProject: b"


@pytest.mark.parametrize("bare", ["Role:", "Project:  ", "ROLE:"])
def test_fold_bare_label_that_heads_the_lines_after_it_stays_separate(bare):
    entries = [_fold_entry(3, text="2016 Ana Cruz"), _fold_entry(4, text=bare),
               _fold_entry(5, text="Chair"), _fold_entry(6, text="Member")]
    out = _fold(entries, [_fold_el(i) for i in range(3, 7)])
    assert _spans(out) == [(3, 3), (4, 4), (5, 5), (6, 6)]


@pytest.mark.parametrize("parent_text", ["Past Grants:", "Past Grants:  ", "Past Grants:\tsee list"])
def test_fold_never_folds_a_label_line_into_a_sub_header_parent(parent_text):
    # The parent's FIRST line ends in a colon: it heads the lines below it.
    entries = [_fold_entry(3, text=parent_text), _fold_entry(4, text="Role: Chair")]
    assert _spans(_fold(entries, [_fold_el(3), _fold_el(4)])) == [(3, 3), (4, 4)]


def test_fold_parent_with_a_colon_after_its_first_line_still_takes_a_label_line():
    # Only the first line decides: a folded parent ending in "tides:" is a record.
    entries = [_fold_entry(3, text="2016 Ana Cruz\tProject: tides:"), _fold_entry(4, text="Role: Chair")]
    assert _spans(_fold(entries, [_fold_el(3), _fold_el(4)])) == [(3, 4)]


def test_fold_label_with_content_after_a_colon_still_folds():
    entries = [_fold_entry(3, text="2016 Ana Cruz"), _fold_entry(4, text="Role: Chair, tides:")]
    assert _spans(_fold(entries, [_fold_el(3), _fold_el(4)])) == [(3, 4)]


def test_fold_never_folds_a_template_instruction_child_into_a_real_entry():
    # _drop_template_instructions runs after the fold and would drop the whole
    # folded entry, real content included.
    parent = "2010-present Society of Example Medicine, member"
    els = [_fold_el(3), _fold_el(4)]
    for child in ("Role: (i.e., officer, secretary, chair, etc.)",
                  "Role (i.e., officer, secretary, chair, etc.)"):
        assert stage2._is_template_text(child)
        entries = [_fold_entry(3, text=parent), _fold_entry(4, text=child)]
        assert _spans(_fold(entries, els)) == [(3, 3), (4, 4)]
        assert stage2._drop_template_instructions(_fold(entries, els))[0]["text"] == parent


def test_fold_never_folds_a_label_line_into_a_template_instruction_parent():
    parent = "Role (i.e., officer, secretary, chair, etc.)"
    entries = [_fold_entry(3, text=parent), _fold_entry(4, text="Role: Chair")]
    assert _spans(_fold(entries, [_fold_el(3), _fold_el(4)])) == [(3, 3), (4, 4)]
    kept = stage2._drop_template_instructions(_fold(entries, [_fold_el(3), _fold_el(4)]))
    assert [e["text"] for e in kept] == ["Role: Chair"]


def test_fold_exact_only_template_instruction_is_also_never_folded():
    # 36 chars: an exact template match, below the near-match length floor.
    parent = "Role (i.e., member, secretary, etc.)"
    assert stage2.is_template_instruction(parent) and not stage2.is_near_template_instruction(parent)
    entries = [_fold_entry(3, text=parent), _fold_entry(4, text="Role: Chair")]
    assert _spans(_fold(entries, [_fold_el(3), _fold_el(4)])) == [(3, 3), (4, 4)]


def test_fold_mentor_label_is_not_a_trigger():
    # No corpus CV has a "Mentor:" detail line, so it is not in DETAIL_LINE_LABELS.
    entries = [_fold_entry(3, text="2016 Ana Cruz"), _fold_entry(4, text="Mentor: Lee Park")]
    assert _spans(_fold(entries, [_fold_el(3), _fold_el(4)])) == [(3, 3), (4, 4)]


def test_fold_entry_with_a_string_end_index_never_raises_or_folds():
    parent = _fold_entry(3, text="2016 Ana Cruz")
    parent["element_idx_end"] = "3.1"
    entries = [parent, _fold_entry(4, text="Project: tides")]
    assert _spans(_fold(entries, [_fold_el(3), _fold_el(4)])) == [(3, "3.1"), (4, 4)]


def test_fold_second_label_line_is_judged_against_the_folded_parent_not_the_label_line():
    # "Project: tides:" ends in a colon, but the entry the next line joins is the
    # folded parent, whose first line is "2016 Ana Cruz".
    entries = [_fold_entry(3, text="2016 Ana Cruz"), _fold_entry(4, text="Project: tides:"),
               _fold_entry(5, text="Role: Chair")]
    assert _spans(_fold(entries, [_fold_el(i) for i in (3, 4, 5)])) == [(3, 5)]


def test_fold_returns_the_same_list_object_when_nothing_folds():
    entries = [_fold_entry(4, text="Kim Lee"), _fold_entry(3, text="2016 Ana Cruz")]
    assert _fold(entries, [_fold_el(3), _fold_el(4)]) is entries


# =============================================================== join_row_cells (#488)

def test_join_row_cells_attaches_trailing_columns_to_first_paragraph():
    assert stage2.join_row_cells(["Title\n- a\n- b", "2024", "Org"]) == "Title | 2024 | Org\n- a\n- b"


def test_join_row_cells_single_paragraph_and_single_cell_rows_are_unchanged():
    assert stage2.join_row_cells(["  Title  ", "2024"]) == "Title   | 2024"
    assert stage2.join_row_cells(["Title\n- a"]) == "Title\n- a"
    assert stage2.join_row_cells([]) == ""


def test_join_row_cells_ignores_leading_and_trailing_blank_lines_in_cell_zero():
    assert stage2.join_row_cells(["\nTitle\n- a\n", "2024"]) == "Title | 2024\n- a"
    assert stage2.join_row_cells(["Title\n\n", "2024"]) == "Title\n\n | 2024"


def test_join_row_cells_keeps_old_shape_when_another_cell_spans_lines():
    # The trailing paragraphs could not be told from the org cell's own lines.
    assert stage2.join_row_cells(["Title\n- a", "Org\nCity", "2024"]) == "Title\n- a | Org\nCity | 2024"
