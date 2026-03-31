"""Event emitter for WebSocket communication."""
import json
from typing import Dict, Set
from fastapi import WebSocket
from datetime import datetime


class EventEmitter:
    """Manages WebSocket connections and broadcasts events."""

    def __init__(self):
        self.connections: Dict[str, Set[WebSocket]] = {}

    async def connect(self, run_id: str, websocket: WebSocket):
        """Register a new WebSocket connection."""
        await websocket.accept()
        if run_id not in self.connections:
            self.connections[run_id] = set()
        self.connections[run_id].add(websocket)

    def disconnect(self, run_id: str, websocket: WebSocket):
        """Remove a WebSocket connection."""
        if run_id in self.connections:
            self.connections[run_id].discard(websocket)
            if not self.connections[run_id]:
                del self.connections[run_id]

    async def emit(self, run_id: str, event: dict):
        """Broadcast an event to all connections."""
        if run_id not in self.connections:
            return

        if "timestamp" not in event:
            event["timestamp"] = datetime.now().isoformat()

        message = json.dumps(event)
        disconnected = set()

        for websocket in self.connections[run_id]:
            try:
                await websocket.send_text(message)
            except Exception:
                disconnected.add(websocket)

        for ws in disconnected:
            self.disconnect(run_id, ws)

    async def emit_run_start(self, run_id: str):
        await self.emit(run_id, {"event": "RUN_START"})

    async def emit_step_start(self, run_id: str, step_number: int, total_cost: float = 0.0):
        await self.emit(run_id, {"event": "STEP_START", "step": step_number, "total_cost": total_cost})

    async def emit_log(self, run_id: str, step_number: int, message: str, level: str = "INFO"):
        await self.emit(run_id, {"event": "LOG", "step": step_number, "level": level, "message": message})

    async def emit_step_complete(self, run_id: str, step_number: int, duration: int, cost: float, output_files: list):
        await self.emit(run_id, {"event": "STEP_COMPLETE", "step": step_number, "duration": duration, "cost": cost, "output_files": output_files})

    async def emit_step_error(self, run_id: str, step_number: int, error: str):
        await self.emit(run_id, {"event": "STEP_ERROR", "step": step_number, "error": error})

    async def emit_run_complete(self, run_id: str, total_cost: float, total_tokens: int, duration: int):
        await self.emit(run_id, {"event": "RUN_COMPLETE", "total_cost": total_cost, "total_tokens": total_tokens, "duration": duration})

    async def emit_progress(self, run_id: str, step_number: int, current: int, total: int, message: str = ""):
        """Emit granular progress within a step (e.g., "Processing 5 of 20 sections")."""
        await self.emit(run_id, {
            "event": "PROGRESS",
            "step": step_number,
            "current": current,
            "total": total,
            "message": message,
            "percentage": round((current / total) * 100) if total > 0 else 0
        })

    async def emit_cost_update(self, run_id: str, step_number: int, cost_delta: float, total_cost: float,
                                tokens_delta: int = 0, total_tokens: int = 0,
                                input_tokens_delta: int = 0, output_tokens_delta: int = 0,
                                input_tokens_total: int = 0, output_tokens_total: int = 0,
                                provider: str = "openai"):
        """Emit real-time cost updates as LLM calls complete."""
        await self.emit(run_id, {
            "event": "COST_UPDATE",
            "step": step_number,
            "cost_delta": cost_delta,
            "total_cost": total_cost,
            "tokens_delta": tokens_delta,
            "total_tokens": total_tokens,
            "input_tokens_delta": input_tokens_delta,
            "output_tokens_delta": output_tokens_delta,
            "input_tokens": input_tokens_total,
            "output_tokens": output_tokens_total,
            "provider": provider,
        })


# Global instance
event_emitter = EventEmitter()
