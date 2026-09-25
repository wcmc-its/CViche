"""
Centralized configuration for CV parsing pipeline.

This module provides configuration settings used by:
- CLI: run_pipeline.py
- Web App: web_interface/backend/app/pipeline/orchestrator.py
- Direct Python imports

Single source of truth for all paths, settings, and feature flags.
"""
from pathlib import Path
import logging
import math
import os
import yaml

logger = logging.getLogger(__name__)

# ============================================================================
# Project Structure
# ============================================================================

# Project root
PROJECT_ROOT = Path(__file__).parent.parent.parent

# ============================================================================
# Template Paths
# ============================================================================

# Priority order for WCM template lookup
TEMPLATE_PATHS = [
    PROJECT_ROOT / "business" / "examples" / "template" / "wcm_cv_template_faculty_october_2022_final .docx",
    PROJECT_ROOT / "data" / "templates" / "wcm_cv_template.docx",
    PROJECT_ROOT / "src" / "unified_pipeline" / "cv_parser" / "cv_template_wcm.docx",
]

# ============================================================================
# Output Directories
# ============================================================================

# CLI output directories
OUTPUT_BASE = PROJECT_ROOT / "src" / "unified_pipeline" / "outputs"
OUTPUT_STAGE_1A = OUTPUT_BASE / "stage_1a_segmentation"
OUTPUT_STAGE_1B = OUTPUT_BASE / "stage_1b_hierarchy_mapping"
OUTPUT_STAGE_2 = OUTPUT_BASE / "stage_2_entry_extraction"
OUTPUT_STAGE_4 = OUTPUT_BASE / "stage_4_wcm_templates"

# Legacy compatibility
OUTPUT_STAGE_1 = OUTPUT_STAGE_1A  # Alias for backward compatibility
OUTPUT_STAGE_2A = OUTPUT_BASE / "stage_2a_entry_delimitation"  # Deprecated
OUTPUT_STAGE_2B = OUTPUT_BASE / "stage_2b_extract_entries_from_delimiters"  # Deprecated

# Web app output directory
WEB_OUTPUT_BASE = PROJECT_ROOT / "web_interface" / "outputs"

# ============================================================================
# LLM Settings
# ============================================================================

# Model selection
DEFAULT_MODEL = "gpt-4o-mini"
SEGMENTATION_MODEL = "gpt-4o-mini"
TAXONOMY_MODEL = "gpt-4o-mini"
PARSING_MODEL = "gpt-4o-mini"

# LLM API pricing (per 1M tokens) -- nested by provider
PRICING = {
    "openai": {
        "gpt-4o-mini": {
            "input": 0.150,   # per 1M tokens
            "output": 0.600,
        },
        "gpt-4o": {
            "input": 2.50,
            "output": 10.00,
        },
        "gpt-5.1": {
            "input": 2.50,
            "output": 10.00,
        },
    },
    "bedrock": {
        # Anthropic Claude models -- per 1M tokens, identical to the direct
        # Anthropic API. Keyed by the BARE model ID; calculate_cost() strips
        # region inference-profile prefixes (us./eu./apac./global.) before
        # lookup, so "us.anthropic.claude-sonnet-4-6" resolves here.
        "anthropic.claude-sonnet-4-6": {
            "input": 3.000,
            "output": 15.000,
        },
        "anthropic.claude-haiku-4-5": {
            "input": 1.000,
            "output": 5.000,
        },
        # Bedrock requires the FULL versioned inference-profile id for Haiku 4.5
        # (the bare alias above is rejected by the API), and _normalize_model_id
        # strips only the region prefix -- not the dated -vN suffix. So the
        # configured stage_3b id resolves to this key, not the bare one above.
        # Same price as the bare entry; keep both in sync. (Sonnet 4.6 needs no
        # dated variant -- its inference-profile id carries no date.)
        "anthropic.claude-haiku-4-5-20251001-v1:0": {
            "input": 1.000,
            "output": 5.000,
        },
        "anthropic.claude-opus-4-7": {
            "input": 15.000,
            "output": 75.000,
        },
        "anthropic.claude-opus-4-6": {
            "input": 15.000,
            "output": 75.000,
        },
        # Older Claude 3.x generation -- kept for historical cost calculations
        "anthropic.claude-3-haiku-20240307-v1:0": {
            "input": 0.250,
            "output": 1.250,
        },
        "anthropic.claude-3-5-sonnet-20241022-v2:0": {
            "input": 3.000,
            "output": 15.000,
        },
        "anthropic.claude-3-5-haiku-20241022-v1:0": {
            "input": 0.800,
            "output": 4.000,
        },
        # Meta Llama models
        "meta.llama3-1-8b-instruct-v1:0": {
            "input": 0.200,
            "output": 0.250,
        },
        "meta.llama3-1-70b-instruct-v1:0": {
            "input": 0.350,
            "output": 0.450,
        },
        "meta.llama3-3-70b-instruct-v1:0": {
            "input": 0.350,
            "output": 0.450,
        },
        # Mistral models
        "mistral.mistral-7b-instruct-v0:2": {
            "input": 0.150,
            "output": 0.200,
        },
        "mistral.mixtral-8x7b-instruct-v0:1": {
            "input": 0.450,
            "output": 0.700,
        },
        "mistral.mistral-large-2407-v1:0": {
            "input": 2.700,
            "output": 8.100,
        },
    },
}

