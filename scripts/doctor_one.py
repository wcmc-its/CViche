#!/usr/bin/env python3
"""Run run_doctor on a single local pipeline run; print a one-line TSV summary.

    PYTHONPATH=src python3 scripts/doctor_one.py <outputs_root> <uid> [source_docx] [findings_json_out]

<outputs_root> is the dir holding the stage_* subdirs (i.e. src/unified_pipeline/outputs).
If findings_json_out is given, the full run_doctor dict is written there.

Interface contract (run_corpus_batch.sh --doctor consumes this via command
substitution, and standalone to (re)doctor any local run):

  stdout  ONE machine-readable TSV line -- the program's output, not a log:
            worst<TAB>nERROR<TAB>nWARN<TAB>nINFO<TAB>top_lints
  stderr  diagnostics only (the batch runner appends these to the per-CV log)
  exit 1  the doctor could not run; the caller substitutes an 'error' row
"""
import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from unified_pipeline.run_doctor import rank_lints, run_doctor

logger = logging.getLogger(__name__)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("outputs_root", help="dir holding the stage_* subdirs")
    ap.add_argument("uid", help="document uid to doctor")
    ap.add_argument("source_docx", nargs="?", default=None,
                    help="original source .docx; enables the source-dependent lints")
    ap.add_argument("findings_json_out", nargs="?", default=None,
                    help="write the full run_doctor dict here as JSON")
    args = ap.parse_args(argv)

    root = Path(args.outputs_root)
    if not root.is_dir():
        ap.error(f"outputs_root is not a directory: {root}")

    # Validate the out dir BEFORE doctoring: the write happens last, so a typo'd
    # path would otherwise discard the whole report.
    out = Path(args.findings_json_out) if args.findings_json_out else None
    if out and not out.parent.is_dir():
        ap.error(f"findings_json_out directory does not exist: {out.parent}")

    # A missing source is not fatal -- the source-dependent lints (segmentation,
    # missed_headers) just skip. The batch passes a path per CV that may not exist.
    source = Path(args.source_docx) if args.source_docx else None
    if source and not source.exists():
        logger.warning("source not found; source-dependent lints will skip: %s", source)
        source = None

    try:
        report = run_doctor(root, args.uid, source=source)
    except Exception:
        # Honour the caller's contract instead of dying with a traceback
        # mid-batch: run_corpus_batch.sh redirects stderr to the per-CV log and
        # substitutes an 'error' row when we exit non-zero.
        logger.exception("run_doctor failed for uid=%s", args.uid)
        return 1

    findings = report.get("findings", [])
    sev = Counter(f["severity"] for f in findings)
    lints = Counter(f["lint"] for f in findings)
    # Ranked by corpus-relative surprise, not raw count: the ubiquitous lints
    # fire most often AND say least, so most_common(4) spent half the line on
    # them and hid the rare ones that identify this run (#438). Same four slots,
    # same format -- the TSV contract is unchanged.
    top_lints = rank_lints(lints)[:4]
    top = ",".join(f"{k}:{v}" for k, v in top_lints)

    # stdout is the TSV contract (see the docstring): deliberately print(), not a
    # logger call -- run_corpus_batch.sh captures this line with $(...), so
    # routing it through logging (stderr, level-prefixed) would break the caller.
    print(f"{report.get('worst_severity') or 'clean'}\t{sev.get('ERROR', 0)}\t"
          f"{sev.get('WARN', 0)}\t{sev.get('INFO', 0)}\t{top}")

    if out:
        out.write_text(json.dumps(report, default=str, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
