#!/usr/bin/env python3
"""
CViche - Full CV Processing Pipeline (V15)

Runs the complete CViche processing pipeline:
- Stage 1a: Segmentation (LLM-powered hierarchical segmentation)
- Stage 1b: Hierarchy Mapping (maps headers to element indices, no LLM)
- Stage 2: Entry Extraction (detects entries and extracts text with LLM)
- Stage 3a: Header Taxonomy Mapping (maps CV headers to taxonomy codes with LLM)
- Stage 3b: Entry Classification (classifies entries using header context + content)
- Stage 3: Run both 3a and 3b together
- Stage 4: Field Extraction (extracts structured fields from classified entries)
- Stage 4.5: Research Summary Generation (generates biosketch-style M1 summary)
- Stage 5: PubMed Enrichment (enriches publications with PubMed metadata)
- Stage 5b: Institution Enrichment (adds city/state via LLM lookups)
- Stage 5c: Teaching Formatter (LLM-reformats K-code entries for readability)
- Stage 5d: Citation Formatter (LLM-reformats non-enriched citations to Vancouver format)
- Stage 6: WCM Word Template Generation (creates formatted Word document)

Usage:
    python3 run_full_pipeline.py <cv_path> [--stage STAGE]

Arguments:
    cv_path       : Path to Word document or just the document UID
    --stage STAGE : Run ONLY this stage: '1a', '1b', '2', '3a', '3b', '3', '4', '4.5', '5', '5b', '5c', '5d', or '6'. Default: run all

The model is not a CLI argument. Each stage resolves its own from
llm_config.yaml (any stage can override the default), so there is no single model
to override; the summary reports which ones actually served the run.

Example:
    # Run full pipeline
    python3 run_full_pipeline.py 2097_Upton_Cv

    # Run only Stage 2 (requires Stage 1b output to exist)
    python3 run_full_pipeline.py 2097_Upton_Cv --stage 2

    # Run only Stage 3a (header taxonomy mapping)
    python3 run_full_pipeline.py 2097_Upton_Cv --stage 3a

    # Run only Stage 3b (entry classification, requires 3a)
    python3 run_full_pipeline.py 2097_Upton_Cv --stage 3b

    # Run both 3a and 3b together
    python3 run_full_pipeline.py 2097_Upton_Cv --stage 3

    # Run only Stage 4 (field extraction, requires 3b)
    python3 run_full_pipeline.py 2097_Upton_Cv --stage 4

    # Run only Stage 4.5 (research summary generation, requires 4)
    python3 run_full_pipeline.py 2097_Upton_Cv --stage 4.5

    # Run only Stage 6 (Word template generation, uses best available input)
    python3 run_full_pipeline.py 2097_Upton_Cv --stage 6

Outputs:
    - Stage 1a: src/unified_pipeline/outputs/stage_1a_segmentation/{uid}_segmented.json
    - Stage 1b: src/unified_pipeline/outputs/stage_1b_hierarchy_mapping/{uid}_hierarchy_mapped.json
    - Stage 2:  src/unified_pipeline/outputs/stage_2_entry_extraction/{uid}_entries.json
    - Stage 3a: src/unified_pipeline/outputs/stage_3a_header_mappings/{uid}_header_taxonomy.json
    - Stage 3b: src/unified_pipeline/outputs/stage_3b_classified_entries/{uid}_classified.json
    - Stage 4:  src/unified_pipeline/outputs/stage_4_field_extraction/{uid}_fields.json
    - Stage 4.5: src/unified_pipeline/outputs/stage_4_5_research_summary/{uid}_research_summary.json
    - Stage 5:  src/unified_pipeline/outputs/stage_5_enrichment/{uid}_enriched.json
    - Stage 5b: src/unified_pipeline/outputs/stage_5b_institution_enrichment/{uid}_institution_enriched.json
    - Stage 5c: src/unified_pipeline/outputs/stage_5c_teaching_formatted/{uid}_teaching_formatted.json
    - Stage 5d: src/unified_pipeline/outputs/stage_5d_citation_formatted/{uid}_citation_formatted.json
    - Stage 6:  src/unified_pipeline/outputs/stage_6_wcm_documents/{uid}_wcm.docx
"""

import contextvars
import functools
import hashlib
import sys
import json
import logging
import logging.config
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from unified_pipeline.core.prompt_logger import set_current_run_id
from unified_pipeline.llm_client import LlmUsage, format_models_used
from unified_pipeline.segmentation.chunked_chat_hierarchy_extractor import get_cv_hierarchy_chunked
from unified_pipeline.stage_1b_hierarchy_mapper import run_stage_1b
from unified_pipeline.stage_2_entry_extraction import run_stage_2
from unified_pipeline.stage_3a_header_taxonomy_mapper import run_stage_3a
from unified_pipeline.stage_3b_entry_classifier import run_stage_3b
from unified_pipeline.stage_4_field_extractor import process_cv as run_stage_4
from unified_pipeline.stage_4_5_research_summary import run_stage_4_5
from unified_pipeline.stage_5_pubmed_enrichment import run_stage5
from unified_pipeline.stage_5b_institution_enrichment import run_stage5b
from unified_pipeline.stage_5c_teaching_formatter import run_stage_5c
from unified_pipeline.stage_5d_citation_formatter import run_stage_5d
from unified_pipeline.stage_6_word_template import run_stage6
from unified_pipeline.repair.protected_data import REPAIR_FLAG_ENV, repair_flag_on
from unified_pipeline.stage_errors import StageError, record_stage_outcome, stage_errors_path

logger = logging.getLogger(__name__)
# "__main__" when this file is run as the CLI, "run_full_pipeline" when a test
# imports it. configure_cli_logging() and the stderr filter both key off the
# real name so the handlers land on this logger either way.
_NARRATION_LOGGER = logger.name


class _NarrationOnly(logging.Filter):
    """Let the stdout handler take the narration (INFO and below) and nothing else."""

    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno < logging.WARNING


