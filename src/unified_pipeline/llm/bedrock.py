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

from unified_pipeline.config import _normalize_model_id, calculate_cost
from unified_pipeline.llm_provenance import FALLBACK_SERVED_KEY
from unified_pipeline.llm.retry import (
    _call_with_retry,
    _client_init_lock,
    _get_llm_timeout_seconds,
)

logger = logging.getLogger(__name__)

# Bedrock stopReason -> OpenAI finish_reason mapping, covering all 9 values
# Bedrock's Converse API documents (#628). The two malformed_* reasons map to
# "error", which is outside OpenAI's vocabulary; that is safe because nothing
# branches on finish_reason -- it is only logged and stored (decided on #628).
STOP_REASON_MAP = {
    "end_turn": "stop",
    "max_tokens": "length",
    "stop_sequence": "stop",
    "tool_use": "tool_calls",
    "guardrail_intervened": "content_filter",  # was "guard_intervened" -- never matched (#628)
    "content_filtered": "content_filter",
    "model_context_window_exceeded": "length",
    "malformed_model_output": "error",
    "malformed_tool_use": "error",
}


# Converse stopReason for the model's own safety filter (not a guardrail,
# which reports "guardrail_intervened"). Sonnet 5 raises it on some
# infectious-disease CV content that Sonnet 4.6 answers normally (#1174).
CONTENT_FILTERED_STOP_REASON = "content_filtered"

# A call that ends content_filtered on a Sonnet-5-family model is retried on
# these models, in order, until one does not end content_filtered (#1174).
# Sonnet 4.6 first: closest to the primary. Haiku 4.5 second: on the M1-score
# prompt of run QFQLNF both Sonnets ended content_filtered and Haiku 4.5
# answered (end_turn), measured 2026-10-05. Each has a PRICING entry in
# config.py, so each attempt is costed at its own rate. Only the family below
# falls back; every other model and every other stopReason keeps today's
# behaviour.
CONTENT_FILTER_FALLBACK_MODELS: tuple[str, ...] = (
    "us.anthropic.claude-sonnet-4-6",
    "us.anthropic.claude-haiku-4-5-20251001-v1:0",
)
#: The first fallback; kept as a name for the callers and tests that read it.
CONTENT_FILTER_FALLBACK_MODEL = CONTENT_FILTER_FALLBACK_MODELS[0]
CONTENT_FILTER_FALLBACK_MODEL_PREFIX = "anthropic.claude-sonnet-5"


class BedrockToolCallDidNotFireError(RuntimeError):
    """A forced json_schema tool call did not fire: the schema was not
    enforced, so the response cannot be trusted as structured output.

    stop_reason is the Converse stopReason of the call that failed to fire;
    cost is what that call was billed, so a fallback can account for it.
    """

    def __init__(self, message: str, *, stop_reason: str | None = None,
                 cost: float = 0.0) -> None:
        super().__init__(message)
        self.stop_reason = stop_reason
        self.cost = cost


class BedrockEmptyResponseError(RuntimeError):
    """Neither the initial call nor the JSON-repair retry returned any text
    content (#884), so there is nothing to repair or return.

    stop_reason is the FINAL attempt's Converse stopReason; cost is what both
    attempts were billed, so a fallback can account for them (#1174).
    """

    def __init__(self, message: str, *, stop_reason: str | None = None,
                 cost: float = 0.0) -> None:
        super().__init__(message)
        self.stop_reason = stop_reason
        self.cost = cost


class BedrockContentFilteredError(RuntimeError):
    """The last model of the content-filter fallback chain (#1174) also ended
    content_filtered with partial text. Raised instead of returning truncated output a caller would
    only mis-parse as invalid JSON."""

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

