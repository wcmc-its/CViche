"""Batch upload endpoints (#1114): app/api/batches.py over
app/services/batch_service.py, through the real router.

POST /batches (size cap, quota pre-check in check_rate_limit's 429 shape, the
Teams card), GET /batches and GET /batches/{id} (visibility: submitter and
admins only, 404 for anyone else), and GET /queue (per-queue live workers,
waiting runs and wait estimate). Names in fixtures are invented.
"""
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import fakeredis
import pytest
import redis
from sqlalchemy.orm import object_session

from app.auth import COOKIE_NAME, create_session_cookie
from app.models import Run, RunBatch, RunState, User
from app.pipeline import run_queue
from app.services import batch_service, notifications

T0 = datetime(2026, 10, 1, 9, 0, 0)


def _make_user(db, email="pat@example.com", role="user", **overrides):
    user = User(email=email, display_name=email.split("@")[0].title(), role=role,
                consent_version="1.0", **overrides)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _auth(client, user):
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, object_session(user)))


def _batch(db, user, batch_id="BATCHA", files_submitted=3, created_at=T0):
    batch = RunBatch(id=batch_id, user_id=user.id, files_submitted=files_submitted, created_at=created_at)
    db.add(batch)
    db.commit()
    return batch


def _run(db, run_id, user, *, batch_id=None, status="complete", queued_at=None, created_at=T0, **fields):
    run = Run(id=run_id, filename=f"{run_id.lower()}.docx", file_type="docx", status=status,
              user_id=user.id, batch_id=batch_id, queued_at=queued_at, created_at=created_at,
              started_at=created_at, **fields)
    db.add(run)
    db.commit()
    return run


# --- POST /batches ------------------------------------------------------------

def test_create_batch_stores_a_letters_only_id_owned_by_the_caller(client, db, seed_simple_mode):
    user = _make_user(db)
    _auth(client, user)

    resp = client.post("/api/batches", json={"files_submitted": 3})

    assert resp.status_code == 200, resp.text
    batch_id = resp.json()["id"]
    assert re.fullmatch(r"[A-Z]{6}", batch_id)
    row = db.get(RunBatch, batch_id)
    assert (row.user_id, row.files_submitted) == (user.id, 3)


def test_create_batch_rejects_more_than_fifty_files(client, db, seed_simple_mode):
    _auth(client, _make_user(db, role="admin"))

    over = client.post("/api/batches", json={"files_submitted": batch_service.MAX_BATCH_FILES + 1})
    at_cap = client.post("/api/batches", json={"files_submitted": batch_service.MAX_BATCH_FILES})

    assert over.status_code == 400
    assert over.json()["detail"]["message"] == "A batch can hold at most 50 files."
    assert at_cap.status_code == 200
    assert db.query(RunBatch).count() == 1


def test_create_batch_rejects_an_empty_batch(client, db, seed_simple_mode):
    _auth(client, _make_user(db))
    assert client.post("/api/batches", json={"files_submitted": 0}).status_code == 422
    assert db.query(RunBatch).count() == 0


def test_create_batch_over_the_daily_quota_is_check_rate_limits_429(client, db, seed_simple_mode):
    """A user with 7 runs left today can't create a batch of 8 -- before any
    upload, with the same {error, message, details} body /upload answers."""
    _auth(client, _make_user(db, daily_limit=7))

    over = client.post("/api/batches", json={"files_submitted": 8})

    assert over.status_code == 429
    detail = over.json()["detail"]
    assert detail["error"] == "rate_limited"
    assert detail["message"] == "This batch needs 8 runs, but only 7 of your daily limit of 7 remain."
    assert set(detail["details"]) == {"limit_type", "limit", "used", "resets_at"}
    assert (detail["details"]["limit_type"], detail["details"]["limit"], detail["details"]["used"]) == ("daily", 7, 0)
    assert db.query(RunBatch).count() == 0
    assert client.post("/api/batches", json={"files_submitted": 7}).status_code == 200


def test_create_batch_over_the_monthly_quota_is_429_monthly(client, db, seed_simple_mode):
    _auth(client, _make_user(db, daily_limit=10, monthly_limit=4))

    resp = client.post("/api/batches", json={"files_submitted": 5})

    assert resp.status_code == 429
    assert resp.json()["detail"]["details"]["limit_type"] == "monthly"


def test_create_batch_posts_one_teams_card_and_none_on_a_refusal(client, db, seed_simple_mode, monkeypatch):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    posted = []
    monkeypatch.setattr(notifications._SESSION, "post",
                        lambda url, json=None, timeout=None: posted.append(json) or SimpleNamespace(status_code=200))
    _auth(client, _make_user(db, email="pat.example@example.com", daily_limit=5))

    assert client.post("/api/batches", json={"files_submitted": 6}).status_code == 429
    batch_id = client.post("/api/batches", json={"files_submitted": 4}).json()["id"]
    notifications.flush()

    assert len(posted) == 1
    card = posted[0]["attachments"][0]["content"]
    assert card["body"][0]["text"] == f"CViche batch {batch_id} submitted (4 files)"
    facts = {f["title"]: f["value"] for f in card["body"][1]["facts"]}
    assert facts["Submitted by"] == "Pat.Example"


