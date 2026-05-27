# Concurrency & load readiness — current behavior, bottlenecks, and the real lever

**Status:** analysis only. No code in this doc. Records findings from a load/scaling review on 2026-05-27.
**Applies to:** the `web_interface` backend running on EKS (cluster `reciter`, nodegroup `cviche-al2023-nodes`, account `665083158573`, region `us-east-1`).
**Related:** [`issue-4-redis-broker.md`](./issue-4-redis-broker.md) — the horizontal-scaling *enabler* (moving process-local state to Redis). This doc is the *why-and-when* around it.

## TL;DR

- CViche is architected for **one long job at a time on a single pod**. It works well for sequential / lightly-overlapping use and degrades — silently — under true concurrency.
- A run executes as an **in-process FastAPI `BackgroundTask`**: no queue, no admission control, no per-pod or cross-pod cap on how many pipelines run at once.
- The first wall under load is **pod compute** (prod is **1 replica, 1 vCPU, 1 GiB, no HPA**), not the database and not the LLM provider.
- **A Bedrock quota increase is NOT the lever.** Live quotas (verified 2026-05-27) show Sonnet 4.6 at **10,000 RPM** with **no tokens-per-minute quota at all** — request rate is nowhere near binding for a sequential-call workload, and there is no TPM knob to raise.
- The real fix is **application-side**: per-pod concurrency admission control + cross-run Bedrock pacing, and (to scale past one pod) the Redis broker from issue #4.

## How a run executes today

`POST /run/{run_id}/start` (`web_interface/backend/app/api/runs.py:121-161`) flips the DB row to `running` and schedules an in-process background task:

```python
def run_pipeline():
    bg_db = SessionLocal()
    orchestrator = PipelineOrchestrator(run_id, file_path, bg_db)
    asyncio.run(orchestrator.execute())   # full 15-20 min pipeline, in-process
background_tasks.add_task(run_pipeline)
```

Consequences:

- **No queue, no concurrency cap.** Every `start` immediately spawns another full pipeline inside the web process. N users who hit "start" together = N pipelines running at once in the same pod, all competing for CPU, RAM, DB connections, and LLM bandwidth. There is no semaphore anywhere gating this, and no "system busy" response — runs just contend silently.
- **Work is tied to the web process lifecycle.** A pod restart (deploy, OOM, eviction) kills every in-flight run. This is exactly the failure the `reconcile_stale_runs` guard cleans up (`app/services/run_service.py`), and concurrency makes it more likely.
- **Stages run sequentially within a run.** The orchestrator iterates `STEP_REGISTRY` in order (`app/pipeline/orchestrator.py:348`) and imports the stage functions directly (`orchestrator.py:45-51`). There is **no intra-run parallelism**: the only `ThreadPoolExecutor` in the codebase lives in `core/legacy_extractor_orchestrator.py`, which is the **CLI** path (`core/cv_pipeline.py`), not the web path. So a single run is a steady drip of one-at-a-time LLM calls, not a burst.

## Bottleneck chain (what breaks first)

1. **Pod compute — breaks first.** Prod is `replicas: 1`, `cpu: "1"`, `memory: "1Gi"` with **requests == limits** (no burst headroom) and **no HorizontalPodAutoscaler anywhere** in `k8s/` (`k8s/base/backend/deployments.yaml`, `k8s/overlays/prod/backend-patch.yaml`). Pipeline stages are mostly LLM I/O-wait, so a single pod can overlap a couple of runs during wait windows — but the CPU bursts (doc parsing, JSON across 12 stages) and the hard 1 GiB ceiling mean 2-3 concurrent full runs will thrash and risk OOM-kill.
2. **LLM provider pacing — next.** Calls have retry/backoff (`app/pipeline/llm_client.py` → `src/unified_pipeline/llm_client.py:106-140`) but **no local concurrency limiter or token bucket**. Concurrent runs fan out concurrent request streams against the shared account. (See the Bedrock section — for *this* model and workload this is softer than it looks, but it is the next thing to give once pods scale.)
3. **DB connections — softer ceiling.** SQLAlchemy defaults give ~5 + 10 overflow = **15 connections per worker** (prod runs `--workers 1`). Runs are long but DB writes are brief, so 15 connections serve a fair number of overlapping runs; under load you would see request threads block on pool checkout before exhausting anything else.
4. **Process-local state — bites the moment you scale to fix #1.** The WebSocket connection map and the cancellation flag are module-global (`event_emitter.py`, `orchestrator.py`). This is why prod is pinned to `--workers 1`. Scaling replicas/workers without a shared bus silently breaks the live viewer and cross-pod cancel. **This is issue #4** — see [`issue-4-redis-broker.md`](./issue-4-redis-broker.md).

