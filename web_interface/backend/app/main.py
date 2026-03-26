"""Main FastAPI application."""
import logging
import os
import traceback

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager

logger = logging.getLogger(__name__)

from app.database import init_db
from app.api import upload, runs, steps, websocket, auth_routes, consent_routes, feedback_routes, admin_routes, saml_routes

# ---------------------------------------------------------------------------
# Allowed origins (env-configurable, comma-separated)
# ---------------------------------------------------------------------------
_allowed_origins = [
    o.strip()
    for o in os.environ.get(
        "CVICHE_ALLOWED_ORIGINS",
        "http://localhost:3000,http://localhost:5173,http://127.0.0.1:3000,http://127.0.0.1:5173",
    ).split(",")
]


# ---------------------------------------------------------------------------
# CSRF protection middleware
# ---------------------------------------------------------------------------
CSRF_EXEMPT_PATHS = {"/api/saml/acs"}


def _build_error_response(request, exc: Exception) -> JSONResponse:
    """Build a sanitized error response, with optional debug traceback."""
    logger.error(
        "Unhandled exception on %s %s: %s",
        request.method,
        request.url.path,
        str(exc),
        exc_info=True,
    )
    if os.environ.get("CVICHE_DEBUG", "").lower() == "true":
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": str(exc),
                "traceback": traceback.format_exception(exc),
            },
        )
    return JSONResponse(
        status_code=500,
        content={
            "error": "internal_error",
            "message": "An unexpected error occurred.",
        },
    )


def _add_security_headers(response: JSONResponse) -> JSONResponse:
    """Add security headers to a response."""
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "font-src 'self'; "
        "connect-src 'self' ws: wss:; "
        "frame-ancestors 'none'"
    )
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response


class CSRFMiddleware(BaseHTTPMiddleware):
    """Reject cross-origin state-changing requests whose Origin header
    does not match the allowed origins list."""

    async def dispatch(self, request, call_next):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            # Skip CSRF for SAML ACS (IdP posts from external origin)
            if request.url.path in CSRF_EXEMPT_PATHS:
                return await call_next(request)
            origin = request.headers.get("origin") or ""
            if origin and not any(origin.startswith(o) for o in _allowed_origins):
                return JSONResponse(
                    status_code=403,
                    content={"error": "forbidden", "message": "Cross-origin request rejected."},
                )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all HTTP responses."""

    async def dispatch(self, request, call_next):
        try:
            response = await call_next(request)
        except Exception as exc:
            response = _build_error_response(request, exc)
        _add_security_headers(response)
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup: Initialize database
    print("🚀 Starting CViche Pipeline Viewer...")
    init_db()
    print("✅ Database initialized")
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
    yield
    # Shutdown: cleanup if needed
    print("👋 Shutting down CViche Pipeline Viewer")


# Create FastAPI app
app = FastAPI(
    title="CViche Pipeline Viewer",
    description="Web interface for CViche with real-time progress tracking",
    version="1.0.0",
    lifespan=lifespan
)

# CORS middleware (allow frontend to connect)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization", "X-Requested-With"],
)

# Security headers middleware (after CORS so headers apply to all responses)
app.add_middleware(SecurityHeadersMiddleware)

# CSRF middleware (must be added after CORS so CORS pre-flight passes first)
app.add_middleware(CSRFMiddleware)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Catch-all: log full traceback server-side, return sanitized response to client."""
    return _build_error_response(request, exc)


# Include API routers
app.include_router(auth_routes.router, prefix="/api", tags=["auth"])
app.include_router(consent_routes.router, prefix="/api", tags=["consent"])
app.include_router(upload.router, prefix="/api", tags=["upload"])
app.include_router(runs.router, prefix="/api", tags=["runs"])
app.include_router(steps.router, prefix="/api", tags=["steps"])
app.include_router(feedback_routes.router, prefix="/api", tags=["feedback"])
app.include_router(admin_routes.router, prefix="/api", tags=["admin"])
app.include_router(saml_routes.router, prefix="/api", tags=["saml"])
app.include_router(websocket.router, tags=["websocket"])


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "CViche Pipeline Viewer API",
        "version": "1.0.0",
        "docs": "/docs",
        "status": "running"
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


if __name__ == "__main__":
    import uvicorn
    import os

    # Enable auto-reload in development mode
    reload = os.getenv("ENVIRONMENT", "development") == "development"

    uvicorn.run(
        "app.main:app",  # Use string import path for reload to work
        host="0.0.0.0",
        port=8000,
        log_level="info",
        reload=reload,  # Auto-reload on code changes
        reload_dirs=["app", "../../../src"]  # Watch both app and src directories
    )
