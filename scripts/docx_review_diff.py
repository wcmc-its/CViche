#!/usr/bin/env python3
"""Diff a delivered <uid>_wcm.docx against the reviewer's corrected copy (#1587).

    PYTHONPATH=src python3 scripts/docx_review_diff.py <delivered.docx> <corrected.docx> \
        --uid UID [--entries STAGE4.json] [--report OUT.json] [--label LABELS_DIR]

--entries   the run's stage-4 (or later) JSON; maps each change to its entry
            index. Without it every element_idx is null.
--report    the typed diff, stored with the run's artifacts (block positions,
            character counts, WCM section headings -- no CV text).
--label     a directory: writes <UID>.json there in the label schema
            scripts/doctor_vs_autopsy.py reads (uid, entry index, change type).

stdout is a human summary, not a contract (the JSON files are). Exits 2 when
either file cannot be read or the stage JSON is malformed.
"""
import argparse
import json
import sys
import zipfile
from pathlib import Path

from docx.opc.exceptions import PackageNotFoundError

from unified_pipeline.doctor.docx_diff import (
    CHANGE_TYPES,
    diff_docx,
    to_label,
    to_report,
)

EXIT_INPUT_ERROR = 2


def _load_entries(path: Path | None) -> dict | None:
    if path is None:
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ValueError(f"{path}: no entries list")
    return data


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("delivered", type=Path)
    parser.add_argument("corrected", type=Path)
    parser.add_argument("--uid", required=True)
    parser.add_argument("--entries", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--label", type=Path, default=None, help="labels directory")
    args = parser.parse_args(argv)
    try:
        diff = diff_docx(args.delivered, args.corrected, _load_entries(args.entries))
    except (OSError, ValueError, KeyError, zipfile.BadZipFile, PackageNotFoundError) as e:
        # an unreadable input fails closed (CODING_STANDARDS 5.5)
        print(f"docx_review_diff: {type(e).__name__}: {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    report = to_report(args.uid, diff)
    if args.report:
        _write_json(args.report, report)
    if args.label:
        args.label.mkdir(parents=True, exist_ok=True)
        _write_json(args.label / f"{args.uid}.json", to_label(args.uid, diff))
    mapped = sum(c.element_idx is not None for c in diff.changes)
    counts = ", ".join(f"{kind} {report['by_type'][kind]}" for kind in CHANGE_TYPES)
    print(f"{args.uid}: {len(diff.changes)} changes ({counts}); {mapped} mapped to an entry; "
          f"corrected file has {diff.corrected_revisions.insertions} pending insertions, "
          f"{diff.corrected_revisions.deletions} pending deletions")
    return 0


if __name__ == "__main__":
    sys.exit(main())
