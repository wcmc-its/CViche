"""Shared call infrastructure for the Bedrock provider adapter.

Owns the pieces the provider adapter depends on: retry/backoff, the per-pod
in-flight-call semaphore, the client-init lock, and the LLM tuning knobs
(timeout, max attempts, max concurrency) read from llm_config.yaml. Split out
of llm_client.py (#496) -- this is where it has to live: llm/bedrock.py
depends on it, so it cannot live in the provider file without creating an
import cycle if a second provider is ever added, and llm_client.py (the
facade) cannot own it either without an import cycle (llm_client imports the
provider handler, which needs this module's state).
"""

import time
import random
import logging
import threading
from collections.abc import Callable
from typing import TypeVar

from unified_pipeline.config import get_llm_env_config

logger = logging.getLogger(__name__)

# Bedrock error codes that should trigger retry
BEDROCK_RETRYABLE_CODES = frozenset({
    "ThrottlingException",
    "ModelTimeoutException",
    "InternalServerException",
    "ServiceUnavailableException",
})

# Subset of BEDROCK_RETRYABLE_CODES that means the PROVIDER is down or
# shedding load, not an ordinary transient blip (a wedged connection, a slow
# model). These get an outage-scale pause (_call_with_retry's outage branch,
# below) instead of the few-seconds retry_count budget: a 24-minute Bedrock
# outage on stage 3b's model burned through 4 attempts in <7s and fell back
# to default classification codes for the rest of the run (#810).
BEDROCK_OUTAGE_CODES = frozenset({
    "ThrottlingException",
    "ServiceUnavailableException",
})

# boto3 is a hard pin in requirements.txt (llm/bedrock.py already imports it
# unconditionally), so botocore -- one of its own transitive dependencies --
# is always present in every supported environment. The previous
# try/except ImportError fallback here could never actually trigger and was
# dead defensive code (PR #620 review).
from botocore.exceptions import ClientError as _BotoClientError
from botocore.exceptions import ConnectionError as _BotoConnectionError
from botocore.exceptions import HTTPClientError as _BotoHTTPClientError

# _call_with_retry is the single retry owner (#632): the Bedrock client is
# configured for one botocore attempt, so the transport-level failures botocore
# used to retry itself (connect/read timeouts, dropped connections -- exactly
# the classes botocore's standard-mode TransientRetryableChecker retries) must
# be retried here or they would become a first-attempt failure.
RETRYABLE_ERRORS = (_BotoClientError, _BotoConnectionError, _BotoHTTPClientError)

T = TypeVar("T")


class LLMOutageError(Exception):
    """A provider-side outage (see _is_outage_error) persisted beyond the
    outage budget (CVICHE_LLM_OUTAGE_BUDGET_SECONDS). Distinct from the
    provider's own exception classes so a caller can tell "the provider was
    down for N seconds" from an ordinary parse/validation error and choose
    to fail the run rather than silently default-code everything in flight
    (#810). Always raised with `from <the last provider error>`, so
    `__cause__` carries it.
    """

    def __init__(self, message: str, seconds_waited: float) -> None:
        super().__init__(message)
        self.seconds_waited = seconds_waited


# Per-pod ceiling on concurrent in-flight LLM calls. A backstop against fanning
# out too many simultaneous Bedrock requests from one pod -- e.g. if the
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
        value, _ = get_llm_env_config(key, default)
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
        value, _ = get_llm_env_config(key, default)
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


def _get_outage_budget_seconds() -> float:
    """Wall-clock ceiling on how long _call_with_retry will pause through an
    outage-class error (see _is_outage_error) before giving up and raising
    LLMOutageError. Tune via CVICHE_LLM_OUTAGE_BUDGET_SECONDS. Default 30
    minutes: the two observed Bedrock outage windows were ~24 min and ~7 min
    (#810).
    """
    return _get_llm_config_float("CVICHE_LLM_OUTAGE_BUDGET_SECONDS", default=1800.0)


# Backoff cap for an outage-class retry -- longer than the ordinary 30s cap
# (below) since an outage pause can legitimately run for the whole budget.
# cancel_check is only checked between retries (see _call_with_retry's
# docstring), so this is also the longest a cancel can be delayed during an
# outage pause.
_OUTAGE_BACKOFF_CAP_SECONDS = 60.0


def _is_outage_error(e: Exception) -> bool:
    """True for an error that reflects a PROVIDER-side outage (down, or
    shedding load) rather than an ordinary transient blip (a timeout, a
    wedged connection). Outage errors get _call_with_retry's minutes-scale
    pause, bounded by _get_outage_budget_seconds(), instead of the
    few-seconds retry_count budget.
    """
    if isinstance(e, _BotoClientError):
        return e.response.get("Error", {}).get("Code", "") in BEDROCK_OUTAGE_CODES
    return False


