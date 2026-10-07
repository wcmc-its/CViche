"""DELETE /api/admin/feedback/{feedback_id} and PUT /api/admin/users/{id}.

Admins can hard-delete a single feedback submission to purge a garbage/abusive
response that would otherwise pollute the aggregated Feedback Insights. The
endpoint is admin-only (non-admins get 403), removes the row (204), and a
re-delete of the same id returns 404.

Also covers PUT /api/admin/users/{id} role/status validation (#409): the
update_user last-admin guards compare body.role/body.status against literal
strings, so an out-of-set value used to bypass them entirely.
"""
import os
from datetime import datetime
from types import SimpleNamespace

import pytest

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")


def _seed_feedback(db):
    """Insert a user + run + one feedback row, returning the feedback id."""
    from app.models import Feedback, Run, User

    user = User(email="reviewer@example.com", display_name="Reviewer", role="user")
    db.add(user)
    db.flush()  # assign user.id without ending the transaction

    db.add(
        Run(
            id="_WY5HW",
            filename="cv.docx",
            file_type="docx",
            status="complete",
            user_id=user.id,
            started_at=datetime(2026, 6, 16, 12, 0, 0),
            completed_at=datetime(2026, 6, 16, 12, 1, 0),
        )
    )
    fb = Feedback(
        run_id="_WY5HW",
        user_id=user.id,
        reviewer_role="self",
        overall_usefulness=1,
        manual_conversion_effort="8+ hours",
        correction_effort="8+ hours",
        biggest_issue="this tool is garbage",
        likelihood_to_recommend=1,
    )
    db.add(fb)
    db.commit()
    db.refresh(fb)
    return fb.id


def _as_admin(client, fn):
    from app.auth import require_admin
    from app.main import app

    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
        id=-1, role="admin", email="admin@example.com"
    )
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(require_admin, None)


def _as_user(client, fn):
    """Override the underlying current-user dep so the REAL require_admin runs
    and raises 403 -- this exercises the actual authorization guard."""
    from app.auth import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        role="user", email="user@example.com"
    )
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_non_admin_cannot_delete_feedback(client, db):
    """A non-admin caller is rejected with 403 and the row survives."""
    from app.models import Feedback

    fb_id = _seed_feedback(db)

    resp = _as_user(client, lambda: client.delete(f"/api/admin/feedback/{fb_id}"))

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    # Row untouched
    assert db.query(Feedback).filter(Feedback.id == fb_id).first() is not None


def _as_staff(client, fn):
    """Staff (read-only) through the REAL require_admin, like _as_user."""
    from app.auth import get_current_user
    from app.main import app

    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
        id=-2, role="staff", email="staff@example.com"
    )
    try:
        return fn()
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_staff_cannot_delete_feedback(client, db):
    """Staff read Feedback Insights but cannot delete from it."""
    from app.models import Feedback

    fb_id = _seed_feedback(db)

    resp = _as_staff(client, lambda: client.delete(f"/api/admin/feedback/{fb_id}"))

    assert resp.status_code == 403
    assert db.query(Feedback).filter(Feedback.id == fb_id).first() is not None


@pytest.mark.parametrize("method, path", [
    ("get", "/api/admin/stats"),           # carries cost
    ("get", "/api/admin/users"),           # user management
    ("put", "/api/admin/users/1"),
    ("get", "/api/admin/config"),          # admin config
    ("put", "/api/admin/config"),
    ("delete", "/api/admin/runs/_WY5HW"),  # delete run
    ("get", "/api/admin/runs"),            # carries raw total_cost
    ("post", "/api/admin/sessions/revoke-all"),
])
def test_staff_is_forbidden_admin_only_endpoints(client, db, method, path):
    _seed_feedback(db)

    resp = _as_staff(client, lambda: getattr(client, method)(path))

    assert resp.status_code == 403


def test_admin_delete_removes_row(client, db):
    """An admin delete returns 204 and the row is gone."""
    from app.models import Feedback

    fb_id = _seed_feedback(db)

    resp = _as_admin(client, lambda: client.delete(f"/api/admin/feedback/{fb_id}"))

    assert resp.status_code == 204
    db.rollback()  # discard uncommitted state: only a committed delete survives
    assert db.query(Feedback).filter(Feedback.id == fb_id).first() is None