# Backward compatibility: flat dict for existing pipeline code
PRICING_FLAT = PRICING["openai"]

# ============================================================================
# Pipeline Features (Best of Both Worlds)
# ============================================================================

# Enable legacy advanced handlers for Stage 4
USE_LEGACY_HANDLERS = True  # Professional WCM formatting

# Advanced features (requires USE_LEGACY_HANDLERS=True)
ENABLE_PMCID_ENRICHMENT = True  # PubMed API lookup
ENABLE_AUTHOR_BOLDING = True  # Bold CV owner's name
ENABLE_AUTHOR_ABBREVIATION = True  # "Zahida Y" not "Yaseen Zahida"
ENABLE_SUBSECTION_CATEGORIZATION = True  # S1-S15 publication categories

# Stage 3 parsing behavior
USE_TAXONOMY_MAPPING = True  # Use Stage 2 LLM mappings (not keyword matching)

# ============================================================================
# Legacy Handler Paths
# ============================================================================

LEGACY_BASE = PROJECT_ROOT / "src" / "legacy" / "stage_based_extraction"
LEGACY_PRODUCTION = LEGACY_BASE / "scripts" / "production"

# ============================================================================
# Helper Functions
# ============================================================================

def get_template_path() -> Path:
    """
    Get WCM template path with automatic fallback.

    Returns:
        Path: Path to WCM template file

    Raises:
        FileNotFoundError: If no template found in any location
    """
    for path in TEMPLATE_PATHS:
        if path.exists():
            return path

    # No template found
    raise FileNotFoundError(
        "WCM template not found. Tried:\n" +
        "\n".join(f"  - {p}" for p in TEMPLATE_PATHS) +
        "\n\nPlease ensure the WCM template is available in one of these locations."
    )


# Region prefixes used by Bedrock cross-region inference-profile IDs, e.g.
# "us.anthropic.claude-sonnet-4-6". PRICING is keyed by the bare model ID.
_BEDROCK_REGION_PREFIXES = ("us-gov.", "us.", "eu.", "apac.", "global.")

# Models already warned about (missing PRICING) -- avoids per-call log spam.
_warned_missing_pricing = set()


def _normalize_model_id(model: str) -> str:
    """Strip a Bedrock cross-region inference-profile prefix from a model ID."""
    if not model:
        return model
    for prefix in _BEDROCK_REGION_PREFIXES:
        if model.startswith(prefix):
            return model[len(prefix):]
    return model


# Bedrock / Anthropic prompt-caching multipliers, applied against the model's
# base input price. Bedrock charges the same cache prices as Anthropic's
# direct API (see docs/LLM_MODELS.md).
CACHE_READ_PRICE_MULTIPLIER = 0.1   # cache hits cost 0.1x input
CACHE_WRITE_PRICE_MULTIPLIER = 1.25  # cache writes cost 1.25x input


