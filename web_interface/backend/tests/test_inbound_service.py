"""app/services/inbound_service.py (#1298): the decision on one raw message,
the S3 poll (idempotent, one bad message never stops the rest), and expiry.

Messages are synthetic (stdlib ``email``); users and addresses are invented.
SES and S3 are never called: storage is a LocalRunStorage in tmp_path and the
mailer is replaced by a recorder.
"""
import io
import json
import logging
import zipfile
from datetime import datetime, timedelta

import pytest
from docx import Document

from app.models import (
    InboundFile,
    InboundFileStatus,
    InboundMessage,
    InboundMessageStatus,
    InboundRejectReason,
    User,
)
from app.services import ed_access, inbound_service
from app.services.inbound_mail import AttachmentReject
from app.services.mailer import MailKind
from app.services.pdf_sandbox import PdfText
from app.storage.local_storage import LocalRunStorage
from tests.conftest import TestingSessionLocal
from tests.test_inbound_mail import PDF, SES_FAIL, SES_PASS, make_eml

SENDER = "pat@med.cornell.edu"


def _docx(label="a") -> bytes:
    document = Document()
    document.add_paragraph(f"Synthetic CV {label}. " * 60)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def storage(tmp_path):
    return LocalRunStorage(str(tmp_path / "store"))


@pytest.fixture
def sent(monkeypatch):
    """Every mail the service tried to send."""
    mails = []
    monkeypatch.setattr(inbound_service.mailer, "send", lambda mail: mails.append(mail) or True)
    return mails


@pytest.fixture(autouse=True)
def _readable_pdfs(monkeypatch):
    """The PDF sandbox is its own suite's business; every PDF here has text and no scanned pages."""
    monkeypatch.setattr(inbound_service, "read_pdf", lambda content: PdfText("x" * 600, 1, []))


def _user(db, email=SENDER, **fields):
    fields.setdefault("consent_version", "1.0")
    user = User(email=email, display_name="Pat Example", **fields)
    db.add(user)
    db.commit()
    return user


def _process(db, storage, raw, key="inbound/m1"):
    return inbound_service.process_message(db, storage, key, raw)


def _message(db, key="inbound/m1") -> InboundMessage:
    return db.query(InboundMessage).filter_by(s3_key=key).one()


def _files(db, **filters):
    return db.query(InboundFile).filter_by(**filters).all()


# --- accepted -----------------------------------------------------------------

def test_accepted_message_holds_each_cv_and_acknowledges_with_a_count(db, storage, sent):
    user = _user(db)
    raw = make_eml(attachments=[("one.docx", _docx("1")), ("two.pdf", PDF)])

    assert _process(db, storage, raw) is True

    message = _message(db)
    assert (message.status, message.file_count, message.user_id) == (InboundMessageStatus.ACCEPTED, 2, user.id)
    assert message.from_addr == SENDER and message.message_id == "<synthetic-1@example.org>"
    files = _files(db, user_id=user.id)
    assert sorted(f.filename for f in files) == ["one.docx", "two.pdf"]
    assert all(f.status == InboundFileStatus.PENDING and len(f.sha256) == 64 for f in files)
    for f in files:
        assert storage.get_global(f"{f.storage_key}{inbound_service.HELD_OBJECT_NAME}")
    assert [(m.kind, m.to_addr) for m in sent] == [(MailKind.ACKNOWLEDGEMENT, SENDER)]
    assert "- 2 waiting" in sent[0].body and "one.docx" not in sent[0].body and "two.pdf" not in sent[0].body
    assert [f.status for f in files] == [InboundFileStatus.PENDING] * 2  # in_process dispatch: all held


def test_nested_eml_attachments_are_held(db, storage, sent):
    _user(db)
    inner = make_eml(attachments=[("inner.docx", _docx())], auth=())
    _process(db, storage, make_eml(attachments=[("forwarded.eml", inner)]))
    assert [f.filename for f in _files(db)] == ["inner.docx"]


