"""Authentication middleware using itsdangerous signed cookies."""
import json
import os
import time
import secrets
import logging
from dataclasses import dataclass
from datetime import datetime

from fastapi import Request, HTTPException, Depends
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.exc import OperationalError

from app.database import get_db
from app.models import User, UserRole, can_view_all_runs  # can_view_all_runs re-exported
from app.config_loader import get_config_value
from app.services import ed_access
from app.ed_group_lookup import (
    check_ed_membership,
    MembershipResult,
)
from app.services.config_service import SESSION_TTL as _CFG_SESSION_TTL
from app.session_idle import get_idle_store, SessionStoreUnavailable
from app.audit_events import (
    SESSION_EXPIRED,
    SESSION_REVOKED,
    SESSION_STORE_UNAVAILABLE,
    ROLE_CHANGED,
    GROUP_MEMBERSHIP_REMOVED,
    DIRECTORY_UNAVAILABLE,
)

logger = logging.getLogger(__name__)

SESSION_TTL = _CFG_SESSION_TTL
COOKIE_NAME = "cviche_session"

# Explicit session-cookie versions (#657 review, threads 8/11/13/19). The
# security model a cookie was minted under is stamped on the cookie itself, not
# inferred from whether the store happens to be reachable right now:
#   v2 (thin)  -- {"v": 2, "sid": ...}. Identity lives in the store; the cookie
#                 carries none. Minted whenever the store is enabled.
#   v1 (rich)  -- today's self-contained payload. Minted only when the store is
#                 disabled (no CVICHE_REDIS_URL), which is production today.
# A cookie with no "v" is a pre-versioning rich cookie and is read as v1.
SESSION_COOKIE_VERSION_THIN = 2
SESSION_COOKIE_VERSION_RICH = 1

# The one 401 body for "we could not verify ED membership" -- named once so the
# unreachable-directory and unconfigured-group arms cannot drift apart.
_ED_UNVERIFIABLE_DETAIL = {
    "error": "directory_unavailable",
    "message": "Unable to verify group membership. Please try again later.",
}

# Response bodies, named once each so the several sites that raise them cannot
# drift apart (CODING_STANDARDS.md 8.2).
_AUTH_REQUIRED_DETAIL = {
    "error": "auth_required",
    "message": "Authentication required. Please log in.",
}
_SESSION_EXPIRED_DETAIL = {
    "error": "auth_required",
    "message": "Session expired. Please log in again.",
}
_USER_NOT_FOUND_DETAIL = {
    "error": "auth_required",
    "message": "User not found. Please log in again.",
}
_ACCOUNT_DISABLED_DETAIL = {
    "error": "account_disabled",
    "message": "Your account has been disabled. Contact an administrator.",
}
_SESSION_INVALID_DETAIL = {
    "error": "session_invalid",
    "message": "Please log in again.",
}
_SESSION_IDLE_DETAIL = {
    "error": "session_idle",
    "message": "Your session timed out due to inactivity. Please log in again.",
}
_NOT_AUTHORIZED_DETAIL = {
    "error": "not_authorized",
    "message": "You are no longer authorized to use CViche.",
}
# 503, not 401: the session may well be perfectly valid -- we cannot tell.
# Saying "log in again" would be a lie, and would also send the user at a login
# page that cannot mint a cookie either. Public (no leading underscore): the
# login and SAML ACS routes answer with these too, so they are part of this
# module's contract, not an internal detail.
SESSION_STORE_UNAVAILABLE_DETAIL = {
    "error": "session_store_unavailable",
    "message": "Sign-in state is temporarily unavailable. Please try again.",
}
SESSION_STATE_UNAVAILABLE_DETAIL = {
    "error": "session_state_unavailable",
    "message": "Sign-in state is temporarily unavailable. Please try again.",
}


class SessionEpochUnreadable(RuntimeError):
    """The global session epoch could not be read as an integer.

    Distinct from "the epoch is 0": the startup seeder always writes
    session_epoch, so a missing or unparseable row is a misconfiguration, and
    reading it as 0 would silently un-revoke every session minted before the
    last "sign out everyone" (#657 review, thread 9).
    """


@dataclass(frozen=True)
class SessionIdentity:
    """Who a session cookie is for, and under which revocation epoch.

    Resolved from the server-side store for a v2 cookie and from the payload
    for a v1 one -- never a mix of the two. ``email`` is populated for v1 only
    (audit extras); a v2 cookie carries no identity fields at all.
    """
    user_id: int
    epoch: int
    sid: str | None
    email: str | None = None

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


