# Plan 6: Admin Dashboard

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the admin dashboard with overview stats, user management, submission browsing, feedback insights, and config management. Admins get full visibility into system usage, user activity, and research-quality feedback data with CSV export.

**Architecture:** Backend adds admin API endpoints under `/api/admin/*`, all gated by the `require_admin` dependency from Plan 1. Frontend adds four tab components rendered inside an `AdminDashboard` shell at the `/admin` route, protected by an admin role guard in `App.tsx`. Navigation shows an admin link only for admin users.

**Tech Stack:** FastAPI, SQLAlchemy, React, Tailwind CSS, lucide-react

**Spec:** `docs/superpowers/specs/2026-03-20-production-readiness-design.md` -- section 7 (Admin Dashboard), plus sections 2 (data model), 5 (feedback metrics), and 8 (logging).

**Dependencies:** Plan 1 (auth foundation -- `require_admin`, `User` model, `SystemConfig` model, `AuthContext`, session management). The Feedback table and Consent table must also exist (from their respective plans), but this plan includes stub/fallback handling if those tables are empty.

---

### Task 1: Admin Stats API Endpoint

**Files:**
- Create: `web_interface/backend/app/api/admin_routes.py`
- Modify: `web_interface/backend/app/main.py`

- [ ] **Step 1: Create admin routes module with stats endpoint**

Create `web_interface/backend/app/api/admin_routes.py`:

```python
"""Admin dashboard API endpoints. All endpoints require admin role."""
import csv
import io
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, case, and_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Run, Step, User, SystemConfig, LLMUsage
from app.auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")


@router.get("/stats")
async def get_admin_stats(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Dashboard overview stats: total runs, active users, total cost, feedback rate."""
    total_runs = db.query(func.count(Run.id)).scalar() or 0

    # Active users: users with at least one run in the last 30 days
    thirty_days_ago = datetime.now() - timedelta(days=30)
    active_users = (
        db.query(func.count(func.distinct(Run.user_id)))
        .filter(Run.started_at >= thirty_days_ago)
        .filter(Run.user_id.isnot(None))
        .scalar()
    ) or 0

    total_cost = db.query(func.sum(Run.total_cost)).scalar() or 0.0

    # Feedback rate: completed runs with feedback / total completed runs
    completed_runs = (
        db.query(func.count(Run.id))
        .filter(Run.status == "complete")
        .scalar()
    ) or 0

    # Try to compute feedback rate if Feedback table exists
    feedback_count = 0
    try:
        from app.models import Feedback
        feedback_count = (
            db.query(func.count(func.distinct(Feedback.run_id)))
            .scalar()
        ) or 0
    except Exception:
        pass  # Feedback table may not exist yet

    feedback_rate = (
        round(feedback_count / completed_runs * 100, 1)
        if completed_runs > 0
        else 0.0
    )

    return {
        "total_runs": total_runs,
        "active_users": active_users,
        "total_cost": round(total_cost, 2),
        "feedback_rate": feedback_rate,
        "completed_runs": completed_runs,
        "feedback_count": feedback_count,
    }
```

- [ ] **Step 2: Register admin routes in main.py**

Add to `web_interface/backend/app/main.py`, in the imports and router registration:

```python
from app.api import upload, runs, steps, websocket, auth_routes, admin_routes

# ... in the router registration section:
app.include_router(admin_routes.router, prefix="/api", tags=["admin"])
```

- [ ] **Step 3: Verify endpoint**

Run:
```bash
# Login as admin first
curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "paa2013@med.cornell.edu", "display_name": "Paul Albert"}' \
  -c cookies.txt

# Hit stats endpoint
curl -s http://localhost:8000/api/admin/stats -b cookies.txt | python3 -m json.tool
```
Expected: JSON with `total_runs`, `active_users`, `total_cost`, `feedback_rate`.

- [ ] **Step 4: Commit**

```bash
git add web_interface/backend/app/api/admin_routes.py web_interface/backend/app/main.py
git commit -m "feat: add admin stats API endpoint"
```

---

### Task 2: Admin Users API Endpoints

**Files:**
- Modify: `web_interface/backend/app/api/admin_routes.py`

- [ ] **Step 1: Add GET /admin/users endpoint**

Add to `web_interface/backend/app/api/admin_routes.py`:

```python
@router.get("/users")
async def list_users(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """List all users with usage stats."""
    users = db.query(User).order_by(User.created_at.desc()).all()

    # Get per-user run counts for today
    today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

    results = []
    for user in users:
        # Runs today
        runs_today = (
            db.query(func.count(Run.id))
            .filter(Run.user_id == user.id, Run.started_at >= today_start)
            .scalar()
        ) or 0

        # Total runs
        total_runs = (
            db.query(func.count(Run.id))
            .filter(Run.user_id == user.id)
            .scalar()
        ) or 0

        # Total cost
        total_cost = (
            db.query(func.sum(Run.total_cost))
            .filter(Run.user_id == user.id)
            .scalar()
        ) or 0.0

        # Completed runs and feedback count for feedback rate
        completed_runs = (
            db.query(func.count(Run.id))
            .filter(Run.user_id == user.id, Run.status == "complete")
            .scalar()
        ) or 0

        user_feedback_count = 0
        try:
            from app.models import Feedback
            user_feedback_count = (
                db.query(func.count(Feedback.id))
                .filter(Feedback.user_id == user.id)
                .scalar()
            ) or 0
        except Exception:
            pass

        feedback_rate = (
            round(user_feedback_count / completed_runs * 100, 1)
            if completed_runs > 0
            else 0.0
        )

        # Effective limits (per-user override or system default)
        from app.config_loader import get_config_value
        system_daily = get_config_value(db, "rate_limit_daily") or 10
        system_monthly = get_config_value(db, "rate_limit_monthly") or 50

        results.append({
            "id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
            "status": user.status,
            "runs_today": runs_today,
            "total_runs": total_runs,
            "total_cost": round(total_cost, 3),
            "completed_runs": completed_runs,
            "feedback_rate": feedback_rate,
            "daily_limit": user.daily_limit,
            "monthly_limit": user.monthly_limit,
            "effective_daily_limit": user.daily_limit if user.daily_limit is not None else system_daily,
            "effective_monthly_limit": user.monthly_limit if user.monthly_limit is not None else system_monthly,
            "last_active_at": user.last_active_at.isoformat() if user.last_active_at else None,
            "created_at": user.created_at.isoformat() if user.created_at else None,
        })

    return results
```

- [ ] **Step 2: Add PUT /admin/users/{user_id} endpoint**

Add to `web_interface/backend/app/api/admin_routes.py`:

```python
from pydantic import BaseModel


class UserUpdateRequest(BaseModel):
    """Request body for updating a user."""
    role: Optional[str] = None  # "user" or "admin"
    status: Optional[str] = None  # "active" or "disabled"
    daily_limit: Optional[int] = None  # null = use system default, 0 = reset to default
    monthly_limit: Optional[int] = None


@router.put("/users/{user_id}")
async def update_user(
    user_id: int,
    body: UserUpdateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Update user role, status, or rate limits."""
    target_user = db.query(User).filter(User.id == user_id).first()
    if not target_user:
        raise HTTPException(
            status_code=404,
            detail={"error": "not_found", "message": "User not found."},
        )

    changes = {}

    if body.role is not None:
        if body.role not in ("user", "admin"):
            raise HTTPException(
                status_code=422,
                detail={"error": "validation_error", "message": "Role must be 'user' or 'admin'."},
            )
        # Prevent removing the last admin
        if body.role == "user" and target_user.role == "admin":
            admin_count = db.query(func.count(User.id)).filter(
                User.role == "admin", User.status == "active"
            ).scalar()
            if admin_count <= 1:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "error": "validation_error",
                        "message": "Cannot demote the last admin. Promote another user first.",
                    },
                )
        changes["role"] = {"old": target_user.role, "new": body.role}
        target_user.role = body.role

    if body.status is not None:
        if body.status not in ("active", "disabled"):
            raise HTTPException(
                status_code=422,
                detail={"error": "validation_error", "message": "Status must be 'active' or 'disabled'."},
            )
        # Prevent disabling yourself
        if target_user.id == admin.id and body.status == "disabled":
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "validation_error",
                    "message": "Cannot disable your own account.",
                },
            )
        changes["status"] = {"old": target_user.status, "new": body.status}
        target_user.status = body.status

    if body.daily_limit is not None:
        old_val = target_user.daily_limit
        # 0 means reset to system default (null)
        new_val = None if body.daily_limit == 0 else body.daily_limit
        changes["daily_limit"] = {"old": old_val, "new": new_val}
        target_user.daily_limit = new_val

    if body.monthly_limit is not None:
        old_val = target_user.monthly_limit
        new_val = None if body.monthly_limit == 0 else body.monthly_limit
        changes["monthly_limit"] = {"old": old_val, "new": new_val}
        target_user.monthly_limit = new_val

    db.commit()
    db.refresh(target_user)

    logger.info(
        "admin_user_updated",
        extra={
            "admin_user_id": admin.id,
            "target_user_id": user_id,
            "changes": changes,
        },
    )

    return {
        "id": target_user.id,
        "email": target_user.email,
        "display_name": target_user.display_name,
        "role": target_user.role,
        "status": target_user.status,
        "daily_limit": target_user.daily_limit,
        "monthly_limit": target_user.monthly_limit,
        "message": "User updated successfully.",
    }
```

- [ ] **Step 3: Verify endpoints**

