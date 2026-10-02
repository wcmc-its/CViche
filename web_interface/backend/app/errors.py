"""Consistent HTTPException factory functions.

Every error response produced by CViche uses the same envelope:
    {"error": "<code>", "message": "<human-readable text>"}

Route handlers and service functions raise these instead of constructing
HTTPException(detail=...) inline, guaranteeing a uniform contract for the
frontend error-handling layer.
"""
from fastapi import HTTPException

# Error code the upload UI keys on to offer "Run it again" (#1286).
DUPLICATE_FILE_ERROR = "duplicate_file"


def not_found(message: str = "Resource not found") -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "not_found", "message": message})


def bad_request(message: str, error_code: str = "bad_request") -> HTTPException:
    return HTTPException(status_code=400, detail={"error": error_code, "message": message})


def duplicate_file(message: str, last_processed_on: str, run_id: str | None) -> HTTPException:
    """409: this exact file was already processed; resend with confirm_duplicate=true to run it again."""
    body = {"error": DUPLICATE_FILE_ERROR, "message": message, "last_processed_on": last_processed_on}
    if run_id is not None:
        body["run_id"] = run_id
    return HTTPException(status_code=409, detail=body)


def forbidden(message: str = "Access denied") -> HTTPException:
    return HTTPException(status_code=403, detail={"error": "forbidden", "message": message})


def rate_limited(message: str, details: dict | None = None) -> HTTPException:
    body = {"error": "rate_limited", "message": message}
    if details:
        body["details"] = details
    return HTTPException(status_code=429, detail=body)


def validation_error(message: str) -> HTTPException:
    return HTTPException(status_code=422, detail={"error": "validation_error", "message": message})


def internal_error(message: str = "An unexpected error occurred.") -> HTTPException:
    return HTTPException(status_code=500, detail={"error": "internal_error", "message": message})


def conflict(message: str) -> HTTPException:
    return HTTPException(status_code=409, detail={"error": "conflict", "message": message})


def unauthorized(message: str = "Authentication required. Please log in.") -> HTTPException:
    return HTTPException(status_code=401, detail={"error": "auth_required", "message": message})
