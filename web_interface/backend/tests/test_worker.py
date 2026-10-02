"""Worker (#701): the conditional DB claim, ack semantics, reclaim windows,
the poison cap and graceful shutdown -- against fakeredis and the shared
in-memory SQLite fixture, with the orchestrator stubbed.

SQLite serializes writers, so the claim-atomicity test proves the rowcount
LOGIC (exactly one winner of ``WHERE status='queued'``), not InnoDB row
locking; an integration test on real MySQL is design §17 layer 2 and out of
scope here. That specific race test lives in test_service_layer.py now (it
exercises run_service.claim_queued directly, the function that replaced
worker._claim -- see the module docstring there).
"""
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import ClassVar

import fakeredis
import pytest
import yaml

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

from app import worker  # noqa: E402
from app.pipeline import run_queue  # noqa: E402
from app.services import run_service  # noqa: E402
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
    monkeypatch.setattr(run_queue, "_producer_client", lambda: r)
    monkeypatch.setattr(run_queue, "_autoclaim_cursors", {})
    monkeypatch.setattr(worker, "SessionLocal", TestingSessionLocal)
    # claim_queued/mark_failed (run_service) open their own short session too
    # -- point it at the same shared test DB the `db` fixture uses.
    monkeypatch.setattr(run_service, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(worker, "PipelineOrchestrator", StubOrchestrator)
    monkeypatch.setattr(worker, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(worker, "_materialize_input_if_missing", lambda *a: None)
    monkeypatch.setattr(worker, "CONSUMER", "w1")
    # Never touch the real /tmp from a test.
    monkeypatch.setattr(worker, "READY_FILE", tmp_path / "ready")
    monkeypatch.setattr(worker, "HEARTBEAT_FILE", tmp_path / "heartbeat")
    StubOrchestrator.calls = []
    StubOrchestrator.on_execute = staticmethod(lambda db, run_id: None)
    worker.shutting_down.clear()
    run_queue.ensure_group()
    yield r
    worker.shutting_down.clear()


def _seed(db, status="queued", run_id="WRK001", resume_from_step=None):
    from app.models import Run
    db.add(Run(id=run_id, filename="cv.docx", file_type="docx", status=status, started_at=OLD,
               resume_from_step=resume_from_step))
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
    run_queue.enqueue("WRK001")

    worker.handle(*run_queue.read_one("w1"))

    row = _row(db)
    assert row.status == "complete"
    assert row.started_at > OLD, "claim must reset started_at so the reaper's clock is truthful"
    assert StubOrchestrator.calls == [("WRK001", None)]
    assert _pending() == 0


def test_claim_queued_clears_the_previous_attempts_error_and_completed_at(db, wired, tmp_path):
    """The claim itself is run_service.claim_queued now (worker._claim was
    removed -- section 1.5, #701 worker.py point 5); its own atomicity and
    field-clearing tests live in test_service_layer.py. This just proves the
    worker's handle() actually goes through it end to end."""
    from app.models import Run
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    run = db.query(Run).filter(Run.id == "WRK001").one()
    run.error_message, run.completed_at = "stage 3 exploded", OLD
    db.commit()
    StubOrchestrator.on_execute = staticmethod(_set_status("complete"))
    run_queue.enqueue("WRK001")

    worker.handle(*run_queue.read_one("w1"))

    row = _row(db)
    assert row.status == "complete"
    assert row.error_message is None
    assert row.completed_at is None, "the stub's terminal commit never sets it; the claim cleared the old one"


def test_db_error_at_claim_leaves_the_entry_pending_and_the_run_queued(db, wired, tmp_path, monkeypatch):
    """No claim result means no ACK: the entry must stay pending so XAUTOCLAIM
    redelivers it, rather than stranding a `queued` row that nothing reaps."""
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")

    def db_down():
        raise RuntimeError("MySQL server has gone away")
    monkeypatch.setattr(run_service, "SessionLocal", db_down)
    run_queue.enqueue("WRK001")

    with pytest.raises(RuntimeError, match="gone away"):
        worker.handle(*run_queue.read_one("w1"))

    assert _pending() == 1
    assert (_row(db).status, _row(db).started_at) == ("queued", OLD)
    assert StubOrchestrator.calls == []


def test_missing_run_id_is_acked_without_a_claim(db, wired, caplog):
    wired.xadd(run_queue.STREAM, {"start_step": "2"})  # no run_id field at all
    with caplog.at_level("WARNING"):
        worker.handle(*run_queue.read_one("w1"))
    assert StubOrchestrator.calls == []
    assert _pending() == 0
    assert "skipped_bad_token" in caplog.text, "must be rejected as BadToken, not fall through to a failed claim"


def test_run_id_with_path_traversal_is_acked_without_a_claim(db, wired, caplog):
    wired.xadd(run_queue.STREAM, {"run_id": "../../etc/passwd"})
    with caplog.at_level("WARNING"):
        worker.handle(*run_queue.read_one("w1"))
    assert StubOrchestrator.calls == []
    assert _pending() == 0
    assert "skipped_bad_token" in caplog.text, "must be rejected as BadToken, not fall through to a failed claim"


def test_run_id_with_an_embedded_newline_is_acked_and_logged_escaped(db, wired, caplog):
    """Log-injection defense: a forged run_id containing a newline must stay
    on one log line -- BadToken fires before any DB call, and the bad-token
    log line uses %r (which escapes the newline) rather than %s."""
    wired.xadd(run_queue.STREAM, {"run_id": "WRK001\nFAKE forged line"})

    with caplog.at_level("WARNING"):
        worker.handle(*run_queue.read_one("w1"))

    assert StubOrchestrator.calls == []
    assert _pending() == 0
    bad_token_lines = [ln for ln in caplog.text.splitlines() if "skipped_bad_token" in ln]
    assert len(bad_token_lines) == 1
    assert "FAKE forged line" in bad_token_lines[0], "escaped by %r, the value must stay on the same line"


def test_resume_from_step_on_the_row_reaches_execute(db, wired, tmp_path):
    """The token is a pure wake-up: the step to resume from comes off the DB
    row (set by the flip that queued the run), not off the token."""
    _seed(db, resume_from_step=4)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    run_queue.enqueue("WRK001")

    worker.handle(*run_queue.read_one("w1"))

    assert StubOrchestrator.calls == [("WRK001", 4)]


def test_a_legacy_tokens_start_step_field_is_ignored_in_favour_of_the_db_row(db, wired, tmp_path):
    """A not-yet-redeployed producer may still enqueue an old-format token
    carrying start_step (N5: enqueue() itself no longer accepts the
    parameter, so a raw XADD stands in for that legacy producer here); it
    must not override the DB's resume_from_step."""
    _seed(db, resume_from_step=4)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    wired.xadd(run_queue.STREAM, {"run_id": "WRK001", "start_step": "99"})  # old-format field, must be ignored

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


def test_a_failed_terminal_commit_is_marked_failed_before_the_ack(db, wired, tmp_path):
    """Design case G: if the orchestrator's own terminal commit itself raises
    (leaving the row `running`, unlike the sibling test above where the stub
    commits `failed` before raising), the worker must not ACK a row still
    `running` -- it fails the row itself first, right before the ACK."""
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")

    def terminal_commit_fails(session, run_id):
        raise RuntimeError("terminal commit failed")
    StubOrchestrator.on_execute = staticmethod(terminal_commit_fails)
    run_queue.enqueue("WRK001")

    worker.handle(*run_queue.read_one("w1"))

    row = _row(db)
    assert row.status == "failed"
    assert "terminal status" in row.error_message.lower()
    assert _pending() == 0


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
    StubOrchestrator.on_execute = staticmethod(_set_status("complete"))
    run_queue.enqueue("WRK001")
    assert run_queue.read_one("A") is not None  # A dies here: no claim, no ack

    entry = run_queue.autoclaim_one("B")
    assert entry is not None
    worker.handle(*entry, reclaimed=True)

    assert _row(db).status == "complete"
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


def test_own_pel_entries_are_reclaimed_immediately_without_waiting_min_idle(db, wired, tmp_path):
    """A3: a same-pod restart (same CONSUMER/HOSTNAME) must not wait out
    MIN_IDLE_MS -- production default, left unmonkeypatched here on purpose,
    unlike the reclaim-window tests above which drive autoclaim_one directly."""
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    StubOrchestrator.on_execute = staticmethod(_set_status("complete"))
    run_queue.enqueue("WRK001")
    run_queue.read_one("w1")  # delivered to this consumer, never claimed/ACKed (a crash)

    worker._reclaim_own_pending()

    assert StubOrchestrator.calls == [("WRK001", None)]
    assert _pending() == 0
    assert _row(db).status == "complete"


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


def test_poison_cap_on_a_running_row_fails_it_and_dead_letters(db, wired, tmp_path, monkeypatch, caplog):
    """A2: a row that is `running` when its entry reaches the delivery cap
    (the row already won a claim, then 3+ post-claim ACK attempts failed)
    must be failed too, not only a `queued` one -- and its still-running
    Steps must be errored, mirroring run_service._mark_run_failed."""
    from app.models import Step
    _seed(db, status="running")
    db.add(Step(run_id="WRK001", step_number=1, step_name="s1", status="running"))
    db.commit()
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)
    eid = run_queue.enqueue("WRK001")
    run_queue.read_one("A")
    for _ in range(run_queue.MAX_DELIVERIES):
        wired.xclaim(run_queue.STREAM, run_queue.GROUP, "A", 0, [eid])

    with caplog.at_level("ERROR"):
        worker.handle(*run_queue.autoclaim_one("B"), reclaimed=True)

    row = _row(db)
    assert row.status == "failed" and "Dead-lettered" in row.error_message
    step = db.query(Step).filter(Step.run_id == "WRK001").one()
    assert step.status == "error"
    assert wired.xlen(run_queue.DEAD_STREAM) == 1
    dead_letter_lines = [ln for ln in caplog.text.splitlines() if "row_failed=" in ln]
    assert len(dead_letter_lines) == 1
    assert "prior_status=running" in dead_letter_lines[0]
    assert "row_failed=True" in dead_letter_lines[0]


def test_poison_cap_on_an_already_terminal_row_leaves_it_alone_and_logs_row_failed_false(
    db, wired, tmp_path, monkeypatch, caplog,
):
    """A run that finished on its own (complete) between the last failed
    reclaim attempt and this one must not be re-failed; the log line still
    fires, with row_failed=False, so on-call can tell the two cases apart."""
    _seed(db, status="complete")
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)
    eid = run_queue.enqueue("WRK001")
    run_queue.read_one("A")
    for _ in range(run_queue.MAX_DELIVERIES):
        wired.xclaim(run_queue.STREAM, run_queue.GROUP, "A", 0, [eid])

    with caplog.at_level("ERROR"):
        worker.handle(*run_queue.autoclaim_one("B"), reclaimed=True)

    assert _row(db).status == "complete"
    dead_letter_lines = [ln for ln in caplog.text.splitlines() if "row_failed=" in ln]
    assert "row_failed=False" in dead_letter_lines[0]


