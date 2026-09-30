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
import io
import sys
import threading
import time

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


def test_emit_progress_failure_is_logged_not_swallowed(caplog):
    """A programming error in update_progress must be visible at debug level (#307)."""
    import asyncio
    import logging

    class _BrokenOrchestrator:
        async def update_progress(self, *args):
            raise RuntimeError("bad progress call")

    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        capture = orch_mod.StreamingStdoutCapture(_BrokenOrchestrator(), 3, loop)
        with caplog.at_level(logging.DEBUG, logger=orch_mod.logger.name):
            capture._emit_progress_sync(1, 2, "Processing 1 of 2")
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()
    assert "Progress emit skipped for step 3: bad progress call" in caplog.text


# ---------------------------------------------------------------------------
# #116: a log line written on the run's own loop never streams back into the
# run's capture. Those lines happen while the capture is the context's stdout
# (inside log(), the emitter, the broker, asyncio's error reporting); streamed,
# each one blocked the run loop 2s waiting on itself and emitted again.
# ---------------------------------------------------------------------------


def _entered_guard(orch_module):
    """A stage guard entered the way _run_with_stdout_capture enters it (#590);
    _run_with_stdout_capture_sync exits it."""
    guard = orch_module._StageGuard()
    guard.enter()
    return guard

def _stream_through_real_capture(emitter, prints, loggers, timeout=20.0):
    """Run a stage that print()s ``prints`` lines through the real capture and
    router, with ``loggers`` writing through sys.stdout the way
    configure_logging sets app logging up. Returns (seconds or None if the
    stage did not finish within ``timeout``, lines recorded as run log)."""
    import asyncio
    import gc
    import logging
    from app.logging_config import _DYNAMIC_STDOUT

    logged = []

    class _Run:
        """Stands in for PipelineOrchestrator's log()/update_progress()."""

        async def log(self, step, message, level="INFO"):
            logged.append(message)
            await emitter.emit_log("R1", step, message, level)

        async def update_progress(self, step, current, total, message=""):
            await emitter.emit_progress("R1", step, current, total, message)

    def stage():
        for n in range(1, prints + 1):
            print(f"Processing {n} of {prints} sections")

    async def run():
        loop = asyncio.get_running_loop()
        await asyncio.to_thread(
            orch_mod.PipelineOrchestrator._run_with_stdout_capture_sync, _Run(), stage, 1, loop,
            _entered_guard(orch_mod))
        gc.collect()  # surface any never-retrieved future now, on this loop
        await asyncio.sleep(0.3)

    outcome = {}

    def target():
        started = time.monotonic()
        asyncio.run(run())
        outcome["seconds"] = time.monotonic() - started

    handler = logging.StreamHandler(_DYNAMIC_STDOUT)
    for name in loggers:
        logging.getLogger(name).addHandler(handler)
    try:
        worker = threading.Thread(target=target, daemon=True)  # may hang on a regression
        worker.start()
        worker.join(timeout)
    finally:
        for name in loggers:
            logging.getLogger(name).removeHandler(handler)
    return outcome.get("seconds"), logged


def test_a_broker_outage_during_a_stage_does_not_stall_the_run(monkeypatch):
    """Valkey unreachable: every publish logs "Redis publish failed" on the run
    loop, inside the capture's context (prod, dev-210: a 2-print stage never
    finished in 60s)."""
    from app.pipeline.event_emitter import EventEmitter
    from app.pipeline.redis_broker import RedisBroker

    real_stdout = io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", real_stdout)
    emitter = EventEmitter(broker=RedisBroker("redis://127.0.0.1:1/0"))

    seconds, logged = _stream_through_real_capture(
        emitter, 2, ["app.pipeline.redis_broker"])

    assert seconds is not None and seconds < 5
    assert logged == ["Processing 1 of 2 sections", "Processing 2 of 2 sections"]
    assert "Redis publish failed" in real_stdout.getvalue()  # still logged, just not streamed


