"""
Stage 3: Taxonomy Mapping (Unified)

Runs Stage 3a (header taxonomy mapping) and Stage 3b (entry classification) in sequence.
This is the standard way to run taxonomy mapping on a CV.

Can also run 3a and 3b independently:
  python stage_3a_header_taxonomy_mapper.py <document_uid>
  python stage_3b_entry_classifier.py <document_uid>

Input:
  - Stage 1a segmentation output (hierarchy)
  - Stage 2 entry extraction output (entries)
Output:
  - Stage 3a header mappings (JSON)
  - Stage 3b classified entries (JSON)
"""

import sys
from pathlib import Path
from typing import Dict, Optional

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from stage_3a_header_taxonomy_mapper import run_stage_3a
from stage_3b_entry_classifier import run_stage_3b


def run_stage_3(
    document_uid: str,
    stage_1a_path: Optional[str] = None,
    stage_2_path: Optional[str] = None,
    output_dir_3a: Optional[str] = None,
    output_dir_3b: Optional[str] = None,
    model: str = "gpt-5.1"
) -> Dict:
    """
    Run full taxonomy mapping pipeline (Stage 3a + 3b).

    Args:
        document_uid: Document identifier (e.g., "2086_Jones_Webb")
        stage_1a_path: Path to Stage 1a segmentation output (optional, auto-detect)
        stage_2_path: Path to Stage 2 entry extraction output (optional, auto-detect)
        output_dir_3a: Output directory for Stage 3a (optional, auto-detect)
        output_dir_3b: Output directory for Stage 3b (optional, auto-detect)
        model: OpenAI model to use (default: gpt-5.1)

    Returns:
        Dict with results from both stages
    """
    print("=" * 80)
    print("STAGE 3: TAXONOMY MAPPING (3a + 3b)")
    print("=" * 80)
    print()

    # Run Stage 3a: Header Taxonomy Mapping
    result_3a = run_stage_3a(
        document_uid=document_uid,
        stage_1a_path=stage_1a_path,
        output_dir=output_dir_3a,
        model=model
    )

    print()

    # Run Stage 3b: Entry Classification
    result_3b = run_stage_3b(
        document_uid=document_uid,
        stage_2_path=stage_2_path,
        stage_3a_path=result_3a["output_path"],
        output_dir=output_dir_3b,
        model=model
    )

    print()
    print("=" * 80)
    print("STAGE 3 COMPLETE")
    print("=" * 80)
    print()
    print(f"Stage 3a output: {result_3a['output_path']}")
    print(f"Stage 3b output: {result_3b['output_path']}")
    print()

    # Combined stats
    total_cost = result_3a["stats"]["cost"] + result_3b["stats"]["cost"]
    total_tokens = result_3a["stats"]["total_tokens"] + result_3b["stats"]["total_tokens"]

    print(f"Total cost: ${total_cost:.4f}")
    print(f"Total tokens: {total_tokens:,}")
    print()

    return {
        "document_uid": document_uid,
        "stage_3a": result_3a,
        "stage_3b": result_3b,
        "combined_stats": {
            "total_cost": total_cost,
            "total_tokens": total_tokens,
            "header_nodes": result_3a["node_count"],
            "entries_classified": result_3b["total_entries"]
        }
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python stage_3_taxonomy_mapper.py <document_uid> [model]")
        print()
        print("  document_uid: Document identifier (e.g., 2086_Jones_Webb)")
        print("  model: Optional, defaults to gpt-5.1")
        print()
        print("Prerequisites:")
        print("  - Stage 1a output: outputs/stage_1a_segmentation/{uid}_segmented.json")
        print("  - Stage 2 output: outputs/stage_2_entry_extraction/{uid}_entries.json")
        print()
        print("Or run stages independently:")
        print("  python stage_3a_header_taxonomy_mapper.py <document_uid>")
        print("  python stage_3b_entry_classifier.py <document_uid>")
        sys.exit(1)

    document_uid = sys.argv[1]
    model = sys.argv[2] if len(sys.argv) > 2 else "gpt-5.1"

    result = run_stage_3(document_uid, model=model)

    # Print code distribution summary
    print("CODE DISTRIBUTION:")
    print("-" * 40)
    code_dist = result["stage_3b"].get("code_distribution", {})
    for code, count in sorted(code_dist.items(), key=lambda x: -x[1]):
        print(f"  {code}: {count}")
