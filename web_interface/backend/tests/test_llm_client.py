"""
Tests for centralized LLM client: call_llm() function with config resolution,
retry logic, normalized response format, and cost calculation.

Covers requirements: LLM-01, LLM-02, LLM-04
"""
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

import pytest

# Add src/ to path so unified_pipeline.llm_client is importable
# From tests/ -> backend/ -> web_interface/ -> project_root/ -> src/
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from unified_pipeline.config import reload_config


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_response(content="test response", prompt_tokens=100,
                        completion_tokens=50, total_tokens=150,
                        finish_reason="stop"):
    """Create a mock OpenAI ChatCompletion response object."""
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = content
    mock_response.choices[0].finish_reason = finish_reason
    mock_response.usage.prompt_tokens = prompt_tokens
    mock_response.usage.completion_tokens = completion_tokens
    mock_response.usage.total_tokens = total_tokens
    return mock_response


def _default_config():
    """Return a default stage config dict for mocking get_stage_config."""
    return {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0,
        "max_tokens": None,
        "retry_count": 3,
    }


def _stage_4_config():
    """Return a stage_4 config dict (model overridden to gpt-4o)."""
    return {
        "provider": "openai",
        "model": "gpt-4o",
        "temperature": 0,
        "max_tokens": None,
        "retry_count": 3,
    }


def _bedrock_config():
    """Return a config dict with bedrock as provider."""
    return {
        "provider": "bedrock",
        "model": "anthropic.claude-3-haiku-20240307-v1:0",
        "temperature": 0,
        "max_tokens": None,
        "retry_count": 3,
    }


def _make_bedrock_response(content="test response", input_tokens=100,
                           output_tokens=50, total_tokens=150,
                           stop_reason="end_turn"):
    """Create a mock Bedrock Converse response dict."""
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"text": content}],
            },
        },
        "usage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "totalTokens": total_tokens,
        },
        "stopReason": stop_reason,
    }


@pytest.fixture(autouse=True)
def reset_state():
    """Reset config cache, OpenAI client, and Bedrock client before each test."""
    reload_config()
    import unified_pipeline.llm_client as mod
    mod._openai_client = None
    mod._bedrock_client = None
    yield
    reload_config()
    mod._openai_client = None
    mod._bedrock_client = None


# ---------------------------------------------------------------------------
# Basic call_llm functionality (LLM-01)
# ---------------------------------------------------------------------------

