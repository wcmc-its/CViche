"""SQLAlchemy database models."""
from enum import StrEnum

from sqlalchemy import Boolean, Column, String, Integer, Float, Text, DateTime, ForeignKey, UniqueConstraint, false, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.base_class import Base

# Length of a hex-encoded sha256 digest.
SHA256_HEX_LENGTH = 64


class RunState(StrEnum):
    """Canonical ``runs.status`` vocabulary (CODING STANDARDS section 1.5: one
    definition of a shared vocabulary, not a hand-written literal at each call
    site). Named ``RunState``, not ``RunStatus`` -- ``app.schemas.RunStatus``
    already names the pydantic response model for a run's status field, and
    the two would collide.

    Used on every line the #701 queue rework adds or changes (the flip/claim/
    fail transitions in ``app.services.run_service``). Existing status string
    literals elsewhere in the codebase are intentionally left as they are (no
    drive-by conversions, CODING STANDARDS section 8.1); the remaining sweep
    is tracked in issue #701.
    """
    CREATED = "created"
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"


class UserRole(StrEnum):
    """Canonical ``users.role`` vocabulary, same pattern as ``RunState``.

    STAFF is read-only elevated access: every run
    and its pipeline detail, but no cost and no admin writes (see
    ``can_view_all_runs``, after ``User``). Used on the lines the staff role adds or
    changes; existing "admin"/"user" literals elsewhere are left as they are
    (no drive-by conversions, CODING STANDARDS section 8.1).
    """
    USER = "user"
    STAFF = "staff"
    ADMIN = "admin"