# Models that 400 on any sampling parameter ("`temperature` is deprecated for
# this model", probed 2026-09-29) and run adaptive thinking unless it is
# switched off. Thinking adds billed output tokens, so it is disabled
# explicitly. Keyed by the bare id, as in PRICING. Every model outside this set
# gets the request shape it always had.
NO_SAMPLING_PARAMS_MODELS = frozenset({"anthropic.claude-sonnet-5"})

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
                # Explicit connect/read timeouts so a wedged Bedrock call can't
                # block the worker thread indefinitely. botocore is held to ONE
                # attempt (total_max_attempts=1, #632): _call_with_retry is the
                # single retry owner, so a logical call costs retry_count+1 raw
                # requests, not that times botocore's own attempts. Note
                # `max_attempts` would be wrong here -- botocore reads it as a
                # RETRY count and adds one for the initial request; only
                # `total_max_attempts` counts the initial request.
                bedrock_config = Config(
                    connect_timeout=10,
                    read_timeout=_get_llm_timeout_seconds(),
                    retries={"mode": "standard", "total_max_attempts": 1},
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


# A markdown code fence, as _strip_markdown_fences and Claude write it. A line
# that starts with it is a separator, not prose (#1218).
_FENCE_MARKER = "```"

# A self-correcting response holds the answer plus its correction: fewer than
# two complete objects is a plain parse failure, not a self-correction (#1218).
_MIN_OBJECTS_FOR_SELF_CORRECTION = 2


def _is_blank_or_fence(line: str) -> bool:
    """True for an empty line or a markdown fence line (```, ```json)."""
    stripped = line.strip()
    return not stripped or stripped.startswith(_FENCE_MARKER)


def _holds_text(edge: str) -> bool:
    """True when `edge`, the text before the first or after the last object,
    has anything beyond whitespace and markdown fence lines."""
    return not all(_is_blank_or_fence(line) for line in edge.splitlines())


def _is_prose_gap(gap: str) -> bool:
    """True when `gap`, the text between two objects, is a line of prose.

    Each object must sit on its own lines: the rest of the line the earlier
    object ends on, and the start of the line the later one begins on, must be
    blank or a fence. So a comma, a semicolon or a bracket next to an object
    rejects the gap. Between them, every line that is not blank or a fence
    must carry a letter, and at least one such line must exist.
    """
    lines = gap.split("\n")
    if not (_is_blank_or_fence(lines[0]) and _is_blank_or_fence(lines[-1])):
        return False
    text_lines = [line for line in lines[1:-1] if not _is_blank_or_fence(line)]
    return bool(text_lines) and all(
        any(ch.isalpha() for ch in line) for line in text_lines
    )


def _objects_separated_by_prose(text: str) -> list[str] | None:
    """Split `text` into the top-level JSON objects it is made of, or None.

    Returns the objects (as source text, in order) only when the text is
    nothing but complete objects, each on its own lines, with a line of prose
    between each pair, e.g. an answer, a line saying the model noticed a
    mistake, then the corrected answer. Only whitespace and fence lines may
    come before the first object or after the last. Anything else is None, so
    genuinely malformed text stays malformed: an object that is truncated or
    not valid, text around the objects, and objects joined by anything other
    than prose (adjacent, comma- or semicolon-separated, or the elements of an
    array), which may be distinct records rather than a correction.
    """
    start = text.find("{")
    if start == -1 or _holds_text(text[:start]):
        return None
    decoder = json.JSONDecoder()
    objects: list[str] = []
    while True:
        try:
            _, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            return None
        objects.append(text[start:end])
        start = text.find("{", end)
        if start == -1:
            return None if _holds_text(text[end:]) else objects
        if not _is_prose_gap(text[end:start]):
            return None


def _select_final_json_object(content: str | None, response_format: dict | None,
                              stage: str | None = None) -> str | None:
    """Reduce a self-correcting JSON response to its last object (#1218).

    Sonnet 5 sometimes answers, writes a line of prose saying it noticed a
    mistake, then answers again. json.loads rejects that ("Extra data") and
    the one-shot repair re-sends the same prompt, so it repeats the shape.
    The later object is the correction, so it is the one returned.

    Content that already parses, is not a JSON request, or is not text is
    returned unchanged, as is anything _objects_separated_by_prose rejects:
    those still take the repair path.
    """
    if not isinstance(content, str) or _validate_json_response(content, response_format):
        return content
    objects = _objects_separated_by_prose(_strip_markdown_fences(content))
    if objects is None or len(objects) < _MIN_OBJECTS_FOR_SELF_CORRECTION:
        return content
    logger.warning(
        "Bedrock JSON response (stage=%s) held %d objects separated by prose; "
        "using the last and discarding %d.", stage, len(objects), len(objects) - 1,
    )
    return objects[-1]


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
        "inferenceConfig": {},
    }
    if _normalize_model_id(model) in NO_SAMPLING_PARAMS_MODELS:
        call_kwargs["additionalModelRequestFields"] = {"thinking": {"type": "disabled"}}
    else:
        call_kwargs["inferenceConfig"]["temperature"] = float(temperature)
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


def _usage_cost(usage: dict, cache_read_tokens: int, cache_write_tokens: int,
                model: str) -> float:
    """Bedrock cost of one call (or a summed call + repair), by the same
    pricing path _finalize_bedrock_result uses."""
    return calculate_cost(
        usage["inputTokens"], usage["outputTokens"], model=model,
        provider="bedrock", cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
    )