def test_loop_reclaims_with_the_delivery_count_so_the_poison_cap_fires(db, wired, tmp_path, monkeypatch):
    """Drives the cap through loop() rather than handle(): the autoclaim branch
    must pass reclaimed=True, or the cap (and the `reclaimed` log line) is
    silently disabled in production while every direct handle() test stays green."""
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)
    monkeypatch.setattr(run_queue, "BLOCK_MS", 50)
    eid = run_queue.enqueue("WRK001")
    run_queue.read_one("A")
    for _ in range(run_queue.MAX_DELIVERIES):
        wired.xclaim(run_queue.STREAM, run_queue.GROUP, "A", 0, [eid])

    t = threading.Thread(target=worker.loop)
    t.start()
    deadline = time.time() + 2
    while wired.xlen(run_queue.DEAD_STREAM) == 0 and time.time() < deadline:
        time.sleep(0.02)
    worker.shutting_down.set()
    t.join(timeout=2)

    assert not t.is_alive()
    assert [d[1]["run_id"] for d in wired.xrange(run_queue.DEAD_STREAM)] == ["WRK001"]
    assert _row(db).status == "failed"
    assert StubOrchestrator.calls == []


def test_loop_survives_a_transient_error_and_leaves_the_entry_pending(db, wired, tmp_path, monkeypatch):
    """A DB blip inside one iteration must not end the loop (the pod would
    CrashLoopBackOff, one entry per restart); the entry stays un-ACKed."""
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    monkeypatch.setattr(run_queue, "BLOCK_MS", 50)
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)  # so the redelivery is observable now
    monkeypatch.setattr(worker, "RETRY_DELAY_S", 0.05)
    run_queue.enqueue("WRK001")
    attempts = []

    def flaky():
        attempts.append(1)
        raise RuntimeError("MySQL server has gone away")
    monkeypatch.setattr(run_service, "SessionLocal", flaky)

    t = threading.Thread(target=worker.loop)
    t.start()
    try:
        time.sleep(0.3)
        assert t.is_alive(), "the loop died on the first error instead of retrying"
        assert len(attempts) >= 2, "the entry was never redelivered after the error"
    finally:
        worker.shutting_down.set()
        t.join(timeout=2)

    assert not t.is_alive()
    # Never ACKed away: the run's token is still outstanding. Not
    # `_pending() == 1`: when shutting_down lands between a read and the claim
    # decision, loop() hands the entry back as a fresh undelivered one
    # (requeue), which reads as 0 pending -- a timing flake under load.
    assert run_queue.live_run_ids() == {"WRK001"}
    assert _row(db).status == "queued"


