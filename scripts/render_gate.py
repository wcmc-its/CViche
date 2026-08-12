#!/usr/bin/env python3
"""Render every corpus CV through stage 6 from one code arm, into a fresh out_dir.

    PYTHONPATH=<arm>/src python3 scripts/render_gate.py <arm_outputs_dir> <out_dir> [uid ...]
    PYTHONPATH=<arm>/src python3 scripts/render_gate.py <arm_outputs_dir> <out_dir> --uids-file FILE

--uids-file reads one uid per line instead of positional args: this corpus's
uids are derived from real filenames and routinely contain spaces and
parentheses ("038WKA_Jonathan Nahmias CV (Updated 1-26-26)"), which shell
word-splitting on a $(...) uid list silently mangles into unmatched
fragments -- the run then just renders fewer uids than asked, with no error.

<arm_outputs_dir> is a stage-output tree shaped like src/unified_pipeline/outputs/
(stage_4_field_extraction/, stage_5d_citation_formatted/, etc.) -- either that
directory itself, or a farm built by symlinking/copying it from elsewhere. LLM
calls are neutralised so the render is deterministic; both call sites in stage 6
are try/except guarded, so raising is safe.

Companion: render_gate_compare.py, which reads two out_dirs (or one twice, as a
determinism control) and is the thing that actually passes or fails.

Fixes two defects found in an earlier, uncommitted version of this script
(docs/analysis/HANDOFF-wave1-completion-2026-08-11.md, issue #584):

1. FAILS OPEN ON A CRASHED RENDER. The old version did `OUT.mkdir(exist_ok=True)`
   and never cleared the directory -- re-rendering into a reused out_dir after a
   change that crashes every CV left the *previous* arm's docx files in place,
   and the compare step (seeing files that matched) reported PASS on a render
   that produced zero real output. Fixed: OUT is wiped before every run, and
   this script itself exits non-zero if any render failed (CODING_STANDARDS
   Section 5.5 -- a gate fails closed, "nothing to compare" is a failure).
2. DATE-SENSITIVE. Stage 6 stamps `Date of preparation: {datetime.now()}` (and
   two more datetime.now() call sites reclassify content by calendar date), so
   two arms rendered on different days report differences that are not a
   regression. Not fixable in the renderer -- the operational rule is: render
   both arms on the SAME day, and if a gate run crosses midnight, re-render the
   reference rather than trusting a stale one. render_gate_compare.py's
   fingerprint check masks date text for exactly this reason.
"""
import argparse
import json
import shutil
import sys
import tempfile
import traceback
from pathlib import Path

PRECEDENCE = [
    ("stage_5d_citation_formatted", "{uid}_citation_formatted.json"),
    ("stage_5c_teaching_formatted", "{uid}_teaching_formatted.json"),
    ("stage_5b_institution_enrichment", "{uid}_institution_enriched.json"),
    ("stage_5_enrichment", "{uid}_enriched.json"),
    ("stage_4_field_extraction", "{uid}_fields.json"),
]


def _parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("arm_outputs", type=Path, help="stage-output tree to render from")
    parser.add_argument("out", type=Path, help="output directory (wiped and recreated)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("uids", nargs="*", default=[], help="uids to render (default: all)")
    group.add_argument("--uids-file", type=Path, default=None,
                        help="file with one uid per line, instead of positional uids")
    args = parser.parse_args(argv)
    if not (args.arm_outputs / "stage_4_field_extraction").is_dir():
        parser.error(f"no stage_4_field_extraction under {args.arm_outputs} -- point this at "
                     f"a stage-output tree, not a repo root")
    return args


def _atomic_write_json(path: Path, obj) -> None:
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2)
        Path(tmp_name).replace(path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def main(argv=None):
    args = _parse_args(argv)
    arm_outputs, out = args.arm_outputs, args.out
    only = ({line.strip() for line in args.uids_file.read_text(encoding="utf-8").splitlines() if line.strip()}
            if args.uids_file else set(args.uids))

    # Fresh directory every run -- see defect 1 above. shutil.rmtree on our own
    # output directory, never on anything the caller didn't name explicitly.
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)

    import unified_pipeline.stage_6_word_template as s6

    def _no_llm(*a, **kw):
        raise RuntimeError("render_gate: LLM disabled for determinism")

    s6.call_llm = _no_llm

    uids = sorted({p.name.replace("_fields.json", "")
                   for p in (arm_outputs / "stage_4_field_extraction").glob("*_fields.json")})
    if only:
        uids = [u for u in uids if u in only]
    if not uids:
        print("no uids to render -- empty arm, or uid filter matched nothing", file=sys.stderr)
        return 1

    results = {}
    for i, uid in enumerate(uids, 1):
        src = None
        for stage_dir, pat in PRECEDENCE:
            cand = arm_outputs / stage_dir / pat.format(uid=uid)
            # is_file(), not exists() -- run_stage6 expects a file; exists()
            # would also accept a directory of the same name and hand it to
            # the reader instead of falling through PRECEDENCE (review on #589).
            if cand.is_file():
                src = cand
                break
        if src is None:
            results[uid] = {"error": "no input artifact"}
            continue
        dest = out / f"{uid}_wcm.docx"
        try:
            s6.run_stage6(input_path=str(src), output_path=str(dest), verbose=False)
            # A renderer that returns without raising and without writing the
            # output file is not a successful render -- "no exception" is not
            # "rendered" (review on #589, same fail-closed guarantee this
            # module's docstring claims for defect 1).
            if not dest.is_file():
                results[uid] = {"error": "run_stage6 returned without writing an output file"}
            else:
                results[uid] = {"input": src.name}
        except Exception as exc:
            results[uid] = {"error": f"{type(exc).__name__}: {exc}",
                            "tb": traceback.format_exc()[-800:]}
        print(f"[{i}/{len(uids)}] {uid} "
              f"{'OK' if 'error' not in results[uid] else results[uid]['error']}", flush=True)

    _atomic_write_json(out / "_render_index.json", results)
    bad = sorted(u for u, r in results.items() if "error" in r)
    print(f"\nrendered={len(results) - len(bad)} failed={len(bad)}")
    if bad:
        print("failed uids:", " ".join(bad))
        return 1  # defect 1: a failed render must never look like a clean arm
    return 0


if __name__ == "__main__":
    sys.exit(main())