def test_call_llm_openai():
    """call_llm returns a dict with all required keys for an OpenAI call."""
    from unified_pipeline.llm_client import call_llm

    mock_response = _make_mock_response()

    with patch("unified_pipeline.llm_client.get_stage_config", return_value=_default_config()), \
         patch("unified_pipeline.llm_client.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        result = call_llm("stage_2", [{"role": "user", "content": "test"}])

    assert isinstance(result, dict)
    assert result["content"] == "test response"
    assert result["provider"] == "openai"
    assert result["model"] == "gpt-4o-mini"
    assert result["prompt_tokens"] == 100
    assert result["completion_tokens"] == 50
    assert result["total_tokens"] == 150
    assert result["finish_reason"] == "stop"
    assert "cost" in result
    assert "latency_ms" in result


def test_call_llm_params():
    """call_llm passes model from stage config and response_format to OpenAI SDK."""
    from unified_pipeline.llm_client import call_llm

    mock_response = _make_mock_response()

    with patch("unified_pipeline.llm_client.get_stage_config", return_value=_stage_4_config()), \
         patch("unified_pipeline.llm_client.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        messages = [{"role": "user", "content": "classify"}]
        call_llm("stage_4", messages, response_format={"type": "json_object"})

        call_kwargs = mock_client.chat.completions.create.call_args
        passed_kwargs = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
        assert passed_kwargs["model"] == "gpt-4o"
        assert passed_kwargs["response_format"] == {"type": "json_object"}


def test_call_llm_temperature():
    """call_llm passes temperature from config to OpenAI SDK."""
    from unified_pipeline.llm_client import call_llm

    mock_response = _make_mock_response()

    with patch("unified_pipeline.llm_client.get_stage_config", return_value=_default_config()), \
         patch("unified_pipeline.llm_client.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        call_llm("stage_2", [{"role": "user", "content": "test"}])

        call_kwargs = mock_client.chat.completions.create.call_args
        passed_kwargs = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
        assert passed_kwargs["temperature"] == 0


def test_call_llm_kwargs_override():
    """kwargs override config values for model and temperature."""
    from unified_pipeline.llm_client import call_llm

    mock_response = _make_mock_response()

    with patch("unified_pipeline.llm_client.get_stage_config", return_value=_default_config()), \
         patch("unified_pipeline.llm_client.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        result = call_llm("stage_2", [{"role": "user", "content": "test"}],
                          model="gpt-4o", temperature=0.5)

        call_kwargs = mock_client.chat.completions.create.call_args
        passed_kwargs = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
        assert passed_kwargs["model"] == "gpt-4o"
        assert passed_kwargs["temperature"] == 0.5
        # Result should reflect the overridden model
        assert result["model"] == "gpt-4o"


# ---------------------------------------------------------------------------
# Normalized response format (LLM-04)
# ---------------------------------------------------------------------------

def test_normalized_response():
    """Returned dict has exactly the required keys with correct values."""
    from unified_pipeline.llm_client import call_llm

    mock_response = _make_mock_response(
        content="extracted text",
        prompt_tokens=200,
        completion_tokens=80,
        total_tokens=280,
        finish_reason="stop",
    )

    with patch("unified_pipeline.llm_client.get_stage_config", return_value=_default_config()), \
         patch("unified_pipeline.llm_client.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        result = call_llm("stage_2", [{"role": "user", "content": "test"}])

    expected_keys = {"content", "prompt_tokens", "completion_tokens", "total_tokens",
                     "cache_read_tokens", "cache_write_tokens",
                     "cost", "model", "provider", "finish_reason", "latency_ms"}
    assert set(result.keys()) == expected_keys
    assert result["content"] == "extracted text"
    assert result["prompt_tokens"] == 200
    assert result["completion_tokens"] == 80
    assert result["total_tokens"] == 280
    # OpenAI path never reports cache splits.
    assert result["cache_read_tokens"] == 0
    assert result["cache_write_tokens"] == 0
    assert result["finish_reason"] == "stop"
    assert result["model"] == "gpt-4o-mini"
    assert result["provider"] == "openai"


def test_normalized_response_cost():
    """cost field is computed via calculate_cost() with correct provider and model."""
    from unified_pipeline.llm_client import call_llm
    from unified_pipeline.config import calculate_cost

    mock_response = _make_mock_response(prompt_tokens=1000, completion_tokens=500, total_tokens=1500)

    with patch("unified_pipeline.llm_client.get_stage_config", return_value=_default_config()), \
         patch("unified_pipeline.llm_client.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        result = call_llm("stage_2", [{"role": "user", "content": "test"}])

    expected_cost = calculate_cost(1000, 500, model="gpt-4o-mini", provider="openai")
    assert result["cost"] == pytest.approx(expected_cost)


def test_normalized_response_latency():
    """latency_ms field is a positive integer measuring elapsed time."""
    from unified_pipeline.llm_client import call_llm

    mock_response = _make_mock_response()

    with patch("unified_pipeline.llm_client.get_stage_config", return_value=_default_config()), \
         patch("unified_pipeline.llm_client.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        result = call_llm("stage_2", [{"role": "user", "content": "test"}])

    assert isinstance(result["latency_ms"], int)
    assert result["latency_ms"] >= 0


def test_normalized_response_bedrock():
    """Bedrock response has exactly the same required keys as OpenAI."""
    from unified_pipeline.llm_client import call_llm

    mock_response = _make_bedrock_response(
        content="extracted text",
        input_tokens=200,
        output_tokens=80,
        total_tokens=280,
        stop_reason="end_turn",
    )

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=_bedrock_config()), \
         patch("unified_pipeline.llm_client._get_bedrock_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = call_llm("stage_2", [{"role": "user", "content": "test"}])

    expected_keys = {"content", "prompt_tokens", "completion_tokens", "total_tokens",
                     "cache_read_tokens", "cache_write_tokens",
                     "cost", "model", "provider", "finish_reason", "latency_ms"}
    assert set(result.keys()) == expected_keys
    assert result["content"] == "extracted text"
    assert result["prompt_tokens"] == 200
    assert result["completion_tokens"] == 80
    assert result["total_tokens"] == 280
    # Mock response has no cacheRead/cacheWrite fields -> 0.
    assert result["cache_read_tokens"] == 0
    assert result["cache_write_tokens"] == 0
    assert result["finish_reason"] == "stop"
    assert result["provider"] == "bedrock"


# ---------------------------------------------------------------------------
# Retry behavior
# ---------------------------------------------------------------------------

def test_call_llm_retry_on_rate_limit():
    """call_llm retries on RateLimitError and succeeds on 3rd try."""
    from unified_pipeline.llm_client import call_llm
    from openai import RateLimitError

    mock_response = _make_mock_response()

    rate_limit_error = RateLimitError(
        "rate limited",
        response=MagicMock(status_code=429, headers={}),
        body=None,
    )

    with patch("unified_pipeline.llm_client.get_stage_config", return_value=_default_config()), \
         patch("unified_pipeline.llm_client.OpenAI") as mock_openai_cls, \
         patch("unified_pipeline.llm_client.time.sleep"):  # skip actual sleep
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = [
            rate_limit_error,
            rate_limit_error,
            mock_response,  # success on 3rd try
        ]
        mock_openai_cls.return_value = mock_client

        result = call_llm("stage_2", [{"role": "user", "content": "test"}])

    assert result["content"] == "test response"
    assert mock_client.chat.completions.create.call_count == 3


def test_call_llm_retry_exhausted():
    """call_llm raises RateLimitError when all retries are exhausted."""
    from unified_pipeline.llm_client import call_llm
    from openai import RateLimitError

    rate_limit_error = RateLimitError(
        "rate limited",
        response=MagicMock(status_code=429, headers={}),
        body=None,
    )

    with patch("unified_pipeline.llm_client.get_stage_config", return_value=_default_config()), \
         patch("unified_pipeline.llm_client.OpenAI") as mock_openai_cls, \
         patch("unified_pipeline.llm_client.time.sleep"):
        mock_client = MagicMock()
        # retry_count=3 means 4 total attempts (initial + 3 retries)
        mock_client.chat.completions.create.side_effect = [
            rate_limit_error,
            rate_limit_error,
            rate_limit_error,
            rate_limit_error,
        ]
        mock_openai_cls.return_value = mock_client

        with pytest.raises(RateLimitError):
            call_llm("stage_2", [{"role": "user", "content": "test"}])

    assert mock_client.chat.completions.create.call_count == 4


def test_call_llm_no_retry_on_auth_error():
    """call_llm raises AuthenticationError immediately without retrying."""
    from unified_pipeline.llm_client import call_llm
    from openai import AuthenticationError

    auth_error = AuthenticationError(
        "invalid api key",
        response=MagicMock(status_code=401, headers={}),
        body=None,
    )

    with patch("unified_pipeline.llm_client.get_stage_config", return_value=_default_config()), \
         patch("unified_pipeline.llm_client.OpenAI") as mock_openai_cls, \
         patch("unified_pipeline.llm_client.time.sleep"):
        mock_client = MagicMock()
        mock_client.chat.completions.create.side_effect = auth_error
        mock_openai_cls.return_value = mock_client

        with pytest.raises(AuthenticationError):
            call_llm("stage_2", [{"role": "user", "content": "test"}])

    # Should only be called once (no retry)
    assert mock_client.chat.completions.create.call_count == 1


# ---------------------------------------------------------------------------
# Provider dispatch
# ---------------------------------------------------------------------------

def test_call_llm_unsupported_provider():
    """call_llm raises ValueError for unsupported provider."""
    from unified_pipeline.llm_client import call_llm

    unsupported_config = {
        "provider": "azure",
        "model": "gpt-4o-mini",
        "temperature": 0,
        "max_tokens": None,
        "retry_count": 3,
    }
    with patch("unified_pipeline.llm_client.get_stage_config", return_value=unsupported_config):
        with pytest.raises(ValueError, match="Unsupported provider"):
            call_llm("stage_2", [{"role": "user", "content": "test"}])


# ---------------------------------------------------------------------------
# Bedrock provider (BED-01, TEST-01)
# ---------------------------------------------------------------------------

def test_call_llm_bedrock():
    """call_llm returns normalized dict for Bedrock provider."""
    from unified_pipeline.llm_client import call_llm

    mock_response = _make_bedrock_response()

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=_bedrock_config()), \
         patch("unified_pipeline.llm_client._get_bedrock_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = call_llm("stage_2", [{"role": "user", "content": "test"}])

    assert isinstance(result, dict)
    assert result["content"] == "test response"
    assert result["provider"] == "bedrock"
    assert result["model"] == "anthropic.claude-3-haiku-20240307-v1:0"
    assert result["prompt_tokens"] == 100
    assert result["completion_tokens"] == 50
    assert result["total_tokens"] == 150
    assert result["finish_reason"] == "stop"  # mapped from end_turn
    assert "cost" in result
    assert "latency_ms" in result


def test_call_llm_bedrock_params():
    """call_llm passes modelId and temperature to Bedrock Converse API."""
    from unified_pipeline.llm_client import call_llm

    mock_response = _make_bedrock_response()

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=_bedrock_config()), \
         patch("unified_pipeline.llm_client._get_bedrock_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response
        mock_get_client.return_value = mock_client

        call_llm("stage_2", [{"role": "user", "content": "classify"}],
                 response_format={"type": "json_object"})

        call_kwargs = mock_client.converse.call_args
        passed = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
        assert passed["modelId"] == "anthropic.claude-3-haiku-20240307-v1:0"
        assert passed["inferenceConfig"]["temperature"] == 0.0


def test_call_llm_bedrock_temperature():
    """call_llm passes temperature from config to Bedrock."""
    from unified_pipeline.llm_client import call_llm

    mock_response = _make_bedrock_response()
    cfg = _bedrock_config()
    cfg["temperature"] = 0.7

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=cfg), \
         patch("unified_pipeline.llm_client._get_bedrock_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response
        mock_get_client.return_value = mock_client

        call_llm("stage_2", [{"role": "user", "content": "test"}])

        call_kwargs = mock_client.converse.call_args
        passed = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
        assert passed["inferenceConfig"]["temperature"] == 0.7


def test_call_llm_bedrock_kwargs_override():
    """kwargs override config values for Bedrock calls."""
    from unified_pipeline.llm_client import call_llm

    mock_response = _make_bedrock_response()

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=_bedrock_config()), \
         patch("unified_pipeline.llm_client._get_bedrock_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = call_llm("stage_2", [{"role": "user", "content": "test"}],
                          model="anthropic.claude-3-5-sonnet-20241022-v2:0",
                          temperature=0.5)

        call_kwargs = mock_client.converse.call_args
        passed = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
        assert passed["modelId"] == "anthropic.claude-3-5-sonnet-20241022-v2:0"
        assert passed["inferenceConfig"]["temperature"] == 0.5
        assert result["model"] == "anthropic.claude-3-5-sonnet-20241022-v2:0"


def test_call_llm_bedrock_system_message_separation():
    """Bedrock call separates system messages into the system parameter."""
    from unified_pipeline.llm_client import call_llm

    mock_response = _make_bedrock_response()

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=_bedrock_config()), \
         patch("unified_pipeline.llm_client._get_bedrock_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response
        mock_get_client.return_value = mock_client

        messages = [
            {"role": "system", "content": "You are a classifier"},
            {"role": "user", "content": "Classify this"},
        ]
        call_llm("stage_2", messages)

        call_kwargs = mock_client.converse.call_args
        passed = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
        # System messages should be in system param, not in messages
        assert "system" in passed
        assert passed["system"][0]["text"] == "You are a classifier"
        # Only user message should be in messages
        assert len(passed["messages"]) == 1
        assert passed["messages"][0]["role"] == "user"
        assert passed["messages"][0]["content"] == [{"text": "Classify this"}]


def test_call_llm_bedrock_stop_reason_mapping():
    """Bedrock stopReason values are mapped to OpenAI finish_reason equivalents."""
    from unified_pipeline.llm_client import call_llm

    mock_response = _make_bedrock_response(stop_reason="max_tokens")

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=_bedrock_config()), \
         patch("unified_pipeline.llm_client._get_bedrock_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = call_llm("stage_2", [{"role": "user", "content": "test"}])

    assert result["finish_reason"] == "length"  # mapped from max_tokens


def test_call_llm_bedrock_retry_on_throttle():
    """call_llm retries on Bedrock ThrottlingException and succeeds."""
    from unified_pipeline.llm_client import call_llm
    from botocore.exceptions import ClientError

    mock_response = _make_bedrock_response()

    throttle_error = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
        "Converse",
    )

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=_bedrock_config()), \
         patch("unified_pipeline.llm_client._get_bedrock_client") as mock_get_client, \
         patch("unified_pipeline.llm_client.time.sleep"):
        mock_client = MagicMock()
        mock_client.converse.side_effect = [
            throttle_error,
            throttle_error,
            mock_response,
        ]
        mock_get_client.return_value = mock_client

        result = call_llm("stage_2", [{"role": "user", "content": "test"}])

    assert result["content"] == "test response"
    assert mock_client.converse.call_count == 3


def test_call_llm_bedrock_retry_exhausted():
    """call_llm raises ClientError when all Bedrock retries are exhausted."""
    from unified_pipeline.llm_client import call_llm
    from botocore.exceptions import ClientError

    throttle_error = ClientError(
        {"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"}},
        "Converse",
    )

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=_bedrock_config()), \
         patch("unified_pipeline.llm_client._get_bedrock_client") as mock_get_client, \
         patch("unified_pipeline.llm_client.time.sleep"):
        mock_client = MagicMock()
        mock_client.converse.side_effect = [
            throttle_error, throttle_error, throttle_error, throttle_error,
        ]
        mock_get_client.return_value = mock_client

        with pytest.raises(ClientError):
            call_llm("stage_2", [{"role": "user", "content": "test"}])

    assert mock_client.converse.call_count == 4


def test_call_llm_bedrock_no_retry_on_access_denied():
    """call_llm raises AccessDeniedException immediately without retrying."""
    from unified_pipeline.llm_client import call_llm
    from botocore.exceptions import ClientError

    access_error = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "Not authorized"}},
        "Converse",
    )

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=_bedrock_config()), \
         patch("unified_pipeline.llm_client._get_bedrock_client") as mock_get_client, \
         patch("unified_pipeline.llm_client.time.sleep"):
        mock_client = MagicMock()
        mock_client.converse.side_effect = access_error
        mock_get_client.return_value = mock_client

        with pytest.raises(ClientError):
            call_llm("stage_2", [{"role": "user", "content": "test"}])

    assert mock_client.converse.call_count == 1


