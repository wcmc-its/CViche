"""The review copy: the run doctor's WARN/ERROR findings flagged in place (#1388 C).

Every document here is built in the test from invented text; no corpus CV.
"""
import copy
import os
import sys
from pathlib import Path

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

import pytest  # noqa: E402
from docx import Document  # noqa: E402
from docx.oxml.ns import qn  # noqa: E402

from app.services import review_comments as rc  # noqa: E402
from app.services.artifact_service import REVIEW_DOCX_SUFFIX  # noqa: E402
from app.services.run_quality_report import LINT_COPY  # noqa: E402
from unified_pipeline.doctor.blind_spots import blind_spots  # noqa: E402
from unified_pipeline.doctor.precision import LintPrecision  # noqa: E402
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
    """The review-notes box's lines under its title, up to its closing
    what-is-not-checked group."""
    lines = _box_lines(path, rc.REVIEW_NOTES_TITLE)
    return lines[:lines.index(rc.NOT_CHECKED_TITLE)]


def _not_checked(path: Path) -> list[str]:
    """The review-notes box's closing group: what CViche does not check."""
    lines = _box_lines(path, rc.REVIEW_NOTES_TITLE)
    return lines[lines.index(rc.NOT_CHECKED_TITLE):]


@pytest.fixture(autouse=True)
def _unmeasured_ledger(monkeypatch):
    """Every lint unmeasured unless a test passes its own ledger rows, so these
    tests read placement, not PRECISION.md's current figures."""
    monkeypatch.setattr(rc, "load_gate_ledger", dict)


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


def _cv_blocks(path: Path) -> list:
    """The blocks the doctor reads, without the review-notes box closing a copy."""
    blocks = read_docx_blocks(str(path))
    closing = blocks[-1] if blocks else None
    if closing and closing[0] == "table" and closing[1].startswith(rc.REVIEW_NOTES_TITLE):
        return blocks[:-1]
    return blocks


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
    assert _cv_blocks(out) == read_docx_blocks(str(clean))


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


def test_an_appendix_diversion_is_a_comment_on_its_sections_heading(tmp_path):
    """#1589: the count goes on the heading of the section the entries were
    meant for, not on any entry (the Appendix group headings name where each
    came from). The Appendix's own code is stage 6's Appendix note's to explain."""
    out, n = rc.write_review_docx(_with_appendix_note(tmp_path), _report(*_DIVERSIONS))
    assert n == 1 and _notes(out) == []
    assert _comments(out) == [
        ("2 entries meant for this section are in the Appendix: move any that belong here.",
         PAST_FUNDING_HEADING)]


def test_a_single_diverted_entry_reads_in_the_singular(tmp_path):
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(_finding(
        "stage6_render_warnings", "stage 6 self-check: M2B: 1 entry diverted to the Appendix "
        "— declined by the research-support renderer as too sparse to table")))
    assert [text for text, _ in _comments(out)] == [
        "1 entry meant for this section is in the Appendix: move any that belong here."]


def test_a_diversion_with_no_heading_to_sit_on_is_a_note(tmp_path):
    """#1639: a diversion whose section has no heading in this document, or
    that names no section, is listed in the box rather than vanishing."""
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("stage6_render_warnings", "stage 6 self-check: K1: 2 entries diverted to the "
                 "Appendix — declined by the renderer"),  # no Didactic Teaching heading
        _finding("stage6_render_warnings", "stage 6 self-check: 1 entry diverted to the "
                 "Appendix — no section named")))
    assert n == 2 and _comments(out) == []
    assert _notes(out) == [f"{rc.DIVERSION_TITLE} (1)", rc.DIVERSION_NOTE, "•\tDidactic Teaching",
                           f"{rc.DIVERSION_TITLE} (1)", rc.DIVERSION_UNPLACED_NOTE]


def test_a_less_certain_finding_with_nothing_to_name_is_still_a_note(tmp_path):
    """#1639: off the text, the box is the only place the copy says it."""
    rows = {("llm_fallback_served", None): LintPrecision("llm_fallback_served", 0, 3, "M1")}
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("llm_fallback_served", "stage 4 S8: the content filter blocked the primary model")),
        rows=rows)
    assert n == 1 and _comments(out) == []
    assert _notes(out) == [f"{rc._note_title('llm_fallback_served')} (1)",
                           f"{_flag('llm_fallback_served')} {rc.LESS_CERTAIN_NOTE}"]


