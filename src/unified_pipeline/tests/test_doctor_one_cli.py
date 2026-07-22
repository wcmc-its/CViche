"""Contract tests for scripts/doctor_one.py (review feedback on PR #286).

run_corpus_batch.sh consumes this script via command substitution:

    dline=$(... doctor_one.py "$ROOT" "$stem" "$f" "$OUT" 2>>"$log") \
        || dline=$'error\t\t\t\t'

so three things are load-bearing and pinned here:
  * stdout is EXACTLY one 5-field TSV line (the data contract),
  * diagnostics go to stderr, never stdout,
  * a run_doctor failure exits non-zero instead of crashing the batch.

Run with:
    python3 -m pytest src/unified_pipeline/tests/test_doctor_one_cli.py -p no:cacheprovider
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
        "doctor_one_cli", _ROOT / "scripts" / "doctor_one.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_REPORT = {
    "worst_severity": "WARN",
    "findings": [
        {"severity": "WARN", "lint": "segmentation"},
        {"severity": "WARN", "lint": "output_hygiene"},
        {"severity": "INFO", "lint": "output_hygiene"},
    ],
}


def test_stdout_is_one_five_field_tsv_line(tmp_path, capsys, monkeypatch):
    cli = _load_cli()
    monkeypatch.setattr(cli, "run_doctor", lambda *a, **k: _REPORT)
    (tmp_path / "stage_2_entry_extraction").mkdir()

    assert cli.main([str(tmp_path), "web05"]) == 0

    out = capsys.readouterr().out
    assert out.count("\n") == 1, "stdout must be exactly one line"
    fields = out.rstrip("\n").split("\t")
    assert len(fields) == 5, f"run_corpus_batch.sh cuts fields 1-4: {fields!r}"
    assert fields[:4] == ["WARN", "0", "2", "1"]
    assert "output_hygiene:2" in fields[4]


def test_run_doctor_failure_exits_nonzero_without_polluting_stdout(tmp_path, capsys, monkeypatch):
    cli = _load_cli()

    def _boom(*a, **k):
        raise IOError("unreadable artifact")

    monkeypatch.setattr(cli, "run_doctor", _boom)
    (tmp_path / "stage_2_entry_extraction").mkdir()

    assert cli.main([str(tmp_path), "web05"]) == 1
    assert capsys.readouterr().out == "", "a failure must not emit a TSV row"


def test_missing_args_are_a_usage_error_not_an_indexerror():
    cli = _load_cli()
    with pytest.raises(SystemExit) as exc:  # argparse exits 2, not IndexError
        cli.main([])
    assert exc.value.code == 2


def test_bad_outputs_root_is_validated(tmp_path):
    cli = _load_cli()
    with pytest.raises(SystemExit):
        cli.main([str(tmp_path / "does-not-exist"), "web05"])


def test_findings_json_round_trips_non_ascii(tmp_path, monkeypatch):
    """The report is written with an explicit encoding and survives intact.

    json.dumps escapes non-ASCII by default, so the file stays pure ASCII and
    any consumer reading with a locale default can still load it -- but the
    values must decode back exactly (CV content carries accented names).
    """
    cli = _load_cli()
    report = dict(_REPORT, note="café — naïve")
    monkeypatch.setattr(cli, "run_doctor", lambda *a, **k: report)
    (tmp_path / "stage_2_entry_extraction").mkdir()
    out = tmp_path / "findings.json"

    assert cli.main([str(tmp_path), "web05", "", str(out)]) == 0
    loaded = json.loads(out.read_text(encoding="utf-8"))
    assert loaded["note"] == "café — naïve"


def test_out_dir_is_validated_before_doctoring(tmp_path, monkeypatch):
    """A typo'd out path must fail fast, not after the whole doctor run."""
    cli = _load_cli()
    called = []
    monkeypatch.setattr(cli, "run_doctor", lambda *a, **k: called.append(1) or _REPORT)
    (tmp_path / "stage_2_entry_extraction").mkdir()

    with pytest.raises(SystemExit):
        cli.main([str(tmp_path), "web05", "", str(tmp_path / "nope" / "f.json")])
    assert not called, "validated the out dir only after running the doctor"
