"""OpenAI provider adapter: client, raw SDK call, and response normalization.

Split out of llm_client.py (#496). The OpenAI SDK handles JSON mode and tool
calls natively, so unlike the Bedrock adapter this file has no
translation/schema-enforcement layer.
"""

from openai import OpenAI

from unified_pipeline.config import calculate_cost
from unified_pipeline.llm.retry import _call_with_retry, _client_init_lock, _get_llm_timeout_seconds

# Lazy-initialized OpenAI client (NOT created at import time per Pitfall 2)
_openai_client = None


def _get_openai_client():
    """Get or create the OpenAI client (lazy initialization)."""
    global _openai_client
    if _openai_client is None:
        with _client_init_lock:
            if _openai_client is None:
                # Bound every request so a non-responsive endpoint raises
                # APITimeoutError (retried by _call_with_retry, then surfaced)
                # instead of blocking forever. Reads OPENAI_API_KEY from the env.
                _openai_client = OpenAI(timeout=_get_llm_timeout_seconds())
    return _openai_client


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


def _handle_openai(messages: list, response_format, cfg: dict) -> dict:
    """Dispatch one OpenAI call and normalize the response."""
    response, api_seconds = _call_with_retry(
        lambda: _call_openai(cfg["model"], messages, cfg["temperature"],
                             response_format, cfg["max_tokens"],
                             **cfg["extra_kwargs"]),
        retry_count=cfg["retry_count"],
        cancel_check=cfg.get("cancel_check"),
    )

    usage = response.usage
    return {
        "content": response.choices[0].message.content,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost": calculate_cost(
            usage.prompt_tokens,
            usage.completion_tokens,
            model=cfg["model"],
            provider="openai",
        ),
        "model": cfg["model"],
        "provider": "openai",
        "finish_reason": response.choices[0].finish_reason,
        "latency_ms": int(api_seconds * 1000),
    }
