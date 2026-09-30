"""scripts/backfill_cv_owner_name.py -- candidate selection, dry-run vs apply."""
import importlib.util
import json
from pathlib import Path

from app.models import Run, Step

_SCRIPT = Path(__file__).parent.parent / "scripts" / "backfill_cv_owner_name.py"
_spec = importlib.util.spec_from_file_location("backfill_cv_owner_name", _SCRIPT)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


def _run(db, run_id, *, owner=None, stage4="complete", files=None):
    db.add(Run(id=run_id, filename="cv.docx", file_type="docx", status="complete",
               cv_owner_name=owner))
    if stage4 is not None:
        db.add(Step(run_id=run_id, step_number=6, stage_id="4", step_name="Fields",
                    status=stage4,
                    output_files=json.dumps(files or [f"/o/{run_id}_fields.json"])))
    db.commit()


def _patch_reader(monkeypatch, names):
    def fake(db, run_id, output_files):
        value = names[run_id]
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(script, "read_cv_owner_name", fake)


def test_dry_run_counts_and_writes_nothing(db, monkeypatch):
    _run(db, "A00001")
    _run(db, "A00002")
    _patch_reader(monkeypatch, {"A00001": "Jane Testperson", "A00002": None})

    summary = script.backfill(db, apply=False)

    assert (summary.candidates, summary.resolved, summary.no_owner, summary.written) == (2, 1, 1, 0)
    assert db.query(Run).filter(Run.cv_owner_name.isnot(None)).count() == 0


def test_apply_writes_only_resolved_names(db, monkeypatch):
    _run(db, "A00001")
    _run(db, "A00002")
    _patch_reader(monkeypatch, {"A00001": "Jane Testperson", "A00002": None})

    summary = script.backfill(db, apply=True)

    assert summary.written == 1
    assert db.get(Run, "A00001").cv_owner_name == "Jane Testperson"
    assert db.get(Run, "A00002").cv_owner_name is None


def test_skips_runs_already_named_or_without_completed_stage_4(db, monkeypatch):
    _run(db, "A00001", owner="Existing Name")
    _run(db, "A00002", stage4="error")
    _run(db, "A00003", stage4=None)
    _patch_reader(monkeypatch, {})

    assert script.backfill(db, apply=True).candidates == 0


def test_read_failure_is_counted_and_does_not_stop_the_batch(db, monkeypatch, caplog):
    _run(db, "A00001")
    _run(db, "A00002")
    _patch_reader(monkeypatch, {"A00001": OSError("s3 down"), "A00002": "Jane Testperson"})

    with caplog.at_level("WARNING"):
        summary = script.backfill(db, apply=True)

    assert (summary.failed, summary.written) == (1, 1)
    assert any(r.exc_info for r in caplog.records)
