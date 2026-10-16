#!/usr/bin/env python3
"""
Prompt Logger - Captures EXACT prompts sent to LLMs

This module logs every LLM API call before it's made, capturing:
- The complete messages array (system + user prompts)
- All parameters (model, temperature, response_format, etc.)
- Metadata (timestamp, caller, purpose)

Usage:
    from prompt_logger import log_prompt_before_call

    # Before OpenAI API call
    log_id = log_prompt_before_call(
        messages=messages,
        model="gpt-4o-mini",
        temperature=0,
        purpose="taxonomy_mapping",
        context={"cv_id": "2090", "section": "education"}
    )

    # Then make the API call
    response = client.chat.completions.create(...)
"""

import contextvars
import hashlib
import json
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, TextIO

from unified_pipeline.llm_provenance import (
    FALLBACK_SERVED_KEY,
    PROMPT_LOG_RESPONSE_SUFFIX,
)

# Default prompt log directory - use absolute path based on this module's location
# This ensures logs go to src/unified_pipeline/prompt_logs regardless of cwd.
# Created on first write by _log_dir(), not here: an unwritable or unmounted
# PROMPT_LOG_DIR must fail the log write (which never fails the LLM call), not
# the import of every module that reaches llm_client (#1911).
_DEFAULT_PROMPT_LOG_DIR = Path(__file__).parent.parent / "prompt_logs"
PROMPT_LOG_DIR = Path(os.getenv("PROMPT_LOG_DIR", str(_DEFAULT_PROMPT_LOG_DIR)))

# The transcripts carry verbatim CV text. Owner-only modes, set at creation so
# they do not depend on the process umask (#1911). The web backend and worker
# run as UID 1000 (Dockerfile USER cviche) and read the files back as the same
# user, so nothing else needs access.
_PRIVATE_DIR_MODE = 0o700
_PRIVATE_FILE_MODE = 0o600

# log_id is 12 hex chars: the filename shape prompt_log_service.py and the
# replay/audit scripts parse ({ts}_{purpose}_{12 hex}[_RESPONSE].json).
_LOG_ID_BYTES = 6

# Per-run subdirectory of PROMPT_LOG_DIR. With CVICHE_MAX_CONCURRENT_RUNS=3,
# up to three runs share this interpreter and this directory; writing every
# run's transcripts into the same flat directory let one run's file-by-mtime
# upload pick up another run's verbatim CV text (#580). The web backend sets
# this once per run via set_current_run_id(); unset (CLI / script usage)
# keeps the original flat layout.
_current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "prompt_logger_current_run_id", default=None
)

# generate_run_id()'s charset (web_interface/backend/app/api/upload.py --
# secrets.token_urlsafe(4)[:6].upper()) and the runs.id column (String(10)).
# Enforced here because run_id can arrive from a request path, and
# PROMPT_LOG_DIR / run_id below would otherwise accept a path-traversal
# payload like "../../etc" (review on #586).
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,10}$")


def set_current_run_id(run_id: str | None) -> contextvars.Token:
    """Scope this task's prompt-log writes to PROMPT_LOG_DIR/<run_id>.

    Must arrive as an argument, not a reach into the web backend's ``app.`` --
    this module is in src/unified_pipeline/, which per CODING_STANDARDS §1.4
    may not import it. Pass None to go back to the shared directory.

    Returns the ContextVar token; pass it to reset_current_run_id() when the
    scope ends so a reused worker thread/task can't inherit this run's id.
    """
    if run_id is not None and not _RUN_ID_RE.match(run_id):
        raise ValueError(f"invalid run_id for prompt log scoping: {run_id!r}")
    return _current_run_id.set(run_id)


def reset_current_run_id(token: contextvars.Token) -> None:
    """Undo a set_current_run_id() call, restoring whatever was active before it."""
    _current_run_id.reset(token)


def _log_dir() -> Path:
    """Directory to write into: PROMPT_LOG_DIR, or PROMPT_LOG_DIR/<run_id>
    if set_current_run_id() was called on this task."""
    run_id = _current_run_id.get()
    d = (PROMPT_LOG_DIR / run_id) if run_id else PROMPT_LOG_DIR
    d.mkdir(mode=_PRIVATE_DIR_MODE, exist_ok=True, parents=True)
    return d


