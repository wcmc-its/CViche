"""Orphaned-run reaper: sweep function, admin endpoint, and storage deletes.

Follow-up to #170/#171: PR #171 stops NEW orphans (a failed upload no longer
commits a 'created' run); this reaps the EXISTING backlog of runs stuck at
status='created' (uploaded but never started) plus their child rows and leftover
storage objects (runs/{id}/input/ and the by-submitter index).
"""
import os
from collections.abc import Iterator
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy.orm import Session

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
    from app.auth import require_admin
    from app.main import app
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
        role="admin", email="admin@example.com")
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(require_admin, None)


def _as_user(client, fn):
    """Override the underlying current-user dep so the REAL require_admin runs
    and returns 403 -- exercises the actual authorization guard."""
    from app.auth import get_current_user
    from app.main import app
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


# ---------------------------------------------------------------------------
# DELETE /api/admin/runs/{run_id} (#683)
# ---------------------------------------------------------------------------

def test_non_admin_cannot_delete_run(client, db):
    resp = _as_user(client, lambda: client.delete("/api/admin/runs/ANYRUN"))
    assert resp.status_code == 403


def test_admin_delete_unknown_run_is_404(client, db):
    resp = _as_admin(client, lambda: client.delete("/api/admin/runs/NOSUCH"))
    assert resp.status_code == 404


def test_admin_delete_running_run_is_409_and_untouched(client, db):
    from app.models import Run
    _seed_run(db, "RUN683", "running", datetime.now())

    fake = _FakeStorage()
    with patch("app.services.run_service.get_storage", return_value=fake):
        resp = _as_admin(client, lambda: client.delete("/api/admin/runs/RUN683"))
    assert resp.status_code == 409
    assert db.query(Run).filter(Run.id == "RUN683").first() is not None
    assert fake.deleted_runs == []


def _seed_children(db, run_id, user_id):
    from app.models import Feedback, LLMUsage, Log, RunMetrics
    db.add_all([
        Log(run_id=run_id, message="m"),
        LLMUsage(run_id=run_id, step_number=1, model="m", prompt_tokens=1,
                 completion_tokens=1, total_tokens=2, cost=0.0),
        Feedback(run_id=run_id, user_id=user_id, reviewer_role="self",
                 overall_usefulness=3, manual_conversion_effort="1 hour",
                 correction_effort="1 hour", biggest_issue="none",
                 likelihood_to_recommend=3),
        RunMetrics(run_id=run_id),
    ])
    db.commit()


_CHILD_MODELS = ("Step", "Log", "LLMUsage", "Feedback", "RunMetrics")


def _child_counts(db, run_id):
    import app.models as m
    return {n: db.query(getattr(m, n)).filter(getattr(m, n).run_id == run_id).count()
            for n in _CHILD_MODELS}


def test_admin_delete_complete_run_removes_only_target_and_logs_audit(client, db, caplog):
    import logging

    from app.audit_events import RUN_DELETED
    from app.models import Run
    user = _seed_user(db, email="Withdraw@Example.com")
    other = _seed_user(db, email="keeper@example.com")
    # Two other runs sit on either side of the target so a find_run that
    # ignores run_id (first(), last()) cannot pass by luck of ordering.
    for rid, uid in (("AAA683", other.id), ("DONE683", user.id), ("ZZZ683", other.id)):
        _seed_run(db, rid, "complete", datetime.now() - timedelta(days=30),
                  user_id=uid, steps=2)
        _seed_children(db, rid, uid)
    before_other = _child_counts(db, "AAA683")
    assert set(before_other.values()) == {2, 1} and before_other["Step"] == 2

    fake = _FakeStorage()
    with patch("app.services.run_service.get_storage", return_value=fake), \
            caplog.at_level(logging.INFO, logger="app.services.admin_run_service"):
        resp = _as_admin(client, lambda: client.delete("/api/admin/runs/DONE683"))
    assert resp.status_code == 204
    db.rollback()  # discard uncommitted state: only a committed delete survives
    assert db.query(Run).filter(Run.id == "DONE683").first() is None
    assert _child_counts(db, "DONE683") == dict.fromkeys(_CHILD_MODELS, 0)
    for rid in ("AAA683", "ZZZ683"):
        assert db.query(Run).filter(Run.id == rid).first() is not None
        assert _child_counts(db, rid) == before_other
    assert fake.deleted_runs == ["DONE683"]
    assert fake.deleted_prefixes == ["by-submitter/withdraw@example.com/DONE683/"]
    audit = [r for r in caplog.records if r.getMessage() == RUN_DELETED]
    assert len(audit) == 1
    assert audit[0].run_id == "DONE683"
    assert audit[0].admin == "admin@example.com"
    assert audit[0].objects_deleted == 3


