"""Test for _RoutedStdout (#581).

sys.stdout is one binding shared by every thread in the interpreter. With
CVICHE_MAX_CONCURRENT_RUNS=3, three runs' stage calls can be writing to it
from three different thread-pool worker threads at once. This pins the fix:
two threads registering different captures each see only their own writes --
the exact race that used to leak run A's stage output (grant titles, the CV
owner's name) into run B's viewer while silently dropping run B's own.

Writes go through router.write(...) directly rather than sys.stdout.write(...):
pytest's own capture wraps sys.stdout for the scope of a test (visible with
plain `pytest`, absent under `-s`), which would make this test exercise
pytest's capture object instead of the router. Calling the router directly
tests the actual dispatch-by-thread-id logic regardless of what the test
runner has done to the ambient sys.stdout.
"""
from __future__ import annotations

import threading

from app.pipeline import orchestrator as orch_mod


class _FakeCapture:
    def __init__(self):
        self.written = []

    def write(self, text):
        self.written.append(text)
        return len(text)

    def flush(self):
        pass


def test_concurrent_threads_each_see_only_their_own_writes():
    router = orch_mod._STDOUT_ROUTER
    results = {}
    barrier = threading.Barrier(2)

    def run(name):
        capture = _FakeCapture()
        router.register(capture)
        try:
            barrier.wait(timeout=5)  # force real interleaving with the other thread
            for i in range(50):
                router.write(f"{name}-{i}\n")
        finally:
            router.unregister()
        results[name] = capture.written

    t1 = threading.Thread(target=run, args=("A",))
    t2 = threading.Thread(target=run, args=("B",))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)

    assert len(results["A"]) == 50
    assert len(results["B"]) == 50
    assert all(line.startswith("A-") for line in results["A"])
    assert all(line.startswith("B-") for line in results["B"])


def test_unregistered_thread_falls_through_without_raising():
    """A write from a thread with no registered capture must not raise -- it
    falls through to the real stdout rather than crashing."""
    router = orch_mod._STDOUT_ROUTER
    router.unregister()  # in case a prior failed test left a stale registration
    n = router.write("")
    assert n == 0
