"""Behaviour coverage for the Bedrock provider adapter and the shared retry
infrastructure (#704), split out of llm_client.py by #496:

    unified_pipeline/llm/bedrock.py  -- Converse API translation, schema
        enforcement (#46), markdown-fence stripping, JSON-repair retry.
    unified_pipeline/llm/retry.py    -- exponential backoff / retry
        classification, and the llm_config.yaml tuning knobs.

No network, ever. Bedrock's real SDK client is never constructed:
``_get_bedrock_client`` is monkeypatched to return a small fake object that
records the kwargs it was called with and returns a canned response shaped
like the real Converse API return value (read from the source, not
guessed). ``call_llm`` (the llm_client.py facade) is never imported or
invoked here -- these tests go straight at the provider-adapter functions it
dispatches to. ``time.sleep`` is monkeypatched to a no-op recorder wherever a
retry path might sleep; ``time.monotonic`` is left untouched (asyncio.run()
depends on it elsewhere in the pipeline -- swapping it globally breaks
unrelated duration math).

Run with:

    python3 -m pytest src/unified_pipeline/tests/test_llm_adapters_behaviour.py -p no:cacheprovider
"""

import json
import logging
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

import unified_pipeline.config as pipeline_config  # noqa: E402
import unified_pipeline.llm.bedrock as bedrock  # noqa: E402
import unified_pipeline.llm.retry as retry  # noqa: E402

# For the #267 parity test only -- proves unified_pipeline.config's own
# resolver reads the same value app.config_loader.get_config would, without
# any PRODUCTION code importing the web app (that is the point of #267).
# Test-only coupling: scripts/check_standards.py's §1.4 gate (core does not
# import the web backend) excludes tests/ via iter_py_files.
_BACKEND_ROOT = Path(__file__).resolve().parents[3] / "web_interface" / "backend"
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))
import app.config_loader as backend_config_loader  # noqa: E402

BEDROCK_MODEL = "anthropic.claude-haiku-4-5"


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


def test_extract_tool_use_input_none_when_tool_block_has_no_input() -> None:
    response = {"output": {"message": {"content": [{"toolUse": {"name": "extract"}}]}}}
    assert bedrock._extract_tool_use_input(response) is None


def test_extract_text_content_finds_text_block() -> None:
    response = {"output": {"message": {"content": [{"toolUse": {}}, {"text": "hi"}]}}}
    assert bedrock._extract_text_content(response) == "hi"


def test_extract_text_content_none_for_empty_or_textless_content() -> None:
    assert bedrock._extract_text_content(
        {"output": {"message": {"content": []}}}
    ) is None
    assert bedrock._extract_text_content(
        {"output": {"message": {"content": [{"toolUse": {}}]}}}
    ) is None
    assert bedrock._extract_text_content({}) is None


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


# A self-correcting json_object response (#1218): the model answers, writes a
# line of prose saying it noticed a mistake, then answers again. Fictional
# values only.
_FIRST_ANSWER = {"entries": [{"entry_index": 0, "role": "Member", "end_date": "2010"}]}
_CORRECTED_ANSWER = {"entries": [{"entry_index": 0, "role": "Member; Chair", "end_date": "2012"}]}
_PROSE = "For entry 0, there is a second role that should also be captured:"
_JSON_FORMAT = {"type": "json_object"}


def _as_json(value: dict) -> str:
    return json.dumps(value, indent=2)


def _self_correcting_shapes() -> dict[str, str]:
    first, second = _as_json(_FIRST_ANSWER), _as_json(_CORRECTED_ANSWER)
    return {
        # What the model wrote when it fenced both blocks.
        "fenced": f"```json\n{first}\n```\n\n{_PROSE}\n\n```json\n{second}\n```",
        # The same, after _strip_markdown_fences removed only the outer pair
        # (what the prompt logs hold).
        "outer_stripped": f"{first}\n```\n\n{_PROSE}\n\n```json\n{second}",
        # No fence anywhere.
        "unfenced": f"{first}\n\n{_PROSE}\n\n{second}",
    }


@pytest.mark.parametrize("shape", ["fenced", "outer_stripped", "unfenced"])
def test_select_final_json_object_takes_the_corrected_answer(shape: str) -> None:
    content = _self_correcting_shapes()[shape]
    with pytest.raises(json.JSONDecodeError):
        json.loads(bedrock._strip_markdown_fences(content))  # the defect

    selected = bedrock._select_final_json_object(content, _JSON_FORMAT, "stage_4")

    assert json.loads(selected) == _CORRECTED_ANSWER


def test_select_final_json_object_takes_the_last_of_three(
    caplog: pytest.LogCaptureFixture,
) -> None:
    third = {"entries": []}
    content = f"{_as_json(_FIRST_ANSWER)}\n\nOops.\n\n{_as_json(_CORRECTED_ANSWER)}\n\nAgain.\n\n{_as_json(third)}"

    with caplog.at_level(logging.WARNING, logger="unified_pipeline.llm.bedrock"):
        selected = bedrock._select_final_json_object(content, _JSON_FORMAT, "stage_4")

    assert json.loads(selected) == third
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "stage=stage_4" in warnings[0]
    assert "3 objects" in warnings[0] and "discarding 2" in warnings[0]


@pytest.mark.parametrize(
    "content",
    [
        _as_json(_FIRST_ANSWER),                                  # one object
        f"```json\n{_as_json(_FIRST_ANSWER)}\n```",               # one fenced object
        "[1, 2, 3]",                                              # valid, not an object
        "plain prose, no JSON at all",
        "",
    ],
)
def test_select_final_json_object_leaves_everything_that_parses_or_is_not_json(
    content: str, caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger="unified_pipeline.llm.bedrock"):
        assert bedrock._select_final_json_object(content, _JSON_FORMAT, "stage_4") is content
    assert caplog.records == []


def test_select_final_json_object_ignores_a_response_that_is_not_json_or_not_text() -> None:
    # No JSON was requested, so two objects with prose between them are text.
    shaped = _self_correcting_shapes()["unfenced"]
    assert bedrock._select_final_json_object(shaped, None, "stage_4") is shaped
    assert bedrock._select_final_json_object(None, _JSON_FORMAT, "stage_4") is None


