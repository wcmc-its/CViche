"""
Word CV Segmentation - Chunked Approach (V6)

Four-pass architecture for processing large Word CVs:
- Pass 1a: HEADER DETECTION - Identify section boundaries using 11 structural/heuristic signals
- Pass 1b: HEADER VALIDATION - Filter false headers + score header-likeness (0.0-1.0)
- Pass 2: CONTENT SEGMENTATION - Process each section's content into entries with LLM
- Pass 3: HIERARCHY BUILDING - Merge results into final hierarchical JSON structure

V6 Enhancements:
- ~850 locked headers (never removed)
- Header-likeness scoring (0.0-1.0)
- Broader filtering (fragments, citations, data blobs)
- entry_type removed from schema

Cost: ~$0.13-0.15 per CV (V6: +$0.01-0.02 for header validation)
Speed: ~30-60 seconds per CV
Scale: Unlimited (no rate limit issues)
"""

import json
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from ..core.docx_structure_extractor import extract_docx_structure
except (ImportError, ValueError):
    from core.docx_structure_extractor import extract_docx_structure

# Components split out of this file (#315 follow-up) — re-exported so any
# existing import of a helper keeps working; segment_word_cv_chunked below
# stays the public entry point.
try:
    from .cv_headers import KNOWN_CV_HEADERS, KNOWN_CV_HEADERS_SET, KNOWN_SUBSECTION_TERMS
    from .header_detection import detect_section_headers, _enforce_hierarchy_consistency
    from .chunking import chunk_section, table_to_text, MAX_CHARS_PER_SECTION, MAX_ENTRIES_PER_CALL
    from .llm_segment import segment_chunk_with_llm, merge_chunk_results
    from .hierarchy import build_hierarchy
except (ImportError, ValueError):
    from cv_headers import KNOWN_CV_HEADERS, KNOWN_CV_HEADERS_SET, KNOWN_SUBSECTION_TERMS
    from header_detection import detect_section_headers, _enforce_hierarchy_consistency
    from chunking import chunk_section, table_to_text, MAX_CHARS_PER_SECTION, MAX_ENTRIES_PER_CALL
    from llm_segment import segment_chunk_with_llm, merge_chunk_results
    from hierarchy import build_hierarchy


