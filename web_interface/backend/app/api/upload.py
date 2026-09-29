"""File upload API endpoint."""
import hashlib
import io
import json
import logging
import os
import secrets
import string
import tempfile
import threading
import time
import zipfile
import zlib
from pathlib import Path
from collections.abc import Callable
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel
from typing import Literal, Optional
from docx import Document
from docx.opc.exceptions import PackageNotFoundError
from lxml.etree import XMLSyntaxError

from app.database import get_db
from app.models import Run, Step, User
from app.schemas import UploadResponse
from app.pipeline.step_registry import STEP_REGISTRY
from app.auth import get_current_user
from app.rate_limiter import check_rate_limit
from app.config_loader import get_config_value
from app.services.config_service import (
    MAX_UPLOAD_SIZE, TIME_PER_1K_TOKENS, BASE_OVERHEAD_SECONDS,
    ESTIMATE_RATE_LIMIT_MAX, ESTIMATE_RATE_LIMIT_WINDOW_SECONDS,
    get_estimated_run_cost, get_estimate_model_name,
)
from app.errors import bad_request, internal_error
from app.storage import get_storage
from app.storage.base import StorageKeyExists
from app.services.template_warning import detect_wcm_template

logger = logging.getLogger(__name__)
ZIP_MAGIC = b"PK\x03\x04"

# Extensions the upload API accepts, matching the frontend's ".docx only" guard
# (UploadPage.tsx's dropzone caption and error, HelpPage.tsx's "accepts .docx
# ... files only"). PDF was accepted here until #524: every downstream reader
# (stage 1a/1b/2's docx_structure_extractor, stage 2, stage 6) is python-docx
# only, so a PDF upload always died at stage 1a. PDF ingest via a conversion
# step is tracked separately as #806, not implemented here.
ALLOWED_UPLOAD_EXTENSIONS = (".docx",)

# Minimum extracted text (characters) for a document to be considered readable.
# A real CV runs into the thousands of characters; anything below this is almost
# certainly a scanned image, a password-protected file, or effectively blank.
MIN_EXTRACTED_CHARS = 500

# /estimate's placeholder char count when _extract_text couldn't read the
# document at all (#794) -- a mid-range guess so the quote shown is neither
# suspiciously cheap nor alarmingly expensive while the real content is
# unknown. Distinct from MIN_EXTRACTED_CHARS above, which gates /upload.
_ESTIMATE_FALLBACK_CHAR_COUNT = 5000
# Floor on the char count an estimate is sized from: a readable-but-blank
# document still costs a run's fixed per-stage overhead.
_ESTIMATE_MIN_CHAR_COUNT = 1000


def _estimate_char_count(extracted: str | None) -> int:
    """The char count both /estimate's quote and /upload's stall-watchdog
    duration are sized from, so one file gets one number (#794). ``None``
    (unreadable) takes the fixed fallback; /estimate flags that to the user."""
    if extracted is None:
        return _ESTIMATE_FALLBACK_CHAR_COUNT
    return max(len(extracted), _ESTIMATE_MIN_CHAR_COUNT)


# Expansion bound checked before python-docx parses (#793). zipfile stops
# inflating each entry at its declared file_size, so capping the declared
# totals caps what a parse can expand to. The 392 local corpus CVs top out at
# 37 entries and 7.2 MB uncompressed; a zip bomb declares gigabytes.
_DOCX_MAX_ENTRIES = 1000
_DOCX_MAX_UNCOMPRESSED_BYTES = 256 * 1024 * 1024


def _validate_docx_magic(content: bytes) -> bool:
    """Check if content is a ZIP archive containing Word document structure,
    within the entry-count and uncompressed-size bounds above."""
    if content[:4] != ZIP_MAGIC:
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            entries = zf.infolist()
            if (len(entries) > _DOCX_MAX_ENTRIES
                    or sum(e.file_size for e in entries) > _DOCX_MAX_UNCOMPRESSED_BYTES):
                logger.warning("Rejected docx: %d entries, %d bytes uncompressed",
                               len(entries), sum(e.file_size for e in entries))
                return False
            return "word/document.xml" in zf.namelist()
    except (zipfile.BadZipFile, Exception):
        return False


