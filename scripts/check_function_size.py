#!/usr/bin/env python3
"""Ratchet on oversized functions: the number may fall, never rise.

Gates CODING_STANDARDS.md section 3, which is otherwise all judgement rules and
therefore unenforceable. One number, checked in, compared on every run.

The metric is *excess* lines -- sum of max(0, length - THRESHOLD) over every
function -- not a count of large functions. Count is perverse: decomposing one
956-line function into five 200-line ones would make a count-based gate fail.
Excess falls whenever a function is genuinely split and rises only when new
oversized code appears, which is the incentive we want.

    python3 scripts/check_function_size.py            # check against baseline
    python3 scripts/check_function_size.py --report   # show the worst offenders
    python3 scripts/check_function_size.py --update   # re-baseline (only downward)

ponytail: one global total, so an improvement in file A can mask a regression in
file B. Per-file baselines are the upgrade if that ever actually happens -- they
cost a merge conflict on every touched file, which is why they are not here yet.
"""

import argparse
import ast
import json
import os
import sys

THRESHOLD = 200
# Scan and baseline are both anchored to this file, never to cwd: a run from
# anywhere must measure the tree the baseline was taken from, or it silently
# compares one repo's functions against another repo's number.
SCRIPTS = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(SCRIPTS)
BASELINE = os.path.join(SCRIPTS, "function-size-baseline.json")
SKIP_DIRS = {".git", "__pycache__", "node_modules", "outputs", "uploads", ".venv", "venv", "archive"}


def scan(root=ROOT):
    """Every non-test function at or over THRESHOLD lines, longest first."""
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if not name.endswith(".py") or name.startswith("test_"):
                continue
            path = os.path.relpath(os.path.join(dirpath, name), root)
            if "tests" in path.split(os.sep):
                continue
            try:
                with open(os.path.join(root, path), encoding="utf-8", errors="replace") as fh:
                    tree = ast.parse(fh.read())
            except (SyntaxError, ValueError):
                continue  # not importable anyway; not this gate's problem
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    length = getattr(node, "end_lineno", node.lineno) - node.lineno + 1
                    if length >= THRESHOLD:
                        found.append((length, node.name, path, node.lineno))
    found.sort(reverse=True)
    return found


def totals(found):
    return {
        "threshold": THRESHOLD,
        "count": len(found),
        "total_lines": sum(f[0] for f in found),
        "excess_lines": sum(f[0] - THRESHOLD for f in found),
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--report", action="store_true", help="list the worst offenders and exit 0")
    ap.add_argument("--update", action="store_true", help="write a new baseline (refuses to regress)")
    args = ap.parse_args()

    found = scan()
    now = totals(found)

    if args.report:
        print(f"functions >= {THRESHOLD} lines: {now['count']}  "
              f"total {now['total_lines']}  excess {now['excess_lines']}\n")
        for length, name, path, line in found[:25]:
            print(f"  {length:5}  {name:45} {path}:{line}")
        return 0

    try:
        with open(BASELINE, encoding="utf-8") as fh:
            base = json.load(fh)
    except FileNotFoundError:
        if not args.update:
            print(f"no baseline at {BASELINE}; run with --update to create it", file=sys.stderr)
            return 2
        base = None

    if args.update:
        if base and now["excess_lines"] > base["excess_lines"]:
            print(f"refusing to raise the baseline: excess {base['excess_lines']} -> "
                  f"{now['excess_lines']}. The ratchet only turns one way.", file=sys.stderr)
            return 1
        with open(BASELINE, "w", encoding="utf-8") as fh:
            json.dump(now, fh, indent=2, sort_keys=True)
            fh.write("\n")
        was = base["excess_lines"] if base else "-"
        print(f"baseline updated: excess {was} -> {now['excess_lines']}")
        return 0

    if now["excess_lines"] > base["excess_lines"]:
        delta = now["excess_lines"] - base["excess_lines"]
        print(f"FAIL: oversized-function debt grew by {delta} lines "
              f"({base['excess_lines']} -> {now['excess_lines']}).", file=sys.stderr)
        print("\nSplit the function, or if it genuinely should stay whole, say why in the PR "
              "and re-baseline with --update.\nWorst offenders now:", file=sys.stderr)
        for length, name, path, line in found[:5]:
            print(f"  {length:5}  {name:45} {path}:{line}", file=sys.stderr)
        return 1

    if now["excess_lines"] < base["excess_lines"]:
        print(f"excess {base['excess_lines']} -> {now['excess_lines']} "
              f"({base['excess_lines'] - now['excess_lines']} lines better). "
              f"Run --update to lock it in.")
        return 0

    print(f"OK: excess {now['excess_lines']} lines across {now['count']} functions "
          f">= {THRESHOLD} lines (unchanged)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
