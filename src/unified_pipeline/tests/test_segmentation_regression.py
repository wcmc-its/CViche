"""Tests for the gold-set segmentation regression harness (#208 precondition).

Pure-function tests only — no LLM, no gold corpus needed. The one docx used
is built in-test to prove the coverage metric sees INSIDE table cells (the
89HQVQ blind spot: whole sections living in 1×1 layout-table cells).

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_segmentation_regression.py -p no:cacheprovider
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from docx import Document  # noqa: E402

from unified_pipeline.segmentation_regression import (  # noqa: E402
    compare_metrics,
    compute_metrics,
    iter_source_lines,
    lint_metrics,
)


# ------------------------------------------------------------- source lines

def test_iter_source_lines_sees_into_table_cells(tmp_path):
    doc = Document()
    doc.add_paragraph("EDUCATION")
    table = doc.add_table(rows=1, cols=1)
    cell = table.rows[0].cells[0]
    cell.paragraphs[0].text = "Grants Awarded"
    cell.add_paragraph("FSMB Foundation Grant | Role: Co-PI | Status: Awarded 2026")
    path = tmp_path / "cv.docx"
    doc.save(path)

    lines = iter_source_lines(str(path))
    assert "EDUCATION" in lines
    assert "Grants Awarded" in lines
    assert any("FSMB" in l for l in lines)


def test_iter_source_lines_reads_through_tracked_insertion_in_cell(tmp_path):
    """#557: a run nested inside w:ins (tracked-change insertion) must not be
    dropped mid-word. Reproduces "Down Syndrome" -> "Down yndrome": a table
    cell paragraph built as 'Down ' + <w:ins>S</w:ins> + 'yndrome' must read
    back whole, not with the tracked-change character silently skipped."""
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls

    doc = Document()
    table = doc.add_table(rows=1, cols=1)
    cell = table.rows[0].cells[0]
    para = cell.paragraphs[0]
    para.add_run("Down ")
    tail_run = para.add_run("yndrome")
    ins = parse_xml(
        f'<w:ins {nsdecls("w")} w:id="1" w:author="a" w:date="2026-01-01T00:00:00Z">'
        '<w:r><w:t>S</w:t></w:r></w:ins>'
    )
    tail_run._r.addprevious(ins)

    path = tmp_path / "cv.docx"
    doc.save(path)

    lines = iter_source_lines(str(path))
    assert "Down Syndrome" in lines
    assert "Down yndrome" not in lines


def test_iter_source_lines_recurses_into_nested_table(tmp_path):
    """iter_source_lines's docstring claims recursive table support
    ("INCLUDING paragraphs inside table cells (recursively)") -- prove a
    table nested inside a cell is actually walked, not just a single level
    of cells."""
    doc = Document()
    outer_table = doc.add_table(rows=1, cols=1)
    outer_cell = outer_table.rows[0].cells[0]
    outer_cell.paragraphs[0].text = "Outer cell text"
    nested_table = outer_cell.add_table(rows=1, cols=1)
    nested_table.rows[0].cells[0].paragraphs[0].text = "Nested table text"

    path = tmp_path / "cv.docx"
    doc.save(path)

    lines = iter_source_lines(str(path))
    assert "Outer cell text" in lines
    assert "Nested table text" in lines


# ------------------------------------------------------------------ metrics

_GRANT_A = "FSMB Foundation Grant | Shapiro, M. (PI) | Improving Access to Healthcare | Role: Co-PI"
_GRANT_B = "John Templeton Foundation OFI | Jung, E. (PI) | Intellectual Humility | Role: PI"
_GRANT_C = "NBME Stemmler Education Grant | Jung, E. (PI) | Use of AI | Role: PI | Status: Not Funded"

_STAGE1A = {"hierarchy": [
    {"text": "GRANTS", "children": [
        {"text": "Grants Awarded", "children": []},
        {"text": "Grants Under Review & Submitted", "children": []},
    ]},
]}


def _entry(text, etype="paragraph", start=0, hierarchy=None):
    return {"text": text, "element_type": etype, "element_idx_start": start,
            "hierarchy": hierarchy or ["GRANTS"]}


def test_metrics_on_clean_extraction():
    source = ["GRANTS", _GRANT_A, _GRANT_B, _GRANT_C]
    stage2 = {"entries": [_entry(_GRANT_A, start=1), _entry(_GRANT_B, start=2),
                          _entry(_GRANT_C, start=3)]}
    m = compute_metrics(source, _STAGE1A, stage2)
    assert m["text_coverage_pct"] == 100.0
    assert m["lost_lines"] == []
    assert m["mega_entries"] == 0
    assert m["headers_detected"] == 3
    assert m["per_h1_content_counts"] == {"grants": 3}


def test_metrics_detect_mega_entry_and_lost_text():
    # The 89HQVQ shape: grants fused into one entry, one grant dropped entirely.
    source = ["GRANTS", _GRANT_A, _GRANT_B, _GRANT_C]
    mega = _entry(_GRANT_A + "\n" + _GRANT_B + "\n" + _GRANT_A + " extra", start=1)
    stage2 = {"entries": [mega]}
    m = compute_metrics(source, _STAGE1A, stage2)
    assert m["mega_entries"] == 1
    assert any("NBME" in l for l in m["lost_lines"])
    assert m["text_coverage_pct"] < 100.0


def test_metrics_count_dups_and_empties():
    stage2 = {"entries": [
        _entry(_GRANT_A, etype="table", start=30.0),
        _entry(_GRANT_A, etype="table", start=30.0),  # duplicate
        _entry("", start=5),                           # empty content
        _entry("", etype="break", start=6),            # break: NOT empty-content
    ]}
    m = compute_metrics([], _STAGE1A, stage2)
    assert m["duplicate_entries"] == 1
    assert m["empty_content"] == 1


def test_coverage_is_whitespace_insensitive():
    """Stage 2 fuses text across in-paragraph breaks with NO whitespace
    ('Present position:' + break + 'Attending' -> 'position:Attending') and
    drops tabs from label/value lines. Neither may count as lost text."""
    source = [
        "Present position:\tAttending Pathologist of the Department",
        "Urine cytology: Pitfall due to a\n\"remnant\" lesion. Cytojournal. 2015",
    ]
    stage2 = {"entries": [
        _entry("Present position:Attending Pathologist of the Department", start=1),
        _entry('Urine cytology: Pitfall due to a"remnant" lesion. Cytojournal. 2015', start=2),
    ]}
    m = compute_metrics(source, _STAGE1A, stage2)
    assert m["lost_lines"] == []
    assert m["text_coverage_pct"] == 100.0


def test_coverage_line_cannot_match_across_entry_boundary():
    # First half in one entry, second half in the next: that IS a lost line
    # (the sentinel between entries must prevent a spanning match).
    source = ["alpha beta gamma delta epsilon zeta"]
    stage2 = {"entries": [_entry("alpha beta gamma", start=1),
                          _entry("delta epsilon zeta", start=2)]}
    m = compute_metrics(source, _STAGE1A, stage2)
    assert m["lost_lines"] == ["alpha beta gamma delta epsilon zeta"]


# ------------------------------------------------------------------ compare

_BASE = {
    "text_coverage_pct": 99.0, "lost_lines": ["old lost line"],
    "headers_detected": 10, "header_titles": ["grants", "education"],
    "mega_entries": 1, "duplicate_entries": 0, "empty_content": 0,
}


def _cand(**over):
    c = dict(_BASE)
    c.update(over)
    return c


def test_compare_flags_coverage_drop_beyond_tolerance():
    verdict, reasons = compare_metrics(_BASE, _cand(text_coverage_pct=97.0))
    assert verdict == "REGRESSION"
    assert any("coverage" in r for r in reasons)


def test_compare_tolerates_llm_wobble():
    verdict, _ = compare_metrics(_BASE, _cand(text_coverage_pct=98.5))
    assert verdict == "OK"


def test_compare_flags_newly_lost_lines_even_within_tolerance():
    verdict, reasons = compare_metrics(
        _BASE, _cand(lost_lines=["old lost line", "a brand new lost line"]))
    assert verdict == "REGRESSION"
    assert any("newly lost" in r for r in reasons)


def test_compare_flags_disappeared_headers_by_name():
    verdict, reasons = compare_metrics(
        _BASE, _cand(headers_detected=9, header_titles=["education"]))
    assert verdict == "REGRESSION"
    assert any("grants" in r for r in reasons)


def test_compare_reports_improvement():
    verdict, reasons = compare_metrics(_BASE, _cand(mega_entries=0))
    assert verdict == "IMPROVED"
    assert any("mega_entries" in r for r in reasons)


# --------------------------------------------------------------------- lint

def test_lint_flags_and_clean():
    dirty = _cand(text_coverage_pct=90.0, mega_entries=2)
    assert lint_metrics(dirty)
    clean = _cand(text_coverage_pct=99.5, mega_entries=0,
                  duplicate_entries=0, empty_content=0)
    assert lint_metrics(clean) == []
