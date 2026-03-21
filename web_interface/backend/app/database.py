"""Database configuration and session management."""
import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Read DATABASE_URL from environment; default to SQLite for local dev
DATABASE_URL = os.environ.get(
    "CVICHE_DATABASE_URL",
    "sqlite:///./cviche_dev.db"
)

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
