"""Bedrock provider adapter: client, Converse API call, message translation,
schema enforcement, and response normalization.

Split out of llm_client.py (#496). Everything here is Bedrock-only: the
Converse API needs manual message translation, a forced-tool schema-
enforcement path (#46), and occasional markdown-fence cleanup on Claude's
output, none of which the OpenAI adapter needs.
"""

import os
import json
import logging
import re

from unified_pipeline.config import calculate_cost
from unified_pipeline.llm.retry import (
    _call_with_retry,
    _client_init_lock,
    _get_llm_timeout_seconds,
    _get_llm_max_attempts,
)

logger = logging.getLogger(__name__)

# Bedrock stopReason -> OpenAI finish_reason mapping. Bedrock's Converse API
# documents 9 stopReason values (#628); malformed_model_output and
# malformed_tool_use are deliberately left unmapped (pass through raw) --
# normalizing them needs a decision (reviewer suggested "error", outside the
# OpenAI finish_reason vocabulary every caller expects) that hasn't been made.
STOP_REASON_MAP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "guardrail_intervened": "content_filter",  # was "guard_intervened" -- never matched (#628)
    "content_filtered": "content_filter",
    "model_context_window_exceeded": "length",
}

# Hard ceiling for any Bedrock call that reaches _call_bedrock without an
# explicit max_tokens. When `maxTokens` is omitted, Bedrock applies the MODEL's
# own default ceiling -- ~64,000 output tokens for the Claude models in use --
# so an uncapped call that misbehaves (repetition, a model that won't stop, a
# pathological input) can run all the way to ~64K and you are billed for every
# token it actually emits. This floor turns that unbounded tail into a bounded
# one. It is deliberately generous: the largest legitimate output ever observed
# across the prompt_logs is ~8.1K tokens, and the largest explicit per-call cap
# in the codebase is 16K (segmentation / pdf_vision), so 16K never truncates a
# real response while still cutting worst-case spend 4x vs the 64K model default.
# Per-stage caps in llm_config.yaml tighten this further where it is safe to do
# so. This is belt-and-suspenders: it guarantees no path is uncapped regardless
# of call-site or YAML discipline. See
# docs/analysis/HANDOFF-runaway-generation-maxtokens-2026-06-17.md.
DEFAULT_MAX_TOKENS = 16000

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
        with _client_init_lock:
            if _bedrock_client is None:
                import boto3
                from botocore.config import Config
                region = os.environ.get("AWS_DEFAULT_REGION", "us-east-1")
                # Explicit connect/read timeouts + bounded standard retries so a
                # wedged Bedrock call can't block the worker thread indefinitely.
                # botocore's default read_timeout (60s) and retry behavior are left
                # implicit otherwise; here we make them explicit and tunable.
                bedrock_config = Config(
                    connect_timeout=10,
                    read_timeout=_get_llm_timeout_seconds(),
                    retries={"mode": "standard", "max_attempts": _get_llm_max_attempts()},
                )
                _bedrock_client = boto3.client(
                    "bedrock-runtime", region_name=region, config=bedrock_config
                )
    return _bedrock_client


def _wants_json(response_format):
    """True when response_format requests a JSON-shaped response (json_object
    or json_schema). OpenAI distinguishes the two natively; Bedrock has no
    structured-output mode and only honors prompt-level hints, so for the
    Bedrock translation/validation layer both are treated the same."""
    return bool(response_format) and response_format.get("type") in (
        "json_object", "json_schema"
    )


