# Web Interface Logging Implementation

## Overview

This document explains how the web interface captures detailed backend logging and streams it to the frontend in real-time via WebSocket.

## The Problem (Historical Context)

### Before Implementation

The CV pipeline backend logged extensive operational details to the console using `print()` statements:
- Individual entry parsing progress
- Confidence scores for taxonomy mappings
- Section categorization details
- Detailed warnings and errors
- Real-time progress indicators

However, **only high-level summary messages reached the frontend** via WebSocket. Users could not see:
- Which entry was currently being processed
- Individual confidence scores
- Detailed error messages from LLM calls
- Granular progress through large sections

### Root Cause

Two separate logging paths existed:

1. **Console Path** (Detailed, Backend-Only)
   ```python
   # In cv_segmenter.py, taxonomy_mapper.py, etc.
   print("Processing section: Education...")
   print(f"  • Confidence: 0.95")
   ```
   → Output went to `stdout` only
   → Never reached database or frontend

2. **WebSocket Path** (High-Level, Frontend-Visible)
   ```python
   # In orchestrator.py
   await self.log(1, "Starting Stage 1: Hierarchical Segmentation")
   await self.log(1, "✓ Segmentation complete!")
   ```
   → Messages saved to database
   → Broadcast via WebSocket to frontend
   → Frontend displayed in real-time

## The Solution

### OutputCapture Class

An **existing but unused** `OutputCapture` class in `orchestrator.py` was activated to bridge the gap:

```python
class OutputCapture:
    """Capture stdout and send to WebSocket in real-time."""

    def __init__(self, orchestrator, step_number: int, loop):
        self.orchestrator = orchestrator
        self.step_number = step_number
        self.loop = loop
        self.buffer = []

    def write(self, text: str):
        """Capture stdout writes (thread-safe)."""
        if text and text.strip():
            # Schedule log in the main event loop (thread-safe)
            asyncio.run_coroutine_threadsafe(
                self.orchestrator.log(self.step_number, text.strip(), "INFO"),
                self.loop
            )
            self.buffer.append(text)
        return len(text)

    def flush(self):
        """Flush (required for file-like object)."""
        pass
```

**Key Features:**
- Implements file-like interface (`write()`, `flush()`)
- Thread-safe using `asyncio.run_coroutine_threadsafe()`
- Routes captured stdout to orchestrator's `log()` method
- Associates captured output with specific pipeline step

### Context Manager Integration

Added helper method to `PipelineOrchestrator`:

```python
def capture_stdout_for_step(self, step_number: int):
    """
    Context manager to capture stdout and send to WebSocket.

    Usage:
        with self.capture_stdout_for_step(1):
            result = await self.pipeline.run_stage_1_async()
    """
    loop = asyncio.get_event_loop()
    capture = OutputCapture(self, step_number, loop)
    return redirect_stdout(capture)
```

Uses Python's built-in `contextlib.redirect_stdout()` to temporarily replace `sys.stdout`.

## Implementation Details

### Pipeline Stage Wrapping

All four pipeline stages are now wrapped with stdout capture:

```python
# Stage 1: Segmentation
await self.log(1, "Starting Stage 1: Hierarchical Segmentation")
with self.capture_stdout_for_step(1):
    result = await self.pipeline.run_stage_1_async()
await self.log(1, f"✓ Segmentation complete!")

# Stage 2: Taxonomy Mapping
await self.log(2, "Starting Stage 2: LLM Taxonomy Mapping")
with self.capture_stdout_for_step(2):
    result = await self.pipeline.run_stage_2_async(stage1_result)
await self.log(2, f"✓ Taxonomy mapping complete!")

# Stage 3: Section Parsing
await self.log(3, "Starting Stage 3: Intelligent Section Parsing")
with self.capture_stdout_for_step(3):
    result = await self.pipeline.run_stage_3_async(stage1_result, stage2_result)
await self.log(3, "✓ Section parsing complete!")

# Stage 4: Template Generation
await self.log(4, "Starting Stage 4: WCM Template Generation")
with self.capture_stdout_for_step(4):
    result = await self.pipeline.run_stage_4_async(stage3_results)
await self.log(4, "✓ WCM template generation complete!")
```

### What Gets Captured

