"""SAML 2.0 Service Provider endpoints."""
import logging
import os
import yaml

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from saml2.metadata import create_metadata_string
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.auth import create_session_cookie, decode_session_cookie, get_cookie_settings, get_session_epoch, COOKIE_NAME
from app.session_idle import get_idle_store
from app.config_loader import get_config_value
from app.saml_client import get_saml_client, extract_user_attrs
from app.ed_group_lookup import check_ed_membership, set_cached_membership, EdUnavailableError
from app.services.user_service import provision_user
from app.redirect_safety import safe_relative_path

logger = logging.getLogger(__name__)
router = APIRouter()

# SAML binding URIs (string literals to avoid import coupling)
BINDING_HTTP_REDIRECT = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect"
BINDING_HTTP_POST = "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST"


# ---------------------------------------------------------------------------
# GET /api/saml/login -- redirect to IdP
# ---------------------------------------------------------------------------
@router.get("/saml/login")
async def saml_login(request: Request, db: Session = Depends(get_db)):
    """Initiate SAML login -- redirect user to IdP for authentication."""
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
    except Exception:
        logger.error("SAML login initiation failed", exc_info=True)
        return RedirectResponse("/login?error=auth_failed", status_code=302)


# ---------------------------------------------------------------------------
# POST /api/saml/acs -- Assertion Consumer Service
# ---------------------------------------------------------------------------
@router.post("/saml/acs")
async def saml_acs(request: Request, db: Session = Depends(get_db)):
    """SAML Assertion Consumer Service -- receives IdP response, provisions user, sets session."""
    # Mode guard
    auth_mode = get_config_value(db, "auth_mode") or "simple"
    if auth_mode != "saml":
        return RedirectResponse("/login?error=saml_not_enabled", status_code=302)

    try:
        form = await request.form()
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

        identity = authn_response.get_identity()
        attrs = extract_user_attrs(identity)

    except ValueError as e:
        # Missing required attribute (e.g., mail)
        logger.warning("SAML ACS: missing attributes -- %s", str(e))
        return RedirectResponse("/login?error=missing_attributes", status_code=302)
    except Exception as e:
        # SEC-02: Log signature/validation failures with [SECURITY] prefix
        err_msg = str(e).lower()
        if "signature" in err_msg or "signed" in err_msg:
            logger.warning(
                "[SECURITY] SAML signature validation failed: %s", str(e)
            )
        else:
            logger.error("SAML ACS processing failed", exc_info=True)
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
        if not ldap_url or not bind_dn:
            logger.error("ED LDAP credentials not configured (ED_LDAP_URL, ED_LDAP_BIND_DN)")
            return RedirectResponse("/login?error=directory_unavailable", status_code=302)

        try:
            membership = check_ed_membership(
                email=attrs["email"],
                access_group=ed_access_group,
                admin_group=ed_admin_group,
                ldap_url=ldap_url,
                bind_dn=bind_dn,
                bind_password=bind_password,
            )
            if not membership["in_access_group"]:
                logger.warning("SAML ACS: user %s not in ED access group", attrs["email"])
                return RedirectResponse("/login?error=not_authorized", status_code=302)
            # Cache the result for per-request checks
            set_cached_membership(attrs["email"], membership)
        except EdUnavailableError:
            logger.error("ED unavailable during SAML login for %s", attrs["email"], exc_info=True)
            return RedirectResponse("/login?error=directory_unavailable", status_code=302)

    # Determine role based on ED groups (if enabled) or preserve existing
    if ed_enabled and membership:
        user_role = "admin" if membership.get("in_admin_group") else "user"
    else:
        user_role = None  # Don't override existing role when ED not enabled

    # JIT User Provisioning (upsert)
    user = provision_user(
        db=db,
        email=attrs["email"],
        display_name=attrs["display_name"],
        auth_method="saml",
        role=user_role,
    )

    # Build redirect response with session cookie
    # relay_state is already a validated, non-empty same-site path.
    response = RedirectResponse(relay_state, status_code=302)
    token = create_session_cookie(user, get_session_epoch(db))
    cookie_settings = get_cookie_settings()
    response.set_cookie(value=token, **cookie_settings)
    return response


# ---------------------------------------------------------------------------
# GET /api/saml/metadata -- SP metadata XML
# ---------------------------------------------------------------------------
@router.get("/saml/metadata")
async def saml_metadata(db: Session = Depends(get_db)):
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
    except Exception:
        logger.error("SAML metadata generation failed", exc_info=True)
        return Response(content="SAML metadata not available", status_code=500)


# ---------------------------------------------------------------------------
# POST/GET /api/saml/logout -- local session logout
# ---------------------------------------------------------------------------
async def _saml_logout(request: Request):
    """Clear local session and redirect to login page (no IdP SLO round-trip)."""
    # Best-effort: drop the server-side idle key so the cleared cookie can't be
    # replayed before its absolute TTL lapses.
    cookie = request.cookies.get(COOKIE_NAME)
    payload = decode_session_cookie(cookie) if cookie else None
    if payload and payload.get("sid"):
        get_idle_store().end(payload["sid"])

    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(key=COOKIE_NAME, httponly=True, samesite="lax")
    return response


@router.post("/saml/logout")
async def saml_logout_post(request: Request):
    """SAML logout via POST."""
    return await _saml_logout(request)


@router.get("/saml/logout")
async def saml_logout_get(request: Request):
    """SAML logout via GET (browser convenience)."""
    return await _saml_logout(request)
