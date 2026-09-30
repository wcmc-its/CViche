"""Regression tests for deterministic run-failure surfacing.

Guards the fix for the reported bug where a Stage 4 error went unsurfaced and
the UI elapsed timer kept counting: the run must reach a terminal state and a
terminal event must be emitted, so the client can stop the timer and show the
failure rather than implying work is still ongoing.

Covers:
  - EventEmitter.emit_run_failed (the new terminal failure event)             [E1]
  - websocket._terminal_event_for_run (replay terminal status on reconnect)   [E4]
  - PipelineOrchestrator per-stage timeout -> surfaced error                  [E3]
  - llm_client / orchestrator timeout config env parsing                      [E3]
  - orchestrator.user_facing_error: run.error_message never carries str(exc) [#592]
"""
import asyncio
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

# Make unified_pipeline.* importable directly (tests/ -> backend/ ->
# web_interface/ -> project_root/ -> src/), matching test_llm_client.py.
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))


# ---------------------------------------------------------------------------
# E1: terminal RUN_FAILED event
# ---------------------------------------------------------------------------

def test_emit_run_failed_delivers_terminal_event():
    """emit_run_failed delivers a RUN_FAILED event (with error + step) to the
    run's local sockets, mirroring emit_run_complete / RUN_CANCELLED."""
    from app.pipeline.event_emitter import EventEmitter

    sent: list[str] = []

    class FakeWS:
        async def accept(self):
            pass

        async def send_text(self, msg):
            sent.append(msg)

    async def run():
        emitter = EventEmitter()  # no broker -> direct local delivery
        ws = FakeWS()
        await emitter.connect("run-1", ws)
        await emitter.emit_run_failed("run-1", "Stage 4 timed out", step_number=6)

    asyncio.run(run())

    assert len(sent) == 1
    event = json.loads(sent[0])
    assert event["event"] == "RUN_FAILED"
    assert event["error"] == "Stage 4 timed out"
    assert event["step"] == 6
    assert "timestamp" in event  # emit() stamps it


# ---------------------------------------------------------------------------
# E4: replay terminal status on (re)connect
# ---------------------------------------------------------------------------

def test_terminal_event_for_run_complete():
    from app.api.websocket import _terminal_event_for_run

    started = datetime(2026, 6, 2, 10, 0, 0)
    run = SimpleNamespace(
        status="complete",
        total_cost=2.5,
        total_tokens=1234,
        started_at=started,
        completed_at=started + timedelta(seconds=90),
    )
    event = _terminal_event_for_run(run)
    assert event == {
        "event": "RUN_COMPLETE",
        "total_cost": 2.5,
        "total_tokens": 1234,
        "duration": 90,
    }


def test_terminal_event_for_run_failed():
    from app.api.websocket import _terminal_event_for_run

    run = SimpleNamespace(status="failed", error_message="boom")
    assert _terminal_event_for_run(run) == {
        "event": "RUN_FAILED",
        "error": "boom",
        "step": None,
    }


def test_terminal_event_for_run_cancelled():
    from app.api.websocket import _terminal_event_for_run

    run = SimpleNamespace(status="cancelled")
    assert _terminal_event_for_run(run) == {"event": "RUN_CANCELLED"}


def test_terminal_event_for_run_running_is_none():
    """A still-running run has no terminal event to replay."""
    from app.api.websocket import _terminal_event_for_run

    run = SimpleNamespace(status="running")
    assert _terminal_event_for_run(run) is None


# ---------------------------------------------------------------------------
# E3: timeout config env parsing
# ---------------------------------------------------------------------------

def test_llm_timeout_env_override(monkeypatch):
    import unified_pipeline.llm_client as mod

    monkeypatch.setenv("CVICHE_LLM_TIMEOUT_SECONDS", "45")
    assert mod._get_llm_timeout_seconds() == 45.0
    # Non-positive / garbage fall back to the default (never disable the bound).
    monkeypatch.setenv("CVICHE_LLM_TIMEOUT_SECONDS", "0")
    assert mod._get_llm_timeout_seconds() == 180.0
    monkeypatch.setenv("CVICHE_LLM_TIMEOUT_SECONDS", "garbage")
    assert mod._get_llm_timeout_seconds() == 180.0


