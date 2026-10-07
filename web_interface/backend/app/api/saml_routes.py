"""SAML 2.0 Service Provider endpoints."""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from saml2 import SAMLError
from saml2.mdstore import SourceNotFound
from saml2.metadata import create_metadata_string
from saml2.sigver import CertificateError
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import (
    decode_session_cookie,
    get_cookie_settings,
    get_cookie_delete_settings,
    resolve_session_identity,
    COOKIE_NAME,
)
from app.session_idle import get_idle_store, SessionStoreUnavailable
from app.config_loader import get_config_value
from app.saml_client import get_saml_client
from app.services.saml_service import SamlLoginFailure, authenticate_saml_response
from app.redirect_safety import safe_relative_path
from app.audit_events import (
    SESSION_REVOKED,
    SESSION_STORE_UNAVAILABLE,
)

logger = logging.getLogger(__name__)

# Login-page error codes this module redirects with when a session cannot be
# minted or revoked; LoginPage.tsx renders a message for each (#657 review,
# threads 15/16).
_STORE_UNAVAILABLE_REDIRECT = "/login?error=session_store_unavailable"
_STATE_UNAVAILABLE_REDIRECT = "/login?error=session_state_unavailable"
_REVOCATION_FAILED_REDIRECT = "/login?error=session_revocation_failed"
router = APIRouter()

# Where the browser goes for each check the ACS workflow can refuse a session
# on. Every response-validation and replay-gate failure shares auth_failed.
_AUTH_FAILED_REDIRECT = "/login?error=auth_failed"
_ACS_FAILURE_REDIRECTS: dict[SamlLoginFailure, str] = {
    SamlLoginFailure.NO_RESPONSE: _AUTH_FAILED_REDIRECT,
    SamlLoginFailure.BAD_SIGNATURE: _AUTH_FAILED_REDIRECT,
    SamlLoginFailure.RESPONSE_REJECTED: _AUTH_FAILED_REDIRECT,
    SamlLoginFailure.PARSER_ERROR: _AUTH_FAILED_REDIRECT,
    SamlLoginFailure.WRONG_DESTINATION: _AUTH_FAILED_REDIRECT,
    SamlLoginFailure.MULTIPLE_ASSERTIONS: _AUTH_FAILED_REDIRECT,
    SamlLoginFailure.REPLAYED: _AUTH_FAILED_REDIRECT,
    SamlLoginFailure.NO_ASSERTION_ID: _AUTH_FAILED_REDIRECT,
    SamlLoginFailure.MISSING_ATTRIBUTES: "/login?error=missing_attributes",
    SamlLoginFailure.NOT_AUTHORIZED: "/login?error=not_authorized",
    SamlLoginFailure.DIRECTORY_UNAVAILABLE: "/login?error=directory_unavailable",
    SamlLoginFailure.SESSION_STORE_UNAVAILABLE: _STORE_UNAVAILABLE_REDIRECT,
    SamlLoginFailure.SESSION_STATE_UNAVAILABLE: _STATE_UNAVAILABLE_REDIRECT,
}

# SAML binding URI (string literal to avoid import coupling); the ACS's POST
# binding lives with the ACS workflow in app/services/saml_service.py.
BINDING_HTTP_REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"


