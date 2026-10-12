"""The original uploaded CV is retained in durable storage (input/<run>.<ext>)
but was only ever read internally. GET /api/run/<id>/input hands it back to the
run owner/admin, named as it was uploaded.
"""
from urllib.parse import quote

import pytest
from sqlalchemy.orm import object_session

import app.api.steps as steps_mod
from app.auth import COOKIE_NAME, create_session_cookie
from app.models import Run, User

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
    """Staff (read-only) read every run's detail, the original included."""
    _, run = _user_and_run(db, suffix="-staff-own")
    staff, _ = _user_and_run(db, role="staff", suffix="-staff")
    _auth(client, staff)
    monkeypatch.setattr(steps_mod, "get_storage",
                        lambda: _LocalStorage({f"input/{run.id}.docx": b"PK\x03\x04 docx bytes"}))

    resp = client.get(f"/api/run/{run.id}/input")
    assert resp.status_code == 200, resp.text


# --- #1333: serve the original only once GuardDuty has tagged it clean ----------

_SCAN_FLAG = "CVICHE_REQUIRE_MALWARE_SCAN"
_SYNTHETIC_NAME = "Synthetic Person CV.docx"


class _ScannedS3Storage(_S3Storage):
    """An S3 store whose objects carry a GuardDutyMalwareScanStatus tag."""

    def __init__(self, files, scan_status):
        super().__init__(files)
        self.scan_status = scan_status
        self.scan_lookups = 0

    def get_malware_scan_status(self, run_id, key):
        self.scan_lookups += 1
        return self.scan_status


def _scanned_download(client, db, monkeypatch, suffix, scan_status):
    user, run = _user_and_run(db, suffix=suffix, filename=_SYNTHETIC_NAME)
    _auth(client, user)
    store = _ScannedS3Storage({f"input/{run.id}.docx": b"x"}, scan_status)
    monkeypatch.setattr(steps_mod, "get_storage", lambda: store)
    return store, client.get(f"/api/run/{run.id}/input", follow_redirects=False)


@pytest.mark.parametrize("flag_value", [None, "0", "false", "off"])
def test_scan_flag_off_never_reads_the_scan_tag(client, db, seed_simple_mode, monkeypatch, flag_value):
    if flag_value is None:
        monkeypatch.delenv(_SCAN_FLAG, raising=False)
    else:
        monkeypatch.setenv(_SCAN_FLAG, flag_value)
    store, resp = _scanned_download(client, db, monkeypatch, "-scan-off", None)
    assert resp.status_code == 307
    assert store.scan_lookups == 0


@pytest.mark.parametrize("flag_value", ["1", "TRUE", " on "])
def test_scan_flag_on_clean_original_is_served(client, db, seed_simple_mode, monkeypatch, flag_value):
    monkeypatch.setenv(_SCAN_FLAG, flag_value)
    store, resp = _scanned_download(client, db, monkeypatch, "-scan-clean", "NO_THREATS_FOUND")
    assert resp.status_code == 307
    assert store.scan_lookups == 1


def test_scan_flag_on_untagged_original_is_409_still_scanning(client, db, seed_simple_mode, monkeypatch):
    monkeypatch.setenv(_SCAN_FLAG, "1")
    _, resp = _scanned_download(client, db, monkeypatch, "-scan-none", None)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "conflict"
    assert "still being scanned" in resp.json()["detail"]["message"]
    assert resp.headers["Retry-After"] == "60"


def test_scan_flag_on_threat_is_403_and_logs_run_id_not_filename(
        client, db, seed_simple_mode, monkeypatch, caplog):
    monkeypatch.setenv(_SCAN_FLAG, "1")
    with caplog.at_level("WARNING", logger=steps_mod.logger.name):
        _, resp = _scanned_download(client, db, monkeypatch, "-scan-bad", "THREATS_FOUND")
    assert resp.status_code == 403
    assert "flagged by the malware scan" in resp.json()["detail"]["message"]
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("run-input-scan-bad" in w for w in warnings)
    assert not any("Synthetic Person" in w for w in warnings)


@pytest.mark.parametrize("scan_status", ["UNSUPPORTED", "ACCESS_DENIED", "FAILED"])
def test_scan_flag_on_unscannable_original_is_403(client, db, seed_simple_mode, monkeypatch, scan_status):
    monkeypatch.setenv(_SCAN_FLAG, "1")
    _, resp = _scanned_download(client, db, monkeypatch, "-scan-unsup", scan_status)
    assert resp.status_code == 403
    assert "couldn't be scanned" in resp.json()["detail"]["message"]


