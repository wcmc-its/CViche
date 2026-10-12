#!/usr/bin/env python3
"""What reviewers changed in their corrected copies (#1587): the changes in
every stored corrected-copy diff (`review_loop_service.corrected_diff_key`),
summed by change type. These are the doctor's misses and the reviewers' fixes
together; telling them apart from the review-copy comments is #1654.

    cd web_interface/backend
    python3 scripts/corrected_copy_report.py

Every run is read, not only runs with a feedback form: a reviewer can upload a
corrected copy without submitting the form.

Read-only. Run it where the app's DB and storage settings are configured (a
prod/dev pod). stdout is for people, not a contract.
"""
import json
import logging
import sys
from collections.abc import Iterable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session  # noqa: E402

from app.models import Run  # noqa: E402
from app.services.review_loop_service import corrected_diff_key  # noqa: E402
from app.storage import get_storage  # noqa: E402
from unified_pipeline.doctor.docx_diff import CHANGE_TYPES  # noqa: E402

logger = logging.getLogger("corrected_copy_report")


def diff_changes(run_ids: Iterable[str]) -> tuple[int, dict[str, int]]:
    """How many of these runs have a stored corrected-copy diff, and their
    changes summed by type."""
    storage = get_storage()
    runs, totals = 0, dict.fromkeys(CHANGE_TYPES, 0)
    for run_id in sorted(set(run_ids)):
        try:
            report = json.loads(storage.get_file(run_id, corrected_diff_key(run_id)))
        except FileNotFoundError:
            continue
        runs += 1
        for kind in CHANGE_TYPES:
            totals[kind] += int(report.get("by_type", {}).get(kind, 0))
    return runs, totals


def render(runs: int, totals: dict[str, int]) -> str:
    """The report as text."""
    lines = [f"Corrected-copy changes ({runs} runs)"]
    lines += [f"{kind:<32} {count:>6}" for kind, count in totals.items()]
    return "\n".join(lines) + "\n"


def build_report(db: Session) -> str:
    return render(*diff_changes(run_id for (run_id,) in db.query(Run.id)))


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        sys.stdout.write(build_report(db))
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
