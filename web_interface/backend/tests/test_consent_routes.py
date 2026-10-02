"""POST /api/consent behavior after the schemas/consent_routes review fixes.

Covers: a valid submission returns the typed ConsentSubmitResponse fields; an
invalid default_submission_type is rejected by the new Literal type (422, no
manual check needed); the Consent audit row is persisted with the request's
resolved ip_address; and a failed db.commit() rolls back rather than leaving a
dangling transaction.
"""
import json
import os
from contextlib import contextmanager
from unittest.mock import patch

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")


def _seed_user(db, email="submitter@example.com", role="user"):
    from app.models import User
    user = User(email=email, display_name="Submitter", role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@contextmanager
def _as_user(user):
    """Override get_current_user to return a real, committed User row."""
    from app.main import app
    from app.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_submit_consent_valid_returns_typed_response(client, db, seed_simple_mode):
    """A valid own_cv submission returns 200 with the ConsentSubmitResponse fields."""
    user = _seed_user(db)
    with _as_user(user):
        resp = client.post("/api/consent", json={"default_submission_type": "own_cv"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["message"] == "Consent recorded successfully."
    assert body["consent_version"] == "1.0"
    assert body["default_submission_type"] == "own_cv"


def test_submit_consent_invalid_type_returns_422(client, db, seed_simple_mode):
    """An invalid default_submission_type is rejected by Literal validation."""
    user = _seed_user(db)
    with _as_user(user):
        resp = client.post("/api/consent", json={"default_submission_type": "root"})

    assert resp.status_code == 422


def test_submit_consent_persists_row_with_ip_address(client, db, seed_simple_mode):
    """A Consent audit row is persisted, carrying the resolved client IP."""
    from app.models import Consent

    user = _seed_user(db)
    with _as_user(user):
        resp = client.post(
            "/api/consent",
            json={"default_submission_type": "own_cv"},
            headers={"X-Forwarded-For": "203.0.113.5"},
        )

    assert resp.status_code == 200
    record = db.query(Consent).filter(Consent.user_id == user.id).one()
    assert record.ip_address == "203.0.113.5"
    assert record.consent_version == "1.0"


def test_submit_consent_commit_failure_rolls_back(client, db, seed_simple_mode):
    """A failed db.commit() triggers db.rollback() and the error propagates."""
    user = _seed_user(db)
    with _as_user(user):
        with patch.object(db, "commit", side_effect=RuntimeError("boom")), \
             patch.object(db, "rollback") as mock_rollback:
            resp = client.post("/api/consent", json={"default_submission_type": "own_cv"})
            mock_rollback.assert_called_once()

    assert resp.status_code == 500


# --- Publish a new consent version (admin) ---

def _seed_consent_users(db, version="1.1"):
    """Four users on consent 1.1: one current, one stale, one never consented, one disabled-and-stale."""
    from app.models import User
    db.add_all([
        User(email="current@example.com", display_name="Current", consent_version=version),
        User(email="stale@example.com", display_name="Stale", consent_version="1.0"),
        User(email="never@example.com", display_name="Never", consent_version=None),
        User(email="off@example.com", display_name="Off", consent_version="1.0", status="disabled"),
    ])
    db.commit()


def _set_consent_version(db, version):
    from app.models import SystemConfig
    row = db.query(SystemConfig).filter(SystemConfig.key == "consent_version").first()
    row.value = json.dumps(version)
    db.commit()


def test_next_consent_version_increments_minor_as_an_integer():
    from app.services.consent_service import next_consent_version
    assert next_consent_version("1.1") == "1.2"
    assert next_consent_version("1.9") == "1.10"
    assert next_consent_version("2.0") == "2.1"
    import pytest
    with pytest.raises(ValueError):
        next_consent_version("v2")


def test_count_users_to_reconsent_counts_active_users_off_the_version(db):
    from app.services.consent_service import count_users_to_reconsent
    _seed_consent_users(db)
    # Against 1.2 all three active users differ (the never-consented one included);
    # the disabled user is not counted. Against 1.1 only the stale and never users differ.
    assert count_users_to_reconsent(db, "1.2") == 3
    assert count_users_to_reconsent(db, "1.1") == 2


def test_consent_publish_preview_reports_next_version_and_affected_users(client, db, seed_simple_mode):
    _set_consent_version(db, "1.1")
    _seed_consent_users(db)
    with _as_user(_seed_user(db, "boss@example.com", role="admin")):
        body = client.get("/api/admin/consent/publish").json()
    # boss@example.com has no consent_version either, so is the fourth affected user.
    assert body == {"current_version": "1.1", "next_version": "1.2", "users_to_reconsent": 4}


def test_consent_publish_writes_next_version_and_audit_logs(client, db, seed_simple_mode, caplog):
    import logging
    _set_consent_version(db, "1.1")
    with _as_user(_seed_user(db, "boss@example.com", role="admin")), caplog.at_level(logging.INFO):
        resp = client.post("/api/admin/consent/publish", json={"version": "1.2"})
        assert resp.status_code == 200
        assert client.get("/api/admin/config").json()["consent_version"] == "1.2"
    assert any(r.getMessage() == "CONSENT_VERSION_PUBLISHED" and r.new_version == "1.2" for r in caplog.records)


def test_consent_publish_rejects_a_stale_version(client, db, seed_simple_mode):
    _set_consent_version(db, "1.2")
    with _as_user(_seed_user(db, "boss@example.com", role="admin")):
        resp = client.post("/api/admin/consent/publish", json={"version": "1.2"})
        assert resp.status_code == 409
        assert client.get("/api/admin/config").json()["consent_version"] == "1.2"


def test_consent_publish_is_admin_only(client, db, seed_simple_mode):
    _set_consent_version(db, "1.1")
    with _as_user(_seed_user(db, "member@example.com")):
        assert client.get("/api/admin/consent/publish").status_code == 403
        assert client.post("/api/admin/consent/publish", json={"version": "1.2"}).status_code == 403
    from app.models import SystemConfig
    row = db.query(SystemConfig).filter(SystemConfig.key == "consent_version").first()
    assert json.loads(row.value) == "1.1"
