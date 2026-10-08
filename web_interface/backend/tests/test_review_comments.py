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
from unified_pipeline.stage6.formatting import CVICHE_BOX_FILL  # noqa: E402

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


def _box_lines(path: Path, title: str) -> list[str]:
    """The lines of the CViche box whose first line starts with ``title``, after it."""
    for table in Document(str(path)).tables:
        lines = [p.text for p in table.cell(0, 0).paragraphs]
        if lines[0].startswith(title):
            return lines[1:]
    return []


def _notes(path: Path) -> list[str]:
    """The review-notes box's lines under its title."""
    return _box_lines(path, rc.REVIEW_NOTES_TITLE)


def _with_appendix_note(tmp_path: Path) -> Path:
    """The clean document with stage 6's Appendix note box under T. APPENDIX."""
    doc = Document(str(_clean_docx(tmp_path)))
    heading = next(p for p in doc.paragraphs if p.text == "T. APPENDIX")
    table = doc.add_table(rows=1, cols=1)
    table.cell(0, 0).paragraphs[0].text = "CViche note: delete this box before sending"
    table.cell(0, 0).add_paragraph("These 5 entries from your original CV did not fit any section above.")
    heading._p.addnext(table._tbl)
    doc.save(str(tmp_path / "DOC_wcm.docx"))
    return tmp_path / "DOC_wcm.docx"


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


def test_near_duplicates_are_one_group_naming_section_removed_and_kept(tmp_path):
    dropped = TEACHING.replace("4 hrs", "5 hrs")
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("dedup_drops", "2 dedup drop(s) poorly covered by the kept entry", [
            f"K1 (jaccard=1.00, 89% covered by kept): dropped '{dropped}' vs kept '{TEACHING}'",
            "S5 (jaccard=0.94, 88% covered by kept): dropped '(9) Quill AB. Squid optics.' vs kept '(11) Quill AB. Squid optics.'"])))
    assert n == 2 and _comments(out) == []
    assert _notes(out) == [
        "Removed as near-duplicates (2)", rc.DEDUP_INSTRUCTION,
        "\u2022\tDidactic Teaching", f'Removed:\t"{dropped}"', f'Kept:\t"{TEACHING}"',
        "\u2022\tNon-peer-reviewed Publications", 'Removed:\t"(9) Quill AB. Squid optics."',
        'Kept:\t"(11) Quill AB. Squid optics."',
    ]
    assert "jaccard" not in "".join(_notes(out))


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
    assert _notes(out) == ["PubMed lookup failed (1)", _flag("enrichment_failures"), f'\u2022\t"{other}"']


def test_no_quote_falls_back_to_the_sections_template_heading(tmp_path):
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("section_lost", "M2B: 1 entry absent from the RESEARCH section")))
    assert _comments(out) == [(_flag("section_lost"), PAST_FUNDING_HEADING)]


def test_findings_with_no_place_are_review_notes_closing_the_document(tmp_path):
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("implausible_year", "entry 9 (K1): date=1905"),  # no Didactic Teaching heading
        _finding("llm_fallback_served", "stage 4 S8: the content filter blocked the primary model"),
        _finding("llm_fallback_served", "stage 4 S5: the content filter blocked the primary model")))
    assert n == 1 and _comments(out) == []
    # A note with nothing to point at (no quote, no section) stays on the run page.
    assert _notes(out) == ["Year probably wrong century (1)", _flag("implausible_year"), "\u2022\tDidactic Teaching"]


_DIVERSIONS = (
    _finding("stage6_render_warnings", "stage 6 self-check: M2B: 2 entries diverted to the "
             "Appendix — declined by the research-support renderer as too sparse to table"),
    _finding("stage6_render_warnings", "stage 6 self-check: T: 3 entries diverted to the "
             "Appendix — no stage 6 section is routed to render this taxonomy code"),
)


def test_appendix_diversions_add_nothing_to_the_review_copy(tmp_path):
    """They name the section CViche first tried, which contradicts the
    Appendix group headings (the CV's own); their counts stay on the run page."""
    assert rc.write_review_docx(_with_appendix_note(tmp_path), _report(*_DIVERSIONS)) is None


def test_a_record_printed_across_table_cells_is_found_by_its_row(tmp_path):
    doc = Document(str(_clean_docx(tmp_path)))
    row = doc.add_table(rows=1, cols=2).rows[0]
    row.cells[0].text, row.cells[1].text = "Squid Optics Seminar Leader (BIOL 412)", "3 hrs, 40 students"
    doc.save(str(tmp_path / "DOC_wcm.docx"))
    out, _ = rc.write_review_docx(tmp_path / "DOC_wcm.docx", _report(
        _finding("enrichment_failures", "1 failed", ["Squid Optics Seminar Leader (BIOL 412) 3 hrs, 40 students"])))
    assert _comments(out) == [(_flag("enrichment_failures"), "Squid Optics Seminar Leader (BIOL 412)")]


def test_a_dedup_drop_led_by_its_entry_number_and_cut_at_the_doctors_length(tmp_path):
    """Current reports lead with "entry N: " (which the run page files as a
    note) and cut each text at DEDUP_TEXT_CHARS; the box marks the cut."""
    kept = ("Squid Optics Seminar Leader, Example School of Marine Biology, eight lectures yearly; " * 5)[:rc.DEDUP_TEXT_CHARS]
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(_finding("dedup_drops", "1 drop", [
        f"entry 12: K1 (jaccard=1.00, 89% covered by kept): dropped 'Squid Optics Seminar' vs kept '{kept}'"])))
    assert _notes(out)[2:] == ["\u2022\tDidactic Teaching", 'Removed:\t"Squid Optics Seminar"', f'Kept:\t"{kept}\u2026"']


