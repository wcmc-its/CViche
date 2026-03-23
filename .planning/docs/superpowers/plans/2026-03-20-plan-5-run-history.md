# Plan 5: Run History Enhancements

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paginate the runs API with user scoping, add "Show more" loading in the frontend, handle incomplete runs with a read-only viewer, add a "Restart with this file" feature with file existence validation, and secure WebSocket connections with cookie-based auth and ownership checks.

**Architecture:** The existing `GET /api/runs` endpoint becomes paginated (`?offset=0&limit=20`) and user-scoped (breaking change). A new `POST /api/run/{run_id}/restart` endpoint clones a run with the same file. WebSocket upgrade validates the session cookie and checks run ownership before accepting the connection. The frontend `RunHistory.tsx` gains incremental loading, incomplete run indicators, and a restart button.

**Tech Stack:** FastAPI, SQLAlchemy, React, TypeScript, WebSocket

**Spec:** `docs/superpowers/specs/2026-03-20-production-readiness-design.md` — section 6 (Run History & Pagination).

**Depends on:** Plan 1 (Auth Foundation) — requires `User` model, `get_current_user` dependency, session cookie infrastructure, and `user_id` column on the `Run` table.

---

### Task 1: Paginated, User-Scoped `GET /api/runs`

**Files:**
- Modify: `web_interface/backend/app/api/runs.py`
- Modify: `web_interface/backend/app/schemas.py`

This is a **breaking change** from the current unpaginated, unscoped endpoint. The frontend must be updated simultaneously (Task 3).

- [ ] **Step 1: Add paginated response schema**

Add to `web_interface/backend/app/schemas.py`, after the existing `RunSummary` class:

```python
class PaginatedRuns(BaseModel):
    """Paginated list of runs."""
    runs: List[RunSummary]
    total: int
    offset: int
    limit: int
    has_more: bool

    class Config:
        from_attributes = True
```

- [ ] **Step 2: Replace `list_runs` with paginated, user-scoped version**

Replace the `list_runs` function in `web_interface/backend/app/api/runs.py`. The import block at the top of the file must also be updated.

Replace the entire import block and `list_runs` function:

```python
"""Run status and management API endpoints."""
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Run, Step, User
from app.schemas import RunStatus, RunSummary, StepSummary, PaginatedRuns
from app.auth import get_current_user
from app.pipeline.orchestrator import PipelineOrchestrator

router = APIRouter()


@router.get("/runs", response_model=PaginatedRuns)
async def list_runs(
    offset: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List pipeline runs for the current user, paginated, most recent first.

    Breaking change: previously returned all runs unpaginated with no user filter.
    Now returns only the current user's runs with offset/limit pagination.
    """
    if limit < 1 or limit > 100:
        limit = 20
    if offset < 0:
        offset = 0

    # Total count for this user
    total = (
        db.query(Run)
        .filter(Run.user_id == current_user.id)
        .count()
    )

    # Paginated query
    runs = (
        db.query(Run)
        .filter(Run.user_id == current_user.id)
        .order_by(Run.started_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    results = []
    for run in runs:
        total_duration_seconds = None
        if run.started_at:
            if run.completed_at:
                total_duration_seconds = int((run.completed_at - run.started_at).total_seconds())
            elif run.status == "running":
                total_duration_seconds = max(0, int((datetime.now() - run.started_at).total_seconds()))

        results.append(RunSummary(
            run_id=run.id,
            filename=run.filename,
            status=run.status,
            started_at=run.started_at,
            completed_at=run.completed_at,
            total_cost=run.total_cost or 0.0,
            total_duration_seconds=total_duration_seconds,
        ))

    return PaginatedRuns(
        runs=results,
        total=total,
        offset=offset,
        limit=limit,
        has_more=(offset + limit) < total,
    )
```

- [ ] **Step 3: Add auth + ownership to existing run endpoints**

Still in `web_interface/backend/app/api/runs.py`, update `get_run_status` to require auth and check ownership:

