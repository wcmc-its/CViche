"""Server-side session revocation via a global epoch (#110/#127).

Sessions are stateless signed cookies, so the SystemConfig session_epoch is the
only way to invalidate them before their TTL. Each cookie is stamped with the
epoch in force at mint time; bumping the epoch (admin "sign out everyone")
rejects every older cookie on its next request -- in both get_current_user and
the websocket auth.
"""
import json
import os
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")


def _make_user(db, email="user@example.com", role="user"):
    from app.models import User
    user = User(email=email, display_name="User", role=role, status="active")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _set_epoch(db, n):
    from app.models import SystemConfig
    row = db.query(SystemConfig).filter(SystemConfig.key == "session_epoch").first()
    if row:
        row.value = json.dumps(n)
    else:
        db.add(SystemConfig(key="session_epoch", value=json.dumps(n)))
    db.commit()


def _auth(client, cookie_value):
    from app.auth import COOKIE_NAME
    client.cookies.set(COOKIE_NAME, cookie_value)


# ---------------------------------------------------------------------------
# Cookie payload
# ---------------------------------------------------------------------------

def test_cookie_carries_current_epoch(db):
    """create_session_cookie reads the epoch itself rather than taking it as an
    argument: an omitted argument used to default to 0, which after a "sign out
    everyone" minted a cookie that was dead on arrival (#657 review, thread 12)."""
    from app.auth import create_session_cookie, decode_session_cookie
    from app.models import User
    user = User(id=1, email="u@example.com", role="user")
    _set_epoch(db, 3)
    token = create_session_cookie(user, db)
    assert decode_session_cookie(token)["epoch"] == 3


# ---------------------------------------------------------------------------
# get_current_user revocation gate
# ---------------------------------------------------------------------------

def test_current_epoch_cookie_accepted(client, db, seed_simple_mode):
    from app.auth import create_session_cookie
    user = _make_user(db)
    _set_epoch(db, 0)
    _auth(client, create_session_cookie(user, db))
    assert client.get("/api/auth/me").status_code == 200


def test_stale_epoch_cookie_rejected(client, db, seed_simple_mode):
    from app.auth import create_session_cookie
    user = _make_user(db)
    _set_epoch(db, 0)
    _auth(client, create_session_cookie(user, db))
    assert client.get("/api/auth/me").status_code == 200  # valid before the bump

    _set_epoch(db, 1)  # an admin revoked everyone
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "auth_required"


def test_cookie_without_epoch_is_rejected(client, db, seed_simple_mode):
    """A rich cookie with no 'epoch' key no longer reads as epoch 0.

    It used to, so that the #110 rollout itself would not force a re-login.
    Defaulting a missing security field is the same class of bug thread 9
    reported against get_session_epoch: it silently hands a truncated or
    hand-built cookie whatever the current epoch happens to be. Every cookie
    minted since #110 carries the field, and the ones that did not have long
    since passed their absolute TTL.
    """
    from app.auth import _serializer
    user = _make_user(db)
    _set_epoch(db, 0)
    legacy = _serializer.dumps({
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        "issued_at": int(time.time()),
    })  # note: no "epoch" key
    _auth(client, legacy)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "session_invalid"


# ---------------------------------------------------------------------------
# Admin "sign out everyone"
# ---------------------------------------------------------------------------

def _as_admin(client, fn):
    from app.main import app
    from app.auth import require_admin
    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
        role="admin", email="admin@example.com", id=999)
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(require_admin, None)


def _as_user(client, fn):
    from app.main import app
    from app.auth import get_current_user
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        role="user", email="u@example.com", id=1)
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_admin_revoke_all_bumps_epoch_and_kills_sessions(client, db, seed_simple_mode):
    from app.auth import create_session_cookie
    from app.models import SystemConfig
    user = _make_user(db)
    _set_epoch(db, 0)
    cookie = create_session_cookie(user, db)

    resp = _as_admin(client, lambda: client.post("/api/admin/sessions/revoke-all"))
    assert resp.status_code == 200
    assert resp.json()["session_epoch"] == 1
    row = db.query(SystemConfig).filter(SystemConfig.key == "session_epoch").first()
    assert json.loads(row.value) == 1

    # The previously-valid cookie is now dead.
    _auth(client, cookie)
    assert client.get("/api/auth/me").status_code == 401


def test_non_admin_cannot_revoke_all(client, db, seed_simple_mode):
    resp = _as_user(client, lambda: client.post("/api/admin/sessions/revoke-all"))
    assert resp.status_code == 403


def test_admin_revoke_all_body_audit_line_and_updated_by(client, db, seed_simple_mode, caplog):
    import logging
    from app.models import SystemConfig
    _set_epoch(db, 4)

    with caplog.at_level(logging.INFO):
        resp = _as_admin(client, lambda: client.post("/api/admin/sessions/revoke-all"))

    assert resp.json() == {"message": "All sessions revoked. Everyone must log in again.",
                           "session_epoch": 5}
    audit = [r.getMessage() for r in caplog.records if r.getMessage().startswith("admin_sessions_revoked_all")]
    assert audit == ["admin_sessions_revoked_all: admin=admin@example.com new_epoch=5"]
    db.rollback()  # only a committed bump survives
    row = db.query(SystemConfig).filter(SystemConfig.key == "session_epoch").first()
    assert (json.loads(row.value), row.updated_by) == (5, 999)


def test_admin_revoke_all_with_no_epoch_row_starts_at_one(client, db, seed_simple_mode):
    """A missing row is the one benign case (never revoked): epoch 0 + 1, and
    the row is written so the next read is well-formed."""
    from app.models import SystemConfig
    db.query(SystemConfig).filter(SystemConfig.key == "session_epoch").delete()
    db.commit()

    resp = _as_admin(client, lambda: client.post("/api/admin/sessions/revoke-all"))

    assert resp.json()["session_epoch"] == 1
    db.expire_all()
    row = db.query(SystemConfig).filter(SystemConfig.key == "session_epoch").first()
    assert (json.loads(row.value), row.updated_by) == (1, 999)


@pytest.mark.parametrize("raw", ["not json", "true", '"3"', "1.5", "null"])
def test_admin_revoke_all_refuses_an_unreadable_epoch_rather_than_resetting_it(
        client, db, seed_simple_mode, raw):
    """An unparseable or non-integer epoch is a misconfiguration: 500, and the
    row is left alone instead of being reset to 1 (which would un-revoke every
    session minted since)."""
    from app.models import SystemConfig
    row = db.query(SystemConfig).filter(SystemConfig.key == "session_epoch").first()
    row.value = raw
    db.commit()

    resp = _as_admin(client, lambda: client.post("/api/admin/sessions/revoke-all"))

    assert resp.status_code == 500
    db.rollback()
    db.expire_all()
    assert db.query(SystemConfig).filter(SystemConfig.key == "session_epoch").first().value == raw
