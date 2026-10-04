"""app.services.run_quality_report: cap source, doctor grouping, owner flag."""
import pytest

from unified_pipeline import quality_score as scorer
from unified_pipeline.run_doctor import KNOWN_LINTS, LINT_PREVALENCE, lint_surprise

from app.services import quality_score_service as qss
from app.services import run_quality_report as rqr

OWNER_GATE = "CV owner name / contact populated (HARD-FAIL gate)"


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
    assert all(s.lint in KNOWN_LINTS for s in rqr.CAP_SOURCE_BY_GATE_NAME.values())


def test_cap_source_parses_the_real_scorers_flag_text(tmp_path):
    """Fixtures above hand-copy the flag format; this runs the real scorer so a
    reworded flag f-string fails here instead of silently nulling cap_reason."""
    snapshot = qss.parse_score(scorer.score_run(tmp_path, "T1"))  # empty dir: no output
    source = rqr.cap_source(snapshot)
    assert source is not None
    assert source == rqr.CAP_SOURCE_BY_GATE_NAME[
        "No rendered output produced at all (HARD-FAIL gate)"]


@pytest.mark.parametrize("gate, lint", [
    (scorer.score_under_extracted_records, "under_extraction"),
    (scorer.score_fused_entries, "segmentation"),
    (scorer.score_lost_source_table, "table_lost"),
])
def test_each_content_loss_cap_points_at_the_lint_that_reports_it(gate, lint):
    """#822: a content-loss cap on the run page names its own gate and the doctor
    lint to read next, recovered from the flag text the scorer really writes."""
    name = next(n for n, fn in scorer.CAP_ONLY_GATES if fn is gate)
    flag = f"HARD-FAIL cap={scorer.CONTENT_LOSS_CAP}: {name} (detail)"
    snapshot = qss.parse_score(_score(total=scorer.CONTENT_LOSS_CAP, raw=92.0,
                                      caps=(scorer.CONTENT_LOSS_CAP,), flags=[flag]))
    source = rqr.cap_source(snapshot)
    assert source is not None and source.lint == lint


def test_cap_source_names_the_stage4_group_failure_gate_from_the_real_scorer(tmp_path):
    """#1174: a run that would score exactly 85 GREEN but for one failed
    stage-4 extraction group is capped at 84, and the report names that gate
    and its doctor lint (run through the real scorer, not a hand-copied flag)."""
    import json
    from docx import Document

    (tmp_path / "T1_fields.json").write_text(json.dumps({
        "cv_owner": {"full_name": "Jane Q. Public"},
        "cv_owner_location": {"inference_success": True, "primary_location": "NY"},
        "stats": {"failed_batches": 1},
        "entries": [{"taxonomy_code": "P", "extraction_success": True,
                     "extraction_error": "llm_response_invalid",
                     "extracted_fields": {"email": "j@x.org"}}]}))
    (tmp_path / "T1_classified.json").write_text(json.dumps({"meta": {
        "total_entries": 4, "duplicate_entries": 0, "code_distribution": {"A": 3, "T": 1}}}))
    (tmp_path / "T1_entries.json").write_text(json.dumps({"coverage": {"coverage_percentage": 100}}))
    doc = Document()
    doc.add_paragraph("clean")
    row = doc.add_table(rows=1, cols=2).rows[0]
    row.cells[0].text, row.cells[1].text = "a", "b"
    doc.save(tmp_path / "T1_wcm.docx")

    result = scorer.score_run(tmp_path, "T1")
    source = rqr.cap_source(qss.parse_score(result))

    assert (result["raw_score_before_caps"], result["totalScore"]) == (85.0, 84)
    assert source == rqr.CapSource(
        "field extraction failed for a group of entries", "stage4_group_failures")
    assert source in rqr.CAP_SOURCE_BY_GATE_NAME.values()


@pytest.mark.parametrize("fired, lint", [
    ((scorer.score_fused_entries, scorer.score_stage4_group_failures), "segmentation"),
    ((scorer.score_under_extracted_records, scorer.score_fused_entries), "segmentation"),
    ((scorer.score_lost_source_table, scorer.score_under_extracted_records), "table_lost"),
    ((scorer.score_lost_source_table, scorer.score_fused_entries), "table_lost"),
    ((scorer.score_under_extracted_records, scorer.score_stage4_group_failures),
     "under_extraction"),
    ((scorer.score_stage4_group_failures, scorer.score_llm_fallback_served),
     "stage4_group_failures"),
    ((scorer.score_fused_entries, scorer.score_llm_fallback_served), "segmentation"),
])
def test_when_gates_tie_at_the_same_cap_the_pointer_names_the_most_specific(fired, lint):
    """#822: the content-loss caps and the stage-4 cap all sit at 84, and the
    pointer is the first matching flag. Flags are written in CAP_ONLY_GATES
    order, as score_run writes them, so this pins that order."""
    assert scorer.CONTENT_LOSS_CAP == scorer.STAGE4_GROUP_FAILURE_CAP
    cap = scorer.CONTENT_LOSS_CAP
    flags = [f"HARD-FAIL cap={cap}: {name} (detail)"
             for name, fn in scorer.CAP_ONLY_GATES if fn in fired]
    assert len(flags) == 2
    snapshot = qss.parse_score(_score(total=cap, raw=92.0, caps=(cap, cap), flags=flags))
    source = rqr.cap_source(snapshot)
    assert source is not None and source.lint == lint


