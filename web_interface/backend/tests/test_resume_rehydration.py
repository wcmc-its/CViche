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
import asyncio
import logging
import shutil
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.models import Run, RunState, Step
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

    def put_file(self, run_id, key, data):
        self.store[(run_id, key)] = data


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
    path.write_text(body, encoding="utf-8")


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


# --- #1567: a resumed PDF run reuses its earlier conversion -----------------

PDF_RUN = "PDFRES"
CONVERTED_KEY = f"converted/{PDF_RUN}.docx"
# SZHPJW's case: stages 1a-4 done, the auto-retry resumes at stage 4.5.
RESUME_STEP = next(sd.number for sd in STEP_REGISTRY if sd.stage_id == "4.5")


class _Converter:
    """Stands in for pdf_sandbox.convert_pdf. Each call writes different
    bytes, so a stage that saw a re-conversion is told apart from one that
    saw the first; with `fail` set it raises instead, as a failed sandbox
    conversion does."""

    def __init__(self):
        self.calls = 0
        self.fail = False

    def __call__(self, pdf_path, docx_path):
        from app.services.pdf_sandbox import ConversionResult, PdfTooComplexError

        self.calls += 1
        if self.fail:
            raise PdfTooComplexError("convert_pdf must not run on this attempt")
        Path(docx_path).write_bytes(f"converted docx, call {self.calls}".encode())
        return ConversionResult(image_only_pages=[])


@pytest.fixture
def pdf_run(db, tmp_path, monkeypatch):
    """A PDF run over the real input materialization, a fake converter and
    storage, and an execute_step that records the bytes of the docx each
    stage was handed. `attempt(start)` runs execute() once; `recycle_pod()`
    wipes the pod-local docx."""
    from types import SimpleNamespace

    from app.pipeline import orchestrator as orch

    converter, storage = _Converter(), _FakeStorage()
    monkeypatch.setattr(orch, "PARENT_DIR", tmp_path / "repo")
    monkeypatch.setattr(orch, "event_emitter", AsyncMock())
    monkeypatch.setattr(orch, "convert_pdf", converter)
    monkeypatch.setattr(orch, "get_storage", lambda: storage)
    monkeypatch.setenv("CVICHE_RUN_DOCTOR", "0")
    upload = tmp_path / f"{PDF_RUN}.pdf"
    upload.write_bytes(b"%PDF-1.4 synthetic")
    db.add(Run(id=PDF_RUN, filename="cv.pdf", file_type="pdf", status="running",
               started_at=datetime.now()))
    db.commit()
    o = orch.PipelineOrchestrator(PDF_RUN, upload, db)
    inputs: dict[str, bytes] = {}

    async def record(step_number, stage_id, cv_path):
        inputs[stage_id] = Path(cv_path).read_bytes()
    monkeypatch.setattr(o, "execute_step", record)
    monkeypatch.setattr(o, "_notify_started", AsyncMock())
    # Stage outputs are not under test: resume exactly where asked.
    monkeypatch.setattr(o, "_prepare_resume", lambda run, start: start)

    def attempt(start=None):
        inputs.clear()
        asyncio.run(o.execute(start_step_number=start))
        db.expire_all()
        return dict(inputs), db.get(Run, PDF_RUN)

    yield SimpleNamespace(
        attempt=attempt, converter=converter, storage=storage,
        run=lambda: db.get(Run, PDF_RUN),
        local=o._pipeline_input_path(),
        recycle_pod=lambda: shutil.rmtree(tmp_path / "repo"))
    shutil.rmtree(o.web_output_dir, ignore_errors=True)


def test_pdf_resume_after_a_pod_recycle_reuses_the_stored_conversion(pdf_run):
    """#1567 acceptance: a resume past stage 1a never calls convert_pdf (here
    it raises), and every resumed stage, stage 6 included, opens the very
    docx attempt 1 converted and stage 1a segmented."""
    first, _ = pdf_run.attempt()
    assert pdf_run.storage.store[(PDF_RUN, CONVERTED_KEY)] == first["1a"]

    pdf_run.recycle_pod()
    pdf_run.converter.fail = True
    resumed, run = pdf_run.attempt(RESUME_STEP)

    assert pdf_run.converter.calls == 1  # attempt 1's only
    assert run.status == RunState.COMPLETE, run.error_message
    assert "6" in resumed
    assert resumed == {stage: first[stage] for stage in resumed}
    assert resumed["6"] == first["1a"]


