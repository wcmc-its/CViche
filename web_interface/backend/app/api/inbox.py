"""The emailed-CV inbox (#1298): CVs that arrived by email wait here, per user,
until their owner signs in and submits them.

Every route is scoped to the signed-in user's own items, admins included.
Submitting goes through the same upload core /upload uses
(``create_run_from_bytes``), with the same consent gate, rate limit, batch and
duplicate-confirm rules. Like /upload, it only CREATES the runs: the client
then POSTs /run/{run_id}/start for each, exactly as a batch upload does.
"""
import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.consent import require_current_consent
from app.database import get_db
from app.errors import bad_request
from app.models import InboundFile, InboundFileStatus, User
from app.rate_limiter import check_rate_limit
from app.services import inbound_service
from app.services.batch_service import MAX_BATCH_FILES, get_owned_batch
from app.services.run_creation import (
    DuplicateInfo,
    RunRequest,
    create_run_from_bytes,
    duplicate_info,
)
from app.storage import get_storage
from app.storage.base import StorageKeyNotFound

logger = logging.getLogger(__name__)
router = APIRouter()


class InboxDuplicate(BaseModel):
    last_processed_on: str
    run_id: str | None = None


class InboxItem(BaseModel):
    id: int
    filename: str
    size_bytes: int
    received_at: datetime
    # Set when any run already holds this file's hash; run_id only for admins
    # and the run's own submitter (#1286).
    duplicate: InboxDuplicate | None = None


class InboxListResponse(BaseModel):
    items: list[InboxItem]


class InboxSubmitRequest(BaseModel):
    item_ids: list[int] = Field(min_length=1)
    submission_type: Literal["own_cv", "authorized_admin"]
    include_track_changes: bool = True
    include_classification_comments: bool = False
    strip_wcm_instructions: bool = True
    batch_id: str | None = None
    confirm_duplicate: bool = False


class InboxSubmitResult(BaseModel):
    id: int
    status: Literal["submitted", "failed"]
    run_id: str | None = None
    # On failure: the error code and message /upload would have answered
    # ("duplicate_file", "rate_limited", "bad_request", ...), plus
    # last_processed_on for a duplicate.
    error: str | None = None
    message: str | None = None
    last_processed_on: str | None = None


class InboxSubmitResponse(BaseModel):
    results: list[InboxSubmitResult]


class InboxDiscardResponse(BaseModel):
    id: int
    status: Literal["discarded"]


def _duplicate(info: DuplicateInfo | None) -> InboxDuplicate | None:
    return None if info is None else InboxDuplicate(last_processed_on=info.processed_on, run_id=info.run_id)


@router.get("/inbox", response_model=InboxListResponse)
def list_inbox(
    db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
) -> InboxListResponse:
    """The caller's pending emailed CVs, oldest first."""
    return InboxListResponse(items=[
        InboxItem(
            id=item.id, filename=item.filename, size_bytes=item.size_bytes, received_at=item.created_at,
            duplicate=_duplicate(duplicate_info(db, item.sha256, current_user)),
        )
        for item in inbound_service.list_pending(db, current_user)
    ])


@router.post("/inbox/{item_id}/discard", response_model=InboxDiscardResponse)
def discard_inbox_item(
    item_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user),
) -> InboxDiscardResponse:
    """Delete one pending item and its stored bytes. 404 for anyone else's."""
    item = inbound_service.get_pending_owned(db, current_user, item_id)
    inbound_service.discard(db, get_storage(), item)
    return InboxDiscardResponse(id=item.id, status="discarded")


def _failure(item_id: int, error: HTTPException) -> InboxSubmitResult:
    detail = error.detail if isinstance(error.detail, dict) else {}
    return InboxSubmitResult(
        id=item_id, status="failed", error=detail.get("error", "error"),
        message=detail.get("message"), last_processed_on=detail.get("last_processed_on"),
        run_id=detail.get("run_id"),
    )


async def _submit_one(
    db: Session, user: User, item: InboundFile, request: RunRequest,
) -> InboxSubmitResult:
    rate_limit_error = check_rate_limit(user, db)
    if rate_limit_error:
        return _failure(item.id, HTTPException(status_code=429, detail=rate_limit_error))
    storage = get_storage()
    object_key = f"{item.storage_key}{inbound_service.HELD_OBJECT_NAME}"
    try:
        content = await run_in_threadpool(storage.get_global, object_key)
    except StorageKeyNotFound:
        return _failure(item.id, bad_request("This file is no longer available.", "file_missing"))
    try:
        response = await create_run_from_bytes(
            db, user, filename=item.filename, content=content, content_type=None, request=request,
        )
    except HTTPException as e:
        return _failure(item.id, e)
    item.status = InboundFileStatus.SUBMITTED
    item.run_id = response.run_id
    db.commit()
    await run_in_threadpool(storage.delete_global_prefix, item.storage_key)
    return InboxSubmitResult(id=item.id, status="submitted", run_id=response.run_id)


@router.post("/inbox/submit", response_model=InboxSubmitResponse)
async def submit_inbox_items(
    body: InboxSubmitRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> InboxSubmitResponse:
    """Create a run for each chosen pending item. A refusal for one item
    (duplicate not confirmed, quota, unreadable) fails that item only and
    leaves it pending; whole-request problems (consent, a batch that is not
    yours, an id that is not your pending item) are 4xx."""
    require_current_consent(db, current_user)
    item_ids = list(dict.fromkeys(body.item_ids))
    if len(item_ids) > MAX_BATCH_FILES:
        raise bad_request(f"At most {MAX_BATCH_FILES} files can be submitted at once.")
    if body.batch_id is not None:
        get_owned_batch(db, body.batch_id, current_user)
    items = [inbound_service.get_pending_owned(db, current_user, item_id) for item_id in item_ids]
    request = RunRequest(
        submission_type=body.submission_type,
        include_track_changes=body.include_track_changes,
        include_classification_comments=body.include_classification_comments,
        strip_wcm_instructions=body.strip_wcm_instructions,
        batch_id=body.batch_id,
        confirm_duplicate=body.confirm_duplicate,
    )
    return InboxSubmitResponse(results=[
        await _submit_one(db, current_user, item, request) for item in items
    ])
