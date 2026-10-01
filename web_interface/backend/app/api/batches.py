"""Batch upload endpoints (#1114) and the queue overview the New run page reads.

HTTP only: every DB read and write lives in ``app.services.batch_service``.
Each file of a batch is still uploaded (POST /upload with ``batch_id``) and
started (POST /run/{id}/start) on its own; these routes create the batch
header, read a batch back, and report the queues.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.consent import require_current_consent
from app.database import get_db
from app.errors import bad_request, internal_error
from app.models import User
from app.rate_limiter import check_rate_limit
from app.schemas import (
    BatchCreateRequest, BatchCreateResponse, BatchDetail, BatchListResponse, QueueOverview,
)
from app.services import batch_service, notifications

router = APIRouter()


@router.post("/batches", response_model=BatchCreateResponse)
def create_batch(
    body: BatchCreateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BatchCreateResponse:
    """Create a batch for ``files_submitted`` files, before any is uploaded.

    Rejects more than MAX_BATCH_FILES files (400), and a batch larger than
    the user's remaining daily or monthly run quota with the same 429 body
    /upload answers, so the user learns it before uploading anything. Each
    /upload still checks the quota itself. Posts the one Teams "batch
    submitted" card. Refused first, like /upload, for a user who has not
    accepted the current consent terms.
    """
    require_current_consent(db, current_user)
    if body.files_submitted > batch_service.MAX_BATCH_FILES:
        raise bad_request(f"A batch can hold at most {batch_service.MAX_BATCH_FILES} files.")
    rate_limit_error = check_rate_limit(current_user, db, requested=body.files_submitted)
    if rate_limit_error:
        raise HTTPException(status_code=429, detail=rate_limit_error)
    try:
        batch = batch_service.create_batch(db, current_user, body.files_submitted)
    except batch_service.BatchIdAttemptsExhausted as e:
        raise internal_error("We couldn't create the batch. Please try again in a moment.") from e
    notifications.notify_batch_submitted(
        notifications.BatchFacts(id=batch.id, files_submitted=batch.files_submitted),
        submitter=current_user.display_name or current_user.email,
    )
    return BatchCreateResponse(id=batch.id)


@router.get("/batches", response_model=BatchListResponse)
def list_batches(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BatchListResponse:
    """Batches the caller may see -- their own, or every one for an admin --
    newest first. Feeds the Runs page's Batch filter."""
    return BatchListResponse(batches=batch_service.list_batches(db, current_user))


@router.get("/batches/{batch_id}", response_model=BatchDetail)
def get_batch(
    batch_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> BatchDetail:
    """The batch view: status counts and one row per run. 404 for a batch
    that does not exist or that the caller may not see."""
    batch = batch_service.get_visible_batch(db, batch_id, current_user)
    return batch_service.batch_detail(db, batch, current_user)


@router.get("/queue", response_model=QueueOverview)
def get_queue(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> QueueOverview:
    """Dispatch mode and, in queue mode, each queue's live workers, waiting
    runs and estimated wait -- for any signed-in user. Plain ``def``: its
    Valkey and DB reads are blocking, so FastAPI runs it in the threadpool."""
    return batch_service.queue_overview(db)
