"""Tests for the #615/#616/#617/#621 segmentation_regression.py fixes.

Each test class below pins one issue. Two cases (bare-string / missing
hierarchy; exact header_titles content) also close out backlog items from
#618 -- noted at the relevant test.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_segmentation_regression_instrument_fixes.py -p no:cacheprovider
"""

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402
from docx import Document  # noqa: E402

from unified_pipeline import segmentation_regression as segreg  # noqa: E402
from unified_pipeline.segmentation_regression import (  # noqa: E402
    Metrics,
    SegmentationRegressionError,
    _load_metrics,
    _snapshot_dir,
    compare_metrics,
    compute_metrics,
    iter_source_lines,
    run_compare,
)


def _metrics(**overrides) -> Metrics:
    """A minimal, fully-populated Metrics dict; tests override only the
    fields they care about."""
    base: Metrics = {
        "source_lines": 10,
        "substantive_lines": 10,
        "text_coverage_pct": 100.0,
        "lost_lines": [],
        "entries_total": 5,
        "entries_content": 5,
        "empty_content": 0,
        "duplicate_entries": 0,
        "mega_entries": 0,
        "max_entry_chars": 100,
        "headers_detected": 2,
        "header_titles": ["education", "awards"],
        "per_h1_content_counts": {},
    }
    base.update(overrides)  # type: ignore[typeddict-item]
    return base


# ------------------------------------------------------------- #615 item 1

def test_iter_source_lines_dedupes_vertically_merged_cell(tmp_path):
    """A 2x1 table with cell(0,0) merged into cell(1,0) must report the
    merged cell's text ONCE. python-docx returns the same underlying _tc
    element for every grid position a vertical merge spans, so an unguarded
    walk counts it once per spanned row.

    Positive control: this exact docx, read by the ORIGINAL (pre-fix)
    iter_source_lines, yields the line TWICE -- confirmed directly against
    origin/dev's copy of this module before writing this test.
    """
    doc = Document()
    table = doc.add_table(rows=2, cols=1)
    table.cell(0, 0).text = "merged section text"
    table.cell(0, 0).merge(table.cell(1, 0))
    path = tmp_path / "vmerge.docx"
    doc.save(path)

    lines = iter_source_lines(str(path))
    assert lines.count("merged section text") == 1


def test_iter_source_lines_unmerged_table_is_unaffected(tmp_path):
    """Regression guard: the dedupe must not collapse two DISTINCT cells
    that happen to hold identical text (no merge involved)."""
    doc = Document()
    table = doc.add_table(rows=2, cols=1)
    table.cell(0, 0).text = "repeated line"
    table.cell(1, 0).text = "repeated line"
    path = tmp_path / "no_merge.docx"
    doc.save(path)

    lines = iter_source_lines(str(path))
    assert lines.count("repeated line") == 2


# ------------------------------------------------------------- #615 items 2/3

def test_compare_metrics_flags_same_count_header_replacement():
    """A header REPLACED by another (same total count) must be visible --
    the old code only diffed titles when headers_detected fell, so a
    same-count swap was invisible entirely (#615 item 2)."""
    baseline = _metrics(headers_detected=2, header_titles=["education", "awards"])
    candidate = _metrics(headers_detected=2, header_titles=["education", "service"])

    verdict, reasons = compare_metrics(baseline, candidate)

    assert verdict == "REGRESSION"
    assert any("headers 2 -> 2" in r and "awards" in r for r in reasons)


def test_compare_metrics_same_titles_same_count_is_ok():
    """Guard: an unchanged header set must not be flagged."""
    baseline = _metrics(headers_detected=2, header_titles=["education", "awards"])
    candidate = _metrics(headers_detected=2, header_titles=["education", "awards"])

    verdict, reasons = compare_metrics(baseline, candidate)

    assert verdict == "OK"
    assert reasons == []


def test_compare_metrics_duplicate_title_loses_one_copy_multiset_diff():
    """A title duplicated in baseline that loses ONE copy must name that
    title in the regression reason. A set() diff collapses the duplicate to
    one member on both sides and reports gone=empty -> sample '?' (#615
    item 3); Counter (multiset) diff sees the surviving imbalance."""
    baseline = _metrics(
        headers_detected=3,
        header_titles=["education", "education", "awards"],
    )
    candidate = _metrics(
        headers_detected=2,
        header_titles=["education", "awards"],
    )

    verdict, reasons = compare_metrics(baseline, candidate)

    assert verdict == "REGRESSION"
    header_reason = next(r for r in reasons if r.startswith("headers"))
    assert "education" in header_reason
    assert "'?'" not in header_reason


