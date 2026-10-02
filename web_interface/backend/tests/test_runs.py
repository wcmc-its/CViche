"""Tests for app/api/runs.py endpoints.

#1111: processing cost is admin-only. A non-admin owner's run list and run
status carry None where an admin sees the dollar figure.

#801: /runs paging and /quality step are bounded (422 outside the range);
/capacity, /start, /cancel, /restart and /retry declare response models whose
wire bytes match the old bare dicts; get_data_quality's handlers catch only
artifact read failures and let anything else propagate.
"""
import json
import logging
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import object_session

from app.auth import COOKIE_NAME, create_session_cookie
from app.models import Feedback, Run, Step, User
from app.pipeline.step_registry import STEP_REGISTRY
from app.schemas import CapacityResponse, RestartRunResponse, RunActionResponse


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


@pytest.mark.parametrize("role, run_by_name", [("admin", "T"), ("user", None)])
def test_run_status_carries_owner_and_admin_only_run_by(client, db, seed_simple_mode, role, run_by_name):
    user, run = _owner_with_run(db, role)
    run.cv_owner_name = "Jane Testperson"
    db.commit()
    _auth(client, user)
    body = client.get(f"/api/run/{run.id}/status").json()
    assert body["cv_owner_name"] == "Jane Testperson"
    assert (body["run_by"] or {}).get("display_name") == run_by_name


# ---------------------------------------------------------------------------
# #801: query-parameter bounds, response models, narrowed quality excepts.
# ---------------------------------------------------------------------------

def _wire_bytes(body: dict) -> bytes:
    """Exactly how Starlette's JSONResponse renders a dict -- the bytes every
    one of these endpoints put on the wire before it had a response_model."""
    return json.dumps(body, ensure_ascii=False, allow_nan=False, indent=None,
                      separators=(",", ":")).encode("utf-8")


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1"])
def test_run_list_rejects_out_of_range_paging(client, db, seed_simple_mode, query):
    user, _ = _owner_with_run(db, "user")
    _auth(client, user)
    assert client.get(f"/api/runs?{query}").status_code == 422


def test_run_list_accepts_the_ui_page_size(client, db, seed_simple_mode):
    """RunHistory.tsx asks for PAGE_SIZE = 100; the bound must not break it."""
    user, _ = _owner_with_run(db, "user")
    _auth(client, user)
    resp = client.get("/api/runs?offset=0&limit=100")
    assert resp.status_code == 200
    assert resp.json()["limit"] == 100


@pytest.fixture
def quality_outputs(monkeypatch, tmp_path):
    """Point get_data_quality at a tmp outputs tree, never the repo's."""
    from app.api import runs as runs_api
    monkeypatch.setattr(runs_api, "_OUTPUTS_ROOT", tmp_path)
    return tmp_path


@pytest.mark.parametrize("step", [0, len(STEP_REGISTRY) + 1])
def test_quality_rejects_a_step_outside_the_registry(client, db, seed_simple_mode, quality_outputs, step):
    user, run = _owner_with_run(db, "user")
    (quality_outputs / run.id).mkdir()
    _auth(client, user)
    assert client.get(f"/api/run/{run.id}/quality?step={step}").status_code == 422


def test_quality_accepts_the_last_registry_step(client, db, seed_simple_mode, quality_outputs):
    user, run = _owner_with_run(db, "user")
    (quality_outputs / run.id).mkdir()
    _auth(client, user)
    resp = client.get(f"/api/run/{run.id}/quality?step={len(STEP_REGISTRY)}")
    assert resp.status_code == 200
    assert resp.json()["step"] == len(STEP_REGISTRY)


_VALID_METADATA = ("stage_4_wcm_templates/cv_template_metadata.json", {"records_processed": {}})
_VALID_METADATA_TEXT = (_VALID_METADATA[0], json.dumps(_VALID_METADATA[1]))

# (step, artifact path under the run's outputs dir, warning type it reports,
#  a well-formed JSON body whose SHAPE the handler can't process). One row per
#  narrowed handler in get_data_quality.
_QUALITY_SITES = [
    (4, "stage_2d_enriched/section_B1_cv_enriched.json", "enriched_read_error", []),
    (4, "stage_3_parsing/education/cv_parsed.json", "parsed_read_error", []),
    # records_processed with a non-numeric count fails the aggregation loop
    # only; the personal-data re-read below never looks at it.
    (4, "stage_4_wcm_templates/cv_template_metadata.json", "metadata_read_error",
     {"records_processed": {"education": "x"}}),
    (3, "stage_3_parsing/publications/cv.json", "parse_error", {"entries": 5}),
    (3, "stage_3_sections/section_x.json", "section_read_error", []),
    (3, "stage_2_taxonomy_mapping/cv_mapped.json", "taxonomy_read_error", []),
]


