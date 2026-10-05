"""Integration tests for dual-mode auth switching (simple vs SAML).

These tests validate that:
- Simple mode login works end-to-end (login -> session -> me -> logout)
- SAML mode login works end-to-end (with mocked pysaml2 client)
- Mode switching correctly enables/disables endpoints
- Config endpoint returns correct mode information

No Docker required -- all SAML interactions use mocked pysaml2.
"""
import json
import os
import pytest
from unittest.mock import patch, MagicMock
from uuid import uuid4

from app.models import User
from app.auth import COOKIE_NAME, create_session_cookie
from app.ed_group_lookup import (
    clear_cache,
    set_cached_membership,
    _group_cache,
    MembershipResult,
    EdUnavailableError,
)


# ---------------------------------------------------------------------------
# Mock helpers (same pattern as test_saml_sp.py / test_ed_group.py)
# ---------------------------------------------------------------------------


def _mock_saml_client(identity_dict=None):
    """Create a mock Saml2Client with pre-configured responses."""
    mock_client = MagicMock()
    mock_client.prepare_for_authenticate.return_value = (
        "req_id",
        {"headers": [("Location", "https://idp.example.com/sso?SAMLRequest=abc123")]},
    )
    if identity_dict is not None:
        mock_response = MagicMock()
        mock_response.get_identity.return_value = identity_dict
        mock_response.response.destination = None  # absent Destination is allowed (#672)
        # A real pysaml2 response always carries an assertion ID; give this
        # stub one too so the replay gate's fail-closed default (a missing
        # ID) doesn't fire on tests that aren't exercising that path.
        assertion = MagicMock()
        assertion.id = f"_{uuid4().hex}"
        mock_response.assertions = [assertion]
        mock_response.assertion = assertion
        mock_client.parse_authn_request_response.return_value = mock_response
    else:
        mock_client.parse_authn_request_response.return_value = None
    mock_client.config.create_metadata_string.return_value = b"<EntityDescriptor>test</EntityDescriptor>"
    return mock_client


_SAML_IDENTITY = {
    "urn:oid:0.9.2342.19200300.100.1.3": ["testuser@med.cornell.edu"],
    "urn:oid:2.16.840.1.113730.3.1.241": ["Test User"],
    "urn:oid:1.3.6.1.4.1.5923.1.1.1.6": ["testuser@med.cornell.edu"],
}

_ED_ENV = {
    "ED_LDAP_URL": "ldaps://ed.weill.cornell.edu:636",
    "ED_LDAP_BIND_DN": "cn=svc-cviche,ou=ServiceAccounts,dc=weill,dc=cornell,dc=edu",
    "ED_LDAP_BIND_PASSWORD": "test-password",
}


# ---------------------------------------------------------------------------
# TestSimpleModeFullCycle
# ---------------------------------------------------------------------------