Now **all** `print()` statements from pipeline code reach the frontend:

**Stage 1 (Segmentation):**
```
CV SEGMENTER - UNIFIED INTERFACE
Input: /path/to/cv.docx
Format: .docx
Approach: Word native structure + text model

Extracting Word document structure...
  ✓ Extracted 142 elements

PASS 1: Detecting section headers...
  ✓ Found 59 potential section headers
    [1] CURRICULUM VITAE (confidence: 1.00)
    [2] II. EDUCATION (confidence: 1.00)
    [3] Professional Experience (confidence: 0.95)
    ...

PASS 2: Processing sections with chunking...
  Processing: CURRICULUM VITAE...
    → 1 chunk(s)
       Chunk 1: 1 elements, 16 characters
    ✓ Extracted 1 entries
  Processing: II. EDUCATION...
    → 1 chunk(s)
       Chunk 1: 1 elements, 13 characters
    ✓ Extracted 0 entries
    ...

PASS 3: Building hierarchical structure...
  ✓ Organized 59 flat sections into 50 top-level groups
```

**Stage 2 (Taxonomy Mapping):**
```
LLM-BASED TAXONOMY MAPPING
Input: cv_segmented.json
Using: GPT-4o-mini with Structured Outputs

Building WCM taxonomy reference...
  ✓ Loaded 86 taxonomy sections

Mapping CV sections to WCM taxonomy...

  [1] Mapping: CURRICULUM VITAE
      Entries: 1, Subgroups: 0
      → Personal Data / Contact Information
         Confidence: 0.95

  [2] Mapping: II. EDUCATION
      Entries: 0, Subgroups: 3
      → Education
         Confidence: 1.00

  [3] Mapping: Undergraduate and Graduate...
      Entries: 1, Subgroups: 2
      → Education
         Confidence: 0.95
         ...
```

**Stage 3 (Section Parsing):**
```
Parsing publications...
Parsing 21 publication entries...
  Target author: Robert U2E
  [1/21] Parsing entry...
      ✓ Multi-phenotype CRISPR-Cas9 Screen Identifies p38... (conf: 0.90)
  [2/21] Parsing entry...
      ✓ BACH2 drives quiescence and maintenance of resting... (conf: 0.90)
  [3/21] Parsing entry...
      ✓ T cell dysfunction and stemness in tumors are... (conf: 1.00)
      ...

Parsing education...
Parsing 1 education entries...
  [1/1] Parsing entry...
      ✓ MD - University of North Carolina at Chapel Hill... (conf: 1.00)
      ...

Parsing positions...
Parsing grants...
```

**Stage 4 (Template Generation):**
```
Using advanced legacy handlers for template population...

Enriching publications with PMID/PMCID lookup...
  ℹ️  No publications needed enrichment

Extracting personal information from CV...
  ✓ Extracted 387 characters from first page

Populating Section A: Personal Data...
  ⚠️  No entry or languages to populate

Populating bibliography with subsection categorization...
  → Starting deduplication across 2 subsections...
  ✓ S1: 24 entries
  ✓ S4: 1 entries
  ✓ Inserted 24 publications across 2 subsections

Populating education (13 entries)...
  ✓ Inserted 13 education entries

  ...
```

## Architecture Diagram

```
┌─────────────────────────────────────────────────────────────────┐
│                     Pipeline Stage Code                         │
│           (cv_segmenter.py, taxonomy_mapper.py, etc.)           │
│                                                                   │
│  print("Processing section: Education...")                       │
│  print(f"  • Confidence: 0.95")                                 │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            │ stdout
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│              redirect_stdout(OutputCapture(...))                 │
│                   (Context Manager Active)                       │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            │ write() calls
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│                    OutputCapture.write()                         │
│         asyncio.run_coroutine_threadsafe()                       │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                            │ async coroutine
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│               PipelineOrchestrator.log()                         │
│   - Save to database (Log table)                                │
│   - Emit via WebSocket                                          │
└───────────────────────────┬─────────────────────────────────────┘
                            │
                ┌───────────┴───────────┐
                │                       │
                ↓                       ↓
    ┌─────────────────────┐  ┌─────────────────────┐
    │   SQLite Database   │  │  event_emitter      │
    │   (cviche.db)       │  │  .emit_log()        │
    │                     │  │                     │
    │   logs table:       │  │  WebSocket          │
    │   - run_id          │  │  broadcast          │
    │   - step_number     │  └──────────┬──────────┘
    │   - level           │             │
    │   - message         │             │ JSON event
    │   - timestamp       │             ↓
    └─────────────────────┘  ┌─────────────────────┐
                             │  Connected Clients  │
                             │  (Frontend Browser) │
                             │                     │
                             │  WebSocket          │
                             │  /ws/run/{id}/stream│
                             └─────────────────────┘
```

