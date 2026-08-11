#!/usr/bin/env python3
"""Compare two doctor_gate.py runs. PASS only at zero changed CVs.

    python3 scripts/doctor_gate_compare.py <a.json> <b.json>

Normalises the harness's own work-dir path out of each report before
comparing. run_doctor embeds each artifact's path in its report, so without
this every CV reads as changed and the gate's noise floor is 100% --
indistinguishable from a broken gate. The work dir is named after the output
file (doctor_gate.py: OUT.stem + "_work"), so the normalisation anchors on the
"_work" PATH SUFFIX rather than a specific stem -- an output named anything
other than "doctor-<name>.json" must still normalise cleanly, or a clean run
reads as 100% changed with nothing to distinguish it from a real regression
(issue #584).

The regex ALSO has to work when doctor_gate.py was invoked with a relative
path (e.g. "scratch/doctor_a_work/...", no leading "/"), not just an
absolute one -- an earlier version of this normaliser required a leading "/"
immediately before the work-dir segment, which relative invocations never
have, so relative-path runs silently reported every CV changed (found while
building this gate: running with a relative out path is the natural way to
invoke it from a repo checkout, not an edge case). Verified this does not
make the check inert: flipping UNDER_EXTRACTION_MIN_CHARS from 800 to 2500
on a 66-CV local farm still reported a real, non-zero CHANGED count (13 of
66) -- see docs/guides/render-doctor-gates.md for the full control log. A
farm with real stage_6_docx output (this one has none) is needed to control
lints gated on it, e.g. table_shape -- also documented there.

Fails closed (CODING_STANDARDS Section 5.5) if either arm recorded any
doctor_gate.py failures (the "_failed" key doctor_gate.py writes alongside
its per-uid reports) -- a uid run_doctor() itself crashed on must never be
silently excluded from the comparison as if it had no findings to compare.
"""
import json
import re
import sys

FAILED_KEY = "_failed"


def norm(rep):
    # Matches zero or more leading path segments, then a final segment
    # ending in "_work", then the trailing slash -- works whether the whole
    # thing starts with "/" (absolute) or not (relative). See module
    # docstring for why the leading "/" cannot be required.
    return re.sub(r'/?(?:[^"/]+/)*[^"/]+_work/', '<WORK>/', json.dumps(rep, sort_keys=True))


def _failure_guard_problems(failed_a: dict, failed_b: dict) -> list:
    """Fail-closed guard (issue #584): a uid run_doctor() crashed on must
    never be silently excluded from the comparison as if it had no findings.
    Returns problem strings; empty means clear to compare."""
    problems = []
    if failed_a:
        problems.append(f"arm A: {len(failed_a)} uid(s) run_doctor() crashed on: "
                        f"{' '.join(sorted(failed_a)[:10])}")
    if failed_b:
        problems.append(f"arm B: {len(failed_b)} uid(s) run_doctor() crashed on: "
                        f"{' '.join(sorted(failed_b)[:10])}")
    return problems


def main():
    a = json.load(open(sys.argv[1]))
    b = json.load(open(sys.argv[2]))

    failed_a, failed_b = a.pop(FAILED_KEY, {}), b.pop(FAILED_KEY, {})
    problems = _failure_guard_problems(failed_a, failed_b)
    if problems:
        print("DOCTOR-GATE FAILURE GUARD TRIPPED -- refusing to compare:")
        for p in problems:
            print(f"  {p}")
        print("\nFAIL - fix the failing arm(s) and re-run before comparing")
        return 1

    missing = set(a) ^ set(b)
    diff = [u for u in a if u in b and norm(a[u]) != norm(b[u])]
    print(f"compared {len(a)} CVs; identical {len(a) - len(diff)}; CHANGED {len(diff)}")
    if missing:
        print(f"UID SET DIFFERS: {sorted(missing)[:10]}")
    ok = not diff and not missing
    print("PASS - no doctor finding changed" if ok else f"FAIL {diff[:8]}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
