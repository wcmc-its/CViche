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


# --- Stub test classes for Plan 02 (SAML endpoints) ---


@pytest.mark.skip(reason="Plan 02 -- SAML login endpoint")
class TestSamlLogin:
    """SAML login redirect endpoint tests (SAML-01)."""

    def test_login_redirects_to_idp(self, client, seed_saml_mode):
        pass

    def test_login_blocked_in_simple_mode(self, client, seed_simple_mode):
        pass


@pytest.mark.skip(reason="Plan 02 -- SAML ACS endpoint")
class TestSamlACS:
    """SAML Assertion Consumer Service tests (SAML-01)."""

    def test_acs_valid_assertion_sets_cookie(self, client, seed_saml_mode):
        pass

    def test_acs_invalid_assertion_redirects_error(self, client, seed_saml_mode):
        pass

    def test_acs_missing_attributes_redirects_error(self, client, seed_saml_mode):
        pass


@pytest.mark.skip(reason="Plan 02 -- SAML metadata endpoint")
class TestSamlMetadata:
    """SAML SP metadata endpoint tests (SAML-02)."""

    def test_metadata_returns_xml(self, client, seed_saml_mode):
        pass


@pytest.mark.skip(reason="Plan 02 -- SAML user provisioning")
class TestSamlUserProvisioning:
    """SAML user auto-provisioning tests (SAML-03)."""

    def test_new_user_created(self, client, db, seed_saml_mode):
        pass

    def test_existing_user_updated(self, client, db, seed_saml_mode):
        pass


@pytest.mark.skip(reason="Plan 02 -- SAML logout endpoint")
class TestSamlLogout:
    """SAML logout endpoint tests (SAML-04)."""

    def test_logout_clears_cookie(self, client, seed_saml_mode):
        pass
