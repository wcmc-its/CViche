"""
Phase 2 Testing Framework: Subsection Classification Evaluation

Generates test packages for evaluating how well individual records are assigned
to subsections within the taxonomy (e.g., research articles vs. case reports).

Usage:
    python3 -m core.phase2_test_generator \
        --mapped outputs/2032_Haendel_Cv_mapped.json \
        --repaired outputs/2032_Haendel_Cv_segmented_repaired.json \
        --output phase2_tests/2032_Haendel_test.json \
        --max-records 50
"""

import json
import argparse
from pathlib import Path
from typing import Dict, List, Any
import sys

# Import S-taxonomy
try:
    from .s_taxonomy import get_s_taxonomy_reference, get_s_taxonomy_for_ids
except ImportError:
    sys.path.append('src/unified_pipeline')
    from s_taxonomy import get_s_taxonomy_reference, get_s_taxonomy_for_ids


# Sections with subsections that need evaluation
SUBSECTION_EVALUATION_TARGETS = {
    "bibliography": {
        "subsections": [
            "peer_reviewed_articles",
            "reviews_and_editorials",
            "books",
            "book_chapters",
            "non_peer_reviewed_publications",
            "case_reports",
            "in_review_submitted",
            "abstracts",
            "other_scholarly_outputs"
        ],
        "description": "Publications need to be classified into specific types"
    },
    "clinical_practice_innovation_leadership": {
        "subsections": [
            "clinical_qi_projects",
            "clinical_trials"
        ],
        "description": "Clinical work needs to be classified into QI projects vs. trials"
    },
    "research_overview": {
        "subsections": [
            "grant_support"
        ],
        "description": "Research activities vs. specific grant records"
    },
    "extramural_professional_activities": {
        "subsections": [
            "editorial_reviewer_roles",
            "consulting_activities"
        ],
        "description": "External activities need classification"
    },
    "conference_presentations_posters": {
        "subsections": [
            "abstracts"
        ],
        "description": "Conference work vs. published abstracts"
    }
}


def get_taxonomy_info(section_ids: List[str]) -> Dict[str, Any]:
    """Extract taxonomy information for specified sections."""
    return get_s_taxonomy_for_ids(section_ids)


def extract_records_for_evaluation(
    mapped_data: Dict,
    repaired_data: Dict,
    max_records_per_section: int = 20
) -> List[Dict]:
    """
    Extract individual records with their current subsection assignments.

    Returns:
        List of record objects with current and proposed classifications
    """
    # Build mapping from group_id to taxonomy assignment
    group_to_taxonomy = {}
    for mapping in mapped_data['mappings']:
        group_id = mapping['source_group_id']
        group_to_taxonomy[group_id] = {
            'section_id': mapping['final_section_id'],
            'section_name': mapping['final_canonical_name'],
            'confidence': mapping['pass1_confidence']
        }

    # Extract all records with subsection assignments
    all_records = []

    def process_group(group):
        """Recursively process groups and extract entries."""
        group_id = group['id']
        taxonomy = group_to_taxonomy.get(group_id, {})
        section_id = taxonomy.get('section_id', 'unknown')

        # Only process if this is a specific subsection (S1, S2, etc.)
        # or another subsection target
        if should_evaluate_section(section_id):
            # Extract entries from this group
            for entry in group.get('entries', []):
                record = {
                    'entry_id': entry['id'],
                    'raw_text': entry['text_snippet'],  # Full text, no truncation
                    'entry_type': entry.get('entry_type', 'other'),
                    'current_assignment': {
                        'section_id': section_id,
                        'section_name': taxonomy.get('section_name', 'Unknown'),
                        'confidence': taxonomy.get('confidence', 0.0)
                    },
                    'group_id': group_id,
                    'group_label': group.get('label_inferred', 'Unknown'),

                    # Fields to be filled by evaluator
                    'evaluation_result': None,  # "CORRECT", "WRONG_SUBSECTION", or "WRONG_SECTION"
                    'correct_section_id': None,  # If WRONG_SUBSECTION or WRONG_SECTION
                    'evaluation_notes': '',  # Required explanation
                    'evaluation_confidence': None,  # 1-5 scale
                    'ambiguity_flags': []  # Can be filled by evaluator if needed
                }

                all_records.append(record)

        # Process subgroups
        for subgroup in group.get('subgroups', []):
            process_group(subgroup)

    # Process all top-level groups
    for group in repaired_data.get('groups', []):
        process_group(group)

    return all_records


