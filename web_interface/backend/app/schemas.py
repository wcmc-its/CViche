"""Pydantic schemas for API request/response validation."""
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, PlainSerializer


def iso_with_offset(dt: datetime) -> str:
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
    datetime, PlainSerializer(iso_with_offset, return_type=str, when_used="json")
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

class RunBySummary(BaseModel):
    """Who ran a run, as the admin "all runs" view shows it."""
    id: int
    display_name: str
    cwid: str | None = None
    email: str | None = None
    department: str | None = None


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
    # Stage 4's inferred CV owner (the run page heading); null until inferred.
    cv_owner_name: str | None = None
    # Who ran it. Admin only; null for everyone else and for user-less runs.
    run_by: RunBySummary | None = None
    # A PDF's scanned pages, whose text is missing from the output (#1282).
    scanned_pages: list[int] = []
    steps: list[StepSummary]

    model_config = ConfigDict(from_attributes=True)


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

    model_config = ConfigDict(from_attributes=True)


class FeedbackReviewer(BaseModel):
    """One reviewer's submission on a run, as the admin runs table lists it."""
    display_name: str
    role: str  # Feedback.reviewer_role
    submitted_at: TZDateTime | None = None


class RunFeedbackSummary(BaseModel):
    """Feedback left on a run by ANY reviewer (including feedback others left
    on the caller's own run). ``reviewers`` is populated only by
    GET /runs?scope=all (admin), newest first; null under scope=mine."""
    count: int = 0
    given_by_me: bool = False
    last_at: TZDateTime | None = None
    reviewers: list[FeedbackReviewer] | None = None


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
    # Advisory quality score columns: only populated by GET /runs?scope=all
    # (admin); always null under scope=mine. quality_cap is set only when a
    # hard-fail cap lowered the score.
    quality_score: int | None = None
    quality_band: str | None = None
    quality_cap: int | None = None
    feedback: RunFeedbackSummary = RunFeedbackSummary()
    # The batch upload this run belongs to (#1114), null for a single upload.
    batch_id: str | None = None

    model_config = ConfigDict(from_attributes=True)


class FilterCount(BaseModel):
    """One option of a runs filter and how many runs it matches."""
    value: str
    count: int


class FacultyOption(FilterCount):
    """A faculty filter option; the list is ordered by ``last_run_at``, newest first."""
    last_run_at: datetime | None = None


class RunByOption(RunBySummary):
    count: int


class FeedbackFilterCounts(BaseModel):
    """Runs matching each value of the ``feedback`` filter."""
    given: int
    needed: int


class InputFormatFilterCounts(BaseModel):
    """Runs matching each value of the ``input_format`` filter."""
    wcm: int
    other: int
    unknown: int


class StatusFilterCounts(BaseModel):
    """Runs behind each status pill (every filter applies except ``status``)."""
    all: int
    running: int
    awaiting_feedback: int
    failed: int
    red: int


class RunFilterOptions(BaseModel):
    """GET /runs/filter-options: the options each admin runs filter offers."""
    departments: list[FilterCount]
    faculty: list[FacultyOption]
    run_by: list[RunByOption]
    self_count: int
    on_behalf_count: int
    status: StatusFilterCounts
    feedback: FeedbackFilterCounts
    input_format: InputFormatFilterCounts


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

    model_config = ConfigDict(from_attributes=True)


class AuthConfigResponse(BaseModel):
    """Public auth configuration for frontend mode detection."""
    mode: str  # "simple" or "saml"
    discovery_url: str | None = None  # Only present when mode is "saml"
    max_upload_mb: int  # The per-file upload cap (CVICHE_MAX_UPLOAD_MB) that /upload enforces


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
    # Where CVs can be emailed; null while email intake (#1298) is off.
    intake_address: str | None = None

    model_config = ConfigDict(from_attributes=True)


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


class CorrectedDocxResponse(BaseModel):
    """POST /run/{id}/feedback/corrected-docx: a one-line confirmation only;
    the typed diff itself is stored with the run, never returned."""
    changes: int
    summary: str


