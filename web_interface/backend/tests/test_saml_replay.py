"""SAML assertion replay protection (app/saml_replay.py + ACS wiring).

Contract:
  - ``allow_unsolicited=True`` stays (IdP-initiated SSO must keep working), so
    InResponseTo is never matched. Instead every successfully parsed assertion
    ID is recorded once, with a TTL covering the assertion validity window.
  - A second presentation of the same assertion ID is rejected at the ACS
    with a 302 redirect and a [SECURITY] log.
  - Valkey reachable -> cross-pod protection via atomic SET NX EX.
  - Valkey configured but unreachable -> FAIL CLOSED BY DEFAULT (log loudly,
    reject the login); ``CVICHE_SAML_REPLAY_FAIL_CLOSED`` opts out for local
    dev only, when Valkey is not running.
  - Valkey unconfigured -> in-process dict fallback (per-pod best-effort).
  - ``SamlReplayCache.check_and_record`` takes exactly one assertion ID (a
    real pysaml2 response never carries more than one -- see
    app/saml_replay.py's module docstring); the ACS always rejects a
    response naming more than one, and defers to the fail-closed/fail-open
    decision for a response naming none.

The store tests use fakeredis for real SET NX EX semantics, construct
``SamlReplayCache`` via its constructor (injecting a client / fail_closed
posture) rather than monkeypatching private state, and install it as the
process singleton via ``set_replay_cache`` when an ACS-level test needs one.
The ACS tests drive the route through the TestClient with stubbed pysaml2
responses.
"""
import logging
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import redis.exceptions