def test_loop_reclaims_own_pending_at_startup_and_after_each_error(wired, monkeypatch):
    """Wires _reclaim_own_pending into loop() at the right two points, without
    depending on Valkey PEL mechanics already proved separately in
    test_run_queue.py."""
    calls = []
    monkeypatch.setattr(worker, "_reclaim_own_pending", lambda queues: calls.append(1) or [])
    monkeypatch.setattr(run_queue, "BLOCK_MS", 50)
    monkeypatch.setattr(worker, "RETRY_DELAY_S", 0.05)

    def boom(*a, **kw):
        raise RuntimeError("valkey blip")
    monkeypatch.setattr(run_queue, "autoclaim_one", boom)

    t = threading.Thread(target=worker.loop)
    t.start()
    time.sleep(0.2)
    worker.shutting_down.set()
    t.join(timeout=2)

    assert not t.is_alive()
    assert len(calls) >= 2, "once at startup, again after at least one loop exception"


def test_reclaim_owed_flag_retries_every_iteration_until_the_db_recovers(db, wired, tmp_path, monkeypatch):
    """B1: a same-pod restart with the DB still down must not get only one
    immediate reclaim retry -- MIN_IDLE_MS is left at its production default
    (unmonkeypatched) on purpose, so this can only pass via the reclaim_owed
    flag retrying own-PEL reclaim every iteration, not via XAUTOCLAIM's own
    idle-time reclaim."""
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    StubOrchestrator.on_execute = staticmethod(_set_status("complete"))
    monkeypatch.setattr(run_queue, "BLOCK_MS", 50)
    monkeypatch.setattr(worker, "RETRY_DELAY_S", 0.02)
    run_queue.enqueue("WRK001")
    run_queue.read_one("w1")  # delivered to this consumer, never claimed/ACKed (a crash)

    attempts = {"n": 0}
    real_session_local = TestingSessionLocal

    def flaky_session_local():
        attempts["n"] += 1
        if attempts["n"] <= 2:
            raise RuntimeError("MySQL server has gone away")
        return real_session_local()
    monkeypatch.setattr(run_service, "SessionLocal", flaky_session_local)

    t = threading.Thread(target=worker.loop)
    t.start()
    try:
        deadline = time.time() + 2
        while _row(db).status != "complete" and time.time() < deadline:
            time.sleep(0.02)
    finally:
        worker.shutting_down.set()
        t.join(timeout=2)

    assert not t.is_alive()
    assert attempts["n"] >= 3, "the DB was retried past the first 2 failures"
    assert StubOrchestrator.calls == [("WRK001", None)]
    assert _row(db).status == "complete"
    assert _pending() == 0


