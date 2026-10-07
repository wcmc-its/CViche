"""WebSocket endpoint for real-time pipeline updates."""
import asyncio
import logging
from dataclasses import dataclass
from http.cookies import SimpleCookie

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool

from app.auth import COOKIE_NAME, authenticate_session_cookie, can_see_cost
from app.models import Run
from app.origins import origin_permitted
from app.pipeline.event_emitter import event_emitter
from app.services.run_service import check_run_access

router = APIRouter()
logger = logging.getLogger(__name__)

# Terminal run statuses -> the live event the orchestrator emits for each.
_TERMINAL_STATUSES = {"complete", "failed", "cancelled"}

# Close codes. 1008/1013 are RFC 6455 codes; 4001/4003 are application-defined
# (the 4000-4999 range) and are what the frontend's socket client already reads.
_CLOSE_POLICY_VIOLATION = 1008   # the run does not exist
_CLOSE_TRY_AGAIN_LATER = 1013    # we could not tell whether the session is valid
_CLOSE_UNAUTHORIZED = 4001       # no/expired/revoked session
_CLOSE_FORBIDDEN = 4003          # authenticated, but not allowed here -- also the
                                 # pre-accept origin rejection, which a browser
                                 # surfaces as a failed handshake, not a close frame
_NO_CLOSE_CODE = 0               # "nothing to close with" -- see _StreamAuth.ok

# HTTPException status -> (close code, close reason). authenticate_session_cookie
# and check_run_access speak HTTP; a WebSocket has no status line, so every
# rejection has to be re-expressed as a close code. One table, so the upgrade
# path and the periodic re-validation cannot drift apart.
_CLOSE_FOR_STATUS: dict[int, tuple[int, str]] = {
    401: (_CLOSE_UNAUTHORIZED, "Authentication required"),
    403: (_CLOSE_FORBIDDEN, "Access denied"),
    404: (_CLOSE_POLICY_VIOLATION, "Run not found"),
    503: (_CLOSE_TRY_AGAIN_LATER, "Sign-in state is temporarily unavailable"),
}
_CLOSE_FOR_UNMAPPED_STATUS = (_CLOSE_UNAUTHORIZED, "Authentication required")

# How long a socket may go without proving its session is still good. A cookie
# revoked (logout, "sign out everyone", idle timeout, disabled account) is
# therefore honored on an open socket within this window, instead of at the
# socket's own lifetime -- which for a long pipeline run is unbounded.
_REVALIDATE_SECONDS = 30
# How often the server writes something, so an idle proxy or load balancer does
# not reap a socket that is merely waiting for the next pipeline event (a run
# can sit minutes inside one LLM stage). Separate from the re-validation
# interval on purpose: they answer different questions and are tuned apart.
_HEARTBEAT_SECONDS = 30
# The frontend switches on `data.event` and ignores every other message
# (usePipelineRun.tsx), so this is invisible to it by construction.
_HEARTBEAT_MESSAGE = {"type": "ping"}


@dataclass(frozen=True)
class _StreamAuth:
    """The verdict on one upgrade request.

    A record rather than a bare tuple so the close code and its reason cannot be
    swapped at a call site (CODING_STANDARDS.md 8.1). When ``ok`` is True the
    close fields are unused; when it is False ``user_id`` is None.
    ``hide_cost`` defaults to True so a verdict built without it fails closed.
    """
    ok: bool
    close_code: int
    reason: str
    user_id: int | None
    hide_cost: bool = True


def _terminal_event_for_run(run: Run) -> dict | None:
    """Build the terminal event to replay on (re)connect, or None if the run
    is still in progress.

    A client can connect (or reconnect) *after* the orchestrator already
    emitted the one-shot terminal event -- or, under horizontal scaling with
    the Redis broker off, connect to a different pod than the one that ran the
    pipeline and never receive live events at all. Replaying the current
    terminal status on connect makes terminal detection pod-independent and
    immediate, so the UI stops its elapsed timer without waiting for the next
    status poll. Shapes mirror EventEmitter.emit_run_complete / RUN_CANCELLED /
    emit_run_failed exactly so the frontend handles them with no special case.
    """
    if run.status not in _TERMINAL_STATUSES:
        return None
    if run.status == "complete":
        duration = None
        if run.started_at and run.completed_at:
            duration = int((run.completed_at - run.started_at).total_seconds())
        return {
            "event": "RUN_COMPLETE",
            "total_cost": run.total_cost or 0.0,
            "total_tokens": run.total_tokens or 0,
            "duration": duration,
        }
    if run.status == "failed":
        return {"event": "RUN_FAILED", "error": run.error_message, "step": None}
    return {"event": "RUN_CANCELLED"}


def _get_cookie_from_scope(websocket: WebSocket) -> str | None:
    """Extract the session cookie value from WebSocket scope headers."""
    headers = dict(websocket.scope.get("headers", []))
    raw_cookie = headers.get(b"cookie", b"").decode("utf-8", errors="replace")
    if not raw_cookie:
        return None
    sc = SimpleCookie(raw_cookie)
    morsel = sc.get(COOKIE_NAME)
    return morsel.value if morsel else None


def _close_for(exc: HTTPException) -> tuple[int, str]:
    return _CLOSE_FOR_STATUS.get(exc.status_code, _CLOSE_FOR_UNMAPPED_STATUS)


