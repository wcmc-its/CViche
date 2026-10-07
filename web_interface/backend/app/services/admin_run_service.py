"""Admin operations on runs and their feedback rows (#335).

Behind the admin runs routes: hard deletes, the orphan reaper, rescoring and
the run-queue diagnostics. The guards, the error each failure maps to, and
the audit lines live here; the route handlers parse the request and shape the
response. The run delete and the reaper's sweep themselves are run_service's.
"""
import logging

import redis
from sqlalchemy.orm import Session

from app.audit_events import RUN_DELETED
from app.config_loader import get_config
from app.errors import conflict, internal_error, not_found
from app.models import Feedback, User
from app.pipeline import concurrency, run_queue
from app.schemas import QualityScoreResult, QueueDbView, QueueStatsResponse, QueueStreamStats
from app.services.quality_score_service import compute_and_cache_score, persist_score_columns
from app.services.run_service import delete_run_and_artifacts, find_run, queue_db_view, reap_orphaned_created_runs

logger = logging.getLogger(__name__)


def hard_delete_feedback(db: Session, feedback_id: int, admin: User) -> None:
    """Delete one feedback row (404 if it does not exist), audit-logged with the
    acting admin and the run it belonged to."""
    feedback = db.query(Feedback).filter(Feedback.id == feedback_id).first()
    if not feedback:
        raise not_found("Feedback not found.")

    run_id = feedback.run_id
    db.delete(feedback)
    db.commit()

    logger.info(
        "admin_feedback_deleted: admin=%s feedback_id=%s run_id=%s",
        admin.email,
        feedback_id,
        run_id,
    )


def reap_orphans(db: Session, admin: User, *, older_than_hours: int | None, dry_run: bool) -> dict[str, object]:
    """run_service.reap_orphaned_created_runs, audit-logged with the acting admin."""
    result = reap_orphaned_created_runs(
        db, older_than_hours=older_than_hours, dry_run=dry_run
    )
    logger.info(
        "admin_reap_orphans: admin=%s dry_run=%s candidates=%d reaped=%d objects=%d",
        admin.email, dry_run, result["candidates"], result["reaped"], result["objects_deleted"],
    )
    return result


def queue_stats(db: Session) -> QueueStatsResponse:
    """The DB view of queued/running runs, plus each Valkey queue's stream stats
    in queue dispatch mode. A missing URL or an unreachable Valkey is a stable
    error code beside the DB view, never a 500 (#701, #1114)."""
    db_view = QueueDbView(**queue_db_view(db))
    if concurrency.dispatch_mode() != "queue":
        return QueueStatsResponse(enabled=False, db=db_view)

    url, _ = get_config("redis", "CVICHE_REDIS_URL", default="")
    if not url:
        return QueueStatsResponse(enabled=True, db=db_view, error="valkey_not_configured")
    try:
        per_queue = {queue.name: run_queue.stats(queue) for queue in run_queue.QUEUES_BY_NAME.values()}
    except redis.exceptions.RedisError as e:
        # The endpoint that diagnoses a stuck queue must still answer when
        # Valkey itself is the problem. The exception's own text can carry a
        # host:port (e.g. "Error 111 connecting to valkey:6379") -- that goes
        # only to the log, never the response.
        logger.warning("Queue stats unavailable: %s: %s", type(e).__name__, e)
        return QueueStatsResponse(enabled=True, db=db_view, error="valkey_unavailable")
    return QueueStatsResponse(
        enabled=True, db=db_view, **per_queue[run_queue.SINGLE.name],
        queues={name: QueueStreamStats(**stats) for name, stats in per_queue.items()},
    )


def hard_delete_run(db: Session, run_id: str, admin: User) -> None:
    """Delete one run and its artifacts: 404 if unknown, 409 while it is
    running, 500 if storage or the DB delete fails (storage goes first, so a
    retry is safe). A success is a RUN_DELETED audit event."""
    run = find_run(db, run_id)
    if not run:
        raise not_found("Run not found.")
    if run.status == "running":
        raise conflict("Run is still running; wait for it to finish before deleting.")

    try:
        objects_deleted = delete_run_and_artifacts(db, run)
    except Exception:
        logger.exception("Admin delete of run %s failed", run_id)
        raise internal_error("Run deletion failed; it is safe to retry.")
    logger.info(
        RUN_DELETED,
        extra={"admin": admin.email, "run_id": run_id, "objects_deleted": objects_deleted},
    )


def score_run(db: Session, run_id: str) -> QualityScoreResult:
    """Compute (or refresh) a run's advisory quality score, cache it, and copy
    it onto the run row. 404 for an unknown run, and for one with nothing to score."""
    if not find_run(db, run_id):
        raise not_found("Run not found")

    result = compute_and_cache_score(run_id)
    if not result:
        raise not_found("No scorable outputs available for this run")

    persist_score_columns(db, run_id, result)

    return QualityScoreResult(
        run_id=run_id,
        totalScore=result.get("totalScore", 0),
        band=result.get("band", ""),
        dimensionScores=result.get("dimensionScores", []),
        flags=result.get("flags", []),
        data_complete=result.get("data_complete"),
        missing_evidence=result.get("missing_evidence", []),
    )
