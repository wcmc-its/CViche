"""Opaque session tokens resolved server-side (#368, reworked for PR #657 review).

Threat model: this is not a tamper fix (the itsdangerous signature already
prevents forging arbitrary payload fields without CVICHE_SESSION_SECRET). The
value is blast-radius reduction -- if the secret ever leaks, an attacker who has
only the secret can otherwise mint a cookie for any user_id/email/role from
nothing, no DB or store touch required. Moving identity resolution server-side
means a leaked signing secret alone is no longer sufficient; the attacker also
needs a live, store-registered sid.

Contract exercised here, after the review response:
  - The security model is stamped on the cookie, not inferred from whether the
    store happens to answer. Store enabled -> a thin ``{"v": 2, "sid"}`` cookie
    and NOTHING else is accepted. Store disabled -> a rich ``{"v": 1, ...}``
    (or pre-versioning) cookie, and a v2 cookie is rejected.
  - When the store is enabled it is authoritative and there is no fallback:
    a deleted, unknown or malformed record is a 401, and an unreachable store
    is a 503, never an authentication off client-supplied fields.
  - get_session_epoch never defaults; an unreadable epoch is a 503.
  - create_session_cookie reads the epoch itself and refuses to mint a cookie
    it could not register server-side.

The store's own fail-closed contract is pinned one level down, in
test_session_idle.py ("store outages fail CLOSED"): start(), resolve(), end()
and touch() each raise SessionStoreUnavailable when the Redis client raises.
The tests here drive the same failures through the auth dependency and HTTP.
"""
import json
import logging
import time
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
import redis.exceptions

import app.session_idle as session_idle
from app.auth import SessionEpochUnreadable, get_session_epoch
from app.session_idle import IdleSessionStore

_KEY = "cviche:session:idle:{sid}"


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


@pytest.fixture
def broken_store(monkeypatch):
    """An ENABLED store whose client raises a Redis transport error.

    Returns the store; the test arms whichever operation it wants to fail, e.g.
    ``broken_store._client.get.side_effect = redis.exceptions.ConnectionError()``.
    """
    store = IdleSessionStore("redis://fake", 1200)
    store._client = MagicMock()
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


def _set_epoch(db, value):
    """Write session_epoch verbatim (JSON-encoded)."""
    from app.models import SystemConfig
    row = db.query(SystemConfig).filter(SystemConfig.key == "session_epoch").first()
    encoded = json.dumps(value)
    if row:
        row.value = encoded
    else:
        db.add(SystemConfig(key="session_epoch", value=encoded))
    db.commit()


def _drop_epoch(db):
    from app.models import SystemConfig
    db.query(SystemConfig).filter(SystemConfig.key == "session_epoch").delete()
    db.commit()


def _events_named(caplog, name):
    return [r for r in caplog.records if r.getMessage() == name]


# ---------------------------------------------------------------------------
# Cookie shape: what each deployment mints
# ---------------------------------------------------------------------------

def test_store_enabled_mints_versioned_thin_payload(db, idle_store):
    from app.auth import create_session_cookie, decode_session_cookie
    user = _make_user(db)
    _set_epoch(db, 0)

    payload = decode_session_cookie(create_session_cookie(user, db))

    # Exactly two keys -- no identity of any kind rides along.
    assert payload["v"] == 2
    assert isinstance(payload["sid"], str) and payload["sid"]
    assert set(payload) == {"v", "sid"}


def test_store_disabled_mints_versioned_rich_payload(db):
    from app.auth import create_session_cookie, decode_session_cookie
    user = _make_user(db)
    _set_epoch(db, 0)

    payload = decode_session_cookie(create_session_cookie(user, db))

    assert payload["v"] == 1
    assert payload["user_id"] == user.id
    assert payload["email"] == user.email
    assert payload["role"] == user.role


def test_create_session_cookie_stamps_the_current_epoch(db, idle_store):
    """The epoch is read at mint time, not passed in (thread 12)."""
    from app.auth import create_session_cookie, decode_session_cookie
    user = _make_user(db)
    _set_epoch(db, 3)

    sid = decode_session_cookie(create_session_cookie(user, db))["sid"]
    record = idle_store.resolve(sid)
    assert record.epoch == 3
    assert record.user_id == user.id