def _write_quality_artifacts(root, run_id, step, rel, content):
    run_dir = root / run_id
    files = {rel: content}
    if step == 4:
        files = {_VALID_METADATA[0]: json.dumps(_VALID_METADATA[1]), **files}
    for path, text in files.items():
        (run_dir / path).parent.mkdir(parents=True, exist_ok=True)
        if text is None:  # a directory where a file is expected: open() raises OSError
            (run_dir / path).mkdir()
        elif isinstance(text, str):
            (run_dir / path).write_text(text)
        else:
            (run_dir / path).write_bytes(text)


@pytest.mark.parametrize("step, rel, warning_type, _shape", _QUALITY_SITES)
@pytest.mark.parametrize("bad_bytes", [b'{"truncated": ', b"\xff\xfe not utf-8", None])
def test_quality_reports_an_unreadable_artifact_as_a_warning(
        client, db, seed_simple_mode, quality_outputs, step, rel, warning_type, _shape, bad_bytes):
    user, run = _owner_with_run(db, "user")
    _write_quality_artifacts(quality_outputs, run.id, step, rel, bad_bytes)
    _auth(client, user)
    resp = client.get(f"/api/run/{run.id}/quality?step={step}")
    assert resp.status_code == 200
    assert warning_type in [w["type"] for w in resp.json()["warnings"]]


@pytest.mark.parametrize("step, rel, _warning_type, shape", _QUALITY_SITES)
def test_quality_lets_an_unexpected_artifact_shape_propagate(
        client, db, seed_simple_mode, quality_outputs, step, rel, _warning_type, shape):
    """A readable file the handler can't process is a bug, not a read
    failure: it must surface, not be folded into a warning (#801)."""
    user, run = _owner_with_run(db, "user")
    _write_quality_artifacts(quality_outputs, run.id, step, rel, json.dumps(shape))
    _auth(client, user)
    # app.main's catch-all turns the propagated AttributeError/TypeError into
    # a logged 500; before #801 it was swallowed into a 200 warning.
    assert client.get(f"/api/run/{run.id}/quality?step={step}").status_code == 500


def test_quality_personal_data_reread_failure_is_logged(client, db, seed_simple_mode, quality_outputs, caplog):
    """The personal-data pass re-reads the metadata files; its read failure
    used to be `except Exception: pass` and is now logged (#801)."""
    user, run = _owner_with_run(db, "user")
    _write_quality_artifacts(quality_outputs, run.id, 3, _VALID_METADATA[0], b"{not json")
    _auth(client, user)
    with caplog.at_level(logging.WARNING, logger="app.api.runs"):
        resp = client.get(f"/api/run/{run.id}/quality?step=4")
    assert resp.status_code == 200
    assert any("quality re-read cv_template_metadata.json failed" in r.getMessage() for r in caplog.records)


def test_quality_personal_data_reread_lets_an_unexpected_shape_propagate(
        client, db, seed_simple_mode, quality_outputs, monkeypatch):
    """The re-read's own handler is reachable only if the file changes between
    the two passes (any dict the first pass accepts, the second does too), so
    the second json.load is stubbed to hand back a list."""
    user, run = _owner_with_run(db, "user")
    _write_quality_artifacts(quality_outputs, run.id, 3, *_VALID_METADATA_TEXT)
    real_load, calls = json.load, []

    def _second_read_is_a_list(fp, *a, **k):
        calls.append(fp)
        return [] if len(calls) == 2 else real_load(fp, *a, **k)
    monkeypatch.setattr(json, "load", _second_read_is_a_list)
    _auth(client, user)
    assert client.get(f"/api/run/{run.id}/quality?step=4").status_code == 500
    assert len(calls) == 2


@pytest.mark.parametrize("path, model", [
    ("/api/capacity", CapacityResponse),
    ("/api/run/{run_id}/start", RunActionResponse),
    ("/api/run/{run_id}/cancel", RunActionResponse),
    ("/api/run/{run_id}/restart", RestartRunResponse),
    ("/api/run/{run_id}/retry/{step_number}", RunActionResponse),
])
def test_run_action_routes_declare_their_response_model(path, model):
    from app.main import app
    routes = [r for r in app.routes if getattr(r, "path", None) == path]
    assert [r.response_model for r in routes] == [model]


