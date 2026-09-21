"""Behaviour coverage for the Bedrock and OpenAI provider adapters and the
shared retry infrastructure (#704), split out of llm_client.py by #496:

    unified_pipeline/llm/bedrock.py  -- Converse API translation, schema
        enforcement (#46), markdown-fence stripping, JSON-repair retry.
    unified_pipeline/llm/openai.py   -- raw chat.completions call + response
        normalization.
    unified_pipeline/llm/retry.py    -- exponential backoff / retry
        classification, and the llm_config.yaml tuning knobs.

No network, ever. Both providers' real SDK clients are never constructed:
``_get_bedrock_client`` / ``_get_openai_client`` are monkeypatched to return
a small fake object that records the kwargs it was called with and returns a
canned response shaped like the real Converse API / ChatCompletion SDK
return value (read from the source, not guessed). ``call_llm`` (the
llm_client.py facade) is never imported or invoked here -- these tests go
straight at the provider-adapter functions it dispatches to. ``time.sleep``
is monkeypatched to a no-op recorder wherever a retry path might sleep;
``time.monotonic`` is left untouched (asyncio.run() depends on it elsewhere
in the pipeline -- swapping it globally breaks unrelated duration math).

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_llm_adapters_behaviour.py -p no:cacheprovider
"""

import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import httpx
import openai
import pytest
from botocore.exceptions import ClientError

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.llm.bedrock as bedrock  # noqa: E402
import unified_pipeline.llm.openai as openai_mod  # noqa: E402
import unified_pipeline.llm.retry as retry  # noqa: E402

BEDROCK_MODEL = "anthropic.claude-haiku-4-5"
OPENAI_MODEL = "gpt-4o-mini"


# ---------------------------------------------------------------------------
# Fakes -- no real SDK client is ever constructed.
# ---------------------------------------------------------------------------

class _FakeBedrockClient:
    """Stands in for the boto3 bedrock-runtime client. ``converse()`` pops
    the next canned response off a queue, so a test can script a first-call
    failure followed by a second-call success (the JSON-repair retry)."""

    def __init__(self, responses: list[dict]) -> None:
        self._responses = list(responses)
        self.calls: list[dict] = []

    def converse(self, **kwargs: object) -> dict:
        self.calls.append(kwargs)
        return self._responses.pop(0)


class _FakeCompletions:
    def __init__(self, response: object) -> None:
        self._response = response
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        return self._response


class _FakeOpenAIClient:
    def __init__(self, response: object) -> None:
        self.chat = SimpleNamespace(completions=_FakeCompletions(response))


def _bedrock_cfg(**overrides: object) -> dict:
    cfg = {
        "model": BEDROCK_MODEL,
        "temperature": 0.2,
        "max_tokens": None,
        "enable_prompt_caching": False,
        "extra_kwargs": {},
        "retry_count": 0,
    }
    cfg.update(overrides)
    return cfg


def _openai_cfg(**overrides: object) -> dict:
    cfg = {
        "model": OPENAI_MODEL,
        "temperature": 0.2,
        "max_tokens": None,
        "extra_kwargs": {},
        "retry_count": 0,
    }
    cfg.update(overrides)
    return cfg


def _converse_response(text: str, *, stop_reason: str = "end_turn",
                       input_tokens: int = 10, output_tokens: int = 5) -> dict:
    return {
        "output": {"message": {"content": [{"text": text}]}},
        "stopReason": stop_reason,
        "usage": {"inputTokens": input_tokens, "outputTokens": output_tokens},
    }


# ---------------------------------------------------------------------------
# bedrock.py -- small pure helpers
# ---------------------------------------------------------------------------

def test_wants_json_true_only_for_json_object_or_schema() -> None:
    assert bedrock._wants_json(None) is False
    assert bedrock._wants_json({"type": "text"}) is False
    assert bedrock._wants_json({"type": "json_object"}) is True
    assert bedrock._wants_json({"type": "json_schema"}) is True


def test_schema_tool_config_none_when_not_requested_or_wrong_shape() -> None:
    assert bedrock._schema_tool_config(None) is None
    assert bedrock._schema_tool_config({"type": "json_object"}) is None
    # top-level schema type must be "object" -- Converse inputSchema constraint
    non_object = {"type": "json_schema", "json_schema": {"schema": {"type": "string"}}}
    assert bedrock._schema_tool_config(non_object) is None


def test_schema_tool_config_builds_forced_tool_choice() -> None:
    response_format = {
        "type": "json_schema",
        "json_schema": {
            "name": "extract_v1",
            "description": "extract fields",
            "schema": {"type": "object", "properties": {"foo": {"type": "string"}}},
        },
    }
    cfg = bedrock._schema_tool_config(response_format)
    assert cfg == {
        "tools": [{"toolSpec": {
            "name": "extract_v1",
            "description": "extract fields",
            "inputSchema": {"json": {"type": "object", "properties": {"foo": {"type": "string"}}}},
        }}],
        "toolChoice": {"tool": {"name": "extract_v1"}},
    }


