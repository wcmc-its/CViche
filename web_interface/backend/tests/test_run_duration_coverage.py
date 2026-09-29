"""Additional coverage for the persisted run-duration feature (PR #103, issue #104).

The existing test_run_duration.py covers the _run_duration_seconds() resolver and
the admin /stats + /runs aggregates for complete/running rows. This file fills the
gaps called out in issue #104 without modifying that file:

  * the orchestrator WRITE path -- execute() actually persisting
    runs.total_duration_seconds on completion and on failure;
  * the start_time-is-None branch (an exception before the pipeline starts must
    leave total_duration_seconds untouched, not crash on the guard);
  * cancelled / failed statuses in the _run_duration_seconds() helper and in the
    admin submissions Duration column (only complete/running were exercised);
  * n=1 nearest-rank p95 and 0-second / sub-second runs in /admin/stats.

Retry semantics (#104, decided 2026-09-09): a resumed run accumulates onto its
prior total_duration_seconds; a fresh run overwrites.

Runnable like the rest of the suite, e.g.:
    DB_HOST=localhost DB_PORT=3306 DB_NAME=testdb DB_USER=testuser \
        python3 -m pytest tests/test_run_duration_coverage.py
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

# Make unified_pipeline.* importable directly (tests/ -> backend/ ->
# web_interface/ -> project_root/ -> src/), matching test_failure_surfacing.py --
# importing the orchestrator pulls in the unified_pipeline stage functions.
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from app.api.runs import _run_duration_seconds
from app.pipeline.orchestrator import GENERIC_FAILURE_MESSAGE


def _run(**kw):
    """Build a lightweight Run-like row (mirrors test_run_duration._run)."""
    base = dict(
        total_duration_seconds=None,
        started_at=None,
        completed_at=None,
        status="created",
    )
    base.update(kw)
    return SimpleNamespace(**base)


# ===========================================================================
# 1. Orchestrator WRITE path -- execute() persists total_duration_seconds.
#    THIS IS THE BIGGEST HOLE: nothing else drives the real persistence.
# ===========================================================================

def _single_step_registry():
    """A one-entry STEP_REGISTRY so execute() loops exactly once."""
    return [SimpleNamespace(number=1, stage_id="1a", name="Hierarchy Extraction")]


def _fake_clock(start, end):
    """orchestrator._now() stub (#598: patch the module clock, never time.monotonic): first call returns ``start`` (sets the orchestrator's
    start_time), every later call returns ``end`` so the terminal-commit delta
    is exactly ``end - start``.

    Uses a sticky end value rather than an exhausting iterator so the off-loop
    quality-score executor (which may also read the clock from another thread)
    can never trip a StopIteration after the duration has been recorded.
    """
    state = {"first": True}

    def _clock():
        if state["first"]:
            state["first"] = False
            return start
        return end

    return _clock


def test_execute_persists_duration_on_complete(monkeypatch, tmp_path, db):
    """A run that reaches the 'complete' commit gets total_duration_seconds
    written from the orchestrator's own _now() delta in execute()'s complete path, not
    from wall-clock completed_at - started_at.

    _now() is pinned to two fixed values so the assertion is exact.
    """
    from app.pipeline import orchestrator as orch
    from app.models import Run

    # Seed a real running row in the in-memory DB.
    db.add(Run(
        id="EXEC_OK", filename="cv.docx", file_type="docx", status="running",
        started_at=datetime(2026, 6, 4, 12, 0, 0), total_duration_seconds=None,
    ))
    db.commit()

    # No real pipeline: stub the event emitter, the input-copy, the per-stage
    # work, and shrink the stage registry to a single no-op step.
    monkeypatch.setattr(orch, "event_emitter", AsyncMock())
    monkeypatch.setattr(orch, "STEP_REGISTRY", _single_step_registry())

    o = orch.PipelineOrchestrator("EXEC_OK", tmp_path / "cv.docx", db)
    monkeypatch.setattr(o, "_copy_to_pipeline_input", lambda: str(tmp_path / "cv.docx"))
    monkeypatch.setattr(o, "execute_step", AsyncMock())

    # Pin elapsed to exactly 42s: first _now() call sets start_time,
    # later calls compute the duration at the complete commit.
    monkeypatch.setattr(orch, "_now", _fake_clock(1000.0, 1042.0))

    asyncio.run(o.execute())

    db.expire_all()
    row = db.query(Run).filter(Run.id == "EXEC_OK").first()
    assert row.status == "complete"
    assert row.completed_at is not None
    assert row.total_duration_seconds == 42


def test_execute_persists_duration_on_failure(monkeypatch, tmp_path, db):
    """A run that fails AFTER the pipeline has started (start_time set) records
    a non-NULL total_duration_seconds via the failure branch,
    flips to 'failed', and emits the terminal RUN_FAILED event."""
    from app.pipeline import orchestrator as orch
    from app.models import Run

    db.add(Run(
        id="EXEC_FAIL", filename="cv.docx", file_type="docx", status="running",
        started_at=datetime(2026, 6, 4, 12, 0, 0), total_duration_seconds=None,
    ))
    db.commit()

    emitter = AsyncMock()
    monkeypatch.setattr(orch, "event_emitter", emitter)
    monkeypatch.setattr(orch, "STEP_REGISTRY", _single_step_registry())

    o = orch.PipelineOrchestrator("EXEC_FAIL", tmp_path / "cv.docx", db)
    monkeypatch.setattr(o, "_copy_to_pipeline_input", lambda: str(tmp_path / "cv.docx"))
    monkeypatch.setattr(o, "execute_step", AsyncMock(side_effect=RuntimeError("stage blew up")))

    # start_time set on the first call; failure duration computed on a later call.
    monkeypatch.setattr(orch, "_now", _fake_clock(2000.0, 2017.0))

    # The handler re-raises after persisting.
    with pytest.raises(RuntimeError, match="stage blew up"):
        asyncio.run(o.execute())

    db.expire_all()
    row = db.query(Run).filter(Run.id == "EXEC_FAIL").first()
    assert row.status == "failed"
    assert row.completed_at is not None
    assert row.error_message == GENERIC_FAILURE_MESSAGE
    # Duration was recorded from the time-to-failure delta (>= 0, here exactly 17).
    assert row.total_duration_seconds is not None
    assert row.total_duration_seconds >= 0
    assert row.total_duration_seconds == 17
    emitter.emit_run_failed.assert_awaited()


def test_execute_failure_before_start_leaves_duration_none(monkeypatch, tmp_path, db):
    """If the exception fires BEFORE start_time is set in execute(), the failure
    handler's `if start_time is not None` guard must be honoured:
    total_duration_seconds stays NULL and nothing throws a TypeError.

    Forcing emit_run_start to raise reproduces a failure in emit_run_start, before
    start_time = _now() on the next line.
    """
    from app.pipeline import orchestrator as orch
    from app.models import Run

    db.add(Run(
        id="EXEC_EARLY", filename="cv.docx", file_type="docx", status="running",
        started_at=datetime(2026, 6, 4, 12, 0, 0), total_duration_seconds=None,
    ))
    db.commit()

    emitter = AsyncMock()
    emitter.emit_run_start = AsyncMock(side_effect=RuntimeError("boom before start"))
    monkeypatch.setattr(orch, "event_emitter", emitter)
    monkeypatch.setattr(orch, "STEP_REGISTRY", _single_step_registry())

    o = orch.PipelineOrchestrator("EXEC_EARLY", tmp_path / "cv.docx", db)
    # execute_step should never be reached; guard against it silently passing.
    monkeypatch.setattr(o, "_copy_to_pipeline_input", lambda: str(tmp_path / "cv.docx"))
    monkeypatch.setattr(o, "execute_step", AsyncMock(side_effect=AssertionError("must not run")))

    with pytest.raises(RuntimeError, match="boom before start"):
        asyncio.run(o.execute())

    db.expire_all()
    row = db.query(Run).filter(Run.id == "EXEC_EARLY").first()
    assert row.status == "failed"
    assert row.completed_at is not None
    assert row.error_message == GENERIC_FAILURE_MESSAGE
    # The `start_time is not None` guard was respected: no wall-clock was forced in here.
    assert row.total_duration_seconds is None


def _seed_and_wire(monkeypatch, tmp_path, db, run_id, prior, exec_step):
    """Seed a running row with a prior total and wire a stubbed orchestrator."""
    from app.pipeline import orchestrator as orch
    from app.models import Run

    db.add(Run(
        id=run_id, filename="cv.docx", file_type="docx", status="running",
        started_at=datetime(2026, 6, 4, 12, 0, 0), total_duration_seconds=prior,
    ))
    db.commit()
    monkeypatch.setattr(orch, "event_emitter", AsyncMock())
    monkeypatch.setattr(orch, "STEP_REGISTRY", _single_step_registry())
    o = orch.PipelineOrchestrator(run_id, tmp_path / "cv.docx", db)
    monkeypatch.setattr(o, "_copy_to_pipeline_input", lambda: str(tmp_path / "cv.docx"))
    monkeypatch.setattr(o, "_prepare_resume", lambda run, n: n)
    monkeypatch.setattr(o, "execute_step", exec_step)
    return orch, o


@pytest.mark.parametrize("resume_from, prior, expected", [
    (1, 100, 130),     # resumed: prior N=100 + M=30
    (1, None, 30),     # resumed with no prior total: 0 + M
    (None, 100, 30),   # fresh run: plain overwrite, prior discarded
])
def test_execute_complete_duration_retry_semantics(
    monkeypatch, tmp_path, db, resume_from, prior, expected
):
    from app.models import Run

    orch, o = _seed_and_wire(monkeypatch, tmp_path, db, "RETRY_OK", prior, AsyncMock())
    monkeypatch.setattr(orch, "_now", _fake_clock(0.0, 30.0))

    asyncio.run(o.execute(start_step_number=resume_from))

    db.expire_all()
    row = db.query(Run).filter(Run.id == "RETRY_OK").first()
    assert row.status == "complete"
    assert row.total_duration_seconds == expected


@pytest.mark.parametrize("resume_from, prior, expected", [
    (1, 100, 117),
    (1, None, 17),
    (None, 100, 17),
])
def test_execute_failure_duration_retry_semantics(
    monkeypatch, tmp_path, db, resume_from, prior, expected
):
    from app.models import Run

    orch, o = _seed_and_wire(
        monkeypatch, tmp_path, db, "RETRY_FAIL", prior,
        AsyncMock(side_effect=RuntimeError("stage blew up")),
    )
    monkeypatch.setattr(orch, "_now", _fake_clock(0.0, 17.0))

    with pytest.raises(RuntimeError, match="stage blew up"):
        asyncio.run(o.execute(start_step_number=resume_from))

    db.expire_all()
    row = db.query(Run).filter(Run.id == "RETRY_FAIL").first()
    assert row.status == "failed"
    assert row.total_duration_seconds == expected


def test_now_reads_time_monotonic(monkeypatch):
    """#598: the module clock is time.monotonic (immune to wall-clock steps)."""
    from app.pipeline import orchestrator as orch

    monkeypatch.setattr(orch.time, "monotonic", lambda: 123.5)
    assert orch._now() == 123.5


