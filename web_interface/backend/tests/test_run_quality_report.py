"""app.services.run_quality_report: cap source, doctor grouping, owner flag."""
import pytest

from app.services import quality_score_service as qss
from app.services import run_quality_report as rqr
from unified_pipeline import quality_score as scorer
from unified_pipeline.doctor import precision
from unified_pipeline.doctor.blind_spots import blind_spots
from unified_pipeline.doctor.precision import LintPrecision
from unified_pipeline.run_doctor import KNOWN_LINTS, LINT_PREVALENCE, lint_surprise

OWNER_GATE = "CV owner name / contact populated (HARD-FAIL gate)"
DOCTOR_ROW = "Doctor findings: estimated cleanup, precision-weighted"


def _score(total=25, raw=80.0, caps=(25,), flags=None):
    if flags is None:
        flags = [f"HARD-FAIL cap=25: {OWNER_GATE} (fraction=1.00)"]
    return {
        "totalScore": total, "raw_score_before_caps": raw,
        "hard_fail_caps_applied": list(caps), "flags": flags,
        "dimensionScores": [{"name": "Duplicate entries", "score": 8.0, "max": 10}],
        "data_complete": True,
    }


def _finding(lint, severity="WARN", status="ran", message="instance message"):
    return {"lint": lint, "severity": severity, "message": message, "evidence": [],
            "status": status, "reason": ""}


def test_every_scorer_gate_has_a_cap_source():
    """A gate added to quality_score.py without a row here would cap a score
    with no reason or lint shown."""
    gate_names = [n for n, _w, _f in scorer.DIMENSIONS if _w == 0] + [n for n, _f in scorer.CAP_ONLY_GATES]
    assert gate_names
    assert all(name in rqr.CAP_SOURCE_BY_GATE_NAME for name in gate_names)
    assert OWNER_GATE in rqr.CAP_SOURCE_BY_GATE_NAME
    assert all(s.lint is None or s.lint in KNOWN_LINTS for s in rqr.CAP_SOURCE_BY_GATE_NAME.values())


def _complete_run(tmp_path, *, doctor=True, failed_group=False):
    """A run dir holding every artifact the real scorer reads."""
    import json

    from docx import Document

    fields = {
        "cv_owner": {"full_name": "Jane Q. Public"},
        "cv_owner_location": {"inference_success": True, "primary_location": "NY"},
        "entries": [{"taxonomy_code": "P", "extraction_success": True,
                     "extracted_fields": {"email": "j@x.org"}}]}
    if failed_group:
        fields["stats"] = {"failed_batches": 1}
        fields["entries"][0]["extraction_error"] = "llm_response_invalid"
    (tmp_path / "T1_fields.json").write_text(json.dumps(fields))
    (tmp_path / "T1_classified.json").write_text(json.dumps({"meta": {
        "total_entries": 4, "duplicate_entries": 0, "code_distribution": {"A": 3, "T": 1}}}))
    (tmp_path / "T1_entries.json").write_text(json.dumps({"coverage": {"coverage_percentage": 100}}))
    if doctor:
        (tmp_path / "T1_doctor.json").write_text(json.dumps({"findings": []}))
    doc = Document()
    doc.add_paragraph("clean")
    doc.save(tmp_path / "T1_wcm.docx")
    return tmp_path


def test_an_unchecked_runs_cap_names_the_missing_doctor_report_and_no_lint(tmp_path):
    """#1595/#1593: no doctor report caps the run at 84 through the doctor
    row. The page gives the reason and points at no doctor lint, since there
    is no report to point into."""
    result = scorer.score_run(_complete_run(tmp_path, doctor=False), "T1")
    assert result["totalScore"] == scorer.NOT_CHECKED_CAP

    report = rqr.build_run_quality_report("T1", result, None)

    assert (report.cap, report.cap_reason, report.cap_lint) == (
        84, "the doctor did not check this run", None)
    assert report.band == qss.BAND_YELLOW


