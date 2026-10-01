"""Valkey Streams job queue for pipeline runs (#701).

Deliberately NOT part of ``RedisBroker``. The broker is best-effort by
contract -- a no-op when ``CVICHE_REDIS_URL`` is unset, failures logged and
swallowed -- which is right for event fan-out and the cancel nudge because the
DB stays the source of truth. A queue with that contract would silently drop
jobs whenever Valkey is down, the opposite of its purpose. Every operation
here is required and loud: no URL raises at first use, and a failed XADD
propagates so the producer can revert the run's status and answer 503.

A message is a work token: a pure wake-up naming only ``run_id`` (see
``WorkToken`` below -- an earlier version also carried ``start_step``, but a
redelivered or stale token could then replay an old, already-superseded step;
``runs.resume_from_step``, set by the flip that queued the run, is the only
source of the resume point now). The DB row is the truth and the worker's
conditional claim (``app/worker.py``) decides whether a delivered token may
execute. Delivery is at-least-once: an entry is XACKed (and XDELed -- see
``ack``) after the run finishes (success or handled failure), so only a crash
leaves it pending for XAUTOCLAIM to hand to another worker.

Every key this module builds carries the ``{cviche:runs}`` hash tag, so all of
them hash to the same Valkey Cluster slot -- ElastiCache Serverless enforces
cluster-mode slot rules, and a MULTI/EXEC (``ack``, ``dead_letter``,
``requeue``) spanning two slots fails with CROSSSLOT.

Two queues (#1114), each its own stream, consumer group and dead-letter
stream (``Queue`` below): ``SINGLE``, the original stream every run used
before batch upload, and ``BATCH``, for runs that carry a ``batch_id``
(``queue_for``). Every per-entry operation takes the ``Queue`` the entry was
read from, so an entry is always ACKed, requeued or dead-lettered on its own
stream. A worker reads the queues ``CVICHE_WORKER_STREAMS`` names
(``worker_queues``); unset, that is ``SINGLE`` alone, today's behaviour.
"""
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType
from typing import TypedDict

import redis

from app.config_loader import get_config
from app.services.artifact_service import RUN_ID_RE

logger = logging.getLogger(__name__)


class BadToken(Exception):
    """Raised by ``WorkToken.from_entry`` for a malformed queue entry: a
    missing, empty, or wrong-shape ``run_id``. The caller (``worker.handle``)
    ACKs it without a DB claim -- an entry that names no valid run_id names no
    run to skip."""

    def __init__(self, entry_id: str, fields: dict[str, str]) -> None:
        self.entry_id = entry_id
        self.fields = fields
        super().__init__(f"bad work token entry_id={entry_id!r} fields={fields!r}")


@dataclass(frozen=True, slots=True)
class WorkToken:
    """A validated wake-up: which run to execute next. Pure wake-up by design
    (mrj4001 review, runs.py point 3) -- see the module docstring for why
    ``start_step`` does not live here."""
    entry_id: str
    run_id: str

    @classmethod
    def from_entry(cls, entry_id: str, fields: dict[str, str]) -> WorkToken:
        """Validate one XREADGROUP/XAUTOCLAIM entry into a WorkToken, or raise
        BadToken. ``run_id`` must match ``artifact_service.RUN_ID_RE`` -- the
        same shape the HTTP layer already requires for a path segment (one
        definition of "what a run_id may look like", CODING STANDARDS section
        1.5), reused rather than a second pattern defined here. An
        old-format entry's ``start_step`` field, if present, is ignored
        rather than rejected, so a message enqueued by a not-yet-redeployed
        producer still wakes the worker correctly."""
        run_id = fields.get("run_id", "")
        if not run_id or not RUN_ID_RE.match(run_id):
            raise BadToken(entry_id, fields)
        return cls(entry_id=entry_id, run_id=run_id)

STREAM = "{cviche:runs}"
GROUP = "cviche-workers"
DEAD_STREAM = "{cviche:runs}:dead"
# The batch-run queue (#1114). Same {cviche:runs} hash tag as every other key
# here (see the module docstring), so a MULTI/EXEC on it stays in one slot.
BATCH_STREAM = "{cviche:runs}:batch"
BATCH_GROUP = "cviche-batch-workers"
BATCH_DEAD_STREAM = "{cviche:runs}:batch:dead"