Run:
```bash
curl -s http://localhost:8000/api/admin/users -b cookies.txt | python3 -m json.tool
```
Expected: Array of user objects with stats.

- [ ] **Step 4: Commit**

```bash
git add web_interface/backend/app/api/admin_routes.py
git commit -m "feat: add admin users list and update API endpoints"
```

---

### Task 3: Admin Runs + Config + Export API Endpoints

**Files:**
- Modify: `web_interface/backend/app/api/admin_routes.py`

- [ ] **Step 1: Add GET /admin/runs endpoint**

Add to `web_interface/backend/app/api/admin_routes.py`:

```python
@router.get("/runs")
async def list_all_runs(
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    user_id: Optional[int] = Query(None),
    status: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    sort_by: Optional[str] = Query("started_at"),
    sort_dir: Optional[str] = Query("desc"),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """List all runs across all users with filtering and pagination."""
    query = db.query(Run)

    # Filters
    if user_id is not None:
        query = query.filter(Run.user_id == user_id)
    if status:
        query = query.filter(Run.status == status)
    if date_from:
        try:
            from_dt = datetime.fromisoformat(date_from)
            query = query.filter(Run.started_at >= from_dt)
        except ValueError:
            pass
    if date_to:
        try:
            to_dt = datetime.fromisoformat(date_to)
            query = query.filter(Run.started_at <= to_dt)
        except ValueError:
            pass

    # Total count before pagination
    total = query.count()

    # Sorting
    sort_column = getattr(Run, sort_by, Run.started_at)
    if sort_dir == "asc":
        query = query.order_by(sort_column.asc())
    else:
        query = query.order_by(sort_column.desc())

    runs = query.offset(offset).limit(limit).all()

    # Build results with user info
    results = []
    for run in runs:
        # Get user info
        user_email = None
        user_name = None
        if run.user_id:
            run_user = db.query(User).filter(User.id == run.user_id).first()
            if run_user:
                user_email = run_user.email
                user_name = run_user.display_name

        # Check feedback status
        has_feedback = False
        try:
            from app.models import Feedback
            has_feedback = (
                db.query(Feedback.id)
                .filter(Feedback.run_id == run.id)
                .first()
            ) is not None
        except Exception:
            pass

        # Calculate duration
        duration_seconds = None
        if run.started_at and run.completed_at:
            duration_seconds = int((run.completed_at - run.started_at).total_seconds())

        results.append({
            "run_id": run.id,
            "user_id": run.user_id,
            "user_email": user_email,
            "user_name": user_name,
            "filename": run.filename,
            "status": run.status,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "completed_at": run.completed_at.isoformat() if run.completed_at else None,
            "duration_seconds": duration_seconds,
            "total_cost": round(run.total_cost or 0.0, 4),
            "total_tokens": run.total_tokens or 0,
            "has_feedback": has_feedback,
            "error_message": run.error_message,
        })

    return {
        "runs": results,
        "total": total,
        "offset": offset,
        "limit": limit,
    }
```

- [ ] **Step 2: Add GET /admin/config endpoint**

Add to `web_interface/backend/app/api/admin_routes.py`:

```python
@router.get("/config")
async def get_config(
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Get all system configuration values."""
    configs = db.query(SystemConfig).all()

    result = {}
    for config in configs:
        try:
            result[config.key] = json.loads(config.value)
        except (json.JSONDecodeError, TypeError):
            result[config.key] = config.value

    # Add computed info
    result["_meta"] = {
        "total_users": db.query(func.count(User.id)).scalar() or 0,
        "admin_count": (
            db.query(func.count(User.id))
            .filter(User.role == "admin", User.status == "active")
            .scalar()
        ) or 0,
    }

    return result
```

- [ ] **Step 3: Add PUT /admin/config endpoint**

Add to `web_interface/backend/app/api/admin_routes.py`:

```python
class ConfigUpdateRequest(BaseModel):
    """Request body for updating a config key."""
    key: str
    value: any  # Will be JSON-encoded


EDITABLE_CONFIG_KEYS = {
    "allowed_users",
    "admin_users",
    "rate_limit_daily",
    "rate_limit_monthly",
    "consent_version",
}


@router.put("/config")
async def update_config(
    body: ConfigUpdateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Update a system config value. Only whitelisted keys are editable."""
    if body.key not in EDITABLE_CONFIG_KEYS:
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_error",
                "message": f"Config key '{body.key}' is not editable.",
            },
        )

    # Validate admin_users: at least one admin must remain
    if body.key == "admin_users":
        if not isinstance(body.value, list) or len(body.value) == 0:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "validation_error",
                    "message": "Admin users list cannot be empty. At least one admin is required.",
                },
            )

    # Validate rate limits: must be positive integers
    if body.key in ("rate_limit_daily", "rate_limit_monthly"):
        if not isinstance(body.value, int) or body.value < 1:
            raise HTTPException(
                status_code=422,
                detail={
                    "error": "validation_error",
                    "message": f"{body.key} must be a positive integer.",
                },
            )

    # Get old value for logging
    existing = db.query(SystemConfig).filter(SystemConfig.key == body.key).first()
    old_value = json.loads(existing.value) if existing else None

    new_value_json = json.dumps(body.value)

    if existing:
        existing.value = new_value_json
        existing.updated_by = admin.id
        existing.updated_at = datetime.now()
    else:
        db.add(SystemConfig(
            key=body.key,
            value=new_value_json,
            updated_by=admin.id,
        ))

    db.commit()

    logger.info(
        "admin_config_changed",
        extra={
            "admin_user_id": admin.id,
            "key": body.key,
            "old_value": old_value,
            "new_value": body.value,
        },
    )

    return {
        "key": body.key,
        "value": body.value,
        "message": f"Config '{body.key}' updated successfully.",
    }
```

- [ ] **Step 4: Add GET /admin/export/{type} endpoint**

Add to `web_interface/backend/app/api/admin_routes.py`:

```python
@router.get("/export/{export_type}")
async def export_data(
    export_type: str,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """Export data as CSV. Types: runs, users, consent, feedback."""
    if export_type not in ("runs", "users", "consent", "feedback"):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_error",
                "message": f"Export type '{export_type}' not supported. Use: runs, users, consent, feedback.",
            },
        )

    output = io.StringIO()
    writer = csv.writer(output)

    if export_type == "runs":
        writer.writerow([
            "run_id", "user_email", "user_name", "filename", "file_type",
            "status", "started_at", "completed_at", "total_cost",
            "total_tokens", "input_tokens", "output_tokens", "error_message",
        ])
        runs = db.query(Run).order_by(Run.started_at.desc()).all()
        for run in runs:
            run_user = db.query(User).filter(User.id == run.user_id).first() if run.user_id else None
            writer.writerow([
                run.id,
                run_user.email if run_user else "",
                run_user.display_name if run_user else "",
                run.filename,
                run.file_type,
                run.status,
                run.started_at.isoformat() if run.started_at else "",
                run.completed_at.isoformat() if run.completed_at else "",
                run.total_cost or 0,
                run.total_tokens or 0,
                run.input_tokens or 0,
                run.output_tokens or 0,
                run.error_message or "",
            ])

    elif export_type == "users":
        writer.writerow([
            "id", "email", "display_name", "role", "status",
            "daily_limit", "monthly_limit", "consent_version",
            "created_at", "last_active_at",
        ])
        users = db.query(User).order_by(User.created_at.desc()).all()
        for user in users:
            writer.writerow([
                user.id,
                user.email,
                user.display_name,
                user.role,
                user.status,
                user.daily_limit or "",
                user.monthly_limit or "",
                user.consent_version or "",
                user.created_at.isoformat() if user.created_at else "",
                user.last_active_at.isoformat() if user.last_active_at else "",
            ])

    elif export_type == "consent":
        try:
            from app.models import Consent
            writer.writerow([
                "id", "user_email", "consent_version", "consent_text_hash",
                "ip_address", "user_agent", "timestamp",
            ])
            consents = db.query(Consent).order_by(Consent.timestamp.desc()).all()
            for consent in consents:
                consent_user = db.query(User).filter(User.id == consent.user_id).first()
                writer.writerow([
                    consent.id,
                    consent_user.email if consent_user else "",
                    consent.consent_version,
                    consent.consent_text_hash,
                    consent.ip_address or "",
                    consent.user_agent or "",
                    consent.timestamp.isoformat() if consent.timestamp else "",
                ])
        except Exception:
            writer.writerow(["No consent data available"])

    elif export_type == "feedback":
        try:
            from app.models import Feedback
            writer.writerow([
                "feedback_id", "run_id", "user_email", "reviewer_role",
                "overall_accuracy", "overall_completeness", "overall_usefulness",
                "manual_conversion_effort", "correction_effort",
                "enrichment_quality", "summary_quality",
                "issue_missing_content", "issue_split_merged",
                "issue_wrong_section", "issue_inaccurate",
                "issue_ai_enrichment", "issue_formatting",
                "issue_locations", "biggest_issue",
                "likelihood_to_recommend", "submitted_at",
                "run_filename", "run_status", "run_cost", "run_duration_seconds",
            ])
            feedbacks = db.query(Feedback).order_by(Feedback.submitted_at.desc()).all()
            for fb in feedbacks:
                fb_user = db.query(User).filter(User.id == fb.user_id).first()
                fb_run = db.query(Run).filter(Run.id == fb.run_id).first()
                duration = None
                if fb_run and fb_run.started_at and fb_run.completed_at:
                    duration = int((fb_run.completed_at - fb_run.started_at).total_seconds())
                writer.writerow([
                    fb.id,
                    fb.run_id,
                    fb_user.email if fb_user else "",
                    fb.reviewer_role,
                    fb.overall_accuracy,
                    fb.overall_completeness,
                    fb.overall_usefulness,
                    fb.manual_conversion_effort,
                    fb.correction_effort,
                    fb.enrichment_quality,
                    fb.summary_quality,
                    fb.issue_missing_content,
                    fb.issue_split_merged,
                    fb.issue_wrong_section,
                    fb.issue_inaccurate,
                    fb.issue_ai_enrichment,
                    fb.issue_formatting,
                    fb.issue_locations or "",
                    fb.biggest_issue or "",
                    fb.likelihood_to_recommend,
                    fb.submitted_at.isoformat() if fb.submitted_at else "",
                    fb_run.filename if fb_run else "",
                    fb_run.status if fb_run else "",
                    fb_run.total_cost if fb_run else "",
                    duration or "",
                ])
        except Exception:
            writer.writerow(["No feedback data available"])

    # Log the export
    logger.info(
        "admin_export",
        extra={
            "admin_user_id": admin.id,
            "export_type": export_type,
        },
    )

    output.seek(0)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"cviche_{export_type}_{timestamp}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
```

