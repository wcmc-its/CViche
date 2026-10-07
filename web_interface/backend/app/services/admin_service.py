"""Admin service functions with O(1) aggregation queries, and the admin user update."""
import json
import logging
from datetime import datetime
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.errors import not_found, validation_error
from app.models import User, Run, Step, Feedback
from app.schemas import AdminStepAvg, AdminUser, AdminUserUpdate
from app.services import admin_policy

logger = logging.getLogger(__name__)


def get_users_with_stats(db: Session) -> list[AdminUser]:
    """Return all users with per-user stats using O(1) aggregation queries.

    Instead of 5N+1 individual queries (5 per user in a loop), this uses:
    1. One subquery for run stats (count, sum, filtered counts) grouped by user_id
    2. One subquery for feedback counts grouped by user_id
    3. One main query joining users with both subqueries

    Compatible with both SQLite and MariaDB/MySQL.
    """
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    # Subquery: per-user run stats
    run_stats = (
        db.query(
            Run.user_id,
            func.count(Run.id).label("total_runs"),
            func.coalesce(func.sum(Run.total_cost), 0.0).label("total_cost"),
            func.count(case((Run.status == "complete", Run.id))).label("completed_runs"),
            func.count(case((Run.started_at >= today_start, Run.id))).label("runs_today"),
        )
        .group_by(Run.user_id)
        .subquery()
    )

    # Subquery: per-user feedback count
    fb_stats = (
        db.query(
            Feedback.user_id,
            func.count(Feedback.id).label("feedback_count"),
        )
        .group_by(Feedback.user_id)
        .subquery()
    )

    # Join users with both stat subqueries
    rows = (
        db.query(
            User,
            func.coalesce(run_stats.c.total_runs, 0).label("total_runs"),
            func.coalesce(run_stats.c.total_cost, 0.0).label("total_cost"),
            func.coalesce(run_stats.c.completed_runs, 0).label("completed_runs"),
            func.coalesce(run_stats.c.runs_today, 0).label("runs_today"),
            func.coalesce(fb_stats.c.feedback_count, 0).label("feedback_count"),
        )
        .outerjoin(run_stats, User.id == run_stats.c.user_id)
        .outerjoin(fb_stats, User.id == fb_stats.c.user_id)
        .order_by(User.created_at.desc())
        .all()
    )

    return [
        AdminUser(
            id=user.id,
            cwid=user.cwid,
            email=user.email,
            display_name=user.display_name,
            role=user.role,
            status=user.status,
            daily_limit=user.daily_limit,
            monthly_limit=user.monthly_limit,
            runs_today=runs_today,
            total_runs=total_runs,
            total_cost=round(float(total_cost), 4),
            feedback_count=feedback_count,
            completed_run_count=completed_runs,
            last_active_at=user.last_active_at,
            created_at=user.created_at,
        )
        for user, total_runs, total_cost, completed_runs, runs_today, feedback_count in rows
    ]


def get_single_user_stats(user: User, db: Session) -> dict:
    """Return stats for a single user. Used after apply_user_update to return fresh stats.

    Returns dict with keys matching AdminUser stat fields.
    Uses individual queries (not subqueries) since it's for a single user -- the overhead
    is constant and avoids the complexity of subqueries for one row.
    """
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    runs_today = (
        db.query(func.count(Run.id))
        .filter(Run.user_id == user.id, Run.started_at >= today_start)
        .scalar() or 0
    )
    total_runs = (
        db.query(func.count(Run.id))
        .filter(Run.user_id == user.id)
        .scalar() or 0
    )
    total_cost = (
        db.query(func.sum(Run.total_cost))
        .filter(Run.user_id == user.id)
        .scalar() or 0.0
    )
    feedback_count = (
        db.query(func.count(Feedback.id))
        .filter(Feedback.user_id == user.id)
        .scalar() or 0
    )
    completed_run_count = (
        db.query(func.count(Run.id))
        .filter(Run.user_id == user.id, Run.status == "complete")
        .scalar() or 0
    )

    return {
        "runs_today": runs_today,
        "total_runs": total_runs,
        "total_cost": round(float(total_cost), 4),
        "feedback_count": feedback_count,
        "completed_run_count": completed_run_count,
    }


