"""Run-related service functions."""
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from app.models import Run, Step, User, Log, LLMUsage, Feedback, RunMetrics
from app.errors import not_found, forbidden
from app.config_loader import get_config
from app.storage import get_storage
from app.services import auto_retry

logger = logging.getLogger(__name__)

# A run older than this while still marked "running" at startup is treated as
# orphaned by a server restart. Generous relative to the ~15-20 min a real run
# takes, so a sibling replica's genuinely in-flight run is never swept.
DEFAULT_STALE_RUN_MINUTES = 60

# A run still at status="created" this many hours after upload was never started
# (or its start failed / was abandoned) and is safe to reap along with its
# storage. Generous so a just-uploaded run that is about to be started is never
# swept.
DEFAULT_ORPHAN_REAP_HOURS = 24


def _mark_run_failed(run: Run, db: Session, now: datetime) -> None:
    """Mark an orphaned "running" run (and any still-running steps) failed.

    This is the pre-#145 behaviour, factored out so both the default path and
    the auto-retry fallback (when a resume launch fails) can reuse it.
    """
    run.status = "failed"
    run.completed_at = now
    run.error_message = (
        "Run interrupted — the server restarted while this run was in "
        "progress. Please restart it with the same file."
    )
    running_steps = (
        db.query(Step)
        .filter(Step.run_id == run.id, Step.status == "running")
        .all()
    )
    for step in running_steps:
        step.status = "error"
        step.completed_at = now
        if not step.error_message:
            step.error_message = "Interrupted by server restart."


def _resume_info_for_run(run: Run, db: Session):
    """Inspect a stale run's steps to decide where/why to resume.

    Returns ``(last_error_type, start_step_number)`` where:
      * ``last_error_type`` is the ``error_type`` of the most-advanced step that
        is errored or still running (or None if none / unclassified). This feeds
        the retryable-vs-terminal decision in ``auto_retry``.
      * ``start_step_number`` is the first non-complete step's number -- the
        stage the pipeline should resume from. Defaults to 1 if every step is
        somehow complete (degenerate; resume from the top).
    """
    steps = (
        db.query(Step)
        .filter(Step.run_id == run.id)
        .order_by(Step.step_number)
        .all()
    )

    last_error_type = None
    for step in steps:
        if step.status in ("error", "running") and step.error_type:
            # Later steps win: we want the error_type of the most-advanced
            # errored/running stage (the one that actually stalled).
            last_error_type = step.error_type

    start_step_number = None
    for step in steps:
        if step.status != "complete":
            start_step_number = step.step_number
            break
    if start_step_number is None:
        start_step_number = 1

    return last_error_type, start_step_number


