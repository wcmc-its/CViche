"""Tests for the cross-stage run doctor.

Pure-function tests only — no LLM, no DB, no gold corpus. Fixtures are small
synthetic 89HQVQ-shaped artifacts (pipe-delimited grant rows, fused
mega-entries, mis-bucketed statuses); the docx files used are built in-test
with python-docx.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_run_doctor.py -p no:cacheprovider
"""

import ast
import importlib
import importlib.util
import json
import re
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402
from docx import Document  # noqa: E402

from unified_pipeline.doctor.lints.render import (  # noqa: E402
    CITATION_EVIDENCE_CHARS,
    DATE_ONLY_LINES_WARN_COUNT,
    IDENTICAL_ROW_EVIDENCE_RECORDS,
    IDENTICAL_ROW_FUSED_MIN_CHARS,
    OUTPUT_LEAK_EVIDENCE_LIMIT,
)
from unified_pipeline.doctor.shared import (  # noqa: E402
    TABLE_ROW_JOINER,
    docx_body_blocks,
)
from unified_pipeline.quality_score import (  # noqa: E402
    _TEMPLATE_DOCX_PATH,
    score_cv_owner,
)
from unified_pipeline.run_doctor import (  # noqa: E402
    CLASSIFIED_UNRENDERED_WARN_ENTRIES,
    DUPLICATE_PASSAGE_MIN_BLOCKS,
    MISSED_HEADERS_WARN_COUNT,
    appendix_entry_count,
    honors_table_totals,
    iter_header_candidates,
    lint_bucket_status,
    lint_citation_field_dropped,
    lint_classified_unrendered,
    lint_date_cell_shape,
    lint_date_only_lines,
    lint_dead_sections,
    lint_dedup_drops,
    lint_duplicate_passages,
    lint_enrichment_failures,
    lint_enrichment_pubtype_mismatch,
    lint_etal_added,
    lint_group_header_context,
    lint_identical_rendered_rows,
    lint_junk_or_header_row,
    lint_llm_fallback_served,
    lint_llm_refusal_in_output,
    lint_missed_headers,
    lint_no_output,
    lint_output_hygiene,
    lint_owner_contact_missing,
    lint_owner_missing_from_citation,
    lint_pipe_leaks,
    lint_pipeline_errors,
    lint_pubmed_title_truncated,
    lint_python_repr_in_output,
    lint_research_summary_call_failed,
    lint_section_lost,
    lint_segmentation,
    lint_segmentation_collapse,
    lint_split_child_unsourced,
    lint_stage3b_fallback_ratio,
    lint_stage4_group_failures,
    lint_stage4_unplaced_items,
    lint_stage6_warnings,
    lint_stage_failure_recorded,
    lint_surprise,
    lint_table_lost,
    lint_table_shape,
    lint_taxonomy_code_coverage,
    lint_under_extraction,
    lint_unrendered_records,
    main,
    rank_lints,
    read_docx_blocks,
    read_docx_table_rows,
    run_doctor,
    unrouted_code_counts,
)
from unified_pipeline.stage_errors import (  # noqa: E402
    StageError,
    record_stage_outcome,
    stage_errors_path,
)

_GRANT_FSMB = ("Federation of State Medical Boards (FSMB) Foundation Grant | "
               "Shapiro, M. (PI) | Role: Co-PI | Amount: $75,000 | Status: Awarded 2026")
_GRANT_TEMPLETON = ("John Templeton Foundation Online Funding Inquiry (OFI) | "
                    "Jung, E. (PI) | Role: PI | Amount: $1,400,000 | "
                    "Status: Submitted 2026, Under review")
_GRANT_NBME = ("National Board of Medical Examiners (NBME) Stemmler Fund | "
               "Shapiro, M. (PI) | Role: PI | Status: Not funded")

_STAGE1A = {"hierarchy": [{"text": "GRANTS", "level": "H1", "children": []}]}


def _entry(text, etype="paragraph", start=0, hierarchy=None, **extra):
    e = {"text": text, "element_type": etype, "element_idx_start": start,
         "element_idx_end": start, "hierarchy": hierarchy or ["GRANTS"]}
    e.update(extra)
    return e


def _grant4(text, code, pct=95.0, start=0):
    # Real stage-4 M2* schemas have no 'status' field: statuses live only in
    # the raw entry text ("... | Status: Not funded").
    fields = {"title": text.split(" | ")[0]}
    return _entry(text, start=start, taxonomy_code=code, extracted_fields=fields,
                  extraction_coverage={"extraction_coverage_percent": pct,
                                       "unextracted_words": [],
                                       "total_original_words": 40,
                                       "total_extracted_words": 36})


def _funding_blocks(current=(), completed=(), pending=()):
    """Stage-6-shaped funding area: plain subsection headers with one Word
    table per grant beneath each."""
    blocks = [("p", "RESEARCH"), ("p", "Research Support:"),
              ("p", "Current Research Funding")]
    blocks += [("table", t) for t in current]
    blocks.append(("p", "Past (Completed) Funding"))
    blocks += [("table", t) for t in completed]
    blocks.append(("p", "Pending Funding"))
    blocks += [("table", t) for t in pending]
    return blocks


# --------------------------------------------------------- lint 1: segmentation

def test_segmentation_lint_fires_on_mega_entry_and_lost_grant():
    # The 89HQVQ shape: grants fused into one cell entry, one lost outright.
    source = ["GRANTS", _GRANT_FSMB, _GRANT_TEMPLETON, _GRANT_NBME]
    mega = _entry(_GRANT_FSMB + "\n" + _GRANT_TEMPLETON + "\n" + _GRANT_FSMB + " again",
                  etype="table", start=30)
    findings = lint_segmentation(source, _STAGE1A, {"entries": [mega]})
    assert findings
    assert all(f["lint"] == "segmentation" and f["severity"] == "WARN"
               for f in findings)
    assert any(f["message"].startswith("coverage") for f in findings)
    assert any("mega_entries" in f["message"] for f in findings)
    coverage = next(f for f in findings if f["message"].startswith("coverage"))
    assert any("NBME" in line for line in coverage["evidence"])


def test_segmentation_lint_quiet_on_clean_extraction():
    source = ["GRANTS", _GRANT_FSMB, _GRANT_TEMPLETON, _GRANT_NBME]
    stage2 = {"entries": [_entry(_GRANT_FSMB, start=1),
                          _entry(_GRANT_TEMPLETON, start=2),
                          _entry(_GRANT_NBME, start=3)]}
    assert lint_segmentation(source, _STAGE1A, stage2) == []


def test_segmentation_lint_quiet_on_a_dob_line_stage_2_withheld():
    """#1232: the pre-LLM scrub (#847) puts '[withheld]' where the source
    line has the date. On a short CV that one line took coverage under the
    97% threshold."""
    dob = "Date of Birth: January 2, 1970"
    source = ["GRANTS", dob, _GRANT_FSMB, _GRANT_TEMPLETON, _GRANT_NBME]
    stage2 = {"entries": [_entry("Date of Birth: [withheld]", start=1),
                          _entry(_GRANT_FSMB, start=2),
                          _entry(_GRANT_TEMPLETON, start=3),
                          _entry(_GRANT_NBME, start=4)]}
    assert lint_segmentation(source, _STAGE1A, stage2) == []


def test_segmentation_lint_evidence_never_quotes_a_withheld_dob():
    """A date-of-birth line that really is lost is reported, but the evidence
    in the run's `_doctor.json` is the scrubbed line (#1232)."""
    source = ["Date of Birth: January 2, 1970", _GRANT_FSMB]
    findings = lint_segmentation(source, _STAGE1A,
                                 {"entries": [_entry(_GRANT_FSMB, start=1)]})
    coverage = next(f for f in findings if f["message"].startswith("coverage"))
    assert coverage["evidence"] == ["Date of Birth: [withheld]"]
    assert "1970" not in coverage["message"]


def test_table_lost_lint_is_one_warn_per_run_with_worst_table_evidence():
    stage2 = {"entries": [_entry(_GRANT_FSMB, start=1)]}
    small = [f"lost contact line {i} zebra" for i in range(3)]
    large = [f"lost grant line {i} yak" for i in range(6)]
    block_lines = ([(0, _GRANT_FSMB)] + [(1, l) for l in small]
                   + [(2, l) for l in large])
    findings = lint_table_lost(block_lines, stage2)
    assert [(f["lint"], f["severity"]) for f in findings] == [("table_lost", "WARN")]
    assert findings[0]["message"] == "2 source table(s) mostly lost; worst: 6 of 6 lines"
    assert findings[0]["evidence"] == large[:5]


def _stage1b(placed, unplaced, synthetic_end=2):
    """Stage 1b with `placed` headings on a source line, `unplaced` without
    one, and a synthetic preamble section over elements 0..synthetic_end."""
    nodes = [{"text": "PERSONAL DATA", "element_idx": None, "synthetic": True}]
    nodes += [{"text": f"SECTION {i}", "element_idx": 10 * (i + 1), "synthetic": False}
              for i in range(placed)]
    nodes += [{"text": f"LOST {i}", "element_idx": None, "synthetic": False}
              for i in range(unplaced)]
    sections = [{"hierarchy": ["PERSONAL DATA"], "element_idx_start": 0,
                 "element_idx_end": synthetic_end, "synthetic": True}]
    sections += [{"hierarchy": [f"SECTION {i}"], "element_idx_start": 10 * (i + 1),
                  "element_idx_end": 10 * (i + 1) + 9} for i in range(placed)]
    return {"hierarchy_with_indices": nodes, "section_boundaries": sections}


def _stage2_at(*starts):
    return {"entries": [{"element_idx_start": s, "element_type": "paragraph", "text": "x"}
                        for s in starts]}


def test_segmentation_collapse_fires_when_1b_places_no_heading():
    # DPEHSZ shape: every 1a heading unplaced, every entry in the preamble.
    stage1b = _stage1b(placed=0, unplaced=4, synthetic_end=50)
    findings = lint_segmentation_collapse(stage1b, _stage2_at(1, 5, 20, 40))
    assert [(f["lint"], f["severity"]) for f in findings] == [("segmentation_collapse", "WARN")]
    assert findings[0]["message"] == ("section structure lost: 0 of 4 headings placed, "
                                      "4 of 4 entries in synthetic sections")
    assert findings[0]["evidence"] == [
        "stage 1b placed 0 of 4 stage 1a headings",
        "element_idx_start 0: synthetic section 'PERSONAL DATA' (to 50) holds 4 of 4 stage-2 entries"]


def test_segmentation_collapse_fires_on_under_half_placed_alone():
    findings = lint_segmentation_collapse(_stage1b(placed=1, unplaced=2), _stage2_at(11, 12))
    assert len(findings) == 1
    assert "1 of 3 headings placed" in findings[0]["message"]


def test_segmentation_collapse_fires_when_the_preamble_holds_most_entries_alone():
    stage1b = _stage1b(placed=3, unplaced=0, synthetic_end=9)
    findings = lint_segmentation_collapse(stage1b, _stage2_at(1, 2, 3, 11))
    assert len(findings) == 1
    assert "3 of 4 entries in synthetic sections" in findings[0]["message"]


def test_segmentation_collapse_quiet_at_half_placed_and_a_small_preamble():
    stage1b = _stage1b(placed=2, unplaced=2)
    assert lint_segmentation_collapse(stage1b, _stage2_at(1, 11, 12, 21, 22)) == []


def test_segmentation_collapse_quiet_when_the_preamble_holds_exactly_half():
    # The synthetic share must be OVER half: 2 of 4 entries is quiet.
    stage1b = _stage1b(placed=3, unplaced=0, synthetic_end=9)
    assert lint_segmentation_collapse(stage1b, _stage2_at(1, 2, 11, 21)) == []


def test_segmentation_collapse_counts_headings_nested_under_children():
    # Stage 1b nests subheadings under `children`: one placed parent with
    # three unplaced children is 1 of 4 placed, not 1 of 1.
    stage1b = _stage1b(placed=1, unplaced=0)
    stage1b["hierarchy_with_indices"][1]["children"] = [
        {"text": f"SUB {i}", "element_idx": None, "synthetic": False,
         "children": [{"text": f"SUBSUB {i}", "element_idx": None, "synthetic": False}]
         if i == 0 else []}
        for i in range(2)]
    findings = lint_segmentation_collapse(stage1b, _stage2_at(11, 12))
    assert len(findings) == 1
    assert "1 of 4 headings placed" in findings[0]["message"]


def test_segmentation_collapse_does_not_count_break_rows_as_entries():
    stage1b = _stage1b(placed=2, unplaced=0, synthetic_end=9)
    stage2 = _stage2_at(11, 21)
    stage2["entries"] += [{"element_idx_start": i, "element_type": "break"} for i in range(1, 9)]
    assert lint_segmentation_collapse(stage1b, stage2) == []


def test_segmentation_collapse_counts_an_entry_on_the_synthetic_end_bound():
    # Section bounds are inclusive: an entry at element_idx_end is inside.
    stage1b = _stage1b(placed=3, unplaced=0, synthetic_end=9)
    findings = lint_segmentation_collapse(stage1b, _stage2_at(1, 9, 11))
    assert "2 of 3 entries in synthetic sections" in findings[0]["message"]


def test_segmentation_collapse_counts_an_entry_on_the_synthetic_start_bound():
    # Section bounds are inclusive at the start too: an entry at
    # element_idx_start is inside (DPEHSZ's first entry sits on it).
    stage1b = _stage1b(placed=3, unplaced=0, synthetic_end=9)
    findings = lint_segmentation_collapse(stage1b, _stage2_at(0, 5, 11))
    assert "2 of 3 entries in synthetic sections" in findings[0]["message"]


def test_segmentation_collapse_names_the_synthetic_section_holding_the_most():
    stage1b = _stage1b(placed=0, unplaced=2, synthetic_end=4)
    stage1b["section_boundaries"].append(
        {"hierarchy": ["CONTACT"], "element_idx_start": 5, "element_idx_end": 30,
         "synthetic": True})
    findings = lint_segmentation_collapse(stage1b, _stage2_at(1, 6, 7, 8))
    assert findings[0]["evidence"][1] == (
        "element_idx_start 5: synthetic section 'CONTACT' (to 30) holds 3 of 4 stage-2 entries")


def test_segmentation_collapse_counts_an_entry_once_in_overlapping_synthetic_sections():
    stage1b = _stage1b(placed=0, unplaced=2, synthetic_end=9)
    stage1b["section_boundaries"].append(
        {"hierarchy": ["CONTACT"], "element_idx_start": 0, "element_idx_end": 9,
         "synthetic": True})
    findings = lint_segmentation_collapse(stage1b, _stage2_at(1, 2))
    assert "2 of 2 entries in synthetic sections" in findings[0]["message"]


def test_run_doctor_wires_segmentation_collapse_to_stage_1b_then_stage_2(tmp_path):
    """The wire: LintSpec passes artifacts positionally, so swapping the
    registry's ("stage_1b", "stage_2") would read headings from stage 2 and
    entries from stage 1b, and the lint would go silent."""
    root = _build_clean_run(tmp_path)
    assert not [f for f in run_doctor(root, _UID)["findings"]
                if f["lint"] == "segmentation_collapse"]
    collapsed = _stage1b(placed=0, unplaced=3, synthetic_end=50)
    _write_stage(root, "stage_1b_hierarchy_mapping", f"{_UID}_cv_hierarchy_mapped.json",
                 {"document_uid": _UID, **collapsed})
    findings = [f for f in run_doctor(root, _UID)["findings"]
                if f["lint"] == "segmentation_collapse"]
    assert [(f["severity"], f["message"].split(":")[0]) for f in findings] == [
        ("WARN", "section structure lost")]


def test_run_doctor_missed_headers_gets_the_stage4_owner_name(tmp_path):
    """The wire (#539): run_doctor hands stage 4's cv_owner to the lint, so a
    bare owner-name line is not reported while a real header still is."""
    root = _build_clean_run(tmp_path)
    source_path = root / "uploads" / f"{_UID}_cv.docx"
    source = Document(str(source_path))
    source.add_paragraph("MIRIAM SHAPIRO").runs[0].bold = True
    source.add_paragraph("MENTORING").runs[0].bold = True
    source.save(str(source_path))

    payload = run_doctor(root, _UID)
    missed = [f["evidence"] for f in payload["findings"] if f["lint"] == "missed_headers"]
    assert missed == [["MENTORING"]]


def test_missed_headers_heading_styled_lines_never_escalate_the_run():
    """#1232: a line that is not ALL-CAPS reached the candidate list through
    its Heading style alone (record titles, journal names, contact lines). A
    CV that styles its records Heading 1 must not read as a WARN storm, and
    the lines stay reported."""
    weak = [f"Example Record {name}" for name in
            ("Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta")]
    assert len(weak) > MISSED_HEADERS_WARN_COUNT
    findings = lint_missed_headers(weak, _STAGE1A, {"entries": []})
    assert [f["evidence"][0] for f in findings] == weak
    assert {f["severity"] for f in findings} == {"INFO"}


def test_missed_headers_only_all_caps_candidates_set_the_run_severity():
    """Weak candidates are not counted toward the WARN threshold, and an
    ALL-CAPS candidate still takes the run severity from ITS OWN count."""
    weak = [f"Example Record {name}" for name in ("Alpha", "Beta", "Gamma")]
    few = [f"SECTION {chr(65 + i)}" for i in range(MISSED_HEADERS_WARN_COUNT - 1)]
    found = lint_missed_headers(few + weak, _STAGE1A, {"entries": []})
    assert {f["severity"] for f in found} == {"INFO"}, \
        "five strong lines plus weak ones stay under the threshold"

    many = [f"SECTION {chr(65 + i)}" for i in range(MISSED_HEADERS_WARN_COUNT)]
    found = lint_missed_headers(many + weak, _STAGE1A, {"entries": []})
    by_line = {f["evidence"][0]: f["severity"] for f in found}
    assert {by_line[line] for line in many} == {"WARN"}
    assert {by_line[line] for line in weak} == {"INFO"}


def test_run_doctor_reports_a_lost_source_table(tmp_path):
    """The wire: run_doctor reads the source docx once, and both the flat
    lines (coverage) and the block-tagged lines (table_lost) come from it."""
    root = _build_clean_run(tmp_path)
    source_path = root / "uploads" / f"{_UID}_cv.docx"
    source = Document(str(source_path))
    table = source.add_table(rows=3, cols=1)
    for i in range(3):
        table.rows[i].cells[0].paragraphs[0].text = f"lost contact line {i} zebra"
    source.save(str(source_path))

    payload = run_doctor(root, _UID)
    lost = [f for f in payload["findings"] if f["lint"] == "table_lost"]
    assert [f["message"] for f in lost] == [
        "1 source table(s) mostly lost; worst: 3 of 3 lines"]
    assert payload["metrics"]["source_coverage_pct"] == 50.0


def test_table_lost_lint_quiet_when_every_table_survives():
    assert lint_table_lost([(0, _GRANT_FSMB)], {"entries": [_entry(_GRANT_FSMB, start=1)]}) == []


# ------------------------------------------------------ lint 2: missed headers

def test_iter_header_candidates_sees_bold_paragraphs_and_cells(tmp_path):
    doc = Document()
    doc.add_paragraph("PROFESSIONAL EXPERIENCE").runs[0].bold = True
    doc.add_paragraph("EDUCATION", style="Heading 1")
    doc.add_paragraph("2016-2020 GRANTS").runs[0].bold = True  # digits: excluded
    doc.add_paragraph("A plain body line that is not bold and not a header")
    table = doc.add_table(rows=1, cols=1)
    table.rows[0].cells[0].paragraphs[0].add_run("GRANTS AWARDED").bold = True
    path = tmp_path / "cv.docx"
    doc.save(path)

    candidates = iter_header_candidates(str(path))
    assert set(candidates) == {"PROFESSIONAL EXPERIENCE", "EDUCATION",
                               "GRANTS AWARDED"}


def test_iter_header_candidates_skips_bold_non_headers(tmp_path):
    doc = Document()
    doc.add_paragraph("CURRICULUM VITAE").runs[0].bold = True    # furniture
    doc.add_paragraph("Jane Q. Sample, M.D.").runs[0].bold = True  # not ALL-CAPS
    doc.add_paragraph("MENTORING").runs[0].bold = True           # a real header
    table = doc.add_table(rows=2, cols=3)                        # data table
    for i, label in enumerate(["Years Taught", "Course Number", "ROLE IN COURSE"]):
        table.rows[0].cells[i].paragraphs[0].add_run(label).bold = True
    path = tmp_path / "cv.docx"
    doc.save(path)

    assert iter_header_candidates(str(path)) == ["MENTORING"]


def _merge_row(row):
    """Merge every cell of a python-docx row into one (a gridSpan merge)."""
    cells = row.cells
    if len(cells) > 1:
        cells[0].merge(cells[-1])


def test_iter_header_candidates_sees_a_gridspan_merged_single_column_table(tmp_path):
    """#749 / #446 review item 6: a section-container table built on a
    two-column grid with every row merged into one cell is ONE logical
    column. `len(tbl.columns)` says 2 and used to skip it, so the bold
    ALL-CAPS header inside was never a candidate -- and a merged row must
    yield the header once, not once per grid column it spans."""
    doc = Document()
    table = doc.add_table(rows=2, cols=2)
    for row in table.rows:
        _merge_row(row)
    table.rows[0].cells[0].paragraphs[0].add_run("PROFESSIONAL SOCIETIES").bold = True
    table.rows[1].cells[0].paragraphs[0].add_run("Member, American College of Physicians")
    path = tmp_path / "cv.docx"
    doc.save(path)

    assert len(table.columns) == 2, "the grid count the old rule keyed on"
    assert iter_header_candidates(str(path)) == ["PROFESSIONAL SOCIETIES"]


def test_iter_header_candidates_skips_a_data_table_with_a_merged_title_row(tmp_path):
    """#749 discriminator, variable row widths: a three-column data table
    whose FIRST row is merged into one title cell is still a data table --
    its bold cells are column headers. Counting effective columns from the
    first row alone (the issue's first suggestion) would read it as
    single-column and promote 'YEARS TAUGHT' to a section header."""
    doc = Document()
    table = doc.add_table(rows=3, cols=3)
    _merge_row(table.rows[0])
    table.rows[0].cells[0].paragraphs[0].add_run("TEACHING").bold = True
    for i, label in enumerate(["YEARS TAUGHT", "COURSE NUMBER", "ROLE IN COURSE"]):
        table.rows[1].cells[i].paragraphs[0].add_run(label).bold = True
    for i, value in enumerate(["2019-2021", "MED 101", "Lecturer"]):
        table.rows[2].cells[i].paragraphs[0].add_run(value)
    doc.add_paragraph("MENTORING").runs[0].bold = True
    path = tmp_path / "cv.docx"
    doc.save(path)

    assert iter_header_candidates(str(path)) == ["MENTORING"]


def test_is_single_column_is_the_logical_cell_count_not_the_grid():
    """The document representation made explicit: single-column means every
    row has exactly one logical cell, independent of the layout grid."""
    from unified_pipeline.run_doctor import _is_single_column, _logical_cells
    doc = Document()
    plain = doc.add_table(rows=2, cols=1)
    merged = doc.add_table(rows=2, cols=3)
    for row in merged.rows:
        _merge_row(row)
    title_over_data = doc.add_table(rows=2, cols=2)
    _merge_row(title_over_data.rows[0])
    data = doc.add_table(rows=2, cols=2)

    assert _is_single_column(plain)
    assert _is_single_column(merged)
    assert [len(_logical_cells(r)) for r in merged.rows] == [1, 1]
    assert (len(merged.rows[0].cells), len(merged.columns)) == (3, 3), \
        "what python-docx itself reports for the merged table"
    assert not _is_single_column(title_over_data)
    assert not _is_single_column(data)


def test_iter_header_candidates_skips_a_leading_role_marker_name(tmp_path):
    """#539: 'PI. <Name>' / 'Dr. <Name>' is the owner's name line (marker in
    FRONT, which the suffix-only credential filter misses); a header that
    merely starts with a marker, or an ALL-CAPS line, is still a candidate."""
    doc = Document()
    doc.add_paragraph("PI. Jane Q. Sample", style="Heading 1")
    doc.add_paragraph("Dr. Alex Example", style="Heading 1")
    doc.add_paragraph("Prof. Sam Oneil-Test", style="Heading 1")
    doc.add_paragraph("PI. RESPONSIBILITIES", style="Heading 1")   # header
    doc.add_paragraph("Dr. PUBLICATIONS", style="Heading 1")       # header
    doc.add_paragraph("Ms. Research Support", style="Heading 1")   # name-shaped: exempt
    doc.add_paragraph("Pi. Notes", style="Heading 1")              # marker case differs
    doc.add_paragraph("Dr. Jane Sample GRANTS AND AWARDS", style="Heading 1")  # header text follows
    doc.add_paragraph("Ms. Research Support GRANTS", style="Heading 1")        # header text follows
    path = tmp_path / "cv.docx"
    doc.save(path)

    assert iter_header_candidates(str(path)) == [
        "PI. RESPONSIBILITIES", "Dr. PUBLICATIONS", "Pi. Notes",
        "Dr. Jane Sample GRANTS AND AWARDS", "Ms. Research Support GRANTS"]


def test_iter_header_candidates_flattens_the_tab_after_an_enumeration_token(tmp_path):
    """#1232: 'I.<tab>OVERVIEW OF ...' is a header whose numeral is tab-
    separated from its title, not a label/value data row, so it is a candidate
    (with the tab read as a space). Any other tab still means a data row."""
    doc = Document()
    doc.add_paragraph("I. \tOVERVIEW OF EXAMPLE DUTIES,").runs[0].bold = True
    doc.add_paragraph("AND EXAMPLE RESEARCH").runs[0].bold = True
    doc.add_paragraph("B.\tTEACHING", style="Heading 2")
    doc.add_paragraph("I.\tTITLE\tVALUE", style="Heading 2")        # second tab
    doc.add_paragraph("ACTIVE\t\t\tEXAMPLELAND", style="Heading 2")  # data row
    path = tmp_path / "cv.docx"
    doc.save(path)

    assert iter_header_candidates(str(path)) == [
        "I. OVERVIEW OF EXAMPLE DUTIES,", "AND EXAMPLE RESEARCH", "B. TEACHING"]


_MH_STAGE4 = {"cv_owner": {"first_name": "Jane", "middle_name": "Q", "last_name": "Sample"}}


def test_missed_headers_skips_the_owner_name_only_when_stage4_names_it():
    stage2 = {"entries": []}
    bare = ["JANE Q. SAMPLE", "Jane Sample", "SAMPLE JANE"]
    # Baseline (no stage 4, or an owner with no last name): still reported.
    assert len(lint_missed_headers(bare, _STAGE1A, stage2)) == 3
    no_last = {"cv_owner": {"first_name": "Jane", "last_name": ""}}
    assert len(lint_missed_headers(bare, _STAGE1A, stage2, no_last)) == 3
    assert lint_missed_headers(bare, _STAGE1A, stage2, _MH_STAGE4) == []


def test_missed_headers_owner_name_words_come_from_every_name_field():
    """Each name field feeds the allowed words; a lone initial is allowed
    whatever letter it is (not only the middle initial)."""
    stage2 = {"entries": []}

    def missed(cand, owner):
        return lint_missed_headers([cand], _STAGE1A, stage2, {"cv_owner": owner})

    base = {"first_name": "Jane", "last_name": "Sample"}
    # Non-middle initial.
    assert missed("JANE Z. SAMPLE", {**base, "middle_name": "Q"}) == []
    # Multi-letter middle name, no full_name.
    assert missed("JANE QUINCY SAMPLE", {**base, "middle_name": "Quincy"}) == []
    # Extra word only in full_name, no middle_name.
    assert missed("JANE QUINCY SAMPLE",
                  {**base, "full_name": "Jane Quincy Sample"}) == []
    # Same line with neither field: reported.
    assert len(missed("JANE QUINCY SAMPLE", base)) == 1


def test_missed_headers_owner_exemption_is_subset_by_construction():
    """Only the owner's name is exempt: a header that shares a word with it,
    carries an extra word, or has only the first or last name stays reported."""
    stage2 = {"entries": []}
    kept = ["SAMPLE PUBLICATIONS", "JANE SAMPLE PUBLICATIONS", "JANE",
            "SAMPLE", "JANE Q. SMITH"]
    found = lint_missed_headers(kept, _STAGE1A, stage2, _MH_STAGE4)
    assert [f["evidence"][0] for f in found] == kept


def test_missed_headers_skips_lines_of_the_personal_data_block():
    """EBYSBC: a CV title line and an employer line that stage 3b filed as
    the owner's Personal Data are the contact block, not missed headers; a
    header filed anywhere else is still reported, and so is any line when
    stage 4 is absent."""
    title, employer, header = ("CURRICULUM VITAE, JANE Q. SAMPLE",
                               "EXAMPLE STATE UNIVERSITY", "PROFESSIONAL EXPERIENCE")
    stage4 = {"entries": [
        {"taxonomy_code": "A", "text": title},
        {"taxonomy_code": "A", "text": f"{employer}\tDepartment of Examples\nExample City"},
        {"taxonomy_code": "T", "text": header}]}
    found = lint_missed_headers([title, employer, header], _STAGE1A,
                                {"entries": []}, stage4)
    assert [f["evidence"][0] for f in found] == [header]
    assert len(lint_missed_headers([title, employer, header], _STAGE1A,
                                   {"entries": []})) == 3


def test_missed_headers_contact_block_matches_whole_lines_and_cells_only():
    """A header word that only sits INSIDE a Personal Data line is not that
    line: 'UNIVERSITY' alone is still reported."""
    stage4 = {"entries": [{"taxonomy_code": "A",
                           "text": "Example State University\tDepartment of Examples"}]}
    found = lint_missed_headers(["UNIVERSITY"], _STAGE1A, {"entries": []}, stage4)
    assert len(found) == 1


def test_missed_headers_fires_on_demoted_header():
    findings = lint_missed_headers(
        ["PROFESSIONAL EXPERIENCE"], _STAGE1A,
        {"entries": [_entry(_GRANT_FSMB, hierarchy=["GRANTS"])]})
    assert len(findings) == 1
    assert "PROFESSIONAL EXPERIENCE" in findings[0]["message"]
    # Still reported in full; INFO rather than WARN because one demoted header
    # is the corpus norm (p50=2 over 73 runs) and a flat WARN made 77% of runs
    # share one meaningless verdict (#438). The finding, its message and its
    # evidence are unchanged -- only whether it escalates the RUN's verdict.
    assert findings[0]["severity"] == "INFO"


def test_missed_headers_escalates_to_warn_at_corpus_scale():
    """Many demoted headers means segmentation lost the document's shape."""
    many = [f"SECTION {i}" for i in range(MISSED_HEADERS_WARN_COUNT)]
    findings = lint_missed_headers(
        many, _STAGE1A, {"entries": [_entry(_GRANT_FSMB, hierarchy=["GRANTS"])]})
    assert len(findings) == MISSED_HEADERS_WARN_COUNT
    assert {f["severity"] for f in findings} == {"WARN"}, \
        "severity is a property of the run, so every finding carries it"


def test_missed_headers_quiet_when_header_is_known():
    # Present in the 1a hierarchy.
    assert lint_missed_headers(["GRANTS"], _STAGE1A, {"entries": []}) == []
    # Absent from 1a but present in an entry hierarchy path.
    stage2 = {"entries": [_entry("Mentee record line long enough",
                                 hierarchy=["MENTORING"])]}
    assert lint_missed_headers(["MENTORING"], {"hierarchy": []}, stage2) == []


# ------------------------------------------------------- lint 3: bucket/status

def test_bucket_status_fires_on_pending_grant_rendered_as_current():
    # Status is read from the raw entry text (stage 4 extracts no 'status'
    # field for M2*), and the WARN is about where the grant RENDERED.
    stage4 = {"entries": [_grant4(_GRANT_TEMPLETON, "M2A")]}
    blocks = _funding_blocks(current=[_GRANT_TEMPLETON])
    findings = lint_bucket_status(stage4, blocks)
    assert len(findings) == 1
    assert findings[0]["severity"] == "WARN"
    assert "M2C" in findings[0]["message"]
    assert "M2A" in findings[0]["message"]


def test_bucket_status_quiet_when_stage6_rebucketed():
    # The #214 render-time rebucket moved the grant to Pending: no finding,
    # whatever the stage-4 bucket says.
    stage4 = {"entries": [_grant4(_GRANT_TEMPLETON, "M2A")]}
    blocks = _funding_blocks(pending=[_GRANT_TEMPLETON])
    assert lint_bucket_status(stage4, blocks) == []


def test_bucket_status_fires_when_grant_dropped_from_funding():
    stage4 = {"entries": [_grant4(_GRANT_NBME, "M2A")]}
    blocks = _funding_blocks(current=[_GRANT_FSMB])
    findings = lint_bucket_status(stage4, blocks)
    assert len(findings) == 1
    assert "no funding heading" in findings[0]["message"]


def test_bucket_status_precedence_matches_stage6_rules():
    # "Not Funded" wins over the "Submitted" it contains.
    nbme = _GRANT_NBME.replace("Status: Not funded",
                               "Status: Submitted 2025-2026, Not Funded")
    not_funded = lint_bucket_status({"entries": [_grant4(nbme, "M2A")]},
                                    _funding_blocks(current=[nbme]))
    assert len(not_funded) == 1 and "M2C" in not_funded[0]["message"]
    # Completed demotes M2A to M2B.
    fsmb = _GRANT_FSMB.replace("Status: Awarded 2026", "Status: Completed 2023")
    completed = lint_bucket_status({"entries": [_grant4(fsmb, "M2A")]},
                                   _funding_blocks(current=[fsmb]))
    assert len(completed) == 1 and "M2B" in completed[0]["message"]


def test_bucket_status_quiet_on_agreement_and_award_guard():
    no_status = _GRANT_FSMB.split(" | Status")[0]
    pending_contract = _GRANT_FSMB.replace("Status: Awarded 2026",
                                           "Status: Awarded, pending contract")
    stage4 = {"entries": [
        _grant4(_GRANT_FSMB, "M2A"),           # Awarded: no move implied
        _grant4(pending_contract, "M2A"),      # award guard beats 'pending'
        _grant4(no_status, "M2A"),             # no status at all
        _grant4(_GRANT_TEMPLETON, "M2C"),      # already in the target bucket
        _entry("Peer-reviewed article citation text", taxonomy_code="S1"),
    ]}
    blocks = _funding_blocks(current=[_GRANT_FSMB, pending_contract, no_status],
                             pending=[_GRANT_TEMPLETON])
    assert lint_bucket_status(stage4, blocks) == []


# ----------------------------------------------------- lint 4: under-extraction

def test_under_extraction_fires_on_low_coverage_mega_entry():
    text = "\n".join([_GRANT_FSMB, _GRANT_TEMPLETON, _GRANT_NBME] * 3)
    assert len(text) > 800
    stage4 = {"entries": [_grant4(text, "M2A", pct=14.9, start=30)]}
    findings = lint_under_extraction(stage4)
    assert len(findings) == 1
    assert findings[0]["severity"] == "WARN"
    assert "14.9" in findings[0]["message"]


def test_under_extraction_quiet_on_healthy_entries():
    big = "\n".join([_GRANT_FSMB, _GRANT_TEMPLETON, _GRANT_NBME] * 3)
    stage4 = {"entries": [
        _grant4(big, "M2A", pct=85.0),                # good coverage
        _grant4(_GRANT_FSMB, "M2A", pct=10.0),        # low coverage, small entry
        _grant4("x" * 900, "M2A", pct=10.0),          # big but no record lines
    ]}
    assert lint_under_extraction(stage4) == []


# ---------------------------------------------------------------- docx reading

def test_read_docx_blocks_body_order_paragraphs_and_tables(tmp_path):
    doc = Document()
    doc.add_paragraph("D. GRANTS")
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].paragraphs[0].text = "First cell content"
    table.rows[0].cells[1].paragraphs[0].text = "Second cell content"
    doc.add_paragraph("T. APPENDIX")
    path = tmp_path / "out.docx"
    doc.save(path)

    blocks = read_docx_blocks(str(path))
    kinds = [k for k, _ in blocks]
    assert kinds == ["p", "table", "p"]
    assert blocks[0][1] == "D. GRANTS"
    assert "First cell content" in blocks[1][1]
    assert "Second cell content" in blocks[1][1]


def test_read_docx_blocks_joins_rows_and_recurses_nested_tables(tmp_path):
    # Mirror of stage 6's _rendered_output_lines() table walk (PR #225): a
    # multi-cell row is ALSO emitted joined as one line, and nested tables
    # (invisible to python-docx cell.text) are recursed into.
    doc = Document()
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "First cell content"
    table.rows[0].cells[1].text = "Second cell content"
    host = doc.add_table(rows=1, cols=1)
    nested = host.rows[0].cells[0].add_table(rows=1, cols=1)
    nested.rows[0].cells[0].text = "Nested table content line"
    path = tmp_path / "out.docx"
    doc.save(path)

    blocks = read_docx_blocks(str(path))
    assert [k for k, _ in blocks] == ["table", "table"]
    assert "First cell content | Second cell content" in blocks[0][1].split("\n")
    assert "Nested table content line" in blocks[1][1].split("\n")


# ------------------------------------------------ lint 5: classified-unrendered

def test_classified_unrendered_fires_when_code_vanishes():
    stage3b = {"entries": [_entry(_GRANT_FSMB, taxonomy_code="M2A", start=1),
                           _entry(_GRANT_NBME, taxonomy_code="M2A", start=2)]}
    blocks = [("p", "D. GRANTS"), ("p", "Completely unrelated output content")]
    findings = lint_classified_unrendered(stage3b, blocks)
    assert len(findings) == 1
    assert findings[0]["severity"] == "WARN"
    assert "M2A" in findings[0]["message"]
    assert findings[0]["evidence"]


def test_classified_unrendered_quiet_when_rendered_in_a_table(tmp_path):
    doc = Document()
    doc.add_paragraph("D. GRANTS")
    table = doc.add_table(rows=1, cols=1)
    table.rows[0].cells[0].paragraphs[0].text = _GRANT_FSMB
    path = tmp_path / "out.docx"
    doc.save(path)

    stage3b = {"entries": [
        _entry(_GRANT_FSMB, taxonomy_code="M2A", start=1),
        # 'T' catch-all is skipped even when absent from the output.
        _entry("Some leftover miscellaneous content line", taxonomy_code="T"),
        # Too short to verify: skipped rather than flagged.
        _entry("Short one", taxonomy_code="K1"),
    ]}
    assert lint_classified_unrendered(stage3b, read_docx_blocks(str(path))) == []


def test_classified_unrendered_quiet_when_reformatted_downstream():
    # Stage 5d re-renders citations from extracted fields: no >=15-char
    # verbatim piece of the 3b text survives, but the distinctive tokens do.
    raw = ("Epigenetic Regulation of Tumor Suppressor Genes in Breast Cancer | "
           "Quimby F, Farrow C, Blackwell D | Journal of Synthetic Oncology | 2024")
    rendered = ("Quimby F., Farrow C., Blackwell D. Epigenetic regulation of "
                "tumor-suppressor genes in breast cancer. J Synth Oncol. 2024.")
    stage3b = {"entries": [_entry(raw, taxonomy_code="S1", start=4)]}
    blocks = [("p", "BIBLIOGRAPHY"), ("p", rendered)]
    assert lint_classified_unrendered(stage3b, blocks) == []


# --------------------------------------------------- lint: taxonomy code coverage

def test_taxonomy_code_coverage_fires_for_a_code_with_no_render_route():
    # N2 was #529's original example and M4 the next (#291 gave both a
    # route), so this uses N3, the parent container code still in
    # test_taxonomy_code_render_coverage.py's _KNOWN_GAPS.
    stage3b = {"entries": [
        _entry("Mentored an invented student", taxonomy_code="N3", start=1),
        _entry("Mentored an invented fellow", taxonomy_code="N3", start=2),
    ]}
    findings = lint_taxonomy_code_coverage(stage3b)
    assert len(findings) == 1
    # #816: always INFO now -- the unrouted-code counts moved to the
    # doctor's `metrics` block (unrouted_code_entries).
    assert findings[0]["severity"] == "INFO"
    assert "N3" in findings[0]["message"]
    assert "2 entries" in findings[0]["message"]


def test_unrouted_code_counts_matches_the_lints_own_by_code_dict():
    """#816: the doctor's `metrics` block reads this SAME dict the lint
    above builds its findings from. A retired M4 code is NOT unrouted: stage
    6 renders it as M2A (#291), so it must not be reported as Appendix-bound."""
    stage3b = {"entries": [
        _entry("Mentored an invented student", taxonomy_code="N3", start=1),
        _entry("Mentored an invented fellow", taxonomy_code="N3", start=2),
        _entry("Another orphan code", taxonomy_code="ZZ", start=3),
        _entry("A stored clinical trial", taxonomy_code="M4A", start=4),
        _entry("A grant", taxonomy_code="M2A", start=5),
    ]}
    assert unrouted_code_counts(stage3b) == {"N3": 2, "ZZ": 1}
    assert unrouted_code_counts({"entries": []}) == {}


def test_taxonomy_code_coverage_quiet_for_a_routed_code():
    stage3b = {"entries": [_entry("A grant", taxonomy_code="M2A", start=1)]}
    assert lint_taxonomy_code_coverage(stage3b) == []


def test_taxonomy_code_coverage_does_not_flag_t_or_headers_or_breaks():
    stage3b = {"entries": [
        _entry("Leftover miscellaneous content", taxonomy_code="T", start=1),
        _entry("A HEADER", taxonomy_code="N2", etype="header", start=2),
        _entry("", taxonomy_code="N2", etype="break", start=3),
    ]}
    assert lint_taxonomy_code_coverage(stage3b) == []


def test_taxonomy_code_coverage_does_not_flag_m1_the_common_routed_case():
    # M1 is only unrouted when the Stage 4.5 summary itself didn't render
    # (#317) -- a distinct, already-covered case. This lint must not
    # false-positive the ordinary path where it did.
    stage3b = {"entries": [_entry("Research summary text", taxonomy_code="M1", start=1)]}
    assert lint_taxonomy_code_coverage(stage3b) == []


def test_taxonomy_code_coverage_does_not_flag_rendered_passthrough_codes_or_n4():
    # E and G render via the passthrough writer's own heading match (not the
    # RENDER_ROUTED_CODES lookup this lint checks), so the lint exempts them;
    # N4 is in RENDER_ROUTED_CODES since #587. Flagging any of them here would
    # call a rendered code "no render route at all".
    stage3b = {"entries": [
        _entry("Weill Cornell Medicine", taxonomy_code="G", start=1),
        _entry("Full-time", taxonomy_code="E", start=2),
        _entry("Many trainees secured faculty positions", taxonomy_code="N4", start=3),
    ]}
    assert lint_taxonomy_code_coverage(stage3b) == []


# --------------------------------------------------------- lint 6: output hygiene

def test_output_hygiene_flags_bracket_code_leaks():
    blocks = [("p", "• [M2A] Federation grant text left in the appendix"),
              ("table", "cell text with a [K1] code")]
    findings = lint_output_hygiene(blocks)
    assert len(findings) == 1
    assert findings[0]["severity"] == "ERROR"
    assert "2" in findings[0]["message"]
    assert any("[M2A]" in line for line in findings[0]["evidence"])


def test_output_hygiene_ignores_source_bracket_tokens_that_are_not_codes():
    # #888: a source-CV bracketed acronym must not be an ERROR.
    blocks = [("p", "Invented Kelp Study for Nowhere [K9P]; pilot trial"),
              ("table", "Sensor [CO2] and [AI] tools in [UK] near [X7Z]")]
    assert lint_output_hygiene(blocks) == []


def test_output_hygiene_flags_real_code_next_to_source_token():
    blocks = [("p", "Invented Kelp Study [K9P] then [D1] leaked")]
    findings = lint_output_hygiene(blocks)
    assert len(findings) == 1
    assert findings[0]["severity"] == "ERROR"
    assert "1 bracketed" in findings[0]["message"]


def test_output_hygiene_flags_retired_invalid_codes():
    # The `invalid_codes` list in taxonomy_v7.json is not in `codes`; the lint
    # must still flag it (guards `codes.update(taxonomy["invalid_codes"]...)`).
    for code in ("S10", "Q5"):
        findings = lint_output_hygiene([("p", f"[{code}] leaked")])
        assert len(findings) == 1, code
        assert findings[0]["severity"] == "ERROR"


def test_output_hygiene_flags_every_taxonomy_code_shape():
    codes = ["A", "B1", "D1", "K5", "M2A", "M4C", "N2", "Q4D", "S0", "T"]  # M4C: retired (#291), still a leak
    findings = lint_output_hygiene([("p", f"• [{c}] leaked") for c in codes])
    assert findings[0]["severity"] == "ERROR"
    assert f"{len(codes)} bracketed" in findings[0]["message"]


def test_output_hygiene_flags_boilerplate_in_appendix():
    blocks = [
        ("p", "T. APPENDIX"),
        ("p", "These entries from your original CV could not be matched to a section of the WCM format."),
        ("p", "• CURRICULUM VITAE"),
        ("p", "1. Page 2 of 9"),
        ("p", "• Real leftover grant content | Role: PI | Amount: $10,000"),
    ]
    findings = lint_output_hygiene(blocks)
    boiler = [f for f in findings if "boilerplate" in f["message"]]
    assert len(boiler) == 1
    assert boiler[0]["severity"] == "WARN"
    assert boiler[0]["message"].startswith("2 ")
    count = next(f for f in findings if "appendix holds" in f["message"])
    assert count["severity"] == "INFO"
    assert "3" in count["message"]


def test_output_hygiene_appendix_count_stays_info_regardless_of_size():
    """#816: the appendix-size threshold was retired -- this finding is
    always INFO now, and the count moves to the doctor's `metrics` block
    (appendix_entries/appendix_share) instead of a per-run WARN."""
    bullets = [("p", f"• Unmapped leftover entry with descriptive text number {i}")
               for i in range(20)]
    blocks = [("p", "T. APPENDIX"), ("p", "The following content:")] + bullets
    count = next(f for f in lint_output_hygiene(blocks)
                 if "appendix holds" in f["message"])
    assert count["severity"] == "INFO"
    assert "20" in count["message"]


def test_output_hygiene_flags_a_non_paragraph_block_inside_the_appendix():
    """#725 review r3923589271 pt 9: the paragraph-only appendix invariant
    used to be documented, not enforced -- the lint dropped every
    non-paragraph block before locating and scanning the appendix, so a
    table landing inside the appendix range was silently invisible to it.
    Measured over the 65-doc farm none actually does this today, but a
    table that DOES land there now gets its own finding instead of being
    dropped."""
    blocks = [
        ("p", "T. APPENDIX"),
        ("p", "These entries from your original CV could not be matched to a section of the WCM format."),
        ("table", "cell text that never gets scanned as an appendix entry"),
        ("p", "• Real leftover grant content | Role: PI | Status: Under review"),
    ]
    findings = lint_output_hygiene(blocks)
    non_paragraph = [f for f in findings if "non-paragraph" in f["message"]]
    assert len(non_paragraph) == 1
    assert non_paragraph[0]["severity"] == "WARN"
    assert non_paragraph[0]["message"].startswith("1 ")


def test_output_hygiene_quiet_on_clean_output():
    blocks = [
        ("p", "D. GRANTS"),
        ("table", _GRANT_FSMB),
        ("p", "T. APPENDIX"),
        ("p", "These entries from your original CV could not be matched to a section of the WCM format."),
        ("p", "• Real leftover grant content | Role: PI | Status: Under review"),
    ]
    findings = lint_output_hygiene(blocks)
    assert all(f["severity"] == "INFO" for f in findings)


def test_output_hygiene_warns_on_foreign_template_scaffolding_in_the_appendix():
    """#530: another institution's instruction line rendered as an appendix
    entry is boilerplate, same as the WCM template's own (invented text)."""
    blocks = [
        ("p", "T. APPENDIX"),
        ("p", "These entries from your original CV could not be matched to a section of the WCM format."),
        ("p", "1. C. Sample Appointments (include institution, title and dates of appointment)"),
        ("p", "2. Real leftover grant content"),
    ]
    findings = lint_output_hygiene(blocks)
    boiler = [f for f in findings if "boilerplate line" in f["message"]]
    assert len(boiler) == 1
    assert boiler[0]["severity"] == "WARN"
    assert boiler[0]["message"].startswith("1 ")


def test_dead_sections_ignores_foreign_template_scaffolding_under_the_header():
    """#530: a foreign template's instruction line does not make a section
    count as alive, same as the WCM template's own (invented text)."""
    scaffolded = [("p", "GRANTS"),
                  ("p", "1. Sample Sabbatical Leave Arrangements: N/A")]
    assert lint_dead_sections(_STAGE2_GRANTS, scaffolded)


def test_appendix_entry_count_matches_the_lints_own_count():
    blocks = [
        ("p", "T. APPENDIX"),
        ("p", "The following content:"),
        ("p", "• one"), ("p", "• two"), ("p", "• three"),
    ]
    assert appendix_entry_count(blocks) == 3


def test_appendix_entry_count_is_none_with_no_appendix_section():
    """None (not 0) when the document has no appendix at all -- distinct
    from an appendix that exists and is empty (#816)."""
    assert appendix_entry_count([("p", "D. GRANTS")]) is None


# ---------------------------------------------------------- lint 7: dead sections

_STAGE2_GRANTS = {"entries": [_entry(_GRANT_FSMB, start=1),
                              _entry(_GRANT_TEMPLETON, start=2),
                              _entry(_GRANT_NBME, start=3)]}


def test_dead_sections_fires_on_empty_matched_section():
    blocks = [("p", "D. GRANTS"), ("p", "T. APPENDIX"),
              ("p", "The following content from the original CV was not mapped:")]
    findings = lint_dead_sections(_STAGE2_GRANTS, blocks)
    assert len(findings) == 1
    assert findings[0]["severity"] == "WARN"
    assert "grants" in findings[0]["message"]
    assert "D. GRANTS" in findings[0]["message"]


def test_dead_sections_recognizes_plain_uppercase_headers():
    # Real stage-6 output uses plain uppercase section headers ('RESEARCH',
    # 'MENTORING'), not the letter-prefixed form.
    blocks = [("p", "GRANTS"), ("p", "T. APPENDIX"),
              ("p", "The following content from the original CV was not mapped:")]
    findings = lint_dead_sections(_STAGE2_GRANTS, blocks)
    assert len(findings) == 1
    assert "GRANTS" in findings[0]["message"]
    # Template instruction scaffolding under the header does not make the
    # section count as alive.
    scaffolded = [("p", "GRANTS"),
                  ("p", "Use the subsection below to record Research Support."),
                  ("p", "List IRB protocols (both active and inactive) here.")]
    assert lint_dead_sections(_STAGE2_GRANTS, scaffolded)
    # Real rendered content under the plain header does.
    filled = [("p", "GRANTS"), ("table", _GRANT_FSMB)]
    assert lint_dead_sections(_STAGE2_GRANTS, filled) == []


def test_dead_sections_quiet_when_section_has_content_or_no_match():
    # Content in a table under the matched section.
    filled = [("p", "D. GRANTS"), ("table", _GRANT_FSMB)]
    assert lint_dead_sections(_STAGE2_GRANTS, filled) == []
    # No name-matched output section at all: too fuzzy to call dead.
    unmatched = [("p", "C. RESEARCH FUNDING")]
    assert lint_dead_sections(_STAGE2_GRANTS, unmatched) == []


# --------------------------------------------------- lint 8: unrendered records

# Five synthetic pipe-delimited grant rows: token-distinct titles so an absent
# row can't reach the 0.7 overlap through its rendered siblings.
_ROW_HARBORVIEW = ("Harborview Medical Simulation Grant | Tanaka, R. (PI), "
                   "Whitfield, P. (Co-PI) | Role: Co-PI | Amount: $80,000 | "
                   "Status: Awarded 2023")
_ROW_BLUERIDGE = ("Blue Ridge Educational Technology Award | Okafor, C. (PI) | "
                  "Role: PI | Amount: $45,000 | Status: Completed 2022")
_ROW_CEDARBROOK = ("Cedarbrook Curriculum Innovation Fund | Marchetti, L. (PI) | "
                   "Role: Co-Investigator | Amount: $30,000 | Status: Awarded 2024")
_ROW_SILVERLAKE = ("Silverlake Assessment Consortium Grant | Petrov, D. (PI) | "
                   "Role: Co-PI | Amount: $65,000 | Status: Under review 2025")
_ROW_FOXGLOVE = ("Foxglove Interprofessional Training Grant | Nakamura, S. (PI) | "
                 "Role: PI | Amount: $120,000 | Status: Submitted 2025")

_CITE_RAW = ("Epigenetic Regulation of Tumor Suppressor Genes in Breast Cancer | "
             "Quimby F, Farrow C, Blackwell D | Journal of Synthetic Oncology | 2024")
_CITE_RENDERED = ("Quimby F., Farrow C., Blackwell D. Epigenetic regulation of "
                  "tumor-suppressor genes in breast cancer. J Synth Oncol. 2024.")


def test_unrendered_records_fires_on_dropped_remainder():
    # The #221 shape: five records fused into one entry, stage 6 rendered
    # three and silently dropped two (no bullet fallback).
    fused = "\n".join([_ROW_HARBORVIEW, _ROW_BLUERIDGE, _ROW_CEDARBROOK,
                       _ROW_SILVERLAKE, _ROW_FOXGLOVE])
    stage4 = {"entries": [_entry(fused, etype="break", start=21,
                                 taxonomy_code="M2A")]}
    blocks = [("p", "RESEARCH"), ("table", _ROW_HARBORVIEW),
              ("table", _ROW_BLUERIDGE), ("table", _ROW_CEDARBROOK)]
    findings = lint_unrendered_records(stage4, blocks)
    assert len(findings) == 1
    assert findings[0]["severity"] == "WARN"
    assert "entry 21 (M2A): 2 of 5 records absent" in findings[0]["message"]
    assert findings[0]["evidence"] == [_ROW_SILVERLAKE[:100], _ROW_FOXGLOVE[:100]]


def test_unrendered_records_quiet_when_all_rendered_incl_reformatted():
    # One record survives verbatim (table cell); the other only via token
    # overlap because 5d re-rendered the citation from extracted fields.
    fused = _ROW_HARBORVIEW + "\n" + _CITE_RAW
    stage4 = {"entries": [_entry(fused, start=5, taxonomy_code="S1")]}
    blocks = [("table", _ROW_HARBORVIEW), ("p", _CITE_RENDERED)]
    assert lint_unrendered_records(stage4, blocks) == []


def test_unrendered_records_skips_single_record_and_appendix_entries():
    blocks = [("p", "D. GRANTS"), ("table", _ROW_HARBORVIEW)]
    # One record line: not a fused candidate, even though it never rendered.
    single = _entry(_ROW_SILVERLAKE + "\nNarrative description of the award.",
                    start=7, taxonomy_code="M2C")
    # 'T' catch-all is skipped whatever its shape.
    fused_t = _entry(_ROW_SILVERLAKE + "\n" + _ROW_FOXGLOVE,
                     start=9, taxonomy_code="T")
    assert lint_unrendered_records({"entries": [single, fused_t]}, blocks) == []


# A record whose tokens split across the cells of ONE structured table row:
# no pipe-fragment squashes to a >=15-char verbatim piece and no single CELL
# holds 3+ distinctive tokens, so only the row read as a whole can prove it
# rendered — the 2054 shape (grant label/value tables) lint 8 false-flagged
# until it mirrored stage 6's row-joined _rendered_output_lines() view.
_ROW_SPLITTABLE = "Ruby Grant | Asthma study | Vasquez lab | Peds wing | Bronx site | 2021-2024"


def test_unrendered_records_quiet_when_record_splits_across_row_cells(tmp_path):
    doc = Document()
    doc.add_paragraph("D. GRANTS")
    doc.add_paragraph(_ROW_HARBORVIEW)
    table = doc.add_table(rows=1, cols=6)
    for i, cell_text in enumerate(_ROW_SPLITTABLE.split(" | ")):
        table.rows[0].cells[i].text = cell_text
    path = tmp_path / "out.docx"
    doc.save(path)

    fused = _ROW_SPLITTABLE + "\n" + _ROW_HARBORVIEW
    stage4 = {"entries": [_entry(fused, start=12, taxonomy_code="M2A")]}
    assert lint_unrendered_records(stage4, read_docx_blocks(str(path))) == []


def test_unrendered_records_quiet_when_record_rendered_in_nested_table(tmp_path):
    # Grants render as one Word table per grant, sometimes nested inside a
    # layout table — cell.text never surfaces nested-table text, so the walk
    # must recurse (mirroring stage 6's _rendered_output_lines()).
    doc = Document()
    doc.add_paragraph("D. GRANTS")
    doc.add_paragraph(_ROW_HARBORVIEW)
    host = doc.add_table(rows=1, cols=1)
    nested = host.rows[0].cells[0].add_table(rows=1, cols=1)
    nested.rows[0].cells[0].text = _ROW_BLUERIDGE
    path = tmp_path / "out.docx"
    doc.save(path)

    fused = _ROW_BLUERIDGE + "\n" + _ROW_HARBORVIEW
    stage4 = {"entries": [_entry(fused, start=33, taxonomy_code="M2B")]}
    assert lint_unrendered_records(stage4, read_docx_blocks(str(path))) == []


def test_unrendered_records_counts_generic_lines_unverifiable_not_missing():
    # Date-prefixed record lines whose payload carries too few distinctive
    # tokens ("Assistant Professor") cannot be proven absent: no finding.
    fused = ("Jun 2020-Jun 2025, Assistant Professor\n"
             "Sep 2015-Sep 2020, Research Fellow")
    stage4 = {"entries": [_entry(fused, etype="break", start=21,
                                 taxonomy_code="D1")]}
    blocks = [("p", "D. GRANTS"), ("table", _ROW_HARBORVIEW)]
    assert lint_unrendered_records(stage4, blocks) == []


# ------------------------------------------------------ lint 8b: section lost

# Synthetic leadership records: every token distinct from the rest of the page.
_LEAD_ROWS = ("Founding Director, Quillfeather Cellular Therapeutics Institute\t1998-2014",
              "Chairman, Marbleton Steering Committee on Genomic Medicine\t1992-1997")


def _stage4(*pairs):
    return {"entries": [{"taxonomy_code": code, "text": text} for code, text in pairs]}


def _page(*sections):
    """Blocks for a WCM render: (heading, [paragraph texts]) per section."""
    return [block for heading, texts in sections
            for block in [("p", heading)] + [("p", t) for t in texts]]


_FILLER = ("EDUCATION", ["Bachelor of Science, Hollowbrook University, 1985"])


def test_section_lost_fires_when_section_is_empty_but_words_render_elsewhere():
    # YME2VA: the leadership section held only its instruction paragraph,
    # while the same words turned up in lectures -- whole-document matching
    # (lint 8) called that rendered.
    blocks = _page(_FILLER,
                   ("INSTITUTIONAL LEADERSHIP ACTIVITIES", ["Please list activities."]),
                   ("INVITATIONS TO SPEAK/PRESENT",
                    ["Quillfeather Cellular Therapeutics Institute lecture, Marbleton Genomic Medicine forum"]))
    findings = lint_section_lost(_stage4(*[("O", t) for t in _LEAD_ROWS]), blocks)
    assert [f["lint"] for f in findings] == ["section_lost"]
    assert findings[0]["message"].startswith("O: 2 entries absent from the INSTITUTIONAL LEADERSHIP")


def test_section_lost_fires_when_records_render_only_in_the_appendix():
    # BYFQBG's N2 training grants rendered, but in the Appendix.
    blocks = _page(_FILLER, ("INSTITUTIONAL LEADERSHIP ACTIVITIES", []),
                   ("T. APPENDIX", list(_LEAD_ROWS)))
    assert lint_section_lost(_stage4(*[("O", t) for t in _LEAD_ROWS]), blocks)


def test_section_lost_quiet_when_records_render_in_their_section():
    blocks = _page(_FILLER, ("INSTITUTIONAL LEADERSHIP ACTIVITIES", list(_LEAD_ROWS)))
    assert lint_section_lost(_stage4(*[("O", t) for t in _LEAD_ROWS]), blocks) == []


def test_section_lost_quiet_when_half_the_role_line_renders_without_its_description():
    # 4N14RQ: the role renders as a table row, but the long description has
    # no slot in the table. Half the first line's tokens (the floor) are in
    # the section, under the per-entry hit floor, so only the first-line
    # test keeps this quiet.
    entry = ("Chief Wexcombe\n"
             "Scope: oversaw scheduling, onboarding, curriculum, wellness, "
             "recruitment, grievances, orientation, quality dashboards, "
             "handoffs, simulation, mentoring, budgeting, conferences.")
    blocks = _page(_FILLER, ("INSTITUTIONAL LEADERSHIP ACTIVITIES", ["Chief | 2022-2023"]))
    assert lint_section_lost(_stage4(("O", entry)), blocks) == []


def test_section_lost_one_token_first_line_cannot_vouch():
    entry = "Quillfeather\nMarbleton Brambleton Ostrander Pellgrave Halloway Tollbridge"
    blocks = _page(_FILLER, ("INSTITUTIONAL LEADERSHIP ACTIVITIES", ["Quillfeather"]))
    assert lint_section_lost(_stage4(("O", entry)), blocks)


def test_section_lost_quiet_when_one_entry_has_exactly_the_hit_floor_in_section():
    # Title reworded at render (first-line test fails) and a long body
    # (share under 0.25), but exactly three of the entry's own tokens are in
    # the section: rendered, not lost.
    entry = ("Wexcombe Pellgrave Ostrander Brambleton\n"
             "Quorvale Zephyrine Halloway: " + ", ".join(
                 ["planning", "staffing", "reporting", "auditing", "training",
                  "hiring", "budgets", "outreach", "grants", "policy", "surveys",
                  "metrics", "forums", "retreats", "bylaws"]))
    blocks = _page(_FILLER, ("INSTITUTIONAL LEADERSHIP ACTIVITIES",
                             ["Director, Quorvale Zephyrine Halloway"]))
    assert lint_section_lost(_stage4(("O", entry)), blocks) == []


def test_section_lost_quiet_when_code_share_is_high_but_spread_thin():
    # Each entry has only 2 of its 5 tokens in the section (under the hit
    # floor, and under half its first line), but together 6 of 15: above
    # the share threshold.
    entries = [("P", "Alderwood Quarry Marston Fennick Oakhollow"),
               ("P", "Birchmoor Quarry Marston Tollbridge Heatherly"),
               ("P", "Cresswell Quarry Marston Lintwhite Brackenby")]
    blocks = _page(_FILLER, ("INSTITUTIONAL ADMINISTRATIVE ACTIVITIES",
                             ["Alderwood Fennick", "Birchmoor Tollbridge", "Cresswell Lintwhite"]))
    assert lint_section_lost(_stage4(*entries), blocks) == []


def test_section_lost_quiet_at_exactly_the_share_threshold():
    # 2 of 8 tokens (0.25) present: the threshold is strict.
    entries = [("P", "Alderwood Quarry Marston Oakhollow"),
               ("P", "Birchmoor Heatherly Tollbridge Lintwhite")]
    blocks = _page(_FILLER, ("INSTITUTIONAL ADMINISTRATIVE ACTIVITIES",
                             ["Alderwood", "Birchmoor"]))
    assert lint_section_lost(_stage4(*entries), blocks) == []


def test_section_lost_leaves_codes_of_only_short_entries_unjudged():
    # Six tokens in all, but no entry has the 3 the per-entry test needs.
    entries = [("O", "Alderwood Quarry"), ("O", "Birchmoor Heatherly"),
               ("O", "Cresswell Lintwhite")]
    blocks = _page(_FILLER, ("INSTITUTIONAL LEADERSHIP ACTIVITIES", []))
    assert lint_section_lost(_stage4(*entries), blocks) == []


def test_section_lost_ignores_codes_outside_the_taxonomy_letters():
    blocks = _page(_FILLER, ("INSTITUTIONAL LEADERSHIP ACTIVITIES", []))
    assert lint_section_lost(_stage4(*[("X9", t) for t in _LEAD_ROWS]), blocks) == []


def test_section_lost_keeps_the_section_across_its_sub_headings():
    blocks = _page(_FILLER, ("MENTORING", []), ("PAST MENTEES", list(_LEAD_ROWS)))
    assert lint_section_lost(_stage4(*[("N3B", t) for t in _LEAD_ROWS]), blocks) == []


def test_section_lost_leaves_codes_too_thin_to_judge():
    # 5 distinctive tokens: under SECTION_LOST_MIN_TOKENS, not judged.
    blocks = _page(_FILLER, ("INSTITUTIONAL LEADERSHIP ACTIVITIES", []))
    entry = "Zephyrine Quorvale Brambleton Ostrander Wexcombe"
    assert lint_section_lost(_stage4(("O", entry)), blocks) == []
    assert lint_section_lost(_stage4(("O", entry + " Pellgrave")), blocks)


def test_section_lost_discounts_template_labels_every_section_carries():
    # One-word template labels ('Administrative', 'Awards') sit in every
    # render; counted, their 3 hits would pass an entry whose own record is gone.
    entry = ("Zephyrine Quorvale Brambleton Ostrander Wexcombe Pellgrave\n"
             "Administrative Awards Abstracts")
    blocks = _page(_FILLER, ("INSTITUTIONAL LEADERSHIP ACTIVITIES",
                             ["Administrative Awards Abstracts"]))
    assert lint_section_lost(_stage4(("O", entry)), blocks)


def test_section_lost_skips_withheld_summary_and_appendix_codes():
    blocks = _page(_FILLER, ("PERSONAL DATA", []), ("RESEARCH", []), ("T. APPENDIX", []))
    stage4 = _stage4(*[(code, t) for code in ("A", "M1", "T") for t in _LEAD_ROWS])
    assert lint_section_lost(stage4, blocks) == []


def test_section_lost_reads_c_under_education_and_k_under_educational_contributions():
    # EDUCATION is a prefix of EDUCATIONAL CONTRIBUTIONS: a K record rendered
    # there must not be charged to EDUCATION, nor a C record to K.
    postdoc = "Postdoctoral Fellowship, Wrenfield Oncology Laboratories, Caldermoor"
    course = "Course Director, Brightwater Pharmacology Seminar Sequence, Caldermoor"
    blocks = _page(("EDUCATION", [postdoc]), ("EDUCATIONAL CONTRIBUTIONS", [course]))
    assert lint_section_lost(_stage4(("C", postdoc), ("K1", course)), blocks) == []
    swapped = _page(("EDUCATION", [course]), ("EDUCATIONAL CONTRIBUTIONS", [postdoc]))
    assert {f["message"][:2] for f in lint_section_lost(
        _stage4(("C", postdoc), ("K1", course)), swapped)} == {"C:", "K1"}


# -------------------------------------------------- lint 9: enrichment failures

def _enriched_entry(text, status=None):
    e = {"text": text, "element_type": "paragraph", "taxonomy_code": "S1"}
    if status:
        e["enrichment_status"] = status
    return e


def test_enrichment_failures_fires_with_counts_by_status():
    entries = [
        _enriched_entry("Rivera T. (2024). Adaptive tutoring in clinical "
                        "reasoning. J Synth Med Educ.", "doi_found_but_fetch_failed"),
        _enriched_entry("Okafor C. (2023). Simulation debriefing at scale. "
                        "Ann Fict Acad Med.", "doi_found_but_fetch_failed"),
        _enriched_entry("Petrov D. (2022). Rubric drift in OSCE scoring. "
                        "Clin Educ Quarterly.", "lookup_failed"),
        _enriched_entry("Nakamura S. (2021). Feedback literacy.", "enriched"),
        _enriched_entry("Marchetti L. (2020). Cohort attrition.", "no_identifier"),
        _enriched_entry("Tanaka R. (2019). Preprint culture.", "doi_not_in_pubmed"),
        _enriched_entry("A non-publication entry with no status at all"),
    ]
    findings = lint_enrichment_failures({"entries": entries})
    assert len(findings) == 1
    assert findings[0]["severity"] == "WARN"
    assert findings[0]["message"].startswith("3 publication(s) failed")
    assert "doi_found_but_fetch_failed: 2" in findings[0]["message"]
    assert "lookup_failed: 1" in findings[0]["message"]
    assert "#222" in findings[0]["message"]
    assert len(findings[0]["evidence"]) == 3
    assert findings[0]["evidence"][0].startswith("Rivera T.")


def test_enrichment_failures_quiet_on_non_failure_statuses():
    entries = [_enriched_entry("Nakamura S. (2021). Feedback literacy.", "enriched"),
               _enriched_entry("Marchetti L. (2020). Cohort attrition.", "no_identifier"),
               _enriched_entry("Tanaka R. (2019). Preprint culture.", "doi_not_in_pubmed"),
               _enriched_entry("No status entry")]
    assert lint_enrichment_failures({"entries": entries}) == []


def test_enrichment_failures_skips_a_citation_stage5_restored_from_a_shared_pmid():
    """RCBKFG JJUQDF 93: another entry kept the shared PMID, so stage 5 put
    this entry's own citation back; that is right, not a failure."""
    restored = _enriched_entry("Okafor C. (2023). Simulation debriefing at scale.",
                               "title_check_failed")
    restored["enrichment_rejected"] = {"source": "doi_search", "shared_pmid_with": 91}
    assert lint_enrichment_failures({"entries": [restored]}) == []
    failed = _enriched_entry("Petrov D. (2022). Rubric drift.", "title_check_failed")
    failed["enrichment_rejected"] = {"source": "doi_search", "shared_pmid_with": None}
    findings = lint_enrichment_failures({"entries": [restored, failed]})
    assert [f["message"][:31] for f in findings] == ["1 publication(s) failed PubMed "]


def test_enrichment_failures_missing_artifact_info_skip(tmp_path):
    root = _build_clean_run(tmp_path)
    next((root / "stage_5_enrichment").glob("*.json")).unlink()
    payload = run_doctor(root, _UID)
    skips = [f for f in payload["findings"] if f["lint"] == "enrichment_failures"]
    assert len(skips) == 1
    assert skips[0]["severity"] == "INFO"
    assert "stage_5_enrichment" in skips[0]["message"]
    assert payload["artifacts"]["stage_5_enrichment"] is None


# ------------------------------- lints 14r/14s: accepted PubMed records (E19)


def _accepted(idx, pubmed_title, cv_title="A synthetic study of tidal sediment cores",
              pubtypes=("Journal Article",), status="enriched", text=None):
    """A stage-5 entry stage 5 accepted from PubMed; every value is invented."""
    return {"element_idx_start": idx, "taxonomy_code": "S1",
            "text": text or f"Vandermeer Q. {cv_title}. J Synth Geol. 2031;4:1-9.",
            "enrichment_status": status, "enrichment_source": "pmid",
            "extracted_fields": {"title": cv_title},
            "enrichment_data": {"pubmed_title": pubmed_title,
                                "publication_types": list(pubtypes)}}


@pytest.mark.parametrize("title", [
    "Sediment transport in ",           # cut before an inline element: trailing space
    "An N",                             # cut inside a token followed by a superscript
    "Coastal cores of the (CoRE",       # cut inside a parenthesis
])
def test_pubmed_title_truncated_warns_on_a_title_cut_mid_phrase(title):
    findings = lint_pubmed_title_truncated({"entries": [_accepted(264, title)]})
    assert [(f["lint"], f["severity"]) for f in findings] == [("pubmed_title_truncated", "WARN")]
    assert findings[0]["message"].startswith("entry 264 (S1): ")


@pytest.mark.parametrize("title", [
    "A synthetic study of tidal sediment cores.",
    "Do tidal cores record storms?",
    "Tidal cores: a field note!",
    "[A synthetic study of tidal cores in translation]",
    "A synthetic study of tidal sediment cores (TIDE)",
    "A synthetic study of tidal sediment cores.   ",
    "The \u201ctidal core\u201d",
    "The \u2018tidal core\u2019",
    "The 'tidal core'",
    'The "tidal core"',
])
def test_pubmed_title_truncated_quiet_on_a_complete_title(title):
    assert lint_pubmed_title_truncated({"entries": [_accepted(264, title)]}) == []


def test_pubmed_title_truncated_skips_records_stage_6_does_not_render_from_pubmed():
    """An empty title falls back to the CV's in stage 6, and only an accepted
    ('enriched') record replaces the CV's citation at all."""
    entries = [_accepted(1, ""), _accepted(2, "Sediment transport in ", status="title_check_failed"),
               {"element_idx_start": 3, "text": "no enrichment at all"}]
    assert lint_pubmed_title_truncated({"entries": entries}) == []


def test_pubmed_title_truncated_does_not_judge_a_shorter_published_title():
    entry = _accepted(5, "Tidal cores.", cv_title="A much longer conference title "
                      "for the same synthetic study of tidal sediment cores in estuaries")
    assert lint_pubmed_title_truncated({"entries": [entry]}) == []


@pytest.mark.parametrize("pubtype", ["Published Erratum", "Retraction of Publication",
                                     "Expression of Concern"])
def test_enrichment_pubtype_mismatch_warns_on_a_notice_accepted_for_a_paper(pubtype):
    entry = _accepted(257, "A synthetic study of tidal sediment cores.", pubtypes=(pubtype,))
    findings = lint_enrichment_pubtype_mismatch({"entries": [entry]})
    assert [(f["lint"], f["severity"]) for f in findings] == [
        ("enrichment_pubtype_mismatch", "WARN")]
    assert findings[0]["message"].startswith("entry 257 (S1): ")
    assert pubtype in findings[0]["message"]


@pytest.mark.parametrize("opening", ["Author Correction", "Correction", "Erratum",
                                     "Retraction", "Corrigendum", "Expression of concern"])
def test_enrichment_pubtype_mismatch_reads_a_notice_title_without_the_type(opening):
    """Each notice opening the title regex names, on a record typed only as
    a Journal Article, so the title alone decides."""
    entry = _accepted(257, f"{opening}: A synthetic study of tidal sediment cores.")
    findings = lint_enrichment_pubtype_mismatch({"entries": [entry]})
    assert [f["lint"] for f in findings] == ["enrichment_pubtype_mismatch"]


@pytest.mark.parametrize("cv_title", [
    "Author Correction: A synthetic study of tidal sediment cores",
    "Erratum to: A synthetic study of tidal sediment cores",
])
def test_enrichment_pubtype_mismatch_quiet_when_the_cv_lists_the_notice(cv_title):
    entry = _accepted(272, f"{cv_title}.", cv_title=cv_title, pubtypes=("Published Erratum",))
    assert lint_enrichment_pubtype_mismatch({"entries": [entry]}) == []


@pytest.mark.parametrize("text, cv_title", [
    # the notice is named only in the entry text, not in the extracted title
    ("Vandermeer Q. Erratum. J Synth Geol. 2031;4:10.", "A synthetic study of tidal cores"),
    # the notice is named only in the extracted title, not in the entry text
    ("Vandermeer Q. J Synth Geol. 2031;4:10.", "Corrigendum to a synthetic study of tidal cores"),
    ("Vandermeer Q. Retraction: tidal cores. J Synth Geol. 2031;4:10.", "Tidal cores"),
    # each remaining word the CV-side regex names, alone in the entry text
    ("Vandermeer Q. Tidal cores (retracted). J Synth Geol. 2031;4:10.", "Tidal cores"),
    ("Vandermeer Q. Tidal cores, errata. J Synth Geol. 2031;4:10.", "Tidal cores"),
    ("Vandermeer Q. Expression of concern: tidal cores. J Synth Geol. 2031;4:10.",
     "Tidal cores"),
])
def test_enrichment_pubtype_mismatch_reads_both_cv_text_and_cv_title(text, cv_title):
    entry = _accepted(272, "A synthetic study of tidal cores.", cv_title=cv_title,
                      pubtypes=("Retraction of Publication",), text=text)
    assert lint_enrichment_pubtype_mismatch({"entries": [entry]}) == []


def test_enrichment_pubtype_mismatch_reads_only_the_opening_of_the_title():
    """A paper whose title names a correction mid-way is not a notice."""
    entry = _accepted(9, "Drift correction for synthetic tidal sediment cores.")
    assert lint_enrichment_pubtype_mismatch({"entries": [entry]}) == []


def test_enrichment_lints_skip_an_accepted_entry_with_malformed_enrichment_data():
    entry = _accepted(9, "Sediment transport in ", pubtypes=("Published Erratum",))
    entry["enrichment_data"] = ["not", "a", "dict"]
    stage5e = {"entries": [entry]}
    assert lint_pubmed_title_truncated(stage5e) == []
    assert lint_enrichment_pubtype_mismatch(stage5e) == []


def test_enrichment_lints_tolerate_non_dict_extracted_fields():
    """A truthy non-dict `extracted_fields` (a list) is read as no CV title,
    not dereferenced: both lints still report the record."""
    cut = _accepted(11, "Sediment transport in ")
    notice = _accepted(12, "A synthetic study of tidal cores.", pubtypes=("Published Erratum",),
                       text="Vandermeer Q. Tidal cores. J Synth Geol. 2031;4:10.")
    for entry in (cut, notice):
        entry["extracted_fields"] = ["not", "a", "dict"]
    stage5e = {"entries": [cut, notice]}
    truncated = lint_pubmed_title_truncated(stage5e)
    assert [f["lint"] for f in truncated] == ["pubmed_title_truncated"]
    assert "the CV's title: 0 chars" in truncated[0]["evidence"][0]
    assert [f["lint"] for f in lint_enrichment_pubtype_mismatch(stage5e)] == [
        "enrichment_pubtype_mismatch"]


@pytest.mark.parametrize("pubtypes", [("Letter", "Comment"), ("Editorial", "Comment"),
                                      ("Journal Article", "Retracted Publication"),
                                      ("Journal Article",)])
def test_enrichment_pubtype_mismatch_quiet_on_a_paper_record(pubtypes):
    """A Comment is usually the CV owner's own commentary, and a Retracted
    Publication is the paper itself."""
    entry = _accepted(9, "A synthetic study of tidal sediment cores.", pubtypes=pubtypes)
    assert lint_enrichment_pubtype_mismatch({"entries": [entry]}) == []


def test_enrichment_pubtype_mismatch_skips_a_record_stage_5_did_not_accept():
    entry = _accepted(9, "Correction: tidal cores.", pubtypes=("Published Erratum",),
                      status="title_check_failed")
    assert lint_enrichment_pubtype_mismatch({"entries": [entry]}) == []


def test_enrichment_lints_run_from_the_stage_5_artifact(tmp_path):
    """Wired through LINT_REGISTRY: run_doctor reads both off the stage-5 JSON."""
    root = _build_clean_run(tmp_path)
    path = next((root / "stage_5_enrichment").glob("*.json"))
    payload = json.loads(path.read_text())
    payload["entries"] = [_accepted(264, "Sediment transport in "),
                          _accepted(257, "Correction: tidal cores.", pubtypes=("Published Erratum",))]
    path.write_text(json.dumps(payload))
    lints = [f["lint"] for f in run_doctor(root, _UID)["findings"]
             if f["lint"] in ("pubmed_title_truncated", "enrichment_pubtype_mismatch")]
    assert sorted(lints) == ["enrichment_pubtype_mismatch", "pubmed_title_truncated"]


# ------------------------------------------------- lint 10: stage-6 warnings

def test_stage6_warnings_reemitted_as_warn():
    report = {"document_uid": "X", "warnings": [
        {"check": "semicolon_fused_bullets", "code": "K3",
         "section": "Administrative teaching",
         "message": "K3 (Administrative teaching): Content appears combined "
                    "with semicolons instead of separate bullets",
         "evidence": ["• a; b; c; d; e"]},
        {"check": "no_visible_teaching_content", "code": "K",
         "section": "EDUCATIONAL CONTRIBUTIONS",
         "message": "K (Teaching): No visible bulleted content found - may "
                    "be using track changes only",
         "evidence": []},
    ]}
    findings = lint_stage6_warnings(report)
    assert len(findings) == 2
    assert all(f["lint"] == "stage6_render_warnings" and f["severity"] == "WARN"
               for f in findings)
    assert "K3 (Administrative teaching)" in findings[0]["message"]
    assert findings[0]["evidence"] == ["• a; b; c; d; e"]


def test_stage6_warnings_quiet_on_clean_sidecar():
    assert lint_stage6_warnings({"warnings": [], "dedup_decisions": []}) == []


def test_stage6_warnings_reemits_appendix_diversion_with_code_and_count():
    """#531: an appendix_diversion sidecar entry re-emits as a WARN whose
    message names the code and the count -- lint_stage6_warnings reads only
    `message`/`evidence` (KNOWN_LINTS/LintSpec untouched, per the ticket),
    so this is a re-emission proof, not a lint-registry change."""
    report = {"document_uid": "X", "warnings": [
        {"check": "appendix_diversion", "code": "N2", "section": "T. APPENDIX",
         "count": 3, "reason": "no_render_route",
         "message": "N2: 3 entries diverted to the Appendix — no stage "
                    "6 section is routed to render this taxonomy code",
         "evidence": []},
    ]}
    findings = lint_stage6_warnings(report)
    assert len(findings) == 1
    f = findings[0]
    assert f["lint"] == "stage6_render_warnings" and f["severity"] == "WARN"
    assert "N2" in f["message"] and "3" in f["message"]
    assert f["evidence"] == []


# ----------------------------------------------------- lint 11: dedup drops

def test_dedup_drops_flags_distinct_record_quiet_on_true_dup():
    report = {"dedup_decisions": [
        # 2Q1_ZQ drop 4: different journals sharing only date tokens — LOSS.
        {"code": "Q4D", "metric": "containment=0.75",
         "dropped_text": "Diagnosis (Jan 2024-Present)",
         "kept_text": "Academic Medicine (Jan 2024-Present)"},
        # 2Q1_ZQ drop 2: same line inside a longer fused entry — true dup.
        {"code": "D1", "metric": "containment=1.00",
         "dropped_text": "Associate Professor, Health Professions Education",
         "kept_text": "Oct 2025-Present\nAssociate Professor, Health "
                      "Professions Education\nDepartment of Medicine"},
    ]}
    findings = lint_dedup_drops(report)
    assert len(findings) == 1
    f = findings[0]
    assert f["lint"] == "dedup_drops" and f["severity"] == "WARN"
    assert "1 dedup drop(s)" in f["message"]
    assert "Diagnosis" in f["evidence"][0]
    assert not any("Associate Professor" in e for e in f["evidence"])


def test_dedup_drops_quiet_with_no_decisions():
    assert lint_dedup_drops({"warnings": [], "dedup_decisions": []}) == []


# ------------------------------------------- lint 14d: date-only lines (#259)

_EDU_HEADER = ("p", "K. EDUCATIONAL CONTRIBUTIONS")


def _date_only_blocks(dates):
    """Level-0 bullets interleaved with activity names -- ZXVGAC's shape."""
    blocks = [_EDU_HEADER]
    for i, date in enumerate(dates):
        blocks += [("p", f"Guest lecture number {i}"), ("p", date)]
    return blocks


def test_date_only_lines_warns_at_threshold_with_three_samples():
    dates = ["June 2019", "07/2008 \u2013 06/2013", "October Issue 2025",
             "October 13, 2016", "2024-2025"]
    assert DATE_ONLY_LINES_WARN_COUNT == 5  # the gap between the corpus's 4 and 7
    assert len(dates) == DATE_ONLY_LINES_WARN_COUNT
    findings = lint_date_only_lines(_date_only_blocks(dates))
    assert len(findings) == 1
    f = findings[0]
    assert f["lint"] == "date_only_lines" and f["severity"] == "WARN"
    assert f["message"].startswith("5 body paragraph(s)")
    assert f["evidence"] == dates[:3]


def test_date_only_lines_info_below_threshold():
    dates = ["June 2019"] * (DATE_ONLY_LINES_WARN_COUNT - 1)
    findings = lint_date_only_lines(_date_only_blocks(dates))
    assert [f["severity"] for f in findings] == ["INFO"]
    assert findings[0]["message"].startswith("4 body paragraph(s)")


def test_date_only_lines_ignores_table_cells():
    blocks = [_EDU_HEADER] + [("table", "June 2019")] * 10
    assert lint_date_only_lines(blocks) == []


def test_date_only_lines_ignores_the_appendix_until_the_next_section():
    blocks = ([("p", "T. APPENDIX")] + [("p", "\u2022 June 2019")] * 10
              + [("p", "U. OTHER"), ("p", "March 2020")])
    findings = lint_date_only_lines(blocks)
    assert len(findings) == 1
    assert findings[0]["message"].startswith("1 body paragraph(s)")
    assert findings[0]["evidence"] == ["March 2020"]


def test_date_only_lines_sees_through_a_list_enumerator_but_not_words():
    blocks = [_EDU_HEADER, ("p", "3. 2009."), ("p", "\u2022 June 2019"),
              ("p", "Johns Hopkins University, 1991"),
              ("p", "Course director, June 2019 \u2013 Present")]
    findings = lint_date_only_lines(blocks)
    assert findings[0]["evidence"] == ["3. 2009.", "\u2022 June 2019"]


def test_date_only_lines_is_a_registered_lint():
    from unified_pipeline.run_doctor import KNOWN_LINTS, LINT_REGISTRY
    assert "date_only_lines" in KNOWN_LINTS
    assert any(spec.lint_id == "date_only_lines" and spec.rule is lint_date_only_lines
               for spec in LINT_REGISTRY)


# ------- lints 14f/14g: repr and refusal text in the output (#1233, #1224)

_REPR_DICT = "{'start_date': '2010', 'end_date': '2020'}"


def _table_block(*rows):
    """A table block the way `docx_body_blocks` writes it: each cell on its
    own line, then each multi-cell row again joined the way `_table_lines`
    joins it."""
    lines = []
    for cells in rows:
        lines += list(cells)
        if len(cells) > 1:
            lines.append(TABLE_ROW_JOINER.join(cells))
    return ("table", "\n".join(lines))


def test_python_repr_flags_a_dict_repr_glued_to_a_year():
    blocks = [("p", "G. LICENSURE, BOARD CERTIFICATION"),
              _table_block(("Example Board", f"2000-{_REPR_DICT}"))]
    findings = lint_python_repr_in_output(blocks)
    assert len(findings) == 1
    f = findings[0]
    assert f["lint"] == "python_repr_in_output" and f["severity"] == "WARN"
    assert f["message"].startswith("1 distinct Python dict/list repr text(s)")
    assert f["evidence"] == [_REPR_DICT]


def test_python_repr_counts_a_table_cell_once_not_again_for_its_joined_row():
    """One repr in the first cell (the joined row then carries text after it)
    and one in the last (it does not): neither is counted a second time."""
    other = "{'start_date': '2015', 'end_date': '2016'}"
    blocks = [_table_block((_REPR_DICT, "Example Talk"), ("Other Talk", other))]
    findings = lint_python_repr_in_output(blocks)
    assert findings[0]["message"].startswith("2 distinct")
    assert findings[0]["evidence"] == [_REPR_DICT, other]


def test_python_repr_counts_a_cell_of_a_real_docx_table_once():
    """`_table_block` imitates `docx_body_blocks`; this reads the real thing, so
    the dedupe cannot drift from how `_table_lines` joins a row."""
    doc = Document()
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].paragraphs[0].text = _REPR_DICT
    table.rows[0].cells[1].paragraphs[0].text = "Example Talk"
    findings = lint_python_repr_in_output(docx_body_blocks(doc))
    assert findings[0]["message"].startswith("1 distinct")
    assert findings[0]["evidence"] == [_REPR_DICT]


@pytest.mark.parametrize("text", [
    "['Example course A', 'Example course B']",
    '["Women\'s health", \'Example course\']',
    "['2001-01', '2003-03']-['2002-02', '2004-04']",
    '{"start_date": "2010", "end_date": "2020"}',
    "[{'start_date': '2010'}]",
])
def test_python_repr_flags_each_shape_str_of_a_container_prints(text):
    findings = lint_python_repr_in_output([("p", f"Example row | {text}")])
    assert [f["severity"] for f in findings] == ["WARN"]
    assert findings[0]["evidence"]


@pytest.mark.parametrize("text", [
    "[1] Doe J, Roe R. Example title. J Example. 2020;1:1-9. [2] Doe J.",
    "Uptake of [3H]-thymidine was measured in {n=12} wells.",
    "Doe J [Ed.]. Example Handbook. Cohort [n=40], set {a, b}.",
    "Example Center for Health (CEH) ('Example Study', 2020)",
    "https://example.org/a[0]/b",
    "",
])
def test_python_repr_ignores_ordinary_brackets_braces_and_quotes(text):
    assert lint_python_repr_in_output([("p", text), ("table", text)]) == []


def test_python_repr_scans_the_appendix_too():
    blocks = [("p", "T. APPENDIX"), ("p", f"1. Example entry {_REPR_DICT}")]
    assert len(lint_python_repr_in_output(blocks)) == 1


def test_python_repr_caps_the_evidence_and_still_counts_every_hit():
    dicts = [f"{{'start_date': '20{i:02d}'}}" for i in range(OUTPUT_LEAK_EVIDENCE_LIMIT + 2)]
    findings = lint_python_repr_in_output([("p", d) for d in dicts])
    assert findings[0]["message"].startswith(f"{len(dicts)} distinct")
    assert findings[0]["evidence"] == dicts[:OUTPUT_LEAK_EVIDENCE_LIMIT]
    assert len(findings[0]["evidence"]) == 5  # the cap itself, not the constant


# The shape MYAXRH rendered (#1224), with invented names. Every sentence below
# is one the model wrote to the user, not text a CV owner wrote.
_REFUSAL = (
    "I don't have access to specific CV details for Example Person beyond "
    "what you've indicated would be provided, and no actual CV content was "
    'included in your message. The "CV CONTEXT" section appears empty.\n\n'
    "Please provide the actual CV content or research details for Example "
    "Person, and I'll be happy to draft a concise summary paragraph.")


def test_llm_refusal_flags_the_text_stage_4_5_rendered_for_an_empty_context():
    blocks = [("p", "Research Activities: In a paragraph (up to 300 words)"),
              ("p", _REFUSAL)]
    findings = lint_llm_refusal_in_output(blocks)
    assert len(findings) == 1
    f = findings[0]
    assert f["lint"] == "llm_refusal_in_output" and f["severity"] == "WARN"
    assert f["message"].startswith("2 distinct language-model refusal")
    assert f["evidence"][0].startswith("I don't have access to")


@pytest.mark.parametrize("sentence", [
    "I do not have access to the document.",
    "I don\u2019t have access to the document.",
    "I am unable to access the CV text.",
    "I'm not able to summarize this without more detail.",
    "I cannot provide a summary of this researcher.",
    "I can't generate that paragraph.",
    "As an AI language model, I have nothing to summarise.",
    "As an AI assistant I cannot see the CV.",
    "As an AI, I have no record of this researcher.",
    "I'm sorry, but there is nothing here to work with.",
    "I apologize, but the context is blank.",
    'The "CV CONTEXT" section is missing.',
    "Please provide the full CV and I will start.",
    "No details were provided in your message.",
    "I will be glad to write the paragraph once I have the CV.",
])
def test_llm_refusal_flags_each_standard_refusal_opener(sentence):
    findings = lint_llm_refusal_in_output([("p", f"Example heading\n{sentence}")])
    assert [f["severity"] for f in findings] == ["WARN"]
    assert findings[0]["evidence"]


@pytest.mark.parametrize("text", [
    "I Can't Sleep Anymore: A Case Series. J Example. 2020;1:1-9.",
    "If yes, please provide Visa type (Examples: J-1, H-1B):",
    "As an AI researcher she builds models of example outcomes.",
    "Example Chatbot as an AI assistant in clinical documentation: a review.",
    "Example Tool as an AI language model in medical education. J Example.",
    "Dr. Doe is unable to attend; please provide feedback to the chair.",
    "The CV context for this hire was reviewed by the committee.",
    "She cannot be reached by phone, and she writes every weekday.",
    "",
])
def test_llm_refusal_ignores_first_person_and_polite_text_in_a_real_cv(text):
    assert lint_llm_refusal_in_output([("p", text), ("table", text)]) == []


def test_neither_lint_fires_on_the_pristine_wcm_template():
    """The template's own scaffolding ("please provide Visa type", every field
    label) is on every rendered CV, so a phrase it contains would fire
    corpus-wide."""
    from docx import Document as OpenDocument
    blocks = docx_body_blocks(OpenDocument(str(_TEMPLATE_DOCX_PATH)))
    assert blocks
    assert lint_python_repr_in_output(blocks) == []
    assert lint_llm_refusal_in_output(blocks) == []


def test_repr_and_refusal_lints_are_registered_lints():
    from unified_pipeline.run_doctor import KNOWN_LINTS, LINT_REGISTRY
    for lint_id, rule in (("python_repr_in_output", lint_python_repr_in_output),
                          ("llm_refusal_in_output", lint_llm_refusal_in_output)):
        assert lint_id in KNOWN_LINTS
        assert any(spec.lint_id == lint_id and spec.rule is rule
                   and spec.inputs == ("blocks",) for spec in LINT_REGISTRY)


def test_run_doctor_wires_repr_and_refusal_through_to_the_verdict(tmp_path):
    """#1233 / #1224 end to end: a real docx table cell and a real paragraph,
    read through `read_docx_blocks`, reach the verdict as one WARN each."""
    root = _build_clean_run(tmp_path)
    docx_path = next((root / "stage_6_wcm_documents").glob(f"{_UID}*_wcm.docx"))
    doc = Document(str(docx_path))
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].paragraphs[0].text = "Example Board"
    table.rows[0].cells[1].paragraphs[0].text = f"2000-{_REPR_DICT}"
    doc.add_paragraph(_REFUSAL)
    doc.save(str(docx_path))

    payload = run_doctor(root, _UID)

    by_lint = {f["lint"]: f for f in payload["findings"]}
    assert by_lint["python_repr_in_output"]["severity"] == "WARN"
    assert by_lint["python_repr_in_output"]["evidence"] == [_REPR_DICT]
    assert by_lint["llm_refusal_in_output"]["severity"] == "WARN"
    assert payload["counts"]["ERROR"] == 0


# ------------------------- lint 14n: the CV owner's name, citation by citation

_CITE_OWNER = {"first_name": "Rowan", "last_name": "Thornquist"}
_CITE_KEPT = "Ashdown A, Brimley B, Corwen C, Dunmore D, Elsworth E, Fenwick F"
_CITE_ALL = f"{_CITE_KEPT}, Garrow G, Thornquist R"
_CITE_TITLE = "Tidal patterns in synthetic estuary sediment cores"
_CITE_TRAILER = "J Synth Geol. 2019;12(3):45-67."
#: Stage 5d's "first 6 authors, et al." cut of `_CITE_ALL`, owner gone.
_CUT_LINE = f"{_CITE_KEPT}, et al. {_CITE_TITLE}. {_CITE_TRAILER}"
_FULL_LINE = f"{_CITE_ALL}. {_CITE_TITLE}. {_CITE_TRAILER}"


def _publication(idx, text, authors=None, code="S1"):
    fields = {} if authors is None else {"authors": authors}
    return {"element_idx_start": idx, "taxonomy_code": code, "text": text,
            "extracted_fields": fields}


def _source(authors=_CITE_ALL, title=_CITE_TITLE, tail=""):
    return f"{authors}. {title}. {_CITE_TRAILER}{tail}"


def _cite_run(*entries, owner=_CITE_OWNER):
    return {"cv_owner": owner, "entries": list(entries)}


def _bibliography(*lines):
    """A render's BIBLIOGRAPHY section, each citation numbered as stage 6 numbers it."""
    return ([("p", "BIBLIOGRAPHY"), ("p", "Peer-reviewed Research Articles:")]
            + [("p", f"{n}. {line}") for n, line in enumerate(lines, 1)])


def test_owner_missing_fires_when_the_et_al_cut_drops_the_owner():
    # VNUAHA-05 / KYOPUV-06 shape: stage 5d kept six authors, the owner was
    # ninth, and the #1292 restore declined on a damaged stage-4 list.
    stage4 = _cite_run(_publication(381, _source(), authors=_CITE_ALL))
    blocks = _bibliography(_CUT_LINE)
    findings = lint_owner_missing_from_citation(stage4, blocks)
    assert [(f["lint"], f["severity"]) for f in findings] == [
        ("owner_missing_from_citation", "WARN")]
    assert findings[0]["message"] == (
        "entry 381 (S1): stage 4's author list names the CV owner, but the rendered "
        "citation does not name them (its author list is cut to 'et al.')")
    assert findings[0]["evidence"] == [_CUT_LINE[:CITATION_EVIDENCE_CHARS]]
    # The same citation is not reported again as a bare co-author cut.
    assert lint_etal_added(stage4, blocks) == []


def test_owner_missing_quiet_when_the_rendered_list_keeps_the_owner():
    stage4 = _cite_run(_publication(12, _source(), authors=_CITE_ALL))
    assert lint_owner_missing_from_citation(stage4, _bibliography(_FULL_LINE)) == []


def test_owner_missing_reads_only_the_author_list_an_et_al_closes():
    # ZGBCIT 348: the owner's name survived only in a trailing note.
    line = f"{_CITE_KEPT}, et al. {_CITE_TITLE}. {_CITE_TRAILER} Presented by Thornquist R."
    stage4 = _cite_run(_publication(348, _source(tail=" Presented by Thornquist R."),
                                    authors=_CITE_ALL))
    findings = lint_owner_missing_from_citation(stage4, _bibliography(line))
    assert [f["message"].split(":")[0] for f in findings] == ["entry 348 (S1)"]


def test_owner_missing_quiet_when_et_al_comma_continues_the_list():
    # KYOPUV 408 / TXTATQ 279: the source elides the middle of its list and
    # the owner follows the "et al.,".
    source = "Ashdown A, Brimley B... Thornquist R, Garrow G. " + _CITE_TITLE + ". " + _CITE_TRAILER
    line = f"Ashdown A, Brimley B, et al., Thornquist R, Garrow G. {_CITE_TITLE}. {_CITE_TRAILER}"
    stage4 = _cite_run(_publication(408, source, authors="Ashdown A, Brimley B, Thornquist R, Garrow G"))
    blocks = _bibliography(line)
    assert lint_owner_missing_from_citation(stage4, blocks) == []
    assert lint_etal_added(stage4, blocks) == []


@pytest.mark.parametrize(("surname", "rendered", "fires"), [
    ("Thornquist", "Thornqvist", False),  # PubMed's spelling, one letter off
    ("Thornquist", "Thornquists", False),
    ("Thornquist", "Thornqvisst", True),  # two letters off
    ("Thornquist", "Thornqvast", True),   # two letters off, same length
    ("Lindo", "Linde", True),             # too short to tolerate a letter
])
def test_owner_missing_tolerates_one_letter_only_on_a_long_surname(surname, rendered, fires):
    # NDXXAD 160/276: a PubMed rebuild printed its own spelling of the owner.
    source = _source(authors=f"{_CITE_KEPT}, {surname} R")
    line = f"{_CITE_KEPT}, {rendered} R. {_CITE_TITLE}. {_CITE_TRAILER}"
    stage4 = _cite_run(_publication(160, source, authors=f"{_CITE_KEPT}, {surname} R"),
                       owner={"last_name": surname})
    assert bool(lint_owner_missing_from_citation(stage4, _bibliography(line))) is fires


def test_owner_missing_falls_back_to_the_source_text_for_a_dropped_credit():
    # BMAMWE 1028, CXRYCF 762: a consortium credit that names the owner is
    # in the source line, not in stage 4's authors, and the rebuilt citation
    # drops it.
    source = _source(authors=_CITE_KEPT, tail=" (including Thornquist R)")
    line = f"{_CITE_KEPT}. {_CITE_TITLE}. {_CITE_TRAILER}"
    stage4 = _cite_run(_publication(1028, source, authors=_CITE_KEPT))
    findings = lint_owner_missing_from_citation(stage4, _bibliography(line))
    assert [f["message"] for f in findings] == [
        "entry 1028 (S1): the source citation names the CV owner, but the rendered "
        "citation does not name them"]


def test_owner_missing_ignores_the_surname_inside_an_email_address():
    # MRJDWE 288: contact data fused onto the entry is not an author credit.
    source = _source(authors=_CITE_KEPT, tail=" Contact: rowan.thornquist@example.org")
    line = f"{_CITE_KEPT}. {_CITE_TITLE}. {_CITE_TRAILER}"
    stage4 = _cite_run(_publication(288, source, authors=_CITE_KEPT))
    assert lint_owner_missing_from_citation(stage4, _bibliography(line)) == []


def _co_presented_talk(co_presented="co-presented with"):
    """A talk the source says was co-presented, as stage 4 stored it (S8,
    the co-presenter as its only author), and its rendered line, which
    credits the co-presenter alone."""
    source = (f"“{_CITE_TITLE},” {co_presented} Garrow G, Annual Synthetic "
              f"Geology Meeting, Larkspur, 2001")
    line = f"Garrow G. {_CITE_TITLE}. Annual Synthetic Geology Meeting; 2001; Larkspur."
    return _publication(762, source, authors="Garrow G", code="S8"), line


@pytest.mark.parametrize("co_presented", ["co-presented with", "co presented with", "copresenter"])
def test_owner_missing_fires_on_a_co_presented_talk_credited_to_the_others(co_presented):
    # XWNZWW-04: "co-presented with" names only the co-presenters, and the
    # rendered citation credits the talk to them alone. CVs spell it with a
    # hyphen, a space or neither.
    talk, line = _co_presented_talk(co_presented)
    findings = lint_owner_missing_from_citation(_cite_run(talk), _bibliography(line))
    assert [f["message"] for f in findings] == [
        "entry 762 (S8): the source says the CV owner co-presented this talk, but the "
        "rendered citation does not name them"]


@pytest.mark.parametrize("owner", [None, {}, {"last_name": ""}, {"last_name": "Wu"}])
def test_owner_missing_judges_nothing_without_a_usable_owner_surname(owner):
    # No surname is owner_contact_missing's finding; a two-letter one is an
    # author's initials as often as a name.
    source = _source(authors=f"{_CITE_KEPT}, Garrow G, Wu R")
    stage4 = _cite_run(_publication(5, source, authors=f"{_CITE_KEPT}, Garrow G, Wu R"),
                       owner=owner)
    assert lint_owner_missing_from_citation(stage4, _bibliography(_CUT_LINE)) == []
    # A co-presented talk credits the owner without naming them, so with no
    # surname to look for in its line there is nothing to judge either.
    talk, line = _co_presented_talk()
    assert lint_owner_missing_from_citation(_cite_run(talk, owner=owner),
                                            _bibliography(line)) == []


@pytest.mark.parametrize("surname", ["Worth", "Core"])
def test_owner_missing_credits_only_the_whole_surname(surname):
    # A short surname inside a longer word is not the owner: "Worth" inside
    # the co-author "Elsworth", "Core" inside the title word "cores".
    stage4 = _cite_run(_publication(9, _source(), authors=_CITE_ALL),
                       owner={"last_name": surname})
    assert lint_owner_missing_from_citation(stage4, _bibliography(_CUT_LINE)) == []


def test_citation_pairing_reads_only_the_bibliography_and_needs_four_tokens():
    stage4 = _cite_run(_publication(7, _source(), authors=_CITE_ALL))
    # A numbered Appendix entry is verbatim source, not a citation stage 6
    # wrote, and a numbered line that is a year alone pairs with nothing.
    appendix = [("p", "T. APPENDIX"), ("p", f"1. {_CUT_LINE}")]
    assert lint_owner_missing_from_citation(stage4, appendix) == []
    assert lint_owner_missing_from_citation(stage4, _bibliography("2019")) == []
    # Another publication's line is not this entry's line either, even one
    # by the same six co-authors: their names are under 60% of its tokens.
    other = "Holloway H, Ivesdale I. Glacial varves of a model lake. Synth Limnol. 2004;3:1-9."
    same_group = (f"{_CITE_KEPT}. Glacial varves of a model lake and their dating by "
                  f"layer counting. Synth Limnol. 2004;3:1-9.")
    assert lint_owner_missing_from_citation(stage4, _bibliography(other)) == []
    assert lint_owner_missing_from_citation(stage4, _bibliography(same_group)) == []
    # The floor is four shared tokens exactly: an author list cut after three
    # names is wholly the source's but does not pair; cut after four, it does.
    three = "Ashdown A, Brimley B, Corwen C, et al."
    four = "Ashdown A, Brimley B, Corwen C, Dunmore D, et al."
    assert lint_owner_missing_from_citation(stage4, _bibliography(three)) == []
    assert [f["message"].split(":")[0] for f in lint_owner_missing_from_citation(
        stage4, _bibliography(four))] == ["entry 7 (S1)"]


def test_citation_pairing_folds_case_and_accents():
    # RNKYST 374 / ZDCXIV 65 shape: a PubMed rebuild prints the title in
    # sentence case and the names without their accents, so the line shares
    # few tokens with the source as written; it is still that entry's line.
    authors = "Áshdöwn A, Brímley B, Córwen C, Dunmore D, Elsworth E, Fenwick F, Garrow G, Thornquist R"
    source = f"{authors}. {_CITE_TITLE.upper()}. {_CITE_TRAILER}"
    stage4 = _cite_run(_publication(374, source, authors=authors))
    findings = lint_owner_missing_from_citation(stage4, _bibliography(_CUT_LINE))
    assert [f["message"].split(":")[0] for f in findings] == ["entry 374 (S1)"]


def test_owner_missing_judges_a_three_letter_surname():
    # ZCTARO-08: the shortest surname the lint judges is three letters.
    authors = f"{_CITE_KEPT}, Garrow G, Orr R"
    stage4 = _cite_run(_publication(804, _source(authors=authors), authors=authors),
                       owner={"last_name": "Orr"})
    findings = lint_owner_missing_from_citation(stage4, _bibliography(_CUT_LINE))
    assert [f["message"].split(":")[0] for f in findings] == ["entry 804 (S1)"]


def test_citation_pairing_keeps_an_entry_on_its_best_line():
    # One entry, two lines over both thresholds: its full line and a cut
    # copy (MQSUIC 382 shape). It keeps the full line, which names the owner,
    # and is not re-paired with the worse one.
    stage4 = _cite_run(_publication(382, _source(), authors=_CITE_ALL))
    assert lint_owner_missing_from_citation(
        stage4, _bibliography(_FULL_LINE, _CUT_LINE)) == []


def test_citation_pairing_does_not_count_initials_or_one_digit_numbers():
    # The same seven co-authors' other paper shares 8 of the 14 tokens of
    # three or more characters in its line (57%). Counting initials and short
    # numbers as tokens would make that 16 of 25 (64%) and pair it.
    stage4 = _cite_run(_publication(7, _source(), authors=_CITE_ALL))
    other = f"{_CITE_KEPT}, Garrow G. Glacial varves of a model lake. Synth Limnol. 2004;3:1-9."
    assert lint_owner_missing_from_citation(stage4, _bibliography(other)) == []


def test_citation_pairing_takes_the_share_of_the_line_not_of_the_source():
    # A source carrying a long note renders as a much shorter line: all 15 of
    # the line's tokens are the source's, though they are under half of the
    # source's 32.
    note = (" Selected for the quarterly highlights of the Larkspur Mossgrove society, "
            "with an invited commentary on coring methods by its editors.")
    stage4 = _cite_run(_publication(7, _source(tail=note), authors=_CITE_ALL))
    findings = lint_owner_missing_from_citation(stage4, _bibliography(_CUT_LINE))
    assert [f["message"].split(":")[0] for f in findings] == ["entry 7 (S1)"]


def test_citation_pairing_reads_only_publication_entries():
    # Only an S entry prints in the bibliography. A talk (R) whose text
    # repeats the paper's cut citation word for word matches that line
    # better than the paper's own entry does, but it is not a candidate.
    talk = _publication(90, _CUT_LINE, code="R")
    stage4 = _cite_run(talk, _publication(10, _source(), authors=_CITE_ALL))
    findings = lint_owner_missing_from_citation(stage4, _bibliography(_CUT_LINE))
    assert [f["message"].split(":")[0] for f in findings] == ["entry 10 (S1)"]


def test_citation_pairing_reads_numbered_lines_through_an_all_caps_sub_heading():
    stage4 = _cite_run(_publication(7, _source(), authors=_CITE_ALL))
    # An all-caps sub-heading names no output section, so the numbered lines
    # after it are still the bibliography's.
    blocks = [("p", "BIBLIOGRAPHY"), ("p", "BOOKS AND CHAPTERS"), ("p", f"1. {_CUT_LINE}")]
    findings = lint_owner_missing_from_citation(stage4, blocks)
    assert [f["message"].split(":")[0] for f in findings] == ["entry 7 (S1)"]
    # An unnumbered paragraph in the section is not a citation stage 6
    # numbered, so it pairs with nothing.
    assert lint_owner_missing_from_citation(
        stage4, [("p", "BIBLIOGRAPHY"), ("p", _CUT_LINE)]) == []


def test_citation_pairing_gives_an_abstract_and_its_paper_their_own_lines():
    # The abstract and the paper that followed it share authors and title;
    # only the paper's line was cut, so only the paper is reported.
    abstract = (f"{_CITE_ALL}. {_CITE_TITLE}. Annual Synthetic Geology Meeting; "
                f"2018 Oct 2; Larkspur.")
    stage4 = _cite_run(_publication(30, abstract, authors=_CITE_ALL, code="S8"),
                       _publication(10, _source(), authors=_CITE_ALL))
    blocks = _bibliography(_CUT_LINE) + [("p", "Abstracts"), ("p", f"1. {abstract}")]
    findings = lint_owner_missing_from_citation(stage4, blocks)
    assert [f["message"].split(":")[0] for f in findings] == ["entry 10 (S1)"]


def test_citation_pairing_takes_each_line_once():
    # A near-duplicate entry whose own line was dropped does not borrow the
    # line the paper rendered as: one line, one entry, one finding.
    abstract = f"{_CITE_ALL}. {_CITE_TITLE}. Annual Synthetic Geology Meeting; 2018."
    stage4 = _cite_run(_publication(10, _source(), authors=_CITE_ALL),
                       _publication(30, abstract, authors=_CITE_ALL, code="S8"))
    findings = lint_owner_missing_from_citation(stage4, _bibliography(_CUT_LINE))
    assert [f["message"].split(":")[0] for f in findings] == ["entry 10 (S1)"]


def test_citation_pairing_prefers_the_entry_closest_in_size():
    # The CV lists the paper twice, once with a note. Each copy's line holds
    # only that copy's tokens, so both lines are wholly "in" the longer
    # entry; the overlap over both sizes still gives each line to its own
    # entry, and only the copy whose line was cut is reported.
    note = " Featured in the Larkspur Mossgrove geology quarterly review highlights."
    stage4 = _cite_run(_publication(4, _source(tail=note), authors=_CITE_ALL),
                       _publication(10, _source(), authors=_CITE_ALL))
    blocks = _bibliography(_CUT_LINE, _FULL_LINE + note)
    findings = lint_owner_missing_from_citation(stage4, blocks)
    assert [f["message"].split(":")[0] for f in findings] == ["entry 10 (S1)"]


def test_citation_pairing_ranks_by_overlap_over_both_sizes_not_shared_count():
    # The CV lists the paper twice, once followed by "Erratum.", and only
    # that copy's line was cut; the cut line keeps the "Erratum", so it is
    # that copy's. By shared-token count both copies tie for the full line
    # (17 tokens each), the first-listed copy takes it, and the plain copy is
    # left the cut line, with a word it does not hold.
    stage4 = _cite_run(_publication(4, _source(tail=" Erratum."), authors=_CITE_ALL),
                       _publication(10, _source(), authors=_CITE_ALL))
    blocks = _bibliography(_FULL_LINE, f"{_CUT_LINE} Erratum.")
    findings = lint_owner_missing_from_citation(stage4, blocks)
    assert [f["message"].split(":")[0] for f in findings] == ["entry 4 (S1)"]


def test_citation_pairing_ranks_by_overlap_over_both_sizes_not_share_of_the_source():
    # The plain copy's tokens are all in the noted copy's line, so by share
    # of the source that line is the plain copy's best match (100%, against
    # 89% for the noted copy, whose "Epub ahead of print" the line leaves
    # out). Over both sizes the noted copy keeps its line, and the plain
    # copy, whose line was cut, is reported.
    note = " Featured in the Larkspur Mossgrove geology quarterly review highlights."
    stage4 = _cite_run(
        _publication(4, _source(tail=f"{note} Epub ahead of print."), authors=_CITE_ALL),
        _publication(10, _source(), authors=_CITE_ALL))
    blocks = _bibliography(_CUT_LINE, _FULL_LINE + note)
    findings = lint_owner_missing_from_citation(stage4, blocks)
    assert [f["message"].split(":")[0] for f in findings] == ["entry 10 (S1)"]


def test_owner_lints_quote_the_head_of_a_long_line():
    title = ("Tidal patterns in synthetic estuary sediment cores across four model "
             "basins, with a reanalysis of layer counts from earlier coring campaigns")
    authors = f"Thornquist R, {_CITE_KEPT}, Garrow G"
    owner_kept = f"Thornquist R, Ashdown A, Brimley B, Corwen C, Dunmore D, Elsworth E, et al. {title}. {_CITE_TRAILER}"
    owner_cut = f"{_CITE_KEPT}, et al. {title}. {_CITE_TRAILER}"
    assert min(len(owner_kept), len(owner_cut)) > CITATION_EVIDENCE_CHARS
    cut = _cite_run(_publication(1, _source(title=title), authors=_CITE_ALL))
    kept = _cite_run(_publication(2, _source(authors=authors, title=title), authors=authors))
    assert [f["evidence"] for f in lint_owner_missing_from_citation(cut, _bibliography(owner_cut))] == [
        [owner_cut[:CITATION_EVIDENCE_CHARS]]]
    assert [f["evidence"] for f in lint_etal_added(kept, _bibliography(owner_kept))] == [
        [owner_kept[:CITATION_EVIDENCE_CHARS]]]


def test_etal_added_reports_a_cut_that_keeps_the_owner():
    authors = f"Thornquist R, {_CITE_KEPT}, Garrow G"
    line = f"Thornquist R, Ashdown A, Brimley B, Corwen C, Dunmore D, Elsworth E, et al. {_CITE_TITLE}. {_CITE_TRAILER}"
    stage4 = _cite_run(_publication(12, _source(authors=authors), authors=authors))
    blocks = _bibliography(line)
    findings = lint_etal_added(stage4, blocks)
    # WARN since #1404 removed the 5d cut (X6 E3: 95 lists cut on 6 CVs).
    assert [(f["lint"], f["severity"], f["message"]) for f in findings] == [(
        "etal_added", "WARN",
        "entry 12 (S1): the rendered citation keeps 6 author(s) and then 'et al.', "
        "where the source elides no author")]
    assert lint_owner_missing_from_citation(stage4, blocks) == []


def test_etal_added_reports_a_cut_with_no_owner_surname_to_judge():
    stage4 = _cite_run(_publication(12, _source(), authors=_CITE_ALL), owner={})
    assert [f["lint"] for f in lint_etal_added(stage4, _bibliography(_CUT_LINE))] == ["etal_added"]


@pytest.mark.parametrize("elided", ["Garrow G, et al", "Garrow G, et. al.", "Garrow G and colleagues",
                                    "Garrow G and col.", "Garrow G…", "Garrow G..."])
def test_etal_added_quiet_when_the_source_elides_authors_itself(elided):
    # TAUBPU 202 ("and col."), EQADVR 144 ("et.al."): the rendered "et al."
    # reproduces the source.
    source = _source(authors=f"{_CITE_KEPT}, {elided}")
    stage4 = _cite_run(_publication(202, source, authors=_CITE_KEPT), owner={})
    assert lint_etal_added(stage4, _bibliography(_CUT_LINE)) == []


@pytest.mark.parametrize("editors", ["In: Garrow G, Holloway H, et al., eds.",
                                     "In: Garrow G, et al. eds."])
def test_etal_added_ignores_an_et_al_in_an_editor_list(editors):
    # VVRTUC 703: the "et al." closes the editors after the title, not the authors.
    line = f"Thornquist R, Ashdown A. Chapter on tidal cores. {editors} Synthetic Geology. Larkspur: Mossgrove Press; 2019."
    source = f"Thornquist R, Ashdown A. Chapter on tidal cores. In: Synthetic Geology, Garrow G, Holloway H, Ivesdale I, Jessop J, Larkspur, Mossgrove Press, 2019."
    stage4 = _cite_run(_publication(703, source, authors="Thornquist R, Ashdown A", code="S4"))
    assert lint_etal_added(stage4, _bibliography(line)) == []


# ------------- lint 14ab: a field that identifies a citation, left out of its line

_BULLETIN = "Synthetic Pharmacy Bulletin"
_BULLETIN_LINE = f"{_BULLETIN}. May/June 2011;17(3):9."
_UNTITLED_TITLE = "Report on pharmacy volunteer outreach programmes for rural clinics"
_UNTITLED_TEXT = f"{_UNTITLED_TITLE}. {_BULLETIN}. May/June 2011;17(3):9."
_WEBINAR_URL = "https://events.example.org/index.jsp?eid=7712"
_WEBINAR_LINE = "Tidal cores explained for clinicians. Webinar; 2016 Oct 12."


def _cited(idx, text, code="S1", **fields):
    """A publication entry with the stage-4 fields given."""
    return {"element_idx_start": idx, "taxonomy_code": code, "text": text,
            "extracted_fields": fields}


def _field_shapes(stage4, blocks):
    return [(f["severity"], f["message"].split(": ")[1])
            for f in lint_citation_field_dropped(stage4, blocks)]


def test_citation_field_dropped_reports_an_untitled_item_rendered_as_its_venue():
    # KJJVVO-10: the descriptive sentence stage 4 filed as the title is the
    # only thing saying what the item is, and the line is venue, date, pages.
    # "Pharmacy" is on the line, but as the venue's word, not the title's.
    entry = _cited(60, _UNTITLED_TEXT, code="S5", publication_venue=_BULLETIN, year=2011,
                   title=_UNTITLED_TITLE)
    findings = lint_citation_field_dropped(_cite_run(entry), _bibliography(_BULLETIN_LINE))
    assert [(f["lint"], f["severity"], f["message"]) for f in findings] == [(
        "citation_field_dropped", "WARN",
        "entry 60 (S5): title_dropped: no word of stage 4's title is in the rendered "
        "citation, so it does not say what the item is")]
    assert findings[0]["evidence"] == [_BULLETIN_LINE]


@pytest.mark.parametrize(("title", "line"), [
    # One title word on the line is a title, reworded or cut, not a drop.
    (_UNTITLED_TITLE, f"Outreach. {_BULLETIN_LINE}"),
    # Two words beyond the venue's own are too few to judge.
    ("Pharmacy bulletin outreach report", _BULLETIN_LINE),
])
def test_citation_field_dropped_quiet_on_a_title_it_cannot_judge_lost(title, line):
    entry = _cited(60, _UNTITLED_TEXT, code="S5", publication_venue=_BULLETIN, title=title)
    assert lint_citation_field_dropped(_cite_run(entry), _bibliography(line)) == []


def test_citation_field_dropped_quiet_when_another_line_prints_the_title():
    # The entry paired with the wrong line: the title is on the page.
    entry = _cited(60, _UNTITLED_TEXT, code="S5", publication_venue=_BULLETIN,
                   title=_UNTITLED_TITLE)
    blocks = _bibliography(_BULLETIN_LINE, "Garrow G. Volunteer outreach programmes. Synth Rural Med. 2005;3:1-9.")
    assert lint_citation_field_dropped(_cite_run(entry), blocks) == []
    # Exactly half of the title's six words is enough.
    half = "Garrow G. Volunteer outreach for clinics. Synth Med. 2005;3:1-9."
    assert _field_shapes(_cite_run(entry), _bibliography(_BULLETIN_LINE, half)) == []


def test_citation_field_dropped_reports_a_webinar_rendered_without_its_link():
    # UXBHHF-20: stage 4 kept the URL; the 5d citation left it out.
    entry = _cited(269, f"{_WEBINAR_LINE} {_WEBINAR_URL}", code="S9",
                   title="Tidal cores explained for clinicians", url=_WEBINAR_URL)
    findings = lint_citation_field_dropped(_cite_run(entry), _bibliography(_WEBINAR_LINE))
    assert [(f["severity"], f["message"]) for f in findings] == [(
        "INFO",
        "entry 269 (S9): url_dropped: the rendered citation leaves out the link stage 4 "
        "kept, and gives no DOI, PMID or volume and pages instead")]


@pytest.mark.parametrize(("url", "line"), [
    (_WEBINAR_URL, f"{_WEBINAR_LINE} {_WEBINAR_URL}"),                 # printed
    ("www.events.example.org/tidal", f"{_WEBINAR_LINE} events.example.org/tidal"),
    (_WEBINAR_URL, f"{_WEBINAR_LINE} doi:10.0000/tidal.1"),             # a DOI locates it
    (_WEBINAR_URL, f"{_WEBINAR_LINE} PMID:12345678."),
    (_WEBINAR_URL, "Tidal cores explained for clinicians. Synth Geol. 2018;12(3):45-67."),
    ("https://doi.org/10.0000/tidal.1", _WEBINAR_LINE),                # a DOI, not a link
    ("10.0000/tidal.1", _WEBINAR_LINE),
    ("interview link here", _WEBINAR_LINE),                             # not a URL
])
def test_citation_field_dropped_quiet_on_a_link_that_is_not_lost(url, line):
    entry = _cited(269, f"{_WEBINAR_LINE} {url}", code="S9",
                   title="Tidal cores explained for clinicians", url=url)
    assert lint_citation_field_dropped(_cite_run(entry), _bibliography(line)) == []


def test_citation_field_dropped_quiet_on_a_link_the_source_does_not_give():
    entry = _cited(269, f"{_WEBINAR_LINE} {_WEBINAR_URL}", code="S9",
                   title="Tidal cores explained for clinicians",
                   url="https://other.example.net/page")
    assert lint_citation_field_dropped(_cite_run(entry), _bibliography(_WEBINAR_LINE)) == []


_ELIDED_SOURCE = f"Ashdown A, Brimley B ... Thornquist R. {_CITE_TITLE}. Annual Synthetic Geology Meeting; 2001."
_ELIDED_LINE = f"Ashdown A, Brimley B, Thornquist R. {_CITE_TITLE}. Annual Synthetic Geology Meeting; 2001."


@pytest.mark.parametrize("marker", ["...", "\u2026", "[...]", "et al.,"])
def test_citation_field_dropped_reports_an_elided_list_rendered_as_complete(marker):
    # VPMMFM-08: the source elides the middle of the author list; the line
    # prints the names on either side as the whole list.
    source = _ELIDED_SOURCE.replace("...", marker)
    entry = _cited(410, source, code="S8", title=_CITE_TITLE,
                   authors="Ashdown A, Brimley B, Thornquist R")
    assert _field_shapes(_cite_run(entry), _bibliography(_ELIDED_LINE)) == [
        ("INFO", "elision_dropped")]


@pytest.mark.parametrize(("source", "line"), [
    # The line keeps the elision.
    (_ELIDED_SOURCE, _ELIDED_LINE.replace("Brimley B,", "Brimley B, et al.,")),
    # A PubMed rebuild prints the whole list.
    (_ELIDED_SOURCE, _ELIDED_LINE + " PMID:12345678."),
    # The ellipsis is in the venue, after the title, not in the author list.
    (_ELIDED_SOURCE.replace(" ... ", ", ").replace("Annual", "Annual ..."), _ELIDED_LINE),
    # No title phrase in the source to tell the author list from the rest.
    (_ELIDED_SOURCE.replace(_CITE_TITLE, "Tidal sediment patterns"), _ELIDED_LINE),
])
def test_citation_field_dropped_quiet_on_an_elision_that_is_not_lost(source, line):
    entry = _cited(410, source, code="S8", title=_CITE_TITLE,
                   authors="Ashdown A, Brimley B, Thornquist R")
    assert _field_shapes(_cite_run(entry), _bibliography(line)) == []


def test_citation_field_dropped_judges_no_elision_without_a_title():
    entry = _cited(410, _ELIDED_SOURCE, code="S8", title="",
                   authors="Ashdown A, Brimley B, Thornquist R")
    assert _field_shapes(_cite_run(entry), _bibliography(_ELIDED_LINE)) == []


def test_citation_field_dropped_is_a_registered_lint():
    from unified_pipeline.run_doctor import KNOWN_LINTS, LINT_REGISTRY
    assert "citation_field_dropped" in KNOWN_LINTS
    assert any(spec.lint_id == "citation_field_dropped"
               and spec.rule is lint_citation_field_dropped
               and spec.inputs == ("stage_4", "blocks") for spec in LINT_REGISTRY)


def test_owner_lints_are_registered_lints():
    from unified_pipeline.run_doctor import KNOWN_LINTS, LINT_REGISTRY
    for lint_id, rule in (("owner_missing_from_citation", lint_owner_missing_from_citation),
                          ("etal_added", lint_etal_added)):
        assert lint_id in KNOWN_LINTS
        assert any(spec.lint_id == lint_id and spec.rule is rule
                   and spec.inputs == ("stage_4", "blocks") for spec in LINT_REGISTRY)


def test_owner_lint_prevalence_is_the_measured_farm_fraction():
    """Runs with a finding, measured 2026-10-02 over the 63-run EBYSBC, s7ab
    and pilot farm (#1259); a new measurement updates both sides."""
    from unified_pipeline.run_doctor import LINT_PREVALENCE
    assert LINT_PREVALENCE["owner_missing_from_citation"] == round(30 / 63, 3)
    assert LINT_PREVALENCE["etal_added"] == round(59 / 63, 3)


def test_run_doctor_reads_the_tracked_insertion_not_the_deleted_source(tmp_path):
    """#1259 end to end, the OIYKZE-02 shape: stage 6 replaced the CV line
    with a tracked deletion plus a tracked insertion of a citation without
    the owner. The owner survives only as w:delText, which accepting the
    changes removes, so the finding must come from the inserted text."""
    from docx.oxml import parse_xml
    from docx.oxml.ns import nsdecls
    root = _build_clean_run(tmp_path)
    fields_path = next((root / "stage_4_field_extraction").glob(f"{_UID}*_fields.json"))
    stage4 = json.loads(fields_path.read_text())
    authors = f"{_CITE_KEPT}, Garrow G, Shapiro M"
    stage4["entries"].append(_publication(115, _source(authors=authors), authors=authors))
    fields_path.write_text(json.dumps(stage4))
    docx_path = next((root / "stage_6_wcm_documents").glob(f"{_UID}*_wcm.docx"))
    doc = Document(str(docx_path))
    doc.add_paragraph("BIBLIOGRAPHY")
    para = doc.add_paragraph("1. ")
    para._p.append(parse_xml(f'<w:del {nsdecls("w")} w:id="1" w:author="t"><w:r>'
                             f'<w:delText>{_source(authors=authors)}</w:delText></w:r></w:del>'))
    para._p.append(parse_xml(f'<w:ins {nsdecls("w")} w:id="2" w:author="t"><w:r>'
                             f'<w:t>{_CUT_LINE}</w:t></w:r></w:ins>'))
    doc.save(str(docx_path))

    payload = run_doctor(root, _UID)

    owner = [f for f in payload["findings"] if f["lint"] == "owner_missing_from_citation"]
    assert [(f["severity"], f["evidence"]) for f in owner] == [
        ("WARN", [_CUT_LINE[:CITATION_EVIDENCE_CHARS]])]
    assert owner[0]["message"].startswith("entry 115 (S1): ")


# --------------------------------------------- lint 14p: date_cell_shape

def _dated4(text, code, start=None, end=None, *, idx, heading="Example Heading"):
    """One stage-4 entry carrying a date range, with invented content."""
    return _entry(text, start=idx, hierarchy=[heading], taxonomy_code=code,
                  extracted_fields={"start_date": start, "end_date": end})


_COMMITTEE_ROW = [["Example Widget Committee", "Member", "2015-Present"]]


def test_date_cell_shape_warns_on_an_open_range_the_source_never_states():
    """EBYSBC E9: a one-year committee row printed as still running."""
    stage4 = {"entries": [_dated4("Example Widget Committee, member, 2015",
                                  "P", "2015", "present", idx=12)]}
    findings = lint_date_cell_shape(stage4, [_COMMITTEE_ROW])
    assert [(f["lint"], f["severity"], f["evidence"]) for f in findings] == [
        ("date_cell_shape", "WARN", ["2015-Present"])]
    assert findings[0]["message"].startswith("entry 12 (P): open_range:")


@pytest.mark.parametrize("word", ["and", "the", "for", "with", "from"])
def test_date_cell_shape_does_not_tie_a_date_to_an_entry_by_function_words(word):
    """The row and the entry share one real word; a function word must not
    make up the second, or the row is read as an entry it is not."""
    stage4 = {"entries": [_dated4(f"Head {word} Example Unit, 2015", "P", "2015",
                                  "present", idx=12)]}
    rows = [[[f"Chair {word} Example Board", "2015-Present"]]]
    assert lint_date_cell_shape(stage4, rows, []) == []


@pytest.mark.parametrize("text", [
    "Example Widget Committee, member, 2015 - present",
    "Example Widget Committee, member since 2015",
    "Example Widget Committee, member, 2015 \u2013 date",
    "Example Widget Committee, member, 2015 to date",
    "Example Widget Committee, member, 2015-",
    "Example Widget Committee, member, 2015 -",
    "Example Widget Committee, member (2015- )",
    "2015 \u2013 Example Widget Committee, member",
    "Example Widget Committee, member, 2015, currently chair",
    "Example Widget Committee, member, 2015; ongoing",
    "Example Widget Committee, member from 2015 to now",
    "Example Widget Committee, active member, 2015",
    "Example Widget Committee, member, 2015, continuing",
    "Example Widget Committee, member, 2015.04- chair",
    "2015.04 - Example Widget Committee, member",
    "Example Widget Committee, member (2015 - )",
    # "YYYY -<tab>" in the middle of a line: the reader's tab after a dash
    # with no end year, read by the marker's second branch, not the
    # line-opening one.
    "Example Widget Committee, member, 2015 -\tSample College",
    # A later line of the entry that opens "YYYY - <text>".
    "Example Widget Committee\n2015 - member of the board",
    # The line-opening "YYYY - " marker after a bullet or bracket.
    "\u2022 2015 - Example Widget Committee, member",
])
def test_date_cell_shape_spares_an_open_range_the_source_states(text):
    stage4 = {"entries": [_dated4(text, "P", "2015", "present", idx=12)]}
    assert lint_date_cell_shape(stage4, [_COMMITTEE_ROW], []) == []


def test_date_cell_shape_reads_an_open_marker_in_the_entry_heading():
    stage4 = {"entries": [_dated4("Example Widget Committee, member, 2015", "P",
                                  "2015", None, idx=12,
                                  heading="Current Committee Service")]}
    assert lint_date_cell_shape(stage4, [_COMMITTEE_ROW], []) == []


def test_date_cell_shape_reads_an_open_marker_in_the_entry_context_heading():
    entry = _dated4("Example Widget Committee, member, 2015", "P", "2015", None,
                    idx=12)
    entry["context_heading"] = "Current Committee Service"
    assert lint_date_cell_shape({"entries": [entry]}, [_COMMITTEE_ROW], []) == []


def test_date_cell_shape_does_not_read_a_closed_range_as_a_trailing_dash():
    """A closed range in the entry's text ("2015-2016") is a dash followed by
    a year, not the trailing dash that marks an open range, so the open
    '-Present' row is still reported."""
    stage4 = {"entries": [_dated4("Example Widget Committee, member, 2015-2016",
                                  "P", "2015", "present", idx=12)]}
    findings = lint_date_cell_shape(stage4, [_COMMITTEE_ROW], [])
    assert [(f["severity"], f["evidence"]) for f in findings] == [
        ("WARN", ["2015-Present"])]


def test_date_cell_shape_does_not_read_a_year_inside_a_longer_number_as_a_dash():
    """A reference number that ends in a year-like run and a dash
    ("12015-") is not a year with a trailing dash, so the row is still
    reported."""
    stage4 = {"entries": [_dated4("Example Widget Committee, member, 2015, "
                                  "ref 12015-", "P", "2015", "present", idx=12)]}
    findings = lint_date_cell_shape(stage4, [_COMMITTEE_ROW], [])
    assert [(f["severity"], f["evidence"]) for f in findings] == [
        ("WARN", ["2015-Present"])]


def test_date_cell_shape_reads_the_dash_marker_in_the_entry_text_only():
    """A heading that opens with a year ("2015 - Committee Service") groups
    entries by year; it does not say a row is still open. The words of the
    heading are read for a marker, its dash is not."""
    stage4 = {"entries": [_dated4("Example Widget Committee, member, 2015", "P",
                                  "2015", "present", idx=12,
                                  heading="2015 - Committee Service")]}
    findings = lint_date_cell_shape(stage4, [_COMMITTEE_ROW], [])
    assert [(f["severity"], f["evidence"]) for f in findings] == [
        ("WARN", ["2015-Present"])]


@pytest.mark.parametrize("number", ["12015", "20151"])
def test_date_cell_shape_reads_entry_years_only_as_whole_four_digit_numbers(number):
    """An entry is indexed under a year only where four digits stand alone:
    a reference number that contains "2015" does not make it a 2015 entry,
    so the row ties to nothing and an untied open range is not reported."""
    stage4 = {"entries": [_dated4(f"Example Widget Committee, member, ref {number}",
                                  "P", None, "present", idx=12)]}
    assert lint_date_cell_shape(stage4, [_COMMITTEE_ROW], []) == []


@pytest.mark.parametrize("code", ["D1", "I"])
def test_date_cell_shape_leaves_memberships_and_the_latest_rank_open_by_decision(code):
    """#1342 (decision 2026-10-02): I memberships and the CV's latest D1 rank
    keep '<start>-Present', so the lint must not report them. A lone D1 row
    is the latest one."""
    stage4 = {"entries": [_dated4("Example Widget Committee, member, 2015",
                                  code, "2015", None, idx=12)]}
    assert lint_date_cell_shape(stage4, [_COMMITTEE_ROW], []) == []


@pytest.mark.parametrize("code", ["D2", "D3"])
def test_date_cell_shape_warns_on_an_open_d2_or_d3_row(code):
    """#1342 (decision 2026-10-02, superseding #946's): a D2 or D3 row
    follows the source-open-marker rule, so a past post printed as current
    is reported."""
    stage4 = {"entries": [_dated4("Example Widget Lab, research fellow, 2015",
                                  code, "2015", None, idx=12)]}
    rows = [[["Example Widget Lab", "Research Fellow", "2015-Present"]]]
    findings = lint_date_cell_shape(stage4, rows, [])
    assert [(f["severity"], f["evidence"]) for f in findings] == [
        ("WARN", ["2015-Present"])]
    assert findings[0]["message"].startswith(f"entry 12 ({code}): open_range:")


def _two_ranks(earlier_start, later_start):
    """An earlier and a later D1 rank, both rendered '-Present'."""
    stage4 = {"entries": [
        _dated4("Assistant Professor of Widgetry, Example University, 2010",
                "D1", earlier_start, None, idx=12),
        _dated4("Associate Professor of Widgetry, Example University, 2016",
                "D1", later_start, None, idx=13)]}
    rows = [[["Assistant Professor", "Widgetry, Example University", "2010-Present"],
             ["Associate Professor", "Widgetry, Example University", "2016-Present"]]]
    return stage4, rows


def test_date_cell_shape_warns_on_an_open_d1_rank_with_a_later_d1_row():
    """Only the latest D1 rank is current: an earlier rank printed
    '-Present' is reported, the latest one is not."""
    stage4, rows = _two_ranks("2010", "2016")
    findings = lint_date_cell_shape(stage4, rows, [])
    assert [(f["message"].split(": ")[0], f["severity"], f["evidence"])
            for f in findings] == [("entry 12 (D1)", "WARN", ["2010-Present"])]


def test_date_cell_shape_reads_a_d1_start_year_from_the_text_without_a_start_date():
    """A D1 row with no start_date is ordered by the first year of its text,
    so the 2010 rank is still the earlier one, a later year in its text
    notwithstanding."""
    stage4, rows = _two_ranks(None, None)
    stage4["entries"][0]["text"] += " (renewed 2019)"
    findings = lint_date_cell_shape(stage4, rows, [])
    assert [(f["message"].split(": ")[0], f["evidence"]) for f in findings] == [
        ("entry 12 (D1)", ["2010-Present"])]


def test_date_cell_shape_orders_d1_ranks_by_start_date_before_text():
    """The start year is the stage-4 start_date's when there is one: a
    degree year earlier in the text does not make the later rank the
    earlier one."""
    stage4, rows = _two_ranks("2010", "2016")
    stage4["entries"][1]["text"] = ("Associate Professor of Widgetry, Example "
                                    "University (PhD 2005), 2016")
    findings = lint_date_cell_shape(stage4, rows, [])
    assert [(f["message"].split(": ")[0], f["evidence"]) for f in findings] == [
        ("entry 12 (D1)", ["2010-Present"])]


def test_date_cell_shape_judges_the_latest_rank_among_d1_rows_only():
    """A D2 or D3 row neither is the latest rank nor moves it: a D2 row that
    starts the rank's year and a D3 row that starts later are both
    reported, and the D1 rank is not."""
    stage4 = {"entries": [
        _dated4("Associate Professor of Widgetry, Example University, 2016",
                "D1", "2016", None, idx=12),
        _dated4("Visiting Scholar, Sample Gadget Institute, 2016",
                "D2", "2016", None, idx=13),
        _dated4("Consultant, Demo Sprocket Clinic, 2019",
                "D3", "2019", None, idx=14)]}
    rows = [[["Associate Professor", "Widgetry, Example University", "2016-Present"],
             ["Visiting Scholar", "Sample Gadget Institute", "2016-Present"],
             ["Consultant", "Demo Sprocket Clinic", "2019-Present"]]]
    findings = lint_date_cell_shape(stage4, rows, [])
    assert [(f["message"].split(": ")[0], f["severity"]) for f in findings] == [
        ("entry 13 (D2)", "WARN"), ("entry 14 (D3)", "WARN")]


def test_date_cell_shape_leaves_every_d1_rank_sharing_the_latest_start_year():
    """Two D1 ranks that start the same latest year have no later D1 row,
    so neither is reported."""
    stage4, rows = _two_ranks("2016", "2016")
    rows = [[["Assistant Professor", "Widgetry, Example University", "2016-Present"],
             ["Associate Professor", "Widgetry, Example University", "2016-Present"]]]
    for entry in stage4["entries"]:
        entry["text"] = entry["text"].replace("2010", "2016")
    assert lint_date_cell_shape(stage4, rows, []) == []


def test_date_cell_shape_ignores_a_d1_row_with_no_year_for_the_latest_rank():
    """A D1 row with no year in its start_date or its text has no start year:
    it neither breaks the ordering (max over a year and None) nor moves the
    latest rank, so the dated D1 rank is still left open by decision."""
    stage4, rows = _two_ranks(None, "2016")
    stage4["entries"][0]["text"] = ("Assistant Professor of Widgetry, "
                                    "Example University")
    assert lint_date_cell_shape(stage4, rows, []) == []


@pytest.mark.parametrize("code, text, cell, shape", [
    ("D1", "Example Widget Lab, research fellow, 03/2010-05/2012",
     "2010-03-2012-05", "raw_value"),
    ("I", "Example Widget Lab, research fellow, 2014", "2014-2014", "same_ends"),
    ("P", "Example Widget Lab, research fellow, 2014, currently chair",
     "2014-2014", "same_ends"),
    # A twentieth-century year ties and is read like any other.
    ("P", "Example Widget Lab, research fellow, 1999", "1999-1999", "same_ends"),
])
def test_date_cell_shape_reports_a_raw_or_same_ended_date_on_any_entry(
        code, text, cell, shape):
    """The D/I decision and the open markers spare only '-Present': a raw
    value or a same-ended range is wrong on a position, a membership, or an
    entry whose text says it is ongoing."""
    year = re.search(r"\d{4}", cell).group(0)
    stage4 = {"entries": [_dated4(text, code, year, None, idx=12)]}
    rows = [[["Example Widget Lab", "Research Fellow", cell]]]
    findings = lint_date_cell_shape(stage4, rows, [])
    assert [(f["severity"], f["evidence"]) for f in findings] == [("INFO", [cell])]
    assert findings[0]["message"].startswith(f"entry 12 ({code}): {shape}:")


_MENTEE_TABLE =[["Name:", "Avery Quill"],
                 ["Site/Position:", "Example University, doctoral student"],
                 ["Mentoring Period:", "2014-02-2016-11"]]


def _mentee_stage4(text):
    return {"entries": [
        _dated4(text, "N3B", "2014-02", "2016-11", idx=40),
        _dated4("Blake Rowan, resident, Sample Hospital, 2018", "N3B", "2018",
                None, idx=41)]}


def test_date_cell_shape_ties_a_mentee_period_to_its_table_and_reports_raw_iso():
    """EBYSBC E21: the period row carries only a label, so the mentee is
    found through the other value cells of the same one-mentee table."""
    stage4 = _mentee_stage4("Avery Quill, doctoral student, Example University, "
                            "02/2014-11/2016")
    findings = lint_date_cell_shape(stage4, [_MENTEE_TABLE], [])
    assert [(f["severity"], f["evidence"]) for f in findings] == [
        ("INFO", ["2014-02-2016-11"])]
    assert findings[0]["message"].startswith("entry 40 (N3B): raw_value:")


def test_date_cell_shape_spares_a_date_the_source_writes_the_same_way():
    stage4 = _mentee_stage4("Avery Quill, doctoral student, Example University, "
                            "2014-02-2016-11")
    assert lint_date_cell_shape(stage4, [_MENTEE_TABLE], []) == []


def test_date_cell_shape_reports_a_dotted_date_the_source_writes_the_same_way():
    """RCBKFG KUUKNJ N4: the source dates its rows "YYYY.MM - ..." and stage
    6 printed the stored "2021.07" as it stood. No WCM date format has a dot,
    so the source's own spelling does not spare it."""
    stage4 = {"entries": [_dated4("2021.07 - Example Widget Panel, member",
                                  "Q3", "2021.07", None, idx=162)]}
    rows = [[["Example Widget Panel", "Member", "2021.07"]]]
    findings = lint_date_cell_shape(stage4, rows, [])
    assert [(f["severity"], f["evidence"]) for f in findings] == [("INFO", ["2021.07"])]
    assert findings[0]["message"].startswith("entry 162 (Q3): raw_value:")


@pytest.mark.parametrize("cell, shapes", [
    ("2003-04-2005-09", ["raw_value"]),
    ("2004-10-07", ["raw_value"]),
    ("2001-06-2004", ["raw_value"]),
    ("2009-Summer", ["raw_value"]),
    ("2012-Fall-present", ["open_range", "raw_value"]),
    ("2010-03-present", ["open_range", "raw_value"]),
    ("2019-2019", ["same_ends"]),
    ("05/2019-05/2019", ["same_ends"]),
    ("October 2008 to October 2008", ["same_ends"]),
    # Ends compared normalized: a no-break space or a case change is the
    # same date.
    ("October 2008 to October 2008", ["same_ends"]),
    ("Fall 2019 - fall 2019", ["same_ends"]),
    ("2004-10-07-2004-10-07", ["raw_value", "same_ends"]),
    ("2003-2005-09", ["raw_value"]),
    # A dotted month or day (RCBKFG KUUKNJ N4), alone or on either side.
    ("2021.07", ["raw_value"]),
    ("2021.7.15", ["raw_value"]),
    ("2017-2021.03", ["raw_value"]),
    ("2022.07-2026.06", ["raw_value"]),
    ("2021.07-present", ["open_range", "raw_value"]),
    ("2015-ongoing", ["open_range"]),
    ("2015-current", ["open_range"]),
    ("Sept 2008 - Sept 2008", ["same_ends"]),
    ("2009-summer", ["raw_value"]),
    ("2013\u20132013", ["same_ends"]),
    ("Oct. 2008 - Oct. 2008", ["same_ends"]),
    ("05/01/2008-05/01/2008", ["same_ends"]),
    # Every month and season word is read, in full and short.
    ("June 2002 to June 2002", ["same_ends"]),
    ("July 2002 to July 2002", ["same_ends"]),
    ("May 2008 - May 2008", ["same_ends"]),
    ("Autumn 2019 - Autumn 2019", ["same_ends"]),
    ("January 2002 to January 2002", ["same_ends"]),
    ("February 2002 to February 2002", ["same_ends"]),
    ("March 2002 to March 2002", ["same_ends"]),
    ("April 2002 to April 2002", ["same_ends"]),
    ("August 2002 to August 2002", ["same_ends"]),
    ("September 2002 to September 2002", ["same_ends"]),
    ("November 2002 to November 2002", ["same_ends"]),
    ("December 2002 to December 2002", ["same_ends"]),
    # Every season word is read as a raw stored value.
    ("2009-Spring", ["raw_value"]),
    ("2009-Fall", ["raw_value"]),
    ("2009-Autumn", ["raw_value"]),
    ("2009-Winter", ["raw_value"]),
    ("2013\u20142013", ["same_ends"]),
    ("2019-2020", []),
    # An ISO side needs a real month and day: "2004-13-07" and "2004-10-32"
    # are not dates the lint reads.
    ("2004-13-07", []),
    ("2004-10-32", []),
    # A dot after the year needs a real month: "2021.13" and "2021.075" are
    # not dates the lint reads.
    ("2021.13", []),
    ("2021.075", []),
    # Only a 19xx/20xx run is read as a year to tie by.
    ("1850-1850", []),
    ("2019", []),
    ("2008-12", []),
    ("09/2019-05/2021", []),
    ("October 2008 to December 2008", []),
])
def test_date_cell_shape_classifies_each_cell_shape(cell, shapes):
    year = re.search(r"\d{4}", cell).group(0)
    stage4 = {"entries": [_dated4("Avery Quill, Example University", "N3A",
                                  year, None, idx=7)]}
    findings = lint_date_cell_shape(
        stage4, [[["Avery Quill", "Example University", cell]]], [])
    assert [f["message"].split(": ")[1] for f in findings] == shapes
    assert all(f["evidence"] == [cell] for f in findings)


def test_date_cell_shape_skips_a_same_ended_cell_with_no_four_digit_year():
    """Positions and grants render mm/yy cells. One with equal ends has no
    four-digit year to tie it to an entry by, so it is not read, and must
    not crash the lint."""
    stage4 = {"entries": [_dated4("Example Widget Committee, member, 05/2019",
                                  "P", "2019-05", "2019-05", idx=12)]}
    rows = [[["Example Widget Committee", "Member", "05/19-05/19"]]]
    blocks = [("p", "05/19-05/19 - Example Widget Committee, member")]
    assert lint_date_cell_shape(stage4, rows, blocks) == []


def test_date_cell_shape_reads_each_line_of_a_table_cell():
    """A cell holding a role on one line and its date on the next."""
    stage4 = {"entries": [_dated4("Avery Quill, doctoral student, Example "
                                  "University, 2014", "N3B", "2014", None, idx=7)]}
    rows = [[["Avery Quill", "Example University", "Doctoral student\n2014-2014"]]]
    findings = lint_date_cell_shape(stage4, rows, [])
    assert [(f["severity"], f["evidence"]) for f in findings] == [
        ("INFO", ["2014-2014"])]
    assert findings[0]["message"].startswith("entry 7 (N3B): same_ends:")


def test_date_cell_shape_reads_a_cell_only_when_it_is_wholly_a_date():
    """A cell that opens with a date and goes on with words is a title, not
    a date cell."""
    stage4 = {"entries": [_dated4("Example Widget annual meeting, Sample "
                                  "College, 2013", "K1", "2013", "2013", idx=7)]}
    rows = [[["Example Widget", "2013-2013 annual meeting"]]]
    assert lint_date_cell_shape(stage4, rows, []) == []


def test_date_cell_shape_ties_each_row_of_a_record_table_to_its_own_entry():
    """In a table of one record per row, a date's context is its own row,
    not the whole table: with the whole table, both entries tie and neither
    row's date would be named."""
    stage4 = {"entries": [
        _dated4("Avery Quill, Example University, 2014", "N3B", "2014", None,
                idx=1),
        _dated4("Blake Rowan, Sample Hospital, 2014", "N3B", "2014", "present",
                idx=2)]}
    rows = [[["Avery Quill", "Example University", "2014-2014"],
             ["Blake Rowan", "Sample Hospital", "2014-Present"]]]
    findings = lint_date_cell_shape(stage4, rows, [])
    assert [(f["message"].split(": ")[0], f["severity"], f["evidence"])
            for f in findings] == [
        ("entry 1 (N3B)", "INFO", ["2014-2014"]),
        ("entry 2 (N3B)", "WARN", ["2014-Present"])]


@pytest.mark.parametrize("colon", [":", ": "])
def test_date_cell_shape_keeps_label_words_out_of_a_record_table_context(colon):
    """In a label/value table the labels ("Mentoring Period:") are the same
    in every record, so they must not tie the date to an entry that merely
    uses those words. A label is read with or without trailing space."""
    stage4 = {"entries": [
        _dated4("Mentoring period, Zephyr Hall, 2019", "N3B", "2019", None,
                idx=1),
        _dated4("Zephyr Quillon, doctoral student, 2019", "N3B", "2019", None,
                idx=2)]}
    rows = [[[f"Mentee Name{colon}", "Zephyr Quillon"],
             [f"Mentoring Period{colon}", "2019-2019"]]]
    findings = lint_date_cell_shape(stage4, rows, [])
    assert [f["message"].split(": ")[0] for f in findings] == ["entry 2 (N3B)"]


def test_date_cell_shape_ties_a_date_through_the_entry_extracted_fields():
    """The entry's text abbreviates the name and leaves out the school that
    the row prints; stage 4's fields carry them, so the row still ties."""
    entry = _entry("Quill A, doctoral student, 2014", start=7, taxonomy_code="N3B",
                   extracted_fields={"mentee_name": "Avery Quill",
                                     "institution": "Example University",
                                     "start_date": "2014", "end_date": "2014"})
    rows = [[["Avery Quill", "Example University", "2014-2014"]]]
    findings = lint_date_cell_shape({"entries": [entry]}, rows, [])
    assert [f["message"].split(": ")[0] for f in findings] == ["entry 7 (N3B)"]


def test_date_cell_shape_ties_by_two_short_words_such_as_a_full_name():
    """Two shared words tie a date, and a three-letter surname is one of
    them: the row holds only the mentee's name."""
    stage4 = {"entries": [_dated4("Avery Roe, doctoral student, Example "
                                  "University, 2014", "N3B", "2014", None, idx=7)]}
    findings = lint_date_cell_shape(stage4, [[["Avery Roe", "2014-2014"]]], [])
    assert [f["message"].split(": ")[0] for f in findings] == ["entry 7 (N3B)"]


def test_date_cell_shape_does_not_tie_a_date_by_a_shared_year():
    """The row and the entry share one word and the year 2016. A year is not
    a word that ties: every entry of that year would share it, so the date
    stays untied."""
    stage4 = {"entries": [_dated4("Example Prize, Sample College, 2015, 2016",
                                  "L", "2015", "2016", idx=7)]}
    findings = lint_date_cell_shape(
        stage4, [[["Example Award", "2016", "2015-2015"]]], [])
    assert [f["message"].split(": ")[0] for f in findings] == ["same_ends"]


def test_date_cell_shape_quotes_each_rendered_date_once_up_to_the_limit():
    text = "Example Lecture series, Sample School, 2013, 2014, 2015, 2016"
    stage4 = {"entries": [_dated4(text, "K1", "2013", "2016", idx=7)]}
    cells = ["2013-2013", "2013-2013", "2014-2014", "2015-2015", "2016-2016"]
    rows = [[["Example Lecture", "Sample School", cell] for cell in cells]]
    findings = lint_date_cell_shape(stage4, rows, [])
    assert [f["evidence"] for f in findings] == [
        ["2013-2013", "2014-2014", "2015-2015"]]


def test_date_cell_shape_reads_a_date_that_opens_a_paragraph():
    stage4 = {"entries": [
        _dated4("Lecture on example topics, Example Medical School, 2013",
                "K1", "2013", "2013", idx=7),
        _dated4("Example seminar series, Sample College, October 14 and 16, 2008",
                "K1", "2008-10-14", "2008-10-16", idx=8)]}
    blocks = [("p", "2013-2013 - Lecture on example topics, Example Medical School"),
              ("p", "October 2008 to October 2008 - Example seminar series, Sample College"),
              ("p", "12. Doe J, Roe R. Example seminar series. J Example. "
                    "Published 2008-10-14, 12(3):1-9.")]
    findings = lint_date_cell_shape(stage4, [], blocks)
    assert [(f["message"].split(": ")[0], f["severity"], f["evidence"])
            for f in findings] == [
        ("entry 7 (K1)", "INFO", ["2013-2013"]),
        ("entry 8 (K1)", "INFO", ["October 2008 to October 2008"])]


@pytest.mark.parametrize("line, rendered", [
    ("\u2022 2013-2013 - Lecture on example topics, Example Medical School",
     "2013-2013"),
    ("2013-2013: Lecture on example topics, Example Medical School", "2013-2013"),
    ("2013-2013, Lecture on example topics, Example Medical School", "2013-2013"),
    ("2013-2013\tLecture on example topics, Example Medical School", "2013-2013"),
    ("oct 2013 to oct 2013 - Lecture on example topics, Example Medical School",
     "oct 2013 to oct 2013"),
    ("2013-2013 \u2013 Lecture on example topics, Example Medical School",
     "2013-2013"),
    ("2013-2013 \u2014 Lecture on example topics, Example Medical School",
     "2013-2013"),
    ("\u00b7 2013-2013 - Lecture on example topics, Example Medical School",
     "2013-2013"),
    ("* 2013-2013 - Lecture on example topics, Example Medical School",
     "2013-2013"),
    ("   2013-2013 - Lecture on example topics, Example Medical School",
     "2013-2013"),
])
def test_date_cell_shape_reads_each_dated_paragraph_shape(line, rendered):
    """A bullet before the date; a colon, comma or tab after it; and a
    lowercase month."""
    stage4 = {"entries": [_dated4("Lecture on example topics, Example Medical "
                                  "School, 2013", "K1", "2013", "2013", idx=7)]}
    findings = lint_date_cell_shape(stage4, [], [("p", line)])
    assert [(f["message"].split(": ")[0], f["evidence"]) for f in findings] == [
        ("entry 7 (K1)", [rendered])]


def test_date_cell_shape_does_not_tie_a_paragraph_by_the_words_of_its_own_date():
    """A paragraph's context is the text after its date: the month word of
    the date itself would tie it to whichever entry also names the month."""
    stage4 = {"entries": [
        _dated4("Example seminar, Sample College, October 2008", "K1",
                "2008-10", "2008-10", idx=7),
        _dated4("Example seminar, Other College, 2008", "K1", "2008", "2008",
                idx=8)]}
    blocks = [("p", "October 2008 to October 2008 - Example seminar")]
    findings = lint_date_cell_shape(stage4, [], blocks)
    assert [(f["message"].split(": ")[0], f["evidence"]) for f in findings] == [
        ("same_ends", ["October 2008 to October 2008"])]


def test_date_cell_shape_reads_a_paragraph_that_is_only_a_date():
    """A date alone on its line has no words to tie it, so it is reported
    without an entry."""
    findings = lint_date_cell_shape({"entries": []}, [], [("p", "2013-2013")])
    assert [(f["message"].split(": ")[0], f["evidence"]) for f in findings] == [
        ("same_ends", ["2013-2013"])]


def test_date_cell_shape_quotes_untied_dates_up_to_the_limit():
    """The count is every untied date; the evidence quotes each distinct
    one once, up to the limit."""
    cells = ["2013-2013", "2013-2013", "2014-2014", "2015-2015", "2016-2016"]
    findings = lint_date_cell_shape({"entries": []}, [[[c] for c in cells]], [])
    assert [(f["message"].split(": ")[:2], f["evidence"]) for f in findings] == [
        (["same_ends", "5 rendered date(s) tied to no stage-4 entry"],
         ["2013-2013", "2014-2014", "2015-2015"])]


def test_date_cell_shape_skips_the_appendix():
    """The Appendix prints the CV's own text, so its dates are not a render."""
    stage4 = {"entries": [_dated4("Lecture on example topics, Example Medical "
                                  "School, 2013", "T", "2013", "2013", idx=7)]}
    blocks = [("p", "T. APPENDIX"),
              ("p", "2013-2013 - Lecture on example topics, Example Medical School")]
    assert lint_date_cell_shape(stage4, [], blocks) == []


def test_date_cell_shape_reads_an_appendix_header_with_trailing_space():
    stage4 = {"entries": [_dated4("Lecture on example topics, Example Medical "
                                  "School, 2013", "T", "2013", "2013", idx=7)]}
    blocks = [("p", "T. APPENDIX "),
              ("p", "2013-2013 - Lecture on example topics, Example Medical School")]
    assert lint_date_cell_shape(stage4, [], blocks) == []


def test_date_cell_shape_reads_a_July_same_ended_paragraph():
    """A full month name ("July", "June") opens a dated paragraph too."""
    stage4 = {"entries": [_dated4("Example seminar series, Sample College, "
                                  "July 2002", "K1", "2002-07", "2002-07", idx=8)]}
    blocks = [("p", "July 2002 to July 2002 - Example seminar series, Sample College"),
              ("p", "June 2002 to June 2002 - Example seminar series, Sample College")]
    findings = lint_date_cell_shape(stage4, [], blocks)
    assert [(f["message"].split(": ")[0], f["severity"], f["evidence"])
            for f in findings] == [
        ("entry 8 (K1)", "INFO",
         ["July 2002 to July 2002", "June 2002 to June 2002"])]


@pytest.mark.parametrize("cell, expected", [
    # The open range ties by two-letter words only: not reported.
    ("2014-Present", []),
    # The same-ended range stays untied: one INFO that names no entry.
    ("2014-2014", ["same_ends"]),
])
def test_date_cell_shape_does_not_tie_a_date_by_two_letter_words(cell, expected):
    """Two-letter words ("Ky", "Bo") are too common to tie a date to an
    entry. With them, the row and entry share three words; without them,
    one ("Tamsin"), below DATE_CELL_MIN_SHARED_TOKENS."""
    stage4 = {"entries": [_dated4("Ky Bo Tamsin, committee member, 2014", "R",
                                  "2014", "present", idx=7)]}
    findings = lint_date_cell_shape(stage4, [[["Ky Bo Tamsin", cell]]], [])
    assert [f["message"].split(": ")[0] for f in findings] == expected


def test_date_cell_shape_does_not_read_an_academic_year_as_an_iso_month():
    """'2016-17' is an academic year, not year-month 17: the cell is not a
    date the lint reads, so neither a raw value nor an open range."""
    stage4 = {"entries": [_dated4("Example Widget course director, Sample "
                                  "College, 2016-17 academic year", "K2",
                                  "2016", None, idx=7)]}
    rows = [[["Example Widget course director", "Sample College",
              "2016-17-present"]]]
    assert lint_date_cell_shape(stage4, rows, []) == []


def test_date_cell_shape_reads_a_date_cell_with_surrounding_space():
    stage4 = {"entries": [_dated4("Avery Quill, Example University, 2019",
                                  "N3A", "2019", None, idx=7)]}
    rows = [[["Avery Quill", "Example University", " 2019-2019 "]]]
    findings = lint_date_cell_shape(stage4, rows, [])
    assert [(f["message"].split(": ")[0], f["evidence"]) for f in findings] == [
        ("entry 7 (N3A)", ["2019-2019"])]


@pytest.mark.parametrize("text", [
    "Example Widget Committee, inactive member, 2015",
    "Example Widget Committee, member, 2015, snow survey lead",
])
def test_date_cell_shape_does_not_read_a_marker_inside_a_longer_word(text):
    """'inactive' holds 'active' and 'snow' holds 'now', but neither is an
    open marker."""
    stage4 = {"entries": [_dated4(text, "P", "2015", "present", idx=12)]}
    findings = lint_date_cell_shape(stage4, [_COMMITTEE_ROW], [])
    assert [(f["severity"], f["evidence"]) for f in findings] == [
        ("WARN", ["2015-Present"])]


@pytest.mark.parametrize("text", [
    "Example Widget Committee, member, 2015, presentations lead",
    "Example Widget Committee, member, 2015, nowhere listed",
    "Example Widget Committee, member, 2015, sincere thanks",
])
def test_date_cell_shape_does_not_read_a_marker_at_the_start_of_a_longer_word(text):
    """'presentations' opens with 'present', 'nowhere' with 'now' and
    'sincere' with 'since', but none of them is an open marker."""
    stage4 = {"entries": [_dated4(text, "P", "2015", "present", idx=12)]}
    findings = lint_date_cell_shape(stage4, [_COMMITTEE_ROW], [])
    assert [(f["severity"], f["evidence"]) for f in findings] == [
        ("WARN", ["2015-Present"])]


@pytest.mark.parametrize("text", [
    "Example Widget Committee, member, 2015/03-",
    "2015/03 - Example Widget Committee, member",
])
def test_date_cell_shape_reads_a_slash_month_before_an_open_dash(text):
    """'YYYY/MM-' and a line opening 'YYYY/MM - <text>' are open markers,
    as 'YYYY.MM-' is."""
    stage4 = {"entries": [_dated4(text, "P", "2015", "present", idx=12)]}
    assert lint_date_cell_shape(stage4, [_COMMITTEE_ROW], []) == []


@pytest.mark.parametrize("closed", ["2015- 2016", "2015-\t2016"])
def test_date_cell_shape_does_not_read_a_dash_before_a_spaced_end_year_as_open(closed):
    """'2015- 2016' and '2015-<tab>2016' are closed ranges with a space or a
    tab after the dash, not a trailing dash. The tab form also pins the
    space required before the dash in the 'YYYY -<tab>' marker."""
    stage4 = {"entries": [_dated4(f"Example Widget Committee, member, {closed}",
                                  "P", "2015", "present", idx=12)]}
    findings = lint_date_cell_shape(stage4, [_COMMITTEE_ROW], [])
    assert [(f["severity"], f["evidence"]) for f in findings] == [
        ("WARN", ["2015-Present"])]


def test_date_cell_shape_does_not_read_a_date_followed_by_a_bare_dash():
    """A dated paragraph needs text after its separator: '2013-2013 -' with
    nothing after the dash is not read as a dated line."""
    assert lint_date_cell_shape({"entries": []}, [], [("p", "2013-2013 -")]) == []


def test_date_cell_shape_does_not_read_a_line_opening_closed_range_as_open():
    """A line that opens with a closed range ("2015-2016 Example ...") is not
    the line-opening "YYYY - <text>" open marker."""
    stage4 = {"entries": [_dated4("2015-2016 Example Widget Committee, member",
                                  "P", "2015", "present", idx=12)]}
    findings = lint_date_cell_shape(stage4, [_COMMITTEE_ROW], [])
    assert [(f["severity"], f["evidence"]) for f in findings] == [
        ("WARN", ["2015-Present"])]


def test_date_cell_shape_does_not_tie_by_an_empty_extracted_field():
    """An empty field (None) adds no word: it must not tie a row that
    happens to print the word 'None'."""
    stage4 = {"entries": [_dated4("Avery Quill, doctoral student, 2014", "N3B",
                                  "2014", None, idx=7)]}
    rows = [[["Avery Unit", "None listed", "2014-2014"]]]
    findings = lint_date_cell_shape(stage4, rows, [])
    assert [f["message"].split(": ")[0] for f in findings] == ["same_ends"]


def test_date_cell_shape_counts_a_merged_cell_repeated_in_a_row_once():
    """A merged cell reaches the lint once per grid column it spans; it is
    one date on the page."""
    findings = lint_date_cell_shape({"entries": []},
                                    [[["2013-2013", "2013-2013"]]], [])
    assert [f["message"].split(": ")[1] for f in findings] == [
        "1 rendered date(s) tied to no stage-4 entry"]


def test_date_cell_shape_reads_again_after_a_section_header_ends_the_appendix():
    """The same scope rule as lint_date_only_lines: a section header after
    the Appendix closes it."""
    stage4 = {"entries": [_dated4("Lecture on example topics, Example Medical "
                                  "School, 2013", "K1", "2013", "2013", idx=7)]}
    blocks = [("p", "T. APPENDIX"),
              ("p", "U. EXAMPLE SECTION"),
              ("p", "2013-2013 - Lecture on example topics, Example Medical School")]
    findings = lint_date_cell_shape(stage4, [], blocks)
    assert [(f["message"].split(": ")[0], f["evidence"]) for f in findings] == [
        ("entry 7 (K1)", ["2013-2013"])]


@pytest.mark.parametrize("line", [
    "2004-10-07 marked the first meeting of the Example Widget Committee at "
    "Sample College",
    "2013-2013 Example Widget Committee minutes were kept at Sample College",
])
def test_date_cell_shape_does_not_read_a_date_that_runs_into_text(line):
    """A paragraph is read only when its opening date is set off by a dash,
    colon, comma or tab, or stands alone. A date that runs straight into a
    sentence is part of the sentence."""
    stage4 = {"entries": [
        _dated4("Example Widget Committee, founding member, Sample College, "
                "2004", "P", "2004-10-07", None, idx=7),
        _dated4("Example Widget Committee, secretary, Sample College, 2013",
                "P", "2013", "2013", idx=8)]}
    assert lint_date_cell_shape(stage4, [], [("p", line)]) == []


def test_date_cell_shape_reads_tables_only_through_their_cells():
    """A table reaches the lint as `table_rows`, cell by cell, where a cell
    is read only when it is wholly a date. Its flattened block in `blocks`
    is not read as a paragraph."""
    stage4 = {"entries": [_dated4("Lecture on example topics, Example Medical "
                                  "School, 2013", "K1", "2013", "2013", idx=7)]}
    blocks = [("table", "2013-2013 - Lecture on example topics, Example "
                        "Medical School")]
    assert lint_date_cell_shape(stage4, [], blocks) == []


def test_date_cell_shape_reports_an_untied_date_only_when_its_shape_alone_is_wrong():
    """Two entries tie for the row, so neither is named: an equal-ended range
    is still reported, without an entry; an open range has no source text to
    be judged by and is not."""
    text = "Example Lecture, Sample School, 2013, 2012"
    stage4 = {"entries": [_dated4(text, "K1", "2013", "2013", idx=1),
                          _dated4(text, "K1", "2013", "2013", idx=2)]}
    rows = [[["Example Lecture", "Sample School", "2013-2013"],
             ["Example Lecture", "Sample School", "2012-Present"]]]
    findings = lint_date_cell_shape(stage4, rows, [])
    assert [(f["severity"], f["evidence"]) for f in findings] == [
        ("INFO", ["2013-2013"])]
    assert findings[0]["message"].startswith(
        "same_ends: 1 rendered date(s) tied to no stage-4 entry")


def test_date_cell_shape_finds_nothing_in_the_pristine_wcm_template():
    template = str(_TEMPLATE_DOCX_PATH)
    assert lint_date_cell_shape({"entries": []}, read_docx_table_rows(template),
                                read_docx_blocks(template)) == []


def test_run_doctor_wires_date_cell_shape_through_to_the_verdict(tmp_path):
    """End to end: stage-4 entries, a real docx table row and a real body
    paragraph reach the verdict, each as a finding naming its entry. The
    paragraph is read only if the registry passes `blocks` to the lint."""
    root = _build_clean_run(tmp_path)
    fields_path = next((root / "stage_4_field_extraction").glob(f"{_UID}*_fields.json"))
    stage4 = json.loads(fields_path.read_text())
    stage4["entries"].append(_dated4("Example Widget Committee, member, 2015",
                                     "P", "2015", "present", idx=12))
    stage4["entries"].append(_dated4("Lecture on example topics, Example Medical "
                                     "School, 2013", "K1", "2013", "2013", idx=13))
    fields_path.write_text(json.dumps(stage4))
    docx_path = next((root / "stage_6_wcm_documents").glob(f"{_UID}*_wcm.docx"))
    doc = Document(str(docx_path))
    table = doc.add_table(rows=1, cols=3)
    for cell, text in zip(table.rows[0].cells, _COMMITTEE_ROW[0]):
        cell.paragraphs[0].text = text
    appendix = next(p for p in doc.paragraphs if p.text == "T. APPENDIX")
    appendix.insert_paragraph_before(
        "2013-2013 - Lecture on example topics, Example Medical School")
    doc.save(str(docx_path))

    payload = run_doctor(root, _UID)

    found = [f for f in payload["findings"] if f["lint"] == "date_cell_shape"]
    assert [(f["message"].split(": ")[0], f["severity"], f["evidence"])
            for f in found] == [
        ("entry 12 (P)", "WARN", ["2015-Present"]),
        ("entry 13 (K1)", "INFO", ["2013-2013"])]
    assert found[0]["message"].startswith("entry 12 (P): open_range:")
    assert found[1]["message"].startswith("entry 13 (K1): same_ends:")


# ------------------------------------------------------ lint 12: pipe leaks

def test_pipe_leaks_flags_multi_pipe_paragraphs_not_tables():
    blocks = [("p", "M. RESEARCH"),
              ("p", "• FY2023 Award VPR-23-001 | Jung, E. (PI) | Needs "
                    "assessment | Status: Awarded."),
              ("p", "• NBME Stemmler Grant | Jung, E. (PI) | Use of AI | "
                    "Status: Submitted."),
              # grant tables legitimately synthesize ' | ' row joins
              ("table", "Title | PI | Amount | Status")]
    findings = lint_pipe_leaks(blocks)
    assert len(findings) == 1
    assert findings[0]["severity"] == "WARN"
    assert "2 rendered line(s)" in findings[0]["message"]


def test_pipe_leaks_flags_single_pipe_bullet_cluster():
    blocks = [("p", "K. EDUCATIONAL CONTRIBUTIONS")] + [
        ("p", f"• Instructor, Course {i} | June 2020 – Present")
        for i in range(3)]
    findings = lint_pipe_leaks(blocks)
    assert len(findings) == 1
    assert "3 single-pipe bullet(s)" in findings[0]["message"]
    assert "educational contributions" in findings[0]["message"]


def test_pipe_leaks_quiet_below_cluster_threshold_and_in_appendix():
    blocks = [("p", "K. EDUCATIONAL CONTRIBUTIONS"),
              ("p", "• Instructor, Course A | June 2020 – Present"),
              ("p", "T. APPENDIX"),
              ("p", "• leftover | raw | source | line"),
              ("p", "• another | raw | leftover | line"),
              ("p", "• third | raw | leftover | line")]
    assert lint_pipe_leaks(blocks) == []


def test_pipe_leaks_flags_pipe_free_fused_citation():
    fused = ("31. Hyer A, Jung E. AI confidence. OLC Accelerate 2025; "
             "2025 November 20; Orlando, FL. Weissman P, Samuel A. Teaching "
             "large courses. AMEE; 2024 August 26; Basel, Switzerland.")
    blocks = [("p", "S. BIBLIOGRAPHY"), ("p", fused),
              ("p", "30. Normal citation. Journal of Things; 2024 May; 12(3).")]
    findings = lint_pipe_leaks(blocks)
    assert len(findings) == 1
    assert "venue-date" in findings[0]["message"]
    assert findings[0]["evidence"][0].startswith("[bibliography] 31.")


def test_pipe_leaks_one_abstract_at_several_meetings_is_not_fused():
    """EBYSBC: one abstract listing the meetings it was presented at has two
    venue-date wedges with only venue names between them; 0 of 2 such hits
    were real. The fused shape needs a second author list and title."""
    venues = ("12. Doe J, Roe K, Poe L, et al. Widget outcomes in example "
              "cohorts. Example Society Annual Meeting; 2025 Apr; Springfield, "
              "IL; Sample Research Conference; 2025 Jan; Shelbyville, CA.")
    also = ("13. Doe J, Roe K. Gizmo trends. Example Forum; 2021 Mar; Virtual. "
            "Also presented at Sample Symposium; 2020 Nov; Example City, CA.")
    blocks = [("p", "S. BIBLIOGRAPHY"), ("p", venues), ("p", also)]
    assert lint_pipe_leaks(blocks) == []


def test_pipe_leaks_initials_between_wedges_are_not_a_second_citation():
    """'Dr.' and single-letter initials are not sentence ends, so a venue
    named after a person does not read as a second citation's body."""
    line = ("14. Doe J. Widget study. Example Meeting; 2019 May; Example City, "
            "CA; Dr. A. B. Sample Memorial Lecture; 2019 Jun; Other City, NY.")
    assert lint_pipe_leaks([("p", "S. BIBLIOGRAPHY"), ("p", line)]) == []


# ----------------------------------------------------- lint 13: table shape

_HONORS_HEADER = ["Name of award", "Organization", "Date awarded (yyyy)"]
_BLOB = ("Basic Science Innovation in Education Award – Runner-up "
         "Presentation. Saibal Day, Eulho Jung, and Thomas Flagg. Basic "
         "Science Innovation in Education. Uniformed Services University "
         "Education Day, Bethesda, MD, August 2025.")


def test_table_shape_flags_malformed_honors_rows():
    tables = [[_HONORS_HEADER,
               [_BLOB, "MD", ""],
               ["College of Example Studies 2020 Outstanding Thesis Award",
                "College of Example Studies 2020 Outstanding Thesis",
                "2020"],
               ["Distinguished Teaching Award", "Indiana University", "2013"]]]
    findings = lint_table_shape(tables)
    assert len(findings) == 1
    f = findings[0]
    # #816: always INFO now -- the malformed-row count moved to the doctor's
    # `metrics` block (honors_malformed_rows/honors_rows).
    assert f["lint"] == "table_shape" and f["severity"] == "INFO"
    assert "2/3 row(s) malformed" in f["message"]
    assert any("state abbrev" in e for e in f["evidence"])
    assert any("blob" in e for e in f["evidence"])
    assert any("empty date" in e for e in f["evidence"])
    assert any("duplicated in name" in e for e in f["evidence"])


_GRANTOR_NAMED_AWARDS = [
    ("American Society for Cell Biology Postdoc Travel Award",
     "American Society for Cell Biology"),
    ("APS/NIDDK Minority Travel Fellowship Award", "APS/NIDDK"),
    ("RSNA R&E Foundation Roentgen Resident/Fellow Research Award",
     "RSNA R&E Foundation"),
    ("College of Basic Sciences Dean's List", "College of Basic Sciences"),
    ("Japanese Government Monbusho Scholarship", "Japanese Government"),
    # EBYSBC: the org plus nothing but an award word is the award's real
    # name and grantor (5 of 5 farm hits), and so is a name equal to its org.
    ("Example Service Corps Scholarship", "Example Service Corps"),
    ("Example Foundation Fellowship", "Example Foundation"),
    ("Example Honor Society", "Example Honor Society"),
]


def _honors_evidence(name, org, date="2013"):
    findings = lint_table_shape([[_HONORS_HEADER, [name, org, date]]])
    return findings[0]["evidence"] if findings else []


def test_table_shape_does_not_flag_awards_named_after_their_grantor():
    """#889: 20/23 batch-4 hits were these -- org legitimately in the name."""
    for name, org in _GRANTOR_NAMED_AWARDS:
        assert _honors_evidence(name, org) == [], name


def test_table_shape_flags_org_fabricated_from_the_name():
    """#887's shapes: an organization cut out of the award name that carries
    a year or ends in a holder's role word -- no grantor's name does."""
    for name, org in (
            ("College of Example Studies 2015 Outstanding Thesis Award",
             "College of Example Studies 2015 Outstanding Thesis"),
            ("Example University Representative, Example Student Conference",
             "Example University Representative"),
            ("Example Society Fellow Award", "Example Society Fellow"),
            ("Example Academy Member of the Year", "Example Academy Member")):
        ev = _honors_evidence(name, org)
        assert any("duplicated in name" in e for e in ev), name


def test_table_shape_an_org_not_cut_from_the_name_is_not_fabricated():
    """A year or role word in an organization the award name does not
    contain says nothing about the name."""
    for org in ("Example Society 2019 Meeting", "Example University Representative"):
        assert _honors_evidence("Best Poster Award", org) == [], org


def test_table_shape_a_role_word_inside_an_org_name_is_not_a_tell():
    """Only a TRAILING role word marks a role; 'Fellows' and an inner
    'Member' are part of real grantors' names."""
    for name, org in (("Example Fellows Program Award", "Example Fellows Program"),
                      ("Example Member Society Prize", "Example Member Society")):
        assert _honors_evidence(name, org) == [], name


def test_table_shape_org_check_is_linear_on_runs_of_years():
    """A starred-alternation fullmatch backtracked ~13x per listed year; eight
    years plus one more word took minutes. Must stay instant, and an org
    with no year or role word of its own was not fabricated from the name."""
    import time
    org = "Association for Educational Research"
    name = f"{org} " + ", ".join(str(y) for y in range(1990, 2010)) + " Grant"
    start = time.monotonic()
    ev = _honors_evidence(name, org)
    assert time.monotonic() - start < 1.0
    assert not any("duplicated in name" in e for e in ev)


def test_table_shape_award_word_name_without_the_org_is_not_flagged():
    assert _honors_evidence("Award 2019", "Some University") == []


def test_table_shape_initials_and_dr_are_not_sentence_boundaries():
    """#889: 'Dr. Robert D. & Alma W. Moreton ...' is one 64-char name, not a
    blob; two real sentences still are."""
    name = "Dr. Robert D. & Alma W. Moreton Original Research Award for 1997"
    assert _honors_evidence(name, "Southern Medical Association", "1997") == []
    # each guard alone: Dr. only, and single-letter initials only
    assert _honors_evidence("Dr. Smith and Dr. Jones Award",
                            "Some University") == []
    assert _honors_evidence("R. D. Smith and A. W. Jones Award",
                            "Some University") == []
    two = "Best Poster Award. Given at the meeting. Judged by peers."
    assert any("blob" in e for e in _honors_evidence(two, "Some University"))


def _honors_stage4(*entries):
    return {"entries": [{"taxonomy_code": "H", "element_idx_start": idx,
                         "text": text, "extracted_fields": fields}
                        for idx, text, fields in entries]}


_ONE_AWARD = {"award_name": "Example Teaching Award",
              "granting_body": "Example College", "date": "2003-04-15"}


def test_table_shape_warns_when_one_award_renders_as_several_rows():
    """EBYSBC ZDCXIV-02: an award on one line and its 'Organization - date'
    on the next is ONE stage-4 award, but the honors parser renders each
    line as a row."""
    stage4 = _honors_stage4(
        (12, "Example Teaching Award\nExample College - 04/15/2003", _ONE_AWARD),
        (14, "Distinguished Example Prize, Sample Society, 2010",
         {"award_name": "Distinguished Example Prize", "date": "2010"}))
    tables = [[_HONORS_HEADER,
               ["Example Teaching Award", "", ""],
               ["Example College \u2014 04/15/2003", "", ""],
               ["Distinguished Example Prize", "Sample Society", "2010"]]]
    split = [f for f in lint_table_shape(tables, stage4) if "split" in f["message"]]
    assert len(split) == 1
    assert split[0]["severity"] == "WARN"
    assert "1 row(s) split off 1 award entry" in split[0]["message"]
    assert split[0]["evidence"] == ["entry 12 (H): 2 rows from 1 stage-4 award(s)"]
    # Without stage 4 there is nothing to count rows against.
    assert not [f for f in lint_table_shape(tables) if "split" in f["message"]]


def test_table_shape_rows_stage4_extracted_as_awards_are_not_split():
    """A fused list stage 4 split into records (`stage4_records`, or an
    off-schema `awards` list) renders one row per record, rightly."""
    records = [{"award_name": "Example Alpha Award"}, {"award_name": "Example Beta Award"}]
    stage4 = _honors_stage4(
        (20, "Example Alpha Award\nExample Beta Award",
         {"award_name": "Example Beta Award", "stage4_records": records}),
        (22, "Example Gamma Prize\nExample Delta Prize",
         {"awards": [{"name": "Example Gamma Prize"}, {"name": "Example Delta Prize"}]}))
    tables = [[_HONORS_HEADER] + [[name, "", "2001"] for name in (
        "Example Alpha Award", "Example Beta Award",
        "Example Gamma Prize", "Example Delta Prize")]]
    assert lint_table_shape(tables, stage4) == []


def test_table_shape_a_row_two_entries_share_is_traced_to_neither():
    """'Example University' sits in both entries' text, so its row counts
    for no entry: the split count is a floor, never a guess."""
    stage4 = _honors_stage4(
        (30, "Example Merit Award\nExample University", {"award_name": "Example Merit Award"}),
        (32, "Example Service Award\nExample University", {"award_name": "Example Service Award"}))
    tables = [[_HONORS_HEADER, ["Example Merit Award", "", "2001"],
               ["Example University", "", ""], ["Example Service Award", "", "2002"]]]
    assert lint_table_shape(tables, stage4) == []


def _split_evidence(tables, stage4):
    """The evidence of the split finding, [] when there is none."""
    return [ev for f in lint_table_shape(tables, stage4) if "split" in f["message"]
            for ev in f["evidence"]]


# One award whose 'Organization - date' line renders as a second row.
_SPLIT_TEXT = "Example Teaching Award\nExample College - 04/15/2003"
_SPLIT_TABLE = [[_HONORS_HEADER, ["Example Teaching Award", "", ""],
                 ["Example College — 04/15/2003", "", ""]]]


def test_table_shape_only_honors_entries_own_honors_rows():
    """An appointment whose text also names the award is no owner of the
    award's row: counted as one, it would make the row shared and hide the
    split. On the EBYSBC farm, every split finding changes without this."""
    stage4 = _honors_stage4((12, _SPLIT_TEXT, _ONE_AWARD))
    stage4["entries"].append({
        "taxonomy_code": "D1", "element_idx_start": 40,
        "text": "Chair, Example Teaching Award Committee, 2005-2008",
        "extracted_fields": {"position": "Chair"}})
    assert _split_evidence(_SPLIT_TABLE, stage4) == [
        "entry 12 (H): 2 rows from 1 stage-4 award(s)"]


def test_table_shape_a_list_of_plain_values_is_one_award():
    """Only a list of records counts awards. A list of plain values, here two
    bodies that gave one award jointly, is still one award, so its two rows
    are a split."""
    joint = {**_ONE_AWARD, "granting_body": ["Example College", "Sample Society"]}
    assert _split_evidence(_SPLIT_TABLE, _honors_stage4((12, _SPLIT_TEXT, joint))) == [
        "entry 12 (H): 2 rows from 1 stage-4 award(s)"]


def test_table_shape_a_row_without_letters_or_digits_is_no_entrys():
    """A name cell of punctuation only has an empty key, which every line
    contains: it is traced to no entry, even when only one entry exists."""
    stage4 = _honors_stage4((12, "Example Teaching Award, Example College, 2003", _ONE_AWARD))
    tables = [[_HONORS_HEADER, ["Example Teaching Award", "Example College", "2003"],
               ["—", "", ""]]]
    assert _split_evidence(tables, stage4) == []


def test_table_shape_a_row_is_one_line_of_an_entry_not_text_across_a_break():
    """The honors parser renders each source line as a row, so a row is one
    line, or part of one. Entry 82's wrapped "...Example" / "University
    Hospital" can render no "Example University" row, so that row is entry
    80's alone, and both entries are split."""
    stage4 = _honors_stage4(
        (80, "Example Merit Award\nExample University", {"award_name": "Example Merit Award"}),
        (82, "Example Service Award, Example\nUniversity Hospital",
         {"award_name": "Example Service Award"}))
    tables = [[_HONORS_HEADER, ["Example Merit Award", "", "2001"],
               ["Example University", "", ""],
               ["Example Service Award, Example", "", "2002"],
               ["University Hospital", "", ""]]]
    assert _split_evidence(tables, stage4) == [
        "entry 80 (H): 2 rows from 1 stage-4 award(s)",
        "entry 82 (H): 2 rows from 1 stage-4 award(s)"]


def test_table_shape_message_does_not_cite_closed_issue():
    f = lint_table_shape([[_HONORS_HEADER, ["Prize. Given here. Then there.",
                                            "NY", ""]]])[0]
    assert "#229" not in f["message"]


def test_table_shape_ignores_non_honors_tables_and_clean_rows():
    tables = [
        # not honors-shaped: ignored even with a giant cell
        [["Committee", "Role"], [_BLOB, "Chair"]],
        # honors-shaped and clean
        [_HONORS_HEADER, ["Distinguished Teaching Award",
                          "Indiana University", "2013"]],
    ]
    assert lint_table_shape(tables) == []


def test_honors_table_totals_sums_across_multiple_tables():
    """#816: the doctor's `metrics` block sums the SAME per-row predicate
    the finding is built from, over every honors-shaped table, ignoring
    non-honors ones entirely."""
    tables = [
        [["Committee", "Role"], [_BLOB, "Chair"]],  # not honors-shaped
        [_HONORS_HEADER,
         [_BLOB, "MD", ""],  # malformed
         ["Distinguished Teaching Award", "Indiana University", "2013"]],
        [_HONORS_HEADER,
         ["Another Award", "Cornell University", "2015"]],  # clean
    ]
    assert honors_table_totals(tables) == (1, 3)


def test_honors_table_totals_zero_with_no_honors_tables():
    assert honors_table_totals([[["Committee", "Role"], [_BLOB, "Chair"]]]) == (0, 0)


# ----------------------------------------------- lint 14: duplicate passages

_NY_RECORD = [
    ("p", "• New York Point-of-Care Ultrasound Course"),
    ("p", "• 2 day introductory POCUS workshop"),
    ("p", "• Lecture: Cardiac Image Review"),
    ("p", "• POCUS for vascular access and lumbar puncture"),
    ("p", "• Instructor: image acquisition: New York University — 10/23/2024"),
]


def test_duplicate_passages_flags_a_repeated_block_run():
    """A record emitted twice repeats several CONSECUTIVE blocks (C0ZGFW
    blocks 361-365 == 486-490). The 5-block duplicate counts ONCE, not once
    per overlapping 3-block window inside it."""
    blocks = ([("p", "K. EDUCATIONAL CONTRIBUTIONS")] + _NY_RECORD
              + [("p", "• Grand Rounds, Weill Cornell Medicine — 3/4/2019")]
              + _NY_RECORD)
    findings = lint_duplicate_passages(blocks)
    assert len(findings) == 1
    f = findings[0]
    assert f["lint"] == "duplicate_passages" and f["severity"] == "WARN"
    assert "1 passage(s)" in f["message"]
    assert f["evidence"][0].startswith("blocks 1-5 repeat at 7-11")

    # three occurrences are two redundant copies, so the count is 2 -- nested
    # and overlapping matches are never charged twice.
    thrice = lint_duplicate_passages(
        blocks + [("p", "• Journal Club, 2001")] + _NY_RECORD)
    assert "2 passage(s)" in thrice[0]["message"]


def test_duplicate_passages_cross_stretch_scan_distance_not_double_counted():
    """`distances` is built globally from every repeated window in the
    document (#446 review, fb73705 rework): stretch A repeats 3 times, 6
    blocks apart, earning scan distance 6. Separately, stretch B repeats
    once, 12 blocks apart, earning scan distance 12. Because A's first and
    third occurrences also happen to sit 12 blocks apart, the distance-12
    scan re-pairs them -- a match already charged via the two distance-6
    pairs. The fix must count A's 3x repeat as 2 (not 3) and B's 1x repeat
    as 1, for 3 total, and no evidence line's 'repeat at' block range may
    appear twice."""
    stretch_a = [("p", "• AAA Alpha item — 2001"),
                 ("p", "• AAA Beta item — 2001"),
                 ("p", "• AAA Gamma item — 2001")]
    filler_1 = [("p", "• Filler One Uno"), ("p", "• Filler One Dos"),
                ("p", "• Filler One Tres")]
    filler_2 = [("p", "• Filler Two Uno"), ("p", "• Filler Two Dos"),
                ("p", "• Filler Two Tres")]
    filler_3 = [("p", "• Filler Three Uno"), ("p", "• Filler Three Dos"),
                ("p", "• Filler Three Tres")]
    filler_4 = [("p", f"• Filler Four {n}") for n in range(1, 10)]
    stretch_b = [("p", "• BBB Uno item — 2002"),
                 ("p", "• BBB Dos item — 2002"),
                 ("p", "• BBB Tres item — 2002")]

    blocks = (stretch_a + filler_1 + stretch_a + filler_2 + stretch_a
              + filler_3 + stretch_b + filler_4 + stretch_b)
    findings = lint_duplicate_passages(blocks)
    assert len(findings) == 1
    assert "3 passage(s)" in findings[0]["message"]
    repeat_ranges = [e.split("repeat at ")[1].split(":")[0]
                      for e in findings[0]["evidence"]]
    assert len(repeat_ranges) == len(set(repeat_ranges))


def test_duplicate_passages_quiet_when_only_the_adjacent_date_differs():
    """The mode both earlier attempts fired on: one course taught at nine
    venues renders nine records whose first three bullets are IDENTICAL and
    whose distinguishing date sits in the ADJACENT block. Nine copies of a
    three-block run, zero duplicated records."""
    venues = [("Denver, Colorado", "6/14/2019"), ("Philadelphia, PA", "4/9/2019"),
              ("Baltimore, MD", "3/23/2019"), ("New Orleans, LA", "4/19/2018"),
              ("Orlando, FL", "4/5/2018"), ("Chicago, IL", "11/7/2019"),
              ("San Diego, CA", "4/11/2017"), ("Austin, TX", "1/18/2016"),
              ("Seattle, WA", "10/5/2015")]
    blocks = [("p", "K. EDUCATIONAL CONTRIBUTIONS")]
    for city, date in venues:
        blocks += [("p", "• ACP National Annual Meeting"),
                   ("p", "• Pre-Course on Point-of-Care Ultrasound"),
                   ("p", "• 2-day CME POCUS workshop"),
                   ("p", f"• Instructor – hands on teaching: {city} — {date}")]
    assert lint_duplicate_passages(blocks) == []

    # and round 1's mode: a single block repeated verbatim WITH its year, its
    # neighbours different on both sides, is a repeated FIELD not a record.
    blocks += [("p", "• Accredited by the ACCME, 2019"),
               ("p", "• SHM National Annual Meeting"),
               ("p", "• Accredited by the ACCME, 2019"),
               ("p", "• Kidney Week")]
    assert lint_duplicate_passages(blocks) == []


def test_duplicate_passages_see_through_renumbering_and_separator_drift():
    """5c/5d renumber lists and stage 6 varies its own field separator, so the
    second copy of a duplicated record is rarely byte-identical — C0ZGFW
    blocks 282-284 == 643-645 differ only in ' — ' vs ': '."""
    record = ["Global Ultrasound Institute POCUS Workshop",
              "2-day introductory POCUS course for Family Practice residents"]
    blocks = ([("p", f"{n}. {line}") for n, line in enumerate(record, start=9)]
              + [("p", "11. Instructor – image acquisition — Morehead, "
                       "Kentucky — 12/7/2022")]
              + [("p", "• Tutor Group, Weill Cornell Medicine, NY — 2018")]
              + [("p", f"{n}. {line}") for n, line in enumerate(record, start=21)]
              + [("p", "23. Instructor – image acquisition: Morehead, "
                       "Kentucky — 12/7/2022")])
    findings = lint_duplicate_passages(blocks)
    assert len(findings) == 1
    assert "1 passage(s)" in findings[0]["message"]
    assert findings[0]["evidence"][0].startswith("blocks 0-2 repeat at 4-6")


def test_duplicate_passages_threshold_is_pinned_at_two_blocks():
    """The corpus's only true positive is a 2-block duplicate (web119: the same
    abstract at two adjacent numbers), so raising the threshold to 3 silently
    discards it. Nothing else pinned the value -- every other test here passes
    at both 2 and 3."""
    assert DUPLICATE_PASSAGE_MIN_BLOCKS == 2
    pair = [("p", "217. Rothwell GW. Anatomically preserved cycadeoid cones, 1993."),
            ("p", "Botanical Gazette 154(3): 512-525.")]
    other = [("p", "• Kidney Week, Chicago — 2016")]
    assert "1 passage(s)" in lint_duplicate_passages(
        pair + other + pair)[0]["message"]
    # ...but one block alone is a repeated field, not a record, at any threshold
    assert lint_duplicate_passages(pair[:1] + other + pair[:1]) == []


def test_read_docx_table_rows_keeps_empty_cells(tmp_path):
    doc = Document()
    table = doc.add_table(rows=2, cols=3)
    for i, text in enumerate(_HONORS_HEADER):
        table.rows[0].cells[i].text = text
    table.rows[1].cells[0].text = "Award X, Some University"
    path = tmp_path / "t.docx"
    doc.save(path)
    rows = read_docx_table_rows(str(path))
    assert rows == [[_HONORS_HEADER, ["Award X, Some University", "", ""]]]


# --------------------------------------- lint 4 addendum: year-edge records

def test_under_extraction_counts_year_edge_lines_as_records():
    # 2Q1_ZQ honors entry shape: plain newline award lines, no pipes/tabs —
    # invisible to _looks_like_record before #229.
    lines = [
        "2020 AECT ST&C Outstanding Article Award, Association for "
        "Educational Communication and Technology (AECT)",
        "2015-2017 Featured Research Article Award, AECT ST&C Division",
        "Basic Science Innovation Award. USU Education Day, August 2025.",
    ]
    entry = _entry("\n".join(lines) + "\nfiller " * 100, taxonomy_code="H",
                   extracted_fields={"award_name": lines[0]})
    entry["extraction_coverage"] = {"extraction_coverage_percent": 19.0}
    findings = lint_under_extraction({"entries": [entry]})
    assert len(findings) == 1
    assert "3 record-like lines" in findings[0]["message"]


# ------------------------------------------- lint 14: owner contact (hard fail)

# web139's shape: stage 4 fell back to the file stem for last_name and left
# every other name field empty. Score 25, RED — but the doctor said worst=WARN.
_OWNER_EMPTY = {"first_name": "", "middle_name": "", "last_name": "web139",
                "suffix": "", "full_name": "", "full_name_with_credentials": ""}


def test_owner_contact_missing_errors_and_names_the_gate():
    findings = lint_owner_contact_missing({"cv_owner": _OWNER_EMPTY}, "web139")
    assert len(findings) == 1
    finding = findings[0]
    assert finding["lint"] == "owner_contact_missing"
    assert finding["severity"] == "ERROR"
    assert "CV owner name / contact populated" in finding["message"]
    assert "25" in finding["message"]
    assert finding["evidence"][0] == "cv_owner name fields populated: last_name"
    assert "last_name is the document uid" in finding["evidence"][1]


def test_owner_contact_missing_evidence_never_prints_a_name():
    """A partly-extracted owner trips the gate (the LLM found a surname but no
    given name and no full_name), and the report is mirrored to S3 and served
    by the admin viewer — so the evidence names fields, never values."""
    findings = lint_owner_contact_missing(
        {"cv_owner": {"first_name": "", "last_name": "Shapiro",
                      "full_name": ""}}, "89TEST")
    assert len(findings) == 1
    blob = " ".join([findings[0]["message"], *findings[0]["evidence"]])
    assert "Shapiro" not in blob
    assert "cv_owner name fields populated: last_name" in blob


def test_owner_contact_missing_errors_when_the_artifact_is_absent_or_broken():
    """score_cv_owner caps at 25 for an absent *_fields.json too, so the lint
    must not go quiet there — a run with no stage-4 output is the most
    undeliverable run there is (#437)."""
    absent = lint_owner_contact_missing(None, "89TEST")
    assert [f["severity"] for f in absent] == ["ERROR"]
    assert "no stage-4 *_fields.json" in absent[0]["message"]
    assert "25" in absent[0]["message"]

    broken = lint_owner_contact_missing(None, "89TEST", "JSONDecodeError: x")
    assert [f["severity"] for f in broken] == ["ERROR"]
    assert "will not parse (JSONDecodeError: x)" in broken[0]["message"]


def test_owner_contact_missing_quiet_on_a_populated_owner():
    # either a full_name, or first AND last, is enough
    assert lint_owner_contact_missing(
        {"cv_owner": {"full_name": "Miriam Shapiro"}}, "89TEST") == []
    assert lint_owner_contact_missing(
        {"cv_owner": {"first_name": "Miriam", "last_name": "Shapiro",
                      "full_name": ""}}, "89TEST") == []


@pytest.mark.parametrize("cv_owner", [
    _OWNER_EMPTY,
    {"full_name": "   "},                       # whitespace is not a name
    {"first_name": "Miriam", "last_name": ""},  # half a name is not a name
    {},
    {"full_name": "Miriam Shapiro"},
    {"first_name": "Miriam", "last_name": "Shapiro"},
])
def test_owner_lint_and_scorer_read_the_same_stage4_artifact(tmp_path, cv_owner):
    """LOADER drift guard, and nothing more. Both sides call the same predicate
    (quality_score.cv_owner_name_missing), so this cannot tell you the predicate
    is RIGHT — it stays green if the predicate is replaced with `lambda d: True`
    or `lambda d: False`. What it does pin is that the doctor's stage-4 artifact
    and the scorer's flat-dir `*_fields.json` resolve to the same document, so a
    change to either loader shows up as a disagreement. The evidence that the
    predicate is calibrated is in the issue: on the live corpus, exactly the CVs
    it flags are the ones whose rendered deliverable carries the uid in the
    owner-name field."""
    (tmp_path / "run_fields.json").write_text(
        json.dumps({"cv_owner": cv_owner, "entries": []}))
    _fraction, _detail, cap = score_cv_owner(tmp_path)
    fired = bool(lint_owner_contact_missing({"cv_owner": cv_owner}, "web139"))
    assert fired == (cap == 25), f"{cv_owner!r}: lint={fired} cap={cap}"


# --------------------------------------- lint 15: pipeline errors (hard fail)

def test_pipeline_errors_errors_on_a_fatal_pattern():
    findings = lint_pipeline_errors({"stage_3b": {"entries": [
        {"error": "NameError: name 'response' is not defined"}]}})
    assert len(findings) == 1
    finding = findings[0]
    assert finding["lint"] == "pipeline_errors_present"
    assert finding["severity"] == "ERROR"
    assert "Pipeline/API errors present" in finding["message"]
    assert "40" in finding["message"]
    assert finding["evidence"][0].startswith("stage_3b.entries[0].error:")


def test_pipeline_errors_quiet_on_benign_and_absent_error_fields():
    # A recorded-but-recoverable failure (a PubMed lookup that 429'd) is a
    # stage-5 WARN via enrichment_failures, not a do-not-deliver hard fail.
    assert lint_pipeline_errors({"stage_2": {"entries": [
        {"error": "HTTP 429 rate limited", "enrichment_status": "lookup_failed"}]}}) == []
    assert lint_pipeline_errors({"stage_4": {"entries": [{"error": None}]}}) == []
    assert lint_pipeline_errors({}) == []


# ------------------------------ #810: stage3b_fallback_ratio (hard fail) ----
#
# Synthesized from the outage's own log line (~/worktrees/batch-slices/s4/
# _batch_runs/logs/web30.log): "Stage 3b (web30): 41 of 83 classification
# batches failed; 510 entries fell back to default codes" against a total of
# 1019 entries_classified (batch-3 artifact). The clean re-run's own values
# (0 failed of 80 batches, 0 fallback of 1037 entries) are the negative case.

def _stage3b_stats(**stats):
    return {"meta": {"stats": stats}}


def test_stage3b_fallback_ratio_errors_on_the_web30_outage_numbers():
    stage3b = _stage3b_stats(failed_batches=41, llm_batches=83,
                             fallback_entries=510, entries_classified=1019)
    findings = lint_stage3b_fallback_ratio(stage3b)
    assert len(findings) == 1
    finding = findings[0]
    assert finding["lint"] == "stage3b_fallback_ratio"
    assert finding["severity"] == "ERROR"
    assert "stage-3b fallback ratio" in finding["message"]
    assert "40" in finding["message"]  # same cap as pipeline_errors_present


def test_stage3b_fallback_ratio_quiet_on_the_clean_rerun_numbers():
    stage3b = _stage3b_stats(failed_batches=0, llm_batches=80,
                             fallback_entries=0, entries_classified=1037)
    assert lint_stage3b_fallback_ratio(stage3b) == []


def test_stage3b_fallback_ratio_quiet_on_missing_keys_not_a_crash():
    """An artifact from before c6402bf added these counters must not crash
    or false-positive -- (False, None), same convention as every other
    missing-evidence case in this module."""
    assert lint_stage3b_fallback_ratio({}) == []
    assert lint_stage3b_fallback_ratio({"meta": {}}) == []
    assert lint_stage3b_fallback_ratio(
        _stage3b_stats(failed_batches=5)) == []  # llm_batches absent


def test_stage3b_fallback_ratio_boundary_at_the_threshold():
    from unified_pipeline.quality_score import STAGE3B_FALLBACK_RATIO_THRESHOLD

    at_threshold = _stage3b_stats(
        failed_batches=int(STAGE3B_FALLBACK_RATIO_THRESHOLD * 100),
        llm_batches=100, fallback_entries=0, entries_classified=1)
    assert lint_stage3b_fallback_ratio(at_threshold) == [], \
        "exactly at the threshold must not exceed it"

    just_over = _stage3b_stats(
        failed_batches=int(STAGE3B_FALLBACK_RATIO_THRESHOLD * 100) + 1,
        llm_batches=100, fallback_entries=0, entries_classified=1)
    assert len(lint_stage3b_fallback_ratio(just_over)) == 1


# ------------------------- #1174: stage4_group_failures (cap below GREEN) ----
#
# Synthetic stand-ins for the shapes the batch autopsy found: a group the
# recovery pass rescued in full (extraction_failed reads 0, so the stats
# alone hide it) and a group left with one entry still unextracted.

def _failed_group_entry(code, rescued, error="llm_response_invalid"):
    return {"taxonomy_code": code, "extraction_error": error,
            "extraction_success": rescued, "llm_recovery_applied": rescued,
            "extracted_fields": {"note": "x"} if rescued else {}}


def test_stage4_group_failures_warns_on_a_fully_rescued_group_and_names_its_code():
    stage4 = {"entries": [_failed_group_entry("P", True) for _ in range(8)],
              "stats": {"failed_batches": 1, "extraction_failed": 0}}
    findings = lint_stage4_group_failures(stage4)
    assert len(findings) == 1
    finding = findings[0]
    assert finding["lint"] == "stage4_group_failures"
    assert finding["severity"] == "WARN"
    assert "8 entries lost their first extraction" in finding["message"]
    assert "rescued 8 and left 0" in finding["message"]
    assert "capped at 84" in finding["message"]
    assert finding["evidence"] == [
        "taxonomy codes (entries): P=8",
        "errors (entries): llm_response_invalid=8",
        "stats.failed_batches=1",
    ]


def test_stage4_group_failures_reports_the_entries_left_without_fields():
    stage4 = {"entries": [_failed_group_entry("M2A", True) for _ in range(3)]
              + [_failed_group_entry("M2A", False)],
              "stats": {"failed_batches": 1, "extraction_failed": 1}}
    message = lint_stage4_group_failures(stage4)[0]["message"]
    assert "4 entries lost" in message and "rescued 3 and left 1" in message


def test_stage4_group_failures_quiet_on_a_clean_or_older_artifact():
    clean = {"entries": [{"taxonomy_code": "P", "extraction_success": True,
                          "extracted_fields": {"note": "x"}}],
             "stats": {"failed_batches": 0}}
    assert lint_stage4_group_failures(clean) == []
    # an artifact from before stage 4 recorded failed_batches / extraction_error
    assert lint_stage4_group_failures({"entries": [{"taxonomy_code": "P"}]}) == []
    assert lint_stage4_group_failures({}) == []


def test_stage4_group_failures_quiet_on_a_per_entry_miss():
    from unified_pipeline.stage4.error_codes import NO_MATCHING_EXTRACTION

    miss = {"taxonomy_code": "A1", "extraction_success": False, "extracted_fields": {},
            "extraction_error": NO_MATCHING_EXTRACTION}
    assert lint_stage4_group_failures({"entries": [miss], "stats": {"failed_batches": 0}}) == []


def test_stage4_group_failures_and_the_score_gate_agree_on_the_same_artifact(tmp_path):
    """Both read quality_score.stage4_group_failures, so the lint fires exactly
    when the cap does -- the doctor reports the gate, not a second definition."""
    from unified_pipeline.quality_score import (
        STAGE4_GROUP_FAILURE_CAP,
        score_stage4_group_failures,
    )

    failed = {"entries": [_failed_group_entry("P", True)], "stats": {"failed_batches": 1}}
    clean = {"entries": [{"taxonomy_code": "P", "extraction_success": True}]}
    for artifact, expect_flag in ((failed, True), (clean, False)):
        (tmp_path / "X_fields.json").write_text(json.dumps(artifact))
        _fraction, _detail, cap = score_stage4_group_failures(tmp_path)
        assert (cap == STAGE4_GROUP_FAILURE_CAP) is expect_flag
        assert bool(lint_stage4_group_failures(artifact)) is expect_flag


# --------------------- #1243: stage4_unplaced_items (WARN) -------------------
#
# Stage 4 stamps every entry of a 2+-entry group whose reply numbered an item
# past the group's entries (#1417); synthetic entries carry that stamp.

def _stamped_entry(code, start, unplaced):
    from unified_pipeline.stage4.schemas import STAGE4_UNPLACED_ITEMS_KEY

    return {"taxonomy_code": code, "element_idx_start": start, "extraction_success": True,
            "extracted_fields": {"note": "x"}, STAGE4_UNPLACED_ITEMS_KEY: unplaced}


def test_stage4_unplaced_items_warns_once_and_names_each_code_and_its_entries():
    stage4 = {"entries": [_stamped_entry("Q1", 10, 2), _stamped_entry("Q1", 14, 2),
                          _stamped_entry("O", 30, 1), _stamped_entry("O", 31, 1),
                          {"taxonomy_code": "P", "element_idx_start": 40}]}
    (finding,) = lint_stage4_unplaced_items(stage4)
    assert finding["lint"] == "stage4_unplaced_items"
    assert finding["severity"] == "WARN"
    assert "left 3 reply item(s) out of 2 taxonomy group(s)" in finding["message"]
    assert finding["evidence"] == ["O: 1 item(s) left out; entries 30, 31",
                                   "Q1: 2 item(s) left out; entries 10, 14"]


def test_stage4_unplaced_items_quiet_on_a_clean_or_older_artifact():
    assert lint_stage4_unplaced_items({"entries": [{"taxonomy_code": "P"}]}) == []
    assert lint_stage4_unplaced_items({"entries": [_stamped_entry("P", 1, 0)]}) == []
    assert lint_stage4_unplaced_items({}) == []


def test_stage4_unplaced_items_runs_from_the_registry(tmp_path):
    stage_dir = tmp_path / "stage_4_field_extraction"
    stage_dir.mkdir()
    (stage_dir / "ABCDEF_fields.json").write_text(json.dumps(
        {"entries": [_stamped_entry("Q1", 10, 2), _stamped_entry("Q1", 14, 2)]}))
    report = run_doctor(tmp_path, "ABCDEF")
    assert [f["lint"] for f in report["findings"]].count("stage4_unplaced_items") == 1


# --------------------- #1174: llm_fallback_served (WARN, caps nothing) -------
#
# Synthetic provenance: stage 4 stamps the entries of a taxonomy group the
# content-filter fallback answered; stage 4.5 lists the calls it served.

_FALLBACK_MODEL = "example.fallback-model-1"


def _fallback_entry(code, model=_FALLBACK_MODEL):
    return {"taxonomy_code": code, "extraction_success": True,
            "extracted_fields": {"note": "x"}, "llm_fallback_model": model}


def test_llm_fallback_served_warns_per_stage_4_section_and_names_the_model():
    stage4 = {"entries": [_fallback_entry("S1"), _fallback_entry("S1"),
                          _fallback_entry("M2A"),
                          {"taxonomy_code": "S1", "extraction_success": True}]}
    findings = lint_llm_fallback_served(stage4)
    assert [f["lint"] for f in findings] == ["llm_fallback_served"] * 2
    assert {f["severity"] for f in findings} == {"WARN"}
    by_section = {f["evidence"][0]: f["message"] for f in findings}
    assert set(by_section) == {
        f"stage 4 M2A on {_FALLBACK_MODEL} (1 entries)",
        f"stage 4 S1 on {_FALLBACK_MODEL} (2 entries)"}
    assert all(f["message"].endswith("the call succeeded, so the quality score is not capped")
               for f in findings)


def test_llm_fallback_served_reports_a_stage_4_5_call_as_the_research_summary():
    stage4_5 = {"research_summary": {"text": "x"}, "llm_fallback_calls": [
        {"call": "m1_relevance_score", "model": _FALLBACK_MODEL}]}
    findings = lint_llm_fallback_served({"entries": []}, stage4_5)
    assert len(findings) == 1
    assert findings[0]["message"].startswith(
        "stage 4.5 research summary (m1_relevance_score): the content filter blocked")
    assert findings[0]["severity"] == "WARN"


def test_llm_fallback_served_quiet_without_provenance_or_on_a_malformed_artifact():
    assert lint_llm_fallback_served({"entries": [{"taxonomy_code": "S1"}]}) == []
    assert lint_llm_fallback_served({}, {"research_summary": {}}) == []
    assert lint_llm_fallback_served({"entries": [None, 3, "x"]}, None) == []
    assert lint_llm_fallback_served({}, {"llm_fallback_calls": [None, {"call": "x"}]}) == []


def test_run_doctor_wires_llm_fallback_served_through_to_the_verdict(tmp_path):
    """Both artifacts, driven through run_doctor() end to end: deleting the
    LINT_REGISTRY row or its optional stage_4_5 view fails this."""
    root = _build_clean_run(tmp_path)
    assert not [f for f in run_doctor(root, _UID)["findings"]
                if f["lint"] == "llm_fallback_served"]
    fields = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    data = json.loads(fields.read_text())
    data["entries"][0]["llm_fallback_model"] = _FALLBACK_MODEL
    fields.write_text(json.dumps(data))
    summary = root / "stage_4_5_research_summary" / f"{_UID}_cv_research_summary.json"
    summary.write_text(json.dumps({"research_summary": {"text": "x"}, "llm_fallback_calls": [
        {"call": "summary_generation", "model": _FALLBACK_MODEL}]}))

    payload = run_doctor(root, _UID)

    found = [f for f in payload["findings"] if f["lint"] == "llm_fallback_served"]
    assert len(found) == 2
    assert {f["severity"] for f in found} == {"WARN"}
    assert any("stage 4.5 research summary (summary_generation)" in f["message"] for f in found)
    assert payload["counts"]["ERROR"] == 0


def test_run_doctor_reports_a_fallback_served_call_from_the_prompt_logs(tmp_path):
    """#1174 residual: a stage no artifact stamps (here 5d) is found through
    the run's prompt logs, one finding per stage; without prompt_log_dir the
    doctor is unchanged."""
    root = _build_clean_run(tmp_path / "run")
    logs = tmp_path / "logs"
    logs.mkdir()
    for name in ("a", "b"):
        (logs / f"{name}_RESPONSE.json").write_text(json.dumps({"purpose": "stage_5d", "response": {
            "model": _FALLBACK_MODEL, "served_by_fallback_model": _FALLBACK_MODEL}}))

    assert not [f for f in run_doctor(root, _UID)["findings"] if f["lint"] == "llm_fallback_served"]
    found = [f for f in run_doctor(root, _UID, prompt_log_dir=logs)["findings"]
             if f["lint"] == "llm_fallback_served"]
    assert [(f["severity"], f["evidence"]) for f in found] == [
        ("WARN", [f"stage 5d calls on {_FALLBACK_MODEL} (2 calls)"])]


# ----------------- #1174: stage_failure_recorded (a recorded stage failure) --

def test_stage_failure_recorded_errors_on_a_fatal_stage_4_5_and_says_what_is_missing():
    record = StageError("4.5", "BedrockContentFilteredError", "ended content_filtered", True)
    findings = lint_stage_failure_recorded([record])
    assert len(findings) == 1
    finding = findings[0]
    assert finding["lint"] == "stage_failure_recorded"
    assert finding["severity"] == "ERROR"
    assert "stage 4.5 failed (BedrockContentFilteredError" in finding["message"]
    assert "not because the CV has no research content" in finding["message"]
    assert finding["evidence"] == [
        "stage=4.5", "exception=BedrockContentFilteredError", "fatal=True"]


def test_stage_failure_recorded_names_any_other_stage_and_grades_a_non_fatal_record_warn():
    fatal, non_fatal = (StageError("5", "ValueError", "bad", True),
                        StageError("6", "OSError", "disk", False))
    first, second = lint_stage_failure_recorded([fatal, non_fatal])
    assert (first["severity"], second["severity"]) == ("ERROR", "WARN")
    assert "stage 5 failed (ValueError: bad)" in first["message"]
    assert "RED" in first["message"] and "RED" not in second["message"]


def test_stage_failure_recorded_quiet_on_a_clean_run():
    assert lint_stage_failure_recorded([]) == []


def test_stage_failure_recorded_names_research_activities_for_a_non_fatal_stage_4_5():
    """The web driver's record when stage 4.5 raised and the run carried on (#1174)."""
    (finding,) = lint_stage_failure_recorded([StageError("4.5", "RuntimeError", "boom", False)])
    assert finding["severity"] == "WARN"
    assert "the Research Activities section has no research summary" in finding["message"]
    assert "RED" not in finding["message"]


# ----------- #1174: research_summary_call_failed (a call stage 4.5 survived) --

def test_research_summary_call_failed_warns_per_call_and_names_the_section():
    stage4_5 = {"research_summary": {"text": ""}, "llm_call_failures": [
        {"call": "m1_relevance_score", "exception_type": "BedrockContentFilteredError",
         "stop_reason": "content_filtered", "message": "filtered"},
        {"call": "summary_generation", "exception_type": "ClientError",
         "stop_reason": None, "message": "denied"}]}

    score, generation = lint_research_summary_call_failed(stage4_5)

    assert {score["lint"], generation["lint"]} == {"research_summary_call_failed"}
    assert {score["severity"], generation["severity"]} == {"WARN"}
    assert "Research Activities text was not scored" in score["message"]
    assert "stop_reason='content_filtered'" in score["message"]
    assert "the Research Activities section has no research summary" in generation["message"]
    assert generation["evidence"] == [
        "call=summary_generation", "exception=ClientError", "stop_reason=None"]


def test_research_summary_call_failed_quiet_without_the_record_or_on_a_malformed_one():
    assert lint_research_summary_call_failed({"research_summary": {"text": "x"}}) == []
    assert lint_research_summary_call_failed({"llm_call_failures": "not a list"}) == []
    assert lint_research_summary_call_failed({"llm_call_failures": [None, 3]}) == []
    assert lint_research_summary_call_failed(None) == []


def test_run_doctor_wires_research_summary_call_failed_through_to_the_verdict(tmp_path):
    """Deleting the LINT_REGISTRY row fails this."""
    root = _build_clean_run(tmp_path)
    assert not [f for f in run_doctor(root, _UID)["findings"]
                if f["lint"] == "research_summary_call_failed"]
    summary = root / "stage_4_5_research_summary" / f"{_UID}_cv_research_summary.json"
    summary.write_text(json.dumps({"research_summary": {"text": ""}, "llm_call_failures": [
        {"call": "summary_generation", "exception_type": "BedrockContentFilteredError",
         "stop_reason": "content_filtered", "message": "filtered"}]}))

    found = [f for f in run_doctor(root, _UID)["findings"]
             if f["lint"] == "research_summary_call_failed"]

    assert len(found) == 1 and found[0]["severity"] == "WARN"


def test_run_doctor_wires_stage_failure_recorded_through_to_the_verdict(tmp_path):
    """The record the drivers write (#745), read from its real location under
    the outputs root. A malformed record is the usual unreadable ERROR, not a
    silent pass."""
    root = _build_clean_run(tmp_path)
    assert not [f for f in run_doctor(root, _UID)["findings"]
                if f["lint"] == "stage_failure_recorded"]
    record_stage_outcome(stage_errors_path(root, _UID), "4.5",
                         StageError.from_exception("4.5", RuntimeError("boom")))

    payload = run_doctor(root, _UID)

    found = [f for f in payload["findings"] if f["lint"] == "stage_failure_recorded"]
    assert len(found) == 1 and found[0]["severity"] == "ERROR"
    assert payload["worst_severity"] == "ERROR"

    stage_errors_path(root, _UID).write_text("{not json")
    broken = [f for f in run_doctor(root, _UID)["findings"]
              if f["lint"] == "stage_failure_recorded"]
    assert len(broken) == 1 and broken[0]["severity"] == "ERROR"
    assert broken[0]["message"].startswith("skipped: unreadable stage_errors")


# ------------------------------------------------ #745: no_output (hard fail) -----

def test_no_output_errors_when_stage4_reached_but_nothing_rendered():
    findings = lint_no_output(True, False, False)
    assert len(findings) == 1
    finding = findings[0]
    assert finding["lint"] == "no_output"
    assert finding["severity"] == "ERROR"
    assert "No output produced" in finding["message"]
    assert "20" in finding["message"]


def test_no_output_quiet_when_either_artifact_exists():
    assert lint_no_output(True, True, False) == []
    assert lint_no_output(True, False, True) == []
    assert lint_no_output(True, True, True) == []


def test_no_output_quiet_when_stage4_never_ran():
    """An incomplete run that never reached stage 4 has no output YET -- not
    the same failure as one that ran the whole pipeline and produced
    nothing, the same distinction owner_contact_missing already draws via
    _DELIVERABLE."""
    assert lint_no_output(False, False, False) == []


# ------------------------------------------------------------ full doctor runs

_UID = "89TEST"

# A real stage-4 artifact carries the cv_owner block the score's hard-fail gate
# reads; a fixture without one is an undeliverable run, not a clean one.
_OWNER = {"first_name": "Miriam", "middle_name": "", "last_name": "Shapiro",
          "suffix": "", "full_name": "Miriam Shapiro",
          "full_name_with_credentials": "Miriam Shapiro, M.D."}


def _write_stage(root, stage_dir, filename, payload):
    directory = root / stage_dir
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(json.dumps(payload))


def _build_clean_run(tmp_path, uid=_UID):
    root = tmp_path / "outputs"
    grants = [_GRANT_FSMB, _GRANT_TEMPLETON, _GRANT_NBME]

    source = Document()
    source.add_paragraph("GRANTS").runs[0].bold = True
    for grant in grants:
        source.add_paragraph(grant)
    uploads = root / "uploads"
    uploads.mkdir(parents=True)
    source.save(uploads / f"{uid}_cv.docx")

    _write_stage(root, "stage_1a_segmentation", f"{uid}_cv_segmented.json",
                 {"document_uid": uid, **_STAGE1A})
    _write_stage(root, "stage_1b_hierarchy_mapping", f"{uid}_cv_hierarchy_mapped.json",
                 {"document_uid": uid,
                  "hierarchy_with_indices": [{"text": "GRANTS", "level": "H1",
                                              "element_idx": 0, "synthetic": False}],
                  "section_boundaries": [{"hierarchy": ["GRANTS"], "element_idx_start": 0,
                                          "element_idx_end": len(grants)}]})
    stage2_entries = [_entry("GRANTS", etype="header", start=0)] + [
        _entry(grant, start=i + 1) for i, grant in enumerate(grants)]
    _write_stage(root, "stage_2_entry_extraction", f"{uid}_cv_entries.json",
                 {"document_uid": uid, "entries": stage2_entries})
    codes = ["M2A", "M2C", "M2C"]
    _write_stage(root, "stage_3b_classified_entries", f"{uid}_cv_classified.json",
                 {"document_uid": uid, "entries": [
                     _entry(g, start=i + 1, taxonomy_code=c)
                     for i, (g, c) in enumerate(zip(grants, codes))]})
    _write_stage(root, "stage_4_field_extraction", f"{uid}_cv_fields.json",
                 {"document_uid": uid, "cv_owner": _OWNER, "entries": [
                     _grant4(g, c, start=i + 1)
                     for i, (g, c) in enumerate(zip(grants, codes))]})
    _write_stage(root, "stage_5_enrichment", f"{uid}_cv_enriched.json",
                 {"document_uid": uid, "entries": [
                     _enriched_entry("Sample citation resolved in PubMed",
                                     "enriched"),
                     _enriched_entry("Sample citation without identifiers",
                                     "no_identifier")]})
    _write_stage(root, "stage_5b_institution_enrichment",
                 f"{uid}_cv_institution_enriched.json",
                 {"document_uid": uid, "entries": []})
    _write_stage(root, "stage_5d_citation_formatted",
                 f"{uid}_cv_citation_formatted.json",
                 {"document_uid": uid, "entries": []})
    _write_stage(root, "stage_4_5_research_summary", f"{uid}_cv_research_summary.json",
                 {"document_uid": uid, "research_summary": {"text": "x"}})

    output = Document()
    # Real renders carry grants under RESEARCH (section_lost reads the
    # section by that heading); "D. GRANTS" is then a sub-heading.
    output.add_paragraph("RESEARCH")
    output.add_paragraph("D. GRANTS")
    output.add_paragraph("Current Research Funding")
    table = output.add_table(rows=1, cols=1)
    table.rows[0].cells[0].paragraphs[0].text = grants[0]
    output.add_paragraph("Pending Funding")
    table = output.add_table(rows=2, cols=1)
    for i, grant in enumerate(grants[1:]):
        table.rows[i].cells[0].paragraphs[0].text = grant
    output.add_paragraph("T. APPENDIX")
    output.add_paragraph("These entries from your original CV could not be matched to a section of the WCM format.")
    out_dir = root / "stage_6_wcm_documents"
    out_dir.mkdir(parents=True)
    output.save(out_dir / f"{uid}_cv_wcm.docx")
    _write_stage(root, "stage_6_wcm_documents", f"{uid}_cv_render_warnings.json",
                 {"document_uid": uid, "warnings": [], "dedup_decisions": []})
    return root


def test_run_doctor_tolerates_missing_artifacts(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    payload = run_doctor(root, "NOPE")
    # One skip per lint in KNOWN_LINTS (60), except no_output: it never even
    # reached stage 4, so its "has_stage4 and not has_docx..." condition is
    # False and it emits NOTHING, not a skip -- it is dispatched by hand
    # (booleans, not `_ready()`-checked content) precisely so an incomplete
    # run like this one is silent rather than reported as "no output" (#745).
    # stage_failure_recorded skips nothing either: no stage-error record is
    # the normal clean case, read as an empty list (#1174).
    assert len(payload["findings"]) == 58
    assert all(f["lint"] != "no_output" for f in payload["findings"])
    assert all(f["severity"] == "INFO" and "skipped" in f["message"]
               for f in payload["findings"])
    assert payload["counts"]["ERROR"] == 0
    assert payload["counts"]["WARN"] == 0
    assert all(v is None for v in payload["artifacts"].values())
    assert payload["metrics"] == {}


def test_run_doctor_clean_run_end_to_end(tmp_path):
    root = _build_clean_run(tmp_path)
    payload = run_doctor(root, _UID)
    assert set(payload) == {"document_uid", "root", "artifacts", "findings",
                            "counts", "worst_severity", "metrics", "lint_precision"}
    # #819: one lint_precision entry per lint key that fired, no more.
    assert set(payload["lint_precision"]) == {f["lint"] for f in payload["findings"]}
    assert all(v is not None for v in payload["artifacts"].values())
    assert not any("skipped" in f["message"] for f in payload["findings"])
    assert payload["counts"]["ERROR"] == 0
    assert payload["counts"]["WARN"] == 0
    assert payload["worst_severity"] == "INFO"
    # #816: appendix_entries/appendix_share are present even at 0/0.0 -- an
    # EMPTY appendix is still a measured appendix, distinct from the "no
    # appendix at all" None that omits the key entirely (see the dedicated
    # metrics tests below). source_coverage_pct is populated because
    # source/stage_1a/stage_2 are all present; no honors table, no unrouted
    # code and no meta.stats in this fixture, so those four keys are absent.
    assert payload["metrics"] == {
        "appendix_entries": 0, "appendix_share": 0.0, "source_coverage_pct": 100.0}


def test_run_doctor_wires_second_pass_error_and_correction_count(tmp_path):
    """End to end (#818): a stage_3b whose t_validation errored surfaces as
    the WARN finding through run_doctor(), and total_post_corrections reaches
    the metrics block -- the wire, not just the helpers."""
    root = _build_clean_run(tmp_path)
    path = next((root / "stage_3b_classified_entries").glob("*_classified.json"))
    data = json.loads(path.read_text())
    data["meta"] = {"stats": {
        "t_validation": {"t_entries_reviewed": 4, "t_entries_reclassified": 0,
                         "error": "'int' object is not iterable"},
        "total_post_corrections": 7}}
    path.write_text(json.dumps(data))

    payload = run_doctor(root, _UID)

    hits = [f for f in payload["findings"]
            if f["lint"] == "stage3b_second_pass_error"]
    assert len(hits) == 1 and hits[0]["severity"] == "WARN"
    assert payload["metrics"]["total_post_corrections"] == 7


# ------------------------------------------------------------- #816: metrics

def test_build_metrics_reads_every_number_from_a_realistic_run(tmp_path):
    """One `_build_metrics` call over a run carrying all seven inputs at
    once: an appendix with real entries, an honors table with a malformed
    row, an unrouted code, a stage-3b fallback ratio, and both yield stats."""
    from unified_pipeline.run_doctor import _build_metrics

    stage3b = {
        "entries": [
            _entry("Mentored an invented student", taxonomy_code="N3", start=1),
            _entry("A grant", taxonomy_code="M2A", start=2),
            # #1256: the yield counts text in the parent, not tags. Of these
            # two fragments of the entry above, only the first is in it.
            _entry("A grant tail", taxonomy_code="M2A", start=3),
            _entry("tail", taxonomy_code="M2A", start=4, is_fragment=True, fragment_of=2),
            _entry("Lost line", taxonomy_code="M2A", start=5, is_fragment=True, fragment_of=2),
        ],
        "meta": {"stats": {
            "failed_batches": 41, "llm_batches": 83,
            "fallback_entries": 510, "entries_classified": 1019,
            "t_validation": {"t_entries_reviewed": 93, "t_entries_reclassified": 28},
            "fragment_reconnection": {"fragments_reviewed": 7, "fragments_reconnected": 3},
            "total_post_corrections": 5,
        }},
    }
    blocks = [
        ("p", "T. APPENDIX"),
        ("p", "These entries from your original CV could not be matched to a section of the WCM format."),
        ("p", "• Unmapped leftover entry one"),
        ("p", "• Unmapped leftover entry two"),
    ]
    table_rows = [
        [_HONORS_HEADER,
         [_BLOB, "MD", ""],
         ["Distinguished Teaching Award", "Indiana University", "2013"]],
    ]
    views = {
        "blocks": blocks, "stage_3b": stage3b, "table_rows": table_rows,
        "source_lines": ["GRANTS", "A grant text line here for coverage"],
        "stage_1a": _STAGE1A,
        "stage_2": {"entries": [_entry("A grant text line here for coverage",
                                       start=1)]},
    }

    metrics = _build_metrics(views)

    assert metrics["appendix_entries"] == 2
    assert metrics["appendix_share"] == round(2 / 5, 4)
    assert metrics["honors_malformed_rows"] == 1
    assert metrics["honors_rows"] == 2
    assert metrics["unrouted_code_entries"] == {"N3": 1}  # N3: #529 routes N2, #291 routes M4
    assert metrics["stage3b_fallback_ratio"] == round(510 / 1019, 4)
    assert metrics["t_validation_yield"] == round(28 / 93, 4)
    assert metrics["fragment_reconnection_yield"] == round(1 / 2, 4)  # not the stats' 3 / 7
    assert metrics["total_post_corrections"] == 5
    assert "source_coverage_pct" in metrics


def test_build_metrics_reports_a_zero_post_correction_count_but_omits_an_absent_one():
    """`total_post_corrections` is a count, so a measured 0 is real ("the
    corrector ran and changed nothing") and must be reported; an artifact
    from before the stat existed carries no key and must be omitted (#818)."""
    from unified_pipeline.run_doctor import _build_metrics

    ran = _build_metrics({"stage_3b": {"meta": {"stats": {"total_post_corrections": 0}}}})
    assert ran == {"total_post_corrections": 0}
    assert _build_metrics({"stage_3b": {"meta": {"stats": {}}}}) == {}
    assert _build_metrics({"stage_3b": {"meta": {"stats": {
        "total_post_corrections": "n/a"}}}}) == {}


@pytest.mark.parametrize("pass_key", ["t_validation", "fragment_reconnection"])
def test_second_pass_error_lint_names_the_errored_pass(pass_key):
    """A non-null `error` on either stage-3b second pass is a WARN that names
    the pass -- including an error text FATAL_ERROR_PATTERN misses (#818)."""
    from unified_pipeline.doctor.lints.runtime import lint_stage3b_second_pass_errors

    stage3b = {"meta": {"stats": {pass_key: {"error": "'int' object is not iterable"}}}}
    findings = lint_stage3b_second_pass_errors(stage3b)
    assert len(findings) == 1
    assert findings[0]["lint"] == "stage3b_second_pass_error"
    assert findings[0]["severity"] == "WARN"
    assert any(pass_key in e for e in findings[0]["evidence"])


def test_second_pass_error_lint_reports_both_passes_in_one_finding():
    from unified_pipeline.doctor.lints.runtime import lint_stage3b_second_pass_errors

    stage3b = {"meta": {"stats": {
        "t_validation": {"error": "boom one"},
        "fragment_reconnection": {"error": "boom two"}}}}
    findings = lint_stage3b_second_pass_errors(stage3b)
    assert len(findings) == 1
    assert len(findings[0]["evidence"]) == 2


@pytest.mark.parametrize("stage3b", [
    {},
    {"meta": None},
    {"meta": {"stats": None}},
    {"meta": {"stats": {"t_validation": {"error": None, "t_entries_reviewed": 3}}}},
    {"meta": {"stats": {"t_validation": {"error": ""}}}},
    {"meta": {"stats": {"fragment_reconnection": "not a dict"}}},
])
def test_second_pass_error_lint_is_silent_without_an_error(stage3b):
    from unified_pipeline.doctor.lints.runtime import lint_stage3b_second_pass_errors

    assert lint_stage3b_second_pass_errors(stage3b) == []


def test_build_metrics_omits_rather_than_reports_a_misleading_zero(tmp_path):
    """A metric whose denominator is 0, or whose input is entirely absent,
    must be OMITTED, not reported as a 0 that reads as measured-and-clean."""
    from unified_pipeline.run_doctor import _build_metrics

    assert _build_metrics({}) == {}
    assert _build_metrics({"blocks": None, "stage_3b": None,
                           "table_rows": None}) == {}
    # blocks present with NO appendix section at all -> appendix_entries is
    # None (not 0), so nothing is reported for it.
    assert _build_metrics({"blocks": [("p", "D. GRANTS")]}) == {}
    # stage_3b present but with an empty entries list: appendix_share's
    # denominator is 0, so the ratio is omitted even though appendix_entries
    # (computed from blocks alone) is not.
    metrics = _build_metrics({
        "blocks": [("p", "T. APPENDIX"), ("p", "boilerplate:"),
                   ("p", "• one leftover entry")],
        "stage_3b": {"entries": []},
    })
    assert metrics == {"appendix_entries": 1}


def test_build_metrics_never_raises_on_a_malformed_stage3b(tmp_path):
    """run_doctor() wraps _build_metrics in its own try/except (belt and
    braces), but the function itself should already degrade gracefully on
    a stage_3b shaped nothing like the real artifact."""
    from unified_pipeline.run_doctor import _build_metrics

    assert _build_metrics({"stage_3b": {"entries": "not a list"}}) == {}
    assert _build_metrics({"stage_3b": {"meta": "not a dict"}}) == {}


def test_run_doctor_reports_corrupt_artifact_as_error_not_missing(tmp_path):
    """A present-but-unparseable stage_4 must surface as an ERROR, not the
    benign INFO "skipped: missing stage_4" -- otherwise a corrupt artifact
    silently disables its lints and skews the corpus-sweep denominators with
    no signal at all."""
    root = _build_clean_run(tmp_path)
    stage4 = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    assert stage4.exists()
    stage4.write_text('{"entries": [ this is not valid json')  # file present, corrupt

    payload = run_doctor(root, _UID)
    messages = {(f["severity"], f["message"]) for f in payload["findings"]}

    # The lints keyed on stage_4 report it unreadable, at ERROR severity...
    unreadable = [f for f in payload["findings"]
                  if f["severity"] == "ERROR" and "unreadable" in f["message"]
                  and "stage_4" in f["message"]]
    assert unreadable, f"expected an ERROR naming unreadable stage_4; got {messages}"
    assert payload["counts"]["ERROR"] >= 1
    # ...and never mislabel a file that exists as "missing".
    assert not any(sev == "INFO" and "skipped: missing" in msg and "stage_4" in msg
                   for sev, msg in messages), \
        "a corrupt-but-present stage_4 must not be reported as missing"


def test_run_doctor_broken_source_docx_errors_both_source_lints(tmp_path):
    """A present-but-unparseable source docx must surface both source-fed lints
    (segmentation, missed_headers) as ERROR, not the benign INFO "skipped:
    missing". The two lints read the docx through independent loaders under
    separate _note labels ("source" / "candidates"); each must map to its own
    ready() kwarg so a broken file is never mislabelled absent."""
    root = _build_clean_run(tmp_path)
    source = root / "uploads" / f"{_UID}_cv.docx"
    assert source.exists()
    source.write_bytes(b"not a real docx, just bytes")  # present, unparseable

    payload = run_doctor(root, _UID)
    by_lint = {f["lint"]: f for f in payload["findings"]}
    for lint in ("segmentation", "missed_headers"):
        assert by_lint[lint]["severity"] == "ERROR", by_lint[lint]
        assert "unreadable" in by_lint[lint]["message"]
    assert not any(f["severity"] == "INFO" and "skipped: missing" in f["message"]
                   and f["lint"] in ("segmentation", "missed_headers")
                   for f in payload["findings"])


def test_run_doctor_reemits_sidecar_findings_end_to_end(tmp_path):
    root = _build_clean_run(tmp_path)
    _write_stage(root, "stage_6_wcm_documents", f"{_UID}_cv_render_warnings.json",
                 {"document_uid": _UID,
                  "warnings": [{"check": "semicolon_fused_bullets",
                                "code": "K3", "section": "Administrative teaching",
                                "message": "K3 (Administrative teaching): fused",
                                "evidence": []}],
                  "dedup_decisions": [
                      {"code": "Q4D", "metric": "containment=0.75",
                       "dropped_text": "Diagnosis (Jan 2024-Present)",
                       "kept_text": "Academic Medicine (Jan 2024-Present)"}]})
    payload = run_doctor(root, _UID)
    lints = {f["lint"] for f in payload["findings"] if f["severity"] == "WARN"}
    assert "stage6_render_warnings" in lints
    assert "dedup_drops" in lints


def test_run_doctor_wires_rendered_blocks_into_dedup_drops_info(tmp_path):
    """#666: the INFO tier needs the rendered document. `dedup_drops` is
    registered with `blocks` optional; dropping that wiring leaves the WARN
    tier working and the INFO tier silently dead, so drive run_doctor()."""
    root = _build_clean_run(tmp_path)
    _write_stage(root, "stage_6_wcm_documents", f"{_UID}_cv_render_warnings.json",
                 {"document_uid": _UID, "warnings": [],
                  "dedup_decisions": [
                      {"code": "Q4D", "metric": "containment=1.00",
                       "dropped_text": "Example Optics",
                       "kept_text": "European Example Optics",
                       "dropped_fields": {"journal_name": "Example Optics"},
                       "kept_fields": {"journal_name": "European Example Optics"}}]})
    payload = run_doctor(root, _UID)
    drops = [f for f in payload["findings"] if f["lint"] == "dedup_drops"]
    assert [f["severity"] for f in drops] == ["INFO"]


def test_run_doctor_wires_stage_5d_into_dedup_drops_entry_index(tmp_path):
    """The evidence names the dropped entry only through the optional
    `stage_5d` view; without that wiring every drop goes back to naming text,
    which the precision harness cannot match to a verified finding."""
    root = _build_clean_run(tmp_path)
    dropped = "3/2031 Example grand rounds talk"
    _write_stage(root, "stage_6_wcm_documents", f"{_UID}_cv_render_warnings.json",
                 {"document_uid": _UID, "warnings": [],
                  "dedup_decisions": [
                      {"code": "K4", "metric": "jaccard=0.95", "dropped_text": dropped,
                       "kept_text": "9/2031 Example grand rounds talk, 3 hours"}]})
    _write_stage(root, "stage_5d_citation_formatted", f"{_UID}_cv_citation_formatted.json",
                 {"entries": [{"element_idx_start": 77, "taxonomy_code": "K4",
                               "text": dropped, "extracted_fields": {}}]})
    payload = run_doctor(root, _UID)
    drops = [f for f in payload["findings"] if f["lint"] == "dedup_drops"]
    assert drops[0]["evidence"][0].startswith("entry 77: K4 (")


def test_run_doctor_hard_fail_gate_makes_an_undeliverable_run_an_error(tmp_path):
    """web139: score 25, RED, do-not-deliver — but worst=WARN, indistinguishable
    from a healthy run. An emptied cv_owner must now push worst to ERROR and say
    which gate tripped, because run_corpus_batch.sh only prints `worst` (#437)."""
    root = _build_clean_run(tmp_path)
    fields = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    data = json.loads(fields.read_text())
    data["cv_owner"] = dict(_OWNER_EMPTY, last_name=_UID)
    fields.write_text(json.dumps(data))

    payload = run_doctor(root, _UID)
    assert payload["worst_severity"] == "ERROR"
    errors = [f for f in payload["findings"] if f["severity"] == "ERROR"]
    assert [f["lint"] for f in errors] == ["owner_contact_missing"]
    assert "cv_owner" in errors[0]["message"]


def test_run_doctor_owner_gate_errors_when_only_stage4_is_absent(tmp_path):
    """The cap-25 gate has TWO trip conditions — an empty cv_owner name AND a
    missing *_fields.json (quality_score.score_cv_owner: "no fields.json
    found"). A run that rendered a document but lost its fields.json scores
    25/RED, so the doctor must ERROR rather than take the quiet skip path."""
    root = _build_clean_run(tmp_path)
    (root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json").unlink()
    payload = run_doctor(root, _UID)
    owner = next(f for f in payload["findings"]
                 if f["lint"] == "owner_contact_missing")
    assert owner["severity"] == "ERROR"
    assert "no stage-4 *_fields.json" in owner["message"]
    assert "25" in owner["message"]
    assert payload["worst_severity"] == "ERROR"


def test_run_doctor_owner_gate_skips_a_run_that_never_reached_stage_4(tmp_path):
    """An incomplete run has no owner name YET, so "do not deliver" would be a
    false positive. run_corpus_batch.sh doctors every CV including ones whose
    pipeline returned rc!=0, so this is reachable in the batch tooling."""
    root = _build_clean_run(tmp_path)
    for stage in ("stage_4_field_extraction", "stage_6_wcm_documents"):
        for leftover in (root / stage).glob(f"{_UID}*"):
            leftover.unlink()
    payload = run_doctor(root, _UID)
    owner = next(f for f in payload["findings"]
                 if f["lint"] == "owner_contact_missing")
    assert owner["severity"] == "INFO"
    assert owner["message"] == "skipped: missing stage_4"
    assert payload["worst_severity"] != "ERROR"


def test_run_doctor_owner_gate_skips_when_the_run_produced_nothing(tmp_path):
    """...but a uid with no scorable output at all is a wrong uid, not an
    undeliverable run: quality_score_service returns no score rather than a RED
    one when none of _NEEDED_SUFFIXES landed, so there is no gate to report."""
    root = tmp_path / "empty"
    root.mkdir()
    payload = run_doctor(root, "NOPE")
    gates = {f["lint"]: f for f in payload["findings"]
             if f["lint"] in ("owner_contact_missing", "pipeline_errors_present")}
    assert set(gates) == {"owner_contact_missing", "pipeline_errors_present"}
    assert gates["owner_contact_missing"]["severity"] == "INFO"
    assert gates["owner_contact_missing"]["message"] == "skipped: missing stage_4"
    assert gates["pipeline_errors_present"]["severity"] == "INFO"
    assert gates["pipeline_errors_present"]["message"] == \
        "skipped: missing stage_2, stage_3b, stage_4"


# --------------------------------- round-2 F3: test the WIRE, not just the
# rule -- both hard-fail gates above are dispatched from run_doctor() by a
# `_run_lint(...)` call the rule-level tests never exercise; deleting either
# call (verifier mutant m20 and its stage3b_fallback_ratio counterpart) left
# the full `test_run_doctor.py` green because nothing drove the gate through
# run_doctor() itself, only through the bare lint function.

def test_run_doctor_wires_no_output_through_to_the_verdict(tmp_path):
    """web204: stage 4 ran, stage 6 never did (#812) -- run_doctor() itself,
    not just lint_no_output(), must surface the ERROR. Deleting the
    `_run_lint("no_output", ...)` call in run_doctor() (m20) leaves this red
    while every rule-level no_output test stays green."""
    root = _build_clean_run(tmp_path)
    for leftover in (root / "stage_6_wcm_documents").glob(f"{_UID}*"):
        leftover.unlink()

    payload = run_doctor(root, _UID)

    assert payload["worst_severity"] == "ERROR"
    no_output = [f for f in payload["findings"] if f["lint"] == "no_output"]
    assert len(no_output) == 1
    assert no_output[0]["severity"] == "ERROR"


def test_run_doctor_wires_stage3b_fallback_ratio_through_to_the_verdict(tmp_path):
    """web30's own outage numbers, driven through run_doctor() end to end --
    not just lint_stage3b_fallback_ratio() in isolation. Deleting run_doctor's
    dispatch of this lint (the LINT_REGISTRY row / its `_run_lint` call) must
    fail this while the rule-level tests above stay green."""
    root = _build_clean_run(tmp_path)
    classified = root / "stage_3b_classified_entries" / f"{_UID}_cv_classified.json"
    data = json.loads(classified.read_text())
    data["meta"] = {"stats": {"failed_batches": 41, "llm_batches": 83,
                              "fallback_entries": 510, "entries_classified": 1019}}
    classified.write_text(json.dumps(data))

    payload = run_doctor(root, _UID)

    assert payload["worst_severity"] == "ERROR"
    fallback = [f for f in payload["findings"]
               if f["lint"] == "stage3b_fallback_ratio"]
    assert len(fallback) == 1
    assert fallback[0]["severity"] == "ERROR"


def test_run_doctor_wires_stage4_group_failures_through_to_the_verdict(tmp_path):
    """A stage-4 artifact with a rescued failed group, driven through
    run_doctor() end to end -- not just lint_stage4_group_failures(). Deleting
    the LINT_REGISTRY row leaves every rule-level test above green and fails
    this."""
    root = _build_clean_run(tmp_path)
    assert not [f for f in run_doctor(root, _UID)["findings"]
                if f["lint"] == "stage4_group_failures"]
    fields = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    data = json.loads(fields.read_text())
    data["entries"][0].update({"extraction_error": "llm_response_invalid",
                               "llm_recovery_applied": True})
    data["stats"] = {"failed_batches": 1, "extraction_failed": 0}
    fields.write_text(json.dumps(data))

    payload = run_doctor(root, _UID)

    found = [f for f in payload["findings"] if f["lint"] == "stage4_group_failures"]
    assert len(found) == 1
    assert found[0]["severity"] == "WARN"
    assert payload["counts"]["ERROR"] == 0


def test_run_doctor_wires_invented_records_through_to_the_verdict(tmp_path):
    """A5IZ6Q (#829), driven through run_doctor() end to end -- not just
    lint_invented_records() in isolation, the way every rule-level test in
    test_doctor_extraction_lint_contracts.py exercises it.

    lint_invented_records' SECOND positional argument is `table_rows`
    (read_docx_table_rows' per-table row lists), not `blocks`
    (read_docx_blocks' paragraph/table text stream) -- the two views share
    one _VIEW_LABELS loader label ("stage_6_docx") because both read the
    same file, so a LINT_REGISTRY row wired to the wrong one
    (`("stage_4", "blocks")` instead of `("stage_4", "table_rows")`) still
    passes `_ready()` and never raises: `_rendered_row_value_sets` just
    treats each ("kind", text) tuple in `blocks` as if it were a table row,
    silently finds nothing that matches, and the WARN never fires. That
    wiring mistake leaves every rule-level test green (they pass table_rows
    by hand) while this end-to-end check catches it."""
    root = _build_clean_run(tmp_path)
    board_label, cert_label = "Full Name of Board", "Certificate #"

    fields = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    data = json.loads(fields.read_text())
    data["entries"].append({
        "taxonomy_code": "F2", "element_type": "table_row",
        "element_idx_start": 99,
        "text": f"{board_label} | {cert_label}",
        "extracted_fields": {"certifying_board": board_label,
                             "certificate_number": cert_label,
                             "year_certified": None,
                             "recertification_date": None}})
    fields.write_text(json.dumps(data))

    docx_path = next((root / "stage_6_wcm_documents").glob(f"{_UID}*_wcm.docx"))
    doc = Document(str(docx_path))
    table = doc.add_table(rows=1, cols=2)
    table.rows[0].cells[0].paragraphs[0].text = board_label
    table.rows[0].cells[1].paragraphs[0].text = cert_label
    doc.save(str(docx_path))

    payload = run_doctor(root, _UID)

    invented = [f for f in payload["findings"] if f["lint"] == "invented_records"]
    assert len(invented) == 1
    assert invented[0]["severity"] == "WARN"
    assert "F2" in invented[0]["message"]
    assert "99" in invented[0]["message"]


def test_run_doctor_wires_wrong_start_date_through_to_the_verdict(tmp_path):
    """#729, end to end: the LINT_REGISTRY row must hand the lint stage 4."""
    root = _build_clean_run(tmp_path)
    fields = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    data = json.loads(fields.read_text())
    data["entries"].append({
        "taxonomy_code": "D1", "element_type": "paragraph",
        "element_idx_start": 98, "text": "Example Board | 2025-2026",
        "extracted_fields": {"start_date": "2026", "end_date": None}})
    fields.write_text(json.dumps(data))

    payload = run_doctor(root, _UID)

    hits = [f for f in payload["findings"] if f["lint"] == "wrong_start_date"]
    assert len(hits) == 1
    assert hits[0]["severity"] == "WARN"
    assert "98" in hits[0]["message"]


def test_run_doctor_wires_offschema_fields_through_to_the_verdict(tmp_path):
    """The LINT_REGISTRY row must hand the lint stage 4; invented values."""
    root = _build_clean_run(tmp_path)
    fields = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    data = json.loads(fields.read_text())
    data["entries"].append({
        "taxonomy_code": "I", "element_type": "paragraph",
        "element_idx_start": 97, "text": "Society A\tSociety B",
        "extracted_fields": {"organization": "Society A",
                             "organization_2": "Society B"}})
    fields.write_text(json.dumps(data))

    payload = run_doctor(root, _UID)

    hits = [f for f in payload["findings"] if f["lint"] == "offschema_fields"]
    assert len(hits) == 1
    assert hits[0]["severity"] == "WARN"
    assert hits[0]["evidence"] == ["entry 97: Society B"]


def test_run_doctor_hands_offschema_fields_the_rendered_document(tmp_path):
    """The LINT_REGISTRY row must also hand the lint the docx (#1245): a value
    its record's own rendered line shows is then not reported, where stage 4
    alone would report it. Invented values."""
    root = _build_clean_run(tmp_path)
    fields = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    data = json.loads(fields.read_text())
    data["entries"].append({
        "taxonomy_code": "B1", "element_type": "paragraph",
        "element_idx_start": 98, "text": "BA, Example College, Townsville",
        "extracted_fields": {"degree": "BA", "institution": "Example College",
                             "location": "Townsville"}})
    fields.write_text(json.dumps(data))
    docx_path = root / "stage_6_wcm_documents" / f"{_UID}_cv_wcm.docx"
    output = Document(str(docx_path))
    output.add_paragraph("BA, Example College, Townsville")
    output.save(str(docx_path))

    payload = run_doctor(root, _UID)

    assert [f for f in payload["findings"] if f["lint"] == "offschema_fields"] == []


def test_run_doctor_wires_implausible_year_through_to_the_verdict(tmp_path):
    """The LINT_REGISTRY row must hand the lint stage 4; invented values."""
    root = _build_clean_run(tmp_path)
    fields = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    data = json.loads(fields.read_text())
    data["entries"].append({
        "taxonomy_code": "R", "element_type": "paragraph",
        "element_idx_start": 96, "text": "Invited talk, Example City 11/02",
        "extracted_fields": {"title": "Invited talk", "date": "1902-11"}})
    fields.write_text(json.dumps(data))

    payload = run_doctor(root, _UID)

    hits = [f for f in payload["findings"] if f["lint"] == "implausible_year"]
    assert len(hits) == 1
    assert hits[0]["severity"] == "WARN"
    assert "entry 96 (R): date=1902" in hits[0]["message"]


def test_run_doctor_hands_the_year_lints_stage_5d(tmp_path):
    """The registry rows give implausible_year and year_not_in_source the
    stage-5d artifact: a year a formatter rewrote out of the entry is not
    judged, and without 5d the same entries are. Invented values."""
    root = _build_clean_run(tmp_path)
    fields = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    data = json.loads(fields.read_text())
    data["entries"] += [
        {"taxonomy_code": "K4", "element_type": "paragraph", "element_idx_start": 97,
         "element_idx_end": 97, "text": "Example lecture, March 3, 2014",
         "extracted_fields": {"date": "1900-03-03"}},
        {"taxonomy_code": "K1", "element_type": "paragraph", "element_idx_start": 98,
         "element_idx_end": 98, "text": "Example course 2017-2019",
         "extracted_fields": {"start_date": "2016"}}]
    fields.write_text(json.dumps(data))

    def year_hits():
        payload = run_doctor(root, _UID)
        return sorted(f["lint"] for f in payload["findings"]
                      if f["lint"] in ("implausible_year", "year_not_in_source"))

    assert year_hits() == ["implausible_year", "year_not_in_source"]
    _write_stage(root, "stage_5d_citation_formatted", f"{_UID}_cv_citation_formatted.json",
                 {"document_uid": _UID, "entries": [
                     {"element_idx_start": 97, "element_idx_end": 97,
                      "extracted_fields": {"formatted_text": "**2014-03-03** - Example lecture"}},
                     {"element_idx_start": 98, "element_idx_end": 98,
                      "extracted_fields": {"formatted_text": "2017-2019 Example course"}}]})
    assert year_hits() == []


def test_run_doctor_hands_table_shape_stage_4(tmp_path):
    """The table_shape row gives the lint stage 4, so a one-award honors entry
    rendered as two rows reaches the report as a WARN."""
    root = _build_clean_run(tmp_path)
    fields = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    data = json.loads(fields.read_text())
    data["entries"].append({
        "taxonomy_code": "H", "element_type": "paragraph", "element_idx_start": 99,
        "text": "Example Teaching Award\nExample College - 2005",
        "extracted_fields": {"award_name": "Example Teaching Award", "date": "2005"}})
    fields.write_text(json.dumps(data))
    docx_path = root / "stage_6_wcm_documents" / f"{_UID}_cv_wcm.docx"
    output = Document(docx_path)
    table = output.add_table(rows=3, cols=3)
    for row, cells in zip(table.rows, (_HONORS_HEADER, ["Example Teaching Award", "", "2005"],
                                       ["Example College \u2014 2005", "", ""])):
        for cell, text in zip(row.cells, cells):
            cell.paragraphs[0].text = text
    output.save(docx_path)

    payload = run_doctor(root, _UID)

    split = [f for f in payload["findings"]
             if f["lint"] == "table_shape" and "split" in f["message"]]
    assert len(split) == 1 and split[0]["severity"] == "WARN"
    assert split[0]["evidence"] == ["entry 99 (H): 2 rows from 1 stage-4 award(s)"]


def test_field_lint_prevalence_is_the_measured_wave1_fraction():
    """Measured 2026-10-02 over the 163-CV wave-1 stage-4 farm (one fire per
    CV at any severity); a new measurement updates both sides."""
    from unified_pipeline.run_doctor import LINT_PREVALENCE
    assert LINT_PREVALENCE["offschema_fields"] == round(37 / 163, 3)
    assert LINT_PREVALENCE["implausible_year"] == round(6 / 163, 3)
    # #1243: the farm's 126 census CVs; its 37 IPXFBA artifacts are gone.
    assert LINT_PREVALENCE["multi_record_coverage"] == round(64 / 126, 3)
    # 63-run EBYSBC/s7ab/pilot farm, 2026-10-02.
    assert LINT_PREVALENCE["year_not_in_source"] == round(11 / 63, 3)


def test_date_cell_shape_prevalence_is_the_measured_farm_fraction():
    """Measured 2026-10-03 over the 63-run EBYSBC/s7ab/pilot doctor farm as
    rendered by origin/dev 8a0a5445 (one fire per run at any severity); a
    new measurement updates both sides."""
    from unified_pipeline.run_doctor import LINT_PREVALENCE
    assert LINT_PREVALENCE["date_cell_shape"] == round(19 / 63, 3)


def test_classification_lint_prevalence_is_the_measured_farm_fraction():
    """Measured 2026-10-04 over the 63-run EBYSBC/s7ab/pilot farm's stage
    1b/2/3b artifacts (one fire per run at any severity); a new measurement
    updates both sides."""
    from unified_pipeline.run_doctor import LINT_PREVALENCE
    assert LINT_PREVALENCE["section_consistency"] == round(23 / 63, 3)
    assert LINT_PREVALENCE["segmentation_collapse"] == round(1 / 63, 3)


def test_span_count_prevalence_is_the_measured_corpus_fraction():
    """Measured 2026-10-05 over the 245 stored runs with stage-4 JSON (106
    analysis/<uid>, 13 analysis/pilot, 126 farm and batch), as rendered by
    origin/dev 5e6eac1d plus the stage-6 envelope fix (one fire per run at
    any severity); a new measurement updates both sides."""
    from unified_pipeline.run_doctor import LINT_PREVALENCE
    assert LINT_PREVALENCE["span_count"] == round(64 / 245, 3)


def test_run_doctor_hands_role_consistency_the_rendered_grant_tables(tmp_path):
    """The role_consistency row must hand the lint the docx (#1403, EOAHMI
    JIJRSN 516): a grant table whose role says PI and whose PI cell is empty
    reaches the report as a WARN. Invented values."""
    from unified_pipeline.stage6.sections.research_support import (
        PI_NAME_LABEL,
        PROJECT_TITLE_LABEL,
        YOUR_ROLE_LABEL,
    )
    root = _build_clean_run(tmp_path)
    fields = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    data = json.loads(fields.read_text())
    data["entries"].append({
        "taxonomy_code": "M2B", "element_type": "paragraph", "element_idx_start": 516,
        "text": "2013-2018: Principal Investigator in \u201cExample Trial\u201d",
        "extracted_fields": {"title": "Example Trial", "pi_role": "PI"}})
    fields.write_text(json.dumps(data))
    docx_path = root / "stage_6_wcm_documents" / f"{_UID}_cv_wcm.docx"
    output = Document(docx_path)
    table = output.add_table(rows=3, cols=2)
    for row, cells in zip(table.rows, ((PROJECT_TITLE_LABEL, "Example Trial"),
                                       (PI_NAME_LABEL, ""), (YOUR_ROLE_LABEL, "PI"))):
        for cell, text in zip(row.cells, cells):
            cell.paragraphs[0].text = text
    output.save(docx_path)

    payload = run_doctor(root, _UID)

    hits = [f for f in payload["findings"] if f["lint"] == "role_consistency"]
    assert [(f["severity"], f["message"].split(":")[0]) for f in hits] == [("WARN", "entry 516")]
    assert "(pi_cell_empty, #1403)" in hits[0]["message"]


def test_role_consistency_prevalence_is_the_measured_fraction():
    """Measured 2026-10-06 (X6-role) over the stored docx of 125 analysis/
    runs and a render of origin/dev 05966dac of the 126 farm/batch runs:
    75 and 31 of them (#1403); a new measurement updates both sides."""
    from unified_pipeline.run_doctor import LINT_PREVALENCE
    assert LINT_PREVALENCE["role_consistency"] == round(106 / 251, 3)


def test_run_doctor_hard_fail_gates_label_corrupt_artifacts_as_unreadable(tmp_path):
    """_ready's contract: a None input is an ERROR "unreadable" when the file
    existed but would not parse, never the benign "missing". The error scan
    passes real loader labels so that distinction survives (#437)."""
    root = tmp_path / "corrupt"
    for stage_dir, name in (("stage_2_entry_extraction", "NOPE_entries.json"),
                            ("stage_3b_classified_entries", "NOPE_classified.json"),
                            ("stage_4_field_extraction", "NOPE_fields.json")):
        (root / stage_dir).mkdir(parents=True)
        (root / stage_dir / name).write_text("{not json")
    payload = run_doctor(root, "NOPE")
    gate = next(f for f in payload["findings"]
                if f["lint"] == "pipeline_errors_present")
    assert gate["severity"] == "ERROR"
    assert gate["message"].startswith("skipped: unreadable ")
    assert "missing" not in gate["message"]
    for label in ("stage_2", "stage_3b", "stage_4"):
        assert label in gate["message"]
    owner = next(f for f in payload["findings"]
                 if f["lint"] == "owner_contact_missing")
    assert owner["severity"] == "ERROR"
    assert "will not parse" in owner["message"]


def test_run_doctor_error_scan_covers_only_what_the_deployed_scorer_reads(tmp_path):
    """quality_score_service copies *_entries/_classified/_fields.json into the
    dir it globs, so a fatal in stage_5 does NOT cap the deployed score — and
    the lint says "capped at 40". Scan the scorer's set, keep the claim true."""
    root = _build_clean_run(tmp_path)
    fatal = "NameError: name 'response' is not defined"

    enriched = root / "stage_5_enrichment" / f"{_UID}_cv_enriched.json"
    data = json.loads(enriched.read_text())
    data["error"] = fatal
    enriched.write_text(json.dumps(data))
    payload = run_doctor(root, _UID)
    assert not [f for f in payload["findings"]
                if f["lint"] == "pipeline_errors_present"]

    classified = root / "stage_3b_classified_entries" / f"{_UID}_cv_classified.json"
    data = json.loads(classified.read_text())
    data["error"] = fatal
    classified.write_text(json.dumps(data))
    payload = run_doctor(root, _UID)
    fired = [f for f in payload["findings"]
             if f["lint"] == "pipeline_errors_present"]
    assert [f["severity"] for f in fired] == ["ERROR"]
    assert "40" in fired[0]["message"]


def test_run_doctor_accepts_source_override(tmp_path):
    root = _build_clean_run(tmp_path)
    override = tmp_path / "elsewhere.docx"
    (root / "uploads" / f"{_UID}_cv.docx").rename(override)
    payload = run_doctor(root, _UID, source=override)
    assert payload["artifacts"]["source"] == str(override)
    assert not any(f["lint"] == "segmentation" and "skipped" in f["message"]
                   for f in payload["findings"])


# ------------------------------------------------------------------- CLI / exit

def test_main_exits_1_and_writes_report_on_warning(tmp_path):
    root = tmp_path / "outputs"
    _write_stage(root, "stage_4_field_extraction", f"{_UID}_cv_fields.json",
                 {"document_uid": _UID, "cv_owner": _OWNER, "entries": [
                     _grant4(_GRANT_TEMPLETON, "M2A")]})
    output = Document()
    output.add_paragraph("Current Research Funding")
    table = output.add_table(rows=1, cols=1)
    table.rows[0].cells[0].paragraphs[0].text = _GRANT_TEMPLETON
    out_dir = root / "stage_6_wcm_documents"
    out_dir.mkdir(parents=True)
    output.save(out_dir / f"{_UID}_cv_wcm.docx")
    with pytest.raises(SystemExit) as exc:
        main([str(root), _UID])
    assert exc.value.code == 1
    report = json.loads((root / f"{_UID}_doctor.json").read_text())
    assert report["counts"]["WARN"] >= 1
    assert any(f["lint"] == "bucket_status" for f in report["findings"])


def test_main_exits_0_on_clean_run(tmp_path):
    root = _build_clean_run(tmp_path)
    out = tmp_path / "reports" / "doctor.json"
    with pytest.raises(SystemExit) as exc:
        main([str(root), _UID, "--out", str(out)])
    assert exc.value.code == 0
    assert json.loads(out.read_text())["worst_severity"] == "INFO"


def test_main_truncates_oversized_report_and_flags_it(tmp_path, monkeypatch):
    # Shrink the caps so a normal report trips them: the missing-artifacts run
    # yields 15 findings (14 INFO skips + the owner gate's ERROR).
    monkeypatch.setattr("unified_pipeline.run_doctor.MAX_REPORT_BYTES", 10)
    monkeypatch.setattr("unified_pipeline.run_doctor.MAX_REPORT_FINDINGS", 5)
    root = tmp_path / "empty"
    root.mkdir()
    out = tmp_path / "doctor.json"
    with pytest.raises(SystemExit):
        main([str(root), "NOPE", "--out", str(out)])
    report = json.loads(out.read_text())
    assert report["findings_truncated"] is True
    assert len(report["findings"]) == 5


def test_main_exits_2_when_report_write_fails(tmp_path):
    # out_path is an existing directory: the advisory os.access check passes but
    # write_text raises IsADirectoryError (an OSError), which must exit(2), not
    # crash with a traceback.
    root = _build_clean_run(tmp_path)
    out = tmp_path / "a_directory"
    out.mkdir()
    with pytest.raises(SystemExit) as exc:
        main([str(root), _UID, "--out", str(out)])
    assert exc.value.code == 2


def test_missed_headers_ignores_trailing_colon():
    """A header the segmenter promoted is not 'missed' just because the source
    line ends in ':' (2026-07-15 corpus: 36 of 61 findings were this artifact)."""
    from unified_pipeline.run_doctor import lint_missed_headers

    stage1a = {"hierarchy": [{"text": "PROFESSIONAL SOCIETIES", "children": []}]}
    stage2 = {"entries": [{"hierarchy": ["RESEARCH SUPPORT AND GRANTS"]}]}

    # both are present in segmentation -- one via 1a, one via an entry path
    assert lint_missed_headers(["PROFESSIONAL SOCIETIES:"], stage1a, stage2) == []
    assert lint_missed_headers(["RESEARCH SUPPORT AND GRANTS:"], stage1a, stage2) == []

    # a genuinely absent header is still reported
    found = lint_missed_headers(["INTELLECTUAL PROPERTY:"], stage1a, stage2)
    assert len(found) == 1
    assert found[0]["lint"] == "missed_headers"


# ------------------------------------------------ #814: enumeration prefixes
# and wrapped headers in missed_headers

def test_missed_headers_ignores_a_roman_numeral_enumeration_prefix():
    """web199: stage 1a promotes 'I.  CURRENT POSITION' to the hierarchy node
    'CURRENT POSITION', without the numeral -- comparing the raw forms
    reported all 11 of web199's sections as missing."""
    stage1a = {"hierarchy": [{"text": "CURRENT POSITION", "children": []}]}
    assert lint_missed_headers(["I.  CURRENT POSITION"], stage1a, {"entries": []}) == []
    # a different roman numeral, multi-letter
    stage1a_xi = {"hierarchy": [{"text": "BIBLIOGRAPHY", "children": []}]}
    assert lint_missed_headers(["XI.  BIBLIOGRAPHY"], stage1a_xi, {"entries": []}) == []


def test_missed_headers_joins_a_header_wrapped_over_two_source_lines():
    """web228: the source wraps one long header over two physical lines,
    which stage 1a correctly joins into a single hierarchy node -- neither
    physical line matches the joined title alone (5 of 7 findings on that
    CV were this)."""
    stage1a = {"hierarchy": [{
        "text": "SERVICE ON NATIONAL GRANT REVIEW PANELS, STUDY SECTIONS, COMMITTEES",
        "children": []}]}
    candidates = ["SERVICE ON NATIONAL GRANT REVIEW PANELS, STUDY SECTIONS,",
                 "COMMITTEES:"]
    assert lint_missed_headers(candidates, stage1a, {"entries": []}) == []


def test_missed_headers_short_candidate_does_not_match_by_prefix():
    """A short candidate ('AND') must not silently match a longer, unrelated
    title just because it happens to be a prefix or suffix of it -- the
    over-normalisation guard #814 added."""
    stage1a = {"hierarchy": [{
        "text": "AND SOME COMPLETELY UNRELATED LONG TITLE", "children": []}]}
    found = lint_missed_headers(["AND"], stage1a, {"entries": []})
    assert len(found) == 1
    assert "AND" in found[0]["message"]


def test_missed_headers_true_positive_still_fires_after_814():
    """A header genuinely absent from segmentation must still be reported --
    #814's normalisation additions (enumeration stripping, prefix/suffix
    matching) must not silence a real miss."""
    stage1a = {"hierarchy": [{"text": "EDUCATION", "children": []}]}
    found = lint_missed_headers(["PROFESSIONAL SOCIETIES"], stage1a, {"entries": []})
    assert len(found) == 1
    assert "PROFESSIONAL SOCIETIES" in found[0]["message"]


# -------------------------------------- #1232: header spellings stage 1a
# normalises, and a wrapped header it copy-edited

def test_missed_headers_folds_a_typographic_apostrophe():
    """A page-break running header is spelled with a curly apostrophe in the
    source and an ASCII one in the 1a node: the header WAS detected."""
    ascii_node = {"hierarchy": [{"text": "LOCAL (CONT'D)", "children": []}]}
    assert lint_missed_headers(["LOCAL (CONT\u2019D)"], ascii_node, {"entries": []}) == []
    curly_node = {"hierarchy": [{"text": "LOCAL (CONT\u2019D)", "children": []}]}
    assert lint_missed_headers(["LOCAL (CONT'D)"], curly_node, {"entries": []}) == []
    found = lint_missed_headers(["OTHER (CONT\u2019D)"], ascii_node, {"entries": []})
    assert len(found) == 1


def test_missed_headers_ignores_a_leading_asterisk_or_bullet_marker():
    stage1a = {"hierarchy": [{"text": "TENURE REVIEWS", "children": []},
                             {"text": "JOURNAL REVIEWS (WITHIN RANK)", "children": []}]}
    assert lint_missed_headers(["*TENURE REVIEWS"], stage1a, {"entries": []}) == []
    assert lint_missed_headers(["\u2022 JOURNAL REVIEWS (WITHIN RANK):"], stage1a,
                               {"entries": []}) == []
    assert len(lint_missed_headers(["*GRANT REVIEWS"], stage1a, {"entries": []})) == 1


def test_missed_headers_ignores_a_trailing_empty_value_but_not_a_real_one():
    """1a promotes 'SPECIALTY BOARD STATUS: N/A' as the label alone."""
    stage1a = {"hierarchy": [{"text": "SPECIALTY BOARD STATUS", "children": []}]}
    assert lint_missed_headers(["SPECIALTY BOARD STATUS: N/A"], stage1a,
                               {"entries": []}) == []
    assert len(lint_missed_headers(["SPECIALTY BOARD STATUS: CERTIFIED"], stage1a,
                                   {"entries": []})) == 1


_COPY_EDITED_TITLE = "OVERVIEW OF EXAMPLE ACCOMPLISHMENTS IN TEACHING, SERVICE AND RESEARCH"


def test_missed_headers_matches_a_wrapped_header_that_1a_copy_edited():
    """The source wraps one header over two lines and misspells a word in the
    first; 1a joins them and corrects it. The joined text is then neither
    equal to, nor a prefix or suffix of, the node (the second line alone is
    under the length-ratio guard), so only a near-copy match recognises it."""
    stage1a = {"hierarchy": [{"text": _COPY_EDITED_TITLE, "children": []}]}
    candidates = ["I. OVERVEIW OF EXAMPLE ACCOMPLISHMENTS IN TEACHING,",
                  "SERVICE AND RESEARCH"]
    assert lint_missed_headers(candidates, stage1a, {"entries": []}) == []


def test_missed_headers_quiet_on_a_tab_led_wrapped_header_read_from_a_docx(tmp_path):
    """The wire for the tab-led wrapped header: the reader must pair the
    tab-led first line with its continuation, and the lint must then match
    the joined text to the node 1a copy-edited."""
    doc = Document()
    doc.add_paragraph("I. \tOVERVEIW OF EXAMPLE ACCOMPLISHMENTS IN TEACHING,").runs[0].bold = True
    doc.add_paragraph("SERVICE AND RESEARCH").runs[0].bold = True
    path = tmp_path / "cv.docx"
    doc.save(path)

    stage1a = {"hierarchy": [{"text": "I. " + _COPY_EDITED_TITLE, "children": []}]}
    candidates = iter_header_candidates(str(path))
    assert len(candidates) == 2
    assert lint_missed_headers(candidates, stage1a, {"entries": []}) == []


def test_missed_headers_near_copy_match_does_not_silence_a_different_header():
    stage1a = {"hierarchy": [{"text": _COPY_EDITED_TITLE, "children": []}]}
    candidates = ["OVERVIEW OF EXAMPLE ACCOMPLISHMENTS IN TEACHING,",
                  "AND UNRELATED COMMITTEE SERVICE"]
    found = lint_missed_headers(candidates, stage1a, {"entries": []})
    assert [f["evidence"] for f in found] == [["AND UNRELATED COMMITTEE SERVICE"]]


# ---------------------------------------------------- round-2 F1: a standalone
# candidate matching a known title by PREFIX or SUFFIX alone, with no next
# candidate to join against -- the verifier's mutant m11 (`_key_matches_title`
# -> `return False`, deleting the whole prefix/suffix branch) survived every
# existing test because none of them requires that branch to ever return True;
# the "wrapped header" test above is satisfied by the JOIN's exact match
# instead. These two are the missing positive coverage.

def test_missed_headers_standalone_prefix_of_a_known_title():
    """A candidate that is a genuine, substantial PREFIX of a known title,
    with no following candidate to join against, must be recognised -- not
    just the two-line-join case above. Ratio 44/53 = 0.83, well past the
    round-2 F2 length-ratio guard."""
    stage1a = {"hierarchy": [{
        "text": "COMMITTEE ON RESEARCH INTEGRITY AND ETHICS OVERSIGHT",
        "children": []}]}
    assert lint_missed_headers(
        ["COMMITTEE ON RESEARCH INTEGRITY AND ETHICS"], stage1a,
        {"entries": []}) == []


def test_missed_headers_standalone_suffix_of_a_known_title():
    """The SUFFIX mirror of the prefix case above: a candidate that is the
    tail of a known title on its own, with no preceding candidate. Ratio
    34/47 = 0.72."""
    stage1a = {"hierarchy": [{
        "text": "SOCIETY FOR EXPERIMENTAL BIOLOGY AND MEDICINE",
        "children": []}]}
    assert lint_missed_headers(
        ["EXPERIMENTAL BIOLOGY AND MEDICINE"], stage1a,
        {"entries": []}) == []


# ------------------------------------------------------- round-2 F2: prefix/
# suffix matching must run against stage-1a titles (`known`) only, never
# against stage-2 entry hierarchy paths (`paths`) -- exact matching may still
# use `paths` (an entry filed under a header IS that header, verbatim).
# web200 (batch-3): the standalone bold label 'UK GOVERNMENT' was silenced as
# a suffix of an unrelated section; see MIN_KEY_TO_TITLE_RATIO's docstring.

def test_missed_headers_exact_match_still_allowed_via_stage2_paths():
    """Exact matching against a stage-2 entry hierarchy path is unaffected by
    the round-2 F2 restriction -- only prefix/suffix narrowed to `known`."""
    stage2 = {"entries": [{"hierarchy": ["GRANTS ADMINISTRATION COMMITTEE"]}]}
    assert lint_missed_headers(
        ["GRANTS ADMINISTRATION COMMITTEE"], {"hierarchy": []}, stage2) == []


def test_missed_headers_prefix_suffix_never_matches_via_stage2_paths_only():
    """A candidate that is a substantial, high-ratio suffix of a stage-2
    entry hierarchy path -- but of NO stage-1a title -- must still be
    reported: prefix/suffix matching only ever looks at `known` (round-2
    F2). Ratio 30/59 = 0.51, comfortably past the length-ratio guard, so a
    silent match here can only be explained by matching against `paths`."""
    stage2 = {"entries": [{
        "hierarchy": ["COMMITTEE MEMBERSHIP FOR THE REGIONAL EXAMPLE AGENCY BOARD"]}]}
    found = lint_missed_headers(
        ["REGIONAL EXAMPLE AGENCY BOARD"], {"hierarchy": []}, stage2)
    assert len(found) == 1
    assert "REGIONAL EXAMPLE AGENCY BOARD" in found[0]["message"]


def test_missed_headers_low_ratio_suffix_of_a_known_title_still_fires():
    """web200 (batch-3): a standalone bold label short enough to clear the
    absolute-length guard, but far shorter than the unrelated known title it
    happens to trail, must still be reported -- the F2 length-ratio guard
    (MIN_KEY_TO_TITLE_RATIO), not just the known/paths restriction, is what
    keeps this a real finding (synthetic values; ratio 16/44 = 0.36)."""
    stage1a = {"hierarchy": [{
        "text": "EXPERIENCE IN WORKING WITH REGIONAL COUNCIL", "children": []}]}
    found = lint_missed_headers(["REGIONAL COUNCIL"], stage1a, {"entries": []})
    assert len(found) == 1
    assert "REGIONAL COUNCIL" in found[0]["message"]


def test_artifact_resolution_does_not_steal_a_longer_uids_files(tmp_path):
    """uid 'web05' must not resolve to 'web050_entries.json'.

    glob(f"{uid}*") is a prefix match and '0' sorts before '_', so the longer
    uid won: the 2026-07-15 sweep doctored web04/web05/web06 against
    web049/web050/web060.
    """
    from unified_pipeline.run_doctor import _find_artifact, _find_source

    d = tmp_path / "stage_2_entry_extraction"
    d.mkdir()
    (d / "web050_entries.json").write_text("{}")   # decoy: longer uid, sorts first
    (d / "web05_entries.json").write_text("{}")

    found = _find_artifact(tmp_path, "web05", "stage_2")
    assert found is not None and found.name == "web05_entries.json"

    # the longer uid still resolves to its own file
    found = _find_artifact(tmp_path, "web050", "stage_2")
    assert found is not None and found.name == "web050_entries.json"

    # and a uid with no artifact of its own gets nothing, not a neighbour's
    assert _find_artifact(tmp_path, "web0", "stage_2") is None

    (tmp_path / "web050.docx").write_text("x")
    (tmp_path / "web05.docx").write_text("x")
    assert _find_source(tmp_path, "web05").name == "web05.docx"


def test_artifacts_are_named_tuples_readable_by_attribute():
    """_ARTIFACTS values are ArtifactSpec, not bare positional tuples -- so a
    call site can read .stage_dir/.suffix by name instead of by position."""
    from unified_pipeline.run_doctor import _ARTIFACTS, ArtifactSpec

    for spec in _ARTIFACTS.values():
        assert isinstance(spec, ArtifactSpec)
        assert spec.stage_dir == spec[0]
        assert spec.suffix == spec[1]


def test_uid_owns_requires_a_real_boundary_suffix():
    """The ownership guard must reject the degenerate cases as well as the
    prefix collision: an empty uid owns nothing, and a name that IS the uid
    (no separator at all) is not owned."""
    from unified_pipeline.run_doctor import _uid_owns

    assert _uid_owns("web05_entries.json", "web05") is True
    assert _uid_owns("web05.docx", "web05") is True
    assert _uid_owns("web050_entries.json", "web05") is False   # prefix collision
    assert _uid_owns("web05", "web05") is False                 # no suffix boundary
    assert _uid_owns("web05_entries.json", "") is False         # empty uid owns nothing


def _para(text, style="Heading 1"):
    """Minimal stand-in for the python-docx paragraph iter_header_candidates sees."""
    class _S:  # noqa: D401
        name = style
    class _R:
        def __init__(self, t): self.text, self.bold = t, True
    class _P:
        def __init__(self, t): self.text, self.style, self.runs = t, _S(), [_R(t)]
    return _P(text)


def test_candidate_filter_rejects_tab_data_rows_and_person_lines(monkeypatch):
    """Header candidates must exclude data rows and the owner's name line.

    2026-07-15 corpus: these were 7 of the 25 residual missed_headers findings.
    """
    import unified_pipeline.run_doctor as D

    rejected = [
        "Active\t\t\tMaryland",                          # licensure data row
        "Certification:\t\t\tAmerican Board of Surgery",  # label<TAB>value
        "STANLEY J. SZEFLER, M.D.",                       # owner name line
        "LEE W. SHOCKLEY, MD, MBA, FACEP, FAAEM, CPE",
        "AMY NICHOLE MERTENS, D.O.",
        "CURRICULUM VITAE – JEFFREY R OLSEN, MD",         # CV title + name
    ]
    kept = [
        "ADMINISTRATIVE APPOINTMENTS, SCHOOL OF MEDICINE, CU:",  # comma, real header
        "PROFESSIONAL SOCIETIES:",
        "TEACHING",
    ]

    for text in rejected:
        assert D._NAME_CREDENTIAL_RE.search(text) or "\t" in text, text
    for text in kept:
        assert not D._NAME_CREDENTIAL_RE.search(text), text
        assert "\t" not in text


# ------------------------------------------------------------------ #438
# Severity means "unusual", not "present"; display order means "informative",
# not "numerous". The safety property under all of this: a finding is never
# suppressed. Only whether it escalates the RUN's verdict changes.


def test_classified_unrendered_severity_tracks_total_entries_lost():
    """One lost entry is the corpus norm; several means content vanished."""
    def blocks_missing_everything():
        return [("p", "nothing here matches")]

    def stage3b(n):
        return {"entries": [
            _entry(f"Distinctive unrendered entry number {i} about widgets",
                   taxonomy_code="B1", start=i) for i in range(n)]}

    few = lint_classified_unrendered(stage3b(1), blocks_missing_everything())
    many = lint_classified_unrendered(
        stage3b(CLASSIFIED_UNRENDERED_WARN_ENTRIES + 1),
        blocks_missing_everything())
    assert few and many, "the lint must still fire in both cases"
    assert few[0]["severity"] == "INFO"
    assert many[0]["severity"] == "WARN"


def test_table_shape_stays_info_regardless_of_how_malformed_the_table_is():
    """#816 retired table_shape's own magnitude threshold: a couple of bad
    rows in a long table and half a short table both stay INFO now -- the
    malformed-row count is a `metrics` value (honors_malformed_rows/
    honors_rows), not a per-run severity signal."""
    header = ["Name of Award", "Granting Organization", "Date Awarded"]

    def tbl(bad, total):
        rows = [header]
        for i in range(bad):
            rows.append([f"Prize {i} awarded in 2019. It was given for work.",
                         "NY", ""])
        for i in range(total - bad):
            rows.append([f"Clean Award {i}", "Some University", "2020"])
        return [rows]

    long_mild = lint_table_shape(tbl(1, 40))
    short_bad = lint_table_shape(tbl(3, 4))
    assert long_mild and short_bad, "the lint must still fire in both cases"
    assert long_mild[0]["severity"] == "INFO", "1/40 malformed rows is not a WARN"
    assert short_bad[0]["severity"] == "INFO", "3/4 malformed rows is INFO too now"


def test_rare_lints_outrank_ubiquitous_ones_however_often_they_fire():
    """The #438 core: most_common() let the 88% lints crowd out the 1% ones."""
    counts = {"output_hygiene": 40, "table_shape": 12, "duplicate_passages": 1}
    assert [k for k, _ in rank_lints(counts)] == [
        "duplicate_passages", "table_shape", "output_hygiene"]
    assert lint_surprise("duplicate_passages") > lint_surprise("output_hygiene")


def test_an_unknown_lint_is_treated_as_maximally_surprising():
    """A newly added lint must surface, not hide, before it is measured."""
    assert lint_surprise("a_brand_new_lint") >= lint_surprise("duplicate_passages")
    assert rank_lints({"a_brand_new_lint": 1, "output_hygiene": 99})[0][0] == \
        "a_brand_new_lint"


def test_magnitude_thresholds_never_suppress_a_finding():
    """Severity may drop to INFO; the finding, message and evidence stay."""
    header = ["Name of Award", "Granting Organization", "Date Awarded"]
    rows = [header] + [["Prize awarded in 2019. Given for work.", "NY", ""]] \
        + [[f"Clean {i}", "Some University", "2020"] for i in range(39)]
    findings = lint_table_shape([rows])
    assert len(findings) == 1
    assert findings[0]["severity"] == "INFO"
    assert "malformed" in findings[0]["message"]
    assert findings[0]["evidence"], "evidence must survive the downgrade"


_RENDER_OVERLAP_NAMES = (
    "RENDER_TOKEN_MIN_COUNT", "RENDER_TOKEN_OVERLAP", "_RENDER_TOKEN_RE",
    "RENDER_PIECE_MIN_CHARS", "RENDER_PIECE_WINDOW", "_entry_pieces",
)

# Every doctor module that binds one of the six names above (#825 round 2):
# `shared.py` is the definition site, the other three are consumers that
# import it. A module-level copy in ANY of these would shadow the import
# silently -- mutant m07 (K-825 verifier round 1) proved this for
# `lints/render.py` specifically.
_RENDER_OVERLAP_MODULES = (
    "unified_pipeline.doctor.shared",
    "unified_pipeline.doctor.lints.render",
    "unified_pipeline.doctor.lints.extraction",
    "unified_pipeline.run_doctor",
)


def _module_level_import_bindings(
        tree: ast.Module, package: str | None) -> tuple[dict[str, str], set[str]]:
    """AST-level module bindings: name -> the fully-resolved dotted module it
    was imported from, and the set of names bound some other way (a
    module-level assignment or def, however it is spelled or nested), which
    would shadow an import of the same name.

    An explicit stack, not `tree.body` alone (#825 verifier round 2, mutants
    m04/m10/m11): a plain `for node in tree.body` walk only sees a bare
    `NAME = value` / `def NAME` sitting directly at module scope, so it missed
    an annotated assignment (`NAME: int = 3`), a tuple-unpacking assignment
    (`A, B = 1, 2`), and any assignment nested in a module-level `if`/`try`/
    `with`/`for`/`while` block -- all still execute at import time and still
    shadow the import. This descends into every such block but never into a
    `def`/`class` body (the def/class name itself is recorded as a binding;
    what it assigns internally is a local, not a module attribute)."""
    imported: dict[str, str] = {}
    other_bindings: set[str] = set()
    stack: list[ast.AST] = list(tree.body)
    while stack:
        node = stack.pop()
        if isinstance(node, ast.ImportFrom):
            resolved = importlib.util.resolve_name(
                "." * node.level + (node.module or ""), package)
            for alias in node.names:
                imported[alias.asname or alias.name] = resolved
        elif isinstance(node, ast.Import):
            continue
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            other_bindings.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            other_bindings.add(node.id)
        else:
            stack.extend(ast.iter_child_nodes(node))
    return imported, other_bindings


def test_doctor_render_overlap_names_are_render_check_objects_not_copies():
    """#825: `doctor/shared.py` imports these from `stage6/render_check.py`
    rather than keeping parallel copies.

    Runtime `is` alone is not a reliable guard here: CPython interns small
    ints globally (-5..256), so a reintroduced `RENDER_TOKEN_MIN_COUNT = 3`
    (or `RENDER_PIECE_MIN_CHARS = 15` / `RENDER_PIECE_WINDOW = 40`) would
    still be `is` its render_check twin by accident of the int cache, not
    because it was actually imported -- verified in the ticket by mutating
    each name to a local assignment and observing `is` stay True for the
    three small-int constants (only the float `RENDER_TOKEN_OVERLAP` and the
    two object identities are caught by `is` alone). So this test also reads
    `doctor/shared.py`'s own AST: every one of these names must be bound by
    the `from unified_pipeline.stage6.render_check import (...)` statement,
    and by nothing else at module level. A reintroduced copy -- of ANY of
    the six names, int-valued or not -- fails the AST half even when the
    int cache would have hidden it from the runtime half alone."""
    from unified_pipeline.doctor import shared as doctor_shared
    from unified_pipeline.stage6 import render_check

    # Runtime half: catches non-interned reintroductions (regex/function
    # objects, and non-cached numeric literals) directly.
    for name in _RENDER_OVERLAP_NAMES:
        assert getattr(doctor_shared, name) is getattr(render_check, name), (
            f"doctor.shared.{name} is a copy, not the render_check object")

    # Static half: catches EVERY reintroduction, including the small-int
    # ones the int cache would otherwise hide from the runtime half.
    tree = ast.parse(Path(doctor_shared.__file__).read_text(encoding="utf-8"))
    imported, other_bindings = _module_level_import_bindings(
        tree, doctor_shared.__package__)

    for name in _RENDER_OVERLAP_NAMES:
        assert imported.get(name) == "unified_pipeline.stage6.render_check", (
            f"{name} is not imported from stage6.render_check in "
            f"doctor/shared.py's AST")
        assert name not in other_bindings, (
            f"{name} is ALSO bound by a module-level assignment or def in "
            f"doctor/shared.py -- that binding shadows the import")


def test_doctor_render_overlap_names_are_render_check_objects_not_copies_in_consumers():
    """#825 round 2 (K-825 verifier r1, finding 1): the test above guards
    `doctor/shared.py`'s own module namespace only. `doctor/shared.py`
    re-exports these names so `lints/render.py`, `lints/extraction.py` and
    `run_doctor.py` can import them from it -- but any of those three could
    instead bind a module-level copy of the same name directly, and nothing
    would notice: each module's own attribute would just shadow whatever it
    re-exported. Proved live: adding `RENDER_TOKEN_MIN_COUNT = 3` /
    `RENDER_TOKEN_OVERLAP = 0.7` directly below `lints/render.py`'s `from
    ..shared import (...)` block left `doctor/shared.py` untouched, passed
    the test above, and passed all 371 doctor tests (mutant m07).

    Same two halves as above, generalized over every module that binds any
    of the six names: runtime `is` against `render_check`, plus an AST check
    that each name is bound ONLY by an `ImportFrom` resolving to
    `doctor/shared.py` or `stage6/render_check.py` -- never a module-level
    assignment or def."""
    from unified_pipeline.stage6 import render_check

    allowed_sources = {
        "unified_pipeline.doctor.shared",
        "unified_pipeline.stage6.render_check",
    }
    checked_at_least_one = False
    for module_name in _RENDER_OVERLAP_MODULES:
        module = importlib.import_module(module_name)
        names_here = [n for n in _RENDER_OVERLAP_NAMES if hasattr(module, n)]
        if not names_here:
            continue
        checked_at_least_one = True

        for name in names_here:
            assert getattr(module, name) is getattr(render_check, name), (
                f"{module_name}.{name} is a copy, not the render_check object")

        tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
        imported, other_bindings = _module_level_import_bindings(
            tree, module.__package__)
        for name in names_here:
            assert name not in other_bindings, (
                f"{module_name}.{name} is ALSO bound by a module-level "
                f"assignment or def -- that binding would shadow the import")
            assert imported.get(name) in allowed_sources, (
                f"{module_name}.{name} is imported from "
                f"{imported.get(name)!r}, not from shared.py or "
                f"render_check.py")

    assert checked_at_least_one, (
        "none of _RENDER_OVERLAP_MODULES binds any of the six names -- "
        "the module list or the name list has drifted")


def test_render_overlap_modules_list_matches_a_source_scan():
    """#825 round 3, verifier note 2 (mutant m08): _RENDER_OVERLAP_MODULES is
    a hand-kept list, so dropping a module from it silently narrows the
    consumer guard above -- the missing module's own copy would never be
    checked. Scan every `doctor/**/*.py` plus `run_doctor.py` for a module
    whose AST imports one of the six names, and assert the hand list is
    exactly that scan's result."""
    import unified_pipeline.doctor as doctor_pkg
    from unified_pipeline import run_doctor as run_doctor_mod

    doctor_root = Path(doctor_pkg.__file__).parent
    src_root = doctor_root.parent.parent
    found: set[str] = set()
    for path in (*doctor_root.rglob("*.py"), Path(run_doctor_mod.__file__)):
        tree = ast.parse(path.read_text())
        if not any(isinstance(n, ast.ImportFrom)
                   and any(a.name in _RENDER_OVERLAP_NAMES for a in n.names)
                   for n in ast.walk(tree)):
            continue
        parts = list(path.resolve().relative_to(src_root).with_suffix("").parts)
        found.add(".".join(parts[:-1] if parts[-1] == "__init__" else parts))

    assert found == set(_RENDER_OVERLAP_MODULES), (
        f"source scan found {sorted(found)}, _RENDER_OVERLAP_MODULES has "
        f"{sorted(_RENDER_OVERLAP_MODULES)} -- update the hand list")


# ------------------------------------------ lint 14r: junk_or_header_row

def _junk4(text, code, idx, **fields):
    """One stage-4 entry with invented content and the given fields."""
    return _entry(text, start=idx, hierarchy=["Example Heading"],
                  taxonomy_code=code, extracted_fields=fields)


def _junk_hits(entries, table_rows, blocks=None):
    findings = lint_junk_or_header_row({"entries": entries}, table_rows, blocks or [])
    return [(f["severity"], f["message"].split(":")[0], f["message"].split(": ")[1])
            for f in findings]


def test_junk_or_header_row_warns_on_an_institution_header_rendered_as_a_row():
    """EBYSBC E8: a course list's institution line printed as a course."""
    entries = [_junk4("Example State University", "K1", 40,
                      institution="Example State University"),
               _junk4("EX101 Widget Studies", "K1", 41, course_title="EX101 Widget Studies")]
    rows = [[["Example State University", ""], ["EX101 Widget Studies", ""]]]
    assert _junk_hits(entries, rows) == [("WARN", "entry 40 (K1)", "header_only")]


def test_junk_or_header_row_reads_a_bulleted_paragraph():
    entries = [_junk4("Example State University", "P", 7,
                      institution="Example State University")]
    blocks = [("p", "INSTITUTIONAL ADMINISTRATIVE ACTIVITIES"),
              ("p", "\u2022 Example State University")]
    assert _junk_hits(entries, [], blocks) == [("WARN", "entry 7 (P)", "header_only")]


def test_junk_or_header_row_quotes_the_undated_header_row_not_the_dated_record():
    """QTATUP 1252/1253/1261: a society header renders as its own undated
    row, after a dated membership row that shows the same name and one
    extra word. The evidence quotes the header's row, not the record's."""
    entries = [_junk4("Example Widget Society", "Q2", 1252,
                      organization="Example Widget Society")]
    rows = [[["Member, Example Widget Society", "1992-Present"]],
            [["Example Widget Society", "Member"]]]
    findings = lint_junk_or_header_row({"entries": entries}, rows, [])
    assert [f["evidence"] for f in findings] == [["Example Widget Society | Member"]]


@pytest.mark.parametrize("text, fields", [
    ("Example Medical School (2009-2014)", {"institution": "Example Medical School"}),
    ("Example Medical School", {"institution": "Example Medical School",
                                "start_date": "2009", "end_date": "2014"}),
], ids=["year_in_text", "year_in_fields"])
def test_junk_or_header_row_quotes_a_dated_row_for_a_dated_header(text, fields):
    """A dated teaching header's own row shows its years, whether the year
    is in its text or its date fields; an undated row with the same name
    before it is not that row."""
    entries = [_junk4(text, "K4", 5, **fields)]
    rows = [[["Example Medical School"]], [["2009-2014 - Example Medical School"]]]
    findings = lint_junk_or_header_row({"entries": entries}, rows, [])
    assert [f["evidence"] for f in findings] == [["2009-2014 - Example Medical School"]]


def test_junk_or_header_row_falls_back_to_a_row_whose_year_disagrees():
    """No row's year agrees with the entry's: the first match is still the
    evidence, so the finding count does not depend on the year."""
    entries = [_junk4("Example Widget Society", "Q2", 1261,
                      organization="Example Widget Society")]
    rows = [[["Example Widget Society", "1991-Present"]]]
    findings = lint_junk_or_header_row({"entries": entries}, rows, [])
    assert [f["evidence"] for f in findings] == [["Example Widget Society | 1991-Present"]]


def test_junk_or_header_row_reads_a_cut_row_only_for_a_label():
    """Only a label's row may lack one of its words: a header's row that
    lacks one is another line, not the header."""
    entries = [_junk4("Example Widget Society", "Q2", 1253,
                      organization="Example Widget Society")]
    assert _junk_hits(entries, [], [("p", "Widget Society:")]) == []


def test_junk_or_header_row_does_not_read_a_section_heading_as_a_row():
    entries = [_junk4("Research", "P", 7, institution="Research")]
    assert _junk_hits(entries, [], [("p", "RESEARCH")]) == []


@pytest.mark.parametrize("code", ["I", "B1", "F1", "T", "A", "S", "C1"])
def test_junk_or_header_row_skips_codes_whose_record_is_an_organization(code):
    """A membership, a school, a licensing state: the organization IS the record."""
    entries = [_junk4("Example Widget Society", code, 3,
                      organization="Example Widget Society")]
    assert _junk_hits(entries, [[["Example Widget Society"]]]) == []


def test_junk_or_header_row_reads_a_dated_header_only_under_teaching():
    """'2009-2014 <institution>' heads a course list (K), but outside
    teaching it is how a CV lists a board seat, rendered as the CV says."""
    fields = {"institution": "Example Medical School", "start_date": "2009",
              "end_date": "2014"}
    teaching = [_junk4("Example Medical School (2009-2014)", "K4", 5, **fields)]
    board = [_junk4("2009-2014 Example Medical School", "Q2", 5, **fields)]
    rows = [[["2009-2014 - Example Medical School"]]]
    assert _junk_hits(teaching, rows) == [("WARN", "entry 5 (K4)", "header_only")]
    assert _junk_hits(board, rows) == []


def test_junk_or_header_row_spares_a_dated_session_at_a_site():
    """One dated day at a place is a session given there, not a header."""
    entries = [_junk4("5/14/97 Example Community Center", "K4", 9,
                      institution="Example Community Center", date="1997-05-14")]
    assert _junk_hits(entries, [[["1997 - Example Community Center"]]]) == []


def test_junk_or_header_row_spares_a_row_that_shows_a_record():
    """The row carries words of its own beyond the institution: a title the
    renderer took from the entry text, so the row is a record."""
    entries = [_junk4("Example Medical School\tModels of Widget Wear and Tear", "K2",
                      11, institution="Example Medical School")]
    rows = [[["Models of Widget Wear and Tear (Example Medical School)"]]]
    assert _junk_hits(entries, rows) == []


def test_junk_or_header_row_allows_no_extra_word_on_a_dated_header():
    fields = {"institution": "Example Heart Society", "start_date": "2017",
              "end_date": "2017"}
    entries = [_junk4("2017 Attendee, Example Heart Society", "K4", 12, **fields)]
    assert _junk_hits(entries, [[["2017 - Attendee, Example Heart Society"]]]) == []
    assert _junk_hits(entries, [[["2017 - Example Heart Society"]]]) == [
        ("WARN", "entry 12 (K4)", "header_only")]


def test_junk_or_header_row_warns_on_a_lead_in_label():
    """EBYSBC E8: a role label above a mentee list printed as a teaching item."""
    entries = [_junk4("Major Widget Advisor:", "K2", 577, teaching_role="Major Widget Advisor")]
    assert _junk_hits(entries, [], [("p", "Major Widget Advisor")]) == [
        ("WARN", "entry 577 (K2)", "label")]


def test_junk_or_header_row_reads_a_long_line_ending_in_a_colon_as_a_record():
    text = ("Developed and taught the following graduate widget courses in the "
            "Department of Example Studies:")
    entries = [_junk4(text, "K1", 20, course_title="graduate widget courses")]
    assert _junk_hits(entries, [], [("p", text)]) == []


def test_junk_or_header_row_warns_on_a_date_fragment_rendered_as_an_award():
    """EBYSBC E29: a wrapped line's tail number printed as an Honors row."""
    entries = [_junk4("47.", "H", 93), _junk4("1983- 1984.", "H", 98,
                                              start_date="1983", end_date="1984")]
    rows = [[["47.", "", ""], ["1983-1984", "", ""]]]
    assert _junk_hits(entries, rows) == [("WARN", "entry 93 (H)", "date_fragment"),
                                         ("WARN", "entry 98 (H)", "date_fragment")]


def test_junk_or_header_row_reads_an_open_end_as_part_of_a_fragment():
    entries = [_junk4("1997-present", "H", 106, start_date="1997", end_date="present")]
    assert _junk_hits(entries, [[["1997-Present"]]]) == [
        ("WARN", "entry 106 (H)", "date_fragment")]


def test_junk_or_header_row_needs_an_institution_to_read_a_header():
    entries = [_junk4("2004-2006 Example City", "K4", 14, location="Example City",
                      start_date="2004", end_date="2006")]
    assert _junk_hits(entries, [[["2004-2006 - Example City"]]]) == []


def test_junk_or_header_row_needs_the_fragment_alone_on_its_row():
    entries = [_junk4("1997-", "H", 105, start_date="1997", end_date="present")]
    assert _junk_hits(entries, [[["Prize", "1997-Present"]]]) == []
    assert _junk_hits(entries, [[["1997-Present"]]]) == [
        ("WARN", "entry 105 (H)", "date_fragment")]


def _rank(text, idx, title, institution, start=None, end=None):
    fields = {"title": title, "institution": institution}
    if start:
        fields.update(start_date=start, end_date=end)
    return _junk4(text, "D1", idx, **fields)


_DATED_RANK = _rank("2010-present Professor of Widgetry, Example University", 30,
                    "Professor of Widgetry", "Example University", "2010", "present")
_EARLIER_RANK = _rank("2004-2010 Assistant Professor, Example University", 25,
                      "Assistant Professor", "Example University", "2004", "2010")


def test_junk_or_header_row_warns_on_the_banner_title_repeated_without_a_date():
    """EBYSBC E10: the CV banner's current title coded D1 renders as a second,
    dateless appointment row."""
    banner = _rank("Current position: Professor of Widgetry", 4,
                   "Professor of Widgetry", "Example University")
    rows = [[["Professor of Widgetry", "Example University", "2010-Present"],
             ["Professor of Widgetry", "Example University", ""]]]
    assert _junk_hits([banner, _EARLIER_RANK, _DATED_RANK], rows) == [
        ("WARN", "entry 4 (D1)", "undated_duplicate")]


def test_junk_or_header_row_reads_each_part_of_a_joined_banner_title():
    banner = _rank("Example Banner", 0, "Chief of Widgets; Professor of Widgetry",
                   "Example University")
    rows = [[["Chief of Widgets; Professor of Widgetry", "Example University", ""]]]
    assert _junk_hits([banner, _DATED_RANK], rows) == [
        ("WARN", "entry 0 (D1)", "undated_duplicate")]


def test_junk_or_header_row_spares_an_undated_rank_inside_the_dated_list():
    """An undated row between dated appointments lost its date; it is no banner."""
    lost_date = _rank("Professor of Widgetry, Example University", 27,
                      "Professor of Widgetry", "Example University")
    rows = [[["Professor of Widgetry", "Example University", ""]]]
    assert _junk_hits([_EARLIER_RANK, lost_date, _DATED_RANK], rows) == []


def test_junk_or_header_row_spares_another_rank_of_the_same_title():
    """'Assistant Professor' beside a dated 'Clinical Assistant Professor' is
    a second post (EBYSBC E4), not a repeat."""
    clinical = _rank("1990-1995 Clinical Assistant Professor, Example University", 30,
                     "Clinical Assistant Professor", "Example University", "1990", "1995")
    undated = _rank("Assistant Professor, Example University", 40,
                    "Assistant Professor", "Example University")
    rows = [[["Assistant Professor", "Example University", ""]]]
    assert _junk_hits([clinical, undated], rows) == []


def test_junk_or_header_row_needs_the_duplicate_row_to_carry_no_year():
    banner = _rank("Professor of Widgetry", 4, "Professor of Widgetry",
                   "Example University")
    rows = [[["Professor of Widgetry", "Example University", "2010-Present"]]]
    assert _junk_hits([banner, _DATED_RANK], rows) == []


def test_junk_or_header_row_quotes_the_rendered_row_once_per_cell():
    """The evidence is the row as rendered, a repeated cell shown once."""
    entries = [_junk4("Example Widget Institute", "K1", 40,
                      institution="Example Widget Institute")]
    findings = lint_junk_or_header_row(
        {"entries": entries},
        [[["Example Widget Institute", "Example Widget Institute", ""]]], [])
    assert [f["evidence"] for f in findings] == [["Example Widget Institute"]]


def test_junk_or_header_row_needs_every_core_word_on_the_row():
    """A short row of other words is some other record, not this header."""
    entries = [_junk4("Example Widget Institute", "K1", 40,
                      institution="Example Widget Institute")]
    assert _junk_hits(entries, [[["Gadget College"]]]) == []


def test_junk_or_header_row_reads_an_empty_field_as_absent():
    """Stage 4 often emits a named field with no value: an empty title does
    not make the header a record."""
    entries = [_junk4("Example Widget Institute", "K1", 40, title="", course_title=None,
                      institution="Example Widget Institute", stage4_records=[{"x": 1}])]
    assert _junk_hits(entries, [[["Example Widget Institute"]]]) == [
        ("WARN", "entry 40 (K1)", "header_only")]


def test_junk_or_header_row_allows_two_extra_words_on_an_undated_header_not_three():
    """Numbers and the entry's own place are not extra words."""
    entries = [_junk4("Example Widget Institute, Townsville", "K1", 40,
                      institution="Example Widget Institute", location="Townsville")]
    two = [[["Example Widget Institute, Townsville 1997 Alpha Beta"]]]
    three = [[["Example Widget Institute, Townsville Alpha Beta Gamma"]]]
    assert _junk_hits(entries, two) == [("WARN", "entry 40 (K1)", "header_only")]
    assert _junk_hits(entries, three) == []


def test_junk_or_header_row_reads_only_body_paragraph_blocks():
    """A table block's text is read through table_rows, never as a paragraph."""
    entries = [_junk4("Example Widget Institute", "P", 7,
                      institution="Example Widget Institute")]
    assert _junk_hits(entries, [], [("table", "Example Widget Institute")]) == []


def test_junk_or_header_row_reads_a_three_letter_word_as_no_fragment():
    """'May 1997' is a dated record's text; '1983 to 1984' is a fragment."""
    entries = [_junk4("May 1997", "H", 50), _junk4("1983 to 1984", "H", 51)]
    rows = [[["May 1997"], ["1983 to 1984"]]]
    assert _junk_hits(entries, rows) == [("WARN", "entry 51 (H)", "date_fragment")]


def test_junk_or_header_row_reads_a_banner_title_against_the_dated_institution():
    """The banner joins the title and the institution in one field; a dated
    D1's title plus its institution carries both."""
    banner = _rank("Example Banner", 4, "Professor of Widgetry, Example University",
                   "")
    rows = [[["Professor of Widgetry, Example University", "Example Heading", ""]]]
    assert _junk_hits([banner, _DATED_RANK], rows) == [
        ("WARN", "entry 4 (D1)", "undated_duplicate")]


def test_junk_or_header_row_reads_undated_duplicates_against_d1_only():
    """A dated D2 of the same title is not an appointment the banner repeats,
    and an undated D2 is not read as a banner."""
    dated_d2 = _junk4("2010-present Professor of Widgetry", "D2", 30,
                      title="Professor of Widgetry", institution="Example University",
                      start_date="2010", end_date="present")
    banner = _rank("Example Banner", 4, "Professor of Widgetry", "Example University")
    undated_d2 = _junk4("Professor of Widgetry", "D2", 4, title="Professor of Widgetry",
                        institution="Example University")
    rows = [[["Professor of Widgetry", "Example University", ""]]]
    assert _junk_hits([banner, dated_d2], rows) == []
    assert _junk_hits([undated_d2, _DATED_RANK], rows) == []


def test_junk_or_header_row_reads_a_duplicate_only_on_a_multi_cell_row():
    """A one-cell line with the title is a paragraph-style mention, and a
    title split across two cells is no title cell."""
    banner = _rank("Example Banner", 4, "Professor of Widgetry", "Example University")
    assert _junk_hits([banner, _DATED_RANK], [[["Professor of Widgetry"]]]) == []
    assert _junk_hits([banner, _DATED_RANK],
                      [[["Professor of", "Widgetry", "Example University"]]]) == []


_DUTY = ("1. Administration of the widget program, including the review of new "
         "widget requests and the training of junior staff at the example clinic.")


def test_junk_or_header_row_warns_on_a_duty_sentence_rendered_as_its_role():
    """RCBKFG GKAQHB 79/84: two duty sentences rendered as paragraphs that
    read only the role stage 4 named; each entry takes its own paragraph."""
    entries = [_junk4(_DUTY, "L3", 79, leadership_role="Administrator",
                      institution="Example Clinic"),
               _junk4(_DUTY.replace("1.", "2."), "L3", 84, leadership_role="Administrator",
                      institution="Example Clinic")]
    blocks = [("p", "Administrator"), ("p", "Administrator")]
    assert _junk_hits(entries, [], blocks) == [
        ("WARN", "entry 79 (L3)", "role_only"), ("WARN", "entry 84 (L3)", "role_only")]
    # One paragraph serves one entry only.
    assert _junk_hits(entries, [], blocks[:1]) == [("WARN", "entry 79 (L3)", "role_only")]


@pytest.mark.parametrize("entry, rows, blocks", [
    # the paragraph shows more than the role
    (_junk4(_DUTY, "L3", 79, leadership_role="Administrator"), [],
     [("p", "Administrator, Example Clinic")]),
    # a table row with more than the role
    (_junk4(_DUTY, "L3", 79, leadership_role="Administrator"),
     [[["Administrator", "Example Clinic"]]], []),
    # three lowercase words is a short line, not a sentence
    (_junk4("Administrator ran two Widget clinics", "L3", 79,
            leadership_role="Administrator"), [], [("p", "Administrator")]),
    # more capitalised words than lowercase ones is a name, not a sentence
    (_junk4("Administrator for the Example Widget Clinic and the Sample Sprocket Program",
            "L3", 79, leadership_role="Administrator"), [], [("p", "Administrator")]),
    # a dated entry is a record of its own
    (_junk4(_DUTY, "L3", 79, leadership_role="Administrator", start_date="2015"), [],
     [("p", "Administrator")]),
    # a launch date dates it too (126-run corpus web40 150, 154: an L2
    # project's name and year sit on the line above its role sentence)
    (_junk4(_DUTY, "L2", 150, role="Administrator", project_name="Widget Service",
            launch_date="2012"), [], [("p", "Administrator")]),
    # a short capitalised line, not a sentence
    (_junk4("Administrator, Example Widget Clinic Program", "L3", 79,
            leadership_role="Administrator"), [], [("p", "Administrator")]),
    # the role is the whole sentence (EBYSBC JFBPNC 79)
    (_junk4("Provided instruction with hands-on training, sessions, and teaching to "
            "all clinical faculty", "K4", 79,
            role="Provided instruction with hands-on training, sessions, and teaching "
                 "to all clinical faculty"), [],
     [("p", "Provided instruction with hands-on training, sessions, and teaching to "
            "all clinical faculty")]),
    # a work's own title is not a role
    (_junk4(_DUTY, "R", 79, title="Administrator"), [], [("p", "Administrator")]),
])
def test_junk_or_header_row_spares_a_role_paragraph_that_is_a_record(entry, rows, blocks):
    assert _junk_hits([entry], rows, blocks) == []


def test_junk_or_header_row_warns_on_a_sentence_with_no_name_as_a_record():
    """RCBKFG GKAQHB 94: stage 4 found only a description, and the sentence
    fills the committee row's name cell."""
    text = "1. Collaborated with example colleagues for widget studies and outreach."
    entry = _junk4(text, "P", 94, description=text[3:])
    assert _junk_hits([entry], [[[text, "", ""]]]) == [
        ("WARN", "entry 94 (P)", "description_only")]
    named = _junk4(text, "P", 94, description=text[3:], committee_name="Widget Committee")
    assert _junk_hits([named], [[[text, "Widget Committee"]]]) == []
    narrative = _junk4(text, "M1", 94, narrative=text[3:])
    assert _junk_hits([narrative], [[[text]]]) == []


def test_junk_or_header_row_warns_on_a_lead_in_label_less_its_first_word():
    """RCBKFG JJUQDF 326: the reviewer lead-in renders as a journal row with
    its first word cut. A row that is not itself a label, or lacks two of
    the label's words, is not it."""
    text = "Reviewer for the following journals and committees:"
    entry = _junk4(text, "Q4D", 326)
    assert _junk_hits([entry], [[["for the following journals and committees:", ""]]]) == [
        ("WARN", "entry 326 (Q4D)", "label")]
    assert _junk_hits([entry], [[["for the following journals and committees", ""]]]) == []
    assert _junk_hits([entry], [[["the following journals and committees:", ""]]]) == []
    assert _junk_hits([entry], [[["for the following journals and committees, books:", ""]]]) == []
    assert _junk_hits([_junk4("Reviewers:", "Q4D", 5)], [[[":"]]]) == []


def test_run_doctor_wires_junk_or_header_row_with_the_rendered_paragraphs(tmp_path):
    """The LINT_REGISTRY row must hand the lint the docx's body paragraphs
    (`blocks`, optional): a header bulleted as a paragraph is seen only there.
    Invented values."""
    root = _build_clean_run(tmp_path)
    fields = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    data = json.loads(fields.read_text())
    data["entries"].append({
        "taxonomy_code": "P", "element_type": "paragraph",
        "element_idx_start": 96, "text": "Example Widget Institute",
        "extracted_fields": {"institution": "Example Widget Institute"}})
    fields.write_text(json.dumps(data))
    docx_path = root / "stage_6_wcm_documents" / f"{_UID}_cv_wcm.docx"
    output = Document(str(docx_path))
    output.add_paragraph("Example Widget Institute")
    output.save(str(docx_path))

    payload = run_doctor(root, _UID)

    hits = [f for f in payload["findings"] if f["lint"] == "junk_or_header_row"]
    assert [(f["severity"], f["evidence"]) for f in hits] == [
        ("WARN", ["Example Widget Institute"])]


# ------------------------------------------ lint 14y: fanout_cell_residue

_LEADERSHIP_HEADER = ["Organization", "Role (i.e., officer, secretary, chair, etc.)",
                      "Dates (yyyy-yyyy)"]
_COMMITTEE_HEADER = ["Name of Committee", "Role (i.e., member, fellow, etc.)",
                     "Organization (Institution/Location)", "Dates (yyyy-yyyy)"]


def _fanout4(text, idx, records, code="Q1"):
    """A stage-4 entry stage 4 split into `records` (#1406). Invented values;
    the entry's own fields are its last record's, as stage 4 writes them."""
    return _entry(text, start=idx, hierarchy=["Example Heading"], taxonomy_code=code,
                  extracted_fields={**records[-1], "stage4_records": records})


def _office(role, start, end, organization=None):
    return {"role": role, "organization": organization,
            "start_date": start, "end_date": end}


def _fanout_hits(entries, tables):
    from unified_pipeline.run_doctor import lint_fanout_cell_residue
    findings = lint_fanout_cell_residue({"entries": entries}, tables)
    return [(f["severity"], f["message"].split(": ")[0], f["message"].split(": ")[1],
             f["evidence"]) for f in findings]


_KEEPER = _fanout4("Widget Keeper, 1992-1993, 1994-1996, 1997-1998", 82, [
    _office("Widget Keeper", "1992", "1993"), _office("Widget Keeper", "1994", "1996"),
    _office("Widget Keeper", "1997", "1998")])


def test_fanout_cell_residue_warns_on_the_other_terms_years_in_the_organization_cell():
    """EOAHMI DUTAVD-01 (entry 82): the last term's row printed the other two
    terms as its organization."""
    rows = [["", "Widget Keeper", "1992-1993"], ["", "Widget Keeper", "1994-1996"],
            ["1992-1993, 1994-1996", "Widget Keeper", "1997-1998"]]
    assert _fanout_hits([_KEEPER], [[_LEADERSHIP_HEADER, *rows]]) == [
        ("WARN", "entry 82 (Q1)", "sibling_year",
         [TABLE_ROW_JOINER.join(["1992-1993, 1994-1996", "Widget Keeper", "1997-1998"])])]


def test_fanout_cell_residue_spares_the_rows_stage_6_now_renders():
    """#1449's render of the same entry: every organization cell empty."""
    rows = [["", "Widget Keeper", "1992-1993"], ["", "Widget Keeper", "1994-1996"],
            ["", "Widget Keeper", "1997-1998"]]
    assert _fanout_hits([_KEEPER], [[_LEADERSHIP_HEADER, *rows]]) == []


def test_fanout_cell_residue_warns_on_a_year_stub_and_the_sibling_role():
    """EOAHMI WYMVGU-01 (entries 931-935): the second of two year-keyed roles
    printed the first role and the tail of '1992-93' as its organization."""
    entry = _fanout4("1992-93 Vice-Chair, widget reviewer", 932, [
        _office("Vice-Chair", "1992", "1993"), _office("Widget reviewer", "1992", "1993")])
    rows = [["", "Vice-Chair", "1992-1993"], ["93 Vice-Chair", "Widget reviewer", "1992-1993"]]
    assert _fanout_hits([entry], [[_LEADERSHIP_HEADER, *rows]]) == [
        ("WARN", "entry 932 (Q1)", "sibling_value, year_stub",
         [TABLE_ROW_JOINER.join(["93 Vice-Chair", "Widget reviewer", "1992-1993"])])]


def test_fanout_cell_residue_warns_on_a_bare_range_word():
    entry = _fanout4("Widget Keeper, 1990 to 1994; Gadget Lead, 1995 to 1996", 40, [
        _office("Widget Keeper", "1990", "1994"), _office("Gadget Lead", "1995", "1996")])
    rows = [["to", "Widget Keeper", "1990-1994"], ["", "Gadget Lead", "1995-1996"]]
    assert _fanout_hits([entry], [[_LEADERSHIP_HEADER, *rows]]) == [
        ("WARN", "entry 40 (Q1)", "bare_to",
         [TABLE_ROW_JOINER.join(["to", "Widget Keeper", "1990-1994"])])]


def test_fanout_cell_residue_reads_a_range_word_at_the_end_of_a_cell():
    entry = _fanout4("Widget Keeper, Example Guild 1990 to 1994; Gadget Lead, 1995", 41, [
        _office("Widget Keeper", "1990", "1994"), _office("Gadget Lead", "1995", "1995")])
    rows = [["Example Guild to", "Widget Keeper", "1990-1994"]]
    assert _fanout_hits([entry], [[_LEADERSHIP_HEADER, *rows]])[0][2] == "bare_to"


def test_fanout_cell_residue_spares_to_when_the_record_names_it():
    entry = _fanout4("Widget Keeper, Example Guild to Widgets 1990 to 1994; Gadget Lead, 1995",
                     42, [_office("Widget Keeper", "1990", "1994", "Example Guild to Widgets"),
                          _office("Gadget Lead", "1995", "1995")])
    rows = [["Example Guild to", "Widget Keeper", "1990-1994"]]
    assert _fanout_hits([entry], [[_LEADERSHIP_HEADER, *rows]]) == []


def test_fanout_cell_residue_warns_on_a_built_record_line_in_one_cell():
    """EOAHMI DUTAVD-01 (entry 79): an earlier record's built line, role and
    years joined by ' | ', printed as its committee name."""
    entry = _fanout4("Widget Steward, 1981, 1990-1994", 79, [
        _office("Widget Steward", "1981", "1981"), _office("Widget Steward", "1990", "1994")],
        code="Q2")
    rows = [["Widget Steward | 1981 | 1981", "Widget Steward", "", "1981"]]
    assert _fanout_hits([entry], [[_COMMITTEE_HEADER, *rows]]) == [
        ("WARN", "entry 79 (Q2)", "built_line",
         [TABLE_ROW_JOINER.join(["Widget Steward | 1981 | 1981", "Widget Steward", "1981"])])]


def test_fanout_cell_residue_spares_a_cell_that_is_another_records_value():
    """A record with no role renders the section's default role, which another
    record holds (EOAHMI JBUVYV 316): no leftover text."""
    entry = _fanout4("Example Board: Widget Committee 2017-present, Gadget Committee, "
                     "member 2016-present", 316, [
                         {"committee_name": "Widget Committee", "role": None,
                          "start_date": "2017", "end_date": "present"},
                         {"committee_name": "Gadget Committee", "role": "Member",
                          "start_date": "2016", "end_date": "present"}], code="Q2")
    rows = [["Widget Committee", "Member", "", "2017-Present"],
            ["Gadget Committee", "Member", "", "2016-Present"]]
    assert _fanout_hits([entry], [[_COMMITTEE_HEADER, *rows]]) == []


def test_fanout_cell_residue_spares_the_records_own_years_in_its_name_cell():
    """A renderer that prints a record's whole line in its name cell does so
    for every entry (EBYSBC MQSUIC 611): not fan-out residue."""
    entry = _fanout4("Widget Coordinator, Example Department; 2003-2008; 2019-now", 611, [
        _office("Widget Coordinator", "2003", "2008"),
        _office("Widget Coordinator", "2019", "present")])
    rows = [["Widget Coordinator, Example Department; 2003-2008", "Widget Coordinator",
             "2003-2008"]]
    assert _fanout_hits([entry], [[_LEADERSHIP_HEADER, *rows]]) == []


def test_fanout_cell_residue_reads_only_cells_made_of_the_entrys_text():
    """A cell with a word the parent's text lacks was filled from elsewhere
    (stage 5b's city, a template default), not left over."""
    rows = [["Example City 1992-1993, 1994-1996", "Widget Keeper", "1997-1998"]]
    assert _fanout_hits([_KEEPER], [[_LEADERSHIP_HEADER, *rows]]) == []


def test_fanout_cell_residue_skips_a_row_two_records_match_equally():
    """Two records with one year: a row showing both roles is neither's."""
    entry = _fanout4("Widget Keeper, Gadget Lead 2001, Example Guild", 70, [
        _office("Widget Keeper", "2001", "2001"), _office("Gadget Lead", "2001", "2001")])
    rows = [["Widget Keeper", "Gadget Lead", "2001"]]
    assert _fanout_hits([entry], [[_LEADERSHIP_HEADER, *rows]]) == []


def test_fanout_cell_residue_skips_a_committee_row_both_records_score_equally():
    """The tie clause itself: both roles score the same on a row whose third
    cell holds both, so the row belongs to neither record and is not residue."""
    entry = _fanout4("Widget Keeper, Gadget Lead 2001, Example Guild", 70, [
        _office("Widget Keeper", "2001", "2001"), _office("Gadget Lead", "2001", "2001")])
    rows = [["Gadget Lead", "Widget Keeper", "Widget Keeper Gadget Lead", "2001"]]
    assert _fanout_hits([entry], [[_COMMITTEE_HEADER, *rows]]) == []


def test_fanout_cell_residue_breaks_a_tie_on_the_rows_dates():
    """One role for every term: the date cell names the record."""
    rows = [["1994-1996, 1997-1998", "Widget Keeper", "1992-1993"]]
    assert _fanout_hits([_KEEPER], [[_LEADERSHIP_HEADER, *rows]])[0][:3] == (
        "WARN", "entry 82 (Q1)", "sibling_year")


def test_fanout_cell_residue_needs_the_row_dated_as_its_record():
    """A row whose date cell holds none of the record's years is another
    entry's row with the same role."""
    entry = _fanout4("Widget Keeper, 1992-1993; Gadget Lead, 1994-1996", 83, [
        _office("Widget Keeper", "1992", "1993"), _office("Gadget Lead", "1994", "1996")])
    rows = [["1994-1996", "Widget Keeper", "2010-2011"]]
    assert _fanout_hits([entry], [[_LEADERSHIP_HEADER, *rows]]) == []


def test_fanout_cell_residue_reads_only_tables_with_a_date_column():
    rows = [["1992-1993, 1994-1996", "Widget Keeper", "1997-1998"]]
    header = ["Organization", "Role", "Notes"]
    assert _fanout_hits([_KEEPER], [[header, *rows]]) == []


def test_fanout_cell_residue_reads_only_split_entries():
    one = _entry("Widget Keeper, 1992-1993, 1997-1998", start=5, taxonomy_code="Q1",
                 extracted_fields={**_office("Widget Keeper", "1997", "1998"),
                                   "stage4_records": [_office("Widget Keeper", "1997", "1998")]})
    rows = [["1992-1993", "Widget Keeper", "1997-1998"]]
    assert _fanout_hits([one], [[_LEADERSHIP_HEADER, *rows]]) == []


def test_fanout_cell_residue_quotes_at_most_three_rows():
    from unified_pipeline.doctor.lints.render import FANOUT_RESIDUE_EVIDENCE_LIMIT
    terms = [("1990", "1991"), ("1992", "1993"), ("1994", "1995"), ("1996", "1997"),
             ("1998", "1999")]
    entry = _fanout4("Widget Keeper, " + ", ".join(f"{a}-{b}" for a, b in terms), 90,
                     [_office("Widget Keeper", a, b) for a, b in terms])
    rows = [[f"{terms[(i + 1) % 5][0]}-{terms[(i + 1) % 5][1]}", "Widget Keeper", f"{a}-{b}"]
            for i, (a, b) in enumerate(terms)]
    hits = _fanout_hits([entry], [[_LEADERSHIP_HEADER, *rows]])
    assert len(hits) == 1
    assert len(hits[0][3]) == FANOUT_RESIDUE_EVIDENCE_LIMIT == 3


def test_run_doctor_reports_fanout_cell_residue(tmp_path):
    """The registry row hands the lint stage 4 and the docx's tables.
    Invented values."""
    root = _build_clean_run(tmp_path)
    fields = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    data = json.loads(fields.read_text())
    data["entries"].append(_KEEPER)
    fields.write_text(json.dumps(data))
    docx_path = root / "stage_6_wcm_documents" / f"{_UID}_cv_wcm.docx"
    output = Document(str(docx_path))
    rows = [_LEADERSHIP_HEADER, ["1992-1993, 1994-1996", "Widget Keeper", "1997-1998"]]
    table = output.add_table(rows=len(rows), cols=3)
    for row, cells in zip(table.rows, rows):
        for cell, text in zip(row.cells, cells):
            cell.paragraphs[0].text = text
    output.save(str(docx_path))

    payload = run_doctor(root, _UID)

    hits = [f for f in payload["findings"] if f["lint"] == "fanout_cell_residue"]
    assert [(f["severity"], f["message"].split(":")[0]) for f in hits] == [
        ("WARN", "entry 82 (Q1)")]


def test_fanout_cell_residue_prevalence_is_the_measured_fraction():
    """Measured 2026-10-05 (FAN-RES) over the 102 fresh renders of origin/dev
    8b287ec2; a new measurement updates both sides."""
    from unified_pipeline.run_doctor import LINT_PREVALENCE
    assert LINT_PREVALENCE["fanout_cell_residue"] == round(1 / 102, 3)


def test_fanout_cell_residue_names_the_cell_in_its_message():
    from unified_pipeline.run_doctor import lint_fanout_cell_residue
    rows = [["1992-1993, 1994-1996", "Widget Keeper", "1997-1998"]]
    [finding] = lint_fanout_cell_residue({"entries": [_KEEPER]}, [[_LEADERSHIP_HEADER, *rows]])
    assert finding["message"] == (
        "entry 82 (Q1): sibling_year: a record split from this entry prints the entry's "
        "leftover text in a name, organization or committee cell")


def test_fanout_cell_residue_reads_a_three_letter_sibling_role():
    entry = _fanout4("Board CEO 2001-2003, widget reviewer 2001-2003", 12, [
        _office("CEO", "2001", "2003"), _office("Widget reviewer", "2001", "2003")])
    rows = [["Board CEO", "Widget reviewer", "2001-2003"]]
    assert _fanout_hits([entry], [[_LEADERSHIP_HEADER, *rows]])[0][2] == "sibling_value"


def test_fanout_cell_residue_reads_a_cell_holding_the_whole_parent_line():
    """EOAHMI BRUSUZ-01's shape: the last record's cell is the parent's whole text."""
    entry = _fanout4("Widget Keeper 1992-1993, Gadget Lead 1994", 13, [
        _office("Widget Keeper", "1992", "1993"), _office("Gadget Lead", "1994", "1994")])
    rows = [["Widget Keeper 1992-1993, Gadget Lead 1994", "Gadget Lead", "1994"]]
    assert _fanout_hits([entry], [[_LEADERSHIP_HEADER, *rows]])[0][2] == (
        "sibling_value, sibling_year")


def test_fanout_cell_residue_spares_a_tied_row_and_a_lone_own_year():
    """A row whose dates break no tie is no record's; a cell holding only its
    record's own year is no built line. An empty table is skipped."""
    rows = [["1994-1996", "Widget Keeper", ""]]
    assert _fanout_hits([_KEEPER], [[], [_LEADERSHIP_HEADER, *rows]]) == []
    entry = _fanout4("Widget Steward, 1981, 1990-1994", 79, [
        _office("Widget Steward", "1981", "1981"), _office("Widget Steward", "1990", "1994")],
        code="Q2")
    assert _fanout_hits([entry], [[_COMMITTEE_HEADER,
                                   ["1981", "Widget Steward", "", "1981"]]]) == []


def test_fanout_cell_residue_skips_records_that_are_not_objects():
    entry = _entry("Widget Keeper, 1992-1993, 1994-1996", start=14, taxonomy_code="Q1",
                   extracted_fields={"stage4_records": [
                       "Widget Keeper 1992-1993", _office("Widget Keeper", "1994", "1996")]})
    rows = [["1992-1993", "Widget Keeper", "1994-1996"]]
    assert _fanout_hits([entry], [[_LEADERSHIP_HEADER, *rows]]) == []


# identical_rendered_rows (EOAHMI QTATUP-04, BRUSUZ-01). Invented values.
_IDR_TITLE = "Example Lecture on Widgets"
_IDR_PLACE = "Example City, ST"
_IDR_HIDES = "the row hides what tells them apart"


def _idr_entry(idx, fields, code="R", text="Example entry text"):
    return {"taxonomy_code": code, "element_idx_start": idx, "text": text,
            "extracted_fields": fields}


def _idr_talk(idx, date, **extra):
    return _idr_entry(idx, {"title": _IDR_TITLE, "location": _IDR_PLACE, "date": date, **extra})


def test_identical_rendered_rows_warns_when_the_year_column_hides_the_dates():
    """Three talks months apart render as one year-only row three times. A
    merged cell repeats in python-docx's row; it is one cell of the row."""
    rows = [[["Title", "Institution/Location", "Dates (yyyy)"],
             [_IDR_TITLE, _IDR_PLACE, "2003"],
             [_IDR_TITLE, _IDR_TITLE, _IDR_PLACE, "2003"],
             [_IDR_TITLE, _IDR_PLACE, "2003"]]]
    entries = [_idr_talk(10, "2003-03", notes="spring"), _idr_talk(12, "2003-11", notes="fall"),
               _idr_talk(14, "2003-12", notes="winter")]

    assert lint_identical_rendered_rows({"entries": entries}, rows) == [{
        "lint": "identical_rendered_rows", "severity": "WARN",
        "message": "entry 10 (R): distinct_records: 3 identical rows in one table stand "
                   "for 3 stage-4 records (also entries 12, 14) that differ in date, notes; "
                   + _IDR_HIDES,
        "evidence": [f"{_IDR_TITLE} | {_IDR_PLACE} | 2003",
                     "entry 10: date=2003-03; notes=spring",
                     "entry 12: date=2003-11; notes=fall",
                     "entry 14: date=2003-12; notes=winter"],
        "status": "ran", "reason": ""}]


def test_identical_rendered_rows_matches_a_record_whose_every_word_shows():
    """A record whose values all render (title and year) still stands for
    the row: its words equal the row's."""
    entries = [_idr_entry(10, {"title": "Example Widgets", "date": "2003"}),
               _idr_entry(12, {"title": "Example Widgets", "date": "2003-05"})]
    rows = [[["Example Widgets", "2003"]] * 2]
    [finding] = lint_identical_rendered_rows({"entries": entries}, rows)
    assert finding["evidence"][1:] == ["entry 10: date=2003", "entry 12: date=2003-05"]


def test_identical_rendered_rows_reads_each_record_of_a_split_entry():
    """QTATUP 980's shape: one entry whose stage4_records hold one talk on
    several dates; the evidence lists the first IDENTICAL_ROW_EVIDENCE_RECORDS."""
    dates = ["2003-07-30", "2003-09-08", "2003-10-02", "2003-12-03"]
    series = "Example lecture series"
    records = [{"title": _IDR_TITLE, "location": _IDR_PLACE, "event_name": series,
                "date": date} for date in dates]
    entry = _idr_entry(5, {**records[-1], "stage4_records": records})
    rows = [[[_IDR_TITLE, f"{series}, {_IDR_PLACE}", "2003"]] * len(dates)]

    [finding] = lint_identical_rendered_rows({"entries": [entry]}, rows)

    assert finding["message"] == (
        "entry 5 (R): distinct_records: 4 identical rows in one table stand for 4 "
        "stage-4 records that differ in date; " + _IDR_HIDES)
    assert finding["evidence"] == [f"{_IDR_TITLE} | {series}, {_IDR_PLACE} | 2003"] + [
        f"entry 5: date={date}" for date in dates[:3]]
    assert IDENTICAL_ROW_EVIDENCE_RECORDS == 3


def test_identical_rendered_rows_is_silent_when_the_records_agree():
    """The CV lists one record twice, and the document repeats it: a
    membership given once with an open end and once without, a talk whose
    second copy has no date, or two copies differing only in bookkeeping."""
    member = {"organization": "Example Society", "membership_type": "Member",
              "start_date": "2013"}
    society_rows = [[["Member, Example Society", "2013-Present"]] * 2]
    assert lint_identical_rendered_rows({"entries": [
        _idr_entry(20, {**member, "end_date": "present"}, code="I"),
        _idr_entry(21, {**member, "end_date": None}, code="I")]}, society_rows) == []

    talk_rows = [[[_IDR_TITLE, _IDR_PLACE, "2003"]] * 2]
    assert lint_identical_rendered_rows(
        {"entries": [_idr_talk(10, "2003-03"), _idr_talk(12, "")]}, talk_rows) == []
    copies = [_idr_talk(10, "2003-03", target_name=name, formatted_text=name,
                        formatting_source=name, formatted_citation=name)
              for name in ("Example A", "Example B")]
    assert lint_identical_rendered_rows({"entries": copies}, talk_rows) == []


def test_identical_rendered_rows_needs_two_identical_rows_of_two_words():
    """One row is not a repeat, a one-word row repeats by design, and a row
    no record's words hold is not judged."""
    entries = [_idr_talk(10, "2003-03"), _idr_talk(12, "2003-11")]
    one_row = [[[_IDR_TITLE, _IDR_PLACE, "2003"]]]
    assert lint_identical_rendered_rows({"entries": entries}, one_row) == []
    assert lint_identical_rendered_rows({"entries": entries}, [[["Widgets"], ["Widgets"]]]) == []
    two_words = lint_identical_rendered_rows(
        {"entries": entries}, [[["Example Widgets"], ["Example Widgets"]]])
    assert [f["message"].split(":")[0] for f in two_words] == ["entry 10 (R)"]
    assert lint_identical_rendered_rows(
        {"entries": entries}, [[["Other Lecture", "2003"]] * 2]) == []


def test_identical_rendered_rows_reads_an_open_end_stage_6_wrote():
    """'2013-Present' from a start date alone: no field holds 'present', so
    the row still stands for both records, which differ in their notes."""
    society = {"organization": "Example Society", "start_date": "2013"}
    entries = [_idr_entry(20, {**society, "notes": "first term"}, code="I"),
               _idr_entry(21, {**society, "notes": "second term"}, code="I")]

    [finding] = lint_identical_rendered_rows(
        {"entries": entries}, [[["Example Society", "2013-Present"]] * 2])

    assert finding["message"] == (
        "entry 20 (I): distinct_records: 2 identical rows in one table stand for 2 "
        "stage-4 records (also entries 21) that differ in notes; " + _IDR_HIDES)


_IDR_FIRST = {"event_name": "Example Widget Institute", "location": _IDR_PLACE}
_IDR_SECOND = {"event_name": "Second Example Symposium on Gadgets", "location": "Other Town, ST"}
_IDR_FUSED_TEXT = ("Example Widget Institute (Example City, ST)* "
                   "Second Example Symposium on Gadgets (Other Town, ST)")


def _idr_fused_entry(records=(_IDR_FIRST, _IDR_SECOND), text=_IDR_FUSED_TEXT):
    return _idr_entry(30, {**records[-1], "stage4_records": list(records)}, text=text)


def test_identical_rendered_rows_warns_on_a_record_also_rendered_inside_the_raw_text():
    """BRUSUZ 228/249/265 on the dev-248 render: record 1 renders as its own
    row, and the whole entry text renders as a second row naming it again.
    The record row quoted is the one nearest the fused row."""
    rows = [[["Example Widget Institute", _IDR_PLACE],
             ["Unrelated Talk", "Elsewhere"],
             ["Example Widget Institute"],
             [_IDR_FUSED_TEXT, "Other Town, ST"]]]

    assert lint_identical_rendered_rows({"entries": [_idr_fused_entry()]}, rows) == [{
        "lint": "identical_rendered_rows", "severity": "WARN",
        "message": "entry 30 (R): fused_repeat: the entry's whole text renders as one "
                   "row while one of its 2 records also renders as its own row",
        "evidence": ["Example Widget Institute", f"{_IDR_FUSED_TEXT} | Other Town, ST"],
        "status": "ran", "reason": ""}]


def test_identical_rendered_rows_fused_needs_a_split_entry_and_its_record_row():
    entry = _idr_fused_entry()
    fused_row = [_IDR_FUSED_TEXT, "Other Town, ST"]
    record_row = ["Example Widget Institute", _IDR_PLACE]
    # Only the fused row: the records fused, none repeated. Only record
    # rows: the entry fanned out cleanly.
    assert lint_identical_rendered_rows({"entries": [entry]}, [[fused_row]]) == []
    assert lint_identical_rendered_rows(
        {"entries": [entry]}, [[record_row, ["Second Example Symposium on Gadgets"]]]) == []
    # A one-word row, and a row the fused row does not hold, are not the record.
    assert lint_identical_rendered_rows(
        {"entries": [entry]}, [[["Institute"], fused_row]]) == []
    organizer = {**_IDR_FIRST, "role": "organizer"}
    assert lint_identical_rendered_rows(
        {"entries": [_idr_fused_entry((organizer, _IDR_SECOND))]},
        [[["organizer, Example Widget Institute"], fused_row]]) == []
    # One record, a records list holding one object, or no list at all.
    for records in ([_IDR_FIRST], [_IDR_FIRST, "not a record"], None):
        one = _idr_entry(30, {**_IDR_FIRST, "stage4_records": records}, text=_IDR_FUSED_TEXT)
        assert lint_identical_rendered_rows({"entries": [one]}, [[record_row, fused_row]]) == []


def test_identical_rendered_rows_fused_record_row_may_show_every_word():
    """A record row holding every word of its record, and every word of the
    fused row (the second record repeats the first one's name), is still a
    repeat."""
    name, place = "Widget Hall Annex Building", "Townville City"
    entry = _idr_fused_entry(({"event_name": name, "location": place}, {"event_name": name}),
                             text=f"{name} ({place})* {name}")
    rows = [[[name, place], [f"{name} ({place})* {name}"]]]
    [finding] = lint_identical_rendered_rows({"entries": [entry]}, rows)
    assert finding["evidence"] == [f"{name} | {place}", f"{name} ({place})* {name}"]


def test_identical_rendered_rows_fused_entry_text_floor():
    """An entry text shorter than IDENTICAL_ROW_FUSED_MIN_CHARS (20 letters
    and digits) can match a row by chance, so it is not judged. A two-word
    record row is enough."""
    assert IDENTICAL_ROW_FUSED_MIN_CHARS == 20
    head = "Widget Hall (Town)* "
    first = {"event_name": "Widget Hall", "location": "Town"}
    for tail, fires in (("Q" * 6, True), ("Q" * 5, False)):
        entry = _idr_fused_entry((first, {"event_name": tail}), text=head + tail)
        rows = [[["Widget Hall"], [head + tail]]]
        assert bool(lint_identical_rendered_rows({"entries": [entry]}, rows)) is fires


def test_run_doctor_wires_identical_rendered_rows_to_stage_4_and_the_table_rows(tmp_path):
    """The LINT_REGISTRY row hands the lint stage 4 and the docx's table
    rows. Invented values."""
    root = _build_clean_run(tmp_path)
    fields = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    data = json.loads(fields.read_text())
    data["entries"] += [_idr_talk(910, "2003-03"), _idr_talk(912, "2003-11")]
    fields.write_text(json.dumps(data))
    docx_path = root / "stage_6_wcm_documents" / f"{_UID}_cv_wcm.docx"
    output = Document(str(docx_path))
    table = output.add_table(rows=2, cols=3)
    for row in table.rows:
        for cell, text in zip(row.cells, (_IDR_TITLE, _IDR_PLACE, "2003")):
            cell.paragraphs[0].text = text
    output.save(str(docx_path))

    payload = run_doctor(root, _UID)

    hits = [f for f in payload["findings"] if f["lint"] == "identical_rendered_rows"]
    assert [(f["severity"], f["message"].split(":")[0]) for f in hits] == [
        ("WARN", "entry 910 (R)")]


def test_identical_rendered_rows_prevalence_is_the_measured_fraction():
    """Measured 2026-10-05 (IDR in doctor/PRECISION.md): 8 of the 102 fresh
    renders of origin/dev 8b287ec2; a new measurement updates both sides."""
    from unified_pipeline.run_doctor import LINT_PREVALENCE
    assert LINT_PREVALENCE["identical_rendered_rows"] == round(8 / 102, 3)


# --------------------------------------- lint 14aa: split_child_unsourced
# EOAHMI recheck DUTAVD-03 (a split record's blank institution filled from a
# neighbouring entry) and WYMVGU-02 (an entry's leading range copied onto
# split records it does not date). Invented values throughout.

_SPLIT_TRAINING_TEXT = ("Resident, Widget Surgery:\tExample Hospital, Springfield (1970-1971) "
                        "Resident, Gadget Surgery:")
_SPLIT_TRAINING = [
    {"training_type": "Resident", "specialty": "Widget Surgery",
     "institution": "Example Hospital, Springfield", "start_date": "1970", "end_date": "1971"},
    {"training_type": "Resident", "specialty": "Gadget Surgery",
     "institution": None, "start_date": None, "end_date": None},
]
_SPLIT_BOARD_TEXT = ("1990-2004  Member, Board, Example Clinic\t1996-97 Vice-President\t"
                     "1997-99 President\tPast-President")


def _split_board(past_end="2004"):
    """Four records of one board line; Past-President carries the board term."""
    org = {"organization": "Example Clinic"}
    return [{"role": "Member, Board", "start_date": "1990", "end_date": "2004", **org},
            {"role": "Vice-President", "start_date": "1996", "end_date": "1997", **org},
            {"role": "President", "start_date": "1997", "end_date": "1999", **org},
            {"role": "Past-President", "start_date": "1990", "end_date": past_end, **org}]


def _split_board_rows(past_dates="1990-2004"):
    return [[["Example Clinic", "Member, Board", "1990-2004"],
             ["Example Clinic", "Vice-President", "1996-1997"],
             ["Example Clinic", "President", "1997-1999"],
             ["Example Clinic", "Past-President", past_dates]]]


def _split_hits(text, records, table_rows=None, code="Q1", idx=30):
    entry = _entry(text, start=idx, hierarchy=["Example Heading"], taxonomy_code=code,
                   extracted_fields={**records[-1], "stage4_records": records})
    return lint_split_child_unsourced({"entries": [entry]}, table_rows)


def test_split_child_unsourced_flags_a_place_from_outside_the_entry():
    rows = [[["Resident, Widget Surgery", "Example Hospital, Springfield", "1970-1971"],
             ["Resident, Gadget Surgery", "Other County Hospital, Shelbyville", ""]]]
    findings = _split_hits(_SPLIT_TRAINING_TEXT, _SPLIT_TRAINING, rows, code="C")
    assert [(f["lint"], f["severity"], f["evidence"]) for f in findings] == [
        ("split_child_unsourced", "INFO",
         ["Resident, Gadget Surgery | Other County Hospital, Shelbyville"])]
    assert findings[0]["message"].startswith("entry 30 (C): a split record with no institution")
    assert findings[0]["message"].endswith("(institution_from_outside_entry)")


@pytest.mark.parametrize("place", [
    "Example Hospital, Springfield",   # the sibling's place, which the entry names
    "Member",                          # one word: the role stage 6 supplies
    "Other Hospital",                  # one word the entry lacks
])
def test_split_child_unsourced_spares_a_place_the_entry_holds_or_a_single_word(place):
    rows = [[["Resident, Gadget Surgery", place, ""]]]
    assert _split_hits(_SPLIT_TRAINING_TEXT, _SPLIT_TRAINING, rows, code="C") == []


def test_split_child_unsourced_reads_the_place_against_two_foreign_words():
    rows = [[["Resident, Gadget Surgery", "Other Lakeside Hospital", ""]]]
    assert len(_split_hits(_SPLIT_TRAINING_TEXT, _SPLIT_TRAINING, rows, code="C")) == 1


def test_split_child_unsourced_spares_a_place_when_no_sibling_names_one():
    """An entry under an employer heading takes its place from the heading."""
    records = [{**record, "institution": None} for record in _SPLIT_TRAINING]
    rows = [[["Resident, Gadget Surgery", "Other County Hospital, Shelbyville", ""]]]
    assert _split_hits(_SPLIT_TRAINING_TEXT, records, rows, code="C") == []


@pytest.mark.parametrize("row", [
    ["Resident, Gadget Surgery", "Other County Hospital, Shelbyville", "1980-1981"],
    ["Resident, Gadget Surgery, Senior", "Other County Hospital, Shelbyville", ""],
    ["Resident, Gadget Surgery", "Other County Hospital 1980", ""],
])
def test_split_child_unsourced_needs_the_records_own_row(row):
    """The row is the record's: its name alone in one cell, its years (none
    here), and a place cell that carries no year."""
    assert _split_hits(_SPLIT_TRAINING_TEXT, _SPLIT_TRAINING, [[row]], code="C") == []


def test_split_child_unsourced_reads_no_place_without_the_docx():
    assert _split_hits(_SPLIT_TRAINING_TEXT, _SPLIT_TRAINING, None, code="C") == []


def test_split_child_unsourced_flags_a_range_copied_onto_an_undated_record():
    findings = _split_hits(_SPLIT_BOARD_TEXT, _split_board(), _split_board_rows(), idx=865)
    assert [(f["severity"], f["message"], f["evidence"]) for f in findings] == [
        ("INFO", "entry 865 (Q1): 2 split records show 1990-2004, which the entry writes "
                 "fewer times, beside a record with its own dates (date_from_sibling)",
         ["Example Clinic | Member, Board | 1990-2004",
          "Example Clinic | Past-President | 1990-2004"])]


def test_split_child_unsourced_spares_a_range_stage_6_does_not_render_twice():
    assert _split_hits(_SPLIT_BOARD_TEXT, _split_board(), _split_board_rows("")) == []


def test_split_child_unsourced_reads_stage_4_alone_without_the_docx():
    findings = _split_hits(_SPLIT_BOARD_TEXT, _split_board())
    assert [(f["message"].split(":")[0], f["evidence"]) for f in findings] == [
        ("entry 30 (Q1)", [_SPLIT_BOARD_TEXT])]
    assert "2 split records show 1990-2004" in findings[0]["message"]


@pytest.mark.parametrize("text, records", [
    # one leading range over a list: no record has a range of its own
    ("2020-present Example Mentee One; Example Mentee Two; Example Mentee Three",
     [{"mentee_name": name, "start_date": "2020", "end_date": "present"}
      for name in ("Example Mentee One", "Example Mentee Two", "Example Mentee Three")]),
    # the range is written as often as it is carried
    ("Widget Panel 2001-2003\tGadget Panel 2001-2003\tSprocket Panel 2006-2007",
     [{"committee_name": name, "start_date": start, "end_date": end}
      for name, start, end in (("Widget Panel", "2001", "2003"), ("Gadget Panel", "2001", "2003"),
                               ("Sprocket Panel", "2006", "2007"))]),
    # the other record's range starts inside the shared one
    ("Facilitator 7/2015-7/2016\tWidget Talk\tGadget Talk\tSprocket Talk",
     [{"title": "Widget Talk", "start_date": "2015", "end_date": "2016"},
      {"title": "Gadget Talk", "start_date": "2015", "end_date": "2016"},
      {"title": "Sprocket Talk", "start_date": "2016"}]),
    # a sentence about the records
    ("Research support. In 2016 after I moved here I was awarded a widget grant, "
     "a gadget grant and a sprocket grant, and in 2018 another grant from the same funder",
     [{"title": "Widget grant", "start_date": "2016"},
      {"title": "Gadget grant", "start_date": "2016"},
      {"title": "Sprocket grant", "start_date": "2018"}]),
])
def test_split_child_unsourced_spares_a_range_the_entry_gives_every_record(text, records):
    assert _split_hits(text, records) == []


def test_split_child_unsourced_reads_an_open_end_as_part_of_the_range():
    """A singleton whose start is the shared range's end is inside it."""
    text = "2000-2006  Example Board\tDirector-elect\tDirector\t2006-07 Past-director"
    records = [{"role": "Director-elect", "start_date": "2000", "end_date": "2006"},
               {"role": "Director", "start_date": "2000", "end_date": "2006"},
               {"role": "Past-director", "start_date": "2006", "end_date": "2007"}]
    assert _split_hits(text, records) == []
    records[-1]["start_date"] = "2007"
    assert len(_split_hits(text, records)) == 1


def test_split_child_unsourced_needs_identity_words_to_find_a_row():
    """A record with nothing to name it matches no row, not a wordless cell."""
    records = [_SPLIT_TRAINING[0], {"institution": None, "start_date": None}]
    rows = [[["-", "Other County Hospital, Shelbyville", ""]]]
    assert _split_hits(_SPLIT_TRAINING_TEXT, records, rows, code="C") == []


def test_split_child_unsourced_reads_no_place_in_a_date_cell_or_the_name_cell():
    """A month-and-year cell is the record's dates, and the name cell is the
    record's own, however stage 4 worded it."""
    records = [_SPLIT_TRAINING[0],
               {"training_type": "Fellow", "specialty": "Sprocket Medicine",
                "institution": None, "start_date": "1972", "end_date": "1973"}]
    rows = [[["Fellow, Sprocket Medicine", "July 1972 - Sept 1973"]]]
    assert _split_hits(_SPLIT_TRAINING_TEXT, records, rows, code="C") == []


def test_split_child_unsourced_reports_every_entry():
    first = _entry(_SPLIT_BOARD_TEXT, start=1, taxonomy_code="Q1",
                   extracted_fields={"stage4_records": _split_board()})
    second = _entry(_SPLIT_BOARD_TEXT, start=2, taxonomy_code="Q1",
                    extracted_fields={"stage4_records": _split_board()})
    findings = lint_split_child_unsourced({"entries": [first, second]})
    assert [f["message"].split(":")[0] for f in findings] == ["entry 1 (Q1)", "entry 2 (Q1)"]


def test_split_child_unsourced_cuts_stage_4_evidence():
    text = _SPLIT_BOARD_TEXT + " " + "x" * 300
    [finding] = _split_hits(text, _split_board())
    assert finding["evidence"] == [text[:200]]


def test_split_child_unsourced_groups_open_ends_however_written():
    records = _split_board()
    records[0]["end_date"], records[-1]["end_date"] = "Present", " present"
    text = _SPLIT_BOARD_TEXT.replace("1990-2004", "1990-present")
    assert len(_split_hits(text, records)) == 1


def test_split_child_unsourced_reads_the_first_year_of_a_date():
    records = _split_board()
    records[-1]["start_date"] = "1990 (renewed 1995)"
    assert len(_split_hits(_SPLIT_BOARD_TEXT, records)) == 1


def test_split_child_unsourced_reads_past_a_record_of_its_own():
    """Records whose range is theirs alone come first; the shared one is
    still found, and a lone range the text never writes is not shared."""
    records = _split_board()
    records = records[1:3] + [records[0], records[3]]
    assert len(_split_hits(_SPLIT_BOARD_TEXT, records)) == 1
    alone = [{"role": "Widget Chair", "start_date": "1990", "end_date": "1991"},
             {"role": "Gadget Chair", "start_date": "1995"}]
    assert _split_hits("1990-91 Widget Chair\tGadget Chair", alone) == []


def test_split_child_unsourced_counts_each_record_once_on_the_render():
    """One carrier rendered twice is not two records showing the range."""
    rows = [[["Example Clinic", "Member, Board", "1990-2004"],
             ["Example Clinic", "Member, Board", "1990-2004"]]]
    assert _split_hits(_SPLIT_BOARD_TEXT, _split_board(), rows) == []


def test_split_child_unsourced_reads_every_shared_range_of_an_entry():
    """The first shared range does not render twice; the second does."""
    text = "2001-2003 Widget Panel\tGadget Panel\t2010-2012 Sprocket Panel\tCog Panel\t2015 Lever Panel"
    records = [{"committee_name": name, "start_date": start, "end_date": end}
               for name, start, end in (("Widget Panel", "2001", "2003"),
                                        ("Gadget Panel", "2001", "2003"),
                                        ("Sprocket Panel", "2010", "2012"),
                                        ("Cog Panel", "2010", "2012"),
                                        ("Lever Panel", "2015", ""))]
    rows = [[["Sprocket Panel", "2010-2012"], ["Cog Panel", "2010-2012"]]]
    findings = _split_hits(text, records, rows)
    assert [f["evidence"] for f in findings] == [["Sprocket Panel | 2010-2012",
                                                   "Cog Panel | 2010-2012"]]


def test_split_child_unsourced_prevalence_is_the_measured_rate():
    """SC-1 in doctor/PRECISION.md: 1 of 102 fresh renders of origin/dev."""
    from unified_pipeline.run_doctor import LINT_PREVALENCE
    assert LINT_PREVALENCE["split_child_unsourced"] == round(1 / 102, 3)


def test_split_child_unsourced_ignores_an_entry_stage_4_did_not_split():
    entry = _entry(_SPLIT_BOARD_TEXT, start=5, taxonomy_code="Q1",
                   extracted_fields={"stage4_records": _split_board()[:1], "role": "Member"})
    assert lint_split_child_unsourced({"entries": [entry]}, _split_board_rows()) == []
    entry["extracted_fields"] = {"stage4_records": "not a list"}
    assert lint_split_child_unsourced({"entries": [entry]}, _split_board_rows()) == []
    entry["extracted_fields"] = {"stage4_records": [_split_board()[0], "not a record"]}
    assert lint_split_child_unsourced({"entries": [entry]}, _split_board_rows()) == []


def test_run_doctor_wires_split_child_unsourced_with_the_rendered_tables(tmp_path):
    """The LINT_REGISTRY row must hand the lint the docx's table rows
    (`table_rows`, optional): the place shape is visible only there."""
    root = _build_clean_run(tmp_path)
    fields = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    data = json.loads(fields.read_text())
    data["entries"].append(_entry(
        _SPLIT_TRAINING_TEXT, start=96, taxonomy_code="C",
        extracted_fields={**_SPLIT_TRAINING[-1], "stage4_records": _SPLIT_TRAINING}))
    fields.write_text(json.dumps(data))
    docx_path = root / "stage_6_wcm_documents" / f"{_UID}_cv_wcm.docx"
    output = Document(str(docx_path))
    table = output.add_table(rows=1, cols=3)
    for cell, value in zip(table.rows[0].cells,
                           ("Resident, Gadget Surgery", "Other County Hospital, Shelbyville", "")):
        cell.text = value
    output.save(str(docx_path))

    payload = run_doctor(root, _UID)

    hits = [f for f in payload["findings"] if f["lint"] == "split_child_unsourced"]
    assert [(f["severity"], f["evidence"]) for f in hits] == [
        ("INFO", ["Resident, Gadget Surgery | Other County Hospital, Shelbyville"])]


# ---------------------------------------- lint 14ac: group_header_context
# X6 E8/E11: a group header whose context never reached the rows of the
# lines under it (KJJVVO-01, RINASX-06, RINASX-14, IEUPKK-14/-17/-18).
# Invented values throughout.

def _grp4(text, code, idx, end=None, heading="Example Heading", **fields):
    """One stage-4 entry with invented content and the given fields."""
    entry = _entry(text, start=idx, hierarchy=[heading], taxonomy_code=code,
                   extracted_fields=fields)
    if end is not None:
        entry["element_idx_end"] = end
    return entry


def _grp_hits(entries, table_rows, blocks=None):
    findings = lint_group_header_context({"entries": entries}, table_rows, blocks or [])
    return [(f["severity"], f["message"].split(":")[0], f["message"].split(": ")[1])
            for f in findings]


def _society_block():
    """A society line coded I, then a committee and an office under it,
    neither naming the society (KJJVVO 302-306)."""
    return [_grp4("Example Widget Society", "I", 10, organization="Example Widget Society"),
            _grp4("Gadget Council 1985-1994", "Q2", 11, committee_name="Gadget Council",
                  start_date="1985", end_date="1994"),
            _grp4("Chair 1988-1990", "Q1", 12, role="Chair",
                  start_date="1988", end_date="1990")]


_SOCIETY_ROWS = [[["Gadget Council", "Member", "1985-1994"], ["", "Chair", "1988-1990"]],
                 [["Example Widget Society", ""]]]


def test_group_header_context_warns_on_lines_that_lost_their_society():
    findings = lint_group_header_context({"entries": _society_block()}, _SOCIETY_ROWS, [])
    assert [(f["severity"], f["message"].split(": ")[1]) for f in findings] == [
        ("WARN", "children_lost_header")]
    assert findings[0]["message"] == (
        "entry 10 (I): children_lost_header: a group header's name (an organization or "
        "institution) is missing from the rows of the lines under it; 2 entries below it")
    assert findings[0]["evidence"] == ["entry 11 (Q2): Gadget Council | Member | 1985-1994",
                                       "entry 12 (Q1): Chair | 1988-1990"]


@pytest.mark.parametrize("shown, fires", [
    ("Example Widget Gizmo Society", False),    # all of the name
    ("Example Widget", False),                  # two of four words: half
    ("Example Group", True),                    # one of four: under half
], ids=["whole", "half", "under_half"])
def test_group_header_context_reads_the_name_at_half_its_words(shown, fires):
    entries = [_grp4("Example Widget Gizmo Society", "I", 10,
                     organization="Example Widget Gizmo Society"), _society_block()[1]]
    rows = [[["Gadget Council", shown, "1985-1994"]]]
    assert bool(_grp_hits(entries, rows)) is fires


def test_group_header_context_spares_a_child_any_of_whose_rows_shows_the_name():
    """ATUVAL 95: a short title also shows inside another entry's row, and
    the child's own row, not the tightest one, carries the header's name."""
    entries = [_grp4("Example State University", "D3", 20,
                     institution="Example State University"),
               _grp4("Assistant Professor", "D1", 21, title="Assistant Professor")]
    rows = [[["Clinical Assistant Professor", ""]],
            [["Assistant Professor", "Example State University, Widget Campus", ""]]]
    assert _grp_hits(entries, rows) == []


@pytest.mark.parametrize("child", [
    _grp4("Gadget Council 1985-1994", "Q2", 11, committee_name="Gadget Council",
          organization="Other Society", start_date="1985", end_date="1994"),
    _grp4("Gadget Award 1985", "H", 11, award_name="Gadget Award", date="1985"),
    _grp4("Gadget Council 1985-1994", "Q2", 11, heading="Other Heading",
          committee_name="Gadget Council", start_date="1985", end_date="1994"),
    _grp4("Gadget Council", "Q2", 11),
    _grp4("Other Society", "Q2", 11, organization="Other Society"),
], ids=["own_holder", "other_family", "other_heading", "no_fields", "next_header"])
def test_group_header_context_stops_at_a_line_that_is_no_child(child):
    entries = [_society_block()[0], child]
    rows = [[["Gadget Council", "Other Society", "1985-1994"], ["Gadget Award", "1985"]]]
    assert _grp_hits(entries, rows) == []


def test_group_header_context_reads_only_bare_roles_under_a_dated_header():
    """MUHLLD 82, 84: a dated society line is a membership of its own; the
    committee after it, in one flat membership list, may be another body's.
    An office under it (KJJVVO 320-324) is still its child."""
    header = _grp4("1976-1997 Example Widget Society", "I", 10,
                   organization="Example Widget Society", start_date="1976", end_date="1997")
    committee, office = _society_block()[1:]
    rows = [[["Gadget Council", "Member", "1985-1994"], ["", "Chair", "1988-1990"]]]
    assert [hit for hit in _grp_hits([header, committee, office], rows)
            if hit[2] == "children_lost_header"] == []
    assert _grp_hits([header, office], rows) == [
        ("WARN", "entry 10 (I)", "children_lost_header")]


@pytest.mark.parametrize("fields, fires", [
    ({"date": "1983-04-17"}, False),                        # a session on one day
    ({"start_date": "1979", "end_date": "1994"}, True),     # a span
], ids=["one_day", "span"])
def test_group_header_context_reads_a_dated_header_only_with_a_span(fields, fires):
    header = _grp4("Example Widget Society", "I", 10,
                   organization="Example Widget Society", **fields)
    office = _society_block()[2]
    rows = [[["", "Chair", "1988-1990"]]]
    shapes = [shape for _, _, shape in _grp_hits([header, office], rows)]
    # One dated day is a session of its own, so the office is a bare role
    # under it rather than its child.
    assert shapes == (["children_lost_header"] if fires else ["role_without_holder"])


@pytest.mark.parametrize("code", ["A", "S1", "T"])
def test_group_header_context_does_not_read_a_header_it_skips(code):
    entries = [_grp4("Example Widget Society", code, 10,
                     organization="Example Widget Society")] + _society_block()[1:2]
    assert [hit for hit in _grp_hits(entries, _SOCIETY_ROWS)
            if hit[2] == "children_lost_header"] == []


def _course_block():
    """A course line, then the owner's roles in it, each a bare role
    (the RINASX-06 shape)."""
    return [_grp4("Widget Course 1951-present", "K1", 140, course_title="Widget Course",
                  start_date="1951", end_date="present"),
            _grp4("Workshop Coordinator 1956-1958", "K3", 141, role="Workshop Coordinator",
                  start_date="1956", end_date="1958"),
            _grp4("Module Director 1960-1964, 1979-1992", "K3", 142, role="Module Director",
                  start_date="1960", end_date="1992", additional_periods="1960-1964; 1979-1992")]


_COURSE_BLOCKS = [("p", "1951-present - Widget Course"),
                  ("p", "1956-1958 - Workshop Coordinator"),
                  ("p", "1960-1964, 1979-1992 - Module Director")]


def test_group_header_context_warns_on_a_role_rendered_alone():
    findings = lint_group_header_context({"entries": _course_block()}, [], _COURSE_BLOCKS)
    assert [(f["severity"], f["message"].split(":")[0], f["message"].split(": ")[1])
            for f in findings] == [("WARN", "entry 140 (K1)", "role_without_holder")]
    assert findings[0]["message"] == (
        "entry 140 (K1): role_without_holder: a role renders alone, without the course, "
        "committee or society it was held in; 2 role lines")
    assert findings[0]["evidence"] == ["entry 141 (K3): 1956-1958 - Workshop Coordinator",
                                       "entry 142 (K3): 1960-1964, 1979-1992 - Module Director"]


@pytest.mark.parametrize("text, fields", [
    ("Chair, Search Committee 1988", {"role": "Chair, Search Committee", "start_date": "1988"}),
    ("Director of Widget Studies 1988", {"role": "Director of Widget Studies",
                                         "start_date": "1988"}),
    ("New Widget Gadget Award Reviewer 1988", {"role": "New Widget Gadget Award Reviewer",
                                               "start_date": "1988"}),
    ("Widget Session Review 1962-1964", {"teaching_role": "Widget Session Review",
                                              "start_date": "1962", "end_date": "1964"}),
    ("Workshop Coordinator for Widget Studies 1956", {"role": "Workshop Coordinator",
                                                      "start_date": "1956"}),
    ("Workshop Coordinator 1956", {"role": "Workshop Coordinator", "start_date": "1956",
                                  "course_title": "Widget Course"}),
], ids=["comma", "of", "four_other_words", "no_role_word", "more_text", "names_a_course"])
def test_group_header_context_spares_a_role_that_is_not_bare(text, fields):
    entries = [_course_block()[0], _grp4(text, "K3", 141, **fields)]
    blocks = [("p", f"1956 - {fields.get('role') or fields.get('teaching_role')}")]
    assert _grp_hits(entries, [], blocks) == []


@pytest.mark.parametrize("blocks", [
    [("p", "1956-1958 - Workshop Coordinator, Widget Course")],
    [],
], ids=["row_shows_the_course", "no_row"])
def test_group_header_context_needs_a_row_of_the_role_alone(blocks):
    assert _grp_hits(_course_block()[:2], [], blocks) == []


@pytest.mark.parametrize("above", [None, "other_heading"])
def test_group_header_context_needs_a_line_above_a_bare_role(above):
    role = _course_block()[1]
    entries = [role] if above is None else [
        _grp4("Widget Course 1951", "K1", 140, heading="Other Heading",
              course_title="Widget Course", start_date="1951"), role]
    assert _grp_hits(entries, [], _COURSE_BLOCKS) == []


def test_group_header_context_gives_each_role_its_own_row():
    """Two lines with one role claim two rows, not one row twice."""
    entries = [_course_block()[0],
               _grp4("Module Director 1960", "K3", 141, role="Module Director", start_date="1960"),
               _grp4("Module Director 1966", "K3", 142, role="Module Director", start_date="1966")]
    one_row = [("p", "1960 - Module Director")]
    two_rows = one_row + [("p", "1966 - Module Director")]
    assert lint_group_header_context({"entries": entries}, [], one_row)[0][
        "message"].endswith("; 1 role line")
    assert lint_group_header_context({"entries": entries}, [], two_rows)[0][
        "message"].endswith("; 2 role lines")


@pytest.mark.parametrize("above", [
    _grp4("1962-1964", "T", 140, start_date="1962", end_date="1964"),
    _grp4("Gizmo Committee member 1951-1960", "P", 140, committee_name="Gizmo Committee",
          role="Member", start_date="1951", end_date="1960"),
    _grp4("Gizmo Committee 1951-1960", "Q2", 140, committee_name="Gizmo Committee",
          start_date="1951", end_date="1960"),
    _grp4("Gizmo Panel Chair", "Q1", 140, organization="Gizmo Panel", role="Chair"),
    _grp4("1958; 1960", "T", 140, additional_dates="1958; 1960"),
    _grp4("Issued 1960", "T", 140, issue_date="1960"),
], ids=["date_only_line", "sibling_line", "dated_committee_line", "sibling_role_at_a_body",
        "second_span_only", "one_date_field_only"])
def test_group_header_context_names_the_role_not_the_line_above(above):
    """RGUNJV 2987, DTFNOR 31, DUTAVD 78: the line above a bare role may be
    a date alone, a sibling role line or a dated committee (a membership of
    its own), not what the role was held in, so the finding names the role
    itself, and quotes its row once."""
    role = _grp4("Widget Member 1962", "Q1", 141, role="Widget Member",
                 start_date="1962")
    findings = lint_group_header_context({"entries": [above, role]}, [],
                                         [("p", "1962 - Widget Member")])
    assert [f["message"].split(": ")[0] for f in findings] == ["entry 141 (Q1)"]
    assert findings[0]["evidence"] == ["entry 141 (Q1): 1962 - Widget Member"]


def test_group_header_context_names_an_undated_committee_as_the_holder():
    """An undated committee line is a heading over its offices."""
    above = _grp4("Gizmo Committee", "Q2", 140, committee_name="Gizmo Committee")
    role = _grp4("Widget Member 1962", "Q1", 141, role="Widget Member", start_date="1962")
    findings = lint_group_header_context({"entries": [above, role]}, [],
                                         [("p", "1962 - Widget Member")])
    assert [f["message"].split(": ")[0] for f in findings] == ["entry 140 (Q2)"]


def test_group_header_context_names_a_holder_and_each_role_once():
    """The RINASX-06 shape: a course line over a run of bare roles. The
    finding names the course line and quotes the first roles under it; with
    no holder above, it names the first role and quotes the roles after it,
    so no entry is named twice either way."""
    course = _course_block()[0]
    roles = [_grp4(f"{word} Leader 196{n}", "K3", 141 + n, role=f"{word} Leader",
                   start_date=f"196{n}")
             for n, word in enumerate(("Widget", "Gadget", "Gizmo", "Doohickey"))]
    blocks = [("p", f"196{n} - {word} Leader")
              for n, word in enumerate(("Widget", "Gadget", "Gizmo", "Doohickey"))]

    def named(finding):
        return [text.split(":")[0] for text in [finding["message"], *finding["evidence"]]]

    held = lint_group_header_context({"entries": [course, *roles]}, [], blocks)
    assert [named(f) for f in held] == [
        ["entry 140 (K1)", "entry 141 (K3)", "entry 142 (K3)", "entry 143 (K3)"]]
    assert held[0]["message"].endswith("; 4 role lines")
    date_only = _grp4("1962-1964", "T", 140, start_date="1962", end_date="1964")
    unheld = lint_group_header_context({"entries": [date_only, *roles]}, [], blocks)
    assert [named(f) for f in unheld] == [
        ["entry 141 (K3)", "entry 142 (K3)", "entry 143 (K3)", "entry 144 (K3)"]]


def test_group_header_context_reports_a_bare_role_under_a_header_once():
    """An office under a society line is its child (children_lost_header),
    not a second finding as a bare role."""
    hits = _grp_hits(_society_block(), _SOCIETY_ROWS)
    assert [shape for _, _, shape in hits] == ["children_lost_header"]


def _lead_block(lead_text="Thesis Committees, Example State University", code="K2", **fields):
    """An undated lead line, then three dated mentee lines coded N3B
    (the IEUPKK-18 shape)."""
    lead_fields = fields or {"institution": "Example State University",
                             "teaching_role": "Thesis Committee Member"}
    return [_grp4(lead_text, code, 610, **lead_fields)] + [
        _grp4(f"1976-1979 Widget Student {n}, MSc candidate", "N3B", 611 + n,
              mentee_name=f"Widget Student {n}", start_date="1976", end_date="1979")
        for n in range(3)]


_GRP_LEAD_ROWS = [[["Thesis Committees, Example State University", ""]]]


def test_group_header_context_notes_a_lead_line_coded_unlike_its_list():
    findings = lint_group_header_context({"entries": _lead_block()}, _GRP_LEAD_ROWS, [])
    assert [(f["severity"], f["message"].split(":")[0], f["message"].split(": ")[1])
            for f in findings] == [("INFO", "entry 610 (K2)", "header_coded_unlike_list")]
    assert findings[0]["evidence"][0] == (
        "entry 610 (K2): Thesis Committees, Example State University")
    assert findings[0]["evidence"][1].startswith("entry 611 (N3B): 1976-1979 Widget Student 0")
    assert len(findings[0]["evidence"]) == 3


@pytest.mark.parametrize("change", [
    "two_below", "same_letter", "mixed_codes", "undated_below", "lead_dated", "label",
    "enumerated", "long", "states_role", "skip_letter", "list_skip_letter", "other_heading",
    "no_shared_word",
])
def test_group_header_context_spares_a_line_that_leads_no_list(change):
    entries = _lead_block()
    if change == "two_below":
        entries = entries[:3]
    elif change == "same_letter":
        entries = _lead_block(code="N3A")
    elif change == "mixed_codes":
        entries[2]["taxonomy_code"] = "N3A"
    elif change == "undated_below":
        entries[3]["text"] = "Widget Student 2, MSc candidate"
        entries[3]["extracted_fields"] = {"mentee_name": "Widget Student 2"}
    elif change == "lead_dated":
        entries[0]["text"] += " 1976"
    elif change == "label":
        entries[0]["text"] += ":"
    elif change == "enumerated":
        entries[0]["text"] = "3. " + entries[0]["text"]
    elif change == "long":
        entries[0]["text"] += " and other widget gadget gizmo committee work"
    elif change == "states_role":
        entries = _lead_block("Chair, Example Widget Campaign", code="O",
                              leadership_role="Chair", organization="Example Widget Campaign")
    elif change == "skip_letter":
        entries[0]["taxonomy_code"] = "S8"
    elif change == "list_skip_letter":
        for entry in entries[1:]:
            entry["taxonomy_code"] = "S8"
    elif change == "no_shared_word":
        entries[0]["extracted_fields"] = {"teaching_role": "Gizmo Mentor"}
    else:
        entries[2]["hierarchy"] = ["Other Heading"]
    rows = _GRP_LEAD_ROWS + [[["Chair", "Example Widget Campaign"]]]
    assert [hit for hit in _grp_hits(entries, rows)
            if hit[2] == "header_coded_unlike_list"] == []


@pytest.mark.parametrize("row", [
    "Thesis Committees, Example State University | 1976-1979",
    "Thesis Committees, Example State University, Widget Gadget Gizmo Campus",
], ids=["row_with_a_year", "three_more_words"])
def test_group_header_context_needs_the_lead_line_on_a_row_of_its_own(row):
    assert _grp_hits(_lead_block(), [[[cell for cell in row.split(" | ")]]]) == []


def test_group_header_context_allows_two_more_words_on_the_lead_row():
    rows = [[["Thesis Committees (Example State University, Widget Campus)", ""]]]
    assert _grp_hits(_lead_block(), rows) == [
        ("INFO", "entry 610 (K2)", "header_coded_unlike_list")]


def _dated_block(parent_end=84):
    """A dated appointment block stage 2 joined from several lines, then an
    undated attending role under it (the IEUPKK-14 shape)."""
    return [_grp4("1971-1979 Professor\tDepartment of Widgets\tExample University", "D1", 80,
                  end=parent_end, title="Professor", institution="Example University",
                  start_date="1971", end_date="1979"),
            _grp4("Widget Attending\tExample Hospital", "D2", 85, end=88,
                  title="Widget Attending", institution="Example Hospital")]


_DATED_ROWS = [[["Professor", "Example University", "1971-1979"],
                ["Widget Attending", "Example Hospital", ""]]]


def test_group_header_context_notes_a_role_that_lost_its_block_dates():
    findings = lint_group_header_context({"entries": _dated_block()}, _DATED_ROWS, [])
    assert [(f["severity"], f["message"].split(":")[0], f["message"].split(": ")[1])
            for f in findings] == [("INFO", "entry 80 (D1)", "parent_dates_lost")]
    assert findings[0]["evidence"] == ["entry 85 (D2): Widget Attending | Example Hospital"]


def test_group_header_context_follows_undated_roles_up_to_the_block():
    entries = _dated_block() + [_grp4("Widget Consultant\tOther Hospital", "D2", 89,
                                      title="Widget Consultant", institution="Other Hospital")]
    rows = [_DATED_ROWS[0] + [["Widget Consultant", "Other Hospital", ""]]]
    findings = lint_group_header_context({"entries": entries}, rows, [])
    assert findings[0]["message"].endswith("; 2 entries below it")


def test_group_header_context_reads_a_dated_group_header_as_a_block():
    """SEKQUI-shaped: an institution and its years, then the roles held there."""
    entries = [_grp4("Example University (1964-1968)", "K2", 230, institution="Example University",
                     start_date="1964", end_date="1968"),
               _grp4("Director, Widget Studies, Example University", "K3", 231,
                     role="Director", course_title="Widget Studies",
                     institution="Example University")]
    rows = [[["Director, Widget Studies", "Example University", ""]]]
    assert [shape for _, _, shape in _grp_hits(entries, rows)] == ["parent_dates_lost"]


@pytest.mark.parametrize("change", [
    "one_line_parent", "short_date", "row_year", "other_letter", "enumerated_parent",
    "enumerated_child", "no_institution", "between", "between_other_letter",
    "between_other_heading",
])
def test_group_header_context_spares_a_role_with_no_lost_block_dates(change):
    """RNKYST 18-21: one dated line above an undated one is a list whose
    next line has no date in the source. A short month/year date with no
    four-digit year ('4/93') is still a date of the entry's own."""
    entries, rows = _dated_block(), [list(_DATED_ROWS[0])]
    if change == "one_line_parent":
        entries = _dated_block(parent_end=80)
    elif change == "short_date":
        entries[1]["text"] += " 4/93-5/94"
    elif change == "row_year":
        rows = [[["Professor", "Example University", "1971-1979"],
                 ["Widget Attending", "Example Hospital", "1973"]]]
    elif change == "other_letter":
        entries[1]["taxonomy_code"] = "G"
    elif change == "enumerated_parent":
        entries[0]["text"] = "1. " + entries[0]["text"]
    elif change == "enumerated_child":
        entries[1]["text"] = "- " + entries[1]["text"]
    elif change == "no_institution":
        del entries[1]["extracted_fields"]["institution"]
    elif change == "between":
        entries.insert(1, _grp4("Gadget Lecture", "D1", 82, title="Gadget Lecture"))
    elif change == "between_other_letter":
        entries.insert(1, _grp4("Gadget Program", "G", 82, organization="Gadget Program"))
    else:
        entries.insert(1, _grp4("Gizmo Attending\tGizmo Hospital", "D2", 82, heading="Other",
                                title="Gizmo Attending", institution="Gizmo Hospital"))
    assert _grp_hits(entries, rows) == []


def test_run_doctor_wires_group_header_context_with_the_rendered_rows(tmp_path):
    """The LINT_REGISTRY row hands the lint the docx's table rows and
    blocks: every shape reads the rendered rows."""
    root = _build_clean_run(tmp_path)
    fields = root / "stage_4_field_extraction" / f"{_UID}_cv_fields.json"
    data = json.loads(fields.read_text())
    data["entries"].extend(_course_block())
    fields.write_text(json.dumps(data))
    docx_path = root / "stage_6_wcm_documents" / f"{_UID}_cv_wcm.docx"
    output = Document(str(docx_path))
    for _, text in _COURSE_BLOCKS:
        output.add_paragraph(text)
    output.save(str(docx_path))

    payload = run_doctor(root, _UID)

    hits = [f for f in payload["findings"] if f["lint"] == "group_header_context"]
    assert [(f["severity"], f["evidence"][0]) for f in hits] == [
        ("WARN", "entry 141 (K3): 1956-1958 - Workshop Coordinator")]


def test_group_header_context_reads_entries_in_source_order():
    """Stage 4 lists entries by batch; the lint reads them by position, and
    skips one with no position."""
    block = _society_block()
    unplaced = _grp4("Gizmo Council", "Q2", None, committee_name="Gizmo Council")
    hits = _grp_hits([block[2], unplaced, block[0], block[1]], _SOCIETY_ROWS)
    assert hits == [("WARN", "entry 10 (I)", "children_lost_header")]


def test_group_header_context_reads_an_undated_committee_as_no_header():
    """KJJVVO 303: a committee with no organization is a line under the
    society, not a header of the office after it."""
    block = _society_block()
    block[1] = _grp4("Gadget Council", "Q2", 11, committee_name="Gadget Council")
    rows = [[["Gadget Council", "Member"], ["", "Chair", "1988-1990"]]]
    findings = lint_group_header_context({"entries": block}, rows, [])
    assert [f["message"].split(":")[0] for f in findings] == ["entry 10 (I)"]


def test_group_header_context_quotes_the_tightest_row_of_a_short_child():
    """'Chair' also shows inside a longer row of the same years."""
    rows = [[["Gadget Council", "Member", "1985-1994"],
             ["Gizmo Panel", "Chair of the Gizmo Panel", "1988-1990"],
             ["", "Chair", "1988-1990"]]]
    findings = lint_group_header_context({"entries": _society_block()}, rows, [])
    assert findings[0]["evidence"][1] == "entry 12 (Q1): Chair | 1988-1990"


def test_group_header_context_reads_a_role_of_three_other_words_as_bare():
    """A role word and three other words is still a role (the RINASX-06
    shape); one more word is a name (see the four_other_words case)."""
    entries = [_course_block()[0],
               _grp4("Widget Gadget Gizmo Leader 1953", "K2", 143,
                     teaching_role="Widget Gadget Gizmo Leader", start_date="1953")]
    blocks = [("p", "1953 - Widget Gadget Gizmo Leader")]
    assert _grp_hits(entries, [], blocks) == [("WARN", "entry 140 (K1)", "role_without_holder")]


def test_group_header_context_reports_each_run_of_bare_roles():
    """A run of roles ends at the next line that is not one; each run is
    named by the line above it."""
    course, director, codirector = _course_block()
    other = _grp4("Gadget Course 1966-present", "K1", 150, course_title="Gadget Course",
                  start_date="1966", end_date="present")
    codirector["element_idx_start"] = codirector["element_idx_end"] = 151
    entries = [course, director, other, codirector]
    hits = _grp_hits(entries, [], _COURSE_BLOCKS)
    assert hits == [("WARN", "entry 140 (K1)", "role_without_holder"),
                    ("WARN", "entry 150 (K1)", "role_without_holder")]


@pytest.mark.parametrize("words, fires", [(12, True), (13, False)])
def test_group_header_context_reads_a_lead_line_of_up_to_twelve_words(words, fires):
    text = " ".join(["Thesis", "Committees,", "Example", "State", "University"]
                    + ["Gizmo"] * (words - 5))
    entries = _lead_block(lead_text=text)
    rows = [[[text, ""]]]
    assert bool(_grp_hits(entries, rows)) is fires


def test_group_header_context_quotes_a_list_line_to_eighty_characters():
    entries = _lead_block()
    entries[1]["text"] = "1976-1979 " + "Widget " * 20
    findings = lint_group_header_context({"entries": entries}, _GRP_LEAD_ROWS, [])
    assert findings[0]["evidence"][1] == "entry 611 (N3B): " + entries[1]["text"][:80]


def test_group_header_context_reads_a_stated_role_in_any_role_field():
    """A lead line with one role field it states and one it does not is
    still a record ('Chair, <campaign>')."""
    entries = _lead_block("Chair, Example Widget Campaign", code="O", leadership_role="Chair",
                          title="Gizmo Office", organization="Example Widget Campaign")
    rows = [[["Chair", "Example Widget Campaign"]]]
    assert _grp_hits(entries, rows) == []


def test_group_header_context_sorts_string_positions_as_numbers():
    """Stage 4 may write element_idx_start as a string ('9', '10')."""
    header, committee, office = _society_block()
    for entry, idx in ((header, "9"), (committee, "10"), (office, "11")):
        entry["element_idx_start"] = entry["element_idx_end"] = idx
    hits = _grp_hits([office, committee, header], _SOCIETY_ROWS)
    assert hits == [("WARN", "entry 9 (I)", "children_lost_header")]


def test_group_header_context_reads_a_child_row_of_its_name_alone():
    """KJJVVO 303: an undated committee renders as its name and nothing
    more."""
    block = _society_block()[:2]
    block[1] = _grp4("Gadget Council", "Q2", 11, committee_name="Gadget Council")
    findings = lint_group_header_context({"entries": block}, [[["Gadget Council"]]], [])
    assert findings[0]["evidence"] == ["entry 11 (Q2): Gadget Council"]


def test_group_header_context_reads_a_lead_line_that_is_all_role_as_a_record():
    entries = _lead_block("Chair, Example Campaign", code="O",
                          leadership_role="Chair, Example Campaign")
    assert _grp_hits(entries, [[["Chair, Example Campaign", ""]]]) == []