@pytest.mark.parametrize(
    "content",
    [
        # An object cut off by max_tokens, after a complete one.
        f'{_as_json(_FIRST_ANSWER)}\n\n{_PROSE}\n\n{{"entries": [{{"entry_index": 0',
        # A single truncated object: the complete inner object is not the answer.
        '{"entries": {"entry_index": 0}',
        # A truncated array of objects: the complete elements are not the answer.
        '[{"entry_index": 0}, {"entry_index": 1}',
        # Two objects and nothing between them: may be two records, not a correction.
        f'{_as_json(_FIRST_ANSWER)}\n{_as_json(_CORRECTED_ANSWER)}',
        f'{_as_json(_FIRST_ANSWER)}\n```\n```json\n{_as_json(_CORRECTED_ANSWER)}',
        # Text before the first object.
        f'Here you go:\n{_as_json(_FIRST_ANSWER)}\n\n{_PROSE}\n\n{_as_json(_CORRECTED_ANSWER)}',
        # One object followed by prose: nothing was corrected.
        f'{_as_json(_FIRST_ANSWER)}\n\nHope this helps.',
        # A brace in the prose that does not open an object.
        f'{_as_json(_FIRST_ANSWER)}\n\nUse the {{placeholder}} form.\n\n{_as_json(_CORRECTED_ANSWER)}',
    ],
)
def test_select_final_json_object_keeps_malformed_text_malformed(content: str) -> None:
    assert bedrock._select_final_json_object(content, _JSON_FORMAT, "stage_4") is content
    assert bedrock._validate_json_response(content, _JSON_FORMAT) is False


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


@pytest.mark.parametrize("bad_max_tokens", [-5, 0, True, False, 1.5, "16000"])
def test_call_bedrock_rejects_invalid_max_tokens(
    monkeypatch: pytest.MonkeyPatch, bad_max_tokens: object,
) -> None:
    fake = _FakeBedrockClient([_converse_response("hi")])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    with pytest.raises(ValueError, match="max_tokens"):
        bedrock._call_bedrock(
            BEDROCK_MODEL, [{"role": "user", "content": "hi"}], 0.1,
            response_format=None, max_tokens=bad_max_tokens, enable_prompt_caching=False,
        )
    assert fake.calls == []  # rejected before the client is ever called


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


@pytest.mark.parametrize(
    ("stop_reason", "expected_finish_reason"),
    [
        ("guardrail_intervened", "content_filter"),  # was "guard_intervened" -- never matched (#628)
        ("content_filtered", "content_filter"),
        ("model_context_window_exceeded", "length"),
        ("malformed_model_output", "error"),  # decided on #628
        ("malformed_tool_use", "error"),
    ],
)
def test_handle_bedrock_stop_reason_map_covers_documented_values(
    monkeypatch: pytest.MonkeyPatch, stop_reason: str, expected_finish_reason: str,
) -> None:
    canned = _converse_response("hello", stop_reason=stop_reason)
    fake = _FakeBedrockClient([canned])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    result = bedrock._handle_bedrock(
        [{"role": "user", "content": "hi"}], response_format=None, cfg=_bedrock_cfg()
    )

    assert result["finish_reason"] == expected_finish_reason


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
    with pytest.raises(bedrock.BedrockToolCallDidNotFireError, match="did not fire"):
        bedrock._handle_bedrock(
            [{"role": "user", "content": "hi"}], response_format=response_format, cfg=_bedrock_cfg()
        )


def test_handle_bedrock_empty_content_retries_and_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    # First call returns an empty content list (#884, e.g. a guardrail
    # intervention); the retry gets real text back.
    empty = {
        "output": {"message": {"content": []}},
        "stopReason": "guardrail_intervened",
        "usage": {"inputTokens": 30, "outputTokens": 0},
    }
    recovered = _converse_response("hello", stop_reason="end_turn",
                                   input_tokens=40, output_tokens=8)
    fake = _FakeBedrockClient([empty, recovered])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    result = bedrock._handle_bedrock(
        [{"role": "user", "content": "hi"}], response_format=None, cfg=_bedrock_cfg()
    )

    assert result["content"] == "hello"
    assert result["finish_reason"] == "stop"  # from the retry's stopReason
    assert result["prompt_tokens"] == 30 + 40
    assert result["completion_tokens"] == 0 + 8
    assert len(fake.calls) == 2


def test_bedrock_failure_types_stay_runtime_errors() -> None:
    # Existing `except RuntimeError` handlers must keep catching both.
    assert issubclass(bedrock.BedrockToolCallDidNotFireError, RuntimeError)
    assert issubclass(bedrock.BedrockEmptyResponseError, RuntimeError)


def test_handle_bedrock_empty_content_on_both_calls_raises_empty_response_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    empty_first = {
        "output": {"message": {"content": []}},
        "stopReason": "guardrail_intervened",
        "usage": {"inputTokens": 30, "outputTokens": 0},
    }
    empty_retry = {
        "output": {"message": {"content": []}},
        "stopReason": "max_tokens",
        "usage": {"inputTokens": 40, "outputTokens": 0},
    }
    fake = _FakeBedrockClient([empty_first, empty_retry])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    with pytest.raises(bedrock.BedrockEmptyResponseError, match="guardrail_intervened.*max_tokens"):
        bedrock._handle_bedrock(
            [{"role": "user", "content": "hi"}], response_format=None, cfg=_bedrock_cfg()
        )
    assert len(fake.calls) == 2  # both reads guarded, no IndexError before the raise


def test_handle_bedrock_empty_content_logs_stop_reason_and_usage(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    # #884 requires logging stopReason + usage when content comes back
    # empty -- once the retry succeeds, this log is the only record of why
    # it fired.
    empty = {
        "output": {"message": {"content": []}},
        "stopReason": "guardrail_intervened",
        "usage": {"inputTokens": 30, "outputTokens": 0},
    }
    recovered = _converse_response("hello", stop_reason="end_turn")
    fake = _FakeBedrockClient([empty, recovered])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    with caplog.at_level(logging.WARNING, logger="unified_pipeline.llm.bedrock"):
        bedrock._handle_bedrock(
            [{"role": "user", "content": "hi"}], response_format=None, cfg=_bedrock_cfg()
        )

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "guardrail_intervened" in msg and "'inputTokens': 30" in msg
        for msg in warnings
    )


