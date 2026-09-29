"""Authorization matrix for the steps.py endpoints (#780 review r3965862896#3):
owner / non-owner / admin / unauthenticated, for /step/{n}, /data/{f},
/prompt-logs and /json/{f} -- plus the non-admin .json preview/data gate.
"""
from __future__ import annotations

import json
import logging

import app.services.artifact_service as artifact_service_mod
from app.models import User, Run, Step
from app.auth import create_session_cookie, COOKIE_NAME
from sqlalchemy.orm import object_session


def _user_and_run(db, role="user", suffix=""):
    user = User(email=f"authz{suffix}@example.com", display_name="T", role=role)
    db.add(user)
    db.commit()
    db.refresh(user)
    run = Run(id=f"az{suffix or '1'}", user_id=user.id, status="complete",
              filename="cv.docx", file_type="docx")
    db.add(run)
    db.commit()
    return user, run


def _auth(client, user):
    client.cookies.set(COOKIE_NAME, create_session_cookie(user, object_session(user)))


class _EmptyStorage:
    """Never has the file -- forces every route onto the local-filesystem path."""

    def exists(self, run_id, key):
        return False

    def get_download_url(self, run_id, key, download_name=None):
        return None

    def get_file(self, run_id, key):
        raise FileNotFoundError(key)

    def list_files(self, run_id, prefix):
        return []


def _seed_local_file(monkeypatch, tmp_path, run_id, filename, content=b"data"):
    monkeypatch.setattr(artifact_service_mod, "_WEB_OUTPUTS_ROOT", tmp_path / "outputs")
    monkeypatch.setattr(artifact_service_mod, "_PIPELINE_OUTPUTS_ROOT", tmp_path / "pipeline_outputs")
    run_dir = tmp_path / "outputs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / filename).write_bytes(content)


class TestStepDetailAuthorization:
    def test_owner_gets_200(self, client, db, seed_simple_mode):
        user, run = _user_and_run(db, suffix="-step-owner")
        db.add(Step(run_id=run.id, step_number=1, stage_id="1a", step_name="Hierarchy",
                    status="complete"))
        db.commit()
        _auth(client, user)
        resp = client.get(f"/api/run/{run.id}/step/1")
        assert resp.status_code == 200

    def test_non_owner_gets_403(self, client, db, seed_simple_mode):
        _, run = _user_and_run(db, suffix="-step-o")
        other, _ = _user_and_run(db, suffix="-step-other")
        _auth(client, other)
        resp = client.get(f"/api/run/{run.id}/step/1")
        assert resp.status_code == 403

    def test_admin_gets_200(self, client, db, seed_simple_mode):
        _, run = _user_and_run(db, suffix="-step-a-owner")
        admin, _ = _user_and_run(db, role="admin", suffix="-step-admin")
        db.add(Step(run_id=run.id, step_number=1, stage_id="1a", step_name="Hierarchy",
                    status="complete"))
        db.commit()
        _auth(client, admin)
        resp = client.get(f"/api/run/{run.id}/step/1")
        assert resp.status_code == 200

    def test_unauthenticated_gets_401(self, client, db, seed_simple_mode):
        _, run = _user_and_run(db, suffix="-step-u")
        resp = client.get(f"/api/run/{run.id}/step/1")
        assert resp.status_code == 401


class TestStepDetailPreview:
    def test_absolute_output_path_still_gets_200_and_a_preview(
            self, client, db, seed_simple_mode, monkeypatch, tmp_path):
        """The orchestrator stores absolute paths in Step.output_files (every
        completed prod step checked, 2026-09-28). The preview must resolve the
        basename, not 400 the whole step-detail request on the leading "/"."""
        user, run = _user_and_run(db, suffix="-step-abs")
        _seed_local_file(monkeypatch, tmp_path, run.id, "cv_entries.json",
                         content=json.dumps({"entries": [{"text": "x"}]}).encode())
        db.add(Step(run_id=run.id, step_number=3, stage_id="2", step_name="Entries",
                    status="complete", output_files=json.dumps(
                        ["/app/src/unified_pipeline/outputs/stage_2_entry_extraction/cv_entries.json"])))
        db.commit()
        _auth(client, user)
        resp = client.get(f"/api/run/{run.id}/step/3")
        assert resp.status_code == 200, resp.text
        assert resp.json()["output_preview"] is not None