_GATE_ROWS = {
    ("pipe_leaks", None): LintPrecision("pipe_leaks", 0, 2, "M1"),
    ("duplicate_records", None): LintPrecision("duplicate_records", 9, 10, "M1"),
    ("stage6_render_warnings", "appendix_grant_too_sparse"):
        LintPrecision("stage6_render_warnings", 1, 6, "M1", "appendix_grant_too_sparse"),
    ("citation_grounding", None): LintPrecision("citation_grounding", 1, 4, "YUY-CG"),
}


def test_a_finding_right_less_than_half_the_time_is_a_note_not_a_comment(tmp_path):
    """#1589's gate: below 50% hand-checked precision, nothing sits on the
    text; the finding is listed in the box, saying why, and quoting it."""
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("pipe_leaks", "1 numbered citation(s) fusing venue-date patterns", [f"[bibliography] {SECOND}"]),
        _finding("duplicate_records", "1 duplicated record(s)", [f"block 7 repeats at 8: {CITATION[:90]}"])),
        rows=_GATE_ROWS)
    assert n == 2
    assert _comments(out) == [(_flag("duplicate_records"), CITATION)]
    assert _notes(out) == [f"{LINT_COPY['pipe_leaks'].title} (1)",
                           f"{_flag('pipe_leaks')} {rc.LESS_CERTAIN_NOTE}", f'\u2022\t"[bibliography] {SECOND}"']


def test_a_less_certain_diversion_is_a_note_naming_its_section(tmp_path):
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(_DIVERSIONS[0]), rows=_GATE_ROWS)
    assert n == 1 and _comments(out) == []
    assert _notes(out)[1:] == [f"{rc.DIVERSION_NOTE} {rc.LESS_CERTAIN_NOTE}", "\u2022\tPast Research Funding"]


def test_a_less_certain_appendix_code_diversion_is_not_a_note_either(tmp_path):
    """T's own entries are stage 6's Appendix note's to explain, gated or not."""
    rows = {**_GATE_ROWS, ("stage6_render_warnings", "appendix_no_route_T"):
            LintPrecision("stage6_render_warnings", 0, 9, "M1", "appendix_no_route_T")}
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(_DIVERSIONS[1]), rows=rows)
    assert n == 0 and _notes(out) == []


def test_a_less_certain_possibility_is_dropped_not_noted(tmp_path):
    """A REVIEW_COPY_ONLY_LINTS lint is only ever a comment on its citation."""
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("citation_grounding", "entry 14 (S1): author_2:initials_not_in_source", [SECOND[4:]],
                 severity="INFO")), rows=_GATE_ROWS)
    assert n == 0 and _comments(out) == [] and _notes(out) == []


def test_the_committed_ledger_is_read_when_no_rows_are_given(tmp_path, monkeypatch):
    """Without ``rows`` the gate reads PRECISION.md through precision.py."""
    monkeypatch.setattr(rc, "load_gate_ledger", lambda: _GATE_ROWS)
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("pipe_leaks", "1 numbered citation(s)", [f"[bibliography] {SECOND}"])))
    assert _comments(out) == []


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
    out, n = rc.write_review_docx(clean, _report(
        _finding("missed_headers", "1 header", severity="INFO"), skipped))
    assert n == 0 and _comments(out) == [] and _notes(out) == []


def test_a_citation_grounding_finding_is_a_comment_on_its_citation_though_info(tmp_path):
    """#1570 at 50% precision: never a run-page row, but a possibility
    commented on the citation it names, whatever its severity."""
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("citation_grounding", "entry 14 (S1): author_2:initials_not_in_source -- the stage 5d "
                 "citation names text its source line lacks", [SECOND[4:]], severity="INFO"),
        # Another lint's INFO finding quoting a line that IS in the document:
        # the exception is this lint's, so this one still gets no comment.
        _finding("owner_attribution", "entry 12 (S1): 1 publication(s) never name the owner",
                 [CITATION[4:]], severity="INFO")))
    assert n == 1
    assert _comments(out) == [(_flag("citation_grounding"), SECOND)]
    assert "may" in _flag("citation_grounding")


def test_a_citation_grounding_finding_not_in_the_document_flags_nothing(tmp_path):
    """No heading comment, no review note: a possibility only reads on the citation."""
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("citation_grounding", "entry 3 (S8): ordinal_not_in_source:10th -- the stage 5d "
                 "citation names text its source line lacks",
                 ["Quill A. A talk nobody printed. 10th Annual Meeting; 2004."], severity="INFO")))
    assert n == 0 and _comments(out) == [] and _notes(out) == []


