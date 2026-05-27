"""Centralized configuration constants with environment variable overrides.

All hardcoded values that were previously scattered across route handlers
are collected here. Each constant can be overridden via an environment variable.
"""
import os

from app.config_loader import get_config
# Session

SESSION_TTL, source = int(get_config("auth", "CVICHE_SESSION_TTL", default=7 * 24 * 3600)[0])

# Auth rate limiting (in-memory login attempt limiter)
LOGIN_RATE_LIMIT_MAX, source = int(get_config("auth", "CVICHE_LOGIN_RATE_LIMIT", default=10)[0])
LOGIN_RATE_LIMIT_WINDOW, source = int(get_config("auth", "CVICHE_LOGIN_RATE_WINDOW", default=60)[0])

# Upload
MAX_UPLOAD_SIZE, source = int(get_config("auth", "CVICHE_MAX_UPLOAD_MB", default=10)[0])*1024*1024

# Cost / time estimation for the /estimate endpoint.
TIME_PER_1K_TOKENS, source = int(get_config("auth", "CVICHE_TIME_PER_1K_TOKENS", default=30)[0])
BASE_OVERHEAD_SECONDS, source = int(get_config("auth", "CVICHE_BASE_OVERHEAD_SECONDS", default=60)[0])

# The cost rate (USD per 1,000 document tokens) is derived from the model
# configured in llm_config.yaml so the estimate tracks the active preset.
# CVICHE_COST_PER_1K_TOKENS, if set, pins it to an explicit value instead.
_COST_PER_1K_TOKENS_OVERRIDE, source = get_config("auth", "CVICHE_COST_PER_1K_TOKENS", default=0)



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


def get_estimated_run_cost(text_char_count: int) -> tuple[float, float]:
    """(min, max) USD cost estimate for a full pipeline run on a CV.

    Uses the entry-classification-aware model in unified_pipeline.config, which
    accounts for stage 3b re-sending its large static prompt once per hierarchy
    group -- the dominant, entry-count-driven cost a flat per-token rate misses.
    The band is intentionally wide because entry count is estimated from
    document text. Falls back to the legacy per-token rate if anything fails.
    """
    try:
        from unified_pipeline.config import estimate_run_cost_usd
        point = estimate_run_cost_usd(text_char_count)
    except Exception:  # pragma: no cover - defensive fallback
        point = (text_char_count / 4 / 1000) * get_cost_per_1k_tokens()

    cost_min = max(point * 0.7, 0.10)
    cost_max = max(point * 1.5, cost_min * 1.3)
    return round(cost_min, 3), round(cost_max, 3)


def get_estimate_model_name() -> str:
    """Friendly name of the model the cost estimate is based on (for display)."""
    try:
        from unified_pipeline.config import get_stage_config, friendly_model_name
        return friendly_model_name(get_stage_config("default")["model"])
    except Exception:  # pragma: no cover - defensive fallback
        return "the configured model"