def test_schema_tool_config_defaults_name_and_description() -> None:
    response_format = {"type": "json_schema", "json_schema": {"schema": {"type": "object"}}}
    cfg = bedrock._schema_tool_config(response_format)
    tool_spec = cfg["tools"][0]["toolSpec"]
    assert tool_spec["name"] == "structured_output"
    assert "exactly this schema" in tool_spec["description"]


def test_schema_tool_config_rejects_bad_bedrock_tool_name() -> None:
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "bad name!", "schema": {"type": "object"}},
    }
    with pytest.raises(ValueError, match="1-64 characters"):
        bedrock._schema_tool_config(response_format)


def test_extract_tool_use_input_finds_tool_block() -> None:
    response = {"output": {"message": {"content": [
        {"text": "preamble"},
        {"toolUse": {"name": "extract", "input": {"k": "v"}}},
    ]}}}
    assert bedrock._extract_tool_use_input(response) == {"k": "v"}


def test_extract_tool_use_input_none_for_text_only_response() -> None:
    response = {"output": {"message": {"content": [{"text": "just text"}]}}}
    assert bedrock._extract_tool_use_input(response) is None
    assert bedrock._extract_tool_use_input({}) is None


def test_strip_markdown_fences_json_language_tag() -> None:
    assert bedrock._strip_markdown_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_markdown_fences_plain_fence() -> None:
    assert bedrock._strip_markdown_fences('```\n{"a": 1}\n```') == '{"a": 1}'


def test_strip_markdown_fences_unfenced_text_unchanged() -> None:
    assert bedrock._strip_markdown_fences("plain response, no fence") == "plain response, no fence"


def test_strip_markdown_fences_non_string_passthrough() -> None:
    assert bedrock._strip_markdown_fences(None) is None
    marker = {"not": "a string"}
    assert bedrock._strip_markdown_fences(marker) is marker


def test_strip_markdown_fences_opening_fence_with_no_newline_unchanged() -> None:
    # starts AND ends with ``` (it IS just "```"), but find("\n") == -1
    assert bedrock._strip_markdown_fences("```") == "```"


def test_translate_messages_basic_no_response_format() -> None:
    messages = [
        {"role": "system", "content": "sys prompt"},
        {"role": "user", "content": "hello"},
    ]
    system_prompts, converse_messages = bedrock._translate_messages(messages)
    assert system_prompts == [{"text": "sys prompt"}]
    assert converse_messages == [{"role": "user", "content": [{"text": "hello"}]}]


def test_translate_messages_json_hint_appended_to_existing_system() -> None:
    messages = [
        {"role": "system", "content": "sys prompt"},
        {"role": "user", "content": "hello"},
    ]
    system_prompts, _ = bedrock._translate_messages(
        messages, response_format={"type": "json_object"}, use_schema_tool=False
    )
    assert system_prompts == [{"text": "sys prompt\n\nRespond with valid JSON only."}]


def test_translate_messages_json_hint_suppressed_when_schema_tool_used() -> None:
    messages = [
        {"role": "system", "content": "sys prompt"},
        {"role": "user", "content": "hello"},
    ]
    system_prompts, _ = bedrock._translate_messages(
        messages, response_format={"type": "json_schema"}, use_schema_tool=True
    )
    assert system_prompts == [{"text": "sys prompt"}]


def test_translate_messages_synthesizes_system_when_absent() -> None:
    system_prompts, converse_messages = bedrock._translate_messages(
        [{"role": "user", "content": "hi"}], response_format={"type": "json_object"}
    )
    assert system_prompts == [{"text": "Respond with valid JSON only."}]
    assert converse_messages == [{"role": "user", "content": [{"text": "hi"}]}]


def test_translate_messages_multimodal_system_raises() -> None:
    messages = [{"role": "system", "content": [{"type": "text", "text": "x"}]}]
    with pytest.raises(NotImplementedError, match="multimodal"):
        bedrock._translate_messages(messages)


def test_translate_messages_multimodal_user_raises() -> None:
    messages = [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}]
    with pytest.raises(NotImplementedError, match="multimodal"):
        bedrock._translate_messages(messages)


def test_validate_json_response_no_format_requested_always_true() -> None:
    assert bedrock._validate_json_response("not json at all", None) is True


def test_validate_json_response_valid_and_invalid() -> None:
    assert bedrock._validate_json_response('{"a": 1}', {"type": "json_object"}) is True
    assert bedrock._validate_json_response("not json", {"type": "json_object"}) is False


def test_validate_json_response_strips_fence_before_parsing() -> None:
    fenced = '```json\n{"a": 1}\n```'
    assert bedrock._validate_json_response(fenced, {"type": "json_object"}) is True


def test_extract_cache_tokens_empty_usage() -> None:
    assert bedrock._extract_cache_tokens(None) == (0, 0)
    assert bedrock._extract_cache_tokens({}) == (0, 0)


