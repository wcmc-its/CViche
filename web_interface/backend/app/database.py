"""Database configuration and session management."""
import logging
import os
from pathlib import Path

import boto3
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import Session, sessionmaker

from app.base_class import Base
from app.config_loader import get_config
from app.database_factory import create_cviche_engine

logger = logging.getLogger(__name__)

# MariaDB/MySQL ER_CHECKREAD, "Record has changed since last read": with
# innodb_snapshot_isolation=ON (prod, MariaDB 11.8) a write against a row
# version newer than the transaction's read snapshot (#751, #1285).
MYSQL_RECORD_CHANGED_ERRNO = 1020
# SQLite's (the test database's) report of the same kind of write contention.
_SQLITE_DATABASE_LOCKED = "database is locked"

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
    from app.models import (  # noqa: F401
        Consent,
        Feedback,
        LLMUsage,
        Log,
        Run,
        RunMetrics,
        Step,
        SystemConfig,
        User,
    )
    Base.metadata.create_all(bind=engine)


def is_retryable_write_conflict(exc: OperationalError) -> bool:
    """Whether exc is a concurrent-write conflict a fresh transaction can
    cure, not any OperationalError.

    MySQL (prod): pymysql raises error 1020 ("Record has changed since last
    read") as a 2-tuple (code, message) in .orig.args.
    SQLite (tests): sqlite3 raises "database is locked" as a plain message,
    no error code.
    Anything else -- connection loss, a real outage -- is a different
    problem and must not be retried or swallowed the same way.
    """
    orig_args = getattr(exc.orig, "args", ())
    if orig_args and orig_args[0] == MYSQL_RECORD_CHANGED_ERRNO:
        return True
    return _SQLITE_DATABASE_LOCKED in str(exc).lower()


def commit_inserts_retrying_conflict(db: Session) -> None:
    """Commit db's staged new rows, retrying once in a fresh transaction when
    the commit hits a retryable write conflict (#1285).

    A failed commit's rollback ends the stale read snapshot and expunges the
    staged rows, so they are re-added and committed again. That replay is
    only sound for a transaction that does nothing but INSERT new rows: with
    any modified or deleted row staged, or on any other error, the original
    exception propagates and the caller handles it (and rolls back).
    """
    staged = list(db.new)
    inserts_only = not db.dirty and not db.deleted
    try:
        db.commit()
    except OperationalError as e:
        if not (inserts_only and is_retryable_write_conflict(e)):
            raise
        db.rollback()
        logger.warning("Commit hit a write conflict; retrying once in a fresh transaction: %s", e)
        db.add_all(staged)
        db.commit()
