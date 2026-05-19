"""File upload API endpoint."""
import io
import logging
import secrets
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
    get_cost_per_1k_tokens, get_estimate_model_name,
)
from app.errors import bad_request

logger = logging.getLogger(__name__)
PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"


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

    # Generate run ID (after validation so rejected uploads don't waste IDs)
    run_id = generate_run_id()

    # Save with randomized filename (no user-provided text on filesystem)
    file_path = UPLOAD_DIR / f"{run_id}.{file_ext.lstrip('.')}"
    with open(file_path, "wb") as f:
        f.write(content)

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
        message=f"File uploaded successfully. Run ID: {run_id}"
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

    # Cost estimation. The per-token rate is derived from the model configured
    # in llm_config.yaml so the estimate tracks the active model preset. It
    # covers all 12 stages: hierarchy/entry extraction, taxonomy mapping, field
    # extraction, research summary, enrichment, and Word document generation.
    cost_per_1k_tokens = get_cost_per_1k_tokens()
    base_cost = (estimated_tokens / 1000) * cost_per_1k_tokens

    # Add variation buffer for different CV complexities
    cost_min = base_cost * 0.8
    cost_max = base_cost * 1.4

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
