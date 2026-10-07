"""Run status and management API endpoints."""
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import redis
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Query as SAQuery
from sqlalchemy.orm import Session, selectinload

from app.api.upload import UPLOAD_DIR, commit_run_or_compensate, create_run_archive
from app.auth import (
    can_view_all_runs,
    get_current_user,
    require_view_all_runs,
    visible_cost,
)
from app.database import get_db
from app.errors import bad_request, conflict, forbidden, not_found
from app.models import Run, RunState, Step, User
from app.pipeline import concurrency, run_queue
from app.pipeline.orchestrator import PipelineOrchestrator
from app.pipeline.step_registry import STEP_REGISTRY
from app.rate_limiter import check_rate_limit
from app.schemas import (
    CapacityResponse,
    PaginatedRuns,
    RestartRunResponse,
    RunActionResponse,
    RunFeedbackSummary,
    RunFilterOptions,
    RunQualityReport,
    RunReviewNote,
    RunStatus,
    RunSummary,
    StatusFilterCounts,
    StepSummary,
)
from app.services import batch_completion, batch_service, quality_score_service
from app.services.run_quality_report import (
    build_run_quality_report,
    columns_need_cleanup,
)
from app.services.run_service import (
    StepSnapshot,
    _materialize_input_if_missing,
    check_run_access,
    claim_run_as_running,
    flip_to_queued,
    revert_queued,
)
from app.services.runs_admin_query import (
    RunScope,
    StatusFilter,
    build_filter_options,
    empty_feedback_summary,
    feedback_clause,
    filtered_runs_query,
    load_feedback_summaries,
    my_status_counts,
    parse_feedback_filter,
    parse_run_filters,
    parse_status_filter,
    run_by_summary,
    status_clause,
)
from app.storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter()

# Run statuses /start may move to "running" (the conditional UPDATE's predicate).
STARTABLE_STATUSES = ("created", "paused")

# Largest page /runs serves. The run-history UI asks for exactly this many
# (RunHistory.tsx PAGE_SIZE), so lowering it breaks that page (#801).
MAX_RUNS_PAGE_SIZE = 100


def _run_duration_seconds(run) -> int | None:
    """Total pipeline time for a run, in whole seconds.

    Prefers the authoritative value persisted by the orchestrator at terminal
    status. Falls back to wall-clock (completed_at - started_at) for runs that
    predate the column, and to live elapsed time while a run is still running.
    """
    if run.total_duration_seconds is not None:
        return run.total_duration_seconds
    if run.started_at and run.completed_at:
        return int((run.completed_at - run.started_at).total_seconds())
    if run.started_at and run.status == "running":
        # Use datetime.now() to match server_default=func.now() (local time).
        return max(0, int((datetime.now() - run.started_at).total_seconds()))
    return None


