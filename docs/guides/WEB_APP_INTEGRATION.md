# Web App Integration Guide

**Last Updated**: November 2, 2025

## Overview

The CV parsing web app is a browser-based interface that provides the same functionality as the CLI but with real-time progress updates and a user-friendly upload experience.

**Key Principle**: The web app calls `cv_pipeline.py` directly - no duplicate logic!

---

## Architecture

```
┌─────────────────────────────────────────────┐
│ React Frontend (port 3000)                  │
│ • File upload                               │
│ • Progress visualization                     │
│ • WebSocket connection                      │
└──────────────────┬──────────────────────────┘
                   │ HTTP + WebSocket
                   ▼
┌─────────────────────────────────────────────┐
│ FastAPI Backend (port 8000)                 │
│ • File handling                             │
│ • Database (SQLite)                         │
│ • WebSocket server                          │
│ • orchestrator.py                           │
└──────────────────┬──────────────────────────┘
                   │ Direct import
                   ▼
┌─────────────────────────────────────────────┐
│ cv_pipeline.py (SAME AS CLI)                │
│ • Stage 1: Segmentation                     │
│ • Stage 2: Taxonomy Mapping                 │
│ • Stage 3: Section Parsing                  │
│ • Stage 4: WCM Template Generation          │
└─────────────────────────────────────────────┘
```

---

## How It Works

### 1. User Uploads CV

```javascript
// Frontend (React)
const handleUpload = async (file) => {
  const formData = new FormData();
  formData.append('file', file);

  const response = await fetch('http://localhost:8000/upload', {
    method: 'POST',
    body: formData
  });

  const { run_id } = await response.json();
  connectWebSocket(run_id);
};
```

### 2. Backend Creates Run

```python
# Backend (FastAPI)
@app.post("/upload")
async def upload_cv(file: UploadFile, db: Session = Depends(get_db)):
    # Save file
    file_path = save_uploaded_file(file)

    # Create run in database
    run = Run(id=run_id, status="pending", file_path=file_path)
    db.add(run)
    db.commit()

    # Start pipeline in background
    asyncio.create_task(run_pipeline(run_id, file_path, db))

    return {"run_id": run_id}
```

### 3. Orchestrator Calls Integrated Pipeline

```python
# orchestrator.py
class PipelineOrchestrator:
    def __init__(self, run_id: str, file_path: Path, db: Session):
        # Create progress callback for WebSocket updates
        async def progress_callback(stage: int, message: str, level: str = "INFO"):
            await self.log(stage, message, level)

        # Initialize integrated pipeline
        self.pipeline = CVPipeline(
            cv_path=str(file_path),
            output_dir=str(self.output_dir),
            progress_callback=progress_callback  # ← Real-time updates!
        )

    async def execute(self):
        # Execute all 4 stages
        await self.execute_step(1)  # Segmentation
        await self.execute_step(2)  # Taxonomy Mapping
        await self.execute_step(3)  # Section Parsing
        await self.execute_step(4)  # Template Generation
```

### 4. Progress Updates Sent via WebSocket

```python
# orchestrator.py
async def log(self, step_number: int, message: str, level: str = "INFO"):
    # Save to database
    log_entry = Log(run_id=self.run_id, step_number=step_number, level=level, message=message)
    self.db.add(log_entry)
    self.db.commit()

    # Emit via WebSocket (real-time to frontend)
    await event_emitter.emit_log(self.run_id, step_number, message, level)
```

### 5. Frontend Displays Progress

```javascript
// Frontend (React)
websocket.onmessage = (event) => {
  const data = JSON.parse(event.data);

  if (data.type === 'log') {
    addLogMessage(data.step_number, data.message);
  } else if (data.type === 'step_complete') {
    markStepComplete(data.step_number);
  }
};
```

---

## Integration Points

### Progress Callback

The key integration mechanism is the progress callback:

```python
# In cv_pipeline.py
class CVPipeline:
    def __init__(self, cv_path, output_dir=None, progress_callback=None):
        self.progress_callback = progress_callback

    async def _log(self, stage: int, message: str, level: str = "INFO"):
        if self.progress_callback:
            await self.progress_callback(stage, message, level)
        else:
            print(message)  # Fallback for CLI
```

**When cv_pipeline runs**:
- CLI: Messages printed to console
- Web: Messages sent via WebSocket to frontend

### Async Execution

The web app uses async methods to avoid blocking:

```python
# orchestrator.py
async def _execute_step_logic(self, step_number: int) -> Dict[str, Any]:
    if step_number == 1:
        # Call integrated pipeline Stage 1
        result = await self.pipeline.run_stage_1_async()
        # ...

    elif step_number == 2:
        # Call integrated pipeline Stage 2
        result = await self.pipeline.run_stage_2_async(stage1_result)
        # ...
```

Each stage runs in an executor to prevent blocking the event loop.

---

## Development Workflow

### Testing Changes

