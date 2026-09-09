"""Main FastAPI application."""
import asyncio
import logging
import os
import traceback


from fastapi import FastAPI, Request, Depends, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session
from starlette.middleware.base import BaseHTTPMiddleware
from contextlib import asynccontextmanager

# Configure logging BEFORE any module-level loggers are wired. dictConfig
# reapplies handlers on existing loggers, but doing it first avoids the
# transient window where boto3 imports might log at default INFO.
from app.logging_config import configure_logging
configure_logging()

logger = logging.getLogger(__name__)

from app.database import init_db, get_db
from app.middleware.request_id import RequestIDMiddleware
from app.api import upload, runs, steps, websocket, auth_routes, consent_routes, feedback_routes, admin_routes, saml_routes
from app.config_loader import get_config
# Allowed origins live in app/origins.py so the WebSocket endpoint can share
# the same allowlist (a WS upgrade gets neither a CORS check nor CSRFMiddleware).
# Re-imported here under their original names: CSRFMiddleware and the CORS
# middleware below read _origin_key/_allowed_origin_keys/_allowed_origins as
# before, and _LOCALHOST_ORIGINS/_resolve_allowed_origins stay reachable as
# app.main attributes for anything that already reads them there.
from app.origins import (  # noqa: F401
    _LOCALHOST_ORIGINS,
    _resolve_allowed_origins,
    _allowed_origins,
    _origin_key,
    _allowed_origin_keys,
)


def _docs_enabled() -> bool:
    """Serve /docs, /redoc and /openapi.json only when explicitly enabled.

    Default off: the interactive docs enumerate every route and schema to
    unauthenticated clients. Enable ONLY with CVICHE_ENABLE_DOCS=true (env var,
    or auth_config.yaml under `auth` -- same wiring as CVICHE_ALLOWED_ORIGINS).
    Deliberately NOT tied to CVICHE_DEBUG: an accidental CVICHE_DEBUG=true in
    prod must not expose the API surface -- docs require their own explicit flag.
    """
    raw, _ = get_config("auth", "CVICHE_ENABLE_DOCS", default="")
    return str(raw).strip().lower() in ("1", "true", "yes")


_DOCS_ENABLED = _docs_enabled()


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
            # Exact scheme+host+port comparison; a prefix match would accept
            # lookalike domains (https://cviche.weill.cornell.edu.evil.com).
            if origin and _origin_key(origin) not in _allowed_origin_keys:
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


async def _stale_run_reaper_loop(interval_seconds: int):
    """Periodically reap runs stuck in 'running' after a pod died mid-run.

    The once-at-startup reconcile only heals a stuck run on the *next* restart;
    in steady state a run whose in-process task died (pod evicted, OOM, or the
    failure-handler commit itself failed) would count elapsed time upward
    forever until something restarts the pod. This loop is the steady-state
    backstop. ``reconcile_stale_runs`` is age-based and idempotent, so running
    it on every replica on an interval is safe -- a sibling's genuinely
    in-flight run isn't touched until it passes the stale threshold. The DB
    work runs in a thread so it never blocks the event loop, and one bad sweep
    is logged and the loop keeps going.
    """
    from app.services.run_service import reconcile_stale_runs
    from app.database import SessionLocal

    def _sweep() -> int:
        db = SessionLocal()
        try:
            return reconcile_stale_runs(db)
        finally:
            db.close()

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            swept = await asyncio.to_thread(_sweep)
            if swept:
                logger.info("Periodic reaper marked %d stale run(s) failed", swept)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning(
                "Periodic stale-run reaper sweep failed; will retry next interval",
                exc_info=True,
            )


# Auth modes with a real credential check, allowed on a deployed (S3) instance.
# Only "saml" is implemented today (see auth_routes.login / auth.py); "oidc" is
# NOT here yet and is deliberately NOT listed -- allow it only once it exists and
# is proven to authenticate, or a config typo'd to "oidc" would boot into a
# locked-out (login 403s, SAML inactive) state. This is an ALLOWLIST on purpose:
# any mode not in it -- "simple", "none", a future permissive mode, a typo --
# fails closed on a deployment by default, so a new mode can't slip the guard
# just because nobody remembered to blocklist it (mrj4001 review on #111).
SECURE_AUTH_MODES = frozenset({"saml"})


