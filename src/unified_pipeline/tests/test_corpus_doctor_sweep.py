"""Contract tests for scripts/corpus_doctor_sweep.py (#563).

Before this fix, `sweep()` caught every per-run `run_doctor` exception, kept
only `str(e)`, and returned a bare `reports` dict -- the traceback (which
lint raised, at which line, on which artifact) was discarded, `main()`
returned 0 unconditionally, and the `--out` payload had no way to say a run
was dropped. `scripts/doctor_one.py` already gets this right for the
single-run case (`logger.exception`, non-zero exit on failure); this pins
the sweep script to the same contract.

Run with:
    python3 -m pytest src/unified_pipeline/tests/test_corpus_doctor_sweep.py -p no:cacheprovider
"""

import importlib.util
import json
import logging
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "corpus_doctor_sweep_cli", _ROOT / "scripts" / "corpus_doctor_sweep.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _make_run(corpus_dir: Path, run_id: str, uid: str) -> None:
    """A run dir with just enough for `_find_uid` to resolve `uid`."""
    outputs = corpus_dir / run_id / "outputs"
    outputs.mkdir(parents=True)
    (outputs / f"{uid}_fields.json").write_text("{}", encoding="utf-8")


def _make_skipped_run(corpus_dir: Path, run_id: str) -> None:
    """A run dir with no artifacts at all -- `_find_uid` returns None."""
    (corpus_dir / run_id / "outputs").mkdir(parents=True)


def test_stage_prunes_symlinks_a_prior_run_left_for_the_same_uid(tmp_path):
    """T1.1/T2.1: `work/<uid>` persists across invocations. Stage uid `aaa111`
    from a run that has `_entries.json`, then stage it again from a run that
    doesn't -- the second call must not leave the first run's `_entries.json`
    symlink behind.
    """
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    work = tmp_path / "work"
    uid = "aaa111"

    run1 = corpus / "run1" / "outputs"
    run1.mkdir(parents=True)
    (run1 / f"{uid}_fields.json").write_text("{}", encoding="utf-8")
    (run1 / f"{uid}_entries.json").write_text("{}", encoding="utf-8")
    cli.stage(run1, uid, work)
    assert (work / uid / "stage_2_entry_extraction" / f"{uid}_entries.json").exists()

    run2 = corpus / "run2" / "outputs"
    run2.mkdir(parents=True)
    (run2 / f"{uid}_fields.json").write_text("{}", encoding="utf-8")
    cli.stage(run2, uid, work)

    assert not (work / uid / "stage_2_entry_extraction").exists(), (
        "run2 has no _entries.json -- the stale symlink from run1 must be gone")
    assert (work / uid / "stage_4_field_extraction" / f"{uid}_fields.json").resolve() == (
        run2 / f"{uid}_fields.json").resolve()


def test_stage_finds_source_docx_under_flat_layout_input_dir(tmp_path):
    """T1.2/T2.7: a flat-layout run (no outputs/ subdir) still has its source
    docx at <run_root>/input/<uid>.docx, a sibling of the jsons themselves,
    not <corpus>/input.
    """
    cli = _load_cli()
    uid = "aaa111"
    run_root = tmp_path / "corpus" / "run1"
    run_root.mkdir(parents=True)
    (run_root / f"{uid}_fields.json").write_text("{}", encoding="utf-8")
    input_dir = run_root / "input"
    input_dir.mkdir()
    (input_dir / f"{uid}.docx").write_text("source", encoding="utf-8")
    work = tmp_path / "work"

    root = cli.stage(run_root, uid, work)

    staged_source = root / f"{uid}.docx"
    assert staged_source.is_symlink(), "source docx must be staged for a flat-layout run"
    assert staged_source.resolve() == (input_dir / f"{uid}.docx").resolve()


