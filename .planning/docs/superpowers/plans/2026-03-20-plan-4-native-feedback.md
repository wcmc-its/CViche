# Plan 4: Native Feedback System

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the external Qualtrics feedback survey with a native in-app feedback system. Collect structured feedback on every completed run through 12 questions derived from the existing Qualtrics form (SV_9KO4A1TkMXGHdqK), enhanced with run-aware context (conditional questions, pre-populated section lists). Store feedback in the database joined directly to run data for research export.

**Architecture:** Feedback model with unique constraint on `(run_id, user_id)` to support inter-rater reliability. Two API endpoints (GET/POST) on `/api/run/{run_id}/feedback`. Three frontend components: `FeedbackForm.tsx` (the 12-question form), `CompletionInterstitial.tsx` (post-run overlay), and `FeedbackBanner.tsx` (persistent nudge). Updates to `RunHistory.tsx` for per-run badges and `PipelineViewer.tsx` for a Feedback tab.

**Tech Stack:** SQLAlchemy + Alembic (backend model/migration), FastAPI (endpoints), React + Tailwind (frontend components)

**Spec:** `docs/superpowers/specs/2026-03-20-production-readiness-design.md` -- sections 2 (Feedback table) and 5 (Feedback Collection).

**Depends on:** Plan 1 (Auth Foundation) -- requires User model, `get_current_user` dependency, auth context in frontend.

---

### Task 1: Feedback Model + Alembic Migration

**Files:**
- Modify: `web_interface/backend/app/models.py`

- [ ] **Step 1: Add Feedback model to models.py**

Add to `web_interface/backend/app/models.py` after the existing `LLMUsage` class:

```python
from sqlalchemy import UniqueConstraint, Index


class Feedback(Base):
    """User feedback on a pipeline run.

    Multiple feedback records per run are allowed -- the CV subject and the admin
    who ran it may both provide feedback (inter-rater reliability data).
    Unique constraint on (run_id, user_id) -- each user can give feedback once per run.
    """
    __tablename__ = "feedback"
    __table_args__ = (
        UniqueConstraint("run_id", "user_id", name="uq_feedback_run_user"),
        Index("ix_feedback_run_id", "run_id"),
        Index("ix_feedback_user_id", "user_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(10), ForeignKey("runs.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)

    # Q1: Reviewer role
    reviewer_role = Column(String(50), nullable=False)  # cv_subject, departmental_staff, faculty_affairs, or free text

    # Q2-Q3: Overall ratings (1-10, nullable for N/A)
    overall_accuracy = Column(Integer, nullable=True)     # 1-10 or null
    overall_completeness = Column(Integer, nullable=True)  # 1-10 or null

    # Q4: Overall usefulness (required, 1-5)
    overall_usefulness = Column(Integer, nullable=False)  # 1-5

    # Q5-Q6: Effort estimates (required)
    manual_conversion_effort = Column(String(50), nullable=False)  # time range string
    correction_effort = Column(String(50), nullable=False)          # time range string

    # Q7-Q8: Conditional quality ratings (shown only if relevant stage ran)
    enrichment_quality = Column(Integer, nullable=True)   # 1-5 or null (stage 5)
    summary_generated = Column(Integer, nullable=True)    # Boolean as int, auto-detected from run
    summary_quality = Column(Integer, nullable=True)      # 1-5 or null (stage 4.5)

    # Q9: Issue severity matrix (6 issue types x 4 severity levels)
    issue_missing_content = Column(String(20), nullable=True)  # not_noticed, minor, moderate, major
    issue_split_merged = Column(String(20), nullable=True)
    issue_wrong_section = Column(String(20), nullable=True)
    issue_inaccurate = Column(String(20), nullable=True)
    issue_ai_enrichment = Column(String(20), nullable=True)
    issue_formatting = Column(String(20), nullable=True)

    # Q10: Issue locations (JSON array of section names)
    issue_locations = Column(Text, nullable=True)

    # Q11: Free text
    biggest_issue = Column(Text, nullable=True)

    # Q12: Likelihood to recommend (required, 1-5)
    likelihood_to_recommend = Column(Integer, nullable=False)  # 1-5

    submitted_at = Column(DateTime, server_default=func.now())
```

- [ ] **Step 2: Verify model loads**

Run: `cd web_interface/backend && python3 -c "from app.models import Feedback; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Generate Alembic migration**

Run: `cd web_interface/backend && alembic revision --autogenerate -m "add feedback table with run_id user_id unique constraint"`

- [ ] **Step 4: Review and apply migration**

Review the generated migration file to confirm:
- The `feedback` table is created with all columns
- The unique constraint `uq_feedback_run_user` on `(run_id, user_id)` is present
- Indexes `ix_feedback_run_id` and `ix_feedback_user_id` are present

Run: `cd web_interface/backend && alembic upgrade head`

- [ ] **Step 5: Verify table exists**

Run: `mysql -u root cviche -e "DESCRIBE feedback;"`
Expected: all columns listed with correct types. Then:
Run: `mysql -u root cviche -e "SHOW INDEX FROM feedback;"`
Expected: unique index on `(run_id, user_id)` plus individual indexes on `run_id` and `user_id`.

- [ ] **Step 6: Commit**

```bash
git add web_interface/backend/app/models.py web_interface/backend/alembic/versions/
git commit -m "feat: add Feedback model with (run_id, user_id) unique constraint"
```

---

### Task 2: Feedback Pydantic Schemas

**Files:**
- Modify: `web_interface/backend/app/schemas.py`

- [ ] **Step 1: Add feedback request/response schemas**

Add to the end of `web_interface/backend/app/schemas.py`:

```python
# ============================================================
# Feedback Schemas
# ============================================================

VALID_SEVERITY_LEVELS = {"not_noticed", "minor", "moderate", "major"}
VALID_EFFORT_CHOICES = {
    "0 minutes", "< 5 minutes", "5-15 minutes", "15-30 minutes",
    "30-60 minutes", "1-2 hours", "2-4 hours", "4-8 hours", "8+ hours", "not_sure"
}
VALID_REVIEWER_ROLES = {"cv_subject", "departmental_staff", "faculty_affairs"}


class FeedbackSubmit(BaseModel):
    """Request body for submitting feedback on a run."""

    # Required fields (core metrics for research paper)
    reviewer_role: str
    overall_usefulness: int  # 1-5
    manual_conversion_effort: str
    correction_effort: str
    likelihood_to_recommend: int  # 1-5

    # Optional fields (nullable / contextual)
    overall_accuracy: Optional[int] = None  # 1-10 or null (N/A)
    overall_completeness: Optional[int] = None  # 1-10 or null (N/A)
    enrichment_quality: Optional[int] = None  # 1-5 or null
    summary_generated: Optional[bool] = None
    summary_quality: Optional[int] = None  # 1-5 or null
    issue_missing_content: Optional[str] = None
    issue_split_merged: Optional[str] = None
    issue_wrong_section: Optional[str] = None
    issue_inaccurate: Optional[str] = None
    issue_ai_enrichment: Optional[str] = None
    issue_formatting: Optional[str] = None
    issue_locations: Optional[List[str]] = None  # List of section names
    biggest_issue: Optional[str] = None

    class Config:
        from_attributes = True


class FeedbackResponse(BaseModel):
    """Response body for feedback data."""
    id: int
    run_id: str
    user_id: int
    reviewer_role: str
    overall_accuracy: Optional[int] = None
    overall_completeness: Optional[int] = None
    overall_usefulness: int
    manual_conversion_effort: str
    correction_effort: str
    enrichment_quality: Optional[int] = None
    summary_generated: Optional[bool] = None
    summary_quality: Optional[int] = None
    issue_missing_content: Optional[str] = None
    issue_split_merged: Optional[str] = None
    issue_wrong_section: Optional[str] = None
    issue_inaccurate: Optional[str] = None
    issue_ai_enrichment: Optional[str] = None
    issue_formatting: Optional[str] = None
    issue_locations: Optional[List[str]] = None
    biggest_issue: Optional[str] = None
    likelihood_to_recommend: int
    submitted_at: datetime

    class Config:
        from_attributes = True


class RunFeedbackStatus(BaseModel):
    """Feedback status for a run (used in run history badges)."""
    run_id: str
    has_feedback: bool
    feedback_count: int

    class Config:
        from_attributes = True
