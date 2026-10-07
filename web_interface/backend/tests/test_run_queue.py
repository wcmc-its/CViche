"""Run queue (#701): the stream module and its only producers -- /start, /retry,
/cancel in ``CVICHE_DISPATCH_MODE=queue``.

Redis is fakeredis, injected by replacing ``run_queue._client`` and
``run_queue._producer_client`` (the module's two client seams -- see
``run_queue._client``'s docstring). The API tests go through the real router
with ``get_current_user`` overridden so the status code (202 for a queued
dispatch) and the enqueue contract are exercised at the wire, not on a helper.
"""
import os
import threading
from datetime import datetime, timezone

from unittest.mock import MagicMock

import fakeredis
import pytest
import redis

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

from app.pipeline import run_queue  # noqa: E402

SEEDED_AT = datetime(2026, 1, 1, 0, 0, 0)


@pytest.fixture
def fake_redis(monkeypatch):
    r = fakeredis.FakeStrictRedis(server=fakeredis.FakeServer(), decode_responses=True)
    monkeypatch.setattr(run_queue, "_client", lambda: r)
    monkeypatch.setattr(run_queue, "_producer_client", lambda: r)
    monkeypatch.setattr(run_queue, "_autoclaim_cursors", {})
    # N3's is_configured() check reads CVICHE_REDIS_URL directly (independent
    # of the client-factory monkeypatches above), so it must see something set.
    monkeypatch.setenv("CVICHE_REDIS_URL", "redis://fake-valkey:6379/0")
    return r


@pytest.fixture
def queue_mode(monkeypatch):
    monkeypatch.setenv("CVICHE_DISPATCH_MODE", "queue")


def _entries(r):
    return [fields for _, fields in r.xrange(run_queue.STREAM)]


# --- module ----------------------------------------------------------------

def test_fakeredis_supports_every_stream_primitive_the_queue_uses(fake_redis):
    """Canary: a fakeredis upgrade that drops one of these must fail here, not
    silently turn the worker tests vacuous."""
    run_queue.ensure_group()
    run_queue.ensure_group()  # BUSYGROUP tolerated
    eid = run_queue.enqueue("CANARY")
    assert run_queue.read_one("a") == (eid, {"run_id": "CANARY", "enqueued_at": _entries(fake_redis)[0]["enqueued_at"]})
    assert run_queue.delivery_count(eid) == 1
    assert run_queue.read_one("a") is None
    fake_redis.xclaim(run_queue.STREAM, run_queue.GROUP, "b", 0, [eid])
    assert run_queue.delivery_count(eid) == 2
    assert fake_redis.xinfo_groups(run_queue.STREAM)[0]["name"] == run_queue.GROUP
    assert run_queue.stats()["pending"] == 1
    run_queue.ack(eid)
    assert run_queue.stats() == {
        "stream_length": 0, "pending": 0, "lag": -1, "consumers": 2, "owners": [], "dead": 0,
    }


def test_every_key_the_module_builds_shares_the_cviche_runs_hash_tag():
    """ElastiCache Serverless is cluster-mode: a MULTI/EXEC (ack, dead_letter,
    requeue) spanning two hash slots fails CROSSSLOT. All three keys this
    module ever XADDs/SETs to must carry the same {cviche:runs} hash tag."""
    guard_key = f"{run_queue.STREAM}:enq:SOME_RUN"
    keys = (run_queue.STREAM, run_queue.DEAD_STREAM, run_queue.BATCH_STREAM, run_queue.BATCH_DEAD_STREAM, guard_key)
    for key in keys:
        assert key.startswith("{cviche:runs}"), key
    assert len(set(keys)) == len(keys), "the batch queue must not share a stream with the single-run queue"


# --- two queues (#1114) -------------------------------------------------------

def _batch_entries(r):
    return [fields for _, fields in r.xrange(run_queue.BATCH_STREAM)]


@pytest.mark.parametrize("batch_files_submitted, expected", [
    (None, "single"),  # no batch
    (1, "single"),     # a single upload with "Email me when job completes" ticked (#1340)
    (2, "batch"),
    (50, "batch"),
])
def test_queue_for_routes_only_a_bulk_batch_run_to_the_batch_queue(batch_files_submitted, expected):
    assert run_queue.queue_for(batch_files_submitted).name == expected


def test_the_batch_queue_has_its_own_stream_group_and_dead_stream():
    single, batch = run_queue.SINGLE, run_queue.BATCH
    assert (single.stream, single.group, single.dead_stream) == (run_queue.STREAM, run_queue.GROUP, run_queue.DEAD_STREAM)
    assert batch.stream != single.stream
    assert batch.group != single.group
    assert batch.dead_stream != single.dead_stream


def test_enqueue_on_the_batch_queue_writes_only_the_batch_stream(fake_redis):
    run_queue.enqueue("BQ1", run_queue.BATCH)
    assert [e["run_id"] for e in _batch_entries(fake_redis)] == ["BQ1"]
    assert _entries(fake_redis) == []


def test_batch_queue_read_ack_and_delivery_count_stay_on_the_batch_stream(fake_redis):
    run_queue.ensure_group()
    run_queue.ensure_group(run_queue.BATCH)
    eid = run_queue.enqueue("BQ2", run_queue.BATCH)
    assert run_queue.read_one("a", run_queue.SINGLE, block=False) is None
    entry_id, fields = run_queue.read_one("a", run_queue.BATCH)
    assert (entry_id, fields["run_id"]) == (eid, "BQ2")
    assert run_queue.delivery_count(eid, run_queue.BATCH) == 1
    run_queue.ack(eid, run_queue.BATCH)
    assert _batch_entries(fake_redis) == []
    assert run_queue.delivery_count(eid, run_queue.BATCH) == 0


