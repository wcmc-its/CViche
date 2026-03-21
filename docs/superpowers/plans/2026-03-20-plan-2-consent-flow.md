# Plan 2: Consent Flow

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the consent flow system so that users must read and agree to the project consent text before uploading CVs. Consent is version-tracked, auditable, and captures the user's default submission type (own CV vs. authorized admin). When the consent version is bumped, users are prompted to re-consent.

**Architecture:** Consent text lives in a markdown file (`consent_text.md`) with a SHA-256 integrity check on startup. The backend exposes GET/POST `/api/consent` endpoints. The frontend adds a `ConsentPage.tsx` that gates access to the upload page via the existing `AuthContext`. The upload page gains a submission_type toggle defaulting from the consent page selection.

**Tech Stack:** FastAPI, SQLAlchemy (Consent model from Plan 1 migration), React, React Router, Tailwind CSS

**Spec:** `docs/superpowers/specs/2026-03-20-production-readiness-design.md` — section 3 (Consent Flow), section 2 (Consent table).

**Depends on:** Plan 1 (User model, auth middleware, AuthContext, session management, config_loader, SystemConfig table).

---

### Task 1: Create Consent Table (Alembic Migration)

The Consent model is already defined in `models.py` from Plan 1, but the table needs to be created via migration.

**Files:**
- Modify: `web_interface/backend/app/models.py` (add Consent model if not present from Plan 1)
- Create: Alembic migration file (auto-generated)

- [ ] **Step 1: Verify or add the Consent model to models.py**

