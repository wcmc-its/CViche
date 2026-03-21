# Plan 1: Auth + Users + Database Migration

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Migrate from SQLite to MariaDB, add User model with auth middleware, session management, login page, and access control — the foundation all other plans depend on.

**Architecture:** MariaDB replaces SQLite via SQLAlchemy connection string change. Alembic manages migrations. Auth uses `itsdangerous` signed cookies with per-request DB status check. Frontend gets a login page and auth context that gates all routes.

**Tech Stack:** MariaDB, SQLAlchemy, Alembic, itsdangerous, FastAPI dependencies, React Router

**Spec:** `docs/superpowers/specs/2026-03-20-production-readiness-design.md` — sections 1, 2 (User + SystemConfig tables), and Implementation Notes.

---

### Task 1: MariaDB Connection + Dependencies

**Files:**
- Modify: `web_interface/backend/requirements.txt`
- Modify: `web_interface/backend/app/database.py`
- Create: `web_interface/backend/.env.example` (update)

- [ ] **Step 1: Add MariaDB driver to requirements**

Add `pymysql` to `web_interface/backend/requirements.txt`:
```
pymysql
itsdangerous
pyyaml
```

- [ ] **Step 2: Install dependencies**

Run: `cd web_interface/backend && pip install pymysql itsdangerous pyyaml`

- [ ] **Step 3: Update database.py for MariaDB**

Replace `web_interface/backend/app/database.py`:

```python
"""Database configuration and session management."""
import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

DATABASE_URL = os.environ.get(
    "CVICHE_DATABASE_URL",
    "mysql+pymysql://root@localhost:3306/cviche"
)

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Dependency for getting database sessions."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Initialize database tables."""
    from app.models import Run, Step, Log, LLMUsage, User, Consent, SystemConfig
    Base.metadata.create_all(bind=engine)
```

- [ ] **Step 4: Create MariaDB database locally**

Run: `mysql -u root -e "CREATE DATABASE IF NOT EXISTS cviche CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"`

- [ ] **Step 5: Verify connection**

Run: `cd web_interface/backend && python3 -c "from app.database import engine; print(engine.url)"`
Expected: prints the MariaDB connection URL

- [ ] **Step 6: Commit**

```bash
git add web_interface/backend/requirements.txt web_interface/backend/app/database.py
git commit -m "feat: migrate database connection from SQLite to MariaDB"
```

---

### Task 2: User + SystemConfig Models

**Files:**
- Modify: `web_interface/backend/app/models.py`

- [ ] **Step 1: Add User and SystemConfig models**

Add to `web_interface/backend/app/models.py`:

```python
class User(Base):
    """User accounts."""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    display_name = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="user")  # "user" or "admin"
    status = Column(String(20), nullable=False, default="active")  # "active" or "disabled"
    daily_limit = Column(Integer, nullable=True)  # null = use system default
    monthly_limit = Column(Integer, nullable=True)
    default_submission_type = Column(String(50), nullable=True)  # "own_cv" or "authorized_admin"
    consent_version = Column(String(50), nullable=True)
    consent_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    last_active_at = Column(DateTime, server_default=func.now())


class SystemConfig(Base):
    """Admin-editable configuration stored in DB."""
    __tablename__ = "system_config"

    key = Column(String(255), primary_key=True)
    value = Column(Text, nullable=False)  # JSON-encoded
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    updated_by = Column(Integer, ForeignKey("users.id"), nullable=True)
```

- [ ] **Step 2: Add user_id and submission_type to Run**

Add columns to the existing `Run` class in `models.py`:

```python
    # Auth fields (added in production readiness migration)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    submission_type = Column(String(50), nullable=True)  # "own_cv" or "authorized_admin"
    show_track_changes = Column(Integer, default=1)  # Boolean as int for MariaDB compat
    show_pipeline_comments = Column(Integer, default=0)
```

- [ ] **Step 3: Verify models load**

