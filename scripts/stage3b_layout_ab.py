#!/usr/bin/env python3
"""Live A/B of stage 3b's per-CV prompt layout (#50) against the default one.

Arm A runs stage 3b with CVICHE_STAGE3B_PER_CV_PROMPT unset (the default
layout: one system prompt per hierarchy group). Arm B sets it to "1" (one
shared, cacheable system prompt per CV). Both arms read the same stored
stage-2/3a artifacts, so only the prompt layout differs.

    estimate <run_dir>... [--rolls N]   no LLM calls: cost of `run`, from the
                                        run's own prompt logs
    run <run_dir>... --out DIR [--rolls N]   PAID: N rolls per arm per run
    compare --out DIR                   classification shift + cache tokens

A <run_dir> holds outputs/<UID>_entries.json, outputs/<UID>_header_taxonomy.json
and, for `estimate`, prompt_logs/ (the S3 run-artifact layout). `compare`
prints uids, entry keys and taxonomy codes only, never entry text.

Pass bar (the #1021 lesson, reverted by #1089): a layout change is accepted
only when arm B's disagreement with arm A is at arm A's own roll-to-roll noise
level, with no category that moves together (stable flips), and no new
fallback-coded entries.
"""

import argparse
import collections
import contextlib
import io
import json
import math
import os
import re
import statistics
import sys
from itertools import combinations
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

FLAG = "CVICHE_STAGE3B_PER_CV_PROMPT"
ARMS = {"A": "0", "B": "1"}
STAGE = "stage_3b"
_CALL_LOG = re.compile(rf"_{STAGE}_[0-9a-f]{{12}}(_RESPONSE)?\.json$")
# Groups classified at once (STAGE3B_GROUP_WORKERS default): each one's first
# call can miss the shared cache before the first write lands.
CONCURRENT_FIRST_CALLS = 4


def _price(input_tokens: float = 0, cache_read: float = 0, cache_write: float = 0) -> float:
    """USD for system-prompt tokens on stage 3b's configured model (config.py PRICING)."""
    from unified_pipeline.config import calculate_cost, get_stage_config

    return calculate_cost(input_tokens, 0, model=get_stage_config(STAGE)["model"],
                          cache_read_tokens=cache_read, cache_write_tokens=cache_write)


def _uid(run_dir: Path) -> str:
    return next((run_dir / "outputs").glob("*_header_taxonomy.json")).name.split("_")[0]


def _stage_paths(run_dir: Path, uid: str) -> tuple[str, str]:
    outputs = run_dir / "outputs"
    return str(outputs / f"{uid}_entries.json"), str(outputs / f"{uid}_header_taxonomy.json")


def _logged_3b_calls(run_dir: Path) -> list[tuple[dict, dict]]:
    """(request, response) pairs of the run's logged stage-3b calls."""
    requests, responses = {}, {}
    for path in (run_dir / "prompt_logs").glob(f"*_{STAGE}_*.json"):
        if not _CALL_LOG.search(path.name):
            continue  # e.g. stage_3b_block_coherence, a different purpose
        record = json.loads(path.read_text())
        target = responses if path.name.endswith("_RESPONSE.json") else requests
        target[record["log_id"]] = record
    return [(requests[k], responses[k]["response"]) for k in requests if k in responses]


def _per_cv_system_chars(run_dir: Path, uid: str) -> tuple[int, int]:
    """(chars of the per-CV system prompt, classification calls) for one run,
    built by the real code from the run's stage-2/3a artifacts. No LLM call."""
    from unified_pipeline import stage_3b_entry_classifier as s3b
    from unified_pipeline.stage3b.classify import CLASSIFY_BATCH_SIZE

    stage_2, stage_3a = _stage_paths(run_dir, uid)
    entries, _ = s3b.load_stage_2_entries(stage_2)
    mapping_index = s3b.build_mapping_index(s3b.load_stage_3a_mappings(stage_3a).get("mappings", []))
    groups = s3b.group_entries_by_hierarchy(entries)
    os.environ[FLAG] = "1"
    try:
        prompts = s3b._shared_prompts(groups, mapping_index, s3b.load_taxonomy())
    finally:
        os.environ.pop(FLAG)
    calls = sum(math.ceil(len(g) / CLASSIFY_BATCH_SIZE) for g in groups.values())
    return len(next(iter(prompts.values())).system_prompt), calls


