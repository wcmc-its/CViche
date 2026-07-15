"""
Pipeline orchestrator - coordinates execution of all 12 pipeline stages.

Uses run_full_pipeline.py stage functions directly.
Stages: 1a, 1b, 2, 3a, 3b, 4, 4.5, 5, 5b, 5c, 5d, 6
"""
import json
import logging
import time
import asyncio
import logging
import os
import re
import shutil
import traceback
import threading
import io
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Add parent project to path to import existing pipeline code
import sys
PARENT_DIR = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(PARENT_DIR))
sys.path.insert(0, str(PARENT_DIR / 'src'))

from app.models import Run, Step, Log
from app.pipeline.step_registry import STEP_REGISTRY, get_step_by_stage_id
from app.pipeline.event_emitter import event_emitter
from app.storage import get_storage
from app.config_loader import get_config

logger = logging.getLogger(__name__)

# The pipeline's prompt_logger writes per-LLM-call transcripts here. We
# replicate fresh files into per-run storage so they survive container
# restarts and replica scale-up.
PROMPT_LOGS_DIR = PARENT_DIR / 'src' / 'unified_pipeline' / 'prompt_logs'

# Import stage functions from run_full_pipeline.py dependencies
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


# Cancellation tracking. The in-process set covers same-worker cancels (and is
# the only mechanism when no Redis broker is configured). When a broker is
# enabled, cancels also round-trip through Redis so a cancel received by one
# worker/replica reaches the worker actually running the pipeline. The broker's
# cancel ops use a sync client, so is_cancelled() stays synchronous and the
# stage-boundary check (check_cancelled) needs no async change.
_cancelled_runs: set = set()
_broker = None


def set_broker(broker):
    """Attach the Redis broker (called at app startup)."""
    global _broker
    _broker = broker


def cancel_run(run_id: str):
    """Signal a run to be cancelled."""
    _cancelled_runs.add(run_id)
    if _broker is not None and _broker.enabled:
        _broker.request_cancel(run_id)


def is_cancelled(run_id: str) -> bool:
    """Check if a run has been cancelled (locally or via the broker)."""
    if run_id in _cancelled_runs:
        return True
    if _broker is not None and _broker.enabled:
        return _broker.is_cancelled(run_id)
    return False


def clear_cancelled(run_id: str):
    """Clear cancellation flag for a run."""
    _cancelled_runs.discard(run_id)
    if _broker is not None and _broker.enabled:
        _broker.clear_cancel(run_id)


class CancelledException(Exception):
    """Exception raised when a pipeline run is cancelled."""
    pass


def _get_stage_timeout_seconds() -> int:
    """Per-stage wall-clock ceiling, in seconds (0 disables it).

    A coarse last-resort backstop: if a stage runs longer than this, the
    orchestrator abandons the await, marks the run failed, and emits
    RUN_FAILED -- so a hung stage can never leave the run pinned at
    "running" forever (the bug where the UI timer counted up indefinitely
    after a silent Stage 4 hang). It is intentionally generous so a large
    CV doing a lot of legitimate work is never clipped; the precise net
    against a single wedged provider call is the per-call timeout on the
    LLM client (see unified_pipeline/llm_client.py). asyncio.wait_for can't
    kill the worker thread the stage runs in, so the LLM-call timeout is
    what actually lets that thread unwind -- this only bounds how long the
    *user* is left waiting. Tune via CVICHE_STAGE_TIMEOUT_SECONDS.
    """
    try:
        #value = int(os.environ.get("CVICHE_STAGE_TIMEOUT_SECONDS", 1800))
        timeout_seconds,_ = get_config("llm","CVICHE_STAGE_TIMEOUT_SECONDS",default=1800)
        value = int(timeout_seconds) 
    except (TypeError, ValueError):
        return 1800
    return value if value >= 0 else 1800


# Progress patterns to detect in stdout
PROGRESS_PATTERNS = [
    # "Processing section 5 of 10" or "Processing 5/10"
    re.compile(r'(?:Processing|Extracting|Mapping|Classifying|Enriching)\s+(?:section\s+)?(\d+)\s*(?:of|/)\s*(\d+)', re.IGNORECASE),
    # "Section 5/10" or "Entry 5/10"
    re.compile(r'(?:Section|Entry|Item|Chunk|Node|Header|Publication|Grant|Position)\s*(\d+)\s*(?:of|/)\s*(\d+)', re.IGNORECASE),
    # "[5/10]" format
    re.compile(r'\[(\d+)\s*/\s*(\d+)\]'),
    # "5 of 10 sections" or "5 of 10 entries"
    re.compile(r'(\d+)\s+of\s+(\d+)\s+(?:sections?|entries?|items?|chunks?|nodes?|headers?|publications?|grants?|positions?)', re.IGNORECASE),
]