```python
@router.get("/run/{run_id}/status", response_model=RunStatus)
async def get_run_status(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Get the current status of a pipeline run."""
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Run {run_id} not found"},
        )
    # Ownership check: user must own the run or be admin
    if run.user_id and run.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "You do not have access to this run."},
        )

    # ... rest of function unchanged (steps query, duration calc, return RunStatus)
```

Apply the same ownership-check pattern to `start_run`, `pause_run`, `cancel_run`, `retry_step`, and `get_data_quality`. Each function signature gains `current_user: User = Depends(get_current_user)`, and the body gains the ownership check block shown above immediately after the `run = db.query(Run)...` lookup.

- [ ] **Step 4: Verify paginated endpoint**

```bash
# Login first (requires Plan 1 auth to be implemented)
curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "paa2013@med.cornell.edu", "display_name": "Paul Albert"}' \
  -c cookies.txt

# Fetch first page
curl -s "http://localhost:8000/api/runs?offset=0&limit=5" -b cookies.txt | python3 -m json.tool
# Expected: { "runs": [...], "total": N, "offset": 0, "limit": 5, "has_more": true/false }

# Fetch second page
curl -s "http://localhost:8000/api/runs?offset=5&limit=5" -b cookies.txt | python3 -m json.tool

# Unauthenticated should fail
curl -s "http://localhost:8000/api/runs" | python3 -m json.tool
# Expected: 401
```

- [ ] **Step 5: Commit**

```bash
git add web_interface/backend/app/api/runs.py web_interface/backend/app/schemas.py
git commit -m "feat: paginate GET /api/runs with user scoping and auth (breaking change)"
```

---

### Task 2: Restart Endpoint — `POST /api/run/{run_id}/restart`

**Files:**
- Modify: `web_interface/backend/app/api/runs.py`

Creates a new pipeline run using the same uploaded file as an existing run. Inherits `submission_type` from the original. Returns an error if the original file has been deleted from disk.

- [ ] **Step 1: Add restart endpoint**

Add to `web_interface/backend/app/api/runs.py`, after the existing `retry_step` endpoint:

```python
@router.post("/run/{run_id}/restart")
async def restart_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Create a new run using the same file as an existing run.

    - Inherits submission_type from the original run.
    - Checks that the original uploaded file still exists on disk.
    - The new run is created with status 'created' (not auto-started).
    - Counts as a new run for rate limiting purposes.
    """
    # Look up original run
    original_run = db.query(Run).filter(Run.id == run_id).first()
    if not original_run:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Run {run_id} not found"},
        )

    # Ownership check
    if original_run.user_id and original_run.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "You do not have access to this run."},
        )

    # Check that the original file still exists
    upload_dir = Path(__file__).parent.parent.parent.parent / "uploads"
    original_file_path = upload_dir / f"{run_id}_{original_run.filename}"

    if not original_file_path.exists():
        raise HTTPException(
            status_code=404,
            detail={
                "error": "file_not_found",
                "message": "Original file no longer available. Please upload the file again.",
            },
        )

    # Generate a new run ID
    from app.api.upload import generate_run_id
    new_run_id = generate_run_id()

    # Copy the file with the new run ID prefix
    import shutil
    new_file_path = upload_dir / f"{new_run_id}_{original_run.filename}"
    shutil.copy2(str(original_file_path), str(new_file_path))

    # Create new run record, inheriting submission_type
    new_run = Run(
        id=new_run_id,
        filename=original_run.filename,
        file_type=original_run.file_type,
        status="created",
        started_at=datetime.now(),
        user_id=current_user.id,
        submission_type=original_run.submission_type,
    )
    db.add(new_run)

    # Create step records (all pending)
    from app.pipeline.step_registry import STEP_REGISTRY

    for step_def in STEP_REGISTRY:
        step = Step(
            run_id=new_run_id,
            step_number=step_def.number,
            stage_id=step_def.stage_id,
            step_name=step_def.name,
            status="pending",
        )
        db.add(step)

    db.commit()

    return {
        "run_id": new_run_id,
        "filename": original_run.filename,
        "status": "created",
        "message": f"New run {new_run_id} created from {run_id}. Ready to start.",
        "inherited_submission_type": original_run.submission_type,
    }
```

