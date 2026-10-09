#!/usr/bin/env python3
"""Score a single local pipeline run; print a one-line TSV summary.

    PYTHONPATH=src python3 scripts/score_one.py <outputs_root> <uid> [wcm_docx] [score_json_out] [--doctor report.json]

<outputs_root> is the dir holding the stage_* subdirs (i.e. src/unified_pipeline/outputs).
If score_json_out is given, the full score_run dict is written there.
--doctor is the run's doctor report (doctor_one.py's findings_json_out). The score is built
from it (#1595); without it the stage_7_doctor/<uid>_doctor.json a local run writes is used,
and with neither the run reads as not checked and is capped below GREEN.

The scorer wants every artifact in ONE directory, but a local run scatters them
across stage_* subdirs, so they are collected into a temp dir first -- the same
approach web_interface/.../quality_score_service.py takes when it pulls a run's
artifacts out of storage. Deterministic and free: no LLM calls.

Interface contract (run_corpus_batch.sh consumes this via command substitution,
and standalone to (re)score any local run whose artifacts are still on disk):

  stdout  ONE machine-readable TSV line -- the program's output, not a log:
            totalScore<TAB>band<TAB>raw_before_caps<TAB>top_penalties
  stderr  diagnostics only (the batch runner appends these to the per-CV log)
  exit 1  the run could not be scored; the caller substitutes an 'error' row
"""
import argparse
import json
import logging
import shutil
import sys
import tempfile
from pathlib import Path

from unified_pipeline.quality_score import DOCTOR_REPORT_SUFFIX, score_run
from unified_pipeline.stage_errors import STAGE_ERRORS_SUFFIX

logger = logging.getLogger(__name__)

# What the dimension scorers actually read. Keep in step with
# quality_score_service._NEEDED_SUFFIXES. The stage-error record (#745) sits in
# its own stage_errors/ dir, which the */<uid><suffix> glob below covers.
NEEDED_SUFFIXES = ("_classified.json", "_fields.json", "_entries.json", STAGE_ERRORS_SUFFIX,
                   DOCTOR_REPORT_SUFFIX)


def collect(outputs_root: Path, uid: str, wcm_docx: Path, dest: Path,
            doctor_report: Path | None = None) -> int:
    """Flatten this run's scorable artifacts into dest. Returns how many landed.
    A doctor report given explicitly replaces the one under outputs_root, so
    the scorer never sees two (it would call the pair ambiguous)."""
    found = 0
    if doctor_report:
        shutil.copy(doctor_report, dest / f"{uid}{DOCTOR_REPORT_SUFFIX}")
        found += 1
    for suffix in NEEDED_SUFFIXES:
        if doctor_report and suffix == DOCTOR_REPORT_SUFFIX:
            continue
        # stage_* subdirs only; a flat outputs_root works too since glob('*')
        # over files simply matches nothing.
        for path in outputs_root.glob(f"*/{uid}{suffix}"):
            shutil.copy(path, dest / path.name)
            found += 1
    if wcm_docx and wcm_docx.exists():
        shutil.copy(wcm_docx, dest / wcm_docx.name)
        found += 1
    return found


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("outputs_root", help="dir holding the stage_* subdirs")
    ap.add_argument("uid", help="document uid to score")
    ap.add_argument("wcm_docx", nargs="?", default=None,
                    help="generated WCM .docx; enables the render dimensions")
    ap.add_argument("score_json_out", nargs="?", default=None,
                    help="write the full score_run dict here as JSON")
    ap.add_argument("--doctor", default=None,
                    help="the run's doctor report; the score is built from it "
                         "(default: <outputs_root>/stage_7_doctor/<uid>_doctor.json, if any)")
    args = ap.parse_args(argv)

    root = Path(args.outputs_root)
    if not root.is_dir():
        ap.error(f"outputs_root is not a directory: {root}")

    # Validate the out dir BEFORE scoring: the write happens last, so a typo'd
    # path would otherwise discard the whole report.
    out = Path(args.score_json_out) if args.score_json_out else None
    if out and not out.parent.is_dir():
        ap.error(f"score_json_out directory does not exist: {out.parent}")

    # A missing docx is not fatal, but it does NOT mean "score the render as 0":
    # both render scorers return a flat fraction=0.5 when no docx is present, and
    # TOTAL_WEIGHT is constant, so half credit is awarded rather than withheld.
    # A run whose stage 6 rendered nothing can therefore out-score one that
    # rendered something genuinely sparse (observed: 82 vs 76 on the same uid).
    # Always pass the docx when it exists; treat a scored row without one as an
    # upper bound, not a measurement.
    docx = Path(args.wcm_docx) if args.wcm_docx else None
    if docx and not docx.exists():
        logger.warning("WCM docx not found; render dimensions get flat half credit "
                       "(inflates the score, does not zero it): %s", docx)
        docx = None

    doctor = Path(args.doctor) if args.doctor else None
    if doctor and not doctor.is_file():
        ap.error(f"doctor report not found: {doctor}")

    tmp = Path(tempfile.mkdtemp(prefix=f"score_{args.uid}_"))
    try:
        found = collect(root, args.uid, docx, tmp, doctor)
        if not found:
            logger.error("no scorable artifacts for uid=%s under %s", args.uid, root)
            return 1
        try:
            report = score_run(str(tmp), args.uid)
        except Exception:
            # Honour the caller's contract instead of dying with a traceback
            # mid-batch: run_corpus_batch.sh redirects stderr to the per-CV log
            # and substitutes an 'error' row when we exit non-zero.
            logger.exception("score_run failed for uid=%s", args.uid)
            return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    penalised = sorted((d for d in report.get("dimensionScores", []) if d.get("penalty", 0) > 0),
                       key=lambda d: -d["penalty"])
    top = ",".join(f"{d['name'].split(' (')[0]}:{d['penalty']:.1f}" for d in penalised[:3])

    # stdout is the TSV contract (see the docstring): deliberately print(), not a
    # logger call -- run_corpus_batch.sh captures this line with $(...), so
    # routing it through logging (stderr, level-prefixed) would break the caller.
    print(f"{report.get('totalScore', '')}\t{(report.get('band') or '').split(' (')[0]}\t"
          f"{report.get('raw_score_before_caps', '')}\t{top}")

    if out:
        out.write_text(json.dumps(report, default=str, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
