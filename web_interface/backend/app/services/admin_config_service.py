"""The SystemConfig keys an admin changes at runtime (#335).

Behind /api/admin/config, /api/admin/consent/publish and
/api/admin/sessions/revoke-all: allowed and admin users, the rate limits, the
consent version and the session epoch -- config_loader.ADMIN_MANAGED_KEYS,
the keys a reseed leaves alone. Every write is stamped with the admin's id
and audit-logged. Not to be confused with config_service, the env-var
constants.
"""
import json
import logging

from sqlalchemy.orm import Session

from app.audit_events import CONSENT_VERSION_PUBLISHED
from app.auth import SessionEpochUnreadable
from app.consent import get_current_consent_document
from app.errors import conflict, validation_error
from app.models import SystemConfig, User
from app.schemas import AdminConfigResponse, AdminConfigUpdate, ConsentPublishPreview
from app.services.consent_service import count_users_to_reconsent, next_consent_version

logger = logging.getLogger(__name__)


def load_admin_config(db: Session) -> AdminConfigResponse:
    """The admin config view of SystemConfig; a key with no row reads as its default."""
    configs = db.query(SystemConfig).all()
    config_dict = {}
    for c in configs:
        config_dict[c.key] = json.loads(c.value)

    return AdminConfigResponse(
        allowed_users=config_dict.get("allowed_users", []),
        admin_users=config_dict.get("admin_users", []),
        rate_limit_daily=config_dict.get("rate_limit_daily", 10),
        rate_limit_monthly=config_dict.get("rate_limit_monthly", 50),
        consent_version=config_dict.get("consent_version", "1.0"),
        auth_mode=config_dict.get("auth_mode", "simple"),
    )


def _stage_config_value(
    db: Session, key: str, new_value: object, admin_id: int, changes: dict[str, dict[str, object]]
) -> None:
    """Stage one SystemConfig write, recording old/new in ``changes``; an unchanged value is left alone."""
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        old_value = json.loads(row.value)
        if old_value != new_value:
            changes[key] = {"old": old_value, "new": new_value}
            row.value = json.dumps(new_value)
            row.updated_by = admin_id
    else:
        changes[key] = {"old": None, "new": new_value}
        db.add(
            SystemConfig(
                key=key, value=json.dumps(new_value), updated_by=admin_id
            )
        )


def update_admin_config(db: Session, update: AdminConfigUpdate, admin: User) -> AdminConfigResponse:
    """Validate and write the keys ``update`` carries, then return the config.

    Fields are checked in order, and the first invalid one is a 422 before
    anything is committed. One commit, and one admin_config_changed audit line
    for the keys whose value changed.
    """
    changes: dict[str, dict[str, object]] = {}

    if update.allowed_users is not None:
        _stage_config_value(db, "allowed_users", update.allowed_users, admin.id, changes)

    if update.admin_users is not None:
        # Validate: at least one admin must remain
        if len(update.admin_users) < 1:
            raise validation_error("At least one admin user is required.")
        # Ensure all admin users are in allowed users list
        allowed = update.allowed_users
        if allowed is None:
            # Use current allowed list
            row = (
                db.query(SystemConfig)
                .filter(SystemConfig.key == "allowed_users")
                .first()
            )
            allowed = json.loads(row.value) if row else []

        for admin_email in update.admin_users:
            if admin_email.lower() not in [a.lower() for a in allowed]:
                raise validation_error(f"Admin user {admin_email} must also be in the allowed users list.")
        _stage_config_value(db, "admin_users", update.admin_users, admin.id, changes)

    if update.rate_limit_daily is not None:
        if update.rate_limit_daily < 1:
            raise validation_error("Daily rate limit must be positive.")
        _stage_config_value(db, "rate_limit_daily", update.rate_limit_daily, admin.id, changes)

    if update.rate_limit_monthly is not None:
        if update.rate_limit_monthly < 1:
            raise validation_error("Monthly rate limit must be positive.")
        _stage_config_value(db, "rate_limit_monthly", update.rate_limit_monthly, admin.id, changes)

    if update.consent_version is not None:
        _stage_config_value(db, "consent_version", update.consent_version, admin.id, changes)

    db.commit()

    if changes:
        logger.info(
            "admin_config_changed: admin=%s changes=%s",
            admin.email,
            json.dumps(changes),
        )

    return load_admin_config(db)


def consent_publish_preview(db: Session) -> ConsentPublishPreview:
    """The next consent version and how many active users would have to agree
    again; a current version not in <major>.<minor> form is a 422."""
    current = get_current_consent_document(db).version
    try:
        proposed = next_consent_version(current)
    except ValueError as exc:
        raise validation_error(str(exc)) from exc
    return ConsentPublishPreview(
        current_version=current,
        next_version=proposed,
        users_to_reconsent=count_users_to_reconsent(db, proposed),
    )


def publish_next_consent_version(db: Session, shown_version: str, admin: User) -> ConsentPublishPreview:
    """Publish the next consent version if it is still ``shown_version``, the one
    the admin reviewed; otherwise another admin published first (409). The write
    is an ordinary config update, followed by a CONSENT_VERSION_PUBLISHED event."""
    preview = consent_publish_preview(db)
    if shown_version != preview.next_version:
        raise conflict(
            f"The next consent version is now {preview.next_version}, not {shown_version}. "
            "Reload and review before publishing."
        )
    update_admin_config(db, AdminConfigUpdate(consent_version=preview.next_version), admin)
    logger.info(
        CONSENT_VERSION_PUBLISHED,
        extra={"admin": admin.email, "old_version": preview.current_version,
               "new_version": preview.next_version, "users_to_reconsent": preview.users_to_reconsent},
    )
    return preview


def _current_session_epoch(row: SystemConfig | None) -> int:
    """The epoch to bump from, read straight off the SystemConfig row.

    Deliberately not auth.get_session_epoch(): that one fails closed on a
    missing row (it must -- see #657 review, thread 9), but here a missing row
    is the one benign case, an instance that has never been revoked. It is
    written back below, so the next read is well-formed either way. An
    unparseable value is still a misconfiguration and propagates as a 500
    rather than resetting the counter to 0 and un-revoking every session.
    """
    if row is None:
        return 0
    try:
        value = json.loads(row.value)
    except (TypeError, ValueError) as exc:
        raise SessionEpochUnreadable("session_epoch is not valid JSON") from exc
    # bool first: it is an int subclass, so `true` would otherwise read as 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise SessionEpochUnreadable(
            "session_epoch is not an integer (got %s)" % type(value).__name__
        )
    return value


def bump_session_epoch(db: Session, admin: User) -> int:
    """Invalidate every session cookie: add one to the global session epoch,
    stamped with the admin and audit-logged. Returns the new epoch."""
    row = db.query(SystemConfig).filter(SystemConfig.key == "session_epoch").first()
    new_epoch = _current_session_epoch(row) + 1
    if row:
        row.value = json.dumps(new_epoch)
        row.updated_by = admin.id
    else:
        db.add(SystemConfig(key="session_epoch", value=json.dumps(new_epoch),
                            updated_by=admin.id))
    db.commit()

    logger.info("admin_sessions_revoked_all: admin=%s new_epoch=%d", admin.email, new_epoch)
    return new_epoch