# ---------------------------------------------------------------------------
# JSON validation + retry (D-05, D-07)
# ---------------------------------------------------------------------------

def test_bedrock_json_valid_passthrough():
    """Valid JSON response passes through without retry."""
    from unified_pipeline.llm_client import call_llm

    valid_json = '{"category": "publications", "count": 5}'
    mock_response = _make_bedrock_response(content=valid_json)

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=_bedrock_config()), \
         patch("unified_pipeline.llm_client._get_bedrock_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = call_llm("stage_2", [{"role": "user", "content": "test"}],
                          response_format={"type": "json_object"})

    assert result["content"] == valid_json
    # Should only call converse once (no retry needed)
    assert mock_client.converse.call_count == 1


def test_bedrock_json_invalid_triggers_retry():
    """Invalid JSON response triggers one retry with stronger prompt hint."""
    from unified_pipeline.llm_client import call_llm

    invalid_json = "Here is the JSON: {bad}"
    valid_json = '{"category": "publications"}'
    first_response = _make_bedrock_response(content=invalid_json)
    retry_response = _make_bedrock_response(content=valid_json)

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=_bedrock_config()), \
         patch("unified_pipeline.llm_client._get_bedrock_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.converse.side_effect = [first_response, retry_response]
        mock_get_client.return_value = mock_client

        result = call_llm("stage_2", [{"role": "user", "content": "test"}],
                          response_format={"type": "json_object"})

    assert result["content"] == valid_json
    # Should call converse twice (initial + retry)
    assert mock_client.converse.call_count == 2


def test_bedrock_json_second_failure_returns_as_is():
    """If retry also produces invalid JSON, return content as-is."""
    from unified_pipeline.llm_client import call_llm

    invalid_json_1 = "not json at all"
    invalid_json_2 = "still not json"
    first_response = _make_bedrock_response(content=invalid_json_1)
    retry_response = _make_bedrock_response(content=invalid_json_2)

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=_bedrock_config()), \
         patch("unified_pipeline.llm_client._get_bedrock_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.converse.side_effect = [first_response, retry_response]
        mock_get_client.return_value = mock_client

        result = call_llm("stage_2", [{"role": "user", "content": "test"}],
                          response_format={"type": "json_object"})

    # Should return the retry content (not raise), per D-05
    assert result["content"] == invalid_json_2
    assert mock_client.converse.call_count == 2

# ---------------------------------------------------------------------------
# Markdown-fence stripping (Bedrock json_schema / json_object responses)
# ---------------------------------------------------------------------------

def test_strip_markdown_fences_plain_json_unchanged():
    """Content without fences is returned unchanged."""
    from unified_pipeline.llm_client import _strip_markdown_fences
    assert _strip_markdown_fences('{"x": 1}') == '{"x": 1}'


def test_strip_markdown_fences_json_lang_tag():
    """```json … ``` block is unwrapped to its inner content."""
    from unified_pipeline.llm_client import _strip_markdown_fences
    fenced = '```json\n{"x": 1}\n```'
    assert _strip_markdown_fences(fenced) == '{"x": 1}'


def test_strip_markdown_fences_bare_fences():
    """``` … ``` without a language tag also unwraps."""
    from unified_pipeline.llm_client import _strip_markdown_fences
    fenced = '```\n{"x": 1}\n```'
    assert _strip_markdown_fences(fenced) == '{"x": 1}'


def test_strip_markdown_fences_with_surrounding_whitespace():
    """Leading/trailing whitespace around the fence block is tolerated."""
    from unified_pipeline.llm_client import _strip_markdown_fences
    fenced = '   \n```json\n{"x": 1}\n```\n  '
    assert _strip_markdown_fences(fenced) == '{"x": 1}'


def test_strip_markdown_fences_preserves_inline_fences():
    """A response with inline (not whole-block) fences is left intact."""
    from unified_pipeline.llm_client import _strip_markdown_fences
    inline = 'Here is some text with ```inline``` fences in the middle.'
    assert _strip_markdown_fences(inline) == inline


def test_bedrock_json_schema_fenced_response_is_stripped():
    """Claude wraps JSON in markdown fences even when told not to.
    When response_format is json_schema (or json_object), the Bedrock path
    must strip the fence so callers can json.loads() the content directly.
    Without this, every parser that uses json_schema would crash with
    JSONDecodeError on the leading backtick."""
    from unified_pipeline.llm_client import call_llm

    fenced = '```json\n{"section_id": "orcid_id", "confidence": 0.99}\n```'
    mock_response = _make_bedrock_response(content=fenced)

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=_bedrock_config()), \
         patch("unified_pipeline.llm_client._get_bedrock_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = call_llm(
            "stage_2",
            [{"role": "user", "content": "classify"}],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "x", "strict": True, "schema": {}},
            },
        )

    # Content must be the inner JSON, not the fenced wrapper.
    assert result["content"] == '{"section_id": "orcid_id", "confidence": 0.99}'
    # And it must round-trip through json.loads(), which is what parsers do.
    import json as _json
    parsed = _json.loads(result["content"])
    assert parsed["section_id"] == "orcid_id"
    # No retry should have fired -- the content was valid after stripping.
    assert mock_client.converse.call_count == 1


