"""Tests for the stage_id -> prompt-log routing in steps.py.

Stage 5b was mistakenly listed in STAGES_WITHOUT_PROMPT_LOGS (the short-circuit
table that returns a "non-LLM stage" message before walking the prompt_logs
dir). Closes #1.
"""
from __future__ import annotations

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