def _dispatch_queue(
    run: Run, db: Session, *, allowed_from: tuple[str, ...], start_step: int | None = None,
    on_flip: Callable[[], tuple[StepSnapshot, ...]] | None = None,
) -> JSONResponse:
    """Queue-mode dispatch (#701): ``run_service.flip_to_queued`` reads the
    row's live status under a lock and conditionally flips it to ``queued``
    in one transaction (so two concurrent requests can't both flip), then
    this XADDs a wake-up token. Three outcomes once the flip returns:

    * Won the flip: XADD the token. A failed XADD (Valkey down) calls the
      guarded ``run_service.revert_queued`` (``WHERE status='queued'``), so a
      claim or cancel that landed in between can never be clobbered. If the
      revert itself matches no row -- the run was already claimed or
      cancelled -- this answers 202 with the run's live status rather than a
      misleading 503 "try again". A process death between the flip's own
      commit and this XADD leaves the row "queued" with no token at all;
      ``run_service.reconcile_queued_runs`` (the periodic reaper, queue mode
      only) is the backstop that re-enqueues it, and the already-queued
      branch below recovers it sooner if the user retries first.
    * Already queued (no flip attempted): idempotent re-enqueue, guarded by
      ``run_queue.claim_reenqueue_slot`` so a retry storm on one run adds at
      most one fresh token per ``REENQUEUE_GUARD_TTL_S`` -- a guard miss just
      answers 202 without another XADD.
    * Lost race (the row was neither "queued" nor in ``allowed_from`` by the
      time the locked read ran -- claimed, cancelled, or started elsewhere
      since the caller's own read): 409, since the client's view of the run
      was already stale.

    ``on_flip``, if given, is passed straight through to ``flip_to_queued``,
    which runs -- and commits -- it in the SAME transaction as the flip
    itself, only once a fresh flip has actually won (B3): retry_step uses it
    to reset the downstream Step rows and hand back their prior state as
    ``StepSnapshot``s, which ``revert_queued`` below restores if the XADD
    then fails. It must NOT run on a lost race, which ``flip_to_queued``
    itself guarantees; committing the reset here instead, in a second
    transaction after the flip's own, left a real window open where a woken
    worker could claim the row in between and execute against stale step
    state -- closed by moving the reset inside flip_to_queued's transaction.

    Checks ``run_queue.is_configured()`` before any of that (N3): unconfigured
    (no ``CVICHE_REDIS_URL``), ``run_queue.enqueue`` would raise a plain
    RuntimeError -- not a ``redis.exceptions.RedisError`` -- which escapes both
    ``except`` clauses below uncaught, after ``flip_to_queued`` had already
    committed the row to "queued" with no worker ever coming for it. Checking
    first means nothing is committed at all on a misconfigured deployment.
    """
    if not run_queue.is_configured():
        logger.error("Queue mode is enabled but CVICHE_REDIS_URL is not set; refusing to queue run %s", run.id)
        raise HTTPException(
            status_code=503,
            detail={"error": "queue_unavailable",
                    "message": "The run queue is unavailable right now -- please try again shortly."},
        )

    # A bulk batch run's token goes on the batch queue (#1114), on every branch below.
    queue = run_queue.queue_for(batch_service.batch_size(db, run.batch_id))
    prior = flip_to_queued(db, run.id, allowed_from, resume_from_step=start_step, on_flip=on_flip)

    if not prior.flipped and prior.prior_status != RunState.QUEUED:
        raise conflict(f"Cannot start run in status: {prior.prior_status}")

    if not prior.flipped:
        if not run_queue.claim_reenqueue_slot(run.id):
            return JSONResponse(
                status_code=202,
                content={"message": f"Run {run.id} already queued", "status": "queued"},
            )
        try:
            run_queue.enqueue(run.id, queue)
        except redis.exceptions.RedisError as e:
            logger.exception("Re-enqueue failed for already-queued run %s", run.id)
            raise HTTPException(
                status_code=503,
                detail={"error": "queue_unavailable",
                        "message": "The run queue is unavailable right now -- please try again shortly."},
            ) from e
        return JSONResponse(
            status_code=202,
            content={"message": f"Run {run.id} already queued", "status": "queued"},
        )

    try:
        run_queue.enqueue(run.id, queue)
    except redis.exceptions.RedisError as e:
        logger.exception("Enqueue failed for run %s; reverting the flip", run.id)
        if not revert_queued(db, run.id, prior):
            db.refresh(run)
            return JSONResponse(
                status_code=202,
                content={"message": f"Run {run.id} {run.status}", "status": run.status},
            )
        raise HTTPException(
            status_code=503,
            detail={"error": "queue_unavailable",
                    "message": "The run queue is unavailable right now -- please try again shortly."},
        ) from e
    return JSONResponse(status_code=202, content={"message": f"Run {run.id} queued", "status": "queued"})


@router.get("/capacity", response_model=CapacityResponse)
async def get_capacity(current_user: User = Depends(get_current_user)):
    """Read-only snapshot of this pod's run-admission capacity.

    Lets the upload UI warn before spending an upload when the pod is already at
    its concurrency cap, so a busy window stops minting `created` runs that can't
    start (issue #177). ADVISORY ONLY: it deliberately does not acquire a slot.
    Admission is per-pod and racy (see concurrency.py), so the authoritative gate
    stays the slot acquisition in start_run -- a check that passes here can still
    429 at start (the client retries that run in place), and a different pod may
    have a free slot. Never gate correctness on this value.
    """
    limit = concurrency.get_max_concurrent_runs()
    active = concurrency.active_count()
    return {
        "available": active < limit,
        "active": active,
        "limit": limit,
    }


