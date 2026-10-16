"""Tests for prompt_logger's run_id scoping (#580, review on #586).

set_current_run_id() validates run_id against the same charset
generate_run_id() produces, since run_id can arrive from a request path and
_log_dir() otherwise builds PROMPT_LOG_DIR / run_id directly from it. It also
returns the ContextVar token so a caller can restore the previous value,
which matters for worker-thread/task reuse.
"""
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[2]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from unified_pipeline.core.prompt_logger import (
    _current_run_id,
    reset_current_run_id,
    set_current_run_id,
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


def test_response_log_records_the_fallback_model_the_doctor_reads(tmp_path, monkeypatch):
    """#1174: the writer and the doctor's reader agree on the record, so a
    fallback-served call in any stage reaches `llm_fallback_served`."""
    from unified_pipeline.core import prompt_logger
    from unified_pipeline.quality_score import prompt_log_fallback_served

    monkeypatch.setattr(prompt_logger, "PROMPT_LOG_DIR", tmp_path)
    prompt_logger.log_prompt_response("id1", {"model": "fb", "served_by_fallback_model": "fb"}, "stage_6")
    prompt_logger.log_prompt_response("id2", {"model": "primary"}, "stage_6")

    assert [c.describe() for c in prompt_log_fallback_served(tmp_path)] == ["stage 6 calls on fb (1 calls)"]



# --- #1911 review: collisions, modes, failure isolation, import side effects ---

def _log_twice(prompt_logger, messages):
    return [prompt_logger.log_prompt_before_call(messages=messages, model="m", purpose="stage_x")
            for _ in range(2)]


def test_identical_prompts_in_the_same_second_get_separate_records(tmp_path, monkeypatch):
    """The log id was a hash of the messages and the filename had one-second
    precision, so a repeated prompt overwrote the first call's record."""
    from unified_pipeline.core import prompt_logger

    monkeypatch.setattr(prompt_logger, "PROMPT_LOG_DIR", tmp_path)
    frozen = prompt_logger.datetime(2026, 1, 1, 0, 0, 0)
    monkeypatch.setattr(prompt_logger, "datetime", type("D", (), {"now": staticmethod(lambda: frozen)}))
    ids = _log_twice(prompt_logger, [{"role": "user", "content": "same"}])
    for log_id in ids:
        prompt_logger.log_prompt_response(log_id, {"content": "r"}, "stage_x")

    assert ids[0] != ids[1]
    assert all(re.fullmatch(r"[0-9a-f]{12}", i) for i in ids)
    records = [json.loads(p.read_text()) for p in sorted(tmp_path.glob("*_stage_x_*[0-9a-f].json"))]
    assert sorted(r["log_id"] for r in records) == sorted(ids)
    assert records[0]["prompt_hash"] == records[1]["prompt_hash"]
    assert len(list(tmp_path.glob("*_RESPONSE.json"))) == 2
    assert len(list(tmp_path.glob("*_READABLE.txt"))) == 2


def test_log_files_are_owner_only(tmp_path, monkeypatch):
    from unified_pipeline.core import prompt_logger

    monkeypatch.setattr(prompt_logger, "PROMPT_LOG_DIR", tmp_path)
    old = os.umask(0)  # the most permissive umask: modes must not depend on it
    try:
        token = set_current_run_id("MODE01")
        try:
            log_id = prompt_logger.log_prompt_before_call(
                messages=[{"role": "user", "content": "cv text"}], model="m", purpose="stage_x")
            prompt_logger.log_prompt_response(log_id, {"content": "r"}, "stage_x")
        finally:
            reset_current_run_id(token)
    finally:
        os.umask(old)

    run_dir = tmp_path / "MODE01"
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    files = list(run_dir.iterdir())
    assert len(files) == 3
    assert {stat.S_IMODE(f.stat().st_mode) for f in files} == {0o600}


def test_a_log_file_is_never_overwritten(tmp_path):
    from unified_pipeline.core import prompt_logger

    path = tmp_path / "x.json"
    path.write_text("first")
    try:
        prompt_logger._open_private(path)
    except FileExistsError:
        assert path.read_text() == "first"
    else:
        raise AssertionError("an existing log file was reopened for writing")

def test_a_log_write_failure_still_returns_an_id(tmp_path, monkeypatch, capsys):
    from unified_pipeline.core import prompt_logger

    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("")
    monkeypatch.setattr(prompt_logger, "PROMPT_LOG_DIR", not_a_dir / "logs")
    log_id = prompt_logger.log_prompt_before_call(
        messages=[{"role": "user", "content": "x"}], model="m", purpose="stage_x")
    assert re.fullmatch(r"[0-9a-f]{12}", log_id)
    assert "Failed to log prompt" in capsys.readouterr().out


def test_structured_content_and_extra_parameters_are_logged(tmp_path, monkeypatch):
    """Content may be a list of blocks, and a context or parameter value may not
    be JSON-native; neither may cost the record."""
    from unified_pipeline.core import prompt_logger

    monkeypatch.setattr(prompt_logger, "PROMPT_LOG_DIR", tmp_path)
    blocks = [{"text": "part one"}, {"cachePoint": {"type": "default"}}, {"image": Path("/img")}]
    prompt_logger.log_prompt_before_call(
        messages=[{"role": "system", "content": blocks}, {"role": "user", "content": "u"}],
        model="m", purpose="stage_x", context={"path": Path("/x")},
        extra_parameters={"enable_prompt_caching": True, "retry_count": 3})

    record = json.loads(next(tmp_path.glob("*[0-9a-f].json")).read_text())
    assert record["api_parameters"]["enable_prompt_caching"] is True
    assert record["api_parameters"]["retry_count"] == 3
    assert record["context"] == {"path": "/x"}
    assert record["messages"][0]["content"][2] == {"image": "/img"}
    readable = next(tmp_path.glob("*_READABLE.txt")).read_text()
    assert '"text": "part one"' in readable
    assert '"retry_count": 3' in readable


def test_import_creates_no_directory(tmp_path):
    """An unwritable PROMPT_LOG_DIR must not fail the import of every module
    that reaches llm_client; the directory is made on the first write."""
    target = tmp_path / "never" / "made"
    env = {**os.environ, "PROMPT_LOG_DIR": str(target), "PYTHONPATH": str(_SRC)}
    subprocess.run([sys.executable, "-c", "import unified_pipeline.core.prompt_logger"],
                   env=env, check=True)
    assert not target.exists()


if __name__ == "__main__":
    test_valid_run_id_is_accepted_and_scopes_log_dir()
    test_path_traversal_run_id_is_rejected()
    test_reset_restores_prior_value_for_nested_scopes()
    print("ok")
