"""SAML assertion replay cache backed by Valkey/Redis.

The SP runs with ``allow_unsolicited=True`` (app/saml_client.py) because
IdP-initiated SSO must keep working, which means pysaml2 never matches
``InResponseTo`` against an outstanding-request map -- a captured, signed
SAMLResponse can be replayed wholesale at the (necessarily CSRF-exempt) ACS
until its NotOnOrAfter lapses. This cache closes that window without breaking
IdP-initiated flows: every successfully parsed assertion ID is recorded once,
and a second presentation of the same ID is rejected.

Backed by the same ``CVICHE_REDIS_URL`` as the pipeline broker and the
idle-session store, with the same fail-open posture:

  - URL configured, Valkey reachable -> cross-pod protection via atomic
    ``SET NX EX``; the key TTL covers the assertion's own validity window
    (plus clock-skew slack), after which the ID can no longer be accepted
    by pysaml2 anyway.
  - URL configured, Valkey unreachable -> FAIL OPEN: log loudly and let the
    login proceed. A Valkey outage must not lock everyone out; the replay
    window this reopens is bounded by the assertion's validity window.
  - No URL configured -> in-process dict fallback. Per-pod best-effort only:
    replicas do not share it and it dies with the process. Deployments get
    cross-pod protection once CVICHE_REDIS_URL is set.
"""
import logging
import threading
import time

from app.config_loader import get_config

logger = logging.getLogger(__name__)

# Namespaced so it can never collide with the broker's run-event keys
# (cviche:run:*) or the idle-session keys (cviche:session:*).
_KEY = "cviche:saml:assertion:{aid}"

# Fallback TTL when the response carries no NotOnOrAfter, and slack added on
# top of the assertion window to absorb SP/IdP clock skew (matches pysaml2's
# customary allowance).
_DEFAULT_TTL = 3600
_CLOCK_SKEW = 300
_MAX_TTL = 86400


def assertion_ids(authn_response) -> list[str]:
    """Assertion IDs carried by a parsed pysaml2 AuthnResponse.

    Only string IDs are returned; a stubbed/mocked response without real IDs
    yields [] and the caller is expected to skip the replay gate (fail open).
    """
    ids = []
    try:
        candidates = list(getattr(authn_response, "assertions", None) or [])
    except TypeError:
        candidates = []
    single = getattr(authn_response, "assertion", None)
    if single is not None:
        candidates.append(single)
    for assertion in candidates:
        aid = getattr(assertion, "id", None)
        if isinstance(aid, str) and aid and aid not in ids:
            ids.append(aid)
    return ids


def replay_ttl(authn_response, now: float | None = None) -> int:
    """Cache TTL covering the assertion's remaining validity window.

    pysaml2 stamps ``not_on_or_after`` (epoch seconds) from the subject
    confirmation while parsing; beyond that instant it rejects the assertion
    itself, so the ID only needs to be remembered until then (plus skew).
    Falls back to a fixed window when the response carries none.
    """
    noa = getattr(authn_response, "not_on_or_after", 0)
    if not isinstance(noa, (int, float)) or noa <= 0:
        return _DEFAULT_TTL
    if now is None:
        now = time.time()
    return int(min(max(noa - now + _CLOCK_SKEW, _CLOCK_SKEW), _MAX_TTL))


class SamlReplayCache:
    """First-presentation check for SAML assertion IDs."""

    def __init__(self, url: str | None):
        self.url = url or None
        self._client = None
        self._lock = threading.Lock()
        # Unconfigured fallback: assertion id -> expiry epoch (see module doc).
        self._local: dict[str, float] = {}

    @classmethod
    def from_env(cls) -> "SamlReplayCache":
        url, _ = get_config("redis", "CVICHE_REDIS_URL", default="")
        return cls(url)

    def _redis(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    import redis
                    # Bound socket ops so a hung/black-holed Valkey raises
                    # (and then fails open) instead of blocking the login.
                    self._client = redis.Redis.from_url(
                        self.url, socket_timeout=2, socket_connect_timeout=2
                    )
        return self._client

    def check_and_record(self, ids: list[str], ttl_seconds: int) -> bool:
        """Record the IDs; return True iff none of them was seen before.

        Returns False ONLY on a genuine replay (an ID already recorded within
        its TTL). Errors reaching Valkey fail open with a loud log -- a Valkey
        outage must not lock every SAML user out.
        """
        if not ids:
            return True
        if self.url:
            try:
                fresh = True
                for aid in ids:
                    # SET NX EX: atomic first-presentation check + record, so
                    # concurrent replays across pods cannot both win.
                    if not self._redis().set(
                        _KEY.format(aid=aid), "1", nx=True, ex=ttl_seconds
                    ):
                        fresh = False
                return fresh
            except Exception:
                logger.error(
                    "[SECURITY] SAML replay cache unreachable; failing open -- "
                    "assertion replay protection suspended until Valkey recovers",
                    exc_info=True,
                )
                return True
        return self._check_local(ids, ttl_seconds)

    def _check_local(self, ids: list[str], ttl_seconds: int) -> bool:
        now = time.time()
        with self._lock:
            expired = [aid for aid, exp in self._local.items() if exp <= now]
            for aid in expired:
                del self._local[aid]
            fresh = all(aid not in self._local for aid in ids)
            for aid in ids:
                self._local[aid] = max(self._local.get(aid, 0), now + ttl_seconds)
            return fresh


_cache: "SamlReplayCache | None" = None
_cache_lock = threading.Lock()


def get_replay_cache() -> SamlReplayCache:
    """Process-wide replay cache, built once from the environment."""
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = SamlReplayCache.from_env()
    return _cache
