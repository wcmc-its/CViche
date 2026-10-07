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
import asyncio
import hashlib
import io
import json
import logging
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from docx import Document
from fastapi import HTTPException
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import object_session

from app.api import upload as upload_module
from app.api.upload import _extract_text, _validate_docx_magic
from app.models import Run, Step, User
from app.pipeline.step_registry import STEP_REGISTRY
from app.services import upload_validation
from app.services.run_creation import RUN_NOT_CREATED_NOTHING_SAVED
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
    from app.auth import COOKIE_NAME, create_session_cookie
    # create_session_cookie reads the current epoch from a DB session;
    # `user` was just committed on the test's session, so borrow that one.
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, object_session(user)))


def _bypass_file_validation(tmp_path):
    """Patch out the magic-byte / text-extraction / template checks so a dummy
    byte payload reaches the storage step, and redirect the ephemeral pod-local
    write into tmp_path. Returns a list of patch context managers."""
    return [
        patch("app.services.run_creation.UPLOAD_DIR", tmp_path),
        patch("app.services.run_creation._validate_docx_magic", return_value=True),
        # None == "couldn't extract"; the endpoint fails open and skips the
        # min-text gate, which is all we need to reach the storage step.
        patch("app.services.run_creation._extract_text", return_value=None),
        patch("app.services.run_creation.detect_wcm_template", return_value=(False, None)),
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
    patches.append(patch("app.services.run_creation.get_storage", return_value=storage))
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
    patches.append(patch("app.services.run_creation.get_storage", return_value=storage))
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
    patches.append(patch("app.services.run_creation.get_storage", return_value=storage))
    return storage, upload_dir, patches


# --- item 1: unsupported extension ------------------------------------------

@pytest.mark.parametrize("filename, reported_ext", [
    ("cv.exe", ".exe"),
    ("cv.txt", ".txt"),
    ("cv.DOC", ".doc"),   # suffix is lower-cased before the check
    ("cv", ""),           # no suffix at all -> "Unsupported file type: ."
])
def test_upload_rejects_unsupported_extension(client, db, seed_simple_mode, tmp_path, filename, reported_ext):
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 1

    A filename whose suffix is not .docx or .pdf is rejected with the exact 400
    message from upload.py:325-328, BEFORE the rate limiter is consulted
    (upload.py:328 -- "after file validation so bad uploads don't count") and
    before any storage write; no Run row is created. An empty filename cannot
    be tested through multipart: FastAPI itself 422s a file part with no
    filename before the endpoint's "No filename provided" guard runs.

    #806: .pdf is accepted again (the orchestrator converts it); .doc stays
    rejected.
    """
    user = _make_user(db)
    _auth(client, user)

    storage = MagicMock()
    rate_limit = MagicMock(return_value=None)
    patches = [
        patch("app.services.run_creation.UPLOAD_DIR", tmp_path),
        patch("app.services.run_creation.get_storage", return_value=storage),
        patch("app.api.upload.check_rate_limit", rate_limit),
    ]
    resp = _run_patches(patches, lambda: _post_upload(client, filename, b"MZ\x90\x00not-a-cv"))

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == {
        "error": "bad_request",
        "message": (
            f"Unsupported file type: {reported_ext}. Only .docx and .pdf files are supported. "
            "Please convert your file to .docx or .pdf before uploading."
        ),
    }
    rate_limit.assert_not_called()
    storage.put_file_exclusive.assert_not_called()
    assert db.query(Run).count() == 0
    assert list(tmp_path.iterdir()) == []


# --- #806: PDF accepted at both API validators -------------------------------

@pytest.mark.parametrize("filename", ["cv.pdf", "cv.PDF"])
def test_estimate_measures_pdf_text(client, db, seed_simple_mode, filename, cv_pdf):
    """#806: /estimate applies the same ALLOWED_UPLOAD_EXTENSIONS gate as
    /upload and sizes a PDF from its real extracted text, not the fixed
    fallback guess. Uppercase suffix (cv.PDF) covers the lower-casing the
    extension check applies before the gate."""
    user = _make_user(db)
    _auth(client, user)

    resp = client.post(
        "/api/estimate",
        files={"file": (filename, cv_pdf(), "application/pdf")},
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["text_characters_is_guess"] is False
    assert body["text_characters"] > upload_module.MIN_EXTRACTED_CHARS


def test_upload_rejects_mostly_scanned_pdf(client, db, seed_simple_mode, cv_pdf):
    """#1282: one text page clears MIN_EXTRACTED_CHARS, but a PDF whose
    image-only pages reach SCANNED_PAGE_REJECT_SHARE is refused, naming them."""
    user = _make_user(db)
    _auth(client, user)
    resp = client.post(
        "/api/upload",
        files={"file": ("cv.pdf", cv_pdf(image_pages=(1, 2)), "application/pdf")},
        data={"submission_type": "own_cv"},
    )
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["message"].startswith("Pages 2–3 of this PDF are scanned images")
    assert db.query(Run).count() == 0


def test_estimate_names_a_minority_of_scanned_pages(client, db, seed_simple_mode, cv_pdf):
    """#1282: below the reject share the file is accepted and /estimate
    names the scanned pages, so the New run page can warn before submit."""
    user = _make_user(db)
    _auth(client, user)
    resp = client.post(
        "/api/estimate",
        files={"file": ("cv.pdf", cv_pdf(image_pages=(2,), text_pages=2), "application/pdf")},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["scanned_pages"] == [3]


def test_upload_records_a_minority_of_scanned_pages(client, db, seed_simple_mode, cv_pdf, tmp_path):
    """#1282: an accepted PDF's scanned pages are stored on the run, for
    the run page's warning; a docx's are not (NULL, not "")."""
    user = _make_user(db)
    _auth(client, user)
    patches = [patch("app.services.run_creation.UPLOAD_DIR", tmp_path),
               patch("app.services.run_creation.get_storage", return_value=MagicMock())]
    resp = _run_patches(patches, lambda: _post_upload(
        client, "cv.pdf", cv_pdf(image_pages=(2,), text_pages=2), "application/pdf"))
    assert resp.status_code == 200, resp.text
    assert db.get(Run, resp.json()["run_id"]).scanned_pages == "3"

    patches = _bypass_file_validation(tmp_path) + [patch("app.services.run_creation.get_storage", return_value=MagicMock())]
    resp = _run_patches(patches, lambda: _post_upload(
        client, "cv.docx", b"PK\x03\x04dummy-docx-bytes", DOCX_MIME, data={"confirm_duplicate": "true"}))
    assert resp.status_code == 200, resp.text
    assert db.get(Run, resp.json()["run_id"]).scanned_pages is None


def test_page_ranges_collapses_runs():
    assert upload_module._page_ranges([2, 3, 4, 7, 9, 10]) == "2–4, 7, 9–10"


def test_upload_still_accepts_docx_after_pdf_rejection(client, db, seed_simple_mode, tmp_path):
    """#524 regression, kept through #806: changing what .pdf does must not
    disturb the .docx path."""
    user = _make_user(db)
    _auth(client, user)
    patches = _bypass_file_validation(tmp_path)
    patches.append(patch("app.services.run_creation.get_storage", return_value=MagicMock()))
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
        patch("app.services.run_creation.UPLOAD_DIR", tmp_path),
        patch("app.services.run_creation.get_storage", return_value=storage),
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


def test_validate_docx_magic_rejects_a_high_ratio_docx(monkeypatch):
    """#793: expansion is bounded before python-docx parses. A docx whose
    declared uncompressed total exceeds the cap is rejected, and one within
    it passes (cap patched small so the fixture stays tiny)."""
    import app.services.upload_validation as upload
    bomb = _docx_bytes("A" * 50_000)  # deflates to a few hundred bytes
    monkeypatch.setattr(upload, "_DOCX_MAX_UNCOMPRESSED_BYTES", 40_000)
    assert len(bomb) < 40_000
    assert _validate_docx_magic(bomb) is False
    monkeypatch.setattr(upload, "_DOCX_MAX_UNCOMPRESSED_BYTES", 10_000_000)
    assert _validate_docx_magic(bomb) is True


def test_validate_docx_magic_rejects_too_many_entries(monkeypatch):
    """#793: the entry-count bound, at and past the cap."""
    import app.services.upload_validation as upload
    content = _docx_bytes("hello")
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        n = len(z.infolist())
    monkeypatch.setattr(upload, "_DOCX_MAX_ENTRIES", n)
    assert _validate_docx_magic(content) is True
    monkeypatch.setattr(upload, "_DOCX_MAX_ENTRIES", n - 1)
    assert _validate_docx_magic(content) is False


# --- #1334: macros, network-linked parts and DDE fields are refused ----------

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_W_STRICT_NS = "http://purl.oclc.org/ooxml/wordprocessingml/main"
_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/"
_SETTINGS_RELS = "word/_rels/settings.xml.rels"


def _docx_with_parts(parts: dict[str, str | bytes]) -> bytes:
    """A real python-docx file with ``parts`` added, or replacing the part of
    the same name."""
    out = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(_docx_bytes("Synthetic CV paragraph. " * 40))) as src, \
            zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as dst:
        for entry in src.infolist():
            if entry.filename not in parts:
                dst.writestr(entry, src.read(entry))
        for name, body in parts.items():
            dst.writestr(name, body)
    return out.getvalue()


