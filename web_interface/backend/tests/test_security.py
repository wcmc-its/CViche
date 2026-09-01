"""Security regression tests for SEC-01 through SEC-07."""
import json
import logging
import os
import importlib
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock


@pytest.fixture(autouse=True)
def _restore_auth_module():
    """Several SEC-03 tests reload app.auth to prove it reads the session secret
    at import. importlib.reload rebinds app.auth's module-level functions
    (get_current_user, require_admin, ...) to NEW objects, which no longer match
    the references the route modules captured via `from app.auth import ...` at
    import time. Any later test that overrides those deps by re-importing them
    from app.auth then silently misses (FastAPI matches overrides by object
    identity) and the request falls through to real auth -> 401. Snapshot the
    module namespace and restore it after each test so a reload here cannot leak
    into the rest of the suite.
    """
    import app.auth as auth_module
    saved = dict(auth_module.__dict__)
    yield
    auth_module.__dict__.clear()
    auth_module.__dict__.update(saved)


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

    def test_placeholder_secret_raises_on_module_load(self):
        """A template placeholder secret hard-fails boot, same as a missing one."""
        import app.auth as auth_module
        with patch.dict(os.environ, {"CVICHE_SESSION_SECRET": "change-me-to-a-long-random-string"}):
            with pytest.raises(RuntimeError, match="placeholder"):
                importlib.reload(auth_module)
        importlib.reload(auth_module)

    @pytest.mark.parametrize("placeholder", [
        "changeme",
        "change-me",
        "secret",
        "dev-secret",
        "change-me-to-a-long-random-string",   # backend/.env.example
        "dev-secret-change-in-production",     # web_interface/docker-compose.yml
        "ChangeMe",                            # match is case-insensitive
        "  secret  ",                          # and whitespace-insensitive
    ])
    def test_known_placeholder_values_rejected(self, placeholder):
        from app.auth import _validate_session_secret
        with pytest.raises(RuntimeError, match="placeholder"):
            _validate_session_secret(placeholder)

    def test_short_secret_warns_but_does_not_fail(self, caplog):
        """Length is only a warning: hard-failing on it could take down a live
        deployment whose real (non-placeholder) secret is merely short."""
        from app.auth import _validate_session_secret
        with caplog.at_level(logging.WARNING, logger="app.auth"):
            assert _validate_session_secret("short-but-real") == "short-but-real"
        assert any("[SECURITY]" in r.getMessage() for r in caplog.records), \
            "expected a [SECURITY] warning for a <32-char secret"

    def test_strong_secret_accepted_silently(self, caplog):
        from app.auth import _validate_session_secret
        secret = "f" * 64
        with caplog.at_level(logging.WARNING, logger="app.auth"):
            assert _validate_session_secret(secret) == secret
        assert not [r for r in caplog.records if "[SECURITY]" in r.getMessage()]

    def test_short_secret_fails_closed_outside_development(self):
        """Same weak secret that only warns in `development` must refuse to
        boot anywhere else -- a live deployment shouldn't run brute-forceable
        session cookies just because nobody rotated the default."""
        from app.auth import _validate_session_secret
        for env in ("production", "staging", "anything-not-development"):
            with pytest.raises(RuntimeError, match="shorter than 32"):
                _validate_session_secret("short-but-real", env)

    def test_strong_secret_accepted_in_production(self):
        from app.auth import _validate_session_secret
        secret = "f" * 64
        assert _validate_session_secret(secret, "production") == secret


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
                 # sp_base_url is a hard precondition: get_saml_client raises if
                 # it is unset, short-circuiting before the signature-requirement
                 # check below. Seed it so the test reaches that assertion.
                 "saml_sp_base_url": "https://test.example.com",
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
        """Signature validation failure at ACS logs WARNING with [SECURITY] prefix.

        Raises the real pysaml2 exception type (#656 review response,
        2026-08-19: saml_acs() now catches specific pysaml2 exception types
        instead of string-matching str(e), so a stand-in generic Exception
        with matching text no longer exercises this path -- the real type
        does)."""
        from saml2.sigver import SignatureError
        # Simulate a SAML response that triggers a signature error
        with patch("app.api.saml_routes.get_saml_client") as mock_client:
            mock_client.return_value.parse_authn_request_response.side_effect = \
                SignatureError("Signature verification failed")

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
        """Non-signature exceptions at ACS still log at ERROR level (not WARNING).

        Raises a real, non-signature pysaml2 exception type (PR #656 review
        item 6 / #672: saml_acs()'s final except now catches
        RuntimeError/OSError/SAMLError/SourceNotFound instead of bare
        Exception, so a stand-in generic Exception no longer exercises this
        path -- a genuinely unexpected exception type now propagates instead,
        see test_acs_unexpected_exception_propagates in test_saml_sp.py).
        StatusError is pysaml2's own exception for an IdP-reported error
        SAML Status -- a real, expected non-signature failure, and a
        saml2.SAMLError subclass."""
        from saml2.response import StatusError
        with patch("app.api.saml_routes.get_saml_client") as mock_client:
            mock_client.return_value.parse_authn_request_response.side_effect = \
                StatusError("Some other pysaml2 failure")

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