- [ ] **Step 2: Verify restart endpoint**

```bash
# Restart a known run (replace RUNID with an actual run ID)
curl -s -X POST http://localhost:8000/api/run/RUNID/restart -b cookies.txt | python3 -m json.tool
# Expected: { "run_id": "NEWID", "filename": "...", "status": "created", ... }

# Restart a run whose file was deleted
curl -s -X POST http://localhost:8000/api/run/DELETED_RUNID/restart -b cookies.txt | python3 -m json.tool
# Expected: 404 with error "file_not_found"

# Restart without auth
curl -s -X POST http://localhost:8000/api/run/RUNID/restart | python3 -m json.tool
# Expected: 401
```

- [ ] **Step 3: Commit**

```bash
git add web_interface/backend/app/api/runs.py
git commit -m "feat: add POST /api/run/{run_id}/restart endpoint with file existence check"
```

---

### Task 3: Frontend — Paginated RunHistory with "Show More"

**Files:**
- Modify: `web_interface/frontend/src/components/RunHistory.tsx`

Replace the current component entirely. The new version fetches 20 runs at a time, tracks pagination state, and provides a "Show more" button to load the next page.

- [ ] **Step 1: Replace RunHistory.tsx with paginated version**

Replace the entire contents of `web_interface/frontend/src/components/RunHistory.tsx`:

```tsx
import { useState, useEffect, useCallback } from 'react'
import { Clock, DollarSign, FileText, CheckCircle2, Loader2, XCircle, AlertCircle, ChevronDown } from 'lucide-react'

interface RunSummary {
  run_id: string
  filename: string
  status: string
  started_at: string
  completed_at: string | null
  total_cost: number
  total_duration_seconds: number | null
}

interface PaginatedResponse {
  runs: RunSummary[]
  total: number
  offset: number
  limit: number
  has_more: boolean
}

interface RunHistoryProps {
  onSelectRun: (runId: string) => void
  /** Incremented externally to trigger a refresh (e.g., after a new upload). */
  refreshKey?: number
}

const PAGE_SIZE = 20

function StatusIcon({ status }: { status: string }) {
  switch (status) {
    case 'complete':
      return <CheckCircle2 className="w-4 h-4 text-green-600" aria-hidden="true" />
    case 'running':
      return <Loader2 className="w-4 h-4 text-blue-600 animate-spin" aria-hidden="true" />
    case 'failed':
      return <XCircle className="w-4 h-4 text-red-600" aria-hidden="true" />
    case 'cancelled':
      return <AlertCircle className="w-4 h-4 text-orange-600" aria-hidden="true" />
    default:
      return <Clock className="w-4 h-4 text-gray-400" aria-hidden="true" />
  }
}

function StatusLabel({ status }: { status: string }) {
  const labels: Record<string, { text: string; className: string }> = {
    complete: { text: 'Complete', className: 'text-green-700 bg-green-50' },
    running: { text: 'Running', className: 'text-blue-700 bg-blue-50' },
    failed: { text: 'Failed', className: 'text-red-700 bg-red-50' },
    cancelled: { text: 'Cancelled', className: 'text-orange-700 bg-orange-50' },
    created: { text: 'Created', className: 'text-gray-600 bg-gray-100' },
  }
  const label = labels[status] || { text: status, className: 'text-gray-600 bg-gray-100' }
  return (
    <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${label.className}`}>
      {label.text}
    </span>
  )
}

