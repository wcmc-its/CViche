"""Fail-closed guard against password-less simple auth on a deployed instance (#111).

`simple` auth mode logs users in by email against an allowlist with no
credential (see auth_routes.login). That is fine for local dev but must never
be the effective mode on a real deployment, where auth_config.yaml is expected
to render auth.mode=saml. The guard refuses to boot in that case unless an
operator explicitly opts in via CVICHE_ALLOW_SIMPLE_AUTH=1.
"""
import pytest

from app.main import _guard_deployed_auth_mode


def test_simple_on_deployed_s3_refuses_to_start():
    with pytest.raises(RuntimeError) as exc:
        _guard_deployed_auth_mode("simple", "s3", allow_simple=False)
    assert "[SECURITY]" in str(exc.value)


def test_simple_on_deployed_s3_allowed_with_override():
    # Explicit opt-in: warns but does not raise.
    _guard_deployed_auth_mode("simple", "s3", allow_simple=True)


def test_saml_on_deployed_s3_is_fine():
    _guard_deployed_auth_mode("saml", "s3", allow_simple=False)


def test_simple_on_local_dev_is_fine():
    # Local storage == not a deployment; simple auth is expected here.
    _guard_deployed_auth_mode("simple", "local", allow_simple=False)
