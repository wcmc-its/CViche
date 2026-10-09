#!/usr/bin/env python3
"""Self-tests for summary_claim_audit.py, on synthetic runs and a fake LLM only.

    python3 scripts/test_summary_claim_audit.py

Pins the source text and prompt a run's call is built from, the reply parser's
refusals, the label files (schema, no CV text, one finding per flagged
sentence), the dry run's calibration and estimate, the resume and fail-closed
paths of `audit`, and the per-run confusion counts of `score`.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import doctor_vs_autopsy as dva  # noqa: E402
import summary_claim_audit as sca  # noqa: E402

UID = "AAAAAA"
MODEL = "us.anthropic.claude-sonnet-5"
SUMMARY = ("I study synthetic widgets in model systems. My work is funded by the Example "
           "Foundation. I mentor three trainees.")
SOURCES = ["Curriculum Vitae", "", "Widget studies, 2020-2024", "Sub-entry text"]


def _write_run(runs_dir: Path, uid: str = UID, method: str = sca.GENERATION_METHOD_LLM,
               summary: str = SUMMARY, prompt_logs: list[tuple[str, str, int, int]] = ()) -> Path:
    """A run in the S3 layout; `prompt_logs` are (purpose, model, chars, prompt_tokens)."""
    outputs = runs_dir / uid / "outputs"
    outputs.mkdir(parents=True)
    (outputs / f"{uid}_research_summary.json").write_text(json.dumps({"research_summary": {
        "text": summary, "generation_method": method,
        "generation_timestamp": "2026-10-08T03:58:00.000000"}}))
    entries = [{"element_idx_start": idx, "text": text}
               for idx, text in zip([0, 1, 2, "2.1"], SOURCES, strict=True)]
    (outputs / f"{uid}_entries.json").write_text(json.dumps({"entries": entries}))
    logs = runs_dir / uid / "prompt_logs"
    logs.mkdir()
    for n, (purpose, model, chars, tokens) in enumerate(prompt_logs):
        log_id = f"{n:012x}"
        (logs / f"t_{purpose}_{log_id}.json").write_text(json.dumps(
            {"log_id": log_id, "purpose": purpose, "character_count": 10 * chars,
             "messages": [{"role": "user", "content": "x" * chars}]}))
        (logs / f"t_{purpose}_{log_id}_RESPONSE.json").write_text(json.dumps(
            {"log_id": log_id, "response": {"model": model, "usage": {"prompt_tokens": tokens}}}))
    return runs_dir / uid


def _reply(*rows: tuple[str, str, str]) -> str:
    """A model reply: one (verdict, kind, severity) per sentence."""
    return json.dumps({"sentences": [{"i": i, "verdict": v, "kind": k, "severity": s, "reason": "r"}
                                     for i, (v, k, s) in enumerate(rows, start=1)]})


OK_ROW = ("supported", "none", "none")
FUNDER_ROW = ("unsupported", "funder", "medium")
MENTOR_ROW = ("partly", "mentoring", "low")


class FakeLlm:
    """call(prompt) -> a call_llm-shaped result; records every prompt it got."""

    def __init__(self, content: str = "", error: Exception | None = None):
        self.content, self.error, self.prompts = content, error, []

    def __call__(self, prompt: str) -> dict:
        self.prompts.append(prompt)
        if self.error:
            raise self.error
        return {"content": self.content, "prompt_tokens": 1000, "completion_tokens": 50,
                "cost": 0.0123, "model": MODEL}


def _main(argv, call=None):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = sca.main([str(a) for a in argv], call=call)
    return code, out.getvalue(), err.getvalue()


def test_source_text_keeps_stage_2_order_and_drops_blank_entries():
    text = sca.source_text({"entries": [{"element_idx_start": 3, "text": " b "},
                                        {"element_idx_start": 1, "text": ""},
                                        {"element_idx_start": "3.1", "text": "c"},
                                        {"element_idx_start": 4}]})
    assert text == "b\nc", text
    for bad in ({}, {"entries": "x"}):
        try:
            sca.source_text(bad)
        except sca.InputError:
            continue
        raise AssertionError(f"accepted {bad}")


def test_load_run_and_prompt_carry_the_doctor_sentences_source_and_date():
    with tempfile.TemporaryDirectory() as tmp:
        run = sca.load_run(_write_run(Path(tmp)), UID)
    assert run.sentences == ("I study synthetic widgets in model systems.",
                             "My work is funded by the Example Foundation.",
                             "I mentor three trainees."), run.sentences
    assert run.summary_date == "2026-10-08"
    prompt = sca.build_prompt(run)
    assert "Curriculum Vitae\nWidget studies, 2020-2024\nSub-entry text\n>>>" in prompt
    assert prompt.endswith("1. I study synthetic widgets in model systems.\n"
                           "2. My work is funded by the Example Foundation.\n"
                           "3. I mentor three trainees."), prompt[-200:]
    assert "written on 2026-10-08" in prompt
    # #1592: 10 of the audit's 19 YUYVIG false positives judged status by heading or label.
    assert "end date is before 2026-10-08 is completed, whatever heading" in prompt
    assert 'marks "present" or "current" is current' in prompt
    assert '"kind": "none" | "grant_status" |' in prompt
    assert len(sca.prompt_template_hash()) == 16


def test_load_run_refuses_a_missing_or_malformed_artifact():
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = _write_run(Path(tmp))
        (run_dir / "outputs" / f"{UID}_entries.json").unlink()
        try:
            sca.load_run(run_dir, UID)
        except sca.InputError as e:
            assert "cannot read" in str(e)
        else:
            raise AssertionError("missing stage-2 artifact accepted")
        (run_dir / "outputs" / f"{UID}_research_summary.json").write_text("[]")
        try:
            sca.load_run(run_dir, UID)
        except sca.InputError as e:
            assert "research_summary" in str(e)
        else:
            raise AssertionError("summary artifact with no research_summary accepted")


def test_parse_reply_accepts_one_valid_verdict_per_sentence_fenced_or_not():
    reply = _reply(OK_ROW, FUNDER_ROW)
    for text in (reply, f"```json\n{reply}\n```"):
        verdicts = sca.parse_reply(text, 2)
        assert [(v.index, v.verdict, v.kind, v.severity) for v in verdicts] == [
            (1, "supported", "none", "none"), (2, "unsupported", "funder", "medium")]


def test_parse_reply_drops_a_leading_think_block_before_fenced_or_bare_json():
    """OKRTPJ and QQGKXR's shape (#1592): reasoning in <think>, then the answer."""
    reply = _reply(OK_ROW, FUNDER_ROW)
    think = "<think>\nSentence 2 names a funder {the CV} lacks; see ```notes```.\n</think>"
    for text in (f"{think}\n\n```json\n{reply}\n```", f"  {think}\n{reply}\n"):
        verdicts = sca.parse_reply(text, 2)
        assert [(v.index, v.verdict) for v in verdicts] == [(1, "supported"), (2, "unsupported")]
    for text in (f"{reply}\n{think}", f"Answer: {think}{reply}", f"<think>{reply}"):
        try:
            sca.parse_reply(text, 2)
        except sca.ReplyError:
            continue
        raise AssertionError(f"accepted a think block that does not lead: {text[:30]!r}")


