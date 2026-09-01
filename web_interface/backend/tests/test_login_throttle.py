"""#342: login throttle -- distributed across pods, degrades to in-memory."""
import os
import threading
import time
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


def test_allow_local_does_not_over_admit_under_real_concurrent_threads():
    """#414's own reproduction, re-run against the fix instead of the guard
    that used to keep login() async-only to avoid needing it.

    login() (auth_routes.py) is a plain `def` since #656's review response,
    so FastAPI now threadpools it -- _allow_local can genuinely be called
    from concurrent OS threads, not serialized on one event loop. #414
    reproduced 18/200 over-admits with real threads and no lock; this pins
    that it can't happen now that _allow_local holds _local_lock."""
    t = LoginThrottle(url=None, max_attempts=50, window_seconds=60)
    n_threads = 200
    results = [None] * n_threads
    barrier = threading.Barrier(n_threads)

    def worker(i):
        barrier.wait()  # maximize contention: every thread starts together
        results[i] = t.allow("10.0.0.1")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n_threads)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    admitted = sum(1 for r in results if r is True)
    assert admitted == t.max_attempts, (
        f"expected exactly {t.max_attempts} admits under {n_threads} concurrent "
        f"callers, got {admitted} -- _allow_local over-admitted"
    )


def test_in_memory_fallback_sweeps_lapsed_ips():
    """The fallback dict must not grow once per distinct (client-supplied) IP."""
    t = LoginThrottle(url=None, max_attempts=3, window_seconds=1)
    for n in range(500):
        t.allow(f"10.0.0.{n}")
    assert len(t._local) == 500
    time.sleep(1.1)  # every window above has now lapsed
    t.allow("10.9.9.9")
    assert len(t._local) == 1  # swept; only the live IP is retained
