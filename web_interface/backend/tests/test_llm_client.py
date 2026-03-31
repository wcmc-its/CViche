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
        "model": "claude-3-haiku",
        "temperature": 0,
        "max_tokens": None,
        "retry_count": 3,
    }


@pytest.fixture(autouse=True)
def reset_state():
    """Reset config cache and OpenAI client before each test."""
    reload_config()
    import unified_pipeline.llm_client as mod
    mod._openai_client = None
    yield
    reload_config()
    mod._openai_client = None


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
    """Returned dict has exactly the 9 required keys with correct values."""
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
                     "cost", "model", "provider", "finish_reason", "latency_ms"}
    assert set(result.keys()) == expected_keys
    assert result["content"] == "extracted text"
    assert result["prompt_tokens"] == 200
    assert result["completion_tokens"] == 80
    assert result["total_tokens"] == 280
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

    with patch("unified_pipeline.llm_client.get_stage_config", return_value=_bedrock_config()):
        with pytest.raises(ValueError, match="Unsupported provider"):
            call_llm("stage_2", [{"role": "user", "content": "test"}])


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
