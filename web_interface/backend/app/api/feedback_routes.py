"""Feedback API endpoints for collecting user feedback on pipeline runs."""
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Run, Step, Feedback, User
from app.schemas import FeedbackSubmit, FeedbackResponse, RunFeedbackStatus
from app.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_run_access(run_id: str, current_user: User, db: Session) -> Run:
    """Verify run exists and user has access. Returns the Run."""
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Run not found"},
        )
    if run.user_id and run.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "Access denied"},
        )
    return run


# Complete WCM section mapping (matches runs.py)
ALL_WCM_SECTIONS = {
    'A': 'Personal Data',
    'B1': 'Academic Degrees',
    'B2': 'Other Educational Experiences',
    'B3': 'Residency & Fellowship Training',
    'C': 'Postdoctoral Training',
    'D1': 'Academic Appointments',
    'D2': 'Hospital Appointments',
    'D3': 'Other Professional Positions',
    'D4': 'Visiting/Adjunct Appointments',
    'E': 'Employment Status',
    'F1': 'Licensure',
    'F2': 'Board Certification',
    'G': 'Institutional Affiliation',
    'H': 'Honors & Awards',
    'I': 'Professional Organizations',
    'J': 'Percent Effort',
    'K1': 'Didactic Teaching',
    'K2': 'Clinical Teaching',
    'K3': 'Administrative Teaching',
    'K4': 'Continuing Education',
    'K5': 'Educational Outreach',
    'K6': 'Curriculum Development',
    'K7': 'Assessment & Examinations',
    'K8': 'Simulation Education',
    'K9': 'Educational Materials',
    'L1': 'Clinical Practice',
    'L2': 'Clinical Innovations',
    'L3': 'Clinical Leadership',
    'L4': 'Quality Improvement',
    'M': 'Research/Grants',
    'M1': 'Research Activities',
    'M2': 'Research Support',
    'M3': 'Patents & Inventions',
    'M4': 'Clinical Trials',
    'N1': 'Mentoring Programs',
    'N2': 'Training Grants',
    'N3': 'Current Mentees',
    'N4': 'Past Mentees',
    'N5': 'Dissertation Committees',
    'N6': 'Career Advising',
    'O': 'Institutional Leadership',
    'P': 'Administrative Activities',
    'Q1': 'Leadership in Organizations',
    'Q2': 'Boards & Committees',
    'Q3': 'Grant Reviewing',
    'Q4': 'Editorial Activities',
    'Q5': 'Ad Hoc Reviewing',
    'R': 'Invitations to Speak',
    'S': 'Bibliography',
    'S1': 'Peer-Reviewed Articles',
    'S2': 'Reviews & Editorials',
    'S3': 'Letters to Editor',
    'S4': 'Book Chapters',
    'S5': 'Books',
    'S6': 'Case Reports',
    'S7': 'In Press/Submitted',
    'S8': 'Abstracts',
    'S9': 'Non-Peer-Reviewed',
    'S10': 'Conference Proceedings',
    'S11': 'Online Publications',
    'S12': 'Newsletters',
    'S13': 'Monographs',
    'S14': 'Other Scholarly Works',
    'S15': 'Patents & Copyrights',
    'T': 'Supplemental',
    'T1': 'Media Appearances',
    'T2': 'Public Lectures',
    'T3': 'Community Service',
    'T4': 'Languages',
    'T5': 'Professional Development',
    'T6': 'Advisory Boards',
    'T7': 'Other Activities',
}


def _get_populated_wcm_sections(run_id: str) -> list[dict]:
    """Return list of WCM sections that have data, based on stage 4 output files."""
    outputs_dir = Path(__file__).parent.parent.parent.parent / "outputs" / run_id
    stage_4_dir = outputs_dir / "stage_4_wcm_templates"

    if not stage_4_dir.exists():
        return []

    populated = []
    metadata_files = list(stage_4_dir.glob("*_template_metadata.json"))

    for metadata_file in metadata_files:
        try:
            with open(metadata_file, 'r') as f:
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
                if section_id in ALL_WCM_SECTIONS:
                    # Check it's not already covered by entity_type entries
                    already_listed = any(
                        p.get("section_id") == section_id for p in populated
                    )
                    if not already_listed:
                        populated.append({
                            "section_id": section_id,
                            "section_name": ALL_WCM_SECTIONS[section_id],
                        })

    return populated


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
    run = _check_run_access(run_id, current_user, db)

    # Get existing feedback for this user on this run
    feedback = db.query(Feedback).filter(
        Feedback.run_id == run_id,
        Feedback.user_id == current_user.id,
    ).first()

    feedback_data = None
    if feedback:
        feedback_data = {
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
            "submitted_at": feedback.submitted_at.isoformat() if feedback.submitted_at else None,
        }

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
    _check_run_access(run_id, current_user, db)

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

    return FeedbackResponse(
        id=feedback.id,
        run_id=feedback.run_id,
        user_id=feedback.user_id,
        reviewer_role=feedback.reviewer_role,
        overall_usefulness=feedback.overall_usefulness,
        likelihood_to_recommend=feedback.likelihood_to_recommend,
        submitted_at=feedback.submitted_at,
    )


@router.get("/runs/feedback-status", response_model=list[RunFeedbackStatus])
async def get_feedback_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return feedback status for all of the current user's completed runs.

    For each completed run owned by the user, indicates whether feedback
    has been submitted.
    """
    # Get all completed runs for this user
    completed_runs = (
        db.query(Run.id)
        .filter(Run.user_id == current_user.id, Run.status == "complete")
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
