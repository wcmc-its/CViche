"""GET /api/admin/stats: the admin Overview tab's aggregates."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import User
from app.schemas import AdminStats
from app.services.admin_service import get_admin_stats

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/admin/stats
# ---------------------------------------------------------------------------
@router.get("/admin/stats", response_model=AdminStats)
async def get_stats(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> AdminStats:
    """Return overview statistics for the admin dashboard."""
    return get_admin_stats(db)
