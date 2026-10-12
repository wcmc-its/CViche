"""Tests for SAML SP client factory, cert generation, and attribute extraction."""

from pathlib import Path
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from saml2.response import IncorrectlySigned, VerificationError
from saml2.sigver import SigverError
from saml2.validate import ResponseLifetimeExceed

from app.saml_client import (
    ATTR_DISPLAY_NAME,
    ATTR_EPPN,
    ATTR_MAIL,
    ATTR_UID,
    _find_xmlsec1,
    _generate_self_signed_cert,
    extract_user_attrs,
)

# --- Unit tests: extract_user_attrs ---


class TestExtractUserAttrs:
    """Test SAML attribute extraction from identity dicts."""

    def test_extract_attrs_oid_keys(self, mock_saml_identity):
        """OID-keyed identity returns correct cwid, email, display_name, eppn."""
        result = extract_user_attrs(mock_saml_identity)
        assert result["cwid"] == "testuser"  # from ePPN local-part
        assert result["email"] == "testuser@med.cornell.edu"
        assert result["display_name"] == "Test User"
        assert result["eppn"] == "testuser@med.cornell.edu"

    def test_extract_attrs_friendly_keys(self, mock_saml_identity_friendly):
        """Friendly-name-keyed identity returns correct values via fallback."""
        result = extract_user_attrs(mock_saml_identity_friendly)
        assert result["cwid"] == "testuser"
        assert result["email"] == "testuser@med.cornell.edu"
        assert result["display_name"] == "Test User"
        assert result["eppn"] == "testuser@med.cornell.edu"

    def test_extract_attrs_no_mail_uses_eppn_cwid(self, mock_saml_identity_no_mail):
        """No mail is fine (nothing sends email): cwid comes from ePPN, email is None."""
        result = extract_user_attrs(mock_saml_identity_no_mail)
        assert result["cwid"] == "testuser"
        assert result["email"] is None

    def test_extract_attrs_no_identifier_raises(self):
        """No mail, no ePPN, no uid -> nothing to anchor identity on -> ValueError."""
        with pytest.raises(ValueError, match="CWID"):
            extract_user_attrs({"displayName": ["Nameless"]})

    def test_extract_attrs_displayname_fallback_to_eppn(self):
        """When displayName is missing, display_name falls back to eppn."""
        identity = {
            ATTR_MAIL: ["testuser@med.cornell.edu"],
            ATTR_EPPN: ["testuser@med.cornell.edu"],
        }
        result = extract_user_attrs(identity)
        assert result["display_name"] == "testuser@med.cornell.edu"

    def test_extract_attrs_email_normalized(self):
        """Email is lowercased and stripped of whitespace."""
        identity = {
            ATTR_MAIL: ["  TestUser@Med.Cornell.Edu  "],
            ATTR_EPPN: ["testuser@med.cornell.edu"],  # provides the cwid anchor
        }
        result = extract_user_attrs(identity)
        assert result["email"] == "testuser@med.cornell.edu"

    def test_extract_attrs_whitespace_only_email_is_none(self):
        """Whitespace-only mail collapses to None, not '' (#413) -- '' would
        collide with every other missing-email user once email is a unique
        nullable column."""
        identity = {
            ATTR_MAIL: ["  "],
            ATTR_EPPN: ["testuser@med.cornell.edu"],  # provides the cwid anchor
        }
        result = extract_user_attrs(identity)
        assert result["email"] is None

    def test_unmapped_attrs_are_logged_not_silent(self, caplog):
        """Attributes the IdP releases that we don't consume are logged (by
        name) so the drop is visible -- and their VALUES are never logged."""
        identity = {
            ATTR_MAIL: ["testuser@med.cornell.edu"],
            ATTR_EPPN: ["testuser@med.cornell.edu"],  # cwid anchor -- a mapped attr
            "eduPersonAffiliation": ["staff"],
            "isMemberOf": ["cn=secret-group"],
        }
        with caplog.at_level("INFO", logger="app.saml_client"):
            extract_user_attrs(identity)
        msg = "\n".join(r.getMessage() for r in caplog.records)
        assert "eduPersonAffiliation" in msg and "isMemberOf" in msg
        # names only -- attribute values must not leak into logs
        assert "staff" not in msg and "secret-group" not in msg

    def test_no_log_when_all_attrs_mapped(self, mock_saml_identity, caplog):
        """No unmapped-attribute noise when the IdP releases only known attrs."""
        with caplog.at_level("INFO", logger="app.saml_client"):
            extract_user_attrs(mock_saml_identity)
        assert "unmapped" not in "\n".join(r.getMessage() for r in caplog.records)

    # --- D9 #20: malformed/multi-valued IdP attributes (mrj4001 review,
    # PR #781 thread r3967362882) -- SAML attributes are externally supplied.

    def test_extract_attrs_empty_list_value_treated_as_absent(self):
        """An attribute present but released with an empty value list is the
        same as not being released -- falls through to the next candidate."""
        identity = {ATTR_UID: [], ATTR_EPPN: ["testuser@med.cornell.edu"]}
        result = extract_user_attrs(identity)
        assert result["cwid"] == "testuser"

    def test_extract_attrs_none_value_treated_as_absent(self):
        identity = {ATTR_UID: None, ATTR_EPPN: ["testuser@med.cornell.edu"]}
        result = extract_user_attrs(identity)
        assert result["cwid"] == "testuser"

    def test_extract_attrs_multi_valued_list_uses_first_value(self):
        """Multiple released values for one attribute: the first is used,
        pinned explicitly rather than left to accidental indexing."""
        identity = {
            ATTR_UID: ["first-uid", "second-uid"],
            ATTR_EPPN: ["testuser@med.cornell.edu"],
        }
        result = extract_user_attrs(identity)
        assert result["cwid"] == "first-uid"

    # --- D9 #21: CWID trust-boundary tests (mrj4001 review, PR #781 thread
    # r3967362882) -- CWID becomes the identity anchor, so its derivation
    # from an untrusted ePPN needs explicit negative coverage.

    def test_extract_attrs_eppn_without_at_sign_yields_no_cwid(self):
        """A malformed ePPN with no '@' cannot anchor identity -- ValueError,
        never a garbage cwid built from the whole string."""
        identity = {ATTR_EPPN: ["not-an-eppn"]}
        with pytest.raises(ValueError, match="CWID"):
            extract_user_attrs(identity)

    def test_extract_attrs_eppn_empty_local_part_yields_no_cwid(self):
        """An empty local part ("@med.cornell.edu") must yield ValueError,
        never a falsy-but-non-None "" cwid reaching provisioning as a
        garbage identity anchor."""
        identity = {ATTR_EPPN: ["@med.cornell.edu"]}
        with pytest.raises(ValueError, match="CWID"):
            extract_user_attrs(identity)

    # --- #1452: a partner IdP's users must never land on a WCM account.

    def test_extract_attrs_partner_eppn_keyed_on_full_eppn(self):
        """Same local part from WCM and from Cornell Ithaca -> two different
        anchors. The partner's uid (a NetID) is ignored, not taken as a CWID."""
        wcm = extract_user_attrs({ATTR_EPPN: ["js1234@med.cornell.edu"]})
        partner = extract_user_attrs({ATTR_UID: ["js1234"], ATTR_EPPN: ["JS1234@Cornell.edu"]})
        assert wcm["cwid"] == "js1234"
        assert partner["cwid"] == "js1234@cornell.edu"

    def test_extract_attrs_partner_eppn_with_empty_local_part_is_rejected(self):
        """"@hss.edu" has no local part: no anchor, and the uid is still not
        taken as a CWID."""
        with pytest.raises(ValueError, match="CWID"):
            extract_user_attrs({ATTR_UID: ["js1234"], ATTR_EPPN: ["@hss.edu"]})


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

    def test_generated_key_is_owner_only(self, tmp_path):
        """The unencrypted SP private key is written 0600, not umask-default 0644."""
        cert_dir = tmp_path / "certs"
        _generate_self_signed_cert(cert_dir)
        mode = (cert_dir / "sp.key").stat().st_mode & 0o777
        assert mode & 0o077 == 0, f"sp.key is group/world-readable: {oct(mode)}"

    def test_generated_cert_and_key_are_cryptographically_valid_pair(self, tmp_path):
        """T11.1 (mrj4001 review, PR #781 thread r3968019366): PEM markers
        alone don't prove the pair would work at runtime in xmlsec/pysaml2.
        Parse both with `cryptography` and prove (a) the cert's public key
        matches the private key's, and (b) the cert verifies its own
        signature (it's self-signed)."""
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        cert_dir = tmp_path / "certs"
        _generate_self_signed_cert(cert_dir)

        cert = x509.load_pem_x509_certificate((cert_dir / "sp.crt").read_bytes())
        key = serialization.load_pem_private_key(
            (cert_dir / "sp.key").read_bytes(), password=None
        )

        assert key.public_key().public_numbers() == cert.public_key().public_numbers()

        # Self-signed: the cert's own public key must verify its signature.
        cert.public_key().verify(
            cert.signature,
            cert.tbs_certificate_bytes,
            padding.PKCS1v15(),
            cert.signature_hash_algorithm,
        )


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

    def test_find_xmlsec1_skips_existing_but_non_executable_candidate(self):
        """T11.2 (mrj4001 review, PR #781 thread r3968019366): a candidate
        path that exists but isn't executable (e.g. a partial package
        install) must fall through to the PATH lookup, not be handed to
        pysaml2/xmlsec as if it were runnable."""
        with patch("app.saml_client.Path") as mock_path_cls:
            mock_instance = MagicMock()
            mock_instance.exists.return_value = True  # every candidate "exists"...
            mock_path_cls.return_value = mock_instance

            with patch("app.saml_client.os.access", return_value=False) as mock_access, \
                 patch("app.saml_client.shutil.which", return_value="/usr/bin/xmlsec1") as mock_which:
                # ...but none is executable, so all three candidates are
                # skipped and the PATH fallback is used.
                result = _find_xmlsec1()
                assert result == "/usr/bin/xmlsec1"
                mock_which.assert_called_once_with("xmlsec1")
            assert mock_access.called

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
        import json as _json

        from app.models import SystemConfig
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
        import json as _json

        from app.models import SystemConfig
        row = db.query(SystemConfig).filter(SystemConfig.key == "saml_sp_base_url").first()
        row.value = _json.dumps("")
        db.commit()

        with pytest.raises(RuntimeError, match="saml_sp_base_url"):
            get_saml_client(db)

    def test_half_populated_cert_dir_raises(self, db, seed_saml_mode, tmp_path):
        """sp.crt present but sp.key missing -> fail loud, don't hand pysaml2 a
        key path that does not exist (the failure would surface later in xmlsec1).
        """
        import json as _json

        from app.models import SystemConfig
        cert_dir = tmp_path / "certs"
        cert_dir.mkdir()
        (cert_dir / "sp.crt").write_text("-----BEGIN CERTIFICATE-----\n")
        row = db.query(SystemConfig).filter(SystemConfig.key == "saml_cert_dir").first()
        row.value = _json.dumps(str(cert_dir))
        db.commit()

        with patch("app.saml_client.Saml2Config.load"), \
             patch("app.saml_client.Saml2Client"):
            with pytest.raises(RuntimeError, match="half-populated"):
                get_saml_client(db)

        # The existing cert is the one filed with the IdP -- never regenerated.
        assert (cert_dir / "sp.crt").read_text() == "-----BEGIN CERTIFICATE-----\n"

    def test_config_allows_unsolicited_responses(self, db, seed_saml_mode, tmp_path):
        """D9 #12 half of the fallback (mrj4001 review, PR #781 thread
        r3967362882): the SP config's allow_unsolicited flag is what makes
        IdP-initiated SSO -- an unsolicited response -- acceptable at all.
        The other half (a real-parser unsolicited response actually being
        accepted) needs the mock-IdP harness (test_integration_saml.py,
        skips without docker) or a real pysaml2 Server/Client harness like
        test_saml_signature_enforcement.py's -- both outside this ticket's
        write set; see the reply for r3967362882."""
        import json as _json

        from app.models import SystemConfig
        row = db.query(SystemConfig).filter(SystemConfig.key == "saml_cert_dir").first()
        row.value = _json.dumps(str(tmp_path / "certs"))
        db.commit()

        with patch("app.saml_client.Saml2Config.load") as mock_load, \
             patch("app.saml_client.Saml2Client"):
            get_saml_client(db)
            saml_config = mock_load.call_args[0][0]
            assert saml_config["service"]["sp"]["allow_unsolicited"] is True

