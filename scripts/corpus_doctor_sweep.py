#!/usr/bin/env python3
"""Stage flat S3 run outputs into the stage_* layout run_doctor expects, run the
doctor over a set of representative runs, and aggregate findings by lint so you
can rank defect classes by how many DISTINCT CVs each affects.

    PYTHONPATH=src python3 scripts/corpus_doctor_sweep.py <corpus_dir> \
        <run_id>[,<run_id>...] [--out sweep.json]

corpus_dir holds `<run_id>/outputs/<uid>_*.json` + `<uid>_wcm.docx` AND the
original upload at `<run_id>/input/<uid>.docx` (durably archived by the backend
since 2026-06-02; sync the whole run dir, not just outputs/). Pass ONE run per
distinct CV (pick reps with scripts/corpus_distinct_cvs.py first) so the
aggregate counts distinct CVs, not runs. The source docx is staged into the run
root so the segmentation/missed_headers lints run (they skip only for runs
predating the input-archiving feature).
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


def _find_uid(run_dir: Path):
    """The artifact prefix for this run (the `_fields.json` stem, else `_wcm`)."""
    for f in run_dir.glob("*_fields.json"):
        return f.name[: -len("_fields.json")]
    for f in run_dir.glob("*_wcm.docx"):
        return f.name[: -len("_wcm.docx")]
    return None


def _resolve_run_dir(corpus_dir: Path, run_id: str) -> Path:
    """The dir holding this run's artifacts, tolerating an already-flat layout.

    `<corpus>/<run>/outputs` when present, else `<corpus>/<run>`. Returns the
    flat path even when it doesn't exist so the caller's `_find_uid is None`
    branch reports the run uniformly (this never raises).
    """
    nested = corpus_dir / run_id / "outputs"
    return nested if nested.is_dir() else corpus_dir / run_id


def _relink(link: Path, src: Path) -> None:
    """Point `link` at `src`, unconditionally replacing any existing link.

    Both failure modes of a bare `if not link.exists(): symlink_to()` bite here:
    mode A -- a broken symlink reports exists()=False yet symlink_to still raises
    FileExistsError (the link path is still there); mode B (the bad one) -- a
    live link from an earlier sweep of a DIFFERENT run under the same uid gets
    silently reused, so run_doctor lints the old run's artifacts under the new
    run's name. `work` persists across invocations (`.doctor_stage`), so mode B
    fires whenever a representative run is re-picked. Unlink first, always.
    """
    link.unlink(missing_ok=True)
    link.symlink_to(src.resolve())


def stage(run_dir: Path, uid: str, work: Path) -> Path:
    """Symlink the flat outputs into `<work>/<uid>/stage_*/` and return the root."""
    root = work / uid
    root.mkdir(parents=True, exist_ok=True)
    for suffix, stagedir in SUFFIX_DIR.items():
        src = run_dir / f"{uid}{suffix}"
        if not src.exists():
            continue
        d = root / stagedir
        d.mkdir(parents=True, exist_ok=True)
        _relink(d / src.name, src)
    # Source docx: the original upload is durably archived at runs/<id>/input/
    # (since 2026-06-02, commit 8358c0b). Symlink it into root so _find_source
    # picks it up and the segmentation/missed_headers lints (1-2) can run.
    for cand_dir in (run_dir.parent / "input", run_dir):
        if not cand_dir.is_dir():
            continue
        cands = [p for p in sorted(cand_dir.glob(f"{uid}*.docx"))
                 if not p.name.endswith("_wcm.docx")]
        if cands:
            _relink(root / cands[0].name, cands[0])
            break
    return root


def sweep(corpus_dir: Path, run_ids, work: Path):
    """Doctor each run; one bad run is reported and skipped, never aborts the rest."""
    reports = {}
    failures = {}
    for run_id in run_ids:
        try:
            run_dir = _resolve_run_dir(corpus_dir, run_id)
            uid = _find_uid(run_dir)
            if not uid:
                print(f"  !! {run_id}: no artifacts found, skipping", file=sys.stderr)
                continue
            reports[run_id] = run_doctor(stage(run_dir, uid, work), uid)
        except Exception as e:  # noqa: BLE001 - one bad run must not lose the sweep
            print(f"  !! {run_id}: run_doctor failed: {e}", file=sys.stderr)
            failures[run_id] = str(e)
    if failures:
        print(f"\n[WARN] {len(failures)} run(s) failed and were dropped from the "
              f"sweep: {list(failures)}. Prevalence counts are over the "
              f"{len(reports)} that succeeded.", file=sys.stderr)
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
        for lint in seen_warn | seen_err:
            warn[lint] += 1
        for lint in seen_err:
            error[lint] += 1
        for lint in ALL_LINTS:
            if lint not in skipped:
                ran[lint] += 1
    # warn[] already counts every rep with a WARN-or-ERROR finding; error[] is a
    # subset. Rank by affected-CV count, ERROR count as tiebreak, then catalog order.
    lints = sorted(ALL_LINTS,
                   key=lambda lint: (-warn[lint], -error[lint], ALL_LINTS.index(lint)))
    return [
        {"lint": lint, "cvs_affected": warn[lint], "cvs_error": error[lint],
         "cvs_ran": ran[lint]}
        for lint in lints
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

    # #7: _resolve_run_dir tolerates both the nested (outputs/) and flat layout,
    # and never raises on an absent run (returns the flat path for the caller's
    # _find_uid-is-None branch to report).
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        corpus = Path(td)
        (corpus / "nested" / "outputs").mkdir(parents=True)
        (corpus / "flat").mkdir()
        assert _resolve_run_dir(corpus, "nested") == corpus / "nested" / "outputs"
        assert _resolve_run_dir(corpus, "flat") == corpus / "flat"
        assert _resolve_run_dir(corpus, "absent") == corpus / "absent"

    # #4: ALL_LINTS is hand-kept in sync with run_doctor. The proper fix is
    # exporting KNOWN_LINTS from run_doctor (#268) -- deriving by function name
    # is wrong (lint_stage6_warnings emits key "stage6_render_warnings"). Until
    # then, trip loudly the next time a lint is added/removed so the count can't
    # silently drift.
    import unified_pipeline.run_doctor as rd
    n_lint_fns = sum(1 for name in dir(rd)
                     if name.startswith("lint_") and callable(getattr(rd, name))
                     # defined here, not an imported lint_* (e.g. lint_metrics)
                     and getattr(getattr(rd, name), "__module__", None) == rd.__name__)
    assert n_lint_fns == len(ALL_LINTS), (
        f"lint drift: run_doctor has {n_lint_fns} lint_* functions but ALL_LINTS "
        f"lists {len(ALL_LINTS)}; reconcile (see #268 for the KNOWN_LINTS export)")
    assert len(set(ALL_LINTS)) == len(ALL_LINTS), "ALL_LINTS has duplicates"

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
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    if not args.corpus_dir:
        ap.error("corpus_dir is required (or use --selftest)")
    if not args.run_ids:  # nargs="?" -> None; .split() would crash without this
        ap.error("run_ids is required (or use --selftest)")

    corpus_dir = Path(args.corpus_dir)
    run_ids = [r.strip() for r in args.run_ids.split(",") if r.strip()]
    if not run_ids:
        ap.error("run_ids must contain at least one run id")

    # Validate --out's parent BEFORE the sweep: the write happens only after every
    # run_doctor call, so a typo'd dir would otherwise discard the whole sweep.
    if args.out and not Path(args.out).parent.is_dir():
        ap.error(f"--out directory does not exist: {Path(args.out).parent}")

    work = Path(args.work) if args.work else corpus_dir / ".doctor_stage"
    work.mkdir(parents=True, exist_ok=True)

    reports = sweep(corpus_dir, run_ids, work)
    ranking = aggregate(reports)
    n = len(reports)

    print(f"\nDoctor sweep: {n} distinct CVs\n")
    print(f"{'lint':24s} {'CVs affected':>13s} {'(of which ERROR)':>17s} {'ran on':>8s}")
    for row in ranking:
        print(f"{row['lint']:24s} {row['cvs_affected']:>10d}/{n:<2d} "
              f"{row['cvs_error']:>15d} {row['cvs_ran']:>8d}/{n}")

    if args.out:
        Path(args.out).write_text(json.dumps(
            {"n_cvs": n, "ranking": ranking, "reports": reports}, indent=2))
        print(f"\n-> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
