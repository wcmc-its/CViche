"""Structured authentication audit events (#373).

Mirrors the ``consent_given`` precedent in app/api/consent_routes.py: every
security-relevant transition in the auth module emits a structured
``logger.info(EVENT_NAME, extra={...})`` call, in addition to (never in
place of) any pre-existing free-text logger.warning/error call at the same
site.

Covers: LOGIN_SUCCESS, LOGIN_FAILED, SESSION_EXPIRED, SESSION_REVOKED,
ROLE_CHANGED, GROUP_MEMBERSHIP_REMOVED, DIRECTORY_UNAVAILABLE.
"""
import json
import logging
import os
from unittest.mock import MagicMock, patch

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

from app.auth import COOKIE_NAME, create_session_cookie, get_cookie_settings, get_cookie_delete_settings
from app.ed_group_lookup import EdUnavailableError, MembershipResult, clear_cache
from app.models import SystemConfig, User
from itsdangerous import URLSafeTimedSerializer


def _make_user(db, email="user@example.com", role="user", cwid=None, auth_method="simple",
                status="active"):
    user = User(
        email=email, display_name="User", role=role, status=status,
        cwid=cwid, auth_method=auth_method,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _set_epoch(db, n):
    row = db.query(SystemConfig).filter(SystemConfig.key == "session_epoch").first()
    if row:
        row.value = json.dumps(n)
    else:
        db.add(SystemConfig(key="session_epoch", value=json.dumps(n)))
    db.commit()


def _events_named(caplog, name):
    """All caplog records whose message is exactly `name` (the event)."""
    return [r for r in caplog.records if r.getMessage() == name]


def _mock_saml_client(identity_dict):
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.get_identity.return_value = identity_dict
    mock_client.parse_authn_request_response.return_value = mock_response
    return mock_client


_SAML_IDENTITY = {
    "urn:oid:0.9.2342.19200300.100.1.3": ["samluser@med.cornell.edu"],
    "urn:oid:2.16.840.1.113730.3.1.241": ["SAML User"],
    "urn:oid:1.3.6.1.4.1.5923.1.1.1.6": ["samluser@cornell.edu"],
}

_ED_ENV = {
    "ED_LDAP_URL": "ldaps://ed.weill.cornell.edu:636",
    "ED_LDAP_BIND_DN": "cn=svc-cviche,ou=ServiceAccounts,dc=weill,dc=cornell,dc=edu",
    "ED_LDAP_BIND_PASSWORD": "test-password",
}


# ---------------------------------------------------------------------------
# LOGIN_SUCCESS / LOGIN_FAILED -- simple mode (auth_routes.py)
# ---------------------------------------------------------------------------

def test_login_success_event_on_simple_login(client, seed_simple_mode, caplog):
    with caplog.at_level(logging.INFO, logger="app.api.auth_routes"):
        resp = client.post(
            "/api/auth/login",
            json={"email": "test@example.com", "display_name": "Test"},
        )
    assert resp.status_code == 200

    events = _events_named(caplog, "LOGIN_SUCCESS")
    assert len(events) == 1
    assert events[0].email == "test@example.com"
    assert events[0].auth_method == "simple"
    assert events[0].role == "user"
    assert events[0].user_id == resp.json()["user_id"]


def test_login_failed_event_not_allowlisted(client, seed_simple_mode, caplog):
    with caplog.at_level(logging.INFO, logger="app.api.auth_routes"):
        resp = client.post(
            "/api/auth/login",
            json={"email": "stranger@example.com", "display_name": "Stranger"},
        )
    assert resp.status_code == 403

    events = _events_named(caplog, "LOGIN_FAILED")
    assert len(events) == 1
    assert events[0].email == "stranger@example.com"
    assert events[0].reason == "not_allowlisted"

    # The pre-existing free-text warning must still fire alongside the new
    # structured event, not be replaced by it.
    assert any(
        "Login rejected for unrecognised email" in r.getMessage()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# SESSION_REVOKED -- explicit logout (auth_routes.py) and epoch bump (auth.py)
# ---------------------------------------------------------------------------

def test_session_revoked_event_on_logout(client, db, seed_simple_mode, caplog):
    user = _make_user(db, email="logout@example.com")
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, epoch=0))

    with caplog.at_level(logging.INFO, logger="app.api.auth_routes"):
        resp = client.post("/api/auth/logout")
    assert resp.status_code == 200

    events = _events_named(caplog, "SESSION_REVOKED")
    assert len(events) == 1
    assert events[0].reason == "user_logout"
    assert events[0].user_id == user.id