Run: `cd web_interface/backend && python3 -c "from app.models import User, SystemConfig; print('OK')"`
Expected: `OK`

- [ ] **Step 4: Commit**

```bash
git add web_interface/backend/app/models.py
git commit -m "feat: add User and SystemConfig models, extend Run table"
```

---

### Task 3: Alembic Setup + Initial Migration

**Files:**
- Create: `web_interface/backend/alembic.ini`
- Create: `web_interface/backend/alembic/` directory
- Create: `web_interface/backend/alembic/env.py`

- [ ] **Step 1: Initialize Alembic**

Run: `cd web_interface/backend && alembic init alembic`

- [ ] **Step 2: Configure alembic.ini**

In `web_interface/backend/alembic.ini`, set:
```ini
sqlalchemy.url = mysql+pymysql://root@localhost:3306/cviche
```

- [ ] **Step 3: Update alembic/env.py to use app models**

In `web_interface/backend/alembic/env.py`, update the `target_metadata` line:

```python
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import Base
from app.models import Run, Step, Log, LLMUsage, User, SystemConfig, Consent
target_metadata = Base.metadata
```

- [ ] **Step 4: Generate initial migration**

Run: `cd web_interface/backend && alembic revision --autogenerate -m "initial schema with auth tables"`

- [ ] **Step 5: Apply migration**

Run: `cd web_interface/backend && alembic upgrade head`

- [ ] **Step 6: Verify tables exist**

Run: `mysql -u root cviche -e "SHOW TABLES;"`
Expected: tables including `users`, `system_config`, `runs`, `steps`, `logs`, `llm_usage`

- [ ] **Step 7: Commit**

```bash
git add web_interface/backend/alembic.ini web_interface/backend/alembic/
git commit -m "feat: initialize Alembic with initial schema migration"
```

---

### Task 4: Auth Config File + Config Seeding

**Files:**
- Create: `web_interface/backend/auth_config.yaml`
- Create: `web_interface/backend/app/config_loader.py`

- [ ] **Step 1: Create auth_config.yaml**

Create `web_interface/backend/auth_config.yaml`:

```yaml
auth:
  mode: simple  # "simple" or "saml"

allowed_users:
  - paa2013@med.cornell.edu

admin_users:
  - paa2013@med.cornell.edu

rate_limits:
  daily: 10
  monthly: 50

consent:
  version: "1.0"
```

- [ ] **Step 2: Create config loader with DB seeding**

Create `web_interface/backend/app/config_loader.py`:

```python
"""Load auth config from YAML and seed SystemConfig DB table."""
import json
import yaml
from pathlib import Path
from sqlalchemy.orm import Session
from app.models import SystemConfig


CONFIG_PATH = Path(__file__).parent.parent / "auth_config.yaml"


def load_yaml_config() -> dict:
    """Load auth_config.yaml from disk."""
    with open(CONFIG_PATH, "r") as f:
        return yaml.safe_load(f)


def seed_system_config(db: Session) -> None:
    """Seed SystemConfig table from YAML. Only inserts keys absent from DB."""
    config = load_yaml_config()

    defaults = {
        "auth_mode": config.get("auth", {}).get("mode", "simple"),
        "allowed_users": json.dumps(config.get("allowed_users", [])),
        "admin_users": json.dumps(config.get("admin_users", [])),
        "rate_limit_daily": json.dumps(config.get("rate_limits", {}).get("daily", 10)),
        "rate_limit_monthly": json.dumps(config.get("rate_limits", {}).get("monthly", 50)),
        "consent_version": json.dumps(config.get("consent", {}).get("version", "1.0")),
    }

    for key, value in defaults.items():
        existing = db.query(SystemConfig).filter(SystemConfig.key == key).first()
        if not existing:
            db.add(SystemConfig(key=key, value=value))

    db.commit()


def get_config_value(db: Session, key: str) -> any:
    """Get a config value from DB, falling back to YAML defaults."""
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        return json.loads(row.value)
    return None
```