def test_db_error_marking_a_poison_run_failed_keeps_its_entry_out_of_the_dlq(db, wired, tmp_path, monkeypatch):
    """The DB write precedes the DLQ move, so a failed write leaves the entry
    pending for the next reclaim instead of parking it with the row still `queued`."""
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)
    eid = run_queue.enqueue("WRK001")
    run_queue.read_one("A")
    for _ in range(run_queue.MAX_DELIVERIES):
        wired.xclaim(run_queue.STREAM, run_queue.GROUP, "A", 0, [eid])
    entry = run_queue.autoclaim_one("B")

    def db_down():
        raise RuntimeError("MySQL server has gone away")
    monkeypatch.setattr(run_service, "SessionLocal", db_down)

    with pytest.raises(RuntimeError, match="gone away"):
        worker.handle(*entry, reclaimed=True)

    assert wired.xlen(run_queue.DEAD_STREAM) == 0
    assert _pending() == 1
    assert _row(db).status == "queued"


def test_delivery_at_the_cap_still_executes(db, wired, tmp_path, monkeypatch):
    """The cap is strictly greater-than: the MAX_DELIVERIES-th delivery runs."""
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)
    StubOrchestrator.on_execute = staticmethod(_set_status("complete"))
    eid = run_queue.enqueue("WRK001")
    run_queue.read_one("A")
    wired.xclaim(run_queue.STREAM, run_queue.GROUP, "A", 0, [eid])  # delivery 2; autoclaim makes 3
    worker.handle(*run_queue.autoclaim_one("B"), reclaimed=True)
    assert StubOrchestrator.calls == [("WRK001", None)]
    assert wired.xlen(run_queue.DEAD_STREAM) == 0
    assert _row(db).status == "complete"


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


def test_shutdown_between_read_and_claim_requeues_the_entry_without_claiming(db, wired, tmp_path, monkeypatch):
    """A4: SIGTERM arriving between the blocking read and the claim decision
    must not start a new run during drain; the token goes back to the stream
    as a fresh entry instead of being claimed."""
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    monkeypatch.setattr(run_queue, "BLOCK_MS", 50)
    eid = run_queue.enqueue("WRK001")

    real_read_one = run_queue.read_one

    def read_then_shutdown(consumer, *args, **kwargs):
        result = real_read_one(consumer, *args, **kwargs)
        if result:
            worker.shutting_down.set()
        return result
    monkeypatch.setattr(run_queue, "read_one", read_then_shutdown)

    t = threading.Thread(target=worker.loop)
    t.start()
    t.join(timeout=2)

    assert not t.is_alive()
    assert StubOrchestrator.calls == []
    assert _row(db).status == "queued", "the row must never be claimed during drain"
    remaining = wired.xrange(run_queue.STREAM)
    assert len(remaining) == 1
    assert remaining[0][0] != eid, "requeue must produce a NEW entry id, not reuse the drained one"
    assert remaining[0][1]["run_id"] == "WRK001"


def test_watchdog_fires_marks_the_run_failed_acks_and_calls_os_exit(db, wired, tmp_path, monkeypatch):
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    monkeypatch.setattr(worker, "RUN_TIMEOUT_S", 0.05)
    exit_codes = []
    monkeypatch.setattr(worker.os, "_exit", lambda code: exit_codes.append(code))

    def hang(session, run_id):
        time.sleep(0.3)  # longer than RUN_TIMEOUT_S -- the watchdog fires first
    StubOrchestrator.on_execute = staticmethod(hang)
    run_queue.enqueue("WRK001")

    worker.handle(*run_queue.read_one("w1"))

    assert exit_codes == [1]
    row = _row(db)
    assert row.status == "failed"
    assert "exceeded" in row.error_message.lower()


def test_watchdog_still_exits_when_mark_failed_itself_raises(db, wired, monkeypatch):
    """N1: os._exit must sit in a finally -- a DB error while marking the
    timed-out run failed must not leave the process running forever with
    the wedged stage thread still inside it."""
    _seed(db)
    run_queue.enqueue("WRK001")
    entry_id, fields = run_queue.read_one("w1")

    def db_down(*a, **kw):
        raise RuntimeError("MySQL server has gone away")
    monkeypatch.setattr(worker, "mark_failed", db_down)
    exit_codes = []
    monkeypatch.setattr(worker.os, "_exit", lambda code: exit_codes.append(code))

    worker._watchdog_fire(entry_id, "WRK001")

    assert exit_codes == [1], "os._exit must still run even though mark_failed raised"


def test_watchdog_acks_before_exit_even_when_os_exit_itself_raises(db, wired, monkeypatch):
    """N1: os._exit is the LAST statement in the finally, so mark_failed and
    ack have already committed by the time it runs -- proven here by making
    the os._exit stub itself raise and checking the DB/queue state left
    behind is already correct, not by anything downstream catching and
    masking that exception."""
    _seed(db, status="running")
    run_queue.enqueue("WRK001")
    entry_id, fields = run_queue.read_one("w1")

    def exit_raises(code):
        raise SystemExit(code)
    monkeypatch.setattr(worker.os, "_exit", exit_raises)

    with pytest.raises(SystemExit):
        worker._watchdog_fire(entry_id, "WRK001")

    assert _row(db).status == "failed"
    assert _pending() == 0


def test_watchdog_is_cancelled_when_the_run_finishes_before_the_deadline(db, wired, tmp_path, monkeypatch):
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    monkeypatch.setattr(worker, "RUN_TIMEOUT_S", 5)  # generous; must never fire in this test
    exit_codes = []
    monkeypatch.setattr(worker.os, "_exit", lambda code: exit_codes.append(code))
    StubOrchestrator.on_execute = staticmethod(_set_status("complete"))
    run_queue.enqueue("WRK001")

    worker.handle(*run_queue.read_one("w1"))
    time.sleep(0.05)  # let a stray, uncancelled timer fire if cancellation failed

    assert exit_codes == []
    assert _row(db).status == "complete"


