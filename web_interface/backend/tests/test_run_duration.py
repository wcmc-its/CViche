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


# --- admin duration-metrics endpoint -------------------------------------------------

def test_duration_metrics_endpoint(client, db):
    """Aggregates over completed runs: persisted duration preferred, wall-clock
    fallback for older rows, non-complete runs excluded, percentiles correct."""
    from app.main import app
    from app.auth import require_admin
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
        resp = client.get("/api/admin/metrics/duration")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert resp.status_code == 200
    data = resp.json()
    # Sorted durations: [50, 100, 200, 300]
    assert data["count"] == 4
    assert data["min_seconds"] == 50
    assert data["max_seconds"] == 300
    assert data["avg_seconds"] == 162.5
    assert data["p50_seconds"] == 200   # nearest-rank: round(0.50*3)=2 -> 200
    assert data["p95_seconds"] == 300   # nearest-rank: round(0.95*3)=3 -> 300


def test_duration_metrics_endpoint_empty(client, db):
    """No completed runs -> count 0 and null stats, not a 500."""
    from app.main import app
    from app.auth import require_admin

    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(role="admin")
    try:
        resp = client.get("/api/admin/metrics/duration")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["avg_seconds"] is None
