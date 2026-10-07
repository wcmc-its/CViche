"""
Pipeline orchestrator - coordinates execution of all 12 pipeline stages.

Uses run_full_pipeline.py stage functions directly.
Stages: 1a, 1b, 2, 3a, 3b, 4, 4.5, 5, 5b, 5c, 5d, 6
"""
import asyncio
import contextvars
import io
import json
import logging
import os
import re
import shutil
import threading
import time
import traceback
from collections.abc import Iterator, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

# Add parent project to path to import existing pipeline code
import sys

PARENT_DIR = Path(__file__).parent.parent.parent.parent.parent
sys.path.insert(0, str(PARENT_DIR))
sys.path.insert(0, str(PARENT_DIR / 'src'))

from app.config_loader import current_image_tag, get_config
from app.models import Log, Run, RunState, Step
from app.pipeline.event_emitter import event_emitter
from app.pipeline.step_registry import STEP_REGISTRY, get_step_by_stage_id
from app.services.cv_owner_service import CV_OWNER_STAGE_ID, read_cv_owner_name
from app.storage import get_storage
from app.storage.base import RunStorage

# The pipeline's prompt_logger writes per-LLM-call transcripts here. We
# replicate fresh files into per-run storage so they survive container
# restarts and replica scale-up.
PROMPT_LOGS_DIR = PARENT_DIR / 'src' / 'unified_pipeline' / 'prompt_logs'


# Import stage functions from run_full_pipeline.py dependencies
from app.services.pdf_sandbox import (
    PDF_BUSY_RUN_MESSAGE,
    PDF_TOO_COMPLEX_MESSAGE,
    ConversionResult,
    PdfBusyError,
    PdfTooComplexError,
    convert_pdf,
)
from unified_pipeline.core.prompt_logger import reset_current_run_id, set_current_run_id
from unified_pipeline.llm.retry import LLMOutageError
from unified_pipeline.llm_client import LlmUsage
from unified_pipeline.repair.protected_data import (
    REPAIR_FLAG_ENV,
    repair_flag_on,
    repairs_report_path,
)
from unified_pipeline.segmentation.chunked_chat_hierarchy_extractor import (
    get_cv_hierarchy_chunked,
)
from unified_pipeline.stage_1b_hierarchy_mapper import run_stage_1b
from unified_pipeline.stage_2_entry_extraction import run_stage_2
from unified_pipeline.stage_3a_header_taxonomy_mapper import run_stage_3a
from unified_pipeline.stage_3b_entry_classifier import run_stage_3b
from unified_pipeline.stage_4_5_research_summary import run_stage_4_5
from unified_pipeline.stage_4_field_extractor import process_cv as run_stage_4
from unified_pipeline.stage_5_pubmed_enrichment import run_stage5
from unified_pipeline.stage_5b_institution_enrichment import run_stage5b
from unified_pipeline.stage_5c_teaching_formatter import run_stage_5c
from unified_pipeline.stage_5d_citation_formatter import run_stage_5d
from unified_pipeline.stage_6_word_template import run_stage6
from unified_pipeline.stage_errors import (
    StageError,
    record_stage_outcome,
    stage_errors_path,
)

# An upload with this suffix is converted, not copied, into the run's private
# docx (#806); upload.py stores a PDF as {run_id}.pdf.
_PDF_SUFFIX = ".pdf"


def _now() -> float:
    """Monotonic clock for elapsed-duration measurement (#598).

    A module-level indirection so tests patch ``orchestrator._now`` directly.
    Patching ``time.monotonic`` itself is wrong: ``asyncio.run()`` calls it
    internally and would consume the stub.
    """
    return time.monotonic()


def _record_total_duration(run: Run, elapsed: int, resumed: bool) -> None:
    """Persist ``run.total_duration_seconds`` (#104).

    A resumed run (retry from a step) accumulates onto the prior total so the
    metric answers "how long did this run take" across attempts; a fresh run
    is a plain assignment.
    """
    if resumed:
        run.total_duration_seconds = (run.total_duration_seconds or 0) + elapsed
    else:
        run.total_duration_seconds = elapsed


# run_id and document_uid become path components (output dir, input copy).
UID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _require_safe_uid(name: str, value: str) -> str:
    if not UID_PATTERN.fullmatch(value):
        raise ValueError(f"Invalid {name} {value!r}: must match {UID_PATTERN.pattern}")
    return value


# run.error_message is shown verbatim to the (non-technical) user, so it
# never carries str(exc): exception text can hold filesystem paths, provider
# request ids and API detail (#592). The raw text stays in the ERROR log line
# and in step.error_message (with traceback), which the UI does not render.
RESUME_INPUT_MISSING_MESSAGE = (
    "Couldn't resume: earlier pipeline results are no longer "
    "available (the server may have restarted since this run). "
    'Please use "Restart with file" to run it from the beginning.'
)
LLM_OUTAGE_MESSAGE = (
    "The AI service was unavailable for too long, so this run stopped. "
    'This is usually temporary; please try "Retry failed step" in a few minutes.'
)
STAGE_TIMEOUT_MESSAGE = (
    "A processing step took too long and was stopped. "
    'Please try "Retry failed step"; if it happens again, contact the CViche team.'
)
GENERIC_FAILURE_MESSAGE = (
    "Something went wrong while processing this CV, and the details were "
    'logged for the CViche team. You can try "Retry failed step", or '
    '"Restart with file" to run it from the beginning.'
)


def _exception_chain(exc: BaseException | None) -> Iterator[BaseException]:
    """Yield exc and every __cause__/__context__ behind it, once each."""
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        yield exc
        exc = exc.__cause__ or exc.__context__


