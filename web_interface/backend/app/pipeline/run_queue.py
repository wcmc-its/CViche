"""Valkey Streams job queue for pipeline runs (#701).

Deliberately NOT part of ``RedisBroker``. The broker is best-effort by
contract -- a no-op when ``CVICHE_REDIS_URL`` is unset, failures logged and
swallowed -- which is right for event fan-out and the cancel nudge because the
DB stays the source of truth. A queue with that contract would silently drop
jobs whenever Valkey is down, the opposite of its purpose. Every operation
here is required and loud: no URL raises at first use, and a failed XADD
propagates so the producer can revert the run's status and answer 503.

A message is a work token (``run_id`` + optional ``start_step``); the DB row
is the truth and the worker's conditional claim (``app/worker.py``) decides
whether a delivered token may execute. Delivery is at-least-once: an entry is
XACKed after the run finishes (success or handled failure), so only a crash
leaves it pending for XAUTOCLAIM to hand to another worker.
"""
import logging
from datetime import datetime

import redis

from app.config_loader import get_config

logger = logging.getLogger(__name__)

STREAM = "cviche:runs:queue"
GROUP = "cviche-workers"
DEAD_STREAM = "cviche:runs:dead"
MAXLEN = 1000
# A healthy worker holds its entry un-ACKed for the whole run (15-20 min), so
# the reclaim threshold must exceed the longest plausible run or live runs churn.
MIN_IDLE_MS = 45 * 60 * 1000
BLOCK_MS = 5000
MAX_DELIVERIES = 3

_client_instance: redis.Redis | None = None


def _client() -> redis.Redis:
    """One lazily-built sync client. Loud when the URL is unset."""
    global _client_instance
    if _client_instance is None:
        url, _ = get_config("redis", "CVICHE_REDIS_URL", default="")
        if not url:
            raise RuntimeError("CVICHE_REDIS_URL is not set; the run queue requires Valkey")
        # socket_timeout bounds every command so a hung Valkey raises instead of
        # wedging the caller -- but XREADGROUP BLOCK legitimately waits BLOCK_MS,
        # so the timeout must be longer than that or every idle read would raise.
        _client_instance = redis.Redis.from_url(
            url,
            socket_timeout=BLOCK_MS / 1000 + 5,
            socket_connect_timeout=2,
            decode_responses=True,
        )
    return _client_instance


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
    fields = {"run_id": run_id, "enqueued_at": datetime.now().isoformat()}
    if start_step is not None:
        fields["start_step"] = str(start_step)
    return _client().xadd(STREAM, fields, maxlen=MAXLEN, approximate=True)


def read_one(consumer: str) -> tuple[str, dict[str, str]] | None:
    """Block up to BLOCK_MS for one never-delivered entry."""
    result = _client().xreadgroup(GROUP, consumer, {STREAM: ">"}, count=1, block=BLOCK_MS)
    if not result:
        return None
    entry_id, fields = result[0][1][0]
    return entry_id, fields


def autoclaim_one(consumer: str) -> tuple[str, dict[str, str]] | None:
    """Take over one entry another consumer left pending past MIN_IDLE_MS."""
    result = _client().xautoclaim(STREAM, GROUP, consumer, MIN_IDLE_MS, "0-0", count=1)
    # redis >= 7 replies (next_id, entries, deleted_ids); older servers omit
    # the third element. Only the entries list matters here.
    entries = result[1]
    if not entries:
        return None
    entry_id, fields = entries[0]
    return entry_id, fields


def delivery_count(entry_id: str) -> int:
    """How many times the group has delivered this entry (poison-job cap input)."""
    rows = _client().xpending_range(STREAM, GROUP, min=entry_id, max=entry_id, count=1)
    return rows[0]["times_delivered"] if rows else 0


def ack(entry_id: str) -> None:
    _client().xack(STREAM, GROUP, entry_id)


def dead_letter(entry_id: str, fields: dict[str, str]) -> None:
    """Park an entry that keeps crashing workers where a human can XRANGE it,
    then ACK the original so it is never redelivered."""
    _client().xadd(DEAD_STREAM, {**fields, "original_id": entry_id}, maxlen=MAXLEN, approximate=True)
    ack(entry_id)


def stats() -> dict[str, object]:
    """Depth and ownership for the admin endpoint. ``queued`` is XLEN, which
    counts ACKed entries too until MAXLEN trims them; ``pending`` is the live
    delivered-but-unACKed count."""
    ensure_group()
    r = _client()
    summary = r.xpending(STREAM, GROUP)
    return {
        "queued": r.xlen(STREAM),
        "pending": summary["pending"],
        "consumers": summary["consumers"],
        "dead": r.xlen(DEAD_STREAM),
    }


def dispatch_mode() -> str:
    """``in_process`` (today's BackgroundTask) or ``queue``; lives beside the
    other run-control knobs in the ``llm`` config section."""
    return get_config("llm", "CVICHE_DISPATCH_MODE", default="in_process")[0]