# What python-docx raises on a zip-shaped upload it cannot read (measured on
# 1.2.0): a zip missing its parts -> KeyError; malformed part XML ->
# XMLSyntaxError; a truncated zip -> BadZipFile; not an OPC package at all ->
# PackageNotFoundError; the tempfile round-trip -> OSError. Anything else is a
# bug and must surface, not be swallowed (§5.4).
_DOCX_READ_ERRORS = (PackageNotFoundError, zipfile.BadZipFile, KeyError, XMLSyntaxError, OSError,
                     zlib.error)


def _extract_text(content: bytes, file_ext: str) -> str | None:
    """Best-effort text extraction for the empty-document guard.

    Returns the extracted text, an empty string when the file is readable but
    contains no text (scan/blank), or ``None`` when the document could not be
    read at all (a known python-docx read failure, see ``_DOCX_READ_ERRORS``).
    Callers treat ``None`` as "cannot determine" and skip the guard rather than
    block a possibly-valid upload.
    """
    if file_ext != ".docx":
        return None
    with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
        tmp.write(content)
        tmp_path = tmp.name
    try:
        doc = Document(tmp_path)
        parts = [p.text for p in doc.paragraphs if p.text.strip()]
        for table in doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if cell.text.strip():
                        parts.append(cell.text)
        return "\n".join(parts)
    except _DOCX_READ_ERRORS as e:
        logger.warning("Text extraction for empty-doc guard failed (%s): %s", file_ext, e, exc_info=True)
        return None
    finally:
        os.unlink(tmp_path)


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
    estimated_cost_min: float
    estimated_cost_max: float
    estimated_time_seconds_min: int
    estimated_time_seconds_max: int
    num_steps: int
    filename: str
    file_size_kb: float
    pricing_model: str
    # True when the document's text couldn't be read and text_characters is
    # the fixed fallback guess, not a measurement (#794).
    text_characters_is_guess: bool = False

# Upload directory
UPLOAD_DIR = Path(__file__).parent.parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


# Recalibrated after the #881 parallel LLM batches went live (dev-207): the old
# 20 s/stage overhead and 3-min floor quoted ~2.5x the real wall time on every
# post-parallel prod run (e.g. estimate max 421 s vs actual 89-105 s). With 5 s
# and a 1-min floor, all six runs from 2026-09-28/29 land inside [min, max].
_PER_STAGE_OVERHEAD_SECONDS = 5
_MIN_ESTIMATE_SECONDS = 60
_MIN_ESTIMATE_SPREAD_SECONDS = 60


def estimate_run_seconds(text_char_count: int) -> tuple[int, int]:
    """(min, max) wall-clock seconds for a full run, scaled to document size.

    Token-based (≈4 chars/token), tuned to observed runs (~8 min for 3500
    tokens). Used by /estimate for the pre-run quote AND stored per-run so the
    client stall watchdog can scale its "taking longer than expected" threshold
    to the actual CV instead of a fixed constant. Single source of truth for
    both, so the quote shown and the threshold applied never drift.
    """
    text_char_count = max(text_char_count, 1000)
    estimated_tokens = text_char_count // 4
    base_time = BASE_OVERHEAD_SECONDS + (estimated_tokens / 1000) * TIME_PER_1K_TOKENS
    total_time = base_time + len(STEP_REGISTRY) * _PER_STAGE_OVERHEAD_SECONDS
    time_min = max(int(total_time * 0.6), _MIN_ESTIMATE_SECONDS)
    time_max = max(int(total_time * 1.3), time_min + _MIN_ESTIMATE_SPREAD_SECONDS)
    return time_min, time_max


_RUN_ID_ALPHABET = string.ascii_uppercase + string.digits


