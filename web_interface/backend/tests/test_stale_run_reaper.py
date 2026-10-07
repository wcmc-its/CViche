"""The periodic stale-run reaper loop (app.main._stale_run_reaper_loop) must
sweep on its interval, survive a transient sweep error, and cancel cleanly.

The "running"-row reaping logic itself (reconcile_stale_runs) is covered by
test_auto_retry_reconcile / test_release_guards; this file exercises the loop
plumbing added for the steady-state backstop, plus (#701) the queued-run
reconciler (reconcile_queued_runs) and the queue-mode clamp on
reconcile_stale_runs's own threshold -- both unit-tested directly here, the
same way the "running" reaper is unit-tested in test_release_guards.py rather
than through the lifespan.
"""
import asyncio
import types
from datetime import datetime, timedelta

import fakeredis
import pytest
import redis

import app.main as main_mod
from app.models import Run, RunBatch, RunState, User
from app.pipeline import run_queue
from app.services import run_service


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


def test_reaper_loop_also_sweeps_queued_runs(monkeypatch):
    """#701: each sweep must call reconcile_queued_runs next to
    reconcile_stale_runs, not only the latter."""
    queued_calls = []

    monkeypatch.setattr("app.services.run_service.reconcile_stale_runs", lambda db: 0)
    monkeypatch.setattr(
        "app.services.run_service.reconcile_queued_runs",
        lambda db: queued_calls.append(1) or 0,
    )

    async def run_briefly():
        task = asyncio.create_task(main_mod._stale_run_reaper_loop(0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_briefly())

    assert len(queued_calls) >= 1


# ---------------------------------------------------------------------------
# B2: app.main._reconcile_queued_runs_at_startup -- the startup lifespan's
# own call to reconcile_queued_runs, guarded against a Valkey outage the same
# corrective way the periodic sweep above survives one.
# ---------------------------------------------------------------------------

class TestReconcileQueuedRunsAtStartup:
    def test_success_returns_the_reconciler_count(self, db):
        assert main_mod._reconcile_queued_runs_at_startup(db, lambda db: 3) == 3

    def test_redis_error_is_logged_and_swallowed_not_raised(self, db, caplog):
        def boom(db):
            raise redis.exceptions.ConnectionError("valkey unreachable")

        with caplog.at_level("ERROR"):
            result = main_mod._reconcile_queued_runs_at_startup(db, boom)

        assert result == 0
        assert "Valkey unavailable" in caplog.text

    def test_a_non_redis_error_still_propagates(self, db):
        """Only Valkey unavailability is survived here -- a genuine DB error
        (or anything else unexpected) must still fail startup loudly, exactly
        like reconcile_stale_runs beside it."""
        def boom(db):
            raise RuntimeError("MySQL server has gone away")

        with pytest.raises(RuntimeError, match="gone away"):
            main_mod._reconcile_queued_runs_at_startup(db, boom)


class TestLifespanStartupSweepCallSite:
    """B2 call-site regression: the tests above pin
    ``_reconcile_queued_runs_at_startup`` in isolation, but nothing yet
    exercises whether ``app.main.lifespan`` actually calls it -- a mutant
    reverting the lifespan's startup sweep to the unguarded
    ``reconcile_queued_runs(db)`` passes every one of them (and the whole
    suite) because the call site itself is untested. This drives the real
    lifespan end-to-end with ``reconcile_queued_runs`` raising a Valkey
    ``ConnectionError`` and asserts startup still completes -- it would raise
    out of the mutant's unguarded call instead."""

    def test_startup_sweep_survives_reconcile_queued_runs_redis_error(self, monkeypatch):
        from app.pipeline.event_emitter import event_emitter
        from tests.conftest import TestingSessionLocal

        # Real init_db()/DB work is exercised elsewhere; here only the guard
        # call site matters, so point the lifespan's own SessionLocal at the
        # already-created in-memory test DB and skip the real init_db() (it
        # would otherwise try to create tables against the real configured
        # engine).
        monkeypatch.setattr(main_mod, "init_db", lambda: None)
        monkeypatch.setattr("app.database.SessionLocal", TestingSessionLocal)

        calls = []

        def boom(db):
            calls.append(1)
            raise redis.exceptions.ConnectionError("valkey unreachable")

        monkeypatch.setattr(run_service, "reconcile_queued_runs", boom)
        monkeypatch.setattr(run_service, "reconcile_stale_runs", lambda db: 0)

        # Other startup dependencies stubbed so this test isolates the B2
        # call site rather than the broker/pub-sub wiring (which is not
        # guarded and would otherwise depend on the environment's
        # CVICHE_REDIS_URL -- see the corrected _reconcile_queued_runs_at_startup
        # docstring: the pod does NOT survive a Valkey outage overall, only
        # this one sweep does).
        monkeypatch.delenv("CVICHE_REDIS_URL", raising=False)

        async def noop():
            return None

        monkeypatch.setattr(event_emitter, "startup", noop)
        monkeypatch.setattr(event_emitter, "shutdown", noop)

        fake_app = types.SimpleNamespace(state=types.SimpleNamespace())

        async def drive():
            async with main_mod.lifespan(fake_app):
                pass

        asyncio.run(drive())  # must not raise -- the guard must have caught it

        assert calls, "reconcile_queued_runs was never reached by the lifespan"


# ---------------------------------------------------------------------------
# #701: run_service.reconcile_queued_runs + the queue-mode stale-run clamp
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_redis(monkeypatch):
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer(), decode_responses=True)
    monkeypatch.setattr(run_queue, "_client", lambda: r)
    monkeypatch.setattr(run_queue, "_producer_client", lambda: r)
    monkeypatch.setattr(run_queue, "_autoclaim_cursors", {})
    return r


