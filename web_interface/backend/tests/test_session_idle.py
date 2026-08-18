"""Server-side idle session enforcement (app/session_idle.py + auth wiring).

Contract:
  - A signed cookie now carries a per-session `sid`.
  - One Valkey key per sid with a sliding TTL; each authenticated request
    refreshes it. A reachable-but-absent key (idle-expired or logged out) is
    rejected with 401 `session_idle`. Logout deletes the key.
  - Cookies without a `sid` (minted before this feature) bypass the check.
  - Disabled (no URL) or unreachable Valkey -> fail open (touch() == True).

The store tests use fakeredis for real TTL/expire semantics; the integration
tests drive the auth dependency through the TestClient with a fake-backed store.
"""
import time
from unittest.mock import MagicMock

import pytest

import app.session_idle as session_idle
from app.session_idle import IdleSessionStore


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
    store = _fake_store(ttl=1)
    store.start("s1", user_id=1, epoch=0)
    time.sleep(1.1)
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


def test_touch_fails_open_when_redis_errors():
    store = IdleSessionStore("redis://fake", 1200)
    client = MagicMock()
    client.expire.side_effect = ConnectionError("valkey down")
    store._client = client
    assert store.touch("s1") is True      # unreachable -> fail open, not 401


def test_touch_fails_open_on_timeout():
    """A hung/partitioned Valkey surfaces as TimeoutError once socket_timeout is
    set; it must fail open exactly like a refused connection, not block or 401."""
    store = IdleSessionStore("redis://fake", 1200)
    client = MagicMock()
    client.expire.side_effect = TimeoutError("valkey hung")
    store._client = client
    assert store.touch("s1") is True


def test_client_is_built_with_socket_timeouts(monkeypatch):
    """The lazily-built client must bound socket ops so a hung Valkey raises
    (and then fails open) instead of blocking the request thread forever."""
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
    from app.auth import create_session_cookie, decode_session_cookie, COOKIE_NAME
    user = _make_user(db)
    cookie = create_session_cookie(user)          # seeds the idle key
    assert decode_session_cookie(cookie).get("sid")

    client.cookies.set(COOKIE_NAME, cookie)
    assert client.get("/api/auth/me").status_code == 200


def test_expired_idle_key_yields_401(client, db, seed_simple_mode, idle_store):
    from app.auth import create_session_cookie, decode_session_cookie, COOKIE_NAME
    user = _make_user(db)
    cookie = create_session_cookie(user)
    sid = decode_session_cookie(cookie)["sid"]

    idle_store.end(sid)                            # simulate the window lapsing
    client.cookies.set(COOKIE_NAME, cookie)

    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "session_idle"


def test_legacy_cookie_without_sid_bypasses_idle(client, db, seed_simple_mode, idle_store):
    """A cookie minted before this feature has no `sid`; it must still work."""
    from app.auth import _serializer, COOKIE_NAME
    user = _make_user(db)
    legacy = _serializer.dumps({
        "user_id": user.id, "email": user.email,
        "role": user.role, "issued_at": int(time.time()),
    })
    client.cookies.set(COOKIE_NAME, legacy)
    assert client.get("/api/auth/me").status_code == 200


def test_logout_deletes_idle_key(client, db, seed_simple_mode, idle_store):
    from app.auth import create_session_cookie, decode_session_cookie, COOKIE_NAME
    user = _make_user(db)
    cookie = create_session_cookie(user)
    sid = decode_session_cookie(cookie)["sid"]

    client.cookies.set(COOKIE_NAME, cookie)
    assert client.post("/api/auth/logout").status_code == 200
    # Key is gone -> a replayed cookie now reads as idle-expired.
    assert idle_store.touch(sid) is False