def _active_admin_count(db: Session, *, excluding: int | None = None) -> int:
    """Active admins, less the user ``excluding`` names when given."""
    query = db.query(func.count(User.id)).filter(User.role == "admin", User.status == "active")
    if excluding is not None:
        query = query.filter(User.id != excluding)
    return query.scalar()


def _raise_if_refused(refusal: str | None) -> None:
    """An admin_policy refusal is a 422 carrying its message."""
    if refusal is not None:
        raise validation_error(refusal)


def apply_user_update(db: Session, user_id: int, update: AdminUserUpdate, admin: User) -> AdminUser:
    """PUT /api/admin/users/{id}: change a user's role, status or limits as ``admin``.

    The safety rules are admin_policy's; a refusal is a 422 with nothing
    committed. Commits, writes one admin_user_updated audit line with each
    changed field's old and new value (none for a no-op), and returns the user
    with fresh stats.
    """
    target = db.query(User).filter(User.id == user_id).first()
    if not target:
        raise not_found("User not found.")

    changes: dict[str, dict[str, object]] = {}

    if update.role is not None and update.role != target.role:
        _raise_if_refused(admin_policy.role_change_refusal(
            target.role, update.role, is_self=target.id == admin.id,
            active_admin_count=_active_admin_count(db),
        ))
        changes["role"] = {"old": target.role, "new": update.role}
        target.role = update.role

    # After the role change: the status rule judges the role this request sets.
    if update.status is not None and update.status != target.status:
        _raise_if_refused(admin_policy.status_change_refusal(
            target.role, update.status, is_self=target.id == admin.id,
            other_active_admin_count=_active_admin_count(db, excluding=target.id),
        ))
        changes["status"] = {"old": target.status, "new": update.status}
        target.status = update.status

    # 0 (or less) resets a limit to the system default.
    if update.daily_limit is not None:
        changes["daily_limit"] = {"old": target.daily_limit, "new": update.daily_limit}
        target.daily_limit = update.daily_limit if update.daily_limit > 0 else None

    if update.monthly_limit is not None:
        changes["monthly_limit"] = {
            "old": target.monthly_limit,
            "new": update.monthly_limit,
        }
        target.monthly_limit = update.monthly_limit if update.monthly_limit > 0 else None

    db.commit()
    db.refresh(target)

    if changes:
        logger.info(
            "admin_user_updated: admin=%s target_user=%s changes=%s",
            admin.email,
            target.email,
            json.dumps(changes),
        )

    stats = get_single_user_stats(target, db)

    return AdminUser(
        id=target.id,
        cwid=target.cwid,
        email=target.email,
        display_name=target.display_name,
        role=target.role,
        status=target.status,
        daily_limit=target.daily_limit,
        monthly_limit=target.monthly_limit,
        runs_today=stats["runs_today"],
        total_runs=stats["total_runs"],
        total_cost=stats["total_cost"],
        feedback_count=stats["feedback_count"],
        completed_run_count=stats["completed_run_count"],
        last_active_at=target.last_active_at,
        created_at=target.created_at,
    )


def get_step_avg_seconds(db: Session) -> list[AdminStepAvg]:
    """Average duration per pipeline stage over the steps of completed runs.

    Failed runs are excluded so a stage that died early doesn't skew the
    average. One grouped query, ordered by the pipeline's step order.
    """
    step_rows = (
        db.query(
            Step.stage_id,
            func.min(Step.step_name),
            func.avg(Step.duration_seconds),
        )
        .join(Run, Run.id == Step.run_id)
        .filter(
            Run.status == "complete",
            Step.duration_seconds.isnot(None),
            Step.stage_id.isnot(None),
        )
        .group_by(Step.stage_id)
        .order_by(func.min(Step.step_number))
        .all()
    )
    return [
        AdminStepAvg(stage_id=stage_id, step_name=name, avg_seconds=round(float(avg), 1))
        for stage_id, name, avg in step_rows
    ]