def reconcile_stale_runs(db: Session) -> int:
    """Mark orphaned "running" runs as failed. Called once at startup.

    A run executes as an in-process background task (see runs.py:start_run). If
    the pod recycles mid-run -- which happens on EKS -- the task dies but the DB
    row stays status="running" forever and the UI counts elapsed time upward
    with no resolution. On startup we sweep runs still marked "running" whose
    started_at is older than the threshold, marking them (and any still-running
    steps) failed so the user gets a clear outcome and can restart.

    Auto-retry (issue #145, FLAG-GATED OFF by default): before marking a stale
    run failed, we ask ``auto_retry.eligible_for_resume`` whether it should be
    bounded-auto-retried instead. A stale "running" run is by definition a
    pod/server interruption (``is_interruption=True``), so when the feature flag
    is on and the run is under its attempt budget it is resumed rather than
    failed. Because ``auto_retry.auto_retry_enabled()`` defaults FALSE,
    ``eligible_for_resume`` is always False by default -> the else-branch always
    runs -> behaviour on origin/dev is byte-for-byte unchanged until
    ``CVICHE_AUTO_RETRY_ENABLED`` is set.

    Age-based rather than "any running run" so that, with multiple replicas, a
    sibling's genuinely in-flight run is not killed. Returns the count of runs
    marked failed (resumed runs are NOT counted -- they stay "running").
    """
    try:
        #minutes = int(os.environ.get("CVICHE_STALE_RUN_MINUTES", DEFAULT_STALE_RUN_MINUTES))
        stale_run_minutes, _ = get_config("llm","CVICHE_STALE_RUN_MINUTES",default=DEFAULT_STALE_RUN_MINUTES)
        minutes = int(stale_run_minutes)
    except (TypeError, ValueError):
        minutes = DEFAULT_STALE_RUN_MINUTES

    cutoff = datetime.now() - timedelta(minutes=minutes)
    stale_runs = (
        db.query(Run)
        .filter(Run.status == "running", Run.started_at < cutoff)
        .all()
    )
    if not stale_runs:
        return 0

    now = datetime.now()
    failed_count = 0
    resumed_count = 0
    for run in stale_runs:
        last_error_type, start_step_number = _resume_info_for_run(run, db)

        # A stale "running" run is, by definition, an interruption (the in-process
        # task died with the pod). Flag-gated: eligible_for_resume is always False
        # while CVICHE_AUTO_RETRY_ENABLED is unset, so the failed path below is the
        # only path taken by default.
        if auto_retry.eligible_for_resume(
            run, last_error_type=last_error_type, is_interruption=True
        ):
            _schedule_auto_retry(run, db, start_step_number)
            resumed_count += 1
            continue

        _mark_run_failed(run, db, now)
        failed_count += 1

    db.commit()
    if resumed_count:
        logger.info(
            "Reconciled %d stale run(s) older than %d min at startup "
            "(%d failed, %d auto-retried)",
            len(stale_runs), minutes, failed_count, resumed_count,
        )
    else:
        logger.info(
            "Reconciled %d stale run(s) older than %d min at startup",
            failed_count, minutes,
        )
    return failed_count


def queue_db_view(db: Session) -> dict[str, int | float | None]:
    """DB side of the run-queue stats (#701): how many runs are queued/running
    and the age of the oldest of each.
    """
    from sqlalchemy import func

    # started_at is re-stamped at the queued flip and again at the worker's
    # claim, so it is the age since the row entered its current status.
    now = datetime.now()
    view: dict[str, int | float | None] = {}
    for status in ("queued", "running"):
        count, oldest = db.query(func.count(Run.id), func.min(Run.started_at)).filter(
            Run.status == status
        ).one()
        view[status] = count
        view[f"oldest_{status}_age_s"] = (now - oldest).total_seconds() if oldest else None
    return view


