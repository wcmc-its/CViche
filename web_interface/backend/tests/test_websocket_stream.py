"""The /ws/run/{run_id}/stream upgrade path and the emitter's terminal dedup.

PR #657 review, threads 1-7. Before this, the WebSocket endpoint accepted any
origin, ran its authorization (a DB query plus a Valkey round trip) inline on
the event loop, validated the session exactly once at upgrade, and replayed the
terminal event with a raw send that could double-report a run's end.

What is pinned here:
  - An Origin that is present and not on the allowlist never gets a socket, and
    is refused *before* authentication (thread 1). A missing Origin is allowed,
    the same way CSRFMiddleware allows it.
  - Authorization runs through run_in_threadpool, not on the event loop
    (thread 2), and is the same authenticate_session_cookie REST runs (thread 3).
  - An open socket re-checks its session on an interval and closes when the
    session stops being valid -- 4001 when it is gone, 1013 when the store
    cannot say (threads 4/5).
  - A terminal event reaches any one socket at most once, whichever of the two
    routes (live broadcast / connect-time replay) gets there first (thread 6),
    and every event carries an event_id (thread 7).
  - Without a broker, a run's events -- emitted from its own asyncio.run loop in
    a worker thread -- are written on the loop that owns the sockets, in order,
    and an undeliverable event never raises into the run (#116).
"""
import asyncio
import contextvars
import io
import json
import logging
import os
import sys
import threading
import time
from contextlib import contextmanager

os.environ.setdefault("CVICHE_SESSION_SECRET", "test-secret-not-for-production")

from unittest.mock import MagicMock

import pytest
import redis.exceptions
from starlette.websockets import WebSocketDisconnect

import app.api.websocket as ws_module
import app.pipeline.event_emitter as emitter_module
import app.session_idle as session_idle
from app.models import Run, SystemConfig, User
from app.pipeline.event_emitter import EventEmitter
from app.session_idle import IdleSessionStore

_STREAM_URL = "/ws/run/{run_id}/stream"
_ALLOWED_ORIGIN = "http://localhost:3000"
_DISALLOWED_ORIGIN = "https://cviche.weill.cornell.edu.evil.example"


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

def _fake_store(ttl=1200):
    fakeredis = pytest.importorskip("fakeredis")
    store = IdleSessionStore("redis://fake", ttl)  # truthy url -> enabled
    store._client = fakeredis.FakeStrictRedis()
    return store


@pytest.fixture
def idle_store(monkeypatch):
    """Install a fake-backed idle store as the process singleton for a test."""
    store = _fake_store()
    monkeypatch.setattr(session_idle, "_store", store)
    return store


def _make_user(db, email="ws@example.com", role="user"):
    user = User(email=email, display_name="WS User", role=role,
                status="active", consent_version="1.0")
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _make_run(db, run_id, user_id, status="running", **fields):
    run = Run(id=run_id, filename="cv.docx", file_type="docx", status=status,
              user_id=user_id, **fields)
    db.add(run)
    db.commit()
    return run


def _set_epoch(db, value):
    row = db.query(SystemConfig).filter(SystemConfig.key == "session_epoch").first()
    encoded = json.dumps(value)
    if row:
        row.value = encoded
    else:
        db.add(SystemConfig(key="session_epoch", value=encoded))
    db.commit()


def _authenticate(client, db, user):
    """Mint a real session cookie for `user`, install it, and return its sid."""
    from app.auth import COOKIE_NAME, create_session_cookie, decode_session_cookie

    _set_epoch(db, 0)
    token = create_session_cookie(user, db)
    client.cookies.set(COOKIE_NAME, token)
    return decode_session_cookie(token).get("sid")


