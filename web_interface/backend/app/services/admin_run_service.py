"""Admin operations on runs and their feedback rows (#335, #336).

Behind the admin runs routes: the paginated runs listing, hard deletes, the
orphan reaper, rescoring and the run-queue diagnostics. The queries, guards,
the error each failure maps to, and the audit lines live here; the route
handlers parse the request and shape the response. The run delete and the
reaper's sweep themselves are run_service's.
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor

import redis
from sqlalchemy.orm import Session, contains_eager

from app.audit_events import RUN_DELETED
from app.config_loader import current_image_tag, get_config
from app.errors import bad_request, conflict, internal_error, not_found
from app.models import Feedback, Run, RunState, User
from app.pipeline import concurrency, run_queue
from app.schemas import (
    AdminRunEntry,
    AdminRunsResponse,
    DoctorRerunResult,
    QualityScoreResult,
    QueueDbView,
    QueueStatsResponse,
    QueueStreamStats,
)
from app.services.artifact_service import RUN_ID_RE
from app.services.quality_score_service import (
    DOCTOR_STATUS_KEY,
    DOCTOR_SUFFIX,
    compute_and_cache_score,
    doctor_status,
    get_cached_score,
    persist_score_columns,
)
from app.services.review_loop_service import doctor_on_stored_outputs
from app.services.run_service import (
    delete_run_and_artifacts,
    find_rowless_archives,
    find_run,
    queue_db_view,
    reap_orphaned_created_runs,
)
from app.storage import get_storage

logger = logging.getLogger(__name__)

# Where the orchestrator mirrors a run's outputs in durable storage
# (_persist_outputs_to_storage); the re-run doctor's report goes there too.
OUTPUTS_PREFIX = "outputs/"
DOCTOR_RERUN_NOT_COMPLETE = "Only a completed run's doctor can be re-run."
DOCTOR_RERUN_HAS_REPORT = "This run already has a doctor report."


def list_runs(
    db: Session, *, offset: int, limit: int, user_email: str | None, status: str | None
) -> AdminRunsResponse:
    """One page of every user's runs, newest first, with feedback and cached
    quality-score fields. ``user_email`` is a case-insensitive substring;
    ``status`` is an exact status, "all", or None for every status but "created"."""
    # Eager-load run.user via the outer join (the relationship is
    # lazy="raise_on_sql"). contains_eager populates run.user from the joined
    # columns -- one query, no per-row lookup -- while the outer join still
    # lets us filter by user email.
    query = db.query(Run).outerjoin(Run.user).options(contains_eager(Run.user))

    if user_email:
        query = query.filter(User.email.ilike(f"%{user_email}%"))

    # Status filtering. The default admin view hides never-started "created"
    # runs -- these accumulate as clutter when users upload a blank WCM template
    # and decline to proceed, leaving a run that is never advanced. An explicit
    # status filters to exactly that status (including "created" to inspect the
    # abandoned ones); the "all" sentinel opts back in to every status.
    if status == "all":
        pass
    elif status:
        query = query.filter(Run.status == status)
    else:
        query = query.filter(Run.status != RunState.CREATED)

    total = query.count()
    rows = query.order_by(Run.started_at.desc()).offset(offset).limit(limit).all()

    # Build run entries with feedback status
    run_ids = [run.id for run in rows]
    feedback_run_ids = set()
    if run_ids:
        feedback_rows = (
            db.query(Feedback.run_id)
            .filter(Feedback.run_id.in_(run_ids))
            .distinct()
            .all()
        )
        feedback_run_ids = {row.run_id for row in feedback_rows}

    # Read cached advisory quality scores in parallel (small JSON per run; only
    # present for runs already scored — None otherwise). Admin-only / paginated.
    cached_scores: dict[str, dict] = {}
    if run_ids:
        with ThreadPoolExecutor(max_workers=8) as pool:
            for rid, score in zip(run_ids, pool.map(get_cached_score, run_ids)):
                if score:
                    cached_scores[rid] = score

    entries = []
    for run in rows:
        # Prefer the persisted pipeline duration so the admin table matches the
        # run status/history API; fall back to wall-clock for runs that predate
        # the column. (Still blank for in-flight runs with no completed_at.)
        if run.total_duration_seconds is not None:
            duration = run.total_duration_seconds
        elif run.started_at and run.completed_at:
            duration = int((run.completed_at - run.started_at).total_seconds())
        else:
            duration = None

        score = cached_scores.get(run.id)
        entries.append(
            AdminRunEntry(
                run_id=run.id,
                user_email=run.user.email if run.user else None,
                user_display_name=run.user.display_name if run.user else None,
                filename=run.filename,
                status=run.status,
                duration_seconds=duration,
                total_cost=round(run.total_cost or 0, 4),
                started_at=run.started_at,
                has_feedback=run.id in feedback_run_ids,
                quality_score=score.get("totalScore") if score else None,
                quality_band=score.get("band") if score else None,
                quality_data_complete=score.get("data_complete") if score else None,
                quality_missing_evidence=(score.get("missing_evidence") or []) if score else [],
            )
        )

    return AdminRunsResponse(
        runs=entries,
        total=total,
        has_more=(offset + limit) < total,
        offset=offset,
        limit=limit,
    )


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
    """run_service.reap_orphaned_created_runs, audit-logged with the acting admin,
    plus the input archives in storage that have no run row at all (#802).

    ``rowless_archives`` counts those archives and ``rowless_run_ids`` names
    them; they are reported, never deleted here (#1216). Both are measured
    after the reap, so a reaped run whose storage could not be deleted shows
    up in them. If storage cannot be listed, ``rowless_archives`` is None and
    ``rowless_run_ids`` is empty: the reap has already happened, and an
    unlisted store must not read as a clean one.
    """
    result = reap_orphaned_created_runs(
        db, older_than_hours=older_than_hours, dry_run=dry_run
    )
    rowless = _rowless_archives_or_none(db)
    result["rowless_archives"] = None if rowless is None else len(rowless)
    result["rowless_run_ids"] = rowless or []
    logger.info(
        "admin_reap_orphans: admin=%s dry_run=%s candidates=%d reaped=%d objects=%d rowless=%s",
        admin.email, dry_run, result["candidates"], result["reaped"], result["objects_deleted"],
        result["rowless_archives"],
    )
    return result


def _rowless_archives_or_none(db: Session) -> list[str] | None:
    """find_rowless_archives, or None (logged with its traceback) when storage
    or the lookup fails, so the reap result above still reaches the admin."""
    try:
        return find_rowless_archives(db)
    except Exception:
        logger.exception("Rowless-archive sweep failed during admin reap-orphans (#802)")
        return None


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
        # only to the DEBUG log, never the response.
        logger.warning("Queue stats unavailable: %s", type(e).__name__)
        logger.debug("Queue stats unavailable: %s", e)
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
    if run.status == RunState.RUNNING:
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
    run = find_run(db, run_id)
    if not run:
        raise not_found("Run not found")
    run_id = run.id  # the stored id from here on, never the URL's string

    # A rescore can't see why the doctor didn't run; keep the reason the
    # orchestrator cached (failed/skipped) rather than reset it to not_checked.
    result = compute_and_cache_score(run_id, doctor_outcome=_cached_doctor_status(run_id))
    if not result:
        raise not_found("No scorable outputs available for this run")

    persist_score_columns(db, run_id, result)
    return _score_result(run_id, result)


def rerun_doctor(db: Session, run_id: str, admin: User) -> DoctorRerunResult:
    """Run the doctor over a completed run's stored outputs, store its report
    beside them and rescore the run from it (#1593): the way back for a run
    whose doctor failed, was switched off, or died with its pod. Only the
    doctor runs: no pipeline stage, no LLM call. 400 for a malformed run id;
    404 for an unknown run; 409 for one not complete, or one that already has
    a report, readable or not (the review copy's comments and their verdicts
    were written against that one)."""
    if not RUN_ID_RE.fullmatch(run_id):
        raise bad_request("Invalid run ID")
    run = find_run(db, run_id)
    if not run:
        raise not_found("Run not found")
    # Every storage key and temp path below is built from the stored id, never
    # from the URL's string (CodeQL py/path-injection).
    run_id = run.id
    if run.status != RunState.COMPLETE:
        raise conflict(DOCTOR_RERUN_NOT_COMPLETE)
    if _has_doctor_report(run_id):
        raise conflict(DOCTOR_RERUN_HAS_REPORT)

    try:
        payload = _store_doctor_report(db, run_id)
    except Exception as e:
        logger.exception("Doctor re-run for run %s failed", run_id)
        raise internal_error("The doctor re-run failed; it is safe to retry.") from e
    logger.info("Doctor re-run for run %s by %s: worst severity %s",
                run_id, admin.email, payload.get("worst_severity"))

    result = compute_and_cache_score(run_id)
    persist_score_columns(db, run_id, result)
    return DoctorRerunResult(
        run_id=run_id,
        worst_severity=payload.get("worst_severity"),
        counts=payload.get("counts") or {},
        score=_score_result(run_id, result) if result else None,
    )


def _cached_doctor_status(run_id: str) -> str | None:
    """The doctor status the run's cached score recorded; None when no score
    is cached or it recorded none."""
    cached = get_cached_score(run_id)
    return doctor_status(cached.get(DOCTOR_STATUS_KEY)) if isinstance(cached, dict) else None


def _has_doctor_report(run_id: str) -> bool:
    """Whether a doctor report is stored under the run's outputs. By key, not
    by parsing it: an unreadable report still blocks an overwrite, and a
    storage error raises rather than reading as "no report"."""
    keys = get_storage().list_files(run_id, OUTPUTS_PREFIX)
    return any(k.endswith(DOCTOR_SUFFIX) for k in keys)


def _store_doctor_report(db: Session, run_id: str) -> dict:
    """The doctor's report on the run's stored outputs, stamped with this
    image's tag and stored under outputs/ where the orchestrator mirrors it."""
    from app.pipeline.orchestrator import DOCTOR_IMAGE_TAG_KEY

    payload = doctor_on_stored_outputs(db, run_id)
    payload[DOCTOR_IMAGE_TAG_KEY] = current_image_tag()
    get_storage().put_file(run_id, f"{OUTPUTS_PREFIX}{run_id}{DOCTOR_SUFFIX}",
                           json.dumps(payload, indent=2).encode("utf-8"))
    return payload


def _score_result(run_id: str, result: dict) -> QualityScoreResult:
    """The rescore response for a freshly computed score dict."""
    return QualityScoreResult(
        run_id=run_id,
        totalScore=result.get("totalScore", 0),
        band=result.get("band", ""),
        dimensionScores=result.get("dimensionScores", []),
        flags=result.get("flags", []),
        data_complete=result.get("data_complete"),
        missing_evidence=result.get("missing_evidence", []),
        deliverable=result.get("deliverable"),
        blockers=result.get("blockers", []),
    )
