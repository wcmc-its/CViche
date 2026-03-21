#!/usr/bin/env python3
"""
Enrich Stage 1 Segments with Original Word Document Formatting

Strategy:
1. Stage 1 performs gestalt segmentation (section identification) - keep as-is
2. For each segment, find which Word paragraphs belong to it
3. Reconstruct segment with " | " delimiters between paragraphs
4. Preserves original line break structure from Word document

This is more reliable than pattern matching because we're using the actual
paragraph boundaries from the Word document.

Usage:
    python3 enrich_segments_with_formatting.py \
        --docx /path/to/original.docx \
        --segmented /path/to/segmented.json \
        --output /path/to/segmented_enriched.json
"""

import json
import argparse
from pathlib import Path
from typing import List, Dict, Any, Tuple
from .docx_structure_extractor import extract_docx_structure


def find_paragraphs_in_segment(
    segment_text: str,
    all_paragraphs: List[str],
    min_paragraph_length: int = 10
) -> List[str]:
    """
    Find which Word paragraphs belong to a Stage 1 segment.

    Args:
        segment_text: The lumped text from Stage 1 entry
        all_paragraphs: All paragraphs from Word document in order
        min_paragraph_length: Minimum length to consider a paragraph

    Returns:
        List of paragraphs that appear in this segment
    """
    matching_paragraphs = []

    for para in all_paragraphs:
        # Skip very short paragraphs (likely artifacts)
        if len(para.strip()) < min_paragraph_length:
            continue

        # Check if this paragraph appears in the segment
        # Use exact substring matching for reliability
        if para in segment_text:
            matching_paragraphs.append(para)

    return matching_paragraphs


def reconstruct_segment_with_delimiters(
    segment_text: str,
    paragraphs: List[str],
    delimiter: str = " | "
) -> Tuple[str, int]:
    """
    Reconstruct segment text with delimiters between paragraphs.

    Args:
        segment_text: Original lumped text from Stage 1
        paragraphs: List of paragraphs that belong to this segment
        delimiter: Delimiter to insert between paragraphs

    Returns:
        Tuple of (reconstructed_text, delimiter_count)
    """
    if not paragraphs:
        return segment_text, 0

    # Join paragraphs with delimiter
    reconstructed = delimiter.join(paragraphs)
    delimiter_count = len(paragraphs) - 1  # n paragraphs = n-1 delimiters

    return reconstructed, delimiter_count


