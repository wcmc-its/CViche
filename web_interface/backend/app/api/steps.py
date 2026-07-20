"""Step details and data API endpoints."""
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Step, Log, Run, User
from app.schemas import StepDetail, LogEntry, OutputPreview
from app.auth import get_current_user, require_admin
from app.services.run_service import check_run_access
from app.storage import get_storage
from app.errors import bad_request, not_found, internal_error, forbidden

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

    # Stage JSON files are internal pipeline artifacts -- restrict to admins.
    # The final .docx (and any other non-JSON output) stays available to the
    # run owner. Gate before the storage short-circuit so the S3 redirect path
    # is covered too.
    if Path(filename).name.endswith(".json") and current_user.role != "admin":
        raise forbidden("Admin access required to access stage JSON.")

    # Prefer durable storage (S3 in prod) so downloads survive pod recycling
    # (#38): the pipeline writes outputs to the pod's ephemeral filesystem, so
    # a restart wipes them and the local FileResponse below 404s. JSON previews
    # need the file contents inline, so only short-circuit for real downloads.
    download_name = Path(filename).name
    if not (preview and download_name.endswith(".json")):
        storage = get_storage()
        storage_key = f"outputs/{download_name}"
        try:
            if storage.exists(run_id, storage_key):
                url = storage.get_download_url(
                    run_id, storage_key, download_name=download_name
                )
                if url:
                    return RedirectResponse(url, status_code=307)
        except Exception as e:
            # Fall back to local serving if storage is unreachable.
            logger.warning(
                "Storage lookup failed for %s/%s: %s", run_id, storage_key, e
            )

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


