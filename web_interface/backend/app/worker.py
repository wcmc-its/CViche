"""Pipeline run worker: ``python -m app.worker`` (#701).

Consumes work tokens from the Valkey stream (``app.pipeline.run_queue``) and
executes one run at a time; global concurrency is the worker replica count.
Never starts uvicorn or imports the FastAPI app. Correctness lives in the DB
claim, not the queue: a delivered token may execute only if this process wins
the conditional ``queued -> running`` UPDATE, so a redelivered token for a run
that is already running, complete or cancelled is skipped and ACKed.

SIGTERM/SIGINT stop the read loop; a run in flight finishes and ACKs first.
A kill mid-run leaves the entry un-ACKed and the row ``running``; XAUTOCLAIM
hands the entry to another worker after MIN_IDLE_MS, whose claim then fails,
and the stale-run reaper marks the row failed. v1 does not auto-resume.
"""
import asyncio
import logging
import os
import signal
import socket
import threading
from datetime import datetime

from sqlalchemy import select, update

from app.api.runs import UPLOAD_DIR, _materialize_input_if_missing
from app.config_loader import get_config
from app.database import SessionLocal
from app.logging_config import configure_logging
from app.models import Run
from app.pipeline import run_queue
from app.pipeline.orchestrator import PipelineOrchestrator

logger = logging.getLogger(__name__)

CONSUMER = os.environ.get("HOSTNAME") or socket.gethostname()
shutting_down = threading.Event()


def _log(event: str, run_id: str, entry_id: str, **kv: object) -> None:
    extra = " ".join(f"{k}={v}" for k, v in kv.items())
    logger.info("%s run_id=%s consumer=%s entry_id=%s %s", event, run_id, CONSUMER, entry_id, extra,
                extra={"run_id": run_id})


def _claim(run_id: str) -> tuple[bool, str | None, str | None]:
    """The one conditional UPDATE that decides ownership. Own short session,
    committed at once so no lock spans the run. Returns (won, status, file_type)."""
    db = SessionLocal()
    try:
        won = db.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == "queued")
            .values(status="running", started_at=datetime.now(), error_message=None, completed_at=None)
        ).rowcount == 1
        db.commit()
        row = db.execute(select(Run.status, Run.file_type).where(Run.id == run_id)).one_or_none()
        return won, row[0] if row else None, row[1] if row else None
    finally:
        db.close()


def _mark_failed(run_id: str, message: str, *, from_status: str) -> None:
    db = SessionLocal()
    try:
        db.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == from_status)
            .values(status="failed", error_message=message, completed_at=datetime.now())
        )
        db.commit()
    finally:
        db.close()


def _execute(run_id: str, file_type: str, start_step: int | None) -> None:
    file_path = UPLOAD_DIR / f"{run_id}.{file_type}"
    _materialize_input_if_missing(run_id, file_type, file_path)
    if not file_path.exists():
        _mark_failed(run_id, "Uploaded file no longer available", from_status="running")
        logger.error("run %s has no input file on this worker or in storage", run_id)
        return
    db = SessionLocal()
    try:
        asyncio.run(PipelineOrchestrator(run_id, file_path, db).execute(start_step_number=start_step))
    except Exception:
        # The orchestrator already committed status=failed for the run.
        logger.exception("run %s raised", run_id)
    finally:
        db.close()


def handle(entry_id: str, fields: dict[str, str], *, reclaimed: bool = False) -> None:
    run_id = fields.get("run_id", "")
    if reclaimed:
        deliveries = run_queue.delivery_count(entry_id)
        _log("reclaimed", run_id, entry_id, deliveries=deliveries)
        if deliveries > run_queue.MAX_DELIVERIES:
            run_queue.dead_letter(entry_id, fields)
            _mark_failed(run_id, f"Dead-lettered after {deliveries} deliveries", from_status="queued")
            _log("dead_lettered", run_id, entry_id)
            return
    try:
        won, status, file_type = _claim(run_id)
        if not won:
            _log("skipped_not_queued", run_id, entry_id, status=status)
            return
        _log("claimed", run_id, entry_id)
        start_step = fields.get("start_step")
        _execute(run_id, file_type, int(start_step) if start_step else None)
    finally:
        # Success and handled failure both ACK; only a process death leaves the
        # entry pending, which is exactly what XAUTOCLAIM exists for.
        run_queue.ack(entry_id)
        _log("acked", run_id, entry_id)


def loop() -> None:
    while not shutting_down.is_set():
        entry = run_queue.autoclaim_one(CONSUMER)
        if entry:
            handle(*entry, reclaimed=True)
            continue
        entry = run_queue.read_one(CONSUMER)
        if entry:
            handle(*entry)


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
    logger.info("worker %s started (stream=%s group=%s)", CONSUMER, run_queue.STREAM, run_queue.GROUP)
    loop()
    logger.info("worker %s stopped", CONSUMER)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
