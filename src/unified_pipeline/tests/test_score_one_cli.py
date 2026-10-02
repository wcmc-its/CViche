"""Contract tests for scripts/score_one.py (#435).

run_corpus_batch.sh consumes this script via command substitution:

    sline=$(... score_one.py "$ROOT" "$stem" "$DOCX" "$OUT" 2>>"$log") \
        || sline=$'error\t\t\t'

so the same three things are load-bearing here as in doctor_one.py:
  * stdout is EXACTLY one 4-field TSV line (the data contract),
  * diagnostics go to stderr, never stdout,
  * a score_run failure exits non-zero instead of crashing the batch.

Plus one that is specific to this script: the scorer needs every artifact in a
single directory, so the stage_* subdirs have to be flattened first. If that
collection silently found nothing we would emit a confident score for an empty
directory -- which is the exact failure mode #435 is about.

Run with:
    python3 -m pytest src/unified_pipeline/tests/test_score_one_cli.py -p no:cacheprovider
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3]
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _load_cli():
    spec = importlib.util.spec_from_file_location(
        "score_one_cli", _ROOT / "scripts" / "score_one.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_REPORT = {
    "run_id": "web05",
    "totalScore": 76,
    "band": "YELLOW (human cleanup needed)",
    "raw_score_before_caps": 76.15,
    "dimensionScores": [
        {"name": "Sparse / under-filled tables in generated docx", "penalty": 10.93},
        {"name": "Broken table / raw formatting artifacts in docx", "penalty": 5.97},
        {"name": "T-bucket share (stage_3b catch-all over-use)", "penalty": 3.99},
        {"name": "Duplicate-entry ratio (de-dup / fragmentation health)", "penalty": 0.0},
    ],
}


def _stage_artifacts(root, uid="web05"):
    """A minimal stage_* layout holding the three JSON artifacts the scorer reads."""
    stage = root / "stage_3b_classified_entries"
    stage.mkdir()
    (stage / f"{uid}_classified.json").write_text("{}", encoding="utf-8")
    stage2 = root / "stage_4_field_extraction"
    stage2.mkdir()
    (stage2 / f"{uid}_fields.json").write_text("{}", encoding="utf-8")
    stage3 = root / "stage_2_entry_extraction"
    stage3.mkdir()
    (stage3 / f"{uid}_entries.json").write_text("{}", encoding="utf-8")


def test_stdout_is_one_four_field_tsv_line(tmp_path, capsys, monkeypatch):
    cli = _load_cli()
    monkeypatch.setattr(cli, "score_run", lambda *a, **k: _REPORT)
    _stage_artifacts(tmp_path)

    assert cli.main([str(tmp_path), "web05"]) == 0

    out = capsys.readouterr().out
    assert out.count("\n") == 1, "stdout must be exactly one line"
    fields = out.rstrip("\n").split("\t")
    assert len(fields) == 4, f"run_corpus_batch.sh cuts fields 1-2: {fields!r}"
    assert fields[0] == "76"
    # The band is truncated at the parenthetical so the column stays sortable.
    assert fields[1] == "YELLOW"
    assert fields[2] == "76.15"
    # Worst penalty first, zero-penalty dimensions omitted.
    assert fields[3].startswith("Sparse / under-filled tables in generated docx:10.9")
    assert "Duplicate-entry ratio" not in fields[3]


def test_artifacts_are_flattened_out_of_stage_subdirs(tmp_path, monkeypatch):
    """The scorer is handed ONE dir containing the artifacts, not the stage tree."""
    cli = _load_cli()
    seen = {}

    def _capture(outputs_dir, run_id):
        seen["names"] = sorted(p.name for p in Path(outputs_dir).iterdir())
        return _REPORT

    monkeypatch.setattr(cli, "score_run", _capture)
    _stage_artifacts(tmp_path)

    assert cli.main([str(tmp_path), "web05"]) == 0
    assert seen["names"] == [
        "web05_classified.json", "web05_entries.json", "web05_fields.json",
    ], seen


def test_source_docx_is_staged_under_the_scorers_source_subdir(tmp_path, monkeypatch):
    """#822: --source reaches the scorer as source/<name>, beside (not among)
    the flat artifacts, so the lost-source-table gate can read it."""
    cli = _load_cli()
    seen = {}

    def _capture(outputs_dir, run_id):
        seen["names"] = sorted(p.name for p in Path(outputs_dir).iterdir())
        seen["source"] = sorted(p.name for p in (Path(outputs_dir) / "source").iterdir())
        return _REPORT

    monkeypatch.setattr(cli, "score_run", _capture)
    _stage_artifacts(tmp_path)
    source = tmp_path / "original_cv.docx"
    source.write_bytes(b"docx bytes")

    assert cli.main([str(tmp_path), "web05", "--source", str(source)]) == 0
    assert seen["source"] == ["original_cv.docx"]
    assert "source" in seen["names"]


def test_without_source_the_scorer_gets_no_source_subdir(tmp_path, monkeypatch):
    cli = _load_cli()
    seen = {}

    def _capture(outputs_dir, run_id):
        seen["has_source"] = (Path(outputs_dir) / "source").exists()
        return _REPORT

    monkeypatch.setattr(cli, "score_run", _capture)
    _stage_artifacts(tmp_path)

    assert cli.main([str(tmp_path), "web05"]) == 0
    assert seen["has_source"] is False


def test_stage_error_record_is_collected_for_the_scorer(tmp_path, monkeypatch):
    """#745: the drivers' stage-error record lives in its own dir beside the
    stage_* dirs; it must reach the flattened dir the scorer reads, or a
    recorded stage failure never caps the score."""
    cli = _load_cli()
    seen = {}

    def _capture(outputs_dir, run_id):
        seen["names"] = sorted(p.name for p in Path(outputs_dir).iterdir())
        return _REPORT

    monkeypatch.setattr(cli, "score_run", _capture)
    _stage_artifacts(tmp_path)
    (tmp_path / "stage_errors").mkdir()
    (tmp_path / "stage_errors" / "web05_stage_errors.json").write_text("[]", encoding="utf-8")

    assert cli.main([str(tmp_path), "web05"]) == 0
    assert "web05_stage_errors.json" in seen["names"], seen


def test_research_summary_artifact_is_collected_for_the_scorer(tmp_path, monkeypatch):
    """#1174: the fallback-served gate reads stage 4.5's provenance from the
    research summary artifact; it must reach the flattened dir the scorer reads,
    or a stage-4.5 call the backup model served never caps the score."""
    cli = _load_cli()
    seen = {}

    def _capture(outputs_dir, run_id):
        seen["names"] = sorted(p.name for p in Path(outputs_dir).iterdir())
        return _REPORT

    monkeypatch.setattr(cli, "score_run", _capture)
    _stage_artifacts(tmp_path)
    (tmp_path / "stage_4_5_research_summary").mkdir()
    (tmp_path / "stage_4_5_research_summary" / "web05_research_summary.json").write_text(
        "{}", encoding="utf-8")

    assert cli.main([str(tmp_path), "web05"]) == 0
    assert "web05_research_summary.json" in seen["names"], seen


def test_no_artifacts_exits_nonzero_instead_of_scoring_an_empty_dir(tmp_path, capsys, monkeypatch):
    """This is the #435 failure mode: never emit a score for nothing."""
    cli = _load_cli()
    called = []
    monkeypatch.setattr(cli, "score_run", lambda *a, **k: called.append(1) or _REPORT)
    (tmp_path / "stage_2_entry_extraction").mkdir()  # present but empty

    assert cli.main([str(tmp_path), "web05"]) == 1
    assert not called, "scored a directory with no artifacts"
    assert capsys.readouterr().out == "", "a failure must not emit a TSV row"


