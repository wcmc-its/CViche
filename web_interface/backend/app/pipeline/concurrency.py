"""Per-pod admission control for pipeline runs.

A run executes as an in-process background task (see runs.py:start_run): the
full 15-20 min pipeline runs inside the web process, and nothing caps how many
start at once. On the production pod (1 vCPU / 1 GiB, single replica) a handful
of concurrent runs thrash CPU, exhaust the ~1 GiB memory ceiling, and fan out
overlapping LLM request streams -- with no "system busy" signal, runs just
contend silently and risk OOM.

This module is the gate: a process-global counter of currently-executing runs,
bounded by CVICHE_MAX_CONCURRENT_RUNS. The endpoints that launch a pipeline
acquire a slot before scheduling the background task and release it when the
task finishes; when the pod is at capacity, new starts are rejected with 429 so
the caller can retry rather than pile on.

Scope is deliberately per-pod (a plain in-process counter, like the WebSocket
emitter and cancellation flag): the resource being protected -- this pod's CPU
and memory -- is itself per-pod. Cross-pod admission would need shared state
and only becomes meaningful once replicas > 1 (see
docs/proposals/issue-4-redis-broker.md and concurrency-and-load-readiness.md).
"""
import asyncio
import logging
import os
import threading
from collections import Counter
from app.config_loader import get_config

logger = logging.getLogger(__name__)

# Concurrent full-pipeline runs allowed on a single pod. Conservative default
# for the 1 vCPU / 1 GiB prod pod: pipelines are LLM-I/O-bound so a little
# overlap uses the wait windows productively, but memory is the hard ceiling,
# so we keep this low. Raise via env once the pod is sized larger.
DEFAULT_MAX_CONCURRENT_RUNS = 2

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
        #value = int(os.environ.get("CVICHE_MAX_CONCURRENT_RUNS", DEFAULT_MAX_CONCURRENT_RUNS))
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
