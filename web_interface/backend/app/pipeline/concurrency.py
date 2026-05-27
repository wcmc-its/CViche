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
import logging
import os
import threading

logger = logging.getLogger(__name__)

# Concurrent full-pipeline runs allowed on a single pod. Conservative default
# for the 1 vCPU / 1 GiB prod pod: pipelines are LLM-I/O-bound so a little
# overlap uses the wait windows productively, but memory is the hard ceiling,
# so we keep this low. Raise via env once the pod is sized larger.
DEFAULT_MAX_CONCURRENT_RUNS = 2

_lock = threading.Lock()
_active_runs = 0


def get_max_concurrent_runs() -> int:
    """Read the per-pod concurrency cap from CVICHE_MAX_CONCURRENT_RUNS."""
    try:
        value = int(os.environ.get("CVICHE_MAX_CONCURRENT_RUNS", DEFAULT_MAX_CONCURRENT_RUNS))
    except (TypeError, ValueError):
        return DEFAULT_MAX_CONCURRENT_RUNS
    # A non-positive cap would wedge the pod (no run could ever start); treat it
    # as the default rather than silently disabling all runs.
    return value if value > 0 else DEFAULT_MAX_CONCURRENT_RUNS


def try_acquire_slot() -> bool:
    """Atomically reserve a run slot. Returns True if a slot was free (caller
    must call release_slot() when the run finishes), False if the pod is at
    capacity and the caller should reject the request."""
    global _active_runs
    max_concurrent = get_max_concurrent_runs()
    with _lock:
        if _active_runs >= max_concurrent:
            return False
        _active_runs += 1
        return True


def release_slot() -> None:
    """Release a previously-acquired run slot. Safe to call once per successful
    try_acquire_slot(); never drops below zero."""
    global _active_runs
    with _lock:
        if _active_runs > 0:
            _active_runs -= 1


def active_count() -> int:
    """Current number of executing runs on this pod (for diagnostics)."""
    with _lock:
        return _active_runs
