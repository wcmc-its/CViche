"""The original uploaded CV is retained in durable storage (input/<run>.<ext>)
but was only ever read internally. GET /api/run/<id>/input hands it back to the
run owner/admin, named as it was uploaded.
"""
from urllib.parse import quote

import app.api.steps as steps_mod
from app.models import User, Run
from app.auth import create_session_cookie, COOKIE_NAME
from sqlalchemy.orm import object_session

_ORIGINAL = "Eulho Jung_CV_JUN_2026_USU.docx"


def _user_and_run(db, role="user", suffix="", filename=_ORIGINAL, file_type="docx"):
    user = User(email=f"in{suffix}@example.com", display_name="T", role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    run = Run(id=f"run-input{suffix or '-1'}", user_id=user.id, status="completed",
              filename=filename, file_type=file_type)
    db.add(run)
    db.commit()
    return user, run


def _auth(client, user):
    # create_session_cookie reads the current epoch from a DB session;
    # `user` was just committed on the test's session, so borrow that one.
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, object_session(user)))


class _LocalStorage:
    """No presigned URLs (like local_storage) -> route proxies the bytes."""

    def __init__(self, files):
        self.files = files

    def exists(self, run_id, key):
        return key in self.files

    def get_download_url(self, run_id, key, download_name=None):
        return None

    def get_file(self, run_id, key):
        return self.files[key]


class _S3Storage:
    """Returns a presigned URL and records the download_name it was asked for."""

    def __init__(self, files):
        self.files = files
        self.asked_name = None

    def exists(self, run_id, key):
        return key in self.files

    def get_download_url(self, run_id, key, download_name=None):
        self.asked_name = download_name
        return f"https://s3.example/{run_id}/{key}?sig=abc"

    def get_file(self, run_id, key):  # pragma: no cover - redirect path skips this
        raise AssertionError("should not proxy bytes when a presigned URL exists")


def test_owner_downloads_original_with_its_upload_name(client, db, seed_simple_mode, monkeypatch):
    user, run = _user_and_run(db, suffix="-ok")
    _auth(client, user)
    monkeypatch.setattr(steps_mod, "get_storage",
                        lambda: _LocalStorage({f"input/{run.id}.docx": b"PK\x03\x04 docx bytes"}))

    resp = client.get(f"/api/run/{run.id}/input")
    assert resp.status_code == 200, resp.text
    assert resp.content == b"PK\x03\x04 docx bytes"
    # Named as uploaded, not as the S3 uid, and the wordprocessing mime type.
    assert resp.headers["content-disposition"] == (
        "attachment; filename*=utf-8''" + quote(_ORIGINAL, safe=""))
    assert "wordprocessingml" in resp.headers["content-type"]


def test_pdf_run_downloads_the_original_pdf(client, db, seed_simple_mode, monkeypatch):
    """#806: a PDF run's original is the PDF itself (the converted docx is a
    pipeline-internal copy), served from input/<run>.pdf as application/pdf."""
    user, run = _user_and_run(db, suffix="-pdf", filename="my cv.pdf", file_type="pdf")
    _auth(client, user)
    monkeypatch.setattr(steps_mod, "get_storage",
                        lambda: _LocalStorage({f"input/{run.id}.pdf": b"%PDF-1.4 original bytes"}))

    resp = client.get(f"/api/run/{run.id}/input")
    assert resp.status_code == 200, resp.text
    assert resp.content == b"%PDF-1.4 original bytes"
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["content-disposition"] == (
        "attachment; filename*=utf-8''" + quote("my cv.pdf", safe=""))


def test_s3_path_redirects_and_renames_via_presigned_url(client, db, seed_simple_mode, monkeypatch):
    user, run = _user_and_run(db, suffix="-s3")
    _auth(client, user)
    store = _S3Storage({f"input/{run.id}.docx": b"ignored"})
    monkeypatch.setattr(steps_mod, "get_storage", lambda: store)

    resp = client.get(f"/api/run/{run.id}/input", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"].startswith("https://s3.example/")
    # The rename-on-download flows through as the original upload name.
    assert store.asked_name == _ORIGINAL


def test_404_when_original_absent_not_500(client, db, seed_simple_mode, monkeypatch):
    user, run = _user_and_run(db, suffix="-404")
    _auth(client, user)
    monkeypatch.setattr(steps_mod, "get_storage", lambda: _LocalStorage({}))

    resp = client.get(f"/api/run/{run.id}/input")
    assert resp.status_code == 404
    assert resp.json().get("detail", {}).get("error") != "internal_error"


def test_non_owner_forbidden(client, db, seed_simple_mode, monkeypatch):
    _, run = _user_and_run(db, suffix="-own")
    other, _ = _user_and_run(db, suffix="-other")  # different user, different run
    _auth(client, other)
    monkeypatch.setattr(steps_mod, "get_storage",
                        lambda: _LocalStorage({f"input/{run.id}.docx": b"x"}))

    resp = client.get(f"/api/run/{run.id}/input")
    assert resp.status_code == 403


def test_non_latin1_upload_name_downloads_instead_of_500(client, db, seed_simple_mode, monkeypatch):
    # HTTP headers are latin-1. A smart quote (Word autocorrects apostrophes) or
    # any non-Latin name used to raise UnicodeEncodeError building the response.
    name = "Dvořák’s CV – 2026.docx"
    user, run = _user_and_run(db, suffix="-utf8", filename=name)
    _auth(client, user)
    monkeypatch.setattr(steps_mod, "get_storage",
                        lambda: _LocalStorage({f"input/{run.id}.docx": b"x"}))

    resp = client.get(f"/api/run/{run.id}/input")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-disposition"] == (
        "attachment; filename*=utf-8''" + quote(name, safe=""))


def test_header_injection_stripped_from_upload_name(client, db, seed_simple_mode, monkeypatch):
    # A crafted upload filename must not break out of the Content-Disposition header.
    user, run = _user_and_run(db, suffix="-inj", filename='evil".docx\r\nX-Injected: 1')
    _auth(client, user)
    monkeypatch.setattr(steps_mod, "get_storage",
                        lambda: _LocalStorage({f"input/{run.id}.docx": b"x"}))

    resp = client.get(f"/api/run/{run.id}/input")
    assert resp.status_code == 200
    cd = resp.headers["content-disposition"]
    assert "\r" not in cd and "\n" not in cd
    assert "x-injected" not in {k.lower() for k in resp.headers}


def test_staff_downloads_another_users_original(client, db, seed_simple_mode, monkeypatch):
    """Staff (read-only, OFA) read every run's detail, the original included."""
    _, run = _user_and_run(db, suffix="-staff-own")
    staff, _ = _user_and_run(db, role="staff", suffix="-staff")
    _auth(client, staff)
    monkeypatch.setattr(steps_mod, "get_storage",
                        lambda: _LocalStorage({f"input/{run.id}.docx": b"PK\x03\x04 docx bytes"}))

    resp = client.get(f"/api/run/{run.id}/input")
    assert resp.status_code == 200, resp.text