def test_execute_step_duration_uses_module_clock(monkeypatch, tmp_path):
    """The per-step duration is the _now() delta (start at 100.0, end at 125.0)."""
    from unittest.mock import MagicMock
    from app.pipeline import orchestrator as orch

    monkeypatch.setattr(orch, "event_emitter", AsyncMock())
    step = MagicMock()
    fake_db = MagicMock()
    fake_db.query.return_value.filter.return_value.first.return_value = step
    o = orch.PipelineOrchestrator("STEPDUR", tmp_path / "cv.docx", fake_db)
    monkeypatch.setattr(o, "_persist_outputs_to_storage", lambda *a: None)
    monkeypatch.setattr(o, "_sync_prompt_logs_to_storage", lambda *a: None)
    monkeypatch.setattr(o, "_record_stage_outcome", lambda *a: None)

    async def ok(stage_id, cv_path):
        return {"cost": 0.0, "output_files": []}

    monkeypatch.setattr(o, "_execute_stage_logic", ok)
    monkeypatch.setattr(orch, "_now", _fake_clock(100.0, 125.0))

    asyncio.run(o.execute_step(1, "1a", "cv.docx"))

    assert step.duration_seconds == 25


# ===========================================================================
# 2. _run_duration_seconds() helper -- cancelled / failed terminal statuses.
#    (test_run_duration covers complete + running only.)
# ===========================================================================