def _rels(rel_type: str, target: str, mode: str = "External") -> str:
    return (
        "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
        f"<Relationship Id='rId1' Type='{_REL_TYPE}{rel_type}' Target='{target}' TargetMode='{mode}'/>"
        "</Relationships>"
    )


def _document(body: str) -> str:
    return f"<w:document xmlns:w='{_W_NS}'><w:body><w:p>{body}</w:p></w:body></w:document>"


def _complex_field(*instr_runs: str) -> str:
    runs = "".join(f"<w:r><w:instrText>{text}</w:instrText></w:r>" for text in instr_runs)
    return (
        "<w:r><w:fldChar w:fldCharType='begin'/></w:r>" + runs
        + "<w:r><w:fldChar w:fldCharType='separate'/></w:r><w:r><w:t>result</w:t></w:r>"
        "<w:r><w:fldChar w:fldCharType='end'/></w:r>"
    )


@pytest.mark.parametrize("label, parts, expected", [
    ("plain docx", {}, None),
    ("vbaProject.bin", {"word/vbaProject.bin": b"\x00" * 64}, upload_validation.ActiveContent.MACRO),
    ("vbaProject.bin under a backslash zip name", {"word\\vbaProject.bin": b"\x00" * 64},
     upload_validation.ActiveContent.MACRO),
    ("VBA project under another name, found by its relationship", {
        "word/foo.bin": b"\x00" * 64,
        "word/_rels/document.xml.rels": (
            "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
            "<Relationship Id='rId99' Type='http://schemas.microsoft.com/office/2006/relationships/vbaProject'"
            " Target='foo.bin'/></Relationships>"),
    }, upload_validation.ActiveContent.MACRO),
    ("https attachedTemplate", {_SETTINGS_RELS: _rels("attachedTemplate", "https://evil.example/x.dotm")},
     upload_validation.ActiveContent.EXTERNAL_LINK),
    ("UNC attachedTemplate", {_SETTINGS_RELS: _rels("attachedTemplate", "\\\\10.0.0.1\\s\\x.dotm")},
     upload_validation.ActiveContent.EXTERNAL_LINK),
    ("percent-encoded UNC", {_SETTINGS_RELS: _rels("attachedTemplate", "%5C%5Chost%5Cs%5Cx.dotm")},
     upload_validation.ActiveContent.EXTERNAL_LINK),
    ("https after leading spaces", {_SETTINGS_RELS: _rels("attachedTemplate", "  HTTPS://evil.example/x.dotm")},
     upload_validation.ActiveContent.EXTERNAL_LINK),
    ("file URL naming a host", {_SETTINGS_RELS: _rels("attachedTemplate", "file://evil.example/x.dotm")},
     upload_validation.ActiveContent.EXTERNAL_LINK),
    ("file: with a backslash UNC", {_SETTINGS_RELS: _rels("attachedTemplate", "file:\\\\evil.example\\s\\x.dotm")},
     upload_validation.ActiveContent.EXTERNAL_LINK),
    ("forward-slash UNC attachedTemplate", {_SETTINGS_RELS: _rels("attachedTemplate", "//evil.example/s/x.dotm")},
     upload_validation.ActiveContent.EXTERNAL_LINK),
    ("ftp attachedTemplate", {_SETTINGS_RELS: _rels("attachedTemplate", "FTP://evil.example/x.dotm")},
     upload_validation.ActiveContent.EXTERNAL_LINK),
    ("remote image in a header's rels", {"word/_rels/header1.xml.rels": _rels("image", "https://evil.example/x.png")},
     upload_validation.ActiveContent.EXTERNAL_LINK),
    ("upper-case rels part name", {"word/_rels/settings.xml.RELS": _rels("attachedTemplate", "https://e.example/x")},
     upload_validation.ActiveContent.EXTERNAL_LINK),
    ("file:/// attachedTemplate", {_SETTINGS_RELS: _rels("attachedTemplate", "file:///C:/x.dotm")}, None),
    ("file://localhost attachedTemplate", {_SETTINGS_RELS: _rels("attachedTemplate", "file://localhost/x.dotm")},
     None),
    ("file://LocalHost attachedTemplate", {_SETTINGS_RELS: _rels("attachedTemplate", "file://LocalHost/x.dotm")},
     None),
    ("Macintosh HD attachedTemplate", {_SETTINGS_RELS: _rels("attachedTemplate", "Macintosh%20HD:Users:x.dotx")},
     None),
    ("mhtml: nesting a URL (CVE-2021-40444 shape)", {"word/_rels/document.xml.rels": _rels(
        "oleObject", "mhtml:http://evil.example/x.html!x-usc:http://evil.example/x.html")},
     upload_validation.ActiveContent.EXTERNAL_LINK),
    ("smb attachedTemplate", {_SETTINGS_RELS: _rels("attachedTemplate", "smb://evil.example/s/x.dotm")},
     upload_validation.ActiveContent.EXTERNAL_LINK),
    ("Mac HFS volume without a space", {_SETTINGS_RELS: _rels("attachedTemplate", "Data:Users:x.dotx")}, None),
    ("Windows drive with forward slashes", {_SETTINGS_RELS: _rels("attachedTemplate", "C://x.dotm")}, None),
    ("Mac Word file://// attachedTemplate", {_SETTINGS_RELS: _rels("attachedTemplate", "file:////Users/x.dotx")},
     None),
    ("internal https-looking target", {_SETTINGS_RELS: _rels("attachedTemplate", "https://x/y.dotm", "Internal")},
     None),
    ("external https hyperlink", {"word/_rels/document.xml.rels": _rels("hyperlink", "https://example.org/")},
     None),
    ("fldSimple DDEAUTO", {"word/document.xml": _document("<w:fldSimple w:instr=' DDEAUTO c:\\\\x \"y\"'/>")},
     upload_validation.ActiveContent.DDE),
    ("complex DDE split across two instrText runs",
     {"word/document.xml": _document(_complex_field(" DD", "E c:\\\\x y"))}, upload_validation.ActiveContent.DDE),
    ("lowercase ddeauto in a footer", {"word/footer1.xml": _document(_complex_field("ddeauto x y"))},
     upload_validation.ActiveContent.DDE),
    ("DDE nested inside an IF field", {"word/document.xml": _document(
        "<w:r><w:fldChar w:fldCharType='begin'/></w:r><w:r><w:instrText>IF 1 = 1 </w:instrText></w:r>"
        + _complex_field("DDEAUTO x y") + "<w:r><w:fldChar w:fldCharType='end'/></w:r>")},
     upload_validation.ActiveContent.DDE),
    ("DDE after a complete nested field", {"word/document.xml": _document(
        "<w:r><w:fldChar w:fldCharType='begin'/></w:r>" + _complex_field("PAGE")
        + "<w:r><w:instrText>DDEAUTO x y</w:instrText></w:r><w:r><w:fldChar w:fldCharType='end'/></w:r>")},
     upload_validation.ActiveContent.DDE),
    ("unterminated DDE field", {"word/document.xml": _document(
        "<w:r><w:fldChar w:fldCharType='begin'/></w:r><w:r><w:instrText>DDE x y</w:instrText></w:r>")},
     upload_validation.ActiveContent.DDE),
    ("DDE only after the field's separate", {"word/document.xml": _document(
        "<w:r><w:fldChar w:fldCharType='begin'/></w:r><w:r><w:instrText>REF x</w:instrText></w:r>"
        "<w:r><w:fldChar w:fldCharType='separate'/></w:r><w:r><w:instrText>DDE x y</w:instrText></w:r>"
        "<w:r><w:fldChar w:fldCharType='end'/></w:r>")}, None),
    ("DDE only after an empty field's separate", {"word/document.xml": _document(
        "<w:r><w:fldChar w:fldCharType='begin'/></w:r><w:r><w:fldChar w:fldCharType='separate'/></w:r>"
        "<w:r><w:instrText>DDEAUTO x y</w:instrText></w:r><w:r><w:fldChar w:fldCharType='end'/></w:r>")}, None),
    ("DDE in an upper-case part name", {"WORD/FOOTER9.XML": _document("<w:fldSimple w:instr='DDEAUTO x y'/>")},
     upload_validation.ActiveContent.DDE),
    ("strict-OOXML fldSimple DDEAUTO", {"word/footer8.xml": _document("<w:fldSimple w:instr='DDEAUTO x y'/>").replace(
        _W_NS, _W_STRICT_NS)}, upload_validation.ActiveContent.DDE),
    ("strict-OOXML complex DDE", {"word/footer7.xml": _document(_complex_field("DDE x y")).replace(
        _W_NS, _W_STRICT_NS)}, upload_validation.ActiveContent.DDE),
    ("a non-DDE field", {"word/document.xml": _document(_complex_field("PAGE"))}, None),
    ("body text DDE", {"word/document.xml": _document("<w:r><w:t>DDE DDEAUTO lab</w:t></w:r>")}, None),
    ("ActiveX part", {
        "word/activeX/activeX1.xml": "<ax:ocx xmlns:ax='http://schemas.microsoft.com/office/2006/activeX'/>",
        "word/activeX/activeX1.bin": b"\x01" * 64,
        "word/activeX/_rels/activeX1.xml.rels": _rels("activeXControlBinary", "activeX1.bin", "Internal"),
    }, None),
])
def test_docx_active_content(label, parts, expected):
    """#1334: each refused kind, and the real-CV shapes the corpus census says
    must pass (local template paths, hyperlinks, ActiveX, body text "DDE")."""
    content = _docx_with_parts(parts)
    assert _validate_docx_magic(content) is True, label  # the scan runs only after this passes
    assert upload_validation.docx_active_content(content) == expected, label


