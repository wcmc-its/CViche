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
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import pytest  # noqa: E402
from docx import Document  # noqa: E402

from unified_pipeline.quality_score import score_cv_owner  # noqa: E402
from unified_pipeline.run_doctor import (  # noqa: E402
    CLASSIFIED_UNRENDERED_WARN_ENTRIES,
    MISSED_HEADERS_WARN_COUNT,
    lint_surprise,
    rank_lints,
    appendix_entry_count,
    honors_table_totals,
    unrouted_code_counts,
    iter_header_candidates,
    lint_bucket_status,
    lint_classified_unrendered,
    lint_dead_sections,
    lint_dedup_drops,
    DUPLICATE_PASSAGE_MIN_BLOCKS,
    lint_duplicate_passages,
    lint_enrichment_failures,
    lint_missed_headers,
    lint_no_output,
    lint_output_hygiene,
    lint_owner_contact_missing,
    lint_pipe_leaks,
    lint_pipeline_errors,
    lint_stage3b_fallback_ratio,
    lint_taxonomy_code_coverage,
    lint_segmentation,
    lint_stage6_warnings,
    lint_table_shape,
    lint_under_extraction,
    lint_unrendered_records,
    main,
    read_docx_blocks,
    read_docx_table_rows,
    run_doctor,
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
    # N2 was #529's original example; #529 gave it a render route, so this
    # now uses M4 -- #291's still-parked status-aware-routing gap (see
    # test_taxonomy_code_render_coverage.py's _KNOWN_GAPS) -- a real,
    # confidently-classified code stage 6 has no renderer for today.
    stage3b = {"entries": [
        _entry("Postdoctoral Fellowship $26,000", taxonomy_code="M4", start=1),
        _entry("Mentored Research Scholar Grant", taxonomy_code="M4", start=2),
    ]}
    findings = lint_taxonomy_code_coverage(stage3b)
    assert len(findings) == 1
    # #816: always INFO now -- the unrouted-code counts moved to the
    # doctor's `metrics` block (unrouted_code_entries).
    assert findings[0]["severity"] == "INFO"
    assert "M4" in findings[0]["message"]
    assert "2 entries" in findings[0]["message"]


def test_unrouted_code_counts_matches_the_lints_own_by_code_dict():
    """#816: the doctor's `metrics` block reads this SAME dict the lint
    above builds its findings from. M4/M4A, not N1/N2: #529 gave N1 and
    N2 render routes (see test_taxonomy_code_render_coverage.py's
    _KNOWN_GAPS)."""
    stage3b = {"entries": [
        _entry("Postdoctoral Fellowship", taxonomy_code="M4", start=1),
        _entry("Mentored Research Scholar Grant", taxonomy_code="M4", start=2),
        _entry("Another orphan code", taxonomy_code="M4A", start=3),
        _entry("A grant", taxonomy_code="M2A", start=4),
    ]}
    assert unrouted_code_counts(stage3b) == {"M4": 2, "M4A": 1}
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
    codes = ["A", "B1", "D1", "K5", "M2A", "M4C", "N2", "Q4D", "S0", "T"]
    findings = lint_output_hygiene([("p", f"• [{c}] leaked") for c in codes])
    assert findings[0]["severity"] == "ERROR"
    assert f"{len(codes)} bracketed" in findings[0]["message"]


def test_output_hygiene_flags_boilerplate_in_appendix():
    blocks = [
        ("p", "T. APPENDIX"),
        ("p", "The following content from the original CV was not "
              "successfully mapped to this CV format:"),
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
        ("p", "The following content from the original CV was not "
              "successfully mapped to this CV format:"),
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
        ("p", "The following content from the original CV was not "
              "successfully mapped to this CV format:"),
        ("p", "• Real leftover grant content | Role: PI | Status: Under review"),
    ]
    findings = lint_output_hygiene(blocks)
    assert all(f["severity"] == "INFO" for f in findings)


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


def test_enrichment_failures_missing_artifact_info_skip(tmp_path):
    root = _build_clean_run(tmp_path)
    next((root / "stage_5_enrichment").glob("*.json")).unlink()
    payload = run_doctor(root, _UID)
    skips = [f for f in payload["findings"] if f["lint"] == "enrichment_failures"]
    assert len(skips) == 1
    assert skips[0]["severity"] == "INFO"
    assert "stage_5_enrichment" in skips[0]["message"]
    assert payload["artifacts"]["stage_5_enrichment"] is None


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


# ----------------------------------------------------- lint 13: table shape

_HONORS_HEADER = ["Name of award", "Organization", "Date awarded (yyyy)"]
_BLOB = ("Basic Science Innovation in Education Award – Runner-up "
         "Presentation. Saibal Day, Eulho Jung, and Thomas Flagg. Basic "
         "Science Innovation in Education. Uniformed Services University "
         "Education Day, Bethesda, MD, August 2025.")


def test_table_shape_flags_malformed_honors_rows():
    tables = [[_HONORS_HEADER,
               [_BLOB, "MD", ""],
               ["2020 AECT Outstanding Article Award, Association for "
                "Educational Communication and Technology (AECT)",
                "Association for Educational Communication and Technology "
                "(AECT)", ""],
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

    output = Document()
    output.add_paragraph("D. GRANTS")
    output.add_paragraph("Current Research Funding")
    table = output.add_table(rows=1, cols=1)
    table.rows[0].cells[0].paragraphs[0].text = grants[0]
    output.add_paragraph("Pending Funding")
    table = output.add_table(rows=2, cols=1)
    for i, grant in enumerate(grants[1:]):
        table.rows[i].cells[0].paragraphs[0].text = grant
    output.add_paragraph("T. APPENDIX")
    output.add_paragraph("The following content from the original CV was not "
                         "successfully mapped to this CV format:")
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
    # One skip per lint in KNOWN_LINTS (23), except no_output: it never even
    # reached stage 4, so its "has_stage4 and not has_docx..." condition is
    # False and it emits NOTHING, not a skip -- it is dispatched by hand
    # (booleans, not `_ready()`-checked content) precisely so an incomplete
    # run like this one is silent rather than reported as "no output" (#745).
    assert len(payload["findings"]) == 22
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
                            "counts", "worst_severity", "metrics"}
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


# ------------------------------------------------------------- #816: metrics

def test_build_metrics_reads_every_number_from_a_realistic_run(tmp_path):
    """One `_build_metrics` call over a run carrying all seven inputs at
    once: an appendix with real entries, an honors table with a malformed
    row, an unrouted code, a stage-3b fallback ratio, and both yield stats."""
    from unified_pipeline.run_doctor import _build_metrics

    stage3b = {
        "entries": [
            _entry("Postdoctoral Fellowship", taxonomy_code="M4", start=1),
            _entry("A grant", taxonomy_code="M2A", start=2),
        ],
        "meta": {"stats": {
            "failed_batches": 41, "llm_batches": 83,
            "fallback_entries": 510, "entries_classified": 1019,
            "t_validation": {"t_entries_reviewed": 93, "t_entries_reclassified": 28},
            "fragment_reconnection": {"fragments_reviewed": 7, "fragments_reconnected": 3},
        }},
    }
    blocks = [
        ("p", "T. APPENDIX"),
        ("p", "The following content from the original CV was not "
              "successfully mapped to this CV format:"),
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
    assert metrics["appendix_share"] == round(2 / 2, 4)
    assert metrics["honors_malformed_rows"] == 1
    assert metrics["honors_rows"] == 2
    assert metrics["unrouted_code_entries"] == {"M4": 1}  # M4, not N2: #529 routes N2
    assert metrics["stage3b_fallback_ratio"] == round(510 / 1019, 4)
    assert metrics["t_validation_yield"] == round(28 / 93, 4)
    assert metrics["fragment_reconnection_yield"] == round(3 / 7, 4)
    assert "source_coverage_pct" in metrics


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
    tree = ast.parse(Path(doctor_shared.__file__).read_text())
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

        tree = ast.parse(Path(module.__file__).read_text())
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
