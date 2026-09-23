"""Run-related service functions."""
import logging
import math
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import Run, RunState, Step, User, Log, LLMUsage, Feedback, RunMetrics
from app.errors import not_found, forbidden
from app.config_loader import get_config
from app.pipeline import concurrency, run_queue
from app.storage import get_storage
from app.services import auto_retry

logger = logging.getLogger(__name__)

# A run older than this while still marked "running" at startup is treated as
# orphaned by a server restart. Generous relative to the ~15-20 min a real run
# takes, so a sibling replica's genuinely in-flight run is never swept.
DEFAULT_STALE_RUN_MINUTES = 60

# In queue mode (#701), the stale-running reaper's threshold is floored at the
# worker's own run watchdog timeout plus this margin -- see
# _effective_stale_run_minutes. Matches the margin k8s/base/worker/deployment.yaml
# adds to terminationGracePeriodSeconds over the same RUN_TIMEOUT_S, so both
# the pod-level and DB-level backstops give the watchdog the same head start.
QUEUE_MODE_STALE_RUN_MARGIN_MINUTES = 5

# Mirrors run_queue.RUN_TIMEOUT_S_DEFAULT (N2), the single default this value,
# app.worker.RUN_TIMEOUT_S and run_queue.MIN_IDLE_MS all trace back to. The
# literal is still duplicated as a plain int (rather than reading
# run_queue.RUN_TIMEOUT_S_DEFAULT directly) only so _effective_stale_run_minutes
# below keeps reading CVICHE_RUN_TIMEOUT_SECONDS live on every call -- a config
# reload must be reflected immediately, and run_queue's own RUN_TIMEOUT_S is
# fixed once at import time (module-load-time config reads elsewhere in this
# file, and in run_queue.py, all share that same one-time-read contract).
DEFAULT_RUN_TIMEOUT_SECONDS = run_queue.RUN_TIMEOUT_S_DEFAULT

# A run still "queued" this long past its flip (#701) has almost certainly
# lost its Valkey token (the enqueue/reconciler crash windows the #895 review
# found) rather than genuinely waiting behind a deep backlog. See
# reconcile_queued_runs.
DEFAULT_QUEUED_RECONCILE_MINUTES = 5

# A run still at status="created" this many hours after upload was never started
# (or its start failed / was abandoned) and is safe to reap along with its
# storage. Generous so a just-uploaded run that is about to be started is never
# swept.
DEFAULT_ORPHAN_REAP_HOURS = 24

# Where an upload's pod-local copy lives. Owned here (services/), not
# app/api/upload.py, so the worker process (#701) can resolve a run's input
# file without importing anything under app.api -- app.api.runs pulls in
# fastapi, app.auth and app.rate_limiter, none of which a queue worker should
# ever need. app.api.upload imports this constant rather than defining its
# own (CODING STANDARDS section 1.5: one definition of a shared path).
UPLOAD_DIR = Path(__file__).parent.parent.parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def _materialize_input_if_missing(run_id: str, file_type: str, dest: Path) -> None:
    """Re-fetch a run's original upload from durable storage if the pod-local
    copy is gone (e.g. after a pod recycle), so start/restart/retry survive.
    No-op if the local file already exists or storage has no copy -- the caller
    keeps its own missing-file handling.
    """
    if dest.exists():
        return
    try:
        data = get_storage().get_file(run_id, f"input/{run_id}.{file_type}")
    except FileNotFoundError as e:
        # The run genuinely has no durable copy (e.g. a legacy run predating the
        # S3 archive). Expected; the caller keeps its own missing-file handling.
        logger.info("No durable input copy for run %s (%s); using local only", run_id, e)
        return
    except Exception as e:
        # Anything other than a missing object (S3 AccessDenied, KMS, network)
        # means durable storage is reachable-but-failing. Surface it at WARNING
        # so a real outage isn't silently misread as "file simply not there".
        logger.warning(
            "Durable input lookup FAILED for run %s (%s); using local copy only. "
            "May indicate an S3/IAM/KMS problem rather than a missing object.",
            run_id, e,
        )
        return
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        logger.info("Re-materialized input for run %s from storage (%d bytes)", run_id, len(data))
    except Exception as e:
        logger.warning("Failed to write re-materialized input for run %s: %s", run_id, e)


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