Ensure the following exists in `web_interface/backend/app/models.py` (add it after the `User` model if Plan 1 hasn't already added it):

```python
class Consent(Base):
    """Consent audit trail."""
    __tablename__ = "consent"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    consent_version = Column(String(50), nullable=False)
    consent_text_hash = Column(String(64), nullable=False)  # SHA-256 hex digest
    ip_address = Column(String(45), nullable=True)  # IPv6 max length
    user_agent = Column(String(512), nullable=True)
    timestamp = Column(DateTime, server_default=func.now())
```

- [ ] **Step 2: Generate Alembic migration for the consent table**

Run:
```bash
cd web_interface/backend && alembic revision --autogenerate -m "add consent table"
```

- [ ] **Step 3: Apply the migration**

Run:
```bash
cd web_interface/backend && alembic upgrade head
```

- [ ] **Step 4: Verify the table exists**

Run:
```bash
mysql -u root cviche -e "DESCRIBE consent;"
```

Expected: table with columns `id`, `user_id`, `consent_version`, `consent_text_hash`, `ip_address`, `user_agent`, `timestamp`.

- [ ] **Step 5: Commit**

```bash
git add web_interface/backend/app/models.py web_interface/backend/alembic/versions/
git commit -m "feat: add consent table via Alembic migration"
```

---

### Task 2: Create consent_text.md

**Files:**
- Create: `web_interface/backend/consent_text.md`

- [ ] **Step 1: Create the consent text file**

Create `web_interface/backend/consent_text.md` with content derived from the spec (section 3, Qualtrics form SV_0kZg7KkZYZlBy4e):

```markdown
# CViche Pilot Program — Consent to Participate

## About This Project

The Samuel J. Wood Library at Weill Cornell Medicine is conducting a pilot of **CViche**, a prototype tool that uses artificial intelligence to convert CVs from various formats into the standard WCM institutional CV format.

This pilot is part of an ongoing research initiative to evaluate the feasibility, accuracy, and usefulness of AI-assisted CV formatting. Your participation helps us measure the tool's performance and identify areas for improvement.

## How Your CV Will Be Used

When you submit a CV through CViche:

1. **AI Processing:** Your CV will be processed using large language models (LLMs) to extract, classify, and reformat its content into the WCM CV template.
2. **Staff Review:** Project staff may review submitted CVs and their processed outputs to evaluate the tool's accuracy and identify errors.
3. **Feedback Analysis:** If you provide feedback after processing, your responses will be analyzed alongside the processing data (cost, duration, error rates) to assess the tool's effectiveness.
4. **Research Use:** Aggregated and de-identified data from this pilot — including processing metrics, feedback scores, and error patterns — may be used in academic publications describing the tool's development and evaluation.

Your submitted CV and personal information will **not** be shared outside the project team or used for any purpose other than this pilot evaluation.

## Sensitive Information Notice

CVs may contain personal details such as home addresses, phone numbers, or dates of birth. You are welcome to **redact or omit any personal contact information** from your CV before submission. The tool processes the content you provide and does not require personal contact details to function.

## What We Collect

- **Your name and email address** (for identification and communication)
- **Your authorization role** — whether you are submitting your own CV or processing a CV on behalf of a faculty member as an authorized administrator
- **The CV document you upload** and all outputs generated during processing
- **Processing metadata** — timestamps, duration, cost, token usage, and any errors encountered
- **Your feedback** (if provided) on the quality of the processed output

## Consent

By checking the consent box below, you confirm that:

- You have read and understand the information above.
- You consent to participate in the CViche pilot program under the terms described.
- You understand that your participation is voluntary and you may stop using the tool at any time.
```

- [ ] **Step 2: Commit**

```bash
git add web_interface/backend/consent_text.md
git commit -m "feat: add consent text markdown file for CViche pilot"
```

---

### Task 3: Consent Version Integrity Check on Startup

**Files:**
- Create: `web_interface/backend/app/consent.py`
- Modify: `web_interface/backend/app/main.py`

- [ ] **Step 1: Create the consent module**

Create `web_interface/backend/app/consent.py`:

```python
"""Consent text management and version integrity checking."""
import hashlib
import logging
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import Consent
from app.config_loader import get_config_value

logger = logging.getLogger(__name__)

CONSENT_TEXT_PATH = Path(__file__).parent.parent / "consent_text.md"

# Module-level cache: populated on startup, used by endpoints
_consent_text: str | None = None
_consent_text_hash: str | None = None


def load_consent_text() -> str:
    """Load consent text from the markdown file."""
    global _consent_text, _consent_text_hash
    text = CONSENT_TEXT_PATH.read_text(encoding="utf-8")
    _consent_text = text
    _consent_text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return text


def get_consent_text() -> str:
    """Return cached consent text (loaded on startup)."""
    if _consent_text is None:
        load_consent_text()
    return _consent_text  # type: ignore[return-value]


def get_consent_text_hash() -> str:
    """Return cached SHA-256 hash of the consent text."""
    if _consent_text_hash is None:
        load_consent_text()
    return _consent_text_hash  # type: ignore[return-value]


def check_consent_integrity(db: Session) -> None:
    """Check consent text hash against the last recorded hash for the current version.

    Called on startup. Logs a warning if the consent text has changed but the
    version string has not been bumped — this prevents silent consent text changes
    that bypass the re-consent mechanism.
    """
    current_hash = get_consent_text_hash()
    current_version = get_config_value(db, "consent_version")

    if current_version is None:
        logger.info("No consent version configured — skipping integrity check.")
        return

    # Find the most recent consent record for this version
    last_consent = (
        db.query(Consent)
        .filter(Consent.consent_version == str(current_version))
        .order_by(Consent.timestamp.desc())
        .first()
    )

    if last_consent is None:
        logger.info(
            "No consent records found for version %s — first deployment with this version.",
            current_version,
        )
        return

    if last_consent.consent_text_hash != current_hash:
        logger.warning(
            "Consent text has changed but version is still %s. "
            "Bump the version in config to require re-consent. "
            "Stored hash: %s, current hash: %s",
            current_version,
            last_consent.consent_text_hash,
            current_hash,
        )
    else:
        logger.info(
            "Consent text integrity OK for version %s (hash: %s...)",
            current_version,
            current_hash[:12],
        )
```

- [ ] **Step 2: Add consent integrity check to startup in main.py**

Add to the `lifespan` function in `web_interface/backend/app/main.py`, after the config seeding block:

```python
    from app.consent import load_consent_text, check_consent_integrity

    # Load and cache consent text
    load_consent_text()
    print("✅ Consent text loaded")

    # Check consent text integrity
    check_consent_integrity(db)
```

Note: This goes inside the same `db = SessionLocal()` / `try` / `finally` block that already exists from Plan 1's config seeding. The full startup block should look like:

```python
    from app.config_loader import seed_system_config
    from app.consent import load_consent_text, check_consent_integrity
    from app.database import SessionLocal

    db = SessionLocal()
    try:
        seed_system_config(db)
        print("✅ System config seeded")
        load_consent_text()
        print("✅ Consent text loaded")
        check_consent_integrity(db)
    finally:
        db.close()
```

- [ ] **Step 3: Verify startup**

Run:
```bash
cd web_interface/backend && python3 -c "
from app.consent import load_consent_text, get_consent_text_hash
load_consent_text()
h = get_consent_text_hash()
print(f'Hash: {h[:16]}...')
print(f'Length: {len(get_consent_text_hash())} chars')
"
```

Expected: prints a 64-character SHA-256 hash prefix and length = 64.

- [ ] **Step 4: Commit**

```bash
git add web_interface/backend/app/consent.py web_interface/backend/app/main.py
git commit -m "feat: add consent text loader with SHA-256 integrity check on startup"
```

---

### Task 4: GET /api/consent Endpoint

**Files:**
- Create: `web_interface/backend/app/api/consent_routes.py`
- Modify: `web_interface/backend/app/main.py`

- [ ] **Step 1: Create consent API routes**

Create `web_interface/backend/app/api/consent_routes.py`:

```python
"""Consent API endpoints."""
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User, Consent
from app.auth import get_current_user
from app.config_loader import get_config_value
from app.consent import get_consent_text, get_consent_text_hash

logger = logging.getLogger(__name__)
router = APIRouter()


class ConsentStatusResponse(BaseModel):
    """Response for GET /api/consent."""
    consent_text: str
    consent_version: str
    has_consented: bool
    user_consent_version: str | None
    user_consent_date: str | None

    class Config:
        from_attributes = True


class ConsentSubmitRequest(BaseModel):
    """Request for POST /api/consent."""
    default_submission_type: str  # "own_cv" or "authorized_admin"


class ConsentSubmitResponse(BaseModel):
    """Response for POST /api/consent."""
    message: str
    consent_version: str
    default_submission_type: str


@router.get("/consent", response_model=ConsentStatusResponse)
async def get_consent(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return current consent text, version, and whether the user has consented."""
    current_version = str(get_config_value(db, "consent_version") or "1.0")
    consent_text = get_consent_text()

    has_consented = (
        current_user.consent_version is not None
        and current_user.consent_version == current_version
    )

    return ConsentStatusResponse(
        consent_text=consent_text,
        consent_version=current_version,
        has_consented=has_consented,
        user_consent_version=current_user.consent_version,
        user_consent_date=(
            current_user.consent_date.isoformat()
            if current_user.consent_date
            else None
        ),
    )


@router.post("/consent", response_model=ConsentSubmitResponse)
async def submit_consent(
    body: ConsentSubmitRequest,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Record consent and update user record."""
    # Validate submission type
    if body.default_submission_type not in ("own_cv", "authorized_admin"):
        raise HTTPException(
            status_code=422,
            detail={
                "error": "validation_error",
                "message": "default_submission_type must be 'own_cv' or 'authorized_admin'.",
            },
        )

    current_version = str(get_config_value(db, "consent_version") or "1.0")
    text_hash = get_consent_text_hash()

    # Create consent audit record
    consent_record = Consent(
        user_id=current_user.id,
        consent_version=current_version,
        consent_text_hash=text_hash,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent", "")[:512],
    )
    db.add(consent_record)

    # Update user record
    current_user.consent_version = current_version
    current_user.consent_date = datetime.now()
    current_user.default_submission_type = body.default_submission_type

    db.commit()

    logger.info(
        "consent_given",
        extra={
            "user_id": current_user.id,
            "user_email": current_user.email,
            "consent_version": current_version,
            "default_submission_type": body.default_submission_type,
        },
    )

    return ConsentSubmitResponse(
        message="Consent recorded successfully.",
        consent_version=current_version,
        default_submission_type=body.default_submission_type,
    )
```

- [ ] **Step 2: Register consent routes in main.py**

Add the import and router registration in `web_interface/backend/app/main.py`:

```python
from app.api import upload, runs, steps, websocket, auth_routes, consent_routes
```

And in the router registration section:

```python
app.include_router(consent_routes.router, prefix="/api", tags=["consent"])
```

- [ ] **Step 3: Verify endpoints load**

Run:
```bash
cd web_interface/backend && python3 -c "from app.api.consent_routes import router; print(f'Routes: {len(router.routes)}')"
```

Expected: `Routes: 2`

- [ ] **Step 4: Test GET /api/consent (requires login cookie)**

Run:
```bash
# Login first
curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "paa2013@med.cornell.edu", "display_name": "Paul Albert"}' \
  -c cookies.txt > /dev/null

# Get consent status
curl -s http://localhost:8000/api/consent -b cookies.txt | python3 -m json.tool
```

Expected: JSON with `consent_text` (full markdown), `consent_version: "1.0"`, `has_consented: false`.

- [ ] **Step 5: Test POST /api/consent**

Run:
```bash
curl -s -X POST http://localhost:8000/api/consent \
  -H "Content-Type: application/json" \
  -d '{"default_submission_type": "authorized_admin"}' \
  -b cookies.txt | python3 -m json.tool
```

Expected: JSON with `consent_version: "1.0"`, `default_submission_type: "authorized_admin"`.

Verify the audit record:
```bash
mysql -u root cviche -e "SELECT id, user_id, consent_version, LEFT(consent_text_hash, 16) as hash_prefix, ip_address, timestamp FROM consent;"
```

Expected: one row with the consent data.

- [ ] **Step 6: Commit**

```bash
git add web_interface/backend/app/api/consent_routes.py web_interface/backend/app/main.py
git commit -m "feat: add GET and POST /api/consent endpoints with audit trail"
```

---

### Task 5: Update AuthContext to Track Consent Status

**Files:**
- Modify: `web_interface/frontend/src/contexts/AuthContext.tsx`

- [ ] **Step 1: Add consent status and consent_version to AuthContext**

Update the `User` interface and `AuthContextType` in `web_interface/frontend/src/contexts/AuthContext.tsx`:

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

interface ConsentStatus {
  consent_text: string
  consent_version: string
  has_consented: boolean
  user_consent_version: string | null
  user_consent_date: string | null
}

interface AuthContextType {
  user: User | null
  loading: boolean
  consentStatus: ConsentStatus | null
  consentLoading: boolean
  needsConsent: boolean
  login: (email: string, displayName: string) => Promise<void>
  logout: () => Promise<void>
  refreshUser: () => Promise<void>
  refreshConsent: () => Promise<void>
}

const AuthContext = createContext<AuthContextType | null>(null)

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null)
  const [loading, setLoading] = useState(true)
  const [consentStatus, setConsentStatus] = useState<ConsentStatus | null>(null)
  const [consentLoading, setConsentLoading] = useState(false)

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

  const refreshConsent = async () => {
    setConsentLoading(true)
    try {
      const res = await fetch('/api/consent')
      if (res.ok) {
        setConsentStatus(await res.json())
      }
    } catch {
      // If consent check fails, don't block — user will see consent page on next load
    } finally {
      setConsentLoading(false)
    }
  }

  useEffect(() => {
    refreshUser()
  }, [])

  // Fetch consent status whenever user changes
  useEffect(() => {
    if (user) {
      refreshConsent()
    } else {
      setConsentStatus(null)
    }
  }, [user])

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
    setConsentStatus(null)
  }

  const needsConsent = !!(
    user &&
    consentStatus &&
    !consentStatus.has_consented
  )

  return (
    <AuthContext.Provider value={{
      user,
      loading,
      consentStatus,
      consentLoading,
      needsConsent,
      login,
      logout,
      refreshUser,
      refreshConsent,
    }}>
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

- [ ] **Step 2: Verify TypeScript compiles**

Run:
```bash
cd web_interface/frontend && npx tsc --noEmit
```

Expected: no errors (or only pre-existing errors unrelated to AuthContext).

- [ ] **Step 3: Commit**

```bash
git add web_interface/frontend/src/contexts/AuthContext.tsx
git commit -m "feat: add consent status tracking to AuthContext"
```

---

### Task 6: ConsentPage.tsx Component

**Files:**
- Create: `web_interface/frontend/src/components/ConsentPage.tsx`

- [ ] **Step 1: Create the consent page component**

Create `web_interface/frontend/src/components/ConsentPage.tsx`:

```tsx
import { useState } from 'react'
import { Loader2, ShieldCheck } from 'lucide-react'
import { useAuth } from '../contexts/AuthContext'
import ErrorBanner from './ErrorBanner'

export default function ConsentPage() {
  const { user, consentStatus, refreshConsent, refreshUser } = useAuth()
  const [submissionType, setSubmissionType] = useState<string>(
    user?.default_submission_type || 'own_cv'
  )
  const [agreed, setAgreed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!agreed) return

    setSubmitting(true)
    setError(null)

    try {
      const res = await fetch('/api/consent', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ default_submission_type: submissionType }),
      })

      if (!res.ok) {
        const err = await res.json()
        throw new Error(err.detail?.message || err.message || 'Failed to submit consent')
      }

      // Refresh both consent status and user data so the app re-evaluates
      await Promise.all([refreshConsent(), refreshUser()])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to submit consent')
    } finally {
      setSubmitting(false)
    }
  }

  if (!consentStatus) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
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
      <div className="w-full max-w-2xl">
        {/* Logo */}
        <div className="flex justify-center mb-6">
          <img
            src="/header-logo.png"
            alt="CViche"
            className="h-16 object-contain"
          />
        </div>

        <form onSubmit={handleSubmit}>
          <section className="bg-white/95 backdrop-blur-sm rounded-lg shadow-lg p-6 md:p-8">
            {/* Header */}
            <div className="flex items-center gap-3 mb-6">
              <ShieldCheck className="h-6 w-6 text-primary-600 flex-shrink-0" aria-hidden="true" />
              <div>
                <h1 className="text-xl font-bold text-gray-900">Consent to Participate</h1>
                <p className="text-sm text-gray-500">Version {consentStatus.consent_version}</p>
              </div>
            </div>

            {/* Consent text — rendered as prose */}
            <div className="prose prose-sm prose-gray max-w-none mb-8 max-h-96 overflow-y-auto border border-gray-200 rounded-lg p-4 bg-gray-50">
              <ConsentTextRenderer text={consentStatus.consent_text} />
            </div>

            {/* User info (pre-filled, read-only) */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 mb-6">
              <div>
                <label className="block text-sm font-semibold text-gray-900 mb-1">Name</label>
                <input
                  type="text"
                  value={user?.display_name || ''}
                  readOnly
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg bg-gray-50 text-gray-700 cursor-not-allowed"
                />
              </div>
              <div>
                <label className="block text-sm font-semibold text-gray-900 mb-1">Email</label>
                <input
                  type="text"
                  value={user?.email || ''}
                  readOnly
                  className="w-full px-3 py-2 border border-gray-200 rounded-lg bg-gray-50 text-gray-700 cursor-not-allowed"
                />
              </div>
            </div>

            {/* Authorization role toggle */}
            <div className="mb-6">
              <label className="block text-sm font-semibold text-gray-900 mb-2">
                Authorization Role
              </label>
              <p className="text-sm text-gray-600 mb-3">
                How will you primarily use CViche? You can change this per submission on the upload page.
              </p>
              <div className="flex gap-3">
                <button
                  type="button"
                  onClick={() => setSubmissionType('own_cv')}
                  className={`flex-1 py-3 px-4 rounded-lg border-2 text-sm font-medium transition-colors ${
                    submissionType === 'own_cv'
                      ? 'border-primary-600 bg-primary-50 text-primary-700'
                      : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                  }`}
                >
                  Submitting my own CV
                </button>
                <button
                  type="button"
                  onClick={() => setSubmissionType('authorized_admin')}
                  className={`flex-1 py-3 px-4 rounded-lg border-2 text-sm font-medium transition-colors ${
                    submissionType === 'authorized_admin'
                      ? 'border-primary-600 bg-primary-50 text-primary-700'
                      : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                  }`}
                >
                  Authorized on behalf of faculty
                </button>
              </div>
            </div>

            {/* Consent checkbox */}
            <div className="mb-6">
              <label className="flex items-start gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={agreed}
                  onChange={(e) => setAgreed(e.target.checked)}
                  className="mt-1 h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                />
                <span className="text-sm text-gray-700">
                  I have read the information above and consent to participate in the CViche pilot program.
                </span>
              </label>
            </div>

            {error && <ErrorBanner message={error} onDismiss={() => setError(null)} />}

            {/* Submit button */}
            <button
              type="submit"
              disabled={!agreed || submitting}
              className="w-full bg-primary-600 text-white py-3 px-4 rounded-lg font-semibold hover:bg-primary-700 disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors focus:ring-2 focus:ring-primary-500 focus:outline-none flex items-center justify-center gap-2"
            >
              {submitting ? (
                <>
                  <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
                  Submitting...
                </>
              ) : (
                <>
                  <ShieldCheck className="h-5 w-5" aria-hidden="true" />
                  Agree and Continue
                </>
              )}
            </button>
          </section>
        </form>
      </div>
    </main>
  )
}


/**
 * Simple markdown-to-HTML renderer for the consent text.
 * Handles headings (## / ###), bold (**text**), numbered lists, bullet lists,
 * and paragraphs. No external dependency needed for this limited subset.
 */
function ConsentTextRenderer({ text }: { text: string }) {
  const lines = text.split('\n')
  const elements: React.ReactNode[] = []
  let key = 0

  const renderInline = (line: string): React.ReactNode => {
    // Bold: **text**
    const parts = line.split(/(\*\*[^*]+\*\*)/)
    return parts.map((part, i) => {
      if (part.startsWith('**') && part.endsWith('**')) {
        return <strong key={i}>{part.slice(2, -2)}</strong>
      }
      return part
    })
  }

  let i = 0
  while (i < lines.length) {
    const line = lines[i]

    // Skip empty lines
    if (line.trim() === '') {
      i++
      continue
    }

    // H1: # Heading
    if (line.startsWith('# ') && !line.startsWith('## ')) {
      elements.push(<h2 key={key++} className="text-lg font-bold text-gray-900 mb-2 mt-4 first:mt-0">{renderInline(line.slice(2))}</h2>)
      i++
      continue
    }

    // H2: ## Heading
    if (line.startsWith('## ')) {
      elements.push(<h3 key={key++} className="text-base font-bold text-gray-900 mb-2 mt-4">{renderInline(line.slice(3))}</h3>)
      i++
      continue
    }

    // H3: ### Heading
    if (line.startsWith('### ')) {
      elements.push(<h4 key={key++} className="text-sm font-bold text-gray-900 mb-1 mt-3">{renderInline(line.slice(4))}</h4>)
      i++
      continue
    }

    // Numbered list: 1. item
    if (/^\d+\.\s/.test(line.trim())) {
      const items: React.ReactNode[] = []
      while (i < lines.length && /^\d+\.\s/.test(lines[i].trim())) {
        items.push(<li key={key++}>{renderInline(lines[i].trim().replace(/^\d+\.\s/, ''))}</li>)
        i++
      }
      elements.push(<ol key={key++} className="list-decimal list-inside space-y-1 mb-3 text-gray-700">{items}</ol>)
      continue
    }

    // Bullet list: - item
    if (line.trim().startsWith('- ')) {
      const items: React.ReactNode[] = []
      while (i < lines.length && lines[i].trim().startsWith('- ')) {
        items.push(<li key={key++}>{renderInline(lines[i].trim().slice(2))}</li>)
        i++
      }
      elements.push(<ul key={key++} className="list-disc list-inside space-y-1 mb-3 text-gray-700">{items}</ul>)
      continue
    }

    // Paragraph
    elements.push(<p key={key++} className="mb-3 text-gray-700">{renderInline(line)}</p>)
    i++
  }

  return <>{elements}</>
}
```

- [ ] **Step 2: Verify TypeScript compiles**

Run:
```bash
cd web_interface/frontend && npx tsc --noEmit
```

Expected: no errors from `ConsentPage.tsx`.

- [ ] **Step 3: Commit**

```bash
git add web_interface/frontend/src/components/ConsentPage.tsx
git commit -m "feat: add ConsentPage component with consent text, role toggle, and checkbox"
```

---

### Task 7: Wire Consent Gate into App.tsx Routing

**Files:**
- Modify: `web_interface/frontend/src/App.tsx`

- [ ] **Step 1: Add consent route and RequireConsent guard to App.tsx**

Replace the contents of `web_interface/frontend/src/App.tsx`:

```tsx
import { BrowserRouter, Routes, Route, useNavigate, useParams, Navigate } from 'react-router-dom'
import { useAuth } from './contexts/AuthContext'
import LoginPage from './components/LoginPage'
import ConsentPage from './components/ConsentPage'
import UploadPage from './components/UploadPage'
import PipelineViewer from './components/PipelineViewer'
import { Loader2 } from 'lucide-react'

/**
 * Gate: redirects to /login if not authenticated.
 */
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

/**
 * Gate: redirects to /consent if user hasn't consented to the current version.
 * Must be nested inside RequireAuth (assumes user is present).
 */
function RequireConsent({ children }: { children: React.ReactNode }) {
  const { needsConsent, consentLoading } = useAuth()

  if (consentLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
  }

  if (needsConsent) {
    return <Navigate to="/consent" replace />
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

/**
 * Consent page route: requires auth but NOT consent (otherwise infinite redirect).
 * If user has already consented, redirect to upload page.
 */
function ConsentRoute() {
  const { needsConsent, consentLoading } = useAuth()

  if (consentLoading) {
    return (
      <div className="flex items-center justify-center min-h-screen">
        <Loader2 className="h-8 w-8 animate-spin text-gray-400" />
      </div>
    )
  }

  // If user already consented, send them to the upload page
  if (!needsConsent) {
    return <Navigate to="/" replace />
  }

  return <ConsentPage />
}

function App() {
  return (
    <BrowserRouter>
      <div className="min-h-screen bg-surface-muted">
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/consent"
            element={
              <RequireAuth>
                <ConsentRoute />
              </RequireAuth>
            }
          />
          <Route
            path="/"
            element={
              <RequireAuth>
                <RequireConsent>
                  <UploadRoute />
                </RequireConsent>
              </RequireAuth>
            }
          />
          <Route
            path="/run/:runId"
            element={
              <RequireAuth>
                <RequireConsent>
                  <PipelineRoute />
                </RequireConsent>
              </RequireAuth>
            }
          />
        </Routes>
      </div>
    </BrowserRouter>
  )
}

export default App
```

- [ ] **Step 2: Verify build**

Run:
```bash
cd web_interface/frontend && npm run build
```

Expected: clean build, no errors.

- [ ] **Step 3: Manual test — consent redirect flow**

1. Clear cookies in browser
2. Open `http://localhost:3000` — should redirect to `/login`
3. Log in — should redirect to `/consent` (no consent on file)
4. Complete consent form — should redirect to `/` (upload page)
5. Refresh page — should stay on `/` (consent is current)
6. Navigate to `/consent` directly — should redirect to `/` (already consented)

- [ ] **Step 4: Commit**

```bash
git add web_interface/frontend/src/App.tsx
git commit -m "feat: add consent gating to app router — redirect to /consent if not consented"
```

---

### Task 8: Upload Page — Submission Type Toggle

**Files:**
- Modify: `web_interface/frontend/src/components/UploadPage.tsx`
- Modify: `web_interface/backend/app/api/upload.py`

- [ ] **Step 1: Add submission_type and toggles to the upload page frontend**

Update `web_interface/frontend/src/components/UploadPage.tsx`. Add state for the three toggles and pass `submission_type` to the upload request.

Add imports at the top:

```tsx
import { useAuth } from '../contexts/AuthContext'
```

Inside the `UploadPage` component, add state variables after the existing state declarations:

```tsx
  const { user } = useAuth()
  const [submissionType, setSubmissionType] = useState<string>(
    user?.default_submission_type || 'own_cv'
  )
  const [showTrackChanges, setShowTrackChanges] = useState(true)
  const [showPipelineComments, setShowPipelineComments] = useState(false)
```

In the `handleUpload` function, include the toggles in the upload request. Replace the `formData.append('file', file)` section:

```tsx
    const formData = new FormData()
    formData.append('file', file)
    formData.append('submission_type', submissionType)
    formData.append('show_track_changes', showTrackChanges ? '1' : '0')
    formData.append('show_pipeline_comments', showPipelineComments ? '1' : '0')
```

Add the following JSX block **between** the estimate section and the error banner (right before `{error && (`):

```tsx
            {/* Run options */}
            <div className="border border-gray-200 rounded-lg p-4 space-y-4">
              <h2 className="text-sm font-semibold text-gray-900">Run Options</h2>

              {/* Submission type */}
              <div>
                <label className="block text-xs font-medium text-gray-600 mb-2">
                  Submission Type
                </label>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => setSubmissionType('own_cv')}
                    className={`flex-1 py-2 px-3 rounded-md border text-xs font-medium transition-colors ${
                      submissionType === 'own_cv'
                        ? 'border-primary-600 bg-primary-50 text-primary-700'
                        : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                    }`}
                  >
                    My own CV
                  </button>
                  <button
                    type="button"
                    onClick={() => setSubmissionType('authorized_admin')}
                    className={`flex-1 py-2 px-3 rounded-md border text-xs font-medium transition-colors ${
                      submissionType === 'authorized_admin'
                        ? 'border-primary-600 bg-primary-50 text-primary-700'
                        : 'border-gray-200 bg-white text-gray-600 hover:border-gray-300'
                    }`}
                  >
                    On behalf of faculty
                  </button>
                </div>
              </div>

              {/* Toggle: Show track changes */}
              <label className="flex items-center justify-between cursor-pointer">
                <div>
                  <span className="text-xs font-medium text-gray-700">Show track changes</span>
                  <p className="text-xs text-gray-500">Highlight modifications from the original text</p>
                </div>
                <input
                  type="checkbox"
                  checked={showTrackChanges}
                  onChange={(e) => setShowTrackChanges(e.target.checked)}
                  className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                />
              </label>

              {/* Toggle: Show pipeline comments */}
              <label className="flex items-center justify-between cursor-pointer">
                <div>
                  <span className="text-xs font-medium text-gray-700">Show pipeline comments</span>
                  <p className="text-xs text-gray-500">Include classification details and confidence scores</p>
                </div>
                <input
                  type="checkbox"
                  checked={showPipelineComments}
                  onChange={(e) => setShowPipelineComments(e.target.checked)}
                  className="h-4 w-4 rounded border-gray-300 text-primary-600 focus:ring-primary-500"
                />
              </label>
            </div>
```

- [ ] **Step 2: Update backend upload endpoint to accept the new fields**

In `web_interface/backend/app/api/upload.py`, update the `upload_cv` function to accept the new form fields and set them on the Run record.

Add imports:

```python
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, Form
from typing import Optional
from app.auth import get_current_user
from app.models import Run, Step, User
```

Update the function signature:

```python
@router.post("/upload", response_model=UploadResponse)
async def upload_cv(
    file: UploadFile = File(...),
    submission_type: Optional[str] = Form(None),
    show_track_changes: Optional[str] = Form("1"),
    show_pipeline_comments: Optional[str] = Form("0"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
```

Update the Run creation to include the new fields:

```python
    run = Run(
        id=run_id,
        filename=file.filename,
        file_type=file_ext[1:],
        status="created",
        started_at=datetime.now(),
        user_id=current_user.id,
        submission_type=submission_type or current_user.default_submission_type,
        show_track_changes=1 if show_track_changes == "1" else 0,
        show_pipeline_comments=1 if show_pipeline_comments == "1" else 0,
    )
```

- [ ] **Step 3: Verify build**

Run:
```bash
cd web_interface/frontend && npm run build
```

Expected: clean build.

- [ ] **Step 4: Test upload with new fields**

Run:
```bash
# Login
curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email": "paa2013@med.cornell.edu", "display_name": "Paul Albert"}' \
  -c cookies.txt > /dev/null

# Submit consent first
curl -s -X POST http://localhost:8000/api/consent \
  -H "Content-Type: application/json" \
  -d '{"default_submission_type": "authorized_admin"}' \
  -b cookies.txt > /dev/null

# Upload with new fields
curl -s -X POST http://localhost:8000/api/upload \
  -F "file=@test.docx" \
  -F "submission_type=own_cv" \
  -F "show_track_changes=1" \
  -F "show_pipeline_comments=0" \
  -b cookies.txt | python3 -m json.tool
```

Then verify the Run record:
```bash
mysql -u root cviche -e "SELECT id, user_id, submission_type, show_track_changes, show_pipeline_comments FROM runs ORDER BY created_at DESC LIMIT 1;"
```

Expected: `submission_type=own_cv`, `show_track_changes=1`, `show_pipeline_comments=0`.

- [ ] **Step 5: Commit**

```bash
git add web_interface/frontend/src/components/UploadPage.tsx web_interface/backend/app/api/upload.py
git commit -m "feat: add submission type toggle and output options to upload page"
```

---

### Task 9: Backend Consent Check at Upload Time

**Files:**
- Modify: `web_interface/backend/app/api/upload.py`

- [ ] **Step 1: Add consent check to the upload endpoint**

In `web_interface/backend/app/api/upload.py`, add a consent check at the beginning of `upload_cv`, after the `current_user` dependency resolves.

Add import:

```python
from app.config_loader import get_config_value
```

Add this check at the top of the `upload_cv` function body, before file validation:

```python
    # Check consent at upload time (not just page visit)
    current_consent_version = str(get_config_value(db, "consent_version") or "1.0")
    if current_user.consent_version != current_consent_version:
        raise HTTPException(
            status_code=403,
            detail={
                "error": "consent_required",
                "message": "You must consent to the current terms before uploading. Please visit the consent page.",
            },
        )
```

- [ ] **Step 2: Handle consent_required error in frontend**

In `web_interface/frontend/src/components/UploadPage.tsx`, update the `handleUpload` error handling to detect `consent_required` and redirect:

Add the import at the top:

```tsx
import { useNavigate } from 'react-router-dom'
```

(This import should already exist from the current code.)

In the `catch` block of `handleUpload`, add:

```tsx
    } catch (err) {
      if (err instanceof Response || (err instanceof Error && err.message.includes('consent'))) {
        // Consent expired — redirect
        navigate('/consent')
        return
      }
      setError('Failed to upload file. Please try again.')
      console.error(err)
    }
```

More precisely, update the upload response handling to check for `consent_required`:

```tsx
      const response = await fetch('/api/upload', {
        method: 'POST',
        body: formData,
      })

      if (!response.ok) {
        const errData = await response.json().catch(() => null)
        if (errData?.detail?.error === 'consent_required') {
          navigate('/consent')
          return
        }
        throw new Error(errData?.detail?.message || 'Upload failed')
      }
```

- [ ] **Step 3: Test consent enforcement**

1. Login and consent to version "1.0"
2. Upload a file — should succeed
3. In the database, change `consent_version` in `system_config` to `"2.0"`:
   ```bash
   mysql -u root cviche -e "UPDATE system_config SET value='\"2.0\"' WHERE \`key\`='consent_version';"
   ```
4. Try uploading again — should get 403 `consent_required` and redirect to `/consent`

- [ ] **Step 4: Commit**

```bash
git add web_interface/backend/app/api/upload.py web_interface/frontend/src/components/UploadPage.tsx
git commit -m "feat: enforce consent check at upload time, redirect on consent_required"
```

---

### Task 10: End-to-End Verification

No files to create or modify — this is a manual verification task.

- [ ] **Step 1: Clean-slate test**

Reset test state:
```bash
mysql -u root cviche -e "DELETE FROM consent; UPDATE users SET consent_version = NULL, consent_date = NULL;"
```

Clear browser cookies.

- [ ] **Step 2: Verify full flow**

1. Open `http://localhost:3000` — redirects to `/login`
2. Enter name + allowed email — redirects to `/consent`
3. Read consent text — verify it renders the markdown correctly with headings, bold, lists
4. Verify name and email fields are pre-filled and read-only
5. Select "Authorized on behalf of faculty" toggle
6. Check the consent checkbox
7. Click "Agree and Continue" — redirects to `/` (upload page)
8. Verify the upload page "Run Options" section shows "On behalf of faculty" selected by default
9. Switch to "My own CV" and upload a file — should succeed
10. Verify the Consent table has an audit record with version, hash, IP, user agent
11. Verify the Run table has `submission_type = 'own_cv'`

- [ ] **Step 3: Verify re-consent flow**

1. Bump consent version: `mysql -u root cviche -e "UPDATE system_config SET value='\"1.1\"' WHERE \`key\`='consent_version';"`
2. Refresh page — should redirect to `/consent`
3. Complete consent again
4. Verify a second row in the Consent table with version `1.1`
5. Upload page should work again

- [ ] **Step 4: Verify integrity check on startup**

1. Modify `consent_text.md` slightly (add a space)
2. Restart the backend server
3. Check server logs — should see warning: "Consent text has changed but version is still 1.1. Bump the version in config to require re-consent."
4. Revert the `consent_text.md` change

- [ ] **Step 5: Commit (if any fixes were needed)**

```bash
git add -A
git commit -m "fix: address issues found during consent flow end-to-end testing"
```