- [ ] **Step 3: Call seed on startup in main.py**

Add to the `lifespan` function in `web_interface/backend/app/main.py`, after `init_db()`:

```python
    from app.config_loader import seed_system_config
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        seed_system_config(db)
        print("✅ System config seeded")
    finally:
        db.close()
```

- [ ] **Step 4: Verify seeding**

Run: `cd web_interface/backend && python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8000 &`
Then: `mysql -u root cviche -e "SELECT * FROM system_config;"`
Expected: rows for `auth_mode`, `allowed_users`, `admin_users`, `rate_limit_daily`, `rate_limit_monthly`, `consent_version`

- [ ] **Step 5: Commit**

```bash
git add web_interface/backend/auth_config.yaml web_interface/backend/app/config_loader.py web_interface/backend/app/main.py
git commit -m "feat: add auth config YAML and DB seeding"
```

---

### Task 5: Session Management + Auth Middleware

**Files:**
- Create: `web_interface/backend/app/auth.py`

- [ ] **Step 1: Create auth module**

Create `web_interface/backend/app/auth.py`:

```python
"""Authentication middleware using itsdangerous signed cookies."""
import os
import time
import json
import secrets
import logging
from datetime import datetime
from functools import lru_cache

from fastapi import Request, HTTPException, Depends
from itsdangerous import URLSafeTimedSerializer, BadSignature, SignatureExpired
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.config_loader import get_config_value

logger = logging.getLogger(__name__)

SESSION_TTL = 7 * 24 * 3600  # 7 days in seconds
COOKIE_NAME = "cviche_session"

# Session secret: from env or random (dev only)
_secret = os.environ.get("CVICHE_SESSION_SECRET")
if not _secret:
    _secret = secrets.token_hex(32)
    logger.warning(
        "CVICHE_SESSION_SECRET not set — using random key. "
        "Sessions will not survive server restarts."
    )

_serializer = URLSafeTimedSerializer(_secret)
_secure_cookies = os.environ.get("CVICHE_SECURE_COOKIES", "true").lower() == "true"


def create_session_cookie(user: User) -> str:
    """Create a signed session cookie value."""
    payload = {
        "user_id": user.id,
        "email": user.email,
        "role": user.role,
        "issued_at": int(time.time()),
    }
    return _serializer.dumps(payload)


def decode_session_cookie(cookie_value: str) -> dict | None:
    """Decode and verify a session cookie. Returns None if invalid/expired."""
    try:
        return _serializer.loads(cookie_value, max_age=SESSION_TTL)
    except (BadSignature, SignatureExpired):
        return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """FastAPI dependency: extract and validate user from session cookie.

    Performs a DB check on every request to verify:
    - User still exists
    - User status is still 'active'
    - User role matches (updates cookie if role changed)
    """
    cookie = request.cookies.get(COOKIE_NAME)
    if not cookie:
        raise HTTPException(
            status_code=401,
            detail={"error": "auth_required", "message": "Authentication required. Please log in."}
        )

    payload = decode_session_cookie(cookie)
    if not payload:
        raise HTTPException(
            status_code=401,
            detail={"error": "auth_required", "message": "Session expired. Please log in again."}
        )

    user = db.query(User).filter(User.id == payload["user_id"]).first()
    if not user:
        raise HTTPException(
            status_code=401,
            detail={"error": "auth_required", "message": "User not found. Please log in again."}
        )

    if user.status != "active":
        raise HTTPException(
            status_code=401,
            detail={"error": "account_disabled", "message": "Your account has been disabled. Contact an administrator."}
        )

    # Update last_active_at (debounced: only if >60s since last update)
    now = datetime.now()
    if not user.last_active_at or (now - user.last_active_at).total_seconds() > 60:
        user.last_active_at = now
        db.commit()

    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """FastAPI dependency: require admin role."""
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"error": "forbidden", "message": "Admin access required."}
        )
    return user


def get_cookie_settings() -> dict:
    """Return cookie settings for Set-Cookie."""
    return {
        "key": COOKIE_NAME,
        "httponly": True,
        "samesite": "lax",
        "secure": _secure_cookies,
        "max_age": SESSION_TTL,
    }
```

