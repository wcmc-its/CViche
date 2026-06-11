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