export default function RunHistory({ onSelectRun, refreshKey }: RunHistoryProps) {
  const [runs, setRuns] = useState<RunSummary[]>([])
  const [total, setTotal] = useState(0)
  const [hasMore, setHasMore] = useState(false)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchRuns = useCallback(async (offset: number, append: boolean) => {
    try {
      const res = await fetch(`/api/runs?offset=${offset}&limit=${PAGE_SIZE}`)
      if (res.status === 401) {
        // Session expired — let the auth context handle redirect
        return
      }
      if (!res.ok) {
        throw new Error(`Failed to fetch runs (${res.status})`)
      }
      const data: PaginatedResponse = await res.json()

      if (append) {
        setRuns((prev) => [...prev, ...data.runs])
      } else {
        setRuns(data.runs)
      }
      setTotal(data.total)
      setHasMore(data.has_more)
      setError(null)
    } catch (err) {
      console.error('Error fetching runs:', err)
      setError(err instanceof Error ? err.message : 'Failed to load run history')
    }
  }, [])

  // Initial load + reload when refreshKey changes
  useEffect(() => {
    setLoading(true)
    fetchRuns(0, false).finally(() => setLoading(false))
  }, [fetchRuns, refreshKey])

  const handleShowMore = async () => {
    setLoadingMore(true)
    await fetchRuns(runs.length, true)
    setLoadingMore(false)
  }

  if (loading) {
    return (
      <div className="text-center py-4 text-sm text-gray-500">
        <Loader2 className="w-4 h-4 animate-spin inline mr-2" aria-hidden="true" />
        Loading history...
      </div>
    )
  }

  if (error) {
    return (
      <div className="text-center py-4 text-sm text-red-600">
        {error}
      </div>
    )
  }

  if (runs.length === 0) {
    return null
  }

  const formatDate = (dateStr: string) => {
    const d = new Date(dateStr)
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
      ' ' + d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
  }

  const formatDuration = (seconds: number | null) => {
    if (seconds === null) return '\u2014'
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    if (mins > 0) return `${mins}m ${secs}s`
    return `${secs}s`
  }

  return (
    <section aria-label="Previous runs">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-700">Previous Runs</h2>
        <span className="text-xs text-gray-400">{total} total</span>
      </div>

      <div className="space-y-2">
        {runs.map((run) => (
          <button
            key={run.run_id}
            onClick={() => onSelectRun(run.run_id)}
            className="w-full text-left bg-white border border-gray-200 rounded-lg px-4 py-3 hover:bg-gray-50 transition-colors cursor-pointer focus:ring-2 focus:ring-blue-500 focus:outline-none"
            style={{ touchAction: 'manipulation' }}
          >
            <div className="flex items-center justify-between gap-3">
              <div className="flex items-center gap-3 min-w-0">
                <StatusIcon status={run.status} />
                <div className="min-w-0">
                  <div className="text-sm font-medium text-gray-900 truncate flex items-center gap-1.5">
                    <FileText className="w-3.5 h-3.5 text-gray-400 flex-shrink-0" aria-hidden="true" />
                    {run.filename}
                  </div>
                  <div className="text-xs text-gray-500 flex items-center gap-2">
                    {formatDate(run.started_at)}
                    <StatusLabel status={run.status} />
                  </div>
                </div>
              </div>
              <div className="flex items-center gap-3 text-xs text-gray-500 shrink-0">
                <span className="flex items-center gap-1">
                  <Clock className="w-3 h-3" aria-hidden="true" />
                  {formatDuration(run.total_duration_seconds)}
                </span>
                <span className="flex items-center gap-1">
                  <DollarSign className="w-3 h-3" aria-hidden="true" />
                  {run.total_cost.toFixed(3)}
                </span>
              </div>
            </div>
          </button>
        ))}
      </div>

      {hasMore && (
        <div className="mt-3 text-center">
          <button
            onClick={handleShowMore}
            disabled={loadingMore}
            className="inline-flex items-center gap-1.5 text-sm text-primary-600 hover:text-primary-700 font-medium disabled:text-gray-400 disabled:cursor-not-allowed transition-colors"
          >
            {loadingMore ? (
              <>
                <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                Loading...
              </>
            ) : (
              <>
                <ChevronDown className="w-4 h-4" aria-hidden="true" />
                Show more ({total - runs.length} remaining)
              </>
            )}
          </button>
        </div>
      )}
    </section>
  )
}
```

- [ ] **Step 2: Build and verify**

```bash
cd web_interface/frontend && npm run build
```
Expected: clean build, no TypeScript errors.

- [ ] **Step 3: Manual test**

1. Open `http://localhost:3000` (logged in).
2. Run history section should load the first 20 runs.
3. If you have more than 20 runs, a "Show more (N remaining)" button appears at the bottom.
4. Clicking "Show more" appends the next 20 runs below the existing list.
5. The button disappears when all runs are loaded.
6. Each run card shows a status badge (Complete / Failed / Cancelled / Created / Running).