class TestDataFileAuthorization:
    def test_owner_gets_200(self, client, db, seed_simple_mode, monkeypatch, tmp_path):
        user, run = _user_and_run(db, suffix="-data-owner")
        _auth(client, user)
        monkeypatch.setattr("app.api.steps.get_storage", lambda: _EmptyStorage())
        _seed_local_file(monkeypatch, tmp_path, run.id, "cv.docx")
        resp = client.get(f"/api/run/{run.id}/data/cv.docx")
        assert resp.status_code == 200

    def test_non_owner_gets_403(self, client, db, seed_simple_mode, monkeypatch, tmp_path):
        _, run = _user_and_run(db, suffix="-data-o")
        other, _ = _user_and_run(db, suffix="-data-other")
        _auth(client, other)
        monkeypatch.setattr("app.api.steps.get_storage", lambda: _EmptyStorage())
        _seed_local_file(monkeypatch, tmp_path, run.id, "cv.docx")
        resp = client.get(f"/api/run/{run.id}/data/cv.docx")
        assert resp.status_code == 403

    def test_admin_gets_200(self, client, db, seed_simple_mode, monkeypatch, tmp_path):
        _, run = _user_and_run(db, suffix="-data-a-owner")
        admin, _ = _user_and_run(db, role="admin", suffix="-data-admin")
        _auth(client, admin)
        monkeypatch.setattr("app.api.steps.get_storage", lambda: _EmptyStorage())
        _seed_local_file(monkeypatch, tmp_path, run.id, "cv.docx")
        resp = client.get(f"/api/run/{run.id}/data/cv.docx")
        assert resp.status_code == 200

    def test_unauthenticated_gets_401(self, client, db, seed_simple_mode):
        _, run = _user_and_run(db, suffix="-data-u")
        resp = client.get(f"/api/run/{run.id}/data/cv.docx")
        assert resp.status_code == 401

    def test_non_admin_json_data_forbidden(self, client, db, seed_simple_mode, monkeypatch, tmp_path):
        """r3965862896#3: non-admin users cannot access stage JSON, download
        or preview, even as the run owner."""
        user, run = _user_and_run(db, suffix="-data-json-owner")
        _auth(client, user)
        monkeypatch.setattr("app.api.steps.get_storage", lambda: _EmptyStorage())
        _seed_local_file(monkeypatch, tmp_path, run.id, "stage1a.json", b"{}")
        resp = client.get(f"/api/run/{run.id}/data/stage1a.json")
        assert resp.status_code == 403

    def test_non_admin_json_preview_forbidden(self, client, db, seed_simple_mode, monkeypatch, tmp_path):
        user, run = _user_and_run(db, suffix="-data-json-prev")
        _auth(client, user)
        monkeypatch.setattr("app.api.steps.get_storage", lambda: _EmptyStorage())
        _seed_local_file(monkeypatch, tmp_path, run.id, "stage1a.json", b"{}")
        resp = client.get(f"/api/run/{run.id}/data/stage1a.json", params={"preview": "true"})
        assert resp.status_code == 403

    def test_non_admin_uppercase_json_data_forbidden(
        self, client, db, seed_simple_mode, monkeypatch, tmp_path
    ):
        """r3965801468, wire-level: a mutation check (restoring
        `Path(filename).name.endswith(".json")` at the admin gate) leaves
        FOO.JSON ungated for a non-admin owner -- this must go red under
        that mutant."""
        user, run = _user_and_run(db, suffix="-data-json-upper")
        _auth(client, user)
        monkeypatch.setattr("app.api.steps.get_storage", lambda: _EmptyStorage())
        _seed_local_file(monkeypatch, tmp_path, run.id, "STAGE1A.JSON", b"{}")
        resp = client.get(f"/api/run/{run.id}/data/STAGE1A.JSON")
        assert resp.status_code == 403

    def test_non_admin_uppercase_json_preview_forbidden(
        self, client, db, seed_simple_mode, monkeypatch, tmp_path
    ):
        user, run = _user_and_run(db, suffix="-data-json-upper-prev")
        _auth(client, user)
        monkeypatch.setattr("app.api.steps.get_storage", lambda: _EmptyStorage())
        _seed_local_file(monkeypatch, tmp_path, run.id, "STAGE1A.JSON", b"{}")
        resp = client.get(f"/api/run/{run.id}/data/STAGE1A.JSON", params={"preview": "true"})
        assert resp.status_code == 403

    def test_admin_uppercase_json_data_not_forbidden(
        self, client, db, seed_simple_mode, monkeypatch, tmp_path
    ):
        """The same FOO.JSON name is not blocked for an admin -- proves the
        403 above is the JSON gate, not a generic filename problem."""
        _, run = _user_and_run(db, suffix="-data-json-upper-owner")
        admin, _ = _user_and_run(db, role="admin", suffix="-data-json-upper-admin")
        _auth(client, admin)
        monkeypatch.setattr("app.api.steps.get_storage", lambda: _EmptyStorage())
        _seed_local_file(monkeypatch, tmp_path, run.id, "STAGE1A.JSON", b"{}")
        resp = client.get(f"/api/run/{run.id}/data/STAGE1A.JSON")
        assert resp.status_code != 403


