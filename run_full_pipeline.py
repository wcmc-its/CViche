#!/usr/bin/env python3
"""
CViche - Full CV Processing Pipeline (V15)

Runs the complete CViche processing pipeline:
- Stage 1a: Segmentation (LLM-powered hierarchical segmentation)
- Stage 1b: Hierarchy Mapping (maps headers to element indices, no LLM)
- Stage 2: Entry Extraction (detects entries and extracts text with LLM)
- Stage 3a: Header Taxonomy Mapping (maps CV headers to taxonomy codes with LLM)
- Stage 3b: Entry Classification (classifies entries using header context + content)
- Stage 3: Run both 3a and 3b together
- Stage 4: Field Extraction (extracts structured fields from classified entries)
- Stage 4.5: Research Summary Generation (generates biosketch-style M1 summary)
- Stage 5: PubMed Enrichment (enriches publications with PubMed metadata)
- Stage 5b: Institution Enrichment (adds city/state via ROR API)
- Stage 5c: Teaching Formatter (LLM-reformats K-code entries for readability)
- Stage 5d: Citation Formatter (LLM-reformats non-enriched citations to Vancouver format)
- Stage 6: WCM Word Template Generation (creates formatted Word document)

Usage:
    python3 run_full_pipeline.py <cv_path> [--stage STAGE] [--model MODEL]

Arguments:
    cv_path       : Path to Word document or just the document UID
    --stage STAGE : Run ONLY this stage: '1a', '1b', '2', '3a', '3b', '3', '4', '4.5', '5', '5b', '5c', '5d', or '6'. Default: run all
    --model MODEL : LLM model to use. Default: gpt-5.1

Example:
    # Run full pipeline
    python3 run_full_pipeline.py 2097_Upton_Cv

    # Run only Stage 2 (requires Stage 1b output to exist)
    python3 run_full_pipeline.py 2097_Upton_Cv --stage 2

    # Run only Stage 3a (header taxonomy mapping)
    python3 run_full_pipeline.py 2097_Upton_Cv --stage 3a

    # Run only Stage 3b (entry classification, requires 3a)
    python3 run_full_pipeline.py 2097_Upton_Cv --stage 3b

    # Run both 3a and 3b together
    python3 run_full_pipeline.py 2097_Upton_Cv --stage 3

    # Run only Stage 4 (field extraction, requires 3b)
    python3 run_full_pipeline.py 2097_Upton_Cv --stage 4

    # Run only Stage 4.5 (research summary generation, requires 4)
    python3 run_full_pipeline.py 2097_Upton_Cv --stage 4.5

    # Run only Stage 6 (Word template generation, uses best available input)
    python3 run_full_pipeline.py 2097_Upton_Cv --stage 6

Outputs:
    - Stage 1a: src/unified_pipeline/outputs/stage_1a_segmentation/{uid}_segmented.json
    - Stage 1b: src/unified_pipeline/outputs/stage_1b_hierarchy_mapping/{uid}_hierarchy_mapped.json
    - Stage 2:  src/unified_pipeline/outputs/stage_2_entry_extraction/{uid}_entries.json
    - Stage 3a: src/unified_pipeline/outputs/stage_3a_header_mappings/{uid}_header_taxonomy.json
    - Stage 3b: src/unified_pipeline/outputs/stage_3b_classified_entries/{uid}_classified.json
    - Stage 4:  src/unified_pipeline/outputs/stage_4_field_extraction/{uid}_fields.json
    - Stage 4.5: src/unified_pipeline/outputs/stage_4_5_research_summary/{uid}_research_summary.json
    - Stage 5:  src/unified_pipeline/outputs/stage_5_enrichment/{uid}_enriched.json
    - Stage 5b: src/unified_pipeline/outputs/stage_5b_institution_enrichment/{uid}_institution_enriched.json
    - Stage 5c: src/unified_pipeline/outputs/stage_5c_teaching_formatted/{uid}_teaching_formatted.json
    - Stage 5d: src/unified_pipeline/outputs/stage_5d_citation_formatted/{uid}_citation_formatted.json
    - Stage 6:  src/unified_pipeline/outputs/stage_6_wcm_documents/{uid}_wcm.docx
"""

import sys
import json
import time
from pathlib import Path
from datetime import timedelta

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent / 'src'))

from unified_pipeline.segmentation.chunked_chat_hierarchy_extractor import get_cv_hierarchy_chunked
from unified_pipeline.stage_1b_hierarchy_mapper import run_stage_1b
from unified_pipeline.stage_2_entry_extraction import run_stage_2
from unified_pipeline.stage_3a_header_taxonomy_mapper import run_stage_3a
from unified_pipeline.stage_3b_entry_classifier import run_stage_3b
from unified_pipeline.stage_4_field_extractor import process_cv as run_stage_4
from unified_pipeline.stage_4_5_research_summary import run_stage_4_5
from unified_pipeline.stage_5_pubmed_enrichment import run_stage5
from unified_pipeline.stage_5b_institution_enrichment import run_stage5b
from unified_pipeline.stage_5c_teaching_formatter import run_stage_5c
from unified_pipeline.stage_5d_citation_formatter import run_stage_5d
from unified_pipeline.stage_6_word_template import run_stage6


def get_stage_order():
    """Return ordered list of stage identifiers."""
    return ['1a', '1b', '2', '3a', '3b', '3', '4', '4.5', '5', '5b', '5c', '5d', '6']