def test_session_revoked_event_on_epoch_mismatch(client, db, seed_simple_mode, caplog):
    user = _make_user(db)
    _set_epoch(db, 0)
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, epoch=0))
    assert client.get("/api/auth/me").status_code == 200  # valid before the bump

    _set_epoch(db, 1)  # an admin revoked everyone
    with caplog.at_level(logging.INFO, logger="app.auth"):
        resp = client.get("/api/auth/me")
    assert resp.status_code == 401

    events = _events_named(caplog, "SESSION_REVOKED")
    assert len(events) == 1
    assert events[0].reason == "epoch_mismatch"
    assert events[0].user_id == user.id


# ---------------------------------------------------------------------------
# SESSION_EXPIRED -- invalid/expired cookie and idle timeout (auth.py)
# ---------------------------------------------------------------------------

def test_session_expired_event_on_invalid_cookie(client, seed_simple_mode, caplog):
    client.cookies.set(COOKIE_NAME, "not-a-valid-signed-cookie")

    with caplog.at_level(logging.INFO, logger="app.auth"):
        resp = client.get("/api/auth/me")
    assert resp.status_code == 401

    events = _events_named(caplog, "SESSION_EXPIRED")
    assert len(events) == 1
    assert events[0].reason == "invalid_or_expired_cookie"


def test_session_expired_event_on_idle_timeout(client, db, seed_simple_mode, caplog):
    user = _make_user(db)
    _set_epoch(db, 0)
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, epoch=0))

    fake_store = MagicMock()
    fake_store.touch.return_value = False  # sid present but idle window lapsed
    # The cookie is rich (no store configured in tests), so identity comes from
    # the payload; the store has no record for this sid.
    fake_store.resolve.return_value = None

    with patch("app.auth.get_idle_store", return_value=fake_store):
        with caplog.at_level(logging.INFO, logger="app.auth"):
            resp = client.get("/api/auth/me")
    assert resp.status_code == 401

    events = _events_named(caplog, "SESSION_EXPIRED")
    assert len(events) == 1
    assert events[0].reason == "idle_timeout"
    assert events[0].user_id == user.id


# ---------------------------------------------------------------------------
# ROLE_CHANGED / GROUP_MEMBERSHIP_REMOVED / DIRECTORY_UNAVAILABLE
# -- per-request ED re-check in get_current_user (auth.py)
# ---------------------------------------------------------------------------

@patch.dict(os.environ, _ED_ENV)
@patch("app.auth.check_ed_membership")
def test_role_changed_event_on_ed_recheck(mock_check_ed, client, db, seed_ed_enabled, caplog):
    """Per-request ED re-check promotes a user -> ROLE_CHANGED fires."""
    clear_cache()
    user = _make_user(db, email="ed-role@example.com", role="user",
                       cwid="edrole1", auth_method="saml")
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, epoch=0))

    mock_check_ed.return_value = MembershipResult(in_access_group=True, in_admin_group=True)

    with caplog.at_level(logging.INFO, logger="app.auth"):
        resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["role"] == "admin"

    events = _events_named(caplog, "ROLE_CHANGED")
    assert len(events) == 1
    assert events[0].user_id == user.id
    assert events[0].old_role == "user"
    assert events[0].new_role == "admin"


