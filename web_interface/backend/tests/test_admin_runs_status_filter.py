"""Admin /api/admin/runs status-filter behavior.

The admin Runs dashboard defaults to hiding never-started "created" runs --
these are abandoned/declined blank-WCM-template uploads that are never advanced
and would otherwise clutter the view. A specific ?status= filters to exactly
that status (including "created" to inspect the abandoned ones); the "all"
sentinel opts back in to every status. Filtering must apply to the total count
as well as the page so pagination stays consistent.
"""
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")


def _seed_mixed_status_runs(db):
    from app.models import Run

    base = datetime(2026, 6, 4, 12, 0, 0)
    db.add_all([
        Run(id="SF_CREATED", filename="a.docx", file_type="docx", status="created",
            started_at=base + timedelta(minutes=5)),
        Run(id="SF_RUNNING", filename="b.docx", file_type="docx", status="running",
            started_at=base + timedelta(minutes=4)),
        Run(id="SF_PAUSED", filename="c.docx", file_type="docx", status="paused",
            started_at=base + timedelta(minutes=3)),
        Run(id="SF_COMPLETE", filename="d.docx", file_type="docx", status="complete",
            started_at=base + timedelta(minutes=2),
            completed_at=base + timedelta(minutes=2, seconds=30)),
        Run(id="SF_FAILED", filename="e.docx", file_type="docx", status="failed",
            started_at=base + timedelta(minutes=1)),
        Run(id="SF_CANCELLED", filename="f.docx", file_type="docx", status="cancelled",
            started_at=base),
    ])
    db.commit()


def _admin_get(client, url):
    from app.main import app
    from app.auth import require_admin

    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(role="admin")
    try:
        return client.get(url)
    finally:
        app.dependency_overrides.pop(require_admin, None)


def _ids(resp):
    return {r["run_id"] for r in resp.json()["runs"]}


def test_default_view_hides_not_started_created_runs(client, db):
    """Default view (no status) excludes never-started "created" runs from both
    the rows and the total count, while keeping every other status."""
    _seed_mixed_status_runs(db)

    resp = _admin_get(client, "/api/admin/runs")

    assert resp.status_code == 200
    ids = _ids(resp)
    assert "SF_CREATED" not in ids
    assert {"SF_RUNNING", "SF_PAUSED", "SF_COMPLETE", "SF_FAILED", "SF_CANCELLED"} <= ids
    assert resp.json()["total"] == 5  # created excluded from the count too


def test_status_created_filters_to_only_not_started(client, db):
    """The "Not started" dropdown option (?status=created) surfaces exactly the
    abandoned runs the default view hides."""
    _seed_mixed_status_runs(db)

    resp = _admin_get(client, "/api/admin/runs?status=created")

    assert resp.status_code == 200
    assert _ids(resp) == {"SF_CREATED"}
    assert resp.json()["total"] == 1


def test_status_all_includes_not_started(client, db):
    """The "All (incl. not-started)" sentinel opts back in to every status."""
    _seed_mixed_status_runs(db)

    resp = _admin_get(client, "/api/admin/runs?status=all")

    assert resp.status_code == 200
    ids = _ids(resp)
    assert "SF_CREATED" in ids
    assert resp.json()["total"] == 6


def test_specific_status_exact_match_still_works(client, db):
    """A concrete status still filters by exact match (unchanged behavior)."""
    _seed_mixed_status_runs(db)

    resp = _admin_get(client, "/api/admin/runs?status=complete")

    assert resp.status_code == 200
    assert _ids(resp) == {"SF_COMPLETE"}
    assert resp.json()["total"] == 1


# --- GET /api/admin/queue/stats (#701) ---------------------------------------

def _seed_queue_view(db):
    from app.models import Run

    old = datetime.now() - timedelta(minutes=30)
    # started_at deliberately does NOT match queued_at (it's left at its
    # original pre-retry value, e.g. hours old): queued_at, not started_at, is
    # the queued-age source (#701 runs.py point 6 -- started_at is re-stamped
    # only when a worker claims the run), so a regression back to reading
    # started_at here would read as hours old, not ~30 minutes.
    db.add_all([
        Run(id="QS_QUEUED", filename="a.docx", file_type="docx", status="queued",
            started_at=datetime.now() - timedelta(hours=5), queued_at=old),
        Run(id="QS_RUNNING", filename="b.docx", file_type="docx", status="running",
            started_at=datetime.now() - timedelta(minutes=5)),
        Run(id="QS_DONE", filename="c.docx", file_type="docx", status="complete", started_at=old),
    ])
    db.commit()


