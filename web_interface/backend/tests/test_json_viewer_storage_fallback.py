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


def _user_and_run(db):
    user = User(email="test@example.com", display_name="Test User", role="user")
    db.add(user)
    db.commit()
    db.refresh(user)
    run = Run(id="run-fallback-1", user_id=user.id, status="completed",
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
