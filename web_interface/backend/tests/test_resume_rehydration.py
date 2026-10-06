"""Unit tests for durable per-step retry/resume (_prepare_resume).

Guards the fix for the reported bug where retrying a late stage after a pod
recycle failed with "No input available for Stage 6". Resume now:

  - rehydrates an earlier stage's output from durable storage when the
    pod-local file is gone (e.g. the pod recycled, wiping the ephemeral
    outputs dir); and
  - when an output is unrecoverable from both disk and storage, backs the
    resume point up so the missing stage is recomputed rather than
    dead-ending a downstream stage.

Cost accounting continues from the persisted per-step costs of the kept
stages (those before the effective resume point).
"""
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

from app.models import Run, Step
from app.pipeline.orchestrator import PipelineOrchestrator
from app.pipeline.step_registry import STEP_REGISTRY

RUN_ID = "RESUMET"
# stage_id of each step number, in execution order.
STAGE_BY_STEP = {sd.number: sd.stage_id for sd in STEP_REGISTRY}
LAST_STEP = max(STAGE_BY_STEP)  # Stage 6 == step 12


class _FakeStorage:
    """In-memory storage stub. Keys present in `store` are retrievable; any
    other key raises FileNotFoundError, mirroring RunStorage.get_file."""

    def __init__(self, store=None):
        self.store = dict(store or {})

    def get_file(self, run_id, key):
        try:
            return self.store[(run_id, key)]
        except KeyError as exc:
            raise FileNotFoundError(key) from exc


def _make_orchestrator(db):
    """Build an orchestrator without __init__ (no filesystem side effects)."""
    orch = PipelineOrchestrator.__new__(PipelineOrchestrator)
    orch.run_id = RUN_ID
    orch.db = db
    orch.stage_outputs = {}
    orch.total_cost = 0.0
    orch.document_uid = RUN_ID
    return orch


def _output_paths(tmp_path):
    """Expected on-disk output path per stage, under a temp dir."""
    return {
        sd.stage_id: tmp_path / f"{RUN_ID}_{sd.stage_id}.json"
        for sd in STEP_REGISTRY
    }


def _seed_steps(db, complete_through, cost_each=1.0):
    """Seed a Run plus 12 Step rows; steps 1..complete_through are complete
    (with a cost), the rest pending (cost NULL). The run's total_cost mirrors
    the sum of the completed steps, as it would in production."""
    db.add(Run(id=RUN_ID, filename="cv.docx", file_type="docx", status="failed",
               total_cost=complete_through * cost_each))
    for sd in STEP_REGISTRY:
        complete = sd.number <= complete_through
        db.add(Step(
            run_id=RUN_ID,
            step_number=sd.number,
            stage_id=sd.stage_id,
            step_name=sd.name,
            status="complete" if complete else "pending",
            cost=cost_each if complete else None,
        ))
    db.commit()


