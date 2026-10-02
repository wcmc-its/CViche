"""The completion email for a batch (#1298, #1335).

One email per batch that asked for it, sent once when every one of its runs
is terminal: every auto-run batch (``run_batches.source == "email"``), and a
web batch whose submitter ticked "Email me when job completes"
(``run_batches.notify_on_complete``). A single upload with the box ticked is a
one-file batch. Other web batches never get it. Called after each run's
terminal transition is committed, from every path that makes one (the
orchestrator, the worker's dead-letter and watchdog, the stale-run reaper,
user cancel).

Exactly once across pods: the last two runs of a batch can finish on two pods
at the same moment, and both then see "all terminal". The winner is whoever's
``UPDATE ... SET completion_notified_at WHERE completion_notified_at IS NULL``
matches a row. The claim is made before the send, so a failed send is never
retried -- mail is a courtesy, as in app.services.mailer.

Counts only: no filename, no score. Never logs an address.
"""
import logging
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import BatchSource, Run, RunBatch, RunState, User
from app.services import mailer

logger = logging.getLogger(__name__)

# A cancelled run, like a failed one, produced nothing to download.
_NOT_READY = (RunState.FAILED, RunState.CANCELLED)


def notify_if_batch_complete(batch_id: str | None) -> None:
    """Best-effort entry point for a terminal hook: own session, never raises."""
    if batch_id is None:
        return
    db = SessionLocal()
    try:
        send_if_batch_complete(db, batch_id)
    except SQLAlchemyError:
        logger.exception("batch %s: completion check failed", batch_id)
        db.rollback()
    finally:
        db.close()


def send_if_batch_complete(db: Session, batch_id: str) -> bool:
    """Send the completion email if this call is the one that completes the
    batch. True only for that call."""
    status_counts = _status_counts(db, batch_id)
    if not _is_complete(db, batch_id, status_counts) or not _claim(db, batch_id):
        return False
    complete = status_counts.get(RunState.COMPLETE, 0)
    failed = sum(status_counts.get(state, 0) for state in _NOT_READY)
    recipient = db.execute(
        select(User.email, User.display_name).join(RunBatch, RunBatch.user_id == User.id).where(RunBatch.id == batch_id)
    ).one_or_none()
    if recipient is None or not recipient.email:
        return False
    single = db.scalar(select(Run.id).where(Run.batch_id == batch_id)) if complete + failed == 1 else None
    mailer.send(mailer.completion_notice(
        recipient.email, complete=complete, failed=failed, batch_id=batch_id, single_run_id=single,
        display_name=recipient.display_name))
    return True


def _status_counts(db: Session, batch_id: str) -> dict[str, int]:
    rows = db.execute(select(Run.status, func.count(Run.id)).where(Run.batch_id == batch_id).group_by(Run.status)).all()
    return {status: count for status, count in rows}


def _is_complete(db: Session, batch_id: str, status_counts: dict[str, int]) -> bool:
    """An unnotified batch that wants the email (an email batch, or a web
    batch with ``notify_on_complete``) whose runs all exist and are all
    terminal. Fewer runs than ``files_submitted`` means they are still being
    created (auto_run, or the browser's uploads)."""
    row = db.execute(
        select(RunBatch.source, RunBatch.notify_on_complete, RunBatch.files_submitted,
               RunBatch.completion_notified_at).where(RunBatch.id == batch_id)
    ).one_or_none()
    if row is None or row.completion_notified_at is not None:
        return False
    if row.source != BatchSource.EMAIL and not row.notify_on_complete:
        return False
    total = sum(status_counts.values())
    terminal = sum(count for status, count in status_counts.items() if status in Run.TERMINAL_RUN_STATUSES)
    return total > 0 and total >= row.files_submitted and terminal == total


def _claim(db: Session, batch_id: str) -> bool:
    """Atomically take the right to send; False if another pod already did."""
    won = db.execute(
        update(RunBatch)
        .where(RunBatch.id == batch_id, RunBatch.completion_notified_at.is_(None))
        .values(completion_notified_at=datetime.now())
    ).rowcount == 1
    db.commit()
    return won