@pytest.fixture
def queue_mode(monkeypatch):
    monkeypatch.setenv("CVICHE_DISPATCH_MODE", "queue")


def _stream_run_ids(r):
    return [fields["run_id"] for _, fields in r.xrange(run_queue.STREAM)]


def _seed_batch(db, batch_id, files_submitted):
    user = User(email=f"{batch_id.lower()}@example.com", display_name="Pat Example", consent_version="1.0")
    db.add(user)
    db.commit()
    db.add(RunBatch(id=batch_id, user_id=user.id, files_submitted=files_submitted))
    db.commit()


def _seed_queued_run(db, run_id, *, queued_at, batch_id=None, batch_files_submitted=2):
    if batch_id is not None and db.get(RunBatch, batch_id) is None:
        _seed_batch(db, batch_id, batch_files_submitted)
    run = Run(id=run_id, filename="cv.docx", file_type="docx",
              status=RunState.QUEUED, queued_at=queued_at, batch_id=batch_id)
    db.add(run)
    db.commit()
    return run


def _batch_stream_run_ids(r):
    return [fields["run_id"] for _, fields in r.xrange(run_queue.BATCH_STREAM)]


class TestReconcileQueuedRuns:
    """run_service.reconcile_queued_runs (#701 A1): requeues a "queued" row
    whose Valkey token was lost, using run_queue.live_run_ids() to skip a row
    that still has one and run_queue.claim_reenqueue_slot() to guard against
    adding more than one fresh token per run per sweep window."""

    def test_noop_outside_queue_mode(self, db, fake_redis, monkeypatch):
        monkeypatch.delenv("CVICHE_DISPATCH_MODE", raising=False)
        _seed_queued_run(db, "NQ001", queued_at=datetime.now() - timedelta(minutes=30))
        assert run_service.reconcile_queued_runs(db) == 0
        assert _stream_run_ids(fake_redis) == []

    def test_requeues_a_stranded_row_with_no_token(self, db, fake_redis, queue_mode):
        _seed_queued_run(db, "SQ001", queued_at=datetime.now() - timedelta(minutes=10))
        assert run_service.reconcile_queued_runs(db) == 1
        assert _stream_run_ids(fake_redis) == ["SQ001"]

    def test_leaves_a_recently_queued_row_alone(self, db, fake_redis, queue_mode):
        _seed_queued_run(db, "SQ002", queued_at=datetime.now() - timedelta(minutes=1))
        assert run_service.reconcile_queued_runs(db) == 0
        assert _stream_run_ids(fake_redis) == []

    def test_leaves_a_row_alone_when_its_token_is_still_undelivered(self, db, fake_redis, queue_mode):
        run_queue.ensure_group()
        _seed_queued_run(db, "SQ003", queued_at=datetime.now() - timedelta(minutes=10))
        run_queue.enqueue("SQ003")  # never delivered -- still live
        assert run_service.reconcile_queued_runs(db) == 0
        assert _stream_run_ids(fake_redis) == ["SQ003"]  # unchanged: no 2nd token

    def test_leaves_a_row_alone_when_its_token_is_pending(self, db, fake_redis, queue_mode):
        run_queue.ensure_group()
        _seed_queued_run(db, "SQ004", queued_at=datetime.now() - timedelta(minutes=10))
        run_queue.enqueue("SQ004")
        run_queue.read_one("worker-a")  # delivered, not yet ACKed -> pending
        assert run_service.reconcile_queued_runs(db) == 0
        assert _stream_run_ids(fake_redis) == ["SQ004"]

    def test_an_acked_and_deleted_token_does_not_count_as_live(self, db, fake_redis, queue_mode):
        """ack()'s XACK+XDEL clears an entry from the stream and the PEL, so
        live_run_ids() must not still count that run as live -- otherwise a
        row genuinely stranded after its one token was fully cleared would
        never be recovered."""
        run_queue.ensure_group()
        _seed_queued_run(db, "SQ005", queued_at=datetime.now() - timedelta(minutes=10))
        entry_id = run_queue.enqueue("SQ005")
        run_queue.read_one("worker-a")
        run_queue.ack(entry_id)
        assert _stream_run_ids(fake_redis) == []

        assert run_service.reconcile_queued_runs(db) == 1
        assert _stream_run_ids(fake_redis) == ["SQ005"]

    def test_the_reenqueue_guard_stops_a_second_sweep_from_double_requeuing(self, db, fake_redis, queue_mode):
        """Isolates claim_reenqueue_slot's own contribution: with no consumer
        group yet (so live_run_ids() alone reports nothing live either way,
        exactly the state right after the very first-ever enqueue), only the
        guard stops a second sweep in the same window from adding a duplicate
        token for the same run (#701 runs.py#5's re-enqueue-storm guard,
        reused here for the reconciler)."""
        _seed_queued_run(db, "SQ006", queued_at=datetime.now() - timedelta(minutes=10))
        assert run_service.reconcile_queued_runs(db) == 1
        assert run_service.reconcile_queued_runs(db) == 0
        assert _stream_run_ids(fake_redis) == ["SQ006"]

    def test_requeues_a_stranded_batch_run_onto_the_batch_queue(self, db, fake_redis, queue_mode):
        """#1114: the reconciler routes through run_queue.queue_for like every
        other producer -- a stranded batch run goes back on the batch stream,
        never onto the single-run stream where general workers would take it."""
        _seed_queued_run(db, "SQB001", queued_at=datetime.now() - timedelta(minutes=10), batch_id="BATCHA")
        assert run_service.reconcile_queued_runs(db) == 1
        assert _batch_stream_run_ids(fake_redis) == ["SQB001"]
        assert _stream_run_ids(fake_redis) == []

    def test_requeues_a_stranded_one_file_batch_run_onto_the_single_queue(self, db, fake_redis, queue_mode):
        """#1340: a one-file batch (a single upload that asked for the
        completion email) routes like a single run on every producer,
        including this one."""
        _seed_queued_run(db, "SQB003", queued_at=datetime.now() - timedelta(minutes=10), batch_id="ONEFIL",
                         batch_files_submitted=1)
        assert run_service.reconcile_queued_runs(db) == 1
        assert _stream_run_ids(fake_redis) == ["SQB003"]
        assert _batch_stream_run_ids(fake_redis) == []

    def test_leaves_a_batch_run_alone_while_its_batch_token_is_live(self, db, fake_redis, queue_mode):
        """#1114: live_run_ids() must see the batch stream too, or every queued
        batch run older than the threshold would get a duplicate token."""
        run_queue.ensure_group(run_queue.BATCH)
        _seed_queued_run(db, "SQB002", queued_at=datetime.now() - timedelta(minutes=10), batch_id="BATCHA")
        run_queue.enqueue("SQB002", run_queue.BATCH)
        assert run_service.reconcile_queued_runs(db) == 0
        assert _batch_stream_run_ids(fake_redis) == ["SQB002"]


