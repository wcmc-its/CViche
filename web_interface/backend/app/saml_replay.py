"""SAML assertion replay cache backed by Valkey/Redis.

The SP runs with ``allow_unsolicited=True`` (app/saml_client.py) because
IdP-initiated SSO must keep working, which means pysaml2 never matches
``InResponseTo`` against an outstanding-request map -- a captured, signed
SAMLResponse can be replayed wholesale at the (necessarily CSRF-exempt) ACS
until its NotOnOrAfter lapses. This cache closes that window without breaking
IdP-initiated flows: every successfully parsed assertion ID is recorded once,
and a second presentation of the same ID is rejected.

Backed by the same ``CVICHE_REDIS_URL`` as the pipeline broker and the
idle-session store. Its posture when replay cannot be verified is fail
CLOSED by default (opt out for local dev; see ``CVICHE_SAML_REPLAY_FAIL_CLOSED``
below), unlike the broker/session store's own fail-open behavior:

  - URL configured, Valkey reachable -> cross-pod protection via atomic
    ``SET NX EX``; the key TTL covers the assertion's own validity window
    (plus clock-skew slack), after which the ID can no longer be accepted
    by pysaml2 anyway.
  - URL configured, Valkey unreachable -> fail CLOSED BY DEFAULT: log loudly
    and reject the login. Safety over availability is the default; set
    ``CVICHE_SAML_REPLAY_FAIL_CLOSED=0`` (local dev only, when Valkey is not
    running) to fail open instead and let the login proceed. On a deployed
    (S3 storage) instance this opt-out is refused outright -- see
    ``check_deployed_posture``.
  - No URL configured -> in-process dict fallback. Per-pod best-effort only:
    replicas do not share it and it dies with the process. Deployments get
    cross-pod protection once CVICHE_REDIS_URL is set; ``check_deployed_posture``
    logs loudly (does not refuse to boot) when SAML is enabled without it.

``SamlReplayCache.check_and_record`` takes exactly one assertion ID, not a
collection. pysaml2 7.5.5's ``parse_assertion`` (``saml2/response.py``,
saml2int limitation) raises unless a response carries exactly one plain or
exactly one encrypted assertion, and the call site in app/api/saml_routes.py
rejects any response whose assertion_ids() still returns more than one (e.g.
one plain plus one encrypted), so a multi-key atomic claim is never needed.
See the ponytail comment there for the upgrade path.
"""
import calendar
import hashlib
import logging
import threading
import time

import redis.exceptions
from saml2.time_util import str_to_time

from app.config_loader import get_config

logger = logging.getLogger(__name__)

# Namespaced so it can never collide with the broker's run-event keys
# (cviche:run:*) or the idle-session keys (cviche:session:*). The assertion ID
# is IdP-supplied free text, so it is sha256-hashed (never used raw) to give a
# fixed, safe key shape regardless of its contents -- see _redis_key.
_KEY = "cviche:saml:assertion:{aid}"

# Explicit opt-out values for CVICHE_SAML_REPLAY_FAIL_CLOSED (case-insensitive).
# Anything else -- unset, "", or any other string -- means fail closed; see
# replay_fail_closed().
_FAIL_OPEN_VALUES = frozenset({"0", "false", "no", "off"})

# Same operational-error taxonomy as app/session_idle.py's _STORE_ERRORS: the
# base of Redis's connection/timeout/response error hierarchy. A programming
# error (TypeError, AttributeError, ...) is NOT caught here -- it propagates,
# per CODING_STANDARDS.md §5.4 / mrj4001 review (PR #781 thread r3966551686):
# converting every exception into an auth failure hides real bugs.
_STORE_ERRORS = (redis.exceptions.RedisError,)


def _redis_key(aid: str) -> str:
    return _KEY.format(aid=hashlib.sha256(aid.encode()).hexdigest())


def replay_fail_closed() -> bool:
    """Whether to REJECT a login when replay protection cannot be verified --
    Valkey unreachable, or an assertion carrying no ID.

    Default True (fail closed): unset, empty, or any value other than an
    explicit opt-out means safety over availability (#111's precedent --
    ``main.py``'s ``SECURE_AUTH_MODES`` allowlist fails closed on a missing
    value the same way). Set ``CVICHE_SAML_REPLAY_FAIL_CLOSED`` to one of
    ``_FAIL_OPEN_VALUES`` (env, or auth_config.yaml under `auth`) to fail open
    instead -- local dev only, when Valkey is not running. Refused outright on
    a deployed instance; see ``check_deployed_posture``.
    """
    raw, _ = get_config("auth", "CVICHE_SAML_REPLAY_FAIL_CLOSED", default="")
    return str(raw).strip().lower() not in _FAIL_OPEN_VALUES


