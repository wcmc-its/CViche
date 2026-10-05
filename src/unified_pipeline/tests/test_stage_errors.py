"""Tests for unified_pipeline.stage_errors (#745): the structured stage-error
record both pipeline drivers write and quality_score reads."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from unified_pipeline.stage_errors import (  # noqa: E402
    STAGE_ERRORS_DIRNAME,
    STAGE_ERRORS_SUFFIX,
    StageError,
    read_stage_errors,
    record_stage_outcome,
    stage_errors_path,
)


def test_path_sits_beside_the_stage_dirs(tmp_path):
    assert stage_errors_path(tmp_path, "ABC") == (
        tmp_path / STAGE_ERRORS_DIRNAME / f"ABC{STAGE_ERRORS_SUFFIX}")


def test_from_exception_names_the_type_and_is_fatal():
    err = StageError.from_exception("4", TypeError("'int' object is not iterable"))
    assert err == StageError("4", "TypeError", "'int' object is not iterable", fatal=True)


def test_from_exception_records_a_non_fatal_failure_when_the_driver_carries_on():
    """The web driver's stage-4.5 record (#1174): same fields, fatal=False."""
    err = StageError.from_exception("4.5", RuntimeError("boom"), fatal=False)
    assert err == StageError(stage="4.5", exception_type="RuntimeError", message="boom", fatal=False)


def test_success_with_no_record_writes_nothing(tmp_path):
    path = stage_errors_path(tmp_path, "ABC")
    assert record_stage_outcome(path, "4", None) is False
    assert not path.exists()


def test_a_new_failure_replaces_the_same_stage_and_keeps_others(tmp_path):
    path = stage_errors_path(tmp_path, "ABC")
    record_stage_outcome(path, "2", StageError("2", "KeyError", "a", fatal=True))
    record_stage_outcome(path, "4", StageError("4", "ValueError", "b", fatal=True))
    record_stage_outcome(path, "2", StageError("2", "OSError", "c", fatal=True))
    assert read_stage_errors(path) == [
        StageError("4", "ValueError", "b", fatal=True),
        StageError("2", "OSError", "c", fatal=True),
    ]


def test_success_removes_only_that_stage(tmp_path):
    path = stage_errors_path(tmp_path, "ABC")
    record_stage_outcome(path, "2", StageError("2", "KeyError", "a", fatal=True))
    record_stage_outcome(path, "4", StageError("4", "ValueError", "b", fatal=True))
    assert record_stage_outcome(path, "2", None) is True
    assert [e.stage for e in read_stage_errors(path)] == ["4"]


@pytest.mark.parametrize("payload", [
    {"stage": "4"},
    [{"stage": "4", "exception_type": "X", "message": "m"}],
    [{"stage": "4", "exception_type": "X", "message": "m", "fatal": "yes"}],
])
def test_malformed_record_raises_instead_of_reading_as_clean(tmp_path, payload):
    path = tmp_path / f"ABC{STAGE_ERRORS_SUFFIX}"
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        read_stage_errors(path)


def test_call_failure_reads_back_the_record_stage_4_5_writes():
    """#1174: the stage-4.5 artifact's llm_call_failures entry, as the doctor reads it."""
    from dataclasses import asdict

    from unified_pipeline.stage_errors import CallFailure

    for failure in (CallFailure("summary_generation", "ClientError", None, "denied"),
                    CallFailure("m1_relevance_score", "BedrockContentFilteredError",
                                "content_filtered", "filtered")):
        assert CallFailure.from_record(asdict(failure)) == failure
    assert CallFailure.from_record(None) is None
    assert CallFailure.from_record(["not", "a", "record"]) is None