@router.get("/runs/filter-options", response_model=RunFilterOptions)
def get_run_filter_options(
    scope: RunScope = Query(RunScope.ALL),
    run_by: str | None = Query(None),
    faculty: str | None = Query(None),
    department: str | None = Query(None),
    feedback: str | None = Query(None),
    input_format: str | None = Query(None),
    status: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(require_view_all_runs),
) -> RunFilterOptions:
    """Options and run counts for the runs filters (scope=all, admin or staff).

    Each facet's counts apply the other filters but not its own."""
    if scope != RunScope.ALL:
        raise bad_request("filter-options is only available with scope=all")
    return build_filter_options(
        db, parse_run_filters(run_by, faculty, department, feedback, input_format, status))


@router.get("/runs/my-status-counts", response_model=StatusFilterCounts)
def get_my_status_counts(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StatusFilterCounts:
    """The status pill counts over the caller's own runs (any role): never other users' runs."""
    return my_status_counts(db, current_user.id)


def _run_summary(current_user: User, run: Run, all_scope: bool,
                 feedback: RunFeedbackSummary) -> RunSummary:
    return RunSummary(
        run_id=run.id,
        filename=run.filename,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        total_cost=visible_cost(current_user, run.total_cost),
        total_duration_seconds=_run_duration_seconds(run),
        cv_owner_name=run.cv_owner_name,
        submission_type=run.submission_type,
        run_by=run_by_summary(run.user) if all_scope else None,
        # Score columns need scope=all (admin or staff): never on scope=mine.
        quality_score=run.quality_score if all_scope else None,
        quality_band=run.quality_band if all_scope else None,
        quality_cap=run.quality_cap if all_scope else None,
        feedback=feedback,
        batch_id=run.batch_id,
    )


def _my_runs_query(db: Session, current_user: User, feedback: str | None,
                   status: str | None) -> SAQuery:
    """scope=mine: the caller's own runs, narrowed by ``feedback`` and ``status``.
    status=red is the admin score filter: 403 here."""
    query = db.query(Run).filter(Run.user_id == current_user.id)
    status_filter = parse_status_filter(status)
    if status_filter is StatusFilter.RED:
        raise forbidden("The red-score filter is admin only")
    for clause in (feedback_clause(parse_feedback_filter(feedback)),
                   status_clause(status_filter)):
        if clause is not None:
            query = query.filter(clause)
    return query


@router.get("/runs", response_model=PaginatedRuns)
async def list_runs(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=MAX_RUNS_PAGE_SIZE),
    scope: RunScope = Query(RunScope.MINE),
    run_by: str | None = Query(None),
    faculty: str | None = Query(None),
    department: str | None = Query(None),
    feedback: str | None = Query(None),
    input_format: str | None = Query(None),
    status: str | None = Query(None),
    batch_id: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List pipeline runs, most recent first.

    scope=mine (default): the current user's runs. scope=all (admin or staff): every
    user's runs, optionally filtered by run_by (user id, "self" or "on_behalf"), faculty (the
    CV owner's name) and department (the running user's ED department); those
    three, and ``input_format`` ("wcm" = written in the WCM CV template, "other",
    "unknown" = not classified), are ignored under scope=mine. ``feedback`` ("given" = any reviewer left
    feedback, "needed" = complete with none) and ``status`` ("running" = queued or
    running, "failed" = failed; "red" = score band RED, scope=all only: 403 under scope=mine) apply in both
    scopes. Every run
    carries a ``feedback`` summary; scope=all adds the reviewer list.
    ``batch_id`` (#1114) narrows either scope to one batch's runs; every row
    carries its ``batch_id`` so the list can tag batch runs.
    """
    all_scope = scope == RunScope.ALL
    if all_scope:
        require_view_all_runs(current_user)
        query = filtered_runs_query(
            db, parse_run_filters(run_by, faculty, department, feedback, input_format, status))
    else:
        query = _my_runs_query(db, current_user, feedback, status)
    if batch_id:
        query = query.filter(Run.batch_id == batch_id)
    total = query.count()
    runs = query.order_by(Run.started_at.desc()).offset(offset).limit(limit).all()

    summaries = load_feedback_summaries(
        db, [run.id for run in runs], current_user.id, with_reviewers=all_scope)
    no_feedback = empty_feedback_summary(with_reviewers=all_scope)
    results = [
        _run_summary(current_user, run, all_scope, summaries.get(run.id, no_feedback))
        for run in runs
    ]

    return PaginatedRuns(
        runs=results,
        total=total,
        has_more=(offset + limit) < total,
        offset=offset,
        limit=limit,
    )


@router.get("/run/{run_id}/status", response_model=RunStatus)
async def get_run_status(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the current status of a pipeline run."""

    # Eager-load the run's steps in the access query (Run.steps is
    # lazy="raise_on_sql", so it must be loaded explicitly before access).
    run = check_run_access(
        run_id, current_user, db, eager=(selectinload(Run.steps), selectinload(Run.user)),
        read_only=True,
    )

    step_summaries = [
        StepSummary(
            step_number=step.step_number,
            stage_id=step.stage_id,
            step_name=step.step_name,
            status=step.status,
            started_at=step.started_at,
            completed_at=step.completed_at,
            duration_seconds=step.duration_seconds,
            cost=visible_cost(current_user, step.cost),
            output_files=step.output_files
        )
        for step in sorted(run.steps, key=lambda s: s.step_number)
    ]

    # Prefer the persisted pipeline duration; fall back to wall-clock / live elapsed.
    total_duration_seconds = _run_duration_seconds(run)

    return RunStatus(
        run_id=run.id,
        filename=run.filename,
        file_type=run.file_type,
        status=run.status,
        started_at=run.started_at,
        completed_at=run.completed_at,
        total_cost=visible_cost(current_user, run.total_cost),
        total_tokens=run.total_tokens or 0,
        input_tokens=run.input_tokens or 0,
        output_tokens=run.output_tokens or 0,
        total_duration_seconds=total_duration_seconds,
        estimated_duration_seconds=run.estimated_duration_seconds,
        error_message=run.error_message,
        cv_owner_name=run.cv_owner_name,
        run_by=run_by_summary(run.user) if can_view_all_runs(current_user) else None,
        scanned_pages=run.scanned_page_numbers,
        steps=step_summaries
    )


@router.post("/run/{run_id}/start", response_model=RunActionResponse)
def start_run(
    run_id: str,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Start executing a pipeline run.

    Plain ``def``, not ``async def`` (#701 run_queue.py point 11 /
    runs.py point 10): the body has no ``await`` and, in queue mode, calls
    the synchronous Valkey XADD. FastAPI runs a plain ``def`` route in its
    threadpool, so a Valkey brownout costs a threadpool slot instead of
    stalling the event loop for every other request on this pod.
    """

    run = check_run_access(run_id, current_user, db)

    queue_mode = concurrency.dispatch_mode() == "queue"
    # "paused" rows predate the pause endpoint's removal (#115); /start stays their recovery path.
    # In queue mode an already-"queued" run is also accepted: _dispatch_queue answers it with an
    # idempotent re-enqueue (#701).
    if run.status not in STARTABLE_STATUSES and not (queue_mode and run.status == RunState.QUEUED):
        raise bad_request(f"Cannot start run in status: {run.status}")

    # Get the uploaded file path. Use the shared UPLOAD_DIR constant (same path
    # restart/retry use) so the three handlers can never resolve it differently.
    file_path = UPLOAD_DIR / f"{run_id}.{run.file_type}"

    _materialize_input_if_missing(run_id, run.file_type, file_path)
    if not file_path.exists():
        # Friendly, actionable wording (matches retry/restart). Hit when neither
        # the pod-local copy nor a durable S3 archive exists -- e.g. a legacy run
        # predating the archive, whose input cannot be recovered.
        raise not_found("Uploaded file no longer available — please upload again.")

    if queue_mode:
        return _dispatch_queue(run, db, allowed_from=STARTABLE_STATUSES)

    # Admission control: cap concurrent in-process pipelines per pod. Acquire a
    # slot before marking the run "running" so a rejected start leaves the run
    # in its prior state, retryable once a slot frees.
    if not concurrency.try_acquire_slot(run_id):
        raise HTTPException(
            status_code=429,
            detail={
                "error": "server_busy",
                # Keep the user-facing text free of a bare per-pod number: the
                # cap is per server instance, but a user's own run list (shown in
                # the side panel) is cross-pod and user-wide, so "maximum of N"
                # reads as wrong when they can see more than N of their own runs
                # in flight. The exact limit/active counts stay in `details` for
                # diagnostics. (issue #170)
                "message": (
                    "The system is temporarily at capacity and can't start a new "
                    "run right now. Any runs already in progress will keep going -- "
                    "please wait a moment and try again."
                ),
                "details": {
                    "limit_type": "concurrency",
                    "limit": concurrency.get_max_concurrent_runs(),
                    "active": concurrency.active_count(),
                },
            },
            headers={"Retry-After": "30"},
        )

    # Atomic created/paused -> running (#799). The guard above is only a fast
    # path: the conditional UPDATE decides which concurrent starter wins. The
    # loser returns its slot and never reaches the pipeline dispatch.
    if not claim_run_as_running(db, run_id, Run.status.in_(STARTABLE_STATUSES)):
        db.rollback()
        concurrency.release_slot(run_id)
        raise conflict("This run was already started by another request.")
    db.commit()

    # Start pipeline execution in background. The slot acquired above is held
    # for the lifetime of the run and released when the task finishes.
    def run_pipeline():
        # Create new DB session for background task
        from app.database import SessionLocal
        bg_db = SessionLocal()
        try:
            orchestrator = PipelineOrchestrator(run_id, file_path, bg_db)
            # Use asyncio to run the async execute method
            import asyncio
            asyncio.run(orchestrator.execute())
        finally:
            bg_db.close()
            concurrency.release_slot(run_id)

    background_tasks.add_task(run_pipeline)

    return {"message": f"Pipeline started for run {run_id}", "status": "running"}


@router.post("/run/{run_id}/cancel", response_model=RunActionResponse)
async def cancel_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Cancel a running pipeline."""
    run = check_run_access(run_id, current_user, db)

    # A queued run has no orchestrator yet: the status flip alone cancels it,
    # because the worker's claim requires status == "queued" (#701).
    if run.status not in ("running", "queued"):
        raise bad_request(f"Cannot cancel run in status: {run.status}")

    _cancel_run_record(db, run)

    return {"message": f"Run {run_id} cancelled", "status": "cancelled"}


@router.post("/run/{run_id}/restart", response_model=RestartRunResponse)
async def restart_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new run using the same uploaded file as a previous run."""

    original_run = check_run_access(run_id, current_user, db)

    # Enforce the same per-user quota as /upload. Restart creates a brand-new
    # run, so without this check an impatient user mashing "restart" sails past
    # the daily/monthly cap and racks up Bedrock spend.
    rate_limit_error = check_rate_limit(current_user, db)
    if rate_limit_error:
        raise HTTPException(status_code=429, detail=rate_limit_error)

    # Locate the original uploaded file
    original_file = UPLOAD_DIR / f"{run_id}.{original_run.file_type}"
    _materialize_input_if_missing(run_id, original_run.file_type, original_file)
    if not original_file.exists():
        raise HTTPException(
            status_code=404,
            detail={
                "error": "file_not_found",
                "message": "Original file no longer available — please upload again.",
            },
        )

    # Allocate a new run id and durably archive the original input under it,
    # exactly as /upload does. The pod-local copy lives only on THIS pod;
    # with multiple replicas behind the load balancer a later start/retry
    # routinely lands on another pod, where only the durable archive can
    # re-materialize the input. Both the pod-local copy and the archive are
    # exclusive creates: a run-id collision on either regenerates the id and
    # retries (#685) rather than silently overwriting another run's file. A
    # failure here (a real storage fault, or every retry colliding) is
    # therefore FATAL, mirroring /upload: a child whose input can't be
    # recovered should not be created at all. (issue #180)
    content = original_file.read_bytes()

    def _build_manifest(new_run_id: str, stored_name: str) -> bytes:
        return json.dumps({
            "run_id": new_run_id,
            "original_filename": original_run.filename,
            "stored_as": stored_name,
            "file_type": original_run.file_type,
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "restarted_from": run_id,  # provenance: the run this was restarted from
            "uploaded_at": datetime.now().isoformat(),
            "user_email": current_user.email,
        }, indent=2).encode("utf-8")

    def _write_local(path: Path) -> None:
        # Exclusive create, exactly like /upload's pod-local write. A
        # non-exclusive copy here was a live defect: on a drawn id that
        # collides with a run whose pod-local input exists on THIS pod, the
        # copy overwrote that file, and the durable manifest write then
        # raised StorageKeyExists, whose cleanup unlinked it -- destroying
        # the other run's local input. Raising FileExistsError instead lets
        # create_run_archive regenerate the id and retry, touching nothing.
        # The bytes are already in memory (read above), so write them
        # directly rather than copying the file a second time.
        with open(path, "xb") as f:
            f.write(content)

    try:
        new_run_id, _, new_file_path, manifest = create_run_archive(
            content, original_run.file_type, _build_manifest, _write_local,
        )
    except Exception as e:
        logger.error(
            "Durable archive of restarted input failed; aborting restart: %s", e,
        )
        raise HTTPException(
            status_code=502,
            detail={
                "error": "storage_unavailable",
                "message": (
                    "We couldn't store the file securely, so no new run was created. "
                    "Please try again in a moment."
                ),
            },
        )
    storage = get_storage()

    # Cross-run, browsable-by-submitter index (best-effort; never fail the
    # restart). Mirrors /upload so restarted runs are findable by submitter too.
    try:
        storage.put_global(
            f"by-submitter/{current_user.email.lower()}/{new_run_id}/manifest.json",
            manifest,
        )
    except Exception as e:
        logger.warning("Failed to write by-submitter index (run=%s): %s", new_run_id, e)

    # Create new Run record, inheriting submission_type and the user's
    # output-rendering choices (issues #153, #199) from the original. Without
    # this the restarted run silently reverts to the column defaults (track
    # changes ON, classification comments OFF, strip instructions ON),
    # discarding a choice the user made at upload.
    new_run = Run(
        id=new_run_id,
        filename=original_run.filename,
        file_type=original_run.file_type,
        status="created",
        started_at=datetime.now(),
        user_id=current_user.id,
        submission_type=original_run.submission_type,
        show_track_changes=original_run.show_track_changes,
        show_pipeline_comments=original_run.show_pipeline_comments,
        strip_template_instructions=original_run.strip_template_instructions,
        scanned_pages=original_run.scanned_pages,  # same file, same pages (#1282)
    )
    db.add(new_run)

    # Create Step records from step registry
    for step_def in STEP_REGISTRY:
        step = Step(
            run_id=new_run_id,
            step_number=step_def.number,
            stage_id=step_def.stage_id,
            step_name=step_def.name,
            status="pending",
        )
        db.add(step)

    commit_run_or_compensate(db, new_run_id, current_user.email, new_file_path)

    # #181: restart replaces a still-running original rather than forking a
    # second copy that keeps spending alongside the new run. Done last, after
    # the child is committed, so a failed restart (429/404/502 above) leaves
    # the original running. Refresh first: the orchestrator may have finished
    # it since check_run_access() read it.
    db.refresh(original_run)
    if original_run.status == "running":
        _cancel_run_record(db, original_run)

    return {"run_id": new_run_id, "message": f"New run created from {run_id}"}


def _cancel_run_record(db: Session, run: Run) -> None:
    """Mark `run` cancelled, then signal the orchestrator to stop it.

    Shared by cancel_run and restart_run (#181); each caller checks that
    the run is still running first.

    Commit BEFORE signalling, not after: db.commit() can raise (see
    commit_run_or_compensate's #802 handling above), while orchestrator_cancel
    cannot -- cancel_run's set.add can't raise, and RedisBroker.request_cancel
    wraps its body in try/except (redis_broker.py, "best-effort"). A commit
    failure here leaves the run running with nothing told to stop it --
    consistent, and the caller's exception surfaces normally. The orchestrator
    also records a cancel on the row itself, so it does not rely on this write
    having happened (#591).
    """
    from app.pipeline.orchestrator import USER_CANCEL_MESSAGE

    run.status = "cancelled"
    run.error_message = USER_CANCEL_MESSAGE
    # Naive, matching every other Run timestamp write (orchestrator.py,
    # run_service.py, upload.py): pymysql drops tzinfo on write, so an aware
    # value would round-trip as naive UTC and get mislabelled with the
    # server's LOCAL offset by schemas.py's _iso_with_offset -- wrong by
    # that offset (timestamp and duration both) on a non-UTC host.
    run.completed_at = datetime.now()
    db.commit()
    batch_completion.notify_if_batch_complete(run.batch_id)

    from app.pipeline.orchestrator import cancel_run as orchestrator_cancel

    orchestrator_cancel(run.id)


@router.post("/run/{run_id}/retry/{step_number}", response_model=RunActionResponse)
def retry_step(
    run_id: str,
    step_number: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Retry a failed step by resuming the pipeline from that step.

    Re-runs the failed stage and every stage after it, reusing the outputs of
    the earlier completed stages already on disk. Cheaper than a full restart,
    which re-runs all 12 stages from the uploaded file.

    Resuming depends on the prior stages' outputs still being present on this
    pod's filesystem. If the pod recycled since the original run those are gone
    and the resumed stage will fail -- at which point the user falls back to
    "Restart with this file".

    Plain ``def`` (#701 run_queue.py point 11 / runs.py point 10): see
    start_run's docstring.
    """

    run = check_run_access(run_id, current_user, db)

    step = db.query(Step).filter(
        Step.run_id == run_id,
        Step.step_number == step_number
    ).first()

    if not step:
        raise not_found(f"Step {step_number} not found for run {run_id}")

    if step.status != "error":
        raise bad_request(f"Can only retry failed steps. Step {step_number} status: {step.status}")

    # The original uploaded file is needed to re-feed the pipeline.
    file_path = UPLOAD_DIR / f"{run_id}.{run.file_type}"
    _materialize_input_if_missing(run_id, run.file_type, file_path)
    if not file_path.exists():
        raise not_found("Uploaded file no longer available — please upload again.")

    queue_mode = concurrency.dispatch_mode() == "queue"

    # Admission control: a retry resumes a full pipeline and consumes the same
    # per-pod resource as a fresh start, so gate it the same way. Acquire before
    # mutating step/run state so a rejected retry leaves the run untouched.
    if not queue_mode and not concurrency.try_acquire_slot(run_id):
        raise HTTPException(
            status_code=429,
            detail={
                "error": "server_busy",
                # Keep the user-facing text free of a bare per-pod number: the
                # cap is per server instance, but a user's own run list (shown in
                # the side panel) is cross-pod and user-wide, so "maximum of N"
                # reads as wrong when they can see more than N of their own runs
                # in flight. The exact limit/active counts stay in `details` for
                # diagnostics. (issue #170)
                "message": (
                    "The system is temporarily at capacity and can't start a new "
                    "run right now. Any runs already in progress will keep going -- "
                    "please wait a moment and try again."
                ),
                "details": {
                    "limit_type": "concurrency",
                    "limit": concurrency.get_max_concurrent_runs(),
                    "active": concurrency.active_count(),
                },
            },
            headers={"Retry-After": "30"},
        )

    # Reset the failed step and every step after it back to pending; the earlier
    # completed steps are left untouched so the pipeline resumes rather than
    # restarts. Clear stale per-step metadata so the re-run repopulates it.
    downstream_steps = db.query(Step).filter(
        Step.run_id == run_id,
        Step.step_number >= step_number,
    ).all()

    def _reset_downstream_steps() -> tuple[StepSnapshot, ...]:
        # B3: snapshot each step's PRIOR state before resetting it, so a
        # failed enqueue's revert path can restore it exactly -- otherwise a
        # step this reset to "pending" is left there with no executor coming
        # for it, and retry_step's own `status != "error"` guard then
        # rejects a second retry outright.
        snapshots = tuple(
            StepSnapshot(
                step_id=s.id, status=s.status, error_message=s.error_message,
                started_at=s.started_at, completed_at=s.completed_at,
                duration_seconds=s.duration_seconds, cost=s.cost,
            )
            for s in downstream_steps
        )
        for s in downstream_steps:
            s.status = "pending"
            s.error_message = None
            s.started_at = None
            s.completed_at = None
            s.duration_seconds = None
            s.cost = None
        return snapshots

    if queue_mode:
        # Deferred to _dispatch_queue's on_flip, which flip_to_queued runs
        # (and commits) inside its OWN transaction, only once the flip has
        # actually won (#701 runs.py point 7, tightened by B3) -- staging
        # these resets here unconditionally, before the flip, would persist
        # them on a lost race (a 409) too.
        return _dispatch_queue(
            run, db, allowed_from=("failed",), start_step=step_number, on_flip=_reset_downstream_steps,
        )

    # Atomic -> running (#799): only one of two concurrent retries may claim a
    # run that is not already running. The step resets below share this
    # transaction, so the loser changes nothing and returns its slot.
    # started_at restarts the stale-run clock: reconcile_stale_runs ages runs by
    # it, so a retry of a run started over an hour ago was reaped mid-run (#145).
    if not claim_run_as_running(
        db, run_id, Run.status != "running",
        error_message=None, completed_at=None, started_at=datetime.now(),
    ):
        db.rollback()
        concurrency.release_slot(run_id)
        raise conflict("This run is already running.")

    _reset_downstream_steps()
    db.commit()

    # Resume pipeline execution in the background, mirroring start_run. The slot
    # acquired above is held for the resumed run and released when it finishes.
    def run_pipeline():
        from app.database import SessionLocal
        bg_db = SessionLocal()
        try:
            orchestrator = PipelineOrchestrator(run_id, file_path, bg_db)
            import asyncio
            asyncio.run(orchestrator.execute(start_step_number=step_number))
        finally:
            bg_db.close()
            concurrency.release_slot(run_id)

    background_tasks.add_task(run_pipeline)

    return {"message": f"Retrying run {run_id} from step {step_number}", "status": "running"}


@router.get("/run/{run_id}/run-quality", response_model=RunQualityReport)
def get_run_quality(
    run_id: str,
    db: Session = Depends(get_db),
    viewer: User = Depends(require_view_all_runs),
) -> RunQualityReport:
    """Quality score breakdown and run-doctor findings for one run (admin or staff).

    Either part is null when its artifact was never stored. Sync def so the
    blocking storage reads run off the event loop.
    """
    check_run_access(run_id, viewer, db, read_only=True)
    return build_run_quality_report(
        run_id,
        quality_score_service.get_cached_score(run_id),
        quality_score_service.get_doctor_report(run_id),
    )


@router.get("/run/{run_id}/review-note", response_model=RunReviewNote)
def get_run_review_note(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> RunReviewNote:
    """Whether the run's owner should see the "may need cleanup" note.

    Open to the run's owner, admins and staff; answers a bool and never the score.
    """
    run = check_run_access(run_id, current_user, db, read_only=True)
    cols = quality_score_service.ScoreColumns(
        run.quality_score, run.quality_band, run.quality_cap)
    if cols.quality_score is None:
        # Not yet backfilled onto the row: fall back to the cached score.
        cols = quality_score_service.score_columns(quality_score_service.get_cached_score(run_id))
    return RunReviewNote(needs_cleanup=columns_need_cleanup(cols))
