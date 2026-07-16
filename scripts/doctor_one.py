#!/usr/bin/env python3
"""Run run_doctor on a single local pipeline run; print a one-line TSV summary.

    PYTHONPATH=src python3 scripts/doctor_one.py <outputs_root> <uid> [source_docx] [findings_json_out]

<outputs_root> is the dir holding the stage_* subdirs (i.e. src/unified_pipeline/outputs).
Prints to stdout:  worst<TAB>nERROR<TAB>nWARN<TAB>nINFO<TAB>top_lints
If findings_json_out is given, the full run_doctor dict is written there.

Used by run_corpus_batch.sh --doctor, and standalone to (re)doctor any local run.
"""
import json
import sys
from pathlib import Path
from collections import Counter

from unified_pipeline.run_doctor import run_doctor

root = Path(sys.argv[1])
uid = sys.argv[2]
source = Path(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3] else None
out = sys.argv[4] if len(sys.argv) > 4 else None

r = run_doctor(root, uid, source=source if (source and source.exists()) else None)
findings = r.get("findings", [])
sev = Counter(f["severity"] for f in findings)
lints = Counter(f["lint"] for f in findings)
top = ",".join(f"{k}:{v}" for k, v in lints.most_common(4))

print(f"{r.get('worst_severity') or 'clean'}\t{sev.get('ERROR', 0)}\t{sev.get('WARN', 0)}\t{sev.get('INFO', 0)}\t{top}")
if out:
    Path(out).write_text(json.dumps(r, default=str, indent=1))