def test_scan_flag_on_unreadable_tag_fails_closed_with_500(client, db, seed_simple_mode, monkeypatch):
    """No s3:GetObjectTagging grant (or an outage) refuses the download; it is
    not mistaken for "still scanning" and the original is not served."""
    monkeypatch.setenv(_SCAN_FLAG, "1")

    class _NoTagAccess(_ScannedS3Storage):
        def get_malware_scan_status(self, run_id, key):
            raise PermissionError("AccessDenied: s3:GetObjectTagging")

    user, run = _user_and_run(db, suffix="-scan-iam", filename=_SYNTHETIC_NAME)
    _auth(client, user)
    store = _NoTagAccess({f"input/{run.id}.docx": b"x"}, None)
    monkeypatch.setattr(steps_mod, "get_storage", lambda: store)

    resp = client.get(f"/api/run/{run.id}/input", follow_redirects=False)
    assert resp.status_code == 500
    assert store.asked_name is None  # no presigned URL was issued


def test_scan_flag_on_absent_original_is_404_before_any_tag_lookup(client, db, seed_simple_mode, monkeypatch):
    """The scan gate runs after the existence check: a missing original stays a
    404, not a 409 "still scanning" (local) or a 500 NoSuchKey on the tag read (S3)."""
    monkeypatch.setenv(_SCAN_FLAG, "1")
    user, run = _user_and_run(db, suffix="-scan-gone", filename=_SYNTHETIC_NAME)
    _auth(client, user)
    store = _ScannedS3Storage({}, "NO_THREATS_FOUND")
    monkeypatch.setattr(steps_mod, "get_storage", lambda: store)

    resp = client.get(f"/api/run/{run.id}/input", follow_redirects=False)
    assert resp.status_code == 404
    assert store.scan_lookups == 0


# --- #1333: ?as_url=true lets the page fetch() the download and show a refusal --

def test_as_url_on_s3_returns_the_presigned_url_as_json(client, db, seed_simple_mode, monkeypatch):
    user, run = _user_and_run(db, suffix="-asurl-s3", filename=_SYNTHETIC_NAME)
    _auth(client, user)
    store = _S3Storage({f"input/{run.id}.docx": b"ignored"})
    monkeypatch.setattr(steps_mod, "get_storage", lambda: store)

    resp = client.get(f"/api/run/{run.id}/input?as_url=true", follow_redirects=False)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"url": f"https://s3.example/{run.id}/input/{run.id}.docx?sig=abc"}
    assert store.asked_name == _SYNTHETIC_NAME
    # A presigned URL is a short-lived credential; no cache may keep it.
    assert resp.headers["cache-control"] == "no-store"


def test_as_url_on_local_storage_returns_null_url_not_the_bytes(client, db, seed_simple_mode, monkeypatch):
    user, run = _user_and_run(db, suffix="-asurl-local", filename=_SYNTHETIC_NAME)
    _auth(client, user)
    monkeypatch.setattr(steps_mod, "get_storage",
                        lambda: _LocalStorage({f"input/{run.id}.docx": b"PK\x03\x04 docx bytes"}))

    resp = client.get(f"/api/run/{run.id}/input?as_url=true")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"url": None}
    assert "content-disposition" not in resp.headers


def test_as_url_with_scan_flag_on_untagged_original_is_409_and_issues_no_url(
        client, db, seed_simple_mode, monkeypatch):
    monkeypatch.setenv(_SCAN_FLAG, "1")
    user, run = _user_and_run(db, suffix="-asurl-scan", filename=_SYNTHETIC_NAME)
    _auth(client, user)
    store = _ScannedS3Storage({f"input/{run.id}.docx": b"x"}, None)
    monkeypatch.setattr(steps_mod, "get_storage", lambda: store)

    resp = client.get(f"/api/run/{run.id}/input?as_url=true", follow_redirects=False)
    assert resp.status_code == 409
    assert "still being scanned" in resp.json()["detail"]["message"]
    assert resp.headers["Retry-After"] == "60"
    assert store.asked_name is None  # no presigned URL was issued


def test_as_url_absent_original_is_404_and_issues_no_url(client, db, seed_simple_mode, monkeypatch):
    user, run = _user_and_run(db, suffix="-asurl-404", filename=_SYNTHETIC_NAME)
    _auth(client, user)
    store = _S3Storage({})
    monkeypatch.setattr(steps_mod, "get_storage", lambda: store)

    resp = client.get(f"/api/run/{run.id}/input?as_url=true", follow_redirects=False)
    assert resp.status_code == 404
    assert "url" not in resp.json()
    assert store.asked_name is None  # no presigned URL was issued


def test_as_url_non_owner_is_403_and_issues_no_url(client, db, seed_simple_mode, monkeypatch):
    _, run = _user_and_run(db, suffix="-asurl-own", filename=_SYNTHETIC_NAME)
    other, _ = _user_and_run(db, suffix="-asurl-other")
    _auth(client, other)
    store = _S3Storage({f"input/{run.id}.docx": b"x"})
    monkeypatch.setattr(steps_mod, "get_storage", lambda: store)

    resp = client.get(f"/api/run/{run.id}/input?as_url=true", follow_redirects=False)
    assert resp.status_code == 403
    assert "url" not in resp.json()
    assert store.asked_name is None  # no presigned URL was issued