def test_bedrock_json_object_fenced_response_is_stripped():
    """Same fence-stripping applies to json_object responses (regression:
    previously the retry path fired and the second response was returned
    as-is, which could itself still be fenced)."""
    from unified_pipeline.llm_client import call_llm

    fenced = '```json\n{"x": 1}\n```'
    mock_response = _make_bedrock_response(content=fenced)

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=_bedrock_config()), \
         patch("unified_pipeline.llm_client._get_bedrock_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = call_llm("stage_2", [{"role": "user", "content": "test"}],
                          response_format={"type": "json_object"})

    assert result["content"] == '{"x": 1}'
    assert mock_client.converse.call_count == 1


def test_bedrock_json_schema_appends_json_only_hint():
    """Bedrock has no native structured-output mode, so a 'respond with
    valid JSON only' hint is appended to the system message whenever a
    JSON response is requested. Regression: the hint previously only
    fired for json_object, leaving json_schema callers unguarded."""
    from unified_pipeline.llm_client import call_llm

    mock_response = _make_bedrock_response(content='{"x": 1}')

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=_bedrock_config()), \
         patch("unified_pipeline.llm_client._get_bedrock_client") as mock_get_client:
        mock_client = MagicMock()
        mock_client.converse.return_value = mock_response
        mock_get_client.return_value = mock_client

        call_llm(
            "stage_2",
            [
                {"role": "system", "content": "You classify CVs."},
                {"role": "user", "content": "Classify this entry."},
            ],
            response_format={
                "type": "json_schema",
                "json_schema": {"name": "x", "strict": True, "schema": {}},
            },
        )

        passed = mock_client.converse.call_args.kwargs
        system_text = passed["system"][0]["text"]
        assert "valid JSON only" in system_text, (
            f"json_schema requests should append the JSON-only hint to the "
            f"Bedrock system message; got: {system_text!r}"
        )
