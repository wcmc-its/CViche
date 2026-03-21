# Logging & WebSocket Code Reference

## 1. WebSocket Endpoint

**Location**: `/web_interface/backend/app/api/websocket.py`

```python
@router.websocket("/ws/run/{run_id}/stream")
async def websocket_stream(websocket: WebSocket, run_id: str):
    """WebSocket endpoint for streaming pipeline events."""
    
    # Verify run exists
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        run = db.query(Run).filter(Run.id == run_id).first()
        if not run:
            await websocket.close(code=1008, reason=f"Run {run_id} not found")
            return
    finally:
        db.close()
    
    # Connect WebSocket
    await event_emitter.connect(run_id, websocket)
    
    try:
        # Keep connection alive and listen for messages
        while True:
            try:
                message = await websocket.receive_text()
                # Handle client messages if needed
            except WebSocketDisconnect:
                break
    except Exception as e:
        print(f"WebSocket error: {e}")
    finally:
        # Disconnect when done
        event_emitter.disconnect(run_id, websocket)
```

**How it works**:
1. Accepts WebSocket connection for a specific run_id
2. Verifies run exists in database
3. Registers connection with event_emitter
4. Keeps connection alive, listening for incoming messages
5. On disconnect, unregisters connection

---

## 2. Event Emitter (Broadcasting)

**Location**: `/web_interface/backend/app/pipeline/event_emitter.py`

```python
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
    
    # High-level event methods
    async def emit_log(self, run_id: str, step_number: int, 
                      message: str, level: str = "INFO"):
        await self.emit(run_id, {
            "event": "LOG", 
            "step": step_number, 
            "level": level, 
            "message": message
        })
    
    async def emit_step_start(self, run_id: str, step_number: int):
        await self.emit(run_id, {"event": "STEP_START", "step": step_number})
    
    async def emit_step_complete(self, run_id: str, step_number: int, 
                                duration: int, cost: float, output_files: list):
        await self.emit(run_id, {
            "event": "STEP_COMPLETE", 
            "step": step_number, 
            "duration": duration, 
            "cost": cost, 
            "output_files": output_files
        })
    
    async def emit_step_error(self, run_id: str, step_number: int, error: str):
        await self.emit(run_id, {
            "event": "STEP_ERROR", 
            "step": step_number, 
            "error": error
        })
    
    async def emit_run_complete(self, run_id: str, total_cost: float, 
                               total_tokens: int, duration: int):
        await self.emit(run_id, {
            "event": "RUN_COMPLETE", 
            "total_cost": total_cost, 
            "total_tokens": total_tokens, 
            "duration": duration
        })

# Global instance
event_emitter = EventEmitter()
```

**Key Points**:
- Maintains a dictionary of connections: `{run_id -> Set[WebSocket]}`
- `emit()` sends to ALL connections for a run_id
- Automatically adds timestamp
- Handles disconnections gracefully
- JSON serializes events before sending

---

## 3. Orchestrator Logging

**Location**: `/web_interface/backend/app/pipeline/orchestrator.py` (Lines 82-87, 148-173)

### Logging Method
```python
async def log(self, step_number: int, message: str, level: str = "INFO"):
    """Log a message to database and emit via WebSocket."""
    log_entry = Log(run_id=self.run_id, step_number=step_number, 
                   level=level, message=message)
    self.db.add(log_entry)
    self.db.commit()
    await event_emitter.emit_log(self.run_id, step_number, message, level)
```

**What it does**:
1. Creates Log database entry
2. Commits to database
3. Emits to all WebSocket clients
4. Ensures message reaches both storage AND frontend

