"""Tests for unified_pipeline.log_redaction (#639): the label a WARNING-or-
above log line uses for an exception in place of its raw text."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from unified_pipeline.log_redaction import redacted_error_label  # noqa: E402

_CV_TEXT_SENTINEL = "Jane Q. Example, 12 Synthetic Lane"


def test_label_is_the_class_name_and_never_the_exception_text() -> None:
    assert redacted_error_label(ValueError(_CV_TEXT_SENTINEL)) == "ValueError"


def test_label_brackets_the_caller_supplied_safe_detail() -> None:
    label = redacted_error_label(KeyError(_CV_TEXT_SENTINEL), "line 3, column 5")
    assert label == "KeyError[line 3, column 5]"
