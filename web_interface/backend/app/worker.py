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
"""
import asyncio
import logging
import os
import signal
import socket
import threading
from pathlib import Path

from sqlalchemy import select

from app.config_loader import get_config
from app.database import SessionLocal
from app.logging_config import configure_logging
from app.models import Run, RunState
from app.pipeline import run_queue
from app.pipeline.orchestrator import PipelineOrchestrator
from app.services.run_service import UPLOAD_DIR, _materialize_input_if_missing, claim_queued, mark_failed

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
RUN_TIMEOUT_S, _ = get_config("llm", "CVICHE_RUN_TIMEOUT_SECONDS", default=5400)
RUN_TIMEOUT_S = int(RUN_TIMEOUT_S)

# How often the in-flight heartbeat thread (see _heartbeat_while_running)
# touches HEARTBEAT_FILE while a run is executing. Well under the liveness
# probe's failureThreshold x period (k8s/base/worker/deployment.yaml, ~60-90s).
HEARTBEAT_INTERVAL_S = 15
READY_FILE = Path("/tmp/worker-ready")
HEARTBEAT_FILE = Path("/tmp/worker-heartbeat")


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


def _watchdog_fire(entry_id: str, run_id: str) -> None:
    """Fires only if a run outlives RUN_TIMEOUT_S. The stage thread it is
    stuck in cannot be killed (the per-stage timeout cancels the await, not
    the thread -- orchestrator._get_stage_timeout_seconds), so this marks the
    row failed, ACKs so nothing waits MIN_IDLE_MS for a token this process is
    about to abandon, and exits the pod so a clean replacement starts.
    os._exit skips finally blocks by design: the mark-failed commit and the
    ACK above already happened, and every test here stubs os._exit."""
    row_failed = mark_failed(
        run_id, f"Run exceeded {RUN_TIMEOUT_S}s on the worker and was stopped",
        from_statuses=(RunState.RUNNING,),
    ) > 0
    run_queue.ack(entry_id)
    logger.error(
        "run_timeout run_id=%s consumer=%s entry_id=%s timeout_s=%s row_failed=%s",
        run_id, CONSUMER, entry_id, RUN_TIMEOUT_S, row_failed,
    )
    os._exit(1)


def _heartbeat_while_running(stop: threading.Event) -> None:
    """Touches HEARTBEAT_FILE every HEARTBEAT_INTERVAL_S while a run is in
    flight, on top of loop()'s own per-iteration touch: a run routinely runs
    far longer than one loop iteration, and the liveness probe must not go
    stale mid-run just because the read loop is blocked inside handle()."""
    while not stop.wait(HEARTBEAT_INTERVAL_S):
        _touch(HEARTBEAT_FILE)


def _execute_guarded(entry_id: str, run_id: str, file_type: str, start_step: int | None) -> None:
    """Wrap one run's execution with the run watchdog and the in-flight
    heartbeat, then re-check the row before handle()'s own ACK."""
    watchdog = threading.Timer(RUN_TIMEOUT_S, _watchdog_fire, args=(entry_id, run_id))
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


def _dead_letter_poison_entry(entry_id: str, fields: dict[str, str], run_id: str, deliveries: int) -> None:
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
    run_queue.dead_letter(entry_id, fields)
    logger.error(
        "dead_lettered run_id=%s consumer=%s entry_id=%s deliveries=%s prior_status=%s row_failed=%s",
        run_id, CONSUMER, entry_id, deliveries, prior_status, row_failed,
    )


def handle(entry_id: str, fields: dict[str, str], *, reclaimed: bool = False) -> None:
    try:
        token = run_queue.WorkToken.from_entry(entry_id, fields)
    except run_queue.BadToken:
        # %r, not %s: fields is attacker-reachable (anything with Valkey
        # write access) and unvalidated at this point, so it must not be
        # interpolated raw into a text log line (log-line injection).
        logger.warning("skipped_bad_token entry_id=%s consumer=%s fields=%r", entry_id, CONSUMER, fields)
        run_queue.ack(entry_id)
        return
    run_id = token.run_id

    if reclaimed:
        deliveries = run_queue.delivery_count(entry_id)
        _log("reclaimed", run_id, entry_id, deliveries=deliveries)
        if run_queue.exceeds_delivery_cap(deliveries):
            # DB first: if this write fails, the entry stays pending and
            # comes back on the next reclaim instead of vanishing into the DLQ.
            _dead_letter_poison_entry(entry_id, fields, run_id, deliveries)
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
        _execute_guarded(entry_id, run_id, claim.file_type, claim.resume_from_step)
    finally:
        # Success and handled failure both ACK; only a process death or a DB
        # error before the claim decided leaves the entry pending -- exactly
        # what XAUTOCLAIM and own-PEL reclaim exist for.
        run_queue.ack(entry_id)
        _log("acked", run_id, entry_id)