## Data Flow

### 1. Pipeline Execution Starts
```python
# In orchestrator.py execute_step()
await self.log(1, "Starting Stage 1: Hierarchical Segmentation")
```

### 2. Stdout Redirection Activated
```python
with self.capture_stdout_for_step(1):
    result = await self.pipeline.run_stage_1_async()
```

### 3. Pipeline Prints to Stdout
```python
# In cv_segmenter.py
print("Extracting Word document structure...")
print(f"  ✓ Extracted {len(elements)} elements")
```

### 4. OutputCapture Intercepts
```python
# OutputCapture.write() called
def write(self, text: str):
    if text and text.strip():
        asyncio.run_coroutine_threadsafe(
            self.orchestrator.log(1, text.strip(), "INFO"),
            self.loop
        )
```

### 5. Log Saved & Broadcast
```python
# In orchestrator.log()
async def log(self, step_number: int, message: str, level: str = "INFO"):
    log_entry = Log(run_id=self.run_id, step_number=step_number,
                    level=level, message=message)
    self.db.add(log_entry)
    self.db.commit()
    await event_emitter.emit_log(self.run_id, step_number, message, level)
```

### 6. Frontend Receives Message
```javascript
// In PipelineViewer.tsx
ws.onmessage = (event) => {
  const data = JSON.parse(event.data);
  if (data.event === "LOG") {
    setLogs(prev => [...prev, {
      timestamp: data.timestamp,
      message: data.message,
      level: data.level
    }]);
  }
};
```

## Files Modified

### backend/app/pipeline/orchestrator.py

**Lines 89-99:** Added `capture_stdout_for_step()` helper method

**Lines 220, 257, 297, 352:** Wrapped pipeline calls with stdout capture:
```python
# Before:
result = await self.pipeline.run_stage_1_async()

# After:
with self.capture_stdout_for_step(1):
    result = await self.pipeline.run_stage_1_async()
```

## WebSocket Event Format

Messages sent to frontend follow this JSON schema:

```json
{
  "event": "LOG",
  "run_id": "ABCD123",
  "step": 1,
  "level": "INFO",
  "message": "Extracting Word document structure...",
  "timestamp": "2025-11-03T12:21:43.456789"
}
```

**Event Types:**
- `RUN_START` - Pipeline execution started
- `STEP_START` - Pipeline step started
- `LOG` - Log message from pipeline
- `STEP_COMPLETE` - Pipeline step completed
- `STEP_ERROR` - Pipeline step failed
- `RUN_COMPLETE` - Pipeline execution completed
- `RUN_ERROR` - Pipeline execution failed

## Frontend Display

The React frontend displays logs in real-time:

```tsx
// In PipelineViewer.tsx
<div className="logs-panel">
  {logs.map((log, i) => (
    <div key={i} className={`log-entry log-${log.level.toLowerCase()}`}>
      <span className="timestamp">{formatTime(log.timestamp)}</span>
      <span className="message">{log.message}</span>
    </div>
  ))}
</div>
```

**Log Levels:**
- `INFO` - Normal operation (most common)
- `WARNING` - Non-critical issues
- `ERROR` - Critical failures

## Performance Considerations

### Thread Safety

`OutputCapture` uses `asyncio.run_coroutine_threadsafe()` to safely schedule async operations from synchronous code:

```python
asyncio.run_coroutine_threadsafe(
    self.orchestrator.log(self.step_number, text.strip(), "INFO"),
    self.loop
)
```

This ensures that log messages are properly queued in the event loop without blocking.

### Database Impact

Each captured print statement creates:
1. **One database INSERT** to `logs` table
2. **One WebSocket broadcast** to all connected clients

