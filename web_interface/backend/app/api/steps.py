"""Step details and data API endpoints."""
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Step, Log, Run, User
from app.schemas import StepDetail, LogEntry, OutputPreview
from app.auth import get_current_user
from app.services.run_service import check_run_access
from app.errors import bad_request, not_found, internal_error

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/run/{run_id}/step/{step_number}", response_model=StepDetail)
async def get_step_detail(
    run_id: str,
    step_number: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get detailed information about a specific step."""

    check_run_access(run_id, current_user, db)

    step = db.query(Step).filter(
        Step.run_id == run_id,
        Step.step_number == step_number
    ).first()

    if not step:
        raise not_found(f"Step {step_number} not found for run {run_id}")

    # Get logs for this step
    logs = db.query(Log).filter(
        Log.run_id == run_id,
        Log.step_number == step_number
    ).order_by(Log.timestamp).all()

    log_entries = [
        LogEntry(
            time=log.timestamp.strftime("%H:%M:%S"),
            level=log.level,
            message=log.message
        )
        for log in logs
    ]

    # Parse output files
    output_files = []
    if step.output_files:
        try:
            output_files = json.loads(step.output_files)
        except:
            output_files = []

    # Try to load preview of first output file
    output_preview = None
    if output_files and step.status == "complete":
        output_preview = await _generate_preview(run_id, output_files[0])

    return StepDetail(
        step_id=step.id,
        step_number=step.step_number,
        name=step.step_name,
        status=step.status,
        duration=step.duration_seconds,
        cost_usd=step.cost or 0.0,
        input_file=step.input_file,
        output_files=output_files,
        logs=log_entries,
        output_preview=output_preview
    )


def _resolve_safe_path(filename: str, run_id: str) -> Path:
    """Resolve filename to a validated path within run output directories.

    Uses Path.resolve() + is_relative_to() for containment checking.
    Rejects absolute paths and directory traversal attempts.

    Raises:
        HTTPException(400): Invalid or malicious filename
        HTTPException(404): File not found in any allowed directory
    """
    # Reject absolute paths outright
    if filename.startswith('/'):
        logger.warning(
            "[SECURITY] Blocked absolute path in file request: %s (run: %s)",
            filename, run_id,
        )
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Check for traversal attempts (belt-and-suspenders; resolve+is_relative_to is the real guard)
    if ".." in filename:
        logger.warning(
            "[SECURITY] Blocked path traversal attempt: %s (run: %s)",
            filename, run_id,
        )
        raise HTTPException(status_code=400, detail="Invalid filename")

    # Candidate 1: web_interface/outputs/{run_id}/
    output_dir = (Path(__file__).parent.parent.parent.parent / "outputs" / run_id).resolve()
    candidate = (output_dir / filename).resolve()

    if candidate.is_relative_to(output_dir) and candidate.exists():
        return candidate

    # Candidate 2: src/unified_pipeline/outputs/ (search all stage subdirectories)
    pipeline_dir = (
        Path(__file__).parent.parent.parent.parent.parent
        / "src" / "unified_pipeline" / "outputs"
    ).resolve()

    if pipeline_dir.exists():
        base_filename = Path(filename).name
        for stage_dir in pipeline_dir.iterdir():
            if stage_dir.is_dir():
                candidate = (stage_dir / base_filename).resolve()
                if candidate.is_relative_to(pipeline_dir) and candidate.exists():
                    return candidate

    raise HTTPException(status_code=404, detail="File not found")


@router.get("/run/{run_id}/data/{filename:path}")
async def get_data_file(
    run_id: str,
    filename: str,
    preview: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Download or preview a data file."""

    check_run_access(run_id, current_user, db)

    file_path = _resolve_safe_path(filename, run_id)

    # Extract just the base filename for download
    download_filename = file_path.name

    # For Word docs, always download
    if download_filename.endswith(".docx"):
        return FileResponse(
            str(file_path),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=download_filename
        )

    # For JSON, can preview or download
    if download_filename.endswith(".json"):
        if preview:
            # Return full preview (no limits - show everything!)
            preview_data = await _generate_preview_from_path(file_path)
            return JSONResponse(content=preview_data.dict() if preview_data else {})
        else:
            return FileResponse(str(file_path), media_type="application/json", filename=download_filename)

    # Default: download
    return FileResponse(str(file_path), filename=download_filename)


async def _generate_preview_from_path(file_path: Path) -> OutputPreview | None:
    """Generate a preview of a JSON file from a direct path."""
    if not file_path.exists() or not str(file_path).endswith(".json"):
        return None

    try:
        with open(file_path, "r") as f:
            data = json.load(f)

        return _parse_json_to_preview(data)

    except Exception as e:
        print(f"Error generating preview: {e}")
        return None


async def _generate_preview(run_id: str, filename: str) -> OutputPreview | None:
    """Generate a preview of a JSON file."""

    output_dir = Path(__file__).parent.parent.parent.parent / "outputs" / run_id
    file_path = output_dir / filename

    if not file_path.exists() or not filename.endswith(".json"):
        return None

    try:
        with open(file_path, "r") as f:
            data = json.load(f)

        return _parse_json_to_preview(data)

    except Exception as e:
        print(f"Error generating preview: {e}")
        return None


def _parse_json_to_preview(data) -> OutputPreview | None:
    """Parse JSON data into OutputPreview structure."""
    try:

        # Handle different JSON structures
        if isinstance(data, list):
            # List of objects
            if len(data) == 0:
                return OutputPreview(headers=[], rows=[])

            # Get headers from first object
            headers = list(data[0].keys()) if isinstance(data[0], dict) else ["value"]

            # Convert to rows - NO LIMIT, show everything
            rows = []
            for item in data:
                if isinstance(item, dict):
                    rows.append([str(item.get(h, "")) for h in headers])
                else:
                    rows.append([str(item)])

            return OutputPreview(headers=headers, rows=rows)

        elif isinstance(data, dict):
            # Check if this is a comprehensive output with multiple section types
            section_keys = ["publications", "education", "positions", "grants"]
            found_sections = [k for k in section_keys if k in data and isinstance(data[k], list)]

            if found_sections:
                # Comprehensive multi-section output - show ALL sections combined
                headers = ["Section", "Index"] + []
                rows = []

                # Collect all possible field names across all sections
                all_fields = set()
                for section_key in found_sections:
                    for item in data[section_key]:
                        if isinstance(item, dict):
                            all_fields.update(item.keys())

                # Sort fields for consistent display
                sorted_fields = sorted(all_fields)
                headers = ["Section", "Index"] + sorted_fields

                # Add rows from each section - NO LIMIT
                for section_key in found_sections:
                    section_data = data[section_key]
                    for idx, item in enumerate(section_data):
                        if isinstance(item, dict):
                            row = [section_key, str(idx + 1)]
                            row.extend([str(item.get(field, "")) for field in sorted_fields])
                            rows.append(row)

                return OutputPreview(headers=headers, rows=rows)

            # Dictionary - try to find a list inside
            for key, value in data.items():
                if isinstance(value, list) and len(value) > 0:
                    # Found a list - use it, show ALL items
                    if isinstance(value[0], dict):
                        headers = list(value[0].keys())
                        rows = [[str(item.get(h, "")) for h in headers] for item in value]
                        return OutputPreview(headers=headers, rows=rows)

            # Fallback: show key-value pairs
            headers = ["Key", "Value"]
            rows = [[str(k), str(v)[:200]] for k, v in list(data.items())]
            return OutputPreview(headers=headers, rows=rows)

    except Exception as e:
        print(f"Error generating preview: {e}")
        return None

    return None


@router.get("/run/{run_id}/data/{filename:path}/json")
async def get_json_content(
    run_id: str,
    filename: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get raw JSON content for display in viewer."""

    check_run_access(run_id, current_user, db)

    file_path = _resolve_safe_path(filename, run_id)

    if not str(file_path).endswith(".json"):
        raise bad_request("Only JSON files can be viewed")

    try:
        with open(file_path, "r") as f:
            data = json.load(f)

        return JSONResponse(content={
            "filename": filename,
            "size_bytes": file_path.stat().st_size,
            "content": data
        })
    except Exception as e:
        raise internal_error("Error reading file")


# Mapping of stage IDs to filename patterns in prompt logs
# These patterns match the actual filenames in the prompt_logs directory
# Note: Some stages don't create prompt logs (1a uses direct OpenAI API, 1b/2/5/5b/6 are non-LLM stages)
STAGE_TO_FILENAME_PATTERNS = {
    '1a': [],  # Stage 1a uses OpenAI API directly without prompt logging
    '1b': [],  # Non-LLM stage (no prompts)
    '2': ['*stage_2_entry_extraction*'],  # Stage 2 may have some LLM components
    '3a': ['*stage_3a_header_taxonomy*', '*taxonomy_mapping_pass1*'],
    '3b': ['*stage_3b_entry_classification*', '*taxonomy_mapping_pass2*'],
    '4': ['*stage_4_field*', '*stage_4_extraction*', '*field_extraction_batch*'],
    '4.5': ['*stage_4_5*', '*stage_4.5*', '*research_summary*'],
    '5': [],  # Non-LLM stage (PubMed API)
    '5b': [],  # Non-LLM stage (ROR API)
    '5c': ['*stage_5c*', '*teaching_formatter*'],
    '5d': ['*stage_5d*', '*citation_formatter*'],
    '6': [],  # Non-LLM stage (Word document generation)
}

# Stages that don't have prompt logs with an explanation
STAGES_WITHOUT_PROMPT_LOGS = {
    '1a': "Stage 1a (Hierarchy Extraction) uses direct OpenAI API calls without prompt logging.",
    '1b': "Stage 1b (Hierarchy Mapping) is a non-LLM stage - no prompts are used.",
    '5': "Stage 5 (PubMed Enrichment) is a non-LLM stage - uses PubMed API.",
    '5b': "Stage 5b (Institution Enrichment) is a non-LLM stage - uses ROR API.",
    '6': "Stage 6 (WCM Word Template) is a non-LLM stage - generates Word documents.",
}


@router.get("/run/{run_id}/prompt-logs")
async def get_prompt_logs(
    run_id: str,
    step: int = 1,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get prompt logs for a specific step, filtered to only show logs from this run."""

    # Verify access and get the run
    run_record = check_run_access(run_id, current_user, db)
    if not run_record.started_at:
        return JSONResponse(content={"logs": [], "error": "Run has no start time"})

    # Convert run start time to timestamp for comparison
    run_start_timestamp = run_record.started_at.timestamp()

    # Get the step to find the stage_id
    step_record = db.query(Step).filter(
        Step.run_id == run_id,
        Step.step_number == step
    ).first()

    if not step_record:
        return JSONResponse(content={"logs": []})

    stage_id = step_record.stage_id or str(step)

    # Check if this stage doesn't have prompt logs
    if stage_id in STAGES_WITHOUT_PROMPT_LOGS:
        return JSONResponse(content={
            "logs": [],
            "stage_id": stage_id,
            "step": step,
            "message": STAGES_WITHOUT_PROMPT_LOGS[stage_id]
        })

    # Find prompt logs directory
    # prompt_logger.py writes to src/unified_pipeline/prompt_logs/ using absolute path
    project_root = Path(__file__).parent.parent.parent.parent.parent
    prompt_log_dirs = [
        project_root / "src" / "unified_pipeline" / "prompt_logs",
    ]

    logs = []
    seen_files = set()

    # Get the filename patterns for this stage
    patterns = STAGE_TO_FILENAME_PATTERNS.get(stage_id, [])

    for prompt_logs_base in prompt_log_dirs:
        if prompt_logs_base.exists() and patterns:
            # Find files matching any of the patterns for this stage
            for pattern in patterns:
                for log_file in sorted(prompt_logs_base.glob(pattern)):
                    # Only include .txt files (readable format)
                    if not log_file.name.endswith('.txt'):
                        continue
                    # Avoid duplicates (by filename, not full path)
                    if log_file.name in seen_files:
                        continue

                    # Filter by file modification time - only include files created during this run
                    file_mtime = log_file.stat().st_mtime
                    if file_mtime < run_start_timestamp:
                        continue  # Skip files from before this run started

                    seen_files.add(log_file.name)
                    try:
                        content = log_file.read_text(encoding='utf-8', errors='replace')
                        logs.append({
                            "filename": str(log_file),
                            "content": content[:50000]  # Limit to 50KB per file
                        })
                    except Exception as e:
                        logs.append({
                            "filename": str(log_file),
                            "content": f"Error reading file: {str(e)}"
                        })

    # Sort logs by filename (which includes timestamp) in descending order (newest first)
    logs.sort(key=lambda x: x['filename'], reverse=True)

    return JSONResponse(content={"logs": logs, "stage_id": stage_id, "step": step})
