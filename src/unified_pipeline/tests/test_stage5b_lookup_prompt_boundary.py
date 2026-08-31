"""Regression guard for #642 review (lookup.py:69 follow-up): a lightweight
validation boundary on the CV-derived strings that reach the institution
lookup prompt -- institution name, per-entry context, and the built owner
context -- so a non-string or anomalously long value can't reach the LLM
prompt unvalidated.

This is a structural boundary (type/length), not a second injection filter;
INSTITUTION_SYSTEM_PROMPT already carries the "DATA, not instructions"
language for prompt-injection concerns. These tests only pin the type
check and the length cap.

    python3 -m pytest src/unified_pipeline/tests/test_stage5b_lookup_prompt_boundary.py -p no:cacheprovider

Self-contained: no DB, no network, no real LLM call.
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.stage5b.lookup import (
    _MAX_PROMPT_FIELD_LEN,
    _sanitize_prompt_field,
    lookup_institutions_llm,
)


def _fake_llm_result(content="{}"):
    return {
        "content": content,
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost": 0.001,
        "model": "sentinel-prompt-boundary",
        "provider": "bedrock",
        "finish_reason": "stop",
        "latency_ms": 12,
    }


# --- unit tests on the helper itself ---

def test_sanitize_prompt_field_passes_through_well_formed_string():
    assert _sanitize_prompt_field("Cornell University", "institution_name") == "Cornell University"


def test_sanitize_prompt_field_passes_through_empty_string():
    assert _sanitize_prompt_field("", "context") == ""


def test_sanitize_prompt_field_rejects_dict_without_stringifying_it(caplog):
    value = {"name": "Cornell University"}
    result = _sanitize_prompt_field(value, "institution_name")
    assert result == ""
    # Never let a non-str reach an f-string silently as str(non_str_thing).
    assert "object at 0x" not in result
    assert any(
        "Non-string institution_name" in record.message and "dict" in record.message
        for record in caplog.records
    )


def test_sanitize_prompt_field_rejects_list_without_stringifying_it():
    value = ["Cornell University", "Ithaca"]
    assert _sanitize_prompt_field(value, "context") == ""


def test_sanitize_prompt_field_rejects_arbitrary_object_without_stringifying_it():
    class _Institution:
        pass

    result = _sanitize_prompt_field(_Institution(), "institution_name")
    assert result == ""
    assert "object at 0x" not in result


def test_sanitize_prompt_field_truncates_absurdly_long_string():
    long_name = "A" * (_MAX_PROMPT_FIELD_LEN * 4)
    result = _sanitize_prompt_field(long_name, "institution_name")
    assert len(result) < len(long_name)
    assert result.startswith("A" * 10)
    assert result.endswith("[truncated]")
    assert len(result) == _MAX_PROMPT_FIELD_LEN + len("... [truncated]")


def test_sanitize_prompt_field_leaves_string_under_the_cap_unchanged():
    name = "A" * (_MAX_PROMPT_FIELD_LEN - 1)
    assert _sanitize_prompt_field(name, "institution_name") == name


def test_sanitize_prompt_field_boundary_length_is_not_truncated():
    name = "A" * _MAX_PROMPT_FIELD_LEN
    assert _sanitize_prompt_field(name, "institution_name") == name


# --- integration: the boundary is actually applied inside lookup_institutions_llm ---

def test_lookup_institutions_llm_handles_non_string_institution_name(monkeypatch):
    """A dict/list slipping through as institution_name must not crash the
    lookup or leak a garbage repr into the prompt -- confirmed via the
    captured user_prompt passed to call_llm."""
    captured = {}

    def _fake_call_llm(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _fake_llm_result('{"INST-0001": {}}')

    monkeypatch.setattr("unified_pipeline.stage5b.lookup.call_llm", _fake_call_llm)

    batch = [("INST-0001", {"name": "Cornell University"}, "B1")]
    results, cost, model = lookup_institutions_llm(batch, None, verbose=False)

    assert results == {"INST-0001": {}}
    user_prompt = captured["messages"][1]["content"]
    assert "object at 0x" not in user_prompt
    assert "{'name':" not in user_prompt


def test_lookup_institutions_llm_handles_non_string_context(monkeypatch):
    def _fake_call_llm(**kwargs):
        return _fake_llm_result('{"INST-0001": {}}')

    monkeypatch.setattr("unified_pipeline.stage5b.lookup.call_llm", _fake_call_llm)

    batch = [("INST-0001", "Cornell University", ["B1", "unexpected list"])]
    results, cost, model = lookup_institutions_llm(batch, None, verbose=False)
    assert results == {"INST-0001": {}}


def test_lookup_institutions_llm_truncates_absurdly_long_institution_name(monkeypatch):
    captured = {}

    def _fake_call_llm(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _fake_llm_result('{"INST-0001": {}}')

    monkeypatch.setattr("unified_pipeline.stage5b.lookup.call_llm", _fake_call_llm)

    long_name = "University of " + ("X" * (_MAX_PROMPT_FIELD_LEN * 3))
    batch = [("INST-0001", long_name, "")]
    results, cost, model = lookup_institutions_llm(batch, None, verbose=False)

    assert results == {"INST-0001": {}}
    user_prompt = captured["messages"][1]["content"]
    assert long_name not in user_prompt
    assert "[truncated]" in user_prompt


def test_lookup_institutions_llm_well_formed_input_is_unaffected(monkeypatch):
    """Existing well-formed-input behavior is unchanged by the boundary."""
    captured = {}

    def _fake_call_llm(**kwargs):
        captured["messages"] = kwargs["messages"]
        return _fake_llm_result('{"INST-0001": {"city": "Ithaca"}}')

    monkeypatch.setattr("unified_pipeline.stage5b.lookup.call_llm", _fake_call_llm)

    batch = [("INST-0001", "Cornell University", "B1, MD, 2020-2024")]
    results, cost, model = lookup_institutions_llm(batch, None, verbose=False)

    assert results == {"INST-0001": {"city": "Ithaca"}}
    assert cost == 0.001
    assert model == "sentinel-prompt-boundary"
    user_prompt = captured["messages"][1]["content"]
    assert "Cornell University" in user_prompt
    assert "B1, MD, 2020-2024" in user_prompt
