"""The review copy: the run doctor's WARN/ERROR findings as Word comments (#1388 C).

Every document here is built in the test from invented text; no corpus CV.
"""
import copy
import os
import sys
from pathlib import Path

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from docx import Document  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402

from app.services import review_comments as rc  # noqa: E402
from app.services.artifact_service import REVIEW_DOCX_SUFFIX  # noqa: E402
from app.services.run_quality_report import LINT_COPY, MAX_INSTANCES_SHOWN  # noqa: E402
from unified_pipeline.run_doctor import read_docx_blocks  # noqa: E402

HEADER = "Example Medical College"
PAST_FUNDING_HEADING = "Past (Completed) Funding"
GRANT = "Zebrafish Fin Regrowth Study, Example Foundation, 2011-2014"
CITATION = "12. Quill AB, Marsh CD. Lanternfish vision under pressure. J Example Biol. 2019;4:1-9."
ENRICHED = "13. Quill AB. A tracked insertion only enrichment adds. J Example Biol. 2020;5:2-3."


def _clean_docx(tmp_path: Path) -> Path:
    doc = Document()
    doc.add_paragraph(HEADER)
    doc.add_paragraph("RESEARCH")
    doc.add_paragraph(PAST_FUNDING_HEADING)
    doc.add_paragraph(GRANT)
    doc.add_paragraph("BIBLIOGRAPHY")
    doc.add_paragraph(CITATION)
    doc.add_paragraph(CITATION)  # a repeated record
    # Stage 5 enrichment writes a citation as a tracked insertion: no direct w:r.
    p = doc.add_paragraph()._p
    ins = p.makeelement(qn("w:ins"), {qn("w:id"): "90", qn("w:author"): "CViche"})
    run = doc.add_paragraph(ENRICHED).runs[0]._r
    ins.append(copy.deepcopy(run))
    p.append(ins)
    doc.element.body.remove(run.getparent())
    doc.add_paragraph("T. APPENDIX")
    out = tmp_path / "DOC_wcm.docx"
    doc.save(str(out))
    return out


def _finding(lint, message, evidence=(), severity="WARN"):
    return {"lint": lint, "severity": severity, "message": message,
            "evidence": list(evidence), "status": "ran", "reason": ""}


def _report(*findings):
    return {"findings": list(findings)}


def _comments(path: Path) -> list[tuple[str, str, str]]:
    """(author, comment text, text of the paragraph it is anchored to)."""
    doc = Document(str(path))
    texts = {c.comment_id: (c.author, c.text) for c in doc.comments}
    anchored = []
    for p in doc.element.body.iter(qn("w:p")):
        for ref in p.iter(qn("w:commentRangeStart")):
            author, text = texts[int(ref.get(qn("w:id")))]
            anchored.append((author, text, "".join(t.text or "" for t in p.iter(qn("w:t")))))
    return anchored


def test_a_quoted_record_gets_the_comment_with_the_run_pages_wording(tmp_path):
    clean = _clean_docx(tmp_path)
    out, n = rc.write_review_docx(clean, _report(
        _finding("pipe_leaks", "1 numbered citation(s) fusing multiple values", [CITATION[:100]])))

    assert out == tmp_path / f"DOC{REVIEW_DOCX_SUFFIX}" and n == 1
    [(author, text, anchor)] = _comments(out)
    assert author == rc.COMMENT_AUTHOR
    assert anchor == CITATION
    copy_ = LINT_COPY["pipe_leaks"]
    assert text.startswith(f"{copy_.title}: 1 numbered citation(s)")
    assert f"What to do: {copy_.what_to_do}" in text
    assert rc.UNLOCATED_NOTE not in text


def test_output_text_in_a_doctor_note_anchors_the_comment(tmp_path):
    """duplicate_records quotes the output in its note, after a block locator."""
    clean = _clean_docx(tmp_path)
    out, _ = rc.write_review_docx(clean, _report(
        _finding("duplicate_records", "1 duplicated record(s)", [f"block 5 repeats at 6: {CITATION[:90]}"])))
    [(_, _, anchor)] = _comments(out)
    assert anchor == CITATION


def test_a_tracked_insertion_is_found_and_commented(tmp_path):
    """Paragraph.text skips w:ins runs; the anchor search must not."""
    clean = _clean_docx(tmp_path)
    out, _ = rc.write_review_docx(clean, _report(
        _finding("enrichment_failures", "1 publication(s) failed PubMed enrichment", [ENRICHED])))
    [(_, _, anchor)] = _comments(out)
    assert anchor == ENRICHED


