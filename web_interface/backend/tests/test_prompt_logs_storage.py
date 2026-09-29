"""Tests for the orchestrator's prompt-log -> storage replication.

The orchestrator writes per-LLM-call transcripts to a shared dir during a
run. ``_sync_prompt_logs_to_storage`` replicates the fresh files (mtime
newer than the step start) into per-run storage so they survive container
restarts in prod and remain readable from the API after the pod is
replaced.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.pipeline import orchestrator as orch_mod
from app.storage.local_storage import LocalRunStorage


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """A LocalRunStorage rooted at a tmp dir, registered as the singleton."""
    backend_dir = tmp_path / "uploads"
    backend_dir.mkdir()
    storage = LocalRunStorage(base_dir=str(backend_dir))

    # Replace the storage singleton so the orchestrator's get_storage()
    # returns our tmp-dir-backed instance for the duration of the test.
    monkeypatch.setattr(orch_mod, "get_storage", lambda: storage)
    return storage


@pytest.fixture
def prompt_logs_dir(tmp_path, monkeypatch):
    """A tmp prompt_logs dir wired into the orchestrator module.

    _sync_prompt_logs_to_storage reads from PROMPT_LOGS_DIR/<run_id> (#580),
    so this returns that per-run subdirectory directly -- existing tests that
    write into the returned path are unaffected.
    """
    root = tmp_path / "prompt_logs"
    d = root / "RUN001"
    d.mkdir(parents=True)
    monkeypatch.setattr(orch_mod, "PROMPT_LOGS_DIR", root)
    return d


@pytest.fixture
def fake_orchestrator():
    """A minimal stand-in for PipelineOrchestrator. _sync_prompt_logs_to_storage
    only uses self.run_id, so we don't need the full constructor."""
    o = MagicMock()
    o.run_id = "RUN001"
    o._sync_prompt_logs_to_storage = (
        orch_mod.PipelineOrchestrator._sync_prompt_logs_to_storage.__get__(o)
    )
    return o


def _write_log(dir_path: Path, name: str, content: str, mtime: float | None = None) -> Path:
    path = dir_path / name
    path.write_text(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


class TestSyncPromptLogsToStorage:
    def test_uploads_files_newer_than_since(
        self, fake_orchestrator, storage, prompt_logs_dir
    ):
        step_start = datetime.now()
        # Sleep briefly so files written next have mtime > step_start.
        time.sleep(0.05)
        _write_log(prompt_logs_dir, "2026-05-20_a_taxonomy_mapping_xx.txt", "alpha")
        _write_log(prompt_logs_dir, "2026-05-20_b_taxonomy_mapping_xx.txt", "beta")

        fake_orchestrator._sync_prompt_logs_to_storage(step_start, step_number=2)

        keys = storage.list_files("RUN001", prefix="prompt_logs/")
        assert sorted(keys) == [
            "prompt_logs/2026-05-20_a_taxonomy_mapping_xx.txt",
            "prompt_logs/2026-05-20_b_taxonomy_mapping_xx.txt",
        ]
        assert storage.get_file(
            "RUN001", "prompt_logs/2026-05-20_a_taxonomy_mapping_xx.txt"
        ) == b"alpha"

    def test_skips_files_older_than_since(
        self, fake_orchestrator, storage, prompt_logs_dir
    ):
        # Pre-existing file written 10 minutes before this step.
        old_mtime = (datetime.now() - timedelta(minutes=10)).timestamp()
        _write_log(prompt_logs_dir, "old.txt", "old content", mtime=old_mtime)
        step_start = datetime.now()
        time.sleep(0.05)
        _write_log(prompt_logs_dir, "new.txt", "new content")

        fake_orchestrator._sync_prompt_logs_to_storage(step_start, step_number=1)

        keys = storage.list_files("RUN001", prefix="prompt_logs/")
        # Only "new.txt" should land in storage.
        assert keys == ["prompt_logs/new.txt"]

    def test_ignores_files_in_a_different_runs_subdirectory(
        self, fake_orchestrator, storage, prompt_logs_dir
    ):
        """#580: prompt_logger scopes writes to PROMPT_LOGS_DIR/<run_id>. A
        file sitting in a sibling run's subdirectory, even one newer than
        `since`, must never be picked up by this run's sync -- the old flat
        directory + mtime-only filter could not tell the two apart, which is
        exactly how one run's verbatim CV text got uploaded under another
        run's id."""
        other_run_dir = prompt_logs_dir.parent / "RUN002"
        other_run_dir.mkdir()
        step_start = datetime.now()
        time.sleep(0.05)
        _write_log(other_run_dir, "other_run_secret.txt", "belongs to RUN002")
        _write_log(prompt_logs_dir, "own.txt", "belongs to RUN001")

        fake_orchestrator._sync_prompt_logs_to_storage(step_start, step_number=1)

        keys = storage.list_files("RUN001", prefix="prompt_logs/")
        assert keys == ["prompt_logs/own.txt"]

    def test_missing_prompt_logs_dir_is_noop(
        self, fake_orchestrator, storage, tmp_path, monkeypatch
    ):
        # Point PROMPT_LOGS_DIR at a path that doesn't exist.
        monkeypatch.setattr(orch_mod, "PROMPT_LOGS_DIR", tmp_path / "absent")

        # Should not raise.
        fake_orchestrator._sync_prompt_logs_to_storage(datetime.now(), step_number=1)

        keys = storage.list_files("RUN001", prefix="prompt_logs/")
        assert keys == []

    def test_none_since_uploads_everything(
        self, fake_orchestrator, storage, prompt_logs_dir
    ):
        # When since is None (e.g., the step never set started_at), upload
        # every file in the dir rather than failing.
        _write_log(prompt_logs_dir, "a.txt", "content")
        _write_log(prompt_logs_dir, "b.txt", "content")

        fake_orchestrator._sync_prompt_logs_to_storage(None, step_number=1)

        keys = storage.list_files("RUN001", prefix="prompt_logs/")
        assert sorted(keys) == ["prompt_logs/a.txt", "prompt_logs/b.txt"]

    def test_storage_put_failure_does_not_raise(
        self, fake_orchestrator, prompt_logs_dir, monkeypatch
    ):
        # If put_file blows up (network, permission, etc.) the orchestrator
        # logs and continues. The step has already succeeded by this point;
        # a missed upload must not flip its status.
        class BrokenStorage:
            def put_file(self, *args, **kwargs):
                raise RuntimeError("network down")

        monkeypatch.setattr(orch_mod, "get_storage", lambda: BrokenStorage())
        _write_log(prompt_logs_dir, "x.txt", "data")

        # Should not raise.
        fake_orchestrator._sync_prompt_logs_to_storage(None, step_number=1)

    def test_directories_inside_prompt_logs_are_ignored(
        self, fake_orchestrator, storage, prompt_logs_dir
    ):
        # The pipeline never writes subdirectories, but if something else
        # does (e.g., a developer ran a script that mkdir'd here), the sync
        # must not crash trying to read_bytes() on a directory.
        (prompt_logs_dir / "subdir").mkdir()
        _write_log(prompt_logs_dir, "real.txt", "data")

        fake_orchestrator._sync_prompt_logs_to_storage(None, step_number=1)

        keys = storage.list_files("RUN001", prefix="prompt_logs/")
        assert keys == ["prompt_logs/real.txt"]


# ---- #306: stage 1a hierarchy walkers must be iterative ----------------------------

def _hierarchy_fixture():
    return [
        {"level": "H1", "text": "A", "children": [
            {"level": "H2", "text": "A1", "children": [{"level": "H3", "text": "A1a"}]},
            {"level": "H2", "text": "A2", "children": []},
        ]},
        {"text": "no-level"},  # level defaults to H1 in the orchestrator's writer
        {"level": "H1", "text": "C", "children": [{"level": "H2", "children": []}]},
    ]


def _recursive_write_reference(f, nodes, depth=0):
    """The pre-#306 nested closure from _execute_stage_logic, verbatim."""
    for node in nodes:
        indent = "  " * depth
        level = node.get('level', 'H1')
        text = node.get('text', '')
        f.write(f"{indent}[{level}] {text}\n")
        if node.get('children'):
            _recursive_write_reference(f, node['children'], depth + 1)


def _deep_chain(depth):
    root = {"level": "H1", "text": "n0", "children": []}
    tip = root
    for i in range(1, depth):
        child = {"level": "H2", "text": f"n{i}", "children": []}
        tip["children"].append(child)
        tip = child
    return [root]


def test_write_hierarchy_is_byte_identical_to_recursive_original():
    import io
    h = _hierarchy_fixture()
    new, ref = io.StringIO(), io.StringIO()
    orch_mod._write_hierarchy(new, h)
    _recursive_write_reference(ref, h)
    assert new.getvalue() == ref.getvalue()
    assert "[H1] no-level\n" in new.getvalue()
    assert "  [H2] \n" in new.getvalue()


def test_write_hierarchy_honours_starting_depth():
    import io
    new, ref = io.StringIO(), io.StringIO()
    orch_mod._write_hierarchy(new, _hierarchy_fixture(), depth=2)
    _recursive_write_reference(ref, _hierarchy_fixture(), depth=2)
    assert new.getvalue() == ref.getvalue()
    assert new.getvalue().startswith("    [H1] A\n")


def test_write_hierarchy_deep_chain_does_not_recurse():
    import io
    buf = io.StringIO()
    orch_mod._write_hierarchy(buf, _deep_chain(5000))
    assert buf.getvalue().count("\n") == 5000


def test_count_headers_matches_recursive_reference_and_deep_chain():
    count = orch_mod.PipelineOrchestrator._count_headers
    assert count(None, _hierarchy_fixture()) == 7
    assert count(None, []) == 0
    assert count(None, _deep_chain(5000)) == 5000