class TestEffectiveStaleRunMinutes:
    """The stale-"running"-reaper clamp: in queue mode a run's token stays
    un-ACKed for the whole run, so the threshold must never sit below the
    worker's own RUN_TIMEOUT_S watchdog."""

    def test_uses_the_configured_value_outside_queue_mode(self, monkeypatch):
        monkeypatch.delenv("CVICHE_DISPATCH_MODE", raising=False)
        monkeypatch.setenv("CVICHE_STALE_RUN_MINUTES", "10")
        monkeypatch.setenv("CVICHE_RUN_TIMEOUT_SECONDS", "5400")
        assert run_service._effective_stale_run_minutes() == 10

    def test_floors_at_the_run_watchdog_timeout_plus_margin_in_queue_mode(self, queue_mode, monkeypatch):
        monkeypatch.setenv("CVICHE_STALE_RUN_MINUTES", "10")
        monkeypatch.setenv("CVICHE_RUN_TIMEOUT_SECONDS", "5400")  # 90 min
        expected = 90 + run_service.QUEUE_MODE_STALE_RUN_MARGIN_MINUTES
        assert run_service._effective_stale_run_minutes() == expected

    def test_configured_value_wins_once_it_already_clears_the_floor(self, queue_mode, monkeypatch):
        monkeypatch.setenv("CVICHE_STALE_RUN_MINUTES", "200")
        monkeypatch.setenv("CVICHE_RUN_TIMEOUT_SECONDS", "5400")
        assert run_service._effective_stale_run_minutes() == 200

    def test_reconcile_stale_runs_leaves_a_running_row_alone_below_the_floor(self, db, queue_mode, monkeypatch):
        """End-to-end: a row that the OLD unclamped threshold would already
        fail (20 min > CVICHE_STALE_RUN_MINUTES=10) is left running because
        queue mode floors the threshold near the 90-minute watchdog."""
        monkeypatch.setenv("CVICHE_STALE_RUN_MINUTES", "10")
        monkeypatch.setenv("CVICHE_RUN_TIMEOUT_SECONDS", "5400")
        run = Run(id="RQF001", filename="cv.docx", file_type="docx", status=RunState.RUNNING,
                  started_at=datetime.now() - timedelta(minutes=20))
        db.add(run)
        db.commit()

        assert run_service.reconcile_stale_runs(db) == 0

        db.expire_all()
        assert db.query(Run).filter(Run.id == "RQF001").one().status == RunState.RUNNING


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

    from app.pipeline import redis_broker
    from app.pipeline.event_emitter import event_emitter
    from tests.conftest import TestingSessionLocal, engine

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
    executor = orch.PipelineOrchestrator("FLIP01", tmp_path / "FLIP01.docx", db)
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
