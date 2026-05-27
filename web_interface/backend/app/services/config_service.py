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

# Upload. This is the safety-net fallback when CVICHE_MAX_UPLOAD_MB is unset;
# the operational cap should be set explicitly per environment. CVs are small
# (a text .docx is well under 1 MB; an image-heavy/scanned PDF rarely exceeds
# ~10 MB), so the default is intentionally conservative to limit resource abuse.
MAX_UPLOAD_SIZE = int(os.environ.get("CVICHE_MAX_UPLOAD_MB", 10)) * 1024 * 1024

# Cost / time estimation for the /estimate endpoint.
TIME_PER_1K_TOKENS = int(os.environ.get("CVICHE_TIME_PER_1K_TOKENS", 30))
BASE_OVERHEAD_SECONDS = int(os.environ.get("CVICHE_BASE_OVERHEAD_SECONDS", 60))

# The cost rate (USD per 1,000 document tokens) is derived from the model
# configured in llm_config.yaml so the estimate tracks the active preset.
# CVICHE_COST_PER_1K_TOKENS, if set, pins it to an explicit value instead.
_COST_PER_1K_TOKENS_OVERRIDE = os.environ.get("CVICHE_COST_PER_1K_TOKENS")


def get_cost_per_1k_tokens() -> float:
    """USD cost per 1,000 document tokens, for the /estimate endpoint.

    Honors an explicit CVICHE_COST_PER_1K_TOKENS override; otherwise derives
    the rate from the model configured in llm_config.yaml.
    """
    if _COST_PER_1K_TOKENS_OVERRIDE:
        return float(_COST_PER_1K_TOKENS_OVERRIDE)
    try:
        from unified_pipeline.config import estimate_cost_per_1k_doc_tokens
        return estimate_cost_per_1k_doc_tokens()
    except Exception:  # pragma: no cover - defensive fallback
        return 0.075


def get_estimate_model_name() -> str:
    """Friendly name of the model the cost estimate is based on (for display)."""
    try:
        from unified_pipeline.config import get_stage_config, friendly_model_name
        return friendly_model_name(get_stage_config("default")["model"])
    except Exception:  # pragma: no cover - defensive fallback
        return "the configured model"