def test_llm_max_attempts_env_override(monkeypatch):
    import unified_pipeline.llm_client as mod

    monkeypatch.setenv("CVICHE_LLM_MAX_ATTEMPTS", "5")
    assert mod._get_llm_max_attempts() == 5
    monkeypatch.setenv("CVICHE_LLM_MAX_ATTEMPTS", "0")
    assert mod._get_llm_max_attempts() == 3
    monkeypatch.setenv("CVICHE_LLM_MAX_ATTEMPTS", "garbage")
    assert mod._get_llm_max_attempts() == 3


def test_stage_timeout_env_override(monkeypatch):
    from app.pipeline import orchestrator as orch

    monkeypatch.setenv("CVICHE_STAGE_TIMEOUT_SECONDS", "42")
    assert orch._get_stage_timeout_seconds() == 42
    # 0 explicitly disables the per-stage ceiling.
    monkeypatch.setenv("CVICHE_STAGE_TIMEOUT_SECONDS", "0")
    assert orch._get_stage_timeout_seconds() == 0
    monkeypatch.setenv("CVICHE_STAGE_TIMEOUT_SECONDS", "garbage")
    assert orch._get_stage_timeout_seconds() == 1800


# ---------------------------------------------------------------------------
# E3: a hung stage becomes a surfaced error (the core bug)
# ---------------------------------------------------------------------------

def test_execute_step_timeout_surfaces_error(monkeypatch, tmp_path):
    """A stage that runs past the per-stage ceiling raises a clear TimeoutError,
    flips the step to 'error', records which step failed, and emits STEP_ERROR --
    instead of hanging forever with the run pinned at 'running'."""
    monkeypatch.setenv("CVICHE_STAGE_TIMEOUT_SECONDS", "1")
    from app.pipeline import orchestrator as orch

    emitter = AsyncMock()
    monkeypatch.setattr(orch, "event_emitter", emitter)

    fake_step = MagicMock()
    fake_step.status = "pending"
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = fake_step

    o = orch.PipelineOrchestrator("test-timeout-run", tmp_path / "cv.docx", fake_db)

    async def hang(stage_id, cv_path):
        await asyncio.sleep(5)  # longer than the 1s ceiling

    monkeypatch.setattr(o, "_execute_stage_logic", hang)

    with pytest.raises(TimeoutError, match="timed out"):
        asyncio.run(o.execute_step(6, "4", "cv.docx"))

    assert fake_step.status == "error"
    assert o.failed_step_number == 6
    emitter.emit_step_error.assert_awaited()


# ---------------------------------------------------------------------------
# #590: a timed-out stage's worker thread stops at its next callback
# ---------------------------------------------------------------------------

def _timeout_orchestrator(monkeypatch, tmp_path, stage_fn):
    """Orchestrator whose stage logic runs ``stage_fn`` in the real worker thread."""
    monkeypatch.setenv("CVICHE_STAGE_TIMEOUT_SECONDS", "1")
    from app.pipeline import orchestrator as orch

    emitter = AsyncMock()
    monkeypatch.setattr(orch, "event_emitter", emitter)
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = MagicMock()
    o = orch.PipelineOrchestrator("run-590-quill", tmp_path / "cv590.docx", fake_db)
    monkeypatch.setattr(o, "_record_stage_outcome", lambda *a: None)

    async def stage_logic(stage_id, cv_path):
        return await o._run_with_stdout_capture(stage_fn, 6)

    monkeypatch.setattr(o, "_execute_stage_logic", stage_logic)
    return o, emitter