def test_batch_queue_autoclaim_and_own_pel_reclaim_read_the_batch_group(fake_redis, monkeypatch):
    run_queue.ensure_group()
    run_queue.ensure_group(run_queue.BATCH)
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)
    eid = run_queue.enqueue("BQ3", run_queue.BATCH)
    run_queue.read_one("a", run_queue.BATCH)
    assert [e for e, _ in run_queue.reclaim_own_pending("a", run_queue.BATCH)] == [eid]
    assert run_queue.autoclaim_one("b", run_queue.SINGLE) is None
    assert run_queue.autoclaim_one("b", run_queue.BATCH)[0] == eid


def test_batch_queue_requeue_puts_the_token_back_on_the_batch_stream(fake_redis):
    run_queue.ensure_group(run_queue.BATCH)
    eid = run_queue.enqueue("BQ4", run_queue.BATCH)
    _, fields = run_queue.read_one("a", run_queue.BATCH)
    new_id = run_queue.requeue(eid, fields, run_queue.BATCH)
    assert [e["run_id"] for e in _batch_entries(fake_redis)] == ["BQ4"]
    assert _entries(fake_redis) == []
    assert run_queue.read_one("b", run_queue.BATCH)[0] == new_id


def test_batch_queue_dead_letter_parks_on_the_batch_dead_stream(fake_redis):
    run_queue.ensure_group(run_queue.BATCH)
    eid = run_queue.enqueue("BQ5", run_queue.BATCH)
    _, fields = run_queue.read_one("a", run_queue.BATCH)
    run_queue.dead_letter(eid, fields, run_queue.BATCH)
    assert [d[1]["run_id"] for d in fake_redis.xrange(run_queue.BATCH_DEAD_STREAM)] == ["BQ5"]
    assert fake_redis.xlen(run_queue.DEAD_STREAM) == 0
    assert _batch_entries(fake_redis) == []


def test_read_one_without_block_sends_no_block_argument(fake_redis):
    """A flex worker's polling pass must not wait BLOCK_MS on each queue in
    turn, or a single run arriving meanwhile waits behind the batch poll.
    Pinned on the XREADGROUP arguments: fakeredis answers an empty blocking
    read at once, so elapsed time can't tell the two apart."""
    run_queue.ensure_group(run_queue.BATCH)
    sent_block = []
    real_xreadgroup = fake_redis.xreadgroup

    def spy(*args, **kwargs):
        sent_block.append(kwargs.get("block"))
        return real_xreadgroup(*args, **kwargs)
    fake_redis.xreadgroup = spy
    try:
        assert run_queue.read_one("a", run_queue.BATCH, block=False) is None
        assert run_queue.read_one("a", run_queue.BATCH) is None
    finally:
        del fake_redis.xreadgroup
    assert sent_block == [None, run_queue.BLOCK_MS]


def test_live_run_ids_covers_the_batch_queue(fake_redis):
    run_queue.ensure_group()
    run_queue.ensure_group(run_queue.BATCH)
    run_queue.enqueue("SINGLE1")
    run_queue.enqueue("BATCH1", run_queue.BATCH)
    run_queue.enqueue("BATCH2", run_queue.BATCH)
    run_queue.read_one("a", run_queue.BATCH)  # BATCH1 pending, BATCH2 undelivered
    assert run_queue.live_run_ids() == {"SINGLE1", "BATCH1", "BATCH2"}


@pytest.mark.parametrize("raw, expected", [
    ("single", ("single",)),
    ("single,batch", ("single", "batch")),
    (" Batch , SINGLE ", ("batch", "single")),
])
def test_parse_worker_streams_keeps_the_listed_order(raw, expected):
    assert tuple(q.name for q in run_queue.parse_worker_streams(raw)) == expected


@pytest.mark.parametrize("raw", ["", " , ", "single,bogus", "single,single"])
def test_parse_worker_streams_rejects_an_empty_unknown_or_repeated_list(raw):
    with pytest.raises(ValueError, match="CVICHE_WORKER_STREAMS"):
        run_queue.parse_worker_streams(raw)


def test_worker_queues_default_is_the_single_run_queue_only(monkeypatch):
    monkeypatch.delenv("CVICHE_WORKER_STREAMS", raising=False)
    assert run_queue.worker_queues() == (run_queue.SINGLE,)
    monkeypatch.setenv("CVICHE_WORKER_STREAMS", "single,batch")
    assert run_queue.worker_queues() == (run_queue.SINGLE, run_queue.BATCH)


def test_live_consumer_counts_are_zero_before_any_group_exists(fake_redis):
    assert run_queue.live_consumer_counts() == {run_queue.SINGLE: 0, run_queue.BATCH: 0}


def test_live_consumer_counts_count_recent_consumers_and_busy_ones_only(fake_redis, monkeypatch):
    """Stale consumers of replaced pods stay in the group: only one seen
    within LIVE_CONSUMER_MAX_IDLE_MS counts -- or one holding a pending entry,
    since a worker mid-run does not touch the stream for the whole run."""
    run_queue.ensure_group()
    run_queue.enqueue("LC1")
    run_queue.read_one("busy")                      # holds LC1 pending
    run_queue.read_one("idle", block=False)         # registered, nothing pending
    assert run_queue.live_consumer_counts()[run_queue.SINGLE] == 2
    monkeypatch.setattr(run_queue, "LIVE_CONSUMER_MAX_IDLE_MS", 0)  # every consumer now reads as stale
    assert run_queue.live_consumer_counts()[run_queue.SINGLE] == 1