def should_evaluate_section(section_id: str) -> bool:
    """
    Determine if records in this section should be evaluated.
    Returns True for specific subsections (S1, S2, etc.) and other targets.
    """
    # Bibliography subsections (S1-S9)
    if section_id.startswith('S') and len(section_id) <= 3:
        return True

    # Clinical subsections (L1, L2)
    if section_id in ['L1', 'L2']:
        return True

    # Check other evaluation targets
    for parent_id, config in SUBSECTION_EVALUATION_TARGETS.items():
        if section_id in config['subsections']:
            return True

    return False


def generate_test_package(
    cv_name: str,
    all_records: List[Dict],
    output_path: Path
):
    """Generate a test package for ChatGPT evaluation."""

    # Get all unique section IDs that appear in records
    unique_sections = set(r['current_assignment']['section_id'] for r in all_records)

    # Build taxonomy reference for all S-taxonomy sections
    s_taxonomy = get_s_taxonomy_reference()
    all_section_ids = list(s_taxonomy.keys())
    taxonomy_reference = s_taxonomy

    # Group records by current assignment for summary
    records_by_current = {}
    for record in all_records:
        current_id = record['current_assignment']['section_id']
        if current_id not in records_by_current:
            records_by_current[current_id] = []
        records_by_current[current_id].append(record)

    # Build test package with rigorous structure
    test_package = {
        "cv_name": cv_name,
        "test_type": "phase2_subsection_classification_validation_v2",
        "version": "2.0",
        "instructions": {
            "CRITICAL": "YOU MUST PRESERVE ALL FIELDS EXCEPT THE EVALUATION FIELDS. DO NOT modify entry_id, raw_text, current_assignment, etc.",
            "goal": "Validate subsection assignments for individual CV records",
            "output_format": "Return the SAME JSON structure with evaluation fields filled in",
            "evaluation_process": [
                "1. For EACH record in the 'records' array:",
                "2. Read 'raw_text' carefully",
                "3. Review 'current_assignment' (what section it's currently in)",
                "4. Consult 'taxonomy_reference' to understand all available sections",
                "5. Determine evaluation_result (one of three options):",
                "   - 'CORRECT': Assignment is appropriate",
                "   - 'WRONG_SUBSECTION': Right parent section, wrong subsection (e.g., S1 should be S2)",
                "   - 'WRONG_SECTION': Wrong parent section entirely (e.g., S1 should be M2 or Q4D)",
                "6. Fill in:",
                "   - 'evaluation_result': One of the three values above",
                "   - 'correct_section_id': If WRONG_SUBSECTION or WRONG_SECTION, provide correct ID",
                "   - 'evaluation_notes': 1-2 sentence explanation (REQUIRED)",
                "   - 'evaluation_confidence': 1-5 (1=very uncertain, 5=very certain)",
                "   - 'ambiguity_flags': Array of issues if any: ['truncated', 'missing_context', 'ambiguous_type', 'mixed_content']"
            ],
            "validation_rules": [
                "RULE 1: evaluation_result MUST be exactly 'CORRECT', 'WRONG_SUBSECTION', or 'WRONG_SECTION'",
                "RULE 2: If evaluation_result is NOT 'CORRECT', correct_section_id MUST be provided",
                "RULE 3: correct_section_id MUST exist in taxonomy_reference",
                "RULE 4: evaluation_notes MUST explain the reasoning",
                "RULE 5: DO NOT invent new section IDs - use only those in taxonomy_reference",
                "RULE 6: DO NOT modify any fields except: evaluation_result, correct_section_id, evaluation_notes, evaluation_confidence, ambiguity_flags"
            ],
            "common_pitfalls": [
                "Publications (S1-S15) vs Research Activities (M1-M4): S for publications WITH citations, M for grant/research descriptions",
                "Editorial Roles (Q4C/Q4D) vs Publications (S): Q4C/Q4D for reviewing/editorial ROLES, S for own publications",
                "Teaching (K) vs Education (B/C): K for teaching others, B/C for own education",
                "Leadership (O/Q1) vs Membership (I): O/Q1 for leadership roles, I for simple membership",
                "Unpublished (S7) vs Published (S1-S6): Check for DOI, journal name, year - if present, likely published"
            ]
        },
        "taxonomy_reference": taxonomy_reference,
        "records": all_records,
        "summary": {
            "total_records": len(all_records),
            "unique_sections": len(unique_sections),
            "breakdown_by_current_assignment": {
                section_id: {
                    "section_name": records_by_current[section_id][0]['current_assignment']['section_name'],
                    "num_records": len(records_by_current[section_id])
                }
                for section_id in sorted(records_by_current.keys())
            }
        }
    }

    # Write output
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(test_package, f, indent=2)

    print(f"✓ Test package generated: {output_path}")
    print(f"  - {len(all_records)} records across {len(unique_sections)} subsections")
    print(f"\nBreakdown:")
    for section_id, info in sorted(test_package['summary']['breakdown_by_current_assignment'].items()):
        print(f"    {section_id}: {info['num_records']:3d} records - {info['section_name']}")

    return test_package