@pytest.mark.parametrize("callback", ["stdout", "cancel_check"])
def test_timed_out_stage_thread_stops_at_next_callback_before_terminal(monkeypatch, tmp_path, callback):
    import threading
    import time

    ticks = []
    exited = threading.Event()

    def chatty_stage():
        try:
            while True:
                if callback == "stdout":
                    print(f"Processing {len(ticks) % 9 + 1} of 10")
                else:
                    o.check_cancelled()  # stages 2 and 4's intra-stage callback
                ticks.append(1)
                time.sleep(0.02)
        finally:
            exited.set()

    from app.pipeline import orchestrator as orch

    monkeypatch.setattr(orch, "STAGE_WORKER_EXIT_GRACE_SECONDS", 3)  # bounds a regression
    errors = []
    monkeypatch.setattr(orch.logger, "error", lambda *a, **k: errors.append(a))
    o, emitter = _timeout_orchestrator(monkeypatch, tmp_path, chatty_stage)
    with pytest.raises(TimeoutError, match="timed out"):
        asyncio.run(o.execute_step(6, "4", "cv.docx"))
    assert errors == []  # the thread exited inside the grace period: nothing to shout about

    # (1) the thread unwound at its next callback, and had exited by the time
    # execute_step raised (so the run cannot go terminal underneath it)
    assert exited.is_set()
    # (2) nothing lands after that point: no new log/progress emits, no new ticks
    logs_before = emitter.emit_log.await_count
    progress_before = emitter.emit_progress.await_count
    ticks_before = len(ticks)
    time.sleep(0.2)
    assert emitter.emit_log.await_count == logs_before
    assert emitter.emit_progress.await_count == progress_before
    assert len(ticks) == ticks_before
    assert callback != "stdout" or logs_before > 0


def test_each_stage_starts_with_a_fresh_stop_flag(monkeypatch, tmp_path):
    o, _ = _timeout_orchestrator(
        monkeypatch, tmp_path,
        lambda: print("Processing 1 of 2") or {"cost": 0.0, "output_files": []})
    monkeypatch.setattr(o, "_persist_outputs_to_storage", lambda files: None)
    monkeypatch.setattr(o, "_sync_prompt_logs_to_storage", lambda *a: None)
    o._stage_guard.stop.set()  # left over from an earlier timed-out stage
    asyncio.run(o.execute_step(6, "4", "cv.docx"))  # would raise StageAbandoned if reused


def _timeout_by_hand(o):
    """Run execute_step to its TimeoutError on a hand-driven loop (no executor join)."""
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(TimeoutError, match="timed out"):
            loop.run_until_complete(o.execute_step(6, "4", "cv.docx"))
    finally:
        loop.close()


def test_stage_that_swallows_exceptions_still_stops_within_the_real_grace(monkeypatch, tmp_path):
    import threading
    import time

    from app.pipeline import orchestrator as orch

    # The grace constant is deliberately NOT patched: the run must wait for the
    # thread (not fail at once), and a prompt exit must not sleep out the grace.
    assert orch.STAGE_WORKER_EXIT_GRACE_SECONDS == 30
    exited = threading.Event()
    quit_flag = threading.Event()

    def swallowing_stage():
        try:
            while not quit_flag.is_set():
                try:
                    print("Processing 1 of 10")
                except Exception:  # noqa: BLE001 - the stage behaviour under test
                    pass
                time.sleep(0.02)
        finally:
            exited.set()

    o, _ = _timeout_orchestrator(monkeypatch, tmp_path, swallowing_stage)
    errors = []
    monkeypatch.setattr(orch.logger, "error", lambda *a, **k: errors.append(a))
    started = time.monotonic()
    try:
        _timeout_by_hand(o)
    finally:
        quit_flag.set()  # never leak the thread, even when the assertions below fail
    assert time.monotonic() - started < 4  # CVICHE_STAGE_TIMEOUT_SECONDS=1 plus a prompt exit
    assert errors == []
    assert exited.is_set()


def test_grace_wait_leaves_the_event_loop_free_to_drain_emits(monkeypatch, tmp_path):
    import threading

    from app.pipeline import orchestrator as orch

    monkeypatch.setattr(orch, "STAGE_WORKER_EXIT_GRACE_SECONDS", 3)
    quit_flag = threading.Event()
    loops = []

    async def ping():
        return None

    def loop_dependent_stage():
        # Every iteration needs the event loop to run a coroutine, so a loop
        # blocked during the grace keeps the thread from reaching its next print.
        while not quit_flag.is_set():
            print("x")  # the stop flag is honoured here
            asyncio.run_coroutine_threadsafe(ping(), loops[0]).result(5)

    o, _ = _timeout_orchestrator(monkeypatch, tmp_path, loop_dependent_stage)
    stage_logic = o._execute_stage_logic

    async def remember_loop(*args):
        loops.append(asyncio.get_running_loop())
        return await stage_logic(*args)

    monkeypatch.setattr(o, "_execute_stage_logic", remember_loop)
    errors = []
    monkeypatch.setattr(orch.logger, "error", lambda *a, **k: errors.append(a))
    try:
        _timeout_by_hand(o)
    finally:
        quit_flag.set()
    assert errors == []  # a blocked loop never runs ping(), so the thread would outlive the grace