def test_parse_reply_refuses_every_reply_that_is_not_one_verdict_per_sentence():
    cases = {
        "not JSON": ("sure, here you go", 1),
        "no sentences list": (json.dumps({"verdicts": []}), 1),
        "too few": (_reply(OK_ROW), 2),
        "too many": (_reply(OK_ROW, OK_ROW), 1),
        "out of order": (json.dumps({"sentences": [{"i": 2, "verdict": "supported", "kind": "none",
                                                     "severity": "none"}]}), 1),
        "unknown verdict": (_reply(("maybe", "none", "none")), 1),
        "unknown kind": (_reply(("unsupported", "vibes", "low")), 1),
        "supported with a severity": (_reply(("supported", "none", "high")), 1),
        "unsupported with no severity": (_reply(("unsupported", "funder", "none")), 1),
        "item not an object": (json.dumps({"sentences": ["supported"]}), 1),
    }
    for why, (text, count) in cases.items():
        try:
            sca.parse_reply(text, count)
        except sca.ReplyError:
            continue
        raise AssertionError(f"{why}: accepted")


def test_audit_run_labels_only_flagged_sentences_and_carries_no_cv_text():
    with tempfile.TemporaryDirectory() as tmp:
        run = sca.load_run(_write_run(Path(tmp)), UID)
    fake = FakeLlm(_reply(OK_ROW, FUNDER_ROW, MENTOR_ROW))
    label, detail = sca.audit_run(run, "B1", fake)
    assert len(fake.prompts) == 1 and fake.prompts[0] == sca.build_prompt(run)
    assert [(f["id"], f["sentence_index"], f["claim_kind"], f["severity"]) for f in label["findings"]] == [
        (f"{UID}-A01", 2, "funder", "medium"), (f"{UID}-A02", 3, "mentoring", "low")]
    assert all(f["issues"] == ["#1554"] and f["element_idx_start"] is None for f in label["findings"])
    audit = label["audit"]
    assert (audit["status"], audit["model"], audit["cost"], audit["prompt_tokens"]) == ("ok", MODEL, 0.0123, 1000)
    assert audit["prompt_template_hash"] == sca.prompt_template_hash()
    dumped = json.dumps(label)
    assert not any(s in dumped for s in run.sentences + tuple(t for t in SOURCES if t)), "CV text in label"
    assert [s.get("verdict") for s in detail["sentences"]] == ["supported", "unsupported", "partly"]
    assert detail["sentences"][1]["text"] == run.sentences[1]