@dataclass(frozen=True, slots=True)
class Queue:
    """One work queue: a stream, the consumer group that reads it, and the
    stream its poison entries are parked on. ``name`` is the short name
    ``CVICHE_WORKER_STREAMS`` and ``GET /api/queue`` use."""
    name: str
    stream: str
    group: str
    dead_stream: str


SINGLE = Queue(name="single", stream=STREAM, group=GROUP, dead_stream=DEAD_STREAM)
BATCH = Queue(name="batch", stream=BATCH_STREAM, group=BATCH_GROUP, dead_stream=BATCH_DEAD_STREAM)
QUEUES_BY_NAME = MappingProxyType({queue.name: queue for queue in (SINGLE, BATCH)})
# CVICHE_WORKER_STREAMS when unset: the single-run queue only, so a worker
# deployed without the setting behaves exactly as it did before #1114.
WORKER_STREAMS_DEFAULT = SINGLE.name


def queue_for(batch_id: str | None) -> Queue:
    """The queue a run's token goes on: ``BATCH`` for a run in a batch,
    ``SINGLE`` otherwise. The one routing rule every producer (``/start``,
    ``/retry``, the already-queued re-enqueue, the queued-run reconciler)
    goes through, so none of them can route a run differently."""
    return BATCH if batch_id else SINGLE


def parse_worker_streams(raw: str) -> tuple[Queue, ...]:
    """``CVICHE_WORKER_STREAMS``: an ordered, comma-separated list of queue
    short names (``single``, ``batch``), case and whitespace ignored, e.g.
    ``single,batch`` for a flex worker. Raises ValueError on an empty list,
    an unknown name or a repeat, so a typo stops the worker at startup
    instead of leaving a queue nobody reads."""
    names = [part.strip().lower() for part in raw.split(",") if part.strip()]
    if not names:
        raise ValueError(f"CVICHE_WORKER_STREAMS={raw!r} names no queue")
    unknown = [name for name in names if name not in QUEUES_BY_NAME]
    if unknown:
        raise ValueError(
            f"CVICHE_WORKER_STREAMS={raw!r}: unknown queue(s) {unknown}; "
            f"expected names from {sorted(QUEUES_BY_NAME)}"
        )
    if len(set(names)) != len(names):
        raise ValueError(f"CVICHE_WORKER_STREAMS={raw!r} repeats a queue")
    return tuple(QUEUES_BY_NAME[name] for name in names)


def worker_queues() -> tuple[Queue, ...]:
    """The queues this worker reads, in priority order (see
    ``parse_worker_streams``)."""
    raw, _ = get_config("redis", "CVICHE_WORKER_STREAMS", default=WORKER_STREAMS_DEFAULT)
    return parse_worker_streams(str(raw))

# DEAD_STREAM has no consumer group and is inspection-only, so bounding it is
# safe. STREAM itself is never trimmed by length: ack() below XDELs each entry
# once it is done, so XLEN already reads as undelivered-plus-pending, not
# unbounded history, without needing an approximate MAXLEN that would drop
# entries no worker has ever seen (#701 run_queue#1).
DEAD_MAXLEN = 1000
# A short-lived guard on the already-queued re-enqueue path: without it, a
# retry storm on one run adds an unbounded number of duplicate tokens for it
# (#701 run_queue#5 / runs.py#5).
REENQUEUE_GUARD_TTL_S = 30
# Generous upper bound on how many pending (delivered, unACKed) entries
# live_run_ids() will enumerate in one call; the live PEL is normally at most
# a few entries, one per in-flight run.
LIVE_RUN_IDS_PENDING_LIMIT = 10_000
# The worker's own run-watchdog timeout (app.worker.RUN_TIMEOUT_S / the
# CVICHE_RUN_TIMEOUT_SECONDS knob). Read directly here rather than imported
# from app.worker -- which has module-level signal-handling/threading setup
# neither run_queue nor run_service should pull in -- so worker.py, MIN_IDLE_MS
# below and run_service's queue-mode stale-run floor (_effective_stale_run_minutes)
# all derive from the SAME single knob instead of each keeping its own copy of
# its default, which could silently drift apart (CODING STANDARDS section 1.5).
# 5400s: prod's measured p99/max run is 3542s (Paul, 2026-09-23), so this default
# is ~1.5x the longest run ever observed.
RUN_TIMEOUT_S_DEFAULT = 5400
_run_timeout_s_cfg, _ = get_config("llm", "CVICHE_RUN_TIMEOUT_SECONDS", default=RUN_TIMEOUT_S_DEFAULT)
RUN_TIMEOUT_S = int(_run_timeout_s_cfg)