def test_a_worker_busy_on_one_queue_still_counts_on_every_queue_it_reads(fake_redis, monkeypatch):
    """A flex worker mid-run on a single run holds that entry pending in the
    single group and touches the batch group not at all, so past
    LIVE_CONSUMER_MAX_IDLE_MS it is idle there -- yet it is a batch worker
    the moment the run ends. A replaced pod, live in no group, never counts."""
    run_queue.ensure_group(run_queue.SINGLE)
    run_queue.ensure_group(run_queue.BATCH)
    for n in range(3):
        run_queue.enqueue(f"FLEX{n}")
    for name in ("flex-1", "flex-2", "flex-3"):
        run_queue.read_one(name, run_queue.SINGLE, block=False)
        run_queue.read_one(name, run_queue.BATCH, block=False)
    run_queue.read_one("replaced-pod", run_queue.BATCH, block=False)
    monkeypatch.setattr(run_queue, "LIVE_CONSUMER_MAX_IDLE_MS", 0)  # 30s have passed for every consumer

    assert run_queue.live_consumer_counts() == {run_queue.SINGLE: 3, run_queue.BATCH: 3}


def test_enqueue_does_not_trim_the_stream_past_1000_entries(fake_redis):
    """#701 run_queue point 1: XADD MAXLEN silently dropped queued runs never
    delivered to any worker. There is no length trim on enqueue any more --
    ack() bounds the stream by deleting finished entries instead."""
    for n in range(1200):
        run_queue.enqueue(f"R{n}")
    assert fake_redis.xlen(run_queue.STREAM) == 1200
    assert _entries(fake_redis)[0]["run_id"] == "R0"


def test_ack_removes_the_entry_from_the_pel_and_the_stream(fake_redis):
    run_queue.ensure_group()
    eid = run_queue.enqueue("ACK1")
    run_queue.read_one("a")
    run_queue.ack(eid)
    assert _entries(fake_redis) == []
    assert run_queue.stats()["pending"] == 0


def test_autoclaim_returns_the_idle_entry_and_none_when_nothing_is_pending(fake_redis, monkeypatch):
    run_queue.ensure_group()
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)
    assert run_queue.autoclaim_one("b") is None
    eid = run_queue.enqueue("AC1")
    run_queue.read_one("a")
    assert run_queue.autoclaim_one("b") == (eid, {"run_id": "AC1", "enqueued_at": _entries(fake_redis)[0]["enqueued_at"]})
    run_queue.ack(eid)
    assert run_queue.autoclaim_one("b") is None


def test_autoclaim_logs_and_skips_a_deleted_pel_entry_without_acking(fake_redis, monkeypatch, caplog):
    """A pending entry can vanish from the stream (XDEL) while still owed to a
    consumer; XAUTOCLAIM itself drops it from the PEL and reports it in the
    reply's third element. It must not be XACKed -- it already left the PEL --
    only logged, so on-call can see the run's token is gone."""
    run_queue.ensure_group()
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)
    eid = run_queue.enqueue("GONE1")
    run_queue.read_one("a")
    fake_redis.xdel(run_queue.STREAM, eid)
    with caplog.at_level("WARNING"):
        assert run_queue.autoclaim_one("b") is None
    assert eid in caplog.text
    assert run_queue.stats()["pending"] == 0


def test_autoclaim_carries_its_cursor_across_calls(fake_redis, monkeypatch):
    """The cursor from one XAUTOCLAIM reply feeds the next call instead of
    every call rescanning the PEL from '0-0'."""
    run_queue.ensure_group()
    monkeypatch.setattr(run_queue, "MIN_IDLE_MS", 0)
    eids = [run_queue.enqueue(f"CUR{n}") for n in range(3)]
    for _ in eids:
        run_queue.read_one("a")
    assert run_queue.autoclaim_one("b") is not None
    assert run_queue._autoclaim_cursors[run_queue.STREAM] != "0-0"


def test_reclaim_own_pending_returns_this_consumers_pel_entries_immediately(fake_redis):
    """#701 worker.py point 3 (A3 own-PEL reclaim): independent of
    MIN_IDLE_MS -- a same-pod restart must not wait for the idle threshold to
    get its own crashed-mid-run entry back."""
    run_queue.ensure_group()
    run_queue.enqueue("OWN1")
    run_queue.read_one("w1")  # delivered to w1, never claimed or ACKed

    entries = run_queue.reclaim_own_pending("w1")

    assert [fields["run_id"] for _, fields in entries] == ["OWN1"]
    assert run_queue.stats()["pending"] == 1, "reclaim only re-owns it -- the caller still ACKs it"


def test_reclaim_own_pending_is_empty_for_a_consumer_with_no_pel_entries(fake_redis):
    run_queue.ensure_group()
    assert run_queue.reclaim_own_pending("nobody") == []


def test_dead_letter_parks_the_entry_and_acks_the_original(fake_redis):
    run_queue.ensure_group()
    eid = run_queue.enqueue("DL1")
    _, fields = run_queue.read_one("a")
    run_queue.dead_letter(eid, fields)
    dead = fake_redis.xrange(run_queue.DEAD_STREAM)
    assert len(dead) == 1 and dead[0][1]["run_id"] == "DL1" and dead[0][1]["original_id"] == eid
    assert run_queue.stats()["pending"] == 0
    assert _entries(fake_redis) == []