def test_sender_match_is_case_insensitive(db, storage, sent):
    _user(db, email="Pat@Med.Cornell.edu")
    _process(db, storage, make_eml(attachments=[("a.docx", _docx())]))
    assert _message(db).status == InboundMessageStatus.ACCEPTED


def test_the_reply_goes_to_the_account_address_not_the_header(db, storage, sent):
    _user(db, email="Pat@Med.Cornell.edu")
    _process(db, storage, make_eml(attachments=[("a.docx", _docx())]))
    assert sent[0].to_addr == "Pat@Med.Cornell.edu"


def test_skipped_attachments_are_summarised_and_counted_in_the_ack(db, storage, sent):
    _user(db)
    _process(db, storage, make_eml(attachments=[("a.docx", _docx()), ("old.doc", b"\xd0\xcf")]))
    message = _message(db)
    assert message.status == InboundMessageStatus.ACCEPTED
    assert message.reject_reason == f"{AttachmentReject.UNSUPPORTED_TYPE}:1"
    assert "- 1 attachment skipped" in sent[0].body


def test_an_unreadable_document_is_skipped(db, storage, sent, monkeypatch):
    _user(db)
    monkeypatch.setattr(inbound_service, "read_pdf", lambda content: PdfText("short", 1, []))
    _process(db, storage, make_eml(attachments=[("a.pdf", PDF)]))
    message = _message(db)
    assert message.reject_reason == InboundRejectReason.NO_VALID_ATTACHMENTS
    assert _files(db) == []


def test_logs_carry_no_filename_subject_or_address(db, storage, sent, caplog):
    _user(db)
    with caplog.at_level(logging.DEBUG):
        _process(db, storage, make_eml(attachments=[("secret-name.docx", _docx())]))
    assert "secret-name" not in caplog.text and SENDER not in caplog.text and "synthetic" not in caplog.text.lower()


# --- sender checks ------------------------------------------------------------

def _rejected(db, key="inbound/m1"):
    message = _message(db, key)
    assert message.status == InboundMessageStatus.REJECTED and message.file_count == 0
    assert _files(db) == []
    return message.reject_reason


@pytest.mark.parametrize("auth, expected", [
    ((SES_FAIL,), InboundRejectReason.NOT_AUTHENTICATED),
    ((), InboundRejectReason.NOT_AUTHENTICATED),
    ((SES_FAIL, "mx.example.org; dmarc=pass"), InboundRejectReason.NOT_AUTHENTICATED),
])
def test_unauthenticated_messages_are_dropped_silently(db, storage, sent, auth, expected):
    _user(db)
    _process(db, storage, make_eml(auth=auth, attachments=[("a.docx", _docx())]))
    assert _rejected(db) == expected
    assert sent == []
    assert _message(db).from_addr is None  # an unauthenticated address is never stored


def test_spam_or_virus_fail_is_dropped_silently(db, storage, sent):
    _user(db)
    _process(db, storage, make_eml(headers={"X-SES-Virus-Verdict": "FAIL"}, attachments=[("a.docx", _docx())]))
    assert _rejected(db) == InboundRejectReason.SPAM_OR_VIRUS
    assert sent == []


def test_unscanned_message_is_dropped_silently(db, storage, sent):
    """#1332: no virus verdict (receipt rule without ScanEnabled) fails closed."""
    _user(db)
    _process(db, storage, make_eml(headers={"X-SES-Virus-Verdict": None}, attachments=[("a.docx", _docx())]))
    assert _rejected(db) == InboundRejectReason.SPAM_OR_VIRUS
    assert db.query(InboundFile).count() == 0
    assert sent == []


def test_a_non_wcm_domain_is_dropped_silently(db, storage, sent):
    _user(db, email="pat@example.org")
    _process(db, storage, make_eml(from_addr="pat@example.org", attachments=[("a.docx", _docx())]))
    assert _rejected(db) == InboundRejectReason.SENDER_DOMAIN
    assert sent == []