def test_capacity_body_is_byte_identical(client, db, seed_simple_mode):
    user, _ = _owner_with_run(db, "user")
    _auth(client, user)
    with patch("app.pipeline.concurrency.active_count", return_value=1), \
         patch("app.pipeline.concurrency.get_max_concurrent_runs", return_value=3):
        resp = client.get("/api/capacity")
    assert resp.content == _wire_bytes({"available": True, "active": 1, "limit": 3})


@pytest.fixture
def run_with_input(db, monkeypatch, tmp_path):
    """A run owned by a plain user, with its input on a tmp UPLOAD_DIR and the
    pipeline itself stubbed, so start/retry reach their in-process return."""
    from app.api import runs as runs_api

    async def _no_pipeline(*_a, **_k):
        return None

    orchestrator = MagicMock()
    orchestrator.return_value.execute = _no_pipeline
    monkeypatch.setattr(runs_api, "PipelineOrchestrator", orchestrator)
    monkeypatch.setattr(runs_api, "UPLOAD_DIR", tmp_path)
    monkeypatch.setattr(runs_api, "_materialize_input_if_missing", lambda *a: None)

    def _make(status):
        user, run = _owner_with_run(db, "user")
        run.status = status
        db.commit()
        (tmp_path / f"{run.id}.docx").write_bytes(b"PK")
        return user, run
    return _make


def test_start_body_is_byte_identical(client, db, seed_simple_mode, run_with_input):
    user, run = run_with_input("created")
    _auth(client, user)
    resp = client.post(f"/api/run/{run.id}/start")
    assert resp.status_code == 200, resp.text
    assert resp.content == _wire_bytes({"message": f"Pipeline started for run {run.id}", "status": "running"})


def test_retry_body_is_byte_identical(client, db, seed_simple_mode, run_with_input):
    user, run = run_with_input("failed")
    db.query(Step).filter(Step.run_id == run.id).update({"status": "error"})
    db.commit()
    _auth(client, user)
    resp = client.post(f"/api/run/{run.id}/retry/1")
    assert resp.status_code == 200, resp.text
    assert resp.content == _wire_bytes({"message": f"Retrying run {run.id} from step 1", "status": "running"})


def test_cancel_body_is_byte_identical(client, db, seed_simple_mode, run_with_input):
    user, run = run_with_input("running")
    _auth(client, user)
    resp = client.post(f"/api/run/{run.id}/cancel")
    assert resp.status_code == 200, resp.text
    assert resp.content == _wire_bytes({"message": f"Run {run.id} cancelled", "status": "cancelled"})


def test_restart_body_is_byte_identical(client, db, seed_simple_mode, run_with_input, monkeypatch, tmp_path):
    from app.api import runs as runs_api
    user, run = run_with_input("complete")
    storage = MagicMock()
    monkeypatch.setattr(runs_api, "check_rate_limit", lambda *a: None)
    monkeypatch.setattr(runs_api, "get_storage", lambda: storage)
    monkeypatch.setattr("app.api.upload.UPLOAD_DIR", tmp_path)
    monkeypatch.setattr("app.api.upload.get_storage", lambda: storage)
    _auth(client, user)
    resp = client.post(f"/api/run/{run.id}/restart")
    assert resp.status_code == 200, resp.text
    new_run_id = resp.json()["run_id"]
    assert resp.content == _wire_bytes({"run_id": new_run_id, "message": f"New run created from {run.id}"})


# ---------------------------------------------------------------------------
# Admin "all runs" view: GET /runs?scope=all and GET /runs/filter-options
# ---------------------------------------------------------------------------

def _seed_admin_view(db):
    """Two faculty-ish users in different departments, an admin, and six runs.

    Synthetic names only. Returns the users by label."""
    alice = User(email="alice@example.com", cwid="abc1001", display_name="Alice Tester",
                 role="user", department="Medicine", consent_version="1.0")
    bob = User(email="bob@example.com", cwid="abc1002", display_name="Bob Tester",
               role="user", department="Library", consent_version="1.0")
    admin = User(email="root@example.com", cwid="abc1003", display_name="Root Admin",
                 role="admin", consent_version="1.0")
    db.add_all([alice, bob, admin])
    db.commit()
    rows = [
        ("ADM001", alice, "Jane Testperson", "own_cv"),
        ("ADM002", alice, "Jane Testperson", "authorized_admin"),
        ("ADM003", bob, "Jane Testperson", "authorized_admin"),
        ("ADM004", bob, "Omar Testperson", "authorized_admin"),
        ("ADM005", admin, None, "authorized_admin"),
        ("ADM006", None, "Omar Testperson", "authorized_admin"),
    ]
    for i, (run_id, user, owner, sub_type) in enumerate(rows):
        db.add(Run(id=run_id, user_id=user.id if user else None, status="complete",
                   filename="cv.docx", file_type="docx", total_cost=2.5,
                   cv_owner_name=owner, submission_type=sub_type,
                   started_at=datetime(2026, 9, 1 + i)))
    db.commit()
    return {"alice": alice, "bob": bob, "admin": admin}