import app.saml_replay as saml_replay
from app.auth import COOKIE_NAME
from app.saml_replay import (
    SamlReplayCache,
    assertion_ids,
    replay_ttl,
    replay_fail_closed,
    set_replay_cache,
    _DEFAULT_TTL,
    _CLOCK_SKEW,
    _MAX_TTL,
)
from tests.conftest import WCM_IDP_SCOPES


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Every test starts and ends with a clean process-wide singleton."""
    set_replay_cache(None)
    yield
    set_replay_cache(None)


def _fake_cache(*, fail_closed: bool = True):
    fakeredis = pytest.importorskip("fakeredis")
    return SamlReplayCache(
        "redis://fake", fail_closed=fail_closed, client=fakeredis.FakeStrictRedis()
    )


def _iso(epoch: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(epoch))


# --- cache unit tests: single-ID API -------------------------------------------

def test_redis_first_presentation_accepted_then_replay_rejected():
    cache = _fake_cache()
    assert cache.check_and_record("id-1", 60) is True
    assert cache.check_and_record("id-1", 60) is False


def test_redis_distinct_ids_are_independent():
    cache = _fake_cache()
    assert cache.check_and_record("id-1", 60) is True
    assert cache.check_and_record("id-2", 60) is True


def test_redis_id_expires_after_ttl():
    cache = _fake_cache()
    assert cache.check_and_record("id-1", 1) is True
    time.sleep(1.1)
    # Beyond the TTL pysaml2 itself rejects the assertion, so re-acceptance
    # of the ID here is safe.
    assert cache.check_and_record("id-1", 1) is True


def test_redis_key_is_hashed_not_raw():
    """The IdP-supplied assertion ID must never be used raw as a Redis key."""
    cache = _fake_cache()
    weird = "id with spaces / and : colons"
    cache.check_and_record(weird, 60)
    keys = [k.decode() if isinstance(k, bytes) else k
            for k in cache._client.keys("*")]
    assert keys, "expected a key to be written"
    assert all(weird not in k for k in keys), "raw assertion ID leaked into key"
    assert all(len(k.rsplit(":", 1)[-1]) == 64 for k in keys), "expected sha256 hex"


def test_client_is_built_with_socket_timeouts(monkeypatch):
    """A hung Valkey must raise (then fail closed by default) instead of
    blocking the login."""
    import redis
    captured = {}

    def fake_from_url(url, **kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(fake_from_url))
    cache = SamlReplayCache("redis://fake", fail_closed=True)
    cache._redis()
    assert captured.get("socket_timeout") == 2
    assert captured.get("socket_connect_timeout") == 2


def test_unconfigured_falls_back_to_local_dict():
    """No CVICHE_REDIS_URL -> per-pod dict still blocks same-pod replays."""
    cache = SamlReplayCache("", fail_closed=True)
    assert cache.check_and_record("id-1", 60) is True
    assert cache.check_and_record("id-1", 60) is False
    assert cache.check_and_record("id-2", 60) is True


def test_local_dict_purges_expired_entries():
    """The one place this suite pins the local dict's purge invariant
    directly -- every other test proves the same fallback through its
    observable True/False return instead."""
    cache = SamlReplayCache(None, fail_closed=True)
    assert cache.check_and_record("id-1", 60) is True
    cache._local["id-1"] = time.time() - 1          # simulate the TTL lapsing
    assert cache.check_and_record("id-1", 60) is True
    assert "id-1" in cache._local                    # re-recorded, old entry purged


# --- check_and_record's explicit-invalid-state contract (D1) -------------------

@pytest.mark.parametrize("bad_id", ["", "   ", "\t\n", None, 123])
def test_check_and_record_rejects_invalid_assertion_id(bad_id):
    """The invalid/missing-ID state is explicit at the API: check_and_record
    never silently treats an empty/blank/non-string ID as 'nothing to check'
    (mrj4001 review, PR #781 thread r3966507348) -- mutant: reverting this
    guard to `if not assertion_id: return True` makes this test fail."""
    cache = SamlReplayCache("", fail_closed=True)
    with pytest.raises(ValueError):
        cache.check_and_record(bad_id, 60)


@pytest.mark.parametrize("bad_ttl", [0, -1, -60])
def test_check_and_record_rejects_non_positive_ttl(bad_ttl):
    cache = SamlReplayCache("", fail_closed=True)
    with pytest.raises(ValueError):
        cache.check_and_record("id-1", bad_ttl)


# --- narrowed operational except (D2) -------------------------------------------

def test_fails_closed_by_default_when_redis_errors(caplog):
    """Default posture (fail_closed=True): reject, don't open."""
    client = MagicMock()
    client.set.side_effect = redis.exceptions.ConnectionError("valkey down")
    cache = SamlReplayCache("redis://fake", fail_closed=True, client=client)
    with caplog.at_level(logging.ERROR, logger="app.saml_replay"):
        assert cache.check_and_record("id-1", 60) is False
        assert cache.check_and_record("id-1", 60) is False  # still closed
    assert any("FAILING CLOSED" in r.getMessage() for r in caplog.records)


def test_fails_open_when_constructed_with_fail_closed_false(caplog):
    """Explicit local-dev opt-out (fail_closed=False)."""
    client = MagicMock()
    client.set.side_effect = redis.exceptions.ConnectionError("valkey down")
    cache = SamlReplayCache("redis://fake", fail_closed=False, client=client)
    with caplog.at_level(logging.ERROR, logger="app.saml_replay"):
        assert cache.check_and_record("id-1", 60) is True  # opted-out, stay open
    security_logs = [r for r in caplog.records if "[SECURITY]" in r.getMessage()]
    assert security_logs, "expected a loud [SECURITY] log on fail-open"


def test_redis_timeout_error_also_caught():
    """RedisError's other common subclass (timeout) is caught the same way."""
    client = MagicMock()
    client.set.side_effect = redis.exceptions.TimeoutError("timed out")
    cache = SamlReplayCache("redis://fake", fail_closed=True, client=client)
    assert cache.check_and_record("id-1", 60) is False


def test_typeerror_from_client_propagates():
    """A programming error (not a RedisError) must NOT be folded into a
    fail-closed/fail-open decision -- it propagates so the bug is visible
    (mrj4001 review, PR #781 thread r3966551686). Mutant: restoring a bare
    `except Exception:` here makes this test fail (the TypeError would be
    swallowed and turned into a bool instead of propagating)."""
    client = MagicMock()
    client.set.side_effect = TypeError("not a redis error")
    cache = SamlReplayCache("redis://fake", fail_closed=True, client=client)
    with pytest.raises(TypeError):
        cache.check_and_record("id-1", 60)


def test_valueerror_from_set_propagates_not_treated_as_malformed_url():
    """The malformed-URL ValueError special-case (D7) is scoped to CLIENT
    CONSTRUCTION only (redis.Redis.from_url(), inside _redis()) -- a
    ValueError raised by client.set() itself (e.g. a bad kwarg -- a
    programming error, not a config problem) must propagate exactly like
    the TypeError case above, not be swallowed as a store failure (PR #781
    fix-round item 2, following r3966551686's narrowing)."""
    client = MagicMock()
    client.set.side_effect = ValueError("not a malformed-URL error")
    cache = SamlReplayCache("redis://fake", fail_closed=True, client=client)
    with pytest.raises(ValueError):
        cache.check_and_record("id-1", 60)


# --- redis.Redis.from_url() failure (D7, D9 #7) ---------------------------------

def test_client_init_connection_error_fails_closed_by_default(monkeypatch):
    """A RedisError from from_url() itself (not just from set()) is covered
    by the same fail-closed-by-default contract."""
    import redis

    def raising_from_url(url, **kwargs):
        raise redis.exceptions.ConnectionError("cannot connect")

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(raising_from_url))
    cache = SamlReplayCache("redis://bad-host", fail_closed=True)
    assert cache.check_and_record("id-1", 60) is False


