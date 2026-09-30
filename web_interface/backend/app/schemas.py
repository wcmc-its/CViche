"""Pydantic schemas for API request/response validation."""
from pydantic import BaseModel, PlainSerializer
from typing import Annotated, Literal
from datetime import datetime


def _iso_with_offset(dt: datetime) -> str:
    """Serialize a datetime to ISO 8601 with an explicit UTC offset.

    Timestamps are written with naive ``datetime.now()`` (and ``func.now()``),
    i.e. the server's wall clock with no tzinfo. Pydantic serializes a naive
    datetime with no timezone designator (``2026-06-11T09:00:00``), so the
    browser's ``new Date()`` interprets it in the *viewer's* local zone. For a
    UTC prod pod viewed from a non-UTC client every timestamp is then wrong by
    the client's offset (e.g. a just-uploaded file shows "5 hours ago" from
    UTC+5:30). Attaching the server's own offset (``astimezone()`` treats a
    naive value as local) makes the instant unambiguous; the client converts it
    correctly regardless of viewer zone. Assumption-free: writes and this
    serializer use the same server zone, so we never need to know what it is.
    """
    if dt.tzinfo is None:
        dt = dt.astimezone()
    return dt.isoformat()


# Apply to every datetime field the API returns. ``when_used='json'`` keeps
# Python-mode access (``model.started_at``) a real datetime for internal callers
# and only rewrites the JSON the browser receives.
TZDateTime = Annotated[
    datetime, PlainSerializer(_iso_with_offset, return_type=str, when_used="json")
]


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
    wcm_template_match_ratio: float | None = None


# ============================================================
# Run Schemas
# ============================================================

class RunStatus(BaseModel):
    """Overall run status."""
    run_id: str
    filename: str
    file_type: str
    status: str
    started_at: TZDateTime
    completed_at: TZDateTime | None = None
    total_cost: float | None  # None for non-admins (#1111)
    total_tokens: int
    input_tokens: int = 0
    output_tokens: int = 0
    total_duration_seconds: int | None = None
    # Input-scaled wall-clock estimate from upload; the client stall watchdog
    # scales its "taking longer than expected" threshold off this. NULL for runs
    # created before the column existed (watchdog falls back to a default).
    estimated_duration_seconds: int | None = None
    error_message: str | None = None
    steps: list[StepSummary]

    class Config:
        from_attributes = True


class StepSummary(BaseModel):
    """Summary of a single step."""
    step_number: int
    stage_id: str | None = None  # e.g., '1a', '1b', '2', '3a', '3b', '4', '4.5', '5', '5b', '5c', '5d', '6'
    step_name: str
    status: str
    started_at: TZDateTime | None = None
    completed_at: TZDateTime | None = None
    duration_seconds: int | None = None
    cost: float | None  # None for non-admins (#1111)
    output_files: str | None = None  # JSON array as string

    class Config:
        from_attributes = True


class RunBySummary(BaseModel):
    """Who ran a run, as the admin "all runs" view shows it."""
    id: int
    display_name: str
    cwid: str | None = None
    email: str | None = None
    department: str | None = None


class RunSummary(BaseModel):
    """Summary of a run for the history list."""
    run_id: str
    filename: str
    status: str
    started_at: TZDateTime
    completed_at: TZDateTime | None = None
    total_cost: float | None  # None for non-admins (#1111)
    total_duration_seconds: int | None = None
    cv_owner_name: str | None = None
    submission_type: str | None = None
    # Only populated by GET /runs?scope=all (admin); null for runs without a
    # user and always null under scope=mine.
    run_by: RunBySummary | None = None

    class Config:
        from_attributes = True


class FilterCount(BaseModel):
    """One option of a runs filter and how many runs it matches."""
    value: str
    count: int


class RunByOption(RunBySummary):
    count: int


class RunFilterOptions(BaseModel):
    """GET /runs/filter-options: the options each admin runs filter offers."""
    departments: list[FilterCount]
    faculty: list[FilterCount]
    run_by: list[RunByOption]
    self_count: int


class PaginatedRuns(BaseModel):
    """Paginated list of runs."""
    runs: list[RunSummary]
    total: int
    has_more: bool
    offset: int
    limit: int