def test_extract_cache_tokens_reads_all_naming_variants() -> None:
    assert bedrock._extract_cache_tokens(
        {"cacheReadInputTokens": 5, "cacheWriteInputTokens": 3}
    ) == (5, 3)
    assert bedrock._extract_cache_tokens(
        {"cacheReadInputTokensCount": 7, "cacheWriteInputTokensCount": 2}
    ) == (7, 2)
    assert bedrock._extract_cache_tokens(
        {"CacheReadInputTokens": 9, "CacheWriteInputTokens": 4}
    ) == (9, 4)


def test_finalize_bedrock_result_sums_cached_and_uncached_tokens() -> None:
    usage = {"inputTokens": 100, "outputTokens": 50}
    result = bedrock._finalize_bedrock_result(
        "content-x", usage, cache_read_tokens=10, cache_write_tokens=20,
        finish_reason="stop", model=BEDROCK_MODEL, latency_ms=250,
    )
    assert result["content"] == "content-x"
    assert result["prompt_tokens"] == 130
    assert result["completion_tokens"] == 50
    assert result["total_tokens"] == 180
    assert result["cache_read_tokens"] == 10
    assert result["cache_write_tokens"] == 20
    assert result["provider"] == "bedrock"
    assert result["finish_reason"] == "stop"
    assert result["latency_ms"] == 250
    assert result["cost"] > 0


# ---------------------------------------------------------------------------
# bedrock.py -- _call_bedrock (request construction against a fake client)
# ---------------------------------------------------------------------------

def test_call_bedrock_defaults_max_tokens_when_omitted(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = _converse_response("hi")
    fake = _FakeBedrockClient([canned])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    response = bedrock._call_bedrock(
        BEDROCK_MODEL, [{"role": "user", "content": "hi"}], 0.7,
        response_format=None, max_tokens=None, enable_prompt_caching=False,
    )

    assert response is canned  # client.converse() return value passed through unchanged
    sent = fake.calls[0]
    assert sent["modelId"] == BEDROCK_MODEL
    assert sent["inferenceConfig"]["temperature"] == 0.7
    assert sent["inferenceConfig"]["maxTokens"] == bedrock.DEFAULT_MAX_TOKENS
    assert "toolConfig" not in sent
    assert "system" not in sent  # no system message given, none synthesized


def test_call_bedrock_preserves_explicit_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeBedrockClient([_converse_response("hi")])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    bedrock._call_bedrock(
        BEDROCK_MODEL, [{"role": "user", "content": "hi"}], 0.1,
        response_format=None, max_tokens=500, enable_prompt_caching=False,
    )

    assert fake.calls[0]["inferenceConfig"]["maxTokens"] == 500


def test_call_bedrock_enable_prompt_caching_appends_cache_point(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeBedrockClient([_converse_response("hi")])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]
    bedrock._call_bedrock(
        BEDROCK_MODEL, messages, 0.1,
        response_format=None, max_tokens=None, enable_prompt_caching=True,
    )

    assert fake.calls[0]["system"] == [{"text": "sys"}, {"cachePoint": {"type": "default"}}]


def test_call_bedrock_attaches_forced_tool_config_for_json_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeBedrockClient([_converse_response("hi")])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "extract", "schema": {"type": "object"}},
    }
    bedrock._call_bedrock(
        BEDROCK_MODEL, [{"role": "user", "content": "hi"}], 0.1,
        response_format=response_format, max_tokens=None, enable_prompt_caching=False,
    )

    assert fake.calls[0]["toolConfig"]["toolChoice"] == {"tool": {"name": "extract"}}


# ---------------------------------------------------------------------------
# bedrock.py -- _handle_bedrock (dispatch + normalization, both response shapes)
# ---------------------------------------------------------------------------

def test_handle_bedrock_text_path_maps_finish_reason_and_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = _converse_response("hello world", stop_reason="end_turn",
                                input_tokens=100, output_tokens=20)
    fake = _FakeBedrockClient([canned])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    result = bedrock._handle_bedrock(
        [{"role": "user", "content": "hi"}], response_format=None, cfg=_bedrock_cfg()
    )

    assert result["content"] == "hello world"
    assert result["finish_reason"] == "stop"  # STOP_REASON_MAP["end_turn"]
    assert result["provider"] == "bedrock"
    assert result["prompt_tokens"] == 100
    assert result["completion_tokens"] == 20
    assert result["total_tokens"] == 120
    assert result["cache_read_tokens"] == 0
    assert result["cache_write_tokens"] == 0
    assert result["cost"] > 0
    assert len(fake.calls) == 1  # valid text, no JSON requested -> no repair call


def test_handle_bedrock_schema_tool_path_returns_serialized_tool_input(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = {
        "output": {"message": {"content": [
            {"toolUse": {"name": "extract", "input": {"foo": "bar"}}},
        ]}},
        "stopReason": "tool_use",
        "usage": {"inputTokens": 50, "outputTokens": 10},
    }
    fake = _FakeBedrockClient([canned])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "extract", "schema": {"type": "object"}},
    }
    result = bedrock._handle_bedrock(
        [{"role": "user", "content": "hi"}], response_format=response_format, cfg=_bedrock_cfg()
    )

    # Only the forced-tool route produces this: the tool `input` re-serialized
    # as the content string, and finish_reason mapped from tool_use.
    assert json.loads(result["content"]) == {"foo": "bar"}
    assert result["finish_reason"] == "tool_calls"
    assert fake.calls[0]["toolConfig"]["toolChoice"] == {"tool": {"name": "extract"}}
    assert len(fake.calls) == 1  # no text-validation / repair path taken