- [ ] **Step 2: Verify module loads**

Run: `cd web_interface/backend && python3 -c "from app.auth import create_session_cookie, get_current_user; print('OK')"`
Expected: `OK` (plus the warning about random session secret)

- [ ] **Step 3: Commit**

```bash
git add web_interface/backend/app/auth.py
git commit -m "feat: add session management and auth middleware"
```

---

### Task 6: Login API Endpoint

**Files:**
- Create: `web_interface/backend/app/api/auth_routes.py`
- Modify: `web_interface/backend/app/main.py`
- Modify: `web_interface/backend/app/schemas.py`

- [ ] **Step 1: Add auth schemas**

Add to `web_interface/backend/app/schemas.py`:

```python
class LoginRequest(BaseModel):
    email: str
    display_name: str


class LoginResponse(BaseModel):
    user_id: int
    email: str
    display_name: str
    role: str

    class Config:
        from_attributes = True


class MeResponse(BaseModel):
    user_id: int
    email: str
    display_name: str
    role: str
    consent_version: Optional[str] = None
    default_submission_type: Optional[str] = None

    class Config:
        from_attributes = True
```

- [ ] **Step 2: Create auth routes**

Create `web_interface/backend/app/api/auth_routes.py`:

```python
"""Authentication API endpoints."""
import json
import time
import logging
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.schemas import LoginRequest, LoginResponse, MeResponse
from app.auth import (
    create_session_cookie,
    get_current_user,
    get_cookie_settings,
    COOKIE_NAME,
)
from app.config_loader import get_config_value

logger = logging.getLogger(__name__)
router = APIRouter()

# Simple in-memory rate limiter for login: {ip: [timestamps]}
_login_attempts: dict[str, list[float]] = defaultdict(list)
LOGIN_RATE_LIMIT = 10  # attempts per IP
LOGIN_RATE_WINDOW = 60  # per 60 seconds


def _check_login_rate_limit(ip: str) -> None:
    """Raise 429 if login rate limit exceeded."""
    now = time.time()
    attempts = _login_attempts[ip]
    # Prune old attempts
    _login_attempts[ip] = [t for t in attempts if now - t < LOGIN_RATE_WINDOW]
    if len(_login_attempts[ip]) >= LOGIN_RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail={
                "error": "login_rate_limited",
                "message": f"Too many login attempts. Try again in {LOGIN_RATE_WINDOW} seconds.",
            },
        )
    _login_attempts[ip].append(now)


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    """Email-based login. Checks email against allowed users list."""
    ip = request.client.host if request.client else "unknown"
    _check_login_rate_limit(ip)

    email = body.email.strip().lower()
    allowed = get_config_value(db, "allowed_users") or []
    admin_list = get_config_value(db, "admin_users") or []

    if email not in [e.lower() for e in allowed]:
        logger.info("user_login_denied", extra={"email": email, "ip_address": ip})
        raise HTTPException(
            status_code=403,
            detail={
                "error": "forbidden",
                "message": "Access denied. Your email is not on the allowed list. Contact an administrator.",
            },
        )

    # Create or update user
    user = db.query(User).filter(User.email == email).first()
    role = "admin" if email in [e.lower() for e in admin_list] else "user"

    if user:
        user.display_name = body.display_name.strip()
        user.role = role
    else:
        user = User(
            email=email,
            display_name=body.display_name.strip(),
            role=role,
            status="active",
        )
        db.add(user)

    db.commit()
    db.refresh(user)

    logger.info("user_login", extra={"email": email, "ip_address": ip, "user_id": user.id})

    # Set session cookie
    cookie_value = create_session_cookie(user)
    response = JSONResponse(
        content={
            "user_id": user.id,
            "email": user.email,
            "display_name": user.display_name,
            "role": user.role,
        }
    )
    response.set_cookie(value=cookie_value, **get_cookie_settings())
    return response


@router.post("/auth/logout")
async def logout():
    """Clear session cookie."""
    response = JSONResponse(content={"message": "Logged out"})
    response.delete_cookie(COOKIE_NAME)
    return response


@router.get("/auth/me", response_model=MeResponse)
async def get_me(user: User = Depends(get_current_user)):
    """Get current user info."""
    return MeResponse(
        user_id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        consent_version=user.consent_version,
        default_submission_type=user.default_submission_type,
    )
```

