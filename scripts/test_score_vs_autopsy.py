#!/usr/bin/env python3
"""Self-tests for score_vs_autopsy.py, on synthetic scores and labels only.

    python3 scripts/test_score_vs_autopsy.py

Pins the target (severity-weighted label cost), the cap-name parse, the GREEN-
with-HIGH list, the per-cap and per-lint rows, the per-batch rows and the
batch-stratified rank r, and that every input the tool cannot score exits 2
instead of printing a number.
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


def _label(*severities, batch=None):
    label = {"uid": "X", "findings": [{"id": f"X-{i}", "severity": s} for i, s in enumerate(severities)]}
    if batch is not None:
        label["batch"] = batch
    return label


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


def _main(runs_dir, labels_dir, *extra):
    err = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
        return sva.main([str(runs_dir), str(labels_dir), *map(str, extra)]), err.getvalue()


# Two graders. Within each batch a higher score always means a lower cost (rank r -1), but
# batch HARSH grades the same kind of run far costlier, and its runs score higher.
_LENIENT = {"LN1": (70, "YELLOW", ("medium", "medium")), "LN2": (80, "YELLOW", ("medium",)),
            "LN3": (90, "GREEN (ship)", ())}
_HARSH = {"HR1": (95, "GREEN (ship)", ("high",) * 4), "HR2": (97, "GREEN (ship)", ("high",) * 3 + ("medium",) * 2),
          "HR3": (99, "GREEN (ship)", ("high",) * 3 + ("medium",))}


def _batches(**batches):
    scores, labels = {}, {}
    for batch, runs in batches.items():
        for uid, (total, band, severities) in runs.items():
            scores[uid] = _score(total, band)
            labels[uid] = _label(*severities, batch=batch)
    return scores, labels


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


def test_pooling_hides_a_within_batch_rank():
    with tempfile.TemporaryDirectory() as tmp:
        runs_dir, labels_dir = _write(tmp, *_batches(LENIENT=_LENIENT, HARSH=_HARSH))
        report = sva.build_report(sva.load(runs_dir, labels_dir))
        assert _main(runs_dir, labels_dir, "--json", Path(tmp) / "out.json")[0] == 0
        written = json.loads((Path(tmp) / "out.json").read_text())
    assert {b: row.cost_rank for b, row in report.by_batch.items()} == {"HARSH": -1.0, "LENIENT": -1.0}
    assert report.stratified == sva.Stratified(cost_rank=-1.0, batches=["HARSH", "LENIENT"], runs=6), report.stratified
    assert report.correlations["score"]["cost_rank"] == 0.54, "pooled across the two graders, the sign flips"
    assert report.by_batch["HARSH"] == sva.BatchRow(runs=3, cost_rank=-1.0, green=3, green_with_high=["HR1", "HR2", "HR3"])
    assert report.by_batch["LENIENT"] == sva.BatchRow(runs=3, cost_rank=-1.0, green=1, green_with_high=[])
    assert report.green_with_high == ["HR1", "HR2", "HR3"], "the pooled list is kept"
    assert written["stratified"] == {"cost_rank": -1.0, "batches": ["HARSH", "LENIENT"], "runs": 6}, written["stratified"]
    assert written["by_batch"]["HARSH"]["green_with_high"] == ["HR1", "HR2", "HR3"], written["by_batch"]
    assert written["correlations"]["score"]["cost_rank"] == 0.54, "the pooled numbers keep their JSON keys"
    assert [r["batch"] for r in written["runs"]] == ["HARSH"] * 3 + ["LENIENT"] * 3


def test_batch_groups():
    mixed = {"MX1": (60, "YELLOW", ("low",)), "MX2": (70, "YELLOW", ("medium",)),  # rank r +0.8
             "MX3": (80, "YELLOW", ("high",)), "MX4": (90, "GREEN (ship)", ("medium", "medium"))}
    flat = {"FL1": (88, "GREEN (ship)", ("low",)), "FL2": (88, "GREEN (ship)", ("high",)), "FL3": (88, "GREEN (ship)", ())}
    scores, labels = _batches(LENIENT=_LENIENT, MIXED=mixed, FLAT=flat, SMALL={"SM1": (60, "YELLOW", ("high",))})
    scores["NB1"], labels["NB1"] = _score(85, "GREEN (ship)"), _label("high")  # no batch key
    scores["NB2"], labels["NB2"] = _score(75, "YELLOW"), _label("low", batch="")
    with tempfile.TemporaryDirectory() as tmp:
        report = sva.build_report(sva.load(*_write(tmp, scores, labels)))
    assert len(report.runs) == 13, "every run stays in the pooled numbers"
    assert report.too_few_runs == {sva.NO_BATCH: 2, "SMALL": 1}, report.too_few_runs
    assert report.by_batch["FLAT"] == sva.BatchRow(runs=3, cost_rank=None, green=3, green_with_high=["FL2"])
    # Weighted by runs: (3 x -1.0 + 4 x 0.8) / 7 = +0.03; an unweighted mean would be -0.10.
    assert report.stratified == sva.Stratified(cost_rank=0.03, batches=["LENIENT", "MIXED"], runs=7), report.stratified
    text = sva.render(report)
    assert f"under {sva.MIN_RUNS} runs, in the pooled numbers only: (no batch) (2), SMALL (1)" in text, text
    assert "     3 /   n/a / 1 of 3   FLAT" in text, text
    assert "batch-stratified score rank r with cost: +0.03 (the rank r of 2 batches, weighted by their 7 runs)" in text
    assert "pooled GREEN runs with a verified HIGH: 2 of 6 (FL2, NB1)" in text, text


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
    with tempfile.TemporaryDirectory() as tmp:  # a batch that is not a string
        scores = {u: _score(99, "GREEN") for u in ("AAA", "BBB", "CCC")}
        labels = {u: _label("low") for u in scores}
        labels["CCC"] = _label("low", batch=7)
        rc, err = _main(*_write(tmp, scores, labels))
        assert rc == sva.EXIT_INPUT_ERROR and "is not a string" in err, (rc, err)


if __name__ == "__main__":
    for test in (test_report, test_pooling_hides_a_within_batch_rank, test_batch_groups, test_fails_closed):
        test()
        print(f"ok {test.__name__}")
