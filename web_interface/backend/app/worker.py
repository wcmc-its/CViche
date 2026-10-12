"""Pipeline run worker: ``python -m app.worker`` (#701).

Consumes work tokens from the Valkey stream (``app.pipeline.run_queue``) and
executes one run at a time; global concurrency is the worker replica count.
Never starts uvicorn or imports the FastAPI app. Correctness lives in the DB
claim, not the queue: a delivered token may execute only if this process wins
the conditional ``queued -> running`` UPDATE (``run_service.claim_queued``),
so a redelivered token for a run that is already running, complete or
cancelled is skipped and ACKed. A token is a pure wake-up naming only
``run_id`` (``run_queue.WorkToken``) -- the step to resume from comes off the
claimed row's ``resume_from_step``, not off the token.

SIGTERM/SIGINT stop the read loop; a run already in flight finishes and ACKs
first. The loop re-checks the flag again right after a blocking read/claim
returns and before it acts on the entry: one read during the drain window is
handed back to the stream (``run_queue.requeue``) rather than started as a
new run. A kill mid-run (SIGKILL, an OOM, a node replacement) leaves the
entry un-ACKed and the row ``running``; at startup, and again after any loop
exception, this consumer reclaims its own pending entries immediately
(``run_queue.reclaim_own_pending``) rather than waiting out
``run_queue.MIN_IDLE_MS`` -- the same-pod-restart case. A run that outlives
``RUN_TIMEOUT_S`` is stopped by its own watchdog (see ``_watchdog_fire``)
rather than only by the pod's ``terminationGracePeriodSeconds``. v1 does not
auto-resume a run this worker itself walked away from mid-execution.

Queues (#1114): ``CVICHE_WORKER_STREAMS`` (``run_queue.worker_queues``) names
the queues this worker reads, in priority order -- unset, the single-run queue
only, exactly the pre-#1114 loop. A worker with several (the flex pool:
``single,batch``) reclaims and polls each in order without blocking and blocks
only on the first when every one is empty (``_read_next``), so a single run
always goes ahead of a batch run on a worker that frees up. Every entry is
handled, ACKed, requeued and dead-lettered on the queue it came from.
"""
import asyncio
import logging
import os
import signal
import socket
import threading
from pathlib import Path
from typing import NamedTuple

from sqlalchemy import select

from app.config_loader import email_intake_enabled, get_config
from app.database import SessionLocal
from app.logging_config import configure_logging
from app.models import Run, RunState
from app.pipeline import run_queue
from app.pipeline.orchestrator import PipelineOrchestrator
from app.pipeline.run_queue import Queue
from app.services.run_service import (
    UPLOAD_DIR,
    _materialize_input_if_missing,
    claim_queued,
    mark_failed,
)

logger = logging.getLogger(__name__)

CONSUMER = os.environ.get("HOSTNAME") or socket.gethostname()
RETRY_DELAY_S = 5
shutting_down = threading.Event()

# Below terminationGracePeriodSeconds (k8s/base/worker/deployment.yaml) with
# margin for the watchdog's own mark-failed/ACK/exit tail -- see
# test_run_timeout_stays_below_the_pod_grace_period. Default 5400s: prod's
# measured p99/max run is 3542s (Paul, 2026-09-23), so this is ~1.5x the
# longest run ever observed, comfortably above the per-stage 1800s ceiling
# (CVICHE_STAGE_TIMEOUT_SECONDS) a single stage could already reach.
#
# Sourced from run_queue.RUN_TIMEOUT_S (N2), not read independently here: that
# module also derives its own MIN_IDLE_MS reclaim threshold from this exact
# value (it must exceed it, or a healthy long run gets XAUTOCLAIMed out from
# under itself), and a second independent config read here could drift from
# that one on a config reload race.
RUN_TIMEOUT_S = run_queue.RUN_TIMEOUT_S

# How often the in-flight heartbeat thread (see _heartbeat_while_running)
# touches HEARTBEAT_FILE while a run is executing. Well under the liveness
# probe's failureThreshold x period (k8s/base/worker/deployment.yaml, ~60-90s).
HEARTBEAT_INTERVAL_S = 15
READY_FILE = Path("/tmp/worker-ready")
HEARTBEAT_FILE = Path("/tmp/worker-heartbeat")
# What loop() reads when main() is bypassed (tests): the single-run queue only,
# the same as an unset CVICHE_WORKER_STREAMS.
DEFAULT_QUEUES: tuple[Queue, ...] = (run_queue.SINGLE,)


