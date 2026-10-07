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


def test_queue_stats_reports_the_batch_stream_beside_the_single_one(client, db, monkeypatch):
    """#1114: ``queues`` has each stream's depth, pending, lag and
    dead-letter count; the top-level fields stay the single stream's."""
    import fakeredis
    from app.pipeline import run_queue

    monkeypatch.setenv("CVICHE_REDIS_URL", "redis://unused")
    monkeypatch.setenv("CVICHE_DISPATCH_MODE", "queue")
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer(), decode_responses=True)
    monkeypatch.setattr(run_queue, "_client", lambda: r)
    monkeypatch.setattr(run_queue, "_producer_client", lambda: r)
    run_queue.ensure_group(run_queue.SINGLE)
    run_queue.ensure_group(run_queue.BATCH)
    run_queue.enqueue("QS_SINGLE")
    run_queue.enqueue("QS_BATCH_A", run_queue.BATCH)
    run_queue.enqueue("QS_BATCH_B", run_queue.BATCH)
    run_queue.enqueue("QS_BATCH_C", run_queue.BATCH)
    batch_entry_id, fields = run_queue.read_one("flex-1", run_queue.BATCH, block=False)
    run_queue.dead_letter(batch_entry_id, fields, run_queue.BATCH)

    resp = _admin_get(client, "/api/admin/queue/stats")

    assert resp.status_code == 200
    body = resp.json()
    single = body["queues"]["single"]
    assert {key: body[key] for key in single} == single, "top-level fields are still the single stream's"
    assert (single["stream_length"], single["pending"], single["consumers"], single["dead"]) == (1, 0, 0, 0)
    batch = body["queues"]["batch"]
    assert (batch["stream_length"], batch["pending"], batch["consumers"], batch["dead"]) == (2, 0, 1, 1)
    # lag is whatever the server reports for the batch group (fakeredis's own
    # lag arithmetic is not Valkey's), read from the batch stream, not single.
    assert batch["lag"] == r.xinfo_groups(run_queue.BATCH_STREAM)[0]["lag"]


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

    def down(queue):
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


# --- quality score evidence (#745) ------------------------------------------
# The runs listing and the compute endpoint carry quality_score.score_run's
# data_complete / missing_evidence, so the admin view can mark a score that
# was computed with a scored artifact missing.

_INCOMPLETE_SCORE = {
    "totalScore": 20,
    "band": "RED (re-run / do-not-deliver)",
    "dimensionScores": [],
    "flags": [],
    "data_complete": False,
    "missing_evidence": ["docx: no docx found"],
}


def _admin_post(client, url):
    from app.main import app
    from app.auth import require_admin

    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(role="admin")
    try:
        return client.post(url)
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_runs_listing_carries_score_evidence(client, db, monkeypatch):
    """A cached incomplete score reaches the row as typed fields; a cache
    written before data_complete existed reads as unknown (None), and an
    unscored run carries neither."""
    from app.api import admin_routes

    _seed_mixed_status_runs(db)
    legacy = {"totalScore": 90, "band": "GREEN (ship)"}
    cache = {"SF_COMPLETE": _INCOMPLETE_SCORE, "SF_FAILED": legacy}
    monkeypatch.setattr(admin_routes, "get_cached_score", cache.get)

    resp = _admin_get(client, "/api/admin/runs")

    assert resp.status_code == 200
    rows = {r["run_id"]: r for r in resp.json()["runs"]}
    assert rows["SF_COMPLETE"]["quality_data_complete"] is False
    assert rows["SF_COMPLETE"]["quality_missing_evidence"] == ["docx: no docx found"]
    assert rows["SF_FAILED"]["quality_data_complete"] is None
    assert rows["SF_FAILED"]["quality_missing_evidence"] == []
    assert rows["SF_RUNNING"]["quality_data_complete"] is None
    assert rows["SF_RUNNING"]["quality_missing_evidence"] == []


def test_compute_score_returns_score_evidence(client, db, monkeypatch):
    """POST /admin/run/{id}/score returns data_complete and missing_evidence."""
    from app.services import admin_run_service

    _seed_mixed_status_runs(db)
    monkeypatch.setattr(admin_run_service, "compute_and_cache_score", lambda _rid: _INCOMPLETE_SCORE)

    resp = _admin_post(client, "/api/admin/run/SF_COMPLETE/score")

    assert resp.status_code == 200
    body = resp.json()
    assert body["totalScore"] == 20
    assert body["data_complete"] is False
    assert body["missing_evidence"] == ["docx: no docx found"]


def test_compute_score_writes_the_run_quality_columns(client, db, monkeypatch):
    """The rescore endpoint copies score, band and cap onto the run row."""
    from app.services import admin_run_service
    from app.models import Run

    _seed_mixed_status_runs(db)
    capped = {**_INCOMPLETE_SCORE, "totalScore": 25, "raw_score_before_caps": 80.0,
              "hard_fail_caps_applied": [25]}
    monkeypatch.setattr(admin_run_service, "compute_and_cache_score", lambda _rid: capped)

    assert _admin_post(client, "/api/admin/run/SF_COMPLETE/score").status_code == 200

    db.expire_all()
    run = db.get(Run, "SF_COMPLETE")
    assert (run.quality_score, run.quality_band, run.quality_cap) == (25, "RED", 25)