# ---------------------------------------------------------------------------
# The store is authoritative: identity NEVER comes from the payload (threads
# 8, 11, 13, 20.1)
# ---------------------------------------------------------------------------

def test_store_enabled_resolves_identity_from_store(client, db, seed_simple_mode, idle_store):
    from app.auth import create_session_cookie, COOKIE_NAME
    user = _make_user(db)

    client.cookies.set(COOKIE_NAME, create_session_cookie(user, db))

    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["user_id"] == user.id
    assert resp.json()["email"] == user.email


def test_embedded_identity_fields_are_ignored_when_the_store_is_enabled(
    client, db, seed_simple_mode, idle_store
):
    """A v2 cookie carrying conflicting user_id/role/epoch fields resolves to
    the identity registered for its sid, not to the fields.

    This is the central security property of #368: it guards against a future
    change quietly reintroducing trust in client-controlled identity.
    """
    from app.auth import _serializer, COOKIE_NAME, create_session_cookie, decode_session_cookie
    real = _make_user(db, email="real@example.com", role="user")
    other = _make_user(db, email="other@example.com", role="admin")

    sid = decode_session_cookie(create_session_cookie(real, db))["sid"]
    forged = _serializer.dumps({
        "v": 2,
        "sid": sid,
        "user_id": other.id,
        "email": other.email,
        "role": "admin",
        "epoch": 0,
    })
    client.cookies.set(COOKIE_NAME, forged)

    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["user_id"] == real.id
    assert resp.json()["email"] == real.email
    assert resp.json()["role"] == "user"


def test_unversioned_rich_cookie_is_rejected_when_the_store_is_enabled(
    client, db, seed_simple_mode, idle_store
):
    """REPLACES the old "falls back and still authenticates" test.

    A store-enabled deployment never trusts payload identity -- that fallback
    was the hole: a deleted session, an unknown sid, or a store outage all
    ended up authenticating off the cookie's own fields. The cost is a one-time
    re-login for sessions minted before this change on a store-enabled
    deployment; production has no CVICHE_REDIS_URL, so production is unaffected.
    """
    from app.auth import _serializer, COOKIE_NAME
    user = _make_user(db)
    sid = "pre-368-sid"
    # A pre-#368 session: the key exists holding the old bare marker, and the
    # cookie carries its own identity. Both used to be enough. Neither is now.
    idle_store._client.set(_KEY.format(sid=sid), "1", ex=1200)

    legacy = _serializer.dumps({
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        "epoch": 0,
        "issued_at": int(time.time()),
        "sid": sid,
    })
    client.cookies.set(COOKIE_NAME, legacy)

    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "session_invalid"


def test_thin_cookie_with_no_store_record_is_401_not_500(client, db, seed_simple_mode, idle_store):
    from app.auth import _serializer, COOKIE_NAME
    _make_user(db)

    client.cookies.set(COOKIE_NAME, _serializer.dumps({"v": 2, "sid": "orphan-sid"}))

    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "session_invalid"


def test_a_revoked_session_cannot_be_revived_by_embedded_fields(
    client, db, seed_simple_mode, idle_store
):
    """Thread 13's headline case: the server explicitly deletes a session, and
    the very same cookie -- which also happens to carry user_id/epoch/role --
    is presented again. Under the old fallback it authenticated, because a
    deleted record and an unknown one both looked like "resolve returned None".
    """
    from app.auth import _serializer, COOKIE_NAME, create_session_cookie, decode_session_cookie
    user = _make_user(db)
    sid = decode_session_cookie(create_session_cookie(user, db))["sid"]
    idle_store.end(sid)                       # explicit server-side revocation

    client.cookies.set(COOKIE_NAME, _serializer.dumps({
        "v": 2,
        "sid": sid,
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        "epoch": 0,
    }))

    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "session_invalid"


