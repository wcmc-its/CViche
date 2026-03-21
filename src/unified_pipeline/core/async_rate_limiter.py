"""
Async Rate Limiter for OpenAI API Calls

Provides concurrency control and token-bucket rate limiting for parallel
LLM calls across pipeline stages. Designed to maximize throughput while
respecting OpenAI rate limits (TPM and RPM).

Rate Limit Analysis (for gpt-5.1 / gpt-4o tier):
- TPM: 2,000,000 tokens/minute
- RPM: 5,000 requests/minute

Stage-specific recommendations (based on observed token usage):
- Stage 2 (Entry Extraction): ~1,600 tokens/request avg → TPM-limited → max 100-120 concurrent
- Stage 3b (Classification): ~4,700 tokens/request avg → TPM-limited → max 20-30 concurrent

Usage:
    from core.async_rate_limiter import get_rate_limiter, RateLimitedOpenAI

    # Get the shared rate limiter instance
    limiter = get_rate_limiter()

    # Use the rate-limited async client
    async with limiter.acquire("stage_2", estimated_tokens=1600):
        response = await async_client.chat.completions.create(...)
        limiter.record_completion(response.usage.total_tokens)

Author: Scholar Signals CV Pipeline
Date: 2025-12-03
"""

import asyncio
import time
import logging
from typing import Optional, Dict, Any, Literal
from dataclasses import dataclass, field
from contextlib import asynccontextmanager
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# ============================================================================
# Configuration
# ============================================================================

@dataclass
class RateLimitConfig:
    """Rate limit configuration for OpenAI API."""

    # OpenAI tier limits (adjust based on your account tier)
    tpm_limit: int = 2_000_000          # Tokens per minute
    rpm_limit: int = 5_000              # Requests per minute

    # Safety margin (use 80% of limits to avoid bursts triggering 429s)
    safety_factor: float = 0.80

    # Per-stage concurrency limits (derived from analysis)
    stage_concurrency: Dict[str, int] = field(default_factory=lambda: {
        "stage_2": 100,      # Entry extraction: ~1,600 tokens/req
        "stage_3b": 20,      # Classification: ~4,700 tokens/req
        "stage_4": 50,       # Field extraction: ~2,000 tokens/req (estimate)
        "default": 30,       # Default for other stages
    })

    # Backoff configuration for 429 errors
    initial_backoff: float = 1.0        # Initial backoff in seconds
    max_backoff: float = 60.0           # Maximum backoff
    backoff_multiplier: float = 2.0     # Exponential multiplier

    @property
    def effective_tpm(self) -> int:
        """TPM with safety margin applied."""
        return int(self.tpm_limit * self.safety_factor)

    @property
    def effective_rpm(self) -> int:
        """RPM with safety margin applied."""
        return int(self.rpm_limit * self.safety_factor)


# Default configuration
DEFAULT_CONFIG = RateLimitConfig()


# ============================================================================
# Token Bucket for TPM Limiting
# ============================================================================

class TokenBucket:
    """
    Token bucket rate limiter for TPM control.

    Refills continuously at TPM rate. Requests must acquire tokens
    before proceeding.
    """

    def __init__(self, tokens_per_minute: int):
        self.tokens_per_minute = tokens_per_minute
        self.tokens_per_second = tokens_per_minute / 60.0
        self.max_tokens = tokens_per_minute  # Allow burst up to 1 minute worth
        self.available_tokens = float(self.max_tokens)
        self.last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int, timeout: float = 60.0) -> bool:
        """
        Acquire tokens from the bucket.

        Args:
            tokens: Number of tokens to acquire
            timeout: Maximum time to wait for tokens

        Returns:
            True if tokens acquired, False if timeout
        """
        deadline = time.monotonic() + timeout

        async with self._lock:
            while True:
                self._refill()

                if self.available_tokens >= tokens:
                    self.available_tokens -= tokens
                    return True

                # Calculate wait time for enough tokens
                tokens_needed = tokens - self.available_tokens
                wait_time = tokens_needed / self.tokens_per_second

                if time.monotonic() + wait_time > deadline:
                    return False

                # Release lock while waiting
                self._lock.release()
                try:
                    await asyncio.sleep(min(wait_time, 0.5))  # Check every 0.5s max
                finally:
                    await self._lock.acquire()

    def _refill(self):
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.last_refill = now

        new_tokens = elapsed * self.tokens_per_second
        self.available_tokens = min(self.max_tokens, self.available_tokens + new_tokens)

    def record_actual_usage(self, actual_tokens: int, estimated_tokens: int):
        """
        Adjust bucket for difference between estimated and actual tokens.

        If we overestimated, return the excess. If underestimated, deduct more.
        """
        difference = estimated_tokens - actual_tokens
        if difference != 0:
            # Adjust available tokens (can go negative if we underestimated)
            self.available_tokens += difference


