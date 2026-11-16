#!/usr/bin/env python3
"""Self-tests for doctor_vs_autopsy.py, on synthetic findings and labels only.

    python3 scripts/test_doctor_vs_autopsy.py

Pins what the scores depend on: which finding formats yield an entry index
(and which must not), which stage-6 message maps to which shape, how hits,
matches, recall and verdict tallies are counted, and that every input the
tool cannot score exits 2 instead of printing a number. For --source-fixed:
which uncaught findings the stage-3b replay calls fixed at source, that an
unreplayable run is counted, and that the flag changes nothing else.
"""
import contextlib
import copy
import io
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import doctor_vs_autopsy as dva  # noqa: E402

UID_A, UID_B, UID_C, UID_D = "TSTAAA", "TSTBBB", "TSTCCC", "TSTDDD"


def _finding(lint, message, evidence=(), severity="WARN", status="ran"):
    return {"lint": lint, "severity": severity, "message": message,
            "evidence": list(evidence), "status": status, "reason": ""}


def _verified(fid, idx, severity="high", class_ref="cls-1", batch_class="E1", credit="no",
              stage="4"):
    return {"id": fid, "class": "whole_record_lost", "class_ref": class_ref,
            "batch_class": batch_class, "stage": stage, "severity": severity, "element_idx_start": idx,
            "records": 1, "doctor_caught": {"verdict": credit, "lints": []}}


def _write_inputs(tmp, reports, labels):
    """`reports` is the doctor_gate.py JSON, or a str written verbatim."""
    doctor = Path(tmp) / "doctor.json"
    doctor.write_text(reports if isinstance(reports, str) else json.dumps(reports))
    labels_dir = Path(tmp) / "labels"
    labels_dir.mkdir()
    for label in labels:
        (labels_dir / f"{label['uid']}.json").write_text(json.dumps(label))
    return doctor, labels_dir


