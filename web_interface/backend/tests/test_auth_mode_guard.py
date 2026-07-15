"""Fail-closed guard against password-less simple auth on a deployed instance (#111).

`simple` auth mode logs users in by email against an allowlist with no
credential (see auth_routes.login). That is fine for local dev but must never
be the effective mode on a real deployment, where auth_config.yaml is expected
to render auth.mode=saml. The guard refuses to boot in that case unless an
operator explicitly opts in via CVICHE_ALLOW_SIMPLE_AUTH=1.
"""
import pytest

from app.main import _guard_deployed_auth_mode, SECURE_AUTH_MODES


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


@pytest.mark.parametrize("auth_mode", ["", None, "   "])
def test_missing_auth_mode_defaults_to_simple_and_is_guarded(auth_mode):
    # The guard owns the missing -> "simple" default (#277): lifespan() now
    # passes get_config_value(db, "auth_mode") RAW, so None reaches here when the
    # key is unset. Empty, blank and None must all normalize to simple and still
    # be caught on a deployment.
    with pytest.raises(RuntimeError):
        _guard_deployed_auth_mode(auth_mode, "s3", allow_simple=False)


def test_missing_auth_mode_on_local_dev_is_fine():
    # Same raw-None input, but not a deployment -> must not raise.
    _guard_deployed_auth_mode(None, "local", allow_simple=False)


# The guard is an ALLOWLIST: any mode not in SECURE_AUTH_MODES fails closed on a
# deployment, so an unrecognized/future mode can't slip through unblocklisted
# (mrj4001 follow-up on #111).
@pytest.mark.parametrize("auth_mode", ["none", "dev", "oidc", "basic", "saaml"])
def test_unknown_mode_on_deployed_s3_fails_closed(auth_mode):
    # "oidc" is intentionally included: it is NOT implemented, so it must fail
    # closed until it is added to SECURE_AUTH_MODES with a real credential path.
    #
    # This is the "unknown mode fails closed on S3" check from #277 -- it already
    # covered that case, over five modes rather than one, so it is asserted here
    # rather than duplicated into a second single-mode test.
    assert auth_mode not in SECURE_AUTH_MODES
    with pytest.raises(RuntimeError, match="not in SECURE_AUTH_MODES") as exc:
        _guard_deployed_auth_mode(auth_mode, "s3", allow_simple=False)
    assert "[SECURITY]" in str(exc.value)


def test_unknown_mode_error_names_the_remediation():
    """The operator reading this at 2am must be told where to fix it (#277)."""
    with pytest.raises(RuntimeError) as exc:
        _guard_deployed_auth_mode("none", "s3", allow_simple=False)
    msg = str(exc.value)
    assert "update SECURE_AUTH_MODES in app/main.py" in msg
    assert "only after it is implemented and verified" in msg


@pytest.mark.parametrize("auth_mode", ["none", "dev", "oidc"])
def test_unknown_mode_on_local_dev_is_fine(auth_mode):
    # Not a deployment -> the guard never fires, whatever the mode.
    _guard_deployed_auth_mode(auth_mode, "local", allow_simple=False)


@pytest.mark.parametrize("auth_mode", ["Oidc", " none ", "DEV"])
def test_unknown_mode_noncanonical_still_guarded(auth_mode):
    # Stray case/whitespace must not let an unknown mode bypass the allowlist.
    with pytest.raises(RuntimeError):
        _guard_deployed_auth_mode(auth_mode, "s3", allow_simple=False)


def test_only_saml_is_currently_allowed():
    # Guards against someone widening the allowlist to an unimplemented mode.
    assert SECURE_AUTH_MODES == frozenset({"saml"})
