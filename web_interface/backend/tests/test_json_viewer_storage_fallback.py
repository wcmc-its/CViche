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
from sqlalchemy.orm import object_session


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
    # create_session_cookie reads the current epoch from a DB session;
    # `user` was just committed on the test's session, so borrow that one.
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, object_session(user)))


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


# --- #296: the shared pipeline outputs dir must not leak across runs ---


def _seed_shared_artifact(monkeypatch, tmp_path, basename):
    """Put a file in the SHARED src/unified_pipeline/outputs tree (candidate 2),
    which every run on the pod writes into, and point steps.py at it."""
    stage_dir = tmp_path / "src" / "unified_pipeline" / "outputs" / "stage_4_wcm_templates"
    stage_dir.mkdir(parents=True)
    (stage_dir / basename).write_bytes(b"PK\x03\x04 another run's parsed CV")
    # steps.py derives pipeline_dir from __file__; redirect it at the temp tree.
    monkeypatch.setattr(steps_mod, "__file__",
                        str(tmp_path / "web_interface" / "backend" / "app" / "api" / "steps.py"))


def test_owner_cannot_read_another_runs_docx_from_shared_dir(
    client, db, seed_simple_mode, monkeypatch, tmp_path
):
    """The exact #296 chain: a non-admin owning run A asks for a .docx that exists
    only under run B. The .json admin gate does not cover .docx, and the shared
    stage dir is not run-scoped, so this used to serve someone else's CV."""
    user, run = _user_and_run(db, role="user", suffix="-x296")
    _auth(client, user)
    monkeypatch.setattr(steps_mod, "get_storage", lambda: _FakeStorage({}))
    _seed_shared_artifact(monkeypatch, tmp_path, "victim-run-id_wcm.docx")

    resp = client.get(f"/api/run/{run.id}/data/victim-run-id_wcm.docx")
    assert resp.status_code == 404, (
        f"cross-run read: got {resp.status_code}, {len(resp.content)} bytes")
    assert b"parsed CV" not in resp.content


def test_owner_can_still_read_its_own_docx_from_shared_dir(
    client, db, seed_simple_mode, monkeypatch, tmp_path
):
    """The scoping must not break the legitimate case it guards -- the run's OWN
    artifact still resolves out of the shared dir."""
    user, run = _user_and_run(db, role="user", suffix="-o296")
    _auth(client, user)
    monkeypatch.setattr(steps_mod, "get_storage", lambda: _FakeStorage({}))
    _seed_shared_artifact(monkeypatch, tmp_path, f"{run.id}_wcm.docx")

    resp = client.get(f"/api/run/{run.id}/data/{run.id}_wcm.docx")
    assert resp.status_code == 200, resp.text
    assert b"parsed CV" in resp.content
# --- review hardening: invariants pinned at the point of use (#287 review) ---


def test_malformed_run_id_rejected(client, db, seed_simple_mode):
    """The router already 404s a slash-bearing run_id before the handler, and
    check_run_access requires an exact DB match. _validate_run_id pins that so a
    later {run_id:path} cannot silently make traversal reachable."""
    from fastapi import HTTPException

    # Called directly, so the assertion cannot be satisfied by the router 404ing
    # first -- that is exactly the ambiguity this guard is meant to remove.
    for bad in ["../../etc", "a/b", "run\x00", "", "x" * 65]:
        with pytest.raises(HTTPException) as exc:
            steps_mod._validate_run_id(bad)
        assert exc.value.status_code == 400, bad
    for ok in ["A1B2C3", "run-fallback-1", "run_fallback_2"]:
        steps_mod._validate_run_id(ok)  # must not raise


def test_non_printable_filename_rejected(client, db, seed_simple_mode, monkeypatch):
    """An encoded NUL used to reach pathlib and raise ValueError -> sanitized 500.
    It should be a clean 400 instead."""
    user, run = _user_and_run(db, role="admin", suffix="-np")
    _auth(client, user)
    monkeypatch.setattr(steps_mod, "get_storage", lambda: _FakeStorage({}))
    resp = client.get(f"/api/run/{run.id}/data/stage1a%00.json")
    assert resp.status_code == 400, resp.text
    assert resp.status_code != 500
