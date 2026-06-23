# Design: Run-event delivery on Redis Streams (Phase 1)

Status: proposed, for the checkpoint. Author: Claude (for Paul). Owner of build: TBD (Mahender offered a POC).

## Scope

Migrate **real-time run-event delivery** from Redis Pub/Sub to Redis Streams. Nothing else.

Explicit non-goals (deferred to a later phase):
- Moving pipeline **execution** off the web pods to Taskiq workers. That is the larger effort and stays a separate phase.
- Therefore this does **not** change the per-pod concurrency cap or the "message on the third pipeline" (`CVICHE_MAX_CONCURRENT_RUNS`). That message is admission control, a different subsystem; it is unaffected by an event-broker swap. Calling this out so it is not mistaken for a fix.

## Current state (grounded)

- `event_emitter.emit()` builds an event dict and, when a broker is enabled, calls `broker.publish_event()` which does `PUBLISH cviche:run:{id}:events <json>` (`redis_broker.py:65`).
- Each web pod runs one subscriber: `psubscribe cviche:run:*:events`, and the loop fans every message out to whatever WebSocket connections that pod holds locally (`event_emitter.py:51,69,89`).
- When `CVICHE_REDIS_URL` is unset the broker is disabled and `emit()` delivers straight to local sockets (single-worker behavior). This degrade path must survive unchanged.
- Cancellation is independent: a TTL key `cviche:run:{id}:cancelled` plus the authoritative DB-status read at stage boundaries. Not part of this change.

## Problem with Pub/Sub

Pub/Sub is fire-and-forget. Any event published while a client's socket is momentarily disconnected (network blip, pod rotation, client navigation) is **dropped** — there is no buffer to replay from. Today the frontend papers over this with DB status polling, which recovers coarse run/step status but not the granular LOG / PROGRESS / COST_UPDATE stream. This is the class of "the UI missed an update" symptom.

## Design

### 1. Stream per run, not a global channel

Key: `cviche:run:{run_id}:stream` (distinct from the cancel key; per-run so cleanup is a single `DEL`).

Publish (sync side, from the orchestrator thread, replacing `publish_event`):

```
XADD cviche:run:{id}:stream MAXLEN ~ 1000 * event <json>
```

`MAXLEN ~ 1000` caps each run's buffer (approximate trim is cheaper). A full run emits a few hundred events, so 1000 covers the whole run while bounding memory.

### 2. Fan-out = independent per-pod XREAD, NOT a consumer group

This is the one decision that is easy to get wrong. Every pod must see every event, because the producer does not know which pod holds the client's socket. A **consumer group** delivers each event to exactly one consumer in the group — that breaks fan-out. So each pod runs its own blocking reader with no group:

```
XREAD BLOCK 5000 STREAMS <discovered run streams> <last-id-per-stream>
```

Each pod tracks its own last-delivered id per stream in memory. This is "pub/sub semantics plus a replay buffer."

Stream discovery: the current code uses one pattern subscription (`cviche:run:*:events`) for all runs. Streams have no pattern read. Options:
- (a) Keep a lightweight index: `XADD` also publishes the run_id onto a shared discovery stream / set that each pod reads to learn which run streams to `XREAD`.
- (b) Only `XREAD` streams for runs this pod actually has sockets for (it knows from `self.connections`), and start reading from "now" when a socket connects, replaying from the client's last id if provided. (b) is simpler and scales with active viewers, not total runs — preferred.

### 3. Reconnect resume (required, not optional)

The replay buffer is the entire point; it only delivers value if the client can resume. Protocol:
- Server sends each event with its stream id (the `XADD`-assigned `id`).
- The WS client remembers the last id it received.
- On reconnect, the client sends `?since=<last-id>` (or a first WS message); the server does `XREAD ... STREAMS <stream> <since-id>` to replay everything after it, then tails live.
- No `since` (fresh open) starts from `$` (live tail) or from `0` if we want full history on first load (decide below).

Without this step the swap is Pub/Sub with extra machinery and should not ship.

### 4. Lifecycle

- Stream is created lazily by the first `XADD` of a run.
- Delete on terminal: when the orchestrator emits `RUN_COMPLETE` / `RUN_FAILED` / `RUN_CANCELLED`, schedule a `DEL` (or short `EXPIRE`, e.g. 1h, so a just-finished viewer can still replay the tail). A short TTL is safer than immediate `DEL` for the reconnect-on-completion case.
- The orphan/stale paths already exist for DB rows; the stream TTL means an abandoned run self-cleans regardless.

### 5. Flag + degrade (unchanged contract)

Gate stays `CVICHE_REDIS_URL`. Unset -> disabled -> `emit()` delivers to local sockets exactly as today. Reachable-but-erroring -> best-effort: log and fall back to local delivery, never fail the run (mirror today's `publish_event` try/except).

### 6. Cancellation: unchanged

Keep the DB-status read at stage boundaries as the authoritative cancel signal and the TTL cancel key as the fast nudge. The stream is job *event delivery*, not in-flight cancellation. (Holding the line on the earlier disagreement about dropping the DB check.)

## Code touch points

- `redis_broker.py`: `publish_event` -> `XADD`; add a `read_events(stream, last_id, block)` helper; bump/keep cancel logic as-is.
- `event_emitter.py`: replace the `psubscribe` loop with per-pod `XREAD` reader(s); thread the event id through to the socket payload; accept a client `since` id on connect and replay.
- WS endpoint (`websocket.py`): accept `?since=` / first-message resume; echo each event's id to the client.
- Frontend WS hook (`usePipelineRun`): remember last event id, send it on reconnect.
- No DB, schema, or admin-config change.

## Rollout / rollback

- Ships behind the existing flag; identical no-Redis behavior, so it merges and runs unchanged until Redis is in play.
- Parallel-safe: pods on the new code `XREAD`; a pod still on old code `psubscribe`s. They do not interoperate, so cut over both producer and consumers together (single image roll), but the flag-off path is always safe.
- Rollback = redeploy previous image; streams left behind self-expire via MAXLEN + TTL.

## Test plan

- Unit: `XADD` then `XREAD` from `0` returns events in order; from a mid id returns only the tail (replay); MAXLEN trims.
- Disabled (`CVICHE_REDIS_URL` unset): `emit()` still delivers locally; no Redis calls.
- Fail-open: `XADD`/`XREAD` raising does not bubble into the run.
- Reconnect: connect, receive N events, drop, reconnect with `since`, assert no gap and no duplication beyond the `since` boundary.
- Cross-pod: event `XADD`ed by pod A reaches a socket held by pod B.

## Open questions for the checkpoint

1. Fresh-open behavior: live-tail only (`$`), or replay full run history (`0`) so opening a completed/in-progress run shows everything? (Leaning: replay from `0` capped by MAXLEN, so the viewer is self-sufficient without the status-poll backfill.)
2. Stream discovery: per-viewer XREAD (option b) vs a discovery index (option a). (Leaning b.)
3. Post-completion stream retention: immediate `DEL` vs short `EXPIRE` (e.g. 1h). (Leaning EXPIRE.)
4. Who builds it — Mahender's POC vs Claude — and does it target `dev` behind the flag first.
