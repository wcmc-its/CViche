#!/usr/bin/env python3
"""Backfill runs.quality_score / quality_band / quality_cap for runs scored
before those columns existed (or whose live write failed).

For every completed run with quality_score NULL, read its cached
runs/{id}/quality_score.json through the storage layer and copy the score, band
and applied cap onto the row. Never recomputes a score: a run with no cached
file is counted as missing and left alone (the admin rescore endpoint computes
one). Dry-run by default; pass --apply to write.

    cd web_interface/backend
    python3 scripts/backfill_quality_score.py            # dry run, prints counts
    python3 scripts/backfill_quality_score.py --apply

Run it where the app's DB and storage settings are configured (a prod/dev pod).
"""
import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session  # noqa: E402

from app.models import Run  # noqa: E402
from app.services.quality_score_service import load_cached_score, score_columns  # noqa: E402

logger = logging.getLogger("backfill_quality_score")

RUN_COMPLETE = "complete"


@dataclass
class BackfillSummary:
    candidates: int = 0
    resolved: int = 0
    missing: int = 0
    failed: int = 0
    written: int = 0


def backfill(db: Session, apply: bool) -> BackfillSummary:
    """Map (and, when ``apply``, store) the cached score of every candidate run."""
    summary = BackfillSummary()
    run_ids = [
        run_id for (run_id,) in
        db.query(Run.id).filter(Run.quality_score.is_(None), Run.status == RUN_COMPLETE).all()
    ]
    for run_id in run_ids:
        summary.candidates += 1
        try:
            raw = load_cached_score(run_id)
        except Exception:
            logger.warning("Could not read the cached score for run %s", run_id, exc_info=True)
            summary.failed += 1
            continue
        cols = score_columns(raw)
        if raw is None or cols.quality_score is None:
            # Absent, or present with no usable totalScore: nothing to copy.
            summary.missing += 1
            continue
        summary.resolved += 1
        if apply:
            db.query(Run).filter(Run.id == run_id).update({
                Run.quality_score: cols.quality_score,
                Run.quality_band: cols.quality_band,
                Run.quality_cap: cols.quality_cap,
            })
            db.commit()
            summary.written += 1
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the columns (default: dry run)")
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
        f"{mode}: candidates={summary.candidates} resolved={summary.resolved} "
        f"missing={summary.missing} failed={summary.failed} written={summary.written}\n")
    return 1 if summary.failed else 0


if __name__ == "__main__":
    sys.exit(main())
