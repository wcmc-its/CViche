"""Login rate limiting, distributed across pods via Valkey/Redis.

The previous limiter lived in a per-process ``defaultdict`` (issue #342): each pod
kept its own counter, so an attacker could bypass the limit by spreading login
attempts across pods, and a restart wiped all throttling. This backs the counter
with the same ``CVICHE_REDIS_URL`` as the idle-session store and pipeline broker
so every pod shares one window.

Degradation (a rate limiter must never fail to *zero* protection):
  - No URL configured        -> per-process in-memory fallback (dev, or a single
    pod). Same protection the old limiter gave.
  - URL set but Valkey down  -> log and fall back to in-memory, never fail open to
    unlimited logins.
"""
import logging
import threading
import time
from collections import defaultdict

from app.config_loader import get_config
from app.services.config_service import LOGIN_RATE_LIMIT_MAX, LOGIN_RATE_LIMIT_WINDOW

logger = logging.getLogger(__name__)

# Namespaced like the idle-session keys (cviche:session:*) so it can never
# collide with the broker's run keys (cviche:run:*).
_KEY = "cviche:login:throttle:{ip}"


class LoginThrottle:
    """Fixed-window login counter. ``enabled`` is True only when a Redis URL is
    configured; otherwise (and on any Valkey error) it falls back to a
    per-process sliding-window counter."""

    def __init__(self, url: str | None, max_attempts: int, window_seconds: int):
        self.url = url or None
        self.enabled = bool(self.url)
        self.max_attempts = max_attempts
        self.window = window_seconds
        self._client = None
        self._lock = threading.Lock()
        self._local: dict[str, list[float]] = defaultdict(list)
        self._local_lock = threading.Lock()
        self._last_sweep = 0.0

    @classmethod
    def from_env(cls) -> "LoginThrottle":
        url, _ = get_config("redis", "CVICHE_REDIS_URL", default="")
        return cls(url, LOGIN_RATE_LIMIT_MAX, LOGIN_RATE_LIMIT_WINDOW)

    def _redis(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    import redis
                    # Bound socket ops so a hung Valkey raises instead of blocking
                    # the login request thread (matches IdleSessionStore).
                    self._client = redis.Redis.from_url(
                        self.url, socket_timeout=2, socket_connect_timeout=2
                    )
        return self._client

    def allow(self, ip: str) -> bool:
        """Return True if the login attempt is within the limit, False if throttled."""
        if not self.enabled:
            return self._allow_local(ip)
        try:
            key = _KEY.format(ip=ip)
            count = self._redis().incr(key)
            if count == 1:
                # ponytail: fixed window; expire only on the first hit. Orphan key
                # if the process dies between incr and expire -- harmless for a
                # login counter, upgrade to a pipeline/Lua SET if it ever matters.
                self._redis().expire(key, self.window)
            return count <= self.max_attempts
        except Exception:
            logger.warning(
                "Login throttle: Valkey unavailable, falling back to in-memory",
                exc_info=True,
            )
            return self._allow_local(ip)

    def _allow_local(self, ip: str) -> bool:
        """Per-process sliding-window fallback (the original in-memory limiter)."""
        # Locked: login() (auth_routes.py) became a plain `def` in #656's
        # review response, so FastAPI now threadpools it -- real concurrent
        # OS threads can call this, unlike when it was an async def with no
        # await (#414: 200 concurrent POSTs -> 1 thread ident under the old
        # shape; reproduces with real OS threads, 18/200 over-admit
        # unsynchronized). This is exactly the upgrade this shortcut's own
        # comment named in advance.
        with self._local_lock:
            now = time.time()
            # Bound the dict: an IP seen once is otherwise retained for the
            # life of the process, and client_ip comes from a
            # client-supplied XFF header. Sweep at most once per window so
            # this stays O(n) per window, not per call.
            # ponytail: growth within a single window is still unbounded;
            # set CVICHE_REDIS_URL to get the TTL-backed path instead.
            if now - self._last_sweep >= self.window:
                self._last_sweep = now
                self._local = defaultdict(
                    list,
                    {k: v for k, v in self._local.items() if v and now - v[-1] < self.window},
                )
            self._local[ip] = [ts for ts in self._local[ip] if now - ts < self.window]
            if len(self._local[ip]) >= self.max_attempts:
                return False
            self._local[ip].append(now)
            return True


_throttle: "LoginThrottle | None" = None
_throttle_lock = threading.Lock()


def get_login_throttle() -> LoginThrottle:
    """Process-wide login throttle, built once from the environment."""
    global _throttle
    if _throttle is None:
        with _throttle_lock:
            if _throttle is None:
                _throttle = LoginThrottle.from_env()
    return _throttle
