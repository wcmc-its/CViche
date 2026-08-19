"""Authentication middleware using itsdangerous signed cookies."""
import os
import time
import secrets
import logging
from datetime import datetime

from fastapi import Request, HTTPException, Depends
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy.orm import Session
from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.models import User
from app.config_loader import get_config_value
from app.ed_group_lookup import (
    get_cached_membership,
    set_cached_membership,
    get_stale_membership,
    check_ed_membership,
    EdUnavailableError,
    LDAPConfig,
)
from pydantic import SecretStr
from app.services.config_service import SESSION_TTL as _CFG_SESSION_TTL
from app.session_idle import get_idle_store
from app.audit_events import (
    SESSION_EXPIRED,
    SESSION_REVOKED,
    ROLE_CHANGED,
    GROUP_MEMBERSHIP_REMOVED,
    DIRECTORY_UNAVAILABLE,
)

logger = logging.getLogger(__name__)

SESSION_TTL = _CFG_SESSION_TTL
COOKIE_NAME = "cviche_session"

# Known placeholder secrets shipped in templates/examples. Booting with one of
# these means every session cookie is forgeable by anyone who has read the
# repo, so refuse outright. Exact matches only, deliberately conservative: a
# hard length gate could take down a live deployment whose real secret is
# merely short, so short secrets only warn below.
_PLACEHOLDER_SECRETS = frozenset({
    "changeme",
    "change-me",
    "secret",
    "dev-secret",
    "change-me-to-a-long-random-string",   # backend/.env.example
    "dev-secret-change-in-production",     # web_interface/docker-compose.yml
})

_GENERATE_HINT = 'Generate one with: python -c "import secrets; print(secrets.token_hex(32))"'


def _validate_session_secret(secret: str | None) -> str:
    if not secret:
        raise RuntimeError(
            "CVICHE_SESSION_SECRET environment variable is required. "
            + _GENERATE_HINT
        )
    if secret.strip().lower() in _PLACEHOLDER_SECRETS:
        raise RuntimeError(
            "CVICHE_SESSION_SECRET is a known placeholder value; session "
            "cookies signed with it are forgeable. " + _GENERATE_HINT
        )
    if len(secret) < 32:
        logger.warning(
            "[SECURITY] CVICHE_SESSION_SECRET is shorter than 32 characters; "
            "session cookies are easier to brute-force. %s", _GENERATE_HINT,
        )
    return secret


_secret = _validate_session_secret(os.environ.get("CVICHE_SESSION_SECRET"))

_serializer = URLSafeTimedSerializer(_secret)
_secure_cookies = os.environ.get("CVICHE_SECURE_COOKIES", "true").lower() == "true"


def get_session_epoch(db: Session) -> int:
    """Current global session epoch.

    A monotonically-increasing counter in SystemConfig. Every cookie is stamped
    with the epoch in force when it was minted; bumping the epoch (the admin
    "sign out everyone" endpoint) invalidates every cookie carrying an older
    value on its next request -- the revocation primitive that stateless signed
    cookies otherwise lack. Defaults to 0 when unset.
    """
    try:
        return int(get_config_value(db, "session_epoch") or 0)
    except (TypeError, ValueError):
        return 0


def create_session_cookie(user: User, epoch: int = 0) -> str:
    # Per-session id: lets the server track idle activity for THIS session in
    # Valkey (independent of the absolute cookie TTL). Seeded here so every cookie
    # mint (login / SAML ACS) starts an idle key in lockstep with the cookie.
    sid = secrets.token_urlsafe(18)
    payload = {
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        # Stamp the revocation epoch in force at mint time (see get_session_epoch).
        "epoch": epoch,
        "issued_at": int(time.time()),
        "sid": sid,
    }
    get_idle_store().start(sid)
    return _serializer.dumps(payload)


def decode_session_cookie(cookie_value: str) -> dict | None:
    try:
        return _serializer.loads(cookie_value, max_age=SESSION_TTL)
    except (BadSignature, SignatureExpired):
        return None


