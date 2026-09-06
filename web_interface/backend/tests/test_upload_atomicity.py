"""Atomicity guard for the /upload endpoint (issue #170).

The durable archive of the original upload is the run's only recoverable input
(the pod-local copy is ephemeral). So a failure to write it must be FATAL: the
endpoint returns an error and creates NO run record -- otherwise a "created" run
is orphaned in the DB and on the Runs dashboard while its input is unrecoverable,
and partial objects are left in the store. The cross-run by-submitter index, by
contrast, is a navigation pointer the pipeline never reads, so its failure must
NOT fail the upload.

These tests pin both halves of that contract via the in-memory SQLite TestClient
harness from conftest (no real S3, no real file parsing).
"""
from unittest.mock import MagicMock, patch

import pytest

from app.models import User, Run
from sqlalchemy.orm import object_session


def _make_user(db):
    """Create an allowed, consented simple-mode user and return it."""
    user = User(
        email="test@example.com",
        display_name="Test User",
        role="user",
        consent_version="1.0",  # matches seed_simple_mode's consent_version config
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _auth(client, user):
    from app.auth import create_session_cookie, COOKIE_NAME
    # create_session_cookie reads the current epoch from a DB session;
    # `user` was just committed on the test's session, so borrow that one.
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, object_session(user)))


def _bypass_file_validation(tmp_path):
    """Patch out the magic-byte / text-extraction / template checks so a dummy
    byte payload reaches the storage step, and redirect the ephemeral pod-local
    write into tmp_path. Returns a list of patch context managers."""
    return [
        patch("app.api.upload.UPLOAD_DIR", tmp_path),
        patch("app.api.upload._validate_docx_magic", return_value=True),
        # None == "couldn't extract"; the endpoint fails open and skips the
        # min-text gate, which is all we need to reach the storage step.
        patch("app.api.upload._extract_text", return_value=None),
        patch("app.api.upload.detect_wcm_template", return_value=(False, None)),
    ]


def _post_dummy_upload(client):
    return client.post(
        "/api/upload",
        files={"file": ("cv.docx", b"PK\x03\x04dummy-docx-bytes", "application/octet-stream")},
    )


def test_upload_aborts_and_creates_no_run_when_archive_fails(client, db, seed_simple_mode, tmp_path):
    """A storage failure on the durable input archive returns 502 and leaves NO
    Run row -- the exact orphan the dashboard would otherwise hide while the S3
    folder lingered."""
    user = _make_user(db)
    _auth(client, user)

    storage = MagicMock()
    storage.put_file_exclusive.side_effect = Exception("S3 unavailable")

    patches = _bypass_file_validation(tmp_path)
    patches.append(patch("app.api.upload.get_storage", return_value=storage))
    for p in patches:
        p.start()
    try:
        resp = _post_dummy_upload(client)
    finally:
        for p in patches:
            p.stop()

    assert resp.status_code == 502, resp.text
    assert resp.json()["detail"]["error"] == "storage_unavailable"
    # The contract: nothing persisted.
    assert db.query(Run).count() == 0
    storage.put_file_exclusive.assert_called()  # we did attempt the durable write


def test_upload_succeeds_when_by_submitter_index_fails(client, db, seed_simple_mode, tmp_path):
    """A failure of the best-effort by-submitter index must NOT fail the upload:
    the run + its input are already durably stored, so the run is still created."""
    user = _make_user(db)
    _auth(client, user)

    storage = MagicMock()
    storage.put_file_exclusive.return_value = None  # durable archive succeeds
    storage.put_global.side_effect = Exception("index write failed")

    patches = _bypass_file_validation(tmp_path)
    patches.append(patch("app.api.upload.get_storage", return_value=storage))
    for p in patches:
        p.start()
    try:
        resp = _post_dummy_upload(client)
    finally:
        for p in patches:
            p.stop()

    assert resp.status_code == 200, resp.text
    assert db.query(Run).count() == 1
    assert db.query(Run).first().status == "created"
    storage.put_global.assert_called()  # we did attempt (and swallow) the index write
