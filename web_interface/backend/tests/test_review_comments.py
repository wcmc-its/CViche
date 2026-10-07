"""The review copy: the run doctor's WARN/ERROR findings flagged in place (#1388 C).

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
from app.services.run_quality_report import LINT_COPY  # noqa: E402
from unified_pipeline.run_doctor import read_docx_blocks  # noqa: E402

HEADER = "Example Medical College"
PAST_FUNDING_HEADING = "Past (Completed) Funding"
GRANT = "Zebrafish Fin Regrowth Study, Example Foundation, 1912-1914"
CITATION = "12. Quill AB, Marsh CD. Lanternfish vision under pressure. J Example Biol. 2019;4:1-9."
SECOND = "14. Quill AB, Reed EF. The squid's lenses and light. J Example Biol. 2021;6:3-8."
ENRICHED = "13. Quill AB. A tracked insertion only enrichment adds. J Example Biol. 2020;5:2-3."
TEACHING = "Tobacco Cessation Lecturer (PHRM 827) 4 hrs, 156 students"
APPENDIX_LINES = ("1. Volunteer, Example Food Bank, 2015", "2. Marathon finisher, 2018")


def _clean_docx(tmp_path: Path, appendix: bool = True) -> Path:
    doc = Document()
    doc.add_paragraph(HEADER)
    doc.add_paragraph("RESEARCH")
    doc.add_paragraph(PAST_FUNDING_HEADING)
    doc.add_paragraph(GRANT)
    doc.add_paragraph("EDUCATIONAL CONTRIBUTIONS")
    doc.add_paragraph(TEACHING)
    doc.add_paragraph("BIBLIOGRAPHY")
    doc.add_paragraph(CITATION)
    doc.add_paragraph(CITATION)  # a repeated record
    doc.add_paragraph(SECOND)
    # Stage 5 enrichment writes a citation as a tracked insertion: no direct w:r.
    p = doc.add_paragraph()._p
    ins = p.makeelement(qn("w:ins"), {qn("w:id"): "90", qn("w:author"): "CViche"})
    run = doc.add_paragraph(ENRICHED).runs[0]._r
    ins.append(copy.deepcopy(run))
    p.append(ins)
    doc.element.body.remove(run.getparent())
    if appendix:
        doc.add_paragraph("T. APPENDIX")
        for line in APPENDIX_LINES:
            doc.add_paragraph(line)
    out = tmp_path / "DOC_wcm.docx"
    doc.save(str(out))
    return out


def _finding(lint, message, evidence=(), severity="WARN"):
    return {"lint": lint, "severity": severity, "message": message,
            "evidence": list(evidence), "status": "ran", "reason": ""}


def _report(*findings):
    return {"findings": list(findings)}


def _comments(path: Path) -> list[tuple[str, str]]:
    """(comment text, the exact text it is anchored to), in document order."""
    doc = Document(str(path))
    texts = {c.comment_id: c.text for c in doc.comments}
    anchored: dict[int, str] = {}
    open_ids: list[int] = []
    for el in doc.element.body.iter():
        if el.tag == qn("w:commentRangeStart"):
            open_ids.append(int(el.get(qn("w:id"))))
            anchored.setdefault(open_ids[-1], "")
        elif el.tag == qn("w:commentRangeEnd"):
            open_ids.remove(int(el.get(qn("w:id"))))
        elif el.tag == qn("w:t"):
            for cid in open_ids:
                anchored[cid] += el.text or ""
    return [(texts[cid], body) for cid, body in anchored.items()]


def _notes(path: Path) -> list[str]:
    """The review-note bullets that close the document, without their bullet."""
    texts = [p.text for p in Document(str(path)).paragraphs]
    if rc.REVIEW_NOTES_HEADING not in texts:
        return []
    return [t.removeprefix(rc.NOTE_BULLET) for t in texts[texts.index(rc.REVIEW_NOTES_HEADING) + 1:]]


def _flag(lint):
    return rc.REVIEW_FLAGS[lint]


def test_every_lint_the_run_page_words_has_a_flag():
    assert set(LINT_COPY) <= set(rc.REVIEW_FLAGS)


def test_a_quoted_record_gets_its_short_flag(tmp_path):
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("enrichment_failures", "1 publication(s) failed PubMed enrichment", [SECOND])))
    assert out == tmp_path / f"DOC{REVIEW_DOCX_SUFFIX}" and n == 1
    assert _comments(out) == [(_flag("enrichment_failures"), SECOND)]
    assert [c.author for c in Document(str(out)).comments] == [rc.COMMENT_AUTHOR]


def test_a_duplicate_is_flagged_on_its_later_copy(tmp_path):
    """The note names the first copy; the second is the one to delete."""
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("duplicate_records", "1 duplicated record(s)", [f"block 7 repeats at 8: {CITATION[:90]}"])))
    doc = Document(str(out))
    starts = [i for i, p in enumerate(doc.element.body.iter(qn("w:p")))
              if p.find(qn("w:commentRangeStart")) is not None]
    assert starts == [8]  # paragraphs: 7 is the first CITATION, 8 the repeat
    assert _comments(out) == [(_flag("duplicate_records"), CITATION)]


def test_each_record_a_finding_lists_gets_its_own_flag(tmp_path):
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("pipe_leaks", "2 numbered citation(s) fusing multiple venue-date patterns",
                 [f"[bibliography] {CITATION[:100]}", f"[bibliography] {SECOND}"])))
    assert n == 2
    assert [anchor for _, anchor in _comments(out)] == [CITATION, SECOND]


def test_a_wrong_year_is_flagged_on_the_year_alone(tmp_path):
    clean = _clean_docx(tmp_path)
    out, _ = rc.write_review_docx(clean, _report(
        _finding("implausible_year", "entry 3 (M2B): end_date=1912 -- before 1959",
                 [f"Grant Title: {GRANT}; Role: PI"])))
    assert _comments(out) == [(_flag("implausible_year"), "1912")]
    # Splitting the run to isolate the year leaves the text the doctor reads alone.
    assert read_docx_blocks(str(out)) == read_docx_blocks(str(clean))


def test_a_dedup_drop_is_flagged_on_the_kept_entry_and_quotes_the_dropped_one(tmp_path):
    dropped = TEACHING.replace("4 hrs", "5 hrs")
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("dedup_drops", "1 dedup drop(s) poorly covered by the kept entry",
                 [f"K1 (jaccard=1.00, 89% covered by kept): dropped '{dropped}' vs kept '{TEACHING}'"])))
    [(text, anchor)] = _comments(out)
    assert anchor == TEACHING
    assert text == f'{rc.DEDUP_DROPPED_FLAG}: "{dropped}"'


def test_a_curly_apostrophe_in_the_quote_matches_a_straight_one_in_the_output(tmp_path):
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("enrichment_failures", "1 failed", ["Quill AB, Reed EF. The squid\u2019s lenses"])))
    assert _comments(out) == [(_flag("enrichment_failures"), SECOND)]


def test_a_tracked_insertion_is_found_and_flagged(tmp_path):
    """Paragraph.text skips w:ins runs; the anchor search must not."""
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("enrichment_failures", "1 publication(s) failed PubMed enrichment", [ENRICHED])))
    assert _comments(out) == [(_flag("enrichment_failures"), ENRICHED)]


def test_a_window_that_recurs_across_records_does_not_anchor(tmp_path):
    """A middle window matching more than one paragraph is not this record:
    the finding becomes a review note, quoting what it was about."""
    other = "99. Other XY, Person Z. Lanternfish vision under pressure. J Example Biol. 2019;4:1-9."
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("enrichment_failures", "1 publication(s) failed PubMed enrichment", [other])))
    assert _comments(out) == []
    assert _notes(out) == [f'{_flag("enrichment_failures")} "{other}"']


def test_no_quote_falls_back_to_the_sections_template_heading(tmp_path):
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("section_lost", "M2B: 1 entry absent from the RESEARCH section")))
    assert _comments(out) == [(_flag("section_lost"), PAST_FUNDING_HEADING)]


def test_findings_with_no_place_are_review_notes_closing_the_document(tmp_path):
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("implausible_year", "entry 9 (K1): date=1905"),  # no Didactic Teaching heading
        _finding("llm_fallback_served", "stage 4 S8: the content filter blocked the primary model"),
        _finding("llm_fallback_served", "stage 4 S5: the content filter blocked the primary model")))
    assert n == 3 and _comments(out) == []
    texts = [p.text for p in Document(str(out)).paragraphs]
    assert texts[-3:-2] == [rc.REVIEW_NOTES_HEADING]  # after the Appendix, at the very end
    assert _notes(out) == [f'{_flag("implausible_year")} (Didactic Teaching)',
                           _flag("llm_fallback_served")]  # the same note once


def test_appendix_diversions_flag_the_heading_with_where_from_and_each_line(tmp_path):
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("stage6_render_warnings", "stage 6 self-check: M2B: 2 entries diverted to the "
                 "Appendix — declined by the research-support renderer as too sparse to table"),
        _finding("stage6_render_warnings", "stage 6 self-check: T: 3 entries diverted to the "
                 "Appendix — no stage 6 section is routed to render this taxonomy code")))
    assert n == 3
    assert _notes(out) == ["Moved to the Appendix: 2 from Past Research Funding, 3 with no section."]
    assert _comments(out) == [(rc.APPENDIX_LINE_FLAG, line) for line in APPENDIX_LINES]


def test_appendix_groups_are_flagged_once_each_not_line_by_line(tmp_path):
    doc = Document(str(_clean_docx(tmp_path)))
    for text in ('From "TEACHING":', "1. 2014 - 2016", 'From "GRANT SUPPORT":', "1. Amount: $2,500"):
        doc.add_paragraph(text)
    doc.save(str(tmp_path / "DOC_wcm.docx"))
    out, _ = rc.write_review_docx(tmp_path / "DOC_wcm.docx", _report(
        _finding("stage6_render_warnings", "stage 6 self-check: T: 2 entries diverted to the Appendix")))
    assert _comments(out) == [(rc.APPENDIX_GROUP_FLAG, 'From "TEACHING":'),
                              (rc.APPENDIX_GROUP_FLAG, 'From "GRANT SUPPORT":')]


def test_a_record_printed_across_table_cells_is_found_by_its_row(tmp_path):
    doc = Document(str(_clean_docx(tmp_path)))
    row = doc.add_table(rows=1, cols=2).rows[0]
    row.cells[0].text, row.cells[1].text = "Squid Optics Seminar Leader (BIOL 412)", "3 hrs, 40 students"
    doc.save(str(tmp_path / "DOC_wcm.docx"))
    out, _ = rc.write_review_docx(tmp_path / "DOC_wcm.docx", _report(
        _finding("enrichment_failures", "1 failed", ["Squid Optics Seminar Leader (BIOL 412) 3 hrs, 40 students"])))
    assert _comments(out) == [(_flag("enrichment_failures"), "Squid Optics Seminar Leader (BIOL 412)")]


def test_an_unplaced_dedup_drop_quotes_only_the_removed_entry(tmp_path):
    evidence = "K1 (jaccard=1.00, 89% covered by kept): dropped 'Nowhere Lecture 5 hrs, 9 students' vs kept 'Nowhere Lecture 4 hrs, 9 students'"
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("dedup_drops", "1 dedup drop(s)", [evidence])))
    assert _comments(out) == []
    [note] = _notes(out)
    assert note == f'{rc.DEDUP_DROPPED_FLAG}: "Nowhere Lecture 5 hrs, 9 students"'
    assert "jaccard" not in note


def test_a_diversion_without_an_appendix_heading_is_still_flagged(tmp_path):
    out, n = rc.write_review_docx(_clean_docx(tmp_path, appendix=False), _report(
        _finding("stage6_render_warnings", "stage 6 self-check: M2B: 2 entries diverted to the Appendix")))
    assert n == 1
    assert _comments(out) == [(_flag("stage6_render_warnings"), PAST_FUNDING_HEADING)]


def test_protected_data_flag_never_carries_the_finding_text(tmp_path):
    """The flag is the fixed wording alone: nothing from the finding, which
    names a category and a section, reaches the comment."""
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("protected_data_in_output", "date of birth found in Personal Data", severity="ERROR")))
    assert _comments(out) == []
    assert _notes(out) == [_flag("protected_data_in_output")]


def test_info_and_skipped_findings_get_no_comment(tmp_path):
    clean = _clean_docx(tmp_path)
    skipped = {**_finding("segmentation", "skipped"), "status": "skipped"}
    assert rc.write_review_docx(clean, _report(
        _finding("missed_headers", "1 header", severity="INFO"), skipped)) is None
    assert not (tmp_path / f"DOC{REVIEW_DOCX_SUFFIX}").exists()


def test_one_lint_is_capped(tmp_path):
    many = [_finding("output_hygiene", f"leak {i}") for i in range(rc.MAX_FLAGS_PER_LINT + 5)]
    _, n = rc.write_review_docx(_clean_docx(tmp_path), _report(*many, _finding("pipe_leaks", "one more")))
    assert n == rc.MAX_FLAGS_PER_LINT + 1


def test_comments_leave_the_body_the_doctor_reads_unchanged(tmp_path):
    """Comments live in comments.xml: the doctor's view of the copy is the clean one's."""
    clean = _clean_docx(tmp_path)
    out, _ = rc.write_review_docx(clean, _report(
        _finding("pipe_leaks", "x", [CITATION]),
        _finding("section_lost", "M2B: 1 entry absent from the RESEARCH section")))
    assert _notes(out) == []
    assert read_docx_blocks(str(out)) == read_docx_blocks(str(clean))
    assert len(list(Document(str(clean)).comments)) == 0  # the clean document is untouched


def test_not_a_doctor_report_writes_nothing(tmp_path):
    assert rc.write_review_docx(_clean_docx(tmp_path), None) is None
