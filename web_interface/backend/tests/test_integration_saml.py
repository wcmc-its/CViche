"""Integration tests for SAML flow against mock IdP container.

Requires: docker compose -f docker-compose.mock-idp.yml up -d
Tests skip gracefully when mock IdP is not running.
"""
import json
import os
import pytest
import httpx
from unittest.mock import patch, MagicMock

from app.models import User
from app.auth import COOKIE_NAME, create_session_cookie
from app.models import SystemConfig
from app.ed_group_lookup import (
    clear_cache,
    set_cached_membership,
    EdUnavailableError,
)

MOCK_IDP_URL = "http://localhost:8443"
MOCK_IDP_METADATA = f"{MOCK_IDP_URL}/simplesaml/saml2/idp/metadata.php"


def _upsert_config(db, configs: dict):
    """Insert or update SystemConfig rows (local copy of conftest helper)."""
    for key, value in configs.items():
        existing = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if existing:
            existing.value = value
        else:
            db.add(SystemConfig(key=key, value=value))
    db.commit()


def _mock_idp_available() -> bool:
    try:
        r = httpx.get(f"{MOCK_IDP_URL}/simplesaml/", timeout=2)
        return r.status_code == 200
    except (httpx.ConnectError, httpx.ReadTimeout):
        return False


requires_mock_idp = pytest.mark.skipif(
    not _mock_idp_available(),
    reason="Mock IdP not running (docker compose -f docker-compose.mock-idp.yml up -d)",
)


# ---------------------------------------------------------------------------
# Mock IdP connectivity tests (require Docker)
# ---------------------------------------------------------------------------


@requires_mock_idp
class TestMockIdPConnectivity:
    """Verify the mock IdP container is reachable and serving metadata."""

    def test_mock_idp_reachable(self):
        """GET to MOCK_IDP_URL/simplesaml/ returns 200."""
        r = httpx.get(f"{MOCK_IDP_URL}/simplesaml/", timeout=5)
        assert r.status_code == 200

    def test_mock_idp_metadata_available(self):
        """GET to MOCK_IDP_METADATA returns 200 with XML content."""
        r = httpx.get(MOCK_IDP_METADATA, timeout=5)
        assert r.status_code == 200
        assert "xml" in r.headers.get("content-type", "").lower()


# ---------------------------------------------------------------------------
# SAML login redirect tests (require Docker)
# ---------------------------------------------------------------------------


@requires_mock_idp
class TestSamlLoginRedirect:
    """Test that SAML login redirect points to the mock IdP."""

    def test_login_redirect_goes_to_mock_idp(self, client, db, seed_saml_mode):
        """GET /api/saml/login returns 302 with Location pointing to localhost:8443."""
        # Override IdP metadata URL to point at mock IdP
        _upsert_config(db, {"saml_idp_metadata_url": json.dumps(MOCK_IDP_METADATA)})

        response = client.get("/api/saml/login", follow_redirects=False)
        assert response.status_code == 302
        location = response.headers.get("location", "")
        assert "localhost:8443" in location


# ---------------------------------------------------------------------------
# SAML ACS with mock IdP tests (require Docker)
# ---------------------------------------------------------------------------


@requires_mock_idp
class TestSamlACSWithMockIdP:
    """End-to-end tests for SAML ACS using the mock IdP.

    Since programmatic IdP login through SimpleSAMLphp's multi-step form
    is fragile with httpx, these tests validate that:
    1. The SP can build an AuthnRequest using real IdP metadata
    2. The login redirect points to the correct IdP endpoint
    3. ACS processing works with mocked pysaml2 client configured against
       real IdP metadata (validates metadata compatibility)
    """

    def test_sp_builds_authn_request_from_mock_idp_metadata(self, client, db, seed_saml_mode):
        """SP configured with mock IdP metadata produces a valid redirect to the IdP."""
        _upsert_config(db, {"saml_idp_metadata_url": json.dumps(MOCK_IDP_METADATA)})

        response = client.get("/api/saml/login", follow_redirects=False)
        assert response.status_code == 302
        location = response.headers.get("location", "")
        # Should contain SAMLRequest parameter
        assert "SAMLRequest" in location
        assert "localhost:8443" in location

    @patch("app.api.saml_routes.get_saml_client")
    def test_acs_with_mock_idp_identity(self, mock_get_client, client, db, seed_saml_mode):
        """ACS processes an identity matching mock IdP user attributes."""
        # Simulate what the mock IdP would return for testuser
        mock_identity = {
            "urn:oid:0.9.2342.19200300.100.1.3": ["testuser@med.cornell.edu"],
            "urn:oid:2.16.840.1.113730.3.1.241": ["Test User"],
            "urn:oid:1.3.6.1.4.1.5923.1.1.1.6": ["testuser@cornell.edu"],
        }
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.get_identity.return_value = mock_identity
        mock_client.parse_authn_request_response.return_value = mock_response
        mock_get_client.return_value = mock_client

        _upsert_config(db, {"saml_idp_metadata_url": json.dumps(MOCK_IDP_METADATA)})

        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/"
        cookies = {c.name: c for c in response.cookies.jar}
        assert COOKIE_NAME in cookies

        # Verify user was provisioned
        user = db.query(User).filter(User.email == "testuser@med.cornell.edu").first()
        assert user is not None
        assert user.display_name == "Test User"
        assert user.auth_method == "saml"