def _schema_tool_config(response_format):
    """Build a Bedrock Converse `toolConfig` that FORCES the model to emit
    output matching an OpenAI-style json_schema response_format.

    OpenAI enforces json_schema server-side; Bedrock's plain Converse call does
    not, so the prompt-hint path returns well-formed-but-off-schema JSON that
    downstream field extraction mis-reads (#46). A forced single-tool call whose
    inputSchema IS the caller's schema constrains the model the same way, and we
    read the structured tool input instead of parsing free text.

    Returns None (caller falls back to the prompt-hint path) when the caller
    didn't request json_schema, or the schema isn't a top-level `object` -- the
    only shape Converse inputSchema accepts. No keyword normalization: this
    corpus's schemas use only object/enum/format/additionalProperties, all of
    which Converse accepts as-is. A future schema with an unsupported keyword
    ($ref, oneOf) makes Bedrock raise ValidationException loudly rather than
    silently degrade -- see the hard-fail in call_llm's bedrock branch.
    # ponytail: no schema normalizer; add one only if a real schema needs it.
    """
    if not (response_format and response_format.get("type") == "json_schema"):
        return None
    js = response_format.get("json_schema") or {}
    schema = js.get("schema")
    if not isinstance(schema, dict) or schema.get("type") != "object":
        return None
    name = js.get("name") or "structured_output"
    # Bedrock's ToolSpecification.name is a required 1-64 char field matching
    # [a-zA-Z0-9_-]+ -- an otherwise-valid OpenAI schema name that violates
    # this only fails once it reaches Bedrock. Catch it here with an
    # actionable message instead (PR #620 review).
    if not (isinstance(name, str) and 1 <= len(name) <= 64
            and re.fullmatch(r"[a-zA-Z0-9_-]+", name)):
        raise ValueError(
            f"Bedrock json_schema name {name!r} must be 1-64 characters and "
            "contain only letters, digits, '_' or '-'."
        )
    return {
        "tools": [{"toolSpec": {
            "name": name,
            "description": js.get("description")
            or "Return the extracted record using exactly this schema.",
            "inputSchema": {"json": schema},
        }}],
        "toolChoice": {"tool": {"name": name}},
    }


def _extract_tool_use_input(response):
    """Return the `input` dict of the first toolUse block in a Converse
    response, or None if the model emitted no tool call (a text-only reply)."""
    content = response.get("output", {}).get("message", {}).get("content", [])
    for block in content:
        if "toolUse" in block:
            return block["toolUse"].get("input")
    return None


def _extract_text_content(response: dict) -> str | None:
    """Return the first text block's text from a Converse response's message
    content list, or None when the list is empty or has no text block.

    A guardrail intervention or other provider-side condition can return an
    empty content list (#884); indexing content[0] directly crashes with an
    unhandled IndexError instead of letting the caller retry or raise a
    clear, attributable error.
    """
    content = response.get("output", {}).get("message", {}).get("content", [])
    for block in content:
        if "text" in block:
            return block["text"]
    return None


def _strip_markdown_fences(text):
    """Remove a surrounding ```json … ``` or ``` … ``` block.

    Claude often wraps JSON in a markdown code fence even when asked not to;
    OpenAI's Structured Outputs never does, so for OpenAI this is a no-op on
    well-formed responses. Only strips when the ENTIRE (stripped) response is
    a single fenced block, so inline fences in legitimate text are preserved.
    """
    if not isinstance(text, str):
        return text
    stripped = text.strip()
    if not (stripped.startswith("```") and stripped.endswith("```")):
        return text
    first_newline = stripped.find("\n")
    if first_newline == -1:
        return text
    return stripped[first_newline + 1 : -3].rstrip()

