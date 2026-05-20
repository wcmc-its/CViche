# Issue #4 — Redis broker for the real-time pipeline viewer

**Status:** design only. No code in this PR. Implementation deferred.
**Issue:** [wcmc-its/CViche#4](https://github.com/wcmc-its/CViche/issues/4)
**Related:** PR #2 (current mitigation: `--workers 1` in `docker-compose.prod.yml`).

## What's broken today

Two pieces of pipeline state are kept in process-local Python globals:

| State | Where | Used by |
|---|---|---|
| WebSocket connection map | `event_emitter.connections: Dict[run_id, Set[WebSocket]]` in `web_interface/backend/app/pipeline/event_emitter.py` (singleton at module scope) | `emit_*` calls from the orchestrator; `connect` / `disconnect` from `app/api/websocket.py` |
| Cancellation flags | `_cancelled_runs: set` in `web_interface/backend/app/pipeline/orchestrator.py` (module-scoped) | `cancel_run` / `is_cancelled` / `clear_cancelled` |

With `uvicorn --workers 1` and a single container replica, both globals are shared by everything that matters. The product works.

With `--workers N > 1` or `replicas > 1`, the globals are per-worker / per-replica:

- **Live events never arrive.** Browser opens WS to load balancer → LB routes to worker A → `event_emitter.connect(run_id, ws)` registers in A's local dict. Browser then POSTs to start a run → LB routes to worker B → orchestrator runs in B and calls `event_emitter.emit_log(run_id, ...)` → B's local dict has no entry for `run_id`, the early `return` at `event_emitter.py:31` fires, the message is dropped. The run still completes; DB-polled status still works; the live viewer (the product's headline feature) silently goes blank.
- **Cancellation hits the wrong process.** Browser DELETEs the run → LB routes to worker C → `cancel_run(run_id)` writes to C's `_cancelled_runs` → B's orchestrator polls B's set, sees nothing, keeps running. The run completes to the end and bills the user for it. UI shows it as cancelled (DB row was updated), pipeline ignored the signal.

PR #2's mitigation is `command: ["--workers", "1"]` in `docker-compose.prod.yml`. That keeps a single replica functional but means we cannot horizontally scale the live viewer without breaking it.

## What the fix has to do

Move the two pieces of cross-worker state out of process memory:

1. **A fan-out for events.** When worker B emits an event for `run_id`, every worker that has a local WebSocket subscribed to `run_id` needs to receive it. Workers that have no subscribers should ignore the message cheaply.
2. **A cross-worker cancellation signal.** When worker C receives a `DELETE /runs/{id}`, the orchestrator worker B must learn about it on its next poll loop (today: a synchronous `is_cancelled()` check at each stage boundary).

Stickiness alone isn't enough: the LB can pin WS → worker A and POST → worker A and DELETE → worker A, but a worker restart drops the WS and the next reconnect lands somewhere else, so we'd still need a way to recover live updates without the operator killing the run.

## Design

Use Redis Pub/Sub. It is the smallest piece of infrastructure that solves both problems, and Redis is already what the prompt-cache fallback memory store would reach for if we ever needed it. Both Bedrock and OpenAI are external services, so adding a second managed service does not move the per-CV cost meaningfully.

### Channels

| Channel | Publisher | Subscriber | Payload |
|---|---|---|---|
| `cviche:run:{run_id}:events` | The worker running the orchestrator | Every worker; each worker filters by local WS subscriptions | The same dict that `event_emitter.emit(run_id, event)` already builds, JSON-serialised |
| `cviche:run:{run_id}:cancel` | Any worker receiving `DELETE /runs/{id}` | The worker running the orchestrator | `{"run_id": "..."}` (cancellation is a yes/no signal; no other payload needed) |

We could fold cancellation into the events channel with a `{"event": "CANCEL"}` message, but a separate channel keeps the cancel subscriber narrow — a worker that owns no orchestrator runs doesn't need to subscribe.

Run-scoped channels (rather than one global channel) keep subscribe sets small and let workers that aren't watching a given run skip its traffic entirely. Redis pub/sub fan-out is cheap at our message rates (~10/s during stage 4) but there's no reason to be sloppy.

### EventEmitter rewrite

```python
# event_emitter.py — sketch, not final code

class EventEmitter:
    def __init__(self, redis_client):
        self._redis = redis_client
        # Local map remains the source of truth for "which WSs do I have
        # for this run_id on this worker?". It is no longer the
        # publish/subscribe boundary.
        self.connections: Dict[str, Set[WebSocket]] = {}
        self._subscriber_task: asyncio.Task | None = None
        self._pubsub = None

    async def startup(self):
        # Single per-worker subscriber loop that fans out to local WSs.
        self._pubsub = self._redis.pubsub()
        self._subscriber_task = asyncio.create_task(self._subscribe_loop())

    async def shutdown(self):
        if self._subscriber_task:
            self._subscriber_task.cancel()
        if self._pubsub:
            await self._pubsub.close()

    async def connect(self, run_id: str, ws: WebSocket):
        await ws.accept()
        first_local_subscriber = run_id not in self.connections
        self.connections.setdefault(run_id, set()).add(ws)
        if first_local_subscriber:
            # Subscribe Redis once per run_id per worker.
            await self._pubsub.subscribe(f"cviche:run:{run_id}:events")

    def disconnect(self, run_id: str, ws: WebSocket):
        if run_id not in self.connections:
            return
        self.connections[run_id].discard(ws)
        if not self.connections[run_id]:
            del self.connections[run_id]
            # Last local subscriber gone -- drop Redis subscription too.
            asyncio.create_task(
                self._pubsub.unsubscribe(f"cviche:run:{run_id}:events")
            )

    async def emit(self, run_id: str, event: dict):
        if "timestamp" not in event:
            event["timestamp"] = datetime.now().isoformat()
        # Publish only -- never write to local WSs from the producer side.
        # All delivery goes through the per-worker subscriber loop, so a
        # worker that is BOTH the producer AND a subscriber still
        # delivers exactly once to each local WS.
        await self._redis.publish(
            f"cviche:run:{run_id}:events",
            json.dumps(event),
        )

    async def _subscribe_loop(self):
        async for message in self._pubsub.listen():
            if message["type"] != "message":
                continue
            channel = message["channel"].decode()
            run_id = channel.split(":")[2]
            local_wss = self.connections.get(run_id)
            if not local_wss:
                continue
            text = message["data"].decode()
            disconnected = set()
            for ws in local_wss:
                try:
                    await ws.send_text(text)
                except Exception:
                    disconnected.add(ws)
            for ws in disconnected:
                self.disconnect(run_id, ws)
```

The interesting property: producers `publish`, subscribers `send_text`. The orchestrator-worker is a producer; the WS-worker is a subscriber. When the same worker happens to be both (single-replica case), the local message round-trips through Redis. That is a few milliseconds extra and removes a whole class of "is this worker the producer or the consumer" branching.

`emit_step_start` / `emit_cost_update` / etc. do not change shape — they just go through the new `emit`.

### Cancellation rewrite

Replace the module-level `_cancelled_runs: set` with two pieces:

1. **Producer side** (HTTP handler / admin tool):
   ```python
   async def cancel_run(run_id: str):
       await redis.publish(f"cviche:run:{run_id}:cancel", json.dumps({"run_id": run_id}))
       # Also stash a short-TTL key so a late-arriving orchestrator (one
       # that subscribes after publish) picks it up. 5 min covers the
       # longest plausible cancel-vs-stage-boundary race.
       await redis.setex(f"cviche:run:{run_id}:cancelled", 300, "1")
   ```

2. **Consumer side** (orchestrator):
   ```python
   class PipelineOrchestrator:
       async def is_cancelled(self) -> bool:
           # Cheap check on each stage boundary.
           return bool(await self._redis.exists(f"cviche:run:{self.run_id}:cancelled"))
   ```

`is_cancelled` becomes `async`. The orchestrator already runs in an async context, so the change is mechanical. The 1-RTT cost is paid only at stage boundaries (12 stages per run), not per-LLM-call.

We do NOT need the orchestrator to subscribe to the cancel channel — polling at stage boundaries is enough for the product's current semantics (cancel takes effect at the next stage, not mid-stage). If we ever want mid-stage cancellation, add a SUBSCRIBE to the orchestrator too.

The orchestrator already calls `clear_cancelled(run_id)` at run completion; that becomes a `DEL cviche:run:{run_id}:cancelled`. The 5-minute TTL is a safety net for the case where the orchestrator dies mid-run and never gets to clear.

### docker-compose / k8s wiring

**Dev `docker-compose.yml`:** add a `redis:7-alpine` service, expose `6379` to the backend.

```yaml
redis:
  image: redis:7-alpine
  ports: ["6379:6379"]
  healthcheck:
    test: ["CMD", "redis-cli", "ping"]

backend:
  environment:
    CVICHE_REDIS_URL: "redis://redis:6379/0"
  depends_on:
    redis:
      condition: service_healthy
```

**Prod `docker-compose.prod.yml`:** Redis lives outside the compose stack (managed ElastiCache / AWS MemoryDB). Backend reads `CVICHE_REDIS_URL` from the deployment env.

**k8s:** point at the cluster's existing Redis if there is one; otherwise add a tiny single-node Redis StatefulSet. Pub/Sub has no durability requirement, so a managed clustered Redis is overkill — a single instance is fine and is the cheapest tier of every managed offering.

### Code-change shape

- New: `app/pipeline/redis_broker.py` — thin wrapper around `redis.asyncio.Redis` with `startup` / `shutdown` hooks tied to FastAPI's lifespan. Connection pool sized to 1 (the subscriber) + a handful for publish.
- Modified: `app/pipeline/event_emitter.py` — see sketch above. Module-global `event_emitter = EventEmitter()` becomes `event_emitter = EventEmitter(redis)` once lifespan provides the client.
- Modified: `app/pipeline/orchestrator.py` — replace `_cancelled_runs`, `cancel_run`, `is_cancelled`, `clear_cancelled` with Redis-backed equivalents. Update every `if is_cancelled(self.run_id):` call site to `if await self.is_cancelled():`. There are ~5 such sites.
- Modified: `app/api/runs.py` (or wherever DELETE lives) — `cancel_run` becomes async.
- Modified: `web_interface/backend/requirements.txt` — add `redis>=5.0` (the async client lives in the same package).
- Modified: `docker-compose.yml`, `docker-compose.prod.yml`, `.env.example` — Redis service / env var.
- Modified: `docker-compose.prod.yml` — drop the `--workers 1` constraint; raise to a reasonable per-container default (`--workers 4`) and remove the `(see issue #4)` comment.

### What this does NOT change

- Frontend WebSocket contract: identical messages, identical shapes.
- DB schema: no migration.
- Run lifecycle, retry semantics, prompt caching, cost reporting: unchanged.

## Test plan

- **Unit:** mock `redis.asyncio.Redis`. Test that `emit` publishes; that `connect` subscribes; that `is_cancelled` reads the right key. Pure plumbing.
- **Integration:** start a real `redis:7-alpine` container (testcontainers-python). Two `EventEmitter` instances connected to it. Worker A: register a WS for `run_id`. Worker B: emit. Assert the message arrives on A. Then: Worker B: `cancel_run`. Worker A: `is_cancelled` returns True. Worker A: `clear_cancelled`. Worker B: `is_cancelled` returns False.
- **E2E:** `docker compose -f docker-compose.yml -f docker-compose.prod.yml up --scale backend=3 -d`. Start a run from the UI; verify the live viewer keeps painting throughout; cancel mid-run; verify the orchestrator exits at the next stage boundary.

## Open questions

- **Do we already have Redis somewhere in the stack?** If yes, reuse it; the design above assumes a fresh dependency. If no, the operational add is small (one container in compose, one CloudFormation/Terraform stanza in prod).
- **Is "cancel at next stage boundary" the contract we want, or should cancellation be checked inside long-running stages (stage_4 is 30-60+ LLM calls)?** The current code is stage-boundary-only and we have not seen this be a problem in practice. The Redis design supports either; the in-stage variant just calls `is_cancelled()` in the per-batch loop instead of only between stages.
- **Backpressure on the events channel.** A WebSocket consumer that pauses (e.g., a backgrounded browser tab) does not slow the orchestrator down because the publish path doesn't block on slow subscribers — but the subscriber loop will queue messages in memory if WS sends are slow. For our message rates (~10/s peak), this is fine. If it ever isn't, drop on the floor with a counter rather than buffering unbounded.

## Rough size

Implementation: half a day to wire it, half a day to test it, plus the integration-test harness. One PR. Reviewer-friendly because the diff in each file is small.

Operational add: one Redis container or one ElastiCache cluster, depending on environment.
