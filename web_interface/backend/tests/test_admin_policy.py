"""admin_policy: the admin safety rules as pure functions (#337).

No database and no FastAPI: every input is a plain value. The HTTP-level
behaviour (the 422 each refusal becomes, and the counts update_user reads) is
pinned in test_admin_feedback_delete.py.
"""
import typing

import pytest

from app.models import UserRole, UserStatus
from app.schemas import AdminUserUpdate
from app.services.admin_policy import (
    LAST_ACTIVE_ADMIN,
    LAST_ADMIN,
    SELF_DEMOTION,
    SELF_DISABLE,
    role_change_refusal,
    status_change_refusal,
)


def test_refusal_messages_are_the_api_error_text():
    """These strings are the 422 bodies the admin UI shows; rewording one is an API change."""
    assert (SELF_DEMOTION, LAST_ADMIN, SELF_DISABLE, LAST_ACTIVE_ADMIN) == (
        "Cannot remove your own admin role.",
        "Cannot remove the last admin.",
        "Cannot disable your own account.",
        "Cannot disable the last active admin.",
    )


@pytest.mark.parametrize("current, new, is_self, active_admins, expected", [
    # admin -> user: the one guarded role change
    ("admin", "user", False, 2, None),
    ("admin", "user", True, 5, SELF_DEMOTION),        # never yourself, however many admins
    ("admin", "user", False, 1, LAST_ADMIN),
    ("admin", "user", False, 0, LAST_ADMIN),          # e.g. a disabled admin, nobody active
    ("admin", "user", True, 1, SELF_DEMOTION),        # the self rule is checked first
    # every other move is unguarded, even with no admin at all
    ("user", "admin", False, 0, None),
    ("staff", "user", False, 0, None),
    ("staff", "admin", True, 0, None),
    ("admin", "admin", True, 1, None),
])
def test_role_change_refusal(current, new, is_self, active_admins, expected):
    assert role_change_refusal(current, new, is_self=is_self, active_admin_count=active_admins) == expected


def test_role_change_refusal_accepts_the_userrole_enum():
    assert role_change_refusal(UserRole.ADMIN, UserRole.USER, is_self=False, active_admin_count=1) == LAST_ADMIN


@pytest.mark.parametrize("role, new_status, is_self, other_active_admins, expected", [
    # disabling: the one guarded status change
    ("admin", "disabled", False, 1, None),
    ("admin", "disabled", True, 3, SELF_DISABLE),     # never yourself, however many admins
    ("admin", "disabled", False, 0, LAST_ACTIVE_ADMIN),
    ("admin", "disabled", True, 0, SELF_DISABLE),     # the self rule is checked first
    ("user", "disabled", False, 0, None),             # a non-admin needs no other admin
    ("staff", "disabled", False, 0, None),
    # re-enabling is never guarded
    ("admin", "active", False, 0, None),
    ("admin", "active", True, 0, None),
])
def test_status_change_refusal(role, new_status, is_self, other_active_admins, expected):
    assert status_change_refusal(
        role, new_status, is_self=is_self, other_active_admin_count=other_active_admins
    ) == expected


def test_status_change_refusal_accepts_the_userstatus_enum():
    assert status_change_refusal(
        UserRole.ADMIN, UserStatus.DISABLED, is_self=False, other_active_admin_count=0
    ) == LAST_ACTIVE_ADMIN


@pytest.mark.parametrize("field, vocabulary", [("role", UserRole), ("status", UserStatus)])
def test_admin_user_update_literals_stay_inside_the_enums(field, vocabulary):
    """AdminUserUpdate keeps role/status as pydantic Literals (the request
    body's allowed set), so they can't name the enum. Every value they allow
    must still be a member, or the policy above compares against a value no
    enum spells (#346)."""
    annotation = AdminUserUpdate.model_fields[field].annotation
    literal = next(arg for arg in typing.get_args(annotation) if typing.get_origin(arg) is typing.Literal)
    assert set(typing.get_args(literal)) <= {member.value for member in vocabulary}
