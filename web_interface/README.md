# CViche Pipeline Viewer

A modern web interface for CViche with real-time progress tracking, cost monitoring, and interactive data viewing.

## Features

- 📤 **Upload CV files** (.docx or .pdf)
- ⚡ **Real-time progress tracking** via WebSocket
- 📊 **Step-by-step pipeline visualization** (9 steps)
- 💰 **Cost and token tracking** for LLM usage
- 📝 **Live log streaming** for each step
- 📁 **Output file downloads** for each stage
- 🎨 **Modern, responsive UI** with Tailwind CSS

## Architecture

### Backend (FastAPI + SQLite)
- **FastAPI** server on port 8000
- **SQLite** database for run/step tracking
- **WebSocket** for real-time events
- **Pipeline orchestrator** for step execution
- **RESTful API** for data access

### Frontend (React + TypeScript + Vite)
- **React 18** with TypeScript
- **Tailwind CSS** for styling
- **Vite** for fast development
- **Zustand** for state management (planned)
- **TanStack Table** for data viewing (planned)

## Quick Start

### Prerequisites

- Python 3.14 (matches the backend image, `python:3.14-slim`)
- Node.js 18+ and npm
- OpenAI API key

### Setup

1. **Clone and navigate:**
   ```bash
   cd web_interface
   ```

2. **Backend setup:**
   ```bash
   cd backend
   pip install -r requirements.txt
   cp .env.example .env
   # Edit .env and add your OPENAI_API_KEY
   ```

3. **Frontend setup:**
   ```bash
   cd ../frontend
   npm install
   ```

4. **Start both servers:**

   **Option A: Separate terminals**
   ```bash
   # Terminal 1: Backend
   cd backend
   uvicorn app.main:app --reload --port 8000

   # Terminal 2: Frontend
   cd frontend
   npm run dev
   ```

   **Option B: Use the startup script**
   ```bash
   ./start.sh
   ```

5. **Access the application:**
   - Frontend: http://localhost:3000
   - Backend API: http://localhost:8000
   - API Docs: http://localhost:8000/docs

## Usage

### Upload a CV

1. Open http://localhost:3000
2. Click "Select a file" and choose a .docx or .pdf CV
3. Click "Start Pipeline"
4. Watch the real-time progress!

### View Pipeline Progress

The interface shows:
- **Sidebar**: All 9 steps with status icons
  - ⏳ Pending
  - ⚙️ Running
  - ✅ Complete
  - ❌ Error
- **Top Bar**: Run ID, total cost, tokens, and status
- **Main Panel**: Selected step details, logs, and outputs

### Pipeline Steps

1. **Identify Sections** - Detects major CV sections
2. **Preserve Formatting** - Retains paragraph structure
3. **Break Into Items** - Splits into individual records
4. **Categorize Entries** - Classifies into taxonomy
5. **Fix Unknowns** - Re-examines unclassified entries
6. **Extract Structured Data** - Pulls out specific fields
7. **AI Assist (LLM Fallback)** - Handles tricky entries
8. **Add Organization Data** - Matches institutions to ROR (optional)
9. **Generate Final Template** - Produces final .docx

## Project Structure

```
web_interface/
├── backend/
│   ├── app/
│   │   ├── api/                  # API endpoints
│   │   │   ├── upload.py         # File upload
│   │   │   ├── runs.py           # Run management
│   │   │   ├── steps.py          # Step details
│   │   │   └── websocket.py      # Real-time streaming
│   │   ├── pipeline/             # Pipeline orchestration
│   │   │   ├── orchestrator.py   # Main executor
│   │   │   ├── step_registry.py  # Step definitions
│   │   │   └── event_emitter.py  # WebSocket events
│   │   ├── database.py           # SQLAlchemy setup
│   │   ├── models.py             # DB models
│   │   ├── schemas.py            # Pydantic schemas
│   │   └── main.py               # FastAPI app
│   ├── requirements.txt
│   └── README.md
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── UploadPage.tsx    # Upload interface
│   │   │   └── PipelineViewer.tsx # Pipeline viewer
│   │   ├── App.tsx
│   │   ├── main.tsx
│   │   └── index.css
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   └── index.html
├── uploads/                       # Uploaded CV files
├── outputs/                       # Pipeline outputs by run_id
├── start.sh                       # Startup script
└── README.md                      # This file
```

## API Endpoints

### Upload
- `POST /api/upload` - Upload CV file

### Runs
- `GET /api/run/{run_id}/status` - Get run status
- `POST /api/run/{run_id}/start` - Start pipeline
- `POST /api/run/{run_id}/pause` - Pause execution
- `POST /api/run/{run_id}/retry/{step_number}` - Retry failed step

### Steps
- `GET /api/run/{run_id}/step/{step_number}` - Get step details
- `GET /api/run/{run_id}/data/{filename}` - Download output file

### WebSocket
- `WS /ws/run/{run_id}/stream` - Real-time pipeline events

## Development

### Backend Development

```bash
cd backend
# Run with auto-reload
uvicorn app.main:app --reload --port 8000
```

### Frontend Development

```bash
cd frontend
# Run dev server
npm run dev

# Build for production
npm run build
```

### Database Schema

The SQLite database (`backend/cviche.db`) contains:
- **runs** - Pipeline run tracking
- **steps** - Individual step execution
- **logs** - Execution logs
- **llm_usage** - LLM API usage and costs

## Phase 1 Status ✅

**Completed:**
- ✅ Project structure
- ✅ FastAPI backend with database
- ✅ File upload and pipeline orchestration
- ✅ REST API endpoints
- ✅ WebSocket real-time updates
- ✅ React frontend with TypeScript
- ✅ Upload page
- ✅ Pipeline viewer with sidebar
- ✅ Live log streaming
- ✅ Cost tracking display

**Next Steps (Phase 2):**
- 🔄 Integrate with CViche pipeline code
- 🔄 Implement data table viewers
- 🔄 Add JSON viewer toggle
- 🔄 Implement retry functionality
- 🔄 Add settings panel
- 🔄 Implement batch processing

## Troubleshooting

### Backend won't start
- Check if port 8000 is available
- Verify Python dependencies are installed
- Ensure OPENAI_API_KEY is set in .env

### Frontend won't start
- Check if port 3000 is available
- Run `npm install` to install dependencies
- Clear node_modules and reinstall if needed

### WebSocket connection fails
- Ensure backend is running on port 8000
- Check browser console for errors
- Verify proxy settings in vite.config.ts

## Technical Documentation

### Logging System
Detailed documentation on how backend logs are captured and streamed to the frontend:
- **[LOGGING_IMPLEMENTATION.md](./LOGGING_IMPLEMENTATION.md)** - Complete guide to the stdout capture system
  - OutputCapture class explanation
  - WebSocket message flow
  - Data flow diagrams
  - Troubleshooting guide
  - Performance considerations

### Backend Architecture
- **[backend/README.md](./backend/README.md)** - Backend-specific documentation

## Contributing

This is a Phase 1 implementation. Future phases will add:
- Data table viewers with TanStack Table
- JSON viewer with react-json-view-lite
- Settings panel for model configuration
- Data Inspector (continuous scroll view)
- PDF support
- Batch processing

## License

Internal use only.
