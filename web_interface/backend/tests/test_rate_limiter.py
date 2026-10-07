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