class TestPathTraversal:
    """SEC-01: File download endpoints reject path traversal and absolute paths."""

    def _create_test_user_and_run(self, db, role="user"):
        """Helper to create a user and run for file access tests."""
        from app.models import User, Run
        user = User(email="test@example.com", display_name="Test User", role=role)
        db.add(user)
        db.commit()
        db.refresh(user)

        run = Run(
            id="test-run-123",
            user_id=user.id,
            status="completed",
            filename="test.docx",
            file_type="docx",
        )
        db.add(run)
        db.commit()
        return user, run

    def _auth_cookie(self, client, user):
        """Set a valid session cookie on the test client."""
        from app.auth import create_session_cookie, COOKIE_NAME
        cookie_value = create_session_cookie(user)
        client.cookies.set(COOKIE_NAME, cookie_value)

    def test_absolute_path_rejected(self, client, db, seed_simple_mode):
        """GET /run/{id}/data//etc/passwd returns 400."""
        user, run = self._create_test_user_and_run(db)
        self._auth_cookie(client, user)

        response = client.get(f"/api/run/{run.id}/data//etc/passwd")
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid filename"

    def test_traversal_rejected(self, client, db, seed_simple_mode):
        """URL-encoded traversal (..%2F) in filename returns 400.

        Note: literal ../../ in the URL path is resolved by the HTTP framework
        before reaching the route handler, so we test the URL-encoded form
        which does reach _resolve_safe_path.
        """
        user, run = self._create_test_user_and_run(db)
        self._auth_cookie(client, user)

        response = client.get(f"/api/run/{run.id}/data/..%2F..%2Fetc%2Fpasswd")
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid filename"

    def test_json_endpoint_absolute_path_rejected(self, client, db, seed_simple_mode):
        """GET /run/{id}/json//etc/passwd returns 400. (Admin: /json is admin-only,
        and traversal must still be rejected even for admins.)"""
        user, run = self._create_test_user_and_run(db, role="admin")
        self._auth_cookie(client, user)

        response = client.get(f"/api/run/{run.id}/json//etc/passwd")
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid filename"

    def test_json_endpoint_traversal_rejected(self, client, db, seed_simple_mode):
        """URL-encoded traversal in JSON endpoint returns 400 (admin-authenticated)."""
        user, run = self._create_test_user_and_run(db, role="admin")
        self._auth_cookie(client, user)

        response = client.get(f"/api/run/{run.id}/json/..%2F..%2Fetc%2Fpasswd")
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid filename"

    def test_valid_relative_path_returns_404_when_file_missing(self, client, db, seed_simple_mode):
        """A valid relative path that doesn't exist returns 404, not 400. Uses a
        .docx (owner-accessible) so this exercises path resolution, not the
        admin-only .json gate."""
        user, run = self._create_test_user_and_run(db)
        self._auth_cookie(client, user)

        response = client.get(f"/api/run/{run.id}/data/nonexistent.docx")
        assert response.status_code == 404

    def test_error_message_reveals_no_internal_paths(self, client, db, seed_simple_mode):
        """Error responses contain no internal file paths."""
        user, run = self._create_test_user_and_run(db)
        self._auth_cookie(client, user)

        # Absolute path attempt
        response = client.get(f"/api/run/{run.id}/data//etc/passwd")
        body = response.json()
        assert "/etc/passwd" not in str(body)
        assert "outputs" not in str(body).lower()

        # Traversal attempt (URL-encoded)
        response = client.get(f"/api/run/{run.id}/data/..%2F..%2Fetc%2Fpasswd")
        body = response.json()
        assert "/etc" not in str(body)

    def test_security_log_emitted_on_absolute_path(self, client, db, seed_simple_mode, caplog):
        """Blocked absolute path attempts are logged with [SECURITY] prefix."""
        user, run = self._create_test_user_and_run(db)
        self._auth_cookie(client, user)

        with caplog.at_level(logging.WARNING):
            client.get(f"/api/run/{run.id}/data//etc/passwd")

        security_logs = [r for r in caplog.records if "[SECURITY]" in r.getMessage()]
        assert len(security_logs) >= 1, "Expected [SECURITY] log for blocked absolute path"

    def test_security_log_emitted_on_traversal(self, client, db, seed_simple_mode, caplog):
        """Blocked traversal attempts are logged with [SECURITY] prefix."""
        user, run = self._create_test_user_and_run(db)
        self._auth_cookie(client, user)

        with caplog.at_level(logging.WARNING):
            client.get(f"/api/run/{run.id}/data/..%2F..%2Fetc%2Fpasswd")

        security_logs = [r for r in caplog.records if "[SECURITY]" in r.getMessage()]
        assert len(security_logs) >= 1, "Expected [SECURITY] log for blocked traversal"


