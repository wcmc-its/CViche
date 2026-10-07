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
import contextlib
import hashlib
import json
import logging
import os
import re
import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import object_session

from app.api import upload as upload_module
from app.api.upload import generate_run_id
from app.models import Run, Step, User
from app.pipeline.step_registry import STEP_REGISTRY
from app.storage.base import StorageError, StorageKeyExists, StorageKeyNotFound
from app.storage.local_storage import LocalRunStorage

# Threads racing one exclusive create. More than a couple, so a non-atomic
# implementation loses the race reliably rather than occasionally.
_RACING_WRITERS = 8


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
    from app.auth import COOKIE_NAME, create_session_cookie
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, object_session(user)))


def _bypass_file_validation(upload_dir):
    return [
        patch("app.services.run_creation.UPLOAD_DIR", upload_dir),
        patch("app.services.run_creation._validate_docx_magic", return_value=True),
        patch("app.services.run_creation._extract_text", return_value=None),
        patch("app.services.run_creation.detect_wcm_template", return_value=(False, None)),
    ]


def _post_dummy_upload(client):
    return client.post(
        "/api/upload",
        files={"file": ("cv.docx", b"PK\x03\x04dummy-docx-bytes", "application/octet-stream")}, data={"submission_type": "own_cv"},
    )


# --- (a) LocalRunStorage.put_file_exclusive itself --------------------------

def test_local_put_file_exclusive_raises_on_second_write(tmp_path):
    storage = LocalRunStorage(base_dir=str(tmp_path))
    storage.put_file_exclusive("R1", "input/cv.docx", b"first-bytes")

    with pytest.raises(StorageKeyExists):
        storage.put_file_exclusive("R1", "input/cv.docx", b"second-bytes")

    # The first write must survive the failed second one untouched.
    assert storage.get_file("R1", "input/cv.docx") == b"first-bytes"


# --- (b) put_file_exclusive's concurrency contract --------------------------