```

- [ ] **Step 2: Verify schemas load**

Run: `cd web_interface/backend && python3 -c "from app.schemas import FeedbackSubmit, FeedbackResponse, RunFeedbackStatus; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Commit**

```bash
git add web_interface/backend/app/schemas.py
git commit -m "feat: add Pydantic schemas for feedback submission and response"
```

---

### Task 3: Feedback API Endpoints

**Files:**
- Create: `web_interface/backend/app/api/feedback.py`
- Modify: `web_interface/backend/app/main.py`

- [ ] **Step 1: Create feedback routes**

Create `web_interface/backend/app/api/feedback.py`:

```python
"""Feedback API endpoints."""
import json
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Run, Step, Feedback, User
from app.schemas import (
    FeedbackSubmit,
    FeedbackResponse,
    VALID_SEVERITY_LEVELS,
    VALID_EFFORT_CHOICES,
    VALID_REVIEWER_ROLES,
)
from app.auth import get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()


def _validate_feedback(body: FeedbackSubmit) -> None:
    """Validate feedback field values. Raises HTTPException on invalid input."""
    errors = []

    # Validate required scale fields
    if not (1 <= body.overall_usefulness <= 5):
        errors.append("overall_usefulness must be between 1 and 5")
    if not (1 <= body.likelihood_to_recommend <= 5):
        errors.append("likelihood_to_recommend must be between 1 and 5")

    # Validate optional scale fields when provided
    if body.overall_accuracy is not None and not (1 <= body.overall_accuracy <= 10):
        errors.append("overall_accuracy must be between 1 and 10, or null for N/A")
    if body.overall_completeness is not None and not (1 <= body.overall_completeness <= 10):
        errors.append("overall_completeness must be between 1 and 10, or null for N/A")
    if body.enrichment_quality is not None and not (1 <= body.enrichment_quality <= 5):
        errors.append("enrichment_quality must be between 1 and 5, or null for N/A")
    if body.summary_quality is not None and not (1 <= body.summary_quality <= 5):
        errors.append("summary_quality must be between 1 and 5, or null for N/A")

    # Validate effort choices
    if body.manual_conversion_effort not in VALID_EFFORT_CHOICES:
        errors.append(
            f"manual_conversion_effort must be one of: {', '.join(sorted(VALID_EFFORT_CHOICES))}"
        )
    if body.correction_effort not in VALID_EFFORT_CHOICES:
        errors.append(
            f"correction_effort must be one of: {', '.join(sorted(VALID_EFFORT_CHOICES))}"
        )

    # Validate severity levels when provided
    severity_fields = [
        ("issue_missing_content", body.issue_missing_content),
        ("issue_split_merged", body.issue_split_merged),
        ("issue_wrong_section", body.issue_wrong_section),
        ("issue_inaccurate", body.issue_inaccurate),
        ("issue_ai_enrichment", body.issue_ai_enrichment),
        ("issue_formatting", body.issue_formatting),
    ]
    for field_name, value in severity_fields:
        if value is not None and value not in VALID_SEVERITY_LEVELS:
            errors.append(
                f"{field_name} must be one of: {', '.join(sorted(VALID_SEVERITY_LEVELS))}"
            )

    # Validate reviewer_role: must be a known role or treated as free-text "other"
    # (we accept any non-empty string; known roles are just conventions)
    if not body.reviewer_role or not body.reviewer_role.strip():
        errors.append("reviewer_role is required")

    if errors:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_error",
                "message": "Feedback validation failed.",
                "details": errors,
            },
        )


def _get_run_context(run_id: str, db: Session) -> dict:
    """Get run context for the feedback form: which stages ran, populated sections."""
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        return {}

    steps = db.query(Step).filter(Step.run_id == run_id).all()
    completed_stages = {s.stage_id for s in steps if s.status == "complete" and s.stage_id}

    # Determine which conditional questions to show
    stage_5_ran = "5" in completed_stages
    stage_4_5_ran = "4.5" in completed_stages

    # Get populated sections from stage 6 output (if available)
    populated_sections: list[str] = []
    from pathlib import Path
    import json as json_mod

    outputs_dir = Path(__file__).parent.parent.parent.parent / "outputs" / run_id
    stage_6_dir = outputs_dir / "stage_6_document"

    if stage_6_dir.exists():
        # Look for the template metadata or section mapping
        for meta_file in stage_6_dir.glob("*_metadata.json"):
            try:
                with open(meta_file, "r") as f:
                    meta = json_mod.load(f)
                sections = meta.get("populated_sections", [])
                if sections:
                    populated_sections = sections
            except Exception:
                pass

    # Fallback: check stage 4 template metadata for populated sections
    if not populated_sections:
        stage_4_dir = outputs_dir / "stage_4_wcm_templates"
        if stage_4_dir.exists():
            for meta_file in stage_4_dir.glob("*_template_metadata.json"):
                try:
                    with open(meta_file, "r") as f:
                        meta = json_mod.load(f)
                    records = meta.get("records_processed", {})
                    # Convert entity types to section names
                    entity_to_sections = {
                        "education": ["B1 - Academic Degrees", "B2 - Other Educational Experiences", "B3 - Residency & Fellowship"],
                        "positions": ["D1 - Academic Appointments", "D2 - Hospital Appointments", "D3 - Other Professional Positions"],
                        "publications": ["S1 - Peer-Reviewed Articles", "S2 - Reviews & Editorials", "S4 - Book Chapters"],
                        "grants": ["M - Research/Grants"],
                        "honors": ["H - Honors & Awards"],
                        "memberships": ["I - Professional Organizations"],
                        "service": ["O - Institutional Leadership", "P - Administrative Activities"],
                        "teaching": ["K1 - Didactic Teaching", "K2 - Clinical Teaching"],
                        "mentoring": ["N3 - Current Mentees", "N4 - Past Mentees"],
                        "presentations": ["R - Invitations to Speak"],
                        "licensure": ["F1 - Licensure"],
                        "certifications": ["F2 - Board Certification"],
                    }
                    for entity_type, count in records.items():
                        if count > 0 and entity_type in entity_to_sections:
                            populated_sections.extend(entity_to_sections[entity_type])
                except Exception:
                    pass

    return {
        "run_id": run_id,
        "filename": run.filename,
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "completed_at": run.completed_at.isoformat() if run.completed_at else None,
        "total_cost": run.total_cost or 0.0,
        "total_duration_seconds": (
            int((run.completed_at - run.started_at).total_seconds())
            if run.completed_at and run.started_at
            else None
        ),
        "completed_stages": sorted(completed_stages),
        "stage_5_ran": stage_5_ran,
        "stage_4_5_ran": stage_4_5_ran,
        "populated_sections": sorted(set(populated_sections)),
    }


@router.get("/run/{run_id}/feedback")
async def get_feedback(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get feedback for a run.

    Returns the current user's feedback if it exists, plus run context
    for rendering the form (which stages ran, populated sections).
    """
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Run not found."},
        )

    # Ownership check: user must own the run or be admin
    if run.user_id and run.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "Access denied."},
        )

    # Get existing feedback from this user for this run
    feedback = (
        db.query(Feedback)
        .filter(Feedback.run_id == run_id, Feedback.user_id == current_user.id)
        .first()
    )

    # Get run context for form rendering
    run_context = _get_run_context(run_id, db)

    feedback_data = None
    if feedback:
        issue_locations = None
        if feedback.issue_locations:
            try:
                issue_locations = json.loads(feedback.issue_locations)
            except (json.JSONDecodeError, TypeError):
                issue_locations = None

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
            "summary_generated": bool(feedback.summary_generated) if feedback.summary_generated is not None else None,
            "summary_quality": feedback.summary_quality,
            "issue_missing_content": feedback.issue_missing_content,
            "issue_split_merged": feedback.issue_split_merged,
            "issue_wrong_section": feedback.issue_wrong_section,
            "issue_inaccurate": feedback.issue_inaccurate,
            "issue_ai_enrichment": feedback.issue_ai_enrichment,
            "issue_formatting": feedback.issue_formatting,
            "issue_locations": issue_locations,
            "biggest_issue": feedback.biggest_issue,
            "likelihood_to_recommend": feedback.likelihood_to_recommend,
            "submitted_at": feedback.submitted_at.isoformat() if feedback.submitted_at else None,
        }

    return {
        "feedback": feedback_data,
        "run_context": run_context,
    }


@router.post("/run/{run_id}/feedback", status_code=201)
async def submit_feedback(
    run_id: str,
    body: FeedbackSubmit,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Submit feedback for a run.

    Returns 409 if this user has already submitted feedback for this run.
    Multiple users can give feedback on the same run (inter-rater reliability).
    """
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "Run not found."},
        )

    # Ownership check
    if run.user_id and run.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "Access denied."},
        )

    # Check for existing feedback from this user
    existing = (
        db.query(Feedback)
        .filter(Feedback.run_id == run_id, Feedback.user_id == current_user.id)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "duplicate_feedback",
                "message": "You have already submitted feedback for this run.",
            },
        )

    # Validate field values
    _validate_feedback(body)

    # Serialize issue_locations list to JSON string
    issue_locations_json = None
    if body.issue_locations:
        issue_locations_json = json.dumps(body.issue_locations)

    feedback = Feedback(
        run_id=run_id,
        user_id=current_user.id,
        reviewer_role=body.reviewer_role.strip(),
        overall_accuracy=body.overall_accuracy,
        overall_completeness=body.overall_completeness,
        overall_usefulness=body.overall_usefulness,
        manual_conversion_effort=body.manual_conversion_effort,
        correction_effort=body.correction_effort,
        enrichment_quality=body.enrichment_quality,
        summary_generated=int(body.summary_generated) if body.summary_generated is not None else None,
        summary_quality=body.summary_quality,
        issue_missing_content=body.issue_missing_content,
        issue_split_merged=body.issue_split_merged,
        issue_wrong_section=body.issue_wrong_section,
        issue_inaccurate=body.issue_inaccurate,
        issue_ai_enrichment=body.issue_ai_enrichment,
        issue_formatting=body.issue_formatting,
        issue_locations=issue_locations_json,
        biggest_issue=body.biggest_issue.strip() if body.biggest_issue else None,
        likelihood_to_recommend=body.likelihood_to_recommend,
        submitted_at=datetime.now(),
    )

    db.add(feedback)
    db.commit()
    db.refresh(feedback)

    logger.info(
        "feedback_submitted",
        extra={
            "run_id": run_id,
            "user_id": current_user.id,
            "user_email": current_user.email,
            "fields_completed": sum(
                1
                for v in [
                    body.overall_accuracy,
                    body.overall_completeness,
                    body.enrichment_quality,
                    body.summary_quality,
                    body.issue_missing_content,
                    body.issue_split_merged,
                    body.issue_wrong_section,
                    body.issue_inaccurate,
                    body.issue_ai_enrichment,
                    body.issue_formatting,
                    body.issue_locations,
                    body.biggest_issue,
                ]
                if v is not None
            )
            + 5,  # 5 required fields always present
        },
    )

    return {
        "message": "Feedback submitted successfully.",
        "feedback_id": feedback.id,
    }


@router.get("/runs/feedback-status")
async def get_feedback_status_for_runs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get feedback status for all of the current user's completed runs.

    Returns a list of {run_id, has_feedback, feedback_count} for each completed run.
    Used by RunHistory to show amber/green badges and by FeedbackBanner to show count.
    """
    # Get all completed runs for this user
    completed_runs = (
        db.query(Run)
        .filter(Run.user_id == current_user.id, Run.status == "complete")
        .all()
    )

    results = []
    for run in completed_runs:
        # Check if this user has submitted feedback for this run
        user_feedback = (
            db.query(Feedback)
            .filter(Feedback.run_id == run.id, Feedback.user_id == current_user.id)
            .first()
        )
        # Count total feedback records for this run (from all users)
        total_feedback = (
            db.query(Feedback).filter(Feedback.run_id == run.id).count()
        )

        results.append({
            "run_id": run.id,
            "has_feedback": user_feedback is not None,
            "feedback_count": total_feedback,
        })

    return results
```