def reap_orphaned_created_runs(
    db: Session,
    *,
    older_than_hours: int | None = None,
    dry_run: bool = False,
) -> dict:
    """Delete runs stuck at status="created" plus their child rows and storage.

    A run leaves "created" only when /start succeeds (-> "running"), and a
    restart mints a NEW run id (runs.py), so a row still at "created" provably
    never advanced -- it is an upload whose run was never started: a declined
    WCM-template upload, a failed/abandoned start, or (pre-#171) a swallowed
    storage error. These are hidden from the Runs dashboard (status != "created"
    filter) but their S3 objects -- runs/{id}/input/ and the by-submitter index
    -- linger in the store. PR #171 stops NEW such orphans; this reaps the
    backlog.

    NOTE: started_at is set at UPLOAD time, not at start (upload.py), so it is a
    reliable *age* gauge for a created row but is NOT a "was started" signal --
    "never started" is established by status == "created" alone.

    Deletes child rows explicitly because the run_id foreign keys are declared
    without ON DELETE CASCADE, so a bare Run delete would fail on MySQL (or
    orphan children on SQLite). Storage cleanup is best-effort and idempotent so
    an S3 hiccup never blocks the DB cleanup and a re-run is safe.

    Args:
        older_than_hours: Age threshold; defaults to CVICHE_ORPHAN_REAP_HOURS
            (24h) when None.
        dry_run: When True, return the candidates without deleting anything.

    Returns:
        {"candidates", "reaped", "objects_deleted", "run_ids", "dry_run"}.
    """
    if older_than_hours is None:
        try:
            hours_cfg, _ = get_config("llm", "CVICHE_ORPHAN_REAP_HOURS",
                                      default=DEFAULT_ORPHAN_REAP_HOURS)
            older_than_hours = int(hours_cfg)
        except (TypeError, ValueError):
            older_than_hours = DEFAULT_ORPHAN_REAP_HOURS

    cutoff = datetime.now() - timedelta(hours=older_than_hours)
    orphans = (
        db.query(Run)
        .filter(Run.status == "created", Run.started_at < cutoff)
        .all()
    )

    # Resolve the submitter email now (Run.user is lazy="raise_on_sql"), before
    # the rows are deleted, so we can also remove the by-submitter index entry.
    targets = []  # list of (run_id, submitter_email_or_None)
    for run in orphans:
        email = None
        if run.user_id is not None:
            u = db.query(User).filter(User.id == run.user_id).first()
            if u and u.email:
                email = u.email.lower()
        targets.append((run.id, email))

    result = {
        "candidates": len(targets),
        "reaped": 0,
        "objects_deleted": 0,
        "run_ids": [rid for rid, _ in targets],
        "dry_run": dry_run,
    }
    if dry_run or not targets:
        return result

    storage = get_storage()
    for run_id, email in targets:
        try:
            # Children first -- bare FKs (no ON DELETE CASCADE).
            db.query(Step).filter(Step.run_id == run_id).delete(synchronize_session=False)
            db.query(Log).filter(Log.run_id == run_id).delete(synchronize_session=False)
            db.query(LLMUsage).filter(LLMUsage.run_id == run_id).delete(synchronize_session=False)
            db.query(Feedback).filter(Feedback.run_id == run_id).delete(synchronize_session=False)
            db.query(RunMetrics).filter(RunMetrics.run_id == run_id).delete(synchronize_session=False)
            db.query(Run).filter(Run.id == run_id).delete(synchronize_session=False)
            db.commit()
        except Exception as e:
            db.rollback()
            logger.warning("Failed to reap orphan run %s from DB: %s", run_id, e)
            continue
        result["reaped"] += 1

        # Best-effort, idempotent storage cleanup -- never block on the store.
        try:
            result["objects_deleted"] += storage.delete_run(run_id)
        except Exception as e:
            logger.warning("Failed to delete storage for reaped run %s: %s", run_id, e)
        if email is not None:
            try:
                result["objects_deleted"] += storage.delete_global_prefix(
                    f"by-submitter/{email}/{run_id}/"
                )
            except Exception as e:
                logger.warning("Failed to delete by-submitter index for run %s: %s", run_id, e)

    logger.info(
        "Reaped %d/%d orphaned 'created' run(s) older than %dh (%d storage objects removed)",
        result["reaped"], result["candidates"], older_than_hours, result["objects_deleted"],
    )
    return result


def _transition_run_for_retry(run: Run, db: Session, start_step_number: int) -> None:
    """Pure DB-state transition that arms a stale run for a resume launch.

    Mirrors the step/run reset that runs.py:retry_step performs for a manual
    retry: bump the attempt counter, reset the failed/running step and every
    step at-or-after ``start_step_number`` back to "pending" (clearing the stale
    per-step metadata so the re-run repopulates it), and put the run back into
    "running" with its terminal fields cleared. No thread, no pipeline, no
    concurrency slot -- kept separate from the launch so it is unit-testable
    without actually running the pipeline.
    """
    run.attempt_count = (run.attempt_count or 1) + 1
    run.status = "running"
    run.error_message = None
    run.completed_at = None

    downstream_steps = (
        db.query(Step)
        .filter(Step.run_id == run.id, Step.step_number >= start_step_number)
        .all()
    )
    for step in downstream_steps:
        step.status = "pending"
        step.error_message = None
        step.error_type = None
        step.started_at = None
        step.completed_at = None
        step.duration_seconds = None
        step.cost = None

    db.commit()