def _expect_close(ws, timeout=5.0):
    """Wait for the server to close an open socket; return the disconnect.

    Bounded on purpose: a plain ws.receive_json() would hang the whole suite
    forever if the server stopped closing (which is exactly the regression
    these tests exist to catch), instead of failing.
    """
    outcome = {}

    def receive():
        try:
            outcome["message"] = ws.receive_json()
        except BaseException as exc:  # re-raised on the main thread below
            outcome["exc"] = exc

    worker = threading.Thread(target=receive, daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        pytest.fail(f"socket still open after {timeout}s; expected the server to close it")
    exc = outcome.get("exc")
    assert isinstance(exc, WebSocketDisconnect), \
        f"expected the server to close the socket, got {outcome!r}"
    return exc


def _connect(client, run_id, **kwargs):
    return client.websocket_connect(_STREAM_URL.format(run_id=run_id), **kwargs)


def _rejection(client, run_id, **kwargs):
    """Open the socket expecting a refusal; return the WebSocketDisconnect.

    Handles both shapes: a pre-accept close (the origin gate) surfaces as a
    refused handshake and raises on entry, while a close after accept() -- what
    every authorization failure does, so the browser sees the code -- raises on
    the first receive.
    """
    try:
        with _connect(client, run_id, **kwargs) as ws:
            return _expect_close(ws)
    except WebSocketDisconnect as exc:
        return exc


# ---------------------------------------------------------------------------
# Origin allowlist (thread 1)
# ---------------------------------------------------------------------------

def test_disallowed_origin_is_refused_before_authentication(client, db, seed_simple_mode):
    """A page on another origin must not be able to open an authenticated
    stream with the user's cookie: a WS upgrade gets no CORS check and never
    reaches CSRFMiddleware, so this endpoint has to check it itself.

    No cookie is sent, and the *reason* is what proves the ordering -- the
    origin gate answers first, so the auth gate is never reached.
    """
    exc = _rejection(client, "ABC123", headers={"origin": _DISALLOWED_ORIGIN})

    assert exc.code == 4003
    assert exc.reason == "Origin not allowed"


def test_allowed_origin_passes_the_origin_gate(client, db, seed_simple_mode):
    """An allowlisted origin reaches the next gate (authentication), which is
    what rejects this unauthenticated connection."""
    exc = _rejection(client, "ABC123", headers={"origin": _ALLOWED_ORIGIN})

    assert exc.code == 4001
    assert exc.reason == "Authentication required"


def test_missing_origin_is_allowed(client, db, seed_simple_mode):
    """Non-browser clients send no Origin; CSRFMiddleware permits those, and
    this gate matches it rather than inventing a stricter rule for one route."""
    exc = _rejection(client, "ABC123")

    assert exc.code == 4001
    assert exc.reason == "Authentication required"


# ---------------------------------------------------------------------------
# Authorization at upgrade (threads 2, 3)
# ---------------------------------------------------------------------------

def test_no_cookie_closes_4001(client, db, seed_simple_mode, idle_store):
    exc = _rejection(client, "ABC123")
    assert exc.code == 4001


def test_unknown_run_closes_1008(client, db, seed_simple_mode, idle_store):
    user = _make_user(db)
    _authenticate(client, db, user)

    exc = _rejection(client, "NOSUCH")

    assert exc.code == 1008
    assert exc.reason == "Run not found"


def test_another_users_run_closes_4003(client, db, seed_simple_mode, idle_store):
    owner = _make_user(db, email="owner@example.com")
    intruder = _make_user(db, email="intruder@example.com")
    _make_run(db, "OWNED1", owner.id)
    _authenticate(client, db, intruder)

    exc = _rejection(client, "OWNED1")

    assert exc.code == 4003
    assert exc.reason == "Access denied"


def test_store_outage_at_upgrade_closes_1013(client, db, seed_simple_mode, monkeypatch):
    """The store is configured but unreachable: we cannot tell whether the
    session is valid, so the socket is refused with "try again later" (1013),
    never opened on an unverifiable session (D1's fail-closed policy)."""
    from app.auth import COOKIE_NAME, _serializer

    broken = IdleSessionStore("redis://fake", 1200)
    broken._client = MagicMock()
    broken._client.get.side_effect = redis.exceptions.ConnectionError("valkey down")
    monkeypatch.setattr(session_idle, "_store", broken)

    user = _make_user(db)
    _make_run(db, "RUN013", user.id)
    client.cookies.set(COOKIE_NAME, _serializer.dumps({"v": 2, "sid": "live-sid"}))

    exc = _rejection(client, "RUN013")

    assert exc.code == 1013


@pytest.mark.parametrize("role, expected_cost", [("admin", 1.25), ("user", None)])
def test_authorized_socket_receives_the_terminal_replay(
        client, db, seed_simple_mode, idle_store, role, expected_cost):
    """Happy path: an authenticated owner gets an open socket, and the run's
    terminal status is replayed to it on connect. The cost in it reaches an
    admin only (#1111)."""
    user = _make_user(db, role=role)
    _make_run(db, "DONE01", user.id, status="complete", total_cost=1.25, total_tokens=42)
    _authenticate(client, db, user)

    with _connect(client, "DONE01", headers={"origin": _ALLOWED_ORIGIN}) as ws:
        message = ws.receive_json()

    assert message["event"] == "RUN_COMPLETE"
    assert message.get("total_cost") == expected_cost
    assert message["total_tokens"] == 42
    # Stamped like every other emitted event (thread 7).
    assert message["event_id"]
    assert message["timestamp"]


def test_authorization_and_snapshot_run_off_the_event_loop(
    client, db, seed_simple_mode, idle_store, monkeypatch
):
    """Both blocking steps at upgrade go through run_in_threadpool.

    Run inline, one upgrade's DB query plus Valkey round trip stalls delivery
    on every other socket this worker holds (thread 2). Asserted by recording
    what actually went through the offload, not by reading the source.
    """
    calls = []
    real_run_in_threadpool = ws_module.run_in_threadpool

    async def recording(func, *args, **kwargs):
        calls.append(func.__name__)
        return await real_run_in_threadpool(func, *args, **kwargs)

    monkeypatch.setattr(ws_module, "run_in_threadpool", recording)

    user = _make_user(db)
    _make_run(db, "DONE02", user.id, status="complete")
    _authenticate(client, db, user)

    with _connect(client, "DONE02") as ws:
        ws.receive_json()

    assert "_authorize_stream" in calls
    assert "_terminal_snapshot" in calls


# ---------------------------------------------------------------------------
# Periodic re-validation (threads 4, 5)
# ---------------------------------------------------------------------------

def test_revoked_session_closes_an_open_socket(
    client, db, seed_simple_mode, idle_store, monkeypatch
):
    """A socket outlives its session otherwise: a logout or "sign out everyone"
    was honored only on the next REST request, while the stream kept running
    for the life of the run."""
    monkeypatch.setattr(ws_module, "_REVALIDATE_SECONDS", 0.05)

    user = _make_user(db)
    _make_run(db, "LIVE01", user.id, status="running")
    sid = _authenticate(client, db, user)

    with _connect(client, "LIVE01") as ws:
        # Revoke server-side, exactly as logout does.
        idle_store.end(sid)
        closed = _expect_close(ws)

    assert closed.code == 4001
    assert closed.reason == "Session no longer valid"


def test_store_outage_during_revalidation_closes_1013(
    client, db, seed_simple_mode, idle_store, monkeypatch
):
    """Same fail-closed rule mid-stream as at upgrade: an unreachable store is
    "we cannot tell", which closes the socket, not "assume it is fine"."""
    monkeypatch.setattr(ws_module, "_REVALIDATE_SECONDS", 0.05)

    user = _make_user(db)
    _make_run(db, "LIVE02", user.id, status="running")
    _authenticate(client, db, user)

    with _connect(client, "LIVE02") as ws:
        broken_client = MagicMock()
        broken_client.get.side_effect = redis.exceptions.ConnectionError("valkey down")
        idle_store._client = broken_client
        closed = _expect_close(ws)

    assert closed.code == 1013


def test_revalidation_does_not_slide_the_idle_window(
    client, db, seed_simple_mode, idle_store, monkeypatch
):
    """An abandoned tab must still idle out. Re-validation reads the session
    record; it must never refresh its TTL, or an open socket would keep its own
    session alive forever."""
    monkeypatch.setattr(ws_module, "_REVALIDATE_SECONDS", 0.05)
    touches = []
    real_touch = idle_store.touch

    def recording_touch(sid):
        touches.append(sid)
        return real_touch(sid)

    monkeypatch.setattr(idle_store, "touch", recording_touch)

    user = _make_user(db)
    _make_run(db, "LIVE03", user.id, status="running")
    sid = _authenticate(client, db, user)

    with _connect(client, "LIVE03") as ws:
        idle_store.end(sid)
        _expect_close(ws)

    # Exactly one touch: the upgrade itself (opening a stream IS user
    # activity). Every later re-validation passes touch_idle=False.
    assert touches == [sid]


# ---------------------------------------------------------------------------
# Emitter: terminal dedup and event_id (threads 6, 7)
# ---------------------------------------------------------------------------

class _RecordingSocket:
    """A stand-in for a Starlette WebSocket that records what it was sent."""

    def __init__(self):
        self.sent = []

    async def accept(self):
        return None

    async def send_text(self, message: str):
        self.sent.append(json.loads(message))


def _event_names(socket):
    return [message["event"] for message in socket.sent]


def _run(coro_fn):
    asyncio.run(coro_fn())


def test_terminal_event_once_when_replay_precedes_the_live_event():
    emitter = EventEmitter()
    socket = _RecordingSocket()

    async def scenario():
        await emitter.connect("R1", socket)
        await emitter.send_direct("R1", socket, {"event": "RUN_COMPLETE", "total_cost": 1.0})
        await emitter.emit_run_complete("R1", 1.0, 10, 5)

    _run(scenario)

    assert _event_names(socket) == ["RUN_COMPLETE"]


def test_terminal_event_once_when_the_live_event_precedes_the_replay():
    """The other order: a client that connects in the same instant the run
    finishes gets the broadcast first and the replay second."""
    emitter = EventEmitter()
    socket = _RecordingSocket()

    async def scenario():
        await emitter.connect("R1", socket)
        await emitter.emit_run_failed("R1", "boom", 3)
        await emitter.send_direct("R1", socket, {"event": "RUN_FAILED", "error": "boom", "step": 3})

    _run(scenario)

    assert _event_names(socket) == ["RUN_FAILED"]


class _InterleavingSocket(_RecordingSocket):
    """A _RecordingSocket whose send_text yields control once before
    recording, so two coroutines racing to deliver to the same socket can
    both pass a dedup check made before the yield."""

    async def send_text(self, message: str):
        await asyncio.sleep(0)
        await super().send_text(message)


def test_terminal_event_once_when_replay_and_live_delivery_interleave():
    """The replay (send_direct) and a live broadcast (_deliver_local) can run
    concurrently: a client connecting in the same instant the run finishes.
    If send_text yields before the dedup mark is claimed, both routes can
    pass the membership check before either marks, and the socket is told
    twice (#657 review, thread 6)."""
    emitter = EventEmitter()
    socket = _InterleavingSocket()

    async def scenario():
        await emitter.connect("R1", socket)
        await asyncio.gather(
            emitter.send_direct("R1", socket, {"event": "RUN_COMPLETE", "total_cost": 1.0}),
            emitter.emit_run_complete("R1", 1.0, 10, 5),
        )

    _run(scenario)

    assert _event_names(socket) == ["RUN_COMPLETE"]


def test_non_terminal_events_are_never_deduped():
    """The dedup is scoped to the three run-ending events; a run emits many
    LOG/PROGRESS events and every one of them must arrive."""
    emitter = EventEmitter()
    socket = _RecordingSocket()

    async def scenario():
        await emitter.connect("R1", socket)
        await emitter.emit_log("R1", 1, "first")
        await emitter.emit_log("R1", 1, "second")
        await emitter.emit_log("R1", 1, "third")

    _run(scenario)

    assert _event_names(socket) == ["LOG", "LOG", "LOG"]
    assert [m["message"] for m in socket.sent] == ["first", "second", "third"]


def test_disconnect_clears_the_terminal_mark():
    """A reconnecting client is a new socket and must be told again -- and the
    mark must not outlive the socket, or the set grows for the life of the
    process."""
    emitter = EventEmitter()
    socket = _RecordingSocket()

    async def scenario():
        await emitter.connect("R1", socket)
        await emitter.send_direct("R1", socket, {"event": "RUN_CANCELLED"})
        emitter.disconnect("R1", socket)
        await emitter.connect("R1", socket)
        await emitter.send_direct("R1", socket, {"event": "RUN_CANCELLED"})

    _run(scenario)

    assert _event_names(socket) == ["RUN_CANCELLED", "RUN_CANCELLED"]


def test_every_event_carries_a_distinct_event_id():
    """event_id lets a consumer recognize the same event arriving twice rather
    than inferring it from field equality. Additive: the frontend switches on
    `event` and ignores unknown keys."""
    emitter = EventEmitter()
    socket = _RecordingSocket()

    async def scenario():
        await emitter.connect("R1", socket)
        await emitter.emit_log("R1", 1, "same")
        await emitter.emit_log("R1", 1, "same")

    _run(scenario)

    ids = [m["event_id"] for m in socket.sent]
    assert all(ids)
    assert len(set(ids)) == 2


def test_cost_fields_reach_only_sockets_that_may_see_cost():
    """#1111: one run, an admin's socket and a non-admin's socket. Every cost
    field is stripped for the non-admin; the rest of each event is intact."""
    emitter = EventEmitter()
    admin, user = _RecordingSocket(), _RecordingSocket()

    async def scenario():
        await emitter.connect("R1", admin, hide_cost=False)
        await emitter.connect("R1", user, hide_cost=True)
        await emitter.emit_step_start("R1", 1, total_cost=0.5)
        await emitter.emit_cost_update("R1", 1, 0.1, 0.6, tokens_delta=7, total_tokens=70)
        await emitter.emit_step_complete("R1", 1, 3, 0.6, [])
        await emitter.emit_run_complete("R1", 0.6, 70, 3)

    _run(scenario)

    cost_keys = {"total_cost", "cost", "cost_delta"}
    assert _event_names(admin) == _event_names(user)
    assert all(cost_keys & message.keys() for message in admin.sent)
    assert not any(cost_keys & message.keys() for message in user.sent)
    assert user.sent[1]["total_tokens"] == 70


def test_connect_hides_cost_unless_told_otherwise():
    """A caller that omits hide_cost fails closed."""
    emitter = EventEmitter()
    socket = _RecordingSocket()

    async def scenario():
        await emitter.connect("R1", socket)
        await emitter.emit_run_complete("R1", 0.6, 70, 3)

    _run(scenario)

    assert "total_cost" not in socket.sent[0]


# ---------------------------------------------------------------------------
# Emitter: a run's events cross from its own loop to the socket loop (#116)
# ---------------------------------------------------------------------------

class _LoopBoundSocket(_RecordingSocket):
    """A socket that, like a real one, can only be written from the loop that
    accepted it. ``slow`` maps an event name to seconds its send takes."""

    def __init__(self, slow=None):
        super().__init__()
        self.slow = slow or {}
        self.loop = None

    async def accept(self):
        self.loop = asyncio.get_running_loop()

    async def send_text(self, message: str):
        if asyncio.get_running_loop() is not self.loop:
            raise RuntimeError("written from a loop that does not own the socket")
        await asyncio.sleep(self.slow.get(json.loads(message)["event"], 0))
        await super().send_text(message)


@contextmanager
def _server_loop():
    """An event loop running in its own thread: uvicorn's main loop."""
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        yield loop
    finally:
        if not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
            thread.join(5)
            loop.close()


def _on(loop, coro):
    return asyncio.run_coroutine_threadsafe(coro, loop).result(5)


def _in_run_thread(coro_fn):
    """Run coro_fn the way a pipeline run does: asyncio.run in a worker thread.
    Returns (seconds taken, exception raised or None)."""
    outcome = {}

    def target():
        started = time.monotonic()
        try:
            asyncio.run(coro_fn())
        except BaseException as exc:  # reported to the test, not swallowed
            outcome["error"] = exc
        outcome["seconds"] = time.monotonic() - started

    worker = threading.Thread(target=target)
    worker.start()
    worker.join(30)
    return outcome["seconds"], outcome.get("error")


def test_a_runs_events_reach_the_server_loops_socket_in_order():
    """The orchestrator emits from its own asyncio.run loop; the socket belongs
    to the server loop. Every event must arrive, in emission order, even when
    one write is slower than the next."""
    emitter = EventEmitter()
    socket = _LoopBoundSocket(slow={"RUN_START": 0.05})

    async def run():
        await emitter.emit_run_start("R1")
        for n in range(5):
            await emitter.emit_log("R1", 1, f"line {n}")
        await emitter.emit_run_complete("R1", 0.1, 5, 2)

    with _server_loop() as server:
        _on(server, emitter.connect("R1", socket))
        _, error = _in_run_thread(run)

    assert error is None
    assert _event_names(socket) == ["RUN_START"] + ["LOG"] * 5 + ["RUN_COMPLETE"]
    assert [m["message"] for m in socket.sent[1:6]] == [f"line {n}" for n in range(5)]
    assert emitter.connections == {"R1": {socket}}


def test_a_slow_socket_does_not_hold_the_run(monkeypatch):
    """The run waits a bounded time for a delivery, then goes on; the delivery
    itself is not cancelled mid-write and still lands."""
    monkeypatch.setattr(emitter_module, "CROSS_LOOP_EMIT_WAIT_SECONDS", 0.05)
    emitter = EventEmitter()
    socket = _LoopBoundSocket(slow={"LOG": 0.5})

    with _server_loop() as server:
        _on(server, emitter.connect("R1", socket))
        seconds, error = _in_run_thread(lambda: emitter.emit_log("R1", 1, "slow"))
        deadline = time.monotonic() + 3
        while not socket.sent and time.monotonic() < deadline:
            time.sleep(0.02)

    assert error is None
    assert seconds < 0.4
    assert _event_names(socket) == ["LOG"]


def test_an_emit_after_the_server_loop_closed_does_not_raise(monkeypatch):
    """At shutdown the server loop can be gone while a run thread is still
    emitting (#1095 drain); the event is dropped, the run is not failed, and
    the delivery coroutine that never ran is closed rather than left to warn
    'never awaited'."""
    emitter = EventEmitter()
    socket = _LoopBoundSocket()
    made = []
    real_deliver = emitter._deliver_local

    def tracking_deliver(run_id, message):
        made.append(real_deliver(run_id, message))
        return made[-1]

    with _server_loop() as server:
        _on(server, emitter.connect("R1", socket))
        server.call_soon_threadsafe(server.stop)
        time.sleep(0.1)
        server.close()
        monkeypatch.setattr(emitter, "_deliver_local", tracking_deliver)
        seconds, error = _in_run_thread(lambda: emitter.emit_run_failed("R1", "deploy"))

    assert error is None
    assert seconds < 0.5
    assert socket.sent == []
    assert len(made) == 1 and made[0].cr_frame is None


def test_a_new_server_loop_replaces_a_closed_one():
    """Sockets belong to the loop that accepted them most recently; a
    process whose first loop is gone (a TestClient portal, a restarted
    server) must deliver on the live one."""
    emitter = EventEmitter()
    old_socket, new_socket = _LoopBoundSocket(), _LoopBoundSocket()

    with _server_loop() as old:
        _on(old, emitter.connect("OLD", old_socket))
    with _server_loop() as new:
        _on(new, emitter.connect("R1", new_socket))
        _, error = _in_run_thread(lambda: emitter.emit_log("R1", 1, "x"))

    assert error is None
    assert _event_names(new_socket) == ["LOG"]


@pytest.mark.parametrize("failure", [asyncio.CancelledError, ValueError])
def test_a_failed_delivery_does_not_raise_into_the_run(monkeypatch, caplog, failure):
    """A delivery that is cancelled on the server loop (its tasks are cancelled
    at loop shutdown) or errors is logged, never raised into the run."""
    emitter = EventEmitter()
    socket = _LoopBoundSocket()

    async def broken(run_id, message):
        raise failure()

    with _server_loop() as server:
        _on(server, emitter.connect("R1", socket))
        monkeypatch.setattr(emitter, "_deliver_local", broken)
        with caplog.at_level(logging.WARNING, logger=emitter_module.__name__):
            _, error = _in_run_thread(lambda: emitter.emit_log("R1", 1, "x"))

    assert error is None
    assert any("R1" in record.getMessage() for record in caplog.records)


def test_a_run_nobody_watches_skips_the_hop_to_the_server_loop():
    """No socket for this run on this process: nothing to deliver, so the run
    must not wait on the server loop at all (here it is not even running)."""
    emitter = EventEmitter()
    idle = asyncio.new_event_loop()
    emitter._socket_loop = idle
    emitter.connections["OTHER"] = {_LoopBoundSocket()}
    try:
        seconds, error = _in_run_thread(lambda: emitter.emit_log("R1", 1, "x"))
    finally:
        idle.close()

    assert error is None
    assert seconds < 0.5


_RUN_SCOPED = contextvars.ContextVar("run_scoped", default=None)


def test_a_delivery_does_not_inherit_the_runs_context():
    """A run's context carries its stdout capture; a delivery running with it
    on the server loop would route the server loop's own log output into
    that run's capture, which blocks the loop on every line."""
    emitter = EventEmitter()
    seen = []

    class _ContextRecordingSocket(_LoopBoundSocket):
        async def send_text(self, message: str):
            seen.append(_RUN_SCOPED.get())
            await super().send_text(message)

    async def run():
        _RUN_SCOPED.set("capture of run R1")
        await emitter.emit_log("R1", 1, "x")

    with _server_loop() as server:
        _on(server, emitter.connect("R1", _ContextRecordingSocket()))
        _, error = _in_run_thread(run)

    assert error is None
    assert seen == [None]


def test_an_emit_on_the_server_loop_itself_writes_before_returning(monkeypatch):
    """A caller already on the socket loop writes directly: no hop, so no
    timeout that could return before its event is written."""
    monkeypatch.setattr(emitter_module, "CROSS_LOOP_EMIT_WAIT_SECONDS", 0.01)
    emitter = EventEmitter()
    socket = _LoopBoundSocket(slow={"LOG": 0.1})

    async def scenario():
        await emitter.connect("R1", socket)
        await emitter.emit_log("R1", 1, "x")
        return list(socket.sent)

    assert [m["event"] for m in asyncio.run(scenario())] == ["LOG"]


def test_a_slow_socket_does_not_feed_the_runs_own_log(monkeypatch):
    """The real stdout capture and router, with app logging written through
    sys.stdout as configure_logging sets it up. A stage print() reaches log()
    in the capture's context; if the emitter's own "not delivered" warning
    were logged in that context, the router would stream it back into the
    run's log, the capture would wait 2s on the run's own loop per line, and
    each such line would emit and warn again. Also pins the emit wait under
    the capture's 2s: past it, the capture gives up and prints an error."""
    from app.logging_config import _DYNAMIC_STDOUT
    from app.pipeline import orchestrator as orch

    real_stdout_side = io.StringIO()
    monkeypatch.setattr(sys, "__stdout__", real_stdout_side)
    handler = logging.StreamHandler(_DYNAMIC_STDOUT)
    emitter_module.logger.addHandler(handler)
    emitter = EventEmitter()
    # Slower than the capture's 2s, so only an emit wait under 2s keeps the
    # capture from timing out.
    socket = _LoopBoundSocket(slow={"LOG": 2.5})
    logged = []

    class _Run:
        """Stands in for PipelineOrchestrator's log()/update_progress()."""

        async def log(self, step, message, level="INFO"):
            logged.append(message)
            await emitter.emit_log("R1", step, message, level)

        async def update_progress(self, step, current, total, message=""):
            await emitter.emit_progress("R1", step, current, total, message)

    def stage():
        print("Processing 1 of 2 sections")
        print("Processing 2 of 2 sections")

    async def run():
        loop = asyncio.get_running_loop()
        await asyncio.to_thread(
            orch.PipelineOrchestrator._run_with_stdout_capture_sync, _Run(), stage, 1, loop)
        await asyncio.sleep(0.5)  # room for any line fed back to play out

    try:
        with _server_loop() as server:
            _on(server, emitter.connect("R1", socket))
            seconds, error = _in_run_thread(run)
            deadline = time.monotonic() + 5
            while _event_names(socket).count("LOG") < 2 and time.monotonic() < deadline:
                time.sleep(0.05)
    finally:
        emitter_module.logger.removeHandler(handler)

    assert error is None
    assert logged == ["Processing 1 of 2 sections", "Processing 2 of 2 sections"]
    assert "[Log emit error" not in real_stdout_side.getvalue()
    assert seconds < 4
    assert _event_names(socket).count("LOG") == 2