# --- SAML endpoint tests (Plan 02) ---

import logging

from saml2.mdstore import SourceNotFound
from saml2.sigver import CertificateError

from app.auth import (
    COOKIE_NAME,
    SESSION_TTL,
    decode_session_cookie,
    get_cookie_settings,
)
from app.ed_group_lookup import EdUnavailableError, MembershipResult
from app.models import SystemConfig, User
from app.saml_replay import SamlReplayCache, set_replay_cache
from app.services.saml_service import SamlLoginFailure


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
    def test_login_rejects_offsite_next(self, mock_get_client, client, seed_saml_mode):
        """GET /api/saml/login?next=<off-site> coerces relay_state to '/' (CWE-601)."""
        mock_get_client.return_value = _mock_saml_client()
        for evil in ("https://evil.com", "//evil.com", "/\\evil.com", "javascript:alert(1)"):
            mock_get_client.return_value.prepare_for_authenticate.reset_mock()
            response = client.get(
                "/api/saml/login", params={"next": evil}, follow_redirects=False
            )
            assert response.status_code == 302
            call_kwargs = mock_get_client.return_value.prepare_for_authenticate.call_args
            assert call_kwargs[1]["relay_state"] == "/", f"unsafe next not rejected: {evil!r}"

    @patch("app.api.saml_routes.get_saml_client")
    def test_login_error_redirects_to_login(self, mock_get_client, client, seed_saml_mode):
        """GET /api/saml/login when get_saml_client raises a config error (RuntimeError,
        as it really does -- see app/saml_client.py) redirects to /login?error=auth_failed."""
        mock_get_client.side_effect = RuntimeError("config error")
        response = client.get("/api/saml/login", follow_redirects=False)
        assert response.status_code == 302
        assert "error=auth_failed" in response.headers["location"]

    @patch("app.api.saml_routes.get_saml_client")
    def test_login_unexpected_exception_propagates(self, mock_get_client, client, seed_saml_mode):
        """GET /api/saml/login: an exception type outside the narrowed catch
        (RuntimeError/OSError/SAMLError/CertificateError/SourceNotFound) is a
        genuinely unexpected bug, not an operational failure -- it must NOT be
        folded into /login?error=auth_failed. It propagates out of the route,
        past saml_routes.py entirely, and is caught only by main.py's
        app-wide SecurityHeadersMiddleware/global exception handler, which
        returns the generic sanitized "internal_error" JSON response (not a
        302, and not either of this file's own SAML-flavored error bodies) --
        see saml_routes.py's comment, mrj4001 review PR #656 item 6 / #672."""
        mock_get_client.side_effect = TypeError("unexpected bug, not an auth failure")
        response = client.get("/api/saml/login", follow_redirects=False)
        assert response.status_code == 500
        assert response.json()["error"] == "internal_error"


