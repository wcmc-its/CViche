"""Pydantic schemas for API request/response validation."""
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


# ============================================================
# Upload Schemas
# ============================================================

class UploadResponse(BaseModel):
    """Response after file upload."""
    run_id: str
    filename: str
    file_type: str
    status: str
    message: str
    # Cheap, no-LLM heuristic: True when the upload looks like a blank/near-blank
    # WCM faculty CV template. The frontend uses this to warn the user (and
    # require an acknowledgement) that formatting may regress before spending a
    # run on what is most likely the unfilled template. Best-effort: defaults to
    # no warning if detection couldn't run, so it never blocks an upload.
    wcm_template_warning: bool = False
    # Fraction of non-trivial lines that matched the blank-template string set
    # (0.0 = clearly a real CV, ~1.0 = clearly an unfilled template). Returned
    # for server-side logging/telemetry and available to the client; the UI
    # currently shows a qualitative warning rather than this raw number. None
    # when the ratio couldn't be computed.
    wcm_template_match_ratio: Optional[float] = None


# ============================================================
# Run Schemas
# ============================================================

class RunStatus(BaseModel):
    """Overall run status."""
    run_id: str
    filename: str
    file_type: str
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    total_cost: float
    total_tokens: int
    input_tokens: int = 0
    output_tokens: int = 0
    total_duration_seconds: Optional[int] = None
    error_message: Optional[str] = None
    steps: List["StepSummary"]

    class Config:
        from_attributes = True


class StepSummary(BaseModel):
    """Summary of a single step."""
    step_number: int
    stage_id: Optional[str] = None  # e.g., '1a', '1b', '2', '3a', '3b', '4', '4.5', '5', '5b', '5c', '5d', '6'
    step_name: str
    status: str
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    duration_seconds: Optional[int] = None
    cost: float
    output_files: Optional[str] = None  # JSON array as string

    class Config:
        from_attributes = True


class RunSummary(BaseModel):
    """Summary of a run for the history list."""
    run_id: str
    filename: str
    status: str
    started_at: datetime
    completed_at: Optional[datetime] = None
    total_cost: float
    total_duration_seconds: Optional[int] = None

    class Config:
        from_attributes = True


class PaginatedRuns(BaseModel):
    """Paginated list of runs."""
    runs: List[RunSummary]
    total: int
    has_more: bool
    offset: int
    limit: int


# ============================================================
# Step Detail Schemas
# ============================================================

class LogEntry(BaseModel):
    """Single log entry."""
    time: str
    level: str
    message: str


class OutputPreview(BaseModel):
    """Preview of step output data."""
    headers: List[str]
    rows: List[List[str]]


class StepDetail(BaseModel):
    """Detailed view of a single step."""
    step_id: int
    step_number: int
    name: str
    status: str
    duration: Optional[int] = None
    cost_usd: float
    input_file: Optional[str] = None
    output_files: List[str]
    logs: List[LogEntry]
    output_preview: Optional[OutputPreview] = None


# ============================================================
# WebSocket Event Schemas
# ============================================================

class WebSocketEvent(BaseModel):
    """Base WebSocket event."""
    event: str
    data: dict


class StepStartEvent(BaseModel):
    """Step started event."""
    event: str = "STEP_START"
    step: int


class LogEvent(BaseModel):
    """Log message event."""
    event: str = "LOG"
    step: int
    level: str
    message: str


class StepCompleteEvent(BaseModel):
    """Step completed event."""
    event: str = "STEP_COMPLETE"
    step: int
    duration: int
    cost: float
    output_files: List[str]


class RunCompleteEvent(BaseModel):
    """Run completed event."""
    event: str = "RUN_COMPLETE"
    total_cost: float
    total_tokens: int
    duration: int


# ============================================================
# Auth Schemas
# ============================================================

class LoginRequest(BaseModel):
    """Login request body."""
    email: str
    display_name: str


class LoginResponse(BaseModel):
    """Login response body."""
    user_id: int
    email: str
    display_name: str
    role: str

    class Config:
        from_attributes = True


class AuthConfigResponse(BaseModel):
    """Public auth configuration for frontend mode detection."""
    mode: str  # "simple" or "saml"
    discovery_url: Optional[str] = None  # Only present when mode is "saml"


class QuotaInfo(BaseModel):
    """Rate limit quota information."""
    daily_limit: Optional[int] = None  # None = unlimited
    daily_used: int = 0
    daily_remaining: Optional[int] = None
    monthly_limit: Optional[int] = None
    monthly_used: int = 0
    monthly_remaining: Optional[int] = None
    is_admin: bool = False


class MeResponse(BaseModel):
    """Current user info."""
    user_id: int
    email: str
    display_name: str
    role: str
    consent_version: Optional[str] = None
    default_submission_type: Optional[str] = None
    quota: Optional[QuotaInfo] = None

    class Config:
        from_attributes = True


# ============================================================
# Consent Schemas
# ============================================================

