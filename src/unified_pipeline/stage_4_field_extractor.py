#!/usr/bin/env python3
"""
Stage 4: Intra-Entry Field Extraction (v2.0)

Extracts structured fields from Stage 3b classified entries.

Input: Stage 3b classified entries with taxonomy codes (S1, M2A, D1, K3, etc.)
Output: Entries with extracted structured fields (authors, dates, titles, etc.)

Changes in v2.0:
- Updated to read from stage_3b_classified_entries/ output
- Complete field schemas for all 60+ taxonomy codes
- Handles sub-codes (M2A/M2B/M2C, Q4A-D, N3A/N3B, etc.)
- Aligned with taxonomy v7 field definitions

Examples:
- S1 (Peer-reviewed Research): authors, year, title, journal, volume, pages, DOI, PMID
- M2A (Active Grants): grant_number, title, pi_role, agency, start_date, end_date, total_funding
- D1 (Academic Appointments): title, institution, department, start_date, end_date
- C (Training): program_type, institution, discipline, start_date, end_date, mentor
- H (Awards): award_name, granting_body, date, amount
"""

import sys
import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from collections.abc import Callable
# Add to path
sys.path.insert(0, str(Path(__file__).parent))

from unified_pipeline.llm_client import call_llm

# Every name below is re-exported from this module by being imported here: it
# is the public import surface of the stage 4 split (#498), pinned by
# tests/test_stage4_import_surface.py. run_full_pipeline.py and the backend
# orchestrator.py are parallel drivers over the same stage_* modules and share
# no code, so a name that moves without a re-export lands silently in whichever
# driver nobody ran. The blanket `noqa: F401` is deliberate -- most of these
# have no caller left in this file and exist purely to keep that surface
# intact. `stage4.schemas._LOADED_SCHEMAS` (a mutable lazy-init cache) is
# deliberately absent: a re-export would be a stale second binding.
from unified_pipeline.stage4.coercion import (  # noqa: F401
    REGEX_PATTERNS,
    apply_regex_post_processing,
    coerce_field_value_types,
    extract_initials,
    normalize_authors_vancouver,
    normalize_dates,
)
from unified_pipeline.stage4.context_headings import stamp_context_headings
from unified_pipeline.stage4.extraction import (  # noqa: F401
    _get_field_descriptions,
    attempt_llm_recovery,
    build_extraction_prompt,
    calculate_unextracted_content,
    extract_fields_batch,
    extract_fields_from_mapped_entries,
    needs_llm_recovery,
)
from unified_pipeline.stage4.owner_name import (  # noqa: F401
    _OWNER_AFFILIATION_FIELDS,
    _entry_end_year,
    _owner_affiliation_lines,
    add_target_names,
    extract_cv_owner_name,
    find_target_name_in_authors,
    infer_cv_owner_location,
)
from unified_pipeline.stage4.schemas import (  # noqa: F401
    DEFAULT_SCHEMA,
    FIELD_DESCRIPTIONS,
    FIELD_SCHEMAS,
    FIELD_SCHEMA_CONFIG_PATH,
    FIELD_SCHEMA_VERSION,
    TAXONOMY_LABELS,
    get_active_schemas,
    get_field_schema,
    get_taxonomy_label,
    load_field_schemas_from_config,
)

logger = logging.getLogger(__name__)


