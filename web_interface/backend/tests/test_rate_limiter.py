"""rate_limiter: who is exempt from the run quota (#346).

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
