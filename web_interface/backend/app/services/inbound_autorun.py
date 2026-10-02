"""Start runs for an accepted emailed message (#1298, revised 2026-10-02): the
sender's prior consent stands in for the attestation, so their CVs run at once
-- as one batch, under their normal quota -- except those held for them to
confirm in the UI: files already processed (#1286), files beyond the remaining
quota, everything when their consent is out of date, and everything when the
deployment cannot queue runs for the worker.

Reuses the web paths: batch_service.create_batch and the Teams batch card as
POST /batches does, run_creation.create_run_from_bytes as /upload does, and the
same flip-and-enqueue as POST /run/{id}/start in queue mode. Never logs a
filename, subject or address (CODING_STANDARDS 4.7).
"""
import asyncio
import logging
from dataclasses import dataclass

import redis
from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.consent import has_current_consent
from app.models import InboundFile, InboundFileStatus, User
from app.pipeline import concurrency, run_queue
from app.rate_limiter import check_rate_limit
from app.services import batch_service, notifications
from app.services.run_creation import RunRequest, create_run_from_bytes, duplicate_info
from app.services.run_service import flip_to_queued, revert_queued
from app.storage.base import RunStorage

logger = logging.getLogger(__name__)

SUBMISSION_TYPES = ("own_cv", "authorized_admin")
# When the user has not chosen one on the consent page.
FALLBACK_SUBMISSION_TYPE = "authorized_admin"
# Where the held bytes of an item live (see inbound_service.HELD_OBJECT_NAME).
HELD_OBJECT_NAME = "file"


@dataclass(frozen=True, slots=True)
class AutoRunResult:
    runs: int
    held: int
    batch_id: str | None = None
    outdated_consent: bool = False


def submission_type_for(user: User) -> str:
    chosen = user.default_submission_type
    return chosen if chosen in SUBMISSION_TYPES else FALLBACK_SUBMISSION_TYPE


def _can_queue_runs() -> bool:
    """Only queue mode lets the worker's run reach an executor: in_process
    runs live in a web pod's BackgroundTask, which this process is not."""
    if concurrency.dispatch_mode() != "queue" or not run_queue.is_configured():
        logger.warning("email intake: runs cannot be queued (dispatch mode is not queue, or no "
                       "CVICHE_REDIS_URL); holding every file for confirmation")
        return False
    return True


def _runnable_count(db: Session, user: User, wanted: int) -> int:
    """How many of ``wanted`` new runs the user's daily and monthly limits allow."""
    for count in range(wanted, 0, -1):
        if check_rate_limit(user, db, requested=count) is None:
            return count
    return 0


def _queue_created_run(db: Session, run_id: str, batch_id: str) -> bool:
    """POST /run/{id}/start's queue-mode path for a brand-new run: flip
    created -> queued, XADD the token on the batch queue, revert the flip if the
    XADD fails."""
    prior = flip_to_queued(db, run_id, ("created",))
    if not prior.flipped:
        return False
    try:
        run_queue.enqueue(run_id, run_queue.queue_for(batch_id))
    except redis.exceptions.RedisError:
        logger.exception("email intake: enqueue failed for run %s; reverting", run_id)
        revert_queued(db, run_id, prior)
        return False
    return True


def _fresh_items(db: Session, user: User, items: list[InboundFile]) -> list[InboundFile]:
    """The items no run holds the hash of; a duplicate is never run automatically."""
    return [item for item in items if duplicate_info(db, item.sha256, user) is None]


def _run_item(db: Session, storage: RunStorage, user: User, item: InboundFile, request: RunRequest) -> bool:
    """Create and queue one run from a held item. False leaves it pending."""
    content = storage.get_global(f"{item.storage_key}{HELD_OBJECT_NAME}")
    try:
        response = asyncio.run(create_run_from_bytes(
            db, user, filename=item.filename, content=content, content_type=None, request=request))
    except HTTPException as e:
        logger.info("email intake: a file was not run (%s)", e.detail.get("error") if isinstance(e.detail, dict) else e.status_code)
        return False
    item.status = InboundFileStatus.SUBMITTED
    item.run_id = response.run_id
    db.commit()
    storage.delete_global_prefix(item.storage_key)
    if not _queue_created_run(db, response.run_id, request.batch_id):
        logger.error("email intake: run %s was created but not queued; start it from Runs", response.run_id)
    return True


def auto_run(db: Session, storage: RunStorage, user: User, items: list[InboundFile]) -> AutoRunResult:
    """Run what may run, in received order; the rest stay pending for the UI."""
    if not has_current_consent(db, user):
        return AutoRunResult(0, len(items), outdated_consent=True)
    if not _can_queue_runs():
        return AutoRunResult(0, len(items))
    budget = _runnable_count(db, user, len(_fresh_items(db, user, items)))
    if budget == 0:
        return AutoRunResult(0, len(items))
    batch = batch_service.create_batch(db, user, budget)
    notifications.notify_batch_submitted(
        notifications.BatchFacts(id=batch.id, files_submitted=batch.files_submitted),
        submitter=user.display_name or user.email,
    )
    request = RunRequest(submission_type=submission_type_for(user), batch_id=batch.id)
    started = 0
    for item in items:
        if started == budget:
            break
        # A duplicate is refused by the upload core itself (confirm_duplicate stays False).
        if _run_item(db, storage, user, item, request):
            started += 1
    return AutoRunResult(started, len(items) - started, batch.id)
