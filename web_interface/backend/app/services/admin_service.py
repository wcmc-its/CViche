"""Admin service functions with O(1) aggregation queries."""
from datetime import datetime
from sqlalchemy import func, case
from sqlalchemy.orm import Session

from app.models import User, Run, Feedback
from app.schemas import AdminUser


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
    """Return stats for a single user. Used after update_user to return fresh stats.

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