def test_cap_source_parses_the_real_scorers_flag_text(tmp_path):
    """Fixtures above hand-copy the flag format; this runs the real scorer so a
    reworded flag f-string fails here instead of silently nulling cap_reason."""
    snapshot = qss.parse_score(scorer.score_run(tmp_path, "T1"))  # empty dir: no output
    source = rqr.cap_source(snapshot)
    assert source is not None
    assert source == rqr.CAP_SOURCE_BY_GATE_NAME[
        "No rendered output produced at all (HARD-FAIL gate)"]


def test_cap_source_names_the_stage4_group_failure_gate_from_the_real_scorer(tmp_path):
    """#1174: a clean, checked run with one failed stage-4 extraction group is
    capped at 84, and the report names that gate and its doctor lint (run
    through the real scorer, not a hand-copied flag)."""
    result = scorer.score_run(_complete_run(tmp_path, failed_group=True), "T1")
    source = rqr.cap_source(qss.parse_score(result))

    assert (result["raw_score_before_caps"], result["totalScore"]) == (100.0, 84)
    assert source == rqr.CapSource(
        "field extraction failed for a group of entries", "stage4_group_failures")
    assert source in rqr.CAP_SOURCE_BY_GATE_NAME.values()


def test_a_fallback_served_call_caps_nothing_through_the_real_scorer(tmp_path):
    """#1174 (Paul, 2026-10-05): a clean, checked run with one fallback-served
    stage-4 group stays 100 GREEN, so the report names no cap."""
    import json

    run = _complete_run(tmp_path)
    fields = json.loads((run / "T1_fields.json").read_text())
    fields["entries"][0]["llm_fallback_model"] = "example.fallback-model-1"
    (run / "T1_fields.json").write_text(json.dumps(fields))

    result = scorer.score_run(run, "T1")
    source = rqr.cap_source(qss.parse_score(result))

    assert (result["raw_score_before_caps"], result["totalScore"]) == (100.0, 100)
    assert result["hard_fail_caps_applied"] == [] and source is None


def test_every_known_lint_has_plain_wording():
    assert not set(KNOWN_LINTS) - rqr.REVIEW_COPY_ONLY_LINTS - set(rqr.LINT_COPY)


def test_review_copy_only_lints_are_known_and_have_no_run_page_wording():
    assert rqr.REVIEW_COPY_ONLY_LINTS.issubset(KNOWN_LINTS)
    assert not rqr.REVIEW_COPY_ONLY_LINTS & set(rqr.LINT_COPY)


def test_every_scorer_row_has_plain_wording():
    names = [n for n, _w, _f in scorer.DIMENSIONS] + [n for n, _f in scorer.CAP_ONLY_GATES]
    assert set(names) == set(rqr.ROW_COPY_BY_GATE_NAME)


def test_dimension_rows_carry_their_wording_and_whether_they_can_cap():
    raw = _score(total=90, raw=90.0, caps=[], flags=[])
    raw["dimensionScores"] = [
        {"name": OWNER_GATE, "score": 15.0, "max": 15},
        {"name": "Doctor findings: estimated cleanup, precision-weighted", "score": 30.0, "max": 40},
        {"name": "Duplicate entries", "score": 8.0, "max": 10},  # an older scorer's name
    ]

    dims = rqr.build_run_quality_report("R1", raw, None).dimensions

    assert [(d.label, d.can_cap) for d in dims] == [
        ("Faculty name and contact", True), ("Problems the checker found", True), (None, False)]
    assert dims[1].if_lost.startswith("Work through the Run Doctor findings")