class TestJsonContentAuthorization:
    """/json/{f} is require_admin -- only role=='admin' ever reaches 200; a
    non-admin owner is blocked before check_run_access even runs."""

    def test_non_admin_owner_gets_403(self, client, db, seed_simple_mode, monkeypatch, tmp_path):
        user, run = _user_and_run(db, suffix="-json-owner")
        _auth(client, user)
        monkeypatch.setattr("app.api.steps.get_storage", lambda: _EmptyStorage())
        _seed_local_file(monkeypatch, tmp_path, run.id, "stage1a.json", b'{"x": 1}')
        resp = client.get(f"/api/run/{run.id}/json/stage1a.json")
        assert resp.status_code == 403

    def test_admin_gets_200(self, client, db, seed_simple_mode, monkeypatch, tmp_path):
        _, run = _user_and_run(db, suffix="-json-a-owner")
        admin, _ = _user_and_run(db, role="admin", suffix="-json-admin")
        _auth(client, admin)
        monkeypatch.setattr("app.api.steps.get_storage", lambda: _EmptyStorage())
        _seed_local_file(monkeypatch, tmp_path, run.id, "stage1a.json", b'{"x": 1}')
        resp = client.get(f"/api/run/{run.id}/json/stage1a.json")
        assert resp.status_code == 200

    def test_malformed_local_json_returns_500_and_logs_warning(
        self, client, db, seed_simple_mode, monkeypatch, tmp_path, caplog
    ):
        """r3965796995/get_json_content: the local-branch read must narrow
        its except and log, not swallow silently -- a mutation check
        (restoring `except Exception: raise internal_error(...)` with no log)
        leaves this warning missing while the 500 stays the same, so this
        must check caplog, not just the status code."""
        admin, run = _user_and_run(db, role="admin", suffix="-json-malformed")
        _auth(client, admin)
        monkeypatch.setattr("app.api.steps.get_storage", lambda: _EmptyStorage())
        _seed_local_file(monkeypatch, tmp_path, run.id, "broken.json", b"{not valid json")

        with caplog.at_level(logging.WARNING):
            resp = client.get(f"/api/run/{run.id}/json/broken.json")

        assert resp.status_code == 500
        assert any("JSON viewer read failed" in r.getMessage() for r in caplog.records)

    def test_unauthenticated_gets_401(self, client, db, seed_simple_mode):
        _, run = _user_and_run(db, suffix="-json-u")
        resp = client.get(f"/api/run/{run.id}/json/stage1a.json")
        assert resp.status_code == 401