def test_client_init_connection_error_fails_open_when_configured(monkeypatch):
    import redis

    def raising_from_url(url, **kwargs):
        raise redis.exceptions.ConnectionError("cannot connect")

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(raising_from_url))
    cache = SamlReplayCache("redis://bad-host", fail_closed=False)
    assert cache.check_and_record("id-1", 60) is True


def test_malformed_url_valueerror_fails_closed_by_default(monkeypatch):
    """A malformed (non-blank) CVICHE_REDIS_URL is configured-but-BROKEN --
    from_url() raises ValueError lazily, at first use -- and must get the
    same fail-closed-by-posture treatment as any other store failure, never
    a silent fall-through to the local dict."""
    import redis

    def raising_from_url(url, **kwargs):
        raise ValueError("invalid URL scheme")

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(raising_from_url))
    cache = SamlReplayCache("not-a-valid-url", fail_closed=True)
    assert cache.check_and_record("id-1", 60) is False
    assert cache._local == {}, "must not silently fall back to the local dict"


def test_malformed_url_valueerror_fails_open_when_configured(monkeypatch, caplog):
    import redis

    def raising_from_url(url, **kwargs):
        raise ValueError("invalid URL scheme")

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(raising_from_url))
    cache = SamlReplayCache("not-a-valid-url", fail_closed=False)
    with caplog.at_level(logging.ERROR, logger="app.saml_replay"):
        assert cache.check_and_record("id-1", 60) is True
    assert any("[SECURITY]" in r.getMessage() for r in caplog.records)


# --- URL normalization (D7, D9 #14) ---------------------------------------------

@pytest.mark.parametrize("raw_url", ["", "   ", "\t", None])
def test_blank_or_whitespace_url_is_unconfigured(raw_url):
    cache = SamlReplayCache(raw_url, fail_closed=True)
    assert cache.url is None
    # Falls back to the local dict, same as an explicitly empty URL.
    assert cache.check_and_record("id-1", 60) is True
    assert cache.check_and_record("id-1", 60) is False


def test_whitespace_only_url_warns_once(caplog):
    with caplog.at_level(logging.WARNING, logger="app.saml_replay"):
        SamlReplayCache("   ", fail_closed=True)
    assert any("whitespace-only" in r.getMessage() for r in caplog.records)


def test_configured_url_is_stripped():
    cache = SamlReplayCache("  redis://fake  ", fail_closed=True)
    assert cache.url == "redis://fake"


# --- rate-limited failure logging + counters (D8) -------------------------------

def test_log_store_failure_first_in_window_logs_error_with_traceback(caplog):
    cache = SamlReplayCache(None, fail_closed=True)
    with caplog.at_level(logging.WARNING, logger="app.saml_replay"):
        result = cache._log_store_failure(ValueError("boom"), 1_000.0)
    assert result is False
    assert cache.failures == 1
    assert cache.fail_closed_rejections == 1
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is not None


def test_log_store_failure_second_in_window_logs_warning_without_traceback(caplog):
    cache = SamlReplayCache(None, fail_closed=True)
    with caplog.at_level(logging.WARNING, logger="app.saml_replay"):
        cache._log_store_failure(ValueError("boom"), 1_000.0)
        cache._log_store_failure(ValueError("boom-again"), 1_010.0)  # +10s, same window
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(errors) == 1
    assert len(warnings) == 1
    assert warnings[0].exc_info is None
    assert cache.failures == 2
    assert cache.fail_closed_rejections == 2


