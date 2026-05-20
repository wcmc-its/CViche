"""Tests for SAML SP client factory, cert generation, and attribute extraction."""
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from app.saml_client import (
    extract_user_attrs,
    _generate_self_signed_cert,
    _find_xmlsec1,
    ATTR_MAIL,
    ATTR_DISPLAY_NAME,
    ATTR_EPPN,
)


# --- Unit tests: extract_user_attrs ---


class TestExtractUserAttrs:
    """Test SAML attribute extraction from identity dicts."""

    def test_extract_attrs_oid_keys(self, mock_saml_identity):
        """OID-keyed identity returns correct email, display_name, eppn."""
        result = extract_user_attrs(mock_saml_identity)
        assert result["email"] == "testuser@med.cornell.edu"
        assert result["display_name"] == "Test User"
        assert result["eppn"] == "testuser@cornell.edu"

    def test_extract_attrs_friendly_keys(self, mock_saml_identity_friendly):
        """Friendly-name-keyed identity returns correct values via fallback."""
        result = extract_user_attrs(mock_saml_identity_friendly)
        assert result["email"] == "testuser@med.cornell.edu"
        assert result["display_name"] == "Test User"
        assert result["eppn"] == "testuser@cornell.edu"

    def test_extract_attrs_missing_mail_raises(self, mock_saml_identity_no_mail):
        """Missing mail attribute raises ValueError."""
        with pytest.raises(ValueError, match="mail"):
            extract_user_attrs(mock_saml_identity_no_mail)

    def test_extract_attrs_displayname_fallback_to_eppn(self):
        """When displayName is missing, display_name falls back to eppn."""
        identity = {
            ATTR_MAIL: ["testuser@med.cornell.edu"],
            ATTR_EPPN: ["testuser@cornell.edu"],
        }
        result = extract_user_attrs(identity)
        assert result["display_name"] == "testuser@cornell.edu"

    def test_extract_attrs_email_normalized(self):
        """Email is lowercased and stripped of whitespace."""
        identity = {
            ATTR_MAIL: ["  TestUser@Med.Cornell.Edu  "],
        }
        result = extract_user_attrs(identity)
        assert result["email"] == "testuser@med.cornell.edu"


# --- Unit test: certificate generation ---


class TestCertGeneration:
    """Test self-signed SP certificate generation."""

    def test_generate_cert_creates_files(self, tmp_path):
        """_generate_self_signed_cert creates sp.crt and sp.key in cert_dir."""
        cert_dir = tmp_path / "certs"
        _generate_self_signed_cert(cert_dir)
        assert (cert_dir / "sp.crt").exists()
        assert (cert_dir / "sp.key").exists()

        # Verify files are non-empty PEM
        crt_content = (cert_dir / "sp.crt").read_text()
        key_content = (cert_dir / "sp.key").read_text()
        assert "BEGIN CERTIFICATE" in crt_content
        assert "BEGIN RSA PRIVATE KEY" in key_content


# --- Unit test: xmlsec1 detection ---


class TestFindXmlsec1:
    """Test xmlsec1 binary detection."""

    def test_find_xmlsec1_uses_which(self):
        """Falls back to shutil.which when platform-specific paths don't exist."""
        with patch("app.saml_client.Path") as mock_path_cls:
            # Make all candidate paths return exists() = False
            mock_instance = MagicMock()
            mock_instance.exists.return_value = False
            mock_path_cls.return_value = mock_instance

            with patch("app.saml_client.shutil.which", return_value="/usr/bin/xmlsec1") as mock_which:
                result = _find_xmlsec1()
                assert result == "/usr/bin/xmlsec1"
                mock_which.assert_called_once_with("xmlsec1")

    def test_find_xmlsec1_raises_when_not_found(self):
        """Raises RuntimeError when xmlsec1 is not found anywhere."""
        with patch("app.saml_client.Path") as mock_path_cls:
            mock_instance = MagicMock()
            mock_instance.exists.return_value = False
            mock_path_cls.return_value = mock_instance

            with patch("app.saml_client.shutil.which", return_value=None):
                with pytest.raises(RuntimeError, match="xmlsec1 binary not found"):
                    _find_xmlsec1()


# --- Unit test: get_saml_client config wiring ---


from app.saml_client import get_saml_client


