#!/usr/bin/env python3
"""Review-derived labels for the doctor's label store (#1654): one `<uid>.json`
per run with a corrected copy, in scripts/doctor_vs_autopsy.py's schema.

    cd web_interface/backend
    python3 scripts/review_labels.py OUT_DIR [--precision]

Each label's `findings` are the stored corrected-copy diff's changes
(doctor/docx_diff.py `to_label`: change type and entry index), and its
`doctor_review` is the comment-fate verdicts (doctor/comment_fate.py
`doctor_review`: fixed is TP, not_a_problem FP, unknown left out). A run whose
verdicts were never stored (a copy uploaded before #1654) gets them now,
with the doctor's re-run beside them (review_loop_service.review_corrected_copy).
`load_labels(OUT_DIR)` reads the directory as it reads an autopsy batch's.

--precision prints each (lint, shape) the verdicts judge: the gate's
hand-checked precision, and the same with these verdicts folded in
(doctor/precision.py `fold_review_verdicts`). The gate itself never reads them.

Run it where the app's DB and storage settings are configured (a prod/dev
pod). Writes only under OUT_DIR, plus any missing verdicts and re-runs under
each run's corrected/. Exits 2 when no run has a corrected copy: an empty
label directory is not a labelled one (CODING_STANDARDS 5.5). stdout is for
people, not a contract.
"""
import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session  # noqa: E402

from app.models import Run  # noqa: E402
from app.services.review_loop_service import (  # noqa: E402
    corrected_diff_key,
    corrected_docx_key,
    corrected_verdicts_key,
    review_corrected_copy,
)
from app.storage import get_storage  # noqa: E402
from unified_pipeline.doctor.comment_fate import (  # noqa: E402
    LABEL_VERDICTS,
    doctor_review,
)
from unified_pipeline.doctor.docx_diff import from_report, to_label  # noqa: E402
from unified_pipeline.doctor.precision import (  # noqa: E402
    ShapeKey,
    fold_review_verdicts,
    load_gate_ledger,
)

logger = logging.getLogger("review_labels")
EXIT_NOTHING_TO_LABEL = 2


def _verdicts(db: Session, run_id: str) -> dict:
    """The run's stored verdict report, computed and stored first when missing."""
    storage = get_storage()
    try:
        return json.loads(storage.get_file(run_id, corrected_verdicts_key(run_id)))
    except FileNotFoundError:
        review_corrected_copy(db, run_id, storage.get_file(run_id, corrected_docx_key(run_id)))
        return json.loads(storage.get_file(run_id, corrected_verdicts_key(run_id)))


def run_label(db: Session, run_id: str) -> dict | None:
    """The run's review label; None when it has no stored corrected-copy diff,
    or no verdicts and none could be made (logged, and the other runs go on)."""
    try:
        diff = json.loads(get_storage().get_file(run_id, corrected_diff_key(run_id)))
    except FileNotFoundError:
        return None
    try:
        verdicts = _verdicts(db, run_id)
    except FileNotFoundError:
        logger.warning("Run %s has a corrected-copy diff but no verdicts, and none could be made; skipped",
                       run_id)
        return None
    return to_label(run_id, from_report(diff), doctor_review(verdicts["findings"]))


def verdict_counts(labels: list[dict]) -> dict[ShapeKey, dict[str, int]]:
    """The labels' TP / FP verdicts as comment-fate counts per (lint, shape),
    as `fold_review_verdicts` takes them."""
    as_verdict = {label: verdict for verdict, label in LABEL_VERDICTS.items()}
    counts: dict[ShapeKey, Counter[str]] = {}
    for label in labels:
        for review in label["doctor_review"]:
            counts.setdefault((review["lint"], review["shape"]), Counter())[as_verdict[review["verdict"]]] += 1
    return {key: dict(c) for key, c in counts.items()}


def precision_lines(counts: dict[ShapeKey, dict[str, int]]) -> list[str]:
    """Each judged key's gate precision, then with the verdicts folded in."""
    rows = load_gate_ledger()
    folded = fold_review_verdicts(rows, counts)

    def cell(key: ShapeKey, ledger: dict) -> str:
        row = ledger.get(key)
        return f"{row.true_positives}/{row.judged}" if row and row.judged else "unmeasured"

    lines = [f"{'lint:shape':<56} {'gate':>12} {'with review':>12}"]
    for key in sorted(counts, key=lambda k: (k[0], k[1] or "")):
        name = f"{key[0]}:{key[1]}" if key[1] else key[0]
        lines.append(f"{name:<56} {cell(key, rows):>12} {cell(key, folded):>12}")
    return lines


def write_labels(db: Session, out_dir: Path) -> list[dict]:
    """Write every run's review label under ``out_dir``; return them."""
    out_dir.mkdir(parents=True, exist_ok=True)
    labels = []
    for (run_id,) in db.query(Run.id).order_by(Run.id):
        label = run_label(db, run_id)
        if label is not None:
            (out_dir / f"{run_id}.json").write_text(json.dumps(label, indent=1), encoding="utf-8")
            labels.append(label)
    return labels


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--precision", action="store_true",
                        help="print the gate's precision with and without these verdicts")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        labels = write_labels(db, args.out_dir)
    finally:
        db.close()
    if not labels:
        logger.error("No run has a corrected copy; nothing written to %s", args.out_dir)
        return EXIT_NOTHING_TO_LABEL
    reviews = sum(len(label["doctor_review"]) for label in labels)
    changes = sum(len(label["findings"]) for label in labels)
    sys.stdout.write(f"{len(labels)} labels in {args.out_dir}: {changes} changes, {reviews} verdicts\n")
    if args.precision:
        sys.stdout.write("\n".join(precision_lines(verdict_counts(labels))) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