def _log(event: str, run_id: str, entry_id: str, **kv: object) -> None:
    extra = " ".join(f"{k}={v}" for k, v in kv.items())
    logger.info("%s run_id=%s consumer=%s entry_id=%s %s", event, run_id, CONSUMER, entry_id, extra,
                extra={"run_id": run_id})


def _touch(path: Path) -> None:
    """Best-effort liveness/readiness signal for the k8s exec probes. Never
    raises: a probe file we failed to write should fail the probe (visible),
    not crash the process that was otherwise healthy."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    except OSError:
        logger.warning("could not touch %s", path, exc_info=True)


def _current_status(run_id: str) -> str | None:
    """Best-effort read used only for logging context and the post-execute
    recheck below -- never a claim decision. The one conditional UPDATE that
    decides ownership is ``run_service.claim_queued``."""
    db = SessionLocal()
    try:
        row = db.execute(select(Run.status).where(Run.id == run_id)).one_or_none()
        return row[0] if row else None
    finally:
        db.close()


def _execute(run_id: str, file_type: str, start_step: int | None) -> None:
    file_path = UPLOAD_DIR / f"{run_id}.{file_type}"
    _materialize_input_if_missing(run_id, file_type, file_path)
    if not file_path.exists():
        mark_failed(run_id, "Uploaded file no longer available", from_statuses=(RunState.RUNNING,))
        logger.error("run %s has no input file on this worker or in storage", run_id)
        return
    db = SessionLocal()
    try:
        asyncio.run(PipelineOrchestrator(run_id, file_path, db).execute(start_step_number=start_step))
    except Exception:
        # The orchestrator already committed status=failed for the run in the
        # common case; _fail_if_still_running (called right after this
        # returns) covers a terminal commit that itself failed.
        logger.exception("run %s raised", run_id)
    finally:
        db.close()


def _fail_if_still_running(run_id: str) -> None:
    """Design case G: the orchestrator's own terminal commit can itself raise
    and leave the row ``running`` (``_execute`` already logs and swallows
    that exception on the assumption the orchestrator committed a terminal
    status itself). Re-checked right before ``handle``'s ACK, so a failed
    terminal commit is never ACKed with the row still claimed by nobody."""
    if _current_status(run_id) == RunState.RUNNING:
        mark_failed(
            run_id,
            "Worker finished executing but no terminal status was committed",
            from_statuses=(RunState.RUNNING,),
        )


def _watchdog_fire(entry_id: str, run_id: str, queue: Queue = run_queue.SINGLE) -> None:
    """Fires only if a run outlives RUN_TIMEOUT_S. The stage thread it is
    stuck in cannot be killed (the per-stage timeout cancels the await, not
    the thread -- orchestrator._get_stage_timeout_seconds), so this marks the
    row failed, ACKs so nothing waits MIN_IDLE_MS for a token this process is
    about to abandon, and exits the pod so a clean replacement starts.

    os._exit lives in a ``finally`` (N1): mark_failed/ack can themselves
    raise (e.g. the DB is down), and without the ``finally`` that exception
    would propagate out of this Timer callback and be swallowed by
    ``threading``'s default excepthook -- the pod would then never exit, and
    the run the watchdog just gave up on (its stage thread still wedged
    inside this same process) would keep it running forever. os._exit still
    skips every OTHER finally block by design (there is nothing left to run
    after this one), and every test here stubs os._exit."""
    try:
        row_failed = mark_failed(
            run_id, f"Run exceeded {RUN_TIMEOUT_S}s on the worker and was stopped",
            from_statuses=(RunState.RUNNING,),
        ) > 0
        run_queue.ack(entry_id, queue)
        logger.error(
            "run_timeout run_id=%s consumer=%s entry_id=%s timeout_s=%s row_failed=%s",
            run_id, CONSUMER, entry_id, RUN_TIMEOUT_S, row_failed,
        )
    except Exception:
        logger.exception(
            "run_timeout run_id=%s consumer=%s entry_id=%s timeout_s=%s: "
            "mark_failed/ack itself failed; exiting anyway",
            run_id, CONSUMER, entry_id, RUN_TIMEOUT_S,
        )
    finally:
        os._exit(1)


def _heartbeat_while_running(stop: threading.Event) -> None:
    """Touches HEARTBEAT_FILE every HEARTBEAT_INTERVAL_S while a run is in
    flight, on top of loop()'s own per-iteration touch: a run routinely runs
    far longer than one loop iteration, and the liveness probe must not go
    stale mid-run just because the read loop is blocked inside handle()."""
    while not stop.wait(HEARTBEAT_INTERVAL_S):
        _touch(HEARTBEAT_FILE)


def _execute_guarded(
    entry_id: str, run_id: str, file_type: str, start_step: int | None, queue: Queue,
) -> None:
    """Wrap one run's execution with the run watchdog and the in-flight
    heartbeat, then re-check the row before handle()'s own ACK."""
    watchdog = threading.Timer(RUN_TIMEOUT_S, _watchdog_fire, args=(entry_id, run_id, queue))
    watchdog.daemon = True
    watchdog.start()
    heartbeat_stop = threading.Event()
    heartbeat = threading.Thread(target=_heartbeat_while_running, args=(heartbeat_stop,), daemon=True)
    heartbeat.start()
    try:
        _execute(run_id, file_type, start_step)
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=1)
        watchdog.cancel()
    _fail_if_still_running(run_id)