# A healthy worker holds its entry un-ACKed for the run's WHOLE duration -- up
# to RUN_TIMEOUT_S, since app.worker's own run watchdog (not this reclaim) is
# what stops a run that outlives its bound -- so the reclaim threshold must
# exceed RUN_TIMEOUT_S itself, not just a typical run's length (15-20 min):
# below it, XAUTOCLAIM steals a still-healthy, still-executing run's entry out
# from under it (B1/N2). 600s of margin on top for delivery/scheduling jitter.
MIN_IDLE_MS = (RUN_TIMEOUT_S + 600) * 1000
BLOCK_MS = 5000
# socket_timeout must exceed BLOCK_MS or every idle XREADGROUP would raise
# instead of legitimately waiting out the blocking read.
WORKER_SOCKET_TIMEOUT_S = BLOCK_MS / 1000 + 5
# The producer client (enqueue, claim_reenqueue_slot) is called from the API
# request path, soon to run in FastAPI's threadpool (#701 runs.py#10): a short
# timeout means a Valkey brownout fails a /start call in about 2s instead of
# tying up a threadpool thread for WORKER_SOCKET_TIMEOUT_S.
PRODUCER_SOCKET_TIMEOUT_S = 2
MAX_DELIVERIES = 3

# XINFO CONSUMERS idle time under which a consumer counts as a live worker
# for GET /api/queue (#1114). Consumers of replaced pods stay registered in the
# group indefinitely (13 consumers for 6 live workers on dev, 2026-10-01);
# a live idle worker re-reads at least every BLOCK_MS, so 30s is 6x margin.
LIVE_CONSUMER_MAX_IDLE_MS = 30_000

_client_lock = threading.Lock()
_worker_client_instance: redis.Redis | None = None
_producer_client_instance: redis.Redis | None = None
# XAUTOCLAIM scan cursor per stream, carried across autoclaim_one calls (see
# its docstring). Only the worker's single read-loop thread writes it.
_autoclaim_cursors: dict[str, str] = {}


class QueueStats(TypedDict):
    stream_length: int
    pending: int | None
    lag: int | None
    consumers: int
    owners: list[dict[str, object]]
    dead: int


def is_configured() -> bool:
    """True when CVICHE_REDIS_URL is set -- the one precondition every
    operation in this module needs. Lets a caller check BEFORE committing any
    state of its own (N3): unconfigured, _build_client below raises a plain
    RuntimeError, not a redis.exceptions.RedisError, so a caller that only
    guards against RedisError would let it escape uncaught -- after whatever
    it already committed."""
    url, _ = get_config("redis", "CVICHE_REDIS_URL", default="")
    return bool(url)


def _build_client(socket_timeout: float) -> redis.Redis:
    url, _ = get_config("redis", "CVICHE_REDIS_URL", default="")
    if not url:
        raise RuntimeError("CVICHE_REDIS_URL is not set; the run queue requires Valkey")
    return redis.Redis.from_url(
        url,
        socket_timeout=socket_timeout,
        socket_connect_timeout=2,
        decode_responses=True,
    )


def _client() -> redis.Redis:
    """Worker-side client: read_one/autoclaim_one/ack/dead_letter/requeue/stats."""
    global _worker_client_instance
    if _worker_client_instance is None:
        with _client_lock:
            if _worker_client_instance is None:
                _worker_client_instance = _build_client(WORKER_SOCKET_TIMEOUT_S)
    return _worker_client_instance