@patch.dict(os.environ, _ED_ENV)
@patch("app.auth.check_ed_membership")
def test_group_membership_removed_event(mock_check_ed, client, db, seed_ed_enabled, caplog):
    """Per-request ED re-check finds the user no longer in the access group."""
    clear_cache()
    user = _make_user(db, email="ed-removed@example.com", role="user",
                       cwid="edremoved1", auth_method="saml")
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, epoch=0))

    mock_check_ed.return_value = MembershipResult(in_access_group=False, in_admin_group=False)

    with caplog.at_level(logging.INFO, logger="app.auth"):
        resp = client.get("/api/auth/me")
    assert resp.status_code == 401

    events = _events_named(caplog, "GROUP_MEMBERSHIP_REMOVED")
    assert len(events) == 1
    assert events[0].user_id == user.id
    assert events[0].cwid == "edremoved1"


@patch.dict(os.environ, _ED_ENV)
@patch("app.auth.check_ed_membership")
def test_directory_unavailable_event(mock_check_ed, client, db, seed_ed_enabled, caplog):
    """ED unreachable and no stale cache -> hard failure, DIRECTORY_UNAVAILABLE fires."""
    clear_cache()
    user = _make_user(db, email="ed-unavailable@example.com", role="user",
                       cwid="edgone1", auth_method="saml")
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, epoch=0))

    mock_check_ed.side_effect = EdUnavailableError("LDAP unreachable")

    with caplog.at_level(logging.INFO, logger="app.auth"):
        resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "directory_unavailable"

    events = _events_named(caplog, "DIRECTORY_UNAVAILABLE")
    assert len(events) == 1
    assert events[0].cwid == "edgone1"


def test_directory_unavailable_event_when_access_group_unconfigured(
    client, db, seed_ed_enabled, caplog
):
    """No ED access group configured -> ValueError branch, DIRECTORY_UNAVAILABLE fires."""
    clear_cache()
    row = db.query(SystemConfig).filter(SystemConfig.key == "ed_access_group").first()
    row.value = json.dumps("")
    db.commit()
    user = _make_user(db, email="ed-unconfigured@example.com", role="user",
                       cwid="ednogroup1", auth_method="saml")
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, epoch=0))

    with caplog.at_level(logging.INFO, logger="app.auth"):
        resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "directory_unavailable"

    events = _events_named(caplog, "DIRECTORY_UNAVAILABLE")
    assert len(events) == 1
    assert events[0].cwid == "ednogroup1"


# ---------------------------------------------------------------------------
# LOGIN_SUCCESS / LOGIN_FAILED -- SAML ACS handler (saml_routes.py)
# ---------------------------------------------------------------------------

@patch("app.api.saml_routes.get_saml_client")
def test_login_success_event_on_saml_acs(mock_get_client, client, db, seed_saml_mode, caplog):
    mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)

    with caplog.at_level(logging.INFO, logger="app.api.saml_routes"):
        resp = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/"},
            follow_redirects=False,
        )
    assert resp.status_code == 302

    events = _events_named(caplog, "LOGIN_SUCCESS")
    assert len(events) == 1
    assert events[0].auth_method == "saml"
    assert events[0].email == "samluser@med.cornell.edu"