@pytest.mark.parametrize("status", ["cancelled", "failed"])
def test_helper_terminal_nonsuccess_prefers_persisted(status):
    """Persisted total_duration_seconds wins for cancelled/failed runs too."""
    base = datetime(2026, 6, 4, 12, 0, 0)
    run = _run(
        total_duration_seconds=37,
        started_at=base,
        completed_at=base + timedelta(seconds=300),  # wall-clock 300, persisted 37
        status=status,
    )
    assert _run_duration_seconds(run) == 37


@pytest.mark.parametrize("status", ["cancelled", "failed"])
def test_helper_terminal_nonsuccess_wallclock_fallback(status):
    """With no persisted value, cancelled/failed fall back to wall-clock."""
    base = datetime(2026, 6, 4, 12, 0, 0)
    run = _run(
        started_at=base,
        completed_at=base + timedelta(seconds=70),
        status=status,
    )
    assert _run_duration_seconds(run) == 70


def test_helper_zero_second_persisted_is_kept():
    """A persisted 0 is a real value -- 'is not None' must keep it, not treat
    it as falsy and fall through to wall-clock."""
    base = datetime(2026, 6, 4, 12, 0, 0)
    run = _run(
        total_duration_seconds=0,
        started_at=base,
        completed_at=base + timedelta(seconds=999),
        status="complete",
    )
    assert _run_duration_seconds(run) == 0


# ===========================================================================
# 3. Admin submissions Duration column -- cancelled / failed rows.
# ===========================================================================