def calculate_cost(prompt_tokens: int, completion_tokens: int,
                   model: str = None, provider: str = "openai",
                   cache_read_tokens: int = 0,
                   cache_write_tokens: int = 0) -> float:
    """
    Calculate cost for an LLM API call.

    Args:
        prompt_tokens: Number of uncached input tokens. With Bedrock prompt
            caching enabled, this should be the *uncached* portion only --
            Bedrock's `usage.inputTokens` already excludes cached tokens.
        completion_tokens: Number of output tokens
        model: Model name/ID (default: DEFAULT_MODEL). Bedrock region
            inference-profile prefixes (us./eu./apac./global.) are stripped
            before the PRICING lookup.
        provider: LLM provider name (default: "openai")
        cache_read_tokens: Input tokens served from prompt cache, billed at
            0.1x the input rate. 0 when caching is off or unsupported.
        cache_write_tokens: Input tokens written to prompt cache on this
            call, billed at 1.25x the input rate. 0 when caching is off or
            unsupported.

    Returns:
        float: Cost in USD
    """
    model = model or DEFAULT_MODEL

    provider_pricing = PRICING.get(provider, PRICING.get("openai", {}))

    # Resolve the pricing key: exact match first, then region-prefix-stripped.
    lookup = model if model in provider_pricing else _normalize_model_id(model)

    if lookup not in provider_pricing:
        if model not in _warned_missing_pricing:
            _warned_missing_pricing.add(model)
            logger.warning(
                "No PRICING entry for model %r (provider %r); falling back to "
                "gpt-4o-mini pricing -- reported cost will be inaccurate. Add "
                "the model to PRICING in config.py.", model, provider,
            )
        provider_pricing = PRICING.get("openai", {})
        lookup = "gpt-4o-mini"

    if lookup not in provider_pricing:
        return 0.0

    pricing = provider_pricing[lookup]
    input_rate = pricing["input"]
    return (
        prompt_tokens * input_rate / 1_000_000
        + completion_tokens * pricing["output"] / 1_000_000
        + cache_read_tokens * input_rate * CACHE_READ_PRICE_MULTIPLIER / 1_000_000
        + cache_write_tokens * input_rate * CACHE_WRITE_PRICE_MULTIPLIER / 1_000_000
    )


# ----------------------------------------------------------------------------
# Pipeline cost estimation (consumed by the web UI /estimate endpoint)
# ----------------------------------------------------------------------------
# The /estimate endpoint multiplies a document's token count by a USD/token
# rate. That rate is anchored to an empirically observed figure and rescaled
# to the currently-configured model, so the estimate tracks llm_config.yaml.

# Historically observed cost per 1,000 *document* tokens for a full 12-stage
# run, measured when the dominant (highest call volume) stage ran on the
# anchor model below. Recalibrate from real run-cost data as runs accumulate.
COST_ESTIMATE_ANCHOR_RATE = 0.075
COST_ESTIMATE_ANCHOR_MODEL = ("openai", "gpt-4o")
# Fraction of pipeline LLM tokens that are input (prompts/schemas dominate).
COST_ESTIMATE_INPUT_SHARE = 0.8


def _blended_price_per_million(provider: str, model: str) -> float:
    """Blended USD/1M-token price: input_share*input + output_share*output.

    Returns 0.0 when the model has no PRICING entry.
    """
    provider_pricing = PRICING.get(provider, {})
    pricing = (provider_pricing.get(model)
               or provider_pricing.get(_normalize_model_id(model)))
    if not pricing:
        return 0.0
    return (COST_ESTIMATE_INPUT_SHARE * pricing["input"] +
            (1 - COST_ESTIMATE_INPUT_SHARE) * pricing["output"])


