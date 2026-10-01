"""scripts/backfill_quality_score.py -- candidate selection, dry-run vs apply."""
import importlib.util
from pathlib import Path

from app.models import Run

_SCRIPT = Path(__file__).parent.parent / "scripts" / "backfill_quality_score.py"
_spec = importlib.util.spec_from_file_location("backfill_quality_score", _SCRIPT)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)


def _run(db, run_id, *, status="complete", score=None):
    db.add(Run(id=run_id, filename="cv.docx", file_type="docx", status=status,
               quality_score=score))
    db.commit()


def _patch_cache(monkeypatch, cache):
    def fake(run_id):
        value = cache[run_id]
        if isinstance(value, Exception):
            raise value
        return value
    monkeypatch.setattr(script, "load_cached_score", fake)


CAPPED = {"totalScore": 25, "raw_score_before_caps": 80.0, "hard_fail_caps_applied": [25]}


def test_dry_run_counts_and_writes_nothing(db, monkeypatch):
    _run(db, "B00001")
    _run(db, "B00002")
    _patch_cache(monkeypatch, {"B00001": CAPPED, "B00002": None})

    summary = script.backfill(db, apply=False)

    assert (summary.candidates, summary.resolved, summary.missing, summary.failed,
            summary.written) == (2, 1, 1, 0, 0)
    assert db.query(Run).filter(Run.quality_score.isnot(None)).count() == 0


def test_apply_copies_score_band_and_cap_from_the_cache(db, monkeypatch):
    _run(db, "B00001")
    _run(db, "B00002")
    _patch_cache(monkeypatch, {"B00001": CAPPED, "B00002": {"totalScore": 91}})

    summary = script.backfill(db, apply=True)

    assert summary.written == 2
    db.expire_all()
    a, b = db.get(Run, "B00001"), db.get(Run, "B00002")
    assert (a.quality_score, a.quality_band, a.quality_cap) == (25, "RED", 25)
    assert (b.quality_score, b.quality_band, b.quality_cap) == (91, "GREEN", None)


def test_a_cache_without_a_usable_score_counts_as_missing(db, monkeypatch):
    _run(db, "B00001")
    _patch_cache(monkeypatch, {"B00001": {"flags": []}})

    summary = script.backfill(db, apply=True)

    assert (summary.missing, summary.written) == (1, 0)


def test_skips_scored_and_incomplete_runs(db, monkeypatch):
    _run(db, "B00001", score=70)
    _run(db, "B00002", status="running")
    _patch_cache(monkeypatch, {})

    assert script.backfill(db, apply=True).candidates == 0


def test_read_failure_is_counted_and_does_not_stop_the_batch(db, monkeypatch, caplog):
    _run(db, "B00001")
    _run(db, "B00002")
    _patch_cache(monkeypatch, {"B00001": OSError("s3 down"), "B00002": CAPPED})

    with caplog.at_level("WARNING"):
        summary = script.backfill(db, apply=True)

    assert (summary.failed, summary.written) == (1, 1)
    assert any(r.exc_info for r in caplog.records)