class TestSamlACS:
    """SAML Assertion Consumer Service tests (SAML-01)."""

    @patch("app.services.saml_service.get_saml_client")
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

    @patch("app.services.saml_service.get_saml_client")
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

    @patch("app.services.saml_service.get_saml_client")
    def test_acs_missing_attributes_redirects_error(self, mock_get_client, client, seed_saml_mode):
        """POST /api/saml/acs with no usable identifier (no mail/ePPN/uid) redirects
        to /login?error=missing_attributes."""
        mock_get_client.return_value = _mock_saml_client({"displayName": ["Nameless"]})
        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error=missing_attributes" in response.headers["location"]

    @patch("app.services.saml_service.get_saml_client")
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

    @patch("app.services.saml_service.get_saml_client")
    def test_acs_rejects_offsite_relay_state(self, mock_get_client, client, db, seed_saml_mode, mock_saml_identity):
        """POST /api/saml/acs with an off-site RelayState redirects to '/' not off-site (CWE-601)."""
        for evil in ("https://evil.com", "//evil.com", "/\\evil.com"):
            mock_get_client.return_value = _mock_saml_client(mock_saml_identity)
            response = client.post(
                "/api/saml/acs",
                data={"SAMLResponse": "base64data", "RelayState": evil},
                follow_redirects=False,
            )
            assert response.status_code == 302
            assert response.headers["location"] == "/", f"unsafe RelayState not rejected: {evil!r}"

    def test_acs_blocked_in_simple_mode(self, client, seed_simple_mode):
        """POST /api/saml/acs in simple mode redirects to /login?error=saml_not_enabled."""
        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error=saml_not_enabled" in response.headers["location"]

    @patch("app.services.saml_service.get_saml_client")
    def test_acs_general_exception_redirects_error(self, mock_get_client, client, seed_saml_mode):
        """POST /api/saml/acs when get_saml_client raises a config error (RuntimeError,
        as it really does -- see app/saml_client.py) redirects to /login?error=auth_failed."""
        mock_get_client.side_effect = RuntimeError("pysaml2 error")
        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert "error=auth_failed" in response.headers["location"]

    @patch("app.services.saml_service.get_saml_client")
    def test_acs_unexpected_exception_from_parsing_is_caught_and_redirects(
        self, mock_get_client, client, seed_saml_mode
    ):
        """D10 (mrj4001 review, PR #781 threads r3967362882 #11 /
        r3966560287): _parse_saml_assertion is the untrusted-input parsing
        boundary, so it now has a bare `except Exception` after its typed
        clauses -- ANY exception raised while parsing (including a
        genuinely unexpected one, not only pysaml2's typed exceptions) fails
        closed to a sanitized redirect instead of a stack-trace-bearing 500.
        This supersedes the previous contract for this function specifically
        (mrj4001 review, PR #656 item 6 / #672) -- see
        test_acs_unexpected_exception_outside_parsing_boundary_still_propagates
        below for the part of the ACS pipeline the catch-all does NOT cover."""
        mock_get_client.side_effect = TypeError("unexpected bug during parsing")
        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data"},
            follow_redirects=False,
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/login?error=auth_failed"
        assert COOKIE_NAME not in {c.name for c in response.cookies.jar}

    @patch("app.services.saml_service.provision_user")
    @patch("app.services.saml_service.get_saml_client")
    def test_acs_unexpected_exception_outside_parsing_boundary_still_propagates(
        self, mock_get_client, mock_provision, client, seed_saml_mode, mock_saml_identity
    ):
        """D10's catch-all is scoped to _parse_saml_assertion only -- a
        genuinely unexpected bug elsewhere in the ACS pipeline (here:
        provisioning, which runs after parsing succeeds) must still surface
        as a 500, not get folded into /login?error=auth_failed (mrj4001
        review PR #656 item 6 / #672, unaffected by D10)."""
        mock_get_client.return_value = _mock_saml_client(mock_saml_identity)
        mock_provision.side_effect = TypeError("unexpected bug, not an auth failure")
        response = client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 500
        assert response.json()["error"] == "internal_error"