## Bedrock quota reality (verified 2026-05-27)

The pipeline runs **every** live stage on `us.anthropic.claude-sonnet-4-6` (the US cross-region inference profile), configured in `src/unified_pipeline/config/llm_config.yaml`. Prompt caching is on. Calls are sequential within a run (above).

Live Service Quotas for this account/region (`aws service-quotas list-service-quotas --service-code bedrock --region us-east-1`):

| Quota | Code | Value | Adjustable |
|---|---|---|---|
| Sonnet 4.6 — requests/min (global cross-region) | `L-F6E116D7` | **10,000 RPM** | yes |
| Sonnet 4.6 — **tokens/min** | — | **no such quota exists** | — |

Reading:

- **RPM is already enormous relative to the workload.** A single run sustains maybe tens of requests/minute (sequential calls). At 10,000 RPM the request-rate ceiling supports on the order of *hundreds* of concurrent runs. Request rate is not the bottleneck.
- **Sonnet 4.6 has no adjustable TPM quota.** The "tokens-per-minute will bind under load" concern has **no self-service lever** for this model (Opus 4.6 *does* expose TPM quotas, e.g. `L-0AD9BBE8` at 3M TPM — so this is a real per-model difference, not a visibility gap). In practice AWS is not hard-capping Sonnet 4.6 tokens/min via an adjustable quota.
- **Therefore: do not file a quota increase.** Raising a 10,000 RPM limit that isn't being approached, on a model with no TPM cap, fixes nothing.

Note the profile/quota naming: the app uses the `us.` cross-region profile, while the only listed RPM quota is the **Global** cross-region one. Both `us.` and `global.` profiles are `ACTIVE` in this account. Worth aligning if metering ever matters, but not a load blocker.

### Dead code to be aware of

`src/unified_pipeline/core/async_rate_limiter.py` implements a proper TPM/RPM token bucket — but it is **imported nowhere** and was written for the old OpenAI path. It is not active. If we build the limiter below, it is a useful reference, not a wired-in component.

## Recommendations (priority order)

1. **Per-pod admission control (highest leverage, smallest change).** A semaphore capping concurrent runs per pod; return `429` / "queued" when full. Converts silent thrash into a graceful, observable limit. Slots in at `start_run`.
2. **Cross-run Bedrock pacing.** A shared limiter (concurrency + simple token budget) at the single `call_llm` chokepoint so concurrent runs don't trigger synchronized retry/backoff storms. Resurrect the `async_rate_limiter` idea, targeted at Bedrock.
3. **Retry hygiene.** Add jitter to the backoff so concurrent runs don't retry in lockstep; ensure a long-throttled run isn't silently reaped at the 60-min stale cutoff (distinguish "slow/throttled" from "dead").
4. **Shed token pressure if needed.** `llm_config.yaml` already has a commented-out block to drop terminal stages (`stage_5b/5c/5d/6`) to Claude Haiku 4.5.
5. **To scale past one pod:** implement issue #4 (Redis broker) first, then raise `--workers` / `replicas` and add an HPA. Order matters — scaling before #4 breaks the live viewer and cross-pod cancel.

## What this is NOT

- Not a request to increase any AWS quota (the data says it's unnecessary).
- Not a re-architecture. Items 1-3 are contained changes around existing chokepoints.
- Not a change to run lifecycle, retry semantics, prompt caching, or cost reporting.

## Verifying the throttle claim (optional, read-only)

To confirm prod has never actually been throttled (rather than inferring it from quotas), pull the Bedrock `InvocationThrottles` CloudWatch metric for the relevant period before investing in pacing work.
