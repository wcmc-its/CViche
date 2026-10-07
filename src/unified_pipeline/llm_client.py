"""
Centralized LLM client for CV parsing pipeline.

Provides call_llm() as the single entry point for all LLM API calls.
Pipeline stages should use this instead of direct SDK calls.

Supports:
- Config-driven model selection (via llm_config.yaml)
- Normalized response format (same dict shape across call sites)
- Built-in retry with exponential backoff on transient errors
- Automatic cost calculation per call
- Lazy client initialization (no import-time side effects)
- AWS Bedrock, the only supported provider (#953)

Usage:
    from unified_pipeline.llm_client import call_llm

    result = call_llm(
        stage="stage_3b",
        messages=[{"role": "user", "content": "Classify this entry..."}],
        response_format={"type": "json_object"},
    )
    print(result["content"])   # LLM response text
    print(result["cost"])      # Cost in USD
    print(result["provider"])  # "bedrock"

The provider-specific call/parse logic lives in unified_pipeline.llm
(bedrock.py, retry.py, #496) -- this module is the thin facade every
pipeline stage imports, plus the config resolution and cost/provenance
tracking that don't belong to the provider adapter. A handful of private
names are re-imported here (not called locally) solely because
web_interface/backend/tests/test_llm_client.py still reaches for them at
this path -- see that file before renaming or dropping any of them.
"""

import threading
import time
from collections import Counter
from dataclasses import dataclass, field

from unified_pipeline.config import get_stage_config
from unified_pipeline.core.prompt_logger import (
    log_prompt_before_call,
    log_prompt_response,
)
from unified_pipeline.llm.bedrock import (
    DEFAULT_MAX_TOKENS,
    _handle_bedrock,
    _schema_tool_config,
    _strip_markdown_fences,
    _translate_messages,
)
from unified_pipeline.llm.retry import (
    _call_with_retry,
    _get_llm_timeout_seconds,
    _get_max_concurrent_llm_calls,
    _llm_call_semaphore,
)

# `import time` is otherwise unused here: it exists so
# `unified_pipeline.llm_client.time` still resolves for
# test_llm_client.py's `mod.time` / `patch("...llm_client.time.sleep")`
# references. The real time.sleep() call lives in llm.retry now, but since
# `time` is a single shared module object, patching .sleep through either
# name patches the same global attribute.


@dataclass
class LlmUsage:
    """Running cost and token totals for the call_llm results a caller feeds it.

    Stages that keep no cost of their own (1a's segmentation calls, stage 6)
    take one of these and ``add`` every call_llm result, so the driver reports
    what the client actually priced instead of an estimate (#1177). Thread-safe:
    stage code may call from pool threads.
    """

    cost: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def token_totals(self) -> dict:
        """The token counts under the key names stage metadata uses."""
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
        }

    def add(self, llm_result: dict) -> None:
        """Fold one call_llm result into the totals."""
        with self._lock:
            self.cost += llm_result.get("cost", 0.0)
            self.prompt_tokens += llm_result.get("prompt_tokens", 0)
            self.completion_tokens += llm_result.get("completion_tokens", 0)
            self.cache_read_tokens += llm_result.get("cache_read_tokens", 0)
            self.cache_write_tokens += llm_result.get("cache_write_tokens", 0)


# Keys call_llm consumes itself; everything else in **kwargs is forwarded to the
# provider SDK untouched.
_EXPLICIT_KWARGS = frozenset({
    "provider", "model", "temperature", "max_tokens", "retry_count", "stage",
    "enable_prompt_caching", "cancel_check",
})


