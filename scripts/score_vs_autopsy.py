#!/usr/bin/env python3
"""Score the quality score against verified autopsy labels (#822).

    python3 scripts/score_vs_autopsy.py <runs_dir> <labels_dir> [--json OUT]

`runs_dir` holds one directory per run, as `aws s3 sync` of
`s3://.../cviche/runs/<uid>/` lays it out: `<uid>/quality_score.json` and,
optionally, `<uid>/outputs/<uid>_doctor.json`. `labels_dir` holds one
`<uid>.json` per run in the schema scripts/doctor_vs_autopsy.py reads; only the
label's `batch` and each finding's `severity` are used here.

The score is meant to predict how much cleanup a run needs. This measures how
well it does, against the one target we have: the run's verified defects,
weighted by severity (`SEVERITY_COST`).

Each batch was graded by its own autopsy, and the graders differ, so a number
pooled across batches partly measures the graders (docs/RUN_DOCTOR_SCORING.md,
"Ground truth"). It reports first, per batch (the label's `batch`; a label with
none is grouped as `NO_BATCH`):

- for each batch of MIN_RUNS or more runs, the rank r of the score with that
  cost, and the GREEN runs that carry a verified HIGH. A smaller batch is
  listed by name and run count, and counts only in the pooled numbers;
- the batch-stratified rank r: each batch's rank r, weighted by its run count,
  over the batches that have one (a batch whose score or cost is constant has
  none).

Then, pooled across all batches:

- the correlation (Pearson and rank) of the final score, the raw score before
  caps, and each dimension's penalty, with that cost and with the HIGH count;
- the GREEN runs that carry a verified HIGH;
- per cap (from the score's `flags`): the runs it fired on, and their mean HIGH
  and cost against the runs it did not fire on;
- per doctor lint, when doctor reports are present: the correlation of its
  WARN/ERROR count with the cost, and of all lints' count together --
  candidate inputs for a fitted score. A lint that fired on one run only
  correlates on that run alone; read its row with its run count in mind.

A negative correlation is the right sign for the score (more defects, lower
score); a positive one is right for a penalty or a lint count. With a few dozen
runs these are descriptive, not a fit: refit on each new labelled batch.

The JSON keeps the pooled numbers under their original keys (`correlations`,
`green_with_high`, `caps`, `lints`); the per-batch ones are `by_batch`,
`too_few_runs` and `stratified`.

Fails closed (CODING_STANDARDS 5.5): an unreadable input, a malformed label
(including a `batch` that is not a string), or fewer than MIN_RUNS runs that
are both scored and labelled exits 2.
"""
import argparse
import json
import re
import statistics
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

SEVERITY_COST = {"high": 3.0, "medium": 1.0, "low": 0.25}  # rough relative cleanup effort
MIN_RUNS = 3  # fewest runs a correlation is reported on, pooled or per batch
NO_BATCH = "(no batch)"  # the group of labels that name no batch
GREEN_PREFIX = "GREEN"
LOUD_SEVERITIES = frozenset({"WARN", "ERROR"})
EXIT_INPUT_ERROR = 2
ALL_LINTS = "(all lints)"
_CAP_FLAG_RE = re.compile(r"cap=(\d+):\s*(.+?)\s*\(")


class InputError(Exception):
    """An input this tool cannot score; main() exits EXIT_INPUT_ERROR."""


@dataclass
class Run:
    uid: str
    score: float
    raw: float
    band: str
    caps: list[str]
    penalties: dict[str, float]
    lint_counts: Counter
    high: int = 0
    medium: int = 0
    low: int = 0
    cost: float = 0.0
    batch: str = NO_BATCH


@dataclass
class BatchRow:
    """One batch of MIN_RUNS or more runs, graded by one autopsy."""
    runs: int
    cost_rank: float | None  # rank r of the score with label cost; None when either is constant
    green: int
    green_with_high: list[str]


@dataclass
class Stratified:
    """The per-batch rank r, weighted by each batch's run count."""
    cost_rank: float | None = None
    batches: list[str] = field(default_factory=list)  # the batches with a rank r
    runs: int = 0  # their runs


@dataclass
class Report:
    runs: list[dict] = field(default_factory=list)
    by_batch: dict[str, BatchRow] = field(default_factory=dict)
    too_few_runs: dict[str, int] = field(default_factory=dict)  # batch -> runs, under MIN_RUNS
    stratified: Stratified = field(default_factory=Stratified)
    # Pooled across all batches, so across graders:
    correlations: dict[str, dict[str, float | None]] = field(default_factory=dict)
    green_with_high: list[str] = field(default_factory=list)
    caps: dict[str, dict] = field(default_factory=dict)
    lints: dict[str, dict[str, float | None]] = field(default_factory=dict)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as e:
        raise InputError(f"{path}: {e}") from e


def _cap_names(flags: list[str]) -> list[str]:
    return [m.group(2) for f in flags if (m := _CAP_FLAG_RE.search(f))]


