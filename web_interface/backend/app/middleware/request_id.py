"""Request ID middleware.

Generates (or honors an incoming) request id for every HTTP request, exposes
it via the ``request_id_var`` ContextVar in :mod:`app.logging_config` so logs
can be correlated, and echoes it back on the response as ``X-Request-ID``.
"""
from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.logging_config import new_request_id, request_id_var

_HEADER = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Honor an incoming X-Request-ID header or mint a fresh id.

    Must be the outermost middleware (add it last in ``main.py``) so the
    request_id is set before any other middleware logs anything.
    """

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get(_HEADER)
        request_id = incoming if incoming and len(incoming) <= 200 else new_request_id()
        token = request_id_var.set(request_id)
        try:
            response: Response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers[_HEADER] = request_id
        return response