def test_handle_bedrock_raises_when_forced_tool_did_not_fire(monkeypatch: pytest.MonkeyPatch) -> None:
    # Model replied with plain text instead of calling the forced tool.
    canned = _converse_response("sorry, no tool call", stop_reason="end_turn")
    fake = _FakeBedrockClient([canned])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "extract", "schema": {"type": "object"}},
    }
    with pytest.raises(RuntimeError, match="did not fire"):
        bedrock._handle_bedrock(
            [{"role": "user", "content": "hi"}], response_format=response_format, cfg=_bedrock_cfg()
        )


def test_handle_bedrock_repairs_invalid_json_on_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _converse_response("not json", stop_reason="end_turn",
                               input_tokens=30, output_tokens=5)
    second = _converse_response('{"a": 1}', stop_reason="end_turn",
                                input_tokens=40, output_tokens=8)
    fake = _FakeBedrockClient([first, second])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    result = bedrock._handle_bedrock(
        [{"role": "user", "content": "hi"}],
        response_format={"type": "json_object"},
        cfg=_bedrock_cfg(),
    )

    # Only the repair route produces valid JSON here (the first call's raw
    # content was "not json").
    assert json.loads(result["content"]) == {"a": 1}
    assert result["prompt_tokens"] == 30 + 40  # tokens accumulated across both calls
    assert result["completion_tokens"] == 5 + 8
    assert len(fake.calls) == 2
    repair_message = fake.calls[1]["messages"][-1]
    assert repair_message["content"] == [{
        "text": "Your previous response was not valid JSON. Please respond with "
                "ONLY valid JSON, no markdown fencing or explanation.",
    }]


def test_handle_bedrock_strips_fence_without_triggering_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    canned = _converse_response('```json\n{"x": 2}\n```', stop_reason="end_turn")
    fake = _FakeBedrockClient([canned])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    result = bedrock._handle_bedrock(
        [{"role": "user", "content": "hi"}],
        response_format={"type": "json_object"},
        cfg=_bedrock_cfg(),
    )

    assert result["content"] == '{"x": 2}'
    assert len(fake.calls) == 1  # fence-wrapped-but-valid never triggers the repair call


# ---------------------------------------------------------------------------
# openai.py -- _call_openai (request construction against a fake client)
# ---------------------------------------------------------------------------

def test_call_openai_minimal_call_omits_optional_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    sentinel_response = object()
    fake = _FakeOpenAIClient(sentinel_response)
    monkeypatch.setattr(openai_mod, "_get_openai_client", lambda: fake)

    result = openai_mod._call_openai(OPENAI_MODEL, [{"role": "user", "content": "hi"}], 0.3)

    assert result is sentinel_response
    sent = fake.chat.completions.calls[0]
    assert sent == {"model": OPENAI_MODEL, "messages": [{"role": "user", "content": "hi"}], "temperature": 0.3}
    assert "response_format" not in sent
    assert "max_completion_tokens" not in sent


def test_call_openai_includes_response_format_and_max_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeOpenAIClient(object())
    monkeypatch.setattr(openai_mod, "_get_openai_client", lambda: fake)

    openai_mod._call_openai(
        OPENAI_MODEL, [{"role": "user", "content": "hi"}], 0.5,
        response_format={"type": "json_object"}, max_tokens=200,
    )

    sent = fake.chat.completions.calls[0]
    assert sent["response_format"] == {"type": "json_object"}
    assert sent["max_completion_tokens"] == 200


def test_call_openai_filters_handled_kwargs_but_passes_through_others(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeOpenAIClient(object())
    monkeypatch.setattr(openai_mod, "_get_openai_client", lambda: fake)

    openai_mod._call_openai(
        OPENAI_MODEL, [{"role": "user", "content": "hi"}], 0.5,
        stage="5c", retry_count=2, provider="openai",  # must be filtered
        top_p=0.9,  # must pass through
        seed=None,  # None values are filtered regardless of key
    )

    sent = fake.chat.completions.calls[0]
    assert "stage" not in sent
    assert "retry_count" not in sent
    assert "provider" not in sent
    assert "seed" not in sent
    assert sent["top_p"] == 0.9


# ---------------------------------------------------------------------------
# openai.py -- _handle_openai (dispatch + normalization)
# ---------------------------------------------------------------------------

def _openai_response(content: str, *, finish_reason: str = "stop",
                     prompt_tokens: int = 40, completion_tokens: int = 10) -> SimpleNamespace:
    usage = SimpleNamespace(
        prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
        total_tokens=prompt_tokens + completion_tokens,
    )
    choice = SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=usage)


