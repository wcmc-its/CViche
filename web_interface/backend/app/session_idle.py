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

As of #368, when this store is enabled the Valkey value is no longer the bare
marker string "1" -- it's a JSON blob carrying identity ({user_id, epoch,
issued_at}). The key is then the session's identity of record, not just an idle
marker: auth.py mints a thin, identity-free cookie (``{"v": 2, "sid": ...}``)
and resolves the real user_id/epoch here on every request via ``resolve()``.

Backed by the same ``CVICHE_REDIS_URL`` as the pipeline broker. Two
configurations, two very different failure policies:

  - No URL configured  -> ``enabled`` is False; every method is a no-op and
    ``touch()`` returns True. Idle enforcement is simply off, auth.py mints a
    rich ``{"v": 1, ...}`` cookie instead, and the cookie's absolute TTL is the
    only bound. (This is production today -- prod has no CVICHE_REDIS_URL.)
  - URL configured but Valkey unreachable -> FAIL CLOSED. Every method raises
    ``SessionStoreUnavailable``; callers turn that into 503 on REST, close 1013
    on the WebSocket, a refusal to mint at login, and a reported failure at
    logout. This replaces the fail-open policy #368 shipped with (PR #657
    review, threads 10/13-18): when the store is enabled the record IS the
    session's identity of record, so an unreachable store does not mean "assume
    the session is fine", it means identity cannot be verified at all. Failing
    open there would let a deleted (logged-out) or revoked session authenticate
    again for the length of an outage, off client-supplied fields.

Only a *reachable-but-absent* key (a genuine expiry or logout) makes ``touch()``
return False and ``resolve()`` return None.

Cookies minted before this feature shipped carry no ``sid``; on a store-disabled
deployment callers skip the check for them, so they remain bounded by the
absolute cookie TTL only.
"""
import json
import logging
import threading
import time
from dataclasses import dataclass

import redis.exceptions

from app.config_loader import get_config
from app.services.config_service import SESSION_IDLE_TIMEOUT

logger = logging.getLogger(__name__)

# Namespaced under cviche:session: so it can never collide with the broker's
# run-event / cancel keys (cviche:run:*).
_KEY = "cviche:session:idle:{sid}"

# The one exception family a reachability failure can surface as. RedisError is
# the hierarchy root -- ConnectionError, TimeoutError, ResponseError and the
# rest are subclasses -- so this catches "the store did not answer" without the
# blanket `except Exception` that used to mask genuine programming errors as
# infrastructure failures (PR #657 review, threads 17/18).
_STORE_ERRORS = (redis.exceptions.RedisError,)

# How many characters of a sid are safe to put in a log line: enough to
# correlate two entries, far too few to replay.
_SID_LOG_PREFIX = 6


class SessionStoreUnavailable(RuntimeError):
    """The session store is configured but could not be reached or did not answer.

    Distinct on purpose from "no record for this sid" (``resolve()`` -> None) and
    from "the idle window lapsed" (``touch()`` -> False): those are answers, this
    is the absence of one. Callers must fail closed on it.
    """


@dataclass(frozen=True)
class SessionRecord:
    """A session's server-side identity of record, as stored under its sid."""
    user_id: int
    epoch: int
    issued_at: int


