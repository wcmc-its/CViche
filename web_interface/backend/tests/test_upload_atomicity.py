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
import hashlib
import io
import json
import logging
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from docx import Document
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import object_session

from app.api import upload as upload_module
from app.api.upload import _extract_text, _validate_docx_magic
from app.models import User, Run, Step
from app.pipeline.step_registry import STEP_REGISTRY
from app.storage.local_storage import LocalRunStorage

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _make_user(db, email="test@example.com", **overrides):
    """Create an allowed, consented simple-mode user and return it.

    ``overrides`` land on the User row (e.g. role="admin", daily_limit=0,
    consent_version="0.9") for the tests that need a user the gate rejects.
    """
    fields = dict(
        email=email,
        display_name="Test User",
        role="user",
        consent_version="1.0",  # matches seed_simple_mode's consent_version config
    )
    fields.update(overrides)
    user = User(**fields)
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
        files={"file": ("cv.docx", b"PK\x03\x04dummy-docx-bytes", "application/octet-stream")}, data={"submission_type": "own_cv"},
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


# --- PR #779 review thread (test_upload_atomicity.py) ------------------------
# Each test below answers one numbered ask from that thread (or the collision
# thread where noted); the docstring's first line names it.

def _docx_bytes(text: str) -> bytes:
    """A real .docx (valid ZIP + word/document.xml) whose body is ``text``.
    Local copy of test_release_guards' helper, per the no-cross-module-import
    convention of these upload test files."""
    doc = Document()
    for line in text.split("\n"):
        doc.add_paragraph(line)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _post_upload(client, filename, content, content_type="application/octet-stream", data=None):
    return client.post(
        "/api/upload",
        files={"file": (filename, content, content_type)},
        data={"submission_type": "own_cv", **(data or {})},
    )


def _run_patches(patches, fn):
    """Start every patch, run fn(), stop them all -- the file's idiom."""
    for p in patches:
        p.start()
    try:
        return fn()
    finally:
        for p in patches:
            p.stop()


def _real_storage_patches(tmp_path):
    """Bypass validation but drive a REAL LocalRunStorage under tmp_path/storage
    and a real pod-local UPLOAD_DIR under tmp_path/uploads. Returns
    (storage, upload_dir, patches)."""
    storage = LocalRunStorage(base_dir=str(tmp_path / "storage"))
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    patches = _bypass_file_validation(upload_dir)
    patches.append(patch("app.api.upload.get_storage", return_value=storage))
    return storage, upload_dir, patches


# --- item 1: unsupported extension ------------------------------------------