- [ ] **Step 2: Register feedback routes in main.py**

Add to `web_interface/backend/app/main.py`:

```python
from app.api import upload, runs, steps, websocket, auth_routes, feedback

# ... in the router registration section:
app.include_router(feedback.router, prefix="/api", tags=["feedback"])
```

- [ ] **Step 3: Verify endpoints load**

Run: `cd web_interface/backend && python3 -c "from app.api.feedback import router; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Test GET endpoint (no feedback yet)**

```bash
# Login first
curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "paa2013@med.cornell.edu", "display_name": "Paul Albert"}' \
  -c cookies.txt

# Get feedback for a run (replace RUN_ID with an actual run ID)
curl -s http://localhost:8000/api/run/RUN_ID/feedback -b cookies.txt | python3 -m json.tool
```
Expected: `{ "feedback": null, "run_context": { ... } }`

- [ ] **Step 5: Test POST endpoint**

```bash
curl -s -X POST http://localhost:8000/api/run/RUN_ID/feedback \
  -H "Content-Type: application/json" \
  -b cookies.txt \
  -d '{
    "reviewer_role": "faculty_affairs",
    "overall_usefulness": 4,
    "manual_conversion_effort": "2-4 hours",
    "correction_effort": "15-30 minutes",
    "likelihood_to_recommend": 4,
    "overall_accuracy": 7,
    "overall_completeness": 8
  }' | python3 -m json.tool
```
Expected: `{ "message": "Feedback submitted successfully.", "feedback_id": 1 }`

- [ ] **Step 6: Test 409 on duplicate**

Run the same POST again.
Expected: HTTP 409 with `"error": "duplicate_feedback"`

- [ ] **Step 7: Commit**

```bash
git add web_interface/backend/app/api/feedback.py web_interface/backend/app/main.py
git commit -m "feat: add GET/POST /api/run/{run_id}/feedback endpoints with validation"
```

---

### Task 4: FeedbackForm.tsx Component

**Files:**
- Create: `web_interface/frontend/src/components/FeedbackForm.tsx`

- [ ] **Step 1: Create the feedback form component**

Create `web_interface/frontend/src/components/FeedbackForm.tsx`:

```tsx
import { useState, useEffect } from 'react'
import { Loader2, CheckCircle2, AlertCircle, MessageSquare } from 'lucide-react'
import ErrorBanner from './ErrorBanner'

interface RunContext {
  run_id: string
  filename: string
  status: string
  started_at: string | null
  completed_at: string | null
  total_cost: number
  total_duration_seconds: number | null
  completed_stages: string[]
  stage_5_ran: boolean
  stage_4_5_ran: boolean
  populated_sections: string[]
}

interface ExistingFeedback {
  id: number
  reviewer_role: string
  overall_accuracy: number | null
  overall_completeness: number | null
  overall_usefulness: number
  manual_conversion_effort: string
  correction_effort: string
  enrichment_quality: number | null
  summary_generated: boolean | null
  summary_quality: number | null
  issue_missing_content: string | null
  issue_split_merged: string | null
  issue_wrong_section: string | null
  issue_inaccurate: string | null
  issue_ai_enrichment: string | null
  issue_formatting: string | null
  issue_locations: string[] | null
  biggest_issue: string | null
  likelihood_to_recommend: number
  submitted_at: string
}

interface FeedbackFormProps {
  runId: string
  onSubmitSuccess?: () => void
  compact?: boolean
}

const REVIEWER_ROLES = [
  { value: 'cv_subject', label: 'CV Subject (this is my CV)' },
  { value: 'departmental_staff', label: 'Departmental Staff' },
  { value: 'faculty_affairs', label: 'Faculty Affairs / Library Staff' },
]

const EFFORT_CHOICES = [
  { value: '0 minutes', label: '0 minutes (no effort needed)' },
  { value: '< 5 minutes', label: '< 5 minutes' },
  { value: '5-15 minutes', label: '5-15 minutes' },
  { value: '15-30 minutes', label: '15-30 minutes' },
  { value: '30-60 minutes', label: '30-60 minutes' },
  { value: '1-2 hours', label: '1-2 hours' },
  { value: '2-4 hours', label: '2-4 hours' },
  { value: '4-8 hours', label: '4-8 hours' },
  { value: '8+ hours', label: '8+ hours' },
  { value: 'not_sure', label: 'Not sure' },
]