def _coerce_int(value) -> int | None:
    """A stored field as an int, or None if it is not one.

    `bool` is excluded deliberately: it is an `int` subclass, so a stored
    `true` would otherwise resolve to user_id 1.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


class IdleSessionStore:
    """Sliding-TTL idle tracker and identity store. ``enabled`` is False when no
    URL is configured, in which case every method is a no-op and ``touch()``
    returns True. When enabled, every method raises SessionStoreUnavailable
    rather than guessing if Valkey cannot be reached."""

    def __init__(self, url: str | None, ttl_seconds: int):
        self.url = url or None
        self.enabled = bool(self.url)
        self.ttl = ttl_seconds
        self._client = None
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> IdleSessionStore:
        url, _ = get_config("redis", "CVICHE_REDIS_URL", default="")
        return cls(url, SESSION_IDLE_TIMEOUT)

    def _redis(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    import redis
                    # Bound socket ops so a hung/black-holed Valkey raises
                    # TimeoutError instead of blocking the request thread
                    # forever. A refused connection already raises; a partition
                    # would not without this. ponytail: 2s ceiling, only hit on
                    # a hang.
                    self._client = redis.Redis.from_url(
                        self.url, socket_timeout=2, socket_connect_timeout=2
                    )
        return self._client

    def start(self, sid: str, user_id: int, epoch: int) -> None:
        """Seed a session's idle key at login with the full TTL, storing the
        session's identity ({user_id, epoch, issued_at}) as the value so
        resolve() can serve it back as the session's identity of record.

        Raises SessionStoreUnavailable if the key could not be written: it must
        be impossible for this to return normally, while enabled, without the
        record existing -- otherwise login mints a thin cookie whose identity
        was never registered and which no later request can resolve.
        """
        if not self.enabled or not sid:
            return
        value = json.dumps({
            "user_id": user_id,
            "epoch": epoch,
            "issued_at": int(time.time()),
        })
        try:
            self._redis().set(_KEY.format(sid=sid), value, ex=self.ttl)
        except _STORE_ERRORS as exc:
            logger.error("Session store unavailable during start", exc_info=True)
            raise SessionStoreUnavailable("could not create the server-side session") from exc

    def resolve(self, sid: str) -> SessionRecord | None:
        """Resolve a session's identity from the store.

        Returns None -- "there is no such session" -- when enforcement is
        disabled, no sid was supplied, the key is absent/expired, or the value
        is not a well-formed record (e.g. a pre-#368 key still holding the bare
        "1" marker, or a hand-edited blob). A malformed value can never become a
        partial identity, and no parse error escapes: that path is an
        authentication failure, not a 500.

        Raises SessionStoreUnavailable if Valkey could not be reached. The
        caller must not treat that as "no such session".
        """
        if not self.enabled or not sid:
            return None
        try:
            raw = self._redis().get(_KEY.format(sid=sid))
        except _STORE_ERRORS as exc:
            logger.error("Session store unavailable during resolve", exc_info=True)
            raise SessionStoreUnavailable("could not read the server-side session") from exc
        if raw is None:
            return None
        return self._parse_record(sid, raw)

    def _parse_record(self, sid: str, raw) -> SessionRecord | None:
        """A stored value as a SessionRecord, or None if it is not one."""
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            logger.warning("Malformed session record for sid %s... (not JSON)",
                           sid[:_SID_LOG_PREFIX])
            return None
        if not isinstance(data, dict):
            logger.warning("Malformed session record for sid %s... (not an object)",
                           sid[:_SID_LOG_PREFIX])
            return None
        user_id = _coerce_int(data.get("user_id"))
        epoch = _coerce_int(data.get("epoch"))
        if user_id is None or epoch is None:
            logger.warning("Malformed session record for sid %s... (bad user_id/epoch)",
                           sid[:_SID_LOG_PREFIX])
            return None
        issued_at = _coerce_int(data.get("issued_at"))
        return SessionRecord(user_id=user_id, epoch=epoch, issued_at=issued_at or 0)

    def touch(self, sid: str) -> bool:
        """Slide the idle window for an active session.

        Returns True if the session is still active -- the key existed and its
        TTL was refreshed, or enforcement is disabled. Returns False when Valkey
        is reachable and the key is gone (idle-expired or logged out), in which
        case the caller must reject the request with 401.

        Raises SessionStoreUnavailable if Valkey could not be reached: an
        outage is not evidence that the idle window is still open.
        """
        if not self.enabled or not sid:
            return True
        try:
            # expire() returns True iff the key existed and the new TTL was set;
            # False means the key is gone -> the idle window lapsed.
            return bool(self._redis().expire(_KEY.format(sid=sid), self.ttl))
        except _STORE_ERRORS as exc:
            logger.error("Session store unavailable during touch", exc_info=True)
            raise SessionStoreUnavailable("could not refresh the server-side session") from exc

    def end(self, sid: str) -> None:
        """Delete a session's idle key on logout.

        Raises SessionStoreUnavailable if the delete could not be issued, so
        logout can report that the server-side session is still live rather
        than claim a revocation that never happened.
        """
        if not self.enabled or not sid:
            return
        try:
            self._redis().delete(_KEY.format(sid=sid))
        except _STORE_ERRORS as exc:
            logger.error("Session store unavailable during end", exc_info=True)
            raise SessionStoreUnavailable("could not revoke the server-side session") from exc


_store: IdleSessionStore | None = None
_store_lock = threading.Lock()


def get_idle_store() -> IdleSessionStore:
    """Process-wide idle store, built once from the environment."""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = IdleSessionStore.from_env()
    return _store
