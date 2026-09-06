"""Regression guards for the Teams run-notification service (issue #154).

Covers:
  - build_teams_payload / build_started_payload / build_feedback_payload field
    mapping and title color, plus run-link derivation from
    CVICHE_ALLOWED_ORIGINS.
  - notify_run_* / notify_feedback_submitted best-effort contract: no-op when
    unconfigured, and a POST exception / non-2xx is swallowed (logged, never
    raised).

See test_feedback_notification.py for the feedback_routes.submit_feedback
call-site wiring -- these tests only cover the notifications helpers.

Cards are Adaptive Cards in the Teams Workflows envelope:
  {"type": "message", "attachments": [{"content": <AdaptiveCard>}]}
"""
import os
os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

import logging
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.services import notifications


@pytest.fixture(autouse=True)
def _clear_webhook_validation_cache():
    """_validate_webhook_url (notifications.py) is memoized with lru_cache so
    a misconfigured URL warns once, not per POST. Clear it before every test
    so one test's cached validation result/warning can't leak into another's
    assertions about a different URL value."""
    notifications._validate_webhook_url.cache_clear()


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


# --- fallbackText / summary (no "cards.unsupported") ----------------------

def test_terminal_payload_has_fallback_and_summary(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    doctor = {"counts": {"ERROR": 1, "WARN": 0}, "findings": [
        {"severity": "ERROR", "lint": "grants_dropped"}]}

    payload = notifications.build_teams_payload(
        _run(), {"totalScore": 87, "band": "GREEN"}, doctor=doctor)

    # message summary and card fallbackText both present, useful, and identical.
    summary = payload["summary"]
    assert _card(payload)["fallbackText"] == summary
    assert summary.startswith("CViche run A1B2C3 complete")
    assert "score 87 (GREEN)" in summary  # surfaces that skip the card still see it
    assert "grants_dropped" in summary


def test_terminal_summary_degrades_without_score_or_doctor(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    payload = notifications.build_teams_payload(_run(status="failed"), None)

    assert payload["summary"] == "CViche run A1B2C3 failed"  # bare status, no " — "
    assert _card(payload)["fallbackText"] == payload["summary"]


def test_started_payload_has_fallback_and_summary(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    payload = notifications.build_started_payload(_run(status="created"))

    assert payload["summary"] == "CViche run A1B2C3 started"
    assert _card(payload)["fallbackText"] == payload["summary"]


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


@pytest.mark.parametrize(
    "doctor",
    [
        {"counts": {"ERROR": "abc"}},
        {"counts": {"ERROR": float("inf")}},
        {"counts": ["not", "a", "dict"]},
        {"counts": {"ERROR": 1}, "findings": [None]},
    ],
)
def test_payload_doctor_line_omitted_and_warned_on_malformed_report(
    monkeypatch, caplog, doctor
):
    """A malformed doctor dict must cost only the Doctor line, never the card
    (CODING STANDARDS §5.4: narrowed except, but logged, never silently
    swallowed)."""
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    with caplog.at_level(logging.WARNING):
        facts = _facts(notifications.build_teams_payload(_run(), None, doctor=doctor))

    assert "Doctor" not in facts
    assert any(
        "Doctor report summary failed to parse" in r.message for r in caplog.records
    )


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


# --- build_feedback_payload -------------------------------------------------

def _feedback(**overrides):
    """A Feedback-like stand-in; the service only reads attributes."""
    base = dict(
        reviewer_role="self",
        overall_accuracy=None,
        overall_usefulness=4,
        likelihood_to_recommend=4,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_feedback_payload_maps_all_fields(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    feedback = _feedback(overall_accuracy=8)

    payload = notifications.build_feedback_payload(feedback, _run(), submitter="Jane Doe")

    facts = _facts(payload)
    assert facts["Run ID"] == "A1B2C3"
    assert facts["File"] == "cv.docx"
    assert facts["Submitted by"] == "Jane Doe"
    assert facts["Reviewer role"] == "self"
    assert facts["Overall usefulness"] == "4/5"
    assert facts["Likelihood to recommend"] == "4/5"
    assert facts["Overall accuracy"] == "8/10"


def test_feedback_payload_omits_optional_fields_when_absent(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    facts = _facts(notifications.build_feedback_payload(_feedback(), _run()))

    assert "Submitted by" not in facts
    assert "Overall accuracy" not in facts


def test_feedback_payload_never_includes_free_text_fields(monkeypatch):
    """biggest_issue/issue_locations are reviewer-typed free text that can name
    a person or quote CV content -- never put it on the card, even when set."""
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    feedback = _feedback(
        biggest_issue="Jane Smith's grant dates were wrong",
        issue_locations=["section M"],
    )

    payload = notifications.build_feedback_payload(feedback, _run())

    assert "Biggest issue" not in _facts(payload)
    assert "Jane Smith" not in str(payload)


@pytest.mark.parametrize("recommend,color", [
    (1, "attention"), (2, "attention"), (3, "default"), (4, "good"), (5, "good"),
])
def test_feedback_payload_color_by_recommend_score(monkeypatch, recommend, color):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    payload = notifications.build_feedback_payload(
        _feedback(likelihood_to_recommend=recommend), _run())

    assert _title(payload)["color"] == color


def test_feedback_payload_has_fallback_and_summary(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    payload = notifications.build_feedback_payload(_feedback(), _run())

    assert payload["summary"].startswith("CViche feedback on run A1B2C3")
    assert _card(payload)["fallbackText"] == payload["summary"]


def test_feedback_payload_action_uses_first_allowed_origin(monkeypatch):
    monkeypatch.setenv("CVICHE_ALLOWED_ORIGINS", "https://cviche.weill.cornell.edu/")

    payload = notifications.build_feedback_payload(_feedback(), _run())

    assert _card(payload)["actions"][0]["url"] == (
        "https://cviche.weill.cornell.edu/run/A1B2C3"
    )


# --- notify_feedback_submitted ----------------------------------------------

def test_notify_feedback_is_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv("CVICHE_TEAMS_WEBHOOK_URL", raising=False)

    def _fail_post(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("requests.post should not be called when unconfigured")

    monkeypatch.setattr(notifications.requests, "post", _fail_post)

    assert notifications.notify_feedback_submitted(_feedback(), _run()) is None


def test_notify_feedback_posts_when_configured(monkeypatch):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    captured = {}

    def _ok_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(notifications.requests, "post", _ok_post)

    notifications.notify_feedback_submitted(_feedback(), _run(), submitter="Jane Doe")

    assert captured["url"] == "https://webhook.example/teams"
    assert _facts(captured["json"])["Submitted by"] == "Jane Doe"


def test_notify_feedback_swallows_post_exception(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")

    def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(notifications.requests, "post", _boom)

    with caplog.at_level(logging.WARNING):
        result = notifications.notify_feedback_submitted(_feedback(), _run())

    assert result is None
    assert any("Teams notification failed" in r.message for r in caplog.records)


# --- #309: webhook URL validation -------------------------------------------

def _fail_post(*args, **kwargs):  # pragma: no cover - must not be reached
    raise AssertionError("requests.post should not be called for an invalid webhook URL")


def test_webhook_rejects_non_https_scheme(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "http://webhook.example/teams")
    mock_post = MagicMock()
    monkeypatch.setattr(notifications.requests, "post", mock_post)

    with caplog.at_level(logging.WARNING):
        result = notifications.notify_run_terminal(_run(), None)

    assert result is None
    mock_post.assert_not_called()
    assert any("not a valid https URL" in r.message for r in caplog.records)


def test_webhook_rejects_malformed_url(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "not-a-url-at-all")
    mock_post = MagicMock()
    monkeypatch.setattr(notifications.requests, "post", mock_post)

    with caplog.at_level(logging.WARNING):
        result = notifications.notify_run_terminal(_run(), None)

    assert result is None
    mock_post.assert_not_called()
    assert any("not a valid https URL" in r.message for r in caplog.records)


def test_webhook_invalid_url_warns_once_not_per_call(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "ftp://webhook.example/teams")
    mock_post = MagicMock()
    monkeypatch.setattr(notifications.requests, "post", mock_post)

    with caplog.at_level(logging.WARNING):
        notifications.notify_run_terminal(_run(), None)
        notifications.notify_run_terminal(_run(), None)
        notifications.notify_run_started(_run())

    mock_post.assert_not_called()
    warnings = [r for r in caplog.records if "not a valid https URL" in r.message]
    assert len(warnings) == 1


# --- #309: run id is URL-encoded in the action button -----------------------

def test_action_button_encodes_run_id(monkeypatch):
    monkeypatch.setenv("CVICHE_ALLOWED_ORIGINS", "https://cviche.weill.cornell.edu")
    run = _run(id="a/b?c")

    payload = notifications.build_teams_payload(run, None)

    action = _card(payload)["actions"][0]
    assert action["url"] == "https://cviche.weill.cornell.edu/run/a%2Fb%3Fc"


# --- #309: _post exception narrowing ----------------------------------------

def test_notify_swallows_connection_error_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")

    def _boom(*args, **kwargs):
        raise notifications.requests.ConnectionError("network down")

    monkeypatch.setattr(notifications.requests, "post", _boom)

    with caplog.at_level(logging.WARNING):
        result = notifications.notify_run_terminal(_run(), None)

    assert result is None
    records = [r for r in caplog.records if "Teams notification failed" in r.message]
    assert len(records) == 1
    assert records[0].levelname == "WARNING"
    assert records[0].exc_info is None


def test_notify_swallows_type_error_with_exception_log(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")

    def _boom(*args, **kwargs):
        raise TypeError("payload is not JSON-serialisable")

    monkeypatch.setattr(notifications.requests, "post", _boom)

    with caplog.at_level(logging.WARNING):
        result = notifications.notify_run_terminal(_run(), None)

    assert result is None
    records = [r for r in caplog.records if "unexpected error" in r.message]
    assert len(records) == 1
    assert records[0].levelname == "ERROR"
    assert records[0].exc_info is not None


# --- #309: card text sanitising ---------------------------------------------

def test_card_text_strips_control_chars_and_truncates():
    value = "cv\x07report" + ("x" * 500)

    result = notifications._card_text(value, notifications._FACT_MAX_CHARS)

    assert "\x07" not in result
    assert len(result) <= notifications._FACT_MAX_CHARS


def test_filename_control_chars_stripped_from_terminal_card(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    run = _run(filename="cv\x07report" + ("x" * 500))

    facts = _facts(notifications.build_teams_payload(run, None))

    assert "\x07" not in facts["File"]
    assert len(facts["File"]) <= notifications._FACT_MAX_CHARS


def test_submitter_control_chars_stripped_from_started_card(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    payload = notifications.build_started_payload(_run(), submitter="Jane\x07Doe" + ("z" * 500))

    submitted_by = _facts(payload)["Submitted by"]
    assert "\x07" not in submitted_by
    assert len(submitted_by) <= notifications._FACT_MAX_CHARS


def test_feedback_card_control_chars_stripped_from_run_id_filename_and_submitter(
    monkeypatch,
):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    run = _run(id="run\x07id" + ("a" * 500), filename="cv\x07report" + ("x" * 500))

    payload = notifications.build_feedback_payload(
        _feedback(), run, submitter="Jane\x07Doe" + ("z" * 500)
    )

    facts = _facts(payload)
    for value in (facts["Run ID"], facts["File"], facts["Submitted by"]):
        assert "\x07" not in value
        assert len(value) <= notifications._FACT_MAX_CHARS


# --- #309: notify_* wrappers never raise (already pinned; named for #309) --
# test_notify_swallows_post_exception, test_notify_feedback_swallows_post_exception
# (above) and test_notify_swallows_connection_error_with_warning /
# test_notify_swallows_type_error_with_exception_log (above) all assert
# notify_run_terminal/notify_feedback_submitted return None despite the
# underlying requests.post call raising. test_feedback_notification.py's
# test_submit_feedback_succeeds_even_if_notification_raises additionally pins
# this at the route call-site.
