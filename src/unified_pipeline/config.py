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
import os
import yaml

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
OUTPUT_STAGE_3 = OUTPUT_BASE / "stage_3_taxonomy_mapping"
OUTPUT_STAGE_4 = OUTPUT_BASE / "stage_4_wcm_templates"

# Legacy compatibility
OUTPUT_STAGE_1 = OUTPUT_STAGE_1A  # Alias for backward compatibility
OUTPUT_STAGE_2A = OUTPUT_BASE / "stage_2a_entry_delimitation"  # Deprecated
OUTPUT_STAGE_2B = OUTPUT_BASE / "stage_2b_extract_entries_from_delimiters"  # Deprecated
OUTPUT_STAGE_2_LEGACY = OUTPUT_STAGE_3  # Old stage 2 pointed to taxonomy mapping

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
        # Anthropic Claude models
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


def calculate_cost(prompt_tokens: int, completion_tokens: int,
                   model: str = None, provider: str = "openai") -> float:
    """
    Calculate cost for LLM API call.

    Args:
        prompt_tokens: Number of input tokens
        completion_tokens: Number of output tokens
        model: Model name (default: DEFAULT_MODEL)
        provider: LLM provider name (default: "openai")

    Returns:
        float: Cost in USD
    """
    model = model or DEFAULT_MODEL

    provider_pricing = PRICING.get(provider, PRICING.get("openai", {}))
    if model not in provider_pricing:
        # Fallback to openai gpt-4o-mini pricing
        provider_pricing = PRICING.get("openai", {})
        model = "gpt-4o-mini"

    if model not in provider_pricing:
        return 0.0

    pricing = provider_pricing[model]
    return (prompt_tokens * pricing["input"] / 1_000_000 +
            completion_tokens * pricing["output"] / 1_000_000)


# ============================================================================
# LLM Config Resolution (YAML + env var overrides)
# ============================================================================

# Config cache
_cached_config = None

logger = logging.getLogger(__name__)


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
        with open(config_path) as f:
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
for output_dir in [OUTPUT_STAGE_1A, OUTPUT_STAGE_1B, OUTPUT_STAGE_2, OUTPUT_STAGE_3, OUTPUT_STAGE_4, WEB_OUTPUT_BASE]:
    output_dir.mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    # Run validation when executed directly
    import sys
    valid = print_validation_report()
    sys.exit(0 if valid else 1)
