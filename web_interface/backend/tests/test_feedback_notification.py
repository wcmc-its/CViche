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

import pytest

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
    from app.models import Run, User

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
    from app.auth import get_current_user
    from app.main import app

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


# --- the review loop (#1587): the corrected copy -------------------------------


def _docx(*paragraphs):
    import io

    from docx import Document

    doc = Document()
    for text in paragraphs:
        doc.add_paragraph(text)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


# Synthetic entries: nothing here is from a real CV.
_DELIVERED = ("Fellowship in Quorvane studies, Northfield Institute, 2011-2013",
              "Brennic Foundation Award for Saltwick lattice modelling, 2015",
              "Lecturer in Heliotropic Drift, Plesk College, 2016-2019")


@pytest.fixture
def run_storage(tmp_path, monkeypatch):
    from app.storage.local_storage import LocalRunStorage

    store = LocalRunStorage(str(tmp_path / "store"))
    monkeypatch.setattr("app.services.review_loop_service.get_storage", lambda: store)
    return store


def test_corrected_docx_upload_stores_the_copy_and_its_diff_and_returns_one_line(client, db, run_storage):
    user = _seed_run(db)
    run_storage.put_file(_RUN_ID, f"outputs/{_RUN_ID}_wcm.docx", _docx(*_DELIVERED))
    corrected = _docx(_DELIVERED[0], _DELIVERED[2])  # the reviewer deleted the award
    with _as_user(user):
        resp = client.post(f"/api/run/{_RUN_ID}/feedback/corrected-docx",
                           files={"file": ("corrected.docx", corrected)})
    assert resp.status_code == 200
    assert resp.json() == {"changes": 1, "summary": "1 change recorded"}

    import json
    assert run_storage.get_file(_RUN_ID, f"corrected/{_RUN_ID}_corrected.docx") == corrected
    report = json.loads(run_storage.get_file(_RUN_ID, f"corrected/{_RUN_ID}_diff.json"))
    assert report["by_type"]["deleted"] == 1
    assert "Brennic" not in json.dumps(report)  # positions and counts only, no CV text
    # Never under outputs/: downloads and the scorer read only that prefix.
    assert run_storage.list_files(_RUN_ID, "outputs/") == [f"outputs/{_RUN_ID}_wcm.docx"]
    # #1654: the verdicts (this run has no review copy, and says so) and the doctor's re-run.
    verdicts = json.loads(run_storage.get_file(_RUN_ID, f"corrected/{_RUN_ID}_verdicts.json"))
    assert verdicts["note"] and verdicts["findings"] == []
    rerun = json.loads(run_storage.get_file(_RUN_ID, f"corrected/{_RUN_ID}_doctor.json"))
    assert rerun["document_uid"] == _RUN_ID and rerun["artifacts"]["stage_6_docx"]


def test_corrected_docx_upload_confirms_though_the_review_pass_fails(client, db, run_storage, monkeypatch):
    """#1654: the copy and its diff are stored; a failing verdict or re-run step is logged only."""
    def boom(*_args):
        raise ValueError("synthetic")

    monkeypatch.setattr("app.services.review_loop_service.store_verdicts", boom)
    monkeypatch.setattr("app.services.review_loop_service.store_corrected_doctor", boom)
    user = _seed_run(db)
    run_storage.put_file(_RUN_ID, f"outputs/{_RUN_ID}_wcm.docx", _docx(*_DELIVERED))
    with _as_user(user):
        resp = client.post(f"/api/run/{_RUN_ID}/feedback/corrected-docx",
                           files={"file": ("corrected.docx", _docx(_DELIVERED[0]))})
    assert resp.status_code == 200
    assert run_storage.list_files(_RUN_ID, "corrected/") == [
        f"corrected/{_RUN_ID}_corrected.docx", f"corrected/{_RUN_ID}_diff.json"]


@pytest.mark.parametrize("name, message", [
    ("corrected.pdf", "Word (.docx) file"),  # a valid Word file, but not named .docx
    ("corrected.docx", "does not match .docx format"),  # over the zip-bomb entry bound (patched to 1)
])
def test_corrected_docx_upload_refuses_what_the_cv_upload_refuses(
        client, db, run_storage, monkeypatch, name, message):
    if name.endswith(".docx"):
        monkeypatch.setattr("app.services.upload_validation._DOCX_MAX_ENTRIES", 1)
    user = _seed_run(db)
    run_storage.put_file(_RUN_ID, f"outputs/{_RUN_ID}_wcm.docx", _docx(*_DELIVERED))
    with _as_user(user):
        resp = client.post(f"/api/run/{_RUN_ID}/feedback/corrected-docx",
                           files={"file": (name, _docx(_DELIVERED[0]))})
    assert resp.status_code == 400
    assert message in resp.json()["detail"]["message"]
    assert run_storage.list_files(_RUN_ID, "corrected/") == []


def test_corrected_docx_upload_is_bounded_by_the_upload_size_cap(client, db, run_storage, monkeypatch):
    corrected = _docx(_DELIVERED[0])
    monkeypatch.setattr("app.api.feedback_routes.MAX_UPLOAD_SIZE", len(corrected) - 1)  # one byte over
    user = _seed_run(db)
    run_storage.put_file(_RUN_ID, f"outputs/{_RUN_ID}_wcm.docx", _docx(*_DELIVERED))
    with _as_user(user):
        resp = client.post(f"/api/run/{_RUN_ID}/feedback/corrected-docx",
                           files={"file": ("corrected.docx", corrected)})
    assert resp.status_code == 400
    assert "too large" in resp.json()["detail"]["message"]
    assert run_storage.list_files(_RUN_ID, "corrected/") == []


def test_corrected_docx_upload_refuses_a_macro_carrying_docx(client, db, run_storage):
    import io
    import zipfile

    buf = io.BytesIO(_docx(*_DELIVERED))
    with zipfile.ZipFile(buf, "a") as zf:
        zf.writestr("word/vbaProject.bin", b"\x00")
    user = _seed_run(db)
    run_storage.put_file(_RUN_ID, f"outputs/{_RUN_ID}_wcm.docx", _docx(*_DELIVERED))
    with _as_user(user):
        resp = client.post(f"/api/run/{_RUN_ID}/feedback/corrected-docx",
                           files={"file": ("corrected.docx", buf.getvalue())})
    assert resp.status_code == 400
    assert "macros" in resp.json()["detail"]["message"]
    assert run_storage.list_files(_RUN_ID, "corrected/") == []


def test_corrected_docx_upload_needs_a_delivered_document(client, db, run_storage):
    user = _seed_run(db)
    with _as_user(user):
        resp = client.post(f"/api/run/{_RUN_ID}/feedback/corrected-docx",
                           files={"file": ("corrected.docx", _docx("x"))})
    assert resp.status_code == 409
    assert run_storage.list_files(_RUN_ID, "corrected/") == []


def test_corrected_docx_upload_is_closed_to_read_only_staff(client, db, run_storage):
    """Same access as submitting feedback: staff may read a run, not upload to it."""
    _seed_run(db)
    run_storage.put_file(_RUN_ID, f"outputs/{_RUN_ID}_wcm.docx", _docx(*_DELIVERED))
    with _as_user(_staff(db)):
        resp = client.post(f"/api/run/{_RUN_ID}/feedback/corrected-docx",
                           files={"file": ("corrected.docx", _docx(*_DELIVERED))})
    assert resp.status_code == 403
    assert run_storage.list_files(_RUN_ID, "corrected/") == []
