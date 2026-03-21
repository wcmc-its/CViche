# Logging & WebSocket Communication Documentation

Complete analysis of how logging and WebSocket communication work in the CV parsing pipeline.

## Documents Overview

### 1. LOGGING_SUMMARY.md (Start Here)
**8.8 KB** - High-level executive summary

Best for: Understanding the overall problem and architecture at a glance

Contains:
- The problem statement
- Two-path architecture explanation  
- File locations summary
- What frontend receives vs. what it's missing
- Why this design exists
- Three options for fixing it

**Read this first** to understand the big picture.

---

### 2. LOGGING_WEBSOCKET_ANALYSIS.md
**12 KB** - Detailed architectural analysis

Best for: Understanding the complete system design and how all components interact

Contains:
- Detailed WebSocket infrastructure overview
- Pipeline orchestration explanation
- All 4 pipeline stages and their logging
- The disconnect diagram
- Progress callback pattern explanation
- Database schema
- Why frontend only sees high-level messages
- Key insights and findings

**Read this for** comprehensive understanding of the entire system.

---

### 3. LOGGING_CODE_REFERENCE.md
**21 KB** - Complete code excerpts with line numbers

Best for: Examining actual code and understanding implementation details

Contains:
- WebSocket endpoint implementation (lines 12-47)
- Event Emitter class (lines 8-68)
- Orchestrator logging method (lines 82-87)
- Complete method implementations with code samples
- Stage 1, 2, 3, 4 execution examples
- Database Log model
- Log API endpoint implementation
- Unused OutputCapture class
- Progress callback pattern (partially implemented)

**Read this to** see actual code implementations.

---

### 4. LOGGING_FILE_LOCATIONS.md
**9.3 KB** - Quick reference with file paths and line numbers

Best for: Finding specific code locations and understanding file organization

Contains:
- Quick reference tables for all components
- Exact file paths for all logging-related code
- Line number references
- Component organization
- How to find specific logging points (bash commands)
- Critical code sections ready to copy-paste
- The disconnect point explained
- Solutions outlined

**Use this as** a quick lookup reference.

---

## Navigation Guide

### If you want to...

**Understand the problem quickly**
→ Read: `LOGGING_SUMMARY.md` (5-10 minutes)

**Understand the complete architecture**
→ Read: `LOGGING_WEBSOCKET_ANALYSIS.md` + `LOGGING_SUMMARY.md` (15-20 minutes)

**Find specific code**
→ Use: `LOGGING_FILE_LOCATIONS.md` (quick lookup)

**See actual implementations**
→ Read: `LOGGING_CODE_REFERENCE.md` (detailed code review)

**Make changes to the code**
→ Use: `LOGGING_FILE_LOCATIONS.md` for file locations, then `LOGGING_CODE_REFERENCE.md` for implementation details

---

## Key Findings Summary

### The Problem
- Backend logs extensive details to console (print statements)
- Frontend only receives high-level summary messages via WebSocket
- Detailed progress, confidence scores, and error details are hidden from UI

### The Root Cause
- Pipeline stages use `print()` for all logging
- Orchestrator explicitly calls `await self.log()` for database/WebSocket messages
- No connection between these two logging systems

### The Architecture
```
Pipeline Stages (print) → Console/Stdout → Nowhere (not captured)
          ↓
Orchestrator (self.log) → Database + WebSocket → Frontend
```

### The Components
1. **WebSocket Endpoint** - Accepts connections
2. **Event Emitter** - Broadcasts messages to all clients
3. **Orchestrator** - Executes stages and manually logs
4. **Pipeline Stages** - Use print() exclusively
5. **Database** - Stores only manually-logged messages

### The Solution
Three options to bridge the gap:
1. **Use OutputCapture** (simpler, less clean)
2. **Add progress callbacks** (cleaner, more changes)
3. **Redirect stdout** (zero changes to pipeline code)

---

## File Organization

### WebSocket Infrastructure
- `web_interface/backend/app/api/websocket.py` (12-47)
- `web_interface/backend/app/pipeline/event_emitter.py` (8-68)

### Orchestration & Logging
- `web_interface/backend/app/pipeline/orchestrator.py` (33-371)
- `web_interface/backend/app/api/steps.py` (15-73)
- `web_interface/backend/app/models.py` (Log class)