# ---------------------------------------------------------------------------
# Lazy initialization
# ---------------------------------------------------------------------------

def test_openai_client_lazy_init():
    """Importing llm_client does not create an OpenAI client. First call does."""
    import unified_pipeline.llm_client as mod

    # After import (and reset in fixture), client should be None
    assert mod._openai_client is None

    mock_response = _make_mock_response()

    with patch("unified_pipeline.llm_client.get_stage_config", return_value=_default_config()), \
         patch("unified_pipeline.llm_client.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        # After calling, client should be set
        mod.call_llm("stage_2", [{"role": "user", "content": "test"}])
        assert mod._openai_client is not None


def test_bedrock_client_lazy_init():
    """Importing llm_client does not create a Bedrock client. First Bedrock call does."""
    import unified_pipeline.llm_client as mod

    # After import (and reset in fixture), client should be None
    assert mod._bedrock_client is None

    mock_response = _make_bedrock_response()
    mock_client = MagicMock()
    mock_client.converse.return_value = mock_response

    with patch("unified_pipeline.llm_client.get_stage_config",
               return_value=_bedrock_config()), \
         patch("boto3.client", return_value=mock_client):

        mod.call_llm("stage_2", [{"role": "user", "content": "test"}])
        assert mod._bedrock_client is not None


# ---------------------------------------------------------------------------
# response_format passthrough
# ---------------------------------------------------------------------------

def test_response_format_passthrough():
    """response_format dict is passed through to OpenAI SDK create() as-is."""
    from unified_pipeline.llm_client import call_llm

    mock_response = _make_mock_response()
    rf = {"type": "json_object"}

    with patch("unified_pipeline.llm_client.get_stage_config", return_value=_default_config()), \
         patch("unified_pipeline.llm_client.OpenAI") as mock_openai_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create.return_value = mock_response
        mock_openai_cls.return_value = mock_client

        call_llm("stage_2", [{"role": "user", "content": "test"}],
                 response_format=rf)

        call_kwargs = mock_client.chat.completions.create.call_args
        passed_kwargs = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]
        assert passed_kwargs["response_format"] == {"type": "json_object"}


