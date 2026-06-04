"""Unit tests for the open-redirect (CWE-601) safe-path validator.

Pure-function tests: no DB, HTTP, or conftest fixtures required, so these run
even when the DB/env plumbing is unavailable.
"""
import pytest

from app.redirect_safety import safe_relative_path


@pytest.mark.parametrize(
    "value",
    [
        "//evil.com",            # protocol-relative
        "///evil.com",           # triple-slash protocol-relative
        "https://evil.com",      # absolute https
        "http:/evil.com",        # single-slash scheme
        "javascript:alert(1)",   # javascript scheme
        "data:text/html,x",      # data scheme
        "/\\evil.com",           # backslash after slash (browser folds \ to /)
        "\\\\evil.com",          # double backslash
        "/\tevil",               # embedded control char
        "/ evil",                # embedded space
        "evil.com",              # no leading slash
        "../etc/passwd",         # relative traversal, no leading slash
        "",                      # empty
        None,                    # None
    ],
)
def test_rejects_unsafe_targets(value):
    """Unsafe / off-site targets are coerced to the safe default '/'."""
    assert safe_relative_path(value) == "/"


@pytest.mark.parametrize(
    "value",
    [
        "/",
        "/dashboard",
        "/runs/ABC123",
        "/runs/ABC123?tab=log&x=1",
        "/admin/stats",
    ],
)
def test_accepts_safe_relative_paths(value):
    """Legitimate single-leading-slash relative paths pass through unchanged."""
    assert safe_relative_path(value) == value


def test_custom_default_is_returned_on_rejection():
    assert safe_relative_path("https://evil.com", default="/home") == "/home"