def _run_main(args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = dva.main([str(a) for a in args])
    return code, out.getvalue(), err.getvalue()


def _row(report, key):
    return next(r for r in report["lints"] if r["key"] == key)


def test_finding_idxs_reads_every_emitter_format():
    cases = [
        (_finding("implausible_year", "entry 128 (M2B): end_date=1912 -- before 1959"), {128}),
        (_finding("offschema_fields", "1 D1 entry: `key` is outside the D1 schema",
                  ["entry 136: [{\"title\": \"Synthetic\"}]", "entry 140: {}"]), {136, 140}),
        (_finding("stage6_render_warnings", "stage 6 self-check: hierarchy-mismatch reroute "
                  "K1->S8 refused fields do not fit: 2 entries",
                  ["element_idx_start 43", "element_idx_start 47"]), {43, 47}),
        (_finding("stage6_render_warnings", "stage 6 self-check: P: entry at element 12 dropped "
                  "as a source table header row", ["element_idx_start=12"]), {12}),
        (_finding("wrong_start_date", "entry 30.0 (D1): start_date '2001' with an empty end_date"), {30}),
        (_finding("under_extraction", "entry 474.1: extraction coverage 12% of 900 chars"), {474}),
        (_finding("under_extraction", "entry 474.6: extraction coverage 9% of 900 chars"), {474}),
        (_finding("offschema_fields", "1 D1 entry", ["  entry 8: {}"]), {8}),
        (_finding("some_lint", "flag", ["idx 7"]), {7}),
    ]
    for finding, expected in cases:
        assert dva.finding_idxs(finding) == expected, (finding, dva.finding_idxs(finding))


def test_finding_idxs_ignores_numbers_that_are_not_entry_indices():
    for finding in (
        _finding("duplicate_records", "1 duplicated record(s)", ["block 337 repeats at 338: text"]),
        _finding("table_shape", "honors table: 2/31 row(s) malformed", ["row 25: name-cell blob"]),
        _finding("missed_headers", "header-like line missing from 1a: 'Synthetic entry 5 heading'"),
        _finding("dedup_drops", "1 drop(s)", ["H (jaccard=0.80): dropped 'Award entry 9' vs kept 'x'"]),
        _finding("output_hygiene", "appendix holds 3 unmapped entries"),
    ):
        assert dva.finding_idxs(finding) == frozenset(), finding


def _scoring_inputs(tmp):
    reports = {
        UID_A: {"findings": [
            _finding("offschema_fields", "1 D1 entry: `key` ...", ["entry 10: {}"],
                     "ERROR"),  # matches A-01
            _finding("offschema_fields", "1 H entry: `key` ...", ["entry 99: {}"], "INFO"),  # unmatched
            _finding("dedup_drops", "1 drop(s)", ["H (jaccard=0.9): dropped 'x' vs kept 'y'"]),  # unlocated
            _finding("implausible_year", "entry 21 (C): start_date=1900 -- before 1950"),  # matches A-02
            _finding("section_lost", "skipped: missing stage_4", status="skipped"),  # not a hit
        ]},
        UID_B: {"findings": [
            _finding("stage6_render_warnings", "stage 6 self-check: hierarchy-mismatch reroute "
                     "K1->S8 refused fields do not fit: 1 entry", ["element_idx_start 5"], "INFO"),
        ]},
        UID_C: {"findings": [{"lint": "offschema_fields", "severity": "WARN",  # no status: ran
                              "message": "unlabelled run", "evidence": ["entry 1: {}"]}]},
        dva.FAILED_KEY: {},
    }
    labels = [
        {"uid": UID_A, "batch": "BATCH1",
         "findings": [_verified("TSTAAA-01", [10, 11], credit="yes"),
                      _verified("TSTAAA-02", 21.0, severity="low", class_ref="cls-2", stage="6"),
                      _verified("TSTAAA-03", None, credit=None)],
         "doctor_review": [
             {"lint": "offschema_fields", "shape": None, "severity": "WARN", "verdict": "TP",
              "element_idx_start": None},
             {"lint": "dedup_drops", "shape": None, "severity": "WARN", "verdict": "FP",
              "element_idx_start": None},
             {"lint": "pipe_leaks", "shape": None, "severity": "WARN", "verdict": "FP",
              "element_idx_start": None}]},
        # TSTBBB-02 shares index 99 with an unmatched TSTAAA hit: a hit matches only its own uid
        {"uid": UID_B, "batch": "BATCH2", "findings": [_verified("TSTBBB-01", 6, batch_class=None,
                                                                 stage=None),
                                                       _verified("TSTBBB-02", 99, class_ref="cls-3")],
         "doctor_review": [{"lint": "stage6_render_warnings", "shape": "reroute_refused",
                            "severity": "INFO", "verdict": "partial", "element_idx_start": [5]}]},
        # labelled but absent from this doctor run: never scored
        {"uid": UID_D, "batch": "BATCH2", "findings": [_verified("TSTDDD-01", 3)],
         "doctor_review": [{"lint": "dedup_drops", "shape": None, "severity": "WARN",
                            "verdict": "TP", "element_idx_start": None}]},
    ]
    return _write_inputs(tmp, reports, labels)


def test_report_counts_hits_matches_and_caught_findings_per_lint():
    with tempfile.TemporaryDirectory() as tmp:
        report = dva.build_report(*_scoring_inputs(tmp))
    assert report["runs"] == {"doctored": 3, "labelled": 3, "scored": 2, "labelled_not_doctored": [UID_D]}
    assert report["unscored_hits"] == {"hits": 1, "uids": [UID_C]}
    offschema = _row(report, "offschema_fields")
    assert (offschema["hits"], offschema["loud"], offschema["located"], offschema["matched"]) == (2, 1, 2, 1)
    assert offschema["caught_ids"] == ["TSTAAA-01"]
    assert [list(h) for h in offschema["unmatched_hits"]] == [[UID_A, [99]]]
    assert "TSTBBB-02" not in {cid for r in report["lints"] for cid in r["caught_ids"]}
    dedup = _row(report, "dedup_drops")
    assert (dedup["hits"], dedup["located"], dedup["unlocated_uids"]) == (1, 0, [UID_A])
    assert _row(report, "implausible_year")["caught_ids"] == ["TSTAAA-02"]
    reroute = _row(report, "stage6_render_warnings:reroute_refused")
    assert (reroute["hits"], reroute["loud"], reroute["matched"]) == (1, 0, 0)
    assert "section_lost" not in {r["key"] for r in report["lints"]}


def test_recall_groups_and_no_idx_findings():
    with tempfile.TemporaryDirectory() as tmp:
        recall = dva.build_report(*_scoring_inputs(tmp))["recall"]
    assert recall["overall"] == {"findings": 4, "caught": 2}
    assert recall["no_idx"] == 1
    assert recall["by_severity"] == {"high": {"findings": 3, "caught": 1}, "low": {"findings": 1, "caught": 1}}
    assert recall["by_batch"] == {"BATCH1": {"findings": 2, "caught": 2}, "BATCH2": {"findings": 2, "caught": 0}}
    assert recall["by_class_ref"]["cls-2"] == {"findings": 1, "caught": 1}
    assert recall["by_batch_class"][dva.NO_CLASS] == {"findings": 1, "caught": 0}
    # the stage the verifier blamed: what #1586 ranks misses by; a label without one is "(none)"
    assert recall["by_stage"] == {"4": {"findings": 2, "caught": 1}, "6": {"findings": 1, "caught": 1},
                                  dva.NO_CLASS: {"findings": 1, "caught": 0}}
    assert recall["autopsy_credit"] == {"judged": 4, "yes": 1, "no": 3}


def test_verdicts_are_tallied_and_split_by_whether_the_arm_still_fires():
    with tempfile.TemporaryDirectory() as tmp:
        report = dva.build_report(*_scoring_inputs(tmp))
    assert _row(report, "offschema_fields")["judged"] == {"TP": 1}
    assert _row(report, "offschema_fields")["judged_firing"] == {"TP": 1}
    assert _row(report, "dedup_drops")["judged"] == {"FP": 1}  # TSTDDD's TP is not scored
    assert _row(report, "dedup_drops")["judged_firing"] == {"FP": 1}
    pipe = _row(report, "pipe_leaks")  # judged on the reviewed doctor, silent in this arm
    assert (pipe["hits"], pipe["judged"], pipe["judged_firing"]) == (0, {"FP": 1}, {})
    reroute = _row(report, "stage6_render_warnings:reroute_refused")
    assert reroute["judged_firing"] == {"partial": 1}


def test_main_prints_the_table_and_writes_the_same_numbers_as_json():
    with tempfile.TemporaryDirectory() as tmp:
        doctor, labels_dir = _scoring_inputs(tmp)
        out_json = Path(tmp) / "report.json"
        code, out, _ = _run_main([doctor, labels_dir, "--json", out_json])
        written = json.loads(out_json.read_text())
    assert code == 0
    assert written["recall"]["overall"] == {"findings": 4, "caught": 2}
    line = next(ln for ln in out.splitlines() if ln.startswith("offschema_fields "))
    assert line.split()[1:8] == ["2", "1", "2", "1", "1", "0", "1"], line
    line = next(ln for ln in out.splitlines() if ln.startswith("implausible_year "))
    assert line.split()[1:8] == ["1", "1", "1", "1", "0", "0", "1"], line  # match != unmat
    assert "recall: 2/4 (50%)" in out
    assert "  by_class_ref: 3 smaller groups 2/4 (each in --json)" in out.splitlines(), out
    assert written["recall"]["by_stage"]["6"] == {"findings": 1, "caught": 1}
    assert "  by_stage: 3 smaller groups 2/4 (each in --json)" in out.splitlines(), out


def _unclassed_inputs(tmp):
    """One batch whose findings mostly carry no class and no doctor_caught,
    as the s7ab labels' unassigned findings do."""
    unclassed = {"class_ref": None, "batch_class": None, "doctor_caught": None}
    reports = {UID_A: {"findings": [
        _finding("offschema_fields", "1 D1 entry: `key` ...", ["entry 1: {}"])]}}
    labels = [{"uid": UID_A, "batch": "BATCH1", "findings": [
        {**_verified("TSTAAA-01", 1), **unclassed},
        {**_verified("TSTAAA-02", 2), **unclassed},
        {**_verified("TSTAAA-03", 3), **unclassed},
        _verified("TSTAAA-04", 4, class_ref="cls-1", credit="yes")]}]
    return _write_inputs(tmp, reports, labels)


def test_findings_with_no_class_or_credit_are_grouped_not_dropped():
    with tempfile.TemporaryDirectory() as tmp:
        doctor, labels_dir = _unclassed_inputs(tmp)
        recall = dva.build_report(doctor, labels_dir)["recall"]
        out_json = Path(tmp) / "report.json"
        code, _, err = _run_main([doctor, labels_dir, "--json", out_json])
        written = json.loads(out_json.read_text())["recall"]
    assert recall["by_class_ref"] == {dva.NO_CLASS: {"findings": 3, "caught": 1},
                                      "cls-1": {"findings": 1, "caught": 0}}
    assert recall["by_batch_class"] == {dva.NO_CLASS: {"findings": 3, "caught": 1},
                                        "E1": {"findings": 1, "caught": 0}}
    assert recall["autopsy_credit"] == {"judged": 1, "yes": 1}  # null doctor_caught is not judged
    assert (code, err) == (0, "")
    assert written["by_class_ref"] == recall["by_class_ref"]


def test_recall_text_lists_big_groups_and_sums_the_small_ones():
    with tempfile.TemporaryDirectory() as tmp:
        code, out, _ = _run_main(list(_unclassed_inputs(tmp)))
    lines = out.splitlines()
    assert code == 0
    assert "  by_batch: BATCH1 1/4 (25%)" in lines, out  # nothing small: no remainder clause
    assert ("  by_class_ref: (none) 1/3 (33%); 1 smaller groups 0/1 (each in --json)"
            in lines), out
    assert "  by_severity: high 1/4 (25%)" in lines, out


def test_inputs_that_cannot_be_scored_exit_2():
    label = {"uid": UID_A, "batch": "B", "findings": [_verified("TSTAAA-01", 1)]}
    review = {"lint": "offschema_fields", "shape": None, "severity": "WARN",
              "verdict": "maybe", "element_idx_start": None}
    both = {UID_A: {"findings": []}, UID_B: {"findings": []}}
    cases = {  # why: (doctor reports, labels, what the error must name)
        "doctor_gate failures": (
            {UID_A: {"findings": []}, dva.FAILED_KEY: {UID_A: "boom"}}, [label], "recorded failures"),
        "no doctored run is labelled": ({UID_B: {"findings": []}}, [label], "nothing to score"),
        "label uid differs from its file name": (both, [{**label, "uid": UID_B}], "does not match"),
        "unknown verdict": (both, [{**label, "doctor_review": [review]}], "verdict"),
        "element_idx_start not numeric": (
            both, [{**label, "findings": [_verified("TSTAAA-01", "abc")]}], "not numeric"),
        "finding without element_idx_start": (
            both, [{**label, "findings": [{"id": "TSTAAA-01", "severity": "high"}]}], "malformed"),
        "no label files": (both, [], "no <uid>.json label files"),
        "doctor JSON is not a findings map": ([], [label], "not a doctor_gate.py findings map"),
        "doctor JSON does not parse": ("{truncated", [label], "cannot read"),
        "doctor finding without a lint": (
            {UID_A: {"findings": [{"message": "x", "status": "ran"}]}}, [label], "malformed doctor report"),
    }
    for why, (reports, labels, named) in cases.items():
        with tempfile.TemporaryDirectory() as tmp:
            doctor, labels_dir = _write_inputs(tmp, reports, labels)
            if why == "label uid differs from its file name":
                (labels_dir / f"{UID_B}.json").rename(labels_dir / f"{UID_A}.json")
            code, out, err = _run_main([doctor, labels_dir])
        assert code == dva.EXIT_INPUT_ERROR, (why, code, out)
        assert err.startswith("doctor_vs_autopsy: ") and named in err, (why, err)


# --- --source-fixed -----------------------------------------------------------

TALKS, HONORS = "INVITED PRESENTATIONS", "HONORS AND AWARDS"
REPEATED_TEXT = "Synthetic Teaching Prize, Example Institute, 2019"


def _entry(idx, code, hierarchy=TALKS, text=None, **extra):
    text = text or f"Workshop faculty, Example Society, session {idx}"  # distinct: not a repeat
    return {"element_idx_start": idx, "hierarchy": [hierarchy], "text": text, "taxonomy_code": code,
            "taxonomy_confidence": 0.95, "classification_reasoning": "professional education",
            "classification_source": "llm", **extra}


def _stage_3a():
    """A talks heading stage 3a mapped to R at full confidence: the header pin's smallest case."""
    return {"mappings": [{"title": TALKS, "taxonomy_options": [{"code": "R", "confidence": 1.0}]}]}


def _stage_3b():
    return {"entries": [
        _entry(10.0, "K4"),  # the pin recodes a K4 under a confident R heading
        _entry(11, "R"),  # already R: nothing touches it
        _entry(12, "K4"),  # recoded too, but a lint catches its finding
        _entry(20, "H", HONORS, REPEATED_TEXT),
        _entry(21, "H", HONORS, REPEATED_TEXT.replace("2019", "2020"),  # a sibling, not a repeat
               is_duplicate=True, duplicate_note="stored mark"),
        _entry(30, "H", HONORS, REPEATED_TEXT.replace("Teaching", "Mentoring")),
        _entry(31, "H", HONORS, REPEATED_TEXT.replace("Teaching", "Mentoring"),  # a real repeat
               is_duplicate=True, duplicate_note="stored mark"),
    ]}


def _source_fixed_inputs(tmp):
    """TSTAAA has both artifacts; TSTBBB's stage 3b path names no file; TSTCCC's
    report has no artifacts map at all."""
    stage_3a, stage_3b = Path(tmp) / "3a.json", Path(tmp) / "3b.json"
    stage_3a.write_text(json.dumps(_stage_3a()))
    stage_3b.write_text(json.dumps(_stage_3b()))
    reports = {
        UID_A: {"artifacts": {"stage_3a": str(stage_3a), "stage_3b": str(stage_3b), "stage_4": None},
                "findings": [_finding("implausible_year", "entry 12 (K4): start_date=1900")]},
        UID_B: {"artifacts": {"stage_3a": str(stage_3a), "stage_3b": str(Path(tmp) / "absent.json")},
                "findings": []},
        UID_C: {"findings": []},
    }
    labels = [
        {"uid": UID_A, "batch": "BATCH1", "findings": [
            _verified("TSTAAA-01", 10, stage="3b"), _verified("TSTAAA-02", 11, stage="3b"),
            _verified("TSTAAA-03", 12, stage="3b"), _verified("TSTAAA-04", [20, 21], stage="3b"),
            _verified("TSTAAA-05", 31, stage="4"), _verified("TSTAAA-06", None, stage="3b"),
            _verified("TSTAAA-07", 99, stage="4")]},  # an index the stored run has no entry for
        {"uid": UID_B, "batch": "BATCH1", "findings": [_verified("TSTBBB-01", 10, stage="3b"),
                                                       _verified("TSTBBB-02", 11, stage="3b")]},
        {"uid": UID_C, "batch": "BATCH1", "findings": [_verified("TSTCCC-01", 10, stage=None)]},
    ]
    return _write_inputs(tmp, reports, labels)


def test_replay_stage3b_reports_each_entry_before_and_after_and_changes_no_input():
    stage_3a, stage_3b = _stage_3a(), _stage_3b()
    before = copy.deepcopy((stage_3a, stage_3b))
    replayed = dva.replay_stage3b(stage_3a, stage_3b)
    assert (stage_3a, stage_3b) == before
    assert replayed == {
        10: ("K4", "R", False, False), 11: ("R", "R", False, False), 12: ("K4", "R", False, False),
        20: ("H", "H", False, False), 21: ("H", "H", True, False),
        30: ("H", "H", False, False), 31: ("H", "H", True, True)}
    assert [idx for idx, r in replayed.items() if r.source_fixed] == [10, 12, 21]


def test_replay_stage3b_lets_a_fixed_sub_entry_stand_for_its_index():
    stage_3b = {"entries": [_entry("474.1", "R"), _entry("474.6", "K4"), _entry(None, "K4")]}
    assert dva.replay_stage3b(_stage_3a(), stage_3b) == {474: ("K4", "R", False, False)}


def test_replay_stage3b_runs_the_whole_cv_pins_after_the_header_pin():
    # a consultancy filed as a committee seat, under a heading stage 3a did not map
    stage_3b = {"entries": [_entry(5, "Q2", "SERVICE", "Consultant, Example Pharma Inc., 2018-2020")]}
    assert dva.replay_stage3b(_stage_3a(), stage_3b) == {5: ("Q2", "D3", False, False)}


def test_source_fixed_counts_only_uncaught_findings_the_replay_changes():
    with tempfile.TemporaryDirectory() as tmp:
        split = dva.build_report(*_source_fixed_inputs(tmp), source_fixed=True)["recall"]["source_fixed"]
    # TSTAAA-03 is recoded as well, but a lint caught it; TSTAAA-06 has no index
    assert split["source_fixed_ids"] == ["TSTAAA-01", "TSTAAA-04"]
    assert (split["uncaught"], split["source_fixed"]) == (8, 2)
    assert split["by_stage"] == {"3b": {"uncaught": 5, "source_fixed": 2},
                                 "4": {"uncaught": 2, "source_fixed": 0},
                                 dva.NO_CLASS: {"uncaught": 1, "source_fixed": 0}}


def test_source_fixed_counts_a_run_it_cannot_replay_and_never_guesses_its_findings():
    with tempfile.TemporaryDirectory() as tmp:
        split = dva.build_report(*_source_fixed_inputs(tmp), source_fixed=True)["recall"]["source_fixed"]
    assert (split["runs_not_replayed"], split["uncaught_not_replayed"]) == (2, 3)
    assert sorted(split["not_replayed"]) == [UID_B, UID_C]
    assert "cannot read" in split["not_replayed"][UID_B]
    assert "names no stage_3a / stage_3b artifact" in split["not_replayed"][UID_C]
    assert not {"TSTBBB-01", "TSTBBB-02", "TSTCCC-01"} & set(split["source_fixed_ids"])


def test_source_fixed_counts_a_malformed_artifact_as_not_replayed():
    for broken in ({"entries": [{"element_idx_start": "abc"}]}, {"entries": None}, {}, []):
        with tempfile.TemporaryDirectory() as tmp:
            doctor, labels_dir = _source_fixed_inputs(tmp)
            (Path(tmp) / "3b.json").write_text(json.dumps(broken))
            split = dva.build_report(doctor, labels_dir, source_fixed=True)["recall"]["source_fixed"]
        assert UID_A in split["not_replayed"], (broken, split)
        assert split["source_fixed"] == 0, (broken, split)


def test_source_fixed_flag_adds_one_block_and_one_line_and_nothing_else():
    with tempfile.TemporaryDirectory() as tmp:
        doctor, labels_dir = _source_fixed_inputs(tmp)
        off_json, on_json = Path(tmp) / "off.json", Path(tmp) / "on.json"
        off = _run_main([doctor, labels_dir, "--json", off_json])
        on = _run_main([doctor, labels_dir, "--json", on_json, "--source-fixed"])
        off_text, on_report = off_json.read_text(), json.loads(on_json.read_text())
    assert (off[0], on[0], off[2], on[2]) == (0, 0, "", "")
    line = ("  of 8 uncaught, 2 no longer reproduce at source on this checkout (stage-3b pins / "
            "duplicate marking); 2 runs not replayed (3 uncaught findings unjudged)")
    assert on[1] == off[1].rstrip("\n") + "\n" + line + "\n", on[1]
    assert "uncaught" not in off[1] and "source_fixed" not in off_text
    assert on_report["recall"].pop("source_fixed")["source_fixed"] == 2
    assert json.dumps(on_report, indent=1, sort_keys=True) + "\n" == off_text


def test_source_fixed_leaves_the_stage_3b_log_level_as_it_found_it():
    log = logging.getLogger(dva.STAGE3B_LOGGER)
    before = log.level
    log.setLevel(logging.DEBUG)  # a level the replay does not itself set
    try:
        with tempfile.TemporaryDirectory() as tmp:
            dva.build_report(*_source_fixed_inputs(tmp), source_fixed=True)
        assert log.level == logging.DEBUG
    finally:
        log.setLevel(before)


if __name__ == "__main__":
    test_finding_idxs_reads_every_emitter_format()
    test_finding_idxs_ignores_numbers_that_are_not_entry_indices()
    test_report_counts_hits_matches_and_caught_findings_per_lint()
    test_recall_groups_and_no_idx_findings()
    test_verdicts_are_tallied_and_split_by_whether_the_arm_still_fires()
    test_main_prints_the_table_and_writes_the_same_numbers_as_json()
    test_findings_with_no_class_or_credit_are_grouped_not_dropped()
    test_recall_text_lists_big_groups_and_sums_the_small_ones()
    test_inputs_that_cannot_be_scored_exit_2()
    test_replay_stage3b_reports_each_entry_before_and_after_and_changes_no_input()
    test_replay_stage3b_lets_a_fixed_sub_entry_stand_for_its_index()
    test_replay_stage3b_runs_the_whole_cv_pins_after_the_header_pin()
    test_source_fixed_counts_only_uncaught_findings_the_replay_changes()
    test_source_fixed_counts_a_run_it_cannot_replay_and_never_guesses_its_findings()
    test_source_fixed_counts_a_malformed_artifact_as_not_replayed()
    test_source_fixed_flag_adds_one_block_and_one_line_and_nothing_else()
    test_source_fixed_leaves_the_stage_3b_log_level_as_it_found_it()
    print("ok")