def user_facing_error(exc: BaseException, resuming: bool) -> str:
    """Map a pipeline failure to the fixed message stored in run.error_message."""
    chain = list(_exception_chain(exc))
    if any(isinstance(e, LLMOutageError) for e in chain):
        return LLM_OUTAGE_MESSAGE
    if isinstance(exc, PdfTooComplexError):
        return PDF_TOO_COMPLEX_MESSAGE
    if isinstance(exc, PdfBusyError):
        return PDF_BUSY_RUN_MESSAGE
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return STAGE_TIMEOUT_MESSAGE
    err_text = str(exc).lower()
    is_missing_input = (
        isinstance(exc, FileNotFoundError)
        or "no such file or directory" in err_text
        or "no input available" in err_text
    )
    # On a resume (per-step retry), a missing input file means an earlier
    # stage's output is no longer on disk -- e.g. the pod recycled since the
    # original run -- so "Restart with file" is the actionable next step.
    if resuming and is_missing_input:
        return RESUME_INPUT_MISSING_MESSAGE
    return GENERIC_FAILURE_MESSAGE


# Cancellation tracking. The in-process set covers same-worker cancels (and is
# the only mechanism when no Redis broker is configured). When a broker is
# enabled, cancels also round-trip through Redis so a cancel received by one
# worker/replica reaches the worker actually running the pipeline. The broker's
# cancel ops use a sync client, so is_cancelled() stays synchronous and the
# stage-boundary check (check_cancelled) needs no async change. Both signals
# are only low-latency hints: the run row's status is the durable record, and
# execute() writes "cancelled" to it if no API request already has (#591).
_cancelled_runs: set = set()
# Runs stopped by shutdown (stop_run_locally). Their row is failed by the drain,
# so execute() must not record them as user cancels.
_stopped_locally: set = set()
# Guards both sets: cancel_run runs on request threads, is_cancelled and
# clear_cancelled on pipeline threads (#307).
_cancelled_lock = threading.Lock()
_broker = None


def set_broker(broker):
    """Attach the Redis broker (called at app startup)."""
    global _broker
    _broker = broker


def cancel_run(run_id: str):
    """Signal a run to be cancelled."""
    with _cancelled_lock:
        _cancelled_runs.add(run_id)
    if _broker is not None and _broker.enabled:
        _broker.request_cancel(run_id)


def stop_run_locally(run_id: str) -> None:
    """Make this process's executor of ``run_id`` stop at its next cancel check.

    For shutdown (#116), which fails the runs it could not drain: the flag stops
    the run's pipeline thread at its next stage boundary and, if the process
    somehow outlives the drain, keeps the pre-"complete" check from writing
    complete over the failure. Deliberately not sent through the broker: the
    Redis flag outlives this pod by CANCEL_TTL_SECONDS and would cancel a retry
    of the same run that another pod starts inside that window.
    """
    with _cancelled_lock:
        _cancelled_runs.add(run_id)
        _stopped_locally.add(run_id)


def is_cancelled(run_id: str) -> bool:
    """Check if a run has been cancelled (locally or via the broker)."""
    with _cancelled_lock:
        if run_id in _cancelled_runs:
            return True
    if _broker is not None and _broker.enabled:
        return _broker.is_cancelled(run_id)
    return False


def clear_cancelled(run_id: str):
    """Clear cancellation flag for a run."""
    with _cancelled_lock:
        _cancelled_runs.discard(run_id)
        _stopped_locally.discard(run_id)
    if _broker is not None and _broker.enabled:
        _broker.clear_cancel(run_id)


USER_CANCEL_MESSAGE = "Cancelled by user"

#: The research-summary stage: the one stage whose failure does not fail the
#: run (#1174, `_run_research_summary_stage`).
RESEARCH_SUMMARY_STAGE_ID = "4.5"

# The doctor report's record of the image that ran its lints (#1239); null when
# the image was built without a tag.
DOCTOR_IMAGE_TAG_KEY = "image_tag"


class CancelledException(Exception):
    """Exception raised when a pipeline run is cancelled."""
    pass


# How long a timed-out stage's worker thread gets to reach its next callback
# and unwind before the run is failed anyway (#590). Generous enough for one
# in-flight LLM call's log/progress round trip; a thread still alive after this
# is logged loudly, and every later callback it makes still raises.
STAGE_WORKER_EXIT_GRACE_SECONDS = 30


class StageAbandoned(BaseException):
    """Raised inside a stage worker thread once its stage timed out (#590).

    BaseException on purpose: stage code and the LLM retry loop wrap calls in
    ``except Exception`` and carry on, which would swallow a cooperative stop
    and let the thread keep working after the run was failed.
    """


class _StageGuard:
    """Per-stage stop flag plus a count of live worker threads (#590).

    ``stop`` is set by the orchestrator when the stage times out; the worker
    thread notices it at its next callback (stdout sink, progress, cancel
    check). ``wait_idle`` lets the orchestrator hold off the terminal status
    until the thread has actually exited.
    """

    def __init__(self) -> None:
        self.stop = threading.Event()
        self._live = 0
        self._cond = threading.Condition()

    def enter(self) -> None:
        with self._cond:
            self._live += 1

    def exit(self) -> None:
        with self._cond:
            self._live -= 1
            self._cond.notify_all()

    def raise_if_stopped(self) -> None:
        if self.stop.is_set():
            raise StageAbandoned("stage timed out; worker stopped at next callback")

    def wait_idle(self, timeout: float) -> bool:
        """True once no worker thread is live, False if ``timeout`` elapsed first."""
        with self._cond:
            return self._cond.wait_for(lambda: self._live == 0, timeout)


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
        # "llm" is where buildspec.yaml writes this key into the ConfigMap's
        # auth_config.yaml; that file has no "app" section (#305).
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