def test_fired_zero_weight_gates_are_listed_and_weighted_caps_are_not():
    flags = [
        f"HARD-FAIL cap=84: {DOCTOR_ROW} (fraction=0.00)",
        f"HARD-FAIL cap=25: {OWNER_GATE} (fraction=1.00)",
        "HARD-FAIL cap=25: Protected personal data absent from rendered docx (HARD-FAIL gate) (x)",
        # A score cached before #1595 retired the content-loss caps: no row.
        "HARD-FAIL cap=84: Source table lost before extraction (CAP-ONLY gate) (worst=6)",
        # A score cached before #1174 dropped the fallback-served gate: no row.
        "HARD-FAIL cap=84: Call served by the content-filter fallback model (caps below GREEN) (x)",
        "HARD-FAIL cap=99: unknown gate",
    ]
    report = rqr.build_run_quality_report("R1", _score(caps=[84, 25, 25, 84, 84], flags=flags), None)

    assert [(g.label, g.cap, g.lint) for g in report.gates_fired] == [
        ("Faculty name and contact", 25, "owner_contact_missing"),
        ("No protected personal data", 25, "protected_data_in_output"),
    ]


def test_a_clean_run_lists_no_gates():
    clean = rqr.build_run_quality_report("R1", _score(total=95, raw=95.0, caps=[], flags=[]), None)
    assert clean.gates_fired == []


FATAL_GATE = "Pipeline/API errors present (HARD-FAIL gate)"


@pytest.mark.parametrize("doctor_lints, cap_lint", [
    (["stage_failure_recorded"], "stage_failure_recorded"),
    (["stage_failure_recorded", "pipeline_errors_present"], "pipeline_errors_present"),
    (["table_shape"], "pipeline_errors_present"),
])
def test_a_fatal_cap_points_at_stage_failure_recorded_when_that_is_the_finding_shown(
        doctor_lints, cap_lint):
    score = _score(total=40, raw=80.0, caps=[40], flags=[f"HARD-FAIL cap=40: {FATAL_GATE} (fraction=1.00)"])
    doctor = {"findings": [_finding(lint, "ERROR") for lint in doctor_lints]}

    report = rqr.build_run_quality_report("R1", score, doctor)

    assert report.cap_lint == cap_lint
    tied = [g.lint for g in report.doctor.findings if g.caps_score]
    assert tied == ([cap_lint] if cap_lint in doctor_lints else [])


def test_cap_source_is_recovered_from_the_matching_flag():
    src = rqr.cap_source(qss.parse_score(_score()))
    assert src == rqr.CapSource("owner name missing", "owner_contact_missing")


def test_cap_source_none_without_a_binding_cap_or_matching_flag():
    assert rqr.cap_source(qss.parse_score(_score(total=90, raw=90.0, caps=[], flags=[]))) is None
    assert rqr.cap_source(qss.parse_score(_score(flags=["HARD-FAIL cap=25: unknown gate"]))) is None


@pytest.mark.parametrize("cols, expected", [
    (qss.ScoreColumns(None, None, None), False),
    (qss.ScoreColumns(91, "GREEN", None), False),
    (qss.ScoreColumns(80, "YELLOW", None), True),
    (qss.ScoreColumns(40, "RED", 40), True),
    (qss.ScoreColumns(90, "GREEN", 25), True),
])
def test_columns_need_cleanup(cols, expected):
    assert rqr.columns_need_cleanup(cols) is expected


def test_doctor_groups_collapse_instances_and_order_rarest_first():
    payload = {"findings": [
        _finding("output_hygiene", "INFO"), _finding("output_hygiene", "INFO"),
        _finding("output_hygiene", "WARN"),
        _finding("invented_records", "WARN"),
        _finding("table_shape", "INFO"),
    ]}

    report = rqr.summarize_doctor(payload)

    assert [g.lint for g in report.findings] == ["invented_records", "table_shape", "output_hygiene"]
    hygiene = report.findings[2]
    assert (hygiene.count, hygiene.severity) == (3, "WARN")  # worst instance wins
    assert hygiene.prevalence == LINT_PREVALENCE["output_hygiene"]
    surprises = [lint_surprise(g.lint) for g in report.findings]
    assert surprises == sorted(surprises, reverse=True)
    assert (report.counts.error, report.counts.warn, report.counts.info) == (0, 2, 1)