def check_deployed_posture(auth_mode: str | None, storage_backend: str) -> None:
    """Fail-closed / loud-warn guard for SAML replay protection on a deployed
    (S3) instance. Mirrors ``main._guard_deployed_auth_mode``'s "deployed"
    test (``storage_backend == "s3"`` -- there is no ENVIRONMENT=production
    signal anywhere; #111's precedent). A no-op unless SAML is the active
    auth mode on a deployed instance.

    (a) The ``CVICHE_SAML_REPLAY_FAIL_CLOSED`` opt-out exists for local dev
        only (Valkey not running); on a deployment it is refused outright --
        unlike ``_guard_deployed_auth_mode``'s ``CVICHE_ALLOW_SIMPLE_AUTH``,
        there is no override env, because there is no legitimate deployed
        reason to run SAML without replay protection.
    (b) A deployment with SAML enabled but no ``CVICHE_REDIS_URL`` only gets
        per-pod (not cross-pod) replay protection -- the security boundary
        silently narrows. buildspec.yaml requires the variable but its
        rendered prod value isn't visible from here, so this logs loudly
        rather than refusing to boot: refusing could block a legitimate
        deploy on an assumption this module can't verify.
    """
    auth_mode = (auth_mode or "").strip().lower()
    storage_backend = (storage_backend or "").strip().lower()
    if storage_backend != "s3" or auth_mode != "saml":
        return
    if not replay_fail_closed():
        raise RuntimeError(
            "[SECURITY] CVICHE_SAML_REPLAY_FAIL_CLOSED opt-out is refused on a "
            "deployed (S3 storage) instance -- it exists for local dev only, "
            "when Valkey is not running. Remove it from the deployed config."
        )
    url, _ = get_config("redis", "CVICHE_REDIS_URL", default="")
    if not (url or "").strip():
        logger.error(
            "[SECURITY] SAML is enabled with no CVICHE_REDIS_URL: assertion "
            "replay protection is per-pod only, not cross-pod, on this "
            "deployed instance -- set CVICHE_REDIS_URL for full protection."
        )


# Fallback TTL when no boundary is derivable at all, and slack added on top
# of every derived boundary to absorb SP/IdP clock skew (matches pysaml2's
# customary allowance).
_DEFAULT_TTL = 3600
_CLOCK_SKEW = 300
_MAX_TTL = 86400

# Rate limit for full-traceback replay-cache-failure logs; see
# SamlReplayCache._log_store_failure.
_LOG_TRACEBACK_EVERY_SECONDS = 60


def _iter_assertions(authn_response: object) -> list[object]:
    """The assertion objects carried by a parsed pysaml2 AuthnResponse.

    Checks both ``.assertions`` (the list pysaml2 populates while parsing --
    see saml2/response.py) and the singular ``.assertion`` convenience
    attribute it sets to the first entry, without double-counting when both
    point at the same object list. A stubbed/mocked response without either
    yields [].
    """
    try:
        candidates = list(getattr(authn_response, "assertions", None) or [])
    except TypeError:
        candidates = []
    single = getattr(authn_response, "assertion", None)
    if single is not None and single not in candidates:
        candidates.append(single)
    return candidates


def assertion_ids(authn_response) -> list[str]:
    """Assertion IDs carried by a parsed pysaml2 AuthnResponse.

    Only non-empty, non-whitespace string IDs are returned, deduplicated; a
    stubbed/mocked response without real IDs yields []. A real pysaml2
    response always carries exactly one (saml2int limitation -- see this
    module's docstring), so the caller treats an empty result as
    replay-unverifiable (deferring to ``replay_fail_closed()``) and more than
    one as untrustworthy (always rejected) -- see
    app/api/saml_routes.py:_reject_replayed_assertion.
    """
    ids: list[str] = []
    for assertion in _iter_assertions(authn_response):
        aid = getattr(assertion, "id", None)
        if isinstance(aid, str) and aid.strip() and aid not in ids:
            ids.append(aid)
    return ids


def _parse_instant(raw: object, what: str) -> float | None:
    """Parse an ISO-8601 NotOnOrAfter string to epoch seconds.

    Returns None (with one WARNING, never an exception) for anything that
    isn't a parseable ISO-8601 string -- a boundary this module can't read is
    a boundary it skips, not a reason to fail the whole TTL computation.
    """
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return float(calendar.timegm(str_to_time(raw)))
    except (ValueError, OverflowError, TypeError, AttributeError):
        # str_to_time's own fallback regex match can be None for a string
        # that matches neither its strict format nor the fragment pattern,
        # which raises AttributeError inside pysaml2 rather than ValueError.
        logger.warning(
            "SAML replay TTL: unparsable %s NotOnOrAfter %r; skipping this boundary",
            what, raw,
        )
        return None