def split_into_batches(test_package: Dict, max_files: int = 5) -> List[Dict]:
    """
    Split test package into smaller batches for ChatGPT upload.
    Each batch should be a complete, standalone test.
    """
    all_records = test_package['records']
    total_records = len(all_records)
    records_per_batch = (total_records // max_files) + 1

    batches = []
    for i in range(0, total_records, records_per_batch):
        batch_records = all_records[i:i + records_per_batch]
        batch = create_batch_package(test_package, batch_records, len(batches) + 1)
        batches.append(batch)

    return batches


def create_batch_package(base_package: Dict, records: List[Dict], batch_num: int) -> Dict:
    """Create a batch package from selected records."""
    # Group records by current assignment for summary
    records_by_current = {}
    for record in records:
        current_id = record['current_assignment']['section_id']
        if current_id not in records_by_current:
            records_by_current[current_id] = []
        records_by_current[current_id].append(record)

    batch = {
        "cv_name": base_package['cv_name'],
        "batch_number": batch_num,
        "test_type": base_package['test_type'],
        "version": base_package.get('version', '2.0'),
        "instructions": base_package['instructions'],
        "taxonomy_reference": base_package['taxonomy_reference'],
        "records": records,
        "summary": {
            "total_records": len(records),
            "unique_sections": len(records_by_current),
            "breakdown_by_current_assignment": {
                section_id: {
                    "section_name": records_by_current[section_id][0]['current_assignment']['section_name'],
                    "num_records": len(records_by_current[section_id])
                }
                for section_id in sorted(records_by_current.keys())
            }
        }
    }
    return batch


def main():
    parser = argparse.ArgumentParser(description="Generate Phase 2 subsection classification tests")
    parser.add_argument('--mapped', required=True, help='Path to mapped JSON file')
    parser.add_argument('--repaired', required=True, help='Path to repaired JSON file')
    parser.add_argument('--output', required=True, help='Output path for test package')
    parser.add_argument('--max-records', type=int, default=50,
                       help='Max records per section (default: 50)')
    parser.add_argument('--max-files', type=int, default=5,
                       help='Max number of files to split into (default: 5)')
    parser.add_argument('--split', action='store_true',
                       help='Split into multiple files for batch upload')

    args = parser.parse_args()

    # Load input files
    print(f"Loading files...")
    with open(args.mapped) as f:
        mapped_data = json.load(f)

    with open(args.repaired) as f:
        repaired_data = json.load(f)

    cv_name = Path(args.mapped).stem.replace('_mapped', '')

    print(f"Extracting records for evaluation...")
    all_records = extract_records_for_evaluation(
        mapped_data,
        repaired_data,
        max_records_per_section=args.max_records
    )

    if not all_records:
        print("⚠️  No records found with subsection assignments to evaluate")
        return

    output_path = Path(args.output)

    if args.split:
        # Generate test package first
        test_package = generate_test_package(cv_name, all_records, output_path)

        # Split into batches
        print(f"\nSplitting into batches (max {args.max_files} files)...")
        batches = split_into_batches(test_package, max_files=args.max_files)

        # Write batch files
        base_name = output_path.stem
        for i, batch in enumerate(batches, 1):
            batch_path = output_path.parent / f"{base_name}_batch{i}.json"
            with open(batch_path, 'w') as f:
                json.dump(batch, f, indent=2)
            print(f"  ✓ Batch {i}: {batch['summary']['total_records']} records → {batch_path}")

        print(f"\n✓ Generated {len(batches)} batch files")
    else:
        # Single file
        generate_test_package(cv_name, all_records, output_path)


if __name__ == '__main__':
    main()
