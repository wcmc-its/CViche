"""WebSocket endpoint for real-time pipeline updates."""
from http.cookies import SimpleCookie

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Run, User
from app.auth import decode_session_cookie, COOKIE_NAME
from app.pipeline.event_emitter import event_emitter
from app.services.run_service import check_run_access

router = APIRouter()


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
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == payload["user_id"]).first()
        if not user or user.status != "active":
            await websocket.accept()
            await websocket.close(code=4001, reason="User not found or disabled")
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
