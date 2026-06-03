#!/usr/bin/env python3
"""
Offline corrector replay / eval harness.

Re-runs the deterministic post-classification correctors against real
``<RUNID>_classified.json`` artifacts WITHOUT calling any LLM, and reports every
correction it would make so the operator can audit corrections vs false
positives. This is the eval gate for changes to the correctors in
``core/validators/`` (the repo norm: no accuracy change without a replay).

It does NOT mutate the artifacts -- each entry list is deep-copied before the
correctors run.

Usage
-----
    # populate a dir with the real runs (14 as of writing):
    aws s3 sync s3://wcm-cviche-storage/cviche/runs/ /tmp/cviche_runs/ \
        --exclude '*' --include '*_classified.json'

    PYTHONPATH=src python -m unified_pipeline.eval.replay_correctors /tmp/cviche_runs
    # or point at a single file / the pipeline's local outputs dir.

Exit code is 0 always; this is a reporting tool. Pipe through a reviewer.
"""
import argparse
import copy
import json
import os
import sys
from pathlib import Path

# Match how stage_3b imports its validators: src/unified_pipeline on the path,
# then `from core.validators...`.
_PKG_ROOT = Path(__file__).resolve().parent.parent  # .../src/unified_pipeline
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from core.validators.wcm_table_corrector import apply_wcm_table_corrections  # noqa: E402
from core.validators.prose_mentee_corrector import apply_prose_mentee_corrections  # noqa: E402

CORRECTORS = {
    "wcm_table": apply_wcm_table_corrections,
    "prose_mentee": apply_prose_mentee_corrections,
}


def _load_entries(path: Path):
    data = json.loads(path.read_text())
    if isinstance(data, list):
        return data
    return data.get("entries") or data.get("classified_entries") or []


def _find_files(target: Path):
    if target.is_file():
        return [target]
    return sorted(target.rglob("*_classified.json"))


def _run_id(path: Path) -> str:
    name = path.name
    return name[: -len("_classified.json")] if name.endswith("_classified.json") else name


def replay(files, correctors):
    """Returns {corrector: {run_id: stats}}; never mutates the source entries."""
    results = {name: {} for name in correctors}
    for path in files:
        entries = _load_entries(path)
        rid = _run_id(path)
        for name, fn in correctors.items():
            _, stats = fn(copy.deepcopy(entries))
            results[name][rid] = stats
    return results


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", help="dir of *_classified.json (recursive) or a single file")
    ap.add_argument("--corrector", choices=list(CORRECTORS), action="append",
                    help="limit to specific corrector(s); default = all")
    ap.add_argument("--preview", type=int, default=80, help="chars of text preview per fire (0 to hide)")
    args = ap.parse_args(argv)

    target = Path(args.path)
    if not target.exists():
        ap.error(f"{target} does not exist")
    files = _find_files(target)
    if not files:
        ap.error(f"no *_classified.json found under {target}")

    correctors = {k: CORRECTORS[k] for k in (args.corrector or CORRECTORS)}
    results = replay(files, correctors)

    print(f"Replayed {len(correctors)} corrector(s) over {len(files)} run(s) from {target}\n")
    grand_total = 0
    for name in correctors:
        per_run = results[name]
        total = sum(s["corrections_made"] for s in per_run.values())
        grand_total += total
        fired_runs = {r: s for r, s in per_run.items() if s["corrections_made"]}
        print(f"== {name}: {total} correction(s) across {len(fired_runs)}/{len(files)} run(s) ==")
        for rid, stats in fired_runs.items():
            print(f"  [{rid}] {stats['corrections_made']}:")
            for d in stats["correction_details"]:
                line = f"    {d['original']} -> {d['corrected_to']}  ({d['reason']})"
                if args.preview:
                    prev = (d["text_preview"] or "").replace("\n", " ").replace("\t", " ")
                    line += f"\n        text: {prev[:args.preview]}"
                print(line)
        if not fired_runs:
            print("  (no corrections)")
        print()
    print(f"TOTAL: {grand_total} correction(s). Review each above for false positives.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
