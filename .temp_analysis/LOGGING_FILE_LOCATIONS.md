# Logging & WebSocket - Exact File Locations

## Quick Reference - File Paths

### WebSocket Infrastructure
| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| WebSocket Endpoint | `web_interface/backend/app/api/websocket.py` | 12-47 | Handles WebSocket connections |
| Event Emitter | `web_interface/backend/app/pipeline/event_emitter.py` | 8-68 | Broadcasts events to WebSocket clients |
| Event Emitter Methods | `web_interface/backend/app/pipeline/event_emitter.py` | 48-64 | emit_run_start, emit_step_start, emit_log, emit_step_complete, emit_step_error, emit_run_complete |

### Pipeline Orchestration
| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| Orchestrator Class | `web_interface/backend/app/pipeline/orchestrator.py` | 58-181 | Executes pipeline stages |
| OutputCapture Class | `web_interface/backend/app/pipeline/orchestrator.py` | 33-56 | Unused: was meant to capture stdout |
| log() Method | `web_interface/backend/app/pipeline/orchestrator.py` | 82-87 | Logs to DB + WebSocket |
| execute_step() | `web_interface/backend/app/pipeline/orchestrator.py` | 129-182 | Main step execution logic |
| _execute_step_logic() | `web_interface/backend/app/pipeline/orchestrator.py` | 184-371 | Individual stage implementations |

### Database & API
| Component | File | Lines | Purpose |
|-----------|------|-------|---------|
| Log Model | `web_interface/backend/app/models.py` | (search for "class Log") | Database schema for logs |
| get_step_detail() | `web_interface/backend/app/api/steps.py` | 15-73 | Retrieves logs for a step |
| Log Retrieval Logic | `web_interface/backend/app/api/steps.py` | 34-47 | Database query + formatting |

### Pipeline Stages (Console Logging Only)
| Stage | File | Lines | Logging Pattern |
|-------|------|-------|-----------------|
| Stage 1: Segmentation | `src/unified_pipeline/core/cv_segmenter.py` | 99-143 | print() statements |
| Stage 2: Taxonomy Mapping | `src/unified_pipeline/core/taxonomy_mapper.py` | 180-260 | print() statements |
| Stage 3: Publications Parser | `src/unified_pipeline/parsers/publications_parser.py` | (search for "Parsing") | print() statements |
| Stage 3: Education Parser | `src/unified_pipeline/parsers/education_parser.py` | (search for "Parsing") | print() statements |
| Stage 3: Positions Parser | `src/unified_pipeline/parsers/positions_parser.py` | (search for "Parsing") | print() statements |
| Stage 3: Grants Parser | `src/unified_pipeline/parsers/grants_parser.py` | (search for "Parsing") | print() statements |
| Stage 4: Template Generation | `src/unified_pipeline/core/cv_pipeline.py` | 645-915 | print() statements (run_stage_4_template_generation) |

### Progress Callback Pattern
| Component | File | Lines | Status |
|-----------|------|-------|--------|
| Progress Callback Parameter | `src/unified_pipeline/core/cv_pipeline.py` | 106-134 | Defined but underutilized |
| _log() Method | `src/unified_pipeline/core/cv_pipeline.py` | 162-174 | Pattern exists, rarely used |
| run_stage_*_async() Methods | `src/unified_pipeline/core/cv_pipeline.py` | 1016-1087 | Async wrappers for web integration |

---

## Detailed Line Number References

### 1. WebSocket Endpoint Definition
**File**: `web_interface/backend/app/api/websocket.py`

```
Line 12:    @router.websocket("/ws/run/{run_id}/stream")
Line 13:    async def websocket_stream(websocket: WebSocket, run_id: str):
...
Line 28:    await event_emitter.connect(run_id, websocket)
Line 31-39: while True loop listening for messages
Line 46:    event_emitter.disconnect(run_id, websocket)
```

### 2. Event Emitter Definition
**File**: `web_interface/backend/app/pipeline/event_emitter.py`

```
Line 8:     class EventEmitter:
Line 14:    async def connect(self, run_id: str, websocket: WebSocket)
Line 21:    def disconnect(self, run_id: str, websocket: WebSocket)
Line 28:    async def emit(self, run_id: str, event: dict)
Line 54:    async def emit_log(self, run_id: str, step_number: int, message: str, level: str)
Line 51:    async def emit_step_start(self, run_id: str, step_number: int)
Line 57:    async def emit_step_complete(...)
Line 60:    async def emit_step_error(...)
Line 63:    async def emit_run_complete(...)
Line 68:    event_emitter = EventEmitter()  # Global instance
```

### 3. Orchestrator Logging Method
**File**: `web_interface/backend/app/pipeline/orchestrator.py`

```
Line 33:    class OutputCapture:  # UNUSED CLASS
Line 82:    async def log(self, step_number: int, message: str, level: str)
Line 84:    log_entry = Log(run_id=self.run_id, step_number=...)
Line 87:    await event_emitter.emit_log(...)
Line 96:    await event_emitter.emit_run_start(self.run_id)
Line 148:   await event_emitter.emit_step_start(...)
Line 149:   await self.log(step_number, f"Starting {step_def.name}")
Line 166:   await event_emitter.emit_step_complete(...)
Line 181:   await event_emitter.emit_step_error(...)
Line 203:   await self.log(1, "Starting Stage 1: Hierarchical Segmentation")
```

