"""User-related service functions."""
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from app.models import User


def provision_user(
    db: Session,
    email: str,
    display_name: str,
    auth_method: str,
    role: str | None = None,
) -> User:
    """Create or update a user record. Returns the User.

    The caller determines the role:
    - Simple auth resolves role from SystemConfig admin_users list
    - SAML auth resolves role from ED group membership (or None to preserve existing)

    Args:
        db: Database session.
        email: User's email (will be stored as-is, caller should normalize).
        display_name: User's display name.
        auth_method: "simple" or "saml".
        role: Role to assign. None = preserve existing role for updates, "user" for new users.
    """
    user = db.query(User).filter(User.email == email).first()
    if user:
        user.display_name = display_name
        user.auth_method = auth_method
        if role is not None:
            user.role = role
        db.commit()
        db.refresh(user)
        return user

    user = User(
        email=email,
        display_name=display_name,
        role=role or "user",
        auth_method=auth_method,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        # A concurrent login committed this email first (unique(email) already
        # guards the row). Drop our insert and return the winner's record.
        db.rollback()
        user = db.query(User).filter(User.email == email).one()
    db.refresh(user)
    return user
