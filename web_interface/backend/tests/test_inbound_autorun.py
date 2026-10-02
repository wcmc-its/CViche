"""app/services/inbound_autorun.py (#1298, revised): an accepted emailed message
starts its CVs automatically as one batch; duplicates, files past the quota,
everything for a user with outdated consent, and everything outside queue mode
are held pending. Messages and users are synthetic; storage is a tmp_path
LocalRunStorage; the queue and the Teams card are recorders."""
import hashlib
import io
import logging

import pytest
from docx import Document

from app.models import (
    InboundFile, InboundFileStatus, InboundMessage, Run, RunBatch, RunState, User,
)
from app.services import inbound_autorun, inbound_service
from app.storage.local_storage import LocalRunStorage
from tests.test_inbound_mail import make_eml

SENDER = "pat@med.cornell.edu"


def _docx(label="a") -> bytes:
    document = Document()
    document.add_paragraph(f"Synthetic CV {label}. " * 60)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def storage(tmp_path, monkeypatch):
    store = LocalRunStorage(str(tmp_path / "store"))
    monkeypatch.setattr("app.services.run_creation.get_storage", lambda: store)
    monkeypatch.setattr("app.services.run_creation.UPLOAD_DIR", tmp_path)
    return store


@pytest.fixture
def sent(monkeypatch):
    mails = []
    monkeypatch.setattr(inbound_service.mailer, "send", lambda mail: mails.append(mail) or True)
    return mails


@pytest.fixture
def queue(monkeypatch):
    """Queue dispatch mode with the enqueue and the Teams batch card recorded."""
    enqueued, cards = [], []
    monkeypatch.setattr(inbound_autorun.concurrency, "dispatch_mode", lambda: "queue")
    monkeypatch.setattr(inbound_autorun.run_queue, "is_configured", lambda: True)
    monkeypatch.setattr(inbound_autorun.run_queue, "enqueue", lambda run_id, q: enqueued.append((run_id, q)))
    monkeypatch.setattr(inbound_autorun.notifications, "notify_batch_submitted",
                        lambda batch, submitter=None: cards.append(batch))
    return SimpleQueue(enqueued, cards)


class SimpleQueue:
    def __init__(self, enqueued, cards):
        self.enqueued, self.cards = enqueued, cards


@pytest.fixture(autouse=True)
def _readable_pdfs(monkeypatch):
    real = inbound_service._extract_text
    monkeypatch.setattr(inbound_service, "_extract_text",
                        lambda content, ext: "x" * 600 if ext == ".pdf" else real(content, ext))


def _user(db, **fields):
    fields.setdefault("consent_version", "1.0")
    user = User(email=SENDER, display_name="Pat Example", **fields)
    db.add(user)
    db.commit()
    return user


def _send(db, storage, names, key="inbound/m1"):
    raw = make_eml(attachments=[(f"{n}.docx", _docx(n)) for n in names])
    assert inbound_service.process_message(db, storage, key, raw) is True


def _statuses(db):
    return {f.filename: f.status for f in db.query(InboundFile)}


def test_accepted_cvs_become_one_queued_batch_and_the_reply_links_to_it(db, storage, sent, queue, seed_simple_mode):
    _user(db)
    _send(db, storage, ["one", "two"])

    batch = db.query(RunBatch).one()
    runs = db.query(Run).all()
    assert (batch.files_submitted, len(runs)) == (2, 2)
    assert {r.batch_id for r in runs} == {batch.id} and {r.status for r in runs} == {RunState.QUEUED}
    assert sorted(run_id for run_id, _ in queue.enqueued) == sorted(r.id for r in runs)
    assert [c.id for c in queue.cards] == [batch.id]  # the Teams batch card, once
    assert set(_statuses(db).values()) == {InboundFileStatus.SUBMITTED}
    assert storage.list_global(inbound_service.HELD_PREFIX) == []
    body = sent[0].body
    assert "Processing 2 CVs" in body and f"/runs?batch={batch.id}" in body and "waiting" not in body
    assert "one.docx" not in body and "two.docx" not in body


@pytest.mark.parametrize("chosen, expected", [
    ("own_cv", "own_cv"), ("authorized_admin", "authorized_admin"), (None, "authorized_admin"), ("junk", "authorized_admin"),
])
def test_submission_type_is_the_users_default_with_a_fallback(db, storage, sent, queue, seed_simple_mode, chosen, expected):
    _user(db, default_submission_type=chosen)
    _send(db, storage, ["one"])
    assert db.query(Run).one().submission_type == expected


def test_web_batch_default_options_are_used(db, storage, sent, queue, seed_simple_mode):
    _user(db)
    _send(db, storage, ["one"])
    run = db.query(Run).one()
    assert (run.show_track_changes, run.show_pipeline_comments, run.strip_template_instructions) == (1, 0, 1)


