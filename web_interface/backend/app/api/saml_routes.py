"""SAML 2.0 Service Provider endpoints."""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.auth import create_session_cookie, get_cookie_settings, COOKIE_NAME
from app.config_loader import get_config_value
from app.saml_client import get_saml_client, extract_user_attrs

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
        relay_state = request.query_params.get("next", "/")
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
        relay_state = form.get("RelayState", "/")

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
    except Exception:
        logger.error("SAML ACS processing failed", exc_info=True)
        return RedirectResponse("/login?error=auth_failed", status_code=302)

    # JIT User Provisioning (upsert)
    user = db.query(User).filter(User.email == attrs["email"]).first()
    if user:
        user.display_name = attrs["display_name"]
        user.auth_method = "saml"
    else:
        user = User(
            email=attrs["email"],
            display_name=attrs["display_name"],
            role="user",
            auth_method="saml",
        )
        db.add(user)
    db.commit()
    db.refresh(user)

    # Build redirect response with session cookie
    response = RedirectResponse(relay_state or "/", status_code=302)
    token = create_session_cookie(user)
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
        metadata_str = client.config.create_metadata_string()
        return Response(content=metadata_str, media_type="application/xml")
    except Exception:
        logger.error("SAML metadata generation failed", exc_info=True)
        return Response(content="SAML metadata not available", status_code=500)


# ---------------------------------------------------------------------------
# POST/GET /api/saml/logout -- local session logout
# ---------------------------------------------------------------------------
async def _saml_logout():
    """Clear local session and redirect to login page (no IdP SLO round-trip)."""
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie(key=COOKIE_NAME, httponly=True, samesite="lax")
    return response


@router.post("/saml/logout")
async def saml_logout_post():
    """SAML logout via POST."""
    return await _saml_logout()


@router.get("/saml/logout")
async def saml_logout_get():
    """SAML logout via GET (browser convenience)."""
    return await _saml_logout()
