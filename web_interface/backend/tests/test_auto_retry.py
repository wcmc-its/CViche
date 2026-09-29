"""Unit tests for auto-retry classification and config (issue #145, foundation).

Covers the pure decision logic and config knobs in app.services.auto_retry:
  - error_type classification (retryable vs terminal vs unknown)
  - config defaults when env unset (feature OFF, max 3, backoff 30)
  - config parsing from env (enabled truthy, custom max)
  - eligible_for_resume across flag-off/on, attempt-cap, and error-type cases

These are pure functions (no DB, no launching); the reaper integration that
acts on the decision lands in a later stage.
"""
import os

from types import SimpleNamespace

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

from app.services import auto_retry


def _run(attempt_count=1):
    """Minimal stand-in for a Run row -- eligible_for_resume only reads
    attempt_count, so a SimpleNamespace suffices (matches existing test style)."""
    return SimpleNamespace(attempt_count=attempt_count)


# ---------------------------------------------------------------------------
# error_type classification
# ---------------------------------------------------------------------------

def test_retryable_error_types_classified_true():
    for error_type in ("api_error", "llm_timeout"):
        assert auto_retry.is_retryable_error_type(error_type) is True


def test_terminal_error_types_classified_false():
    for error_type in ("parse_error", "invalid_response", "token_limit", "file_error"):
        assert auto_retry.is_retryable_error_type(error_type) is False


def test_unknown_and_none_error_types_fail_safe_to_false():
    assert auto_retry.is_retryable_error_type("unknown") is False
    assert auto_retry.is_retryable_error_type(None) is False
    assert auto_retry.is_retryable_error_type("") is False
    assert auto_retry.is_retryable_error_type("something_new") is False


def test_retryable_and_terminal_sets_are_disjoint():
    assert auto_retry.RETRYABLE_ERROR_TYPES.isdisjoint(auto_retry.TERMINAL_ERROR_TYPES)


# ---------------------------------------------------------------------------
# config defaults when env unset
# ---------------------------------------------------------------------------

def test_config_defaults_when_env_unset(monkeypatch):
    monkeypatch.delenv("CVICHE_AUTO_RETRY_ENABLED", raising=False)
    monkeypatch.delenv("CVICHE_MAX_AUTO_RETRIES", raising=False)
    monkeypatch.delenv("CVICHE_AUTO_RETRY_BACKOFF_SECONDS", raising=False)

    assert auto_retry.auto_retry_enabled() is False
    assert auto_retry.max_auto_retries() == 2
    assert auto_retry.auto_retry_backoff_seconds() == 30


# ---------------------------------------------------------------------------
# config parsing from env
# ---------------------------------------------------------------------------

def test_enabled_parses_truthy_values(monkeypatch):
    for truthy in ("1", "true", "TRUE", "yes", "on"):
        monkeypatch.setenv("CVICHE_AUTO_RETRY_ENABLED", truthy)
        assert auto_retry.auto_retry_enabled() is True


def test_enabled_parses_falsy_and_garbage_values(monkeypatch):
    for falsy in ("0", "false", "no", "off", "garbage"):
        monkeypatch.setenv("CVICHE_AUTO_RETRY_ENABLED", falsy)
        assert auto_retry.auto_retry_enabled() is False


def test_max_auto_retries_custom_and_bad_value(monkeypatch):
    monkeypatch.setenv("CVICHE_MAX_AUTO_RETRIES", "5")
    assert auto_retry.max_auto_retries() == 5
    # Non-integer falls back to the default rather than raising.
    monkeypatch.setenv("CVICHE_MAX_AUTO_RETRIES", "garbage")
    assert auto_retry.max_auto_retries() == 2


def test_backoff_seconds_custom_and_bad_value(monkeypatch):
    monkeypatch.setenv("CVICHE_AUTO_RETRY_BACKOFF_SECONDS", "90")
    assert auto_retry.auto_retry_backoff_seconds() == 90
    monkeypatch.setenv("CVICHE_AUTO_RETRY_BACKOFF_SECONDS", "garbage")
    assert auto_retry.auto_retry_backoff_seconds() == 30


# ---------------------------------------------------------------------------
# eligible_for_resume
# ---------------------------------------------------------------------------

def test_eligible_false_when_flag_off(monkeypatch):
    monkeypatch.delenv("CVICHE_AUTO_RETRY_ENABLED", raising=False)
    # Even an interruption / retryable error is ineligible while the flag is off.
    assert auto_retry.eligible_for_resume(
        _run(attempt_count=1), last_error_type="api_error", is_interruption=True
    ) is False


def test_eligible_true_when_on_under_cap_and_interruption(monkeypatch):
    monkeypatch.setenv("CVICHE_AUTO_RETRY_ENABLED", "1")
    # Pod-crash interruption is infra-level, retryable regardless of error_type.
    assert auto_retry.eligible_for_resume(
        _run(attempt_count=1), last_error_type=None, is_interruption=True
    ) is True


def test_eligible_true_when_on_under_cap_and_retryable_error(monkeypatch):
    monkeypatch.setenv("CVICHE_AUTO_RETRY_ENABLED", "1")
    assert auto_retry.eligible_for_resume(
        _run(attempt_count=2), last_error_type="llm_timeout", is_interruption=False
    ) is True


def test_eligible_false_when_on_under_cap_but_terminal_error(monkeypatch):
    monkeypatch.setenv("CVICHE_AUTO_RETRY_ENABLED", "1")
    # Deterministic failure, not an interruption -> not eligible.
    assert auto_retry.eligible_for_resume(
        _run(attempt_count=1), last_error_type="parse_error", is_interruption=False
    ) is False


def test_eligible_false_when_attempts_over_cap(monkeypatch):
    monkeypatch.setenv("CVICHE_AUTO_RETRY_ENABLED", "1")
    monkeypatch.setenv("CVICHE_MAX_AUTO_RETRIES", "3")
    # 4 > cap of 3 -> ineligible even for an interruption.
    assert auto_retry.eligible_for_resume(
        _run(attempt_count=4), last_error_type="api_error", is_interruption=True
    ) is False


def test_default_cap_allows_three_executions_in_total(monkeypatch):
    # #145 decision: 3 executions total. The first run is attempt 1, so the
    # 1st and 2nd executions may be retried and the 3rd may not.
    monkeypatch.setenv("CVICHE_AUTO_RETRY_ENABLED", "1")
    monkeypatch.delenv("CVICHE_MAX_AUTO_RETRIES", raising=False)
    eligible = [
        auto_retry.eligible_for_resume(
            _run(attempt_count=n), last_error_type=None, is_interruption=True
        )
        for n in (1, 2, 3)
    ]
    assert eligible == [True, True, False]