def test_local_put_file_exclusive_is_atomic_under_concurrency(tmp_path):
    """Exactly one of N simultaneous writers to the same key may win.

    The sequential test above only proves the second write fails after the
    first returned; it would also pass for an exists()-then-put_file
    implementation, which is the TOCTOU race this method exists to close.
    Here every writer is released from a barrier at once, so a check-then-act
    implementation lets two of them observe "absent" and both write.

    This is the LOCAL half of the contract. The S3 half is
    test_s3_storage.py::test_put_file_exclusive_sends_if_none_match_and_maps_412,
    which asserts (via the Stubber's expected_params) that the backend
    actually sends IfNoneMatch="*" -- S3 resolves the race server-side, so
    exclusivity there is a property of the request, not of this process, and
    a client-side thread race cannot test it.
    """
    storage = LocalRunStorage(base_dir=str(tmp_path))
    barrier = threading.Barrier(_RACING_WRITERS)
    lock = threading.Lock()
    winners: list[bytes] = []
    losers = 0

    def race(n: int) -> None:
        nonlocal losers
        payload = f"writer-{n}".encode()
        barrier.wait()
        try:
            storage.put_file_exclusive("RACE01", "input/cv.docx", payload)
        except StorageKeyExists:
            # Expected for every writer but one: the key was already created.
            with lock:
                losers += 1
            return
        with lock:
            winners.append(payload)

    threads = [threading.Thread(target=race, args=(n,)) for n in range(_RACING_WRITERS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(winners) == 1, f"exactly one writer may win, got {len(winners)}"
    assert losers == _RACING_WRITERS - 1
    # And the stored bytes are the winner's, whole -- not a mix of two writers.
    assert storage.get_file("RACE01", "input/cv.docx") == winners[0]


# --- (a1) get_file's shared exception contract (#790) -----------------------

def test_local_get_file_missing_key_raises_storagekeynotfound_not_bare_filenotfound(tmp_path):
    """LocalRunStorage is the other half of the StorageKeyNotFound contract
    that base.py documents (the S3 half is
    test_s3_storage.py::test_get_file_nosuchkey_raises_storagekeynotfound_not_bare_filenotfound).
    StorageKeyNotFound subclasses FileNotFoundError, so a regression to a
    bare FileNotFoundError here still passes any test that only checks
    pytest.raises(FileNotFoundError) -- this one pins the specific type."""
    storage = LocalRunStorage(base_dir=str(tmp_path))

    with pytest.raises(StorageKeyNotFound):
        storage.get_file("R1", "input/missing.txt")


def test_storage_exceptions_are_storageerror_subclasses():
    """base.py's exception contract (#790 acceptance criterion 3) is that a
    caller can catch the single base StorageError instead of enumerating
    StorageKeyExists and StorageKeyNotFound separately -- pinned directly
    against the class hierarchy, not inferred from a raise site."""
    assert issubclass(StorageKeyExists, StorageError)
    assert issubclass(StorageKeyNotFound, StorageError)
    assert issubclass(StorageKeyNotFound, FileNotFoundError)


# --- (a2) put_file / put_global atomic-write contract (#787) ---------------
#
# put_file and put_global used to end in path.write_bytes(data): a process
# interrupted mid-write left a truncated file readable under the final key,
# which a later read consumed as if it were complete. The fix writes to a
# temp file in the same directory and os.replace()s it into place.
# put_file_exclusive is untouched -- its exclusive-create semantics
# (test_local_put_file_exclusive_raises_on_second_write above) are already
# atomic and out of scope here.

def _boom(*_args, **_kwargs):
    raise OSError("simulated crash before rename")


def _boom_keyboard_interrupt(*_args, **_kwargs):
    raise KeyboardInterrupt("simulated interrupt before rename")


def test_local_put_file_interrupted_replace_leaves_previous_content_intact(tmp_path, monkeypatch):
    """A crash between the temp-file write and the rename must not corrupt
    the previously-stored artifact, and must not leave the temp file
    behind."""
    storage = LocalRunStorage(base_dir=str(tmp_path))
    storage.put_file("R1", "data.json", b"original-complete-bytes")

    monkeypatch.setattr("app.storage.local_storage.os.replace", _boom)
    with pytest.raises(OSError):
        storage.put_file("R1", "data.json", b"new-bytes-that-must-never-land")
    monkeypatch.undo()

    assert storage.get_file("R1", "data.json") == b"original-complete-bytes"
    leftover = [n for n in os.listdir(tmp_path / "R1") if n != "data.json"]
    assert leftover == [], f"temp file leaked: {leftover}"


def test_local_put_file_interrupted_write_leaves_no_file_at_new_key(tmp_path, monkeypatch):
    """Same interruption, but the key never existed before: the destination
    must stay absent, not hold a partial write."""
    storage = LocalRunStorage(base_dir=str(tmp_path))

    monkeypatch.setattr("app.storage.local_storage.os.replace", _boom)
    with pytest.raises(OSError):
        storage.put_file("R2", "data.json", b"partial-bytes")
    monkeypatch.undo()

    assert storage.exists("R2", "data.json") is False
    run_dir = tmp_path / "R2"
    leftover = os.listdir(run_dir) if run_dir.exists() else []
    assert leftover == [], f"temp file leaked: {leftover}"


def test_local_put_file_interrupted_by_keyboardinterrupt_still_cleans_up(tmp_path, monkeypatch):
    """_atomic_write's cleanup catches BaseException, not just Exception, so
    a KeyboardInterrupt (or SystemExit) during the write still removes the
    temp file instead of leaking it. The interrupted-write tests above only
    exercise OSError, which `except Exception` alone would already catch --
    this pins the broader clause specifically, so narrowing it to Exception
    would fail this test rather than pass silently."""
    storage = LocalRunStorage(base_dir=str(tmp_path))

    monkeypatch.setattr("app.storage.local_storage.os.replace", _boom_keyboard_interrupt)
    with pytest.raises(KeyboardInterrupt):
        storage.put_file("R6", "data.json", b"partial-bytes")
    monkeypatch.undo()

    assert storage.exists("R6", "data.json") is False
    run_dir = tmp_path / "R6"
    leftover = os.listdir(run_dir) if run_dir.exists() else []
    assert leftover == [], f"temp file leaked: {leftover}"


def test_local_put_file_failed_cleanup_keeps_the_original_error(tmp_path, monkeypatch, caplog):
    """If removing the temp file also fails, the write's own error still
    propagates and the cleanup failure is logged, not raised in its place."""
    storage = LocalRunStorage(base_dir=str(tmp_path))

    def _unlink_denied(*_args, **_kwargs):
        raise PermissionError("simulated cleanup failure")

    monkeypatch.setattr("app.storage.local_storage.os.replace", _boom)
    monkeypatch.setattr("app.storage.local_storage.os.unlink", _unlink_denied)
    with caplog.at_level("WARNING", logger="app.storage.local_storage"):
        with pytest.raises(OSError, match="simulated crash before rename"):
            storage.put_file("R7", "data.json", b"partial-bytes")

    assert any("failed to clean up temp file" in r.getMessage() for r in caplog.records)


def test_local_put_file_restores_standard_readable_mode(tmp_path):
    """mkstemp() creates the temp file at 0o600 (owner-only); os.replace()
    preserves the SOURCE's mode, not the destination's, so without an
    explicit chmod the final file would regress from the pre-#787
    path.write_bytes() default (0o644 under the standard umask) to
    owner-only."""
    storage = LocalRunStorage(base_dir=str(tmp_path))
    storage.put_file("R7", "data.json", b"bytes")
    mode = (tmp_path / "R7" / "data.json").stat().st_mode & 0o777
    assert mode == 0o644


def test_local_put_global_interrupted_replace_leaves_no_temp_file(tmp_path, monkeypatch):
    """put_global goes through the same _atomic_write helper as put_file;
    pin it independently since it writes outside the run namespace."""
    storage = LocalRunStorage(base_dir=str(tmp_path))

    monkeypatch.setattr("app.storage.local_storage.os.replace", _boom)
    with pytest.raises(OSError):
        storage.put_global("by-submitter/e@x.edu/R1/manifest.json", b"partial")
    monkeypatch.undo()

    target_dir = tmp_path / "by-submitter" / "e@x.edu" / "R1"
    leftover = os.listdir(target_dir) if target_dir.exists() else []
    assert leftover == [], f"temp file leaked: {leftover}"


def test_local_put_file_leaves_no_temp_file_on_success(tmp_path):
    """The success path cleans up after itself: only the final key exists,
    never a stray temp file beside it."""
    storage = LocalRunStorage(base_dir=str(tmp_path))
    storage.put_file("R3", "data.json", b"bytes")
    assert os.listdir(tmp_path / "R3") == ["data.json"]


def test_local_put_file_temp_file_is_created_in_destination_directory(tmp_path, monkeypatch):
    """The temp file mkstemp() creates must live in path.parent -- the
    destination's own directory -- not the platform default temp dir.
    os.replace() is only an atomic same-filesystem rename when both sides
    share a filesystem; a store mounted on a different filesystem than the
    default temp dir would turn every put_file into a cross-device (EXDEV)
    error if this ever regressed to mkstemp(dir=None) (#787)."""
    storage = LocalRunStorage(base_dir=str(tmp_path))
    real_replace = os.replace
    seen_src_parents = []

    def _spy_replace(src, dst):
        seen_src_parents.append(Path(src).parent)
        return real_replace(src, dst)

    monkeypatch.setattr("app.storage.local_storage.os.replace", _spy_replace)
    storage.put_file("R4", "data.json", b"bytes")

    assert seen_src_parents == [tmp_path / "R4"]


# --- (b2) key/run-id validation is a storage invariant ----------------------

@pytest.mark.parametrize("key", [
    "../escaped.txt",
    "input/../../escaped.txt",
    "/etc/passwd",
    "./cv.docx",
])
def test_local_storage_rejects_escaping_key(tmp_path, key):
    """Without this guard put_file_exclusive("R1", "../../escaped.txt", ...)
    wrote outside the storage base (measured), and get_file read from there."""
    storage = LocalRunStorage(base_dir=str(tmp_path / "store"))
    for call in (
        lambda: storage.put_file_exclusive("R1", key, b"pwn"),
        lambda: storage.put_file("R1", key, b"pwn"),
        lambda: storage.get_file("R1", key),
        lambda: storage.exists("R1", key),
    ):
        with pytest.raises(ValueError):
            call()
    assert list(tmp_path.glob("escaped.txt")) == []


@pytest.mark.parametrize("run_id", ["../../etc", "R1/../R2", "a b", "", "x" * 65])
def test_local_storage_rejects_bad_run_id(tmp_path, run_id):
    storage = LocalRunStorage(base_dir=str(tmp_path / "store"))
    with pytest.raises(ValueError):
        storage.put_file_exclusive(run_id, "input/cv.docx", b"pwn")
    with pytest.raises(ValueError):
        storage.delete_run(run_id)


def test_local_storage_rejects_symlink_escape(tmp_path):
    """String rules alone are not enough: a symlink inside the store
    redirects a perfectly well-formed key outside it, so containment is
    checked against the RESOLVED path."""
    store = tmp_path / "store"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_bytes(b"not-yours")

    storage = LocalRunStorage(base_dir=str(store))
    (store / "R1").mkdir(parents=True)
    (store / "R1" / "input").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError):
        storage.put_file_exclusive("R1", "input/cv.docx", b"pwn")
    with pytest.raises(ValueError):
        storage.get_file("R1", "input/secret.txt")
    assert not (outside / "cv.docx").exists()


def test_local_delete_rejects_traversal_out_of_the_store(tmp_path):
    """delete_global_prefix / delete_run recursively remove a tree, so an
    unvalidated prefix is a data-loss bug, not a routing bug. The pre-existing
    non-empty check does not stop "../sibling"."""
    store = tmp_path / "store"
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "keep.txt").write_bytes(b"keep")

    storage = LocalRunStorage(base_dir=str(store))
    with pytest.raises(ValueError):
        storage.delete_global_prefix("../sibling/")
    with pytest.raises(ValueError):
        storage.delete_run("../sibling")
    assert (sibling / "keep.txt").read_bytes() == b"keep"


