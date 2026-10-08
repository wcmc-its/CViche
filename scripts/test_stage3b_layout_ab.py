#!/usr/bin/env python3
"""Self-tests for stage3b_layout_ab.py, on synthetic runs only. No LLM call.

    python3 scripts/test_stage3b_layout_ab.py

Pins the comparison (noise floor, cross-arm disagreement, stable flips,
fallback counts), the offline estimate's inputs, and that a `run` roll sets
and restores the layout flag and records the cache tokens it was billed.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import stage3b_layout_ab as ab  # noqa: E402

from unified_pipeline.stage3b import classify  # noqa: E402

UID = "ZZZZZZ"
MAPPINGS = [{"title": f"SECTION {n}", "taxonomy_options": [{"code": code, "confidence": 0.9}], "children": []}
            for n, code in ((1, "H"), (2, "I"))]
ENTRIES = [{"element_type": "text", "element_idx_start": n, "element_idx_end": n,
            "text": f"Synthetic entry {n}", "hierarchy": [f"SECTION {n}"]} for n in (1, 2)]


def _classified(codes, sources=None):
    sources = sources or ["llm"] * len(codes)
    return {"entries": [{"element_type": "text", "element_idx_start": i, "element_idx_end": i,
                         "taxonomy_code": c, "classification_source": s}
                        for i, (c, s) in enumerate(zip(codes, sources, strict=True))]}


def _log(log_dir: Path, name: str, log_id: str, system: str, user: str, usage: dict, cost: float):
    (log_dir / f"2026-01-01_00-00-00_{name}_{log_id}.json").write_text(json.dumps(
        {"log_id": log_id, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}))
    (log_dir / f"2026-01-01_00-00-01_{name}_{log_id}_RESPONSE.json").write_text(json.dumps(
        {"log_id": log_id, "response": {"usage": usage, "cost": cost}}))


def _run_dir(tmp: str) -> Path:
    run = Path(tmp) / UID
    (run / "outputs").mkdir(parents=True)
    (run / "outputs" / f"{UID}_entries.json").write_text(json.dumps({"entries": ENTRIES}))
    (run / "outputs" / f"{UID}_header_taxonomy.json").write_text(json.dumps({"mappings": MAPPINGS}))
    logs = run / "prompt_logs"
    logs.mkdir()
    usage = {"prompt_tokens": 1000, "completion_tokens": 10, "cache_read_tokens": 0, "cache_write_tokens": 0}
    _log(logs, "stage_3b", "aaaaaaaaaaaa", "S" * 4000, "Classify these 1 entries:", usage, 0.01)
    _log(logs, "stage_3b", "bbbbbbbbbbbb", "S" * 4000, "Classify these 1 entries:", usage, 0.02)
    _log(logs, "stage_3b_block_coherence", "cccccccccccc", "B", "B", usage, 5.0)
    return run


def test_entry_codes_and_disagreements():
    codes = ab.entry_codes({"entries": [
        {"element_type": "text", "element_idx_start": 1, "element_idx_end": 1, "taxonomy_code": "H"},
        {"element_type": "text", "element_idx_start": 1, "element_idx_end": 1, "taxonomy_code": "I"}]})
    assert codes == {("text", 1, 1, 1): "H", ("text", 1, 1, 2): "I"}, codes
    assert ab.disagreements({"x": "H", "y": "I"}, {"x": "H", "y": "S1"}) == 1
    assert ab.disagreements({"x": "H"}, {"x": "H", "z": "T"}) == 1, "an entry only one arm has counts"


def test_stable_flips_skip_entries_either_arm_is_unsure_of():
    arm_a = [{"e1": "H", "e2": "S8", "e3": "R"}, {"e1": "H", "e2": "S8", "e3": "S8"}]
    arm_b = [{"e1": "H", "e2": "R", "e3": "R"}, {"e1": "H", "e2": "R", "e3": "R"}]
    assert ab.stable_flips(arm_a, arm_b) == {("S8", "R"): 1}


def test_compare_uid_counts_noise_cross_and_fallbacks():
    rolls = {"A": [_classified(["H", "I"]), _classified(["H", "T"]), _classified(["H", "I"])],
             "B": [_classified(["H", "I"], ["llm", "fallback"]), _classified(["H", "I"])]}
    result = ab.compare_uid(rolls)
    assert sorted(result["noise_a"]) == [0, 1, 1], result["noise_a"]
    assert len(result["cross"]) == 6 and sorted(result["cross"]) == [0, 0, 0, 0, 1, 1], result["cross"]
    assert result["fallbacks"] == {"A": [0, 0, 0], "B": [1, 0]}
    assert result["stable_flips"] == {}


def test_estimate_reads_stage_3b_calls_only():
    with tempfile.TemporaryDirectory() as tmp:
        estimate = ab.estimate_run(_run_dir(tmp))
    assert estimate["uid"] == UID
    assert abs(estimate["arm_a"] - 0.03) < 1e-9, "the block-coherence log is a different purpose"
    assert estimate["calls"] == 2, "two single-entry groups"
    assert estimate["per_cv_system_chars"] > 0
    assert os.environ.get(ab.FLAG) is None, "the estimate leaves the flag as it found it"


def test_run_roll_sets_the_arm_flag_and_records_billed_cache_tokens():
    seen = []

    def fake(**kwargs):
        seen.append((os.environ.get(ab.FLAG), kwargs["messages"][1]["content"]))
        return {"content": json.dumps({"classifications": [{"index": 0, "code": "H", "confidence": 0.9}]}),
                "prompt_tokens": 100, "completion_tokens": 5, "cost": 0.5, "model": "test-model",
                "cache_read_tokens": 70, "cache_write_tokens": 0}

    real = classify.call_llm
    classify.call_llm = fake
    try:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            usage = ab.run_roll(_run_dir(tmp), "B", out)
            assert json.loads((out / "usage.json").read_text()) == usage
            assert (out / f"{UID}_classified.json").exists()
        assert classify.call_llm is fake, "run_roll puts back the call_llm it wrapped"
    finally:
        classify.call_llm = real
    assert usage["calls"] == 2 and usage["cache_read_tokens"] == 140 and usage["cost"] == 1.0, usage
    assert [flag for flag, _ in seen] == ["1", "1"]
    assert all(user.startswith("These entries come from GROUP") for _, user in seen)
    assert os.environ.get(ab.FLAG) is None


if __name__ == "__main__":
    for test in (test_entry_codes_and_disagreements, test_stable_flips_skip_entries_either_arm_is_unsure_of,
                 test_compare_uid_counts_noise_cross_and_fallbacks, test_estimate_reads_stage_3b_calls_only,
                 test_run_roll_sets_the_arm_flag_and_records_billed_cache_tokens):
        test()
        print(f"ok {test.__name__}")
