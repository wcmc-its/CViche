"""The JSON viewer endpoint must fall back to durable storage (S3 in prod) when
the file is not on the local pod filesystem -- the same #38 durability fix the
download endpoint already has. Without it, clicking a stage's .json file name in
a multi-replica deployment 500s ("Failed to load JSON file.") even though the
Download button works, because the viewer request can land on a pod that never
wrote that file.
"""
import json

import pytest

import app.api.steps as steps_mod
from app.models import User, Run
from app.auth import create_session_cookie, COOKIE_NAME


def _user_and_run(db, role="admin", suffix=""):
    # Stage JSON is admin-only, so the viewer/fallback tests run as admin;
    # the 403 tests below pass role="user".
    user = User(email=f"test{suffix}@example.com", display_name="Test User", role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    run = Run(id=f"run-fallback{suffix or '-1'}", user_id=user.id, status="completed",
              filename="cv.docx", file_type="docx")
    db.add(run)
    db.commit()
    return user, run


def _auth(client, user):
    client.cookies.set(COOKIE_NAME, create_session_cookie(user))


class _FakeStorage:
    """Minimal storage stub: holds {key: bytes}, raises FileNotFoundError on miss."""

    def __init__(self, files):
        self.files = files

    def get_file(self, run_id, key):
        try:
            return self.files[key]
        except KeyError:
            raise FileNotFoundError(key)

    def exists(self, run_id, key):
        return key in self.files

    def get_download_url(self, run_id, key, download_name=None):
        return None


def test_viewer_falls_back_to_storage_when_not_on_local_pod(
    client, db, seed_simple_mode, monkeypatch
):
    user, run = _user_and_run(db)
    _auth(client, user)

    payload = {"hello": "world", "n": 1}
    monkeypatch.setattr(
        steps_mod, "get_storage",
        lambda: _FakeStorage({"outputs/stage1a.json": json.dumps(payload).encode()}),
    )

    # File is NOT on local disk -> _resolve_safe_path 404s -> storage fallback hits.
    resp = client.get(f"/api/run/{run.id}/json/stage1a.json")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["content"] == payload
    assert body["size_bytes"] > 0


def test_viewer_404_when_absent_in_both_local_and_storage(
    client, db, seed_simple_mode, monkeypatch
):
    user, run = _user_and_run(db)
    _auth(client, user)

    monkeypatch.setattr(steps_mod, "get_storage", lambda: _FakeStorage({}))

    resp = client.get(f"/api/run/{run.id}/json/missing.json")
    assert resp.status_code == 404
    # Generic catch-all must not leak as a 500.
    assert resp.json().get("detail", {}).get("error") != "internal_error"


# --- Stage JSON is admin-only (internal pipeline artifact) ---


def test_viewer_forbidden_for_non_admin(client, db, seed_simple_mode):
    """A non-admin (even the run owner) cannot view stage JSON."""
    user, run = _user_and_run(db, role="user", suffix="-va")
    _auth(client, user)
    resp = client.get(f"/api/run/{run.id}/json/stage1a.json")
    assert resp.status_code == 403


def test_data_json_download_forbidden_for_non_admin(client, db, seed_simple_mode):
    """The download route also blocks .json for non-admins (before file lookup)."""
    user, run = _user_and_run(db, role="user", suffix="-dj")
    _auth(client, user)
    resp = client.get(f"/api/run/{run.id}/data/stage1a.json")
    assert resp.status_code == 403


def test_data_docx_still_allowed_for_owner(client, db, seed_simple_mode, monkeypatch):
    """The .docx (real output) stays downloadable by a non-admin owner -- the
    gate must not blanket-block the data route. Absent file -> 404, never 403."""
    user, run = _user_and_run(db, role="user", suffix="-dd")
    _auth(client, user)
    monkeypatch.setattr(steps_mod, "get_storage", lambda: _FakeStorage({}))
    resp = client.get(f"/api/run/{run.id}/data/cv.docx")
    assert resp.status_code != 403


# --- CV Insights: the two user-facing fields stay owner-accessible ---


def test_cv_insights_allowed_for_non_admin_owner(client, db, seed_simple_mode, monkeypatch):
    """A non-admin owner can read cv_owner + location (the "CV Insights" panel),
    but ONLY those fields -- the rest of the stage JSON is not leaked."""
    user, run = _user_and_run(db, role="user", suffix="-ci")
    _auth(client, user)
    payload = {
        "cv_owner": {"full_name": "Jane Doe"},
        "cv_owner_location": {"inference_success": True},
        "secret_internal_field": "should not leak",
    }
    monkeypatch.setattr(
        steps_mod, "get_storage",
        lambda: _FakeStorage({"outputs/x_fields.json": json.dumps(payload).encode()}),
    )
    resp = client.get(f"/api/run/{run.id}/cv-insights/x_fields.json")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "cv_owner": {"full_name": "Jane Doe"},
        "cv_owner_location": {"inference_success": True},
    }
    assert "secret_internal_field" not in body


def test_cv_insights_rejects_non_fields_json(client, db, seed_simple_mode, monkeypatch):
    """Not a generic 2-key reader: only *_fields.json is accepted."""
    user, run = _user_and_run(db, role="user", suffix="-cin")
    _auth(client, user)
    monkeypatch.setattr(
        steps_mod, "get_storage",
        lambda: _FakeStorage({"outputs/stage1a.json": json.dumps({"cv_owner": {}}).encode()}),
    )
    resp = client.get(f"/api/run/{run.id}/cv-insights/stage1a.json")
    assert resp.status_code == 404