- [ ] **Step 4: Commit**

```bash
git add web_interface/frontend/src/components/RunHistory.tsx
git commit -m "feat: paginated RunHistory with 'Show more' button and status badges"
```

---

### Task 4: Incomplete Run Handling — Read-Only Pipeline Viewer

**Files:**
- Modify: `web_interface/frontend/src/components/PipelineViewer.tsx`

When a user clicks a run with status `failed`, `cancelled`, or `created`, the pipeline viewer opens in read-only mode: it shows whatever logs and output were produced before the run stopped, displays the terminal status prominently, and offers a "Restart with this file" button.

- [ ] **Step 1: Add incomplete run banner and restart button to PipelineViewer**

In `web_interface/frontend/src/components/PipelineViewer.tsx`, add these imports at the top (merge with existing imports):

```tsx
import { RotateCcw, AlertTriangle } from 'lucide-react'
```

Add state for the restart flow, near the other `useState` declarations inside `PipelineViewer`:

```tsx
  const [restarting, setRestarting] = useState(false)
  const [restartError, setRestartError] = useState<string | null>(null)
```

Add the restart handler function inside the component, before the `return` statement:

```tsx
  const handleRestart = async () => {
    setRestarting(true)
    setRestartError(null)
    try {
      const res = await fetch(`/api/run/${runId}/restart`, { method: 'POST' })
      if (!res.ok) {
        const err = await res.json()
        const message = err.detail?.message || err.detail || 'Restart failed'
        setRestartError(message)
        return
      }
      const data = await res.json()
      // Navigate to the new run
      onBack()
      // Small delay so the upload page re-renders with new run in history,
      // then navigate to the new run's pipeline viewer
      setTimeout(() => {
        window.location.href = `/run/${data.run_id}`
      }, 100)
    } catch (err) {
      setRestartError(err instanceof Error ? err.message : 'Restart failed')
    } finally {
      setRestarting(false)
    }
  }
```

Add the incomplete run banner. Place this JSX immediately inside the component's return, right after the `<PipelineHeader ... />` component and before the main content area:

```tsx
      {/* Incomplete run banner */}
      {runStatus && ['failed', 'cancelled', 'created'].includes(runStatus.status) && (
        <div className={`mx-4 mt-3 rounded-lg border p-4 ${
          runStatus.status === 'failed'
            ? 'bg-red-50 border-red-200'
            : runStatus.status === 'cancelled'
            ? 'bg-orange-50 border-orange-200'
            : 'bg-gray-50 border-gray-200'
        }`}>
          <div className="flex items-start gap-3">
            <AlertTriangle className={`w-5 h-5 mt-0.5 flex-shrink-0 ${
              runStatus.status === 'failed' ? 'text-red-500' :
              runStatus.status === 'cancelled' ? 'text-orange-500' : 'text-gray-500'
            }`} aria-hidden="true" />
            <div className="flex-1 min-w-0">
              <p className={`text-sm font-semibold ${
                runStatus.status === 'failed' ? 'text-red-800' :
                runStatus.status === 'cancelled' ? 'text-orange-800' : 'text-gray-800'
              }`}>
                {runStatus.status === 'failed' && 'This run failed'}
                {runStatus.status === 'cancelled' && 'This run was cancelled'}
                {runStatus.status === 'created' && 'This run was never started'}
              </p>
              {runStatus.error_message && (
                <p className="text-sm text-gray-600 mt-1">{runStatus.error_message}</p>
              )}
              <p className="text-xs text-gray-500 mt-1">
                Logs and any partial output produced before the run stopped are shown below.
              </p>

              {restartError && (
                <p className="text-sm text-red-600 mt-2">{restartError}</p>
              )}

              <button
                onClick={handleRestart}
                disabled={restarting}
                className="mt-3 inline-flex items-center gap-1.5 text-sm font-medium px-3 py-1.5 rounded-md bg-white border border-gray-300 text-gray-700 hover:bg-gray-50 disabled:opacity-50 disabled:cursor-not-allowed transition-colors shadow-sm"
              >
                {restarting ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" aria-hidden="true" />
                    Restarting...
                  </>
                ) : (
                  <>
                    <RotateCcw className="w-4 h-4" aria-hidden="true" />
                    Restart with this file
                  </>
                )}
              </button>
            </div>
          </div>
        </div>
      )}
```

- [ ] **Step 2: Disable action buttons for terminal runs**

In `PipelineViewer.tsx`, wherever the Start / Cancel / Pause buttons are rendered, wrap them in a condition that hides them when the run is in a terminal state:

```tsx
{runStatus && !['failed', 'cancelled', 'complete'].includes(runStatus.status) && (
  // ... existing start/cancel/pause buttons
)}
```

This ensures the pipeline viewer is truly read-only for incomplete runs — the user can browse logs and output files but cannot start/cancel/pause a terminal run.

- [ ] **Step 3: Build and verify**

```bash
cd web_interface/frontend && npm run build
```
Expected: clean build.

- [ ] **Step 4: Manual test**

1. Navigate to a run with status `failed` — should see a red banner with error message and "Restart with this file" button.
2. Navigate to a run with status `cancelled` — should see an orange banner.
3. Navigate to a run with status `created` — should see a gray banner.
4. Click "Restart with this file" — should create a new run and navigate to it.
5. For a run whose file was deleted, clicking restart should show "Original file no longer available. Please upload the file again."
6. Logs and output files from the partial run should still be visible.

- [ ] **Step 5: Commit**

```bash
git add web_interface/frontend/src/components/PipelineViewer.tsx
git commit -m "feat: read-only pipeline viewer for incomplete runs with restart button"
```

---

### Task 5: WebSocket Auth — Cookie Validation on Upgrade

**Files:**
- Modify: `web_interface/backend/app/api/websocket.py`

The WebSocket endpoint must validate the session cookie during the HTTP upgrade handshake and verify run ownership before accepting the connection. Unauthenticated or unauthorized connections are rejected with appropriate WebSocket close codes.

- [ ] **Step 1: Replace websocket.py with auth-aware version**

Replace the entire contents of `web_interface/backend/app/api/websocket.py`:

```python
"""WebSocket endpoint for real-time pipeline updates with auth."""
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.database import SessionLocal
from app.models import Run, User
from app.auth import decode_session_cookie, COOKIE_NAME
from app.pipeline.event_emitter import event_emitter

logger = logging.getLogger(__name__)
router = APIRouter()


def _authenticate_websocket(websocket: WebSocket) -> dict | None:
    """Extract and validate session cookie from WebSocket upgrade request.

    Returns the session payload dict if valid, None otherwise.
    WebSocket connections receive cookies from the browser automatically
    when connecting to the same origin. For cross-origin dev setups
    (frontend on :3000, backend on :8000), the Vite proxy must forward
    the WebSocket connection so cookies are included.
    """
    cookie_value = websocket.cookies.get(COOKIE_NAME)
    if not cookie_value:
        return None
    return decode_session_cookie(cookie_value)


def _check_run_ownership(db, run_id: str, user_id: int, user_role: str) -> tuple[bool, str]:
    """Verify the user owns the run or is an admin.

    Returns (allowed: bool, reason: str).
    """
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        return False, f"Run {run_id} not found"

    if run.user_id and run.user_id != user_id and user_role != "admin":
        return False, "Access denied: you do not own this run"

    return True, ""


@router.websocket("/ws/run/{run_id}/stream")
async def websocket_stream(websocket: WebSocket, run_id: str):
    """WebSocket endpoint for streaming pipeline events.

    Auth flow:
    1. Validate session cookie from the upgrade request headers.
    2. Query DB to confirm user is active.
    3. Check run ownership (user owns run, or user is admin).
    4. Accept connection only if all checks pass.
    """
    # Step 1: Validate session cookie
    session = _authenticate_websocket(websocket)
    if not session:
        await websocket.close(code=4001, reason="Authentication required")
        return

    # Step 2: Verify user is active + check run ownership
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == session["user_id"]).first()
        if not user or user.status != "active":
            await websocket.close(code=4001, reason="Authentication required")
            return

        allowed, reason = _check_run_ownership(
            db, run_id, user.id, user.role
        )
        if not allowed:
            await websocket.close(code=4003, reason=reason)
            return
    finally:
        db.close()

    # Step 3: Accept connection and stream events
    await event_emitter.connect(run_id, websocket)

    try:
        while True:
            try:
                message = await websocket.receive_text()
                # Client messages (ping/pong, future commands) handled here
            except WebSocketDisconnect:
                break
    except Exception as e:
        logger.warning(f"WebSocket error for run {run_id}: {e}")
    finally:
        event_emitter.disconnect(run_id, websocket)
```

- [ ] **Step 2: Verify WebSocket auth**

Open the browser dev console on the pipeline viewer page for a running pipeline. The WebSocket connection should establish successfully (the browser sends the session cookie automatically via the Vite proxy).

To test rejection:
```bash
# Connect without cookies using wscat (if installed)
npx wscat -c ws://localhost:8000/ws/run/RUNID/stream
# Expected: connection closed with code 4001 "Authentication required"
```

- [ ] **Step 3: Commit**

```bash
git add web_interface/backend/app/api/websocket.py
git commit -m "feat: add cookie-based auth and ownership check to WebSocket upgrade"
```

---

### Task 6: Backend — Auth on Upload Endpoint (Set user_id on Run)

**Files:**
- Modify: `web_interface/backend/app/api/upload.py`

The upload endpoint must associate new runs with the authenticated user by setting `user_id` on the Run record. This is required for user-scoped pagination to work.

- [ ] **Step 1: Add auth to upload endpoint**

In `web_interface/backend/app/api/upload.py`, add imports:

```python
from app.auth import get_current_user
from app.models import Run, Step, User
```

Update the `upload_cv` function signature to require authentication:

```python
@router.post("/upload", response_model=UploadResponse)
async def upload_cv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
```

Update the Run creation to set `user_id`:

```python
    run = Run(
        id=run_id,
        filename=file.filename,
        file_type=file_ext[1:],
        status="created",
        started_at=datetime.now(),
        user_id=current_user.id,
    )
```

Also add auth to the `estimate_processing` endpoint (optional but consistent):

```python
@router.post("/estimate", response_model=EstimateResponse)
async def estimate_processing(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
```

- [ ] **Step 2: Verify upload sets user_id**

```bash
# Upload a file while authenticated
curl -s -X POST http://localhost:8000/api/upload \
  -F "file=@test.docx" \
  -b cookies.txt | python3 -m json.tool

# Check the run record in the database
mysql -u root cviche -e "SELECT id, filename, user_id FROM runs ORDER BY created_at DESC LIMIT 1;"
# Expected: user_id is set to the logged-in user's ID
```