# ---------------------------------------------------------------------------
# Plan 02 integration: LLMUsage provider column
# ---------------------------------------------------------------------------

def test_llm_usage_provider_column():
    """LLMUsage model has a provider column with server_default='openai'."""
    # This test verifies Plan 02 output is present
    backend_path = str(Path(__file__).parent.parent)
    if backend_path not in sys.path:
        sys.path.insert(0, backend_path)

    from app.models import LLMUsage
    from sqlalchemy import inspect as sa_inspect

    # Check the column exists
    mapper = sa_inspect(LLMUsage)
    column_names = [c.key for c in mapper.mapper.column_attrs]
    assert "provider" in column_names

    # Check server_default
    provider_col = LLMUsage.__table__.columns["provider"]
    assert provider_col.server_default is not None
    assert "openai" in str(provider_col.server_default.arg)


# ---------------------------------------------------------------------------
# E2E pipeline smoke test (TEST-02)
# ---------------------------------------------------------------------------

@pytest.mark.e2e
def test_pipeline_e2e_openai():
    """Full CV pipeline completes with OpenAI config and produces output.

    This is a smoke test per D-08: pipeline completes all stages without
    exceptions and generates output .docx. No assertions on output quality.

    Requires OPENAI_API_KEY in environment. Costs money per run.
    Run explicitly: pytest -m e2e

    Per D-10: Runs against OpenAI (default config) only. Bedrock E2E is
    manual when AWS credentials are available.
    """
    import os
    import tempfile
    import shutil

    # Skip if no API key available
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY not set -- skipping E2E test")

    # Use the sample CV
    sample_cv = str(Path(__file__).parent.parent.parent.parent / "data" /
                     "sample_cvs" / "word" / "sample_vasquez_cv.docx")
    if not Path(sample_cv).exists():
        pytest.skip(f"Sample CV not found at {sample_cv}")

    # Create temp output directory so test doesn't pollute project outputs
    temp_dir = tempfile.mkdtemp(prefix="cviche_e2e_")

    try:
        from unified_pipeline.core.cv_pipeline import CVPipeline

        # Pass output_dir via the constructor so it is normalised to Path()
        # alongside the stage_dirs map. Monkey-patching pipeline.output_dir
        # after construction skips that normalisation (and CVPipeline uses
        # `self.output_dir / "subdir"` at several call sites).
        pipeline = CVPipeline(sample_cv, output_dir=temp_dir)

        result = pipeline.run()

        # D-08: Verify pipeline completed without exceptions
        assert result is not None, "Pipeline returned None"
        assert isinstance(result, dict), f"Pipeline returned {type(result)}, expected dict"

        # Check that output files were generated (at minimum, some output exists)
        output_files = list(Path(temp_dir).rglob("*"))
        assert len(output_files) > 0, "Pipeline produced no output files"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Load hardening: per-pod concurrency cap + retry backoff jitter
