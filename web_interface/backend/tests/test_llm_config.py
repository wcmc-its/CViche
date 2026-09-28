"""
Tests for LLM config system: YAML loading, stage overrides, env var overrides,
nested PRICING dict, and calculate_cost().

Covers requirements: CFG-01, CFG-02, CFG-03
"""
import os
import sys
from pathlib import Path
from unittest.mock import patch, mock_open

import pytest

# Add src/ to path so unified_pipeline.config is importable
# From tests/ -> backend/ -> web_interface/ -> project_root/ -> src/
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from unified_pipeline.config import (
    DEFAULT_MODEL,
    PRICING,
    calculate_cost,
    get_stage_config,
    reload_config,
)


@pytest.fixture(autouse=True)
def reset_config_cache():
    """Reset the config cache before and after each test."""
    reload_config()
    yield
    reload_config()


# ---------------------------------------------------------------------------
# YAML config loading and defaults (CFG-01)
# ---------------------------------------------------------------------------

def test_yaml_default_config():
    """When YAML has default block, get_stage_config returns those defaults."""
    yaml_content = """
default:
  provider: bedrock
  model: anthropic.claude-haiku-4-5
  temperature: 0
  retry_count: 3
stages: {}
"""
    with patch("unified_pipeline.config.Path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=yaml_content)):
        reload_config()
        cfg = get_stage_config("stage_2")
    assert cfg["provider"] == "bedrock"
    assert cfg["model"] == "anthropic.claude-haiku-4-5"
    assert cfg["temperature"] == 0
    assert cfg["max_tokens"] is None
    assert cfg["retry_count"] == 3


def test_missing_yaml_fallback():
    """When llm_config.yaml does not exist, hardcoded defaults are used."""
    with patch("unified_pipeline.config.Path.exists", return_value=False):
        reload_config()
        cfg = get_stage_config("stage_2")
    assert cfg["provider"] == "bedrock"
    assert cfg["model"] == DEFAULT_MODEL
    assert cfg["temperature"] == 0


def test_malformed_yaml_fallback():
    """When llm_config.yaml has invalid content, hardcoded defaults are used."""
    with patch("unified_pipeline.config.Path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=": invalid: yaml: [[")):
        reload_config()
        cfg = get_stage_config("stage_2")
    assert cfg["provider"] == "bedrock"
    assert cfg["model"] == DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Per-stage overrides (CFG-02)
# ---------------------------------------------------------------------------

def test_stage_override():
    """Stage-specific override changes model while other stages keep default."""
    yaml_content = """
default:
  provider: bedrock
  model: us.anthropic.claude-sonnet-4-6
  temperature: 0
  retry_count: 3
stages:
  stage_4:
    model: anthropic.claude-haiku-4-5
"""
    with patch("unified_pipeline.config.Path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=yaml_content)):
        reload_config()
        cfg_4 = get_stage_config("stage_4")
        cfg_2 = get_stage_config("stage_2")
    assert cfg_4["model"] == "anthropic.claude-haiku-4-5"
    assert cfg_4["provider"] == "bedrock"  # inherited from default
    assert cfg_2["model"] == "us.anthropic.claude-sonnet-4-6"


def test_stage_override_provider_and_model():
    """Stage override can change both model and temperature."""
    yaml_content = """
default:
  provider: bedrock
  model: us.anthropic.claude-sonnet-4-6
  temperature: 0
  retry_count: 3
stages:
  stage_3b:
    model: anthropic.claude-haiku-4-5-20251001-v1:0
    temperature: 0.2
"""
    with patch("unified_pipeline.config.Path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=yaml_content)):
        reload_config()
        cfg = get_stage_config("stage_3b")
    assert cfg["provider"] == "bedrock"
    assert cfg["model"] == "anthropic.claude-haiku-4-5-20251001-v1:0"
    assert cfg["temperature"] == 0.2


# ---------------------------------------------------------------------------
# Environment variable overrides (CFG-03)
# ---------------------------------------------------------------------------

def test_env_var_override_provider():
    """CVICHE_LLM_PROVIDER env var overrides the global default provider."""
    yaml_content = """
default:
  provider: bedrock
  model: us.anthropic.claude-sonnet-4-6
  temperature: 0
stages: {}
"""
    with patch("unified_pipeline.config.Path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=yaml_content)), \
         patch.dict(os.environ, {"CVICHE_LLM_PROVIDER": "azure"}):
        reload_config()
        cfg = get_stage_config("stage_2")
    assert cfg["provider"] == "azure"


def test_env_var_override_model():
    """CVICHE_LLM_MODEL env var overrides the global default model."""
    yaml_content = """
default:
  provider: bedrock
  model: us.anthropic.claude-sonnet-4-6
  temperature: 0
stages: {}
"""
    with patch("unified_pipeline.config.Path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=yaml_content)), \
         patch.dict(os.environ, {"CVICHE_LLM_MODEL": "anthropic.claude-haiku-4-5"}):
        reload_config()
        cfg = get_stage_config("stage_2")
    assert cfg["model"] == "anthropic.claude-haiku-4-5"


def test_env_var_does_not_override_stage_specific():
    """Stage-specific YAML overrides take precedence over env vars."""
    yaml_content = """
default:
  provider: bedrock
  model: us.anthropic.claude-sonnet-4-6
  temperature: 0
stages:
  stage_4:
    provider: bedrock
    model: anthropic.claude-haiku-4-5
"""
    with patch("unified_pipeline.config.Path.exists", return_value=True), \
         patch("builtins.open", mock_open(read_data=yaml_content)), \
         patch.dict(os.environ, {"CVICHE_LLM_PROVIDER": "azure"}):
        reload_config()
        cfg = get_stage_config("stage_4")
    # stage_4 has explicit provider in YAML, so env var does NOT override
    assert cfg["provider"] == "bedrock"


# ---------------------------------------------------------------------------
# PRICING structure
# ---------------------------------------------------------------------------

def test_pricing_nested_structure():
    """PRICING dict is nested: provider -> model -> input/output."""
    assert PRICING["bedrock"]["anthropic.claude-sonnet-4-6"]["input"] == 3.000
    assert PRICING["bedrock"]["anthropic.claude-sonnet-4-6"]["output"] == 15.000
    assert PRICING["bedrock"]["anthropic.claude-haiku-4-5"]["input"] == 1.000
    assert PRICING["bedrock"]["anthropic.claude-haiku-4-5"]["output"] == 5.000


# ---------------------------------------------------------------------------
# calculate_cost with provider support
# ---------------------------------------------------------------------------

def test_calculate_cost_with_provider():
    """calculate_cost accepts provider parameter for multi-provider pricing."""
    # 1M input + 1M output tokens at anthropic.claude-haiku-4-5: 1.000 + 5.000 = 6.000
    cost = calculate_cost(1_000_000, 1_000_000, model="anthropic.claude-haiku-4-5", provider="bedrock")
    assert cost == pytest.approx(6.000)


def test_calculate_cost_backward_compat():
    """calculate_cost still works without model/provider args (falls back to
    DEFAULT_MODEL on the bedrock provider)."""
    cost = calculate_cost(1_000_000, 1_000_000)
    # DEFAULT_MODEL is us.anthropic.claude-sonnet-4-6: 3.000 + 15.000 = 18.000
    assert cost == pytest.approx(18.000)


def test_calculate_cost_unknown_model_fallback():
    """Unknown model falls back to the Bedrock default model's pricing without error."""
    cost = calculate_cost(1_000_000, 1_000_000, model="unknown-model", provider="bedrock")
    # Should fall back to DEFAULT_MODEL (Sonnet 4.6): 3.000 + 15.000 = 18.000
    assert cost == pytest.approx(18.000)


def test_bedrock_region_prefix_is_stripped():
    """A us./eu. inference-profile prefix resolves to the bare PRICING key."""
    cost = calculate_cost(
        1_000_000, 1_000_000,
        model="us.anthropic.claude-sonnet-4-6", provider="bedrock",
    )
    # Sonnet 4.6: 3.000 + 15.000 = 18.000, via the same direct-match branch
    # as DEFAULT_MODEL (not the unknown-model fallback).
    assert cost == pytest.approx(18.000)


def test_dated_haiku_4_5_id_prices_as_haiku_not_fallback():
    """Regression: the FULL versioned Haiku 4.5 id Bedrock requires for stage 3b
    must price at Haiku rates. The bare alias is rejected by Bedrock and
    _normalize_model_id strips only the region prefix, so the dated id needs its
    own PRICING entry -- otherwise stage 3b (the dominant cost stage) silently
    falls back to the Bedrock default model's rates and reported run cost is
    misstated."""
    model = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    cost = calculate_cost(1_000_000, 1_000_000, model=model, provider="bedrock")
    # Haiku 4.5: 1.000 + 5.000 = 6.000; the Sonnet-4-6 fallback would be 18.000.
    assert cost == pytest.approx(6.000)
    assert cost != pytest.approx(18.000)