@pytest.mark.parametrize("filename, reported_ext", [
    ("cv.exe", ".exe"),
    ("cv.txt", ".txt"),
    ("cv.DOC", ".doc"),   # suffix is lower-cased before the check
    ("cv", ""),           # no suffix at all -> "Unsupported file type: ."
    ("cv.pdf", ".pdf"),   # #524: PDF is no longer accepted at the API
    ("cv.PDF", ".pdf"),   # suffix is lower-cased before the check
])
def test_upload_rejects_unsupported_extension(client, db, seed_simple_mode, tmp_path, filename, reported_ext):
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 1

    A filename whose suffix is not .docx is rejected with the exact 400
    message from upload.py:325-328, BEFORE the rate limiter is consulted
    (upload.py:328 -- "after file validation so bad uploads don't count") and
    before any storage write; no Run row is created. An empty filename cannot
    be tested through multipart: FastAPI itself 422s a file part with no
    filename before the endpoint's "No filename provided" guard runs.

    #524: .pdf is now rejected the same as any other unsupported extension
    (ALLOWED_UPLOAD_EXTENSIONS == (".docx",)), matching the frontend's
    .docx-only guard -- every downstream reader is python-docx only.
    """
    user = _make_user(db)
    _auth(client, user)

    storage = MagicMock()
    rate_limit = MagicMock(return_value=None)
    patches = [
        patch("app.api.upload.UPLOAD_DIR", tmp_path),
        patch("app.api.upload.get_storage", return_value=storage),
        patch("app.api.upload.check_rate_limit", rate_limit),
    ]
    resp = _run_patches(patches, lambda: _post_upload(client, filename, b"MZ\x90\x00not-a-cv"))

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == {
        "error": "bad_request",
        "message": (
            f"Unsupported file type: {reported_ext}. Only .docx files are supported. "
            "Please convert your file to .docx before uploading."
        ),
    }
    rate_limit.assert_not_called()
    storage.put_file_exclusive.assert_not_called()
    assert db.query(Run).count() == 0
    assert list(tmp_path.iterdir()) == []


# --- G-524: PDF rejected at both API validators (#524, #525) ----------------

@pytest.mark.parametrize("filename", ["cv.pdf", "cv.PDF"])
def test_estimate_rejects_pdf(client, db, seed_simple_mode, filename):
    """#524/#525: /estimate (upload.py:~511) applies the same
    ALLOWED_UPLOAD_EXTENSIONS gate as /upload, so a PDF can no longer reach
    the (now-deleted) pypdf-import branch. Uppercase suffix (cv.PDF) covers
    the same lower-casing the extension check applies before the gate."""
    user = _make_user(db)
    _auth(client, user)

    resp = client.post(
        "/api/estimate",
        files={"file": (filename, b"%PDF-1.4 dummy pdf content", "application/pdf")},
    )

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == {
        "error": "bad_request",
        "message": (
            "Unsupported file type: .pdf. Only .docx files are supported. "
            "Please convert your file to .docx before uploading."
        ),
    }


def test_upload_still_accepts_docx_after_pdf_rejection(client, db, seed_simple_mode, tmp_path):
    """#524 regression: rejecting .pdf must not disturb the .docx path -- the
    ONLY accepted extension is unchanged."""
    user = _make_user(db)
    _auth(client, user)
    patches = _bypass_file_validation(tmp_path)
    patches.append(patch("app.api.upload.get_storage", return_value=MagicMock()))
    resp = _run_patches(
        patches, lambda: _post_upload(client, "cv.docx", b"PK\x03\x04dummy-docx-bytes", DOCX_MIME)
    )

    assert resp.status_code == 200, resp.text
    assert db.query(Run).count() == 1
    assert db.get(Run, resp.json()["run_id"]).file_type == "docx"


# --- item 3: .docx with a broken ZIP / no word/document.xml ------------------

def _zip_without_document_xml() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("hello.txt", "not a word document")
    return buf.getvalue()


@pytest.mark.parametrize("payload_name, payload", [
    ("truncated-zip", b"PK\x03\x04" + b"\x00garbage" * 25),   # zipfile.BadZipFile
    ("zip-without-document-xml", _zip_without_document_xml()),  # valid ZIP, wrong contents
])
def test_upload_rejects_docx_with_bad_zip_structure(client, db, seed_simple_mode, tmp_path, caplog, payload_name, payload):
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 3

    Both payloads pass the 4-byte ZIP magic check at upload.py:52, so this
    exercises the zipfile branch at :54-58 for real (the only existing
    .docx-spoof test sends %PDF bytes, which never reach zipfile). Rejected
    with the exact .docx-mismatch 400, a [SECURITY] warning, no storage
    write, no Run row.
    """
    assert payload[:4] == upload_module.ZIP_MAGIC  # precondition: magic alone would pass
    assert _validate_docx_magic(payload) is False

    user = _make_user(db)
    _auth(client, user)
    storage = MagicMock()
    patches = [
        patch("app.api.upload.UPLOAD_DIR", tmp_path),
        patch("app.api.upload.get_storage", return_value=storage),
    ]
    with caplog.at_level(logging.WARNING):
        resp = _run_patches(patches, lambda: _post_upload(client, "cv.docx", payload, DOCX_MIME))

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == {
        "error": "bad_request",
        "message": "File content does not match .docx format. The file may be corrupted or mislabeled.",
    }
    security = [r.getMessage() for r in caplog.records if "[SECURITY]" in r.getMessage()]
    assert any("claims .docx" in m and user.email in m for m in security), security
    storage.put_file_exclusive.assert_not_called()
    assert db.query(Run).count() == 0
    assert list(tmp_path.iterdir()) == []