def test_a_run_with_fused_entries_and_a_failed_stage4_group_points_at_the_fused_entries(tmp_path):
    """The same tie through the real scorer (#822, #1174): two fused entries and
    one failed stage-4 group both cap at 84; the report names the fused-entries
    gate, not the stage-4 one."""
    import json
    from docx import Document

    fused = {"element_type": "table_row", "text": "\n".join(
        f"Example Grant {i} Title Words Here | Example Agency | 2011-2014 | Role: PI"
        for i in range(3))}
    (tmp_path / "T1_fields.json").write_text(json.dumps({
        "cv_owner": {"full_name": "Jane Q. Public"},
        "cv_owner_location": {"inference_success": True, "primary_location": "NY"},
        "stats": {"failed_batches": 1},
        "entries": [{"taxonomy_code": "P", "extraction_success": True,
                     "extraction_error": "llm_response_invalid",
                     "extracted_fields": {"email": "j@x.org"}}]}))
    (tmp_path / "T1_classified.json").write_text(json.dumps({"meta": {
        "total_entries": 4, "duplicate_entries": 0, "code_distribution": {"A": 3, "T": 1}}}))
    (tmp_path / "T1_entries.json").write_text(json.dumps({
        "coverage": {"coverage_percentage": 100}, "entries": [fused, fused]}))
    doc = Document()
    doc.add_paragraph("clean")
    row = doc.add_table(rows=1, cols=2).rows[0]
    row.cells[0].text, row.cells[1].text = "a", "b"
    doc.save(tmp_path / "T1_wcm.docx")

    result = scorer.score_run(tmp_path, "T1")
    source = rqr.cap_source(qss.parse_score(result))

    assert [f.split(": ")[1].split(" (")[0].split(":")[0] for f in result["flags"]] == [
        "Source records fused", "Stage-4 extraction group failed"]
    assert result["hard_fail_caps_applied"] == [84, 84] and result["totalScore"] == 84
    assert source == rqr.CapSource(
        "several records were fused into one entry", "segmentation")


def test_cap_source_names_the_fallback_served_gate_from_the_real_scorer(tmp_path):
    """#1174: the same 85 GREEN run with one fallback-served stage-4 group is
    capped at 84, and the report names that gate and its doctor lint."""
    import json
    from docx import Document

    (tmp_path / "T1_fields.json").write_text(json.dumps({
        "cv_owner": {"full_name": "Jane Q. Public"},
        "cv_owner_location": {"inference_success": True, "primary_location": "NY"},
        "entries": [{"taxonomy_code": "S1", "extraction_success": True,
                     "llm_fallback_model": "example.fallback-model-1",
                     "extracted_fields": {"email": "j@x.org"}}]}))
    (tmp_path / "T1_classified.json").write_text(json.dumps({"meta": {
        "total_entries": 4, "duplicate_entries": 0, "code_distribution": {"A": 3, "T": 1}}}))
    (tmp_path / "T1_entries.json").write_text(json.dumps({"coverage": {"coverage_percentage": 100}}))
    doc = Document()
    doc.add_paragraph("clean")
    row = doc.add_table(rows=1, cols=2).rows[0]
    row.cells[0].text, row.cells[1].text = "a", "b"
    doc.save(tmp_path / "T1_wcm.docx")

    result = scorer.score_run(tmp_path, "T1")
    source = rqr.cap_source(qss.parse_score(result))

    assert (result["raw_score_before_caps"], result["totalScore"]) == (85.0, 84)
    assert source == rqr.CapSource("a backup model answered part of the run", "llm_fallback_served")


def test_every_known_lint_has_plain_wording():
    assert set(KNOWN_LINTS) <= set(rqr.LINT_COPY)


def test_every_scorer_row_has_plain_wording():
    names = [n for n, _w, _f in scorer.DIMENSIONS] + [n for n, _f in scorer.CAP_ONLY_GATES]
    assert set(names) == set(rqr.ROW_COPY_BY_GATE_NAME)


def test_dimension_rows_carry_their_wording_and_whether_they_can_cap():
    raw = _score(total=90, raw=90.0, caps=[], flags=[])
    raw["dimensionScores"] = [
        {"name": OWNER_GATE, "score": 15.0, "max": 15},
        {"name": "Duplicate-entry ratio (de-dup / fragmentation health)", "score": 8.0, "max": 10},
        {"name": "Duplicate entries", "score": 8.0, "max": 10},  # an older scorer's name
    ]

    dims = rqr.build_run_quality_report("R1", raw, None).dimensions

    assert [(d.label, d.can_cap) for d in dims] == [
        ("Faculty name and contact", True), ("No duplicate entries", False), (None, False)]
    assert dims[1].if_lost.startswith("Check that repeated entries")


def test_fired_zero_weight_gates_are_listed_and_weighted_caps_are_not():
    flags = [
        f"HARD-FAIL cap=25: {OWNER_GATE} (fraction=1.00)",
        "HARD-FAIL cap=84: Source table lost before extraction (CAP-ONLY gate) (worst=6)",
        "HARD-FAIL cap=84: Call served by the content-filter fallback model (caps below GREEN) (x)",
        "HARD-FAIL cap=99: unknown gate",
    ]
    report = rqr.build_run_quality_report("R1", _score(caps=[25, 84, 84], flags=flags), None)

    assert [(g.label, g.cap, g.lint) for g in report.gates_fired] == [
        ("Source tables read in full", 84, "table_lost"),
        ("Usual AI model used throughout", 84, "llm_fallback_served"),
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
        "quotes": ["Visiting Instructor of Pathology, Lakeside Hospital Institute, 2006-2008"]}


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
