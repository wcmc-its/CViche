"""Tests for /livez, /readyz, and the deprecated /health alias."""
from unittest.mock import patch

import pytest
from sqlalchemy.exc import OperationalError

from app.database import get_db


class TestLivez:
    def test_livez_returns_ok(self, client):
        response = client.get("/livez")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_livez_does_not_touch_db(self, client):
        """Liveness must NOT call the DB. Override the DB dep to raise; /livez
        must still return 200."""
        from app.main import app

        def broken_db():
            raise RuntimeError("DB should not be called from /livez")

        app.dependency_overrides[get_db] = broken_db
        try:
            response = client.get("/livez")
            assert response.status_code == 200
        finally:
            # Restore the test-default override; the client fixture clears
            # everything when the test exits, but we revert in case other
            # assertions in this test block run.
            pass


class TestReadyz:
    def test_readyz_healthy_db_returns_200(self, client):
        response = client.get("/readyz")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["checks"]["db"] == {"ok": True}

    def test_readyz_reports_notifications_unconfigured(self, client, monkeypatch):
        """#782 D4: /readyz surfaces notification-config state without ever
        failing readiness on it (ok stays True either way)."""
        monkeypatch.delenv("CVICHE_TEAMS_WEBHOOK_URL", raising=False)
        response = client.get("/readyz")
        assert response.status_code == 200
        body = response.json()
        assert body["checks"]["notifications"] == {
            "ok": True, "configured": False, "valid": False,
        }

    def test_readyz_reports_notifications_configured_and_valid(self, client, monkeypatch):
        monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
        response = client.get("/readyz")
        assert response.status_code == 200
        body = response.json()
        assert body["checks"]["notifications"] == {
            "ok": True, "configured": True, "valid": True,
        }

    def test_readyz_broken_db_returns_503(self, client):
        from app.main import app

        class BrokenSession:
            def execute(self, *args, **kwargs):
                raise OperationalError("SELECT 1", {}, Exception("connection refused"))

        def override():
            yield BrokenSession()

        app.dependency_overrides[get_db] = override
        try:
            response = client.get("/readyz")
            assert response.status_code == 503
            body = response.json()
            assert body["status"] == "not_ready"
            assert body["checks"]["db"]["ok"] is False
            assert "connection refused" in body["checks"]["db"]["error"]
        finally:
            pass

    def test_readyz_local_storage_skips_s3_check(self, client, monkeypatch):
        monkeypatch.setenv("CVICHE_STORAGE_BACKEND", "local")
        response = client.get("/readyz")
        assert response.status_code == 200
        body = response.json()
        assert "s3" not in body["checks"]

    def test_readyz_s3_backend_missing_bucket_returns_503(self, client, monkeypatch):
        monkeypatch.setenv("CVICHE_STORAGE_BACKEND", "s3")
        monkeypatch.delenv("CVICHE_S3_BUCKET", raising=False)

        # Force the bucket lookup to resolve empty regardless of any ambient
        # auth_config.yaml (which may supply a real bucket under its s3:
        # section). Delegate every other key to the real resolver so the
        # storage-backend lookup still sees the env override above.
        import app.main as main_mod

        real_get_config = main_mod.get_config

        def fake_get_config(section, key, default=None):
            if key == "CVICHE_S3_BUCKET":
                return "", "default"
            return real_get_config(section, key, default)

        with patch.object(main_mod, "get_config", side_effect=fake_get_config):
            response = client.get("/readyz")
        assert response.status_code == 503
        body = response.json()
        assert body["checks"]["s3"]["ok"] is False
        assert "CVICHE_S3_BUCKET" in body["checks"]["s3"]["error"]

    def test_readyz_s3_backend_reachable_bucket_returns_200(self, client, monkeypatch):
        """When boto3.head_bucket succeeds the s3 check passes."""
        monkeypatch.setenv("CVICHE_STORAGE_BACKEND", "s3")
        monkeypatch.setenv("CVICHE_S3_BUCKET", "test-bucket")

        fake_client = type("FakeS3", (), {"head_bucket": lambda self, Bucket: None})()
        with patch("boto3.client", return_value=fake_client):
            response = client.get("/readyz")
        assert response.status_code == 200
        body = response.json()
        assert body["checks"]["s3"] == {"ok": True}

    def test_readyz_s3_backend_unreachable_bucket_returns_503(self, client, monkeypatch):
        monkeypatch.setenv("CVICHE_STORAGE_BACKEND", "s3")
        monkeypatch.setenv("CVICHE_S3_BUCKET", "test-bucket")

        class FakeS3:
            def head_bucket(self, Bucket):
                raise Exception("403 Forbidden")

        with patch("boto3.client", return_value=FakeS3()):
            response = client.get("/readyz")
        assert response.status_code == 503
        body = response.json()
        assert body["checks"]["s3"]["ok"] is False
        assert "403" in body["checks"]["s3"]["error"]


class TestHealthAlias:
    def test_health_still_returns_200(self, client):
        """/health remains as a backward-compat alias; existing wiring must
        not break."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "healthy"}