def segment_word_cv_chunked(docx_path: str, output_dir: str = None) -> Dict[str, Any]:
    """
    Main entry point: Segment a Word CV using three-pass chunked approach.

    Args:
        docx_path: Path to Word document to segment
        output_dir: Optional output directory for JSON

    Returns dictionary with:
    - num_sections: Number of top-level sections identified
    - total_entries: Total entries extracted
    - output_file: Path to output JSON
    - processing_stats: Detailed statistics
    """

    print("="*80)
    print("WORD CV SEGMENTATION - CHUNKED APPROACH (Scalable)")
    print("="*80)
    print(f"Input: {docx_path}")
    print()

    # Extract Word structure
    print("Extracting Word document structure...")
    structure = extract_docx_structure(docx_path)
    print(f"  ✓ Extracted {len(structure['elements'])} elements")
    print()

    # Pass 1a: Detect headers (structural/heuristic signals)
    print("PASS 1a: Header Detection (structural analysis)...")
    headers = detect_section_headers(structure)
    print(f"  ✓ Found {len(headers)} potential section headers")

    # Show detected headers
    for h in headers[:10]:  # Show first 10
        print(f"    [{h['level']}] {h['text']} (confidence: {h['confidence']:.2f})")
    if len(headers) > 10:
        print(f"    ... and {len(headers) - 10} more")
    print()

    # V6: Pass 1b: Header Validation & Scoring (filter false headers + assess confidence)
    print("PASS 1b: Header Validation & Scoring...")
    validation_metadata = None
    try:
        # Import header validation (separate module for clean separation)
        try:
            from .header_validator import filter_false_headers
        except (ImportError, ValueError):
            from header_validator import filter_false_headers

        validation_result = filter_false_headers(
            sections=headers,
            batch_size=20,
            use_llm=True,
        )

        # Update headers with filtered list
        headers = validation_result['filtered_sections']
        removed = validation_result['removed_sections']
        stats = validation_result['stats']
        cost = validation_result['cost']

        # Store validation metadata for final JSON output
        validation_metadata = {
            'validation_applied': True,
            'headers_detected': stats['total_headers_input'],
            'headers_locked': stats['locked_headers'],
            'headers_removed_regex': stats['removed_by_regex'],
            'headers_removed_llm': stats['removed_by_llm'],
            'headers_kept': stats['total_kept'],
            'validation_cost': cost,
            'removed_headers': [
                {
                    'text': r.get('text', ''),
                    'reason': r.get('removal_reason', ''),
                    'filter_method': r.get('filter_method', ''),
                    'score': r.get('header_likeness_score', 0.0)
                }
                for r in removed
            ]
        }

        print(f"  ✓ Validated {stats['total_headers_input']} headers")
        print(f"  ✓ Removed {stats['total_removed']} false headers (${cost:.4f})")
        if removed:
            print(f"    Removed: {', '.join([r['text'][:30] + '...' if len(r['text']) > 30 else r['text'] for r in removed[:5]])}")
            if len(removed) > 5:
                print(f"    ... and {len(removed) - 5} more")
        print(f"  ✓ Final header count: {len(headers)}")
        print()

    except ImportError as e:
        print(f"  ⚠️  Header validation unavailable: {e}")
        print(f"  ⚠️  Continuing with unvalidated headers...")
        validation_metadata = {'validation_applied': False, 'validation_error': str(e)}
        print()

    # Pass 2: Process each section
    print("PASS 2: Processing sections with chunking...")
    groups = []
    group_counter = 1
    total_chunks_processed = 0

    # Track token usage and cost (initialize with Pass 1b validation cost)
    total_prompt_tokens = 0
    total_completion_tokens = 0
    total_tokens = 0
    total_cost = 0.0

    # Add Pass 1b validation cost if available
    if validation_metadata and 'validation_cost' in validation_metadata:
        total_cost += validation_metadata['validation_cost']

    elements = structure['elements']

    for i, header in enumerate(headers):
        # Determine section boundaries
        start_idx = header['idx']
        end_idx = headers[i+1]['idx'] if i+1 < len(headers) else len(elements)

        section_label = header['text']
        print(f"  Processing: {section_label}...")

        # Chunk this section if needed
        chunks = chunk_section(elements, start_idx, end_idx)
        print(f"    → {len(chunks)} chunk(s)")

        # Process each chunk
        chunk_results = []
        for chunk_idx, chunk in enumerate(chunks):
            chunk_chars = sum(len(e.get('text', '')) for e in chunk)
            print(f"       Chunk {chunk_idx+1}: {len(chunk)} elements, {chunk_chars:,} characters")

            result = segment_chunk_with_llm(chunk, section_label, header['level'])
            chunk_results.append(result)
            total_chunks_processed += 1

            # Aggregate token usage and cost
            if 'token_usage' in result:
                usage = result['token_usage']
                total_prompt_tokens += usage.get('prompt_tokens', 0)
                total_completion_tokens += usage.get('completion_tokens', 0)
                total_tokens += usage.get('total_tokens', 0)
            if 'cost' in result:
                total_cost += result['cost']

        # Merge chunks for this section
        group_id = f"G{group_counter}"
        merged_group = merge_chunk_results(chunk_results, header, group_id)
        groups.append(merged_group)

        print(f"    ✓ Extracted {len(merged_group['entries'])} entries")
        group_counter += 1

    print()
    print(f"PASS 3: Building hierarchical structure...")

    # Pass 3b: Build hierarchy from flat groups
    hierarchical_groups = build_hierarchy(groups)

    # Count hierarchical statistics
    def count_all_groups(groups):
        """Recursively count all groups and subgroups"""
        count = len(groups)
        for g in groups:
            count += count_all_groups(g.get('subgroups', []))
        return count

    total_groups_hierarchical = count_all_groups(hierarchical_groups)
    print(f"  ✓ Organized {len(groups)} flat sections into {len(hierarchical_groups)} top-level groups")
    print(f"     (total groups including subgroups: {total_groups_hierarchical})")

    # Calculate total entries (including nested)
    def count_all_entries(groups):
        """Recursively count all entries in groups and subgroups"""
        count = 0
        for g in groups:
            count += len(g.get('entries', []))
            count += count_all_entries(g.get('subgroups', []))
        return count

    total_entries = count_all_entries(hierarchical_groups)

    # Build final JSON structure
    document_uid = Path(docx_path).stem

    result = {
        "document_uid": document_uid,
        "meta": {
            "num_top_level_groups": len(hierarchical_groups),
            "total_groups_including_subgroups": total_groups_hierarchical,
            "total_entries": total_entries,
            "processing_method": "chunked_word_segmentation_hierarchical",
            "chunks_processed": total_chunks_processed
        },
        "groups": hierarchical_groups
    }

    # Add validation metadata if available (V6: Pass 1b)
    if validation_metadata:
        result['meta']['header_validation'] = validation_metadata

    # Save output using OutputManager for consistent paths
    from ..core.output_manager import OutputManager

    if output_dir is None:
        # Use default output structure
        om = OutputManager(docx_path)
        output_file = om.get_stage1_json_path()
        output_txt = om.get_stage1_txt_path()
    else:
        # Use specified output directory
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"{document_uid}_segmented.json"
        output_txt = output_dir / f"{document_uid}_segmented.txt"

    # Save JSON output
    with open(output_file, 'w') as f:
        json.dump(result, f, indent=2)

    print(f"  ✓ Saved JSON to: {output_file}")

    # Also save human-readable text version (hierarchy only, matching signature_based format)
    try:
        with open(output_txt, 'w', encoding='utf-8') as f:
            f.write(f"CV Hierarchy: {document_uid}\n")
            f.write("=" * 80 + "\n\n")

            def write_group(group, depth=0):
                indent = "  " * depth
                # Use label_inferred (the correct key in the JSON schema)
                label = group.get('label_inferred') or group.get('title', 'NO TITLE')
                # Convert numeric level to H1/H2/H3 format
                level_num = group.get('level', 1)
                level_marker = f"[H{level_num}]"
                f.write(f"{indent}{level_marker} {label}\n")
                if 'subgroups' in group and group['subgroups']:
                    for subgroup in group['subgroups']:
                        write_group(subgroup, depth + 1)

            for group in hierarchical_groups:
                write_group(group)

        print(f"  ✓ Saved TXT to: {output_txt}")
    except Exception as e:
        print(f"  ⚠ Could not save TXT version: {e}")

    print()

    # Return summary with token usage
    return {
        "num_sections": len(hierarchical_groups),
        "num_sections_including_subgroups": total_groups_hierarchical,
        "total_entries": total_entries,
        "output_file": str(output_file),
        "chunks_processed": total_chunks_processed,
        "format": "docx",
        "approach": "chunked-word-segmentation-hierarchical",
        "total_cost": total_cost,
        "token_usage": {
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "total_tokens": total_tokens
        }
    }


if __name__ == '__main__':
    import sys

    if len(sys.argv) < 2:
        print("Usage: python word_cv_segmentation_chunked.py <docx_file> [output_dir]")
        print()
        print("Example:")
        print("  python word_cv_segmentation_chunked.py cv.docx")
        print("  python word_cv_segmentation_chunked.py cv.docx ./outputs")
        sys.exit(1)

    docx_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None

    try:
        result = segment_word_cv_chunked(docx_path, output_dir)

        print("="*80)
        print("PROCESSING COMPLETE")
        print("="*80)
        print(f"Sections: {result['num_sections']}")
        print(f"Entries: {result['total_entries']}")
        print(f"Chunks: {result['chunks_processed']}")
        print(f"Output: {result['output_file']}")
        print("="*80)

    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
