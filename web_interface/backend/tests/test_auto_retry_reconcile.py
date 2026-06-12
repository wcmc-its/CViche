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
