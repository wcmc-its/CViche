"""app/services/mailer.py (#1298): off by default, counts-only bodies, no
filename ever, header values on one line. SES is never called: boto3.client
is patched. Addresses are invented."""
import logging
from unittest.mock import MagicMock

import pytest

from app.models import InboundRejectReason
from app.services import mailer


@pytest.fixture
def ses(monkeypatch):
    client = MagicMock()
    monkeypatch.setattr(mailer, "_client", lambda: client)
    return client


def test_send_is_off_by_default_and_logs_only_the_kind(ses, monkeypatch, caplog):
    monkeypatch.delenv("CVICHE_MAIL_SEND", raising=False)
    with caplog.at_level(logging.INFO, logger="app.services.mailer"):
        sent = mailer.send(mailer.processing_notice("pat@med.cornell.edu", runs=2, held=0, batch_id="BATCHA"))
    assert sent is False
    ses.send_email.assert_not_called()
    assert "would have sent" in caplog.text
    assert "pat@med.cornell.edu" not in caplog.text and "We received" not in caplog.text


def test_send_calls_ses_when_the_flag_is_on(ses, monkeypatch):
    monkeypatch.setenv("CVICHE_MAIL_SEND", "true")
    monkeypatch.setenv("CVICHE_MAIL_FROM", "no-reply@mail.example.org")
    assert mailer.send(mailer.processing_notice("pat@med.cornell.edu", runs=2, held=0, batch_id="BATCHA")) is True
    kwargs = ses.send_email.call_args.kwargs
    assert kwargs["FromEmailAddress"] == "no-reply@mail.example.org"
    assert kwargs["Destination"] == {"ToAddresses": ["pat@med.cornell.edu"]}


def test_default_sender_is_the_cviche_no_reply_address(ses, monkeypatch):
    monkeypatch.setenv("CVICHE_MAIL_SEND", "1")
    monkeypatch.delenv("CVICHE_MAIL_FROM", raising=False)
    mailer.send(mailer.processing_notice("pat@med.cornell.edu", runs=1, held=0, batch_id="BATCHA"))
    assert ses.send_email.call_args.kwargs["FromEmailAddress"] == "no-reply@cviche.weill.cornell.edu"


def _notice(**kwargs):
    kwargs.setdefault("batch_id", "BATCHA")
    return mailer.processing_notice("pat@med.cornell.edu", **kwargs).body


def test_notice_for_runs_only_links_to_the_batch(monkeypatch):
    monkeypatch.setenv("CVICHE_PUBLIC_URL", "https://cviche.example.org/")
    body = _notice(runs=3, held=0)
    assert "Processing 3 CVs" in body and "https://cviche.example.org/runs?batch=BATCHA" in body
    assert "waiting" not in body


def test_notice_with_held_files_also_links_to_new_run(monkeypatch):
    monkeypatch.setenv("CVICHE_PUBLIC_URL", "https://cviche.example.org")
    body = _notice(runs=2, held=1)
    assert "Processing 2 CVs" in body and "1 CV is waiting for your confirmation" in body
    assert "https://cviche.example.org/runs?batch=BATCHA" in body and "https://cviche.example.org/ " not in body
    assert body.count("https://cviche.example.org/\n") == 1  # the New run link


def test_notice_when_everything_is_held():
    body = _notice(runs=0, held=4, batch_id=None)
    assert "Processing" not in body and "4 CVs are waiting for your confirmation" in body


def test_notice_for_outdated_consent_says_to_sign_in(monkeypatch):
    monkeypatch.setenv("CVICHE_PUBLIC_URL", "https://cviche.example.org")
    body = _notice(runs=0, held=3, batch_id=None, outdated_consent=True)
    assert "sign in to cviche to review the updated terms" in body.lower() and "3 CVs are waiting" in body
    assert "Processing" not in body


def test_notices_never_carry_a_filename():
    for kwargs in ({"runs": 2, "held": 1}, {"runs": 0, "held": 2, "outdated_consent": True}, {"runs": 1, "held": 0, "skipped": 2}):
        body = _notice(**kwargs)
        assert ".docx" not in body and ".pdf" not in body


def test_header_values_lose_cr_and_lf(ses, monkeypatch):
    monkeypatch.setenv("CVICHE_MAIL_SEND", "1")
    mailer.send(mailer.OutboundMail(
        mailer.MailKind.REJECTION, "pat@med.cornell.edu\r\nBcc: x@example.org", "Hi\nthere\r\n", "body"))
    kwargs = ses.send_email.call_args.kwargs
    assert "\n" not in kwargs["Destination"]["ToAddresses"][0] and "\r" not in kwargs["Destination"]["ToAddresses"][0]
    assert "\n" not in kwargs["Content"]["Simple"]["Subject"]["Data"]


def test_a_ses_error_is_swallowed(ses, monkeypatch):
    from botocore.exceptions import ClientError
    monkeypatch.setenv("CVICHE_MAIL_SEND", "1")
    ses.send_email.side_effect = ClientError({"Error": {"Code": "MessageRejected"}}, "SendEmail")
    assert mailer.send(mailer.processing_notice("pat@med.cornell.edu", runs=1, held=0, batch_id="BATCHA")) is False


@pytest.mark.parametrize("reason", sorted(mailer.REPLYABLE_REASONS))
def test_every_rejection_reply_has_fixed_wording(reason):
    assert mailer.rejection("pat@med.cornell.edu", reason).body.startswith("We couldn't accept your email")


def test_unknown_or_unauthenticated_reasons_are_never_replyable():
    for reason in (InboundRejectReason.NOT_AUTHENTICATED, InboundRejectReason.UNKNOWN_USER,
                   InboundRejectReason.SENDER_DOMAIN, InboundRejectReason.SPAM_OR_VIRUS):
        assert reason not in mailer.REPLYABLE_REASONS
