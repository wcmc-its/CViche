"""Feedback API endpoints for collecting user feedback on pipeline runs."""
import asyncio
import functools
import json
import logging
from collections.abc import Mapping
from pathlib import Path
from types import MappingProxyType
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy.orm import Session

from app.api.upload import _read_bounded
from app.auth import get_current_user
from app.database import get_db
from app.errors import bad_request, not_found
from app.models import Feedback, Run, RunState, Step, User
from app.schemas import (
    CorrectedCopyDetail,
    CorrectedDocxResponse,
    FeedbackDetail,
    FeedbackResponse,
    FeedbackSubmit,
    RunFeedbackStatus,
    iso_with_offset,
)
from app.services import corrected_copy_service, review_loop_service
from app.services.config_service import MAX_UPLOAD_SIZE
from app.services.malware_scan import ScanState, require_clean_scan, scan_state
from app.services.run_creation import _reject_active_docx_content
from app.services.run_service import check_run_access
from app.services.runs_admin_query import load_run_feedback_with_reviewers
from app.services.upload_validation import _validate_docx_magic
from app.storage import get_storage
from app.storage.base import RunStorage
from app.timeutil import utc_now
from unified_pipeline.stage3b.io import load_taxonomy

logger = logging.getLogger(__name__)

router = APIRouter()

DOCX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
# web_interface/outputs/: where _get_populated_wcm_sections looks for a run's files.
OUTPUTS_ROOT = Path(__file__).parent.parent.parent.parent / "outputs"


@functools.cache
def _taxonomy_section_labels() -> Mapping[str, str]:
    """Code -> label for every code in core/taxonomy_v7.json. Read from the
    taxonomy, not a hand-kept copy: the old copy had drifted to a retired
    code scheme (S3 'Letters to Editor', S10-S15, T1-T7; #1782)."""
    return MappingProxyType({
        entry["code"]: entry["label"] for entry in load_taxonomy()["codes"]
    })


def _get_populated_wcm_sections(run_id: str) -> list[dict]:
    """Return list of WCM sections that have data, based on stage 4 output files."""
    outputs_dir = OUTPUTS_ROOT / run_id
    stage_4_dir = outputs_dir / "stage_4_wcm_templates"

    if not stage_4_dir.exists():
        return []

    populated = []
    metadata_files = list(stage_4_dir.glob("*_template_metadata.json"))

    for metadata_file in metadata_files:
        try:
            with open(metadata_file, 'r', encoding='utf-8') as f:
                metadata = json.load(f)

            records_processed = metadata.get('records_processed', {})
            for entity_type, count in records_processed.items():
                if count > 0:
                    populated.append({
                        "entity_type": entity_type,
                        "count": count,
                    })
        except Exception as e:
            logger.warning("Error reading template metadata %s: %s", metadata_file.name, e)

    # Also check enriched files for section-level detail
    enriched_dir = outputs_dir / "stage_2d_enriched"
    if enriched_dir.exists():
        import re
        for enriched_file in enriched_dir.glob("section_*_enriched.json"):
            section_match = re.search(r'section_([A-Z]\d*)_', enriched_file.name)
            if section_match:
                section_id = section_match.group(1)
                section_labels = _taxonomy_section_labels()
                if section_id in section_labels:
                    # Check it's not already covered by entity_type entries
                    already_listed = any(
                        p.get("section_id") == section_id for p in populated
                    )
                    if not already_listed:
                        populated.append({
                            "section_id": section_id,
                            "section_name": section_labels[section_id],
                        })

    return populated


def serialize_feedback(feedback: Feedback) -> dict:
    """Every stored answer field of a Feedback row, JSON-ready (the one
    per-field serialisation; GET /run/{id}/feedback and /feedback/all share it)."""
    return {
        "id": feedback.id,
        "run_id": feedback.run_id,
        "user_id": feedback.user_id,
        "reviewer_role": feedback.reviewer_role,
        "overall_accuracy": feedback.overall_accuracy,
        "overall_completeness": feedback.overall_completeness,
        "overall_usefulness": feedback.overall_usefulness,
        "manual_conversion_effort": feedback.manual_conversion_effort,
        "correction_effort": feedback.correction_effort,
        "enrichment_quality": feedback.enrichment_quality,
        "summary_generated": feedback.summary_generated,
        "summary_quality": feedback.summary_quality,
        "issue_missing_content": feedback.issue_missing_content,
        "issue_split_merged": feedback.issue_split_merged,
        "issue_wrong_section": feedback.issue_wrong_section,
        "issue_inaccurate": feedback.issue_inaccurate,
        "issue_ai_enrichment": feedback.issue_ai_enrichment,
        "issue_formatting": feedback.issue_formatting,
        "issue_locations": json.loads(feedback.issue_locations) if feedback.issue_locations else None,
        "biggest_issue": feedback.biggest_issue,
        "likelihood_to_recommend": feedback.likelihood_to_recommend,
        "submitted_at": iso_with_offset(feedback.submitted_at) if feedback.submitted_at else None,
    }


