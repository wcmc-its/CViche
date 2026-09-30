"""Event emitter for WebSocket communication.

Delivery has two modes, selected by whether a Redis broker is enabled:

  - Broker disabled (no CVICHE_REDIS_URL): events are written directly to this
    process's local WebSocket connections. Correct for a single worker/replica.
    The write always happens on the loop the sockets were accepted on: a run
    emits from its own asyncio.run loop in a worker thread, so emit() hands the
    delivery to the socket-owning loop rather than writing from the wrong one
    (#116).
  - Broker enabled: emit() only *publishes* to Redis; a per-worker subscriber
    loop (started at app startup) receives every run's events and delivers them
    to whatever sockets that worker holds locally. This lets events reach a
    browser connected to a different worker/replica than the one running the
    pipeline -- and, because the subscriber runs on the main server loop where
    the sockets live, delivery is on the correct loop.
"""
import asyncio
import contextvars
import json
import logging
import uuid
from fastapi import WebSocket
from datetime import datetime

from app.pipeline.redis_broker import EVENTS_CHANNEL

# How long one get_message() waits before looping; well under redis-py 8's 5s
# default socket_timeout, which is what killed the blocking listen() (#960).
SUBSCRIBER_POLL_SECONDS = 1.0
# Pause before resubscribing after an error, so a down Valkey isn't hammered.
SUBSCRIBER_RETRY_SECONDS = 1.0
# How long a run thread's emit waits for its event to be written on the socket
# loop. Waiting keeps a run's events in order; the cap keeps a slow client from
# stalling the pipeline, and stays under the 2s a streamed stdout line waits
# for its whole log() call (orchestrator.StreamingStdoutCapture).
CROSS_LOOP_EMIT_WAIT_SECONDS = 1.0

logger = logging.getLogger(__name__)

# A run ends exactly once, and the client acts on that once (it stops its
# elapsed timer and switches to the outcome view). But the terminal event can
# reach one socket by two routes -- the live broadcast, and the replay the
# WebSocket endpoint sends on connect -- and a client connecting in the same
# instant the run finishes gets both. These names are the routes' overlap; a
# socket is told at most once (#657 review, thread 6). Kept in sync by hand
# with websocket._terminal_event_for_run, which builds these same three.
_TERMINAL_EVENTS = frozenset({"RUN_COMPLETE", "RUN_FAILED", "RUN_CANCELLED"})

# Dollar fields any event may carry. Processing cost is admin-only (#1111), so
# these are removed from every message sent to a non-admin's socket.
_COST_KEYS = frozenset({"total_cost", "cost", "cost_delta"})


def _stamp(event: dict) -> dict:
    """Add the fields every emitted event carries, in place.

    `event_id` is a per-emission identity: it lets a consumer recognize the
    same event arriving twice (across a reconnect, or from two delivery
    routes) rather than inferring duplication from field equality. Additive --
    the frontend switches on `event` and ignores unknown keys.
    """
    event.setdefault("timestamp", datetime.now().isoformat())
    event.setdefault("event_id", uuid.uuid4().hex)
    return event


def _without_cost(message: str) -> str:
    """The serialized event with its _COST_KEYS removed."""
    event = json.loads(message)
    for key in _COST_KEYS:
        event.pop(key, None)
    return json.dumps(event)


def _event_name(message: str) -> str | None:
    """The `event` name inside an already-serialized message, or None.

    _deliver_local is handed JSON, not a dict (the broker path receives it off
    the wire), so the name has to be read back out to know whether this is a
    terminal event.
    """
    try:
        parsed = json.loads(message)
    except json.JSONDecodeError:
        logger.warning("Undeliverable event: message is not JSON")
        return None
    if not isinstance(parsed, dict):
        return None
    name = parsed.get("event")
    return name if isinstance(name, str) else None