def test_pdf_resume_on_the_same_pod_reuses_the_local_conversion(pdf_run):
    """The pod-local docx is used before storage is consulted, and without
    converting again."""
    first, _ = pdf_run.attempt()
    pdf_run.storage.store.clear()
    pdf_run.converter.fail = True

    resumed, run = pdf_run.attempt(RESUME_STEP)

    assert pdf_run.converter.calls == 1
    assert run.status == RunState.COMPLETE, run.error_message
    assert resumed["6"] == first["1a"]


def test_pdf_resume_with_no_surviving_conversion_converts_again(pdf_run):
    """A run whose attempt 1 predates #1567 has no stored copy: after a pod
    recycle the resume converts again, as before, and stores that docx."""
    pdf_run.attempt()
    pdf_run.recycle_pod()
    pdf_run.storage.store.clear()

    resumed, run = pdf_run.attempt(RESUME_STEP)

    assert pdf_run.converter.calls == 2
    assert run.status == RunState.COMPLETE, run.error_message
    assert resumed["6"] == b"converted docx, call 2"
    assert pdf_run.storage.store[(PDF_RUN, CONVERTED_KEY)] == resumed["6"]


def test_a_fresh_start_of_a_pdf_run_always_converts(pdf_run):
    """Only a resume reuses: a start without start_step_number converts."""
    pdf_run.attempt()
    pdf_run.attempt()
    assert pdf_run.converter.calls == 2


def test_a_failed_conversion_leaves_no_docx_for_a_resume_to_reuse(pdf_run):
    """The converter wrote part of a docx and then failed: neither the
    pod-local path nor storage holds anything a resume could pick up."""
    def write_then_fail(pdf_path, docx_path):
        Path(docx_path).write_bytes(b"half a docx")
        raise RuntimeError("child killed mid-write")
    with patch("app.pipeline.orchestrator.convert_pdf", write_then_fail), \
            pytest.raises(RuntimeError, match="mid-write"):
        pdf_run.attempt()

    assert pdf_run.run().status == RunState.FAILED
    assert not pdf_run.local.exists()
    assert list(pdf_run.local.parent.iterdir()) == []
    assert pdf_run.storage.store == {}


def test_storage_failure_storing_the_conversion_does_not_fail_the_run(pdf_run, caplog):
    def refuse(run_id, key, data):
        raise RuntimeError("s3 unreachable")
    pdf_run.storage.put_file = refuse

    with caplog.at_level(logging.WARNING, logger="app.pipeline.orchestrator"):
        _, run = pdf_run.attempt()

    assert run.status == RunState.COMPLETE, run.error_message
    [record] = [r for r in caplog.records
                if "Could not persist the converted docx" in r.getMessage()]
    assert record.exc_info and str(record.exc_info[1]) == "s3 unreachable"


def test_storage_failure_reading_the_conversion_converts_again(pdf_run, caplog):
    pdf_run.attempt()
    pdf_run.recycle_pod()

    def unreachable(run_id, key):
        raise RuntimeError("s3 unreachable")
    pdf_run.storage.get_file = unreachable

    with caplog.at_level(logging.WARNING, logger="app.pipeline.orchestrator"):
        resumed, run = pdf_run.attempt(RESUME_STEP)

    assert pdf_run.converter.calls == 2
    assert run.status == RunState.COMPLETE, run.error_message
    assert resumed["6"] == b"converted docx, call 2"
    [record] = [r for r in caplog.records
                if "Could not rehydrate the converted docx" in r.getMessage()]
    assert record.exc_info and str(record.exc_info[1]) == "s3 unreachable"