def process_cv(
    docx_path: str,
    model: str | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> dict[str, Any]:
    """
    Main pipeline: Load Stage 3b classified entries and extract fields.

    Args:
        docx_path: Path to the CV document (or just the document UID)
        model: Unused -- the model is resolved from llm_config.yaml, not this argument
        cancel_check: Optional zero-arg callable threaded into the per-batch
            extraction loop. It should raise to abort the run (the web
            orchestrator passes its check_cancelled, which raises
            CancelledException). This stage is the heaviest -- ~130 LLM calls
            spread across batches -- so an intra-stage check is what lets a
            cancel land mid-stage instead of after the last batch. None (the
            standalone CLI default) is a no-op, leaving CLI behavior unchanged.
    """
    # Derive UIDs
    filename = Path(docx_path).stem
    document_uid = filename

    # Stage 3b is the only live producer of classified entries -- the legacy
    # stage_3_taxonomy_mapping/ fallback this used to check for is dead: no
    # live driver (run_full_pipeline.py, orchestrator.py, scripts/) writes
    # that directory any more (#852), so treating its presence as a second
    # source of provenance was never reachable outside a hand-crafted fixture.
    stage3b_path = Path(__file__).parent / "outputs" / "stage_3b_classified_entries" / f"{document_uid}_classified.json"

    if not stage3b_path.exists():
        raise FileNotFoundError(
            f"No Stage 3b output found for {document_uid}.\n"
            f"  Tried: {stage3b_path}"
        )

    logger.info("Loading Stage 3b output: %s", stage3b_path.name)
    with open(stage3b_path, "r", encoding="utf-8") as f:
        stage_data = json.load(f)
    # Stage 3b uses "entries" key
    # #985: stamp over the FULL list, before the filter below drops fragment /
    # duplicate sub-headings that must still end a heading's run.
    mapped_entries = stamp_context_headings(stage_data.get("entries", []))

    # Filter out fragments and duplicates (they don't need field extraction)
    valid_entries = [
        e for e in mapped_entries
        if not e.get("is_fragment") and not e.get("is_duplicate")
    ]
    fragment_count = len([e for e in mapped_entries if e.get("is_fragment")])
    duplicate_count = len([e for e in mapped_entries if e.get("is_duplicate")])

    logger.info(f"  Total entries from Stage 3b: {len(mapped_entries)}")
    logger.info(f"  - Fragments (skipped): {fragment_count}")
    logger.info(f"  - Duplicates (skipped): {duplicate_count}")
    logger.info(f"  - Valid for extraction: {len(valid_entries)}")

    # Extract fields
    result = extract_fields_from_mapped_entries(
        valid_entries,
        batch_size=10,
        document_uid=document_uid,
        cancel_check=cancel_check,
        docx_path=docx_path,
    )

    # Build output with stage metadata
    output = {
        "document_uid": document_uid,
        "stage": "4",
        "stage_name": "Field Extraction",
        "source_stage": "3b",
        "cv_owner": result.get("cv_owner"),  # Include CV owner info
        "cv_owner_location": result.get("cv_owner_location"),  # Include location for geographic scope
        "total_entries": len(result["entries"]),
        "total_cost": result["total_cost"],
        "total_tokens": result["total_tokens"],
        "cache_read_tokens": result.get("cache_read_tokens", 0),
        "cache_write_tokens": result.get("cache_write_tokens", 0),
        "stats": {
            **result.get("stats", {}),
            "fragments_skipped": fragment_count,
            "duplicates_skipped": duplicate_count,
        },
        "entries": result["entries"]
    }

    # Save output
    output_dir = Path(__file__).parent / "outputs" / "stage_4_field_extraction"
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{document_uid}_fields.json"

    with open(output_path, "w") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    logger.info(f"\n{'='*80}")
    logger.info("Stage 4 Complete")
    logger.info(f"{'='*80}")
    logger.info(f"Total entries: {len(result['entries'])}")
    logger.info(f"  - Extracted: {result['stats']['extracted']}")
    logger.info(f"  - Skipped (empty/minimal): {result['stats']['skipped']}")
    logger.info(f"  - Fragments (excluded): {fragment_count}")
    logger.info(f"  - Duplicates (excluded): {duplicate_count}")
    logger.info(f"  - Reformatted: {result['stats'].get('entries_reformatted', 0)}")
    logger.info(f"Total cost: ${result['total_cost']:.4f}")
    logger.info(f"Total tokens: {result['total_tokens']:,}")
    logger.info(f"Output: {output_path}")
    logger.info(f"{'='*80}\n")

    return {
        "output": output,
        "output_path": str(output_path)
    }

def run_validation(output_path: str) -> None:
    """
    Run validation script on the extraction output.

    Args:
        output_path: Path to the Stage 4 output JSON file
    """
    import subprocess

    # Find validation script (project root directory)
    # __file__ is in: src/unified_pipeline/stage_4_field_extractor.py
    # Validator is in: validate_stage4_extraction.py
    validator_path = Path(__file__).parent.parent.parent / "validate_stage4_extraction.py"

    if not validator_path.exists():
        logger.warning(f"\n⚠️  Validation script not found: {validator_path}")
        logger.warning("   Skipping validation (extraction still successful)")
        return

    logger.info(f"\n{'='*80}")
    logger.info("Running Validation")
    logger.info(f"{'='*80}\n")

    try:
        # Run validation script
        result = subprocess.run(
            [sys.executable, str(validator_path), output_path],
            capture_output=False,  # Show output directly
            text=True
        )

        if result.returncode != 0:
            logger.warning("\n⚠️  Validation completed with warnings")

    except Exception as e:
        logger.exception(f"\n⚠️  Validation error: {e}")
        logger.warning("   Extraction still successful")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        logger.info("Usage: python stage_4_field_extractor.py <document_uid_or_path>")
        logger.info("  document_uid_or_path: Either the document UID (e.g., '2005_Bpg')")
        logger.info("                        or path to CV document (e.g., 'path/to/2005_Bpg.docx')")
        logger.info("  (the LLM model is configured in llm_config.yaml)")
        sys.exit(1)

    input_arg = sys.argv[1]

    # Handle both document UID and file path
    # If it's a path to an existing file, use it directly
    # If it's just a UID, use it to look up Stage 3b output
    if os.path.exists(input_arg):
        docx_path = input_arg
    else:
        # Assume it's a document UID - create a fake path (only stem is used)
        docx_path = f"{input_arg}.docx"

    try:
        result = process_cv(docx_path)
        output_path = result["output_path"]

        # Run validation on the output
        run_validation(output_path)

        logger.info("\n Success")

    except Exception as e:
        logger.exception(f"Error: {e}")
        sys.exit(1)