def _translate_messages(messages, response_format=None, use_schema_tool=False):
    """Translate OpenAI-style messages to Bedrock Converse format.

    Bedrock Converse API separates system messages from conversation messages.
    System messages become the `system` parameter, other messages go in `messages`.
    Content must be wrapped in [{"text": "..."}] format.

    Args:
        messages: OpenAI-style message list [{"role": "...", "content": "..."}]
        response_format: Optional response format dict
        use_schema_tool: True when the schema is being enforced via a forced
            Converse toolConfig (json_schema path). In that case the
            "respond with valid JSON only" prompt hint is suppressed -- it
            contradicts a forced tool call and can confuse the model.

    Returns:
        Tuple of (system_prompts, converse_messages)
    """
    system_prompts = []
    converse_messages = []
    json_hint = _wants_json(response_format) and not use_schema_tool

    for msg in messages:
        if msg["role"] == "system":
            text = msg["content"]
            if not isinstance(text, str):
                # Same constraint as the user/assistant branch below (Bedrock's
                # `text` field must be a str) -- fail loud here too instead of
                # crashing on `text +=` or reaching boto3 with a malformed
                # request (PR #620 review).
                raise NotImplementedError(
                    "Bedrock Converse translation does not support multimodal "
                    "(list) system message content -- see #265.")
            if json_hint:
                text += "\n\nRespond with valid JSON only."
            system_prompts.append({"text": text})
        else:
            text = msg["content"]
            if not isinstance(text, str):
                # Multimodal / list content (e.g. pdf_vision's OpenAI image_url
                # blocks) cannot be wrapped as {"text": <list>}: Bedrock's
                # Converse `text` field must be a str, and images use a different
                # block shape ({"image": {"format", "source": {"bytes"}}}).
                # Wrapping the list silently produced a botocore
                # ParamValidationError deep in the call; fail loud and actionable
                # instead. Real multimodal support is tracked in #265.
                raise NotImplementedError(
                    "Bedrock Converse translation does not support multimodal "
                    f"(list) message content (role={msg['role']!r}). A stage that "
                    "sends image blocks must be pinned to a vision-capable "
                    "provider in llm_config.yaml, or Bedrock image-block "
                    "translation must be implemented -- see #265.")
            converse_messages.append({
                "role": msg["role"],
                "content": [{"text": text}],
            })

    # If JSON format requested but no system message existed, create one
    if json_hint and not system_prompts:
        system_prompts.append({"text": "Respond with valid JSON only."})

    return system_prompts, converse_messages