def _ids(resp):
    return [r["run_id"] for r in resp.json()["runs"]]


def test_scope_all_is_forbidden_for_a_non_admin(client, db, seed_simple_mode):
    users = _seed_admin_view(db)
    _auth(client, users["alice"])
    assert client.get("/api/runs?scope=all").status_code == 403
    assert client.get("/api/runs/filter-options?scope=all").status_code == 403


def test_scope_mine_is_the_default_and_ignores_filters(client, db, seed_simple_mode):
    users = _seed_admin_view(db)
    _auth(client, users["alice"])
    resp = client.get("/api/runs?faculty=Omar%20Testperson&department=Library&run_by=self")
    assert resp.status_code == 200
    assert _ids(resp) == ["ADM002", "ADM001"]
    body = resp.json()["runs"][0]
    assert body["cv_owner_name"] == "Jane Testperson"
    assert body["submission_type"] == "authorized_admin"
    assert body["run_by"] is None
    assert body["total_cost"] is None  # non-admin never sees cost


def test_scope_all_lists_every_run_with_run_by(client, db, seed_simple_mode):
    users = _seed_admin_view(db)
    _auth(client, users["admin"])
    resp = client.get("/api/runs?scope=all")
    assert resp.status_code == 200
    assert resp.json()["total"] == 6
    assert _ids(resp) == ["ADM006", "ADM005", "ADM004", "ADM003", "ADM002", "ADM001"]
    by_id = {r["run_id"]: r for r in resp.json()["runs"]}
    assert by_id["ADM004"]["run_by"] == {
        "id": users["bob"].id, "display_name": "Bob Tester", "cwid": "abc1002",
        "email": "bob@example.com", "department": "Library"}
    assert by_id["ADM006"]["run_by"] is None
    assert by_id["ADM004"]["total_cost"] == 2.5  # admin sees cost via visible_cost


@pytest.mark.parametrize("query, expected", [
    ("faculty=Jane%20Testperson", ["ADM003", "ADM002", "ADM001"]),
    ("department=Library", ["ADM004", "ADM003"]),
    ("run_by=self", ["ADM001"]),
    ("department=Library&faculty=Omar%20Testperson", ["ADM004"]),
    ("faculty=Nobody", []),
])
def test_scope_all_filters(client, db, seed_simple_mode, query, expected):
    users = _seed_admin_view(db)
    _auth(client, users["admin"])
    resp = client.get(f"/api/runs?scope=all&{query}")
    assert resp.status_code == 200
    assert _ids(resp) == expected
    assert resp.json()["total"] == len(expected)


def test_scope_all_run_by_user_id(client, db, seed_simple_mode):
    users = _seed_admin_view(db)
    _auth(client, users["admin"])
    resp = client.get(f"/api/runs?scope=all&run_by={users['bob'].id}")
    assert _ids(resp) == ["ADM004", "ADM003"]


@pytest.mark.parametrize("url", [
    "/api/runs?scope=all&run_by=nobody",
    "/api/runs?scope=everyone",
])
def test_scope_all_rejects_bad_params(client, db, seed_simple_mode, url):
    users = _seed_admin_view(db)
    _auth(client, users["admin"])
    assert client.get(url).status_code == 422


def test_filter_options_unfiltered(client, db, seed_simple_mode):
    users = _seed_admin_view(db)
    _auth(client, users["admin"])
    resp = client.get("/api/runs/filter-options?scope=all")
    assert resp.status_code == 200
    body = resp.json()
    assert body["departments"] == [{"value": "Library", "count": 2},
                                   {"value": "Medicine", "count": 2}]
    assert body["faculty"] == [  # newest run first (ADM006 > ADM003)
        {"value": "Omar Testperson", "count": 2, "last_run_at": "2026-09-06T00:00:00"},
        {"value": "Jane Testperson", "count": 3, "last_run_at": "2026-09-03T00:00:00"}]
    assert [(o["display_name"], o["count"]) for o in body["run_by"]] == [
        ("Alice Tester", 2), ("Bob Tester", 2), ("Root Admin", 1)]
    assert body["run_by"][0] == {"id": users["alice"].id, "display_name": "Alice Tester",
                                 "cwid": "abc1001", "email": "alice@example.com",
                                 "department": "Medicine", "count": 2}
    assert body["self_count"] == 1