def test_audit_run_makes_no_call_for_a_summary_the_model_did_not_write():
    with tempfile.TemporaryDirectory() as tmp:
        run = sca.load_run(_write_run(Path(tmp), method="existing_m1"), UID)
    fake = FakeLlm(_reply(OK_ROW))
    label, _ = sca.audit_run(run, "B1", fake)
    assert fake.prompts == [] and label["findings"] == []
    assert label["audit"]["status"] == sca.STATUS_NOT_GENERATED and label["audit"]["cost"] == 0.0


def test_audit_run_records_a_failed_call_and_an_unparsed_reply():
    with tempfile.TemporaryDirectory() as tmp:
        run = sca.load_run(_write_run(Path(tmp)), UID)
    with contextlib.redirect_stderr(io.StringIO()):
        label, _ = sca.audit_run(run, "B1", FakeLlm(error=TimeoutError("slow")))
    assert label["audit"]["status"] == sca.STATUS_CALL_FAILED
    assert label["audit"]["error"] == "TimeoutError: slow" and label["findings"] == []
    label, detail = sca.audit_run(run, "B1", FakeLlm("no json here"))
    assert label["audit"]["status"] == sca.STATUS_REPLY_UNPARSED and label["findings"] == []
    assert label["audit"]["cost"] == 0.0123, "an unparsed reply was still billed"
    assert detail["raw_reply"] == "no json here"


def test_label_files_load_in_doctor_vs_autopsy():
    with tempfile.TemporaryDirectory() as tmp:
        run = sca.load_run(_write_run(Path(tmp) / "runs"), UID)
        label, _ = sca.audit_run(run, "B1", FakeLlm(_reply(OK_ROW, FUNDER_ROW, OK_ROW)))
        labels_dir = Path(tmp) / "labels"
        labels_dir.mkdir()
        (labels_dir / f"{UID}.json").write_text(json.dumps(label))
        loaded = dva.load_labels(labels_dir)
    assert loaded.uids == {UID}
    assert [(v.id, v.severity, v.class_ref, v.stage) for v in loaded.verified] == [
        (f"{UID}-A01", "medium", "#1554", "4.5")]