1. **Test CLI First**:
   ```bash
   python run_pipeline.py full "data/sample_cvs/word/test.docx"
   ```

2. **Then Test Web App**:
   ```bash
   # Terminal 1: Backend
   cd web_interface/backend
   uvicorn app.main:app --reload --port 8000

   # Terminal 2: Frontend
   cd web_interface/frontend
   npm start

   # Browser: Upload the same CV
   ```

3. **Verify Same Results**:
   - Check that output files are identical
   - Verify same features (author abbreviation, bolding, etc.)
   - Confirm progress messages make sense

### Adding Features

**To add a new feature to the pipeline:**

1. **Implement in `cv_pipeline.py`**:
   ```python
   # Example: Add new validation step
   def run_stage_5_validation(self):
       # New logic here
       return {"validation_results": results}

   async def run_stage_5_async(self):
       loop = asyncio.get_event_loop()
       return await loop.run_in_executor(None, self.run_stage_5_validation)
   ```

2. **Update orchestrator.py**:
   ```python
   elif step_number == 5:
       result = await self.pipeline.run_stage_5_async()
       # Handle result
   ```

3. **No changes needed elsewhere!**
   - Frontend automatically shows new step
   - Progress messages automatically displayed
   - CLI gets the feature too

---

## Configuration

### Backend Configuration

**Environment Variables** (`web_interface/backend/.env`):
```bash
# Database
DATABASE_URL=sqlite:///./cv_pipeline.db

# CORS (for local development)
CORS_ORIGINS=http://localhost:3000

# File upload
MAX_UPLOAD_SIZE=50MB
UPLOAD_DIR=./uploads
```

### Feature Flags

All features controlled by `src/unified_pipeline/config.py`:

```python
# These settings apply to both CLI and web app
USE_LEGACY_HANDLERS = True  # Author abbreviation, bolding, etc.
ENABLE_PMCID_ENRICHMENT = True  # PubMed API lookup
ENABLE_AUTHOR_BOLDING = True
USE_TAXONOMY_MAPPING = True  # NOT keyword matching
```

To disable a feature for both CLI and web:
```python
ENABLE_PMCID_ENRICHMENT = False  # ← Change in one place
```

---

## Database Schema

**Run Table**:
```sql
CREATE TABLE runs (
    id TEXT PRIMARY KEY,
    file_path TEXT NOT NULL,
    status TEXT NOT NULL,  -- pending, running, complete, failed
    created_at DATETIME,
    completed_at DATETIME,
    total_cost REAL,
    total_tokens INTEGER,
    error_message TEXT
);
```

**Step Table**:
```sql
CREATE TABLE steps (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_number INTEGER NOT NULL,
    step_name TEXT NOT NULL,
    status TEXT NOT NULL,  -- pending, running, complete, error
    started_at DATETIME,
    completed_at DATETIME,
    duration_seconds INTEGER,
    cost REAL,
    output_files TEXT,  -- JSON array
    error_message TEXT,
    FOREIGN KEY (run_id) REFERENCES runs (id)
);
```

**Log Table**:
```sql
CREATE TABLE logs (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL,
    step_number INTEGER,
    level TEXT NOT NULL,  -- INFO, WARNING, ERROR
    message TEXT NOT NULL,
    timestamp DATETIME,
    FOREIGN KEY (run_id) REFERENCES runs (id)
);
```

---

## WebSocket Events

### Client → Server

**Connect**:
```json
{
  "type": "subscribe",
  "run_id": "run_abc123"
}
```

### Server → Client

**Log Message**:
```json
{
  "type": "log",
  "run_id": "run_abc123",
  "step_number": 1,
  "level": "INFO",
  "message": "Segmentation complete!",
  "timestamp": "2025-11-02T19:56:15.314996"
}
```

**Step Start**:
```json
{
  "type": "step_start",
  "run_id": "run_abc123",
  "step_number": 2,
  "step_name": "Taxonomy Mapping"
}
```

**Step Complete**:
```json
{
  "type": "step_complete",
  "run_id": "run_abc123",
  "step_number": 2,
  "duration": 45,
  "cost": 0.0023,
  "output_files": ["step_2_output.json"]
}
```

**Run Complete**:
```json
{
  "type": "run_complete",
  "run_id": "run_abc123",
  "total_cost": 0.15,
  "total_tokens": 125000,
  "duration": 180
}
```

---

## Error Handling

### Pipeline Errors

If cv_pipeline raises an exception:

1. **Orchestrator catches it**:
   ```python
   try:
       result = await self.pipeline.run_stage_1_async()
   except Exception as e:
       await self.log(1, f"ERROR: {str(e)}", "ERROR")
       raise  # Re-raise to mark step as failed
   ```

2. **Step marked as error**:
   ```python
   step.status = "error"
   step.error_message = str(e)
   ```

3. **WebSocket notification**:
   ```python
   await event_emitter.emit_step_error(run_id, step_number, str(e))
   ```

