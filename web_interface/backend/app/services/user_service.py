"""User-related service functions."""
from sqlalchemy.orm import Session
from app.models import User


def provision_user(
    db: Session,
    display_name: str,
    auth_method: str,
    role: str | None = None,
    cwid: str | None = None,
    email: str | None = None,
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
    else:
        user = User(
            cwid=cwid,
            email=email,
            display_name=display_name,
            role=role or "user",
            auth_method=auth_method,
        )
        db.add(user)
    db.commit()
    db.refresh(user)
    return user
