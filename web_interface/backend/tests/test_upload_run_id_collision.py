"""Run-id collision closes with an exclusive create + retry, not an overwrite
(issue #685).

Before this fix, /upload and restart_run wrote the durable archive with a
plain put_file: a repeated run id (the old generator drew from ~2e8 effective
values, not the nominal 36**6) silently overwrote whatever was already
archived under that id -- one user's CV replacing another's. The fix makes
every archive write (and the pod-local upload write) an exclusive create, and
regenerates the run id and retries, bounded, on a collision.

These tests drive the real LocalRunStorage (not a mock) for the collision
tests, so "the first user's data survives" is proven against the actual
filesystem write path, not against an assertion that a mock was called.
"""
import asyncio
import re
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import object_session

from app.models import Run, User
from app.storage.base import StorageKeyExists
from app.storage.local_storage import LocalRunStorage
from app.api import upload as upload_module
from app.api.upload import generate_run_id


# --- shared helpers (mirrors test_upload_atomicity.py's, kept local so this
# file doesn't depend on another test module's internals) -------------------

def _make_user(db, email="collision@example.com"):
    user = User(
        email=email,
        display_name="Collision Test",
        role="user",
        consent_version="1.0",  # matches seed_simple_mode's consent_version config
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _auth(client, user):
    from app.auth import create_session_cookie, COOKIE_NAME
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, object_session(user)))


def _bypass_file_validation(upload_dir):
    return [
        patch("app.api.upload.UPLOAD_DIR", upload_dir),
        patch("app.api.upload._validate_docx_magic", return_value=True),
        patch("app.api.upload._extract_text", return_value=None),
        patch("app.api.upload.detect_wcm_template", return_value=(False, None)),
    ]


def _post_dummy_upload(client):
    return client.post(
        "/api/upload",
        files={"file": ("cv.docx", b"PK\x03\x04dummy-docx-bytes", "application/octet-stream")},
    )


# --- (a) LocalRunStorage.put_file_exclusive itself --------------------------

def test_local_put_file_exclusive_raises_on_second_write(tmp_path):
    storage = LocalRunStorage(base_dir=str(tmp_path))
    storage.put_file_exclusive("R1", "input/cv.docx", b"first-bytes")

    with pytest.raises(StorageKeyExists):
        storage.put_file_exclusive("R1", "input/cv.docx", b"second-bytes")

    # The first write must survive the failed second one untouched.
    assert storage.get_file("R1", "input/cv.docx") == b"first-bytes"


# --- (c) /upload regenerates the id on a real storage collision -------------

def test_upload_regenerates_id_on_real_storage_collision(client, db, seed_simple_mode, tmp_path):
    user = _make_user(db)
    _auth(client, user)

    storage = LocalRunStorage(base_dir=str(tmp_path / "storage"))
    # Pre-seed the archive as if run AAAAAA (another user's upload) already
    # landed there before this request draws the same id.
    other_bytes = b"PK\x03\x04 someone-elses-cv-bytes"
    storage.put_file("AAAAAA", "input/AAAAAA.docx", other_bytes)
    storage.put_file("AAAAAA", "input/manifest.json", b'{"run_id": "AAAAAA"}')

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    patches = _bypass_file_validation(upload_dir)
    patches.append(patch("app.api.upload.get_storage", return_value=storage))
    patches.append(patch("app.api.upload.generate_run_id", side_effect=["AAAAAA", "BBBBBB"]))
    for p in patches:
        p.start()
    try:
        resp = _post_dummy_upload(client)
    finally:
        for p in patches:
            p.stop()

    assert resp.status_code == 200, resp.text
    assert resp.json()["run_id"] == "BBBBBB"

    # The run row belongs to the fresh id; the colliding id never got one.
    assert db.query(Run).filter(Run.id == "BBBBBB").first() is not None
    assert db.query(Run).filter(Run.id == "AAAAAA").first() is None

    # The whole point: the first user's archive was never touched.
    assert storage.get_file("AAAAAA", "input/AAAAAA.docx") == other_bytes
    assert storage.exists("BBBBBB", "input/BBBBBB.docx")


