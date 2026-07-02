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

from unified_pipeline.run_doctor import (  # noqa: E402
    APPENDIX_WARN_ENTRIES,
    iter_header_candidates,
    lint_bucket_status,
    lint_classified_unrendered,
    lint_dead_sections,
    lint_missed_headers,
    lint_output_hygiene,
    lint_segmentation,
    lint_under_extraction,
    main,
    read_docx_blocks,
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


def test_missed_headers_fires_on_demoted_header():
    findings = lint_missed_headers(
        ["PROFESSIONAL EXPERIENCE"], _STAGE1A,
        {"entries": [_entry(_GRANT_FSMB, hierarchy=["GRANTS"])]})
    assert len(findings) == 1
    assert findings[0]["severity"] == "WARN"
    assert "PROFESSIONAL EXPERIENCE" in findings[0]["message"]


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


# ------------------------------------------------------------ full doctor runs

_UID = "89TEST"


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
                 {"document_uid": uid, "entries": [
                     _grant4(g, c, start=i + 1)
                     for i, (g, c) in enumerate(zip(grants, codes))]})

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
    return root


def test_run_doctor_tolerates_missing_artifacts(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    payload = run_doctor(root, "NOPE")
    assert len(payload["findings"]) == 7
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
                 {"document_uid": _UID, "entries": [
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