def test_log_store_failure_new_window_logs_error_again(caplog):
    cache = SamlReplayCache(None, fail_closed=True)
    with caplog.at_level(logging.WARNING, logger="app.saml_replay"):
        cache._log_store_failure(ValueError("boom"), 1_000.0)
        cache._log_store_failure(ValueError("boom-later"), 1_000.0 + 61)  # new window
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 2


def test_log_store_failure_open_posture_counts_but_does_not_reject(caplog):
    cache = SamlReplayCache(None, fail_closed=False)
    with caplog.at_level(logging.WARNING, logger="app.saml_replay"):
        result = cache._log_store_failure(ValueError("boom"), 1_000.0)
    assert result is True
    assert cache.failures == 1
    assert cache.fail_closed_rejections == 0


def test_stats_returns_failure_counters():
    cache = SamlReplayCache(None, fail_closed=True)
    assert cache.stats() == {"failures": 0, "fail_closed_rejections": 0}
    cache._log_store_failure(ValueError("boom"), 1_000.0)
    assert cache.stats() == {"failures": 1, "fail_closed_rejections": 1}


# --- replay_fail_closed() default interpretation --------------------------------

def test_replay_fail_closed_defaults_true_when_unset(monkeypatch):
    monkeypatch.delenv("CVICHE_SAML_REPLAY_FAIL_CLOSED", raising=False)
    monkeypatch.setattr(
        saml_replay, "get_config", lambda section, key, default="": (default, "default")
    )
    assert saml_replay.replay_fail_closed() is True


@pytest.mark.parametrize("raw", ["", "1", "true", "TRUE", "anything-else"])
def test_replay_fail_closed_true_for_non_opt_out_values(monkeypatch, raw):
    monkeypatch.setattr(
        saml_replay, "get_config", lambda section, key, default="": (raw, "env")
    )
    assert saml_replay.replay_fail_closed() is True


@pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "NO", "off", "OFF"])
def test_replay_fail_closed_false_for_explicit_opt_out(monkeypatch, raw):
    monkeypatch.setattr(
        saml_replay, "get_config", lambda section, key, default="": (raw, "env")
    )
    assert saml_replay.replay_fail_closed() is False


# --- check_deployed_posture is tested in tests/test_auth_mode_guard.py, which
# already covers _guard_deployed_auth_mode's identical "deployed" pattern.


# --- singleton injection / reset (D6, D9 #17 #18) --------------------------------

def test_get_replay_cache_returns_same_instance_twice(monkeypatch):
    monkeypatch.setattr(
        SamlReplayCache, "from_env", classmethod(lambda cls: cls("", fail_closed=True))
    )
    first = saml_replay.get_replay_cache()
    second = saml_replay.get_replay_cache()
    assert first is second


def test_set_replay_cache_installs_explicit_instance():
    custom = SamlReplayCache("", fail_closed=True)
    set_replay_cache(custom)
    assert saml_replay.get_replay_cache() is custom


def test_set_replay_cache_none_forces_rebuild(monkeypatch):
    set_replay_cache(SamlReplayCache("", fail_closed=True))
    built = {"n": 0}

    def fake_from_env(cls):
        built["n"] += 1
        return cls("", fail_closed=True)

    monkeypatch.setattr(SamlReplayCache, "from_env", classmethod(fake_from_env))
    set_replay_cache(None)
    saml_replay.get_replay_cache()
    assert built["n"] == 1


def test_get_replay_cache_singleton_construction_is_race_free(monkeypatch):
    """20 threads racing get_replay_cache() from a cold (None) singleton must
    construct exactly one instance -- proves the double-checked lock, not
    just the happy path (mrj4001 review, PR #781 thread r3966568501)."""
    call_count = {"n": 0}
    count_lock = threading.Lock()

    def counting_from_env(cls):
        with count_lock:
            call_count["n"] += 1
        time.sleep(0.02)  # widen the race window
        return cls("", fail_closed=True)

    monkeypatch.setattr(SamlReplayCache, "from_env", classmethod(counting_from_env))

    barrier = threading.Barrier(20)
    results: list[SamlReplayCache] = []
    results_lock = threading.Lock()

    def worker():
        barrier.wait()
        cache = saml_replay.get_replay_cache()
        with results_lock:
            results.append(cache)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert call_count["n"] == 1
    assert len(results) == 20
    assert len({id(c) for c in results}) == 1


