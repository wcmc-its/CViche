"""rate_limiter: who is exempt from the run quota (#346), and the Eastern
day and month windows runs and unfilled batch files are counted in (#798,
#1202).

Only an admin is unlimited. The exemption returns before any database read,
so these cases need no session; a staff or user account with its own
overrides also resolves without one.
"""
import pytest

from app.models import User, UserRole
from app.rate_limiter import get_effective_limits, get_quota

DAILY_OVERRIDE = 3
MONTHLY_OVERRIDE = 7


def _user(role: str) -> User:
    return User(email="quota@example.com", role=role,
                daily_limit=DAILY_OVERRIDE, monthly_limit=MONTHLY_OVERRIDE)


@pytest.mark.parametrize("role", [UserRole.ADMIN, "admin"])
def test_admin_is_unlimited(role):
    assert get_effective_limits(_user(role), db=None) == (None, None)


@pytest.mark.parametrize("role", [UserRole.STAFF, UserRole.USER])
def test_staff_and_user_keep_their_limits(role):
    assert get_effective_limits(_user(role), db=None) == (DAILY_OVERRIDE, MONTHLY_OVERRIDE)


def test_admin_quota_reports_unlimited():
    quota = get_quota(_user(UserRole.ADMIN), db=None)
    assert quota["is_admin"] is True
    assert quota["daily_limit"] is None


def test_daily_count_starts_at_eastern_midnight_not_utc_midnight(db, monkeypatch):
    """#798: Run.started_at is stored as naive UTC. The ET day boundary must be
    converted to UTC before it is compared, so a run at 23:30 ET yesterday
    (03:30 UTC today) is not counted against today's quota."""
    from datetime import datetime, timezone

    from app import rate_limiter
    from app.models import Run

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 11, 12, 0, tzinfo=rate_limiter.ET).astimezone(tz)

    monkeypatch.setattr(rate_limiter, "datetime", _Frozen)
    user = User(email="quota-day@example.com", display_name="Quota Day", role=UserRole.USER)
    db.add(user)
    db.commit()
    for run_id, utc_hour in (("ETDAY1", 3), ("ETDAY2", 5)):  # 23:30 ET Oct 10, 01:30 ET Oct 11
        db.add(Run(id=run_id, filename="a.docx", file_type="docx", status="complete",
                   user_id=user.id,
                   started_at=datetime(2026, 10, 11, utc_hour, 30, tzinfo=timezone.utc)))
    db.commit()

    daily, monthly = rate_limiter.get_run_counts(user.id, db)
    assert (daily, monthly) == (1, 2)


def test_unfilled_batch_files_count_in_the_windows_the_batch_was_created_in(db, monkeypatch):
    """#1202: a batch's unfilled files (files_submitted minus its runs) count
    where its created_at falls, on the same Eastern day and month boundaries
    get_run_counts applies to runs (#798), each boundary inclusive as there."""
    from datetime import datetime, timezone

    from app import rate_limiter
    from app.models import Run, RunBatch

    class _Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 11, 12, 0, tzinfo=rate_limiter.ET).astimezone(tz)

    monkeypatch.setattr(rate_limiter, "datetime", _Frozen)
    user = User(email="quota-batch@example.com", display_name="Quota Batch", role=UserRole.USER)
    db.add(user)
    db.commit()
    for batch_id, files, created_utc in (
        ("LASTMO", 4, datetime(2026, 10, 1, 3, 30, tzinfo=timezone.utc)),   # 23:30 ET Sep 30
        ("MONTH0", 1, datetime(2026, 10, 1, 4, 0, tzinfo=timezone.utc)),    # 00:00 ET Oct 1, the month's start
        ("YESTDY", 2, datetime(2026, 10, 11, 3, 30, tzinfo=timezone.utc)),  # 23:30 ET Oct 10
        ("TODAY0", 1, datetime(2026, 10, 11, 4, 0, tzinfo=timezone.utc)),   # 00:00 ET Oct 11, the day's start
        ("TODAYB", 3, datetime(2026, 10, 11, 5, 30, tzinfo=timezone.utc)),  # 01:30 ET Oct 11
    ):
        db.add(RunBatch(id=batch_id, user_id=user.id, files_submitted=files, created_at=created_utc))
    db.commit()
    for run_id in ("TODAY1", "TODAY2"):  # two of TODAYB's three files uploaded
        db.add(Run(id=run_id, filename="a.docx", file_type="docx", status="complete", user_id=user.id,
                   batch_id="TODAYB", started_at=datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc)))
    db.commit()

    # Daily: TODAY0 1 + TODAYB 1. Monthly: those, MONTH0 1 and YESTDY 2.
    assert rate_limiter.get_unfilled_batch_counts(user.id, db) == (2, 5)