def test_docx_active_content_logs_the_category_and_no_filename(caplog):
    with caplog.at_level(logging.WARNING, logger="app.services.upload_validation"):
        upload_validation.docx_active_content(_docx_with_parts({"word/vbaProject.bin": b"\x00"}))
    assert [r.getMessage() for r in caplog.records] == ["[SECURITY] Refused docx carrying active content: macro"]


def test_docx_active_content_still_scans_past_an_unreadable_part(caplog):
    """Malformed XML is not a refusal by itself (python-docx's read handles
    it), but it does not hide a network template in another part."""
    content = _docx_with_parts({
        "word/_rels/document.xml.rels": "<<<not xml",
        _SETTINGS_RELS: _rels("attachedTemplate", "https://evil.example/x.dotm"),
    })
    with caplog.at_level(logging.WARNING, logger="app.services.upload_validation"):
        assert upload_validation.docx_active_content(content) == upload_validation.ActiveContent.EXTERNAL_LINK
    skipped = [r for r in caplog.records if "skipped an unreadable part" in r.getMessage()]
    assert len(skipped) == 1 and skipped[0].exc_info is not None
    assert upload_validation.docx_active_content(_docx_with_parts({"word/document.xml": "<<<not xml"})) is None


@pytest.mark.parametrize("label, mark", [
    ("encrypted", lambda info: setattr(info, "flag_bits", info.flag_bits | 0x1)),
    ("unsupported compression", lambda info: setattr(info, "compress_type", 99)),
])
def test_docx_active_content_skips_an_orphan_part_zipfile_cannot_read(label, mark):
    """An orphan part python-docx never reads, but zipfile refuses to (it raises
    RuntimeError / NotImplementedError), is skipped like any unreadable part,
    never a 500."""
    out = io.BytesIO(_docx_with_parts({}))
    with zipfile.ZipFile(out, "a") as zf:
        zf.writestr("word/orphan.xml", _document("<w:fldSimple w:instr='PAGE'/>"))
        mark(zf.filelist[-1])  # rewrites the central directory entry on close
    content = out.getvalue()
    assert _validate_docx_magic(content) is True, label
    assert upload_validation.docx_active_content(content) is None, label


def test_docx_active_content_resolves_no_entities(tmp_path):
    """The parser never resolves an entity (no XXE): a field whose instruction
    would read DDEAUTO only if a local file were pulled in stays unread."""
    payload = tmp_path / "payload.txt"
    payload.write_text("DDEAUTO x y")
    document = (
        f"<!DOCTYPE w:document [<!ENTITY t SYSTEM '{payload.as_uri()}'>]>"
        + _document("<w:r><w:fldChar w:fldCharType='begin'/></w:r><w:r><w:instrText>&t;</w:instrText></w:r>"
                    "<w:r><w:fldChar w:fldCharType='end'/></w:r>")
    )
    assert upload_validation.docx_active_content(_docx_with_parts({"word/document.xml": document})) is None


