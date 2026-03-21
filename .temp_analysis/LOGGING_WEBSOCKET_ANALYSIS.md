# CV Pipeline Logging and WebSocket Communication Analysis

## Overview

The CV parsing pipeline uses **two parallel logging mechanisms**:

1. **Console logging** (via `print()` statements) - Detailed operational logs
2. **WebSocket logging** (via `event_emitter`) - High-level messages to frontend

The issue is that pipeline stages output extensive console logs but only select high-level messages are sent to the frontend via WebSocket.

---

## Key Architecture Components

### 1. WebSocket Infrastructure

#### File: `web_interface/backend/app/api/websocket.py`
- **WebSocket Endpoint**: `/ws/run/{run_id}/stream`
- **Handler**: `websocket_stream()` 
- **Connection Management**:
  - Accepts WebSocket connections for each run_id
  - Verifies run exists in database
  - Keeps connection alive while listening for messages
  - Handles disconnections gracefully

```python
@router.websocket("/ws/run/{run_id}/stream")
async def websocket_stream(websocket: WebSocket, run_id: str):
    """WebSocket endpoint for streaming pipeline events."""
    await event_emitter.connect(run_id, websocket)
    # Connection stays alive, listening for messages
```

#### File: `web_interface/backend/app/pipeline/event_emitter.py`
- **Class**: `EventEmitter`
- **Key Methods**:
  - `connect()` - Register new WebSocket connection
  - `disconnect()` - Remove WebSocket connection
  - `emit()` - Broadcast event to all connections for a run
  - `emit_log()` - Send log message to frontend
  - `emit_run_start()`, `emit_step_start()`, `emit_step_complete()`, `emit_step_error()`, `emit_run_complete()`

```python
async def emit_log(self, run_id: str, step_number: int, message: str, level: str = "INFO"):
    await self.emit(run_id, {
        "event": "LOG", 
        "step": step_number, 
        "level": level, 
        "message": message
    })
```

---

## 2. Pipeline Orchestration (Web Interface)

#### File: `web_interface/backend/app/pipeline/orchestrator.py`

This is where the **disconnect happens**. The orchestrator:

**What it does:**
- Converts print() output to WebSocket messages in real-time
- Logs to database
- Sends events via event_emitter

**Output Capture Mechanism**:
```python
class OutputCapture:
    """Capture stdout and send to WebSocket in real-time."""
    def write(self, text: str):
        """Capture stdout writes (thread-safe)."""
        if text and text.strip():
            asyncio.run_coroutine_threadsafe(
                self.orchestrator.log(self.step_number, text.strip(), "INFO"),
                self.loop
            )
```

**Logging Method**:
```python
async def log(self, step_number: int, message: str, level: str = "INFO"):
    """Log a message to database and emit via WebSocket."""
    log_entry = Log(run_id=self.run_id, step_number=step_number, 
                    level=level, message=message)
    self.db.add(log_entry)
    self.db.commit()
    await event_emitter.emit_log(self.run_id, step_number, message, level)
```

**Current Limitation:**
- The orchestrator calls `self.pipeline.run_stage_*_async()` methods
- These methods execute the pipeline logic but **print() output goes to console, not captured**
- Only `self.log()` method calls result in WebSocket messages

**Direct WebSocket Emissions in orchestrator**:
```python
await event_emitter.emit_run_start(self.run_id)
await self.log(step_number, f"Starting {step_def.name}")
await self.log(step_number, f"✓ Segmentation complete!")
await event_emitter.emit_step_complete(...)
```

---

## 3. Pipeline Stages (Core Logic)

All pipeline stages use **console print() statements** exclusively. They do NOT call any WebSocket methods.

### Stage 1: Segmentation
**File**: `src/unified_pipeline/core/cv_segmenter.py`

```python
print("="*80)
print("CV SEGMENTER - UNIFIED INTERFACE")
print(f"Input: {file_path}")
print(f"Format: {file_format}")
print("Approach: Three-pass vision segmentation")
print()
print(f"✓ Segmentation complete: {result['output_file']}")
```

