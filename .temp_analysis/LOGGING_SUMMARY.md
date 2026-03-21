# Logging & WebSocket Communication - Executive Summary

## The Problem

Your CV pipeline backend logs extensive operational details to the **console** during execution:
- Individual entry parsing progress
- Confidence scores and confidence breakdowns  
- Taxonomy mapping decisions
- Section categorization results
- Detailed warnings and errors

However, the **frontend only receives high-level summary messages** via WebSocket.

---

## Why This Happens

### The Two-Path Architecture

1. **Console Path** (Detailed, Backend-Only)
   - All pipeline stages use `print()` statements
   - Output goes to stdout/stderr
   - Visible in backend terminal
   - Never reaches database or frontend
   - What frontend sees: Nothing (except error stack traces)

2. **WebSocket Path** (High-Level, Frontend-Visible)
   - Orchestrator explicitly calls `await self.log()`
   - Messages saved to database
   - Broadcast via WebSocket to frontend
   - Frontend displays in UI in real-time
   - What frontend sees: Only manually-logged summary messages

### Example: Stage 1 (Segmentation)

When Stage 1 executes:

```python
# In orchestrator.py (web_interface/backend/app/pipeline/orchestrator.py:208)
result = await self.pipeline.run_stage_1_async()

# Backend (src/unified_pipeline/core/cv_segmenter.py:99-143) prints:
# "CV SEGMENTER - UNIFIED INTERFACE"
# "Input: /path/to/cv.pdf"
# "Format: pdf"
# "Approach: Three-pass vision segmentation"
# "Pass 1: GESTALT (map all sections)"
# "Pass 2: TRIAGE (count items, decide chunking)"
# "Pass 3: EXTRACTION (get entries, chunked if needed)"
# ^^^^^^^^ NONE OF THIS REACHES THE FRONTEND

# Only these orchestrator.log() calls reach frontend:
await self.log(1, f"✓ Segmentation complete!")
await self.log(1, f"  • Sections: {result['num_sections']}")
await self.log(1, f"  • Entries: {result['total_entries']}")
```

---

## File Locations Summary

### WebSocket Infrastructure (3 files)
1. **Endpoint**: `web_interface/backend/app/api/websocket.py`
   - WebSocket route handler
   - Connection/disconnection management
   
2. **Event Broadcaster**: `web_interface/backend/app/pipeline/event_emitter.py`
   - `EventEmitter` class with `emit_log()` method
   - Maintains connection registry
   - Broadcasts JSON events
   
3. **Database**: `web_interface/backend/app/models.py`
   - `Log` table schema
   - Stores all logged messages

### Pipeline Orchestration (1 file)
4. **Orchestrator**: `web_interface/backend/app/pipeline/orchestrator.py`
   - `PipelineOrchestrator` class
   - `execute_step()` method calls stages
   - `log()` method sends to database + WebSocket
   - `OutputCapture` class (unused - was meant to capture print output)

### Pipeline Stages (7 files) - CONSOLE LOGGING ONLY
5. `src/unified_pipeline/core/cv_segmenter.py` (Stage 1)
6. `src/unified_pipeline/core/taxonomy_mapper.py` (Stage 2)
7. `src/unified_pipeline/parsers/publications_parser.py` (Stage 3)
8. `src/unified_pipeline/parsers/education_parser.py` (Stage 3)
9. `src/unified_pipeline/parsers/positions_parser.py` (Stage 3)
10. `src/unified_pipeline/parsers/grants_parser.py` (Stage 3)
11. `src/unified_pipeline/core/cv_pipeline.py` (Stage 4)

All use `print()` statements exclusively.

---

## What Frontend Actually Receives

### WebSocket Events (Real-Time)
```json
{
  "event": "RUN_START",
  "timestamp": "2025-11-03T10:15:22.123456"
}

{
  "event": "STEP_START",
  "step": 1,
  "timestamp": "2025-11-03T10:15:22.234567"
}

{
  "event": "LOG",
  "step": 1,
  "level": "INFO",
  "message": "Starting Stage 1: Hierarchical Segmentation",
  "timestamp": "2025-11-03T10:15:22.345678"
}

{
  "event": "LOG",
  "step": 1,
  "level": "INFO",
  "message": "✓ Segmentation complete!",
  "timestamp": "2025-11-03T10:15:35.456789"
}

{
  "event": "STEP_COMPLETE",
  "step": 1,
  "duration": 13,
  "cost": 0.0045,
  "output_files": ["stage_1_segmentation/cv_segmented.json"],
  "timestamp": "2025-11-03T10:15:35.567890"
}
```

These are the ONLY messages the frontend receives.

---

## What Frontend Is Missing

Everything the stages print:
- Individual entry parsing progress
- Confidence scores (e.g., "Confidence: 0.87")
- Taxonomy mapping decisions
- Section categorization details
- Entry counts per category
- Error details from LLM parsing

### Example: Stage 2 (Taxonomy Mapping)

