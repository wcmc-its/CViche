"""Email CV intake (#1298): decide on one raw message, hold its CVs in the
sender's inbox, poll S3 for new messages, expire stale items, and the inbox
reads the API serves.

Nothing here starts a run or spends LLM money: accepted CVs wait as
``inbound_files`` rows until their owner signs in and submits them. Nothing
logs a filename, subject or address (CODING_STANDARDS 4.7).
"""
import logging
import secrets
import threading
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.errors import not_found
from app.models import (
    InboundFile,
    InboundFileStatus,
    InboundMessage,
    InboundMessageStatus,
    InboundRejectReason,
    User,
    UserStatus,
)
from app.services import ed_access, mailer
from app.services.batch_service import MAX_BATCH_FILES
from app.services.inbound_autorun import AutoRunResult, auto_run
from app.services.inbound_mail import (
    Attachment,
    AttachmentReject,
    ParsedMessage,
    UnparseableMessage,
    is_wcm_address,
    parse_message,
)
from app.services.pdf_sandbox import (
    EncryptedPdfError,
    PdfBusyError,
    PdfTooComplexError,
    UnreadablePdfError,
    read_pdf,
)
from app.services.upload_validation import (
    MIN_EXTRACTED_CHARS,
    PDF_EXTENSION,
    _extract_text,
    _validate_docx_magic,
    docx_active_content,
    is_mostly_scanned,
)
from app.storage.base import RunStorage

logger = logging.getLogger(__name__)

# SES writes raw messages under this prefix of the CViche bucket.
INBOUND_PREFIX = "inbound/"
# Where held CV bytes live until submitted, discarded or expired.
HELD_PREFIX = "inbound-files/"
HELD_OBJECT_NAME = "file"
POLL_INTERVAL_SECONDS = 60
# Pending items older than this are expired and their bytes deleted.
PENDING_EXPIRY = timedelta(days=14)
# A mail loop must not fill storage: most pending items one user may hold.
MAX_PENDING_PER_USER = 100

# ponytail: polling the prefix every 60 s instead of S3 -> SQS events. Fine at
# a handful of CV emails a day; move to SQS if volume or latency ever matters.


class TransientIntakeError(Exception):
    """Try this message again on the next poll; do not record it as decided."""


@dataclass(frozen=True, slots=True)
class Verdict:
    user: User | None
    reason: InboundRejectReason | None


def _find_user(db: Session, addr: str) -> User | None:
    return db.scalar(select(User).where(func.lower(User.email) == addr.lower()))


def _passes_access_check(db: Session, user: User) -> bool:
    """The ED access-group check auth.py runs per request; any failure denies."""
    try:
        ed_access.verify_ed_access(user, db)
    except (ed_access.EdCwidMissing, ed_access.EdUnverifiable, ed_access.EdNotInAccessGroup):
        return False
    return True


def _screen_sender(db: Session, parsed: ParsedMessage) -> Verdict:
    """Outer-message checks, cheapest first. A reason that is replyable (see
    mailer.REPLYABLE_REASONS) is only ever returned with the matched user."""
    if not parsed.authenticated:
        return Verdict(None, InboundRejectReason.NOT_AUTHENTICATED)
    if parsed.spam_or_virus:
        return Verdict(None, InboundRejectReason.SPAM_OR_VIRUS)
    if not parsed.from_addr or not is_wcm_address(parsed.from_addr):
        return Verdict(None, InboundRejectReason.SENDER_DOMAIN)
    user = _find_user(db, parsed.from_addr)
    if user is None:
        return Verdict(None, InboundRejectReason.UNKNOWN_USER)
    if not user.consent_version:
        return Verdict(user, InboundRejectReason.NEVER_CONSENTED)
    if user.status != UserStatus.ACTIVE:
        return Verdict(user, InboundRejectReason.USER_DISABLED)
    if not _passes_access_check(db, user):
        return Verdict(user, InboundRejectReason.NOT_IN_ACCESS_GROUP)
    return Verdict(user, None)


def _readable_reject(att: Attachment) -> AttachmentReject | None:
    """The upload validators /upload applies to bytes: zip-bomb bound, docx
    structure and active content, the PDF sandbox, and the minimum text length."""
    ext = "." + att.filename.rsplit(".", 1)[-1].lower()
    if ext == ".docx" and not _validate_docx_magic(att.content):
        return AttachmentReject.NOT_A_REAL_FILE
    if ext == ".docx" and docx_active_content(att.content) is not None:
        return AttachmentReject.ACTIVE_CONTENT
    try:
        if ext == PDF_EXTENSION:
            pdf = read_pdf(att.content)
            if is_mostly_scanned(pdf):
                return AttachmentReject.SCANNED_PDF
            text = pdf.text
        else:
            text = _extract_text(att.content, ext)
    except PdfBusyError as e:
        raise TransientIntakeError("pdf sandbox busy") from e
    except (EncryptedPdfError, PdfTooComplexError, UnreadablePdfError):
        return AttachmentReject.UNREADABLE
    if text is not None and len(text.strip()) < MIN_EXTRACTED_CHARS:
        return AttachmentReject.NO_READABLE_TEXT
    return None