**Print statements are NOT captured by the orchestrator**

### Stage 2: Taxonomy Mapping
**File**: `src/unified_pipeline/core/taxonomy_mapper.py`

```python
print("="*80)
print("LLM-BASED TAXONOMY MAPPING")
print(f"Input: {segmented_cv_path}")
print(f"  ✓ Loaded {len(CV_SECTIONS)} taxonomy sections")
print(f"{indent}[{len(mappings)+1}] Mapping: {section_label}")
print(f"MAPPING RESULTS")
print(f"Total sections: {total_sections}")
print(f"Average confidence: {avg_confidence:.3f}")
```

### Stage 3: Section Parsing
**Files**: 
- `src/unified_pipeline/parsers/publications_parser.py`
- `src/unified_pipeline/parsers/education_parser.py`
- `src/unified_pipeline/parsers/positions_parser.py`
- `src/unified_pipeline/parsers/grants_parser.py`

```python
# publications_parser.py
print(f"Parsing {len(entries)} publication entries...")
print(f"  [{idx}/{len(entries)}] Parsing entry...")
print(f"  ✓ {len(all_parsed)} publications parsed")

# education_parser.py
print(f"Parsing {len(entries)} education entries...")
print(f"  [{idx}/{len(entries)}] Parsing entry...")
print(f"Total education entries: {len(all_parsed)}")
```

### Stage 4: Template Generation
**File**: `src/unified_pipeline/core/cv_pipeline.py` (run_stage_4_template_generation method)

```python
print("="*80)
print("STAGE 4: WCM TEMPLATE GENERATION")
print(f"Output: {self.stage_dirs['stage_4']}")
print(f"  Loaded {len(items)} {section_type}")
print("Using advanced legacy handlers for template population...")
print("Enriching publications with PMID/PMCID lookup...")
print(f"  ✓ Enriched {enriched_count} publications with identifiers")
```

---

## 4. The Disconnect: Where Logging Splits

### Console Logging Path (Detailed, Backend-only)
```
Pipeline Stages (print statements)
    ↓
Console/stderr output
    ↓
Visible in backend logs ONLY
    ↓
NOT captured for frontend
```

### WebSocket Logging Path (High-level, Frontend-visible)
```
Orchestrator.log() calls
    ↓
Database insertion (Log table)
    ↓
event_emitter.emit_log()
    ↓
WebSocket broadcast to frontend
    ↓
Frontend receives in real-time
```

### What's Missing

The pipeline stages do NOT:
1. Accept a `progress_callback` parameter like cv_pipeline.py does
2. Call any logging/callback mechanism
3. Have access to the event_emitter
4. Send output to WebSocket

---

## 5. The Progress Callback Pattern

**File**: `src/unified_pipeline/core/cv_pipeline.py`

The main CV_Pipeline class HAS the pattern but doesn't use it everywhere:

```python
def __init__(self, cv_path: str, output_dir: Optional[str] = None,
             progress_callback: Optional[Callable[[int, str, str], 
                                                 Awaitable[None]]] = None):
    """
    Args:
        progress_callback: Optional async callback for progress updates
                          Signature: async def callback(stage: int, message: str, level: str = "INFO")
    """
    self.progress_callback = progress_callback

async def _log(self, stage: int, message: str, level: str = "INFO"):
    """Log a message. Calls progress_callback if provided, otherwise prints."""
    if self.progress_callback:
        await self.progress_callback(stage, message, level)
    else:
        print(message)
```

**BUT** the individual stage methods (run_stage_1_segmentation, etc.) don't use this pattern - they just print().

---

## 6. Database Schema for Logging

**File**: `web_interface/backend/app/models.py`

```python
class Log(Base):
    __tablename__ = "logs"
    
    id = Column(Integer, primary_key=True)
    run_id = Column(String, ForeignKey("runs.id"))
    step_number = Column(Integer)
    level = Column(String)  # INFO, WARNING, ERROR
    message = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)
```

All WebSocket logs are stored here, queryable by run_id and step_number.

---

## 7. Frontend Log Retrieval

