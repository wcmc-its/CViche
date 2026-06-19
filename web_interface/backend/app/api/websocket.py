"""WebSocket endpoint for real-time pipeline updates."""
import json
from http.cookies import SimpleCookie

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Run, User
from app.auth import decode_session_cookie, COOKIE_NAME
from app.session_idle import get_idle_store
from app.pipeline.event_emitter import event_emitter
from app.services.run_service import check_run_access

router = APIRouter()

# Terminal run statuses -> the live event the orchestrator emits for each.
_TERMINAL_STATUSES = {"complete", "failed", "cancelled"}


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


@router.websocket("/ws/run/{run_id}/stream")
async def websocket_stream(websocket: WebSocket, run_id: str):
    """WebSocket endpoint for streaming pipeline events."""

    # --- Auth: validate session cookie on upgrade ---
    cookie_value = _get_cookie_from_scope(websocket)
    payload = decode_session_cookie(cookie_value) if cookie_value else None

    if not payload:
        await websocket.accept()
        await websocket.close(code=4001, reason="Authentication required")
        return

    from app.database import SessionLocal
    from app.auth import get_session_epoch
    db = SessionLocal()
    try:
        # Same global revocation gate as get_current_user -- this is the second
        # cookie-decode site, so a revoked/old-epoch cookie must be rejected here
        # too or a long-lived socket would outlive a "sign out everyone".
        if int(payload.get("epoch", 0)) != get_session_epoch(db):
            await websocket.accept()
            await websocket.close(code=4001, reason="Session expired")
            return

        user = db.query(User).filter(User.id == payload["user_id"]).first()
        if not user or user.status != "active":
            await websocket.accept()
            await websocket.close(code=4001, reason="User not found or disabled")
            return

        # Server-side idle enforcement: a stale cookie must not open a new stream.
        # Touch once at upgrade (counts as activity); we deliberately don't keep
        # refreshing for the life of the socket, so a tab left open on a finished
        # run still idles out via the user's REST activity. Cookies without a
        # `sid` (pre-feature) bypass; no-op / fail-open when Valkey is off.
        sid = payload.get("sid")
        if sid and not get_idle_store().touch(sid):
            await websocket.accept()
            await websocket.close(code=4001, reason="Session timed out")
            return

        # Verify run exists and user has access
        try:
            run = check_run_access(run_id, user, db)
        except HTTPException as exc:
            await websocket.accept()
            if exc.status_code == 404:
                await websocket.close(code=1008, reason=f"Run {run_id} not found")
            else:
                await websocket.close(code=4003, reason="Access denied")
            return
    finally:
        db.close()

    # Connect WebSocket
    await event_emitter.connect(run_id, websocket)

    # Replay current terminal status (if any) directly to this socket so a
    # client connecting after the live terminal event -- or to a different
    # pod than the one that ran the pipeline -- still stops its timer and
    # shows the outcome immediately. Sent point-to-point (not broadcast) to
    # avoid re-notifying already-synced peers. Best-effort: a send failure
    # here must not abort the connection.
    snapshot_db = SessionLocal()
    try:
        run = snapshot_db.query(Run).filter(Run.id == run_id).first()
        terminal_event = _terminal_event_for_run(run) if run else None
    finally:
        snapshot_db.close()
    if terminal_event is not None:
        try:
            await websocket.send_text(json.dumps(terminal_event))
        except Exception:
            pass

    try:
        # Keep connection alive and listen for messages
        while True:
            # Receive messages from client (for ping/pong or commands)
            try:
                message = await websocket.receive_text()
                # Handle client messages if needed (e.g., pause/resume commands)
                # For now, just echo to keep connection alive
            except WebSocketDisconnect:
                break

    except Exception as e:
        print(f"WebSocket error: {e}")

    finally:
        # Disconnect when done
        event_emitter.disconnect(run_id, websocket)
