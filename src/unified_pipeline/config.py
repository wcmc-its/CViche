"""
Centralized configuration for CV parsing pipeline.

This module provides configuration settings used by:
- CLI: run_pipeline.py
- Web App: web_interface/backend/app/pipeline/orchestrator.py
- Direct Python imports

Single source of truth for all paths, settings, and feature flags.
"""
from pathlib import Path
import os

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

# OpenAI API pricing (per 1M tokens)
PRICING = {
    "gpt-4o-mini": {
        "input": 0.150,  # $0.150 per 1M input tokens
        "output": 0.600  # $0.600 per 1M output tokens
    },
    "gpt-4o": {
        "input": 2.50,
        "output": 10.00
    }
}

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


def calculate_cost(prompt_tokens: int, completion_tokens: int, model: str = None) -> float:
    """
    Calculate cost for LLM API call.

    Args:
        prompt_tokens: Number of input tokens
        completion_tokens: Number of output tokens
        model: Model name (default: DEFAULT_MODEL)

    Returns:
        float: Cost in USD
    """
    model = model or DEFAULT_MODEL

    if model not in PRICING:
        # Fallback to gpt-4o-mini pricing
        model = "gpt-4o-mini"

    pricing = PRICING[model]
    cost = (prompt_tokens * pricing["input"] / 1_000_000 +
            completion_tokens * pricing["output"] / 1_000_000)

    return cost


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
