"""Request ID middleware.

Generates (or honors an incoming) request id for every HTTP request, exposes
it via the ``request_id_var`` ContextVar in :mod:`app.logging_config` so logs
can be correlated, and echoes it back on the response as ``X-Request-ID``.

The id is a log-correlation label only: never an identity, credential or
idempotency key (#2078). An incoming id is honored only when it matches
``_VALID_ID``, so a client can't put spaces, quotes or control characters
into log lines; anything else gets a fresh id.
"""
from __future__ import annotations

import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.logging_config import new_request_id, request_id_var

_HEADER = "X-Request-ID"
# Covers uuid hex and AWS-style trace ids (``Root=1-abc-def``).
_VALID_ID = re.compile(r"[A-Za-z0-9._:=-]{1,128}")


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Honor an incoming X-Request-ID header or mint a fresh id.

    Must be the outermost middleware (add it last in ``main.py``) so the
    request_id is set before any other middleware logs anything.
    """

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(_HEADER)
        request_id = (
            incoming
            if incoming and _VALID_ID.fullmatch(incoming)
            else new_request_id()
        )
        token = request_id_var.set(request_id)
        try:
            response: Response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[_HEADER] = request_id
        return response