class TestErrorSanitization:
    """SEC-05: Error responses contain no internal details."""

    def test_unhandled_exception_returns_generic_error(self, client, db, seed_simple_mode):
        """Unhandled exceptions return sanitized JSON, not stack traces."""
        from app.main import app

        @app.get("/test-500-trigger")
        async def trigger_error():
            raise RuntimeError("Internal detail: /usr/local/lib/python3.14/secret.py line 42")

        response = client.get("/test-500-trigger")
        assert response.status_code == 500
        body = response.json()
        assert body["error"] == "internal_error"
        assert body["message"] == "An unexpected error occurred."

    def test_no_traceback_in_production_error(self, client, db, seed_simple_mode):
        """Production error responses contain no traceback, file paths, or .py references."""
        from app.main import app

        @app.get("/test-500-no-trace")
        async def trigger_error_trace():
            raise ValueError("something broke in /app/secret.py")

        with patch.dict(os.environ, {"CVICHE_DEBUG": ""}, clear=False):
            response = client.get("/test-500-no-trace")
        body_str = json.dumps(response.json())
        assert "Traceback" not in body_str
        assert "File " not in body_str
        assert ".py" not in body_str

    def test_debug_mode_includes_traceback(self, client, db, seed_simple_mode):
        """With CVICHE_DEBUG=true, error responses include traceback."""
        from app.main import app

        @app.get("/test-500-debug")
        async def trigger_debug_error():
            raise RuntimeError("debug test error")

        with patch.dict(os.environ, {"CVICHE_DEBUG": "true"}, clear=False):
            response = client.get("/test-500-debug")
        body = response.json()
        assert body["error"] == "internal_error"
        assert "traceback" in body
        assert "debug test error" in body["message"]

    def test_http_exception_not_swallowed(self, client, db, seed_simple_mode):
        """FastAPI HTTPException still returns its own status and detail."""
        response = client.get("/api/nonexistent-route-xyz")
        assert response.status_code in (404, 405)
        body = response.json()
        assert body.get("error") != "internal_error"


