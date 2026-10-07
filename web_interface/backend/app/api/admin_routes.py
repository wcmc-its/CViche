"""Admin dashboard API endpoints. All endpoints require admin role."""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func
from sqlalchemy.orm import Session, contains_eager

from app.database import get_db
from app.models import User, Run, Feedback
from app.auth import require_admin, require_view_all_runs
from app.schemas import (
    AdminStats,
    AdminUser,
    AdminRunEntry,
    AdminRunsResponse,
    AdminConfigResponse,
    AdminConfigUpdate,
    ConsentPublishPreview,
    ConsentPublishRequest,
    AdminUserUpdate,
    QualityScoreResult,
    QueueStatsResponse,
)
from app.services.runs_admin_query import submission_split
from app.services.admin_service import apply_user_update, get_step_avg_seconds, get_users_with_stats
from app.services.admin_config_service import (
    bump_session_epoch, consent_publish_preview, load_admin_config, publish_next_consent_version,
    update_admin_config,
)
from app.services.admin_export_service import open_export
from app.services.admin_run_service import (
    hard_delete_feedback, hard_delete_run, queue_stats, reap_orphans, score_run,
)
from app.services.quality_score_service import get_cached_score
from concurrent.futures import ThreadPoolExecutor

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/admin/stats
# ---------------------------------------------------------------------------
@router.get("/admin/stats", response_model=AdminStats)
async def get_stats(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Return overview statistics for the admin dashboard."""
    total_runs = db.query(func.count(Run.id)).scalar() or 0

    thirty_days_ago = datetime.now() - timedelta(days=30)
    active_users = (
        db.query(func.count(func.distinct(Run.user_id)))
        .filter(Run.started_at >= thirty_days_ago, Run.user_id.isnot(None))
        .scalar()
        or 0
    )

    total_cost = db.query(func.sum(Run.total_cost)).scalar() or 0.0

    completed_runs = (
        db.query(func.count(Run.id)).filter(Run.status == "complete").scalar() or 0
    )
    runs_with_feedback = (
        db.query(func.count(func.distinct(Feedback.run_id))).scalar() or 0
    )
    feedback_rate = (
        (runs_with_feedback / completed_runs * 100) if completed_runs > 0 else 0.0
    )

    # CV-to-WCM conversion time, aggregated server-side over completed runs and
    # returned on this existing stats call (the dashboard already makes it), so the
    # admin overview gets avg/p95 without a second round-trip. The aggregate has to
    # be computed here rather than on the client because /admin/runs is paginated --
    # the browser never holds the whole population. Prefer the persisted pipeline
    # duration; fall back to wall-clock for runs that predate the column.
    #
    # Select only the three duration columns rather than hydrating a full Run ORM
    # object per completed run (#128) -- at scale that was the dominant cost here.
    # avg/p95 stay in Python: the wall-clock fallback needs a per-dialect timestamp
    # diff the SQLite test suite can't exercise, and the nearest-rank p95 is already
    # portable and correct.
    durations = sorted(
        total if total is not None
        else int((completed_at - started_at).total_seconds())
        for total, started_at, completed_at in db.query(
            Run.total_duration_seconds, Run.started_at, Run.completed_at
        )
        .filter(Run.status == "complete", Run.started_at.isnot(None), Run.completed_at.isnot(None))
        .all()
    )
    avg_duration_seconds = round(sum(durations) / len(durations), 1) if durations else None
    # Nearest-rank p95 over the sorted durations (portable; modest run volume).
    p95_duration_seconds = (
        durations[min(len(durations) - 1, max(0, round(0.95 * (len(durations) - 1))))]
        if durations else None
    )

    step_avg_seconds = get_step_avg_seconds(db)

    return AdminStats(
        total_runs=total_runs,
        active_users=active_users,
        total_cost=round(total_cost, 4),
        feedback_rate=round(feedback_rate, 1),
        avg_duration_seconds=avg_duration_seconds,
        p95_duration_seconds=p95_duration_seconds,
        step_avg_seconds=step_avg_seconds,
        submissions=submission_split(db),
    )


# ---------------------------------------------------------------------------
# GET /api/admin/users
# ---------------------------------------------------------------------------
@router.get("/admin/users", response_model=list[AdminUser])
async def get_users(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Return all users with per-user stats."""
    return get_users_with_stats(db)


# ---------------------------------------------------------------------------
# PUT /api/admin/users/{user_id}
# ---------------------------------------------------------------------------
@router.put("/admin/users/{user_id}", response_model=AdminUser)
async def update_user(
    user_id: int,
    body: AdminUserUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> AdminUser:
    """Update a user's role, status, or limits."""
    return apply_user_update(db, user_id, body, admin)


# ---------------------------------------------------------------------------
# DELETE /api/admin/feedback/{feedback_id}
# ---------------------------------------------------------------------------
@router.delete("/admin/feedback/{feedback_id}", status_code=204)
async def delete_feedback(
    feedback_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> None:
    """Hard-delete a single feedback submission.

    Used to purge a garbage/abusive response that would otherwise pollute the
    aggregated Feedback Insights. 404 if the row does not exist. The deletion is
    logged with the acting admin and the affected run so the action is auditable.
    """
    hard_delete_feedback(db, feedback_id, admin)


# ---------------------------------------------------------------------------
# POST /api/admin/runs/reap-orphans
# ---------------------------------------------------------------------------
# response_model=None: the return annotation types the handler without
# generating a response schema, so the OpenAPI document is unchanged.
@router.post("/admin/runs/reap-orphans", response_model=None)
async def reap_orphan_runs(
    dry_run: bool = Query(False, description="Preview candidates without deleting anything."),
    older_than_hours: int | None = Query(
        None, ge=1, description="Override the age threshold in hours (default 24)."
    ),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, object]:
    """Reap runs stuck at status='created' (uploaded but never started) along
    with their child rows and leftover storage objects (runs/{id}/input/ and the
    by-submitter index).

    These accumulate when an upload's run is never started -- a declined
    WCM-template upload, or a failed/abandoned start -- and are hidden from the
    Runs dashboard but stay visible in S3. Call with ?dry_run=true first to
    preview which runs would be removed.
    """
    return reap_orphans(db, admin, older_than_hours=older_than_hours, dry_run=dry_run)


# ---------------------------------------------------------------------------
# GET /api/admin/queue/stats
# ---------------------------------------------------------------------------
@router.get("/admin/queue/stats", response_model=QueueStatsResponse)
def get_queue_stats(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> QueueStatsResponse:
    """Run-queue depth and ownership (Valkey) beside the DB view of queued and
    running runs (#701). ``enabled`` reflects ``dispatch_mode() == "queue"``,
    not merely whether ``CVICHE_REDIS_URL`` is set -- that URL is shared with
    the event broker, the idle-session store, the login throttle and SAML
    replay, so in_process mode can still see it configured.

    A queued row older than ``CVICHE_QUEUED_RECONCILE_MINUTES`` while stream
    ``lag`` and ``pending`` are both 0 has lost its Valkey token; the
    queued-run reconciler (``run_service.reconcile_queued_runs``) requeues it
    on its next sweep, and the user's own /start does the same sooner. ``lag``
    also counts stale tokens -- an idempotent re-enqueue of an
    already-queued run, or a run cancelled while queued whose token nothing
    has claimed yet -- so a non-zero lag does not by itself rule stranding
    out.

    The top-level stream fields are the single-run queue's (unchanged since
    #701); ``queues`` repeats them per queue, adding the batch stream and its
    dead-letter count (#1114).

    Plain ``def``: both ``queue_db_view`` (sync Session) and ``run_queue``'s
    Valkey calls (sync redis-py) are blocking, so FastAPI runs this in the
    threadpool instead of stalling the event loop (#701 admin_routes.py
    point 1 / run_queue.py point 11).
    """
    return queue_stats(db)


# ---------------------------------------------------------------------------
# DELETE /api/admin/runs/{run_id}
# ---------------------------------------------------------------------------
@router.delete("/admin/runs/{run_id}", status_code=204)
async def delete_run(
    run_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> None:
    """Hard-delete one run (rows, stage artifacts, source CV, by-submitter
    index) -- the removal path for a consent-withdrawal request (#683).
    404 if unknown; 409 while the run is executing; 500 if storage or the DB
    delete fails (storage is deleted first, so a retry is safe).

    A 'cancelled' run is deletable: cancellation is a signal the orchestrator
    honours at its next stage boundary and nothing acknowledges it, so a
    just-cancelled run may still be writing for a short window (#683)."""
    hard_delete_run(db, run_id, admin)


# ---------------------------------------------------------------------------
# GET /api/admin/runs
# ---------------------------------------------------------------------------
@router.get("/admin/runs", response_model=AdminRunsResponse)
async def get_runs(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    user: str | None = Query(None, description="Filter by user email"),
    status: str | None = Query(
        None,
        description=(
            "Filter by run status. Omit for the default view, which hides "
            "never-started 'created' runs (abandoned/declined uploads). Pass a "
            "specific status for an exact match, or 'all' to include every "
            "status, never-started runs included."
        ),
    ),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Return all runs paginated, with optional filters."""
    # Eager-load run.user via the outer join (the relationship is
    # lazy="raise_on_sql"). contains_eager populates run.user from the joined
    # columns -- one query, no per-row lookup -- while the outer join still
    # lets us filter by user email.
    query = db.query(Run).outerjoin(Run.user).options(contains_eager(Run.user))

    if user:
        query = query.filter(User.email.ilike(f"%{user}%"))

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
        query = query.filter(Run.status != "created")

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


# ---------------------------------------------------------------------------
# POST /api/admin/run/{run_id}/score  -- compute/backfill the advisory score
# ---------------------------------------------------------------------------
@router.post("/admin/run/{run_id}/score", response_model=QualityScoreResult)
def compute_run_score(
    run_id: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> QualityScoreResult:
    """Compute (or refresh) the advisory quality score for a run and cache it.

    Used to backfill runs created before scoring existed, or to refresh after a
    re-run. Sync def so FastAPI runs the (blocking) storage I/O off the loop.
    """
    return score_run(db, run_id)


# ---------------------------------------------------------------------------
# GET /api/admin/config
# ---------------------------------------------------------------------------
@router.get("/admin/config", response_model=AdminConfigResponse)
async def get_config(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> AdminConfigResponse:
    """Return current system configuration values."""
    return load_admin_config(db)


# ---------------------------------------------------------------------------
# PUT /api/admin/config
# ---------------------------------------------------------------------------
@router.put("/admin/config", response_model=AdminConfigResponse)
async def update_config(
    body: AdminConfigUpdate,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> AdminConfigResponse:
    """Update system configuration values."""
    return update_admin_config(db, body, admin)


# ---------------------------------------------------------------------------
# GET/POST /api/admin/consent/publish  -- publish the next consent version
# ---------------------------------------------------------------------------
@router.get("/admin/consent/publish", response_model=ConsentPublishPreview)
async def preview_consent_publish(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> ConsentPublishPreview:
    """The next consent version and how many active users must agree again if it is published."""
    return consent_publish_preview(db)


@router.post("/admin/consent/publish", response_model=ConsentPublishPreview)
async def publish_consent_version(
    body: ConsentPublishRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> ConsentPublishPreview:
    """Publish the next consent version. ``body.version`` is the one the admin was
    shown; if another admin published first it is no longer the next one (409)."""
    return publish_next_consent_version(db, body.version, admin)


# ---------------------------------------------------------------------------
# POST /api/admin/sessions/revoke-all
# ---------------------------------------------------------------------------
# response_model=None: as for reap_orphan_runs, typed without a response schema.
@router.post("/admin/sessions/revoke-all", response_model=None)
async def revoke_all_sessions(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> dict[str, str | int]:
    """Invalidate every active session ("sign out everyone").

    Bumps the global session epoch in SystemConfig. Each cookie carries the
    epoch in force when it was minted, so the next request from any existing
    session -- including the admin who pressed this -- fails the epoch check in
    get_current_user (and the websocket auth) and is bounced to login. This is
    the only way to revoke stateless signed-cookie sessions before their TTL --
    use it after a credential leak, a permissions change, or to force re-auth.
    """
    new_epoch = bump_session_epoch(db, admin)
    return {
        "message": "All sessions revoked. Everyone must log in again.",
        "session_epoch": new_epoch,
    }


# ---------------------------------------------------------------------------
# GET /api/admin/export/{export_type}
# ---------------------------------------------------------------------------
@router.get("/admin/export/{export_type}")
async def export_csv(
    export_type: str,
    db: Session = Depends(get_db),
    viewer: User = Depends(require_view_all_runs),
) -> StreamingResponse:
    """Export data as CSV. Supported types: runs, users, consent, feedback.
    Admins may export any type; staff only _STAFF_EXPORT_TYPES."""
    return StreamingResponse(
        open_export(db, viewer, export_type),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="cviche_{export_type}_{datetime.now().strftime("%Y%m%d")}.csv"'
        },
    )
