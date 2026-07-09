#!/usr/bin/env python3
"""Stage flat S3 run outputs into the stage_* layout run_doctor expects, run the
doctor over a set of representative runs, and aggregate findings by lint so you
can rank defect classes by how many DISTINCT CVs each affects.

    PYTHONPATH=src python3 scripts/corpus_doctor_sweep.py <corpus_dir> \
        <run_id>[,<run_id>...] [--out sweep.json]

corpus_dir holds `<run_id>/outputs/<uid>_*.json` + `<uid>_wcm.docx` (a flat
`aws s3 sync s3://wcm-cviche-storage/cviche/runs/ <dir>`). Pass ONE run per
distinct CV (pick reps with scripts/corpus_distinct_cvs.py first) so the
aggregate counts distinct CVs, not runs. Source docx is absent from S3 outputs,
so the segmentation/missed_headers lints skip -- under_extraction still catches
the fused-entry class from stage_4 alone.
"""
import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

from unified_pipeline.run_doctor import run_doctor

# S3-flat suffix -> the stage_* dir run_doctor._ARTIFACTS globs (kept in sync
# with that map; a suffix run_doctor stops reading just goes unused here).
SUFFIX_DIR = {
    "_segmented.json": "stage_1a_segmentation",
    "_entries.json": "stage_2_entry_extraction",
    "_classified.json": "stage_3b_classified_entries",
    "_fields.json": "stage_4_field_extraction",
    "_enriched.json": "stage_5_enrichment",
    "_wcm.docx": "stage_6_wcm_documents",
    "_render_warnings.json": "stage_6_wcm_documents",
}


def _uid_of(run_dir: Path):
    """The artifact prefix for this run (the `_fields.json` stem, else `_wcm`)."""
    for f in run_dir.glob("*_fields.json"):
        return f.name[: -len("_fields.json")]
    for f in run_dir.glob("*_wcm.docx"):
        return f.name[: -len("_wcm.docx")]
    return None


def stage(run_dir: Path, uid: str, work: Path) -> Path:
    """Symlink the flat outputs into `<work>/<uid>/stage_*/` and return the root."""
    root = work / uid
    for suffix, stagedir in SUFFIX_DIR.items():
        src = run_dir / f"{uid}{suffix}"
        if not src.exists():
            continue
        d = root / stagedir
        d.mkdir(parents=True, exist_ok=True)
        link = d / src.name
        if not link.exists():
            link.symlink_to(src.resolve())
    return root


def sweep(corpus_dir: Path, run_ids, work: Path):
    reports = {}
    for rid in run_ids:
        run_dir = corpus_dir / rid / "outputs"
        if not run_dir.is_dir():
            run_dir = corpus_dir / rid  # tolerate an already-flat layout
        uid = _uid_of(run_dir)
        if not uid:
            print(f"  !! {rid}: no artifacts found, skipping", file=sys.stderr)
            continue
        root = stage(run_dir, uid, work)
        reports[rid] = run_doctor(root, uid)
    return reports


# The 13 lints run_doctor always considers (skipped ones emit a "skipped: missing"
# INFO; a lint that runs clean emits nothing -- so ran = ALL - skipped, not the
# set of lints that happened to fire).
ALL_LINTS = [
    "segmentation", "missed_headers", "bucket_status", "under_extraction",
    "classified_unrendered", "output_hygiene", "dead_sections",
    "unrendered_records", "enrichment_failures", "stage6_render_warnings",
    "dedup_drops", "pipe_leaks", "table_shape",
]


def aggregate(reports):
    """Per lint: how many distinct reps have a real (>=WARN) finding, and ERROR."""
    warn = Counter()
    error = Counter()
    ran = Counter()  # reps where the lint actually ran (input present, not skipped)
    for rep in reports.values():
        seen_warn, seen_err, skipped = set(), set(), set()
        for f in rep["findings"]:
            lint, sev = f["lint"], f["severity"]
            if sev == "INFO" and "skipped: missing" in f["message"]:
                skipped.add(lint)
            elif sev == "WARN":
                seen_warn.add(lint)
            elif sev == "ERROR":
                seen_err.add(lint)
        for l in seen_warn | seen_err:
            warn[l] += 1
        for l in seen_err:
            error[l] += 1
        for l in ALL_LINTS:
            if l not in skipped:
                ran[l] += 1
    # warn[] already counts every rep with a WARN-or-ERROR finding; error[] is a
    # subset. Rank by affected-CV count, ERROR count as tiebreak, then catalog order.
    lints = sorted(ALL_LINTS, key=lambda l: (-warn[l], -error[l], ALL_LINTS.index(l)))
    return [
        {"lint": l, "cvs_affected": warn[l] + 0, "cvs_error": error[l],
         "cvs_ran": ran[l]}
        for l in lints
    ]


def _selftest():
    # rep A: pipe_leaks WARN + segmentation skipped. rep B: pipe_leaks ERROR, nothing skipped.
    reports = {
        "A": {"findings": [
            {"lint": "pipe_leaks", "severity": "WARN", "message": "x"},
            {"lint": "segmentation", "severity": "INFO", "message": "skipped: missing source"},
        ]},
        "B": {"findings": [
            {"lint": "pipe_leaks", "severity": "ERROR", "message": "y"},
        ]},
    }
    rank = {r["lint"]: r for r in aggregate(reports)}
    assert rank["pipe_leaks"]["cvs_affected"] == 2, "both reps have a >=WARN pipe_leaks finding"
    assert rank["pipe_leaks"]["cvs_error"] == 1, "only rep B is ERROR"
    assert rank["pipe_leaks"]["cvs_ran"] == 2, "pipe_leaks ran on both (never skipped)"
    assert rank["segmentation"]["cvs_ran"] == 1, "segmentation skipped on A, ran (clean) on B"
    assert rank["segmentation"]["cvs_affected"] == 0
    assert aggregate(reports)[0]["lint"] == "pipe_leaks", "ranked first by affected count"
    print("selftest OK")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("corpus_dir", nargs="?")
    ap.add_argument("run_ids", nargs="?", help="comma-separated representative run_ids")
    ap.add_argument("--work", default=None, help="staging dir (default: <corpus_dir>/.doctor_stage)")
    ap.add_argument("--out", default=None, help="write full JSON report here")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not a.corpus_dir:
        ap.error("corpus_dir is required (or use --selftest)")

    corpus_dir = Path(a.corpus_dir)
    work = Path(a.work) if a.work else corpus_dir / ".doctor_stage"
    work.mkdir(parents=True, exist_ok=True)
    run_ids = [r.strip() for r in a.run_ids.split(",") if r.strip()]

    reports = sweep(corpus_dir, run_ids, work)
    ranking = aggregate(reports)
    n = len(reports)

    print(f"\nDoctor sweep: {n} distinct CVs\n")
    print(f"{'lint':24s} {'CVs affected':>13s} {'(of which ERROR)':>17s} {'ran on':>8s}")
    for row in ranking:
        print(f"{row['lint']:24s} {row['cvs_affected']:>10d}/{n:<2d} "
              f"{row['cvs_error']:>15d} {row['cvs_ran']:>8d}/{n}")

    if a.out:
        Path(a.out).write_text(json.dumps(
            {"n_cvs": n, "ranking": ranking, "reports": reports}, indent=2))
        print(f"\n-> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