class ConsentStatus(BaseModel):
    """Response for GET /api/consent."""
    text: str
    version: str
    user_has_consented: bool
    current_hash: str


class ConsentSubmit(BaseModel):
    """Request for POST /api/consent."""
    default_submission_type: str  # "own_cv" or "authorized_admin"


# ============================================================
# Feedback Schemas
# ============================================================

class FeedbackSubmit(BaseModel):
    """Feedback submission from a user on a pipeline run."""
    reviewer_role: str  # required
    overall_accuracy: Optional[int] = None  # 1-10
    overall_completeness: Optional[int] = None  # 1-10
    overall_usefulness: int  # 1-5, required
    manual_conversion_effort: str  # required
    correction_effort: str  # required
    enrichment_quality: Optional[int] = None  # 1-5
    summary_generated: Optional[bool] = None
    summary_quality: Optional[int] = None  # 1-5
    issue_missing_content: Optional[str] = None
    issue_split_merged: Optional[str] = None
    issue_wrong_section: Optional[str] = None
    issue_inaccurate: Optional[str] = None
    issue_ai_enrichment: Optional[str] = None
    issue_formatting: Optional[str] = None
    issue_locations: Optional[list[str]] = None
    biggest_issue: Optional[str] = None
    likelihood_to_recommend: int  # 1-5, required


class FeedbackResponse(BaseModel):
    """Response after feedback submission."""
    id: int
    run_id: str
    user_id: int
    reviewer_role: str
    overall_usefulness: int
    likelihood_to_recommend: int
    submitted_at: datetime

    class Config:
        from_attributes = True


class RunFeedbackStatus(BaseModel):
    """Feedback status for a single run."""
    run_id: str
    has_feedback: bool


# ============================================================
# Settings Schemas
# ============================================================

class Settings(BaseModel):
    """Pipeline settings."""
    model: str = "gpt-4o-mini"
    max_tokens: int = 16000
    enable_step_8: bool = False  # Organization enrichment
    pricing: dict = {
        "gpt-4o-mini": {
            "input_per_1k": 0.00015,
            "output_per_1k": 0.0006
        },
        "gpt-4o": {
            "input_per_1k": 0.0025,
            "output_per_1k": 0.01
        }
    }


# ============================================================
# Admin Schemas
# ============================================================

class AdminStats(BaseModel):
    """Overview statistics for the admin dashboard."""
    total_runs: int
    active_users: int
    total_cost: float
    feedback_rate: float  # percentage 0-100
    # CV-to-WCM conversion time over completed runs (whole seconds). None when
    # there are no completed runs yet.
    avg_duration_seconds: Optional[float] = None
    p95_duration_seconds: Optional[int] = None


class AdminUser(BaseModel):
    """User record with per-user stats for admin view."""
    id: int
    email: str
    display_name: str
    role: str
    status: str
    daily_limit: Optional[int] = None
    monthly_limit: Optional[int] = None
    runs_today: int = 0
    total_runs: int = 0
    total_cost: float = 0.0
    feedback_count: int = 0
    completed_run_count: int = 0
    last_active_at: Optional[datetime] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AdminUserUpdate(BaseModel):
    """Request body for updating a user via admin."""
    role: Optional[str] = None  # "user" or "admin"
    status: Optional[str] = None  # "active" or "disabled"
    daily_limit: Optional[int] = None  # 0 = reset to system default
    monthly_limit: Optional[int] = None  # 0 = reset to system default


class AdminRunEntry(BaseModel):
    """Single run entry for admin run listing."""
    run_id: str
    user_email: Optional[str] = None
    user_display_name: Optional[str] = None
    filename: str
    status: str
    duration_seconds: Optional[int] = None
    total_cost: float = 0.0
    started_at: Optional[datetime] = None
    has_feedback: bool = False
    quality_score: Optional[int] = None      # advisory 0-100, None if not computed
    quality_band: Optional[str] = None       # "GREEN (ship)" / "YELLOW ..." / "RED ..."

    class Config:
        from_attributes = True


class QualityScoreResult(BaseModel):
    """Per-run quality score detail (advisory, computed from artifacts)."""
    run_id: str
    totalScore: int
    band: str
    dimensionScores: List[dict] = []
    flags: List[str] = []


class AdminRunsResponse(BaseModel):
    """Paginated list of all runs for admin."""
    runs: List[AdminRunEntry]
    total: int
    has_more: bool
    offset: int
    limit: int


class AdminConfigResponse(BaseModel):
    """Current system configuration."""
    allowed_users: List[str] = []
    admin_users: List[str] = []
    rate_limit_daily: int = 10
    rate_limit_monthly: int = 50
    consent_version: str = "1.0"
    auth_mode: str = "simple"


class AdminConfigUpdate(BaseModel):
    """Request body for updating system config."""
    allowed_users: Optional[List[str]] = None
    admin_users: Optional[List[str]] = None
    rate_limit_daily: Optional[int] = None
    rate_limit_monthly: Optional[int] = None
    consent_version: Optional[str] = None