def test_admin_runs_table_cancelled_and_failed_duration(client, db):
    """The /admin/runs Duration column resolves cancelled & failed rows the
    same way as complete: persisted preferred, wall-clock fallback."""
    from app.main import app
    from app.auth import require_admin
    from app.models import Run

    base = datetime(2026, 6, 4, 12, 0, 0)
    db.add_all([
        # Cancelled, no persisted value -> wall-clock 70s.
        Run(id="ACR001", filename="a.docx", file_type="docx", status="cancelled",
            started_at=base, completed_at=base + timedelta(seconds=70)),
        # Failed, persisted 80s must win over wall-clock 999s.
        Run(id="ACR002", filename="b.docx", file_type="docx", status="failed",
            started_at=base, completed_at=base + timedelta(seconds=999),
            total_duration_seconds=80),
    ])
    db.commit()

    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(role="admin")
    try:
        resp = client.get("/api/admin/runs")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert resp.status_code == 200
    by_id = {r["run_id"]: r["duration_seconds"] for r in resp.json()["runs"]}
    assert by_id["ACR001"] == 70   # wall-clock fallback for cancelled
    assert by_id["ACR002"] == 80   # persisted preferred for failed


def test_admin_stats_excludes_cancelled_and_failed_from_aggregates(client, db):
    """avg/p95 are computed over status=='complete' only: adding cancelled and
    failed rows (even with persisted durations) must not move the aggregates."""
    from app.main import app
    from app.auth import require_admin
    from app.models import Run

    base = datetime(2026, 6, 4, 12, 0, 0)
    completed_only = [
        Run(id="SX001", filename="a.docx", file_type="docx", status="complete",
            started_at=base, completed_at=base + timedelta(seconds=999),
            total_duration_seconds=100),
        Run(id="SX002", filename="b.docx", file_type="docx", status="complete",
            started_at=base, completed_at=base + timedelta(seconds=999),
            total_duration_seconds=200),
    ]
    db.add_all(completed_only)
    db.add_all([
        # Cancelled & failed rows carry durations but must be excluded.
        Run(id="SX003", filename="c.docx", file_type="docx", status="cancelled",
            started_at=base, completed_at=base + timedelta(seconds=10),
            total_duration_seconds=150),
        Run(id="SX004", filename="d.docx", file_type="docx", status="failed",
            started_at=base, completed_at=base + timedelta(seconds=10),
            total_duration_seconds=5000),
    ])
    db.commit()

    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(role="admin")
    try:
        resp = client.get("/api/admin/stats")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert resp.status_code == 200
    data = resp.json()
    # Only the two complete runs (100, 200) count: avg 150, p95 nearest-rank 200.
    assert data["avg_duration_seconds"] == 150.0
    assert data["p95_duration_seconds"] == 200


# ===========================================================================
# 4. /admin/stats avg + p95 edge cases -- n=1, and 0-second / sub-second runs.
# ===========================================================================

def test_admin_stats_single_completed_run(client, db):
    """n=1 nearest-rank: a single completed run is both the avg and the p95
    (pins the index math at len-1 == 0)."""
    from app.main import app
    from app.auth import require_admin
    from app.models import Run

    base = datetime(2026, 6, 4, 12, 0, 0)
    db.add(Run(
        id="ONE001", filename="a.docx", file_type="docx", status="complete",
        started_at=base, completed_at=base + timedelta(seconds=999),
        total_duration_seconds=123,
    ))
    db.commit()

    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(role="admin")
    try:
        resp = client.get("/api/admin/stats")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert resp.status_code == 200
    data = resp.json()
    assert data["avg_duration_seconds"] == 123.0
    assert data["p95_duration_seconds"] == 123


def test_admin_stats_counts_zero_second_run(client, db):
    """A persisted 0-second run is a real data point: the aggregate must include
    it (the 'is not None' check, not a falsy drop)."""
    from app.main import app
    from app.auth import require_admin
    from app.models import Run

    base = datetime(2026, 6, 4, 12, 0, 0)
    db.add_all([
        # 0-second persisted run -- must be counted, not dropped as falsy.
        Run(id="Z001", filename="a.docx", file_type="docx", status="complete",
            started_at=base, completed_at=base + timedelta(seconds=999),
            total_duration_seconds=0),
        Run(id="Z002", filename="b.docx", file_type="docx", status="complete",
            started_at=base, completed_at=base + timedelta(seconds=999),
            total_duration_seconds=10),
    ])
    db.commit()

    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(role="admin")
    try:
        resp = client.get("/api/admin/stats")
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert resp.status_code == 200
    data = resp.json()
    # Durations sorted: [0, 10]. If 0 were dropped, avg would be 10.0.
    assert data["avg_duration_seconds"] == 5.0
    assert data["p95_duration_seconds"] == 10
