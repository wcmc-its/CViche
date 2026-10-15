"""The corrected_copies rows: which reviewers' corrected copies of a run are
stored, for the runs list (its "Corrected" line and filter) and the run page's
list of copies.

review_loop_service stores the copy itself, one per run and uploader (#1754),
and records its row here. The rows exist so the runs list never lists storage
per run. Copies stored before the table were backfilled once.
"""
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import CorrectedCopy, Run, User

#: The uploader role shown for the run's own submitter, whatever their users.role.
OWNER_ROLE = "owner"


@dataclass(frozen=True)
class CopyRecord:
    """One stored corrected copy and who uploaded it. ``uploader`` and
    ``role`` are None for a copy whose uploader is unknown (stored before
    #1754); ``role`` is OWNER_ROLE for the run's submitter, else their
    users.role ("staff" or "admin")."""
    id: int
    run_id: str
    user_id: int | None
    uploader: str | None
    role: str | None
    uploaded_at: datetime
    changes: int
    storage_key: str


def record_copy(db: Session, run_id: str, user_id: int | None, *, changes: int,
                storage_key: str, uploaded_at: datetime) -> None:
    """Insert the uploader's row for the run, or replace it: one row per run
    and uploader, as storage keeps one copy per run and uploader. Commits.
    A NULL user (a copy stored before #1754) is matched by its storage key,
    since a unique constraint never matches NULLs."""
    for attempt in range(2):
        query = db.query(CorrectedCopy).filter(CorrectedCopy.run_id == run_id)
        query = query.filter(CorrectedCopy.storage_key == storage_key if user_id is None
                             else CorrectedCopy.user_id == user_id)
        row = query.one_or_none()
        if row is None:
            row = CorrectedCopy(run_id=run_id, user_id=user_id)
            db.add(row)
        row.changes, row.storage_key, row.uploaded_at = changes, storage_key, uploaded_at
        try:
            db.commit()
            return
        except IntegrityError:
            # The same uploader's concurrent upload inserted the row between
            # the read and the commit: read it back and update it, once.
            db.rollback()
            if attempt:
                raise


def has_corrected_copy() -> ColumnElement[bool]:
    """Correlated EXISTS: this run has at least one corrected copy."""
    return select(CorrectedCopy.id).where(CorrectedCopy.run_id == Run.id).exists()


def load_copies(db: Session, run_ids: list[str]) -> dict[str, list[CopyRecord]]:
    """Every corrected copy of the given runs, newest first per run, in one
    query. Runs without a copy are absent from the result."""
    if not run_ids:
        return {}
    rows = (
        db.query(CorrectedCopy, User.display_name, User.role, Run.user_id)
        .join(Run, Run.id == CorrectedCopy.run_id)
        .outerjoin(User, User.id == CorrectedCopy.user_id)
        .filter(CorrectedCopy.run_id.in_(run_ids))
        .order_by(CorrectedCopy.uploaded_at.desc(), CorrectedCopy.id.desc())
        .all()
    )
    by_run: dict[str, list[CopyRecord]] = {}
    for copy, display_name, user_role, owner_id in rows:
        known = copy.user_id is not None and display_name is not None
        role = (OWNER_ROLE if copy.user_id == owner_id else user_role) if known else None
        by_run.setdefault(copy.run_id, []).append(CopyRecord(
            id=copy.id, run_id=copy.run_id, user_id=copy.user_id,
            uploader=display_name if known else None, role=role,
            uploaded_at=copy.uploaded_at, changes=copy.changes, storage_key=copy.storage_key))
    return by_run
