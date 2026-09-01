"""SAML 2.0 Service Provider endpoints."""
import logging
import os

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response
from saml2 import SAMLError
from saml2.mdstore import SourceNotFound
from saml2.metadata import create_metadata_string
from saml2.sigver import SigverError, CertificateError
from saml2.response import IncorrectlySigned
from sqlalchemy.orm import Session

from app.database import get_db
from app.auth import (
    create_session_cookie,
    decode_session_cookie,
    get_cookie_settings,
    get_cookie_delete_settings,
    resolve_session_identity,
    SessionEpochUnreadable,
    COOKIE_NAME,
)
from app.session_idle import get_idle_store, SessionStoreUnavailable
from app.config_loader import get_config_value
from app.saml_client import get_saml_client, extract_user_attrs
from app.saml_replay import get_replay_cache, assertion_ids, replay_ttl, replay_fail_closed
from app.ed_group_lookup import check_ed_membership, EdUnavailableError, LDAPConfig
from pydantic import SecretStr
from app.services.user_service import provision_user, normalize_email
from app.redirect_safety import safe_relative_path
from app.audit_events import (
    LOGIN_SUCCESS,
    LOGIN_FAILED,
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

# SAML binding URIs (string literals to avoid import coupling)
BINDING_HTTP_REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
BINDING_HTTP_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"


# ---------------------------------------------------------------------------
# GET /api/saml/login -- redirect to IdP
# ---------------------------------------------------------------------------
@router.get("/saml/login")
def saml_login(request: Request, db: Session = Depends(get_db)):
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
        return RedirectResponse("/login?error=auth_failed", status_code=302)
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
        return RedirectResponse("/login?error=auth_failed", status_code=302)


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


def _saml_acs_process(form: dict, db: Session):
    """Synchronous body of saml_acs() -- SAML parse, replay/ED checks,
    provisioning, session creation. See saml_acs's docstring."""
    try:
        saml_response = form.get("SAMLResponse", "")
        # CWE-601: the IdP echoes RelayState back verbatim; validate it as a
        # same-site relative path before using it as the post-auth redirect.
        relay_state = safe_relative_path(form.get("RelayState"))

        client = get_saml_client(db)
        authn_response = client.parse_authn_request_response(
            saml_response, BINDING_HTTP_POST
        )

        if authn_response is None:
            logger.warning("SAML ACS: authn_response is None (invalid assertion)")
            return RedirectResponse("/login?error=auth_failed", status_code=302)

        # Replay gate: allow_unsolicited=True (required for IdP-initiated SSO)
        # means pysaml2 never matches InResponseTo, so a captured signed
        # response would otherwise replay until its NotOnOrAfter lapses. Each
        # assertion ID is accepted exactly once (see app/saml_replay.py).
        ids = assertion_ids(authn_response)
        replay_cache = get_replay_cache()
        if ids:
            if not replay_cache.check_and_record(ids, replay_ttl(authn_response)):
                # Redirect like every other ACS failure: the benign replay case
                # is a human re-POSTing the ACS form (back button), not an attacker.
                logger.warning("[SECURITY] SAML assertion replay rejected (ID already presented)")
                return RedirectResponse("/login?error=auth_failed", status_code=302)
        elif replay_fail_closed():
            # Real pysaml2 responses always carry assertion IDs; a missing ID
            # means replay can't be verified, so reject when configured to fail
            # closed (prod).
            logger.warning("[SECURITY] SAML assertion carried no ID; failing closed -- rejecting")
            return RedirectResponse("/login?error=auth_failed", status_code=302)
        else:
            # No ID and failing open: only stubbed parsers land here in practice.
            logger.warning("SAML ACS: no assertion ID extractable; replay gate skipped (fail open)")

        identity = authn_response.get_identity()
        attrs = extract_user_attrs(identity)
        # email is the unique identity key; normalize once so the ED membership
        # check, its cache, and provisioning all agree on casing (#348).
        attrs["email"] = normalize_email(attrs["email"])

    except ValueError as e:
        # Missing required attribute (e.g., mail)
        logger.warning("SAML ACS: missing attributes -- %s", str(e))
        return RedirectResponse("/login?error=missing_attributes", status_code=302)
    except (SigverError, CertificateError, IncorrectlySigned) as e:
        # SEC-02: signature/validation failures get the [SECURITY] prefix.
        # Narrow exception types, not string-matching str(e) -- pysaml2's
        # exception message text isn't a stable API across dependency
        # versions. Verified against the installed pysaml2: SigverError is
        # the base of SignatureError/BadSignature/MissingKey/XmlsecError;
        # CertificateError is its own separate root (not a SigverError
        # subclass); IncorrectlySigned is response.py's distinct
        # signature-failure type. Together these are pysaml2's full
        # signature/cert-validation exception surface.
        logger.warning("[SECURITY] SAML signature validation failed: %s", str(e))
        return RedirectResponse("/login?error=auth_failed", status_code=302)
    except (RuntimeError, OSError, SAMLError, SourceNotFound) as e:
        # Same taxonomy as saml_login (get_saml_client() is the shared call
        # that can raise RuntimeError/OSError/SourceNotFound), plus
        # SAMLError for pysaml2's other real response-processing errors not
        # already narrowed above -- e.g. StatusError (IdP reported an error
        # status) and VerificationError (audience/recipient/conditions
        # check failed). CertificateError is intentionally omitted from
        # this tuple: it's already caught above alongside the
        # signature-specific exceptions.
        #
        # A genuinely unexpected exception still propagates rather than
        # redirecting -- see saml_login's comment for why (mrj4001 review,
        # PR #656 item 6 / #672).
        logger.error("SAML ACS processing failed: %s", e, exc_info=True)
        return RedirectResponse("/login?error=auth_failed", status_code=302)

    # ED group authorization check (if enabled)
    ed_enabled = get_config_value(db, "ed_enabled")
    membership = None
    if ed_enabled:
        from app.config_loader import get_config

        ed_access_group = get_config_value(db, "ed_access_group") or ""
        ed_admin_group = get_config_value(db, "ed_admin_group") or ""
        ldap_url, source = get_config("ldap", "ED_LDAP_URL", default="")
        bind_dn, source = get_config("ldap", "ED_LDAP_BIND_DN", default="")
        bind_password = os.environ.get("ED_LDAP_BIND_PASSWORD", "")
        if not ldap_url or not bind_dn or not ed_access_group.strip():
            # An unset access group would otherwise reach LDAP as an empty
            # search_base; check_ed_membership now rejects it outright, so guard
            # here and fail closed with the same operator-visible error.
            logger.error(
                "ED not configured (need ED_LDAP_URL, ED_LDAP_BIND_DN, ed_access_group)"
            )
            return RedirectResponse("/login?error=directory_unavailable", status_code=302)

        ldap_cfg = LDAPConfig(
            ldap_url=ldap_url, bind_dn=bind_dn, bind_password=SecretStr(bind_password)
        )
        try:
            # use_cache=False: the login path is the stronger gate, so it reads
            # NEITHER cache -- not the 5-minute live one, not the 30-minute
            # stale one -- and always queries ED. A user removed from ED three
            # minutes ago must not be able to START a new session off a warm
            # live entry, nor off a last-known-good answer during an outage.
            # (The per-request re-check in auth.py keeps use_cache=True.)
            # check_ed_membership still WRITES both caches on success, so there
            # is no set_cached_membership here and the login keeps warming the
            # cache the per-request checks read.
            membership = check_ed_membership(
                cwid=attrs["cwid"],
                access_group=ed_access_group,
                admin_group=ed_admin_group,
                cfg=ldap_cfg,
                use_cache=False,
            )
            if not membership.in_access_group:
                logger.warning("SAML ACS: user %s not in ED access group", attrs["cwid"])
                logger.info(
                    LOGIN_FAILED,
                    extra={"cwid": attrs["cwid"], "reason": "not_authorized"},
                )
                return RedirectResponse("/login?error=not_authorized", status_code=302)
        except EdUnavailableError:
            logger.error("ED unavailable during SAML login for %s", attrs["cwid"], exc_info=True)
            return RedirectResponse("/login?error=directory_unavailable", status_code=302)

    # Determine role based on ED groups (if enabled) or preserve existing
    if ed_enabled and membership is not None:
        user_role = "admin" if membership.in_admin_group else "user"
    else:
        user_role = None  # Don't override existing role when ED not enabled

    # JIT User Provisioning (upsert) -- anchored on cwid, email optional
    user = provision_user(
        db=db,
        cwid=attrs["cwid"],
        email=attrs["email"],
        display_name=attrs["display_name"],
        auth_method="saml",
        role=user_role,
    )

    # Mint before building the response: a session store that cannot be written
    # (or an unreadable epoch) is a login that cannot succeed, and issuing the
    # cookie anyway hands the user a credential no later request can resolve
    # (#657 review, thread 16). No cookie is set on either failure path.
    try:
        token = create_session_cookie(user, db)
    except SessionStoreUnavailable:
        logger.error("Session store unavailable during SAML login for %s",
                     attrs["cwid"], exc_info=True)
        logger.info(
            LOGIN_FAILED,
            extra={"cwid": attrs["cwid"], "reason": "session_store_unavailable"},
        )
        logger.info(SESSION_STORE_UNAVAILABLE, extra={"reason": "start"})
        return RedirectResponse(_STORE_UNAVAILABLE_REDIRECT, status_code=302)
    except SessionEpochUnreadable:
        logger.error("Session epoch unreadable during SAML login for %s",
                     attrs["cwid"], exc_info=True)
        logger.info(
            LOGIN_FAILED,
            extra={"cwid": attrs["cwid"], "reason": "session_state_unavailable"},
        )
        return RedirectResponse(_STATE_UNAVAILABLE_REDIRECT, status_code=302)

    # relay_state is already a validated, non-empty same-site path.
    response = RedirectResponse(relay_state, status_code=302)
    response.set_cookie(value=token, **get_cookie_settings())

    logger.info(
        LOGIN_SUCCESS,
        extra={
            "user_id": user.id,
            "email": user.email,
            "role": user.role,
            "auth_method": "saml",
        },
    )

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
