"""Parse one raw inbound message (#1298): outer-message trust headers and the
CV attachments. Pure -- no database, no storage, no network.

Only the OUTER message is trusted: the first ``Authentication-Results`` header
(the one SES prepends) must show ``dmarc=pass``, and the sender checks use the
outer From. A forwarded or attached message contributes its attachments only,
never a sender, so a spoofed inner header proves nothing. The SES spam and
virus verdicts are read from the outer message too: SES scans the whole raw
message, attached ones included.

Nothing here logs or returns a filename, subject or address for logging
(CODING_STANDARDS 4.7): rejected attachments are reported as reason counts.
"""
import email
import re
from collections import Counter
from dataclasses import dataclass, field
from email import policy
from email.errors import MessageError
from email.message import EmailMessage, Message
from enum import StrEnum
from pathlib import Path

from app.models import Run
from app.services.config_service import MAX_UPLOAD_SIZE

# Mirrors WCM_EMAIL_DOMAINS in web_interface/frontend/src/components/adminUserRules.ts
# (#1289). Keep the two lists in step; subdomains of each count too.
WCM_EMAIL_DOMAINS: tuple[str, ...] = (
    "med.cornell.edu",
    "qatar-med.cornell.edu",
    "weill.cornell.edu",
    "nyp.org",
)

ACCEPTED_EXTENSIONS = (".docx", ".pdf")
EML_EXTENSION = ".eml"
ZIP_MAGIC = b"PK\x03\x04"
PDF_MAGIC = b"%PDF-"
# SES prepends this authserv-id to the one header whose verdicts we trust.
SES_AUTHSERV_ID = re.compile(r"^\s*amazonses\.com\s*;", re.IGNORECASE)
DMARC_PASS = re.compile(r"\bdmarc=pass\b", re.IGNORECASE)
SES_FAIL_VERDICT = "FAIL"
SES_PASS_VERDICT = "PASS"
SES_SPAM_HEADER = "X-SES-Spam-Verdict"
SES_VIRUS_HEADER = "X-SES-Virus-Verdict"
# Attachments inside a message attached to the outer one; one level only.
MAX_ATTACHED_MESSAGE_DEPTH = 1


class AttachmentReject(StrEnum):
    UNSUPPORTED_TYPE = "unsupported_type"
    NOT_A_REAL_FILE = "not_a_real_file"
    TOO_LARGE = "too_large"
    FILENAME_TOO_LONG = "filename_too_long"
    NESTED_TOO_DEEP = "nested_too_deep"
    NO_READABLE_TEXT = "no_readable_text"
    SCANNED_PDF = "scanned_pdf"
    UNREADABLE = "unreadable"


@dataclass(frozen=True, slots=True)
class Attachment:
    filename: str
    content: bytes


@dataclass(slots=True)
class ParsedMessage:
    message_id: str | None
    from_addr: str | None
    authenticated: bool
    spam_or_virus: bool
    attachments: list[Attachment] = field(default_factory=list)
    rejected: Counter = field(default_factory=Counter)


class UnparseableMessage(Exception):
    """The bytes are not a message we can read headers from."""


def is_wcm_address(addr: str) -> bool:
    """True when the address is on a WCM domain (or a subdomain of one)."""
    domain = addr.rpartition("@")[2].strip().lower()
    return any(domain == wcm or domain.endswith(f".{wcm}") for wcm in WCM_EMAIL_DOMAINS)


def _dmarc_passed(msg: EmailMessage) -> bool:
    """Only the FIRST Authentication-Results header counts, and only when it is
    SES's own: a sender can plant later ones, but SES prepends its own on top."""
    headers = msg.get_all("Authentication-Results") or []
    if not headers:
        return False
    first = str(headers[0])
    return bool(SES_AUTHSERV_ID.match(first) and DMARC_PASS.search(first))


def _ses_verdict(msg: EmailMessage, header: str) -> str:
    return str(msg.get(header, "")).strip().upper()


