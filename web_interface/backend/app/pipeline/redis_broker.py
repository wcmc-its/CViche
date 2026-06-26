"""Optional Redis broker for cross-worker real-time fan-out and cancellation.

When CVICHE_REDIS_URL is set, pipeline events and run cancellation cross worker
and replica boundaries via Redis. When it is unset the broker is *disabled* and
the EventEmitter / orchestrator fall back to process-local state -- exactly
today's single-worker behavior. This lets the code merge and run unchanged until
Redis is provisioned, then scale out by setting one env var (see
docs/proposals/issue-4-redis-broker.md and concurrency-and-load-readiness.md).

Two clients, by design, because of how a run executes:

  - A SYNC client used by the orchestrator. A run runs inside its own
    background-thread event loop (see runs.py:start_run), so publishing events
    and checking the cancel flag from there with a *sync* client avoids binding
    an async client to the wrong loop -- and keeps the orchestrator's
    cancellation check synchronous (no async ripple through the hot path).

  - An ASYNC client owning the pub/sub subscriber loop, created on the main
    server loop where the WebSocket connections live (see event_emitter). Because
    the subscriber runs on that loop, delivering to sockets from it is correct --
    which also fixes the latent cross-loop fragility of the in-process path,
    where the orchestrator thread writes to sockets owned by the main loop.

All Redis operations are best-effort: a broker failure logs and degrades rather
than failing the run (the DB remains the source of truth for run status).
"""
import os
import json
import logging
import threading

from app.config_loader import get_config

logger = logging.getLogger(__name__)

EVENTS_CHANNEL = "cviche:run:{run_id}:events"
EVENTS_PATTERN = "cviche:run:*:events"
CANCEL_KEY = "cviche:run:{run_id}:cancelled"
# Safety net: if the orchestrator dies mid-run and never clears the flag, the
# key expires rather than cancelling a future run that reuses the id.
CANCEL_TTL_SECONDS = 300


class RedisBroker:
    """Best-effort Redis wrapper. ``enabled`` is False when no URL is configured,
    in which case every method is a no-op and callers use process-local state."""

    def __init__(self, url: str | None):
        self.url = url or None
        self.enabled = bool(self.url)
        self._sync_client = None
        self._async_client = None
        self._sync_lock = threading.Lock()

    # --- sync side: used from the orchestrator's background-thread loop --------

    def _sync(self):
        if self._sync_client is None:
            with self._sync_lock:
                if self._sync_client is None:
                    import redis
                    # Bound socket ops so a hung Valkey raises instead of
                    # blocking the orchestrator thread; the except-branches
                    # below already degrade to process-local state.
                    self._sync_client = redis.Redis.from_url(
                        self.url, socket_timeout=2, socket_connect_timeout=2
                    )
        return self._sync_client

    def publish_event(self, run_id: str, event: dict) -> None:
        if not self.enabled:
            return
        try:
            self._sync().publish(EVENTS_CHANNEL.format(run_id=run_id), json.dumps(event))
        except Exception:
            logger.warning("Redis publish failed for run %s", run_id, exc_info=True)

    def request_cancel(self, run_id: str) -> None:
        if not self.enabled:
            return
        try:
            self._sync().setex(CANCEL_KEY.format(run_id=run_id), CANCEL_TTL_SECONDS, "1")
        except Exception:
            logger.warning("Redis cancel-set failed for run %s", run_id, exc_info=True)

    def is_cancelled(self, run_id: str) -> bool:
        if not self.enabled:
            return False
        try:
            return bool(self._sync().exists(CANCEL_KEY.format(run_id=run_id)))
        except Exception:
            logger.warning("Redis cancel-check failed for run %s", run_id, exc_info=True)
            return False

    def clear_cancel(self, run_id: str) -> None:
        if not self.enabled:
            return
        try:
            self._sync().delete(CANCEL_KEY.format(run_id=run_id))
        except Exception:
            logger.warning("Redis cancel-clear failed for run %s", run_id, exc_info=True)

    # --- async side: used by the subscriber loop on the main server loop -------

    async def async_client(self):
        if self._async_client is None:
            import redis.asyncio as aioredis
            # Only bound the connect: the subscriber's listen() blocks waiting
            # for messages, so a socket_timeout would spuriously break idle
            # pub/sub reads. socket_connect_timeout just stops a hung connect.
            self._async_client = aioredis.Redis.from_url(
                self.url, socket_connect_timeout=2
            )
        return self._async_client

    async def shutdown(self) -> None:
        if self._async_client is not None:
            try:
                await self._async_client.aclose()
            except Exception:
                logger.warning("Redis async client close failed", exc_info=True)
            self._async_client = None
        if self._sync_client is not None:
            try:
                self._sync_client.close()
            except Exception:
                logger.warning("Redis sync client close failed", exc_info=True)
            self._sync_client = None


def broker_from_env() -> RedisBroker:
    """Build the broker from CVICHE_REDIS_URL (disabled when unset/empty)."""
    CVICHE_REDIS_URL, _ = get_config("redis", "CVICHE_REDIS_URL", default="")
    return RedisBroker(CVICHE_REDIS_URL)