def _validate_session_secret(secret: str | None, environment: str = "development") -> str:
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
        message = (
            "CVICHE_SESSION_SECRET is shorter than 32 characters; session "
            "cookies are easier to brute-force. %s" % _GENERATE_HINT
        )
        # Same ENVIRONMENT convention main.py already uses. A hard gate
        # everywhere could take down a live deployment whose real secret is
        # merely short with no chance to rotate first -- so `development`
        # (the default, e.g. a fresh local checkout) only warns; anything
        # else fails closed rather than boot with a brute-forceable key.
        if environment == "development":
            logger.warning("[SECURITY] %s", message)
        else:
            raise RuntimeError("[SECURITY] " + message)
    return secret


_secret = _validate_session_secret(
    os.environ.get("CVICHE_SESSION_SECRET"),
    os.environ.get("ENVIRONMENT", "development"),
)

_serializer = URLSafeTimedSerializer(_secret)
_secure_cookies = os.environ.get("CVICHE_SECURE_COOKIES", "true").lower() == "true"


def get_session_epoch(db: Session) -> int:
    """Current global session epoch.

    A monotonically-increasing counter in SystemConfig. Every cookie is stamped
    with the epoch in force when it was minted; bumping the epoch (the admin
    "sign out everyone" endpoint) invalidates every cookie carrying an older
    value on its next request -- the revocation primitive that stateless signed
    cookies otherwise lack.

    Never defaults. The startup seeder writes session_epoch on every boot, so a
    missing row, a null, a non-integer or a value that is not JSON at all is a
    misconfiguration, not "epoch 0" -- and reading it as 0 would silently
    re-admit every cookie minted before the last revocation. Raises
    SessionEpochUnreadable instead; callers fail closed with a 503. An
    OperationalError (the DB itself is down) propagates as before.
    """
    try:
        raw = get_config_value(db, "session_epoch")
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise SessionEpochUnreadable("session_epoch is not valid JSON") from exc
    if raw is None:
        raise SessionEpochUnreadable("session_epoch is not seeded")
    # bool first: it is an int subclass, so `true` would otherwise read as 1.
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise SessionEpochUnreadable(
            "session_epoch is not an integer (got %s)" % type(raw).__name__
        )
    return raw


def create_session_cookie(user: User, db: Session) -> str:
    """Mint a session cookie for `user`, stamped with the current epoch.

    The epoch is read here rather than passed in: an omitted argument used to
    default to 0, which after a "sign out everyone" minted a cookie that was
    already dead on arrival (#657 review, thread 12).

    Per-session id: lets the server track idle activity for THIS session in
    Valkey (independent of the absolute cookie TTL) and, when the store is
    enabled, hold the session's identity.

    Raises SessionEpochUnreadable or SessionStoreUnavailable rather than return
    a cookie that cannot work. In particular, when the store is enabled the
    record is written BEFORE the cookie value exists, so a failed write can
    never leave a caller holding a thin cookie whose identity was never
    registered (thread 16).
    """
    epoch = get_session_epoch(db)
    sid = secrets.token_urlsafe(18)
    store = get_idle_store()

    # Thin vs. rich payload (#368): when the idle store is enabled, identity is
    # resolved server-side from the store record written just below, so the
    # cookie itself only needs to carry the sid -- a leaked
    # CVICHE_SESSION_SECRET alone is no longer enough to mint a session for an
    # arbitrary user, since the attacker would also need a live,
    # store-registered sid. When the store is disabled (no CVICHE_REDIS_URL --
    # local/dev/docker-compose default, and production today), there is nowhere
    # server-side to resolve identity from, so keep minting the self-contained
    # payload -- now explicitly stamped v1.
    if store.enabled:
        store.start(sid, user.id, epoch)
        return _serializer.dumps({"v": SESSION_COOKIE_VERSION_THIN, "sid": sid})
    return _serializer.dumps({
        "v": SESSION_COOKIE_VERSION_RICH,
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        # Stamp the revocation epoch in force at mint time (see get_session_epoch).
        "epoch": epoch,
        "issued_at": int(time.time()),
        "sid": sid,
    })


def decode_session_cookie(cookie_value: str) -> dict | None:
    try:
        return _serializer.loads(cookie_value, max_age=SESSION_TTL)
    except (BadSignature, SignatureExpired):
        return None