- [ ] **Step 3: Commit**

```bash
git add web_interface/backend/app/api/upload.py
git commit -m "feat: require auth on upload, set user_id on new runs"
```

---

### Task 7: Backend — Auth on Steps Endpoints

**Files:**
- Modify: `web_interface/backend/app/api/steps.py`

All step-related endpoints must verify authentication and run ownership. This includes `/api/run/{run_id}/step/{step_number}`, `/api/run/{run_id}/data/*`, and `/api/run/{run_id}/prompt-logs`.

- [ ] **Step 1: Add auth imports to steps.py**

Add at the top of `web_interface/backend/app/api/steps.py`:

```python
from app.auth import get_current_user
from app.models import User
```

- [ ] **Step 2: Create ownership check helper**

Add a helper function near the top of the file (after imports):

```python
def _check_run_access(run_id: str, db: Session, current_user: User) -> Run:
    """Verify run exists and user has access. Returns the Run or raises HTTPException."""
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": f"Run {run_id} not found"},
        )
    if run.user_id and run.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "You do not have access to this run."},
        )
    return run
```

- [ ] **Step 3: Add `current_user` parameter to all endpoints**

For every endpoint in `steps.py`, add `current_user: User = Depends(get_current_user)` to the function signature, and call `_check_run_access(run_id, db, current_user)` at the start of the function body. Example pattern:

```python
@router.get("/run/{run_id}/step/{step_number}")
async def get_step_detail(
    run_id: str,
    step_number: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _check_run_access(run_id, db, current_user)
    # ... rest of existing function body unchanged
```

Apply this pattern to every endpoint in the file.

- [ ] **Step 4: Verify**

```bash
# Access step data with auth
curl -s http://localhost:8000/api/run/RUNID/step/1 -b cookies.txt | python3 -m json.tool
# Expected: step data

# Without auth
curl -s http://localhost:8000/api/run/RUNID/step/1 | python3 -m json.tool
# Expected: 401
```

- [ ] **Step 5: Commit**

```bash
git add web_interface/backend/app/api/steps.py
git commit -m "feat: add auth and ownership checks to all step endpoints"
```

---

### Task 8: Integration Verification

**Files:** None (verification only)

- [ ] **Step 1: End-to-end pagination test**

1. Log in at `http://localhost:3000/login`.
2. Upload 2-3 test files to create runs.
3. Navigate to the upload page — run history should show your runs (not other users' runs).
4. Verify the "N total" count matches your actual runs.

- [ ] **Step 2: Restart flow test**

1. Navigate to a failed or cancelled run in the pipeline viewer.
2. Verify the colored banner displays with the correct status message.
3. Click "Restart with this file".
4. Verify a new run is created and you are navigated to it.
5. The new run should appear in your run history.

- [ ] **Step 3: File deletion edge case**

1. Create a run, then manually delete its upload file:
   ```bash
   rm web_interface/uploads/RUNID_filename.docx
   ```
2. Navigate to that run's pipeline viewer.
3. Click "Restart with this file".
4. Verify you see the error: "Original file no longer available. Please upload the file again."

- [ ] **Step 4: WebSocket auth test**

1. Open a running pipeline in the pipeline viewer.
2. Verify real-time events stream in the log viewer (WebSocket connected with cookie).
3. Open browser dev tools Network tab, filter by WS — verify the WebSocket connection is established.
4. Log out, then try to navigate directly to `/run/RUNID` — should redirect to login.

- [ ] **Step 5: Cross-user isolation test**

1. Log in as User A, create a run.
2. Log out, log in as User B.
3. User B's run history should NOT show User A's run.
4. User B navigating directly to `/run/USER_A_RUN_ID` should receive a 403 error.

- [ ] **Step 6: Commit (if any test-driven fixes were needed)**

```bash
git add -A
git commit -m "fix: address issues found during run history integration testing"
```