# --- cross-instance / cross-pod protection (D9 #2 #13, T11.4) -------------------

def test_two_cache_instances_sharing_fakeserver_second_rejects_first():
    """The cross-pod promise: two independently constructed caches sharing
    one Valkey (fakeredis.FakeServer) must agree on first presentation."""
    fakeredis = pytest.importorskip("fakeredis")
    server = fakeredis.FakeServer()
    cache1 = SamlReplayCache(
        "redis://fake", fail_closed=True, client=fakeredis.FakeStrictRedis(server=server)
    )
    cache2 = SamlReplayCache(
        "redis://fake", fail_closed=True, client=fakeredis.FakeStrictRedis(server=server)
    )
    assert cache1.check_and_record("id-cross-pod", 60) is True
    assert cache2.check_and_record("id-cross-pod", 60) is False


def test_concurrent_check_and_record_same_id_exactly_one_true():
    """Two threads racing the same ID against the same backing store: SET NX
    EX is the security boundary, so exactly one must win even under real
    concurrency, not just sequential calls."""
    fakeredis = pytest.importorskip("fakeredis")
    server = fakeredis.FakeServer()
    cache1 = SamlReplayCache(
        "redis://fake", fail_closed=True, client=fakeredis.FakeStrictRedis(server=server)
    )
    cache2 = SamlReplayCache(
        "redis://fake", fail_closed=True, client=fakeredis.FakeStrictRedis(server=server)
    )
    barrier = threading.Barrier(2)
    results = []
    results_lock = threading.Lock()

    def worker(cache):
        barrier.wait()
        result = cache.check_and_record("id-race", 60)
        with results_lock:
            results.append(result)

    t1 = threading.Thread(target=worker, args=(cache1,))
    t2 = threading.Thread(target=worker, args=(cache2,))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert sorted(results) == [False, True]


# --- assertion_ids / replay_ttl -----------------------------------------------

class _Boundary:
    def __init__(self, not_on_or_after):
        self.not_on_or_after = not_on_or_after


class _SubjectConfirmation:
    def __init__(self, not_on_or_after):
        self.subject_confirmation_data = _Boundary(not_on_or_after)


class _Subject:
    def __init__(self, confirmations):
        self.subject_confirmation = confirmations


class _Assertion:
    """Minimal stand-in for a parsed pysaml2 Assertion, with optional
    Conditions / SubjectConfirmationData NotOnOrAfter boundaries."""

    def __init__(self, aid, conditions_noa=None, scd_noas=None):
        self.id = aid
        self.issuer = SimpleNamespace(text="https://idp.test.local")
        self.conditions = _Boundary(conditions_noa) if conditions_noa is not None else None
        self.subject = _Subject([_SubjectConfirmation(n) for n in (scd_noas or [])])


class _AuthnResponse:
    """Minimal stand-in for a parsed pysaml2 AuthnResponse. Entries may be
    plain ids (wrapped with no boundaries) or ready-made _Assertion objects
    (for tests that need to control the Conditions/SCD boundaries)."""

    def __init__(self, entries, identity=None, not_on_or_after=0):
        self.assertions = [
            e if isinstance(e, _Assertion) else _Assertion(e) for e in entries
        ]
        self.assertion = self.assertions[-1] if self.assertions else None
        self.not_on_or_after = not_on_or_after
        self._identity = identity or {}
        # The outer <Response> and the SP's ACS URLs, read by the ACS
        # Destination check; no Destination is allowed (#672).
        self.response = SimpleNamespace(destination=None)
        self.return_addrs = []

    def get_identity(self):
        return self._identity


def test_assertion_ids_collects_string_ids():
    resp = _AuthnResponse(["id-a", "id-b"])
    assert assertion_ids(resp) == ["id-a", "id-b"]


def test_assertion_ids_falls_back_to_single_assertion():
    resp = _AuthnResponse(["id-c"])
    resp.assertions = []
    assert assertion_ids(resp) == ["id-c"]


def test_assertion_ids_ignores_non_string_ids():
    """MagicMock-based stubs (the rest of the SAML suite) yield no IDs, so the
    route's replay gate skips them instead of tripping on repr collisions."""
    assert assertion_ids(MagicMock()) == []


def test_assertion_ids_drops_empty_and_whitespace_ids():
    resp = _AuthnResponse(["", "   ", "id-ok"])
    assert assertion_ids(resp) == ["id-ok"]