def test_filter_options_requires_scope_all(client, db, seed_simple_mode):
    users = _seed_admin_view(db)
    _auth(client, users["admin"])
    assert client.get("/api/runs/filter-options?scope=mine").status_code == 400


# ---------------------------------------------------------------------------
# Feedback summary on GET /runs, the `feedback` filter, filter-options counts
# ---------------------------------------------------------------------------

def _leave_feedback(db, run_id, user, role="self", at=None):
    db.add(Feedback(run_id=run_id, user_id=user.id, reviewer_role=role,
                    overall_usefulness=4, manual_conversion_effort="1-2 hours",
                    correction_effort="1-2 hours", likelihood_to_recommend=4,
                    submitted_at=at or datetime(2026, 9, 10)))
    db.commit()


def _seed_feedback_view(db):
    """ADM001 has feedback from bob (older) and admin (newer); ADM003 has
    feedback from alice (the owner is bob); ADM005 is a failed run, no feedback."""
    users = _seed_admin_view(db)
    db.get(Run, "ADM005").status = "failed"
    db.commit()
    _leave_feedback(db, "ADM001", users["bob"], "colleague", datetime(2026, 9, 10))
    _leave_feedback(db, "ADM001", users["admin"], "admin_reviewer", datetime(2026, 9, 11))
    _leave_feedback(db, "ADM003", users["alice"], "self", datetime(2026, 9, 12))
    return users


def test_runs_feedback_summary_counts_any_reviewer_and_flags_mine(client, db, seed_simple_mode):
    users = _seed_feedback_view(db)
    _auth(client, users["alice"])
    by_id = {r["run_id"]: r["feedback"] for r in client.get("/api/runs").json()["runs"]}
    # Alice owns ADM001: bob and the admin reviewed it, she did not.
    last_at = by_id["ADM001"].pop("last_at")
    assert last_at.startswith("2026-09-11T00:00:00")  # TZDateTime appends the server offset
    assert by_id["ADM001"] == {"count": 2, "given_by_me": False, "reviewers": None}
    assert by_id["ADM002"] == {"count": 0, "given_by_me": False, "last_at": None,
                               "reviewers": None}


def test_scope_all_feedback_lists_reviewers_newest_first(client, db, seed_simple_mode):
    users = _seed_feedback_view(db)
    _auth(client, users["admin"])
    by_id = {r["run_id"]: r["feedback"] for r in client.get("/api/runs?scope=all").json()["runs"]}
    reviewers = by_id["ADM001"]["reviewers"]
    assert [(r["display_name"], r["role"]) for r in reviewers] == [
        ("Root Admin", "admin_reviewer"), ("Bob Tester", "colleague")]
    assert reviewers[0]["submitted_at"].startswith("2026-09-11T00:00:00")
    assert reviewers[1]["submitted_at"].startswith("2026-09-10T00:00:00")
    assert by_id["ADM001"]["given_by_me"] is True  # the admin is one of the reviewers
    assert by_id["ADM003"]["given_by_me"] is False
    assert by_id["ADM002"]["reviewers"] == []


def test_feedback_summary_loads_in_a_fixed_number_of_queries(client, db, seed_simple_mode):
    from sqlalchemy import event
    users = _seed_feedback_view(db)
    _auth(client, users["admin"])
    statements = []
    engine = db.get_bind()
    listener = lambda conn, cur, stmt, *a: statements.append(stmt)  # noqa: E731
    event.listen(engine, "before_cursor_execute", listener)
    try:
        assert client.get("/api/runs?scope=all").status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    feedback_queries = [s for s in statements if "FROM feedback" in s and "FROM runs" not in s]
    assert len(feedback_queries) == 2  # one GROUP BY, one reviewer query


@pytest.mark.parametrize("value, expected", [
    ("given", ["ADM003", "ADM001"]),
    ("needed", ["ADM006", "ADM004", "ADM002"]),  # ADM005 failed: not "needed"
])
def test_scope_all_feedback_filter(client, db, seed_simple_mode, value, expected):
    users = _seed_feedback_view(db)
    _auth(client, users["admin"])
    resp = client.get(f"/api/runs?scope=all&feedback={value}")
    assert _ids(resp) == expected
    assert resp.json()["total"] == len(expected)


def test_scope_mine_feedback_filter_counts_feedback_from_others(client, db, seed_simple_mode):
    users = _seed_feedback_view(db)
    _auth(client, users["alice"])
    assert _ids(client.get("/api/runs?feedback=given")) == ["ADM001"]
    assert _ids(client.get("/api/runs?feedback=needed")) == ["ADM002"]


