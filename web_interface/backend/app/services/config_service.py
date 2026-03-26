"""Centralized configuration constants with environment variable overrides.

All hardcoded values that were previously scattered across route handlers
are collected here. Each constant can be overridden via an environment variable.
"""
import os

# Session
SESSION_TTL = int(os.environ.get("CVICHE_SESSION_TTL", 7 * 24 * 3600))

# Auth rate limiting (in-memory login attempt limiter)
LOGIN_RATE_LIMIT_MAX = int(os.environ.get("CVICHE_LOGIN_RATE_LIMIT", 10))
LOGIN_RATE_LIMIT_WINDOW = int(os.environ.get("CVICHE_LOGIN_RATE_WINDOW", 60))

# Upload
MAX_UPLOAD_SIZE = int(os.environ.get("CVICHE_MAX_UPLOAD_MB", 50)) * 1024 * 1024

# Cost estimation (empirical rates from actual pipeline runs)
COST_PER_1K_TOKENS = float(os.environ.get("CVICHE_COST_PER_1K_TOKENS", 0.075))
TIME_PER_1K_TOKENS = int(os.environ.get("CVICHE_TIME_PER_1K_TOKENS", 30))
BASE_OVERHEAD_SECONDS = int(os.environ.get("CVICHE_BASE_OVERHEAD_SECONDS", 60))
