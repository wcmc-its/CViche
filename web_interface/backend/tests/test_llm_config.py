"""
Tests for LLM config system: YAML loading, stage overrides, env var overrides,
nested PRICING dict, and calculate_cost().

Covers requirements: CFG-01, CFG-02, CFG-03
"""
import os
import sys
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

# Add src/ to path so unified_pipeline.config is importable
# From tests/ -> backend/ -> web_interface/ -> project_root/ -> src/
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent / "src"))

from unified_pipeline.config import (
    COST_ESTIMATE_ANCHOR_RATE,
    DEFAULT_MODEL,
    PRICING,
    calculate_cost,
    estimate_cost_per_1k_doc_tokens,
    estimate_run_cost_usd,
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
    # us. regional (cross-region inference) rates, as billed.
    assert PRICING["bedrock"]["anthropic.claude-sonnet-4-6"]["input"] == 3.300
    assert PRICING["bedrock"]["anthropic.claude-sonnet-4-6"]["output"] == 16.500
    assert PRICING["bedrock"]["anthropic.claude-haiku-4-5"]["input"] == 1.100
    assert PRICING["bedrock"]["anthropic.claude-haiku-4-5"]["output"] == 5.500
    # Opus 4.6/4.7 list at $5/$25 (not the Opus 4/4.1 $15/$75); regional 1.1x.
    for opus in ("anthropic.claude-opus-4-6", "anthropic.claude-opus-4-7"):
        assert PRICING["bedrock"][opus] == {"input": 5.500, "output": 27.500}


# ---------------------------------------------------------------------------
# calculate_cost with provider support
# ---------------------------------------------------------------------------

def test_calculate_cost_with_provider():
    """calculate_cost accepts provider parameter for multi-provider pricing."""
    # 1M input + 1M output tokens at anthropic.claude-haiku-4-5: 1.100 + 5.500 = 6.600
    cost = calculate_cost(1_000_000, 1_000_000, model="anthropic.claude-haiku-4-5", provider="bedrock")
    assert cost == pytest.approx(6.600)


def test_calculate_cost_backward_compat():
    """calculate_cost still works without model/provider args (falls back to
    DEFAULT_MODEL on the bedrock provider)."""
    cost = calculate_cost(1_000_000, 1_000_000)
    # DEFAULT_MODEL is us.anthropic.claude-sonnet-5: 2.200 + 11.000 = 13.200
    assert cost == pytest.approx(13.200)


def test_calculate_cost_unknown_model_fallback():
    """Unknown model falls back to the Bedrock default model's pricing without error."""
    cost = calculate_cost(1_000_000, 1_000_000, model="unknown-model", provider="bedrock")
    # Should fall back to DEFAULT_MODEL (Sonnet 5): 2.200 + 11.000 = 13.200
    assert cost == pytest.approx(13.200)


def test_bedrock_region_prefix_is_stripped():
    """A us./eu. inference-profile prefix resolves to the bare PRICING key.

    Uses the dated Haiku 4.5 id (whose price differs from the Sonnet-4.6
    fallback the unknown-model branch would otherwise return) so a broken
    prefix-strip actually fails this test instead of coincidentally landing
    on the same number.
    """
    cost = calculate_cost(
        1_000_000, 1_000_000,
        model="us.anthropic.claude-haiku-4-5-20251001-v1:0", provider="bedrock",
    )
    # Haiku 4.5: 1.100 + 5.500 = 6.600, via the region-prefix-stripped match
    # -- not the Sonnet 5 DEFAULT_MODEL fallback (13.200), which the
    # previous version of this test could not distinguish from a real fix.
    assert cost == pytest.approx(6.600)


def test_cost_estimate_anchor_is_at_billed_regional_rate():
    """The /estimate anchor carries the same 1.1x us.-regional premium as
    PRICING: 0.10125 (list-price anchor) * 1.1. For the anchor model itself
    the rescale ratio is 1, so the estimate equals the anchor."""
    assert COST_ESTIMATE_ANCHOR_RATE == pytest.approx(0.10125 * 1.1)
    rate = estimate_cost_per_1k_doc_tokens(model="us.anthropic.claude-sonnet-4-6", provider="bedrock")
    assert rate == pytest.approx(0.111375)


def test_dated_haiku_4_5_id_prices_as_haiku_not_fallback():
    """Regression: the FULL versioned Haiku 4.5 id Bedrock requires for stage 3b
    must price at Haiku rates. The bare alias is rejected by Bedrock and
    _normalize_model_id strips only the region prefix, so the dated id needs its
    own PRICING entry -- otherwise stage 3b (the dominant cost stage) silently
    falls back to the Bedrock default model's rates and reported run cost is
    misstated."""
    model = "us.anthropic.claude-haiku-4-5-20251001-v1:0"
    cost = calculate_cost(1_000_000, 1_000_000, model=model, provider="bedrock")
    # Haiku 4.5: 1.100 + 5.500 = 6.600; the Sonnet 5 fallback would be 13.200.
    assert cost == pytest.approx(6.600)
    assert cost != pytest.approx(13.200)


def test_sonnet_5_prices_as_sonnet_5_not_fallback():
    """Without its own PRICING entry, Sonnet 5 falls back to Sonnet 4.6's rates
    and every recorded cost is overstated by 50% (the #76 failure mode)."""
    cost = calculate_cost(1_000_000, 1_000_000, model="us.anthropic.claude-sonnet-5", provider="bedrock")
    assert cost == pytest.approx(2.200 + 11.000)


def test_estimate_for_sonnet_5_carries_its_tokenizer_inflation():
    """A list-price rescale alone would quote Sonnet 5 at 0.667x of Sonnet 4.6;
    its tokenizer spends 1.43x input / 1.20x output tokens, so the quote must be
    ~0.87x -- close to the 0.842x real cost the A/B measured (docs/adr/0001)."""
    s46 = estimate_cost_per_1k_doc_tokens(model="us.anthropic.claude-sonnet-4-6", provider="bedrock")
    s5 = estimate_cost_per_1k_doc_tokens(model="us.anthropic.claude-sonnet-5", provider="bedrock")
    assert s5 / s46 == pytest.approx((0.8 * 2.2 * 1.43 + 0.2 * 11.0 * 1.20) / (0.8 * 3.3 + 0.2 * 16.5))
    assert 0.84 < s5 / s46 < 0.90


def test_run_estimate_for_sonnet_5_carries_its_tokenizer_inflation():
    """The run-cost estimator behind /estimate prices through the same
    inflation. Without it, switching the default to Sonnet 5 would cut the
    quote by the 33% list-price gap, far below the ~0.86x whole-run cost the
    A/B measured -- the under-quote #538 fixed. With it the quote is cheaper
    than Sonnet 4.6's but never below that measured ratio."""
    s46 = estimate_run_cost_usd(40_000, model="us.anthropic.claude-sonnet-4-6", provider="bedrock")
    s5 = estimate_run_cost_usd(40_000, model="us.anthropic.claude-sonnet-5", provider="bedrock")
    assert _SONNET_5_MEASURED_WHOLE_RUN_RATIO <= s5 / s46 < 1.0


# Whole-run Sonnet 5 / Sonnet 4.6 cost on the 2026-09-29 10-CV A/B: the
# Sonnet stages at 0.842x, with stage 3b (Haiku, unchanged) carried over.
_SONNET_5_MEASURED_WHOLE_RUN_RATIO = 0.86