### WebSocket Emissions in execute_step()
```python
async def execute_step(self, step_number: int):
    """Execute a single step."""
    step_def = STEP_REGISTRY[step_number - 1]
    
    # ... create/update step record ...
    
    try:
        step.status = "running"
        step.started_at = datetime.now()
        self.db.commit()
        
        await event_emitter.emit_step_start(self.run_id, step_number)
        await self.log(step_number, f"Starting {step_def.name}")
        
        start_time = time.time()
        
        # Execute the actual step logic
        result = await self._execute_step_logic(step_number)
        
        duration = int(time.time() - start_time)
        
        # Mark as complete
        step.status = "complete"
        step.completed_at = datetime.now()
        step.duration_seconds = duration
        step.cost = result.get("cost", 0.0)
        step.output_files = json.dumps(result.get("output_files", []))
        self.db.commit()
        
        await self.log(step_number, f"Completed {step_def.name} in {duration}s")
        await event_emitter.emit_step_complete(
            self.run_id,
            step_number,
            duration,
            step.cost,
            result.get("output_files", [])
        )
    
    except Exception as e:
        # ... error handling ...
        await self.log(step_number, f"Error in {step_def.name}: {str(e)}", "ERROR")
        await event_emitter.emit_step_error(self.run_id, step_number, str(e))
        raise
```

### Stage 1 Execution (Example)
```python
if step_number == 1:
    # Stage 1: Segmentation (using integrated cv_pipeline.py)
    await self.log(1, "Starting Stage 1: Hierarchical Segmentation")
    await self.log(1, "Processing CV using integrated pipeline...")
    
    try:
        # Call integrated pipeline Stage 1
        result = await self.pipeline.run_stage_1_async()
        
        # These are the ONLY logs sent to frontend
        await self.log(1, f"✓ Segmentation complete!")
        await self.log(1, f"  • Sections: {result['num_sections']}")
        await self.log(1, f"  • Entries: {result['total_entries']}")
        
        # ... save files ...
        
        # Cost calculation and log
        token_usage = result.get('token_usage', {})
        if token_usage:
            cost = (token_usage.get('prompt_tokens', 0) * 0.150 / 1_000_000 +
                   token_usage.get('completion_tokens', 0) * 0.600 / 1_000_000)
            await self.log(1, f"  • Cost: ${cost:.4f}")
    
    except Exception as e:
        await self.log(1, f"ERROR: {str(e)}", "ERROR")
        raise
```

**The Disconnect**: The detailed output from `self.pipeline.run_stage_1_async()` (which contains all the print statements) is not captured here. Only these summary messages reach the frontend.

---

## 4. Pipeline Stage Logging (Example: CVSegmenter)

**Location**: `/src/unified_pipeline/core/cv_segmenter.py` (Lines 99-143)

```python
def segment(self, file_path: str, output_dir: Optional[str] = None) -> Dict[str, Any]:
    """
    Segment CV into hierarchical sections and entries.
    """
    # Validate file exists
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"CV file not found: {file_path}")
    
    # Detect format
    file_format = self.detect_format(file_path)
    
    # THESE PRINT STATEMENTS ARE NOT CAPTURED BY ORCHESTRATOR
    print("="*80)
    print("CV SEGMENTER - UNIFIED INTERFACE")
    print("="*80)
    print(f"Input: {file_path}")
    print(f"Format: {file_format}")
    
    # Route to appropriate segmenter
    if file_format == '.pdf':
        print("Approach: Three-pass vision segmentation")
        print("  Pass 1: GESTALT (map all sections)")
        print("  Pass 2: TRIAGE (count items, decide chunking)")
        print("  Pass 3: EXTRACTION (get entries, chunked if needed)")
        print()
        
        result = segment_cv_three_pass(file_path, output_dir)
        result['format'] = 'pdf'
        result['approach'] = 'three-pass-vision'
    
    elif file_format == '.docx':
        print("Approach: Word native structure + text model")
        print("  - Exploits Word styles, lists, tables")
        print("  - Uses GPT-4o with Structured Outputs")
        print("  - 6-12x faster and cheaper than PDF")
        print()
        
        result = segment_word_cv_chunked(file_path, output_dir)
        result['format'] = 'docx'
        result['approach'] = 'word-chunked-hierarchical'
    
    print()
    print("="*80)
    print("SEGMENTATION COMPLETE")
    print("="*80)
    print(f"Format: {result['format']}")
    print(f"Approach: {result['approach']}")
    print(f"Sections: {result['num_sections']}")
    print(f"Entries: {result['total_entries']}")
    print(f"Output: {result['output_file']}")
    print()
    
    return result
```

**Issue**: All this output goes to stdout/stderr, NOT to WebSocket.

---

## 5. Taxonomy Mapper Logging

**Location**: `/src/unified_pipeline/core/taxonomy_mapper.py` (Lines 180-260)

