"""Outbound mail through SESv2 (#1298): the acknowledgement and rejection
replies for emailed CVs, as multipart text + HTML (the logo inline by CID).

Off by default: ``CVICHE_MAIL_SEND`` must be truthy, or a send is only logged
at INFO (the kind of message, never a body or an address). A send failure is
logged and swallowed -- mail is a courtesy and must never fail an intake.

Bodies carry counts and fixed wording only. NEVER a filename: filenames embed
faculty names. The one link goes to the New run page, never to a one-click run.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, replace
from datetime import datetime
from email.message import EmailMessage
from enum import StrEnum
from typing import TYPE_CHECKING

from app.config_loader import get_config
from app.models import InboundRejectReason
from app.services import notifications
from app.services.email_templates import (
    LOGO_FILES, EmailContent, Para, StatusKind, StatusRow, greeting_for, render_html, render_text,
)

if TYPE_CHECKING:
    from botocore.client import BaseClient

logger = logging.getLogger(__name__)

DEFAULT_MAIL_FROM = "no-reply@cviche.weill.cornell.edu"
# SES identities for CViche live in us-east-1 (spec, 2026-10-02).
DEFAULT_MAIL_REGION = "us-east-1"
# Where the terms live in the app. A user who has consented is bounced from
# /consent to /, so the data-handling terms they agreed to are linked at /help;
# a user whose consent is outdated gets /consent (after sign-in) to accept the new ones.
TERMS_PATH = "help#data-retention"
CONSENT_PATH = "consent"
CONSENT_DATE_FORMAT = "%B %-d, %Y"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_LINE_BREAKS = re.compile(r"[\r\n]+")


class MailKind(StrEnum):
    ACKNOWLEDGEMENT = "acknowledgement"
    REJECTION = "rejection"
    COMPLETION = "completion"


@dataclass(frozen=True, slots=True)
class OutboundMail:
    kind: MailKind
    to_addr: str
    subject: str
    body: str
    html: str = ""


# What a sender is told when their (authenticated, known) email is refused.
_REJECTION_TEXT: dict[InboundRejectReason, str] = {
    InboundRejectReason.NEVER_CONSENTED: "Please sign in to CViche once and accept the consent terms, then send it again.",
    InboundRejectReason.NOT_IN_ACCESS_GROUP: "We couldn't confirm your access to CViche.",
    InboundRejectReason.NO_VALID_ATTACHMENTS: "It had no .docx or .pdf CVs we could read.",
    InboundRejectReason.TOO_MANY_FILES: "It had more CVs than one email can carry.",
    InboundRejectReason.INBOX_FULL: "You already have the most CVs we can hold waiting for you. Submit or discard some first.",
}
# Reasons that get a reply: the sender passed DMARC AND matched a known user.
REPLYABLE_REASONS = frozenset(_REJECTION_TEXT)


def one_line(value: str) -> str:
    """A header value with CR/LF removed, so it can never inject a header."""
    return _LINE_BREAKS.sub(" ", value).strip()


def _enabled() -> bool:
    value, _ = get_config("mail", "CVICHE_MAIL_SEND", default="")
    return str(value).strip().lower() in _TRUTHY


def _new_run_url() -> str:
    explicit, _ = get_config("mail", "CVICHE_PUBLIC_URL", default="")
    return (explicit or notifications._run_link_base()).rstrip("/") + "/"


def _runs_batch_url(batch_id: str) -> str:
    return f"{_new_run_url()}runs?batch={batch_id}"


def processing_notice(
    to_addr: str, *, runs: int, held: int, batch_id: str | None = None,
    outdated_consent: bool = False, skipped: int = 0, consent_date: datetime | None = None,
    display_name: str | None = None,
) -> OutboundMail:
    """The reply to an accepted message: a status list of how many CVs are
    processing, waiting for the sender in New run, and skipped, with one button.
    Counts only, never a filename."""
    if outdated_consent:
        paras = [Para("The CViche terms have been updated. Please sign in to review and accept them.", bold=True)]
        status = (StatusRow(StatusKind.WAITING, held, "Waiting for you in New run"),)
        cta = ("Review the terms", f"{_new_run_url()}{CONSENT_PATH}")
    else:
        paras = [Para(f"Processing {_cvs(runs)}. Follow progress in Runs.", bold=True)] if runs else []
        paras.append(_terms_para(consent_date))
        status = (
            StatusRow(StatusKind.PROCESSING, runs, "Processing"),
            StatusRow(StatusKind.WAITING, held, "Waiting for your confirmation in New run"),
            StatusRow(StatusKind.SKIPPED, skipped, f"{_plural(skipped, 'Attachment')} skipped"),
        )
        cta = ((_cv_button(runs), _runs_batch_url(batch_id)) if runs else ("Open New run", _new_run_url()))
    content = EmailContent(_processing_headline(runs, held, outdated_consent), tuple(paras), *cta,
                           greeting=greeting_for(display_name), status=status)
    return _mail(MailKind.ACKNOWLEDGEMENT, to_addr, f"CViche received {_cvs(runs + held)}", content)


def _terms_para(consent_date: datetime | None) -> Para:
    when = f" on {consent_date.strftime(CONSENT_DATE_FORMAT)}" if consent_date else ""
    return Para(f"These CVs are processed under the CViche terms you agreed to{when}",
                f"{_new_run_url()}{TERMS_PATH}", "Read the terms")


def _plural(count: int, noun: str) -> str:
    return noun if count == 1 else f"{noun}s"


def _cv_button(count: int) -> str:
    return "View your CV" if count == 1 else "View your CVs"


def _processing_headline(runs: int, held: int, outdated_consent: bool) -> str:
    if outdated_consent:
        return "Please accept the updated terms"
    if runs:
        return "Your CV is being processed" if runs == 1 and not held else "Your CVs are being processed"
    return "Your CV is waiting for you" if held == 1 else "Your CVs are waiting for you"


def _mail(kind: MailKind, to_addr: str, subject: str, content: EmailContent) -> OutboundMail:
    return OutboundMail(kind, to_addr, subject, render_text(content), render_html(content))


def _cvs(count: int) -> str:
    return f"{count} CV" if count == 1 else f"{count} CVs"


def rejection(to_addr: str, reason: InboundRejectReason, display_name: str | None = None) -> OutboundMail:
    content = EmailContent(
        "We couldn't accept your email",
        (Para(f"We couldn't accept your email to CViche. {_REJECTION_TEXT[reason]}", bold=True),),
        greeting=greeting_for(display_name),
    )
    return _mail(MailKind.REJECTION, to_addr, "CViche couldn't accept your email", content)


def completion_notice(
    to_addr: str, *, complete: int, failed: int, batch_id: str, single_run_id: str | None = None,
    display_name: str | None = None,
) -> OutboundMail:
    """Sent once when every run of an emailed batch is terminal (#1298). Counts
    only, never a filename or a score. ``single_run_id`` is the lone run of a
    one-CV batch: that email links to the run itself."""
    if single_run_id is not None:
        content = _single_completion(complete, single_run_id)
    else:
        content = _batch_completion(complete, failed, batch_id)
    content = replace(content, greeting=greeting_for(display_name))
    return _mail(MailKind.COMPLETION, to_addr, content.headline, content)


def _single_completion(complete: int, run_id: str) -> EmailContent:
    url = f"{_new_run_url()}run/{run_id}"
    if complete:
        return EmailContent("Your CV is ready", (), "View your CV", url)
    return EmailContent("Your CV failed to process", (Para("Open the run to retry it."),), "View your CV", url)


def _batch_completion(complete: int, failed: int, batch_id: str) -> EmailContent:
    status = (StatusRow(StatusKind.READY, complete, "Ready to download"), StatusRow(StatusKind.FAILED, failed, "Failed"))
    paras = (Para("Open a failed run to retry it."),) if failed else ()
    return EmailContent("Your CVs are done", paras, "View your CVs", _runs_batch_url(batch_id), status=status)


def send(mail: OutboundMail) -> bool:
    """Send ``mail`` if ``CVICHE_MAIL_SEND`` is on. Returns whether SES took it.
    Never raises."""
    if not _enabled():
        logger.info("mail send is off; would have sent a %s message", mail.kind)
        return False
    from botocore.exceptions import BotoCoreError, ClientError

    sender, _ = get_config("mail", "CVICHE_MAIL_FROM", default=DEFAULT_MAIL_FROM)
    try:
        raw = build_message(mail, one_line(sender)).as_bytes()
        _client().send_email(
            FromEmailAddress=one_line(sender),
            Destination={"ToAddresses": [one_line(mail.to_addr)]},
            Content={"Raw": {"Data": raw}},
        )
    except (BotoCoreError, ClientError, OSError) as e:
        logger.warning("SES send failed for a %s message: %s", mail.kind, type(e).__name__)
        return False
    return True


def build_message(mail: OutboundMail, sender: str) -> EmailMessage:
    """multipart/related (HTML + the CID logos it references, and only those)
    inside multipart/alternative's HTML part. Every header value is one line."""
    message = EmailMessage()
    message["Subject"] = one_line(mail.subject)
    message["From"] = sender
    message["To"] = one_line(mail.to_addr)
    message.set_content(mail.body)
    if mail.html:
        message.add_alternative(mail.html, subtype="html")
        html_part = message.get_payload()[1]
        for cid, path in LOGO_FILES.items():
            if f"cid:{cid}" in mail.html:
                html_part.add_related(path.read_bytes(), maintype="image", subtype="png", cid=f"<{cid}>",
                                      filename=path.name, disposition="inline")
    return message


def _client() -> BaseClient:
    import boto3

    region, _ = get_config("mail", "CVICHE_MAIL_REGION", default=DEFAULT_MAIL_REGION)
    return boto3.client("sesv2", region_name=region)