def _ses_failed(msg: EmailMessage) -> bool:
    """True when SES marked the message as spam or did not scan it clean of
    viruses (#1332). The virus check fails closed: only PASS is accepted. A missing header means the receipt rule
    has no ``ScanEnabled: true``, so nothing was scanned; GRAY and
    PROCESSING_FAILED mean SES could not say the attachments are safe. Spam
    rejects on FAIL only: it says nothing about the files, and GRAY is common
    for legitimate mail."""
    return (
        _ses_verdict(msg, SES_SPAM_HEADER) == SES_FAIL_VERDICT
        or _ses_verdict(msg, SES_VIRUS_HEADER) != SES_PASS_VERDICT
    )


def _sender(msg: EmailMessage) -> str | None:
    """The single From address, lowercased; None for zero or several."""
    header = msg["From"]
    addresses = header.addresses if header is not None else ()
    if len(addresses) != 1 or not addresses[0].addr_spec:
        return None
    return addresses[0].addr_spec.lower()


def _clean_filename(raw: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "", Path(raw.replace("\\", "/")).name)


def _validate_bytes(filename: str, content: bytes) -> AttachmentReject | None:
    ext = Path(filename).suffix.lower()
    if len(filename) > Run.FILENAME_MAX_LENGTH:
        return AttachmentReject.FILENAME_TOO_LONG
    if len(content) > MAX_UPLOAD_SIZE:
        return AttachmentReject.TOO_LARGE
    if ext == ".pdf" and not content.startswith(PDF_MAGIC):
        return AttachmentReject.NOT_A_REAL_FILE
    if ext == ".docx" and not content.startswith(ZIP_MAGIC):
        return AttachmentReject.NOT_A_REAL_FILE
    return None


def _is_inline_image(part: Message) -> bool:
    """A signature logo or pasted picture: an image with no filename or marked inline."""
    return part.get_content_maintype() == "image" and (
        not part.get_filename() or part.get_content_disposition() == "inline"
    )


def _is_body_text(part: Message) -> bool:
    return part.get_content_maintype() == "text" and not part.get_filename()


def _attached_message(part: Message) -> Message | None:
    """The message a part carries: a message/rfc822 part, or an ``.eml`` file."""
    if part.get_content_type() == "message/rfc822":
        payload = part.get_payload()
        return payload[0] if isinstance(payload, list) and payload else None
    if (part.get_filename() or "").lower().endswith(EML_EXTENSION):
        raw = part.get_payload(decode=True)
        return email.message_from_bytes(raw, policy=policy.default) if raw else None
    return None


def _collect(msg: Message, depth: int, out: ParsedMessage) -> None:
    """Walk ``msg``'s leaf parts into ``out``; recurse into an attached message
    once (``MAX_ATTACHED_MESSAGE_DEPTH``)."""
    for part in msg.walk():
        if part.is_multipart() or _is_inline_image(part) or _is_body_text(part):
            continue
        attached = _attached_message(part)
        if attached is None:
            _take_attachment(part, out)
        elif depth >= MAX_ATTACHED_MESSAGE_DEPTH:
            out.rejected[AttachmentReject.NESTED_TOO_DEEP] += 1
        else:
            _collect(attached, depth + 1, out)


def _take_attachment(part: Message, out: ParsedMessage) -> None:
    filename = _clean_filename(part.get_filename() or "")
    if Path(filename).suffix.lower() not in ACCEPTED_EXTENSIONS:
        out.rejected[AttachmentReject.UNSUPPORTED_TYPE] += 1
        return
    content = part.get_payload(decode=True) or b""
    reject = _validate_bytes(filename, content)
    if reject is not None:
        out.rejected[reject] += 1
        return
    out.attachments.append(Attachment(filename, content))


def parse_message(raw: bytes) -> ParsedMessage:
    """Read the trust headers and collect the CV attachments.

    Raises UnparseableMessage when the bytes are not a readable message.
    """
    try:
        msg = email.message_from_bytes(raw, policy=policy.default)
        if not msg.keys():
            raise UnparseableMessage("no headers")
        parsed = ParsedMessage(
            message_id=(str(msg["Message-ID"]).strip()[:255] if msg["Message-ID"] else None),
            from_addr=_sender(msg),
            authenticated=_dmarc_passed(msg),
            spam_or_virus=_ses_failed(msg),
        )
        _collect(msg, 0, parsed)
    except (MessageError, ValueError, LookupError) as e:
        raise UnparseableMessage(type(e).__name__) from e
    return parsed