def test_chars_per_token_is_measured_on_stage_4_5_calls_of_the_same_model_only():
    with tempfile.TemporaryDirectory() as tmp:
        runs = Path(tmp)
        _write_run(runs, "AAAAAA", prompt_logs=[("stage_4_5", MODEL, 2200, 1000),
                                                ("stage_4", MODEL, 9999, 1),
                                                ("stage_4_5_rescore", MODEL, 9999, 1),
                                                ("stage_4_5", "other-model", 9999, 1)])
        _write_run(runs, "BBBBBB", prompt_logs=[("stage_4_5", MODEL, 2000, 1000)])
        ratio, calls = sca.measure_chars_per_token([runs / "AAAAAA", runs / "BBBBBB"], MODEL)
        assert (round(ratio, 3), calls) == (2.1, 2), (ratio, calls)
        try:
            sca.measure_chars_per_token([runs / "AAAAAA"], "no-such-model")
        except sca.InputError:
            pass
        else:
            raise AssertionError("no calibration calls accepted")


def test_estimate_prices_the_real_prompt_and_zero_for_no_call():
    with tempfile.TemporaryDirectory() as tmp:
        run = sca.load_run(_write_run(Path(tmp)), UID)
        skipped = sca.load_run(_write_run(Path(tmp), "BBBBBB", method="existing_m1"), "BBBBBB")
    row = sca.estimate(run, 2.0, MODEL)
    assert row["prompt_chars"] == len(sca.build_prompt(run))
    assert row["input_tokens"] == round(row["prompt_chars"] / 2.0)
    assert row["output_tokens"] == 3 * sca.OUTPUT_TOKENS_PER_SENTENCE
    assert row["cost"] == sca.calculate_cost(row["input_tokens"], row["output_tokens"], model=MODEL) > 0
    assert sca.estimate(skipped, 2.0, MODEL)["cost"] == 0.0


def test_dry_run_prints_prompts_and_table_and_makes_no_call():
    with tempfile.TemporaryDirectory() as tmp:
        runs = Path(tmp) / "runs"
        _write_run(runs, prompt_logs=[("stage_4_5", MODEL, 2200, 1000)])
        fake = FakeLlm(_reply(OK_ROW, OK_ROW, OK_ROW))
        code, out, _ = _main(["audit", runs, "--out", Path(tmp) / "out", "--dry-run"], call=fake)
        assert not (Path(tmp) / "out" / "labels").exists()
    assert code == 0 and fake.prompts == []
    assert f"===== {UID} prompt =====" in out and "2.20 chars/token measured over 1 logged" in out
    assert out.rstrip().endswith("(1 calls)"), out[-200:]


def test_audit_writes_labels_resumes_and_exits_1_on_a_failed_run():
    with tempfile.TemporaryDirectory() as tmp:
        runs, out = Path(tmp) / "runs", Path(tmp) / "out"
        _write_run(runs, "AAAAAA")
        _write_run(runs, "BBBBBB")
        with contextlib.redirect_stderr(io.StringIO()):
            code, stdout, _ = _main(["audit", runs, "--out", out, "--uid", "AAAAAA"],
                                    call=FakeLlm(error=RuntimeError("down")))
        assert code == sca.EXIT_RUN_FAILED and "failed ['AAAAAA']" in stdout
        fake = FakeLlm(_reply(OK_ROW, FUNDER_ROW, OK_ROW))
        code, _, _ = _main(["audit", runs, "--out", out], call=fake)
        assert code == 0 and len(fake.prompts) == 2, "the failed run is retried"
        label = json.loads((out / "labels" / "AAAAAA.json").read_text())
        assert label["audit"]["status"] == "ok" and len(label["findings"]) == 1
        assert (out / "detail" / "BBBBBB.json").is_file()
        code, _, err = _main(["audit", runs, "--out", out], call=fake)
        assert code == 0 and len(fake.prompts) == 2 and err.count("already audited") == 2