def _write(path: Path, body: str = "{}"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def _run_prepare(db, tmp_path, start_step, storage):
    orch = _make_orchestrator(db)
    orch.pipeline_output_dir = tmp_path / "pipeline_outputs"
    paths = _output_paths(tmp_path)
    run = db.query(Run).filter(Run.id == RUN_ID).first()
    with patch.object(orch, "_get_output_paths", return_value=paths), \
         patch("app.pipeline.orchestrator.get_storage", return_value=storage):
        effective = orch._prepare_resume(run, start_step)
    return orch, paths, effective


def test_all_prior_present_locally_resumes_at_requested_step(db, tmp_path):
    """Happy path: every earlier output is on local disk -> resume exactly at the
    requested step, all prior outputs registered, cost = kept steps' costs."""
    _seed_steps(db, complete_through=LAST_STEP - 1)
    paths = _output_paths(tmp_path)
    for step, stage in STAGE_BY_STEP.items():
        if step < LAST_STEP:
            _write(paths[stage])

    orch, _, effective = _run_prepare(db, tmp_path, LAST_STEP, _FakeStorage())

    assert effective == LAST_STEP
    # Every stage before the last is registered for downstream resolution.
    assert set(orch.stage_outputs) == {
        s for n, s in STAGE_BY_STEP.items() if n < LAST_STEP
    }
    assert orch.total_cost == pytest.approx(LAST_STEP - 1)  # run.total_cost, no back-up


def test_missing_local_output_rehydrated_from_storage(db, tmp_path):
    """Stage 4's local output is gone but present in durable storage -> it is
    fetched back to disk and resume still proceeds at the requested step."""
    _seed_steps(db, complete_through=LAST_STEP - 1)
    paths = _output_paths(tmp_path)
    for step, stage in STAGE_BY_STEP.items():
        if step < LAST_STEP and stage != "4":
            _write(paths[stage])

    stage4_path = paths["4"]
    storage = _FakeStorage({(RUN_ID, f"outputs/{stage4_path.name}"): b'{"stage4": true}'})

    orch, _, effective = _run_prepare(db, tmp_path, LAST_STEP, storage)

    assert effective == LAST_STEP  # recoverable -> no back-up
    assert "4" in orch.stage_outputs
    assert stage4_path.exists()  # rehydrated to local disk
    assert stage4_path.read_bytes() == b'{"stage4": true}'
    assert orch.total_cost == pytest.approx(LAST_STEP - 1)


def test_unrecoverable_output_backs_up_resume_point(db, tmp_path):
    """Stage 4 missing locally AND absent from storage -> resume backs up to
    step 6 (Stage 4) to recompute it, instead of dead-ending Stage 6."""
    _seed_steps(db, complete_through=LAST_STEP - 1)
    paths = _output_paths(tmp_path)
    for step, stage in STAGE_BY_STEP.items():
        if step < LAST_STEP and stage != "4":
            _write(paths[stage])

    orch, _, effective = _run_prepare(db, tmp_path, LAST_STEP, _FakeStorage())

    stage4_step = next(n for n, s in STAGE_BY_STEP.items() if s == "4")
    assert effective == stage4_step  # backed up to recompute Stage 4
    # Only the stages strictly before the gap are registered.
    assert set(orch.stage_outputs) == {
        s for n, s in STAGE_BY_STEP.items() if n < stage4_step
    }
    # Cost kept = the (stage4_step - 1) stages before the gap.
    assert orch.total_cost == pytest.approx(stage4_step - 1)


def test_nothing_available_falls_back_to_full_recompute(db, tmp_path):
    """No local outputs and empty storage (e.g. recycle, local storage backend)
    -> resume from step 1, a full recompute, with zero carried cost."""
    _seed_steps(db, complete_through=LAST_STEP - 1)

    orch, _, effective = _run_prepare(db, tmp_path, LAST_STEP, _FakeStorage())

    assert effective == 1
    assert orch.stage_outputs == {}
    assert orch.total_cost == pytest.approx(0.0)


def test_storage_error_is_logged_and_treated_as_missing(db, tmp_path, caplog):
    """A storage backend that raises on get_file must not crash resume; the
    output is treated as missing and the resume point backs up. The failure
    is logged with the stage, the run and its traceback -- it used to be
    `except Exception: pass`, which left a misconfigured S3 untraceable (#305)."""
    _seed_steps(db, complete_through=LAST_STEP - 1)
    paths = _output_paths(tmp_path)
    # Only the first stage exists locally; everything else is gone.
    first_stage = STAGE_BY_STEP[1]
    _write(paths[first_stage])

    class _BoomStorage:
        def get_file(self, run_id, key):
            raise RuntimeError("s3 unreachable")

    with caplog.at_level(logging.WARNING, logger="app.pipeline.orchestrator"):
        orch, _, effective = _run_prepare(db, tmp_path, LAST_STEP, _BoomStorage())

    # Backs up to step 2 (first missing output after the one present locally).
    assert effective == 2
    assert set(orch.stage_outputs) == {first_stage}
    assert orch.total_cost == pytest.approx(1.0)
    [record] = [r for r in caplog.records if "Could not rehydrate stage" in r.getMessage()]
    assert record.levelno == logging.WARNING
    assert f"stage {STAGE_BY_STEP[2]} output for run {RUN_ID}" in record.getMessage()
    assert record.exc_info and str(record.exc_info[1]) == "s3 unreachable"


def test_object_absent_from_storage_is_not_logged_as_a_failure(db, tmp_path, caplog):
    """FileNotFoundError is the expected "never mirrored" case: it backs up
    like any gap, and only the back-up's own info line reports it."""
    _seed_steps(db, complete_through=LAST_STEP - 1)

    with caplog.at_level(logging.WARNING, logger="app.pipeline.orchestrator"):
        _, _, effective = _run_prepare(db, tmp_path, LAST_STEP, _FakeStorage())

    assert effective == 1
    assert not [r for r in caplog.records if "Could not rehydrate stage" in r.getMessage()]


def test_stage_error_record_rehydrated_from_storage(db, tmp_path):
    """#745: a resumed run's stage-error record follows it onto a fresh pod.
    Without it, the retried stage succeeding could not clear its entry, and the
    durable copy -- which the scorer reads -- would keep capping the score."""
    from unified_pipeline.stage_errors import stage_errors_path

    _seed_steps(db, complete_through=LAST_STEP - 1)
    paths = _output_paths(tmp_path)
    for step, stage in STAGE_BY_STEP.items():
        if step < LAST_STEP:
            _write(paths[stage])
    record = b'[{"stage": "6", "exception_type": "ValueError", "message": "m", "fatal": true}]'
    storage = _FakeStorage({(RUN_ID, f"outputs/{RUN_ID}_stage_errors.json"): record})

    orch, _, _ = _run_prepare(db, tmp_path, LAST_STEP, storage)

    local = stage_errors_path(orch.pipeline_output_dir, RUN_ID)
    assert local.read_bytes() == record
