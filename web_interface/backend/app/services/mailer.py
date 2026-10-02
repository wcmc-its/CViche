"""Outbound mail through SESv2 (#1298): the acknowledgement and rejection
replies for emailed CVs. Plain text only.

Off by default: ``CVICHE_MAIL_SEND`` must be truthy, or a send is only logged
at INFO (the kind of message, never a body or an address). A send failure is
logged and swallowed -- mail is a courtesy and must never fail an intake.

Bodies carry counts and fixed wording only. NEVER a filename: filenames embed
faculty names. The one link goes to the New run page, never to a one-click run.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from app.config_loader import get_config
from app.models import InboundRejectReason
from app.services import notifications

if TYPE_CHECKING:
    from botocore.client import BaseClient

logger = logging.getLogger(__name__)

DEFAULT_MAIL_FROM = "no-reply@cviche.weill.cornell.edu"
# SES identities for CViche live in us-east-1 (spec, 2026-10-02).
DEFAULT_MAIL_REGION = "us-east-1"
# Where the terms live in the app. A user who has consented is bounced from
# /consent to /, so the terms they agreed to are linked at the read-only /terms page;
# a user whose consent is outdated gets /consent (after sign-in) to accept the new ones.
TERMS_PATH = "terms"
CONSENT_PATH = "consent"
CONSENT_DATE_FORMAT = "%B %-d, %Y"
_TRUTHY = frozenset({"1", "true", "yes", "on"})
_LINE_BREAKS = re.compile(r"[\r\n]+")


class MailKind(StrEnum):
    ACKNOWLEDGEMENT = "acknowledgement"
    REJECTION = "rejection"


@dataclass(frozen=True, slots=True)
class OutboundMail:
    kind: MailKind
    to_addr: str
    subject: str
    body: str


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
) -> OutboundMail:
    """The reply to an accepted message: how many CVs are processing (with a link
    to their batch in Runs) and how many wait for the sender in New run. Counts
    only, never a filename."""
    lines = []
    if outdated_consent:
        lines.append("The CViche terms have been updated. Please sign in to review and accept them: "
                     f"{_new_run_url()}{CONSENT_PATH}")
        lines.append(f"Your {_cvs(held)} {'is' if held == 1 else 'are'} waiting for you in New run: {_new_run_url()}")
    else:
        if runs:
            lines.append(f"Processing {_cvs(runs)}. Follow progress in Runs: {_runs_batch_url(batch_id)}")
        if held:
            lines.append(f"{_cvs(held)} {'is' if held == 1 else 'are'} waiting for your confirmation "
                         f"in New run: {_new_run_url()}")
    if not outdated_consent:
        when = f" on {consent_date.strftime(CONSENT_DATE_FORMAT)}" if consent_date else ""
        lines.append(f"These CVs are processed under the CViche terms you agreed to{when}: "
                     f"{_new_run_url()}{TERMS_PATH}")
    if skipped:
        lines.append(f"{skipped} other attachment(s) could not be used and were skipped.")
    return OutboundMail(
        MailKind.ACKNOWLEDGEMENT, to_addr, f"CViche received {_cvs(runs + held)}", "\n\n".join(lines) + "\n",
    )


def _cvs(count: int) -> str:
    return f"{count} CV" if count == 1 else f"{count} CVs"


def rejection(to_addr: str, reason: InboundRejectReason) -> OutboundMail:
    return OutboundMail(
        MailKind.REJECTION, to_addr, "CViche couldn't accept your email",
        f"We couldn't accept your email to CViche. {_REJECTION_TEXT[reason]}\n",
    )


def send(mail: OutboundMail) -> bool:
    """Send ``mail`` if ``CVICHE_MAIL_SEND`` is on. Returns whether SES took it.
    Never raises."""
    if not _enabled():
        logger.info("mail send is off; would have sent a %s message", mail.kind)
        return False
    from botocore.exceptions import BotoCoreError, ClientError

    sender, _ = get_config("mail", "CVICHE_MAIL_FROM", default=DEFAULT_MAIL_FROM)
    try:
        _client().send_email(
            FromEmailAddress=one_line(sender),
            Destination={"ToAddresses": [one_line(mail.to_addr)]},
            Content={"Simple": {
                "Subject": {"Data": one_line(mail.subject), "Charset": "UTF-8"},
                "Body": {"Text": {"Data": mail.body, "Charset": "UTF-8"}},
            }},
        )
    except (BotoCoreError, ClientError) as e:
        logger.warning("SES send failed for a %s message: %s", mail.kind, type(e).__name__)
        return False
    return True


def _client() -> BaseClient:
    import boto3

    region, _ = get_config("mail", "CVICHE_MAIL_REGION", default=DEFAULT_MAIL_REGION)
    return boto3.client("sesv2", region_name=region)