```python
def map_cv_sections(segmented_cv_path: str, output_path: str = None) -> Dict[str, Any]:
    """
    Map CV sections to WCM taxonomy using LLM.
    """
    print("="*80)
    print("LLM-BASED TAXONOMY MAPPING")
    print("="*80)
    print(f"Input: {segmented_cv_path}")
    print(f"Using: GPT-4o-mini with Structured Outputs")
    print()
    
    print("Building WCM taxonomy reference...")
    print(f"  ✓ Loaded {len(CV_SECTIONS)} taxonomy sections")
    print()
    
    print("Mapping CV sections to WCM taxonomy...")
    print()
    
    # ... mapping logic ...
    
    # For each section:
    for i, section in enumerate(cv_data.get("groups", [])):
        indent = "  "  # Indentation for readability
        print(f"{indent}[{len(mappings)+1}] Mapping: {section_label}")
        print(f"{indent}    Entries: {len(entries)}, Subgroups: {len(subgroups)}")
        
        # ... call LLM ...
        
        if mapping:
            print(f"{indent}    → {mapping['mapped_canonical_name']}")
            print(f"{indent}       Confidence: {mapping['confidence']:.2f}")
            if mapping['confidence'] < 0.6:
                print(f"{indent}       ⚠️  Low confidence - {mapping['reasoning']}")
    
    # Results summary
    print()
    print("="*80)
    print("MAPPING RESULTS")
    print("="*80)
    print(f"Total sections: {total_sections}")
    print(f"  High confidence (≥0.8): {high_confidence} ({high_confidence/total_sections*100:.1f}%)")
    print(f"  Medium confidence (0.6-0.79): {medium_confidence}")
    print(f"  Low confidence (<0.6): {low_confidence}")
    print(f"Average confidence: {avg_confidence:.3f}")
    print()
    print(f"✓ Results saved to: {output_path}")
    print()
```

**Issue**: All mapping details, confidence scores, warnings are printed to console only.

---

## 6. Section Parsers Logging

**Location**: `/src/unified_pipeline/parsers/publications_parser.py` (excerpt)

```python
def parse_publications_section(entries: List[Dict], section_metadata: Dict = None,
                               target_author: str = None) -> Dict[str, Any]:
    """Parse publication entries from CV section."""
    
    print(f"Parsing {len(entries)} publication entries...")
    if target_author:
        print(f"  Target author: {target_author}")
    
    all_parsed = []
    
    for idx, entry in enumerate(entries):
        print(f"  [{idx}/{len(entries)}] Parsing entry...")
        
        try:
            # ... parse entry with LLM ...
            
            confidence = result.get('confidence', 0.0)
            conf_emoji = "✓" if confidence >= 0.8 else "⚠️"
            publication = result.get('publication', {})
            
            print(f"      {conf_emoji} {publication['title'][:60]}... (conf: {confidence:.2f})")
            
            all_parsed.append(result)
        
        except Exception as e:
            print(f"      ✗ Error: {e}")
    
    print("="*80)
    print("RESULTS")
    print("="*80)
    print(f"Total publications: {len(all_parsed)}")
    print(f"  High confidence (≥0.8): {output_data['high_confidence']}")
    print(f"  Medium confidence (0.6-0.79): {output_data['medium_confidence']}")
    print(f"  Low confidence (<0.6): {output_data['low_confidence']}")
    print()
    
    return {"publications": all_parsed}
```

**Issue**: Individual entry parsing progress, confidence scores, errors all go to console.

---

## 7. Database Log Model

**Location**: `/web_interface/backend/app/models.py`

```python
class Log(Base):
    __tablename__ = "logs"
    
    id = Column(Integer, primary_key=True)
    run_id = Column(String, ForeignKey("runs.id"))
    step_number = Column(Integer)
    level = Column(String)  # INFO, WARNING, ERROR
    message = Column(Text)
    timestamp = Column(DateTime, default=datetime.utcnow)
    
    __table_args__ = (
        Index('idx_run_step', 'run_id', 'step_number'),
    )
```

**Usage**:
- Every call to `orchestrator.log()` creates a Log entry
- Only orchestrator-generated messages are stored
- Frontend retrieves via `/api/run/{run_id}/step/{step_number}` endpoint

---