- [ ] **Step 3: Register auth routes in main.py**

Add to `web_interface/backend/app/main.py`:

```python
from app.api import upload, runs, steps, websocket, auth_routes

# ... in the router registration section:
app.include_router(auth_routes.router, prefix="/api", tags=["auth"])
```

- [ ] **Step 4: Verify login endpoint**

Run:
```bash
curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "paa2013@med.cornell.edu", "display_name": "Paul Albert"}' \
  -c cookies.txt | python3 -m json.tool
```
Expected: JSON with `user_id`, `email`, `display_name`, `role: "admin"` and a Set-Cookie header.

- [ ] **Step 5: Verify /auth/me with cookie**

Run:
```bash
curl -s http://localhost:8000/api/auth/me -b cookies.txt | python3 -m json.tool
```
Expected: same user info JSON.

- [ ] **Step 6: Verify unauthorized access**

Run:
```bash
curl -s http://localhost:8000/api/auth/me | python3 -m json.tool
```
Expected: 401 with `"error": "auth_required"`.

- [ ] **Step 7: Commit**

```bash
git add web_interface/backend/app/api/auth_routes.py web_interface/backend/app/schemas.py web_interface/backend/app/main.py
git commit -m "feat: add login, logout, and /auth/me API endpoints"
```

---

### Task 7: CORS + CSRF Configuration

**Files:**
- Modify: `web_interface/backend/app/main.py`

- [ ] **Step 1: Update CORS to use environment variable**

In `web_interface/backend/app/main.py`, replace the hardcoded CORS origins:

```python
import os

_allowed_origins = os.environ.get(
    "CVICHE_ALLOWED_ORIGINS",
    "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173"
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
```

- [ ] **Step 2: Add Origin/Referer CSRF check middleware**

Add to `web_interface/backend/app/main.py`:

```python
from starlette.middleware.base import BaseHTTPMiddleware

class CSRFMiddleware(BaseHTTPMiddleware):
    """Check Origin/Referer header on state-changing requests."""

    async def dispatch(self, request: Request, call_next):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            origin = request.headers.get("origin") or ""
            referer = request.headers.get("referer") or ""
            # Allow requests with no origin (same-origin, curl, etc.)
            if origin and not any(origin.startswith(o) for o in _allowed_origins):
                if referer and not any(referer.startswith(o) for o in _allowed_origins):
                    return JSONResponse(
                        status_code=403,
                        content={"error": "forbidden", "message": "Cross-origin request rejected."}
                    )
        return await call_next(request)

app.add_middleware(CSRFMiddleware)
```

- [ ] **Step 3: Commit**

```bash
git add web_interface/backend/app/main.py
git commit -m "feat: configurable CORS origins and CSRF middleware"
```

---

### Task 8: Protect Existing API Endpoints

**Files:**
- Modify: `web_interface/backend/app/api/upload.py`
- Modify: `web_interface/backend/app/api/runs.py`
- Modify: `web_interface/backend/app/api/steps.py`

- [ ] **Step 1: Add auth dependency to upload endpoints**

In `web_interface/backend/app/api/upload.py`, add:

```python
from app.auth import get_current_user
from app.models import User
```

Update `upload_cv` and `estimate_processing` signatures to include:
```python
async def upload_cv(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
```

Set `user_id` on the Run record:
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