def estimate_cost_per_1k_doc_tokens(model: str = None, provider: str = None) -> float:
    """Estimate pipeline cost (USD) per 1,000 document tokens for a model.

    Lets the web /estimate endpoint show a cost that tracks the model
    configured in llm_config.yaml. When model/provider are omitted, the
    effective default config is used (YAML default + CVICHE_LLM_* env vars).

    The estimate rescales COST_ESTIMATE_ANCHOR_RATE by the ratio of blended
    model prices. It is an approximation -- see docs/LLM_MODELS.md.
    """
    if model is None or provider is None:
        # "default" is not a real stage, so get_stage_config returns the
        # effective default config (YAML default block + env overrides).
        cfg = get_stage_config("default")
        provider = provider or cfg["provider"]
        model = model or cfg["model"]

    anchor_provider, anchor_model = COST_ESTIMATE_ANCHOR_MODEL
    anchor_blended = _blended_price_per_million(anchor_provider, anchor_model)
    model_blended = _blended_price_per_million(provider, model)

    if not anchor_blended or not model_blended:
        # Unknown pricing on either side -- fall back to the flat anchor rate.
        return COST_ESTIMATE_ANCHOR_RATE

    return COST_ESTIMATE_ANCHOR_RATE * (model_blended / anchor_blended)


# ----------------------------------------------------------------------------
# Entry-classification-aware run cost model
# ----------------------------------------------------------------------------
# The flat per-doc-token rate above (estimate_cost_per_1k_doc_tokens) badly
# under-predicts entry-dense CVs, because it assumes total cost scales with
# document LENGTH. It does not. The dominant cost is stage 3b entry
# classification, which re-sends a large, static taxonomy/rules system prompt
# (~10.7K tokens) on ONE LLM call per hierarchy group (entries batched, max 15
# per call). That cost scales with entry COUNT, not document length: a compact
# but entry-dense CV is cheap by tokens yet expensive by calls.
#
# Constants are calibrated against a complete run (2026-03-28):
#   56,372 doc chars / 123 entries / 17 classification calls /
#   234K input + 19K output tokens / ~$0.99 on Sonnet 4.6.
# stage 3b alone was 191K of the 234K input tokens (~82%).
# Recalibrate from real run-cost data as runs accumulate.
#
# KNOWN GAP (see issue #50): the current pipeline added a per-section
# core_taxonomy_mapper stage absent from the 2026-03-28 calibration run, so
# OTHER_STAGES_INPUT_MULTIPLE can under-count section-dense CVs until a
# complete current-pipeline run is measured. The min/max band widens to
# partially absorb this; recalibrate when a current run's logs are available.
COST_ESTIMATE_CHARS_PER_ENTRY = 458            # document chars per classified entry
COST_ESTIMATE_ENTRIES_PER_3B_CALL = 7.2        # entries per classification call (hierarchy groups, batch <= 15)
COST_ESTIMATE_3B_INPUT_TOKENS_PER_CALL = 11200  # static taxonomy prompt + batch text, per call
COST_ESTIMATE_3B_OUTPUT_TOKENS_PER_ENTRY = 155  # classification JSON emitted per entry
COST_ESTIMATE_3B_EXTRA_PASS_INPUT_TOKENS = 3221  # T-validation (~2906) + fragment reconnection (~315), once/run
COST_ESTIMATE_OTHER_STAGES_INPUT_MULTIPLE = 2.8  # non-3b input tokens / document tokens
COST_ESTIMATE_OTHER_STAGES_OUTPUT_SHARE = 0.05   # non-3b output tokens / non-3b input tokens


def _model_io_rates(provider: str, model: str) -> tuple:
    """(input, output) USD per 1M tokens for a model, with gpt-4o-mini fallback."""
    provider_pricing = PRICING.get(provider, PRICING.get("openai", {}))
    lookup = model if model in provider_pricing else _normalize_model_id(model)
    pricing = provider_pricing.get(lookup) or PRICING["openai"]["gpt-4o-mini"]
    return pricing["input"], pricing["output"]


