"""Shared call infrastructure for the OpenAI and Bedrock provider adapters.

Owns the pieces both providers depend on: retry/backoff, the per-pod
in-flight-call semaphore, the client-init lock, and the LLM tuning knobs
(timeout, max attempts, max concurrency) read from llm_config.yaml. Split out
of llm_client.py (#496) -- this is where it has to live: llm/openai.py and
llm/bedrock.py both depend on it, so it cannot live in either provider file
without creating a cross-provider dependency, and llm_client.py (the facade)
cannot own it either without an import cycle (llm_client imports the provider
handlers, which need this module's state).
"""

import sys
import time
import random
import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar
from openai import (
    RateLimitError,
    APITimeoutError,
    APIConnectionError,
    InternalServerError,
)

# Calculate the absolute path to web_interface/backend so `app.config_loader`
# is importable from here (src/unified_pipeline/llm/ -> project root -> backend).
CURRENT_FILE = Path(__file__).resolve()
PROJECT_ROOT = CURRENT_FILE.parent.parent.parent.parent
BACKEND_ROOT = PROJECT_ROOT / "web_interface" / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.config_loader import get_config


logger = logging.getLogger(__name__)

# Bedrock error codes that should trigger retry
BEDROCK_RETRYABLE_CODES = frozenset({
    "ThrottlingException",
    "ModelTimeoutException",
    "InternalServerException",
    "ServiceUnavailableException",
})

# boto3 is a hard pin in requirements.txt (llm/bedrock.py already imports it
# unconditionally), so botocore -- one of its own transitive dependencies --
# is always present in every supported environment. The previous
# try/except ImportError fallback here could never actually trigger and was
# dead defensive code (PR #620 review).
from botocore.exceptions import ClientError as _BotoClientError

RETRYABLE_ERRORS = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError, _BotoClientError)

T = TypeVar("T")


# Per-pod ceiling on concurrent in-flight LLM calls. A backstop against fanning
# out too many simultaneous Bedrock/OpenAI requests from one pod -- e.g. if the
# per-run admission cap (CVICHE_MAX_CONCURRENT_RUNS) is raised, or a stage ever
# parallelizes its calls. The live pipeline runs stages sequentially and runs
# are admission-capped, so in-flight calls are already few; this default is
# generous headroom rather than a bottleneck. Read once at import (a
# deploy-time knob), since BoundedSemaphore is sized at construction.
def _get_llm_config_int(key: str, default: int, min_value: int = 1) -> int:
    """Read an int LLM knob, falling back to default if unset, unparseable,
    or below min_value (min_value itself is kept -- e.g. a concurrency count
    of exactly 1 is valid)."""
    try:
        value, _ = get_config("llm", key, default=default)
        result = int(value)
    except (TypeError, ValueError):
        return default
    return result if result >= min_value else default


def _get_llm_config_float(key: str, default: float, min_value: float = 0.0) -> float:
    """Read a float LLM knob, falling back to default if unset, unparseable,
    or at/below min_value (min_value itself is EXCLUDED here, unlike the int
    sibling above -- deliberately: min_value defaults to 0.0 for this
    function's timeout/duration callers, and a 0.0 timeout is meaningless,
    not a valid edge case to keep)."""
    try:
        value, _ = get_config("llm", key, default=default)
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if result > min_value else default


def _get_max_concurrent_llm_calls() -> int:
    return _get_llm_config_int("CVICHE_MAX_CONCURRENT_LLM_CALLS", default=8)


_llm_call_semaphore = threading.BoundedSemaphore(_get_max_concurrent_llm_calls())
# Sized once here, at import. Changing CVICHE_MAX_CONCURRENT_LLM_CALLS on a
# running pod has no effect -- BoundedSemaphore's capacity is fixed at
# construction -- it takes a pod restart (PR #620 review).


def _get_llm_timeout_seconds() -> float:
    """Per-call response timeout, in seconds.

    Without an explicit timeout a wedged provider call blocks the worker
    thread the pipeline stage runs in indefinitely -- nothing raises, the
    run stays "running", and the UI elapsed timer counts up forever (the
    reported Stage 4 hang). A bounded timeout turns that hang into a normal
    exception that propagates to the orchestrator, fails the run, and
    surfaces to the user. Generous by default so legitimately slow calls
    are not clipped; tune via CVICHE_LLM_TIMEOUT_SECONDS.
    """
    return _get_llm_config_float("CVICHE_LLM_TIMEOUT_SECONDS", default=180.0)


def _get_llm_max_attempts() -> int:
    """Total botocore attempts (initial + retries) for Bedrock calls.

    botocore's standard retry mode retries connect/read timeouts and
    throttling up to this many attempts, then raises -- so a persistently
    wedged Bedrock endpoint fails deterministically instead of hanging.
    Tune via CVICHE_LLM_MAX_ATTEMPTS.
    """
    return _get_llm_config_int("CVICHE_LLM_MAX_ATTEMPTS", default=3)


