"""commit_inserts_retrying_conflict (#1285): a commit of fresh INSERTs that
prod's MariaDB rejects with 1020 ("Record has changed since last read") gets
exactly one retry in a fresh transaction; anything else propagates untouched.
The 1020 is raised from the real flush by conftest's write_conflict_on_insert.
Names are invented."""
import pymysql
import pytest
from sqlalchemy.exc import OperationalError

from app.database import commit_inserts_retrying_conflict
from app.models import RunBatch, User


def _user(db, email="ana@example.com"):
    user = User(email=email, display_name="Ana", role="user")
    db.add(user)
    db.commit()
    return user


def test_a_one_off_1020_on_an_insert_is_retried_and_the_row_lands(db, write_conflict_on_insert):
    owner = _user(db)
    attempts = write_conflict_on_insert("run_batches")
    db.add(RunBatch(id="RETRYA", user_id=owner.id, files_submitted=2))

    commit_inserts_retrying_conflict(db)

    assert len(attempts) == 2
    db.expire_all()
    assert db.get(RunBatch, "RETRYA").files_submitted == 2


def test_a_second_1020_in_a_row_propagates(db, write_conflict_on_insert):
    owner = _user(db)
    attempts = write_conflict_on_insert("run_batches", failures=2)
    db.add(RunBatch(id="RETRYB", user_id=owner.id, files_submitted=2))

    with pytest.raises(OperationalError) as raised:
        commit_inserts_retrying_conflict(db)

    assert raised.value.orig.args[0] == 1020
    assert len(attempts) == 2


def test_an_unrelated_operational_error_is_not_retried(db, monkeypatch):
    """A lost connection is a real outage, not a conflict: no second commit."""
    _user(db)
    lost = OperationalError("INSERT ...", {}, pymysql.err.OperationalError(2013, "Lost connection"))
    commits = []

    def failing_commit():
        commits.append(1)
        raise lost
    monkeypatch.setattr(db, "commit", failing_commit)

    with pytest.raises(OperationalError) as raised:
        commit_inserts_retrying_conflict(db)

    assert raised.value is lost
    assert len(commits) == 1


def test_a_1020_with_a_modified_row_staged_is_not_replayed(db, write_conflict_on_insert):
    """The rollback would discard the UPDATE; re-adding only the new rows
    would commit half the transaction, so the conflict propagates instead."""
    owner = _user(db)
    attempts = write_conflict_on_insert("run_batches")
    owner.display_name = "Ana B"
    db.add(RunBatch(id="RETRYC", user_id=owner.id, files_submitted=2))

    with pytest.raises(OperationalError):
        commit_inserts_retrying_conflict(db)

    assert len(attempts) == 1
    db.rollback()
    assert db.get(RunBatch, "RETRYC") is None