class _FlakyStorage(_FakeStorage):
    """delete_run raises until .healthy is set."""
    healthy = False

    def delete_run(self, run_id):
        if not self.healthy:
            raise OSError("store unavailable")
        return super().delete_run(run_id)


def test_admin_delete_storage_failure_is_500_and_rows_survive_then_retry_succeeds(client, db):
    from app.models import Run
    user = _seed_user(db)
    for rid in ("BAD683", "OTHER683"):
        _seed_run(db, rid, "complete", datetime.now(), user_id=user.id, steps=2)
        _seed_children(db, rid, user.id)
    before = _child_counts(db, "BAD683")

    flaky = _FlakyStorage()
    with patch("app.services.run_service.get_storage", return_value=flaky):
        resp = _as_admin(client, lambda: client.delete("/api/admin/runs/BAD683"))
        assert resp.status_code == 500
        assert "safe to retry" in resp.text
        db.rollback()
        assert db.query(Run).filter(Run.id == "BAD683").first() is not None
        assert _child_counts(db, "BAD683") == before

        flaky.healthy = True  # storage recovers: the same request now succeeds
        resp = _as_admin(client, lambda: client.delete("/api/admin/runs/BAD683"))
    assert resp.status_code == 204
    db.rollback()
    assert db.query(Run).filter(Run.id == "BAD683").first() is None
    assert _child_counts(db, "BAD683") == dict.fromkeys(_CHILD_MODELS, 0)
    assert db.query(Run).filter(Run.id == "OTHER683").first() is not None
    assert flaky.deleted_runs == ["BAD683"]


def test_admin_delete_index_prefix_failure_is_500_and_rows_survive(client, db):
    from app.models import Run
    user = _seed_user(db)
    _seed_run(db, "IDX683", "complete", datetime.now(), user_id=user.id)

    class _PrefixFails(_FakeStorage):
        def delete_global_prefix(self, prefix):
            raise OSError("index unavailable")

    with patch("app.services.run_service.get_storage", return_value=_PrefixFails()):
        resp = _as_admin(client, lambda: client.delete("/api/admin/runs/IDX683"))
    assert resp.status_code == 500
    db.rollback()
    assert db.query(Run).filter(Run.id == "IDX683").first() is not None


def test_reaper_stays_best_effort_when_storage_fails(db):
    """The reaper must keep logging-and-continuing: rows go, the sweep reports
    the run reaped, and the storage error does not propagate."""
    from app.models import Run
    from app.services.run_service import reap_orphaned_created_runs
    user = _seed_user(db)
    _seed_run(db, "ORPH683", "created", datetime.now() - timedelta(hours=48),
              user_id=user.id)
    with patch("app.services.run_service.get_storage", return_value=_FlakyStorage()):
        result = reap_orphaned_created_runs(db, older_than_hours=24)
    assert result["reaped"] == 1
    assert db.query(Run).filter(Run.id == "ORPH683").first() is None


def test_delete_run_and_artifacts_rolls_back_on_db_failure(db):
    from app.models import Run
    from app.services.run_service import delete_run_and_artifacts
    _seed_run(db, "FAIL683", "complete", datetime.now())
    run = db.query(Run).filter(Run.id == "FAIL683").first()

    fake = _FakeStorage()
    with patch("app.services.run_service.get_storage", return_value=fake), \
            patch("app.services.run_service._delete_run_rows",
                  side_effect=RuntimeError("db down")), \
            patch.object(db, "rollback", wraps=db.rollback) as rollback:
        with pytest.raises(RuntimeError, match="db down"):
            delete_run_and_artifacts(db, run)
    rollback.assert_called_once()
    assert db.query(Run).filter(Run.id == "FAIL683").first() is not None


# ---------------------------------------------------------------------------
# Admin endpoints: response body, threshold override, error text and audit
# lines, pinned before they move out of the route handlers (#335).
# ---------------------------------------------------------------------------

def test_admin_reap_body_and_audit_line(client, db, caplog):
    import logging
    user = _seed_user(db)
    _seed_run(db, "OLD005", "created", datetime.now() - timedelta(hours=48), user_id=user.id)

    with caplog.at_level(logging.INFO):
        resp = _as_admin(client, lambda: client.post("/api/admin/runs/reap-orphans?dry_run=true"))

    assert resp.json() == {"candidates": 1, "reaped": 0, "objects_deleted": 0,
                           "run_ids": ["OLD005"], "dry_run": True}
    audit = [r.getMessage() for r in caplog.records if r.getMessage().startswith("admin_reap_orphans")]
    assert audit == ["admin_reap_orphans: admin=admin@example.com dry_run=True candidates=1 reaped=0 objects=0"]


