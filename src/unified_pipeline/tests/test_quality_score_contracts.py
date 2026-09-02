"""Regression guard for the PR #724 review round: contract tests for
quality_score.py's ambiguous-artifact guard, pipeline-error accounting, gate
mode validation, the widened fatal-error pattern, metadata-invariant
validation, the narrowed _load_first exception handling, linear_interp's
clamp, and the boundary / edge-case sweep the review asked for (T3 items
1-5).

    python3 -m pytest src/unified_pipeline/tests/test_quality_score_contracts.py -p no:cacheprovider

Self-contained: no DB, no network, no PII. All artifacts are synthetic tmp
files and python-docx-built fixtures.
"""

import json
import logging
import sys
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline import quality_score as qs  # noqa: E402
from unified_pipeline.quality_score import (  # noqa: E402
    FATAL_ERROR_PATTERN,
    VALID_GATE_MODES,
    _load_docx,
    _load_first,
    band_for,
    linear_interp,
    quality_gate,
    score_broken_format,
    score_cv_owner,
    score_duplicate_ratio,
    score_field_sparseness,
    score_pipeline_errors,
    score_run,
    score_sparse_tables,
    score_t_bucket,
)

docx = pytest.importorskip("docx")
from docx import Document  # noqa: E402


def _make_docx(paragraph_texts=(), tables=()):
    """tables: list of list-of-rows-of-cell-text, e.g. [[["a", "b"], ["", ""]]]."""
    doc = Document()
    for text in paragraph_texts:
        doc.add_paragraph(text)
    for rows in tables:
        n_rows = len(rows)
        n_cols = len(rows[0]) if rows else 0
        tbl = doc.add_table(rows=n_rows, cols=n_cols)
        for r, row_texts in enumerate(rows):
            for c, cell_text in enumerate(row_texts):
                tbl.cell(r, c).text = cell_text
    return doc


def _truncate(dir_path: Path, name: str) -> Path:
    p = dir_path / name
    p.write_text('{"unterminated": ')
    return p


def _classified(total_entries=None, duplicate_entries=None, code_distribution=None,
                stats=None):
    meta = {}
    if total_entries is not None:
        meta["total_entries"] = total_entries
    if duplicate_entries is not None:
        meta["duplicate_entries"] = duplicate_entries
    if code_distribution is not None:
        meta["code_distribution"] = code_distribution
    if stats is not None:
        meta["stats"] = stats
    return {"meta": meta}


def _write_json(dir_path: Path, name: str, obj) -> Path:
    p = dir_path / name
    p.write_text(json.dumps(obj))
    return p


# --------------------------------------------------------------------- D1
# _load_first: ambiguous multiple-file match (#724 review T1)
# --------------------------------------------------------------------- D1


def test_load_first_multiple_matches_not_loaded(tmp_path, caplog):
    (tmp_path / "ABC_fields.json").write_text('{"a": 1}')
    (tmp_path / "XYZ_fields.json").write_text('{"a": 2}')
    with caplog.at_level(logging.WARNING, logger="unified_pipeline.quality_score"):
        data, reason = _load_first(tmp_path, "*_fields.json")

    assert data is None
    assert reason.startswith("ambiguous: 2 files match"), reason
    assert "ABC_fields.json" in reason and "XYZ_fields.json" in reason, reason
    assert any("ABC_fields.json" in r.message and "XYZ_fields.json" in r.message
              for r in caplog.records)


def test_score_cv_owner_ambiguous_fields_json_hard_fails(tmp_path):
    (tmp_path / "ABC_fields.json").write_text('{"a": 1}')
    (tmp_path / "XYZ_fields.json").write_text('{"a": 2}')
    fraction, detail, cap = score_cv_owner(tmp_path)
    assert fraction == 1.0
    assert cap == 25
    assert "unreadable" in detail and "ambiguous" in detail, detail


# --------------------------------------------------------------------- D2
# score_pipeline_errors: unreadable JSON counts as a signal (#724 review T2.1)
# --------------------------------------------------------------------- D2


def test_pipeline_errors_unreadable_json_counts_and_is_named(tmp_path):
    _truncate(tmp_path, "ABC_classified.json")
    fraction, detail, cap = score_pipeline_errors(tmp_path)
    assert "nonnull_error_fields=1" in detail, detail
    assert "ABC_classified.json" in detail, detail
    assert cap is None
    assert fraction == pytest.approx(1 / 3)