def estimate_run(run_dir: Path) -> dict:
    """One roll's cost per arm. Arm A is the run's logged stage-3b cost. Arm B
    swaps each classification call's logged system-prompt cost for the shared
    prompt's: CONCURRENT_FIRST_CALLS cache writes, then cache reads. Tokens per
    character are calibrated on the run's own cache reads and writes."""
    uid = _uid(run_dir)
    calls = _logged_3b_calls(run_dir)
    cached = [(q, r) for q, r in calls
              if r["usage"].get("cache_read_tokens") or r["usage"].get("cache_write_tokens")]
    tokens_per_char = statistics.median(
        (r["usage"]["cache_read_tokens"] or r["usage"]["cache_write_tokens"])
        / len(q["messages"][0]["content"]) for q, r in cached) if cached else 0.25
    arm_a = sum(r.get("cost") or 0.0 for _, r in calls)
    old_system = 0.0
    for q, r in calls:
        if not q["messages"][-1]["content"].startswith("Classify these "):
            continue  # T-validation / fragment passes: same prompt in both arms
        usage = r["usage"]
        old_system += _price(cache_read=usage.get("cache_read_tokens", 0),
                             cache_write=usage.get("cache_write_tokens", 0))
        if not (usage.get("cache_read_tokens") or usage.get("cache_write_tokens")):
            old_system += _price(input_tokens=len(q["messages"][0]["content"]) * tokens_per_char)
    chars, n_calls = _per_cv_system_chars(run_dir, uid)
    system_tokens = chars * tokens_per_char
    writes = min(CONCURRENT_FIRST_CALLS, n_calls)
    new_system = _price(cache_write=system_tokens * writes, cache_read=system_tokens * (n_calls - writes))
    return {"uid": uid, "arm_a": arm_a, "arm_b": arm_a - old_system + new_system,
            "calls": n_calls, "per_cv_system_chars": chars}


def _tallying(call_llm, tally: collections.Counter):
    """Wrap call_llm so a roll records the cache and cost numbers it was billed."""
    def wrapped(*args, **kwargs):
        result = call_llm(*args, **kwargs)
        for key in ("prompt_tokens", "completion_tokens", "cache_read_tokens", "cache_write_tokens", "cost"):
            tally[key] += result.get(key) or 0
        tally["calls"] += 1
        return result
    return wrapped


def run_roll(run_dir: Path, arm: str, out_dir: Path) -> dict:
    """PAID: one stage-3b run of one arm; writes classified.json + usage.json."""
    from unified_pipeline import stage_3b_entry_classifier as s3b
    from unified_pipeline.stage3b import classify

    uid = _uid(run_dir)
    stage_2, stage_3a = _stage_paths(run_dir, uid)
    out_dir.mkdir(parents=True, exist_ok=True)
    tally = collections.Counter()
    real = classify.call_llm
    classify.call_llm = _tallying(real, tally)
    os.environ[FLAG] = ARMS[arm]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            s3b.run_stage_3b(uid, stage_2_path=stage_2, stage_3a_path=stage_3a, output_dir=str(out_dir))
    finally:
        classify.call_llm = real
        os.environ.pop(FLAG, None)
    usage = dict(tally)
    (out_dir / "usage.json").write_text(json.dumps(usage, indent=1))
    return usage


def entry_codes(classified: dict) -> dict[tuple, str]:
    """Entry key -> taxonomy code. The key is the entry's source span, with an
    ordinal for the rare entries that share one."""
    codes, seen = {}, collections.Counter()
    for entry in classified["entries"]:
        span = (entry.get("element_type"), entry.get("element_idx_start"), entry.get("element_idx_end"))
        seen[span] += 1
        codes[span + (seen[span],)] = entry.get("taxonomy_code")
    return codes