# ---------------------------------------------------------------------------
# GET /api/saml/login -- redirect to IdP
# ---------------------------------------------------------------------------
@router.get("/saml/login")
def saml_login(request: Request, db: Session = Depends(get_db)) -> RedirectResponse:
    """Initiate SAML login -- redirect user to IdP for authentication.

    Plain `def`, not `async def`: this does synchronous DB/pysaml2 work and
    no `await`. FastAPI runs a sync path function in its threadpool
    automatically, exactly like it already does for the sync
    get_current_user dependency -- so this keeps the blocking work off the
    event loop with no async infrastructure change needed."""
    # Mode guard: only available in SAML mode
    auth_mode = get_config_value(db, "auth_mode") or "simple"
    if auth_mode != "saml":
        return RedirectResponse("/login?error=saml_not_enabled", status_code=302)

    try:
        client = get_saml_client(db)
        # CWE-601: only forward same-site relative paths as RelayState.
        relay_state = safe_relative_path(request.query_params.get("next"))
        reqid, info = client.prepare_for_authenticate(
            binding=BINDING_HTTP_REDIRECT, relay_state=relay_state
        )
        # Extract Location header from pysaml2 response
        for key, value in info["headers"]:
            if key == "Location":
                return RedirectResponse(value, status_code=302)
        # If no Location header found, something went wrong
        logger.error("SAML login: no Location header in prepare_for_authenticate response")
        return RedirectResponse(_AUTH_FAILED_REDIRECT, status_code=302)
    except (RuntimeError, OSError, SAMLError, CertificateError, SourceNotFound) as e:
        # RuntimeError: our own config-validation raises in get_saml_client()
        # (missing saml_sp_base_url, half-populated cert dir, no xmlsec1
        # binary). OSError: get_saml_client() -> Saml2Config.load() eagerly
        # fetches the remote IdP metadata over HTTP (verified against the
        # installed pysaml2: saml2.mdstore.MetadataStore.load() calls
        # MetaDataExtern.load() unconditionally); a network failure surfaces
        # as requests' ConnectionError/Timeout, both OSError subclasses.
        # SAMLError/CertificateError/SourceNotFound: pysaml2's own errors
        # building the AuthnRequest (e.g. SignOnError when the IdP metadata
        # supports neither binding) or fetching/parsing that metadata (a
        # SourceNotFound on a non-200 response). These are all real,
        # expected operational failure modes -- redirect like before.
        #
        # A genuinely unexpected exception (a bug, not an operational
        # failure) is deliberately NOT caught here: it propagates to
        # main.py's global exception handler, which logs the full traceback
        # server-side and returns a sanitized 500 to the client. Swallowing
        # it into "auth_failed" would mask a real bug as a routine login
        # failure (mrj4001 review, PR #656 item 6 / #672).
        logger.error("SAML login initiation failed: %s", e, exc_info=True)
        return RedirectResponse(_AUTH_FAILED_REDIRECT, status_code=302)


# ---------------------------------------------------------------------------
# POST /api/saml/acs -- Assertion Consumer Service
# ---------------------------------------------------------------------------
@router.post("/saml/acs")
async def saml_acs(request: Request, db: Session = Depends(get_db)):
    """SAML Assertion Consumer Service -- receives IdP response, provisions user, sets session.

    The only genuinely async step is reading the request body; everything
    after (pysaml2 parsing, LDAP, DB) is synchronous and runs via
    run_in_threadpool in _saml_acs_process, off the event loop, the same way
    FastAPI already threadpools a plain `def` route -- see saml_login's
    docstring."""
    # Mode guard
    auth_mode = get_config_value(db, "auth_mode") or "simple"
    if auth_mode != "saml":
        return RedirectResponse("/login?error=saml_not_enabled", status_code=302)

    form = await request.form()
    # dict(): cross into the threadpool with a plain dict, not Starlette's
    # FormData -- simplest thing that's safe to hand to another thread.
    return await run_in_threadpool(_saml_acs_process, dict(form), db)


def _saml_acs_process(form: dict, db: Session) -> RedirectResponse:
    """Synchronous body of saml_acs() -- SAML parse, replay/ED checks,
    provisioning, session creation (app/services/saml_service.py, #349).
    See saml_acs's docstring."""
    # CWE-601: the IdP echoes RelayState back verbatim; validate it as a
    # same-site relative path before using it as the post-auth redirect.
    relay_state = safe_relative_path(form.get("RelayState"))

    outcome = authenticate_saml_response(form.get("SAMLResponse", ""), db)
    if isinstance(outcome, SamlLoginFailure):
        return RedirectResponse(_ACS_FAILURE_REDIRECTS[outcome], status_code=302)

    # relay_state is already a validated, non-empty same-site path.
    response = RedirectResponse(relay_state, status_code=302)
    response.set_cookie(value=outcome.token, **get_cookie_settings())
    return response