@patch.dict(os.environ, _ED_ENV)
@patch("app.api.saml_routes.check_ed_membership")
@patch("app.api.saml_routes.get_saml_client")
def test_login_failed_event_saml_not_authorized(
    mock_get_client, mock_check_ed, client, db, seed_ed_enabled, caplog
):
    clear_cache()
    mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
    mock_check_ed.return_value = MembershipResult(in_access_group=False, in_admin_group=False)

    with caplog.at_level(logging.INFO, logger="app.api.saml_routes"):
        resp = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/"},
            follow_redirects=False,
        )
    assert resp.status_code == 302
    assert "error=not_authorized" in resp.headers["location"]

    events = _events_named(caplog, "LOGIN_FAILED")
    assert len(events) == 1
    assert events[0].reason == "not_authorized"
    # cwid derived from the eppn local-part (no explicit uid attr released)
    assert events[0].cwid == "samluser"

    # The pre-existing free-text warning must still fire alongside it.
    assert any("not in ED access group" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# Review-response coverage: cookie path, malformed payload, role downgrade,
# disabled user, PII-in-logs (PR #656 review, 2026-08-19)
# ---------------------------------------------------------------------------

def test_cookie_settings_security_attributes():
    """Regression coverage for the cookie's security-relevant attributes,
    not just code inspection: HttpOnly, SameSite, Max-Age, and Path -- the
    last of which #656's review caught missing from get_cookie_settings()."""
    settings = get_cookie_settings()
    assert settings["httponly"] is True
    assert settings["samesite"] == "lax"
    assert settings["max_age"] > 0
    # The cookie is minted from both /api/auth/login and /api/saml/acs;
    # without a matching explicit path, a browser can refuse to send a
    # SAML-created cookie to /api/auth/me, or refuse the logout deletion.
    assert settings["path"] == get_cookie_delete_settings()["path"] == "/"


def test_get_current_user_rejects_malformed_payload_not_500(client, seed_simple_mode, caplog):
    """A validly-signed cookie missing the fields get_current_user requires
    must fail closed with 401, not raise KeyError/ValueError into a 500."""
    forged = URLSafeTimedSerializer("test-secret-not-for-production").dumps({"sid": "x"})
    client.cookies.set(COOKIE_NAME, forged)

    with caplog.at_level(logging.INFO, logger="app.auth"):
        resp = client.get("/api/auth/me")
    assert resp.status_code == 401

    events = _events_named(caplog, "SESSION_EXPIRED")
    assert any(e.reason == "malformed_payload" for e in events)


def test_get_current_user_rejects_non_numeric_epoch_not_500(client, seed_simple_mode, caplog):
    forged = URLSafeTimedSerializer("test-secret-not-for-production").dumps(
        {"user_id": 1, "epoch": "not-a-number"}
    )
    client.cookies.set(COOKIE_NAME, forged)

    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


@patch.dict(os.environ, _ED_ENV)
@patch("app.auth.check_ed_membership")
def test_role_changed_event_on_ed_downgrade(mock_check_ed, client, db, seed_ed_enabled, caplog):
    """Per-request ED re-check demotes a user -> ROLE_CHANGED fires (not just
    the promotion direction)."""
    clear_cache()
    user = _make_user(db, email="ed-downgrade@example.com", role="admin",
                       cwid="eddowngrade1", auth_method="saml")
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, epoch=0))

    mock_check_ed.return_value = MembershipResult(in_access_group=True, in_admin_group=False)

    with caplog.at_level(logging.INFO, logger="app.auth"):
        resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    assert resp.json()["role"] == "user"

    events = _events_named(caplog, "ROLE_CHANGED")
    assert len(events) == 1
    assert events[0].old_role == "admin"
    assert events[0].new_role == "user"


def test_disabled_user_rejected(client, db, seed_simple_mode):
    user = _make_user(db, email="disabled@example.com", status="disabled")
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, epoch=0))

    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "account_disabled"


def test_login_does_not_log_the_session_secret_or_cookie_value(client, seed_simple_mode, caplog):
    """Data-minimization spot check: whatever a login logs, it must not be
    the signed cookie value or the signing secret itself."""
    with caplog.at_level(logging.INFO):
        resp = client.post(
            "/api/auth/login",
            json={"email": "test@example.com", "display_name": "Test"},
        )
    assert resp.status_code == 200
    cookie_value = resp.cookies.get(COOKIE_NAME)
    assert cookie_value

    log_text = "\n".join(r.getMessage() for r in caplog.records)
    assert cookie_value not in log_text
    assert "test-secret-not-for-production" not in log_text