def _assertion_boundaries(assertion: object) -> list[float]:
    """Every NotOnOrAfter instant pysaml2 enforces for one assertion.

    ``assertion.conditions.not_on_or_after`` (saml2/response.py's
    ``condition_ok``, ~line 600) and each
    ``subject.subject_confirmation[i].subject_confirmation_data.not_on_or_after``
    (validated in ``_bearer_confirmed``, ~line 691, but never stored on the
    response) are both raw ISO-8601 strings straight off the XML -- pysaml2
    rejects the assertion once either lapses, so the replay key must outlive
    the later of them.
    """
    instants: list[float] = []
    conditions = getattr(assertion, "conditions", None)
    parsed = _parse_instant(getattr(conditions, "not_on_or_after", None), "Conditions")
    if parsed is not None:
        instants.append(parsed)

    subject = getattr(assertion, "subject", None)
    confirmations = getattr(subject, "subject_confirmation", None) or []
    for confirmation in confirmations:
        data = getattr(confirmation, "subject_confirmation_data", None)
        parsed = _parse_instant(
            getattr(data, "not_on_or_after", None), "SubjectConfirmationData"
        )
        if parsed is not None:
            instants.append(parsed)
    return instants


def replay_ttl(authn_response, now: float | None = None) -> int:
    """Cache TTL covering every validity boundary pysaml2 enforces.

    Walks the response's own ``not_on_or_after`` (already validated/converted
    by pysaml2's ``condition_ok`` from the assertion's Conditions), plus every
    assertion's Conditions and SubjectConfirmationData boundary (see
    ``_assertion_boundaries``) -- beyond the LATEST of these pysaml2 rejects
    the assertion itself, so the ID only needs to be remembered until then
    (plus clock-skew slack). Falls back to a fixed window when none is
    derivable at all.
    """
    if now is None:
        now = time.time()
    instants: list[float] = []

    noa = getattr(authn_response, "not_on_or_after", 0)
    if isinstance(noa, (int, float)) and noa > 0:
        instants.append(float(noa))
    for assertion in _iter_assertions(authn_response):
        instants.extend(_assertion_boundaries(assertion))

    if not instants:
        return _DEFAULT_TTL
    return int(min(max(max(instants) - now + _CLOCK_SKEW, _CLOCK_SKEW), _MAX_TTL))


