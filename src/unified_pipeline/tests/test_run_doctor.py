"""Tests for the cross-stage run doctor.

Pure-function tests only — no LLM, no DB, no gold corpus. Fixtures are small
synthetic 89HQVQ-shaped artifacts (pipe-delimited grant rows, fused
mega-entries, mis-bucketed statuses); the docx files used are built in-test
with python-docx.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_run_doctor.py -p no:cacheprovider
"""

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
    APPENDIX_WARN_ENTRIES,
    CLASSIFIED_UNRENDERED_WARN_ENTRIES,
    MISSED_HEADERS_WARN_COUNT,
    TABLE_SHAPE_WARN_DEFECTS,
    TABLE_SHAPE_WARN_ROW_RATIO,
    lint_surprise,
    rank_lints,
    iter_header_candidates,
    lint_bucket_status,
    lint_classified_unrendered,
    lint_dead_sections,
    lint_dedup_drops,
    DUPLICATE_PASSAGE_MIN_BLOCKS,
    lint_duplicate_passages,
    lint_enrichment_failures,
    lint_missed_headers,
    lint_output_hygiene,
    lint_owner_contact_missing,
    lint_pipe_leaks,
    lint_pipeline_errors,
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
    assert findings[0]["severity"] == "WARN"
    assert "M4" in findings[0]["message"]
    assert "2 entries" in findings[0]["message"]


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


def test_taxonomy_code_coverage_does_not_flag_codes_that_duplicate_instead():
    # E, G and N4 all render via their own direct dispatch (not the
    # RENDER_ROUTED_CODES lookup this lint checks) and then ALSO duplicate
    # into the appendix -- a real defect, but a different one (#294 for G,
    # #587 for N4) from "no render route at all", which is what this lint
    # exists to catch. Flagging them here would conflate the two classes.
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


def test_output_hygiene_warns_on_oversized_appendix():
    bullets = [("p", f"• Unmapped leftover entry with descriptive text number {i}")
               for i in range(APPENDIX_WARN_ENTRIES + 1)]
    blocks = [("p", "T. APPENDIX"), ("p", "The following content:")] + bullets
    count = next(f for f in lint_output_hygiene(blocks)
                 if "appendix holds" in f["message"])
    assert count["severity"] == "WARN"


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
    assert f["lint"] == "table_shape" and f["severity"] == "WARN"
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
    assert len(payload["findings"]) == 18  # one skip per lint in KNOWN_LINTS
    assert all(f["severity"] == "INFO" and "skipped" in f["message"]
               for f in payload["findings"])
    assert payload["counts"]["ERROR"] == 0
    assert payload["counts"]["WARN"] == 0
    assert all(v is None for v in payload["artifacts"].values())


def test_run_doctor_clean_run_end_to_end(tmp_path):
    root = _build_clean_run(tmp_path)
    payload = run_doctor(root, _UID)
    assert set(payload) == {"document_uid", "root", "artifacts", "findings",
                            "counts", "worst_severity"}
    assert all(v is not None for v in payload["artifacts"].values())
    assert not any("skipped" in f["message"] for f in payload["findings"])
    assert payload["counts"]["ERROR"] == 0
    assert payload["counts"]["WARN"] == 0
    assert payload["worst_severity"] == "INFO"


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


def test_table_shape_severity_tracks_how_malformed_the_table_is():
    """A couple of bad rows in a long table is normal; a bad short table is not."""
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
    assert short_bad[0]["severity"] == "WARN", "3/4 malformed rows is"


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
