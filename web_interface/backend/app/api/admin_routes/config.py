"""Admin routes over the runtime-editable SystemConfig keys: the config view
and update, publishing the next consent version (a write of consent_version),
and "sign out everyone" (a bump of session_epoch)."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import User
from app.schemas import AdminConfigResponse, AdminConfigUpdate, ConsentPublishPreview, ConsentPublishRequest
from app.services.admin_config_service import (
    bump_session_epoch, consent_publish_preview, load_admin_config, publish_next_consent_version,
    update_admin_config,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/admin/config
# ---------------------------------------------------------------------------
@router.get("/admin/config", response_model=AdminConfigResponse)
async def get_config(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> AdminConfigResponse:
    """Return current system configuration values."""
    return load_admin_config(db)


# ---------------------------------------------------------------------------
# PUT /api/admin/config
# ---------------------------------------------------------------------------
@router.put("/admin/config", response_model=AdminConfigResponse)
async def update_config(
    body: AdminConfigUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> AdminConfigResponse:
    """Update system configuration values."""
    return update_admin_config(db, body, admin)


# ---------------------------------------------------------------------------
# GET/POST /api/admin/consent/publish  -- publish the next consent version
# ---------------------------------------------------------------------------
@router.get("/admin/consent/publish", response_model=ConsentPublishPreview)
async def preview_consent_publish(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> ConsentPublishPreview:
    """The next consent version and how many active users must agree again if it is published."""
    return consent_publish_preview(db)


@router.post("/admin/consent/publish", response_model=ConsentPublishPreview)
async def publish_consent_version(
    body: ConsentPublishRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> ConsentPublishPreview:
    """Publish the next consent version. ``body.version`` is the one the admin was
    shown; if another admin published first it is no longer the next one (409)."""
    return publish_next_consent_version(db, body.version, admin)


# ---------------------------------------------------------------------------
# POST /api/admin/sessions/revoke-all
# ---------------------------------------------------------------------------
# response_model=None: the return annotation types the handler without
# generating a response schema, so the OpenAPI document is unchanged.
@router.post("/admin/sessions/revoke-all", response_model=None)
async def revoke_all_sessions(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, str | int]:
    """Invalidate every active session ("sign out everyone").

    Bumps the global session epoch in SystemConfig. Each cookie carries the
    epoch in force when it was minted, so the next request from any existing
    session -- including the admin who pressed this -- fails the epoch check in
    get_current_user (and the websocket auth) and is bounced to login. This is
    the only way to revoke stateless signed-cookie sessions before their TTL --
    use it after a credential leak, a permissions change, or to force re-auth.
    """
    new_epoch = bump_session_epoch(db, admin)
    return {
        "message": "All sessions revoked. Everyone must log in again.",
        "session_epoch": new_epoch,
    }
