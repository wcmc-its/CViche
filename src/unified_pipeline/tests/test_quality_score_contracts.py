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
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline import quality_score as qs  # noqa: E402
from unified_pipeline.doctor.lints.extraction import (  # noqa: E402
    _FUNDING_SECTIONS,
    lint_grant_bucket,
)
from unified_pipeline.doctor.lints.render import lint_group_header_context  # noqa: E402
from unified_pipeline.doctor.shared import (  # noqa: E402
    docx_body_blocks,
    docx_table_rows,
)
from unified_pipeline.quality_score import (  # noqa: E402
    CAP_ONLY_GATES,
    DIMENSIONS,
    FATAL_ERROR_PATTERN,
    SCORED_ARTIFACT_COUNT,
    TOTAL_WEIGHT,
    VALID_GATE_MODES,
    FallbackServedCall,
    _goal_claimed_row_ids,
    _is_placeholder_only_row,
    _load_docx,
    _load_first,
    band_for,
    linear_interp,
    llm_fallback_served,
    missing_evidence,
    no_output_produced,
    prompt_log_fallback_served,
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
    score_stage4_group_failures,
    score_t_bucket,
    stage4_group_failures,
)
from unified_pipeline.stage4.error_codes import (  # noqa: E402
    LLM_PROVIDER_ERROR,
    LLM_RESPONSE_INVALID,
    LLM_TIMEOUT,
    NO_MATCHING_EXTRACTION,
)
from unified_pipeline.stage6.formatting import add_cviche_box  # noqa: E402

docx = pytest.importorskip("docx")
from docx import Document  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402


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


def test_sparse_tables_ignores_stage_6s_cviche_note_box(tmp_path):
    """#1388: the Appendix note box is CViche talking, not a CV table."""
    doc = _make_docx(tables=[[["Alice", "PI"], ["Bob", ""]]])
    add_cviche_box(doc, "CViche note: delete this box before sending")
    doc.save(tmp_path / "out.docx")
    fraction, detail, _ = score_sparse_tables(tmp_path)
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


# ------------------------------------------------------------ #822 raw tabs
# The same exclusion, applied to raw_tab_paragraphs: the template's own
# tabbed body paragraphs ("Signature: \t\t\t\t", "Name of Current
# Employer(s):\t", ...) are kept by stage 6, so every run was charged for
# them. A tabbed line with text of its own still counts.
# ------------------------------------------------------------ #822 raw tabs

@pytest.mark.skipif(not _TEMPLATE.exists(), reason="pristine WCM template not checked out")
def test_broken_format_template_tab_paragraphs_excluded_from_raw_tab(tmp_path):
    """Every tabbed body paragraph of the real template -- read from the
    checked-in file, not retyped -- is excluded when kept verbatim."""
    tabbed = [p.text for p in Document(_TEMPLATE).paragraphs if "\t" in p.text]
    assert len(tabbed) == 5, tabbed
    _make_docx(tabbed).save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "raw_tab_paragraphs=0" in detail, detail
    assert "template_tab_excluded=5" in detail, detail
    assert fraction == 0.0, detail