def _resolve_call_config(stage: str, kwargs: dict) -> dict:
    """Merge the stage's YAML config with per-call kwarg overrides."""
    config = get_stage_config(stage)
    return {
        # For provider-layer log lines (e.g. the #1174 content-filter fallback).
        "stage": stage,
        "provider": kwargs.get("provider", config["provider"]),
        "model": kwargs.get("model", config["model"]),
        "temperature": kwargs.get("temperature", config["temperature"]),
        "max_tokens": kwargs.get("max_tokens", config["max_tokens"]),
        "retry_count": kwargs.get("retry_count", config["retry_count"]),
        "enable_prompt_caching": kwargs.get(
            "enable_prompt_caching", config.get("enable_prompt_caching", False)
        ),
        # Raises to cancel; never returns True. Forwarded to
        # llm.retry._call_with_retry so a cancel fires between retry
        # attempts, not just before the call is first dispatched.
        "cancel_check": kwargs.get("cancel_check"),
        "extra_kwargs": {k: v for k, v in kwargs.items() if k not in _EXPLICIT_KWARGS},
    }


_PROVIDER_HANDLERS = {
    "bedrock": _handle_bedrock,
}


# Which models actually served this process's calls, counted at call_llm's
# single exit point.
#
# run_full_pipeline stamped a hardcoded "Model: gpt-5.1" on every run while the
# work ran on Bedrock Sonnet/Haiku (#444). The deeper problem is that there is
# no single model to print: llm_config.yaml resolves per stage, and stage_3b is
# deliberately on Haiku for accuracy and cost. Counting what was actually called
# is a measurement rather than a second assertion that can drift from reality
# the way the first one did -- it is also the only variant immune to a stale
# deployment CVICHE_LLM_MODEL and to the model= kwarg in prompt_ab_tester.
#
# Process-local and never reset: a CLI run is one pipeline. The web backend
# runs many pipelines per process, so it must not read this as per-run.
_MODELS_USED = Counter()
_MODELS_USED_LOCK = threading.Lock()


def models_used() -> dict:
    """Model id -> completed calls, for this process. Empty before any call."""
    with _MODELS_USED_LOCK:
        return dict(_MODELS_USED)


def format_models_used() -> str:
    """One-line provenance summary, busiest model first."""
    counts = models_used()
    if not counts:
        return "none (no LLM calls recorded)"
    return ", ".join(
        f"{model} ({n} call{'' if n == 1 else 's'})"
        for model, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


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
        - provider (str): Provider used ("bedrock")
        - finish_reason (str): Why generation stopped
        - latency_ms (int): API response time in milliseconds -- the time the
          provider call itself took. Retry backoff sleeps, failed attempts and
          time queued on the concurrency semaphore are excluded by design, so
          this is safe to aggregate in cost/perf dashboards (#274). On the
          Bedrock JSON-repair path it is the sum of both live calls.

    Raises:
        ValueError: If provider is not supported
        botocore.exceptions.ClientError: On non-retryable Bedrock errors or
            exhausted retries
    """
    cfg = _resolve_call_config(stage, kwargs)

    handler = _PROVIDER_HANDLERS.get(cfg["provider"])
    if handler is None:
        # Before log_prompt_before_call: a provider we can't dispatch to never
        # reaches an API, so logging a prompt for it would leave an orphan entry
        # that no response ever closes.
        raise ValueError(
            f"Unsupported provider: {cfg['provider']}. "
            f"Supported providers: {', '.join(sorted(_PROVIDER_HANDLERS))}."
        )

    log_id = log_prompt_before_call(
        messages=messages,
        model=cfg["model"],
        purpose=stage,
        temperature=cfg["temperature"],
        response_format=response_format,
        max_tokens=cfg["max_tokens"],
        context={"provider": cfg["provider"]},
        caller_file="llm_client.py",
    )

    result = handler(messages, response_format, cfg)

    # Single exit point. Previously this block was copy-pasted into all three
    # return branches, so a change to the logging signature had to land in three
    # places and missing one silently dropped the log (#273).
    log_prompt_response(
        log_id=log_id,
        response=result,
        purpose=stage,
        elapsed_time=result["latency_ms"] / 1000.0,
    )
    # Record what actually served the call, not what was configured (#444).
    with _MODELS_USED_LOCK:
        _MODELS_USED[result.get("model") or cfg["model"]] += 1
    return result