const SEVERITY_LEVELS = [
  { value: 'not_noticed', label: 'Not noticed' },
  { value: 'minor', label: 'Minor' },
  { value: 'moderate', label: 'Moderate' },
  { value: 'major', label: 'Major' },
]

const ISSUE_TYPES = [
  { key: 'issue_missing_content', label: 'Missing content', description: 'Content from the original CV was not included in the output' },
  { key: 'issue_split_merged', label: 'Entries split or merged', description: 'Entries were incorrectly split into multiple items or merged together' },
  { key: 'issue_wrong_section', label: 'Wrong section placement', description: 'Content placed in the wrong WCM section' },
  { key: 'issue_inaccurate', label: 'Inaccurate information', description: 'Dates, names, titles, or other facts were changed or incorrect' },
  { key: 'issue_ai_enrichment', label: 'AI enrichment errors', description: 'PubMed or other enrichment data was incorrect or mismatched' },
  { key: 'issue_formatting', label: 'Formatting issues', description: 'Layout, spacing, or style problems in the output document' },
]

function SliderWithLabels({
  label,
  description,
  value,
  onChange,
  min,
  max,
  required,
  allowNA,
}: {
  label: string
  description?: string
  value: number | null
  onChange: (val: number | null) => void
  min: number
  max: number
  required?: boolean
  allowNA?: boolean
}) {
  const isNA = value === null && allowNA

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <label className="block text-sm font-semibold text-gray-900">
          {label}
          {required && <span className="text-red-500 ml-1">*</span>}
        </label>
        {allowNA && (
          <label className="flex items-center gap-1.5 text-sm text-gray-600 cursor-pointer">
            <input
              type="checkbox"
              checked={isNA}
              onChange={(e) => onChange(e.target.checked ? null : Math.ceil((min + max) / 2))}
              className="rounded border-gray-300 text-primary-600 focus:ring-primary-500"
            />
            N/A
          </label>
        )}
      </div>
      {description && <p className="text-xs text-gray-500">{description}</p>}
      {!isNA && (
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-500 w-4 text-center">{min}</span>
          <input
            type="range"
            min={min}
            max={max}
            value={value ?? Math.ceil((min + max) / 2)}
            onChange={(e) => onChange(parseInt(e.target.value))}
            className="flex-1 h-2 bg-gray-200 rounded-lg appearance-none cursor-pointer accent-primary-600"
          />
          <span className="text-xs text-gray-500 w-4 text-center">{max}</span>
          <span className="text-sm font-bold text-primary-700 bg-primary-50 px-2 py-0.5 rounded min-w-[2rem] text-center">
            {value ?? Math.ceil((min + max) / 2)}
          </span>
        </div>
      )}
    </div>
  )
}

function SelectField({
  label,
  description,
  value,
  onChange,
  options,
  required,
}: {
  label: string
  description?: string
  value: string
  onChange: (val: string) => void
  options: { value: string; label: string }[]
  required?: boolean
}) {
  return (
    <div className="space-y-1">
      <label className="block text-sm font-semibold text-gray-900">
        {label}
        {required && <span className="text-red-500 ml-1">*</span>}
      </label>
      {description && <p className="text-xs text-gray-500">{description}</p>}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:outline-none text-sm"
      >
        <option value="">Select...</option>
        {options.map((opt) => (
          <option key={opt.value} value={opt.value}>
            {opt.label}
          </option>
        ))}
      </select>
    </div>
  )
}

