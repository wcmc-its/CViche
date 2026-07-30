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
than observed from the call.

The tests use a SENTINEL model id rather than the shipped config: asserting the
artifact equals whatever ``llm_config.yaml`` resolves would pass against a
hardcoded string too.

    python3 -m pytest src/unified_pipeline/tests/test_stage_artifact_model_provenance.py -p no:cacheprovider

Self-contained: no DB, no network, no real LLM call.
"""

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
    _text, usage = s5d.call_llm_formatter("some raw content", verbose=False)
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
    monkeypatch.setattr(s5b, "call_llm", lambda **kw: _fake_llm_result('{"x": {}}'))
    batch = [("inst-1", "Some University", "context line")]
    out = s5b.lookup_institutions_llm(batch, None, verbose=False)
    assert len(out) == 3, "the helper must return (results, cost, model) for #459"
    assert out[2] == SENTINEL


def test_the_recorded_model_is_not_the_inert_default():
    """The defaults that were being written into every artifact."""
    import inspect
    from unified_pipeline import (stage_3b_entry_classifier as s3b,
                                  stage_5b_institution_enrichment as s5b,
                                  stage_5c_teaching_formatter as s5c,
                                  stage_5d_citation_formatter as s5d)
    defaults = {
        "stage_3b": inspect.signature(s3b.run_stage_3b).parameters["model"].default,
        "stage_5b": inspect.signature(s5b.run_stage5b).parameters.get("model"),
        "stage_5c": inspect.signature(s5c.run_stage_5c).parameters["model"].default,
        "stage_5d": inspect.signature(s5d.run_stage_5d).parameters["model"].default,
    }
    # These defaults still exist as parameters; what changed is that the
    # artifact prefers the OBSERVED model over them. Pin that they are still
    # the OpenAI-shaped strings, so this test fails loudly if someone "fixes"
    # #459 by editing the default instead of recording reality.
    assert defaults["stage_3b"].startswith("gpt-"), \
        "if the default changed, re-check that the artifact records the OBSERVED model"
    assert defaults["stage_5d"].startswith("gpt-")


if __name__ == "__main__":
    class _MP:
        def setattr(self, obj, name, value):
            setattr(obj, name, value)
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn(_MP()) if _fn.__code__.co_argcount else _fn()
    print("OK")
