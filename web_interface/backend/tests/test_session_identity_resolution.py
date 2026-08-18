"""Opaque session tokens resolved server-side (#368).

Threat model: this is not a tamper fix (the itsdangerous signature already
prevents forging arbitrary payload fields without CVICHE_SESSION_SECRET). The
value is blast-radius reduction -- if the secret ever leaks, an attacker who
has only the secret can currently mint a cookie for any user_id/email/role
from nothing, no DB or store touch required. Moving identity resolution
server-side (via the idle store, when enabled) means a leaked signing secret
alone is no longer sufficient; the attacker also needs a live,
store-registered sid.

Contract exercised here:
  - Store enabled: create_session_cookie mints a THIN {sid}-only payload;
    identity for that session lives in Valkey, not the cookie.
  - Store disabled (no CVICHE_REDIS_URL): create_session_cookie keeps minting
    today's rich, self-contained payload -- no behavior change.
  - get_current_user resolves identity via the store when it has a record for
    the sid, and falls back to the payload's own embedded fields otherwise
    (store disabled/unreachable, or a legacy rich cookie whose sid was never
    registered) -- this fallback is what guarantees existing logged-in users
    are NOT forced to re-login on deploy.
  - The epoch revocation gate (#110/#127) is enforced identically on both the
    resolved-identity and the fallback path.
"""
import json
import time

import pytest

import app.session_idle as session_idle
from app.session_idle import IdleSessionStore


def _fake_store(ttl=1200):
    fakeredis = pytest.importorskip("fakeredis")
    store = IdleSessionStore("redis://fake", ttl)  # truthy url -> enabled
    store._client = fakeredis.FakeStrictRedis()
    return store


@pytest.fixture
def idle_store(monkeypatch):
    """Install a fake-backed idle store as the process singleton for a test."""
    store = _fake_store()
    monkeypatch.setattr(session_idle, "_store", store)
    return store


def _make_user(db, email="test@example.com", role="user"):
    from app.models import User
    user = User(email=email, display_name="Test User", role=role,
                status="active", consent_version="1.0")
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


# ---------------------------------------------------------------------------
# (a) store enabled -> thin cookie
# ---------------------------------------------------------------------------

def test_store_enabled_mints_thin_payload_no_identity_fields(db, idle_store):
    from app.auth import create_session_cookie, decode_session_cookie
    user = _make_user(db)

    cookie = create_session_cookie(user, epoch=0)
    payload = decode_session_cookie(cookie)

    assert "sid" in payload
    assert "email" not in payload
    assert "role" not in payload
    assert "user_id" not in payload
    assert "epoch" not in payload


# ---------------------------------------------------------------------------
# (b) store enabled + resolve() succeeds -> identity comes from the store
# ---------------------------------------------------------------------------

def test_store_enabled_resolves_identity_from_store(client, db, seed_simple_mode, idle_store):
    from app.auth import create_session_cookie, COOKIE_NAME
    user = _make_user(db)

    # The cookie itself carries no user_id/email/role (see test above) -- the
    # only way /api/auth/me can return the right user is via the store.
    cookie = create_session_cookie(user, epoch=0)
    client.cookies.set(COOKIE_NAME, cookie)

    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == user.email
    assert resp.json()["user_id"] == user.id


# ---------------------------------------------------------------------------
# (c) store enabled but resolve() returns None -> fall back to the payload.
# This is the no-forced-logout guarantee: a legacy rich cookie minted by the
# pre-#368 code (whose store record still holds the old bare "1" marker,
# not the new JSON blob) must keep authenticating -- exactly the state every
# session in flight is in at the moment this change deploys. The key still
# exists (so the unchanged idle-touch check still passes); resolve() just
# can't parse "1" as identity and falls back to the cookie's own fields.
# ---------------------------------------------------------------------------

def test_legacy_rich_cookie_with_pre_368_store_value_falls_back_and_still_authenticates(
    client, db, seed_simple_mode, idle_store
):
    from app.auth import _serializer, COOKIE_NAME
    user = _make_user(db)
    sid = "pre-368-sid"

    # Simulate a session started by the pre-#368 code: the store key exists
    # with the old bare marker value, not the new {user_id, epoch, ...} blob.
    idle_store._client.set(f"cviche:session:idle:{sid}", "1", ex=1200)
    assert idle_store.resolve(sid) is None  # old value doesn't parse as identity

    legacy_cookie = _serializer.dumps({
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        "epoch": 0,
        "issued_at": int(time.time()),
        "sid": sid,
    })

    client.cookies.set(COOKIE_NAME, legacy_cookie)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == user.email


# ---------------------------------------------------------------------------
# (d) store disabled -> unchanged rich-cookie behavior
# ---------------------------------------------------------------------------

def test_store_disabled_still_mints_rich_payload_and_authenticates(client, db, seed_simple_mode):
    from app.auth import create_session_cookie, decode_session_cookie, COOKIE_NAME
    user = _make_user(db)

    cookie = create_session_cookie(user, epoch=0)
    payload = decode_session_cookie(cookie)
    assert payload["user_id"] == user.id
    assert payload["email"] == user.email
    assert payload["role"] == user.role

    client.cookies.set(COOKIE_NAME, cookie)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == user.email


# ---------------------------------------------------------------------------
# (e) epoch revocation on both paths
# ---------------------------------------------------------------------------

def test_epoch_revocation_on_resolved_identity_path(client, db, seed_simple_mode, idle_store):
    from app.auth import create_session_cookie, COOKIE_NAME
    user = _make_user(db)
    _set_epoch(db, 0)

    cookie = create_session_cookie(user, epoch=0)
    client.cookies.set(COOKIE_NAME, cookie)
    assert client.get("/api/auth/me").status_code == 200

    _set_epoch(db, 1)  # admin "sign out everyone"
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "auth_required"


def test_epoch_revocation_on_fallback_path(client, db, seed_simple_mode, idle_store):
    from app.auth import _serializer, COOKIE_NAME
    user = _make_user(db)
    _set_epoch(db, 0)
    sid = "pre-368-sid-epoch"

    # Same pre-#368 store state as the backward-compat test above: the key
    # exists with the old marker value, so the fallback path is exercised
    # rather than an outright rejection.
    idle_store._client.set(f"cviche:session:idle:{sid}", "1", ex=1200)

    legacy_cookie = _serializer.dumps({
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        "epoch": 0,
        "issued_at": int(time.time()),
        "sid": sid,
    })
    client.cookies.set(COOKIE_NAME, legacy_cookie)
    assert client.get("/api/auth/me").status_code == 200

    _set_epoch(db, 1)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "auth_required"


# ---------------------------------------------------------------------------
# A thin cookie with no resolvable store record is rejected outright rather
# than trusted -- there is no embedded payload to fall back to, so this is
# never a "fall back to legacy fields" situation. Reported as session_idle:
# the store record a thin cookie depends on being gone is indistinguishable
# from (and functionally equivalent to) an idle timeout -- see
# test_session_idle.py::test_expired_idle_key_yields_401 for the same
# contract from the angle of a key that lapses mid-session.
# ---------------------------------------------------------------------------

def test_thin_cookie_with_no_store_record_is_rejected_not_trusted(client, db, seed_simple_mode, idle_store):
    from app.auth import _serializer, COOKIE_NAME
    _make_user(db)

    # A thin {sid}-only cookie whose sid was never start()-ed -- resolve()
    # returns None and there is no embedded user_id to fall back to.
    thin_orphan = _serializer.dumps({"sid": "orphan-sid"})
    client.cookies.set(COOKIE_NAME, thin_orphan)

    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "session_idle"
