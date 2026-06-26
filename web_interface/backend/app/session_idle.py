"""Server-side idle session enforcement backed by Valkey/Redis.

Sessions are stateless `itsdangerous`-signed cookies bounded only by an absolute
TTL (see auth.py / config_service.SESSION_TTL); the cookie carries no notion of
"last activity", so a forgotten or replayed cookie stays valid for the full TTL
no matter how long the user has been gone. This module adds a server-enforced
idle timeout that is independent of the cookie:

  - At login a random ``sid`` is minted into the cookie and one Valkey key is
    created for it with a sliding TTL (SESSION_IDLE_TIMEOUT).
  - Every authenticated request refreshes that TTL. If the key has expired (the
    user was idle past the window) the request is rejected with 401 and must
    re-authenticate.
  - Logout deletes the key.

Backed by the same ``CVICHE_REDIS_URL`` as the pipeline broker, and degrades
safely:

  - No URL configured  -> ``enabled`` is False; every method is a no-op and
    ``touch()`` returns True. Idle enforcement is simply off and the cookie's
    absolute TTL still applies.
  - URL configured but Valkey unreachable -> FAIL OPEN: log a warning and treat
    the session as still active, so a transient Valkey blip can't sign everyone
    out at once. Only a *reachable-but-absent* key (a genuine expiry or logout)
    makes ``touch()`` return False.

Cookies minted before this feature shipped carry no ``sid``; callers skip the
check for them, so they remain bounded by the absolute cookie TTL only.
"""
import logging
import threading

from app.config_loader import get_config
from app.services.config_service import SESSION_IDLE_TIMEOUT

logger = logging.getLogger(__name__)

# Namespaced under cviche:session: so it can never collide with the broker's
# run-event / cancel keys (cviche:run:*).
_KEY = "cviche:session:idle:{sid}"


class IdleSessionStore:
    """Sliding-TTL idle tracker. ``enabled`` is False when no URL is configured,
    in which case every method is a no-op and ``touch()`` returns True."""

    def __init__(self, url: str | None, ttl_seconds: int):
        self.url = url or None
        self.enabled = bool(self.url)
        self.ttl = ttl_seconds
        self._client = None
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "IdleSessionStore":
        url, _ = get_config("redis", "CVICHE_REDIS_URL", default="")
        return cls(url, SESSION_IDLE_TIMEOUT)

    def _redis(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    import redis
                    # Bound socket ops so a hung/black-holed Valkey raises
                    # TimeoutError (which the except-branches fail open on)
                    # instead of blocking the request thread forever. A refused
                    # connection already raises; a partition would not without
                    # this. ponytail: 2s ceiling, only hit on a hang.
                    self._client = redis.Redis.from_url(
                        self.url, socket_timeout=2, socket_connect_timeout=2
                    )
        return self._client

    def start(self, sid: str) -> None:
        """Seed a session's idle key at login with the full TTL."""
        if not self.enabled or not sid:
            return
        try:
            self._redis().set(_KEY.format(sid=sid), "1", ex=self.ttl)
        except Exception:
            # Fail open: a login must not break because Valkey is unreachable.
            logger.warning("Idle-session start failed (failing open)", exc_info=True)

    def touch(self, sid: str) -> bool:
        """Slide the idle window for an active session.

        Returns True if the session is still active -- the key existed and its
        TTL was refreshed, or enforcement is disabled / Valkey is unreachable
        (fail open). Returns False ONLY when Valkey is reachable and the key is
        gone (idle-expired or logged out), in which case the caller must reject
        the request with 401.
        """
        if not self.enabled or not sid:
            return True
        try:
            # expire() returns True iff the key existed and the new TTL was set;
            # False means the key is gone -> the idle window lapsed.
            return bool(self._redis().expire(_KEY.format(sid=sid), self.ttl))
        except Exception:
            logger.warning("Idle-session touch failed (failing open)", exc_info=True)
            return True

    def end(self, sid: str) -> None:
        """Delete a session's idle key on logout."""
        if not self.enabled or not sid:
            return
        try:
            self._redis().delete(_KEY.format(sid=sid))
        except Exception:
            logger.warning("Idle-session end failed", exc_info=True)


_store: "IdleSessionStore | None" = None
_store_lock = threading.Lock()


def get_idle_store() -> IdleSessionStore:
    """Process-wide idle store, built once from the environment."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = IdleSessionStore.from_env()
    return _store