def test_a_review_copy_only_lint_is_no_row_and_no_count_on_the_run_page():
    """citation_grounding (#1570) is right about half the time: Paul,
    2026-10-08, a review-copy comment, never a run-page finding."""
    report = rqr.summarize_doctor({"findings": [
        _finding("citation_grounding", "INFO"), _finding("citation_grounding", "WARN"),
        _finding("table_shape", "INFO"),
    ]})
    assert [g.lint for g in report.findings] == ["table_shape"]
    assert (report.counts.error, report.counts.warn, report.counts.info) == (0, 0, 1)


def test_doctor_unmeasured_lint_has_null_prevalence_and_falls_back_to_the_doctor_message():
    report = rqr.summarize_doctor({"findings": [_finding("brand_new_lint", message="Something new")]})
    group = report.findings[0]
    assert (group.prevalence, group.message, group.title, group.what_to_do) == (
        None, "Something new", None, None)


def test_doctor_rows_carry_the_lint_wording():
    group = rqr.summarize_doctor({"findings": [_finding("under_extraction")]}).findings[0]
    assert (group.title, group.what_to_do) == (
        "Big entry mostly unread", "Compare the entry with the source and add the missing records.")
    assert group.message == rqr.LINT_COPY["under_extraction"].explanation


def test_doctor_not_run_lints_are_counted_not_listed_and_junk_is_skipped():
    report = rqr.summarize_doctor({"findings": [
        _finding("segmentation", "INFO", status="skipped"),
        _finding("table_shape", "INFO", status="unreadable"),
        "junk", {"severity": "WARN"}, _finding("pipe_leaks", "BOGUS"),
        _finding("dead_sections", "WARN"),
    ]})
    assert [g.lint for g in report.findings] == ["dead_sections"]
    assert report.not_run == 2


@pytest.mark.parametrize("payload", [None, [], "x"])
def test_summarize_doctor_none_for_a_non_report(payload):
    assert rqr.summarize_doctor(payload) is None


def test_doctor_instance_names_the_section_and_quotes_the_real_lints_evidence():
    """#1388: "Several records read as one" said "compare the quoted entry" and
    showed no quote. Runs the real lint, so a reworded "entry N (CODE):" prefix
    fails here instead of silently dropping the section."""
    from unified_pipeline.doctor.lints.extraction import lint_multi_record_coverage

    entry = {"taxonomy_code": "D1", "element_idx_start": 7, "extracted_fields": {
                 "title": "Lecturer", "institution": "Northfield University School of Medicine",
                 "start_date": "2001", "end_date": "2005"},
             "text": "Lecturer, Northfield University School of Medicine, 2001-2005 "
                     "Visiting Instructor of Pathology, Lakeside Hospital Institute, 2006-2008"}
    findings = lint_multi_record_coverage(
        {"entries": [entry]}, [("p", "Lecturer | Northfield University School of Medicine | 2001-2005")])

    [instance] = rqr.summarize_doctor({"findings": findings}).findings[0].instances

    assert instance.model_dump() == {
        "severity": "WARN", "section": "Academic Appointments",
        "detail": "2 record-shaped clauses, one stage-4 record; 1 other clause(s) on no line of the output",
        "quotes": ["Visiting Instructor of Pathology, Lakeside Hospital Institute, 2006-2008"], "notes": []}