def test_heartbeat_file_is_touched_by_a_background_thread_while_a_run_is_in_flight(
    db, wired, tmp_path, monkeypatch,
):
    _seed(db)
    (tmp_path / "WRK001.docx").write_bytes(b"PK")
    heartbeat_path = tmp_path / "in_flight_heartbeat"
    monkeypatch.setattr(worker, "HEARTBEAT_FILE", heartbeat_path)
    monkeypatch.setattr(worker, "HEARTBEAT_INTERVAL_S", 0.02)

    def slow(session, run_id):
        time.sleep(0.1)
        _set_status("complete")(session, run_id)
    StubOrchestrator.on_execute = staticmethod(slow)
    run_queue.enqueue("WRK001")

    assert not heartbeat_path.exists()
    worker.handle(*run_queue.read_one("w1"))

    assert heartbeat_path.exists(), "the in-flight heartbeat thread must have touched it during the run"


def test_loop_touches_the_heartbeat_file_each_iteration(wired, monkeypatch, tmp_path):
    heartbeat_path = tmp_path / "loop_heartbeat"
    monkeypatch.setattr(worker, "HEARTBEAT_FILE", heartbeat_path)
    monkeypatch.setattr(run_queue, "BLOCK_MS", 20)

    t = threading.Thread(target=worker.loop)
    t.start()
    time.sleep(0.15)
    worker.shutting_down.set()
    t.join(timeout=2)

    assert not t.is_alive()
    assert heartbeat_path.exists()


@pytest.mark.parametrize("env", [
    {"CVICHE_REDIS_URL": "", "CVICHE_STORAGE_BACKEND": "s3"},
    {"CVICHE_REDIS_URL": "redis://x", "CVICHE_STORAGE_BACKEND": "local"},
])
def test_main_refuses_to_start_without_redis_and_s3(monkeypatch, env, tmp_path):
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CVICHE_WORKER_ALLOW_LOCAL_STORAGE", raising=False)
    monkeypatch.setattr(worker, "configure_logging", lambda: None)
    monkeypatch.setattr(run_queue, "ensure_group", lambda queue: pytest.fail("must not touch Valkey"))
    ready_file = tmp_path / "ready"
    monkeypatch.setattr(worker, "READY_FILE", ready_file)

    assert worker.main() == 2
    assert not ready_file.exists(), "the ready file must never appear on the refusal path"


def test_main_writes_the_ready_file_only_after_config_checks_and_ensure_group(monkeypatch, tmp_path):
    monkeypatch.setenv("CVICHE_REDIS_URL", "redis://x")
    monkeypatch.setenv("CVICHE_STORAGE_BACKEND", "s3")
    monkeypatch.setattr(worker, "configure_logging", lambda: None)
    ready_file = tmp_path / "ready"
    monkeypatch.setattr(worker, "READY_FILE", ready_file)
    monkeypatch.delenv("CVICHE_WORKER_STREAMS", raising=False)
    ensure_group_calls = []
    loop_queues = []
    monkeypatch.setattr(run_queue, "ensure_group", lambda queue: ensure_group_calls.append(queue))
    monkeypatch.setattr(worker, "loop", loop_queues.append)  # don't actually run the read loop

    assert worker.main() == 0

    assert ensure_group_calls == [run_queue.SINGLE], "unset CVICHE_WORKER_STREAMS = the single-run queue only"
    assert loop_queues == [(run_queue.SINGLE,)]
    assert ready_file.exists()



@pytest.mark.parametrize(("webhook", "warned"), [("", True), ("https://example.webhook.office.com/x", False)])
def test_main_warns_when_the_teams_webhook_is_missing(monkeypatch, tmp_path, caplog, webhook, warned):
    monkeypatch.setenv("CVICHE_REDIS_URL", "redis://x")
    monkeypatch.setenv("CVICHE_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", webhook)
    monkeypatch.setattr(worker, "configure_logging", lambda: None)
    monkeypatch.setattr(worker, "READY_FILE", tmp_path / "ready")
    monkeypatch.setattr(run_queue, "ensure_group", lambda queue: None)
    monkeypatch.setattr(worker, "loop", lambda queues: None)

    with caplog.at_level("WARNING"):
        assert worker.main() == 0

    assert ("will post no Teams run cards" in caplog.text) is warned


# --- two queues (#1114) -------------------------------------------------------

BOTH = (run_queue.SINGLE, run_queue.BATCH)


def _batch_pending():
    """Delivered-but-unACKed entries on the batch queue."""
    return run_queue._client().xpending(run_queue.BATCH_STREAM, run_queue.BATCH_GROUP)["pending"]


def _run_loop_until(predicate, queues, timeout=3.0):
    """Run worker.loop(queues) on a thread until predicate() holds, then stop it."""
    t = threading.Thread(target=worker.loop, args=(queues,))
    t.start()
    try:
        deadline = time.time() + timeout
        while not predicate() and time.time() < deadline:
            time.sleep(0.02)
    finally:
        worker.shutting_down.set()
        t.join(timeout=3)
    assert not t.is_alive()


def test_handle_on_the_batch_queue_acks_on_the_batch_stream(db, wired, tmp_path):
    run_queue.ensure_group(run_queue.BATCH)
    _seed(db, run_id="BAT001")
    (tmp_path / "BAT001.docx").write_bytes(b"PK")
    StubOrchestrator.on_execute = staticmethod(_set_status("complete"))
    run_queue.enqueue("BAT001", run_queue.BATCH)

    worker.handle(*run_queue.read_one("w1", run_queue.BATCH), queue=run_queue.BATCH)

    assert _row(db, "BAT001").status == "complete"
    assert _batch_pending() == 0
    assert wired.xlen(run_queue.BATCH_STREAM) == 0


