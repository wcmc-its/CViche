"""Authentication middleware using itsdangerous signed cookies."""
import os
import time
import secrets
import logging
from datetime import datetime

from fastapi import Request, HTTPException, Depends
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.config_loader import get_config_value
from app.ed_group_lookup import (
    get_cached_membership,
    set_cached_membership,
    get_stale_membership,
    check_ed_membership,
    EdUnavailableError,
)

logger = logging.getLogger(__name__)

SESSION_TTL = 7 * 24 * 3600  # 7 days
COOKIE_NAME = "cviche_session"

_secret = os.environ.get("CVICHE_SESSION_SECRET")
if not _secret:
    _secret = secrets.token_hex(32)
    logger.warning(
        "CVICHE_SESSION_SECRET not set — using random key. "
        "Sessions will not survive server restarts."
    )

_serializer = URLSafeTimedSerializer(_secret)
_secure_cookies = os.environ.get("CVICHE_SECURE_COOKIES", "true").lower() == "true"


def create_session_cookie(user: User) -> str:
    payload = {
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        "issued_at": int(time.time()),
    }
    return _serializer.dumps(payload)


def decode_session_cookie(cookie_value: str) -> dict | None:
    try:
        return _serializer.loads(cookie_value, max_age=SESSION_TTL)
    except (BadSignature, SignatureExpired):
        return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency: extract and validate user from session cookie.
    Performs per-request DB check for status and role sync."""
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        raise HTTPException(
            status_code=401,
            detail={"error": "auth_required", "message": "Authentication required. Please log in."}
        )

    payload = decode_session_cookie(cookie)
    if not payload:
        raise HTTPException(
            status_code=401,
            detail={"error": "auth_required", "message": "Session expired. Please log in again."}
        )

    user = db.query(User).filter(User.id == payload["user_id"]).first()
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"error": "auth_required", "message": "User not found. Please log in again."}
        )

    if user.status != "active":
        raise HTTPException(
            status_code=401,
            detail={"error": "account_disabled", "message": "Your account has been disabled. Contact an administrator."}
        )

    # ED group re-check for SAML users (if enabled)
    if user.auth_method == "saml":
        ed_enabled = get_config_value(db, "ed_enabled")
        if ed_enabled:
            membership = get_cached_membership(user.email)
            if membership is None:
                # Cache miss -- query ED
                ed_access_group = get_config_value(db, "ed_access_group") or ""
                ed_admin_group = get_config_value(db, "ed_admin_group") or ""
                ldap_url = os.environ.get("ED_LDAP_URL", "")
                bind_dn = os.environ.get("ED_LDAP_BIND_DN", "")
                bind_password = os.environ.get("ED_LDAP_BIND_PASSWORD", "")
                try:
                    membership = check_ed_membership(
                        email=user.email,
                        access_group=ed_access_group,
                        admin_group=ed_admin_group,
                        ldap_url=ldap_url,
                        bind_dn=bind_dn,
                        bind_password=bind_password,
                    )
                    set_cached_membership(user.email, membership)
                except EdUnavailableError:
                    # ED unreachable -- use stale cache
                    logger.warning("ED unavailable during per-request check for %s", user.email)
                    membership = get_stale_membership(user.email)
                    if membership is None:
                        # No stale data available -- cannot verify membership
                        raise HTTPException(
                            status_code=401,
                            detail={"error": "directory_unavailable",
                                    "message": "Unable to verify group membership. Please try again later."}
                        )

            if not membership["in_access_group"]:
                # User removed from access group -- deny
                logger.warning("Per-request ED check: %s no longer in access group", user.email)
                raise HTTPException(
                    status_code=401,
                    detail={"error": "not_authorized",
                            "message": "You are no longer authorized to use CViche."}
                )

            # Sync role from ED group membership
            new_role = "admin" if membership.get("in_admin_group") else "user"
            if user.role != new_role:
                user.role = new_role
                db.commit()

    # Debounced last_active_at update (once per 60s)
    now = datetime.now()
    if not user.last_active_at or (now - user.last_active_at).total_seconds() > 60:
        user.last_active_at = now
        db.commit()

    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "Admin access required."}
        )
    return user


def get_cookie_settings() -> dict:
    return {
        "key": COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "secure": _secure_cookies,
        "max_age": SESSION_TTL,
    }