@router.get("/run/{run_id}/feedback")
async def get_feedback(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get existing feedback for this user on this run, plus run context.

    Returns the user's feedback (or null if none submitted), along with
    which pipeline stages completed and which WCM sections are populated.
    """
    run = check_run_access(run_id, current_user, db, read_only=True)

    # Get existing feedback for this user on this run
    feedback = db.query(Feedback).filter(
        Feedback.run_id == run_id,
        Feedback.user_id == current_user.id,
    ).first()

    feedback_data = serialize_feedback(feedback) if feedback else None

    # Query completed stages from Step table
    completed_steps = (
        db.query(Step.stage_id)
        .filter(Step.run_id == run_id, Step.status == "complete")
        .all()
    )
    completed_stages = [s.stage_id for s in completed_steps if s.stage_id]

    # If stage 6 completed, get populated WCM sections
    wcm_sections = []
    if "6" in completed_stages:
        wcm_sections = _get_populated_wcm_sections(run_id)

    return {
        "feedback": feedback_data,
        "run_context": {
            "run_id": run.id,
            "filename": run.filename,
            "status": run.status,
            "completed_stages": completed_stages,
            "wcm_sections": wcm_sections,
        },
    }


@router.get("/run/{run_id}/feedback/all", response_model=list[FeedbackDetail])
def get_all_feedback(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[FeedbackDetail]:
    """Every reviewer's feedback on this run, newest first.

    Run owner, admin or staff only (403 otherwise, 404 for an unknown run).
    """
    check_run_access(run_id, current_user, db, read_only=True)
    rows = load_run_feedback_with_reviewers(db, run_id)
    return [
        FeedbackDetail(**serialize_feedback(feedback), display_name=display_name)
        for feedback, display_name in rows
    ]


@router.post("/run/{run_id}/feedback", response_model=FeedbackResponse, status_code=201)
async def submit_feedback(
    run_id: str,
    body: FeedbackSubmit,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Submit feedback for a pipeline run.

    Returns 409 if the user has already submitted feedback for this run.
    """
    run = check_run_access(run_id, current_user, db)

    # Check for existing feedback
    existing = db.query(Feedback).filter(
        Feedback.run_id == run_id,
        Feedback.user_id == current_user.id,
    ).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "duplicate_feedback",
                "message": "You have already submitted feedback for this run.",
            },
        )

    # Validate required integer ranges
    if not (1 <= body.overall_usefulness <= 5):
        raise HTTPException(
            status_code=422,
            detail={"error": "validation_error", "message": "overall_usefulness must be between 1 and 5"},
        )
    if not (1 <= body.likelihood_to_recommend <= 5):
        raise HTTPException(
            status_code=422,
            detail={"error": "validation_error", "message": "likelihood_to_recommend must be between 1 and 5"},
        )
    if body.overall_accuracy is not None and not (1 <= body.overall_accuracy <= 10):
        raise HTTPException(
            status_code=422,
            detail={"error": "validation_error", "message": "overall_accuracy must be between 1 and 10"},
        )
    if body.overall_completeness is not None and not (1 <= body.overall_completeness <= 10):
        raise HTTPException(
            status_code=422,
            detail={"error": "validation_error", "message": "overall_completeness must be between 1 and 10"},
        )
    if body.enrichment_quality is not None and not (1 <= body.enrichment_quality <= 5):
        raise HTTPException(
            status_code=422,
            detail={"error": "validation_error", "message": "enrichment_quality must be between 1 and 5"},
        )
    if body.summary_quality is not None and not (1 <= body.summary_quality <= 5):
        raise HTTPException(
            status_code=422,
            detail={"error": "validation_error", "message": "summary_quality must be between 1 and 5"},
        )

    # Convert issue_locations list to JSON string
    issue_locations_json = json.dumps(body.issue_locations) if body.issue_locations else None

    # Convert summary_generated bool to int
    summary_generated_int = None
    if body.summary_generated is not None:
        summary_generated_int = 1 if body.summary_generated else 0

    feedback = Feedback(
        run_id=run_id,
        user_id=current_user.id,
        reviewer_role=body.reviewer_role,
        overall_accuracy=body.overall_accuracy,
        overall_completeness=body.overall_completeness,
        overall_usefulness=body.overall_usefulness,
        manual_conversion_effort=body.manual_conversion_effort,
        correction_effort=body.correction_effort,
        enrichment_quality=body.enrichment_quality,
        summary_generated=summary_generated_int,
        summary_quality=body.summary_quality,
        issue_missing_content=body.issue_missing_content,
        issue_split_merged=body.issue_split_merged,
        issue_wrong_section=body.issue_wrong_section,
        issue_inaccurate=body.issue_inaccurate,
        issue_ai_enrichment=body.issue_ai_enrichment,
        issue_formatting=body.issue_formatting,
        issue_locations=issue_locations_json,
        biggest_issue=body.biggest_issue,
        likelihood_to_recommend=body.likelihood_to_recommend,
    )

    db.add(feedback)
    db.commit()
    db.refresh(feedback)

    logger.info("Feedback submitted for run %s by user %s", run_id, current_user.id)

    # Best-effort Teams notification. Off the event loop (blocking POST);
    # swallows all failures so a webhook problem can never affect the
    # already-committed feedback or the response.
    try:
        from app.services.notifications import notify_feedback_submitted
        submitter = current_user.display_name or current_user.email
        await asyncio.get_running_loop().run_in_executor(
            None, notify_feedback_submitted, feedback, run, submitter
        )
    except Exception as e:  # noqa: BLE001 -- best-effort, must never raise
        logger.warning("Feedback notification failed for run %s: %s", run_id, e)

    return FeedbackResponse(
        id=feedback.id,
        run_id=feedback.run_id,
        user_id=feedback.user_id,
        reviewer_role=feedback.reviewer_role,
        overall_usefulness=feedback.overall_usefulness,
        likelihood_to_recommend=feedback.likelihood_to_recommend,
        submitted_at=feedback.submitted_at,
    )


