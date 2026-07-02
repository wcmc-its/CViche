"""Regression guards for the Teams run-notification service (issue #154).

Covers:
  - build_teams_payload / build_started_payload field mapping and title color,
    plus run-link derivation from CVICHE_ALLOWED_ORIGINS.
  - notify_run_* best-effort contract: no-op when unconfigured, and a POST
    exception / non-2xx is swallowed (logged, never raised).

Cards are Adaptive Cards in the Teams Workflows envelope:
  {"type": "message", "attachments": [{"content": <AdaptiveCard>}]}
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


def _card(payload):
    """The Adaptive Card content out of the Teams message envelope."""
    assert payload["type"] == "message"
    card = payload["attachments"][0]["content"]
    assert card["type"] == "AdaptiveCard"
    return card


def _facts(payload):
    """{name: value} from the card's FactSet."""
    card = _card(payload)
    factset = next(b for b in card["body"] if b["type"] == "FactSet")
    return {f["title"]: f["value"] for f in factset["facts"]}


def _title(payload):
    """The first TextBlock (heading) of the card."""
    card = _card(payload)
    return next(b for b in card["body"] if b["type"] == "TextBlock")


# --- build_teams_payload (terminal) ---------------------------------------

def test_payload_maps_all_fields_with_score(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    run = _run()
    score = {"totalScore": 87, "band": "GREEN (ship)"}

    payload = notifications.build_teams_payload(run, score)

    facts = _facts(payload)
    assert facts["Run ID"] == "A1B2C3"
    assert facts["File"] == "cv.docx"
    assert facts["Status"] == "complete"
    assert facts["Quality score"] == "87 (GREEN (ship))"
    assert facts["Total cost"] == "$0.1234"
    assert facts["Duration"] == "42s"
    # complete -> green title
    assert _title(payload)["color"] == "good"


def test_payload_renders_na_on_failure_without_score(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    run = _run(status="failed", total_cost=0.0, total_duration_seconds=None)

    payload = notifications.build_teams_payload(run, None)

    facts = _facts(payload)
    assert facts["Status"] == "failed"
    assert facts["Quality score"] == "n/a"
    assert facts["Duration"] == "n/a"
    # Failed runs use the red title color.
    assert _title(payload)["color"] == "attention"


def test_payload_run_link_uses_first_allowed_origin(monkeypatch):
    monkeypatch.setenv(
        "CVICHE_ALLOWED_ORIGINS",
        "https://cviche.weill.cornell.edu/,https://other.example.com",
    )
    run = _run()

    payload = notifications.build_teams_payload(run, {"totalScore": 1, "band": "RED"})

    action = _card(payload)["actions"][0]
    assert action["type"] == "Action.OpenUrl"
    assert action["url"] == "https://cviche.weill.cornell.edu/run/A1B2C3"


def test_payload_omits_action_when_no_origin_configured(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    run = _run()

    payload = notifications.build_teams_payload(run, None)

    assert "actions" not in _card(payload)


# --- build_started_payload ------------------------------------------------

def test_started_payload_has_no_score_or_cost(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    run = _run(status="created")  # start fires before status flips in-memory

    payload = notifications.build_started_payload(run)

    facts = _facts(payload)
    assert facts == {"Run ID": "A1B2C3", "File": "cv.docx", "Status": "started"}
    # The lean start card omits the terminal-only fields entirely.
    assert "Quality score" not in facts
    assert "Total cost" not in facts
    # started -> blue title
    assert _title(payload)["color"] == "accent"


def test_payload_includes_submitter_when_given(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    started = notifications.build_started_payload(_run(status="created"), submitter="Jane Doe")
    terminal = notifications.build_teams_payload(_run(), {"totalScore": 9, "band": "GREEN"}, submitter="Jane Doe")

    assert _facts(started)["Submitted by"] == "Jane Doe"
    assert _facts(terminal)["Submitted by"] == "Jane Doe"


def test_payload_omits_submitter_when_none(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    assert "Submitted by" not in _facts(notifications.build_started_payload(_run()))
    assert "Submitted by" not in _facts(notifications.build_teams_payload(_run(), None))


def test_started_payload_action_uses_first_allowed_origin(monkeypatch):
    monkeypatch.setenv("CVICHE_ALLOWED_ORIGINS", "https://cviche.weill.cornell.edu/")
    run = _run()

    payload = notifications.build_started_payload(run)

    assert _card(payload)["actions"][0]["url"] == (
        "https://cviche.weill.cornell.edu/run/A1B2C3"
    )


# --- doctor line on the terminal card ---------------------------------------

def test_payload_includes_doctor_summary(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    doctor = {
        "counts": {"ERROR": 1, "WARN": 2, "INFO": 3},
        "findings": [
            {"lint": "output_hygiene", "severity": "WARN"},
            {"lint": "segmentation", "severity": "ERROR"},
        ],
    }

    facts = _facts(notifications.build_teams_payload(_run(), None, doctor=doctor))

    # 3 substantive findings (INFO excluded); the most severe names the lint.
    assert facts["Doctor"] == "3 findings (top: segmentation)"


def test_payload_doctor_reports_zero_findings_when_clean(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    doctor = {"counts": {"ERROR": 0, "WARN": 0, "INFO": 7}, "findings": []}

    facts = _facts(notifications.build_teams_payload(_run(), None, doctor=doctor))

    assert facts["Doctor"] == "0 findings"


def test_payload_omits_doctor_when_unavailable(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    assert "Doctor" not in _facts(notifications.build_teams_payload(_run(), None))


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
    assert captured["json"]["type"] == "message"


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


# --- notify_run_started ---------------------------------------------------

def test_notify_started_is_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv("CVICHE_TEAMS_WEBHOOK_URL", raising=False)

    def _fail_post(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("requests.post should not be called when unconfigured")

    monkeypatch.setattr(notifications.requests, "post", _fail_post)

    assert notifications.notify_run_started(_run()) is None


def test_notify_started_posts_when_configured(monkeypatch):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    captured = {}

    def _ok_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(notifications.requests, "post", _ok_post)

    notifications.notify_run_started(_run())

    assert captured["url"] == "https://webhook.example/teams"
    assert _title(captured["json"])["text"].endswith("started")