def _open_private(path: Path) -> TextIO:
    """Open a new log file for writing, owner-only (0600). O_EXCL: a log file
    is never truncated by a later call (#1911); the random log_id makes a
    name collision a write failure, not a silent overwrite."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, _PRIVATE_FILE_MODE)
    return open(fd, "w", encoding="utf-8")


def _as_text(content: object) -> str:
    """A message's content for the readable transcript. Structured content (a
    list of blocks) is shown as JSON rather than failing the write."""
    if isinstance(content, str):
        return content
    return json.dumps(content, indent=2, ensure_ascii=False, default=str)


def _extract_prompts(messages: list[dict[str, Any]]) -> dict[str, Any]:
    """The system, last user and assistant messages, for easy reading."""
    extracted = {"system_prompt": None, "user_prompt": None, "assistant_prompts": []}
    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if role == "system":
            extracted["system_prompt"] = content
        elif role == "user":
            extracted["user_prompt"] = content
        elif role == "assistant":
            extracted["assistant_prompts"].append(content)
    return extracted


def _write_section(f: TextIO, title: str, body: str) -> None:
    f.write("\n" + "=" * 80 + "\n")
    f.write(title + "\n")
    f.write("=" * 80 + "\n")
    f.write(body + "\n")


def _write_readable(path: Path, record: dict[str, Any]) -> None:
    """The human-readable .txt copy of *record* (the one the web viewer serves)."""
    params = record["api_parameters"]
    extracted = record["extracted_prompts"]
    with _open_private(path) as f:
        f.write("=" * 80 + "\n")
        f.write(f"PROMPT LOG - {record['purpose']}\n")
        f.write("=" * 80 + "\n")
        f.write(f"Timestamp: {record['timestamp']}\n")
        f.write(f"Log ID: {record['log_id']}\n")
        f.write(f"Model: {params['model']}\n")
        f.write(f"Temperature: {params['temperature']}\n")
        if record["context"]:
            f.write(f"Context: {json.dumps(record['context'], indent=2, default=str)}\n")
        _write_section(f, "SYSTEM PROMPT:", _as_text(extracted["system_prompt"] or "(none)"))
        _write_section(f, "USER PROMPT:", _as_text(extracted["user_prompt"] or "(none)"))
        if extracted["assistant_prompts"]:
            body = "".join(f"\n--- Assistant Message {i} ---\n{_as_text(ap)}\n"
                           for i, ap in enumerate(extracted["assistant_prompts"], 1))
            _write_section(f, "ASSISTANT PROMPTS:", body.removesuffix("\n"))
        _write_section(f, "FULL API CALL PARAMETERS:", json.dumps(params, indent=2, default=str))
        if params["response_format"]:
            _write_section(f, "RESPONSE FORMAT (Structured Outputs Schema):",
                           json.dumps(params["response_format"], indent=2, default=str))


def log_prompt_before_call(
    messages: list[dict[str, Any]],
    model: str,
    purpose: str,
    temperature: float | None = None,
    response_format: dict | None = None,
    max_tokens: int | None = None,
    context: dict[str, Any] | None = None,
    caller_file: str | None = None,
    extra_parameters: dict[str, Any] | None = None,
) -> str:
    """
    Log the EXACT prompt before sending to LLM API.

    Args:
        messages: The messages array sent to the provider (system + user
            prompts); a message's content may be a string or structured blocks
        model: Model name (e.g., "gpt-4o-mini")
        purpose: What this call is for (e.g., "taxonomy_mapping", "publication_extraction")
        temperature: Temperature parameter
        response_format: Response format (for structured outputs)
        max_tokens: Max tokens parameter
        context: Additional context (cv_id, section, etc.)
        caller_file: Name of the file making the call
        extra_parameters: Any further call options the provider receives
            (prompt caching, retry count, pass-through kwargs), recorded
            under api_parameters next to the four above

    Returns:
        Log ID: a random 12-hex identifier for this call, unique per call even
        when the messages repeat. Pass it to log_prompt_response(). Returned
        even when the log write fails -- logging never fails the call (#1911).
    """
    log_id = secrets.token_hex(_LOG_ID_BYTES)
    try:
        timestamp = datetime.now()
        prompt_content = json.dumps(messages, sort_keys=True, default=str)
        prompt_record = {
            "log_id": log_id,
            # The old content hash, kept for spotting repeated prompts; log_id
            # itself no longer depends on content, so repeats can't collide.
            "prompt_hash": hashlib.md5(prompt_content.encode()).hexdigest()[:12],
            "timestamp": timestamp.isoformat(),
            "purpose": purpose,
            "caller_file": caller_file,
            "context": context or {},
            "api_parameters": {
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "response_format": response_format,
                **(extra_parameters or {}),
            },
            "messages": messages,
            "character_count": len(prompt_content),
            "message_count": len(messages),
            "extracted_prompts": _extract_prompts(messages),
        }

        # Format: YYYY-MM-DD_HH-MM-SS_{purpose}_{log_id}.json
        stem = f"{timestamp.strftime('%Y-%m-%d_%H-%M-%S')}_{purpose}_{log_id}"
        log_dir = _log_dir()
        log_path = log_dir / f"{stem}.json"
        with _open_private(log_path) as f:
            json.dump(prompt_record, f, indent=2, ensure_ascii=False, default=str)
        _write_readable(log_dir / f"{stem}_READABLE.txt", prompt_record)

        print(f"📝 Prompt logged: {log_path.name}")

    except Exception as e:
        # Don't fail the actual API call if logging fails
        print(f"⚠️  Failed to log prompt: {e}")

    return log_id


def log_prompt_response(
    log_id: str,
    response: Any,
    purpose: str,
    elapsed_time: float | None = None
) -> None:
    """
    Log the response from the LLM (optional, for completeness).

    Args:
        log_id: The log ID from log_prompt_before_call
        response: The OpenAI API response object
        purpose: Same purpose string used in log_prompt_before_call
        elapsed_time: Time taken for the API call in seconds
    """
    timestamp = datetime.now()

    # Extract response data
    response_record = {
        "log_id": log_id,
        "timestamp": timestamp.isoformat(),
        "purpose": purpose,
        "elapsed_time_seconds": elapsed_time,
        "response": {}
    }

    try:
        # Handle different response types
        if isinstance(response, dict):
            # Handle call_llm() normalized response dict
            response_record["response"]["choices"] = [{
                "index": 0,
                "message": {"role": "assistant", "content": response.get("content", "")},
                "finish_reason": response.get("finish_reason", "")
            }]
            response_record["response"]["usage"] = {
                "prompt_tokens": response.get("prompt_tokens", 0),
                "completion_tokens": response.get("completion_tokens", 0),
                "total_tokens": response.get("total_tokens", 0),
                # Prompt-cache breakdown -- without these, caching is invisible
                # in the logs and its effectiveness can't be verified.
                "cache_read_tokens": response.get("cache_read_tokens", 0),
                "cache_write_tokens": response.get("cache_write_tokens", 0),
            }
            response_record["response"]["cost"] = response.get("cost", 0.0)
            response_record["response"]["model"] = response.get("model", "")
            response_record["response"]["provider"] = response.get("provider", "")
            if response.get(FALLBACK_SERVED_KEY):
                response_record["response"][FALLBACK_SERVED_KEY] = response[FALLBACK_SERVED_KEY]
        else:
            # Handle OpenAI SDK response objects (exception files)
            if hasattr(response, 'choices'):
                response_record["response"]["choices"] = [
                    {
                        "index": choice.index,
                        "message": {
                            "role": choice.message.role,
                            "content": choice.message.content
                        },
                        "finish_reason": choice.finish_reason
                    }
                    for choice in response.choices
                ]

            if hasattr(response, 'usage'):
                response_record["response"]["usage"] = {
                    "prompt_tokens": response.usage.prompt_tokens,
                    "completion_tokens": response.usage.completion_tokens,
                    "total_tokens": response.usage.total_tokens
                }

            if hasattr(response, 'model'):
                response_record["response"]["model"] = response.model

        # Save response
        filename = f"{timestamp.strftime('%Y-%m-%d_%H-%M-%S')}_{purpose}_{log_id}{PROMPT_LOG_RESPONSE_SUFFIX}"
        log_path = _log_dir() / filename

        with _open_private(log_path) as f:
            json.dump(response_record, f, indent=2, ensure_ascii=False, default=str)

        print(f"📝 Response logged: {log_path.name}")

    except Exception as e:
        print(f"⚠️  Failed to log response: {e}")


def get_caller_info():
    """Get the file that's calling this function (for automatic caller_file)."""
    try:
        import inspect
        # Limit stack inspection depth to prevent recursion issues
        stack = inspect.stack(context=0)  # context=0 to avoid reading source lines
        if len(stack) > 2:
            frame = stack[2]  # Go up 2 frames to get the actual caller
            return Path(frame.filename).name
        else:
            return "unknown"
    except Exception:
        return "unknown"