def _effective_stale_run_minutes() -> int:
    """CVICHE_STALE_RUN_MINUTES, floored in queue mode at the run watchdog's
    own timeout plus margin.

    In queue mode a run's Valkey token stays un-ACKed for the run's whole
    duration (Paul's ACK-semantics decision, #701 worker#3) -- the worker's own
    watchdog (``app.worker.RUN_TIMEOUT_S``) is what fails a run that outlives
    its bound. Without this floor, a ``CVICHE_STALE_RUN_MINUTES`` below that
    timeout would let this DB-side reaper mark a run failed while its worker is
    still legitimately executing it and would go on to succeed. Outside queue
    mode the configured value is used as-is, unchanged from before #701.
    """
    try:
        stale_run_minutes, _ = get_config("llm", "CVICHE_STALE_RUN_MINUTES", default=DEFAULT_STALE_RUN_MINUTES)
        minutes = int(stale_run_minutes)
    except (TypeError, ValueError):
        minutes = DEFAULT_STALE_RUN_MINUTES

    if concurrency.dispatch_mode() != "queue":
        return minutes

    try:
        run_timeout_s_cfg, _ = get_config(
            "llm", "CVICHE_RUN_TIMEOUT_SECONDS", default=DEFAULT_RUN_TIMEOUT_SECONDS
        )
        run_timeout_s = int(run_timeout_s_cfg)
    except (TypeError, ValueError):
        run_timeout_s = DEFAULT_RUN_TIMEOUT_SECONDS
    watchdog_floor_minutes = math.ceil(run_timeout_s / 60) + QUEUE_MODE_STALE_RUN_MARGIN_MINUTES
    return max(minutes, watchdog_floor_minutes)


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

    In queue mode the threshold is floored well above the run watchdog's own
    timeout -- see ``_effective_stale_run_minutes`` -- so this reaper backstops
    a run whose worker died without tripping its watchdog, rather than racing
    a run that is still legitimately executing.
    """
    minutes = _effective_stale_run_minutes()
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


def _queued_reconcile_minutes() -> int:
    try:
        minutes_cfg, _ = get_config(
            "llm", "CVICHE_QUEUED_RECONCILE_MINUTES", default=DEFAULT_QUEUED_RECONCILE_MINUTES
        )
        return int(minutes_cfg)
    except (TypeError, ValueError):
        return DEFAULT_QUEUED_RECONCILE_MINUTES


def reconcile_queued_runs(db: Session) -> int:
    """Requeue runs stranded at status="queued" (#701 queue mode only).

    A no-op outside queue mode -- safe to call unconditionally from every
    reaper call site (the periodic loop and the startup sweep), the same way
    ``reconcile_stale_runs`` is age-based and idempotent so multiple replicas
    calling it on an interval never step on each other.

    Covers the crash windows the #895 review found where a "queued" row's
    Valkey token never reaches a worker: a producer dying between
    ``flip_to_queued``'s commit and ``run_queue.enqueue`` (runs.py point 9), an
    entry trimmed while pending (run_queue point 3), or any other silent
    token loss. A row whose id IS in ``run_queue.live_run_ids()`` (undelivered
    or pending, not yet ACKed) is left alone -- it has a live token and a
    legitimately deep backlog must not be re-enqueued out from under itself.

    Re-enqueuing a row that in fact still has a live token would be a harmless
    duplicate (the worker's claim is conditional), but this still goes through
    ``run_queue.claim_reenqueue_slot`` -- the same guard the already-queued
    ``/start`` path uses -- so a backlog of many stranded rows, or repeated
    sweeps, adds at most one fresh token per run per ``REENQUEUE_GUARD_TTL_S``.
    A guard miss means another sweep or an operator's own ``/start`` already
    requeued this run within that window, so it is skipped rather than
    duplicated.

    Returns the number of rows actually requeued.
    """
    if concurrency.dispatch_mode() != "queue":
        return 0

    minutes = _queued_reconcile_minutes()
    cutoff = datetime.now() - timedelta(minutes=minutes)
    stranded = (
        db.query(Run)
        .filter(Run.status == RunState.QUEUED, Run.queued_at.isnot(None), Run.queued_at < cutoff)
        .all()
    )
    if not stranded:
        return 0

    live_ids = run_queue.live_run_ids()
    requeued = 0
    for run in stranded:
        if run.id in live_ids or not run_queue.claim_reenqueue_slot(run.id):
            continue
        run_queue.enqueue(run.id)
        requeued += 1
        logger.warning("requeued_stranded run_id=%s queued_at=%s", run.id, run.queued_at)

    if requeued:
        logger.info(
            "Queued-run reconciler requeued %d/%d stranded run(s) (queued_at older than %d min)",
            requeued, len(stranded), minutes,
        )
    return requeued


def queue_db_view(db: Session) -> dict[str, int | float | None]:
    """DB side of the run-queue stats (#701): how many runs are queued/running
    and the age of the oldest of each.

    Queued age is measured from ``queued_at``, not ``started_at``: since the
    routes rework, ``flip_to_queued`` stamps ``queued_at`` and leaves
    ``started_at`` alone, and ``claim_queued`` re-stamps ``started_at`` only
    once a worker actually claims the run (mrj4001 review, runs.py point 6 --
    ``started_at`` means "began executing"). Running age still comes from
    ``started_at``, which is exactly when a "running" row entered that status.
    """
    from sqlalchemy import func

    now = datetime.now()

    queued_count, oldest_queued = db.query(func.count(Run.id), func.min(Run.queued_at)).filter(
        Run.status == RunState.QUEUED
    ).one()
    running_count, oldest_running = db.query(func.count(Run.id), func.min(Run.started_at)).filter(
        Run.status == RunState.RUNNING
    ).one()

    return {
        "queued": queued_count,
        "oldest_queued_age_s": (now - oldest_queued).total_seconds() if oldest_queued else None,
        "running": running_count,
        "oldest_running_age_s": (now - oldest_running).total_seconds() if oldest_running else None,
    }


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
    # Resolve the original upload path the same way runs.py start/retry do.
    # Both now live in this module (#701), so no lazy/circular import is needed.
    try:
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


# ============================================================
# Queue-mode run transitions (#701)
#
# Named DB-state transitions shared by the queue producer (app/api/runs.py's
# _dispatch_queue), the queue worker (app/worker.py) and the queued-run
# reconciler, in place of each hand-writing its own conditional UPDATE with
# inline status literals (CODING STANDARDS section 1.5: one definition of a
# shared vocabulary). None of these commit a caller-supplied Session's
# unrelated pending changes for it -- claim_queued and mark_failed open and
# close their own short session (mirroring the pre-existing worker._claim /
# worker._mark_failed they replace), while flip_to_queued and revert_queued
# take the request's Session so they share its transaction with the route's
# other reads.
# ============================================================

@dataclass(frozen=True, slots=True)
class ClaimResult:
    """Outcome of claim_queued: whether this call won the conditional claim,
    and the row's state right after -- populated whether the claim was won or
    lost, so a losing caller can still log what it saw."""
    won: bool
    status: str | None
    file_type: str | None
    resume_from_step: int | None


@dataclass(frozen=True, slots=True)
class StepSnapshot:
    """One Step row's state captured just before a queue-mode retry_step
    reset it to "pending" (B3): the exact fields revert_queued needs to
    restore it if the follow-up XADD then fails, so the run comes back
    retryable -- an "error" step, not one stranded "pending" with no
    executor and rejected by retry_step's own ``status != "error"`` guard."""
    step_id: int
    status: str
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_seconds: int | None
    cost: float | None


@dataclass(frozen=True, slots=True)
class FlipResult:
    """Outcome of flip_to_queued: whether the flip applied, plus the exact
    pre-flip values (read under the same row lock as the flip) a caller needs
    to undo it with revert_queued if the follow-up XADD then fails.
    ``step_snapshots`` is populated only when the caller passed
    ``flip_to_queued`` an ``on_flip`` and the flip actually won (B3)."""
    flipped: bool
    prior_status: str
    prior_started_at: datetime | None
    prior_error_message: str | None
    prior_completed_at: datetime | None
    prior_queued_at: datetime | None
    step_snapshots: tuple[StepSnapshot, ...] = ()


def claim_queued(run_id: str) -> ClaimResult:
    """The one conditional UPDATE that decides a queue worker's ownership of a
    run: queued -> running. Own short session, committed at once so no lock
    spans the run itself -- mirrors the worker's pre-existing ``_claim``,
    moved here so the worker no longer needs its own DB-transition code next
    to the reconciler's and the routes' (section 1.5).

    Returns ``resume_from_step`` off the row as it stands right after the
    attempt, so the caller resumes from the DB's recorded step rather than
    from anything carried on the Valkey token itself: the token is a pure
    wake-up now, so a redelivered or stale one can no longer replay an old
    start_step (mrj4001 review, runs.py point 3).
    """
    db = SessionLocal()
    try:
        won = db.execute(
            update(Run)
            .where(Run.id == run_id, Run.status == RunState.QUEUED)
            .values(status=RunState.RUNNING, started_at=datetime.now(),
                    error_message=None, completed_at=None)
        ).rowcount == 1
        db.commit()
        row = db.execute(
            select(Run.status, Run.file_type, Run.resume_from_step).where(Run.id == run_id)
        ).one_or_none()
        return ClaimResult(
            won=won,
            status=row[0] if row else None,
            file_type=row[1] if row else None,
            resume_from_step=row[2] if row else None,
        )
    finally:
        db.close()


def mark_failed(run_id: str, message: str, *, from_statuses: tuple[str, ...]) -> int:
    """Named failed-transition for the queue worker (dead-letter, run
    watchdog) and the queued-run reconciler, replacing worker.py's own
    hand-written UPDATE (section 1.5). Own short session, matching
    claim_queued.

    Also errors out any Step rows still "running" for this run -- the same
    cleanup ``_mark_run_failed`` (the startup reaper's ORM path, above) does
    for an orphaned run -- so a dead-lettered or watchdog-killed run never
    leaves a phantom "running" step in the UI. Returns the Run UPDATE's
    rowcount, so a caller can tell a lost race (0: something else already
    moved the row) from a real transition (1).
    """
    db = SessionLocal()
    try:
        now = datetime.now()
        rowcount = db.execute(
            update(Run)
            .where(Run.id == run_id, Run.status.in_(from_statuses))
            .values(status=RunState.FAILED, error_message=message, completed_at=now)
        ).rowcount
        if rowcount:
            db.execute(
                update(Step)
                .where(Step.run_id == run_id, Step.status == "running")
                .values(status="error", completed_at=now)
            )
        db.commit()
        return rowcount
    finally:
        db.close()


def flip_to_queued(
    db: Session, run_id: str, allowed_from: tuple[str, ...], *, resume_from_step: int | None = None,
    on_flip: Callable[[], tuple[StepSnapshot, ...]] | None = None,
) -> FlipResult:
    """Conditionally flip a run to "queued". Reads the pre-flip state under a
    row lock in the SAME transaction as the flip (``SELECT ... FOR UPDATE``),
    not from an ORM row loaded earlier in the request -- a read from earlier
    can describe a row the UPDATE no longer matches if it changed in between
    (mrj4001 review, runs.py point 4). No ``RETURNING``: MariaDB's UPDATE does
    not support it (probed against the mysql/mariadb SQLAlchemy dialects --
    ``update_returning`` is False for both), so the locked read is a separate
    statement rather than ``.returning()``.

    ``resume_from_step`` is written unconditionally, including ``None`` --
    retry_step passes the step to resume from; start_run passes ``None`` so a
    fresh start always clears a stale resume point left by an earlier retry.

    Already-"queued" is reported as ``flipped=False`` without writing the row
    -- the caller's re-enqueue path (``run_queue.claim_reenqueue_slot``)
    handles that case; this never issues a second flip for it.

    ``on_flip``, if given, runs -- and is committed -- in this SAME
    transaction, only once the flip has actually won (B3): retry_step uses it
    to reset the downstream Step rows to "pending" and passes back their
    prior state as ``StepSnapshot``s, attached to the returned ``FlipResult``
    so ``revert_queued`` can restore them too if the caller's own follow-up
    (the queue XADD) then fails. Committing the flip and the step reset
    separately, as an earlier version of this did, left a real window open: a
    woken worker could claim the row between the two commits and execute
    against still-stale step state.
    """
    prior_status, prior_started_at, prior_error_message, prior_completed_at, prior_queued_at = db.execute(
        select(Run.status, Run.started_at, Run.error_message, Run.completed_at, Run.queued_at)
        .where(Run.id == run_id)
        .with_for_update()
    ).one()
    if prior_status == RunState.QUEUED:
        db.commit()  # release the row lock; nothing to flip
        return FlipResult(False, prior_status, prior_started_at, prior_error_message,
                           prior_completed_at, prior_queued_at)
    rowcount = db.execute(
        update(Run)
        .where(Run.id == run_id, Run.status.in_(allowed_from))
        .values(status=RunState.QUEUED, queued_at=datetime.now(), error_message=None,
                completed_at=None, resume_from_step=resume_from_step)
    ).rowcount
    step_snapshots: tuple[StepSnapshot, ...] = ()
    if rowcount == 1 and on_flip is not None:
        step_snapshots = on_flip()
    db.commit()
    return FlipResult(rowcount == 1, prior_status, prior_started_at, prior_error_message,
                       prior_completed_at, prior_queued_at, step_snapshots=step_snapshots)


def revert_queued(db: Session, run_id: str, prior: FlipResult) -> bool:
    """Undo a flip_to_queued when the follow-up XADD then failed. Guarded
    ``WHERE status='queued'`` (mrj4001 review, runs.py point 1) so a revert
    can never clobber a claim or cancel that landed between the flip and this
    call: if the worker already won queued->running, or the run was
    cancelled, this is a no-op and the caller should report the run's live
    status instead of a stale "reverted to prior" one.

    Also restores any ``prior.step_snapshots`` (B3) -- the downstream Step
    rows a queue-mode retry_step reset to "pending" before the flip -- to
    their pre-reset state, in the same transaction as the Run revert. Without
    this a failed enqueue left the run back at its prior (retryable-looking)
    status but its failed step stuck "pending": no executor is coming for it,
    and retry_step's own ``status != "error"`` guard then rejects a second
    retry outright.

    Returns True if the revert actually applied.
    """
    rowcount = db.execute(
        update(Run)
        .where(Run.id == run_id, Run.status == RunState.QUEUED)
        .values(status=prior.prior_status, started_at=prior.prior_started_at,
                error_message=prior.prior_error_message, completed_at=prior.prior_completed_at,
                queued_at=prior.prior_queued_at)
    ).rowcount
    if rowcount == 1:
        for snap in prior.step_snapshots:
            db.execute(
                update(Step)
                .where(Step.id == snap.step_id)
                .values(status=snap.status, error_message=snap.error_message,
                        started_at=snap.started_at, completed_at=snap.completed_at,
                        duration_seconds=snap.duration_seconds, cost=snap.cost)
            )
    db.commit()
    return rowcount == 1