class CapacityResponse(BaseModel):
    """GET /api/capacity: advisory run-admission snapshot for this pod (#177)."""
    available: bool
    active: int
    limit: int


class RunActionResponse(BaseModel):
    """Body of /start, /cancel and /retry: a human-readable message plus the
    run's status after the action. Field order is the wire order (#801)."""
    message: str
    status: str


class RestartRunResponse(BaseModel):
    """Body of /restart: the id of the newly created run (#801)."""
    run_id: str
    message: str


# ============================================================
# Step Detail Schemas
# ============================================================

class LogEntry(BaseModel):
    """Single log entry."""
    time: str
    level: str
    message: str


class OutputPreview(BaseModel):
    """Preview of step output data.

    truncated/total_rows are additive (#780 review r3965770586): previews cap
    at PREVIEW_MAX_ROWS in app.services.artifact_service, so a large pipeline
    artifact can no longer be expanded into an unbounded response. Both default
    so existing callers that construct an OutputPreview without them keep working.
    """
    headers: list[str]
    rows: list[list[str]]
    truncated: bool = False
    total_rows: int | None = None


class StepDetail(BaseModel):
    """Detailed view of a single step."""
    step_id: int
    step_number: int
    name: str
    status: str
    duration: int | None = None
    cost_usd: float | None  # None for non-admins (#1111)
    input_file: str | None = None
    output_files: list[str]
    logs: list[LogEntry]
    output_preview: OutputPreview | None = None


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
    output_files: list[str]


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
    discovery_url: str | None = None  # Only present when mode is "saml"


class QuotaInfo(BaseModel):
    """Rate limit quota information."""
    daily_limit: int | None = None  # None = unlimited
    daily_used: int = 0
    daily_remaining: int | None = None
    monthly_limit: int | None = None
    monthly_used: int = 0
    monthly_remaining: int | None = None
    is_admin: bool = False


class MeResponse(BaseModel):
    """Current user info."""
    user_id: int
    cwid: str | None = None
    email: str | None = None
    display_name: str
    role: str
    consent_version: str | None = None
    default_submission_type: str | None = None
    quota: QuotaInfo | None = None

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
    default_submission_type: Literal["own_cv", "authorized_admin"]


class ConsentSubmitResponse(BaseModel):
    """Response for POST /api/consent."""
    message: str
    consent_version: str
    default_submission_type: str


# ============================================================
# Feedback Schemas
# ============================================================

class FeedbackSubmit(BaseModel):
    """Feedback submission from a user on a pipeline run."""
    reviewer_role: str  # required
    overall_accuracy: int | None = None  # 1-10
    overall_completeness: int | None = None  # 1-10
    overall_usefulness: int  # 1-5, required
    manual_conversion_effort: str  # required
    correction_effort: str  # required
    enrichment_quality: int | None = None  # 1-5
    summary_generated: bool | None = None
    summary_quality: int | None = None  # 1-5
    issue_missing_content: str | None = None
    issue_split_merged: str | None = None
    issue_wrong_section: str | None = None
    issue_inaccurate: str | None = None
    issue_ai_enrichment: str | None = None
    issue_formatting: str | None = None
    issue_locations: list[str] | None = None
    biggest_issue: str | None = None
    likelihood_to_recommend: int  # 1-5, required


class FeedbackResponse(BaseModel):
    """Response after feedback submission."""
    id: int
    run_id: str
    user_id: int
    reviewer_role: str
    overall_usefulness: int
    likelihood_to_recommend: int
    submitted_at: TZDateTime

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

class AdminStepAvg(BaseModel):
    """Average duration of one pipeline stage over completed runs."""
    stage_id: str
    step_name: str
    avg_seconds: float


class AdminStats(BaseModel):
    """Overview statistics for the admin dashboard."""
    total_runs: int
    active_users: int
    total_cost: float
    feedback_rate: float  # percentage 0-100
    # CV-to-WCM conversion time over completed runs (whole seconds). None when
    # there are no completed runs yet.
    avg_duration_seconds: float | None = None
    p95_duration_seconds: int | None = None
    # Per-stage average duration over completed runs, in pipeline order.
    step_avg_seconds: list[AdminStepAvg] = []