def load_run(run_dir: Path) -> Run | None:
    """One run's score (and doctor lint counts); None when it has no score."""
    score_path = run_dir / "quality_score.json"
    if not score_path.exists():
        return None
    q = _read_json(score_path)
    if not isinstance(q, dict) or "totalScore" not in q:
        raise InputError(f"{score_path}: no totalScore")
    penalties = {d["name"]: float(d.get("penalty") or 0.0)
                 for d in q.get("dimensionScores", []) if d.get("max")}
    lints: Counter = Counter()
    doctor_path = run_dir / "outputs" / f"{run_dir.name}_doctor.json"
    if doctor_path.exists():
        doctor = _read_json(doctor_path)
        findings = doctor.get("findings", []) if isinstance(doctor, dict) else doctor
        lints.update(f.get("lint") for f in findings if f.get("severity") in LOUD_SEVERITIES)
    return Run(uid=run_dir.name, score=float(q["totalScore"]),
               raw=float(q.get("raw_score_before_caps", q["totalScore"])),
               band=str(q.get("band", "")), caps=_cap_names(q.get("flags", [])),
               penalties=penalties, lint_counts=lints)


def _label_batch(label: dict, path: Path) -> str:
    batch = label.get("batch")
    if batch is None or batch == "":
        return NO_BATCH
    if not isinstance(batch, str):
        raise InputError(f"{path}: batch {batch!r} is not a string")
    return batch


def apply_label(run: Run, label: object, path: Path) -> None:
    if not isinstance(label, dict) or not isinstance(label.get("findings"), list):
        raise InputError(f"{path}: no findings list")
    severities = Counter(f.get("severity") for f in label["findings"])
    unknown = set(severities) - set(SEVERITY_COST)
    if unknown:
        raise InputError(f"{path}: unknown severity {sorted(map(str, unknown))}")
    run.high, run.medium, run.low = severities["high"], severities["medium"], severities["low"]
    run.cost = sum(SEVERITY_COST[s] * n for s, n in severities.items())
    run.batch = _label_batch(label, path)


def load(runs_dir: Path, labels_dir: Path) -> list[Run]:
    runs = []
    for label_path in sorted(labels_dir.glob("*.json")):
        run = load_run(runs_dir / label_path.stem)
        if run is not None:
            apply_label(run, _read_json(label_path), label_path)
            runs.append(run)
    if len(runs) < MIN_RUNS:
        raise InputError(f"{len(runs)} runs both scored and labelled; need {MIN_RUNS}")
    return runs


def _rank_r(xs: list[float], ys: list[float]) -> float | None:
    """Spearman's rank r, unrounded; None when either side is constant (it has no correlation)."""
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return None
    return statistics.correlation(xs, ys, method="ranked")