def test_local_storage_still_accepts_the_keys_live_callers_pass(tmp_path):
    """Regression guard on the validator itself: real callers pass an empty
    key and trailing-slash prefixes (list_files(run_id, "outputs/") in
    steps.py and quality_score_service.py), and legacy run ids can contain
    "-"/"_" from the pre-#685 generator. None of those may be rejected."""
    storage = LocalRunStorage(base_dir=str(tmp_path / "store"))
    for run_id in ("AAAAAA", "A-B_c1", "run1"):
        storage.put_file(run_id, "outputs/report.json", b"{}")
        assert storage.list_files(run_id, "outputs/") == ["outputs/report.json"]
        assert storage.list_files(run_id) == ["outputs/report.json"]
    storage.put_global("by-submitter/jdoe@example.edu/AAAAAA/manifest.json", b"{}")


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
    # Pre-seed the POD-LOCAL path too, with sentinel bytes distinct from
    # anything this request writes. Round-2 verifier finding: the pod-local
    # `open(path, "xb")` exclusive create (separate from the durable-storage
    # one above) had no test -- flipping it to "wb" killed nothing, because
    # nothing pre-created a local file for the first attempt to collide
    # against. With this sentinel present, a real "xb" open raises
    # FileExistsError on the first ("AAAAAA") attempt and the loop moves on
    # to "BBBBBB" without touching the sentinel; a "wb" mutant instead
    # silently overwrites it with this request's own upload bytes (and the
    # storage collision on "AAAAAA"'s pre-seeded manifest then triggers a
    # cleanup unlink of that clobbered file) -- either way the sentinel is
    # gone, which the assertions below catch.
    local_sentinel_path = upload_dir / "AAAAAA.docx"
    local_sentinel_bytes = b"PK\x03\x04 pod-local-sentinel-untouched"
    local_sentinel_path.write_bytes(local_sentinel_bytes)

    patches = _bypass_file_validation(upload_dir)
    patches.append(patch("app.services.run_creation.get_storage", return_value=storage))
    patches.append(patch("app.services.run_creation.generate_run_id", side_effect=["AAAAAA", "BBBBBB"]))
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

    # The pod-local sentinel survives byte-for-byte, and was never unlinked
    # (a "wb" mutant would overwrite it; a stray cleanup call would delete it
    # -- create_run_archive must do neither to a file it didn't create).
    assert local_sentinel_path.exists(), "pod-local collision must not delete the other upload's file"
    assert local_sentinel_path.read_bytes() == local_sentinel_bytes


# --- (c2) manifest-first ordering blocks a partial write on an extension
# mismatch (PROBE3, round-2 verifier finding) ------------------------------

def test_upload_manifest_first_blocks_partial_write_on_extension_mismatch(client, db, seed_simple_mode, tmp_path):
    """A collision on a run id already archived under a DIFFERENT extension
    must be caught before any CV byte lands under that id's namespace.

    Pre-seed AAAAAA with a .pdf archive plus its manifest.json -- so this
    .docx upload's own content key (input/AAAAAA.docx) is NOT already
    occupied and only the manifest write can detect the collision. Before
    the round-2 fix, create_run_archive wrote the content key first, so a
    same-id/different-extension collision would write input/AAAAAA.docx
    into the other run's namespace before the manifest write ever collided.
    Writing manifest.json first closes that: the collision is caught there,
    before the .docx write is attempted at all.
    """
    user = _make_user(db, email="probe3@example.com")
    _auth(client, user)

    storage = LocalRunStorage(base_dir=str(tmp_path / "storage"))
    other_pdf_bytes = b"%PDF-1.4 someone-elses-pdf-cv"
    storage.put_file("AAAAAA", "input/AAAAAA.pdf", other_pdf_bytes)
    storage.put_file("AAAAAA", "input/manifest.json", b'{"run_id": "AAAAAA", "file_type": "pdf"}')

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    patches = _bypass_file_validation(upload_dir)
    patches.append(patch("app.services.run_creation.get_storage", return_value=storage))
    patches.append(patch("app.services.run_creation.generate_run_id", side_effect=["AAAAAA", "BBBBBB"]))
    for p in patches:
        p.start()
    try:
        resp = _post_dummy_upload(client)
    finally:
        for p in patches:
            p.stop()

    assert resp.status_code == 200, resp.text
    assert resp.json()["run_id"] == "BBBBBB"

    # The whole point: the AAAAAA namespace gains NO .docx.
    assert storage.exists("AAAAAA", "input/AAAAAA.docx") is False
    assert storage.list_files("AAAAAA", prefix="input/") == [
        "input/AAAAAA.pdf", "input/manifest.json",
    ]
    assert storage.get_file("AAAAAA", "input/AAAAAA.pdf") == other_pdf_bytes

    # The fresh id got the real archive.
    assert storage.exists("BBBBBB", "input/BBBBBB.docx")
    assert db.query(Run).filter(Run.id == "BBBBBB").first() is not None
    assert db.query(Run).filter(Run.id == "AAAAAA").first() is None


# --- (d) restart_run regenerates the id on the same real collision ---------

def test_restart_regenerates_id_on_real_storage_collision(db, tmp_path):
    """Restart's collision path, with NOTHING about the filesystem mocked.

    The earlier version of this test patched shutil.copy2 to a no-op and
    pathlib.Path.exists to True, which mocked the pod-local layer away
    entirely -- and hid a real defect: restart's pod-local write was a
    non-exclusive shutil.copy2, so a colliding id OVERWROTE the other run's
    pod-local input and create_run_archive's StorageKeyExists cleanup then
    UNLINKED it. Here UPLOAD_DIR is a real temp dir holding a real sentinel,
    which is the assertion that catches that (the /upload test above makes
    the same one).
    """
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

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    original_bytes = b"PK\x03\x04fake-restarted-docx"
    (upload_dir / "ORIGB1.docx").write_bytes(original_bytes)
    # The colliding id's pod-local file, belonging to another run on this pod.
    local_sentinel_path = upload_dir / "AAAAAA.docx"
    local_sentinel_bytes = b"PK\x03\x04 pod-local-sentinel-untouched"
    local_sentinel_path.write_bytes(local_sentinel_bytes)

    with patch.object(runs_api, "check_run_access", return_value=original), \
         patch.object(runs_api, "check_rate_limit", return_value=None), \
         patch.object(runs_api, "_materialize_input_if_missing", return_value=None), \
         patch.object(runs_api, "get_storage", return_value=storage), \
         patch.object(runs_api, "UPLOAD_DIR", upload_dir), \
         patch("app.services.run_creation.UPLOAD_DIR", upload_dir), \
         patch("app.services.run_creation.get_storage", return_value=storage), \
         patch("app.services.run_creation.generate_run_id", side_effect=["AAAAAA", "BBBBBB"]):
        result = asyncio.run(
            runs_api.restart_run(run_id="ORIGB1", db=db, current_user=user)
        )

    assert result["run_id"] == "BBBBBB"
    assert db.query(Run).filter(Run.id == "BBBBBB").first() is not None
    assert db.query(Run).filter(Run.id == "AAAAAA").first() is None

    # The pre-existing run's archive must be untouched by the restart's retry.
    assert storage.get_file("AAAAAA", "input/AAAAAA.docx") == other_bytes

    # The child run's archive is read back, not merely asserted to exist:
    # the bytes AND the manifest's provenance must survive the retry.
    assert storage.get_file("BBBBBB", "input/BBBBBB.docx") == original_bytes
    child_manifest = json.loads(storage.get_file("BBBBBB", "input/manifest.json"))
    assert child_manifest["run_id"] == "BBBBBB"
    assert child_manifest["restarted_from"] == "ORIGB1"
    assert child_manifest["stored_as"] == "BBBBBB.docx"

    # The pod-local sentinel survives byte-for-byte: restart's pod-local write
    # is an exclusive create, so the colliding attempt neither overwrote it nor
    # unlinked it on the way out.
    assert local_sentinel_path.exists(), "restart collision must not delete the other run's local file"
    assert local_sentinel_path.read_bytes() == local_sentinel_bytes
    assert (upload_dir / "BBBBBB.docx").read_bytes() == original_bytes


