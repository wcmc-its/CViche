"""Per-pod admission control for in-process pipeline runs.

Applies only when CVICHE_DISPATCH_MODE is ``in_process`` (the default, and what
prod runs today). There a run executes as an in-process background task (see
runs.py:start_run): the full 15-20 min pipeline runs inside the web process,
so the pod's own CPU and memory are what a burst of starts would exhaust.

This module is the gate: a process-global counter of currently-executing runs,
bounded by CVICHE_MAX_CONCURRENT_RUNS. The endpoints that launch a pipeline
acquire a slot before scheduling the background task and release it when the
task finishes; when the pod is at capacity, new starts are rejected with 429 so
the caller can retry rather than pile on.

Scope is per-pod: the counter is a plain in-process variable, and the resource
it protects -- this pod's CPU and memory -- is itself per-pod. Behind a
multi-replica backend (prod's HPA runs 2-4 pods) that has two consequences
(#527): there is no cluster-wide ceiling (effective admission is replicas x
cap, and it rises as the HPA scales out), and a start can 429 on a full pod
while another pod has a free slot -- the ALB, not the gate, picks the pod.

In ``queue`` mode (dev since #701) none of this gates a start: the backend
hands the run to the Valkey queue, and the cviche-worker replica count is the
global run ceiling (one run per worker pod; see k8s/base/worker/deployment.yaml).
The flag-gated auto-retry reaper (run_service._launch_resume, #145) also takes
a slot here before relaunching a run in-process.
"""
import asyncio
import logging
import threading
from collections import Counter
from typing import Literal

from app.config_loader import get_config

logger = logging.getLogger(__name__)

# The two run-dispatch strategies (#701): today's per-pod BackgroundTask, or
# handing the run to a Valkey-backed queue worker. Lives here, not in
# run_queue.py (mrj4001 review, run_queue.py point 10): this is routing
# policy that gates start_run/retry_step admission the same way
# get_max_concurrent_runs does, and both knobs are read from the same "llm"
# config section.
DispatchMode = Literal["in_process", "queue"]
_VALID_DISPATCH_MODES: tuple[DispatchMode, ...] = ("in_process", "queue")

# Concurrent full-pipeline runs allowed on a single pod. Sized for the
# 2 vCPU / 2Gi backend pod both k8s overlays set: pipelines are LLM-I/O-bound
# (five concurrent runs over three pods measured ~1.5% of the CPU limit and
# ~17% of the memory limit, 2026-07-29, #527), so memory is the binding limit.
# Kept equal to CVICHE_MAX_CONCURRENT_RUNS in k8s/overlays/{dev,prod}/
# backend-patch.yaml, so a pod whose env var goes missing keeps the same cap.
DEFAULT_MAX_CONCURRENT_RUNS = 3

# How often wait_for_drain re-checks whether this pod's runs have finished.
DRAIN_POLL_SECONDS = 5

# Per-pod state, not per-run state (CODING_STANDARDS §4.1): which runs hold a
# slot on this pod, and whether the pod is shutting down. A Counter rather than
# a set because two concurrent starters of the same run both hold a slot until
# the loser of claim_run_as_running releases its own.
_lock = threading.Lock()
_active_run_ids: Counter[str] = Counter()
_draining = False


def get_max_concurrent_runs() -> int:
    """Read the per-pod concurrency cap from CVICHE_MAX_CONCURRENT_RUNS."""
    try:
        max_concurrent_runs, _ = get_config("llm","CVICHE_MAX_CONCURRENT_RUNS",default=DEFAULT_MAX_CONCURRENT_RUNS)
        value = int(max_concurrent_runs) 
    except (TypeError, ValueError):
        return DEFAULT_MAX_CONCURRENT_RUNS
    # A non-positive cap would wedge the pod (no run could ever start); treat it
    # as the default rather than silently disabling all runs.
    return value if value > 0 else DEFAULT_MAX_CONCURRENT_RUNS


def try_acquire_slot(run_id: str) -> bool:
    """Atomically reserve a run slot for ``run_id``. Returns True if a slot was
    free (caller must call release_slot(run_id) when the run finishes), False
    if the pod is at capacity or draining for shutdown and the caller should
    reject the request."""
    max_concurrent = get_max_concurrent_runs()
    with _lock:
        if _draining or _active_run_ids.total() >= max_concurrent:
            return False
        _active_run_ids[run_id] += 1
        return True


def release_slot(run_id: str) -> None:
    """Release a slot previously acquired for ``run_id``. Releasing a run that
    holds no slot is a no-op, so the count never drops below zero."""
    with _lock:
        _active_run_ids[run_id] -= 1
        if _active_run_ids[run_id] <= 0:
            del _active_run_ids[run_id]


def active_count() -> int:
    """Current number of executing runs on this pod (for diagnostics)."""
    with _lock:
        return _active_run_ids.total()


def active_run_ids() -> list[str]:
    """IDs of the runs currently holding a slot on this pod."""
    with _lock:
        return sorted(_active_run_ids)


def begin_draining() -> None:
    """Stop admitting runs on this pod: every later try_acquire_slot() returns
    False, so a start or retry gets the normal "at capacity" 429. One-way --
    only process shutdown calls this."""
    global _draining
    with _lock:
        _draining = True


async def wait_for_drain(budget_seconds: float, poll_seconds: float | None = None) -> list[str]:
    """Wait until no run holds a slot on this pod, or ``budget_seconds`` pass.

    Returns the IDs still running when the budget ran out (empty when the pod
    drained in time). Sleeps on the event loop rather than blocking it, so an
    in-flight run's streamed-log coroutines, which are scheduled onto this loop,
    keep running during the wait.
    """
    poll_seconds = DRAIN_POLL_SECONDS if poll_seconds is None else poll_seconds
    loop = asyncio.get_running_loop()
    deadline = loop.time() + budget_seconds
    remaining = active_run_ids()
    while remaining and loop.time() < deadline:
        await asyncio.sleep(min(poll_seconds, max(deadline - loop.time(), 0)))
        remaining = active_run_ids()
    return remaining


def dispatch_mode() -> DispatchMode:
    """``in_process`` (today's BackgroundTask) or ``queue``.

    Normalises whitespace and case, and raises ``ValueError`` on anything
    else, so a typo'd ``CVICHE_DISPATCH_MODE`` (e.g. ``"queeu"``) fails loudly
    rather than silently running in-process forever (#701 run_queue.py point
    10). N7: this now fails BACKEND STARTUP itself, not only the next
    /start -- ``run_service.reconcile_stale_runs`` (via
    ``_effective_stale_run_minutes``) and ``reconcile_queued_runs`` both call
    this during the startup lifespan's own reconcile sweep, so a typo'd value
    stops the whole pod from ever becoming ready rather than surfacing lazily
    on the first request that reaches it.
    """
    raw, _ = get_config("llm", "CVICHE_DISPATCH_MODE", default="in_process")
    mode = raw.strip().lower()
    if mode not in _VALID_DISPATCH_MODES:
        raise ValueError(
            f"CVICHE_DISPATCH_MODE={raw!r} is not one of {_VALID_DISPATCH_MODES}"
        )
    return mode
