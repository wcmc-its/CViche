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

def test_cookie_carries_epoch():
    from app.auth import create_session_cookie, decode_session_cookie
    from app.models import User
    user = User(id=1, email="u@example.com", role="user")
    token = create_session_cookie(user, epoch=3)
    assert decode_session_cookie(token)["epoch"] == 3


# ---------------------------------------------------------------------------
# get_current_user revocation gate
# ---------------------------------------------------------------------------

def test_current_epoch_cookie_accepted(client, db, seed_simple_mode):
    from app.auth import create_session_cookie
    user = _make_user(db)
    _set_epoch(db, 0)
    _auth(client, create_session_cookie(user, epoch=0))
    assert client.get("/api/auth/me").status_code == 200


def test_stale_epoch_cookie_rejected(client, db, seed_simple_mode):
    from app.auth import create_session_cookie
    user = _make_user(db)
    _set_epoch(db, 0)
    _auth(client, create_session_cookie(user, epoch=0))
    assert client.get("/api/auth/me").status_code == 200  # valid before the bump

    _set_epoch(db, 1)  # an admin revoked everyone
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "auth_required"


def test_legacy_cookie_without_epoch_accepted_until_first_bump(client, db, seed_simple_mode):
    """A cookie minted before this feature (no 'epoch' key) is read as epoch 0,
    so the rollout itself does not force a mass re-login -- the first bump does."""
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
    assert client.get("/api/auth/me").status_code == 200

    _set_epoch(db, 1)
    assert client.get("/api/auth/me").status_code == 401


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
    cookie = create_session_cookie(user, epoch=0)

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