class SamlReplayCache:
    """First-presentation check for a single SAML assertion ID.

    Configuration (``url``, ``fail_closed``) is resolved once at the
    composition root (``from_env``) and injected, rather than read from the
    environment on every call -- easier to unit test and to reuse if the
    config mechanism ever changes (mrj4001 review, PR #781 thread
    r3966565625).
    """

    def __init__(self, url: str | None, *, fail_closed: bool, client: object | None = None):
        # Blank-after-strip (including whitespace-only) is unconfigured, same
        # as an empty string -- a stray space in an env var must not silently
        # switch this to the Redis path and then fail from_url() later.
        stripped = (url or "").strip()
        self.url = stripped or None
        if url and not stripped:
            logger.warning(
                "CVICHE_REDIS_URL is whitespace-only; SAML replay protection "
                "falls back to the per-pod local cache."
            )
        self._fail_closed = fail_closed
        self._client = client
        self._lock = threading.Lock()
        # Unconfigured fallback: assertion id -> expiry epoch (see module doc).
        self._local: dict[str, float] = {}
        # Failure telemetry (no metrics library in this backend -- see
        # _log_store_failure); read via stats().
        self.failures = 0
        self.fail_closed_rejections = 0
        self._last_traceback_at = 0.0

    @classmethod
    def from_env(cls) -> SamlReplayCache:
        url, _ = get_config("redis", "CVICHE_REDIS_URL", default="")
        return cls(url, fail_closed=replay_fail_closed())

    def _redis(self):
        if self._client is None:
            with self._lock:
                if self._client is None:
                    import redis
                    # Bound socket ops so a hung/black-holed Valkey raises
                    # (and then fails CLOSED by default; see replay_fail_closed())
                    # instead of blocking the login.
                    self._client = redis.Redis.from_url(
                        self.url, socket_timeout=2, socket_connect_timeout=2
                    )
        return self._client

    def check_and_record(self, assertion_id: str, ttl_seconds: int) -> bool:
        """Record one assertion ID; return True iff it was not seen before.

        Raises ValueError for anything that isn't a genuine ID or a positive
        TTL -- the invalid/missing-ID state is explicit at this API rather
        than silently treated as "nothing to check" (mrj4001 review, PR #781
        thread r3966507348); callers convert an empty/absent ID into the
        configured fail-closed/fail-open decision themselves (see
        app/api/saml_routes.py:_reject_replayed_assertion).

        Returns False ONLY on a genuine replay (the ID already recorded
        within its TTL), or when Valkey is unreachable and this cache was
        constructed with ``fail_closed=True`` (the default): a
        rate-limited log, then the login is rejected. ``fail_closed=False``
        (local dev only) fails open instead of locking every SAML user out.
        """
        if not isinstance(assertion_id, str) or not assertion_id.strip():
            raise ValueError("assertion_id must be a non-empty, non-whitespace string")
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be > 0, got {ttl_seconds!r}")
        if self.url:
            try:
                client = self._redis()
            except _STORE_ERRORS as exc:
                return self._log_store_failure(exc, time.time())
            except ValueError as exc:
                # A malformed (non-blank) CVICHE_REDIS_URL surfaces here:
                # redis.Redis.from_url() raises ValueError lazily, at first
                # use inside _redis() -- CLIENT CONSTRUCTION only (D7), not
                # the .set() call below. This is configured-but-BROKEN, not
                # "unconfigured" -- it must never silently fall through to
                # the per-pod local cache; instead it gets exactly the same
                # fail-closed-by-posture treatment as any other store
                # failure. Scoped this narrowly on purpose: a ValueError
                # from client.set() itself would be a programming error
                # (e.g. a bad kwarg), not a config problem, and must
                # propagate rather than be swallowed as a store failure
                # (mrj4001 review, PR #781 fix-round item 2).
                return self._log_store_failure(exc, time.time())
            try:
                # SET NX EX: atomic first-presentation check + record -- one
                # key, one command, so concurrent replays across pods cannot
                # both win. (Never more than one ID per call -- see this
                # module's docstring.)
                return bool(
                    client.set(_redis_key(assertion_id), "1", nx=True, ex=ttl_seconds)
                )
            except _STORE_ERRORS as exc:
                return self._log_store_failure(exc, time.time())
        return self._check_local(assertion_id, ttl_seconds)

    def _log_store_failure(self, exc: Exception, now: float) -> bool:
        """Record + log a Valkey failure, rate-limiting the traceback.

        The first failure in each _LOG_TRACEBACK_EVERY_SECONDS window logs
        ERROR with a full traceback; later failures in the same window log
        one WARNING line without one -- a Valkey outage must not turn every
        SAML login attempt into a full stack trace (mrj4001 review, PR #781
        thread r3966573729). No metrics library exists in this backend, so
        the counters below (exposed via stats()) are the closest equivalent
        to saml_replay_cache_failure_total / saml_replay_fail_closed_total.
        """
        self.failures += 1
        if self._fail_closed:
            self.fail_closed_rejections += 1
            outcome = "FAILING CLOSED -- rejecting login until Valkey recovers"
        else:
            outcome = "failing open -- replay protection suspended until Valkey recovers"

        first_in_window = (now - self._last_traceback_at) >= _LOG_TRACEBACK_EVERY_SECONDS
        if first_in_window:
            self._last_traceback_at = now
            logger.error(
                "[SECURITY] SAML replay cache unreachable; %s "
                "(failures=%d, fail_closed_rejections=%d)",
                outcome, self.failures, self.fail_closed_rejections,
                exc_info=exc,
            )
        else:
            logger.warning(
                "[SECURITY] SAML replay cache unreachable; %s -- "
                "%d failures since start; traceback suppressed for %ds",
                outcome, self.failures, _LOG_TRACEBACK_EVERY_SECONDS,
            )
        return not self._fail_closed

    def stats(self) -> dict[str, int]:
        """Failure counters -- the closest thing to a metric this backend has."""
        return {
            "failures": self.failures,
            "fail_closed_rejections": self.fail_closed_rejections,
        }

    def _check_local(self, assertion_id: str, ttl_seconds: int) -> bool:
        now = time.time()
        with self._lock:
            expired = [aid for aid, exp in self._local.items() if exp <= now]
            for aid in expired:
                del self._local[aid]
            fresh = assertion_id not in self._local
            self._local[assertion_id] = max(
                self._local.get(assertion_id, 0), now + ttl_seconds
            )
            return fresh


_cache: SamlReplayCache | None = None
_cache_lock = threading.Lock()


def get_replay_cache() -> SamlReplayCache:
    """Process-wide replay cache, built once from the environment."""
    global _cache
    if _cache is None:
        with _cache_lock:
            if _cache is None:
                _cache = SamlReplayCache.from_env()
    return _cache


def set_replay_cache(cache: SamlReplayCache | None) -> None:
    """Explicit injection/reset hook for the process-wide singleton.

    Tests use this instead of monkeypatching the private ``_cache`` global
    directly (mrj4001 review, PR #781 threads r3966568501 / r3966565625);
    pass None to force the next ``get_replay_cache()`` call to rebuild from
    the environment. Guarded by the same lock as the double-checked read so
    an explicit reset can never race a concurrent lazy construction.
    """
    global _cache
    with _cache_lock:
        _cache = cache