def _best_effort_commit(db: Session, what: str) -> bool:
    """Commit a non-critical per-request write (last_active_at bump, role sync).

    A page load fires several API calls at once, each running this dependency
    and writing the same `users` row. On some MySQL configurations the racing
    writers raise OperationalError 1020 ("Record has changed since last read"),
    which would otherwise surface as a 500. These writes aren't required to
    serve the current request, so on conflict we roll back and continue; the
    update simply lands on a later request.

    Returns True if committed, False if the conflict was swallowed.
    """
    try:
        db.commit()
        return True
    except OperationalError as e:
        db.rollback()
        logger.warning("Skipped %s write due to concurrent DB conflict: %s", what, e)
        return False


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
        logger.info(SESSION_EXPIRED, extra={"reason": "invalid_or_expired_cookie"})
        raise HTTPException(
            status_code=401,
            detail={"error": "auth_required", "message": "Session expired. Please log in again."}
        )

    # A validly-signed cookie can still carry a malformed payload -- a legacy
    # shape, or a future one (e.g. #657's thin {sid}-only cookie). Fail closed
    # with 401 rather than let a KeyError/TypeError/ValueError from a raw
    # subscript become an unhandled 500.
    try:
        user_id = payload["user_id"]
        epoch = int(payload.get("epoch", 0))
    except (KeyError, TypeError, ValueError):
        logger.info(SESSION_EXPIRED, extra={"reason": "malformed_payload"})
        raise HTTPException(
            status_code=401,
            detail={"error": "auth_required", "message": "Session invalid. Please log in again."}
        )

    # Global revocation gate: a cookie minted before the current session epoch
    # (bumped by the admin "sign out everyone" action) is dead. A missing epoch
    # -- cookies minted before this feature shipped -- is read as 0, so the
    # rollout itself does not force a mass re-login; the first epoch bump does.
    if epoch != get_session_epoch(db):
        logger.info(
            SESSION_REVOKED,
            extra={
                "user_id": user_id,
                "email": payload.get("email"),
                "reason": "epoch_mismatch",
            },
        )
        raise HTTPException(
            status_code=401,
            detail={"error": "auth_required", "message": "Session expired. Please log in again."}
        )

    user = db.query(User).filter(User.id == user_id).first()
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

    # Server-side idle enforcement: reject (and refresh) the session's sliding
    # idle window in Valkey. Checked before the ED lookup so an idle session
    # short-circuits the (potentially slow) LDAP call. Cookies minted before this
    # feature carry no `sid` and bypass the check -- they stay bounded by the
    # absolute cookie TTL. No-op / fail-open when Valkey is unset or unreachable.
    sid = payload.get("sid")
    if sid and not get_idle_store().touch(sid):
        logger.info(
            SESSION_EXPIRED,
            extra={
                "user_id": user_id,
                "email": payload.get("email"),
                "reason": "idle_timeout",
            },
        )
        raise HTTPException(
            status_code=401,
            detail={"error": "session_idle",
                    "message": "Your session timed out due to inactivity. Please log in again."}
        )

    # ED group re-check for SAML users (if enabled)
    if user.auth_method == "saml":
        ed_enabled = get_config_value(db, "ed_enabled")
        if ed_enabled:
            if not user.cwid:
                # Legacy session provisioned before CWID anchoring -- force a
                # clean re-login so the cwid gets set (see provision_user).
                raise HTTPException(
                    status_code=401,
                    detail={"error": "session_invalid",
                            "message": "Please log in again."}
                )
            membership = get_cached_membership(user.cwid)
            if membership is None:
                # Cache miss -- query ED
                from app.config_loader import get_config

                ed_access_group = get_config_value(db, "ed_access_group") or ""
                ed_admin_group = get_config_value(db, "ed_admin_group") or ""
                ldap_url, source = get_config("ldap", "ED_LDAP_URL", default="")
                ldap_bind_dn, source = get_config("ldap", "ED_LDAP_BIND_DN", default="")
                bind_password = os.environ.get("ED_LDAP_BIND_PASSWORD", "")
                if not ldap_url or not ldap_bind_dn or not ed_access_group:
                    # Same guard saml_acs() applies at login -- an incomplete
                    # ED config can't be told apart from "not authorized"
                    # without this, so treat it as directory-unavailable.
                    logger.error("ED LDAP config incomplete for per-request re-check")
                    logger.info(DIRECTORY_UNAVAILABLE, extra={"cwid": user.cwid})
                    raise HTTPException(
                        status_code=401,
                        detail={"error": "directory_unavailable",
                                "message": "Unable to verify group membership. Please try again later."}
                    )
                ldap_cfg = LDAPConfig(
                    ldap_url=ldap_url, bind_dn=ldap_bind_dn,
                    bind_password=SecretStr(bind_password),
                )
                try:
                    membership = check_ed_membership(
                        cwid=user.cwid,
                        access_group=ed_access_group,
                        admin_group=ed_admin_group,
                        cfg=ldap_cfg,
                    )
                    set_cached_membership(user.cwid, membership)
                except EdUnavailableError:
                    # ED unreachable -- use stale cache
                    logger.warning("ED unavailable during per-request check for %s", user.cwid)
                    membership = get_stale_membership(user.cwid)
                    if membership is None:
                        # No stale data available -- cannot verify membership
                        logger.info(DIRECTORY_UNAVAILABLE, extra={"cwid": user.cwid})
                        raise HTTPException(
                            status_code=401,
                            detail={"error": "directory_unavailable",
                                    "message": "Unable to verify group membership. Please try again later."}
                        )

            if not membership["in_access_group"]:
                # User removed from access group -- deny
                logger.warning("Per-request ED check: %s no longer in access group", user.cwid)
                logger.info(
                    GROUP_MEMBERSHIP_REMOVED,
                    extra={"user_id": user.id, "cwid": user.cwid},
                )
                raise HTTPException(
                    status_code=401,
                    detail={"error": "not_authorized",
                            "message": "You are no longer authorized to use CViche."}
                )

            # Sync role from ED group membership
            new_role = "admin" if membership.get("in_admin_group") else "user"
            if user.role != new_role:
                old_role = user.role
                user.role = new_role
                _best_effort_commit(db, "role sync")
                logger.info(
                    ROLE_CHANGED,
                    extra={"user_id": user.id, "old_role": old_role, "new_role": new_role},
                )

    # Debounced last_active_at update (once per 60s)
    now = datetime.now()
    if not user.last_active_at or (now - user.last_active_at).total_seconds() > 60:
        user.last_active_at = now
        _best_effort_commit(db, "last_active_at")

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
        # Explicit -- the cookie is minted from both /api/auth/login and
        # /api/saml/acs. Without an explicit path here it must match
        # get_cookie_delete_settings()'s '/' by luck of the browser's default,
        # or a cookie minted from one path can end up not sent to another.
        "path": "/",
    }


def get_cookie_delete_settings() -> dict:
    """Attributes for clearing the session cookie at logout. These must mirror
    the set-time secure/samesite/path (default '/'); a deletion cookie whose
    attributes differ from the original is ignored by some browsers."""
    return {
        "key": COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "secure": _secure_cookies,
        "path": "/",
    }
