"""Application logging configuration.

Two output formats:

- ``text`` (default in dev): compact human-readable lines, prefixed with the
  request_id when one is set by ``RequestIDMiddleware``.
- ``json`` (default in prod): one JSON object per log line. EKS / CloudWatch /
  any log shipper can parse the fields directly.

Configure via env:

- ``CVICHE_LOG_FORMAT``: ``json`` or ``text``. Default ``text``.
- ``CVICHE_LOG_LEVEL``: standard Python log level name. Default ``INFO``.

The configuration is applied via ``logging.config.dictConfig`` once at startup
and overrides uvicorn's bundled formatters so access and error logs share the
same shape. Call :func:`configure_logging` exactly once, as early as possible
(see ``app.main``).
"""
from __future__ import annotations

import contextvars
import json
import logging
import logging.config
import os
import time
import uuid

# ContextVar populated by RequestIDMiddleware per request. The logging filter
# reads it onto every LogRecord so the formatter can include it. Falls back
# to "-" when no request is in scope (startup, background tasks).
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)

# Optional correlation IDs the formatters surface when present. Callers attach
# them via ``logger.info("msg", extra={"user_id": ..., "run_id": ...})``;
# unattached LogRecords carry no field and the formatters skip them.
_EXTRA_FIELDS = ("user_id", "run_id")


def new_request_id() -> str:
    """Generate a fresh request id. Public so the middleware can call it."""
    return uuid.uuid4().hex


class RequestIDFilter(logging.Filter):
    """Inject the ContextVar's request_id onto every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = request_id_var.get()
        return True


class JSONFormatter(logging.Formatter):
    """Render a LogRecord as a single JSON object.

    Always emits: timestamp, level, logger, message, request_id.
    Optional fields (user_id, run_id) only appear when the caller passed them
    via ``extra={...}``. Exceptions are flattened into an ``exc_info`` string
    so the line stays valid JSON.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)
            )
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _build_config(level: str, fmt: str) -> dict:
    """Build the dictConfig payload."""
    formatters = {
        "text": {
            "format": "%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
        "json": {
            "()": "app.logging_config.JSONFormatter",
        },
    }
    formatter_name = "json" if fmt == "json" else "text"

    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "request_id": {"()": "app.logging_config.RequestIDFilter"},
        },
        "formatters": formatters,
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": formatter_name,
                "filters": ["request_id"],
                "stream": "ext://sys.stdout",
            },
        },
        "root": {
            "level": level,
            "handlers": ["default"],
        },
        "loggers": {
            # Make uvicorn's three internal loggers share our formatter so a
            # CloudWatch reader sees one shape for every line.
            "uvicorn": {"handlers": ["default"], "level": level, "propagate": False},
            "uvicorn.error": {
                "handlers": ["default"],
                "level": level,
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["default"],
                "level": level,
                "propagate": False,
            },
            # AWS SDK is chatty; pin to WARNING unless explicitly raised.
            "boto3": {"level": "WARNING"},
            "botocore": {"level": "WARNING"},
            "urllib3": {"level": "WARNING"},
            "s3transfer": {"level": "WARNING"},
        },
    }


def configure_logging(
    level: str | None = None,
    fmt: str | None = None,
) -> None:
    """Apply the logging configuration. Idempotent.

    Parameters override env vars; env vars override defaults.
    """
    resolved_level = (level or os.environ.get("CVICHE_LOG_LEVEL", "INFO")).upper()
    resolved_fmt = (fmt or os.environ.get("CVICHE_LOG_FORMAT", "text")).lower()
    if resolved_fmt not in ("text", "json"):
        resolved_fmt = "text"
    logging.config.dictConfig(_build_config(resolved_level, resolved_fmt))