def test_admin_reap_older_than_hours_overrides_the_default_threshold(client, db):
    _seed_run(db, "YOUNG05", "created", datetime.now() - timedelta(hours=5))

    default = _as_admin(client, lambda: client.post("/api/admin/runs/reap-orphans?dry_run=true"))
    override = _as_admin(
        client, lambda: client.post("/api/admin/runs/reap-orphans?dry_run=true&older_than_hours=1"))
    zero = _as_admin(client, lambda: client.post("/api/admin/runs/reap-orphans?older_than_hours=0"))

    assert default.json()["run_ids"] == []
    assert override.json()["run_ids"] == ["YOUNG05"]
    assert zero.status_code == 422  # ge=1


def test_admin_delete_error_messages(client, db):
    _seed_run(db, "BUSY683", "running", datetime.now())

    missing = _as_admin(client, lambda: client.delete("/api/admin/runs/NOSUCH"))
    busy = _as_admin(client, lambda: client.delete("/api/admin/runs/BUSY683"))

    assert missing.json()["detail"] == {"error": "not_found", "message": "Run not found."}
    assert busy.json()["detail"] == {
        "error": "conflict",
        "message": "Run is still running; wait for it to finish before deleting."}


def test_admin_delete_allows_a_cancelled_run(client, db):
    from app.models import Run
    _seed_run(db, "CANC683", "cancelled", datetime.now())

    with patch("app.services.run_service.get_storage", return_value=_FakeStorage()):
        resp = _as_admin(client, lambda: client.delete("/api/admin/runs/CANC683"))

    assert resp.status_code == 204
    db.rollback()
    assert db.query(Run).filter(Run.id == "CANC683").first() is None


def test_admin_delete_failure_logs_the_traceback_and_returns_the_retry_message(client, db, caplog):
    import logging
    _seed_run(db, "LOG683", "complete", datetime.now())

    with patch("app.services.run_service.get_storage", return_value=_FlakyStorage()), \
            caplog.at_level(logging.INFO):
        resp = _as_admin(client, lambda: client.delete("/api/admin/runs/LOG683"))

    assert resp.json()["detail"] == {
        "error": "internal_error", "message": "Run deletion failed; it is safe to retry."}
    failed = [r for r in caplog.records if r.getMessage() == "Admin delete of run LOG683 failed"]
    assert len(failed) == 1 and failed[0].exc_info is not None


@pytest.fixture
def enforced_foreign_keys() -> Iterator[None]:
    """Enforce FKs the way MySQL InnoDB does. The suite's SQLite engine leaves
    them off, so a delete that MySQL refuses would otherwise pass here."""
    from sqlalchemy import text

    from tests.conftest import engine
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
    yield
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))


def test_delete_run_unlinks_the_inbox_file_it_was_submitted_from(db: Session, enforced_foreign_keys: None) -> None:
    """#408: inbound_files.run_id references runs.id with no ON DELETE rule.
    Deleting a run submitted from the inbox (#1298) must clear that link, or
    the row delete fails after the run's storage is already gone."""
    from app.models import InboundFile, InboundFileStatus, InboundMessage, Run
    from app.services.run_service import delete_run_and_artifacts

    user = _seed_user(db)
    _seed_run(db, "MAIL408", "complete", datetime.now(), user_id=user.id, steps=2)
    message = InboundMessage(s3_key="inbound/msg-408", status="accepted", file_count=1, user_id=user.id)
    db.add(message)
    db.flush()
    db.add(InboundFile(inbound_message_id=message.id, user_id=user.id, filename="cv.docx", size_bytes=1,
                       sha256="0" * 64, storage_key="inbox/408/", status=InboundFileStatus.SUBMITTED,
                       run_id="MAIL408"))
    db.commit()

    fake = _FakeStorage()
    with patch("app.services.run_service.get_storage", return_value=fake):
        delete_run_and_artifacts(db, db.query(Run).filter(Run.id == "MAIL408").one())

    assert db.query(Run).filter(Run.id == "MAIL408").first() is None
    inbox_file = db.query(InboundFile).one()
    assert inbox_file.run_id is None
    assert inbox_file.status == InboundFileStatus.SUBMITTED