class FeedbackResponse(BaseModel):
    """Response after feedback submission."""
    id: int
    run_id: str
    user_id: int
    reviewer_role: str
    overall_usefulness: int
    likelihood_to_recommend: int
    submitted_at: TZDateTime

    model_config = ConfigDict(from_attributes=True)


class FeedbackDetail(BaseModel):
    """One stored Feedback row plus its reviewer's display name
    (GET /run/{run_id}/feedback/all)."""
    id: int
    run_id: str
    user_id: int
    display_name: str
    reviewer_role: str
    overall_accuracy: int | None = None
    overall_completeness: int | None = None
    overall_usefulness: int
    manual_conversion_effort: str
    correction_effort: str
    enrichment_quality: int | None = None
    summary_generated: int | None = None  # boolean stored as int
    summary_quality: int | None = None
    issue_missing_content: str | None = None
    issue_split_merged: str | None = None
    issue_wrong_section: str | None = None
    issue_inaccurate: str | None = None
    issue_ai_enrichment: str | None = None
    issue_formatting: str | None = None
    issue_locations: list[str] | None = None
    biggest_issue: str | None = None
    likelihood_to_recommend: int
    submitted_at: TZDateTime | None = None


class RunFeedbackStatus(BaseModel):
    """Feedback status for a single run."""
    run_id: str
    has_feedback: bool


# ============================================================
# Admin Schemas
# ============================================================

class AdminStepAvg(BaseModel):
    """Average duration of one pipeline stage over completed runs."""
    stage_id: str
    step_name: str
    avg_seconds: float


class DepartmentSubmissions(BaseModel):
    """Runs one department's submitters filed, by who submitted them."""
    department: str | None  # None: the submitter has no ED department ("Unknown")
    own_cv: int
    on_behalf: int


class SubmissionSplit(BaseModel):
    """Who submits CVs: faculty themselves (own_cv) vs on their behalf (authorized_admin)."""
    own_cv: int = 0
    on_behalf: int = 0
    departments: list[DepartmentSubmissions] = []


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
    submissions: SubmissionSplit = SubmissionSplit()


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

    model_config = ConfigDict(from_attributes=True)


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

    model_config = ConfigDict(from_attributes=True)


class DimensionScore(BaseModel):
    """One weighted dimension of ``quality_score.score_run``'s result, as it
    builds each ``dimensionScores`` entry: ``score`` earned out of ``max`` (the
    weight), ``penalty`` lost, and the scorer's ``detail`` line."""
    name: str
    score: float
    max: int
    penalty: float
    detail: str


class QualityScoreResult(BaseModel):
    """Per-run quality score detail (advisory, computed from artifacts)."""
    run_id: str
    totalScore: int
    band: str
    dimensionScores: list[DimensionScore] = []
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


class QueueStreamStats(BaseModel):
    """One queue's stream as ``run_queue.stats`` reads it (#1114): depth,
    group pending/lag/consumers, per-consumer pending, dead-letter count."""
    stream_length: int
    pending: int | None = None
    lag: int | None = None
    consumers: int
    owners: list[dict] = []
    dead: int


class QueueStatsResponse(BaseModel):
    """Run-queue depth and ownership (Valkey), beside the DB view, for the
    admin dashboard (#701). ``enabled`` is ``dispatch_mode() == "queue"``,
    independent of whether ``CVICHE_REDIS_URL`` happens to be set (other
    features share that same URL). ``error`` is a stable code
    (``valkey_unavailable`` / ``valkey_not_configured``) -- never raw
    exception text, which can carry a host:port.

    The top-level stream fields are the single-run queue's, as before batch
    upload; ``queues`` has every queue's, keyed by short name (``single``,
    ``batch``, #1114), so the batch stream and its dead-letter count show
    without renaming a field an existing reader uses."""
    enabled: bool
    db: QueueDbView
    error: str | None = None
    stream_length: int | None = None
    pending: int | None = None
    lag: int | None = None
    consumers: int | None = None
    owners: list[dict] = []
    dead: int | None = None
    queues: dict[str, QueueStreamStats] = {}