def _producer_client() -> redis.Redis:
    """Producer-side client: enqueue and claim_reenqueue_slot only."""
    global _producer_client_instance
    if _producer_client_instance is None:
        with _client_lock:
            if _producer_client_instance is None:
                _producer_client_instance = _build_client(PRODUCER_SOCKET_TIMEOUT_S)
    return _producer_client_instance


def _reset_client() -> None:
    """Test / config-reload hook: drop both cached clients under the lock."""
    global _worker_client_instance, _producer_client_instance
    with _client_lock:
        _worker_client_instance = None
        _producer_client_instance = None


def ensure_group(queue: Queue = SINGLE) -> None:
    """Create the queue's consumer group from id 0 (entries enqueued before any
    worker existed stay visible). Idempotent: BUSYGROUP means it already exists."""
    try:
        _client().xgroup_create(queue.stream, queue.group, id="0", mkstream=True)
    except redis.exceptions.ResponseError as e:
        if not str(e).startswith("BUSYGROUP"):
            raise


def enqueue(run_id: str, queue: Queue = SINGLE) -> str:
    """XADD a work token onto ``queue`` (route with ``queue_for``); returns the
    entry id. Raises on failure.

    Pure wake-up (N5): naming only ``run_id``, no ``start_step`` -- no
    production caller has passed one since the resume point moved onto
    ``runs.resume_from_step`` (see the module docstring and ``WorkToken``).
    ``WorkToken.from_entry`` still tolerates and ignores a legacy token's
    ``start_step`` field, for a message an old, not-yet-redeployed producer
    enqueued before this parameter was dropped."""
    fields = {"run_id": run_id, "enqueued_at": datetime.now(timezone.utc).isoformat()}
    entry_id = _producer_client().xadd(queue.stream, fields)
    logger.info("enqueued run_id=%s entry_id=%s queue=%s", run_id, entry_id, queue.name)
    return entry_id


def claim_reenqueue_slot(run_id: str) -> bool:
    """True at most once per REENQUEUE_GUARD_TTL_S per run_id. Guards the
    already-queued re-enqueue path against a retry storm."""
    return bool(_producer_client().set(f"{STREAM}:enq:{run_id}", "1", nx=True, ex=REENQUEUE_GUARD_TTL_S))


def read_one(consumer: str, queue: Queue = SINGLE, *, block: bool = True) -> tuple[str, dict[str, str]] | None:
    """One never-delivered entry from ``queue``: blocks up to BLOCK_MS for it,
    or with ``block=False`` returns at once (a multi-queue worker's polling
    pass, see ``app.worker._read_next``)."""
    block_ms = BLOCK_MS if block else None
    result = _client().xreadgroup(queue.group, consumer, {queue.stream: ">"}, count=1, block=block_ms)
    if not result:
        return None
    entry_id, fields = result[0][1][0]
    return entry_id, fields


def autoclaim_one(consumer: str, queue: Queue = SINGLE) -> tuple[str, dict[str, str]] | None:
    """Take over one entry another consumer left pending on ``queue`` past
    MIN_IDLE_MS.

    Carries the scan cursor across calls (the server's own cursor, not always
    "0-0") so a PEL larger than one COUNT is walked incrementally instead of
    rescanned from the head every call; the server returns "0-0" again once a
    full cycle completes. Entries reported deleted (the reply's third element:
    trimmed or XDELed while pending) have already left the PEL -- they are not
    XACKed, only logged, so on-call can see a run's token is gone."""
    cursor = _autoclaim_cursors.get(queue.stream, "0-0")
    result = _client().xautoclaim(queue.stream, queue.group, consumer, MIN_IDLE_MS, cursor, count=1)
    next_cursor, entries = result[0], result[1]
    deleted = result[2] if len(result) > 2 else []
    _autoclaim_cursors[queue.stream] = next_cursor
    for dead_id in deleted:
        logger.warning(
            "pending entry %s was trimmed or deleted from the stream while pending; "
            "its run has no queue-side recovery left",
            dead_id,
        )
    if not entries:
        return None
    entry_id, fields = entries[0]
    return entry_id, fields