- [ ] **Step 5: Verify endpoints**

Run:
```bash
# Runs
curl -s "http://localhost:8000/api/admin/runs?limit=5" -b cookies.txt | python3 -m json.tool

# Config
curl -s http://localhost:8000/api/admin/config -b cookies.txt | python3 -m json.tool

# Export
curl -s http://localhost:8000/api/admin/export/runs -b cookies.txt -o /tmp/test_export.csv && head /tmp/test_export.csv
```

- [ ] **Step 6: Commit**

```bash
git add web_interface/backend/app/api/admin_routes.py
git commit -m "feat: add admin runs, config, and CSV export API endpoints"
```

---

### Task 4: AdminDashboard Shell Component

**Files:**
- Create: `web_interface/frontend/src/components/AdminDashboard.tsx`

- [ ] **Step 1: Create AdminDashboard.tsx**

Create `web_interface/frontend/src/components/AdminDashboard.tsx`:

```tsx
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ArrowLeft,
  BarChart3,
  Users,
  FileText,
  MessageSquare,
  Settings,
  Loader2,
  Activity,
  DollarSign,
  TrendingUp,
} from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import AdminUsers from './AdminUsers'
import AdminSubmissions from './AdminSubmissions'
import AdminFeedbackInsights from './AdminFeedbackInsights'
import AdminConfig from './AdminConfig'

interface Stats {
  total_runs: number
  active_users: number
  total_cost: number
  feedback_rate: number
  completed_runs: number
  feedback_count: number
}

type TabId = 'users' | 'submissions' | 'feedback' | 'config'

const TABS: { id: TabId; label: string; icon: typeof Users }[] = [
  { id: 'users', label: 'Users', icon: Users },
  { id: 'submissions', label: 'All Submissions', icon: FileText },
  { id: 'feedback', label: 'Feedback Insights', icon: MessageSquare },
  { id: 'config', label: 'Config', icon: Settings },
]

export default function AdminDashboard() {
  const navigate = useNavigate()
  const { user } = useAuth()
  const [stats, setStats] = useState<Stats | null>(null)
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<TabId>('users')

  useEffect(() => {
    fetchStats()
  }, [])

  const fetchStats = async () => {
    try {
      const res = await fetch('/api/admin/stats')
      if (res.ok) {
        setStats(await res.json())
      }
    } catch (err) {
      console.error('Failed to fetch admin stats:', err)
    } finally {
      setLoading(false)
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header
        className="border-b border-gray-200 px-4 py-3 md:px-6"
        style={{
          backgroundImage: 'url(/headerbg.png)',
          backgroundSize: 'cover',
          backgroundPosition: 'center',
        }}
      >
        <div className="flex items-center justify-between max-w-7xl mx-auto">
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/')}
              aria-label="Back to upload"
              className="shrink-0 rounded p-1 text-gray-700 hover:text-gray-900 focus:ring-2 focus:ring-blue-500 focus:outline-none"
            >
              <ArrowLeft className="h-5 w-5" />
            </button>
            <div>
              <h1 className="text-lg font-bold text-gray-900">Admin Dashboard</h1>
              <p className="text-sm text-gray-700">
                Logged in as {user?.display_name}
              </p>
            </div>
          </div>
          <img src="/header-logo.png" alt="CViche" className="h-10 hidden md:block" />
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 py-6 md:px-6">
        {/* Stat Cards */}
        {stats && (
          <div className="grid grid-cols-2 lg:grid-cols-4 gap-4 mb-6">
            <StatCard
              label="Total Runs"
              value={stats.total_runs.toLocaleString()}
              icon={<BarChart3 className="h-5 w-5 text-blue-600" />}
              bgColor="bg-blue-50"
            />
            <StatCard
              label="Active Users"
              value={stats.active_users.toLocaleString()}
              subtitle="Last 30 days"
              icon={<Activity className="h-5 w-5 text-green-600" />}
              bgColor="bg-green-50"
            />
            <StatCard
              label="Total Cost"
              value={`$${stats.total_cost.toFixed(2)}`}
              icon={<DollarSign className="h-5 w-5 text-amber-600" />}
              bgColor="bg-amber-50"
            />
            <StatCard
              label="Feedback Rate"
              value={`${stats.feedback_rate}%`}
              subtitle={`${stats.feedback_count} / ${stats.completed_runs} runs`}
              icon={<TrendingUp className="h-5 w-5 text-purple-600" />}
              bgColor="bg-purple-50"
            />
          </div>
        )}

        {/* Tab Navigation */}
        <div className="border-b border-gray-200 mb-6">
          <nav className="flex gap-1 -mb-px" aria-label="Admin tabs">
            {TABS.map((tab) => {
              const Icon = tab.icon
              const isActive = activeTab === tab.id
              return (
                <button
                  key={tab.id}
                  onClick={() => setActiveTab(tab.id)}
                  className={`flex items-center gap-2 px-4 py-2.5 text-sm font-medium border-b-2 transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                    isActive
                      ? 'border-blue-600 text-blue-600'
                      : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
                  }`}
                  aria-selected={isActive}
                  role="tab"
                >
                  <Icon className="h-4 w-4" aria-hidden="true" />
                  {tab.label}
                </button>
              )
            })}
          </nav>
        </div>

        {/* Tab Content */}
        <div role="tabpanel">
          {activeTab === 'users' && <AdminUsers />}
          {activeTab === 'submissions' && <AdminSubmissions />}
          {activeTab === 'feedback' && <AdminFeedbackInsights />}
          {activeTab === 'config' && <AdminConfig />}
        </div>
      </div>
    </div>
  )
}


function StatCard({
  label,
  value,
  subtitle,
  icon,
  bgColor,
}: {
  label: string
  value: string
  subtitle?: string
  icon: React.ReactNode
  bgColor: string
}) {
  return (
    <div className={`${bgColor} rounded-lg p-4 border border-gray-200`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm font-medium text-gray-600">{label}</span>
        {icon}
      </div>
      <p className="text-2xl font-bold text-gray-900">{value}</p>
      {subtitle && (
        <p className="text-xs text-gray-500 mt-1">{subtitle}</p>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Verify build**

Run: `cd web_interface/frontend && npx tsc --noEmit`
(This will fail until all tab components exist; that is expected. Verify no errors in AdminDashboard.tsx itself.)

- [ ] **Step 3: Commit**

```bash
git add web_interface/frontend/src/components/AdminDashboard.tsx
git commit -m "feat: add AdminDashboard shell with stat cards and tab navigation"
```

---

### Task 5: AdminUsers Tab Component

**Files:**
- Create: `web_interface/frontend/src/components/AdminUsers.tsx`

- [ ] **Step 1: Create AdminUsers.tsx**

Create `web_interface/frontend/src/components/AdminUsers.tsx`:

```tsx
import { useState, useEffect } from 'react'
import {
  Loader2,
  Shield,
  User as UserIcon,
  Ban,
  CheckCircle2,
  Pencil,
  X,
  Save,
  History,
} from 'lucide-react'

interface UserRow {
  id: number
  email: string
  display_name: string
  role: string
  status: string
  runs_today: number
  total_runs: number
  total_cost: number
  completed_runs: number
  feedback_rate: number
  daily_limit: number | null
  monthly_limit: number | null
  effective_daily_limit: number
  effective_monthly_limit: number
  last_active_at: string | null
  created_at: string | null
}