def _reclaim_own_pending() -> None:
    """At worker startup, and again after any loop() exception: pick up
    entries this consumer already owns in the PEL -- most often a same-pod
    restart, which keeps the same CONSUMER (HOSTNAME) -- immediately instead
    of waiting out MIN_IDLE_MS (45 min in production)."""
    for entry_id, fields in run_queue.reclaim_own_pending(CONSUMER):
        logger.info("reclaiming own pending entry_id=%s consumer=%s at startup", entry_id, CONSUMER)
        handle(entry_id, fields, reclaimed=True)


def loop() -> None:
    try:
        _reclaim_own_pending()
    except Exception:
        logger.exception("worker %s: startup own-PEL reclaim failed", CONSUMER)
    while not shutting_down.is_set():
        _touch(HEARTBEAT_FILE)
        try:
            entry = run_queue.autoclaim_one(CONSUMER)
            reclaimed = entry is not None
            if entry is None:
                entry = run_queue.read_one(CONSUMER)
            if entry is None:
                continue
            if shutting_down.is_set():
                # A4: SIGTERM arrived while this blocking read/claim was in
                # flight. Hand the token straight back as a fresh entry
                # rather than starting a new run during drain -- the next
                # (non-draining) worker picks it up within seconds instead of
                # waiting out MIN_IDLE_MS.
                new_entry_id = run_queue.requeue(*entry)
                logger.info("shutdown: requeued entry_id=%s as new_entry_id=%s before claiming",
                            entry[0], new_entry_id)
                continue
            handle(*entry, reclaimed=reclaimed)
        except Exception:
            # A DB or Valkey blip must not crash-loop the pod: the entry in hand
            # is still pending (handle only ACKs after the claim decided), so
            # XAUTOCLAIM/own-PEL reclaim redeliver it; wait out the outage and
            # read again. The reclaim attempt below is itself guarded -- it
            # must never let a still-ongoing outage kill this loop either.
            logger.exception("worker %s: loop iteration failed; retrying in %ss", CONSUMER, RETRY_DELAY_S)
            try:
                _reclaim_own_pending()
            except Exception:
                logger.exception("worker %s: post-error own-PEL reclaim failed", CONSUMER)
            shutting_down.wait(RETRY_DELAY_S)


def main() -> int:
    configure_logging()
    url, _ = get_config("redis", "CVICHE_REDIS_URL", default="")
    backend, _ = get_config("s3", "CVICHE_STORAGE_BACKEND", default="local")
    storage_ok = backend == "s3" or os.environ.get("CVICHE_WORKER_ALLOW_LOCAL_STORAGE") == "1"
    if not url or not storage_ok:
        logger.error("worker refusing to start: CVICHE_REDIS_URL set=%s CVICHE_STORAGE_BACKEND=%s "
                     "(both required: outputs must cross pods via S3)", bool(url), backend)
        return 2
    # Publish side of the broker only, wired like app.main's lifespan; the
    # backend replicas run the subscriber that fans out to WebSockets.
    from app.pipeline import orchestrator as orchestrator_module
    from app.pipeline.event_emitter import event_emitter
    from app.pipeline.redis_broker import broker_from_env
    broker = broker_from_env()
    event_emitter.set_broker(broker)
    orchestrator_module.set_broker(broker)
    run_queue.ensure_group()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: shutting_down.set())
    # Readiness only after every startup check above (including ensure_group,
    # which proves Valkey is actually reachable) has passed -- a rollout must
    # not count a pod as available before that.
    _touch(READY_FILE)
    logger.info("worker %s started (stream=%s group=%s)", CONSUMER, run_queue.STREAM, run_queue.GROUP)
    loop()
    logger.info("worker %s stopped", CONSUMER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