def reclaim_own_pending(consumer: str, queue: Queue = SINGLE) -> list[tuple[str, dict[str, str]]]:
    """Every entry already in ``consumer``'s own PEL on ``queue``, reclaimed at
    min_idle=0 -- used at worker startup and after a loop() exception, so a
    same-pod restart (which keeps the same CONSUMER/HOSTNAME) picks its
    crashed-mid-run entries back up immediately instead of waiting out
    MIN_IDLE_MS (RUN_TIMEOUT_S plus margin -- N2 -- in production).

    XPENDING + XCLAIM rather than a fresh XREADGROUP id="0": fakeredis does
    not replay a consumer's own delivery history through id="0" the way real
    Valkey/Redis does, so this is the shape both implementations agree on."""
    rows = _client().xpending_range(
        queue.stream, queue.group, min="-", max="+", count=LIVE_RUN_IDS_PENDING_LIMIT, consumername=consumer,
    )
    ids = [row["message_id"] for row in rows]
    if not ids:
        return []
    return list(_client().xclaim(queue.stream, queue.group, consumer, min_idle_time=0, message_ids=ids))


def delivery_count(entry_id: str, queue: Queue = SINGLE) -> int:
    """How many times the group has delivered this entry (poison-job cap input)."""
    rows = _client().xpending_range(queue.stream, queue.group, min=entry_id, max=entry_id, count=1)
    return rows[0]["times_delivered"] if rows else 0


def exceeds_delivery_cap(deliveries: int) -> bool:
    """True once a token has been redelivered more than MAX_DELIVERIES times --
    the poison-job threshold the worker dead-letters on."""
    return deliveries > MAX_DELIVERIES


def ack(entry_id: str, queue: Queue = SINGLE) -> None:
    """XACK then XDEL in one transaction: an acked entry leaves both the PEL
    and the stream, so XLEN reads as true backlog (undelivered + pending)."""
    pipe = _client().pipeline(transaction=True)
    pipe.xack(queue.stream, queue.group, entry_id)
    pipe.xdel(queue.stream, entry_id)
    pipe.execute()


def dead_letter(entry_id: str, fields: dict[str, str], queue: Queue = SINGLE) -> None:
    """Atomically park a poison entry on the dead stream and remove it from the
    live one: XADD dead + XACK + XDEL of the original in one transaction, so a
    crash between steps can neither leave it redeliverable (which would
    dead-letter it a second time) nor leave a duplicate in DEAD_STREAM."""
    pipe = _client().pipeline(transaction=True)
    pipe.xadd(queue.dead_stream, {**fields, "original_id": entry_id}, maxlen=DEAD_MAXLEN, approximate=True)
    pipe.xack(queue.stream, queue.group, entry_id)
    pipe.xdel(queue.stream, entry_id)
    pipe.execute()
    logger.error("dead_lettered entry_id=%s run_id=%s queue=%s", entry_id, fields.get("run_id"), queue.name)


def requeue(entry_id: str, fields: dict[str, str], queue: Queue = SINGLE) -> str:
    """Hand an in-flight token back to the stream as a fresh, undelivered entry
    and remove the original, in one transaction: XADD the same fields, then
    XACK+XDEL the original. Used when shutdown is signalled between a read and
    a claim decision, so the next worker picks it up within seconds instead of
    waiting out MIN_IDLE_MS."""
    pipe = _client().pipeline(transaction=True)
    pipe.xadd(queue.stream, fields)
    pipe.xack(queue.stream, queue.group, entry_id)
    pipe.xdel(queue.stream, entry_id)
    new_entry_id, _, _ = pipe.execute()
    logger.info("requeued entry_id=%s new_entry_id=%s run_id=%s queue=%s",
                entry_id, new_entry_id, fields.get("run_id"), queue.name)
    return new_entry_id