# ============================================================
# Batch upload Schemas (#1114)
# ============================================================

class BatchCreateRequest(BaseModel):
    """POST /api/batches: how many valid files the user is about to upload,
    and whether to email them when every run is finished (#1335)."""
    files_submitted: int = Field(ge=1)
    notify_on_complete: bool = False


class BatchCreateResponse(BaseModel):
    id: str


class BatchSummary(BaseModel):
    """One batch in GET /api/batches (the Runs page's Batch filter)."""
    id: str
    submitted_by: RunBySummary | None = None
    created_at: TZDateTime
    run_count: int
    files_submitted: int


class BatchListResponse(BaseModel):
    batches: list[BatchSummary]


class BatchStatusCounts(BaseModel):
    """How many of a batch's runs are in each status. ``created`` is a run
    uploaded but not yet started (the client starts each right after upload)."""
    complete: int = 0
    running: int = 0
    queued: int = 0
    failed: int = 0
    cancelled: int = 0
    created: int = 0


class BatchRunRow(BaseModel):
    """One run in the batch view. ``queue_position`` is set on queued rows
    only: how many queued batch runs entered the queue before this one.
    ``quality_score`` is admin-only, like the Runs list's Score column."""
    run_id: str
    filename: str
    cv_owner_name: str | None = None
    status: str
    queue_position: int | None = None
    quality_score: int | None = None


class BatchDetail(BatchSummary):
    """GET /api/batches/{id}: the batch view's header and rows, oldest run first."""
    status_counts: BatchStatusCounts
    runs: list[BatchRunRow]


class QueueLane(BaseModel):
    """One queue in GET /api/queue. ``workers`` counts live consumers of the
    queue (null when Valkey can't be read); ``ahead`` is runs waiting in it;
    ``est_wait_minutes`` is (ahead + that kind's running runs) x the recent
    median run time / workers, null when either is unknown. The batch figure
    is a best case: single runs can take the flex workers too."""
    workers: int | None
    ahead: int
    est_wait_minutes: int | None


class QueueOverview(BaseModel):
    """GET /api/queue, for any signed-in user. ``single``/``batch`` are null
    unless ``dispatch_mode`` is ``queue`` -- in-process dispatch has no queue.
    ``completion_email_available``: the server can send mail, so the page
    offers "Email me when job completes" (#1335)."""
    dispatch_mode: str
    completion_email_available: bool = False
    single: QueueLane | None = None
    batch: QueueLane | None = None


class AdminConfigResponse(BaseModel):
    """Current system configuration."""
    allowed_users: list[str] = []
    admin_users: list[str] = []
    rate_limit_daily: int = 10
    rate_limit_monthly: int = 50
    consent_version: str = "1.0"
    auth_mode: str = "simple"


class ConsentPublishPreview(BaseModel):
    """What publishing the next consent version would do, before it is published."""
    current_version: str
    next_version: str
    users_to_reconsent: int  # active users whose consent_version is not next_version


class ConsentPublishRequest(BaseModel):
    """The version the admin was shown; must still be the next version."""
    version: str


class AdminConfigUpdate(BaseModel):
    """Request body for updating system config."""
    allowed_users: list[str] | None = None
    admin_users: list[str] | None = None
    rate_limit_daily: int | None = None
    rate_limit_monthly: int | None = None
    consent_version: str | None = None


# --- Run quality / run doctor (admin run page) and the owner-facing note ---

DoctorSeverity = Literal["ERROR", "WARN", "INFO"]
QualityBand = Literal["GREEN", "YELLOW", "RED"]


class QualityDimension(BaseModel):
    """One weighted scorer dimension: ``points`` earned out of ``weight``. The
    wording fields are null for a row named by an older scorer build."""
    name: str  # the scorer's technical name
    weight: int
    points: float
    label: str | None = None  # plain label
    checks: str | None = None  # what the row checks
    scoring: str | None = None  # how it loses points, or the cap it applies
    if_lost: str | None = None  # what to do when it loses points
    can_cap: bool = False