def _round(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _corr(xs: list[float], ys: list[float]) -> dict[str, float | None]:
    rank = _rank_r(xs, ys)
    if rank is None:
        return {"pearson": None, "rank": None}
    return {"pearson": round(statistics.correlation(xs, ys), 2), "rank": round(rank, 2)}


def _vs_targets(xs: list[float], runs: list[Run]) -> dict[str, float | None]:
    by_cost, by_high = _corr(xs, [r.cost for r in runs]), _corr(xs, [r.high for r in runs])
    return {"cost_pearson": by_cost["pearson"], "cost_rank": by_cost["rank"],
            "high_pearson": by_high["pearson"], "high_rank": by_high["rank"]}


def _mean(values: list[float]) -> float | None:
    return round(statistics.fmean(values), 2) if values else None


def _cap_rows(runs: list[Run]) -> dict[str, dict]:
    rows = {}
    for cap in sorted({c for r in runs for c in r.caps}):
        hit = [r for r in runs if cap in r.caps]
        miss = [r for r in runs if cap not in r.caps]
        rows[cap] = {"runs": [r.uid for r in hit],
                     "zero_high": [r.uid for r in hit if r.high == 0],
                     "mean_high": _mean([r.high for r in hit]), "mean_high_uncapped": _mean([r.high for r in miss]),
                     "mean_cost": _mean([r.cost for r in hit]), "mean_cost_uncapped": _mean([r.cost for r in miss])}
    return rows


def _is_green(run: Run) -> bool:
    return run.band.startswith(GREEN_PREFIX)


def _green_with_high(runs: list[Run]) -> list[str]:
    return [r.uid for r in runs if _is_green(r) and r.high]


def _group_by_batch(runs: list[Run]) -> dict[str, list[Run]]:
    groups: dict[str, list[Run]] = {}
    for run in runs:
        groups.setdefault(run.batch, []).append(run)
    return dict(sorted(groups.items()))


def _batch_row(runs: list[Run]) -> BatchRow:
    return BatchRow(runs=len(runs), cost_rank=_round(_rank_r([r.score for r in runs], [r.cost for r in runs])),
                    green=sum(1 for r in runs if _is_green(r)), green_with_high=_green_with_high(runs))


def _stratified(groups: dict[str, list[Run]]) -> Stratified:
    """Weights each batch's unrounded rank r by its run count; a batch with no rank r is left out."""
    ranks = {batch: _rank_r([r.score for r in runs], [r.cost for r in runs]) for batch, runs in groups.items()}
    ranked = {batch: rank for batch, rank in ranks.items() if rank is not None}
    n = sum(len(groups[batch]) for batch in ranked)
    if not n:
        return Stratified()
    mean = sum(rank * len(groups[batch]) for batch, rank in ranked.items()) / n
    return Stratified(cost_rank=round(mean, 2), batches=list(ranked), runs=n)


def build_report(runs: list[Run]) -> Report:
    report = Report(runs=[{k: v for k, v in asdict(r).items() if k not in ("penalties", "lint_counts")}
                          for r in runs])
    groups = _group_by_batch(runs)
    graded = {batch: members for batch, members in groups.items() if len(members) >= MIN_RUNS}
    report.by_batch = {batch: _batch_row(members) for batch, members in graded.items()}
    report.too_few_runs = {batch: len(members) for batch, members in groups.items() if batch not in graded}
    report.stratified = _stratified(graded)
    report.correlations["score"] = _vs_targets([r.score for r in runs], runs)
    report.correlations["raw score (before caps)"] = _vs_targets([r.raw for r in runs], runs)
    for name in sorted({n for r in runs for n in r.penalties}):
        report.correlations[f"penalty: {name}"] = _vs_targets([r.penalties.get(name, 0.0) for r in runs], runs)
    report.green_with_high = _green_with_high(runs)
    report.caps = _cap_rows(runs)
    if any(r.lint_counts for r in runs):
        report.lints[ALL_LINTS] = _vs_targets([sum(r.lint_counts.values()) for r in runs], runs)
    for lint in sorted({l for r in runs for l in r.lint_counts}):
        report.lints[lint] = _vs_targets([r.lint_counts[lint] for r in runs], runs)
    return report


def _fmt(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:+.2f}"


def _render_batches(report: Report) -> list[str]:
    out = [f"per batch, {MIN_RUNS}+ runs (runs / score rank r with cost / GREEN runs with a verified HIGH):"]
    out += [f"  {row.runs:4d} / {_fmt(row.cost_rank)} / {len(row.green_with_high)} of {row.green}   {batch}"
            for batch, row in report.by_batch.items()]
    s = report.stratified
    out.append(f"batch-stratified score rank r with cost: {_fmt(s.cost_rank).strip()}"
               f" (the rank r of {len(s.batches)} batches, weighted by their {s.runs} runs)")
    if report.too_few_runs:
        out.append(f"under {MIN_RUNS} runs, in the pooled numbers only: "
                   + ", ".join(f"{batch} ({n})" for batch, n in report.too_few_runs.items()))
    return out


def render(report: Report) -> str:
    runs = report.runs
    greens = [r for r in runs if r["band"].startswith(GREEN_PREFIX)]
    n_batches = len(report.by_batch) + len(report.too_few_runs)
    out = [f"{len(runs)} runs in {n_batches} batches; target cost = "
           + " + ".join(f"{w:g}x{s}" for s, w in SEVERITY_COST.items()),
           "", *_render_batches(report),
           "", "pooled across batches, so across graders; compare within a batch first.",
           "pooled correlation with label cost / HIGH count (Pearson, rank):"]
    for name, c in report.correlations.items():
        out.append(f"  {_fmt(c['cost_pearson'])} {_fmt(c['cost_rank'])}   "
                   f"{_fmt(c['high_pearson'])} {_fmt(c['high_rank'])}   {name}")
    out += ["", f"pooled GREEN runs with a verified HIGH: {len(report.green_with_high)} of {len(greens)}"
            + (f" ({', '.join(report.green_with_high)})" if report.green_with_high else "")]
    out += ["", "pooled caps (fired on / with 0 HIGH / mean HIGH capped vs not / mean cost capped vs not):"]
    for cap, row in report.caps.items():
        out.append(f"  {len(row['runs'])} / {len(row['zero_high'])} / {row['mean_high']} vs {row['mean_high_uncapped']}"
                   f" / {row['mean_cost']} vs {row['mean_cost_uncapped']}   {cap}")
    if report.lints:
        out += ["", "pooled doctor lint WARN+ count vs label cost (Pearson, rank), strongest first:"]
        ranked = sorted(report.lints.items(), key=lambda kv: -abs(kv[1]["cost_pearson"] or 0))
        out += [f"  {_fmt(c['cost_pearson'])} {_fmt(c['cost_rank'])}   {lint}" for lint, c in ranked]
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("runs_dir", type=Path)
    ap.add_argument("labels_dir", type=Path)
    ap.add_argument("--json", type=Path, help="also write the report as JSON")
    args = ap.parse_args(argv)
    try:
        report = build_report(load(args.runs_dir, args.labels_dir))
    except InputError as e:
        print(f"score_vs_autopsy: {e}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    print(render(report))
    if args.json:
        args.json.write_text(json.dumps(asdict(report), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
