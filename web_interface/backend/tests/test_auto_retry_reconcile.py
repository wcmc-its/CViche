"""Tests for auto-retry wiring into the stale-run reaper (issue #145).

These exercise ``reconcile_stale_runs`` with the auto-retry feature flag both
OFF (the default) and ON, asserting the safety property that matters most: with
the flag off, behaviour is byte-for-byte the pre-#145 behaviour (stale running
run -> failed, steps -> error, attempt_count untouched). With the flag on and a
run under its attempt budget, the run is instead transitioned for resume and a
background launch is requested -- but the launch itself is patched out so no
pipeline ever runs.

The DB-state transition (``_transition_run_for_retry``) is kept separate from
the thread launch (``_launch_resume``) precisely so these tests can assert the
transition deterministically while stubbing the launch.
"""
import os
os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

from datetime import datetime, timedelta

import pytest

from app.models import Run, Step
from app.pipeline.step_registry import STEP_REGISTRY
from app.services import run_service


def _seed_stale_running_run(db, run_id="STALE1", *, attempt_count=1,
                            failed_at=6, last_error_type="api_error"):
    """A run stuck "running" past the stale threshold (pod died mid-stage).

    Steps before ``failed_at`` are complete; the step at ``failed_at`` is the one
    that was executing when the pod recycled (status "running" with an
    error_type); everything after is still pending.
    """
    run = Run(
        id=run_id, filename="cv.docx", file_type="docx", status="running",
        started_at=datetime.now() - timedelta(minutes=120),
        attempt_count=attempt_count,
    )
    db.add(run)
    for sd in STEP_REGISTRY:
        if sd.number < failed_at:
            status, etype = "complete", None
        elif sd.number == failed_at:
            status, etype = "running", last_error_type
        else:
            status, etype = "pending", None
        db.add(Step(
            run_id=run_id, step_number=sd.number, stage_id=sd.stage_id,
            step_name=sd.name, status=status, error_type=etype,
        ))
    db.commit()
    return run


# --- flag OFF (default): regression guard -----------------------------------

def test_flag_off_stale_run_is_failed_not_retried(db, monkeypatch):
    """Default env (flag unset): the pre-#145 behaviour is preserved exactly --
    the stale run is marked failed, the executing step becomes error, and
    attempt_count is NOT bumped. No resume launch is attempted."""
    launched = []
    monkeypatch.setattr(
        run_service, "_launch_resume",
        lambda *a, **k: launched.append((a, k)) or True,
    )

    run = _seed_stale_running_run(db, run_id="OFF001", failed_at=6)

    swept = run_service.reconcile_stale_runs(db)

    assert swept == 1                       # counted as a failed sweep
    assert launched == []                   # no resume attempted
    db.refresh(run)
    assert run.status == "failed"
    assert run.completed_at is not None
    assert run.attempt_count == 1           # untouched
    assert "restart" in (run.error_message or "").lower()

    step6 = db.query(Step).filter(
        Step.run_id == "OFF001", Step.step_number == 6
    ).first()
    assert step6.status == "error"          # the executing step is failed


# --- flag ON + under cap: resume path ---------------------------------------

def test_flag_on_under_cap_transitions_and_launches(db, monkeypatch):
    """Flag on and attempt_count under the cap: the run is transitioned for
    resume (attempt_count bumped, status back to running, resume-from step and
    downstream reset to pending) and the launch is requested with the right
    start step. The launch is patched, so no pipeline runs."""
    monkeypatch.setenv("CVICHE_AUTO_RETRY_ENABLED", "1")

    calls = []
    monkeypatch.setattr(
        run_service, "_launch_resume",
        lambda run_id, file_path, start_step_number: (
            calls.append((run_id, start_step_number)) or True
        ),
    )
    # _schedule_auto_retry resolves the upload via runs._materialize_input_if_missing
    # (a no-op when durable storage has no copy), then calls the patched
    # _launch_resume -- so no pipeline and no real file are needed here.

    run = _seed_stale_running_run(
        db, run_id="ON0001", attempt_count=1, failed_at=6, last_error_type="api_error"
    )

    swept = run_service.reconcile_stale_runs(db)

    # Resumed runs are NOT counted as failed sweeps.
    assert swept == 0
    assert len(calls) == 1
    assert calls[0][0] == "ON0001"
    assert calls[0][1] == 6                  # resume from the first non-complete step

    db.refresh(run)
    assert run.status == "running"           # back to running, not failed
    assert run.attempt_count == 2            # bumped
    assert run.error_message is None
    assert run.completed_at is None

    steps = {s.step_number: s for s in db.query(Step).filter(Step.run_id == "ON0001")}
    assert steps[5].status == "complete"     # before resume point: untouched
    assert steps[6].status == "pending"      # resume-from step: reset
    assert steps[6].error_type is None
    assert steps[12].status == "pending"     # downstream: reset


# --- flag ON + at cap: NOT retried ------------------------------------------

