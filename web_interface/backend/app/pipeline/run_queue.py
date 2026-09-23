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
"""
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
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
# A healthy worker holds its entry un-ACKed for the whole run (15-20 min), so
# the reclaim threshold must exceed the longest plausible run or live runs churn.
MIN_IDLE_MS = 45 * 60 * 1000
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

_client_lock = threading.Lock()
_worker_client_instance: redis.Redis | None = None
_producer_client_instance: redis.Redis | None = None
_autoclaim_cursor = "0-0"


class QueueStats(TypedDict):
    stream_length: int
    pending: int | None
    lag: int | None
    consumers: int
    owners: list[dict[str, object]]
    dead: int


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


def ensure_group() -> None:
    """Create the consumer group from id 0 (entries enqueued before any worker
    existed stay visible). Idempotent: BUSYGROUP means it already exists."""
    try:
        _client().xgroup_create(STREAM, GROUP, id="0", mkstream=True)
    except redis.exceptions.ResponseError as e:
        if not str(e).startswith("BUSYGROUP"):
            raise


def enqueue(run_id: str, start_step: int | None = None) -> str:
    """XADD a work token; returns the entry id. Raises on failure."""
    fields = {"run_id": run_id, "enqueued_at": datetime.now(timezone.utc).isoformat()}
    if start_step is not None:
        fields["start_step"] = str(start_step)
    entry_id = _producer_client().xadd(STREAM, fields)
    logger.info("enqueued run_id=%s entry_id=%s", run_id, entry_id)
    return entry_id


def claim_reenqueue_slot(run_id: str) -> bool:
    """True at most once per REENQUEUE_GUARD_TTL_S per run_id. Guards the
    already-queued re-enqueue path against a retry storm."""
    return bool(_producer_client().set(f"{STREAM}:enq:{run_id}", "1", nx=True, ex=REENQUEUE_GUARD_TTL_S))


def read_one(consumer: str) -> tuple[str, dict[str, str]] | None:
    """Block up to BLOCK_MS for one never-delivered entry."""
    result = _client().xreadgroup(GROUP, consumer, {STREAM: ">"}, count=1, block=BLOCK_MS)
    if not result:
        return None
    entry_id, fields = result[0][1][0]
    return entry_id, fields


def autoclaim_one(consumer: str) -> tuple[str, dict[str, str]] | None:
    """Take over one entry another consumer left pending past MIN_IDLE_MS.

    Carries the scan cursor across calls (the server's own cursor, not always
    "0-0") so a PEL larger than one COUNT is walked incrementally instead of
    rescanned from the head every call; the server returns "0-0" again once a
    full cycle completes. Entries reported deleted (the reply's third element:
    trimmed or XDELed while pending) have already left the PEL -- they are not
    XACKed, only logged, so on-call can see a run's token is gone."""
    global _autoclaim_cursor
    result = _client().xautoclaim(STREAM, GROUP, consumer, MIN_IDLE_MS, _autoclaim_cursor, count=1)
    next_cursor, entries = result[0], result[1]
    deleted = result[2] if len(result) > 2 else []
    _autoclaim_cursor = next_cursor
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


def delivery_count(entry_id: str) -> int:
    """How many times the group has delivered this entry (poison-job cap input)."""
    rows = _client().xpending_range(STREAM, GROUP, min=entry_id, max=entry_id, count=1)
    return rows[0]["times_delivered"] if rows else 0


def exceeds_delivery_cap(deliveries: int) -> bool:
    """True once a token has been redelivered more than MAX_DELIVERIES times --
    the poison-job threshold the worker dead-letters on."""
    return deliveries > MAX_DELIVERIES


def ack(entry_id: str) -> None:
    """XACK then XDEL in one transaction: an acked entry leaves both the PEL
    and the stream, so XLEN reads as true backlog (undelivered + pending)."""
    pipe = _client().pipeline(transaction=True)
    pipe.xack(STREAM, GROUP, entry_id)
    pipe.xdel(STREAM, entry_id)
    pipe.execute()


def dead_letter(entry_id: str, fields: dict[str, str]) -> None:
    """Atomically park a poison entry on the dead stream and remove it from the
    live one: XADD dead + XACK + XDEL of the original in one transaction, so a
    crash between steps can neither leave it redeliverable (which would
    dead-letter it a second time) nor leave a duplicate in DEAD_STREAM."""
    pipe = _client().pipeline(transaction=True)
    pipe.xadd(DEAD_STREAM, {**fields, "original_id": entry_id}, maxlen=DEAD_MAXLEN, approximate=True)
    pipe.xack(STREAM, GROUP, entry_id)
    pipe.xdel(STREAM, entry_id)
    pipe.execute()
    logger.error("dead_lettered entry_id=%s run_id=%s", entry_id, fields.get("run_id"))


def requeue(entry_id: str, fields: dict[str, str]) -> str:
    """Hand an in-flight token back to the stream as a fresh, undelivered entry
    and remove the original, in one transaction: XADD the same fields, then
    XACK+XDEL the original. Used when shutdown is signalled between a read and
    a claim decision, so the next worker picks it up within seconds instead of
    waiting out MIN_IDLE_MS."""
    pipe = _client().pipeline(transaction=True)
    pipe.xadd(STREAM, fields)
    pipe.xack(STREAM, GROUP, entry_id)
    pipe.xdel(STREAM, entry_id)
    new_entry_id, _, _ = pipe.execute()
    logger.info("requeued entry_id=%s new_entry_id=%s run_id=%s", entry_id, new_entry_id, fields.get("run_id"))
    return new_entry_id


def live_run_ids() -> set[str]:
    """run_ids with an outstanding token: never delivered (after the group's
    last-delivered-id) or delivered but not yet ACKed. Excludes ACKed history
    -- ``ack``'s XDEL already removes those from the stream -- so a normal
    backlog reads as live, not as a candidate for reconciliation."""
    r = _client()
    try:
        groups = r.xinfo_groups(STREAM)
    except redis.exceptions.ResponseError as e:
        if "no such key" not in str(e).lower():
            raise
        groups = []
    group = next((g for g in groups if g["name"] == GROUP), None)
    if group is None:
        return set()

    run_ids: set[str] = set()
    for _, fields in r.xrange(STREAM, min=f"({group['last-delivered-id']}", max="+"):
        if fields.get("run_id"):
            run_ids.add(fields["run_id"])

    pending_ids = [
        row["message_id"]
        for row in r.xpending_range(STREAM, GROUP, min="-", max="+", count=LIVE_RUN_IDS_PENDING_LIMIT)
    ]
    if pending_ids:
        pipe = r.pipeline(transaction=False)
        for message_id in pending_ids:
            pipe.xrange(STREAM, min=message_id, max=message_id)
        for entries in pipe.execute():
            if entries and entries[0][1].get("run_id"):
                run_ids.add(entries[0][1]["run_id"])
    return run_ids


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


def dispatch_mode() -> str:
    """``in_process`` (today's BackgroundTask) or ``queue``; lives beside the
    other run-control knobs in the ``llm`` config section."""
    return get_config("llm", "CVICHE_DISPATCH_MODE", default="in_process")[0]