# ============================================================================
# Main Rate Limiter
# ============================================================================

class AsyncRateLimiter:
    """
    Async rate limiter for OpenAI API calls.

    Combines:
    - Per-stage semaphores for concurrency control
    - Token bucket for TPM limiting
    - Exponential backoff for 429 handling
    """

    def __init__(self, config: RateLimitConfig = None):
        self.config = config or DEFAULT_CONFIG

        # Token bucket for TPM limiting
        self._token_bucket = TokenBucket(self.config.effective_tpm)

        # Per-stage semaphores for concurrency control
        self._semaphores: Dict[str, asyncio.Semaphore] = {}

        # Statistics
        self._stats = {
            "total_requests": 0,
            "total_tokens": 0,
            "retries": 0,
            "rate_limit_waits": 0,
        }

        # Lock for semaphore creation
        self._semaphore_lock = asyncio.Lock()

    async def _get_semaphore(self, stage: str) -> asyncio.Semaphore:
        """Get or create semaphore for a stage."""
        if stage not in self._semaphores:
            async with self._semaphore_lock:
                if stage not in self._semaphores:
                    max_concurrent = self.config.stage_concurrency.get(
                        stage,
                        self.config.stage_concurrency["default"]
                    )
                    self._semaphores[stage] = asyncio.Semaphore(max_concurrent)
                    logger.info(f"Created semaphore for {stage} with max_concurrent={max_concurrent}")
        return self._semaphores[stage]

    @asynccontextmanager
    async def acquire(self, stage: str, estimated_tokens: int = 1000):
        """
        Acquire rate limit permission for an API call.

        Args:
            stage: Pipeline stage name (e.g., "stage_2", "stage_3b")
            estimated_tokens: Estimated tokens for this request

        Yields:
            Context for making the API call

        Example:
            async with limiter.acquire("stage_2", estimated_tokens=1600):
                response = await client.chat.completions.create(...)
        """
        semaphore = await self._get_semaphore(stage)

        # Acquire semaphore (concurrency limit)
        async with semaphore:
            # Acquire tokens from bucket (TPM limit)
            acquired = await self._token_bucket.acquire(estimated_tokens)
            if not acquired:
                self._stats["rate_limit_waits"] += 1
                logger.warning(f"Token bucket timeout for {stage}, proceeding anyway")

            self._stats["total_requests"] += 1

            try:
                yield
            finally:
                pass  # Cleanup if needed

    def record_completion(self, actual_tokens: int, estimated_tokens: int = None):
        """
        Record actual token usage after API call completes.

        Args:
            actual_tokens: Actual tokens used (from response.usage.total_tokens)
            estimated_tokens: Original estimate (for adjustment)
        """
        self._stats["total_tokens"] += actual_tokens

        if estimated_tokens is not None:
            self._token_bucket.record_actual_usage(actual_tokens, estimated_tokens)

    def record_retry(self):
        """Record a retry (for 429 handling)."""
        self._stats["retries"] += 1

    @property
    def stats(self) -> Dict[str, Any]:
        """Get current statistics."""
        return {
            **self._stats,
            "available_tokens": int(self._token_bucket.available_tokens),
        }

    def get_stage_concurrency(self, stage: str) -> int:
        """Get the configured concurrency limit for a stage."""
        return self.config.stage_concurrency.get(
            stage,
            self.config.stage_concurrency["default"]
        )


