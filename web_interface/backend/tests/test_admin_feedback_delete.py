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

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")


def _seed_feedback(db):
    """Insert a user + run + one feedback row, returning the feedback id."""
    from app.models import User, Run, Feedback

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
    from app.main import app
    from app.auth import require_admin

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
    from app.main import app
    from app.auth import get_current_user

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


def test_admin_delete_removes_row(client, db):
    """An admin delete returns 204 and the row is gone."""
    from app.models import Feedback

    fb_id = _seed_feedback(db)

    resp = _as_admin(client, lambda: client.delete(f"/api/admin/feedback/{fb_id}"))

    assert resp.status_code == 204
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
    from app.main import app
    from app.auth import require_admin
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