For a typical CV processing run:
- **~500-800 log messages** generated
- **~2-4 KB per message** (JSON serialization)
- **Total: 1-3 MB of log data** per run

Database writes are synchronous but non-blocking for the main pipeline execution.

### Message Ordering

Messages are guaranteed to arrive in order due to:
1. Sequential `print()` calls in pipeline code
2. Single event loop processing messages
3. WebSocket maintaining connection order

## Troubleshooting

### Logs not appearing in frontend

**Check WebSocket connection:**
```javascript
// In browser console
ws.readyState === WebSocket.OPEN  // Should be true
```

**Verify backend logging:**
```bash
# Backend console should show
INFO:     ('127.0.0.1', 12345) - "WebSocket /ws/run/ABC123/stream" [accepted]
INFO:     connection open
```

**Check database:**
```bash
sqlite3 backend/cviche.db
SELECT COUNT(*) FROM logs WHERE run_id='ABC123';
```

### Missing log messages

**Stdout not captured:**
- Ensure `capture_stdout_for_step()` context manager is active
- Verify `print()` statements exist in pipeline code (not `logger.info()`)

**Context manager not wrapping call:**
```python
# Wrong - capture won't work
with self.capture_stdout_for_step(1):
    pass
result = await self.pipeline.run_stage_1_async()  # Outside context!

# Correct
with self.capture_stdout_for_step(1):
    result = await self.pipeline.run_stage_1_async()  # Inside context
```

### Duplicate log messages

**Multiple capture contexts:**
```python
# Wrong - nested capture
with self.capture_stdout_for_step(1):
    with self.capture_stdout_for_step(1):  # Don't nest!
        result = await self.pipeline.run_stage_1_async()
```

**Multiple orchestrator.log() calls:**
```python
# Check that you're not manually logging captured output
await self.log(1, "Starting...")
with self.capture_stdout_for_step(1):
    print("Starting...")  # Don't duplicate messages
    result = await self.pipeline.run_stage_1_async()
```

### WebSocket disconnects during long operations

**Backend timeout:**
- Ensure WebSocket keepalive is configured
- Check for firewall/proxy timeouts

**Frontend timeout:**
```javascript
// In WebSocket initialization, add ping interval
setInterval(() => {
  if (ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({type: "ping"}));
  }
}, 30000);  // Every 30 seconds
```

## Future Enhancements

### Structured Logging

Currently all captured output is treated as `INFO` level. Could parse log prefixes:

```python
def write(self, text: str):
    if text.startswith("ERROR:"):
        level = "ERROR"
    elif text.startswith("⚠️") or text.startswith("WARNING:"):
        level = "WARNING"
    else:
        level = "INFO"

    asyncio.run_coroutine_threadsafe(
        self.orchestrator.log(self.step_number, text.strip(), level),
        self.loop
    )
```

### Log Filtering

Add frontend controls to filter by:
- Log level (INFO/WARNING/ERROR)
- Step number
- Time range
- Keyword search

### Log Export

Add button to download logs:
```python
@app.get("/api/run/{run_id}/logs/export")
async def export_logs(run_id: str, db: Session = Depends(get_db)):
    logs = db.query(Log).filter(Log.run_id == run_id).all()
    return {
        "run_id": run_id,
        "logs": [{"timestamp": log.timestamp, "message": log.message} for log in logs]
    }
```

### Real-time Search

Implement client-side log search:
```javascript
const [searchTerm, setSearchTerm] = useState("");
const filteredLogs = logs.filter(log =>
  log.message.toLowerCase().includes(searchTerm.toLowerCase())
);
```

## Related Documentation

- **Architecture:** See `web_interface/README.md` for overall system design
- **WebSocket Events:** See `backend/app/pipeline/event_emitter.py` for event definitions
- **Database Schema:** See `backend/app/models.py` for `Log` table structure
- **Pipeline Stages:** See `src/unified_pipeline/` for pipeline implementation

## References

- Python `contextlib.redirect_stdout()`: https://docs.python.org/3/library/contextlib.html#contextlib.redirect_stdout
- `asyncio.run_coroutine_threadsafe()`: https://docs.python.org/3/library/asyncio-task.html#asyncio.run_coroutine_threadsafe
- FastAPI WebSockets: https://fastapi.tiangolo.com/advanced/websockets/
