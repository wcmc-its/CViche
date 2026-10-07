"""File upload API endpoint."""
import hashlib
import json
import logging
import secrets
import string
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Literal, NamedTuple, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import can_see_cost, get_current_user, visible_cost
from app.consent import require_current_consent
from app.database import get_db
from app.errors import bad_request, duplicate_file, internal_error
from app.models import Run, Step, User
from app.pipeline.step_registry import STEP_REGISTRY
from app.rate_limiter import check_rate_limit
from app.schemas import UploadResponse
from app.services import run_creation
from app.services.batch_service import MAX_BATCH_FILES, get_owned_batch
from app.services.config_service import (
    BASE_OVERHEAD_SECONDS,
    ESTIMATE_RATE_LIMIT_MAX,
    ESTIMATE_RATE_LIMIT_WINDOW_SECONDS,
    MAX_UPLOAD_SIZE,
    TIME_PER_1K_TOKENS,
    get_estimate_model_name,
    get_estimated_run_cost,
)
from app.services.input_format import detect_input_format_or_none
from app.services.pdf_sandbox import (
    PDF_BUSY_MESSAGE,
    PDF_TOO_COMPLEX_MESSAGE,
    PDF_UNREADABLE_MESSAGE,
    EncryptedPdfError,
    PdfBusyError,
    PdfTooComplexError,
    UnreadablePdfError,
)
from app.services.run_creation import (  # noqa: F401 -- re-exported: other modules and tests import these from here
    _ENCRYPTED_PDF_MESSAGE,
    _PDF_BUSY_RETRY_AFTER_SECONDS,
    _RUN_ID_ATTEMPTS,
    DUPLICATE_DATE_FORMAT,
    DuplicateInfo,
    RunIdAttemptsExhausted,
    RunRequest,
    _add_pending_steps,
    _archive_or_502,
    _compensate_failed_run,
    _estimate_char_count,
    _extract_text_or_400,
    _page_ranges,
    _read_upload_text,
    _reject_mostly_scanned_pdf,
    _reject_unconfirmed_duplicate,
    _unlink_best_effort,
    compensated_run_creation,
    create_run_archive,
    create_run_from_bytes,
    duplicate_info,
    estimate_run_seconds,
    generate_run_id,
)
from app.services.run_service import UPLOAD_DIR, latest_run_with_hash
from app.services.template_warning import detect_wcm_template
from app.services.upload_validation import (  # noqa: F401 -- re-exported: tests patch these names here
    _DOCX_MAX_ENTRIES,
    _DOCX_MAX_UNCOMPRESSED_BYTES,
    _DOCX_READ_ERRORS,
    MIN_EXTRACTED_CHARS,
    PDF_EXTENSION,
    PDF_MAGIC,
    SCANNED_PAGE_REJECT_SHARE,
    ZIP_MAGIC,
    _extract_text,
    _validate_docx_magic,
    _validate_pdf_magic,
)
from app.storage import get_storage
from app.storage.base import StorageKeyExists

logger = logging.getLogger(__name__)
# Extensions the upload API accepts, matching the frontend's guard
# (UploadPage.tsx's processFile). A PDF is stored and archived as-is; the
# orchestrator converts it to the run's private docx copy before stage 1a
# (#806), since every pipeline reader is python-docx only (#524).
ALLOWED_UPLOAD_EXTENSIONS = (".docx", PDF_EXTENSION)
_UNSUPPORTED_TYPE_HINT = (
    "Only .docx and .pdf files are supported. "
    "Please convert your file to .docx or .pdf before uploading."
)


# Bytes read per chunk while bounding an upload body (#793): large enough that
# a normal CV (a few hundred KB) reads in one or two chunks, small enough that
# a request over the size cap is caught well before the whole body is buffered.
_UPLOAD_READ_CHUNK_SIZE = 1024 * 1024