def generate_run_id() -> str:
    """Generate a unique 6-character run ID like 'A1B2C3'.

    Draws each of the 6 characters uniformly from A-Z0-9 (36 symbols) via
    secrets.choice, giving 36**6 ~= 2.18e9 equally-likely ids. This narrows
    the alphabet from the prior generator's: `secrets.token_urlsafe(4)[:6]
    .upper()` emitted base64url output (which can include `-` and `_`)
    case-folded onto 38 symbols, and truncating to 6 chars from a 4-byte
    (32-bit) draw left the 6th character able to take only 4 distinct
    values -- collapsing the last position to ~4 outcomes and the whole id
    to ~2e8 effective values instead of the nominal 36**6 (#685). Every
    existing consumer already accepts the narrower `^[A-Z0-9]{6}$` set --
    steps.py's `_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")` is a
    superset -- so no consumer needed a change.

    Six base-36 characters stay the canonical run id (#797 decision,
    2026-09-09): a collision now costs a redraw in create_run_archive, never
    an overwrite, so the id width sets only the redraw rate. With N existing
    runs a fresh draw collides with probability ~N / 36**6: ~4.6e-5 at 1e5
    runs, ~4.6e-4 at 1e6, ~4.6e-3 at 1e7. Migration trigger: when the run
    table approaches ~1e6 rows (redraws near 5e-4 per upload), move to a
    UUID/ULID canonical id with this 6-char form kept as a display id --
    widening touches Run.id (String(10)), artifact basenames and download
    URLs. Exhaustion of every redraw is logged at ERROR by both callers of
    create_run_archive (upload and restart).
    """
    return "".join(secrets.choice(_RUN_ID_ALPHABET) for _ in range(6))


# Bound on how many times a fresh run id may be regenerated after a storage
# collision before the request is failed outright (#685). Kept small and
# named rather than inline: at ~2.18e9 uniform ids (see generate_run_id), a
# single draw collides with probability ~N / 2.18e9 for N existing runs, so
# more than a couple of
# regenerations would only ever fire under a genuine storage fault, not an
# id collision -- in which case retrying further just delays surfacing it.
_RUN_ID_ATTEMPTS = 5


class RunIdAttemptsExhausted(Exception):
    """Raised when every retry to allocate a collision-free run id failed."""


def create_run_archive(
    content: bytes,
    file_ext: str,
    build_manifest: Callable[[str, str], bytes],
    write_local: Callable[[Path], None],
) -> tuple[str, str, Path, bytes]:
    """Allocate a fresh run id and durably archive `content` under it.

    Exclusive-creates the durable archive via put_file_exclusive so a run-id
    collision can never silently overwrite another run's archived input
    (#685) -- this is what closes the overwrite, not the pod-local write
    below. input/manifest.json is written BEFORE input/{stored_name}: the
    manifest is the collision sentinel, so a colliding id is caught before
    any CV content byte lands under another run's prefix (verifier finding,
    round 2) -- writing content first could, on an extension mismatch (this
    id already archived under a *different* extension), let the new file
    land in the other run's namespace ahead of the manifest check catching
    it. On a collision (StorageKeyExists, or FileExistsError from
    `write_local`) the id is regenerated and the whole attempt retried, up
    to _RUN_ID_ATTEMPTS times; a colliding attempt's own partial pod-local
    file is removed before the retry so failed attempts never accumulate on
    disk.

    If the manifest write succeeds but the *content* write then fails, the
    manifest is left orphaned under the abandoned id with no run row ever
    created for it. This is harmless and not cleaned up: (1) if the content
    write failed with StorageKeyExists, that would require this id's content
    key to already exist while its manifest.json did not -- a state nothing
    in this codebase can produce, since create_run_archive is the only
    writer of any `input/` key (verified by grep) and always writes the
    manifest first; (2) if it failed with a real storage fault, the request
    is failing anyway (fatal per #170) and the orphaned manifest carries only
    this same requesting user's own filename/email, so it is not a
    cross-user disclosure -- just an unclaimed object nothing ever lists or
    references. RunStorage has no single-key delete (only whole-run
    delete_run, which in a genuine collision would risk deleting the OTHER
    run's real files sharing that id -- unsafe to call here), so proactively
    deleting it would need a new storage primitive for a branch that cannot
    currently occur.

    Shared by /upload and restart_run so both close the same race the same
    way (runs.py imports this rather than duplicating the retry loop).

    Args:
        content: the file bytes to archive (the raw upload, or the original
            run's input being copied forward on restart).
        file_ext: dotted extension, e.g. ".docx".
        build_manifest: (run_id, stored_name) -> the manifest JSON to archive
            alongside the file. Caller-supplied because /upload's and
            restart_run's manifests carry different provenance fields.
        write_local: writes the pod-local copy at the given path. Both
            callers use an exclusive `"xb"` open, so a pod-local id
            collision raises FileExistsError and this loop retries with a
            fresh id rather than overwriting another run's local input.

    Returns:
        (run_id, stored_name, file_path, manifest) for the archived file.

    Raises:
        RunIdAttemptsExhausted: every attempt collided.
        Exception: a non-collision storage failure, propagated as-is (fatal,
            not retried -- mirrors #170: no run is created on an archive
            failure that isn't a collision).
    """
    storage = get_storage()
    for _ in range(_RUN_ID_ATTEMPTS):
        run_id = generate_run_id()
        stored_name = f"{run_id}.{file_ext.lstrip('.')}"
        file_path = UPLOAD_DIR / stored_name
        try:
            write_local(file_path)
        except FileExistsError:
            # Pod-local id collision (vanishingly rare -- see generate_run_id).
            # Regenerate rather than overwrite another upload's local copy.
            continue
        manifest = build_manifest(run_id, stored_name)
        try:
            # Manifest first: it is the collision sentinel, so a colliding id
            # is caught before any CV content byte is written (see docstring).
            storage.put_file_exclusive(run_id, "input/manifest.json", manifest)
            storage.put_file_exclusive(run_id, f"input/{stored_name}", content)
        except StorageKeyExists:
            # Same run id already has an archive: another request won the
            # race. Discard this attempt's local file and try a fresh id.
            _unlink_best_effort(file_path)
            continue
        except Exception:
            # A real storage fault, not a collision -- fatal per #170: clean
            # up and propagate, do not retry.
            _unlink_best_effort(file_path)
            raise
        return run_id, stored_name, file_path, manifest
    raise RunIdAttemptsExhausted(
        f"could not allocate a collision-free run id after {_RUN_ID_ATTEMPTS} attempts"
    )


