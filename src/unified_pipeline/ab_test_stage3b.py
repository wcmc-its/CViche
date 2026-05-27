#!/usr/bin/env python3
"""A/B test stage 3b entry classification across models (cost + agreement).

Runs the SAME stage-2 entries through stage 3b once per model and compares the
resulting taxonomy codes, T-rate, tokens, and cost. There are no ground-truth
labels, so the *reference* model (default: Sonnet 4.6, the current production
model) is the baseline and each *candidate* is scored by how often it agrees
with the reference, with every disagreement listed for human review.

Supports any number of candidates (Bedrock Claude or OpenAI gpt-* — the
provider is inferred from the model id) for N-way comparison.

The stage-3b code does not thread its `model` argument through to call_llm
(the model is resolved from llm_config.yaml via get_stage_config). This harness
forces the model+provider per run by wrapping get_stage_config as llm_client
resolves it, so no config files are edited.

Requires live access (AWS Bedrock creds and/or OPENAI_API_KEY) and model access
for every model compared.

Usage:
    PYTHONPATH=src python -m unified_pipeline.ab_test_stage3b UID [UID ...]
        [--reference us.anthropic.claude-sonnet-4-6]
        [--candidates "us.anthropic.claude-haiku-4-5-20251001-v1:0,gpt-5.1"]
        [--out DIR]

UIDs must have committed inputs under
src/unified_pipeline/outputs/stage_2_entry_extraction/{UID}_entries.json and
.../stage_3a_header_mappings/{UID}_header_taxonomy.json.
"""
import argparse
import contextlib
import json
from datetime import datetime
from pathlib import Path

import unified_pipeline.llm_client as llm_client
from unified_pipeline.config import get_stage_config as _real_get_stage_config
from unified_pipeline.stage_3b_entry_classifier import run_stage_3b

REFERENCE_DEFAULT = "us.anthropic.claude-sonnet-4-6"
# Haiku 4.5 requires the FULL versioned inference-profile id on Bedrock; the
# short alias "us.anthropic.claude-haiku-4-5" is rejected with ValidationException
# and silently degrades to fallback classifications (see _assert_real_run).
CANDIDATE_DEFAULT = "us.anthropic.claude-haiku-4-5-20251001-v1:0"

# (input, output) USD per 1M tokens. Cost is computed here from token counts
# rather than trusting stats["cost"], because config.py PRICING is keyed by the
# bare model id and may mis-price dated Bedrock ids. Keys are matched as
# substrings of the model id, MOST-SPECIFIC FIRST (gpt-4o-mini before gpt-4o).
_RATES = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-5.1": (2.50, 10.00),
    "gpt-4o": (2.50, 10.00),
    "sonnet-4-6": (3.0, 15.0),
    "haiku-4-5": (1.0, 5.0),
    "opus-4-7": (15.0, 75.0),
}


def _model_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    rate = next((r for key, r in _RATES.items() if key in model), None)
    if rate is None:
        return 0.0
    return (input_tokens * rate[0] + output_tokens * rate[1]) / 1_000_000


def _provider_for(model: str) -> str:
    """Infer the provider from the model id (OpenAI vs Bedrock)."""
    return "openai" if model.lower().startswith(("gpt-", "o1", "o3", "o4", "chatgpt")) else "bedrock"


@contextlib.contextmanager
def force_model(model: str):
    """Force every stage onto `model` (and its inferred provider) by wrapping
    get_stage_config as llm_client resolves it. Restores the original on exit."""
    original = llm_client.get_stage_config
    provider = _provider_for(model)

    def patched(stage):
        cfg = dict(_real_get_stage_config(stage))
        cfg["model"] = model
        cfg["provider"] = provider
        return cfg

    llm_client.get_stage_config = patched
    try:
        yield
    finally:
        llm_client.get_stage_config = original