class TestSecurityHeaders:
    """SEC-06: HTTP security headers on all responses."""

    def test_csp_header_present(self, client, db, seed_simple_mode):
        """All responses include Content-Security-Policy header."""
        response = client.get("/health")
        assert "content-security-policy" in {k.lower() for k in response.headers.keys()}
        csp = response.headers["content-security-policy"]
        assert "default-src 'self'" in csp

    def test_csp_allows_websockets(self, client, db, seed_simple_mode):
        """CSP connect-src allows WebSocket protocols."""
        response = client.get("/health")
        csp = response.headers["content-security-policy"]
        assert "connect-src" in csp
        assert "ws:" in csp
        assert "wss:" in csp

    def test_csp_blocks_inline_scripts(self, client, db, seed_simple_mode):
        """CSP script-src does NOT allow unsafe-inline."""
        response = client.get("/health")
        csp = response.headers["content-security-policy"]
        for directive in csp.split(";"):
            if "script-src" in directive:
                assert "unsafe-inline" not in directive
                break

    def test_x_frame_options_deny(self, client, db, seed_simple_mode):
        """X-Frame-Options is DENY."""
        response = client.get("/health")
        assert response.headers["x-frame-options"] == "DENY"

    def test_x_content_type_options_nosniff(self, client, db, seed_simple_mode):
        """X-Content-Type-Options is nosniff."""
        response = client.get("/health")
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_hsts_header_present(self, client, db, seed_simple_mode):
        """Strict-Transport-Security header is present with max-age."""
        response = client.get("/health")
        hsts = response.headers["strict-transport-security"]
        assert "max-age=31536000" in hsts

    def test_hsts_no_preload(self, client, db, seed_simple_mode):
        """HSTS header does NOT include preload (premature for pre-production app)."""
        response = client.get("/health")
        hsts = response.headers["strict-transport-security"]
        assert "preload" not in hsts

    def test_referrer_policy(self, client, db, seed_simple_mode):
        """Referrer-Policy is strict-origin-when-cross-origin."""
        response = client.get("/health")
        assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"

    def test_headers_on_error_responses(self, client, db, seed_simple_mode):
        """Security headers are present even on 404 error responses."""
        response = client.get("/nonexistent-page")
        assert "x-frame-options" in {k.lower() for k in response.headers.keys()}
        assert "x-content-type-options" in {k.lower() for k in response.headers.keys()}