def _unlink_best_effort(path: Path) -> None:
    """Remove a leftover pod-local file, never raising.

    Cleanup after an aborted attempt -- an OSError here (permissions, a
    concurrent delete) must not mask the original failure being handled.
    """
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _compensate_failed_run(run_id: str, email: str, file_path: Path) -> None:
    """Undo a durable archive after the row commit that should have followed
    it failed (#802). run_id was generated fresh for this request and no row
    ever referenced it, so deleting its whole storage namespace is safe here
    -- nothing else can live under it (unlike a general run_id, this one is
    never reused for another archive).

    Called from commit_run_or_compensate below, shared by /upload and
    restart_run (runs.py imports that).

    # ponytail: compensation runs in-process; a crash between archive and
    # this block still orphans -- a storage-keyed sweep is the upgrade path
    # (#802 option 2).
    """
    storage = get_storage()
    try:
        storage.delete_run(run_id)
    except Exception:
        logger.exception("Compensating delete_run failed for orphaned run %s", run_id)
    try:
        # Mirrors the exact key put_global wrote the index under.
        storage.delete_global_prefix(f"by-submitter/{email.lower()}/{run_id}/")
    except Exception:
        logger.exception("Compensating delete_global_prefix failed for orphaned run %s", run_id)
    _unlink_best_effort(file_path)


