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
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException
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


def generate_run_id() -> str:
    """Generate a unique 6-character run ID like 'A1B2C3'."""
    return secrets.token_urlsafe(4)[:6].upper()


@router.post("/upload", response_model=UploadResponse)
async def upload_cv(
    file: UploadFile = File(...),
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

    # Durably archive the ORIGINAL upload (same bucket/prefix as outputs) so the
    # run is reproducible and restart/retry survive a pod recycle. The pod-local
    # copy above is ephemeral. Non-fatal: never block a run on archival.
    try:
        storage = get_storage()
        storage.put_file(run_id, f"input/{stored_name}", content)
        storage.put_file(run_id, "input/manifest.json", json.dumps({
            "run_id": run_id,
            "original_filename": file.filename,  # only record of the real name
            "stored_as": stored_name,
            "file_type": file_ext[1:],
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "content_type": file.content_type,
            "uploaded_at": datetime.now().isoformat(),
            "user_email": current_user.email,
        }, indent=2).encode("utf-8"))
    except Exception as e:
        logger.warning("Failed to archive original upload to storage (run=%s): %s", run_id, e)

    # Create run record
    run = Run(
        id=run_id,
        filename=file.filename,
        file_type=file_ext[1:],  # Remove dot
        status="created",
        started_at=datetime.now(),
        user_id=current_user.id,
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

    # Time estimation based on empirical data
    # Actual processing observed: ~8 minutes for 3500 tokens
    # Time is highly variable due to API latency and document complexity
    # Use conservative estimates to avoid misleading users
    base_overhead_seconds = BASE_OVERHEAD_SECONDS
    time_per_1k_tokens = TIME_PER_1K_TOKENS

    base_time = base_overhead_seconds + (estimated_tokens / 1000) * time_per_1k_tokens
    num_stages = len(STEP_REGISTRY)

    # Each stage adds overhead for initialization and API calls
    total_time = base_time + (num_stages * 20)  # ~20 seconds per stage average

    time_min = int(total_time * 0.6)
    time_max = int(total_time * 1.3)

    # Ensure reasonable minimums
    time_min = max(time_min, 180)  # At least 3 minutes
    time_max = max(time_max, time_min + 180)  # At least 3 minutes more than min
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