def test_assertion_ids_deduplicates():
    resp = _AuthnResponse(["dup", "dup", "other"])
    assert assertion_ids(resp) == ["dup", "other"]


def test_replay_ttl_covers_validity_window_plus_skew():
    now = 1_000_000.0
    resp = _AuthnResponse([], not_on_or_after=now + 600)
    assert replay_ttl(resp, now=now) == 600 + _CLOCK_SKEW


def test_replay_ttl_defaults_without_not_on_or_after():
    assert replay_ttl(_AuthnResponse([])) == _DEFAULT_TTL
    assert replay_ttl(MagicMock()) == _DEFAULT_TTL


def test_replay_ttl_floors_at_skew_for_stale_windows():
    now = 1_000_000.0
    resp = _AuthnResponse([], not_on_or_after=now - 50)
    assert replay_ttl(resp, now=now) == _CLOCK_SKEW


def test_replay_ttl_caps_at_max_ttl():
    now = 1_000_000.0
    resp = _AuthnResponse([], not_on_or_after=now + _MAX_TTL * 10)
    assert replay_ttl(resp, now=now) == _MAX_TTL


def test_replay_ttl_derives_from_subject_confirmation_data_only():
    """SCD-only response (response-level not_on_or_after absent): TTL still
    comes from the assertion's SubjectConfirmationData boundary."""
    now = 1_000_000.0
    assertion = _Assertion("id-1", scd_noas=[_iso(now + 500)])
    resp = _AuthnResponse([assertion], not_on_or_after=0)
    assert replay_ttl(resp, now=now) == 500 + _CLOCK_SKEW


def test_replay_ttl_max_wins_across_two_boundaries():
    now = 1_000_000.0
    lower = _Assertion("id-1", conditions_noa=_iso(now + 100))
    higher = _Assertion("id-2", scd_noas=[_iso(now + 900)])
    resp = _AuthnResponse([lower, higher], not_on_or_after=0)
    assert replay_ttl(resp, now=now) == 900 + _CLOCK_SKEW


def test_replay_ttl_garbage_conditions_string_is_skipped_with_warning(caplog):
    now = 1_000_000.0
    assertion = _Assertion("id-1", conditions_noa="not-a-date")
    resp = _AuthnResponse([assertion], not_on_or_after=0)
    with caplog.at_level(logging.WARNING, logger="app.saml_replay"):
        ttl = replay_ttl(resp, now=now)
    assert ttl == _DEFAULT_TTL
    assert any("unparsable" in r.getMessage() for r in caplog.records)


# --- integration through the ACS route ----------------------------------------

_IDENTITY = {
    "urn:oid:0.9.2342.19200300.100.1.3": ["testuser@med.cornell.edu"],
    "urn:oid:2.16.840.1.113730.3.1.241": ["Test User"],
    "urn:oid:1.3.6.1.4.1.5923.1.1.1.6": ["testuser@med.cornell.edu"],  # ePPN -> cwid anchor
}


@pytest.fixture
def replay_cache():
    """Install a local-dict replay cache as the process singleton for a test,
    via the constructor + set_replay_cache injection hook -- not by
    monkeypatching private module/instance state (mrj4001 review, PR #781
    threads r3966565625 / r3966568501)."""
    cache = SamlReplayCache("", fail_closed=True)
    set_replay_cache(cache)
    return cache


def _mock_client(response):
    mock_client = MagicMock()
    mock_client.metadata.shibmd_scopes.return_value = WCM_IDP_SCOPES
    mock_client.parse_authn_request_response.return_value = response
    return mock_client


def _post_acs(client):
    return client.post(
        "/api/saml/acs",
        data={"SAMLResponse": "base64data", "RelayState": "/"},
        follow_redirects=False,
    )