class QualityGate(BaseModel):
    """A weight-0 score row whose cap fired; it carries no points."""
    name: str
    cap: int
    lint: str  # the doctor lint that reports the same condition
    label: str | None = None
    checks: str | None = None
    scoring: str | None = None
    if_lost: str | None = None


class DoctorSeverityCounts(BaseModel):
    """Distinct lints that fired at each severity (not finding instances)."""
    error: int = 0
    warn: int = 0
    info: int = 0


class DoctorFindingInstance(BaseModel):
    """One instance of a lint: where in the CV it is and what it quotes."""
    severity: DoctorSeverity
    section: str | None = None  # CV section name; None when the finding names no taxonomy code
    detail: str  # the doctor's own message, its entry/code prefix and issue refs removed
    quotes: list[str] = []  # text the doctor quotes from the CV or output; "\u2026" where it was cut
    notes: list[str] = []  # the doctor's own locators ("row 3: ...", "entry 16"), not CV text


class DoctorFindingGroup(BaseModel):
    """One lint that fired, collapsed across its instances."""
    lint: str
    severity: DoctorSeverity  # the most severe of the lint's instances
    message: str  # plain-English explanation of the lint
    title: str | None = None  # plain title; null for a lint with no wording yet
    what_to_do: str | None = None
    count: int  # instances of this lint in the run
    prevalence: float | None = None  # share of runs it fires on; None if unmeasured
    caps_score: bool = False  # this lint is the gate behind the run's applied cap
    # Worst first, then report order; at most MAX_INSTANCES_SHOWN, so count can exceed its length.
    instances: list[DoctorFindingInstance] = []


FixConfidence = Literal["high", "medium", "unmeasured"]
FixEffort = Literal["quick", "minutes", "longer"]


class FixListProblem(BaseModel):
    """One finding as the Fix list words it (#1589): plain title and action
    only, never a lint key, stage, entry index or issue number."""
    severity: DoctorSeverity
    title: str
    what_to_do: str
    confidence: FixConfidence  # from the lint's hand-checked precision (doctor/PRECISION.md)
    effort: FixEffort


class FixListItem(BaseModel):
    """Every finding about one entry (or one finding that names no entry)."""
    problems: list[FixListProblem]  # worst first
    quotes: list[str] = []  # the text at stake, as the doctor quotes it


class FixListGroup(BaseModel):
    """One document location; groups come in the document's section order."""
    section: str | None = None  # None: the finding names no section
    items: list[FixListItem]


class RunDoctorReport(BaseModel):
    counts: DoctorSeverityCounts
    findings: list[DoctorFindingGroup]  # rarest lint first
    not_run: int = 0  # lints skipped or unreadable (an input artifact was absent)
    fix_list: list[FixListGroup] = []  # the CV runner's view (#1589)
    # ERROR/WARN findings left to Diagnostics: below the precision bar, or no plain wording yet.
    fix_list_held_back: int = 0
    fix_list_more: int = 0  # items past MAX_FIX_LIST_ITEMS, listed in Diagnostics only
    not_checked: list[str] = []  # what the doctor cannot see, on every run


class RunQualityReport(BaseModel):
    """GET /run/{run_id}/run-quality (admin). Score fields are null together
    when no score is cached; ``doctor`` is null when no report was stored."""
    run_id: str
    score: int | None = None
    band: QualityBand | None = None
    band_meaning: str | None = None
    provisional: bool = True
    cap: int | None = None
    cap_reason: str | None = None
    cap_lint: str | None = None
    earned: int | None = None  # weighted total before the cap
    total_weight: int | None = None
    data_complete: bool | None = None
    dimensions: list[QualityDimension] = []
    gates_fired: list[QualityGate] = []  # weight-0 rows whose cap fired
    doctor: RunDoctorReport | None = None


class RunReviewNote(BaseModel):
    """GET /run/{run_id}/review-note: never carries the score itself.

    `scored` is false until the run's score is stored (a few seconds after the
    run turns complete), so the page knows `needs_cleanup` is not final yet."""
    needs_cleanup: bool
    scored: bool
