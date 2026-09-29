"""The periodic stale-run reaper loop (app.main._stale_run_reaper_loop) must
sweep on its interval, survive a transient sweep error, and cancel cleanly.

The reaping logic itself (reconcile_stale_runs) is covered by
test_auto_retry_reconcile / test_release_guards; this only exercises the
loop plumbing added for the steady-state backstop.
"""
import asyncio

import app.main as main_mod


def test_reaper_loop_sweeps_repeatedly_and_survives_errors(monkeypatch):
    calls = []

    def fake_reconcile(db):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("transient db blip")   # first sweep fails
        return 2

    monkeypatch.setattr("app.services.run_service.reconcile_stale_runs", fake_reconcile)

    async def run_briefly():
        task = asyncio.create_task(main_mod._stale_run_reaper_loop(0.01))
        await asyncio.sleep(0.1)          # ~10 intervals
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_briefly())

    # Survived the first sweep raising and kept going -> multiple sweeps ran.
    assert len(calls) >= 2


# --- shutdown drain (#116): app.main._drain_runs_before_exit ------------------

def _drain_fixture_runs(monkeypatch):
    """Point the drain at the test DB and a fast poll; seed two runs."""
    from datetime import datetime, timedelta

    from app.models import Run
    from app.pipeline import concurrency
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr("app.database.SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(concurrency, "DRAIN_POLL_SECONDS", 0.01)
    monkeypatch.setenv("CVICHE_MAX_CONCURRENT_RUNS", "3")
    db = TestingSessionLocal()
    started = datetime.now() - timedelta(minutes=10)
    db.add(Run(id="LONG01", filename="a.docx", file_type="docx", status="running", started_at=started))
    db.add(Run(id="QUICK2", filename="b.docx", file_type="docx", status="running", started_at=started))
    db.commit()
    assert concurrency.try_acquire_slot("LONG01") is True
    assert concurrency.try_acquire_slot("QUICK2") is True
    return db, concurrency


def _finish_run(db, concurrency, run_id):
    from app.models import Run

    db.query(Run).filter(Run.id == run_id).update({"status": "complete"})
    db.commit()
    concurrency.release_slot(run_id)


def test_drain_waits_for_active_runs_then_returns(monkeypatch):
    db, concurrency = _drain_fixture_runs(monkeypatch)

    async def finish_both_later():
        await asyncio.sleep(0.05)
        _finish_run(db, concurrency, "QUICK2")
        await asyncio.sleep(0.05)
        _finish_run(db, concurrency, "LONG01")

    async def shut_down():
        finisher = asyncio.create_task(finish_both_later())
        await main_mod._drain_runs_before_exit(budget_seconds=5)
        # The drain must not return while a run still holds a slot.
        assert finisher.done()

    asyncio.run(shut_down())

    assert concurrency.try_acquire_slot("AFTER3") is False   # draining
    db.expire_all()
    from app.models import Run
    assert {r.id: r.status for r in db.query(Run)} == {"LONG01": "complete", "QUICK2": "complete"}
    db.close()


def test_drain_fails_runs_still_executing_at_the_budget(monkeypatch):
    from app.models import Run
    from app.services.run_service import DEPLOY_INTERRUPT_MESSAGE

    db, concurrency = _drain_fixture_runs(monkeypatch)

    async def shut_down():
        async def finish_quick_run():
            await asyncio.sleep(0.02)
            _finish_run(db, concurrency, "QUICK2")

        finisher = asyncio.create_task(finish_quick_run())
        await main_mod._drain_runs_before_exit(budget_seconds=0.2)
        await finisher

    asyncio.run(shut_down())

    db.expire_all()
    long_run = db.query(Run).filter(Run.id == "LONG01").one()
    quick_run = db.query(Run).filter(Run.id == "QUICK2").one()
    assert long_run.status == "failed"
    assert long_run.error_message == DEPLOY_INTERRUPT_MESSAGE
    assert quick_run.status == "complete"
    assert quick_run.error_message is None
    # The leftover run's executor is told to stop; the finished one is not.
    from app.pipeline import orchestrator
    assert orchestrator.is_cancelled("LONG01") is True
    assert orchestrator.is_cancelled("QUICK2") is False
    db.close()


def test_drain_budget_reads_config_with_default(monkeypatch):
    monkeypatch.delenv("CVICHE_SHUTDOWN_DRAIN_SECONDS", raising=False)
    monkeypatch.setattr(main_mod, "get_config", lambda section, key, default=None: (default, "default"))
    assert main_mod._shutdown_drain_seconds() == main_mod.DEFAULT_SHUTDOWN_DRAIN_SECONDS
    monkeypatch.setattr(main_mod, "get_config", lambda section, key, default=None: ("7", "env"))
    assert main_mod._shutdown_drain_seconds() == 7


def test_lifespan_shutdown_runs_the_drain_with_the_configured_budget(monkeypatch):
    from unittest.mock import AsyncMock, patch

    from fastapi.testclient import TestClient

    from tests.conftest import TestingSessionLocal, engine

    from app.pipeline import redis_broker
    from app.pipeline.event_emitter import event_emitter

    # Runs still draining must be able to emit and receive cancels, so the
    # drain has to come before the emitter and the broker are shut down.
    order = []
    drain = AsyncMock(side_effect=lambda budget: order.append("drain"))
    monkeypatch.setattr(main_mod, "_drain_runs_before_exit", drain)
    real_emitter_shutdown = event_emitter.shutdown

    async def recording_emitter_shutdown():
        order.append("emitter")
        await real_emitter_shutdown()

    monkeypatch.setattr(event_emitter, "shutdown", recording_emitter_shutdown)
    real_broker_from_env = redis_broker.broker_from_env

    def recording_broker_from_env():
        broker = real_broker_from_env()
        real_broker_shutdown = broker.shutdown

        async def recording_broker_shutdown():
            order.append("broker")
            await real_broker_shutdown()

        broker.shutdown = recording_broker_shutdown
        return broker

    monkeypatch.setattr(redis_broker, "broker_from_env", recording_broker_from_env)
    monkeypatch.setenv("CVICHE_SHUTDOWN_DRAIN_SECONDS", "42")
    with patch("app.database.SessionLocal", TestingSessionLocal), \
         patch("app.database.engine", engine):
        with TestClient(main_mod.app):
            drain.assert_not_awaited()   # startup must not drain
    drain.assert_awaited_once_with(42)
    assert order == ["drain", "emitter", "broker"]


def test_drain_survives_a_db_error_marking_runs_failed(monkeypatch, caplog):
    """The rest of shutdown (emitter, broker, notification flush) must still run
    when failing the leftover runs raises."""
    from app.pipeline import concurrency

    def broken_fail(db, run_ids):
        raise RuntimeError("database went away")

    monkeypatch.setattr("app.services.run_service.fail_runs_interrupted_by_shutdown", broken_fail)
    monkeypatch.setenv("CVICHE_MAX_CONCURRENT_RUNS", "3")
    assert concurrency.try_acquire_slot("ORPHN1") is True

    with caplog.at_level("ERROR", logger="app.main"):
        asyncio.run(main_mod._drain_runs_before_exit(budget_seconds=0))

    assert "could not mark runs failed: ORPHN1" in caplog.text


def test_a_run_the_drain_failed_is_not_flipped_to_complete_by_its_executor(monkeypatch, tmp_path):
    """If the process outlives the drain, the run's still-running executor must
    not write "complete" over the deploy failure (a user told to restart would
    otherwise get a duplicate run)."""
    from datetime import datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.models import Run
    from app.pipeline import concurrency
    from app.pipeline import orchestrator as orch
    from app.services.run_service import DEPLOY_INTERRUPT_MESSAGE
    from tests.conftest import TestingSessionLocal

    monkeypatch.setattr("app.database.SessionLocal", TestingSessionLocal)
    monkeypatch.setenv("CVICHE_MAX_CONCURRENT_RUNS", "3")
    db = TestingSessionLocal()
    db.add(Run(id="FLIP01", filename="cv.docx", file_type="docx", status="running",
               started_at=datetime.now()))
    db.commit()
    assert concurrency.try_acquire_slot("FLIP01") is True

    monkeypatch.setattr(orch, "event_emitter", AsyncMock())
    monkeypatch.setattr(orch, "STEP_REGISTRY", [SimpleNamespace(number=1, stage_id="1a", name="x")])
    executor = orch.PipelineOrchestrator("FLIP01", tmp_path / "cv.docx", db)
    monkeypatch.setattr(executor, "_copy_to_pipeline_input", lambda: str(tmp_path / "cv.docx"))

    async def last_stage_outlives_the_drain(*args):
        # Shutdown's drain runs out of budget while this stage is executing.
        await main_mod._drain_runs_before_exit(budget_seconds=0)

    monkeypatch.setattr(executor, "execute_step", last_stage_outlives_the_drain)

    asyncio.run(executor.execute())

    db.expire_all()
    run = db.query(Run).filter(Run.id == "FLIP01").one()
    assert run.status == "failed"
    assert run.error_message == DEPLOY_INTERRUPT_MESSAGE
    db.close()


def test_drain_stops_leftover_runs_before_failing_them(monkeypatch):
    """Stop first, then fail: a run stopped after its failure is written could
    pass its pre-"complete" cancel check in between and overwrite the failure."""
    from app.pipeline import concurrency

    order = []
    monkeypatch.setattr("app.pipeline.orchestrator.stop_run_locally",
                        lambda run_id: order.append(("stop", run_id)))

    def recording_fail(db, run_ids):
        order.append(("fail", tuple(run_ids)))
        return len(run_ids)

    monkeypatch.setattr("app.services.run_service.fail_runs_interrupted_by_shutdown", recording_fail)
    monkeypatch.setenv("CVICHE_MAX_CONCURRENT_RUNS", "3")
    assert concurrency.try_acquire_slot("ORDR01") is True

    asyncio.run(main_mod._drain_runs_before_exit(budget_seconds=0))

    assert order == [("stop", "ORDR01"), ("fail", ("ORDR01",))]
