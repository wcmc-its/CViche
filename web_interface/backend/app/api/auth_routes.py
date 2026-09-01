"""Authentication API endpoints."""
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.client_ip import get_client_ip
from app.database import get_db
from app.models import User
from app.schemas import LoginRequest, LoginResponse, AuthConfigResponse, MeResponse, QuotaInfo
from app.auth import create_session_cookie, decode_session_cookie, get_cookie_settings, get_cookie_delete_settings, get_current_user, COOKIE_NAME
from app.session_idle import get_idle_store
from app.login_throttle import get_login_throttle
from app.config_loader import get_config_value
from app.rate_limiter import get_quota
from app.services.user_service import provision_user, normalize_email
from app.audit_events import LOGIN_SUCCESS, LOGIN_FAILED, SESSION_REVOKED

logger = logging.getLogger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/auth/config
# ---------------------------------------------------------------------------
@router.get("/auth/config", response_model=AuthConfigResponse, response_model_exclude_none=True)
def get_auth_config(db: Session = Depends(get_db)):
    """Return public auth configuration for frontend mode detection.
    This endpoint requires NO authentication -- the frontend needs it
    before the user has logged in.

    Plain `def`, not `async def`, here and on the rest of this module's
    routes: none of them `await` anything -- every call inside is
    synchronous DB/service work. FastAPI runs a sync path function in its
    threadpool automatically, exactly like it already does for the sync
    get_current_user dependency, so this keeps the blocking work off the
    event loop with no async infrastructure change needed."""
    mode = get_config_value(db, "auth_mode") or "simple"
    response = {"mode": mode}
    if mode == "saml":
        response["discovery_url"] = get_config_value(db, "saml_discovery_url") or ""
    return response


# ---------------------------------------------------------------------------
# POST /api/auth/login
# ---------------------------------------------------------------------------
@router.post("/auth/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Authenticate a user by email against the allowed_users list."""
    # Mode guard: reject simple login when SAML is active
    auth_mode = get_config_value(db, "auth_mode") or "simple"
    if auth_mode != "simple":
        return JSONResponse(
            status_code=403,
            content={
                "error": "sso_required",
                "message": "This instance uses SSO. Please use the SSO login button.",
            },
        )

    client_ip = get_client_ip(request) or "unknown"

    if not get_login_throttle().allow(client_ip):
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited", "message": "Too many login attempts. Please try again later."},
        )

    # Normalise email for comparison
    email_lower = normalize_email(body.email)

    # Check allowed_users from SystemConfig. frozenset, not list: O(1) membership
    # instead of a linear scan on every login. Rebuilt per-request rather than
    # cached at module scope -- admin_users/allowed_users are edited live via
    # SystemConfig, and a cached copy would need its own invalidation story.
    allowed_users = get_config_value(db, "allowed_users") or []
    allowed_lower = frozenset(e.lower() for e in allowed_users)

    if email_lower not in allowed_lower:
        logger.warning("Login rejected for unrecognised email: %s", body.email)
        logger.info(
            LOGIN_FAILED,
            extra={"email": body.email, "reason": "not_allowlisted"},
        )
        return JSONResponse(
            status_code=403,
            content={"error": "forbidden", "message": "Email not in the allowed users list."},
        )

    # Determine role
    admin_users = get_config_value(db, "admin_users") or []
    admin_lower = frozenset(e.lower() for e in admin_users)
    role = "admin" if email_lower in admin_lower else "user"

    # Create or update User record
    user = provision_user(
        db=db,
        email=email_lower,
        display_name=body.display_name.strip(),
        auth_method="simple",
        role=role,
    )

    # Build response
    response_data = LoginResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
    )

    response = JSONResponse(content=response_data.model_dump())
    cookie_settings = get_cookie_settings()
    token = create_session_cookie(user, db)
    response.set_cookie(value=token, **cookie_settings)

    logger.info(
        LOGIN_SUCCESS,
        extra={
            "user_id": user.id,
            "email": user.email,
            "role": user.role,
            "auth_method": "simple",
        },
    )

    return response


# ---------------------------------------------------------------------------
# POST /api/auth/logout
# ---------------------------------------------------------------------------
@router.post("/auth/logout")
def logout(request: Request):
    """Clear the session cookie and drop its server-side idle key."""
    # Best-effort: delete the Valkey idle key so the session can't be revived by
    # replaying the (now-cleared) cookie before its absolute TTL lapses.
    cookie = request.cookies.get(COOKIE_NAME)
    payload = decode_session_cookie(cookie) if cookie else None
    if payload and payload.get("sid"):
        get_idle_store().end(payload["sid"])

    if payload:
        logger.info(
            SESSION_REVOKED,
            extra={
                # payload.get(...): logout() reads defensively regardless of
                # cookie shape. #657 will make this the *only* option once
                # the thin {sid}-only cookie lands; repoint through its
                # resolve_session_identity() then instead of the raw payload.
                "user_id": payload.get("user_id"),
                "email": payload.get("email"),
                "reason": "user_logout",
            },
        )

    response = JSONResponse(content={"message": "Logged out."})
    response.delete_cookie(**get_cookie_delete_settings())
    return response


# ---------------------------------------------------------------------------
# GET /api/auth/me
# ---------------------------------------------------------------------------
@router.get("/auth/me")
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return the current authenticated user's info."""
    quota_data = get_quota(user, db)
    data = MeResponse(
        user_id=user.id,
        cwid=user.cwid,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        consent_version=user.consent_version,
        default_submission_type=user.default_submission_type,
        quota=QuotaInfo(**quota_data),
    )
    return data