def disagreements(a: dict[tuple, str], b: dict[tuple, str]) -> int:
    return sum(1 for key in a.keys() | b.keys() if a.get(key) != b.get(key))


def stable_flips(arm_a: list[dict], arm_b: list[dict]) -> collections.Counter:
    """(code in every A roll, code in every B roll) -> entries, for entries each
    arm codes the same way on every roll but the two arms code differently."""
    flips = collections.Counter()
    keys = set().union(*arm_a, *arm_b)
    for key in keys:
        a_codes = {roll.get(key) for roll in arm_a}
        b_codes = {roll.get(key) for roll in arm_b}
        if len(a_codes) == 1 and len(b_codes) == 1 and a_codes != b_codes:
            flips[(a_codes.pop(), b_codes.pop())] += 1
    return flips


def compare_uid(rolls: dict[str, list[dict]]) -> dict:
    """Noise floor (A vs A), cross-arm disagreement (A vs B) and stable flips."""
    arm_a = [entry_codes(c) for c in rolls["A"]]
    arm_b = [entry_codes(c) for c in rolls["B"]]
    noise = [disagreements(x, y) for x, y in combinations(arm_a, 2)]
    cross = [disagreements(x, y) for x in arm_a for y in arm_b]
    fallbacks = {arm: [sum(e.get("classification_source") == "fallback" for e in c["entries"]) for c in rolls[arm]]
                 for arm in ARMS}
    return {"noise_a": noise, "cross": cross, "stable_flips": stable_flips(arm_a, arm_b), "fallbacks": fallbacks}


def _load_rolls(uid_dir: Path) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    rolls, usage = {arm: [] for arm in ARMS}, {arm: [] for arm in ARMS}
    for arm in ARMS:
        for roll_dir in sorted((uid_dir / arm).glob("roll*")):
            rolls[arm].append(json.loads(next(roll_dir.glob("*_classified.json")).read_text()))
            usage[arm].append(json.loads((roll_dir / "usage.json").read_text()))
    return rolls, usage


def _print_compare(out: Path) -> None:
    for uid_dir in sorted(p for p in out.iterdir() if p.is_dir()):
        rolls, usage = _load_rolls(uid_dir)
        result = compare_uid(rolls)
        print(f"{uid_dir.name}: A-vs-A noise {result['noise_a']}  A-vs-B {sorted(result['cross'])}")
        print(f"  fallback entries per roll: {result['fallbacks']}")
        for (a_code, b_code), n in result["stable_flips"].most_common():
            print(f"  stable flip {a_code} -> {b_code}: {n}")
        for arm in ARMS:
            for i, u in enumerate(usage[arm], 1):
                print(f"  {arm} roll{i}: calls {u.get('calls')} cache_read {u.get('cache_read_tokens')} "
                      f"cache_write {u.get('cache_write_tokens')} cost ${u.get('cost', 0):.2f}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["estimate", "run", "compare"])
    parser.add_argument("run_dirs", nargs="*", type=Path)
    parser.add_argument("--rolls", type=int, default=6)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    if args.command == "compare":
        _print_compare(args.out)
        return 0
    if args.command == "estimate":
        total = 0.0
        for run_dir in args.run_dirs:
            e = estimate_run(run_dir)
            total += args.rolls * (e["arm_a"] + e["arm_b"])
            print(f"{e['uid']}: per roll A ${e['arm_a']:.2f}  B ${e['arm_b']:.2f}  "
                  f"({e['calls']} classification calls, per-CV prompt {e['per_cv_system_chars']} chars)")
        print(f"total for {args.rolls} rolls per arm: ${total:.2f}")
        return 0
    for run_dir in args.run_dirs:
        uid = _uid(run_dir)
        for roll in range(1, args.rolls + 1):
            # Alternate which arm goes first so neither always meets a warm cache.
            for arm in (("A", "B") if roll % 2 else ("B", "A")):
                usage = run_roll(run_dir, arm, args.out / uid / arm / f"roll{roll}")
                print(f"{uid} {arm} roll{roll}: ${usage.get('cost', 0):.2f}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