def test_dead_letter_leaves_both_streams_unchanged_on_a_pipeline_failure(fake_redis):
    """A crash mid-transaction must not leave the entry dead-lettered without
    also being acked (or vice versa): the two streams are only ever changed
    together. Patches only the instance's own ``pipeline`` attribute (not via
    ``monkeypatch``, whose ``undo()`` would also unwind the fixture's own
    ``_client``/``_producer_client`` patches) and restores it in a finally."""
    run_queue.ensure_group()
    eid = run_queue.enqueue("DL2")
    _, fields = run_queue.read_one("a")

    class _BoomPipe:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def xadd(self, *a, **k):
            return self

        def xack(self, *a, **k):
            return self

        def xdel(self, *a, **k):
            return self

        def execute(self):
            raise redis.exceptions.ConnectionError("boom mid-transaction")

    fake_redis.pipeline = lambda transaction=True: _BoomPipe()
    try:
        with pytest.raises(redis.exceptions.ConnectionError):
            run_queue.dead_letter(eid, fields)
    finally:
        del fake_redis.pipeline
    assert fake_redis.xlen(run_queue.DEAD_STREAM) == 0
    assert run_queue.stats()["pending"] == 1


def test_exceeds_delivery_cap_boundary():
    assert run_queue.exceeds_delivery_cap(run_queue.MAX_DELIVERIES) is False
    assert run_queue.exceeds_delivery_cap(run_queue.MAX_DELIVERIES + 1) is True


def test_enqueued_at_is_utc(fake_redis):
    run_queue.enqueue("TZ1")
    stamp = _entries(fake_redis)[0]["enqueued_at"]
    assert datetime.fromisoformat(stamp).tzinfo == timezone.utc


def test_claim_reenqueue_slot_is_one_shot_per_run_within_the_ttl(fake_redis):
    assert run_queue.claim_reenqueue_slot("R1") is True
    assert run_queue.claim_reenqueue_slot("R1") is False
    assert run_queue.claim_reenqueue_slot("R2") is True
    assert fake_redis.ttl("{cviche:runs}:enq:R1") == run_queue.REENQUEUE_GUARD_TTL_S


def test_requeue_replaces_the_entry_with_an_undelivered_one(fake_redis):
    run_queue.ensure_group()
    eid = run_queue.enqueue("RQ1")
    _, fields = run_queue.read_one("a")
    new_id = run_queue.requeue(eid, fields)
    assert new_id != eid
    assert _entries(fake_redis) == [{"run_id": "RQ1", "enqueued_at": fields["enqueued_at"]}]
    assert run_queue.stats()["pending"] == 0
    # the new entry is undelivered: a fresh read still sees it
    assert run_queue.read_one("b") == (new_id, fields)


def test_live_run_ids_covers_undelivered_and_pending_but_not_acked(fake_redis):
    run_queue.ensure_group()
    run_queue.enqueue("PENDING")
    acked_eid = run_queue.enqueue("ACKED")
    run_queue.enqueue("UNDELIVERED")
    run_queue.read_one("a")  # delivers PENDING
    run_queue.read_one("a")  # delivers ACKED
    run_queue.ack(acked_eid)
    assert run_queue.live_run_ids() == {"PENDING", "UNDELIVERED"}


def test_live_run_ids_is_empty_when_no_group_exists(fake_redis):
    assert run_queue.live_run_ids() == set()


def test_stats_exposes_lag_after_enqueue_and_a_read(fake_redis):
    run_queue.ensure_group()
    run_queue.enqueue("LAG1")
    run_queue.enqueue("LAG2")
    run_queue.read_one("a")
    body = run_queue.stats()
    assert body["lag"] == 1
    assert body["pending"] == 1


def test_stats_does_not_create_the_stream_or_group(fake_redis):
    """A GET must not create the stream/group as a side effect -- that would
    mask 'no worker has ever started' on the very next call."""
    assert fake_redis.exists(run_queue.STREAM) == 0
    body = run_queue.stats()
    assert body == {"stream_length": 0, "pending": None, "lag": None, "consumers": 0, "owners": [], "dead": 0}
    assert fake_redis.exists(run_queue.STREAM) == 0


def test_stats_reraises_an_unexpected_response_error(fake_redis):
    """The guard is pinned to the 'no such key' string, not to ResponseError in
    general -- a different server error (WRONGTYPE, say) must still surface.
    A stub ``pipeline()`` stands in for the client's real one: patching the
    client instance's own ``xinfo_groups`` does not reach a pipeline's queued
    commands, which are a separate object (confirmed empirically against this
    repo's pinned fakeredis 2.35.1 / redis-py 7.4.0)."""
    class _BoomPipe:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def xlen(self, *a, **k):
            return self

        def xinfo_groups(self, *a, **k):
            return self

        def execute(self):
            raise redis.exceptions.ResponseError("WRONGTYPE Operation against a key holding the wrong kind of value")

    fake_redis.pipeline = lambda transaction=True: _BoomPipe()
    try:
        with pytest.raises(redis.exceptions.ResponseError, match="WRONGTYPE"):
            run_queue.stats()
    finally:
        del fake_redis.pipeline


def test_client_is_loud_when_redis_url_is_unset(monkeypatch):
    monkeypatch.delenv("CVICHE_REDIS_URL", raising=False)
    run_queue._reset_client()
    with pytest.raises(RuntimeError, match="CVICHE_REDIS_URL"):
        run_queue._client()
    run_queue._reset_client()


