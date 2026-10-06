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
from app.pipeline import run_queue
from app.services import batch_completion, batch_service, inbound_autorun, inbound_service, mailer
from app.services.pdf_sandbox import PdfText
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
    """The PDF sandbox is its own suite's business; every PDF here has text and no scanned pages."""
    monkeypatch.setattr(inbound_service, "read_pdf", lambda content: PdfText("x" * 600, 1, []))


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


def test_a_one_file_email_batch_goes_on_the_single_queue(db, storage, sent, queue, seed_simple_mode):
    """#1340: a one-file batch routes like a single upload; the batch only
    drives the completion email."""
    _user(db)
    _send(db, storage, ["one"])

    run = db.query(Run).one()
    assert db.query(RunBatch).one().files_submitted == 1
    assert queue.enqueued == [(run.id, run_queue.SINGLE)]


def test_accepted_cvs_become_one_queued_batch_and_the_reply_links_to_it(db, storage, sent, queue, seed_simple_mode):
    _user(db)
    _send(db, storage, ["one", "two"])

    batch = db.query(RunBatch).one()
    runs = db.query(Run).all()
    assert (batch.files_submitted, len(runs)) == (2, 2)
    assert {r.batch_id for r in runs} == {batch.id} and {r.status for r in runs} == {RunState.QUEUED}
    assert sorted(run_id for run_id, _ in queue.enqueued) == sorted(r.id for r in runs)
    assert {q for _, q in queue.enqueued} == {run_queue.BATCH}
    assert [c.id for c in queue.cards] == [batch.id]  # the Teams batch card, once
    assert set(_statuses(db).values()) == {InboundFileStatus.SUBMITTED}
    assert storage.list_global(inbound_service.HELD_PREFIX) == []
    body = sent[0].body
    assert "Follow progress in Runs." in body and "- 2 processing" in body and f"/runs?batch={batch.id}" in body and "waiting" not in body
    assert "one.docx" not in body and "two.docx" not in body


def test_the_reply_cites_the_users_stored_consent_date(db, storage, sent, queue, seed_simple_mode):
    from datetime import datetime
    _user(db, consent_date=datetime(2026, 8, 6, 9, 0))
    _send(db, storage, ["one"])
    assert "terms you agreed to on August 6, 2026: " in sent[0].body and "/terms" in sent[0].body


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
    assert "- 1 processing" in sent[0].body and "- 1 waiting for your confirmation" in sent[0].body


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
    assert "- 2 processing" in sent[0].body and "- 2 waiting for your confirmation" in sent[0].body


def test_a_user_at_their_limit_gets_no_batch_and_every_file_is_held(db, storage, sent, queue, seed_simple_mode):
    _user(db, daily_limit=0)
    _send(db, storage, ["one"])
    assert db.query(Run).count() == 0 and db.query(RunBatch).count() == 0 and queue.cards == []
    assert "Processing 1" not in sent[0].body and "- 1 waiting" in sent[0].body


def test_outdated_consent_holds_everything_and_says_to_sign_in(db, storage, sent, queue, seed_simple_mode):
    _user(db, consent_version="0.1")
    _send(db, storage, ["one", "two"])

    assert db.query(Run).count() == 0 and db.query(RunBatch).count() == 0 and queue.enqueued == []
    assert set(_statuses(db).values()) == {InboundFileStatus.PENDING}
    body = sent[0].body.lower()
    assert "terms have been updated" in body and "sign in to review and accept them" in body and "- 2 waiting for you" in body
    assert "- 2 processing" not in body


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
    assert "- 1 waiting" in sent[0].body


def test_logs_carry_no_filename_or_address(db, storage, sent, queue, caplog, seed_simple_mode):
    _user(db)
    with caplog.at_level(logging.DEBUG):
        _send(db, storage, ["secret-name"])
    assert "secret-name" not in caplog.text and SENDER not in caplog.text


def _finish(db, *states):
    """Put the batch's runs, in order, into the given terminal states."""
    for run, state in zip(db.query(Run).order_by(Run.id).all(), states, strict=True):
        run.status = state
    db.commit()


def _completion_mails(sent):
    return [m for m in sent if m.kind == mailer.MailKind.COMPLETION]


def test_an_auto_run_batch_is_marked_email_and_a_web_batch_is_not(db, storage, sent, queue, seed_simple_mode):
    user = _user(db)
    web = batch_service.create_batch(db, user, 1)
    _send(db, storage, ["one"])
    assert web.source == "web" and db.query(RunBatch).filter(RunBatch.id != web.id).one().source == "email"


def test_completion_is_sent_once_when_the_last_run_finishes_and_not_before(db, storage, sent, queue, seed_simple_mode):
    _user(db)
    _send(db, storage, ["one", "two", "three"])
    batch_id = db.query(RunBatch).one().id
    _finish(db, RunState.COMPLETE, RunState.FAILED, RunState.RUNNING)
    assert batch_completion.send_if_batch_complete(db, batch_id) is False and _completion_mails(sent) == []
    _finish(db, RunState.COMPLETE, RunState.FAILED, RunState.CANCELLED)
    assert batch_completion.send_if_batch_complete(db, batch_id) is True
    assert batch_completion.send_if_batch_complete(db, batch_id) is False
    [mail] = _completion_mails(sent)
    assert mail.to_addr == SENDER and "- 1 ready to download\n- 2 failed" in mail.body
    assert db.query(RunBatch).one().completion_notified_at is not None


