"""File upload API endpoint."""
import hashlib
import io
import json
import logging
import os
import secrets
import string
import tempfile
import zipfile
from pathlib import Path
from collections.abc import Callable
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
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


def _validate_docx_magic(content: bytes) -> bool:
    """Check if content is a ZIP archive containing Word document structure."""
    if content[:4] != ZIP_MAGIC:
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            return "word/document.xml" in zf.namelist()
    except (zipfile.BadZipFile, Exception):
        return False


# What python-docx raises on a zip-shaped upload it cannot read (measured on
# 1.2.0): a zip missing its parts -> KeyError; malformed part XML ->
# XMLSyntaxError; a truncated zip -> BadZipFile; not an OPC package at all ->
# PackageNotFoundError; the tempfile round-trip -> OSError. Anything else is a
# bug and must surface, not be swallowed (§5.4).
_DOCX_READ_ERRORS = (PackageNotFoundError, zipfile.BadZipFile, KeyError, XMLSyntaxError, OSError)


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

# Upload directory
UPLOAD_DIR = Path(__file__).parent.parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


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
    total_time = base_time + len(STEP_REGISTRY) * 20  # ~20s/stage init + API overhead
    time_min = max(int(total_time * 0.6), 180)        # at least 3 min
    time_max = max(int(total_time * 1.3), time_min + 180)
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
    """
    return "".join(secrets.choice(_RUN_ID_ALPHABET) for _ in range(6))


# Bound on how many times a fresh run id may be regenerated after a storage
# collision before the request is failed outright (#685). Kept small and
# named rather than inline: at ~2.18e9 uniform ids (see generate_run_id), a
# single collision is already a ~1-in-2e9 event, so more than a couple of
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

    Called from _commit_run_or_compensate below, shared by /upload and
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


def _commit_run_or_compensate(
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

    # Read file content first for validation
    content = await file.read()

    # Check file size
    if len(content) > MAX_UPLOAD_SIZE:
        raise bad_request(f"File too large ({len(content) // (1024*1024)} MB). Maximum size is {MAX_UPLOAD_SIZE // (1024*1024)} MB.")

    # Validate magic bytes match claimed extension
    if file_ext == ".docx" and not _validate_docx_magic(content):
        logger.warning("[SECURITY] Rejected upload: file claims .docx but magic bytes do not match (user=%s)", current_user.email)
        raise bad_request("File content does not match .docx format. The file may be corrupted or mislabeled.")

    # Reject documents we can't read (scanned images, password-protected, blank).
    # These pass the magic-byte check but yield no text, so they would burn LLM
    # calls and return empty output with no explanation to the user. Fail open
    # (extracted is None) if extraction couldn't run, to avoid blocking valid files.
    extracted = _extract_text(content, file_ext)
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
    wcm_template_warning, wcm_template_match_ratio = detect_wcm_template(extracted)
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
    # helper as /estimate. extracted is None only when text extraction couldn't
    # run; fall back to a size-based char estimate then.
    est_char_count = len(extracted) if extracted else max(1, len(content) // 30000) * 2000
    _, estimated_duration_seconds = estimate_run_seconds(est_char_count)

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

    _commit_run_or_compensate(db, run_id, current_user.email, file_path)

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
    import tempfile
    import os

    # Validate file type
    if not file.filename:
        raise bad_request("No filename provided")

    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in ALLOWED_UPLOAD_EXTENSIONS:
        raise bad_request(
            f"Unsupported file type: {file_ext}. Only .docx files are supported. "
            "Please convert your file to .docx before uploading."
        )

    # Read file content
    content = await file.read()

    # Check file size
    if len(content) > MAX_UPLOAD_SIZE:
        raise bad_request(f"File too large ({len(content) // (1024*1024)} MB). Maximum size is {MAX_UPLOAD_SIZE // (1024*1024)} MB.")

    # Validate magic bytes
    if file_ext == ".docx" and not _validate_docx_magic(content):
        logger.warning("[SECURITY] Rejected estimate: file claims .docx but magic bytes do not match")
        raise bad_request("File content does not match .docx format. The file may be corrupted or mislabeled.")

    file_size_kb = len(content) / 1024

    # Extract actual text from document to estimate tokens
    document_text = ""
    text_char_count = 0

    try:
        if file_ext == ".docx":
            # Extract text from Word document
            with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            try:
                from docx import Document
                doc = Document(tmp_path)
                # Get text from paragraphs
                paragraphs_text = "\n".join([para.text for para in doc.paragraphs if para.text.strip()])
                # Also get text from tables
                tables_text = ""
                for table in doc.tables:
                    for row in table.rows:
                        for cell in row.cells:
                            if cell.text.strip():
                                tables_text += cell.text + " "
                document_text = paragraphs_text + "\n" + tables_text
                text_char_count = len(document_text)
            finally:
                os.unlink(tmp_path)
    except Exception as e:
        # Fallback: very rough estimate
        text_char_count = 5000  # Assume a typical CV has ~5000 characters

    # Ensure we have a reasonable minimum
    text_char_count = max(text_char_count, 1000)

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
    )