@pytest.mark.parametrize("endpoint, data", [
    ("/api/upload", {"submission_type": "own_cv"}),
    ("/api/estimate", None),
])
def test_upload_and_estimate_refuse_a_docx_with_a_macro(client, db, seed_simple_mode, tmp_path, endpoint, data):
    """#1334: a distinct 400, not the generic "does not match .docx", and no run."""
    user = _make_user(db)
    _auth(client, user)
    storage = MagicMock()
    patches = [
        patch("app.services.run_creation.UPLOAD_DIR", tmp_path),
        patch("app.services.run_creation.get_storage", return_value=storage),
    ]
    content = _docx_with_parts({"word/vbaProject.bin": b"\x00" * 64})
    resp = _run_patches(patches, lambda: client.post(
        endpoint, files={"file": ("cv.docx", content, DOCX_MIME)}, data=data,
    ))

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == {"error": "bad_request", "message": upload_validation.ACTIVE_CONTENT_MESSAGE}
    storage.put_file_exclusive.assert_not_called()
    assert db.query(Run).count() == 0
    assert list(tmp_path.iterdir()) == []


# --- #865 review: _extract_text catches only the measured read failures ------

def _zip_bytes(**members: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in members.items():
            z.writestr(name.replace("__", "/"), body)
    return buf.getvalue()


_CONTENT_TYPES = (
    "<Types xmlns='http://schemas.openxmlformats.org/package/2006/content-types'>"
    "<Override PartName='/word/document.xml' ContentType='application/vnd.openxmlformats"
    "-officedocument.wordprocessingml.document.main+xml'/></Types>"
)
_RELS = (
    "<Relationships xmlns='http://schemas.openxmlformats.org/package/2006/relationships'>"
    "<Relationship Id='rId1' Type='http://schemas.openxmlformats.org/officeDocument/2006/"
    "relationships/officeDocument' Target='word/document.xml'/></Relationships>"
)


def _corrupt_deflate_docx() -> bytes:
    """A docx whose zip directory is intact but whose word/document.xml
    deflate stream is not: python-docx gets past the zip layer and zlib
    raises while inflating the part."""
    raw = bytearray(_docx_bytes("synthetic paragraph " * 200))
    with zipfile.ZipFile(io.BytesIO(bytes(raw))) as z:
        info = z.getinfo("word/document.xml")
    name_len, extra_len = int.from_bytes(raw[info.header_offset + 26:info.header_offset + 28], "little"), \
        int.from_bytes(raw[info.header_offset + 28:info.header_offset + 30], "little")
    start = info.header_offset + 30 + name_len + extra_len
    for i in range(start + 2, start + 2 + 35):
        raw[i] ^= 0xFF
    return bytes(raw)


@pytest.mark.parametrize("label, content", [
    ("not a zip at all -> PackageNotFoundError", b"PK\x03\x04 but not really a zip"),
    ("truncated docx -> PackageNotFoundError", _docx_bytes("hello")[:1200]),
    ("zip with no parts -> KeyError", _zip_bytes()),
    ("malformed document.xml -> XMLSyntaxError",
     _zip_bytes(**{"[Content_Types].xml": _CONTENT_TYPES, "_rels__.rels": _RELS,
                   "word__document.xml": "<<<not xml"})),
    ("corrupt deflate stream in document.xml -> zlib.error", _corrupt_deflate_docx()),
])
def test_extract_text_returns_none_and_logs_a_traceback_for_a_known_read_failure(label, content, caplog):
    """PR #865 review thread on upload.py's ``except Exception`` (r4034042444):
    the catch is now the measured ``_DOCX_READ_ERRORS`` tuple. Each shape a
    corrupt zip-shaped upload can take maps to None ("cannot determine", the
    guard is skipped) and leaves a WARNING that carries the traceback."""
    with caplog.at_level(logging.WARNING, logger="app.api.upload"):
        assert _extract_text(content, ".docx") is None, label
    records = [r for r in caplog.records if "empty-doc guard failed" in r.getMessage()]
    assert len(records) == 1, label
    assert records[0].exc_info is not None, label


def test_extract_text_lets_an_unexpected_error_surface():
    """The other half of narrowing the catch: an exception that is NOT a known
    read failure is a bug in the reader, and it propagates instead of being
    swallowed into a fail-open None (§5.4). Widening the tuple back to
    ``Exception`` fails this test."""
    with patch("app.services.upload_validation.Document", side_effect=RuntimeError("reader bug")):
        with pytest.raises(RuntimeError):
            _extract_text(_docx_bytes("hello"), ".docx")


def test_extract_text_returns_none_for_unrecognized_extension():
    """_extract_text names only .docx and .pdf. Any other extension falls
    through to the final ``return None`` with no attempt at extraction --
    "cannot determine", not "read and found nothing." (A corrupt PDF is not
    this case: it raises UnreadablePdfError, #806.)"""
    assert _extract_text(b"PK\x03\x04 an old binary .doc", ".doc") is None


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
        patch("app.services.run_creation.UPLOAD_DIR", tmp_path),
        patch("app.services.run_creation._validate_docx_magic", return_value=True),
        patch("app.services.run_creation._extract_text", return_value=""),
        patch("app.services.run_creation.get_storage", return_value=storage),
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
        patch("app.services.run_creation.UPLOAD_DIR", tmp_path),
        patch("app.services.run_creation._validate_docx_magic", return_value=True),
        patch("app.services.run_creation._extract_text", return_value=extracted),
        patch("app.services.run_creation.detect_wcm_template", detect),
        patch("app.services.run_creation.get_storage", return_value=MagicMock()),
    ]
    resp = _run_patches(patches, lambda: _post_dummy_upload(client))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["wcm_template_warning"] is warning
    assert body["wcm_template_match_ratio"] == ratio
    detect.assert_called_once()
    assert detect.call_args.args[0] == extracted  # reuses the extracted text, no re-parse
    assert db.query(Run).filter(Run.id == body["run_id"]).first().status == "created"


# --- input format (WCM template vs other) recorded on the run -----------------

# Synthetic text: the template's own section headings, nothing from a real CV.
_WCM_TEMPLATE_TEXT = "\n".join([
    "PERSONAL DATA", "EMPLOYMENT STATUS", "INSTITUTIONAL/HOSPITAL AFFILIATION",
    "LICENSURE, BOARD CERTIFICATION", "PERCENT EFFORT AND INSTITUTIONAL RESPONSIBILITIES",
    "EDUCATIONAL CONTRIBUTIONS", "CLINICAL PRACTICE, INNOVATION, and LEADERSHIP",
    "INSTITUTIONAL LEADERSHIP ACTIVITIES", "EXTRAMURAL PROFESSIONAL RESPONSIBILITIES",
    "INVITATIONS TO SPEAK/PRESENT", "Synthetic filler line so the text is long enough. " * 12,
])


def _upload_with_text(client, db, tmp_path, extracted, input_format_patch=None):
    user = _make_user(db)
    _auth(client, user)
    patches = [
        patch("app.services.run_creation.UPLOAD_DIR", tmp_path),
        patch("app.services.run_creation._validate_docx_magic", return_value=True),
        patch("app.services.run_creation._extract_text", return_value=extracted),
        patch("app.services.run_creation.detect_wcm_template", return_value=(False, None)),
        patch("app.services.run_creation.get_storage", return_value=MagicMock()),
    ]
    if input_format_patch is not None:
        patches.append(input_format_patch)
    resp = _run_patches(patches, lambda: _post_dummy_upload(client))
    assert resp.status_code == 200, resp.text
    return db.query(Run).filter(Run.id == resp.json()["run_id"]).one()


@pytest.mark.parametrize("text, fmt", [(_WCM_TEMPLATE_TEXT, "wcm"), ("Plain CV text. " * 60, "other")],
                         ids=["wcm", "other"])
def test_upload_records_input_format_on_the_run(client, db, seed_simple_mode, tmp_path, text, fmt):
    run = _upload_with_text(client, db, tmp_path, text)
    assert run.input_format == fmt
    assert run.input_format_score is not None