@pytest.mark.parametrize("message, section, detail", [
    ("taxonomy code M2A: none of its 3 classified entries appear in the output document",
     "Current Research Funding", "none of its 3 classified entries appear in the output document"),
    # An unknown code is not a section: the message keeps its prefix rather than lose it.
    ("entry 4 (ZZ9): one record", None, "entry 4 (ZZ9): one record"),
    ("entry 4: status 'completed' implies M2B (#561)", None, "entry 4: status 'completed' implies M2B"),
    ("protected personal data (date of birth) found in Personal Data",
     None, "protected personal data (date of birth) found in Personal Data"),
])
def test_doctor_instance_section_comes_only_from_a_known_taxonomy_code(message, section, detail):
    [instance] = rqr.summarize_doctor({"findings": [_finding("segmentation", message=message)]}).findings[0].instances
    assert (instance.section, instance.detail) == (section, detail)


def test_doctor_instance_reads_the_section_off_the_real_offschema_message():
    """"3 B1 entries: `x` is outside the B1 schema" names its code after a count."""
    from unified_pipeline.doctor.lints.extraction import lint_offschema_fields

    findings = lint_offschema_fields({"entries": [{
        "taxonomy_code": "B1", "element_idx_start": 3,
        "extracted_fields": {"degree": "MD", "research_mentor": "Dr. Ada Quill"}}]})

    [instance] = rqr.summarize_doctor({"findings": findings}).findings[0].instances

    assert instance.section == "Academic Degrees"
    assert instance.detail.startswith("1 entry: `research_mentor` is outside the B1 schema")
    assert (instance.quotes, instance.notes) == ([], ["entry 3: Dr. Ada Quill"])


def test_doctor_instance_reads_the_section_off_the_real_section_lost_message():
    """"O: 2 entries absent from the INSTITUTIONAL LEADERSHIP section" opens with its code."""
    from unified_pipeline.doctor.lints.render import lint_section_lost

    rows = ("Founding Director, Quillfeather Cellular Therapeutics Institute\t1998-2014",
            "Chairman, Marbleton Steering Committee on Genomic Medicine\t1992-1997")
    blocks = [("p", "INSTITUTIONAL LEADERSHIP ACTIVITIES"), ("p", "Please list activities."),
              ("p", "T. APPENDIX"), *[("p", row) for row in rows]]
    findings = lint_section_lost({"entries": [{"taxonomy_code": "O", "text": t} for t in rows]}, blocks)

    [instance] = rqr.summarize_doctor({"findings": findings}).findings[0].instances

    assert instance.section == "Institutional Leadership"
    assert instance.detail.startswith("2 entries absent from the INSTITUTIONAL LEADERSHIP section")


@pytest.mark.parametrize("warning, section, detail", [
    ({"message": "T: 6 entries diverted to the Appendix"}, "Appendix/Other", "6 entries diverted to the Appendix"),
    ({"message": "K2 (Clinical teaching): Content appears combined with semicolons"},
     "Research Mentoring & Clinical Teaching", "Content appears combined with semicolons"),
    ({"message": "2 D1 entries: `consulting` is outside the D1 schema"},
     "Academic Appointments", "stage 6 self-check: 2 entries: `consulting` is outside the D1 schema"),
    # No known code: the self-check keeps its whole message.
    ({"message": "K (Teaching): No visible bulleted content found"},
     None, "stage 6 self-check: K (Teaching): No visible bulleted content found"),
    ({"message": "hierarchy-mismatch reroute L2->Q2 refused"}, None,
     "stage 6 self-check: hierarchy-mismatch reroute L2->Q2 refused"),
])
def test_doctor_instance_reads_the_section_off_the_real_stage6_self_check(warning, section, detail):
    from unified_pipeline.doctor.lints.render import lint_stage6_warnings

    [instance] = rqr.summarize_doctor(
        {"findings": lint_stage6_warnings({"warnings": [warning]})}).findings[0].instances

    assert (instance.section, instance.detail) == (section, detail)


