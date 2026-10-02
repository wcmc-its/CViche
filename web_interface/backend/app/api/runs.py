"""Run status and management API endpoints."""
import hashlib
import json
import logging
from collections.abc import Callable
from datetime import datetime
import redis
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks, Query
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Query as SAQuery, Session, selectinload
from pathlib import Path

from app.database import get_db
from app.models import Run, RunState, Step, User
from app.schemas import (
    RunFilterOptions, RunStatus, RunSummary, StepSummary, PaginatedRuns,
    CapacityResponse, RunActionResponse, RestartRunResponse, StatusFilterCounts,
    RunFeedbackSummary, RunQualityReport, RunReviewNote,
)
from app.pipeline.orchestrator import PipelineOrchestrator
from app.pipeline.step_registry import STEP_REGISTRY
from app.pipeline import concurrency, run_queue
from app.auth import get_current_user, require_admin, visible_cost
from app.api.upload import UPLOAD_DIR, create_run_archive, commit_run_or_compensate
from app.services.run_service import (
    check_run_access, claim_run_as_running, _materialize_input_if_missing,
    flip_to_queued, revert_queued, StepSnapshot,
)
from app.rate_limiter import check_rate_limit
from app.errors import not_found, bad_request, conflict, forbidden
from app.services.runs_admin_query import (
    RunScope, StatusFilter, build_filter_options, empty_feedback_summary, feedback_clause,
    filtered_runs_query, load_feedback_summaries, parse_feedback_filter,
    my_status_counts, parse_run_filters, parse_status_filter, run_by_summary, status_clause,
)
from app.services import quality_score_service
from app.services.run_quality_report import build_run_quality_report, columns_need_cleanup
from app.storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter()

# Run statuses /start may move to "running" (the conditional UPDATE's predicate).
STARTABLE_STATUSES = ("created", "paused")

# Largest page /runs serves. The run-history UI asks for exactly this many
# (RunHistory.tsx PAGE_SIZE), so lowering it breaks that page (#801).
MAX_RUNS_PAGE_SIZE = 100

# web_interface/outputs: where the orchestrator writes each run's stage
# artifacts (orchestrator.py's web_output_dir). Module-level so tests can
# point get_data_quality at a tmp dir instead of the real outputs tree.
_OUTPUTS_ROOT = Path(__file__).parent.parent.parent.parent / "outputs"