class TestGetSamlClient:
    """Verify Saml2Client is configured from the right SystemConfig values.

    Regression coverage for the ACS-URL bug: ACS/SLO endpoints in SP metadata
    must come from saml_sp_base_url, NOT entity_id. The IdP enforces the
    Destination/Recipient against the URL we publish in metadata, so any drift
    silently rejects every assertion.
    """

    def test_acs_endpoints_use_sp_base_url_not_entity_id(self, db, seed_saml_mode, tmp_path):
        """ACS/SLO endpoint URLs are derived from sp_base_url, not entity_id."""
        # Override cert dir to a writable tmp path
        from app.models import SystemConfig
        import json as _json
        row = db.query(SystemConfig).filter(SystemConfig.key == "saml_cert_dir").first()
        row.value = _json.dumps(str(tmp_path / "certs"))
        db.commit()

        # Stub out IdP metadata loading so the call doesn't try to fetch a URL
        with patch("app.saml_client.Saml2Config.load") as mock_load, \
             patch("app.saml_client.Saml2Client") as mock_client_cls:
            get_saml_client(db)

            assert mock_load.called, "Saml2Config.load() was not called"
            saml_config = mock_load.call_args[0][0]
            endpoints = saml_config["service"]["sp"]["endpoints"]
            acs_url = endpoints["assertion_consumer_service"][0][0]
            slo_url = endpoints["single_logout_service"][0][0]

            # seed_saml_mode sets sp_base_url=https://cviche.med.cornell.edu
            # and entity_id=https://cviche.med.cornell.edu/shibboleth (with /shibboleth)
            assert acs_url == "https://cviche.med.cornell.edu/api/saml/acs", (
                f"ACS URL should be derived from sp_base_url, got {acs_url}"
            )
            assert slo_url == "https://cviche.med.cornell.edu/api/saml/logout", (
                f"SLO URL should be derived from sp_base_url, got {slo_url}"
            )
            # Sanity check: the bug would have produced this:
            assert "/shibboleth/api/saml/acs" not in acs_url, (
                "ACS URL must not include /shibboleth (the entity_id path)"
            )

    def test_raises_when_sp_base_url_missing(self, db, seed_saml_mode, tmp_path):
        """get_saml_client raises if saml_sp_base_url is empty -- fail loud, not silently broken."""
        from app.models import SystemConfig
        import json as _json
        row = db.query(SystemConfig).filter(SystemConfig.key == "saml_sp_base_url").first()
        row.value = _json.dumps("")
        db.commit()

        with pytest.raises(RuntimeError, match="saml_sp_base_url"):
            get_saml_client(db)


# --- SAML endpoint tests (Plan 02) ---

from app.models import User
from app.auth import COOKIE_NAME


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
        mock_client.parse_authn_request_response.return_value = mock_response
    else:
        mock_client.parse_authn_request_response.return_value = None
    return mock_client


class TestSamlLogin:
    """SAML login redirect endpoint tests (SAML-01)."""

    @patch("app.api.saml_routes.get_saml_client")
    def test_login_redirects_to_idp(self, mock_get_client, client, seed_saml_mode):
        """GET /api/saml/login in SAML mode returns 302 redirect to IdP."""
        mock_get_client.return_value = _mock_saml_client()
        response = client.get("/api/saml/login", follow_redirects=False)
        assert response.status_code == 302
        assert "idp.example.com" in response.headers["location"]

    def test_login_blocked_in_simple_mode(self, client, seed_simple_mode):
        """GET /api/saml/login in simple mode redirects to /login?error=saml_not_enabled."""
        response = client.get("/api/saml/login", follow_redirects=False)
        assert response.status_code == 302
        assert "error=saml_not_enabled" in response.headers["location"]

    @patch("app.api.saml_routes.get_saml_client")
    def test_login_relay_state_from_next_param(self, mock_get_client, client, seed_saml_mode):
        """GET /api/saml/login?next=/runs/ABC passes relay_state to prepare_for_authenticate."""
        mock_get_client.return_value = _mock_saml_client()
        response = client.get("/api/saml/login?next=/runs/ABC", follow_redirects=False)
        assert response.status_code == 302
        mock_get_client.return_value.prepare_for_authenticate.assert_called_once()
        call_kwargs = mock_get_client.return_value.prepare_for_authenticate.call_args
        assert call_kwargs[1]["relay_state"] == "/runs/ABC"

    @patch("app.api.saml_routes.get_saml_client")
    def test_login_error_redirects_to_login(self, mock_get_client, client, seed_saml_mode):
        """GET /api/saml/login when client raises redirects to /login?error=auth_failed."""
        mock_get_client.side_effect = Exception("config error")
        response = client.get("/api/saml/login", follow_redirects=False)
        assert response.status_code == 302
        assert "error=auth_failed" in response.headers["location"]


