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
        sent = mailer.send(mailer.acknowledgement("pat@med.cornell.edu", 2))
    assert sent is False
    ses.send_email.assert_not_called()
    assert "would have sent" in caplog.text
    assert "pat@med.cornell.edu" not in caplog.text and "We received" not in caplog.text


def test_send_calls_ses_when_the_flag_is_on(ses, monkeypatch):
    monkeypatch.setenv("CVICHE_MAIL_SEND", "true")
    monkeypatch.setenv("CVICHE_MAIL_FROM", "no-reply@mail.example.org")
    assert mailer.send(mailer.acknowledgement("pat@med.cornell.edu", 2)) is True
    kwargs = ses.send_email.call_args.kwargs
    assert kwargs["FromEmailAddress"] == "no-reply@mail.example.org"
    assert kwargs["Destination"] == {"ToAddresses": ["pat@med.cornell.edu"]}


def test_default_sender_is_the_cviche_no_reply_address(ses, monkeypatch):
    monkeypatch.setenv("CVICHE_MAIL_SEND", "1")
    monkeypatch.delenv("CVICHE_MAIL_FROM", raising=False)
    mailer.send(mailer.acknowledgement("pat@med.cornell.edu", 1))
    assert ses.send_email.call_args.kwargs["FromEmailAddress"] == "no-reply@cviche.weill.cornell.edu"


def test_acknowledgement_has_the_count_and_the_new_run_link_and_no_filename(monkeypatch):
    monkeypatch.setenv("CVICHE_PUBLIC_URL", "https://cviche.example.org/")
    mail = mailer.acknowledgement("pat@med.cornell.edu", 3)
    assert "3 CVs" in mail.body
    assert "https://cviche.example.org/" in mail.body
    assert ".docx" not in mail.body and ".pdf" not in mail.body


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
    assert mailer.send(mailer.acknowledgement("pat@med.cornell.edu", 1)) is False


@pytest.mark.parametrize("reason", sorted(mailer.REPLYABLE_REASONS))
def test_every_rejection_reply_has_fixed_wording(reason):
    assert mailer.rejection("pat@med.cornell.edu", reason).body.startswith("We couldn't accept your email")


def test_unknown_or_unauthenticated_reasons_are_never_replyable():
    for reason in (InboundRejectReason.NOT_AUTHENTICATED, InboundRejectReason.UNKNOWN_USER,
                   InboundRejectReason.SENDER_DOMAIN, InboundRejectReason.SPAM_OR_VIRUS):
        assert reason not in mailer.REPLYABLE_REASONS
