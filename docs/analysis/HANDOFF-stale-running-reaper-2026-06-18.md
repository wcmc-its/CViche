# Handoff — auto-resolve stuck `running` runs (periodic, heartbeat-based stale-run reaper, #116)

**Status:** ready to implement, but NOT a quick win. Needs a progress heartbeat to be multi-replica safe; plan for an Alembic migration. Do this in a fresh session.
**Raised by:** Mahender, 2026-06-18 — a run wedged for ~16h with the "may be stuck" banner showing (see repro below).
**Type:** resilience / failure-surfacing. Complements #173 (which reaps never-started `created` orphans — a *different* failure).

## The concrete repro

Run `ZTKKUQ` (a sample CV, `/app/data/sample_cvs/word/ZTKKUQ.docx`) sat at **status=`running`**, Stage 2 Entry Extraction, **12% progress, ~16h elapsed, 201 output tokens, $0.006**. The client stall watchdog correctly fired ("This run is taking much longer than expected and may be stuck"), but nothing server-side ever resolved it. `ZTKKUQ` is also one of the six S3 folder IDs from the earlier orphan screenshots, so a chunk of that "6 folders" backlog is stuck/abandoned sample-CV runs.

## What exists today (and the gap)

- **`reconcile_stale_runs(db)`** (`web_interface/backend/app/services/run_service.py:80-152`) already marks `running` runs older than `CVICHE_STALE_RUN_MINUTES` (default 60) as failed (via `_mark_run_failed`), with a flag-gated auto-retry branch (#145, off by default).
- It is wired **only at startup** in the lifespan (`web_interface/backend/app/main.py:165`). So a stuck run is resolved **only when a pod restarts**. A long-lived pod that doesn't restart leaves the run hanging indefinitely (the 16h case).
- The **client stall watchdog** (`web_interface/frontend/src/hooks/usePipelineRun.tsx`: `STALL_NO_PROGRESS_MS = 5min`, `STALL_ELAPSED_MULTIPLIER = 3`) only *warns* the user (Cancel / Restart with file / Contact support) — it changes nothing server-side.
- There is **no scheduler / periodic task** in the backend (confirmed: no APScheduler/celery; the only `asyncio.create_task` is the Redis event subscriber in `event_emitter.py`). The lifespan runs startup work once, then `yield`s.

So the obvious fix is "run `reconcile_stale_runs` periodically, not just at startup." It is NOT that simple — see the trap.

## The trap: a naive periodic age-based sweep is multi-replica unsafe

`reconcile_stale_runs` decides staleness by **`started_at` age** (run started > 60 min ago). That is fine *at startup* (any `running` run is from a dead previous instance). But run **periodically across 2–4 replicas** it becomes a race: pod A's sweep will mark pod B's **genuinely in-flight** run as `failed` the moment it crosses 60 min, while pod B is still running it and writing step updates. A legitimately slow run (notably a **very large / 100-page CV**, which the product owner is explicitly worried about truncating/breaking) gets killed mid-flight.

The DB row carries no owning-pod and no progress signal, so age alone can't tell "alive but slow" from "dead/stuck."

Note: the in-process LLM/Bedrock timeouts (`_get_llm_timeout_seconds` = 180s, botocore read_timeout) mean a truly *in-process* wedge should fail within minutes — so the 16h `ZTKKUQ` case is almost certainly a **dead worker (pod recycle)** with the row left `running`, i.e. exactly the startup-reconcile case that just needs to also run *while the pod is up*. But the multi-replica false-positive risk above is what makes the safe design need a heartbeat.

## Recommended design (robust)

1. **Add a progress heartbeat.** New `Run.last_progress_at` (DateTime) column, written by the orchestrator as it makes progress — at minimum on each step transition, ideally also periodically within a long single stage (e.g. on each `call_llm` / progress emit), since a long Stage 4 has no step transitions for many minutes. This is the signal that distinguishes alive-but-slow from dead.
2. **Periodic sweep in the lifespan.** An `asyncio` task started before `yield` and cancelled on shutdown, every `CVICHE_STALE_REAP_INTERVAL_MINUTES` (default ~10–15): reap `running` runs whose `last_progress_at` is older than a *no-progress* threshold (e.g. 10–15 min), reusing `_mark_run_failed`. With a heartbeat, an alive run on any pod keeps `last_progress_at` fresh, so a sibling pod's sweep can't false-positive it — multi-replica safe.
3. **Migration.** `last_progress_at` needs a **MySQL-safe** Alembic migration — mirror `a7c3e1f90b24_add_attempt_count_to_runs.py` (explicit type + `server_default`), and heed the repo's SQLite-only-migration history (a bare `String()` once broke prod). Migrations run as the one-shot `migrate` initContainer (`alembic upgrade head`).
4. **Concurrency-slot accounting (call out, don't over-build).** `concurrency._active_runs` is a plain per-pod int with no per-run mapping, and the slot is released in `run_pipeline`'s `finally` (`runs.py`). Marking a run `failed` from the reaper does NOT release a held slot. For the dead-worker case the process restarted (counter already 0). For an alive-but-wedged worker the 180s timeouts should unwedge and release. So scope the reaper to fixing **DB/UI state** (and freeing the user) — leave slot accounting to the timeout path, and document it.

## Simpler interim (if a migration is undesirable right now)

Periodic sweep reusing the existing **age-based** `reconcile_stale_runs` but with a **generous** threshold (e.g. 120–180 min, separate config from the 60-min startup value). A run `running` for 2–3h is essentially certainly dead, so false-positives are near-zero even multi-replica, and no migration is needed. Tradeoff: a stuck run waits up to 2–3h to auto-fail (vs ~15 min with a heartbeat), and a genuinely enormous CV approaching 3h is a small residual risk. Ship this only if the heartbeat is deferred, and `log()` the coarseness.

## The task

1. Decide heartbeat (robust, migration) vs generous-age-threshold (interim, no migration). Recommend the heartbeat.
2. Implement the periodic lifespan task with clean start/cancel, a fresh DB session per tick, and per-tick exception isolation.
3. Add the reap function in `run_service.py` (heartbeat: `reap_stale_running_runs(db, no_progress_minutes)`; or reuse `reconcile_stale_runs` with the generous threshold).
4. Keep the auto-retry branch behavior identical (flag-gated off).
5. Tests: the reap function directly (seed `running` runs with fresh vs stale `last_progress_at` / age; assert only the stale ones fail, plus their still-running steps go `error`); a thin test of the loop wiring (don't let the real loop run in tests — extract the tick body).

## Acceptance criteria

- A `running` run with no progress for the threshold is auto-failed without a pod restart (resolves the `ZTKKUQ` 16h case).
- A genuinely in-flight run on another replica is NEVER failed (heartbeat keeps it alive), including a large/long CV.
- The client stall banner still fires (unchanged); the difference is the run now actually reaches a terminal state.

## Pointers

- `web_interface/backend/app/services/run_service.py:80-152` — `reconcile_stale_runs` + `_mark_run_failed` (reuse).
- `web_interface/backend/app/main.py:137-192` (lifespan), `:165` (startup reconcile call) — where the periodic task hangs.
- `web_interface/backend/app/models.py` Run (`started_at` indexed; no progress column yet); `a7c3e1f90b24_add_attempt_count_to_runs.py` — migration template.
- `web_interface/backend/app/pipeline/orchestrator.py` — where to bump `last_progress_at`.
- `web_interface/backend/app/pipeline/concurrency.py` — per-pod slot counter (the slot-release nuance).
- `web_interface/frontend/src/hooks/usePipelineRun.tsx` — the client stall watchdog (`STALL_NO_PROGRESS_MS`, `STALL_ELAPSED_MULTIPLIER`).
- Related: #173 (`created`-orphan reaper, merged/open — different failure), #145 (auto-retry, flag-gated), the LLM/stage timeouts in `src/unified_pipeline/llm_client.py`.

## Out of scope (do not bundle)

- The `created`-orphan reaper (that's #173).
- Heartbeat-driven WS reconnection / cross-loop emits (the broader #116 wishlist) — keep this focused on auto-resolving stuck `running` runs.
- Cross-pod concurrency admission (#80) — separate.