# ==========================
# Authentication Models
# ==========================
class User(Base):
    """User accounts for authentication and authorization."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # cwid is the SSO identity anchor (unique, stable, present for every WCM
    # identity). email is preferred-but-optional -- nothing in the app sends
    # mail, so a user without an ED `mail` (e.g. external affiliates) still
    # authenticates. nullable for simple-auth users, who have no CWID.
    # A partner-institution user's anchor is their scoped ePPN instead
    # ("user@cornell.edu"), never a bare CWID -- hence 255 (#1452).
    cwid = Column(String(255), unique=True, nullable=True, index=True)
    email = Column(String(255), unique=True, nullable=True, index=True)
    display_name = Column(String(255), nullable=False)
    # A UserRole value: "user", "staff" or "admin". A plain String with no
    # Enum type or CHECK constraint, so adding a role needs no migration.
    role = Column(String(20), nullable=False, default=UserRole.USER)
    status = Column(String(20), nullable=False, default="active")  # "active" or "disabled"
    daily_limit = Column(Integer, nullable=True)
    monthly_limit = Column(Integer, nullable=True)
    default_submission_type = Column(String(50), nullable=True)
    # Department name from the Enterprise Directory, refreshed at SAML login.
    # NULL for simple-auth users and when ED carries no department attribute.
    department = Column(String(255), nullable=True)
    auth_method = Column(String(20), nullable=True, server_default="simple")  # "simple" or "saml"
    consent_version = Column(String(50), nullable=True)
    consent_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    last_active_at = Column(DateTime, server_default=func.now())

    # ORM relationships. lazy="raise_on_sql": callers must eager-load the paths
    # they need (selectinload/joinedload); a stray lazy load raises instead of
    # silently emitting SQL or detaching outside the request/session scope (we
    # run pipeline work in background tasks). No cascades -- deletes defer to the
    # DB FK rules via passive_deletes; a deliberate cascade decision is left to
    # the follow-up (issue #131, step 3).
    runs = relationship("Run", back_populates="user", lazy="raise_on_sql", passive_deletes=True)
    consents = relationship("Consent", back_populates="user", lazy="raise_on_sql", passive_deletes=True)
    feedback = relationship("Feedback", back_populates="user", lazy="raise_on_sql", passive_deletes=True)


def can_view_all_runs(user: User) -> bool:
    """Read-only access to every user's runs: the runs list, run detail, "Run
    by", run quality, batches, pipeline logs/prompts/stage JSON, and feedback
    insights.

    Admin or staff. Grants READS only -- every write
    (admin config, user management, deleting feedback or runs, acting on
    another user's run) stays behind require_admin or ownership, and cost stays
    behind can_see_cost. Lives here, not in app.auth, so services the worker
    imports (run_service, batch_service) need not import app.auth, which reads
    CVICHE_SESSION_SECRET at import (see services/ed_access.py).
    """
    return user.role in (UserRole.ADMIN, UserRole.STAFF)


class Consent(Base):
    """Audit trail for user consent acceptance."""
    __tablename__ = "consent"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    consent_version = Column(String(50), nullable=False)
    consent_text_hash = Column(String(64), nullable=False)
    ip_address = Column(String(45), nullable=True)
    user_agent = Column(String(512), nullable=True)
    timestamp = Column(DateTime, server_default=func.now())
    user = relationship("User", back_populates="consents", lazy="raise_on_sql")
# ==========================
# Feedback Models
# ==========================
class Feedback(Base):
    """User feedback on a pipeline run."""
    __tablename__ = "feedback"
    __table_args__ = (UniqueConstraint('run_id', 'user_id', name='uq_feedback_run_user'),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(10), ForeignKey("runs.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    reviewer_role = Column(String(50), nullable=False)
    overall_accuracy = Column(Integer, nullable=True)  # 1-10 or null
    overall_completeness = Column(Integer, nullable=True)  # 1-10 or null
    overall_usefulness = Column(Integer, nullable=False)  # 1-5
    manual_conversion_effort = Column(String(50), nullable=False)
    correction_effort = Column(String(50), nullable=False)
    enrichment_quality = Column(Integer, nullable=True)  # 1-5 or null
    summary_generated = Column(Integer, nullable=True)  # boolean as int
    summary_quality = Column(Integer, nullable=True)  # 1-5 or null
    issue_missing_content = Column(Text, nullable=True)
    issue_split_merged = Column(Text, nullable=True)
    issue_wrong_section = Column(Text, nullable=True)
    issue_inaccurate = Column(Text, nullable=True)
    issue_ai_enrichment = Column(Text, nullable=True)
    issue_formatting = Column(Text, nullable=True)
    issue_locations = Column(Text, nullable=True)  # JSON array
    biggest_issue = Column(Text, nullable=True)
    likelihood_to_recommend = Column(Integer, nullable=False)  # 1-5
    submitted_at = Column(DateTime, server_default=func.now())
    run = relationship("Run", back_populates="feedback", lazy="raise_on_sql")
    user = relationship("User", back_populates="feedback", lazy="raise_on_sql")


# ==========================
# Configuration Models
# ==========================
class SystemConfig(Base):
    """Key-value store for system configuration."""
    __tablename__ = "system_config"

    key = Column(String(255), primary_key=True)
    value = Column(Text, nullable=False)  # JSON-encoded
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)

    # Forward-only audit pointer to the admin who last changed this key.
    updated_by_user = relationship("User", lazy="raise_on_sql")


# ==========================
# Pipeline Models
# ==========================
class Run(Base):
    """Pipeline run tracking."""
    __tablename__ = "runs"

    id = Column(String(10), primary_key=True)  # e.g., "A1B2C3"
    filename = Column(String(255), nullable=False)
    # Single source of truth for upload.py's pre-archive length guard (#796):
    # a filename this column can't hold must be rejected before
    # create_run_archive runs, not at INSERT time after the archive is
    # already durable.
    FILENAME_MAX_LENGTH = filename.type.length
    file_type = Column(String(20), nullable=False)  # "docx" or "pdf"
    status = Column(String(20), nullable=False, index=True)  # "running", "complete", "failed", "paused"
    # Statuses a run does not transition past. Single definition (CODING
    # STANDARDS §1.5) -- app.services.notifications imports this.
    TERMINAL_RUN_STATUSES = frozenset({"complete", "failed", "cancelled"})
    started_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    completed_at = Column(DateTime)
    # When this run last entered "queued" via run_service.flip_to_queued
    # (#701). started_at is re-stamped at the worker's claim (queued ->
    # running), so it means "began executing" only; the admin queue view's
    # queued-age reads this column instead. NULL = never queued (in-process
    # dispatch, or a run that predates this column).
    queued_at = Column(DateTime, nullable=True)
    # The step a resumed run should start from, set by retry_step's flip and
    # NULLed by start_run's flip (#701). The Valkey work token is a pure
    # wake-up and no longer carries this -- a redelivered or stale token could
    # otherwise resume an old, already-superseded step. NULL = start from the
    # top.
    resume_from_step = Column(Integer, nullable=True)
    # Authoritative total pipeline execution time, in whole seconds, persisted by
    # the orchestrator when a run reaches a terminal status (it already computes
    # this value and previously only emitted it over the WebSocket). Distinct from
    # wall-clock completed_at - started_at, which can be larger for retried runs
    # (it spans the idle time a run sat failed before retry). NULL for runs that
    # predate this column or never reached a terminal status here.
    total_duration_seconds = Column(Integer)
    # Input-scaled wall-clock estimate (whole seconds) computed from the document
    # at upload, so the client stall watchdog can scale its "taking longer than
    # expected" threshold to the actual CV instead of a fixed constant. NULL for
    # runs created before this column existed (watchdog falls back to a default).
    estimated_duration_seconds = Column(Integer)
    # Number of pipeline execution attempts; 1 = initial run; incremented by
    # auto-retry (issue #145). MySQL-portable Integer; NOT NULL so existing rows
    # backfill to 1 via the server_default.
    attempt_count = Column(Integer, nullable=False, default=1, server_default=text("1"))
    total_cost = Column(Float, default=0.0)
    total_tokens = Column(Integer, default=0)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    # Bedrock prompt-caching split: cache_read = input tokens served from
    # cache (0.1x input rate); cache_write = input tokens written to cache
    # (1.25x input rate). Both are subsets of input_tokens, not additions to
    # it -- input_tokens already includes the cached portion, so don't sum
    # these into totals.
    cache_read_tokens = Column(Integer, default=0)
    cache_write_tokens = Column(Integer, default=0)
    error_message = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    # Auth-related fields
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    submission_type = Column(String(50), nullable=True)
    # CV owner's name as inferred by stage 4 (cv_owner in *_fields.json), kept on
    # the run so the admin runs list can filter and group by faculty member
    # without opening each run's JSON. NULL until stage 4 completes or when no
    # owner was inferred.
    cv_owner_name = Column(String(255), nullable=True, index=True)
    # Denormalised advisory quality score (see quality_score_service.score_columns):
    # the final 0-100 score, its GREEN/YELLOW/RED band, and the hard-fail cap
    # value when one lowered the score. NULL until scored.
    quality_score = Column(Integer, nullable=True, index=True)
    quality_band = Column(String(20), nullable=True)
    quality_cap = Column(Integer, nullable=True)
    show_track_changes = Column(Integer, default=1)
    show_pipeline_comments = Column(Integer, default=0)
    strip_template_instructions = Column(Integer, default=1, server_default="1", nullable=False)
    # Image tag of the process that last moved this run to "running" (#1239):
    # stamped at the worker's claim (and at each resume or retry, so the latest
    # executing image wins), not at upload. NULL for runs that predate the
    # column or when the image was built without a tag.
    image_tag = Column(String(128), nullable=True)
    # Whether the uploaded CV was written in the WCM faculty CV template ("wcm")
    # or another format ("other"), and the count of template signals behind it
    # (app.services.input_format). NULL when undetermined: runs that predate the
    # columns, unreadable text, or a detector failure at upload.
    input_format = Column(String(10), nullable=True, index=True)
    input_format_score = Column(Integer, nullable=True)
    # sha256 (hex) of the uploaded bytes (#1286): the upload endpoint matches it
    # against every run, any submitter, to ask before re-processing a file.
    # NULL for runs that predate the column until the backfill fills it.
    source_sha256 = Column(String(SHA256_HEX_LENGTH), nullable=True, index=True)
    # A PDF's image-only (scanned) pages, 1-based and comma-joined (#1282):
    # their text never reaches the pipeline, so the run page warns. Set at
    # upload; NULL for a docx, a PDF with none, and runs that predate it.
    scanned_pages = Column(Text, nullable=True)

    @property
    def scanned_page_numbers(self) -> list[int]:
        return [int(n) for n in self.scanned_pages.split(",")] if self.scanned_pages else []
    # The batch upload this run belongs to (#1114), NULL for a single upload.
    # A bulk batch (is_bulk_batch: two or more files) also routes the run's
    # queue token to the batch queue (run_queue.queue_for) and suppresses its
    # Teams "started" card; a one-file batch does neither.
    batch_id = Column(String(8), ForeignKey("run_batches.id"), nullable=True, index=True)

    # ORM relationships (see User for the lazy/cascade rationale). user_id is
    # nullable, so run.user can be None for anonymous/simple-mode runs.
    user = relationship("User", back_populates="runs", lazy="raise_on_sql")
    steps = relationship("Step", back_populates="run", lazy="raise_on_sql", passive_deletes=True)
    logs = relationship("Log", back_populates="run", lazy="raise_on_sql", passive_deletes=True)
    llm_usage = relationship("LLMUsage", back_populates="run", lazy="raise_on_sql", passive_deletes=True)
    feedback = relationship("Feedback", back_populates="run", lazy="raise_on_sql", passive_deletes=True)
    metrics = relationship("RunMetrics", back_populates="run", uselist=False, lazy="raise_on_sql", passive_deletes=True)


class BatchSource(StrEnum):
    """``run_batches.source`` (#1298)."""
    WEB = "web"
    EMAIL = "email"


class RunBatch(Base):
    """One batch upload (#1114): a set of runs a user submitted together.

    A table rather than only ``runs.batch_id``: the batch view reports files
    that never became runs (``files_submitted`` minus the run count), and the
    Runs page's Batch filter lists batches with their submitter and time.
    """
    __tablename__ = "run_batches"

    # Letters only, like run ids (#1192), generated by batch_service.
    id = Column(String(8), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    # Valid files the user selected when creating the batch.
    files_submitted = Column(Integer, nullable=False)
    # Where the batch came from (#1298): an "email" batch always gets the
    # completion email.
    source = Column(String(10), nullable=False, default=BatchSource.WEB, server_default=BatchSource.WEB)
    # A web batch whose submitter ticked "Email me when job completes" (#1335)
    # gets it too.
    notify_on_complete = Column(Boolean, nullable=False, default=False, server_default=false())
    # Set once, by a conditional UPDATE, by whichever pod sends the completion email.
    completion_notified_at = Column(DateTime, nullable=True)

    user = relationship("User", lazy="raise_on_sql")


# The fewest files a batch needs to be treated as a bulk upload. A one-file
# batch is a single upload in all but name: ticking "Email me when job
# completes" on a single file creates one so the completion email fires
# (#1340). Its run is routed (run_queue.queue_for), counted
# (batch_service._lane_loads) and announced in Teams (notifications) like a
# single run; only the completion email stays batch-driven.
BULK_BATCH_MIN_FILES = 2


def is_bulk_batch(files_submitted: int | None) -> bool:
    """True for a batch of BULK_BATCH_MIN_FILES or more files; False for a
    one-file batch and for no batch at all (``None``)."""
    return files_submitted is not None and files_submitted >= BULK_BATCH_MIN_FILES


class Step(Base):
    """Individual pipeline step execution."""
    __tablename__ = "steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(10), ForeignKey("runs.id"), nullable=False, index=True)
    step_number = Column(Integer, nullable=False)  # 1-12
    stage_id = Column(String(10), nullable=True)  # e.g., '1a', '1b', '2', '3a', '3b', '4', '4.5', '5', '5b', '5c', '5d', '6'
    step_name = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False)  # "pending", "running", "complete", "error"
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    duration_seconds = Column(Integer)
    cost = Column(Float, default=0.0)
    input_file = Column(String(512))
    output_files = Column(Text)  # JSON array as string
    error_message = Column(Text)
    error_type = Column(String(50), nullable=True)  # llm_timeout, token_limit, parse_error, invalid_response, api_error, file_error, unknown

    run = relationship("Run", back_populates="steps", lazy="raise_on_sql")


class Log(Base):
    """Pipeline execution logs."""
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(10), ForeignKey("runs.id"), nullable=False, index=True)
    step_number = Column(Integer)
    timestamp = Column(DateTime, server_default=func.now())
    level = Column(String(20), default="INFO")  # INFO, WARNING, ERROR
    message = Column(Text, nullable=False)

    run = relationship("Run", back_populates="logs", lazy="raise_on_sql")


class LLMUsage(Base):
    """LLM API usage tracking."""
    __tablename__ = "llm_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(10), ForeignKey("runs.id"), nullable=False, index=True)
    step_number = Column(Integer, nullable=False)
    model = Column(String(100), nullable=False)  # "gpt-4o-mini", etc.
    prompt_tokens = Column(Integer, nullable=False)
    completion_tokens = Column(Integer, nullable=False)
    total_tokens = Column(Integer, nullable=False)
    cost = Column(Float, nullable=False)
    latency_ms = Column(Integer)
    timestamp = Column(DateTime, server_default=func.now())
    model_version = Column(String(100), nullable=True)  # Full version e.g. gpt-4o-2024-08-06
    temperature = Column(Float, nullable=True)
    finish_reason = Column(String(50), nullable=True)  # stop, length, content_filter
    retry_count = Column(Integer, default=0)
    prompt_version = Column(String(64), nullable=True)  # SHA-256 hash of prompt template
    provider = Column(String(50), server_default="openai", nullable=True)  # LLM provider: "openai", "bedrock", etc.

    run = relationship("Run", back_populates="llm_usage", lazy="raise_on_sql")


class RunMetrics(Base):
    """Aggregate metrics for a pipeline run."""
    __tablename__ = "run_metrics"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(10), ForeignKey("runs.id"), unique=True, nullable=False, index=True)
    word_count = Column(Integer, nullable=True)
    char_count = Column(Integer, nullable=True)
    publication_count = Column(Integer, nullable=True)
    sections_populated = Column(Integer, nullable=True)
    sections_total = Column(Integer, default=71)
    language = Column(String(10), nullable=True)
    computed_at = Column(DateTime, server_default=func.now())

    run = relationship("Run", back_populates="metrics", lazy="raise_on_sql")