# --- (e) exhausting every attempt fails the request, no run row ------------

def test_upload_fails_after_exhausting_run_id_attempts(client, db, seed_simple_mode, tmp_path, caplog):
    """PR #779 review thread web_interface/backend/tests/test_upload_run_id_collision.py item 23

    Mirrors test_upload_atomicity's contract: when the durable archive can
    never be created, the request errors and NO run row is left behind --
    whether the cause is a real outage or (here) every retry colliding.

    Rewritten against the real LocalRunStorage, which is what this module's
    docstring promises for every collision test; the earlier version used a
    MagicMock that collided on every call and asserted only a call count, so
    it could not see whether the five aborted attempts left pod-local files
    behind, whether each attempt actually drew a FRESH id, or whether the
    colliding runs' archives were touched. Every one of the _RUN_ID_ATTEMPTS
    draws is pre-seeded with another run's manifest, so all five collide.
    """
    user = _make_user(db, email="exhausted@example.com")
    _auth(client, user)

    storage = LocalRunStorage(base_dir=str(tmp_path / "storage"))
    colliding_ids = ["AAAAAA", "BBBBBB", "CCCCCC", "DDDDDD", "EEEEEE"]
    assert len(colliding_ids) == upload_module._RUN_ID_ATTEMPTS
    seeds = {}
    for rid in colliding_ids:
        seeds[rid] = f'{{"run_id": "{rid}", "owner": "someone-else"}}'.encode()
        storage.put_file(rid, "input/manifest.json", seeds[rid])

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    # Exactly _RUN_ID_ATTEMPTS ids: a sixth draw raises StopIteration (still a
    # 502, but call_count below then reads 6 and fails), so the list doubles
    # as the bound check.
    draw = MagicMock(side_effect=list(colliding_ids))
    patches = _bypass_file_validation(upload_dir)
    patches.append(patch("app.services.run_creation.get_storage", return_value=storage))
    patches.append(patch("app.services.run_creation.generate_run_id", draw))
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
    # #797: exhaustion is not silent -- the caller logs it at ERROR.
    assert any(
        r.levelno == logging.ERROR and "could not allocate a collision-free run id" in r.getMessage()
        for r in caplog.records
    )
    # One fresh id per attempt, bounded by the named retry constant.
    assert draw.call_count == upload_module._RUN_ID_ATTEMPTS
    # Every colliding run's archive is byte-identical to its seed and gained
    # no content key (the manifest put collides first, so the content put is
    # never reached).
    for rid in colliding_ids:
        assert storage.get_file(rid, "input/manifest.json") == seeds[rid]
        assert storage.list_files(rid) == ["input/manifest.json"]
    # Each attempt's pod-local file was created exclusively and then unlinked
    # on the collision, so five aborted attempts leave nothing on disk.
    assert list(upload_dir.iterdir()) == []


# --- (f) generate_run_id is uniform over its format -------------------------

def test_generate_run_id_format_and_position_entropy():
    """Pins #685's actual defect: the prior generator's 6th character could
    take only ~4 values. 20,000 draws must keep the documented format and
    show broad (not collapsed) spread at every position."""
    # Lazy: unified_pipeline is on sys.path only once conftest has imported the
    # orchestrator, which happens in an autouse fixture, after collection.
    from unified_pipeline.core.run_id import is_run_id

    ids = [generate_run_id() for _ in range(20_000)]

    pattern = re.compile(r"^[A-Z]{6}$")
    assert all(pattern.match(i) for i in ids), "generate_run_id must keep the letters-only ^[A-Z]{6}$ format"
    # #457: stage 4 and stage 6 refuse to read a run id as a person's name, by
    # this shape. #1192 narrowed the alphabet and an isalpha() guard went inert
    # unnoticed; a generator change that escapes the shape now fails here.
    assert all(is_run_id(i) for i in ids), "generate_run_id emitted an id unified_pipeline.core.run_id does not recognise"

    for pos in range(6):
        distinct = len({run_id[pos] for run_id in ids})
        assert distinct == 26, (
            f"position {pos} saw only {distinct} distinct symbols in 20,000 draws "
            "(the pre-fix defect collapsed position 5 to ~4)"
        )


# --- (g) PR #779 review asks: the collision loop's other branches ----------

