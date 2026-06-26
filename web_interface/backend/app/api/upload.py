"""File upload API endpoint."""
import hashlib
import io
import json
import logging
import os
import secrets
import tempfile
import zipfile
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
from pydantic import BaseModel
from typing import Optional

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
from app.errors import bad_request
from app.storage import get_storage
from app.services.template_warning import detect_wcm_template

logger = logging.getLogger(__name__)
PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"

# Minimum extracted text (characters) for a document to be considered readable.
# A real CV runs into the thousands of characters; anything below this is almost
# certainly a scanned image, a password-protected file, or effectively blank.
MIN_EXTRACTED_CHARS = 500


def _validate_pdf_magic(content: bytes) -> bool:
    """Check if content starts with PDF magic bytes."""
    return content[:5] == PDF_MAGIC


def _validate_docx_magic(content: bytes) -> bool:
    """Check if content is a ZIP archive containing Word document structure."""
    if content[:4] != ZIP_MAGIC:
        return False
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            return "word/document.xml" in zf.namelist()
    except (zipfile.BadZipFile, Exception):
        return False


def _extract_text(content: bytes, file_ext: str) -> str | None:
    """Best-effort text extraction for the empty-document guard.

    Returns the extracted text, an empty string when the file is readable but
    contains no text (scan/blank) or is password-protected, or ``None`` when
    extraction could not run at all (missing library, unexpected read error).
    Callers treat ``None`` as "cannot determine" and skip the guard rather than
    block a possibly-valid upload.
    """
    try:
        if file_ext == ".docx":
            from docx import Document
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
            finally:
                os.unlink(tmp_path)

        elif file_ext == ".pdf":
            import pdfplumber
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(content)
                tmp_path = tmp.name
            try:
                parts = []
                with pdfplumber.open(tmp_path) as pdf:
                    for page in pdf.pages:
                        parts.append(page.extract_text() or "")
                return "\n".join(parts)
            finally:
                os.unlink(tmp_path)
    except Exception as e:
        msg = str(e).lower()
        # A password/encryption failure means the document is genuinely
        # unreadable -> trip the guard (empty string) rather than fail open.
        if "password" in msg or "encrypt" in msg or "decrypt" in msg:
            return ""
        logger.warning("Text extraction for empty-doc guard failed (%s): %s", file_ext, e)
        return None
    return None


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


def generate_run_id() -> str:
    """Generate a unique 6-character run ID like 'A1B2C3'."""
    return secrets.token_urlsafe(4)[:6].upper()


@router.post("/upload", response_model=UploadResponse)
async def upload_cv(
    file: UploadFile = File(...),
    # Output-rendering options (issue #153). Sent as multipart form fields
    # alongside the file. Defaults mirror the Run model column defaults
    # (track changes ON, classification comments OFF) and are applied when the
    # fields are absent, keeping older clients backward compatible.
    include_track_changes: bool = Form(True),
    include_classification_comments: bool = Form(False),
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

    file_ext = Path(file.filename).suffix.lower()
    if file_ext not in [".docx", ".pdf"]:
        raise bad_request(f"Unsupported file type: {file_ext}. Only .docx and .pdf are supported.")

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
    if file_ext == ".pdf" and not _validate_pdf_magic(content):
        logger.warning("[SECURITY] Rejected upload: file claims .pdf but magic bytes do not match (user=%s)", current_user.email)
        raise bad_request("File content does not match .pdf format. The file may be corrupted or mislabeled.")
    elif file_ext == ".docx" and not _validate_docx_magic(content):
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
            "password-protected, or empty. Please upload a text-based PDF or Word document."
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

    # Generate run ID (after validation so rejected uploads don't waste IDs)
    run_id = generate_run_id()

    # Save with randomized filename (no user-provided text on filesystem)
    stored_name = f"{run_id}.{file_ext.lstrip('.')}"
    file_path = UPLOAD_DIR / stored_name
    with open(file_path, "wb") as f:
        f.write(content)

    # Durably archive the ORIGINAL upload to the run's storage namespace BEFORE
    # creating the run record. The pod-local copy above is ephemeral (lost on a
    # pod recycle), so this S3 object is the run's only recoverable input. A
    # failure here is therefore FATAL: we abort with an error and create NO run
    # record, rather than commit a "created" run whose input can't be recovered
    # and which would linger as an orphan in the DB and on the Runs dashboard
    # (and leave half-written objects in the store). Stop on error -- do not
    # proceed. (issue #170)
    storage = get_storage()
    manifest = json.dumps({
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
    try:
        storage.put_file(run_id, f"input/{stored_name}", content)
        storage.put_file(run_id, "input/manifest.json", manifest)
    except Exception as e:
        logger.error("Durable archive of upload failed; aborting upload (run=%s): %s", run_id, e)
        # Nothing has been committed to the DB yet, so there is no run to roll
        # back. Remove the ephemeral pod-local copy so the failed attempt leaves
        # nothing behind, then surface a clear error to the user.
        try:
            file_path.unlink(missing_ok=True)
        except OSError:
            pass
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
        estimated_duration_seconds=estimated_duration_seconds,
        show_track_changes=1 if include_track_changes else 0,
        show_pipeline_comments=1 if include_classification_comments else 0,
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

    db.commit()

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
    if file_ext not in [".docx", ".pdf"]:
        raise bad_request(f"Unsupported file type: {file_ext}. Only .docx and .pdf are supported.")

    # Read file content
    content = await file.read()

    # Check file size
    if len(content) > MAX_UPLOAD_SIZE:
        raise bad_request(f"File too large ({len(content) // (1024*1024)} MB). Maximum size is {MAX_UPLOAD_SIZE // (1024*1024)} MB.")

    # Validate magic bytes
    if file_ext == ".pdf" and not _validate_pdf_magic(content):
        logger.warning("[SECURITY] Rejected estimate: file claims .pdf but magic bytes do not match")
        raise bad_request("File content does not match .pdf format. The file may be corrupted or mislabeled.")
    elif file_ext == ".docx" and not _validate_docx_magic(content):
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

        elif file_ext == ".pdf":
            # For PDFs, try to extract text using pypdf if available
            try:
                import pypdf
                with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name
                try:
                    reader = pypdf.PdfReader(tmp_path)
                    for page in reader.pages:
                        document_text += page.extract_text() or ""
                    text_char_count = len(document_text)
                finally:
                    os.unlink(tmp_path)
            except ImportError:
                # Fallback: rough estimate for PDFs (typically ~500-1000 chars per page, ~1 page per 30KB)
                estimated_pages = max(1, len(content) // 30000)
                text_char_count = estimated_pages * 2000  # ~2000 chars per page average
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