def test_flag_on_at_cap_is_failed_not_retried(db, monkeypatch):
    """Flag on but attempt_count already at the cap: the budget is exhausted,
    so the run is marked failed and the resume launch is NEVER called."""
    monkeypatch.setenv("CVICHE_AUTO_RETRY_ENABLED", "1")

    launched = []
    monkeypatch.setattr(
        run_service, "_launch_resume",
        lambda *a, **k: launched.append((a, k)) or True,
    )

    from app.services import auto_retry
    at_cap = auto_retry.max_auto_retries() + 1  # over the cap -> not eligible

    run = _seed_stale_running_run(
        db, run_id="CAP001", attempt_count=at_cap, failed_at=6
    )

    swept = run_service.reconcile_stale_runs(db)

    assert swept == 1                        # failed, counted
    assert launched == []                    # NOT retried
    db.refresh(run)
    assert run.status == "failed"
    assert run.attempt_count == at_cap       # untouched


# --- a resumed run is not re-reaped (#145, 2026-09-29 U2MUQ5) ---------------

def _patch_launch(monkeypatch, calls):
    monkeypatch.setattr(
        run_service, "_launch_resume",
        lambda run_id, file_path, start_step_number: calls.append(run_id) or True,
    )


def test_resumed_run_is_not_stale_on_the_next_sweep(db, monkeypatch):
    """The resume restarts started_at, so a second sweep (another pod's startup
    reconcile, or the next periodic pass) leaves the resumed run alone instead
    of retrying it again or failing it while it executes."""
    monkeypatch.setenv("CVICHE_AUTO_RETRY_ENABLED", "1")
    calls = []
    _patch_launch(monkeypatch, calls)
    run = _seed_stale_running_run(db, run_id="RSWEEP", attempt_count=1)

    assert run_service.reconcile_stale_runs(db) == 0
    assert run_service.reconcile_stale_runs(db) == 0

    assert calls == ["RSWEEP"]              # launched once, not twice
    db.refresh(run)
    assert run.status == "running"
    assert run.attempt_count == 2
    assert run.started_at > datetime.now() - timedelta(minutes=1)


def test_sweep_with_a_stale_snapshot_loses_the_claim(db, monkeypatch):
    """Two pods read the same stale run. The first resumes it; the second still
    holds the started_at it read, so both its retry and its fail path lose the
    atomic claim and change nothing."""
    monkeypatch.setenv("CVICHE_AUTO_RETRY_ENABLED", "1")
    calls = []
    _patch_launch(monkeypatch, calls)
    run = _seed_stale_running_run(db, run_id="RRACE1", attempt_count=1)
    seen_by_second_pod = run.started_at

    assert run_service.reconcile_stale_runs(db) == 0     # first pod resumes

    assert run_service._transition_run_for_retry(
        run, db, 6, seen_by_second_pod
    ) is False
    assert run_service._claim_stale_run(
        db, run, seen_by_second_pod, status="failed"
    ) is False
    db.commit()

    db.refresh(run)
    assert run.status == "running"
    assert run.attempt_count == 2
    assert calls == ["RRACE1"]


def test_sweep_does_not_fail_a_run_a_sibling_resumed_mid_sweep(db, monkeypatch):
    """The fail path claims too: if a sibling pod resumes the run after this
    sweep's query but before its write, the sweep leaves the run running."""
    real_resume_info = run_service._resume_info_for_run

    def sibling_resumes_first(run, session):
        session.query(Run).filter(Run.id == run.id).update(
            {"started_at": datetime.now(), "attempt_count": 2},
            synchronize_session=False,
        )
        session.commit()
        return real_resume_info(run, session)

    monkeypatch.setattr(run_service, "_resume_info_for_run", sibling_resumes_first)
    run = _seed_stale_running_run(db, run_id="RRACE2", attempt_count=1)

    assert run_service.reconcile_stale_runs(db) == 0

    db.refresh(run)
    assert run.status == "running"
    assert run.completed_at is None


def test_launch_resume_releases_the_slot_it_took_for_the_run(db, monkeypatch, tmp_path):
    """The resumed pipeline's thread must release the slot held for *this* run:
    the shutdown drain (#116) waits on slot-holding run ids, so a slot left
    behind would hold every deploy for the full drain budget."""
    import time
    from unittest.mock import AsyncMock, MagicMock

    from app.pipeline import concurrency
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr("app.database.SessionLocal", TestingSessionLocal)
    orchestrator_cls = MagicMock()
    orchestrator_cls.return_value.execute = AsyncMock(return_value=None)
    monkeypatch.setattr("app.pipeline.orchestrator.PipelineOrchestrator", orchestrator_cls)

    assert run_service._launch_resume("RSUME1", tmp_path / "cv.docx", 4) is True

    deadline = time.monotonic() + 5
    while concurrency.active_run_ids() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert concurrency.active_run_ids() == []
    orchestrator_cls.return_value.execute.assert_awaited_once_with(start_step_number=4)