def test_upload_survives_an_input_format_detector_failure(client, db, seed_simple_mode, tmp_path, caplog):
    boom = patch("app.services.input_format.detect_input_format", side_effect=RuntimeError("boom"))
    with caplog.at_level("WARNING", logger="app.services.input_format"):
        run = _upload_with_text(client, db, tmp_path, _WCM_TEMPLATE_TEXT, boom)
    assert run.input_format is None and run.input_format_score is None
    assert any("Input-format detection failed" in r.message for r in caplog.records)


# --- #1286: ask before re-processing a file already run -----------------------

_DUP_BYTES = b"PK\x03\x04synthetic-duplicate-bytes"
_DUP_SHA = hashlib.sha256(_DUP_BYTES).hexdigest()


def _seed_prior_run(db, owner, run_id="P00001", sha=_DUP_SHA, started=datetime(2026, 3, 4, 10, 0)):
    db.add(Run(id=run_id, filename="old.docx", file_type="docx", status="complete",
               user_id=owner.id, started_at=started, source_sha256=sha))
    db.commit()


def _dup_upload(client, tmp_path, data=None):
    """Upload the duplicate bytes with a real LocalRunStorage; returns (response, storage)."""
    root = tmp_path / uuid.uuid4().hex
    root.mkdir()
    storage, _, patches = _real_storage_patches(root)
    resp = _run_patches(patches, lambda: _post_upload(client, "cv.docx", _DUP_BYTES, DOCX_MIME, data))
    return resp, root


def test_upload_stores_source_sha256(client, db, seed_simple_mode, tmp_path):
    _auth(client, _make_user(db))
    resp, _ = _dup_upload(client, tmp_path)
    assert resp.status_code == 200, resp.text
    assert db.get(Run, resp.json()["run_id"]).source_sha256 == _DUP_SHA


def test_duplicate_upload_stops_without_creating_or_archiving(client, db, seed_simple_mode, tmp_path):
    other = _make_user(db, email="other@example.com")
    _seed_prior_run(db, other)
    _auth(client, _make_user(db))
    resp, root = _dup_upload(client, tmp_path)

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["error"] == "duplicate_file"
    assert "March 4, 2026" in detail["message"]
    assert db.query(Run).count() == 1  # only the seeded prior run
    assert [p for p in root.rglob("*") if p.is_file()] == []  # nothing archived or written locally


def test_confirm_duplicate_proceeds(client, db, seed_simple_mode, tmp_path):
    _seed_prior_run(db, _make_user(db, email="other@example.com"))
    _auth(client, _make_user(db))
    resp, _ = _dup_upload(client, tmp_path, {"confirm_duplicate": "true"})

    assert resp.status_code == 200, resp.text
    assert db.query(Run).filter(Run.source_sha256 == _DUP_SHA).count() == 2


def test_duplicate_notice_hides_other_submitters_run_from_non_admin(client, db, seed_simple_mode, tmp_path):
    other = _make_user(db, email="other@example.com")
    _seed_prior_run(db, other, run_id="ZZ9999")
    _auth(client, _make_user(db))
    resp, _ = _dup_upload(client, tmp_path)

    assert resp.status_code == 409
    assert "run_id" not in resp.json()["detail"]
    for leaked in ("ZZ9999", "other@example.com", "Test User"):
        assert leaked not in resp.text


def test_duplicate_notice_shows_run_id_to_admin_and_to_its_own_submitter(client, db, seed_simple_mode, tmp_path):
    other = _make_user(db, email="other@example.com")
    _seed_prior_run(db, other, run_id="ZZ9999")
    admin = _make_user(db, email="admin@example.com", role="admin")
    _auth(client, admin)
    assert _dup_upload(client, tmp_path)[0].json()["detail"]["run_id"] == "ZZ9999"

    me = _make_user(db, email="me@example.com")
    _seed_prior_run(db, me, run_id="MINE01", started=datetime(2026, 5, 1))
    _auth(client, me)
    assert _dup_upload(client, tmp_path)[0].json()["detail"]["run_id"] == "MINE01"


# --- #793: bounded read + off-event-loop extraction -------------------------

class _TrackedFile:
    """Minimal UploadFile stand-in that records every chunk `.read()` served,
    so a test can measure how much of the body `_read_bounded` actually
    consumed instead of only asserting the final rejection."""

    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0
        self.total_served = 0

    async def read(self, size: int) -> bytes:
        chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        self.total_served += len(chunk)
        return chunk


def test_read_bounded_returns_the_full_body_within_the_limit():
    """#793 positive case: a body under max_size is read completely and
    reassembled in order, across multiple chunks."""
    data = b"a" * 250 + b"b" * 250  # 500 bytes, 2.5 chunks at chunk_size=200
    fake = _TrackedFile(data)
    with patch("app.api.upload._UPLOAD_READ_CHUNK_SIZE", 200):
        result = asyncio.run(upload_module._read_bounded(fake, max_size=1000))
    assert result == data
    assert fake.total_served == len(data)


def test_read_bounded_aborts_without_buffering_the_whole_oversized_body():
    """#793 negative case: a body far larger than max_size is rejected after
    only a little over the cap has been read -- not fully buffered first the
    way `await file.read()` + a size check used to (#793 item 1)."""
    huge = b"x" * (10 * 1024 * 1024)  # 10 MB body
    fake = _TrackedFile(huge)
    with patch("app.api.upload._UPLOAD_READ_CHUNK_SIZE", 1024):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(upload_module._read_bounded(fake, max_size=2048))
    assert exc_info.value.status_code == 400
    assert "too large" in exc_info.value.detail["message"].lower()
    # Aborted at most one chunk past the cap -- nowhere near the full 10 MB body.
    assert fake.total_served <= 2048 + 1024


def test_read_bounded_pins_the_size_boundary():
    """#793 polish (M10): a body of exactly max_size bytes is accepted (the
    check is `total > max_size`, not `>=`); one byte over is rejected. Pins
    the boundary itself, not just "some big body is rejected"."""
    with patch("app.api.upload._UPLOAD_READ_CHUNK_SIZE", 4096):
        exactly_at_cap = asyncio.run(
            upload_module._read_bounded(_TrackedFile(b"a" * 1000), max_size=1000)
        )
        assert exactly_at_cap == b"a" * 1000

        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(
                upload_module._read_bounded(_TrackedFile(b"a" * 1001), max_size=1000)
            )
    assert exc_info.value.status_code == 400


def test_upload_offloads_extraction_and_template_checks_to_threadpool(client, db, seed_simple_mode, tmp_path):
    """#793 item 3: `_extract_text`, `detect_wcm_template` and
    `detect_input_format_or_none` are dispatched through `run_in_threadpool`, not called synchronously inside the async
    handler. Reverting either `await run_in_threadpool(fn, ...)` call back to
    a bare `fn(...)` leaves the response unchanged but this test catches it,
    since it asserts run_in_threadpool was the actual dispatch mechanism for
    both, not just that the endpoint still returns 200."""
    user = _make_user(db)
    _auth(client, user)
    extract_mock = MagicMock(return_value="x" * 600)
    detect_mock = MagicMock(return_value=(False, None))
    format_mock = MagicMock(return_value=(None, None))
    real_run_in_threadpool = upload_module.run_in_threadpool
    dispatched: list[object] = []

    async def spy(func, *args, **kwargs):
        dispatched.append(func)
        return await real_run_in_threadpool(func, *args, **kwargs)

    patches = [
        patch("app.services.run_creation.UPLOAD_DIR", tmp_path),
        patch("app.services.run_creation._validate_docx_magic", return_value=True),
        patch("app.services.run_creation._extract_text", extract_mock),
        patch("app.services.run_creation.detect_wcm_template", detect_mock),
        patch("app.services.run_creation.detect_input_format_or_none", format_mock),
        patch("app.services.run_creation.get_storage", return_value=MagicMock()),
        patch("app.services.run_creation.run_in_threadpool", spy),
    ]
    resp = _run_patches(patches, lambda: _post_dummy_upload(client))

    assert resp.status_code == 200, resp.text
    # _read_upload_text wraps _extract_text, adding the PDF scanned-page gate (#1282);
    # the #1334 active-content scan runs off the loop first.
    assert dispatched == [
        upload_validation.docx_active_content, upload_module._read_upload_text, detect_mock, format_mock,
    ]
    extract_mock.assert_called_once()


