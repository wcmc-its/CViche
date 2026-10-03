"""POST /api/run/{run_id}/feedback also fires a best-effort Teams notification.

This covers the call-site wiring in feedback_routes.submit_feedback -- notify
is actually called with the right feedback/run/submitter, and a failure there
never affects the already-committed feedback or the 201 response. The
notifications.build_feedback_payload / notify_feedback_submitted helpers
themselves are covered in test_notifications.py; a helper test alone doesn't
prove the route actually calls them with the right arguments.
"""
import os
from contextlib import contextmanager
from datetime import datetime
from unittest.mock import Mock

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

_RUN_ID = "_FDBK1"

_VALID_BODY = {
    "reviewer_role": "self",
    "overall_usefulness": 4,
    "manual_conversion_effort": "1-2 hours",
    "correction_effort": "1-2 hours",
    "likelihood_to_recommend": 5,
}


def _seed_run(db, email="reviewer@example.com"):
    """Insert a user + one of their runs, returning the committed user."""
    from app.models import User, Run

    user = User(email=email, display_name="Reviewer", role="user")
    db.add(user)
    db.flush()  # assign user.id without ending the transaction

    db.add(Run(
        id=_RUN_ID, filename="cv.docx", file_type="docx", status="complete",
        user_id=user.id, started_at=datetime(2026, 6, 16, 12, 0, 0),
        completed_at=datetime(2026, 6, 16, 12, 1, 0),
    ))
    db.commit()
    db.refresh(user)
    return user


@contextmanager
def _as_user(user):
    from app.main import app
    from app.auth import get_current_user

    app.dependency_overrides[get_current_user] = lambda: user
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def test_submit_feedback_notifies_teams_with_run_and_submitter(client, db, monkeypatch):
    user = _seed_run(db)
    notify = Mock()
    monkeypatch.setattr("app.services.notifications.notify_feedback_submitted", notify)

    with _as_user(user):
        resp = client.post(f"/api/run/{_RUN_ID}/feedback", json=_VALID_BODY)

    assert resp.status_code == 201
    notify.assert_called_once()
    feedback_arg, run_arg, submitter_arg = notify.call_args[0]
    assert feedback_arg.run_id == _RUN_ID
    assert run_arg.id == _RUN_ID
    assert submitter_arg == "Reviewer"


def test_submit_feedback_succeeds_even_if_notification_raises(client, db, monkeypatch):
    """The Teams POST is best-effort: a failure there must not roll back the
    feedback or turn the 201 into a 500."""
    user = _seed_run(db)
    monkeypatch.setattr(
        "app.services.notifications.notify_feedback_submitted",
        Mock(side_effect=RuntimeError("webhook down")),
    )

    with _as_user(user):
        resp = client.post(f"/api/run/{_RUN_ID}/feedback", json=_VALID_BODY)

    assert resp.status_code == 201

    from app.models import Feedback
    assert db.query(Feedback).filter(Feedback.run_id == _RUN_ID).count() == 1


def _staff(db):
    from app.models import User

    staff = User(email="staff@example.com", display_name="Staff User", role="staff")
    db.add(staff)
    db.commit()
    db.refresh(staff)
    return staff


def test_staff_can_read_but_not_submit_feedback_on_another_users_run(client, db, monkeypatch):
    """Staff is read-only: GET passes check_run_access(read_only=True), POST
    stays owner-or-admin and 403s with nothing written or notified."""
    _seed_run(db)
    staff = _staff(db)
    notify = Mock()
    monkeypatch.setattr("app.services.notifications.notify_feedback_submitted", notify)

    with _as_user(staff):
        read = client.get(f"/api/run/{_RUN_ID}/feedback")
        read_all = client.get(f"/api/run/{_RUN_ID}/feedback/all")
        write = client.post(f"/api/run/{_RUN_ID}/feedback", json=_VALID_BODY)

    assert read.status_code == 200
    assert read_all.status_code == 200
    assert write.status_code == 403
    notify.assert_not_called()
    from app.models import Feedback
    assert db.query(Feedback).filter(Feedback.run_id == _RUN_ID).count() == 0


def _add_feedback(db, user, at, **overrides):
    from app.models import Feedback

    fields = dict(
        run_id=_RUN_ID, user_id=user.id, reviewer_role="self", overall_usefulness=4,
        manual_conversion_effort="1-2 hours", correction_effort="1-2 hours",
        likelihood_to_recommend=5, submitted_at=at, issue_locations='["A", "M2"]',
        biggest_issue="Jane Testperson grants are missing",
    )
    fields.update(overrides)
    db.add(Feedback(**fields))
    db.commit()


def _other_user(db, email, role="user"):
    from app.models import User

    user = User(email=email, display_name="Other Reviewer", role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_feedback_all_lists_every_reviewer_newest_first(client, db):
    owner = _seed_run(db)
    other = _other_user(db, "other@example.com")
    _add_feedback(db, owner, datetime(2026, 9, 1))
    _add_feedback(db, other, datetime(2026, 9, 2), reviewer_role="colleague")

    with _as_user(owner):
        resp = client.get(f"/api/run/{_RUN_ID}/feedback/all")

    assert resp.status_code == 200
    rows = resp.json()
    assert [(r["display_name"], r["reviewer_role"]) for r in rows] == [
        ("Other Reviewer", "colleague"), ("Reviewer", "self")]
    submitted = datetime.fromisoformat(rows[0]["submitted_at"])
    assert submitted.tzinfo is not None  # carries a UTC offset (schemas.py rule)
    assert submitted.replace(tzinfo=None) == datetime(2026, 9, 2)
    assert rows[0]["issue_locations"] == ["A", "M2"]
    assert rows[0]["biggest_issue"] == "Jane Testperson grants are missing"
    assert rows[0]["overall_usefulness"] == 4
    assert rows[0]["summary_generated"] is None


def test_feedback_all_is_open_to_an_admin_and_closed_to_a_stranger(client, db):
    owner = _seed_run(db)
    _add_feedback(db, owner, datetime(2026, 9, 1))
    admin = _other_user(db, "root@example.com", role="admin")
    stranger = _other_user(db, "stranger@example.com")

    with _as_user(admin):
        assert client.get(f"/api/run/{_RUN_ID}/feedback/all").status_code == 200
    with _as_user(stranger):
        assert client.get(f"/api/run/{_RUN_ID}/feedback/all").status_code == 403
    with _as_user(admin):
        assert client.get("/api/run/NOSUCH/feedback/all").status_code == 404


def test_feedback_all_matches_the_single_feedback_serialisation(client, db):
    """One per-field serialisation: /feedback/all adds only display_name."""
    owner = _seed_run(db)
    _add_feedback(db, owner, datetime(2026, 9, 1))

    with _as_user(owner):
        single = client.get(f"/api/run/{_RUN_ID}/feedback").json()["feedback"]
        listed = client.get(f"/api/run/{_RUN_ID}/feedback/all").json()[0]

    listed.pop("display_name")
    assert listed == single