def test_handle_openai_normalizes_response_and_zeroes_cache_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _openai_response('{"a": 1}', finish_reason="stop",
                                prompt_tokens=40, completion_tokens=10)
    fake = _FakeOpenAIClient(response)
    monkeypatch.setattr(openai_mod, "_get_openai_client", lambda: fake)

    result = openai_mod._handle_openai(
        [{"role": "user", "content": "hi"}], response_format=None, cfg=_openai_cfg()
    )

    assert result["content"] == '{"a": 1}'
    assert result["prompt_tokens"] == 40
    assert result["completion_tokens"] == 10
    assert result["total_tokens"] == 50
    assert result["cache_read_tokens"] == 0  # OpenAI has no prompt-cache accounting
    assert result["cache_write_tokens"] == 0
    assert result["provider"] == "openai"
    assert result["finish_reason"] == "stop"
    assert result["model"] == OPENAI_MODEL
    assert result["cost"] > 0
    assert isinstance(result["latency_ms"], int)


def test_handle_openai_wires_response_format_into_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _openai_response('{"a": 1}')
    fake = _FakeOpenAIClient(response)
    monkeypatch.setattr(openai_mod, "_get_openai_client", lambda: fake)

    openai_mod._handle_openai(
        [{"role": "user", "content": "hi"}],
        response_format={"type": "json_object"},
        cfg=_openai_cfg(),
    )

    assert fake.chat.completions.calls[0]["response_format"] == {"type": "json_object"}


def test_handle_openai_different_finish_reason_and_length(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _openai_response("truncated output", finish_reason="length",
                                prompt_tokens=5, completion_tokens=2)
    fake = _FakeOpenAIClient(response)
    monkeypatch.setattr(openai_mod, "_get_openai_client", lambda: fake)

    result = openai_mod._handle_openai(
        [{"role": "user", "content": "hi"}], response_format=None, cfg=_openai_cfg()
    )

    assert result["finish_reason"] == "length"
    assert result["content"] == "truncated output"


# ---------------------------------------------------------------------------
# retry.py -- llm_config.yaml knob readers
# ---------------------------------------------------------------------------

def test_get_llm_config_int_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CVICHE_TEST_INT_KEY", raising=False)
    assert retry._get_llm_config_int("CVICHE_TEST_INT_KEY", default=42) == 42


def test_get_llm_config_int_default_when_unparseable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVICHE_TEST_INT_KEY", "not-a-number")
    assert retry._get_llm_config_int("CVICHE_TEST_INT_KEY", default=42) == 42


def test_get_llm_config_int_below_min_value_falls_back_to_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVICHE_TEST_INT_KEY", "0")
    assert retry._get_llm_config_int("CVICHE_TEST_INT_KEY", default=42, min_value=1) == 42


def test_get_llm_config_int_min_value_itself_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVICHE_TEST_INT_KEY", "1")
    assert retry._get_llm_config_int("CVICHE_TEST_INT_KEY", default=42, min_value=1) == 1


def test_get_llm_config_int_reads_good_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVICHE_TEST_INT_KEY", "7")
    assert retry._get_llm_config_int("CVICHE_TEST_INT_KEY", default=42) == 7


def test_get_llm_config_float_default_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CVICHE_TEST_FLOAT_KEY", raising=False)
    assert retry._get_llm_config_float("CVICHE_TEST_FLOAT_KEY", default=12.5) == 12.5


def test_get_llm_config_float_default_when_unparseable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVICHE_TEST_FLOAT_KEY", "not-a-float")
    assert retry._get_llm_config_float("CVICHE_TEST_FLOAT_KEY", default=12.5) == 12.5


def test_get_llm_config_float_at_min_value_excluded_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # default min_value=0.0 is EXCLUDED (result > min_value required), unlike
    # the int sibling where min_value itself is kept.
    monkeypatch.setenv("CVICHE_TEST_FLOAT_KEY", "0")
    assert retry._get_llm_config_float("CVICHE_TEST_FLOAT_KEY", default=12.5) == 12.5


def test_get_llm_config_float_reads_good_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVICHE_TEST_FLOAT_KEY", "45.5")
    assert retry._get_llm_config_float("CVICHE_TEST_FLOAT_KEY", default=12.5) == 45.5


def test_get_max_concurrent_llm_calls_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVICHE_MAX_CONCURRENT_LLM_CALLS", "5")
    assert retry._get_max_concurrent_llm_calls() == 5


def test_get_llm_timeout_seconds_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVICHE_LLM_TIMEOUT_SECONDS", "99.5")
    assert retry._get_llm_timeout_seconds() == 99.5


def test_get_llm_max_attempts_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CVICHE_LLM_MAX_ATTEMPTS", "6")
    assert retry._get_llm_max_attempts() == 6


# ---------------------------------------------------------------------------
# retry.py -- _call_with_retry
# ---------------------------------------------------------------------------

def test_call_with_retry_success_first_try_never_sleeps(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(retry.time, "sleep", lambda s: sleeps.append(s))

    result, api_seconds = retry._call_with_retry(lambda: "ok", retry_count=3)

    assert result == "ok"
    assert sleeps == []
    assert api_seconds >= 0


def test_call_with_retry_retries_generic_retryable_error_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(retry.time, "sleep", lambda s: sleeps.append(s))
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise openai.APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))
        return "recovered"

    result, _ = retry._call_with_retry(flaky, retry_count=2)

    assert result == "recovered"
    assert attempts["n"] == 2
    assert len(sleeps) == 1  # exactly one backoff between the two attempts


