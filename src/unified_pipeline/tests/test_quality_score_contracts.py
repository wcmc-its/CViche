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
    CAP_ONLY_GATES,
    DIMENSIONS,
    FATAL_ERROR_PATTERN,
    SCORED_ARTIFACT_COUNT,
    TOTAL_WEIGHT,
    FallbackServedCall,
    _load_docx,
    _load_first,
    band_for,
    linear_interp,
    llm_fallback_served,
    missing_evidence,
    no_output_produced,
    prompt_log_fallback_served,
    quality_gate,
    score_cv_owner,
    score_no_output,
    score_pipeline_errors,
    score_run,
    score_stage3b_fallback_ratio,
    score_stage4_group_failures,
    stage4_group_failures,
)
from unified_pipeline.stage4.error_codes import (  # noqa: E402
    LLM_RESPONSE_INVALID,
    LLM_TIMEOUT,
    NO_MATCHING_EXTRACTION,
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


# D14 (T3.2): duplicate_entries > total_entries, duplicate_entries < 0,
# total_entries < 0, and total_entries != sum(code_distribution.values())
# are covered by the D10 tests above (test_duplicate_ratio_negative_*,
# test_duplicate_ratio_duplicate_exceeds_total,
# test_t_bucket_negative_total_entries,
# test_t_bucket_code_distribution_disagrees_with_total_entries).


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
    falls to its own 'absent' case. Three caps fire -- no doctor report (84,
    #1595), cv_owner (25, no fields.json) and #745's no_output (20, no docx at
    all) -- and the lowest wins: 'nothing was produced' is more severe than
    'produced something with no name in it'."""
    result = score_run(tmp_path)
    assert result["totalScore"] == 20
    assert result["band"].startswith("RED")
    assert result["hard_fail_caps_applied"] == [84, 25, 20]


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
    _write_json(tmp_path, "X_doctor.json", {"findings": []})
    _make_docx(["clean"], tables=[[["a", "b"]]]).save(tmp_path / "X_wcm.docx")
    return tmp_path


def test_missing_evidence_empty_when_every_artifact_loads(tmp_path):
    assert missing_evidence(_complete_run_dir(tmp_path)) == []


def test_missing_evidence_names_every_absent_artifact_in_order(tmp_path):
    assert missing_evidence(tmp_path) == [
        "no fields.json found",
        "no classified.json found",
        "no entries.json found",
        "no doctor report found",
        "docx: no docx found",
    ]
    assert SCORED_ARTIFACT_COUNT == 5


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
        "no entries.json found", "no doctor report found", "docx: no docx found",
    ]
    assert result["flags"][-1] == (
        "EVIDENCE INCOMPLETE (5 of 5 artifacts): no fields.json found; "
        "no classified.json found; no entries.json found; no doctor report found; "
        "docx: no docx found")
    assert any(f.startswith("HARD-FAIL cap=25") for f in result["flags"]), result["flags"]


def test_score_run_one_missing_artifact_counts_one_of_five(tmp_path):
    _complete_run_dir(tmp_path)
    (tmp_path / "X_classified.json").unlink()
    result = score_run(tmp_path)
    assert result["data_complete"] is False
    assert result["missing_evidence"] == ["no classified.json found"]
    assert result["flags"] == [
        "No hard-fail caps triggered",
        "EVIDENCE INCOMPLETE (1 of 5 artifacts): no classified.json found",
    ]


def test_quality_gate_carries_data_complete_through(tmp_path):
    result = quality_gate(_complete_run_dir(tmp_path), mode="advisory")
    assert result["data_complete"] is True and result["missing_evidence"] == []


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


def test_score_run_keeps_a_run_with_a_failed_stage4_group_out_of_green(tmp_path):
    """End to end through score_run (the gate is dispatched from CAP_ONLY_GATES,
    which the scorer-level tests above never touch). The fixture run scores
    100 GREEN, so a cap of 84 is visible; the same run with one rescued
    failed group must differ in the cap and nothing else."""
    clean = score_run(_complete_run_dir(tmp_path))
    assert (clean["totalScore"], clean["band"]) == (100, "GREEN (ship)")

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


def test_weighted_penalties_alone_never_reach_red():
    """A dimension of weight w loses exactly w * fraction points, with no
    normalization, and the weights sum to the points between 100 and the
    YELLOW line (#1595): only a hard-fail cap makes a run RED."""
    assert TOTAL_WEIGHT == 100 - qs.BAND_YELLOW


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
    reply parsed, so the score ignores it. The same GREEN run with a served
    stage-4 group and a served stage-4.5 call scores exactly the same."""
    for name in ("clean", "served"):
        (tmp_path / name).mkdir()
    clean = score_run(_complete_run_dir(tmp_path / "clean"))
    assert (clean["totalScore"], clean["band"]) == (100, "GREEN (ship)")

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


# ------------------------------------------- #1259 co-authors cut to "et al."
# The same invented owner, now first on every source list, so the cut keeps
# them and drops two co-authors: etal_added's shape, not owner_missing's.

_ETAL_AUTHORS = f"Thornquist R, {_CUT_KEPT}, Garrow G"
_ETAL_KEPT = "Thornquist R, Ashdown A, Brimley B, Corwen C, Dunmore D, Elsworth E"


# -------------------------- zero-false-positive WARN lints into the content cap
# grant_boundary (#1226), grant_bucket's application shape (#1343) and
# junk_or_header_row (EBYSBC E8/E10/E29). Invented grants and institutions.


# ------------------------------------------------ #822 raw-tab deduction limit


def test_no_output_is_not_the_same_failure_as_ambiguous_or_corrupt(tmp_path):
    """no_docx_produced is deliberately narrower than 'doc is None':
    'more than one docx' and 'a docx exists but won't parse' are different
    failures, which missing_evidence names, not this gate."""
    _garbage(tmp_path)  # a present-but-corrupt "docx"
    fraction, _detail, cap = score_no_output(tmp_path)
    assert fraction == 0.0 and cap is None

    _make_docx(["a"]).save(tmp_path / "a.docx")
    _make_docx(["b"]).save(tmp_path / "b.docx")
    fraction, _detail, cap = score_no_output(tmp_path)
    assert fraction == 0.0 and cap is None


def test_stage3b_fallback_ratio_quiet_when_classified_json_is_absent(tmp_path):
    """No classified.json at all (an incomplete run) must not crash or
    false-positive: this dimension is a pure gate (weight 0)."""
    fraction, detail, cap = score_stage3b_fallback_ratio(tmp_path)
    assert fraction == 0.0 and cap is None
    assert "no classified.json found" in detail


def test_stage4_group_failures_ignores_a_per_entry_miss_in_a_successful_call():
    """NO_MATCHING_EXTRACTION is not a failed call: the group's reply simply
    held no item for this entry."""
    entries = [{"taxonomy_code": "A1", "extraction_success": False,
                "extraction_error": NO_MATCHING_EXTRACTION, "extracted_fields": {}}]
    assert stage4_group_failures(_stage4_artifact(entries, failed_batches=0)) is None


# --------------------------------------------------------------------- #1595
# The doctor-findings dimension: the score is built from the doctor's own
# findings, each weighted by its lint's precision and its fix minutes.
# --------------------------------------------------------------------- #1595


def _ledger(**precisions):
    """A stand-in for the gate's ledger (`precision.load_gate_ledger`):
    lint -> (true positives, judged), one lint-level row each."""
    from unified_pipeline.doctor.precision import LintPrecision
    return lambda: {(lint, None): LintPrecision(lint, tp, judged, "test")
                    for lint, (tp, judged) in precisions.items()}


def _finding(lint, severity="WARN", **extra):
    return {"lint": lint, "severity": severity, "message": "m", "status": "ran", **extra}


def test_every_doctor_lint_states_its_fix_minutes():
    """A new lint must say what its finding costs, or the score prices it at
    the default without anyone deciding so."""
    from unified_pipeline.run_doctor import KNOWN_LINTS
    assert set(KNOWN_LINTS) - set(qs.LINT_FIX_MINUTES) == set()


def test_a_findings_cost_is_precision_times_severity_times_fix_minutes(monkeypatch):
    monkeypatch.setattr(qs, "load_gate_ledger", _ledger(multi_record_coverage=(3, 4)))
    costs = qs.estimate_cleanup([_finding("multi_record_coverage")] * 2)
    assert [(c.lint, c.findings, c.precision) for c in costs] == [("multi_record_coverage", 2, 0.75)]
    assert costs[0].minutes == pytest.approx(2 * 0.75 * qs.FIX_MINUTES_RECORD_LOST)


@pytest.mark.parametrize("finding", [
    _finding("multi_record_coverage", severity="INFO"),       # a note, not a problem
    _finding("multi_record_coverage", status="skipped"),      # the lint did not run
    _finding("multi_record_coverage", severity="DEBUG"),      # not a doctor severity
    {"severity": "WARN"},                                     # no lint name
    "not a finding",
])
def test_findings_that_name_no_defect_cost_nothing(monkeypatch, finding):
    monkeypatch.setattr(qs, "load_gate_ledger", _ledger(multi_record_coverage=(1, 1)))
    assert qs.estimate_cleanup([finding]) == []


def test_an_error_finding_counts_like_a_warning(monkeypatch):
    monkeypatch.setattr(qs, "load_gate_ledger", _ledger(junk_or_header_row=(1, 1)))
    warn, = qs.estimate_cleanup([_finding("junk_or_header_row")])
    error, = qs.estimate_cleanup([_finding("junk_or_header_row", severity="ERROR")])
    assert error.minutes == warn.minutes == pytest.approx(qs.FIX_MINUTES_DUPLICATE)


def test_an_unmeasured_lint_gets_the_stated_prior(monkeypatch):
    """No verdicts in the ledger (absent, or listed with none judged): the
    prior, never 0 (which would hide the lint) and never 1.

    The expected minutes are literals, not derived from the constant, so a
    change to the prior has to change this test too."""
    assert 0.0 < qs.UNMEASURED_PRECISION_PRIOR < 1.0
    monkeypatch.setattr(qs, "load_gate_ledger", _ledger(missed_headers=(0, 0)))
    # One WARN each: 0.5 prior x 1.0 severity x the lint's fix minutes
    # (missed_headers 0.5, dedup_drops 1.0).
    for lint, expected_minutes in (("missed_headers", 0.25), ("dedup_drops", 0.5)):
        cost, = qs.estimate_cleanup([_finding(lint)])
        assert cost.precision == qs.UNMEASURED_PRECISION_PRIOR, lint
        assert cost.minutes == pytest.approx(expected_minutes), lint


def test_a_findings_weight_is_the_row_of_its_own_shape(monkeypatch):
    """The score reads a finding's precision as the review copy's gate does
    (`finding_precision`): a stage-6 warning reads its shape's row, not the
    lint pooled, and the lint's cost is the mean over its findings."""
    from unified_pipeline.doctor.precision import STAGE6_LINT, LintPrecision
    rows = {(STAGE6_LINT, "reroute_refused"): LintPrecision(STAGE6_LINT, 1, 1, "t", "reroute_refused"),
            (STAGE6_LINT, "appendix_recovered"): LintPrecision(STAGE6_LINT, 1, 5, "t", "appendix_recovered")}
    monkeypatch.setattr(qs, "load_gate_ledger", lambda: rows)
    refused = _finding(STAGE6_LINT, message="stage 6 self-check: reroute of X refused")
    recovered = _finding(STAGE6_LINT, message="stage 6 self-check: 2 entries recovered into the Appendix")
    assert qs.lint_precision_weight(STAGE6_LINT, refused["message"]) == 1.0
    assert qs.lint_precision_weight(STAGE6_LINT, recovered["message"]) == pytest.approx(0.2)
    cost, = qs.estimate_cleanup([refused, recovered])
    assert cost.precision == pytest.approx(0.6)
    assert cost.minutes == pytest.approx(1.2 * qs.LINT_FIX_MINUTES[STAGE6_LINT])


def test_the_score_weights_with_the_gates_held_out_fold():
    """Paul, 2026-10-08: the score's weights are the gate's combined numbers,
    YUY-HO folded in. On the real ledger, a lint whose held-out verdicts were
    folded weighs its combined precision, not its in-sample one."""
    from unified_pipeline.doctor.precision import load_gate_ledger, load_shape_ledger
    in_sample, gate = load_shape_ledger(), load_gate_ledger()
    folded = [key for key, row in gate.items()
              if row.precision is not None and key[1] is None
              and (key not in in_sample or in_sample[key].precision != row.precision)]
    assert folded, "no lint-level row of the gate's ledger differs from the in-sample one"
    for lint, _shape in folded:
        assert qs.lint_precision_weight(lint, "") == pytest.approx(gate[(lint, None)].precision), lint


def test_a_lint_newer_than_this_scorer_costs_the_default_minutes(monkeypatch):
    monkeypatch.setattr(qs, "load_gate_ledger", _ledger(brand_new_lint=(1, 1)))
    cost, = qs.estimate_cleanup([_finding("brand_new_lint")])
    assert cost.minutes == pytest.approx(qs.DEFAULT_FIX_MINUTES)


def test_a_mostly_wrong_lint_counts_less_than_a_precise_one(monkeypatch):
    """#1595's point: year_not_in_source-like lints barely count."""
    monkeypatch.setattr(qs, "load_gate_ledger", _ledger(year_not_in_source=(1, 10), grant_boundary=(10, 10)))
    sloppy, = qs.estimate_cleanup([_finding("year_not_in_source")])
    precise, = qs.estimate_cleanup([_finding("grant_boundary")])
    assert sloppy.minutes == pytest.approx(precise.minutes / 10)


def test_a_process_finding_costs_no_cleanup(monkeypatch):
    monkeypatch.setattr(qs, "load_gate_ledger", _ledger(llm_fallback_served=(1, 1)))
    cost, = qs.estimate_cleanup([_finding("llm_fallback_served")])
    assert cost.minutes == 0.0


@pytest.mark.parametrize("write, detail", [
    (lambda d: None, "no doctor report found"),
    (lambda d: (d / "X_doctor.json").write_text('{"trunc'), "doctor report unreadable"),
    (lambda d: _write_json(d, "X_doctor.json", {"counts": {}}), "doctor report has no findings list"),
    (lambda d: _write_json(d, "X_doctor.json", []), "doctor report has no findings list"),
])
def test_a_run_the_doctor_did_not_check_is_capped_out_of_green(tmp_path, write, detail):
    write(tmp_path)
    fraction, text, cap = qs.score_doctor_findings(tmp_path)
    assert (fraction, cap) == (0.0, qs.NOT_CHECKED_CAP)
    assert text.startswith(detail) and "not checked" in text, text


def test_the_penalty_rises_with_the_estimate_and_never_reaches_the_weight(tmp_path, monkeypatch):
    monkeypatch.setattr(qs, "load_gate_ledger", _ledger(multi_record_coverage=(1, 1)))
    fractions = []
    for n in (0, 1, 5, 500):
        _write_json(tmp_path, "X_doctor.json", {"findings": [_finding("multi_record_coverage")] * n})
        fraction, detail, cap = qs.score_doctor_findings(tmp_path)
        assert cap is None
        fractions.append(fraction)
    assert fractions[0] == 0.0
    assert fractions == sorted(fractions) and len(set(fractions)) == 4
    assert fractions[-1] < 1.0
    assert "estimated_cleanup_minutes=500.0 from 500 findings (multi_record_coverage 500x" in detail


def _run_with_minutes(tmp_path, minutes, monkeypatch):
    """A complete run whose doctor findings cost exactly `minutes`."""
    monkeypatch.setattr(qs, "load_gate_ledger", _ledger(multi_record_coverage=(1, 1)))
    _complete_run_dir(tmp_path)
    n = round(minutes / qs.FIX_MINUTES_RECORD_LOST)
    _write_json(tmp_path, "X_doctor.json", {"findings": [_finding("multi_record_coverage")] * n})
    return score_run(tmp_path)


def test_green_means_at_most_the_green_cleanup_minutes(tmp_path, monkeypatch):
    """The GREEN line sits at GREEN_MAX_CLEANUP_MINUTES: a run at the limit is
    GREEN, a run one finding over it is not."""
    assert qs.GREEN_MAX_CLEANUP_MINUTES == 3.0
    (tmp_path / "at").mkdir()
    (tmp_path / "over").mkdir()
    at = _run_with_minutes(tmp_path / "at", 3, monkeypatch)
    over = _run_with_minutes(tmp_path / "over", 4, monkeypatch)
    assert (at["totalScore"], at["band"]) == (85, "GREEN (ship)")
    assert over["band"].startswith("YELLOW") and over["hard_fail_caps_applied"] == []


def test_no_amount_of_findings_alone_makes_a_run_red(tmp_path, monkeypatch):
    result = _run_with_minutes(tmp_path, 10_000, monkeypatch)
    assert result["totalScore"] == qs.BAND_YELLOW and result["band"].startswith("YELLOW")


def test_score_run_caps_an_unchecked_run_and_marks_it_incomplete(tmp_path):
    """#1593: IXJMKS scored 97 GREEN with no doctor report."""
    _complete_run_dir(tmp_path)
    (tmp_path / "X_doctor.json").unlink()
    result = score_run(tmp_path)
    assert (result["totalScore"], result["hard_fail_caps_applied"]) == (84, [84])
    assert result["data_complete"] is False
    assert result["missing_evidence"] == ["no doctor report found"]


def test_the_doctor_report_is_not_read_as_a_pipeline_error(tmp_path):
    """The report quotes errors it found; scanning it as stage output would
    turn a lint's evidence into a fatal pipeline error."""
    _write_json(tmp_path, "X_doctor.json", {"findings": [
        {"lint": "pipeline_errors_present", "error": "NameError: name 'x' is not defined"}]})
    fraction, detail, cap = score_pipeline_errors(tmp_path)
    assert (fraction, cap) == (0.0, None), detail


def test_the_review_copy_is_never_the_scored_document(tmp_path):
    """#1595 side finding: a stored run dir holds the review copy (#1543) beside
    the document. It must not make the docx evidence ambiguous."""
    _make_docx(["clean"]).save(tmp_path / "X_wcm.docx")
    _make_docx(["clean", "with comments"]).save(tmp_path / f"X{qs.REVIEW_DOCX_SUFFIX}")
    doc, reason = _load_docx(tmp_path)
    assert reason is None and [p.text for p in doc.paragraphs] == ["clean"]
