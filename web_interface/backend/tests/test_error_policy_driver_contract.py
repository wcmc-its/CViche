"""Regression guard for issue #646 / CODING_STANDARDS.md §5.1 ("The error
policy belongs to the driver, not to the stage"): the same twelve stages have
opposite semantics depending on which driver runs them, and nothing enforced
that until now.

This covers the orchestrator half -- a stage exception must fail the run and
must stop the pipeline, not get logged and skipped past. Stage 4.5 (the
research summary) is the one exception, by decision on #1174: its failure is
recorded non-fatal and the run carries on (the tests at the end of this file). The opposite half
(the CLI must NOT stop the run on the same kind of failure) is already proven
by test_run_full_pipeline_exit_status.py::test_a_mid_pipeline_crash_also_exits_non_zero.
The two together are the check §5.1 was missing.
"""
import ast
import asyncio
import inspect
import json
import sys
from datetime import datetime
from pathlib import Path
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

    o = orch.PipelineOrchestrator(run_id, tmp_path / f"{run_id}.docx", db)
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
    # The user sees a fixed message, never the exception text (#592).
    from app.pipeline.orchestrator import GENERIC_FAILURE_MESSAGE
    assert run.error_message == GENERIC_FAILURE_MESSAGE


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


@pytest.mark.parametrize("env, repair", [(None, False), ("0", False), ("1", True)])
def test_stage_6_repairs_under_the_flag_and_keeps_the_repair_report(monkeypatch, tmp_path, db,
                                                                    env, repair):
    """#1389: CVICHE_RUN_REPAIR=1 asks stage 6 to repair protected data, and
    the `<uid>_repairs.json` it writes is persisted with the docx, like the
    render-warnings sidecar, so the run's record of what was cut survives the pod."""
    from app.pipeline import orchestrator as orch

    if env is None:
        monkeypatch.delenv("CVICHE_RUN_REPAIR", raising=False)
    else:
        monkeypatch.setenv("CVICHE_RUN_REPAIR", env)
    captured = {}

    def fake_run_stage6(**kwargs):
        captured.update(kwargs)
        out = tmp_path / "STAGE6REPAIR_wcm.docx"
        out.write_bytes(b"PK")
        if kwargs["repair_protected_data"]:
            (tmp_path / "STAGE6REPAIR_repairs.json").write_text("{}")
        return str(out)

    monkeypatch.setattr(orch, "run_stage6", fake_run_stage6)
    o = _orchestrator(monkeypatch, tmp_path, db, "STAGE6REPAIR")
    o.stage_outputs["5d"] = str(tmp_path / "stage5d.json")

    result = asyncio.run(o._execute_stage_logic("6", str(tmp_path / "cv.docx")))

    assert captured["repair_protected_data"] is repair
    assert (str(tmp_path / "STAGE6REPAIR_repairs.json") in result["output_files"]) is repair


# -- #1177: every stage that calls the LLM adds its cost to the run ---------


def test_stage_5b_cost_is_added_to_the_run_total(monkeypatch, tmp_path, db):
    """The orchestrator never called update_cost for 5b, so its LLM spend was
    missing from Run.total_cost."""
    from app.pipeline import orchestrator as orch

    out = tmp_path / "stage5b.json"
    out.write_text(json.dumps({"institution_enrichment_stats": {"cost": 0.25}}))
    monkeypatch.setattr(orch, "run_stage5b", lambda **kwargs: str(out))

    o = _orchestrator(monkeypatch, tmp_path, db, "STAGE5BCOST")
    result = asyncio.run(o._execute_stage_logic("5b", str(tmp_path / "cv.docx")))

    assert o.total_cost == pytest.approx(0.25)
    assert result["cost"] == pytest.approx(0.25)


def test_stage_5b_runs_without_the_on_disk_institution_cache(monkeypatch, tmp_path, db):
    """#1238: the web path calls stage 5b with persist_cache=False. The cache
    directory is not writable in the container, and a shared file would race
    across concurrent runs and worker pods."""
    from app.pipeline import orchestrator as orch

    captured = {}
    out = tmp_path / "stage5b.json"
    out.write_text(json.dumps({"institution_enrichment_stats": {"cost": 0.0}}))

    def fake_run_stage5b(**kwargs):
        captured.update(kwargs)
        return str(out)

    monkeypatch.setattr(orch, "run_stage5b", fake_run_stage5b)
    o = _orchestrator(monkeypatch, tmp_path, db, "STAGE5BCACHE")
    asyncio.run(o._execute_stage_logic("5b", str(tmp_path / "cv.docx")))

    assert captured["persist_cache"] is False


def test_stage_6_cost_is_added_to_the_run_total(monkeypatch, tmp_path, db):
    """Stage 6 hands its priced call_llm results back through the LlmUsage the
    driver passes in; the orchestrator never read them."""
    from app.pipeline import orchestrator as orch

    def fake_run_stage6(**kwargs):
        kwargs["llm_usage"].add({"cost": 0.3, "prompt_tokens": 40, "completion_tokens": 10})
        out = tmp_path / "out.docx"
        out.write_bytes(b"PK")
        return str(out)

    monkeypatch.setattr(orch, "run_stage6", fake_run_stage6)

    o = _orchestrator(monkeypatch, tmp_path, db, "STAGE6COST")
    o.stage_outputs["5d"] = str(tmp_path / "stage5d.json")
    result = asyncio.run(o._execute_stage_logic("6", str(tmp_path / "cv.docx")))

    assert o.total_cost == pytest.approx(0.3)
    from app.models import Run
    assert db.query(Run).filter(Run.id == "STAGE6COST").first().total_tokens == 50
    assert result["cost"] == pytest.approx(0.3)


