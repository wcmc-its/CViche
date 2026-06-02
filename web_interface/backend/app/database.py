"""Database configuration and session management."""
import os
import boto3
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.base_class import Base
from app.database_factory import create_cviche_engine
from app.config_loader import get_config

# Read DATABASE_URL from environment; default to SQLite for local dev.
# The default is an absolute path anchored at the backend directory so it is
# stable regardless of the process working directory (the pipeline
# orchestrator pins cwd to the repo root while runs execute).

db_user, source = get_config("db", "DB_USER", default="")

engine = create_cviche_engine(override_user=db_user)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
   

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
