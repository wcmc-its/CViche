"""Worker (#701): the conditional DB claim, ack semantics, reclaim windows,
the poison cap and graceful shutdown -- against fakeredis and the shared
in-memory SQLite fixture, with the orchestrator stubbed.

SQLite serializes writers, so the claim-atomicity test proves the rowcount
LOGIC (exactly one winner of ``WHERE status='queued'``), not InnoDB row
locking; an integration test on real MySQL is design §17 layer 2 and out of
scope here.
"""
import os
import threading
import time
from datetime import datetime
from typing import ClassVar

import fakeredis
import pytest

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

from app import worker  # noqa: E402
from app.pipeline import run_queue  # noqa: E402
from tests.conftest import TestingSessionLocal  # noqa: E402

OLD = datetime(2026, 1, 1, 0, 0, 0)


class StubOrchestrator:
    """Records constructions; ``on_execute`` runs inside execute() with the
    worker's session so a test can mimic the orchestrator's own terminal commit."""
    calls: ClassVar[list[tuple[str, int | None]]] = []
    on_execute = staticmethod(lambda db, run_id: None)

    def __init__(self, run_id, file_path, db):
        self.run_id, self.file_path, self.db = run_id, file_path, db

    async def execute(self, start_step_number=None):
        StubOrchestrator.calls.append((self.run_id, start_step_number))
        StubOrchestrator.on_execute(self.db, self.run_id)


@pytest.fixture
def wired(db, monkeypatch, tmp_path):
    """fakeredis + test DB + stub orchestrator + input file present."""
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer(), decode_responses=True)
    monkeypatch.setattr(run_queue, "_client", lambda: r)
    monkeypatch.setattr(worker, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(worker, "PipelineOrchestrator", StubOrchestrator)
    monkeypatch.setattr(worker, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(worker, "_materialize_input_if_missing", lambda *a: None)
    monkeypatch.setattr(worker, "CONSUMER", "w1")
    StubOrchestrator.calls = []
    StubOrchestrator.on_execute = staticmethod(lambda db, run_id: None)
    worker.shutting_down.clear()
    run_queue.ensure_group()
    yield r
    worker.shutting_down.clear()


def _seed(db, status="queued", run_id="WRK001"):
    from app.models import Run
    db.add(Run(id=run_id, filename="cv.docx", file_type="docx", status=status, started_at=OLD))
    db.commit()


def _row(db, run_id="WRK001"):
    from app.models import Run
    db.expire_all()
    return db.query(Run).filter(Run.id == run_id).one()


def _pending():
    return run_queue.stats()["pending"]


def _set_status(status, error=None):
    def _apply(session, run_id):
        from app.models import Run
        run = session.query(Run).filter(Run.id == run_id).one()
        run.status, run.error_message = status, error
        session.commit()
    return _apply


def test_success_claims_resets_started_at_executes_and_acks(db, wired, tmp_path):
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    StubOrchestrator.on_execute = staticmethod(_set_status("complete"))
    run_queue.enqueue("WRK001", start_step=None)

    worker.handle(*run_queue.read_one("w1"))

    row = _row(db)
    assert row.status == "complete"
    assert row.started_at > OLD, "claim must reset started_at so the reaper's clock is truthful"
    assert StubOrchestrator.calls == [("WRK001", None)]
    assert _pending() == 0


def test_start_step_from_the_token_reaches_execute(db, wired, tmp_path):
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    run_queue.enqueue("WRK001", start_step=4)
    worker.handle(*run_queue.read_one("w1"))
    assert StubOrchestrator.calls == [("WRK001", 4)]


def test_failed_execution_is_acked_not_dead_lettered_and_status_is_the_orchestrators(db, wired, tmp_path):
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")

    def fail(session, run_id):
        _set_status("failed", "stage 3 exploded")(session, run_id)
        raise RuntimeError("boom")
    StubOrchestrator.on_execute = staticmethod(fail)
    run_queue.enqueue("WRK001")

    worker.handle(*run_queue.read_one("w1"))

    row = _row(db)
    assert (row.status, row.error_message) == ("failed", "stage 3 exploded")
    assert _pending() == 0
    assert wired.xlen(run_queue.DEAD_STREAM) == 0


def test_missing_input_marks_failed_and_acks_without_executing(db, wired):
    _seed(db)
    run_queue.enqueue("WRK001")
    worker.handle(*run_queue.read_one("w1"))
    row = _row(db)
    assert row.status == "failed" and "no longer available" in row.error_message
    assert StubOrchestrator.calls == []
    assert _pending() == 0


@pytest.mark.parametrize("status", ["complete", "running", "cancelled", "failed", "created"])
def test_redelivery_for_a_non_queued_run_skips_and_acks(db, wired, tmp_path, status):
    """Windows F/H (and a cancelled-before-claim run): the claim fails, the
    orchestrator is never constructed, the row is untouched, the entry is ACKed."""
    _seed(db, status=status)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    run_queue.enqueue("WRK001")
    worker.handle(*run_queue.read_one("w1"))
    row = _row(db)
    assert (row.status, row.started_at) == (status, OLD)
    assert StubOrchestrator.calls == []
    assert _pending() == 0


def test_unknown_run_id_skips_and_acks(db, wired):
    run_queue.enqueue("NOPE")
    worker.handle(*run_queue.read_one("w1"))
    assert StubOrchestrator.calls == [] and _pending() == 0


def test_reclaim_window_c_second_worker_claims_and_executes(db, wired, tmp_path, monkeypatch):
    """Consumer A read the entry and died before the DB claim: B autoclaims it
    and runs it."""
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)
    run_queue.enqueue("WRK001")
    assert run_queue.read_one("A") is not None  # A dies here: no claim, no ack

    entry = run_queue.autoclaim_one("B")
    assert entry is not None
    worker.handle(*entry, reclaimed=True)

    assert _row(db).status == "running"  # stub leaves the claim's status
    assert StubOrchestrator.calls == [("WRK001", None)]
    assert _pending() == 0


def test_reclaim_window_e_db_already_running_skips_and_leaves_row_alone(db, wired, tmp_path, monkeypatch):
    _seed(db, status="running")
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)
    run_queue.enqueue("WRK001")
    run_queue.read_one("A")  # A claimed in the DB, then died mid-run

    worker.handle(*run_queue.autoclaim_one("B"), reclaimed=True)

    row = _row(db)
    assert (row.status, row.started_at) == ("running", OLD)
    assert StubOrchestrator.calls == []
    assert _pending() == 0


