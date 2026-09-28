"""Stage <-> prompt-log purpose routing and bounded prompt-log listing.

Extracted from app/api/steps.py (PR #780 review, r3965813607#2): the
stage/purpose mapping is pipeline domain logic, not HTTP routing, and is now
independently testable here.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy.orm import Session

from app.models import Step

logger = logging.getLogger(__name__)

# Per-file content cap (#780 review r3965818983): was an inline literal.
PROMPT_LOG_CONTENT_MAX_CHARS = 50_000

# Mapping of stage IDs to the set of `purpose` values written into prompt log
# filenames by src/unified_pipeline/core/prompt_logger.py.
#
# Filenames look like: {YYYY-MM-DD_HH-MM-SS}_{purpose}_{12-char-hex-id}[_READABLE].{txt}
# Today every LLM call routed through unified_pipeline.llm_client.call_llm logs
# with purpose=<stage> (e.g. "stage_3a"). Older files in the directory used
# more specific purposes (e.g. "taxonomy_mapping_pass1", "field_extraction_batch_S8");
# they're preserved here for viewing historical runs.
#
# Each stage entry separates `exact` matches (the purpose must equal the value)
# from `prefix` matches (the purpose must equal the value or start with
# value + "_"). The prefix list intentionally excludes ambiguous bare names
# like "stage_4" -- that gets exact-only treatment so it doesn't swallow
# `stage_4_5_*` files that belong to stage 4.5.
#
# Stage IDs without LLM activity get an empty list and surface a friendly
# explanation via STAGES_WITHOUT_PROMPT_LOGS instead.
#
# The '4' exact list carries #771's stage-4 label drops (commits b883e5f
# and ccbb57c): #771 deleted the modules behind those five purposes, so
# nothing surviving emits them. Merging dev brought both commits in; the
# STAGE_TO_PURPOSES conflict was resolved by keeping this moved dict and
# dropping steps.py's copy.
STAGE_TO_PURPOSES: dict[str, dict[str, list[str]]] = {
    '1a': {'exact': [], 'prefix': []},
    '1b': {'exact': [], 'prefix': []},
    '2':  {'exact': ['stage_2', 'stage_2_entry_extraction'],
           'prefix': []},
    '2a': {'exact': ['stage_2a'],
           'prefix': ['segmentation']},
    '3a': {'exact': ['stage_3a', 'core_taxonomy_mapper', 'core_taxonomy_v2',
                     'stage_3a_header_taxonomy', 'taxonomy_mapping_pass1'],
           'prefix': []},
    '3b': {'exact': ['stage_3b', 'stage_3b_entry_classification',
                     'stage_3b_t_validation', 'stage_3b_fragment_reconnection',
                     'taxonomy_mapping_pass2', 'taxonomy_mapping_pass2_batch'],
           'prefix': []},
    '4':  {'exact': ['stage_4', 'core_extraction_recovery', 'core_personal_info',
                     'core_section_orchestrator', 'core_candidate_surfacer'],
           'prefix': ['stage_4_field', 'stage_4_extraction',
                      'field_extraction_batch', 'parser_']},
    '4.5': {'exact': ['stage_4_5', '4.5_summary_generation', '4.5_m1_scoring'],
            'prefix': ['stage_4_5', 'research_summary']},
    '5':  {'exact': [], 'prefix': []},
    '5b': {'exact': ['stage_5b', 'institution_enrichment'],
           'prefix': []},
    '5c': {'exact': ['stage_5c', 'stage_5c_teaching_formatting'],
           'prefix': ['teaching_formatter']},
    '5d': {'exact': ['stage_5d', 'stage_5d_citation_formatting'],
           'prefix': ['citation_formatter']},
    '6':  {'exact': ['stage_6'], 'prefix': []},
}

# Filename: {YYYY-MM-DD}_{HH-MM-SS}_{purpose}_{12 hex chars}[_READABLE|_RESPONSE].{ext}
#
# .json dropped from the extension group (#780 review r3965815739): the
# retrieval code below (list_prompt_logs) only ever reads `.txt` transcripts,
# so a `.json` name matching here was a correctness inconsistency -- it
# "parsed" but could never be listed. If JSON prompt logs are ever served,
# widen both this regex and the `.txt` filter in list_prompt_logs together.
_PROMPT_LOG_FILENAME_RE = re.compile(
    r'^\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}_(?P<purpose>.+)_[0-9a-f]{12}'
    r'(?:_READABLE|_RESPONSE)?\.txt$'
)

# Stages that don't have prompt logs, with an explanation.
# NOTE: stages 5b and 6 were mistakenly included here. Both make LLM calls --
# 5b runs `call_llm(stage="stage_5b")` from institution
# enrichment, and 6 calls `call_llm(stage="stage_6")` for geographic scope
# classification during template population. Prompts ARE being logged for
# both, but the short-circuit returned the "non-LLM stage" message before
# the file walker ran. Keep only stages with zero LLM activity here.
STAGES_WITHOUT_PROMPT_LOGS = {
    '1a': "Stage 1a (Hierarchy Extraction) does not have prompt logging wired up.",
    '1b': "Stage 1b (Hierarchy Mapping) is a non-LLM stage - no prompts are used.",
    '5': "Stage 5 (PubMed Enrichment) is a non-LLM stage - uses PubMed API.",
}


class PromptLogStorage(Protocol):
    """The subset of the storage backend list_prompt_logs relies on."""

    def list_files(self, run_id: str, prefix: str) -> list[str]: ...
    def get_file(self, run_id: str, key: str) -> bytes: ...


def purpose_from_filename(name: str) -> str | None:
    """Extract the `purpose` field from a prompt-log filename, or None if it
    doesn't match the prompt_logger naming convention."""
    m = _PROMPT_LOG_FILENAME_RE.match(name)
    return m.group('purpose') if m else None