# ------------------------------------------------------------- #616 item i
# (also closes one of #618's two cases landing here: missing/partial
# hierarchy shapes)

def _stage2(entries):
    return {"entries": entries}


def test_compute_metrics_bare_string_hierarchy_falls_back_to_none():
    """A malformed entry with hierarchy as a bare string ("Education"
    instead of ["Education"]) must not index hierarchy[0] and silently key
    on its first CHARACTER ("e"); it must fall back to "(none)" (#616 item
    i / #618 backlog: missing/partial hierarchy shapes)."""
    entries = [{
        "element_type": "paragraph",
        "text": "some content",
        "hierarchy": "Education",
    }]
    m = compute_metrics(["some content"], {"hierarchy": []}, _stage2(entries))

    assert m["per_h1_content_counts"] == {"(none)": 1}
    assert "e" not in m["per_h1_content_counts"]


def test_compute_metrics_missing_hierarchy_key_falls_back_to_none():
    """An entry with no hierarchy key at all must also fall back to
    "(none)", not crash (#618 backlog: missing hierarchy shape)."""
    entries = [{"element_type": "paragraph", "text": "some content"}]
    m = compute_metrics(["some content"], {"hierarchy": []}, _stage2(entries))

    assert m["per_h1_content_counts"] == {"(none)": 1}


def test_compute_metrics_empty_list_hierarchy_falls_back_to_none():
    """An entry with hierarchy=[] (empty list, not missing/falsy string)
    must also fall back to "(none)" rather than raising IndexError."""
    entries = [{"element_type": "paragraph", "text": "some content", "hierarchy": []}]
    m = compute_metrics(["some content"], {"hierarchy": []}, _stage2(entries))

    assert m["per_h1_content_counts"] == {"(none)": 1}


def test_compute_metrics_well_formed_hierarchy_list_still_works():
    """Regression guard: a normal list hierarchy is unaffected."""
    entries = [{
        "element_type": "paragraph",
        "text": "some content",
        "hierarchy": ["Education", "Sub"],
    }]
    m = compute_metrics(["some content"], {"hierarchy": []}, _stage2(entries))

    assert m["per_h1_content_counts"] == {"education": 1}


# ------------------------------------------------------------- #618: exact
# header_titles content (the second of the two backlog cases landing here)

def test_compute_metrics_header_titles_exact_content():
    """Pins the exact header_titles list, not just its length -- #618's
    'exact header_titles content, not just count' case."""
    hierarchy = [
        {"text": "  Education  ", "children": [
            {"text": "Undergraduate", "children": []},
        ]},
        {"text": "AWARDS", "children": []},
    ]
    m = compute_metrics([], {"hierarchy": hierarchy}, _stage2([]))

    assert m["header_titles"] == ["education", "undergraduate", "awards"]
    assert m["headers_detected"] == 3


# ------------------------------------------------------------- #617: comment-
# only, informational fields never wired into comparison

def test_compare_metrics_ignores_max_entry_chars_and_per_h1_and_entry_counts():
    """max_entry_chars, per_h1_content_counts, entries_total and
    entries_content are informational only (#617) -- a large divergence in
    any of them alone must not produce a REGRESSION or IMPROVED verdict."""
    baseline = _metrics(
        max_entry_chars=100,
        per_h1_content_counts={"education": 1},
        entries_total=5,
        entries_content=5,
    )
    candidate = _metrics(
        max_entry_chars=9999,
        per_h1_content_counts={"awards": 40},
        entries_total=1,
        entries_content=1,
    )

    verdict, reasons = compare_metrics(baseline, candidate)

    assert verdict == "OK"
    assert reasons == []


# ------------------------------------------------------------- #616 item ii

def test_load_metrics_rejects_non_dict_json(tmp_path, monkeypatch):
    monkeypatch.setattr(segreg, "_outputs_root", lambda: tmp_path)
    snap = _snapshot_dir("badshape")
    snap.mkdir(parents=True)
    (snap / "metrics.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")

    with pytest.raises(SegmentationRegressionError, match="expected a JSON object"):
        _load_metrics("badshape")