### Pipeline Stages (Console Only)
- `src/unified_pipeline/core/cv_segmenter.py` (Stage 1)
- `src/unified_pipeline/core/taxonomy_mapper.py` (Stage 2)
- `src/unified_pipeline/parsers/publications_parser.py` (Stage 3)
- `src/unified_pipeline/parsers/education_parser.py` (Stage 3)
- `src/unified_pipeline/parsers/positions_parser.py` (Stage 3)
- `src/unified_pipeline/parsers/grants_parser.py` (Stage 3)
- `src/unified_pipeline/core/cv_pipeline.py` (Stage 4)

---

## Quick Reference Table

| Aspect | Location | Key Function |
|--------|----------|--------------|
| WebSocket Endpoint | `websocket.py:12-47` | `websocket_stream()` |
| Event Broadcasting | `event_emitter.py:28` | `emit()` |
| Log Sending | `event_emitter.py:54` | `emit_log()` |
| DB + WebSocket Logging | `orchestrator.py:82` | `log()` |
| Step Execution | `orchestrator.py:129` | `execute_step()` |
| Stage 1 (Prints) | `cv_segmenter.py:99-143` | `segment()` |
| Stage 2 (Prints) | `taxonomy_mapper.py:180-260` | `map_cv_sections()` |
| Stage 3 (Prints) | `*_parser.py` | `parse_*_section()` |
| Stage 4 (Prints) | `cv_pipeline.py:645-915` | `run_stage_4_template_generation()` |
| Log Database | `models.py` | `Log` class |
| Log Retrieval | `steps.py:34-47` | Query logs |
| Unused Capture | `orchestrator.py:33-56` | `OutputCapture` |

---

## Key Code Snippets

### WebSocket Message Example
```python
await event_emitter.emit_log(run_id, step_number, "Segmentation complete!", "INFO")
# Sends to frontend:
# {"event": "LOG", "step": 1, "level": "INFO", "message": "...", "timestamp": "..."}
```

### The Disconnect Point
```python
# orchestrator.py line 208:
result = await self.pipeline.run_stage_1_async()
# ↑ All print() output goes to console, not captured
# ↓ Only these reach frontend:
await self.log(1, "✓ Segmentation complete!")
```

### What Print Goes to Console
```python
# cv_segmenter.py lines 99-143:
print("CV SEGMENTER - UNIFIED INTERFACE")
print(f"Input: {file_path}")
print(f"Format: {file_format}")
print("Approach: Three-pass vision segmentation")
# ↑ None of this reaches the frontend
```

---

## Common Questions

**Q: Where do print statements appear?**
A: Backend console/terminal, not visible to frontend UI

**Q: How does frontend get updates?**
A: Via WebSocket events sent by `event_emitter.emit_log()`

**Q: Why not capture all print output?**
A: There's an unused `OutputCapture` class (line 33) that was meant to do this

**Q: Which files use console logging?**
A: All 7 pipeline stage files (segmenter, taxonomy_mapper, and 5 parsers)

**Q: Which files use WebSocket?**
A: Only the orchestrator.py (lines 82-87) via explicit `self.log()` calls

**Q: What does the database store?**
A: Only messages that orchestrator explicitly logs via `self.log()`

**Q: How many WebSocket events are sent?**
A: Typically 7-10 per run (RUN_START, 4x STEP_START, 4x STEP_COMPLETE, RUN_COMPLETE)

**Q: How many log messages reach the frontend?**
A: As few as 4 (just the STEP_COMPLETE events) up to 20+ if orchestrator logs details

---

## Implementation Notes

### For Adding Detailed Logging
1. Choose a bridging mechanism (OutputCapture, callbacks, or stdout redirect)
2. Test with a small CV first (faster iteration)
3. Monitor WebSocket message frequency (don't spam frontend)
4. Consider batching messages for efficiency
5. Add error handling for disconnected clients

### For Debugging
```bash
# Find all print statements
grep -rn "print(" src/unified_pipeline/
grep -rn "print(" web_interface/backend/app/

# Find WebSocket emissions
grep -rn "emit_" web_interface/backend/app/

# Find database logs
grep -rn "self.log(" web_interface/backend/app/
```

---

## Document Maintenance

Last Updated: 2025-11-03

These documents are based on a complete analysis of:
- WebSocket infrastructure (3 files)
- Pipeline orchestration (1 file)
- Pipeline stages (7 files)
- Database models (1 file)
- API endpoints (1 file)

Total files analyzed: 13

---

## Further Reading

See the individual documents for:
- **LOGGING_SUMMARY.md**: Problem statement and high-level overview
- **LOGGING_WEBSOCKET_ANALYSIS.md**: Complete architectural details
- **LOGGING_CODE_REFERENCE.md**: Full code implementations
- **LOGGING_FILE_LOCATIONS.md**: File paths and line numbers