# ============================================================================
# Singleton Instance
# ============================================================================

_rate_limiter: Optional[AsyncRateLimiter] = None
_limiter_lock = asyncio.Lock()


async def get_rate_limiter(config: RateLimitConfig = None) -> AsyncRateLimiter:
    """
    Get the shared rate limiter instance.

    Args:
        config: Optional configuration (only used on first call)

    Returns:
        Shared AsyncRateLimiter instance
    """
    global _rate_limiter

    if _rate_limiter is None:
        async with _limiter_lock:
            if _rate_limiter is None:
                _rate_limiter = AsyncRateLimiter(config)
                logger.info("Created shared AsyncRateLimiter instance")

    return _rate_limiter


def get_rate_limiter_sync(config: RateLimitConfig = None) -> AsyncRateLimiter:
    """
    Synchronous version of get_rate_limiter for initialization.

    Use this when you need to get the limiter outside an async context.
    """
    global _rate_limiter

    if _rate_limiter is None:
        _rate_limiter = AsyncRateLimiter(config)
        logger.info("Created shared AsyncRateLimiter instance (sync)")

    return _rate_limiter


def reset_rate_limiter():
    """Reset the shared rate limiter (for testing)."""
    global _rate_limiter
    _rate_limiter = None


# ============================================================================
# Retry Decorator with Exponential Backoff
# ============================================================================

def with_retry(
    max_retries: int = 3,
    initial_backoff: float = 1.0,
    max_backoff: float = 60.0,
    backoff_multiplier: float = 2.0,
    retryable_errors: tuple = (429, 500, 502, 503, 504)
):
    """
    Decorator for async functions that adds retry with exponential backoff.

    Args:
        max_retries: Maximum number of retries
        initial_backoff: Initial backoff in seconds
        max_backoff: Maximum backoff in seconds
        backoff_multiplier: Multiplier for exponential backoff
        retryable_errors: HTTP status codes to retry on

    Example:
        @with_retry(max_retries=3)
        async def call_openai(...):
            ...
    """
    def decorator(func):
        async def wrapper(*args, **kwargs):
            limiter = get_rate_limiter_sync()
            backoff = initial_backoff
            last_error = None

            for attempt in range(max_retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    last_error = e

                    # Check if this is a retryable error
                    status_code = getattr(e, 'status_code', None)
                    if status_code not in retryable_errors:
                        raise

                    if attempt < max_retries:
                        limiter.record_retry()
                        logger.warning(
                            f"Retryable error (status={status_code}), "
                            f"attempt {attempt + 1}/{max_retries + 1}, "
                            f"backing off {backoff:.1f}s"
                        )
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * backoff_multiplier, max_backoff)
                    else:
                        logger.error(f"Max retries exceeded: {e}")
                        raise

            raise last_error

        return wrapper
    return decorator


# ============================================================================
# High-Level Helper: Rate-Limited Batch Processor
# ============================================================================