def test_call_with_retry_client_error_retryable_code_retries_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(retry.time, "sleep", lambda s: sleeps.append(s))
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "x"}}, "Converse")
        return "recovered"

    result, _ = retry._call_with_retry(flaky, retry_count=2)

    assert result == "recovered"
    assert attempts["n"] == 2
    assert len(sleeps) == 1


def test_call_with_retry_client_error_non_retryable_code_raises_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(retry.time, "sleep", lambda s: sleeps.append(s))
    attempts = {"n": 0}

    def always_denied() -> str:
        attempts["n"] += 1
        raise ClientError({"Error": {"Code": "AccessDeniedException", "Message": "nope"}}, "Converse")

    with pytest.raises(ClientError):
        retry._call_with_retry(always_denied, retry_count=3)

    assert attempts["n"] == 1  # no retry attempted for a non-retryable code
    assert sleeps == []


def test_call_with_retry_exhausts_attempts_and_raises_last_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # ModelTimeoutException, not ThrottlingException: it's retryable but NOT
    # in BEDROCK_OUTAGE_CODES (#810), so this exercises the ordinary
    # retry_count-bounded path. ThrottlingException now gets the separate,
    # much longer outage-budget path -- see the
    # test_call_with_retry_outage_* tests below.
    sleeps: list[float] = []
    monkeypatch.setattr(retry.time, "sleep", lambda s: sleeps.append(s))
    attempts = {"n": 0}

    def always_timed_out() -> str:
        attempts["n"] += 1
        raise ClientError({"Error": {"Code": "ModelTimeoutException", "Message": "x"}}, "Converse")

    with pytest.raises(ClientError) as exc_info:
        retry._call_with_retry(always_timed_out, retry_count=2)

    assert exc_info.value.response["Error"]["Code"] == "ModelTimeoutException"
    assert attempts["n"] == 3  # retry_count + 1 total attempts
    assert len(sleeps) == 2  # a backoff between each pair of attempts, none after the last


def test_call_with_retry_rejects_non_int_retry_count() -> None:
    with pytest.raises(TypeError):
        retry._call_with_retry(lambda: "x", retry_count="3")


def test_call_with_retry_rejects_bool_retry_count() -> None:
    with pytest.raises(TypeError):
        retry._call_with_retry(lambda: "x", retry_count=True)


def test_call_with_retry_rejects_negative_retry_count() -> None:
    with pytest.raises(ValueError):
        retry._call_with_retry(lambda: "x", retry_count=-1)


def test_call_with_retry_cancel_after_backoff_stops_further_attempts(monkeypatch: pytest.MonkeyPatch) -> None:
    # cancel_check is checked between attempts, after the backoff sleep and
    # before the next call_fn() -- so a cancel there propagates out of the
    # retry loop unchanged, and the provider is never invoked a second time.
    sleeps: list[float] = []
    monkeypatch.setattr(retry.time, "sleep", lambda s: sleeps.append(s))
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        raise openai.APIConnectionError(request=httpx.Request("POST", "https://example.invalid"))

    class Cancelled(Exception):
        pass

    checks = {"n": 0}

    def cancel_check() -> None:
        checks["n"] += 1
        raise Cancelled()

    with pytest.raises(Cancelled):
        retry._call_with_retry(flaky, retry_count=3, cancel_check=cancel_check)

    assert attempts["n"] == 1  # cancel fired before a second attempt started
    assert len(sleeps) == 1  # the first attempt's backoff still ran


# ---------------------------------------------------------------------------
# retry.py -- _call_with_retry's outage-class path (#810)
# ---------------------------------------------------------------------------

def _bedrock_outage_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "down"}}, "Converse")


def _openai_rate_limit_error() -> openai.RateLimitError:
    return openai.RateLimitError(
        "rate limited",
        response=httpx.Response(429, request=httpx.Request("POST", "https://example.invalid")),
        body=None,
    )


def _openai_internal_server_error() -> openai.InternalServerError:
    return openai.InternalServerError(
        "internal error",
        response=httpx.Response(500, request=httpx.Request("POST", "https://example.invalid")),
        body=None,
    )


