"""SQLAlchemy database models."""
from sqlalchemy import Column, String, Integer, Float, Text, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.sql import func
from app.database import Base


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


class SystemConfig(Base):
    """Key-value store for system configuration."""
    __tablename__ = "system_config"

    key = Column(String(255), primary_key=True)
    value = Column(Text, nullable=False)  # JSON-encoded
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)


class Run(Base):
    """Pipeline run tracking."""
    __tablename__ = "runs"

    id = Column(String, primary_key=True)  # e.g., "A1B2C3"
    filename = Column(String, nullable=False)
    file_type = Column(String, nullable=False)  # "docx" or "pdf"
    status = Column(String, nullable=False)  # "running", "complete", "failed", "paused"
    started_at = Column(DateTime, nullable=False, server_default=func.now())
    completed_at = Column(DateTime)
    total_cost = Column(Float, default=0.0)
    total_tokens = Column(Integer, default=0)
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    error_message = Column(Text)
    created_at = Column(DateTime, server_default=func.now())
    # Auth-related fields
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    submission_type = Column(String(50), nullable=True)
    show_track_changes = Column(Integer, default=1)
    show_pipeline_comments = Column(Integer, default=0)


class Step(Base):
    """Individual pipeline step execution."""
    __tablename__ = "steps"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, ForeignKey("runs.id"), nullable=False)
    step_number = Column(Integer, nullable=False)  # 1-12
    stage_id = Column(String, nullable=True)  # e.g., '1a', '1b', '2', '3a', '3b', '4', '4.5', '5', '5b', '5c', '5d', '6'
    step_name = Column(String, nullable=False)
    status = Column(String, nullable=False)  # "pending", "running", "complete", "error"
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    duration_seconds = Column(Integer)
    cost = Column(Float, default=0.0)
    input_file = Column(String)
    output_files = Column(Text)  # JSON array as string
    error_message = Column(Text)
    error_type = Column(String(50), nullable=True)  # llm_timeout, token_limit, parse_error, invalid_response, api_error, file_error, unknown


class Log(Base):
    """Pipeline execution logs."""
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, ForeignKey("runs.id"), nullable=False)
    step_number = Column(Integer)
    timestamp = Column(DateTime, server_default=func.now())
    level = Column(String, default="INFO")  # INFO, WARNING, ERROR
    message = Column(Text, nullable=False)


class LLMUsage(Base):
    """LLM API usage tracking."""
    __tablename__ = "llm_usage"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String, ForeignKey("runs.id"), nullable=False)
    step_number = Column(Integer, nullable=False)
    model = Column(String, nullable=False)  # "gpt-4o-mini", etc.
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