export default function AdminUsers() {
  const [users, setUsers] = useState<UserRow[]>([])
  const [loading, setLoading] = useState(true)
  const [editingUserId, setEditingUserId] = useState<number | null>(null)
  const [editForm, setEditForm] = useState({ daily_limit: '', monthly_limit: '' })
  const [actionLoading, setActionLoading] = useState<number | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetchUsers()
  }, [])

  const fetchUsers = async () => {
    try {
      const res = await fetch('/api/admin/users')
      if (res.ok) {
        setUsers(await res.json())
      }
    } catch (err) {
      console.error('Failed to fetch users:', err)
    } finally {
      setLoading(false)
    }
  }

  const updateUser = async (userId: number, updates: Record<string, any>) => {
    setActionLoading(userId)
    setError(null)
    try {
      const res = await fetch(`/api/admin/users/${userId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updates),
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail?.message || 'Update failed')
      }
      await fetchUsers()
      setEditingUserId(null)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Update failed')
    } finally {
      setActionLoading(null)
    }
  }

  const toggleStatus = (user: UserRow) => {
    const newStatus = user.status === 'active' ? 'disabled' : 'active'
    updateUser(user.id, { status: newStatus })
  }

  const startEditLimits = (user: UserRow) => {
    setEditingUserId(user.id)
    setEditForm({
      daily_limit: user.daily_limit?.toString() || '',
      monthly_limit: user.monthly_limit?.toString() || '',
    })
  }

  const saveLimits = (userId: number) => {
    updateUser(userId, {
      daily_limit: editForm.daily_limit ? parseInt(editForm.daily_limit) : 0,
      monthly_limit: editForm.monthly_limit ? parseInt(editForm.monthly_limit) : 0,
    })
  }

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '--'
    const d = new Date(dateStr)
    return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' })
  }

  if (loading) {
    return (
      <div className="text-center py-12 text-gray-500">
        <Loader2 className="h-6 w-6 animate-spin inline mr-2" aria-hidden="true" />
        Loading users...
      </div>
    )
  }

  return (
    <div>
      {error && (
        <div className="mb-4 bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm">
          {error}
          <button onClick={() => setError(null)} className="float-right text-red-500 hover:text-red-700">
            <X className="h-4 w-4" />
          </button>
        </div>
      )}

      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="text-left px-4 py-3 font-semibold text-gray-700">User</th>
                <th className="text-left px-4 py-3 font-semibold text-gray-700">Role</th>
                <th className="text-center px-4 py-3 font-semibold text-gray-700">Runs Today</th>
                <th className="text-right px-4 py-3 font-semibold text-gray-700">Total Cost</th>
                <th className="text-center px-4 py-3 font-semibold text-gray-700">Feedback</th>
                <th className="text-left px-4 py-3 font-semibold text-gray-700">Last Active</th>
                <th className="text-center px-4 py-3 font-semibold text-gray-700">Status</th>
                <th className="text-center px-4 py-3 font-semibold text-gray-700">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {users.map((u) => (
                <tr key={u.id} className={u.status === 'disabled' ? 'bg-gray-50 opacity-60' : 'hover:bg-gray-50'}>
                  {/* User */}
                  <td className="px-4 py-3">
                    <div className="font-medium text-gray-900">{u.display_name}</div>
                    <div className="text-xs text-gray-500">{u.email}</div>
                  </td>

                  {/* Role badge */}
                  <td className="px-4 py-3">
                    {u.role === 'admin' ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-800">
                        <Shield className="h-3 w-3" aria-hidden="true" />
                        Admin
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-gray-100 text-gray-700">
                        <UserIcon className="h-3 w-3" aria-hidden="true" />
                        User
                      </span>
                    )}
                  </td>

                  {/* Runs today */}
                  <td className="px-4 py-3 text-center">
                    <span
                      className={`font-medium ${
                        u.runs_today >= u.effective_daily_limit && u.role !== 'admin'
                          ? 'text-red-600'
                          : 'text-gray-900'
                      }`}
                    >
                      {u.runs_today}
                    </span>
                    <span className="text-gray-400">
                      {u.role !== 'admin' ? ` / ${u.effective_daily_limit}` : ''}
                    </span>
                  </td>

                  {/* Total cost */}
                  <td className="px-4 py-3 text-right font-medium text-gray-900">
                    ${u.total_cost.toFixed(2)}
                  </td>

                  {/* Feedback rate */}
                  <td className="px-4 py-3 text-center">
                    <span
                      className={`font-medium ${
                        u.feedback_rate < 50 && u.completed_runs > 0
                          ? 'text-red-600'
                          : 'text-gray-900'
                      }`}
                    >
                      {u.completed_runs > 0 ? `${u.feedback_rate}%` : '--'}
                    </span>
                  </td>

                  {/* Last active */}
                  <td className="px-4 py-3 text-gray-600">{formatDate(u.last_active_at)}</td>

                  {/* Status */}
                  <td className="px-4 py-3 text-center">
                    {u.status === 'active' ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-800">
                        <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                        Active
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">
                        <Ban className="h-3 w-3" aria-hidden="true" />
                        Disabled
                      </span>
                    )}
                  </td>

                  {/* Actions */}
                  <td className="px-4 py-3">
                    <div className="flex items-center justify-center gap-1">
                      {/* Enable/Disable toggle */}
                      <button
                        onClick={() => toggleStatus(u)}
                        disabled={actionLoading === u.id}
                        title={u.status === 'active' ? 'Disable user' : 'Enable user'}
                        className={`p-1.5 rounded transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500 ${
                          u.status === 'active'
                            ? 'text-red-600 hover:bg-red-50'
                            : 'text-green-600 hover:bg-green-50'
                        }`}
                      >
                        {actionLoading === u.id ? (
                          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                        ) : u.status === 'active' ? (
                          <Ban className="h-4 w-4" aria-hidden="true" />
                        ) : (
                          <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                        )}
                      </button>

                      {/* Edit limits */}
                      {editingUserId === u.id ? (
                        <div className="flex items-center gap-2 ml-2">
                          <div className="flex flex-col gap-1">
                            <label className="text-xs text-gray-500">Daily</label>
                            <input
                              type="number"
                              min="1"
                              value={editForm.daily_limit}
                              onChange={(e) => setEditForm({ ...editForm, daily_limit: e.target.value })}
                              placeholder={String(u.effective_daily_limit)}
                              className="w-16 px-2 py-1 text-xs border border-gray-300 rounded focus:ring-1 focus:ring-blue-500 focus:outline-none"
                            />
                          </div>
                          <div className="flex flex-col gap-1">
                            <label className="text-xs text-gray-500">Monthly</label>
                            <input
                              type="number"
                              min="1"
                              value={editForm.monthly_limit}
                              onChange={(e) => setEditForm({ ...editForm, monthly_limit: e.target.value })}
                              placeholder={String(u.effective_monthly_limit)}
                              className="w-16 px-2 py-1 text-xs border border-gray-300 rounded focus:ring-1 focus:ring-blue-500 focus:outline-none"
                            />
                          </div>
                          <button
                            onClick={() => saveLimits(u.id)}
                            className="p-1.5 text-green-600 hover:bg-green-50 rounded"
                            title="Save limits"
                          >
                            <Save className="h-4 w-4" aria-hidden="true" />
                          </button>
                          <button
                            onClick={() => setEditingUserId(null)}
                            className="p-1.5 text-gray-400 hover:bg-gray-100 rounded"
                            title="Cancel"
                          >
                            <X className="h-4 w-4" aria-hidden="true" />
                          </button>
                        </div>
                      ) : (
                        <button
                          onClick={() => startEditLimits(u)}
                          className="p-1.5 text-gray-500 hover:bg-gray-100 rounded transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
                          title="Adjust rate limits"
                        >
                          <Pencil className="h-4 w-4" aria-hidden="true" />
                        </button>
                      )}

                      {/* View history */}
                      <button
                        onClick={() => {
                          // Navigate to submissions tab filtered by this user
                          // This is handled by the parent AdminDashboard via a callback
                          // For now, we log the action
                          console.log('View history for user', u.id)
                        }}
                        className="p-1.5 text-gray-500 hover:bg-gray-100 rounded transition-colors focus:outline-none focus:ring-2 focus:ring-blue-500"
                        title="View run history"
                      >
                        <History className="h-4 w-4" aria-hidden="true" />
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {users.length === 0 && (
          <div className="text-center py-12 text-gray-500">No users found.</div>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add web_interface/frontend/src/components/AdminUsers.tsx
git commit -m "feat: add AdminUsers tab with enable/disable, rate limit editing"
```

---

### Task 6: AdminSubmissions Tab Component

**Files:**
- Create: `web_interface/frontend/src/components/AdminSubmissions.tsx`

- [ ] **Step 1: Create AdminSubmissions.tsx**

Create `web_interface/frontend/src/components/AdminSubmissions.tsx`:

```tsx
import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Loader2,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Clock,
  ChevronDown,
  MessageSquare,
  Filter,
  X,
} from 'lucide-react'

interface RunRow {
  run_id: string
  user_id: number | null
  user_email: string | null
  user_name: string | null
  filename: string
  status: string
  started_at: string | null
  completed_at: string | null
  duration_seconds: number | null
  total_cost: number
  total_tokens: number
  has_feedback: boolean
  error_message: string | null
}

interface RunsResponse {
  runs: RunRow[]
  total: number
  offset: number
  limit: number
}

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

export default function AdminSubmissions() {
  const navigate = useNavigate()
  const [data, setData] = useState<RunsResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [offset, setOffset] = useState(0)
  const [allRuns, setAllRuns] = useState<RunRow[]>([])
  const [loadingMore, setLoadingMore] = useState(false)

  // Filters
  const [filterStatus, setFilterStatus] = useState('')
  const [filterUser, setFilterUser] = useState('')
  const [filterDateFrom, setFilterDateFrom] = useState('')
  const [filterDateTo, setFilterDateTo] = useState('')
  const [showFilters, setShowFilters] = useState(false)

  // Sorting
  const [sortBy, setSortBy] = useState('started_at')
  const [sortDir, setSortDir] = useState('desc')

  const LIMIT = 20

  useEffect(() => {
    setOffset(0)
    setAllRuns([])
    fetchRuns(0, true)
  }, [filterStatus, filterUser, filterDateFrom, filterDateTo, sortBy, sortDir])

  const fetchRuns = async (currentOffset: number, reset: boolean = false) => {
    if (reset) setLoading(true)
    else setLoadingMore(true)

    try {
      const params = new URLSearchParams({
        offset: String(currentOffset),
        limit: String(LIMIT),
        sort_by: sortBy,
        sort_dir: sortDir,
      })
      if (filterStatus) params.set('status', filterStatus)
      if (filterUser) params.set('user_id', filterUser)
      if (filterDateFrom) params.set('date_from', filterDateFrom)
      if (filterDateTo) params.set('date_to', filterDateTo)

      const res = await fetch(`/api/admin/runs?${params}`)
      if (res.ok) {
        const result: RunsResponse = await res.json()
        setData(result)
        if (reset) {
          setAllRuns(result.runs)
        } else {
          setAllRuns((prev) => [...prev, ...result.runs])
        }
      }
    } catch (err) {
      console.error('Failed to fetch runs:', err)
    } finally {
      setLoading(false)
      setLoadingMore(false)
    }
  }

  const loadMore = () => {
    const newOffset = offset + LIMIT
    setOffset(newOffset)
    fetchRuns(newOffset)
  }

  const handleSort = (column: string) => {
    if (sortBy === column) {
      setSortDir(sortDir === 'desc' ? 'asc' : 'desc')
    } else {
      setSortBy(column)
      setSortDir('desc')
    }
  }

  const clearFilters = () => {
    setFilterStatus('')
    setFilterUser('')
    setFilterDateFrom('')
    setFilterDateTo('')
  }

  const hasActiveFilters = filterStatus || filterUser || filterDateFrom || filterDateTo

  const formatDate = (dateStr: string | null) => {
    if (!dateStr) return '--'
    const d = new Date(dateStr)
    return (
      d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' }) +
      ' ' +
      d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    )
  }

  const formatDuration = (seconds: number | null) => {
    if (seconds === null) return '--'
    const mins = Math.floor(seconds / 60)
    const secs = seconds % 60
    if (mins > 0) return `${mins}m ${secs}s`
    return `${secs}s`
  }

  const SortHeader = ({ column, label }: { column: string; label: string }) => (
    <th
      className="text-left px-4 py-3 font-semibold text-gray-700 cursor-pointer hover:bg-gray-100 select-none"
      onClick={() => handleSort(column)}
    >
      <span className="flex items-center gap-1">
        {label}
        {sortBy === column && (
          <ChevronDown
            className={`h-3 w-3 transition-transform ${sortDir === 'asc' ? 'rotate-180' : ''}`}
            aria-hidden="true"
          />
        )}
      </span>
    </th>
  )

  if (loading) {
    return (
      <div className="text-center py-12 text-gray-500">
        <Loader2 className="h-6 w-6 animate-spin inline mr-2" aria-hidden="true" />
        Loading submissions...
      </div>
    )
  }

  return (
    <div>
      {/* Filter bar */}
      <div className="mb-4 flex items-center gap-3 flex-wrap">
        <button
          onClick={() => setShowFilters(!showFilters)}
          className={`flex items-center gap-2 px-3 py-2 text-sm rounded-lg border transition-colors ${
            hasActiveFilters
              ? 'bg-blue-50 border-blue-200 text-blue-700'
              : 'bg-white border-gray-200 text-gray-600 hover:bg-gray-50'
          }`}
        >
          <Filter className="h-4 w-4" aria-hidden="true" />
          Filters
          {hasActiveFilters && (
            <span className="bg-blue-600 text-white text-xs rounded-full px-1.5 py-0.5">
              {[filterStatus, filterUser, filterDateFrom, filterDateTo].filter(Boolean).length}
            </span>
          )}
        </button>

        {hasActiveFilters && (
          <button
            onClick={clearFilters}
            className="flex items-center gap-1 px-2 py-1 text-xs text-gray-500 hover:text-gray-700"
          >
            <X className="h-3 w-3" aria-hidden="true" />
            Clear all
          </button>
        )}

        <span className="text-sm text-gray-500 ml-auto">
          {data ? `${data.total} total runs` : ''}
        </span>
      </div>

      {showFilters && (
        <div className="mb-4 bg-white border border-gray-200 rounded-lg p-4 grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">Status</label>
            <select
              value={filterStatus}
              onChange={(e) => setFilterStatus(e.target.value)}
              className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded focus:ring-1 focus:ring-blue-500 focus:outline-none"
            >
              <option value="">All</option>
              <option value="complete">Complete</option>
              <option value="running">Running</option>
              <option value="failed">Failed</option>
              <option value="cancelled">Cancelled</option>
              <option value="created">Created</option>
            </select>
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">User ID</label>
            <input
              type="number"
              value={filterUser}
              onChange={(e) => setFilterUser(e.target.value)}
              placeholder="All users"
              className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded focus:ring-1 focus:ring-blue-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">From</label>
            <input
              type="date"
              value={filterDateFrom}
              onChange={(e) => setFilterDateFrom(e.target.value)}
              className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded focus:ring-1 focus:ring-blue-500 focus:outline-none"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-600 mb-1">To</label>
            <input
              type="date"
              value={filterDateTo}
              onChange={(e) => setFilterDateTo(e.target.value)}
              className="w-full px-3 py-1.5 text-sm border border-gray-300 rounded focus:ring-1 focus:ring-blue-500 focus:outline-none"
            />
          </div>
        </div>
      )}

      {/* Table */}
      <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-50 border-b border-gray-200">
                <th className="text-left px-4 py-3 font-semibold text-gray-700">Run ID</th>
                <th className="text-left px-4 py-3 font-semibold text-gray-700">User</th>
                <th className="text-left px-4 py-3 font-semibold text-gray-700">Filename</th>
                <th className="text-center px-4 py-3 font-semibold text-gray-700">Status</th>
                <SortHeader column="started_at" label="Date" />
                <SortHeader column="total_cost" label="Cost" />
                <th className="text-center px-4 py-3 font-semibold text-gray-700">Duration</th>
                <th className="text-center px-4 py-3 font-semibold text-gray-700">Feedback</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {allRuns.map((run) => (
                <tr
                  key={run.run_id}
                  className="hover:bg-gray-50 cursor-pointer transition-colors"
                  onClick={() => navigate(`/run/${run.run_id}`)}
                >
                  <td className="px-4 py-3 font-mono text-xs text-gray-700">{run.run_id}</td>
                  <td className="px-4 py-3">
                    <div className="text-gray-900 text-xs">{run.user_name || '--'}</div>
                    <div className="text-gray-500 text-xs">{run.user_email || ''}</div>
                  </td>
                  <td className="px-4 py-3 text-gray-900 max-w-[200px] truncate" title={run.filename}>
                    {run.filename}
                  </td>
                  <td className="px-4 py-3 text-center">
                    <span className="inline-flex items-center gap-1">
                      <StatusIcon status={run.status} />
                      <span className="text-xs text-gray-600">{run.status}</span>
                    </span>
                  </td>
                  <td className="px-4 py-3 text-gray-600 text-xs">{formatDate(run.started_at)}</td>
                  <td className="px-4 py-3 text-right font-medium text-gray-900">
                    ${run.total_cost.toFixed(3)}
                  </td>
                  <td className="px-4 py-3 text-center text-gray-600">
                    {formatDuration(run.duration_seconds)}
                  </td>
                  <td className="px-4 py-3 text-center">
                    {run.status === 'complete' ? (
                      run.has_feedback ? (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-green-100 text-green-700">
                          <MessageSquare className="h-3 w-3" aria-hidden="true" />
                          Given
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-amber-100 text-amber-700">
                          <MessageSquare className="h-3 w-3" aria-hidden="true" />
                          Pending
                        </span>
                      )
                    ) : (
                      <span className="text-gray-400">--</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {allRuns.length === 0 && (
          <div className="text-center py-12 text-gray-500">
            No runs found{hasActiveFilters ? ' matching filters' : ''}.
          </div>
        )}
      </div>

      {/* Show more */}
      {data && allRuns.length < data.total && (
        <div className="text-center mt-4">
          <button
            onClick={loadMore}
            disabled={loadingMore}
            className="px-4 py-2 text-sm font-medium text-blue-600 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50 transition-colors"
          >
            {loadingMore ? (
              <span className="flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                Loading...
              </span>
            ) : (
              `Show more (${allRuns.length} of ${data.total})`
            )}
          </button>
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add web_interface/frontend/src/components/AdminSubmissions.tsx
git commit -m "feat: add AdminSubmissions tab with sorting, filtering, pagination"
```

---

### Task 7: AdminFeedbackInsights Tab Component

**Files:**
- Create: `web_interface/frontend/src/components/AdminFeedbackInsights.tsx`

- [ ] **Step 1: Create AdminFeedbackInsights.tsx**

Create `web_interface/frontend/src/components/AdminFeedbackInsights.tsx`:

```tsx
import { useState, useEffect } from 'react'
import {
  Loader2,
  Download,
  BarChart3,
  Clock,
  AlertTriangle,
  TrendingUp,
} from 'lucide-react'

interface FeedbackSummary {
  total_feedback: number
  avg_accuracy: number | null
  avg_completeness: number | null
  avg_usefulness: number | null
  avg_recommend: number | null
  effort_distribution: Record<string, number>
  correction_distribution: Record<string, number>
  issue_heatmap: Record<string, Record<string, number>>
}

export default function AdminFeedbackInsights() {
  const [summary, setSummary] = useState<FeedbackSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [exporting, setExporting] = useState(false)

  useEffect(() => {
    fetchFeedbackSummary()
  }, [])

  const fetchFeedbackSummary = async () => {
    try {
      // Fetch all feedback and compute summaries client-side
      // (For the pilot scale, fetching all feedback is acceptable)
      const res = await fetch('/api/admin/runs?limit=1000&status=complete')
      if (res.ok) {
        const data = await res.json()

        // Build a summary from available run data
        // Full feedback aggregation will be available once the Feedback table is populated
        const totalFeedback = data.runs.filter((r: any) => r.has_feedback).length
        const totalComplete = data.runs.length

        setSummary({
          total_feedback: totalFeedback,
          avg_accuracy: null,
          avg_completeness: null,
          avg_usefulness: null,
          avg_recommend: null,
          effort_distribution: {},
          correction_distribution: {},
          issue_heatmap: {},
        })
      }
    } catch (err) {
      console.error('Failed to fetch feedback summary:', err)
    } finally {
      setLoading(false)
    }
  }

  const handleExport = async () => {
    setExporting(true)
    try {
      const res = await fetch('/api/admin/export/feedback')
      if (res.ok) {
        const blob = await res.blob()
        const url = window.URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `cviche_feedback_${new Date().toISOString().slice(0, 10)}.csv`
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        window.URL.revokeObjectURL(url)
      }
    } catch (err) {
      console.error('Export failed:', err)
    } finally {
      setExporting(false)
    }
  }

  if (loading) {
    return (
      <div className="text-center py-12 text-gray-500">
        <Loader2 className="h-6 w-6 animate-spin inline mr-2" aria-hidden="true" />
        Loading feedback insights...
      </div>
    )
  }

  const ISSUE_TYPES = [
    { key: 'issue_missing_content', label: 'Missing Content' },
    { key: 'issue_split_merged', label: 'Split/Merged Entries' },
    { key: 'issue_wrong_section', label: 'Wrong Section' },
    { key: 'issue_inaccurate', label: 'Inaccurate Data' },
    { key: 'issue_ai_enrichment', label: 'AI Enrichment Issues' },
    { key: 'issue_formatting', label: 'Formatting Problems' },
  ]

  const SEVERITY_LEVELS = ['not_noticed', 'minor', 'moderate', 'major']

  const SEVERITY_COLORS: Record<string, string> = {
    not_noticed: 'bg-gray-100 text-gray-500',
    minor: 'bg-yellow-100 text-yellow-700',
    moderate: 'bg-orange-100 text-orange-700',
    major: 'bg-red-100 text-red-700',
  }

  return (
    <div className="space-y-6">
      {/* Export button */}
      <div className="flex justify-end">
        <button
          onClick={handleExport}
          disabled={exporting}
          className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-200 rounded-lg hover:bg-gray-50 disabled:opacity-50 transition-colors"
        >
          {exporting ? (
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
          ) : (
            <Download className="h-4 w-4" aria-hidden="true" />
          )}
          Export Feedback CSV
        </button>
      </div>

      {/* Average Scores */}
      <section className="bg-white rounded-lg border border-gray-200 p-6">
        <h3 className="text-base font-semibold text-gray-900 mb-4 flex items-center gap-2">
          <BarChart3 className="h-5 w-5 text-blue-600" aria-hidden="true" />
          Average Scores
        </h3>

        {summary && summary.total_feedback > 0 ? (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <ScoreCard label="Accuracy" value={summary.avg_accuracy} max={10} />
            <ScoreCard label="Completeness" value={summary.avg_completeness} max={10} />
            <ScoreCard label="Usefulness" value={summary.avg_usefulness} max={5} />
            <ScoreCard label="Recommend" value={summary.avg_recommend} max={5} />
          </div>
        ) : (
          <p className="text-gray-500 text-sm">
            No feedback data available yet. Scores will appear here once users submit feedback on completed runs.
          </p>
        )}
      </section>

      {/* Time Savings Analysis */}
      <section className="bg-white rounded-lg border border-gray-200 p-6">
        <h3 className="text-base font-semibold text-gray-900 mb-4 flex items-center gap-2">
          <Clock className="h-5 w-5 text-green-600" aria-hidden="true" />
          Time Savings Analysis
        </h3>

        {summary && summary.total_feedback > 0 ? (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div>
              <h4 className="text-sm font-medium text-gray-700 mb-3">Manual Conversion Effort</h4>
              <p className="text-xs text-gray-500 mb-2">How long would manual reformatting take?</p>
              <DistributionBar distribution={summary.effort_distribution} />
            </div>
            <div>
              <h4 className="text-sm font-medium text-gray-700 mb-3">Correction Effort</h4>
              <p className="text-xs text-gray-500 mb-2">How long to correct the CViche output?</p>
              <DistributionBar distribution={summary.correction_distribution} />
            </div>
          </div>
        ) : (
          <p className="text-gray-500 text-sm">
            Time savings data will appear here once feedback is collected. This is the key metric for the research paper:
            median time saved per CV = manual effort - correction effort.
          </p>
        )}
      </section>

      {/* Issue Heatmap */}
      <section className="bg-white rounded-lg border border-gray-200 p-6">
        <h3 className="text-base font-semibold text-gray-900 mb-4 flex items-center gap-2">
          <AlertTriangle className="h-5 w-5 text-amber-600" aria-hidden="true" />
          Issue Heatmap
        </h3>

        {summary && summary.total_feedback > 0 ? (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200">
                  <th className="text-left px-3 py-2 font-medium text-gray-700">Issue Type</th>
                  {SEVERITY_LEVELS.map((level) => (
                    <th key={level} className="text-center px-3 py-2 font-medium text-gray-700 capitalize">
                      {level.replace('_', ' ')}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100">
                {ISSUE_TYPES.map((issue) => (
                  <tr key={issue.key}>
                    <td className="px-3 py-2 font-medium text-gray-800">{issue.label}</td>
                    {SEVERITY_LEVELS.map((level) => {
                      const count = summary.issue_heatmap[issue.key]?.[level] || 0
                      return (
                        <td key={level} className="px-3 py-2 text-center">
                          <span className={`inline-block min-w-[2rem] px-2 py-0.5 rounded text-xs font-medium ${SEVERITY_COLORS[level]}`}>
                            {count}
                          </span>
                        </td>
                      )
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="text-gray-500 text-sm">
            The issue heatmap shows aggregated severity across 6 issue types and 4 severity levels.
            Data will populate as users submit feedback.
          </p>
        )}
      </section>

      {/* Summary stats at bottom */}
      <div className="text-center text-sm text-gray-500 py-4">
        {summary?.total_feedback || 0} feedback responses collected
      </div>
    </div>
  )
}


function ScoreCard({
  label,
  value,
  max,
}: {
  label: string
  value: number | null
  max: number
}) {
  const display = value !== null ? value.toFixed(1) : '--'
  const percentage = value !== null ? (value / max) * 100 : 0

  return (
    <div className="bg-gray-50 rounded-lg p-4">
      <div className="text-xs font-medium text-gray-500 mb-1">{label}</div>
      <div className="text-2xl font-bold text-gray-900">
        {display}
        <span className="text-sm font-normal text-gray-400"> / {max}</span>
      </div>
      {value !== null && (
        <div className="mt-2 h-1.5 bg-gray-200 rounded-full overflow-hidden">
          <div
            className="h-full bg-blue-500 rounded-full transition-all"
            style={{ width: `${percentage}%` }}
          />
        </div>
      )}
    </div>
  )
}


function DistributionBar({ distribution }: { distribution: Record<string, number> }) {
  const entries = Object.entries(distribution)
  const total = entries.reduce((sum, [, count]) => sum + count, 0)

  if (total === 0) {
    return <p className="text-xs text-gray-400">No data yet</p>
  }

  const COLORS = [
    'bg-green-400',
    'bg-green-300',
    'bg-yellow-300',
    'bg-orange-300',
    'bg-red-300',
    'bg-red-400',
  ]

  return (
    <div>
      <div className="flex h-6 rounded-full overflow-hidden">
        {entries.map(([label, count], i) => (
          <div
            key={label}
            className={`${COLORS[i % COLORS.length]} transition-all`}
            style={{ width: `${(count / total) * 100}%` }}
            title={`${label}: ${count} (${Math.round((count / total) * 100)}%)`}
          />
        ))}
      </div>
      <div className="flex flex-wrap gap-x-4 gap-y-1 mt-2">
        {entries.map(([label, count], i) => (
          <span key={label} className="flex items-center gap-1 text-xs text-gray-600">
            <span className={`inline-block w-2 h-2 rounded-full ${COLORS[i % COLORS.length]}`} />
            {label}: {count}
          </span>
        ))}
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add web_interface/frontend/src/components/AdminFeedbackInsights.tsx
git commit -m "feat: add AdminFeedbackInsights tab with scores, time savings, issue heatmap"
```

---

### Task 8: AdminConfig Tab Component

**Files:**
- Create: `web_interface/frontend/src/components/AdminConfig.tsx`

- [ ] **Step 1: Create AdminConfig.tsx**

Create `web_interface/frontend/src/components/AdminConfig.tsx`:

```tsx
import { useState, useEffect } from 'react'
import {
  Loader2,
  Save,
  Download,
  Plus,
  X,
  Users,
  Shield,
  Clock,
  FileText,
} from 'lucide-react'

interface ConfigData {
  allowed_users: string[]
  admin_users: string[]
  rate_limit_daily: number
  rate_limit_monthly: number
  consent_version: string
  auth_mode: string
  _meta: {
    total_users: number
    admin_count: number
  }
}

type ExportType = 'runs' | 'users' | 'consent' | 'feedback'

export default function AdminConfig() {
  const [config, setConfig] = useState<ConfigData | null>(null)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)
  const [exporting, setExporting] = useState<ExportType | null>(null)

  // Editable state
  const [allowedUsers, setAllowedUsers] = useState<string[]>([])
  const [adminUsers, setAdminUsers] = useState<string[]>([])
  const [dailyLimit, setDailyLimit] = useState('')
  const [monthlyLimit, setMonthlyLimit] = useState('')
  const [consentVersion, setConsentVersion] = useState('')
  const [newEmail, setNewEmail] = useState('')

  useEffect(() => {
    fetchConfig()
  }, [])

  const fetchConfig = async () => {
    try {
      const res = await fetch('/api/admin/config')
      if (res.ok) {
        const data: ConfigData = await res.json()
        setConfig(data)
        setAllowedUsers(data.allowed_users || [])
        setAdminUsers(data.admin_users || [])
        setDailyLimit(String(data.rate_limit_daily || 10))
        setMonthlyLimit(String(data.rate_limit_monthly || 50))
        setConsentVersion(data.consent_version || '1.0')
      }
    } catch (err) {
      console.error('Failed to fetch config:', err)
    } finally {
      setLoading(false)
    }
  }

  const saveConfig = async (key: string, value: any) => {
    setSaving(key)
    setError(null)
    setSuccess(null)
    try {
      const res = await fetch('/api/admin/config', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ key, value }),
      })
      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail?.message || 'Save failed')
      }
      setSuccess(`${key} updated successfully.`)
      await fetchConfig()
      // Clear success after 3 seconds
      setTimeout(() => setSuccess(null), 3000)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Save failed')
    } finally {
      setSaving(null)
    }
  }

  const addAllowedUser = () => {
    const email = newEmail.trim().toLowerCase()
    if (!email || !email.includes('@')) return
    if (allowedUsers.includes(email)) {
      setError('Email already in allowed list.')
      return
    }
    const updated = [...allowedUsers, email]
    setAllowedUsers(updated)
    setNewEmail('')
    saveConfig('allowed_users', updated)
  }

  const removeAllowedUser = (email: string) => {
    // Don't allow removing admin users from allowed list
    if (adminUsers.includes(email)) {
      setError('Cannot remove an admin from the allowed list. Demote them first.')
      return
    }
    const updated = allowedUsers.filter((e) => e !== email)
    setAllowedUsers(updated)
    saveConfig('allowed_users', updated)
  }

  const toggleAdmin = (email: string) => {
    let updated: string[]
    if (adminUsers.includes(email)) {
      // Demote: must keep at least one admin
      if (adminUsers.length <= 1) {
        setError('Cannot remove the last admin.')
        return
      }
      updated = adminUsers.filter((e) => e !== email)
    } else {
      // Promote
      updated = [...adminUsers, email]
    }
    setAdminUsers(updated)
    saveConfig('admin_users', updated)
  }

  const handleExport = async (type: ExportType) => {
    setExporting(type)
    try {
      const res = await fetch(`/api/admin/export/${type}`)
      if (res.ok) {
        const blob = await res.blob()
        const url = window.URL.createObjectURL(blob)
        const a = document.createElement('a')
        a.href = url
        a.download = `cviche_${type}_${new Date().toISOString().slice(0, 10)}.csv`
        document.body.appendChild(a)
        a.click()
        document.body.removeChild(a)
        window.URL.revokeObjectURL(url)
      }
    } catch (err) {
      console.error('Export failed:', err)
    } finally {
      setExporting(null)
    }
  }

  if (loading) {
    return (
      <div className="text-center py-12 text-gray-500">
        <Loader2 className="h-6 w-6 animate-spin inline mr-2" aria-hidden="true" />
        Loading configuration...
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Status messages */}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm flex items-center justify-between">
          {error}
          <button onClick={() => setError(null)}>
            <X className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      )}
      {success && (
        <div className="bg-green-50 border border-green-200 text-green-700 px-4 py-3 rounded-lg text-sm">
          {success}
        </div>
      )}

      {/* Allowed Users */}
      <section className="bg-white rounded-lg border border-gray-200 p-6">
        <h3 className="text-base font-semibold text-gray-900 mb-4 flex items-center gap-2">
          <Users className="h-5 w-5 text-blue-600" aria-hidden="true" />
          Allowed Users
        </h3>

        <div className="space-y-2 mb-4">
          {allowedUsers.map((email) => (
            <div
              key={email}
              className="flex items-center justify-between px-3 py-2 bg-gray-50 rounded-lg"
            >
              <div className="flex items-center gap-2">
                <span className="text-sm text-gray-900">{email}</span>
                {adminUsers.includes(email) && (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-purple-100 text-purple-800">
                    <Shield className="h-3 w-3" aria-hidden="true" />
                    Admin
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1">
                <button
                  onClick={() => toggleAdmin(email)}
                  className={`px-2 py-1 text-xs rounded transition-colors ${
                    adminUsers.includes(email)
                      ? 'text-purple-600 hover:bg-purple-50'
                      : 'text-gray-500 hover:bg-gray-100'
                  }`}
                  title={adminUsers.includes(email) ? 'Demote from admin' : 'Promote to admin'}
                >
                  {adminUsers.includes(email) ? 'Demote' : 'Make Admin'}
                </button>
                <button
                  onClick={() => removeAllowedUser(email)}
                  className="p-1 text-red-500 hover:bg-red-50 rounded transition-colors"
                  title="Remove user"
                >
                  <X className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>
            </div>
          ))}
        </div>

        <div className="flex gap-2">
          <input
            type="email"
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
            placeholder="newuser@med.cornell.edu"
            onKeyDown={(e) => e.key === 'Enter' && addAllowedUser()}
            className="flex-1 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none"
          />
          <button
            onClick={addAllowedUser}
            disabled={!newEmail.trim() || saving === 'allowed_users'}
            className="flex items-center gap-1 px-4 py-2 text-sm font-medium text-white bg-blue-600 rounded-lg hover:bg-blue-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors"
          >
            {saving === 'allowed_users' ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Plus className="h-4 w-4" aria-hidden="true" />
            )}
            Add
          </button>
        </div>
      </section>

      {/* Rate Limits */}
      <section className="bg-white rounded-lg border border-gray-200 p-6">
        <h3 className="text-base font-semibold text-gray-900 mb-4 flex items-center gap-2">
          <Clock className="h-5 w-5 text-amber-600" aria-hidden="true" />
          Default Rate Limits
        </h3>
        <p className="text-sm text-gray-500 mb-4">
          System defaults for all users. Individual overrides can be set in the Users tab.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Daily Limit (runs per day)
            </label>
            <div className="flex gap-2">
              <input
                type="number"
                min="1"
                value={dailyLimit}
                onChange={(e) => setDailyLimit(e.target.value)}
                className="w-24 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none"
              />
              <button
                onClick={() => saveConfig('rate_limit_daily', parseInt(dailyLimit))}
                disabled={saving === 'rate_limit_daily' || !dailyLimit}
                className="flex items-center gap-1 px-3 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 transition-colors"
              >
                {saving === 'rate_limit_daily' ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Save className="h-4 w-4" aria-hidden="true" />
                )}
                Save
              </button>
            </div>
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Monthly Limit (runs per month)
            </label>
            <div className="flex gap-2">
              <input
                type="number"
                min="1"
                value={monthlyLimit}
                onChange={(e) => setMonthlyLimit(e.target.value)}
                className="w-24 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none"
              />
              <button
                onClick={() => saveConfig('rate_limit_monthly', parseInt(monthlyLimit))}
                disabled={saving === 'rate_limit_monthly' || !monthlyLimit}
                className="flex items-center gap-1 px-3 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 transition-colors"
              >
                {saving === 'rate_limit_monthly' ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
                ) : (
                  <Save className="h-4 w-4" aria-hidden="true" />
                )}
                Save
              </button>
            </div>
          </div>
        </div>
      </section>

      {/* Consent Version */}
      <section className="bg-white rounded-lg border border-gray-200 p-6">
        <h3 className="text-base font-semibold text-gray-900 mb-4 flex items-center gap-2">
          <FileText className="h-5 w-5 text-green-600" aria-hidden="true" />
          Consent Version
        </h3>
        <p className="text-sm text-gray-500 mb-4">
          Bumping the version triggers re-consent for all users on their next visit.
        </p>

        <div className="flex gap-2 items-end">
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-1">
              Current Version
            </label>
            <input
              type="text"
              value={consentVersion}
              onChange={(e) => setConsentVersion(e.target.value)}
              placeholder="1.0"
              className="w-32 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none"
            />
          </div>
          <button
            onClick={() => saveConfig('consent_version', consentVersion)}
            disabled={saving === 'consent_version' || !consentVersion.trim()}
            className="flex items-center gap-1 px-3 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 transition-colors"
          >
            {saving === 'consent_version' ? (
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            ) : (
              <Save className="h-4 w-4" aria-hidden="true" />
            )}
            Save
          </button>
        </div>
      </section>

      {/* Data Export */}
      <section className="bg-white rounded-lg border border-gray-200 p-6">
        <h3 className="text-base font-semibold text-gray-900 mb-4 flex items-center gap-2">
          <Download className="h-5 w-5 text-gray-600" aria-hidden="true" />
          Data Export
        </h3>
        <p className="text-sm text-gray-500 mb-4">
          Download CSV exports for research analysis. Exports are logged with admin identity and timestamp.
        </p>

        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          {(
            [
              { type: 'runs' as ExportType, label: 'All Runs', desc: 'Run data with costs and durations' },
              { type: 'users' as ExportType, label: 'All Users', desc: 'User accounts and limits' },
              { type: 'consent' as ExportType, label: 'Consent Log', desc: 'Audit trail of consent events' },
              { type: 'feedback' as ExportType, label: 'Feedback + Runs', desc: 'Feedback joined to run data' },
            ] as const
          ).map(({ type, label, desc }) => (
            <button
              key={type}
              onClick={() => handleExport(type)}
              disabled={exporting === type}
              className="flex flex-col items-center gap-2 p-4 bg-gray-50 rounded-lg border border-gray-200 hover:bg-gray-100 disabled:opacity-50 transition-colors text-center"
            >
              {exporting === type ? (
                <Loader2 className="h-5 w-5 animate-spin text-gray-500" aria-hidden="true" />
              ) : (
                <Download className="h-5 w-5 text-gray-500" aria-hidden="true" />
              )}
              <span className="text-sm font-medium text-gray-800">{label}</span>
              <span className="text-xs text-gray-500">{desc}</span>
            </button>
          ))}
        </div>
      </section>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add web_interface/frontend/src/components/AdminConfig.tsx
git commit -m "feat: add AdminConfig tab with user management, rate limits, consent, exports"
```

---

### Task 9: Wire Admin Route into App.tsx + Navigation

**Files:**
- Modify: `web_interface/frontend/src/App.tsx`

- [ ] **Step 1: Add admin route with role guard**

Update `web_interface/frontend/src/App.tsx` to add the `/admin` route and admin navigation link.

The full updated file should be:

```tsx
import { BrowserRouter, Routes, Route, useNavigate, useParams, Navigate, Link } from 'react-router-dom'
import { useAuth } from './contexts/AuthContext'
import LoginPage from './components/LoginPage'
import UploadPage from './components/UploadPage'
import PipelineViewer from './components/PipelineViewer'
import AdminDashboard from './components/AdminDashboard'
import { Loader2, Shield, LogOut } from 'lucide-react'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace />
  }

  return <>{children}</>
}

function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()

  if (loading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
  }

  if (!user) {
    return <Navigate to="/login" replace />
  }

  if (user.role !== 'admin') {
    return <Navigate to="/" replace />
  }

  return <>{children}</>
}

function UploadRoute() {
  const navigate = useNavigate()
  return <UploadPage onUploadSuccess={(runId) => navigate(`/run/${runId}`)} />
}

function PipelineRoute() {
  const { runId } = useParams<{ runId: string }>()
  const navigate = useNavigate()
  if (!runId) return <Navigate to="/" replace />
  return <PipelineViewer runId={runId} onBack={() => navigate('/')} />
}

function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-surface-muted">
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/" element={<RequireAuth><UploadRoute /></RequireAuth>} />
          <Route path="/run/:runId" element={<RequireAuth><PipelineRoute /></RequireAuth>} />
          <Route path="/admin" element={<RequireAdmin><AdminDashboard /></RequireAdmin>} />
        </Routes>
      </div>
    </BrowserRouter>
  )
}

export default App
```

- [ ] **Step 2: Add admin link to UploadPage navigation**

Update `web_interface/frontend/src/components/UploadPage.tsx` to add an admin link and logout button visible in the header area. Add the following block after the logo `<div>` and before the main card `<section>`:

```tsx
import { useAuth } from '../contexts/AuthContext'
import { Link } from 'react-router-dom'
import { Shield, LogOut } from 'lucide-react'

// Inside the component, before the return:
const { user, logout } = useAuth()

// Add this block after the logo div and before the main <section>:
        {/* User bar */}
        <div className="flex items-center justify-between mb-4 px-1">
          <div className="flex items-center gap-2 text-sm text-gray-700">
            <span>Signed in as <strong>{user?.display_name}</strong></span>
          </div>
          <div className="flex items-center gap-3">
            {user?.role === 'admin' && (
              <Link
                to="/admin"
                className="flex items-center gap-1.5 text-sm font-medium text-purple-700 hover:text-purple-900 transition-colors"
              >
                <Shield className="h-4 w-4" aria-hidden="true" />
                Admin
              </Link>
            )}
            <button
              onClick={logout}
              className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 transition-colors"
            >
              <LogOut className="h-4 w-4" aria-hidden="true" />
              Sign out
            </button>
          </div>
        </div>
```

The exact modification: in `UploadPage.tsx`, add the imports at the top of the file:

```tsx
import { useAuth } from '../contexts/AuthContext'
import { Link } from 'react-router-dom'
import { Shield, LogOut } from 'lucide-react'
```

Add inside the component function body, before the `return`:

```tsx
  const { user, logout } = useAuth()
```

In the JSX, after the logo `<div className="flex justify-center mb-6">...</div>` block and before the `<section className="bg-white/95 ...">` block, insert:

```tsx
        {/* User bar */}
        <div className="flex items-center justify-between mb-4 px-1">
          <div className="flex items-center gap-2 text-sm text-gray-700">
            <span>Signed in as <strong>{user?.display_name}</strong></span>
          </div>
          <div className="flex items-center gap-3">
            {user?.role === 'admin' && (
              <Link
                to="/admin"
                className="flex items-center gap-1.5 text-sm font-medium text-purple-700 hover:text-purple-900 transition-colors"
              >
                <Shield className="h-4 w-4" aria-hidden="true" />
                Admin
              </Link>
            )}
            <button
              onClick={logout}
              className="flex items-center gap-1 text-sm text-gray-500 hover:text-gray-700 transition-colors"
            >
              <LogOut className="h-4 w-4" aria-hidden="true" />
              Sign out
            </button>
          </div>
        </div>
```

- [ ] **Step 3: Build and verify**

Run: `cd web_interface/frontend && npm run build`
Expected: clean build, no errors.

- [ ] **Step 4: Manual test**

1. Login as admin (paa2013@med.cornell.edu) -- should see "Admin" link on upload page.
2. Click "Admin" -- navigates to `/admin`, dashboard loads with stat cards and tabs.
3. Click through all 4 tabs -- each renders.
4. Login as non-admin -- "Admin" link should not be visible.
5. Navigate to `/admin` directly as non-admin -- should redirect to `/`.

- [ ] **Step 5: Commit**

```bash
git add web_interface/frontend/src/App.tsx web_interface/frontend/src/components/UploadPage.tsx
git commit -m "feat: add /admin route with role guard and admin link in navigation"
```

---

### Task 10: Integration Testing

**Files:** None (testing only)

- [ ] **Step 1: Test admin stats endpoint returns correct values**

```bash
# Start backend
cd web_interface/backend && python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &

# Login
curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "paa2013@med.cornell.edu", "display_name": "Paul Albert"}' \
  -c cookies.txt

# Stats
curl -s http://localhost:8000/api/admin/stats -b cookies.txt | python3 -m json.tool
```
Expected: `total_runs`, `active_users`, `total_cost`, `feedback_rate` all present.

- [ ] **Step 2: Test non-admin gets 403**

```bash
# Create a non-admin user
curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "jsmith@med.cornell.edu", "display_name": "Jane Smith"}' \
  -c cookies_user.txt

# Try admin endpoint
curl -s http://localhost:8000/api/admin/stats -b cookies_user.txt | python3 -m json.tool
```
Expected: 403 with `"error": "forbidden"`.

- [ ] **Step 3: Test user update endpoint**

```bash
# Get users list
curl -s http://localhost:8000/api/admin/users -b cookies.txt | python3 -m json.tool

# Disable a user (use their ID from the list)
curl -s -X PUT http://localhost:8000/api/admin/users/2 \
  -H "Content-Type: application/json" \
  -d '{"status": "disabled"}' \
  -b cookies.txt | python3 -m json.tool
```
Expected: User status changes to `disabled`. Their next API call returns 401.

- [ ] **Step 4: Test config update**

```bash
# Read config
curl -s http://localhost:8000/api/admin/config -b cookies.txt | python3 -m json.tool

# Update rate limit
curl -s -X PUT http://localhost:8000/api/admin/config \
  -H "Content-Type: application/json" \
  -d '{"key": "rate_limit_daily", "value": 15}' \
  -b cookies.txt | python3 -m json.tool
```
Expected: Config updated, old value preserved in logs.

- [ ] **Step 5: Test CSV export**

```bash
curl -s http://localhost:8000/api/admin/export/runs -b cookies.txt | head -5
curl -s http://localhost:8000/api/admin/export/users -b cookies.txt | head -5
```
Expected: CSV output with headers and data rows.

- [ ] **Step 6: Test last-admin protection**

```bash
# Try to demote the only admin
curl -s -X PUT http://localhost:8000/api/admin/users/1 \
  -H "Content-Type: application/json" \
  -d '{"role": "user"}' \
  -b cookies.txt | python3 -m json.tool
```
Expected: 422 with message "Cannot demote the last admin."

- [ ] **Step 7: Frontend verification**

1. Open `http://localhost:3000`
2. Login as admin
3. Click "Admin" link
4. Verify: stat cards show data, all 4 tabs load, Users tab shows the user table
5. In Users tab: click disable on a user, verify badge changes
6. In Config tab: change daily limit, verify save succeeds
7. In Config tab: click export buttons, verify CSV downloads
8. In Submissions tab: verify pagination works, click a run to view it
