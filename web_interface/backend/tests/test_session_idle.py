"""Server-side idle session enforcement (app/session_idle.py + auth wiring).

Contract:
  - A signed cookie now carries a per-session `sid`.
  - One Valkey key per sid with a sliding TTL; each authenticated request
    refreshes it. A reachable-but-absent key (idle-expired or logged out) is
    rejected with 401 `session_idle`. Logout deletes the key.
  - Cookies without a `sid` (minted before this feature) bypass the check.
  - Disabled (no URL) -> fail open (touch() == True); the store is a no-op.
  - Configured but unreachable Valkey -> FAIL CLOSED: every method raises
    SessionStoreUnavailable (PR #657 review, threads 10/13-18). An outage is
    not evidence that a session is still valid.

The store tests use fakeredis for real TTL/expire semantics; the integration
tests drive the auth dependency through the TestClient with a fake-backed store.
"""
import time
from unittest.mock import MagicMock

import pytest
import redis.exceptions

import app.session_idle as session_idle
from app.session_idle import (
    IdleSessionStore,
    SessionRecord,
    SessionStoreUnavailable,
)

# A key set to expire in 1 ms has certainly lapsed by the time a bounded poll
# of this length gives up -- deterministic where a flat time.sleep(1.1) was
# merely slow and CI-flaky (PR #657 review, thread 20.7).
_EXPIRY_POLL_SECONDS = 0.05


def _unreachable_store(**side_effects):
    """A store whose client raises a Redis transport error for the named ops."""
    store = IdleSessionStore("redis://fake", 1200)
    client = MagicMock()
    for op, exc in side_effects.items():
        getattr(client, op).side_effect = exc
    store._client = client
    return store


def _fake_store(ttl=1200):
    fakeredis = pytest.importorskip("fakeredis")
    store = IdleSessionStore("redis://fake", ttl)  # truthy url -> enabled
    store._client = fakeredis.FakeStrictRedis()
    return store


# --- store unit tests --------------------------------------------------------

def test_disabled_store_is_noop_and_fails_open():
    store = IdleSessionStore("", 1200)
    assert store.enabled is False
    store.start("s1", user_id=1, epoch=0)          # no-op, must not raise
    assert store.touch("s1") is True   # disabled -> always active
    store.end("s1")


def test_start_then_touch_keeps_session_active():
    store = _fake_store()
    store.start("s1", user_id=1, epoch=0)
    assert store.touch("s1") is True


def test_touch_without_start_is_expired():
    store = _fake_store()
    # No start() -> no key -> reachable-but-absent -> rejected.
    assert store.touch("never-seen") is False


def test_touch_after_expiry_is_rejected():
    """A lapsed key reads as expired, not as an outage.

    Driven by a 1 ms pexpire and a bounded poll rather than a flat
    time.sleep(ttl + margin): the assertion is about expire() answering False
    for a gone key, not about wall-clock duration.
    """
    store = _fake_store(ttl=1200)
    store.start("s1", user_id=1, epoch=0)
    key = "cviche:session:idle:s1"
    store._client.pexpire(key, 1)

    # Poll the key directly, never through touch() -- touch() would slide the
    # window back to the full TTL and the key would never lapse.
    deadline = time.monotonic() + _EXPIRY_POLL_SECONDS
    while store._client.exists(key) and time.monotonic() < deadline:
        time.sleep(0.001)
    assert store.touch("s1") is False


def test_touch_slides_the_window():
    store = _fake_store(ttl=100)
    store.start("s1", user_id=1, epoch=0)
    key = "cviche:session:idle:s1"
    store._client.expire(key, 5)          # pretend it's about to lapse
    assert store.touch("s1") is True
    assert store._client.ttl(key) > 5     # refreshed back toward the full TTL


def test_end_deletes_key():
    store = _fake_store()
    store.start("s1", user_id=1, epoch=0)
    store.end("s1")
    assert store.touch("s1") is False


# --- store outages fail CLOSED (PR #657 review, threads 10/13-18) ------------

def test_touch_raises_when_redis_errors():
    """A refused connection is not evidence the idle window is still open."""
    store = _unreachable_store(expire=redis.exceptions.ConnectionError("valkey down"))
    with pytest.raises(SessionStoreUnavailable):
        store.touch("s1")


def test_touch_raises_on_timeout():
    """A hung/partitioned Valkey surfaces as redis TimeoutError once
    socket_timeout is set; it must fail closed exactly like a refused
    connection, not silently extend the session."""
    store = _unreachable_store(expire=redis.exceptions.TimeoutError("valkey hung"))
    with pytest.raises(SessionStoreUnavailable):
        store.touch("s1")


def test_start_raises_when_redis_errors():
    """Login must not mint a thin cookie whose record was never written."""
    store = _unreachable_store(set=redis.exceptions.ConnectionError("valkey down"))
    with pytest.raises(SessionStoreUnavailable):
        store.start("s1", user_id=1, epoch=0)


def test_resolve_raises_when_redis_errors():
    """An outage must be distinguishable from "no such session"."""
    store = _unreachable_store(get=redis.exceptions.ConnectionError("valkey down"))
    with pytest.raises(SessionStoreUnavailable):
        store.resolve("s1")


def test_end_raises_when_redis_errors():
    """Logout must be able to report that revocation did not happen."""
    store = _unreachable_store(delete=redis.exceptions.ConnectionError("valkey down"))
    with pytest.raises(SessionStoreUnavailable):
        store.end("s1")


