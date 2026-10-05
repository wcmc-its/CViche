"""Regression guards for the Teams run-notification service (issue #154, #309, #782).

Covers:
  - build_teams_payload / build_started_payload / build_feedback_payload field
    mapping and title color, plus run-link derivation from
    CVICHE_ALLOWED_ORIGINS. These now take typed DTOs (RunFacts/
    FeedbackFacts), not ORM objects -- see _run_facts/_feedback_facts below.
  - notify_run_* / notify_feedback_submitted best-effort contract: no-op when
    unconfigured, and any failure (payload-build or delivery) is swallowed
    (logged, never raised).
  - Delivery (#782): each notify_* enqueues onto a background worker
    (notifications._DELIVERY) and returns immediately; notifications.flush()
    makes a test's queued delivery synchronous before it asserts.

See test_feedback_notification.py for the feedback_routes.submit_feedback
call-site wiring -- these tests only cover the notifications helpers.

Cards are Adaptive Cards in the Teams Workflows envelope:
  {"type": "message", "attachments": [{"content": <AdaptiveCard>}]}
"""
import asyncio
import logging
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import quote

import pytest

from app.services import notifications


@pytest.fixture(autouse=True)
def _clear_webhook_validation_cache():
    """_validate_webhook_url (notifications.py) is memoized with lru_cache so
    a misconfigured URL warns once, not per POST. Clear it before every test
    so one test's cached validation result/warning can't leak into another's
    assertions about a different URL value."""
    notifications._validate_webhook_url.cache_clear()


@pytest.fixture(autouse=True)
def _no_real_sleep_in_retries(monkeypatch):
    """_deliver's retry backoff sleeps via notifications.time.sleep -- never
    let a test actually wait out a real backoff. A test that wants to assert
    on sleep's own arguments overrides this with its own monkeypatch."""
    monkeypatch.setattr(notifications.time, "sleep", lambda *_args, **_kwargs: None)