def test_audit_refuses_an_output_directory_inside_the_repository():
    with tempfile.TemporaryDirectory() as tmp:
        runs = Path(tmp) / "runs"
        _write_run(runs)
        fake = FakeLlm(_reply(OK_ROW, OK_ROW, OK_ROW))
        code, _, err = _main(["audit", runs, "--out", sca._REPO / "scratch_audit"], call=fake)
    assert code == sca.EXIT_INPUT_ERROR and "inside the repository" in err and fake.prompts == []
    assert not (sca._REPO / "scratch_audit").exists()


def test_a_paid_run_is_refused_while_prompt_logs_would_land_in_the_repository():
    prompt_logger = sca.prompt_logger
    real_dir, real_call = prompt_logger.PROMPT_LOG_DIR, sca.bedrock_call
    paid = FakeLlm(_reply(OK_ROW, OK_ROW, OK_ROW))
    sca.bedrock_call = paid  # never the real one, whatever PROMPT_LOG_DIR this process has
    try:
        with tempfile.TemporaryDirectory() as tmp:
            runs = Path(tmp) / "runs"
            _write_run(runs)
            prompt_logger.PROMPT_LOG_DIR = sca._REPO / "src" / "unified_pipeline" / "prompt_logs"
            code, _, err = _main(["audit", runs, "--out", Path(tmp) / "out"])
            assert code == sca.EXIT_INPUT_ERROR and "PROMPT_LOG_DIR" in err and paid.prompts == []
            prompt_logger.PROMPT_LOG_DIR = Path(tmp) / "logs"
            code, _, _ = _main(["audit", runs, "--out", Path(tmp) / "out"])
            assert code == 0 and len(paid.prompts) == 1, "outside the repo, the paid call is made"
    finally:
        prompt_logger.PROMPT_LOG_DIR, sca.bedrock_call = real_dir, real_call


def _audit_label(uid, *severities, status="ok"):
    findings = [{"id": f"{uid}-A{i}", "severity": s, "sentence_index": i} for i, s in enumerate(severities, 1)]
    return {"uid": uid, "findings": findings, "audit": {"status": status, "cost": 0.5}}


def _truth_label(uid, *findings):
    """`findings` are (severity, issues) pairs."""
    return {"uid": uid, "findings": [{"id": f"{uid}-{i:02d}", "severity": s, "issues": list(issues)}
                                     for i, (s, issues) in enumerate(findings, 1)]}


def _write_labels(directory: Path, labels: list[dict]) -> Path:
    directory.mkdir(parents=True)
    for label in labels:
        (directory / f"{label['uid']}.json").write_text(json.dumps(label))
    return directory