@pytest.mark.parametrize("late_print", [True, False])
def test_stage_thread_that_outlives_grace_is_logged_and_its_callbacks_stay_dead(monkeypatch, tmp_path, late_print):
    import threading

    from app.pipeline import orchestrator as orch

    monkeypatch.setattr(orch, "STAGE_WORKER_EXIT_GRACE_SECONDS", 0.2)
    release = threading.Event()
    exited = threading.Event()
    late_write = []

    def wedged_stage():
        try:
            print("unfinished line", end="")  # buffered by the sink, not yet emitted
            release.wait(timeout=10)  # ignores the stop flag: no callback while wedged
            if late_print:
                print("late line after the terminal status")
                late_write.append(1)  # only reached if the sink failed to raise
            # else: returns normally, so the sink's final flush() must not emit
        finally:
            exited.set()

    o, emitter = _timeout_orchestrator(monkeypatch, tmp_path, wedged_stage)
    errors = []
    monkeypatch.setattr(orch.logger, "error", lambda *a, **k: errors.append(a))
    # asyncio.run() would join the still-running executor thread on exit;
    # drive the loop by hand so the wedged thread outlives execute_step.
    loop = asyncio.new_event_loop()
    try:
        with pytest.raises(TimeoutError, match="timed out"):
            loop.run_until_complete(o.execute_step(6, "4", "cv.docx"))
    finally:
        loop.close()  # shutdown(wait=False) on the default executor

    # (3) still running past the grace period: logged loudly, run failed anyway
    assert not exited.is_set()
    assert any("still running" in str(a[-1]) for a in errors)
    error_logs = [c for c in emitter.emit_log.await_args_list if c.args[3] == "ERROR"]
    assert any("still running" in c.args[2] for c in error_logs)

    # The loop is stopped by now, so a late emit would never reach emit_log;
    # count attempts to schedule one on the loop instead.
    scheduled = []
    real_schedule = asyncio.run_coroutine_threadsafe

    def spy(coro, loop):
        scheduled.append(1)
        return real_schedule(coro, loop)

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", spy)
    release.set()
    assert exited.wait(timeout=5)
    assert o._stage_guard.wait_idle(5)  # the sink's final flush() runs after the stage returns
    assert late_write == []  # the write raised StageAbandoned
    assert scheduled == []


# ---------------------------------------------------------------------------
# #745: a failed stage leaves a structured stage-error record for the scorer
# ---------------------------------------------------------------------------

def _orchestrator_for_step(monkeypatch, tmp_path, stage_logic):
    from app.pipeline import orchestrator as orch

    monkeypatch.setattr(orch, "event_emitter", AsyncMock())
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = MagicMock()
    o = orch.PipelineOrchestrator("test-745-run", tmp_path / "cv745.docx", fake_db)
    o.pipeline_output_dir = tmp_path / "outputs"
    persisted: list[list[str]] = []
    monkeypatch.setattr(o, "_persist_outputs_to_storage", persisted.append)
    monkeypatch.setattr(o, "_sync_prompt_logs_to_storage", lambda *a: None)
    monkeypatch.setattr(o, "_execute_stage_logic", stage_logic)
    return o, persisted


def test_execute_step_failure_writes_and_mirrors_a_stage_error_record(monkeypatch, tmp_path):
    """The orchestrator raises (fails the run) but first records which stage
    broke and how, and mirrors it to outputs/ where the scorer collects it --
    even when the message names no exception type."""
    from unified_pipeline.stage_errors import StageError, read_stage_errors, stage_errors_path

    async def boom(stage_id, cv_path):
        raise TypeError("'int' object is not iterable")

    o, persisted = _orchestrator_for_step(monkeypatch, tmp_path, boom)
    with pytest.raises(TypeError):
        asyncio.run(o.execute_step(6, "4", "cv.docx"))

    path = stage_errors_path(o.pipeline_output_dir, "cv745")
    assert read_stage_errors(path) == [
        StageError("4", "TypeError", "'int' object is not iterable", fatal=True)]
    assert [str(path)] in persisted