def test_sweep_failure_carries_traceback_and_good_run_still_reported(tmp_path, monkeypatch):
    """Positive control: on dev, `sweep()` returns one bare dict, not a 3-tuple,
    so `reports, failures, skipped = sweep(...)` raises ValueError immediately
    -- this test fails against dev's sweep() before it even reaches the
    assertions below, and dev's `failures[run_id]` is a plain string with no
    traceback even if you unpack around that.
    """
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    _make_run(corpus, "good1", "aaa111")
    _make_run(corpus, "bad1", "bbb222")
    work = tmp_path / "work"

    def fake_run_doctor(root, uid):
        if uid == "bbb222":
            raise ValueError("boom: lint_x could not read artifact")
        return {"findings": []}

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    reports, failures, skipped = cli.sweep(corpus, ["good1", "bad1"], work)

    assert "good1" in reports, "the good run's report must still be produced"
    assert skipped == []
    assert set(failures) == {"bad1"}
    assert failures["bad1"]["error"] == "boom: lint_x could not read artifact"
    assert "ValueError" in failures["bad1"]["traceback"]
    assert "boom: lint_x could not read artifact" in failures["bad1"]["traceback"]
    assert "in fake_run_doctor" in failures["bad1"]["traceback"], (
        "must be a real stack trace, not just the message repeated")


def test_main_exits_zero_when_at_least_one_report_succeeds(tmp_path, monkeypatch):
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    _make_run(corpus, "good1", "aaa111")
    _make_run(corpus, "bad1", "bbb222")

    def fake_run_doctor(root, uid):
        if uid == "bbb222":
            raise ValueError("boom")
        return {"findings": []}

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    assert cli.main([str(corpus), "good1,bad1"]) == 0


def test_main_returns_nonzero_when_every_run_fails_or_is_skipped(tmp_path, monkeypatch):
    """#5.5: nothing to report is a failure, not a pass -- dev returns 0 here."""
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    _make_run(corpus, "bad1", "bbb222")
    _make_skipped_run(corpus, "skip1")

    def fake_run_doctor(root, uid):
        raise RuntimeError("doctor exploded")

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    assert cli.main([str(corpus), "bad1,skip1"]) != 0


def test_out_payload_contains_failures_and_skipped(tmp_path, monkeypatch):
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    _make_run(corpus, "good1", "aaa111")
    _make_run(corpus, "bad1", "bbb222")
    _make_skipped_run(corpus, "skip1")
    out = tmp_path / "sweep.json"

    def fake_run_doctor(root, uid):
        if uid == "bbb222":
            raise ValueError("boom")
        return {"findings": []}

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    assert cli.main([str(corpus), "good1,bad1,skip1", "--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))

    assert "failures" in payload and "bad1" in payload["failures"]
    assert payload["failures"]["bad1"]["error"] == "boom"
    assert "traceback" in payload["failures"]["bad1"]
    assert "skipped" in payload and payload["skipped"] == ["skip1"]


def test_diagnostics_go_through_the_logger_not_bare_stderr_prints(tmp_path, monkeypatch, capsys, caplog):
    """The old `print(..., file=sys.stderr)` sites at :107/:114-117 must be
    gone: nothing reaches the process's stderr stream directly any more (the
    module never calls `logging.basicConfig`, so a bare `logger.warning` here
    is only visible to caplog, not to capsys) -- and stdout carries only the
    ranking table, the script's one deliberate print() block.
    """
    cli = _load_cli()
    corpus = tmp_path / "corpus"
    _make_run(corpus, "good1", "aaa111")
    _make_run(corpus, "bad1", "bbb222")
    _make_skipped_run(corpus, "skip1")

    def fake_run_doctor(root, uid):
        if uid == "bbb222":
            raise ValueError("boom")
        return {"findings": []}

    monkeypatch.setattr(cli, "run_doctor", fake_run_doctor)

    with caplog.at_level(logging.WARNING):
        rc = cli.main([str(corpus), "good1,bad1,skip1"])
    assert rc == 0

    captured = capsys.readouterr()
    assert captured.err == "", "diagnostics must not bypass logging onto stderr"
    assert "!!" not in captured.out, "the old bare diagnostic marker must be gone"
    assert "Doctor sweep: 1 distinct CVs" in captured.out
    assert "lint" in captured.out and "CVs affected" in captured.out

    assert "skip1: no artifacts found, skipping" in caplog.text
    assert "run_doctor failed for run_id=bad1" in caplog.text
    assert "1 run(s) failed and were dropped" in caplog.text