def _validate_json_response(content, response_format):
    """Check if content is valid JSON when response_format requires it.

    Fences are stripped before parsing so a fence-wrapped-but-otherwise-valid
    response is not treated as invalid (the surrounding Bedrock path then
    avoids a needless second LLM call). Callers receive the stripped content.

    Args:
        content: Response text from LLM
        response_format: The response_format dict (or None)

    Returns:
        True if no validation needed or content is valid JSON, False otherwise
    """
    if not _wants_json(response_format):
        return True
    try:
        json.loads(_strip_markdown_fences(content))
        return True
    except (json.JSONDecodeError, TypeError):
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
        ValueError: max_tokens was given but is not a positive int.
        botocore.exceptions.ClientError: On non-retryable Bedrock errors
    """
    # Reject an invalid max_tokens here rather than letting it reach Bedrock
    # as-is: the API rejects it server-side too, but as an opaque
    # ParamValidationError deep in the boto3 call instead of a clear,
    # attributable error at the call site (#631).
    if max_tokens is not None and (
        isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens <= 0
    ):
        raise ValueError(f"Bedrock max_tokens must be a positive int, got {max_tokens!r}")
    client = _get_bedrock_client()
    tool_config = _schema_tool_config(response_format)
    system_prompts, converse_messages = _translate_messages(
        messages, response_format, use_schema_tool=tool_config is not None
    )

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
    # Always send maxTokens. When the caller (and config) leave it None, fall
    # back to the conservative DEFAULT_MAX_TOKENS floor instead of omitting the
    # field -- omitting it lets Bedrock apply the model's ~64K default ceiling,
    # which is the runaway-generation tail risk this floor exists to bound.
    call_kwargs["inferenceConfig"]["maxTokens"] = (
        max_tokens if max_tokens is not None else DEFAULT_MAX_TOKENS
    )
    # json_schema callers: force a single-tool call whose inputSchema is the
    # caller's schema, so Bedrock constrains the output the way OpenAI does
    # server-side (#46). json_object / non-schema callers keep the prompt-hint
    # path (no toolConfig).
    if tool_config is not None:
        call_kwargs["toolConfig"] = tool_config

    # ClientError (retryable or not) propagates to _call_with_retry as-is --
    # that's where BEDROCK_RETRYABLE_CODES classification actually happens.
    # A try/except here that re-raises unconditionally in both branches was
    # a no-op (PR #620 review); removed rather than kept as dead code.
    return client.converse(**call_kwargs)


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


def _finalize_bedrock_result(content, usage, cache_read_tokens, cache_write_tokens,
                             finish_reason, model, latency_ms):
    """Build the normalized result dict shared by both Bedrock response paths
    (forced-tool json_schema and text / json_object).

    With caching on, Bedrock's `inputTokens` reports ONLY the uncached input
    tokens; the cached portion shows up in cacheRead/cacheWrite. calculate_cost
    prices each bucket separately, and we synthesize totals from the three so
    downstream cost/token tracking still sees the full input regardless of
    caching.
    """
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


def _handle_bedrock(messages: list, response_format, cfg: dict) -> dict:
    """Dispatch one Bedrock call and normalize the response.

    Covers both output shapes: the #46 forced-tool json_schema path, and the
    text/json_object path (with its one-shot JSON repair retry).
    """
    model = cfg["model"]
    response, api_seconds = _call_with_retry(
        lambda: _call_bedrock(model, messages, cfg["temperature"], response_format,
                              cfg["max_tokens"],
                              enable_prompt_caching=cfg["enable_prompt_caching"],
                              **cfg["extra_kwargs"]),
        retry_count=cfg["retry_count"],
        cancel_check=cfg.get("cancel_check"),
    )

    usage = response["usage"]
    cache_read_tokens, cache_write_tokens = _extract_cache_tokens(usage)

    if _schema_tool_config(response_format) is not None:
        # #46 json_schema path: the forced tool's structured `input` IS the
        # answer. Re-serialize it so every caller's json.loads(content)
        # keeps working, and skip the text-validation/fence-strip below
        # (structured tool output is guaranteed valid JSON).
        stop_reason = response.get("stopReason")
        tool_input = _extract_tool_use_input(response)
        if stop_reason != "tool_use" or tool_input is None:
            # Forced tool call that didn't fire => schema not enforced.
            # Fail loud rather than silently parsing free text.
            raise RuntimeError(
                f"Bedrock forced json_schema tool call did not fire "
                f"(stopReason={stop_reason!r}, tool_input="
                f"{'present' if tool_input is not None else 'missing'})"
            )
        # Returns before the JSON-repair branch, so there is only the one
        # dispatch to account for.
        return _finalize_bedrock_result(
            json.dumps(tool_input), usage, cache_read_tokens, cache_write_tokens,
            STOP_REASON_MAP.get(stop_reason, stop_reason), model,
            int(api_seconds * 1000),
        )

    # Extract and normalize Bedrock response (text / json_object path). A
    # guardrail intervention or other provider condition can send back an
    # empty content list with no text block at all (#884) -- read it through
    # the same guarded helper the retry branch below re-reads, rather than
    # indexing content[0] directly.
    content = _extract_text_content(response)
    stop_reason = response.get("stopReason", "end_turn")
    finish_reason = STOP_REASON_MAP.get(stop_reason, stop_reason)
    content_missing = content is None

    # D-05: Validate JSON when response_format was requested. An empty
    # content list is treated the same as invalid JSON -- both need the
    # one-shot repair retry -- regardless of whether JSON was requested,
    # since there is no content to return either way (#884).
    if content_missing:
        logger.warning(
            "Bedrock response had no text content (stopReason=%r, usage=%r). "
            "Retrying with stronger hint...", stop_reason, usage,
        )
    if content_missing or not _validate_json_response(content, response_format):
        if not content_missing:
            logger.warning("Bedrock response is not valid JSON. Retrying with stronger hint...")
        # Retry once with stronger prompt hint. Bedrock Converse enforces
        # strict user/assistant role alternation and raises a fatal
        # ValidationException (not in BEDROCK_RETRYABLE_CODES) on two
        # consecutive turns of the same role. Every call site in this
        # codebase sends a single trailing user turn, so appending a new
        # user turn here broke the repair path in the common case (#630).
        # Fold the hint into the existing trailing user turn instead of
        # adding a new one when the shape allows it; only append a fresh
        # turn as a fallback for a shape this repair path doesn't expect
        # (the last turn isn't user -- e.g. a caller with a hanging
        # assistant turn). Content is always a str by this point:
        # _translate_messages already raised NotImplementedError on the
        # first call for any list (multimodal) content, so a stronger
        # isinstance(last["content"], str) check here can never be False.
        hint = ("Your previous response was not valid JSON. Please respond with "
                "ONLY valid JSON, no markdown fencing or explanation.")
        stronger_messages = list(messages)  # shallow copy
        last = stronger_messages[-1] if stronger_messages else None
        if last is not None and last["role"] == "user":
            stronger_messages[-1] = {**last, "content": f'{last["content"]}\n\n{hint}'}
        else:
            stronger_messages.append({"role": "user", "content": hint})
        # Go through _call_with_retry rather than calling _call_bedrock
        # bare: this retry is a live Bedrock request like any other, and a
        # transient throttle on it should back off instead of raising.
        # _call_with_retry acquires _llm_call_semaphore itself, so this
        # call stays bounded without nesting the acquire.
        retry_response, retry_api_seconds = _call_with_retry(
            lambda: _call_bedrock(model, stronger_messages, cfg["temperature"],
                                  response_format, cfg["max_tokens"],
                                  enable_prompt_caching=cfg["enable_prompt_caching"],
                                  **cfg["extra_kwargs"]),
            retry_count=cfg["retry_count"],
            cancel_check=cfg.get("cancel_check"),
        )
        # This repair call is a second live Bedrock request, so its API time
        # belongs in latency_ms -- as its tokens already do just below.
        # Previously latency_ms was frozen before this branch ran, so the
        # repair call was billed but never timed.
        api_seconds += retry_api_seconds
        retry_content = _extract_text_content(retry_response)
        retry_stop_reason = retry_response.get("stopReason", "end_turn")
        if content_missing and retry_content is None:
            # Neither call returned any text -- nothing to repair or return.
            # Fail loud with both stopReasons so the caller's per-group
            # except records a real error string instead of an IndexError
            # with no context (#884).
            raise RuntimeError(
                "Bedrock Converse returned no text content on the initial "
                f"call (stopReason={stop_reason!r}) or the retry "
                f"(stopReason={retry_stop_reason!r})"
            )
        # If the retry also came back empty but the initial call had
        # (invalid) content, keep the initial content -- let downstream
        # handle it per D-05, same as the pre-#884 "still invalid" path.
        content = retry_content if retry_content is not None else content
        retry_usage = retry_response["usage"]
        retry_cache_read, retry_cache_write = _extract_cache_tokens(retry_usage)
        # Accumulate token usage from retry. No "totalTokens" key here:
        # _finalize_bedrock_result computes its own total from inputTokens/
        # outputTokens/cache below, it never reads this dict's totalTokens
        # (which also wouldn't reflect cache-expanded accounting) -- PR #620
        # review.
        usage = {
            "inputTokens": usage["inputTokens"] + retry_usage["inputTokens"],
            "outputTokens": usage["outputTokens"] + retry_usage["outputTokens"],
        }
        cache_read_tokens += retry_cache_read
        cache_write_tokens += retry_cache_write
        stop_reason = retry_stop_reason
        finish_reason = STOP_REASON_MAP.get(stop_reason, stop_reason)
        # If still invalid, return as-is (let downstream handle it per D-05)

    # Strip a surrounding markdown fence (Claude wraps JSON in ```json…```
    # even when told not to) so callers can json.loads() the content
    # directly. This MUST stay at the handler body level, NOT inside the
    # retry branch above: _validate_json_response strips fences before
    # validating, so a fence-wrapped-but-valid response passes validation
    # and never triggers a retry -- stripping here is the only thing that
    # makes the returned content fence-free for the common case. No-op when
    # no fence is present or no JSON was requested.
    if _wants_json(response_format):
        content = _strip_markdown_fences(content)

    return _finalize_bedrock_result(
        content, usage, cache_read_tokens, cache_write_tokens,
        finish_reason, model,
        # After the JSON-repair branch, so a repair call's API time is included
        # rather than dropped.
        int(api_seconds * 1000),
    )