def test_handle_bedrock_invalid_then_empty_retry_keeps_initial_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # First call returns invalid JSON (triggers the repair retry); the
    # repair retry itself comes back with an empty content list. #884's
    # fallback (`content = retry_content if retry_content is not None else
    # content`) must keep the first call's (invalid) content instead of
    # returning None -- every json_object caller's json.loads(content)
    # needs a string, not None.
    first = _converse_response("not json", stop_reason="end_turn")
    empty_retry = {
        "output": {"message": {"content": []}},
        "stopReason": "max_tokens",
        "usage": {"inputTokens": 40, "outputTokens": 0},
    }
    fake = _FakeBedrockClient([first, empty_retry])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    result = bedrock._handle_bedrock(
        [{"role": "user", "content": "hi"}],
        response_format={"type": "json_object"},
        cfg=_bedrock_cfg(),
    )

    assert result["content"] == "not json"  # kept, not None
    assert result["finish_reason"] == "length"  # retry's stopReason (max_tokens)
    assert len(fake.calls) == 2


def test_handle_bedrock_repairs_invalid_json_on_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    first = _converse_response("not json", stop_reason="end_turn",
                               input_tokens=30, output_tokens=5)
    second = _converse_response('{"a": 1}', stop_reason="end_turn",
                                input_tokens=40, output_tokens=8)
    fake = _FakeBedrockClient([first, second])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    turn = {"role": "user", "content": "hi"}
    messages = [turn]
    result = bedrock._handle_bedrock(
        messages,
        response_format={"type": "json_object"},
        cfg=_bedrock_cfg(),
    )

    # The repair works on copies: the caller's list and turn are untouched.
    assert messages == [{"role": "user", "content": "hi"}] and messages[0] is turn
    # Only the repair route produces valid JSON here (the first call's raw
    # content was "not json").
    assert json.loads(result["content"]) == {"a": 1}
    assert result["prompt_tokens"] == 30 + 40  # tokens accumulated across both calls
    assert result["completion_tokens"] == 5 + 8
    assert len(fake.calls) == 2
    # #630: the repair call must NOT append a second user turn -- Bedrock
    # Converse rejects two consecutive same-role turns. The hint is folded
    # into the existing (only) trailing user turn instead, so role
    # alternation is preserved end to end.
    repair_messages = fake.calls[1]["messages"]
    assert [m["role"] for m in repair_messages] == ["user"]
    assert repair_messages[-1]["content"] == [{
        "text": "hi\n\nYour previous response was not valid JSON. Please respond with "
                "ONLY valid JSON, no markdown fencing or explanation.",
    }]