def _dead_letter_poison_entry(
    entry_id: str, fields: dict[str, str], run_id: str, deliveries: int, queue: Queue,
) -> None:
    """A2: a poison entry can reach the delivery cap while its run is either
    still `queued` (every pre-claim attempt failed) or `running` (the row won
    a claim but 3+ post-claim ACK attempts then failed), so both are failed
    here rather than only `queued`. Logged at ERROR with enough context --
    the status just before this attempt, and whether the row actually
    changed -- to tell a real dead-letter from a race that had already
    resolved on its own by the time this fired."""
    prior_status = _current_status(run_id)
    row_failed = mark_failed(
        run_id, f"Dead-lettered after {deliveries} deliveries",
        from_statuses=(RunState.QUEUED, RunState.RUNNING),
    ) > 0
    run_queue.dead_letter(entry_id, fields, queue)
    logger.error(
        "dead_lettered run_id=%s consumer=%s entry_id=%s deliveries=%s prior_status=%s row_failed=%s",
        run_id, CONSUMER, entry_id, deliveries, prior_status, row_failed,
    )


def handle(
    entry_id: str, fields: dict[str, str], *, reclaimed: bool = False, queue: Queue = run_queue.SINGLE,
) -> None:
    try:
        token = run_queue.WorkToken.from_entry(entry_id, fields)
    except run_queue.BadToken:
        # %r, not %s: fields is attacker-reachable (anything with Valkey
        # write access) and unvalidated at this point, so it must not be
        # interpolated raw into a text log line (log-line injection).
        logger.warning("skipped_bad_token entry_id=%s consumer=%s fields=%r", entry_id, CONSUMER, fields)
        run_queue.ack(entry_id, queue)
        return
    run_id = token.run_id

    if reclaimed:
        deliveries = run_queue.delivery_count(entry_id, queue)
        _log("reclaimed", run_id, entry_id, deliveries=deliveries)
        if run_queue.exceeds_delivery_cap(deliveries):
            # DB first: if this write fails, the entry stays pending and
            # comes back on the next reclaim instead of vanishing into the DLQ.
            _dead_letter_poison_entry(entry_id, fields, run_id, deliveries, queue)
            return

    # Outside the ACKing try: a DB error here must leave the entry pending, so
    # XAUTOCLAIM/own-PEL reclaim redeliver it. ACKing without a claim result
    # would strand the run `queued` with no message and nothing to reap it.
    claim = claim_queued(run_id)
    try:
        if not claim.won:
            _log("skipped_not_queued", run_id, entry_id, status=claim.status)
            return
        _log("claimed", run_id, entry_id)
        _execute_guarded(entry_id, run_id, claim.file_type, claim.resume_from_step, queue)
    finally:
        # Success and handled failure both ACK; only a process death or a DB
        # error before the claim decided leaves the entry pending -- exactly
        # what XAUTOCLAIM and own-PEL reclaim exist for.
        run_queue.ack(entry_id, queue)
        _log("acked", run_id, entry_id, queue=queue.name)


class Entry(NamedTuple):
    """One delivered entry and the queue it came from -- the queue every
    ACK/requeue/dead-letter of it must go back to."""
    queue: Queue
    entry_id: str
    fields: dict[str, str]


