"""POST /api/consent behavior after the schemas/consent_routes review fixes.

Covers: a valid submission returns the typed ConsentSubmitResponse fields; an
invalid default_submission_type is rejected by the new Literal type (422, no
manual check needed); the Consent audit row is persisted with the request's
resolved ip_address; and a failed db.commit() rolls back rather than leaving a
dangling transaction.
"""
import os
from contextlib import contextmanager
from unittest.mock import patch

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")


def _seed_user(db, email="submitter@example.com"):
    from app.models import User
    user = User(email=email, display_name="Submitter", role="user")
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