@pytest.mark.parametrize("stored", [
    "1",                                 # pre-#368 bare marker
    "not json",
    "[]",
    '{"user_id": "abc", "epoch": 0}',
    '{"user_id": null, "epoch": 0}',
    '{"epoch": 1}',
    '{"user_id": 1}',
    '{"user_id": 1, "epoch": "x"}',
])
def test_malformed_store_record_is_401_not_500(client, db, seed_simple_mode, idle_store, stored):
    """A hand-edited or half-written record is a controlled authentication
    failure, never an unhandled int()/subscript error (threads 19.3, 20.2)."""
    from app.auth import _serializer, COOKIE_NAME
    _make_user(db)
    sid = "malformed-sid"
    idle_store._client.set(_KEY.format(sid=sid), stored, ex=1200)

    client.cookies.set(COOKIE_NAME, _serializer.dumps({"v": 2, "sid": sid}))

    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "session_invalid"


# ---------------------------------------------------------------------------
# Cookie version is honoured in both directions (thread 11)
# ---------------------------------------------------------------------------

def test_rich_v1_cookie_is_accepted_when_the_store_is_disabled(client, db, seed_simple_mode):
    from app.auth import create_session_cookie, COOKIE_NAME
    user = _make_user(db)
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, db))

    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["email"] == user.email


def test_pre_versioning_rich_cookie_is_accepted_when_the_store_is_disabled(
    client, db, seed_simple_mode
):
    """Production's live sessions carry no "v" at all; they must keep working."""
    from app.auth import _serializer, COOKIE_NAME
    user = _make_user(db)
    client.cookies.set(COOKIE_NAME, _serializer.dumps({
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        "epoch": 0,
        "issued_at": int(time.time()),
    }))

    assert client.get("/api/auth/me").status_code == 200


def test_thin_v2_cookie_is_rejected_when_the_store_is_disabled(client, db, seed_simple_mode):
    """A thin cookie has no identity without a store to look it up in."""
    from app.auth import _serializer, COOKIE_NAME
    _make_user(db)
    client.cookies.set(COOKIE_NAME, _serializer.dumps({"v": 2, "sid": "some-sid"}))

    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "session_invalid"


# ---------------------------------------------------------------------------
# Epoch revocation, on the store path (threads 9, 12)
# ---------------------------------------------------------------------------

def test_epoch_revocation_on_the_store_path(client, db, seed_simple_mode, idle_store, caplog):
    from app.auth import create_session_cookie, COOKIE_NAME
    user = _make_user(db)
    _set_epoch(db, 0)

    client.cookies.set(COOKIE_NAME, create_session_cookie(user, db))
    assert client.get("/api/auth/me").status_code == 200

    _set_epoch(db, 1)  # admin "sign out everyone"
    with caplog.at_level(logging.INFO, logger="app.auth"):
        resp = client.get("/api/auth/me")

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "auth_required"
    events = _events_named(caplog, "SESSION_REVOKED")
    assert len(events) == 1
    assert events[0].reason == "epoch_mismatch"
    assert events[0].user_id == user.id


@pytest.mark.parametrize("bad_value", ["abc", True, 1.5, None])
def test_get_session_epoch_raises_on_a_corrupt_row(db, bad_value):
    _set_epoch(db, bad_value)
    with pytest.raises(SessionEpochUnreadable):
        get_session_epoch(db)


def test_get_session_epoch_raises_on_a_missing_row(db):
    """The seeder always writes session_epoch, so a missing row is a
    misconfiguration -- never a silent epoch 0 (thread 9)."""
    _drop_epoch(db)
    with pytest.raises(SessionEpochUnreadable):
        get_session_epoch(db)


def test_get_session_epoch_raises_on_a_non_json_row(db):
    from app.models import SystemConfig
    row = db.query(SystemConfig).filter(SystemConfig.key == "session_epoch").first()
    if row is None:
        db.add(SystemConfig(key="session_epoch", value="}not json{"))
    else:
        row.value = "}not json{"
    db.commit()
    with pytest.raises(SessionEpochUnreadable):
        get_session_epoch(db)