def _guard_deployed_auth_mode(auth_mode: str | None, storage_backend: str, allow_simple: bool) -> None:
    """Fail closed if a deployed instance's auth mode isn't a known-secure one.

    On a real deployment (S3 storage backend) auth_config.yaml is expected to
    render a mode in SECURE_AUTH_MODES (auth.mode=saml). Anything else -- most
    often "simple" (email-allowlist login with NO credential, see
    auth_routes.login) from a broken or empty auth_config.yaml, but also any
    unrecognized/future mode -- means the instance can't authenticate the way a
    deployment must. Refuse to boot so the misconfiguration can't silently
    expose or brick the instance (issue #111). An operator who genuinely wants
    to override sets CVICHE_ALLOW_SIMPLE_AUTH=1. Local dev
    (storage_backend="local") is never affected.
    """
    # Normalize before comparing: config values can arrive with stray case or
    # whitespace ("S3", "s3 ", "Simple"). An exact-match compare would let those
    # slip past the guard and boot a deployed non-secure instance (bypass).
    # A missing/empty auth_mode resolves to "simple" here -- so a deployment
    # with no rendered auth.mode fails closed regardless of the caller's own
    # defaulting, while local dev (non-s3) stays unaffected.
    auth_mode = (auth_mode or "").strip().lower() or "simple"
    storage_backend = (storage_backend or "").strip().lower()
    if storage_backend != "s3" or auth_mode in SECURE_AUTH_MODES:
        return
    if auth_mode == "simple":
        detail = ("login is email-allowlist only with no credential")
    else:
        detail = (f"'{auth_mode}' is not in SECURE_AUTH_MODES "
                  f"(allowed: {', '.join(sorted(SECURE_AUTH_MODES))}). "
                  f"To add a new mode, update SECURE_AUTH_MODES in app/main.py "
                  f"only after it is implemented and verified.")
    msg = (
        f"[SECURITY] auth_mode={auth_mode} on a deployed (S3 storage) instance: "
        f"{detail}. Expected auth.mode=saml from auth_config.yaml -- check that "
        "the overlay's auth_config.yaml rendered."
    )
    if allow_simple:
        logger.warning("%s Proceeding anyway because CVICHE_ALLOW_SIMPLE_AUTH=1.", msg)
    else:
        raise RuntimeError(
            f"{msg} Refusing to start; set CVICHE_ALLOW_SIMPLE_AUTH=1 to override."
        )


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan events."""
    # Startup: Initialize database
    logger.info("🚀 Starting CViche Pipeline Viewer...")
    # init_db() emits CREATE TABLE IF NOT EXISTS via metadata.create_all().
    # In production the runtime DB role is DML-only (IAM-auth'd cviche_app_user
    # with SELECT/INSERT/UPDATE/DELETE) and Alembic owns schema via a separate
    # one-shot migrate Job. Set CVICHE_INIT_DB=0 in the EKS overlay so pods
    # don't fail at boot trying to issue DDL they aren't authorized for.
    if os.environ.get("CVICHE_INIT_DB", "1") == "1":
        init_db()
        logger.info("✅ Database initialized")
    else:
        logger.info("⏭️  Skipping init_db() (CVICHE_INIT_DB=0); Alembic owns schema.")
    from app.config_loader import seed_system_config, get_config_value
    from app.consent import load_consent_text, check_consent_integrity
    from app.services.run_service import reconcile_stale_runs
    from app.database import SessionLocal
    db = SessionLocal()
    try:
        seed_system_config(db)
        logger.info("✅ System config seeded")
        # Fail closed: a deployed (S3) instance must not silently fall back to
        # password-less email-allowlist auth if auth_config.yaml didn't render
        # auth.mode=saml (issue #111). No-op in local dev (local storage).
        # Raw, deliberately un-defaulted: _guard_deployed_auth_mode owns the
        # "missing/blank -> simple" normalization. Defaulting here too meant the
        # rule lived in two places and could drift apart on a later refactor,
        # with the caller's copy silently deciding what the guard sees (#277).
        auth_mode = get_config_value(db, "auth_mode")
        storage_backend, _ = get_config("s3", "CVICHE_STORAGE_BACKEND", default="local")
        _guard_deployed_auth_mode(
            auth_mode, storage_backend,
            allow_simple=os.environ.get("CVICHE_ALLOW_SIMPLE_AUTH") == "1",
        )
        logger.info("✅ Auth mode: %s", auth_mode or "simple (default)")
        from app.services.notifications import validate_configuration
        notif_status = validate_configuration()
        logger.info(
            "✅ Notifications: configured=%s valid=%s",
            notif_status["configured"], notif_status["valid"],
        )
        load_consent_text()
        logger.info("✅ Consent text loaded")
        check_consent_integrity(db)
        # Resolve runs orphaned by a previous restart so they don't hang
        # in "running" forever (the UI would count elapsed time up endlessly).
        swept = reconcile_stale_runs(db)
        if swept:
            logger.info("♻️  Reconciled %d stale run(s) from a previous restart", swept)
    finally:
        db.close()

    # Real-time broker: when CVICHE_REDIS_URL is set, pipeline events and
    # cancellation cross worker/replica boundaries via Redis; otherwise the
    # emitter and orchestrator use process-local state (single-worker behavior).
    from app.pipeline.redis_broker import broker_from_env
    from app.pipeline.event_emitter import event_emitter
    from app.pipeline import orchestrator as orchestrator_module
    broker = broker_from_env()
    app.state.broker = broker
    event_emitter.set_broker(broker)
    orchestrator_module.set_broker(broker)
    await event_emitter.startup()
    if broker.enabled:
        logger.info("✅ Redis broker enabled (cross-worker events + cancellation)")
    else:
        logger.info("ℹ️  Redis broker disabled — in-process events (single-worker mode)")

    # Steady-state backstop to the startup reconcile: periodically reap runs
    # left stuck in "running" by a pod death mid-run. Set the interval to 0 to
    # disable. Age-based + idempotent, so it is multi-replica safe.
    reap_interval_raw, _ = get_config(
        "llm", "CVICHE_STALE_RUN_REAP_INTERVAL_SECONDS", default=300
    )
    try:
        reap_interval = int(reap_interval_raw)
    except (TypeError, ValueError):
        reap_interval = 300
    reaper_task = (
        asyncio.create_task(_stale_run_reaper_loop(reap_interval))
        if reap_interval > 0 else None
    )
    if reaper_task:
        print(f"♻️  Periodic stale-run reaper started (every {reap_interval}s)")

    yield

    # Shutdown: cancel the reaper, stop the subscriber loop, close broker conns.
    if reaper_task:
        reaper_task.cancel()
        try:
            await reaper_task
        except asyncio.CancelledError:
            pass
    await event_emitter.shutdown()
    await broker.shutdown()
    from app.services.notifications import flush as flush_notifications
    flush_notifications()
    logger.info("👋 Shutting down CViche Pipeline Viewer")


# Create FastAPI app
app = FastAPI(
    title="CViche Pipeline Viewer",
    description="Web interface for CViche with real-time progress tracking",
    version="1.0.0",
    lifespan=lifespan,
    # API docs are opt-in (see _docs_enabled); openapi_url=None also disables
    # the schema endpoint the docs pages are rendered from.
    docs_url="/docs" if _DOCS_ENABLED else None,
    redoc_url="/redoc" if _DOCS_ENABLED else None,
    openapi_url="/openapi.json" if _DOCS_ENABLED else None,
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

# Request ID middleware: added LAST so it is the outermost layer. The
# ContextVar it sets must be populated before any other middleware logs.
app.add_middleware(RequestIDMiddleware)

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
        "docs": "/docs" if _DOCS_ENABLED else None,
        "status": "running"
    }


@app.get("/livez")
async def livez():
    """Liveness probe -- the process is up.

    Does NOT check downstream dependencies. If this endpoint fails the right
    response is to restart the container, so it must never call the DB,
    S3, or any other network resource.
    """
    return {"status": "ok"}


@app.get("/readyz")
def readyz(response: Response, db: Session = Depends(get_db)):
    """Readiness probe -- downstream dependencies are reachable.

    Returns 503 with a JSON body naming the failing check(s) if the database
    is unreachable or the configured S3 bucket cannot be reached. A failing
    /readyz should de-list the replica from the load balancer; it should
    NOT trigger a restart.
    """
    checks: dict[str, dict] = {}

    cviche_storage_backend, source = get_config("s3", "CVICHE_STORAGE_BACKEND", default="local")
    
    try:
        db.execute(text("SELECT 1"))
        checks["db"] = {"ok": True}
    except Exception as exc:
        checks["db"] = {"ok": False, "error": str(exc)}

    from app.services.notifications import validate_configuration
    notif_status = validate_configuration()
    checks["notifications"] = {"ok": True, **notif_status}

    storage_backend = cviche_storage_backend
    if storage_backend == "s3":
        # Default "" not "local": an unset bucket must hit the clean "not set"
        # error below rather than head_bucket a phantom bucket named "local"
        # and fail with a confusing AWS error (issue #109).
        bucket, source = get_config("s3", "CVICHE_S3_BUCKET", default="")
        if not bucket:
            checks["s3"] = {"ok": False, "error": "CVICHE_S3_BUCKET not set"}
        else:
            try:
                import boto3
                from botocore.config import Config
                s3 = boto3.client(
                    "s3",
                    config=Config(
                        connect_timeout=2,
                        read_timeout=2,
                        retries={"max_attempts": 1},
                    ),
                )
                s3.head_bucket(Bucket=bucket)
                checks["s3"] = {"ok": True}
            except Exception as exc:
                checks["s3"] = {"ok": False, "error": str(exc)}

    all_ok = all(c["ok"] for c in checks.values())
    if not all_ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"status": "ready" if all_ok else "not_ready", "checks": checks}


@app.get("/health")
async def health():
    """Deprecated alias for /livez. Kept so existing healthcheck wiring keeps
    working; new probes should use /livez (liveness) or /readyz (readiness)."""
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