@pytest.mark.parametrize(
    "make_error",
    [
        lambda: _bedrock_outage_error("ServiceUnavailableException"),
        lambda: _bedrock_outage_error("ThrottlingException"),
        _openai_rate_limit_error,
        _openai_internal_server_error,
    ],
    ids=[
        "bedrock_service_unavailable",
        "bedrock_throttling",
        "openai_rate_limit",
        "openai_internal_server_error",
    ],
)
def test_call_with_retry_outage_error_keeps_retrying_past_retry_count(
    monkeypatch: pytest.MonkeyPatch, make_error: Callable[[], Exception]
) -> None:
    # Every outage-class error -- both Bedrock codes named in
    # BEDROCK_OUTAGE_CODES and both OpenAI exception classes _is_outage_error
    # recognizes -- must NOT be bounded by retry_count=1 (which would allow
    # only 2 total attempts on the ordinary path). Verifier round 2 (m13):
    # dropping "ThrottlingException" from BEDROCK_OUTAGE_CODES survived with
    # the suite green because only ServiceUnavailableException had a test
    # here; this parametrization covers all four outage classes so no one
    # code/class can be silently dropped again.
    monkeypatch.setattr(retry.time, "sleep", lambda s: None)
    attempts = {"n": 0}

    def eventually_recovers() -> str:
        attempts["n"] += 1
        if attempts["n"] <= 3:
            raise make_error()
        return "recovered"

    result, _ = retry._call_with_retry(eventually_recovers, retry_count=1)

    assert result == "recovered"
    assert attempts["n"] > 2  # far more than retry_count(1)+1 == 2 would allow


