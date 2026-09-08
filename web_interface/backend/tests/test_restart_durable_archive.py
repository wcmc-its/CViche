"""Regression guard: restart durably archives the new run's input to storage.

``POST /run/{id}/restart`` forks a brand-new run from a previous one's uploaded
file. The pod-local copy it writes lives only on the pod that served the restart;
with multiple replicas behind the load balancer a later ``/start`` or ``/retry``
routinely lands on a *different* pod, where the input can only be recovered from
durable storage (S3). If restart does NOT archive the input there, the child run
is unstartable the moment a request hits another pod -- surfacing as the
"Uploaded file not found / Original file no longer available" errors restart is
supposed to recover from.

These tests drive ``restart_run`` directly against the in-memory ``db`` fixture,
stubbing the file-I/O and rate-limit side effects, and assert that the input is
archived to storage under the new run's namespace (and that a storage failure is
fatal, leaving no orphan run -- parity with ``/upload``).
"""

import asyncio
import json
from unittest.mock import patch, MagicMock

import pytest
from fastapi import HTTPException


def _make_original(db):
    from app.models import Run, User

    user = User(email="Archive@Example.com", display_name="Archive", role="user")
    db.add(user)
    db.commit()
    db.refresh(user)

    original = Run(
        id="ORIGAR",
        filename="my cv.docx",
        file_type="docx",
        status="completed",
        user_id=user.id,
        submission_type="standard",
    )
    db.add(original)
    db.commit()
    return user, original


def test_restart_archives_input_to_storage(db, tmp_path):
    from app.api import runs as runs_api
    from app.models import Run

    user, original = _make_original(db)
    storage = MagicMock()
    # Real (temp) UPLOAD_DIR: restart's pod-local write is now an exclusive
    # `open(path, "xb")` rather than a shutil.copy2, so it must not be
    # pointed at the repo's live uploads/ directory.
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    with patch.object(runs_api, "check_run_access", return_value=original), \
         patch.object(runs_api, "check_rate_limit", return_value=None), \
         patch.object(runs_api, "_materialize_input_if_missing", return_value=None), \
         patch.object(runs_api, "get_storage", return_value=storage), \
         patch.object(runs_api, "UPLOAD_DIR", upload_dir), \
         patch("app.api.upload.UPLOAD_DIR", upload_dir), \
         patch("app.api.upload.get_storage", return_value=storage), \
         patch("pathlib.Path.read_bytes", return_value=b"PK\x03\x04fake-docx"), \
         patch("pathlib.Path.exists", return_value=True):
        result = asyncio.run(
            runs_api.restart_run(run_id="ORIGAR", db=db, current_user=user)
        )

    new_run_id = result["run_id"]
    assert new_run_id != "ORIGAR"

    # The input doc and its manifest must be archived under the NEW run's
    # namespace so any pod can re-materialize it.
    put_keys = {call.args[1] for call in storage.put_file_exclusive.call_args_list}
    assert f"input/{new_run_id}.docx" in put_keys, (
        f"restart must archive the input under the new run; got {put_keys}"
    )
    assert "input/manifest.json" in put_keys

    # put_file_exclusive(run_id, key, data): the run_id is always the NEW run.
    for call in storage.put_file_exclusive.call_args_list:
        assert call.args[0] == new_run_id

    # Manifest records provenance so a restarted run is traceable to its source.
    manifest_call = next(
        c for c in storage.put_file_exclusive.call_args_list
        if c.args[1] == "input/manifest.json"
    )
    manifest = json.loads(manifest_call.args[2].decode("utf-8"))
    assert manifest["restarted_from"] == "ORIGAR"
    assert manifest["run_id"] == new_run_id
    assert manifest["original_filename"] == "my cv.docx"

    # The run row was actually created.
    assert db.query(Run).filter(Run.id == new_run_id).first() is not None


def test_restart_aborts_when_archive_fails(db, tmp_path):
    """A failed durable archive is fatal: no run row, 502 surfaced (like /upload)."""
    from app.api import runs as runs_api
    from app.models import Run

    user, original = _make_original(db)
    runs_before = db.query(Run).count()

    storage = MagicMock()
    storage.put_file_exclusive.side_effect = RuntimeError("S3 down")
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    with patch.object(runs_api, "check_run_access", return_value=original), \
         patch.object(runs_api, "check_rate_limit", return_value=None), \
         patch.object(runs_api, "_materialize_input_if_missing", return_value=None), \
         patch.object(runs_api, "get_storage", return_value=storage), \
         patch.object(runs_api, "UPLOAD_DIR", upload_dir), \
         patch("app.api.upload.UPLOAD_DIR", upload_dir), \
         patch("app.api.upload.get_storage", return_value=storage), \
         patch("pathlib.Path.read_bytes", return_value=b"PK\x03\x04fake-docx"), \
         patch("pathlib.Path.unlink", return_value=None), \
         patch("pathlib.Path.exists", return_value=True):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                runs_api.restart_run(run_id="ORIGAR", db=db, current_user=user)
            )

    assert exc.value.status_code == 502
    # No orphan run created when the archive fails.
    assert db.query(Run).count() == runs_before


def test_restart_denied_to_non_owner_creates_nothing(db, tmp_path):
    """The authorization boundary itself, NOT stubbed.

    Every other restart test patches check_run_access to return the original
    run, so none of them exercises the gate -- the gate's own unit tests live
    in test_service_layer.py (test_raises_403_when_not_owner,
    test_denies_unowned_run_to_non_admin), but nothing proved restart_run
    actually calls it before archiving or creating a child run. Here a second,
    non-admin user restarts someone else's run against the real gate.
    """
    from app.api import runs as runs_api
    from app.models import Run, User

    user, original = _make_original(db)
    other = User(email="intruder@example.com", display_name="Intruder", role="user")
    db.add(other)
    db.commit()
    db.refresh(other)

    storage = MagicMock()
    runs_before = db.query(Run).count()
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()

    with patch.object(runs_api, "get_storage", return_value=storage), \
         patch.object(runs_api, "UPLOAD_DIR", upload_dir), \
         patch("app.api.upload.UPLOAD_DIR", upload_dir), \
         patch("app.api.upload.get_storage", return_value=storage):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(
                runs_api.restart_run(run_id="ORIGAR", db=db, current_user=other)
            )

    assert exc.value.status_code == 403
    # No archive written and no child run created for the non-owner.
    storage.put_file_exclusive.assert_not_called()
    storage.put_global.assert_not_called()
    assert db.query(Run).count() == runs_before
    assert list(upload_dir.iterdir()) == []
