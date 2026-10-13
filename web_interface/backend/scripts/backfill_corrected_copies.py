#!/usr/bin/env python3
"""Backfill corrected_copies rows for corrected copies stored before the table.

The runs list reads the corrected_copies table, which the upload writes from
its migration on. A copy stored before that has no row. For every run, list
its corrected/ keys through the storage layer, under both key schemes
(review_loop_service.stored_uploaders): corrected/<run>_u<user id>_* (one copy
per uploader, #1754) and corrected/<run>_* (before #1754, uploader unknown:
user_id NULL, shown as an unknown uploader). Each copy without a row gets one:
its change count from its stored diff, its date from the storage object's
last-modified time. A copy that already has a row is left alone, so a second
run changes nothing. Dry-run by default, printing one line per copy; pass
--apply to write.

    cd web_interface/backend
    python3 scripts/backfill_corrected_copies.py            # dry run
    python3 scripts/backfill_corrected_copies.py --apply

Run it where the app's DB and storage settings are configured (a prod/dev pod).
"""
import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session  # noqa: E402

from app.models import CorrectedCopy, Run, User  # noqa: E402
from app.services.corrected_copy_service import record_copy  # noqa: E402
from app.services.review_loop_service import (  # noqa: E402
    corrected_diff_key,
    corrected_docx_key,
    stored_uploaders,
)
from app.storage import get_storage  # noqa: E402

logger = logging.getLogger("backfill_corrected_copies")


@dataclass
class BackfillSummary:
    runs: int = 0
    copies: int = 0
    existing: int = 0
    unknown_uploader: int = 0
    failed: int = 0
    written: int = 0


def _changes(run_id: str, uploader_id: int | None) -> int:
    """How many changes the copy's stored diff lists."""
    report = json.loads(get_storage().get_file(run_id, corrected_diff_key(run_id, uploader_id)))
    return len(report["changes"])


def _backfill_copy(db: Session, run_id: str, uploader_id: int | None, apply: bool,
                   summary: BackfillSummary, out: TextIO) -> None:
    """Report, and when ``apply`` write, the row of one stored copy that has none."""
    key = corrected_docx_key(run_id, uploader_id)
    if db.query(CorrectedCopy.id).filter(CorrectedCopy.storage_key == key).first():
        summary.existing += 1
        return
    if uploader_id is not None and db.get(User, uploader_id) is None:
        raise LookupError(f"uploader {uploader_id} is not a user")
    changes = _changes(run_id, uploader_id)
    uploaded_at = get_storage().last_modified(run_id, key)
    summary.unknown_uploader += uploader_id is None
    who = "unknown uploader" if uploader_id is None else f"user {uploader_id}"
    out.write(f"{run_id} {who} {changes} changes {uploaded_at.isoformat()}\n")
    if apply:
        record_copy(db, run_id, uploader_id, changes=changes, storage_key=key, uploaded_at=uploaded_at)
        summary.written += 1


def backfill(db: Session, apply: bool, out: TextIO = sys.stdout) -> BackfillSummary:
    """Add (when ``apply``) a corrected_copies row for every stored copy without one."""
    summary = BackfillSummary()
    for (run_id,) in db.query(Run.id).order_by(Run.id).all():
        summary.runs += 1
        for uploader_id in stored_uploaders(run_id):
            summary.copies += 1
            try:
                _backfill_copy(db, run_id, uploader_id, apply, summary, out)
            except Exception:  # noqa: BLE001 -- one bad copy is counted and logged; the rest still run
                db.rollback()
                logger.warning("Could not backfill run %s uploader %s", run_id, uploader_id, exc_info=True)
                summary.failed += 1
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the rows (default: dry run)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        summary = backfill(db, apply=args.apply)
    finally:
        db.close()
    mode = "APPLIED" if args.apply else "DRY RUN (pass --apply to write)"
    sys.stdout.write(
        f"{mode}: runs={summary.runs} copies={summary.copies} existing={summary.existing} "
        f"unknown_uploader={summary.unknown_uploader} failed={summary.failed} written={summary.written}\n")
    return 1 if summary.failed else 0


if __name__ == "__main__":
    sys.exit(main())