def test_unreadable_epoch_is_503_not_a_silent_epoch_zero(client, db, seed_simple_mode):
    from app.auth import create_session_cookie, COOKIE_NAME
    user = _make_user(db)
    _set_epoch(db, 0)
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, db))
    assert client.get("/api/auth/me").status_code == 200

    _set_epoch(db, "abc")
    resp = client.get("/api/auth/me")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "session_state_unavailable"


# ---------------------------------------------------------------------------
# Store outages fail closed with 503, and grant no identity (threads 10, 13,
# 14, 20.4)
# ---------------------------------------------------------------------------

def test_store_outage_during_resolve_is_503(client, db, seed_simple_mode, broken_store, caplog):
    from app.auth import _serializer, COOKIE_NAME
    _make_user(db)
    broken_store._client.get.side_effect = redis.exceptions.ConnectionError("valkey down")

    client.cookies.set(COOKIE_NAME, _serializer.dumps({"v": 2, "sid": "any-sid"}))
    with caplog.at_level(logging.INFO, logger="app.auth"):
        resp = client.get("/api/auth/me")

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "session_store_unavailable"
    events = _events_named(caplog, "SESSION_STORE_UNAVAILABLE")
    assert len(events) == 1
    assert events[0].reason == "resolve"


def test_store_outage_during_resolve_grants_no_identity_from_the_cookie(
    client, db, seed_simple_mode, broken_store
):
    """The rich-cookie fallback is what made an outage indistinguishable from
    "session not found". A cookie with perfectly good embedded fields must NOT
    authenticate while the store is down."""
    from app.auth import _serializer, COOKIE_NAME
    user = _make_user(db)
    broken_store._client.get.side_effect = redis.exceptions.ConnectionError("valkey down")

    client.cookies.set(COOKIE_NAME, _serializer.dumps({
        "v": 2,
        "sid": "any-sid",
        "user_id": user.id,
        "email": user.email,
        "role": "admin",
        "epoch": 0,
    }))

    resp = client.get("/api/auth/me")
    assert resp.status_code == 503
    assert "user_id" not in resp.json()


def test_store_outage_during_touch_is_503(client, db, seed_simple_mode, broken_store, caplog):
    """resolve() answers, then expire() fails: the idle refresh must not be
    read as "still active" (thread 14)."""
    from app.auth import _serializer, COOKIE_NAME
    user = _make_user(db)
    broken_store._client.get.return_value = json.dumps(
        {"user_id": user.id, "epoch": 0, "issued_at": int(time.time())}
    )
    broken_store._client.expire.side_effect = redis.exceptions.ConnectionError("valkey down")

    client.cookies.set(COOKIE_NAME, _serializer.dumps({"v": 2, "sid": "any-sid"}))
    with caplog.at_level(logging.INFO, logger="app.auth"):
        resp = client.get("/api/auth/me")

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "session_store_unavailable"
    events = _events_named(caplog, "SESSION_STORE_UNAVAILABLE")
    assert len(events) == 1
    assert events[0].reason == "touch"


# ---------------------------------------------------------------------------
# Logout is per-session revocation (threads 20.3, 20.5)
# ---------------------------------------------------------------------------

def test_replaying_the_cookie_after_logout_is_rejected(client, db, seed_simple_mode, idle_store):
    from app.auth import create_session_cookie, COOKIE_NAME
    user = _make_user(db)
    cookie = create_session_cookie(user, db)

    client.cookies.set(COOKIE_NAME, cookie)
    assert client.get("/api/auth/me").status_code == 200
    assert client.post("/api/auth/logout").status_code == 200

    # Replay the original cookie value, not whatever the logout response left.
    client.cookies.set(COOKIE_NAME, cookie)
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "session_invalid"


def test_logging_out_one_session_leaves_the_other_alive(client, db, seed_simple_mode, idle_store):
    """sid is the unit of revocation: two devices, one logout."""
    from app.auth import create_session_cookie, COOKIE_NAME
    user = _make_user(db)
    cookie_a = create_session_cookie(user, db)
    cookie_b = create_session_cookie(user, db)
    assert cookie_a != cookie_b

    client.cookies.set(COOKIE_NAME, cookie_a)
    assert client.post("/api/auth/logout").status_code == 200

    client.cookies.set(COOKIE_NAME, cookie_a)
    assert client.get("/api/auth/me").status_code == 401
    client.cookies.set(COOKIE_NAME, cookie_b)
    assert client.get("/api/auth/me").status_code == 200