def test_handle_bedrock_repair_appends_new_turn_when_last_turn_is_not_user(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The merge-into-last-turn shortcut only applies to a trailing user
    # turn. A conversation that (unusually) ends on an assistant
    # turn falls back to appending a fresh user turn, same as before #630.
    first = _converse_response("not json", stop_reason="end_turn")
    second = _converse_response('{"a": 1}', stop_reason="end_turn")
    fake = _FakeBedrockClient([first, second])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    bedrock._handle_bedrock(
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "ok"}],
        response_format={"type": "json_object"},
        cfg=_bedrock_cfg(),
    )

    repair_messages = fake.calls[1]["messages"]
    assert [m["role"] for m in repair_messages] == ["user", "assistant", "user"]
    assert repair_messages[-1]["content"] == [{
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


def test_handle_bedrock_self_correcting_response_needs_no_repair_call(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    # #1218: answer, prose, corrected answer. The last object is returned and
    # the repair call (which only repeated the shape) is never spent.
    canned = _converse_response(
        _self_correcting_shapes()["fenced"], input_tokens=30, output_tokens=7)
    fake = _FakeBedrockClient([canned])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    with caplog.at_level(logging.WARNING, logger="unified_pipeline.llm.bedrock"):
        result = bedrock._handle_bedrock(
            [{"role": "user", "content": "hi"}],
            response_format=_JSON_FORMAT,
            cfg=_bedrock_cfg(stage="stage_4"),
        )

    assert json.loads(result["content"]) == _CORRECTED_ANSWER
    assert len(fake.calls) == 1
    assert result["prompt_tokens"] == 30 and result["completion_tokens"] == 7
    messages = [r.getMessage() for r in caplog.records]
    assert any("stage=stage_4" in m and "discarding 1" in m for m in messages)
    assert not any("not valid JSON" in m for m in messages)


def test_handle_bedrock_repair_that_repeats_the_shape_is_still_usable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The first response is plain garbage, so the repair runs; the repair then
    # self-corrects the way both stored failures did.
    first = _converse_response("not json at all")
    repaired = _converse_response(_self_correcting_shapes()["unfenced"])
    fake = _FakeBedrockClient([first, repaired])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    result = bedrock._handle_bedrock(
        [{"role": "user", "content": "hi"}],
        response_format=_JSON_FORMAT,
        cfg=_bedrock_cfg(stage="stage_4"),
    )

    assert json.loads(result["content"]) == _CORRECTED_ANSWER
    assert len(fake.calls) == 2


def test_handle_bedrock_truncated_multi_object_response_still_goes_to_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    truncated = f'{_as_json(_FIRST_ANSWER)}\n\n{_PROSE}\n\n{{"entries": [{{"entry_index": 0'
    repaired = _converse_response(_as_json(_CORRECTED_ANSWER))
    fake = _FakeBedrockClient([_converse_response(truncated), repaired])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    result = bedrock._handle_bedrock(
        [{"role": "user", "content": "hi"}],
        response_format=_JSON_FORMAT,
        cfg=_bedrock_cfg(stage="stage_4"),
    )

    assert json.loads(result["content"]) == _CORRECTED_ANSWER  # from the repair call
    assert len(fake.calls) == 2


def test_handle_bedrock_valid_single_object_content_is_returned_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = _as_json(_FIRST_ANSWER)
    fake = _FakeBedrockClient([_converse_response(text)])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    result = bedrock._handle_bedrock(
        [{"role": "user", "content": "hi"}],
        response_format=_JSON_FORMAT,
        cfg=_bedrock_cfg(stage="stage_4"),
    )

    assert result["content"] == text
    assert len(fake.calls) == 1


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


def test_get_llm_timeout_seconds_reads_yaml_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#267 verify r2 BLOCKING: retry.py's readers must reach the yaml
    (ConfigMap) layer through get_llm_env_config, not a bare
    os.environ.get -- a bare env read would silently drop the
    CVICHE_LLM_TIMEOUT_SECONDS ConfigMap value in prod while every
    env-only test above stayed green."""
    monkeypatch.delenv("CVICHE_LLM_TIMEOUT_SECONDS", raising=False)
    yaml_path = tmp_path / "auth_config.yaml"
    _write_llm_yaml(yaml_path, CVICHE_LLM_TIMEOUT_SECONDS="240")
    monkeypatch.setattr(pipeline_config, "AUTH_CONFIG_PATH", yaml_path)
    assert retry._get_llm_timeout_seconds() == 240.0


def test_get_max_concurrent_llm_calls_reads_yaml_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#267 verify r2 BLOCKING: same as the timeout test above, for the int
    reader (_get_llm_config_int) via CVICHE_MAX_CONCURRENT_LLM_CALLS."""
    monkeypatch.delenv("CVICHE_MAX_CONCURRENT_LLM_CALLS", raising=False)
    yaml_path = tmp_path / "auth_config.yaml"
    _write_llm_yaml(yaml_path, CVICHE_MAX_CONCURRENT_LLM_CALLS="5")
    monkeypatch.setattr(pipeline_config, "AUTH_CONFIG_PATH", yaml_path)
    assert retry._get_max_concurrent_llm_calls() == 5


# ---------------------------------------------------------------------------
# config.py -- get_llm_env_config (env -> auth_config.yaml `llm:` block ->
# default, #267). retry.py's knob readers above call this; these tests hit
# it directly so the resolver's own branches (yaml hit, missing file,
# malformed yaml) are covered independently of retry.py's int/float parsing.
# ---------------------------------------------------------------------------

def _write_llm_yaml(path: Path, **llm_keys: str) -> None:
    """Write an auth_config.yaml-shaped file with only an `llm:` block."""
    lines = ["llm:"] + [f'  {k}: "{v}"' for k, v in llm_keys.items()]
    path.write_text("\n".join(lines) + "\n")


def test_get_llm_env_config_env_wins_over_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    yaml_path = tmp_path / "auth_config.yaml"
    _write_llm_yaml(yaml_path, CVICHE_TEST_PARITY_KEY="13")
    monkeypatch.setattr(pipeline_config, "AUTH_CONFIG_PATH", yaml_path)
    monkeypatch.setenv("CVICHE_TEST_PARITY_KEY", "99")
    assert pipeline_config.get_llm_env_config("CVICHE_TEST_PARITY_KEY", "42") == ("99", "env")


def test_get_llm_env_config_reads_yaml_when_env_unset(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CVICHE_TEST_PARITY_KEY", raising=False)
    yaml_path = tmp_path / "auth_config.yaml"
    _write_llm_yaml(yaml_path, CVICHE_TEST_PARITY_KEY="13")
    monkeypatch.setattr(pipeline_config, "AUTH_CONFIG_PATH", yaml_path)
    assert pipeline_config.get_llm_env_config("CVICHE_TEST_PARITY_KEY", "42") == ("13", "yaml")


def test_get_llm_env_config_default_when_neither_set(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CVICHE_TEST_PARITY_KEY", raising=False)
    yaml_path = tmp_path / "auth_config.yaml"
    _write_llm_yaml(yaml_path, CVICHE_OTHER_KEY="13")
    monkeypatch.setattr(pipeline_config, "AUTH_CONFIG_PATH", yaml_path)
    assert pipeline_config.get_llm_env_config("CVICHE_TEST_PARITY_KEY", "42") == ("42", "default")


def test_get_llm_env_config_default_when_yaml_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CVICHE_TEST_PARITY_KEY", raising=False)
    missing = tmp_path / "does-not-exist.yaml"
    monkeypatch.setattr(pipeline_config, "AUTH_CONFIG_PATH", missing)
    monkeypatch.setattr(pipeline_config, "AUTH_CONFIG_EXAMPLE_PATH", missing)
    assert pipeline_config.get_llm_env_config("CVICHE_TEST_PARITY_KEY", "42") == ("42", "default")


def test_get_llm_env_config_default_on_malformed_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CVICHE_TEST_PARITY_KEY", raising=False)
    bad_path = tmp_path / "auth_config.yaml"
    bad_path.write_text("llm: [unclosed\n")
    monkeypatch.setattr(pipeline_config, "AUTH_CONFIG_PATH", bad_path)
    assert pipeline_config.get_llm_env_config("CVICHE_TEST_PARITY_KEY", "42") == ("42", "default")


def test_get_llm_env_config_malformed_yaml_warning_carries_exc_info(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """#267 final round item 4: the backend's own warning for this branch
    (app.config_loader.get_config's `except Exception` handler) logs with
    exc_info=True. The pipeline's replacement warning must carry it too, not
    just the message -- compare origin/dev's app.config_loader.py:120."""
    monkeypatch.delenv("CVICHE_TEST_PARITY_KEY", raising=False)
    bad_path = tmp_path / "auth_config.yaml"
    bad_path.write_text("llm: [unclosed\n")
    monkeypatch.setattr(pipeline_config, "AUTH_CONFIG_PATH", bad_path)
    with caplog.at_level(logging.WARNING, logger=pipeline_config.__name__):
        pipeline_config.get_llm_env_config("CVICHE_TEST_PARITY_KEY", "42")
    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is not None


def test_get_llm_env_config_null_llm_block_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare `llm:` key with a null value (yaml.safe_load parses it as
    None, not {}) degrades to default like a missing `llm:` key, not raise
    on `.get(key)`."""
    monkeypatch.delenv("CVICHE_TEST_PARITY_KEY", raising=False)
    yaml_path = tmp_path / "auth_config.yaml"
    yaml_path.write_text("llm:\n")
    monkeypatch.setattr(pipeline_config, "AUTH_CONFIG_PATH", yaml_path)
    assert pipeline_config.get_llm_env_config("CVICHE_TEST_PARITY_KEY", "42") == ("42", "default")


def test_get_llm_env_config_empty_file_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty auth_config.yaml (yaml.safe_load returns None for the whole
    document, not just the llm: block) degrades to default, not raise on
    `cfg.get("llm")`."""
    monkeypatch.delenv("CVICHE_TEST_PARITY_KEY", raising=False)
    yaml_path = tmp_path / "auth_config.yaml"
    yaml_path.write_text("")
    monkeypatch.setattr(pipeline_config, "AUTH_CONFIG_PATH", yaml_path)
    assert pipeline_config.get_llm_env_config("CVICHE_TEST_PARITY_KEY", "42") == ("42", "default")


def test_get_llm_env_config_non_mapping_llm_block_falls_back_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A non-mapping `llm:` value (e.g. a plain scalar) degrades to default
    with a warning naming the bad type, as the backend's
    app.config_loader.get_config does (its broad except catches the
    AttributeError), rather than raising from `.get(key)` at import."""
    monkeypatch.delenv("CVICHE_TEST_PARITY_KEY", raising=False)
    yaml_path = tmp_path / "auth_config.yaml"
    yaml_path.write_text('llm: "oops"\n')
    monkeypatch.setattr(pipeline_config, "AUTH_CONFIG_PATH", yaml_path)
    with caplog.at_level(logging.WARNING, logger=pipeline_config.__name__):
        result = pipeline_config.get_llm_env_config("CVICHE_TEST_PARITY_KEY", "42")
    assert result == ("42", "default")
    assert "not a mapping" in caplog.text


@pytest.mark.parametrize("document", ["- a\n- b\n", "oops\n"])
def test_get_llm_env_config_non_mapping_document_falls_back_and_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    document: str,
) -> None:
    """A top-level list or scalar document degrades to default with a
    warning, the same as a non-mapping `llm:` block one level down."""
    monkeypatch.delenv("CVICHE_TEST_PARITY_KEY", raising=False)
    yaml_path = tmp_path / "auth_config.yaml"
    yaml_path.write_text(document)
    monkeypatch.setattr(pipeline_config, "AUTH_CONFIG_PATH", yaml_path)
    with caplog.at_level(logging.WARNING, logger=pipeline_config.__name__):
        result = pipeline_config.get_llm_env_config("CVICHE_TEST_PARITY_KEY", "42")
    assert result == ("42", "default")
    assert "not a mapping" in caplog.text


@pytest.mark.parametrize("raw", ['""', "0"])
def test_get_llm_env_config_falsy_yaml_value_falls_back_to_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    """An empty-string or zero yaml value falls through to the default, as
    the backend's get_config treats a falsy yaml value (`if value:`)."""
    monkeypatch.delenv("CVICHE_TEST_PARITY_KEY", raising=False)
    yaml_path = tmp_path / "auth_config.yaml"
    yaml_path.write_text(f"llm:\n  CVICHE_TEST_PARITY_KEY: {raw}\n")
    monkeypatch.setattr(pipeline_config, "AUTH_CONFIG_PATH", yaml_path)
    assert pipeline_config.get_llm_env_config("CVICHE_TEST_PARITY_KEY", "42") == ("42", "default")


def test_llm_config_readers_survive_non_mapping_llm_block_at_import(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The parity break as filed: with `llm: "oops"` in auth_config.yaml,
    `import unified_pipeline.llm.retry` used to raise AttributeError (the
    module-level `_llm_call_semaphore = threading.BoundedSemaphore(...)`
    line calls _get_max_concurrent_llm_calls at import time). retry.py is
    already imported by the time this test runs, so call its readers
    directly -- they must still resolve to the documented defaults
    (180.0, 8), not raise."""
    yaml_path = tmp_path / "auth_config.yaml"
    yaml_path.write_text('llm: "oops"\n')
    monkeypatch.setattr(pipeline_config, "AUTH_CONFIG_PATH", yaml_path)
    for key in (
        "CVICHE_LLM_TIMEOUT_SECONDS",
        "CVICHE_MAX_CONCURRENT_LLM_CALLS",
    ):
        monkeypatch.delenv(key, raising=False)
    assert retry._get_llm_timeout_seconds() == 180.0
    assert retry._get_max_concurrent_llm_calls() == 8


def test_get_llm_env_config_parity_with_backend_config_loader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#267: 'the value source the backend used must still win where it
    did.' Point both resolvers at the same auth_config.yaml and assert they
    agree for the yaml-layer case (env unset, the ConfigMap path
    buildspec.yaml relies on) and the env-override case."""
    yaml_path = tmp_path / "auth_config.yaml"
    _write_llm_yaml(yaml_path, CVICHE_TEST_PARITY_KEY="13")
    monkeypatch.setattr(pipeline_config, "AUTH_CONFIG_PATH", yaml_path)
    monkeypatch.setattr(backend_config_loader, "CONFIG_PATH", yaml_path)

    monkeypatch.delenv("CVICHE_TEST_PARITY_KEY", raising=False)
    assert pipeline_config.get_llm_env_config(
        "CVICHE_TEST_PARITY_KEY", "42"
    ) == backend_config_loader.get_config("llm", "CVICHE_TEST_PARITY_KEY", default="42")

    monkeypatch.setenv("CVICHE_TEST_PARITY_KEY", "99")
    assert pipeline_config.get_llm_env_config(
        "CVICHE_TEST_PARITY_KEY", "42"
    ) == backend_config_loader.get_config("llm", "CVICHE_TEST_PARITY_KEY", default="42")


def test_auth_config_path_matches_backend_config_path() -> None:
    """#267 verify r2 BLOCKING: the parity test above monkeypatches BOTH
    resolvers onto the same tmp file, so it cannot see the two real,
    unpatched paths drift apart (a moved file, a `.parent` depth change --
    #620). Assert the actual module-level constants -- what prod uses --
    still name the same files, real and .example."""
    assert pipeline_config.AUTH_CONFIG_PATH.resolve() == backend_config_loader.CONFIG_PATH.resolve()
    assert (
        pipeline_config.AUTH_CONFIG_EXAMPLE_PATH.resolve()
        == backend_config_loader.EXAMPLE_CONFIG_PATH.resolve()
    )


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


def test_call_with_retry_retries_non_outage_client_error_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    # InternalServerException is retryable but NOT in BEDROCK_OUTAGE_CODES, so
    # this exercises the ordinary retry_count-bounded path (distinct from the
    # ThrottlingException/ServiceUnavailableException outage-class tests below).
    sleeps: list[float] = []
    monkeypatch.setattr(retry.time, "sleep", lambda s: sleeps.append(s))
    attempts = {"n": 0}

    def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ClientError({"Error": {"Code": "InternalServerException", "Message": "x"}}, "Converse")
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


def test_call_with_retry_stops_ordinary_retries_at_the_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    # #636: each hung attempt costs a full per-attempt timeout, so retry_count
    # alone let 4 x 180s pass before failing. A 362s deadline allows the first
    # retry (180s + 1s backoff) and refuses the second: 361s alone fits, but
    # not with its 2s backoff added.
    clock = {"t": 0.0}
    sleeps: list[float] = []

    def fake_sleep(s: float) -> None:
        sleeps.append(s)
        clock["t"] += s

    monkeypatch.setattr(retry.time, "sleep", fake_sleep)
    monkeypatch.setattr(retry.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(retry.random, "uniform", lambda lo, hi: hi)
    monkeypatch.setenv("CVICHE_LLM_RETRY_DEADLINE_SECONDS", "362")
    # Far-off default, so only the explicit deadline above can stop the retries.
    monkeypatch.setenv("CVICHE_LLM_TIMEOUT_SECONDS", "1000")
    attempts = {"n": 0}

    def hangs_then_times_out() -> str:
        attempts["n"] += 1
        clock["t"] += 180.0
        raise ClientError({"Error": {"Code": "ModelTimeoutException", "Message": "x"}}, "Converse")

    with pytest.raises(ClientError):
        retry._call_with_retry(hangs_then_times_out, retry_count=3)

    assert attempts["n"] == 2
    assert sleeps == [1.0]


def test_retry_deadline_defaults_to_twice_the_attempt_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CVICHE_LLM_RETRY_DEADLINE_SECONDS", raising=False)
    monkeypatch.setenv("CVICHE_LLM_TIMEOUT_SECONDS", "100")

    assert retry._get_retry_deadline_seconds() == 200.0


def test_call_with_retry_outage_pause_does_not_use_up_the_retry_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    # A 60s outage pause (Retry-After) is longer than the 10s ordinary
    # deadline; the ordinary error after it must still get its retry.
    clock = {"t": 0.0}

    def fake_sleep(s: float) -> None:
        clock["t"] += s

    monkeypatch.setattr(retry.time, "sleep", fake_sleep)
    monkeypatch.setattr(retry.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(retry.random, "uniform", lambda lo, hi: hi)
    monkeypatch.setenv("CVICHE_LLM_RETRY_DEADLINE_SECONDS", "10")
    attempts = {"n": 0}

    def outage_then_blip_then_ok() -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ClientError({
                "Error": {"Code": "ServiceUnavailableException", "Message": "down"},
                "ResponseMetadata": {"HTTPHeaders": {"retry-after": "60"}},
            }, "Converse")
        if attempts["n"] == 2:
            raise ClientError({"Error": {"Code": "InternalServerException", "Message": "x"}}, "Converse")
        return "recovered"

    result, _ = retry._call_with_retry(outage_then_blip_then_ok, retry_count=1)

    assert result == "recovered"
    assert attempts["n"] == 3


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
        raise ClientError({"Error": {"Code": "InternalServerException", "Message": "x"}}, "Converse")

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


@pytest.mark.parametrize(
    "make_error",
    [
        lambda: _bedrock_outage_error("ServiceUnavailableException"),
        lambda: _bedrock_outage_error("ThrottlingException"),
    ],
    ids=[
        "bedrock_service_unavailable",
        "bedrock_throttling",
    ],
)
def test_call_with_retry_outage_error_keeps_retrying_past_retry_count(
    monkeypatch: pytest.MonkeyPatch, make_error: Callable[[], Exception]
) -> None:
    # Every outage-class error -- both Bedrock codes named in
    # BEDROCK_OUTAGE_CODES -- must NOT be bounded by retry_count=1 (which
    # would allow only 2 total attempts on the ordinary path). Verifier
    # round 2 (m13): dropping "ThrottlingException" from BEDROCK_OUTAGE_CODES
    # survived with the suite green because only ServiceUnavailableException
    # had a test here; this parametrization covers both outage codes so
    # neither can be silently dropped again.
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


def test_call_with_retry_outage_backoff_saturates_at_60s_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    # The outage path's equal-jitter backoff doubles from 1s and saturates
    # at _OUTAGE_BACKOFF_CAP_SECONDS (60), not the ordinary path's 30s cap
    # and not unbounded. random.uniform is pinned to its upper bound so
    # each wait equals `base` exactly. Verifier round 3: the cap assertion
    # was lost in a rewrite and both `no cap` and `30s cap` mutants
    # survived -- this pins it on its own.
    clock = {"t": 0.0}
    sleeps: list[float] = []

    def fake_sleep(s: float) -> None:
        sleeps.append(s)
        clock["t"] += s

    monkeypatch.setattr(retry.time, "sleep", fake_sleep)
    monkeypatch.setattr(retry.time, "monotonic", lambda: clock["t"])
    monkeypatch.setattr(retry.random, "uniform", lambda lo, hi: hi)
    monkeypatch.setenv("CVICHE_LLM_OUTAGE_BUDGET_SECONDS", "200")
    attempts = {"n": 0}

    def down_eight_times() -> str:
        attempts["n"] += 1
        if attempts["n"] <= 8:
            raise ClientError({"Error": {"Code": "ServiceUnavailableException", "Message": "down"}}, "Converse")
        return "recovered"

    result, _ = retry._call_with_retry(down_eight_times, retry_count=1)

    assert result == "recovered"
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0, 32.0, 60.0, 60.0]


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



# ---------------------------------------------------------------------------
# #632 -- _call_with_retry is the SINGLE retry owner: the Bedrock client makes
# exactly one botocore attempt, so retry_count+1 is the true request count.
# ---------------------------------------------------------------------------


def _fresh_bedrock_client(monkeypatch: pytest.MonkeyPatch):
    """Build the REAL client through _get_bedrock_client (real botocore Config,
    real retry handlers), with dummy credentials and no cached singleton."""
    monkeypatch.setattr(bedrock, "_bedrock_client", None)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    return bedrock._get_bedrock_client()


def test_bedrock_client_config_is_a_single_botocore_attempt(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _fresh_bedrock_client(monkeypatch)
    # botocore rewrites the retries dict into total_max_attempts at client
    # build; 1 means the initial request only, i.e. botocore retries nothing.
    assert client.meta.config.retries["total_max_attempts"] == 1
    assert client.meta.config.retries["mode"] == "standard"


class _FakeRaw:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def stream(self, *args, **kwargs):
        yield self._body


def _count_raw_requests(
    client, monkeypatch: pytest.MonkeyPatch, retry_count: int,
    status: int = 500, headers: dict | None = None, body: bytes = b'{"message": "boom"}',
) -> int:
    """Answer every HTTP send with the given response and return how many raw
    requests the client + _call_with_retry made in total."""
    from botocore.awsrequest import AWSResponse

    if headers is None:
        headers = {"x-amzn-errortype": "InternalServerException"}
    sent: list[str] = []

    def fake_send(request, **kwargs):
        sent.append(request.url)
        return AWSResponse(request.url, status, dict(headers), _FakeRaw(body))

    client.meta.events.register("before-send.bedrock-runtime.Converse", fake_send)
    monkeypatch.setattr(retry.time, "sleep", lambda s: None)
    with pytest.raises(ClientError):
        retry._call_with_retry(
            lambda: client.converse(modelId="m", messages=[]), retry_count=retry_count
        )
    return len(sent)


@pytest.mark.parametrize("retry_count", [0, 2, 3])
def test_raw_request_count_is_retry_count_plus_one(
    monkeypatch: pytest.MonkeyPatch, retry_count: int
) -> None:
    client = _fresh_bedrock_client(monkeypatch)
    assert _count_raw_requests(client, monkeypatch, retry_count) == retry_count + 1


@pytest.mark.parametrize(
    "label,status,headers,body",
    [
        # Modeled `retryable`-trait error botocore used to retry (4 sends).
        ("ModelNotReady429", 429, {"x-amzn-errortype": "ModelNotReadyException"}, b'{"message": "warming"}'),
        # Bare load-balancer 502: no modeled code, botocore used to retry it.
        ("bare502", 502, {}, b"<html>Bad Gateway</html>"),
    ],
)
def test_raw_request_count_is_retry_count_plus_one_for_formerly_botocore_retried_errors(
    monkeypatch: pytest.MonkeyPatch, label: str, status: int, headers: dict, body: bytes
) -> None:
    client = _fresh_bedrock_client(monkeypatch)
    assert _count_raw_requests(client, monkeypatch, 2, status, headers, body) == 3


def test_bedrock_retryable_codes_cover_the_service_model() -> None:
    """A botocore bump that marks another Bedrock error `retryable` must fail
    here: botocore no longer retries it (#632), so _call_with_retry has to."""
    from botocore.loaders import Loader

    model = Loader().load_service_model("bedrock-runtime", "service-2")
    modeled = {
        name
        for name, shape in model["shapes"].items()
        if shape.get("exception") and shape.get("retryable")
    }
    assert modeled, "service model no longer lists any retryable error; re-check this test"
    assert modeled <= retry.BEDROCK_RETRYABLE_CODES


@pytest.mark.parametrize(
    "exc_name", ["ConnectTimeoutError", "ReadTimeoutError", "EndpointConnectionError"]
)
def test_call_with_retry_retries_transport_errors_botocore_no_longer_retries(
    monkeypatch: pytest.MonkeyPatch, exc_name: str
) -> None:
    """With botocore held to one attempt, connect/read timeouts and dropped
    connections must be retried by _call_with_retry, else they regress to a
    first-attempt failure."""
    import botocore.exceptions as bexc

    monkeypatch.setattr(retry.time, "sleep", lambda s: None)
    calls: list[int] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) <= 2:
            raise getattr(bexc, exc_name)(endpoint_url="u")
        return "ok"

    result, _ = retry._call_with_retry(flaky, retry_count=3)
    assert result == "ok"
    assert len(calls) == 3


# ---------------------------------------------------------------------------
# bedrock.py -- content_filtered fallback to Sonnet 4.6 (#1174)
# ---------------------------------------------------------------------------

SONNET_5 = "us.anthropic.claude-sonnet-5"
_USER_MSG = [{"role": "user", "content": "synthetic request"}]


def _empty_response(stop_reason: str, *, input_tokens: int = 100) -> dict:
    return {
        "output": {"message": {"content": []}},
        "stopReason": stop_reason,
        "usage": {"inputTokens": input_tokens, "outputTokens": 1},
    }


def _filtered_empty_pair() -> list[dict]:
    """First call and its JSON-repair retry, both empty and filtered."""
    return [_empty_response("content_filtered"), _empty_response("content_filtered")]


def _model_ids(fake: _FakeBedrockClient) -> list[str]:
    return [call["modelId"] for call in fake.calls]


def test_content_filtered_empty_falls_back_once_to_sonnet_4_6(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ok = _converse_response("hello", input_tokens=10, output_tokens=5)
    fake = _FakeBedrockClient(_filtered_empty_pair() + [ok])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    result = bedrock._handle_bedrock(_USER_MSG, None, _bedrock_cfg(model=SONNET_5))

    assert _model_ids(fake) == [SONNET_5, SONNET_5, bedrock.CONTENT_FILTER_FALLBACK_MODEL]
    assert bedrock.CONTENT_FILTER_FALLBACK_MODEL == "us.anthropic.claude-sonnet-4-6"
    assert result["content"] == "hello"
    assert result["model"] == bedrock.CONTENT_FILTER_FALLBACK_MODEL
    # The fallback is priced at its own rate, plus the two billed filtered calls.
    fallback_cost = pipeline_config.calculate_cost(
        10, 5, model=bedrock.CONTENT_FILTER_FALLBACK_MODEL, provider="bedrock")
    filtered_cost = pipeline_config.calculate_cost(
        200, 2, model=SONNET_5, provider="bedrock")
    assert fallback_cost > 0
    assert result["cost"] == pytest.approx(fallback_cost + filtered_cost)


def test_content_filtered_fallback_request_drops_sonnet_5_only_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeBedrockClient(_filtered_empty_pair() + [_converse_response("hello")])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    bedrock._handle_bedrock(_USER_MSG, None, _bedrock_cfg(model=SONNET_5))

    assert "additionalModelRequestFields" in fake.calls[0]
    assert "additionalModelRequestFields" not in fake.calls[2]
    assert fake.calls[2]["inferenceConfig"]["temperature"] == 0.2


def test_content_filtered_partial_text_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    # Mid-stream truncation: non-empty text, never BedrockEmptyResponseError.
    truncated = _converse_response('{"a": ', stop_reason="content_filtered")
    ok = _converse_response('{"a": 1}')
    fake = _FakeBedrockClient([truncated, truncated, ok])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    result = bedrock._handle_bedrock(
        _USER_MSG, {"type": "json_object"}, _bedrock_cfg(model=SONNET_5))

    assert _model_ids(fake) == [SONNET_5, SONNET_5, bedrock.CONTENT_FILTER_FALLBACK_MODEL]
    assert json.loads(result["content"]) == {"a": 1}
    assert result["finish_reason"] == "stop"


def test_content_filtered_schema_tool_path_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    response_format = {
        "type": "json_schema",
        "json_schema": {"name": "extract_v1", "schema": {"type": "object"}},
    }
    filtered = _empty_response("content_filtered")
    tool_ok = {
        "output": {"message": {"content": [{"toolUse": {"input": {"foo": "bar"}}}]}},
        "stopReason": "tool_use",
        "usage": {"inputTokens": 10, "outputTokens": 5},
    }
    fake = _FakeBedrockClient([filtered, tool_ok])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    result = bedrock._handle_bedrock(_USER_MSG, response_format, _bedrock_cfg(model=SONNET_5))

    assert _model_ids(fake) == [SONNET_5, bedrock.CONTENT_FILTER_FALLBACK_MODEL]
    assert json.loads(result["content"]) == {"foo": "bar"}


def test_second_content_filter_on_fallback_raises_and_never_loops(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeBedrockClient(_filtered_empty_pair() + _filtered_empty_pair())
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    with pytest.raises(bedrock.BedrockEmptyResponseError, match="content_filtered"):
        bedrock._handle_bedrock(_USER_MSG, None, _bedrock_cfg(model=SONNET_5))

    # primary + its repair, fallback + its repair: no third model, no more calls.
    assert _model_ids(fake) == [SONNET_5, SONNET_5] + [bedrock.CONTENT_FILTER_FALLBACK_MODEL] * 2


def test_second_content_filter_with_partial_text_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    truncated = _converse_response('{"a": ', stop_reason="content_filtered")
    fake = _FakeBedrockClient([truncated] * 4)
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    with pytest.raises(bedrock.BedrockContentFilteredError):
        bedrock._handle_bedrock(
            _USER_MSG, {"type": "json_object"}, _bedrock_cfg(model=SONNET_5))

    assert len(fake.calls) == 4


@pytest.mark.parametrize(
    "stop_reason", ["guardrail_intervened", "max_tokens", "malformed_model_output"])
def test_other_stop_reasons_never_fall_back(
    monkeypatch: pytest.MonkeyPatch, stop_reason: str,
) -> None:
    fake = _FakeBedrockClient([_empty_response(stop_reason), _empty_response(stop_reason)])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    with pytest.raises(bedrock.BedrockEmptyResponseError):
        bedrock._handle_bedrock(_USER_MSG, None, _bedrock_cfg(model=SONNET_5))

    assert _model_ids(fake) == [SONNET_5, SONNET_5]


def test_non_filtered_result_on_sonnet_5_makes_one_call(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeBedrockClient([_converse_response("hello", stop_reason="guardrail_intervened")])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    result = bedrock._handle_bedrock(_USER_MSG, None, _bedrock_cfg(model=SONNET_5))

    assert _model_ids(fake) == [SONNET_5]
    assert result["model"] == SONNET_5


@pytest.mark.parametrize(
    "model", [BEDROCK_MODEL, "us.anthropic.claude-sonnet-4-6", "anthropic.claude-sonnet-4-5"])
def test_content_filtered_on_non_sonnet_5_model_does_not_fall_back(
    monkeypatch: pytest.MonkeyPatch, model: str,
) -> None:
    fake = _FakeBedrockClient(_filtered_empty_pair())
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    with pytest.raises(bedrock.BedrockEmptyResponseError):
        bedrock._handle_bedrock(_USER_MSG, None, _bedrock_cfg(model=model))

    assert _model_ids(fake) == [model, model]


def test_content_filter_fallback_warning_names_the_stage(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    fake = _FakeBedrockClient(_filtered_empty_pair() + [_converse_response("hello")])
    monkeypatch.setattr(bedrock, "_get_bedrock_client", lambda: fake)

    with caplog.at_level(logging.WARNING, logger="unified_pipeline.llm.bedrock"):
        bedrock._handle_bedrock(
            _USER_MSG, None, _bedrock_cfg(model=SONNET_5, stage="stage_4_synthetic"))

    assert any(
        "stage_4_synthetic" in r.getMessage() and "fallback" in r.getMessage()
        for r in caplog.records if r.levelno == logging.WARNING
    )


def test_resolve_call_config_carries_the_stage_for_provider_log_lines() -> None:
    from unified_pipeline import llm_client
    assert llm_client._resolve_call_config("stage_4", {})["stage"] == "stage_4"