def test_doctor_instance_splits_its_notes_from_cv_quotes_and_marks_a_cut_quote():
    cut = "x" * 100
    report = rqr.summarize_doctor({"findings": [{**_finding(
        "table_shape", message="missed header: CLINICAL SERVICE"), "evidence": [
            "entry 16", "row 3: name-cell blob (140 chars): Lakeside", "block 4 repeats at 9: Lecture",
            "element_idx_start 43", "D1", "CLINICAL SERVICE", "EDUCATION", cut, cut + "y"]}]})

    [instance] = report.findings[0].instances

    assert instance.notes == ["entry 16", "row 3: name-cell blob (140 chars): Lakeside",
                              "block 4 repeats at 9: Lecture", "element_idx_start 43", "D1"]
    # "CLINICAL SERVICE" is already in the detail; an all-caps CV line is not a code.
    assert instance.quotes == ["EDUCATION", cut + rqr.TRUNCATION_MARK, cut + "y"]


def test_doctor_instances_list_the_worst_first_and_cap_the_list_but_not_the_count():
    infos = [_finding("output_hygiene", "INFO", message=f"info {i}") for i in range(rqr.MAX_INSTANCES_SHOWN)]
    findings = [*infos, _finding("output_hygiene", "WARN", message="the warning")]

    group = rqr.summarize_doctor({"findings": findings}).findings[0]

    assert group.count == rqr.MAX_INSTANCES_SHOWN + 1
    assert len(group.instances) == rqr.MAX_INSTANCES_SHOWN
    assert [i.detail for i in group.instances[:3]] == ["the warning", "info 0", "info 1"]
    assert group.severity == "WARN"


def test_doctor_instance_quotes_skip_blank_and_non_list_evidence():
    report = rqr.summarize_doctor({"findings": [
        {**_finding("table_shape"), "evidence": ["  ", "kept", 3]},
        {**_finding("pipe_leaks"), "evidence": "not a list"},
    ]})
    assert {g.lint: g.instances[0].quotes for g in report.findings} == {
        "table_shape": ["kept", "3"], "pipe_leaks": []}


def test_report_for_a_capped_run_ties_the_cap_lint_to_its_finding():
    doctor = {"findings": [_finding("owner_contact_missing", "ERROR"), _finding("table_shape", "INFO")]}

    r = rqr.build_run_quality_report("R1", _score(), doctor)

    assert (r.score, r.band, r.band_meaning, r.provisional) == (25, "RED", "Don't deliver", True)
    assert (r.cap, r.cap_reason, r.cap_lint, r.earned) == (25, "owner name missing", "owner_contact_missing", 80)
    assert [(d.name, d.weight, d.points) for d in r.dimensions] == [("Duplicate entries", 10, 8.0)]
    assert r.total_weight == 10
    assert {g.lint: g.caps_score for g in r.doctor.findings} == {
        "owner_contact_missing": True, "table_shape": False}


def test_report_degrades_each_part_to_null_independently():
    no_score = rqr.build_run_quality_report("R1", None, {"findings": []})
    assert no_score.score is None and no_score.dimensions == [] and no_score.doctor is not None

    no_doctor = rqr.build_run_quality_report("R1", _score(total=91, raw=91.0, caps=[], flags=[]), None)
    assert no_doctor.band == "GREEN" and no_doctor.cap is None and no_doctor.doctor is None

    nothing = rqr.build_run_quality_report("R1", None, None)
    assert nothing.model_dump(exclude={"run_id", "provisional", "dimensions", "gates_fired"}) == {
        k: None for k in nothing.model_dump(exclude={"run_id", "provisional", "dimensions", "gates_fired"})}


# --- the Fix list (#1589) -----------------------------------------------------


def _ledger(**rows):
    """{lint: (tp, judged)} as the gate's ledger rows, one per lint."""
    return {(lint, None): LintPrecision(lint, tp, judged, "M9") for lint, (tp, judged) in rows.items()}


def _fix_list(findings, rows=None):
    return rqr.summarize_doctor({"findings": findings}, rows=rows or {})


def _fix_list_titles(report):
    return [p.title for g in report.fix_list for i in g.items for p in i.problems]


