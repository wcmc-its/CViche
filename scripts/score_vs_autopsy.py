#!/usr/bin/env python3
"""Score the quality score against verified autopsy labels (#822).

    python3 scripts/score_vs_autopsy.py <runs_dir> <labels_dir> [--json OUT]

`runs_dir` holds one directory per run, as `aws s3 sync` of
`s3://.../cviche/runs/<uid>/` lays it out: `<uid>/quality_score.json` and,
optionally, `<uid>/outputs/<uid>_doctor.json`. `labels_dir` holds one
`<uid>.json` per run in the schema scripts/doctor_vs_autopsy.py reads; only each
finding's `severity` is used here.

The score is meant to predict how much cleanup a run needs. This measures how
well it does, against the one target we have: the run's verified defects,
weighted by severity (`SEVERITY_COST`). It reports:

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

Fails closed (CODING_STANDARDS 5.5): an unreadable input, a malformed label,
or fewer than MIN_RUNS runs that are both scored and labelled exits 2.
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
MIN_RUNS = 3
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


@dataclass
class Report:
    runs: list[dict] = field(default_factory=list)
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


def apply_label(run: Run, label: object, path: Path) -> None:
    if not isinstance(label, dict) or not isinstance(label.get("findings"), list):
        raise InputError(f"{path}: no findings list")
    severities = Counter(f.get("severity") for f in label["findings"])
    unknown = set(severities) - set(SEVERITY_COST)
    if unknown:
        raise InputError(f"{path}: unknown severity {sorted(map(str, unknown))}")
    run.high, run.medium, run.low = severities["high"], severities["medium"], severities["low"]
    run.cost = sum(SEVERITY_COST[s] * n for s, n in severities.items())


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


def _corr(xs: list[float], ys: list[float]) -> dict[str, float | None]:
    if len(set(xs)) < 2 or len(set(ys)) < 2:
        return {"pearson": None, "rank": None}  # a constant has no correlation
    return {"pearson": round(statistics.correlation(xs, ys), 2),
            "rank": round(statistics.correlation(xs, ys, method="ranked"), 2)}


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


def build_report(runs: list[Run]) -> Report:
    report = Report(runs=[{k: v for k, v in asdict(r).items() if k not in ("penalties", "lint_counts")}
                          for r in runs])
    report.correlations["score"] = _vs_targets([r.score for r in runs], runs)
    report.correlations["raw score (before caps)"] = _vs_targets([r.raw for r in runs], runs)
    for name in sorted({n for r in runs for n in r.penalties}):
        report.correlations[f"penalty: {name}"] = _vs_targets([r.penalties.get(name, 0.0) for r in runs], runs)
    report.green_with_high = [r.uid for r in runs if r.band.startswith(GREEN_PREFIX) and r.high]
    report.caps = _cap_rows(runs)
    if any(r.lint_counts for r in runs):
        report.lints[ALL_LINTS] = _vs_targets([sum(r.lint_counts.values()) for r in runs], runs)
    for lint in sorted({l for r in runs for l in r.lint_counts}):
        report.lints[lint] = _vs_targets([r.lint_counts[lint] for r in runs], runs)
    return report


def _fmt(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:+.2f}"


def render(report: Report) -> str:
    runs = report.runs
    greens = [r for r in runs if r["band"].startswith(GREEN_PREFIX)]
    out = [f"{len(runs)} runs; target cost = " + " + ".join(f"{w:g}x{s}" for s, w in SEVERITY_COST.items()),
           "", "correlation with label cost / HIGH count (Pearson, rank):"]
    for name, c in report.correlations.items():
        out.append(f"  {_fmt(c['cost_pearson'])} {_fmt(c['cost_rank'])}   "
                   f"{_fmt(c['high_pearson'])} {_fmt(c['high_rank'])}   {name}")
    out += ["", f"GREEN runs with a verified HIGH: {len(report.green_with_high)} of {len(greens)}"
            + (f" ({', '.join(report.green_with_high)})" if report.green_with_high else "")]
    out += ["", "caps (fired on / with 0 HIGH / mean HIGH capped vs not / mean cost capped vs not):"]
    for cap, row in report.caps.items():
        out.append(f"  {len(row['runs'])} / {len(row['zero_high'])} / {row['mean_high']} vs {row['mean_high_uncapped']}"
                   f" / {row['mean_cost']} vs {row['mean_cost_uncapped']}   {cap}")
    if report.lints:
        out += ["", "doctor lint WARN+ count vs label cost (Pearson, rank), strongest first:"]
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
