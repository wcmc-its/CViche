"""#342: login throttle -- distributed across pods, degrades to in-memory."""
import os
os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

from app.login_throttle import LoginThrottle


class FakeRedis:
    """Minimal INCR/EXPIRE stub so the distributed path is testable with no dep."""

    def __init__(self):
        self.counts: dict[str, int] = {}
        self.expires: dict[str, int] = {}

    def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    def expire(self, key, ttl):
        self.expires[key] = ttl
        return True


def test_in_memory_fallback_allows_then_blocks():
    """No Redis URL -> per-process counter (dev / single pod)."""
    t = LoginThrottle(url=None, max_attempts=3, window_seconds=60)
    assert not t.enabled
    assert [t.allow("1.2.3.4") for _ in range(3)] == [True, True, True]
    assert t.allow("1.2.3.4") is False


def test_in_memory_is_per_ip():
    t = LoginThrottle(url=None, max_attempts=1, window_seconds=60)
    assert t.allow("a") is True
    assert t.allow("a") is False
    assert t.allow("b") is True  # a different IP has its own window


def test_distributed_path_counts_and_expires_once():
    """The whole point of #342: one shared counter, TTL set once per window."""
    t = LoginThrottle(url="redis://fake", max_attempts=3, window_seconds=60)
    assert t.enabled
    fake = FakeRedis()
    t._client = fake  # inject before the lazy real client is built
    assert [t.allow("9.9.9.9") for _ in range(3)] == [True, True, True]
    assert t.allow("9.9.9.9") is False
    assert fake.expires == {"cviche:login:throttle:9.9.9.9": 60}


def test_valkey_error_degrades_to_local_not_open():
    """A rate limiter must never fail to zero protection."""
    class BoomRedis:
        def incr(self, key):
            raise ConnectionError("valkey down")

    t = LoginThrottle(url="redis://fake", max_attempts=2, window_seconds=60)
    t._client = BoomRedis()
    assert t.allow("x") is True
    assert t.allow("x") is True
    assert t.allow("x") is False  # still throttled via the in-memory fallback
