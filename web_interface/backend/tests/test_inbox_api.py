"""app/api/inbox.py (#1298): the emailed-CV inbox through the real router.

Ownership isolation (admins see only their own), discard, and submit through
the shared upload core. Storage is a LocalRunStorage in tmp_path; users and
files are invented.
"""
import hashlib
import io
from datetime import datetime

import pytest
from docx import Document
from sqlalchemy.orm import object_session

from app.auth import COOKIE_NAME, create_session_cookie
from app.models import (
    InboundFile, InboundFileStatus, InboundMessage, InboundMessageStatus, Run, RunBatch, User,
)
from app.services import inbound_service
from app.storage.local_storage import LocalRunStorage

ATTEST = {"submission_type": "own_cv"}


def _docx(label="a") -> bytes:
    document = Document()
    document.add_paragraph(f"Synthetic CV {label}. " * 60)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _user(db, email, role="user", **fields):
    fields.setdefault("consent_version", "1.0")
    user = User(email=email, display_name=email.split("@")[0].title(), role=role, **fields)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _auth(client, user):
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, object_session(user)))


@pytest.fixture
def storage(tmp_path, monkeypatch):
    store = LocalRunStorage(str(tmp_path / "store"))
    monkeypatch.setattr("app.api.inbox.get_storage", lambda: store)
    monkeypatch.setattr("app.services.run_creation.get_storage", lambda: store)
    monkeypatch.setattr("app.services.run_creation.UPLOAD_DIR", tmp_path)
    return store


@pytest.fixture
def pat(db, seed_simple_mode):
    return _user(db, "pat@med.cornell.edu")


def _hold(db, storage, user, filename="cv.docx", label=None, status=InboundFileStatus.PENDING) -> InboundFile:
    content = _docx(label or filename)
    message = InboundMessage(
        s3_key=f"inbound/{filename}-{user.id}-{db.query(InboundMessage).count()}",
        status=InboundMessageStatus.ACCEPTED, user_id=user.id, file_count=1,
    )
    db.add(message)
    db.flush()
    prefix = f"inbound-files/{filename}-{user.id}-{message.id}/"
    storage.put_global(f"{prefix}{inbound_service.HELD_OBJECT_NAME}", content)
    item = InboundFile(
        inbound_message_id=message.id, user_id=user.id, filename=filename, size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(), storage_key=prefix, status=status,
    )
    db.add(item)
    db.commit()
    return item


def _held(storage, item) -> list[str]:
    return storage.list_global(item.storage_key)


# --- GET /inbox ---------------------------------------------------------------

def test_inbox_requires_sign_in(client, seed_simple_mode):
    assert client.get("/api/inbox").status_code == 401
    assert client.post("/api/inbox/submit", json={"item_ids": [1], **ATTEST}).status_code == 401
    assert client.post("/api/inbox/1/discard").status_code == 401


def test_inbox_lists_only_the_callers_pending_items(client, db, storage, pat):
    mine = _hold(db, storage, pat, "mine.docx")
    _hold(db, storage, pat, "done.docx", status=InboundFileStatus.SUBMITTED)
    other = _user(db, "sam@med.cornell.edu")
    _hold(db, storage, other, "theirs.docx")
    _auth(client, pat)

    body = client.get("/api/inbox").json()

    assert [(i["id"], i["filename"]) for i in body["items"]] == [(mine.id, "mine.docx")]
    assert body["items"][0]["size_bytes"] == mine.size_bytes and body["items"][0]["duplicate"] is None


def test_an_admin_sees_only_their_own_inbox(client, db, storage, pat):
    admin = _user(db, "boss@med.cornell.edu", role="admin")
    _hold(db, storage, pat, "pats.docx")
    _auth(client, admin)
    assert client.get("/api/inbox").json() == {"items": []}


def test_duplicate_info_follows_the_1286_privacy_rule(client, db, storage, pat):
    item = _hold(db, storage, pat, "dup.docx")
    other = _user(db, "sam@med.cornell.edu")
    db.add(Run(id="SAMRUN", filename="x.docx", file_type="docx", status="complete", user_id=other.id,
               started_at=datetime(2026, 9, 1), source_sha256=item.sha256))
    db.commit()
    _auth(client, pat)

    duplicate = client.get("/api/inbox").json()["items"][0]["duplicate"]

    assert duplicate == {"last_processed_on": "September 1, 2026", "run_id": None}  # not pat's run: date only
    admin = _user(db, "boss@med.cornell.edu", role="admin")
    _hold(db, storage, admin, "dup.docx", label="dup.docx")
    _auth(client, admin)
    assert client.get("/api/inbox").json()["items"][0]["duplicate"]["run_id"] == "SAMRUN"
    staff = _user(db, "reader@med.cornell.edu", role="staff")  # reads every run: gets the id too
    _hold(db, storage, staff, "dup.docx", label="dup.docx")
    _auth(client, staff)
    assert client.get("/api/inbox").json()["items"][0]["duplicate"]["run_id"] == "SAMRUN"


# --- discard ------------------------------------------------------------------

def test_discard_deletes_the_bytes_and_hides_the_item(client, db, storage, pat):
    item = _hold(db, storage, pat)
    _auth(client, pat)

    resp = client.post(f"/api/inbox/{item.id}/discard")

    assert resp.status_code == 200 and resp.json() == {"id": item.id, "status": "discarded"}
    db.refresh(item)
    assert item.status == InboundFileStatus.DISCARDED
    assert _held(storage, item) == []
    assert client.get("/api/inbox").json() == {"items": []}
    assert client.post(f"/api/inbox/{item.id}/discard").status_code == 404  # no longer pending


