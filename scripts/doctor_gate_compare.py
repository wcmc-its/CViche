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

The path scrub only touches the two fields run_doctor's report actually uses
for filesystem paths -- `root` and `artifacts` (see run_doctor()'s return
shape) -- not the whole serialized report. A finding's `message` or
`evidence` text is comparison-relevant CONTENT: scrubbing a work-dir-shaped
substring out of it, rather than out of an actual path field, could rewrite
a real regression into looking identical (review on #589).

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
import argparse
import json
import re
import sys

FAILED_KEY = "_failed"

# Matches zero or more leading path segments, then a final segment ending in
# "_work", then the trailing slash -- works whether the whole thing starts
# with "/" (absolute) or not (relative). See module docstring for why the
# leading "/" cannot be required. Applied to a raw path string (not a JSON-
# encoded one), so no need to exclude '"'.
_WORK_RE = re.compile(r'/?(?:[^/]+/)*[^/]+_work/')


def _scrub_work_dir(value):
    if not isinstance(value, str):
        return value
    return _WORK_RE.sub('<WORK>/', value)


def norm(rep):
    """Canonical JSON for rep with the harness's own work-dir path scrubbed
    out of `root` and `artifacts` -- the only two fields run_doctor's report
    uses for filesystem paths. See module docstring."""
    scrubbed = dict(rep) if isinstance(rep, dict) else rep
    if isinstance(scrubbed, dict):
        if "root" in scrubbed:
            scrubbed["root"] = _scrub_work_dir(scrubbed["root"])
        if isinstance(scrubbed.get("artifacts"), dict):
            scrubbed["artifacts"] = {k: _scrub_work_dir(v) for k, v in scrubbed["artifacts"].items()}
    return json.dumps(scrubbed, sort_keys=True)


def _failure_guard_problems(failed_a, failed_b) -> list:
    """Fail-closed guard (issue #584): a uid run_doctor() crashed on must
    never be silently excluded from the comparison as if it had no findings.
    Returns problem strings; empty means clear to compare.

    Validates the shape too (review on #589): a malformed "_failed" value
    (not a dict) must be a loud gate failure, not a TypeError/AttributeError
    from len()/sorted() below or a comparison that quietly treats it as
    empty."""
    problems = []
    for label, failed in (("A", failed_a), ("B", failed_b)):
        if not isinstance(failed, dict):
            problems.append(f"arm {label}: \"_failed\" is not an object "
                            f"(got {type(failed).__name__}) -- malformed doctor_gate.py output")
            continue
        if failed:
            problems.append(f"arm {label}: {len(failed)} uid(s) run_doctor() crashed on: "
                            f"{' '.join(sorted(failed)[:10])}")
    return problems


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("a", help="first doctor_gate.py output JSON")
    parser.add_argument("b", help="second doctor_gate.py output JSON")
    args = parser.parse_args(argv)

    with open(args.a, encoding="utf-8") as f:
        a = json.load(f)
    with open(args.b, encoding="utf-8") as f:
        b = json.load(f)

    failed_a, failed_b = a.pop(FAILED_KEY, {}), b.pop(FAILED_KEY, {})
    problems = _failure_guard_problems(failed_a, failed_b)
    if problems:
        print("DOCTOR-GATE FAILURE GUARD TRIPPED -- refusing to compare:")
        for p in problems:
            print(f"  {p}")
        print("\nFAIL - fix the failing arm(s) and re-run before comparing")
        return 1

    missing = set(a) ^ set(b)
    common = set(a) & set(b)
    diff = [u for u in common if norm(a[u]) != norm(b[u])]
    print(f"compared {len(common)} CVs; identical {len(common) - len(diff)}; CHANGED {len(diff)}")
    if missing:
        print(f"UID SET DIFFERS: {sorted(missing)[:10]}")
    ok = not diff and not missing and bool(common)
    if not common:
        print("FAIL - no comparable CVs (empty UID intersection)")
    else:
        print("PASS - no doctor finding changed" if ok else f"FAIL {diff[:8]}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
