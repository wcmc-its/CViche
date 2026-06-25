"""Authentication API endpoints."""
import time
import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import LoginRequest, LoginResponse, AuthConfigResponse, MeResponse, QuotaInfo
from app.auth import create_session_cookie, decode_session_cookie, get_cookie_settings, get_cookie_delete_settings, get_current_user, get_session_epoch, COOKIE_NAME
from app.session_idle import get_idle_store
from app.config_loader import get_config_value
from app.rate_limiter import get_quota
from app.services.user_service import provision_user
from app.services.config_service import LOGIN_RATE_LIMIT_MAX, LOGIN_RATE_LIMIT_WINDOW

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory rate limiter: {ip: [timestamp, ...]}
# ---------------------------------------------------------------------------
_rate_limit_store: dict[str, list[float]] = defaultdict(list)


def _check_rate_limit(ip: str) -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    now = time.time()
    # Prune entries older than the window
    _rate_limit_store[ip] = [
        ts for ts in _rate_limit_store[ip] if now - ts < LOGIN_RATE_LIMIT_WINDOW
    ]
    if len(_rate_limit_store[ip]) >= LOGIN_RATE_LIMIT_MAX:
        return False
    _rate_limit_store[ip].append(now)
    return True


# ---------------------------------------------------------------------------
# GET /api/auth/config
# ---------------------------------------------------------------------------
@router.get("/auth/config", response_model=AuthConfigResponse, response_model_exclude_none=True)
async def get_auth_config(db: Session = Depends(get_db)):
    """Return public auth configuration for frontend mode detection.
    This endpoint requires NO authentication -- the frontend needs it
    before the user has logged in."""
    mode = get_config_value(db, "auth_mode") or "simple"
    response = {"mode": mode}
    if mode == "saml":
        response["discovery_url"] = get_config_value(db, "saml_discovery_url") or ""
    return response


# ---------------------------------------------------------------------------
# POST /api/auth/login
# ---------------------------------------------------------------------------
@router.post("/auth/login")
async def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
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

    client_ip = request.client.host if request.client else "unknown"

    if not _check_rate_limit(client_ip):
        return JSONResponse(
            status_code=429,
            content={"error": "rate_limited", "message": "Too many login attempts. Please try again later."},
        )

    # Normalise email for comparison
    email_lower = body.email.strip().lower()

    # Check allowed_users from SystemConfig
    allowed_users = get_config_value(db, "allowed_users") or []
    allowed_lower = [e.lower() for e in allowed_users]

    if email_lower not in allowed_lower:
        logger.warning("Login rejected for unrecognised email: %s", body.email)
        return JSONResponse(
            status_code=403,
            content={"error": "forbidden", "message": "Email not in the allowed users list."},
        )

    # Determine role
    admin_users = get_config_value(db, "admin_users") or []
    admin_lower = [e.lower() for e in admin_users]
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
    token = create_session_cookie(user, get_session_epoch(db))
    response.set_cookie(value=token, **cookie_settings)
    return response


# ---------------------------------------------------------------------------
# POST /api/auth/logout
# ---------------------------------------------------------------------------
@router.post("/auth/logout")
async def logout(request: Request):
    """Clear the session cookie and drop its server-side idle key."""
    # Best-effort: delete the Valkey idle key so the session can't be revived by
    # replaying the (now-cleared) cookie before its absolute TTL lapses.
    cookie = request.cookies.get(COOKIE_NAME)
    payload = decode_session_cookie(cookie) if cookie else None
    if payload and payload.get("sid"):
        get_idle_store().end(payload["sid"])

    response = JSONResponse(content={"message": "Logged out."})
    response.delete_cookie(**get_cookie_delete_settings())
    return response


# ---------------------------------------------------------------------------
# GET /api/auth/me
# ---------------------------------------------------------------------------
@router.get("/auth/me")
async def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return the current authenticated user's info."""
    quota_data = get_quota(user, db)
    data = MeResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        consent_version=user.consent_version,
        default_submission_type=user.default_submission_type,
        quota=QuotaInfo(**quota_data),
    )
    return data
