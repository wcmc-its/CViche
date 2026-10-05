"""Batch upload (#1114): the run_batches rows, the batch view, and the
per-queue wait estimate behind GET /api/queue.

A batch is a header row (``RunBatch``) that its runs point at through
``runs.batch_id``. Each file is still uploaded and started on its own by the
client; this module only creates the header, answers who may see it, and
reads the batch and the two queues back.

Visibility (Counsel-approved policy, spec "Access policy"): a batch is visible
to the user who submitted it and to admins, and to nobody else -- another user
gets the same 404 as for a batch that does not exist.
"""
import bisect
import logging
import math
import secrets
import statistics
import string
from collections import Counter
from dataclasses import dataclass
from datetime import datetime

import redis
from sqlalchemy import ColumnElement, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query, Session

from app.errors import not_found
from app.models import BULK_BATCH_MIN_FILES, BatchSource, Run, RunBatch, RunState, User, can_view_all_runs
from app.pipeline import concurrency, run_queue
from app.schemas import (
    BatchDetail, BatchRunRow, BatchStatusCounts, BatchSummary, QueueLane, QueueOverview,
)
from app.services import mailer
from app.services.runs_admin_query import run_by_summary

logger = logging.getLogger(__name__)

# Most files one batch may hold (spec decision, Paul 2026-10-01). POST
# /batches and the multi-file POST /estimate both enforce it.
MAX_BATCH_FILES = 50

# Letters only, like run ids (#1192): an id read aloud or retyped never
# confuses O/0 or I/1. 26**6 ~= 3.1e8 ids; a collision costs a redraw.
_BATCH_ID_ALPHABET = string.ascii_uppercase
BATCH_ID_LENGTH = 6
# Redraws after a primary-key collision before giving up. At 26**6 ids a
# second collision in a row means something other than chance is wrong.
_BATCH_ID_ATTEMPTS = 5
# How each database reports a duplicate primary key: MySQL/MariaDB's
# ER_DUP_ENTRY errno, and SQLite's message prefix (the test database).
_MYSQL_DUPLICATE_KEY_ERRNO = 1062
_SQLITE_UNIQUE_FAILED = "UNIQUE constraint failed"

# How many recent completed runs the wait estimate's median duration is taken
# over: recent enough to track today's pipeline speed, enough to be stable.
RECENT_DURATION_SAMPLE = 50

_SECONDS_PER_MINUTE = 60


class BatchIdAttemptsExhausted(Exception):
    """Every redraw of a batch id collided with an existing one."""


def generate_batch_id() -> str:
    return "".join(secrets.choice(_BATCH_ID_ALPHABET) for _ in range(BATCH_ID_LENGTH))


def _is_duplicate_key(error: IntegrityError) -> bool:
    """True when ``error`` is a duplicate-key violation -- the only integrity
    error a redrawn id can cure. MySQL/MariaDB report it as errno 1062,
    SQLite (the test database) as "UNIQUE constraint failed"."""
    orig = error.orig
    if orig is None:
        return False
    if orig.args and orig.args[0] == _MYSQL_DUPLICATE_KEY_ERRNO:
        return True
    return _SQLITE_UNIQUE_FAILED in str(orig)


def create_batch(
    db: Session, user: User, files_submitted: int, source: str = BatchSource.WEB, *, notify_on_complete: bool = False,
) -> RunBatch:
    """Insert and commit a new batch owned by ``user``, redrawing the id on a
    duplicate-key collision. Any other integrity error (a foreign-key
    violation, say) is re-raised at once: no redraw can cure it. Raises
    BatchIdAttemptsExhausted, chained to the last collision, if every draw
    collides."""
    last_collision: IntegrityError | None = None
    for _ in range(_BATCH_ID_ATTEMPTS):
        batch = RunBatch(id=generate_batch_id(), user_id=user.id, files_submitted=files_submitted, source=source,
                         notify_on_complete=notify_on_complete)
        db.add(batch)
        try:
            db.commit()
        except IntegrityError as e:
            db.rollback()
            if not _is_duplicate_key(e):
                raise
            last_collision = e
            logger.warning("batch id collision on %s; redrawing", batch.id)
            continue
        db.refresh(batch)
        return batch
    raise BatchIdAttemptsExhausted(f"no free batch id after {_BATCH_ID_ATTEMPTS} attempts") from last_collision


def can_see_batch(user: User, batch: RunBatch) -> bool:
    """The submitter, or anyone who may read every run (admin or staff)."""
    return can_view_all_runs(user) or batch.user_id == user.id


def get_visible_batch(db: Session, batch_id: str, user: User) -> RunBatch:
    """The batch, if ``user`` may see it. 404 both when it does not exist and
    when it belongs to someone else (the approved policy: a batch is visible
    to its submitter, admins and read-only staff only, and its existence is
    not disclosed)."""
    batch = db.get(RunBatch, batch_id)
    if batch is None or not can_see_batch(user, batch):
        raise not_found("Batch not found")
    return batch


