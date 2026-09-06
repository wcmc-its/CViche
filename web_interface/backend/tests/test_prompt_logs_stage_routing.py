"""Tests for the stage_id -> prompt-log routing in steps.py.

Stage 5b was mistakenly listed in STAGES_WITHOUT_PROMPT_LOGS (the short-circuit
table that returns a "non-LLM stage" message before walking the prompt_logs
dir). Closes #1.
"""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.steps import STAGE_TO_PURPOSES, STAGES_WITHOUT_PROMPT_LOGS


class TestStage5bRouting:
    def test_5b_is_not_in_without_prompt_logs(self):
        """The 'non-LLM stage' short-circuit must not swallow stage 5b -- it
        does call call_llm(stage='stage_5b') from institution enrichment."""
        assert '5b' not in STAGES_WITHOUT_PROMPT_LOGS

    def test_5b_has_purposes(self):
        """Stage 5b should map to LLM purposes so the file walker can find
        its prompt logs."""
        entry = STAGE_TO_PURPOSES.get('5b')
        assert entry is not None
        assert entry.get('exact'), "stage 5b must have at least one exact purpose"
        assert 'stage_5b' in entry['exact']


class TestStage6Routing:
    def test_6_is_not_in_without_prompt_logs(self):
        """Same short-circuit hid stage 6's prompt logs. stage_6_word_template.py
        calls call_llm(stage='stage_6') for geographic scope classification."""
        assert '6' not in STAGES_WITHOUT_PROMPT_LOGS

    def test_6_has_purposes(self):
        entry = STAGE_TO_PURPOSES.get('6')
        assert entry is not None
        assert 'stage_6' in entry.get('exact', [])


class TestStageTablesAreConsistent:
    """Invariant: a stage_id with LLM purposes should not also be flagged as
    non-LLM. If we add another LLM stage in the future and forget the same
    update, this test catches it."""

    def test_no_overlap_between_tables(self):
        overlap = []
        for stage_id, entry in STAGE_TO_PURPOSES.items():
            has_purposes = bool(entry.get('exact')) or bool(entry.get('prefix'))
            if has_purposes and stage_id in STAGES_WITHOUT_PROMPT_LOGS:
                overlap.append(stage_id)
        assert not overlap, (
            f"Stages have LLM purposes but are also flagged as non-LLM: {overlap}. "
            f"Either remove them from STAGES_WITHOUT_PROMPT_LOGS (they DO have "
            f"prompt logs) or clear their purposes."
        )

    def test_stages_without_logs_have_empty_purposes(self):
        """A stage flagged as non-LLM should have empty purposes if it
        appears in STAGE_TO_PURPOSES at all -- otherwise the file walker
        would be inconsistent with the short-circuit message."""
        offenders = []
        for stage_id in STAGES_WITHOUT_PROMPT_LOGS:
            entry = STAGE_TO_PURPOSES.get(stage_id)
            if entry is None:
                continue
            if entry.get('exact') or entry.get('prefix'):
                offenders.append(stage_id)
        assert not offenders, (
            f"Stages flagged non-LLM still have purposes: {offenders}"
        )


class TestLegacyFlatDirFallbackRemoved:
    """#686: get_prompt_logs used to fall back to the flat, process-shared
    src/unified_pipeline/prompt_logs/ dir and admit any .txt file there by
    mtime alone -- run_id appeared nowhere in that loop, so one run's
    transcripts could be served to a request for a different run. That
    fallback is now gone; only per-run storage (prompt_logs/ keys) is read.
    """

    LEGACY_DIR = (
        Path(__file__).resolve().parents[3] / "src" / "unified_pipeline" / "prompt_logs"
    )

    def _seed_legacy_file(self, purpose: str = "stage_2") -> Path:
        self.LEGACY_DIR.mkdir(parents=True, exist_ok=True)
        path = self.LEGACY_DIR / f"2026-01-01_00-00-00_{purpose}_abcdef012345.txt"
        path.write_text("this belongs to a different run's transcript")
        return path

    def teardown_method(self, method):
        shutil.rmtree(self.LEGACY_DIR, ignore_errors=True)

    def test_get_prompt_logs_ignores_the_legacy_flat_dir(self, client, db):
        from app.main import app
        from app.auth import get_current_user
        from app.models import Run, Step

        legacy_file = self._seed_legacy_file(purpose="stage_2")
        # mtime well after the run's start -- under the old fallback this was
        # exactly the file that would have been admitted.
        run_start = datetime(2020, 1, 1, 0, 0, 0)

        db.add(Run(
            id="LEGACY1", filename="cv.docx", file_type="docx",
            status="complete", started_at=run_start,
        ))
        db.add(Step(
            run_id="LEGACY1", step_number=1, stage_id="2",
            step_name="Entry Extraction", status="complete",
        ))
        db.commit()

        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(
            role="admin", email="admin@example.com"
        )
        try:
            resp = client.get("/api/run/LEGACY1/prompt-logs", params={"step": 1})
        finally:
            app.dependency_overrides.pop(get_current_user, None)

        assert resp.status_code == 200
        body = resp.json()
        filenames = [log["filename"] for log in body["logs"]]
        assert str(legacy_file) not in filenames
        assert legacy_file.name not in filenames
        assert body["logs"] == []
