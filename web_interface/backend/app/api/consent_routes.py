"""Consent API endpoints."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.client_ip import get_client_ip
from app.consent import get_current_consent_document
from app.database import get_db
from app.models import User
from app.schemas import ConsentStatus, ConsentSubmit, ConsentSubmitResponse
from app.services.consent_service import record_consent

router = APIRouter()


@router.get("/consent", response_model=ConsentStatus)
def get_consent(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return current consent text, version, and whether the user has consented."""
    document = get_current_consent_document(db)

    user_has_consented = (
        current_user.consent_version is not None
        and current_user.consent_version == document.version
    )

    return ConsentStatus(
        text=document.text,
        version=document.version,
        user_has_consented=user_has_consented,
        current_hash=document.hash,
    )


@router.post("/consent", response_model=ConsentSubmitResponse)
def submit_consent(
    body: ConsentSubmit,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record consent and update user record."""
    document = record_consent(
        db=db,
        user=current_user,
        default_submission_type=body.default_submission_type,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:512],
    )

    return ConsentSubmitResponse(
        message="Consent recorded successfully.",
        consent_version=document.version,
        default_submission_type=body.default_submission_type,
    )
