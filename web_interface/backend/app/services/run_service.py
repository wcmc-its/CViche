"""Run-related service functions."""
import logging
import os
from datetime import datetime, timedelta

from sqlalchemy.orm import Session
from app.models import Run, Step, User
from app.errors import not_found, forbidden

logger = logging.getLogger(__name__)

# A run older than this while still marked "running" at startup is treated as
# orphaned by a server restart. Generous relative to the ~15-20 min a real run
# takes, so a sibling replica's genuinely in-flight run is never swept.
DEFAULT_STALE_RUN_MINUTES = 60


def reconcile_stale_runs(db: Session) -> int:
    """Mark orphaned "running" runs as failed. Called once at startup.

    A run executes as an in-process background task (see runs.py:start_run). If
    the pod recycles mid-run -- which happens on EKS -- the task dies but the DB
    row stays status="running" forever and the UI counts elapsed time upward
    with no resolution. On startup we sweep runs still marked "running" whose
    started_at is older than the threshold, marking them (and any still-running
    steps) failed so the user gets a clear outcome and can restart.

    Age-based rather than "any running run" so that, with multiple replicas, a
    sibling's genuinely in-flight run is not killed. Returns the count swept.
    """
    try:
        minutes = int(os.environ.get("CVICHE_STALE_RUN_MINUTES", DEFAULT_STALE_RUN_MINUTES))
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
    for run in stale_runs:
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

    db.commit()
    logger.info("Reconciled %d stale run(s) older than %d min at startup", len(stale_runs), minutes)
    return len(stale_runs)


def check_run_access(run_id: str, current_user: User, db: Session) -> Run:
    """Verify run exists and user has access. Returns the Run.

    Raises:
        HTTPException 404 if run not found.
        HTTPException 403 if user does not own the run and is not admin.
    """
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise not_found("Run not found")
    if run.user_id and run.user_id != current_user.id and current_user.role != "admin":
        raise forbidden("Access denied")
    return run
