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


@pytest.mark.parametrize("storage_backend", ["S3", "s3 ", " S3", "s3\n"])
def test_noncanonical_s3_still_guarded(storage_backend):
    # Stray case/whitespace in the storage backend must NOT bypass the guard.
    with pytest.raises(RuntimeError):
        _guard_deployed_auth_mode("simple", storage_backend, allow_simple=False)


@pytest.mark.parametrize("auth_mode", ["Simple", "SIMPLE", "simple "])
def test_noncanonical_simple_still_guarded(auth_mode):
    # Stray case/whitespace in the auth mode must NOT bypass the guard.
    with pytest.raises(RuntimeError):
        _guard_deployed_auth_mode(auth_mode, "s3", allow_simple=False)


def test_missing_auth_mode_defaults_to_simple_and_is_guarded():
    # Caller passes "" (get_config_value(...) or "simple" upstream); an empty
    # mode on a deployment normalizes to simple and is still caught.
    with pytest.raises(RuntimeError):
        _guard_deployed_auth_mode("", "s3", allow_simple=False)