class TestPromptLogsAuthorization:
    def test_owner_gets_200(self, client, db, seed_simple_mode):
        user, run = _user_and_run(db, suffix="-pl-owner")
        _auth(client, user)
        resp = client.get(f"/api/run/{run.id}/prompt-logs")
        assert resp.status_code == 200

    def test_non_owner_gets_403(self, client, db, seed_simple_mode):
        _, run = _user_and_run(db, suffix="-pl-o")
        other, _ = _user_and_run(db, suffix="-pl-other")
        _auth(client, other)
        resp = client.get(f"/api/run/{run.id}/prompt-logs")
        assert resp.status_code == 403

    def test_admin_gets_200(self, client, db, seed_simple_mode):
        _, run = _user_and_run(db, suffix="-pl-a-owner")
        admin, _ = _user_and_run(db, role="admin", suffix="-pl-admin")
        _auth(client, admin)
        resp = client.get(f"/api/run/{run.id}/prompt-logs")
        assert resp.status_code == 200

    def test_unauthenticated_gets_401(self, client, db, seed_simple_mode):
        _, run = _user_and_run(db, suffix="-pl-u")
        resp = client.get(f"/api/run/{run.id}/prompt-logs")
        assert resp.status_code == 401


class TestPromptLogsBounds:
    """r3965818983: unbounded prompt-log count -- limit/offset now bound and
    page the file listing."""

    def _seed_run_with_logs(self, db, monkeypatch, suffix, n):
        user, run = _user_and_run(db, suffix=suffix)
        db.add(Step(run_id=run.id, step_number=1, stage_id="3a", step_name="Taxonomy",
                    status="complete"))
        db.commit()
        filenames = [f"2026-01-0{min(i, 9)}_00-00-{i:02d}_stage_3a_{i:012x}.txt" for i in range(n)]
        files = {f"prompt_logs/{name}": b"content" for name in filenames}

        class _Storage:
            def exists(self, run_id, key):
                return False

            def get_download_url(self, run_id, key, download_name=None):
                return None

            def list_files(self, run_id, prefix):
                return list(files.keys())

            def get_file(self, run_id, key):
                return files[key]

        monkeypatch.setattr("app.api.steps.get_storage", lambda: _Storage())
        return user, run

    def test_default_limit_caps_response_and_reports_total(self, client, db, seed_simple_mode, monkeypatch):
        user, run = self._seed_run_with_logs(db, monkeypatch, "-pl-bounds", 60)
        _auth(client, user)
        resp = client.get(f"/api/run/{run.id}/prompt-logs")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["logs"]) == 50  # default limit
        assert body["total"] == 60
        assert body["truncated"] is True

    def test_limit_and_offset_page_through(self, client, db, seed_simple_mode, monkeypatch):
        user, run = self._seed_run_with_logs(db, monkeypatch, "-pl-page", 10)
        _auth(client, user)
        resp = client.get(f"/api/run/{run.id}/prompt-logs", params={"limit": 5, "offset": 5})
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["logs"]) == 5
        assert body["total"] == 10
        assert body["truncated"] is False

    def test_limit_over_max_is_rejected(self, client, db, seed_simple_mode, monkeypatch):
        user, run = self._seed_run_with_logs(db, monkeypatch, "-pl-overmax", 1)
        _auth(client, user)
        resp = client.get(f"/api/run/{run.id}/prompt-logs", params={"limit": 500})
        assert resp.status_code == 422