class TestCorsLockdown:
    """SEC-07: CORS uses explicit methods and headers, not wildcards."""

    def test_cors_methods_not_wildcard(self, client, db, seed_simple_mode):
        """CORS preflight does not return wildcard for allowed methods."""
        response = client.options(
            "/api/upload",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        allowed_methods = response.headers.get("access-control-allow-methods", "")
        assert "*" not in allowed_methods

    def test_cors_headers_not_wildcard(self, client, db, seed_simple_mode):
        """CORS preflight does not return wildcard for allowed headers."""
        response = client.options(
            "/api/upload",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        allowed_headers = response.headers.get("access-control-allow-headers", "")
        assert "*" not in allowed_headers

    def test_cors_allows_post_method(self, client, db, seed_simple_mode):
        """CORS preflight allows POST method (needed for uploads)."""
        response = client.options(
            "/api/upload",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        allowed_methods = response.headers.get("access-control-allow-methods", "")
        assert "POST" in allowed_methods

    def test_cors_allows_content_type_header(self, client, db, seed_simple_mode):
        """CORS preflight allows Content-Type header."""
        response = client.options(
            "/api/upload",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )
        allowed_headers = response.headers.get("access-control-allow-headers", "")
        assert "content-type" in allowed_headers.lower()


class TestCsrfExactOrigin:
    """CSRF Origin check compares parsed scheme+host+port exactly, not by
    prefix -- startswith() accepted lookalike extensions of an allowed origin."""

    def _post(self, client, origin):
        # The middleware runs before routing, so a nonexistent path isolates
        # the origin decision: 403 = rejected by CSRF, 404 = passed through.
        return client.post("/api/nonexistent-csrf-probe", headers={"Origin": origin})

    def test_exact_allowed_origin_passes(self, client, db, seed_simple_mode):
        assert self._post(client, "http://localhost:3000").status_code == 404

    def test_host_suffix_lookalike_rejected(self, client, db, seed_simple_mode):
        """Prefix comparison accepted this (it extends an allowed origin)."""
        resp = self._post(client, "http://localhost:3000.evil.com")
        assert resp.status_code == 403
        assert resp.json()["error"] == "forbidden"

    def test_port_extension_lookalike_rejected(self, client, db, seed_simple_mode):
        """Prefix comparison accepted :30001 because it starts with :3000."""
        assert self._post(client, "http://localhost:30001").status_code == 403

    def test_scheme_mismatch_rejected(self, client, db, seed_simple_mode):
        assert self._post(client, "https://localhost:3000").status_code == 403

    def test_unlisted_origin_rejected(self, client, db, seed_simple_mode):
        assert self._post(client, "https://evil.com").status_code == 403

    def test_unparseable_origin_rejected(self, client, db, seed_simple_mode):
        assert self._post(client, "null").status_code == 403

    def test_absent_origin_skips_check(self, client, db, seed_simple_mode):
        """Same-origin browser requests may omit Origin; behavior unchanged."""
        resp = client.post("/api/nonexistent-csrf-probe")
        assert resp.status_code == 404

    def test_get_requests_not_gated(self, client, db, seed_simple_mode):
        resp = client.get("/health", headers={"Origin": "https://evil.com"})
        assert resp.status_code == 200

    def test_origin_key_normalizes_default_ports(self):
        from app.main import _origin_key
        assert _origin_key("https://cviche.weill.cornell.edu") == \
            _origin_key("https://cviche.weill.cornell.edu:443")
        assert _origin_key("http://example.com") == _origin_key("http://example.com:80")
        assert _origin_key("http://example.com") != _origin_key("https://example.com")

    def test_origin_key_unparseable_is_none(self):
        from app.main import _origin_key
        for bad in ("null", "", "http://", "http://host:notaport"):
            assert _origin_key(bad) is None, bad


class TestDocsGating:
    """/docs, /redoc and /openapi.json are opt-in; default posture is off."""

    def test_docs_endpoints_disabled_by_default(self, client, db, seed_simple_mode):
        for path in ("/docs", "/redoc", "/openapi.json"):
            assert client.get(path).status_code == 404, path

    def test_root_does_not_advertise_docs(self, client, db, seed_simple_mode):
        assert client.get("/").json()["docs"] is None

    def test_enable_docs_env_flag(self, monkeypatch):
        from app.main import _docs_enabled
        monkeypatch.setenv("CVICHE_ENABLE_DOCS", "true")
        assert _docs_enabled() is True

    def test_debug_flag_does_not_enable_docs(self, monkeypatch):
        # CVICHE_DEBUG must NOT expose the API docs: an accidental debug flag in
        # prod should not enumerate the API surface. Docs require their own flag.
        from app.main import _docs_enabled
        monkeypatch.delenv("CVICHE_ENABLE_DOCS", raising=False)
        monkeypatch.setenv("CVICHE_DEBUG", "true")
        assert _docs_enabled() is False

    def test_docs_disabled_without_flags(self, monkeypatch):
        from app.main import _docs_enabled
        monkeypatch.delenv("CVICHE_ENABLE_DOCS", raising=False)
        monkeypatch.delenv("CVICHE_DEBUG", raising=False)
        assert _docs_enabled() is False


class TestUploadValidation:
    """SEC-04: Upload validation by magic bytes, size limit, and filename sanitization."""

    def _create_auth_user(self, client, db):
        """Create a user with consent and set auth cookie."""
        from app.models import User
        from app.auth import create_session_cookie, COOKIE_NAME

        user = User(
            email="test@example.com",
            display_name="Test User",
            role="user",
            consent_version="1.0",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        cookie_value = create_session_cookie(user)
        client.cookies.set(COOKIE_NAME, cookie_value)
        return user

    def test_spoofed_docx_with_pdf_content_rejected(self, client, db, seed_simple_mode):
        """A file with .docx extension but PDF magic bytes is rejected."""
        self._create_auth_user(client, db)
        pdf_content = b"%PDF-1.4 fake pdf content that is definitely not a docx"
        response = client.post(
            "/api/upload",
            files={"file": ("resume.docx", pdf_content, "application/octet-stream")},
        )
        assert response.status_code == 400
        assert "does not match .docx format" in response.json()["detail"]["message"]

    def test_spoofed_pdf_with_zip_content_rejected(self, client, db, seed_simple_mode):
        """A file with .pdf extension but ZIP magic bytes is rejected."""
        self._create_auth_user(client, db)
        zip_content = b"PK\x03\x04" + b"\x00" * 100
        response = client.post(
            "/api/upload",
            files={"file": ("resume.pdf", zip_content, "application/octet-stream")},
        )
        assert response.status_code == 400
        assert "does not match .pdf format" in response.json()["detail"]["message"]

    def test_valid_pdf_accepted(self, client, db, seed_simple_mode, tmp_path):
        """A legitimate PDF file is accepted."""
        self._create_auth_user(client, db)
        # Minimal valid PDF
        pdf_content = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
        # Patch UPLOAD_DIR to tmp_path so we don't pollute real uploads. Mock the
        # empty-document text extraction (covered separately in test_release_guards)
        # so this magic-byte-acceptance test isn't tripped by the text-bearing guard.
        with patch("app.api.upload.UPLOAD_DIR", tmp_path), \
             patch("app.api.upload._extract_text", return_value="x" * 600):
            response = client.post(
                "/api/upload",
                files={"file": ("my_cv.pdf", pdf_content, "application/pdf")},
            )
        assert response.status_code == 200
        data = response.json()
        assert data["filename"] == "my_cv.pdf"
        assert data["file_type"] == "pdf"

    def test_oversized_file_rejected(self, client, db, seed_simple_mode):
        """Files exceeding the size limit are rejected."""
        self._create_auth_user(client, db)
        pdf_header = b"%PDF-1.4"
        with patch("app.api.upload.MAX_UPLOAD_SIZE", 100):  # Set limit to 100 bytes for test
            big_content = pdf_header + b"\x00" * 200  # 208 bytes > 100 byte limit
            response = client.post(
                "/api/upload",
                files={"file": ("big.pdf", big_content, "application/pdf")},
            )
        assert response.status_code == 400
        assert "too large" in response.json()["detail"]["message"].lower()

    def test_randomized_filename_on_disk(self, client, db, seed_simple_mode, tmp_path):
        """Uploaded files are stored with randomized names, not the user-provided filename."""
        self._create_auth_user(client, db)
        pdf_content = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
        with patch("app.api.upload.UPLOAD_DIR", tmp_path), \
             patch("app.api.upload._extract_text", return_value="x" * 600):
            response = client.post(
                "/api/upload",
                files={"file": ("John_Doe_CV_2024.pdf", pdf_content, "application/pdf")},
            )
        assert response.status_code == 200
        run_id = response.json()["run_id"]
        # File on disk should be {run_id}.pdf, NOT contain "John_Doe"
        saved_files = list(tmp_path.iterdir())
        assert len(saved_files) == 1
        saved_name = saved_files[0].name
        assert saved_name == f"{run_id}.pdf"
        assert "John_Doe" not in saved_name

    def test_security_log_on_spoofed_upload(self, client, db, seed_simple_mode, caplog):
        """Spoofed upload attempts are logged with [SECURITY] prefix at WARNING level."""
        self._create_auth_user(client, db)
        pdf_content = b"%PDF-1.4 not a docx"
        with caplog.at_level(logging.WARNING):
            client.post(
                "/api/upload",
                files={"file": ("resume.docx", pdf_content, "application/octet-stream")},
            )
        security_logs = [r for r in caplog.records if "[SECURITY]" in r.getMessage()]
        assert len(security_logs) >= 1, "Expected [SECURITY] log for spoofed upload"

    def test_estimate_rejects_spoofed_file(self, client, db, seed_simple_mode):
        """The /estimate endpoint also validates magic bytes."""
        self._create_auth_user(client, db)
        pdf_content = b"%PDF-1.4 not a docx"
        response = client.post(
            "/api/estimate",
            files={"file": ("resume.docx", pdf_content, "application/octet-stream")},
        )
        assert response.status_code == 400
        assert "does not match .docx format" in response.json()["detail"]["message"]

    def test_random_bytes_rejected(self, client, db, seed_simple_mode):
        """A file with random bytes (not matching any format) is rejected."""
        self._create_auth_user(client, db)
        random_content = b"\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09" * 100
        response = client.post(
            "/api/upload",
            files={"file": ("resume.pdf", random_content, "application/pdf")},
        )
        assert response.status_code == 400
