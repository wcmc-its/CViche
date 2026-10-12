#!/usr/bin/env python3
"""Run run_doctor over every corpus CV from one code arm, into a findings JSON.

    PYTHONPATH=<arm>/src python3 scripts/doctor_gate.py <farm> <out.json> [--source-dir DIR]

<farm> is a flat stage_* tree (see render_gate.py's docstring for the shape),
which is not the per-run layout run_doctor expects, so each uid gets a symlink
view built for it under <out.json's parent>/<out.json stem>_work/<uid>/.
Deterministic -- no LLM, no network; run_doctor only reads artifacts.

Fixes a defect found in an earlier, uncommitted version of this script (issue
#584): it is BLIND TO SOURCE-DOCX LINTS without --source-dir. run_doctor's
segmentation lints need the original .docx to compare against; a farm built
from JSON stage outputs alone has none, so both segmentation lints silently
skip on every uid -- on the 100-CV farm this was 200 of 439 baseline
findings, invisible to any gate built without this flag. Point --source-dir
at the directory holding <uid>*.docx (e.g. data/sample_cvs/word/web_harvest/)
to make them live.

Not fixed here, and not fixable at this layer: lint_pipeline_errors never
fires on this farm because none of its CVs represent a failed run -- it needs
a synthetic failed-run fixture, which belongs in run_doctor's own test suite
(src/unified_pipeline/tests/test_run_doctor.py), not in a corpus gate.
"""
import argparse
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("farm", type=Path, help="flat stage_* tree to doctor")
    parser.add_argument("out", type=Path, help="findings JSON to write")
    parser.add_argument("--source-dir", type=Path, default=None,
                         help="directory holding <uid>*.docx (see docstring)")
    args = parser.parse_args(argv)
    if not args.farm.is_dir():
        parser.error(f"farm is not a directory: {args.farm}")
    if args.source_dir is not None and not args.source_dir.is_dir():
        # Fail closed (CODING_STANDARDS 5.5): a bad --source-dir must not
        # silently disable every segmentation lint the way a missing one
        # does when the flag is simply omitted -- omitting the flag is a
        # documented, intentional mode; a typo'd path is not.
        parser.error(f"--source-dir is not a directory: {args.source_dir}")
    return args


def _atomic_write_json(path: Path, obj) -> None:
    """Write JSON via a temp sibling + rename so a crash mid-write can never
    leave downstream tooling reading a truncated report (review on #589)."""
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1, sort_keys=True)
        Path(tmp_name).replace(path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def main(argv=None):
    args = _parse_args(argv)
    farm, out, source_dir = args.farm, args.out, args.source_dir
    work = out.parent / (out.stem + "_work")

    from unified_pipeline.run_doctor import _uid_owns, run_doctor  # noqa: E402

    # Fresh WORK every run -- an earlier invocation's symlink views must not
    # leak into this one. A uid dropped from the current farm (or renamed)
    # would otherwise leave a stale symlink tree that run_doctor happily
    # reads, making the gate's result depend on what a PREVIOUS run built,
    # not just the farm passed on this invocation (review on #589).
    shutil.rmtree(work, ignore_errors=True)

    uids = sorted(p.name.replace("_fields.json", "")
                  for p in (farm / "stage_4_field_extraction").glob("*_fields.json"))

    reports, failed = {}, {}
    for i, uid in enumerate(uids, 1):
        root = work / uid
        for stage in sorted(d for d in farm.iterdir() if d.is_dir()):
            # _uid_owns, not a bare prefix match -- glob(f"{uid}*") matches a
            # LONGER uid that starts with this one (web05 also matches
            # web050_entries.json); run_doctor's own docstring on this
            # helper cites the 2026-07-15 sweep this exact bug caused (3 of
            # 25 CVs doctored against the wrong artifacts).
            hits = sorted(p for p in stage.glob(f"{uid}*") if _uid_owns(p.name, uid))
            if not hits:
                continue
            (root / stage.name).mkdir(parents=True, exist_ok=True)
            for src in hits:
                link = root / stage.name / src.name
                link.unlink(missing_ok=True)
                link.symlink_to(src.resolve())
        source_path = None
        if source_dir:
            docx_hits = sorted(p for p in source_dir.glob(f"{uid}*.docx") if _uid_owns(p.name, uid))
            if docx_hits:
                source_path = docx_hits[0]
        try:
            reports[uid] = run_doctor(root, uid, source=source_path)
        except Exception as e:
            failed[uid] = f"{type(e).__name__}: {e}"
            traceback.print_exc()
        if i % 25 == 0:
            print(f"  [{i}/{len(uids)}]", flush=True)

    # Failures ride along in the output (top-level "_failed" key) rather than
    # only being printed, so doctor_gate_compare.py can fail closed on them the
    # same way render_gate_compare.py does on render_gate.py's failures --
    # "nothing to compare for this uid" must never look like CHANGED 0.
    _atomic_write_json(out, {**reports, "_failed": failed})
    n_find = sum(len(r.get("findings", [])) for r in reports.values())
    source_note = (f", source-linked={sum(1 for u in uids if source_dir and list(source_dir.glob(f'{u}*.docx')))}"
                   if source_dir else " (no --source-dir: segmentation lints are blind on every uid)")
    print(f"doctored={len(reports)} failed={len(failed)} findings={n_find}{source_note}")
    if failed:
        print("FAILURES:", json.dumps(failed, indent=1))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
