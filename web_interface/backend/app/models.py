"""SQLAlchemy database models."""
from sqlalchemy import Column, String, Integer, Float, Text, DateTime, ForeignKey, UniqueConstraint, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.base_class import Base

# ==========================
# Authentication Models
# ==========================
class User(Base):
    """User accounts for authentication and authorization."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    display_name = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="user")  # "user" or "admin"
    status = Column(String(20), nullable=False, default="active")  # "active" or "disabled"
    daily_limit = Column(Integer, nullable=True)
    monthly_limit = Column(Integer, nullable=True)
    default_submission_type = Column(String(50), nullable=True)
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
    issue_missing_content = Column(String(20), nullable=True)
    issue_split_merged = Column(String(20), nullable=True)
    issue_wrong_section = Column(String(20), nullable=True)
    issue_inaccurate = Column(String(20), nullable=True)
    issue_ai_enrichment = Column(String(20), nullable=True)
    issue_formatting = Column(String(20), nullable=True)
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
    file_type = Column(String(20), nullable=False)  # "docx" or "pdf"
    status = Column(String(20), nullable=False, index=True)  # "running", "complete", "failed", "paused"
    started_at = Column(DateTime, nullable=False, server_default=func.now(), index=True)
    completed_at = Column(DateTime)
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
    show_track_changes = Column(Integer, default=1)
    show_pipeline_comments = Column(Integer, default=0)
    strip_template_instructions = Column(Integer, default=1, server_default="1", nullable=False)

    # ORM relationships (see User for the lazy/cascade rationale). user_id is
    # nullable, so run.user can be None for anonymous/simple-mode runs.
    user = relationship("User", back_populates="runs", lazy="raise_on_sql")
    steps = relationship("Step", back_populates="run", lazy="raise_on_sql", passive_deletes=True)
    logs = relationship("Log", back_populates="run", lazy="raise_on_sql", passive_deletes=True)
    llm_usage = relationship("LLMUsage", back_populates="run", lazy="raise_on_sql", passive_deletes=True)
    feedback = relationship("Feedback", back_populates="run", lazy="raise_on_sql", passive_deletes=True)
    metrics = relationship("RunMetrics", back_populates="run", uselist=False, lazy="raise_on_sql", passive_deletes=True)


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