## 8. Log Retrieval API

**Location**: `/web_interface/backend/app/api/steps.py` (Lines 15-47)

```python
@router.get("/run/{run_id}/step/{step_number}", response_model=StepDetail)
async def get_step_detail(run_id: str, step_number: int, db: Session = Depends(get_db)):
    """Get detailed information about a specific step."""
    
    step = db.query(Step).filter(
        Step.run_id == run_id,
        Step.step_number == step_number
    ).first()
    
    if not step:
        raise HTTPException(
            status_code=404,
            detail=f"Step {step_number} not found for run {run_id}"
        )
    
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
    
    return StepDetail(
        step_id=step.id,
        step_number=step.step_number,
        name=step.step_name,
        status=step.status,
        duration=step.duration_seconds,
        cost_usd=step.cost or 0.0,
        input_file=step.input_file,
        output_files=output_files,
        logs=log_entries,
        output_preview=output_preview
    )
```

**Flow**:
1. Frontend calls `/api/run/{run_id}/step/{step_number}`
2. Backend queries Log table
3. Returns only logs that orchestrator recorded
4. Frontend displays in UI

---

## 9. The Unused OutputCapture Class

**Location**: `/web_interface/backend/app/pipeline/orchestrator.py` (Lines 33-56)

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

**Status**: This class is defined but **NEVER USED**. It was designed to capture print output but is not integrated into the actual stage execution.

---

## 10. Progress Callback Pattern (Partially Implemented)

**Location**: `/src/unified_pipeline/core/cv_pipeline.py` (Lines 99-175)

```python
class CVPipeline:
    def __init__(self, cv_path: str, output_dir: Optional[str] = None,
                 progress_callback: Optional[Callable[[int, str, str], 
                                                     Awaitable[None]]] = None):
        """
        Initialize pipeline with input CV path.
        
        Args:
            progress_callback: Optional async callback for progress updates
                              Signature: async def callback(stage: int, message: str, level: str = "INFO")
        """
        self.progress_callback = progress_callback
    
    async def _log(self, stage: int, message: str, level: str = "INFO"):
        """
        Log a message. Calls progress_callback if provided, otherwise prints.
        
        Args:
            stage: Pipeline stage number (1-4)
            message: Log message
            level: Log level (INFO, WARNING, ERROR)
        """
        if self.progress_callback:
            await self.progress_callback(stage, message, level)
        else:
            print(message)
```

**The Pattern**:
- Accepts optional `progress_callback` parameter
- `_log()` method checks if callback exists
- If yes, calls it; if no, prints

**The Problem**:
- This method is NOT used in the actual stage execution methods
- Stage 1, 2, 3 execution methods use `print()` directly, not `self._log()`
- Stage 4 also uses `print()` directly

---

## Summary: Where Logs Go

```
┌─────────────────────────────────────────────────────────┐
│           CV Pipeline Stages (Execution)                │
│  - cv_segmenter.segment() - print() statements         │
│  - taxonomy_mapper.map_cv_sections() - print()         │
│  - publications_parser.parse() - print()               │
│  - education_parser.parse() - print()                  │
│  - positions_parser.parse() - print()                  │
│  - grants_parser.parse() - print()                     │
└─────────────────────────────────────────────────────────┘
                           ↓
              ┌──────────────────────┐
              │   Stdout / Stderr    │
              │  (Console Logs)      │
              └──────────────────────┘
              
┌─────────────────────────────────────────────────────────┐
│      Orchestrator.log() calls (Explicit)                │
│  - await self.log(step, "Starting...")                  │
│  - await self.log(step, "✓ Complete!")                  │
│  - await self.log(step, "ERROR: ...")                   │
└─────────────────────────────────────────────────────────┘
                      ↓           ↓
        ┌─────────────┴───────────┴──────────────┐
        ↓                                        ↓
    ┌────────────┐                          ┌──────────────┐
    │  Database  │                          │  event_emitter │
    │  (Log)     │                          │  (WebSocket)   │
    └────────────┘                          └──────────────┘
        ↓                                        ↓
    ┌────────────┐                          ┌──────────────┐
    │ Persistent │                          │  Frontend    │
    │   Storage  │                          │   Receives   │
    └────────────┘                          └──────────────┘
```