def test_execute_step_success_clears_an_earlier_stage_error(monkeypatch, tmp_path):
    """A retried stage that now succeeds removes its entry, locally and in the
    mirrored copy, so a recovered run is not capped by its own history."""
    from unified_pipeline.stage_errors import (
        StageError, read_stage_errors, record_stage_outcome, stage_errors_path)

    async def ok(stage_id, cv_path):
        return {"cost": 0.0, "output_files": []}

    o, persisted = _orchestrator_for_step(monkeypatch, tmp_path, ok)
    path = stage_errors_path(o.pipeline_output_dir, "cv745")
    record_stage_outcome(path, "4", StageError("4", "TypeError", "x", fatal=True))

    asyncio.run(o.execute_step(6, "4", "cv.docx"))

    assert read_stage_errors(path) == []
    assert [str(path)] in persisted


def test_execute_step_success_without_a_record_writes_none(monkeypatch, tmp_path):
    from unified_pipeline.stage_errors import stage_errors_path

    async def ok(stage_id, cv_path):
        return {"cost": 0.0, "output_files": []}

    o, persisted = _orchestrator_for_step(monkeypatch, tmp_path, ok)
    asyncio.run(o.execute_step(6, "4", "cv.docx"))
    assert not stage_errors_path(o.pipeline_output_dir, "cv745").exists()
    assert persisted == [[]]


# ---------------------------------------------------------------------------
# #592: run.error_message is a fixed user-facing message, never str(exc)
# ---------------------------------------------------------------------------

_LEAKY = "boto3 ClientError: /app/src/unified_pipeline/x.py RequestId=abc123"


def test_user_facing_error_generic_failure_hides_exception_text():
    from app.pipeline.orchestrator import GENERIC_FAILURE_MESSAGE, user_facing_error

    msg = user_facing_error(RuntimeError(_LEAKY), resuming=False)
    assert msg == GENERIC_FAILURE_MESSAGE
    assert "abc123" not in msg and "/app/" not in msg


def test_user_facing_error_llm_outage_found_through_the_chain():
    """A stage that wraps LLMOutageError (raise X from outage) still reads as
    an outage, so the user is told to wait rather than that the CV is bad."""
    from app.pipeline.orchestrator import LLM_OUTAGE_MESSAGE, user_facing_error
    from unified_pipeline.llm.retry import LLMOutageError

    try:
        try:
            raise LLMOutageError(_LEAKY, seconds_waited=31.0)
        except LLMOutageError as outage:
            raise RuntimeError("stage 4 failed") from outage
    except RuntimeError as wrapped:
        assert user_facing_error(wrapped, resuming=False) == LLM_OUTAGE_MESSAGE


def test_user_facing_error_stage_timeout():
    from app.pipeline.orchestrator import STAGE_TIMEOUT_MESSAGE, user_facing_error

    exc = TimeoutError("Stage 4 (Field Extraction) timed out after 1800s")
    assert user_facing_error(exc, resuming=False) == STAGE_TIMEOUT_MESSAGE


def test_user_facing_error_missing_input_only_special_on_resume():
    from app.pipeline.orchestrator import (
        GENERIC_FAILURE_MESSAGE,
        RESUME_INPUT_MISSING_MESSAGE,
        user_facing_error,
    )

    exc = FileNotFoundError("[Errno 2] No such file or directory: '/x/y.json'")
    assert user_facing_error(exc, resuming=True) == RESUME_INPUT_MISSING_MESSAGE
    assert user_facing_error(exc, resuming=False) == GENERIC_FAILURE_MESSAGE


def test_user_facing_error_messages_name_real_buttons():
    """Every message's quoted action must be a button PipelineViewer renders."""
    from app.pipeline import orchestrator as orch

    viewer = (
        Path(__file__).parents[2] / "frontend" / "src" / "components" / "PipelineViewer.tsx"
    ).read_text()
    for msg in (
        orch.RESUME_INPUT_MISSING_MESSAGE,
        orch.LLM_OUTAGE_MESSAGE,
        orch.STAGE_TIMEOUT_MESSAGE,
        orch.GENERIC_FAILURE_MESSAGE,
    ):
        for label in msg.split('"')[1::2]:
            assert label in viewer, f"{label!r} is not a PipelineViewer button"