def test_producer_client_uses_a_short_socket_timeout(monkeypatch):
    """#701 runs.py point 10: the producer client must fail fast (about 2s)
    rather than block a threadpool thread for the worker's ~10s BLOCK_MS
    timeout during a Valkey brownout."""
    monkeypatch.setenv("CVICHE_REDIS_URL", "redis://localhost:0")
    run_queue._reset_client()
    try:
        assert run_queue._producer_client().get_connection_kwargs()["socket_timeout"] == run_queue.PRODUCER_SOCKET_TIMEOUT_S
        assert run_queue._client().get_connection_kwargs()["socket_timeout"] == run_queue.WORKER_SOCKET_TIMEOUT_S
        assert run_queue.PRODUCER_SOCKET_TIMEOUT_S < run_queue.WORKER_SOCKET_TIMEOUT_S
    finally:
        run_queue._reset_client()


def test_min_idle_ms_exceeds_the_run_watchdog_timeout(monkeypatch):
    """N2: a healthy worker holds its entry un-ACKed for the run's whole
    duration -- up to RUN_TIMEOUT_S, since app.worker's own run watchdog (not
    this reclaim threshold) is what stops a run that outlives its bound. If
    MIN_IDLE_MS sat at or below RUN_TIMEOUT_S, XAUTOCLAIM would steal a still
    healthy, still-executing run's entry out from under it well before the
    watchdog ever gets a chance to fire."""
    assert run_queue.MIN_IDLE_MS > run_queue.RUN_TIMEOUT_S * 1000
    assert run_queue.MIN_IDLE_MS == (run_queue.RUN_TIMEOUT_S + 600) * 1000


def test_worker_run_timeout_s_is_sourced_from_run_queue_not_read_independently():
    """N2: app.worker.RUN_TIMEOUT_S and run_queue.MIN_IDLE_MS must trace back
    to the exact same CVICHE_RUN_TIMEOUT_SECONDS read -- two independent
    config reads could drift apart on a reload race, silently reopening the
    same gap this fix closes."""
    from app import worker
    assert worker.RUN_TIMEOUT_S == run_queue.RUN_TIMEOUT_S