def test_load_metrics_rejects_entry_missing_metrics_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(segreg, "_outputs_root", lambda: tmp_path)
    snap = _snapshot_dir("partial")
    snap.mkdir(parents=True)
    (snap / "metrics.json").write_text(
        json.dumps({"uid1": {"source_lines": 1}}), encoding="utf-8"
    )

    with pytest.raises(SegmentationRegressionError, match="missing Metrics keys"):
        _load_metrics("partial")


def test_load_metrics_accepts_well_formed_snapshot(tmp_path, monkeypatch):
    monkeypatch.setattr(segreg, "_outputs_root", lambda: tmp_path)
    snap = _snapshot_dir("good")
    snap.mkdir(parents=True)
    payload = {"uid1": _metrics()}
    (snap / "metrics.json").write_text(json.dumps(payload), encoding="utf-8")

    loaded = _load_metrics("good")

    assert loaded == payload


def test_load_metrics_missing_snapshot_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(segreg, "_outputs_root", lambda: tmp_path)

    with pytest.raises(SegmentationRegressionError, match="No snapshot"):
        _load_metrics("does-not-exist")


# ------------------------------------------------------------ #616 item iii

def test_load_metrics_reads_utf8_encoding_explicitly(tmp_path, monkeypatch):
    """_load_metrics() must round-trip non-ASCII content intact. (This does
    not, by itself, distinguish an explicit encoding="utf-8" from the
    platform default, since that default is UTF-8 on this platform; the
    explicit encoding= argument on every read_text()/write_text() call is
    verified directly by grep in the PR body, item iii.)"""
    monkeypatch.setattr(segreg, "_outputs_root", lambda: tmp_path)
    snap = _snapshot_dir("utf8")
    snap.mkdir(parents=True)
    payload = {"uid1": _metrics(header_titles=["école", "café committee"])}
    (snap / "metrics.json").write_bytes(json.dumps(payload).encode("utf-8"))

    loaded = _load_metrics("utf8")

    assert loaded["uid1"]["header_titles"] == ["école", "café committee"]


