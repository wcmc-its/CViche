#!/usr/bin/env python3
"""Backfill runs.input_format / input_format_score and runs.source_sha256 for
runs uploaded before those columns existed (or whose detection failed at upload).

For every run with input_format or source_sha256 NULL, read the archived source
CV runs/{id}/input/{id}.{ext} through the storage layer; hash its bytes for
source_sha256, and extract its text with the same extractor the upload endpoint
uses, run the detector and copy the result onto the row. A run with no archived source (everything before 2026-06-02) is
counted as "no source" and left NULL. Dry-run by default, printing one line per
run; pass --apply to write.

    cd web_interface/backend
    python3 scripts/backfill_input_format.py            # dry run
    python3 scripts/backfill_input_format.py --apply

Run it where the app's DB and storage settings are configured (a prod/dev pod).
"""
import argparse
import hashlib
import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import Row, or_  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.api.upload import _extract_text  # noqa: E402
from app.models import Run  # noqa: E402
from app.services.input_format import (  # noqa: E402
    INPUT_FORMAT_OTHER,
    INPUT_FORMAT_WCM,
    detect_input_format,
)
from app.storage import get_storage  # noqa: E402
from app.storage.base import StorageKeyNotFound  # noqa: E402

logger = logging.getLogger("backfill_input_format")


@dataclass
class BackfillSummary:
    candidates: int = 0
    wcm: int = 0
    other: int = 0
    no_source: int = 0
    undetermined: int = 0
    failed: int = 0
    hashed: int = 0
    written: int = 0


def read_source(run_id: str, file_type: str) -> bytes:
    """The archived source CV's bytes.

    Raises StorageKeyNotFound when the run has no archived source."""
    return get_storage().get_file(run_id, f"input/{run_id}.{file_type}")


def _detect(content: bytes, file_type: str, summary: BackfillSummary) -> dict | None:
    """Column updates for the detected format, or None when it is undetermined."""
    fmt, score = detect_input_format(_extract_text(content, f".{file_type}"))
    if fmt is None:
        summary.undetermined += 1
        return None
    summary.wcm += fmt == INPUT_FORMAT_WCM
    summary.other += fmt == INPUT_FORMAT_OTHER
    return {Run.input_format: fmt, Run.input_format_score: score}


def _updates_for(run: Row, content: bytes, summary: BackfillSummary, out: TextIO) -> dict:
    """The column updates one run's archived source yields (empty: nothing to fill)."""
    updates: dict = {}
    if run.source_sha256 is None:
        digest = hashlib.sha256(content).hexdigest()
        out.write(f"{run.id} sha256 {digest}\n")
        summary.hashed += 1
        updates[Run.source_sha256] = digest
    if run.input_format is None:
        detected = _detect(content, run.file_type, summary)
        if detected:
            out.write(f"{run.id} {detected[Run.input_format]} {detected[Run.input_format_score]}\n")
            updates.update(detected)
    return updates


def backfill(db: Session, apply: bool, out: TextIO = sys.stdout) -> BackfillSummary:
    """Fill (and, when ``apply``, store) the NULL input format and source hash of every run."""
    summary = BackfillSummary()
    candidates = db.query(Run.id, Run.file_type, Run.input_format, Run.source_sha256).filter(
        or_(Run.input_format.is_(None), Run.source_sha256.is_(None))).all()
    for run in candidates:
        summary.candidates += 1
        try:
            updates = _updates_for(run, read_source(run.id, run.file_type), summary, out)
        except StorageKeyNotFound:
            summary.no_source += 1
            continue
        except Exception:
            logger.warning("Could not classify run %s", run.id, exc_info=True)
            summary.failed += 1
            continue
        if apply and updates:
            db.query(Run).filter(Run.id == run.id).update(updates)
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
        f"{mode}: candidates={summary.candidates} wcm={summary.wcm} other={summary.other} "
        f"hashed={summary.hashed} no_source={summary.no_source} undetermined={summary.undetermined} "
        f"failed={summary.failed} written={summary.written}\n")
    return 1 if summary.failed else 0


if __name__ == "__main__":
    sys.exit(main())
