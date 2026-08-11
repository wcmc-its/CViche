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
import json
import sys
import traceback
from pathlib import Path

FARM = Path(sys.argv[1])
OUT = Path(sys.argv[2])
SOURCE_DIR = None
if "--source-dir" in sys.argv:
    SOURCE_DIR = Path(sys.argv[sys.argv.index("--source-dir") + 1])
WORK = OUT.parent / (OUT.stem + "_work")

from unified_pipeline.run_doctor import run_doctor  # noqa: E402

uids = sorted(p.name.replace("_fields.json", "")
              for p in (FARM / "stage_4_field_extraction").glob("*_fields.json"))

reports, failed = {}, {}
for i, uid in enumerate(uids, 1):
    root = WORK / uid
    for stage in sorted(d for d in FARM.iterdir() if d.is_dir()):
        hits = sorted(stage.glob(f"{uid}*"))
        if not hits:
            continue
        (root / stage.name).mkdir(parents=True, exist_ok=True)
        for src in hits:
            link = root / stage.name / src.name
            link.unlink(missing_ok=True)
            link.symlink_to(src.resolve())
    source_path = None
    if SOURCE_DIR:
        docx_hits = sorted(SOURCE_DIR.glob(f"{uid}*.docx"))
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
OUT.write_text(json.dumps({**reports, "_failed": failed}, indent=1, sort_keys=True))
n_find = sum(len(r.get("findings", [])) for r in reports.values())
source_note = f", source-linked={sum(1 for u in uids if SOURCE_DIR and list(SOURCE_DIR.glob(f'{u}*.docx')))}" if SOURCE_DIR else " (no --source-dir: segmentation lints are blind on every uid)"
print(f"doctored={len(reports)} failed={len(failed)} findings={n_find}{source_note}")
if failed:
    print("FAILURES:", json.dumps(failed, indent=1))
    sys.exit(1)