# Guards construction of the module-level OpenAI and Bedrock clients
# (llm/openai.py, llm/bedrock.py). call_llm runs on several threads at once
# (see _llm_call_semaphore), so two threads can both observe `_client is
# None`. For OpenAI that is merely wasteful -- the loser's client is
# discarded and both are valid. For Bedrock it is not safe: boto3 builds
# clients off the shared default session, and only *use* of an existing
# client is thread-safe, not its creation.
_client_init_lock = threading.Lock()


def _call_with_retry(
    call_fn: Callable[[], T],
    retry_count: int = 3,
    cancel_check: Callable[[], None] | None = None,
) -> tuple[T, float]:
    """Call function with exponential backoff on transient errors.

    cancel_check: raises to cancel; checked between attempts, never
    mid-call. Called once per retry, after that attempt's backoff sleep and
    before the next call_fn() -- so a cancel raised there propagates out of
    the retry loop unchanged instead of waiting for an in-flight call (which
    cannot be interrupted) to finish. None (the default) is a no-op, so a
    caller that never cancels does not need to pass it.

    Args:
        call_fn: Zero-argument callable that makes the API call
        retry_count: Max number of retries (total attempts = retry_count + 1)
        cancel_check: Optional zero-arg callable invoked between retry
            attempts; see above.

    Returns:
        (result, api_seconds) -- the return value of call_fn on success, and
        the wall time of that one successful call_fn() invocation.

        api_seconds deliberately EXCLUDES backoff sleeps, failed attempts, and
        time spent blocked on _llm_call_semaphore. It is API response time, not
        time-to-success: callers report it as latency_ms, and cost/perf
        dashboards built on that field would otherwise read a 35s "latency" for
        a call whose every attempt took 300ms (#274). The wait a caller
        actually experienced is not currently reported anywhere; add a separate
        field if something needs it, rather than folding it back into this one.

    Raises:
        TypeError: If retry_count is not an int
        ValueError: If retry_count is negative
        The last error if all retries are exhausted
        Non-retryable errors immediately (including non-retryable ClientError)
    """
    # retry_count is a call-site kwarg passthrough (ultimately from
    # llm_config.yaml via get_stage_config), so a malformed value is
    # reachable, not just theoretical. Validate the type first: "3" < 0
    # raises TypeError immediately, and 3.5 passes a bare `< 0` check but
    # later breaks range(retry_count + 1) with a confusing TypeError deep in
    # the loop. A negative int would make the loop run zero times and fall
    # through to `raise last_error` with last_error still None -- also a
    # bare TypeError instead of the misconfiguration that caused it
    # (PR #620 review).
    if isinstance(retry_count, bool) or not isinstance(retry_count, int):
        raise TypeError(f"retry_count must be an integer, got {type(retry_count).__name__}")
    if retry_count < 0:
        raise ValueError(f"retry_count must be >= 0, got {retry_count}")
    last_error = None
    for attempt in range(retry_count + 1):
        try:
            # Bound concurrent in-flight calls per pod. The slot is acquired only
            # around the actual call and released before any backoff sleep below,
            # so a backing-off caller never holds a slot idle.
            with _llm_call_semaphore:
                # monotonic, not time(): a wall-clock step (NTP) mid-call must
                # not be able to produce a negative or wildly wrong latency.
                started = time.monotonic()
                result = call_fn()
                return result, time.monotonic() - started
        except RETRYABLE_ERRORS as e:
            # For botocore ClientError, only retry if the error code is retryable.
            # Non-retryable Bedrock errors (AccessDeniedException, ValidationException,
            # etc.) should propagate immediately.
            if isinstance(e, _BotoClientError):
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code not in BEDROCK_RETRYABLE_CODES:
                    raise
            last_error = e
            if attempt < retry_count:
                # Exponential backoff with equal jitter (AWS "backoff and
                # jitter"): half the exponential base plus a random half, i.e.
                # a wait in [base/2, base] where base is 1s, 2s, 4s... capped at
                # 30s. The jitter decorrelates the retries of many runs that
                # were throttled at the same instant, so they don't retry in
                # lockstep and re-throttle together (a self-inflicted herd).
                base = min(2 ** attempt, 30)
                wait = base / 2 + random.uniform(0, base / 2)
                logger.warning(
                    f"LLM call failed (attempt {attempt + 1}/{retry_count + 1}): {e}. "
                    f"Retrying in {wait:.1f}s..."
                )
                time.sleep(wait)
                if cancel_check is not None:
                    cancel_check()
    raise last_error
