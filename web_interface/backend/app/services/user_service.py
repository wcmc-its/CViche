"""User-related service functions."""
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
    else:
        user = User(
            email=email,
            display_name=display_name,
            role=role or "user",
            auth_method=auth_method,
        )
        db.add(user)
    db.commit()
    db.refresh(user)
    return user