def test_compute_score_404s_for_an_unknown_run_and_for_no_scorable_outputs(client, db, monkeypatch):
    """Two distinct 404s; neither writes the quality columns."""
    from app.services import admin_run_service
    from app.models import Run

    _seed_mixed_status_runs(db)
    monkeypatch.setattr(admin_run_service, "compute_and_cache_score", lambda _rid: None)

    unknown = _admin_post(client, "/api/admin/run/NOSUCH/score")
    unscorable = _admin_post(client, "/api/admin/run/SF_COMPLETE/score")

    assert unknown.json()["detail"] == {"error": "not_found", "message": "Run not found"}
    assert unscorable.json()["detail"] == {
        "error": "not_found", "message": "No scorable outputs available for this run"}
    db.expire_all()
    assert db.get(Run, "SF_COMPLETE").quality_score is None


def test_compute_score_fills_defaults_for_keys_the_result_lacks(client, db, monkeypatch):
    from app.services import admin_run_service

    _seed_mixed_status_runs(db)
    monkeypatch.setattr(admin_run_service, "compute_and_cache_score", lambda _rid: {"totalScore": 50})

    resp = _admin_post(client, "/api/admin/run/SF_COMPLETE/score")

    assert resp.json() == {"run_id": "SF_COMPLETE", "totalScore": 50, "band": "", "dimensionScores": [],
                           "flags": [], "data_complete": None, "missing_evidence": []}


# --- GET /api/admin/runs: user filter, row fields, pagination ----------------

def _seed_owned_runs(db):
    """Three runs, newest first R3 > R2 > R1; R1 and R3 are Alice's, R2 is
    Bob's, and only R3 has feedback."""
    from app.models import Feedback, Run, User

    alice = User(email="Alice@Example.com", display_name="Alice", role="user")
    bob = User(email="bob@example.com", display_name="Bob", role="user")
    db.add_all([alice, bob])
    db.flush()
    base = datetime(2026, 6, 4, 12, 0, 0)
    db.add_all([
        Run(id="R1", filename="one.docx", file_type="docx", status="complete", user_id=alice.id,
            started_at=base, completed_at=base + timedelta(seconds=90), total_cost=0.123456),
        Run(id="R2", filename="two.docx", file_type="docx", status="failed", user_id=bob.id,
            started_at=base + timedelta(minutes=1)),
        Run(id="R3", filename="three.docx", file_type="docx", status="complete", user_id=alice.id,
            started_at=base + timedelta(minutes=2), completed_at=base + timedelta(minutes=3),
            total_duration_seconds=42),
        Feedback(run_id="R3", user_id=bob.id, reviewer_role="staff", overall_usefulness=3,
                 manual_conversion_effort="1 hour", correction_effort="1 hour",
                 biggest_issue="none", likelihood_to_recommend=3),
    ])
    db.commit()


def test_runs_listing_row_fields_and_newest_first_order(client, db):
    _seed_owned_runs(db)

    body = _admin_get(client, "/api/admin/runs").json()

    assert [r["run_id"] for r in body["runs"]] == ["R3", "R2", "R1"]
    rows = {r["run_id"]: r for r in body["runs"]}
    assert rows["R3"]["has_feedback"] is True and rows["R1"]["has_feedback"] is False
    assert (rows["R1"]["user_email"], rows["R1"]["user_display_name"]) == ("Alice@Example.com", "Alice")
    assert rows["R1"]["total_cost"] == 0.1235 and rows["R2"]["total_cost"] == 0.0
    # persisted duration first, then wall-clock, then nothing
    assert (rows["R3"]["duration_seconds"], rows["R1"]["duration_seconds"],
            rows["R2"]["duration_seconds"]) == (42, 90, None)


def test_runs_listing_user_filter_is_a_case_insensitive_substring(client, db):
    """A fragment from the middle of the address, in the other case. (SQLite's
    LIKE is itself case-insensitive for ASCII, so this cannot tell ilike from
    like; it does pin the substring match.)"""
    _seed_owned_runs(db)

    body = _admin_get(client, "/api/admin/runs?user=LICE@EXAMPLE").json()

    assert [r["run_id"] for r in body["runs"]] == ["R3", "R1"]
    assert body["total"] == 2


def test_runs_listing_paginates_with_total_and_has_more(client, db):
    _seed_owned_runs(db)

    first = _admin_get(client, "/api/admin/runs?limit=2").json()
    to_the_end = _admin_get(client, "/api/admin/runs?offset=1&limit=2").json()
    last = _admin_get(client, "/api/admin/runs?offset=2&limit=2").json()

    assert ([r["run_id"] for r in first["runs"]], first["total"], first["has_more"]) == (["R3", "R2"], 3, True)
    assert (first["offset"], first["limit"]) == (0, 2)
    assert ([r["run_id"] for r in to_the_end["runs"]], to_the_end["has_more"]) == (["R2", "R1"], False)
    assert ([r["run_id"] for r in last["runs"]], last["has_more"]) == (["R1"], False)