class TestSimpleModeFullCycle:
    """Validate simple mode login -> session -> me -> logout end-to-end."""

    def test_simple_mode_login_full_cycle(self, client, seed_simple_mode):
        """POST /api/auth/login -> GET /api/auth/me -> POST /api/auth/logout full cycle."""
        # Step 1: Login
        login_resp = client.post(
            "/api/auth/login",
            json={"email": "test@example.com", "display_name": "Test"},
        )
        assert login_resp.status_code == 200
        data = login_resp.json()
        assert data["email"] == "test@example.com"

        # Extract session cookie
        cookie_value = None
        for cookie in login_resp.cookies.jar:
            if cookie.name == COOKIE_NAME:
                cookie_value = cookie.value
                break
        assert cookie_value is not None, "Session cookie not set after login"

        # Step 2: Check /api/auth/me with cookie
        me_resp = client.get("/api/auth/me", cookies={COOKIE_NAME: cookie_value})
        assert me_resp.status_code == 200
        me_data = me_resp.json()
        assert me_data["email"] == "test@example.com"
        assert me_data["display_name"] == "Test"

        # Step 3: Logout
        logout_resp = client.post("/api/auth/logout")
        assert logout_resp.status_code == 200
        assert "Logged out" in logout_resp.json()["message"]

    @pytest.mark.parametrize("flag, expected", [("1", "cv@scholars-mail.weill.cornell.edu"), ("", None)])
    def test_me_exposes_intake_address_only_while_intake_is_on(self, client, db, seed_simple_mode, monkeypatch, flag, expected):
        """#1298: /api/auth/me carries the intake address only while CVICHE_EMAIL_INTAKE is on.

        Mints the cookie directly: a real login spends the shared login rate limit."""
        monkeypatch.setenv("CVICHE_EMAIL_INTAKE", flag)
        user = User(email="test@example.com", display_name="Test", role="user", status="active", consent_version="1.0")
        db.add(user)
        db.commit()
        me_resp = client.get("/api/auth/me", cookies={COOKIE_NAME: create_session_cookie(user, db)})
        assert me_resp.json()["intake_address"] == expected

    def test_simple_mode_config_returns_simple(self, client, seed_simple_mode):
        """GET /api/auth/config returns {"mode": "simple"} with no discovery_url key."""
        response = client.get("/api/auth/config")
        assert response.status_code == 200
        data = response.json()
        assert data == {"mode": "simple"}
        assert "discovery_url" not in data

    def test_simple_mode_rejects_saml_login(self, client, seed_simple_mode):
        """GET /api/saml/login returns 302 redirect to /login?error=saml_not_enabled."""
        response = client.get("/api/saml/login", follow_redirects=False)
        assert response.status_code == 302
        assert "error=saml_not_enabled" in response.headers["location"]

    def test_simple_mode_rejects_unknown_email(self, client, seed_simple_mode):
        """POST /api/auth/login with unknown email returns 403 with error "forbidden"."""
        response = client.post(
            "/api/auth/login",
            json={"email": "unknown@example.com", "display_name": "Unknown"},
        )
        assert response.status_code == 403
        data = response.json()
        assert data["error"] == "forbidden"


# ---------------------------------------------------------------------------
# TestSamlModeFullCycle
# ---------------------------------------------------------------------------


class TestSamlModeFullCycle:
    """Validate SAML mode login cycle with mocked pysaml2 client."""

    @patch("app.api.saml_routes.get_saml_client")
    def test_saml_mode_login_full_cycle(self, mock_get_client, client, db, seed_saml_mode):
        """Patch get_saml_client, POST /api/saml/acs creates user and sets cookie, GET /api/auth/me returns 200."""
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)

        # Step 1: ACS with mock SAML response
        acs_resp = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/"},
            follow_redirects=False,
        )
        assert acs_resp.status_code == 302
        assert acs_resp.headers["location"] == "/"

        # Extract session cookie
        cookie_value = None
        for cookie in acs_resp.cookies.jar:
            if cookie.name == COOKIE_NAME:
                cookie_value = cookie.value
                break
        assert cookie_value is not None, "Session cookie not set after ACS"

        # Step 2: Verify user session
        me_resp = client.get("/api/auth/me", cookies={COOKIE_NAME: cookie_value})
        assert me_resp.status_code == 200
        me_data = me_resp.json()
        assert me_data["email"] == "testuser@med.cornell.edu"

    def test_saml_mode_config_returns_saml(self, client, seed_saml_mode):
        """GET /api/auth/config returns {"mode": "saml", "discovery_url": "..."}."""
        response = client.get("/api/auth/config")
        assert response.status_code == 200
        data = response.json()
        assert data["mode"] == "saml"
        assert data["discovery_url"] == "https://login.weill.cornell.edu/discovery"

    def test_saml_mode_rejects_simple_login(self, client, seed_saml_mode):
        """POST /api/auth/login with email body returns 403 with error "sso_required"."""
        response = client.post(
            "/api/auth/login",
            json={"email": "test@example.com", "display_name": "Test"},
        )
        assert response.status_code == 403
        data = response.json()
        assert data["error"] == "sso_required"

    def test_saml_logout_clears_session(self, client):
        """POST /api/saml/logout returns 302 with cookie cleared."""
        response = client.post("/api/saml/logout", follow_redirects=False)
        assert response.status_code == 302
        assert "/login" in response.headers["location"]
        set_cookie = response.headers.get("set-cookie", "")
        assert COOKIE_NAME in set_cookie


# ---------------------------------------------------------------------------
# TestSamlModeWithED
# ---------------------------------------------------------------------------