def test_estimate_offloads_extraction_to_threadpool(client, db, seed_simple_mode):
    """#793 polish (M6): /estimate's `_extract_text` call is off the event
    loop too, the same as /upload's (issue text: '/estimate is off the
    event loop, same as /upload'). A bare synchronous `_extract_text(...)`
    call would leave the response unchanged, so this asserts
    run_in_threadpool was the actual dispatch mechanism, not just that
    /estimate still returns 200."""
    user = _make_user(db)
    _auth(client, user)
    extract_mock = MagicMock(return_value="x" * 600)
    real_run_in_threadpool = upload_module.run_in_threadpool
    dispatched: list[object] = []

    async def spy(func, *args, **kwargs):
        dispatched.append(func)
        return await real_run_in_threadpool(func, *args, **kwargs)

    patches = [
        patch("app.services.run_creation._validate_docx_magic", return_value=True),
        patch("app.services.run_creation._extract_text", extract_mock),
        patch("app.services.run_creation.run_in_threadpool", spy),
    ]
    resp = _run_patches(
        patches,
        lambda: client.post(
            "/api/estimate",
            files={"file": ("cv.docx", b"PK\x03\x04dummy-docx-bytes", "application/octet-stream")},
        ),
    )

    assert resp.status_code == 200, resp.text
    assert dispatched == [upload_validation.docx_active_content, upload_module._read_upload_text]
    extract_mock.assert_called_once()


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
    patches.append(patch("app.services.run_creation.get_storage", return_value=MagicMock()))
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
    patches.append(patch("app.services.run_creation.get_storage", return_value=MagicMock()))
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
    patches.append(patch("app.services.run_creation.get_storage", return_value=MagicMock()))
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
    patches.append(patch("app.services.run_creation.get_storage", return_value=storage))
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
        patch("app.services.run_creation.UPLOAD_DIR", tmp_path),
        patch("app.services.run_creation.get_storage", return_value=MagicMock()),
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
    patches.append(patch("app.services.run_creation.get_storage", return_value=storage))
    patches.append(patch("app.services.run_creation.generate_run_id", return_value="FATAL1"))
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
    patches.append(patch("app.services.run_creation.generate_run_id", return_value="ORPHN1"))
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
    # The over-length case ("x" * 296 + ".docx", 301 chars) is no longer here:
    # #796 now rejects it with 400 before create_run_archive runs, which is
    # covered by test_upload_rejects_filename_over_column_width_before_archive
    # below -- exactly the "sanitization tracked in #796" flip this test used
    # to note.
])
def test_upload_traversal_filename_cannot_escape(client, db, seed_simple_mode, tmp_path, filename):
    """PR #779 review thread web_interface/backend/tests/test_upload_atomicity.py item 17

    Starlette hands the multipart filename over verbatim (probed on 0.52.1).
    upload.py:324 takes only its suffix and :252 names the stored file
    `<run_id>.<ext>`, so the client string never shapes a filesystem or
    storage path: the ONLY files created under tmp_path are the four the
    endpoint owns. The raw string is retained as a display name (response,
    Run.filename, manifest original_filename).
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

def test_upload_db_commit_failure_compensates_the_archive_and_creates_no_run(client, db, seed_simple_mode, tmp_path):
    """PR #779 review thread web_interface/backend/tests/test_upload_run_id_collision.py item 2

    upload.py's `db.commit()` is now guarded (#802): a DB fault there is
    caught, logged, and compensated -- the archive, the by-submitter index,
    and the pod-local copy are all deleted before a 500 is returned -- rather
    than reaching the app's catch-all handler and leaving a storage-only
    orphan #116 used to track. The archive-before-DB ordering is still
    #170's requirement; only what happens after a commit failure changed.
    """
    user = _make_user(db)
    _auth(client, user)
    storage, upload_dir, patches = _real_storage_patches(tmp_path)
    patches.append(patch("app.services.run_creation.generate_run_id", return_value="DBFA1L"))
    patches.append(patch.object(db, "commit", side_effect=OperationalError("stmt", {}, Exception("db down"))))
    resp = _run_patches(patches, lambda: _post_dummy_upload(client))

    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"] == {
        "error": "internal_error",
        "message": (
            "We couldn't finish creating your run, so nothing was saved. "
            "Please try again in a moment."
        ),
    }

    db.rollback()
    assert db.query(Run).count() == 0
    assert db.query(Step).count() == 0
    # The commit failure is now compensated: nothing archived under this run
    # id survives it, and the pod-local copy is gone too.
    assert storage.exists("DBFA1L", "input/manifest.json") is False
    assert storage.exists("DBFA1L", "input/DBFA1L.docx") is False
    assert not (tmp_path / "storage" / "by-submitter" / "test@example.com" / "DBFA1L" / "manifest.json").exists()
    assert list(upload_dir.iterdir()) == []


def test_upload_compensates_archive_when_commit_fails(client, db, seed_simple_mode, tmp_path, caplog):
    """#802 acceptance 1/2: a commit failure after a successful archive calls
    the compensating delete for the orphaned run id -- delete_run and
    delete_global_prefix for the exact by-submitter key put_global wrote --
    and unlinks the pod-local copy, before the 5xx is returned. Both the
    commit failure and (trivially, since it succeeds here) the compensation
    are logged.
    """
    user = _make_user(db)
    _auth(client, user)

    storage = MagicMock()
    storage.put_file_exclusive.return_value = None
    storage.put_global.return_value = None
    patches = _bypass_file_validation(tmp_path)
    patches.append(patch("app.services.run_creation.get_storage", return_value=storage))
    patches.append(patch("app.services.run_creation.generate_run_id", return_value="CMFAIL"))
    patches.append(patch.object(db, "commit", side_effect=OperationalError("stmt", {}, Exception("db down"))))

    # Wraps the real rollback (so the session actually rolls back) while
    # still recording the call, so we can pin BOTH that it happened and
    # that it happened before the compensating delete_run -- not just that
    # the two were called at all (r1 m07: deleting `db.rollback()` in
    # `commit_run_or_compensate` left every other assertion green).
    call_order = MagicMock()
    with patch.object(db, "rollback", wraps=db.rollback) as rollback_mock:
        call_order.attach_mock(rollback_mock, "rollback")
        call_order.attach_mock(storage.delete_run, "delete_run")
        with caplog.at_level(logging.ERROR, logger="app.api.upload"):
            resp = _run_patches(patches, lambda: _post_dummy_upload(client))
        rollback_mock.assert_called_once()

    call_names = [c[0] for c in call_order.mock_calls]
    assert call_names.index("rollback") < call_names.index("delete_run"), call_order.mock_calls

    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"]["error"] == "internal_error"

    db.rollback()
    assert db.query(Run).count() == 0
    assert db.query(Step).count() == 0

    storage.delete_run.assert_called_once_with("CMFAIL")
    storage.delete_global_prefix.assert_called_once_with("by-submitter/test@example.com/CMFAIL/")
    assert list(tmp_path.iterdir()) == []  # pod-local upload copy unlinked
    assert any("commit failed" in r.getMessage() for r in caplog.records)


def test_upload_compensation_failure_does_not_mask_commit_error(client, db, seed_simple_mode, tmp_path, caplog):
    """#802: when the compensation's OWN storage calls also raise, the
    response returned to the client is still the commit-failure 500, not a
    compensation-failure one -- and both errors are logged, instead of the
    second exception replacing or hiding the first. The 500 no longer claims
    "nothing was saved": the archive survived, so the message says the file
    was stored, and the log names the run whose archive is left behind."""
    user = _make_user(db)
    _auth(client, user)

    storage = MagicMock()
    storage.put_file_exclusive.return_value = None
    storage.put_global.return_value = None
    storage.delete_run.side_effect = Exception("delete_run also failed")
    storage.delete_global_prefix.side_effect = Exception("delete_global_prefix also failed")
    patches = _bypass_file_validation(tmp_path)
    patches.append(patch("app.services.run_creation.get_storage", return_value=storage))
    patches.append(patch("app.services.run_creation.generate_run_id", return_value="CMFAI2"))
    patches.append(patch.object(db, "commit", side_effect=OperationalError("stmt", {}, Exception("db down"))))
    with caplog.at_level(logging.ERROR, logger="app.api.upload"):
        resp = _run_patches(patches, lambda: _post_dummy_upload(client))

    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"] == {
        "error": "internal_error",
        "message": (
            "We couldn't finish creating your run. Your file was stored, but no run "
            "was created for it. Please try again in a moment."
        ),
    }

    db.rollback()
    assert db.query(Run).count() == 0

    # Both compensation calls were attempted despite the first raising.
    storage.delete_run.assert_called_once_with("CMFAI2")
    storage.delete_global_prefix.assert_called_once_with("by-submitter/test@example.com/CMFAI2/")
    messages = [r.getMessage() for r in caplog.records]
    assert any("commit failed" in m for m in messages)
    assert any("delete_run failed" in m for m in messages)
    assert any("delete_global_prefix failed" in m for m in messages)
    assert any("Compensation incomplete: run CMFAI2" in m for m in messages)


@pytest.mark.parametrize("failing_delete", ["delete_run", "delete_global_prefix"])
def test_upload_partial_compensation_does_not_claim_nothing_was_saved(client, db, seed_simple_mode, tmp_path, failing_delete):
    """#802: only one of the two compensating deletes fails (the live role
    lacked s3:DeleteObject, #1216). Something this request archived is still
    stored -- the run's input or the by-submitter manifest -- so the 500 must
    not say "nothing was saved"."""
    user = _make_user(db)
    _auth(client, user)

    storage = MagicMock()
    getattr(storage, failing_delete).side_effect = Exception("AccessDenied")
    patches = _bypass_file_validation(tmp_path)
    patches.append(patch("app.services.run_creation.get_storage", return_value=storage))
    patches.append(patch.object(db, "commit", side_effect=OperationalError("stmt", {}, Exception("db down"))))
    resp = _run_patches(patches, lambda: _post_dummy_upload(client))

    assert resp.status_code == 500, resp.text
    assert "nothing was saved" not in resp.json()["detail"]["message"]
    storage.delete_run.assert_called_once()
    storage.delete_global_prefix.assert_called_once()


def test_upload_compensates_when_rollback_also_fails(client, db, seed_simple_mode, tmp_path, caplog):
    """#802 item 1: a dead connection can make db.rollback() raise after the
    commit failed. That must not skip the compensation: the archive is still
    deleted, the failed rollback is logged, and the client gets the clean
    500 rather than a bare one from the rollback's exception."""
    user = _make_user(db)
    _auth(client, user)
    storage, upload_dir, patches = _real_storage_patches(tmp_path)
    patches.append(patch("app.services.run_creation.generate_run_id", return_value="RBFAIL"))
    patches.append(patch.object(db, "commit", side_effect=OperationalError("stmt", {}, Exception("db down"))))
    patches.append(patch.object(db, "rollback", side_effect=OperationalError("stmt", {}, Exception("conn gone"))))
    with caplog.at_level(logging.ERROR):
        resp = _run_patches(patches, lambda: _post_dummy_upload(client))

    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"]["message"] == RUN_NOT_CREATED_NOTHING_SAVED
    assert storage.exists("RBFAIL", "input/manifest.json") is False
    assert storage.exists("RBFAIL", "input/RBFAIL.docx") is False
    assert not (tmp_path / "storage" / "by-submitter" / "test@example.com" / "RBFAIL").exists()
    assert list(upload_dir.iterdir()) == []
    assert any("rollback failed" in r.getMessage() for r in caplog.records)