def test_an_unknown_sender_gets_no_reply(db, storage, sent):
    _process(db, storage, make_eml(attachments=[("a.docx", _docx())]))
    assert _rejected(db) == InboundRejectReason.UNKNOWN_USER
    assert sent == []


def test_a_sender_who_is_not_the_account_holder_is_unknown_even_when_other_users_exist(db, storage, sent):
    _user(db, email="someone.else@med.cornell.edu")
    _process(db, storage, make_eml(attachments=[("a.docx", _docx())]))
    assert _rejected(db) == InboundRejectReason.UNKNOWN_USER
    assert sent == []


def test_a_user_who_never_consented_is_told_to_sign_in(db, storage, sent):
    _user(db, consent_version=None)
    _process(db, storage, make_eml(attachments=[("a.docx", _docx())]))
    assert _rejected(db) == InboundRejectReason.NEVER_CONSENTED
    assert [(m.kind, m.to_addr) for m in sent] == [(MailKind.REJECTION, SENDER)]
    assert "a.docx" not in sent[0].body


def test_a_disabled_user_is_refused_without_a_reply(db, storage, sent):
    _user(db, status="disabled")
    _process(db, storage, make_eml(attachments=[("a.docx", _docx())]))
    assert _rejected(db) == InboundRejectReason.USER_DISABLED
    assert sent == []


def test_a_user_out_of_the_ed_access_group_is_refused(db, storage, sent, seed_ed_enabled, monkeypatch):
    from app.ed_group_lookup import EdUnavailableError
    _user(db, auth_method="saml", cwid="pat0001")

    def unavailable(**kwargs):
        raise EdUnavailableError("directory down")

    monkeypatch.setattr("app.services.ed_access.check_ed_membership", unavailable)
    _process(db, storage, make_eml(attachments=[("a.docx", _docx())]))
    assert _rejected(db) == InboundRejectReason.NOT_IN_ACCESS_GROUP  # fails closed
    assert [m.kind for m in sent] == [MailKind.REJECTION]


def test_ed_check_runs_through_the_shared_service(db, storage, sent, monkeypatch):
    _user(db)
    calls = []

    def deny(user, db):
        calls.append(user.email)
        raise ed_access.EdNotInAccessGroup()

    monkeypatch.setattr("app.services.ed_access.verify_ed_access", deny)
    _process(db, storage, make_eml(attachments=[("a.docx", _docx())]))
    assert calls == [SENDER]
    assert _rejected(db) == InboundRejectReason.NOT_IN_ACCESS_GROUP


# --- caps ---------------------------------------------------------------------

def test_more_files_than_one_batch_rejects_the_message(db, storage, sent, monkeypatch):
    _user(db)
    monkeypatch.setattr(inbound_service, "MAX_BATCH_FILES", 2)
    _process(db, storage, make_eml(attachments=[(f"{n}.docx", _docx(str(n))) for n in range(3)]))
    assert _rejected(db) == InboundRejectReason.TOO_MANY_FILES
    assert [m.kind for m in sent] == [MailKind.REJECTION]


def test_a_full_inbox_rejects_further_files(db, storage, sent, monkeypatch):
    user = _user(db)
    monkeypatch.setattr(inbound_service, "MAX_PENDING_PER_USER", 1)
    _process(db, storage, make_eml(attachments=[("a.docx", _docx("a"))]), key="inbound/m1")
    _process(db, storage, make_eml(attachments=[("b.docx", _docx("b"))]), key="inbound/m2")
    assert _message(db, "inbound/m2").reject_reason == InboundRejectReason.INBOX_FULL
    assert len(_files(db, user_id=user.id)) == 1


