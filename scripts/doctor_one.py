#!/usr/bin/env python3
"""Run run_doctor on a single local pipeline run; print a one-line TSV summary.

    PYTHONPATH=src python3 scripts/doctor_one.py <outputs_root> <uid> [source_docx] [findings_json_out] [--metrics-tsv PATH]

<outputs_root> is the dir holding the stage_* subdirs (i.e. src/unified_pipeline/outputs).
If findings_json_out is given, the full run_doctor dict is written there.

Interface contract (run_corpus_batch.sh --doctor consumes this via command
substitution, and standalone to (re)doctor any local run):

  stdout  ONE machine-readable TSV line -- the program's output, not a log:
            worst<TAB>nERROR<TAB>nWARN<TAB>nINFO<TAB>top_lints
  stderr  diagnostics only (the batch runner appends these to the per-CV log)
  exit 1  the doctor could not run; the caller substitutes an 'error' row

--metrics-tsv PATH (#816) APPENDS one fixed-column row of run_doctor()'s
`metrics` block to PATH -- a batch-trend number per run, distinct from the
findings above. Fixed columns (not `key=value` pairs) so the file opens
directly as a spreadsheet the way summary.tsv/scores.tsv/doctor.tsv already
do; a header row is the CALLER's job (run_corpus_batch.sh creates it once,
same as those three), not this script's -- this only ever appends a data
row, so two processes racing to create the header can't both win. A metric
absent from this run's `metrics` dict (an artifact this run lacks, or a
denominator of 0) is an empty cell, not a 0 -- 0 is a real, different value
for several of these (e.g. a fallback ratio of exactly 0.0). This does NOT
change the stdout contract above: still one line, unchanged columns.
"""
import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from unified_pipeline.run_doctor import rank_lints, run_doctor

logger = logging.getLogger(__name__)

#: Column order for --metrics-tsv, after `uid`. Fixed so the file can be
#: joined/plotted by position across CVs and across runs of this script.
METRICS_TSV_COLUMNS = (
    "appendix_entries", "appendix_share",
    "honors_malformed_rows", "honors_rows",
    "unrouted_code_entries", "source_coverage_pct",
    "stage3b_fallback_ratio", "t_validation_yield",
    "fragment_reconnection_yield", "total_post_corrections",
)


def _metrics_tsv_row(uid: str, metrics: dict) -> str:
    """One TSV row for `metrics` (#816): `uid` then METRICS_TSV_COLUMNS in
    order, missing values as an empty cell. `unrouted_code_entries` is a
    {code: n} dict -- the only non-scalar metric -- serialized as
    `code=n;code=n`, sorted by code, so the row still has exactly one value
    per column; the full dict is still in the JSON findings_json_out."""
    cells = [uid]
    for col in METRICS_TSV_COLUMNS:
        value = metrics.get(col)
        if value is None:
            cells.append("")
        elif isinstance(value, dict):
            cells.append(";".join(f"{k}={v}" for k, v in sorted(value.items())))
        else:
            cells.append(str(value))
    return "\t".join(cells)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("outputs_root", help="dir holding the stage_* subdirs")
    ap.add_argument("uid", help="document uid to doctor")
    ap.add_argument("source_docx", nargs="?", default=None,
                    help="original source .docx; enables the source-dependent lints")
    ap.add_argument("findings_json_out", nargs="?", default=None,
                    help="write the full run_doctor dict here as JSON")
    ap.add_argument("--metrics-tsv", default=None,
                    help="append one row of run_doctor()'s metrics block here (#816)")
    args = ap.parse_args(argv)

    root = Path(args.outputs_root)
    if not root.is_dir():
        ap.error(f"outputs_root is not a directory: {root}")

    # Validate the out dir BEFORE doctoring: the write happens last, so a typo'd
    # path would otherwise discard the whole report.
    out = Path(args.findings_json_out) if args.findings_json_out else None
    if out and not out.parent.is_dir():
        ap.error(f"findings_json_out directory does not exist: {out.parent}")

    metrics_tsv = Path(args.metrics_tsv) if args.metrics_tsv else None
    if metrics_tsv and not metrics_tsv.parent.is_dir():
        ap.error(f"--metrics-tsv directory does not exist: {metrics_tsv.parent}")

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

    if metrics_tsv:
        row = _metrics_tsv_row(args.uid, report.get("metrics") or {})
        with open(metrics_tsv, "a", encoding="utf-8") as f:
            f.write(row + "\n")

    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(name)s: %(message)s")
    sys.exit(main())