class TestSamlAcsExceptionTaxonomy:
    """D9 #9-#11 (mrj4001 review, PR #781 thread r3967362882): every other
    ACS test stubs parse_authn_request_response() with a valid response or
    None. These exercise the real pysaml2 exception types the parser raises
    for signature failure, an expired assertion, and audience/recipient
    mismatch -- proving each produces a sanitized redirect with no session
    cookie and no provisioned user."""

    @pytest.mark.parametrize("exc", [
        SigverError("bad signature"),
        IncorrectlySigned("not correctly signed"),
    ], ids=["SigverError", "IncorrectlySigned"])
    @patch("app.services.saml_service.get_saml_client")
    def test_signature_failure_redirects_without_cookie_or_user(
        self, mock_get_client, exc, client, db, seed_saml_mode
    ):
        """#9: ACS signature-validation failure."""
        mock_client = MagicMock()
        mock_client.parse_authn_request_response.side_effect = exc
        mock_get_client.return_value = mock_client
        response = client.post(
            "/api/saml/acs", data={"SAMLResponse": "base64data"}, follow_redirects=False
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/login?error=auth_failed"
        assert COOKIE_NAME not in {c.name for c in response.cookies.jar}
        assert db.query(User).filter(User.email == "testuser@med.cornell.edu").first() is None

    @patch("app.services.saml_service.get_saml_client")
    def test_expired_assertion_redirects_without_cookie(
        self, mock_get_client, client, db, seed_saml_mode
    ):
        """#10: ResponseLifetimeExceed -- pysaml2's expired-assertion exception."""
        mock_client = MagicMock()
        mock_client.parse_authn_request_response.side_effect = ResponseLifetimeExceed("too old")
        mock_get_client.return_value = mock_client
        response = client.post(
            "/api/saml/acs", data={"SAMLResponse": "base64data"}, follow_redirects=False
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/login?error=auth_failed"
        assert COOKIE_NAME not in {c.name for c in response.cookies.jar}

    @patch("app.services.saml_service.get_saml_client")
    def test_audience_restriction_failure_redirects_without_500(
        self, mock_get_client, client, db, seed_saml_mode
    ):
        """#11 (audience): pysaml2's audience check raises a BARE Exception
        (saml2/response.py's for_me()), not a SAMLError subclass -- only
        D10's catch-all handles this. Mutant M4: dropping that catch-all
        makes this fail (500 instead of a sanitized redirect)."""
        mock_client = MagicMock()
        mock_client.parse_authn_request_response.side_effect = Exception(
            "AudienceRestrictions conditions not satisfied!"
        )
        mock_get_client.return_value = mock_client
        response = client.post(
            "/api/saml/acs", data={"SAMLResponse": "base64data"}, follow_redirects=False
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/login?error=auth_failed"
        assert COOKIE_NAME not in {c.name for c in response.cookies.jar}

    @patch("app.services.saml_service.get_saml_client")
    def test_recipient_mismatch_redirects_without_cookie(
        self, mock_get_client, client, db, seed_saml_mode
    ):
        """#11 (recipient): saml2.response.VerificationError -- a SAMLError
        subclass, caught by the existing typed clause."""
        mock_client = MagicMock()
        mock_client.parse_authn_request_response.side_effect = VerificationError(
            "No valid recipient"
        )
        mock_get_client.return_value = mock_client
        response = client.post(
            "/api/saml/acs", data={"SAMLResponse": "base64data"}, follow_redirects=False
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/login?error=auth_failed"
        assert COOKIE_NAME not in {c.name for c in response.cookies.jar}

    @patch("app.services.saml_service.get_saml_client")
    def test_destination_mismatch_none_response_redirects(self, mock_get_client, client, seed_saml_mode):
        """#11 (destination): a Destination mismatch makes
        parse_authn_request_response return None rather than raise -- same
        code path as test_acs_invalid_assertion_redirects_error, pinned here
        explicitly alongside its audience/recipient siblings."""
        mock_get_client.return_value = _mock_saml_client(identity_dict=None)
        response = client.post(
            "/api/saml/acs", data={"SAMLResponse": "base64data"}, follow_redirects=False
        )
        assert response.status_code == 302
        assert response.headers["location"] == "/login?error=auth_failed"

    @patch("app.services.saml_service.get_saml_client")
    def test_acs_calls_parser_with_no_outstanding_map(
        self, mock_get_client, client, db, seed_saml_mode, mock_saml_identity
    ):
        """#12 half of the fallback (mrj4001 review, PR #781 thread
        r3967362882): IdP-initiated SSO requires the ACS to never constrain
        parsing to an outstanding-request map. See
        TestGetSamlClient.test_config_allows_unsolicited_responses for the
        other half."""
        mock_get_client.return_value = _mock_saml_client(mock_saml_identity)
        client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": "/"},
            follow_redirects=False,
        )
        call = mock_get_client.return_value.parse_authn_request_response
        call.assert_called_once()
        args, kwargs = call.call_args
        assert "outstanding" not in kwargs
        assert len(args) == 2  # (saml_response, BINDING_HTTP_POST) only


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
        """GET /api/saml/metadata when get_saml_client raises a config error
        (RuntimeError, as it really does -- see app/saml_client.py) returns 500."""
        mock_get_client.side_effect = RuntimeError("config error")
        response = client.get("/api/saml/metadata")
        assert response.status_code == 500

    @patch("app.api.saml_routes.get_saml_client")
    def test_metadata_unexpected_exception_propagates(self, mock_get_client, client, seed_saml_mode):
        """GET /api/saml/metadata: an exception type outside the narrowed catch
        (RuntimeError/OSError/SAMLError/CertificateError/SourceNotFound) is a
        genuinely unexpected bug -- it must NOT be folded into the same
        "SAML metadata not available" plain-text 500 an ops/config problem
        gets. It propagates out of the route entirely, caught only by
        main.py's app-wide SecurityHeadersMiddleware/global exception
        handler, which returns a distinct, generic sanitized "internal_error"
        JSON response -- see saml_routes.py's comment, mrj4001 review PR #656
        item 6 / #672."""
        mock_get_client.side_effect = TypeError("unexpected bug, not unavailability")
        response = client.get("/api/saml/metadata")
        assert response.status_code == 500
        assert response.json()["error"] == "internal_error"
        assert b"SAML metadata not available" not in response.content


class TestSamlUserProvisioning:
    """SAML user auto-provisioning tests (SAML-03)."""

    @patch("app.services.saml_service.get_saml_client")
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

    @patch("app.services.saml_service.get_saml_client")
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


# The login-page error each ACS workflow failure produced before #349.
_AUTH_FAILED = "/login?error=auth_failed"
_LOGIN_ERROR_FOR = {
    SamlLoginFailure.NO_RESPONSE: _AUTH_FAILED,
    SamlLoginFailure.BAD_SIGNATURE: _AUTH_FAILED,
    SamlLoginFailure.RESPONSE_REJECTED: _AUTH_FAILED,
    SamlLoginFailure.PARSER_ERROR: _AUTH_FAILED,
    SamlLoginFailure.WRONG_DESTINATION: _AUTH_FAILED,
    SamlLoginFailure.MULTIPLE_ASSERTIONS: _AUTH_FAILED,
    SamlLoginFailure.REPLAYED: _AUTH_FAILED,
    SamlLoginFailure.NO_ASSERTION_ID: _AUTH_FAILED,
    SamlLoginFailure.MISSING_ATTRIBUTES: "/login?error=missing_attributes",
    SamlLoginFailure.NOT_AUTHORIZED: "/login?error=not_authorized",
    SamlLoginFailure.DIRECTORY_UNAVAILABLE: "/login?error=directory_unavailable",
    SamlLoginFailure.SESSION_STORE_UNAVAILABLE: "/login?error=session_store_unavailable",
    SamlLoginFailure.SESSION_STATE_UNAVAILABLE: "/login?error=session_state_unavailable",
    SamlLoginFailure.ACCOUNT_DISABLED: "/login?error=account_disabled",
}


class TestSamlAcsContract:
    """POST /api/saml/acs outcomes pinned before the ACS workflow moved into
    app/services/saml_service.py (#349, CODING_STANDARDS.md §6.3): the arms the
    tests above, test_saml_replay.py, test_ed_group.py and
    test_session_identity_resolution.py did not already pin."""

    _ACS_URL = "https://cviche.med.cornell.edu/api/saml/acs"

    @staticmethod
    def _post(client, relay_state="/"):
        return client.post(
            "/api/saml/acs",
            data={"SAMLResponse": "base64data", "RelayState": relay_state},
            follow_redirects=False,
        )

    @staticmethod
    def _events(caplog, name):
        return [r for r in caplog.records if r.getMessage() == name]

    @pytest.mark.parametrize("failure", list(SamlLoginFailure))
    def test_every_workflow_failure_redirects_to_its_login_error(self, failure, client, seed_saml_mode):
        """Every check the workflow can refuse on maps to the login-page error
        it produced before #349 -- and none falls through to a 500."""
        with patch("app.api.saml_routes.authenticate_saml_response", return_value=failure):
            response = self._post(client)

        assert (response.status_code, response.headers["location"]) == (302, _LOGIN_ERROR_FOR[failure])
        assert "set-cookie" not in response.headers

    @patch("app.services.saml_service.get_saml_client")
    def test_success_sets_the_login_cookie_and_logs_login_success(
        self, mock_get_client, client, db, seed_saml_mode, mock_saml_identity, caplog
    ):
        mock_get_client.return_value = _mock_saml_client(mock_saml_identity)

        with caplog.at_level(logging.INFO, logger="app.api.saml_routes"):
            response = self._post(client, relay_state="/runs/R1")

        assert (response.status_code, response.headers["location"]) == (302, "/runs/R1")
        user = db.query(User).one()
        token = response.cookies[COOKIE_NAME]
        assert decode_session_cookie(token)["user_id"] == user.id
        secure = "; Secure" if get_cookie_settings()["secure"] else ""
        assert response.headers["set-cookie"] == (
            f"{COOKIE_NAME}={token}; HttpOnly; Max-Age={SESSION_TTL}; Path=/; SameSite=lax{secure}"
        )
        [event] = self._events(caplog, "LOGIN_SUCCESS")
        assert (event.name, event.user_id, event.email, event.role, event.auth_method) == (
            "app.api.saml_routes", user.id, "testuser@med.cornell.edu", "user", "saml",
        )

    @patch("app.services.saml_service.get_saml_client")
    def test_unreadable_epoch_redirects_without_a_cookie_after_provisioning(
        self, mock_get_client, client, db, seed_saml_mode, mock_saml_identity, caplog
    ):
        mock_get_client.return_value = _mock_saml_client(mock_saml_identity)
        db.query(SystemConfig).filter(SystemConfig.key == "session_epoch").one().value = '"abc"'
        db.commit()

        with caplog.at_level(logging.INFO, logger="app.api.saml_routes"):
            response = self._post(client)

        assert response.headers["location"] == "/login?error=session_state_unavailable"
        assert "set-cookie" not in response.headers
        [failed] = self._events(caplog, "LOGIN_FAILED")
        assert (failed.name, failed.cwid, failed.reason) == (
            "app.api.saml_routes", "testuser", "session_state_unavailable",
        )
        assert not self._events(caplog, "LOGIN_SUCCESS")
        assert db.query(User).one().cwid == "testuser"

    @patch("app.services.saml_service.get_saml_client")
    def test_ed_disabled_preserves_the_existing_role(
        self, mock_get_client, client, db, seed_saml_mode, mock_saml_identity
    ):
        db.add(User(cwid="testuser", email="testuser@med.cornell.edu", display_name="Old",
                    role="admin", auth_method="saml"))
        db.commit()
        mock_get_client.return_value = _mock_saml_client(mock_saml_identity)

        assert self._post(client).headers["location"] == "/"

        db.expire_all()
        assert db.query(User).one().role == "admin"

    @pytest.mark.parametrize("exc, level, message", [
        (CertificateError("bad cert"), logging.WARNING,
         "[SECURITY] SAML signature validation failed: bad cert"),
        (OSError("metadata unreachable"), logging.ERROR,
         "SAML ACS processing failed: metadata unreachable"),
        (SourceNotFound("https://idp/metadata"), logging.ERROR,
         "SAML ACS processing failed: https://idp/metadata"),
    ], ids=["CertificateError", "OSError", "SourceNotFound"])
    @patch("app.services.saml_service.get_saml_client")
    def test_parse_exception_is_logged_by_its_own_clause(
        self, mock_get_client, exc, level, message, client, db, seed_saml_mode, caplog
    ):
        mock_get_client.return_value.parse_authn_request_response.side_effect = exc

        with caplog.at_level(logging.INFO, logger="app.api.saml_routes"):
            response = self._post(client)

        assert response.headers["location"] == "/login?error=auth_failed"
        assert "set-cookie" not in response.headers
        assert [(r.name, r.levelno) for r in caplog.records if r.getMessage() == message] == [
            ("app.api.saml_routes", level),
        ]

    @patch("app.services.saml_service.get_saml_client")
    def test_wrong_destination_is_rejected_before_the_replay_gate_records_the_id(
        self, mock_get_client, client, db, seed_saml_mode, mock_saml_identity, caplog
    ):
        mock_client = _mock_saml_client(mock_saml_identity)
        authn_response = mock_client.parse_authn_request_response.return_value
        authn_response.response.destination = "https://evil.test/api/saml/acs"
        authn_response.return_addrs = [self._ACS_URL]
        mock_get_client.return_value = mock_client
        cache = SamlReplayCache("", fail_closed=True)
        set_replay_cache(cache)
        try:
            with caplog.at_level(logging.WARNING, logger="app.api.saml_routes"):
                response = self._post(client)
        finally:
            set_replay_cache(None)

        assert response.headers["location"] == "/login?error=auth_failed"
        assert cache._local == {}
        assert db.query(User).count() == 0
        assert any(r.getMessage().startswith(
            "[SECURITY] SAML response rejected: Destination 'https://evil.test/api/saml/acs'"
        ) for r in caplog.records)

    @pytest.mark.parametrize("ed_answer, location", [
        (MembershipResult(in_access_group=False, in_admin_group=False), "/login?error=not_authorized"),
        (EdUnavailableError("ldap down"), "/login?error=directory_unavailable"),
    ], ids=["not_in_access_group", "ed_unavailable"])
    @patch("app.services.saml_service.fetch_ed_department")
    @patch("app.services.saml_service.check_ed_membership")
    @patch("app.services.saml_service.get_saml_client")
    def test_ed_denial_provisions_nobody_and_reads_no_department(
        self, mock_get_client, mock_check_ed, mock_department, ed_answer, location,
        client, db, seed_ed_enabled, mock_saml_identity, monkeypatch,
    ):
        for key, value in {"ED_LDAP_URL": "ldaps://ed.test:636", "ED_LDAP_BIND_DN": "cn=svc",
                           "ED_LDAP_BIND_PASSWORD": "pw"}.items():
            monkeypatch.setenv(key, value)
        mock_get_client.return_value = _mock_saml_client(mock_saml_identity)
        if isinstance(ed_answer, Exception):
            mock_check_ed.side_effect = ed_answer
        else:
            mock_check_ed.return_value = ed_answer

        response = self._post(client)

        assert response.headers["location"] == location
        assert "set-cookie" not in response.headers
        assert db.query(User).count() == 0
        mock_department.assert_not_called()


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
