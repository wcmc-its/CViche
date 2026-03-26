"""Consistent HTTPException factory functions.

Every error response produced by CViche uses the same envelope:
    {"error": "<code>", "message": "<human-readable text>"}

Route handlers and service functions raise these instead of constructing
HTTPException(detail=...) inline, guaranteeing a uniform contract for the
frontend error-handling layer.
"""
from fastapi import HTTPException


def not_found(message: str = "Resource not found") -> HTTPException:
    return HTTPException(status_code=404, detail={"error": "not_found", "message": message})


def bad_request(message: str, error_code: str = "bad_request") -> HTTPException:
    return HTTPException(status_code=400, detail={"error": error_code, "message": message})


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