def _finalize_schema_tool_response(response: dict, usage: dict, cache_read_tokens: int,
                                   cache_write_tokens: int, model: str,
                                   api_seconds: float) -> dict:
    """#46 json_schema path: the forced tool's structured `input` IS the
    answer. Re-serialize it so every caller's json.loads(content) keeps
    working, and skip the text-validation/fence-strip of the text path
    (structured tool output is guaranteed valid JSON). Returns before the
    JSON-repair branch, so there is only the one dispatch to account for."""
    stop_reason = response.get("stopReason")
    tool_input = _extract_tool_use_input(response)
    if stop_reason != "tool_use" or tool_input is None:
        # Forced tool call that didn't fire => schema not enforced.
        # Fail loud rather than silently parsing free text.
        raise BedrockToolCallDidNotFireError(
            f"Bedrock forced json_schema tool call did not fire "
            f"(stopReason={stop_reason!r}, tool_input="
            f"{'present' if tool_input is not None else 'missing'})",
            stop_reason=stop_reason,
            cost=_usage_cost(usage, cache_read_tokens, cache_write_tokens, model),
        )
    return _finalize_bedrock_result(
        json.dumps(tool_input), usage, cache_read_tokens, cache_write_tokens,
        STOP_REASON_MAP.get(stop_reason, stop_reason), model,
        int(api_seconds * 1000),
    )


def _call_bedrock_json_repair(messages: list, response_format: dict | None,
                              cfg: dict) -> tuple[dict, float]:
    """Re-send the request once with a stronger JSON hint; return the raw
    Converse response and its API seconds.

    Bedrock Converse enforces strict user/assistant role alternation and
    raises a fatal ValidationException (not in BEDROCK_RETRYABLE_CODES) on
    two consecutive turns of the same role. Every call site in this codebase
    sends a single trailing user turn, so appending a new user turn broke the
    repair path in the common case (#630). The hint is folded into the
    existing trailing user turn instead; a fresh turn is appended only for a
    shape this path doesn't expect (the last turn isn't user -- e.g. a caller
    with a hanging assistant turn). `messages` and its dicts are never
    mutated: the repair works on a shallow copy of the list, and the replaced
    trailing turn is a new dict built with {**last, ...}. A caller holding
    `messages` still sees the original request, not the repair attempt. Content is always a str here:
    _translate_messages already raised NotImplementedError on the first call
    for any list (multimodal) content.

    Goes through _call_with_retry rather than calling _call_bedrock bare:
    this retry is a live Bedrock request like any other, and a transient
    throttle on it should back off instead of raising. _call_with_retry
    acquires _llm_call_semaphore itself, so the call stays bounded without
    nesting the acquire.
    """
    hint = ("Your previous response was not valid JSON. Please respond with "
            "ONLY valid JSON, no markdown fencing or explanation.")
    stronger_messages = list(messages)  # shallow copy
    last = stronger_messages[-1] if stronger_messages else None
    if last is not None and last["role"] == "user":
        stronger_messages[-1] = {**last, "content": f'{last["content"]}\n\n{hint}'}
    else:
        stronger_messages.append({"role": "user", "content": hint})
    return _call_with_retry(
        lambda: _call_bedrock(cfg["model"], stronger_messages, cfg["temperature"],
                              response_format, cfg["max_tokens"],
                              enable_prompt_caching=cfg["enable_prompt_caching"],
                              **cfg["extra_kwargs"]),
        retry_count=cfg["retry_count"],
        cancel_check=cfg.get("cancel_check"),
    )