def _process_owed(entries: list[Entry], *, context: str) -> list[Entry]:
    """Run handle(reclaimed=True) on each already-known entry and return the
    ones whose processing still failed, so the caller can retry exactly those
    next iteration. Deliberately does NOT touch Redis (no XCLAIM/XAUTOCLAIM)
    here -- entries are passed in, already fetched -- so retrying a still-down
    DB does not itself inflate the entry's delivery count and risk a spurious
    dead-letter purely from retrying (B1)."""
    still_owed = []
    for entry in entries:
        try:
            handle(entry.entry_id, entry.fields, reclaimed=True, queue=entry.queue)
        except Exception:
            logger.exception("worker %s: %s entry_id=%s still failing; will retry",
                             CONSUMER, context, entry.entry_id)
            still_owed.append(entry)
    return still_owed


def _reclaim_own_pending(queues: tuple[Queue, ...] = DEFAULT_QUEUES) -> list[Entry]:
    """At worker startup, and again whenever nothing is already known to be
    owed (see loop()): fetch entries this consumer already owns in the PEL
    -- most often a same-pod restart, which keeps the same CONSUMER
    (HOSTNAME) -- immediately instead of waiting out MIN_IDLE_MS
    (RUN_TIMEOUT_S plus margin, N2). This is the only place that re-fetches from Redis (one
    XCLAIM per entry); a failure processing an entry is retried directly via
    _process_owed instead of coming back through here, so a prolonged outage
    never re-XCLAIMs the same entry on every retry. Covers every queue this
    worker reads."""
    entries = [
        Entry(queue, entry_id, fields)
        for queue in queues
        for entry_id, fields in run_queue.reclaim_own_pending(CONSUMER, queue)
    ]
    for entry in entries:
        logger.info("reclaiming own pending entry_id=%s consumer=%s queue=%s at startup",
                    entry.entry_id, CONSUMER, entry.queue.name)
    return _process_owed(entries, context="reclaimed")


def _autoclaim_next(queues: tuple[Queue, ...]) -> Entry | None:
    """XAUTOCLAIM sweep over every queue this worker reads, in order: the
    first entry another consumer left pending past MIN_IDLE_MS."""
    for queue in queues:
        claimed = run_queue.autoclaim_one(CONSUMER, queue)
        if claimed is not None:
            return Entry(queue, *claimed)
    return None


def _read_next(queues: tuple[Queue, ...]) -> Entry | None:
    """The next never-delivered entry, by queue priority. One queue: the
    blocking read on it alone, exactly the pre-#1114 loop. Several: each is
    polled in order without blocking, so a waiting single run is always taken
    before a batch run; only when every one is empty does this block
    (BLOCK_MS) -- on the first queue, the one whose arrivals must not wait."""
    if len(queues) > 1:
        for queue in queues:
            delivered = run_queue.read_one(CONSUMER, queue, block=False)
            if delivered is not None:
                return Entry(queue, *delivered)
    first = queues[0]
    delivered = run_queue.read_one(CONSUMER, first)
    return Entry(first, *delivered) if delivered is not None else None


