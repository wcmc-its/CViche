"""Event emitter for WebSocket communication.

Delivery has two modes, selected by whether a Redis broker is enabled:

  - Broker disabled (no CVICHE_REDIS_URL): events are written directly to this
    process's local WebSocket connections. Correct for a single worker/replica;
    this is the historical behavior.
  - Broker enabled: emit() only *publishes* to Redis; a per-worker subscriber
    loop (started at app startup) receives every run's events and delivers them
    to whatever sockets that worker holds locally. This lets events reach a
    browser connected to a different worker/replica than the one running the
    pipeline -- and, because the subscriber runs on the main server loop where
    the sockets live, delivery is on the correct loop.
"""
import asyncio
import json
import logging
from typing import Dict, Optional, Set
from fastapi import WebSocket
from datetime import datetime

from app.pipeline.redis_broker import EVENTS_PATTERN

logger = logging.getLogger(__name__)


class EventEmitter:
    """Manages WebSocket connections and broadcasts events."""

    def __init__(self, broker=None):
        self.connections: Dict[str, Set[WebSocket]] = {}
        self._broker = broker
        self._pubsub = None
        self._subscriber_task: Optional[asyncio.Task] = None

    def set_broker(self, broker) -> None:
        """Attach the Redis broker (called at app startup)."""
        self._broker = broker

    @property
    def _enabled(self) -> bool:
        return self._broker is not None and self._broker.enabled

    async def startup(self) -> None:
        """Start the subscriber loop when the broker is enabled. A single
        pattern subscription covers every run's event channel for this worker."""
        if not self._enabled:
            return
        client = await self._broker.async_client()
        self._pubsub = client.pubsub()
        await self._pubsub.psubscribe(EVENTS_PATTERN)
        self._subscriber_task = asyncio.create_task(self._subscribe_loop())

    async def shutdown(self) -> None:
        if self._subscriber_task:
            self._subscriber_task.cancel()
            try:
                await self._subscriber_task
            except asyncio.CancelledError:
                pass
            self._subscriber_task = None
        if self._pubsub is not None:
            try:
                await self._pubsub.aclose()
            except Exception:
                logger.warning("pubsub close failed", exc_info=True)
            self._pubsub = None

    async def _subscribe_loop(self) -> None:
        """Receive published events and fan them out to local sockets."""
        try:
            async for message in self._pubsub.listen():
                if message.get("type") != "pmessage":
                    continue
                channel = message["channel"]
                if isinstance(channel, bytes):
                    channel = channel.decode()
                # channel == cviche:run:{run_id}:events
                run_id = channel.split(":")[2]
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode()
                await self._deliver_local(run_id, data)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("subscriber loop error", exc_info=True)

    async def _deliver_local(self, run_id: str, message: str) -> None:
        """Send a serialized message to this worker's sockets for run_id."""
        local = self.connections.get(run_id)
        if not local:
            return
        disconnected = set()
        for websocket in list(local):
            try:
                await websocket.send_text(message)
            except Exception:
                disconnected.add(websocket)
        for ws in disconnected:
            self.disconnect(run_id, ws)

    async def connect(self, run_id: str, websocket: WebSocket):
        """Register a new WebSocket connection."""
        await websocket.accept()
        if run_id not in self.connections:
            self.connections[run_id] = set()
        self.connections[run_id].add(websocket)

    def disconnect(self, run_id: str, websocket: WebSocket):
        """Remove a WebSocket connection."""
        if run_id in self.connections:
            self.connections[run_id].discard(websocket)
            if not self.connections[run_id]:
                del self.connections[run_id]

    async def emit(self, run_id: str, event: dict):
        """Broadcast an event. Publishes via Redis when enabled, else delivers
        directly to this process's local connections."""
        if "timestamp" not in event:
            event["timestamp"] = datetime.now().isoformat()

        if self._enabled:
            # Publish only; the subscriber loop delivers to local sockets
            # (including this worker's). Never write to sockets from the
            # producer side when brokered -- it may be the wrong loop.
            self._broker.publish_event(run_id, event)
            return

        await self._deliver_local(run_id, json.dumps(event))

    async def emit_run_start(self, run_id: str):
        await self.emit(run_id, {"event": "RUN_START"})

    async def emit_step_start(self, run_id: str, step_number: int, total_cost: float = 0.0):
        await self.emit(run_id, {"event": "STEP_START", "step": step_number, "total_cost": total_cost})

    async def emit_log(self, run_id: str, step_number: int, message: str, level: str = "INFO"):
        await self.emit(run_id, {"event": "LOG", "step": step_number, "level": level, "message": message})

    async def emit_step_complete(self, run_id: str, step_number: int, duration: int, cost: float, output_files: list):
        await self.emit(run_id, {"event": "STEP_COMPLETE", "step": step_number, "duration": duration, "cost": cost, "output_files": output_files})

    async def emit_step_error(self, run_id: str, step_number: int, error: str):
        await self.emit(run_id, {"event": "STEP_ERROR", "step": step_number, "error": error})

    async def emit_run_complete(self, run_id: str, total_cost: float, total_tokens: int, duration: int):
        await self.emit(run_id, {"event": "RUN_COMPLETE", "total_cost": total_cost, "total_tokens": total_tokens, "duration": duration})

    async def emit_run_failed(self, run_id: str, error: str, step_number: int | None = None):
        """Emit an explicit terminal failure event.

        The granular STEP_ERROR tells the client which stage broke; this
        run-level event is the authoritative "this run is over, it failed"
        signal that lets the UI stop the elapsed timer and switch to the
        failure state immediately, rather than waiting for the next status
        poll to observe run.status == 'failed'. Mirrors emit_run_complete /
        RUN_CANCELLED so every terminal outcome has a run-level event.
        """
        await self.emit(run_id, {"event": "RUN_FAILED", "error": error, "step": step_number})

    async def emit_progress(self, run_id: str, step_number: int, current: int, total: int, message: str = ""):
        """Emit granular progress within a step (e.g., "Processing 5 of 20 sections")."""
        await self.emit(run_id, {
            "event": "PROGRESS",
            "step": step_number,
            "current": current,
            "total": total,
            "message": message,
            "percentage": round((current / total) * 100) if total > 0 else 0
        })

    async def emit_cost_update(self, run_id: str, step_number: int, cost_delta: float, total_cost: float,
                                tokens_delta: int = 0, total_tokens: int = 0,
                                input_tokens_delta: int = 0, output_tokens_delta: int = 0,
                                input_tokens_total: int = 0, output_tokens_total: int = 0,
                                cache_read_tokens_delta: int = 0, cache_write_tokens_delta: int = 0,
                                cache_read_tokens_total: int = 0, cache_write_tokens_total: int = 0,
                                provider: str = "openai"):
        """Emit real-time cost updates as LLM calls complete.

        Cache token fields carry the Bedrock prompt-caching split (input
        tokens served from / written to cache). They are subsets of
        input_tokens, not additions -- the frontend can divide
        cache_read / input to show a cache hit rate.
        """
        await self.emit(run_id, {
            "event": "COST_UPDATE",
            "step": step_number,
            "cost_delta": cost_delta,
            "total_cost": total_cost,
            "tokens_delta": tokens_delta,
            "total_tokens": total_tokens,
            "input_tokens_delta": input_tokens_delta,
            "output_tokens_delta": output_tokens_delta,
            "input_tokens": input_tokens_total,
            "output_tokens": output_tokens_total,
            "cache_read_tokens_delta": cache_read_tokens_delta,
            "cache_write_tokens_delta": cache_write_tokens_delta,
            "cache_read_tokens": cache_read_tokens_total,
            "cache_write_tokens": cache_write_tokens_total,
            "provider": provider,
        })


# Global instance
event_emitter = EventEmitter()