def _stage_branches_that_record_cost() -> set[str]:
    """Stage ids whose `elif stage_id == 'X'` branch in _execute_stage_logic
    calls update_cost, directly or through _track_llm_cost."""
    from app.pipeline import orchestrator as orch

    tree = ast.parse(inspect.getsource(orch.PipelineOrchestrator._execute_stage_logic).lstrip())
    recorded = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.If) and isinstance(node.test, ast.Compare)
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "stage_id"
                and isinstance(node.test.comparators[0], ast.Constant)):
            continue
        calls = {n.func.attr for stmt in node.body for n in ast.walk(stmt)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        if calls & {"update_cost", "_track_llm_cost"}:
            recorded.add(node.test.comparators[0].value)
    return recorded


def test_the_orchestrator_records_cost_for_exactly_the_stages_the_cli_reports():
    """One list of cost-bearing stages: the CLI's. A stage added there but not
    recorded here (or the reverse) fails this, so neither driver can forget it."""
    import run_full_pipeline

    assert _stage_branches_that_record_cost() == set(run_full_pipeline._COST_REPORTING_STAGES)


# --- #1174: stage 4.5 never fails the run --------------------------------------

def _raise_from_stage_4_5(**_kwargs):
    raise RuntimeError("simulated research summary failure")


def test_a_stage_4_5_exception_is_returned_as_a_non_fatal_stage_error(monkeypatch, tmp_path, db):
    """The 4.5 branch catches run_stage_4_5's exception instead of raising it:
    no artifact, no cost, and a non-fatal record for execute_step to write."""
    from app.pipeline import orchestrator as orch
    from unified_pipeline.stage_errors import StageError

    monkeypatch.setattr(orch, "run_stage_4_5", _raise_from_stage_4_5)
    o = _orchestrator(monkeypatch, tmp_path, db, "S45RESULT")

    result = asyncio.run(o._execute_stage_logic("4.5", str(tmp_path / "cv.docx")))

    assert result["stage_error"] == StageError(
        stage="4.5", exception_type="RuntimeError",
        message="simulated research summary failure", fatal=False)
    assert result["output_files"] == [] and result["cost"] == 0.0
    assert "4.5" not in o.stage_outputs


def test_a_stage_4_5_cancellation_still_stops_the_run(monkeypatch, tmp_path, db):
    """A user cancel raised inside stage 4.5 is not a stage failure to carry on past."""
    from app.pipeline import orchestrator as orch

    def _cancelled(**_kwargs):
        raise orch.CancelledException("cancelled")
    monkeypatch.setattr(orch, "run_stage_4_5", _cancelled)
    o = _orchestrator(monkeypatch, tmp_path, db, "S45CANCEL")

    with pytest.raises(orch.CancelledException):
        asyncio.run(o._execute_stage_logic("4.5", str(tmp_path / "cv.docx")))


def test_a_stage_4_5_exception_lets_the_run_continue_and_records_it_non_fatal(
        monkeypatch, tmp_path, db):
    """Through the real execute()/execute_step: stage 4.5 raises, stage 5
    still runs, the run completes, and the stage-error record holds a
    non-fatal 4.5 entry (which execute_step's success path would otherwise
    have cleared)."""
    from app.models import Log, Run, Step
    from app.pipeline import orchestrator as orch
    from unified_pipeline.stage_errors import StageError, read_stage_errors

    monkeypatch.setattr(orch, "run_stage_4_5", _raise_from_stage_4_5)
    o = _orchestrator(monkeypatch, tmp_path, db, "S45CONT")
    monkeypatch.setattr(orch, "STEP_REGISTRY", [
        SimpleNamespace(number=7, stage_id="4.5", name="Research Summary"),
        SimpleNamespace(number=8, stage_id="5", name="PubMed Enrichment")])
    real_stage_logic = o._execute_stage_logic
    ran = []

    async def _stage_logic(stage_id, cv_path):
        ran.append(stage_id)
        if stage_id == "4.5":
            return await real_stage_logic(stage_id, cv_path)
        return {"output_files": [], "cost": 0.0}
    monkeypatch.setattr(o, "_execute_stage_logic", _stage_logic)

    asyncio.run(o.execute())

    assert ran == ["4.5", "5"]
    db.expire_all()
    assert db.query(Run).filter(Run.id == "S45CONT").first().status == "complete"
    step = db.query(Step).filter(Step.run_id == "S45CONT", Step.step_number == 7).first()
    assert step.status == "complete"
    assert read_stage_errors(o._stage_errors_path()) == [StageError(
        stage="4.5", exception_type="RuntimeError",
        message="simulated research summary failure", fatal=False)]
    skip_logs = db.query(Log).filter(Log.run_id == "S45CONT", Log.step_number == 7,
                                     Log.message.startswith("Research summary skipped")).all()
    assert [entry.level for entry in skip_logs] == ["WARNING"]


def test_a_successful_stage_4_5_registers_its_artifact_for_later_stages(monkeypatch, tmp_path, db):
    from app.pipeline import orchestrator as orch

    artifact = tmp_path / "TEST_research_summary.json"
    artifact.write_text(json.dumps({"research_summary": {"m1_score": 0.1}, "total_cost": 0.0}))
    monkeypatch.setattr(orch, "run_stage_4_5", lambda **_kwargs: str(artifact))
    o = _orchestrator(monkeypatch, tmp_path, db, "S45OK")

    result = asyncio.run(o._execute_stage_logic("4.5", str(tmp_path / "cv.docx")))

    assert o.stage_outputs["4.5"] == str(artifact)
    assert result["output_files"] == [str(artifact)]
    assert result.get("stage_error") is None