async def process_batch_with_rate_limit(
    items: list,
    process_func,
    stage: str,
    estimated_tokens_per_item: int = 1000,
    desc: str = "Processing",
) -> list:
    """
    Process a batch of items with rate limiting.

    Args:
        items: List of items to process
        process_func: Async function to process each item
        stage: Pipeline stage name for concurrency limits
        estimated_tokens_per_item: Estimated tokens per API call
        desc: Description for logging

    Returns:
        List of results in same order as input items

    Example:
        async def classify_entry(entry):
            async with limiter.acquire("stage_3b", 4700):
                return await call_openai(entry)

        results = await process_batch_with_rate_limit(
            entries,
            classify_entry,
            stage="stage_3b",
            estimated_tokens_per_item=4700
        )
    """
    limiter = get_rate_limiter_sync()

    async def wrapped_process(idx: int, item):
        async with limiter.acquire(stage, estimated_tokens_per_item):
            result = await process_func(item)
            return (idx, result)

    # Create tasks for all items
    tasks = [wrapped_process(i, item) for i, item in enumerate(items)]

    # Run all tasks concurrently (semaphore controls actual concurrency)
    logger.info(f"{desc}: Processing {len(items)} items with max {limiter.get_stage_concurrency(stage)} concurrent")

    completed = await asyncio.gather(*tasks, return_exceptions=True)

    # Sort by original index and extract results
    results = [None] * len(items)
    errors = []

    for result in completed:
        if isinstance(result, Exception):
            errors.append(result)
        else:
            idx, value = result
            results[idx] = value

    if errors:
        logger.warning(f"{desc}: {len(errors)} errors occurred")
        for err in errors[:3]:  # Log first 3 errors
            logger.error(f"  Error: {err}")

    logger.info(f"{desc}: Completed. Stats: {limiter.stats}")

    return results


# ============================================================================
# Convenience: Async OpenAI Client Wrapper
# ============================================================================

class RateLimitedOpenAI:
    """
    Wrapper around AsyncOpenAI that adds rate limiting.

    Example:
        client = RateLimitedOpenAI(stage="stage_3b")

        response = await client.chat_completion(
            messages=[...],
            model="gpt-5.1",
            estimated_tokens=4700
        )
    """

    def __init__(self, stage: str = "default", client: AsyncOpenAI = None):
        self.stage = stage
        self._client = client or AsyncOpenAI()
        self._limiter = get_rate_limiter_sync()

    @with_retry(max_retries=3)
    async def chat_completion(
        self,
        messages: list,
        model: str = "gpt-5.1",
        estimated_tokens: int = 1000,
        **kwargs
    ):
        """
        Make a rate-limited chat completion request.

        Args:
            messages: Chat messages
            model: Model name
            estimated_tokens: Estimated total tokens for rate limiting
            **kwargs: Additional arguments for chat.completions.create

        Returns:
            OpenAI ChatCompletion response
        """
        async with self._limiter.acquire(self.stage, estimated_tokens):
            response = await self._client.chat.completions.create(
                messages=messages,
                model=model,
                **kwargs
            )

            # Record actual usage
            if hasattr(response, 'usage') and response.usage:
                self._limiter.record_completion(
                    response.usage.total_tokens,
                    estimated_tokens
                )

            return response


# ============================================================================
# Testing / CLI
# ============================================================================

if __name__ == "__main__":
    import sys

    async def test_rate_limiter():
        """Test the rate limiter with simulated requests."""
        print("Testing AsyncRateLimiter...")

        limiter = get_rate_limiter_sync()

        # Simulate concurrent requests
        async def fake_request(i: int, stage: str):
            async with limiter.acquire(stage, estimated_tokens=1500):
                print(f"  Request {i} ({stage}) started")
                await asyncio.sleep(0.5)  # Simulate API call
                limiter.record_completion(1400, 1500)  # Slightly less than estimated
                print(f"  Request {i} ({stage}) completed")
                return i

        # Test Stage 2 concurrency (should allow ~100 concurrent)
        print("\nTesting Stage 2 (max 100 concurrent):")
        tasks = [fake_request(i, "stage_2") for i in range(10)]
        results = await asyncio.gather(*tasks)
        print(f"Results: {results}")

        # Test Stage 3b concurrency (should allow ~20 concurrent)
        print("\nTesting Stage 3b (max 20 concurrent):")
        tasks = [fake_request(i, "stage_3b") for i in range(5)]
        results = await asyncio.gather(*tasks)
        print(f"Results: {results}")

        print(f"\nFinal stats: {limiter.stats}")

    asyncio.run(test_rate_limiter())