# ---------------------------------------------------------------------------

def test_max_concurrent_llm_calls_env_override(monkeypatch):
    import unified_pipeline.llm_client as mod
    monkeypatch.setenv("CVICHE_MAX_CONCURRENT_LLM_CALLS", "3")
    assert mod._get_max_concurrent_llm_calls() == 3
    # Non-positive or garbage falls back to the default (never wedge to zero).
    monkeypatch.setenv("CVICHE_MAX_CONCURRENT_LLM_CALLS", "0")
    assert mod._get_max_concurrent_llm_calls() == 8
    monkeypatch.setenv("CVICHE_MAX_CONCURRENT_LLM_CALLS", "garbage")
    assert mod._get_max_concurrent_llm_calls() == 8


def test_retry_backoff_uses_equal_jitter(monkeypatch):
    """Backoff waits land in [base/2, base] (equal jitter) so concurrent
    throttled callers don't retry in lockstep."""
    import unified_pipeline.llm_client as mod
    from botocore.exceptions import ClientError

    sleeps = []
    monkeypatch.setattr(mod.time, "sleep", lambda s: sleeps.append(s))

    throttle = ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")

    def always_throttled():
        raise throttle

    with pytest.raises(ClientError):
        mod._call_with_retry(always_throttled, retry_count=3)

    # retry_count=3 -> sleeps before attempts 1,2,3 with exponential bases 1,2,4.
    assert len(sleeps) == 3
    for wait, base in zip(sleeps, (1, 2, 4)):
        assert base / 2 <= wait <= base


def test_call_semaphore_released_on_success_and_failure(monkeypatch):
    """The in-flight-call slot must be returned whether the call succeeds or
    raises, so a pod can't slowly leak its way to a deadlock."""
    import unified_pipeline.llm_client as mod
    from botocore.exceptions import ClientError

    sem = mod._llm_call_semaphore
    initial = sem._value  # available permits (CPython BoundedSemaphore)

    assert mod._call_with_retry(lambda: "ok", retry_count=0) == "ok"
    assert sem._value == initial  # released after success

    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    non_retryable = ClientError({"Error": {"Code": "ValidationException"}}, "Converse")

    def fails():
        raise non_retryable

    with pytest.raises(ClientError):
        mod._call_with_retry(fails, retry_count=2)
    assert sem._value == initial  # released after failure too
