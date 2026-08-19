"""Consent API endpoints."""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.client_ip import get_client_ip
from app.database import get_db
from app.models import User, Consent
from app.auth import get_current_user
from app.config_loader import get_config_value
from app.consent import get_consent_text, get_consent_hash
from app.schemas import ConsentStatus, ConsentSubmit, ConsentSubmitResponse

logger = logging.getLogger(__name__)
router = APIRouter()

DEFAULT_CONSENT_VERSION = "1.0"


@router.get("/consent", response_model=ConsentStatus)
def get_consent(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return current consent text, version, and whether the user has consented."""
    current_version = str(get_config_value(db, "consent_version") or DEFAULT_CONSENT_VERSION)
    consent_text = get_consent_text()
    current_hash = get_consent_hash()

    user_has_consented = (
        current_user.consent_version is not None
        and current_user.consent_version == current_version
    )

    return ConsentStatus(
        text=consent_text,
        version=current_version,
        user_has_consented=user_has_consented,
        current_hash=current_hash,
    )


@router.post("/consent", response_model=ConsentSubmitResponse)
def submit_consent(
    body: ConsentSubmit,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record consent and update user record."""
    current_version = str(get_config_value(db, "consent_version") or DEFAULT_CONSENT_VERSION)
    text_hash = get_consent_hash()

    # Consent is an append-only audit/event table by design: every submission
    # (including a repeat consent to the same version) intentionally gets its
    # own row -- this is not a bug to dedupe.
    consent_record = Consent(
        user_id=current_user.id,
        consent_version=current_version,
        consent_text_hash=text_hash,
        ip_address=get_client_ip(request),
        user_agent=request.headers.get("user-agent", "")[:512],
    )
    db.add(consent_record)

    # Update user record
    current_user.consent_version = current_version
    current_user.consent_date = datetime.now(timezone.utc)
    current_user.default_submission_type = body.default_submission_type

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    logger.info(
        "consent_given",
        extra={
            "user_id": current_user.id,
            "user_email": current_user.email,
            "consent_version": current_version,
            "default_submission_type": body.default_submission_type,
        },
    )

    return ConsentSubmitResponse(
        message="Consent recorded successfully.",
        consent_version=current_version,
        default_submission_type=body.default_submission_type,
    )
