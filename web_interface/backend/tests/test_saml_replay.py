"""SAML assertion replay protection (app/saml_replay.py + ACS wiring).

Contract:
  - ``allow_unsolicited=True`` stays (IdP-initiated SSO must keep working), so
    InResponseTo is never matched. Instead every successfully parsed assertion
    ID is recorded once, with a TTL covering the assertion validity window.
  - A second presentation of the same assertion ID is rejected at the ACS with
    401 and a [SECURITY] log.
  - Valkey reachable -> cross-pod protection via atomic SET NX EX.
  - Valkey configured but unreachable -> FAIL OPEN (log loudly, allow login).
  - Valkey unconfigured -> in-process dict fallback (per-pod best-effort).

The store tests use fakeredis for real SET NX EX semantics; the ACS tests
drive the route through the TestClient with stubbed pysaml2 responses.
"""
import logging
import time
from unittest.mock import MagicMock, patch

import pytest

import app.saml_replay as saml_replay
from app.saml_replay import (
    SamlReplayCache,
    assertion_ids,
    replay_ttl,
    _DEFAULT_TTL,
    _CLOCK_SKEW,
)


def _fake_cache():
    fakeredis = pytest.importorskip("fakeredis")
    cache = SamlReplayCache("redis://fake")  # truthy url -> redis path
    cache._client = fakeredis.FakeStrictRedis()
    return cache


# --- cache unit tests ---------------------------------------------------------

def test_redis_first_presentation_accepted_then_replay_rejected():
    cache = _fake_cache()
    assert cache.check_and_record(["id-1"], 60) is True
    assert cache.check_and_record(["id-1"], 60) is False


def test_redis_distinct_ids_are_independent():
    cache = _fake_cache()
    assert cache.check_and_record(["id-1"], 60) is True
    assert cache.check_and_record(["id-2"], 60) is True


def test_redis_id_expires_after_ttl():
    cache = _fake_cache()
    assert cache.check_and_record(["id-1"], 1) is True
    time.sleep(1.1)
    # Beyond the TTL pysaml2 itself rejects the assertion, so re-acceptance
    # of the ID here is safe.
    assert cache.check_and_record(["id-1"], 1) is True


def test_redis_partial_replay_rejected():
    """A response mixing one fresh and one already-seen assertion is a replay."""
    cache = _fake_cache()
    assert cache.check_and_record(["id-1"], 60) is True
    assert cache.check_and_record(["id-1", "id-2"], 60) is False


def test_fails_open_when_redis_errors(caplog):
    cache = SamlReplayCache("redis://fake")
    client = MagicMock()
    client.set.side_effect = ConnectionError("valkey down")
    cache._client = client
    with caplog.at_level(logging.ERROR, logger="app.saml_replay"):
        assert cache.check_and_record(["id-1"], 60) is True
        assert cache.check_and_record(["id-1"], 60) is True  # still open
    security_logs = [r for r in caplog.records if "[SECURITY]" in r.getMessage()]
    assert security_logs, "expected a loud [SECURITY] log on fail-open"


def test_fails_closed_when_configured(monkeypatch, caplog):
    monkeypatch.setattr(saml_replay, "replay_fail_closed", lambda: True)
    cache = SamlReplayCache("redis://fake")
    client = MagicMock()
    client.set.side_effect = ConnectionError("valkey down")
    cache._client = client
    with caplog.at_level(logging.ERROR, logger="app.saml_replay"):
        assert cache.check_and_record(["id-1"], 60) is False  # reject, not open
    assert any("FAILING CLOSED" in r.getMessage() for r in caplog.records)


def test_redis_key_is_hashed_not_raw():
    """The IdP-supplied assertion ID must never be used raw as a Redis key."""
    cache = _fake_cache()
    weird = "id with spaces / and : colons"
    cache.check_and_record([weird], 60)
    keys = [k.decode() if isinstance(k, bytes) else k
            for k in cache._client.keys("*")]
    assert keys, "expected a key to be written"
    assert all(weird not in k for k in keys), "raw assertion ID leaked into key"
    assert all(len(k.rsplit(":", 1)[-1]) == 64 for k in keys), "expected sha256 hex"


def test_client_is_built_with_socket_timeouts(monkeypatch):
    """A hung Valkey must raise (then fail open) instead of blocking the login."""
    import redis
    captured = {}

    def fake_from_url(url, **kwargs):
        captured.update(kwargs)
        return MagicMock()

    monkeypatch.setattr(redis.Redis, "from_url", staticmethod(fake_from_url))
    cache = SamlReplayCache("redis://fake")
    cache._redis()
    assert captured.get("socket_timeout") == 2
    assert captured.get("socket_connect_timeout") == 2


def test_unconfigured_falls_back_to_local_dict():
    """No CVICHE_REDIS_URL -> per-pod dict still blocks same-pod replays."""
    cache = SamlReplayCache("")
    assert cache.check_and_record(["id-1"], 60) is True
    assert cache.check_and_record(["id-1"], 60) is False
    assert cache.check_and_record(["id-2"], 60) is True


def test_local_dict_purges_expired_entries():
    cache = SamlReplayCache(None)
    assert cache.check_and_record(["id-1"], 60) is True
    cache._local["id-1"] = time.time() - 1          # simulate the TTL lapsing
    assert cache.check_and_record(["id-1"], 60) is True
    assert "id-1" in cache._local                   # re-recorded, old entry purged


def test_empty_id_list_is_a_noop():
    cache = SamlReplayCache("")
    assert cache.check_and_record([], 60) is True
    assert cache._local == {}


# --- assertion_ids / replay_ttl -----------------------------------------------

class _Assertion:
    def __init__(self, aid):
        self.id = aid


class _AuthnResponse:
    """Minimal stand-in for a parsed pysaml2 AuthnResponse."""

    def __init__(self, aids, identity=None, not_on_or_after=0):
        self.assertions = [_Assertion(a) for a in aids]
        self.assertion = self.assertions[-1] if self.assertions else None
        self.not_on_or_after = not_on_or_after
        self._identity = identity or {}

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


# --- integration through the ACS route ----------------------------------------

_IDENTITY = {
    "urn:oid:0.9.2342.19200300.100.1.3": ["testuser@med.cornell.edu"],
    "urn:oid:2.16.840.1.113730.3.1.241": ["Test User"],
    "urn:oid:1.3.6.1.4.1.5923.1.1.1.6": ["testuser@cornell.edu"],  # ePPN -> cwid anchor
}


@pytest.fixture
def replay_cache(monkeypatch):
    """Install a local-dict replay cache as the process singleton for a test."""
    cache = SamlReplayCache("")
    monkeypatch.setattr(saml_replay, "_cache", cache)
    return cache


def _mock_client(response):
    mock_client = MagicMock()
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

    @patch("app.api.saml_routes.get_saml_client")
    def test_stub_without_ids_fails_open(
        self, mock_get_client, client, db, seed_saml_mode, replay_cache
    ):
        """Responses carrying no extractable assertion ID skip the gate (fail
        open) -- this keeps the mocked SAML suite and exotic parsers working."""
        mock_response = MagicMock()
        mock_response.get_identity.return_value = _IDENTITY
        mock_get_client.return_value = _mock_client(mock_response)
        assert _post_acs(client).status_code == 302
        assert _post_acs(client).status_code == 302
        assert replay_cache._local == {}