class AdminUser(BaseModel):
    """User record with per-user stats for admin view."""
    id: int
    cwid: str | None = None
    email: str | None = None
    display_name: str
    role: str
    status: str
    daily_limit: int | None = None
    monthly_limit: int | None = None
    runs_today: int = 0
    total_runs: int = 0
    total_cost: float = 0.0
    feedback_count: int = 0
    completed_run_count: int = 0
    last_active_at: TZDateTime | None = None
    created_at: TZDateTime | None = None

    class Config:
        from_attributes = True


class AdminUserUpdate(BaseModel):
    """Request body for updating a user via admin.

    role/status are constrained to their valid sets so an out-of-set value
    (e.g. role="viewer") 422s here instead of reaching the last-admin /
    last-active-admin guards in admin_routes.update_user, which compare
    against the literal strings "user"/"admin" and "active"/"disabled" and
    silently no-op the guard for anything else (#409).
    """
    role: Literal["user", "admin"] | None = None
    status: Literal["active", "disabled"] | None = None
    daily_limit: int | None = None  # 0 = reset to system default
    monthly_limit: int | None = None  # 0 = reset to system default


class AdminRunEntry(BaseModel):
    """Single run entry for admin run listing."""
    run_id: str
    user_email: str | None = None
    user_display_name: str | None = None
    filename: str
    status: str
    duration_seconds: int | None = None
    total_cost: float = 0.0
    started_at: TZDateTime | None = None
    has_feedback: bool = False
    quality_score: int | None = None      # advisory 0-100, None if not computed
    quality_band: str | None = None       # "GREEN (ship)" / "YELLOW ..." / "RED ..."
    # False when the score was computed with a scored artifact missing or
    # unreadable (#745); None when not computed or cached before the field existed.
    quality_data_complete: bool | None = None
    quality_missing_evidence: list[str] = []

    class Config:
        from_attributes = True


class QualityScoreResult(BaseModel):
    """Per-run quality score detail (advisory, computed from artifacts)."""
    run_id: str
    totalScore: int
    band: str
    dimensionScores: list[dict] = []
    flags: list[str] = []
    # quality_score.score_run's evidence inventory (#745): data_complete is
    # False when any scored artifact was missing or unreadable, and
    # missing_evidence names each one. None only for a pre-#724 result.
    data_complete: bool | None = None
    missing_evidence: list[str] = []


class AdminRunsResponse(BaseModel):
    """Paginated list of all runs for admin."""
    runs: list[AdminRunEntry]
    total: int
    has_more: bool
    offset: int
    limit: int


class QueueDbView(BaseModel):
    """DB side of the run-queue stats (#701): counts and ages by status,
    matching ``run_service.queue_db_view``'s keys."""
    queued: int
    running: int
    oldest_queued_age_s: float | None = None
    oldest_running_age_s: float | None = None


class QueueStatsResponse(BaseModel):
    """Run-queue depth and ownership (Valkey), beside the DB view, for the
    admin dashboard (#701). ``enabled`` is ``dispatch_mode() == "queue"``,
    independent of whether ``CVICHE_REDIS_URL`` happens to be set (other
    features share that same URL). ``error`` is a stable code
    (``valkey_unavailable`` / ``valkey_not_configured``) -- never raw
    exception text, which can carry a host:port."""
    enabled: bool
    db: QueueDbView
    error: str | None = None
    stream_length: int | None = None
    pending: int | None = None
    lag: int | None = None
    consumers: int | None = None
    owners: list[dict] = []
    dead: int | None = None


class AdminConfigResponse(BaseModel):
    """Current system configuration."""
    allowed_users: list[str] = []
    admin_users: list[str] = []
    rate_limit_daily: int = 10
    rate_limit_monthly: int = 50
    consent_version: str = "1.0"
    auth_mode: str = "simple"


class AdminConfigUpdate(BaseModel):
    """Request body for updating system config."""
    allowed_users: list[str] | None = None
    admin_users: list[str] | None = None
    rate_limit_daily: int | None = None
    rate_limit_monthly: int | None = None
    consent_version: str | None = None