def test_run_compare_report_written_with_utf8_encoding(tmp_path, monkeypatch):
    """REPORT.md must round-trip a non-ASCII lost-line sample intact when
    read back with encoding="utf-8". (Same caveat as
    test_load_metrics_reads_utf8_encoding_explicitly above: the platform
    default is UTF-8 too, so this alone doesn't prove the write is
    explicit -- that's confirmed by grep, PR body item iii.)"""
    monkeypatch.setattr(segreg, "_outputs_root", lambda: tmp_path)
    baseline_dir = _snapshot_dir("base")
    candidate_dir = _snapshot_dir("cand")
    baseline_dir.mkdir(parents=True)
    candidate_dir.mkdir(parents=True)

    baseline_metrics = {"uid1": _metrics(header_titles=["café"], headers_detected=1)}
    candidate_metrics = {"uid1": _metrics(header_titles=[], headers_detected=0)}
    (baseline_dir / "metrics.json").write_text(json.dumps(baseline_metrics), encoding="utf-8")
    (candidate_dir / "metrics.json").write_text(json.dumps(candidate_metrics), encoding="utf-8")

    run_compare("base", "cand")

    report = (candidate_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "café" in report


# ------------------------------------------------------------- #616 item iv

def test_snapshot_dir_rejects_path_traversal_label():
    with pytest.raises(ValueError, match="invalid snapshot label"):
        _snapshot_dir("../../etc")


def test_snapshot_dir_rejects_embedded_slash_label():
    with pytest.raises(ValueError, match="invalid snapshot label"):
        _snapshot_dir("gold/../../evil")


def test_snapshot_dir_accepts_normal_label():
    path = _snapshot_dir("baseline_2026-08-01")
    assert path.name == "segsnap_baseline_2026-08-01"


def test_snapshot_dir_rejects_trailing_newline_label():
    # re.match's `$` matches just before a trailing "\n", so a match()-based
    # check alone would accept "abc\n" and produce a directory literally
    # named "segsnap_abc\n". fullmatch() anchors both ends and closes this.
    with pytest.raises(ValueError, match="invalid snapshot label"):
        _snapshot_dir("abc\n")


# -------------------------------------------------------------- #616 item v

def test_snapshot_raises_typed_exception_on_missing_cv_dir(tmp_path):
    empty = tmp_path / "nope"
    with pytest.raises(SegmentationRegressionError, match="No gold CV directory found"):
        segreg.snapshot("label", str(empty), None)


def test_snapshot_raises_typed_exception_on_no_docx_files(tmp_path):
    empty_dir = tmp_path / "cvs"
    empty_dir.mkdir()
    with pytest.raises(SegmentationRegressionError, match="No .docx files matched"):
        segreg.snapshot("label", str(empty_dir), None)


def test_main_translates_segmentation_regression_error_to_sys_exit(tmp_path, monkeypatch):
    """Proves main() surfaces a SegmentationRegressionError raised deep in
    the call stack (_load_metrics(), via run_compare()) as a matching
    SystemExit at the CLI entry point. (This alone doesn't distinguish
    main() from a hypothetical sys.exit() still inside _load_metrics() --
    that _load_metrics() raises rather than exits is pinned separately by
    test_load_metrics_missing_snapshot_raises above, which asserts the
    SegmentationRegressionError type directly.)"""
    monkeypatch.setattr(segreg, "_outputs_root", lambda: tmp_path)
    monkeypatch.setattr(sys, "argv", ["segmentation_regression", "compare", "nope1", "nope2"])

    with pytest.raises(SystemExit) as excinfo:
        segreg.main()

    assert isinstance(excinfo.value.code, str)
    assert "No snapshot" in excinfo.value.code


# ------------------------------------------------------------------- #621
# (this is also the module's first orchestration-level test at run_compare()
# itself, per the issue's own suggestion -- a #618 backlog case)

def test_run_compare_missing_uid_fails_closed(tmp_path, monkeypatch):
    """A uid present in baseline but absent from candidate (e.g. snapshot()
    crashed partway, or --uids excluded it) must be reported as MISSING and
    counted toward regressions -- not silently dropped from `shared` with a
    0-regression pass (#621)."""
    monkeypatch.setattr(segreg, "_outputs_root", lambda: tmp_path)
    baseline_dir = _snapshot_dir("base")
    candidate_dir = _snapshot_dir("cand")
    baseline_dir.mkdir(parents=True)
    candidate_dir.mkdir(parents=True)

    baseline_metrics = {"uid1": _metrics(), "uid2": _metrics()}
    candidate_metrics = {"uid1": _metrics()}  # uid2 missing entirely
    (baseline_dir / "metrics.json").write_text(json.dumps(baseline_metrics), encoding="utf-8")
    (candidate_dir / "metrics.json").write_text(json.dumps(candidate_metrics), encoding="utf-8")

    exit_code = run_compare("base", "cand")

    assert exit_code == 1
    report = (candidate_dir / "REPORT.md").read_text(encoding="utf-8")
    assert "uid2" in report
    assert "MISSING" in report


def test_run_compare_no_missing_no_regressions_still_passes(tmp_path, monkeypatch):
    """Regression guard: the #621 fix must not turn an ordinary clean
    compare (no missing uids, no regressions) into a failure."""
    monkeypatch.setattr(segreg, "_outputs_root", lambda: tmp_path)
    baseline_dir = _snapshot_dir("base")
    candidate_dir = _snapshot_dir("cand")
    baseline_dir.mkdir(parents=True)
    candidate_dir.mkdir(parents=True)

    metrics = {"uid1": _metrics()}
    (baseline_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")
    (candidate_dir / "metrics.json").write_text(json.dumps(metrics), encoding="utf-8")

    exit_code = run_compare("base", "cand")

    assert exit_code == 0


def test_run_compare_no_shared_and_no_missing_still_raises(tmp_path, monkeypatch):
    """Both snapshots empty (or otherwise share nothing and baseline has no
    extra uids either) is still the pre-existing 'nothing to compare'
    error, now raised rather than sys.exit()-ed directly."""
    monkeypatch.setattr(segreg, "_outputs_root", lambda: tmp_path)
    baseline_dir = _snapshot_dir("base")
    candidate_dir = _snapshot_dir("cand")
    baseline_dir.mkdir(parents=True)
    candidate_dir.mkdir(parents=True)

    (baseline_dir / "metrics.json").write_text(json.dumps({}), encoding="utf-8")
    (candidate_dir / "metrics.json").write_text(json.dumps({}), encoding="utf-8")

    with pytest.raises(SegmentationRegressionError, match="share no CVs"):
        run_compare("base", "cand")