def test_a_single_cv_batch_gets_the_single_wording_and_the_run_link(db, storage, sent, queue, seed_simple_mode):
    _user(db)
    _send(db, storage, ["one"])
    _finish(db, RunState.FAILED)
    assert batch_completion.notify_if_batch_complete is not None
    batch_completion.send_if_batch_complete(db, db.query(RunBatch).one().id)
    [mail] = _completion_mails(sent)
    assert mail.subject == "Your CV failed to process" and "Open the run to retry it." in mail.body
    assert f"/run/{db.query(Run).one().id}" in mail.body


def test_a_web_batch_never_gets_a_completion_email(db, sent, seed_simple_mode):
    user = _user(db)
    batch = batch_service.create_batch(db, user, 1)
    db.add(Run(id="WEBRUN", filename="cv.docx", file_type="docx", status=RunState.COMPLETE, batch_id=batch.id))
    db.commit()
    assert batch_completion.send_if_batch_complete(db, batch.id) is False
    assert sent == [] and db.get(RunBatch, batch.id).completion_notified_at is None


def test_a_web_batch_that_asked_for_the_email_gets_it_once_when_its_last_run_finishes(db, sent, seed_simple_mode):
    """'Email me when job completes' on a multi-file upload (#1335)."""
    user = _user(db)
    batch = batch_service.create_batch(db, user, 2, notify_on_complete=True)
    db.add_all([Run(id=f"WEBRN{n}", filename="cv.docx", file_type="docx", status=RunState.COMPLETE, batch_id=batch.id)
                for n in (1, 2)])
    db.commit()
    _finish(db, RunState.COMPLETE, RunState.RUNNING)
    assert batch_completion.send_if_batch_complete(db, batch.id) is False and sent == []
    _finish(db, RunState.COMPLETE, RunState.FAILED)
    assert batch_completion.send_if_batch_complete(db, batch.id) is True
    assert batch_completion.send_if_batch_complete(db, batch.id) is False
    [mail] = _completion_mails(sent)
    assert mail.to_addr == SENDER and "- 1 ready to download\n- 1 failed" in mail.body


def test_a_single_upload_that_asked_for_the_email_links_to_its_run(db, sent, seed_simple_mode):
    """A ticked single upload is a one-file batch, so it gets the one-CV wording (#1335)."""
    user = _user(db)
    batch = batch_service.create_batch(db, user, 1, notify_on_complete=True)
    db.add(Run(id="WEBONE", filename="cv.docx", file_type="docx", status=RunState.COMPLETE, batch_id=batch.id))
    db.commit()
    assert batch_completion.send_if_batch_complete(db, batch.id) is True
    [mail] = _completion_mails(sent)
    assert mail.subject == "Your CV is ready" and "/run/WEBONE" in mail.body


def test_two_pods_finishing_the_last_runs_at_once_send_exactly_one_email(db, storage, sent, queue, monkeypatch, seed_simple_mode):
    _user(db)
    _send(db, storage, ["one", "two"])
    batch_id = db.query(RunBatch).one().id
    _finish(db, RunState.COMPLETE, RunState.COMPLETE)
    # Both pods read "all terminal" before either claims: only the claim decides.
    monkeypatch.setattr(batch_completion, "_is_complete", lambda *args: True)
    results = [batch_completion.send_if_batch_complete(db, batch_id) for _ in range(2)]
    assert results == [True, False] and len(_completion_mails(sent)) == 1


def test_a_batch_still_creating_its_runs_is_not_complete(db, storage, sent, queue, seed_simple_mode):
    user = _user(db)
    batch = batch_service.create_batch(db, user, 2, source="email")
    db.add(Run(id="RUN001", filename="cv.docx", file_type="docx", status=RunState.COMPLETE, batch_id=batch.id))
    db.commit()
    assert batch_completion.send_if_batch_complete(db, batch.id) is False


def test_a_refused_file_shrinks_the_batch_so_the_rest_can_complete(db, storage, sent, queue, monkeypatch, seed_simple_mode):
    _user(db)
    real = inbound_autorun._run_item
    monkeypatch.setattr(inbound_autorun, "_run_item",
                        lambda db_, st, u, item, req: False if item.filename == "two.docx" else real(db_, st, u, item, req))
    _send(db, storage, ["one", "two"])
    batch = db.query(RunBatch).one()
    assert batch.files_submitted == 1
    _finish(db, RunState.COMPLETE)
    assert batch_completion.send_if_batch_complete(db, batch.id) is True


def test_the_mail_flag_off_means_logged_not_sent(db, storage, queue, monkeypatch, caplog, seed_simple_mode):
    from unittest.mock import MagicMock
    ses = MagicMock()
    monkeypatch.setattr(mailer, "_client", lambda: ses)
    monkeypatch.delenv("CVICHE_MAIL_SEND", raising=False)
    _user(db)
    _send(db, storage, ["one"])
    _finish(db, RunState.COMPLETE)
    with caplog.at_level(logging.INFO):
        batch_completion.send_if_batch_complete(db, db.query(RunBatch).one().id)
    ses.send_email.assert_not_called()
    assert "would have sent a completion message" in caplog.text and SENDER not in caplog.text


def test_completion_mail_carries_no_filename_or_score(db, storage, sent, queue, seed_simple_mode):
    _user(db)
    _send(db, storage, ["secret-name"])
    _finish(db, RunState.COMPLETE)
    batch_completion.send_if_batch_complete(db, db.query(RunBatch).one().id)
    [mail] = _completion_mails(sent)
    for part in (mail.body, mail.html):
        assert "secret-name" not in part and ".docx" not in part and "score" not in part.lower()