def test_upload_compensates_failure_before_the_commit(client, db, seed_simple_mode, tmp_path):
    """#802 item 1: an exception after the archive but before db.commit()
    (here the duration estimate) is inside the compensated section too: no
    run row, nothing left under the run id, and a clean 500."""
    user = _make_user(db)
    _auth(client, user)
    storage, upload_dir, patches = _real_storage_patches(tmp_path)
    patches.append(patch("app.services.run_creation.generate_run_id", return_value="PREFAI"))
    patches.append(patch("app.services.run_creation.estimate_run_seconds", side_effect=ValueError("bad estimate")))
    resp = _run_patches(patches, lambda: _post_dummy_upload(client))

    assert resp.status_code == 500, resp.text
    assert resp.json()["detail"]["message"] == RUN_NOT_CREATED_NOTHING_SAVED
    assert db.query(Run).count() == 0
    assert storage.exists("PREFAI", "input/manifest.json") is False
    assert not (tmp_path / "storage" / "by-submitter" / "test@example.com" / "PREFAI").exists()
    assert list(upload_dir.iterdir()) == []


# --- #796: filename bound BEFORE the archive --------------------------------

def test_upload_rejects_filename_over_column_width_before_archive(client, db, seed_simple_mode, tmp_path):
    """#796 acceptance: a filename longer than Run.filename's column width
    (String(255)) is rejected with 400 BEFORE create_run_archive runs -- no
    storage write, no Run row, no pod-local file. Content validation is
    bypassed (as the other atomicity tests do) so the 400 is provably from
    the length guard, not a magic-byte mismatch on the dummy payload."""
    user = _make_user(db)
    _auth(client, user)

    storage = MagicMock()
    patches = _bypass_file_validation(tmp_path)
    patches.append(patch("app.services.run_creation.get_storage", return_value=storage))
    filename = "x" * 256 + ".docx"  # 261 chars; Run.filename is String(255)
    resp = _run_patches(
        patches,
        lambda: client.post(
            "/api/upload",
            files={"file": (filename, b"PK\x03\x04dummy-docx-bytes", DOCX_MIME)},
            data={"submission_type": "own_cv"},
        ),
    )

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["error"] == "bad_request"
    storage.put_file_exclusive.assert_not_called()
    assert db.query(Run).count() == 0
    assert list(tmp_path.iterdir()) == []