# ---------------------------------------------------------------------------
# GET /api/saml/metadata -- SP metadata XML
# ---------------------------------------------------------------------------
@router.get("/saml/metadata")
def saml_metadata(db: Session = Depends(get_db)):
    """Serve SP metadata XML for IdP registration."""
    auth_mode = get_config_value(db, "auth_mode") or "simple"
    if auth_mode != "saml":
        return Response(content="SAML not enabled", status_code=404)

    try:
        client = get_saml_client(db)
         # pysaml2 7.x: build SP metadata via the module-level helper.
        # First arg `configfile` is required by signature but ignored when
        # `config=` is provided directly.
        metadata_str = create_metadata_string("", config=client.config)
        return Response(content=metadata_str, media_type="application/xml")
    except (RuntimeError, OSError, SAMLError, CertificateError, SourceNotFound) as e:
        # Same expected-failure taxonomy as saml_login (shared
        # get_saml_client() call), plus pysaml2's own metadata-building
        # errors from create_metadata_string(). This endpoint is
        # unauthenticated and IdP-facing, not a login attempt, but it's
        # still a security-relevant surface -- the same choice applies: a
        # genuinely unexpected exception propagates to the global handler
        # (sanitized 500, full traceback logged server-side) instead of
        # being folded into the same "not available" response an ops/config
        # problem gets (mrj4001 review, PR #656 item 6 / #672).
        logger.error("SAML metadata generation failed: %s", e, exc_info=True)
        return Response(content="SAML metadata not available", status_code=500)


# ---------------------------------------------------------------------------
# POST/GET /api/saml/logout -- local session logout
# ---------------------------------------------------------------------------
def _saml_logout(request: Request):
    """Clear local session and redirect to login page (no IdP SLO round-trip).

    Mirrors auth_routes.logout: the cookie is cleared on every path, but a
    server-side revocation that could not be completed is reported (via the
    login page's error code) rather than swallowed -- the session stays
    replayable until its absolute TTL otherwise (#657 review, thread 15).
    """
    cookie = request.cookies.get(COOKIE_NAME)
    payload = decode_session_cookie(cookie) if cookie else None

    try:
        identity = resolve_session_identity(payload) if payload else None
        if identity is not None and identity.sid:
            get_idle_store().end(identity.sid)
    except SessionStoreUnavailable:
        logger.error("Session store unavailable during SAML logout", exc_info=True)
        logger.info(SESSION_STORE_UNAVAILABLE, extra={"reason": "end"})
        failed = RedirectResponse(_REVOCATION_FAILED_REDIRECT, status_code=302)
        failed.delete_cookie(**get_cookie_delete_settings())
        return failed

    if identity is not None:
        logger.info(
            SESSION_REVOKED,
            extra={
                "user_id": identity.user_id,
                "email": identity.email,
                "reason": "user_logout",
            },
        )

    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(**get_cookie_delete_settings())
    return response


@router.post("/saml/logout")
def saml_logout_post(request: Request):
    """SAML logout via POST."""
    return _saml_logout(request)


@router.get("/saml/logout")
def saml_logout_get(request: Request):
    """SAML logout via GET.

    Not just "browser convenience" -- this is the endpoint registered with
    the IdP as the Single Logout Service, and it's registered with
    HTTP-Redirect binding (docs/sp-registration.md), which is IdP-initiated:
    the IdP sends the browser here via a 302, i.e. a GET, by the SAML
    standard's own binding. Removing GET or requiring POST-then-confirm
    would break that registered contract, not just a convenience link --
    reviewed and kept as-is (#674)."""
    return _saml_logout(request)
