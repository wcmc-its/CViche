"""Regression guard for the PR #724 review round: contract tests for
quality_score.py's ambiguous-artifact guard, pipeline-error accounting, gate
mode validation, metadata-invariant validation, the narrowed _load_first
exception handling, and linear_interp's clamp.

    python3 -m pytest src/unified_pipeline/tests/test_quality_score_contracts.py -p no:cacheprovider

Self-contained: no DB, no network, no PII. All artifacts are synthetic tmp
files.
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
    _load_first,
    linear_interp,
    quality_gate,
    score_cv_owner,
    score_duplicate_ratio,
    score_pipeline_errors,
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


@pytest.mark.parametrize("mode", sorted(VALID_GATE_MODES))
def test_quality_gate_accepts_every_valid_mode(tmp_path, mode):
    # Directory is empty; only asserting the mode itself does not raise.
    result = quality_gate(tmp_path, mode=mode)
    assert result["gate_mode"] == mode


def test_band_for_boundaries():
    from unified_pipeline.quality_score import band_for
    assert band_for(85) == "GREEN (ship)"
    assert band_for(60) == "YELLOW (human cleanup needed)"
    assert band_for(59) == "RED (re-run / do-not-deliver)"


# --------------------------------------------------------------------- D5
# FATAL_ERROR_PATTERN widened for ValidationException etc (#724 review T2.5)
# --------------------------------------------------------------------- D5


@pytest.mark.parametrize("text", [
    "ValidationException: field required",
    "TypeError: unsupported operand",
    "AttributeError: 'NoneType' object has no attribute 'x'",
    # Pre-existing branches must keep matching.
    "name 'response' is not defined",
    "Traceback (most recent call last):",
    "NameError: name 'x' is not defined",
])
def test_fatal_error_pattern_matches_exception_type_names(text):
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
