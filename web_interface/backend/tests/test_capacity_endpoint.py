"""Read-only run-admission capacity probe for the upload UI (issue #177).

The upload flow calls GET /api/capacity before spending an upload so a busy pod
stops minting `created` runs that can't start. The probe is ADVISORY and must be
READ-ONLY: it must not acquire a concurrency slot, because the authoritative gate
is still the slot acquisition in start_run (admission is per-pod and racy). These
tests pin that contract.
"""
from unittest.mock import patch

from app.models import User


def _make_user(db):
    user = User(
        email="test@example.com",
        display_name="Test User",
        role="user",
        consent_version="1.0",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _auth(client, user):
    from app.auth import create_session_cookie, COOKIE_NAME
    client.cookies.set(COOKIE_NAME, create_session_cookie(user))


def test_capacity_available_when_below_cap(client, db, seed_simple_mode):
    _auth(client, _make_user(db))
    with patch("app.pipeline.concurrency.active_count", return_value=0), \
         patch("app.pipeline.concurrency.get_max_concurrent_runs", return_value=2):
        resp = client.get("/api/capacity")
    assert resp.status_code == 200
    assert resp.json() == {"available": True, "active": 0, "limit": 2}


def test_capacity_unavailable_when_at_cap(client, db, seed_simple_mode):
    _auth(client, _make_user(db))
    with patch("app.pipeline.concurrency.active_count", return_value=2), \
         patch("app.pipeline.concurrency.get_max_concurrent_runs", return_value=2):
        resp = client.get("/api/capacity")
    assert resp.status_code == 200
    body = resp.json()
    assert body["available"] is False
    assert body["active"] == 2 and body["limit"] == 2


def test_capacity_is_read_only(client, db, seed_simple_mode):
    """Hitting the probe must never reserve a slot."""
    from app.pipeline import concurrency

    _auth(client, _make_user(db))
    before = concurrency.active_count()
    resp = client.get("/api/capacity")
    assert resp.status_code == 200
    assert concurrency.active_count() == before


def test_capacity_requires_auth(client):
    resp = client.get("/api/capacity")
    assert resp.status_code == 401
