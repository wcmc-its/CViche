"""Tests for resilient per-request user writes in auth.get_current_user.

A page load fires several concurrent API calls, each running get_current_user
and writing the same `users` row (last_active_at / role sync). On some MySQL
configs the racing writers raise OperationalError 1020, which previously
surfaced as 500s. _best_effort_commit swallows that conflict instead.
"""
from unittest.mock import MagicMock

from sqlalchemy.exc import OperationalError

from app.auth import _best_effort_commit


def test_best_effort_commit_success_returns_true():
    db = MagicMock()
    assert _best_effort_commit(db, "last_active_at") is True
    db.commit.assert_called_once()
    db.rollback.assert_not_called()


def test_best_effort_commit_swallows_operational_error_and_rolls_back():
    db = MagicMock()
    db.commit.side_effect = OperationalError(
        "UPDATE users SET last_active_at=...",
        {},
        Exception('(1020, "Record has changed since last read in table \'users\'")'),
    )
    # Must NOT raise -- the request keeps serving.
    assert _best_effort_commit(db, "last_active_at") is False
    db.commit.assert_called_once()
    db.rollback.assert_called_once()
