"""Unit tests for run duration resolution (_run_duration_seconds).

The runs table now persists total_duration_seconds; the API helper must prefer
that authoritative value, fall back to wall-clock for older runs, report live
elapsed while running, and report nothing for a not-yet-run row.
"""
import os
from datetime import datetime, timedelta
from types import SimpleNamespace

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

from app.api.runs import _run_duration_seconds


def _run(**kw):
    base = dict(
        total_duration_seconds=None,
        started_at=None,
        completed_at=None,
        status="created",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_prefers_persisted_duration_over_wallclock():
    started = datetime(2026, 6, 4, 12, 0, 0)
    run = _run(
        total_duration_seconds=42,
        started_at=started,
        completed_at=started + timedelta(seconds=300),  # wall-clock 300, persisted 42
        status="complete",
    )
    # Persisted value wins (e.g. a retried run whose wall-clock spans idle time).
    assert _run_duration_seconds(run) == 42


def test_falls_back_to_wallclock_when_not_persisted():
    started = datetime(2026, 6, 4, 12, 0, 0)
    run = _run(
        started_at=started,
        completed_at=started + timedelta(seconds=90),
        status="complete",
    )
    assert _run_duration_seconds(run) == 90


def test_running_reports_live_elapsed():
    run = _run(started_at=datetime.now() - timedelta(seconds=10), status="running")
    elapsed = _run_duration_seconds(run)
    assert elapsed is not None and elapsed >= 9


def test_running_never_negative_on_clock_skew():
    run = _run(started_at=datetime.now() + timedelta(seconds=30), status="running")
    assert _run_duration_seconds(run) == 0


def test_not_started_returns_none():
    run = _run(started_at=datetime.now(), status="created")  # no completed_at, not running
    assert _run_duration_seconds(run) is None


# --- admin /stats duration aggregates (folded in, no separate endpoint) --------------

def test_admin_stats_includes_duration_aggregates(client, db):
    """avg/p95 conversion time ride along on /admin/stats: persisted duration
    preferred, wall-clock fallback for older rows, non-complete runs excluded."""
    from app.auth import require_admin
    from app.main import app
    from app.models import Run

    base = datetime(2026, 6, 4, 12, 0, 0)
    db.add_all([
        Run(id="DUR001", filename="a.docx", file_type="docx", status="complete",
            started_at=base, completed_at=base + timedelta(seconds=999), total_duration_seconds=100),
        Run(id="DUR002", filename="b.docx", file_type="docx", status="complete",
            started_at=base, completed_at=base + timedelta(seconds=999), total_duration_seconds=200),
        Run(id="DUR003", filename="c.docx", file_type="docx", status="complete",
            started_at=base, completed_at=base + timedelta(seconds=999), total_duration_seconds=300),
        # Old completed run, no persisted duration -> wall-clock fallback of 50s.
        Run(id="DUR004", filename="d.docx", file_type="docx", status="complete",
            started_at=base, completed_at=base + timedelta(seconds=50)),
        # Excluded: not complete.
        Run(id="DUR005", filename="e.docx", file_type="docx", status="running",
            started_at=base),
    ])
    db.commit()

    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(role="admin")
    try:
        resp = client.get("/api/admin/stats")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert resp.status_code == 200
    data = resp.json()
    # Sorted durations over the 4 completed runs: [50, 100, 200, 300]
    assert data["avg_duration_seconds"] == 162.5
    assert data["p95_duration_seconds"] == 300   # nearest-rank: round(0.95*3)=3 -> 300


def test_admin_stats_duration_aggregates_null_when_no_completed_runs(client, db):
    """No completed runs -> aggregates are null, not a 500."""
    from app.auth import require_admin
    from app.main import app

    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(role="admin")
    try:
        resp = client.get("/api/admin/stats")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["avg_duration_seconds"] is None
    assert data["p95_duration_seconds"] is None


def test_admin_stats_step_avg_seconds_completed_runs_only_in_pipeline_order(client, db):
    """step_avg_seconds averages Step.duration_seconds per stage over completed
    runs only, ordered by step_number; failed runs and null durations are excluded."""
    from app.auth import require_admin
    from app.main import app
    from app.models import Run, Step

    base = datetime(2026, 6, 4, 12, 0, 0)
    db.add_all([
        Run(id="STP001", filename="a.docx", file_type="docx", status="complete", started_at=base),
        Run(id="STP002", filename="b.docx", file_type="docx", status="complete", started_at=base),
        Run(id="STP003", filename="c.docx", file_type="docx", status="failed", started_at=base),
    ])
    db.flush()
    db.add_all([
        # Inserted out of pipeline order on purpose.
        Step(run_id="STP001", step_number=2, stage_id="2", step_name="Entry Extraction",
             status="complete", duration_seconds=40),
        Step(run_id="STP001", step_number=1, stage_id="1a", step_name="Hierarchy Extraction",
             status="complete", duration_seconds=10),
        Step(run_id="STP002", step_number=1, stage_id="1a", step_name="Hierarchy Extraction",
             status="complete", duration_seconds=20),
        Step(run_id="STP002", step_number=2, stage_id="2", step_name="Entry Extraction",
             status="complete", duration_seconds=61),
        # A stage id that sorts before "1a" as a string but runs last: the
        # result must follow step_number, not stage_id.
        Step(run_id="STP001", step_number=13, stage_id="10", step_name="Synthetic Late Stage",
             status="complete", duration_seconds=7),
        # Null duration on a completed run: ignored.
        Step(run_id="STP002", step_number=3, stage_id="3b", step_name="Entry Classification",
             status="complete", duration_seconds=None),
        # Failed run: excluded even though it has durations.
        Step(run_id="STP003", step_number=1, stage_id="1a", step_name="Hierarchy Extraction",
             status="complete", duration_seconds=1000),
        Step(run_id="STP003", step_number=5, stage_id="5", step_name="PubMed Enrichment",
             status="error", duration_seconds=500),
    ])
    db.commit()

    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(role="admin")
    try:
        resp = client.get("/api/admin/stats")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert resp.status_code == 200
    assert resp.json()["step_avg_seconds"] == [
        {"stage_id": "1a", "step_name": "Hierarchy Extraction", "avg_seconds": 15.0},
        {"stage_id": "2", "step_name": "Entry Extraction", "avg_seconds": 50.5},
        {"stage_id": "10", "step_name": "Synthetic Late Stage", "avg_seconds": 7.0},
    ]


def test_admin_stats_step_avg_seconds_empty_without_completed_runs(client, db):
    """No completed runs -> step_avg_seconds is an empty list, not null or a 500."""
    from app.auth import require_admin
    from app.main import app

    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(role="admin")
    try:
        resp = client.get("/api/admin/stats")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert resp.status_code == 200
    assert resp.json()["step_avg_seconds"] == []


def _admin_stats(client):
    from app.auth import require_admin
    from app.main import app

    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(role="admin")
    try:
        return client.get("/api/admin/stats").json()
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_admin_stats_totals_active_users_and_feedback_rate(client, db):
    """active_users: distinct submitters with a run started in the last 30 days.
    feedback_rate: distinct runs with any feedback (whatever their status) per
    completed run, as a percentage."""
    from app.models import Feedback, Run, User

    users = [User(email=f"u{i}@example.com", display_name=f"U{i}", role="user") for i in range(3)]
    db.add_all(users)
    db.flush()
    now = datetime.now()
    db.add_all([
        Run(id="ST_A", filename="a.docx", file_type="docx", status="complete", user_id=users[0].id,
            started_at=now - timedelta(days=1), total_cost=1.11111),
        Run(id="ST_B", filename="b.docx", file_type="docx", status="complete", user_id=users[1].id,
            started_at=now - timedelta(days=40), total_cost=2.0),
        Run(id="ST_C", filename="c.docx", file_type="docx", status="failed", user_id=users[0].id,
            started_at=now - timedelta(days=2)),
        Run(id="ST_D", filename="d.docx", file_type="docx", status="running",
            started_at=now - timedelta(days=1), total_cost=0.5),
        Run(id="ST_F", filename="f.docx", file_type="docx", status="complete", user_id=users[2].id,
            started_at=now - timedelta(days=3), total_cost=0.0),
    ])
    for run_id, reviewer in (("ST_A", users[0]), ("ST_A", users[1]), ("ST_C", users[0])):
        db.add(Feedback(run_id=run_id, user_id=reviewer.id, reviewer_role="self", overall_usefulness=3,
                        manual_conversion_effort="1 hour", correction_effort="1 hour",
                        biggest_issue="none", likelihood_to_recommend=3))
    db.commit()

    data = _admin_stats(client)

    assert (data["total_runs"], data["active_users"]) == (5, 2)
    assert data["total_cost"] == 3.6111
    assert data["feedback_rate"] == 66.7  # 2 runs with feedback / 3 completed


def test_admin_stats_totals_are_zero_on_an_empty_database(client, db):
    data = _admin_stats(client)

    assert (data["total_runs"], data["active_users"], data["total_cost"], data["feedback_rate"]) == (0, 0, 0.0, 0.0)


def test_admin_runs_table_prefers_persisted_duration(client, db):
    """The admin submissions table's Duration column uses the persisted value,
    falling back to wall-clock for older rows -- consistent with the run API."""
    from app.auth import require_admin
    from app.main import app
    from app.models import Run

    base = datetime(2026, 6, 4, 12, 0, 0)
    db.add_all([
        # Persisted duration (120s) must win over wall-clock (999s).
        Run(id="ADR001", filename="a.docx", file_type="docx", status="complete",
            started_at=base, completed_at=base + timedelta(seconds=999), total_duration_seconds=120),
        # Older completed run, no persisted value -> wall-clock 45s.
        Run(id="ADR002", filename="b.docx", file_type="docx", status="complete",
            started_at=base, completed_at=base + timedelta(seconds=45)),
        # In-flight run -> no duration shown.
        Run(id="ADR003", filename="c.docx", file_type="docx", status="running", started_at=base),
    ])
    db.commit()

    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(role="admin")
    try:
        resp = client.get("/api/admin/runs")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert resp.status_code == 200
    by_id = {r["run_id"]: r["duration_seconds"] for r in resp.json()["runs"]}
    assert by_id["ADR001"] == 120   # persisted preferred over wall-clock
    assert by_id["ADR002"] == 45    # wall-clock fallback
    assert by_id["ADR003"] is None  # in-flight, no completed_at
