"""Consent recording service.

Owns the domain workflow for POST /api/consent: resolving the current
consent document, creating the audit record, mutating the user's consent
fields, and managing the commit/rollback transaction. The route handler is
left with request parsing, dependency wiring, and response shaping.
"""
import logging
import re
from datetime import datetime, timezone

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.consent import ConsentDocument, get_current_consent_document
from app.models import Consent, User, UserStatus

logger = logging.getLogger(__name__)


def record_consent(
    db: Session,
    user: User,
    default_submission_type: str,
    ip_address: str | None,
    user_agent: str,
) -> ConsentDocument:
    """Record a user's consent to the current document and update their profile.

    `default_submission_type` is validated by the caller's request schema
    (ConsentSubmit's Literal type, on the router side) before this is called
    -- this function trusts the value rather than re-validating it, so the
    accepted values are defined in exactly one place.

    Owns the rest of the domain workflow:
    - resolves the current consent version/text/hash via
      get_current_consent_document()
    - creates the append-only Consent audit row (every submission -- including
      a repeat consent to the same version -- intentionally gets its own row;
      this is not a bug to dedupe)
    - mutates the user's consent fields
    - commits the transaction, rolling back and re-raising on failure

    Returns the ConsentDocument the consent was recorded against, so the
    caller can build its response without a second lookup.
    """
    document = get_current_consent_document(db)

    consent_record = Consent(
        user_id=user.id,
        consent_version=document.version,
        consent_text_hash=document.hash,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.add(consent_record)

    user.consent_version = document.version
    user.consent_date = datetime.now(timezone.utc)
    user.default_submission_type = default_submission_type

    try:
        db.commit()
    except Exception:
        db.rollback()
        raise

    logger.info(
        "consent_given",
        extra={
            "user_id": user.id,
            "user_email": user.email,
            "consent_version": document.version,
            "default_submission_type": default_submission_type,
        },
    )

    return document


# A published consent version is "<major>.<minor>", e.g. "1.2".
_CONSENT_VERSION_RE = re.compile(r"^(\d+)\.(\d+)$")


def next_consent_version(current: str) -> str:
    """The version a publish moves to: the minor number plus one ("1.1" -> "1.2",
    "1.9" -> "1.10"; compared as integers, never as decimals). Raises ValueError
    for a current version that is not "<major>.<minor>"."""
    match = _CONSENT_VERSION_RE.match(current.strip())
    if match is None:
        raise ValueError(f'Consent version "{current}" is not in <major>.<minor> form.')
    return f"{match.group(1)}.{int(match.group(2)) + 1}"


def count_users_to_reconsent(db: Session, version: str) -> int:
    """Active users who would have to agree again if ``version`` were current:
    those whose consent_version differs, including users who never consented
    (a NULL version, which SQL's != would silently skip)."""
    return (
        db.query(func.count(User.id))
        .filter(User.status == UserStatus.ACTIVE,
                or_(User.consent_version.is_(None), User.consent_version != version))
        .scalar() or 0
    )