def _dispatch_bedrock(messages: list, response_format: dict | None, cfg: dict) -> tuple[dict, str | None]:
    """Dispatch one Bedrock call and normalize the response. Returns the
    result and the FINAL attempt's raw Converse stopReason (the result's own
    finish_reason is the lossy mapped form: guardrail_intervened and
    content_filtered both read "content_filter").

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
        return _finalize_schema_tool_response(
            response, usage, cache_read_tokens, cache_write_tokens, model, api_seconds
        ), response.get("stopReason")

    # Extract and normalize Bedrock response (text / json_object path). A
    # guardrail intervention or other provider condition can send back an
    # empty content list with no text block at all (#884) -- read it through
    # the same guarded helper the retry branch below re-reads, rather than
    # indexing content[0] directly.
    # A self-correcting response (answer, prose, corrected answer) is reduced
    # to its last object here, before validation, so it needs no repair call
    # (#1218).
    stage = cfg.get("stage")
    content = _select_final_json_object(
        _extract_text_content(response), response_format, stage)
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
        retry_response, retry_api_seconds = _call_bedrock_json_repair(
            messages, response_format, cfg)
        # This repair call is a second live Bedrock request, so its API time
        # belongs in latency_ms -- as its tokens already do just below.
        # Previously latency_ms was frozen before this branch ran, so the
        # repair call was billed but never timed.
        api_seconds += retry_api_seconds
        retry_content = _select_final_json_object(
            _extract_text_content(retry_response), response_format, stage)
        retry_stop_reason = retry_response.get("stopReason", "end_turn")
        retry_usage = retry_response["usage"]
        retry_cache_read, retry_cache_write = _extract_cache_tokens(retry_usage)
        if content_missing and retry_content is None:
            # Neither call returned any text -- nothing to repair or return.
            # Fail loud with both stopReasons so the caller's per-group
            # except records a real error string instead of an IndexError
            # with no context (#884).
            raise BedrockEmptyResponseError(
                "Bedrock Converse returned no text content on the initial "
                f"call (stopReason={stop_reason!r}) or the retry "
                f"(stopReason={retry_stop_reason!r})",
                stop_reason=retry_stop_reason,
                cost=_usage_cost(
                    {"inputTokens": usage["inputTokens"] + retry_usage["inputTokens"],
                     "outputTokens": usage["outputTokens"] + retry_usage["outputTokens"]},
                    cache_read_tokens + retry_cache_read,
                    cache_write_tokens + retry_cache_write, model),
            )
        # If the retry also came back empty but the initial call had
        # (invalid) content, keep the initial content -- let downstream
        # handle it per D-05, same as the pre-#884 "still invalid" path.
        content = retry_content if retry_content is not None else content
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
    ), stop_reason


def _content_filter_fallback_applies(model: str) -> bool:
    """True for a Sonnet-5-family primary model: the only family that falls
    back on content_filtered (#1174)."""
    return _normalize_model_id(model).startswith(CONTENT_FILTER_FALLBACK_MODEL_PREFIX)


def _call_on_content_filter_fallback(messages: list, response_format: dict | None, cfg: dict,
                                     first_attempt_cost: float) -> dict:
    """Walk CONTENT_FILTER_FALLBACK_MODELS in order until one does not end
    content_filtered (#1174). Calls _dispatch_bedrock, never _handle_bedrock,
    so it cannot recurse: each fallback model is tried at most once per
    logical call.

    Every filtered attempt was billed, so all of their costs are added to the
    result that serves, and FALLBACK_SERVED_KEY names the model that served.
    The last model ending content_filtered raises; any other failure of a
    fallback call propagates at once, as it would for a primary call, without
    trying the next model.
    """
    stage = cfg.get("stage")
    filtered_on = cfg["model"]
    billed = first_attempt_cost
    last_hop = len(CONTENT_FILTER_FALLBACK_MODELS) - 1
    for hop, fallback in enumerate(CONTENT_FILTER_FALLBACK_MODELS):
        logger.warning(
            "Bedrock call (stage=%s) ended %s on %s; retrying on fallback %s (%d of %d).",
            stage, CONTENT_FILTERED_STOP_REASON, filtered_on, fallback,
            hop + 1, len(CONTENT_FILTER_FALLBACK_MODELS),
        )
        try:
            result, stop_reason = _dispatch_bedrock(
                messages, response_format, {**cfg, "model": fallback})
        except (BedrockEmptyResponseError, BedrockToolCallDidNotFireError) as e:
            if e.stop_reason != CONTENT_FILTERED_STOP_REASON:
                raise
            if hop == last_hop:
                _log_content_filter_give_up(stage, fallback)
                raise
            billed += e.cost
            filtered_on = fallback
            continue
        if stop_reason != CONTENT_FILTERED_STOP_REASON:
            result["cost"] += billed
            result[FALLBACK_SERVED_KEY] = fallback
            return result
        billed += result["cost"]
        filtered_on = fallback
    _log_content_filter_give_up(stage, filtered_on)
    raise BedrockContentFilteredError(
        f"Bedrock call ended stopReason={CONTENT_FILTERED_STOP_REASON!r} on "
        f"{cfg['model']} and on every fallback {', '.join(CONTENT_FILTER_FALLBACK_MODELS)}")


def _log_content_filter_give_up(stage: str | None, fallback: str) -> None:
    """The last fallback also ended content_filtered. The exception that
    follows is the record; this line names the stage and the model."""
    logger.warning(
        "Bedrock call (stage=%s) was also %s on the last fallback %s; giving up.",
        stage, CONTENT_FILTERED_STOP_REASON, fallback)


def _handle_bedrock(messages: list, response_format: dict | None, cfg: dict) -> dict:
    """Dispatch one Bedrock call; if a Sonnet-5-family model ends it
    content_filtered, retry it down CONTENT_FILTER_FALLBACK_MODELS (#1174).
    Any other model or stopReason is returned or raised exactly as
    _dispatch_bedrock produced it."""
    eligible = _content_filter_fallback_applies(cfg["model"])
    try:
        result, stop_reason = _dispatch_bedrock(messages, response_format, cfg)
    except (BedrockEmptyResponseError, BedrockToolCallDidNotFireError) as e:
        if not (eligible and e.stop_reason == CONTENT_FILTERED_STOP_REASON):
            raise
        return _call_on_content_filter_fallback(messages, response_format, cfg, e.cost)
    if eligible and stop_reason == CONTENT_FILTERED_STOP_REASON:
        return _call_on_content_filter_fallback(
            messages, response_format, cfg, result["cost"])
    return result
