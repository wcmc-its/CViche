"""Admin user management: list users with their stats, and change one user's
role, status or limits under admin_policy's safety rules."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import User
from app.schemas import AdminUser, AdminUserUpdate
from app.services.admin_service import apply_user_update, get_users_with_stats

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/admin/users
# ---------------------------------------------------------------------------
@router.get("/admin/users", response_model=list[AdminUser])
async def get_users(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> list[AdminUser]:
    """Return all users with per-user stats."""
    return get_users_with_stats(db)


# ---------------------------------------------------------------------------
# PUT /api/admin/users/{user_id}
# ---------------------------------------------------------------------------
@router.put("/admin/users/{user_id}", response_model=AdminUser)
async def update_user(
    user_id: int,
    body: AdminUserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> AdminUser:
    """Update a user's role, status, or limits."""
    return apply_user_update(db, user_id, body, admin)