def _entry_key(e: dict):
    """Stable key to align the same entry across runs."""
    idx = e.get("element_idx_start")
    if idx is not None:
        return (idx, (e.get("text") or "")[:80])
    return (None, (e.get("text") or "")[:160])


def _t_rate(entries: list) -> float:
    codes = [e.get("taxonomy_code") for e in entries]
    return (sum(1 for c in codes if c == "T") / len(codes)) if codes else 0.0


def _assert_real_run(uid: str, model: str, entries: list) -> None:
    """Fail loudly if no entry was actually classified by the LLM. A bad model
    id raises per-batch errors that stage 3b swallows and fills with fallback
    codes (confidence 0.5) -- plausible-looking but meaningless. Hard error."""
    content = [e for e in entries if (e.get("text") or "").strip()
               and e.get("element_type") != "header"]
    if content and not any(e.get("classification_source") == "llm" for e in content):
        raise RuntimeError(
            f"{model} produced only fallback classifications on {uid} (no LLM "
            f"output). The model likely failed every batch -- check the run log "
            f"for 'Batch classification error' (e.g. an invalid model id)."
        )


def run_one(uid: str, model: str, out_dir: Path) -> dict:
    """Run stage 3b under `model`. run_stage_3b returns stats + the output path
    (entries are written there, not returned), so load them back."""
    model_dir = out_dir / model.replace("/", "_").replace(":", "_")
    model_dir.mkdir(parents=True, exist_ok=True)
    with force_model(model):
        res = run_stage_3b(uid, output_dir=str(model_dir))
    entries = json.loads(Path(res["output_path"]).read_text()).get("entries", [])
    _assert_real_run(uid, model, entries)
    stats = res["stats"]
    cost = _model_cost(model, stats.get("input_tokens", 0), stats.get("output_tokens", 0))
    return {"stats": stats, "entries": entries, "cost": cost, "output_path": res["output_path"]}


def compare(uid: str, candidate: str, ref_res: dict, cand_res: dict) -> dict:
    ref = {_entry_key(e): e for e in ref_res["entries"]}
    cand = {_entry_key(e): e for e in cand_res["entries"]}
    aligned = [k for k in ref if k in cand]

    agree = 0
    diffs = []
    for k in aligned:
        rc, cc = ref[k].get("taxonomy_code"), cand[k].get("taxonomy_code")
        if rc == cc:
            agree += 1
        else:
            diffs.append({
                "text": (ref[k].get("text") or "")[:140],
                "hierarchy": ref[k].get("hierarchy", []),
                "reference_code": rc, "candidate_code": cc,
                "reference_conf": ref[k].get("taxonomy_confidence"),
                "candidate_conf": cand[k].get("taxonomy_confidence"),
            })

    n = len(aligned)
    return {
        "uid": uid, "candidate": candidate, "aligned_entries": n,
        "agreement": agree, "agreement_pct": round(100 * agree / n, 1) if n else 0.0,
        "reference_t_rate_pct": round(100 * _t_rate(ref_res["entries"]), 1),
        "candidate_t_rate_pct": round(100 * _t_rate(cand_res["entries"]), 1),
        "reference_cost": round(ref_res["cost"], 4),
        "candidate_cost": round(cand_res["cost"], 4),
        "diffs": diffs,
    }


