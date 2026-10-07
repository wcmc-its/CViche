"""User-related service functions."""
import logging
from dataclasses import dataclass, field

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit_events import (
    ROLE_CHANGED,
    USER_CREATED,
    USER_EMAIL_UPDATED,
    USER_IDENTITY_LINKED,
)
from app.models import User

logger = logging.getLogger(__name__)

# Default role for a newly provisioned user when the caller resolves none.
DEFAULT_ROLE = "user"
# Which workflow made the change, so a provisioning ROLE_CHANGED is told apart
# from the per-request ED re-check's (app.auth) in the same log stream.
PROVISIONING_SOURCE = "provisioning"


@dataclass(frozen=True)
class AuditEvent:
    """One structured audit line: `logger.info(name, extra=fields)`."""

    name: str
    fields: dict[str, object] = field(default_factory=dict)


def normalize_email(email: str | None) -> str | None:
    """Canonicalize an email for use as a user identity key.

    email is unique on User, so every auth path must normalize the same way or
    case/whitespace variants create distinct identities (#348). Simple login
    already did this inline; the SAML path did not.

    Since #326 email is also OPTIONAL -- a SAML user whose ED record has no
    `mail` is provisioned on cwid alone -- so None must pass through instead of
    raising. A blank or whitespace-only value collapses to None for the same
    reason: "" is a real value under the unique index and two email-less users
    would collide on it, whereas NULL does not (#413).
    """
    if not email:
        return None
    return email.strip().lower() or None


def provision_user(
    db: Session,
    display_name: str,
    auth_method: str,
    role: str | None = None,
    cwid: str | None = None,
    email: str | None = None,
    department: str | None = None,
) -> User:
    """Create or update a user record. Returns the User.

    Identity anchor:
    - SAML: `cwid` (unique, stable). `email` is stored when present but optional.
      A legacy row provisioned before CWID anchoring (matched by email, cwid
      still NULL) is upgraded in place -- its cwid is set -- not duplicated.
    - Simple auth: `email` (simple users have no CWID).

    The caller determines the role:
    - Simple auth resolves role from SystemConfig admin_users list
    - SAML auth resolves role from ED group membership (or None to preserve existing)

    Args:
        db: Database session.
        display_name: User's display name.
        auth_method: "simple" or "saml".
        role: Role to assign. None = preserve existing role for updates, "user" for new users.
        cwid: SSO identity anchor (SAML). Normalized by caller.
        email: User's email. Optional; stored when present. Normalized by caller.
        department: ED department name. None = leave the stored value alone, so a
            failed or empty ED read never clobbers a known department.
    """
    user = None
    if cwid:
        user = db.query(User).filter(User.cwid == cwid).first()
        if user is None and email:
            # Adopt a legacy row that predates CWID anchoring (email match, no cwid).
            user = db.query(User).filter(User.email == email, User.cwid.is_(None)).first()
    elif email:
        user = db.query(User).filter(User.email == email).first()

    if user:
        events = _identity_change_events(user, auth_method, role=role, cwid=cwid, email=email)
        user.display_name = display_name
        user.auth_method = auth_method
        if cwid:
            user.cwid = cwid
        if email:
            user.email = email
        if role is not None:
            user.role = role
        if department is not None:
            user.department = department
        db.commit()
        db.refresh(user)
        _emit(events)
        return user

    user = User(
        cwid=cwid,
        email=email,
        display_name=display_name,
        role=role or DEFAULT_ROLE,
        auth_method=auth_method,
        department=department,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent login committed this identity first. Drop our insert and
        # return the winner's record, keyed on whichever unique column actually
        # collided: cwid for SSO (email is nullable now, so it is not
        # necessarily the constraint that fired), email for simple auth. The
        # winner logged USER_CREATED; this request created nothing.
        db.rollback()
        q = db.query(User)
        user = (q.filter(User.cwid == cwid) if cwid else q.filter(User.email == email)).one()
        db.refresh(user)
        return user
    db.refresh(user)
    _emit([_created_event(user)])
    return user


def _audit_base(user: User, auth_method: str) -> dict[str, object]:
    """Fields every provisioning audit event carries. Never the raw email (#366)."""
    return {
        "user_id": user.id,
        "cwid": user.cwid,
        "auth_method": auth_method,
        "source": PROVISIONING_SOURCE,
    }


def _created_event(user: User) -> AuditEvent:
    fields = _audit_base(user, user.auth_method)
    fields.update(role=user.role, has_email=user.email is not None)
    return AuditEvent(USER_CREATED, fields)


def _identity_change_events(
    user: User,
    auth_method: str,
    *,
    role: str | None,
    cwid: str | None,
    email: str | None,
) -> list[AuditEvent]:
    """Audit events for the changes provision_user is about to apply to `user`.

    Read before the assignments so old values are still on the row. Only an
    actual change yields an event -- a routine re-login with identical values
    logs nothing. The email event records that the address changed, and whether
    there was one before, but never either address.
    """
    # The cwid the row will carry, so a legacy row's email/role events in the
    # same login name the cwid being linked rather than None.
    base = {**_audit_base(user, auth_method), "cwid": cwid or user.cwid}
    events: list[AuditEvent] = []
    if cwid and user.cwid != cwid:
        events.append(AuditEvent(USER_IDENTITY_LINKED, {**base, "old_cwid": user.cwid}))
    if email and user.email != email:
        events.append(AuditEvent(USER_EMAIL_UPDATED, {**base, "had_email": user.email is not None}))
    if role is not None and user.role != role:
        events.append(AuditEvent(ROLE_CHANGED, {**base, "old_role": user.role, "new_role": role}))
    return events


def _emit(events: list[AuditEvent]) -> None:
    """Log each event; called only after the change it describes has committed."""
    for event in events:
        logger.info(event.name, extra=event.fields)
