"""Regression guard for issue #459: stage artifacts recorded a model the run
never used.

Four stages write a model name into their own output JSON. Each takes a
``model: str = "gpt-5.1"`` (5c: ``"gpt-4o-mini"``) parameter that no
orchestrator ever passes, so the default was recorded verbatim. Measured over
the 100-CV corpus of the 2026-07-25 batch:

    stage_3b  100/100 runs mislabelled
    stage_5b  100/100
    stage_5c   91/100
    stage_5d   98/100

stage_3b is the one that matters most: it is deliberately on Haiku 4.5, so its
artifacts claiming a different model defeats exactly the comparison the field
exists for. Same root cause as #444 -- a model asserted at the write site rather
than observed from the call. stage_3b's `model` parameter was later removed
outright (#644 review); the other three followed (#954) -- none was ever
forwarded to call_llm().

The tests use a SENTINEL model id rather than the shipped config: asserting the
artifact equals whatever ``llm_config.yaml`` resolves would pass against a
hardcoded string too.

    python3 -m pytest src/unified_pipeline/tests/test_stage_artifact_model_provenance.py -p no:cacheprovider

Self-contained: no DB, no network, no real LLM call.
"""

import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

SENTINEL = "sentinel-model-provenance-4242"


def _fake_llm_result(content="{}"):
    return {
        "content": content,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost": 0.001,
        "model": SENTINEL,
        "provider": "bedrock",
        "finish_reason": "stop",
        "latency_ms": 12,
    }


def test_5d_usage_carries_the_model_the_api_returned(monkeypatch):
    from unified_pipeline import stage_5d_citation_formatter as s5d
    monkeypatch.setattr(s5d, "call_llm", lambda **kw: _fake_llm_result('{"a":1}'))
    _text, usage = s5d.call_llm_formatter("some raw content")
    assert usage is not None, "the helper must return usage for this to work"
    assert usage.get("model") == SENTINEL, \
        "the observed model is not carried out of the call (#459)"


def test_5c_usage_carries_the_model_the_api_returned(monkeypatch):
    from unified_pipeline import stage_5c_teaching_formatter as s5c
    monkeypatch.setattr(s5c, "call_llm", lambda **kw: _fake_llm_result('{"a":1}'))
    _text, usage = s5c.call_llm_formatter("some raw content", verbose=False)
    assert usage is not None
    assert usage.get("model") == SENTINEL


def test_5b_lookup_returns_the_model_the_api_returned(monkeypatch):
    from unified_pipeline import stage_5b_institution_enrichment as s5b
    # The #523 split moved lookup_institutions_llm (and its call_llm binding)
    # into stage5b/lookup.py; patching the old facade module would patch a
    # name the moved function no longer reads.
    from unified_pipeline.stage5b import lookup as s5b_lookup
    monkeypatch.setattr(s5b_lookup, "call_llm", lambda **kw: _fake_llm_result('{"x": {}}'))
    batch = [("inst-1", "Some University", "context line")]
    results, cost, model = s5b.lookup_institutions_llm(batch, None, verbose=False)
    assert model == SENTINEL
    assert results == {"x": {}}
    assert cost == 0.001


def test_5b_artifact_records_observed_model_not_just_the_helper_return(monkeypatch, tmp_path):
    """The helper returning the observed model isn't enough -- prove
    run_stage5b() actually WRITES it into institution_enrichment_stats.model
    in the output artifact. A regression at the artifact-writing layer (e.g.
    someone reintroducing `model` as the recorded value instead of
    `observed_model or model`) would pass every other test in this file.
    """
    from unified_pipeline import stage_5b_institution_enrichment as s5b
    from unified_pipeline.stage5b import cache as s5b_cache
    from unified_pipeline.stage5b import lookup as s5b_lookup

    # Isolate the on-disk cache so this test can't read or write the real one.
    monkeypatch.setattr(s5b_cache, "CACHE_FILE", tmp_path / "institution_cache.json")
    monkeypatch.setattr(s5b_cache, "OLD_CACHE_FILE", tmp_path / "ror_cache.json")
    monkeypatch.setattr(s5b_lookup, "call_llm",
                         lambda **kw: _fake_llm_result('{"INST-0001": {"city": "Ithaca", "state": "New York", "country": "United States", "country_code": "US"}}'))

    input_path = tmp_path / "input.json"
    input_path.write_text(json.dumps({
        "document_uid": "test-uid",
        "entries": [{
            "taxonomy_code": "B1",
            "extracted_fields": {"institution": "Cornell University"},
        }],
    }))
    output_path = tmp_path / "output.json"

    s5b.run_stage5b(str(input_path), output_path=str(output_path), verbose=False, refresh_cache=True)

    written = json.loads(output_path.read_text())
    recorded_model = written["institution_enrichment_stats"]["model"]
    assert recorded_model == SENTINEL, (
        f"artifact recorded model={recorded_model!r}, expected the observed "
        f"SENTINEL -- the write site may be using the inert `model` default "
        f"instead of `observed_model`"
    )
    assert recorded_model != "gpt-5.1", "recorded the inert default, not the observed model"