def test_a_previously_processed_file_is_held_never_run(db, storage, sent, queue, seed_simple_mode):
    user = _user(db)
    db.add(Run(id="OLDRUN", filename="x.docx", file_type="docx", status="complete", user_id=user.id,
               source_sha256=hashlib.sha256(_docx("one")).hexdigest()))
    db.commit()
    _send(db, storage, ["one", "two"])

    assert _statuses(db) == {"one.docx": InboundFileStatus.PENDING, "two.docx": InboundFileStatus.SUBMITTED}
    assert db.query(Run).count() == 2 and db.query(RunBatch).one().files_submitted == 1
    assert "Processing 1 CV" in sent[0].body and "1 CV is waiting for your confirmation" in sent[0].body


def test_two_identical_files_in_one_message_run_once(db, storage, sent, queue, seed_simple_mode):
    _user(db)
    raw = make_eml(attachments=[("a.docx", _docx("same")), ("b.docx", _docx("same"))])
    inbound_service.process_message(db, storage, "inbound/m1", raw)
    assert db.query(Run).count() == 1
    assert sorted(_statuses(db).values()) == [InboundFileStatus.PENDING, InboundFileStatus.SUBMITTED]


def test_files_beyond_the_remaining_quota_are_held_in_received_order(db, storage, sent, queue, seed_simple_mode):
    _user(db, daily_limit=2)
    _send(db, storage, ["one", "two", "three", "four"])

    assert db.query(Run).count() == 2 and db.query(RunBatch).one().files_submitted == 2
    statuses = _statuses(db)
    assert [statuses[f"{n}.docx"] for n in ("one", "two", "three", "four")] == [
        InboundFileStatus.SUBMITTED, InboundFileStatus.SUBMITTED, InboundFileStatus.PENDING, InboundFileStatus.PENDING]
    assert "Processing 2 CVs" in sent[0].body and "2 CVs are waiting for your confirmation" in sent[0].body


def test_a_user_at_their_limit_gets_no_batch_and_every_file_is_held(db, storage, sent, queue, seed_simple_mode):
    _user(db, daily_limit=0)
    _send(db, storage, ["one"])
    assert db.query(Run).count() == 0 and db.query(RunBatch).count() == 0 and queue.cards == []
    assert "Processing" not in sent[0].body and "1 CV is waiting" in sent[0].body


def test_outdated_consent_holds_everything_and_says_to_sign_in(db, storage, sent, queue, seed_simple_mode):
    _user(db, consent_version="0.1")
    _send(db, storage, ["one", "two"])

    assert db.query(Run).count() == 0 and db.query(RunBatch).count() == 0 and queue.enqueued == []
    assert set(_statuses(db).values()) == {InboundFileStatus.PENDING}
    body = sent[0].body.lower()
    assert "sign in to cviche to review the updated terms" in body and "2 cvs are waiting" in body
    assert "processing" not in body


def test_outside_queue_mode_everything_is_held_with_a_warning(db, storage, sent, monkeypatch, caplog, seed_simple_mode):
    _user(db)
    monkeypatch.setattr(inbound_autorun.concurrency, "dispatch_mode", lambda: "in_process")
    with caplog.at_level(logging.WARNING, logger="app.services.inbound_autorun"):
        _send(db, storage, ["one"])
    assert db.query(Run).count() == 0 and set(_statuses(db).values()) == {InboundFileStatus.PENDING}
    assert "cannot be queued" in caplog.text


def test_an_enqueue_failure_leaves_the_run_created_and_is_logged(db, storage, sent, queue, monkeypatch, caplog, seed_simple_mode):
    import redis
    _user(db)

    def boom(run_id, q):
        raise redis.exceptions.ConnectionError("down")

    monkeypatch.setattr(inbound_autorun.run_queue, "enqueue", boom)
    with caplog.at_level(logging.ERROR, logger="app.services.inbound_autorun"):
        _send(db, storage, ["one"])
    assert db.query(Run).one().status == RunState.CREATED
    assert "not queued" in caplog.text


def test_an_unexpected_auto_run_failure_keeps_the_files_pending(db, storage, sent, queue, monkeypatch, seed_simple_mode):
    _user(db)

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(inbound_service, "auto_run", boom)
    _send(db, storage, ["one"])
    assert db.query(InboundMessage).one().status == "accepted"
    assert set(_statuses(db).values()) == {InboundFileStatus.PENDING}
    assert "1 CV is waiting" in sent[0].body


def test_logs_carry_no_filename_or_address(db, storage, sent, queue, caplog, seed_simple_mode):
    _user(db)
    with caplog.at_level(logging.DEBUG):
        _send(db, storage, ["secret-name"])
    assert "secret-name" not in caplog.text and SENDER not in caplog.text
