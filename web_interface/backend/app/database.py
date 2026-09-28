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

# Read DB_HOST/DB_PORT/DB_NAME/DB_USER via get_config (env, then the
# auth_config.yaml yaml fallback). There is no SQLite path: create_cviche_engine
# raises if any of these four is missing, in local dev or production alike.

db_user, source = get_config("db", "DB_USER", default="")

# 1. Extract routing parameters using in configuration loader
db_host, _ = get_config("db", "DB_HOST", default="")
db_port, _ = get_config("db", "DB_PORT", default="")
db_name, _ = get_config("db", "DB_NAME", default="")
migrate_user, _ = get_config("db", "DB_USER", default="")

# 2. Pass them directly to the factory
engine = create_cviche_engine(
        db_host=db_host,
        db_port=db_port,
        db_name=db_name,
        db_user=migrate_user
    )

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