def test_a_quote_stage_6_reworded_is_found_by_its_middle(tmp_path):
    """Stage 4's "Grant Title: ..." is not what stage 6 prints; a unique window is."""
    clean = _clean_docx(tmp_path)
    out, _ = rc.write_review_docx(clean, _report(
        _finding("implausible_year", "entry 3 (M2B): end_date=1912 -- before 1959",
                 [f"Grant Title: {GRANT}; Role: PI"])))
    [(_, text, anchor)] = _comments(out)
    assert anchor == GRANT
    assert "Section: Past Research Funding" in text


def test_a_window_that_recurs_across_records_does_not_anchor(tmp_path):
    """A middle window matching more than one paragraph is not this record."""
    clean = _clean_docx(tmp_path)
    out, _ = rc.write_review_docx(clean, _report(
        _finding("enrichment_failures", "1 publication(s) failed PubMed enrichment",
                 ["99. Other XY, Person Z. Lanternfish vision under pressure. J Example Biol. 2019;4:1-9."])))
    [(_, text, anchor)] = _comments(out)
    assert anchor == HEADER
    assert rc.UNLOCATED_NOTE in text


def test_no_quote_falls_back_to_the_sections_template_heading(tmp_path):
    clean = _clean_docx(tmp_path)
    out, _ = rc.write_review_docx(clean, _report(
        _finding("section_lost", "M2B: 1 entry absent from the RESEARCH section")))
    [(_, _, anchor)] = _comments(out)
    assert anchor == PAST_FUNDING_HEADING


def test_a_code_stage_6_routes_nowhere_falls_back_to_its_top_level_heading(tmp_path):
    """Stage 6's heading map is blank for T (it routes no overflow there)."""
    clean = _clean_docx(tmp_path)
    out, _ = rc.write_review_docx(clean, _report(
        _finding("stage6_render_warnings", "stage 6 self-check: T: 6 entries diverted to the Appendix")))
    [(_, text, anchor)] = _comments(out)
    assert anchor == "T. APPENDIX"
    assert "Section: Appendix/Other" in text


def test_protected_data_comment_never_carries_more_than_the_finding(tmp_path):
    """The doctor's protected-data finding names a section and a category,
    never the value; the comment is built from that finding alone."""
    clean = _clean_docx(tmp_path)
    finding = _finding("protected_data_in_output", "date of birth found in Personal Data",
                       severity="ERROR")
    out, _ = rc.write_review_docx(clean, _report(finding))
    [(_, text, _)] = _comments(out)
    assert "date of birth found in Personal Data" in text
    assert text.count("\n") <= 3  # title line, unlocated note, what to do


def test_info_and_skipped_findings_get_no_comment(tmp_path):
    clean = _clean_docx(tmp_path)
    skipped = {**_finding("segmentation", "skipped"), "status": "skipped"}
    assert rc.write_review_docx(clean, _report(
        _finding("missed_headers", "1 header", severity="INFO"), skipped)) is None
    assert not (tmp_path / f"DOC{REVIEW_DOCX_SUFFIX}").exists()


def test_one_lint_is_capped_at_what_the_run_page_lists(tmp_path):
    clean = _clean_docx(tmp_path)
    many = [_finding("output_hygiene", f"leak {i}") for i in range(MAX_INSTANCES_SHOWN + 5)]
    _, n = rc.write_review_docx(clean, _report(*many, _finding("pipe_leaks", "one more")))
    assert n == MAX_INSTANCES_SHOWN + 1


def test_comments_leave_the_body_the_doctor_reads_unchanged(tmp_path):
    """Comments live in comments.xml: the doctor's view of the copy is the clean one's."""
    clean = _clean_docx(tmp_path)
    out, _ = rc.write_review_docx(clean, _report(
        _finding("pipe_leaks", "x", [CITATION]),
        _finding("section_lost", "M2B: 1 entry absent from the RESEARCH section"),
        _finding("dedup_drops", "1 dedup drop(s)")))
    assert read_docx_blocks(str(out)) == read_docx_blocks(str(clean))
    assert len(list(Document(str(clean)).comments)) == 0  # the clean document is untouched


def test_not_a_doctor_report_writes_nothing(tmp_path):
    assert rc.write_review_docx(_clean_docx(tmp_path), None) is None
