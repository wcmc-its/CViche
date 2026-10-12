#!/usr/bin/env python3
"""Self-tests for score_vs_autopsy.py, on synthetic scores and labels only.

    python3 scripts/test_score_vs_autopsy.py

Pins the target (severity-weighted label cost), the cap-name parse, the GREEN-
with-HIGH list, the per-cap and per-lint rows, and that every input the tool
cannot score exits 2 instead of printing a number.
"""
import contextlib
import io
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import score_vs_autopsy as sva  # noqa: E402

CAP_FLAG = "HARD-FAIL cap=84: Grant details shifted between grants (CAP-ONLY gate) (grant_boundary_findings=3; cap=84)"


def _score(total, band, flags=(), penalty=0.0):
    return {"totalScore": total, "raw_score_before_caps": max(total, 90), "band": band, "flags": list(flags),
            "dimensionScores": [{"name": "Sparse tables", "max": 12, "penalty": penalty},
                                {"name": "No output (gate)", "max": 0, "penalty": 0.0}]}


def _label(*severities):
    return {"uid": "X", "findings": [{"id": f"X-{i}", "severity": s} for i, s in enumerate(severities)]}


def _write(tmp, runs, labels, doctors=None):
    runs_dir, labels_dir = Path(tmp) / "runs", Path(tmp) / "labels"
    for uid, score in runs.items():
        (runs_dir / uid / "outputs").mkdir(parents=True)
        (runs_dir / uid / "quality_score.json").write_text(json.dumps(score))
    for uid, findings in (doctors or {}).items():
        (runs_dir / uid / "outputs" / f"{uid}_doctor.json").write_text(json.dumps({"findings": findings}))
    labels_dir.mkdir()
    for uid, label in labels.items():
        (labels_dir / f"{uid}.json").write_text(label if isinstance(label, str) else json.dumps(label))
    return runs_dir, labels_dir


def _main(runs_dir, labels_dir):
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        return sva.main([str(runs_dir), str(labels_dir)]), err.getvalue()


def test_report():
    with tempfile.TemporaryDirectory() as tmp:
        runs_dir, labels_dir = _write(tmp,
            {"AAA": _score(100, "GREEN (ship)", penalty=0.0),
             "BBB": _score(84, "YELLOW", [CAP_FLAG], penalty=0.5),
             "CCC": _score(99, "GREEN (ship)", penalty=0.2),
             "DDD": _score(95, "GREEN (ship)")},
            {"AAA": _label("high", "low"), "BBB": _label("medium"), "CCC": _label("low", "low"),
             "DDD": _label("high", "high", "medium")},
            {"DDD": [{"lint": "dedup_drops", "severity": "WARN"}, {"lint": "dedup_drops", "severity": "INFO"}],
             "AAA": [{"lint": "dedup_drops", "severity": "WARN"}]})
        runs = sva.load(runs_dir, labels_dir)
        by = {r.uid: r for r in runs}
        assert by["AAA"].cost == 3.25 and by["DDD"].cost == 7.0, (by["AAA"].cost, by["DDD"].cost)
        assert by["BBB"].caps == ["Grant details shifted between grants"], by["BBB"].caps
        assert list(by["BBB"].penalties) == ["Sparse tables"], "a weight-0 gate is not a dimension"
        assert by["DDD"].lint_counts["dedup_drops"] == 1, "INFO findings are not counted"
        report = sva.build_report(runs)
        assert report.green_with_high == ["AAA", "DDD"], report.green_with_high
        cap = report.caps["Grant details shifted between grants"]
        assert cap["runs"] == ["BBB"] and cap["zero_high"] == ["BBB"] and cap["mean_high"] == 0.0, cap
        assert report.correlations["score"]["cost_pearson"] is not None
        assert set(report.lints) == {sva.ALL_LINTS, "dedup_drops"}, report.lints


def test_fails_closed():
    with tempfile.TemporaryDirectory() as tmp:  # too few runs
        runs_dir, labels_dir = _write(tmp, {"AAA": _score(99, "GREEN")}, {"AAA": _label("high")})
        assert _main(runs_dir, labels_dir)[0] == sva.EXIT_INPUT_ERROR
    with tempfile.TemporaryDirectory() as tmp:  # unknown severity
        scores = {u: _score(99, "GREEN") for u in ("AAA", "BBB", "CCC")}
        labels = {u: _label("low") for u in scores}
        labels["CCC"] = _label("critical")
        assert _main(*_write(tmp, scores, labels))[0] == sva.EXIT_INPUT_ERROR
    with tempfile.TemporaryDirectory() as tmp:  # unreadable label
        scores = {u: _score(99, "GREEN") for u in ("AAA", "BBB", "CCC")}
        labels = {u: _label("low") for u in scores}
        labels["CCC"] = "{not json"
        assert _main(*_write(tmp, scores, labels))[0] == sva.EXIT_INPUT_ERROR


if __name__ == "__main__":
    for test in (test_report, test_fails_closed):
        test()
        print(f"ok {test.__name__}")