def _payload_int(value) -> int | None:
    """A payload field as an int, or None if it is not one. `bool` is excluded
    deliberately -- it is an int subclass, so `true` would read as user 1."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def resolve_session_identity(payload: dict) -> SessionIdentity | None:
    """Resolve the identity a decoded session payload stands for, or None.

    Which security model applies is decided by whether the store is enabled --
    a deployment-level fact -- and then enforced against the cookie's own
    version stamp. It is never decided by whether the store happens to answer
    right now (#657 review, threads 8/11/13).

    Store enabled: the store record is the identity of record and the ONLY
    source of identity. Nothing is ever read from the payload but the version
    and the sid, so a deleted, revoked or unknown session can never be revived
    from client-supplied fields, and an outage raises SessionStoreUnavailable
    rather than degrading into "no such session".

    Store disabled: there is nowhere server-side to resolve from, so a v1 (or
    pre-versioning) rich payload is the identity. A v2 cookie is rejected --
    a thin cookie has no identity without a store to look it up in.

    Returns None for anything malformed. Never raises for a malformed shape;
    the only exception out of here is a store outage.
    """
    store = get_idle_store()
    if store.enabled:
        return _resolve_stored_identity(payload, store)
    return _resolve_payload_identity(payload)


def _resolve_stored_identity(payload: dict, store) -> SessionIdentity | None:
    """Identity for a store-enabled deployment: the store record, or nothing.

    A cookie that is not stamped v2 is rejected outright. On a deployment that
    has the store enabled this forces a one-time re-login for sessions minted
    before this change -- deliberately: continuing to honour their embedded
    fields is exactly the payload-trusting fallback this removes.
    """
    if payload.get("v") != SESSION_COOKIE_VERSION_THIN:
        return None
    sid = payload.get("sid")
    if not isinstance(sid, str) or not sid:
        return None
    record = store.resolve(sid)          # SessionStoreUnavailable propagates
    if record is None:
        return None
    return SessionIdentity(user_id=record.user_id, epoch=record.epoch, sid=sid)


def _resolve_payload_identity(payload: dict) -> SessionIdentity | None:
    """Identity for a store-disabled deployment: the cookie's own fields.

    A missing epoch is NOT read as 0 -- the field has been stamped on every
    cookie since the revocation gate shipped, so its absence is a malformed
    payload, and defaulting it would hand a forged/truncated cookie the current
    epoch for free.
    """
    if payload.get("v") not in (None, SESSION_COOKIE_VERSION_RICH):
        return None
    user_id = _payload_int(payload.get("user_id"))
    epoch = _payload_int(payload.get("epoch"))
    if user_id is None or epoch is None:
        return None
    sid = payload.get("sid")
    email = payload.get("email")
    return SessionIdentity(
        user_id=user_id,
        epoch=epoch,
        sid=sid if isinstance(sid, str) and sid else None,
        email=email if isinstance(email, str) else None,
    )


def _is_retryable_write_conflict(exc: OperationalError) -> bool:
    """Whether exc is the specific benign concurrent-write conflict
    _best_effort_persist exists to swallow, not any OperationalError.

    MySQL (prod): pymysql raises error 1020 ("Record has changed since last
    read") as a 2-tuple (code, message) in .orig.args.
    SQLite (tests): sqlite3 raises "database is locked" as a plain message,
    no error code.
    Anything else -- connection loss, a real outage -- is a different
    problem and must not be swallowed the same way.
    """
    orig_args = getattr(exc.orig, "args", ())
    if orig_args and orig_args[0] == 1020:
        return True
    return "database is locked" in str(exc).lower()


def _best_effort_persist(user_id: int, what: str, **fields) -> bool:
    """Persist a non-critical per-request field update (last_active_at bump,
    role sync) through its own short-lived session, isolated from the
    request's shared `db` session -- so this dependency can never commit
    unrelated work staged elsewhere in the same request as a side effect.

    A page load fires several API calls at once, each running this
    dependency and writing the same `users` row; concurrent writers can hit
    the retryable conflict _is_retryable_write_conflict names, which isn't
    required to serve the current request -- on that specific conflict we
    roll back and continue; the update simply lands on a later request. Any
    other OperationalError (connection loss, a real outage) propagates.

    Returns True if committed, False if the conflict was swallowed.
    """
    from app.database import SessionLocal  # see other SessionLocal call
    # sites in this codebase (main.py, runs.py, ...) -- imported locally so
    # tests that patch app.database.SessionLocal are honored at call time.
    session = SessionLocal()
    try:
        session.query(User).filter(User.id == user_id).update(fields)
        session.commit()
        return True
    except OperationalError as e:
        session.rollback()
        if not _is_retryable_write_conflict(e):
            raise
        logger.warning("Skipped %s write due to concurrent DB conflict: %s", what, e)
        return False
    finally:
        session.close()


def _resolve_identity_or_reject(payload: dict) -> SessionIdentity:
    """resolve_session_identity, with the two failure modes turned into their
    respective HTTP answers: 503 when the store could not be reached (we do not
    know), 401 when it answered and there is no such session (we do)."""
    try:
        identity = resolve_session_identity(payload)
    except SessionStoreUnavailable as exc:
        logger.error("Session store unavailable while resolving identity", exc_info=True)
        logger.info(SESSION_STORE_UNAVAILABLE, extra={"reason": "resolve"})
        raise HTTPException(
            status_code=503, detail=SESSION_STORE_UNAVAILABLE_DETAIL
        ) from exc
    if identity is None:
        logger.info(SESSION_EXPIRED, extra={"reason": "unresolvable_session"})
        raise HTTPException(status_code=401, detail=_SESSION_INVALID_DETAIL)
    return identity


def _enforce_session_epoch(identity: SessionIdentity, db: Session) -> None:
    """Global revocation gate: a cookie stamped with an older epoch than the
    one in force (bumped by the admin "sign out everyone" action) is dead."""
    try:
        current_epoch = get_session_epoch(db)
    except SessionEpochUnreadable as exc:
        logger.error("Session epoch is unreadable; failing closed", exc_info=True)
        raise HTTPException(
            status_code=503, detail=SESSION_STATE_UNAVAILABLE_DETAIL
        ) from exc
    if identity.epoch != current_epoch:
        logger.info(
            SESSION_REVOKED,
            extra={
                "user_id": identity.user_id,
                "email": identity.email,
                "reason": "epoch_mismatch",
            },
        )
        raise HTTPException(status_code=401, detail=_SESSION_EXPIRED_DETAIL)


def _load_active_user(identity: SessionIdentity, db: Session) -> User:
    user = db.query(User).filter(User.id == identity.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail=_USER_NOT_FOUND_DETAIL)
    if user.status != "active":
        raise HTTPException(status_code=401, detail=_ACCOUNT_DISABLED_DETAIL)
    return user


def _enforce_idle_window(identity: SessionIdentity) -> None:
    """Reject (and refresh) the session's sliding idle window in Valkey.

    Checked before the ED lookup so an idle session short-circuits the
    (potentially slow) LDAP call. A v1 cookie minted before this feature
    carries no `sid` and bypasses the check -- it stays bounded by the absolute
    cookie TTL. A store outage is a 503, never a silent pass (thread 14).
    """
    if not identity.sid:
        return
    try:
        still_active = get_idle_store().touch(identity.sid)
    except SessionStoreUnavailable as exc:
        logger.error("Session store unavailable while refreshing the idle window",
                     exc_info=True)
        logger.info(SESSION_STORE_UNAVAILABLE, extra={"reason": "touch"})
        raise HTTPException(
            status_code=503, detail=SESSION_STORE_UNAVAILABLE_DETAIL
        ) from exc
    if not still_active:
        logger.info(
            SESSION_EXPIRED,
            extra={
                "user_id": identity.user_id,
                "email": identity.email,
                "reason": "idle_timeout",
            },
        )
        raise HTTPException(status_code=401, detail=_SESSION_IDLE_DETAIL)


def authenticate_session_cookie(
    cookie_value: str | None,
    db: Session,
    *,
    touch_idle: bool,
) -> tuple[User, SessionIdentity]:
    """Cookie -> (User, SessionIdentity), or an HTTPException.

    The shared half of get_current_user, lifted out so the WebSocket endpoint
    runs the identical checks instead of its own transcription of them
    (CODING_STANDARDS.md 3.2). Everything here is request-shape-agnostic: no
    Request, no WebSocket, no response building.

    `touch_idle=False` runs every check but does not slide the idle window --
    for the WebSocket's periodic re-validation, where a socket sitting open
    must not count as user activity. For a v2 cookie the resolve() above has
    already proved the record still exists, which is the same key `touch()`
    would have refreshed.
    """
    if not cookie_value:
        raise HTTPException(status_code=401, detail=_AUTH_REQUIRED_DETAIL)

    payload = decode_session_cookie(cookie_value)
    if not payload:
        logger.info(SESSION_EXPIRED, extra={"reason": "invalid_or_expired_cookie"})
        raise HTTPException(status_code=401, detail=_SESSION_EXPIRED_DETAIL)

    identity = _resolve_identity_or_reject(payload)
    _enforce_session_epoch(identity, db)
    user = _load_active_user(identity, db)
    if touch_idle:
        _enforce_idle_window(identity)
    return user, identity


def _recheck_ed_membership(user: User, db: Session) -> None:
    """Per-request ED group re-check for SAML users, when ED is enabled.

    Re-verifies on every request that the user is still in the access group and
    syncs their role from the admin and staff groups; a removal takes effect on
    the next request rather than at the next login. The check itself is
    ``ed_access.verify_ed_access`` (shared with the emailed-CV intake, #1298);
    this wraps it with the 401s and audit events.
    """
    try:
        membership = ed_access.verify_ed_access(user, db, check_ed_membership)
    except ed_access.EdCwidMissing:
        # Legacy session provisioned before CWID anchoring -- force a clean
        # re-login so the cwid gets set (see provision_user).
        raise HTTPException(status_code=401, detail=_SESSION_INVALID_DETAIL)
    except ed_access.EdUnverifiable:
        # ED unreachable or misconfigured with no usable stale answer, or no
        # access group configured: fail closed, never a 500, never an approval.
        logger.warning("ED unavailable during per-request check for %s", user.cwid)
        logger.info(DIRECTORY_UNAVAILABLE, extra={"cwid": user.cwid})
        raise HTTPException(status_code=401, detail=_ED_UNVERIFIABLE_DETAIL)
    except ed_access.EdNotInAccessGroup:
        # User removed from access group -- deny
        logger.warning("Per-request ED check: %s no longer in access group", user.cwid)
        logger.info(
            GROUP_MEMBERSHIP_REMOVED,
            extra={"user_id": user.id, "cwid": user.cwid},
        )
        raise HTTPException(status_code=401, detail=_NOT_AUTHORIZED_DETAIL)
    if membership is None:
        return

    # Sync role from ED group membership
    new_role = role_for_membership(membership)
    if user.role != new_role:
        old_role = user.role
        # Update the in-memory value WITHOUT marking the request session's
        # instance dirty: the write goes through _best_effort_persist's own
        # session, and a dirty instance would make the route's later
        # db.commit() re-issue the same UPDATE from a snapshot taken before
        # that side commit. MariaDB 11.6+ (innodb_snapshot_isolation=ON,
        # REPEATABLE READ) rejects that with error 1020 "Record has changed
        # since last read" -- every state-changing request after the 60s
        # debounce 500'd in prod on 2026-09-03 (run CJZE8G feedback POST).
        set_committed_value(user, "role", new_role)
        _best_effort_persist(user.id, "role sync", role=new_role)
        logger.info(
            ROLE_CHANGED,
            extra={"user_id": user.id, "old_role": old_role, "new_role": new_role},
        )


def _bump_last_active(user: User) -> None:
    """Debounced last_active_at update (once per 60s)."""
    now = datetime.now()
    if not user.last_active_at or (now - user.last_active_at).total_seconds() > 60:
        set_committed_value(user, "last_active_at", now)  # see role sync above
        _best_effort_persist(user.id, "last_active_at", last_active_at=now)


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency: extract and validate user from session cookie.
    Performs per-request DB check for status and role sync."""
    user, _identity = authenticate_session_cookie(
        request.cookies.get(COOKIE_NAME), db, touch_idle=True
    )
    _recheck_ed_membership(user, db)
    _bump_last_active(user)
    return user


def role_for_membership(membership: MembershipResult) -> UserRole:
    """The role ED membership grants: admin wins over staff, staff over user.

    The caller has already denied a user outside the access group; the
    MembershipResult invariant also keeps admin/staff False for such a user.
    """
    if membership.in_admin_group:
        return UserRole.ADMIN
    if membership.in_staff_group:
        return UserRole.STAFF
    return UserRole.USER


def can_see_cost(user: User) -> bool:
    """Processing cost is admin-only (#1111). The UI hides it by role too; the
    API withholding it is what keeps it out of the network tab."""
    return user.role == "admin"


def visible_cost(user: User, cost: float | None) -> float | None:
    """A dollar figure as this user may see it: the value, or None."""
    return (cost or 0.0) if can_see_cost(user) else None


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "Admin access required."}
        )
    return user


def require_view_all_runs(user: User = Depends(get_current_user)) -> User:
    """FastAPI dependency for the read-only routes can_view_all_runs opens."""
    if not can_view_all_runs(user):
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "Admin or staff access required."}
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
