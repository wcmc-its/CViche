"""Regression guard for issue #646 / CODING_STANDARDS.md §5.1 ("The error
policy belongs to the driver, not to the stage"): the same twelve stages have
opposite semantics depending on which driver runs them, and nothing enforced
that until now.

This covers the orchestrator half -- a stage exception must fail the run and
must stop the pipeline, not get logged and skipped past. The opposite half
(the CLI must NOT stop the run on the same kind of failure) is already proven
by test_run_full_pipeline_exit_status.py::test_a_mid_pipeline_crash_also_exits_non_zero.
The two together are the check §5.1 was missing.
"""
import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _orchestrator(monkeypatch, tmp_path, db, run_id):
    """A run-to-completion orchestrator with the real pipeline stubbed out,
    mirroring test_run_doctor_integration._orchestrator."""
    from app.pipeline import orchestrator as orch
    from app.models import Run

    db.add(Run(
        id=run_id, filename="cv.docx", file_type="docx", status="running",
        started_at=datetime(2026, 7, 1, 12, 0, 0),
    ))
    db.commit()

    monkeypatch.setattr(orch, "event_emitter", AsyncMock())
    monkeypatch.setattr(
        orch, "STEP_REGISTRY",
        [SimpleNamespace(number=1, stage_id="1a", name="Hierarchy Extraction"),
         SimpleNamespace(number=2, stage_id="2", name="Entry Extraction")],
    )

    o = orch.PipelineOrchestrator(run_id, tmp_path / "cv.docx", db)
    monkeypatch.setattr(o, "_copy_to_pipeline_input", lambda: str(tmp_path / "cv.docx"))
    monkeypatch.setattr(o, "pipeline_output_dir", tmp_path / "outputs")
    return o


def test_a_stage_exception_fails_the_run_and_stops_the_pipeline(monkeypatch, tmp_path, db):
    """Opposite of the CLI: one stage raising must fail the whole run rather
    than being recorded and skipped past."""
    from app.models import Run

    calls = []

    async def _boom(stage_id, cv_path):
        calls.append(stage_id)
        raise RuntimeError("simulated stage failure")

    o = _orchestrator(monkeypatch, tmp_path, db, "ERRPOLICY")
    # Raise from the real execute_step's call site (_execute_stage_logic)
    # rather than replacing execute_step itself, so execute_step's own
    # try/except/raise -- catch the stage exception, mark the run failed,
    # re-raise to stop the pipeline -- is the code under test, not bypassed
    # by the mock.
    monkeypatch.setattr(o, "_execute_stage_logic", _boom)

    with pytest.raises(RuntimeError, match="simulated stage failure"):
        asyncio.run(o.execute())

    # Only the failing stage ran -- the orchestrator does not continue on to
    # stage "2" the way the CLI's swallow-and-continue would.
    assert calls == ["1a"]

    db.expire_all()
    run = db.query(Run).filter(Run.id == "ERRPOLICY").first()
    assert run is not None, "run row must survive the failed execute() call"
    assert run.status == "failed"
    assert "simulated stage failure" in run.error_message


def test_stage_4_receives_the_real_resolved_cv_path(monkeypatch, tmp_path, db):
    """#456: the owner-name side-channel tier (stage4/owner_name.py) can only
    ever fire on the web driver if stage 4 is handed a real, openable .docx
    path. Before this, `_execute_stage_logic` called `run_stage_4` with a
    reconstructed `f"{uid}.docx"` that never resolved to a file on disk --
    contrast stages 1b/2 in the same method, which always passed the real
    `cv_path`. This exercises the real `_execute_stage_logic` call site
    directly (not `execute()`) and inspects the kwargs `run_stage_4` actually
    receives.
    """
    from app.pipeline import orchestrator as orch

    captured = {}

    def fake_run_stage_4(**kwargs):
        captured.update(kwargs)
        return {"output": {}, "output_path": str(tmp_path / "stage4.json")}

    monkeypatch.setattr(orch, "run_stage_4", fake_run_stage_4)

    o = _orchestrator(monkeypatch, tmp_path, db, "STAGE4PATH")
    real_cv_path = str(tmp_path / "cv.docx")

    result = asyncio.run(o._execute_stage_logic("4", real_cv_path))

    assert captured.get("docx_path") == real_cv_path
    assert captured["docx_path"] != f"{o.document_uid}.docx", \
        "must not regress to the synthetic, non-existent filename"
    assert result["output_files"] == [str(tmp_path / "stage4.json")]


def test_stage_6_receives_the_real_resolved_cv_path(monkeypatch, tmp_path, db):
    """#550: stage 6's personal-data fallback reopens the source .docx. Until
    now this driver passed no path and the fallback only ran because
    `_copy_to_pipeline_input` happens to drop the upload into the same
    directory stage 6's SAMPLE_CV_DIR auto-discovery scans, under the same
    uid, from the same CWD. Pass it explicitly, like stages 1b/2/4 do.
    """
    from app.pipeline import orchestrator as orch
    from app.models import Run

    captured = {}

    def fake_run_stage6(**kwargs):
        captured.update(kwargs)
        out = tmp_path / "out.docx"
        out.write_bytes(b"PK")
        return str(out)

    monkeypatch.setattr(orch, "run_stage6", fake_run_stage6)
    monkeypatch.setattr(orch, "run_doctor", lambda *a, **k: None, raising=False)

    o = _orchestrator(monkeypatch, tmp_path, db, "STAGE6PATH")
    o.stage_outputs["5d"] = str(tmp_path / "stage5d.json")
    real_cv_path = str(tmp_path / "cv.docx")

    asyncio.run(o._execute_stage_logic("6", real_cv_path))

    assert captured.get("original_doc_path") == real_cv_path