def loop(queues: tuple[Queue, ...] = DEFAULT_QUEUES) -> None:
    # B1: an own-PEL entry whose processing fails here (DB/Valkey still down)
    # must not be abandoned after one immediate retry -- left in this
    # consumer's own PEL it would otherwise strand until MIN_IDLE_MS elapses
    # (reconcile_queued_runs also can't see it: live_run_ids() counts a
    # pending entry as live). `owed` keeps the specific entries that failed
    # so every subsequent iteration retries exactly them (via _process_owed,
    # not a fresh Redis reclaim) until they succeed; `owed is None` means "we
    # don't know of anything, redo the Redis-side scan" -- used only when a
    # failure left no specific entry in hand.
    owed: list[Entry] | None
    try:
        owed = _reclaim_own_pending(queues)
    except Exception:
        logger.exception("worker %s: startup own-PEL reclaim failed", CONSUMER)
        owed = None
    while not shutting_down.is_set():
        _touch(HEARTBEAT_FILE)
        if owed:
            owed = _process_owed(owed, context="pending")
        elif owed is None:
            try:
                owed = _reclaim_own_pending(queues)
            except Exception:
                logger.exception("worker %s: pending own-PEL reclaim failed", CONSUMER)
                owed = None
        entry = None
        try:
            entry = _autoclaim_next(queues)
            reclaimed = entry is not None
            if entry is None:
                entry = _read_next(queues)
            if entry is None:
                continue
            if shutting_down.is_set():
                # A4: SIGTERM arrived while this blocking read/claim was in
                # flight. Hand the token straight back as a fresh entry
                # rather than starting a new run during drain -- the next
                # (non-draining) worker picks it up within seconds instead of
                # waiting out MIN_IDLE_MS.
                new_entry_id = run_queue.requeue(entry.entry_id, entry.fields, entry.queue)
                logger.info("shutdown: requeued entry_id=%s as new_entry_id=%s before claiming",
                            entry.entry_id, new_entry_id)
                continue
            handle(entry.entry_id, entry.fields, reclaimed=reclaimed, queue=entry.queue)
        except Exception:
            # A DB or Valkey blip must not crash-loop the pod: the entry in hand
            # is still pending (handle only ACKs after the claim decided), so
            # XAUTOCLAIM/own-PEL reclaim redeliver it; wait out the outage and
            # read again.
            logger.exception("worker %s: loop iteration failed; retrying in %ss", CONSUMER, RETRY_DELAY_S)
            if entry is not None:
                owed = (owed or []) + [entry]
            elif not owed:
                owed = None
            shutting_down.wait(RETRY_DELAY_S)


def _start_email_intake() -> threading.Thread | None:
    """Start the emailed-CV intake poller (#1298) when CVICHE_EMAIL_INTAKE is on.

    Off by default. The import is here, not at module top: the intake service
    pulls in app.auth for the ED check, which a worker with intake off must not
    need (see run_service.UPLOAD_DIR)."""
    if not email_intake_enabled():
        return None
    from app.services import inbound_service
    from app.storage import get_storage

    thread = threading.Thread(
        target=inbound_service.run_intake_loop, args=(SessionLocal, get_storage(), shutting_down),
        name="email-intake", daemon=True,
    )
    thread.start()
    logger.info("worker %s: email intake poller started", CONSUMER)
    return thread


def main() -> int:
    configure_logging()
    url, _ = get_config("redis", "CVICHE_REDIS_URL", default="")
    backend, _ = get_config("s3", "CVICHE_STORAGE_BACKEND", default="local")
    storage_ok = backend == "s3" or os.environ.get("CVICHE_WORKER_ALLOW_LOCAL_STORAGE") == "1"
    if not url or not storage_ok:
        logger.error("worker refusing to start: CVICHE_REDIS_URL set=%s CVICHE_STORAGE_BACKEND=%s "
                     "(both required: outputs must cross pods via S3)", bool(url), backend)
        return 2
    try:
        queues = run_queue.worker_queues()
    except ValueError:
        logger.exception("worker refusing to start: CVICHE_WORKER_STREAMS is invalid")
        return 2
    # Every run's terminal Teams card is posted from here, not the backend
    # (CVICHE_DISPATCH_MODE=queue), so a worker without the webhook drops them
    # all silently. Warn, don't refuse: notifications are best-effort.
    from app.services.notifications import validate_configuration
    notif_status = validate_configuration()
    if not notif_status["valid"]:
        logger.warning("worker %s will post no Teams run cards: CVICHE_TEAMS_WEBHOOK_URL "
                       "configured=%s valid=%s", CONSUMER, notif_status["configured"], notif_status["valid"])
    # Publish side of the broker only, wired like app.main's lifespan; the
    # backend replicas run the subscriber that fans out to WebSockets.
    from app.pipeline import orchestrator as orchestrator_module
    from app.pipeline.event_emitter import event_emitter
    from app.pipeline.redis_broker import broker_from_env
    broker = broker_from_env()
    event_emitter.set_broker(broker)
    orchestrator_module.set_broker(broker)
    for queue in queues:
        run_queue.ensure_group(queue)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: shutting_down.set())
    # Readiness only after every startup check above (including ensure_group,
    # which proves Valkey is actually reachable) has passed -- a rollout must
    # not count a pod as available before that.
    _touch(READY_FILE)
    logger.info("worker %s started (queues=%s)", CONSUMER, ",".join(queue.name for queue in queues))
    _start_email_intake()
    loop(queues)
    logger.info("worker %s stopped", CONSUMER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