def test_broken_format_template_tab_paragraph_whitespace_variant_excluded(tmp_path):
    """Matched on whitespace-NORMALIZED text: a different tab run than the
    template's own ("Signature: \t\t\t\t") is still the template line."""
    _make_docx(["Signature:\t"]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "raw_tab_paragraphs=0" in detail, detail
    assert "template_tab_excluded=1" in detail, detail


def test_broken_format_filled_template_tab_line_still_counted(tmp_path):
    """A faculty member's real tabbed line -- a template label with a value
    after the tab -- is not the template's own text, so it still counts,
    and only it reaches the fraction."""
    _make_docx(["Name:\tJane Q. Doe", "Signature: \t\t\t\t"]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "raw_tab_paragraphs=1" in detail, detail
    assert "template_tab_excluded=1" in detail, detail
    assert fraction == pytest.approx(0.6 * (1 / 20)), detail


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
    # #452: tables with no CV content are not charged -- the render put
    # nothing into them, the same as the blank template's own scaffolding.
    _make_docx(tables=[[["", ""], ["", ""]], [["", ""], ["", ""]]]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_sparse_tables(tmp_path)
    assert fraction == 0.0
    assert detail == "total_tables=2; no table carries CV content"


def test_sparse_tables_blank_template_scores_zero(tmp_path):
    """#452's acceptance invariant: the empty WCM template carries no CV
    content, so it must not take any sparse-tables penalty (it took the
    full 12 points: 31 of its 33 tables are >=50% empty by construction)."""
    shutil.copy(qs._TEMPLATE_DOCX_PATH, tmp_path / "X_wcm.docx")
    fraction, detail, cap = score_sparse_tables(tmp_path)
    assert (fraction, cap) == (0.0, None)
    assert detail == "total_tables=33; no table carries CV content"


def test_sparse_tables_scores_only_tables_carrying_cv_content(tmp_path):
    # One template-only table (a real label cell from the template), one
    # half-filled content table: only the second is judged, and it is sparse.
    label = min(qs._template_cell_texts())
    _make_docx(tables=[[[label, ""], ["", ""]],
                       [["Cardiology Grand Rounds", ""], ["", ""]]]).save(tmp_path / "out.docx")
    fraction, detail, cap = score_sparse_tables(tmp_path)
    assert "total_tables=2; scored_tables=1; sparse_tables=1; sparse_table_ratio=1.000;" in detail
    assert "empty_cells=3/4" in detail
    assert fraction == 1.0


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


# #427: an entry with nothing to extract counts on neither term. YTPMZK's own
# shapes: a stage-4-skipped entry, a column-header row, a placeholder row, and
# template instruction text (each helper isolated; the constants are defined
# further down, so they are looked up at call time).
@pytest.mark.parametrize("extra", [
    {"taxonomy_code": "T", "text": "N/A", "extraction_skipped": True},
    {"taxonomy_code": "T", "text": "x", "extraction_skipped": True},
    {"taxonomy_code": "N3A", "text": "Site/Position |"},
    {"taxonomy_code": "K3", "text": "N/A"},
    {"taxonomy_code": "A", "text": "_CONTAINMENT_ONLY_TEMPLATE_TEXT"},
    {"taxonomy_code": "A", "text": "_NEAR_TEMPLATE_INSTRUCTION_TEXT"},
    {"taxonomy_code": "A", "text": "_FOREIGN_TEMPLATE_INSTRUCTION_TEXT"},
])
def test_field_sparseness_entry_with_nothing_to_extract_counts_on_neither_term(
        tmp_path, extra):
    extra = {**extra, "text": globals().get(extra["text"], extra["text"])}
    entries = [{**_BOTH, **extra}] + [_CLEAN] * 9
    fraction, detail, _ = _sparseness(tmp_path, entries)
    assert "nothing_to_extract=1" in detail, detail
    assert "allnull_or_zerocov=0" in detail, detail
    assert "success_rate=1.000" in detail, detail   # denominator stays 10
    assert fraction == 0.0


@pytest.mark.parametrize("code,text", [
    ("K3", "Developed curriculum for residents 1995"),
    # stage 4 extracts T entries, so a real T entry's miss still counts
    ("T", "Chaired the departmental seminar series"),
    # every piece is a template label, but it is a real J effort record
    ("J", "Clinical | 100%"),
])
def test_field_sparseness_real_content_with_null_fields_still_counts(tmp_path, code, text):
    entries = [{**_BOTH, "taxonomy_code": code, "text": text}] + [_CLEAN] * 9
    fraction, detail, _ = _sparseness(tmp_path, entries)
    assert "nothing_to_extract=0" in detail, detail
    assert fraction == pytest.approx(1.0)


def test_field_sparseness_non_string_text_is_scored_not_crashed(tmp_path):
    entries = [{**_BOTH, "text": 5}] + [_CLEAN] * 9
    fraction, detail, _ = _sparseness(tmp_path, entries)
    assert "nothing_to_extract=0" in detail, detail
    assert fraction == pytest.approx(1.0)


def test_field_sparseness_exclusion_leaves_the_denominator_at_every_entry(tmp_path):
    t_entry = {**_BOTH, "taxonomy_code": "T", "text": "x", "extraction_skipped": True}
    fraction, detail, _ = _sparseness(tmp_path, [t_entry, _FAILED_ONLY] + [_CLEAN] * 8)
    assert "success_rate=0.900" in detail, detail   # 1 failure of 10, not of 9
    assert fraction == pytest.approx(0.5)


def test_field_sparseness_missing_success_flag_counts_as_a_failure(tmp_path):
    no_flag = {"extracted_fields": {"title": "x"}}
    _, detail, _ = _sparseness(tmp_path, [no_flag] + [_CLEAN] * 9)
    assert "success_rate=0.900" in detail, detail



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


# --------------------------------------------------------------------- D21b
# #1174 stage-4 failed extraction groups: a cap-only gate (weight 0) read from
# stage 4's own `extraction_error` markers and `stats.failed_batches`.
# --------------------------------------------------------------------- D21b

def _entry_in_failed_group(code="A1", error=LLM_RESPONSE_INVALID, rescued=False):
    """An entry of a taxonomy group whose extraction call failed, as stage 4
    writes it: no fields and the error. When the recovery pass succeeds it sets
    extraction_success and llm_recovery_applied and KEEPS the error."""
    return {"taxonomy_code": code, "extraction_error": error,
            "extraction_success": rescued, "llm_recovery_applied": rescued,
            "extracted_fields": {"note": "x"} if rescued else {}}


def _clean_entry(code="A1"):
    return {"taxonomy_code": code, "extraction_success": True,
            "extracted_fields": {"note": "x"}}


def _stage4_artifact(entries, **stats):
    data = {"cv_owner": {"full_name": "Jane Q. Public"}, "entries": entries}
    if stats:
        data["stats"] = stats
    return data


def test_stage4_group_failures_splits_rescued_entries_from_unrecovered_ones():
    entries = [_entry_in_failed_group("P", rescued=True) for _ in range(3)]
    entries.append(_entry_in_failed_group("M2A", error=LLM_TIMEOUT))
    entries += [_clean_entry(), _clean_entry()]

    failures = stage4_group_failures(_stage4_artifact(entries, failed_batches=2))

    assert failures.failed_batches == 2
    assert failures.entries_failed == 4
    assert failures.entries_rescued == 3
    assert failures.entries_unrecovered == 1
    assert failures.entries_by_code == {"M2A": 1, "P": 3}
    assert failures.errors == {LLM_RESPONSE_INVALID: 3, LLM_TIMEOUT: 1}


def test_stage4_group_failures_sees_a_fully_rescued_group_that_extraction_failed_cannot():
    """The shape of the batch's silent-GREEN run: every entry of the failed
    group was rescued, so stats.extraction_failed (entries still unextracted
    AFTER recovery) is 0 and a count of extraction_success == False is 0 too."""
    entries = [_entry_in_failed_group("P", rescued=True) for _ in range(8)]
    artifact = _stage4_artifact(entries, failed_batches=1, extraction_failed=0)
    assert not any(e["extraction_success"] is False for e in artifact["entries"])

    failures = stage4_group_failures(artifact)

    assert failures is not None
    assert (failures.entries_failed, failures.entries_unrecovered) == (8, 0)


def test_stage4_group_failures_ignores_a_per_entry_miss_in_a_successful_call():
    """NO_MATCHING_EXTRACTION is not a failed call: the group's reply simply
    held no item for this entry. score_field_sparseness already counts it."""
    entries = [{"taxonomy_code": "A1", "extraction_success": False,
                "extraction_error": NO_MATCHING_EXTRACTION, "extracted_fields": {}}]
    assert stage4_group_failures(_stage4_artifact(entries, failed_batches=0)) is None


def test_stage4_group_failures_counts_a_code_stage_4_adds_later():
    """Everything but the one non-failure counts, so a new failure code is not
    silently missed -- the gap this gate exists to close."""
    entries = [_entry_in_failed_group(error="llm_some_future_failure")]
    assert stage4_group_failures(_stage4_artifact(entries)).errors == {
        "llm_some_future_failure": 1}


def test_stage4_group_failures_reads_the_failed_batches_stat_on_its_own():
    failures = stage4_group_failures(_stage4_artifact([_clean_entry()], failed_batches=1))
    assert (failures.failed_batches, failures.entries_failed) == (1, 0)


@pytest.mark.parametrize("artifact", [
    None, [], {}, {"entries": None}, {"entries": "x"}, {"entries": [None, 3, "x"]},
    {"entries": [_clean_entry()]},
    {"entries": [], "stats": "x"},
    {"entries": [], "stats": {"failed_batches": 0}},
    {"entries": [], "stats": {"failed_batches": -1}},
    {"entries": [], "stats": {"failed_batches": "1"}},
], ids=repr)
def test_stage4_group_failures_is_none_without_a_failed_group_and_never_raises(artifact):
    assert stage4_group_failures(artifact) is None


def test_score_stage4_group_failures_caps_one_point_under_green_even_when_all_rescued(tmp_path):
    entries = [_entry_in_failed_group("P", rescued=True) for _ in range(8)]
    _write_json(tmp_path, "X_fields.json", _stage4_artifact(entries, failed_batches=1))

    fraction, detail, cap = score_stage4_group_failures(tmp_path)

    assert fraction == 1.0
    assert cap == qs.BAND_GREEN - 1 == 84
    assert "entries_failed=8 (rescued=8, unrecovered=0)" in detail
    assert "taxonomy_codes=P:8" in detail


def test_score_stage4_group_failures_quiet_without_a_failure_or_an_artifact(tmp_path):
    assert score_stage4_group_failures(tmp_path) == (0.0, "no fields.json found", None)

    _write_json(tmp_path, "X_fields.json", _stage4_artifact([_clean_entry()], failed_batches=0))
    assert score_stage4_group_failures(tmp_path) == (
        0.0, "no failed extraction group", None)

    _truncate(tmp_path, "X_fields.json")
    fraction, detail, cap = score_stage4_group_failures(tmp_path)
    assert (fraction, cap) == (0.0, None)
    assert detail.startswith("fields.json unreadable")


def test_stage4_group_failure_gate_is_cap_only_and_moves_no_raw_score():
    assert score_stage4_group_failures not in [scorer for _, _, scorer in DIMENSIONS]
    assert score_stage4_group_failures in [gate for _, gate in CAP_ONLY_GATES]
    assert TOTAL_WEIGHT == 100


def test_score_run_keeps_a_run_with_a_failed_stage4_group_out_of_green(tmp_path):
    """End to end through score_run (the gate is dispatched from CAP_ONLY_GATES,
    which the scorer-level tests above never touch). The fixture run scores
    exactly 85 GREEN, so a cap of 84 is visible; the same run with one rescued
    failed group must differ in the cap and nothing else."""
    clean = score_run(_complete_run_dir(tmp_path))
    assert (clean["totalScore"], clean["band"]) == (85, "GREEN (ship)")

    rescued = {**_clean_entry("P"), "extraction_error": LLM_RESPONSE_INVALID,
               "llm_recovery_applied": True, "extracted_fields": {"email": "j@x.org"}}
    artifact = _stage4_artifact([rescued], failed_batches=1)
    artifact["cv_owner_location"] = {"inference_success": True, "primary_location": "NY"}
    _write_json(tmp_path, "X_fields.json", artifact)
    failed = score_run(tmp_path)

    assert failed["raw_score_before_caps"] == clean["raw_score_before_caps"]
    assert failed["total_weight"] == clean["total_weight"]
    assert len(failed["dimensionScores"]) == len(clean["dimensionScores"])
    assert failed["hard_fail_caps_applied"] == [84]
    assert failed["totalScore"] == 84 and failed["band"].startswith("YELLOW")
    assert failed["flags"][0].startswith(
        "HARD-FAIL cap=84: Stage-4 extraction group failed (caps below GREEN) (failed_batches=1;")
    assert clean["hard_fail_caps_applied"] == []


def test_the_scorer_reads_the_markers_stage_4_actually_writes(monkeypatch):
    """Producer -> consumer wire (#1174): stage 4's real failed-group path, with
    the LLM stubbed, writes entries the scorer counts. The group call returns
    invalid JSON; the recovery pass rescues the one entry long enough to be
    eligible (>= 50 chars) and cannot touch the short one."""
    from unified_pipeline.stage4 import extraction

    replies = iter([
        {"content": "not valid json", "cost": 0.0, "total_tokens": 0},
        {"content": json.dumps({"recovered_entries": [
            {"entry_id": "0_0", "fields": {"note": "recovered"}}]}),
         "cost": 0.0, "total_tokens": 0},
    ])
    monkeypatch.setattr(extraction, "call_llm", lambda **kwargs: next(replies))
    entries = [
        {"text": "Example entry with well over fifty characters of padding text.",
         "taxonomy_code": "A1", "element_idx_start": 0, "element_idx_end": 0},
        {"text": "too short", "taxonomy_code": "A1",
         "element_idx_start": 1, "element_idx_end": 1},
    ]

    result = extraction.extract_fields_batch(entries, 0, 1)
    failures = stage4_group_failures({"entries": result["entries"]})

    assert result["success"] is False
    assert failures.errors == {LLM_RESPONSE_INVALID: 2}
    assert failures.entries_by_code == {"A1": 2}
    assert (failures.entries_rescued, failures.entries_unrecovered) == (1, 1)


def test_the_scorer_does_not_count_the_per_entry_miss_stage_4_actually_writes(monkeypatch):
    """The other half of the wire: a group call that SUCCEEDS but omits one
    entry marks it NO_MATCHING_EXTRACTION, which is not a failed group."""
    from unified_pipeline.stage4 import extraction

    reply = {"content": json.dumps({"entries": [{"entry_index": 0, "note": "kept"}]}),
             "cost": 0.0, "total_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0}
    monkeypatch.setattr(extraction, "call_llm", lambda **kwargs: reply)
    entries = [
        {"text": "kept", "taxonomy_code": "A1", "element_idx_start": 0, "element_idx_end": 0},
        {"text": "omitted by the reply", "taxonomy_code": "A1",
         "element_idx_start": 1, "element_idx_end": 1},
    ]

    result = extraction.extract_fields_batch(entries, 0, 1)

    assert [e.get("extraction_error") for e in result["entries"]] == [
        None, NO_MATCHING_EXTRACTION]
    assert stage4_group_failures({"entries": result["entries"]}) is None


def test_dimension_weights_sum_to_100():
    """Whole-number points per dimension: a dimension of weight w loses exactly
    w * fraction points, with no normalization."""
    assert TOTAL_WEIGHT == 100


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

# The real instruction above, prefixed with extra faculty-authored text so it
# is no longer an EXACT match (rule a) -- it isolates `is_template_instruction`
# rule (c), containment, from the other two OR-branches in score_t_bucket:
# pipe-free but short of _NEAR_MATCH_MIN_RATIO's 0.93 (the prefix drags the
# whole-string similarity ratio down to ~0.88), so is_near_template_instruction
# is False; and is_template_label_line splits on "|"/tab/newline only, so the
# whole padded sentence is ONE piece that is not itself a known label/instruction
# verbatim, so it is also False. A mutant that replaces is_template_instruction's
# result with False (dropping rules a-d entirely) has nothing else in the OR to
# fall back on for this text.
_CONTAINMENT_ONLY_TEMPLATE_TEXT = "See attached: " + _REAL_TEMPLATE_INSTRUCTION_TEXT

# A short template FIELD LABEL ("degree"), not a directive sentence: below
# is_template_instruction's own _MIN_EXACT_LEN (25 chars) and
# is_near_template_instruction's _CONTAINMENT_MIN_LEN (40 chars) floors, so
# both are False for it -- only is_template_label_line's "every piece is a
# known label, no length floor" rule matches a bare label line like this
# (the Appendix-only case its docstring describes: an unfilled field, a
# column-header row). Isolates that helper from the other two OR-branches.
_LABEL_ONLY_LINE_TEXT = "Degree"

# Another institution's template instruction (#530): invented, and False under
# all three WCM-only helpers, so only `is_foreign_template_instruction` claims it.
_FOREIGN_TEMPLATE_INSTRUCTION_TEXT = (
    "C. Sample Appointments (include institution, title and dates of appointment)")


def test_t_bucket_excludes_foreign_template_instruction_text(tmp_path):
    """The WCM-only helpers do not match this text, so a mutant dropping
    `is_foreign_template_instruction(text)` from score_t_bucket's OR-condition
    counts it as a real T entry."""
    entries = [
        {"taxonomy_code": "A", "text": "Real content",
         "element_idx_start": 1, "element_idx_end": 1},
        {"taxonomy_code": "T", "text": _FOREIGN_TEMPLATE_INSTRUCTION_TEXT,
         "element_idx_start": 2, "element_idx_end": 2},
    ]
    _write_json(tmp_path, "X_classified.json", _classified_with_entries(entries))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "T_excluded_template=1" in detail, detail
    assert "T_count=0" in detail, detail


def test_placeholder_only_row_helper():
    assert _is_placeholder_only_row("N/A | N/A") is True
    assert _is_placeholder_only_row("Not Applicable") is True
    assert _is_placeholder_only_row("none") is True
    assert _is_placeholder_only_row("N/A.") is True
    assert _is_placeholder_only_row("na") is True            # is_unanswered_prompt's own "na" spelling
    assert _is_placeholder_only_row("Listed above") is True  # ditto, its "listed above" spelling
    assert _is_placeholder_only_row("| |") is True          # bare pipe row
    assert _is_placeholder_only_row("N/A\tN/A") is True      # tab-separated cells (wrapped source line)
    assert _is_placeholder_only_row("N/A\nNone") is True     # newline-separated cells
    assert _is_placeholder_only_row("") is False
    assert _is_placeholder_only_row(None) is False
    assert _is_placeholder_only_row("Teaching") is False     # real one-word entry
    # A known template LABEL paired with an unanswered value is exactly what
    # `is_unanswered_prompt` treats as a non-answer (its own docstring
    # example: "Primary Hospital Affiliation: | N/A") -- "Teaching" is
    # itself a recognized template label, not incidental real content, so
    # reusing that helper here correctly now excludes this row too.
    assert _is_placeholder_only_row("N/A | Teaching") is True
    # Real, non-label content paired with an unanswered cell is still kept.
    assert _is_placeholder_only_row("N/A | Robotic Surgery Outcomes") is False
    # A real row with a blank cell is not blank: only an ALL-blank row is.
    assert _is_placeholder_only_row("Robotic Surgery Outcomes | ") is False
    assert _is_placeholder_only_row("Robotic Surgery | | 2019") is False


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


def test_goal_claimed_row_ids_ignores_a_non_grant_entry_sharing_the_grants_span():
    """`_goal_claimed_row_ids` must build its ownership candidates from ONLY
    the M2A/M2B/M2C grant codes, not every entry -- `claim_goal_rows` treats
    a `parent_idx` spanned by more than one candidate as ambiguous (`len(
    owners) != 1`) and claims nothing. A non-grant entry ("A") that happens
    to share the real grant's exact element range must therefore be excluded
    from the candidate list before calling in, or a genuine, unambiguous
    goal-claim silently stops being claimed the moment an unrelated entry's
    source element sits at the same index as the grant's."""
    grant = {"taxonomy_code": "M2A", "text": "Some Grant",
              "element_idx_start": 10, "element_idx_end": 10}
    overlapping_non_grant = {"taxonomy_code": "A", "text": "An unrelated entry",
                              "element_idx_start": 10, "element_idx_end": 10}
    goal_row = {"taxonomy_code": "T",
                "text": "The major goals of this project are: to cure things",
                "parent_idx": 10, "element_idx_start": 11, "element_idx_end": 11}
    entries = [grant, overlapping_non_grant, goal_row]
    claimed = _goal_claimed_row_ids(entries)
    assert claimed == {id(goal_row)}


def test_goal_claimed_row_ids_only_considers_t_coded_rows_as_candidates():
    """Only T-coded entries are claim candidates, as in stage 6
    (`entries_by_code['T']`). A mutant widening candidates to every entry
    also claims the non-T goal-shaped row, so the claimed set gains a non-T
    id; this pins the exact set. (T_count itself would not move under that
    mutant: the extra id is not a T entry.)"""
    grant = {"taxonomy_code": "M2A", "text": "Some Grant",
              "element_idx_start": 10, "element_idx_end": 10}
    non_t_row = {"taxonomy_code": "A",
                 "text": "The major goals of this project are: to build widgets",
                 "parent_idx": 10, "element_idx_start": 10, "element_idx_end": 10}
    goal_row = {"taxonomy_code": "T",
                "text": "The major goals of this project are: to cure things",
                "parent_idx": 10, "element_idx_start": 11, "element_idx_end": 11}
    entries = [grant, non_t_row, goal_row]
    claimed = _goal_claimed_row_ids(entries)
    assert claimed == {id(goal_row)}


def test_goal_claimed_row_ids_degrades_to_empty_set_when_stage6_import_fails(monkeypatch):
    """`_goal_claimed_row_ids`'s ``except ImportError: return set()`` fallback
    (its own docstring: "a missing python-docx degrades this one exclusion to
    claim nothing") needs its own test -- python-docx is installed in this
    environment, so nothing else exercises the except branch, and a mutant
    that replaces it with a bare `raise` would break every OTHER
    quality_score dimension's ability to run against a real artifact, not
    just silence this one exclusion. A `None` entry in `sys.modules` forces
    `ModuleNotFoundError` (an `ImportError` subclass) on the `from ... import`
    regardless of whether the real dependency is present, without needing to
    actually uninstall python-docx for the test."""
    monkeypatch.setitem(
        sys.modules, "unified_pipeline.stage6.sections.research_support", None)
    grant = {"taxonomy_code": "M2A", "text": "Some Grant",
              "element_idx_start": 10, "element_idx_end": 10}
    goal_row = {"taxonomy_code": "T",
                "text": "The major goals of this project are: to cure things",
                "parent_idx": 10, "element_idx_start": 11, "element_idx_end": 11}
    assert _goal_claimed_row_ids([grant, goal_row]) == set()


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


def test_t_bucket_excludes_text_only_is_template_instruction_matches(tmp_path):
    """`_CONTAINMENT_ONLY_TEMPLATE_TEXT` is True under `is_template_instruction`
    (containment) but False under both `is_near_template_instruction` and
    `is_template_label_line` -- unlike every other fixture in this file, whose
    text happens to satisfy more than one of the three helpers at once. A
    mutant that replaces `is_template_instruction(text)` with `False` in
    score_t_bucket's OR-condition has no other helper to fall back on for
    this text, so this is the only test that isolates it."""
    entries = [
        {"taxonomy_code": "A", "text": "Real content",
         "element_idx_start": 1, "element_idx_end": 1},
        {"taxonomy_code": "T", "text": _CONTAINMENT_ONLY_TEMPLATE_TEXT,
         "element_idx_start": 2, "element_idx_end": 2},
    ]
    _write_json(tmp_path, "X_classified.json", _classified_with_entries(entries))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "T_excluded_template=1" in detail, detail
    assert "T_count=0" in detail, detail


def test_t_bucket_excludes_a_short_template_label_only_line(tmp_path):
    """`_LABEL_ONLY_LINE_TEXT` is True under `is_template_label_line` only --
    both `is_template_instruction` and `is_near_template_instruction` require
    at least 25/40 normalized chars and this label is far shorter. A mutant
    that replaces `is_template_label_line(text)` with `False` has no other
    helper to fall back on for this text."""
    entries = [
        {"taxonomy_code": "A", "text": "Real content",
         "element_idx_start": 1, "element_idx_end": 1},
        {"taxonomy_code": "T", "text": _LABEL_ONLY_LINE_TEXT,
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


def test_t_bucket_a_row_matching_two_reasons_is_excluded_only_once(tmp_path):
    """A row can genuinely satisfy BOTH the template-instruction check and
    the goal-claim check at once: the template's own major-goals LABEL
    (`is_template_instruction`, via containment) with real goal text
    appended after it (which `parse_major_goals` still reads as a claim,
    and `claim_goal_rows` still matches to the owning grant by span). The
    goal-claim branch runs first and the others are `elif`, so this must be
    subtracted once, not twice."""
    text = "(Optional - The major goals of this project are): to cure disease"
    entries = [
        {"taxonomy_code": "M2A", "text": "Some Grant",
         "element_idx_start": 10, "element_idx_end": 10},
        {"taxonomy_code": "T", "text": text,
         "parent_idx": 10, "element_idx_start": 11, "element_idx_end": 11},
    ]
    _write_json(tmp_path, "X_classified.json", _classified_with_entries(entries))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "T_excluded_goal_claim=1" in detail, detail
    assert "T_excluded_template=0" in detail, detail
    assert "T_count=0" in detail, detail


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


def test_t_bucket_non_t_entries_are_never_checked_against_the_exclusions(tmp_path):
    """The per-entry loop must skip a non-T entry entirely (`if
    entry.get("taxonomy_code") != "T": continue`) rather than merely not
    counting it toward `t_count_raw` -- the T_excluded_* counters are
    subtracted from `code_dist.get("T", 0)`, which already counts ONLY T
    entries, so a non-T entry that happens to read as placeholder/template
    text must not increment any excluded_* counter either. A real, unrouted
    T entry is included alongside an "A"-coded placeholder row so the
    difference is visible: dropping the skip would incorrectly subtract the
    "A" row's placeholder match from the genuine T entry's count."""
    entries = [
        {"taxonomy_code": "T",
         "text": "A genuinely unrouted piece of real content about something specific",
         "element_idx_start": 1, "element_idx_end": 1},
        {"taxonomy_code": "A", "text": "N/A", "element_idx_start": 2, "element_idx_end": 2},
    ]
    _write_json(tmp_path, "X_classified.json", _classified_with_entries(entries))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "T_excluded_placeholder=0" in detail, detail
    assert "T_count=1" in detail, detail


def test_t_bucket_ratio_denominator_is_the_unchanged_total_entries(tmp_path):
    """t_ratio must divide by `total` (all entries, every code), never by
    `total - excluded` -- pins the "denominator left unchanged" judgement
    call with an exact ratio value. Needs a case with BOTH a genuine
    (unexcluded) T entry and an excluded one, so t_count is nonzero and
    total != total - excluded; every other exclusion test in this file has
    T_count=0, under which both denominators give the same (zero) ratio and
    so cannot tell them apart."""
    entries = [
        {"taxonomy_code": "T",
         "text": "A genuinely unrouted piece of real content about something specific",
         "element_idx_start": 1, "element_idx_end": 1},
        {"taxonomy_code": "T", "text": "N/A", "element_idx_start": 2, "element_idx_end": 2},
        {"taxonomy_code": "A", "text": "Real content", "element_idx_start": 3, "element_idx_end": 3},
    ]
    _write_json(tmp_path, "X_classified.json", _classified_with_entries(entries))
    fraction, detail, cap = score_t_bucket(tmp_path)
    assert "T_excluded_placeholder=1" in detail, detail
    assert "T_count=1" in detail, detail
    assert "total=3" in detail, detail
    # 1/3, NOT 1/(3-1)=0.5 -- the mutant this test kills.
    assert "t_ratio=0.3333" in detail, detail


# ------------------------------------------------------ #461 tracked changes
#
# Stage 6 writes enriched citations, institution locations and the research
# summary as `<w:ins>` tracked insertions. python-docx's `.text` skips runs
# that are not direct children of the paragraph, so the docx dimensions must
# read the accepted-changes view (`doctor.shared._docx_text`) instead. Text
# in a `<w:del>` (`w:delText`) is not part of that view.

def _tracked(paragraph, text, kind="ins"):
    """Append `text` to `paragraph` as a tracked insertion (or deletion)."""
    from docx.oxml import OxmlElement
    wrapper = OxmlElement(f"w:{kind}")
    wrapper.set(qn("w:id"), "1")
    wrapper.set(qn("w:author"), "PubMed Enrichment")
    run = OxmlElement("w:r")
    node = OxmlElement("w:t" if kind == "ins" else "w:delText")
    node.text = text
    run.append(node)
    wrapper.append(run)
    paragraph._p.append(wrapper)
    return paragraph


def test_python_docx_text_misses_tracked_insertion_fixture():
    """Guards the fixtures below: the naive reader really is blind to them."""
    doc = _make_docx([""])
    _tracked(doc.paragraphs[0], "inserted\ttext")
    assert doc.paragraphs[0].text == ""


def test_sparse_tables_cell_filled_only_by_tracked_insertion_is_not_empty(tmp_path):
    doc = _make_docx(tables=[[["a", ""], ["b", "c"]]])
    _tracked(doc.tables[0].cell(0, 1).paragraphs[0], "enriched location")
    doc.save(tmp_path / "out.docx")
    fraction, detail, cap = score_sparse_tables(tmp_path)
    assert "empty_cells=0/4" in detail, detail


def test_sparse_tables_cell_with_only_tracked_deletion_is_still_empty(tmp_path):
    doc = _make_docx(tables=[[["a", ""], ["b", "c"]]])
    _tracked(doc.tables[0].cell(0, 1).paragraphs[0], "removed text", kind="del")
    doc.save(tmp_path / "out.docx")
    fraction, detail, cap = score_sparse_tables(tmp_path)
    assert "empty_cells=1/4" in detail, detail


def test_broken_format_tab_inside_tracked_insertion_paragraph_counted(tmp_path):
    doc = _make_docx([""])
    _tracked(doc.paragraphs[0], "research summary with a\traw tab")
    doc.save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "raw_tab_paragraphs=1" in detail, detail


def test_broken_format_tab_inside_tracked_insertion_cell_counted(tmp_path):
    doc = _make_docx(tables=[[["", "clean"]]])
    _tracked(doc.tables[0].cell(0, 0).paragraphs[0], "location\tvalue")
    doc.save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "raw_tab_cells=1" in detail, detail


def test_broken_format_tab_inside_tracked_deletion_not_counted(tmp_path):
    doc = _make_docx([""])
    _tracked(doc.paragraphs[0], "gone\ttext", kind="del")
    doc.save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "raw_tab_paragraphs=0" in detail, detail


def test_broken_format_tab_stop_definition_is_not_a_tab_character(tmp_path):
    """`<w:pPr><w:tabs><w:tab/>` declares a tab stop; it renders no tab."""
    doc = _make_docx(["plain paragraph"])
    doc.paragraphs[0].paragraph_format.tab_stops.add_tab_stop(1000)
    doc.save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "raw_tab_paragraphs=0" in detail, detail


def test_broken_format_prompt_echo_inside_tracked_insertion_counted(tmp_path):
    doc = _make_docx([""])
    _tracked(doc.paragraphs[0], "Please list here your publications")
    doc.save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "echo_paragraphs=1" in detail, detail


def test_broken_format_template_tab_line_split_across_insertion_still_excluded(tmp_path):
    """The template exclusion compares the accepted-changes text, so a kept
    template line whose text is partly a tracked insertion is still it."""
    doc = _make_docx(["Signature:"])
    _tracked(doc.paragraphs[0], "\t")
    doc.save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "template_tab_excluded=1" in detail, detail
    assert "raw_tab_paragraphs=0" in detail, detail


def test_broken_format_template_tab_cell_inside_insertion_still_excluded(tmp_path):
    doc = _make_docx(tables=[[["", "clean"]]])
    _tracked(doc.tables[0].cell(0, 0).paragraphs[0], "Project title:\t\t")
    doc.save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "raw_tab_cells=0" in detail, detail


def test_broken_format_line_break_keeps_template_paragraph_match(tmp_path):
    """A `<w:br/>` renders as a newline (as python-docx's `.text` does), which
    whitespace normalisation folds into the template's space -- so a kept
    template line broken across two lines is still the template line."""
    doc = _make_docx([""])
    run = doc.paragraphs[0].add_run("Date of")
    run.add_break()
    doc.paragraphs[0].add_run("Preparation:\t")
    doc.save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "template_tab_excluded=1" in detail, detail


def test_broken_format_page_break_renders_no_whitespace(tmp_path):
    """A page break adds no text (python-docx agrees), so it glues the words
    and the line is no longer the template line."""
    from docx.enum.text import WD_BREAK
    doc = _make_docx([""])
    run = doc.paragraphs[0].add_run("Date of")
    run.add_break(WD_BREAK.PAGE)
    doc.paragraphs[0].add_run("Preparation:\t")
    doc.save(tmp_path / "out.docx")
    fraction, detail, cap = score_broken_format(tmp_path)
    assert "raw_tab_paragraphs=1" in detail, detail


# --------------------------------------------------------------------- D21c
# #1174 a call the content-filter fallback served: read from the provenance
# stage 4 and stage 4.5 write, for the doctor only; the score does not cap on it.
# --------------------------------------------------------------------- D21c

_FB_MODEL = "example.fallback-model-1"


def _fb_entry(code="S1"):
    return {**_clean_entry(code), "llm_fallback_model": _FB_MODEL}


def test_llm_fallback_served_groups_stage_4_entries_by_section_and_lists_4_5_calls():
    served = llm_fallback_served(
        _stage4_artifact([_fb_entry("S1"), _fb_entry("S1"), _fb_entry("M2A"), _clean_entry("S1")]),
        {"llm_fallback_calls": [{"call": "summary_generation", "model": _FB_MODEL}]})

    assert served == [
        FallbackServedCall("4", "M2A", _FB_MODEL, 1, "entries"),
        FallbackServedCall("4", "S1", _FB_MODEL, 2, "entries"),
        FallbackServedCall("4.5", "research summary (summary_generation)", _FB_MODEL, 1, "call")]
    assert served[1].describe() == f"stage 4 S1 on {_FB_MODEL} (2 entries)"


@pytest.mark.parametrize("stage_4, stage_4_5", [
    (None, None), ({}, {}), ({"entries": None}, {"llm_fallback_calls": None}),
    ({"entries": [None, 3, "x", _clean_entry()]}, {"llm_fallback_calls": [None, {"call": "x"}]}),
    ([], "x"),
], ids=repr)
def test_llm_fallback_served_is_empty_without_provenance_and_never_raises(stage_4, stage_4_5):
    assert llm_fallback_served(stage_4, stage_4_5) == []


def _prompt_log(directory, name, purpose, response):
    (directory / f"{name}_RESPONSE.json").write_text(json.dumps({"purpose": purpose, "response": response}))


def test_prompt_log_fallback_served_counts_served_calls_per_stage_but_not_4_5(tmp_path):
    served = {"model": _FB_MODEL, "served_by_fallback_model": _FB_MODEL}
    _prompt_log(tmp_path, "a", "stage_5d", served)
    _prompt_log(tmp_path, "b", "stage_5d", served)
    _prompt_log(tmp_path, "c", "stage_3b", served)
    _prompt_log(tmp_path, "d", "stage_4_5", served)   # stage 4.5 lists its own
    _prompt_log(tmp_path, "e", "stage_5d", {"model": "primary"})
    (tmp_path / "f_RESPONSE.json").write_text("{torn")
    _prompt_log(tmp_path, "g", "stage_5d", "not a dict")
    (tmp_path / "h.json").write_text(json.dumps({"purpose": "stage_6", "response": served}))  # a prompt, not a response

    assert prompt_log_fallback_served(tmp_path) == [
        FallbackServedCall("3b", "calls", _FB_MODEL, 1, "calls"),
        FallbackServedCall("5d", "calls", _FB_MODEL, 2, "calls")]
    assert prompt_log_fallback_served(None) == []
    assert prompt_log_fallback_served(tmp_path / "absent") == []


def test_score_run_does_not_cap_a_run_with_a_fallback_served_call(tmp_path):
    """#1174 (Paul, 2026-10-05): a call the fallback served succeeded and its
    reply parsed, so the score ignores it. The same 85 GREEN run with a served
    stage-4 group and a served stage-4.5 call scores exactly the same."""
    for name in ("clean", "served"):
        (tmp_path / name).mkdir()
    clean = score_run(_complete_run_dir(tmp_path / "clean"))
    assert (clean["totalScore"], clean["band"]) == (85, "GREEN (ship)")

    served_dir = _complete_run_dir(tmp_path / "served")
    fields = json.loads((served_dir / "X_fields.json").read_text())
    fields["entries"][0]["llm_fallback_model"] = _FB_MODEL
    _write_json(served_dir, "X_fields.json", fields)
    _write_json(served_dir, "X_research_summary.json",
                {"llm_fallback_calls": [{"call": "m1_relevance_score", "model": _FB_MODEL}]})
    assert llm_fallback_served(fields, json.loads(
        (served_dir / "X_research_summary.json").read_text()))
    served = score_run(served_dir)

    for key in ("totalScore", "band", "raw_score_before_caps", "hard_fail_caps_applied", "flags"):
        assert served[key] == clean[key], key
    assert not any("fallback model" in name for name, _gate in CAP_ONLY_GATES)


# --------------------------------------------------------------------- D23
# #822: cap-only content-loss gates (under-extracted entry, fused entries,
# lost source table). Synthetic text only.
# --------------------------------------------------------------------- D23

def _big_multi_record_entry(coverage_pct):
    """A stage-4 entry over under_extraction's size and record floors, with the
    given extraction coverage (the lint fires below 40%)."""
    lines = [f"Example Society {i} of Medicine and Surgery | Member | 201{i}-202{i} | "
             "Committee on Sample Matters" for i in range(12)]
    return {"element_idx_start": 7, "text": "\n".join(lines),
            "extracted_fields": {"role": "Member"}, "extraction_success": True,
            "extraction_coverage": {"extraction_coverage_percent": coverage_pct}}


def _fused_entry(element_type="table_row"):
    """An entry packing MEGA_ENTRY_MIN_RECORDS record-like lines."""
    lines = [f"Example Grant {i} Title Words Here | Example Agency | 2011-2014 | Role: PI"
             for i in range(3)]
    return {"element_type": element_type, "text": "\n".join(lines)}


def _source_docx_with_table(root: Path, n_lines: int, subdir: str = qs.SOURCE_DOCX_SUBDIR) -> None:
    """A source CV whose only table holds n_lines substantive lines."""
    rows = [[f"Alpha record number {i} about example research topics"] for i in range(n_lines)]
    (root / subdir).mkdir(exist_ok=True)
    _make_docx(["Body paragraph of an example curriculum vitae."], tables=[rows]).save(
        root / subdir / "cv.docx")


def _unrelated_entries(root: Path) -> None:
    _write_json(root, "X_entries.json",
                {"entries": [{"element_type": "paragraph", "text": "Completely unrelated example text"}]})


def test_under_extracted_entry_caps_just_under_green(tmp_path):
    _write_json(tmp_path, "X_fields.json", {"entries": [_big_multi_record_entry(12)]})
    fraction, detail, cap = qs.score_under_extracted_records(tmp_path)
    assert cap == qs.BAND_GREEN - 1 == qs.CONTENT_LOSS_CAP
    assert "under_extraction_findings=1" in detail


def test_a_well_extracted_big_entry_does_not_cap(tmp_path):
    _write_json(tmp_path, "X_fields.json", {"entries": [_big_multi_record_entry(90)]})
    assert qs.score_under_extracted_records(tmp_path)[2] is None


def test_under_extraction_gate_is_not_evaluated_without_fields_json(tmp_path):
    fraction, detail, cap = qs.score_under_extracted_records(tmp_path)
    assert cap is None
    assert "not evaluated" in detail


def test_one_fused_entry_does_not_cap_but_the_threshold_count_does(tmp_path):
    _write_json(tmp_path, "X_entries.json", {"entries": [_fused_entry()]})
    _, detail, cap = qs.score_fused_entries(tmp_path)
    assert cap is None and "mega_entries=1" in detail
    _write_json(tmp_path, "X_entries.json", {"entries": [_fused_entry(), _fused_entry()]})
    _, detail, cap = qs.score_fused_entries(tmp_path)
    assert cap == qs.CONTENT_LOSS_CAP and "mega_entries=2" in detail


def test_fused_header_and_break_entries_are_not_counted(tmp_path):
    _write_json(tmp_path, "X_entries.json", {"entries": [
        _fused_entry("header"), _fused_entry("break"), _fused_entry()]})
    assert qs.score_fused_entries(tmp_path)[2] is None


def test_fused_entries_gate_is_not_evaluated_without_entries_json(tmp_path):
    fraction, detail, cap = qs.score_fused_entries(tmp_path)
    assert cap is None
    assert "not evaluated" in detail


def test_a_lost_source_table_caps_at_the_line_floor(tmp_path):
    _unrelated_entries(tmp_path)
    _source_docx_with_table(tmp_path, qs.LOST_TABLE_CAP_MIN_LINES)
    _, detail, cap = qs.score_lost_source_table(tmp_path)
    assert cap == qs.CONTENT_LOSS_CAP
    assert f"worst_lost_table_lines={qs.LOST_TABLE_CAP_MIN_LINES}" in detail


def test_a_lost_table_below_the_line_floor_does_not_cap(tmp_path):
    _unrelated_entries(tmp_path)
    _source_docx_with_table(tmp_path, qs.LOST_TABLE_CAP_MIN_LINES - 1)
    _, detail, cap = qs.score_lost_source_table(tmp_path)
    assert cap is None
    assert f"worst_lost_table_lines={qs.LOST_TABLE_CAP_MIN_LINES - 1}" in detail


def test_the_worst_of_several_lost_tables_decides_the_cap(tmp_path):
    _unrelated_entries(tmp_path)
    big = qs.LOST_TABLE_CAP_MIN_LINES + 2
    small = [[f"Beta entry number {i} about example teaching topics"]
             for i in range(qs.LOST_TABLE_CAP_MIN_LINES - 1)]
    large = [[f"Alpha record number {i} about example research topics"] for i in range(big)]
    (tmp_path / qs.SOURCE_DOCX_SUBDIR).mkdir()
    _make_docx(["Body paragraph of an example curriculum vitae."], tables=[small, large]).save(
        tmp_path / qs.SOURCE_DOCX_SUBDIR / "cv.docx")
    _, detail, cap = qs.score_lost_source_table(tmp_path)
    assert cap == qs.CONTENT_LOSS_CAP
    assert f"worst_lost_table_lines={big}" in detail


def test_a_source_table_that_stage_2_kept_does_not_cap(tmp_path):
    n = qs.LOST_TABLE_CAP_MIN_LINES + 3
    _write_json(tmp_path, "X_entries.json", {"entries": [
        {"element_type": "table_row", "text": f"Alpha record number {i} about example research topics"}
        for i in range(n)]})
    _source_docx_with_table(tmp_path, n)
    _, detail, cap = qs.score_lost_source_table(tmp_path)
    assert cap is None and "worst_lost_table_lines=0" in detail


def test_lost_table_gate_is_not_evaluated_without_a_source_docx(tmp_path):
    _unrelated_entries(tmp_path)
    fraction, detail, cap = qs.score_lost_source_table(tmp_path)
    assert cap is None
    assert "not evaluated" in detail


def test_lost_table_gate_ignores_an_unreadable_source(tmp_path, caplog):
    _unrelated_entries(tmp_path)
    (tmp_path / qs.SOURCE_DOCX_SUBDIR).mkdir()
    (tmp_path / qs.SOURCE_DOCX_SUBDIR / "cv.docx").write_bytes(b"not a zip")
    with caplog.at_level(logging.WARNING):
        _, detail, cap = qs.score_lost_source_table(tmp_path)
    assert cap is None and "not evaluated" in detail
    # The scored dir sits right after the label, not only inside the exception text.
    assert f"could not read source docx {tmp_path / qs.SOURCE_DOCX_SUBDIR / 'cv.docx'} (" in caplog.text


def test_lost_table_gate_ignores_an_ambiguous_source(tmp_path, caplog):
    """Two readable originals: either alone would cap, so taking the first (or
    any) of them would fire; the gate must refuse to guess."""
    _unrelated_entries(tmp_path)
    _source_docx_with_table(tmp_path, qs.LOST_TABLE_CAP_MIN_LINES)
    rows = [[f"Alpha record number {i} about example research topics"]
            for i in range(qs.LOST_TABLE_CAP_MIN_LINES)]
    _make_docx(["Second example curriculum vitae."], tables=[rows]).save(
        tmp_path / qs.SOURCE_DOCX_SUBDIR / "second.docx")
    with caplog.at_level(logging.WARNING):
        _, detail, cap = qs.score_lost_source_table(tmp_path)
    assert cap is None and "not evaluated" in detail
    assert "found multiple source docx" in caplog.text
    assert str(tmp_path) in caplog.text


def test_content_loss_gates_are_registered_and_weightless():
    """The wire: score_run only runs what CAP_ONLY_GATES lists, and none of them
    may enter DIMENSIONS (a clean run's raw score must not move)."""
    registered = [fn for _, fn in qs.CAP_ONLY_GATES]
    for gate in (qs.score_under_extracted_records, qs.score_fused_entries,
                 qs.score_lost_source_table):
        assert gate in registered
        assert gate not in [fn for _, _, fn in qs.DIMENSIONS]
    assert TOTAL_WEIGHT == 100


def test_score_run_caps_a_green_run_that_lost_records_without_moving_its_raw_score(tmp_path):
    clean = _complete_run_dir(tmp_path)
    before = score_run(clean)
    assert before["raw_score_before_caps"] >= qs.BAND_GREEN, before
    assert before["hard_fail_caps_applied"] == []

    fields = json.loads((clean / "X_fields.json").read_text())
    fields["entries"].append(_big_multi_record_entry(12))
    _write_json(clean, "X_fields.json", fields)
    after = score_run(clean)

    assert after["hard_fail_caps_applied"] == [qs.CONTENT_LOSS_CAP]
    assert after["totalScore"] == qs.CONTENT_LOSS_CAP
    assert after["band"].startswith("YELLOW")
    assert any(f.startswith(f"HARD-FAIL cap={qs.CONTENT_LOSS_CAP}: Source records lost")
               for f in after["flags"]), after["flags"]
    assert [d["score"] for d in after["dimensionScores"]
            if d["name"].startswith("Pipeline/API")] == [25.0]


def test_a_run_already_below_green_keeps_its_lower_score_under_the_content_cap(tmp_path):
    """The cap is a ceiling, not a target: a 75-point run stays 75."""
    clean = _complete_run_dir(tmp_path)
    _write_json(clean, "X_classified.json", _classified(
        total_entries=4, duplicate_entries=3, code_distribution={"A": 1, "T": 3}))
    _write_json(clean, "X_fields.json", {
        "cv_owner": {"full_name": "Jane Q. Public"},
        "cv_owner_location": {"inference_success": True, "primary_location": "NY"},
        "entries": [{"extracted_fields": {"email": "j@x.org"}, "extraction_success": True},
                    _big_multi_record_entry(12)]})
    result = score_run(clean)
    assert result["hard_fail_caps_applied"] == [qs.CONTENT_LOSS_CAP]
    assert result["raw_score_before_caps"] < qs.CONTENT_LOSS_CAP, result
    assert result["totalScore"] == round(result["raw_score_before_caps"])


# ------------------------------------------------ #822 owner-cut citations gate
# Invented authors and titles. Stage 5d's "first 6 authors, et al." cut drops
# the owner, who is eighth on every source list.

_CUT_OWNER = {"full_name": "Rowan Thornquist", "first_name": "Rowan",
              "last_name": "Thornquist"}
_CUT_KEPT = "Ashdown A, Brimley B, Corwen C, Dunmore D, Elsworth E, Fenwick F"
_CUT_TRAILER = "J Synth Geol. 2019;12(3):45-67."
_CUT_TITLES = ("Tidal patterns in synthetic estuary sediment cores",
               "Seasonal drift of invented glacier meltwater channels",
               "Mineral banding in fictional basalt columns",
               "Wind erosion of imaginary coastal dune ridges")


def _owner_cut_run(tmp_path: Path, cut: int, kept: int = 0) -> Path:
    """A run whose bibliography renders `cut` citations without the owner and
    `kept` more with them."""
    authors = f"{_CUT_KEPT}, Garrow G, Thornquist R"
    titles = _CUT_TITLES[:cut + kept]
    _write_json(tmp_path, "X_fields.json", {
        "cv_owner": _CUT_OWNER,
        "cv_owner_location": {"inference_success": True, "primary_location": "NY"},
        "entries": [{"element_idx_start": 10 + n, "taxonomy_code": "S1",
                     "text": f"{authors}. {title}. {_CUT_TRAILER}",
                     "extracted_fields": {"authors": authors}, "extraction_success": True}
                    for n, title in enumerate(titles)]})
    lines = [f"{_CUT_KEPT}, et al. {title}. {_CUT_TRAILER}" for title in titles[:cut]]
    lines += [f"{authors}. {title}. {_CUT_TRAILER}" for title in titles[cut:]]
    _make_docx(["BIBLIOGRAPHY", "Peer-reviewed Research Articles:"]
               + [f"{n}. {line}" for n, line in enumerate(lines, 1)]).save(tmp_path / "X_wcm.docx")
    return tmp_path


def test_owner_missing_gate_caps_at_the_minimum_count(tmp_path):
    assert qs.OWNER_MISSING_CITATIONS_CAP_MIN == 3
    assert qs.score_owner_missing_from_citation(_owner_cut_run(tmp_path, cut=3)) == (
        1.0, f"owner_missing_citations=3; cap={qs.CONTENT_LOSS_CAP}", qs.CONTENT_LOSS_CAP)


def test_owner_missing_gate_quiet_below_the_minimum_count(tmp_path):
    """Two cut citations and one that keeps the owner: the finding reports, the
    gate does not cap."""
    assert qs.score_owner_missing_from_citation(_owner_cut_run(tmp_path, cut=2, kept=1)) == (
        0.0, "owner_missing_citations=2", None)


def test_owner_missing_gate_not_evaluated_without_its_artifacts(tmp_path):
    fraction, detail, cap = qs.score_owner_missing_from_citation(tmp_path)
    assert (fraction, cap) == (0.0, None) and "no fields.json found; not evaluated" == detail
    _owner_cut_run(tmp_path, cut=3)
    (tmp_path / "X_wcm.docx").unlink()
    assert qs.score_owner_missing_from_citation(tmp_path) == (
        0.0, "no docx found; not evaluated", None)


def test_score_run_caps_a_run_whose_owner_was_cut_from_three_citations(tmp_path):
    """The wire: registered in CAP_ONLY_GATES, weightless, and its flag names it."""
    assert qs.score_owner_missing_from_citation in [fn for _, fn in qs.CAP_ONLY_GATES]
    assert qs.score_owner_missing_from_citation not in [fn for _, _, fn in qs.DIMENSIONS]
    _complete_run_dir(tmp_path)
    result = score_run(_owner_cut_run(tmp_path, cut=3))
    assert result["hard_fail_caps_applied"] == [qs.CONTENT_LOSS_CAP]
    assert any(f.startswith(f"HARD-FAIL cap={qs.CONTENT_LOSS_CAP}: CV owner cut from their own "
                            "citations") for f in result["flags"]), result["flags"]


# ------------------------------------------- #1259 co-authors cut to "et al."
# The same invented owner, now first on every source list, so the cut keeps
# them and drops two co-authors: etal_added's shape, not owner_missing's.

_ETAL_AUTHORS = f"Thornquist R, {_CUT_KEPT}, Garrow G"
_ETAL_KEPT = "Thornquist R, Ashdown A, Brimley B, Corwen C, Dunmore D, Elsworth E"


def _etal_cut_run(tmp_path: Path, cut: int, kept: int = 0) -> Path:
    """A run whose bibliography renders `cut` citations cut to six authors and
    "et al." and `kept` more with every author."""
    titles = _CUT_TITLES[:cut + kept]
    _write_json(tmp_path, "X_fields.json", {
        "cv_owner": _CUT_OWNER,
        "cv_owner_location": {"inference_success": True, "primary_location": "NY"},
        "entries": [{"element_idx_start": 10 + n, "taxonomy_code": "S1",
                     "text": f"{_ETAL_AUTHORS}. {title}. {_CUT_TRAILER}",
                     "extracted_fields": {"authors": _ETAL_AUTHORS}, "extraction_success": True}
                    for n, title in enumerate(titles)]})
    lines = [f"{_ETAL_KEPT}, et al. {title}. {_CUT_TRAILER}" for title in titles[:cut]]
    lines += [f"{_ETAL_AUTHORS}. {title}. {_CUT_TRAILER}" for title in titles[cut:]]
    _make_docx(["BIBLIOGRAPHY", "Peer-reviewed Research Articles:"]
               + [f"{n}. {line}" for n, line in enumerate(lines, 1)]).save(tmp_path / "X_wcm.docx")
    return tmp_path


def test_etal_added_gate_caps_at_the_minimum_count(tmp_path):
    assert qs.ETAL_ADDED_CAP_MIN == 3
    assert qs.score_etal_added(_etal_cut_run(tmp_path, cut=3)) == (
        1.0, f"etal_added_citations=3; cap={qs.CONTENT_LOSS_CAP}", qs.CONTENT_LOSS_CAP)


def test_etal_added_gate_quiet_below_the_minimum_count(tmp_path):
    """Two cut lists and one full one: the finding reports, the gate does not
    cap."""
    assert qs.score_etal_added(_etal_cut_run(tmp_path, cut=2, kept=1)) == (
        0.0, "etal_added_citations=2", None)


def test_etal_added_gate_leaves_an_owner_cut_to_the_owner_gate(tmp_path):
    """Three cut lists that also lost the owner are owner_missing's findings,
    not etal_added's: the two gates never count one citation twice."""
    _owner_cut_run(tmp_path, cut=3)
    assert qs.score_etal_added(tmp_path) == (0.0, "etal_added_citations=0", None)


def test_etal_added_gate_not_evaluated_without_its_artifacts(tmp_path):
    assert qs.score_etal_added(tmp_path) == (0.0, "no fields.json found; not evaluated", None)
    _etal_cut_run(tmp_path, cut=3)
    (tmp_path / "X_wcm.docx").unlink()
    assert qs.score_etal_added(tmp_path) == (0.0, "no docx found; not evaluated", None)


def test_score_run_caps_a_run_with_three_cut_author_lists(tmp_path):
    """The wire: registered in CAP_ONLY_GATES, weightless, and its flag names it."""
    assert qs.score_etal_added in [fn for _, fn in qs.CAP_ONLY_GATES]
    assert qs.score_etal_added not in [fn for _, _, fn in qs.DIMENSIONS]
    _complete_run_dir(tmp_path)
    result = score_run(_etal_cut_run(tmp_path, cut=3))
    assert result["hard_fail_caps_applied"] == [qs.CONTENT_LOSS_CAP], result["flags"]
    assert result["totalScore"] <= qs.CONTENT_LOSS_CAP
    assert any(f.startswith(f"HARD-FAIL cap={qs.CONTENT_LOSS_CAP}: Co-authors cut from citations")
               for f in result["flags"]), result["flags"]


def test_score_run_does_not_cap_two_cut_author_lists(tmp_path):
    _complete_run_dir(tmp_path)
    result = score_run(_etal_cut_run(tmp_path, cut=2, kept=1))
    assert result["hard_fail_caps_applied"] == [], result["flags"]


# -------------------------- zero-false-positive WARN lints into the content cap
# grant_boundary (#1226), grant_bucket's application shape (#1343) and
# junk_or_header_row (EBYSBC E8/E10/E29). Invented grants and institutions.

#: A named owner with a location, so the owner gate does not cap these runs.
_OWNED_RUN = {"cv_owner": {"full_name": "Jane Q. Public"},
              "cv_owner_location": {"inference_success": True, "primary_location": "NY"}}

_WHOLE_GRANT = {"agency": "Example Fund", "grant_number": "R01 XX000001",
                "title": "Study of example things", "start_date": "2001", "end_date": "2004"}


def _shifted_grants_run(tmp_path: Path, shifted: int) -> Path:
    """A grant list with `shifted` entries that open with the PI line, which
    closes a record: the shape a slipped stage-2 cut leaves."""
    entries = [{"element_idx_start": 300 + 2 * n, "taxonomy_code": "M2B",
                "hierarchy": ["Research Support", "Past"],
                "text": f"PI: A. Person\t2001-2004\tExample Fund\tStudy {n}",
                "extracted_fields": dict(_WHOLE_GRANT)} for n in range(shifted)]
    _write_json(tmp_path, "X_fields.json", {**_OWNED_RUN, "entries": entries})
    return tmp_path


def test_grant_boundary_gate_caps_at_the_minimum_count(tmp_path):
    assert qs.GRANT_BOUNDARY_CAP_MIN == 3
    assert qs.score_grant_boundary(_shifted_grants_run(tmp_path, 3)) == (
        1.0, f"grant_boundary_findings=3; cap={qs.CONTENT_LOSS_CAP}", qs.CONTENT_LOSS_CAP)


def test_grant_boundary_gate_quiet_below_the_minimum_count(tmp_path):
    """Two flagged grants report in the doctor but do not cap."""
    assert qs.score_grant_boundary(_shifted_grants_run(tmp_path, 2)) == (
        0.0, "grant_boundary_findings=2", None)


def test_grant_boundary_gate_not_evaluated_without_fields(tmp_path):
    assert qs.score_grant_boundary(tmp_path) == (
        0.0, "no fields.json found; not evaluated", None)


_GRANT_TEXT = "Example Research Foundation Study of Example Longevity Measures Program"
_FUNDING_TITLE = dict(_FUNDING_SECTIONS)


def _grant_rendered_run(tmp_path: Path, heading: list[str], code: str, rendered_under: str,
                        **fields) -> Path:
    """One grant filed under `heading`, rendered in the `rendered_under`
    funding subsection."""
    _write_json(tmp_path, "X_fields.json", {**_OWNED_RUN, "entries": [
        {"element_idx_start": 200, "taxonomy_code": code, "hierarchy": heading,
         "text": _GRANT_TEXT, "extracted_fields": fields}]})
    _make_docx([_FUNDING_TITLE[rendered_under].title(), _GRANT_TEXT]).save(tmp_path / "X_wcm.docx")
    return tmp_path


def test_grant_application_gate_caps_on_one_application_rendered_as_an_award(tmp_path):
    assert qs.GRANT_APPLICATION_AS_AWARD_CAP_MIN == 1
    run = _grant_rendered_run(tmp_path, ["Grants Applied"], "M2B", "M2B")
    assert qs.score_grant_application_as_award(run) == (
        1.0, f"applications_rendered_as_awards=1; cap={qs.CONTENT_LOSS_CAP}",
        qs.CONTENT_LOSS_CAP)


def test_grant_application_gate_spares_an_application_rendered_under_pending(tmp_path):
    run = _grant_rendered_run(tmp_path, ["Grants Applied"], "M2C", "M2C")
    assert qs.score_grant_application_as_award(run) == (
        0.0, "applications_rendered_as_awards=0", None)


def test_grant_application_gate_does_not_cap_on_an_ended_current_grant(tmp_path):
    """The lint flags a Current grant whose end date has passed; the gate does
    not read that shape, which depends on the day the run is rescored."""
    run = _grant_rendered_run(tmp_path, ["Research Support", "Ongoing"], "M2A", "M2A",
                              start_date="1990", end_date="2001")
    fields = json.loads((run / "X_fields.json").read_text())
    blocks = docx_body_blocks(Document(str(run / "X_wcm.docx")))
    assert len(lint_grant_bucket(fields, blocks)) == 1
    assert qs.score_grant_application_as_award(run) == (
        0.0, "applications_rendered_as_awards=0", None)


def test_grant_application_gate_not_evaluated_without_its_artifacts(tmp_path):
    assert qs.score_grant_application_as_award(tmp_path) == (
        0.0, "no fields.json found; not evaluated", None)
    _grant_rendered_run(tmp_path, ["Grants Applied"], "M2B", "M2B")
    (tmp_path / "X_wcm.docx").unlink()
    assert qs.score_grant_application_as_award(tmp_path) == (
        0.0, "no docx found; not evaluated", None)


_JUNK_INSTITUTIONS = ("Example State University", "Sample Valley College",
                      "Placeholder Institute of Studies", "Fictional Medical School",
                      "Imaginary Coast University")


def _junk_rows_run(tmp_path: Path, headers: int) -> Path:
    """A course list whose first `headers` institution lines each render as a
    row of their own, with one real course under each. The course rows name
    their institution, so `group_header_context` does not also fire."""
    entries, rows = [], []
    for n, name in enumerate(_JUNK_INSTITUTIONS[:headers]):
        course = f"EX10{n} Widget Studies"
        entries += [{"element_idx_start": 40 + 2 * n, "taxonomy_code": "K1",
                     "hierarchy": ["Example Heading"], "text": name,
                     "extracted_fields": {"institution": name}},
                    {"element_idx_start": 41 + 2 * n, "taxonomy_code": "K1",
                     "hierarchy": ["Example Heading"], "text": course,
                     "extracted_fields": {"course_title": course}}]
        rows += [[name, ""], [course, name]]
    _write_json(tmp_path, "X_fields.json", {**_OWNED_RUN, "entries": entries})
    _make_docx(tables=[rows]).save(tmp_path / "X_wcm.docx")
    return tmp_path


def test_junk_rows_gate_caps_at_the_minimum_count(tmp_path):
    assert qs.JUNK_ROWS_CAP_MIN == 5
    assert qs.score_junk_rows(_junk_rows_run(tmp_path, 5)) == (
        1.0, f"junk_or_header_rows=5; cap={qs.CONTENT_LOSS_CAP}", qs.CONTENT_LOSS_CAP)


def test_junk_rows_gate_quiet_below_the_minimum_count(tmp_path):
    assert qs.score_junk_rows(_junk_rows_run(tmp_path, 4)) == (
        0.0, "junk_or_header_rows=4", None)


def test_junk_rows_gate_not_evaluated_without_its_artifacts(tmp_path):
    assert qs.score_junk_rows(tmp_path) == (0.0, "no fields.json found; not evaluated", None)
    _junk_rows_run(tmp_path, 5)
    (tmp_path / "X_wcm.docx").unlink()
    assert qs.score_junk_rows(tmp_path) == (0.0, "no docx found; not evaluated", None)


_GROUP_ROLES = ("Module Director", "Workshop Coordinator", "Session Leader",
                "Course Tutor", "Panel Moderator")


def _group_header_run(tmp_path: Path, runs: int, lead_lines: int = 0) -> Path:
    """`runs` course lines, each over one bare role rendered as the role
    alone (one `role_without_holder` WARN each), then `lead_lines` undated
    lead lines over a dated list of another letter, each rendered as a row
    of its own (one `header_coded_unlike_list` INFO each)."""
    entries, paragraphs, rows = [], [], []
    for n, role in enumerate(_GROUP_ROLES[:runs]):
        year = str(1961 + n)
        entries += [{"element_idx_start": 60 + 2 * n, "taxonomy_code": "K1",
                     "hierarchy": ["Example Heading"], "text": f"Widget Course {n} 1950-present",
                     "extracted_fields": {"course_title": f"Widget Course {n}",
                                          "start_date": "1950", "end_date": "present"}},
                    {"element_idx_start": 61 + 2 * n, "taxonomy_code": "K3",
                     "hierarchy": ["Example Heading"], "text": f"{role} {year}",
                     "extracted_fields": {"role": role, "start_date": year}}]
        paragraphs.append(f"{year} - {role}")
    for n in range(lead_lines):
        lead = f"Thesis Committees, Example College {n}"
        entries.append({"element_idx_start": 100 + 10 * n, "taxonomy_code": "K2",
                        "hierarchy": ["Other Heading"], "text": lead,
                        "extracted_fields": {"institution": f"Example College {n}",
                                             "teaching_role": "Thesis Committee Member"}})
        entries += [{"element_idx_start": 101 + 10 * n + m, "taxonomy_code": "N3B",
                     "hierarchy": ["Other Heading"], "text": f"1976-1979 Widget Student {m}",
                     "extracted_fields": {"mentee_name": f"Widget Student {m}",
                                          "start_date": "1976", "end_date": "1979"}}
                    for m in range(3)]
        rows.append([lead, ""])
    _write_json(tmp_path, "X_fields.json", {**_OWNED_RUN, "entries": entries})
    _make_docx(paragraphs, tables=[rows] if rows else []).save(tmp_path / "X_wcm.docx")
    return tmp_path


def test_group_header_gate_caps_at_the_minimum_count(tmp_path):
    assert qs.GROUP_HEADER_CAP_MIN == 4
    run = _group_header_run(tmp_path, qs.GROUP_HEADER_CAP_MIN)
    assert qs.score_group_header_context(run) == (
        1.0, f"group_header_warns=4; cap={qs.CONTENT_LOSS_CAP}", qs.CONTENT_LOSS_CAP)


def test_group_header_gate_quiet_below_the_minimum_count(tmp_path):
    run = _group_header_run(tmp_path, qs.GROUP_HEADER_CAP_MIN - 1)
    assert qs.score_group_header_context(run) == (0.0, "group_header_warns=3", None)


def test_group_header_gate_counts_only_warn_findings(tmp_path):
    """The INFO shapes (a lead line coded unlike its list, a role that lost a
    block's dates) are reported by the doctor but do not count toward the cap."""
    run = _group_header_run(tmp_path, qs.GROUP_HEADER_CAP_MIN - 1, lead_lines=2)
    fields = json.loads((run / "X_fields.json").read_text())
    doc = Document(str(run / "X_wcm.docx"))
    severities = sorted(f["severity"] for f in lint_group_header_context(
        fields, docx_table_rows(doc), docx_body_blocks(doc)))
    assert severities == ["INFO", "INFO", "WARN", "WARN", "WARN"]
    assert qs.score_group_header_context(run) == (0.0, "group_header_warns=3", None)


def test_group_header_gate_not_evaluated_without_its_artifacts(tmp_path):
    assert qs.score_group_header_context(tmp_path) == (
        0.0, "no fields.json found; not evaluated", None)
    _group_header_run(tmp_path, 4)
    (tmp_path / "X_wcm.docx").unlink()
    assert qs.score_group_header_context(tmp_path) == (0.0, "no docx found; not evaluated", None)


@pytest.mark.parametrize("gate, flag, build", [
    (qs.score_grant_boundary, "Grant details shifted between grants",
     lambda d: _shifted_grants_run(d, 3)),
    (qs.score_grant_application_as_award, "Grant applications rendered as awards",
     lambda d: _grant_rendered_run(d, ["Grants Applied"], "M2B", "M2B")),
    (qs.score_junk_rows, "Headers or labels rendered as records",
     lambda d: _junk_rows_run(d, 5)),
    (qs.score_group_header_context, "Rows lost the group header above them",
     lambda d: _group_header_run(d, 4)),
])
def test_score_run_applies_each_zero_fp_lint_gate(tmp_path, gate, flag, build):
    """The wire: registered in CAP_ONLY_GATES, weightless, and score_run
    applies its cap under a flag that names it."""
    assert gate in [fn for _, fn in qs.CAP_ONLY_GATES]
    assert gate not in [fn for _, _, fn in qs.DIMENSIONS]
    _complete_run_dir(tmp_path)
    result = score_run(build(tmp_path))
    assert result["hard_fail_caps_applied"] == [qs.CONTENT_LOSS_CAP], result["flags"]
    assert result["totalScore"] <= qs.CONTENT_LOSS_CAP
    assert any(f.startswith(f"HARD-FAIL cap={qs.CONTENT_LOSS_CAP}: {flag}")
               for f in result["flags"]), result["flags"]


# ------------------------------------------------ #822 raw-tab deduction limit

@pytest.mark.parametrize("tabs, expected_fraction", [
    (4, 0.12),    # under the limit: 0.6 * 4 / 20, unchanged
    (10, 0.3),    # the limit itself
    (40, 0.3),    # VVRTUC's shape: was clamp(1.2) = all 10 points
])
def test_broken_format_raw_tabs_cost_at_most_three_points(tmp_path, tabs, expected_fraction):
    _make_docx([f"line {n}\twith a raw tab" for n in range(tabs)]).save(tmp_path / "out.docx")
    fraction, detail, _ = score_broken_format(tmp_path)
    assert f"raw_tab_paragraphs={tabs}" in detail
    assert fraction == pytest.approx(expected_fraction)
    assert fraction * dict((s, w) for _, w, s in DIMENSIONS)[score_broken_format] <= 3


def test_broken_format_echoes_still_add_on_top_of_the_tab_limit(tmp_path):
    """The limit is on tabs only: 40 tabs plus 15 instruction echoes is 3 + 4 points."""
    echo = "Please list here the courses taught"
    _make_docx([f"line {n}\twith a raw tab" for n in range(40)]
               + [f"{echo} {n}" for n in range(15)]).save(tmp_path / "out.docx")
    fraction, detail, _ = score_broken_format(tmp_path)
    assert "echo_paragraphs=15" in detail
    assert fraction == pytest.approx(0.7)