def test_score_run_failure_exits_nonzero_without_polluting_stdout(tmp_path, capsys, monkeypatch):
    cli = _load_cli()

    def _boom(*a, **k):
        raise IOError("unreadable artifact")

    monkeypatch.setattr(cli, "score_run", _boom)
    _stage_artifacts(tmp_path)

    assert cli.main([str(tmp_path), "web05"]) == 1
    assert capsys.readouterr().out == "", "a failure must not emit a TSV row"


def test_missing_docx_still_scores_but_is_not_treated_as_zero(tmp_path, capsys, monkeypatch):
    """A absent docx gives the render dimensions flat half credit, not 0.

    Worth pinning: it means a run that rendered nothing can out-score one that
    rendered something sparse, so the row is an upper bound. The CLI must not
    quietly drop the run.
    """
    cli = _load_cli()
    monkeypatch.setattr(cli, "score_run", lambda *a, **k: _REPORT)
    _stage_artifacts(tmp_path)

    assert cli.main([str(tmp_path), "web05", str(tmp_path / "absent_wcm.docx")]) == 0
    assert capsys.readouterr().out.rstrip("\n").split("\t")[0] == "76"


def test_missing_args_are_a_usage_error_not_an_indexerror():
    cli = _load_cli()
    with pytest.raises(SystemExit) as exc:  # argparse exits 2, not IndexError
        cli.main([])
    assert exc.value.code == 2


def test_bad_outputs_root_is_validated(tmp_path):
    cli = _load_cli()
    with pytest.raises(SystemExit):
        cli.main([str(tmp_path / "does-not-exist"), "web05"])


def test_out_dir_is_validated_before_scoring(tmp_path, monkeypatch):
    """A typo'd out path must fail fast, not after the whole score run."""
    cli = _load_cli()
    called = []
    monkeypatch.setattr(cli, "score_run", lambda *a, **k: called.append(1) or _REPORT)
    _stage_artifacts(tmp_path)

    with pytest.raises(SystemExit):
        cli.main([str(tmp_path), "web05", "", str(tmp_path / "nope" / "f.json")])
    assert not called, "validated the out dir only after scoring"


def test_score_json_round_trips_non_ascii(tmp_path, monkeypatch):
    """CV content carries accented names; the written report must decode back."""
    cli = _load_cli()
    report = dict(_REPORT, note="café — naïve")
    monkeypatch.setattr(cli, "score_run", lambda *a, **k: report)
    _stage_artifacts(tmp_path)
    out = tmp_path / "score.json"

    assert cli.main([str(tmp_path), "web05", "", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["note"] == "café — naïve"