# --- GET /batches and GET /batches/{id}: visibility ---------------------------

def test_list_batches_shows_a_user_only_their_own_and_an_admin_all(client, db, seed_simple_mode):
    pat = _make_user(db)
    sam = _make_user(db, email="sam@example.com")
    admin = _make_user(db, email="admin@example.com", role="admin")
    _batch(db, pat, "PATBAT", files_submitted=2, created_at=T0)
    _batch(db, sam, "SAMBAT", files_submitted=5, created_at=T0 + timedelta(hours=1))
    _run(db, "PATRUN", pat, batch_id="PATBAT")

    _auth(client, pat)
    mine = client.get("/api/batches").json()["batches"]
    _auth(client, admin)
    every = client.get("/api/batches").json()["batches"]

    assert [(b["id"], b["run_count"], b["files_submitted"], b["submitted_by"]["display_name"]) for b in mine] == [
        ("PATBAT", 1, 2, "Pat"),
    ]
    assert [b["id"] for b in every] == ["SAMBAT", "PATBAT"], "newest first"


@pytest.mark.parametrize("viewer, status", [("owner", 200), ("admin", 200), ("other", 404)])
def test_batch_view_is_visible_to_its_submitter_and_admins_only(client, db, seed_simple_mode, viewer, status):
    owner = _make_user(db)
    users = {
        "owner": owner,
        "admin": _make_user(db, email="admin@example.com", role="admin"),
        "other": _make_user(db, email="sam@example.com"),
    }
    _batch(db, owner)
    _auth(client, users[viewer])

    resp = client.get("/api/batches/BATCHA")

    assert resp.status_code == status
    if status == 404:
        assert resp.json() == client.get("/api/batches/NOSUCH").json(), "same answer as a batch that doesn't exist"


def test_batch_view_counts_statuses_and_lists_runs_in_upload_order(client, db, seed_simple_mode):
    owner = _make_user(db)
    _batch(db, owner, files_submitted=5)
    _run(db, "RUNONE", owner, batch_id="BATCHA", status="complete", created_at=T0, cv_owner_name="Alex Placeholder")
    _run(db, "RUNTWO", owner, batch_id="BATCHA", status="running", created_at=T0 + timedelta(seconds=1))
    _run(db, "RUNTRE", owner, batch_id="BATCHA", status="failed", created_at=T0 + timedelta(seconds=2))
    _run(db, "RUNFOR", owner, batch_id="BATCHA", status="queued", created_at=T0 + timedelta(seconds=3),
         queued_at=T0)
    _run(db, "NOTMINE", owner)  # not in the batch
    _auth(client, owner)

    body = client.get("/api/batches/BATCHA").json()

    assert body["run_count"] == 4 and body["files_submitted"] == 5
    assert body["status_counts"] == {"complete": 1, "running": 1, "queued": 1, "failed": 1, "cancelled": 0, "created": 0}
    assert [(r["run_id"], r["filename"], r["cv_owner_name"]) for r in body["runs"]] == [
        ("RUNONE", "runone.docx", "Alex Placeholder"),
        ("RUNTWO", "runtwo.docx", None),
        ("RUNTRE", "runtre.docx", None),
        ("RUNFOR", "runfor.docx", None),
    ]
    assert body["submitted_by"]["display_name"] == "Pat"


@pytest.mark.parametrize("role, expected", [("admin", 87), ("user", None)])
def test_batch_view_score_is_admin_only(client, db, seed_simple_mode, role, expected):
    owner = _make_user(db, role=role)
    _batch(db, owner)
    _run(db, "SCORED", owner, batch_id="BATCHA", quality_score=87)
    _auth(client, owner)

    rows = client.get("/api/batches/BATCHA").json()["runs"]

    assert [r["quality_score"] for r in rows] == [expected]


def test_queue_position_counts_only_earlier_queued_runs_on_the_batch_queue(client, db, seed_simple_mode):
    """Spec endpoint 7: queued runs in the SAME queue with an earlier queue
    time. A single run queued earlier is on the other queue and never counts;
    another user's earlier batch run does."""
    owner = _make_user(db)
    other = _make_user(db, email="sam@example.com")
    _batch(db, owner)
    _batch(db, other, "OTHERB")
    _run(db, "SINGLE", owner, status="queued", queued_at=T0)
    _run(db, "OTHERR", other, batch_id="OTHERB", status="queued", queued_at=T0 + timedelta(minutes=1))
    _run(db, "FIRSTQ", owner, batch_id="BATCHA", status="queued", queued_at=T0 + timedelta(minutes=2),
         created_at=T0)
    _run(db, "SECNDQ", owner, batch_id="BATCHA", status="queued", queued_at=T0 + timedelta(minutes=3),
         created_at=T0 + timedelta(seconds=1))
    _run(db, "DONEQQ", owner, batch_id="BATCHA", status="complete", queued_at=T0,
         created_at=T0 + timedelta(seconds=2))
    _auth(client, owner)

    rows = client.get("/api/batches/BATCHA").json()["runs"]

    assert {r["run_id"]: r["queue_position"] for r in rows} == {"FIRSTQ": 1, "SECNDQ": 2, "DONEQQ": None}


