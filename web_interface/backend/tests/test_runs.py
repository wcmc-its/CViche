"""Tests for app/api/runs.py endpoints.

#1111: processing cost is admin-only. A non-admin owner's run list and run
status carry None where an admin sees the dollar figure.
"""
import pytest
from sqlalchemy.orm import object_session

from app.auth import COOKIE_NAME, create_session_cookie
from app.models import Run, Step, User


def _owner_with_run(db, role):
    user = User(email=f"runs-{role}@example.com", display_name="T", role=role,
                consent_version="1.0")
    db.add(user)
    db.commit()
    db.refresh(user)
    run = Run(id=f"COST{role[:2].upper()}", user_id=user.id, status="complete",
              filename="cv.docx", file_type="docx", total_cost=1.25)
    db.add(run)
    db.add(Step(run_id=run.id, step_number=1, stage_id="1a", step_name="Hierarchy",
                status="complete", cost=0.42))
    db.commit()
    return user, run


def _auth(client, user):
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, object_session(user)))


@pytest.mark.parametrize("role, expected", [("admin", 1.25), ("user", None)])
def test_run_list_cost_is_admin_only(client, db, seed_simple_mode, role, expected):
    user, _ = _owner_with_run(db, role)
    _auth(client, user)
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    assert [r["total_cost"] for r in resp.json()["runs"]] == [expected]


@pytest.mark.parametrize("role, run_cost, step_cost", [("admin", 1.25, 0.42), ("user", None, None)])
def test_run_status_cost_is_admin_only(client, db, seed_simple_mode, role, run_cost, step_cost):
    user, run = _owner_with_run(db, role)
    _auth(client, user)
    resp = client.get(f"/api/run/{run.id}/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_cost"] == run_cost
    assert [s["cost"] for s in body["steps"]] == [step_cost]
