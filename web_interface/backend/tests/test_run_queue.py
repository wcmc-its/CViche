"""Run queue (#701): the stream module and its only producers -- /start, /retry,
/cancel in ``CVICHE_DISPATCH_MODE=queue``.

Redis is fakeredis, injected by replacing ``run_queue._client``. The API tests
go through the real router with ``get_current_user`` overridden so the status
code (202 for a queued dispatch) and the enqueue contract are exercised at the
wire, not on a helper.
"""
import os
from datetime import datetime
from unittest.mock import MagicMock

import fakeredis
import pytest

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

from app.pipeline import run_queue  # noqa: E402


@pytest.fixture
def fake_redis(monkeypatch):
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer(), decode_responses=True)
    monkeypatch.setattr(run_queue, "_client", lambda: r)
    return r


@pytest.fixture
def queue_mode(monkeypatch):
    monkeypatch.setenv("CVICHE_DISPATCH_MODE", "queue")


def _entries(r):
    return [fields for _, fields in r.xrange(run_queue.STREAM)]


# --- module ----------------------------------------------------------------

def test_fakeredis_supports_every_stream_primitive_the_queue_uses(fake_redis):
    """Canary: a fakeredis upgrade that drops one of these must fail here, not
    silently turn the worker tests vacuous."""
    run_queue.ensure_group()
    run_queue.ensure_group()  # BUSYGROUP tolerated
    eid = run_queue.enqueue("CANARY", start_step=3)
    assert run_queue.read_one("a") == (eid, {"run_id": "CANARY", "enqueued_at": _entries(fake_redis)[0]["enqueued_at"], "start_step": "3"})
    assert run_queue.delivery_count(eid) == 1
    assert run_queue.read_one("a") is None
    fake_redis.xclaim(run_queue.STREAM, run_queue.GROUP, "b", 0, [eid])
    assert run_queue.delivery_count(eid) == 2
    assert run_queue.stats()["pending"] == 1
    run_queue.ack(eid)
    assert run_queue.stats() == {"queued": 1, "pending": 0, "consumers": [], "dead": 0}


def test_autoclaim_returns_the_idle_entry_and_none_when_nothing_is_pending(fake_redis, monkeypatch):
    run_queue.ensure_group()
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)
    assert run_queue.autoclaim_one("b") is None
    eid = run_queue.enqueue("AC1")
    run_queue.read_one("a")
    assert run_queue.autoclaim_one("b") == (eid, {"run_id": "AC1", "enqueued_at": _entries(fake_redis)[0]["enqueued_at"]})
    run_queue.ack(eid)
    assert run_queue.autoclaim_one("b") is None


def test_dead_letter_parks_the_entry_and_acks_the_original(fake_redis):
    run_queue.ensure_group()
    eid = run_queue.enqueue("DL1")
    _, fields = run_queue.read_one("a")
    run_queue.dead_letter(eid, fields)
    dead = fake_redis.xrange(run_queue.DEAD_STREAM)
    assert len(dead) == 1 and dead[0][1]["run_id"] == "DL1" and dead[0][1]["original_id"] == eid
    assert run_queue.stats()["pending"] == 0


def test_dispatch_mode_defaults_to_in_process(monkeypatch):
    monkeypatch.delenv("CVICHE_DISPATCH_MODE", raising=False)
    assert run_queue.dispatch_mode() == "in_process"


def test_client_is_loud_when_redis_url_is_unset(monkeypatch):
    monkeypatch.delenv("CVICHE_REDIS_URL", raising=False)
    monkeypatch.setattr(run_queue, "_client_instance", None)
    with pytest.raises(RuntimeError, match="CVICHE_REDIS_URL"):
        run_queue._client()


# --- API producers ---------------------------------------------------------

def _seed(db, status="created", run_id="RUNQ01"):
    from app.models import Run, Step, User

    user = User(email="queue@example.com", display_name="Q", role="user")
    db.add(user)
    db.commit()
    db.refresh(user)
    run = Run(id=run_id, filename="cv.docx", file_type="docx", status=status, user_id=user.id,
              started_at=datetime(2026, 1, 1, 0, 0, 0))
    db.add(run)
    db.add_all([Step(run_id=run_id, step_number=n, step_name=f"s{n}", status=s)
                for n, s in ((1, "complete"), (2, "error"), (3, "pending"))])
    db.commit()
    return user, run


@pytest.fixture
def as_user_with_input(db, monkeypatch, tmp_path):
    """Route auth + input-file plumbing so a request reaches the dispatch seam."""
    from app.api import runs as runs_api
    from app.auth import get_current_user
    from app.main import app

    def _install(user):
        app.dependency_overrides[get_current_user] = lambda: user
        monkeypatch.setattr(runs_api, "UPLOAD_DIR", tmp_path)
        monkeypatch.setattr(runs_api, "_materialize_input_if_missing", lambda *a: None)
        (tmp_path / "RUNQ01.docx").write_bytes(b"PK")
    yield _install
    app.dependency_overrides.pop(get_current_user, None)


def _status(db, run_id="RUNQ01"):
    from app.models import Run
    db.expire_all()
    return db.query(Run).filter(Run.id == run_id).one().status


