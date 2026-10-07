"""Admin routes over runs: the paginated listing, hard delete, the orphan
reaper, rescoring and the run-queue diagnostics -- plus deleting one feedback
row, since a feedback row belongs to a run (the feedback CSV is in exports)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import User
from app.schemas import AdminRunsResponse, QualityScoreResult, QueueStatsResponse
from app.services.admin_run_service import (
    hard_delete_feedback, hard_delete_run, list_runs, queue_stats, reap_orphans, score_run,
)

router = APIRouter()


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
) -> AdminRunsResponse:
    """Return all runs paginated, with optional filters."""
    return list_runs(db, offset=offset, limit=limit, user_email=user, status=status)


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
