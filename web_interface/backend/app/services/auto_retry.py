"""Auto-retry classification and configuration for interrupted runs (issue #145).

FLAG-OFF BY DEFAULT. This module is foundation-only scaffolding: it provides the
pure decision logic and config knobs the reaper will consult, but nothing here
launches a retry or writes to the DB. The feature stays inert until
``CVICHE_AUTO_RETRY_ENABLED`` is explicitly set truthy.

Retryable vs terminal error types
----------------------------------
A run can end in error for two fundamentally different reasons:

  * Transient / infrastructure failures (network blip, Bedrock throttling, a
    timed-out LLM call, a mid-run pod recycle). Re-running the same input is
    likely to succeed, so these are RETRYABLE.

  * Deterministic failures (the input file is unparseable, the model returned an
    invalid/over-length response, a token-limit overflow, a file error). The same
    input will fail the same way on a retry -- auto-retrying just re-burns Bedrock
    spend with no chance of success -- so these are TERMINAL.

When in doubt we fail safe toward NOT retrying (unknown error types are treated
as terminal) so an unclassified failure can never silently loop and rack up cost.
The error_type vocabulary mirrors ``Step.error_type`` in app.models.
"""
from typing import Optional

from app.config_loader import get_config

# Config section shared with run_service's CVICHE_STALE_RUN_MINUTES so the
# resilience/reaper knobs all live together.
_CONFIG_SECTION = "llm"

# Transient / infra failures -- safe to resume; re-running the same input is
# likely to succeed.
RETRYABLE_ERROR_TYPES = frozenset({"api_error", "llm_timeout"})

# Deterministic failures -- the same input re-fails the same way, so retrying
# just re-burns Bedrock spend with no chance of success.
TERMINAL_ERROR_TYPES = frozenset({
    "parse_error",
    "invalid_response",
    "token_limit",
    "file_error",
})

# Defaults: feature OFF, and even when on, a small attempt ceiling plus a backoff
# so a persistently failing run cannot loop unbounded.
DEFAULT_MAX_AUTO_RETRIES = 3
DEFAULT_BACKOFF_SECONDS = 30

# Strings parsed as truthy for the on/off flag (case-insensitive).
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def is_retryable_error_type(error_type: Optional[str]) -> bool:
    """True iff *error_type* is a known transient/infra failure worth retrying.

    None, "unknown", and any unrecognized value return False -- we fail safe on
    cost rather than retry an unclassified (possibly deterministic) failure.
    """
    if not error_type:
        return False
    return error_type in RETRYABLE_ERROR_TYPES


def auto_retry_enabled() -> bool:
    """Whether auto-retry is enabled. DEFAULT FALSE (flag-off feature).

    Reads ``CVICHE_AUTO_RETRY_ENABLED`` (env > YAML > default) and parses it
    leniently: "1"/"true"/"yes"/"on" (case-insensitive) are truthy; anything
    else -- including bad values -- leaves the feature OFF.
    """
    value, _ = get_config(_CONFIG_SECTION, "CVICHE_AUTO_RETRY_ENABLED", default=False)
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in _TRUTHY


def max_auto_retries() -> int:
    """Max number of auto-retry attempts. Default DEFAULT_MAX_AUTO_RETRIES (3).

    Reads ``CVICHE_MAX_AUTO_RETRIES``; a missing or non-integer value falls back
    to the default rather than raising.
    """
    try:
        value, _ = get_config(
            _CONFIG_SECTION, "CVICHE_MAX_AUTO_RETRIES", default=DEFAULT_MAX_AUTO_RETRIES
        )
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_AUTO_RETRIES


def auto_retry_backoff_seconds() -> int:
    """Backoff before re-launching a run. Default DEFAULT_BACKOFF_SECONDS (30).

    Reads ``CVICHE_AUTO_RETRY_BACKOFF_SECONDS``; a missing or non-integer value
    falls back to the default rather than raising.
    """
    try:
        value, _ = get_config(
            _CONFIG_SECTION,
            "CVICHE_AUTO_RETRY_BACKOFF_SECONDS",
            default=DEFAULT_BACKOFF_SECONDS,
        )
        return int(value)
    except (TypeError, ValueError):
        return DEFAULT_BACKOFF_SECONDS


def eligible_for_resume(run, *, last_error_type: Optional[str], is_interruption: bool) -> bool:
    """Pure decision: should this run be auto-retried?

    A run is eligible iff ALL of:
      * the feature flag is enabled (else always False), and
      * it has not exhausted the retry budget (``run.attempt_count`` <= cap), and
      * the failure is retryable -- either an inherently-retryable infra
        interruption (``is_interruption``, e.g. a pod-crash mid-run) OR a
        transient ``last_error_type``.

    No DB writes, no launching -- the caller (reaper) acts on the decision.
    """
    if not auto_retry_enabled():
        return False
    if run.attempt_count > max_auto_retries():
        return False
    return is_interruption or is_retryable_error_type(last_error_type)