class EventEmitter:
    """Manages WebSocket connections and broadcasts events."""

    def __init__(self, broker=None):
        self.connections: dict[str, set[WebSocket]] = {}
        # Sockets that have already been told this run is over. Per socket, not
        # per run: two clients watching one run each need their own copy, and a
        # reconnecting client is a new socket and gets told again.
        self._terminal_delivered: set[WebSocket] = set()
        # Sockets whose user may not see cost; _send_one strips it for them.
        self._cost_hidden: set[WebSocket] = set()
        self._broker = broker
        self._pubsub = None
        self._subscriber_task: asyncio.Task | None = None
        # The loop that accepted this process's sockets -- the server's main
        # loop. Only it may write to them; set by connect().
        self._socket_loop: asyncio.AbstractEventLoop | None = None

    def set_broker(self, broker) -> None:
        """Attach the Redis broker (called at app startup)."""
        self._broker = broker

    @property
    def _enabled(self) -> bool:
        return self._broker is not None and self._broker.enabled

    async def startup(self) -> None:
        """Start the subscriber loop when the broker is enabled. One channel
        carries every run's events; see EVENTS_CHANNEL for why not a pattern."""
        if not self._enabled:
            return
        client = await self._broker.async_client()
        self._pubsub = client.pubsub()
        await self._pubsub.subscribe(EVENTS_CHANNEL)
        self._subscriber_task = asyncio.create_task(self._subscribe_loop())

    async def shutdown(self) -> None:
        if self._subscriber_task:
            self._subscriber_task.cancel()
            try:
                await self._subscriber_task
            except asyncio.CancelledError:
                pass
            self._subscriber_task = None
        await self._close_pubsub()

    async def _close_pubsub(self) -> None:
        if self._pubsub is not None:
            try:
                await self._pubsub.aclose()
            except Exception:
                logger.warning("pubsub close failed", exc_info=True)
            self._pubsub = None

    async def _subscribe_loop(self) -> None:
        """Receive published events and fan them out to local sockets.

        Polls with a short timeout instead of a blocking listen(): redis-py 8
        defaults socket_timeout to 5s, so an idle listen() raised TimeoutError
        after 5s on prod (#960). And an error never ends the loop -- it drops
        the subscription and resubscribes, since nothing else would restart it
        and every brokered event depends on it."""
        while True:
            try:
                if self._pubsub is None:
                    self._pubsub = (await self._broker.async_client()).pubsub()
                    await self._pubsub.subscribe(EVENTS_CHANNEL)
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=SUBSCRIBER_POLL_SECONDS)
                if message is not None:
                    envelope = json.loads(message["data"])
                    await self._deliver_local(envelope["run_id"], json.dumps(envelope["event"]))
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("subscriber loop error; resubscribing", exc_info=True)
                await self._close_pubsub()
                await asyncio.sleep(SUBSCRIBER_RETRY_SECONDS)

    async def _send_one(self, websocket: WebSocket, message: str, *,
                        is_terminal: bool) -> bool:
        """Send one serialized message to one socket. False => drop the socket.

        The single write path, shared by the broadcast (_deliver_local) and the
        point-to-point replay (send_direct), so the terminal dedup cannot hold
        on one route and not the other. For a terminal event the mark is
        claimed before the send is awaited, not after: the replay and a live
        broadcast can run concurrently, and if both passed the membership
        check before either marked, they could both await send_text and both
        deliver (#657 review, thread 6, interleaved case).
        """
        if is_terminal:
            if websocket in self._terminal_delivered:
                logger.debug("Terminal event already delivered to this socket; skipping")
                return True
            self._terminal_delivered.add(websocket)
        if websocket in self._cost_hidden:
            message = _without_cost(message)
        try:
            await websocket.send_text(message)
        except Exception:
            if is_terminal:
                self._terminal_delivered.discard(websocket)
            return False
        return True

    async def _deliver_local(self, run_id: str, message: str) -> None:
        """Send a serialized message to this worker's sockets for run_id."""
        local = self.connections.get(run_id)
        if not local:
            return
        is_terminal = _event_name(message) in _TERMINAL_EVENTS
        disconnected = set()
        for websocket in list(local):
            if not await self._send_one(websocket, message, is_terminal=is_terminal):
                disconnected.add(websocket)
        for ws in disconnected:
            self.disconnect(run_id, ws)

    async def send_direct(self, run_id: str, websocket: WebSocket, event: dict) -> None:
        """Deliver one event to one socket, bypassing the broadcast.

        Used for the WebSocket endpoint's terminal-status replay on connect:
        the other sockets on this run already saw it. Stamped and deduped
        exactly like a broadcast event, so a replay that races the live
        terminal event does not double-report the run's end.
        """
        message = json.dumps(_stamp(event))
        is_terminal = _event_name(message) in _TERMINAL_EVENTS
        if not await self._send_one(websocket, message, is_terminal=is_terminal):
            logger.info("Direct send for run %s failed; dropping socket", run_id)
            self.disconnect(run_id, websocket)

    async def connect(self, run_id: str, websocket: WebSocket, *, hide_cost: bool = True):
        """Register a new WebSocket connection. ``hide_cost`` defaults to True
        so a caller that forgets it fails closed."""
        await websocket.accept()
        self._socket_loop = asyncio.get_running_loop()
        if hide_cost:
            self._cost_hidden.add(websocket)
        if run_id not in self.connections:
            self.connections[run_id] = set()
        self.connections[run_id].add(websocket)

    def disconnect(self, run_id: str, websocket: WebSocket):
        """Remove a WebSocket connection."""
        # Drop the dedup mark with the socket, or the set grows for the life of
        # the process and a reused object could inherit another socket's mark.
        self._terminal_delivered.discard(websocket)
        self._cost_hidden.discard(websocket)
        if run_id in self.connections:
            self.connections[run_id].discard(websocket)
            if not self.connections[run_id]:
                del self.connections[run_id]

    async def emit(self, run_id: str, event: dict):
        """Broadcast an event. Publishes via Redis when enabled, else delivers
        directly to this process's local connections, on the loop that owns
        them (see _deliver_on_socket_loop)."""
        _stamp(event)

        if self._enabled:
            # Publish only; the subscriber loop delivers to local sockets
            # (including this worker's). Never write to sockets from the
            # producer side when brokered -- it may be the wrong loop.
            self._broker.publish_event(run_id, event)
            return

        # Nobody watching this run on this process: skip the hop to the socket
        # loop (most LOG events of an unwatched run end here).
        if not self.connections.get(run_id):
            return
        message = json.dumps(event)
        loop = self._socket_loop
        if loop is None or loop is asyncio.get_running_loop():
            # Already on the socket loop; awaiting a hop back onto it would
            # deadlock, so write directly.
            await self._deliver_local(run_id, message)
        else:
            await self._deliver_on_socket_loop(loop, run_id, message)

    async def _deliver_on_socket_loop(self, loop: asyncio.AbstractEventLoop,
                                      run_id: str, message: str) -> None:
        """Deliver from another thread's loop by running _deliver_local on the
        loop that owns the sockets, and wait for it (up to
        CROSS_LOOP_EMIT_WAIT_SECONDS) without blocking the caller's loop.

        Never raises: the caller is a pipeline run, and an event it cannot
        deliver -- the server loop already gone at shutdown (#1095 drain), or a
        client too slow to take it -- must not fail the run. A delivery still
        running at the timeout is left to finish, not cancelled mid-write.

        The delivery task starts from an empty context, not a copy of the
        run's. The run's context can carry its stdout capture (_capture_var),
        and app logging writes through sys.stdout: a log line from inside the
        delivery would be routed into the capture, whose synchronous log
        emit then blocks the server loop for up to 2s per line.
        """
        coro = self._deliver_local(run_id, message)
        try:
            future = contextvars.Context().run(
                asyncio.run_coroutine_threadsafe, coro, loop)
        except RuntimeError:
            coro.close()
            logger.info("Event for run %s dropped: the socket loop is closed", run_id)
            return
        done, _ = await asyncio.wait({asyncio.wrap_future(future)},
                                     timeout=CROSS_LOOP_EMIT_WAIT_SECONDS)
        if not done:
            logger.warning("Event for run %s not delivered within %.1fs; not waiting further",
                           run_id, CROSS_LOOP_EMIT_WAIT_SECONDS)
        elif future.cancelled():
            logger.warning("Event delivery for run %s was cancelled", run_id)
        elif future.exception() is not None:
            logger.warning("Event delivery for run %s failed", run_id,
                           exc_info=future.exception())

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
                                provider: str = "bedrock"):
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