def test_feedback_filter_rejects_unknown_value(client, db, seed_simple_mode):
    users = _seed_feedback_view(db)
    _auth(client, users["admin"])
    assert client.get("/api/runs?scope=all&feedback=maybe").status_code == 422
    assert client.get("/api/runs?feedback=maybe").status_code == 422


def test_filter_options_feedback_counts_cascade(client, db, seed_simple_mode):
    users = _seed_feedback_view(db)
    _auth(client, users["admin"])
    body = client.get("/api/runs/filter-options?scope=all").json()
    assert body["feedback"] == {"given": 2, "needed": 3}
    # Its own filter is ignored, other filters apply.
    body = client.get("/api/runs/filter-options?scope=all&feedback=given&department=Library").json()
    assert body["feedback"] == {"given": 1, "needed": 1}
    # ...and the other facets honour the feedback filter.
    assert [o["count"] for o in body["run_by"]] == [1]  # bob: ADM003 only
    assert body["self_count"] == 0


@pytest.mark.parametrize("value, expected", [
    ("running", ["ADM006"]),
    ("failed", ["ADM005"]),
    ("red", ["ADM003"]),
])
def test_scope_all_status_filter(client, db, seed_simple_mode, value, expected):
    users = _seed_feedback_view(db)
    db.get(Run, "ADM006").status = "running"
    db.get(Run, "ADM003").quality_band = "RED"
    db.commit()
    _auth(client, users["admin"])
    resp = client.get(f"/api/runs?scope=all&status={value}")
    assert _ids(resp) == expected
    assert resp.json()["total"] == len(expected)


def test_scope_all_run_by_on_behalf_and_its_option_count(client, db, seed_simple_mode):
    users = _seed_admin_view(db)
    _auth(client, users["admin"])
    assert _ids(client.get("/api/runs?scope=all&run_by=on_behalf")) == [
        "ADM006", "ADM005", "ADM004", "ADM003", "ADM002"]
    assert client.get("/api/runs/filter-options?scope=all").json()["on_behalf_count"] == 5


def test_scope_mine_status_filter_sees_only_own_failures(client, db, seed_simple_mode):
    users = _seed_feedback_view(db)
    db.get(Run, "ADM001").status = "failed"  # Alice's own Jane run
    db.get(Run, "ADM004").status = "running"  # Bob's
    db.commit()
    _auth(client, users["alice"])
    assert _ids(client.get("/api/runs?status=failed")) == ["ADM002", "ADM001"]
    assert _ids(client.get("/api/runs?status=running")) == []
    _auth(client, users["bob"])
    # Alice's failed Jane run does not flag Bob's Jane run (ADM003).
    assert _ids(client.get("/api/runs?status=failed")) == []
    assert _ids(client.get("/api/runs?status=running")) == ["ADM004"]


def test_filter_options_cascade_applies_the_status_filter(client, db, seed_simple_mode):
    users = _seed_admin_view(db)
    db.get(Run, "ADM006").status = "running"
    db.commit()
    _auth(client, users["admin"])
    body = client.get("/api/runs/filter-options?scope=all&status=running").json()
    assert [(f["value"], f["count"]) for f in body["faculty"]] == [("Omar Testperson", 1)]
    assert body["status"]["all"] == 6  # the pill counts ignore the status filter itself


def test_red_status_is_admin_only_and_bad_status_is_422(client, db, seed_simple_mode):
    users = _seed_feedback_view(db)
    _auth(client, users["alice"])
    assert client.get("/api/runs?status=red").status_code == 403
    assert client.get("/api/runs?scope=all&status=red").status_code == 403
    assert client.get("/api/runs?status=green").status_code == 422
    _auth(client, users["admin"])
    assert client.get("/api/runs?scope=all&status=green").status_code == 422
    assert client.get("/api/runs/filter-options?scope=all&status=red").status_code == 200


# ---------------------------------------------------------------------------
# Run score: list columns (admin), run-quality report (admin), review note
# ---------------------------------------------------------------------------

def _scored(db, run_id, score, band, cap):
    run = db.get(Run, run_id)
    run.quality_score, run.quality_band, run.quality_cap = score, band, cap
    db.commit()


