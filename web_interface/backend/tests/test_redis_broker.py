"""Tests for the optional Redis broker (issue #4) and its in-process fallback.

Two modes:
  - disabled (no CVICHE_REDIS_URL): emitter delivers to local sockets, cancel
    uses the in-process set -- the historical single-worker behavior.
  - enabled: events publish to Redis and a subscriber loop fans them out to
    local sockets; cancellation round-trips through Redis so it crosses worker /
    replica boundaries.

The enabled-path tests use fakeredis (in-memory, no server) and are skipped if
it isn't installed; the fallback-path tests need neither redis nor fakeredis.
"""
import asyncio

import pytest

from app.pipeline.redis_broker import RedisBroker, broker_from_env
from app.pipeline.event_emitter import EventEmitter


class FakeWS:
    """Minimal stand-in for a Starlette WebSocket."""
    def __init__(self):
        self.sent = []
        self.accepted = False

    async def accept(self):
        self.accepted = True

    async def send_text(self, message):
        self.sent.append(message)


def _fakeredis_broker():
    """An enabled broker backed by a shared in-memory fakeredis server, with the
    sync (publish/cancel) and async (subscribe) clients injected."""
    fakeredis = pytest.importorskip("fakeredis")
    import fakeredis.aioredis  # noqa: F401

    server = fakeredis.FakeServer()
    broker = RedisBroker("redis://fake")  # truthy url -> enabled
    broker._sync_client = fakeredis.FakeStrictRedis(server=server)
    broker._async_client = fakeredis.aioredis.FakeRedis(server=server)
    return broker, server


# --- disabled / fallback path -----------------------------------------------

def test_broker_from_env_disabled_without_url(monkeypatch):
    monkeypatch.delenv("CVICHE_REDIS_URL", raising=False)
    broker = broker_from_env()
    assert broker.enabled is False
    # Every op is a safe no-op when disabled.
    broker.publish_event("R1", {"event": "LOG"})
    broker.request_cancel("R1")
    broker.clear_cancel("R1")
    assert broker.is_cancelled("R1") is False


def test_broker_from_env_enabled_with_url(monkeypatch):
    monkeypatch.setenv("CVICHE_REDIS_URL", "redis://localhost:6379/0")
    assert broker_from_env().enabled is True


def test_emitter_fallback_delivers_to_local_socket():
    """With no broker, emit writes straight to this process's connections."""
    emitter = EventEmitter(broker=None)
    ws = FakeWS()

    async def scenario():
        await emitter.connect("RUN1", ws)
        await emitter.emit("RUN1", {"event": "LOG", "message": "hello"})

    asyncio.run(scenario())
    assert ws.accepted is True
    assert len(ws.sent) == 1
    assert "hello" in ws.sent[0]


# --- enabled path (fakeredis) -----------------------------------------------

def test_cancel_roundtrips_through_redis_across_workers():
    broker, server = _fakeredis_broker()
    import fakeredis

    # A second broker on the SAME server stands in for another worker/replica.
    other = RedisBroker("redis://fake")
    other._sync_client = fakeredis.FakeStrictRedis(server=server)

    assert broker.is_cancelled("RUN1") is False
    broker.request_cancel("RUN1")
    # The cancel set by one worker is visible to the other.
    assert other.is_cancelled("RUN1") is True
    broker.clear_cancel("RUN1")
    assert other.is_cancelled("RUN1") is False


def test_published_event_delivered_to_local_socket_via_subscriber():
    broker, _ = _fakeredis_broker()
    emitter = EventEmitter(broker)
    ws = FakeWS()

    async def scenario():
        await emitter.startup()              # async subscriber loop
        await emitter.connect("RUN1", ws)
        await emitter.emit("RUN1", {"event": "LOG", "message": "via-redis"})
        for _ in range(100):                 # let the subscriber loop deliver
            if ws.sent:
                break
            await asyncio.sleep(0.01)
        await emitter.shutdown()
        await broker.shutdown()

    asyncio.run(scenario())
    assert len(ws.sent) == 1
    assert "via-redis" in ws.sent[0]


def test_delivery_works_when_server_rejects_psubscribe(monkeypatch):
    """Prod Valkey (ElastiCache Serverless) answers PSUBSCRIBE with "unknown
    command". fakeredis accepts it, which is how #960 shipped with this suite
    green -- so make the fake reject it the same way and prove delivery works."""
    import redis.asyncio.client
    from redis.exceptions import ResponseError

    async def rejected(self, *args, **kwargs):
        raise ResponseError("unknown command 'psubscribe'")

    monkeypatch.setattr(redis.asyncio.client.PubSub, "psubscribe", rejected)
    broker, _ = _fakeredis_broker()
    emitter = EventEmitter(broker)
    ws = FakeWS()

    async def scenario():
        await emitter.startup()
        await emitter.connect("RUN1", ws)
        await emitter.emit("RUN1", {"event": "LOG", "message": "no-pattern"})
        for _ in range(100):
            if ws.sent:
                break
            await asyncio.sleep(0.01)
        await emitter.shutdown()
        await broker.shutdown()

    asyncio.run(scenario())
    assert len(ws.sent) == 1
    assert "no-pattern" in ws.sent[0]