def estimate_run_cost_usd(text_char_count: int, model: str = None,
                          provider: str = None) -> float:
    """Point estimate (USD) for a full pipeline run on a CV of this size.

    Models stage 3b entry classification explicitly (call_count x fixed
    prompt + per-entry output) because it dominates cost and scales with
    entry count, and treats all other stages as roughly proportional to
    document tokens. Priced at the active model's input/output rates so the
    estimate tracks llm_config.yaml. See the calibration constants above.

    When model/provider are omitted, the effective default config is used.
    """
    if model is None or provider is None:
        cfg = get_stage_config("default")
        provider = provider or cfg["provider"]
        model = model or cfg["model"]
    in_rate, out_rate = _model_io_rates(provider, model)

    doc_tokens = max(text_char_count, 0) / 4.0
    est_entries = max(text_char_count, 0) / COST_ESTIMATE_CHARS_PER_ENTRY
    est_3b_calls = math.ceil(est_entries / COST_ESTIMATE_ENTRIES_PER_3B_CALL) if est_entries else 0

    # Stage 3b: the static taxonomy prompt re-sent on every call dominates;
    # the per-call constant already folds in the small per-batch entry text.
    stage3b_input = est_3b_calls * COST_ESTIMATE_3B_INPUT_TOKENS_PER_CALL
    if est_3b_calls:
        stage3b_input += COST_ESTIMATE_3B_EXTRA_PASS_INPUT_TOKENS
    stage3b_output = est_entries * COST_ESTIMATE_3B_OUTPUT_TOKENS_PER_ENTRY

    # All other stages: roughly proportional to document tokens (input-heavy).
    other_input = doc_tokens * COST_ESTIMATE_OTHER_STAGES_INPUT_MULTIPLE
    other_output = other_input * COST_ESTIMATE_OTHER_STAGES_OUTPUT_SHARE

    total_input = stage3b_input + other_input
    total_output = stage3b_output + other_output
    return (total_input * in_rate + total_output * out_rate) / 1_000_000


# Human-readable names for known model IDs, used by the web UI.
_FRIENDLY_MODEL_NAMES = {
    "anthropic.claude-sonnet-4-6": "Claude Sonnet 4.6",
    "anthropic.claude-haiku-4-5": "Claude Haiku 4.5",
    "anthropic.claude-haiku-4-5-20251001-v1:0": "Claude Haiku 4.5",
    "anthropic.claude-opus-4-7": "Claude Opus 4.7",
    "anthropic.claude-opus-4-6": "Claude Opus 4.6",
    "gpt-4o": "GPT-4o",
    "gpt-4o-mini": "GPT-4o mini",
    "gpt-5.1": "GPT-5.1",
}


def friendly_model_name(model: str) -> str:
    """Best-effort human-readable model name for UI display."""
    if not model:
        return "unknown"
    return _FRIENDLY_MODEL_NAMES.get(_normalize_model_id(model), model)


# ============================================================================
# LLM Config Resolution (YAML + env var overrides)
# ============================================================================

# Config cache
_cached_config = None


def _load_yaml_config() -> dict:
    """Load llm_config.yaml with caching. Returns empty dict on failure."""
    global _cached_config
    if _cached_config is not None:
        return _cached_config

    config_path = Path(__file__).parent / "config" / "llm_config.yaml"
    if not config_path.exists():
        _cached_config = {}
        return _cached_config

    try:
        with open(config_path, encoding="utf-8") as f:
            _cached_config = yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning(f"Failed to load llm_config.yaml: {e}. Using defaults.")
        _cached_config = {}

    return _cached_config