def get_owned_batch(db: Session, batch_id: str, user: User) -> RunBatch:
    """The batch an upload is joining: it must exist and be the caller's own,
    else the same 404 ``get_visible_batch`` answers, so /upload discloses no
    more about another user's batch than GET /batches/{id} does. No admin
    exception: only the submitter adds runs, so an admin uploading into
    someone else's batch gets the 404 too."""
    batch = db.get(RunBatch, batch_id)
    if batch is None or batch.user_id != user.id:
        raise not_found("Batch not found")
    return batch


def _run_counts(db: Session, batch_ids: list[str]) -> dict[str, int]:
    if not batch_ids:
        return {}
    rows = (
        db.query(Run.batch_id, func.count(Run.id))
        .filter(Run.batch_id.in_(batch_ids))
        .group_by(Run.batch_id)
        .all()
    )
    return dict(rows)


def _submitters(db: Session, user_ids: set[int]) -> dict[int, User]:
    if not user_ids:
        return {}
    return {user.id: user for user in db.query(User).filter(User.id.in_(user_ids)).all()}


def _summary(batch: RunBatch, submitter: User | None, run_count: int) -> BatchSummary:
    return BatchSummary(
        id=batch.id,
        submitted_by=run_by_summary(submitter),
        created_at=batch.created_at,
        run_count=run_count,
        files_submitted=batch.files_submitted,
    )


def list_batches(db: Session, user: User) -> list[BatchSummary]:
    """Every batch ``user`` may see (their own; all of them for an admin or
    staff), newest first."""
    query = db.query(RunBatch)
    if not can_view_all_runs(user):
        query = query.filter(RunBatch.user_id == user.id)
    batches = query.order_by(RunBatch.created_at.desc(), RunBatch.id).all()
    counts = _run_counts(db, [batch.id for batch in batches])
    submitters = _submitters(db, {batch.user_id for batch in batches})
    return [
        _summary(batch, submitters.get(batch.user_id), counts.get(batch.id, 0))
        for batch in batches
    ]


def _status_counts(runs: list[Run]) -> BatchStatusCounts:
    """Count a batch's runs by status. A legacy ``paused`` status cannot
    occur here (batches postdate its removal, #115), so it has no field."""
    by_status = Counter(run.status for run in runs)
    return BatchStatusCounts(
        complete=by_status[RunState.COMPLETE],
        running=by_status[RunState.RUNNING],
        queued=by_status[RunState.QUEUED],
        failed=by_status[RunState.FAILED],
        cancelled=by_status[RunState.CANCELLED],
        created=by_status[RunState.CREATED],
    )


def batch_size(db: Session, batch_id: str | None) -> int | None:
    """``files_submitted`` of the batch ``batch_id`` names -- the input
    ``run_queue.queue_for`` routes on -- or None for a run in no batch."""
    if batch_id is None:
        return None
    return db.query(RunBatch.files_submitted).filter(RunBatch.id == batch_id).scalar()


def _on_batch_queue() -> ColumnElement[bool]:
    """SQL twin of ``run_queue.queue_for``: true for a run whose token goes
    on the batch queue, i.e. one in a batch of BULK_BATCH_MIN_FILES or more
    files. Needs ``Run`` outer-joined to ``RunBatch`` (``_with_batch``); a
    run in no batch has no ``files_submitted`` and reads as false."""
    return func.coalesce(RunBatch.files_submitted, 0) >= BULK_BATCH_MIN_FILES


def _with_batch(query: Query) -> Query:
    """``query`` over ``Run`` with each run's batch outer-joined, for ``_on_batch_queue``."""
    return query.outerjoin(RunBatch, Run.batch_id == RunBatch.id)


def _queued_at_in(db: Session, queue: run_queue.Queue) -> list[datetime]:
    """When every queued run on ``queue`` entered it, ascending."""
    in_queue = _on_batch_queue() if queue is run_queue.BATCH else ~_on_batch_queue()
    rows = (
        _with_batch(db.query(Run.queued_at))
        .filter(Run.status == RunState.QUEUED, Run.queued_at.isnot(None), in_queue)
        .order_by(Run.queued_at)
        .all()
    )
    return [queued_at for (queued_at,) in rows]


def _queue_position(run: Run, queue_times: list[datetime]) -> int | None:
    """Queued runs that entered the batch's queue before ``run`` (spec
    endpoint 7). Runs on the other queue never count, so a bulk batch run's
    position can hold still while single runs go ahead of it. A one-file
    batch's run is on the single queue (``run_queue.queue_for``) and is
    ranked there."""
    if run.status != RunState.QUEUED or run.queued_at is None:
        return None
    return bisect.bisect_left(queue_times, run.queued_at)


