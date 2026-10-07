"""User-related service functions."""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import User, UserRole


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
        return user

    user = User(
        cwid=cwid,
        email=email,
        display_name=display_name,
        role=role or UserRole.USER,
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
        # necessarily the constraint that fired), email for simple auth.
        db.rollback()
        q = db.query(User)
        user = (q.filter(User.cwid == cwid) if cwid else q.filter(User.email == email)).one()
    db.refresh(user)
    return user
