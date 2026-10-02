#!/usr/bin/env python3
"""Backfill runs.quality_score / quality_band / quality_cap for runs scored
before those columns existed (or whose live write failed).

For every completed run with quality_score NULL, read its cached
runs/{id}/quality_score.json through the storage layer and copy the score, band
and applied cap onto the row. Never recomputes a score: a run with no cached
file is counted as missing and left alone (the admin rescore endpoint computes
one). Dry-run by default; pass --apply to write.

--rescore-stale instead recomputes every completed run whose cached score was
computed on other dimension weights (its total_weight is not the scorer's
current TOTAL_WEIGHT, e.g. the /95-era scores), re-caching the JSON and
rewriting the columns. Deterministic, no LLM; a run whose outputs are gone from
storage is counted as missing and keeps its old score.

    cd web_interface/backend
    python3 scripts/backfill_quality_score.py            # dry run, prints counts
    python3 scripts/backfill_quality_score.py --apply
    python3 scripts/backfill_quality_score.py --rescore-stale [--apply]

Run it where the app's DB and storage settings are configured (a prod/dev pod).
"""
import argparse
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session  # noqa: E402

from app.models import Run, RunState  # noqa: E402
from app.services.quality_score_service import (  # noqa: E402
    compute_and_cache_score,
    load_cached_score,
    persist_score_columns,
    score_columns,
)
from unified_pipeline.quality_score import TOTAL_WEIGHT  # noqa: E402

logger = logging.getLogger("backfill_quality_score")



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
        db.query(Run.id).filter(Run.quality_score.is_(None), Run.status == RunState.COMPLETE).all()
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


def rescore_stale(db: Session, apply: bool) -> BackfillSummary:
    """Recompute (when ``apply``) every completed run scored on stale weights.

    ``resolved`` counts the stale runs found; ``missing`` the ones whose
    outputs could not be rescored."""
    summary = BackfillSummary()
    run_ids = [run_id for (run_id,) in db.query(Run.id).filter(Run.status == RunState.COMPLETE).all()]
    for run_id in run_ids:
        summary.candidates += 1
        try:
            raw = load_cached_score(run_id)
        except Exception:
            logger.warning("Could not read the cached score for run %s", run_id, exc_info=True)
            summary.failed += 1
            continue
        if not isinstance(raw, dict) or raw.get("total_weight") == TOTAL_WEIGHT:
            continue
        summary.resolved += 1
        if not apply:
            continue
        fresh = compute_and_cache_score(run_id)
        if fresh is None:
            summary.missing += 1
            continue
        persist_score_columns(db, run_id, fresh)
        summary.written += 1
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the columns (default: dry run)")
    parser.add_argument("--rescore-stale", action="store_true",
                        help=f"recompute runs whose cached total_weight is not {TOTAL_WEIGHT}")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    from app.database import SessionLocal

    db = SessionLocal()
    try:
        summary = (rescore_stale if args.rescore_stale else backfill)(db, apply=args.apply)
    finally:
        db.close()
    mode = "APPLIED" if args.apply else "DRY RUN (pass --apply to write)"
    sys.stdout.write(
        f"{mode}: candidates={summary.candidates} resolved={summary.resolved} "
        f"missing={summary.missing} failed={summary.failed} written={summary.written}\n")
    return 1 if summary.failed else 0


if __name__ == "__main__":
    sys.exit(main())