class TestSamlACS:
    """SAML Assertion Consumer Service tests (SAML-01)."""

    @patch("app.api.saml_routes.get_saml_client")
    def test_acs_valid_assertion_sets_cookie(self, mock_get_client, client, db, seed_saml_mode, mock_saml_identity):
        """POST /api/saml/acs with valid assertion returns 302 and sets cviche_session cookie."""
        mock_get_client.return_value = _mock_saml_client(mock_saml_identity)
        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        # Check cookie is set
        cookies = {c.name: c for c in response.cookies.jar}
        assert COOKIE_NAME in cookies

    @patch("app.api.saml_routes.get_saml_client")
    def test_acs_invalid_assertion_redirects_error(self, mock_get_client, client, seed_saml_mode):
        """POST /api/saml/acs with None authn_response redirects to /login?error=auth_failed."""
        mock_get_client.return_value = _mock_saml_client(identity_dict=None)
        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "baddata"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error=auth_failed" in response.headers["location"]

    @patch("app.api.saml_routes.get_saml_client")
    def test_acs_missing_attributes_redirects_error(self, mock_get_client, client, seed_saml_mode, mock_saml_identity_no_mail):
        """POST /api/saml/acs with missing mail redirects to /login?error=missing_attributes."""
        mock_get_client.return_value = _mock_saml_client(mock_saml_identity_no_mail)
        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error=missing_attributes" in response.headers["location"]

    @patch("app.api.saml_routes.get_saml_client")
    def test_acs_relay_state_preserved(self, mock_get_client, client, db, seed_saml_mode, mock_saml_identity):
        """POST /api/saml/acs with RelayState=/runs/ABC123 redirects to that URL."""
        mock_get_client.return_value = _mock_saml_client(mock_saml_identity)
        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/runs/ABC123"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/runs/ABC123"

    def test_acs_blocked_in_simple_mode(self, client, seed_simple_mode):
        """POST /api/saml/acs in simple mode redirects to /login?error=saml_not_enabled."""
        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error=saml_not_enabled" in response.headers["location"]

    @patch("app.api.saml_routes.get_saml_client")
    def test_acs_general_exception_redirects_error(self, mock_get_client, client, seed_saml_mode):
        """POST /api/saml/acs when client raises redirects to /login?error=auth_failed."""
        mock_get_client.side_effect = Exception("pysaml2 error")
        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error=auth_failed" in response.headers["location"]


class TestSamlMetadata:
    """SAML SP metadata endpoint tests (SAML-02)."""

    @patch("app.api.saml_routes.create_metadata_string")
    @patch("app.api.saml_routes.get_saml_client")
    def test_metadata_returns_xml(self, mock_get_client, mock_create_md, client, seed_saml_mode):
        """GET /api/saml/metadata in SAML mode returns 200 with application/xml content."""
        mock_get_client.return_value = _mock_saml_client()
        mock_create_md.return_value = b"<EntityDescriptor>test</EntityDescriptor>"
        response = client.get("/api/saml/metadata")
        assert response.status_code == 200
        assert "application/xml" in response.headers["content-type"]
        assert b"EntityDescriptor" in response.content

    def test_metadata_404_in_simple_mode(self, client, seed_simple_mode):
        """GET /api/saml/metadata in simple mode returns 404."""
        response = client.get("/api/saml/metadata")
        assert response.status_code == 404

    @patch("app.api.saml_routes.get_saml_client")
    def test_metadata_error_returns_500(self, mock_get_client, client, seed_saml_mode):
        """GET /api/saml/metadata when client raises returns 500."""
        mock_get_client.side_effect = Exception("config error")
        response = client.get("/api/saml/metadata")
        assert response.status_code == 500


class TestSamlUserProvisioning:
    """SAML user auto-provisioning tests (SAML-03)."""

    @patch("app.api.saml_routes.get_saml_client")
    def test_new_user_created(self, mock_get_client, client, db, seed_saml_mode, mock_saml_identity):
        """ACS flow creates new User with auth_method=saml and role=user."""
        mock_get_client.return_value = _mock_saml_client(mock_saml_identity)
        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        user = db.query(User).filter(User.email == "testuser@med.cornell.edu").first()
        assert user is not None
        assert user.display_name == "Test User"
        assert user.role == "user"
        assert user.auth_method == "saml"

    @patch("app.api.saml_routes.get_saml_client")
    def test_existing_user_updated(self, mock_get_client, client, db, seed_saml_mode, mock_saml_identity):
        """ACS flow updates existing user's display_name and auth_method to saml."""
        # Pre-create user with old data
        existing = User(
            email="testuser@med.cornell.edu",
            display_name="Old Name",
            role="user",
            auth_method="simple",
        )
        db.add(existing)
        db.commit()

        mock_get_client.return_value = _mock_saml_client(mock_saml_identity)
        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 302

        db.expire_all()
        user = db.query(User).filter(User.email == "testuser@med.cornell.edu").first()
        assert user is not None
        assert user.display_name == "Test User"
        assert user.auth_method == "saml"


class TestSamlLogout:
    """SAML logout endpoint tests (SAML-04)."""

    def test_logout_post_clears_cookie(self, client):
        """POST /api/saml/logout returns 302 to /login and clears cviche_session."""
        response = client.post("/api/saml/logout", follow_redirects=False)
        assert response.status_code == 302
        assert "/login" in response.headers["location"]
        # Check that the cookie is being cleared (set-cookie with max-age=0 or expires in past)
        set_cookie = response.headers.get("set-cookie", "")
        assert COOKIE_NAME in set_cookie

    def test_logout_get_clears_cookie(self, client):
        """GET /api/saml/logout returns 302 to /login and clears cviche_session."""
        response = client.get("/api/saml/logout", follow_redirects=False)
        assert response.status_code == 302
        assert "/login" in response.headers["location"]
        set_cookie = response.headers.get("set-cookie", "")
        assert COOKIE_NAME in set_cookie
