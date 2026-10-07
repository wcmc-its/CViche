"""Admin safety rules for PUT /api/admin/users/{id} (#337).

The one definition of when an admin may change a user's role or status. Pure
functions over plain values -- whether the target is the acting admin, role
and status strings, and admin counts the caller has already read -- so each
rule is testable without a database or FastAPI.

Each function returns the refusal message for a change the rules block, or
None when the change is allowed; the caller turns a message into a 422. A
message, not a bool, because each field has two distinct refusals, and the
messages are the API's error text.

frontend/src/components/adminUserRules.ts mirrors the demotion half
(demotionBlock) so the UI disables a change the API would refuse.
"""
from app.models import UserRole

# User.status of an account that cannot sign in (AdminUserUpdate's Literal).
DISABLED_STATUS = "disabled"

SELF_DEMOTION = "Cannot remove your own admin role."
LAST_ADMIN = "Cannot remove the last admin."
SELF_DISABLE = "Cannot disable your own account."
LAST_ACTIVE_ADMIN = "Cannot disable the last active admin."


def role_change_refusal(
    current_role: str, new_role: str, *, is_self: bool, active_admin_count: int
) -> str | None:
    """Why moving a user from ``current_role`` to ``new_role`` is refused, or None.

    Only admin -> user is guarded; a staff or user account moves freely. An
    admin cannot demote themselves, nor the last admin. ``active_admin_count``
    counts every active admin, the target included only while it is active --
    so demoting a disabled admin is still refused while just one is active.
    """
    if current_role != UserRole.ADMIN or new_role != UserRole.USER:
        return None
    if is_self:
        return SELF_DEMOTION
    if active_admin_count <= 1:
        return LAST_ADMIN
    return None


def status_change_refusal(
    role: str, new_status: str, *, is_self: bool, other_active_admin_count: int
) -> str | None:
    """Why setting a user's status to ``new_status`` is refused, or None.

    Only disabling is guarded: nobody may disable their own account, and an
    admin cannot be disabled while no other admin is active. ``role`` is the
    target's role after any role change in the same request (role is applied
    first), and ``other_active_admin_count`` excludes the target.
    """
    if new_status != DISABLED_STATUS:
        return None
    if is_self:
        return SELF_DISABLE
    if role == UserRole.ADMIN and other_active_admin_count < 1:
        return LAST_ACTIVE_ADMIN
    return None