class InboundMessageStatus(StrEnum):
    """``inbound_messages.status`` (#1298)."""
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    # Read from S3 but not yet decided: the poller crashed or failed mid-message.
    FAILED = "failed"


class InboundRejectReason(StrEnum):
    """Why a whole message was dropped (``inbound_messages.reject_reason``).

    Never carries a filename, subject or address (CODING_STANDARDS 4.7)."""
    UNPARSEABLE = "unparseable"
    NOT_AUTHENTICATED = "not_authenticated"
    SPAM_OR_VIRUS = "spam_or_virus"
    SENDER_DOMAIN = "sender_domain"
    UNKNOWN_USER = "unknown_user"
    NEVER_CONSENTED = "never_consented"
    USER_DISABLED = "user_disabled"
    NOT_IN_ACCESS_GROUP = "not_in_access_group"
    NO_VALID_ATTACHMENTS = "no_valid_attachments"
    TOO_MANY_FILES = "too_many_files"
    INBOX_FULL = "inbox_full"
    PROCESSING_ERROR = "processing_error"


class InboundFileStatus(StrEnum):
    """``inbound_files.status`` (#1298)."""
    PENDING = "pending"
    SUBMITTED = "submitted"
    DISCARDED = "discarded"
    EXPIRED = "expired"


class InboundMessage(Base):
    """One raw message SES wrote under ``inbound/`` (#1298). Unique on the S3
    key so a re-poll never reads the same message twice."""
    __tablename__ = "inbound_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    s3_key = Column(String(512), nullable=False, unique=True)
    message_id = Column(String(255), nullable=True)
    from_addr = Column(String(255), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    received_at = Column(DateTime, nullable=False, server_default=func.now())
    status = Column(String(20), nullable=False)
    reject_reason = Column(String(120), nullable=True)
    file_count = Column(Integer, nullable=False, default=0)


class InboundFile(Base):
    """One CV held in a user's inbox, waiting for them to submit it (#1298)."""
    __tablename__ = "inbound_files"

    id = Column(Integer, primary_key=True, autoincrement=True)
    inbound_message_id = Column(Integer, ForeignKey("inbound_messages.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    filename = Column(String(255), nullable=False)
    size_bytes = Column(Integer, nullable=False)
    sha256 = Column(String(64), nullable=False)
    # Global storage prefix holding the bytes; deleted on discard/expiry.
    storage_key = Column(String(255), nullable=False)
    status = Column(String(20), nullable=False, default=InboundFileStatus.PENDING)
    run_id = Column(String(10), ForeignKey("runs.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
