"""Regression guards for the Teams run-notification service (issue #154).

Covers:
  - build_teams_payload field mapping for success (with score) and failure
    (no score -> "n/a"), plus run-link derivation from CVICHE_ALLOWED_ORIGINS.
  - notify_run_terminal best-effort contract: no-op when unconfigured, and a
    POST exception is swallowed (logged, never raised).
"""
import os
os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

import logging
from types import SimpleNamespace

import pytest

from app.services import notifications


def _run(**overrides):
    """A Run-like stand-in; the service only reads attributes (matches the
    SimpleNamespace style used by the auto_retry tests)."""
    base = dict(
        id="A1B2C3",
        filename="cv.docx",
        status="complete",
        total_cost=0.1234,
        total_duration_seconds=42,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


# --- build_teams_payload --------------------------------------------------

def test_payload_maps_all_fields_with_score(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    run = _run()
    score = {"totalScore": 87, "band": "GREEN (ship)"}

    payload = notifications.build_teams_payload(run, score)

    assert payload["@type"] == "MessageCard"
    facts = {f["name"]: f["value"] for f in payload["sections"][0]["facts"]}
    assert facts["Run ID"] == "A1B2C3"
    assert facts["File"] == "cv.docx"
    assert facts["Status"] == "complete"
    assert facts["Quality score"] == "87 (GREEN (ship))"
    assert facts["Total cost"] == "$0.1234"
    assert facts["Duration"] == "42s"


def test_payload_renders_na_on_failure_without_score(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    run = _run(status="failed", total_cost=0.0, total_duration_seconds=None)

    payload = notifications.build_teams_payload(run, None)

    facts = {f["name"]: f["value"] for f in payload["sections"][0]["facts"]}
    assert facts["Status"] == "failed"
    assert facts["Quality score"] == "n/a"
    assert facts["Duration"] == "n/a"
    # Failed runs use the red theme.
    assert payload["themeColor"] == "E01E5A"


def test_payload_run_link_uses_first_allowed_origin(monkeypatch):
    monkeypatch.setenv(
        "CVICHE_ALLOWED_ORIGINS",
        "https://cviche.weill.cornell.edu/,https://other.example.com",
    )
    run = _run()

    payload = notifications.build_teams_payload(run, {"totalScore": 1, "band": "RED"})

    action = payload["potentialAction"][0]
    assert action["@type"] == "OpenUri"
    assert action["targets"][0]["uri"] == "https://cviche.weill.cornell.edu/run/A1B2C3"


def test_payload_omits_action_when_no_origin_configured(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    run = _run()

    payload = notifications.build_teams_payload(run, None)

    assert "potentialAction" not in payload


# --- notify_run_terminal --------------------------------------------------

def test_notify_is_noop_when_webhook_unconfigured(monkeypatch):
    monkeypatch.delenv("CVICHE_TEAMS_WEBHOOK_URL", raising=False)

    called = {"posted": False}

    def _fail_post(*args, **kwargs):  # pragma: no cover - must not be reached
        called["posted"] = True
        raise AssertionError("requests.post should not be called when unconfigured")

    monkeypatch.setattr(notifications.requests, "post", _fail_post)

    # Returns None, raises nothing, and never POSTs.
    assert notifications.notify_run_terminal(_run(), {"totalScore": 90, "band": "GREEN"}) is None
    assert called["posted"] is False


def test_notify_posts_when_configured(monkeypatch):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    captured = {}

    def _ok_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(notifications.requests, "post", _ok_post)

    notifications.notify_run_terminal(_run(), {"totalScore": 90, "band": "GREEN"})

    assert captured["url"] == "https://webhook.example/teams"
    assert captured["json"]["@type"] == "MessageCard"


def test_notify_swallows_post_exception(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")

    def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(notifications.requests, "post", _boom)

    with caplog.at_level(logging.WARNING):
        # Must not raise despite the POST blowing up.
        result = notifications.notify_run_terminal(_run(), None)

    assert result is None
    assert any("Teams notification failed" in r.message for r in caplog.records)


def test_notify_logs_warning_on_non_2xx(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")

    monkeypatch.setattr(
        notifications.requests,
        "post",
        lambda *a, **k: SimpleNamespace(status_code=500),
    )

    with caplog.at_level(logging.WARNING):
        result = notifications.notify_run_terminal(_run(), None)

    assert result is None
    assert any("returned HTTP 500" in r.message for r in caplog.records)
