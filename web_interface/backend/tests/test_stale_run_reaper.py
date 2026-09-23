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
from datetime import datetime, timedelta

import fakeredis
import pytest

import app.main as main_mod
from app.models import Run, RunState
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
# #701: run_service.reconcile_queued_runs + the queue-mode stale-run clamp
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_redis(monkeypatch):
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer(), decode_responses=True)
    monkeypatch.setattr(run_queue, "_client", lambda: r)
    monkeypatch.setattr(run_queue, "_producer_client", lambda: r)
    monkeypatch.setattr(run_queue, "_autoclaim_cursor", "0-0")
    return r


@pytest.fixture
def queue_mode(monkeypatch):
    monkeypatch.setenv("CVICHE_DISPATCH_MODE", "queue")


def _stream_run_ids(r):
    return [fields["run_id"] for _, fields in r.xrange(run_queue.STREAM)]


def _seed_queued_run(db, run_id, *, queued_at):
    run = Run(id=run_id, filename="cv.docx", file_type="docx",
              status=RunState.QUEUED, queued_at=queued_at)
    db.add(run)
    db.commit()
    return run


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
