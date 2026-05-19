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
- OpenAI and AWS Bedrock providers

Usage:
    from unified_pipeline.llm_client import call_llm

    result = call_llm(
        stage="stage_3b",
        messages=[{"role": "user", "content": "Classify this entry..."}],
        response_format={"type": "json_object"},
    )
    print(result["content"])   # LLM response text
    print(result["cost"])      # Cost in USD
    print(result["provider"])  # "openai" or "bedrock"
"""

import os
import time
import json as json_module
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

# Bedrock stopReason -> OpenAI finish_reason mapping
STOP_REASON_MAP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "guard_intervened": "content_filter",
}

# Bedrock error codes that should trigger retry
BEDROCK_RETRYABLE_CODES = frozenset({
    "ThrottlingException",
    "ModelTimeoutException",
    "InternalServerException",
    "ServiceUnavailableException",
})

# Lazy-initialized OpenAI client (NOT created at import time per Pitfall 2)
_openai_client = None

# Import ClientError at module level for RETRYABLE_ERRORS tuple.
# botocore is always available as a transitive dependency of boto3,
# but we guard the import so it doesn't fail when boto3 is not installed.
try:
    from botocore.exceptions import ClientError as _BotoClientError
except ImportError:
    _BotoClientError = type(None)  # Will never match if botocore is not installed

RETRYABLE_ERRORS = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError, _BotoClientError)


def _get_openai_client():
    """Get or create the OpenAI client (lazy initialization)."""
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI()
    return _openai_client


_bedrock_client = None


def _get_bedrock_client():
    """Get or create the Bedrock Runtime client (lazy initialization).

    Uses boto3 default credential chain (BED-02):
    1. AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY env vars
    2. ~/.aws/credentials
    3. IAM instance role (EC2/ECS/EKS)
    """
    global _bedrock_client
    if _bedrock_client is None:
        import boto3
        region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
        _bedrock_client = boto3.client("bedrock-runtime", region_name=region)
    return _bedrock_client


def _call_with_retry(call_fn, retry_count: int = 3):
    """Call function with exponential backoff on transient errors.

    Args:
        call_fn: Zero-argument callable that makes the API call
        retry_count: Max number of retries (total attempts = retry_count + 1)

    Returns:
        The return value of call_fn on success

    Raises:
        The last error if all retries are exhausted
        Non-retryable errors immediately (including non-retryable ClientError)
    """
    last_error = None
    for attempt in range(retry_count + 1):
        try:
            return call_fn()
        except RETRYABLE_ERRORS as e:
            # For botocore ClientError, only retry if the error code is retryable.
            # Non-retryable Bedrock errors (AccessDeniedException, ValidationException,
            # etc.) should propagate immediately.
            if isinstance(e, _BotoClientError) and _BotoClientError is not type(None):
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code not in BEDROCK_RETRYABLE_CODES:
                    raise
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


def _translate_messages(messages, response_format=None):
    """Translate OpenAI-style messages to Bedrock Converse format.

    Bedrock Converse API separates system messages from conversation messages.
    System messages become the `system` parameter, other messages go in `messages`.
    Content must be wrapped in [{"text": "..."}] format.

    Args:
        messages: OpenAI-style message list [{"role": "...", "content": "..."}]
        response_format: Optional response format dict

    Returns:
        Tuple of (system_prompts, converse_messages)
    """
    system_prompts = []
    converse_messages = []
    json_hint = (response_format and response_format.get("type") == "json_object")

    for msg in messages:
        if msg["role"] == "system":
            text = msg["content"]
            if json_hint:
                text += "\n\nRespond with valid JSON only."
            system_prompts.append({"text": text})
        else:
            converse_messages.append({
                "role": msg["role"],
                "content": [{"text": msg["content"]}],
            })

    # If JSON format requested but no system message existed, create one
    if json_hint and not system_prompts:
        system_prompts.append({"text": "Respond with valid JSON only."})

    return system_prompts, converse_messages


def _validate_json_response(content, response_format):
    """Check if content is valid JSON when response_format requires it.

    Args:
        content: Response text from LLM
        response_format: The response_format dict (or None)

    Returns:
        True if no validation needed or content is valid JSON, False otherwise
    """
    if not response_format or response_format.get("type") != "json_object":
        return True
    try:
        json_module.loads(content)
        return True
    except (json_module.JSONDecodeError, TypeError):
        return False


def _call_bedrock(model, messages, temperature, response_format=None,
                  max_tokens=None, enable_prompt_caching=False, **kwargs):
    """Make a Bedrock Converse API call.

    Args:
        model: Bedrock model ID (e.g., "anthropic.claude-3-haiku-20240307-v1:0")
        messages: OpenAI-style message list (will be translated to Converse format)
        temperature: Temperature setting
        response_format: Optional response format (triggers prompt injection per D-04)
        max_tokens: Optional max tokens limit
        enable_prompt_caching: If True, append a cachePoint checkpoint after
            the system block so the system prompt is read from cache on
            subsequent calls within the 5-minute TTL. When False, the request
            is byte-identical to the pre-caching shape.
        **kwargs: Additional arguments (currently unused for Bedrock)

    Returns:
        Bedrock Converse response dict

    Raises:
        botocore.exceptions.ClientError: On non-retryable Bedrock errors
    """
    from botocore.exceptions import ClientError

    client = _get_bedrock_client()
    system_prompts, converse_messages = _translate_messages(messages, response_format)

    if enable_prompt_caching and system_prompts:
        # Cache the system block: the large, stable schema / instruction prefix
        # that stage_4 re-sends across 30-60+ calls per CV. The checkpoint goes
        # AFTER the cacheable content, marking it as the end of the cached
        # prefix. Bedrock returns the previously-written cache as a read on
        # subsequent calls that match the cached prefix.
        system_prompts = system_prompts + [{"cachePoint": {"type": "default"}}]

    call_kwargs = {
        "modelId": model,
        "messages": converse_messages,
        "inferenceConfig": {"temperature": float(temperature)},
    }
    if system_prompts:
        call_kwargs["system"] = system_prompts
    if max_tokens is not None:
        call_kwargs["inferenceConfig"]["maxTokens"] = max_tokens

    try:
        return client.converse(**call_kwargs)
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code in BEDROCK_RETRYABLE_CODES:
            # Re-raise as-is; _call_with_retry will catch it via RETRYABLE_ERRORS
            raise
        # Non-retryable errors propagate immediately
        raise


def _extract_cache_tokens(usage: dict) -> tuple:
    """Read cacheRead / cacheWrite token counts from a Converse `usage` dict.

    Bedrock's prompt-caching docs use `cacheReadInputTokens` and
    `cacheWriteInputTokens` in the prompt-caching guide, but the conversation-
    inference page documents the same fields as `...Count`-suffixed names and
    some examples capitalize them. Accept all observed variants so the caller
    doesn't have to track which one a given response actually carries.

    Returns:
        (cache_read_tokens, cache_write_tokens) -- both 0 when caching is
        disabled or the response doesn't include the fields.
    """
    if not usage:
        return 0, 0
    cache_read = (usage.get("cacheReadInputTokens")
                  or usage.get("cacheReadInputTokensCount")
                  or usage.get("CacheReadInputTokens")
                  or 0)
    cache_write = (usage.get("cacheWriteInputTokens")
                   or usage.get("cacheWriteInputTokensCount")
                   or usage.get("CacheWriteInputTokens")
                   or 0)
    return int(cache_read), int(cache_write)


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
        - prompt_tokens (int): Total input token count (uncached + cache
          read + cache write). With Bedrock caching on, the SDK's own
          `inputTokens` is the uncached portion only, so this is
          synthesized to keep downstream token tracking intact.
        - completion_tokens (int): Output token count
        - total_tokens (int): prompt_tokens + completion_tokens
        - cache_read_tokens (int): Input tokens served from prompt cache
          (priced at 0.1x input). 0 when caching is off or not supported.
        - cache_write_tokens (int): Input tokens written to prompt cache
          (priced at 1.25x input). 0 when caching is off or not supported.
        - cost (float): Cost in USD
        - model (str): Model used
        - provider (str): Provider used ("openai" or "bedrock")
        - finish_reason (str): Why generation stopped
        - latency_ms (int): Wall-clock time in milliseconds

    Raises:
        ValueError: If provider is not supported
        openai errors: On non-retryable OpenAI errors or exhausted retries
        botocore.exceptions.ClientError: On non-retryable Bedrock errors
    """
    config = get_stage_config(stage)
    provider = kwargs.get("provider", config["provider"])
    model = kwargs.get("model", config["model"])
    temperature = kwargs.get("temperature", config["temperature"])
    max_tokens = kwargs.get("max_tokens", config["max_tokens"])
    retry_count = kwargs.get("retry_count", config["retry_count"])
    enable_prompt_caching = kwargs.get("enable_prompt_caching",
                                       config.get("enable_prompt_caching", False))

    # Filter out keys already extracted as explicit args
    extra_kwargs = {k: v for k, v in kwargs.items()
                    if k not in {"provider", "model", "temperature", "max_tokens",
                                 "retry_count", "stage", "enable_prompt_caching"}}

    start_time = time.time()

    # Dispatch to provider
    if provider == "openai":
        response = _call_with_retry(
            lambda: _call_openai(model, messages, temperature, response_format,
                                 max_tokens, **extra_kwargs),
            retry_count=retry_count,
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
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost": cost,
            "model": model,
            "provider": provider,
            "finish_reason": finish_reason,
            "latency_ms": latency_ms,
        }

    elif provider == "bedrock":
        response = _call_with_retry(
            lambda: _call_bedrock(model, messages, temperature, response_format,
                                  max_tokens,
                                  enable_prompt_caching=enable_prompt_caching,
                                  **extra_kwargs),
            retry_count=retry_count,
        )

        latency_ms = int((time.time() - start_time) * 1000)

        # Extract and normalize Bedrock response
        content = response["output"]["message"]["content"][0]["text"]
        usage = response["usage"]
        stop_reason = response.get("stopReason", "end_turn")
        finish_reason = STOP_REASON_MAP.get(stop_reason, stop_reason)
        cache_read_tokens, cache_write_tokens = _extract_cache_tokens(usage)

        # D-05: Validate JSON when response_format was requested
        if not _validate_json_response(content, response_format):
            logger.warning("Bedrock response is not valid JSON. Retrying with stronger hint...")
            # Retry once with stronger prompt hint
            stronger_messages = list(messages)  # shallow copy
            stronger_messages.append({
                "role": "user",
                "content": "Your previous response was not valid JSON. Please respond with ONLY valid JSON, no markdown fencing or explanation.",
            })
            retry_response = _call_bedrock(model, stronger_messages, temperature,
                                            response_format, max_tokens,
                                            enable_prompt_caching=enable_prompt_caching,
                                            **extra_kwargs)
            content = retry_response["output"]["message"]["content"][0]["text"]
            retry_usage = retry_response["usage"]
            retry_cache_read, retry_cache_write = _extract_cache_tokens(retry_usage)
            # Accumulate token usage from retry
            usage = {
                "inputTokens": usage["inputTokens"] + retry_usage["inputTokens"],
                "outputTokens": usage["outputTokens"] + retry_usage["outputTokens"],
                "totalTokens": usage["totalTokens"] + retry_usage["totalTokens"],
            }
            cache_read_tokens += retry_cache_read
            cache_write_tokens += retry_cache_write
            stop_reason = retry_response.get("stopReason", "end_turn")
            finish_reason = STOP_REASON_MAP.get(stop_reason, stop_reason)
            # If still invalid, return as-is (let downstream handle it per D-05)

        # With caching on, Bedrock's `inputTokens` reports ONLY the uncached
        # input tokens; the cached portion shows up in cacheRead/cacheWrite.
        # calculate_cost prices each bucket separately, and we synthesize
        # totals from the three so downstream cost/token tracking still sees
        # the full input regardless of caching.
        uncached_input_tokens = usage["inputTokens"]
        output_tokens = usage["outputTokens"]
        total_input_tokens = uncached_input_tokens + cache_read_tokens + cache_write_tokens
        total_tokens = total_input_tokens + output_tokens

        cost = calculate_cost(
            uncached_input_tokens,
            output_tokens,
            model=model,
            provider="bedrock",
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        )

        return {
            "content": content,
            "prompt_tokens": total_input_tokens,
            "completion_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_write_tokens": cache_write_tokens,
            "cost": cost,
            "model": model,
            "provider": "bedrock",
            "finish_reason": finish_reason,
            "latency_ms": latency_ms,
        }

    else:
        raise ValueError(
            f"Unsupported provider: {provider}. "
            f"Supported providers: openai, bedrock."
        )