def _retry_after_seconds(e: Exception) -> float | None:
    """Best-effort Retry-After extraction (#637): botocore surfaces it as a
    response header. None if absent or unparseable -- the caller falls back
    to the generic equal-jitter backoff.
    """
    try:
        if isinstance(e, _BotoClientError):
            value = e.response.get("ResponseMetadata", {}).get("HTTPHeaders", {}).get("retry-after")
            return float(value) if value is not None else None
        return None
    except (TypeError, ValueError, AttributeError):
        return None


def _outage_backoff_wait(outage_retries: int, retry_after: float | None) -> float:
    """Seconds to pause before the next attempt during an outage-class
    retry. A provider-supplied Retry-After wins over the generic formula
    (#637), still capped -- a provider could itself ask for longer than we're
    willing to sleep between cancel_check calls. Otherwise the same
    equal-jitter formula _call_with_retry uses for ordinary retries, with
    _OUTAGE_BACKOFF_CAP_SECONDS in place of the 30s cap.
    """
    if retry_after is not None and retry_after >= 0:
        # Floored at 1s: an honoured Retry-After of 0 would otherwise be a
        # zero-second sleep -- a tight loop hammering the provider for the
        # whole outage budget instead of backing off (verifier round 2,
        # NOTE 3).
        return max(1.0, min(retry_after, _OUTAGE_BACKOFF_CAP_SECONDS))
    base = min(2 ** outage_retries, _OUTAGE_BACKOFF_CAP_SECONDS)
    return base / 2 + random.uniform(0, base / 2)


# Guards construction of the module-level Bedrock client (llm/bedrock.py).
# call_llm runs on several threads at once (see _llm_call_semaphore), so two
# threads can both observe `_client is None`. This is not safe for Bedrock:
# boto3 builds clients off the shared default session, and only *use* of an
# existing client is thread-safe, not its creation.
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
            This is the ONLY retry layer: the Bedrock client is built with one
            botocore attempt (#632), so retry_count + 1 is also the raw request
            count (outage-class errors excluded, see below).
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
        LLMOutageError: An outage-class error (see _is_outage_error) persisted
            past the outage budget (CVICHE_LLM_OUTAGE_BUDGET_SECONDS, #810).
        The last error if all (non-outage) retries are exhausted
        Non-retryable errors immediately (including non-retryable ClientError)
    """
    # retry_count is a call-site kwarg passthrough (ultimately from
    # llm_config.yaml via get_stage_config), so a malformed value is
    # reachable, not just theoretical. Validate the type and range here, up
    # front, so a bad value fails with a message pointing at the
    # misconfiguration -- not something confusing raised later. The loop
    # below has two paths: an outage-class error (_is_outage_error) retries
    # on its own outage_budget-bounded schedule, uncounted against
    # retry_count; an ordinary retryable error retries while
    # `attempt < retry_count`, then falls through to `raise e`, re-raising
    # the last attempt's own exception as-is. A negative retry_count would
    # make that check false starting on the very first failure, so the
    # misconfiguration would otherwise surface only as an ordinary-looking
    # exception from attempt 0 instead of pointing at the bad retry_count
    # (PR #620 review).
    if isinstance(retry_count, bool) or not isinstance(retry_count, int):
        raise TypeError(f"retry_count must be an integer, got {type(retry_count).__name__}")
    if retry_count < 0:
        raise ValueError(f"retry_count must be >= 0, got {retry_count}")
    attempt = 0
    outage_retries = 0
    outage_started: float | None = None
    while True:
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

            if _is_outage_error(e):
                # Outage-class errors don't count against retry_count -- they
                # get their own, much longer budget instead (#810). first
                # outage timestamp and retry count are local to this call, not
                # shared across LLM calls: each caller's own outage pause is
                # independent (several stage-3b workers can each be waiting
                # out the same outage on their own thread).
                now = time.monotonic()
                if outage_started is None:
                    outage_started = now
                elapsed = now - outage_started
                budget = _get_outage_budget_seconds()
                if elapsed >= budget:
                    raise LLMOutageError(
                        f"LLM provider outage exceeded the {budget:.0f}s budget "
                        f"({elapsed:.0f}s elapsed, {outage_retries} pauses): {e}",
                        seconds_waited=elapsed,
                    ) from e
                wait = _outage_backoff_wait(outage_retries, _retry_after_seconds(e))
                outage_retries += 1
                logger.warning(
                    "LLM provider outage (%.0fs elapsed / %.0fs budget): %s. Pausing %.1fs...",
                    elapsed, budget, e, wait,
                )
                time.sleep(wait)
                if cancel_check is not None:
                    cancel_check()
                continue

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
                attempt += 1
                continue
            raise e