class _RoutedStdout:
    """Process-wide sys.stdout replacement that dispatches per calling thread.

    Installed once, permanently, at import. sys.stdout is a single binding
    shared by every thread in the interpreter -- with up to
    CVICHE_MAX_CONCURRENT_RUNS=3 runs each directly reassigning it per stage
    call from its own thread-pool worker thread, the last assignment won:
    run A's stage output streamed into run B's viewer and run B's own output
    was silently dropped (#581). This object is never reassigned; instead
    each stage call registers/unregisters *itself* under its own thread id
    (see PipelineOrchestrator._run_with_stdout_capture_sync), so a write is
    routed to whichever run's thread produced it. Not
    contextlib.redirect_stdout -- that rebinds the same global and would
    carry the identical race. Since #883, a stage may also fan work out onto
    a thread pool via contextvars.copy_context().run(...) (core/batch_pool);
    those pool threads have no entry in the thread-id dict, so _capture_var
    is the fallback route -- set on the stage thread, it is inherited by
    every context the pool copies from it.
    """

    def __init__(self, real_stdout):
        self._real = real_stdout
        self._captures: dict[int, StreamingStdoutCapture] = {}
        self._tokens: dict[int, contextvars.Token] = {}

    def register(self, capture: StreamingStdoutCapture) -> None:
        if threading.get_ident() in self._tokens:
            # A second register on one thread would overwrite the first token
            # and leave _capture_var pointing at the outer capture after the
            # inner unregister. Nothing nests today; keep it that way loudly.
            raise RuntimeError("stdout capture already registered on this thread")
        self._captures[threading.get_ident()] = capture
        self._tokens[threading.get_ident()] = _capture_var.set(capture)

    def unregister(self) -> None:
        self._captures.pop(threading.get_ident(), None)
        token = self._tokens.pop(threading.get_ident(), None)
        if token is not None:
            _capture_var.reset(token)

    def _target(self):
        ident = threading.get_ident()
        if ident in self._captures:
            return self._captures[ident]
        capture = _capture_var.get()
        return capture if capture is not None else self._real

    def write(self, text: str) -> int:
        return self._target().write(text)

    def flush(self) -> None:
        self._target().flush()

    # standards-waiver: 3.7 -- fixes #581's sys.stdout leak, see docstring above
    def __getattr__(self, name):
        # isatty(), encoding, etc. -- anything we don't model ourselves goes
        # to the real stdout, not whichever run happens to be registered.
        return getattr(self._real, name)


# Installed once at import time, replacing the old per-call sys.stdout
# reassignment. See _RoutedStdout's docstring. The assignment (not just the
# construction) has to happen here: building the router without installing
# it leaves sys.stdout pointing at the original stream until the first
# stage call, which is exactly the ambiguity this class exists to remove.
_STDOUT_ROUTER = _RoutedStdout(sys.stdout)
sys.stdout = _STDOUT_ROUTER


class StreamingStdoutCapture:
    """Captures stdout and streams lines to database in real-time.

    Uses asyncio.run_coroutine_threadsafe to safely call async log methods
    from the synchronous context where stage functions run.
    """

    def __init__(self, orchestrator, step_number: int, event_loop: asyncio.AbstractEventLoop,
                 guard: _StageGuard | None = None):
        self.orchestrator = orchestrator
        self.guard = guard
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
        if self.guard is not None and self.guard.stop.is_set():
            return  # stage abandoned (#590): nothing may land after the terminal status
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
        except Exception as exc:
            # Progress is best-effort, but a programming error must be visible.
            logger.debug("Progress emit skipped for step %d: %s", self.step_number, exc)

    def _writer_is_the_run_loop(self) -> bool:
        """True when this write comes from the run's own event loop thread.

        A capture's lines come from the stage thread (and pools it spawns).
        A write on the run loop's thread is a side effect of streaming -- a
        log call inside log(), the emitter or the broker, or asyncio's own
        error reporting -- made while this capture is still the context's
        stdout. Streaming it would block that loop in _emit_log_sync waiting
        on itself, and its own emit could write again (#116).
        """
        try:
            return asyncio.get_running_loop() is self.event_loop
        except RuntimeError:
            return False

    def write(self, text: str):
        """Capture stdout writes and stream them to the database."""
        if text and self._writer_is_the_run_loop():
            # Checked before the stop point: a run-loop write is a logging
            # side effect, not the stage, so it must not raise StageAbandoned.
            if self._original_stdout:
                self._original_stdout.write(text)
            return len(text)
        if self.guard is not None:
            self.guard.raise_if_stopped()  # cooperative stop point (#590)
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
        if self._writer_is_the_run_loop():
            # A logging handler's flush after a run-loop write lands here too;
            # streaming the buffer from this thread would block like write().
            if self._original_stdout:
                self._original_stdout.flush()
            return
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


# Referenced by _RoutedStdout.register/unregister/_target above -- defined
# here (not next to that class) because the type parameter needs
# StreamingStdoutCapture, which isn't defined yet at that point in the
# module; methods resolve module globals at call time, so the split is safe.
_capture_var: contextvars.ContextVar[StreamingStdoutCapture | None] = contextvars.ContextVar(
    "cviche_stdout_capture", default=None
)


def _write_hierarchy(f, nodes, depth: int = 0) -> None:
    """Write ``[LEVEL] text`` lines, indented two spaces per depth, to ``f``.

    Iterative pre-order walk so a deep header chain cannot hit the recursion limit.
    """
    stack = [(node, depth) for node in reversed(nodes)]
    while stack:
        node, d = stack.pop()
        f.write(f"{'  ' * d}[{node.get('level', 'H1')}] {node.get('text', '')}\n")
        children = node.get('children')
        if children:
            stack.extend((child, d + 1) for child in reversed(children))


