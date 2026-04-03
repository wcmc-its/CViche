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

import json
import os
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Any, Optional
import hashlib


# Default prompt log directory - use absolute path based on this module's location
# This ensures logs go to src/unified_pipeline/prompt_logs regardless of cwd
_DEFAULT_PROMPT_LOG_DIR = Path(__file__).parent.parent / "prompt_logs"
PROMPT_LOG_DIR = Path(os.getenv("PROMPT_LOG_DIR", str(_DEFAULT_PROMPT_LOG_DIR)))
PROMPT_LOG_DIR.mkdir(exist_ok=True, parents=True)


def log_prompt_before_call(
    messages: List[Dict[str, str]],
    model: str,
    purpose: str,
    temperature: Optional[float] = None,
    response_format: Optional[Dict] = None,
    max_tokens: Optional[int] = None,
    context: Optional[Dict[str, Any]] = None,
    caller_file: Optional[str] = None
) -> str:
    """
    Log the EXACT prompt before sending to LLM API.

    Args:
        messages: The messages array sent to OpenAI (system + user prompts)
        model: Model name (e.g., "gpt-4o-mini")
        purpose: What this call is for (e.g., "taxonomy_mapping", "publication_extraction")
        temperature: Temperature parameter
        response_format: Response format (for structured outputs)
        max_tokens: Max tokens parameter
        context: Additional context (cv_id, section, etc.)
        caller_file: Name of the file making the call

    Returns:
        Log ID (hash-based identifier for this prompt)
    """
    timestamp = datetime.now()

    # Create a unique ID for this prompt based on content hash
    prompt_content = json.dumps(messages, sort_keys=True)
    log_id = hashlib.md5(prompt_content.encode()).hexdigest()[:12]

    # Build the complete prompt record
    prompt_record = {
        "log_id": log_id,
        "timestamp": timestamp.isoformat(),
        "purpose": purpose,
        "caller_file": caller_file,
        "context": context or {},
        "api_parameters": {
            "model": model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": response_format
        },
        "messages": messages,
        "character_count": len(prompt_content),
        "message_count": len(messages)
    }

    # Also extract just the prompts for easy reading
    extracted_prompts = {
        "system_prompt": None,
        "user_prompt": None,
        "assistant_prompts": []
    }

    for msg in messages:
        role = msg.get("role", "")
        content = msg.get("content", "")

        if role == "system":
            extracted_prompts["system_prompt"] = content
        elif role == "user":
            extracted_prompts["user_prompt"] = content
        elif role == "assistant":
            extracted_prompts["assistant_prompts"].append(content)

    prompt_record["extracted_prompts"] = extracted_prompts

    # Save to file
    # Format: YYYY-MM-DD_HH-MM-SS_{purpose}_{log_id}.json
    filename = f"{timestamp.strftime('%Y-%m-%d_%H-%M-%S')}_{purpose}_{log_id}.json"
    log_path = PROMPT_LOG_DIR / filename

    try:
        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(prompt_record, f, indent=2, ensure_ascii=False)

        # Also save a human-readable version
        readable_filename = f"{timestamp.strftime('%Y-%m-%d_%H-%M-%S')}_{purpose}_{log_id}_READABLE.txt"
        readable_path = PROMPT_LOG_DIR / readable_filename

        with open(readable_path, 'w', encoding='utf-8') as f:
            f.write("=" * 80 + "\n")
            f.write(f"PROMPT LOG - {purpose}\n")
            f.write("=" * 80 + "\n")
            f.write(f"Timestamp: {timestamp.isoformat()}\n")
            f.write(f"Log ID: {log_id}\n")
            f.write(f"Model: {model}\n")
            f.write(f"Temperature: {temperature}\n")
            if context:
                f.write(f"Context: {json.dumps(context, indent=2)}\n")
            f.write("\n" + "=" * 80 + "\n")
            f.write("SYSTEM PROMPT:\n")
            f.write("=" * 80 + "\n")
            f.write((extracted_prompts.get("system_prompt") or "(none)") + "\n")
            f.write("\n" + "=" * 80 + "\n")
            f.write("USER PROMPT:\n")
            f.write("=" * 80 + "\n")
            f.write((extracted_prompts.get("user_prompt") or "(none)") + "\n")
            if extracted_prompts.get("assistant_prompts"):
                f.write("\n" + "=" * 80 + "\n")
                f.write("ASSISTANT PROMPTS:\n")
                f.write("=" * 80 + "\n")
                for i, ap in enumerate(extracted_prompts["assistant_prompts"], 1):
                    f.write(f"\n--- Assistant Message {i} ---\n")
                    f.write(ap + "\n")
            f.write("\n" + "=" * 80 + "\n")
            f.write("FULL API CALL PARAMETERS:\n")
            f.write("=" * 80 + "\n")
            f.write(json.dumps(prompt_record["api_parameters"], indent=2) + "\n")
            if response_format:
                f.write("\n" + "=" * 80 + "\n")
                f.write("RESPONSE FORMAT (Structured Outputs Schema):\n")
                f.write("=" * 80 + "\n")
                f.write(json.dumps(response_format, indent=2) + "\n")

        print(f"📝 Prompt logged: {log_path.name}")

    except Exception as e:
        # Don't fail the actual API call if logging fails
        print(f"⚠️  Failed to log prompt: {e}")

    return log_id


def log_prompt_response(
    log_id: str,
    response: Any,
    purpose: str,
    elapsed_time: Optional[float] = None
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
                "total_tokens": response.get("total_tokens", 0)
            }
            response_record["response"]["model"] = response.get("model", "")
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
        filename = f"{timestamp.strftime('%Y-%m-%d_%H-%M-%S')}_{purpose}_{log_id}_RESPONSE.json"
        log_path = PROMPT_LOG_DIR / filename

        with open(log_path, 'w', encoding='utf-8') as f:
            json.dump(response_record, f, indent=2, ensure_ascii=False)

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