def test_a_flex_worker_takes_a_waiting_single_run_before_a_batch_run(db, wired, tmp_path, monkeypatch):
    """The batch run was queued FIRST; a worker reading single,batch still
    runs the single run first, because it polls the queues in order."""
    monkeypatch.setattr(run_queue, "BLOCK_MS", 50)
    run_queue.ensure_group(run_queue.BATCH)
    for run_id in ("BAT001", "SNG001"):
        _seed(db, run_id=run_id)
        (tmp_path / f"{run_id}.docx").write_bytes(b"PK")
    StubOrchestrator.on_execute = staticmethod(_set_status("complete"))
    run_queue.enqueue("BAT001", run_queue.BATCH)
    run_queue.enqueue("SNG001", run_queue.SINGLE)

    _run_loop_until(lambda: len(StubOrchestrator.calls) == 2, BOTH)

    assert [run_id for run_id, _ in StubOrchestrator.calls] == ["SNG001", "BAT001"]
    assert _pending() == 0 and _batch_pending() == 0


def test_a_single_queue_worker_never_reads_the_batch_queue(db, wired, tmp_path, monkeypatch):
    """The general pool (CVICHE_WORKER_STREAMS unset) is what caps batch runs
    at the flex pool's size: it must leave a batch token alone entirely."""
    monkeypatch.setattr(run_queue, "BLOCK_MS", 50)
    run_queue.ensure_group(run_queue.BATCH)
    _seed(db, run_id="BAT001")
    (tmp_path / "BAT001.docx").write_bytes(b"PK")
    run_queue.enqueue("BAT001", run_queue.BATCH)

    _run_loop_until(lambda: False, worker.DEFAULT_QUEUES, timeout=0.3)

    assert StubOrchestrator.calls == []
    assert _row(db, "BAT001").status == "queued"
    assert _batch_pending() == 0
    assert run_queue.read_one("flex", run_queue.BATCH, block=False) is not None, "never delivered to anyone"


def test_read_next_polls_every_queue_then_blocks_only_on_the_first(wired, monkeypatch):
    calls = []

    def fake_read_one(consumer, queue, *, block=True):
        calls.append((queue.name, block))
        return None
    monkeypatch.setattr(run_queue, "read_one", fake_read_one)

    assert worker._read_next(BOTH) is None
    assert calls == [("single", False), ("batch", False), ("single", True)]


def test_read_next_with_one_queue_is_the_single_blocking_read_of_before(wired, monkeypatch):
    calls = []

    def fake_read_one(consumer, queue, *, block=True):
        calls.append((queue.name, block))
        return None
    monkeypatch.setattr(run_queue, "read_one", fake_read_one)

    assert worker._read_next(worker.DEFAULT_QUEUES) is None
    assert calls == [("single", True)]


def test_read_next_returns_the_batch_entry_tagged_with_its_queue(wired):
    run_queue.ensure_group(run_queue.BATCH)
    eid = run_queue.enqueue("BAT001", run_queue.BATCH)
    entry = worker._read_next(BOTH)
    assert (entry.queue, entry.entry_id, entry.fields["run_id"]) == (run_queue.BATCH, eid, "BAT001")


def test_loop_autoclaims_an_abandoned_batch_entry(db, wired, tmp_path, monkeypatch):
    """XAUTOCLAIM sweeps every queue the worker reads, not just the first."""
    monkeypatch.setattr(run_queue, "BLOCK_MS", 50)
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)
    run_queue.ensure_group(run_queue.BATCH)
    _seed(db, run_id="BAT001")
    (tmp_path / "BAT001.docx").write_bytes(b"PK")
    StubOrchestrator.on_execute = staticmethod(_set_status("complete"))
    run_queue.enqueue("BAT001", run_queue.BATCH)
    run_queue.read_one("dead-pod", run_queue.BATCH)  # delivered, then that pod died

    monkeypatch.setattr(run_queue, "read_one", lambda *a, **kw: None)  # only autoclaim can find it
    _run_loop_until(lambda: _row(db, "BAT001").status == "complete", BOTH)

    assert StubOrchestrator.calls == [("BAT001", None)]
    assert _batch_pending() == 0


def test_own_pel_reclaim_covers_the_batch_queue(db, wired, tmp_path):
    run_queue.ensure_group(run_queue.BATCH)
    _seed(db, run_id="BAT001")
    (tmp_path / "BAT001.docx").write_bytes(b"PK")
    StubOrchestrator.on_execute = staticmethod(_set_status("complete"))
    run_queue.enqueue("BAT001", run_queue.BATCH)
    run_queue.read_one("w1", run_queue.BATCH)  # this pod crashed mid-run

    assert worker._reclaim_own_pending(BOTH) == []

    assert StubOrchestrator.calls == [("BAT001", None)]
    assert _batch_pending() == 0


def test_loop_startup_reclaims_own_pending_on_every_queue(db, wired, tmp_path, monkeypatch):
    """A flex pod restarting with a batch entry still in its own PEL runs it
    at startup -- not only after MIN_IDLE_MS, through XAUTOCLAIM."""
    monkeypatch.setattr(run_queue, "BLOCK_MS", 50)
    run_queue.ensure_group(run_queue.BATCH)
    _seed(db, run_id="BAT001")
    (tmp_path / "BAT001.docx").write_bytes(b"PK")
    StubOrchestrator.on_execute = staticmethod(_set_status("complete"))
    run_queue.enqueue("BAT001", run_queue.BATCH)
    run_queue.read_one("w1", run_queue.BATCH)  # this pod crashed mid-run

    _run_loop_until(lambda: _row(db, "BAT001").status == "complete", BOTH, timeout=1.0)

    assert StubOrchestrator.calls == [("BAT001", None)]
    assert _batch_pending() == 0