def test_fix_list_merges_an_entrys_findings_and_quotes_the_real_under_extraction_text():
    """SQMWHM entry 29's shape: under_extraction names the entry but no code;
    another finding on the same entry names the section. Runs the real lint, so
    a reworded "entry N:" prefix fails here instead of splitting the item."""
    from unified_pipeline.doctor.lints.extraction import lint_under_extraction

    lines = [f"Visiting Lecturer in Medicine, Northfield University School of Medicine, {1990 + i}-{1991 + i}"
             for i in range(10)]
    entry = {"element_type": "paragraph", "element_idx_start": 29, "taxonomy_code": "H",
             "text": "\n".join(lines), "extracted_fields": {"title": "Visiting Lecturer"},
             "extraction_coverage": {"extraction_coverage_percent": 13.1}}
    [under] = lint_under_extraction({"entries": [entry]})
    junk = {**_finding("junk_or_header_row", message="entry 29 (H): a lead-in label prints as a record"),
            "evidence": ["Honors:"]}

    report = _fix_list([junk, under])

    [group] = report.fix_list
    assert group.section == "Honors & Awards"
    [item] = group.items
    assert [p.title for p in item.problems] == [
        rqr.LINT_COPY["junk_or_header_row"].title, rqr.LINT_COPY["under_extraction"].title]
    assert item.quotes == ["Honors:", under["evidence"][0] + rqr.TRUNCATION_MARK]


def test_fix_list_item_lists_each_lint_once_worst_first():
    report = _fix_list([
        _finding("junk_or_header_row", message="entry 7 (D1): a"),
        _finding("under_extraction", message="entry 7: b"),
        _finding("under_extraction", "ERROR", message="entry 7: c"),
    ])
    [item] = report.fix_list[0].items
    assert [(p.title, p.severity) for p in item.problems] == [
        (rqr.LINT_COPY["under_extraction"].title, "ERROR"),
        (rqr.LINT_COPY["junk_or_header_row"].title, "WARN")]


def test_fix_list_is_in_document_order():
    """No-section findings first, then template order (D1 before H before S1),
    entries by index within a section, entry-less findings last."""
    report = _fix_list([
        _finding("junk_or_header_row", message="entry 40 (S1): x"),
        _finding("junk_or_header_row", message="entry 9 (H): x"),
        _finding("dead_sections", message="taxonomy code H: empty"),
        _finding("junk_or_header_row", message="entry 3 (H): x"),
        _finding("junk_or_header_row", message="entry 50 (D1): x"),
        _finding("no_output", "ERROR", message="no document"),
    ])
    assert [g.section for g in report.fix_list] == [
        None, "Academic Appointments", "Honors & Awards", "Peer-Reviewed Research Articles"]
    honors = report.fix_list[2].items
    assert [i.problems[0].title for i in honors] == [
        rqr.LINT_COPY["junk_or_header_row"].title, rqr.LINT_COPY["junk_or_header_row"].title,
        rqr.LINT_COPY["dead_sections"].title]


def test_fix_list_holds_back_low_precision_info_and_unworded_findings():
    ledger = _ledger(dedup_drops=(2, 8), junk_or_header_row=(105, 107))
    report = _fix_list([
        _finding("dedup_drops", message="entry 1 (D1): x"),  # 25%: Diagnostics only
        _finding("brand_new_lint", message="entry 2 (D1): x"),  # no plain wording yet
        _finding("table_shape", "INFO", message="entry 3 (D1): x"),  # INFO: not counted
        _finding("junk_or_header_row", message="entry 4 (D1): x"),
    ], ledger)
    assert _fix_list_titles(report) == [rqr.LINT_COPY["junk_or_header_row"].title]
    assert report.fix_list_held_back == 2
    # Diagnostics still lists every lint.
    assert {g.lint for g in report.findings} == {
        "dedup_drops", "brand_new_lint", "table_shape", "junk_or_header_row"}