@router.post("/run/{run_id}/feedback/corrected-docx", response_model=CorrectedDocxResponse)
async def upload_corrected_docx(
    run_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> CorrectedDocxResponse:
    """Store a reviewer's corrected copy of the delivered document with the
    run, diff it against that document (doctor/docx_diff.py) and store the
    typed diff beside it (#1587). Each uploader keeps one copy per run: a
    second upload by the same user replaces theirs, never another user's
    (#1754). Returns a one-line count only; the
    comment-fate verdicts and the doctor's re-run on the copy (#1654) follow
    in a background task.

    Owner, admin, and staff on any run (staff are read-only everywhere else
    and still cannot submit the feedback survey), and the CV upload's gates
    on the file: its size cap, a .docx name, the docx magic and zip-bomb
    bounds, and the active-content refusal. GuardDuty scans the bucket as for every
    upload; the copy is served back only by download_corrected_copy, which
    waits on that scan as the original CV's download does.
    """
    check_run_access(run_id, current_user, db, allow_staff=True)
    if not file.filename or Path(file.filename).suffix.lower() != ".docx":
        raise bad_request("Upload the corrected document as a Word (.docx) file.")
    content = await _read_bounded(file, MAX_UPLOAD_SIZE)
    if not _validate_docx_magic(content):
        raise bad_request("File content does not match .docx format. The file may be corrupted or mislabeled.")
    await _reject_active_docx_content(content)
    changes = await run_in_threadpool(review_loop_service.record_corrected_docx, db, run_id,
                                     current_user.id, content, utc_now())
    logger.info("Corrected copy recorded for run %s by user %s: %d changes", run_id, current_user.id, changes)
    # Verdicts from the review copy's comments and the doctor's re-run (#1654)
    # run after the response, so the confirmation never waits on them; their
    # failure is logged there, never shown to the uploader.
    background_tasks.add_task(review_loop_service.review_corrected_copy_in_background,
                              run_id, current_user.id, content)
    return CorrectedDocxResponse(changes=changes, summary=review_loop_service.changes_summary(changes))


def _listed_scan_state(storage: RunStorage, run_id: str, key: str) -> ScanState:
    """`scan_state` for one row of the copies list. A tag that can't be read
    (a backfilled row whose object is gone, no s3:GetObjectTagging, an outage)
    marks that copy unscannable instead of failing the whole list; the
    download route still raises on it."""
    try:
        return scan_state(storage, run_id, key)
    except Exception:
        logger.exception("Scan tag unreadable for corrected copy %s of run %s", key, run_id)
        return ScanState.UNSCANNABLE


@router.get("/run/{run_id}/feedback/corrected-copies", response_model=list[CorrectedCopyDetail])
def list_corrected_copies(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CorrectedCopyDetail]:
    """Every reviewer's corrected copy of the run, newest first: who uploaded
    it (and as owner, staff or admin), when, how many changes, and whether
    the malware scan lets it be downloaded yet.

    Run owner, admin or staff (403 otherwise, 404 for an unknown run): the
    owner sees staff's copies of their run too.
    """
    check_run_access(run_id, current_user, db, read_only=True)
    storage = get_storage()
    return [
        CorrectedCopyDetail(id=copy.id, uploader=copy.uploader, role=copy.role,
                            uploaded_at=copy.uploaded_at, changes=copy.changes,
                            mine=copy.user_id == current_user.id,
                            download=_listed_scan_state(storage, run_id, copy.storage_key))
        for copy in corrected_copy_service.load_copies(db, [run_id]).get(run_id, [])
    ]


@router.get("/run/{run_id}/feedback/corrected-copies/{copy_id}")
def download_corrected_copy(
    run_id: str,
    copy_id: int,
    as_url: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    """Download one reviewer's corrected copy of the run (copy_id from
    list_corrected_copies), named after the run's upload and its uploader.

    Run owner, admin or staff, as for the list. A reviewer uploaded the file,
    so it is served only once the malware scan passes it, exactly as the
    original CV is (malware_scan.require_clean_scan: 409 while scanning, 403
    when flagged or unscannable). ``?as_url=true`` answers 200 ``{"url": ...}``
    after the same checks, so the page can show a refusal's message; ``url``
    is null on local storage.
    """
    run = check_run_access(run_id, current_user, db, read_only=True)
    copies = corrected_copy_service.load_copies(db, [run_id]).get(run_id, [])
    copy = next((c for c in copies if c.id == copy_id), None)
    if copy is None:
        raise not_found("Corrected copy not found")
    name = review_loop_service.corrected_download_name(run.filename, copy.uploader)
    storage = get_storage()
    try:
        if not storage.exists(run_id, copy.storage_key):
            raise not_found("Corrected copy not found")
        require_clean_scan(storage, run_id, copy.storage_key)
        # S3 returns a presigned URL that names the download (quoted there);
        # local storage returns None, so the bytes are proxied below.
        url = storage.get_download_url(run_id, copy.storage_key, download_name=name)
        if as_url:
            # A presigned URL is a short-lived credential: never cache it.
            return JSONResponse(content={"url": url}, headers={"Cache-Control": "no-store"})
        if url:
            return RedirectResponse(url, status_code=307)
        data = storage.get_file(run_id, copy.storage_key)
    except FileNotFoundError as e:
        raise not_found("Corrected copy not found") from e
    return Response(
        content=data,
        media_type=DOCX_MEDIA_TYPE,
        # RFC 5987: headers are latin-1, and the name carries the CV's and
        # the uploader's names.
        headers={"Content-Disposition": f"attachment; filename*=utf-8''{quote(name, safe='')}"},
    )


@router.get("/runs/feedback-status", response_model=list[RunFeedbackStatus])
async def get_feedback_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return feedback status for all of the current user's completed runs.

    For each completed run owned by the user, indicates whether feedback
    has been submitted.

    Kept for compatibility only: the runs table no longer uses it (GET /runs
    carries a per-run ``feedback`` summary covering every reviewer).
    """
    # Get all completed runs for this user
    completed_runs = (
        db.query(Run.id)
        .filter(Run.user_id == current_user.id, Run.status == RunState.COMPLETE)
        .all()
    )
    completed_run_ids = [r.id for r in completed_runs]

    if not completed_run_ids:
        return []

    # Get run IDs that have feedback from this user
    feedback_run_ids = set(
        row.run_id
        for row in db.query(Feedback.run_id)
        .filter(
            Feedback.user_id == current_user.id,
            Feedback.run_id.in_(completed_run_ids),
        )
        .all()
    )

    return [
        RunFeedbackStatus(
            run_id=run_id,
            has_feedback=run_id in feedback_run_ids,
        )
        for run_id in completed_run_ids
    ]
