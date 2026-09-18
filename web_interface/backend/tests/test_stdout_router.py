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

import contextvars
import threading

from app.pipeline import orchestrator as orch_mod
from unified_pipeline.core.batch_pool import map_in_order


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


def test_context_var_routes_thread_spawned_via_copy_context(monkeypatch):
    """Since #883, a stage may spawn work on threads it did not itself
    register with the router (core/batch_pool runs each call inside
    contextvars.copy_context().run(...)). Such a thread has no entry in the
    thread-id dict, but it inherits the ContextVar set by register() on the
    thread that created the context, so it must still route to that thread's
    capture -- not fall through to real stdout."""
    router = orch_mod._STDOUT_ROUTER
    fake_real = _FakeCapture()
    monkeypatch.setattr(router, "_real", fake_real)

    capture = _FakeCapture()
    router.register(capture)
    try:
        ctx = contextvars.copy_context()

        def in_thread():
            router.write("from-copied-context\n")

        t = threading.Thread(target=lambda: ctx.run(in_thread))
        t.start()
        t.join(timeout=5)
    finally:
        router.unregister()

    assert capture.written == ["from-copied-context\n"]
    assert fake_real.written == []


def test_context_var_routes_batch_pool_threads(monkeypatch):
    """Same as above, through the real core.batch_pool.map_in_order pool path
    (workers=2) rather than a bare copy_context().run -- this is what stage
    3b (and stage 4 in #882) actually calls."""
    router = orch_mod._STDOUT_ROUTER
    fake_real = _FakeCapture()
    monkeypatch.setattr(router, "_real", fake_real)

    capture = _FakeCapture()
    router.register(capture)
    try:
        def fn(i):
            router.write(f"batch-{i}\n")
            return i

        results = map_in_order(fn, [(i,) for i in range(4)], workers=2)
    finally:
        router.unregister()

    assert results == [0, 1, 2, 3]
    assert sorted(capture.written) == [f"batch-{i}\n" for i in range(4)]
    assert fake_real.written == []


def test_thread_without_context_still_falls_through_to_real_stdout(monkeypatch):
    """A plain threading.Thread that was NOT spawned via copy_context() from
    a registered thread starts with a fresh context where the ContextVar is
    unset -- it must still fall through to real stdout rather than
    accidentally picking up another thread's capture. Pins the #581
    semantics: the ContextVar fallback must not turn into a global default."""
    router = orch_mod._STDOUT_ROUTER
    fake_real = _FakeCapture()
    monkeypatch.setattr(router, "_real", fake_real)

    capture = _FakeCapture()
    router.register(capture)  # registered on the test thread only
    try:
        def in_thread():
            router.write("no-context\n")

        t = threading.Thread(target=in_thread)
        t.start()
        t.join(timeout=5)
    finally:
        router.unregister()

    assert capture.written == []
    assert fake_real.written == ["no-context\n"]


def test_unregister_resets_the_context_var(monkeypatch):
    """unregister() must reset the ContextVar token, not just pop the
    thread-id dict entry -- otherwise a write from the same thread after
    unregister() would keep routing to the stale capture via the fallback."""
    router = orch_mod._STDOUT_ROUTER
    fake_real = _FakeCapture()
    monkeypatch.setattr(router, "_real", fake_real)

    capture = _FakeCapture()
    router.register(capture)
    router.unregister()

    router.write("after-unregister\n")

    assert capture.written == []
    assert fake_real.written == ["after-unregister\n"]