def test_store_errors_do_not_swallow_programming_errors():
    """Only the Redis hierarchy is caught: a TypeError from a bad call site
    must surface as itself, not be relabelled an infrastructure outage
    (threads 17/18)."""
    store = _unreachable_store(expire=TypeError("bad argument"))
    with pytest.raises(TypeError):
        store.touch("s1")


# --- resolve() record parsing ------------------------------------------------

def test_resolve_returns_typed_record():
    store = _fake_store()
    store.start("s1", user_id=42, epoch=7)
    record = store.resolve("s1")
    assert isinstance(record, SessionRecord)
    assert (record.user_id, record.epoch) == (42, 7)
    assert record.issued_at > 0


def test_resolve_missing_key_is_none():
    assert _fake_store().resolve("never-seen") is None


@pytest.mark.parametrize("stored", [
    "1",                              # pre-#368 bare marker
    "not json at all",
    '"a bare string"',
    "[]",
    '{"epoch": 1}',                   # no user_id
    '{"user_id": 1}',                 # no epoch
    '{"user_id": "abc", "epoch": 0}',
    '{"user_id": null, "epoch": 0}',
    '{"user_id": 1.5, "epoch": 0}',
    '{"user_id": true, "epoch": 0}',  # bool is an int subclass -- must not pass
    '{"user_id": 1, "epoch": "x"}',
    '{"user_id": 1, "epoch": true}',
])
def test_resolve_malformed_record_is_none_not_an_exception(stored):
    """A malformed value is an authentication failure, never a 500: no
    TypeError/ValueError escapes the parse (thread 20.2)."""
    store = _fake_store()
    store._client.set("cviche:session:idle:bad", stored, ex=1200)
    assert store.resolve("bad") is None


def test_disabled_store_resolve_and_end_are_noops():
    store = IdleSessionStore("", 1200)
    assert store.resolve("s1") is None
    store.end("s1")               # must not raise
    store.start("s1", user_id=1, epoch=0)


def test_client_is_built_with_socket_timeouts(monkeypatch):
    """The lazily-built client must bound socket ops so a hung Valkey raises
    SessionStoreUnavailable instead of blocking the request thread forever."""
    import redis
    captured = {}

    def fake_from_url(url, **kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(fake_from_url))
    store = IdleSessionStore("redis://fake", 1200)
    store._redis()                        # trigger lazy construction
    assert captured.get("socket_timeout") == 2
    assert captured.get("socket_connect_timeout") == 2


# --- integration through the auth dependency ---------------------------------

@pytest.fixture
def idle_store(monkeypatch):
    """Install a fake-backed idle store as the process singleton for a test."""
    store = _fake_store()
    monkeypatch.setattr(session_idle, "_store", store)
    return store


def _make_user(db):
    from app.models import User
    user = User(email="test@example.com", display_name="Test User",
                role="user", consent_version="1.0")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_minted_cookie_carries_sid_and_is_accepted(client, db, seed_simple_mode, idle_store):
    from app.auth import COOKIE_NAME, create_session_cookie, decode_session_cookie
    user = _make_user(db)
    cookie = create_session_cookie(user, db)      # seeds the idle key
    assert decode_session_cookie(cookie).get("sid")

    client.cookies.set(COOKIE_NAME, cookie)
    assert client.get("/api/auth/me").status_code == 200


def test_expired_idle_key_yields_401(client, db, seed_simple_mode, idle_store):
    """A lapsed key is rejected as `session_invalid`, not `session_idle`.

    With the store enabled the cookie is thin: the key IS the identity, so a
    key that is gone means the session cannot be resolved at all, and identity
    resolution rejects it before the idle refresh is ever reached. The
    `session_idle` code now only reports the narrow race where the key vanishes
    between resolve() and expire() -- see test_auth_audit_events.py's
    idle_timeout test.
    """
    from app.auth import COOKIE_NAME, create_session_cookie, decode_session_cookie
    user = _make_user(db)
    cookie = create_session_cookie(user, db)
    sid = decode_session_cookie(cookie)["sid"]

    idle_store.end(sid)                            # simulate the window lapsing
    client.cookies.set(COOKIE_NAME, cookie)

    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "session_invalid"


def test_legacy_cookie_without_sid_bypasses_idle(client, db, seed_simple_mode):
    """A rich cookie minted before this feature has no `sid`; it must still
    work, bounded only by the absolute cookie TTL.

    Runs with the store DISABLED (no idle_store fixture): a store-enabled
    deployment accepts v2 cookies only, so "no sid" is not a shape that can
    reach this path there at all.
    """
    from app.auth import COOKIE_NAME, _serializer
    user = _make_user(db)
    legacy = _serializer.dumps({
        "user_id": user.id, "email": user.email, "epoch": 0,
        "role": user.role, "issued_at": int(time.time()),
    })
    client.cookies.set(COOKIE_NAME, legacy)
    assert client.get("/api/auth/me").status_code == 200


def test_logout_deletes_idle_key(client, db, seed_simple_mode, idle_store):
    from app.auth import COOKIE_NAME, create_session_cookie, decode_session_cookie
    user = _make_user(db)
    cookie = create_session_cookie(user, db)
    sid = decode_session_cookie(cookie)["sid"]

    client.cookies.set(COOKIE_NAME, cookie)
    assert client.post("/api/auth/logout").status_code == 200
    # Key is gone -> a replayed cookie now reads as idle-expired.
    assert idle_store.touch(sid) is False
