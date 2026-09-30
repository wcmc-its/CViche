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