def test_pipeline_errors_no_json_files(tmp_path):
    fraction, detail, cap = score_pipeline_errors(tmp_path)
    assert fraction == 0.0
    assert "nonnull_error_fields=0" in detail
    assert cap is None


# --------------------------------------------------------------------- D3
# quality_gate: mode validation (#724 review T2.3)
# --------------------------------------------------------------------- D3


def test_quality_gate_rejects_invalid_mode(tmp_path):
    with pytest.raises(ValueError, match="blok"):
        quality_gate(tmp_path, mode="blok")


@pytest.mark.parametrize("mode,expected_gate_passed", [
    ("off", True),
    ("advisory", True),
    ("block", False),
])
def test_quality_gate_accepts_every_valid_mode(tmp_path, mode, expected_gate_passed):
    """F4: an empty output directory scores 25 (RED, below BAND_YELLOW), so
    it fails the block threshold -- asserts gate_passed per mode, not just
    that the mode itself doesn't raise."""
    result = quality_gate(tmp_path, mode=mode)
    assert result["gate_mode"] == mode
    assert result["gate_passed"] == expected_gate_passed, result


def test_band_for_boundaries():
    assert band_for(85) == "GREEN (ship)"
    assert band_for(60) == "YELLOW (human cleanup needed)"
    assert band_for(59) == "RED (re-run / do-not-deliver)"


# --------------------------------------------------------------------- D5
# FATAL_ERROR_PATTERN widened for ValidationException etc (#724 review T2.5),
# then made case-sensitive on the exception-name branch plus an explicit
# API-envelope branch (follow-up review, D5'): the original case-insensitive
# `\b\w+(?:Error|Exception)\b` matched ordinary prose ending in "...error"
# case-insensitively, including the OpenAI API's own
# `'type': 'invalid_request_error'` envelope text on farm uid L7IAKW -- a
# real stage failure, but caught by accident of the broad heuristic rather
# than by an actual exception type name.
# --------------------------------------------------------------------- D5


@pytest.mark.parametrize("text", [
    "ValidationException: field required",
    "TypeError: unsupported operand",
    "AttributeError: 'NoneType' object has no attribute 'x'",
    "KeyError: 'x'",
    # Pre-existing branches must keep matching.
    "name 'response' is not defined",
    "Traceback (most recent call last):",
    "NameError: name 'x' is not defined",
])
def test_fatal_error_pattern_matches_exception_type_names(text):
    assert FATAL_ERROR_PATTERN.search(text), text


@pytest.mark.parametrize("text", [
    "the war on terror",
    "terror",
    "keyerror",  # lowercase -- not a capitalized exception type name
])
def test_fatal_error_pattern_case_sensitive_exception_branch_ignores_prose(text):
    """D5': the exception-name branch is case-sensitive so lowercase prose
    ending in "...error"/"...exception" (or containing "error"/"terror" as
    a substring) is not mistaken for an exception type name."""
    assert not FATAL_ERROR_PATTERN.search(text), text


@pytest.mark.parametrize("text", [
    "'type': 'invalid_request_error'",
    "Error code: 400",
])
def test_fatal_error_pattern_matches_api_envelope(text):
    """D5': explicit, still case-insensitive branch for the OpenAI API
    envelope shape that farm uid L7IAKW actually hit -- kept fatal by name,
    not by accident of the broad exception-name heuristic."""
    assert FATAL_ERROR_PATTERN.search(text), text


def test_fatal_error_pattern_does_not_match_benign_text():
    assert not FATAL_ERROR_PATTERN.search("optional field unavailable")


def test_score_pipeline_errors_validation_exception_is_fatal(tmp_path):
    _write_json(tmp_path, "ABC_classified.json",
               {"meta": {"stats": {"t_validation": {
                   "error": "ValidationException: entries malformed"}}}})
    fraction, detail, cap = score_pipeline_errors(tmp_path)
    assert fraction == 1.0
    assert cap == 40
    assert "fatal_pattern=YES" in detail


