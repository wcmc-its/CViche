"""Fallback-surfacing guards for Stage 3b entry classification (#61).

Stage 3b used to swallow per-batch LLM errors with a one-line print and
silently substitute default codes (``classification_source="fallback"``),
so an invalid model id / IAM gap / outage produced a "successful" run whose
every classification was fake. These tests pin the new behavior: batch
failures are logged as errors with batch context, per-run LLM/fallback
counts are tracked in stats and written to the stage output JSON, partial
batch failures stay non-fatal, and a nonempty run with ZERO successful LLM
classifications raises instead of completing.

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_stage_3b_fallback_surfacing.py -p no:cacheprovider

Self-contained: ``call_llm`` stubbed at the module attribute, no Bedrock/OpenAI.
"""

import json
import logging
import sys
from pathlib import Path

import pytest

# Ensure the repo's ``src`` directory is importable regardless of cwd/rootdir.
_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.stage_3b_entry_classifier as stage_3b  # noqa: E402
# call_llm is stubbed at the module that actually calls it: the #522 split
# moved every classification call site into stage3b/classify.py, so patching
# the facade's copy would no longer intercept anything (#496).
import unified_pipeline.stage3b.classify as stage3b_classify  # noqa: E402
from unified_pipeline.stage_3b_entry_classifier import (  # noqa: E402
    TaxonomyContext,
    classify_entries_batch,
    load_taxonomy,
)

TAXONOMY = load_taxonomy()


def _entries(texts, hierarchy=("HONORS AND AWARDS",)):
    return [{"text": t, "hierarchy": list(hierarchy)} for t in texts]


def _context(codes=("H",)):
    return TaxonomyContext(meta_section={
        "title": "HONORS AND AWARDS",
        "taxonomy_options": [{"code": c, "confidence": 0.9} for c in codes],
    })


def _ok_response(indices, code="H", model="test-model"):
    return {
        "content": json.dumps({"classifications": [
            {"index": i, "code": code, "confidence": 0.9} for i in indices
        ]}),
        "prompt_tokens": 100,
        "completion_tokens": 20,
        "cost": 0.001,
        "model": model,
    }


def _boom(**kwargs):
    raise RuntimeError("ValidationException: invalid model identifier")


# --- classify_entries_batch: per-batch failure accounting ---------------------

def test_failed_batch_falls_back_and_is_counted(monkeypatch):
    """An LLM error still falls back per entry, but the stats say so."""
    monkeypatch.setattr(stage3b_classify, "call_llm", _boom)

    results, stats = classify_entries_batch(
        _entries(["Dean's Award for Excellence, 2015", "Teaching Prize, 2018"]),
        _context(), TAXONOMY)

    assert [r["classification_source"] for r in results] == ["fallback", "fallback"]
    assert all(r["taxonomy_code"] == "H" for r in results)
    assert stats["llm_batches"] == 1
    assert stats["failed_batches"] == 1
    assert stats["llm_classified"] == 0
    assert stats["fallback_entries"] == 2


def test_failed_batch_logs_error_with_batch_info(monkeypatch, caplog):
    """The old print is now a logger error carrying exception + batch context."""
    monkeypatch.setattr(stage3b_classify, "call_llm", _boom)

    with caplog.at_level(logging.ERROR, logger=stage_3b.logger.name):
        classify_entries_batch(
            _entries(["Dean's Award for Excellence, 2015"]), _context(), TAXONOMY)

    records = [r for r in caplog.records
               if "batch classification failed" in r.getMessage()]
    assert len(records) == 1
    rec = records[0]
    assert rec.levelno == logging.ERROR
    assert rec.exc_info is not None
    assert "invalid model identifier" in str(rec.exc_info[1])
    assert "offset 0" in rec.getMessage()
    assert "HONORS AND AWARDS" in rec.getMessage()


def test_successful_batch_counts_llm_classified(monkeypatch):
    monkeypatch.setattr(
        stage3b_classify, "call_llm",
        lambda **kw: _ok_response([0, 1], model="test-model"))

    results, stats = classify_entries_batch(
        _entries(["Dean's Award for Excellence, 2015", "Teaching Prize, 2018"]),
        _context(), TAXONOMY)

    assert [r["classification_source"] for r in results] == ["llm", "llm"]
    assert stats["llm_batches"] == 1
    assert stats["failed_batches"] == 0
    assert stats["llm_classified"] == 2
    assert stats["fallback_entries"] == 0
    assert stats["model"] == "test-model"