**What backend logs to console** (not visible to frontend):
```
LLM-BASED TAXONOMY MAPPING
Input: cv_segmented.json
Using: GPT-4o-mini with Structured Outputs

Building WCM taxonomy reference...
  ✓ Loaded 45 taxonomy sections

Mapping CV sections to WCM taxonomy...

  [1] Mapping: Education
      Entries: 3, Subgroups: 0
      → Academic Education
      Confidence: 0.95
  [2] Mapping: Research Experience  
      Entries: 5, Subgroups: 0
      → Professional Positions
      Confidence: 0.87
      ⚠️ Low confidence - ambiguous section label
```

**What frontend receives** (if anything):
```
Nothing (unless orchestrator manually logs it)
```

---

## Why This Design Exists

1. **Simplicity**: Only high-level events reduce WebSocket traffic
2. **Decoupling**: Pipeline code doesn't depend on orchestrator
3. **Reusability**: Stages can be run via CLI or web app
4. **Performance**: Less frequent database writes

---

## Why It's a Problem

1. **Poor Visibility**: Users can't see detailed progress
2. **Debugging**: Can't diagnose issues from UI
3. **Confidence Scores**: Important QA data is hidden
4. **Errors**: Detailed error messages stuck in console
5. **Bottleneck Detection**: Can't identify which step/entry is slow

---

## How It Works Currently

### Console Path
```
Pipeline.run_stage_1_segmenter()
    → print("Starting segmentation...")
    → print(f"Processing {num_sections} sections")
    → print(f"✓ Complete!")
    ↓
stdout/stderr
    ↓
Backend terminal/logs
    ↓
X Frontend (not visible)
```

### WebSocket Path
```
Orchestrator.execute_step()
    → await self.log(1, "Starting Stage 1...")
    → result = await pipeline.run_stage_1_async()
    → await self.log(1, "✓ Segmentation complete!")
    ↓
    ├→ Database (Log table)
    └→ event_emitter.emit_log()
        ↓
        WebSocket broadcast
        ↓
        Frontend receives in real-time
```

---

## Key Insight: The Unused OutputCapture Class

There IS a class in `orchestrator.py` (lines 33-56) designed to capture stdout:

```python
class OutputCapture:
    """Capture stdout and send to WebSocket in real-time."""
    def write(self, text: str):
        """Capture stdout writes (thread-safe)."""
        if text and text.strip():
            asyncio.run_coroutine_threadsafe(
                self.orchestrator.log(...),
                self.loop
            )
```

**This class is never used.** It was designed to solve this exact problem but isn't integrated.

---

## How to Bridge the Gap

### Option 1: Use OutputCapture (Simpler)
Activate the unused `OutputCapture` class in the orchestrator to redirect stdout.

**Pros**: Minimal changes, automatic capture  
**Cons**: Less clean, potential performance impact

### Option 2: Modify Stages to Use Callbacks (Cleaner)
Update pipeline stages to accept optional `progress_callback`:

```python
# cv_segmenter.py
def segment(self, file_path: str, progress_callback=None):
    # Instead of print(), call callback
    if progress_callback:
        await progress_callback(1, "Starting segmentation...")
    else:
        print("Starting segmentation...")
```

**Pros**: Clean architecture, explicit logging  
**Cons**: Requires modifying 7 stage files

### Option 3: Capture stdout with redirect_stdout
Redirect `sys.stdout` during stage execution (already imported in orchestrator).

**Pros**: Zero changes to stage code  
**Cons**: Must ensure proper cleanup

---

## What Needs to Happen

To get detailed logs to the frontend:

1. **Identify all print() statements** in pipeline stages
2. **Choose bridging mechanism** (OutputCapture, callbacks, or stdout redirect)
3. **Implement chosen approach**
4. **Test WebSocket message flow**
5. **Update frontend** to handle detailed messages

---

## Detailed File References

| File | Purpose | Key Sections |
|------|---------|--------------|
| `web_interface/backend/app/api/websocket.py` | WebSocket endpoint | Lines 12-47 |
| `web_interface/backend/app/pipeline/event_emitter.py` | Broadcasting | Lines 8-68 |
| `web_interface/backend/app/pipeline/orchestrator.py` | Orchestration | Lines 58-181 |
| `src/unified_pipeline/core/cv_segmenter.py` | Stage 1 (segmentation) | Lines 99-143 |
| `src/unified_pipeline/core/taxonomy_mapper.py` | Stage 2 (mapping) | Lines 180-260 |
| `src/unified_pipeline/parsers/*.py` | Stage 3 (parsing) | Lines ~120-160 |
| `src/unified_pipeline/core/cv_pipeline.py` | Stage 4 (template) | Lines 645-915 |

---

## See Also

- `LOGGING_WEBSOCKET_ANALYSIS.md` - Detailed architectural analysis
- `LOGGING_CODE_REFERENCE.md` - Complete code excerpts with line numbers
- `LOGGING_FILE_LOCATIONS.md` - Quick reference with all file paths