def test_a_bad_token_on_the_batch_queue_is_acked_on_the_batch_stream(wired):
    run_queue.ensure_group(run_queue.BATCH)
    wired.xadd(run_queue.BATCH_STREAM, {"junk": "x"})

    worker.handle(*run_queue.read_one("w1", run_queue.BATCH), queue=run_queue.BATCH)

    assert _batch_pending() == 0
    assert wired.xlen(run_queue.BATCH_STREAM) == 0


def test_poison_cap_on_the_batch_queue_dead_letters_to_the_batch_dead_stream(db, wired, monkeypatch):
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)
    run_queue.ensure_group(run_queue.BATCH)
    _seed(db, run_id="BAT001")
    eid = run_queue.enqueue("BAT001", run_queue.BATCH)
    run_queue.read_one("A", run_queue.BATCH)
    for _ in range(run_queue.MAX_DELIVERIES):
        wired.xclaim(run_queue.BATCH_STREAM, run_queue.BATCH_GROUP, "A", 0, [eid])

    worker.handle(*run_queue.autoclaim_one("B", run_queue.BATCH), reclaimed=True, queue=run_queue.BATCH)

    assert [d[1]["run_id"] for d in wired.xrange(run_queue.BATCH_DEAD_STREAM)] == ["BAT001"]
    assert wired.xlen(run_queue.DEAD_STREAM) == 0
    assert _batch_pending() == 0
    assert _row(db, "BAT001").status == "failed"


def test_shutdown_requeues_a_batch_entry_onto_the_batch_stream(db, wired, monkeypatch):
    monkeypatch.setattr(run_queue, "BLOCK_MS", 50)
    run_queue.ensure_group(run_queue.BATCH)
    _seed(db, run_id="BAT001")
    eid = run_queue.enqueue("BAT001", run_queue.BATCH)
    real_read_one = run_queue.read_one

    def read_then_shutdown(consumer, *args, **kwargs):
        result = real_read_one(consumer, *args, **kwargs)
        if result:
            worker.shutting_down.set()
        return result
    monkeypatch.setattr(run_queue, "read_one", read_then_shutdown)

    t = threading.Thread(target=worker.loop, args=(BOTH,))
    t.start()
    t.join(timeout=2)

    assert not t.is_alive()
    remaining = wired.xrange(run_queue.BATCH_STREAM)
    assert [(e != eid, f["run_id"]) for e, f in remaining] == [(True, "BAT001")]
    assert wired.xlen(run_queue.STREAM) == 0
    assert _row(db, "BAT001").status == "queued"


def test_watchdog_on_a_batch_run_acks_the_batch_entry(db, wired, tmp_path, monkeypatch):
    run_queue.ensure_group(run_queue.BATCH)
    _seed(db, run_id="BAT001")
    (tmp_path / "BAT001.docx").write_bytes(b"PK")
    monkeypatch.setattr(worker, "RUN_TIMEOUT_S", 0.05)
    exit_codes = []
    monkeypatch.setattr(worker.os, "_exit", lambda code: exit_codes.append(code))
    acked = []
    real_ack = run_queue.ack
    monkeypatch.setattr(run_queue, "ack", lambda entry_id, queue=run_queue.SINGLE: (acked.append(queue), real_ack(entry_id, queue))[1])
    StubOrchestrator.on_execute = staticmethod(lambda session, run_id: time.sleep(0.3))
    run_queue.enqueue("BAT001", run_queue.BATCH)

    worker.handle(*run_queue.read_one("w1", run_queue.BATCH), queue=run_queue.BATCH)

    assert exit_codes == [1]
    assert acked and set(acked) == {run_queue.BATCH}, "the watchdog's ack and handle's own must both hit the batch stream"


