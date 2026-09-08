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
import json
import re
import threading
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import object_session

from app.models import Run, User
from app.storage.base import StorageKeyExists
from app.storage.local_storage import LocalRunStorage
from app.api import upload as upload_module
from app.api.upload import generate_run_id

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
         patch("app.api.upload.UPLOAD_DIR", upload_dir), \
         patch("app.api.upload.get_storage", return_value=storage), \
         patch("app.api.upload.generate_run_id", side_effect=["AAAAAA", "BBBBBB"]):
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
    # One put_file_exclusive call per attempt (each attempt's manifest put
    # goes first and collides, so the content put is never reached); bounded
    # by the named retry constant, not unbounded.
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