def test_redelete_returns_404(client, db):
    """Re-deleting an already-deleted (or never-existent) id returns 404."""
    fb_id = _seed_feedback(db)

    first = _as_admin(client, lambda: client.delete(f"/api/admin/feedback/{fb_id}"))
    assert first.status_code == 204

    second = _as_admin(client, lambda: client.delete(f"/api/admin/feedback/{fb_id}"))
    assert second.status_code == 404
    assert second.json()["detail"]["error"] == "not_found"


def _seed_lone_admin(db):
    """Insert a single active admin user, returning it."""
    from app.models import User

    user = User(
        email="lone-admin@example.com",
        display_name="Lone Admin",
        role="admin",
        status="active",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_update_user_rejects_invalid_role_value(client, db):
    """role='viewer' 422s before the handler runs, so the last-admin guard
    (which only matches the literal 'user'/'admin') never sees it (#409)."""
    admin = _seed_lone_admin(db)

    resp = _as_admin(
        client,
        lambda: client.put(f"/api/admin/users/{admin.id}", json={"role": "viewer"}),
    )

    assert resp.status_code == 422
    db.refresh(admin)
    assert admin.role == "admin"


def test_update_user_rejects_invalid_status_value(client, db):
    """status='Disabled' (wrong case) 422s before the handler runs, so the
    disable-last-admin guard (which only matches literal 'disabled') never
    sees it (#409)."""
    admin = _seed_lone_admin(db)

    resp = _as_admin(
        client,
        lambda: client.put(
            f"/api/admin/users/{admin.id}", json={"status": "Disabled"}
        ),
    )

    assert resp.status_code == 422
    db.refresh(admin)
    assert admin.status == "active"


def test_update_user_last_admin_guard_still_blocks_valid_demotion(client, db):
    """A valid role value ('user') on the last admin still trips the
    last-admin guard -- the Literal constraint doesn't touch this path."""
    admin = _seed_lone_admin(db)

    resp = _as_admin(
        client,
        lambda: client.put(f"/api/admin/users/{admin.id}", json={"role": "user"}),
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["message"] == "Cannot remove the last admin."
    db.refresh(admin)
    assert admin.role == "admin"


def test_update_user_accepts_valid_role_and_status_values(client, db):
    """Valid role/status values still update normally (no false rejection)."""
    from app.models import User

    admin = _seed_lone_admin(db)
    second_admin = User(
        email="second-admin@example.com",
        display_name="Second Admin",
        role="admin",
        status="active",
    )
    db.add(second_admin)
    db.commit()
    db.refresh(second_admin)

    resp = _as_admin(
        client,
        lambda: client.put(
            f"/api/admin/users/{second_admin.id}",
            json={"role": "user", "status": "disabled"},
        ),
    )

    assert resp.status_code == 200
    db.refresh(second_admin)
    assert second_admin.role == "user"
    assert second_admin.status == "disabled"


def test_update_user_blocks_self_demotion_even_with_other_admins(client, db):
    """An admin cannot demote themselves, even when another admin exists."""
    from app.auth import require_admin
    from app.main import app
    from app.models import User

    me = _seed_lone_admin(db)
    db.add(User(email="other-admin@example.com", display_name="Other", role="admin", status="active"))
    db.commit()

    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
        id=me.id, role="admin", email=me.email
    )
    try:
        resp = client.put(f"/api/admin/users/{me.id}", json={"role": "user"})
    finally:
        app.dependency_overrides.pop(require_admin, None)

    assert resp.status_code == 422
    assert resp.json()["detail"]["message"] == "Cannot remove your own admin role."
    db.refresh(me)
    assert me.role == "admin"


def test_admin_delete_logs_the_acting_admin_and_the_run(client, db, caplog):
    """The delete is auditable: the log line names the admin, the feedback id
    and the run it belonged to. An unknown id is a 404 with its own message."""
    import logging

    fb_id = _seed_feedback(db)

    with caplog.at_level(logging.INFO):
        resp = _as_admin(client, lambda: client.delete(f"/api/admin/feedback/{fb_id}"))
        missing = _as_admin(client, lambda: client.delete(f"/api/admin/feedback/{fb_id}"))

    assert resp.status_code == 204
    audit = [r.getMessage() for r in caplog.records if r.getMessage().startswith("admin_feedback_deleted")]
    assert audit == [f"admin_feedback_deleted: admin=admin@example.com feedback_id={fb_id} run_id=_WY5HW"]
    assert missing.json()["detail"]["message"] == "Feedback not found."


# ---------------------------------------------------------------------------
# PUT /api/admin/users/{id}: the admin safety rules, limits and audit line,
# pinned before the rules move out of the route handler (#337, #335).
# ---------------------------------------------------------------------------

def _seed_user(db, email, role="user", status="active"):
    from app.models import User

    user = User(email=email, display_name=email.split("@")[0], role=role, status=status)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _put_as(client, acting, user_id, body):
    """PUT as `acting` (a seeded user) through an overridden require_admin."""
    from app.auth import require_admin
    from app.main import app

    app.dependency_overrides[require_admin] = lambda: SimpleNamespace(
        id=acting.id, role="admin", email=acting.email
    )
    try:
        return client.put(f"/api/admin/users/{user_id}", json=body)
    finally:
        app.dependency_overrides.pop(require_admin, None)


def test_update_user_unknown_id_is_404(client, db):
    resp = _as_admin(client, lambda: client.put("/api/admin/users/4242", json={"role": "user"}))

    assert resp.status_code == 404
    assert resp.json()["detail"] == {"error": "not_found", "message": "User not found."}


def test_update_user_blocks_disabling_yourself_even_with_other_admins(client, db):
    me = _seed_user(db, "me@example.com", role="admin")
    _seed_user(db, "other@example.com", role="admin")

    resp = _put_as(client, me, me.id, {"status": "disabled"})

    assert resp.status_code == 422
    assert resp.json()["detail"]["message"] == "Cannot disable your own account."
    db.refresh(me)
    assert me.status == "active"


def test_update_user_blocks_disabling_the_last_active_admin(client, db):
    """Another admin exists but is disabled, so it does not count."""
    target = _seed_user(db, "target@example.com", role="admin")
    _seed_user(db, "dormant@example.com", role="admin", status="disabled")

    resp = _as_admin(client, lambda: client.put(f"/api/admin/users/{target.id}", json={"status": "disabled"}))

    assert resp.status_code == 422
    assert resp.json()["detail"]["message"] == "Cannot disable the last active admin."
    db.refresh(target)
    assert target.status == "active"


def test_update_user_allows_disabling_an_admin_when_another_is_active(client, db):
    target = _seed_user(db, "target@example.com", role="admin")
    _seed_user(db, "other@example.com", role="admin")

    resp = _as_admin(client, lambda: client.put(f"/api/admin/users/{target.id}", json={"status": "disabled"}))

    assert resp.status_code == 200
    assert resp.json()["status"] == "disabled"


def test_update_user_demotion_counts_only_active_admins(client, db):
    """Demoting a disabled admin is refused while only one admin is active:
    the count is of active admins, and a disabled target is not one of them."""
    dormant = _seed_user(db, "dormant@example.com", role="admin", status="disabled")
    _seed_user(db, "active@example.com", role="admin")

    resp = _as_admin(client, lambda: client.put(f"/api/admin/users/{dormant.id}", json={"role": "user"}))

    assert resp.status_code == 422
    assert resp.json()["detail"]["message"] == "Cannot remove the last admin."


def test_update_user_disable_rule_sees_a_role_set_in_the_same_request(client, db):
    """role is applied before status, so promoting a user and disabling them
    in one request is judged as disabling an admin -- here the last one."""
    target = _seed_user(db, "member@example.com")

    resp = _as_admin(
        client,
        lambda: client.put(f"/api/admin/users/{target.id}", json={"role": "admin", "status": "disabled"}),
    )

    assert resp.status_code == 422
    assert resp.json()["detail"]["message"] == "Cannot disable the last active admin."
    db.refresh(target)
    assert (target.role, target.status) == ("user", "active")


def test_update_user_only_guards_admin_to_user(client, db):
    """No admin exists at all, yet a staff member can still be moved to user
    or to admin: only demoting an admin is guarded."""
    staff = _seed_user(db, "staff@example.com", role="staff")
    other = _seed_user(db, "other-staff@example.com", role="staff")

    to_user = _as_admin(client, lambda: client.put(f"/api/admin/users/{staff.id}", json={"role": "user"}))
    to_admin = _as_admin(client, lambda: client.put(f"/api/admin/users/{other.id}", json={"role": "admin"}))

    assert (to_user.status_code, to_user.json()["role"]) == (200, "user")
    assert (to_admin.status_code, to_admin.json()["role"]) == (200, "admin")


def test_update_user_re_enabling_an_admin_is_never_guarded(client, db):
    dormant = _seed_user(db, "dormant@example.com", role="admin", status="disabled")

    resp = _as_admin(client, lambda: client.put(f"/api/admin/users/{dormant.id}", json={"status": "active"}))

    assert (resp.status_code, resp.json()["status"]) == (200, "active")


def test_update_user_limits_zero_or_negative_reset_to_the_system_default(client, db):
    """Each update is committed: a rollback after the request keeps it."""
    target = _seed_user(db, "member@example.com")

    set_resp = _as_admin(
        client,
        lambda: client.put(f"/api/admin/users/{target.id}", json={"daily_limit": 5, "monthly_limit": 20}),
    )
    db.rollback()
    db.refresh(target)
    assert (target.daily_limit, target.monthly_limit) == (5, 20)

    reset_resp = _as_admin(
        client,
        lambda: client.put(f"/api/admin/users/{target.id}", json={"daily_limit": 0, "monthly_limit": -1}),
    )

    assert (set_resp.json()["daily_limit"], set_resp.json()["monthly_limit"]) == (5, 20)
    assert (reset_resp.json()["daily_limit"], reset_resp.json()["monthly_limit"]) == (None, None)
    db.rollback()
    db.refresh(target)
    assert (target.daily_limit, target.monthly_limit) == (None, None)


def test_update_user_audit_line_lists_every_change_in_order(client, db, caplog):
    """One line per update with old/new per field; a limit logs the value as
    sent (0), not the None it is stored as."""
    import logging

    _seed_user(db, "keeper@example.com", role="admin")
    target = _seed_user(db, "second@example.com", role="admin")

    with caplog.at_level(logging.INFO):
        resp = _as_admin(
            client,
            lambda: client.put(
                f"/api/admin/users/{target.id}",
                json={"role": "user", "status": "disabled", "daily_limit": 0, "monthly_limit": 20},
            ),
        )

    assert resp.status_code == 200
    audit = [r.getMessage() for r in caplog.records if r.getMessage().startswith("admin_user_updated")]
    assert audit == [
        "admin_user_updated: admin=admin@example.com target_user=second@example.com changes="
        '{"role": {"old": "admin", "new": "user"}, "status": {"old": "active", "new": "disabled"}, '
        '"daily_limit": {"old": null, "new": 0}, "monthly_limit": {"old": null, "new": 20}}'
    ]


def test_update_user_without_a_change_logs_nothing(client, db, caplog):
    import logging

    admin = _seed_lone_admin(db)

    with caplog.at_level(logging.INFO):
        resp = _as_admin(
            client,
            lambda: client.put(f"/api/admin/users/{admin.id}", json={"role": "admin", "status": "active"}),
        )

    assert resp.status_code == 200
    assert not [r for r in caplog.records if r.getMessage().startswith("admin_user_updated")]


def test_update_user_response_carries_the_users_fresh_stats(client, db):
    from app.models import Feedback, Run

    target = _seed_user(db, "member@example.com")
    db.add_all([
        Run(id="STAT1", filename="a.docx", file_type="docx", status="complete",
            user_id=target.id, started_at=datetime.now(), total_cost=1.23456),
        Run(id="STAT2", filename="b.docx", file_type="docx", status="failed",
            user_id=target.id, started_at=datetime(2026, 1, 2), total_cost=1.0),
        Feedback(run_id="STAT1", user_id=target.id, reviewer_role="self", overall_usefulness=3,
                 manual_conversion_effort="1 hour", correction_effort="1 hour",
                 biggest_issue="none", likelihood_to_recommend=3),
    ])
    db.commit()

    resp = _as_admin(client, lambda: client.put(f"/api/admin/users/{target.id}", json={"daily_limit": 3}))

    body = resp.json()
    assert (body["id"], body["email"], body["daily_limit"]) == (target.id, "member@example.com", 3)
    assert (body["total_runs"], body["completed_run_count"], body["runs_today"]) == (2, 1, 1)
    assert (body["total_cost"], body["feedback_count"]) == (2.2346, 1)