export default function FeedbackForm({ runId, onSubmitSuccess, compact }: FeedbackFormProps) {
  const [loading, setLoading] = useState(true)
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [runContext, setRunContext] = useState<RunContext | null>(null)
  const [existingFeedback, setExistingFeedback] = useState<ExistingFeedback | null>(null)

  // Form state
  const [reviewerRole, setReviewerRole] = useState('')
  const [reviewerRoleOther, setReviewerRoleOther] = useState('')
  const [overallAccuracy, setOverallAccuracy] = useState<number | null>(5)
  const [overallCompleteness, setOverallCompleteness] = useState<number | null>(5)
  const [overallUsefulness, setOverallUsefulness] = useState<number>(3)
  const [manualConversionEffort, setManualConversionEffort] = useState('')
  const [correctionEffort, setCorrectionEffort] = useState('')
  const [enrichmentQuality, setEnrichmentQuality] = useState<number | null>(3)
  const [summaryQuality, setSummaryQuality] = useState<number | null>(3)
  const [issueSeverity, setIssueSeverity] = useState<Record<string, string | null>>({
    issue_missing_content: null,
    issue_split_merged: null,
    issue_wrong_section: null,
    issue_inaccurate: null,
    issue_ai_enrichment: null,
    issue_formatting: null,
  })
  const [issueLocations, setIssueLocations] = useState<string[]>([])
  const [biggestIssue, setBiggestIssue] = useState('')
  const [likelihoodToRecommend, setLikelihoodToRecommend] = useState<number>(3)

  // Fetch existing feedback and run context
  useEffect(() => {
    const fetchFeedback = async () => {
      try {
        const res = await fetch(`/api/run/${runId}/feedback`)
        if (!res.ok) {
          if (res.status === 404) {
            setError('Run not found.')
            return
          }
          throw new Error('Failed to load feedback data')
        }

        const data = await res.json()
        setRunContext(data.run_context)

        if (data.feedback) {
          setExistingFeedback(data.feedback)
          setSubmitted(true)
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load feedback form')
      } finally {
        setLoading(false)
      }
    }

    fetchFeedback()
  }, [runId])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError(null)

    // Client-side validation for required fields
    const finalRole = reviewerRole === 'other' ? reviewerRoleOther.trim() : reviewerRole
    if (!finalRole) {
      setError('Please select your role.')
      return
    }
    if (!manualConversionEffort) {
      setError('Please estimate the manual conversion effort.')
      return
    }
    if (!correctionEffort) {
      setError('Please estimate the correction effort.')
      return
    }

    setSubmitting(true)

    const payload: Record<string, unknown> = {
      reviewer_role: finalRole,
      overall_accuracy: overallAccuracy,
      overall_completeness: overallCompleteness,
      overall_usefulness: overallUsefulness,
      manual_conversion_effort: manualConversionEffort,
      correction_effort: correctionEffort,
      likelihood_to_recommend: likelihoodToRecommend,
      biggest_issue: biggestIssue.trim() || null,
      issue_locations: issueLocations.length > 0 ? issueLocations : null,
    }

    // Add conditional fields only if the relevant stage ran
    if (runContext?.stage_5_ran) {
      payload.enrichment_quality = enrichmentQuality
    }
    if (runContext?.stage_4_5_ran) {
      payload.summary_generated = true
      payload.summary_quality = summaryQuality
    }

    // Add issue severity fields (only non-null values)
    for (const [key, value] of Object.entries(issueSeverity)) {
      if (value !== null) {
        payload[key] = value
      }
    }

    try {
      const res = await fetch(`/api/run/${runId}/feedback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })

      if (res.status === 409) {
        setError('You have already submitted feedback for this run.')
        setSubmitted(true)
        return
      }

      if (!res.ok) {
        const errData = await res.json()
        throw new Error(errData.detail?.message || 'Failed to submit feedback')
      }

      setSubmitted(true)
      onSubmitSuccess?.()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit feedback')
    } finally {
      setSubmitting(false)
    }
  }

  const toggleIssueLocation = (section: string) => {
    setIssueLocations((prev) =>
      prev.includes(section) ? prev.filter((s) => s !== section) : [...prev, section]
    )
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-12">
        <Loader2 className="h-6 w-6 animate-spin text-gray-400" />
        <span className="ml-2 text-gray-500">Loading feedback form...</span>
      </div>
    )
  }

  // Already submitted view
  if (submitted) {
    return (
      <div className="bg-green-50 border border-green-200 rounded-lg p-6 text-center">
        <CheckCircle2 className="h-10 w-10 text-green-600 mx-auto mb-3" />
        <h3 className="text-lg font-semibold text-green-900 mb-1">Feedback Submitted</h3>
        <p className="text-sm text-green-700">
          Thank you for your feedback on this run.
          {existingFeedback && (
            <span className="block mt-1 text-xs text-green-600">
              Submitted on {new Date(existingFeedback.submitted_at).toLocaleString()}
            </span>
          )}
        </p>
      </div>
    )
  }

  const formatDuration = (seconds: number | null) => {
    if (seconds === null) return '--'
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    if (mins > 0) return `${mins}m ${secs}s`
    return `${secs}s`
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-6">
      {/* Run Context Header */}
      {runContext && !compact && (
        <div className="bg-gray-50 border border-gray-200 rounded-lg p-4">
          <h3 className="text-sm font-semibold text-gray-700 mb-2">Run Details</h3>
          <dl className="grid grid-cols-2 gap-2 text-sm">
            <div>
              <dt className="text-gray-500">File</dt>
              <dd className="font-medium text-gray-900 truncate">{runContext.filename}</dd>
            </div>
            <div>
              <dt className="text-gray-500">Date</dt>
              <dd className="font-medium text-gray-900">
                {runContext.started_at
                  ? new Date(runContext.started_at).toLocaleDateString()
                  : '--'}
              </dd>
            </div>
            <div>
              <dt className="text-gray-500">Duration</dt>
              <dd className="font-medium text-gray-900">
                {formatDuration(runContext.total_duration_seconds)}
              </dd>
            </div>
            <div>
              <dt className="text-gray-500">Cost</dt>
              <dd className="font-medium text-gray-900">${runContext.total_cost.toFixed(3)}</dd>
            </div>
            <div className="col-span-2">
              <dt className="text-gray-500">Stages completed</dt>
              <dd className="font-medium text-gray-900">
                {runContext.completed_stages.join(', ') || 'None'}
              </dd>
            </div>
          </dl>
        </div>
      )}

      <p className="text-xs text-gray-500 italic">
        Estimated completion time: ~3 minutes. Fields marked with <span className="text-red-500">*</span> are required.
      </p>

      {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

      {/* Q1: Reviewer Role */}
      <div className="space-y-1">
        <label className="block text-sm font-semibold text-gray-900">
          1. What best describes your role? <span className="text-red-500">*</span>
        </label>
        <div className="space-y-2">
          {REVIEWER_ROLES.map((role) => (
            <label key={role.value} className="flex items-center gap-2 text-sm cursor-pointer">
              <input
                type="radio"
                name="reviewer_role"
                value={role.value}
                checked={reviewerRole === role.value}
                onChange={(e) => setReviewerRole(e.target.value)}
                className="text-primary-600 focus:ring-primary-500"
              />
              {role.label}
            </label>
          ))}
          <label className="flex items-center gap-2 text-sm cursor-pointer">
            <input
              type="radio"
              name="reviewer_role"
              value="other"
              checked={reviewerRole === 'other'}
              onChange={(e) => setReviewerRole(e.target.value)}
              className="text-primary-600 focus:ring-primary-500"
            />
            Other:
            {reviewerRole === 'other' && (
              <input
                type="text"
                value={reviewerRoleOther}
                onChange={(e) => setReviewerRoleOther(e.target.value)}
                placeholder="Specify your role"
                className="ml-1 px-2 py-1 border border-gray-300 rounded text-sm focus:ring-2 focus:ring-primary-500 focus:outline-none"
              />
            )}
          </label>
        </div>
      </div>

      {/* Q2: Overall Accuracy (1-10 slider + N/A) */}
      <SliderWithLabels
        label="2. How accurate was the converted CV?"
        description="Were names, dates, titles, and content faithfully preserved from the original?"
        value={overallAccuracy}
        onChange={setOverallAccuracy}
        min={1}
        max={10}
        allowNA
      />

      {/* Q3: Overall Completeness (1-10 slider + N/A) */}
      <SliderWithLabels
        label="3. How complete was the converted CV?"
        description="Was all content from the original captured in the output?"
        value={overallCompleteness}
        onChange={setOverallCompleteness}
        min={1}
        max={10}
        allowNA
      />

      {/* Q4: Overall Usefulness (1-5, required) */}
      <SliderWithLabels
        label="4. How usable is this as a starting point?"
        description="1 = would need to start over, 5 = ready to use with minimal edits"
        value={overallUsefulness}
        onChange={(v) => setOverallUsefulness(v ?? 3)}
        min={1}
        max={5}
        required
      />

      {/* Q5: Manual Conversion Effort */}
      <SelectField
        label="5. How long would manual reformatting take?"
        description="If you had to convert this CV to WCM format manually (without CViche), how long would it take?"
        value={manualConversionEffort}
        onChange={setManualConversionEffort}
        options={EFFORT_CHOICES}
        required
      />

      {/* Q6: Correction Effort */}
      <SelectField
        label="6. How long to correct the CViche output?"
        description="How much time would it take to fix issues in the CViche output to make it ready for use?"
        value={correctionEffort}
        onChange={setCorrectionEffort}
        options={EFFORT_CHOICES}
        required
      />

      {/* Q7: Publication Enrichment Quality (conditional) */}
      {runContext?.stage_5_ran && (
        <SliderWithLabels
          label="7. Publication enrichment quality"
          description="How accurate was the PubMed-enriched publication data (author lists, journals, MeSH terms)?"
          value={enrichmentQuality}
          onChange={setEnrichmentQuality}
          min={1}
          max={5}
          allowNA
        />
      )}

      {/* Q8: Research Summary Quality (conditional) */}
      {runContext?.stage_4_5_ran && (
        <SliderWithLabels
          label="8. Research summary quality"
          description="How well does the auto-generated research summary (M1 section) capture the research profile?"
          value={summaryQuality}
          onChange={setSummaryQuality}
          min={1}
          max={5}
          allowNA
        />
      )}

      {/* Q9: Issue Severity Matrix */}
      <div className="space-y-3">
        <div>
          <label className="block text-sm font-semibold text-gray-900">
            9. Did you notice any of these issues?
          </label>
          <p className="text-xs text-gray-500">
            Rate the severity of each issue type, or leave blank if not applicable.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm border-collapse">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="text-left py-2 pr-4 font-medium text-gray-700 min-w-[180px]">
                  Issue Type
                </th>
                {SEVERITY_LEVELS.map((level) => (
                  <th key={level.value} className="text-center py-2 px-2 font-medium text-gray-700 min-w-[80px]">
                    {level.label}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {ISSUE_TYPES.map((issue) => (
                <tr key={issue.key} className="border-b border-gray-100">
                  <td className="py-2.5 pr-4">
                    <div className="font-medium text-gray-900 text-sm">{issue.label}</div>
                    <div className="text-xs text-gray-500">{issue.description}</div>
                  </td>
                  {SEVERITY_LEVELS.map((level) => (
                    <td key={level.value} className="text-center py-2.5 px-2">
                      <input
                        type="radio"
                        name={issue.key}
                        value={level.value}
                        checked={issueSeverity[issue.key] === level.value}
                        onChange={() =>
                          setIssueSeverity((prev) => ({
                            ...prev,
                            [issue.key]: level.value,
                          }))
                        }
                        className="text-primary-600 focus:ring-primary-500"
                      />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {/* Q10: Issue Locations (conditional checkboxes) */}
      {runContext && runContext.populated_sections.length > 0 && (
        <div className="space-y-2">
          <label className="block text-sm font-semibold text-gray-900">
            10. In which sections did you notice issues?
          </label>
          <p className="text-xs text-gray-500">
            Select all sections where you found problems. Only sections populated in this run are shown.
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-1.5 max-h-48 overflow-y-auto border border-gray-200 rounded-lg p-3">
            {runContext.populated_sections.map((section) => (
              <label
                key={section}
                className="flex items-center gap-2 text-sm cursor-pointer hover:bg-gray-50 px-1.5 py-1 rounded"
              >
                <input
                  type="checkbox"
                  checked={issueLocations.includes(section)}
                  onChange={() => toggleIssueLocation(section)}
                  className="rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                />
                <span className="text-gray-700">{section}</span>
              </label>
            ))}
          </div>
        </div>
      )}

      {/* Q11: Biggest Issue (free text) */}
      <div className="space-y-1">
        <label className="block text-sm font-semibold text-gray-900">
          11. What is the single biggest issue affecting your confidence in this output?
        </label>
        <textarea
          value={biggestIssue}
          onChange={(e) => setBiggestIssue(e.target.value)}
          placeholder="Describe the most important issue, if any..."
          rows={3}
          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:outline-none text-sm resize-none"
        />
      </div>

      {/* Q12: Likelihood to Recommend (1-5, required) */}
      <SliderWithLabels
        label="12. How likely are you to recommend CViche to a colleague?"
        description="1 = would not recommend, 5 = would strongly recommend"
        value={likelihoodToRecommend}
        onChange={(v) => setLikelihoodToRecommend(v ?? 3)}
        min={1}
        max={5}
        required
      />

      {/* Submit Button */}
      <button
        type="submit"
        disabled={submitting}
        className="w-full bg-amber-500 text-white py-3 px-4 rounded-lg font-semibold hover:bg-amber-600 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors focus:ring-2 focus:ring-amber-400 focus:outline-none flex items-center justify-center gap-2"
      >
        {submitting ? (
          <>
            <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
            Submitting...
          </>
        ) : (
          <>
            <MessageSquare className="h-5 w-5" aria-hidden="true" />
            Submit Feedback
          </>
        )}
      </button>
    </form>
  )
}
```

- [ ] **Step 2: Build and verify**

Run: `cd web_interface/frontend && npm run build`
Expected: clean build, no TypeScript errors

- [ ] **Step 3: Commit**

```bash
git add web_interface/frontend/src/components/FeedbackForm.tsx
git commit -m "feat: add FeedbackForm component with 12 questions, sliders, severity matrix"
```

---

### Task 5: CompletionInterstitial.tsx Component

**Files:**
- Create: `web_interface/frontend/src/components/CompletionInterstitial.tsx`
- Modify: `web_interface/frontend/src/components/PipelineViewer.tsx`

- [ ] **Step 1: Create the completion interstitial overlay**

Create `web_interface/frontend/src/components/CompletionInterstitial.tsx`:

```tsx
import { useState } from 'react'
import { Download, MessageSquare, CheckCircle2, X } from 'lucide-react'
import FeedbackForm from './FeedbackForm'

interface CompletionInterstitialProps {
  runId: string
  filename: string
  totalCost: number
  durationSeconds: number | null
  onDismiss: () => void
  onDownload: () => void
}

export default function CompletionInterstitial({
  runId,
  filename,
  totalCost,
  durationSeconds,
  onDismiss,
  onDownload,
}: CompletionInterstitialProps) {
  const [showFeedbackForm, setShowFeedbackForm] = useState(false)
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false)

  const formatDuration = (seconds: number | null) => {
    if (seconds === null) return '--'
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    if (mins > 0) return `${mins}m ${secs}s`
    return `${secs}s`
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm p-4"
      role="dialog"
      aria-modal="true"
      aria-label="Pipeline complete"
    >
      <div className="bg-white rounded-2xl shadow-2xl max-w-lg w-full max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="bg-gradient-to-r from-green-50 to-emerald-50 border-b border-green-200 rounded-t-2xl p-6 text-center relative">
          <button
            onClick={onDismiss}
            className="absolute top-3 right-3 text-gray-400 hover:text-gray-600 p-1"
            aria-label="Dismiss"
          >
            <X className="h-5 w-5" />
          </button>
          <CheckCircle2 className="h-14 w-14 text-green-600 mx-auto mb-3" aria-hidden="true" />
          <h2 className="text-xl font-bold text-gray-900 mb-1">Pipeline Complete!</h2>
          <p className="text-gray-600">
            Your WCM-formatted CV is ready.
          </p>
          <div className="flex justify-center gap-6 mt-3 text-sm text-gray-500">
            <span>Duration: <strong>{formatDuration(durationSeconds)}</strong></span>
            <span>Cost: <strong>${totalCost.toFixed(3)}</strong></span>
          </div>
        </div>

        {/* Actions */}
        <div className="p-6 space-y-4">
          {!showFeedbackForm ? (
            <>
              {/* Download Button */}
              <button
                onClick={onDownload}
                className="w-full bg-primary-600 text-white py-3 px-4 rounded-lg font-semibold hover:bg-primary-700 transition-colors focus:ring-2 focus:ring-primary-500 focus:outline-none flex items-center justify-center gap-2"
              >
                <Download className="h-5 w-5" aria-hidden="true" />
                Download WCM CV
              </button>

              {/* Give Feedback Button */}
              {!feedbackSubmitted ? (
                <button
                  onClick={() => setShowFeedbackForm(true)}
                  className="w-full bg-amber-500 text-white py-3 px-4 rounded-lg font-semibold hover:bg-amber-600 transition-colors focus:ring-2 focus:ring-amber-400 focus:outline-none flex items-center justify-center gap-2"
                >
                  <MessageSquare className="h-5 w-5" aria-hidden="true" />
                  Give Feedback (~3 min)
                </button>
              ) : (
                <div className="flex items-center justify-center gap-2 text-green-700 bg-green-50 py-3 px-4 rounded-lg">
                  <CheckCircle2 className="h-5 w-5" />
                  <span className="font-medium">Feedback submitted -- thank you!</span>
                </div>
              )}

              {/* Dismiss Link */}
              <div className="text-center">
                <button
                  onClick={onDismiss}
                  className="text-sm text-gray-400 hover:text-gray-600 transition-colors"
                >
                  I'll do this later
                </button>
              </div>
            </>
          ) : (
            /* Inline Feedback Form */
            <div>
              <div className="flex items-center justify-between mb-4">
                <h3 className="font-semibold text-gray-900">Feedback Form</h3>
                <button
                  onClick={() => setShowFeedbackForm(false)}
                  className="text-sm text-gray-500 hover:text-gray-700"
                >
                  Back
                </button>
              </div>
              <FeedbackForm
                runId={runId}
                compact
                onSubmitSuccess={() => {
                  setFeedbackSubmitted(true)
                  setShowFeedbackForm(false)
                }}
              />
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Wire interstitial into PipelineViewer**

In `web_interface/frontend/src/components/PipelineViewer.tsx`, add:

1. Import at the top:
```tsx
import CompletionInterstitial from './CompletionInterstitial'
```

2. Add state variables inside the `PipelineViewer` component:
```tsx
  const [showInterstitial, setShowInterstitial] = useState(false)
  const [interstitialDismissed, setInterstitialDismissed] = useState(false)
```

3. Add an effect to show the interstitial when the run completes (after the existing WebSocket effect):
```tsx
  // Show completion interstitial when run transitions to 'complete'
  useEffect(() => {
    if (runStatus?.status === 'complete' && !interstitialDismissed) {
      setShowInterstitial(true)
    }
  }, [runStatus?.status, interstitialDismissed])
```

4. Add the download handler:
```tsx
  const handleDownloadFromInterstitial = () => {
    // Find the final output file from stage 6
    const stage6Step = runStatus?.steps.find(s => s.stage_id === '6')
    if (stage6Step?.output_files) {
      try {
        const files = JSON.parse(stage6Step.output_files)
        const docxFile = files.find((f: string) => f.endsWith('.docx'))
        if (docxFile) {
          window.open(`/api/run/${runId}/data/${docxFile}`, '_blank')
        }
      } catch {
        // Fallback: no file found
      }
    }
  }
```

5. Render the interstitial before the closing `</div>` of the component (after the `JsonViewerModal`):
```tsx
      {/* Completion Interstitial */}
      {showInterstitial && runStatus && (
        <CompletionInterstitial
          runId={runId}
          filename={runStatus.filename}
          totalCost={runStatus.total_cost}
          durationSeconds={runStatus.total_duration_seconds ?? null}
          onDismiss={() => {
            setShowInterstitial(false)
            setInterstitialDismissed(true)
          }}
          onDownload={handleDownloadFromInterstitial}
        />
      )}
```

- [ ] **Step 3: Build and verify**

Run: `cd web_interface/frontend && npm run build`
Expected: clean build, no errors

- [ ] **Step 4: Manual test**

1. Start a pipeline run on a small CV
2. Wait for completion
3. Verify the interstitial overlay appears with:
   - Success message
   - Download button (blue)
   - Give Feedback button (amber)
   - "I'll do this later" dismiss link
4. Click "Give Feedback" -- verify the form expands inline
5. Dismiss and verify the interstitial does not reappear on page navigation back

- [ ] **Step 5: Commit**

```bash
git add web_interface/frontend/src/components/CompletionInterstitial.tsx web_interface/frontend/src/components/PipelineViewer.tsx
git commit -m "feat: add completion interstitial overlay with download + feedback entry point"
```

---

### Task 6: FeedbackBanner.tsx Component

**Files:**
- Create: `web_interface/frontend/src/components/FeedbackBanner.tsx`
- Modify: `web_interface/frontend/src/components/UploadPage.tsx`

- [ ] **Step 1: Create the persistent feedback banner**

Create `web_interface/frontend/src/components/FeedbackBanner.tsx`:

```tsx
import { useState, useEffect } from 'react'
import { MessageSquare, ChevronRight } from 'lucide-react'

interface FeedbackBannerProps {
  onGiveFeedback: (runId: string) => void
}

interface FeedbackStatusItem {
  run_id: string
  has_feedback: boolean
  feedback_count: number
}

export default function FeedbackBanner({ onGiveFeedback }: FeedbackBannerProps) {
  const [pendingRuns, setPendingRuns] = useState<FeedbackStatusItem[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchFeedbackStatus = async () => {
      try {
        const res = await fetch('/api/runs/feedback-status')
        if (res.ok) {
          const data: FeedbackStatusItem[] = await res.json()
          // Filter to only runs where this user has NOT given feedback
          setPendingRuns(data.filter((r) => !r.has_feedback))
        }
      } catch (err) {
        console.error('Error fetching feedback status:', err)
      } finally {
        setLoading(false)
      }
    }

    fetchFeedbackStatus()
  }, [])

  if (loading || pendingRuns.length === 0) {
    return null
  }

  // Find the oldest pending run (first in the list, assuming sorted by date)
  const oldestPendingRunId = pendingRuns[0].run_id

  return (
    <div className="bg-amber-50 border border-amber-300 rounded-lg px-4 py-3 mb-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <MessageSquare className="h-5 w-5 text-amber-600 flex-shrink-0" aria-hidden="true" />
          <p className="text-sm font-medium text-amber-800">
            You have{' '}
            <strong className="text-amber-900">{pendingRuns.length}</strong>{' '}
            {pendingRuns.length === 1 ? 'run' : 'runs'} awaiting feedback
          </p>
        </div>
        <button
          onClick={() => onGiveFeedback(oldestPendingRunId)}
          className="flex items-center gap-1 text-sm font-semibold text-amber-700 hover:text-amber-900 transition-colors whitespace-nowrap"
        >
          Give Feedback
          <ChevronRight className="h-4 w-4" aria-hidden="true" />
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Add FeedbackBanner to UploadPage**

In `web_interface/frontend/src/components/UploadPage.tsx`:

1. Add the import:
```tsx
import FeedbackBanner from './FeedbackBanner'
```

2. Replace the `{/* Run History */}` section (currently around lines 230-233) with:
```tsx
        {/* Feedback Banner + Run History */}
        <div className="mt-6 bg-white/95 backdrop-blur-sm rounded-lg shadow-lg p-6">
          <FeedbackBanner
            onGiveFeedback={(runId) => navigate(`/run/${runId}?tab=feedback`)}
          />
          <RunHistory onSelectRun={(runId) => navigate(`/run/${runId}`)} />
        </div>
```

- [ ] **Step 3: Build and verify**

Run: `cd web_interface/frontend && npm run build`
Expected: clean build, no errors

- [ ] **Step 4: Manual test**

1. Navigate to the upload page
2. If there are completed runs without feedback, verify the amber banner appears:
   - Shows count of pending runs
   - "Give Feedback" link navigates to the oldest pending run's feedback tab
3. If all runs have feedback (or no completed runs), verify the banner is hidden

- [ ] **Step 5: Commit**

```bash
git add web_interface/frontend/src/components/FeedbackBanner.tsx web_interface/frontend/src/components/UploadPage.tsx
git commit -m "feat: add persistent amber feedback banner on upload page"
```

---

### Task 7: Run History Feedback Badges

**Files:**
- Modify: `web_interface/frontend/src/components/RunHistory.tsx`

- [ ] **Step 1: Add feedback status fetching and badge rendering**

Replace `web_interface/frontend/src/components/RunHistory.tsx` with:

```tsx
import { useState, useEffect } from 'react'
import {
  Clock,
  DollarSign,
  FileText,
  CheckCircle2,
  Loader2,
  XCircle,
  AlertCircle,
  MessageSquare,
} from 'lucide-react'

interface RunSummary {
  run_id: string
  filename: string
  status: string
  started_at: string
  completed_at: string | null
  total_cost: number
  total_duration_seconds: number | null
}

interface RunHistoryProps {
  onSelectRun: (runId: string) => void
}

interface FeedbackStatusItem {
  run_id: string
  has_feedback: boolean
  feedback_count: number
}

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'complete':
      return <CheckCircle2 className="w-4 h-4 text-green-600" aria-hidden="true" />
    case 'running':
      return <Loader2 className="w-4 h-4 text-blue-600 animate-spin" aria-hidden="true" />
    case 'failed':
      return <XCircle className="w-4 h-4 text-red-600" aria-hidden="true" />
    case 'cancelled':
      return <AlertCircle className="w-4 h-4 text-orange-600" aria-hidden="true" />
    default:
      return <Clock className="w-4 h-4 text-gray-400" aria-hidden="true" />
  }
}

function FeedbackBadge({ hasFeedback }: { hasFeedback: boolean }) {
  if (hasFeedback) {
    return (
      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
        <CheckCircle2 className="w-3 h-3" aria-hidden="true" />
        Feedback given
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-800">
      <MessageSquare className="w-3 h-3" aria-hidden="true" />
      Needs feedback
    </span>
  )
}

export default function RunHistory({ onSelectRun }: RunHistoryProps) {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [feedbackStatus, setFeedbackStatus] = useState<Record<string, FeedbackStatusItem>>({})
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchData = async () => {
      try {
        // Fetch runs and feedback status in parallel
        const [runsRes, feedbackRes] = await Promise.all([
          fetch('/api/runs'),
          fetch('/api/runs/feedback-status'),
        ])

        if (runsRes.ok) {
          const runsData = await runsRes.json()
          setRuns(runsData)
        }

        if (feedbackRes.ok) {
          const feedbackData: FeedbackStatusItem[] = await feedbackRes.json()
          const statusMap: Record<string, FeedbackStatusItem> = {}
          for (const item of feedbackData) {
            statusMap[item.run_id] = item
          }
          setFeedbackStatus(statusMap)
        }
      } catch (err) {
        console.error('Error fetching runs:', err)
      } finally {
        setLoading(false)
      }
    }
    fetchData()
  }, [])

  if (loading) {
    return (
      <div className="text-center py-4 text-sm text-gray-500">
        <Loader2 className="w-4 h-4 animate-spin inline mr-2" aria-hidden="true" />
        Loading history...
      </div>
    )
  }

  if (runs.length === 0) {
    return null
  }

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr)
    return (
      d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
      ' ' +
      d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    )
  }

  const formatDuration = (seconds: number | null) => {
    if (seconds === null) return '--'
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    if (mins > 0) return `${mins}m ${secs}s`
    return `${secs}s`
  }

  return (
    <section aria-label="Previous runs">
      <h2 className="text-sm font-semibold text-gray-700 mb-3">Previous Runs</h2>
      <div className="space-y-2">
        {runs.map((run) => {
          const fbStatus = feedbackStatus[run.run_id]
          const isComplete = run.status === 'complete'

          return (
            <button
              key={run.run_id}
              onClick={() => onSelectRun(run.run_id)}
              className="w-full text-left bg-white border border-gray-200 rounded-lg px-4 py-3 hover:bg-gray-50 transition-colors cursor-pointer focus:ring-2 focus:ring-blue-500 focus:outline-none"
              style={{ touchAction: 'manipulation' }}
            >
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-3 min-w-0">
                  <StatusIcon status={run.status} />
                  <div className="min-w-0">
                    <div className="text-sm font-medium text-gray-900 truncate flex items-center gap-1.5">
                      <FileText className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" aria-hidden="true" />
                      {run.filename}
                    </div>
                    <div className="text-xs text-gray-500 flex items-center gap-2">
                      {formatDate(run.started_at)}
                      {isComplete && fbStatus && (
                        <FeedbackBadge hasFeedback={fbStatus.has_feedback} />
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-3 text-xs text-gray-500 shrink-0">
                  <span className="flex items-center gap-1">
                    <Clock className="w-3 h-3" aria-hidden="true" />
                    {formatDuration(run.total_duration_seconds)}
                  </span>
                  <span className="flex items-center gap-1">
                    <DollarSign className="w-3 h-3" aria-hidden="true" />
                    {run.total_cost.toFixed(3)}
                  </span>
                </div>
              </div>
            </button>
          )
        })}
      </div>
    </section>
  )
}
```

- [ ] **Step 2: Build and verify**

Run: `cd web_interface/frontend && npm run build`
Expected: clean build, no errors

- [ ] **Step 3: Manual test**

1. Navigate to the upload page
2. Verify completed runs show feedback badges:
   - Amber "Needs feedback" badge for runs without feedback from this user
   - Green "Feedback given" badge for runs with feedback from this user
3. Verify non-complete runs (running, failed, cancelled) do NOT show a badge

- [ ] **Step 4: Commit**

```bash
git add web_interface/frontend/src/components/RunHistory.tsx
git commit -m "feat: add amber/green feedback badges to run history items"
```

---

### Task 8: Feedback Tab in Pipeline Viewer

**Files:**
- Modify: `web_interface/frontend/src/components/PipelineViewer.tsx`

- [ ] **Step 1: Add Feedback tab alongside Logs and Prompt Logs**

In `web_interface/frontend/src/components/PipelineViewer.tsx`:

1. Add import at the top:
```tsx
import FeedbackForm from './FeedbackForm'
```

2. Add state for active tab (replace the current `showPromptLogs` boolean with a union type). Add near the existing state declarations:
```tsx
  const [activeTab, setActiveTab] = useState<'logs' | 'prompts' | 'feedback'>('logs')
```

3. Read the `tab` query parameter from the URL to auto-select the feedback tab when navigating from the banner. Add after the existing state declarations:
```tsx
  // Auto-select feedback tab if URL has ?tab=feedback
  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    if (params.get('tab') === 'feedback') {
      setActiveTab('feedback')
    }
  }, [])
```

4. Replace the existing `{/* Log/Prompt Tabs */}` section (the tablist `div` and the two tab panels) with:

```tsx
              {/* Log/Prompt/Feedback Tabs */}
              <div className="mb-4 flex gap-2" role="tablist" aria-label="View options">
                <button
                  onClick={() => setActiveTab('logs')}
                  role="tab"
                  aria-selected={activeTab === 'logs'}
                  aria-controls="log-panel"
                  className={`px-4 py-2 rounded-lg font-medium transition-colors focus:ring-2 focus:ring-primary-500 focus:outline-none ${
                    activeTab === 'logs'
                      ? 'bg-primary-500 text-white'
                      : 'bg-gray-200 text-gray-700 hover:bg-gray-300'
                  }`}
                >
                  Logs
                </button>
                <button
                  onClick={() => {
                    setActiveTab('prompts')
                    fetchPromptLogs()
                  }}
                  role="tab"
                  aria-selected={activeTab === 'prompts'}
                  aria-controls="prompt-log-panel"
                  className={`px-4 py-2 rounded-lg font-medium transition-colors focus:ring-2 focus:ring-primary-500 focus:outline-none ${
                    activeTab === 'prompts'
                      ? 'bg-primary-500 text-white'
                      : 'bg-gray-200 text-gray-700 hover:bg-gray-300'
                  }`}
                >
                  Prompt Logs
                </button>
                {runStatus?.status === 'complete' && (
                  <button
                    onClick={() => setActiveTab('feedback')}
                    role="tab"
                    aria-selected={activeTab === 'feedback'}
                    aria-controls="feedback-panel"
                    className={`px-4 py-2 rounded-lg font-medium transition-colors focus:ring-2 focus:ring-primary-500 focus:outline-none ${
                      activeTab === 'feedback'
                        ? 'bg-amber-500 text-white'
                        : 'bg-amber-100 text-amber-800 hover:bg-amber-200'
                    }`}
                  >
                    Feedback
                  </button>
                )}
              </div>

              {/* Tab Panels */}
              {activeTab === 'logs' && (
                <div id="log-panel" role="tabpanel">
                  <LogViewer
                    logs={logs[currentStep] || []}
                    logsEndRef={logsEndRef}
                  />
                </div>
              )}
              {activeTab === 'prompts' && (
                <div id="prompt-log-panel" role="tabpanel">
                  <PromptLogViewer
                    promptLogs={promptLogs}
                    promptLogsMessage={promptLogsMessage}
                    selectedPromptLog={selectedPromptLog}
                    onSelectPromptLog={setSelectedPromptLog}
                  />
                </div>
              )}
              {activeTab === 'feedback' && (
                <div id="feedback-panel" role="tabpanel">
                  <FeedbackForm runId={runId} />
                </div>
              )}
```

5. Update the `showPromptLogs` references throughout the file. The existing code uses `showPromptLogs` in the `useEffect` for re-fetching prompt logs when step changes. Replace:
```tsx
  // Re-fetch prompt logs when step changes and panel is visible
  useEffect(() => {
    if (showPromptLogs) {
      fetchPromptLogs()
    }
  }, [currentStep, fetchPromptLogs, showPromptLogs])
```
with:
```tsx
  // Re-fetch prompt logs when step changes and panel is visible
  useEffect(() => {
    if (activeTab === 'prompts') {
      fetchPromptLogs()
    }
  }, [currentStep, fetchPromptLogs, activeTab])
```

6. Remove the now-unused `showPromptLogs` state variable and its setter. Remove:
```tsx
  const [showPromptLogs, setShowPromptLogs] = useState(false)
```

- [ ] **Step 2: Build and verify**

Run: `cd web_interface/frontend && npm run build`
Expected: clean build, no errors

- [ ] **Step 3: Manual test**

1. Navigate to a completed run's pipeline viewer
2. Verify three tabs appear: "Logs", "Prompt Logs", "Feedback"
3. Click "Feedback" -- verify the FeedbackForm renders within the tab panel
4. Navigate to a running or failed run -- verify only "Logs" and "Prompt Logs" tabs appear (no Feedback tab)
5. Navigate from upload page by clicking "Give Feedback" on the banner -- verify it goes to `/run/:runId?tab=feedback` and the Feedback tab is auto-selected

- [ ] **Step 4: Commit**

```bash
git add web_interface/frontend/src/components/PipelineViewer.tsx
git commit -m "feat: add Feedback tab to pipeline viewer for completed runs"
```

---

### Post-Implementation Verification Checklist

- [ ] **Database:** `DESCRIBE feedback` shows all columns. `SHOW INDEX FROM feedback` shows unique constraint on `(run_id, user_id)`.
- [ ] **GET endpoint:** `GET /api/run/{run_id}/feedback` returns `{ feedback: null, run_context: { ... } }` for runs with no feedback, and returns feedback data for runs with existing feedback.
- [ ] **POST endpoint:** `POST /api/run/{run_id}/feedback` creates a Feedback record. Returns 409 on duplicate. Returns 422 on invalid field values.
- [ ] **Inter-rater:** Two different users can submit feedback for the same run (two rows in the `feedback` table with the same `run_id` but different `user_id`).
- [ ] **Conditional questions:** Enrichment quality (Q7) hidden when stage 5 did not run. Summary quality (Q8) hidden when stage 4.5 did not run.
- [ ] **Issue location checkboxes:** Pre-populated with only the WCM sections populated in the specific run's output.
- [ ] **CompletionInterstitial:** Appears once when run completes. Shows download + feedback buttons. Does not reappear after dismissal.
- [ ] **FeedbackBanner:** Amber banner shows count of completed runs missing feedback. Goes away when all runs have feedback. Cannot be permanently dismissed.
- [ ] **Run history badges:** Amber "Needs feedback" badge on completed runs without feedback. Green "Feedback given" badge on completed runs with feedback.
- [ ] **Feedback tab:** Appears in pipeline viewer for completed runs only. Auto-selects when navigating with `?tab=feedback`.
- [ ] **Validation:** Required fields (`reviewer_role`, `overall_usefulness`, `manual_conversion_effort`, `correction_effort`, `likelihood_to_recommend`) enforced server-side with clear error messages.
- [ ] **Build:** `npm run build` passes with no TypeScript errors.