class TestSamlModeWithED:
    """Validate ED group authorization with SAML mode."""

    @patch.dict(os.environ, _ED_ENV)
    @patch("app.auth.check_ed_membership")
    @patch("app.api.saml_routes.check_ed_membership")
    @patch("app.api.saml_routes.get_saml_client")
    def test_ed_user_in_access_group_full_cycle(
        self, mock_get_client, mock_check_ed, mock_auth_check_ed, client, db, seed_ed_enabled
    ):
        """Mock ACS with user in access group -> session created -> /api/auth/me succeeds.

        check_ed_membership now owns the cache-aside flow itself, so mocking it
        out at the ACS call site means nothing is cached for the per-request
        re-check -- the auth.py call site is mocked too, with the same answer.
        """
        clear_cache()
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_check_ed.return_value = MembershipResult(in_access_group=True, in_admin_group=False)
        mock_auth_check_ed.return_value = MembershipResult(in_access_group=True, in_admin_group=False)

        # ACS: user in access group
        acs_resp = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/"},
            follow_redirects=False,
        )
        assert acs_resp.status_code == 302
        assert acs_resp.headers["location"] == "/"

        cookie_value = None
        for cookie in acs_resp.cookies.jar:
            if cookie.name == COOKIE_NAME:
                cookie_value = cookie.value
                break
        assert cookie_value is not None

        # Verify session via /api/auth/me (need cached membership for per-request check)
        me_resp = client.get("/api/auth/me", cookies={COOKIE_NAME: cookie_value})
        assert me_resp.status_code == 200
        assert me_resp.json()["email"] == "testuser@med.cornell.edu"

    @patch.dict(os.environ, _ED_ENV)
    @patch("app.api.saml_routes.check_ed_membership")
    @patch("app.api.saml_routes.get_saml_client")
    def test_ed_admin_group_sets_admin_role(
        self, mock_get_client, mock_check_ed, client, db, seed_ed_enabled
    ):
        """Mock ACS with user in both groups -> user.role is "admin"."""
        clear_cache()
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_check_ed.return_value = MembershipResult(in_access_group=True, in_admin_group=True)

        client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/"},
            follow_redirects=False,
        )

        user = db.query(User).filter(User.email == "testuser@med.cornell.edu").first()
        assert user is not None
        assert user.role == "admin"

    @patch.dict(os.environ, _ED_ENV)
    @patch("app.api.saml_routes.check_ed_membership")
    @patch("app.api.saml_routes.get_saml_client")
    def test_ed_denial_at_login(
        self, mock_get_client, mock_check_ed, client, db, seed_ed_enabled
    ):
        """Mock ACS with user NOT in access group -> redirect to /login?error=not_authorized, no user created."""
        clear_cache()
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_check_ed.return_value = MembershipResult(in_access_group=False, in_admin_group=False)

        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error=not_authorized" in response.headers["location"]

        # Verify no user was created
        user = db.query(User).filter(User.email == "testuser@med.cornell.edu").first()
        assert user is None

    @patch.dict(os.environ, _ED_ENV)
    @patch("app.auth.check_ed_membership")
    @patch("app.api.saml_routes.check_ed_membership")
    @patch("app.api.saml_routes.get_saml_client")
    def test_ed_cache_expiry_triggers_recheck(
        self, mock_get_client, mock_acs_check_ed, mock_auth_check_ed, client, db, seed_ed_enabled
    ):
        """Cache expiry triggers ED re-check on /api/auth/me."""
        clear_cache()
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_acs_check_ed.return_value = MembershipResult(in_access_group=True, in_admin_group=False)

        # Step 1: Login via ACS (populates cache)
        acs_resp = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/"},
            follow_redirects=False,
        )
        assert acs_resp.status_code == 302

        cookie_value = None
        for cookie in acs_resp.cookies.jar:
            if cookie.name == COOKIE_NAME:
                cookie_value = cookie.value
                break
        assert cookie_value is not None

        # Step 2: Simulate TTL cache expiry (clear TTL cache but stale remains)
        _group_cache.clear()

        # Step 3: Mock auth.check_ed_membership for the per-request re-check
        mock_auth_check_ed.return_value = MembershipResult(in_access_group=True, in_admin_group=True)

        # Step 4: /api/auth/me triggers re-check
        me_resp = client.get("/api/auth/me", cookies={COOKIE_NAME: cookie_value})
        assert me_resp.status_code == 200

        # Verify the auth module's check_ed_membership was called (re-check happened)
        mock_auth_check_ed.assert_called_once()

        # Verify user was promoted to admin after re-check
        me_data = me_resp.json()
        assert me_data["role"] == "admin"
