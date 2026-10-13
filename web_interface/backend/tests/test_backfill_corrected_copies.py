"""scripts/backfill_corrected_copies.py: both key schemes, dry run vs apply,
and a second run changing nothing. Synthetic names and files only."""
import importlib.util
import io
import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from app.models import CorrectedCopy, Run, User

_SCRIPT = Path(__file__).parent.parent / "scripts" / "backfill_corrected_copies.py"
_spec = importlib.util.spec_from_file_location("backfill_corrected_copies", _SCRIPT)
script = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(script)

_STAMP = datetime(2026, 10, 10, 15, 0, tzinfo=UTC)


@pytest.fixture
def store(tmp_path, monkeypatch):
    from app.storage.local_storage import LocalRunStorage

    store = LocalRunStorage(str(tmp_path))
    monkeypatch.setattr(script, "get_storage", lambda: store)
    monkeypatch.setattr("app.services.review_loop_service.get_storage", lambda: store)
    return store


def _copy(store, run_id, owner, changes):
    """A stored copy and its diff: ``owner`` None is the pre-#1754 key scheme."""
    stem = f"corrected/{run_id}" + ("" if owner is None else f"_u{owner}")
    store.put_file(run_id, f"{stem}_corrected.docx", b"PK synthetic")
    store.put_file(run_id, f"{stem}_diff.json", json.dumps({"changes": [{}] * changes}).encode())
    os.utime(store._resolve(run_id, f"{stem}_corrected.docx"), (_STAMP.timestamp(), _STAMP.timestamp()))


@pytest.fixture
def seeded(db, store):
    owner = User(email="owner@example.com", display_name="Pat Owner", role="user")
    db.add(owner)
    db.commit()
    for run_id in ("BF0001", "BF0002", "BF0003"):
        db.add(Run(id=run_id, filename="cv.docx", file_type="docx", status="complete", user_id=owner.id))
    db.commit()
    _copy(store, "BF0001", owner.id, 3)  # one copy per uploader (#1754)
    _copy(store, "BF0002", None, 5)  # before #1754: uploader unknown
    return owner  # BF0003 has no copy


def _rows(db):
    return sorted((r.run_id, r.user_id, r.changes, r.uploaded_at) for r in db.query(CorrectedCopy).all())


def test_dry_run_reports_both_key_schemes_and_writes_nothing(db, seeded):
    out = io.StringIO()
    summary = script.backfill(db, apply=False, out=out)
    assert (summary.runs, summary.copies, summary.unknown_uploader, summary.written) == (3, 2, 1, 0)
    assert f"BF0001 user {seeded.id} 3 changes" in out.getvalue()
    assert "BF0002 unknown uploader 5 changes" in out.getvalue()
    assert _rows(db) == []


def test_apply_writes_a_row_per_copy_and_a_second_run_changes_nothing(db, seeded):
    first = script.backfill(db, apply=True, out=io.StringIO())
    assert (first.written, first.existing, first.failed) == (2, 0, 0)
    rows = _rows(db)
    assert rows == [("BF0001", seeded.id, 3, _STAMP), ("BF0002", None, 5, _STAMP)]

    again = script.backfill(db, apply=True, out=io.StringIO())
    assert (again.written, again.existing, again.failed) == (0, 2, 0)
    assert _rows(db) == rows


def test_a_copy_by_an_unknown_user_id_is_counted_failed_and_the_rest_still_run(db, seeded, store):
    _copy(store, "BF0003", 999, 1)
    summary = script.backfill(db, apply=True, out=io.StringIO())
    assert (summary.failed, summary.written) == (1, 2)
    assert [r[0] for r in _rows(db)] == ["BF0001", "BF0002"]
