"""Consent text management and version integrity checking."""
import hashlib
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Consent
from app.config_loader import get_config_value

logger = logging.getLogger(__name__)

CONSENT_TEXT_PATH = Path(__file__).parent.parent / "consent_text.md"

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