# --- GET /queue ---------------------------------------------------------------

@pytest.fixture
def fake_valkey(monkeypatch):
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer(), decode_responses=True)
    monkeypatch.setattr(run_queue, "_client", lambda: r)
    monkeypatch.setattr(run_queue, "_producer_client", lambda: r)
    monkeypatch.setenv("CVICHE_REDIS_URL", "redis://fake-valkey:6379/0")
    monkeypatch.setenv("CVICHE_DISPATCH_MODE", "queue")
    return r


def test_queue_reports_only_the_mode_outside_queue_mode(client, db, seed_simple_mode, monkeypatch):
    monkeypatch.delenv("CVICHE_DISPATCH_MODE", raising=False)
    _auth(client, _make_user(db))
    assert client.get("/api/queue").json() == {"dispatch_mode": "in_process", "single": None, "batch": None}


def test_queue_reports_live_workers_waiting_runs_and_the_wait_per_queue(client, db, seed_simple_mode, fake_valkey):
    user = _make_user(db)
    _batch(db, user)
    for consumer in ("general-1", "flex-1"):
        run_queue.ensure_group(run_queue.SINGLE)
        run_queue.read_one(consumer, run_queue.SINGLE, block=False)
    run_queue.ensure_group(run_queue.BATCH)
    run_queue.read_one("flex-1", run_queue.BATCH, block=False)
    for n, seconds in enumerate((600, 900, 1200)):
        _run(db, f"DONE{n:02d}", user, status="complete", total_duration_seconds=seconds,
             completed_at=T0 + timedelta(minutes=n))
    _run(db, "SNGLQA", user, status="queued", queued_at=T0)
    _run(db, "SNGLQB", user, status="queued", queued_at=T0)
    _run(db, "SNGLRN", user, status="running")
    for n in range(3):
        _run(db, f"BATQ{n:02d}", user, batch_id="BATCHA", status="queued", queued_at=T0)
    _auth(client, user)

    body = client.get("/api/queue").json()

    # single: (2 waiting + 1 running) x 900s median / 2 workers = 1350s -> 23 min
    # batch:  (3 waiting + 0 running) x 900s median / 1 worker  = 2700s -> 45 min
    assert body == {
        "dispatch_mode": "queue",
        "single": {"workers": 2, "ahead": 2, "est_wait_minutes": 23},
        "batch": {"workers": 1, "ahead": 3, "est_wait_minutes": 45},
    }


def test_queue_still_answers_when_valkey_is_down(client, db, seed_simple_mode, fake_valkey, monkeypatch):
    def down(queue):
        raise redis.exceptions.ConnectionError("valkey down")
    monkeypatch.setattr(run_queue, "live_consumer_count", down)
    user = _make_user(db)
    _run(db, "SNGLQA", user, status="queued", queued_at=T0)
    _auth(client, user)

    resp = client.get("/api/queue")

    assert resp.status_code == 200
    assert resp.json()["single"] == {"workers": None, "ahead": 1, "est_wait_minutes": None}


def test_queue_has_no_estimate_before_any_run_has_completed(client, db, seed_simple_mode, fake_valkey):
    run_queue.ensure_group(run_queue.SINGLE)
    run_queue.read_one("general-1", run_queue.SINGLE, block=False)
    _auth(client, _make_user(db))
    assert client.get("/api/queue").json()["single"] == {"workers": 1, "ahead": 0, "est_wait_minutes": None}


def test_median_duration_reads_only_the_most_recent_completed_runs(db):
    """Bounded to RECENT_DURATION_SAMPLE: 60 older slow runs must not drag the
    median of the 50 most recent fast ones."""
    user = _make_user(db)
    for n in range(60):
        _run(db, f"OLD{n:03d}", user, status="complete", total_duration_seconds=10_000,
             completed_at=T0 + timedelta(minutes=n))
    for n in range(batch_service.RECENT_DURATION_SAMPLE):
        _run(db, f"NEW{n:03d}", user, status="complete", total_duration_seconds=100,
             completed_at=T0 + timedelta(days=1, minutes=n))
    _run(db, "FAILED", user, status=RunState.FAILED, total_duration_seconds=1,
         completed_at=T0 + timedelta(days=2))

    assert batch_service.recent_median_duration_seconds(db) == 100