def test_score_counts_runs_per_cell_and_by_the_high_or_medium_threshold():
    with tempfile.TemporaryDirectory() as tmp:
        audit = _write_labels(Path(tmp) / "audit", [
            _audit_label("TPHIGH", "high"),           # truth HIGH -> TP in both
            _audit_label("TPLOW", "low"),             # truth MED  -> TP any, FN at high_or_medium
            _audit_label("FPRUN", "medium"),          # truth only another issue -> FP
            _audit_label("FNRUN"),                    # truth LOW -> FN
            _audit_label("TNRUN"),                    # clean -> TN
            _audit_label("UNLAB", "high")])           # not labelled -> not scored
        truth = _write_labels(Path(tmp) / "truth", [
            _truth_label("TPHIGH", ("high", ["#1554"])),
            _truth_label("TPLOW", ("medium", ["NEW", "#1554"])),
            _truth_label("FPRUN", ("high", ["#666"])),
            _truth_label("FNRUN", ("low", ["#1554"])),
            _truth_label("TNRUN"),
            _truth_label("NOAUD", ("high", ["#1554"]))])
        out_json = Path(tmp) / "score.json"
        code, out, _ = _main(["score", audit, truth, "--json", out_json])
        report = json.loads(out_json.read_text())
    assert code == 0, out
    assert report["runs"] == {"audited": 6, "labelled": 6, "scored": 5, "labelled_not_audited": ["NOAUD"]}
    assert report["instances"] == 3 and report["cost"] == 2.5
    anyv, hm = report["any_severity"], report["high_or_medium"]
    assert (anyv["tp"], anyv["fp"], anyv["fn"], anyv["tn"]) == (["TPHIGH", "TPLOW"], ["FPRUN"], ["FNRUN"], 1)
    assert (anyv["precision"], anyv["recall"]) == (2 / 3, 2 / 3)
    assert anyv["target_runs"] == ["TPHIGH", "TPLOW"] and anyv["target_caught"] == ["TPHIGH", "TPLOW"]
    assert (hm["tp"], hm["fp"], hm["fn"]) == (["TPHIGH"], ["FPRUN"], ["FNRUN", "TPLOW"])
    assert hm["target_caught"] == ["TPHIGH"]
    assert report["flagged_sentences"] == {"FPRUN": [1], "TPHIGH": [1], "TPLOW": [1]}
    assert "any_severity: TP 2 FP 1 FN 1 TN 1; precision 67% recall 67%" in out


def test_score_exits_2_when_it_cannot_score():
    cases = {
        "an audit run failed": ([_audit_label("AAAAAA", status="call_failed")],
                                [_truth_label("AAAAAA")], "call or reply failed"),
        "no overlap": ([_audit_label("AAAAAA")], [_truth_label("BBBBBB")], "nothing to score"),
        "label uid differs from its file name": ([{"uid": "ZZZZZZ", "findings": []}],
                                                 [_truth_label("AAAAAA")], "does not match"),
    }
    for why, (audit_labels, truth_labels, named) in cases.items():
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "audit"
            audit.mkdir()
            for label in audit_labels:
                (audit / "AAAAAA.json").write_text(json.dumps(label))
            truth = _write_labels(Path(tmp) / "truth", truth_labels)
            code, _, err = _main(["score", audit, truth])
        assert code == sca.EXIT_INPUT_ERROR and named in err, (why, code, err)
    with tempfile.TemporaryDirectory() as tmp:
        code, _, err = _main(["score", Path(tmp) / "none", Path(tmp) / "none"])
    assert code == sca.EXIT_INPUT_ERROR and "no <uid>.json" in err


if __name__ == "__main__":
    test_source_text_keeps_stage_2_order_and_drops_blank_entries()
    test_load_run_and_prompt_carry_the_doctor_sentences_source_and_date()
    test_load_run_refuses_a_missing_or_malformed_artifact()
    test_parse_reply_accepts_one_valid_verdict_per_sentence_fenced_or_not()
    test_parse_reply_drops_a_leading_think_block_before_fenced_or_bare_json()
    test_parse_reply_refuses_every_reply_that_is_not_one_verdict_per_sentence()
    test_audit_run_labels_only_flagged_sentences_and_carries_no_cv_text()
    test_audit_run_makes_no_call_for_a_summary_the_model_did_not_write()
    test_audit_run_records_a_failed_call_and_an_unparsed_reply()
    test_label_files_load_in_doctor_vs_autopsy()
    test_chars_per_token_is_measured_on_stage_4_5_calls_of_the_same_model_only()
    test_estimate_prices_the_real_prompt_and_zero_for_no_call()
    test_dry_run_prints_prompts_and_table_and_makes_no_call()
    test_audit_writes_labels_resumes_and_exits_1_on_a_failed_run()
    test_audit_refuses_an_output_directory_inside_the_repository()
    test_a_paid_run_is_refused_while_prompt_logs_would_land_in_the_repository()
    test_score_counts_runs_per_cell_and_by_the_high_or_medium_threshold()
    test_score_exits_2_when_it_cannot_score()
    print("ok")