def _readable_attachments(parsed: ParsedMessage) -> list[Attachment]:
    keep = []
    for att in parsed.attachments:
        reject = _readable_reject(att)
        if reject is None:
            keep.append(att)
        else:
            parsed.rejected[reject] += 1
    return keep


def _pending_count(db: Session, user: User) -> int:
    return db.scalar(
        select(func.count(InboundFile.id)).where(
            InboundFile.user_id == user.id, InboundFile.status == InboundFileStatus.PENDING
        )
    )


def _summarise(rejected: Counter) -> str | None:
    return ",".join(f"{reason}:{n}" for reason, n in sorted(rejected.items())) or None


def _hold_files(db: Session, message: InboundMessage, user: User, files: list[Attachment],
                storage: RunStorage) -> tuple[list[str], list[InboundFile]]:
    """Store each CV's bytes and add its row; returns the storage prefixes
    written (so a failed commit can delete them) and the rows, in received order."""
    written, rows = [], []
    for att in files:
        prefix = f"{HELD_PREFIX}{secrets.token_hex(16)}/"
        storage.put_global(f"{prefix}{HELD_OBJECT_NAME}", att.content)
        written.append(prefix)
        row = InboundFile(
            inbound_message_id=message.id, user_id=user.id, filename=att.filename,
            size_bytes=len(att.content), sha256=sha256(att.content).hexdigest(), storage_key=prefix,
            status=InboundFileStatus.PENDING,
        )
        db.add(row)
        rows.append(row)
    return written, rows


def _decide(db: Session, parsed: ParsedMessage, message: InboundMessage) -> tuple[Verdict, list[Attachment]]:
    """Fill ``message`` with the decision; returns the verdict and the CVs to hold."""
    verdict = _screen_sender(db, parsed)
    files: list[Attachment] = []
    reason = verdict.reason
    if reason is None:
        if len(parsed.attachments) > MAX_BATCH_FILES:
            reason = InboundRejectReason.TOO_MANY_FILES
        else:
            files = _readable_attachments(parsed)
            if not files:
                reason = InboundRejectReason.NO_VALID_ATTACHMENTS
            elif _pending_count(db, verdict.user) + len(files) > MAX_PENDING_PER_USER:
                reason = InboundRejectReason.INBOX_FULL
    if reason is not None:
        files = []
    message.status = InboundMessageStatus.REJECTED if reason else InboundMessageStatus.ACCEPTED
    message.reject_reason = reason or _summarise(parsed.rejected)
    message.file_count = len(files)
    return Verdict(verdict.user, reason), files


def _notify(parsed: ParsedMessage, verdict: Verdict, result: AutoRunResult | None) -> None:
    """Mail the known, DMARC-passed sender (never anyone else), at the
    address on their account rather than the header they sent."""
    user = verdict.user
    if user is None or not user.email:
        return
    if verdict.reason is None and result is not None:
        mailer.send(mailer.processing_notice(
            user.email, runs=result.runs, held=result.held, batch_id=result.batch_id,
            outdated_consent=result.outdated_consent, skipped=sum(parsed.rejected.values()),
            consent_date=user.consent_date, display_name=user.display_name,
        ))
    elif verdict.reason in mailer.REPLYABLE_REASONS:
        mailer.send(mailer.rejection(user.email, verdict.reason, user.display_name))


def _delete_held(storage: RunStorage, prefixes: list[str]) -> None:
    for prefix in prefixes:
        storage.delete_global_prefix(prefix)


def process_message(db: Session, storage: RunStorage, s3_key: str, raw: bytes) -> bool:
    """Decide on one raw message and commit the decision in one transaction.

    The ``inbound_messages`` row is flushed first: its unique ``s3_key`` makes
    a second poller racing us on the same key fail here, before any bytes are
    stored or mail sent. Returns False when another poller already took it.
    """
    message = InboundMessage(s3_key=s3_key, status=InboundMessageStatus.FAILED, file_count=0)
    db.add(message)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return False
    try:
        parsed = parse_message(raw)
    except UnparseableMessage:
        message.status, message.reject_reason = InboundMessageStatus.REJECTED, InboundRejectReason.UNPARSEABLE
        db.commit()
        return True
    verdict, files = _decide(db, parsed, message)
    message.message_id = parsed.message_id
    if parsed.authenticated:  # no address of an unauthenticated sender is kept
        message.from_addr = parsed.from_addr
    message.user_id = verdict.user.id if verdict.user else None
    written: list[str] = []
    try:
        written, rows = _hold_files(db, message, verdict.user, files, storage) if files else ([], [])
        db.commit()
    except Exception:
        db.rollback()
        _delete_held(storage, written)
        raise
    logger.info("inbound message decided status=%s reason=%s files=%d",
                message.status, message.reject_reason, message.file_count)
    result = _auto_run_or_hold(db, storage, verdict.user, rows) if rows else None
    _notify(parsed, verdict, result)
    return True