def test_validate_docx_magic_accepts_a_real_docx():
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 3

    Positive control for the zipfile branch: a real python-docx file (ZIP with
    word/document.xml) passes, so the rejections above are structural, not a
    validator that fails everything.
    """
    assert _validate_docx_magic(_docx_bytes("real content")) is True


# --- item 7: password-protected document ------------------------------------

def test_extract_text_returns_empty_for_encrypted_document():
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 7

    upload.py:89-95: an extraction error whose message names
    password/encrypt/decrypt maps to "" (trips the unreadable-document guard),
    NOT to None (which would fail open and let the upload through).

    #524 removed the .pdf/pdfplumber arm of this (formerly parametrized) test
    along with _extract_text's now-unreachable .pdf branch -- .pdf can no
    longer reach _extract_text at all (rejected earlier by the extension
    check).
    """
    with patch("docx.Document", side_effect=Exception("Package is encrypted")):
        assert _extract_text(b"PK\x03\x04payload", ".docx") == ""


def test_extract_text_returns_none_for_unrecognized_extension():
    """#524: _extract_text's if/elif now only names .docx. Any other
    extension (e.g. the deleted .pdf arm) falls through to the final
    ``return None`` with no attempt at extraction -- "cannot determine",
    not "read and found nothing." Guards the deletion of the .pdf branch
    against silently returning "" instead."""
    assert _extract_text(b"%PDF-1.4 dummy", ".pdf") is None