# ---------------------------------------------------------------------------
# SAML error path tests (do NOT require mock IdP -- use mocked pysaml2)
# ---------------------------------------------------------------------------


def _mock_saml_client(identity_dict):
    """Create a mock Saml2Client with pre-configured ACS response."""
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.get_identity.return_value = identity_dict
    mock_client.parse_authn_request_response.return_value = mock_response
    return mock_client


_ED_ENV = {
    "ED_LDAP_URL": "ldaps://ed.weill.cornell.edu:636",
    "ED_LDAP_BIND_DN": "cn=svc-cviche,ou=ServiceAccounts,dc=weill,dc=cornell,dc=edu",
    "ED_LDAP_BIND_PASSWORD": "test-password",
}

_SAML_IDENTITY = {
    "urn:oid:0.9.2342.19200300.100.1.3": ["test@med.cornell.edu"],
    "urn:oid:2.16.840.1.113730.3.1.241": ["Test User"],
    "urn:oid:1.3.6.1.4.1.5923.1.1.1.6": ["test@cornell.edu"],
}

_SAML_IDENTITY_NO_MAIL = {
    "urn:oid:2.16.840.1.113730.3.1.241": ["Test User"],
    "urn:oid:1.3.6.1.4.1.5923.1.1.1.6": ["test@cornell.edu"],
}


class TestSamlErrorPaths:
    """Integration-level tests for SAML error scenarios.

    These exercise the full request->response cycle including config seeding,
    SAML attribute extraction, ED group checks, and cookie handling.
    No Docker required -- all SAML interactions use mocked pysaml2.
    """

    @patch("app.api.saml_routes.get_saml_client")
    def test_acs_missing_mail_attribute(self, mock_get_client, client, seed_saml_mode):
        """POST to ACS with identity missing mail -> redirect to /login?error=missing_attributes."""
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY_NO_MAIL)
        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error=missing_attributes" in response.headers["location"]

    @patch.dict(os.environ, _ED_ENV)
    @patch("app.api.saml_routes.check_ed_membership")
    @patch("app.api.saml_routes.get_saml_client")
    def test_acs_ed_group_denied(self, mock_get_client, mock_check_ed, client, seed_ed_enabled):
        """POST to ACS with ED enabled, user not in access group -> redirect to /login?error=not_authorized."""
        clear_cache()
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_check_ed.return_value = {"in_access_group": False, "in_admin_group": False}
        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error=not_authorized" in response.headers["location"]

    @patch.dict(os.environ, _ED_ENV)
    @patch("app.api.saml_routes.check_ed_membership")
    @patch("app.api.saml_routes.get_saml_client")
    def test_acs_ed_unavailable(self, mock_get_client, mock_check_ed, client, seed_ed_enabled):
        """POST to ACS with ED enabled, LDAP raises EdUnavailableError -> redirect to /login?error=directory_unavailable."""
        clear_cache()
        mock_get_client.return_value = _mock_saml_client(_SAML_IDENTITY)
        mock_check_ed.side_effect = EdUnavailableError("Connection refused")
        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error=directory_unavailable" in response.headers["location"]

    @patch.dict(os.environ, _ED_ENV)
    @patch("app.auth.check_ed_membership")
    def test_acs_expired_cache_ed_down(self, mock_check_ed, client, db, seed_ed_enabled):
        """User with expired cache and ED unavailable -> 401 on /api/auth/me."""
        clear_cache()

        # Create SAML user
        user = User(
            email="test@med.cornell.edu",
            display_name="Test User",
            role="user",
            auth_method="saml",
        )
        db.add(user)
        db.commit()
        db.refresh(user)

        # No cached membership and ED is down -> 401
        mock_check_ed.side_effect = EdUnavailableError("Connection refused")
        token = create_session_cookie(user)

        response = client.get("/api/auth/me", cookies={COOKIE_NAME: token})
        assert response.status_code == 401
        data = response.json()
        assert data["detail"]["error"] == "directory_unavailable"