def test_a_source_line_coverage_finding_is_one_review_note_per_line_though_info(tmp_path):
    """#1588: the quoted source lines are not on the page, so each one is a
    review note under its own title, never a comment, whatever the severity."""
    lost = ("Visiting lecturer in comparative squid anatomy, Example Polytechnic",
            "Organised the annual squid optics colloquium for graduate students")
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("source_line_coverage", "entry 31: 2 source line(s) with under 50% of their word pairs "
                 "anywhere in the output, Appendix included (lowest 0%)", lost, severity="INFO")))
    assert n == 2 and _comments(out) == []
    assert _notes(out) == ["Text from your CV that may be missing (2)", _flag("source_line_coverage"),
                           *(f'•\t"{line}"' for line in lost)]


def test_a_source_line_coverage_finding_below_the_bar_is_still_its_notes(tmp_path):
    """Its lines are notes already, never on the text, so the precision gate
    has nothing to move: a measured precision under 50% (PRECISION.md, YUY-SLC)
    must not drop them as it drops a less certain possibility."""
    lost = ("Visiting lecturer in comparative squid anatomy, Example Polytechnic",)
    rows = {("source_line_coverage", None): LintPrecision("source_line_coverage", 1, 4, "T")}
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("source_line_coverage", "entry 31: 1 source line(s) with under 50% of their word pairs "
                 "anywhere in the output, Appendix included (lowest 0%)", lost, severity="INFO")), rows=rows)
    assert n == 1 and _comments(out) == []
    assert _notes(out) == ["Text from your CV that may be missing (1)", _flag("source_line_coverage"),
                           f'•\t"{lost[0]}"']


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
    paragraphs = Document(str(out)).tables[-1].cell(0, 0).paragraphs
    at = next(i for i, p in enumerate(paragraphs) if p.text.startswith("Removed:"))
    removed, kept = paragraphs[at:at + 2]
    for para, label in ((removed, "Removed:\t"), (kept, "Kept:\t")):
        assert para.runs[0].text == label and para.runs[0].italic
        assert para.paragraph_format.first_line_indent < 0
    assert removed.paragraph_format.space_after.pt == 0 and removed.paragraph_format.keep_with_next
    assert kept.paragraph_format.space_after.pt == 3


def test_a_copy_with_nothing_to_flag_is_written_and_says_what_is_not_checked(tmp_path):
    """#1589: a quiet doctor is not a clean document, so every run gets its
    copy, closing with what CViche cannot see."""
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("llm_fallback_served", "stage 4 S8: the content filter blocked the primary model")))
    assert out == tmp_path / f"DOC{REVIEW_DOCX_SUFFIX}" and n == 0
    assert _comments(out) == [] and _notes(out) == []
    assert _not_checked(out) == [rc.NOT_CHECKED_TITLE, rc.NOT_CHECKED_INSTRUCTION,
                                 *(f"\u2022\t{spot.sentence}" for spot in blind_spots())]
    assert len(_not_checked(out)) > 2  # COVERAGE.md lists at least one blind spot


def test_what_is_not_checked_closes_a_box_that_has_notes(tmp_path):
    out, _ = rc.write_review_docx(_clean_docx(tmp_path), _report(
        _finding("output_hygiene", "1 boilerplate line(s)", ["Insert dates here (MM/YYYY)"])))
    lines = _box_lines(out, rc.REVIEW_NOTES_TITLE)
    assert lines[0] == "Stray text to delete (1)"
    assert lines[-len(blind_spots()) - 2:] == _not_checked(out)


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
    assert _cv_blocks(out) == read_docx_blocks(str(clean))
    assert len(list(Document(str(clean)).comments)) == 0  # the clean document is untouched


def test_not_a_doctor_report_writes_nothing(tmp_path):
    assert rc.write_review_docx(_clean_docx(tmp_path), None) is None


def test_source_line_coverage_notes_are_capped_per_document(tmp_path):
    """Up to 3 lines a finding, up to 57 findings a run on YUYVIG: the notes
    stop at MAX_FLAGS_PER_LINT, as a comment-placing lint's flags do."""
    findings = [_finding("source_line_coverage", f"entry {i}: 3 source line(s) with under 50% of their "
                         "word pairs anywhere in the output, Appendix included (lowest 0%)",
                         [f"Squid optics seminar number {i} line {j} for graduate students" for j in range(3)])
                for i in range(20)]
    out, n = rc.write_review_docx(_clean_docx(tmp_path), _report(*findings))
    assert n == rc.MAX_FLAGS_PER_LINT
    assert _notes(out)[0] == f"Text from your CV that may be missing ({rc.MAX_FLAGS_PER_LINT})"