# ---------------------------------------------------------------------------
# Login and logout report store failures instead of minting or swallowing
# (threads 15, 16, 19.2, 20.6)
# ---------------------------------------------------------------------------

def test_login_refuses_to_mint_when_the_store_write_fails(
    client, db, seed_simple_mode, broken_store, caplog
):
    """A thin cookie whose record was never written is an unusable credential;
    login must fail atomically rather than issue one."""
    broken_store._client.set.side_effect = redis.exceptions.ConnectionError("valkey down")

    with caplog.at_level(logging.INFO, logger="app.api.auth_routes"):
        resp = client.post(
            "/api/auth/login",
            json={"email": "test@example.com", "display_name": "Test"},
        )

    assert resp.status_code == 503
    assert resp.json()["error"] == "session_store_unavailable"
    assert "set-cookie" not in {k.lower() for k in resp.headers}

    failures = _events_named(caplog, "LOGIN_FAILED")
    assert len(failures) == 1
    assert failures[0].reason == "session_store_unavailable"
    assert not _events_named(caplog, "LOGIN_SUCCESS")

    outages = _events_named(caplog, "SESSION_STORE_UNAVAILABLE")
    assert len(outages) == 1
    assert outages[0].reason == "start"


def test_login_refuses_to_mint_when_the_epoch_is_unreadable(client, db, seed_simple_mode):
    _set_epoch(db, "abc")

    resp = client.post(
        "/api/auth/login",
        json={"email": "test@example.com", "display_name": "Test"},
    )

    assert resp.status_code == 503
    assert resp.json()["error"] == "session_state_unavailable"
    assert "set-cookie" not in {k.lower() for k in resp.headers}


def test_logout_reports_a_failed_revocation(client, db, seed_simple_mode, broken_store, caplog):
    """The cookie is cleared either way, but a revocation that did not happen
    is a 503 -- not a "Logged out." the user has no reason to doubt."""
    from app.auth import _serializer, COOKIE_NAME
    user = _make_user(db)
    broken_store._client.get.return_value = json.dumps(
        {"user_id": user.id, "epoch": 0, "issued_at": int(time.time())}
    )
    broken_store._client.delete.side_effect = redis.exceptions.ConnectionError("valkey down")

    client.cookies.set(COOKIE_NAME, _serializer.dumps({"v": 2, "sid": "live-sid"}))
    with caplog.at_level(logging.INFO, logger="app.api.auth_routes"):
        resp = client.post("/api/auth/logout")

    assert resp.status_code == 503
    assert resp.json()["error"] == "session_revocation_failed"
    # The device is still signed out: the delete-cookie header is present.
    assert 'cviche_session=""' in resp.headers["set-cookie"]

    # SESSION_REVOKED must NOT fire -- nothing was revoked.
    assert not _events_named(caplog, "SESSION_REVOKED")
    outages = _events_named(caplog, "SESSION_STORE_UNAVAILABLE")
    assert len(outages) == 1
    assert outages[0].reason == "end"


def test_logout_emits_session_revoked_with_the_resolved_user_id(
    client, db, seed_simple_mode, idle_store, caplog
):
    """A thin cookie carries no user_id; the audit line takes it from the store."""
    from app.auth import create_session_cookie, COOKIE_NAME
    user = _make_user(db)
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, db))

    with caplog.at_level(logging.INFO, logger="app.api.auth_routes"):
        resp = client.post("/api/auth/logout")

    assert resp.status_code == 200
    events = _events_named(caplog, "SESSION_REVOKED")
    assert len(events) == 1
    assert events[0].user_id == user.id
    assert events[0].reason == "user_logout"


# ---------------------------------------------------------------------------
# The SAML arms of the same two store-outage cases (threads 16, 18)
#
# saml_routes is a second, independent login/logout path -- it mints and clears
# the same cookie through its own code, so the simple-auth tests above prove
# nothing about it. Same contract on both: a login that could not register the
# session server-side must not hand out a cookie, and a logout that could not
# revoke server-side must say so while still clearing the device.
# ---------------------------------------------------------------------------

