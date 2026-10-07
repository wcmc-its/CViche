"""The simple-auth login workflow: allowlist, role, provisioning, session (#343).

Holds the part of POST /api/auth/login that is not HTTP -- whether an email may
sign in, the role it signs in with, the User row it lands on, and the session
token minted for it. The route keeps the HTTP edge: the auth-mode guard, the
per-IP throttle, and turning the outcome into a status code, body and cookie.
Nothing here sees a Request or builds a Response.
"""
import logging
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.orm import Session

from app.audit_events import LOGIN_FAILED, LOGIN_SUCCESS, SESSION_STORE_UNAVAILABLE
from app.auth import SessionEpochUnreadable, create_session_cookie
from app.config_loader import get_config_value
from app.models import User, UserRole
from app.services.user_service import normalize_email, provision_user
from app.session_idle import SessionStoreUnavailable

# Named for the route, not __name__: these lines were emitted under
# app.api.auth_routes before the workflow moved here, and both log formats
# print the logger name, so keeping it leaves every line unchanged.
logger = logging.getLogger("app.api.auth_routes")


class LoginRejection(StrEnum):
    """Why a login produced no session. Each value is also the LOGIN_FAILED
    audit line's `reason`."""
    NOT_ALLOWLISTED = "not_allowlisted"
    ACCOUNT_DISABLED = "account_disabled"
    SESSION_STORE_UNAVAILABLE = "session_store_unavailable"
    SESSION_STATE_UNAVAILABLE = "session_state_unavailable"


@dataclass(frozen=True)
class LoginSession:
    """A login that succeeded: the provisioned user and their session token."""
    user: User
    token: str


def _config_emails(db: Session, key: str) -> frozenset[str]:
    """A SystemConfig email list, lowercased. frozenset, not list: O(1)
    membership instead of a linear scan on every login. Read per login rather
    than cached at module scope -- admin_users/allowed_users are edited live
    via SystemConfig, and a cached copy would need its own invalidation story."""
    return frozenset(e.lower() for e in get_config_value(db, key) or [])


def authenticate_simple_login(db: Session, email: str, display_name: str) -> LoginSession | LoginRejection:
    """Sign `email` in against the allowed_users list.

    Role comes from admin_users on every login, so removal from it demotes.
    The User row is provisioned before the session is minted, so a mint
    failure still leaves the row created or updated.
    """
    email_lower = normalize_email(email)
    if email_lower not in _config_emails(db, "allowed_users"):
        logger.warning("Login rejected for unrecognised email: %s", email)
        logger.info(LOGIN_FAILED, extra={"email": email, "reason": LoginRejection.NOT_ALLOWLISTED})
        return LoginRejection.NOT_ALLOWLISTED

    is_admin = email_lower in _config_emails(db, "admin_users")
    user = provision_user(
        db=db,
        email=email_lower,
        display_name=display_name.strip(),
        auth_method="simple",
        role=UserRole.ADMIN if is_admin else UserRole.USER,
    )

    # The same test get_current_user applies (app.auth._load_active_user): a
    # session minted here would be refused on its first use anyway.
    if user.status != "active":
        logger.info(LOGIN_FAILED, extra={"email": user.email, "reason": LoginRejection.ACCOUNT_DISABLED})
        return LoginRejection.ACCOUNT_DISABLED

    token = _mint_session(user, db)
    if isinstance(token, LoginRejection):
        return token

    logger.info(
        LOGIN_SUCCESS,
        extra={
            "user_id": user.id,
            "email": user.email,
            "role": user.role,
            "auth_method": "simple",
        },
    )
    return LoginSession(user=user, token=token)


def _mint_session(user: User, db: Session) -> str | LoginRejection:
    """The session cookie value for a freshly-authenticated user.

    When the session store is enabled the cookie's identity lives server-side,
    so a store that cannot be written is a login that cannot succeed. Issuing
    the cookie anyway would hand the user a credential no later request can
    resolve (#657 review, thread 16).
    """
    try:
        return create_session_cookie(user, db)
    except SessionStoreUnavailable:
        logger.error("Session store unavailable during login for %s", user.email,
                     exc_info=True)
        logger.info(
            LOGIN_FAILED,
            extra={"email": user.email, "reason": LoginRejection.SESSION_STORE_UNAVAILABLE},
        )
        logger.info(SESSION_STORE_UNAVAILABLE, extra={"reason": "start"})
        return LoginRejection.SESSION_STORE_UNAVAILABLE
    except SessionEpochUnreadable:
        logger.error("Session epoch unreadable during login for %s", user.email,
                     exc_info=True)
        logger.info(
            LOGIN_FAILED,
            extra={"email": user.email, "reason": LoginRejection.SESSION_STATE_UNAVAILABLE},
        )
        return LoginRejection.SESSION_STATE_UNAVAILABLE
