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
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import object_session

from app.auth import COOKIE_NAME, create_session_cookie
from app.models import Run, Step, User
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
