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


# --- cancellation is a terminal state too: the DB row must be able to stop a run
# that a different process (a queue worker, #701) is executing ------------------

def test_check_cancelled_reads_the_db_status_not_only_the_process_local_flag(db, tmp_path):
    """A cancel set on the row by another process -- the backend answering
    POST /cancel while a worker pod runs the pipeline -- must stop the run at the
    next stage boundary even though this process's cancel set is empty and no
    Redis broker is attached (the cancel key would expire after 300s anyway)."""
    from app.models import Run
    from app.pipeline import orchestrator as orch

    db.add(Run(id="CANCDB", filename="cv.docx", file_type="docx", status="running"))
    db.commit()
    o = orch.PipelineOrchestrator("CANCDB", tmp_path / "cv.docx", db)
    o.check_cancelled()  # running: no raise

    other = db.get_bind().connect()
    other.execute(__import__("sqlalchemy").text("UPDATE runs SET status='cancelled' WHERE id='CANCDB'"))
    other.commit()
    other.close()

    assert "CANCDB" not in orch._cancelled_runs
    with pytest.raises(orch.CancelledException):
        o.check_cancelled()