def enrich_segmented_cv(
    docx_path: str,
    segmented_json_path: str,
    output_path: str,
    min_paragraph_length: int = 10,
    min_segment_length_for_enrichment: int = 500
) -> Dict[str, Any]:
    """
    Main function: enrich Stage 1 segments with Word document formatting.

    Args:
        docx_path: Path to original Word document
        segmented_json_path: Path to Stage 1 segmented JSON
        output_path: Output path for enriched JSON
        min_paragraph_length: Minimum paragraph length to consider
        min_segment_length_for_enrichment: Only enrich segments longer than this

    Returns:
        Statistics dictionary
    """
    print("=" * 80)
    print("ENRICHING STAGE 1 SEGMENTS WITH WORD DOCUMENT FORMATTING")
    print("=" * 80)
    print(f"Word doc: {docx_path}")
    print(f"Segmented JSON: {segmented_json_path}")
    print()

    # Step 1: Extract Word document structure
    print("Step 1: Extracting Word document structure...")
    structure = extract_docx_structure(docx_path)
    elements = structure.get('elements', [])

    # Get all paragraph texts in order
    all_paragraphs = []
    for elem in elements:
        if elem.get('type') == 'paragraph':
            text = elem.get('text', '')
            if text.strip():
                all_paragraphs.append(text)

    print(f"  Found {len(all_paragraphs)} paragraphs in Word doc")

    # Step 2: Load Stage 1 segmented JSON
    print("\nStep 2: Loading Stage 1 segmented JSON...")
    with open(segmented_json_path, 'r') as f:
        segmented = json.load(f)

    # Step 3: Enrich each entry
    print("\nStep 3: Enriching entries with paragraph delimiters...")
    print()

    entries_processed = 0
    entries_enriched = 0
    total_delimiters = 0
    total_paragraphs_found = 0

    for group in segmented.get('groups', []):
        # Process main group entries
        for entry in group.get('entries', []):
            entries_processed += 1
            text = entry.get('text_snippet', '')
            entry_id = entry.get('id', 'unknown')

            # Skip short entries (likely single items, don't need enrichment)
            if len(text) < min_segment_length_for_enrichment:
                continue

            # Skip if already has delimiters
            if ' | ' in text:
                continue

            # Find paragraphs in this segment
            matching_paragraphs = find_paragraphs_in_segment(
                text,
                all_paragraphs,
                min_paragraph_length
            )

            if len(matching_paragraphs) > 1:
                # Reconstruct with delimiters
                new_text, delimiter_count = reconstruct_segment_with_delimiters(
                    text,
                    matching_paragraphs,
                    " | "
                )

                entry['text_snippet'] = new_text
                entries_enriched += 1
                total_delimiters += delimiter_count
                total_paragraphs_found += len(matching_paragraphs)

                print(f"  ✓ {entry_id:20} ({len(text):6,} chars) → {len(matching_paragraphs):3d} paragraphs, {delimiter_count:3d} delimiters")

        # Process subgroup entries
        for subgroup in group.get('subgroups', []):
            for entry in subgroup.get('entries', []):
                entries_processed += 1
                text = entry.get('text_snippet', '')
                entry_id = entry.get('id', 'unknown')

                if len(text) < min_segment_length_for_enrichment:
                    continue

                if ' | ' in text:
                    continue

                matching_paragraphs = find_paragraphs_in_segment(
                    text,
                    all_paragraphs,
                    min_paragraph_length
                )

                if len(matching_paragraphs) > 1:
                    new_text, delimiter_count = reconstruct_segment_with_delimiters(
                        text,
                        matching_paragraphs,
                        " | "
                    )

                    entry['text_snippet'] = new_text
                    entries_enriched += 1
                    total_delimiters += delimiter_count
                    total_paragraphs_found += len(matching_paragraphs)

                    print(f"  ✓ {entry_id:20} ({len(text):6,} chars) → {len(matching_paragraphs):3d} paragraphs, {delimiter_count:3d} delimiters")

    # Step 4: Save output
    print("\nStep 4: Saving enriched output...")
    with open(output_path, 'w') as f:
        json.dump(segmented, f, indent=2, ensure_ascii=False)

    print()
    print("=" * 80)
    print("ENRICHMENT COMPLETE")
    print("=" * 80)
    print(f"Entries processed: {entries_processed}")
    print(f"Entries enriched: {entries_enriched}")
    print(f"Total paragraphs found: {total_paragraphs_found}")
    print(f"Total delimiters inserted: {total_delimiters}")
    if entries_enriched > 0:
        print(f"Average paragraphs per enriched entry: {total_paragraphs_found / entries_enriched:.1f}")
    print(f"Output saved to: {output_path}")
    print("=" * 80)

    return {
        'entries_processed': entries_processed,
        'entries_enriched': entries_enriched,
        'total_paragraphs_found': total_paragraphs_found,
        'total_delimiters': total_delimiters
    }


def main():
    parser = argparse.ArgumentParser(
        description='Enrich Stage 1 segments with Word document formatting'
    )
    parser.add_argument('--docx', required=True, help='Original Word document')
    parser.add_argument('--segmented', required=True, help='Stage 1 segmented JSON file')
    parser.add_argument('--output', required=True, help='Output path for enriched JSON')
    parser.add_argument('--min-para-length', type=int, default=10,
                       help='Minimum paragraph length to consider (default: 10)')
    parser.add_argument('--min-segment-length', type=int, default=500,
                       help='Only enrich segments longer than this (default: 500)')

    args = parser.parse_args()

    enrich_segmented_cv(
        args.docx,
        args.segmented,
        args.output,
        args.min_para_length,
        args.min_segment_length
    )


if __name__ == '__main__':
    main()