def test_discarding_someone_elses_item_is_a_404_even_for_an_admin(client, db, storage, pat):
    item = _hold(db, storage, pat)
    admin = _user(db, "boss@med.cornell.edu", role="admin")
    other = _user(db, "sam@med.cornell.edu")
    for caller in (admin, other):
        _auth(client, caller)
        assert client.post(f"/api/inbox/{item.id}/discard").status_code == 404
    db.refresh(item)
    assert item.status == InboundFileStatus.PENDING and len(_held(storage, item)) == 1


# --- submit -------------------------------------------------------------------

def test_submit_creates_a_run_per_item_and_marks_them_submitted(client, db, storage, pat):
    first, second = _hold(db, storage, pat, "a.docx"), _hold(db, storage, pat, "b.docx")
    _auth(client, pat)

    resp = client.post("/api/inbox/submit", json={"item_ids": [first.id, second.id], **ATTEST})

    assert resp.status_code == 200, resp.text
    results = resp.json()["results"]
    assert [(r["id"], r["status"]) for r in results] == [(first.id, "submitted"), (second.id, "submitted")]
    for item, result in zip((first, second), results):
        db.refresh(item)
        assert (item.status, item.run_id) == (InboundFileStatus.SUBMITTED, result["run_id"])
        run = db.get(Run, result["run_id"])
        assert (run.filename, run.user_id, run.submission_type, run.source_sha256) == (
            item.filename, pat.id, "own_cv", item.sha256)
        assert _held(storage, item) == []  # the run's own archive holds the file now
        assert storage.exists(run.id, f"input/{run.id}.docx")


def test_submit_carries_options_and_batch_like_upload(client, db, storage, pat):
    item = _hold(db, storage, pat)
    db.add(RunBatch(id="BATCHA", user_id=pat.id, files_submitted=1))
    db.commit()
    _auth(client, pat)

    resp = client.post("/api/inbox/submit", json={
        "item_ids": [item.id], "submission_type": "authorized_admin", "batch_id": "BATCHA",
        "include_track_changes": False, "include_classification_comments": True,
        "strip_wcm_instructions": False,
    })

    run = db.get(Run, resp.json()["results"][0]["run_id"])
    assert (run.batch_id, run.submission_type) == ("BATCHA", "authorized_admin")
    assert (run.show_track_changes, run.show_pipeline_comments, run.strip_template_instructions) == (0, 1, 0)


def test_submit_with_someone_elses_batch_is_a_404_and_creates_nothing(client, db, storage, pat):
    item = _hold(db, storage, pat)
    other = _user(db, "sam@med.cornell.edu")
    db.add(RunBatch(id="THEIRS", user_id=other.id, files_submitted=1))
    db.commit()
    _auth(client, pat)

    resp = client.post("/api/inbox/submit", json={"item_ids": [item.id], "batch_id": "THEIRS", **ATTEST})

    assert resp.status_code == 404 and db.query(Run).count() == 0


def test_submit_refuses_someone_elses_item_whole(client, db, storage, pat):
    mine, theirs = _hold(db, storage, pat, "mine.docx"), _hold(db, storage, _user(db, "sam@med.cornell.edu"), "theirs.docx")
    _auth(client, pat)

    resp = client.post("/api/inbox/submit", json={"item_ids": [mine.id, theirs.id], **ATTEST})

    assert resp.status_code == 404 and db.query(Run).count() == 0
    db.refresh(mine)
    assert mine.status == InboundFileStatus.PENDING


def test_submit_needs_current_consent(client, db, storage, pat):
    item = _hold(db, storage, pat)
    pat.consent_version = "0.1"
    db.commit()
    _auth(client, pat)

    resp = client.post("/api/inbox/submit", json={"item_ids": [item.id], **ATTEST})

    assert resp.status_code == 403 and resp.json()["detail"]["error"] == "consent_required"
    db.refresh(item)
    assert item.status == InboundFileStatus.PENDING and db.query(Run).count() == 0


def test_a_duplicate_waits_for_confirmation_then_runs(client, db, storage, pat):
    item = _hold(db, storage, pat)
    db.add(Run(id="OLDRUN", filename="x.docx", file_type="docx", status="complete", user_id=pat.id,
               started_at=datetime(2026, 9, 1), source_sha256=item.sha256))
    db.commit()
    _auth(client, pat)

    first = client.post("/api/inbox/submit", json={"item_ids": [item.id], **ATTEST}).json()["results"][0]

    assert (first["status"], first["error"], first["last_processed_on"]) == ("failed", "duplicate_file", "September 1, 2026")
    db.refresh(item)
    assert item.status == InboundFileStatus.PENDING and len(_held(storage, item)) == 1

    second = client.post("/api/inbox/submit", json={"item_ids": [item.id], "confirm_duplicate": True, **ATTEST})
    assert second.json()["results"][0]["status"] == "submitted"


def test_the_run_quota_is_enforced_per_item(client, db, storage, pat):
    pat.daily_limit = 1
    db.commit()
    first, second = _hold(db, storage, pat, "a.docx"), _hold(db, storage, pat, "b.docx")
    _auth(client, pat)

    results = client.post("/api/inbox/submit", json={"item_ids": [first.id, second.id], **ATTEST}).json()["results"]

    assert [r["status"] for r in results] == ["submitted", "failed"]
    assert results[1]["error"] == "rate_limited"
    db.refresh(second)
    assert second.status == InboundFileStatus.PENDING


def test_submit_rejects_an_empty_or_oversized_selection(client, db, storage, pat):
    _auth(client, pat)
    assert client.post("/api/inbox/submit", json={"item_ids": [], **ATTEST}).status_code == 422
    too_many = list(range(1, 52))
    assert client.post("/api/inbox/submit", json={"item_ids": too_many, **ATTEST}).status_code == 400
