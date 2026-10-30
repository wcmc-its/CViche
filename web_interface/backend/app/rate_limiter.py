"""Rate limiting for pipeline runs based on per-user and system-wide limits."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.config_loader import get_config_value
from app.models import Run, RunBatch, User, UserRole

ET = ZoneInfo("America/New_York")


def get_effective_limits(user: User, db: Session) -> tuple[int | None, int | None]:
    """Return (daily_limit, monthly_limit). None = unlimited (admin).
    Uses per-user overrides if set, otherwise system defaults from SystemConfig."""
    if user.role == UserRole.ADMIN:
        return (None, None)

    # Per-user overrides take precedence
    daily = user.daily_limit
    monthly = user.monthly_limit

    # Fall back to system defaults
    if daily is None:
        daily = get_config_value(db, "rate_limit_daily")
        if daily is None:
            daily = 10  # hardcoded fallback
    if monthly is None:
        monthly = get_config_value(db, "rate_limit_monthly")
        if monthly is None:
            monthly = 50  # hardcoded fallback

    return (daily, monthly)


def _window_starts() -> tuple[datetime, datetime]:
    """Where the daily and monthly windows begin: midnight today and midnight
    on the 1st of this month, America/New_York."""
    now_et = datetime.now(ET)
    day_start = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = now_et.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (day_start, month_start)


def get_run_counts(user_id: int, db: Session) -> tuple[int, int]:
    """Return (daily_count, monthly_count) of runs for this user.
    Daily resets at midnight America/New_York.
    Monthly resets at midnight ET on the 1st."""
    day_start, month_start = _window_starts()

    base_query = db.query(func.count(Run.id)).filter(Run.user_id == user_id)

    daily_count = base_query.filter(Run.started_at >= day_start).scalar() or 0
    monthly_count = base_query.filter(Run.started_at >= month_start).scalar() or 0

    return (daily_count, monthly_count)


def get_unfilled_batch_counts(user_id: int, db: Session) -> tuple[int, int]:
    """Return (daily, monthly) files this user's batches have not uploaded yet
    (#1202): per batch, ``files_submitted`` minus the runs created in it.
    A batch counts in the windows its ``created_at`` falls in, the same
    boundaries get_run_counts applies to ``Run.started_at``."""
    day_start, month_start = _window_starts()
    runs_in_batch = select(func.count(Run.id)).where(Run.batch_id == RunBatch.id).scalar_subquery()
    unfilled = RunBatch.files_submitted - runs_in_batch
    # A batch holding more runs than files has none left to upload: it counts
    # 0, so it never offsets another batch's unfilled files.
    unfilled_or_zero = case((unfilled > 0, unfilled), else_=0)

    base_query = db.query(func.coalesce(func.sum(unfilled_or_zero), 0)).filter(RunBatch.user_id == user_id)

    daily = base_query.filter(RunBatch.created_at >= day_start).scalar()
    monthly = base_query.filter(RunBatch.created_at >= month_start).scalar()

    return (int(daily), int(monthly))


def _limit_message(period: str, limit: int, used: int, requested: int) -> str:
    """The 429 message: one run reports the limit as reached (the wording
    /upload has always used); a batch of several says how many remain."""
    if requested == 1:
        return f"{period} limit of {limit} runs reached."
    remaining = max(0, limit - used)
    return (
        f"This batch needs {requested} runs, but only {remaining} of your "
        f"{period.lower()} limit of {limit} remain."
    )


def check_rate_limit(
    user: User, db: Session, requested: int = 1, *, include_unfilled_batches: bool = False,
) -> dict | None:
    """Check if user can create ``requested`` new runs (1 for /upload, the
    batch size for POST /batches, #1114). Returns None if allowed, or an
    error dict {error, message, details} if rate limited.

    include_unfilled_batches (POST /batches, #1202): the files the user's
    earlier batches have not uploaded yet count as used, in ``details.used``
    too, so a batch nobody uploads to still holds its share of the quota.
    /upload leaves it off: its run fills one of those files, and counting the
    file as well would charge it twice."""
    daily_limit, monthly_limit = get_effective_limits(user, db)

    # Admins are unlimited
    if daily_limit is None and monthly_limit is None:
        return None

    daily_count, monthly_count = get_run_counts(user.id, db)
    if include_unfilled_batches:
        unfilled_daily, unfilled_monthly = get_unfilled_batch_counts(user.id, db)
        daily_count += unfilled_daily
        monthly_count += unfilled_monthly

    if monthly_limit is not None and monthly_count + requested > monthly_limit:
        now_et = datetime.now(ET)
        # Next month reset
        if now_et.month == 12:
            resets_at = now_et.replace(year=now_et.year + 1, month=1, day=1,
                                       hour=0, minute=0, second=0, microsecond=0)
        else:
            resets_at = now_et.replace(month=now_et.month + 1, day=1,
                                       hour=0, minute=0, second=0, microsecond=0)
        return {
            "error": "rate_limited",
            "message": _limit_message("Monthly", monthly_limit, monthly_count, requested),
            "details": {
                "limit_type": "monthly",
                "limit": monthly_limit,
                "used": monthly_count,
                "resets_at": resets_at.isoformat(),
            },
        }

    if daily_limit is not None and daily_count + requested > daily_limit:
        now_et = datetime.now(ET)
        tomorrow = now_et.replace(hour=0, minute=0, second=0, microsecond=0)
        resets_at = tomorrow + timedelta(days=1)
        return {
            "error": "rate_limited",
            "message": _limit_message("Daily", daily_limit, daily_count, requested),
            "details": {
                "limit_type": "daily",
                "limit": daily_limit,
                "used": daily_count,
                "resets_at": resets_at.isoformat(),
            },
        }

    return None


def get_quota(user: User, db: Session) -> dict:
    """Return quota info for display: daily_limit, daily_used, daily_remaining,
    monthly_limit, monthly_used, monthly_remaining, is_admin, resets_at."""
    daily_limit, monthly_limit = get_effective_limits(user, db)
    is_admin = user.role == UserRole.ADMIN

    if is_admin:
        return {
            "daily_limit": None,
            "daily_used": 0,
            "daily_remaining": None,
            "monthly_limit": None,
            "monthly_used": 0,
            "monthly_remaining": None,
            "is_admin": True,
        }

    daily_count, monthly_count = get_run_counts(user.id, db)

    daily_remaining = max(0, daily_limit - daily_count) if daily_limit is not None else None
    monthly_remaining = max(0, monthly_limit - monthly_count) if monthly_limit is not None else None

    # Calculate next reset time (daily)
    now_et = datetime.now(ET)
    tomorrow_midnight = now_et.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)

    return {
        "daily_limit": daily_limit,
        "daily_used": daily_count,
        "daily_remaining": daily_remaining,
        "monthly_limit": monthly_limit,
        "monthly_used": monthly_count,
        "monthly_remaining": monthly_remaining,
        "is_admin": False,
        "resets_at": tomorrow_midnight.isoformat(),
    }
