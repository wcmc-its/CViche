"""
Centralized LLM client for CV parsing pipeline.

Provides call_llm() as the single entry point for all LLM API calls.
Pipeline stages should use this instead of direct OpenAI SDK calls.

Supports:
- Config-driven provider and model selection (via llm_config.yaml)
- Normalized response format (same dict shape regardless of provider)
- Built-in retry with exponential backoff on transient errors
- Automatic cost calculation per call
- Lazy client initialization (no import-time side effects)

Usage:
    from unified_pipeline.llm_client import call_llm

    result = call_llm(
        stage="stage_3b",
        messages=[{"role": "user", "content": "Classify this entry..."}],
        response_format={"type": "json_object"},
    )
    print(result["content"])   # LLM response text
    print(result["cost"])      # Cost in USD
    print(result["provider"])  # "openai"
"""

import time
import logging
from openai import (
    OpenAI,
    RateLimitError,
    APITimeoutError,
    APIConnectionError,
    InternalServerError,
)

from unified_pipeline.config import get_stage_config, calculate_cost

logger = logging.getLogger(__name__)

# Lazy-initialized OpenAI client (NOT created at import time per Pitfall 2)
_openai_client = None

RETRYABLE_ERRORS = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)


def _get_openai_client():
    """Get or create the OpenAI client (lazy initialization)."""
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


def _call_with_retry(call_fn, retry_count: int = 3):
    """Call function with exponential backoff on transient errors.

    Args:
        call_fn: Zero-argument callable that makes the API call
        retry_count: Max number of retries (total attempts = retry_count + 1)

    Returns:
        The return value of call_fn on success

    Raises:
        The last error if all retries are exhausted
        Non-retryable errors immediately
    """
    last_error = None
    for attempt in range(retry_count + 1):
        try:
            return call_fn()
        except RETRYABLE_ERRORS as e:
            last_error = e
            if attempt < retry_count:
                wait = min(2 ** attempt, 30)  # 1s, 2s, 4s... capped at 30s
                logger.warning(
                    f"LLM call failed (attempt {attempt + 1}/{retry_count + 1}): {e}. "
                    f"Retrying in {wait}s..."
                )
                time.sleep(wait)
    raise last_error


def _call_openai(model: str, messages: list, temperature: float,
                 response_format=None, max_tokens=None, **kwargs):
    """Make an OpenAI chat completion call.

    Args:
        model: Model name (e.g., "gpt-4o-mini")
        messages: List of message dicts
        temperature: Temperature setting
        response_format: Optional response format (e.g., {"type": "json_object"})
        max_tokens: Optional max tokens limit
        **kwargs: Additional arguments passed to the OpenAI SDK

    Returns:
        OpenAI ChatCompletion response object
    """
    client = _get_openai_client()

    call_kwargs = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    if response_format is not None:
        call_kwargs["response_format"] = response_format

    if max_tokens is not None:
        call_kwargs["max_completion_tokens"] = max_tokens

    # Pass through any additional kwargs (filtering out keys we already handle)
    handled_keys = {"model", "messages", "temperature", "response_format", "max_tokens",
                    "stage", "retry_count", "provider"}
    for k, v in kwargs.items():
        if k not in handled_keys and v is not None:
            call_kwargs[k] = v

    return client.chat.completions.create(**call_kwargs)


def call_llm(stage: str, messages: list, response_format=None, **kwargs) -> dict:
    """Centralized LLM call with config resolution, retries, and cost tracking.

    Args:
        stage: Pipeline stage identifier (e.g., "stage_3b", "stage_4")
        messages: List of message dicts [{"role": "user", "content": "..."}]
        response_format: Optional response format (e.g., {"type": "json_object"})
        **kwargs: Override config values -- model, temperature, max_tokens, etc.

    Returns:
        Normalized response dict with keys:
        - content (str): LLM response text
        - prompt_tokens (int): Input token count
        - completion_tokens (int): Output token count
        - total_tokens (int): Total token count
        - cost (float): Cost in USD
        - model (str): Model used
        - provider (str): Provider used ("openai", etc.)
        - finish_reason (str): Why generation stopped
        - latency_ms (int): Wall-clock time in milliseconds

    Raises:
        ValueError: If provider is not supported
        openai errors: On non-retryable errors or exhausted retries
    """
    config = get_stage_config(stage)
    provider = kwargs.get("provider", config["provider"])
    model = kwargs.get("model", config["model"])
    temperature = kwargs.get("temperature", config["temperature"])
    max_tokens = kwargs.get("max_tokens", config["max_tokens"])
    retry_count = kwargs.get("retry_count", config["retry_count"])

    # Filter out keys already extracted as explicit args
    extra_kwargs = {k: v for k, v in kwargs.items()
                    if k not in {"provider", "model", "temperature", "max_tokens",
                                 "retry_count", "stage"}}

    start_time = time.time()

    # Dispatch to provider
    if provider == "openai":
        response = _call_with_retry(
            lambda: _call_openai(model, messages, temperature, response_format,
                                 max_tokens, **extra_kwargs),
            retry_count=retry_count
        )
    else:
        raise ValueError(
            f"Unsupported provider: {provider}. "
            f"Supported providers: openai. "
            f"(Bedrock support coming in Phase 21)"
        )

    latency_ms = int((time.time() - start_time) * 1000)

    # Extract response fields
    content = response.choices[0].message.content
    usage = response.usage
    finish_reason = response.choices[0].finish_reason

    # Calculate cost
    cost = calculate_cost(
        usage.prompt_tokens,
        usage.completion_tokens,
        model=model,
        provider=provider,
    )

    return {
        "content": content,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cost": cost,
        "model": model,
        "provider": provider,
        "finish_reason": finish_reason,
        "latency_ms": latency_ms,
    }