def live_run_ids() -> set[str]:
    """run_ids with an outstanding token on either queue: never delivered
    (after the group's last-delivered-id) or delivered but not yet ACKed.
    Excludes ACKed history -- ``ack``'s XDEL already removes those from the
    stream -- so a normal backlog reads as live, not as a candidate for
    reconciliation."""
    run_ids: set[str] = set()
    for queue in QUEUES_BY_NAME.values():
        run_ids |= _live_run_ids_in(queue)
    return run_ids


def _live_run_ids_in(queue: Queue) -> set[str]:
    """``live_run_ids`` for one queue."""
    r = _client()
    try:
        groups = r.xinfo_groups(queue.stream)
    except redis.exceptions.ResponseError as e:
        if "no such key" not in str(e).lower():
            raise
        groups = []
    group = next((g for g in groups if g["name"] == queue.group), None)
    if group is None:
        return set()

    run_ids: set[str] = set()
    for _, fields in r.xrange(queue.stream, min=f"({group['last-delivered-id']}", max="+"):
        if fields.get("run_id"):
            run_ids.add(fields["run_id"])

    pending_ids = [
        row["message_id"]
        for row in r.xpending_range(queue.stream, queue.group, min="-", max="+", count=LIVE_RUN_IDS_PENDING_LIMIT)
    ]
    if pending_ids:
        pipe = r.pipeline(transaction=False)
        for message_id in pending_ids:
            pipe.xrange(queue.stream, min=message_id, max=message_id)
        for entries in pipe.execute():
            if entries and entries[0][1].get("run_id"):
                run_ids.add(entries[0][1]["run_id"])
    return run_ids


def live_consumer_count(queue: Queue) -> int:
    """Workers currently reading ``queue``, for GET /api/queue (#1114): its
    group's consumers seen within LIVE_CONSUMER_MAX_IDLE_MS, plus any holding
    a pending entry -- a worker mid-run does not touch the stream for the
    run's whole 15-20 minutes, so idle time alone would drop every busy
    worker. A crashed pod's consumer can still hold a pending entry until
    XAUTOCLAIM moves it (MIN_IDLE_MS), the one window this overcounts. No
    stream or no group yet reads as 0 workers, never an error."""
    try:
        consumers = _client().xinfo_consumers(queue.stream, queue.group)
    except redis.exceptions.ResponseError as e:
        message = str(e).lower()
        if "no such key" not in message and "nogroup" not in message:
            raise
        return 0
    return sum(
        1 for consumer in consumers
        if consumer["idle"] < LIVE_CONSUMER_MAX_IDLE_MS or consumer["pending"] > 0
    )


def stats() -> QueueStats:
    """Read-only depth and ownership for the admin endpoint. Never calls
    ensure_group(): a GET must not mask "no worker has ever started" by
    creating the stream and group as a side effect. A missing stream or group
    reads as pending=None/lag=None/consumers=0/owners=[]; any other
    ResponseError still raises.

    xpending() is read separately from the xlen/xinfo_groups pipeline rather
    than folded into it: fakeredis 2.35.1 with redis-py 7.4.0 (this repo's
    pinned test combination) raises a client-side IndexError -- not
    ResponseError -- parsing XPENDING's reply when the group does not exist,
    which would abort the whole pipelined read for exactly the "queue never
    started" case this rewrite exists to report cleanly."""
    r = _client()
    pipe = r.pipeline(transaction=False)
    pipe.xlen(STREAM)
    pipe.xinfo_groups(STREAM)
    pipe.xlen(DEAD_STREAM)
    try:
        stream_length, groups, dead = pipe.execute()
    except redis.exceptions.ResponseError as e:
        if "no such key" not in str(e).lower():
            raise
        stream_length, groups, dead = r.xlen(STREAM), [], r.xlen(DEAD_STREAM)

    group = next((g for g in groups if g["name"] == GROUP), None)
    owners: list[dict[str, object]] = []
    if group is not None:
        owners = r.xpending(STREAM, GROUP)["consumers"] or []

    return QueueStats(
        stream_length=stream_length,
        pending=group["pending"] if group else None,
        lag=group.get("lag") if group else None,
        consumers=group["consumers"] if group else 0,
        owners=owners,
        dead=dead,
    )