def purpose_matches_stage(purpose: str, stage_purposes: dict[str, list[str]] | list) -> bool:
    """A filename's purpose belongs to a stage if it equals one of the
    `exact` values, or starts with one of the `prefix` values followed by
    an underscore. The split prevents collisions like `stage_4_5_*` being
    claimed by stage 4 just because it shares the `stage_4_` prefix."""
    if not stage_purposes:
        return False
    if purpose in stage_purposes.get('exact', ()):
        return True
    for known in stage_purposes.get('prefix', ()):
        if purpose.startswith(known + '_'):
            return True
    return False


def resolve_stage_id_for_step(db: Session, run_id: str, step_number: int) -> str | None:
    """Look up the stage_id for a run's step (#780 review r3965813607 --
    moves the third of the three db.query( sites out of api/steps.py).
    Returns None if the step doesn't exist."""
    step_record = (
        db.query(Step)
        .filter(Step.run_id == run_id, Step.step_number == step_number)
        .first()
    )
    if not step_record:
        return None
    return step_record.stage_id or str(step_number)


@dataclass(frozen=True)
class PromptLogPage:
    """A bounded page of prompt logs (#780 review r3965818983): total says
    how many matched before paging, truncated says whether this page is not
    the last one."""
    logs: list[dict[str, str]]
    total: int
    truncated: bool


def list_prompt_logs(
    storage: PromptLogStorage,
    run_id: str,
    stage_id: str,
    *,
    limit: int,
    offset: int,
) -> PromptLogPage:
    """List this run's prompt-log files for stage_id, newest first, paged by
    (limit, offset). Bounded on two axes: PROMPT_LOG_CONTENT_MAX_CHARS per
    file (unchanged) and now the file COUNT too -- previously every matching
    file was read and returned in one response."""
    purposes = STAGE_TO_PURPOSES.get(stage_id, [])

    try:
        storage_keys = storage.list_files(run_id, prefix="prompt_logs/")
    except Exception as exc:
        logger.warning("Could not list prompt logs in storage for %s: %s", run_id, exc)
        storage_keys = []

    matches: list[tuple[str, str]] = []  # (filename, storage_key)
    seen: set[str] = set()
    for key in storage_keys:
        filename = key.rsplit('/', 1)[-1]
        if not filename.endswith('.txt') or filename in seen:
            continue
        purpose = purpose_from_filename(filename)
        if not purpose or not purpose_matches_stage(purpose, purposes):
            continue
        seen.add(filename)
        matches.append((filename, key))

    matches.sort(key=lambda pair: pair[0], reverse=True)
    total = len(matches)
    page = matches[offset:offset + limit]

    logs: list[dict[str, str]] = []
    for filename, key in page:
        try:
            content = storage.get_file(run_id, key).decode('utf-8', errors='replace')
            logs.append({"filename": filename, "content": content[:PROMPT_LOG_CONTENT_MAX_CHARS]})
        except Exception as exc:
            logs.append({"filename": filename, "content": f"Error reading file: {exc}"})

    return PromptLogPage(logs=logs, total=total, truncated=offset + len(page) < total)
