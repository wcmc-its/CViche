"""Tests for resilient per-request user writes in auth.get_current_user.

A page load fires several concurrent API calls, each running get_current_user
and writing the same `users` row (last_active_at / role sync). On some MySQL
configs the racing writers raise OperationalError 1020, which previously
surfaced as 500s. _best_effort_persist swallows that specific conflict
instead -- through its own short-lived session, isolated from the request's
shared `db` session (#656 review response, 2026-08-19).
"""
import pymysql.err
import pytest
from sqlalchemy.exc import OperationalError

from app.auth import _best_effort_persist, _is_retryable_write_conflict
from app.models import User


def _make_user(db, email="user@example.com", role="user"):
    user = User(email=email, display_name="User", role=role, status="active",
                auth_method="simple")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_best_effort_persist_success_writes_through_its_own_session(client, db):
    # `client` fixture (unused otherwise) patches app.database.SessionLocal to
    # the test DB for the duration of this test -- see its docstring.
    user = _make_user(db)
    assert _best_effort_persist(user.id, "role sync", role="admin") is True

    # Re-query via a *different* session than the one used to persist, to
    # prove the write actually landed rather than sitting uncommitted.
    db.expire_all()
    refreshed = db.query(User).filter(User.id == user.id).first()
    assert refreshed.role == "admin"


def test_best_effort_persist_does_not_touch_unrelated_dirty_state(client, db):
    """The request's shared `db` session can have unrelated staged changes
    (a different route's work-in-progress); _best_effort_persist must not
    commit them as a side effect of its own unrelated write."""
    user = _make_user(db)
    other = _make_user(db, email="other@example.com")

    # Stage an uncommitted change on the *shared* session.
    other.display_name = "Not Yet Saved"

    assert _best_effort_persist(user.id, "role sync", role="admin") is True

    # The staged, uncommitted change on `db` must still be uncommitted --
    # a fresh session must not see it.
    from app.database import SessionLocal
    fresh = SessionLocal()
    try:
        seen = fresh.query(User).filter(User.id == other.id).first()
        assert seen.display_name != "Not Yet Saved"
    finally:
        fresh.close()


# ---------------------------------------------------------------------------
# _is_retryable_write_conflict: real per-backend exception shapes, not
# string-matched or hand-mocked -- pymysql's is a real (code, message) tuple;
# sqlite3's is a plain message. Verified against the actual installed
# libraries, not asserted from memory.
# ---------------------------------------------------------------------------

def _operational_error(orig: Exception) -> OperationalError:
    return OperationalError("UPDATE users SET ...", {}, orig)


def test_mysql_1020_conflict_is_retryable():
    exc = _operational_error(
        pymysql.err.OperationalError(1020, "Record has changed since last read in table 'users'")
    )
    assert _is_retryable_write_conflict(exc) is True


def test_sqlite_database_locked_is_retryable():
    import sqlite3
    exc = _operational_error(sqlite3.OperationalError("database is locked"))
    assert _is_retryable_write_conflict(exc) is True


def test_unrelated_operational_error_is_not_retryable():
    """A real outage (connection lost, etc.) must NOT be classified the same
    as the benign concurrent-write conflict -- swallowing it would hide a
    genuine infrastructure failure the same way #489 did for a different
    bare `except Exception: pass` (docs/CODING_STANDARDS.md §5.4)."""
    exc = _operational_error(
        pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query")
    )
    assert _is_retryable_write_conflict(exc) is False


def test_best_effort_persist_reraises_unrelated_operational_error(db, monkeypatch):
    user = _make_user(db)

    class _ExplodingSession:
        def query(self, *a, **k):
            return self

        def filter(self, *a, **k):
            return self

        def update(self, *a, **k):
            pass

        def commit(self):
            raise _operational_error(
                pymysql.err.OperationalError(2013, "Lost connection to MySQL server during query")
            )

        def rollback(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr("app.database.SessionLocal", lambda: _ExplodingSession())
    with pytest.raises(OperationalError):
        _best_effort_persist(user.id, "role sync", role="admin")


def test_bump_last_active_does_not_dirty_the_request_session(client, db):
    """The bump is persisted through _best_effort_persist's own session; the
    request session's User instance must NOT be left dirty, or the route's
    later db.commit() re-issues the same UPDATE from a snapshot older than
    that side commit and MariaDB (innodb_snapshot_isolation=ON) rejects it
    with 1020 -- the 2026-09-03 prod regression on POST /run/{id}/feedback."""
    from datetime import datetime, timedelta

    from sqlalchemy import inspect

    from app.auth import _bump_last_active

    user = _make_user(db)
    user.last_active_at = datetime.now() - timedelta(minutes=5)
    db.commit()

    _bump_last_active(user)

    assert user not in db.dirty
    assert not inspect(user).modified
    assert (datetime.now() - user.last_active_at).total_seconds() < 5
    # and the write really landed, via the side session
    db.expire_all()
    assert (datetime.now() - db.query(User).get(user.id).last_active_at).total_seconds() < 5
