"""Orphaned-run reaper: sweep function, admin endpoint, and storage deletes.

Follow-up to #170/#171: PR #171 stops NEW orphans (a failed upload no longer
commits a 'created' run); this reaps the EXISTING backlog of runs stuck at
status='created' (uploaded but never started) plus their child rows and leftover
storage objects (runs/{id}/input/ and the by-submitter index).
"""
import os
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")


class _FakeStorage:
    """Records delete calls and returns canned object counts."""
    def __init__(self):
        self.deleted_runs = []
        self.deleted_prefixes = []

    def delete_run(self, run_id):
        self.deleted_runs.append(run_id)
        return 2

    def delete_global_prefix(self, prefix):
        self.deleted_prefixes.append(prefix)
        return 1


def _seed_user(db, email="submitter@example.com"):
    from app.models import User
    user = User(email=email, display_name="Submitter", role="user")
    db.add(user)
    db.flush()
    return user


def _seed_run(db, run_id, status, started_at, user_id=None, steps=0):
    from app.models import Run, Step
    db.add(Run(id=run_id, filename="cv.docx", file_type="docx",
               status=status, started_at=started_at, user_id=user_id))
    for i in range(steps):
        db.add(Step(run_id=run_id, step_number=i + 1, stage_id=f"s{i}",
                    step_name=f"Step {i}", status="pending"))
    db.commit()


# ---------------------------------------------------------------------------
# Sweep function
# ---------------------------------------------------------------------------

def test_reap_deletes_old_created_run_with_children_and_storage(db):
    """Only an old 'created' run is reaped -- with its Step children and its
    storage (run namespace + by-submitter index). Younger and non-'created'
    runs are untouched."""
    from app.models import Run, Step
    from app.services.run_service import reap_orphaned_created_runs

    user = _seed_user(db)
    old = datetime.now() - timedelta(hours=48)
    young = datetime.now()
    _seed_run(db, "OLD001", "created", old, user_id=user.id, steps=3)
    _seed_run(db, "YOUNG1", "created", young, user_id=user.id, steps=2)  # too new
    _seed_run(db, "RUN001", "running", old, user_id=user.id, steps=2)    # not 'created'
    _seed_run(db, "DONE01", "complete", old, user_id=user.id, steps=2)   # not 'created'

    fake = _FakeStorage()
    with patch("app.services.run_service.get_storage", return_value=fake):
        result = reap_orphaned_created_runs(db, older_than_hours=24)

    assert result["candidates"] == 1
    assert result["reaped"] == 1
    assert result["objects_deleted"] == 3  # delete_run (2) + delete_global_prefix (1)

    assert db.query(Run).filter(Run.id == "OLD001").first() is None
    assert db.query(Step).filter(Step.run_id == "OLD001").count() == 0
    for rid in ("YOUNG1", "RUN001", "DONE01"):
        assert db.query(Run).filter(Run.id == rid).first() is not None
        assert db.query(Step).filter(Step.run_id == rid).count() == 2

    assert fake.deleted_runs == ["OLD001"]
    assert fake.deleted_prefixes == [f"by-submitter/{user.email.lower()}/OLD001/"]


def test_reap_dry_run_changes_nothing(db):
    """dry_run reports candidates but deletes neither rows nor storage."""
    from app.models import Run
    from app.services.run_service import reap_orphaned_created_runs

    user = _seed_user(db)
    _seed_run(db, "OLD002", "created", datetime.now() - timedelta(hours=48),
              user_id=user.id, steps=2)

    fake = _FakeStorage()
    with patch("app.services.run_service.get_storage", return_value=fake):
        result = reap_orphaned_created_runs(db, older_than_hours=24, dry_run=True)

    assert result["candidates"] == 1
    assert result["reaped"] == 0
    assert result["run_ids"] == ["OLD002"]
    assert db.query(Run).filter(Run.id == "OLD002").first() is not None
    assert fake.deleted_runs == []


# ---------------------------------------------------------------------------
# Admin endpoint
# ---------------------------------------------------------------------------

def _as_admin(client, fn):
    from app.main import app
    from app.auth import require_admin
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
        role="admin", email="admin@example.com")
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(require_admin, None)


def _as_user(client, fn):
    """Override the underlying current-user dep so the REAL require_admin runs
    and returns 403 -- exercises the actual authorization guard."""
    from app.main import app
    from app.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        role="user", email="user@example.com")
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_non_admin_cannot_reap(client, db):
    resp = _as_user(client, lambda: client.post("/api/admin/runs/reap-orphans"))
    assert resp.status_code == 403


def test_admin_dry_run_previews_without_deleting(client, db):
    from app.models import Run
    user = _seed_user(db)
    _seed_run(db, "OLD003", "created", datetime.now() - timedelta(hours=48),
              user_id=user.id, steps=2)

    resp = _as_admin(client, lambda: client.post("/api/admin/runs/reap-orphans?dry_run=true"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["candidates"] == 1
    assert body["reaped"] == 0
    assert db.query(Run).filter(Run.id == "OLD003").first() is not None


def test_admin_reap_removes_orphan(client, db):
    from app.models import Run
    user = _seed_user(db)
    _seed_run(db, "OLD004", "created", datetime.now() - timedelta(hours=48),
              user_id=user.id, steps=2)

    fake = _FakeStorage()
    with patch("app.services.run_service.get_storage", return_value=fake):
        resp = _as_admin(client, lambda: client.post("/api/admin/runs/reap-orphans"))
    assert resp.status_code == 200
    assert resp.json()["reaped"] == 1
    assert db.query(Run).filter(Run.id == "OLD004").first() is None


# ---------------------------------------------------------------------------
# Local storage delete methods
# ---------------------------------------------------------------------------

def test_local_storage_delete_run_and_global_prefix(tmp_path):
    from app.storage.local_storage import LocalRunStorage
    s = LocalRunStorage(base_dir=str(tmp_path))
    s.put_file("R1", "input/cv.docx", b"data")
    s.put_file("R1", "input/manifest.json", b"{}")
    s.put_global("by-submitter/e@x.edu/R1/manifest.json", b"{}")

    assert s.delete_run("R1") == 2
    assert s.exists("R1", "input/cv.docx") is False
    assert s.delete_run("R1") == 0  # idempotent

    assert s.delete_global_prefix("by-submitter/e@x.edu/R1/") == 1
    assert s.delete_global_prefix("by-submitter/e@x.edu/R1/") == 0  # idempotent


def test_local_storage_delete_guards_empty_prefix(tmp_path):
    from app.storage.local_storage import LocalRunStorage
    s = LocalRunStorage(base_dir=str(tmp_path))
    with pytest.raises(ValueError):
        s.delete_global_prefix("")
    with pytest.raises(ValueError):
        s.delete_global_prefix("/")