@pytest.mark.parametrize("tp, judged, confidence", [
    (105, 107, "high"),
    (9, 9, "medium"),  # right every time, but too few checked
    (5, 9, "medium"),
    (0, 0, "unmeasured"),
])
def test_fix_list_confidence_comes_from_the_ledger(tp, judged, confidence):
    report = _fix_list([_finding("junk_or_header_row")], _ledger(junk_or_header_row=(tp, judged)))
    [problem] = report.fix_list[0].items[0].problems
    assert (problem.confidence, problem.effort) == (confidence, rqr.EFFORT_QUICK)


def test_fix_list_carries_no_lint_key_stage_entry_index_or_issue_number():
    report = _fix_list([
        {**_finding("role_consistency",
                    message="entry 1515 (M2A): pi role empty (owner_pi_role_empty, #1403)"),
         "evidence": ["entry 16", "R01 Cardiac Imaging"]},
        _finding("stage6_render_warnings", message="stage 6 self-check: T: 6 entries diverted"),
    ])
    wire = report.model_dump_json(include={"fix_list"})
    for leak in ("role_consistency", "owner_pi_role_empty", "1515", "#1403", "entry 16", "stage 6",
                 "stage6_render_warnings"):
        assert leak not in wire
    assert "R01 Cardiac Imaging" in wire


def test_fix_list_cuts_at_its_cap_and_counts_the_rest(monkeypatch):
    monkeypatch.setattr(rqr, "MAX_FIX_LIST_ITEMS", 2)
    report = _fix_list([_finding("junk_or_header_row", message=f"entry {i} (D1): x") for i in range(5)])
    assert sum(len(g.items) for g in report.fix_list) == 2
    assert report.fix_list_more == 3


def test_every_run_says_what_the_doctor_does_not_check():
    """The review copy's "What CViche does not check" sentences, from
    doctor/COVERAGE.md, so the two views cannot drift."""
    assert _fix_list([]).not_checked == [spot.sentence for spot in blind_spots()] != []


def test_fix_list_gate_is_the_review_copys_per_shape_gate():
    """Two shapes of one lint on the committed gate ledger: owner_attribution's
    uncredited-citation shape is right under half the time and stays in
    Diagnostics; its mentee-heading shape reaches the Fix list. Each finding is
    shown exactly when the review copy would mark it in place."""
    findings = [
        _finding("owner_attribution", message="entry 4 (D1): no owner (citation_without_owner)"),
        _finding("owner_attribution",
                 message="entry 5 (N3): mentees under another heading (mentee_under_non_mentee_heading)"),
    ]
    report = rqr.summarize_doctor({"findings": findings})
    assert [precision.shown_in_place(f["lint"], f["message"]) for f in findings] == [False, True]
    assert sum(len(g.items) for g in report.fix_list) == 1
    assert report.fix_list_held_back == 1


def test_fix_list_reads_held_out_verdicts_folded_in():
    """appendix_recovered_A is 3 / 20 in-sample but 18 / 35 with YUYVIG's
    held-out verdicts: the gate's combined row shows it, at medium confidence.
    appendix_no_route_T stays under half combined (15 / 34) and is held back."""
    recovered = _finding("stage6_render_warnings",
                         message="stage 6 self-check: A: 2 entries recovered into the Appendix")
    no_route = _finding("stage6_render_warnings",
                        message="stage 6 self-check: T: 3 entries, no stage 6 section is routed")
    report = rqr.summarize_doctor({"findings": [recovered, no_route]})
    [problem] = [p for g in report.fix_list for i in g.items for p in i.problems]
    assert problem.title == rqr.LINT_COPY["stage6_render_warnings"].title
    assert problem.confidence == "medium"
    assert report.fix_list_held_back == 1


def test_every_effort_names_a_known_worded_lint():
    assert set(rqr.LINT_EFFORT) <= set(rqr.LINT_COPY) <= set(KNOWN_LINTS)