def test_score_pipeline_errors_api_envelope_error_is_fatal(tmp_path):
    """D5': the farm's actual L7IAKW shape -- an OpenAI 400 recorded as
    meta.stats.t_validation.error -- stays fatal via the explicit
    API-envelope branch, not the case-sensitive exception-name branch."""
    _write_json(tmp_path, "ABC_classified.json",
               {"meta": {"stats": {"t_validation": {
                   "error": "{'error': {'type': 'invalid_request_error'}}"}}}})
    fraction, detail, cap = score_pipeline_errors(tmp_path)
    assert fraction == 1.0
    assert cap == 40
    assert "fatal_pattern=YES" in detail


# --------------------------------------------------------------------- D6
# score_sparse_tables: zero tables is worst-case, not perfect (#724 T2.6)
# --------------------------------------------------------------------- D6


def test_sparse_tables_zero_tables_is_worst_case(tmp_path):
    _make_docx(["some prose, no tables at all"]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_sparse_tables(tmp_path)
    assert fraction == 1.0
    assert detail == "no tables in docx (template always renders tables)"
    assert cap is None


def test_sparse_tables_nonzero_tables_still_scored_normally(tmp_path):
    """Regression: a docx WITH tables must not be affected by the D6 change."""
    _make_docx(tables=[[["Alice", "PI"], ["Bob", "Co-I"]]]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_sparse_tables(tmp_path)
    assert fraction == 0.0
    assert "total_tables=1" in detail


# --------------------------------------------------------------------- D10
# metadata invariant validation (#724 review T2.10, T2.11 / T3.2)
# --------------------------------------------------------------------- D10


def test_duplicate_ratio_negative_total_entries(tmp_path):
    _write_json(tmp_path, "X_classified.json", _classified(total_entries=-1, duplicate_entries=0))
    fraction, detail, cap = score_duplicate_ratio(tmp_path)
    assert fraction == 1.0
    assert cap is None
    assert "invalid metadata: total_entries < 0" in detail, detail


def test_duplicate_ratio_negative_duplicate_entries(tmp_path):
    _write_json(tmp_path, "X_classified.json", _classified(total_entries=10, duplicate_entries=-5))
    fraction, detail, cap = score_duplicate_ratio(tmp_path)
    assert fraction == 1.0
    assert "invalid metadata: duplicate_entries < 0" in detail, detail


def test_duplicate_ratio_duplicate_exceeds_total(tmp_path):
    _write_json(tmp_path, "X_classified.json", _classified(total_entries=10, duplicate_entries=15))
    fraction, detail, cap = score_duplicate_ratio(tmp_path)
    assert fraction == 1.0
    assert "invalid metadata: duplicate_entries > total_entries" in detail, detail


def test_t_bucket_negative_total_entries(tmp_path):
    _write_json(tmp_path, "X_classified.json",
               _classified(total_entries=-1, code_distribution={"A": 1}))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert fraction == 1.0
    assert cap is None
    assert "invalid metadata: total_entries < 0" in detail, detail


def test_t_bucket_code_distribution_disagrees_with_total_entries(tmp_path):
    _write_json(tmp_path, "X_classified.json",
               _classified(total_entries=100, code_distribution={"A": 3, "T": 1}))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert fraction == 1.0
    assert "invalid metadata: sum(code_distribution) != total_entries" in detail, detail


def test_t_bucket_total_entries_absent_no_mismatch_flagged(tmp_path):
    """When total_entries is absent, there is nothing to disagree with --
    code_distribution alone still drives the ratio as before."""
    _write_json(tmp_path, "X_classified.json",
               _classified(code_distribution={"A": 97, "T": 3}))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "invalid metadata" not in detail, detail
    assert fraction == 0.0


# --------------------------------------------------------------------- D11
# _load_first: unexpected exception types are NOT swallowed (#724 T2.12)
# --------------------------------------------------------------------- D11


def test_load_first_propagates_unexpected_exception_type(tmp_path, monkeypatch):
    (tmp_path / "X_fields.json").write_text("{}")

    def boom(_f):
        raise RuntimeError("unexpected programming error")

    monkeypatch.setattr(qs.json, "load", boom)
    with pytest.raises(RuntimeError, match="unexpected programming error"):
        _load_first(tmp_path, "*_fields.json")


# --------------------------------------------------------------------- D12
# linear_interp clamps its output (#724 review T2.13)
# --------------------------------------------------------------------- D12


def test_linear_interp_within_range_unchanged():
    assert linear_interp(0.5, 0.0, 1.0, 0.0, 10.0) == pytest.approx(5.0)


def test_linear_interp_clamps_below_range():
    assert linear_interp(-5.0, 0.0, 1.0, 0.0, 10.0) == 0.0


def test_linear_interp_clamps_above_range():
    assert linear_interp(5.0, 0.0, 1.0, 0.0, 10.0) == 10.0


def test_linear_interp_clamps_reversed_output_range():
    """out_lo may be greater than out_hi (a descending map); the clamp bound
    must still be [min, max] of the two, not [out_lo, out_hi] literally."""
    assert linear_interp(-5.0, 0.0, 1.0, 10.0, 0.0) == 10.0
    assert linear_interp(5.0, 0.0, 1.0, 10.0, 0.0) == 0.0


# --------------------------------------------------------------------- D13
# T-bucket and duplicate-ratio breakpoint boundaries (#724 review T3.1)
# --------------------------------------------------------------------- D13


@pytest.mark.parametrize("t_count,total,expected", [
    (3, 100, 0.0),                                     # 0.03 -> 0
    (4, 100, 0.08),                                     # just above 0.03
    (8, 100, 0.4),                                      # 0.08 -> 0.4
    (9, 100, 0.4 + (1 / 7) * 0.4),                      # just above 0.08
    (15, 100, 0.8),                                     # 0.15 -> 0.8
    (16, 100, 1.0),                                     # just above 0.15 -> 1
])
def test_t_bucket_breakpoints(tmp_path, t_count, total, expected):
    code_distribution = {"A": total - t_count, "T": t_count}
    _write_json(tmp_path, "X_classified.json", _classified(code_distribution=code_distribution))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert fraction == pytest.approx(expected, abs=1e-9), detail


@pytest.mark.parametrize("dup,total,expected", [
    (10, 100, 0.0),                                     # 0.10 -> 0
    (11, 100, 0.02),                                     # just above 0.10
    (30, 100, 0.4),                                      # 0.30 -> 0.4
    (31, 100, 0.42),                                     # just above 0.30
    (50, 100, 0.8),                                      # 0.50 -> 0.8
    (51, 100, 1.0),                                      # just above 0.50 -> 1
])
def test_duplicate_ratio_breakpoints(tmp_path, dup, total, expected):
    _write_json(tmp_path, "X_classified.json",
               _classified(total_entries=total, duplicate_entries=dup))
    fraction, detail, cap = score_duplicate_ratio(tmp_path)
    assert fraction == pytest.approx(expected, abs=1e-9), detail


# D14 (T3.2): duplicate_entries > total_entries, duplicate_entries < 0,
# total_entries < 0, and total_entries != sum(code_distribution.values())
# are covered by the D10 tests above (test_duplicate_ratio_negative_*,
# test_duplicate_ratio_duplicate_exceeds_total,
# test_t_bucket_negative_total_entries,
# test_t_bucket_code_distribution_disagrees_with_total_entries).


# --------------------------------------------------------------------- D15
# score_broken_format: real DOCX fixtures (#724 T2.7, T3.3; D7' follow-up)
#
# D7' (follow-up review, 2026-09-02): the raw-tab check now scans table
# cells too (nested one level), confirmed against the pristine WCM template
# to add no false positives (only one incidental, unreachable template tab).
# The prompt-echo check stays paragraph-only by deliberate design: every
# INSTRUCTION_MARKERS hit checked against the pristine template's own table
# cells is the template's own label/instruction text (see module docstring
# for the per-marker template locations), so scanning cells for echoes would
# false-positive on all 66 farm docx, not catch a real defect.
# --------------------------------------------------------------------- D15


def test_broken_format_raw_tab_in_paragraph(tmp_path):
    _make_docx(["a paragraph with a\traw tab"]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "raw_tab_paragraphs=1" in detail


def test_broken_format_raw_tab_in_table_cell_detected(tmp_path):
    """D7': the cell walk now catches a raw tab inside a table cell."""
    _make_docx(tables=[[["has\ta tab", "clean"]]]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "raw_tab_cells=1" in detail, detail


def test_broken_format_raw_tab_gridspan_merged_cell_counted_once(tmp_path):
    """F3 (#724 second follow-up review): row.cells repeats a gridSpan-merged
    cell once per spanned column, so counting every row.cells entry would
    count a single merged cell's raw tab 3 times for a 3-column merge.
    Dedupe by the underlying w:tc element identity so it counts once."""
    doc = _make_docx(tables=[[["a\tb", "mid", "end"], ["x", "y", "z"]]])
    table = doc.tables[0]
    table.rows[0].cells[0].merge(table.rows[0].cells[2])
    doc.save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "raw_tab_cells=1" in detail, detail


def test_broken_format_prompt_echo_in_paragraph(tmp_path):
    _make_docx(["Please list here your publications"]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "echo_paragraphs=1" in detail, detail


def test_broken_format_prompt_echo_in_table_cell_not_detected(tmp_path):
    """Deliberate exclusion, not a blind spot: the echo check stays
    paragraph-only because these markers are the template's own cell text
    (module docstring cites the exact template locations)."""
    _make_docx(tables=[[["please choose one", "clean"]]]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "echo_paragraphs=0" in detail, detail


def test_broken_format_legitimate_please_sentence_still_matches(tmp_path):
    """D8: no pattern change -- pins the current (intentionally broad) match
    rather than silently narrowing it. Farm sample (T2.8): 19 of 20 sampled
    'please' hits were true prompt-echo positives; the one false positive was
    'bedside' (a citation title), not 'please'. This sentence is legitimate
    academic prose and still matches -- expected, not a bug to fix here."""
    _make_docx(["Please note the patient responded well to treatment."]).save(
        tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "echo_paragraphs=1" in detail, detail


# --------------------------------------------------------------------- D16
# _load_docx: absent / corrupt / ambiguous (#724 review T3.4)
# --------------------------------------------------------------------- D16


def test_load_docx_no_docx(tmp_path):
    doc, reason = _load_docx(tmp_path)
    assert doc is None
    assert reason == "no docx found"


def test_load_docx_corrupt_docx(tmp_path):
    (tmp_path / "broken.docx").write_bytes(b"not a real docx, just garbage bytes")
    doc, reason = _load_docx(tmp_path)
    assert doc is None
    assert reason.startswith("docx open error:"), reason


def test_load_docx_multiple_docx_ambiguous(tmp_path):
    _make_docx(["hello"]).save(tmp_path / "A.docx")
    _make_docx(["world"]).save(tmp_path / "B.docx")
    doc, reason = _load_docx(tmp_path)
    assert doc is None
    assert reason.startswith("ambiguous: 2 files match *.docx"), reason
    assert "A.docx" in reason and "B.docx" in reason


# --------------------------------------------------------------------- D17
# T3.5 edge-case sweep
# --------------------------------------------------------------------- D17


def test_edge_empty_output_directory(tmp_path):
    """An existing but completely empty output directory: every dimension
    falls to its own 'absent' case; the cv_owner hard-fail cap (25) wins."""
    result = score_run(tmp_path)
    assert result["totalScore"] == 25
    assert result["band"].startswith("RED")
    assert result["hard_fail_caps_applied"] == [25]


def test_edge_nonexistent_directory_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        score_run(tmp_path / "does_not_exist_at_all")


def test_edge_run_id_none_derives_from_first_json(tmp_path):
    _write_json(tmp_path, "XYZ123_fields.json", {"cv_owner": {}})
    result = score_run(tmp_path, run_id=None)
    assert result["run_id"] == "XYZ123"


def test_edge_run_id_none_unknown_when_no_json_files(tmp_path):
    result = score_run(tmp_path, run_id=None)
    assert result["run_id"] == "UNKNOWN"


def test_edge_malformed_json_mixed_with_valid_json(tmp_path):
    _write_json(tmp_path, "AAA_entries.json", {"nested": {"error": "stage timeout"}})
    _truncate(tmp_path, "BBB_classified.json")
    fraction, detail, cap = score_pipeline_errors(tmp_path)
    assert "nonnull_error_fields=2" in detail, detail
    assert "BBB_classified.json" in detail, detail
    assert cap is None
    assert fraction == pytest.approx(2 / 3), detail


def test_edge_multiple_json_files_for_same_pattern(tmp_path):
    (tmp_path / "AAA_classified.json").write_text('{"meta": {}}')
    (tmp_path / "BBB_classified.json").write_text('{"meta": {}}')
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert fraction == 1.0
    assert "unreadable" in detail and "ambiguous" in detail


def test_edge_empty_entries_list(tmp_path):
    _write_json(tmp_path, "X_fields.json", {"entries": []})
    fraction, detail, cap = score_field_sparseness(tmp_path)
    assert fraction == 1.0
    assert detail == "no entries"


def test_edge_empty_code_distribution_no_total_entries(tmp_path):
    _write_json(tmp_path, "X_classified.json", _classified(code_distribution={}))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "invalid metadata" not in detail
    assert fraction == 0.0
    assert "T_count=0; total=1" in detail


def test_edge_zero_tables(tmp_path):
    _make_docx(["prose only"]).save(tmp_path / "out.docx")
    fraction, reason, _ = score_sparse_tables(tmp_path)
    assert fraction == 1.0
    assert reason == "no tables in docx (template always renders tables)", reason


def test_edge_all_empty_tables(tmp_path):
    _make_docx(tables=[[["", ""], ["", ""]], [["", ""], ["", ""]]]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_sparse_tables(tmp_path)
    assert fraction == 1.0
    assert "sparse_tables=2" in detail


def test_edge_completely_populated_tables(tmp_path):
    _make_docx(tables=[[["Alice", "PI"], ["Bob", "Co-I"]]]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_sparse_tables(tmp_path)
    assert fraction == 0.0
    assert "empty_cells=0/4" in detail


def test_edge_100_percent_duplicate_entries(tmp_path):
    _write_json(tmp_path, "X_classified.json", _classified(total_entries=10, duplicate_entries=10))
    fraction, reason, _ = score_duplicate_ratio(tmp_path)
    assert fraction == 1.0
    assert reason == (
        "total_entries=10; duplicate_entries=10; dup_ratio=1.000; "
        "entries_coverage_pct=None; fraction=1.000"
    ), reason


def test_edge_0_percent_duplicate_entries(tmp_path):
    _write_json(tmp_path, "X_classified.json", _classified(total_entries=10, duplicate_entries=0))
    fraction, reason, _ = score_duplicate_ratio(tmp_path)
    assert fraction == 0.0
    assert reason == (
        "total_entries=10; duplicate_entries=0; dup_ratio=0.000; "
        "entries_coverage_pct=None; fraction=0.000"
    ), reason


def test_edge_missing_cv_owner_key(tmp_path):
    _write_json(tmp_path, "X_fields.json", {"entries": []})
    fraction, detail, cap = score_cv_owner(tmp_path)
    assert fraction == 1.0
    assert cap == 25
    assert detail == "cv_owner name empty; hard-fail cap=25"


def test_edge_cv_owner_first_name_only(tmp_path):
    """CONCERN (F4/D17): first_name alone still hard-fails (cap=25) even
    though a real first name was extracted -- pinned as current behaviour,
    arguable, not changed here (out of the D1-D12/D5'/D7' write set)."""
    _write_json(tmp_path, "X_fields.json", {"cv_owner": {"first_name": "Jane"}})
    fraction, detail, cap = score_cv_owner(tmp_path)
    assert cap == 25
    assert fraction == 1.0
    assert detail == "cv_owner name empty; hard-fail cap=25", detail


def test_edge_cv_owner_last_name_only(tmp_path):
    _write_json(tmp_path, "X_fields.json", {"cv_owner": {"last_name": "Public"}})
    fraction, detail, cap = score_cv_owner(tmp_path)
    assert cap == 25
    assert fraction == 1.0
    assert detail == "cv_owner name empty; hard-fail cap=25", detail


def test_edge_cv_owner_full_name_only_escapes_hard_fail(tmp_path):
    _write_json(tmp_path, "X_fields.json", {"cv_owner": {"full_name": "Jane Q. Public"}})
    fraction, detail, cap = score_cv_owner(tmp_path)
    assert cap is None, detail
    assert "full_name='Jane Q. Public'" in detail
    assert fraction == 1.0, detail


def test_edge_cv_owner_whitespace_only_names(tmp_path):
    _write_json(tmp_path, "X_fields.json",
               {"cv_owner": {"full_name": "   ", "first_name": " ", "last_name": "\t"}})
    fraction, detail, cap = score_cv_owner(tmp_path)
    assert cap == 25
    assert fraction == 1.0
    assert detail == "cv_owner name empty; hard-fail cap=25", detail
