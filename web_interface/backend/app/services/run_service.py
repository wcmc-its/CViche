"""Run-related service functions."""
from sqlalchemy.orm import Session
from app.models import Run, User
from app.errors import not_found, forbidden


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
