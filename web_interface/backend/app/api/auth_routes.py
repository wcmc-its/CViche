"""Authentication API endpoints."""
import time
import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import LoginRequest, LoginResponse, MeResponse, QuotaInfo
from app.auth import create_session_cookie, get_cookie_settings, get_current_user, COOKIE_NAME
from app.config_loader import get_config_value
from app.rate_limiter import get_quota

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory rate limiter: {ip: [timestamp, ...]}
# ---------------------------------------------------------------------------
_rate_limit_store: dict[str, list[float]] = defaultdict(list)
RATE_LIMIT_MAX = 10
RATE_LIMIT_WINDOW = 60  # seconds


def _check_rate_limit(ip: str) -> bool:
    """Return True if the request is allowed, False if rate-limited."""
    now = time.time()
    # Prune entries older than the window
    _rate_limit_store[ip] = [
        ts for ts in _rate_limit_store[ip] if now - ts < RATE_LIMIT_WINDOW
    ]
    if len(_rate_limit_store[ip]) >= RATE_LIMIT_MAX:
        return False
    _rate_limit_store[ip].append(now)
    return True


# ---------------------------------------------------------------------------
# POST /api/auth/login
# ---------------------------------------------------------------------------
@router.post("/auth/login")
async def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Authenticate a user by email against the allowed_users list."""
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
    user = db.query(User).filter(User.email == email_lower).first()
    if user:
        user.display_name = body.display_name.strip()
        user.role = role
    else:
        user = User(
            email=email_lower,
            display_name=body.display_name.strip(),
            role=role,
        )
        db.add(user)
    db.commit()
    db.refresh(user)

    # Build response
    response_data = LoginResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
    )

    response = JSONResponse(content=response_data.model_dump())
    cookie_settings = get_cookie_settings()
    token = create_session_cookie(user)
    response.set_cookie(value=token, **cookie_settings)
    return response


# ---------------------------------------------------------------------------
# POST /api/auth/logout
# ---------------------------------------------------------------------------
@router.post("/auth/logout")
async def logout():
    """Clear the session cookie."""
    response = JSONResponse(content={"message": "Logged out."})
    response.delete_cookie(
        key=COOKIE_NAME,
        httponly=True,
        samesite="lax",
    )
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