def test_a_mostly_scanned_pdf_is_skipped_and_counted_in_the_reply(db, storage, sent, monkeypatch):
    _user(db)
    monkeypatch.setattr(inbound_service, "read_pdf", lambda content: PdfText("x" * 600, 4, [2, 3]))  # 2 of 4 pages
    _process(db, storage, make_eml(attachments=[("scan.pdf", PDF), ("ok.docx", _docx())]))
    message = _message(db)
    assert message.status == InboundMessageStatus.ACCEPTED
    assert message.reject_reason == f"{AttachmentReject.SCANNED_PDF}:1"
    assert [f.filename for f in _files(db)] == ["ok.docx"]
    assert "- 1 attachment skipped" in sent[0].body and "scan.pdf" not in sent[0].body


def test_a_docx_with_a_macro_is_skipped_and_counted(db, storage, sent):
    """#1334: the email intake refuses active content the way /upload does."""
    _user(db)
    buffer = io.BytesIO(_docx("m"))
    with zipfile.ZipFile(buffer, "a") as z:
        z.writestr("word/vbaProject.bin", b"\x00" * 64)
    _process(db, storage, make_eml(attachments=[("macro.docx", buffer.getvalue()), ("ok.docx", _docx())]))
    message = _message(db)
    assert message.status == InboundMessageStatus.ACCEPTED
    assert message.reject_reason == f"{AttachmentReject.ACTIVE_CONTENT}:1"
    assert [f.filename for f in _files(db)] == ["ok.docx"]


def test_a_minority_of_scanned_pages_is_accepted(db, storage, sent, monkeypatch):
    _user(db)
    monkeypatch.setattr(inbound_service, "read_pdf", lambda content: PdfText("x" * 600, 4, [2]))
    _process(db, storage, make_eml(attachments=[("ok.pdf", PDF)]))
    assert [f.filename for f in _files(db)] == ["ok.pdf"]


def test_an_oversize_attachment_is_skipped(db, storage, sent, monkeypatch):
    from app.services import inbound_mail
    _user(db)
    monkeypatch.setattr(inbound_mail, "MAX_UPLOAD_SIZE", len(PDF) + 1)
    _process(db, storage, make_eml(attachments=[("big.docx", _docx()), ("ok.pdf", PDF)]))
    assert [f.filename for f in _files(db)] == ["ok.pdf"]


def test_unparseable_bytes_are_recorded_as_rejected(db, storage, sent):
    _process(db, storage, b"")
    assert _rejected(db) == InboundRejectReason.UNPARSEABLE


# --- the poller ---------------------------------------------------------------

def _put(storage, name, raw):
    storage.put_global(f"{inbound_service.INBOUND_PREFIX}{name}", raw)


def test_poll_once_is_idempotent(db, storage, sent):
    _user(db)
    _put(storage, "m1", make_eml(attachments=[("a.docx", _docx())]))

    assert inbound_service.poll_once(TestingSessionLocal, storage) == 1
    assert inbound_service.poll_once(TestingSessionLocal, storage) == 0

    assert db.query(InboundMessage).count() == 1
    assert len(_files(db)) == 1
    assert len(sent) == 1


def test_a_second_poller_losing_the_race_stores_and_sends_nothing(db, storage, sent):
    _user(db)
    raw = make_eml(attachments=[("a.docx", _docx())])
    assert _process(db, storage, raw) is True
    assert _process(db, storage, raw) is False
    assert len(_files(db)) == 1 and len(sent) == 1


def test_one_bad_message_does_not_stop_the_others(db, storage, sent, monkeypatch):
    _user(db)
    for name in ("m1", "m2", "m3"):
        _put(storage, name, make_eml(attachments=[(f"{name}.docx", _docx(name))]))
    real = inbound_service.parse_message

    def parse(raw):
        if b"m2.docx" in raw:
            raise RuntimeError("boom")
        return real(raw)

    monkeypatch.setattr(inbound_service, "parse_message", parse)

    assert inbound_service.poll_once(TestingSessionLocal, storage) == 3

    statuses = {m.s3_key: (m.status, m.reject_reason) for m in db.query(InboundMessage)}
    assert statuses["inbound/m2"] == (InboundMessageStatus.FAILED, InboundRejectReason.PROCESSING_ERROR)
    assert statuses["inbound/m1"][0] == statuses["inbound/m3"][0] == InboundMessageStatus.ACCEPTED
    assert sorted(f.filename for f in _files(db)) == ["m1.docx", "m3.docx"]
    # a failed message is not retried on the next poll
    assert inbound_service.poll_once(TestingSessionLocal, storage) == 0


