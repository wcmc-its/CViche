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
import zipfile
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline import quality_score as qs  # noqa: E402
from unified_pipeline.quality_score import (  # noqa: E402
    FATAL_ERROR_PATTERN,
    SCORED_ARTIFACT_COUNT,
    VALID_GATE_MODES,
    _goal_claimed_row_ids,
    _is_placeholder_only_row,
    _load_docx,
    _load_first,
    band_for,
    linear_interp,
    missing_evidence,
    no_output_produced,
    quality_gate,
    score_broken_format,
    score_cv_owner,
    score_duplicate_ratio,
    score_field_sparseness,
    score_no_output,
    score_pipeline_errors,
    score_run,
    score_sparse_tables,
    score_stage3b_fallback_ratio,
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


def _classified_with_entries(entries: list[dict], total_entries: int | None = None) -> dict:
    """A classified.json shaped the way stage_3b_entry_classifier.py actually
    writes one (#822 finding 2): meta.code_distribution is DERIVED from the
    entries' own taxonomy_code, the same as the real writer's `code_counts`
    loop, so a test fixture can't silently disagree with itself the way a
    hand-typed code_distribution could."""
    code_dist: dict[str, int] = {}
    for e in entries:
        code = e.get("taxonomy_code", "?")
        code_dist[code] = code_dist.get(code, 0) + 1
    return {
        "meta": {
            "total_entries": total_entries if total_entries is not None else len(entries),
            "code_distribution": code_dist,
        },
        "entries": entries,
    }


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


# --------------------------------------------------------------------- #745
# score_pipeline_errors: structured stage-error records written by the drivers
# --------------------------------------------------------------------- #745


def test_structured_stage_error_without_exception_name_is_fatal(tmp_path):
    """#745 acceptance: a stage failure whose message names no exception type
    -- the farm's ODAWYA shape, which FATAL_ERROR_PATTERN cannot see -- is
    still fatal when a driver recorded it."""
    from unified_pipeline.stage_errors import StageError, record_stage_outcome
    message = "'int' object is not iterable"
    assert not FATAL_ERROR_PATTERN.search(message), "fixture must be regex-invisible"
    record_stage_outcome(tmp_path / "ABC_stage_errors.json", "3b",
                         StageError("3b", "TypeError", message, fatal=True))
    fraction, detail, cap = score_pipeline_errors(tmp_path)
    assert cap == 40
    assert fraction == 1.0
    assert "fatal_pattern=YES" in detail
    assert "stage 3b" in detail, detail


def test_structured_non_fatal_stage_error_counts_but_does_not_cap(tmp_path):
    from unified_pipeline.stage_errors import StageError, record_stage_outcome
    record_stage_outcome(tmp_path / "ABC_stage_errors.json", "5b",
                         StageError("5b", "KeyError", "degraded", fatal=False))
    fraction, detail, cap = score_pipeline_errors(tmp_path)
    assert cap is None
    assert "nonnull_error_fields=1" in detail
    assert fraction == pytest.approx(1 / 3)


def test_malformed_stage_error_record_is_named_not_read_as_clean(tmp_path):
    """Valid JSON of the wrong shape is reported as unreadable (a signal),
    never silently treated as 'no stage failed'."""
    _write_json(tmp_path, "ABC_stage_errors.json", [{"stage": "4"}])
    fraction, detail, cap = score_pipeline_errors(tmp_path)
    assert cap is None
    assert "ABC_stage_errors.json: unreadable" in detail, detail
    assert "nonnull_error_fields=1" in detail


def test_a_stage_error_file_that_is_not_json_is_counted_once(tmp_path):
    """The generic *.json loop already reports an unparseable file; the
    stage-error reader must not report it a second time."""
    (tmp_path / "ABC_stage_errors.json").write_text("{not json", encoding="utf-8")
    fraction, detail, cap = score_pipeline_errors(tmp_path)
    assert cap is None
    assert "nonnull_error_fields=1" in detail, detail
    assert detail.count("ABC_stage_errors.json") == 1, detail


def test_run_without_stage_error_record_falls_back_to_the_pattern(tmp_path):
    """A run that predates the record is scored exactly as before."""
    _write_json(tmp_path, "ABC_classified.json",
               {"meta": {"stats": {"t_validation": {"error": "'int' object is not iterable"}}}})
    fraction, detail, cap = score_pipeline_errors(tmp_path)
    assert cap is None
    assert "fatal_pattern=NO" in detail


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


def test_duplicate_ratio_stale_coverage_pct_over_130_no_longer_bumps(tmp_path):
    """#856 bounds coverage_percentage to <= 100; a pre-#856 artifact on disk
    can still carry a stale value over 130 (e.g. web207's stored 450.8), and
    #869 deletes the dead +0.1 bump that used to fire on it. dup=11/100 ->
    dup_ratio=0.11, strictly inside the (0.10, 0.30] interpolation band, so
    the interpolated fraction (0.02) must come through with no bump."""
    _write_json(tmp_path, "X_classified.json",
               _classified(total_entries=100, duplicate_entries=11))
    _write_json(tmp_path, "X_entries.json", {"coverage": {"coverage_percentage": 450.0}})
    fraction, detail, cap = score_duplicate_ratio(tmp_path)
    assert fraction == pytest.approx(0.02, abs=1e-9), detail
    assert "entries_coverage_pct=450.0" in detail, detail


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


def test_broken_format_template_tab_cell_not_counted(tmp_path):
    """#724 follow-up review: the pristine template's own 'Project title:'
    label cell contains a raw tab and IS reachable from rendered CV content
    (27 of 65 farm docx carry it as their only raw-tab cell), so it must be
    excluded by exact text match rather than counted as a defect."""
    _make_docx(tables=[[["Project title:\t\t", "clean"]]]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "raw_tab_cells=0" in detail, detail


def test_broken_format_non_template_tab_cell_still_counted(tmp_path):
    """A raw tab in a cell that is NOT the template's boilerplate text
    remains a genuine raw-formatting signal."""
    _make_docx(tables=[[["Some other\ttext", "clean"]]]).save(tmp_path / "out.docx")
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


@pytest.mark.parametrize("prose", [
    # D8 (#724 review thread 2 item 8): the bare words "please", "e.g.," and
    # "bedside" are ordinary academic prose; only the template's own
    # instruction phrases count as an echo now.
    "Please note the patient responded well to treatment.",
    "Emergency physician diagnosis of an atrial septal defect: the bedside "
    "bubble study. Academic Emergency Medicine. 2010 May.",
    "Research examines how built environments shape outcomes, e.g., obesity "
    "risk in rural counties.",
    "Clinical Teaching - Inpatient Rheumatology Teaching, including Bedside "
    "Teaching and Clinic Supervision",
])
def test_broken_format_legitimate_prose_not_counted_as_echo(tmp_path, prose):
    _make_docx([prose]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "echo_paragraphs=0" in detail, detail


@pytest.mark.parametrize("instruction", [
    # Marker hits that are NOT byte-identical to a template body paragraph
    # (a truncated/altered quote of one, or a marker phrase that only
    # occurs in the template's own table CELLS -- see the module docstring's
    # per-marker cell locations) -- still genuine signals after #822
    # finding 1, so still counted.
    "*Please annotate multi-investigator, program project, center grants (P50 etc.)",
    "Please list trainees and faculty that you have formally supervised",
    "Entries should follow standard journal format. Please also include PMCID: PMC",
    "If yes, please provide Visa type (Examples: J-1, H-1B, E-3, TN, etc.):",
    "Date (yyyy-yyyy)",
    "DEA number: (optional)",
    "Type of Supervision (research, clinical, teaching, leadership)",
])
def test_broken_format_non_template_instruction_line_counted_as_echo(tmp_path, instruction):
    _make_docx([instruction]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "echo_paragraphs=1" in detail, detail
    assert "template_echo_excluded=0" in detail, detail


# ------------------------------------------------------------------ #822 F1
# #822 finding 1: 20 of the 21 echo_paragraphs YTPMZK scored were
# byte-identical (after whitespace normalization) to one of the template's
# own 20 body instruction paragraphs -- stage 6 keeps them verbatim on
# purpose, so every run lost ~5.3 of 10 points on this dimension for the
# template's own text. A marker hit is now excluded when its
# whitespace-normalized text exactly equals a template body paragraph's.
# ------------------------------------------------------------------ #822 F1

@pytest.mark.parametrize("instruction", [
    # Verbatim body-paragraph instruction lines from the pristine WCM
    # template (one per alternation the narrowed pattern keeps that also
    # has an exact body-paragraph match, not only a cell-text match).
    "Current Employment Status (Please choose one, list here, delete the others):",
    "Part-time salaried by Cornell (show percentage of full time effort, e.g., 50%)",
    "Please include medical and scientific societies.)",
    "Clinical teaching (bedside teaching, teaching rounds, teaching in operating "
    "room, precepting in clinic, morning report, etc.)",
    "Duplicate table below as needed. For each funding vehicle, please include the following:",
    "Please summarize as for current projects: source-type, project title, dates, your role.",
])
def test_broken_format_verbatim_template_paragraph_excluded_from_echo(tmp_path, instruction):
    """A body paragraph identical to one of the template's own kept
    instruction paragraphs is not counted as a prompt echo (#822 finding
    1) -- it still matches INSTRUCTION_MARKERS (that's how the pattern was
    narrowed), it is just not counted as a defect."""
    _make_docx([instruction]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "echo_paragraphs=0" in detail, detail
    assert "template_echo_excluded=1" in detail, detail


def test_broken_format_verbatim_template_paragraph_whitespace_variant_still_excluded(tmp_path):
    """The match is on whitespace-NORMALIZED text: a stray leading space and
    a doubled internal space must not defeat the #822 finding 1 exclusion."""
    _make_docx([" Please include  medical and scientific societies.) "]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "echo_paragraphs=0" in detail, detail
    assert "template_echo_excluded=1" in detail, detail


def test_broken_format_altered_template_paragraph_still_counted_as_echo(tmp_path):
    """A near-miss -- a template instruction line with content appended --
    is NOT byte-identical to the template's own paragraph, so it is still a
    genuine echo signal, not excluded."""
    _make_docx(["Please include medical and scientific societies.) and honor societies"]
               ).save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "echo_paragraphs=1" in detail, detail
    assert "template_echo_excluded=0" in detail, detail


_TEMPLATE = Path(__file__).resolve().parents[3] / "key_files" / \
    "wcm_cv_template_faculty_october_2022_final.docx"


@pytest.mark.skipif(not _TEMPLATE.exists(), reason="pristine WCM template not checked out")
def test_broken_format_narrowed_pattern_still_matches_every_template_instruction():
    """The narrowed INSTRUCTION_MARKERS must keep matching every instruction
    paragraph in the template it is derived from: 20 body paragraphs matched
    the bare-word pattern before narrowing (scouted 2026-09-04), and the
    narrowed pattern matches the same 20 -- a drop here means a template
    instruction line can now echo into a rendered CV unseen."""
    doc = Document(_TEMPLATE)
    matched = [p.text for p in doc.paragraphs if qs.INSTRUCTION_MARKERS.search(p.text)]
    assert len(matched) == 20, matched
    assert all(p.strip() for p in matched)


@pytest.mark.skipif(not _TEMPLATE.exists(), reason="pristine WCM template not checked out")
def test_broken_format_every_matched_template_instruction_is_excluded_from_echo():
    """#822 finding 1's real-farm contract: every one of the template's own
    20 instruction paragraphs -- read from the actual checked-in template,
    not retyped -- is in the exclusion set `_template_body_paragraph_texts`
    builds, so none of them is ever counted as a prompt echo when stage 6
    keeps them verbatim in a rendered CV."""
    doc = Document(_TEMPLATE)
    matched = [p.text for p in doc.paragraphs if qs.INSTRUCTION_MARKERS.search(p.text)]
    excluded = qs._template_body_paragraph_texts()
    assert len(matched) == 20, matched
    for text in matched:
        assert qs._normalize_whitespace(text) in excluded, text


@pytest.mark.skipif(not _TEMPLATE.exists(), reason="pristine WCM template not checked out")
def test_template_body_paragraph_texts_cached_across_calls():
    """Built once, memoized at module level: two calls return the identical
    frozenset object, not two independently re-parsed copies."""
    first = qs._template_body_paragraph_texts()
    second = qs._template_body_paragraph_texts()
    assert first is second


def test_template_body_paragraph_texts_missing_file_raises(tmp_path, monkeypatch):
    """#822 finding 1's 'do not swallow' contract: the WCM template is a
    checked-in repo asset, not run output, so a missing file means a broken
    checkout and must raise -- never silently return an empty exclusion set,
    which would silently un-fix finding 1 and score every run as if the
    template had no instruction paragraphs of its own."""
    monkeypatch.setattr(qs, "_TEMPLATE_DOCX_PATH", tmp_path / "missing.docx")
    qs._template_body_paragraph_texts.cache_clear()
    try:
        with pytest.raises(FileNotFoundError):
            qs._template_body_paragraph_texts()
    finally:
        qs._template_body_paragraph_texts.cache_clear()


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
    falls to its own 'absent' case. Two hard-fail caps fire -- cv_owner (25,
    no fields.json) and #745's no_output (20, no docx at all) -- and the
    lower one wins: 'nothing was produced' is more severe than 'produced
    something with no name in it'."""
    result = score_run(tmp_path)
    assert result["totalScore"] == 20
    assert result["band"].startswith("RED")
    assert result["hard_fail_caps_applied"] == [25, 20]


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


# --------------------------------------------------------------------- D18
# score_run artifact health: data_complete / missing_evidence (#724 review
# thread 2 item 2). The score does not move; the result says what evidence
# it was computed without, distinguishing absent from unreadable from
# ambiguous per artifact.
# --------------------------------------------------------------------- D18


def _complete_run_dir(tmp_path: Path) -> Path:
    """Every artifact score_run reads, all loadable."""
    _write_json(tmp_path, "X_fields.json", {
        "cv_owner": {"full_name": "Jane Q. Public"},
        "cv_owner_location": {"inference_success": True, "primary_location": "NY"},
        "entries": [{"extracted_fields": {"email": "j@x.org"}, "extraction_success": True}],
    })
    _write_json(tmp_path, "X_classified.json",
                _classified(total_entries=4, duplicate_entries=0,
                            code_distribution={"A": 3, "T": 1}))
    _write_json(tmp_path, "X_entries.json", {"coverage": {"coverage_percentage": 100}})
    _make_docx(["clean"], tables=[[["a", "b"]]]).save(tmp_path / "X_wcm.docx")
    return tmp_path


def test_missing_evidence_empty_when_every_artifact_loads(tmp_path):
    assert missing_evidence(_complete_run_dir(tmp_path)) == []


def test_missing_evidence_names_every_absent_artifact_in_order(tmp_path):
    assert missing_evidence(tmp_path) == [
        "no fields.json found",
        "no classified.json found",
        "no entries.json found",
        "docx: no docx found",
    ]
    assert SCORED_ARTIFACT_COUNT == 4


def test_missing_evidence_distinguishes_unreadable_ambiguous_and_absent(tmp_path):
    _complete_run_dir(tmp_path)
    _truncate(tmp_path, "X_fields.json")                       # unreadable
    _write_json(tmp_path, "Y_classified.json", {"meta": {}})   # now ambiguous
    (tmp_path / "X_entries.json").unlink()                     # absent
    missing = missing_evidence(tmp_path)
    assert len(missing) == 3, missing
    assert missing[0].startswith("fields.json unreadable (JSONDecodeError"), missing[0]
    assert missing[1].startswith("classified.json unreadable (ambiguous: 2 files match"), missing[1]
    assert missing[2] == "no entries.json found"


def test_missing_evidence_names_corrupt_docx(tmp_path):
    _complete_run_dir(tmp_path)
    (tmp_path / "X_wcm.docx").write_bytes(b"garbage")
    missing = missing_evidence(tmp_path)
    assert len(missing) == 1, missing
    assert missing[0].startswith("docx: docx open error:"), missing[0]


def test_score_run_complete_evidence_is_flagged_complete(tmp_path):
    result = score_run(_complete_run_dir(tmp_path))
    assert result["data_complete"] is True
    assert result["missing_evidence"] == []
    assert not any(f.startswith("EVIDENCE INCOMPLETE") for f in result["flags"]), result["flags"]


def test_score_run_incomplete_evidence_is_flagged_and_score_unchanged(tmp_path):
    """The empty-directory score is still 20/RED (test_edge_empty_output_directory);
    what changes is that the result now says the 20 was computed with no
    evidence at all, as both a field and a flag after the hard-fail flags."""
    result = score_run(tmp_path)
    assert result["totalScore"] == 20 and result["band"].startswith("RED")
    assert result["data_complete"] is False
    assert result["missing_evidence"] == [
        "no fields.json found", "no classified.json found",
        "no entries.json found", "docx: no docx found",
    ]
    assert result["flags"][-1] == (
        "EVIDENCE INCOMPLETE (4 of 4 artifacts): no fields.json found; "
        "no classified.json found; no entries.json found; docx: no docx found")
    assert result["flags"][0].startswith("HARD-FAIL cap=25")


def test_score_run_one_missing_artifact_counts_one_of_four(tmp_path):
    _complete_run_dir(tmp_path)
    (tmp_path / "X_classified.json").unlink()
    result = score_run(tmp_path)
    assert result["data_complete"] is False
    assert result["missing_evidence"] == ["no classified.json found"]
    assert result["flags"] == [
        "No hard-fail caps triggered",
        "EVIDENCE INCOMPLETE (1 of 4 artifacts): no classified.json found",
    ]


def test_quality_gate_carries_data_complete_through(tmp_path):
    result = quality_gate(_complete_run_dir(tmp_path), mode="advisory")
    assert result["data_complete"] is True and result["missing_evidence"] == []


# --------------------------------------------------------------------- D19
# score_field_sparseness scoring curve around overlapping failures (#724
# review thread 2 item 9). The dimension is a deliberate double signal:
#   a = 0.5 * (allnull_or_zerocov / total) / 0.10     (the entries)
#   b = 0.5 * (1 - success_rate) / 0.10                (the extractor)
#   fraction = clamp(a + b)
# so an entry failing both weighs on both terms and the dimension saturates
# once 10% of entries fail both, or 20% fail one each.
# --------------------------------------------------------------------- D19


def _entry(success: bool, fields_present: bool, coverage_pct=None) -> dict:
    e = {
        "extraction_success": success,
        "extracted_fields": {"title": "x"} if fields_present else {"title": None},
    }
    if coverage_pct is not None:
        e["extraction_coverage"] = {"extraction_coverage_percent": coverage_pct}
    return e


_CLEAN = _entry(success=True, fields_present=True)
_ALLNULL_ONLY = _entry(success=True, fields_present=False)     # entries term only
_FAILED_ONLY = _entry(success=False, fields_present=True)      # extractor term only
_BOTH = _entry(success=False, fields_present=False)            # both terms


def _sparseness(tmp_path: Path, entries: list) -> tuple:
    _write_json(tmp_path, "X_fields.json", {"entries": entries})
    return score_field_sparseness(tmp_path)


@pytest.mark.parametrize("entries,expected,allnull,success_rate", [
    # id: clean run
    ([_CLEAN] * 10, 0.0, 0, "1.000"),
    # one entry with null fields but the extractor claimed success: a only
    ([_ALLNULL_ONLY] + [_CLEAN] * 9, 0.5, 1, "1.000"),
    # one entry the extractor failed on but fields are present: b only
    ([_FAILED_ONLY] + [_CLEAN] * 9, 0.5, 0, "0.900"),
    # one entry failing both, of 10: both terms fire, dimension saturates
    ([_BOTH] + [_CLEAN] * 9, 1.0, 1, "0.900"),
    # the same overlap diluted: 1 of 20 and 1 of 40
    ([_BOTH] + [_CLEAN] * 19, 0.5, 1, "0.950"),
    ([_BOTH] + [_CLEAN] * 39, 0.25, 1, "0.975"),
    # zero coverage counts on the entries term even with fields present
    ([_entry(True, True, coverage_pct=0)] + [_CLEAN] * 9, 0.5, 1, "1.000"),
    # 100% failure clamps at 1.0 (a = b = 5.0 before the clamp)
    ([_BOTH] * 10, 1.0, 10, "0.000"),
])
def test_field_sparseness_curve(tmp_path, entries, expected, allnull, success_rate):
    fraction, detail, cap = _sparseness(tmp_path, entries)
    assert fraction == pytest.approx(expected), detail
    assert f"allnull_or_zerocov={allnull}" in detail, detail
    assert f"success_rate={success_rate}" in detail, detail
    assert cap is None


def test_field_sparseness_one_entry_failing_both_weighs_as_two_single_failures(tmp_path):
    """The documented intent, pinned: one entry that is both all-null and
    extractor-failed scores exactly like two different entries each failing
    one way -- both are 0.5 + 0.5 = 1.0 out of 10 entries -- and twice a
    single-failure entry (0.5). This is the double signal item 9 asked to
    have established and tested; a change that de-duplicates the overlap
    (counting the both-failing entry once, 0.5) fails here."""
    overlap, _, _ = _sparseness(tmp_path, [_BOTH] + [_CLEAN] * 9)
    tmp2 = tmp_path / "two_singles"
    tmp2.mkdir()
    two_singles, _, _ = _sparseness(tmp2, [_ALLNULL_ONLY, _FAILED_ONLY] + [_CLEAN] * 8)
    tmp3 = tmp_path / "one_single"
    tmp3.mkdir()
    one_single, _, _ = _sparseness(tmp3, [_ALLNULL_ONLY] + [_CLEAN] * 9)
    assert overlap == pytest.approx(1.0)
    assert two_singles == pytest.approx(1.0)
    assert one_single == pytest.approx(0.5)
    assert overlap == pytest.approx(two_singles)
    assert overlap == pytest.approx(2 * one_single)


def test_field_sparseness_saturates_at_ten_percent_both_failing(tmp_path):
    """Beyond 1 both-failing entry in 10 the curve is flat at 1.0 -- two such
    entries score the same as one, so the dimension cannot rank a run with
    20% dual failures below one with 10%."""
    one, _, _ = _sparseness(tmp_path, [_BOTH] + [_CLEAN] * 9)
    tmp2 = tmp_path / "two"
    tmp2.mkdir()
    two, _, _ = _sparseness(tmp2, [_BOTH] * 2 + [_CLEAN] * 8)
    assert one == pytest.approx(1.0) and two == pytest.approx(1.0)


# --------------------------------------------------------------------- D20
# _load_docx catches only what python-docx raises for a present-but-unreadable
# file (the rule #724 review item 12 set for _load_first, applied to its
# sibling); anything else propagates.
# --------------------------------------------------------------------- D20


def _zip_without(src: Path, dst: Path, drop: str, replace: bytes | None = None) -> Path:
    """Copy docx zip `src` to `dst`, dropping member `drop` or, when `replace`
    is given, writing those bytes in its place."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
        for item in zin.infolist():
            if item.filename == drop:
                if replace is not None:
                    zout.writestr(item, replace)
                continue
            zout.writestr(item, zin.read(item.filename))
    return dst


def _garbage(tmp_path: Path) -> Path:
    p = tmp_path / "out.docx"
    p.write_bytes(b"not a real docx, just garbage bytes")
    return p


def _empty(tmp_path: Path) -> Path:
    p = tmp_path / "out.docx"
    p.write_bytes(b"")
    return p


def _truncated(tmp_path: Path) -> Path:
    real = tmp_path / "real.bin"
    _make_docx(["x"]).save(real)
    data = real.read_bytes()
    p = tmp_path / "out.docx"
    p.write_bytes(data[: len(data) // 2])
    return p


def _missing_document_xml(tmp_path: Path) -> Path:
    real = tmp_path / "real.bin"
    _make_docx(["x"]).save(real)
    return _zip_without(real, tmp_path / "out.docx", "word/document.xml")


def _corrupt_document_xml(tmp_path: Path) -> Path:
    real = tmp_path / "real.bin"
    _make_docx(["x"]).save(real)
    return _zip_without(real, tmp_path / "out.docx", "word/document.xml",
                        replace=b"<w:document><unclosed")


def _directory(tmp_path: Path) -> Path:
    p = tmp_path / "out.docx"
    p.mkdir()
    return p


@pytest.mark.parametrize("build,exc_name", [
    (_garbage, "BadZipFile"),
    (_empty, "BadZipFile"),
    (_truncated, "BadZipFile"),
    (_missing_document_xml, "KeyError"),
    (_corrupt_document_xml, "XMLSyntaxError"),
    (_directory, "IsADirectoryError"),
])
def test_load_docx_unreadable_docx_names_exception_and_warns(tmp_path, caplog, build, exc_name):
    build(tmp_path)
    with caplog.at_level(logging.WARNING, logger="unified_pipeline.quality_score"):
        doc, reason = _load_docx(tmp_path)
    assert doc is None
    assert reason.startswith(f"docx open error: {exc_name}:"), reason
    assert any("out.docx" in r.message for r in caplog.records), caplog.records


def test_load_docx_propagates_unexpected_exception_type(tmp_path, monkeypatch):
    """A RuntimeError from python-docx is a programming error, not an
    unreadable file, and is no longer swallowed into 'docx open error'."""
    _make_docx(["x"]).save(tmp_path / "out.docx")

    def _boom(*_args, **_kwargs):
        raise RuntimeError("bug in the loader")

    monkeypatch.setattr(docx, "Document", _boom)
    with pytest.raises(RuntimeError, match="bug in the loader"):
        _load_docx(tmp_path)


def test_score_dimensions_over_corrupt_docx_still_half_penalty(tmp_path):
    """The two docx dimensions keep their 0.5 'unavailable' fraction for a
    corrupt docx and now carry the exception name in the reason."""
    _garbage(tmp_path)
    for scorer in (score_sparse_tables, score_broken_format):
        fraction, reason, cap = scorer(tmp_path)
        assert fraction == 0.5 and cap is None
        assert reason.startswith("docx open error: BadZipFile:"), reason


# --------------------------------------------------------------------- D21
# #745 no_output / #810 stage3b_fallback_ratio: two new hard-fail
# dimensions, both weight 0 (pure gates -- see quality_score.py's own
# comment on why a nonzero weight would move every OTHER run's score too).
# --------------------------------------------------------------------- D21

def test_no_output_caps_at_20_with_no_docx_at_all(tmp_path):
    """web204 (#745): a run with real stage-4 output but no docx at all."""
    _write_json(tmp_path, "X_fields.json", {"cv_owner": {"full_name": "Jane Q. Public"}})
    fraction, detail, cap = score_no_output(tmp_path)
    assert fraction == 1.0
    assert cap == 20
    assert "no docx produced" in detail


def test_no_output_quiet_when_a_docx_exists(tmp_path):
    _make_docx(["hello"]).save(tmp_path / "out.docx")
    fraction, _detail, cap = score_no_output(tmp_path)
    assert fraction == 0.0 and cap is None


def test_no_output_is_not_the_same_failure_as_ambiguous_or_corrupt(tmp_path):
    """no_docx_produced is deliberately narrower than 'doc is None':
    'more than one docx' and 'a docx exists but won't parse' are different
    failures, already scored by score_sparse_tables/score_broken_format's
    own 0.5 neutral fraction, not this gate."""
    _garbage(tmp_path)  # a present-but-corrupt "docx"
    fraction, _detail, cap = score_no_output(tmp_path)
    assert fraction == 0.0 and cap is None

    _make_docx(["a"]).save(tmp_path / "a.docx")
    _make_docx(["b"]).save(tmp_path / "b.docx")
    fraction, _detail, cap = score_no_output(tmp_path)
    assert fraction == 0.0 and cap is None


# --------------------------------------------- round-2 N4: score_no_output and
# doctor.lints.runtime.lint_no_output must agree on "nothing to deliver" BY
# CONSTRUCTION -- both call quality_score.no_output_produced rather than each
# inlining their own version of the AND-of-absence condition.

def test_no_output_produced_is_the_and_of_absence():
    """positive: neither artifact -> True. negative: either artifact present
    -> False. The score's caller can't see the report at all (its outputs_dir
    never receives it), so it must pass has_report=False explicitly -- the
    default -- which degenerates the predicate to docx-absence alone for it,
    not a second definition of "missing"."""
    assert no_output_produced(has_docx=False, has_report=False) is True
    assert no_output_produced(has_docx=False) is True  # score's call shape
    assert no_output_produced(has_docx=True, has_report=False) is False
    assert no_output_produced(has_docx=False, has_report=True) is False
    assert no_output_produced(has_docx=True, has_report=True) is False


def test_score_no_output_and_lint_no_output_agree_via_the_shared_predicate(tmp_path):
    """Both hard-fail gates driven from the SAME has_docx value must reach
    the same verdict -- deleting either one's call to no_output_produced (in
    favour of its own inlined condition) is exactly what this catches if the
    two conditions are ever edited to drift apart."""
    from unified_pipeline.doctor.lints.runtime import lint_no_output

    # no docx at all: score hard-fails, and so does the doctor (report absent
    # too, since the score's caller has no report input at all).
    has_docx = not qs.no_docx_produced(tmp_path)
    assert has_docx is False
    fraction, _detail, cap = score_no_output(tmp_path)
    assert (fraction, cap) == (1.0, 20)
    assert lint_no_output(True, has_docx, False) != []

    # a docx exists: neither gate fires.
    _make_docx(["hello"]).save(tmp_path / "out.docx")
    has_docx = not qs.no_docx_produced(tmp_path)
    assert has_docx is True
    fraction, _detail, cap = score_no_output(tmp_path)
    assert (fraction, cap) == (0.0, None)
    assert lint_no_output(True, has_docx, False) == []


def test_stage3b_fallback_ratio_caps_at_40_on_the_web30_outage_numbers(tmp_path):
    _write_json(tmp_path, "X_classified.json", _classified(stats={
        "failed_batches": 41, "llm_batches": 83,
        "fallback_entries": 510, "entries_classified": 1019}))
    fraction, detail, cap = score_stage3b_fallback_ratio(tmp_path)
    assert fraction == 1.0
    assert cap == 40
    assert "hard-fail cap=40" in detail


def test_stage3b_fallback_ratio_quiet_on_the_clean_rerun_numbers(tmp_path):
    _write_json(tmp_path, "X_classified.json", _classified(stats={
        "failed_batches": 0, "llm_batches": 80,
        "fallback_entries": 0, "entries_classified": 1037}))
    fraction, _detail, cap = score_stage3b_fallback_ratio(tmp_path)
    assert fraction == 0.0 and cap is None


def test_stage3b_fallback_ratio_quiet_when_classified_json_is_absent(tmp_path):
    """No classified.json at all (an incomplete run) must not crash or
    false-positive -- distinct from score_t_bucket's 1.0-fraction convention
    for the same absence, because this dimension is a pure gate (weight 0)."""
    fraction, detail, cap = score_stage3b_fallback_ratio(tmp_path)
    assert fraction == 0.0 and cap is None
    assert "no classified.json found" in detail


def test_new_hard_fail_dimensions_do_not_move_a_clean_runs_score(tmp_path):
    """Adding weight-0 dimensions must not change TOTAL_WEIGHT's effect on a
    run that trips neither gate -- both scorers return fraction 0.0 with
    weight 0, so the composite is byte-identical to before these two
    dimensions existed."""
    root = _complete_run_dir(tmp_path)
    _make_docx(["hello"], tables=[[["a", "b"], ["c", "d"]]]).save(root / "out.docx")
    before = score_run(root)
    # Re-score after confirming neither new gate fired -- there is no
    # 'before this PR' run_doctor to diff against in-process, so this
    # instead pins that both new dimensions are present but silent.
    dims = {d["name"]: d for d in before["dimensionScores"]}
    assert dims["No rendered output produced at all (HARD-FAIL gate)"]["max"] == 0
    assert dims["Stage-3b batch-fallback ratio (HARD-FAIL gate)"]["max"] == 0
    assert before["hard_fail_caps_applied"] == []


# --------------------------------------------------------------------- D22
# #822 finding 2: score_t_bucket excludes correctly-diverted T entries
# (template scaffolding, placeholder rows, claimed grant-goal rows) from the
# catch-all-over-use numerator, without moving the denominator.
# --------------------------------------------------------------------- D22

# A real phrase from core/template_boilerplate_phrases.json's "instructions"
# set (>= 25 normalized chars, so it is a distinctive, exact-match instruction
# under is_template_instruction's own length floor) -- not a hand-typed
# phrase, so this test cannot silently drift from the actual boilerplate list.
_REAL_TEMPLATE_INSTRUCTION_TEXT = "Please include medical and scientific societies.)"

# The same real instruction, worded as an older template revision would (one
# word changed, matching is_near_template_instruction's own docstring
# example of "a comma or a word" off) -- ratio 0.973, above _NEAR_MATCH_MIN_RATIO
# (0.93), so is_template_instruction (exact-match only) must NOT match this,
# and is_near_template_instruction must.
_NEAR_TEMPLATE_INSTRUCTION_TEXT = (
    "Please do not delete or modify numbering or lettering of the various "
    "sections and subsections;"
)


def test_placeholder_only_row_helper():
    assert _is_placeholder_only_row("N/A | N/A") is True
    assert _is_placeholder_only_row("Not Applicable") is True
    assert _is_placeholder_only_row("none") is True
    assert _is_placeholder_only_row("N/A.") is True
    assert _is_placeholder_only_row("| |") is True          # bare pipe row
    assert _is_placeholder_only_row("") is False
    assert _is_placeholder_only_row(None) is False
    assert _is_placeholder_only_row("Teaching") is False     # real one-word entry
    assert _is_placeholder_only_row("N/A | Teaching") is False  # mixed: real content present


def test_goal_claimed_row_ids_matches_the_owning_grants_row_only():
    """A T row inside a grant's own element range, stating that grant's
    major goal, is claimed; a T row outside any grant's range, or one that
    is not goals-shaped at all, is not."""
    grant = {"taxonomy_code": "M2A", "text": "Some Grant",
              "element_idx_start": 10, "element_idx_end": 10}
    goal_row = {"taxonomy_code": "T",
                "text": "The major goals of this project are: to cure things",
                "parent_idx": 10, "element_idx_start": 11, "element_idx_end": 11}
    unrelated_row = {"taxonomy_code": "T", "text": "Some unrelated appendix line",
                      "parent_idx": 10, "element_idx_start": 12, "element_idx_end": 12}
    orphan_goal_row = {"taxonomy_code": "T",
                        "text": "The major goals of this project are: orphaned",
                        "parent_idx": 999, "element_idx_start": 13, "element_idx_end": 13}
    entries = [grant, goal_row, unrelated_row, orphan_goal_row]
    claimed = _goal_claimed_row_ids(entries)
    assert claimed == {id(goal_row)}


def test_t_bucket_excludes_template_instruction_text(tmp_path):
    entries = [
        {"taxonomy_code": "A", "text": "Real content",
         "element_idx_start": 1, "element_idx_end": 1},
        {"taxonomy_code": "T", "text": _REAL_TEMPLATE_INSTRUCTION_TEXT,
         "element_idx_start": 2, "element_idx_end": 2},
    ]
    _write_json(tmp_path, "X_classified.json", _classified_with_entries(entries))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "T_excluded_template=1" in detail, detail
    assert "T_count=0" in detail, detail
    assert "total=2" in detail, detail   # denominator unchanged by the exclusion
    assert fraction == 0.0
    assert cap is None


def test_t_bucket_excludes_near_template_instruction_text(tmp_path):
    entries = [
        {"taxonomy_code": "A", "text": "Real content",
         "element_idx_start": 1, "element_idx_end": 1},
        {"taxonomy_code": "T", "text": _NEAR_TEMPLATE_INSTRUCTION_TEXT,
         "element_idx_start": 2, "element_idx_end": 2},
    ]
    _write_json(tmp_path, "X_classified.json", _classified_with_entries(entries))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "T_excluded_template=1" in detail, detail
    assert "T_count=0" in detail, detail


def test_t_bucket_excludes_placeholder_only_rows(tmp_path):
    entries = [
        {"taxonomy_code": "A", "text": "Real content",
         "element_idx_start": 1, "element_idx_end": 1},
        {"taxonomy_code": "T", "text": "N/A | N/A",
         "element_idx_start": 2, "element_idx_end": 2},
        {"taxonomy_code": "T", "text": "| |",
         "element_idx_start": 3, "element_idx_end": 3},
    ]
    _write_json(tmp_path, "X_classified.json", _classified_with_entries(entries))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "T_excluded_placeholder=2" in detail, detail
    assert "T_count=0" in detail, detail
    assert "total=3" in detail, detail


def test_t_bucket_excludes_grant_goal_claim_rows(tmp_path):
    entries = [
        {"taxonomy_code": "M2A", "text": "Some Grant",
         "element_idx_start": 10, "element_idx_end": 10},
        {"taxonomy_code": "T",
         "text": "The major goals of this project are: to cure things",
         "parent_idx": 10, "element_idx_start": 11, "element_idx_end": 11},
    ]
    _write_json(tmp_path, "X_classified.json", _classified_with_entries(entries))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "T_excluded_goal_claim=1" in detail, detail
    assert "T_count=0" in detail, detail
    assert "total=2" in detail, detail


def test_t_bucket_a_genuine_misroute_still_counts(tmp_path):
    """None of the three exclusions apply to ordinary unrouted content -- the
    fix must not zero out a real T over-use signal."""
    entries = [
        {"taxonomy_code": "T",
         "text": "A genuinely unrouted piece of real content about something specific",
         "element_idx_start": 1, "element_idx_end": 1},
    ]
    _write_json(tmp_path, "X_classified.json", _classified_with_entries(entries))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "T_excluded_template=0" in detail, detail
    assert "T_excluded_placeholder=0" in detail, detail
    assert "T_excluded_goal_claim=0" in detail, detail
    assert "T_count=1" in detail, detail


def test_t_bucket_row_inside_a_grants_span_but_no_goal_falls_through_to_placeholder(tmp_path):
    """A T row that sits inside a grant's own element range is only a
    goal-claim if it actually states a goal -- `claim_goal_rows` requires
    `parse_major_goals` to return one. A bare "N/A" in that same span parses
    to no goal, so `_goal_claimed_row_ids` correctly leaves it unclaimed and
    it falls through to the placeholder check instead -- proof the three
    exclusions are checked in order and a row is only ever counted once,
    under whichever category actually applies."""
    entries = [
        {"taxonomy_code": "M2A", "text": "Some Grant",
         "element_idx_start": 10, "element_idx_end": 10},
        {"taxonomy_code": "T", "text": "N/A",
         "parent_idx": 10, "element_idx_start": 11, "element_idx_end": 11},
    ]
    _write_json(tmp_path, "X_classified.json", _classified_with_entries(entries))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "T_excluded_goal_claim=0" in detail, detail
    assert "T_excluded_placeholder=1" in detail, detail
    assert "T_count=0" in detail, detail


def test_t_bucket_legacy_meta_only_artifact_is_unaffected(tmp_path):
    """No 'entries' key at all (every pre-existing synthetic fixture in this
    file, and any artifact from before this fix) must score exactly as
    before -- no exclusion is possible without per-entry text."""
    _write_json(tmp_path, "X_classified.json",
               _classified(code_distribution={"A": 97, "T": 3}))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "T_excluded_template=0" in detail, detail
    assert "T_excluded_placeholder=0" in detail, detail
    assert "T_excluded_goal_claim=0" in detail, detail
    assert "T_count_raw=3" in detail and "T_count=3" in detail, detail


def test_t_bucket_entries_not_a_list_falls_back_gracefully(tmp_path):
    """A malformed 'entries' value (wrong shape, not the expected list of
    dicts) must not crash the scorer -- it degrades to no exclusions,
    exactly like the artifact having no 'entries' key at all."""
    data = _classified(code_distribution={"A": 97, "T": 3})
    data["entries"] = {"not": "a list"}
    _write_json(tmp_path, "X_classified.json", data)
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "T_count=3" in detail, detail


def test_t_bucket_exclusions_clamp_at_zero_on_a_meta_entries_mismatch(tmp_path):
    """meta.code_distribution and the entries list are the same writer's own
    two views of one fact and should never disagree in real output, but the
    subtraction must not go negative if they ever do -- an entries list
    claiming more excludable T rows than meta's own T count reports."""
    entries = [
        {"taxonomy_code": "T", "text": "N/A", "element_idx_start": 1, "element_idx_end": 1},
        {"taxonomy_code": "T", "text": "None", "element_idx_start": 2, "element_idx_end": 2},
    ]
    data = _classified_with_entries(entries)
    data["meta"]["code_distribution"]["T"] = 1  # meta under-reports vs. the entries list
    data["meta"]["total_entries"] = 1
    _write_json(tmp_path, "X_classified.json", data)
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "T_count=0" in detail, detail
    assert fraction == 0.0


def test_t_bucket_non_dict_entry_in_list_is_skipped_not_crashed(tmp_path):
    entries = ["not a dict", None, 42,
               {"taxonomy_code": "T", "text": "A genuinely unrouted piece of content",
                "element_idx_start": 1, "element_idx_end": 1}]
    data = _classified(code_distribution={"T": 1})
    data["entries"] = entries
    _write_json(tmp_path, "X_classified.json", data)
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "T_count=1" in detail, detail