def test_subscriber_survives_a_read_timeout(monkeypatch):
    """redis-py 8 defaults socket_timeout to 5s, so on prod an idle read raised
    TimeoutError and the subscriber loop exited for good -- every later event
    was lost until the pod restarted (#960). One failed read must not end
    delivery: the next event still reaches the socket."""
    import redis.asyncio.client
    from redis.exceptions import TimeoutError as RedisTimeoutError

    real_parse = redis.asyncio.client.PubSub.parse_response
    fault = {"armed": False}

    async def flaky_parse(self, *args, **kwargs):
        if fault["armed"]:
            fault["armed"] = False
            raise RedisTimeoutError("Timeout reading from valkey:6379")
        return await real_parse(self, *args, **kwargs)

    monkeypatch.setattr(redis.asyncio.client.PubSub, "parse_response", flaky_parse)
    monkeypatch.setattr("app.pipeline.event_emitter.SUBSCRIBER_RETRY_SECONDS", 0.01)
    broker, _ = _fakeredis_broker()
    emitter = EventEmitter(broker)
    ws = FakeWS()

    async def scenario():
        await emitter.startup()
        await emitter.connect("RUN1", ws)
        fault["armed"] = True                # the loop's next read times out
        for _ in range(100):
            if not fault["armed"]:
                break
            await asyncio.sleep(0.01)
        for _ in range(200):                 # resubscribed; keep emitting until one lands
            await emitter.emit("RUN1", {"event": "LOG", "message": "after-timeout"})
            await asyncio.sleep(0.02)
            if ws.sent:
                break
        await emitter.shutdown()
        await broker.shutdown()

    asyncio.run(scenario())
    assert fault["armed"] is False, "the injected timeout never fired"
    assert ws.sent and "after-timeout" in ws.sent[0]


def test_subscriber_only_delivers_to_matching_run():
    """A socket for RUN1 must not receive RUN2's events."""
    broker, _ = _fakeredis_broker()
    emitter = EventEmitter(broker)
    ws1 = FakeWS()

    async def scenario():
        await emitter.startup()
        await emitter.connect("RUN1", ws1)
        await emitter.emit("RUN2", {"event": "LOG", "message": "other-run"})
        # Give the loop time; ws1 should stay empty.
        for _ in range(20):
            await asyncio.sleep(0.01)
        await emitter.shutdown()
        await broker.shutdown()

    asyncio.run(scenario())
    assert ws1.sent == []


# --- orchestrator cancel integration ----------------------------------------

def test_orchestrator_cancel_uses_broker_when_enabled():
    from app.pipeline import orchestrator as orch
    broker, _ = _fakeredis_broker()
    try:
        orch.set_broker(broker)
        orch._cancelled_runs.clear()
        # Simulate the cancel arriving on a different worker: clear the local
        # set so only the broker can report the cancellation.
        orch.cancel_run("RUN1")
        orch._cancelled_runs.clear()
        assert orch.is_cancelled("RUN1") is True   # read back from Redis
        orch.clear_cancelled("RUN1")
        assert orch.is_cancelled("RUN1") is False
    finally:
        orch.set_broker(None)
        orch._cancelled_runs.clear()


def test_orchestrator_cancel_falls_back_to_local_set():
    from app.pipeline import orchestrator as orch
    try:
        orch.set_broker(None)
        orch._cancelled_runs.clear()
        assert orch.is_cancelled("RUN1") is False
        orch.cancel_run("RUN1")
        assert orch.is_cancelled("RUN1") is True
        orch.clear_cancelled("RUN1")
        assert orch.is_cancelled("RUN1") is False
    finally:
        orch._cancelled_runs.clear()


def test_stop_run_locally_sets_only_the_in_process_flag():
    """Shutdown's stop (#116) must not leave a Redis cancel key behind: it
    would outlive the pod by CANCEL_TTL_SECONDS and cancel a retry of the same
    run started on another pod in that window."""
    from app.pipeline import orchestrator as orch
    broker, _ = _fakeredis_broker()
    try:
        orch.set_broker(broker)
        orch._cancelled_runs.clear()
        orch.stop_run_locally("RUN1")
        assert orch.is_cancelled("RUN1") is True
        orch._cancelled_runs.clear()
        assert broker.is_cancelled("RUN1") is False
    finally:
        orch.set_broker(None)
        orch._cancelled_runs.clear()


class _CountingLock:
    """Stand-in for _cancelled_lock that counts acquisitions (#307)."""

    def __init__(self):
        self.acquired = 0

    def __enter__(self):
        self.acquired += 1

    def __exit__(self, *exc):
        return False


@pytest.mark.parametrize("mutate", ["cancel_run", "stop_run_locally", "clear_cancelled", "is_cancelled"])
def test_cancel_state_access_takes_the_lock(monkeypatch, mutate):
    from app.pipeline import orchestrator as orch
    lock = _CountingLock()
    monkeypatch.setattr(orch, "_cancelled_lock", lock)
    monkeypatch.setattr(orch, "_broker", None)
    try:
        getattr(orch, mutate)("RUNLOCK")
        assert lock.acquired == 1
    finally:
        orch._cancelled_runs.clear()
        orch._stopped_locally.clear()


def test_clear_cancelled_forgets_a_local_stop():
    from app.pipeline import orchestrator as orch
    try:
        orch.stop_run_locally("RUN1")
        assert "RUN1" in orch._stopped_locally
        orch.clear_cancelled("RUN1")
        assert "RUN1" not in orch._stopped_locally
    finally:
        orch._cancelled_runs.clear()
        orch._stopped_locally.clear()