def batch_detail(db: Session, batch: RunBatch, viewer: User) -> BatchDetail:
    """The batch view: header, status counts, and one row per run in upload
    order. ``quality_score`` is shown to admins and staff only, as on the
    all-runs list."""
    runs = (
        db.query(Run).filter(Run.batch_id == batch.id)
        .order_by(Run.created_at, Run.id)
        .all()
    )
    queue = run_queue.queue_for(batch.files_submitted)
    queue_times = _queued_at_in(db, queue) if any(r.status == RunState.QUEUED for r in runs) else []
    show_score = can_view_all_runs(viewer)
    rows = [
        BatchRunRow(
            run_id=run.id,
            filename=run.filename,
            cv_owner_name=run.cv_owner_name,
            status=run.status,
            queue_position=_queue_position(run, queue_times),
            quality_score=run.quality_score if show_score else None,
        )
        for run in runs
    ]
    submitter = db.get(User, batch.user_id)
    summary = _summary(batch, submitter, len(runs))
    return BatchDetail(**summary.model_dump(), status_counts=_status_counts(runs), runs=rows)


# --- GET /api/queue -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _LaneLoad:
    """DB side of one queue: runs waiting in it and runs of its kind executing."""
    waiting: int
    running: int


def _lane_loads(db: Session) -> dict[run_queue.Queue, _LaneLoad]:
    """Queued and running counts per queue. The queue a run is on is
    ``run_queue.queue_for`` of its batch's size, so batch-queue runs are the
    ones in a bulk batch (``_on_batch_queue``); a one-file batch's run counts
    under the single queue, where it waits."""
    on_batch_queue = _on_batch_queue()
    rows = (
        _with_batch(db.query(Run.status, on_batch_queue, func.count(Run.id)))
        .filter(Run.status.in_((RunState.QUEUED, RunState.RUNNING)))
        .group_by(Run.status, on_batch_queue)
        .all()
    )
    counts: Counter[tuple[str, bool]] = Counter()
    for status, in_batch, count in rows:
        counts[(status, bool(in_batch))] += count
    return {
        queue: _LaneLoad(
            waiting=counts[(RunState.QUEUED, queue is run_queue.BATCH)],
            running=counts[(RunState.RUNNING, queue is run_queue.BATCH)],
        )
        for queue in (run_queue.SINGLE, run_queue.BATCH)
    }


def recent_median_duration_seconds(db: Session) -> float | None:
    """Median pipeline time of the last RECENT_DURATION_SAMPLE completed runs,
    or None before any run has completed."""
    rows = (
        db.query(Run.total_duration_seconds)
        .filter(Run.status == RunState.COMPLETE, Run.total_duration_seconds.isnot(None))
        .order_by(Run.completed_at.desc())
        .limit(RECENT_DURATION_SAMPLE)
        .all()
    )
    durations = [seconds for (seconds,) in rows]
    return statistics.median(durations) if durations else None


def _live_workers() -> dict[run_queue.Queue, int] | None:
    """Live workers per queue (``run_queue.live_consumer_counts``), or None
    when Valkey can't be read -- the page then shows no estimate rather than
    failing to load."""
    if not run_queue.is_configured():
        return None
    try:
        return run_queue.live_consumer_counts()
    except redis.exceptions.RedisError as e:
        # No host:port in the log line's message (the exception text can
        # carry it); the type is enough to tell an outage from a bug.
        logger.warning("queue worker counts unavailable: %s", type(e).__name__)
        return None


def estimate_wait_minutes(load: _LaneLoad, workers: int | None, median_seconds: float | None) -> int | None:
    """(waiting + running) x median run time / workers, rounded up to whole
    minutes (spec endpoint 6); None when workers or the median is unknown."""
    if not workers or median_seconds is None:
        return None
    seconds = (load.waiting + load.running) * median_seconds / workers
    return math.ceil(seconds / _SECONDS_PER_MINUTE)


def queue_overview(db: Session) -> QueueOverview:
    """GET /api/queue: the dispatch mode, and per queue its live workers,
    the runs waiting in it and the estimated wait for a new run."""
    mode = concurrency.dispatch_mode()
    can_email = mailer.sending_enabled()
    if mode != "queue":
        return QueueOverview(dispatch_mode=mode, completion_email_available=can_email)
    loads = _lane_loads(db)
    median_seconds = recent_median_duration_seconds(db)
    live = _live_workers()
    lanes = {}
    for queue, load in loads.items():
        workers = live[queue] if live is not None else None
        lanes[queue.name] = QueueLane(
            workers=workers,
            ahead=load.waiting,
            est_wait_minutes=estimate_wait_minutes(load, workers, median_seconds),
        )
    return QueueOverview(dispatch_mode=mode, single=lanes[run_queue.SINGLE.name], batch=lanes[run_queue.BATCH.name],
                         completion_email_available=can_email)
