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


def test_every_known_lint_has_a_plain_english_line():
    assert set(KNOWN_LINTS) <= set(rqr.LINT_EXPLANATIONS)


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
    assert (group.prevalence, group.message) == (None, "Something new")


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
    assert nothing.model_dump(exclude={"run_id", "provisional", "dimensions"}) == {
        k: None for k in nothing.model_dump(exclude={"run_id", "provisional", "dimensions"})}
