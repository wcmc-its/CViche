"""GET /api/admin/export/{export_type}: the CSV downloads.

The one admin route staff may call (require_view_all_runs, not
require_admin); which types staff may export is admin_export_service's rule.
"""
from datetime import datetime

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.auth import require_view_all_runs
from app.database import get_db
from app.models import User
from app.services.admin_export_service import open_export

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/admin/export/{export_type}
# ---------------------------------------------------------------------------
@router.get("/admin/export/{export_type}")
async def export_csv(
    export_type: str,
    db: Session = Depends(get_db),
    viewer: User = Depends(require_view_all_runs),
) -> StreamingResponse:
    """Export data as CSV. Supported types: runs, users, consent, feedback.
    Admins may export any type; staff only _STAFF_EXPORT_TYPES."""
    return StreamingResponse(
        open_export(db, viewer, export_type),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="cviche_{export_type}_{datetime.now().strftime("%Y%m%d")}.csv"'
        },
    )