class StreamingStdoutCapture:
    """Captures stdout and streams lines to database in real-time.

    Uses asyncio.run_coroutine_threadsafe to safely call async log methods
    from the synchronous context where stage functions run.
    """

    def __init__(self, orchestrator, step_number: int, event_loop: asyncio.AbstractEventLoop):
        self.orchestrator = orchestrator
        self.step_number = step_number
        self.event_loop = event_loop
        self.buffer = ""
        self.captured_lines = []  # Also collect for backup
        self._original_stdout = sys.__stdout__
        self._last_progress = (0, 0)  # Track last progress to avoid duplicates

    def _detect_progress(self, line: str) -> tuple:
        """Detect progress patterns in a line and return (current, total) or None."""
        for pattern in PROGRESS_PATTERNS:
            match = pattern.search(line)
            if match:
                try:
                    current = int(match.group(1))
                    total = int(match.group(2))
                    if 0 < current <= total:
                        return (current, total)
                except (ValueError, IndexError):
                    pass
        return None

    def _emit_log_sync(self, line: str):
        """Emit a log line synchronously by scheduling it on the event loop."""
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.orchestrator.log(self.step_number, line, "INFO"),
                self.event_loop
            )
            # Wait briefly for the log to be stored (but don't block too long)
            future.result(timeout=2.0)
        except Exception as e:
            # If async logging fails, at least print to stderr
            if self._original_stdout:
                self._original_stdout.write(f"[Log emit error: {e}]\n")

    def _emit_progress_sync(self, current: int, total: int, message: str = ""):
        """Emit progress update synchronously."""
        try:
            future = asyncio.run_coroutine_threadsafe(
                self.orchestrator.update_progress(self.step_number, current, total, message),
                self.event_loop
            )
            future.result(timeout=1.0)
        except Exception:
            pass  # Progress updates are less critical

    def write(self, text: str):
        """Capture stdout writes and stream them to the database."""
        if text:
            # Also write to original stdout for debugging
            if self._original_stdout:
                self._original_stdout.write(text)
                self._original_stdout.flush()

            # Accumulate text and process complete lines
            self.buffer += text
            while '\n' in self.buffer:
                line, self.buffer = self.buffer.split('\n', 1)
                line = line.strip()
                if line:
                    self.captured_lines.append(line)
                    # Stream to database in real-time
                    self._emit_log_sync(line)
                    # Check for progress patterns
                    progress = self._detect_progress(line)
                    if progress and progress != self._last_progress:
                        self._last_progress = progress
                        self._emit_progress_sync(progress[0], progress[1], line)
        return len(text) if text else 0

    def flush(self):
        """Flush any remaining buffer content."""
        if self.buffer.strip():
            line = self.buffer.strip()
            self.captured_lines.append(line)
            self._emit_log_sync(line)
            self.buffer = ""
        if self._original_stdout:
            self._original_stdout.flush()

    def get_captured_lines(self) -> list:
        """Return all captured lines."""
        # Flush any remaining content
        self.flush()
        return self.captured_lines