class TestAcsReplayGate:
    @patch("app.api.saml_routes.get_saml_client")
    def test_replayed_assertion_rejected_with_security_log(
        self, mock_get_client, client, db, seed_saml_mode, replay_cache, caplog
    ):
        resp = _AuthnResponse(
            ["_replay-me"], identity=_IDENTITY,
            not_on_or_after=time.time() + 300,
        )
        mock_get_client.return_value = _mock_client(resp)

        first = _post_acs(client)
        assert first.status_code == 302
        assert any(c.name for c in first.cookies.jar)

        with caplog.at_level(logging.WARNING):
            second = _post_acs(client)
        # Same shape as every other ACS failure: redirect to the login page,
        # no session issued.
        assert second.status_code == 302
        assert second.headers["location"] == "/login?error=auth_failed"
        # T11.3 / D9 #22: the security invariant is "replayed assertion must
        # not authenticate" -- assert the negative directly, not just the
        # redirect shape.
        assert COOKIE_NAME not in {c.name for c in second.cookies.jar}
        security_logs = [r for r in caplog.records if "[SECURITY]" in r.getMessage()]
        assert security_logs, "expected [SECURITY] log for the replayed assertion"

    @patch("app.api.saml_routes.get_saml_client")
    def test_fresh_assertions_each_accepted(
        self, mock_get_client, client, db, seed_saml_mode, replay_cache
    ):
        for aid in ("_fresh-1", "_fresh-2"):
            resp = _AuthnResponse([aid], identity=_IDENTITY)
            mock_get_client.return_value = _mock_client(resp)
            assert _post_acs(client).status_code == 302

    @pytest.mark.parametrize("fail_closed_opted_out", [False, True])
    @patch("app.api.saml_routes.get_saml_client")
    def test_multiple_assertion_ids_always_rejected_without_partial_recording(
        self, mock_get_client, fail_closed_opted_out, client, db, seed_saml_mode,
        replay_cache, monkeypatch,
    ):
        """D9 #1/#19: a response naming more than one assertion ID must be
        rejected outright regardless of CVICHE_SAML_REPLAY_FAIL_CLOSED --
        parametrized over the default fail-closed posture AND the explicit
        local-dev opt-out, so the `len(ids) > 1` branch is pinned as
        ignoring the flag entirely, not merely as agreeing with it by
        coincidence (mrj4001 review, PR #781 final-polish item 1: mutating
        that branch to `if len(ids) > 1 and replay_fail_closed():` survived
        the whole suite before this parametrization, because only the
        fail-closed arm was ever exercised here). NEITHER id is recorded on
        rejection, in either arm -- proven by a follow-up single-ID
        presentation of one of them still succeeding. Mutants: M2 (dropping
        the `len(ids) > 1` reject entirely) and the flag-leak mutant above
        both die on this test."""
        if fail_closed_opted_out:
            monkeypatch.setattr("app.api.saml_routes.replay_fail_closed", lambda: False)

        resp = _AuthnResponse(["_multi-a", "_multi-b"], identity=_IDENTITY)
        mock_get_client.return_value = _mock_client(resp)

        response = _post_acs(client)
        assert response.status_code == 302
        assert response.headers["location"] == "/login?error=auth_failed"
        assert COOKIE_NAME not in {c.name for c in response.cookies.jar}
        assert replay_cache._local == {}, "neither ID may be recorded on rejection"

        follow_up_resp = _AuthnResponse(["_multi-a"], identity=_IDENTITY)
        mock_get_client.return_value = _mock_client(follow_up_resp)
        follow_up = _post_acs(client)
        assert follow_up.status_code == 302
        assert "error=" not in follow_up.headers["location"]

    @patch("app.api.saml_routes.get_saml_client")
    def test_stub_without_ids_fails_closed_by_default(
        self, mock_get_client, client, db, seed_saml_mode, replay_cache, monkeypatch
    ):
        """Responses carrying no extractable assertion ID cannot have replay
        verified, so the default (fail closed, CVICHE_SAML_REPLAY_FAIL_CLOSED
        unset) rejects the login rather than skipping the gate."""
        monkeypatch.delenv("CVICHE_SAML_REPLAY_FAIL_CLOSED", raising=False)
        mock_response = MagicMock()
        mock_response.get_identity.return_value = _IDENTITY
        mock_response.response.destination = None  # absent Destination is allowed (#672)
        mock_get_client.return_value = _mock_client(mock_response)
        response = _post_acs(client)
        assert response.status_code == 302
        assert response.headers["location"] == "/login?error=auth_failed"
        assert replay_cache._local == {}

    @patch("app.api.saml_routes.replay_fail_closed", return_value=False)
    @patch("app.api.saml_routes.get_saml_client")
    def test_stub_without_ids_fails_open_when_opted_out(
        self, mock_get_client, _fail_closed, client, db, seed_saml_mode, replay_cache
    ):
        """Explicit local-dev opt-out: no ID still skips the gate (fail open)
        -- this keeps the mocked SAML suite and exotic parsers working."""
        mock_response = MagicMock()
        mock_response.get_identity.return_value = _IDENTITY
        mock_response.response.destination = None  # absent Destination is allowed (#672)
        mock_response.assertion = SimpleNamespace(id=None, issuer=SimpleNamespace(text="https://idp.test.local"))  # issuer, no ID (#1452)
        mock_get_client.return_value = _mock_client(mock_response)
        assert _post_acs(client).status_code == 302
        assert _post_acs(client).status_code == 302
        assert replay_cache._local == {}

    @patch("app.api.saml_routes.get_saml_client")
    def test_store_unreachable_with_flag_unset_rejects_login(
        self, mock_get_client, client, db, seed_saml_mode, monkeypatch, caplog
    ):
        """Flag unset (default) + Valkey unreachable -> fail closed, reject
        the login rather than let it through."""
        monkeypatch.delenv("CVICHE_SAML_REPLAY_FAIL_CLOSED", raising=False)
        broken_client = MagicMock()
        broken_client.set.side_effect = redis.exceptions.ConnectionError("valkey down")
        cache = SamlReplayCache("redis://fake", fail_closed=True, client=broken_client)
        set_replay_cache(cache)

        resp = _AuthnResponse(
            ["_store-down"], identity=_IDENTITY, not_on_or_after=time.time() + 300
        )
        mock_get_client.return_value = _mock_client(resp)

        with caplog.at_level(logging.ERROR):
            response = _post_acs(client)
        assert response.status_code == 302
        assert response.headers["location"] == "/login?error=auth_failed"
        assert not any(c.name for c in response.cookies.jar)

    @patch("app.api.saml_routes.get_saml_client")
    def test_replay_cache_bug_propagates_as_500_not_redirected(
        self, mock_get_client, client, db, seed_saml_mode
    ):
        """A programming error inside check_and_record (anything other than
        a RedisError) must surface as a sanitized 500 with NO cookie minted
        -- it must NOT be caught by D10's parsing-boundary catch-all in
        _parse_saml_assertion. Before PR #781 fix-round item 1, the
        catch-all's try enclosed _reject_replayed_assertion too, so this
        TypeError was silently redirected to /login?error=auth_failed
        instead of propagating -- directly contradicting D2's whole point
        (mrj4001 review threads r3966551686 / r3966560287). This test was
        RED against that code (302, cookie-free but wrongly redirected
        rather than 500) before the _parse_saml_assertion restructuring."""
        resp = _AuthnResponse(
            ["_bug-id"], identity=_IDENTITY, not_on_or_after=time.time() + 300
        )
        mock_get_client.return_value = _mock_client(resp)

        broken_cache = SamlReplayCache("", fail_closed=True)
        broken_cache.check_and_record = MagicMock(
            side_effect=TypeError("programming error inside check_and_record")
        )
        set_replay_cache(broken_cache)

        response = _post_acs(client)
        assert response.status_code == 500
        assert response.json()["error"] == "internal_error"
        assert not any(c.name for c in response.cookies.jar)


class TestReplayTtlOnRedisKey:
    """D9 #3: prove the TTL replay_ttl() computes is the TTL actually placed
    on the Redis key, not merely that replay_ttl() and Redis expiry are each
    correct in isolation."""

    @patch("app.api.saml_routes.get_saml_client")
    def test_route_level_ttl_matches_computed_ttl(
        self, mock_get_client, client, db, seed_saml_mode
    ):
        fakeredis = pytest.importorskip("fakeredis")
        fake_client = fakeredis.FakeStrictRedis()
        cache = SamlReplayCache("redis://fake", fail_closed=True, client=fake_client)
        set_replay_cache(cache)

        noa = time.time() + 500
        assertion = _Assertion("_ttl-check", conditions_noa=_iso(noa))
        resp = _AuthnResponse([assertion], identity=_IDENTITY, not_on_or_after=noa)
        expected_ttl = replay_ttl(resp)
        mock_get_client.return_value = _mock_client(resp)

        response = _post_acs(client)
        assert response.status_code == 302

        key = saml_replay._redis_key("_ttl-check")
        ttl_on_key = fake_client.ttl(key)
        assert ttl_on_key > 0
        assert abs(ttl_on_key - expected_ttl) <= 2