def test_upload_collision_on_content_after_manifest_retries(client, db, seed_simple_mode, tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 16

    A StorageKeyExists from the CONTENT put (manifest already landed) is a
    collision too: the id is regenerated and the request succeeds under the
    fresh id. create_run_archive's docstring says this codebase cannot
    produce that state (nothing writes a content key without its manifest),
    but the branch is reachable with a pre-seeded content object lacking a
    manifest, and neither real-storage collision test above drives it (both
    pre-seed the manifest, so the manifest put collides first). Pins the
    documented residue as well: the orphaned manifest under the abandoned id
    is left in place, and carries only this requester's own data.
    """
    user = _make_user(db, email="content-collision@example.com")
    _auth(client, user)

    storage = LocalRunStorage(base_dir=str(tmp_path / "storage"))
    other_bytes = b"PK\x03\x04 someone-elses-cv-bytes"
    # Content key only -- NO manifest, so the manifest put succeeds and only
    # the content put can detect the collision.
    storage.put_file("AAAAAA", "input/AAAAAA.docx", other_bytes)

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    patches = _bypass_file_validation(upload_dir)
    patches.append(patch("app.services.run_creation.get_storage", return_value=storage))
    patches.append(patch("app.services.run_creation.generate_run_id", side_effect=["AAAAAA", "BBBBBB"]))
    for p in patches:
        p.start()
    try:
        resp = _post_dummy_upload(client)
    finally:
        for p in patches:
            p.stop()

    assert resp.status_code == 200, resp.text
    assert resp.json()["run_id"] == "BBBBBB"

    # The pre-existing content object was never overwritten.
    assert storage.get_file("AAAAAA", "input/AAAAAA.docx") == other_bytes
    # The documented residue: this request's manifest stays orphaned under
    # the abandoned id (no run row), naming only this same requester.
    assert storage.exists("AAAAAA", "input/manifest.json") is True
    orphan = json.loads(storage.get_file("AAAAAA", "input/manifest.json"))
    assert orphan["run_id"] == "AAAAAA"
    assert orphan["user_email"] == user.email
    assert storage.list_files("AAAAAA") == ["input/AAAAAA.docx", "input/manifest.json"]

    # The fresh id got the whole archive, and only it got a run row.
    assert storage.list_files("BBBBBB") == ["input/BBBBBB.docx", "input/manifest.json"]
    assert storage.get_file("BBBBBB", "input/BBBBBB.docx") == b"PK\x03\x04dummy-docx-bytes"
    assert [r.id for r in db.query(Run).all()] == ["BBBBBB"]
    # The colliding attempt's pod-local file was unlinked; only the winner's remains.
    assert [p.name for p in upload_dir.iterdir()] == ["BBBBBB.docx"]


def test_upload_real_storage_fault_is_not_retried(client, db, seed_simple_mode, tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_run_id_collision.py item 15

    A non-collision storage failure is fatal on the FIRST attempt: no fresh
    id is drawn, no second put is tried, the attempt's pod-local file is
    removed, and no run row or by-submitter index is written. The existing
    fatal-path test (test_upload_atomicity.py) only asserts the put was
    attempted, which one call or five satisfy alike; here both the put count
    and the id-draw count are pinned to exactly one, in contrast with the
    exhaustion test's _RUN_ID_ATTEMPTS.
    """
    user = _make_user(db, email="fault@example.com")
    _auth(client, user)

    storage = MagicMock()
    storage.put_file_exclusive.side_effect = RuntimeError("S3 down")
    draw = MagicMock(return_value="AAAAAA")

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    patches = _bypass_file_validation(upload_dir)
    patches.append(patch("app.services.run_creation.get_storage", return_value=storage))
    patches.append(patch("app.services.run_creation.generate_run_id", draw))
    for p in patches:
        p.start()
    try:
        resp = _post_dummy_upload(client)
    finally:
        for p in patches:
            p.stop()

    assert resp.status_code == 502, resp.text
    assert resp.json()["detail"]["error"] == "storage_unavailable"
    # Exactly one attempt: one id drawn, one (failing) put, no retry loop.
    assert draw.call_count == 1
    assert storage.put_file_exclusive.call_count == 1
    assert storage.put_file_exclusive.call_args.args[:2] == ("AAAAAA", "input/manifest.json")
    storage.put_global.assert_not_called()
    assert db.query(Run).count() == 0
    # The single attempt's pod-local file was cleaned up on the fault.
    assert list(upload_dir.iterdir()) == []


def test_create_run_archive_concurrent_same_first_draw(tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_run_id_collision.py item 18

    N uploads whose FIRST id draw is the same value, released together: the
    real id-regeneration loop over the real LocalRunStorage and a real
    UPLOAD_DIR must let exactly one keep that id, give every other caller a
    distinct fresh id, and let all N succeed with their own bytes intact.
    test_local_put_file_exclusive_is_atomic_under_concurrency above covers
    only the storage primitive on one fixed key; the two /upload collision
    tests are sequential (a pre-seeded archive). This drives create_run_archive
    directly because the TestClient + shared-SQLite-session harness cannot
    safely serve N requests from N threads.

    Each writer gets its own pod-local directory (a thread-local stands in for
    a pod's disk), so the pod-local open("xb") cannot settle the race by
    itself; only the durable storage's put_file_exclusive can, which is the
    cross-pod collision #685 is about. The verifier's first draft of this test
    shared one UPLOAD_DIR and never reached storage.
    """
    storage = LocalRunStorage(base_dir=str(tmp_path / "storage"))
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    pod_of = threading.local()  # this thread's pod-local disk

    def unlink_on_this_pod(path) -> None:
        (pod_of.dir / path.name).unlink(missing_ok=True)

    lock = threading.Lock()
    first_drawn: set[int] = set()
    unique_draws = 0

    def draw() -> str:
        # Every thread's first draw is the same id; later draws are unique.
        nonlocal unique_draws
        with lock:
            me = threading.get_ident()
            if me not in first_drawn:
                first_drawn.add(me)
                return "SAME01"
            unique_draws += 1
            return f"UNIQ{unique_draws:02d}"

    barrier = threading.Barrier(_RACING_WRITERS)
    results: dict[int, tuple] = {}
    errors: dict[int, str] = {}

    def race(n: int) -> None:
        payload = f"writer-{n}-payload".encode()
        pod_of.dir = upload_dir / f"pod{n}"
        pod_of.dir.mkdir()

        def build_manifest(run_id: str, stored_name: str) -> bytes:
            return json.dumps({"run_id": run_id, "stored_as": stored_name, "writer": n}).encode()

        def write_local(path) -> None:
            with open(pod_of.dir / path.name, "xb") as f:
                f.write(payload)

        barrier.wait()
        try:
            run_id, stored_name, file_path, _ = upload_module.create_run_archive(
                payload, ".docx", build_manifest, write_local,
            )
        except Exception as e:  # recorded, asserted empty below
            with lock:
                errors[n] = repr(e)
            return
        with lock:
            results[n] = (run_id, stored_name, file_path, payload)

    with patch("app.services.run_creation.UPLOAD_DIR", upload_dir), \
         patch("app.services.run_creation.get_storage", return_value=storage), \
         patch("app.services.run_creation.generate_run_id", new=draw), \
         patch("app.services.run_creation._unlink_best_effort", new=unlink_on_this_pod):
        threads = [threading.Thread(target=race, args=(n,)) for n in range(_RACING_WRITERS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

    assert errors == {}, f"no writer may fail on a mere collision: {errors}"
    assert len(results) == _RACING_WRITERS
    ids = [r[0] for r in results.values()]
    assert len(set(ids)) == _RACING_WRITERS, f"ids must be distinct, got {ids}"
    assert ids.count("SAME01") == 1, f"exactly one writer keeps the shared draw, got {ids}"

    winner = next(n for n, r in results.items() if r[0] == "SAME01")
    assert storage.get_file("SAME01", "input/SAME01.docx") == results[winner][3]

    for n, (run_id, stored_name, file_path, payload) in results.items():
        assert stored_name == f"{run_id}.docx"
        assert file_path == upload_dir / stored_name
        # Durable archive: both keys, this writer's whole payload, its own manifest.
        assert storage.list_files(run_id) == [f"input/{stored_name}", "input/manifest.json"]
        assert storage.get_file(run_id, f"input/{stored_name}") == payload
        assert json.loads(storage.get_file(run_id, "input/manifest.json"))["writer"] == n
        # This pod's disk holds exactly its winning copy: the SAME01 loser file
        # was unlinked before the retry, so no residue.
        pod = upload_dir / f"pod{n}"
        assert [p.name for p in pod.iterdir()] == [stored_name]
        assert (pod / stored_name).read_bytes() == payload
    # Every pod-local SAME01 copy but the winner's is gone.
    assert sum((upload_dir / f"pod{n}" / "SAME01.docx").exists() for n in range(_RACING_WRITERS)) == 1


# --- (h) PR #779 review asks: restart_run ----------------------------------

# Every Run column restart reads or could plausibly touch; snapshotted before a
# restart and compared after (item 21), so a write to the original is caught
# whichever column it lands on.
_ORIGINAL_RUN_COLS = (
    "status", "filename", "file_type", "user_id", "submission_type",
    "started_at", "completed_at", "total_cost", "total_tokens", "error_message",
    "show_track_changes", "show_pipeline_comments", "strip_template_instructions",
)


def _snapshot_run(run) -> dict:
    return {col: getattr(run, col) for col in _ORIGINAL_RUN_COLS}


def _make_original(db, user, run_id, **overrides):
    """A completed original run owned by `user`, with every render flag set
    away from its column default (1/0/1) so a fall-back to defaults on the
    child is caught."""
    fields = dict(
        id=run_id,
        filename="my cv.docx",
        file_type="docx",
        status="completed",
        user_id=user.id,
        submission_type="standard",
        show_track_changes=0,
        show_pipeline_comments=1,
        strip_template_instructions=0,
    )
    fields.update(overrides)
    original = Run(**fields)
    db.add(original)
    db.commit()
    db.refresh(original)
    return original


@contextlib.contextmanager
def _restart_env(original, storage, upload_dir, materialize=True, extra=()):
    """The restart_run harness shared by (h): the access gate and rate limit
    stubbed, storage and UPLOAD_DIR redirected on every module that reads its
    own copy (runs.py and upload.py each import the name into their own
    namespace; `_materialize_input_if_missing`/`UPLOAD_DIR` themselves live in
    app.services.run_service (#701) -- see runs.py's own get_storage patch
    below, which covers restart_run's archive-the-new-run's-input call, a
    separate get_storage() reached directly from runs.py, not through
    _materialize_input_if_missing). `_materialize_input_if_missing` stays REAL
    unless materialize=False -- it is a no-op when the local file exists.
    """
    from app.api import runs as runs_api

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(runs_api, "check_run_access", return_value=original))
        stack.enter_context(patch.object(runs_api, "check_rate_limit", return_value=None))
        if not materialize:
            stack.enter_context(patch.object(runs_api, "_materialize_input_if_missing", return_value=None))
        stack.enter_context(patch.object(runs_api, "get_storage", return_value=storage))
        stack.enter_context(patch("app.services.run_service.get_storage", return_value=storage))
        stack.enter_context(patch.object(runs_api, "UPLOAD_DIR", upload_dir))
        stack.enter_context(patch("app.services.run_creation.UPLOAD_DIR", upload_dir))
        stack.enter_context(patch("app.services.run_creation.get_storage", return_value=storage))
        for p in extra:
            stack.enter_context(p)
        yield runs_api


def _restart(runs_api, run_id, db, user):
    return asyncio.run(runs_api.restart_run(run_id=run_id, db=db, current_user=user))


def test_restart_rematerializes_missing_local_input_from_storage(db, tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_run_id_collision.py item 5

    The pod-local original is gone (pod recycle) but the durable copy exists:
    restart re-fetches input/{id}.{ext} from storage into UPLOAD_DIR and forks
    the child from those bytes. Every other restart test patches
    _materialize_input_if_missing to a no-op, so the positive half of that
    helper had no assertion (the 404 half is test_release_guards.py::
    TestRestartQuota::test_restart_passes_quota_check_when_under_limit).
    """
    user = _make_user(db, email="rematerialize@example.com")
    original = _make_original(db, user, "ORIGM1")

    storage = LocalRunStorage(base_dir=str(tmp_path / "storage"))
    original_bytes = b"PK\x03\x04 durable-copy-of-the-original"
    storage.put_file("ORIGM1", "input/ORIGM1.docx", original_bytes)

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()  # NO ORIGM1.docx here: the pod-local copy is gone

    with _restart_env(original, storage, upload_dir) as runs_api:
        result = _restart(runs_api, "ORIGM1", db, user)

    child_id = result["run_id"]
    assert child_id != "ORIGM1"
    # Re-materialized from storage, byte for byte.
    assert (upload_dir / "ORIGM1.docx").read_bytes() == original_bytes
    # And the child was forked from exactly those bytes.
    assert storage.get_file(child_id, f"input/{child_id}.docx") == original_bytes
    assert (upload_dir / f"{child_id}.docx").read_bytes() == original_bytes
    manifest = json.loads(storage.get_file(child_id, "input/manifest.json"))
    assert manifest["sha256"] == hashlib.sha256(original_bytes).hexdigest()
    assert manifest["size_bytes"] == len(original_bytes)
    assert manifest["restarted_from"] == "ORIGM1"
    assert db.get(Run, child_id) is not None


def test_restart_with_no_local_or_durable_input_is_404(db, tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_run_id_collision.py item 5

    Neither a pod-local copy nor a durable one: restart is refused with
    404 file_not_found and creates nothing -- no child row, no archive, no
    local file.
    """
    user = _make_user(db, email="no-input@example.com")
    original = _make_original(db, user, "ORIGM2")

    storage = LocalRunStorage(base_dir=str(tmp_path / "storage"))  # empty
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()  # empty

    with _restart_env(original, storage, upload_dir) as runs_api:
        with pytest.raises(HTTPException) as exc:
            _restart(runs_api, "ORIGM2", db, user)

    assert exc.value.status_code == 404
    assert exc.value.detail["error"] == "file_not_found"
    assert [r.id for r in db.query(Run).all()] == ["ORIGM2"]
    assert list(upload_dir.iterdir()) == []
    assert not (tmp_path / "storage").exists() or list((tmp_path / "storage").iterdir()) == []


def test_restart_real_storage_fault_is_not_retried(db, tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_run_id_collision.py item 15

    Restart twin of the /upload test: a non-collision storage fault fails
    the restart on the first attempt -- one id drawn, one put, the attempt's
    pod-local file removed, the original's local file untouched, no child row
    and no by-submitter index.
    """
    user = _make_user(db, email="restart-fault@example.com")
    original = _make_original(db, user, "ORIGF1")

    storage = MagicMock()
    storage.put_file_exclusive.side_effect = RuntimeError("S3 down")
    draw = MagicMock(return_value="AAAAAA")

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    original_bytes = b"PK\x03\x04fake-restarted-docx"
    (upload_dir / "ORIGF1.docx").write_bytes(original_bytes)

    with _restart_env(original, storage, upload_dir,
                      extra=[patch("app.services.run_creation.generate_run_id", draw)]) as runs_api:
        with pytest.raises(HTTPException) as exc:
            _restart(runs_api, "ORIGF1", db, user)

    assert exc.value.status_code == 502
    assert exc.value.detail["error"] == "storage_unavailable"
    assert draw.call_count == 1
    assert storage.put_file_exclusive.call_count == 1
    assert storage.put_file_exclusive.call_args.args[:2] == ("AAAAAA", "input/manifest.json")
    storage.put_global.assert_not_called()
    assert [r.id for r in db.query(Run).all()] == ["ORIGF1"]
    assert sorted(p.name for p in upload_dir.iterdir()) == ["ORIGF1.docx"]
    assert (upload_dir / "ORIGF1.docx").read_bytes() == original_bytes


def test_restart_fails_after_exhausting_run_id_attempts(db, tmp_path, caplog):
    """PR #779 review thread web_interface/backend/tests/test_upload_run_id_collision.py item 19

    Restart shares create_run_archive, so its retry bound is the same
    _RUN_ID_ATTEMPTS: when every draw collides the restart fails 502, exactly
    that many ids were drawn and puts attempted, no child row exists, and
    every aborted attempt's pod-local file was unlinked (only the original's
    remains). Only /upload's exhaustion was tested before.
    """
    user = _make_user(db, email="restart-exhausted@example.com")
    original = _make_original(db, user, "ORIGB2")

    storage = MagicMock()
    storage.put_file_exclusive.side_effect = StorageKeyExists("always collides")
    # Exactly _RUN_ID_ATTEMPTS ids; a sixth draw would raise StopIteration
    # and push call_count to 6, failing the bound assertion below.
    colliding_ids = ["AAAAAA", "BBBBBB", "CCCCCC", "DDDDDD", "EEEEEE"]
    assert len(colliding_ids) == upload_module._RUN_ID_ATTEMPTS
    draw = MagicMock(side_effect=list(colliding_ids))

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    (upload_dir / "ORIGB2.docx").write_bytes(b"PK\x03\x04fake-restarted-docx")

    with _restart_env(original, storage, upload_dir,
                      extra=[patch("app.services.run_creation.generate_run_id", draw)]) as runs_api:
        with pytest.raises(HTTPException) as exc:
            _restart(runs_api, "ORIGB2", db, user)

    assert exc.value.status_code == 502
    assert exc.value.detail["error"] == "storage_unavailable"
    assert draw.call_count == upload_module._RUN_ID_ATTEMPTS
    assert storage.put_file_exclusive.call_count == upload_module._RUN_ID_ATTEMPTS
    # #797: exhaustion is not silent -- the caller logs it at ERROR.
    assert any(
        r.levelno == logging.ERROR and "could not allocate a collision-free run id" in r.getMessage()
        for r in caplog.records
    )
    storage.put_global.assert_not_called()
    assert [r.id for r in db.query(Run).all()] == ["ORIGB2"]
    assert sorted(p.name for p in upload_dir.iterdir()) == ["ORIGB2.docx"]


def test_restart_preserves_all_fields_on_child(db, tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_run_id_collision.py item 20

    The child row carries every field restart copies from the original --
    filename, file_type, submission_type and the three render flags (set to
    non-defaults so a fall-back is caught) -- plus the fields it sets fresh:
    user_id = the restarting user, status 'created', started_at set,
    completed_at clear, one pending Step per registry entry. The manifest's
    file_type/original_filename match the original. test_restart_render_options
    asserts only the flags and submission_type.
    """
    user = _make_user(db, email="preserve@example.com")
    original = _make_original(db, user, "ORIGP1", filename="Dr Who CV.docx")

    storage = LocalRunStorage(base_dir=str(tmp_path / "storage"))
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    (upload_dir / "ORIGP1.docx").write_bytes(b"PK\x03\x04fake-restarted-docx")

    before = datetime.now()
    with _restart_env(original, storage, upload_dir) as runs_api:
        result = _restart(runs_api, "ORIGP1", db, user)

    child = db.get(Run, result["run_id"])
    assert child is not None
    assert (child.filename, child.file_type, child.submission_type) == ("Dr Who CV.docx", "docx", "standard")
    assert (child.show_track_changes, child.show_pipeline_comments, child.strip_template_instructions) == (0, 1, 0)
    assert child.user_id == user.id
    assert child.status == "created"
    assert child.started_at is not None and child.started_at >= before.replace(microsecond=0)
    assert child.completed_at is None

    steps = db.query(Step).filter(Step.run_id == child.id).order_by(Step.step_number).all()
    assert [(s.step_number, s.stage_id, s.step_name, s.status) for s in steps] == [
        (d.number, d.stage_id, d.name, "pending") for d in STEP_REGISTRY
    ]

    manifest = json.loads(storage.get_file(child.id, "input/manifest.json"))
    assert manifest["file_type"] == "docx"
    assert manifest["original_filename"] == "Dr Who CV.docx"
    assert manifest["stored_as"] == f"{child.id}.docx"
    assert manifest["user_email"] == user.email


def test_restart_does_not_modify_original_run(db, tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_run_id_collision.py item 21

    Restart reads the original and must write nothing to it: every snapshotted
    Run column, its pod-local input, its durable archive (input AND outputs)
    and its storage key list are byte-identical after the restart. No test
    re-read the original after the call before this one.
    """
    user = _make_user(db, email="untouched@example.com")
    original = _make_original(
        db, user, "ORIGU1",
        completed_at=datetime(2026, 9, 1, 12, 0, 0),
        total_cost=1.25,
        total_tokens=4321,
        error_message=None,
    )

    storage = LocalRunStorage(base_dir=str(tmp_path / "storage"))
    original_bytes = b"PK\x03\x04 the-original-input"
    original_manifest = b'{"run_id": "ORIGU1", "original_filename": "my cv.docx"}'
    storage.put_file("ORIGU1", "input/ORIGU1.docx", original_bytes)
    storage.put_file("ORIGU1", "input/manifest.json", original_manifest)
    storage.put_file("ORIGU1", "outputs/report.json", b'{"done": true}')

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    (upload_dir / "ORIGU1.docx").write_bytes(original_bytes)

    before = _snapshot_run(original)
    keys_before = storage.list_files("ORIGU1")
    assert keys_before == ["input/ORIGU1.docx", "input/manifest.json", "outputs/report.json"]

    with _restart_env(original, storage, upload_dir) as runs_api:
        result = _restart(runs_api, "ORIGU1", db, user)

    assert result["run_id"] != "ORIGU1"
    db.refresh(original)
    assert _snapshot_run(original) == before
    assert (upload_dir / "ORIGU1.docx").read_bytes() == original_bytes
    assert storage.get_file("ORIGU1", "input/ORIGU1.docx") == original_bytes
    assert storage.get_file("ORIGU1", "input/manifest.json") == original_manifest
    assert storage.get_file("ORIGU1", "outputs/report.json") == b'{"done": true}'
    assert storage.list_files("ORIGU1") == keys_before
    # The original's Step rows (none seeded) gained nothing either.
    assert db.query(Step).filter(Step.run_id == "ORIGU1").count() == 0


def test_duplicate_restart_creates_independent_children(db, tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_run_id_collision.py item 22

    Restarting the same original twice yields two distinct children, each
    with its own row, its own durable archive of the same bytes, its own
    manifest naming itself and the original, its own pod-local copy and its
    own full set of pending Steps -- and the original is unchanged. No test
    called restart twice on one original before.
    """
    user = _make_user(db, email="duplicate@example.com")
    original = _make_original(db, user, "ORIGD1")

    storage = LocalRunStorage(base_dir=str(tmp_path / "storage"))
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    original_bytes = b"PK\x03\x04 restarted-twice"
    (upload_dir / "ORIGD1.docx").write_bytes(original_bytes)

    before = _snapshot_run(original)
    with _restart_env(original, storage, upload_dir,
                      extra=[patch("app.services.run_creation.generate_run_id", side_effect=["CHILD1", "CHILD2"])]) as runs_api:
        first = _restart(runs_api, "ORIGD1", db, user)
        second = _restart(runs_api, "ORIGD1", db, user)

    assert (first["run_id"], second["run_id"]) == ("CHILD1", "CHILD2")
    for cid in ("CHILD1", "CHILD2"):
        row = db.get(Run, cid)
        assert row is not None
        assert (row.user_id, row.status, row.filename, row.file_type) == (user.id, "created", "my cv.docx", "docx")
        assert storage.list_files(cid) == [f"input/{cid}.docx", "input/manifest.json"]
        assert storage.get_file(cid, f"input/{cid}.docx") == original_bytes
        manifest = json.loads(storage.get_file(cid, "input/manifest.json"))
        assert (manifest["run_id"], manifest["restarted_from"], manifest["stored_as"]) == (cid, "ORIGD1", f"{cid}.docx")
        assert (upload_dir / f"{cid}.docx").read_bytes() == original_bytes
        assert db.query(Step).filter(Step.run_id == cid).count() == len(STEP_REGISTRY)
    assert db.query(Step).count() == 2 * len(STEP_REGISTRY)

    db.refresh(original)
    assert _snapshot_run(original) == before
    assert sorted(p.name for p in upload_dir.iterdir()) == ["CHILD1.docx", "CHILD2.docx", "ORIGD1.docx"]


# --- (i) #181: restart replaces a still-running original --------------------


def test_restart_cancels_a_running_original(db, tmp_path):
    """#181: "Restart with file" on a still-running original must replace it,
    not fork a second copy that keeps burning Bedrock spend alongside the
    new one. Cancellation happens only after the child is fully created (see
    test_restart_does_not_cancel_original_when_restart_fails for the reverse)."""
    user = _make_user(db, email="running-restart@example.com")
    original = _make_original(db, user, "ORIGR1", status="running")

    storage = LocalRunStorage(base_dir=str(tmp_path / "storage"))
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    (upload_dir / "ORIGR1.docx").write_bytes(b"PK\x03\x04 fake-running-original")

    orchestrator_cancel = MagicMock()
    with _restart_env(
        original, storage, upload_dir,
        extra=[patch("app.pipeline.orchestrator.cancel_run", orchestrator_cancel)],
    ) as runs_api:
        result = _restart(runs_api, "ORIGR1", db, user)

    assert result["run_id"] != "ORIGR1"
    # Without this signal the original's pipeline keeps running every stage.
    orchestrator_cancel.assert_called_once_with("ORIGR1")
    db.refresh(original)
    assert original.status == "cancelled"
    assert original.error_message == "Cancelled by user"
    assert original.completed_at is not None
    # The child itself is untouched by the cancel of its parent.
    child = db.get(Run, result["run_id"])
    assert child.status == "created"


def test_cancel_run_record_does_not_signal_orchestrator_if_commit_fails(db):
    """PR #942 review comment 4106923776: db.commit() can raise (#802's
    commit_run_or_compensate establishes the same is true on the sibling
    write path), so it must run BEFORE the orchestrator signal, not after --
    otherwise a commit failure would leave the pipeline told to stop while
    the row still reads "running" (CancelledException's handler does not
    touch the DB; see orchestrator.py's "status already updated by API
    endpoint"), stranding the run until reconcile_stale_runs sweeps it up an
    hour later. orchestrator_cancel itself can't raise (cancel_run's
    set.add, and RedisBroker.request_cancel's own try/except), so a raised
    commit is the only failure this ordering needs to guard against."""
    from app.api import runs as runs_api

    user = _make_user(db, email="commit-fails-on-cancel@example.com")
    run = _make_original(db, user, "CANCELFAIL1", status="running")

    orchestrator_cancel = MagicMock()
    with patch("app.pipeline.orchestrator.cancel_run", orchestrator_cancel), \
            patch.object(db, "commit", side_effect=RuntimeError("db down")):
        with pytest.raises(RuntimeError):
            runs_api._cancel_run_record(db, run)

    orchestrator_cancel.assert_not_called()


def test_cancel_run_endpoint_marks_and_signals(db):
    """cancel_run (POST /run/{id}/cancel) must actually delegate to
    _cancel_run_record -- the handler's own return dict hardcodes
    status="cancelled" regardless, so this kills the mutant that deletes
    the _cancel_run_record(db, run) call from cancel_run by checking the
    DB row and the orchestrator signal, not just the response body."""
    from app.api import runs as runs_api

    user = _make_user(db, email="cancel-endpoint@example.com")
    run = _make_original(db, user, "CANCELOK1", status="running")

    orchestrator_cancel = MagicMock()
    with patch.object(runs_api, "check_run_access", return_value=run), \
            patch("app.pipeline.orchestrator.cancel_run", orchestrator_cancel):
        result = asyncio.run(
            runs_api.cancel_run(run_id="CANCELOK1", db=db, current_user=user)
        )

    assert result["status"] == "cancelled"
    orchestrator_cancel.assert_called_once_with("CANCELOK1")
    db.refresh(run)
    assert run.status == "cancelled"
    assert run.error_message == "Cancelled by user"
    assert run.completed_at is not None


def test_restart_does_not_cancel_original_when_restart_fails(db, tmp_path):
    """#181 regression guard: a restart that fails (missing file, here) must
    leave a running original alone. Cancelling the original before the
    child is known to exist would strand the user with neither run -- see
    the 404 branch of restart_run, which raises before create_run_archive."""
    user = _make_user(db, email="running-restart-fails@example.com")
    original = _make_original(db, user, "ORIGR2", status="running")

    storage = LocalRunStorage(base_dir=str(tmp_path / "storage"))  # empty
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()  # no ORIGR2.docx anywhere -> 404

    with _restart_env(original, storage, upload_dir) as runs_api:
        with pytest.raises(HTTPException) as exc:
            _restart(runs_api, "ORIGR2", db, user)

    assert exc.value.status_code == 404
    db.refresh(original)
    assert original.status == "running"
    assert original.error_message is None
    assert original.completed_at is None
    assert [r.id for r in db.query(Run).all()] == ["ORIGR2"]