def test_main_reads_cviche_worker_streams_and_ensures_every_group(monkeypatch, tmp_path):
    monkeypatch.setenv("CVICHE_REDIS_URL", "redis://x")
    monkeypatch.setenv("CVICHE_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("CVICHE_WORKER_STREAMS", "single,batch")
    monkeypatch.setattr(worker, "configure_logging", lambda: None)
    monkeypatch.setattr(worker, "READY_FILE", tmp_path / "ready")
    ensured, loop_queues = [], []
    monkeypatch.setattr(run_queue, "ensure_group", ensured.append)
    monkeypatch.setattr(worker, "loop", loop_queues.append)

    assert worker.main() == 0

    assert ensured == [run_queue.SINGLE, run_queue.BATCH]
    assert loop_queues == [BOTH]


def test_main_refuses_to_start_on_an_invalid_cviche_worker_streams(monkeypatch, tmp_path):
    monkeypatch.setenv("CVICHE_REDIS_URL", "redis://x")
    monkeypatch.setenv("CVICHE_STORAGE_BACKEND", "s3")
    monkeypatch.setenv("CVICHE_WORKER_STREAMS", "single,batchh")
    monkeypatch.setattr(worker, "configure_logging", lambda: None)
    ready_file = tmp_path / "ready"
    monkeypatch.setattr(worker, "READY_FILE", ready_file)
    monkeypatch.setattr(run_queue, "ensure_group", lambda queue: pytest.fail("must not touch Valkey"))

    assert worker.main() == 2
    assert not ready_file.exists()


def test_run_timeout_stays_below_the_pod_grace_period():
    """RUN_TIMEOUT_S plus the watchdog's own mark-failed/ACK/exit tail must
    fit inside terminationGracePeriodSeconds, or a run the watchdog is about
    to stop cleanly can still be SIGKILLed mid-write first."""
    manifest_path = Path(__file__).resolve().parent.parent.parent.parent / "k8s/base/worker/deployment.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    grace_period = manifest["spec"]["template"]["spec"]["terminationGracePeriodSeconds"]

    assert worker.RUN_TIMEOUT_S < grace_period


_K8S = Path(__file__).resolve().parent.parent.parent.parent / "k8s"


def test_flex_worker_manifest_differs_from_the_worker_only_where_intended():
    """#1114: cviche-worker-flex is a copy of the worker base. Its probes,
    grace period, rollout strategy and security context must never drift from
    the general pool's; only the name/label, CVICHE_WORKER_STREAMS and the
    base replica count (0, so an overlay that doesn't patch it runs none) may
    differ."""
    worker = yaml.safe_load((_K8S / "base/worker/deployment.yaml").read_text())
    flex = yaml.safe_load((_K8S / "base/worker/flex-deployment.yaml").read_text())

    flex_env = flex["spec"]["template"]["spec"]["containers"][0]["env"]
    assert {"name": "CVICHE_WORKER_STREAMS", "value": "single,batch"} in flex_env
    assert run_queue.parse_worker_streams("single,batch") == (run_queue.SINGLE, run_queue.BATCH)
    assert flex["spec"]["replicas"] == 0
    assert flex["metadata"]["name"] == "cviche-worker-flex"
    assert flex["spec"]["selector"]["matchLabels"] == {"app": "cviche-worker-flex"}
    assert flex["spec"]["template"]["metadata"]["labels"] == {"app": "cviche-worker-flex"}

    flex_env.remove({"name": "CVICHE_WORKER_STREAMS", "value": "single,batch"})
    flex["metadata"]["name"] = worker["metadata"]["name"]
    flex["spec"]["replicas"] = worker["spec"]["replicas"]
    flex["spec"]["selector"] = worker["spec"]["selector"]
    flex["spec"]["template"]["metadata"]["labels"] = worker["spec"]["template"]["metadata"]["labels"]
    assert flex == worker


def test_dev_worker_pools_split_six_workers_three_and_three_at_identical_sizing():
    """#1114: dev keeps 6 workers and 6 x 100m / 512Mi of requests in total,
    split 3 general + 3 flex, the flex patch mirroring the general one."""
    general = yaml.safe_load((_K8S / "overlays/dev/worker-patch.yaml").read_text())
    flex = yaml.safe_load((_K8S / "overlays/dev/worker-flex-patch.yaml").read_text())

    assert (general["spec"]["replicas"], flex["spec"]["replicas"]) == (3, 3)
    assert flex["metadata"]["name"] == "cviche-worker-flex"
    assert flex["spec"]["template"]["spec"]["volumes"] == general["spec"]["template"]["spec"]["volumes"]
    assert flex["spec"]["template"]["spec"]["nodeSelector"] == general["spec"]["template"]["spec"]["nodeSelector"]
    general_container = general["spec"]["template"]["spec"]["containers"][0]
    flex_container = flex["spec"]["template"]["spec"]["containers"][0]
    for key in ("name", "resources", "envFrom", "volumeMounts"):
        assert flex_container[key] == general_container[key], key
    assert general_container["resources"]["requests"] == {"cpu": "100m", "memory": "512Mi"}
    overlay = yaml.safe_load((_K8S / "overlays/dev/kustomization.yaml").read_text())
    assert {"path": "worker-flex-patch.yaml"} in overlay["patches"]


def test_email_intake_poller_is_off_unless_flagged(monkeypatch):
    """#1298: CVICHE_EMAIL_INTAKE gates the poller thread; default off."""
    monkeypatch.delenv("CVICHE_EMAIL_INTAKE", raising=False)
    assert worker._start_email_intake() is None


def test_email_intake_poller_starts_a_thread_when_flagged(monkeypatch):
    from app.services import inbound_service
    started = threading.Event()
    monkeypatch.setenv("CVICHE_EMAIL_INTAKE", "1")
    monkeypatch.setattr(inbound_service, "run_intake_loop", lambda factory, storage, stop: started.set())
    monkeypatch.setattr("app.storage.get_storage", lambda: object())
    thread = worker._start_email_intake()
    assert thread is not None
    thread.join(timeout=5)
    assert started.is_set()


def test_email_intake_does_not_pull_app_auth_or_app_api_into_the_worker():
    """#1298: the worker boundary (run_service.UPLOAD_DIR) -- with intake on, the
    worker imports neither app.auth (which needs CVICHE_SESSION_SECRET at import)
    nor anything under app.api. Fresh interpreter, no session secret."""
    import subprocess
    import sys
    code = (
        "import sys\n"
        "from app import worker\n"
        "from app.services import inbound_service\n"
        "bad = [m for m in sys.modules if m == 'app.auth' or m == 'app.api' or m.startswith('app.api.')]\n"
        "print(bad)\n"
        "sys.exit(1 if bad else 0)\n"
    )
    env = {k: v for k, v in os.environ.items() if k != "CVICHE_SESSION_SECRET"}
    env["CVICHE_EMAIL_INTAKE"] = "1"
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                            cwd=str(Path(__file__).resolve().parents[1]))
    assert result.returncode == 0, result.stdout + result.stderr