- [ ] **Step 2: Add auth + ownership checks to runs.py**

In `web_interface/backend/app/api/runs.py`:

```python
from app.auth import get_current_user
from app.models import User
```

Update `get_run_status` to check ownership:
```python
async def get_run_status(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    run = db.query(Run).filter(Run.id == run_id).first()
    if not run:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "Run not found"})
    if run.user_id and run.user_id != current_user.id and current_user.role != "admin":
        raise HTTPException(status_code=403, detail={"error": "forbidden", "message": "Access denied"})
    # ... rest of function
```

Apply the same ownership check pattern to `start_run`, `cancel_run`, `pause_run`, `retry_step`, `get_data_quality`.

Update `list_runs` to be user-scoped with pagination:
```python
@router.get("/runs")
async def list_runs(
    offset: int = 0,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    runs = (
        db.query(Run)
        .filter(Run.user_id == current_user.id)
        .order_by(Run.started_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    # ... format response with RunSummary
```

- [ ] **Step 3: Add auth + ownership to steps.py**

Same pattern: add `current_user: User = Depends(get_current_user)` to all endpoints, check run ownership before returning data.

- [ ] **Step 4: Test authenticated flow**

```bash
# Login first
curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "paa2013@med.cornell.edu", "display_name": "Paul Albert"}' \
  -c cookies.txt

# Access runs with cookie
curl -s http://localhost:8000/api/runs -b cookies.txt | python3 -m json.tool

# Access without cookie should fail
curl -s http://localhost:8000/api/runs | python3 -m json.tool
```

- [ ] **Step 5: Commit**

```bash
git add web_interface/backend/app/api/upload.py web_interface/backend/app/api/runs.py web_interface/backend/app/api/steps.py
git commit -m "feat: add auth middleware to all API endpoints with ownership checks"
```

---

### Task 9: Frontend Auth Context

**Files:**
- Create: `web_interface/frontend/src/contexts/AuthContext.tsx`

- [ ] **Step 1: Create auth context**

Create `web_interface/frontend/src/contexts/AuthContext.tsx`:

```tsx
import { createContext, useContext, useState, useEffect, ReactNode } from 'react'

interface User {
  user_id: number
  email: string
  display_name: string
  role: string
  consent_version: string | null
  default_submission_type: string | null
}

interface AuthContextType {
  user: User | null
  loading: boolean
  login: (email: string, displayName: string) => Promise<void>
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)

  const refreshUser = async () => {
    try {
      const res = await fetch('/api/auth/me')
      if (res.ok) {
        setUser(await res.json())
      } else {
        setUser(null)
      }
    } catch {
      setUser(null)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refreshUser()
  }, [])

  const login = async (email: string, displayName: string) => {
    const res = await fetch('/api/auth/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, display_name: displayName }),
    })
    if (!res.ok) {
      const err = await res.json()
      throw new Error(err.detail?.message || err.detail || 'Login failed')
    }
    await refreshUser()
  }

  const logout = async () => {
    await fetch('/api/auth/logout', { method: 'POST' })
    setUser(null)
  }

  return (
    <AuthContext.Provider value={{ user, loading, login, logout, refreshUser }}>
      {children}
    </AuthContext.Provider>
  )
}

export function useAuth() {
  const ctx = useContext(AuthContext)
  if (!ctx) throw new Error('useAuth must be used within AuthProvider')
  return ctx
}
```

- [ ] **Step 2: Commit**

```bash
git add web_interface/frontend/src/contexts/AuthContext.tsx
git commit -m "feat: add React auth context with login/logout/session check"
```

---

### Task 10: Login Page Component

**Files:**
- Create: `web_interface/frontend/src/components/LoginPage.tsx`

- [ ] **Step 1: Create login page**

Create `web_interface/frontend/src/components/LoginPage.tsx`:

```tsx
import { useState } from 'react'
import { LogIn, Loader2 } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import ErrorBanner from './ErrorBanner'

export default function LoginPage() {
  const { login } = useAuth()
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!email.trim() || !displayName.trim()) return

    setLoading(true)
    setError(null)
    try {
      await login(email.trim(), displayName.trim())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setLoading(false)
    }
  }

  return (
    <main
      className="flex items-center justify-center min-h-screen p-4"
      style={{
        backgroundImage: 'url(/headerbg.png)',
        backgroundSize: 'cover',
        backgroundPosition: 'center',
        backgroundRepeat: 'no-repeat',
      }}
    >
      <div className="w-full max-w-md">
        <div className="flex justify-center mb-6">
          <img src="/header-logo.png" alt="CViche" className="h-16 object-contain" />
        </div>

        <section className="bg-white/95 backdrop-blur-sm rounded-lg shadow-lg p-6 md:p-8">
          <h1 className="text-xl font-bold text-gray-900 mb-2">Sign In</h1>
          <p className="text-gray-600 mb-6">Enter your WCM email to access CViche.</p>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label htmlFor="display-name" className="block text-sm font-semibold text-gray-900 mb-1">
                Full Name
              </label>
              <input
                id="display-name"
                type="text"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                placeholder="Jane Smith"
                required
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none"
              />
            </div>
            <div>
              <label htmlFor="email" className="block text-sm font-semibold text-gray-900 mb-1">
                WCM Email
              </label>
              <input
                id="email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="abc1234@med.cornell.edu"
                required
                className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-blue-500 focus:outline-none"
              />
            </div>

            {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

            <button
              type="submit"
              disabled={loading || !email.trim() || !displayName.trim()}
              className="w-full bg-primary-600 text-white py-3 px-4 rounded-lg font-semibold hover:bg-primary-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors focus:ring-2 focus:ring-primary-500 focus:outline-none flex items-center justify-center gap-2"
            >
              {loading ? (
                <>
                  <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
                  Signing in...
                </>
              ) : (
                <>
                  <LogIn className="h-5 w-5" aria-hidden="true" />
                  Sign In
                </>
              )}
            </button>
          </form>
        </section>
      </div>
    </main>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add web_interface/frontend/src/components/LoginPage.tsx
git commit -m "feat: add login page component"
```

---

### Task 11: Wire Auth into App Router

**Files:**
- Modify: `web_interface/frontend/src/App.tsx`
- Modify: `web_interface/frontend/src/main.tsx`

- [ ] **Step 1: Wrap app in AuthProvider**

Update `web_interface/frontend/src/main.tsx`:

```tsx
import React from 'react'
import ReactDOM from 'react-dom/client'
import App from './App'
import './index.css'
import { AuthProvider } from './contexts/AuthContext'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <AuthProvider>
      <App />
    </AuthProvider>
  </React.StrictMode>,
)
```

- [ ] **Step 2: Add auth gating to App.tsx**

Update `web_interface/frontend/src/App.tsx`:

```tsx
import { BrowserRouter, Routes, Route, useNavigate, useParams, Navigate } from 'react-router-dom'
import { useAuth } from './contexts/AuthContext'
import LoginPage from './components/LoginPage'
import UploadPage from './components/UploadPage'
import PipelineViewer from './components/PipelineViewer'
import { Loader2 } from 'lucide-react'

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
        </Routes>
      </div>
    </BrowserRouter>
  )
}

export default App
```

- [ ] **Step 3: Build and verify**

Run: `cd web_interface/frontend && npm run build`
Expected: clean build, no errors

- [ ] **Step 4: Manual test**

1. Open `http://localhost:3000` — should redirect to `/login`
2. Enter name + email — should redirect to `/`
3. Refresh page — should stay on `/` (session persisted)
4. Clear cookies — should redirect back to `/login`

- [ ] **Step 5: Commit**

```bash
git add web_interface/frontend/src/main.tsx web_interface/frontend/src/App.tsx
git commit -m "feat: wire auth context into app router with login gating"
```