def _auto_run_or_hold(db: Session, storage: RunStorage, user: User, rows: list[InboundFile]) -> AutoRunResult:
    """Start what may start; if that fails for any reason the files stay
    pending in the inbox and the sender is told they are waiting."""
    try:
        return auto_run(db, storage, user, rows)
    except Exception:
        db.rollback()
        logger.exception("inbound auto-run failed; the files stay pending")
        return AutoRunResult(0, len(rows))


def _record_failure(session_factory: Callable[[], Session], s3_key: str) -> None:
    with session_factory() as db:
        db.add(InboundMessage(
            s3_key=s3_key, status=InboundMessageStatus.FAILED,
            reject_reason=InboundRejectReason.PROCESSING_ERROR, file_count=0,
        ))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()


def _process_key(session_factory: Callable[[], Session], storage: RunStorage, s3_key: str) -> None:
    """One message, its own transaction. A failure is logged and recorded and
    never propagates: one bad message must not stop the loop."""
    try:
        raw = storage.get_global(s3_key)
    except Exception:  # noqa: BLE001 -- S3 read failures are retried next poll
        logger.warning("inbound read failed; will retry", exc_info=True)
        return
    with session_factory() as db:
        try:
            process_message(db, storage, s3_key, raw)
            return
        except TransientIntakeError:
            db.rollback()
            logger.warning("inbound message deferred to the next poll")
            return
        except Exception:  # noqa: BLE001 -- the loop boundary: record and carry on
            db.rollback()
            logger.exception("inbound message failed")
    _record_failure(session_factory, s3_key)


def poll_once(session_factory: Callable[[], Session], storage: RunStorage) -> int:
    """Process every ``inbound/`` key not yet in ``inbound_messages``; returns how many."""
    keys = storage.list_global(INBOUND_PREFIX)
    with session_factory() as db:
        known = set(db.scalars(select(InboundMessage.s3_key)))
    fresh = [key for key in keys if key not in known]
    for key in fresh:
        _process_key(session_factory, storage, key)
    return len(fresh)


def expire_stale(session_factory: Callable[[], Session], storage: RunStorage,
                 now: datetime | None = None) -> int:
    """Expire pending items past ``PENDING_EXPIRY`` and delete their bytes."""
    cutoff = (now or datetime.now()) - PENDING_EXPIRY
    expired = 0
    with session_factory() as db:
        items = db.scalars(select(InboundFile).where(
            InboundFile.status == InboundFileStatus.PENDING, InboundFile.created_at < cutoff
        )).all()
        for item in items:
            try:
                storage.delete_global_prefix(item.storage_key)
            except Exception:  # noqa: BLE001 -- keep the row pending; retried next pass
                logger.warning("expiry could not delete held bytes; will retry", exc_info=True)
                continue
            item.status = InboundFileStatus.EXPIRED
            db.commit()
            expired += 1
    return expired


def run_intake_loop(session_factory: Callable[[], Session], storage: RunStorage,
                    stop: threading.Event) -> None:
    """The worker's intake thread: poll, expire, sleep, until ``stop`` is set."""
    while not stop.is_set():
        try:
            poll_once(session_factory, storage)
            expire_stale(session_factory, storage)
        except Exception:  # noqa: BLE001 -- a bad pass must not end the loop
            logger.exception("inbound intake pass failed")
        stop.wait(POLL_INTERVAL_SECONDS)


# --- Inbox reads and writes behind /api/inbox ----------------------------------


def list_pending(db: Session, user: User) -> list[InboundFile]:
    return list(db.scalars(
        select(InboundFile)
        .where(InboundFile.user_id == user.id, InboundFile.status == InboundFileStatus.PENDING)
        .order_by(InboundFile.created_at, InboundFile.id)
    ))


def get_pending_owned(db: Session, user: User, item_id: int) -> InboundFile:
    """The caller's own pending item; 404 for anyone else's, an unknown id or
    an item no longer pending (admins included: only their own)."""
    item = db.get(InboundFile, item_id)
    if item is None or item.user_id != user.id or item.status != InboundFileStatus.PENDING:
        raise not_found("Inbox item not found")
    return item


def discard(db: Session, storage: RunStorage, item: InboundFile) -> None:
    """Delete the held bytes, then mark the item discarded."""
    storage.delete_global_prefix(item.storage_key)
    item.status = InboundFileStatus.DISCARDED
    db.commit()
