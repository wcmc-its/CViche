#!/usr/bin/env python3
"""Backfill runs.cv_owner_name for runs that completed stage 4 before the
column existed (or whose live write failed).

For every run with cv_owner_name NULL and a completed stage-4 step, resolve the
stage's *_fields.json through the same storage layer the run view uses (the
pod's local files, then S3) and store cv_owner.full_name (falling back to
"first_name last_name"). Dry-run by default; pass --apply to write.

    cd web_interface/backend
    python3 scripts/backfill_cv_owner_name.py            # dry run, prints counts
    python3 scripts/backfill_cv_owner_name.py --apply

Run it where the app's DB and storage settings are configured (a prod/dev pod).
"""
import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session  # noqa: E402

from app.models import Run, Step  # noqa: E402
from app.services.cv_owner_service import read_cv_owner_name  # noqa: E402

logger = logging.getLogger("backfill_cv_owner_name")

STAGE_4_ID = "4"
STEP_COMPLETE = "complete"


@dataclass
class BackfillSummary:
    candidates: int = 0
    resolved: int = 0
    no_owner: int = 0
    failed: int = 0
    written: int = 0


def backfill(db: Session, apply: bool) -> BackfillSummary:
    """Resolve (and, when ``apply``, store) the owner name of every candidate run."""
    summary = BackfillSummary()
    rows = (
        db.query(Run.id, Step.output_files)
        .join(Step, Step.run_id == Run.id)
        .filter(Run.cv_owner_name.is_(None),
                Step.stage_id == STAGE_4_ID,
                Step.status == STEP_COMPLETE)
        .all()
    )
    for run_id, output_files in rows:
        summary.candidates += 1
        try:
            name = read_cv_owner_name(db, run_id, output_files)
        except Exception:
            logger.warning("Could not read stage-4 fields for run %s", run_id, exc_info=True)
            summary.failed += 1
            continue
        if name is None:
            summary.no_owner += 1
            continue
        summary.resolved += 1
        if apply:
            db.query(Run).filter(Run.id == run_id).update({Run.cv_owner_name: name})
            db.commit()
            summary.written += 1
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the names (default: dry run)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        summary = backfill(db, apply=args.apply)
    finally:
        db.close()
    mode = "APPLIED" if args.apply else "DRY RUN (pass --apply to write)"
    print(f"{mode}: candidates={summary.candidates} resolved={summary.resolved} "
          f"no_owner={summary.no_owner} failed={summary.failed} written={summary.written}")
    return 1 if summary.failed else 0


if __name__ == "__main__":
    sys.exit(main())