# --- (d) restart_run regenerates the id on the same real collision ---------

def test_restart_regenerates_id_on_real_storage_collision(db, tmp_path):
    from app.api import runs as runs_api

    user = _make_user(db, email="restart-collision@example.com")
    original = Run(
        id="ORIGB1",
        filename="my cv.docx",
        file_type="docx",
        status="completed",
        user_id=user.id,
        submission_type="standard",
    )
    db.add(original)
    db.commit()

    storage = LocalRunStorage(base_dir=str(tmp_path / "storage"))
    other_bytes = b"PK\x03\x04 someone-elses-restarted-cv"
    storage.put_file("AAAAAA", "input/AAAAAA.docx", other_bytes)
    storage.put_file("AAAAAA", "input/manifest.json", b"{}")

    with patch.object(runs_api, "check_run_access", return_value=original), \
         patch.object(runs_api, "check_rate_limit", return_value=None), \
         patch.object(runs_api, "_materialize_input_if_missing", return_value=None), \
         patch.object(runs_api, "get_storage", return_value=storage), \
         patch("app.api.upload.get_storage", return_value=storage), \
         patch("app.api.upload.generate_run_id", side_effect=["AAAAAA", "BBBBBB"]), \
         patch("shutil.copy2", return_value=None), \
         patch("pathlib.Path.read_bytes", return_value=b"PK\x03\x04fake-restarted-docx"), \
         patch("pathlib.Path.exists", return_value=True):
        result = asyncio.run(
            runs_api.restart_run(run_id="ORIGB1", db=db, current_user=user)
        )

    assert result["run_id"] == "BBBBBB"
    assert db.query(Run).filter(Run.id == "BBBBBB").first() is not None
    assert db.query(Run).filter(Run.id == "AAAAAA").first() is None

    # The pre-existing run's archive must be untouched by the restart's retry.
    assert storage.get_file("AAAAAA", "input/AAAAAA.docx") == other_bytes
    assert storage.exists("BBBBBB", "input/BBBBBB.docx")


# --- (e) exhausting every attempt fails the request, no run row ------------

def test_upload_fails_after_exhausting_run_id_attempts(client, db, seed_simple_mode, tmp_path):
    """Mirrors test_upload_atomicity's contract: when the durable archive
    can never be created, the request errors and NO run row is left behind
    -- whether the cause is a real outage or (here) every retry colliding."""
    user = _make_user(db, email="exhausted@example.com")
    _auth(client, user)

    storage = MagicMock()
    storage.put_file_exclusive.side_effect = StorageKeyExists("always collides")

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
    assert db.query(Run).count() == 0
    # One put_file_exclusive call per attempt (each attempt's first put
    # collides, so the manifest put is never reached); bounded by the named
    # retry constant, not unbounded.
    assert storage.put_file_exclusive.call_count == upload_module._RUN_ID_ATTEMPTS


# --- (f) generate_run_id is uniform over its format -------------------------

def test_generate_run_id_format_and_position_entropy():
    """Pins #685's actual defect: the prior generator's 6th character could
    take only ~4 values. 20,000 draws must keep the documented format and
    show broad (not collapsed) spread at every position."""
    ids = [generate_run_id() for _ in range(20_000)]

    pattern = re.compile(r"^[A-Z0-9]{6}$")
    assert all(pattern.match(i) for i in ids), "generate_run_id must keep the ^[A-Z0-9]{6}$ format"

    for pos in range(6):
        distinct = len({run_id[pos] for run_id in ids})
        assert distinct >= 30, (
            f"position {pos} saw only {distinct} distinct symbols in 20,000 draws "
            "(the pre-fix defect collapsed position 5 to ~4)"
        )