def test_scope_all_carries_score_columns_and_scope_mine_never_does(client, db, seed_simple_mode):
    users = _seed_admin_view(db)
    _scored(db, "ADM001", 25, "RED", 25)

    _auth(client, users["admin"])
    by_id = {r["run_id"]: r for r in client.get("/api/runs?scope=all").json()["runs"]}
    assert (by_id["ADM001"]["quality_score"], by_id["ADM001"]["quality_band"],
            by_id["ADM001"]["quality_cap"]) == (25, "RED", 25)
    assert by_id["ADM002"]["quality_score"] is None

    # An admin's own scope=mine list is cost-visible but still carries no score.
    admin_run = db.get(Run, "ADM005")
    admin_run.quality_score = 90
    db.commit()
    mine = client.get("/api/runs").json()["runs"]
    assert [r["run_id"] for r in mine] == ["ADM005"]
    assert (mine[0]["quality_score"], mine[0]["quality_band"], mine[0]["quality_cap"]) == (None, None, None)

    _auth(client, users["alice"])
    alice_runs = client.get("/api/runs").json()["runs"]
    assert all(r["quality_score"] is None for r in alice_runs)


_OWNER_GATE = "CV owner name / contact populated (HARD-FAIL gate)"
_CACHED_SCORE = {
    "totalScore": 25, "raw_score_before_caps": 80.0, "hard_fail_caps_applied": [25],
    "flags": [f"HARD-FAIL cap=25: {_OWNER_GATE} (fraction=1.00)"],
    "dimensionScores": [{"name": "Duplicate entries", "score": 8.0, "max": 10}],
    "data_complete": True,
}
_DOCTOR = {"findings": [
    {"lint": "owner_contact_missing", "severity": "ERROR", "message": "m", "status": "ran"},
    {"lint": "table_shape", "severity": "INFO", "message": "m", "status": "ran"},
]}


def _patch_quality(monkeypatch, score, doctor):
    from app.services import quality_score_service as svc
    monkeypatch.setattr(svc, "get_cached_score", lambda _rid: score)
    monkeypatch.setattr(svc, "get_doctor_report", lambda _rid: doctor)


def test_run_quality_is_admin_only(client, db, seed_simple_mode, monkeypatch):
    users = _seed_admin_view(db)
    _patch_quality(monkeypatch, _CACHED_SCORE, _DOCTOR)
    _auth(client, users["alice"])  # alice owns ADM001
    assert client.get("/api/run/ADM001/run-quality").status_code == 403


def test_run_quality_report_shape(client, db, seed_simple_mode, monkeypatch):
    users = _seed_admin_view(db)
    _patch_quality(monkeypatch, _CACHED_SCORE, _DOCTOR)
    _auth(client, users["admin"])

    resp = client.get("/api/run/ADM001/run-quality")

    assert resp.status_code == 200
    body = resp.json()
    assert {k: body[k] for k in (
        "run_id", "score", "band", "band_meaning", "provisional", "cap", "cap_reason",
        "cap_lint", "earned", "total_weight", "data_complete", "dimensions")} == {
        "run_id": "ADM001", "score": 25, "band": "RED", "band_meaning": "Don't deliver",
        "provisional": True, "cap": 25, "cap_reason": "owner name missing",
        "cap_lint": "owner_contact_missing", "earned": 80, "total_weight": 10,
        "data_complete": True,
        "dimensions": [{"name": "Duplicate entries", "weight": 10, "points": 8.0}]}
    assert [(f["lint"], f["severity"], f["count"], f["caps_score"])
            for f in body["doctor"]["findings"]] == [
        ("owner_contact_missing", "ERROR", 1, True), ("table_shape", "INFO", 1, False)]
    assert body["doctor"]["counts"] == {"error": 1, "warn": 0, "info": 1}


def test_run_quality_parts_are_null_when_missing_not_an_error(client, db, seed_simple_mode, monkeypatch):
    users = _seed_admin_view(db)
    _patch_quality(monkeypatch, None, None)
    _auth(client, users["admin"])

    resp = client.get("/api/run/ADM001/run-quality")

    assert resp.status_code == 200
    body = resp.json()
    assert (body["score"], body["band"], body["doctor"], body["dimensions"]) == (None, None, None, [])
    assert client.get("/api/run/NOSUCH/run-quality").status_code == 404


@pytest.mark.parametrize("score, band, cap, expected", [
    (91, "GREEN", None, False),
    (72, "YELLOW", None, True),
    (90, "GREEN", 25, True),
    (None, None, None, False),
])
def test_review_note_for_the_owner_never_carries_the_score(
        client, db, seed_simple_mode, monkeypatch, score, band, cap, expected):
    users = _seed_admin_view(db)
    _patch_quality(monkeypatch, None, None)
    _scored(db, "ADM001", score, band, cap)
    _auth(client, users["alice"])

    resp = client.get("/api/run/ADM001/review-note")

    assert resp.status_code == 200
    assert resp.json() == {"needs_cleanup": expected}