**File**: `web_interface/backend/app/api/steps.py`

```python
@router.get("/run/{run_id}/step/{step_number}", response_model=StepDetail)
async def get_step_detail(run_id: str, step_number: int, db: Session):
    """Get detailed information about a specific step."""
    
    # Get logs for this step
    logs = db.query(Log).filter(
        Log.run_id == run_id,
        Log.step_number == step_number
    ).order_by(Log.timestamp).all()
    
    log_entries = [
        LogEntry(
            time=log.timestamp.strftime("%H:%M:%S"),
            level=log.level,
            message=log.message
        )
        for log in logs
    ]
    
    return StepDetail(..., logs=log_entries)
```

---

## Summary of Logging Points

| Stage | Component | Logging Mechanism | Destination |
|-------|-----------|-------------------|------------|
| **All** | Orchestrator | `await self.log()` | DB + WebSocket |
| **1** | CVSegmenter | `print()` | Console only |
| **2** | taxonomy_mapper | `print()` | Console only |
| **3** | publications_parser | `print()` | Console only |
| **3** | education_parser | `print()` | Console only |
| **3** | positions_parser | `print()` | Console only |
| **3** | grants_parser | `print()` | Console only |
| **4** | cv_pipeline.run_stage_4() | `print()` | Console only |
| **4** | Legacy handlers | `print()` (if any) | Console only |

---

## Why Frontend Only Sees High-Level Messages

The orchestrator only calls `self.log()` at these points:
1. `emit_run_start()` - high-level event
2. `emit_step_start()` - start message
3. `await self.log(step, "Starting Stage X...")` - manual log
4. `await self.log(step, "✓ Completed...")` - manual log
5. `emit_step_complete()` - completion event
6. `emit_step_error()` - error event
7. `emit_run_complete()` - final event

All the detailed progress within each stage (individual entry parsing, confidence scores, taxonomy mappings, etc.) goes to print() which:
- Appears in backend console
- Is NOT captured
- Does NOT reach frontend
- Is NOT stored in database

---

## Files Involved in Logging/WebSocket Flow

### WebSocket & Event Infrastructure
1. `/web_interface/backend/app/api/websocket.py` - WebSocket endpoint
2. `/web_interface/backend/app/pipeline/event_emitter.py` - Event broadcasting
3. `/web_interface/backend/app/models.py` - Database Log model
4. `/web_interface/backend/app/database.py` - Database initialization

### Orchestration & Logging
5. `/web_interface/backend/app/pipeline/orchestrator.py` - Pipeline execution + logging
6. `/web_interface/backend/app/api/runs.py` - Run lifecycle endpoints
7. `/web_interface/backend/app/api/steps.py` - Step detail & log retrieval

### Pipeline Core (Console-only logging)
8. `/src/unified_pipeline/core/cv_segmenter.py` - Stage 1
9. `/src/unified_pipeline/core/taxonomy_mapper.py` - Stage 2
10. `/src/unified_pipeline/parsers/publications_parser.py` - Stage 3 (publications)
11. `/src/unified_pipeline/parsers/education_parser.py` - Stage 3 (education)
12. `/src/unified_pipeline/parsers/positions_parser.py` - Stage 3 (positions)
13. `/src/unified_pipeline/parsers/grants_parser.py` - Stage 3 (grants)
14. `/src/unified_pipeline/core/cv_pipeline.py` - Stage 4 + has progress_callback pattern (partially used)

---

## Key Insights

1. **WebSocket infrastructure is robust** - Event emitter handles connection management, threading, disconnections

2. **Logging pattern exists but isn't used** - cv_pipeline.py has progress_callback support but synchronous stages don't use it

3. **Print statements bypass everything** - Backend stages output extensive details to console that never reach frontend

4. **Database stores only high-level logs** - The Log table only contains what orchestrator explicitly calls `self.log()`

5. **Frontend is intentionally receiving summary data** - This may be by design to keep frontend updates manageable, but it loses detailed progress visibility

6. **Threading/async consideration** - The OutputCapture class tries to bridge this but isn't integrated with actual stage code