def get_stage_config(stage: str) -> dict:
    """
    Resolve LLM config for a pipeline stage.

    Resolution order (later overrides earlier):
    1. Hardcoded defaults (openai / gpt-4o-mini / temperature 0)
    2. YAML default block
    3. YAML stage-specific overrides
    4. CVICHE_LLM_PROVIDER / CVICHE_LLM_MODEL env vars (only for keys
       NOT explicitly set in the stage-specific YAML block)

    Args:
        stage: Pipeline stage name (e.g., "stage_2", "stage_4")

    Returns:
        dict with keys: provider, model, temperature, max_tokens, retry_count
    """
    config = _load_yaml_config()

    # Layer 1: hardcoded defaults
    effective = {
        "provider": "openai",
        "model": "gpt-4o-mini",
        "temperature": 0,
        "max_tokens": None,
        "retry_count": 3,
        # Bedrock-only knob; ignored by the OpenAI path. Default false so the
        # request is byte-identical to pre-caching behavior when the YAML is
        # absent. The shipping llm_config.yaml sets it to true.
        "enable_prompt_caching": False,
    }

    # Layer 2: YAML default block
    if config and "default" in config:
        effective.update({k: v for k, v in config["default"].items() if v is not None})

    # Layer 3: YAML stage-specific overrides
    if config and "stages" in config and stage in config["stages"]:
        effective.update({k: v for k, v in config["stages"][stage].items() if v is not None})

    # Layer 4: Env var overrides apply to global default ONLY (not stage-specific overrides)
    # If stage has explicit YAML override for a key, env vars do NOT override that key
    has_stage_override = (config and "stages" in config and stage in config["stages"])
    stage_keys = set(config.get("stages", {}).get(stage, {}).keys()) if has_stage_override else set()

    env_provider = os.environ.get("CVICHE_LLM_PROVIDER")
    env_model = os.environ.get("CVICHE_LLM_MODEL")

    if env_provider and "provider" not in stage_keys:
        effective["provider"] = env_provider
    if env_model and "model" not in stage_keys:
        effective["model"] = env_model

    return effective


def reload_config():
    """Clear the cached config so the next get_stage_config() re-reads YAML."""
    global _cached_config
    _cached_config = None


# ============================================================================
# LLM Runtime Knobs (env -> web app's auth_config.yaml `llm:` block -> default)
# ============================================================================
# Mirrors web_interface/backend/app/config_loader.get_config("llm", key,
# default)'s resolution order and source file, without the pipeline core
# importing the web app package (#267). llm/retry.py used to sys.path-insert
# web_interface/backend and `from app.config_loader import get_config`,
# coupling LLM infrastructure to the web app's package layout (#620 review).
# The yaml layer is live in deployment -- buildspec.yaml writes
# CVICHE_LLM_TIMEOUT_SECONDS / CVICHE_LLM_MAX_ATTEMPTS into the ConfigMap's
# `llm:` block (auth_config.yaml) -- so a bare os.environ.get() would
# silently drop that layer and regress prod.

AUTH_CONFIG_PATH = PROJECT_ROOT / "web_interface" / "backend" / "auth_config.yaml"
AUTH_CONFIG_EXAMPLE_PATH = PROJECT_ROOT / "web_interface" / "backend" / "auth_config.yaml.example"


def get_llm_env_config(key: str, default: object) -> tuple[object, str]:
    """Resolve one LLM runtime knob: env var, then the `llm:` section of
    auth_config.yaml (falling back to the tracked .example when the real
    file is absent, same as app.config_loader.load_yaml_config), then
    `default`. Not cached -- read infrequently (once per knob, at import),
    so a fresh read each time is not worth a staleness/parity risk.

    Returns (value, source) with source one of "env", "yaml", "default" --
    same shape as app.config_loader.get_config.
    """
    value = os.environ.get(key)
    if value:
        return value, "env"

    path = AUTH_CONFIG_PATH if AUTH_CONFIG_PATH.exists() else AUTH_CONFIG_EXAMPLE_PATH
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        if not isinstance(cfg, dict):
            # An empty file (None) is ordinary; a list or scalar document is a
            # misconfiguration. Either way degrade to default, as the backend's
            # get_config does inside its broad except.
            if cfg is not None:
                logger.warning(
                    "%s is not a mapping (got %s); using default for %s",
                    path, type(cfg).__name__, key,
                )
            cfg = {}
        llm_cfg = cfg.get("llm")
        if not isinstance(llm_cfg, dict):
            # A missing/null `llm:` block (llm_cfg is None) is the ordinary
            # case and not worth a warning. A present-but-wrong-shaped block
            # (e.g. `llm: "oops"`) is a misconfiguration -- log it, but still
            # degrade to default rather than raising (parity with the
            # backend's app.config_loader.get_config, which catches this
            # inside its own broad except and returns default too).
            if llm_cfg is not None:
                logger.warning(
                    "auth_config.yaml 'llm' section is not a mapping (got %s); ignoring it for %s",
                    type(llm_cfg).__name__, key,
                )
            llm_cfg = {}
        value = llm_cfg.get(key)
        if value:
            return value, "yaml"
    except (OSError, yaml.YAMLError):
        logger.warning("Failed to load %s; using default for %s", path, key, exc_info=True)

    return default, "default"