def render_markdown(reference: str, candidates: list, results: list) -> str:
    uids = sorted({r["uid"] for r in results})
    lines = [
        "# Stage 3b model A/B — agreement & cost",
        "",
        f"- **Reference (baseline):** `{reference}`",
        f"- **CVs:** {len(uids)} ({', '.join(uids)})",
        "",
        "No ground-truth labels exist, so agreement is measured against the "
        "reference. Disagreements are not necessarily candidate errors — review "
        "the diffs to judge which code is correct.",
        "",
        "## Summary (per candidate, across all CVs)",
        "",
        "| Candidate | overall agree % | candidate $ | reference $ | cost ratio |",
        "|---|--:|--:|--:|--:|",
    ]
    ref_cost_total = sum(r["reference_cost"] for r in results) / max(len(candidates), 1)
    for cand in candidates:
        rs = [r for r in results if r["candidate"] == cand]
        al = sum(r["aligned_entries"] for r in rs)
        ag = sum(r["agreement"] for r in rs)
        cc = sum(r["candidate_cost"] for r in rs)
        rc = sum(r["reference_cost"] for r in rs)
        agree_pct = round(100 * ag / al, 1) if al else 0.0
        ratio = f"{rc / cc:.2f}x" if cc else "n/a"
        lines.append(f"| `{cand}` | {agree_pct} | {cc:.4f} | {rc:.4f} | {ratio} |")

    lines += ["", "## Per-CV", "",
              "| CV | candidate | aligned | agree % | ref T% | cand T% | ref $ | cand $ |",
              "|----|----|--:|--:|--:|--:|--:|--:|"]
    for r in sorted(results, key=lambda x: (x["uid"], x["candidate"])):
        lines.append(
            f"| {r['uid']} | `{r['candidate']}` | {r['aligned_entries']} | "
            f"{r['agreement_pct']} | {r['reference_t_rate_pct']} | "
            f"{r['candidate_t_rate_pct']} | {r['reference_cost']:.4f} | {r['candidate_cost']:.4f} |"
        )

    lines += ["", "## Disagreements (reference → candidate)", ""]
    any_diff = False
    for r in sorted(results, key=lambda x: (x["candidate"], x["uid"])):
        if not r["diffs"]:
            continue
        any_diff = True
        lines.append(f"### `{r['candidate']}` — {r['uid']} ({len(r['diffs'])} of {r['aligned_entries']})")
        lines.append("")
        lines.append("| code (ref→cand) | conf (ref→cand) | hierarchy | entry |")
        lines.append("|---|---|---|---|")
        for d in r["diffs"]:
            hier = " > ".join(d["hierarchy"]) if d["hierarchy"] else "—"
            text = d["text"].replace("|", "\\|")
            lines.append(
                f"| {d['reference_code']} → {d['candidate_code']} | "
                f"{d['reference_conf']} → {d['candidate_conf']} | {hier} | {text} |"
            )
        lines.append("")
    if not any_diff:
        lines.append("_None — every candidate matched the reference on every aligned entry._")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="A/B test stage 3b across models.")
    ap.add_argument("uids", nargs="+", help="document UIDs with stage_2 + stage_3a outputs")
    ap.add_argument("--reference", default=REFERENCE_DEFAULT)
    ap.add_argument("--candidates", default=CANDIDATE_DEFAULT,
                    help="comma-separated candidate model ids compared vs the reference")
    ap.add_argument("--out", default=None, help="results dir (default: outputs/ab_stage3b/<timestamp>)")
    args = ap.parse_args()

    candidates = [c.strip() for c in args.candidates.split(",") if c.strip()]
    base = Path(__file__).parent / "outputs"
    out_dir = Path(args.out) if args.out else base / "ab_stage3b" / datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for uid in args.uids:
        print(f"\n{'#' * 70}\n# A/B {uid}\n{'#' * 70}")
        print(f"\n--- reference: {args.reference} ---")
        ref_res = run_one(uid, args.reference, out_dir)
        for cand in candidates:
            print(f"\n--- candidate: {cand} ---")
            cand_res = run_one(uid, cand, out_dir)
            results.append(compare(uid, cand, ref_res, cand_res))

    report_md = render_markdown(args.reference, candidates, results)
    (out_dir / "REPORT.md").write_text(report_md)
    (out_dir / "results.json").write_text(json.dumps(
        {"reference": args.reference, "candidates": candidates, "results": results}, indent=2,
    ))
    print("\n" + report_md)
    print(f"\nWrote {out_dir / 'REPORT.md'} and results.json")


if __name__ == "__main__":
    main()