def _run(**overrides):
    """A Run-like stand-in; the service only reads attributes (matches the
    SimpleNamespace style used by the auto_retry tests). Feeds notify_*
    (which still accept ORM-like objects) and RunFacts.from_run."""
    base = dict(
        id="A1B2C3",
        filename="cv.docx",
        status="complete",
        total_cost=0.1234,
        total_duration_seconds=42,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _run_facts(**overrides) -> notifications.RunFacts:
    """A RunFacts DTO built the same way build_*_payload's real callers get
    one -- via RunFacts.from_run -- so tests exercise the actual conversion,
    not a hand-built shortcut."""
    return notifications.RunFacts.from_run(_run(**overrides))


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


def _feedback_facts(**overrides) -> notifications.FeedbackFacts:
    return notifications.FeedbackFacts.from_feedback(_feedback(**overrides))


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
    score = {"totalScore": 87, "band": "GREEN (ship)"}

    payload = notifications.build_teams_payload(_run_facts(), score)

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

    payload = notifications.build_teams_payload(
        _run_facts(status="failed", total_cost=0.0, total_duration_seconds=None), None
    )

    facts = _facts(payload)
    assert facts["Status"] == "failed"
    assert facts["Quality score"] == "n/a"
    assert facts["Duration"] == "n/a"
    # Failed runs use the red title color.
    assert _title(payload)["color"] == "attention"


def test_payload_marks_score_computed_on_missing_evidence(monkeypatch):
    """#745: a score computed with a scored artifact missing says so on the
    card and in the summary, with the count only -- never the
    missing_evidence strings, which can name CV files."""
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    score = {
        "totalScore": 20,
        "band": "RED (re-run / do-not-deliver)",
        "data_complete": False,
        "missing_evidence": ["docx: no docx found", "no entries.json found"],
    }

    payload = notifications.build_teams_payload(_run_facts(), score)

    expected = "20 (RED (re-run / do-not-deliver)) — incomplete: 2 file(s) missing or unreadable"
    assert _facts(payload)["Quality score"] == expected
    assert f"score {expected}" in payload["summary"]
    assert "no docx found" not in str(payload)


def test_payload_score_incomplete_without_evidence_list(monkeypatch):
    """data_complete False with no usable list still reads as incomplete."""
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    score = {"totalScore": 70, "band": "YELLOW (review)", "data_complete": False}

    payload = notifications.build_teams_payload(_run_facts(), score)

    assert _facts(payload)["Quality score"] == "70 (YELLOW (review)) — incomplete"


@pytest.mark.parametrize("data_complete", [True, None])
def test_payload_score_unmarked_when_complete_or_unknown(monkeypatch, data_complete):
    """A complete score, or a cache written before data_complete existed,
    renders as the plain number -- unknown is not incomplete."""
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    score = {"totalScore": 87, "band": "GREEN (ship)", "missing_evidence": ["x"]}
    if data_complete is not None:
        score["data_complete"] = data_complete

    payload = notifications.build_teams_payload(_run_facts(), score)

    assert _facts(payload)["Quality score"] == "87 (GREEN (ship))"


def test_payload_run_link_uses_first_allowed_origin(monkeypatch):
    monkeypatch.setenv(
        "CVICHE_ALLOWED_ORIGINS",
        "https://cviche.weill.cornell.edu/,https://other.example.com",
    )

    payload = notifications.build_teams_payload(_run_facts(), {"totalScore": 1, "band": "RED"})

    action = _card(payload)["actions"][0]
    assert action["type"] == "Action.OpenUrl"
    assert action["url"] == "https://cviche.weill.cornell.edu/run/A1B2C3"


def test_payload_omits_action_when_no_origin_configured(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    payload = notifications.build_teams_payload(_run_facts(), None)

    assert "actions" not in _card(payload)


# --- fallbackText / summary (no "cards.unsupported") ----------------------

def test_terminal_payload_has_fallback_and_summary(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    doctor = {"counts": {"ERROR": 1, "WARN": 0}, "findings": [
        {"severity": "ERROR", "lint": "grants_dropped"}]}

    payload = notifications.build_teams_payload(
        _run_facts(), {"totalScore": 87, "band": "GREEN"}, doctor_report=doctor)

    # message summary and card fallbackText both present, useful, and identical.
    summary = payload["summary"]
    assert _card(payload)["fallbackText"] == summary
    assert summary.startswith("CViche run A1B2C3 complete")
    assert "score 87 (GREEN)" in summary  # surfaces that skip the card still see it
    assert "grants_dropped" in summary


def test_terminal_summary_degrades_without_score_or_doctor(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    payload = notifications.build_teams_payload(_run_facts(status="failed"), None)

    assert payload["summary"] == "CViche run A1B2C3 failed"  # bare status, no " — "
    assert _card(payload)["fallbackText"] == payload["summary"]


def test_started_payload_has_fallback_and_summary(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    payload = notifications.build_started_payload(_run_facts(status="created"))

    assert payload["summary"] == "CViche run A1B2C3 started"
    assert _card(payload)["fallbackText"] == payload["summary"]


# --- build_started_payload ------------------------------------------------

def test_started_payload_has_no_score_or_cost(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    facts = _run_facts(status="created")  # start fires before status flips in-memory

    payload = notifications.build_started_payload(facts)

    payload_facts = _facts(payload)
    assert payload_facts == {"Run ID": "A1B2C3", "File": "cv.docx", "Status": "started"}
    # The lean start card omits the terminal-only fields entirely.
    assert "Quality score" not in payload_facts
    assert "Total cost" not in payload_facts
    # started -> blue title
    assert _title(payload)["color"] == "accent"


def test_payload_includes_submitter_when_given(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    started = notifications.build_started_payload(_run_facts(status="created"), submitter="Jane Doe")
    terminal = notifications.build_teams_payload(_run_facts(), {"totalScore": 9, "band": "GREEN"}, submitter="Jane Doe")

    assert _facts(started)["Submitted by"] == "Jane Doe"
    assert _facts(terminal)["Submitted by"] == "Jane Doe"


def test_payload_omits_submitter_when_none(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    assert "Submitted by" not in _facts(notifications.build_started_payload(_run_facts()))
    assert "Submitted by" not in _facts(notifications.build_teams_payload(_run_facts(), None))


def test_started_payload_action_uses_first_allowed_origin(monkeypatch):
    monkeypatch.setenv("CVICHE_ALLOWED_ORIGINS", "https://cviche.weill.cornell.edu/")

    payload = notifications.build_started_payload(_run_facts())

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

    facts = _facts(notifications.build_teams_payload(_run_facts(), None, doctor_report=doctor))

    # 3 substantive findings (INFO excluded); the most severe names the lint.
    assert facts["Doctor"] == "3 findings (top: segmentation)"


def test_payload_doctor_top_lint_carries_its_precision(monkeypatch):
    """#819: the top lint is shown with its PRECISION.md precision."""
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    doctor = {
        "counts": {"ERROR": 0, "WARN": 2},
        "findings": [{"lint": "missed_headers", "severity": "WARN"},
                     {"lint": "table_shape", "severity": "WARN"}],
        "lint_precision": {"missed_headers": {"precision": 0.5, "judged": 8, "label": "p~0.50 n=8"},
                           "table_shape": {"precision": 0.17, "judged": 6, "label": "p~0.17 n=6"}},
    }

    facts = _facts(notifications.build_teams_payload(_run_facts(), None, doctor_report=doctor))

    assert facts["Doctor"] == "2 findings (top: missed_headers, p~0.50 n=8)"


def test_payload_doctor_top_lint_unmeasured(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    doctor = {
        "counts": {"ERROR": 1},
        "findings": [{"lint": "no_output", "severity": "ERROR"}],
        "lint_precision": {"no_output": {"precision": None, "judged": 0, "label": "p unmeasured"}},
    }

    facts = _facts(notifications.build_teams_payload(_run_facts(), None, doctor_report=doctor))

    assert facts["Doctor"] == "1 findings (top: no_output, p unmeasured)"


@pytest.mark.parametrize("lint_precision", [
    None, "garbled", {"no_output": "garbled"}, {"no_output": {"label": 7}}, {"other": {"label": "x"}},
])
def test_payload_doctor_precision_missing_or_malformed_is_left_off(monkeypatch, lint_precision):
    """A report from before #819, or a garbled block, still gives the Doctor
    line, just without a precision label."""
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    doctor = {"counts": {"ERROR": 1}, "findings": [{"lint": "no_output", "severity": "ERROR"}],
              "lint_precision": lint_precision}

    facts = _facts(notifications.build_teams_payload(_run_facts(), None, doctor_report=doctor))

    assert facts["Doctor"] == "1 findings (top: no_output)"


def test_payload_doctor_precision_label_is_sanitised(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    doctor = {"counts": {"ERROR": 1}, "findings": [{"lint": "no_output", "severity": "ERROR"}],
              "lint_precision": {"no_output": {"label": "p~0.50\x07 n=8"}}}

    facts = _facts(notifications.build_teams_payload(_run_facts(), None, doctor_report=doctor))

    assert "\x07" not in facts["Doctor"]


def test_payload_doctor_reports_zero_findings_when_clean(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    doctor = {"counts": {"ERROR": 0, "WARN": 0, "INFO": 7}, "findings": []}

    facts = _facts(notifications.build_teams_payload(_run_facts(), None, doctor_report=doctor))

    assert facts["Doctor"] == "0 findings"


def test_payload_omits_doctor_when_unavailable(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    assert "Doctor" not in _facts(notifications.build_teams_payload(_run_facts(), None))


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
        facts = _facts(notifications.build_teams_payload(_run_facts(), None, doctor_report=doctor))

    assert "Doctor" not in facts
    assert any(
        "Doctor report summary failed to parse" in r.message for r in caplog.records
    )


# --- doctor `lint` is sanitised (#782 D2 / D8 point 3) -----------------------

def test_doctor_lint_control_chars_stripped_and_bounded(monkeypatch):
    """finding["lint"] is a doctor-module code literal by convention, not
    enforced -- it is interpolated straight into the Doctor fact and the
    card's summary/fallbackText, so it must be run through _card_text like
    any other unenforced string. Must fail against the pre-fix implementation
    (mutant M1: drop the _card_text call in _doctor_text)."""
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    bad_lint = "lint\x07id\x9f" + ("x" * 500)
    doctor = {"counts": {"ERROR": 1, "WARN": 0}, "findings": [
        {"severity": "ERROR", "lint": bad_lint}]}

    payload = notifications.build_teams_payload(_run_facts(), None, doctor_report=doctor)
    facts = _facts(payload)

    assert "\x07" not in facts["Doctor"]
    assert "\x9f" not in facts["Doctor"]
    # "N findings (top: ...)" wrapper adds a small fixed overhead around the
    # sanitised+truncated lint value -- the lint itself is bounded to
    # _FACT_MAX_CHARS, so the whole fact can't run away with an unbounded
    # lint id.
    assert len(facts["Doctor"]) <= notifications._FACT_MAX_CHARS + 20
    assert "\x07" not in payload["summary"]
    assert "\x9f" not in payload["summary"]


# --- notify_run_terminal --------------------------------------------------

def test_notify_is_noop_when_webhook_unconfigured(monkeypatch):
    monkeypatch.delenv("CVICHE_TEAMS_WEBHOOK_URL", raising=False)

    called = {"posted": False}

    def _fail_post(*args, **kwargs):  # pragma: no cover - must not be reached
        called["posted"] = True
        raise AssertionError("_SESSION.post should not be called when unconfigured")

    monkeypatch.setattr(notifications._SESSION, "post", _fail_post)

    # Returns None, raises nothing, and never POSTs (even once delivery --
    # queued on the background worker -- has had a chance to run).
    assert notifications.notify_run_terminal(_run(), {"totalScore": 90, "band": "GREEN"}) is None
    notifications.flush()
    assert called["posted"] is False


def test_notify_posts_when_configured(monkeypatch):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    captured = {}

    def _ok_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(notifications._SESSION, "post", _ok_post)

    notifications.notify_run_terminal(_run(), {"totalScore": 90, "band": "GREEN"})
    notifications.flush()

    assert captured["url"] == "https://webhook.example/teams"
    assert captured["json"]["type"] == "message"


def test_notify_swallows_unexpected_post_exception_with_exception_log(monkeypatch, caplog):
    """A non-request exception (e.g. a bad payload) is not transient -- it is
    logged once at ERROR with a traceback and NOT retried."""
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    calls = {"n": 0}

    def _boom(*args, **kwargs):
        calls["n"] += 1
        raise RuntimeError("network down")

    monkeypatch.setattr(notifications._SESSION, "post", _boom)

    with caplog.at_level(logging.WARNING):
        # Must not raise despite the POST blowing up.
        result = notifications.notify_run_terminal(_run(), None)
        notifications.flush()

    assert result is None
    assert calls["n"] == 1  # unexpected, non-transient -- no retry
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "unexpected error" in errors[0].message


def test_notify_retries_5xx_then_gives_up_with_one_summary_warning(monkeypatch, caplog):
    """Also pins the actual backoff: deleting time.sleep entirely, or
    replacing _RETRY_BACKOFF_SECONDS with all-zero delays, leaves every
    other assertion in this test (and test_notify_does_not_retry_non_
    retryable_4xx / test_notify_terminal_retries_and_swallows_timeout) green
    -- only the recorded sleep durations catch it."""
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    sleeps = []
    monkeypatch.setattr(notifications.time, "sleep", lambda seconds: sleeps.append(seconds))

    monkeypatch.setattr(
        notifications._SESSION,
        "post",
        lambda *a, **k: SimpleNamespace(status_code=500),
    )

    with caplog.at_level(logging.WARNING):
        result = notifications.notify_run_terminal(_run(), None)
        notifications.flush()

    assert result is None
    warnings = [
        r for r in caplog.records
        if r.name == "app.services.notifications" and r.levelname == "WARNING"
    ]
    assert len(warnings) == 1
    assert f"failed after {notifications._RETRY_ATTEMPTS} attempts" in warnings[0].message
    assert "HTTP 500" in warnings[0].message
    # 3 attempts -> 2 backoff sleeps, matching _RETRY_BACKOFF_SECONDS exactly
    # (not just "some number of sleeps" -- the actual durations).
    assert sleeps == [1.0, 2.0]


def test_notify_retries_backoff_stops_sleeping_once_it_succeeds(monkeypatch):
    """Fail once (retryable), then succeed on the second attempt: exactly one
    backoff sleep, for exactly the first interval -- proves the backoff
    schedule is read positionally (attempt 1's sleep), not just summed or
    ignored once a later attempt succeeds."""
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    sleeps = []
    monkeypatch.setattr(notifications.time, "sleep", lambda seconds: sleeps.append(seconds))

    calls = {"n": 0}

    def _fail_then_succeed(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            return SimpleNamespace(status_code=503)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(notifications._SESSION, "post", _fail_then_succeed)

    notifications.notify_run_terminal(_run(), None)
    notifications.flush()

    assert calls["n"] == 2
    assert sleeps == [1.0]


@pytest.mark.parametrize("status_code", [400, 404])
def test_notify_does_not_retry_non_retryable_4xx(monkeypatch, caplog, status_code):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    calls = {"n": 0}

    def _client_error(*a, **k):
        calls["n"] += 1
        return SimpleNamespace(status_code=status_code)

    monkeypatch.setattr(notifications._SESSION, "post", _client_error)

    with caplog.at_level(logging.WARNING):
        result = notifications.notify_run_terminal(_run(), None)
        notifications.flush()

    assert result is None
    assert calls["n"] == 1  # a client error is not retried
    warnings = [
        r for r in caplog.records
        if r.name == "app.services.notifications" and r.levelname == "WARNING"
    ]
    assert len(warnings) == 1
    assert "not retried" in warnings[0].message
    assert f"HTTP {status_code}" in warnings[0].message


@pytest.mark.parametrize("status_code", [429, 500, 503])
def test_notify_retries_retryable_status_codes(monkeypatch, status_code):
    """429 and 5xx ARE retried (unlike plain 4xx above) -- exhausts all
    _RETRY_ATTEMPTS rather than stopping after one."""
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    calls = {"n": 0}

    def _retryable_error(*a, **k):
        calls["n"] += 1
        return SimpleNamespace(status_code=status_code)

    monkeypatch.setattr(notifications._SESSION, "post", _retryable_error)

    notifications.notify_run_terminal(_run(), None)
    notifications.flush()

    assert calls["n"] == notifications._RETRY_ATTEMPTS


# --- notify_run_started ---------------------------------------------------

def test_notify_started_is_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv("CVICHE_TEAMS_WEBHOOK_URL", raising=False)

    def _fail_post(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("_SESSION.post should not be called when unconfigured")

    monkeypatch.setattr(notifications._SESSION, "post", _fail_post)

    assert notifications.notify_run_started(_run()) is None
    notifications.flush()


def test_notify_started_posts_when_configured(monkeypatch):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    captured = {}

    def _ok_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(notifications._SESSION, "post", _ok_post)

    notifications.notify_run_started(_run())
    notifications.flush()

    assert captured["url"] == "https://webhook.example/teams"
    assert _title(captured["json"])["text"].endswith("started")


def test_notify_started_swallows_post_exception(monkeypatch, caplog):
    """#782 D8 point 1: notify_run_started's own delivery failure isolation
    -- the terminal/feedback paths already had a version of this test, the
    started path did not."""
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")

    def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(notifications._SESSION, "post", _boom)

    with caplog.at_level(logging.WARNING):
        result = notifications.notify_run_started(_run())
        notifications.flush()

    assert result is None
    assert any("unexpected error" in r.message for r in caplog.records)


def test_notify_started_swallows_builder_exception(monkeypatch, caplog):
    """#782 D7: the builder call is inside notify_run_started's own
    never-raise boundary, not just _deliver's."""
    def _boom(*a, **k):
        raise RuntimeError("bad run object")

    monkeypatch.setattr(notifications, "build_started_payload", _boom)

    with caplog.at_level(logging.ERROR):
        result = notifications.notify_run_started(_run())

    assert result is None
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "payload build failed" in errors[0].message


# --- batch upload (#1114) ------------------------------------------------------

def _capture_posts(monkeypatch):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    posted = []

    def _ok_post(url, json=None, timeout=None):
        posted.append(json)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(notifications._SESSION, "post", _ok_post)
    return posted


def test_run_facts_carry_the_batch_id():
    assert _run_facts(batch_id="BATCHA").batch_id == "BATCHA"
    assert _run_facts().batch_id is None


def test_notify_started_posts_nothing_for_a_run_in_a_batch(monkeypatch):
    """A batch posts one "batch submitted" card instead; its runs' started
    cards would bury it (up to 50 of them)."""
    posted = _capture_posts(monkeypatch)

    notifications.notify_run_started(_run(batch_id="BATCHA"), batch_files_submitted=2)
    notifications.notify_run_started(_run(id="SNGL01", batch_id=None))
    notifications.flush()

    assert [_title(p)["text"] for p in posted] == ["CViche run SNGL01 started"]


def test_notify_started_posts_for_a_one_file_batch_run(monkeypatch):
    """#1340: a single upload with "Email me when job completes" ticked is a
    one-file batch; it posts no batch card, so its run posts the started card."""
    posted = _capture_posts(monkeypatch)

    notifications.notify_run_started(_run(id="ONEFIL", batch_id="BATCHA"), batch_files_submitted=1)
    notifications.flush()

    assert [_title(p)["text"] for p in posted] == ["CViche run ONEFIL started"]


def test_notify_started_posts_nothing_for_a_batch_run_of_unknown_size(monkeypatch):
    """No size supplied for a batch run: the bulk-batch default, never a flood of cards."""
    posted = _capture_posts(monkeypatch)

    notifications.notify_run_started(_run(batch_id="BATCHA"))
    notifications.flush()

    assert posted == []


@pytest.mark.parametrize("files_submitted, expected", [(1, ["CViche run ONERUN started"]), (2, [])])
def test_the_orchestrator_gives_notify_run_started_the_batch_size(db, tmp_path, monkeypatch,
                                                                   files_submitted, expected):
    """The wire, not just the rule: the orchestrator reads the run's batch
    size and passes it on, so a one-file batch's run posts its started card
    and a bulk batch's does not (#1340)."""
    from app.models import Run, RunBatch, User
    from app.pipeline import orchestrator as orch
    user = User(email="pat@example.com", display_name="Pat Example", consent_version="1.0")
    db.add(user)
    db.commit()
    db.add(RunBatch(id="BATCHA", user_id=user.id, files_submitted=files_submitted))
    run = Run(id="ONERUN", filename="cv.docx", file_type="docx", status="running",
              user_id=user.id, batch_id="BATCHA")
    db.add(run)
    db.commit()
    posted = _capture_posts(monkeypatch)

    asyncio.run(orch.PipelineOrchestrator("ONERUN", tmp_path / "ONERUN.docx", db)._notify_started(run))
    notifications.flush()

    assert [_title(p)["text"] for p in posted] == expected


def test_notify_terminal_still_posts_for_a_run_in_a_batch(monkeypatch):
    posted = _capture_posts(monkeypatch)

    notifications.notify_run_terminal(_run(batch_id="BATCHA", status="complete"))
    notifications.flush()

    assert [_title(p)["text"] for p in posted] == ["CViche run A1B2C3 complete"]


def test_batch_submitted_payload_names_the_submitter_count_and_links_the_batch(monkeypatch):
    monkeypatch.setenv("CVICHE_ALLOWED_ORIGINS", "https://cviche.weill.cornell.edu")

    payload = notifications.build_batch_submitted_payload(
        notifications.BatchFacts(id="BATCHA", files_submitted=30), submitter="Pat Example",
    )

    assert _title(payload)["text"] == "CViche batch BATCHA submitted (30 files)"
    assert _facts(payload) == {"Batch ID": "BATCHA", "Files": "30", "Submitted by": "Pat Example"}
    assert _card(payload)["actions"] == [{
        "type": "Action.OpenUrl", "title": "Open batch",
        "url": "https://cviche.weill.cornell.edu/runs?batch=BATCHA",
    }]
    assert payload["summary"] == "CViche batch BATCHA submitted (30 files)"


def test_batch_submitted_payload_has_no_button_without_an_app_origin(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    payload = notifications.build_batch_submitted_payload(notifications.BatchFacts(id="BATCHA", files_submitted=2))
    assert "actions" not in _card(payload)
    assert "Submitted by" not in _facts(payload)


def test_notify_batch_submitted_posts_one_card(monkeypatch):
    posted = _capture_posts(monkeypatch)

    notifications.notify_batch_submitted(notifications.BatchFacts(id="BATCHA", files_submitted=3), "Pat Example")
    notifications.flush()

    assert [_title(p)["text"] for p in posted] == ["CViche batch BATCHA submitted (3 files)"]


def test_notify_batch_submitted_posts_nothing_for_a_one_file_batch(monkeypatch):
    """#1340: a one-file batch is a single upload; its run posts the started card instead."""
    posted = _capture_posts(monkeypatch)

    notifications.notify_batch_submitted(notifications.BatchFacts(id="BATCHA", files_submitted=1), "Pat Example")
    notifications.flush()

    assert posted == []


def test_notify_batch_submitted_is_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv("CVICHE_TEAMS_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(notifications, "_enqueue", lambda *a: pytest.fail("must not enqueue when unconfigured"))
    assert notifications.notify_batch_submitted(notifications.BatchFacts(id="BATCHA", files_submitted=3)) is None


def test_notify_batch_submitted_swallows_a_builder_exception(monkeypatch, caplog):
    def _boom(*a, **k):
        raise RuntimeError("bad batch")
    monkeypatch.setattr(notifications, "build_batch_submitted_payload", _boom)

    with caplog.at_level(logging.ERROR):
        result = notifications.notify_batch_submitted(notifications.BatchFacts(id="BATCHA", files_submitted=3))

    assert result is None
    assert any("batch BATCHA" in r.getMessage() for r in caplog.records)


# --- requests.Timeout is an explicit, tested failure mode (#782 D8 point 2) --

def test_notify_terminal_retries_and_swallows_timeout(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    calls = {"n": 0}

    def _timeout(*a, **k):
        calls["n"] += 1
        raise notifications.requests.Timeout("timed out")

    monkeypatch.setattr(notifications._SESSION, "post", _timeout)

    with caplog.at_level(logging.WARNING):
        result = notifications.notify_run_terminal(_run(), None)
        notifications.flush()

    assert result is None
    # requests.Timeout is a RequestException -- retried up to the bound, not
    # given up on after one attempt. Proves M5 (drop the retry loop): without
    # a retry loop this would be 1, not _RETRY_ATTEMPTS.
    assert calls["n"] == notifications._RETRY_ATTEMPTS
    warnings = [
        r for r in caplog.records
        if r.name == "app.services.notifications" and r.levelname == "WARNING"
    ]
    assert len(warnings) == 1
    assert f"failed after {notifications._RETRY_ATTEMPTS} attempts" in warnings[0].message


# --- build_feedback_payload -------------------------------------------------

def test_feedback_payload_maps_all_fields(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    feedback_facts = _feedback_facts(overall_accuracy=8)

    payload = notifications.build_feedback_payload(feedback_facts, _run_facts(), submitter="Jane Doe")

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

    facts = _facts(notifications.build_feedback_payload(_feedback_facts(), _run_facts()))

    assert "Submitted by" not in facts
    assert "Overall accuracy" not in facts


def test_feedback_payload_never_includes_free_text_fields(monkeypatch):
    """biggest_issue/issue_locations are reviewer-typed free text that can name
    a person or quote CV content -- FeedbackFacts doesn't even carry them, so
    they cannot reach the card."""
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    feedback = _feedback(
        biggest_issue="Jane Smith's grant dates were wrong",
        issue_locations=["section M"],
    )
    feedback_facts = notifications.FeedbackFacts.from_feedback(feedback)

    payload = notifications.build_feedback_payload(feedback_facts, _run_facts())

    assert "Biggest issue" not in _facts(payload)
    assert "Jane Smith" not in str(payload)


@pytest.mark.parametrize("recommend,color", [
    (1, "attention"), (2, "attention"), (3, "default"), (4, "good"), (5, "good"),
])
def test_feedback_payload_color_by_recommend_score(monkeypatch, recommend, color):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    payload = notifications.build_feedback_payload(
        _feedback_facts(likelihood_to_recommend=recommend), _run_facts())

    assert _title(payload)["color"] == color


def test_feedback_payload_has_fallback_and_summary(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)

    payload = notifications.build_feedback_payload(_feedback_facts(), _run_facts())

    assert payload["summary"].startswith("CViche feedback on run A1B2C3")
    assert _card(payload)["fallbackText"] == payload["summary"]


def test_feedback_payload_action_uses_first_allowed_origin(monkeypatch):
    monkeypatch.setenv("CVICHE_ALLOWED_ORIGINS", "https://cviche.weill.cornell.edu/")

    payload = notifications.build_feedback_payload(_feedback_facts(), _run_facts())

    assert _card(payload)["actions"][0]["url"] == (
        "https://cviche.weill.cornell.edu/run/A1B2C3"
    )


# --- notify_feedback_submitted ----------------------------------------------

def test_notify_feedback_is_noop_when_unconfigured(monkeypatch):
    monkeypatch.delenv("CVICHE_TEAMS_WEBHOOK_URL", raising=False)

    def _fail_post(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("_SESSION.post should not be called when unconfigured")

    monkeypatch.setattr(notifications._SESSION, "post", _fail_post)

    assert notifications.notify_feedback_submitted(_feedback(), _run()) is None
    notifications.flush()


def test_notify_feedback_posts_when_configured(monkeypatch):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    captured = {}

    def _ok_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(notifications._SESSION, "post", _ok_post)

    notifications.notify_feedback_submitted(_feedback(), _run(), submitter="Jane Doe")
    notifications.flush()

    assert captured["url"] == "https://webhook.example/teams"
    assert _facts(captured["json"])["Submitted by"] == "Jane Doe"


def test_notify_feedback_swallows_post_exception(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")

    def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(notifications._SESSION, "post", _boom)

    with caplog.at_level(logging.WARNING):
        result = notifications.notify_feedback_submitted(_feedback(), _run())
        notifications.flush()

    assert result is None
    assert any("unexpected error" in r.message for r in caplog.records)


def test_notify_feedback_swallows_builder_exception(monkeypatch, caplog):
    """#782 D7: build_feedback_payload raising is caught by notify_feedback_
    submitted's own never-raise boundary."""
    def _boom(*a, **k):
        raise RuntimeError("bad feedback object")

    monkeypatch.setattr(notifications, "build_feedback_payload", _boom)

    with caplog.at_level(logging.ERROR):
        result = notifications.notify_feedback_submitted(_feedback(), _run())

    assert result is None
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "payload build failed" in errors[0].message


def test_notify_terminal_swallows_builder_exception(monkeypatch, caplog):
    """#782 D7: build_teams_payload raising is caught by notify_run_terminal's
    own never-raise boundary, not just _deliver's (a payload-build exception
    used to escape notify_* entirely -- it ran outside the try)."""
    def _boom(*a, **k):
        raise RuntimeError("bad run object")

    monkeypatch.setattr(notifications, "build_teams_payload", _boom)

    with caplog.at_level(logging.ERROR):
        result = notifications.notify_run_terminal(_run(), None)

    assert result is None
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "payload build failed" in errors[0].message


# --- #309: webhook URL validation -------------------------------------------

def test_webhook_rejects_non_https_scheme(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "http://webhook.example/teams")
    mock_post = MagicMock()
    monkeypatch.setattr(notifications._SESSION, "post", mock_post)

    with caplog.at_level(logging.WARNING):
        result = notifications.notify_run_terminal(_run(), None)
        notifications.flush()

    assert result is None
    mock_post.assert_not_called()
    assert any("not a valid https URL" in r.message for r in caplog.records)


def test_webhook_rejects_malformed_url(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "not-a-url-at-all")
    mock_post = MagicMock()
    monkeypatch.setattr(notifications._SESSION, "post", mock_post)

    with caplog.at_level(logging.WARNING):
        result = notifications.notify_run_terminal(_run(), None)
        notifications.flush()

    assert result is None
    mock_post.assert_not_called()
    assert any("not a valid https URL" in r.message for r in caplog.records)


def test_webhook_invalid_url_warns_once_not_per_call(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "ftp://webhook.example/teams")
    mock_post = MagicMock()
    monkeypatch.setattr(notifications._SESSION, "post", mock_post)

    with caplog.at_level(logging.WARNING):
        notifications.notify_run_terminal(_run(), None)
        notifications.notify_run_terminal(_run(), None)
        notifications.notify_run_started(_run())
        notifications.flush()

    mock_post.assert_not_called()
    warnings = [r for r in caplog.records if "not a valid https URL" in r.message]
    assert len(warnings) == 1


def test_webhook_cache_revalidates_on_each_distinct_value(monkeypatch):
    """#782 D8 point 6: _validate_webhook_url is memoized (maxsize=1), but a
    changed config value must still be re-validated on its own -- an
    invalid -> valid -> invalid sequence must post only for the valid one,
    proving the cache doesn't mask a config change either direction."""
    captured = []

    def _ok_post(url, json=None, timeout=None):
        captured.append(url)
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(notifications._SESSION, "post", _ok_post)

    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "not-a-url-1")
    notifications.notify_run_terminal(_run(), None)
    notifications.flush()

    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    notifications.notify_run_terminal(_run(), None)
    notifications.flush()

    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "not-a-url-2")
    notifications.notify_run_terminal(_run(), None)
    notifications.flush()

    assert captured == ["https://webhook.example/teams"]


# --- #309: run id is URL-encoded in the action button -----------------------

def test_action_button_encodes_run_id(monkeypatch):
    monkeypatch.setenv("CVICHE_ALLOWED_ORIGINS", "https://cviche.weill.cornell.edu")

    payload = notifications.build_teams_payload(_run_facts(id="a/b?c"), None)

    action = _card(payload)["actions"][0]
    assert action["url"] == "https://cviche.weill.cornell.edu/run/a%2Fb%3Fc"


def test_action_button_uses_untruncated_run_id_for_link(monkeypatch):
    """#782 D8 point 4: the production code truncated the run id (via
    _card_text) before handing it to _action_buttons, so the OpenUrl button
    pointed at a truncated, nonexistent run id for any id over
    _FACT_MAX_CHARS. Must fail against the pre-fix implementation (mutant
    M4: pass the _card_text-truncated id to _adaptive_card instead of the
    raw one)."""
    monkeypatch.setenv("CVICHE_ALLOWED_ORIGINS", "https://cviche.weill.cornell.edu")
    long_id = "R" * 300

    payload = notifications.build_teams_payload(_run_facts(id=long_id), None)

    action = _card(payload)["actions"][0]
    assert action["url"] == f"https://cviche.weill.cornell.edu/run/{quote(long_id, safe='')}"
    # The FactSet's displayed Run ID is still the sanitised/truncated one.
    assert len(_facts(payload)["Run ID"]) <= notifications._FACT_MAX_CHARS


# --- #309: _do_post/_deliver exception narrowing ----------------------------

def test_notify_swallows_connection_error_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")

    def _boom(*args, **kwargs):
        raise notifications.requests.ConnectionError("network down")

    monkeypatch.setattr(notifications._SESSION, "post", _boom)

    with caplog.at_level(logging.WARNING):
        result = notifications.notify_run_terminal(_run(), None)
        notifications.flush()

    assert result is None
    records = [r for r in caplog.records if "failed after" in r.message]
    assert len(records) == 1
    assert records[0].levelname == "WARNING"
    assert records[0].exc_info is None


def test_notify_swallows_type_error_with_exception_log(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")

    def _boom(*args, **kwargs):
        raise TypeError("payload is not JSON-serialisable")

    monkeypatch.setattr(notifications._SESSION, "post", _boom)

    with caplog.at_level(logging.WARNING):
        result = notifications.notify_run_terminal(_run(), None)
        notifications.flush()

    assert result is None
    records = [r for r in caplog.records if "unexpected error" in r.message]
    assert len(records) == 1
    assert records[0].levelname == "ERROR"
    assert records[0].exc_info is not None


# --- #782 D6: the webhook URL/query string must never reach the logs -------

def test_connection_error_never_leaks_webhook_path_or_query(monkeypatch, caplog):
    """str(requests.ConnectionError) can embed the full request URL --
    including the webhook's secret path and query string. Must fail against
    the pre-fix implementation, which %s-formatted the exception directly
    (mutant M2)."""
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    secret_path = "/webhookb2/SECRET-PATH-xyz"
    secret_query = "sig=abcTOPSECRET"

    def _boom(*args, **kwargs):
        raise notifications.requests.ConnectionError(
            "HTTPSConnectionPool(host='example.invalid', port=443): Max "
            f"retries exceeded with url: {secret_path}?{secret_query} "
            "(Caused by NewConnectionError(...))"
        )

    monkeypatch.setattr(notifications._SESSION, "post", _boom)

    with caplog.at_level(logging.DEBUG):
        notifications.notify_run_terminal(_run(), None)
        notifications.flush()

    assert secret_path not in caplog.text
    assert secret_query not in caplog.text


# --- card text sanitising ---------------------------------------------------

def test_card_text_strips_control_chars_and_truncates():
    value = "cv\x07report" + ("x" * 500)

    result = notifications._card_text(value, notifications._FACT_MAX_CHARS)

    assert "\x07" not in result
    assert len(result) <= notifications._FACT_MAX_CHARS


def test_filename_control_chars_stripped_from_terminal_card(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    facts = _run_facts(id="run\x07id" + ("a" * 500), filename="cv\x07report" + ("x" * 500))

    payload_facts = _facts(notifications.build_teams_payload(facts, None))

    assert "\x07" not in payload_facts["File"]
    assert len(payload_facts["File"]) <= notifications._FACT_MAX_CHARS
    assert "\x07" not in payload_facts["Run ID"]
    assert len(payload_facts["Run ID"]) <= notifications._FACT_MAX_CHARS


def test_submitter_control_chars_stripped_from_started_card(monkeypatch):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    facts = _run_facts(id="run\x07id" + ("a" * 500), filename="cv\x07" + ("x" * 500))

    payload = notifications.build_started_payload(facts, submitter="Jane\x07Doe" + ("z" * 500))

    payload_facts = _facts(payload)
    submitted_by = payload_facts["Submitted by"]
    assert "\x07" not in submitted_by
    assert len(submitted_by) <= notifications._FACT_MAX_CHARS
    assert "\x07" not in payload_facts["File"]
    assert len(payload_facts["File"]) <= notifications._FACT_MAX_CHARS
    assert "\x07" not in payload_facts["Run ID"]
    assert len(payload_facts["Run ID"]) <= notifications._FACT_MAX_CHARS


def test_feedback_card_control_chars_stripped_from_run_id_filename_and_submitter(
    monkeypatch,
):
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    run_facts = _run_facts(id="run\x07id" + ("a" * 500), filename="cv\x07report" + ("x" * 500))

    payload = notifications.build_feedback_payload(
        _feedback_facts(), run_facts, submitter="Jane\x07Doe" + ("z" * 500)
    )

    payload_facts = _facts(payload)
    for value in (payload_facts["Run ID"], payload_facts["File"], payload_facts["Submitted by"]):
        assert "\x07" not in value
        assert len(value) <= notifications._FACT_MAX_CHARS


def test_feedback_card_sanitises_reviewer_role(monkeypatch):
    """reviewer_role is free text when the survey's "other" option is picked.

    FeedbackForm.tsx submits `formData.reviewer_role_other.trim()` as
    reviewer_role for "other", and schemas.FeedbackSubmit types it as a bare
    `str`, so it is user-typed and must be sanitised like filename/submitter.
    """
    monkeypatch.delenv("CVICHE_ALLOWED_ORIGINS", raising=False)
    feedback_facts = _feedback_facts(reviewer_role="dept\x07admin\x9f" + ("r" * 500))

    facts = _facts(notifications.build_feedback_payload(feedback_facts, _run_facts()))

    assert "\x07" not in facts["Reviewer role"]
    assert "\x9f" not in facts["Reviewer role"]
    assert len(facts["Reviewer role"]) <= notifications._FACT_MAX_CHARS
    assert facts["Reviewer role"].startswith("deptadmin")


# --- #782 D4: startup validation / health check -----------------------------

def test_validate_configuration_reports_unconfigured(monkeypatch):
    monkeypatch.delenv("CVICHE_TEAMS_WEBHOOK_URL", raising=False)

    assert notifications.validate_configuration() == {"configured": False, "valid": False}


def test_validate_configuration_reports_valid(monkeypatch):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")

    assert notifications.validate_configuration() == {"configured": True, "valid": True}


def test_validate_configuration_reports_configured_but_invalid(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "not-a-url")

    with caplog.at_level(logging.WARNING):
        result = notifications.validate_configuration()

    assert result == {"configured": True, "valid": False}
    assert any("not a valid https URL" in r.message for r in caplog.records)


# --- #782 D5: terminal-status guard -----------------------------------------

def test_notify_run_terminal_skips_non_terminal_status(monkeypatch, caplog):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    mock_post = MagicMock()
    monkeypatch.setattr(notifications._SESSION, "post", mock_post)

    with caplog.at_level(logging.WARNING):
        result = notifications.notify_run_terminal(_run(status="running"), None)
        notifications.flush()

    assert result is None
    mock_post.assert_not_called()
    assert any("non-terminal status" in r.message for r in caplog.records)


@pytest.mark.parametrize("status", sorted(notifications.Run.TERMINAL_RUN_STATUSES))
def test_notify_run_terminal_posts_for_each_terminal_status(monkeypatch, status):
    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    captured = {"called": False}

    def _ok_post(url, json=None, timeout=None):
        captured["called"] = True
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(notifications._SESSION, "post", _ok_post)

    notifications.notify_run_terminal(_run(status=status), None)
    notifications.flush()

    assert captured["called"] is True


# --- D1's core property: notify_* returns before the POST completes -------

def test_notify_returns_before_delivery_completes(monkeypatch):
    """The whole point of D1 (#782) is that notify_* hands delivery to the
    background worker and returns immediately, without waiting for the POST.
    Nothing above pins that directly -- a mutant that replaces
    `_DELIVERY.submit(_deliver, ...)` with a synchronous `_deliver(...)` call
    leaves the rest of the suite green. This test fails under that mutant:
    with a POST blocked for up to 2s, notify_run_terminal must still return
    in well under that."""
    release = threading.Event()
    post_completed = {"value": False}
    calls = {"n": 0}

    def _blocking_post(url, json=None, timeout=None):
        calls["n"] += 1
        release.wait(timeout=2.0)
        post_completed["value"] = True
        return SimpleNamespace(status_code=200)

    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    monkeypatch.setattr(notifications._SESSION, "post", _blocking_post)

    start = time.perf_counter()
    notifications.notify_run_terminal(_run(), None)
    elapsed = time.perf_counter() - start

    assert elapsed < 0.5
    assert post_completed["value"] is False  # the POST has not completed yet

    release.set()
    notifications.flush()

    assert post_completed["value"] is True
    assert calls["n"] == 1


# --- flush() has no Future list of its own -- proves the FIFO-queue design -

def test_flush_waits_for_delivery_queued_before_it(monkeypatch):
    """flush() keeps no list of pending Futures (removed as module-level
    mutable state, #782 follow-up) -- it relies on _DELIVERY's single worker
    processing its queue FIFO. A slow delivery queued first must have
    finished by the time flush() returns, even though nothing tracks it."""
    release = threading.Event()
    done = {"value": False}

    def _slow_post(url, json=None, timeout=None):
        release.wait(timeout=5)
        done["value"] = True
        return SimpleNamespace(status_code=200)

    monkeypatch.setenv("CVICHE_TEAMS_WEBHOOK_URL", "https://webhook.example/teams")
    monkeypatch.setattr(notifications._SESSION, "post", _slow_post)

    notifications.notify_run_terminal(_run(), None)  # queues the slow delivery

    # Release the slow post from a background thread shortly after flush()
    # starts -- the single worker is still busy inside _slow_post, so
    # flush()'s own no-op sentinel task can't even start (let alone
    # complete) until release fires. If flush() returned without actually
    # waiting on the FIFO queue, `done["value"]` would still be False here.
    threading.Timer(0.05, release.set).start()

    notifications.flush()

    assert done["value"] is True


# --- run_id access stays inside the never-raise boundary (#782 follow-up) --

class _RaisingIdRun:
    """A run-like object whose `id` access raises -- simulates a SQLAlchemy
    DetachedInstanceError on an expired attribute (r3968255070 point 5's
    scenario: an ORM property that raises). getattr(obj, "id", default)
    only swallows AttributeError, so this must propagate through any bare
    getattr("id") call made outside a try/except."""

    filename = "cv.docx"
    status = "complete"
    total_cost = 0.1
    total_duration_seconds = 1

    @property
    def id(self):
        raise RuntimeError("detached instance")


def test_notify_run_terminal_swallows_raising_id_property(caplog):
    with caplog.at_level(logging.ERROR):
        result = notifications.notify_run_terminal(_RaisingIdRun(), None)

    assert result is None
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "payload build failed" in errors[0].message


def test_notify_run_started_swallows_raising_id_property(caplog):
    with caplog.at_level(logging.ERROR):
        result = notifications.notify_run_started(_RaisingIdRun())

    assert result is None
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "payload build failed" in errors[0].message


def test_notify_feedback_submitted_swallows_raising_id_property(caplog):
    with caplog.at_level(logging.ERROR):
        result = notifications.notify_feedback_submitted(_feedback(), _RaisingIdRun())

    assert result is None
    errors = [r for r in caplog.records if r.levelname == "ERROR"]
    assert len(errors) == 1
    assert "payload build failed" in errors[0].message


# --- notify_* wrappers never raise (already pinned; named for #309/#782) ---
# test_notify_swallows_unexpected_post_exception_with_exception_log,
# test_notify_feedback_swallows_post_exception,
# test_notify_swallows_connection_error_with_warning,
# test_notify_swallows_type_error_with_exception_log, and the three
# test_notify_*_swallows_builder_exception tests (above) all assert
# notify_run_terminal/notify_run_started/notify_feedback_submitted return
# None despite the payload build or the underlying POST raising.
# test_feedback_notification.py's
# test_submit_feedback_succeeds_even_if_notification_raises additionally pins
# this at the route call-site.


@pytest.mark.parametrize(
    ("status_code", "success", "retryable"),
    [(200, True, False), (404, False, False), (429, False, True), (503, False, True)],
)
def test_do_post_uses_an_injected_session_and_classifies_the_status(status_code, success, retryable):
    # #310: a test hands _do_post its own session instead of monkeypatching
    # the module-global _SESSION.
    session = MagicMock()
    session.post.return_value = SimpleNamespace(status_code=status_code)

    attempt = notifications._do_post("https://example.invalid/hook", {"k": "v"}, session=session)

    session.post.assert_called_once_with(
        "https://example.invalid/hook", json={"k": "v"}, timeout=notifications._POST_TIMEOUT,
    )
    assert (attempt.success, attempt.retryable, attempt.detail) == (success, retryable, f"HTTP {status_code}")


def test_every_card_carries_the_named_adaptive_card_version():
    card = notifications.build_teams_payload(_run_facts())
    assert card["attachments"][0]["content"]["version"] == notifications.ADAPTIVE_CARD_VERSION == "1.5"
