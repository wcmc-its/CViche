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

    drain = AsyncMock()
    monkeypatch.setattr(main_mod, "_drain_runs_before_exit", drain)
    monkeypatch.setenv("CVICHE_SHUTDOWN_DRAIN_SECONDS", "42")
    with patch("app.database.SessionLocal", TestingSessionLocal), \
         patch("app.database.engine", engine):
        with TestClient(main_mod.app):
            drain.assert_not_awaited()   # startup must not drain
    drain.assert_awaited_once_with(42)