def test_a_failed_delivery_does_not_stall_the_run_or_leak_a_future(monkeypatch):
    """A delivery that fails on the server loop: its exception is retrieved (no
    "Future exception was never retrieved" from the run loop), and the
    warnings it causes stay out of the capture (a 3-print stage did not
    finish in 90s)."""
    import asyncio
    import logging
    from app.pipeline import event_emitter as emitter_module

    real_stdout = io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", real_stdout)
    records = []

    class _Keep(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    keep = _Keep()
    logging.getLogger("asyncio").addHandler(keep)

    class _Socket:
        async def accept(self):
            return None

    async def broken(run_id, message):
        raise ValueError("send failed")

    server = asyncio.new_event_loop()
    thread = threading.Thread(target=server.run_forever, daemon=True)
    thread.start()
    emitter = emitter_module.EventEmitter()
    try:
        asyncio.run_coroutine_threadsafe(emitter.connect("R1", _Socket()), server).result(5)
        monkeypatch.setattr(emitter, "_deliver_local", broken)
        seconds, logged = _stream_through_real_capture(
            emitter, 3, ["asyncio", emitter_module.__name__])
    finally:
        logging.getLogger("asyncio").removeHandler(keep)
        server.call_soon_threadsafe(server.stop)
        thread.join(5)
        server.close()

    assert seconds is not None and seconds < 5
    assert logged == [f"Processing {n} of 3 sections" for n in (1, 2, 3)]
    assert not any("never retrieved" in message for message in records)
    assert "[Log emit error" not in real_stdout.getvalue()


def test_a_run_loop_write_goes_to_real_stdout_and_a_stage_write_streams(monkeypatch):
    """The guard's two sides, directly: a write made on the capture's event
    loop is passed to the real stdout and never streamed; a write from any
    other thread is streamed as before."""
    import asyncio

    real_stdout = io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", real_stdout)
    streamed = []

    class _Run:
        async def log(self, step, message, level="INFO"):
            streamed.append(message)

    async def scenario():
        loop = asyncio.get_running_loop()
        capture = orch_mod.StreamingStdoutCapture(_Run(), 1, loop)
        capture.write("on the run loop\n")
        capture.buffer = "partial"
        capture.flush()  # a handler's flush on the run loop: no streaming either
        kept = capture.buffer
        capture.buffer = ""
        await asyncio.to_thread(capture.write, "from the stage\n")
        return kept

    kept = asyncio.run(scenario())

    assert streamed == ["from the stage"]
    assert kept == "partial"
    assert "on the run loop\n" in real_stdout.getvalue()


def test_a_stage_printing_from_its_own_event_loop_still_streams():
    """Only the capture's own run loop is exempt. A stage that runs its own
    asyncio.run() (an async client inside a stage) and prints from inside it
    is still the stage talking: its lines, and the progress parsed from them,
    must stream."""
    import asyncio

    streamed, progress = [], []

    class _Run:
        async def log(self, step, message, level="INFO"):
            streamed.append(message)

        async def update_progress(self, step, current, total, message=""):
            progress.append((current, total))

    async def stage_async():
        print("Processing 2 of 5 sections")

    def stage():
        asyncio.run(stage_async())

    async def run():
        loop = asyncio.get_running_loop()
        await asyncio.to_thread(
            orch_mod.PipelineOrchestrator._run_with_stdout_capture_sync, _Run(), stage, 1, loop,
            _entered_guard(orch_mod))

    asyncio.run(run())

    assert streamed == ["Processing 2 of 5 sections"]
    assert progress == [(2, 5)]


def test_a_run_loop_write_after_a_stage_timeout_does_not_raise(monkeypatch):
    """#590's stop point is for the stage thread. A log line written on the
    run loop after the stage was abandoned (the timeout path logs there) must
    not raise StageAbandoned into that logging call."""
    import asyncio

    real_stdout = io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", real_stdout)
    guard = orch_mod._StageGuard()
    guard.stop.set()

    async def scenario():
        capture = orch_mod.StreamingStdoutCapture(object(), 1, asyncio.get_running_loop(), guard)
        return capture.write("stage 4 timed out\n")

    assert asyncio.run(scenario()) == len("stage 4 timed out\n")
    assert "stage 4 timed out\n" in real_stdout.getvalue()


def test_the_delivered_debug_line_stays_out_of_the_capture(monkeypatch):
    """The success branch logs at debug on the run loop (#1132 review). With
    debug on and app logging through sys.stdout, that line must go to the
    real stdout, not stream back into the run's log."""
    import asyncio
    import logging
    from app.pipeline import event_emitter as emitter_module

    real_stdout = io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", real_stdout)
    previous_level = emitter_module.logger.level
    emitter_module.logger.setLevel(logging.DEBUG)  # setLevel, not .level=: clears the logger's cache

    class _Socket:
        def __init__(self):
            self.sent = []

        async def accept(self):
            return None

        async def send_text(self, message):
            self.sent.append(message)

    socket = _Socket()
    server = asyncio.new_event_loop()
    thread = threading.Thread(target=server.run_forever, daemon=True)
    thread.start()
    emitter = emitter_module.EventEmitter()
    try:
        asyncio.run_coroutine_threadsafe(emitter.connect("R1", socket), server).result(5)
        seconds, logged = _stream_through_real_capture(emitter, 2, [emitter_module.__name__])
    finally:
        emitter_module.logger.setLevel(previous_level)
        server.call_soon_threadsafe(server.stop)
        thread.join(5)
        server.close()

    assert seconds is not None and seconds < 5
    assert logged == ["Processing 1 of 2 sections", "Processing 2 of 2 sections"]
    assert "Event for run R1 delivered" in real_stdout.getvalue()
    assert len(socket.sent) == 4  # two LOG, two PROGRESS