def test_call_with_retry_outage_budget_exhausted_raises_llmoutageerror(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = {"t": 0.0}

    def fake_sleep(s: float) -> None:
        clock["t"] += s

    monkeypatch.setattr(retry.time, "sleep", fake_sleep)
    monkeypatch.setattr(retry.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(retry.random, "uniform", lambda lo, hi: hi)
    monkeypatch.setenv("CVICHE_LLM_OUTAGE_BUDGET_SECONDS", "50")
    attempts = {"n": 0}
    raised: list[ClientError] = []

    def always_unavailable() -> str:
        attempts["n"] += 1
        # A broken budget check (e.g. resetting outage_started on every
        # iteration instead of once) would otherwise spin this forever --
        # this test's fake time.sleep never actually sleeps, so a broken
        # budget check pins the test process rather than failing it
        # (verifier round 2, NOTE 4: `timeout 45` -> exit 124). Capping at
        # well past the 7 attempts a working budget needs turns that hang
        # into a fast, explicit failure.
        if attempts["n"] > 20:
            raise AssertionError("budget never exhausted")
        e = ClientError({"Error": {"Code": "ServiceUnavailableException", "Message": "down"}}, "Converse")
        raised.append(e)
        raise e

    with pytest.raises(retry.LLMOutageError) as exc_info:
        retry._call_with_retry(always_unavailable, retry_count=1)

    assert exc_info.value.__cause__ is raised[-1]
    assert exc_info.value.seconds_waited >= 50
    assert attempts["n"] == 7  # 1+2+4+8+16+32=63s elapsed by the 7th failure >= the 50s budget


def test_call_with_retry_outage_honors_retry_after_capped_at_60(monkeypatch: pytest.MonkeyPatch) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr(retry.time, "sleep", lambda s: sleeps.append(s))
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ClientError(
                {
                    "Error": {"Code": "ServiceUnavailableException", "Message": "down"},
                    "ResponseMetadata": {"HTTPHeaders": {"retry-after": "120"}},
                },
                "Converse",
            )
        return "recovered"

    result, _ = retry._call_with_retry(flaky, retry_count=3)

    assert result == "recovered"
    assert sleeps == [60.0]  # the provider's 120s is capped at the 60s outage ceiling


def test_call_with_retry_outage_retry_after_zero_floored_at_one_second(monkeypatch: pytest.MonkeyPatch) -> None:
    # A Retry-After: 0 would otherwise honour a zero-second sleep -- a tight
    # loop hammering the provider for the whole outage budget instead of
    # backing off (verifier round 2, NOTE 3). Floored at 1s.
    sleeps: list[float] = []
    monkeypatch.setattr(retry.time, "sleep", lambda s: sleeps.append(s))
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ClientError(
                {
                    "Error": {"Code": "ServiceUnavailableException", "Message": "down"},
                    "ResponseMetadata": {"HTTPHeaders": {"retry-after": "0"}},
                },
                "Converse",
            )
        return "recovered"

    result, _ = retry._call_with_retry(flaky, retry_count=3)

    assert result == "recovered"
    assert sleeps == [1.0]


def test_call_with_retry_outage_retry_after_negative_ignored_uses_normal_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A negative Retry-After is nonsensical (and time.sleep raises ValueError
    # on a negative duration); it must be ignored in favour of the ordinary
    # equal-jitter backoff, not floored the way 0 is. Two failures so the
    # backoff's outage_retries-driven growth (1s then 2s) distinguishes this
    # from a mutant that floors negative values at 1s on every attempt
    # (verifier round 2, NOTE 3 / m15).
    sleeps: list[float] = []
    monkeypatch.setattr(retry.time, "sleep", lambda s: sleeps.append(s))
    monkeypatch.setattr(retry.random, "uniform", lambda lo, hi: hi)
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise ClientError(
                {
                    "Error": {"Code": "ServiceUnavailableException", "Message": "down"},
                    "ResponseMetadata": {"HTTPHeaders": {"retry-after": "-5"}},
                },
                "Converse",
            )
        return "recovered"

    result, _ = retry._call_with_retry(flaky, retry_count=3)

    assert result == "recovered"
    assert sleeps == [1.0, 2.0]  # normal backoff (base 1, then 2), jitter pinned to hi


def test_call_with_retry_outage_cancel_check_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    # time.monotonic is faked off the recorded sleeps (unlike most tests in
    # this file) so this exercises the OUTAGE branch's cancel_check on a
    # short, deterministic virtual clock instead of the real one: with the
    # real clock and the default 1800s budget, deleting the outage branch's
    # cancel_check() call (mutant) doesn't fail this test -- it just keeps
    # retrying for ~1800 real seconds before LLMOutageError finally raises,
    # a CI hang rather than a failure (verifier round 1, m09).
    clock = {"t": 0.0}
    sleeps: list[float] = []

    def fake_sleep(s: float) -> None:
        sleeps.append(s)
        clock["t"] += s

    monkeypatch.setattr(retry.time, "sleep", fake_sleep)
    monkeypatch.setattr(retry.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(retry.random, "uniform", lambda lo, hi: hi)
    monkeypatch.setenv("CVICHE_LLM_OUTAGE_BUDGET_SECONDS", "10")
    attempts = {"n": 0}

    def always_unavailable() -> str:
        attempts["n"] += 1
        raise ClientError({"Error": {"Code": "ServiceUnavailableException", "Message": "down"}}, "Converse")

    class Cancelled(Exception):
        pass

    checks = {"n": 0}

    def cancel_check() -> None:
        checks["n"] += 1
        raise Cancelled()

    with pytest.raises(Cancelled):
        retry._call_with_retry(always_unavailable, retry_count=3, cancel_check=cancel_check)

    assert attempts["n"] == 1  # cancel fired before a second attempt started
    assert checks["n"] == 1
    assert sleeps == [1.0]  # the outage-branch wait (base=1, jitter pinned to hi)


def test_call_with_retry_openai_rate_limit_error_is_outage_class(monkeypatch: pytest.MonkeyPatch) -> None:
    # OpenAI's RateLimitError is outage-class too (not just Bedrock's codes):
    # it must survive past retry_count the same way.
    monkeypatch.setattr(retry.time, "sleep", lambda s: None)
    attempts = {"n": 0}

    def eventually_recovers() -> str:
        attempts["n"] += 1
        if attempts["n"] <= 3:
            raise openai.RateLimitError(
                "rate limited",
                response=httpx.Response(429, request=httpx.Request("POST", "https://example.invalid")),
                body=None,
            )
        return "recovered"

    result, _ = retry._call_with_retry(eventually_recovers, retry_count=1)

    assert result == "recovered"
    assert attempts["n"] == 4  # more than retry_count(1)+1 == 2 would allow


def test_call_with_retry_openai_internal_server_error_is_outage_class(monkeypatch: pytest.MonkeyPatch) -> None:
    # OpenAI's InternalServerError is outage-class too, alongside
    # RateLimitError (Do-2 names both) -- only RateLimitError had a test
    # before this (verifier round 1, m12: dropping InternalServerError from
    # _is_outage_error survived with the suite green).
    monkeypatch.setattr(retry.time, "sleep", lambda s: None)
    attempts = {"n": 0}

    def eventually_recovers() -> str:
        attempts["n"] += 1
        if attempts["n"] <= 3:
            raise openai.InternalServerError(
                "internal error",
                response=httpx.Response(500, request=httpx.Request("POST", "https://example.invalid")),
                body=None,
            )
        return "recovered"

    result, _ = retry._call_with_retry(eventually_recovers, retry_count=1)

    assert result == "recovered"
    assert attempts["n"] == 4  # more than retry_count(1)+1 == 2 would allow


def test_call_with_retry_openai_rate_limit_error_honors_retry_after_capped_at_60(monkeypatch: pytest.MonkeyPatch) -> None:
    # The botocore half of Retry-After honoring (#637) is covered by
    # test_call_with_retry_outage_honors_retry_after_capped_at_60 above; the
    # OpenAI httpx-response half was not (verifier round 1, m11: forcing
    # _retry_after_seconds's OpenAI branch to always return None survived
    # with the suite green).
    sleeps: list[float] = []
    monkeypatch.setattr(retry.time, "sleep", lambda s: sleeps.append(s))
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise openai.RateLimitError(
                "rate limited",
                response=httpx.Response(
                    429,
                    headers={"Retry-After": "120"},
                    request=httpx.Request("POST", "https://example.invalid"),
                ),
                body=None,
            )
        return "recovered"

    result, _ = retry._call_with_retry(flaky, retry_count=3)

    assert result == "recovered"
    assert sleeps == [60.0]  # the provider's 120s is capped at the 60s outage ceiling