class _NotNarration(logging.Filter):
    """Keep this CLI's narration off stderr -- it already went to stdout, unprefixed.

    Every other logger's INFO still reaches the stderr handler, which is where
    the stage modules' own logging has always gone.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return not (record.name == _NARRATION_LOGGER
                    and record.levelno < logging.WARNING)


def configure_cli_logging() -> None:
    """Wire this CLI's two output streams. Called from __main__, and by the
    tests that assert on either stream.

    stdout: this module's INFO records, formatted as the message and nothing
    else. That is the narration AND a cross-process contract --
    scripts/run_corpus_batch.sh:152-163 greps `Top-level sections:`,
    `Total headers:`, `Entries extracted:`, `Entries classified:`,
    `Entries defaulted:` (#810) and an ANCHORED `^Models: ` out of this
    stdout, so a timestamp or level in front of a line blanks a column of
    summary.tsv. CODING_STANDARDS.md 7.1 names it;
    src/unified_pipeline/tests/test_run_full_pipeline_stdout_contract.py pins it.

    stderr: everything else, timestamped -- WARNING and above from here
    (stage-failure notices and their tracebacks) plus every other logger. The
    two filters are the whole split: narration goes to exactly one stream.

    dictConfig, not basicConfig (project convention), and deliberately not
    web_interface/backend/app/logging_config.py's configure_logging():
    CODING_STANDARDS.md 1.4 -- the pipeline core does not import the web
    backend.
    """
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "narration_only": {"()": _NarrationOnly},
            "not_narration": {"()": _NotNarration},
        },
        "formatters": {
            "plain": {"format": "%(asctime)s %(levelname)s %(name)s %(message)s"},
            "message_only": {"format": "%(message)s"},
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "plain",
                "stream": "ext://sys.stderr",
                "filters": ["not_narration"],
            },
            "narration": {
                "class": "logging.StreamHandler",
                "formatter": "message_only",
                "stream": "ext://sys.stdout",
                "filters": ["narration_only"],
            },
        },
        "loggers": {
            _NARRATION_LOGGER: {
                "level": "INFO",
                "handlers": ["narration"],
                # WARNING and above still travel up to the stderr handler; the
                # filters above decide which stream each record lands on.
                "propagate": True,
            },
        },
        "root": {"level": "INFO", "handlers": ["default"]},
    })

# '3' is a CLI alias for "run 3a then 3b", not a stage with a runner of its own.
_COMPOSITE_STAGE = '3'
# Nothing runs after stage 6, so its failure notice does not promise to continue.
_FINAL_STAGE = '6'

#: The repo root, taken from this script's own location so no path below
#: depends on the directory the CLI happens to be launched from (#490). Only
#: the script's directory is resolved: a worktree's ``outputs`` symlink to a
#: shared corpus farm is left unresolved, so it still lands on the farm.
_REPO_ROOT = Path(__file__).resolve().parent
#: Root the stage_* output dirs (and the #745 stage-error record) live under.
_OUTPUTS_ROOT = _REPO_ROOT / 'src' / 'unified_pipeline' / 'outputs'
_STAGE_1A_DIRNAME = 'stage_1a_segmentation'
# The only stages that open the source document. Everything else works from the
# JSON artifacts an earlier run left behind -- including stage 4, which reads
# Path(docx_path).stem and never the file -- so a standalone --stage rerun of
# those does not need the .docx to still be on disk.
_STAGES_READING_THE_DOCX = ('1a', '1b', '2')
_BANNER_WIDTH = 80


def get_stage_order() -> list[str]:
    """Return ordered list of stage identifiers."""
    return ['1a', '1b', '2', '3a', '3b', '3', '4', '4.5', '5', '5b', '5c', '5d', '6']


# The MINIMUM prerequisite each stage can start from, in one place.
#
# Before #780's review the registry stopped at stage 4 and stages 4.5-6 resolved
# their inputs by globbing the outputs tree instead -- two competing
# dependency-resolution mechanisms, one of which could pick a previous run's
# artifact. This map is the only one; STAGE_INPUT_PREFERENCE below says which
# *better* input a stage will use when the run produced one.
STAGE_DEPENDENCIES: dict[str, tuple[str, ...]] = {
    '1a': (),
    '1b': ('1a',),
    '2': ('1b',),
    '3a': ('1a',),
    '3b': ('2', '3a'),
    '3': ('1a', '2'),      # --stage 3 runs 3a then 3b
    '4': ('3b',),
    '4.5': ('4',),
    '5': ('4',),
    '5b': ('4',),
    '5c': ('4',),
    '5d': ('4',),
    '6': ('4',),
}

# Which predecessor output each stage prefers as its input, best first. A stage
# not listed here takes its input from a named prerequisite instead (stage 1b
# from 1a, stage 2 from 1b, stage 3b from 3a) rather than from a preference
# chain.
STAGE_INPUT_PREFERENCE: dict[str, tuple[str, ...]] = {
    '4.5': ('4',),
    '5': ('4',),
    '5b': ('5', '4'),
    '5c': ('5b', '5', '4'),
    '5d': ('5c', '5b', '5', '4'),
    '6': ('5d', '5c', '5b', '5', '4'),
}


@dataclass(frozen=True)
class StageResult:
    """What one stage produced.

    Replaces the per-stage ``dict`` with optional ``error`` / ``skipped`` /
    ``cost`` / ``output_file`` keys that every reader had to guess at (#780
    review). ``cost=None`` means *not known*, which is deliberately distinct
    from ``0.0``: stage 5b's cost is unreadable when its output file is missing
    or corrupt, and #489 is exactly the incident where that read as a genuine
    zero.
    """

    stage: str
    output_file: str | None = None
    duration_seconds: float = 0.0
    cost: float | None = None
    error: str | None = None
    skipped_reason: str | None = None
    stats: Mapping[str, object] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.error is None and self.skipped_reason is None


def _produced_a_file(output_file: str | None) -> bool:
    """True only when the recorded output exists on disk *as a file*.

    A recorded path is not evidence: ``{'output_file': '/path/missing.docx'}``
    used to count as success, which left #443's failure mode half open -- exit
    0 with no deliverable. A directory at that path is not a document either.
    """
    return bool(output_file) and Path(output_file).is_file()


def failed_stages(results: Mapping[str, StageResult]) -> list[str]:
    """Return the names of stages that did not produce what they were asked to.

    Every stage runs inside ``run_stage``, which records its exception as
    ``results['stage_X'].error`` and carries on. Nothing ever read that back: a
    stage-6 crash printed a warning, then printed PIPELINE COMPLETE and exited 0
    with no document on disk (#443). Batch tooling that filters on the exit
    column -- which is what any reasonable consumer does -- counted two
    zero-output runs of 96 as successes.

    Counted as failed:

    - a stage that stored an ``error``
    - a stage that was reached but skipped for a missing prerequisite, which
      only happens downstream of an earlier failure
    - stage 6 whose ``output_file`` is not a file on disk, because stage 6 is
      the deliverable: if it produced no document the run produced nothing,
      whatever else succeeded

    A stage absent from ``results`` never ran (``--stage`` targeted a different
    one) and is not a failure.
    """
    failed = []
    for name, result in results.items():
        if result.error is not None or result.skipped_reason is not None:
            failed.append(name)
        elif name == 'stage_6' and not _produced_a_file(result.output_file):
            failed.append(name)
    return failed


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins}m {secs:.1f}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours}h {mins}m {secs:.1f}s"


def get_output_paths(document_uid: str) -> dict[str, Path]:
    """Expected output file path for EVERY stage.

    Each entry mirrors the writing module's own naming, so a standalone
    ``--stage`` run can find an input by its exact path instead of a
    ``*{uid}*`` glob:

    - 5:  ``stage_5_pubmed_enrichment.py:49,717`` (``OUTPUT_DIR``, ``{uid}_enriched.json``)
    - 5b: ``stage_5b_institution_enrichment.py:59,421``
    - 5c: ``stage_5c_teaching_formatter.py:32,414``
    - 5d: ``stage_5d_citation_formatter.py:30,381``
    - 6:  ``stage_6_word_template.py:269,900``

    Those five modules name their output from the ``document_uid`` carried in
    their input JSON, which every stage propagates unchanged from stage 1a.
    """
    base = _OUTPUTS_ROOT
    return {
        '1a': base / _STAGE_1A_DIRNAME / f'{document_uid}_segmented.json',
        '1b': base / 'stage_1b_hierarchy_mapping' / f'{document_uid}_hierarchy_mapped.json',
        '2': base / 'stage_2_entry_extraction' / f'{document_uid}_entries.json',
        '3a': base / 'stage_3a_header_mappings' / f'{document_uid}_header_taxonomy.json',
        '3b': base / 'stage_3b_classified_entries' / f'{document_uid}_classified.json',
        '4': base / 'stage_4_field_extraction' / f'{document_uid}_fields.json',
        '4.5': base / 'stage_4_5_research_summary' / f'{document_uid}_research_summary.json',
        '5': base / 'stage_5_enrichment' / f'{document_uid}_enriched.json',
        '5b': base / 'stage_5b_institution_enrichment' / f'{document_uid}_institution_enriched.json',
        '5c': base / 'stage_5c_teaching_formatted' / f'{document_uid}_teaching_formatted.json',
        '5d': base / 'stage_5d_citation_formatted' / f'{document_uid}_citation_formatted.json',
        '6': base / 'stage_6_wcm_documents' / f'{document_uid}_wcm.docx',
    }


def check_prerequisites(start_stage: str, document_uid: str) -> tuple[bool, str | None, str | None]:
    """
    Check that prerequisite outputs exist for starting at a given stage.

    Resolves through STAGE_DEPENDENCIES, so every stage is validated -- before
    #780's review the map stopped at stage 4 and ``--stage 5c`` skipped
    validation entirely.

    Returns:
        (success: bool, missing_file: str or None, required_stage: str or None)
    """
    paths = get_output_paths(document_uid)
    for prereq in STAGE_DEPENDENCIES.get(start_stage, ()):
        prereq_path = paths[prereq]
        if not prereq_path.exists():
            return False, str(prereq_path), prereq
    return True, None, None


def resolve_cv_path(cv_path_or_uid: str) -> tuple[str, str]:
    """
    Resolve CV path from either a full path or just the document UID.

    Supports:
    - Full path: 'data/sample_cvs/word/2097_Upton_Cv.docx'
    - Just UID: '2097_Upton_Cv' (looks in data/sample_cvs/word/)

    Returns:
        (cv_path, document_uid)
    """
    # Check if it's already a valid path
    if Path(cv_path_or_uid).exists():
        # Extract UID from filename
        uid = Path(cv_path_or_uid).stem
        return cv_path_or_uid, uid

    # Check if it looks like a path (has directory separators or .docx extension)
    if '/' in cv_path_or_uid or '\\' in cv_path_or_uid or cv_path_or_uid.endswith('.docx'):
        # It's a path but file doesn't exist
        return cv_path_or_uid, Path(cv_path_or_uid).stem

    # Treat as UID - look in standard location
    standard_path = Path('data/sample_cvs/word') / f'{cv_path_or_uid}.docx'
    if standard_path.exists():
        return str(standard_path), cv_path_or_uid

    # Also try without assuming .docx extension was missing
    if Path(f'data/sample_cvs/word/{cv_path_or_uid}').exists():
        return f'data/sample_cvs/word/{cv_path_or_uid}', Path(cv_path_or_uid).stem

    # Return as-is and let the error happen downstream with a clear message
    return cv_path_or_uid, cv_path_or_uid


# prompt_logger._RUN_ID_RE caps a run id at 10 chars of [A-Za-z0-9_-]; that
# module is off limits for this ticket, so document_uid (routinely longer,
# e.g. 'sample_vasquez_cv') is hashed down to this length rather than passed
# through as-is.
_RUN_SCOPE_ID_LEN = 10


def resolve_cv_path_for_run(cv_path_or_uid: str) -> tuple[str, str]:
    """resolve_cv_path(), then scope this process's prompt-log writes to the
    resolved document for the rest of the run (#686 -- the CLI never scoped
    its writes, so every run's transcripts landed in one shared flat dir).
    """
    cv_path, document_uid = resolve_cv_path(cv_path_or_uid)
    run_scope_id = hashlib.sha256(document_uid.encode()).hexdigest()[:_RUN_SCOPE_ID_LEN]
    set_current_run_id(run_scope_id)
    return cv_path, document_uid


def _scope_prompt_logger_per_run(fn: Callable[[], int]) -> Callable[[], int]:
    """Decorator: run fn inside a fresh copy of the current contextvars
    Context, so set_current_run_id() -- called deep inside fn, via
    resolve_cv_path_for_run() -- only ever mutates that copy. No module-level
    state is needed to undo it: the copy is discarded when fn returns OR
    raises (including sys.exit()'s SystemExit), so prompt_logger's ContextVar
    reverts on its own in the caller's real context -- no token, no global,
    no explicit reset call. Applied to main() itself rather than called
    inside it so main()'s line count never changes: a decorator line is not
    part of a FunctionDef node's own lineno..end_lineno span."""
    @functools.wraps(fn)
    def wrapper() -> int:
        return contextvars.copy_context().run(fn)
    return wrapper


@dataclass
class PipelineContext:
    """Everything a stage needs to know about the run it is part of.

    ``outputs`` holds only what THIS run produced, keyed by stage id. That is
    the whole point of it: stages 5b/5c/5d/6 used to rediscover their input by
    globbing ``*{uid}*`` in the outputs tree and taking ``candidates[0]``, so a
    previous run's artifact could be rendered as if it were this run's (#780
    review). ``results`` is the reporting record and may also carry artifacts
    that a ``--stage`` run found already on disk.
    """

    cv_path: Path
    document_uid: str
    target_stage: str | None = None
    outputs: dict[str, str] = field(default_factory=dict)
    results: dict[str, StageResult] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Stage 4 is handed cv_path and locates stage 3b's output from
        # Path(docx_path).stem (stage_4_field_extractor.py:104-106), so the two
        # identities must agree. resolve_cv_path() always returns them equal;
        # this fails loudly rather than silently extracting a different CV if
        # that ever stops being true.
        if self.cv_path.stem != self.document_uid:
            raise ValueError(
                f"resolved CV path stem {self.cv_path.stem!r} does not match "
                f"document uid {self.document_uid!r}; stage 4 resolves its input "
                f"from the path stem and would read another document's entries")

    def should_run(self, stage: str) -> bool:
        """Whether this run executes ``stage``."""
        if self.target_stage is None:
            return True  # Full pipeline - run all stages
        if self.target_stage == _COMPOSITE_STAGE:
            # --stage 3 means run both 3a and 3b
            return stage in ('3a', '3b')
        return stage == self.target_stage  # Single stage mode - only run the target

    def record(self, stage: str, result: StageResult) -> None:
        """Store a stage's result, and its artifact if it produced one."""
        self.results[f'stage_{stage}'] = result
        if result.output_file:
            self.outputs[stage] = result.output_file

    def best_input(self, *stages: str) -> str | None:
        """The best available input among ``stages``, best first.

        In a full run the answer comes only from what this run produced: an
        artifact this run did not write does not exist as far as the pipeline is
        concerned. Disk is consulted only in standalone ``--stage`` mode, where
        reading an earlier run's artifact is the explicit intent -- and then by
        the stage's exact expected path, never by a substring glob.
        """
        for stage in stages:
            produced = self.outputs.get(stage)
            if produced:
                return produced
        if self.target_stage is None:
            return None
        expected = get_output_paths(self.document_uid)
        for stage in stages:
            path = expected[stage]
            if path.is_file():
                return str(path)
        return None


@dataclass(frozen=True)
class PipelineResult:
    """The whole run: every stage's result, wall time, and what failed."""

    results: Mapping[str, StageResult]
    total_duration_seconds: float
    failed: list[str]

    @property
    def total_cost(self) -> float:
        """Derived, never accumulated -- a new costed stage cannot be forgotten."""
        return sum(result.cost or 0.0 for result in self.results.values())

    @property
    def exit_code(self) -> int:
        return 1 if self.failed else 0


def _banner(title: str) -> None:
    """The rule/title/rule block each stage opens with."""
    logger.info("=" * _BANNER_WIDTH)
    logger.info("%s", title)
    logger.info("=" * _BANNER_WIDTH)
    logger.info("")


def _requirement_label(stages: tuple[str, ...], all_required: bool = False) -> str:
    """Human name for the inputs a stage needed, in preference order."""
    if all_required:
        return " and ".join(f"Stage {stage}" for stage in stages)
    if len(stages) == 1:
        return f"Stage {stages[0]}"
    if len(stages) == 2:
        return f"Stage {stages[0]} or {stages[1]}"
    return f"Stage {', '.join(stages[:-1])}, or {stages[-1]}"


def _skipped(stage: str, requirement: str) -> StageResult:
    """Record a stage that could not start because its input was never produced."""
    logger.info("  Skipped: %s output required", requirement)
    return StageResult(stage=stage, skipped_reason=f"{requirement} required")


def _record_stage_outcome(document_uid: str, stage: str, error: StageError | None) -> None:
    """Write (or, on success, clear) ``stage``'s entry in the stage-error record
    quality_score reads for its fatal gate (#745).

    Failing to write it must neither turn a clean stage into a failure nor mask
    the stage's own exception, which run_stage has already logged and recorded
    in the summary -- so the write failure is logged with its traceback and the
    run carries on, the CLI's documented continue-on-error behaviour.
    """
    try:
        record_stage_outcome(stage_errors_path(_OUTPUTS_ROOT, document_uid), stage, error)
    except (OSError, ValueError):
        logger.exception("Could not update the stage-error record for stage %s", stage)


def run_stage(ctx: PipelineContext, stage: str,
              fn: Callable[[PipelineContext], StageResult]) -> StageResult:
    """Run one stage inside the pipeline's single failure boundary.

    One place owns timing, the try/except, the traceback, and result storage --
    so every stage, stage 1a included, behaves identically when it raises. Stage
    1a used to run outside any handler: a segmentation failure escaped main(),
    so no summary printed and failed_stages() never ran.
    """
    start = time.perf_counter()
    stage_error: StageError | None = None
    try:
        result = fn(ctx)
    except Exception as e:
        # Continue-on-error is the CLI's documented behaviour, so this catches
        # everything -- but it never swallows: the traceback goes to the logger
        # and the reason is carried into the summary and the exit code.
        logger.exception("Stage %s failed", stage)
        logger.warning("  Warning: Stage %s failed: %s: %s", stage, type(e).__name__, e)
        if stage != _FINAL_STAGE:
            logger.warning("  Continuing with remaining stages...")
        result = StageResult(stage=stage, error=f"{type(e).__name__}: {e}")
        stage_error = StageError.from_exception(stage, e)
    result = replace(result, duration_seconds=time.perf_counter() - start)
    if stage_error is not None or result.succeeded:
        _record_stage_outcome(ctx.document_uid, stage, stage_error)
    if result.succeeded:
        logger.info("  Time: %s", format_duration(result.duration_seconds))
    logger.info("")
    ctx.record(stage, result)
    return result


def _count_headers(nodes: list[dict]) -> int:
    """Total header nodes in a hierarchy, including every nested child.

    Iterative: a deep header chain must not hit the interpreter's recursion limit.
    """
    count = 0
    stack = [nodes]
    while stack:
        level = stack.pop()
        count += len(level)
        for node in level:
            stack.append(node.get('children', []))
    return count


def _hierarchy_lines(nodes: list[dict], depth: int = 0) -> list[str]:
    """Indented ``[LEVEL] text`` lines for the human-readable stage 1a dump.

    Iterative pre-order walk (see ``_count_headers``).
    """
    lines: list[str] = []
    stack = [(node, depth) for node in reversed(nodes)]
    while stack:
        node, d = stack.pop()
        level = node.get('level', 'H1')
        text = node.get('text', '')
        lines.append(f"{'  ' * d}[{level}] {text}")
        stack.extend((child, d + 1) for child in reversed(node.get('children', [])))
    return lines


def _write_hierarchy_txt(txt_file: Path, document_uid: str, hierarchy: list[dict]) -> None:
    """Write stage 1a's .txt companion next to its JSON."""
    with open(txt_file, 'w', encoding='utf-8') as f:
        f.write(f"CV Hierarchy: {document_uid}\n")
        f.write("=" * _BANNER_WIDTH + "\n\n")
        for line in _hierarchy_lines(hierarchy):
            f.write(line + "\n")


def _stage_1a(ctx: PipelineContext) -> StageResult:
    """Segmentation: the CV's header hierarchy, from the docx."""
    _banner("STAGE 1A: HIERARCHY EXTRACTION")

    hierarchy, stats = get_cv_hierarchy_chunked(cv_path=str(ctx.cv_path))

    output_dir = _OUTPUTS_ROOT / _STAGE_1A_DIRNAME
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"{ctx.document_uid}_segmented.json"
    total_headers = _count_headers(hierarchy)

    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({'document_uid': ctx.document_uid,
                   'hierarchy': hierarchy,
                   'meta': stats}, f, indent=2)
    _write_hierarchy_txt(output_file.with_suffix('.txt'), ctx.document_uid, hierarchy)

    cost = stats.get('extraction_cost', 0)
    logger.info("")
    logger.info("Stage 1a Complete")
    logger.info("  Output: %s", output_file)
    logger.info("  Top-level sections: %s", len(hierarchy))
    logger.info("  Total headers: %s", total_headers)
    logger.info("  Cost: $%.4f", cost)
    return StageResult(stage='1a', output_file=str(output_file), cost=cost,
                       stats={'num_sections': len(hierarchy),
                              'total_headers': total_headers,
                              'meta': stats})


def _stage_1b(ctx: PipelineContext) -> StageResult:
    """Hierarchy mapping: header nodes to docx element indices. No LLM."""
    _banner("STAGE 1B: HIERARCHY MAPPING (NO LLM)")

    hierarchy_json_path = ctx.best_input('1a')
    if not hierarchy_json_path:
        return _skipped('1b', _requirement_label(STAGE_DEPENDENCIES['1b']))

    data, path = run_stage_1b(docx_path=str(ctx.cv_path),
                              hierarchy_json_path=hierarchy_json_path)
    logger.info("")
    logger.info("Stage 1b Complete")
    logger.info("  Output: %s", path)
    logger.info("  Total sections: %s", data['meta']['total_sections'])
    logger.info("  Leaf sections: %s", data['meta']['leaf_sections'])
    return StageResult(stage='1b', output_file=str(path),
                       stats={'total_sections': data['meta']['total_sections'],
                              'leaf_sections': data['meta']['leaf_sections']})


def _stage_2(ctx: PipelineContext) -> StageResult:
    """Entry extraction: entries and their text, per leaf section."""
    _banner("STAGE 2: ENTRY EXTRACTION")

    hierarchy_json_path = ctx.best_input('1b')
    if not hierarchy_json_path:
        return _skipped('2', _requirement_label(STAGE_DEPENDENCIES['2']))

    data, path = run_stage_2(docx_path=str(ctx.cv_path),
                             hierarchy_json_path=str(hierarchy_json_path))
    cost = data.get('total_cost', 0)
    entries_found = data.get('total_entries', 0)
    logger.info("")
    logger.info("Stage 2 Complete")
    logger.info("  Output: %s", path)
    logger.info("  Entries extracted: %s", entries_found)
    logger.info("  Cost: $%.4f", cost)
    return StageResult(stage='2', output_file=str(path), cost=cost,
                       stats={'total_entries': entries_found})


def _stage_3a(ctx: PipelineContext) -> StageResult:
    """Header taxonomy mapping: CV headers to taxonomy codes."""
    _banner("STAGE 3A: HEADER TAXONOMY MAPPING")

    # run_stage_3a resolves stage 1a's file itself, from document_uid; the guard
    # is still the pipeline's, so a failed 1a skips 3a instead of erroring in it.
    if not ctx.best_input('1a'):
        return _skipped('3a', _requirement_label(STAGE_DEPENDENCIES['3a']))

    result = run_stage_3a(document_uid=ctx.document_uid)
    cost = result['stats']['cost']
    logger.info("")
    logger.info("Stage 3a Complete")
    logger.info("  Output: %s", result['output_path'])
    logger.info("  Header nodes mapped: %s", result['node_count'])
    logger.info("  Cost: $%.4f", cost)
    return StageResult(stage='3a', output_file=str(result['output_path']), cost=cost,
                       stats={'node_count': result['node_count']})


def _stage_3b(ctx: PipelineContext) -> StageResult:
    """Entry classification: taxonomy code per entry, from header + content."""
    _banner("STAGE 3B: ENTRY CLASSIFICATION")

    missing = tuple(stage for stage in STAGE_DEPENDENCIES['3b'] if not ctx.best_input(stage))
    if missing:
        return _skipped('3b', _requirement_label(missing, all_required=True))

    result = run_stage_3b(document_uid=ctx.document_uid,
                          stage_3a_path=ctx.best_input('3a'))
    cost = result['stats']['cost']
    # #810 decision 4: `total_entries` counts every classified entry
    # REGARDLESS of whether it got a real code or fell back to a default one
    # on an LLM failure -- summary.tsv's `classified` column was silently
    # counting fallbacks as classifications (web30: 510 of 1019 entries
    # defaulted, still read `cls=1019`). `llm_classified` is entries that
    # actually received a real code; `fallback_entries` is the rest. The
    # 'Entries classified:' literal is unchanged -- it is a parsed contract
    # (run_corpus_batch.sh, test_run_full_pipeline_stdout_contract.py) -- only
    # the VALUE logged changes, and a new 'Entries defaulted:' line is added.
    llm_classified = result['stats']['llm_classified']
    fallback_entries = result['stats']['fallback_entries']
    logger.info("")
    logger.info("Stage 3b Complete")
    logger.info("  Output: %s", result['output_path'])
    logger.info("  Entries classified: %s", llm_classified)
    logger.info("  Entries defaulted: %s", fallback_entries)
    logger.info("  Cost: $%.4f", cost)
    return StageResult(stage='3b', output_file=str(result['output_path']), cost=cost,
                       stats={'entries_classified': llm_classified,
                              'fallback_entries': fallback_entries,
                              'code_distribution': result.get('code_distribution', {})})


def _stage_4(ctx: PipelineContext) -> StageResult:
    """Field extraction: structured fields per classified entry."""
    _banner("STAGE 4: FIELD EXTRACTION")

    if not ctx.best_input('3b'):
        return _skipped('4', _requirement_label(STAGE_DEPENDENCIES['4']))

    # The resolved path, not a reconstructed f"{uid}.docx": process_cv reads only
    # Path(docx_path).stem (stage_4_field_extractor.py:104-106) to locate stage
    # 3b's output, and PipelineContext guarantees that stem == document_uid, so
    # this carries the same identity without the working-directory coupling.
    result = run_stage_4(docx_path=str(ctx.cv_path))
    output = result['output']
    cost = output.get('total_cost', 0)
    logger.info("")
    logger.info("Stage 4 Complete")
    logger.info("  Output: %s", result['output_path'])
    logger.info("  Entries with fields: %s", output.get('stats', {}).get('extracted', 0))
    logger.info("  Cost: $%.4f", cost)
    return StageResult(stage='4', output_file=str(result['output_path']), cost=cost,
                       stats={'entries_extracted': output.get('total_entries', 0),
                              'extraction_stats': output.get('stats', {})})


def _stage_4_5(ctx: PipelineContext) -> StageResult:
    """Research summary: biosketch-style M1 paragraph."""
    _banner("STAGE 4.5: RESEARCH SUMMARY GENERATION")

    input_path = ctx.best_input(*STAGE_INPUT_PREFERENCE['4.5'])
    if not input_path:
        return _skipped('4.5', _requirement_label(STAGE_INPUT_PREFERENCE['4.5']))

    output_path = run_stage_4_5(input_path=input_path, verbose=True)
    with open(output_path, encoding='utf-8') as f:
        data = json.load(f)
    info = data.get('research_summary', {})
    logger.info("")
    logger.info("Stage 4.5 Complete")
    logger.info("  Output: %s", output_path)
    logger.info("  Method: %s", info.get('method', 'unknown'))
    logger.info("  M1 Score: %.2f", info.get('m1_score', 0))
    logger.info("  Summary length: %s chars", info.get('summary_length', 0))
    return StageResult(stage='4.5', output_file=str(output_path),
                       cost=data.get('total_cost', 0.0),
                       stats={'method': info.get('method', 'unknown'),
                              'm1_score': info.get('m1_score', 0),
                              'summary_length': info.get('summary_length', 0)})


def _stage_5(ctx: PipelineContext) -> StageResult:
    """PubMed enrichment: publication metadata for matched citations."""
    _banner("STAGE 5: PUBMED ENRICHMENT")

    input_path = ctx.best_input(*STAGE_INPUT_PREFERENCE['5'])
    if not input_path:
        return _skipped('5', _requirement_label(STAGE_INPUT_PREFERENCE['5']))

    result = run_stage5(stage4_path=input_path, verbose=True)
    # The stage's own answer, not a path rebuilt here from the uid: rebuilding
    # reported an artifact that stage 5 had not necessarily written.
    output_path = result['output_path']
    logger.info("")
    logger.info("Stage 5 Complete")
    logger.info("  Output: %s", output_path)
    return StageResult(stage='5', output_file=str(output_path))


def _read_stage_5b_cost(output_path: str) -> float | None:
    """Stage 5b's own reported cost, or None when its output cannot be read.

    None is not 0.0: #489 is the incident where a corrupt or missing stage 5b
    output was swallowed and reported as a genuine $0.00.
    """
    try:
        with open(output_path, encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("Could not read stage 5b cost from %s: %s: %s",
                       output_path, type(e).__name__, e)
        return None
    return data.get('institution_enrichment_stats', {}).get('cost', 0.0)


def _read_stage_cost(output_path: str, stage: str, section: str) -> float | None:
    """A formatter stage's own reported cost, or None when its output cannot be read.

    Stages 5c and 5d record ``total_cost`` under a section key of their output
    JSON. None is not 0.0, for the same reason as stage 5b's (#489).
    """
    try:
        with open(output_path, encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError) as e:  # unreadable file, or JSON that does not parse
        logger.warning("Could not read stage %s cost from %s: %s: %s",
                       stage, output_path, type(e).__name__, e)
        return None
    return data.get(section, {}).get('total_cost', 0.0)


def _stage_5b(ctx: PipelineContext) -> StageResult:
    """Institution enrichment: city/state on institution-bearing entries."""
    _banner("STAGE 5B: INSTITUTION ENRICHMENT")

    input_path = ctx.best_input(*STAGE_INPUT_PREFERENCE['5b'])
    if not input_path:
        return _skipped('5b', _requirement_label(STAGE_INPUT_PREFERENCE['5b']))

    output_path = run_stage5b(input_path=input_path, verbose=True)
    cost = _read_stage_5b_cost(output_path)
    logger.info("")
    logger.info("Stage 5b Complete")
    logger.info("  Output: %s", output_path)
    if cost is None:
        logger.info("  Cost: unknown (failed to read institution enrichment stats)")
    elif cost > 0:
        logger.info("  Cost: $%.4f", cost)
    return StageResult(stage='5b', output_file=str(output_path), cost=cost)


def _stage_5c(ctx: PipelineContext) -> StageResult:
    """Teaching formatter: readable K-code entries."""
    _banner("STAGE 5C: TEACHING FORMATTER")

    input_path = ctx.best_input(*STAGE_INPUT_PREFERENCE['5c'])
    if not input_path:
        return _skipped('5c', _requirement_label(STAGE_INPUT_PREFERENCE['5c']))

    output_path = run_stage_5c(input_path=input_path, verbose=True)
    logger.info("")
    logger.info("Stage 5c Complete")
    logger.info("  Output: %s", output_path)
    return StageResult(stage='5c', output_file=str(output_path),
                       cost=_read_stage_cost(output_path, '5c', 'stage_5c'))


def _stage_5d(ctx: PipelineContext) -> StageResult:
    """Citation formatter: Vancouver format for non-enriched citations."""
    _banner("STAGE 5D: CITATION FORMATTER (NON-ENRICHED)")

    input_path = ctx.best_input(*STAGE_INPUT_PREFERENCE['5d'])
    if not input_path:
        return _skipped('5d', _requirement_label(STAGE_INPUT_PREFERENCE['5d']))

    output_path = run_stage_5d(input_path=input_path, verbose=True)
    logger.info("")
    logger.info("Stage 5d Complete")
    logger.info("  Output: %s", output_path)
    return StageResult(stage='5d', output_file=str(output_path),
                       cost=_read_stage_cost(output_path, '5d', 'stage_5d'))


def _stage_6(ctx: PipelineContext) -> StageResult:
    """WCM Word template: the deliverable document."""
    _banner("STAGE 6: WCM WORD TEMPLATE GENERATION")

    input_path = ctx.best_input(*STAGE_INPUT_PREFERENCE['6'])
    if not input_path:
        return _skipped('6', _requirement_label(STAGE_INPUT_PREFERENCE['6']))

    # #550: the personal-data fallback reopens the source .docx; hand it the
    # resolved path instead of leaving stage 6 to guess from SAMPLE_CV_DIR
    # (which only resolves for a uid-style run from the repo root). A
    # standalone --stage 6 rerun without the .docx on disk is still fine:
    # the fallback checks is_file() and skips.
    usage = LlmUsage()
    output_path = run_stage6(input_path=input_path, verbose=True,
                             original_doc_path=str(ctx.cv_path), llm_usage=usage,
                             # #1389: off unless "1". The env var only: the web driver's
                             # get_config also reads auth_config.yaml's "repair" section,
                             # and that loader is backend code this CLI does not import.
                             repair_protected_data=repair_flag_on(os.environ.get(REPAIR_FLAG_ENV)))
    logger.info("")
    logger.info("Stage 6 Complete")
    logger.info("  Output: %s", output_path)
    return StageResult(stage='6', output_file=str(output_path), cost=usage.cost)


_STAGE_RUNNERS: dict[str, Callable[[PipelineContext], StageResult]] = {
    '1a': _stage_1a,
    '1b': _stage_1b,
    '2': _stage_2,
    '3a': _stage_3a,
    '3b': _stage_3b,
    '4': _stage_4,
    '4.5': _stage_4_5,
    '5': _stage_5,
    '5b': _stage_5b,
    '5c': _stage_5c,
    '5d': _stage_5d,
    '6': _stage_6,
}


def _load_existing_results(ctx: PipelineContext) -> None:
    """In ``--stage`` mode, record earlier stages' artifacts already on disk.

    They are recorded for the summary only -- never in ``ctx.outputs`` -- so a
    full run can never take an input it did not itself produce.
    """
    if ctx.target_stage is None:
        return
    expected = get_output_paths(ctx.document_uid)
    if not ctx.should_run('1a') and expected['1a'].is_file():
        with open(expected['1a'], encoding='utf-8') as f:
            hierarchy = json.load(f).get('hierarchy', [])
        ctx.results['stage_1a'] = StageResult(
            stage='1a', output_file=str(expected['1a']),
            stats={'num_sections': len(hierarchy),
                   'total_headers': _count_headers(hierarchy)})
    for stage in ('1b', '2', '3a'):
        if not ctx.should_run(stage) and expected[stage].is_file():
            ctx.results[f'stage_{stage}'] = StageResult(
                stage=stage, output_file=str(expected[stage]))


def run_pipeline(ctx: PipelineContext) -> PipelineResult:
    """Run every stage this context selects, in order, through run_stage()."""
    start = time.perf_counter()
    _load_existing_results(ctx)
    for stage in get_stage_order():
        if stage == _COMPOSITE_STAGE:
            continue  # an alias for 3a+3b, not a runner of its own
        if ctx.should_run(stage):
            run_stage(ctx, stage, _STAGE_RUNNERS[stage])
    return PipelineResult(results=dict(ctx.results),
                          total_duration_seconds=time.perf_counter() - start,
                          failed=failed_stages(ctx.results))


# Stage label -> the padded prefix the summary prints it under. Padding is part
# of the output, so it lives with the label rather than being recomputed.
_SUMMARY_LABELS: dict[str, str] = {
    '1a': 'Stage 1a:',
    '1b': 'Stage 1b:',
    '2': 'Stage 2: ',
    '3a': 'Stage 3a:',
    '3b': 'Stage 3b:',
    '4': 'Stage 4: ',
    '4.5': 'Stage 4.5:',
    '5': 'Stage 5: ',
    '5b': 'Stage 5b:',
    '5c': 'Stage 5c:',
    '5d': 'Stage 5d:',
    '6': 'Stage 6: ',
}

# Every stage that calls call_llm, in pipeline order (#1177).
# tests/test_run_full_pipeline_exit_status.py fails when a stage module that
# imports call_llm is missing from here. Stage 5b is printed specially: its
# cost can be unknown (#489), which is not the same as zero.
_COST_REPORTING_STAGES = ('1a', '2', '3a', '3b', '4', '4.5', '5b', '5c', '5d', '6')
_CODE_DISTRIBUTION_LIMIT = 10


def _print_outputs(result: PipelineResult) -> None:
    logger.info("Outputs:")
    for stage, label in _SUMMARY_LABELS.items():
        stage_result = result.results.get(f'stage_{stage}')
        if stage_result is not None and stage_result.output_file:
            logger.info("  %s %s", label, stage_result.output_file)
    logger.info("")


def _print_timing(result: PipelineResult) -> None:
    logger.info("Timing:")
    for stage, label in _SUMMARY_LABELS.items():
        stage_result = result.results.get(f'stage_{stage}')
        if (stage_result is not None and stage_result.succeeded
                and stage_result.duration_seconds > 0):
            logger.info("  %s %s", label, format_duration(stage_result.duration_seconds))
    logger.info("  Total:    %s", format_duration(result.total_duration_seconds))
    logger.info("")


def _print_stage_5b_cost(stage_5b: StageResult) -> None:
    if stage_5b.cost is None:
        logger.info("  Stage 5b: unknown (institution enrichment stats unreadable)")
    elif stage_5b.cost > 0:
        logger.info("  %s $%.4f", _SUMMARY_LABELS['5b'], stage_5b.cost)


def _print_costs(result: PipelineResult) -> None:
    logger.info("Costs:")
    for stage in _COST_REPORTING_STAGES:
        stage_result = result.results.get(f'stage_{stage}')
        if stage_result is None or not stage_result.succeeded:
            continue
        if stage == '5b':
            _print_stage_5b_cost(stage_result)
        elif stage_result.cost is not None:
            logger.info("  %s $%.4f", _SUMMARY_LABELS[stage], stage_result.cost)
        else:
            logger.info("  Stage %s: unknown (output cost unreadable)", stage)
    logger.info("  Total:    $%.4f", result.total_cost)
    logger.info("")


def _print_processing_stats(result: PipelineResult) -> None:
    logger.info("Processing Stats:")
    stage_1a = result.results.get('stage_1a')
    if stage_1a is not None and 'num_sections' in stage_1a.stats:
        logger.info("  Top-level sections: %s", stage_1a.stats['num_sections'])
        logger.info("  Total headers: %s", stage_1a.stats['total_headers'])
    stage_2 = result.results.get('stage_2')
    if stage_2 is not None and 'total_entries' in stage_2.stats:
        logger.info("  Entries extracted: %s", stage_2.stats['total_entries'])
    stage_3a = result.results.get('stage_3a')
    if stage_3a is not None and 'node_count' in stage_3a.stats:
        logger.info("  Header nodes mapped: %s", stage_3a.stats['node_count'])
    stage_3b = result.results.get('stage_3b')
    if stage_3b is not None and 'entries_classified' in stage_3b.stats:
        # This is the occurrence `run_corpus_batch.sh`'s `tail -1` actually
        # reads (see the module docstring on print_summary below) -- the
        # per-stage narration in _stage_3b() logs the same two literals
        # first, but this later copy is the one summary.tsv's `classified`/
        # `defaulted` columns come from.
        logger.info("  Entries classified: %s", stage_3b.stats['entries_classified'])
        logger.info("  Entries defaulted: %s", stage_3b.stats.get('fallback_entries', 0))
    stage_4 = result.results.get('stage_4')
    if stage_4 is not None and 'entries_extracted' in stage_4.stats:
        logger.info("  Fields extracted: %s", stage_4.stats['entries_extracted'])
    _print_code_distribution(stage_3b)
    logger.info("")


def _print_code_distribution(stage_3b: StageResult | None) -> None:
    if stage_3b is None:
        return
    code_dist = stage_3b.stats.get('code_distribution') or {}
    if not code_dist:
        return
    logger.info("")
    logger.info("Code Distribution (top %s):", _CODE_DISTRIBUTION_LIMIT)
    ranked = sorted(code_dist.items(), key=lambda item: -item[1])[:_CODE_DISTRIBUTION_LIMIT]
    for code, count in ranked:
        logger.info("  %s: %s", code, count)


def print_summary(result: PipelineResult, ctx: PipelineContext) -> None:
    """The run's operator-facing report.

    Five of its lines are a cross-process contract, not decoration:
    ``scripts/run_corpus_batch.sh:152-163`` greps ``Top-level sections:``,
    ``Total headers:``, ``Entries extracted:``, ``Entries classified:``,
    ``Entries defaulted:`` (#810) and an anchored ``^Models: `` out of this
    stdout into ``summary.tsv``. Pinned by
    ``src/unified_pipeline/tests/test_run_full_pipeline_stdout_contract.py``.
    """
    logger.info("=" * _BANNER_WIDTH)
    logger.info("%s", "PIPELINE COMPLETE WITH ERRORS" if result.failed else "PIPELINE COMPLETE")
    logger.info("=" * _BANNER_WIDTH)
    logger.info("Document: %s", ctx.document_uid)
    logger.info("Models: %s", format_models_used())
    logger.info("")
    if result.failed:
        logger.info("Failed stages:")
        for name in result.failed:
            stage_result = result.results[name]
            reason = (stage_result.error or stage_result.skipped_reason
                      or 'produced no output document')
            logger.info("  %s: %s", name, reason)
        logger.info("")
    _print_outputs(result)
    _print_timing(result)
    _print_costs(result)
    _print_processing_stats(result)


def _print_usage() -> None:
    logger.info("Usage: python3 run_full_pipeline.py <cv_path_or_uid> [--stage STAGE]")
    logger.info("")
    logger.info("Arguments:")
    logger.info("  cv_path_or_uid : Path to Word document OR just the document UID")
    logger.info("                   (if UID only, looks in data/sample_cvs/word/)")
    logger.info("  --stage STAGE  : Run ONLY this stage: '1a', '1b', '2', '3a', '3b', '3', or '4'")
    logger.info("                   (omit for full pipeline)")
    logger.info("")
    logger.info("Examples:")
    logger.info("  # Full pipeline")
    logger.info("  python3 run_full_pipeline.py 2097_Upton_Cv")
    logger.info("")
    logger.info("  # Run only Stage 2")
    logger.info("  python3 run_full_pipeline.py 2097_Upton_Cv --stage 2")
    logger.info("")
    logger.info("  # Run only Stage 3a (header taxonomy mapping)")
    logger.info("  python3 run_full_pipeline.py 2097_Upton_Cv --stage 3a")
    logger.info("")
    logger.info("  # Run only Stage 3b (entry classification)")
    logger.info("  python3 run_full_pipeline.py 2097_Upton_Cv --stage 3b")
    logger.info("")
    logger.info("  # Run both 3a and 3b")
    logger.info("  python3 run_full_pipeline.py 2097_Upton_Cv --stage 3")
    logger.info("")
    logger.info("  # Run only Stage 4 (field extraction)")
    logger.info("  python3 run_full_pipeline.py 2097_Upton_Cv --stage 4")


def parse_args(argv: list[str]) -> tuple[str, str | None]:
    """(cv_path_or_uid, target_stage). Exits 1 on a missing or invalid argument."""
    if len(argv) < 2:
        _print_usage()
        sys.exit(1)

    target_stage = None  # None = run all stages; otherwise run only that stage
    valid_stages = get_stage_order()
    i = 2  # Start after the cv_path_or_uid argument
    while i < len(argv):
        arg = argv[i]
        if arg == "--stage" and i + 1 < len(argv):
            target_stage = argv[i + 1]
            if target_stage not in valid_stages:
                logger.error("Error: Invalid stage '%s'. Use one of: %s",
                             target_stage, ', '.join(valid_stages))
                sys.exit(1)
            i += 2
        else:
            i += 1
    return argv[1], target_stage


def _exit_if_prerequisites_missing(target_stage: str, document_uid: str) -> None:
    ok, missing_file, required_stage = check_prerequisites(target_stage, document_uid)
    if ok:
        return
    logger.error("Error: Cannot run stage %s", target_stage)
    logger.error("  Missing prerequisite: Stage %s output", required_stage)
    logger.error("  Expected file: %s", missing_file)
    logger.error("  Run the prerequisite stage first, or run without --stage for full pipeline.")
    sys.exit(1)


def _cv_is_missing(cv_path: str, target_stage: str | None) -> bool:
    """Report an unresolvable first argument as what it is.

    resolve_cv_path() hands back whatever it was given when nothing matched, so
    a typo or an unsupported extension used to travel all the way into the
    context and surface as a stem/uid mismatch -- an accurate message about the
    wrong thing. The stem/uid guard stays for the case it is actually about.

    Scoped to the runs that actually open the document: a full run, or a
    standalone _STAGES_READING_THE_DOCX one. `--stage 5b` on last week's stage-4
    artifact is a legitimate rerun and must not need the .docx back.
    """
    if target_stage is not None and target_stage not in _STAGES_READING_THE_DOCX:
        return False
    if Path(cv_path).is_file():
        return False
    logger.error("Error: CV not found: %s", cv_path)
    logger.error("  Give a path to a .docx, or a document UID present in "
                 "data/sample_cvs/word/.")
    return True


@_scope_prompt_logger_per_run
def main() -> int:
    cv_path_or_uid, target_stage = parse_args(sys.argv)
    cv_path, document_uid = resolve_cv_path_for_run(cv_path_or_uid)

    if _cv_is_missing(cv_path, target_stage):
        return 1

    if target_stage and target_stage != "1a":
        _exit_if_prerequisites_missing(target_stage, document_uid)

    logger.info("=" * _BANNER_WIDTH)
    if target_stage:
        logger.info("CV PROCESSING PIPELINE - STAGE %s ONLY", target_stage.upper())
    else:
        logger.info("CV PROCESSING PIPELINE (V15)")
    logger.info("=" * _BANNER_WIDTH)
    logger.info("Input: %s", cv_path)
    logger.info("Document UID: %s", document_uid)
    logger.info("")

    ctx = PipelineContext(cv_path=Path(cv_path), document_uid=document_uid,
                          target_stage=target_stage)
    result = run_pipeline(ctx)
    print_summary(result, ctx)

    # Continue-on-error is kept deliberately -- the partial stage artifacts are
    # worth having for diagnosis. What changes is that the run stops claiming
    # success it did not have.
    return result.exit_code


if __name__ == '__main__':
    # Only the CLI entry point configures logging, not a test that imports this
    # module and calls main() directly (it calls configure_cli_logging() itself
    # when it needs the streams). Nothing else in this file or in any non-test
    # src/unified_pipeline module configures a handler, and Python's root logger
    # has only a last-resort WARNING-only one.
    #
    # See configure_cli_logging(): narration and the five lines
    # scripts/run_corpus_batch.sh greps go to stdout as bare messages;
    # WARNING and above, tracebacks included, go to timestamped stderr. Both
    # streams still land in a batch log -- run_corpus_batch.sh:122,124 redirect
    # the run with `> "$log" 2>&1`.
    configure_cli_logging()
    sys.exit(main())