def test_queue_stats_without_redis_reports_disabled_and_the_db_view(client, db, monkeypatch):
    monkeypatch.delenv("CVICHE_REDIS_URL", raising=False)
    monkeypatch.delenv("CVICHE_DISPATCH_MODE", raising=False)
    _seed_queue_view(db)

    resp = _admin_get(client, "/api/admin/queue/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert (body["db"]["queued"], body["db"]["running"]) == (1, 1)
    assert 29 * 60 < body["db"]["oldest_queued_age_s"] < 31 * 60
    assert 4 * 60 < body["db"]["oldest_running_age_s"] < 6 * 60


def test_queue_stats_enabled_false_with_url_set_but_mode_in_process(client, db, monkeypatch):
    """#701 admin_routes.py point 3: 'enabled' is dispatch_mode() == 'queue',
    not merely whether the (shared) URL is configured -- and a disabled GET
    must never write to Valkey (point 6)."""
    import fakeredis
    from app.pipeline import run_queue

    monkeypatch.setenv("CVICHE_REDIS_URL", "redis://unused")
    monkeypatch.delenv("CVICHE_DISPATCH_MODE", raising=False)
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer(), decode_responses=True)
    monkeypatch.setattr(run_queue, "_client", lambda: r)
    monkeypatch.setattr(run_queue, "_producer_client", lambda: r)

    resp = _admin_get(client, "/api/admin/queue/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is False
    assert "error" not in body or body["error"] is None
    assert r.exists(run_queue.STREAM) == 0, "a disabled GET must not create the stream"


def test_queue_stats_reports_valkey_not_configured_in_queue_mode_with_no_url(client, db, monkeypatch):
    """#701 admin_routes.py point 3: queue mode with no URL must answer 200
    with a stable error code, never 500 from run_queue._client()'s RuntimeError."""
    monkeypatch.setenv("CVICHE_DISPATCH_MODE", "queue")
    monkeypatch.delenv("CVICHE_REDIS_URL", raising=False)
    _seed_queue_view(db)

    resp = _admin_get(client, "/api/admin/queue/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["error"] == "valkey_not_configured"
    assert (body["db"]["queued"], body["db"]["running"]) == (1, 1)


def test_queue_stats_with_redis_merges_stream_depth_and_pending(client, db, monkeypatch):
    import fakeredis
    from app.pipeline import run_queue

    monkeypatch.setenv("CVICHE_REDIS_URL", "redis://unused")
    monkeypatch.setenv("CVICHE_DISPATCH_MODE", "queue")
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer(), decode_responses=True)
    monkeypatch.setattr(run_queue, "_client", lambda: r)
    monkeypatch.setattr(run_queue, "_producer_client", lambda: r)
    run_queue.ensure_group()
    run_queue.enqueue("QS_QUEUED")
    run_queue.read_one("pod-a")

    resp = _admin_get(client, "/api/admin/queue/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert (body["stream_length"], body["pending"], body["dead"]) == (1, 1, 0)
    assert body["consumers"] == 1
    assert body["lag"] == 0, "one entry enqueued, one delivered -- nothing outstanding"
    assert body["owners"] == [{"name": "pod-a", "pending": 1}]
    assert body["db"] == {"queued": 0, "running": 0, "oldest_queued_age_s": None, "oldest_running_age_s": None}


def test_queue_stats_answers_200_when_valkey_is_unreachable(client, db, monkeypatch):
    """The endpoint that diagnoses a stuck queue must not 500 when Valkey is the
    problem: the DB view still answers, with a stable error code -- never the
    exception's own text, which can carry a host:port (#701 admin_routes.py
    point 2)."""
    import redis
    from app.pipeline import run_queue

    monkeypatch.setenv("CVICHE_REDIS_URL", "redis://unused")
    monkeypatch.setenv("CVICHE_DISPATCH_MODE", "queue")
    _seed_queue_view(db)

    def down():
        raise redis.exceptions.ConnectionError("Error 111 connecting to valkey:6379")
    monkeypatch.setattr(run_queue, "stats", down)

    resp = _admin_get(client, "/api/admin/queue/stats")

    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["error"] == "valkey_unavailable"
    assert "valkey:6379" not in resp.text
    assert (body["db"]["queued"], body["db"]["running"]) == (1, 1)


def test_queue_stats_requires_admin(client, db):
    assert client.get("/api/admin/queue/stats").status_code in (401, 403)