def test_review_note_falls_back_to_the_cached_score_before_backfill(client, db, seed_simple_mode, monkeypatch):
    users = _seed_admin_view(db)
    _patch_quality(monkeypatch, {"totalScore": 70, "hard_fail_caps_applied": []}, None)
    _auth(client, users["alice"])
    assert client.get("/api/run/ADM001/review-note").json() == {"needs_cleanup": True}


def test_review_note_is_for_the_owner_and_admins_only(client, db, seed_simple_mode, monkeypatch):
    users = _seed_admin_view(db)
    _patch_quality(monkeypatch, None, None)
    _auth(client, users["bob"])  # bob does not own ADM001
    assert client.get("/api/run/ADM001/review-note").status_code == 403
    _auth(client, users["admin"])
    assert client.get("/api/run/ADM001/review-note").status_code == 200


# ---------------------------------------------------------------------------
# #1114: batch_id on every /runs row, and the batch_id filter.
# ---------------------------------------------------------------------------

def _runs_in_and_out_of_a_batch(db, role="user"):
    from app.models import RunBatch
    user = User(email=f"batch-{role}@example.com", display_name="B", role=role, consent_version="1.0")
    db.add(user)
    db.commit()
    db.refresh(user)
    db.add(RunBatch(id="BATCHA", user_id=user.id, files_submitted=2))
    db.add_all([
        Run(id="INBATA", user_id=user.id, status="complete", filename="a.docx", file_type="docx",
            batch_id="BATCHA", started_at=datetime(2026, 10, 1, 9, 0)),
        Run(id="INBATB", user_id=user.id, status="queued", filename="b.docx", file_type="docx",
            batch_id="BATCHA", started_at=datetime(2026, 10, 1, 9, 1)),
        Run(id="SINGLE", user_id=user.id, status="complete", filename="c.docx", file_type="docx",
            started_at=datetime(2026, 10, 1, 9, 2)),
    ])
    db.commit()
    return user


def test_run_list_rows_carry_their_batch_id(client, db, seed_simple_mode):
    user = _runs_in_and_out_of_a_batch(db)
    _auth(client, user)
    rows = client.get("/api/runs").json()["runs"]
    assert {r["run_id"]: r["batch_id"] for r in rows} == {"INBATA": "BATCHA", "INBATB": "BATCHA", "SINGLE": None}


@pytest.mark.parametrize("role, scope", [("user", "mine"), ("admin", "all")])
def test_run_list_filters_to_one_batch_in_either_scope(client, db, seed_simple_mode, role, scope):
    user = _runs_in_and_out_of_a_batch(db, role)
    _auth(client, user)
    body = client.get(f"/api/runs?scope={scope}&batch_id=BATCHA").json()
    assert sorted(r["run_id"] for r in body["runs"]) == ["INBATA", "INBATB"]
    assert body["total"] == 2


# ---------------------------------------------------------------------------
# input_format filter: GET /runs?scope=all&input_format= and filter-options
# ---------------------------------------------------------------------------

def _seed_input_formats(db):
    users = _seed_admin_view(db)
    for run_id, fmt in [("ADM001", "wcm"), ("ADM002", "other"), ("ADM003", "wcm"), ("ADM004", "other")]:
        db.query(Run).filter(Run.id == run_id).update({Run.input_format: fmt})
    db.commit()  # ADM005 and ADM006 stay NULL
    return users


@pytest.mark.parametrize("value, expected", [
    ("wcm", ["ADM003", "ADM001"]),
    ("other", ["ADM004", "ADM002"]),
    ("unknown", ["ADM006", "ADM005"]),
])
def test_scope_all_input_format_filter(client, db, seed_simple_mode, value, expected):
    users = _seed_input_formats(db)
    _auth(client, users["admin"])
    resp = client.get(f"/api/runs?scope=all&input_format={value}")
    assert _ids(resp) == expected
    assert resp.json()["total"] == len(expected)


def test_input_format_filter_rejects_unknown_value(client, db, seed_simple_mode):
    users = _seed_input_formats(db)
    _auth(client, users["admin"])
    assert client.get("/api/runs?scope=all&input_format=docx").status_code == 422


def test_filter_options_input_format_counts_cascade(client, db, seed_simple_mode):
    users = _seed_input_formats(db)
    _auth(client, users["admin"])
    counts = client.get("/api/runs/filter-options?scope=all").json()["input_format"]
    assert counts == {"wcm": 2, "other": 2, "unknown": 2}
    # Other filters narrow the counts; the input_format filter itself does not.
    narrowed = client.get(
        "/api/runs/filter-options?scope=all&department=Library&input_format=wcm").json()
    assert narrowed["input_format"] == {"wcm": 1, "other": 1, "unknown": 0}