def validate_setup() -> dict:
    """
    Validate that required components exist and are configured correctly.

    Returns:
        dict: Validation results with structure:
            {
                "valid": bool,
                "issues": List[str],
                "template_path": Path or None,
                "legacy_available": bool,
                "features_enabled": dict,
                "output_dirs": dict
            }
    """
    issues = []
    template_path = None

    # Check template
    try:
        template_path = get_template_path()
    except FileNotFoundError as e:
        issues.append(str(e))

    # Check legacy handlers
    legacy_available = LEGACY_PRODUCTION.exists()
    if USE_LEGACY_HANDLERS and not legacy_available:
        issues.append(
            f"Legacy handlers not found at {LEGACY_PRODUCTION}\n"
            f"Advanced features (author abbreviation, bolding, PMCID lookup) will not work.\n"
            f"Set USE_LEGACY_HANDLERS=False in config.py to use simple template filler."
        )

    # Create output directories if missing
    for output_dir in [OUTPUT_BASE, WEB_OUTPUT_BASE]:
        if not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=True)

    return {
        "valid": len(issues) == 0,
        "issues": issues,
        "template_path": template_path,
        "legacy_available": legacy_available,
        "features_enabled": {
            "legacy_handlers": USE_LEGACY_HANDLERS and legacy_available,
            "pmcid_enrichment": ENABLE_PMCID_ENRICHMENT and USE_LEGACY_HANDLERS and legacy_available,
            "author_bolding": ENABLE_AUTHOR_BOLDING and USE_LEGACY_HANDLERS and legacy_available,
            "author_abbreviation": ENABLE_AUTHOR_ABBREVIATION and USE_LEGACY_HANDLERS and legacy_available,
            "subsection_categorization": ENABLE_SUBSECTION_CATEGORIZATION and USE_LEGACY_HANDLERS and legacy_available,
            "taxonomy_mapping": USE_TAXONOMY_MAPPING,
        },
        "output_dirs": {
            "cli": str(OUTPUT_BASE),
            "web": str(WEB_OUTPUT_BASE),
        }
    }


def print_validation_report():
    """Print human-readable validation report."""
    print("=" * 80)
    print("CV PIPELINE CONFIGURATION VALIDATION")
    print("=" * 80)

    validation = validate_setup()

    if validation["valid"]:
        print("\n✅ Configuration is valid!\n")
    else:
        print("\n❌ Configuration has issues:\n")
        for issue in validation["issues"]:
            print(f"  • {issue}\n")

    print("Template:")
    if validation["template_path"]:
        print(f"  ✓ {validation['template_path']}")
    else:
        print("  ✗ Not found")

    print("\nLegacy Handlers:")
    if validation["legacy_available"]:
        print(f"  ✓ Available at {LEGACY_PRODUCTION}")
    else:
        print(f"  ✗ Not found at {LEGACY_PRODUCTION}")

    print("\nFeatures Enabled:")
    for feature, enabled in validation["features_enabled"].items():
        status = "✓" if enabled else "✗"
        feature_name = feature.replace("_", " ").title()
        print(f"  {status} {feature_name}")

    print("\nOutput Directories:")
    for name, path in validation["output_dirs"].items():
        print(f"  • {name.upper()}: {path}")

    print("\n" + "=" * 80)

    return validation["valid"]


# ============================================================================
# Module Initialization
# ============================================================================

# Create output directories on import
for output_dir in [OUTPUT_STAGE_1A, OUTPUT_STAGE_1B, OUTPUT_STAGE_2, OUTPUT_STAGE_4, WEB_OUTPUT_BASE]:
    output_dir.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    # Run validation when executed directly
    import sys
    valid = print_validation_report()
    sys.exit(0 if valid else 1)