# What reading one stage artifact can legitimately raise: an unreadable or
# vanished file, non-UTF-8 bytes, or truncated/invalid JSON. get_data_quality
# records these as a per-file warning and carries on; anything else (a shape
# the code doesn't expect, a TypeError) is a bug and propagates (#801).
_ARTIFACT_READ_ERRORS = (OSError, UnicodeDecodeError, json.JSONDecodeError)


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

    # A batch run's token goes on the batch queue (#1114), on every branch below.
    queue = run_queue.queue_for(run.batch_id)
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
    current_user: User = Depends(require_admin),
) -> RunFilterOptions:
    """Options and run counts for the admin runs filters (scope=all, admin only).

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
        # Score columns are admin-only, like cost: never on scope=mine.
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

    scope=mine (default): the current user's runs. scope=all (admin only): every
    user's runs, optionally filtered by run_by (user id, "self" or "on_behalf"), faculty (the
    CV owner's name) and department (the running user's ED department); those
    three, and ``input_format`` ("wcm" = written in the WCM CV template, "other",
    "unknown" = not classified), are ignored under scope=mine. ``feedback`` ("given" = any reviewer left
    feedback, "needed" = complete with none) and ``status`` ("running" = queued or
    running, "failed" = failed; "red" = score band RED, admin only: 403 under scope=mine) apply in both
    scopes. Every run
    carries a ``feedback`` summary; scope=all adds the reviewer list.
    ``batch_id`` (#1114) narrows either scope to one batch's runs; every row
    carries its ``batch_id`` so the list can tag batch runs.
    """
    all_scope = scope == RunScope.ALL
    if all_scope:
        require_admin(current_user)
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
        run_by=run_by_summary(run.user) if current_user.role == "admin" else None,
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
    admin: User = Depends(require_admin),
) -> RunQualityReport:
    """Quality score breakdown and run-doctor findings for one run (admin only).

    Either part is null when its artifact was never stored. Sync def so the
    blocking storage reads run off the event loop. The path is not
    /run/{run_id}/quality, which is the extraction data-quality report below.
    """
    check_run_access(run_id, admin, db)
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

    Open to the run's owner and admins; answers a bool and never the score.
    """
    run = check_run_access(run_id, current_user, db)
    cols = quality_score_service.ScoreColumns(
        run.quality_score, run.quality_band, run.quality_cap)
    if cols.quality_score is None:
        # Not yet backfilled onto the row: fall back to the cached score.
        cols = quality_score_service.score_columns(quality_score_service.get_cached_score(run_id))
    return RunReviewNote(needs_cleanup=columns_need_cleanup(cols))


@router.get("/run/{run_id}/quality")
async def get_data_quality(
    run_id: str,
    step: int = Query(3, ge=1, le=len(STEP_REGISTRY)),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Get comprehensive data quality report for a run.

    Args:
        step: STEP_REGISTRY number, 1..len(STEP_REGISTRY) (contiguous, see validate_registry); else 422

    Returns all extraction data including:
    - Raw extracted entries with confidence scores
    - Rejected/filtered entries with reasons
    - Warnings and quality metrics
    """
    import json

    run = check_run_access(run_id, current_user, db)

    # Get output directory for this run
    outputs_dir = _OUTPUTS_ROOT / run_id

    if not outputs_dir.exists():
        raise not_found("Run output not available")

    # Complete WCM section mapping (all 71 sections)
    ALL_WCM_SECTIONS = {
        'A': {'name': 'Personal Data', 'entity_type': 'personal_data'},
        'B1': {'name': 'Academic Degrees', 'entity_type': 'education'},
        'B2': {'name': 'Other Educational Experiences', 'entity_type': 'education'},
        'B3': {'name': 'Residency & Fellowship Training', 'entity_type': 'education'},
        'C': {'name': 'Postdoctoral Training', 'entity_type': 'education'},
        'D1': {'name': 'Academic Appointments', 'entity_type': 'positions'},
        'D2': {'name': 'Hospital Appointments', 'entity_type': 'positions'},
        'D3': {'name': 'Other Professional Positions', 'entity_type': 'positions'},
        'D4': {'name': 'Visiting/Adjunct Appointments', 'entity_type': 'positions'},
        'E': {'name': 'Employment Status', 'entity_type': 'positions'},
        'F1': {'name': 'Licensure', 'entity_type': 'licensure'},
        'F2': {'name': 'Board Certification', 'entity_type': 'certifications'},
        'G': {'name': 'Institutional Affiliation', 'entity_type': 'positions'},
        'H': {'name': 'Honors & Awards', 'entity_type': 'honors'},
        'I': {'name': 'Professional Organizations', 'entity_type': 'memberships'},
        'J': {'name': 'Percent Effort', 'entity_type': 'other'},
        'K1': {'name': 'Didactic Teaching', 'entity_type': 'teaching'},
        'K2': {'name': 'Clinical Teaching', 'entity_type': 'teaching'},
        'K3': {'name': 'Administrative Teaching', 'entity_type': 'teaching'},
        'K4': {'name': 'Continuing Education', 'entity_type': 'teaching'},
        'K5': {'name': 'Educational Outreach', 'entity_type': 'teaching'},
        'K6': {'name': 'Curriculum Development', 'entity_type': 'teaching'},
        'K7': {'name': 'Assessment & Examinations', 'entity_type': 'teaching'},
        'K8': {'name': 'Simulation Education', 'entity_type': 'teaching'},
        'K9': {'name': 'Educational Materials', 'entity_type': 'teaching'},
        'L1': {'name': 'Clinical Practice', 'entity_type': 'clinical'},
        'L2': {'name': 'Clinical Innovations', 'entity_type': 'clinical'},
        'L3': {'name': 'Clinical Leadership', 'entity_type': 'clinical'},
        'L4': {'name': 'Quality Improvement', 'entity_type': 'clinical'},
        'M': {'name': 'Research/Grants', 'entity_type': 'grants'},
        'M1': {'name': 'Research Activities', 'entity_type': 'grants'},
        'M2': {'name': 'Research Support', 'entity_type': 'grants'},
        'M3': {'name': 'Patents & Inventions', 'entity_type': 'grants'},
        'M4': {'name': 'Clinical Trials', 'entity_type': 'grants'},
        'N1': {'name': 'Mentoring Programs', 'entity_type': 'mentoring'},
        'N2': {'name': 'Training Grants', 'entity_type': 'mentoring'},
        'N3': {'name': 'Current Mentees', 'entity_type': 'mentoring'},
        'N4': {'name': 'Past Mentees', 'entity_type': 'mentoring'},
        'N5': {'name': 'Dissertation Committees', 'entity_type': 'mentoring'},
        'N6': {'name': 'Career Advising', 'entity_type': 'mentoring'},
        'O': {'name': 'Institutional Leadership', 'entity_type': 'service'},
        'P': {'name': 'Administrative Activities', 'entity_type': 'service'},
        'Q1': {'name': 'Leadership in Organizations', 'entity_type': 'service'},
        'Q2': {'name': 'Boards & Committees', 'entity_type': 'service'},
        'Q3': {'name': 'Grant Reviewing', 'entity_type': 'service'},
        'Q4': {'name': 'Editorial Activities', 'entity_type': 'service'},
        'Q5': {'name': 'Ad Hoc Reviewing', 'entity_type': 'service'},
        'R': {'name': 'Invitations to Speak', 'entity_type': 'presentations'},
        'S': {'name': 'Bibliography', 'entity_type': 'publications'},
        'S1': {'name': 'Peer-Reviewed Articles', 'entity_type': 'publications'},
        'S2': {'name': 'Reviews & Editorials', 'entity_type': 'publications'},
        'S3': {'name': 'Letters to Editor', 'entity_type': 'publications'},
        'S4': {'name': 'Book Chapters', 'entity_type': 'publications'},
        'S5': {'name': 'Books', 'entity_type': 'publications'},
        'S6': {'name': 'Case Reports', 'entity_type': 'publications'},
        'S7': {'name': 'In Press/Submitted', 'entity_type': 'publications'},
        'S8': {'name': 'Abstracts', 'entity_type': 'publications'},
        'S9': {'name': 'Non-Peer-Reviewed', 'entity_type': 'publications'},
        'S10': {'name': 'Conference Proceedings', 'entity_type': 'publications'},
        'S11': {'name': 'Online Publications', 'entity_type': 'publications'},
        'S12': {'name': 'Newsletters', 'entity_type': 'publications'},
        'S13': {'name': 'Monographs', 'entity_type': 'publications'},
        'S14': {'name': 'Other Scholarly Works', 'entity_type': 'publications'},
        'S15': {'name': 'Patents & Copyrights', 'entity_type': 'publications'},
        'T': {'name': 'Supplemental', 'entity_type': 'other'},
        'T1': {'name': 'Media Appearances', 'entity_type': 'other'},
        'T2': {'name': 'Public Lectures', 'entity_type': 'other'},
        'T3': {'name': 'Community Service', 'entity_type': 'other'},
        'T4': {'name': 'Languages', 'entity_type': 'other'},
        'T5': {'name': 'Professional Development', 'entity_type': 'other'},
        'T6': {'name': 'Advisory Boards', 'entity_type': 'other'},
        'T7': {'name': 'Other Activities', 'entity_type': 'other'}
    }

    quality_report = {
        "run_id": run_id,
        "filename": run.filename,
        "status": run.status,
        "step": step,
        "all_sections": ALL_WCM_SECTIONS,  # Complete section list
        "sections": {},
        "warnings": [],
        "metrics": {}
    }

    # Add quality data for Steps 1 and 2
    if step == 1:
        # Step 1: Segmentation quality
        segmented_dir = outputs_dir / "stage_1_segmentation"
        if segmented_dir.exists():
            segmented_files = list(segmented_dir.glob("*_segmented.json"))
            if segmented_files:
                # Read first segmented file
                with open(segmented_files[0], 'r') as f:
                    segmented_data = json.load(f)

                meta = segmented_data.get('meta', {})
                quality_report["segmentation"] = {
                    "num_top_level_groups": meta.get('num_top_level_groups', 0),
                    "total_groups": meta.get('total_groups_including_subgroups', 0),
                    "total_entries": meta.get('total_entries', 0),
                    "processing_method": meta.get('processing_method', 'unknown')
                }

                # Show sample groups
                groups = segmented_data.get('groups', [])
                quality_report["sample_sections"] = []
                for group in groups[:5]:  # First 5 groups
                    quality_report["sample_sections"].append({
                        "label": group.get('label_inferred', 'Unknown'),
                        "num_entries": len(group.get('entries', [])),
                        "num_subgroups": len(group.get('subgroups', []))
                    })

        return quality_report

    if step == 2:
        # Step 2: Taxonomy mapping quality
        taxonomy_dir = outputs_dir / "stage_2_taxonomy_mapping"
        if taxonomy_dir.exists():
            mapped_files = list(taxonomy_dir.glob("*_mapped.json"))
            if mapped_files:
                # Read first mapped file
                with open(mapped_files[0], 'r') as f:
                    mapped_data = json.load(f)

                meta = mapped_data.get('meta', {})
                mapping_stats = meta.get('mapping_stats', {})

                quality_report["taxonomy_mapping"] = {
                    "total_sections": mapping_stats.get('total_sections', 0),
                    "high_confidence": mapping_stats.get('high_confidence', 0),
                    "medium_confidence": mapping_stats.get('medium_confidence', 0),
                    "low_confidence": mapping_stats.get('low_confidence', 0),
                    "unmapped": mapping_stats.get('unmapped', 0),
                    "avg_confidence": mapping_stats.get('avg_confidence', 0)
                }

                # Show mapped sections
                mappings = mapped_data.get('mappings', [])
                quality_report["mapped_sections"] = []
                for mapping in mappings[:10]:  # First 10 mappings
                    quality_report["mapped_sections"].append({
                        "source_label": mapping.get('source_label', 'Unknown'),
                        "mapped_to": mapping.get('mapped_canonical_name', 'Unknown'),
                        "section_id": mapping.get('mapped_section_id', ''),
                        "confidence": mapping.get('confidence', 0),
                        "num_entries": mapping.get('num_entries', 0)
                    })

        return quality_report

    # If Step 4, read Stage 4 template metadata instead
    if step == 4:
        stage_4_dir = outputs_dir / "stage_4_wcm_templates"

        if not stage_4_dir.exists():
            # Step 4 not yet run or no data
            return quality_report

        # Find all template metadata files
        metadata_files = list(stage_4_dir.glob("*_template_metadata.json"))

        if not metadata_files:
            # No templates generated - return empty report
            return quality_report

        # Read enriched files to get actual entry data for display
        enriched_dir = outputs_dir / "stage_2d_enriched"
        entries_by_type = {}
        section_ids_by_type = {}  # Track which section IDs map to each entity type
        section_files_found = {}  # Track which section files exist (section_id -> file path)

        # Also read parsed files to track unpopulated entries
        parsed_dir = outputs_dir / "stage_3_parsing"
        parsed_entries_by_type = {}  # All parsed entries before filtering

        if enriched_dir.exists():
            for enriched_file in enriched_dir.glob("section_*_enriched.json"):
                try:
                    with open(enriched_file, 'r') as f:
                        enriched_data = json.load(f)

                    # Determine entity type from enriched data structure
                    # Files are named like: section_B1_CV_xxx_enriched.json
                    # They contain 'parsed_entries' with 'structured_data' and 'confidence'
                    parsed_entries = enriched_data.get('parsed_entries', [])

                    if not parsed_entries:
                        continue

                    # Infer entity type from section_type or structured_data keys
                    section_type = enriched_data.get('section_type', '')

                    # Map section types to entity types
                    section_to_entity = {
                        'B1': 'education',
                        'B2': 'education',
                        'B3': 'education',
                        'C': 'education',
                        'D1': 'positions',
                        'D2': 'positions',
                        'D3': 'positions',
                        'D4': 'positions',
                        'F1': 'licensure',
                        'F2': 'certifications',
                        'H': 'honors',
                        'I': 'memberships',
                        'M': 'grants',
                        'N3': 'mentoring',
                        'N4': 'mentoring',
                        'O': 'service',
                        'P': 'service',
                        'S': 'publications'
                    }

                    # Section names for display
                    section_names = {
                        'B1': 'Academic Degrees',
                        'B2': 'Other Educational Experiences',
                        'B3': 'Residency & Fellowship Training',
                        'C': 'Postdoctoral Training',
                        'D1': 'Academic Appointments',
                        'D2': 'Hospital Appointments',
                        'D3': 'Other Professional Positions',
                        'D4': 'Visiting/Adjunct Appointments',
                        'F1': 'Licensure',
                        'F2': 'Board Certification',
                        'H': 'Honors & Awards',
                        'I': 'Professional Organizations',
                        'M': 'Research/Grants',
                        'N3': 'Current Mentees',
                        'N4': 'Past Mentees',
                        'O': 'Institutional Leadership',
                        'P': 'Institutional Administrative Activities',
                        'S': 'Publications'
                    }

                    # Try to extract section ID from filename
                    import re
                    section_match = re.search(r'section_([A-Z]\d*)_', enriched_file.name)
                    if section_match:
                        section_id = section_match.group(1)
                        section_files_found[section_id] = enriched_file  # Track this file
                        entity_type = section_to_entity.get(section_id)

                        if entity_type:
                            if entity_type not in entries_by_type:
                                entries_by_type[entity_type] = []
                                section_ids_by_type[entity_type] = []
                            entries_by_type[entity_type].extend(parsed_entries)

                            # Track section ID and name
                            section_info = {
                                'id': section_id,
                                'name': section_names.get(section_id, section_id),
                                'count': len(parsed_entries)
                            }
                            section_ids_by_type[entity_type].append(section_info)

                except _ARTIFACT_READ_ERRORS as e:
                    quality_report["warnings"].append({
                        "type": "enriched_read_error",
                        "file": enriched_file.name,
                        "error": str(e)
                    })

        # Read parsed files to get ALL entries before filtering (for unpopulated tracking)
        if parsed_dir.exists():
            for entity_type_dir in parsed_dir.iterdir():
                if entity_type_dir.is_dir():
                    entity_type = entity_type_dir.name

                    for parsed_file in entity_type_dir.glob("*_parsed.json"):
                        try:
                            with open(parsed_file, 'r') as f:
                                parsed_data = json.load(f)

                            # Get all entries from parsed file
                            # Format: {entity_type: [{entry1}, {entry2}, ...]}
                            entries_key = entity_type  # e.g., "education", "publications"
                            all_parsed = parsed_data.get(entries_key, [])

                            if entity_type not in parsed_entries_by_type:
                                parsed_entries_by_type[entity_type] = []

                            # Normalize entries to have confidence field
                            for entry in all_parsed:
                                if 'confidence' not in entry:
                                    entry['confidence'] = 1.0  # Pre-structured entries
                                parsed_entries_by_type[entity_type].append(entry)

                        except _ARTIFACT_READ_ERRORS as e:
                            quality_report["warnings"].append({
                                "type": "parsed_read_error",
                                "file": parsed_file.name,
                                "error": str(e)
                            })

        # Aggregate records_processed from metadata files
        total_by_type = {}

        for metadata_file in metadata_files:
            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)

                records_processed = metadata.get('records_processed', {})

                # Aggregate by entity type
                for entity_type, count in records_processed.items():
                    total_by_type[entity_type] = total_by_type.get(entity_type, 0) + count

            except _ARTIFACT_READ_ERRORS as e:
                quality_report["warnings"].append({
                    "type": "metadata_read_error",
                    "file": metadata_file.name,
                    "error": str(e)
                })

        # Build sections report with actual entry data
        for entity_type, count in total_by_type.items():
            if count > 0:
                # Get entries for this type
                all_entries = entries_by_type.get(entity_type, [])
                section_info_list = section_ids_by_type.get(entity_type, [])

                # Separate high vs low confidence
                high_conf = [e for e in all_entries if e.get('confidence', 0) > 0.6]
                low_conf = [e for e in all_entries if e.get('confidence', 0) <= 0.6]

                # Calculate average confidence
                avg_conf = sum(e.get('confidence', 0) for e in all_entries) / len(all_entries) if all_entries else 0

                # Build section IDs string for display (e.g., "B1, B2, C")
                section_ids_str = ", ".join([s['id'] for s in section_info_list]) if section_info_list else ""
                section_names_list = [f"{s['id']} ({s['name']})" for s in section_info_list] if section_info_list else []

                quality_report["sections"][entity_type] = {
                    "total_extracted": len(all_entries),
                    "total_inserted": count,
                    "low_confidence": len(low_conf),
                    "avg_confidence": avg_conf,
                    "entries": [e.get('structured_data', e) for e in high_conf[:10]],  # First 10 high-confidence entries
                    "section_ids": section_ids_str,  # "B1, B2, C"
                    "section_names": section_names_list  # ["B1 (Academic Degrees)", "B2 (Other Educational Experiences)"]
                }
                quality_report["metrics"][f"{entity_type}_inserted"] = count

                # Track unpopulated entries if some were excluded
                if len(all_entries) > count:
                    quality_report["sections"][entity_type]["unpopulated_count"] = len(all_entries) - count

        # Add Personal Data (Section A) if available
        # Personal data is stored separately in stage_4_wcm_templates metadata
        for metadata_file in metadata_files:
            try:
                with open(metadata_file, 'r') as f:
                    metadata = json.load(f)

                # Check if pipeline_stages has personal data info
                pipeline_stages = metadata.get('pipeline_stages', {})

                # Look for personal info in the first page extraction
                # This is typically stored in the personal_info field or can be reconstructed
                if 'personal_data' not in quality_report["sections"]:
                    # Add a placeholder for personal data if it was populated
                    # We'll assume it was populated if the template was generated
                    quality_report["sections"]["personal_data"] = {
                        "total_extracted": 1,
                        "total_inserted": 1,
                        "low_confidence": 0,
                        "avg_confidence": 1.0,
                        "entries": [{
                            "section": "Personal Data (Section A)",
                            "note": "Name and contact information extracted from first page"
                        }],
                        "section_ids": "A",
                        "section_names": ["A (Personal Data)"]
                    }
                    quality_report["metrics"]["personal_data_inserted"] = 1
                break  # Only need to check once

            except _ARTIFACT_READ_ERRORS as e:  # the loop above already warned
                logger.warning("quality re-read %s failed (run=%s): %s", metadata_file.name, run_id, e)

        # Track unpopulated entries with reasons
        quality_report["unpopulated_data"] = {
            "total_unpopulated": 0,
            "by_reason": {},
            "by_section": {}
        }

        # Use parsed entries (before filtering) to detect unpopulated data
        for entity_type, all_parsed in parsed_entries_by_type.items():
            inserted_count = total_by_type.get(entity_type, 0)

            if len(all_parsed) > inserted_count:
                # Some entries were not inserted - analyze why
                unpopulated = []
                seen_entries = {}  # Track unique entries (first occurrence assumed inserted)
                unique_count = 0   # Count of unique entries seen

                for entry in all_parsed:
                    confidence = entry.get('confidence', 0)
                    # Parsed entries may not have structured_data wrapper
                    structured_data = entry.get('structured_data', entry)
                    original_text = entry.get('original_text', '')

                    # Determine reason for exclusion
                    reason = None
                    reason_detail = None

                    # Create unique key for duplicate detection
                    if entity_type == 'education':
                        degree = structured_data.get('degree', structured_data.get('Degree', ''))
                        institution = structured_data.get('institution', structured_data.get('Institution', ''))
                        year = structured_data.get('end_year', structured_data.get('Year Awarded', ''))
                        entry_key = f"{degree}|{institution}|{year}".lower()
                    elif entity_type == 'publications':
                        title = structured_data.get('title', structured_data.get('Title', ''))
                        year = structured_data.get('year', structured_data.get('Year', ''))
                        entry_key = f"{title}|{year}".lower()
                    else:
                        # Generic key for other types
                        entry_key = str(structured_data)

                    # Check for duplicate
                    is_first_occurrence = entry_key not in seen_entries
                    was_inserted = False  # Will determine if this entry was likely inserted

                    if entry_key in seen_entries:
                        # Duplicate entry - definitely not inserted
                        reason = "duplicate"
                        reason_detail = f"Duplicate entry (same as entry #{seen_entries[entry_key]})"
                    else:
                        # First occurrence of this entry
                        unique_count += 1
                        seen_entries[entry_key] = unique_count

                        # Check other exclusion reasons
                        if confidence <= 0.6:
                            reason = "low_confidence"
                            reason_detail = f"Confidence {confidence:.2f} ≤ 0.6 threshold"
                        else:
                            # Check if it looks like a name (for education/positions)
                            if entity_type in ['education', 'positions']:
                                institution = structured_data.get('institution', structured_data.get('Institution', ''))
                                degree = structured_data.get('degree', structured_data.get('Degree', ''))

                                # Name detection logic (same as populate_cv.py)
                                if len(institution) < 30 and (', ' in institution or 'PhD' in institution or 'MD' in institution):
                                    reason = "filtered_as_name"
                                    reason_detail = f"Detected as CV owner name: '{institution}'"
                                elif not any(kw in institution.lower() for kw in ['university', 'college', 'institute', 'school', 'hospital', 'center', 'centre', 'medical', 'academy']):
                                    words = institution.split()
                                    if len(words) <= 4 and len(institution) < 40:
                                        reason = "filtered_as_name"
                                        reason_detail = f"Likely person name, not institution: '{institution}'"
                                    else:
                                        # First occurrence, high confidence, valid entry - likely inserted
                                        was_inserted = unique_count <= inserted_count
                                else:
                                    # First occurrence, high confidence, valid entry - likely inserted
                                    was_inserted = unique_count <= inserted_count
                            else:
                                # Non-education/position entity - likely inserted if within count
                                was_inserted = unique_count <= inserted_count

                        # If still no reason and wasn't inserted, mark as other
                        if reason is None and not was_inserted:
                            reason = "other"
                            reason_detail = "High confidence but excluded (possibly missing required fields)"

                    # Only add to unpopulated list if it wasn't inserted
                    if not was_inserted and reason is not None:
                        unpopulated.append({
                            "reason": reason,
                            "reason_detail": reason_detail,
                            "confidence": confidence,
                            "structured_data": structured_data,
                            "original_text": original_text[:100] if original_text else ""  # First 100 chars
                        })

                # Categorize by reason
                by_reason = {}
                for entry in unpopulated:
                    reason = entry['reason']
                    if reason not in by_reason:
                        by_reason[reason] = []
                    by_reason[reason].append(entry)

                # Add to report
                quality_report["unpopulated_data"]["by_section"][entity_type] = {
                    "total_unpopulated": len(unpopulated),
                    "total_extracted": len(all_parsed),
                    "total_inserted": inserted_count,
                    "by_reason": {
                        reason: {
                            "count": len(entries),
                            "examples": entries[:3]  # First 3 examples
                        }
                        for reason, entries in by_reason.items()
                    }
                }

                quality_report["unpopulated_data"]["total_unpopulated"] += len(unpopulated)

                # Aggregate by reason across all sections
                for reason, entries in by_reason.items():
                    if reason not in quality_report["unpopulated_data"]["by_reason"]:
                        quality_report["unpopulated_data"]["by_reason"][reason] = {
                            "count": 0,
                            "sections": []
                        }
                    quality_report["unpopulated_data"]["by_reason"][reason]["count"] += len(entries)
                    quality_report["unpopulated_data"]["by_reason"][reason]["sections"].append({
                        "entity_type": entity_type,
                        "count": len(entries)
                    })

        # Initialize ALL 71 sections with empty status
        all_sections_with_status = {}
        for section_id, section_info in ALL_WCM_SECTIONS.items():
            all_sections_with_status[section_id] = {
                "section_id": section_id,
                "section_name": section_info['name'],
                "entity_type": section_info['entity_type'],
                "status": "empty",  # Will be updated if data found
                "total_extracted": 0,
                "total_inserted": 0,
                "low_confidence": 0,
                "avg_confidence": 0.0,
                "entries": []
            }

        # Update sections that have data
        for section_id, enriched_file in section_files_found.items():
            if section_id in all_sections_with_status:
                # Get entry data from quality_report.sections if available
                entity_type = ALL_WCM_SECTIONS[section_id]['entity_type']

                # Find entries for this section
                for entity_name, section_data in quality_report["sections"].items():
                    if section_id in section_data.get('section_ids', '').split(', '):
                        all_sections_with_status[section_id].update({
                            "status": "populated",
                            "total_extracted": section_data.get('total_extracted', 0),
                            "total_inserted": section_data.get('total_inserted', 0),
                            "low_confidence": section_data.get('low_confidence', 0),
                            "avg_confidence": section_data.get('avg_confidence', 0.0),
                            "entries": section_data.get('entries', [])
                        })
                        break

        # Replace sections dict with complete section-by-section breakdown
        quality_report["sections_by_id"] = all_sections_with_status
        quality_report["sections_summary"] = quality_report["sections"]  # Keep entity type summary

        # Calculate overall statistics
        total_sections = len(ALL_WCM_SECTIONS)
        populated_sections = sum(1 for s in all_sections_with_status.values() if s['status'] == 'populated')
        empty_sections = total_sections - populated_sections

        quality_report["statistics"] = {
            "total_sections": total_sections,
            "populated_sections": populated_sections,
            "empty_sections": empty_sections,
            "coverage_percentage": (populated_sections / total_sections * 100) if total_sections > 0 else 0
        }

        # Set overall quality score based on whether any records were inserted
        if total_by_type or quality_report["sections"]:
            quality_report["overall_quality_score"] = 100.0

        return quality_report

    # Read Stage 3 parsed data (all sections)
    stage_3_dir = outputs_dir / "stage_3_parsing"

    for section_type in ["publications", "education", "positions", "grants", "certifications", "honors", "memberships", "service", "licensure", "mentoring"]:
        section_dir = stage_3_dir / section_type

        if not section_dir.exists():
            continue

        # Find all JSON files in this section directory
        json_files = list(section_dir.glob("*.json"))

        all_entries = []
        total_confidence = 0.0
        low_confidence_count = 0

        for json_file in json_files:
            try:
                with open(json_file, 'r') as f:
                    data = json.load(f)

                # Handle different JSON structures
                if isinstance(data, list):
                    entries = data
                elif isinstance(data, dict):
                    entries = data.get('entries', data.get('parsed_entries', []))
                else:
                    continue

                for entry in entries:
                    if isinstance(entry, dict):
                        confidence = entry.get('confidence', 1.0)
                        total_confidence += confidence

                        if confidence < 0.7:
                            low_confidence_count += 1
                            quality_report["warnings"].append({
                                "section": section_type,
                                "type": "low_confidence",
                                "confidence": confidence,
                                "entry": entry.get('title', entry.get('institution', entry.get('degree', 'Unknown')))[:100]
                            })

                        all_entries.append({
                            **entry,
                            "source_file": json_file.name,
                            "included": confidence >= 0.5  # Threshold for inclusion
                        })
            except _ARTIFACT_READ_ERRORS as e:
                quality_report["warnings"].append({
                    "section": section_type,
                    "type": "parse_error",
                    "file": json_file.name,
                    "error": str(e)
                })

        if all_entries:
            avg_confidence = total_confidence / len(all_entries) if all_entries else 0

            quality_report["sections"][section_type] = {
                "total_extracted": len(all_entries),
                "low_confidence": low_confidence_count,
                "avg_confidence": round(avg_confidence, 3),
                "entries": all_entries  # All entries with metadata
            }

            quality_report["metrics"][f"{section_type}_coverage"] = round(avg_confidence * 100, 1)

    # Read Stage 3 WCM sections (all 71 section types)
    stage_3_sections_dir = outputs_dir / "stage_3_sections"

    if stage_3_sections_dir.exists():
        section_files = list(stage_3_sections_dir.glob("section_*.json"))

        for section_file in section_files:
            try:
                with open(section_file, 'r') as f:
                    section_data = json.load(f)

                # Extract section name from filename (e.g., "section_education_CV_123.json" -> "education")
                section_name = section_file.stem.replace("_parsed", "").replace(f"_{Path(run.filename).stem}", "").replace("section_", "")

                num_entries = section_data.get('num_entries', 0)
                avg_conf = section_data.get('avg_taxonomy_confidence', 0.0)

                if num_entries > 0:
                    # Add to sections report
                    quality_report["sections"][section_name] = {
                        "total_extracted": num_entries,
                        "low_confidence": 0,  # WCM sections don't have per-entry confidence yet
                        "avg_confidence": round(avg_conf, 3),
                        "entries": section_data.get('entries', [])[:10],  # First 10 entries only for performance
                        "section_type": "wcm_section"
                    }

                    quality_report["metrics"][f"{section_name}_coverage"] = round(avg_conf * 100, 1)

            except _ARTIFACT_READ_ERRORS as e:
                quality_report["warnings"].append({
                    "section": section_name if 'section_name' in locals() else section_file.name,
                    "type": "section_read_error",
                    "file": section_file.name,
                    "error": str(e)
                })

    # Read Stage 2 taxonomy data to see classification confidence
    stage_2_file = outputs_dir / "stage_2_taxonomy_mapping" / f"{Path(run.filename).stem}_mapped.json"

    if stage_2_file.exists():
        try:
            with open(stage_2_file, 'r') as f:
                taxonomy_data = json.load(f)

            misclassified = []
            for section in taxonomy_data.get('sections', []):
                confidence = section.get('confidence', 0)
                if confidence < 0.7:
                    misclassified.append({
                        "section_title": section.get('title', 'Unknown')[:50],
                        "classified_as": section.get('wcm_section', 'Unknown'),
                        "confidence": confidence
                    })

            if misclassified:
                quality_report["warnings"].append({
                    "type": "uncertain_classification",
                    "count": len(misclassified),
                    "details": misclassified[:10]  # First 10
                })
        except _ARTIFACT_READ_ERRORS as e:
            quality_report["warnings"].append({
                "type": "taxonomy_read_error",
                "error": str(e)
            })

    # Calculate overall quality score
    if quality_report["sections"]:
        avg_coverage = sum(quality_report["metrics"].values()) / len(quality_report["metrics"])
        quality_report["overall_quality_score"] = round(avg_coverage, 1)

    return quality_report
