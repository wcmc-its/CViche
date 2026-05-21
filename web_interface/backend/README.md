# CViche Pipeline Viewer - Backend

FastAPI backend for the CViche web interface.

## Setup

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure environment variables:**
   ```bash
   cp .env.example .env
   # Edit .env and add your OpenAI API key
   ```

3. **Start the server:**
   ```bash
   uvicorn app.main:app --reload --port 8000
   ```

4. **Access the API:**
   - API: http://localhost:8000
   - Interactive docs: http://localhost:8000/docs
   - Liveness probe: http://localhost:8000/livez
   - Readiness probe: http://localhost:8000/readyz

## API Endpoints

### Upload
- `POST /api/upload` - Upload a CV file (.docx or .pdf)

### Runs
- `GET /api/run/{run_id}/status` - Get run status and all steps
- `POST /api/run/{run_id}/start` - Start pipeline execution
- `POST /api/run/{run_id}/pause` - Pause execution
- `POST /api/run/{run_id}/retry/{step_number}` - Retry a failed step

### Steps
- `GET /api/run/{run_id}/step/{step_number}` - Get step details
- `GET /api/run/{run_id}/data/{filename}` - Download output file

### WebSocket
- `WS /ws/run/{run_id}/stream` - Real-time pipeline events

## Project Structure

```
backend/
├── app/
│   ├── api/                 # API endpoints
│   │   ├── upload.py        # File upload
│   │   ├── runs.py          # Run management
│   │   ├── steps.py         # Step details
│   │   └── websocket.py     # WebSocket streaming
│   ├── pipeline/            # Pipeline orchestration
│   │   ├── orchestrator.py  # Main pipeline executor
│   │   ├── step_registry.py # Step definitions (1-9)
│   │   └── event_emitter.py # WebSocket events
│   ├── database.py          # SQLAlchemy setup
│   ├── models.py            # Database models
│   ├── schemas.py           # Pydantic schemas
│   └── main.py              # FastAPI app
├── cviche.db                # SQLite database (created on first run)
├── requirements.txt         # Python dependencies
└── .env                     # Environment variables
```

## Database Schema

The application uses SQLite with the following tables:

- **runs** - Pipeline run tracking
- **steps** - Individual step execution
- **logs** - Execution logs
- **llm_usage** - LLM API usage and costs

## Development

To run in development mode with auto-reload:

```bash
uvicorn app.main:app --reload --port 8000
```

To run tests:

```bash
pytest
```