async def _read_bounded(file: UploadFile, max_size: int) -> bytes:
    """Read an upload in bounded chunks, aborting once max_size is exceeded.

    Unlike ``await file.read()`` followed by a size check, this never buffers
    more than ``max_size`` plus one chunk of an oversized body before
    rejecting it (#793).
    """
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_READ_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > max_size:
            # Production caps are always a whole number of MB (config_service
            # builds MAX_UPLOAD_SIZE as int(CVICHE_MAX_UPLOAD_MB)*1024*1024),
            # so the cap is always printed in whole MB -- no KB/bytes
            # fallback for a sub-1MB cap, which only a test ever patches in.
            raise bad_request(f"File too large. Maximum size is {max_size // (1024 * 1024)} MB.")
        chunks.append(chunk)
    return b"".join(chunks)


class _EstimatePerUserWindow:
    """Per-pod, in-memory, fixed-window call counter, one window per user id.

    Distinct from check_rate_limit's DB-backed run quota (#795): this counts
    calls directly, not Run rows, so it catches a user who never goes over
    their run quota but calls /estimate repeatedly. `clock` is injectable so
    tests can control window elapsing without sleeping or patching the real
    clock.

    ponytail: the count is per POD and resets on restart -- it does not
    share state across the up to 3 backend pods in prod, so a user's real
    ceiling is close to max_calls * pod_count per window, not max_calls.
    Upgrade path: back this with the same Valkey URL LoginThrottle
    (app/login_throttle.py) uses, CVICHE_REDIS_URL, if a pod-shared bound is
    ever needed.
    """

    def __init__(
        self,
        max_calls: int,
        window_seconds: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._max_calls = max_calls
        self._window_seconds = window_seconds
        self._clock = clock
        # user_id -> (window_start, count_in_window)
        self._windows: dict[int, tuple[float, int]] = {}
        # estimate_processing is async, so today allow() runs on the event
        # loop; the lock keeps the read-modify-write safe if it is ever
        # called from a threadpool.
        self._lock = threading.Lock()

    def allow(self, user_id: int) -> bool:
        """Record one call for user_id and return whether it is within the
        current window's budget. A window that has fully elapsed since the
        user's last call starts fresh rather than carrying its count over."""
        now = self._clock()
        with self._lock:
            window_start, count = self._windows.get(user_id, (now, 0))
            if now - window_start >= self._window_seconds:
                window_start, count = now, 0
            if count >= self._max_calls:
                return False
            self._windows[user_id] = (window_start, count + 1)
            return True

    def reset(self) -> None:
        """Test-only: clear every user's window."""
        with self._lock:
            self._windows.clear()


_estimate_rate_limiter = _EstimatePerUserWindow(
    ESTIMATE_RATE_LIMIT_MAX, ESTIMATE_RATE_LIMIT_WINDOW_SECONDS
)


def _check_estimate_rate_limit(user_id: int) -> dict | None:
    """Same {error, message, details} shape check_rate_limit returns, so
    estimate_processing raises the identical 429 either way (#795)."""
    if _estimate_rate_limiter.allow(user_id):
        return None
    return {
        "error": "rate_limited",
        "message": (
            f"Estimate limit of {ESTIMATE_RATE_LIMIT_MAX} calls per "
            f"{ESTIMATE_RATE_LIMIT_WINDOW_SECONDS} seconds reached."
        ),
        "details": {
            "limit_type": "estimate",
            "limit": ESTIMATE_RATE_LIMIT_MAX,
            "window_seconds": ESTIMATE_RATE_LIMIT_WINDOW_SECONDS,
        },
    }


router = APIRouter()


class EstimateResponse(BaseModel):
    """Response model for cost/time estimation."""
    document_tokens: int
    text_characters: int
    # Cost fields are None for non-admins (#1111).
    estimated_cost_min: float | None
    estimated_cost_max: float | None
    estimated_time_seconds_min: int
    estimated_time_seconds_max: int
    num_steps: int
    filename: str
    file_size_kb: float
    pricing_model: str | None
    # True when the document's text couldn't be read and text_characters is
    # the fixed fallback guess, not a measurement (#794).
    text_characters_is_guess: bool = False
    # A PDF's image-only pages, 1-based, whose text the run can't read
    # (#1282). Fewer than SCANNED_PAGE_REJECT_SHARE of its pages, or the
    # file would have been refused.
    scanned_pages: list[int] = []


@router.post("/upload", response_model=UploadResponse)
async def upload_cv(
    file: UploadFile = File(...),
    # Output-rendering options (issue #153). Sent as multipart form fields
    # alongside the file. Defaults mirror the Run model column defaults
    # (track changes ON, classification comments OFF) and are applied when the
    # fields are absent, keeping older clients backward compatible.
    include_track_changes: bool = Form(True),
    include_classification_comments: bool = Form(False),
    strip_wcm_instructions: bool = Form(True),
    # Per-upload role attestation (Faculty Affairs). Required: every run must
    # record whether the submitter was the faculty member or an administrator
    # who attested to having the faculty member's permission.
    submission_type: Literal["own_cv", "authorized_admin"] = Form(...),
    # The batch this file belongs to (#1114), from POST /batches. Optional:
    # absent, the run is a single upload exactly as before.
    batch_id: str | None = Form(None),
    # Set by the UI after the user agrees to re-process a file already run (#1286).
    confirm_duplicate: bool = Form(False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a CV file and create a new pipeline run."""

    # Check consent at upload time (not just page visit)
    require_current_consent(db, current_user)

    # A batch_id must name one of the caller's own batches (404 for unknown
    # and someone else's alike) -- checked before anything is read or archived.
    if batch_id is not None:
        get_owned_batch(db, batch_id, current_user)

    # Validate file type
    if not file.filename:
        raise bad_request("No filename provided")

    # Bound BEFORE create_run_archive runs (#796): Run.filename is
    # String(255) and previously failed only at db.commit(), after the
    # durable archive was already written.
    if len(file.filename) > Run.FILENAME_MAX_LENGTH:
        raise bad_request(
            f"Filename too long ({len(file.filename)} characters). "
            f"Maximum length is {Run.FILENAME_MAX_LENGTH} characters."
        )

    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise bad_request(f"Unsupported file type: {file_ext}. {_UNSUPPORTED_TYPE_HINT}")

    # Check rate limit (after file validation so bad uploads don't count)
    rate_limit_error = check_rate_limit(current_user, db)
    if rate_limit_error:
        raise HTTPException(status_code=429, detail=rate_limit_error)

    # Read file content in bounded chunks so an oversized body is never fully
    # buffered before being rejected (#793).
    content = await _read_bounded(file, MAX_UPLOAD_SIZE)

    return await create_run_from_bytes(
        db, current_user, filename=file.filename, content=content, content_type=file.content_type,
        request=RunRequest(
            submission_type=submission_type,
            include_track_changes=include_track_changes,
            include_classification_comments=include_classification_comments,
            strip_wcm_instructions=strip_wcm_instructions,
            batch_id=batch_id,
            confirm_duplicate=confirm_duplicate,
        ),
    )


class BatchEstimateFile(BaseModel):
    """One file of a multi-file /estimate (#1114): its estimate, or the
    reason it could not be estimated -- the same 400 message /estimate gives
    for that file alone (unsupported type, too large, unreadable PDF...)."""
    filename: str
    estimate: EstimateResponse | None = None
    error: str | None = None


class BatchEstimateResponse(BaseModel):
    """Multi-file /estimate (#1114): a row per file plus totals over the
    files that could be estimated. Cost fields are None for non-admins, as
    on the single-file response (#1111)."""
    files: list[BatchEstimateFile]
    estimated_time_seconds_min: int
    estimated_time_seconds_max: int
    estimated_cost_min: float | None
    estimated_cost_max: float | None
    num_steps: int
    pricing_model: str | None


def _estimate_file_ext(file: UploadFile) -> str:
    """The file's lower-cased extension, or a 400 for a missing name or an
    unsupported type."""
    if not file.filename:
        raise bad_request("No filename provided")
    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise bad_request(f"Unsupported file type: {file_ext}. {_UNSUPPORTED_TYPE_HINT}")
    return file_ext


def _check_estimate_limits(current_user: User, db: Session) -> None:
    """Both /estimate budgets, raising their 429. A call counts once against
    each, however many files it carries (#1114)."""
    # Per-pod, per-user in-memory budget (#795), checked first since it's
    # cheaper than the DB-backed check below -- a user already over it never
    # costs a query. Both checks run before the body is read (_read_bounded)
    # or parsed (_extract_text).
    estimate_limit_error = _check_estimate_rate_limit(current_user.id)
    if estimate_limit_error:
        raise HTTPException(status_code=429, detail=estimate_limit_error)

    # Also rate-limited the same as /upload (#795): reuses /upload's
    # DB-backed per-run quota, which on its own only blocks a user already
    # over their run quota -- an estimate creates no Run row. The budget
    # above is what actually caps /estimate's own call volume.
    rate_limit_error = check_rate_limit(current_user, db)
    if rate_limit_error:
        raise HTTPException(status_code=429, detail=rate_limit_error)


async def _estimate_one(file: UploadFile, file_ext: str, current_user: User) -> EstimateResponse:
    """Read, validate and size one file for /estimate; raises the same 400s
    /upload would for an unreadable or mislabeled file."""
    # Read file content in bounded chunks so an oversized body is never fully
    # buffered before being rejected (#793).
    content = await _read_bounded(file, MAX_UPLOAD_SIZE)

    # Validate magic bytes
    if file_ext == PDF_EXTENSION and not run_creation._validate_pdf_magic(content):
        logger.warning("[SECURITY] Rejected estimate: file claims .pdf but magic bytes do not match")
        raise bad_request("File content does not match .pdf format. The file may be corrupted or mislabeled.")
    if file_ext == ".docx" and not run_creation._validate_docx_magic(content):
        logger.warning("[SECURITY] Rejected estimate: file claims .docx but magic bytes do not match")
        raise bad_request("File content does not match .docx format. The file may be corrupted or mislabeled.")
    if file_ext == ".docx":
        await run_creation._reject_active_docx_content(content)

    file_size_kb = len(content) / 1024

    # Extract text through the same implementation /upload uses (#794) --
    # this endpoint used to re-walk the docx paragraphs/tables inline, which
    # could compute a different text_char_count for the same file. Off the
    # event loop, same as /upload (#793).
    extracted, scanned_pages = await _extract_text_or_400(content, file_ext)
    if extracted is None:
        # _extract_text already logged the specific read failure (§5.4). No
        # filename here (CODING_STANDARDS §4.7): CV filenames usually carry
        # the owner's name, and every log line already carries the request id
        # via RequestIDFilter.
        logger.warning("Estimate falling back to a fixed char-count guess")
    text_char_count = _estimate_char_count(extracted)

    # Estimate tokens (roughly 4 characters per token for English text)
    estimated_tokens = text_char_count // 4

    # Cost estimation. Driven by the entry-classification-aware model in
    # unified_pipeline.config: stage 3b re-sends a large static taxonomy prompt
    # once per hierarchy group, so cost scales with entry COUNT (estimated from
    # document text), not document length. Priced at the active model from
    # llm_config.yaml. Covers all stages: hierarchy/entry extraction, taxonomy
    # mapping, field extraction, research summary, enrichment, and doc gen.
    cost_min, cost_max = get_estimated_run_cost(text_char_count)

    # Time estimate (input-scaled). Shared helper with the per-run value the
    # stall watchdog uses, so the quote shown here and the threshold applied
    # during the run can't drift apart.
    time_min, time_max = estimate_run_seconds(text_char_count)
    num_stages = len(STEP_REGISTRY)

    cost_min = max(cost_min, 0.10)  # At least $0.10
    cost_max = max(cost_max, cost_min * 1.3)  # At least 1.3x the min

    return EstimateResponse(
        document_tokens=estimated_tokens,
        text_characters=text_char_count,
        estimated_cost_min=visible_cost(current_user, round(cost_min, 3)),
        estimated_cost_max=visible_cost(current_user, round(cost_max, 3)),
        estimated_time_seconds_min=time_min,
        estimated_time_seconds_max=time_max,
        num_steps=num_stages,
        filename=file.filename or "",
        file_size_kb=round(file_size_kb, 1),
        pricing_model=get_estimate_model_name() if can_see_cost(current_user) else None,
        text_characters_is_guess=extracted is None,
        scanned_pages=scanned_pages,
    )


async def _estimate_batch_file(file: UploadFile, current_user: User) -> BatchEstimateFile:
    """One row of a multi-file /estimate. A 400 for this file becomes the
    row's ``error`` so one bad file doesn't cost the whole batch its quote;
    any other failure (a full PDF sandbox's 503) still fails the call."""
    filename = file.filename or ""
    try:
        file_ext = _estimate_file_ext(file)
        estimate = await _estimate_one(file, file_ext, current_user)
    except HTTPException as e:
        if e.status_code != 400:
            raise
        return BatchEstimateFile(filename=filename, error=e.detail["message"])
    return BatchEstimateFile(filename=filename, estimate=estimate)


def _sum_cost(values: list[float | None]) -> float | None:
    """Total of per-file costs, or None when they are hidden (non-admin)."""
    if any(value is None for value in values):
        return None
    return round(sum(value for value in values if value is not None), 3)


def _batch_totals(rows: list[BatchEstimateFile], current_user: User) -> BatchEstimateResponse:
    estimates = [row.estimate for row in rows if row.estimate is not None]
    return BatchEstimateResponse(
        files=rows,
        estimated_time_seconds_min=sum(e.estimated_time_seconds_min for e in estimates),
        estimated_time_seconds_max=sum(e.estimated_time_seconds_max for e in estimates),
        estimated_cost_min=_sum_cost([e.estimated_cost_min for e in estimates]),
        estimated_cost_max=_sum_cost([e.estimated_cost_max for e in estimates]),
        num_steps=len(STEP_REGISTRY),
        pricing_model=get_estimate_model_name() if can_see_cost(current_user) else None,
    )


@router.post("/estimate", response_model=EstimateResponse | BatchEstimateResponse)
async def estimate_processing(
    file: UploadFile | None = File(None),
    # Up to MAX_BATCH_FILES files at once, for a batch upload (#1114): one
    # call against both rate limits, a row per file plus totals.
    files: list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> EstimateResponse | BatchEstimateResponse:
    """
    Estimate cost and time for processing a CV file.

    This endpoint analyzes the document to estimate:
    - Number of tokens (based on document text)
    - Estimated cost range (based on token count and LLM pricing)
    - Estimated time range (based on token count and processing patterns)

    One file as ``file`` answers an EstimateResponse, as it always has; up to
    MAX_BATCH_FILES as ``files`` answers a BatchEstimateResponse. Nothing is
    saved - this is just for estimation.
    """
    if files is None:
        if file is None:
            raise bad_request("No file provided")
        file_ext = _estimate_file_ext(file)
        _check_estimate_limits(current_user, db)
        return await _estimate_one(file, file_ext, current_user)

    if file is not None:
        raise bad_request("Send one file as `file` or several as `files`, not both.")
    if len(files) > MAX_BATCH_FILES:
        raise bad_request(f"At most {MAX_BATCH_FILES} files can be estimated at once.")
    _check_estimate_limits(current_user, db)
    rows = [await _estimate_batch_file(each, current_user) for each in files]
    return _batch_totals(rows, current_user)