def test_upload_rejects_password_protected_document(client, db, seed_simple_mode, tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 7

    Endpoint half: extraction reporting "" (the encrypted-document result)
    is rejected at upload.py:353-358 with the password-protected message; no
    storage write, no Run row. Mutating :106 `return ""` to `return None`
    turns this into a 200.
    """
    user = _make_user(db)
    _auth(client, user)
    storage = MagicMock()
    patches = [
        patch("app.api.upload.UPLOAD_DIR", tmp_path),
        patch("app.api.upload._validate_docx_magic", return_value=True),
        patch("app.api.upload._extract_text", return_value=""),
        patch("app.api.upload.get_storage", return_value=storage),
    ]
    resp = _run_patches(patches, lambda: _post_dummy_upload(client))

    assert resp.status_code == 400, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "bad_request"
    assert "password-protected" in detail["message"]
    assert detail["message"].startswith("We couldn't read any text from this file.")
    storage.put_file_exclusive.assert_not_called()
    assert db.query(Run).count() == 0


# --- item 8: WCM template warning ------------------------------------------

@pytest.mark.parametrize("warning, ratio", [(True, 0.82), (False, None)])
def test_upload_returns_wcm_template_warning(client, db, seed_simple_mode, tmp_path, warning, ratio):
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 8

    upload.py:367 feeds the ALREADY-extracted text to detect_wcm_template and
    :478-479 surface its (warning, ratio) pair on the response verbatim. The
    warning never blocks: the Run is created either way (:360-366).
    """
    user = _make_user(db)
    _auth(client, user)
    extracted = "x" * 600  # above MIN_EXTRACTED_CHARS so the readability gate passes
    detect = MagicMock(return_value=(warning, ratio))
    patches = [
        patch("app.api.upload.UPLOAD_DIR", tmp_path),
        patch("app.api.upload._validate_docx_magic", return_value=True),
        patch("app.api.upload._extract_text", return_value=extracted),
        patch("app.api.upload.detect_wcm_template", detect),
        patch("app.api.upload.get_storage", return_value=MagicMock()),
    ]
    resp = _run_patches(patches, lambda: _post_dummy_upload(client))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["wcm_template_warning"] is warning
    assert body["wcm_template_match_ratio"] == ratio
    detect.assert_called_once()
    assert detect.call_args.args[0] == extracted  # reuses the extracted text, no re-parse
    assert db.query(Run).filter(Run.id == body["run_id"]).first().status == "created"


# --- item 9: render options persisted --------------------------------------

@pytest.mark.parametrize("form, expected", [
    (
        {"include_track_changes": "false", "include_classification_comments": "true", "strip_wcm_instructions": "false"},
        (0, 1, 0),
    ),
    ({}, (1, 0, 1)),  # absent fields -> the Form defaults at upload.py:301-303
])
def test_upload_persists_render_options(client, db, seed_simple_mode, tmp_path, form, expected):
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 9

    The multipart form fields reach the Run columns as the truthy ints
    upload.py:453-455 writes (show_track_changes, show_pipeline_comments,
    strip_template_instructions), with the documented defaults when absent.
    Swapping any two of those three lines fails the first arm.
    """
    user = _make_user(db)
    _auth(client, user)
    patches = _bypass_file_validation(tmp_path)
    patches.append(patch("app.api.upload.get_storage", return_value=MagicMock()))
    resp = _run_patches(
        patches,
        lambda: _post_upload(client, "cv.docx", b"PK\x03\x04dummy", DOCX_MIME, data=form),
    )

    assert resp.status_code == 200, resp.text
    run = db.get(Run, resp.json()["run_id"])
    assert (run.show_track_changes, run.show_pipeline_comments, run.strip_template_instructions) == expected


# --- submission_type: per-upload role attestation ----------------------------

@pytest.mark.parametrize("value, status", [
    ("authorized_admin", 200),
    ("own_cv", 200),
    ("faculty", 422),  # not one of the two attested roles
    (None, 422),       # absent: the attestation is required, no default
])
def test_upload_requires_and_persists_submission_type(client, db, seed_simple_mode, tmp_path, value, status):
    """Every upload records which attestation the submitter accepted (Faculty
    Affairs, 2026-09). Before this the Run.submission_type column was never
    written -- restart copied None to None."""
    user = _make_user(db)
    _auth(client, user)
    patches = _bypass_file_validation(tmp_path)
    patches.append(patch("app.api.upload.get_storage", return_value=MagicMock()))
    resp = _run_patches(
        patches,
        lambda: client.post(
            "/api/upload",
            files={"file": ("cv.docx", b"PK\x03\x04dummy", DOCX_MIME)},
            data={} if value is None else {"submission_type": value},
        ),
    )

    assert resp.status_code == status, resp.text
    if status == 200:
        assert db.get(Run, resp.json()["run_id"]).submission_type == value
    else:
        assert db.query(Run).count() == 0


# --- item 10: manifest integrity + by-submitter index ------------------------

def test_upload_manifest_and_index_are_consistent(client, db, seed_simple_mode, tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 10

    Reads the archived manifest back from a REAL LocalRunStorage and checks
    every field upload.py:386-397 writes (hash, size, names, type, user);
    the by-submitter index (:428-431, keyed by the LOWER-cased email) holds
    the byte-identical manifest; the archived content is the upload bytes;
    and the Run row is tied to the uploading user.
    """
    user = _make_user(db, email="Case@Example.com")
    _auth(client, user)
    storage, upload_dir, patches = _real_storage_patches(tmp_path)
    content = b"PK\x03\x04manifest-integrity-bytes"
    before = datetime.now()
    resp = _run_patches(patches, lambda: _post_upload(client, "My CV.docx", content, DOCX_MIME))

    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    manifest_bytes = storage.get_file(run_id, "input/manifest.json")
    manifest = json.loads(manifest_bytes)
    assert manifest["run_id"] == run_id
    assert manifest["original_filename"] == "My CV.docx"
    assert manifest["stored_as"] == f"{run_id}.docx"
    assert manifest["file_type"] == "docx"
    assert manifest["size_bytes"] == len(content)
    assert manifest["sha256"] == hashlib.sha256(content).hexdigest()
    assert manifest["content_type"] == DOCX_MIME
    assert manifest["user_email"] == "Case@Example.com"
    assert before <= datetime.fromisoformat(manifest["uploaded_at"]) <= datetime.now()
    assert set(manifest) == {
        "run_id", "original_filename", "stored_as", "file_type", "size_bytes",
        "sha256", "content_type", "uploaded_at", "user_email",
    }

    # The index is the SAME manifest, under the lower-cased submitter email.
    # The directory name is read back from disk (iterdir) rather than opened
    # by the expected name, because a case-insensitive filesystem (macOS
    # APFS) would happily open "Case@Example.com" as "case@example.com".
    by_submitter = tmp_path / "storage" / "by-submitter"
    assert [p.name for p in by_submitter.iterdir()] == ["case@example.com"]
    index_path = by_submitter / "case@example.com" / run_id / "manifest.json"
    assert index_path.read_bytes() == manifest_bytes

    assert storage.get_file(run_id, f"input/{run_id}.docx") == content
    assert db.get(Run, run_id).user_id == user.id


# --- item 11: step rows ------------------------------------------------------

def test_upload_creates_pending_step_rows(client, db, seed_simple_mode, tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 11

    upload.py:460-468: exactly one Step per STEP_REGISTRY entry, carrying the
    registry's number / stage_id / name, all "pending", all on this run.
    """
    user = _make_user(db)
    _auth(client, user)
    patches = _bypass_file_validation(tmp_path)
    patches.append(patch("app.api.upload.get_storage", return_value=MagicMock()))
    resp = _run_patches(patches, lambda: _post_dummy_upload(client))

    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]
    steps = db.query(Step).filter(Step.run_id == run_id).order_by(Step.step_number).all()
    assert len(steps) == len(STEP_REGISTRY) == 12
    assert [(s.step_number, s.stage_id, s.step_name, s.status) for s in steps] == [
        (d.number, d.stage_id, d.name, "pending") for d in STEP_REGISTRY
    ]
    assert db.query(Step).count() == len(STEP_REGISTRY)  # none attached elsewhere


# --- item 12: rate limit -----------------------------------------------------

@pytest.mark.parametrize("role, expected_status", [("user", 429), ("admin", 200)])
def test_upload_enforces_rate_limit(client, db, seed_simple_mode, tmp_path, role, expected_status):
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 12

    A user whose daily_limit is 0 is refused at upload.py:329-331 with the
    rate_limited envelope (limit_type "daily") before any storage write and
    with no Run row; an admin with the same column value is unlimited
    (rate_limiter.py:17-18) and uploads normally.
    """
    user = _make_user(db, email=f"{role}@example.com", role=role, daily_limit=0)
    _auth(client, user)
    storage = MagicMock()
    patches = _bypass_file_validation(tmp_path)
    patches.append(patch("app.api.upload.get_storage", return_value=storage))
    resp = _run_patches(patches, lambda: _post_dummy_upload(client))

    assert resp.status_code == expected_status, resp.text
    if expected_status == 429:
        detail = resp.json()["detail"]
        assert detail["error"] == "rate_limited"
        assert detail["message"] == "Daily limit of 0 runs reached."
        assert detail["details"]["limit_type"] == "daily"
        assert detail["details"]["limit"] == 0
        storage.put_file_exclusive.assert_not_called()
        assert db.query(Run).count() == 0
    else:
        storage.put_file_exclusive.assert_called()
        assert db.query(Run).count() == 1


def test_upload_validates_extension_before_rate_limit(client, db, seed_simple_mode, tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 12

    Ordering: a rate-limited user posting a bad extension gets the 400, not
    the 429 -- validation precedes the quota check (upload.py:328) so a
    rejected upload never counts against the user.
    """
    user = _make_user(db, daily_limit=0)
    _auth(client, user)
    patches = [
        patch("app.api.upload.UPLOAD_DIR", tmp_path),
        patch("app.api.upload.get_storage", return_value=MagicMock()),
    ]
    resp = _run_patches(patches, lambda: _post_upload(client, "cv.exe", b"MZ"))

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["message"].startswith("Unsupported file type: .exe.")


# --- item 14: pod-local cleanup after a fatal storage failure ----------------

def test_upload_removes_local_file_after_fatal_storage_failure(client, db, seed_simple_mode, tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 14

    The pod-local copy is written (exclusive "xb") BEFORE the durable
    archive; when the archive fails fatally, create_run_archive's
    `except Exception` branch (upload.py:271-275) unlinks it. Asserted on
    the directory itself: deleting the _unlink_best_effort call at :274
    leaves `<run_id>.docx` behind and fails this. The collision-retry and
    restart arms of this ask live in test_upload_run_id_collision.py.
    """
    user = _make_user(db)
    _auth(client, user)
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    storage = MagicMock()
    storage.put_file_exclusive.side_effect = Exception("S3 unavailable")
    patches = _bypass_file_validation(upload_dir)
    patches.append(patch("app.api.upload.get_storage", return_value=storage))
    patches.append(patch("app.api.upload.generate_run_id", return_value="FATAL1"))
    resp = _run_patches(patches, lambda: _post_dummy_upload(client))

    assert resp.status_code == 502, resp.text
    # The local write happened (the archive attempt that failed came after it)...
    storage.put_file_exclusive.assert_called_once_with("FATAL1", "input/manifest.json", storage.put_file_exclusive.call_args.args[2])
    # ...and was cleaned up: nothing is left on the pod.
    assert list(upload_dir.iterdir()) == []
    assert db.query(Run).count() == 0


# --- item 15: content write fails after the manifest succeeded --------------

def test_upload_content_write_failure_after_manifest_is_fatal(client, db, seed_simple_mode, tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 15

    Manifest-first ordering (upload.py:262-265) means a real storage fault
    can strike between the two exclusive puts. Pins the documented outcome
    (:208-223): fatal 502, NO retry (exactly two puts), no Run row, no index
    write, pod-local file removed -- and the manifest left orphaned under the
    abandoned id with no content beside it. That orphan is asserted
    explicitly so a future cleanup changes this test on purpose.
    """
    user = _make_user(db)
    _auth(client, user)
    storage, upload_dir, patches = _real_storage_patches(tmp_path)
    real_put = storage.put_file_exclusive

    def manifest_ok_content_fails(run_id, key, data):
        if key == "input/manifest.json":
            return real_put(run_id, key, data)
        raise RuntimeError("S3 down")

    put = MagicMock(side_effect=manifest_ok_content_fails)
    put_global = MagicMock()
    patches.append(patch.object(storage, "put_file_exclusive", put))
    patches.append(patch.object(storage, "put_global", put_global))
    patches.append(patch("app.api.upload.generate_run_id", return_value="ORPHN1"))
    resp = _run_patches(patches, lambda: _post_dummy_upload(client))

    assert resp.status_code == 502, resp.text
    assert resp.json()["detail"]["error"] == "storage_unavailable"
    assert [c.args[:2] for c in put.call_args_list] == [
        ("ORPHN1", "input/manifest.json"),
        ("ORPHN1", "input/ORPHN1.docx"),
    ]  # exactly two puts: a non-collision fault is not retried
    put_global.assert_not_called()
    assert db.query(Run).count() == 0
    assert list(upload_dir.iterdir()) == []
    # The documented orphan: manifest present, content absent.
    assert storage.exists("ORPHN1", "input/manifest.json") is True
    assert storage.exists("ORPHN1", "input/ORPHN1.docx") is False


# --- item 17: path-traversal characters in the original filename ------------

@pytest.mark.parametrize("filename", [
    "../../../etc/evil.docx",
    "a/b\\c.docx",
    "x" * 296 + ".docx",   # 301 chars; Run.filename is String(255) -- SQLite does not enforce it
])
def test_upload_traversal_filename_cannot_escape(client, db, seed_simple_mode, tmp_path, filename):
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 17

    Starlette hands the multipart filename over verbatim (probed on 0.52.1).
    upload.py:324 takes only its suffix and :252 names the stored file
    `<run_id>.<ext>`, so the client string never shapes a filesystem or
    storage path: the ONLY files created under tmp_path are the four the
    endpoint owns. The raw string is retained as a display name (response,
    Run.filename, manifest original_filename) -- pinned here so the
    sanitization tracked in #796 flips this assertion deliberately.
    """
    user = _make_user(db)
    _auth(client, user)
    storage, upload_dir, patches = _real_storage_patches(tmp_path)
    content = b"PK\x03\x04traversal-probe-bytes"
    resp = _run_patches(patches, lambda: _post_upload(client, filename, content, DOCX_MIME))

    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]

    created = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*") if p.is_file())
    assert created == sorted([
        f"uploads/{run_id}.docx",
        f"storage/{run_id}/input/{run_id}.docx",
        f"storage/{run_id}/input/manifest.json",
        f"storage/by-submitter/test@example.com/{run_id}/manifest.json",
    ])
    assert storage.list_files(run_id, "input/") == [f"input/{run_id}.docx", "input/manifest.json"]
    assert storage.get_file(run_id, f"input/{run_id}.docx") == content
    assert not any(Path(filename).name in p.name for p in tmp_path.rglob("*"))

    assert resp.json()["filename"] == filename
    assert db.get(Run, run_id).filename == filename
    assert json.loads(storage.get_file(run_id, "input/manifest.json"))["original_filename"] == filename


# --- collision thread item 2: DB commit fails after the archive succeeded ---

def test_upload_db_commit_failure_leaves_archive_but_no_run(client, db, seed_simple_mode, tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_run_id_collision.py item 2

    upload.py:470 `db.commit()` is unguarded: a DB fault there reaches the
    app's catch-all handler (500 internal_error, sanitized) and leaves the
    archive + by-submitter index in storage with NO Run or Step row -- the
    storage-only orphan #116 tracks. This pins that current behaviour; the
    archive-before-DB ordering is #170's requirement.
    """
    user = _make_user(db)
    _auth(client, user)
    storage, upload_dir, patches = _real_storage_patches(tmp_path)
    patches.append(patch("app.api.upload.generate_run_id", return_value="DBFA1L"))
    patches.append(patch.object(db, "commit", side_effect=OperationalError("stmt", {}, Exception("db down"))))
    resp = _run_patches(patches, lambda: _post_dummy_upload(client))

    assert resp.status_code == 500, resp.text
    assert resp.json() == {"error": "internal_error", "message": "An unexpected error occurred."}

    db.rollback()
    assert db.query(Run).count() == 0
    assert db.query(Step).count() == 0
    # The durable side completed before the DB failed: archive + index persist.
    assert storage.exists("DBFA1L", "input/manifest.json") is True
    assert storage.exists("DBFA1L", "input/DBFA1L.docx") is True
    assert (tmp_path / "storage" / "by-submitter" / "test@example.com" / "DBFA1L" / "manifest.json").exists()


# --- collision thread item 4: consent enforced at upload time ---------------

@pytest.mark.parametrize("consent_version", ["0.9", None])
def test_upload_requires_current_consent(client, db, seed_simple_mode, tmp_path, consent_version):
    """PR #779 review thread web_interface/backend/tests/test_upload_run_id_collision.py item 4

    upload.py:310-318: a user whose consent_version is stale (or never set)
    is refused with 403 consent_required before the rate limiter, the file
    checks, or any storage write run; no Run row. Flipping :311 `!=` to
    `==` fails both arms.
    """
    user = _make_user(db, consent_version=consent_version)
    _auth(client, user)
    storage = MagicMock()
    rate_limit = MagicMock(return_value=None)
    patches = _bypass_file_validation(tmp_path)
    patches.append(patch("app.api.upload.get_storage", return_value=storage))
    patches.append(patch("app.api.upload.check_rate_limit", rate_limit))
    resp = _run_patches(patches, lambda: _post_dummy_upload(client))

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == {
        "error": "consent_required",
        "message": "Please review and accept the updated consent terms.",
    }
    rate_limit.assert_not_called()
    storage.put_file_exclusive.assert_not_called()
    assert db.query(Run).count() == 0