def test_mixed_success_and_failure_batches_merge_stats(monkeypatch):
    """Two internal batches in ONE classify_entries_batch call: the first
    succeeds, the second raises. Pins the cross-batch aggregation in
    classify_entries_batch itself, distinct from run_stage_3b's per-group
    aggregation (each hierarchy group is its own classify_entries_batch call
    there, so that path never exercises summing multiple _BatchStats within
    a single call)."""
    calls = {"n": 0}

    def call(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _ok_response([0, 1], code="H")
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr(stage3b_classify, "call_llm", call)

    results, stats = classify_entries_batch(
        _entries([
            "Award A",
            "Award B",
            "Award C",
            "Award D",
        ]),
        _context(),
        TAXONOMY,
        batch_size=2,
    )

    assert calls["n"] == 2

    assert [r["classification_source"] for r in results] == [
        "llm",
        "llm",
        "fallback",
        "fallback",
    ]

    assert stats["llm_batches"] == 2
    assert stats["failed_batches"] == 1
    assert stats["llm_classified"] == 2
    assert stats["fallback_entries"] == 2
    assert stats["entries_classified"] == 4


@pytest.mark.parametrize(
    "response_json",
    [
        {"classifications": None},
        {"classifications": {}},
        {"classifications": "not-a-list"},
    ],
)
def test_invalid_classifications_container_falls_back(monkeypatch, response_json):
    """A malformed *container* (not just a malformed element inside it) must
    not crash the batch. ``{"classifications": None}`` used to reach
    ``for c in classifications`` uncaught (that loop is deliberately outside
    the call_llm try/except) and raise TypeError, escaping
    classify_entries_batch entirely instead of falling back per entry."""
    def call(**kwargs):
        return {
            "content": json.dumps(response_json),
            "prompt_tokens": 40,
            "completion_tokens": 8,
            "cost": 0.0008,
            "model": "test-model",
        }

    monkeypatch.setattr(stage3b_classify, "call_llm", call)

    results, stats = classify_entries_batch(
        _entries(["Award A", "Award B"]),
        _context(),
        TAXONOMY,
        batch_size=2,
    )

    assert len(results) == 2
    assert all(r["classification_source"] == "fallback" for r in results)


def test_omitted_index_falls_back_without_batch_failure(monkeypatch):
    """An entry the LLM skipped is a fallback, but NOT a failed batch."""
    monkeypatch.setattr(stage3b_classify, "call_llm", lambda **kw: _ok_response([0]))

    results, stats = classify_entries_batch(
        _entries(["Dean's Award for Excellence, 2015", "Teaching Prize, 2018"]),
        _context(), TAXONOMY)

    assert [r["classification_source"] for r in results] == ["llm", "fallback"]
    assert stats["failed_batches"] == 0
    assert stats["llm_classified"] == 1
    assert stats["fallback_entries"] == 1


def test_empty_entries_never_attempt_llm(monkeypatch):
    def _fail(**kwargs):
        raise AssertionError("call_llm must not run for empty-text entries")
    monkeypatch.setattr(stage3b_classify, "call_llm", _fail)

    results, stats = classify_entries_batch(
        _entries(["", "   "]), _context(), TAXONOMY)

    assert [r["classification_source"] for r in results] == ["empty_entry"] * 2
    assert stats["llm_batches"] == 0
    assert stats["failed_batches"] == 0
    assert stats["empty_entries"] == 2


# --- run_stage_3b: run-level surfacing ----------------------------------------

def _write_run_fixtures(tmp_path, entries, mappings):
    stage_2 = tmp_path / "entries.json"
    stage_2.write_text(json.dumps({"entries": entries}))
    stage_3a = tmp_path / "header_taxonomy.json"
    stage_3a.write_text(json.dumps({"mappings": mappings}))
    return stage_2, stage_3a


_MAPPINGS = [
    {"title": "HONORS AND AWARDS",
     "taxonomy_options": [{"code": "H", "confidence": 0.9}], "children": []},
    {"title": "MEMBERSHIPS",
     "taxonomy_options": [{"code": "I", "confidence": 0.9}], "children": []},
]

_RUN_ENTRIES = [
    {"element_type": "text", "text": "Dean's Award for Excellence, 2015",
     "hierarchy": ["HONORS AND AWARDS"]},
    {"element_type": "text", "text": "American College of Physicians, 2010-present",
     "hierarchy": ["MEMBERSHIPS"]},
]


def test_run_fails_when_every_batch_fails(monkeypatch, tmp_path):
    """Zero successful LLM classifications in a nonempty run -> raise, no output."""
    monkeypatch.setattr(stage3b_classify, "call_llm", _boom)
    stage_2, stage_3a = _write_run_fixtures(tmp_path, _RUN_ENTRIES, _MAPPINGS)
    out_dir = tmp_path / "out"

    with pytest.raises(RuntimeError, match="zero LLM classifications"):
        stage_3b.run_stage_3b(
            "9999_Doe_Jane_CV",
            stage_2_path=str(stage_2),
            stage_3a_path=str(stage_3a),
            output_dir=str(out_dir),
        )

    assert not (out_dir / "9999_Doe_Jane_CV_classified.json").exists()


def test_run_survives_partial_failure_and_reports_stats(monkeypatch, tmp_path):
    """One failed batch of two: run completes and the output JSON says so."""
    calls = {"n": 0}

    def _first_call_fails(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("ThrottlingException: rate exceeded")
        return _ok_response([0], code="I")

    monkeypatch.setattr(stage3b_classify, "call_llm", _first_call_fails)
    stage_2, stage_3a = _write_run_fixtures(tmp_path, _RUN_ENTRIES, _MAPPINGS)
    out_dir = tmp_path / "out"

    result = stage_3b.run_stage_3b(
        "9999_Doe_Jane_CV",
        stage_2_path=str(stage_2),
        stage_3a_path=str(stage_3a),
        output_dir=str(out_dir),
    )

    output = json.loads(Path(result["output_path"]).read_text())
    cstats = output["meta"]["classification_stats"]
    assert cstats["llm_batches"] == 2
    assert cstats["failed_batches"] == 1
    assert cstats["llm_classified"] == 1
    assert cstats["fallback_entries"] == 1
    assert cstats["fallback_rate"] == 0.5
    assert cstats["had_classification_errors"] is True

    sources = sorted(e["classification_source"] for e in output["entries"])
    assert sources == ["fallback", "llm"]


def test_run_with_zero_entries_does_not_raise_zero_llm_error(monkeypatch, tmp_path):
    """Zero entries (empty content list) is NOT the same state as "entries
    exist but their text is blank" (see test_run_with_only_empty_entries_does_not_fail
    below): here there are no hierarchy groups at all, so llm_batches stays 0
    and the "zero LLM classifications" guard -- which only fires when
    llm_batches > 0 -- must not raise."""
    def _fail(**kwargs):
        raise AssertionError("call_llm must not be called")

    monkeypatch.setattr(stage3b_classify, "call_llm", _fail)

    stage_2, stage_3a = _write_run_fixtures(
        tmp_path,
        [],
        _MAPPINGS,
    )
    out_dir = tmp_path / "out"

    result = stage_3b.run_stage_3b(
        "9999_Doe_Jane_CV",
        stage_2_path=str(stage_2),
        stage_3a_path=str(stage_3a),
        output_dir=str(out_dir),
    )

    output = json.loads(Path(result["output_path"]).read_text())

    assert output["entries"] == []
    assert output["meta"]["classification_stats"]["llm_batches"] == 0
    assert output["meta"]["classification_stats"]["failed_batches"] == 0


def test_run_with_only_empty_entries_does_not_fail(monkeypatch, tmp_path):
    """No LLM call was ever attempted -> nothing failed, run completes."""
    def _fail(**kwargs):
        raise AssertionError("call_llm must not run for empty-text entries")
    monkeypatch.setattr(stage3b_classify, "call_llm", _fail)

    empty_entries = [
        {"element_type": "text", "text": "  ", "hierarchy": ["HONORS AND AWARDS"]},
    ]
    stage_2, stage_3a = _write_run_fixtures(tmp_path, empty_entries, _MAPPINGS)
    out_dir = tmp_path / "out"

    result = stage_3b.run_stage_3b(
        "9999_Doe_Jane_CV",
        stage_2_path=str(stage_2),
        stage_3a_path=str(stage_3a),
        output_dir=str(out_dir),
    )

    output = json.loads(Path(result["output_path"]).read_text())
    cstats = output["meta"]["classification_stats"]
    assert cstats["llm_batches"] == 0
    assert cstats["failed_batches"] == 0
    assert cstats["empty_entries"] == 1
    assert cstats["had_classification_errors"] is False