@router.get("/run/{run_id}/input")
async def download_input_file(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Download the ORIGINAL uploaded CV, named as the user uploaded it.

    The source is retained in durable storage at input/<run_id>.<ext> because
    restart/retry re-materialize it (see runs._materialize_input_if_missing),
    but it was only ever read internally — there was no way to get the original
    file back out. Same owner-or-admin gate as the outputs download.
    """
    run = check_run_access(run_id, current_user, db)  # 404/403 as needed; returns the Run

    file_type = (run.file_type or "").lower() or "docx"
    key = f"input/{run_id}.{file_type}"
    media_type = (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        if file_type == "docx" else "application/pdf"
    )
    # Both consumers of this name percent-encode it into Content-Disposition
    # (below, and S3's ResponseContentDisposition), so it needs no stripping.
    original_name = (run.filename or "").strip() or f"{run_id}.{file_type}"

    storage = get_storage()
    try:
        if not storage.exists(run_id, key):
            raise not_found("Original upload is not available for this run")
        # S3 returns a presigned URL that renames the download; local storage
        # returns None (no presigned URLs), so proxy the bytes ourselves.
        url = storage.get_download_url(run_id, key, download_name=original_name)
        if url:
            return RedirectResponse(url, status_code=307)
        data = storage.get_file(run_id, key)
    except HTTPException:
        raise
    except FileNotFoundError:
        raise not_found("Original upload is not available for this run")
    except Exception as e:
        logger.warning("Original-input download failed for run %s: %s", run_id, e)
        raise internal_error("Could not retrieve the original upload")

    return Response(
        content=data,
        media_type=media_type,
        # RFC 5987 encoding: headers are latin-1, and a CV named "Smith's CV.docx"
        # (smart quote) or "Dvořák.docx" would otherwise raise on response build.
        headers={
            "Content-Disposition":
                f"attachment; filename*=utf-8''{quote(original_name, safe='')}"
        },
    )


async def _generate_preview_from_path(file_path: Path) -> OutputPreview | None:
    """Generate a preview of a JSON file from a direct path."""
    if not file_path.exists() or not str(file_path).endswith(".json"):
        return None

    try:
        with open(file_path, "r") as f:
            data = json.load(f)

        return _parse_json_to_preview(data)

    except Exception:
        logger.exception("Error generating preview")
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

    except Exception:
        logger.exception("Error generating preview")
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

    except Exception:
        logger.exception("Error generating preview")
        return None

    return None


# Distinct prefix from the download route on purpose: /data/{filename:path} has a
# greedy :path that would otherwise swallow a trailing "/json" and shadow this
# route (whichever registers first wins). /json/{filename:path} can't collide.
@router.get("/run/{run_id}/json/{filename:path}")
async def get_json_content(
    run_id: str,
    filename: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
):
    """Get raw JSON content for display in viewer. Admin-only: stage JSON is an
    internal pipeline artifact, not user-facing output."""

    check_run_access(run_id, current_user, db)

    # Local pod filesystem first. _resolve_safe_path also runs the security guard
    # (absolute/traversal -> 400 "Invalid filename"), so it must come first.
    try:
        file_path = _resolve_safe_path(filename, run_id)
        if not str(file_path).endswith(".json"):
            raise bad_request("Only JSON files can be viewed")
        with open(file_path, "r") as f:
            data = json.load(f)
        return JSONResponse(content={
            "filename": filename,
            "size_bytes": file_path.stat().st_size,
            "content": data,
        })
    except HTTPException as e:
        if e.status_code != 404:
            raise  # 400 invalid/traversal/non-json -> propagate; only a local miss falls through
    except Exception:
        raise internal_error("Error reading file")

    # Durable storage fallback (S3 in prod): the file may live in S3 but not on
    # this pod. Mirrors get_data_file (#38) so the viewer survives pod recycles /
    # multi-replica routing the same way the download button does.
    download_name = Path(filename).name
    if not download_name.endswith(".json"):
        raise not_found("File not found")
    storage = get_storage()
    storage_key = f"outputs/{download_name}"
    try:
        raw = storage.get_file(run_id, storage_key)
        data = json.loads(raw)
        return JSONResponse(content={
            "filename": filename,
            "size_bytes": len(raw),
            "content": data,
        })
    except FileNotFoundError:
        raise not_found("File not found")
    except Exception:
        logger.warning("Storage JSON read failed for %s/%s", run_id, storage_key, exc_info=True)
        raise internal_error("Error reading file")


# Mapping of stage IDs to the set of `purpose` values written into prompt log
# filenames by src/unified_pipeline/core/prompt_logger.py.
#
# Filenames look like: {YYYY-MM-DD_HH-MM-SS}_{purpose}_{12-char-hex-id}[_READABLE].{txt,json}
# Today every LLM call routed through unified_pipeline.llm_client.call_llm logs
# with purpose=<stage> (e.g. "stage_3a"). Older files in the directory used
# more specific purposes (e.g. "taxonomy_mapping_pass1", "field_extraction_batch_S8");
# they're preserved here for viewing historical runs.
#
# Each stage entry separates `exact` matches (the purpose must equal the value)
# from `prefix` matches (the purpose must equal the value or start with
# value + "_"). The prefix list intentionally excludes ambiguous bare names
# like "stage_4" — that gets exact-only treatment so it doesn't swallow
# `stage_4_5_*` files that belong to stage 4.5.
#
# Stage IDs without LLM activity get an empty list and surface a friendly
# explanation via STAGES_WITHOUT_PROMPT_LOGS instead.
STAGE_TO_PURPOSES = {
    '1a': {'exact': [], 'prefix': []},
    '1b': {'exact': [], 'prefix': []},
    '2':  {'exact': ['stage_2', 'stage_2_entry_extraction'],
           'prefix': []},
    '2a': {'exact': ['stage_2a'],
           'prefix': ['segmentation']},
    '3a': {'exact': ['stage_3a', 'core_taxonomy_mapper', 'core_taxonomy_v2',
                     'stage_3a_header_taxonomy', 'taxonomy_mapping_pass1'],
           'prefix': []},
    '3b': {'exact': ['stage_3b', 'stage_3b_entry_classification',
                     'stage_3b_t_validation', 'stage_3b_fragment_reconnection',
                     'taxonomy_mapping_pass2', 'taxonomy_mapping_pass2_batch'],
           'prefix': []},
    '4':  {'exact': ['stage_4', 'core_extraction_recovery', 'core_personal_info',
                     'core_repair_segmentation', 'core_section_orchestrator',
                     'core_candidate_surfacer', 'cv_parser_classifier',
                     'cv_parser_evaluator', 'cv_parser_structurer',
                     'validator_llm'],
           'prefix': ['stage_4_field', 'stage_4_extraction',
                      'field_extraction_batch', 'parser_']},
    '4.5': {'exact': ['stage_4_5', '4.5_summary_generation', '4.5_m1_scoring'],
            'prefix': ['stage_4_5', 'research_summary']},
    '5':  {'exact': [], 'prefix': []},
    '5b': {'exact': ['stage_5b', 'institution_enrichment'],
           'prefix': []},
    '5c': {'exact': ['stage_5c', 'stage_5c_teaching_formatting'],
           'prefix': ['teaching_formatter']},
    '5d': {'exact': ['stage_5d', 'stage_5d_citation_formatting'],
           'prefix': ['citation_formatter']},
    '6':  {'exact': ['stage_6'], 'prefix': []},
}

# Filename: {YYYY-MM-DD}_{HH-MM-SS}_{purpose}_{12 hex chars}[_READABLE|_RESPONSE].{ext}
_PROMPT_LOG_FILENAME_RE = re.compile(
    r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_(?P<purpose>.+)_[0-9a-f]{12}'
    r'(?:_READABLE|_RESPONSE)?\.(?:txt|json)$'
)


def _purpose_from_filename(name: str):
    """Extract the `purpose` field from a prompt-log filename, or None if it
    doesn't match the prompt_logger naming convention."""
    m = _PROMPT_LOG_FILENAME_RE.match(name)
    return m.group('purpose') if m else None


def _purpose_matches_stage(purpose: str, stage_purposes) -> bool:
    """A filename's purpose belongs to a stage if it equals one of the
    `exact` values, or starts with one of the `prefix` values followed by
    an underscore. The split prevents collisions like `stage_4_5_*` being
    claimed by stage 4 just because it shares the `stage_4_` prefix."""
    if not stage_purposes:
        return False
    if purpose in stage_purposes.get('exact', ()):
        return True
    for known in stage_purposes.get('prefix', ()):
        if purpose.startswith(known + '_'):
            return True
    return False

# Stages that don't have prompt logs, with an explanation.
# NOTE: stages 5b and 6 were mistakenly included here. Both make LLM calls --
# 5b runs ROR lookups AND `call_llm(stage="stage_5b")` from institution
# enrichment, and 6 calls `call_llm(stage="stage_6")` for geographic scope
# classification during template population. Prompts ARE being logged for
# both, but the short-circuit returned the "non-LLM stage" message before
# the file walker ran. Keep only stages with zero LLM activity here.
STAGES_WITHOUT_PROMPT_LOGS = {
    '1a': "Stage 1a (Hierarchy Extraction) uses direct OpenAI API calls without prompt logging.",
    '1b': "Stage 1b (Hierarchy Mapping) is a non-LLM stage - no prompts are used.",
    '5': "Stage 5 (PubMed Enrichment) is a non-LLM stage - uses PubMed API.",
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

    logs: list[dict] = []
    seen_files: set[str] = set()

    # The set of `purpose` values that belong to this stage.
    purposes = STAGE_TO_PURPOSES.get(stage_id, [])
    if not purposes:
        return JSONResponse(content={"logs": [], "stage_id": stage_id, "step": step})

    # Per-run storage: the orchestrator replicates new prompt log files into
    # storage at step-end, so this works across container restarts in prod
    # (S3) and locally (LocalRunStorage mirrors them into the run dir).
    storage = get_storage()
    try:
        storage_keys = storage.list_files(run_id, prefix="prompt_logs/")
    except Exception as exc:
        logger.warning("Could not list prompt logs in storage for %s: %s", run_id, exc)
        storage_keys = []

    for key in sorted(storage_keys):
        filename = key.rsplit('/', 1)[-1]
        if not filename.endswith('.txt'):
            continue
        if filename in seen_files:
            continue
        purpose = _purpose_from_filename(filename)
        if not purpose or not _purpose_matches_stage(purpose, purposes):
            continue
        seen_files.add(filename)
        try:
            content = storage.get_file(run_id, key).decode('utf-8', errors='replace')
            logs.append({
                "filename": filename,
                "content": content[:50000],
            })
        except Exception as exc:
            logs.append({
                "filename": filename,
                "content": f"Error reading file: {exc}",
            })

    # Fallback: legacy local-fs path. Picks up runs that completed before
    # storage-replication landed, and lets dev "still works" while the
    # backend pipeline runs on the same machine as the API.
    project_root = Path(__file__).parent.parent.parent.parent.parent
    legacy_prompt_logs_dir = project_root / "src" / "unified_pipeline" / "prompt_logs"
    if legacy_prompt_logs_dir.exists():
        for log_file in sorted(legacy_prompt_logs_dir.iterdir()):
            if not log_file.name.endswith('.txt'):
                continue
            if log_file.name in seen_files:
                continue
            purpose = _purpose_from_filename(log_file.name)
            if not purpose or not _purpose_matches_stage(purpose, purposes):
                continue
            if log_file.stat().st_mtime < run_start_timestamp:
                continue
            seen_files.add(log_file.name)
            try:
                content = log_file.read_text(encoding='utf-8', errors='replace')
                logs.append({
                    "filename": str(log_file),
                    "content": content[:50000],
                })
            except Exception as exc:
                logs.append({
                    "filename": str(log_file),
                    "content": f"Error reading file: {exc}",
                })

    # Newest first.
    logs.sort(key=lambda x: x['filename'], reverse=True)

    return JSONResponse(content={"logs": logs, "stage_id": stage_id, "step": step})