def test_a_dedup_quote_the_doctor_cut_still_reads(tmp_path):
    """The run page marks a quote cut at a doctor cap with an ellipsis."""
    evidence = ("K1 (jaccard=0.92, 86% covered by kept): dropped 'Core Squid Curriculum b Lecturer' "
                "vs kept 'Core Squid Curriculum b Coordinator, Lecturer, Workshop Facilitator, Squid Opti'")
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("dedup_drops", "1 dedup drop(s)", [evidence + "\u2026"])))
    assert _notes(out)[3] == 'Removed:\t"Core Squid Curriculum b Lecturer"'


def test_protected_data_flag_never_carries_the_finding_text(tmp_path):
    """The flag is the fixed wording alone: nothing from the finding, which
    names a category and a section, reaches the comment."""
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("protected_data_in_output", "protected personal data (children / dependents) found in "
                 "Appendix -- value withheld from this finding", severity="ERROR"),
        _finding("protected_data_in_output", "a bare date found in the Personal Data block", severity="ERROR")))
    assert _comments(out) == []
    assert _notes(out) == ["Protected personal data in document (2)", _flag("protected_data_in_output"),
                           "\u2022\tAppendix: children / dependents", "\u2022\tPersonal Data: a bare date"]


def test_info_and_skipped_findings_get_no_comment(tmp_path):
    clean = _clean_docx(tmp_path)
    skipped = {**_finding("segmentation", "skipped"), "status": "skipped"}
    assert rc.write_review_docx(clean, _report(
        _finding("missed_headers", "1 header", severity="INFO"), skipped)) is None
    assert not (tmp_path / f"DOC{REVIEW_DOCX_SUFFIX}").exists()


def test_stray_text_is_titled_plainly_and_quotes_what_to_delete(tmp_path):
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("output_hygiene", "1 boilerplate line(s) rendered in the appendix", ["Insert dates here (MM/YYYY)"])))
    assert _notes(out) == ["Stray text to delete (1)", _flag("output_hygiene"), '\u2022\t"Insert dates here (MM/YYYY)"']


def test_review_notes_box_closes_the_document_in_its_own_type_and_spacing(tmp_path):
    """One table, last in the document (deleting it is one step); the space
    above it sits on the last CV paragraph, not a blank one; full width, 6/8pt
    padding, a 9pt gray small-caps title and 10pt text."""
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("enrichment_failures", "1 failed", ["99. Nowhere AB. Unprinted paper. J Example. 2001."])))
    doc = Document(str(out))
    body = [el for el in doc.element.body if el.tag != qn("w:sectPr")]
    assert body[-1].tag == qn("w:tbl")
    last_text = doc.paragraphs[-1]
    assert last_text.text == APPENDIX_LINES[-1] and last_text.paragraph_format.space_after.pt == 6
    tbl_pr = doc.tables[-1]._tbl.tblPr
    assert (tbl_pr.find(qn("w:tblW")).get(qn("w:type")), tbl_pr.find(qn("w:tblW")).get(qn("w:w"))) == ("pct", "5000")
    assert {e.tag.split("}")[1]: e.get(qn("w:w")) for e in tbl_pr.find(qn("w:tblCellMar"))} == {
        "top": "120", "bottom": "120", "left": "160", "right": "160"}
    cell = doc.tables[-1].cell(0, 0)
    assert cell._tc.tcPr.find(qn("w:shd")).get(qn("w:fill")) == CVICHE_BOX_FILL
    title, group, instruction, item = cell.paragraphs[:4]
    assert title.text == rc.REVIEW_NOTES_TITLE and title.runs[0].font.small_caps
    assert title.runs[0].font.size.pt == 9 and str(title.runs[0].font.color.rgb) == "595959"
    assert group.runs[0].bold and group.runs[0].font.size.pt == 10
    assert group.paragraph_format.space_before.pt == 0  # the first group
    assert instruction.runs[0].font.size.pt == 10 and not instruction.runs[0].bold
    assert item.paragraph_format.first_line_indent < 0  # hanging bullet
    assert {r.font.name for p in cell.paragraphs for r in p.runs} == {"Arial"}


def test_removed_and_kept_are_parallel_italic_labels_with_hanging_quotes(tmp_path):
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(_finding("dedup_drops", "1 drop", [
        "K1 (jaccard=1.00, 89% covered by kept): dropped 'Squid Lecture 5 hrs' vs kept 'Squid Lecture 4 hrs'"])))
    removed, kept = Document(str(out)).tables[-1].cell(0, 0).paragraphs[-2:]
    for para, label in ((removed, "Removed:\t"), (kept, "Kept:\t")):
        assert para.runs[0].text == label and para.runs[0].italic
        assert para.paragraph_format.first_line_indent < 0
    assert removed.paragraph_format.space_after.pt == 0 and removed.paragraph_format.keep_with_next
    assert kept.paragraph_format.space_after.pt == 3


def test_no_review_copy_when_nothing_has_a_place_or_an_item(tmp_path):
    """A run whose only findings point at nothing gets no review copy: an
    empty notes box would be one more thing to delete."""
    assert rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("llm_fallback_served", "stage 4 S8: the content filter blocked the primary model"))) is None
    assert not (tmp_path / f"DOC{REVIEW_DOCX_SUFFIX}").exists()


def test_one_lints_comments_are_capped(tmp_path):
    many = [_finding("output_hygiene", f"leak {i}", [CITATION]) for i in range(rc.MAX_FLAGS_PER_LINT + 5)]
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(*many, _finding("pipe_leaks", "one", [SECOND])))
    assert len(_comments(out)) == rc.MAX_FLAGS_PER_LINT + 1


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