def test_client_build_is_locked_against_concurrent_first_use(monkeypatch):
    """A double-checked lock: concurrent first callers under the threadpool
    (point 2) must share one client instance, not each build and leak one."""
    monkeypatch.setenv("CVICHE_REDIS_URL", "redis://localhost:0")
    run_queue._reset_client()
    built = []
    real_build = run_queue._build_client

    def slow_build(timeout):
        import time
        time.sleep(0.05)
        c = real_build(timeout)
        built.append(c)
        return c

    monkeypatch.setattr(run_queue, "_build_client", slow_build)
    try:
        threads = [threading.Thread(target=run_queue._client) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(built) == 1
        assert len({id(run_queue._client()) for _ in range(4)}) == 1
    finally:
        run_queue._reset_client()


# --- WorkToken (#701 runs.py point 3: pure wake-up) -------------------------

def test_work_token_from_entry_accepts_a_well_formed_run_id():
    token = run_queue.WorkToken.from_entry("1-1", {"run_id": "ABC123"})
    assert token.entry_id == "1-1"
    assert token.run_id == "ABC123"


def test_work_token_from_entry_ignores_a_legacy_start_step_field():
    """An old-format entry (enqueued by a not-yet-redeployed producer) still
    wakes the worker: start_step rides along but is not part of WorkToken --
    the DB's resume_from_step is the only source of the resume point now."""
    token = run_queue.WorkToken.from_entry("1-1", {"run_id": "ABC123", "start_step": "3"})
    assert token == run_queue.WorkToken(entry_id="1-1", run_id="ABC123")
    assert not hasattr(token, "start_step")


@pytest.mark.parametrize("fields", [
    {},
    {"run_id": ""},
    {"run_id": "../../etc/passwd"},
    {"run_id": "has a space"},
    {"run_id": "a" * 65},  # one past RUN_ID_RE's 64-char cap
])
def test_work_token_from_entry_rejects_a_malformed_run_id(fields):
    with pytest.raises(run_queue.BadToken) as exc:
        run_queue.WorkToken.from_entry("1-1", fields)
    assert exc.value.entry_id == "1-1"
    assert exc.value.fields == fields


def test_work_token_is_frozen():
    token = run_queue.WorkToken.from_entry("1-1", {"run_id": "ABC123"})
    with pytest.raises(AttributeError):
        token.run_id = "OTHER1"


def test_work_token_run_id_pattern_is_artifact_service_s_single_definition():
    """CODING STANDARDS section 1.5: one definition of "what a run_id may
    look like", reused rather than a second pattern hand-written here."""
    from app.services.artifact_service import RUN_ID_RE
    assert run_queue.RUN_ID_RE is RUN_ID_RE


# --- API producers ---------------------------------------------------------

def _seed(db, status="created", run_id="RUNQ01"):
    from app.models import Run, Step, User

    user = User(email="queue@example.com", display_name="Q", role="user")
    db.add(user)
    db.commit()
    db.refresh(user)
    run = Run(id=run_id, filename="cv.docx", file_type="docx", status=status, user_id=user.id,
              started_at=SEEDED_AT)
    db.add(run)
    db.add_all([Step(run_id=run_id, step_number=n, step_name=f"s{n}", status=s)
                for n, s in ((1, "complete"), (2, "error"), (3, "pending"))])
    db.commit()
    return user, run


@pytest.fixture
def as_user_with_input(db, monkeypatch, tmp_path):
    """Route auth + input-file plumbing so a request reaches the dispatch seam."""
    from app.api import runs as runs_api
    from app.auth import get_current_user
    from app.main import app

    def _install(user):
        app.dependency_overrides[get_current_user] = lambda: user
        monkeypatch.setattr(runs_api, "UPLOAD_DIR", tmp_path)
        monkeypatch.setattr(runs_api, "_materialize_input_if_missing", lambda *a: None)
        (tmp_path / "RUNQ01.docx").write_bytes(b"PK")
    yield _install
    app.dependency_overrides.pop(get_current_user, None)


def _row(db, run_id="RUNQ01"):
    from app.models import Run
    db.expire_all()
    return db.query(Run).filter(Run.id == run_id).one()


def _status(db, run_id="RUNQ01"):
    return _row(db, run_id).status


def test_start_in_queue_mode_flips_to_queued_and_enqueues_once(client, db, fake_redis, queue_mode, monkeypatch, as_user_with_input):
    """Design §4 window A: the flip is COMMITTED before XADD, so 'XADD ok, DB
    update lost' cannot happen; the call order is pinned, not just the outcome."""
    from sqlalchemy.orm import Session

    user, _ = _seed(db)
    as_user_with_input(user)
    order, real_commit, real_enqueue = [], Session.commit, run_queue.enqueue
    monkeypatch.setattr(Session, "commit", lambda self: (order.append("commit"), real_commit(self))[1])
    monkeypatch.setattr(run_queue, "enqueue", lambda *a, **k: (order.append("enqueue"), real_enqueue(*a, **k))[1])

    resp = client.post("/api/run/RUNQ01/start")

    assert resp.status_code == 202, resp.text
    assert resp.json() == {"message": "Run RUNQ01 queued", "status": "queued"}
    assert order == ["commit", "enqueue"]
    assert _status(db) == "queued"
    assert _row(db).queued_at is not None, "queued age in the admin view counts from the flip"
    assert _row(db).started_at == SEEDED_AT, "started_at means 'began executing'; only the worker's claim sets it"
    assert _row(db).resume_from_step is None, "a fresh start clears any resume point a prior retry left"
    assert [e["run_id"] for e in _entries(fake_redis)] == ["RUNQ01"]
    assert "start_step" not in _entries(fake_redis)[0]


def test_start_in_process_mode_is_untouched(client, db, monkeypatch, as_user_with_input):
    """Default mode still schedules the BackgroundTask and answers 200/running."""
    from starlette.background import BackgroundTasks
    from app.pipeline import concurrency

    monkeypatch.delenv("CVICHE_DISPATCH_MODE", raising=False)
    add_task = MagicMock()
    monkeypatch.setattr(BackgroundTasks, "add_task", add_task)
    monkeypatch.setattr(concurrency, "try_acquire_slot", lambda run_id: True)
    user, _ = _seed(db)
    as_user_with_input(user)
    resp = client.post("/api/run/RUNQ01/start")
    assert resp.status_code == 200
    assert resp.json()["status"] == "running"
    assert add_task.call_count == 1
    assert _status(db) == "running"


def test_start_reverts_status_and_answers_503_when_xadd_fails(client, db, monkeypatch, queue_mode, as_user_with_input):
    def boom(*_a, **_k):
        raise redis.exceptions.ConnectionError("valkey down")
    monkeypatch.setattr(run_queue, "enqueue", boom)
    user, _ = _seed(db, status="paused")
    as_user_with_input(user)
    resp = client.post("/api/run/RUNQ01/start")
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "queue_unavailable"
    assert (_status(db), _row(db).started_at) == ("paused", SEEDED_AT)


def test_start_does_not_turn_a_non_redis_error_into_a_503(client, db, monkeypatch, queue_mode, as_user_with_input):
    """#701 runs.py point 2: only redis.exceptions.RedisError is caught and
    revert-and-503'd. Anything else (an unexpected bug in enqueue, say) is
    loud misconfiguration and propagates -- the flip stays "queued" with no
    token, which the queued-run reconciler is the backstop for.

    The specific "CVICHE_REDIS_URL is not set" case this used to simulate is
    now caught BEFORE the flip by run_queue.is_configured() (N3) and never
    reaches enqueue at all -- see
    test_start_in_queue_mode_refuses_before_flipping_when_valkey_is_unconfigured
    -- so CVICHE_REDIS_URL is set here and an unrelated bug stands in for
    "some other non-Redis exception", to keep proving the general principle.
    """
    monkeypatch.setenv("CVICHE_REDIS_URL", "redis://fake-valkey:6379/0")

    def boom(*_a, **_k):
        raise RuntimeError("boom: unexpected bug in enqueue")
    monkeypatch.setattr(run_queue, "enqueue", boom)
    user, _ = _seed(db, status="paused")
    as_user_with_input(user)
    resp = client.post("/api/run/RUNQ01/start")
    assert resp.status_code == 500
    assert _status(db) == "queued", "the flip is not reverted for a non-Redis error"


def test_start_reports_live_status_when_a_revert_loses_to_a_concurrent_claim(client, db, monkeypatch, queue_mode, as_user_with_input):
    """#701 runs.py point 1: a failed XADD's revert is guarded WHERE
    status='queued'. If the worker's claim (queued->running) landed in
    between, the revert matches no row -- report the live status (202), not
    a stale 503 that tells the caller to retry a run that is already
    executing."""
    from sqlalchemy import update as sa_update
    from app.models import Run

    monkeypatch.setenv("CVICHE_REDIS_URL", "redis://fake-valkey:6379/0")

    def race_then_fail(run_id, *a, **k):
        # Simulate the worker's claim winning the race right after the flip,
        # then the producer's own XADD failing.
        db.execute(sa_update(Run).where(Run.id == run_id).values(status="running"))
        db.commit()
        raise redis.exceptions.ConnectionError("valkey down")

    monkeypatch.setattr(run_queue, "enqueue", race_then_fail)
    user, _ = _seed(db, status="paused")
    as_user_with_input(user)
    resp = client.post("/api/run/RUNQ01/start")
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "running"
    assert _status(db) == "running", "the guarded revert must not clobber the worker's claim"


def _make_batch_run(db, run_id="RUNQ01", files_submitted=2):
    """Put the seeded run in a batch owned by its user (#1114)."""
    from app.models import RunBatch
    run = _row(db, run_id)
    db.add(RunBatch(id="BATCHQ", user_id=run.user_id, files_submitted=files_submitted))
    run.batch_id = "BATCHQ"
    db.commit()


def test_start_of_a_batch_run_enqueues_on_the_batch_stream(client, db, fake_redis, queue_mode, as_user_with_input):
    user, _ = _seed(db)
    _make_batch_run(db)
    as_user_with_input(user)
    resp = client.post("/api/run/RUNQ01/start")
    assert resp.status_code == 202, resp.text
    assert [e["run_id"] for e in _batch_entries(fake_redis)] == ["RUNQ01"]
    assert _entries(fake_redis) == []


def test_start_of_a_one_file_batch_run_enqueues_on_the_single_stream(
    client, db, fake_redis, queue_mode, as_user_with_input,
):
    """A single upload with "Email me when job completes" ticked is a one-file
    batch (#1340); it must not wait behind bulk batches on the batch queue."""
    user, _ = _seed(db)
    _make_batch_run(db, files_submitted=1)
    as_user_with_input(user)
    assert client.post("/api/run/RUNQ01/start").status_code == 202
    assert [e["run_id"] for e in _entries(fake_redis)] == ["RUNQ01"]
    assert _batch_entries(fake_redis) == []


def test_re_enqueue_of_an_already_queued_batch_run_stays_on_the_batch_stream(
    client, db, fake_redis, queue_mode, as_user_with_input,
):
    user, _ = _seed(db, status="queued")
    _make_batch_run(db)
    as_user_with_input(user)
    assert client.post("/api/run/RUNQ01/start").status_code == 202
    assert [e["run_id"] for e in _batch_entries(fake_redis)] == ["RUNQ01"]
    assert _entries(fake_redis) == []


def test_retry_of_a_batch_run_enqueues_on_the_batch_stream(client, db, fake_redis, queue_mode, as_user_with_input):
    user, _ = _seed(db, status="failed")
    _make_batch_run(db)
    as_user_with_input(user)
    assert client.post("/api/run/RUNQ01/retry/2").status_code == 202
    assert [e["run_id"] for e in _batch_entries(fake_redis)] == ["RUNQ01"]
    assert _entries(fake_redis) == []


def test_start_on_already_queued_run_re_enqueues_without_flip(client, db, fake_redis, queue_mode, as_user_with_input):
    user, _ = _seed(db, status="queued")
    as_user_with_input(user)
    run_queue.enqueue("RUNQ01")
    resp = client.post("/api/run/RUNQ01/start")
    assert resp.status_code == 202
    assert _status(db) == "queued"
    assert [e["run_id"] for e in _entries(fake_redis)] == ["RUNQ01", "RUNQ01"]


def test_start_on_already_queued_run_is_guarded_against_a_re_enqueue_storm(client, db, fake_redis, queue_mode, as_user_with_input):
    """#701 runs.py point 5: a second /start on an already-queued run within
    REENQUEUE_GUARD_TTL_S of the first does not add another token."""
    user, _ = _seed(db, status="queued")
    as_user_with_input(user)

    first = client.post("/api/run/RUNQ01/start")
    second = client.post("/api/run/RUNQ01/start")

    assert (first.status_code, second.status_code) == (202, 202)
    assert len(_entries(fake_redis)) == 1, "the guard must limit the storm to one fresh token"


def test_start_on_terminal_run_is_still_400_in_queue_mode(client, db, fake_redis, queue_mode, as_user_with_input):
    user, _ = _seed(db, status="complete")
    as_user_with_input(user)
    resp = client.post("/api/run/RUNQ01/start")
    assert resp.status_code == 400
    assert _status(db) == "complete"
    assert _entries(fake_redis) == []


def test_start_in_queue_mode_never_acquires_a_pod_slot(client, db, fake_redis, queue_mode, monkeypatch, as_user_with_input):
    from app.pipeline import concurrency
    monkeypatch.setattr(concurrency, "try_acquire_slot", lambda run_id: pytest.fail("slot acquired in queue mode"))
    user, _ = _seed(db)
    as_user_with_input(user)
    assert client.post("/api/run/RUNQ01/start").status_code == 202


def test_start_in_queue_mode_refuses_before_flipping_when_valkey_is_unconfigured(
    client, db, queue_mode, monkeypatch, as_user_with_input,
):
    """N3: CVICHE_REDIS_URL unset must be checked BEFORE flip_to_queued --
    checking only inside the enqueue try/except (as the pre-N3 code did)
    misses it entirely: _build_client's RuntimeError isn't a
    redis.exceptions.RedisError, so it escaped uncaught AFTER the flip had
    already committed the row "queued", stranding it with no worker ever
    coming for it."""
    monkeypatch.delenv("CVICHE_REDIS_URL", raising=False)
    user, _ = _seed(db)
    as_user_with_input(user)

    resp = client.post("/api/run/RUNQ01/start")

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "queue_unavailable"
    assert _status(db) == "created", "must never be flipped to queued when misconfigured"


def test_retry_in_queue_mode_resets_steps_and_persists_resume_from_step(client, db, fake_redis, queue_mode, as_user_with_input):
    """#701 runs.py point 3: the token is a pure wake-up (run_id only) --
    the resume point is persisted on the row by the flip, not carried on the
    Valkey message, so a redelivered or stale token can't replay an old
    step."""
    from app.models import Step
    user, _ = _seed(db, status="failed")
    as_user_with_input(user)
    resp = client.post("/api/run/RUNQ01/retry/2")
    assert resp.status_code == 202, resp.text
    assert _status(db) == "queued"
    assert _row(db).queued_at is not None
    assert _row(db).started_at == SEEDED_AT, "started_at means 'began executing'; the flip doesn't touch it"
    assert _row(db).resume_from_step == 2
    assert "start_step" not in _entries(fake_redis)[0]
    assert {s.step_number: s.status for s in db.query(Step).all()} == {1: "complete", 2: "pending", 3: "pending"}


def test_retry_in_queue_mode_on_non_failed_run_is_409_and_keeps_steps(client, db, fake_redis, queue_mode, as_user_with_input):
    """#701 runs.py point 8: a flip that loses because the run's live status
    wasn't in allowed_from ("failed") is a conflict, not a bad request -- the
    caller's view of the run was simply stale."""
    from app.models import Step
    user, _ = _seed(db, status="cancelled")
    as_user_with_input(user)
    assert client.post("/api/run/RUNQ01/retry/2").status_code == 409
    assert _status(db) == "cancelled"
    assert _entries(fake_redis) == []
    assert db.query(Step).filter(Step.step_number == 2).one().status == "error"


def test_start_on_a_retry_queued_run_keeps_its_resume_step(client, db, fake_redis, queue_mode, as_user_with_input):
    """#701 runs.py point 3: /start on a run a retry already queued (the
    already-queued branch, no fresh flip) must not touch resume_from_step --
    only a winning flip ever writes it, and start_run's flip nulls it."""
    from sqlalchemy import update as sa_update
    from app.models import Run

    user, _ = _seed(db, status="queued")
    db.execute(sa_update(Run).where(Run.id == "RUNQ01").values(resume_from_step=2))
    db.commit()
    as_user_with_input(user)

    resp = client.post("/api/run/RUNQ01/start")

    assert resp.status_code == 202
    assert _row(db).resume_from_step == 2


def test_cancel_accepts_queued_and_the_worker_then_skips(client, db, fake_redis, queue_mode, as_user_with_input, monkeypatch):
    from app import worker
    from app.services import run_service
    from tests.conftest import TestingSessionLocal

    user, _ = _seed(db, status="queued")
    as_user_with_input(user)
    eid = run_queue.enqueue("RUNQ01")
    resp = client.post("/api/run/RUNQ01/cancel")
    assert resp.status_code == 200
    assert _status(db) == "cancelled"

    orchestrator = MagicMock()
    monkeypatch.setattr(worker, "PipelineOrchestrator", orchestrator)
    monkeypatch.setattr(worker, "SessionLocal", TestingSessionLocal)
    # worker.handle's claim now goes through run_service.claim_queued, which
    # opens its own session -- point it at the same shared test DB (#701
    # worker.py point 5; the wired fixture in test_worker.py does this too).
    monkeypatch.setattr(run_service, "SessionLocal", TestingSessionLocal)
    run_queue.ensure_group()
    worker.handle(*run_queue.read_one("w1"))
    assert orchestrator.call_count == 0
    assert _status(db) == "cancelled"
    assert run_queue.stats()["pending"] == 0
    assert run_queue.delivery_count(eid) == 0


def test_cancel_still_rejects_a_created_run(client, db, as_user_with_input):
    user, _ = _seed(db, status="created")
    as_user_with_input(user)
    assert client.post("/api/run/RUNQ01/cancel").status_code == 400


def test_dispatch_routes_stay_plain_def_not_async():
    """N4: start_run, retry_step (#701 runs.py point 10) and get_queue_stats
    (run_queue.py point 11) must stay plain ``def``, not ``async def`` --
    FastAPI then runs each in the threadpool, so its synchronous Valkey call
    (XADD, or the blocking redis-py stats calls) costs a threadpool slot
    instead of stalling the event loop for every other request on the pod."""
    import inspect
    from app.api.runs import start_run, retry_step
    from app.api.admin_routes.runs import get_queue_stats

    assert not inspect.iscoroutinefunction(start_run)
    assert not inspect.iscoroutinefunction(retry_step)
    assert not inspect.iscoroutinefunction(get_queue_stats)


def test_queue_stats_route_has_a_typed_response_model():
    """N4: the admin queue-stats route must declare response_model so its
    shape is validated and documented, not returned as an untyped dict."""
    from app.api.admin_routes import router as admin_router
    from app.api.admin_routes.runs import get_queue_stats
    from app.schemas import QueueStatsResponse

    route = next(r for r in admin_router.routes if r.endpoint is get_queue_stats)
    assert route.response_model is QueueStatsResponse
