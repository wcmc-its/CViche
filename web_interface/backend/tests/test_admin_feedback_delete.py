"""DELETE /api/admin/feedback/{feedback_id} behavior.

Admins can hard-delete a single feedback submission to purge a garbage/abusive
response that would otherwise pollute the aggregated Feedback Insights. The
endpoint is admin-only (non-admins get 403), removes the row (204), and a
re-delete of the same id returns 404.
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
        role="admin", email="admin@example.com"
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