_SAML_IDENTITY = {
    "urn:oid:0.9.2342.19200300.100.1.3": ["samluser@med.cornell.edu"],
    "urn:oid:2.16.840.1.113730.3.1.241": ["SAML User"],
    "urn:oid:1.3.6.1.4.1.5923.1.1.1.6": ["samluser@med.cornell.edu"],
}


def _mock_saml_client(identity_dict):
    """A pysaml2 client stub that "validates" any assertion into identity_dict
    (the same shape tests/test_auth_audit_events.py uses)."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.get_identity.return_value = identity_dict
    mock_response.response.destination = None  # absent Destination is allowed (#672)
    # A real pysaml2 response always carries an assertion ID; give this stub
    # one too so the replay gate's fail-closed default (a missing ID) doesn't
    # fire on tests that aren't exercising that path.
    assertion = MagicMock()
    assertion.id = f"_{uuid4().hex}"
    mock_response.assertions = [assertion]
    mock_response.assertion = assertion
    mock_client.parse_authn_request_response.return_value = mock_response
    return mock_client


@patch("app.api.saml_routes.get_saml_client")
def test_saml_acs_refuses_to_mint_when_the_store_is_unreachable(
    mock_get_client, client, db, seed_saml_mode, broken_store, caplog
):
    """A valid assertion plus an unwritable store is a login that cannot
    succeed: issuing the cookie anyway hands the user a credential no later
    request can resolve. The browser goes back to the login page with a
    truthful error code, and no Set-Cookie is sent."""
    mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
    _set_epoch(db, 0)
    broken_store._client.set.side_effect = redis.exceptions.ConnectionError("valkey down")

    with caplog.at_level(logging.INFO, logger="app.api.saml_routes"):
        resp = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/"},
            follow_redirects=False,
        )

    assert resp.status_code == 302
    assert resp.headers["location"] == "/login?error=session_store_unavailable"
    assert "set-cookie" not in {k.lower() for k in resp.headers}

    failures = _events_named(caplog, "LOGIN_FAILED")
    assert len(failures) == 1
    assert failures[0].reason == "session_store_unavailable"
    # No LOGIN_SUCCESS may fire alongside it -- the login did not happen.
    assert not _events_named(caplog, "LOGIN_SUCCESS")

    outages = _events_named(caplog, "SESSION_STORE_UNAVAILABLE")
    assert len(outages) == 1
    assert outages[0].reason == "start"


def test_saml_logout_reports_a_failed_revocation(
    client, db, seed_saml_mode, broken_store, caplog
):
    """Mirrors the simple-auth logout arm: the device is signed out either way,
    but a revocation that did not happen is reported (via the login page's
    error code) instead of being swallowed -- the session stays replayable
    until its absolute TTL otherwise."""
    from app.auth import _serializer, COOKIE_NAME
    user = _make_user(db, email="samluser@med.cornell.edu")
    broken_store._client.get.return_value = json.dumps(
        {"user_id": user.id, "epoch": 0, "issued_at": int(time.time())}
    )
    broken_store._client.delete.side_effect = redis.exceptions.ConnectionError("valkey down")

    client.cookies.set(COOKIE_NAME, _serializer.dumps({"v": 2, "sid": "live-sid"}))
    with caplog.at_level(logging.INFO, logger="app.api.saml_routes"):
        resp = client.post("/api/saml/logout", follow_redirects=False)

    assert resp.status_code == 302
    assert resp.headers["location"] == "/login?error=session_revocation_failed"
    # The device is still signed out: the delete-cookie header is present.
    assert 'cviche_session=""' in resp.headers["set-cookie"]

    # SESSION_REVOKED must NOT fire -- nothing was revoked.
    assert not _events_named(caplog, "SESSION_REVOKED")
    outages = _events_named(caplog, "SESSION_STORE_UNAVAILABLE")
    assert len(outages) == 1
    assert outages[0].reason == "end"
