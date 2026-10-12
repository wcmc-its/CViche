"""Tests for the stage_id -> prompt-log routing in steps.py.

Stage 5b was mistakenly listed in STAGES_WITHOUT_PROMPT_LOGS (the short-circuit
table that returns a "non-LLM stage" message before walking the prompt_logs
dir). Closes #1.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.api.steps import STAGE_TO_PURPOSES, STAGES_WITHOUT_PROMPT_LOGS

# Filename parser/matcher moved to app.services.prompt_log_service (#780
# review r3965813607#2) -- new tests import it from its actual home; the
# STAGE_TO_PURPOSES/STAGES_WITHOUT_PROMPT_LOGS import above keeps working
# unmodified via steps.py's re-export.
from app.services.prompt_log_service import (
    purpose_from_filename,
    purpose_matches_stage,
)


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

    def setup_method(self, method):
        self._seeded_files: list[Path] = []
        # Record whether LEGACY_DIR predates this test, and its exact
        # contents if so -- teardown_method uses both to prove it leaves the
        # directory exactly as it found it (never rmtree: a full checkout
        # can hold tens of thousands of real transcript files here).
        self._dir_pre_existed = self.LEGACY_DIR.is_dir()
        self._pre_listing = (
            {p.name for p in self.LEGACY_DIR.iterdir()} if self._dir_pre_existed else set()
        )

    def _seed_legacy_file(self, purpose: str = "stage_2") -> Path:
        self.LEGACY_DIR.mkdir(parents=True, exist_ok=True)
        # The id slot must hold exactly 12 hex chars: that is what
        # steps.py's _PROMPT_LOG_FILENAME_RE requires, and a name that does
        # not match parses to purpose=None, which the removed fallback
        # skipped too -- lengthening this would make the test vacuous.
        # 48 random bits still make a collision with (or a human misreading
        # of) a real transcript that predates the test impossible.
        unique = uuid.uuid4().hex[:12]
        path = self.LEGACY_DIR / f"2026-01-01_00-00-00_{purpose}_{unique}.txt"
        path.write_text("this belongs to a different run's transcript")
        self._seeded_files.append(path)
        return path

    def teardown_method(self, method):
        # LEGACY_DIR is the real, process-shared src/unified_pipeline/prompt_logs/
        # directory -- in a full checkout it holds tens of thousands of files
        # that predate this test and belong to unrelated runs. Delete only the
        # file(s) this test itself seeded (unlink, never rmtree), and only
        # remove the directory itself if this test is the one that created it.
        for path in self._seeded_files:
            path.unlink(missing_ok=True)

        if self._dir_pre_existed:
            post_listing = {p.name for p in self.LEGACY_DIR.iterdir()}
            assert post_listing == self._pre_listing, (
                "legacy dir's contents changed by this test beyond the file(s) "
                f"it seeded: before={sorted(self._pre_listing)!r} "
                f"after={sorted(post_listing)!r}"
            )
        elif self.LEGACY_DIR.is_dir():
            remaining = list(self.LEGACY_DIR.iterdir())
            assert not remaining, (
                f"legacy dir has unexpected leftover files after cleanup: {remaining}"
            )
            self.LEGACY_DIR.rmdir()

    def test_get_prompt_logs_ignores_the_legacy_flat_dir(self, client, db):
        from app.auth import get_current_user
        from app.main import app
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


class TestPromptLogFilenameParsing:
    """r3965862896#6: the prompt filename parser and matcher are core routing
    logic -- exact matches, prefix matches, invalid names, and the
    stage_4/stage_4_5 collision the module docstring documents."""

    def test_exact_purpose_match(self):
        purposes = STAGE_TO_PURPOSES['3a']
        purpose = purpose_from_filename("2026-01-01_00-00-00_stage_3a_0123456789ab.txt")
        assert purpose == "stage_3a"
        assert purpose_matches_stage(purpose, purposes) is True

    def test_prefix_purpose_match(self):
        purposes = STAGE_TO_PURPOSES['4']
        purpose = purpose_from_filename(
            "2026-01-01_00-00-00_stage_4_field_S8_0123456789ab.txt"
        )
        assert purpose == "stage_4_field_S8"
        assert purpose_matches_stage(purpose, purposes) is True

    def test_invalid_filename_does_not_parse(self):
        # No 12-hex-char id segment.
        assert purpose_from_filename("not_a_prompt_log.txt") is None
        # Missing the timestamp prefix entirely.
        assert purpose_from_filename("stage_3a_0123456789ab.txt") is None

    def test_json_prompt_log_name_no_longer_parses(self):
        """r3965815739: .json was dropped from the filename regex -- the
        retrieval code only ever reads .txt, so a .json name matching here
        was a dead-end correctness inconsistency."""
        assert purpose_from_filename("2026-01-01_00-00-00_stage_3a_0123456789ab.json") is None

    def test_stage_4_vs_stage_4_5_collision(self):
        """A stage_4_5_* purpose must be claimed by stage 4.5, never by stage
        4's `stage_4_` prefix entry -- this is exactly what the exact/prefix
        split in purpose_matches_stage exists to prevent."""
        purpose = purpose_from_filename(
            "2026-01-01_00-00-00_stage_4_5_summary_generation_0123456789ab.txt"
        )
        assert purpose == "stage_4_5_summary_generation"
        assert purpose_matches_stage(purpose, STAGE_TO_PURPOSES['4']) is False
        assert purpose_matches_stage(purpose, STAGE_TO_PURPOSES['4.5']) is True

    def test_purpose_matches_stage_false_for_empty_purposes(self):
        assert purpose_matches_stage("stage_1a", STAGE_TO_PURPOSES['1a']) is False