def test_upload_accepts_filename_at_exactly_column_width(client, db, seed_simple_mode, tmp_path):
    """Negative control for an off-by-one: exactly 255 characters (the
    column width itself) is accepted, not rejected."""
    user = _make_user(db)
    _auth(client, user)
    storage, upload_dir, patches = _real_storage_patches(tmp_path)

    filename = "x" * 250 + ".docx"  # exactly 255 chars
    assert len(filename) == 255
    content = b"PK\x03\x04dummy-docx-bytes"
    resp = _run_patches(
        patches,
        lambda: client.post(
            "/api/upload",
            files={"file": (filename, content, DOCX_MIME)},
            data={"submission_type": "own_cv"},
        ),
    )

    assert resp.status_code == 200, resp.text
    run_id = resp.json()["run_id"]
    assert db.get(Run, run_id).filename == filename


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
    patches.append(patch("app.services.run_creation.get_storage", return_value=storage))
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


# --- #1114: batch upload ------------------------------------------------------

def _add_batch(db, owner, batch_id="BATCHA"):
    from app.models import RunBatch
    db.add(RunBatch(id=batch_id, user_id=owner.id, files_submitted=2))
    db.commit()


def test_upload_with_the_callers_batch_id_puts_the_run_in_the_batch(client, db, seed_simple_mode, tmp_path):
    user = _make_user(db)
    _add_batch(db, user)
    _auth(client, user)
    patches = _bypass_file_validation(tmp_path)
    patches.append(patch("app.services.run_creation.get_storage", return_value=MagicMock()))

    resp = _run_patches(patches, lambda: _post_upload(client, "cv.docx", b"PK\x03\x04dummy", DOCX_MIME,
                                                      data={"batch_id": "BATCHA"}))

    assert resp.status_code == 200, resp.text
    assert db.get(Run, resp.json()["run_id"]).batch_id == "BATCHA"


def test_upload_without_a_batch_id_is_a_single_run(client, db, seed_simple_mode, tmp_path):
    user = _make_user(db)
    _auth(client, user)
    patches = _bypass_file_validation(tmp_path)
    patches.append(patch("app.services.run_creation.get_storage", return_value=MagicMock()))

    resp = _run_patches(patches, lambda: _post_dummy_upload(client))

    assert resp.status_code == 200, resp.text
    assert db.get(Run, resp.json()["run_id"]).batch_id is None


@pytest.mark.parametrize("batch_owner", ["nobody", "someone else", "admin"])
def test_upload_into_a_batch_the_caller_does_not_own_is_refused_before_storage(
    client, db, seed_simple_mode, tmp_path, batch_owner,
):
    """A batch_id must name one of the caller's own batches. Another user's
    batch answers the same 404 as one that doesn't exist, as GET
    /batches/{id} does, so /upload discloses no batch's existence (the caller
    is an admin in the last arm -- admins see every batch, but only the
    submitter adds runs)."""
    uploader = _make_user(db, role="admin" if batch_owner == "admin" else "user")
    if batch_owner != "nobody":
        _add_batch(db, _make_user(db, email="sam@example.com"))
    _auth(client, uploader)
    storage = MagicMock()
    patches = _bypass_file_validation(tmp_path)
    patches.append(patch("app.services.run_creation.get_storage", return_value=storage))

    resp = _run_patches(patches, lambda: _post_upload(client, "cv.docx", b"PK\x03\x04dummy", DOCX_MIME,
                                                      data={"batch_id": "BATCHA"}))

    assert resp.status_code == 404, resp.text
    assert resp.json()["detail"] == {"error": "not_found", "message": "Batch not found"}
    storage.put_file_exclusive.assert_not_called()
    assert db.query(Run).count() == 0


def _post_estimates(client, *names):
    return client.post(
        "/api/estimate",
        files=[("files", (name, b"PK\x03\x04dummy-docx-bytes", "application/octet-stream")) for name in names],
    )


@pytest.mark.parametrize("role, cost_visible", [("admin", True), ("user", False)])
def test_estimate_many_files_returns_a_row_per_file_and_totals(client, db, seed_simple_mode, role, cost_visible):
    user = _make_user(db, role=role)
    _auth(client, user)
    patches = [
        patch("app.services.run_creation._validate_docx_magic", return_value=True),
        patch("app.services.run_creation._extract_text", return_value="x" * 4000),
    ]

    resp = _run_patches(patches, lambda: _post_estimates(client, "a.docx", "b.docx", "c.docx"))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [row["filename"] for row in body["files"]] == ["a.docx", "b.docx", "c.docx"]
    assert all(row["error"] is None for row in body["files"])
    one = body["files"][0]["estimate"]
    assert body["estimated_time_seconds_min"] == 3 * one["estimated_time_seconds_min"]
    assert body["estimated_time_seconds_max"] == 3 * one["estimated_time_seconds_max"]
    if cost_visible:
        assert body["estimated_cost_min"] == pytest.approx(3 * one["estimated_cost_min"])
        assert body["pricing_model"]
    else:
        assert (body["estimated_cost_min"], body["estimated_cost_max"], one["estimated_cost_min"]) == (None, None, None)
        assert body["pricing_model"] is None


def test_estimate_many_files_reports_a_bad_file_on_its_row_and_totals_the_rest(client, db, seed_simple_mode):
    user = _make_user(db)
    _auth(client, user)
    patches = [
        patch("app.services.run_creation._validate_docx_magic", return_value=True),
        patch("app.services.run_creation._extract_text", return_value="x" * 4000),
    ]

    resp = _run_patches(patches, lambda: _post_estimates(client, "a.docx", "notes.txt"))

    assert resp.status_code == 200, resp.text
    rows = resp.json()["files"]
    assert rows[1]["estimate"] is None
    assert rows[1]["error"].startswith("Unsupported file type: .txt.")
    assert resp.json()["estimated_time_seconds_min"] == rows[0]["estimate"]["estimated_time_seconds_min"]


def test_estimate_many_files_counts_as_one_call_against_the_estimate_limit(client, db, seed_simple_mode):
    user = _make_user(db)
    _auth(client, user)
    one_call = upload_module._EstimatePerUserWindow(max_calls=1, window_seconds=300)
    patches = [
        patch("app.api.upload._estimate_rate_limiter", one_call),
        patch("app.services.run_creation._validate_docx_magic", return_value=True),
        patch("app.services.run_creation._extract_text", return_value="x" * 4000),
    ]

    def two_calls():
        names = [f"cv{n}.docx" for n in range(upload_module.MAX_BATCH_FILES)]
        return _post_estimates(client, *names), _post_estimates(client, "again.docx")

    batch_resp, next_resp = _run_patches(patches, two_calls)

    assert batch_resp.status_code == 200, batch_resp.text
    assert len(batch_resp.json()["files"]) == upload_module.MAX_BATCH_FILES
    assert next_resp.status_code == 429


def test_estimate_refuses_more_than_fifty_files_or_both_shapes_at_once(client, db, seed_simple_mode):
    user = _make_user(db)
    _auth(client, user)
    names = [f"cv{n}.docx" for n in range(upload_module.MAX_BATCH_FILES + 1)]

    too_many = _post_estimates(client, *names)
    both = client.post("/api/estimate", files=[
        ("file", ("a.docx", b"PK", "application/octet-stream")),
        ("files", ("b.docx", b"PK", "application/octet-stream")),
    ])
    neither = client.post("/api/estimate", data={"unrelated": "x"})

    assert too_many.status_code == 400
    assert both.status_code == 400
    assert neither.status_code == 400