def test_start_in_queue_mode_flips_to_queued_and_enqueues_once(client, db, fake_redis, queue_mode, as_user_with_input):
    user, _ = _seed(db)
    as_user_with_input(user)
    resp = client.post("/api/run/RUNQ01/start")
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"message": "Run RUNQ01 queued", "status": "queued"}
    assert _status(db) == "queued"
    assert [e["run_id"] for e in _entries(fake_redis)] == ["RUNQ01"]
    assert "start_step" not in _entries(fake_redis)[0]


def test_start_in_process_mode_is_untouched(client, db, monkeypatch, as_user_with_input):
    """Default mode still schedules the BackgroundTask and answers 200/running."""
    from starlette.background import BackgroundTasks
    from app.pipeline import concurrency

    monkeypatch.delenv("CVICHE_DISPATCH_MODE", raising=False)
    add_task = MagicMock()
    monkeypatch.setattr(BackgroundTasks, "add_task", add_task)
    monkeypatch.setattr(concurrency, "try_acquire_slot", lambda: True)
    user, _ = _seed(db)
    as_user_with_input(user)
    resp = client.post("/api/run/RUNQ01/start")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    assert add_task.call_count == 1
    assert _status(db) == "running"


def test_start_reverts_status_and_answers_503_when_xadd_fails(client, db, monkeypatch, queue_mode, as_user_with_input):
    def boom(*_a, **_k):
        raise ConnectionError("valkey down")
    monkeypatch.setattr(run_queue, "enqueue", boom)
    user, _ = _seed(db, status="paused")
    as_user_with_input(user)
    resp = client.post("/api/run/RUNQ01/start")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "queue_unavailable"
    assert _status(db) == "paused"


def test_start_on_already_queued_run_re_enqueues_without_flip(client, db, fake_redis, queue_mode, as_user_with_input):
    user, _ = _seed(db, status="queued")
    as_user_with_input(user)
    run_queue.enqueue("RUNQ01")
    resp = client.post("/api/run/RUNQ01/start")
    assert resp.status_code == 202
    assert _status(db) == "queued"
    assert [e["run_id"] for e in _entries(fake_redis)] == ["RUNQ01", "RUNQ01"]


def test_start_on_terminal_run_is_still_400_in_queue_mode(client, db, fake_redis, queue_mode, as_user_with_input):
    user, _ = _seed(db, status="complete")
    as_user_with_input(user)
    resp = client.post("/api/run/RUNQ01/start")
    assert resp.status_code == 400
    assert _status(db) == "complete"
    assert _entries(fake_redis) == []


def test_start_in_queue_mode_never_acquires_a_pod_slot(client, db, fake_redis, queue_mode, monkeypatch, as_user_with_input):
    from app.pipeline import concurrency
    monkeypatch.setattr(concurrency, "try_acquire_slot", lambda: pytest.fail("slot acquired in queue mode"))
    user, _ = _seed(db)
    as_user_with_input(user)
    assert client.post("/api/run/RUNQ01/start").status_code == 202


def test_retry_in_queue_mode_resets_steps_and_enqueues_with_start_step(client, db, fake_redis, queue_mode, as_user_with_input):
    from app.models import Step
    user, _ = _seed(db, status="failed")
    as_user_with_input(user)
    resp = client.post("/api/run/RUNQ01/retry/2")
    assert resp.status_code == 202, resp.text
    assert _status(db) == "queued"
    assert _entries(fake_redis)[0]["start_step"] == "2"
    assert {s.step_number: s.status for s in db.query(Step).all()} == {1: "complete", 2: "pending", 3: "pending"}


def test_retry_in_queue_mode_on_non_failed_run_is_400_and_keeps_steps(client, db, fake_redis, queue_mode, as_user_with_input):
    from app.models import Step
    user, _ = _seed(db, status="cancelled")
    as_user_with_input(user)
    assert client.post("/api/run/RUNQ01/retry/2").status_code == 400
    assert _status(db) == "cancelled"
    assert _entries(fake_redis) == []
    assert db.query(Step).filter(Step.step_number == 2).one().status == "error"


def test_cancel_accepts_queued_and_the_worker_then_skips(client, db, fake_redis, queue_mode, as_user_with_input, monkeypatch):
    from app import worker
    from tests.conftest import TestingSessionLocal

    user, _ = _seed(db, status="queued")
    as_user_with_input(user)
    eid = run_queue.enqueue("RUNQ01")
    resp = client.post("/api/run/RUNQ01/cancel")
    assert resp.status_code == 200
    assert _status(db) == "cancelled"

    orchestrator = MagicMock()
    monkeypatch.setattr(worker, "PipelineOrchestrator", orchestrator)
    monkeypatch.setattr(worker, "SessionLocal", TestingSessionLocal)
    run_queue.ensure_group()
    worker.handle(*run_queue.read_one("w1"))
    assert orchestrator.call_count == 0
    assert _status(db) == "cancelled"
    assert run_queue.stats()["pending"] == 0
    assert run_queue.delivery_count(eid) == 0


def test_cancel_still_rejects_a_created_run(client, db, as_user_with_input):
    user, _ = _seed(db, status="created")
    as_user_with_input(user)
    assert client.post("/api/run/RUNQ01/cancel").status_code == 400
