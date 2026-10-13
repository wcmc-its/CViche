"""app/services/corrected_copy_service.py: the corrected_copies rows behind the
runs list's "Corrected" line and filter. Every name here is synthetic."""
from datetime import UTC, datetime

import pytest
from sqlalchemy import event
from sqlalchemy.exc import IntegrityError

from app.models import CorrectedCopy, Run, User
from app.services import corrected_copy_service as svc
from tests.conftest import TestingSessionLocal, engine

_EARLY = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
_LATE = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)


def _user(db, name, role="user"):
    user = User(email=f"{name.split()[0].lower()}@example.com", display_name=name, role=role)
    db.add(user)
    db.commit()
    return user


def _run(db, run_id, owner):
    db.add(Run(id=run_id, filename="cv.docx", file_type="docx", status="complete", user_id=owner.id))
    db.commit()


def _key(run_id, user_id):
    return f"corrected/{run_id}_u{user_id}_corrected.docx"


def test_record_copy_replaces_the_same_uploaders_row_and_keeps_anothers(db):
    owner, staff = _user(db, "Pat Owner"), _user(db, "Sam Staff", "staff")
    _run(db, "CC0001", owner)
    svc.record_copy(db, "CC0001", owner.id, changes=3, storage_key=_key("CC0001", owner.id), uploaded_at=_EARLY)
    svc.record_copy(db, "CC0001", staff.id, changes=5, storage_key=_key("CC0001", staff.id), uploaded_at=_EARLY)
    svc.record_copy(db, "CC0001", owner.id, changes=1, storage_key=_key("CC0001", owner.id), uploaded_at=_LATE)

    rows = {r.user_id: (r.changes, r.uploaded_at) for r in db.query(CorrectedCopy).all()}
    assert rows == {owner.id: (1, _LATE), staff.id: (5, _EARLY)}


def test_record_copy_matches_an_unknown_uploaders_row_by_its_key(db):
    """A copy stored before #1754 has no uploader; recording it twice keeps one row."""
    _run(db, "CC0002", _user(db, "Pat Owner"))
    for changes in (2, 4):
        svc.record_copy(db, "CC0002", None, changes=changes, storage_key="corrected/CC0002_corrected.docx",
                        uploaded_at=_EARLY)
    assert [(r.user_id, r.changes) for r in db.query(CorrectedCopy).all()] == [(None, 4)]


def test_record_copy_updates_the_row_a_concurrent_upload_inserted_first(db, monkeypatch):
    """The same uploader's other upload commits the row between this one's
    read and commit: the unique constraint refuses the insert, and this
    upload reads the row back and updates it."""
    owner = _user(db, "Pat Owner")
    _run(db, "CC0003", owner)
    real_commit, raced = db.commit, []

    def racing_commit():
        if not raced:
            raced.append(True)
            db.rollback()
            other = TestingSessionLocal()
            other.add(CorrectedCopy(run_id="CC0003", user_id=owner.id, changes=9,
                                    storage_key=_key("CC0003", owner.id), uploaded_at=_EARLY))
            other.commit()
            other.close()
            raise IntegrityError("INSERT INTO corrected_copies", {}, Exception("duplicate entry"))
        real_commit()

    monkeypatch.setattr(db, "commit", racing_commit)
    svc.record_copy(db, "CC0003", owner.id, changes=2, storage_key=_key("CC0003", owner.id), uploaded_at=_LATE)

    assert [(r.changes, r.uploaded_at) for r in db.query(CorrectedCopy).all()] == [(2, _LATE)]


def test_record_copy_raises_when_the_retry_conflicts_too(db, monkeypatch):
    owner = _user(db, "Pat Owner")
    _run(db, "CC0004", owner)

    def always_conflicts():
        raise IntegrityError("INSERT INTO corrected_copies", {}, Exception("duplicate entry"))

    monkeypatch.setattr(db, "commit", always_conflicts)
    with pytest.raises(IntegrityError):
        svc.record_copy(db, "CC0004", owner.id, changes=1, storage_key=_key("CC0004", owner.id),
                        uploaded_at=_LATE)


def test_load_copies_names_each_uploader_and_role_newest_first(db):
    owner, staff, admin = _user(db, "Pat Owner"), _user(db, "Sam Staff", "staff"), _user(db, "Ari Admin", "admin")
    _run(db, "CC0005", owner)
    _run(db, "CC0006", owner)
    svc.record_copy(db, "CC0005", owner.id, changes=1, storage_key=_key("CC0005", owner.id), uploaded_at=_EARLY)
    svc.record_copy(db, "CC0005", staff.id, changes=2, storage_key=_key("CC0005", staff.id), uploaded_at=_LATE)
    svc.record_copy(db, "CC0005", admin.id, changes=3, storage_key=_key("CC0005", admin.id),
                    uploaded_at=datetime(2026, 10, 12, 12, 0, tzinfo=UTC))
    svc.record_copy(db, "CC0005", None, changes=4, storage_key="corrected/CC0005_corrected.docx",
                    uploaded_at=datetime(2026, 10, 1, tzinfo=UTC))

    copies = svc.load_copies(db, ["CC0005", "CC0006"])

    assert "CC0006" not in copies  # no copy, no entry
    assert [(c.uploader, c.role, c.changes) for c in copies["CC0005"]] == [
        ("Sam Staff", "staff", 2), ("Ari Admin", "admin", 3), ("Pat Owner", svc.OWNER_ROLE, 1), (None, None, 4)]


def test_load_copies_is_one_query_for_a_whole_page(db):
    owner = _user(db, "Pat Owner")
    run_ids = [f"CC01{n:02d}" for n in range(5)]
    for run_id in run_ids:
        _run(db, run_id, owner)
        svc.record_copy(db, run_id, owner.id, changes=1, storage_key=_key(run_id, owner.id), uploaded_at=_EARLY)
    statements = []

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        copies = svc.load_copies(db, run_ids)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(copies) == 5
    assert len([s for s in statements if s.lstrip().upper().startswith("SELECT")]) == 1


def test_load_copies_of_no_runs_queries_nothing(db):
    assert svc.load_copies(db, []) == {}