### 4. Stage 1 Execution
**File**: `web_interface/backend/app/pipeline/orchestrator.py` Lines 201-229

The critical section where Stage 1 is executed:
- Line 208: `result = await self.pipeline.run_stage_1_async()`
- Lines 210-212: Manual logging of summary (these reach frontend)
- Everything from print() inside `run_stage_1_async()` (in cv_segmenter.py) is NOT captured

### 5. Taxonomy Mapper Logging
**File**: `src/unified_pipeline/core/taxonomy_mapper.py`

```
Line 180:   print("="*80)
Line 181:   print("LLM-BASED TAXONOMY MAPPING")
Line 182:   print("="*80)
...
Line 230:   print(f"{indent}[{len(mappings)+1}] Mapping: {section_label}")
...
Line 260:   print(f"Average confidence: {avg_confidence:.3f}")
```

All these print statements go to console, NOT to WebSocket.

### 6. Section Parser Logging (Publications Example)
**File**: `src/unified_pipeline/parsers/publications_parser.py`

```
Line 123:   print(f"Parsing {len(entries)} publication entries...")
Line 131:   print(f"  [{idx}/{len(entries)}] Parsing entry...")
Line 150:   print(f"      {conf_emoji} {publication['title'][:60]}... (conf: {confidence:.2f})")
Line 163:   print("="*80)
Line 164:   print("RESULTS")
```

All progress messages are printed to console only.

### 7. CV Pipeline Progress Callback
**File**: `src/unified_pipeline/core/cv_pipeline.py`

```
Line 110:   progress_callback: Optional[Callable[[int, str, str], Awaitable[None]]] = None
Line 134:   self.progress_callback = progress_callback
Line 162:   async def _log(self, stage: int, message: str, level: str = "INFO"):
Line 171-174: if self.progress_callback: await self.progress_callback(...) else: print(...)
```

The pattern exists but is NOT used in actual stage methods.

### 8. Stage 4 Execution
**File**: `src/unified_pipeline/core/cv_pipeline.py` Lines 645-915

The run_stage_4_template_generation method contains:
```
Line 654-657: print() statements for Stage 4 headers
Line 681-684: Loaded item counts (printed)
Line 693-699: Using legacy handlers (printed)
...
Line 698-699: Enriching publications (printed)
```

All of these go to console only.

### 9. Database Log Model
**File**: `web_interface/backend/app/models.py`

Search for "class Log" in this file to find:
```
Column definitions:
- id: Primary key
- run_id: Foreign key to Run
- step_number: Which step
- level: INFO/WARNING/ERROR
- message: Log message text
- timestamp: When logged
```

### 10. Log API Endpoint
**File**: `web_interface/backend/app/api/steps.py`

```
Line 15:    @router.get("/run/{run_id}/step/{step_number}")
Line 34-38: logs = db.query(Log).filter(Log.run_id == run_id, Log.step_number == step_number)
Line 40-46: Convert to LogEntry objects for response
```

---

## How to Find Specific Logging Points

### Find all print statements in a pipeline stage:
```bash
grep -n "print(" src/unified_pipeline/core/cv_segmenter.py
grep -n "print(" src/unified_pipeline/core/taxonomy_mapper.py
grep -n "print(" src/unified_pipeline/parsers/publications_parser.py
```

### Find all WebSocket emit calls:
```bash
grep -n "emit_" web_interface/backend/app/pipeline/orchestrator.py
```

### Find all database log calls:
```bash
grep -n "self.log(" web_interface/backend/app/pipeline/orchestrator.py
```

### Find the event_emitter usage:
```bash
grep -rn "event_emitter" web_interface/backend/app/
```

---

## Critical Code Sections (Copy-Paste Ready)

### The Disconnect Point
**File**: `web_interface/backend/app/pipeline/orchestrator.py` (Stage 1 example, line 208)

```python
# This line calls the actual pipeline stage:
result = await self.pipeline.run_stage_1_async()

# All the print() statements inside this async function go to console
# These are the ONLY messages sent to frontend:
await self.log(1, f"✓ Segmentation complete!")
await self.log(1, f"  • Sections: {result['num_sections']}")
```

### The Solution Would Be
Modify the pipeline stages to accept and use a `progress_callback` parameter:

1. **cv_segmenter.py**: Add `progress_callback` parameter
2. **taxonomy_mapper.py**: Add `progress_callback` parameter  
3. **publications_parser.py**: Add `progress_callback` parameter
4. **education_parser.py**: Add `progress_callback` parameter
5. **positions_parser.py**: Add `progress_callback` parameter
6. **grants_parser.py**: Add `progress_callback` parameter
7. **orchestrator.py**: Pass `progress_callback` when initializing pipeline
8. Or: Use OutputCapture class to redirect stdout (simpler but less clean)

