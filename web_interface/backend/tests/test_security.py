"""Security regression tests for SEC-02 and SEC-03."""
import logging
import os
import importlib
import pytest
from unittest.mock import patch, MagicMock


class TestSessionSecret:
    """SEC-03: Application refuses to start without CVICHE_SESSION_SECRET."""

    def test_missing_secret_raises_runtime_error(self):
        """Removing CVICHE_SESSION_SECRET causes RuntimeError on module reload."""
        import app.auth as auth_module
        saved = os.environ.pop("CVICHE_SESSION_SECRET", None)
        try:
            with pytest.raises(RuntimeError, match="CVICHE_SESSION_SECRET"):
                importlib.reload(auth_module)
        finally:
            if saved is not None:
                os.environ["CVICHE_SESSION_SECRET"] = saved
            # Restore module state by reloading with the env var present
            importlib.reload(auth_module)

    def test_present_secret_initializes_normally(self):
        """With CVICHE_SESSION_SECRET set, auth module loads without error."""
        with patch.dict(os.environ, {"CVICHE_SESSION_SECRET": "test-value"}):
            import app.auth as auth_module
            importlib.reload(auth_module)
            # Verify serializer was created (module-level _serializer exists)
            assert hasattr(auth_module, "_serializer")
            assert auth_module._serializer is not None
        # Restore
        import app.auth as auth_mod
        importlib.reload(auth_mod)

    def test_error_message_includes_generation_command(self):
        """Error message tells user how to generate a secret."""
        import app.auth as auth_module
        saved = os.environ.pop("CVICHE_SESSION_SECRET", None)
        try:
            with pytest.raises(RuntimeError, match=r'python -c "import secrets'):
                importlib.reload(auth_module)
        finally:
            if saved is not None:
                os.environ["CVICHE_SESSION_SECRET"] = saved
            importlib.reload(auth_module)


class TestSamlSignature:
    """SEC-02: SAML assertions require valid IdP signatures."""

    def test_config_requires_signed_assertions(self):
        """The pysaml2 SP config dict has want_assertions_signed=True.

        We inspect the config dict built by get_saml_client rather than
        calling pysaml2 (which would need IdP metadata and xmlsec1).
        """
        from unittest.mock import call
        import app.saml_client as saml_mod

        # Capture the config dict passed to Saml2Config.load()
        with patch.object(saml_mod, "_find_xmlsec1", return_value="/usr/bin/xmlsec1"), \
             patch.object(saml_mod, "_generate_self_signed_cert"), \
             patch("app.saml_client.Path") as mock_path_cls, \
             patch.object(saml_mod, "Saml2Config") as mock_conf_cls, \
             patch.object(saml_mod, "Saml2Client"), \
             patch.object(saml_mod, "get_config_value", side_effect=lambda db, key: {
                 "saml_entity_id": "https://test.example.com",
                 "saml_idp_metadata_url": "https://idp.example.com/metadata",
                 "saml_cert_dir": "/tmp/test-certs",
             }.get(key, "")):
            # Make cert path checks pass
            mock_path_inst = MagicMock()
            mock_path_inst.__truediv__ = MagicMock(return_value=mock_path_inst)
            mock_path_inst.exists.return_value = True
            mock_path_inst.__str__ = MagicMock(return_value="/tmp/test-certs/sp.key")
            mock_path_cls.return_value = mock_path_inst

            mock_db = MagicMock()
            saml_mod.get_saml_client(mock_db)

            # Extract the config dict passed to Saml2Config().load()
            load_call = mock_conf_cls.return_value.load
            assert load_call.called, "Saml2Config.load() was not called"
            config_dict = load_call.call_args[0][0]
            sp_config = config_dict["service"]["sp"]
            assert sp_config["want_assertions_signed"] is True, \
                f"want_assertions_signed should be True, got {sp_config['want_assertions_signed']}"

    def test_signature_failure_logs_security_warning(self, client, db, seed_saml_mode, caplog):
        """Signature validation failure at ACS logs WARNING with [SECURITY] prefix."""
        # Simulate a SAML response that triggers a signature error
        with patch("app.api.saml_routes.get_saml_client") as mock_client:
            mock_client.return_value.parse_authn_request_response.side_effect = \
                Exception("Signature verification failed")

            with caplog.at_level(logging.WARNING):
                response = client.post(
                    "/api/saml/acs",
                    data={"SAMLResponse": "dummybase64", "RelayState": "/"},
                    follow_redirects=False,
                )

            # Should redirect to login (not crash)
            assert response.status_code == 302
            assert "error=auth_failed" in response.headers["location"]

            # Verify [SECURITY] log was emitted at WARNING level
            security_logs = [
                r for r in caplog.records
                if "[SECURITY]" in r.getMessage() and r.levelno == logging.WARNING
            ]
            assert len(security_logs) >= 1, \
                f"Expected [SECURITY] WARNING log for signature failure. Got logs: {[r.getMessage() for r in caplog.records]}"

    def test_non_signature_error_logs_at_error_level(self, client, db, seed_saml_mode, caplog):
        """Non-signature exceptions at ACS still log at ERROR level (not WARNING)."""
        with patch("app.api.saml_routes.get_saml_client") as mock_client:
            mock_client.return_value.parse_authn_request_response.side_effect = \
                Exception("Some other pysaml2 failure")

            with caplog.at_level(logging.DEBUG):
                response = client.post(
                    "/api/saml/acs",
                    data={"SAMLResponse": "dummybase64", "RelayState": "/"},
                    follow_redirects=False,
                )

            assert response.status_code == 302

            # Should NOT have [SECURITY] prefix
            security_logs = [
                r for r in caplog.records
                if "[SECURITY]" in r.getMessage()
            ]
            assert len(security_logs) == 0, \
                f"Non-signature errors should not produce [SECURITY] logs. Got: {[r.getMessage() for r in caplog.records]}"

            # Should have ERROR level log
            error_logs = [
                r for r in caplog.records
                if r.levelno == logging.ERROR and "SAML ACS processing failed" in r.getMessage()
            ]
            assert len(error_logs) >= 1, \
                f"Expected ERROR log for non-signature failure. Got logs: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