def test_a_busy_pdf_sandbox_defers_the_message_to_the_next_poll(db, storage, sent, monkeypatch):
    from app.services.pdf_sandbox import PdfBusyError
    _user(db)
    _put(storage, "m1", make_eml(attachments=[("a.pdf", PDF)]))

    def busy(content):
        raise PdfBusyError("full")

    monkeypatch.setattr(inbound_service, "read_pdf", busy)
    inbound_service.poll_once(TestingSessionLocal, storage)
    assert db.query(InboundMessage).count() == 0  # nothing recorded, so it is retried
    assert _files(db) == []


def test_failed_hold_deletes_the_bytes_it_wrote(db, storage, sent, monkeypatch, tmp_path):
    _user(db)
    original = db.commit

    def failing_commit():
        raise RuntimeError("db down")

    monkeypatch.setattr(db, "commit", failing_commit)
    with pytest.raises(RuntimeError):
        _process(db, storage, make_eml(attachments=[("a.docx", _docx())]))
    monkeypatch.setattr(db, "commit", original)
    assert storage.list_global(inbound_service.HELD_PREFIX) == []
    assert sent == []


# --- expiry -------------------------------------------------------------------

def test_expiry_marks_old_pending_items_and_deletes_their_bytes(db, storage, sent):
    _user(db)
    _process(db, storage, make_eml(attachments=[("old.docx", _docx("old"))]), key="inbound/m1")
    _process(db, storage, make_eml(attachments=[("new.docx", _docx("new"))]), key="inbound/m2")
    by_name = {f.filename: f for f in _files(db)}
    old, new = by_name["old.docx"], by_name["new.docx"]
    now = datetime(2026, 10, 20)
    old.created_at = now - inbound_service.PENDING_EXPIRY - timedelta(minutes=1)
    new.created_at = now - inbound_service.PENDING_EXPIRY + timedelta(days=1)
    db.commit()
    old_key = f"{old.storage_key}{inbound_service.HELD_OBJECT_NAME}"

    assert inbound_service.expire_stale(TestingSessionLocal, storage, now=now) == 1

    db.expire_all()
    assert (old.status, new.status) == (InboundFileStatus.EXPIRED, InboundFileStatus.PENDING)
    assert storage.list_global(inbound_service.HELD_PREFIX) == [f"{new.storage_key}{inbound_service.HELD_OBJECT_NAME}"]
    assert old_key not in storage.list_global(inbound_service.HELD_PREFIX)


def test_expiry_leaves_non_pending_items_alone(db, storage, sent):
    _user(db)
    _process(db, storage, make_eml(attachments=[("a.docx", _docx())]))
    item = _files(db)[0]
    item.status, item.created_at = InboundFileStatus.SUBMITTED, datetime(2020, 1, 1)
    db.commit()
    assert inbound_service.expire_stale(TestingSessionLocal, storage, now=datetime(2026, 10, 20)) == 0


def test_local_storage_global_reads_and_listing(storage):
    storage.put_global("inbound/a", b"1")
    storage.put_global("inbound/b/c", b"2")
    storage.put_global("other/x", b"3")
    assert storage.list_global("inbound/") == ["inbound/a", "inbound/b/c"]
    assert storage.get_global("inbound/b/c") == b"2"
    with pytest.raises(FileNotFoundError):
        storage.get_global("inbound/missing")
    with pytest.raises(ValueError):
        storage.list_global("")