def format_duration(seconds: float) -> str:
    """Format duration in seconds to human-readable string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    elif seconds < 3600:
        mins = int(seconds // 60)
        secs = seconds % 60
        return f"{mins}m {secs:.1f}s"
    else:
        hours = int(seconds // 3600)
        mins = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours}h {mins}m {secs:.1f}s"


def get_output_paths(document_uid: str) -> dict:
    """Get expected output file paths for each stage."""
    base = Path('src/unified_pipeline/outputs')
    return {
        '1a': base / 'stage_1a_segmentation' / f'{document_uid}_segmented.json',
        '1b': base / 'stage_1b_hierarchy_mapping' / f'{document_uid}_hierarchy_mapped.json',
        '2': base / 'stage_2_entry_extraction' / f'{document_uid}_entries.json',
        '3a': base / 'stage_3a_header_mappings' / f'{document_uid}_header_taxonomy.json',
        '3b': base / 'stage_3b_classified_entries' / f'{document_uid}_classified.json',
        '4': base / 'stage_4_field_extraction' / f'{document_uid}_fields.json',
        '4.5': base / 'stage_4_5_research_summary' / f'{document_uid}_research_summary.json',
    }


def check_prerequisites(start_stage: str, document_uid: str) -> tuple:
    """
    Check that prerequisite outputs exist for starting at a given stage.

    Returns:
        (success: bool, missing_file: str or None, required_stage: str or None)
    """
    paths = get_output_paths(document_uid)

    # Map each stage to its required prerequisite(s)
    # Some stages need multiple prerequisites
    prerequisites = {
        '1a': [],           # No prerequisite
        '1b': ['1a'],       # Needs Stage 1a
        '2': ['1b'],        # Needs Stage 1b
        '3a': ['1a'],       # Needs Stage 1a (hierarchy)
        '3b': ['2', '3a'],  # Needs Stage 2 (entries) and Stage 3a (header mappings)
        '3': ['1a', '2'],   # Needs Stage 1a and Stage 2 (will run 3a then 3b)
        '4': ['3b'],        # Needs Stage 3b (classified entries)
    }

    prereqs = prerequisites.get(start_stage, [])
    for prereq in prereqs:
        prereq_path = paths[prereq]
        if not prereq_path.exists():
            return False, str(prereq_path), prereq

    return True, None, None


def resolve_cv_path(cv_path_or_uid: str) -> tuple:
    """
    Resolve CV path from either a full path or just the document UID.

    Supports:
    - Full path: 'data/sample_cvs/word/2097_Upton_Cv.docx'
    - Just UID: '2097_Upton_Cv' (looks in data/sample_cvs/word/)

    Returns:
        (cv_path, document_uid)
    """
    # Check if it's already a valid path
    if Path(cv_path_or_uid).exists():
        # Extract UID from filename
        uid = Path(cv_path_or_uid).stem
        return cv_path_or_uid, uid

    # Check if it looks like a path (has directory separators or .docx extension)
    if '/' in cv_path_or_uid or '\\' in cv_path_or_uid or cv_path_or_uid.endswith('.docx'):
        # It's a path but file doesn't exist
        return cv_path_or_uid, Path(cv_path_or_uid).stem

    # Treat as UID - look in standard location
    standard_path = Path('data/sample_cvs/word') / f'{cv_path_or_uid}.docx'
    if standard_path.exists():
        return str(standard_path), cv_path_or_uid

    # Also try without assuming .docx extension was missing
    if Path(f'data/sample_cvs/word/{cv_path_or_uid}').exists():
        return f'data/sample_cvs/word/{cv_path_or_uid}', Path(cv_path_or_uid).stem

    # Return as-is and let the error happen downstream with a clear message
    return cv_path_or_uid, cv_path_or_uid


def main():
    # Parse arguments
    if len(sys.argv) < 2:
        print("Usage: python3 run_full_pipeline.py <cv_path_or_uid> [--stage STAGE] [--model MODEL]")
        print()
        print("Arguments:")
        print("  cv_path_or_uid : Path to Word document OR just the document UID")
        print("                   (if UID only, looks in data/sample_cvs/word/)")
        print("  --stage STAGE  : Run ONLY this stage: '1a', '1b', '2', '3a', '3b', '3', or '4'")
        print("                   (omit for full pipeline)")
        print("  --model MODEL  : LLM model to use (default: gpt-5.1)")
        print()
        print("Examples:")
        print("  # Full pipeline")
        print("  python3 run_full_pipeline.py 2097_Upton_Cv")
        print()
        print("  # Run only Stage 2")
        print("  python3 run_full_pipeline.py 2097_Upton_Cv --stage 2")
        print()
        print("  # Run only Stage 3a (header taxonomy mapping)")
        print("  python3 run_full_pipeline.py 2097_Upton_Cv --stage 3a")
        print()
        print("  # Run only Stage 3b (entry classification)")
        print("  python3 run_full_pipeline.py 2097_Upton_Cv --stage 3b")
        print()
        print("  # Run both 3a and 3b")
        print("  python3 run_full_pipeline.py 2097_Upton_Cv --stage 3")
        print()
        print("  # Run only Stage 4 (field extraction)")
        print("  python3 run_full_pipeline.py 2097_Upton_Cv --stage 4")
        sys.exit(1)

    # First positional argument is the CV path or UID
    cv_path, document_uid = resolve_cv_path(sys.argv[1])

    # Parse optional flags
    model = "gpt-5.1"  # Default model
    target_stage = None  # None = run all stages; otherwise run only that stage
    valid_stages = get_stage_order()

    i = 2  # Start after the cv_path_or_uid argument
    while i < len(sys.argv):
        arg = sys.argv[i]
        if arg == "--model" and i + 1 < len(sys.argv):
            model = sys.argv[i + 1]
            i += 2
        elif arg == "--stage" and i + 1 < len(sys.argv):
            target_stage = sys.argv[i + 1]
            if target_stage not in valid_stages:
                print(f"Error: Invalid stage '{target_stage}'. Use one of: {', '.join(valid_stages)}")
                sys.exit(1)
            i += 2
        else:
            i += 1

    # Check prerequisites if running a specific stage
    if target_stage and target_stage != "1a":
        ok, missing_file, required_stage = check_prerequisites(target_stage, document_uid)
        if not ok:
            print(f"Error: Cannot run stage {target_stage}")
            print(f"  Missing prerequisite: Stage {required_stage} output")
            print(f"  Expected file: {missing_file}")
            print()
            print(f"  Run the prerequisite stage first, or run without --stage for full pipeline.")
            sys.exit(1)

    print("=" * 80)
    if target_stage:
        print(f"CV PROCESSING PIPELINE - STAGE {target_stage.upper()} ONLY")
    else:
        print(f"CV PROCESSING PIPELINE (V12)")
    print("=" * 80)
    print(f"Input: {cv_path}")
    print(f"Document UID: {document_uid}")
    print(f"Model: {model}")
    print()

    # Track all results, costs, and timing
    all_results = {}
    total_cost = 0.0
    stage1a_cost = 0.0
    stage_times = {}  # Track duration of each stage
    pipeline_start_time = time.time()

    # Helper to check if we should run a stage
    def should_run(stage: str) -> bool:
        if target_stage is None:
            return True  # Full pipeline - run all stages
        if target_stage == '3':
            # --stage 3 means run both 3a and 3b
            return stage in ('3a', '3b')
        return stage == target_stage  # Single stage mode - only run the target

    # Get output paths for loading previous stage results
    output_paths = get_output_paths(document_uid)

    # Helper to count headers in hierarchy
    def count_headers(nodes):
        count = len(nodes)
        for node in nodes:
            count += count_headers(node.get('children', []))
        return count

    # ========== STAGE 1A: HIERARCHY EXTRACTION ==========
    stage1a_result = None

    if should_run('1a'):
        print("=" * 80)
        print("STAGE 1A: HIERARCHY EXTRACTION")
        print("=" * 80)
        print()

        stage_start = time.time()

        # Use chunked_chat_hierarchy_extractor (V9 architecture)
        hierarchy, stats = get_cv_hierarchy_chunked(
            cv_path=cv_path
        )

        # Save output to standard location
        output_dir = Path('src/unified_pipeline/outputs/stage_1a_segmentation')
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{document_uid}_segmented.json"

        total_headers = count_headers(hierarchy)

        # Build result structure
        stage1_output = {
            'document_uid': document_uid,
            'hierarchy': hierarchy,
            'meta': stats
        }

        with open(output_file, 'w') as f:
            json.dump(stage1_output, f, indent=2)

        # Also save human-readable .txt version
        txt_file = output_file.with_suffix('.txt')
        with open(txt_file, 'w') as f:
            f.write(f"CV Hierarchy: {document_uid}\n")
            f.write("=" * 80 + "\n\n")

            def write_hierarchy(nodes, depth=0):
                for node in nodes:
                    indent = "  " * depth
                    level = node.get('level', 'H1')
                    text = node.get('text', '')
                    f.write(f"{indent}[{level}] {text}\n")
                    if node.get('children'):
                        write_hierarchy(node['children'], depth + 1)

            write_hierarchy(hierarchy)

        stage1a_cost = stats.get('extraction_cost', 0)
        total_cost += stage1a_cost

        stage_duration = time.time() - stage_start
        stage_times['1a'] = stage_duration

        # Store result for later stages
        stage1a_result = {
            'output_file': str(output_file),
            'num_sections': len(hierarchy),
            'total_headers': total_headers,
            'stats': stats,
            'duration': stage_duration
        }
        all_results['stage_1a'] = stage1a_result

        print()
        print("Stage 1a Complete")
        print(f"  Output: {output_file}")
        print(f"  Top-level sections: {len(hierarchy)}")
        print(f"  Total headers: {total_headers}")
        print(f"  Cost: ${stage1a_cost:.4f}")
        print(f"  Time: {format_duration(stage_duration)}")
        print()
    else:
        # Load existing Stage 1a output (needed for later stages or summary)
        stage1a_file = output_paths['1a']
        if stage1a_file.exists():
            with open(stage1a_file) as f:
                stage1a_data = json.load(f)
            stage1a_result = {
                'output_file': str(stage1a_file),
                'num_sections': len(stage1a_data.get('hierarchy', [])),
                'total_headers': count_headers(stage1a_data.get('hierarchy', [])),
                'stats': stage1a_data.get('meta', {})
            }
            all_results['stage_1a'] = stage1a_result
            if target_stage is None:  # Only show skip message in full pipeline mode
                print(f"[Skipped] Stage 1a - using existing output: {stage1a_file}")

    # ========== STAGE 1B: HIERARCHY MAPPING ==========
    stage1b_path = None

    if should_run('1b'):
        print("=" * 80)
        print("STAGE 1B: HIERARCHY MAPPING (NO LLM)")
        print("=" * 80)
        print()

        stage_start = time.time()

        try:
            stage1b_data, stage1b_path = run_stage_1b(
                docx_path=cv_path,
                hierarchy_json_path=stage1a_result['output_file']
            )
            stage_duration = time.time() - stage_start
            stage_times['1b'] = stage_duration

            all_results['stage_1b'] = {
                'output_file': str(stage1b_path),
                'total_sections': stage1b_data['meta']['total_sections'],
                'leaf_sections': stage1b_data['meta']['leaf_sections'],
                'duration': stage_duration
            }
            print()
            print("Stage 1b Complete")
            print(f"  Output: {stage1b_path}")
            print(f"  Total sections: {stage1b_data['meta']['total_sections']}")
            print(f"  Leaf sections: {stage1b_data['meta']['leaf_sections']}")
            print(f"  Time: {format_duration(stage_duration)}")
            print()
        except Exception as e:
            print(f"  Warning: Stage 1b failed: {e}")
            print("  Continuing with remaining stages...")
            all_results['stage_1b'] = {'error': str(e)}
            print()
    else:
        # Load existing Stage 1b output path
        stage1b_path = output_paths['1b']
        if stage1b_path.exists():
            all_results['stage_1b'] = {'output_file': str(stage1b_path)}
            if target_stage is None:  # Only show skip message in full pipeline mode
                print(f"[Skipped] Stage 1b - using existing output: {stage1b_path}")

    # ========== STAGE 2: ENTRY EXTRACTION ==========
    stage2_path = None

    if should_run('2'):
        print("=" * 80)
        print("STAGE 2: ENTRY EXTRACTION")
        print("=" * 80)
        print()

        stage_start = time.time()

        if stage1b_path:
            try:
                stage2_data, stage2_path = run_stage_2(
                    docx_path=cv_path,
                    hierarchy_json_path=str(stage1b_path)
                )
                stage2_cost = stage2_data.get('total_cost', 0)
                total_cost += stage2_cost
                entries_found = stage2_data.get('total_entries', 0)

                stage_duration = time.time() - stage_start
                stage_times['2'] = stage_duration

                all_results['stage_2'] = {
                    'output_file': str(stage2_path),
                    'total_entries': entries_found,
                    'cost': stage2_cost,
                    'duration': stage_duration
                }
                print()
                print("Stage 2 Complete")
                print(f"  Output: {stage2_path}")
                print(f"  Entries extracted: {entries_found}")
                print(f"  Cost: ${stage2_cost:.4f}")
                print(f"  Time: {format_duration(stage_duration)}")
                print()
            except Exception as e:
                print(f"  Warning: Stage 2 failed: {e}")
                print("  Continuing with remaining stages...")
                all_results['stage_2'] = {'error': str(e)}
                print()
        else:
            print("  Skipped: Stage 1b output required")
            all_results['stage_2'] = {'skipped': 'Stage 1b required'}
            print()
    else:
        # Load existing Stage 2 output path
        stage2_path = output_paths['2']
        if stage2_path.exists():
            all_results['stage_2'] = {'output_file': str(stage2_path)}
            if target_stage is None:  # Only show skip message in full pipeline mode
                print(f"[Skipped] Stage 2 - using existing output: {stage2_path}")

    # ========== STAGE 3A: HEADER TAXONOMY MAPPING ==========
    stage3a_path = None
    stage3a_cost = 0.0

    if should_run('3a'):
        print("=" * 80)
        print("STAGE 3A: HEADER TAXONOMY MAPPING")
        print("=" * 80)
        print()

        stage_start = time.time()

        try:
            stage3a_result = run_stage_3a(
                document_uid=document_uid
            )
            stage3a_path = stage3a_result['output_path']
            stage3a_cost = stage3a_result['stats']['cost']
            total_cost += stage3a_cost

            stage_duration = time.time() - stage_start
            stage_times['3a'] = stage_duration

            all_results['stage_3a'] = {
                'output_file': stage3a_path,
                'node_count': stage3a_result['node_count'],
                'cost': stage3a_cost,
                'duration': stage_duration
            }
            print()
            print("Stage 3a Complete")
            print(f"  Output: {stage3a_path}")
            print(f"  Header nodes mapped: {stage3a_result['node_count']}")
            print(f"  Cost: ${stage3a_cost:.4f}")
            print(f"  Time: {format_duration(stage_duration)}")
            print()
        except Exception as e:
            print(f"  Warning: Stage 3a failed: {e}")
            all_results['stage_3a'] = {'error': str(e)}
            print()
    else:
        # Load existing Stage 3a output path
        stage3a_path = output_paths['3a']
        if stage3a_path.exists():
            stage3a_path = str(stage3a_path)
            all_results['stage_3a'] = {'output_file': stage3a_path}
            if target_stage is None:  # Only show skip message in full pipeline mode
                print(f"[Skipped] Stage 3a - using existing output: {stage3a_path}")

    # ========== STAGE 3B: ENTRY CLASSIFICATION ==========
    stage3b_cost = 0.0

    if should_run('3b'):
        print("=" * 80)
        print("STAGE 3B: ENTRY CLASSIFICATION")
        print("=" * 80)
        print()

        stage_start = time.time()

        # Stage 3b requires Stage 2 and Stage 3a outputs
        if stage2_path and stage3a_path:
            try:
                stage3b_result = run_stage_3b(
                    document_uid=document_uid,
                    stage_3a_path=stage3a_path
                )
                stage3b_cost = stage3b_result['stats']['cost']
                total_cost += stage3b_cost

                stage_duration = time.time() - stage_start
                stage_times['3b'] = stage_duration

                all_results['stage_3b'] = {
                    'output_file': stage3b_result['output_path'],
                    'entries_classified': stage3b_result['total_entries'],
                    'cost': stage3b_cost,
                    'code_distribution': stage3b_result.get('code_distribution', {}),
                    'duration': stage_duration
                }
                print()
                print("Stage 3b Complete")
                print(f"  Output: {stage3b_result['output_path']}")
                print(f"  Entries classified: {stage3b_result['total_entries']}")
                print(f"  Cost: ${stage3b_cost:.4f}")
                print(f"  Time: {format_duration(stage_duration)}")
                print()
            except Exception as e:
                print(f"  Warning: Stage 3b failed: {e}")
                all_results['stage_3b'] = {'error': str(e)}
                print()
        else:
            missing = []
            if not stage2_path:
                missing.append("Stage 2")
            if not stage3a_path:
                missing.append("Stage 3a")
            print(f"  Skipped: {' and '.join(missing)} output required")
            all_results['stage_3b'] = {'skipped': f'{", ".join(missing)} required'}
            print()

    # ========== STAGE 4: FIELD EXTRACTION ==========
    stage4_cost = 0.0
    stage3b_output_path = None

    # Get Stage 3b output path (either just ran or from existing)
    if 'stage_3b' in all_results and 'output_file' in all_results['stage_3b']:
        stage3b_output_path = all_results['stage_3b']['output_file']
    elif output_paths['3b'].exists():
        stage3b_output_path = str(output_paths['3b'])

    if should_run('4'):
        print("=" * 80)
        print("STAGE 4: FIELD EXTRACTION")
        print("=" * 80)
        print()

        stage_start = time.time()

        if stage3b_output_path:
            try:
                # run_stage_4 expects a document path/UID, not the 3b output path
                stage4_result = run_stage_4(
                    docx_path=f"{document_uid}.docx"  # Uses UID to find 3b output
                )
                stage4_output = stage4_result['output']
                stage4_cost = stage4_output.get('total_cost', 0)
                total_cost += stage4_cost

                stage_duration = time.time() - stage_start
                stage_times['4'] = stage_duration

                all_results['stage_4'] = {
                    'output_file': stage4_result['output_path'],
                    'entries_extracted': stage4_output.get('total_entries', 0),
                    'cost': stage4_cost,
                    'stats': stage4_output.get('stats', {}),
                    'duration': stage_duration
                }
                print()
                print("Stage 4 Complete")
                print(f"  Output: {stage4_result['output_path']}")
                print(f"  Entries with fields: {stage4_output.get('stats', {}).get('extracted', 0)}")
                print(f"  Cost: ${stage4_cost:.4f}")
                print(f"  Time: {format_duration(stage_duration)}")
                print()
            except Exception as e:
                print(f"  Warning: Stage 4 failed: {e}")
                import traceback
                traceback.print_exc()
                all_results['stage_4'] = {'error': str(e)}
                print()
        else:
            print("  Skipped: Stage 3b output required")
            all_results['stage_4'] = {'skipped': 'Stage 3b required'}
            print()

    # ========== STAGE 4.5: RESEARCH SUMMARY GENERATION ==========
    stage45_cost = 0.0
    stage4_output_path = None

    # Get Stage 4 output path (either just ran or from existing)
    if 'stage_4' in all_results and 'output_file' in all_results['stage_4']:
        stage4_output_path = all_results['stage_4']['output_file']
    elif output_paths['4'].exists():
        stage4_output_path = str(output_paths['4'])

    if should_run('4.5'):
        print("=" * 80)
        print("STAGE 4.5: RESEARCH SUMMARY GENERATION")
        print("=" * 80)
        print()

        stage_start = time.time()

        if stage4_output_path:
            try:
                stage45_output_path = run_stage_4_5(
                    input_path=stage4_output_path,
                    verbose=True
                )

                # Read the output to get stats
                with open(stage45_output_path, 'r') as f:
                    stage45_data = json.load(f)

                stage_duration = time.time() - stage_start
                stage_times['4.5'] = stage_duration

                research_summary_info = stage45_data.get('research_summary', {})
                all_results['stage_4.5'] = {
                    'output_file': stage45_output_path,
                    'method': research_summary_info.get('method', 'unknown'),
                    'm1_score': research_summary_info.get('m1_score', 0),
                    'summary_length': research_summary_info.get('summary_length', 0),
                    'duration': stage_duration
                }
                print()
                print("Stage 4.5 Complete")
                print(f"  Output: {stage45_output_path}")
                print(f"  Method: {research_summary_info.get('method', 'unknown')}")
                print(f"  M1 Score: {research_summary_info.get('m1_score', 0):.2f}")
                print(f"  Summary length: {research_summary_info.get('summary_length', 0)} chars")
                print(f"  Time: {format_duration(stage_duration)}")
                print()
            except Exception as e:
                print(f"  Warning: Stage 4.5 failed: {e}")
                import traceback
                traceback.print_exc()
                all_results['stage_4.5'] = {'error': str(e)}
                print()
        else:
            print("  Skipped: Stage 4 output required")
            all_results['stage_4.5'] = {'skipped': 'Stage 4 required'}
            print()

    # ========== STAGE 5: PUBMED ENRICHMENT ==========
    stage5_output_path = None
    if should_run('5'):
        print("=" * 80)
        print("STAGE 5: PUBMED ENRICHMENT")
        print("=" * 80)
        print()

        stage_start = time.time()

        # Find input (prefer Stage 4 output)
        input_for_stage5 = stage4_output_path
        if not input_for_stage5:
            # Try to find existing Stage 4 output
            stage4_dir = Path('src/unified_pipeline/outputs/stage_4_field_extraction')
            candidates = list(stage4_dir.glob(f"*{document_uid}*_fields.json"))
            if candidates:
                input_for_stage5 = str(candidates[0])

        if input_for_stage5:
            try:
                stage5_result = run_stage5(
                    stage4_path=input_for_stage5,
                    verbose=True
                )
                # run_stage5 returns a dict; output path is in stage_5_enrichment
                stage5_output_path = f"src/unified_pipeline/outputs/stage_5_enrichment/{document_uid}_enriched.json"

                stage_duration = time.time() - stage_start
                stage_times['5'] = stage_duration

                all_results['stage_5'] = {
                    'output_file': stage5_output_path,
                    'result': stage5_result,
                    'duration': stage_duration
                }
                print()
                print("Stage 5 Complete")
                print(f"  Output: {stage5_output_path}")
                print(f"  Time: {format_duration(stage_duration)}")
                print()
            except Exception as e:
                print(f"  Warning: Stage 5 failed: {e}")
                all_results['stage_5'] = {'error': str(e)}
                print()
        else:
            print("  Skipped: Stage 4 output required")
            all_results['stage_5'] = {'skipped': 'Stage 4 required'}
            print()

    # ========== STAGE 5B: INSTITUTION ENRICHMENT ==========
    stage5b_output_path = None
    if should_run('5b'):
        print("=" * 80)
        print("STAGE 5B: INSTITUTION ENRICHMENT")
        print("=" * 80)
        print()

        stage_start = time.time()

        # Find input (prefer Stage 5 output, fall back to Stage 4)
        input_for_stage5b = stage5_output_path or stage4_output_path
        if not input_for_stage5b:
            # Try to find existing outputs
            for stage_dir, pattern in [
                ('stage_5_enrichment', '*_enriched.json'),
                ('stage_4_field_extraction', '*_fields.json')
            ]:
                candidates = list(Path(f'src/unified_pipeline/outputs/{stage_dir}').glob(f"*{document_uid}*{pattern.split('*')[1]}"))
                if candidates:
                    input_for_stage5b = str(candidates[0])
                    break

        if input_for_stage5b:
            try:
                stage5b_output_path = run_stage5b(
                    input_path=input_for_stage5b,
                    verbose=True
                )

                # Read output to get enrichment stats and cost
                stage5b_cost = 0.0
                try:
                    with open(stage5b_output_path, 'r') as f:
                        stage5b_data = json.load(f)
                    stage5b_stats = stage5b_data.get('institution_enrichment_stats', {})
                    stage5b_cost = stage5b_stats.get('cost', 0.0)
                    total_cost += stage5b_cost
                except Exception:
                    pass

                stage_duration = time.time() - stage_start
                stage_times['5b'] = stage_duration

                all_results['stage_5b'] = {
                    'output_file': stage5b_output_path,
                    'cost': stage5b_cost,
                    'duration': stage_duration
                }
                print()
                print("Stage 5b Complete")
                print(f"  Output: {stage5b_output_path}")
                if stage5b_cost > 0:
                    print(f"  Cost: ${stage5b_cost:.4f}")
                print(f"  Time: {format_duration(stage_duration)}")
                print()
            except Exception as e:
                print(f"  Warning: Stage 5b failed: {e}")
                all_results['stage_5b'] = {'error': str(e)}
                print()
        else:
            print("  Skipped: Stage 4 or 5 output required")
            all_results['stage_5b'] = {'skipped': 'Stage 4 or 5 required'}
            print()

    # ========== STAGE 5C: TEACHING FORMATTER ==========
    stage5c_output_path = None
    if should_run('5c'):
        print("=" * 80)
        print("STAGE 5C: TEACHING FORMATTER")
        print("=" * 80)
        print()

        stage_start = time.time()

        # Find input (prefer Stage 5b > 5 > 4)
        input_for_stage5c = stage5b_output_path or stage5_output_path or stage4_output_path
        if not input_for_stage5c:
            # Try to find existing outputs
            for stage_dir, pattern in [
                ('stage_5b_institution_enrichment', '*_institution_enriched.json'),
                ('stage_5_enrichment', '*_enriched.json'),
                ('stage_4_field_extraction', '*_fields.json')
            ]:
                candidates = list(Path(f'src/unified_pipeline/outputs/{stage_dir}').glob(f"*{document_uid}*{pattern.split('*')[1]}"))
                if candidates:
                    input_for_stage5c = str(candidates[0])
                    break

        if input_for_stage5c:
            try:
                stage5c_output_path = run_stage_5c(
                    input_path=input_for_stage5c,
                    verbose=True
                )

                stage_duration = time.time() - stage_start
                stage_times['5c'] = stage_duration

                all_results['stage_5c'] = {
                    'output_file': stage5c_output_path,
                    'duration': stage_duration
                }
                print()
                print("Stage 5c Complete")
                print(f"  Output: {stage5c_output_path}")
                print(f"  Time: {format_duration(stage_duration)}")
                print()
            except Exception as e:
                print(f"  Warning: Stage 5c failed: {e}")
                import traceback
                traceback.print_exc()
                all_results['stage_5c'] = {'error': str(e)}
                print()
        else:
            print("  Skipped: Stage 4, 5, or 5b output required")
            all_results['stage_5c'] = {'skipped': 'Earlier stage output required'}
            print()

    # ========== STAGE 5D: CITATION FORMATTER ==========
    stage5d_output_path = None
    if should_run('5d'):
        print("=" * 80)
        print("STAGE 5D: CITATION FORMATTER (NON-ENRICHED)")
        print("=" * 80)
        print()

        stage_start = time.time()

        # Find input (prefer Stage 5c > 5b > 5 > 4)
        input_for_stage5d = stage5c_output_path or stage5b_output_path or stage5_output_path or stage4_output_path
        if not input_for_stage5d:
            # Try to find existing outputs
            for stage_dir, pattern in [
                ('stage_5c_teaching_formatted', '*_teaching_formatted.json'),
                ('stage_5b_institution_enrichment', '*_institution_enriched.json'),
                ('stage_5_enrichment', '*_enriched.json'),
                ('stage_4_field_extraction', '*_fields.json')
            ]:
                candidates = list(Path(f'src/unified_pipeline/outputs/{stage_dir}').glob(f"*{document_uid}*{pattern.split('*')[1]}"))
                if candidates:
                    input_for_stage5d = str(candidates[0])
                    break

        if input_for_stage5d:
            try:
                stage5d_output_path = run_stage_5d(
                    input_path=input_for_stage5d,
                    verbose=True
                )

                stage_duration = time.time() - stage_start
                stage_times['5d'] = stage_duration

                all_results['stage_5d'] = {
                    'output_file': stage5d_output_path,
                    'duration': stage_duration
                }
                print()
                print("Stage 5d Complete")
                print(f"  Output: {stage5d_output_path}")
                print(f"  Time: {format_duration(stage_duration)}")
                print()
            except Exception as e:
                print(f"  Warning: Stage 5d failed: {e}")
                import traceback
                traceback.print_exc()
                all_results['stage_5d'] = {'error': str(e)}
                print()
        else:
            print("  Skipped: Earlier stage output required")
            all_results['stage_5d'] = {'skipped': 'Earlier stage output required'}
            print()

    # ========== STAGE 6: WCM WORD TEMPLATE ==========
    stage6_output_path = None
    if should_run('6'):
        print("=" * 80)
        print("STAGE 6: WCM WORD TEMPLATE GENERATION")
        print("=" * 80)
        print()

        stage_start = time.time()

        # Find input - ALWAYS search for best available file (prefer Stage 5d > 5c > 5b > 5 > 4)
        # This handles cases where later stages were run separately
        input_for_stage6 = None
        for stage_dir, pattern in [
            ('stage_5d_citation_formatted', f'*{document_uid}*_citation_formatted.json'),
            ('stage_5c_teaching_formatted', f'*{document_uid}*_teaching_formatted.json'),
            ('stage_5b_institution_enrichment', f'*{document_uid}*_institution_enriched.json'),
            ('stage_5_enrichment', f'*{document_uid}*_enriched.json'),
            ('stage_4_field_extraction', f'*{document_uid}*_fields.json')
        ]:
            candidates = list(Path(f'src/unified_pipeline/outputs/{stage_dir}').glob(pattern))
            if candidates:
                input_for_stage6 = str(candidates[0])
                break

        # Fall back to variables if no files found (shouldn't happen normally)
        if not input_for_stage6:
            input_for_stage6 = stage5d_output_path or stage5c_output_path or stage5b_output_path or stage5_output_path or stage4_output_path

        if input_for_stage6:
            try:
                stage6_output_path = run_stage6(
                    input_path=input_for_stage6,
                    verbose=True
                )

                stage_duration = time.time() - stage_start
                stage_times['6'] = stage_duration

                all_results['stage_6'] = {
                    'output_file': stage6_output_path,
                    'duration': stage_duration
                }
                print()
                print("Stage 6 Complete")
                print(f"  Output: {stage6_output_path}")
                print(f"  Time: {format_duration(stage_duration)}")
                print()
            except Exception as e:
                print(f"  Warning: Stage 6 failed: {e}")
                import traceback
                traceback.print_exc()
                all_results['stage_6'] = {'error': str(e)}
                print()
        else:
            print("  Skipped: Stage 4, 5, or 5b output required")
            all_results['stage_6'] = {'skipped': 'Earlier stage output required'}
            print()

    # ========== SUMMARY ==========
    total_duration = time.time() - pipeline_start_time

    print("=" * 80)
    print("PIPELINE COMPLETE")
    print("=" * 80)
    print(f"Document: {document_uid}")
    print(f"Model: {model}")
    print()
    print("Outputs:")
    if stage1a_result:
        print(f"  Stage 1a: {stage1a_result['output_file']}")
    if 'stage_1b' in all_results and 'output_file' in all_results['stage_1b']:
        print(f"  Stage 1b: {all_results['stage_1b']['output_file']}")
    if 'stage_2' in all_results and 'output_file' in all_results['stage_2']:
        print(f"  Stage 2:  {all_results['stage_2']['output_file']}")
    if 'stage_3a' in all_results and 'output_file' in all_results['stage_3a']:
        print(f"  Stage 3a: {all_results['stage_3a']['output_file']}")
    if 'stage_3b' in all_results and 'output_file' in all_results['stage_3b']:
        print(f"  Stage 3b: {all_results['stage_3b']['output_file']}")
    if 'stage_4' in all_results and 'output_file' in all_results['stage_4']:
        print(f"  Stage 4:  {all_results['stage_4']['output_file']}")
    if 'stage_4.5' in all_results and 'output_file' in all_results['stage_4.5']:
        print(f"  Stage 4.5: {all_results['stage_4.5']['output_file']}")
    if 'stage_5' in all_results and 'output_file' in all_results['stage_5']:
        print(f"  Stage 5:  {all_results['stage_5']['output_file']}")
    if 'stage_5b' in all_results and 'output_file' in all_results['stage_5b']:
        print(f"  Stage 5b: {all_results['stage_5b']['output_file']}")
    if 'stage_5c' in all_results and 'output_file' in all_results['stage_5c']:
        print(f"  Stage 5c: {all_results['stage_5c']['output_file']}")
    if 'stage_5d' in all_results and 'output_file' in all_results['stage_5d']:
        print(f"  Stage 5d: {all_results['stage_5d']['output_file']}")
    if 'stage_6' in all_results and 'output_file' in all_results['stage_6']:
        print(f"  Stage 6:  {all_results['stage_6']['output_file']}")
    print()
    print("Timing:")
    if '1a' in stage_times:
        print(f"  Stage 1a: {format_duration(stage_times['1a'])}")
    if '1b' in stage_times:
        print(f"  Stage 1b: {format_duration(stage_times['1b'])}")
    if '2' in stage_times:
        print(f"  Stage 2:  {format_duration(stage_times['2'])}")
    if '3a' in stage_times:
        print(f"  Stage 3a: {format_duration(stage_times['3a'])}")
    if '3b' in stage_times:
        print(f"  Stage 3b: {format_duration(stage_times['3b'])}")
    if '4' in stage_times:
        print(f"  Stage 4:  {format_duration(stage_times['4'])}")
    if '4.5' in stage_times:
        print(f"  Stage 4.5: {format_duration(stage_times['4.5'])}")
    if '5' in stage_times:
        print(f"  Stage 5:  {format_duration(stage_times['5'])}")
    if '5b' in stage_times:
        print(f"  Stage 5b: {format_duration(stage_times['5b'])}")
    if '5c' in stage_times:
        print(f"  Stage 5c: {format_duration(stage_times['5c'])}")
    if '5d' in stage_times:
        print(f"  Stage 5d: {format_duration(stage_times['5d'])}")
    if '6' in stage_times:
        print(f"  Stage 6:  {format_duration(stage_times['6'])}")
    print(f"  Total:    {format_duration(total_duration)}")
    print()
    print("Costs:")
    if stage1a_cost > 0:
        print(f"  Stage 1a: ${stage1a_cost:.4f}")
    if 'stage_2' in all_results and 'cost' in all_results['stage_2']:
        print(f"  Stage 2:  ${all_results['stage_2']['cost']:.4f}")
    if 'stage_3a' in all_results and 'cost' in all_results['stage_3a']:
        print(f"  Stage 3a: ${all_results['stage_3a']['cost']:.4f}")
    if 'stage_3b' in all_results and 'cost' in all_results['stage_3b']:
        print(f"  Stage 3b: ${all_results['stage_3b']['cost']:.4f}")
    if 'stage_4' in all_results and 'cost' in all_results['stage_4']:
        print(f"  Stage 4:  ${all_results['stage_4']['cost']:.4f}")
    if 'stage_5b' in all_results and 'cost' in all_results['stage_5b'] and all_results['stage_5b']['cost'] > 0:
        print(f"  Stage 5b: ${all_results['stage_5b']['cost']:.4f}")
    print(f"  Total:    ${total_cost:.4f}")
    print()
    print("Processing Stats:")
    if stage1a_result:
        print(f"  Top-level sections: {stage1a_result['num_sections']}")
        print(f"  Total headers: {stage1a_result['total_headers']}")
    if 'stage_2' in all_results and 'total_entries' in all_results['stage_2']:
        print(f"  Entries extracted: {all_results['stage_2']['total_entries']}")
    if 'stage_3a' in all_results and 'node_count' in all_results['stage_3a']:
        print(f"  Header nodes mapped: {all_results['stage_3a']['node_count']}")
    if 'stage_3b' in all_results and 'entries_classified' in all_results['stage_3b']:
        print(f"  Entries classified: {all_results['stage_3b']['entries_classified']}")
    if 'stage_4' in all_results and 'entries_extracted' in all_results['stage_4']:
        print(f"  Fields extracted: {all_results['stage_4']['entries_extracted']}")

    # Show code distribution if available
    if 'stage_3b' in all_results and 'code_distribution' in all_results['stage_3b']:
        code_dist = all_results['stage_3b']['code_distribution']
        if code_dist:
            print()
            print("Code Distribution (top 10):")
            sorted_codes = sorted(code_dist.items(), key=lambda x: -x[1])[:10]
            for code, count in sorted_codes:
                print(f"  {code}: {count}")
    print()


if __name__ == '__main__':
    main()
