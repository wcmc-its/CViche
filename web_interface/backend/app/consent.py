"""Consent text management and version integrity checking."""
import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Consent, User
from app.config_loader import get_config_value

logger = logging.getLogger(__name__)

CONSENT_TEXT_PATH = Path(__file__).parent.parent / "consent_text.md"

DEFAULT_CONSENT_VERSION = "1.0"

# Module-level cache: populated on startup, used by endpoints
_consent_text: str | None = None
_consent_text_hash: str | None = None


def load_consent_text() -> str:
    """Load consent text from the markdown file."""
    global _consent_text, _consent_text_hash
    text = CONSENT_TEXT_PATH.read_text(encoding="utf-8")
    _consent_text = text
    _consent_text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text


def get_consent_text() -> str:
    """Return cached consent text (loaded on startup)."""
    if _consent_text is None:
        load_consent_text()
    return _consent_text  # type: ignore[return-value]


def get_consent_hash() -> str:
    """Return cached SHA-256 hash of the consent text."""
    if _consent_text_hash is None:
        load_consent_text()
    return _consent_text_hash  # type: ignore[return-value]


@dataclass(frozen=True)
class ConsentDocument:
    """The current consent document: version, text, and hash, resolved together.

    Callers that need any of these values go through
    get_current_consent_document() instead of looking each one up
    independently, which is what previously let version and hash lookups at
    a call site drift out of step with each other. Structured as one
    immutable value so a consistency check (e.g. confirming `hash` is
    actually the hash of `text`) can be added inside
    get_current_consent_document() later without changing any caller.
    """
    version: str
    text: str
    hash: str


def get_current_consent_document(db: Session) -> ConsentDocument:
    """Resolve the current consent version, text, and hash as one unit.

    Single place responsible for resolving these three values -- replaces
    independently calling get_config_value(db, "consent_version"),
    get_consent_text(), and get_consent_hash() at each call site.
    """
    version = str(get_config_value(db, "consent_version") or DEFAULT_CONSENT_VERSION)
    return ConsentDocument(
        version=version,
        text=get_consent_text(),
        hash=get_consent_hash(),
    )


def require_current_consent(db: Session, user: User) -> None:
    """Raise 403 ``consent_required`` unless ``user`` has accepted the current
    consent version. The first check of every request that starts work on a
    CV: /upload, and POST /batches (#1114), which would otherwise create a
    batch and post its Teams card for a user every upload then refuses."""
    current_version = str(get_config_value(db, "consent_version") or DEFAULT_CONSENT_VERSION)
    if user.consent_version != current_version:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "consent_required",
                "message": "Please review and accept the updated consent terms.",
            },
        )


def check_consent_integrity(db: Session) -> None:
    """Check consent text hash against the last recorded hash for the current version.

    Called on startup. Logs a warning if the consent text has changed but the
    version string has not been bumped -- this prevents silent consent text changes
    that bypass the re-consent mechanism.
    """
    current_hash = get_consent_hash()
    current_version = get_config_value(db, "consent_version")

    if current_version is None:
        logger.info("No consent version configured -- skipping integrity check.")
        return

    # Find the most recent consent record for this version
    last_consent = (
        db.query(Consent)
        .filter(Consent.consent_version == str(current_version))
        .order_by(Consent.timestamp.desc())
        .first()
    )

    if last_consent is None:
        logger.info(
            "No consent records found for version %s -- first deployment with this version.",
            current_version,
        )
        return

    if last_consent.consent_text_hash != current_hash:
        logger.warning(
            "Consent text has changed but version is still %s. "
            "Bump the version in config to require re-consent. "
            "Stored hash: %s, current hash: %s",
            current_version,
            last_consent.consent_text_hash,
            current_hash,
        )
    else:
        logger.info(
            "Consent text integrity OK for version %s (hash: %s...)",
            current_version,
            current_hash[:12],
        )
