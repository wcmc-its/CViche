"""Tests for prompt_logger's run_id scoping (#580, review on #586).

set_current_run_id() validates run_id against the same charset
generate_run_id() produces, since run_id can arrive from a request path and
_log_dir() otherwise builds PROMPT_LOG_DIR / run_id directly from it. It also
returns the ContextVar token so a caller can restore the previous value,
which matters for worker-thread/task reuse.
"""
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.prompt_logger import (
    set_current_run_id,
    reset_current_run_id,
    _current_run_id,
)


def test_valid_run_id_is_accepted_and_scopes_log_dir():
    token = set_current_run_id("A1B2C3")
    try:
        assert _current_run_id.get() == "A1B2C3"
    finally:
        reset_current_run_id(token)
    assert _current_run_id.get() is None


def test_path_traversal_run_id_is_rejected():
    for bad in ("../../etc", "a/b", "a\\b", ""):
        try:
            set_current_run_id(bad)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for run_id={bad!r}")


def test_reset_restores_prior_value_for_nested_scopes():
    outer_token = set_current_run_id("OUTER1")
    try:
        inner_token = set_current_run_id("INNER1")
        try:
            assert _current_run_id.get() == "INNER1"
        finally:
            reset_current_run_id(inner_token)
        assert _current_run_id.get() == "OUTER1"
    finally:
        reset_current_run_id(outer_token)
    assert _current_run_id.get() is None


if __name__ == "__main__":
    test_valid_run_id_is_accepted_and_scopes_log_dir()
    test_path_traversal_run_id_is_rejected()
    test_reset_restores_prior_value_for_nested_scopes()
    print("ok")
