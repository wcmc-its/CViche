#!/usr/bin/env python3
"""Collapse a corpus of stage-4 fields.json to DISTINCT CVs before you quote a
prevalence.

The S3 run corpus is dominated by a handful of synthetic test CVs re-run many
times (measured 2026-07-07: 92 runs = ~12-20 distinct CVs, one CV = 48% of
runs). Counting by run inflates the denominator and distorts "how widespread is
this?" in BOTH directions: a bug in a dup-heavy CV reads as alarmingly common
(44/92), a bug in a rare CV reads as negligible (2/92). Either way the honest
denominator is DISTINCT CVs, not runs.

Point this at a directory of fields.json (flat or `<run>/outputs/<uid>_fields.json`)
and it prints the run->distinct-CV collapse, warns when one CV dominates, and --
given the runs where you found a bug (--hits) -- reports the distinct-CV
prevalence you should actually quote.

Distinct count is a RANGE: owner-name grouping (coarser) to content-hash grouping
(finer, since re-runs of one CV drift slightly). The truth sits between; quote the
range, never the run count. And remember: a rate on this synthetic-dominated
corpus BOUNDS a bug class -- it does not estimate how often real users hit it.

    python3 scripts/corpus_distinct_cvs.py <corpus_dir> [--hits R1,R2,...]
    python3 scripts/corpus_distinct_cvs.py --selftest
"""
import argparse
import glob
import hashlib
import json
import os
import sys
from collections import Counter


def _norm_owner(owner):
    """A stable owner label, or '' when the field is blank/unusable."""
    if isinstance(owner, dict):
        owner = owner.get("name") or owner.get("full_name") or ""
    return " ".join(str(owner or "").split()).strip()


def _content_fp(entries):
    """Fingerprint a CV by its first 40 entry texts (order-stable)."""
    blob = "||".join((e.get("text") or "")[:80] for e in (entries or [])[:40])
    return hashlib.md5(blob.encode("utf-8", "ignore")).hexdigest()[:12]


def _run_id(path):
    """`<run>/outputs/<uid>_fields.json` -> run; else the filename stem."""
    parts = path.replace("\\", "/").split("/")
    if len(parts) >= 3 and parts[-2] == "outputs":
        return parts[-3]
    return os.path.basename(path).split("_fields.json")[0]


def scan(corpus_dir):
    """Return {run_id: {"owner":..., "content_fp":...}} for every fields.json."""
    paths = glob.glob(os.path.join(corpus_dir, "**", "*_fields.json"), recursive=True)
    runs = {}
    for p in paths:
        try:
            d = json.load(open(p))
        except (OSError, json.JSONDecodeError):
            continue
        entries = d.get("entries", []) if isinstance(d, dict) else []
        owner = _norm_owner(d.get("cv_owner") if isinstance(d, dict) else None)
        cfp = _content_fp(entries)
        runs[_run_id(p)] = {"owner": owner or f"<blank:{cfp}>", "content_fp": cfp}
    return runs


def report(runs, hits=None):
    n = len(runs)
    if not n:
        print("no fields.json found under that directory", file=sys.stderr)
        return 1
    by_owner = Counter(r["owner"] for r in runs.values())
    by_content = Counter(r["content_fp"] for r in runs.values())
    d_owner, d_content = len(by_owner), len(by_content)
    top_owner, top_n = by_owner.most_common(1)[0]
    top_share = 100 * top_n / n
    dup2 = sum(c for c in by_owner.values() if c >= 2)

    print(f"{n} runs  ->  ~{min(d_owner, d_content)}-{max(d_owner, d_content)} "
          f"distinct CVs  (by owner: {d_owner}, by content: {d_content})")
    print(f"top CV: {top_owner!r} = {top_n} runs ({top_share:.0f}%)")
    if top_share >= 25 or dup2 / n >= 0.5:
        print(f"\n  !! DUP-DOMINATED: top CV is {top_share:.0f}% of runs; "
              f"{100*dup2/n:.0f}% of runs are a CV seen >=2x.")
        print(f"     Quote prevalence over ~{min(d_owner,d_content)}-"
              f"{max(d_owner,d_content)} distinct CVs, NOT {n} runs.")
        print("     This corpus is synthetic-dominated: a rate here bounds the "
              "class, it does\n     not estimate real-user frequency.")

    print("\ndistinct CVs by run count:")
    for owner, c in by_owner.most_common():
        print(f"  {c:4d}  {owner[:60]}")

    if hits:
        hit_runs = [h for h in hits if h in runs]
        missing = [h for h in hits if h not in runs]
        hit_owners = {runs[h]["owner"] for h in hit_runs}
        print(f"\nHIT PREVALENCE (the number to quote):")
        print(f"  {len(hit_runs)} of {n} runs affected  =>  "
              f"{len(hit_owners)} of {d_owner} distinct CVs "
              f"({100*len(hit_owners)/d_owner:.0f}% of distinct CVs)")
        for o in sorted(hit_owners):
            print(f"    - {o[:60]}")
        if missing:
            print(f"  (note: {len(missing)} --hits run(s) not in corpus: "
                  f"{', '.join(missing)})")
    return 0


def _selftest():
    # 4 runs, 2 distinct owners; owner A run 3x. Bug hits 2 runs -> 1 distinct CV.
    runs = {
        "r1": {"owner": "Alpha", "content_fp": "a1"},
        "r2": {"owner": "Alpha", "content_fp": "a2"},
        "r3": {"owner": "Alpha", "content_fp": "a1"},
        "r4": {"owner": "Beta", "content_fp": "b1"},
    }
    by_owner = Counter(r["owner"] for r in runs.values())
    assert len(by_owner) == 2 and by_owner["Alpha"] == 3
    hits = ["r1", "r3"]  # same CV twice
    hit_owners = {runs[h]["owner"] for h in hits}
    assert len(hit_owners) == 1, "2 hit runs collapse to 1 distinct CV"
    assert _norm_owner({"full_name": "X Y"}) == "X Y"
    assert _norm_owner("  a  b ") == "a b"
    assert _norm_owner(None) == ""
    assert _run_id("x/RUNID/outputs/UID_fields.json") == "RUNID"
    assert _run_id("UID_fields.json") == "UID"
    print("selftest OK")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("corpus_dir", nargs="?", help="dir of *_fields.json")
    ap.add_argument("--hits", help="comma-separated run_ids where the bug appears")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if not a.corpus_dir:
        ap.error("corpus_dir is required (or use --selftest)")
    hits = [h.strip() for h in a.hits.split(",") if h.strip()] if a.hits else None
    return report(scan(a.corpus_dir), hits)


if __name__ == "__main__":
    sys.exit(main())
