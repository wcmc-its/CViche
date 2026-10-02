"""scripts/backfill_input_format.py -- candidate selection, dry-run vs apply."""
import importlib.util
import io
from pathlib import Path

from app.models import Run
from app.storage.base import StorageKeyNotFound

_SCRIPT = Path(__file__).parent.parent / "scripts" / "backfill_input_format.py"
_spec = importlib.util.spec_from_file_location("backfill_input_format", _SCRIPT)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

# Synthetic: seven distinctive template headings is above the WCM threshold.
WCM_TEXT = "\n".join([
    "EMPLOYMENT STATUS", "INSTITUTIONAL/HOSPITAL AFFILIATION", "LICENSURE, BOARD CERTIFICATION",
    "EDUCATIONAL CONTRIBUTIONS", "INSTITUTIONAL LEADERSHIP ACTIVITIES",
    "EXTRAMURAL PROFESSIONAL RESPONSIBILITIES", "INVITATIONS TO SPEAK/PRESENT",
])
OTHER_TEXT = "Summary of a career in synthetic studies."


def _run(db, run_id, *, fmt=None):
    db.add(Run(id=run_id, filename="cv.docx", file_type="docx", status="complete",
               input_format=fmt))
    db.commit()


def _patch_sources(monkeypatch, sources):
    def fake(run_id, file_type):
        value = sources[run_id]
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(script, "read_source_text", fake)


def test_dry_run_reports_and_writes_nothing(db, monkeypatch):
    _run(db, "B00001")
    _run(db, "B00002")
    _patch_sources(monkeypatch, {"B00001": WCM_TEXT, "B00002": OTHER_TEXT})
    out = io.StringIO()

    summary = script.backfill(db, apply=False, out=out)

    assert (summary.candidates, summary.wcm, summary.other, summary.written) == (2, 1, 1, 0)
    assert "B00001 wcm 7" in out.getvalue() and "B00002 other 0" in out.getvalue()
    assert db.query(Run).filter(Run.input_format.isnot(None)).count() == 0


def test_apply_writes_format_and_score(db, monkeypatch):
    _run(db, "B00001")
    _patch_sources(monkeypatch, {"B00001": WCM_TEXT})

    summary = script.backfill(db, apply=True, out=io.StringIO())

    assert summary.written == 1
    db.expire_all()
    run = db.get(Run, "B00001")
    assert (run.input_format, run.input_format_score) == ("wcm", 7)


def test_skips_runs_that_already_have_a_format(db, monkeypatch):
    _run(db, "B00001", fmt="other")
    _patch_sources(monkeypatch, {})

    assert script.backfill(db, apply=True, out=io.StringIO()).candidates == 0


def test_missing_source_and_empty_text_stay_null(db, monkeypatch):
    _run(db, "B00001")
    _run(db, "B00002")
    _patch_sources(monkeypatch, {"B00001": StorageKeyNotFound("gone"), "B00002": ""})

    summary = script.backfill(db, apply=True, out=io.StringIO())

    assert (summary.no_source, summary.undetermined, summary.written) == (1, 1, 0)
    assert db.query(Run).filter(Run.input_format.isnot(None)).count() == 0


def test_read_failure_is_counted_logged_and_does_not_stop_the_batch(db, monkeypatch, caplog):
    _run(db, "B00001")
    _run(db, "B00002")
    _patch_sources(monkeypatch, {"B00001": OSError("s3 down"), "B00002": OTHER_TEXT})

    with caplog.at_level("WARNING"):
        summary = script.backfill(db, apply=True, out=io.StringIO())

    assert (summary.failed, summary.other, summary.written) == (1, 1, 1)
    assert "B00001" in caplog.text