def _launch_resume(run_id: str, file_path, start_step_number: int) -> bool:
    """Replicate runs.py's threaded background launch to resume ``run_id`` from
    ``start_step_number``. Returns True if a background task was scheduled.

    Mirrors start_run / retry_step exactly: acquire a per-pod concurrency slot
    first (so a resume can't blow past the pod's run cap); if none is free, log
    and return False WITHOUT launching (the caller then leaves the run for a
    later reaper pass / manual retry). The slot is released in the task's
    ``finally`` once the resumed pipeline finishes.

    The WHOLE body is wrapped in try/except so a malformed run, a missing
    import, or any unexpected error can NEVER crash the startup reaper -- on any
    failure we return False and the caller falls back to marking the run failed.
    """
    try:
        # Imported lazily to avoid a circular import (app.api.runs imports this
        # module) and to keep the reaper importable without the API package.
        from app.pipeline import concurrency
        from app.pipeline.orchestrator import PipelineOrchestrator

        if not concurrency.try_acquire_slot():
            logger.warning(
                "Auto-retry for run %s deferred: pod at concurrency capacity "
                "(%d active). Will be retried on a later reaper pass.",
                run_id, concurrency.active_count(),
            )
            return False

        def run_pipeline():
            from app.database import SessionLocal
            bg_db = SessionLocal()
            try:
                orchestrator = PipelineOrchestrator(run_id, file_path, bg_db)
                import asyncio
                asyncio.run(orchestrator.execute(start_step_number=start_step_number))
            finally:
                bg_db.close()
                concurrency.release_slot()

        import threading
        threading.Thread(target=run_pipeline, daemon=True).start()
        logger.info(
            "Auto-retry launched for run %s, resuming from step %d",
            run_id, start_step_number,
        )
        return True
    except Exception:
        logger.exception("Auto-retry launch failed for run %s", run_id)
        return False


def _schedule_auto_retry(run: Run, db: Session, start_step_number: int) -> None:
    """Compose the DB transition + background launch for a bounded auto-retry.

    FLAG-GATED (issue #145): only reached when ``auto_retry.eligible_for_resume``
    returned True, which requires ``CVICHE_AUTO_RETRY_ENABLED`` to be set. By
    default this is never called and behaviour is unchanged.

    Sequence:
      1. Resolve the original upload (re-materialising from durable storage if
         the pod-local copy is gone, exactly as start/retry do).
      2. ``_transition_run_for_retry`` -- the pure, testable DB state change.
      3. ``_launch_resume`` -- the threaded pipeline launch. If it can't launch
         (no slot, or any error), fall back to marking the run failed so a
         stale run is never silently left "running" with no executor.

    NOTE: activation is intentionally UNVERIFIED end-to-end here -- it depends on
    the periodic reaper of #116. This is the startup reaper, which runs once and
    need not sleep, so the backoff (auto_retry.auto_retry_backoff_seconds()) is
    a no-op here. TODO(#116): in the periodic-reaper context, honour the backoff
    (delay the relaunch by auto_retry_backoff_seconds()) so a persistently
    failing run cannot tight-loop.
    """
    # Resolve the original upload path the same way runs.py start/retry do. Done
    # via lazy import to avoid a circular import with app.api.runs at module load.
    try:
        from app.api.runs import _materialize_input_if_missing, UPLOAD_DIR

        file_path = UPLOAD_DIR / f"{run.id}.{run.file_type}"
        _materialize_input_if_missing(run.id, run.file_type, file_path)
    except Exception:
        logger.exception(
            "Auto-retry could not resolve input for run %s; marking failed", run.id
        )
        _mark_run_failed(run, db, datetime.now())
        return

    _transition_run_for_retry(run, db, start_step_number)

    if not _launch_resume(run.id, file_path, start_step_number):
        # Launch declined (no slot) or errored -- don't leave the run pinned at
        # "running" with nothing executing it. Fall back to the failed path.
        _mark_run_failed(run, db, datetime.now())
        db.commit()


def check_run_access(run_id: str, current_user: User, db: Session, *, eager=()) -> Run:
    """Verify run exists and user has access. Returns the Run.

    Pass *eager* a sequence of SQLAlchemy loader options (e.g.
    ``(selectinload(Run.steps),)``) to eager-load relationships in the same
    access query. This is required before touching any relationship attribute,
    since they are declared ``lazy="raise_on_sql"``.

    Raises:
        HTTPException 404 if run not found.
        HTTPException 403 if user does not own the run and is not admin.
    """
    query = db.query(Run).filter(Run.id == run_id)
    for option in eager:
        query = query.options(option)
    run = query.first()
    if not run:
        raise not_found("Run not found")
    if current_user.role != "admin" and run.user_id != current_user.id:
        raise forbidden("Access denied")
    return run
