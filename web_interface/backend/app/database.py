"""Database configuration and session management."""
import os
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Read DATABASE_URL from environment; default to SQLite for local dev.
# The default is an absolute path anchored at the backend directory so it is
# stable regardless of the process working directory (the pipeline
# orchestrator pins cwd to the repo root while runs execute).
DATABASE_URL = os.environ.get("CVICHE_DATABASE_URL", "")
if not DATABASE_URL:
    _default_db = Path(__file__).resolve().parent.parent / "cviche_dev.db"
    DATABASE_URL = f"sqlite:///{_default_db}"

# Configure engine kwargs based on database backend
engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    # SQLite requires this for multithreaded FastAPI usage
    engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    # MariaDB/MySQL: verify connections are still alive before using them
    engine_kwargs["pool_pre_ping"] = True

# Create engine
engine = create_engine(DATABASE_URL, **engine_kwargs)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for models
Base = declarative_base()


def get_db():
    """Dependency for getting database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database tables."""
    from app.models import Run, Step, Log, LLMUsage, RunMetrics, User, SystemConfig, Consent, Feedback  # noqa: F401
    Base.metadata.create_all(bind=engine)