class PipelineOrchestrator:
    """Orchestrates the execution of all 12 pipeline stages."""

    def __init__(self, run_id: str, file_path: Path, db: Session):
        self.run_id = run_id
        self.file_path = file_path
        self.db = db

        # Output directory for this run (in the unified_pipeline outputs)
        self.pipeline_output_dir = PARENT_DIR / 'src' / 'unified_pipeline' / 'outputs'

        # Web interface output directory (for tracking)
        self.web_output_dir = Path(__file__).parent.parent.parent.parent / "outputs" / run_id
        self.web_output_dir.mkdir(parents=True, exist_ok=True)

        # Document UID extracted from filename
        self.document_uid = Path(file_path).stem

        # Track outputs between stages
        self.stage_outputs: Dict[str, str] = {}

        # Total cost tracking
        self.total_cost = 0.0

        # Step number of the stage that raised, so the run-level failure handler
        # can attribute RUN_FAILED to the stage the user was watching.
        self.failed_step_number: Optional[int] = None

    async def log(self, step_number: int, message: str, level: str = "INFO"):
        """Log a message to database and emit via WebSocket."""
        log_entry = Log(run_id=self.run_id, step_number=step_number, level=level, message=message)
        self.db.add(log_entry)
        self.db.commit()
        await event_emitter.emit_log(self.run_id, step_number, message, level)

    async def update_cost(self, step_number: int, cost_delta: float, tokens_delta: int = 0,
                          input_tokens_delta: int = 0, output_tokens_delta: int = 0,
                          cache_read_tokens_delta: int = 0,
                          cache_write_tokens_delta: int = 0,
                          provider: str = "openai"):
        """Update run costs in real-time and emit cost update event.

        cache_read_tokens_delta / cache_write_tokens_delta are subsets of
        input_tokens_delta (Bedrock prompt-caching split), not additions to
        it -- input_tokens already includes the cached portion. Stages that
        don't surface a cache split simply pass 0.
        """
        run = self.db.query(Run).filter(Run.id == self.run_id).first()
        if not run:
            return

        self.total_cost += cost_delta
        run.total_cost = self.total_cost
        run.total_tokens = (run.total_tokens or 0) + tokens_delta
        run.input_tokens = (run.input_tokens or 0) + input_tokens_delta
        run.output_tokens = (run.output_tokens or 0) + output_tokens_delta
        run.cache_read_tokens = (run.cache_read_tokens or 0) + cache_read_tokens_delta
        run.cache_write_tokens = (run.cache_write_tokens or 0) + cache_write_tokens_delta
        self.db.commit()

        await event_emitter.emit_cost_update(
            self.run_id,
            step_number,
            cost_delta,
            run.total_cost,
            tokens_delta,
            run.total_tokens,
            input_tokens_delta,
            output_tokens_delta,
            run.input_tokens,
            run.output_tokens,
            cache_read_tokens_delta=cache_read_tokens_delta,
            cache_write_tokens_delta=cache_write_tokens_delta,
            cache_read_tokens_total=run.cache_read_tokens,
            cache_write_tokens_total=run.cache_write_tokens,
            provider=provider
        )

    async def update_progress(self, step_number: int, current: int, total: int, message: str = ""):
        """Emit progress update within a step."""
        await event_emitter.emit_progress(self.run_id, step_number, current, total, message)

    def check_cancelled(self):
        """Check if this run has been cancelled and raise exception if so."""
        if is_cancelled(self.run_id):
            raise CancelledException(f"Run {self.run_id} was cancelled by user")

    def _copy_to_pipeline_input(self) -> str:
        """Copy uploaded file to pipeline input directory and return the path."""
        # Copy to data/sample_cvs/word/ for the pipeline to find
        input_dir = PARENT_DIR / 'data' / 'sample_cvs' / 'word'
        input_dir.mkdir(parents=True, exist_ok=True)

        dest_path = input_dir / f"{self.document_uid}.docx"
        if not dest_path.exists():
            shutil.copy2(self.file_path, dest_path)

        return str(dest_path)

    def _get_output_paths(self) -> Dict[str, Path]:
        """Get expected output file paths for each stage."""
        base = self.pipeline_output_dir
        return {
            '1a': base / 'stage_1a_segmentation' / f'{self.document_uid}_segmented.json',
            '1b': base / 'stage_1b_hierarchy_mapping' / f'{self.document_uid}_hierarchy_mapped.json',
            '2': base / 'stage_2_entry_extraction' / f'{self.document_uid}_entries.json',
            '3a': base / 'stage_3a_header_mappings' / f'{self.document_uid}_header_taxonomy.json',
            '3b': base / 'stage_3b_classified_entries' / f'{self.document_uid}_classified.json',
            '4': base / 'stage_4_field_extraction' / f'{self.document_uid}_fields.json',
            '4.5': base / 'stage_4_5_research_summary' / f'{self.document_uid}_research_summary.json',
            '5': base / 'stage_5_enrichment' / f'{self.document_uid}_enriched.json',
            '5b': base / 'stage_5b_institution_enrichment' / f'{self.document_uid}_institution_enriched.json',
            '5c': base / 'stage_5c_teaching_formatted' / f'{self.document_uid}_teaching_formatted.json',
            '5d': base / 'stage_5d_citation_formatted' / f'{self.document_uid}_citation_formatted.json',
            '6': base / 'stage_6_wcm_documents' / f'{self.document_uid}_wcm.docx',
        }

    def _persist_outputs_to_storage(self, output_files):
        """Mirror a stage's output files to durable storage (S3 in prod).

        The pipeline writes outputs to the pod's ephemeral filesystem; a pod
        restart/roll wipes them, so downloads for completed runs 404 even
        though the run shows complete (issue #38). When running on S3, copy
        each output up under ``outputs/{basename}`` so the download route can
        serve it via a presigned URL. Keyed by basename to match how the
        download route normalizes the requested filename.

        Best-effort: a storage failure here logs a warning and must never fail
        the run. In local mode this is a no-op (downloads serve from disk).
        """
        from app.config_loader import get_config
        cviche_storage_backend, source = get_config("s3", "CVICHE_STORAGE_BACKEND", default="local")

        if cviche_storage_backend != "s3":
            return

        storage = get_storage()
        for path_str in output_files:
            try:
                path = Path(path_str)
                if not path.is_file():
                    continue
                storage.put_file(self.run_id, f"outputs/{path.name}", path.read_bytes())
            except Exception as e:
                logger.warning(
                    "Failed to mirror output %s to storage for run %s: %s",
                    path_str, self.run_id, e,
                )

    def _prepare_resume(self, run: Run, start_step_number: int) -> int:
        """Prime in-memory state for a resumed run (per-step retry) and return
        the step to actually resume from.

        For each stage completed before ``start_step_number`` it makes that
        stage's output available again: it prefers the pod-local file and falls
        back to durable storage when the local file is gone (e.g. the pod
        recycled since the original run, wiping the ephemeral outputs dir).
        Outputs are mirrored to storage under ``outputs/{basename}`` as each
        stage completes, so this is the read side of that mirror. A registered
        output lets downstream stages resolve their inputs from the in-memory
        path.

        If a prior stage's output can be recovered from neither source, resume
        backs up to recompute from that stage rather than dead-ending a later
        stage (e.g. Stage 6's "No input available"). Worst case -- only the
        durable upload survives -- this returns step 1, i.e. a full recompute.

        Cost accounting continues from the run's existing total. If the resume
        point is backed up, the stages between the new and the originally
        requested point get recomputed and re-add their cost, so their
        already-counted cost is subtracted here to avoid double-counting that
        recomputed suffix.
        """
        output_paths = self._get_output_paths()
        storage = get_storage()

        effective_start = start_step_number
        for step_def in STEP_REGISTRY:
            if step_def.number >= start_step_number:
                break
            raw_path = output_paths.get(step_def.stage_id)
            if raw_path is None:
                continue
            path = Path(raw_path)
            if not path.exists():
                # Local copy gone (pod recycle). Try durable storage.
                try:
                    data = storage.get_file(self.run_id, f"outputs/{path.name}")
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_bytes(data)
                    logger.info(
                        "Rehydrated stage %s output for run %s from storage",
                        step_def.stage_id, self.run_id,
                    )
                except Exception:
                    pass
            if path.exists():
                self.stage_outputs[step_def.stage_id] = str(path)
            else:
                # First unrecoverable gap: recompute from here so the missing
                # input is regenerated instead of failing a downstream stage.
                effective_start = step_def.number
                logger.info(
                    "Resume for run %s backing up to step %d: stage %s output "
                    "unavailable locally and in storage",
                    self.run_id, step_def.number, step_def.stage_id,
                )
                break

        self.total_cost = run.total_cost or 0.0
        if effective_start < start_step_number:
            recomputed = self.db.query(Step).filter(
                Step.run_id == self.run_id,
                Step.step_number >= effective_start,
                Step.step_number < start_step_number,
            ).all()
            self.total_cost -= sum((s.cost or 0.0) for s in recomputed)
        return effective_start

    async def execute(self, start_step_number: Optional[int] = None):
        """Execute the pipeline.

        When ``start_step_number`` is given (a per-step retry), stages before it
        are skipped and their outputs are made available to the resumed stages
        (preferring the pod-local file, falling back to durable storage). If an
        earlier output can't be recovered, ``_prepare_resume`` lowers the resume
        point so the missing stage is recomputed rather than dead-ending a later
        stage; in the worst case the pipeline recomputes from the start.
        """
        run = self.db.query(Run).filter(Run.id == self.run_id).first()
        if not run:
            raise ValueError(f"Run {self.run_id} not found")

        # Initialised before the try so the failure handler can always record a
        # duration even if something throws before the pipeline proper starts.
        start_time = None

        try:
            await event_emitter.emit_run_start(self.run_id)
            start_time = time.time()

            # Only the terminal card is sent to Teams (#154). The run-started
            # card was dropped to halve the notification volume — the terminal
            # card carries the outcome, which is what a watcher actually needs.

            # Copy file to pipeline input directory
            cv_path = self._copy_to_pipeline_input()

            if start_step_number is not None:
                # May lower the resume point if an earlier output is unrecoverable.
                start_step_number = self._prepare_resume(run, start_step_number)

            # Execute all 12 stages (or, on retry, from the failed step onward)
            for step_def in STEP_REGISTRY:
                if start_step_number is not None and step_def.number < start_step_number:
                    continue
                # Check for cancellation before each step
                self.check_cancelled()
                await self.execute_step(step_def.number, step_def.stage_id, cv_path)

            # Final cancellation check after the last stage. A cancel signalled
            # while the final stage was running (the intra-stage checks may have
            # already passed) must not be lost: re-checking here routes it into
            # the CancelledException handler instead of falling through to the
            # "complete" commit below.
            self.check_cancelled()

            # "cancelled" is terminal. The cancel endpoint sets run.status on a
            # *different* DB session/row, so our in-memory ``run`` object can be
            # stale; refreshing it (or re-querying) surfaces a cancel that
            # landed without tripping check_cancelled() above -- e.g. set
            # directly on the row, or by a replica that never populated this
            # worker's in-process flag. Either way we must not flip it back to
            # "complete".
            self.db.refresh(run)
            if run.status == "cancelled" or is_cancelled(self.run_id):
                raise CancelledException(f"Run {self.run_id} was cancelled by user")

            duration = int(time.time() - start_time)
            run.status = "complete"
            run.completed_at = datetime.now()
            # Persist the authoritative pipeline duration (previously only emitted
            # over the WebSocket) so historical conversion-time metrics are queryable.
            run.total_duration_seconds = duration
            run.total_cost = self.total_cost
            self.db.commit()

            await event_emitter.emit_run_complete(self.run_id, run.total_cost, run.total_tokens, duration)

            # Compute & cache the advisory quality score for the admin view.
            # Best-effort, run off the event loop; never affects run status.
            score = None
            try:
                import asyncio
                from app.services.quality_score_service import compute_and_cache_score
                score = await asyncio.get_running_loop().run_in_executor(
                    None, compute_and_cache_score, self.run_id
                )
            except Exception as e:
                logger.warning("Quality score caching failed for run %s: %s", self.run_id, e)

            # Post-run artifact doctor: cross-stage lints over the stage
            # outputs just written (pure local file reads, no LLM). Gated by
            # CVICHE_RUN_DOCTOR and best-effort: the run is already committed
            # complete above, so a doctor problem can never fail the run.
            doctor = None
            try:
                doctor = await self._run_doctor()
            except Exception as e:
                logger.warning("Run doctor failed for run %s: %s", self.run_id, e)
                try:
                    # A failed output_files commit would leave the session in
                    # pending-rollback and strip the submitter off the card.
                    self.db.rollback()
                except Exception:
                    pass

            # Notify on terminal success, passing the freshly-computed score so
            # the Teams message includes it. Fully decoupled and best-effort:
            # notify_run_terminal swallows all failures, so this never affects
            # run status (which is already committed above).
            await self._notify_terminal(run, score, doctor)

        except CancelledException:
            # Run was cancelled - status already updated by API endpoint
            await self.log(0, "Pipeline cancelled by user", "WARNING")
            await event_emitter.emit(self.run_id, {"event": "RUN_CANCELLED"})

        except Exception as e:
            run.status = "failed"
            # On a resume (per-step retry), a missing input file means an earlier
            # stage's output is no longer on disk -- e.g. the pod recycled since
            # the original run. Surface a clear next step instead of leaking a raw
            # filesystem path + errno to the (non-technical) user.
            err_text = str(e).lower()
            is_missing_input = (
                isinstance(e, FileNotFoundError)
                or "no such file or directory" in err_text
                or "no input available" in err_text
            )
            if start_step_number is not None and is_missing_input:
                run.error_message = (
                    "Couldn't resume: earlier pipeline results are no longer "
                    "available (the server may have restarted since this run). "
                    'Please use "Restart with this file" to run it from the beginning.'
                )
            else:
                run.error_message = str(e)
            run.completed_at = datetime.now()
            # Record time-to-failure too -- useful when diagnosing a run that was
            # "taking too long" and then errored out.
            if start_time is not None:
                run.total_duration_seconds = int(time.time() - start_time)
            self.db.commit()
            await self.log(0, f"Pipeline failed: {str(e)}", "ERROR")
            # Authoritative terminal failure signal. Emit the user-facing
            # message (run.error_message), not the raw exception, so a
            # resume-input failure surfaces its actionable guidance. Mirrors
            # RUN_COMPLETE / RUN_CANCELLED so the UI stops the timer and
            # switches to the failure state without waiting for a status poll.
            await event_emitter.emit_run_failed(
                self.run_id, run.error_message or str(e), self.failed_step_number
            )

            # Notify on terminal failure too (failures are the most important to
            # push). No score is computed on the failure path; pass a best-effort
            # cached read (usually None) and let the payload render "n/a".
            await self._notify_terminal(run, self._cached_score())

            raise

        finally:
            # Clean up cancellation flag
            clear_cancelled(self.run_id)

    def _cached_score(self):
        """Best-effort read of the run's cached quality score (or None).

        Used on the failure path, where no score is computed; the notification
        renders "n/a" when this is None.
        """
        try:
            from app.services.quality_score_service import get_cached_score
            return get_cached_score(self.run_id)
        except Exception:  # pragma: no cover - defensive
            return None

    def _submitter_label(self, run):
        """Best-effort display name (or email) of the run's submitter, or None.

        Resolved here, on the orchestrator's thread, and passed down as a plain
        string: Run.user is lazy="raise_on_sql" (so run.user would raise), and
        the notification POST runs in another thread where the session must not
        be touched.
        """
        try:
            if not getattr(run, "user_id", None):
                return None
            from app.models import User
            user = self.db.query(User).filter(User.id == run.user_id).first()
            if not user:
                return None
            return user.display_name or user.email
        except Exception:  # pragma: no cover - defensive
            return None

    async def _notify_terminal(self, run, score, doctor=None):
        """Best-effort outbound notification for a terminal run.

        Runs off the event loop (the HTTP POST is blocking) and swallows all
        failures; notify_run_terminal is itself best-effort, but the executor
        dispatch is wrapped too so a webhook problem can never affect run
        status or bubble out of the pipeline.
        """
        try:
            from app.services.notifications import notify_run_terminal
            submitter = self._submitter_label(run)
            await asyncio.get_running_loop().run_in_executor(
                None, notify_run_terminal, run, score, submitter, doctor
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Run notification failed for run %s: %s", self.run_id, e)

    async def _run_doctor(self):
        """Run cross-stage lints over this run's artifacts and publish the report.

        Gated by CVICHE_RUN_DOCTOR (unset or "1" = on, "0" = off). The lints
        are pure local file reads (no LLM, no network), run off the event
        loop. The report lands in the pipeline outputs dir at
        stage_7_doctor/<uid>_doctor.json -- a location the stage-JSON viewer
        already serves -- then is attached to the final step's output_files so
        the UI lists it, and mirrored to durable storage for the S3 fallback.
        Returns the report dict, or None when disabled.
        """
        enabled, _ = get_config("doctor", "CVICHE_RUN_DOCTOR", default="1")
        if str(enabled).strip() == "0":
            return None

        loop = asyncio.get_running_loop()
        payload, out_path = await loop.run_in_executor(None, self._doctor_report)

        # Attach the report to the last step's output_files here, on the
        # orchestrator's thread (like _submitter_label: the session must not
        # be touched from an executor thread).
        step = (
            self.db.query(Step)
            .filter(Step.run_id == self.run_id)
            .order_by(Step.step_number.desc())
            .first()
        )
        if step is not None:
            files = json.loads(step.output_files) if step.output_files else []
            if str(out_path) not in files:
                files.append(str(out_path))
                step.output_files = json.dumps(files)
                self.db.commit()

        await loop.run_in_executor(
            None, self._persist_outputs_to_storage, [str(out_path)]
        )
        return payload

    def _doctor_report(self):
        """Run the doctor lints and write the JSON report; returns (payload, path)."""
        from unified_pipeline.run_doctor import run_doctor

        source = PARENT_DIR / 'data' / 'sample_cvs' / 'word' / f'{self.document_uid}.docx'
        payload = run_doctor(
            self.pipeline_output_dir,
            self.document_uid,
            source=source if source.exists() else None,
        )
        out_path = (
            self.pipeline_output_dir / 'stage_7_doctor'
            / f'{self.document_uid}_doctor.json'
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2))
        return payload, out_path

    async def execute_step(self, step_number: int, stage_id: str, cv_path: str):
        """Execute a single stage."""
        step_def = get_step_by_stage_id(stage_id)

        step = self.db.query(Step).filter(
            Step.run_id == self.run_id,
            Step.step_number == step_number
        ).first()

        if not step:
            step = Step(
                run_id=self.run_id,
                step_number=step_number,
                step_name=step_def.name,
                status="pending"
            )
            self.db.add(step)
            self.db.commit()

        try:
            step.status = "running"
            step.started_at = datetime.now()
            self.db.commit()

            await event_emitter.emit_step_start(self.run_id, step_number, self.total_cost)
            await self.log(step_number, f"Starting Stage {stage_id}: {step_def.name}")

            start_time = time.time()

            # Execute the actual stage logic, bounded by a coarse per-stage
            # wall-clock ceiling. A hung stage (e.g. a wedged provider call)
            # then raises TimeoutError into the except below instead of
            # leaving the run pinned at "running" forever. Translate it to a
            # clear, user-facing message rather than a bare asyncio error.
            stage_timeout = _get_stage_timeout_seconds()
            try:
                if stage_timeout > 0:
                    result = await asyncio.wait_for(
                        self._execute_stage_logic(stage_id, cv_path),
                        timeout=stage_timeout,
                    )
                else:
                    result = await self._execute_stage_logic(stage_id, cv_path)
            except (asyncio.TimeoutError, TimeoutError) as exc:
                raise TimeoutError(
                    f"Stage {stage_id} ({step_def.name}) timed out after "
                    f"{stage_timeout}s and was stopped."
                ) from exc

            duration = int(time.time() - start_time)

            # Mark as complete
            step.status = "complete"
            step.completed_at = datetime.now()
            step.duration_seconds = duration
            step.cost = result.get("cost", 0.0)
            step.output_files = json.dumps(result.get("output_files", []))
            self.db.commit()

            # Mirror outputs to durable storage so downloads survive pod
            # recycling (#38). Off-loop to avoid blocking websocket emits.
            await asyncio.get_running_loop().run_in_executor(
                None, self._persist_outputs_to_storage, result.get("output_files", [])
            )

            await self.log(step_number, f"Completed Stage {stage_id} in {duration}s")
            await event_emitter.emit_step_complete(
                self.run_id,
                step_number,
                duration,
                step.cost,
                result.get("output_files", [])
            )

            # Replicate prompt-log files written during this step into the
            # per-run storage backend. In dev (local storage) this just
            # mirrors them into the run dir; in prod (S3) it makes them
            # survive container restarts. Runs in a thread so the boto3 PUT
            # doesn't block the event loop. Failure does not fail the step.
            await asyncio.to_thread(
                self._sync_prompt_logs_to_storage, step.started_at, step_number
            )

        except Exception as e:
            step.status = "error"
            step.completed_at = datetime.now()
            tb_str = traceback.format_exc()
            step.error_message = f"{str(e)}\n\nTraceback:\n{tb_str}"
            self.db.commit()

            # Remember which stage broke so the run-level handler can name it
            # in the terminal RUN_FAILED event.
            self.failed_step_number = step_number

            await self.log(step_number, f"Error in Stage {stage_id}: {str(e)}", "ERROR")
            await event_emitter.emit_step_error(self.run_id, step_number, str(e))
            raise

    def _sync_prompt_logs_to_storage(
        self, since: Optional[datetime], step_number: int
    ) -> None:
        """Copy prompt log files written since ``since`` into per-run storage.

        The pipeline's prompt_logger.py writes files to a single shared
        directory (`src/unified_pipeline/prompt_logs/`). In prod the
        container's writable layer is wiped on every restart, so these
        files must be replicated to durable storage to be readable later
        from the API.

        Files are filtered by mtime > ``since`` so each step uploads only
        what it wrote. Failures are logged and swallowed -- the step has
        already succeeded by the time this runs, and a missed prompt-log
        upload should not flip its status.
        """
        if not PROMPT_LOGS_DIR.exists():
            return

        since_ts = since.timestamp() if since else 0
        storage = get_storage()
        uploaded = 0

        try:
            entries = list(PROMPT_LOGS_DIR.iterdir())
        except OSError as exc:
            logger.warning("Could not list prompt logs dir: %s", exc)
            return

        for path in entries:
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime < since_ts:
                    continue
                data = path.read_bytes()
                storage.put_file(self.run_id, f"prompt_logs/{path.name}", data)
                uploaded += 1
            except Exception as exc:
                logger.warning(
                    "Failed to upload prompt log %s for run %s step %d: %s",
                    path.name, self.run_id, step_number, exc,
                )

        if uploaded:
            logger.info(
                "Synced %d prompt log(s) to storage for run %s step %d",
                uploaded, self.run_id, step_number,
            )

    def _count_headers(self, nodes) -> int:
        """Count total headers in hierarchy."""
        count = len(nodes)
        for node in nodes:
            count += self._count_headers(node.get('children', []))
        return count

    def _run_with_stdout_capture_sync(self, func, step_number: int, event_loop: asyncio.AbstractEventLoop, *args, **kwargs):
        """Run a function while capturing stdout and streaming logs in real-time.

        This runs in a thread pool, so the event loop remains free to process log emissions.
        The event_loop parameter is required to schedule async log operations
        from the synchronous context where stage functions run.
        """
        capture = StreamingStdoutCapture(self, step_number, event_loop)

        original_stdout = sys.stdout
        sys.stdout = capture
        try:
            result = func(*args, **kwargs)
            # Flush any remaining buffer
            capture.flush()
            return result
        finally:
            sys.stdout = original_stdout

    async def _run_with_stdout_capture(self, func, step_number: int, *args, **kwargs):
        """Run a function in a thread pool while capturing stdout and streaming logs in real-time.

        Uses asyncio.to_thread to run the synchronous stage function in a thread pool,
        keeping the event loop free to process log emissions.
        """
        event_loop = asyncio.get_running_loop()
        return await asyncio.to_thread(
            self._run_with_stdout_capture_sync,
            func, step_number, event_loop, *args, **kwargs
        )

    async def _execute_stage_logic(self, stage_id: str, cv_path: str) -> Dict[str, Any]:
        """Execute a specific pipeline stage."""
        output_paths = self._get_output_paths()
        output_files = []
        cost = 0.0
        step_number = get_step_by_stage_id(stage_id).number

        # Pin cwd to the project root for the pipeline's repo-root-relative
        # path lookups. Runs execute in concurrent background threads and cwd
        # is process-global, so this is intentionally not saved/restored per
        # run: every run targets the same constant directory, which removes
        # the race the previous getcwd()/restore caused.
        os.chdir(PARENT_DIR)

        try:
            if stage_id == '1a':
                # Stage 1a: Hierarchy Extraction
                await self.log(step_number, "Extracting CV hierarchy structure...")

                hierarchy, stats = await self._run_with_stdout_capture(
                    get_cv_hierarchy_chunked,
                    step_number,
                    cv_path=cv_path
                )

                # Save output
                output_dir = self.pipeline_output_dir / 'stage_1a_segmentation'
                output_dir.mkdir(parents=True, exist_ok=True)
                output_file = output_dir / f"{self.document_uid}_segmented.json"

                total_headers = self._count_headers(hierarchy)
                stage1_output = {
                    'document_uid': self.document_uid,
                    'hierarchy': hierarchy,
                    'meta': stats
                }

                with open(output_file, 'w') as f:
                    json.dump(stage1_output, f, indent=2)

                # Also save human-readable version
                txt_file = output_file.with_suffix('.txt')
                with open(txt_file, 'w') as f:
                    f.write(f"CV Hierarchy: {self.document_uid}\n")
                    f.write("=" * 80 + "\n\n")
                    def write_hierarchy(nodes, depth=0):
                        for node in nodes:
                            indent = "  " * depth
                            level = node.get('level', 'H1')
                            text = node.get('text', '')
                            f.write(f"{indent}[{level}] {text}\n")
                            if node.get('children'):
                                write_hierarchy(node['children'], depth + 1)
                    write_hierarchy(hierarchy)

                cost = stats.get('extraction_cost', 0)
                # Stage 1a returns 'extraction_input_tokens' and 'extraction_output_tokens'
                input_tokens = stats.get('extraction_input_tokens', 0) or stats.get('input_tokens', 0) or stats.get('prompt_tokens', 0)
                output_tokens = stats.get('extraction_output_tokens', 0) or stats.get('output_tokens', 0) or stats.get('completion_tokens', 0)
                self.stage_outputs['1a'] = str(output_file)
                output_files.append(str(output_file))

                await self.log(step_number, f"Extracted {len(hierarchy)} top-level sections, {total_headers} total headers")
                await self.update_cost(step_number, cost, input_tokens + output_tokens, input_tokens, output_tokens)

            elif stage_id == '1b':
                # Stage 1b: Hierarchy Mapping (no LLM)
                await self.log(step_number, "Mapping hierarchy to document indices...")

                stage1a_path = self.stage_outputs.get('1a') or str(output_paths['1a'])

                stage1b_data, stage1b_path = await self._run_with_stdout_capture(
                    run_stage_1b,
                    step_number,
                    docx_path=cv_path,
                    hierarchy_json_path=stage1a_path
                )

                self.stage_outputs['1b'] = str(stage1b_path)
                output_files.append(str(stage1b_path))

                await self.log(step_number, f"Mapped {stage1b_data['meta']['total_sections']} sections, {stage1b_data['meta']['leaf_sections']} leaf sections")

            elif stage_id == '2':
                # Stage 2: Entry Extraction
                await self.log(step_number, "Extracting individual entries...")

                stage1b_path = self.stage_outputs.get('1b') or str(output_paths['1b'])

                # Honor the user's WCM-instruction-stripping choice recorded on
                # the Run row. Column defaults to ON (1); treat as a truthy int,
                # falling back to True if the row/column is unexpectedly None.
                run = self.db.query(Run).filter(Run.id == self.run_id).first()
                strip_template_instructions = bool(run.strip_template_instructions) if run and run.strip_template_instructions is not None else True

                stage2_data, stage2_path = await self._run_with_stdout_capture(
                    run_stage_2,
                    step_number,
                    docx_path=cv_path,
                    hierarchy_json_path=stage1b_path,
                    strip_template_instructions=strip_template_instructions,
                    # Stage 2 makes one LLM call per section (~86 total); pass an
                    # intra-stage cancel check so an abort lands mid-stage rather
                    # than only at the stage boundary. check_cancelled() is sync,
                    # so it's safe to call from inside the stage worker thread.
                    cancel_check=self.check_cancelled
                )

                cost = stage2_data.get('total_cost', 0)
                entries_found = stage2_data.get('total_entries', 0)
                input_tokens = stage2_data.get('prompt_tokens', 0) or stage2_data.get('input_tokens', 0)
                output_tokens = stage2_data.get('completion_tokens', 0) or stage2_data.get('output_tokens', 0)

                self.stage_outputs['2'] = str(stage2_path)
                output_files.append(str(stage2_path))

                await self.log(step_number, f"Extracted {entries_found} entries")
                await self.update_cost(step_number, cost, input_tokens + output_tokens, input_tokens, output_tokens)

            elif stage_id == '3a':
                # Stage 3a: Header Taxonomy Mapping
                await self.log(step_number, "Mapping headers to WCM taxonomy...")

                stage3a_result = await self._run_with_stdout_capture(
                    run_stage_3a,
                    step_number,
                    document_uid=self.document_uid
                )

                cost = stage3a_result['stats']['cost']
                stats = stage3a_result.get('stats', {})
                input_tokens = stats.get('input_tokens', 0) or stats.get('prompt_tokens', 0)
                output_tokens = stats.get('output_tokens', 0) or stats.get('completion_tokens', 0)
                self.stage_outputs['3a'] = stage3a_result['output_path']
                output_files.append(stage3a_result['output_path'])

                await self.log(step_number, f"Mapped {stage3a_result['node_count']} header nodes")
                await self.update_cost(step_number, cost, input_tokens + output_tokens, input_tokens, output_tokens)

            elif stage_id == '3b':
                # Stage 3b: Entry Classification
                await self.log(step_number, "Classifying entries to taxonomy codes...")

                stage3a_path = self.stage_outputs.get('3a') or str(output_paths['3a'])

                stage3b_result = await self._run_with_stdout_capture(
                    run_stage_3b,
                    step_number,
                    document_uid=self.document_uid,
                    stage_3a_path=stage3a_path
                )

                cost = stage3b_result['stats']['cost']
                stats3b = stage3b_result.get('stats', {})
                input_tokens = stats3b.get('input_tokens', 0) or stats3b.get('prompt_tokens', 0)
                output_tokens = stats3b.get('output_tokens', 0) or stats3b.get('completion_tokens', 0)
                self.stage_outputs['3b'] = stage3b_result['output_path']
                output_files.append(stage3b_result['output_path'])

                await self.log(step_number, f"Classified {stage3b_result['total_entries']} entries")
                await self.update_cost(step_number, cost, input_tokens + output_tokens, input_tokens, output_tokens)

            elif stage_id == '4':
                # Stage 4: Field Extraction
                await self.log(step_number, "Extracting structured fields...")

                stage4_result = await self._run_with_stdout_capture(
                    run_stage_4,
                    step_number,
                    docx_path=f"{self.document_uid}.docx",
                    # Stage 4 is the heaviest stage (~130 LLM calls across
                    # batches); pass an intra-stage cancel check so an abort
                    # lands mid-stage. check_cancelled() is sync, so it's safe
                    # to call from inside the stage worker thread.
                    cancel_check=self.check_cancelled
                )

                stage4_output = stage4_result['output']
                cost = stage4_output.get('total_cost', 0)
                stats4 = stage4_output.get('stats', {})
                input_tokens = stats4.get('input_tokens', 0) or stats4.get('prompt_tokens', 0) or stage4_output.get('total_tokens', 0) // 2
                output_tokens = stats4.get('output_tokens', 0) or stats4.get('completion_tokens', 0) or stage4_output.get('total_tokens', 0) // 2
                cache_read_tokens = stage4_output.get('cache_read_tokens', 0) or stats4.get('cache_read_tokens', 0)
                cache_write_tokens = stage4_output.get('cache_write_tokens', 0) or stats4.get('cache_write_tokens', 0)

                self.stage_outputs['4'] = stage4_result['output_path']
                output_files.append(stage4_result['output_path'])

                extracted = stats4.get('extracted', 0)
                await self.log(step_number, f"Extracted fields for {extracted} entries")
                await self.update_cost(step_number, cost, input_tokens + output_tokens, input_tokens, output_tokens,
                                       cache_read_tokens_delta=cache_read_tokens,
                                       cache_write_tokens_delta=cache_write_tokens)

            elif stage_id == '4.5':
                # Stage 4.5: Research Summary
                await self.log(step_number, "Generating research summary...")

                stage4_path = self.stage_outputs.get('4') or str(output_paths['4'])

                stage45_output_path = await self._run_with_stdout_capture(
                    run_stage_4_5,
                    step_number,
                    input_path=stage4_path,
                    verbose=True
                )

                with open(stage45_output_path, 'r') as f:
                    stage45_data = json.load(f)

                research_info = stage45_data.get('research_summary', {})
                self.stage_outputs['4.5'] = stage45_output_path
                output_files.append(stage45_output_path)

                method = research_info.get('method', 'generated')
                await self.log(step_number, f"Research summary {method}, M1 score: {research_info.get('m1_score', 0):.2f}")

                # Track LLM cost for stage 4.5
                cost = stage45_data.get('total_cost', 0)
                input_tokens = stage45_data.get('prompt_tokens', 0)
                output_tokens = stage45_data.get('completion_tokens', 0)
                cache_read_tokens = stage45_data.get('cache_read_tokens', 0)
                cache_write_tokens = stage45_data.get('cache_write_tokens', 0)
                if cost > 0:
                    await self.update_cost(step_number, cost, input_tokens + output_tokens, input_tokens, output_tokens,
                                           cache_read_tokens_delta=cache_read_tokens,
                                           cache_write_tokens_delta=cache_write_tokens)

            elif stage_id == '5':
                # Stage 5: PubMed Enrichment
                await self.log(step_number, "Enriching publications with PubMed data...")

                stage4_path = self.stage_outputs.get('4') or str(output_paths['4'])

                await self._run_with_stdout_capture(
                    run_stage5,
                    step_number,
                    stage4_path=stage4_path,
                    verbose=True
                )

                stage5_output_path = str(output_paths['5'])
                self.stage_outputs['5'] = stage5_output_path
                output_files.append(stage5_output_path)

                await self.log(step_number, "PubMed enrichment complete")

            elif stage_id == '5b':
                # Stage 5b: Institution Enrichment
                await self.log(step_number, "Adding institution location data...")

                # Prefer most recent output
                input_path = self.stage_outputs.get('5') or self.stage_outputs.get('4') or str(output_paths['5'])

                stage5b_output_path = await self._run_with_stdout_capture(
                    run_stage5b,
                    step_number,
                    input_path=input_path,
                    verbose=True
                )

                self.stage_outputs['5b'] = stage5b_output_path
                output_files.append(stage5b_output_path)

                await self.log(step_number, "Institution enrichment complete")

            elif stage_id == '5c':
                # Stage 5c: Teaching Formatter
                await self.log(step_number, "Formatting teaching entries...")

                # Prefer most recent output
                input_path = (self.stage_outputs.get('5b') or
                             self.stage_outputs.get('5') or
                             self.stage_outputs.get('4'))
                if not input_path:
                    input_path = str(output_paths['5b']) if output_paths['5b'].exists() else str(output_paths['4'])

                stage5c_output_path = await self._run_with_stdout_capture(
                    run_stage_5c,
                    step_number,
                    input_path=input_path,
                    verbose=True
                )

                self.stage_outputs['5c'] = stage5c_output_path
                output_files.append(stage5c_output_path)

                # Track LLM cost for stage 5c
                with open(stage5c_output_path, 'r') as f:
                    stage5c_data = json.load(f)
                stage5c_meta = stage5c_data.get('stage_5c', {})
                cost = stage5c_meta.get('total_cost', 0)
                input_tokens = stage5c_meta.get('prompt_tokens', 0)
                output_tokens = stage5c_meta.get('completion_tokens', 0)
                cache_read_tokens = stage5c_meta.get('cache_read_tokens', 0)
                cache_write_tokens = stage5c_meta.get('cache_write_tokens', 0)
                if cost > 0:
                    await self.update_cost(step_number, cost, input_tokens + output_tokens, input_tokens, output_tokens,
                                           cache_read_tokens_delta=cache_read_tokens,
                                           cache_write_tokens_delta=cache_write_tokens)

                await self.log(step_number, "Teaching entries formatted")

            elif stage_id == '5d':
                # Stage 5d: Citation Formatter
                await self.log(step_number, "Formatting citations to Vancouver format...")

                # Prefer most recent output
                input_path = (self.stage_outputs.get('5c') or
                             self.stage_outputs.get('5b') or
                             self.stage_outputs.get('5') or
                             self.stage_outputs.get('4'))
                if not input_path:
                    for stage in ['5c', '5b', '5', '4']:
                        if output_paths[stage].exists():
                            input_path = str(output_paths[stage])
                            break

                stage5d_output_path = await self._run_with_stdout_capture(
                    run_stage_5d,
                    step_number,
                    input_path=input_path,
                    verbose=True
                )

                self.stage_outputs['5d'] = stage5d_output_path
                output_files.append(stage5d_output_path)

                # Track LLM cost for stage 5d
                with open(stage5d_output_path, 'r') as f:
                    stage5d_data = json.load(f)
                stage5d_meta = stage5d_data.get('stage_5d', {})
                cost = stage5d_meta.get('total_cost', 0)
                input_tokens = stage5d_meta.get('prompt_tokens', 0)
                output_tokens = stage5d_meta.get('completion_tokens', 0)
                cache_read_tokens = stage5d_meta.get('cache_read_tokens', 0)
                cache_write_tokens = stage5d_meta.get('cache_write_tokens', 0)
                if cost > 0:
                    await self.update_cost(step_number, cost, input_tokens + output_tokens, input_tokens, output_tokens,
                                           cache_read_tokens_delta=cache_read_tokens,
                                           cache_write_tokens_delta=cache_write_tokens)

                await self.log(step_number, "Citations formatted")

            elif stage_id == '6':
                # Stage 6: WCM Word Template
                await self.log(step_number, "Generating WCM Word template...")

                # Find best available input (prefer 5d > 5c > 5b > 5 > 4)
                input_path = None
                for stage in ['5d', '5c', '5b', '5', '4']:
                    if stage in self.stage_outputs:
                        input_path = self.stage_outputs[stage]
                        break
                    elif output_paths[stage].exists():
                        input_path = str(output_paths[stage])
                        break

                if not input_path:
                    raise ValueError("No input available for Stage 6")

                # Issue #153: honor the user's output-rendering choices recorded
                # on the Run row. Columns default to track changes ON (1) and
                # classification comments OFF (0); treat as truthy ints, falling
                # back to those defaults if the row/column is unexpectedly None.
                run = self.db.query(Run).filter(Run.id == self.run_id).first()
                emit_track_changes = bool(run.show_track_changes) if run and run.show_track_changes is not None else True
                emit_comments = bool(run.show_pipeline_comments) if run and run.show_pipeline_comments is not None else False
                strip_template_instructions = bool(run.strip_template_instructions) if run and run.strip_template_instructions is not None else True

                stage6_output_path = await self._run_with_stdout_capture(
                    run_stage6,
                    step_number,
                    input_path=input_path,
                    verbose=True,
                    emit_track_changes=emit_track_changes,
                    emit_comments=emit_comments,
                    strip_template_instructions=strip_template_instructions,
                )

                self.stage_outputs['6'] = stage6_output_path
                output_files.append(stage6_output_path)
                # Render-warnings sidecar (#227/#228): persist alongside the
                # docx so the doctor's inputs stay reconstructable off-pod.
                sidecar = Path(stage6_output_path).with_name(
                    f"{self.document_uid}_render_warnings.json")
                if sidecar.is_file():
                    output_files.append(str(sidecar))

                await self.log(step_number, f"WCM template generated: {Path(stage6_output_path).name}")

            else:
                raise ValueError(f"Unknown stage ID: {stage_id}")

        finally:
            os.chdir(PARENT_DIR)

        return {
            "output_files": output_files,
            "cost": cost
        }
