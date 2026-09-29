"""Step details and data API endpoints."""
import json
import logging
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import StepDetail, LogEntry
from app.auth import get_current_user, require_admin
from app.services import artifact_service, prompt_log_service
from app.services.run_service import check_run_access
from app.storage import get_storage
from app.errors import bad_request, not_found, internal_error, forbidden

logger = logging.getLogger(__name__)

router = APIRouter()

# Re-exported for existing tests / callers that import these off steps.py --
# both moved to app.services.prompt_log_service (#780 review r3965813607#2).
STAGE_TO_PURPOSES = prompt_log_service.STAGE_TO_PURPOSES
STAGES_WITHOUT_PROMPT_LOGS = prompt_log_service.STAGES_WITHOUT_PROMPT_LOGS

# Re-exported for existing tests that call steps.py's validator directly
# (moved to app.services.artifact_service, same review point).
_validate_run_id = artifact_service.validate_run_id


@router.get("/run/{run_id}/step/{step_number}", response_model=StepDetail)
def get_step_detail(
    run_id: str,
    step_number: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StepDetail:
    """Get detailed information about a specific step."""

    check_run_access(run_id, current_user, db)

    step, logs = artifact_service.get_step_with_logs(db, run_id, step_number)
    if not step:
        raise not_found(f"Step {step_number} not found for run {run_id}")

    log_entries = [
        LogEntry(
            time=log.timestamp.strftime("%H:%M:%S"),
            level=log.level,
            message=log.message
        )
        for log in logs
    ]

    # Parse output files. Only malformed persisted data is recoverable here --
    # a programming error elsewhere must not silently become an empty list
    # (#780 review r3965790925).
    output_files = []
    if step.output_files:
        try:
            output_files = json.loads(step.output_files)
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning(
                "Malformed output_files for run=%s step=%s: %s", run_id, step_number, exc
            )
            output_files = []

    # Try to load preview of first output file. The pipeline stores absolute
    # paths; the resolver takes basenames only (it 400s on a leading "/"), so
    # passing the raw path failed every completed step's detail request.
    output_preview = None
    if output_files and step.status == "complete":
        output_preview = artifact_service.generate_preview(
            db, run_id, Path(output_files[0]).name)

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


@router.get("/run/{run_id}/data/{filename:path}")
def get_data_file(
    run_id: str,
    filename: str,
    preview: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Download or preview a data file."""

    check_run_access(run_id, current_user, db)

    # Stage JSON files are internal pipeline artifacts -- restrict to admins.
    # The final .docx (and any other non-JSON output) stays available to the
    # run owner. This single gate deliberately sits ABOVE every branch below --
    # before the storage short-circuit AND before `preview` is ever read -- so it
    # covers the S3 redirect, the download and the ?preview=true JSON viewer
    # alike. Do not move it into a branch or duplicate it per-branch.
    if artifact_service.is_json_artifact(filename) and current_user.role != "admin":
        raise forbidden("Admin access required to access stage JSON.")

    resolved = artifact_service.resolve_artifact(db, run_id, filename)
    download_name = resolved.basename

    # Prefer durable storage (S3 in prod) so downloads survive pod recycling
    # (#38): the pipeline writes outputs to the pod's ephemeral filesystem, so
    # a restart wipes them and the local FileResponse below 404s. JSON previews
    # need the file contents inline, so only short-circuit for real downloads.
    if not (preview and artifact_service.is_json_artifact(download_name)):
        storage = get_storage()
        try:
            if storage.exists(run_id, resolved.storage_key):
                url = storage.get_download_url(
                    run_id, resolved.storage_key, download_name=download_name
                )
                if url:
                    return RedirectResponse(url, status_code=307)
        except Exception as e:
            # Fall back to local serving if storage is unreachable.
            logger.warning(
                "Storage lookup failed for %s/%s: %s", run_id, resolved.storage_key, e
            )

    if resolved.local_path is None:
        raise not_found("File not found")
    file_path = resolved.local_path

    # For Word docs, always download
    if download_name.endswith(".docx"):
        return FileResponse(
            str(file_path),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename=download_name
        )

    # For JSON, can preview or download
    if artifact_service.is_json_artifact(download_name):
        if preview:
            # Return full preview (bounded -- see artifact_service.PREVIEW_MAX_ROWS)
            preview_data = artifact_service.generate_preview_from_path(file_path)
            return JSONResponse(content=preview_data.dict() if preview_data else {})
        else:
            return FileResponse(str(file_path), media_type="application/json", filename=download_name)

    # Default: download
    return FileResponse(str(file_path), filename=download_name)


@router.get("/run/{run_id}/input")
def download_input_file(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Download the ORIGINAL uploaded CV, named as the user uploaded it.

    The source is retained in durable storage at input/<run_id>.<ext> because
    restart/retry re-materialize it (see run_service._materialize_input_if_missing),
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


# Distinct prefix from the download route on purpose: /data/{filename:path} has a
# greedy :path that would otherwise swallow a trailing "/json" and shadow this
# route (whichever registers first wins). /json/{filename:path} can't collide.
@router.get("/run/{run_id}/json/{filename:path}")
def get_json_content(
    run_id: str,
    filename: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin),
) -> JSONResponse:
    """Get raw JSON content for display in viewer. Admin-only: stage JSON is an
    internal pipeline artifact, not user-facing output."""

    check_run_access(run_id, current_user, db)

    resolved = artifact_service.resolve_artifact(db, run_id, filename)

    # Local pod filesystem first.
    if resolved.local_path is not None:
        if not artifact_service.is_json_artifact(resolved.basename):
            raise bad_request("Only JSON files can be viewed")
        try:
            with open(resolved.local_path) as f:
                data = json.load(f)
            return JSONResponse(content={
                "filename": filename,
                "size_bytes": resolved.local_path.stat().st_size,
                "content": data,
            })
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning(
                "JSON viewer read failed for %s/%s: %s", run_id, resolved.basename, exc
            )
            raise internal_error("Error reading file")

    # Durable storage fallback (S3 in prod): the file may live in S3 but not on
    # this pod. Mirrors get_data_file (#38) so the viewer survives pod recycles /
    # multi-replica routing the same way the download button does.
    if not artifact_service.is_json_artifact(resolved.basename):
        raise not_found("File not found")
    storage = get_storage()
    try:
        raw = storage.get_file(run_id, resolved.storage_key)
        data = json.loads(raw)
        return JSONResponse(content={
            "filename": filename,
            "size_bytes": len(raw),
            "content": data,
        })
    except FileNotFoundError:
        raise not_found("File not found")
    except Exception:
        logger.warning("Storage JSON read failed for %s/%s", run_id, resolved.storage_key, exc_info=True)
        raise internal_error("Error reading file")


@router.get("/run/{run_id}/prompt-logs")
def get_prompt_logs(
    run_id: str,
    step: int = 1,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> JSONResponse:
    """Get prompt logs for a specific step, filtered to only show logs from this run."""

    # Verify access and get the run
    run_record = check_run_access(run_id, current_user, db)
    if not run_record.started_at:
        return JSONResponse(content={"logs": [], "error": "Run has no start time"})

    stage_id = prompt_log_service.resolve_stage_id_for_step(db, run_id, step)
    if stage_id is None:
        return JSONResponse(content={"logs": []})

    # Check if this stage doesn't have prompt logs
    if stage_id in prompt_log_service.STAGES_WITHOUT_PROMPT_LOGS:
        return JSONResponse(content={
            "logs": [],
            "stage_id": stage_id,
            "step": step,
            "message": prompt_log_service.STAGES_WITHOUT_PROMPT_LOGS[stage_id]
        })

    # The set of `purpose` values that belong to this stage.
    purposes = prompt_log_service.STAGE_TO_PURPOSES.get(stage_id, [])
    if not purposes:
        return JSONResponse(content={"logs": [], "stage_id": stage_id, "step": step})

    # Per-run storage: the orchestrator replicates new prompt log files into
    # storage at step-end, so this works across container restarts in prod
    # (S3) and locally (LocalRunStorage mirrors them into the run dir).
    storage = get_storage()
    page = prompt_log_service.list_prompt_logs(storage, run_id, stage_id, limit=limit, offset=offset)

    return JSONResponse(content={
        "logs": page.logs,
        "stage_id": stage_id,
        "step": step,
        "total": page.total,
        "truncated": page.truncated,
    })