def test_claim_is_won_by_exactly_one_of_two_racing_workers(wired, monkeypatch, tmp_path):
    """Logic-layer proof only: SQLite serializes writers, so this shows exactly
    one of two concurrent conditional UPDATEs sees rowcount 1; InnoDB row
    locking is design §17 layer 2. A file-backed DB with per-thread connections
    (not the shared-connection StaticPool fixture) so the two threads hold
    separate transactions; the busy timeout makes the loser wait, not raise."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.database import Base
    from app.models import Run

    engine = create_engine(f"sqlite:///{tmp_path}/race.db", connect_args={"timeout": 5})
    Base.metadata.create_all(engine)
    RaceSession = sessionmaker(bind=engine)
    monkeypatch.setattr(worker, "SessionLocal", RaceSession)
    with RaceSession() as s:
        s.add(Run(id="WRK001", filename="cv.docx", file_type="docx", status="queued", started_at=OLD))
        s.commit()
    results, errors, start = [], [], threading.Barrier(2)

    def race():
        start.wait()
        try:
            results.append(worker._claim("WRK001")[0])
        except Exception as e:  # a thread crash must fail the test, not shrink the list
            errors.append(e)

    threads = [threading.Thread(target=race) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sorted(results) == [False, True]
    with RaceSession() as s:
        assert s.query(Run).filter(Run.id == "WRK001").one().status == "running"


def test_poison_cap_dead_letters_after_max_deliveries(db, wired, tmp_path, monkeypatch):
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)
    eid = run_queue.enqueue("WRK001")
    run_queue.read_one("A")
    for _ in range(run_queue.MAX_DELIVERIES):  # each XCLAIM is one more delivery
        wired.xclaim(run_queue.STREAM, run_queue.GROUP, "A", 0, [eid])
    assert run_queue.delivery_count(eid) > run_queue.MAX_DELIVERIES

    worker.handle(*run_queue.autoclaim_one("B"), reclaimed=True)

    dead = wired.xrange(run_queue.DEAD_STREAM)
    assert [d[1]["run_id"] for d in dead] == ["WRK001"]
    assert _pending() == 0
    row = _row(db)
    assert row.status == "failed" and "Dead-lettered" in row.error_message
    assert StubOrchestrator.calls == []


def test_delivery_at_the_cap_still_executes(db, wired, tmp_path, monkeypatch):
    """The cap is strictly greater-than: the MAX_DELIVERIES-th delivery runs."""
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)
    eid = run_queue.enqueue("WRK001")
    run_queue.read_one("A")
    wired.xclaim(run_queue.STREAM, run_queue.GROUP, "A", 0, [eid])  # delivery 2; autoclaim makes 3
    worker.handle(*run_queue.autoclaim_one("B"), reclaimed=True)
    assert StubOrchestrator.calls == [("WRK001", None)]
    assert wired.xlen(run_queue.DEAD_STREAM) == 0


def test_shutdown_flag_exits_an_idle_loop_within_the_block_timeout(wired, monkeypatch):
    monkeypatch.setattr(run_queue, "BLOCK_MS", 50)
    t = threading.Thread(target=worker.loop)
    t.start()
    time.sleep(0.1)
    assert t.is_alive()
    worker.shutting_down.set()
    t.join(timeout=2)
    assert not t.is_alive()


def test_shutdown_lets_the_in_flight_run_finish_and_ack(db, wired, tmp_path, monkeypatch):
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    monkeypatch.setattr(run_queue, "BLOCK_MS", 50)
    claimed = threading.Event()

    def slow(session, run_id):
        claimed.set()
        time.sleep(0.3)
        _set_status("complete")(session, run_id)
    StubOrchestrator.on_execute = staticmethod(slow)
    run_queue.enqueue("WRK001")

    t = threading.Thread(target=worker.loop)
    t.start()
    assert claimed.wait(timeout=2)
    worker.shutting_down.set()  # SIGTERM mid-run
    t.join(timeout=3)

    assert not t.is_alive()
    assert _row(db).status == "complete"
    assert _pending() == 0


@pytest.mark.parametrize("env", [
    {"CVICHE_REDIS_URL": "", "CVICHE_STORAGE_BACKEND": "s3"},
    {"CVICHE_REDIS_URL": "redis://x", "CVICHE_STORAGE_BACKEND": "local"},
])
def test_main_refuses_to_start_without_redis_and_s3(monkeypatch, env):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CVICHE_WORKER_ALLOW_LOCAL_STORAGE", raising=False)
    monkeypatch.setattr(worker, "configure_logging", lambda: None)
    monkeypatch.setattr(run_queue, "ensure_group", lambda: pytest.fail("must not touch Valkey"))
    assert worker.main() == 2