class PipelineOrchestrator:
    """Orchestrates the execution of all 12 pipeline stages."""

    def __init__(self, run_id: str, file_path: Path, db: Session):
        self.run_id = _require_safe_uid("run_id", run_id)
        self.file_path = file_path
        # Output and input-copy paths are keyed by document_uid alone, so two
        # runs sharing one would silently reuse each other's artifacts (#597).
        # Every caller names the stored file f"{run_id}.{ext}"; enforce it.
        self.document_uid = _require_safe_uid("document_uid", Path(file_path).stem)
        if self.document_uid != self.run_id:
            raise ValueError(
                f"document_uid {self.document_uid!r} must equal run_id {self.run_id!r}"
            )
        self.db = db

        # Output directory for this run (in the unified_pipeline outputs)
        self.pipeline_output_dir = PARENT_DIR / 'src' / 'unified_pipeline' / 'outputs'

        # Web interface output directory (for tracking)
        self.web_output_dir = Path(__file__).parent.parent.parent.parent / "outputs" / run_id
        self.web_output_dir.mkdir(parents=True, exist_ok=True)

        # Track outputs between stages
        self.stage_outputs: dict[str, str] = {}

        # Total cost tracking
        self.total_cost = 0.0

        # Step number of the stage that raised, so the run-level failure handler
        # can attribute RUN_FAILED to the stage the user was watching.
        self.failed_step_number: int | None = None

        # Stop flag + live-thread count for the stage being executed (#590);
        # replaced at the start of every execute_step.
        self._stage_guard = _StageGuard()

        # Set by _copy_to_pipeline_input when the upload is a PDF (#806).
        self.pdf_conversion: ConversionResult | None = None

    async def log(self, step_number: int, message: str, level: str = "INFO"):
        """Log a message to database and emit via WebSocket."""
        log_entry = Log(run_id=self.run_id, step_number=step_number, level=level, message=message)
        self.db.add(log_entry)
        self.db.commit()
        await event_emitter.emit_log(self.run_id, step_number, message, level)

    async def _track_llm_cost(self, step_number: int, cost: float, tokens: Mapping) -> float:
        """Record one stage's reported LLM cost on the run; returns the cost.

        ``tokens`` carries prompt/completion/cache_read/cache_write token
        counts under those key prefixes (a stage's own metadata dict works);
        a stage that reports cost only passes ``{}``. Nothing is recorded for
        a zero cost.
        """
        if cost > 0:
            input_tokens = tokens.get('prompt_tokens', 0)
            output_tokens = tokens.get('completion_tokens', 0)
            await self.update_cost(step_number, cost, input_tokens + output_tokens, input_tokens, output_tokens,
                                   cache_read_tokens_delta=tokens.get('cache_read_tokens', 0),
                                   cache_write_tokens_delta=tokens.get('cache_write_tokens', 0))
        return cost

    async def update_cost(self, step_number: int, cost_delta: float, tokens_delta: int = 0,
                          input_tokens_delta: int = 0, output_tokens_delta: int = 0,
                          cache_read_tokens_delta: int = 0,
                          cache_write_tokens_delta: int = 0,
                          provider: str = "bedrock"):
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
        """Check if this run has been cancelled and raise exception if so.

        The DB row is the authoritative cross-process signal (#701): the Redis
        cancel key expires after 300s, shorter than one stage can take, and a
        worker pod never sees the backend's in-process set. One SELECT per
        stage boundary is the price of not finishing a cancelled run.

        The read goes through its own pooled connection, not ``self.db``: stage
        2 passes this method as ``cancel_check`` and calls it from its parallel
        section threads, and a Session is not safe to share across threads.
        """
        self._stage_guard.raise_if_stopped()  # stage 2's intra-stage callback (#590)
        with self.db.get_bind().connect() as conn:
            status = conn.execute(select(Run.status).where(Run.id == self.run_id)).scalar()
        if status == RunState.CANCELLED or is_cancelled(self.run_id):
            raise CancelledException(f"Run {self.run_id} was cancelled by user")

    def _pipeline_input_path(self) -> Path:
        """This run's private copy of the upload: word/<run_id>/<uid>.docx."""
        return (PARENT_DIR / 'data' / 'sample_cvs' / 'word'
                / self.run_id / f"{self.document_uid}.docx")

    def _copy_to_pipeline_input(self) -> str:
        """Materialize this run's private docx from the upload; return its path.

        A docx is copied. A PDF is converted (#806), in pdf_sandbox's
        limited child process: the upload itself stays the original
        (download-original and restart read it by file_type), and every
        stage downstream sees only this docx.
        """
        dest_path = self._pipeline_input_path()
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        if Path(self.file_path).suffix.lower() == _PDF_SUFFIX:
            self.pdf_conversion = convert_pdf(self.file_path, dest_path)
        else:
            shutil.copy2(self.file_path, dest_path)
        return str(dest_path)

    async def _warn_image_only_pages(self) -> None:
        """Name a converted PDF's image-only pages in the run log (#536):
        their text never reaches the pipeline, so the output silently lacks
        it otherwise. Logged under the first stage, which the user sees first,
        and on a run's first attempt only: a retry or resume re-converts but
        the step-1 log already carries the warning."""
        if self.pdf_conversion is None or not self.pdf_conversion.image_only_pages:
            return
        pages = ", ".join(str(n) for n in self.pdf_conversion.image_only_pages)
        await self.log(
            STEP_REGISTRY[0].number,
            f"PDF page(s) {pages} contain only images (likely scanned), so their "
            "text could not be read and is missing from the output.",
            "WARNING",
        )

    def _get_output_paths(self) -> dict[str, Path]:
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

    def _persist_cv_owner_name(self, step: Step) -> None:
        """Store stage 4's inferred CV owner on ``runs.cv_owner_name``.

        The admin runs list filters on it. Best-effort: a failure here logs a
        warning and never fails the run (the name is display metadata).
        """
        try:
            name = read_cv_owner_name(self.db, self.run_id, step.output_files)
            if name is None:
                return
            run = self.db.query(Run).filter(Run.id == self.run_id).first()
            if run is None:
                return
            run.cv_owner_name = name
            self.db.commit()
        except Exception:
            logger.warning("Could not persist cv_owner_name for run %s",
                           self.run_id, exc_info=True)
            self.db.rollback()

    def _stage_errors_path(self) -> Path:
        return stage_errors_path(self.pipeline_output_dir, self.document_uid)

    def _record_stage_outcome(self, stage_id: str, error: StageError | None) -> None:
        """Write (or, on success, clear) ``stage_id``'s entry in the stage-error
        record quality_score reads for its fatal gate (#745), then mirror it to
        durable storage beside the other outputs, where the scorer collects it.

        Best-effort like the output mirror: failing to write the record must
        never mask the stage's own exception or fail a stage that succeeded,
        so the failure is logged with its traceback and the step carries on.
        """
        path = self._stage_errors_path()
        try:
            written = record_stage_outcome(path, stage_id, error)
        except (OSError, ValueError):
            logger.exception(
                "Could not update the stage-error record for run %s stage %s",
                self.run_id, stage_id)
            return
        if written:
            self._persist_outputs_to_storage([str(path)])

    def _rehydrate_stage_errors(self, storage: RunStorage) -> None:
        """Bring a resumed run's stage-error record (#745) back from durable
        storage when the pod-local copy is gone, so a retried stage that now
        succeeds can clear its entry instead of the stale durable copy
        capping the score. A run with no failed stage has none to fetch."""
        path = self._stage_errors_path()
        if path.exists():
            return
        try:
            data = storage.get_file(self.run_id, f"outputs/{path.name}")
        except FileNotFoundError:
            return  # no stage has failed for this run
        except Exception:
            logger.exception(
                "Could not rehydrate the stage-error record for run %s", self.run_id)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

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
        self._rehydrate_stage_errors(storage)

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
                except FileNotFoundError:
                    pass  # never mirrored; the back-up below logs the gap
                except Exception:
                    # A misconfigured or unreachable store: still recompute
                    # from here, but keep the trace that says why (#305).
                    logger.warning(
                        "Could not rehydrate stage %s output for run %s from "
                        "storage; recomputing it", step_def.stage_id, self.run_id,
                        exc_info=True,
                    )
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

    async def execute(self, start_step_number: int | None = None):
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
        run_id_token = None

        try:
            # Scope this run's prompt-log writes to PROMPT_LOGS_DIR/<run_id>
            # instead of the shared flat directory (#580). Must happen here,
            # not in __init__: __init__ runs on the request thread, while
            # execute() runs inside run_in_threadpool's own thread with its
            # own contextvars.Context -- the same one asyncio.to_thread
            # copies into every stage call made below. Token is reset in the
            # finally block below so a reused worker thread/task can't
            # inherit this run's id (review on #586).
            run_id_token = set_current_run_id(self.run_id)

            await event_emitter.emit_run_start(self.run_id)
            start_time = _now()

            # Notify Teams that a fresh run started processing (issue #154).
            # Only on a true start, not a per-step retry/resume (which passes a
            # start_step_number), so a re-run doesn't re-announce. Best-effort:
            # _notify_started runs off the loop and swallows all failures.
            if start_step_number is None:
                await self._notify_started(run)

            # Copy (or, for a PDF, convert) the upload to the pipeline input dir
            # Off the loop: a PDF conversion can wait for a sandbox slot and
            # then run for minutes, and the loop must keep serving events.
            cv_path = await asyncio.to_thread(self._copy_to_pipeline_input)
            if start_step_number is None:
                await self._warn_image_only_pages()

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
            if run.status == RunState.CANCELLED or is_cancelled(self.run_id):
                raise CancelledException(f"Run {self.run_id} was cancelled by user")

            duration = int(_now() - start_time)
            run.status = RunState.COMPLETE
            run.completed_at = datetime.now()
            # Persist the authoritative pipeline duration (previously only emitted
            # over the WebSocket) so historical conversion-time metrics are queryable.
            _record_total_duration(run, duration, start_step_number is not None)
            run.total_cost = self.total_cost
            self.db.commit()

            await event_emitter.emit_run_complete(self.run_id, run.total_cost, run.total_tokens, duration)

            # Compute & cache the advisory quality score for the admin view.
            # Best-effort, run off the event loop; never affects run status.
            score = None
            try:
                from app.services.quality_score_service import (
                    compute_and_cache_score,
                    persist_score_columns,
                )
                score = await asyncio.get_running_loop().run_in_executor(
                    None, compute_and_cache_score, self.run_id
                )
                # On this thread: self.db must not be touched from the executor.
                persist_score_columns(self.db, self.run_id, score)
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
            if self._stopped_by_shutdown():
                # The drain fails this run and writes its log row and failure
                # card itself (#116). Nothing to emit: uvicorn has already
                # closed every socket before the drain runs.
                logger.info("Run %s stopped by shutdown; the drain records its failure", self.run_id)
            else:
                self._persist_cancelled()
                await self.log(0, "Pipeline cancelled by user", "WARNING")
                await event_emitter.emit(self.run_id, {"event": "RUN_CANCELLED"})
                await self._notify_batch_complete(run)

        except Exception as e:
            run.status = RunState.FAILED
            run.error_message = user_facing_error(
                e, resuming=start_step_number is not None
            )
            run.completed_at = datetime.now()
            # Record time-to-failure too -- useful when diagnosing a run that was
            # "taking too long" and then errored out.
            if start_time is not None:
                _record_total_duration(
                    run,
                    int(_now() - start_time),
                    start_step_number is not None,
                )
            self.db.commit()
            await self.log(0, f"Pipeline failed: {str(e)}", "ERROR")
            # Authoritative terminal failure signal. Emit the user-facing
            # message (run.error_message), not the raw exception, so a
            # resume-input failure surfaces its actionable guidance. Mirrors
            # RUN_COMPLETE / RUN_CANCELLED so the UI stops the timer and
            # switches to the failure state without waiting for a status poll.
            await event_emitter.emit_run_failed(
                self.run_id, run.error_message, self.failed_step_number
            )

            # Notify on terminal failure too (failures are the most important to
            # push). No score is computed on the failure path; pass a best-effort
            # cached read (usually None) and let the payload render "n/a".
            await self._notify_terminal(run, self._cached_score())

            raise

        finally:
            if run_id_token is not None:
                reset_current_run_id(run_id_token)
            # Clean up cancellation flag
            clear_cancelled(self.run_id)

    def _stopped_by_shutdown(self) -> bool:
        """True when this run was stopped by the shutdown drain (stop_run_locally),
        not cancelled by a user."""
        with _cancelled_lock:
            return self.run_id in _stopped_locally

    def _persist_cancelled(self) -> None:
        """Record a user cancel on the run row unless it is already terminal.

        The API endpoint normally wrote "cancelled" first, but this does not
        assume it did. A conditional UPDATE keeps this idempotent and never
        overwrites another terminal status (#591). A shutdown stop never gets
        here: the drain fails that row instead.
        """
        updated = self.db.query(Run).filter(
            Run.id == self.run_id, Run.status == RunState.RUNNING
        ).update(
            {
                "status": RunState.CANCELLED,
                "error_message": USER_CANCEL_MESSAGE,
                "completed_at": datetime.now(),
            },
            synchronize_session=False,
        )
        self.db.commit()
        if updated:
            logger.info("Run %s marked cancelled by the orchestrator", self.run_id)

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

    async def _notify_started(self, run):
        """Best-effort outbound notification that a run started processing.

        Mirrors _notify_terminal: runs the blocking POST off the event loop and
        swallows every failure so a webhook problem can never affect the run.
        """
        try:
            from app.services.batch_service import batch_size
            from app.services.notifications import notify_run_started
            submitter = self._submitter_label(run)
            # Read here, like the submitter: the POST thread must not touch the session.
            batch_files_submitted = batch_size(self.db, getattr(run, "batch_id", None))
            await asyncio.get_running_loop().run_in_executor(
                None, notify_run_started, run, submitter, batch_files_submitted
            )
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("Run start notification failed for run %s: %s", self.run_id, e)

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
        await self._notify_batch_complete(run)

    async def _notify_batch_complete(self, run: Run) -> None:
        """Send the emailed batch's completion email if this was its last run
        (#1298). Own session, off the event loop; DB errors are logged inside."""
        from app.services.batch_completion import notify_if_batch_complete
        await asyncio.get_running_loop().run_in_executor(None, notify_if_batch_complete, run.batch_id)

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

        source = self._pipeline_input_path()
        payload = run_doctor(
            self.pipeline_output_dir,
            self.document_uid,
            source=source if source.exists() else None,
            prompt_log_dir=PROMPT_LOGS_DIR / self.run_id,
        )
        out_path = (
            self.pipeline_output_dir / 'stage_7_doctor'
            / f'{self.document_uid}_doctor.json'
        )
        payload[DOCTOR_IMAGE_TAG_KEY] = current_image_tag()
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

            start_time = _now()
            self._stage_guard = _StageGuard()

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
                await self._stop_stage_worker(step_number, stage_id)
                raise TimeoutError(
                    f"Stage {stage_id} ({step_def.name}) timed out after "
                    f"{stage_timeout}s and was stopped."
                ) from exc

            duration = int(_now() - start_time)

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
            # None clears the stage's entry; stage 4.5 may hand back a non-fatal one (#1174).
            await asyncio.to_thread(self._record_stage_outcome, stage_id, result.get("stage_error"))
            if stage_id == CV_OWNER_STAGE_ID:
                self._persist_cv_owner_name(step)

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
            await asyncio.to_thread(
                self._record_stage_outcome, stage_id, StageError.from_exception(stage_id, e))

            await self.log(step_number, f"Error in Stage {stage_id}: {str(e)}", "ERROR")
            await event_emitter.emit_step_error(self.run_id, step_number, str(e))
            raise

    async def _stop_stage_worker(self, step_number: int, stage_id: str) -> None:
        """Stop a timed-out stage's worker thread before the run goes terminal (#590).

        asyncio.wait_for cancels only the awaiting task; the thread underneath
        keeps running. Set the stop flag so it raises at its next callback, then
        wait up to STAGE_WORKER_EXIT_GRACE_SECONDS for it to exit. If it is
        still alive after that, say so loudly and fail the run anyway: its
        callbacks stay dead, but artifact writes it makes itself cannot be
        stopped without process isolation.
        """
        guard = self._stage_guard
        guard.stop.set()
        if await asyncio.to_thread(guard.wait_idle, STAGE_WORKER_EXIT_GRACE_SECONDS):
            return
        message = (
            f"Stage {stage_id} worker thread is still running "
            f"{STAGE_WORKER_EXIT_GRACE_SECONDS}s after its timeout; failing the run "
            "anyway. Its progress and log callbacks are disabled."
        )
        logger.error("Run %s: %s", self.run_id, message)
        await self.log(step_number, message, "ERROR")

    def _sync_prompt_logs_to_storage(
        self, since: datetime | None, step_number: int
    ) -> None:
        """Copy this run's prompt log files written since ``since`` into per-run storage.

        prompt_logger.py writes each run's transcripts under
        ``PROMPT_LOGS_DIR/<run_id>`` (set_current_run_id() is called once at
        the top of execute()) rather than the old shared flat directory, so
        this can never pick up a concurrent run's files (#580). In prod the
        container's writable layer is wiped on every restart, so these files
        must be replicated to durable storage to be readable later from the
        API.

        Files are filtered by mtime > ``since`` purely to avoid re-uploading
        earlier steps' files on every step -- the per-run directory, not the
        mtime filter, is what keeps runs apart now.
        """
        run_log_dir = PROMPT_LOGS_DIR / self.run_id
        if not run_log_dir.exists():
            return

        since_ts = since.timestamp() if since else 0
        storage = get_storage()
        uploaded = 0

        try:
            entries = list(run_log_dir.iterdir())
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
        """Count total headers in hierarchy.

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

    def _run_with_stdout_capture_sync(self, func, step_number: int, event_loop: asyncio.AbstractEventLoop, guard: _StageGuard, *args, **kwargs):
        """Run a function while capturing stdout and streaming logs in real-time.

        This runs in a thread pool, so the event loop remains free to process log emissions.
        The event_loop parameter is required to schedule async log operations
        from the synchronous context where stage functions run.

        Registers with the process-wide _STDOUT_ROUTER under this thread's id
        rather than reassigning sys.stdout directly -- sys.stdout is one
        binding shared by every concurrent run's thread, so reassigning it
        here would race the same way the code this replaced did (#581).
        register() also sets _capture_var on this thread, so a pool thread
        spawned from inside func() (see core/batch_pool.map_in_order) still
        routes to this run's capture instead of falling through to real stdout.
        """
        capture = StreamingStdoutCapture(self, step_number, event_loop, guard)
        sys.stdout = _STDOUT_ROUTER  # idempotent; defends against anything else having swapped it
        _STDOUT_ROUTER.register(capture)
        try:
            result = func(*args, **kwargs)
            # Flush any remaining buffer
            capture.flush()
            return result
        finally:
            _STDOUT_ROUTER.unregister()
            guard.exit()  # pairs with guard.enter() in _run_with_stdout_capture (#590)

    async def _run_with_stdout_capture(self, func, step_number: int, *args, **kwargs):
        """Run a function in a thread pool while capturing stdout and streaming logs in real-time.

        Uses asyncio.to_thread to run the synchronous stage function in a thread pool,
        keeping the event loop free to process log emissions.
        """
        event_loop = asyncio.get_running_loop()
        guard = self._stage_guard
        guard.enter()  # before the thread starts, so a timeout can never miss it (#590)
        return await asyncio.to_thread(
            self._run_with_stdout_capture_sync,
            func, step_number, event_loop, guard, *args, **kwargs
        )

    def _render_options(self) -> tuple[bool, bool, bool]:
        """(emit_track_changes, emit_comments, strip_template_instructions) for stage 6.

        Pure move out of `_execute_stage_logic` (#550, keeps the §3 ratchet
        from rising): identical body, no behaviour change.
        """
        # Issue #153: honor the user's output-rendering choices recorded
        # on the Run row. Columns default to track changes ON (1) and
        # classification comments OFF (0); treat as truthy ints, falling
        # back to those defaults if the row/column is unexpectedly None.
        run = self.db.query(Run).filter(Run.id == self.run_id).first()
        emit_track_changes = bool(run.show_track_changes) if run and run.show_track_changes is not None else True
        emit_comments = bool(run.show_pipeline_comments) if run and run.show_pipeline_comments is not None else False
        strip_template_instructions = bool(run.strip_template_instructions) if run and run.strip_template_instructions is not None else True
        return emit_track_changes, emit_comments, strip_template_instructions

    def _repair_protected_data(self) -> bool:
        """Whether stage 6 repairs protected data (#1389): off unless CVICHE_RUN_REPAIR=1."""
        return repair_flag_on(get_config("repair", REPAIR_FLAG_ENV, default="0")[0])

    async def _execute_stage_logic(self, stage_id: str, cv_path: str) -> dict[str, Any]:
        """Execute a specific pipeline stage."""
        output_paths = self._get_output_paths()
        output_files = []
        cost = 0.0
        stage_error: StageError | None = None  # only stage 4.5 sets it: a failure it carried on past
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

                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(stage1_output, f, indent=2)

                # Also save human-readable version
                txt_file = output_file.with_suffix('.txt')
                with open(txt_file, 'w', encoding='utf-8') as f:
                    f.write(f"CV Hierarchy: {self.document_uid}\n")
                    f.write("=" * 80 + "\n\n")
                    _write_hierarchy(f, hierarchy)

                cost = stats.get('extraction_cost', 0)
                # Stage 1a returns 'extraction_input_tokens' and 'extraction_output_tokens'
                input_tokens = stats.get('extraction_input_tokens', 0) or stats.get('input_tokens', 0) or stats.get('prompt_tokens', 0)
                output_tokens = stats.get('extraction_output_tokens', 0) or stats.get('output_tokens', 0) or stats.get('completion_tokens', 0)
                self.stage_outputs['1a'] = str(output_file)
                output_files.append(str(output_file))

                await self.log(step_number, f"Extracted {len(hierarchy)} top-level sections, {total_headers} total headers")
                await self.update_cost(step_number, cost, input_tokens + output_tokens, input_tokens, output_tokens,
                                       cache_read_tokens_delta=stats.get('extraction_cache_read_tokens', 0),
                                       cache_write_tokens_delta=stats.get('extraction_cache_write_tokens', 0))

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
                    docx_path=cv_path,  # real path, not f"{uid}.docx" -- the owner-name side channel opens it (#456)
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
                # Stage 4.5: Research Summary. Its failure does not fail the run (#1174).
                stage45_data, stage_error = await self._run_research_summary_stage(
                    step_number, self.stage_outputs.get('4') or str(output_paths['4']), output_files)
                cost = await self._track_llm_cost(step_number, stage45_data.get('total_cost', 0), stage45_data)

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
                    verbose=True, persist_cache=False,  # no disk cache on the web path (#1238)
                )

                self.stage_outputs['5b'] = stage5b_output_path
                output_files.append(stage5b_output_path)

                # Track LLM cost for stage 5b (the stage reports cost only, no tokens)
                with open(stage5b_output_path, 'r', encoding='utf-8') as f:
                    stage5b_data = json.load(f)
                cost = await self._track_llm_cost(
                    step_number, stage5b_data.get('institution_enrichment_stats', {}).get('cost', 0), {})

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
                with open(stage5c_output_path, 'r', encoding='utf-8') as f:
                    stage5c_data = json.load(f)
                stage5c_meta = stage5c_data.get('stage_5c', {})
                cost = await self._track_llm_cost(step_number, stage5c_meta.get('total_cost', 0), stage5c_meta)

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
                with open(stage5d_output_path, 'r', encoding='utf-8') as f:
                    stage5d_data = json.load(f)
                stage5d_meta = stage5d_data.get('stage_5d', {})
                cost = await self._track_llm_cost(step_number, stage5d_meta.get('total_cost', 0), stage5d_meta)

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

                emit_track_changes, emit_comments, strip_template_instructions = self._render_options()

                stage6_usage = LlmUsage()
                stage6_output_path = await self._run_with_stdout_capture(
                    run_stage6,
                    step_number,
                    input_path=input_path,
                    verbose=True,
                    llm_usage=stage6_usage,
                    emit_track_changes=emit_track_changes,
                    emit_comments=emit_comments,
                    strip_template_instructions=strip_template_instructions,
                    # #550: explicit, like stages 1b/2/4 -- not the
                    # SAMPLE_CV_DIR auto-discovery that happened to find
                    # _copy_to_pipeline_input's copy by uid and CWD.
                    original_doc_path=cv_path,
                    repair_protected_data=self._repair_protected_data(),
                )

                self.stage_outputs['6'] = stage6_output_path
                output_files.append(stage6_output_path)
                # Render-warnings (#227/#228) and repair (#1389) sidecars persist with the docx.
                for sidecar in (Path(stage6_output_path).with_name(f"{self.document_uid}_render_warnings.json"),
                                repairs_report_path(stage6_output_path)):
                    if sidecar.is_file():
                        output_files.append(str(sidecar))

                # Track LLM cost for stage 6 (geographic scope + appendix reclassification)
                cost = await self._track_llm_cost(step_number, stage6_usage.cost, stage6_usage.token_totals())

                await self.log(step_number, f"WCM template generated: {Path(stage6_output_path).name}")

            else:
                raise ValueError(f"Unknown stage ID: {stage_id}")

        finally:
            os.chdir(PARENT_DIR)

        return {
            "output_files": output_files,
            "cost": cost,
            "stage_error": stage_error,
        }

    async def _run_research_summary_stage(self, step_number: int, stage4_path: str,
                                          output_files: list[str]) -> tuple[dict, StageError | None]:
        """Stage 4.5 for `_execute_stage_logic`: (its artifact, None), or ({}, a
        non-fatal StageError) when it raised.

        The one stage that never fails the run (#1174): a research summary is
        one section, so when run_stage_4_5 raises, the run carries on to
        stages 5 and 6 without it. Stage 6 renders no summary when the
        artifact is absent and routes the M1 entries to the Appendix. The
        failure is recorded non-fatal in the stage-error record, which the
        quality score counts and the doctor's stage_failure_recorded WARN
        names, and logged as a WARNING on the step.
        """
        await self.log(step_number, "Generating research summary...")
        try:
            stage45_output_path = await self._run_with_stdout_capture(
                run_stage_4_5, step_number, input_path=stage4_path, verbose=True)
        except CancelledException:
            raise
        except Exception as e:
            logger.warning("Stage 4.5 failed for run %s; continuing without a research summary",
                           self.run_id, exc_info=True)
            await self.log(step_number, f"Research summary skipped: stage 4.5 failed ({type(e).__name__}). "
                           "The Research Activities section has no summary; the run continues.", "WARNING")
            return {}, StageError.from_exception(RESEARCH_SUMMARY_STAGE_ID, e, fatal=False)

        with open(stage45_output_path, 'r', encoding='utf-8') as f:
            stage45_data = json.load(f)

        research_info = stage45_data.get('research_summary', {})
        self.stage_outputs[RESEARCH_SUMMARY_STAGE_ID] = stage45_output_path
        output_files.append(stage45_output_path)

        method = research_info.get('method', 'generated')
        await self.log(step_number, f"Research summary {method}, M1 score: {research_info.get('m1_score', 0):.2f}")
        return stage45_data, None