4. **Frontend shows error**:
   ```javascript
   if (data.type === 'step_error') {
     showErrorMessage(data.step_number, data.error_message);
   }
   ```

### Validation

Before starting pipeline:

```python
# In orchestrator.py __init__
from src.unified_pipeline.config import validate_setup

validation = validate_setup()
if not validation['valid']:
    raise ValueError(f"Setup invalid: {validation['issues']}")
```

---

## Testing

### Unit Tests

Test orchestrator without running full pipeline:

```python
# tests/test_orchestrator.py
def test_progress_callback():
    messages = []

    async def callback(stage, message, level):
        messages.append((stage, message, level))

    pipeline = CVPipeline("dummy.docx", progress_callback=callback)
    # ... test that callback is called
```

### Integration Tests

Test complete flow:

```python
# tests/integration/test_web_app.py
async def test_full_pipeline_via_web():
    # Create run
    response = await client.post("/upload", files={"file": cv_file})
    run_id = response.json()["run_id"]

    # Wait for completion
    await asyncio.sleep(120)

    # Check results
    run = db.query(Run).filter(Run.id == run_id).first()
    assert run.status == "complete"
    assert run.total_cost > 0
```

---

## Debugging

### Backend Logs

```bash
# View logs
tail -f logs/backend.log

# Or run with debug
uvicorn app.main:app --reload --log-level debug
```

### Database Inspection

```bash
# Open database
sqlite3 cv_pipeline.db

# Check runs
SELECT * FROM runs ORDER BY created_at DESC LIMIT 10;

# Check logs for a run
SELECT step_number, level, message
FROM logs
WHERE run_id = 'run_abc123'
ORDER BY timestamp;
```

### WebSocket Debugging

Browser console:
```javascript
// Monitor WebSocket messages
websocket.onmessage = (event) => {
  console.log('WebSocket:', JSON.parse(event.data));
};
```

---

## Deployment

### Production Setup

1. **Use production server** (not `--reload`):
   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 4
   ```

2. **Build frontend**:
   ```bash
   cd web_interface/frontend
   npm run build
   # Serve build/ directory with nginx/apache
   ```

3. **Use PostgreSQL** instead of SQLite:
   ```bash
   DATABASE_URL=postgresql://user:pass@localhost/cv_pipeline
   ```

4. **Set CORS properly**:
   ```python
   CORS_ORIGINS=https://yourdomain.com
   ```

5. **Add authentication**:
   ```python
   # In app/main.py
   from fastapi.security import OAuth2PasswordBearer

   oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

   @app.post("/upload")
   async def upload_cv(
       file: UploadFile,
       token: str = Depends(oauth2_scheme)
   ):
       # Verify token
       # ... rest of code
   ```

---

## Troubleshooting

### "Pipeline not using legacy handlers"

**Check config**:
```bash
python -c "from src.unified_pipeline.config import validate_setup; print(validate_setup())"
```

Should show:
```python
{
  'valid': True,
  'features_enabled': {
    'legacy_handlers': True,  # ← Should be True
    'author_abbreviation': True,
    'author_bolding': True,
    ...
  }
}
```

### "Web app shows different results than CLI"

**This shouldn't happen!** Both use the same code.

Debug:
1. Check orchestrator.py imports CVPipeline
2. Verify no old code paths are being used
3. Run validation: `python scripts/validate_integration.py`

### "WebSocket not connecting"

**Check CORS**:
```python
# app/main.py
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],  # ← Check this
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

---

## Best Practices

1. **Always validate setup** before deploying:
   ```bash
   python scripts/validate_integration.py
   ```

2. **Test CLI first**, then web app:
   - Faster feedback loop
   - Easier debugging
   - Ensures core logic works

3. **Use progress callbacks** for all long operations:
   ```python
   await self._log(stage, "Starting expensive operation...")
   # ... do work ...
   await self._log(stage, "✓ Operation complete")
   ```

4. **Handle errors gracefully**:
   ```python
   try:
       result = await self.pipeline.run_stage_X_async()
   except Exception as e:
       await self.log(X, f"ERROR: {str(e)}", "ERROR")
       raise  # Let orchestrator handle failure
   ```

5. **Keep orchestrator thin** - all logic in cv_pipeline.py:
   ```python
   # ✅ Good
   result = await self.pipeline.run_stage_1_async()

   # ❌ Bad - don't reimplement pipeline logic
   segmenter = CVSegmenter()
   result = segmenter.segment(...)
   ```

---

## Summary

The web app integration is **simple and maintainable** because:

✅ **Single source of truth**: cv_pipeline.py powers everything
✅ **Progress callbacks**: Real-time updates without code duplication
✅ **Async execution**: Non-blocking for better UX
✅ **Centralized config**: One place to change settings
✅ **Validated setup**: Automated checks prevent issues

**When you add a feature to cv_pipeline.py, both CLI and web app get it automatically!**

For complete technical details, see `INTEGRATION_CONSOLIDATION_COMPLETE.md`.