def commit_run_or_compensate(
    db: Session, run_id: str, email: str, file_path: Path,
) -> None:
    """Commit the pending Run/Step rows, or compensate the archive and raise
    a 5xx if the commit fails (#802). Shared by /upload and restart_run so a
    commit failure after a successful archive is handled identically on both
    write paths.
    """
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception(
            "Run row commit failed after a durable archive; compensating (run=%s)",
            run_id,
        )
        _compensate_failed_run(run_id, email, file_path)
        raise internal_error(
            "We couldn't finish creating your run, so nothing was saved. "
            "Please try again in a moment."
        )


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
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Upload a CV file and create a new pipeline run."""

    # Check consent at upload time (not just page visit)
    current_consent_version = str(get_config_value(db, "consent_version") or "1.0")
    if current_user.consent_version != current_consent_version:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "consent_required",
                "message": "Please review and accept the updated consent terms.",
            },
        )

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
        raise bad_request(
            f"Unsupported file type: {file_ext}. Only .docx files are supported. "
            "Please convert your file to .docx before uploading."
        )

    # Check rate limit (after file validation so bad uploads don't count)
    rate_limit_error = check_rate_limit(current_user, db)
    if rate_limit_error:
        raise HTTPException(status_code=429, detail=rate_limit_error)

    # Read file content in bounded chunks so an oversized body is never fully
    # buffered before being rejected (#793).
    content = await _read_bounded(file, MAX_UPLOAD_SIZE)

    # Validate magic bytes match claimed extension
    if file_ext == ".docx" and not _validate_docx_magic(content):
        logger.warning("[SECURITY] Rejected upload: file claims .docx but magic bytes do not match (user=%s)", current_user.email)
        raise bad_request("File content does not match .docx format. The file may be corrupted or mislabeled.")

    # Reject documents we can't read (scanned images, password-protected, blank).
    # These pass the magic-byte check but yield no text, so they would burn LLM
    # calls and return empty output with no explanation to the user. Fail open
    # (extracted is None) if extraction couldn't run, to avoid blocking valid files.
    extracted = await run_in_threadpool(_extract_text, content, file_ext)
    if extracted is not None and len(extracted.strip()) < MIN_EXTRACTED_CHARS:
        logger.info("Rejected upload with no readable text (user=%s, chars=%d)", current_user.email, len(extracted.strip()))
        raise bad_request(
            "We couldn't read any text from this file. It may be a scanned image, "
            "password-protected, or empty. Please upload a text-based Word document."
        )

    # Cheap, no-LLM check: does this look like the *blank* WCM CV template?
    # Reuses the already-extracted text -- a filled CV matches the blank-template
    # string set on almost no lines, an unfilled template on nearly all of them.
    # Best-effort and non-fatal: detect_wcm_template swallows its own errors and
    # returns (False, None), so this never blocks an upload. We only warn (the UI
    # requires an acknowledgement) -- we never reject, since reformatting an
    # existing publication list is a legitimate, template-shaped use.
    wcm_template_warning, wcm_template_match_ratio = await run_in_threadpool(
        detect_wcm_template, extracted
    )
    if wcm_template_warning:
        logger.info(
            "Upload looks like a blank WCM template (user=%s, match_ratio=%s)",
            current_user.email, wcm_template_match_ratio,
        )

    # Allocate a fresh run id and durably archive the ORIGINAL upload to the
    # run's storage namespace BEFORE creating the run record. The pod-local
    # copy is ephemeral (lost on a pod recycle), so the archive is the run's
    # only recoverable input. Both the pod-local write and the archive are
    # exclusive creates: a run-id collision on either regenerates the id and
    # retries (#685) rather than silently overwriting another run's file. A
    # failure here (a real storage fault, or every retry colliding) is
    # therefore FATAL: we abort with an error and create NO run record,
    # rather than commit a "created" run whose input can't be recovered and
    # which would linger as an orphan in the DB and on the Runs dashboard
    # (and leave half-written objects in the store). Stop on error -- do not
    # proceed. (issue #170)
    def _build_manifest(run_id: str, stored_name: str) -> bytes:
        return json.dumps({
            "run_id": run_id,
            "original_filename": file.filename,  # only record of the real name
            "stored_as": stored_name,
            "file_type": file_ext[1:],
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "content_type": file.content_type,
            "uploaded_at": datetime.now().isoformat(),
            "user_email": current_user.email,
        }, indent=2).encode("utf-8")

    def _write_local(path: Path) -> None:
        with open(path, "xb") as f:
            f.write(content)

    try:
        run_id, stored_name, file_path, manifest = create_run_archive(
            content, file_ext, _build_manifest, _write_local,
        )
    except Exception as e:
        logger.error("Durable archive of upload failed; aborting upload: %s", e)
        raise HTTPException(
            status_code=502,
            detail={
                "error": "storage_unavailable",
                "message": (
                    "We couldn't store your file securely, so no run was created. "
                    "Please try again in a moment."
                ),
            },
        )
    storage = get_storage()

    # Cross-run, browsable-by-submitter index: the same manifest keyed under the
    # submitter so runs can be found by who uploaded them in S3 without opening
    # each run folder. This is a navigation pointer only -- the run and its input
    # are already durably stored above and the pipeline does not read it. So,
    # unlike the archive, this stays BEST-EFFORT: a failure here must never fail
    # the upload or orphan a run.
    try:
        storage.put_global(
            f"by-submitter/{current_user.email.lower()}/{run_id}/manifest.json",
            manifest,
        )
    except Exception as e:
        logger.warning("Failed to write by-submitter index (run=%s): %s", run_id, e)

    # Input-scaled wall-clock estimate, stored so the client stall watchdog can
    # scale its "taking longer than expected" threshold to this CV instead of a
    # fixed constant (large CVs were false-positiving as "may be stuck"). Same
    # helper and char count as /estimate.
    _, estimated_duration_seconds = estimate_run_seconds(_estimate_char_count(extracted))

    # Create run record. Persist the user's output-rendering choices (issue
    # #153) as the truthy ints the Stage 6 generator reads at render time.
    run = Run(
        id=run_id,
        filename=file.filename,
        file_type=file_ext[1:],  # Remove dot
        status="created",
        started_at=datetime.now(),
        user_id=current_user.id,
        submission_type=submission_type,
        estimated_duration_seconds=estimated_duration_seconds,
        show_track_changes=1 if include_track_changes else 0,
        show_pipeline_comments=1 if include_classification_comments else 0,
        strip_template_instructions=1 if strip_wcm_instructions else 0,
    )
    db.add(run)

    # Create step records (all pending initially)
    for step_def in STEP_REGISTRY:
        step = Step(
            run_id=run_id,
            step_number=step_def.number,
            stage_id=step_def.stage_id,
            step_name=step_def.name,
            status="pending"
        )
        db.add(step)

    commit_run_or_compensate(db, run_id, current_user.email, file_path)

    return UploadResponse(
        run_id=run_id,
        filename=file.filename,
        file_type=file_ext[1:],
        status="created",
        message=f"File uploaded successfully. Run ID: {run_id}",
        wcm_template_warning=wcm_template_warning,
        wcm_template_match_ratio=wcm_template_match_ratio,
    )


@router.post("/estimate", response_model=EstimateResponse)
async def estimate_processing(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Estimate cost and time for processing a CV file.

    This endpoint analyzes the document to estimate:
    - Number of tokens (based on document text)
    - Estimated cost range (based on token count and LLM pricing)
    - Estimated time range (based on token count and processing patterns)

    The file is not saved - this is just for estimation.
    """
    # Validate file type
    if not file.filename:
        raise bad_request("No filename provided")

    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise bad_request(
            f"Unsupported file type: {file_ext}. Only .docx files are supported. "
            "Please convert your file to .docx before uploading."
        )

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

    # Read file content in bounded chunks so an oversized body is never fully
    # buffered before being rejected (#793).
    content = await _read_bounded(file, MAX_UPLOAD_SIZE)

    # Validate magic bytes
    if file_ext == ".docx" and not _validate_docx_magic(content):
        logger.warning("[SECURITY] Rejected estimate: file claims .docx but magic bytes do not match")
        raise bad_request("File content does not match .docx format. The file may be corrupted or mislabeled.")

    file_size_kb = len(content) / 1024

    # Extract text through the same implementation /upload uses (#794) --
    # this endpoint used to re-walk the docx paragraphs/tables inline, which
    # could compute a different text_char_count for the same file. Off the
    # event loop, same as /upload (#793).
    extracted = await run_in_threadpool(_extract_text, content, file_ext)
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
        estimated_cost_min=round(cost_min, 3),
        estimated_cost_max=round(cost_max, 3),
        estimated_time_seconds_min=time_min,
        estimated_time_seconds_max=time_max,
        num_steps=num_stages,
        filename=file.filename,
        file_size_kb=round(file_size_kb, 1),
        pricing_model=get_estimate_model_name(),
        text_characters_is_guess=extracted is None,
    )