def test_stage_5_model_parameters_were_removed_not_deprioritized():
    """5b/5c/5d and the candidate surfacer now match stage_3b: their inert
    `model` parameters (and the `--model` CLI flags that fed them) are gone,
    so no caller can believe it is selecting a model. The model comes only
    from config/llm_config.yaml via call_llm(stage=...)."""
    import inspect
    from unified_pipeline import (stage_5b_institution_enrichment as s5b,
                                  stage_5c_teaching_formatter as s5c,
                                  stage_5d_citation_formatter as s5d)
    from unified_pipeline.stage5b import lookup
    from unified_pipeline.core import candidate_surfacer
    for fn in (s5b.run_stage5b, s5c.run_stage_5c, s5d.run_stage_5d,
               lookup.lookup_institutions_llm,
               candidate_surfacer.surface_candidates_for_subsection):
        assert "model" not in inspect.signature(fn).parameters, fn.__qualname__


def test_stage_3b_model_parameter_was_removed_not_deprioritized():
    """stage_3b took the harder fix of the four: its `model` parameter was
    never forwarded to call_llm() at all, so it was removed outright rather
    than kept around and merely outranked by the observed model (#644
    review -- a parameter that doesn't affect behavior is dangerous
    precisely because callers believe they're selecting a model when they
    aren't). Guards against it quietly coming back as dead weight."""
    import inspect
    from unified_pipeline import stage_3b_entry_classifier as s3b
    assert "model" not in inspect.signature(s3b.run_stage_3b).parameters


def test_3b_artifact_records_observed_model_not_a_default(monkeypatch, tmp_path):
    """run_stage_3b() must WRITE the model that served the call into the
    artifact (``meta.model``, mirrored in ``meta.stats.model``). The helper
    tests elsewhere prove classify_entries_batch() returns the observed
    model; only this one covers the artifact-writing layer, where a
    regression to a hard-coded or configured default would pass the rest of
    this file (#699, review of #642)."""
    from unified_pipeline import stage_3b_entry_classifier as s3b
    from unified_pipeline.stage3b import classify as s3b_classify

    classification = {"classifications": [{"index": 0, "code": "H", "confidence": 0.9}]}
    monkeypatch.setattr(s3b_classify, "call_llm",
                        lambda **kw: _fake_llm_result(json.dumps(classification)))

    stage_2 = tmp_path / "entries.json"
    stage_2.write_text(json.dumps({"entries": [{
        "element_type": "text",
        "text": "Synthetic Award for Synthetic Work, 2015",
        "hierarchy": ["HONORS AND AWARDS"],
    }]}))
    stage_3a = tmp_path / "header_taxonomy.json"
    stage_3a.write_text(json.dumps({"mappings": [{
        "title": "HONORS AND AWARDS",
        "taxonomy_options": [{"code": "H", "confidence": 0.9}],
        "children": [],
    }]}))
    out_dir = tmp_path / "out"

    s3b.run_stage_3b("0000_Test_Synthetic_CV", stage_2_path=str(stage_2),
                     stage_3a_path=str(stage_3a), output_dir=str(out_dir))

    written = json.loads((out_dir / "0000_Test_Synthetic_CV_classified.json").read_text())
    assert written["entries"][0]["classification_source"] == "llm", \
        "the sentinel call must have classified the entry, not fallen back"
    recorded_model = written["meta"]["model"]
    assert recorded_model == SENTINEL, (
        f"artifact recorded model={recorded_model!r}, expected the observed SENTINEL")
    assert written["meta"]["stats"]["model"] == SENTINEL
    assert recorded_model != "gpt-5.1", "recorded the stale default, not the observed model"