def _authorize_stream(cookie_value: str | None, run_id: str) -> _StreamAuth:
    """Session + run-access checks for an upgrade request. Blocking; call it
    through run_in_threadpool.

    Runs exactly the checks a REST request runs -- authenticate_session_cookie
    is the same function get_current_user calls -- rather than this module's own
    transcription of them, which is how the WebSocket path had previously
    drifted from REST (#657 review, thread 3). ``touch_idle=True``: opening a
    stream is user activity, so it slides the idle window once, here.
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        user, _identity = authenticate_session_cookie(cookie_value, db, touch_idle=True)
        check_run_access(run_id, user, db, read_only=True)
        return _StreamAuth(ok=True, close_code=_NO_CLOSE_CODE, reason="", user_id=user.id,
                           hide_cost=not can_see_cost(user))
    except HTTPException as exc:
        close_code, reason = _close_for(exc)
        return _StreamAuth(ok=False, close_code=close_code, reason=reason, user_id=None)
    finally:
        db.close()


def _session_still_valid(cookie_value: str | None) -> tuple[bool, int]:
    """Re-run the session checks for an already-open socket. Blocking; call it
    through run_in_threadpool.

    ``touch_idle=False``: a socket sitting open is not user activity, so this
    must not slide the idle window -- otherwise an abandoned tab would keep its
    own session alive forever, which is the opposite of what an idle timeout is
    for. Everything else (store record, epoch, account status) is checked
    exactly as it is on a REST request.
    """
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        authenticate_session_cookie(cookie_value, db, touch_idle=False)
        return True, _NO_CLOSE_CODE
    except HTTPException as exc:
        close_code, _reason = _close_for(exc)
        return False, close_code
    finally:
        db.close()


def _terminal_snapshot(run_id: str) -> dict | None:
    """The run's terminal event right now, or None. Blocking; call it through
    run_in_threadpool."""
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        run = db.query(Run).filter(Run.id == run_id).first()
        return _terminal_event_for_run(run) if run else None
    finally:
        db.close()


async def _close_quietly(websocket: WebSocket, code: int, reason: str) -> None:
    """Close a socket that may already be gone.

    Starlette raises RuntimeError when the peer has already disconnected; that
    is the state we were trying to reach, not an error worth propagating.
    """
    try:
        await websocket.close(code=code, reason=reason)
    except (WebSocketDisconnect, RuntimeError):
        logger.info("WebSocket already closed before close(%s) could be sent", code)


async def _replay_terminal_event(run_id: str, websocket: WebSocket) -> None:
    """Replay the run's terminal status to this one socket, if it has one.

    Sent point-to-point (not broadcast) to avoid re-notifying already-synced
    peers, and through the emitter so it shares the per-socket terminal dedup
    with live delivery: without that, a client connecting in the same instant
    the orchestrator emits RUN_COMPLETE receives it twice (#657 review,
    thread 6). send_direct does not raise on a closed socket -- it drops it
    and logs -- so there is nothing to catch here.
    """
    terminal_event = await run_in_threadpool(_terminal_snapshot, run_id)
    if terminal_event is None:
        return
    await event_emitter.send_direct(run_id, websocket, terminal_event)


async def _stream_until_closed(websocket: WebSocket, cookie_value: str | None) -> None:
    """Hold the socket open, re-validating the session and sending heartbeats.

    Both are deadline-driven, not restarted per message, so a chatty client
    cannot postpone re-validation indefinitely by talking. Client messages
    themselves are read and discarded -- the protocol is server-to-client only
    today; receiving is how a disconnect is noticed.
    """
    loop = asyncio.get_running_loop()
    next_check = loop.time() + _REVALIDATE_SECONDS
    next_beat = loop.time() + _HEARTBEAT_SECONDS
    while True:
        try:
            await asyncio.wait_for(
                websocket.receive_text(),
                timeout=max(0.0, min(next_check, next_beat) - loop.time()),
            )
            continue
        except TimeoutError:
            pass
        except WebSocketDisconnect:
            return

        now = loop.time()
        if now >= next_check:
            ok, close_code = await run_in_threadpool(_session_still_valid, cookie_value)
            if not ok:
                await _close_quietly(websocket, close_code, "Session no longer valid")
                return
            next_check = loop.time() + _REVALIDATE_SECONDS
        if now >= next_beat:
            try:
                await websocket.send_json(_HEARTBEAT_MESSAGE)
            except (WebSocketDisconnect, RuntimeError):
                return
            next_beat = loop.time() + _HEARTBEAT_SECONDS


@router.websocket("/ws/run/{run_id}/stream")
async def websocket_stream(websocket: WebSocket, run_id: str):
    """Stream one run's pipeline events to an authenticated browser.

    Three gates before a byte is sent: the Origin allowlist (a WebSocket
    upgrade is exempt from CORS and never reaches CSRFMiddleware, so without
    this any page on any site could open an authenticated stream with the
    user's cookie), the session, and access to this particular run.
    """
    if not origin_permitted(websocket.headers.get("origin")):
        # Closed BEFORE accept(), which the ASGI server turns into a rejected
        # handshake (HTTP 403) rather than an accepted-then-closed socket, so
        # the offending page never gets an open connection at all.
        await websocket.close(code=_CLOSE_FORBIDDEN, reason="Origin not allowed")
        return

    cookie_value = _get_cookie_from_scope(websocket)
    # Off the event loop: authorization is a DB query plus (when the session
    # store is enabled) a Valkey round trip. Run inline, each upgrade blocks
    # every other socket's delivery for the duration (#657 review, thread 2).
    auth = await run_in_threadpool(_authorize_stream, cookie_value, run_id)
    if not auth.ok:
        await websocket.accept()
        await _close_quietly(websocket, auth.close_code, auth.reason)
        return

    await event_emitter.connect(run_id, websocket, hide_cost=auth.hide_cost)
    try:
        await _replay_terminal_event(run_id, websocket)
        await _stream_until_closed(websocket, cookie_value)
    finally:
        event_emitter.disconnect(run_id, websocket)
