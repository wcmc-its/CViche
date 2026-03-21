#!/usr/bin/env python3
"""
Reinsert Paragraph Delimiters into Stage 1 Segmented Output

Problem: Stage 1 segmentation lumps multiple paragraphs together, losing line break information.
Solution: Go back to original Word doc, find paragraph boundaries, insert "|" delimiter.

Usage:
    python3 reinsert_paragraph_delimiters.py \\
        --docx /path/to/original.docx \\
        --segmented /path/to/segmented.json \\
        --output /path/to/segmented_with_delimiters.json
"""

import json
import re
from pathlib import Path
from typing import List, Dict, Any
import argparse
from .docx_structure_extractor import extract_docx_structure


def normalize_text(text: str) -> str:
    """Normalize text for fuzzy matching."""
    # Remove extra whitespace
    text = re.sub(r'\s+', ' ', text)
    # Remove common OCR/formatting artifacts
    text = text.replace('\t', ' ')
    return text.strip()


def find_paragraph_boundaries(lumped_text: str, paragraphs: List[str]) -> List[int]:
    """
    Find where paragraph boundaries should be in lumped text.

    Returns list of character positions where "|" should be inserted.
    """
    boundaries = []

    # Normalize lumped text
    norm_lumped = normalize_text(lumped_text)

    # Try to find each paragraph in the lumped text
    search_start = 0

    for para_text in paragraphs:
        if not para_text.strip():
            continue

        norm_para = normalize_text(para_text)

        # Skip if paragraph is too short (likely just whitespace or artifacts)
        if len(norm_para) < 5:
            continue

        # Find this paragraph in the lumped text (exact match only)
        pos = norm_lumped.find(norm_para, search_start)

        if pos >= 0:
            # Found it! Mark the boundary at the end of this paragraph
            boundary_pos = pos + len(norm_para)
            boundaries.append(boundary_pos)
            search_start = boundary_pos
        # Note: Fuzzy matching removed - was causing mid-word boundary insertion
        # when paragraphs were truncated in the lumped text

    # Remove duplicates and sort
    boundaries = sorted(set(boundaries))

    return boundaries


def insert_delimiters(text: str, boundaries: List[int], delimiter: str = " | ") -> str:
    """
    Insert delimiters at specified character positions.
    """
    if not boundaries:
        return text

    # Work backwards to avoid position shifting
    result = text
    for pos in reversed(boundaries):
        if 0 < pos < len(result):
            result = result[:pos] + delimiter + result[pos:]

    return result


def reinsert_delimiters_from_word(
    docx_path: str,
    segmented_json_path: str,
    output_path: str
) -> Dict[str, Any]:
    """
    Main function: reinsert paragraph delimiters into segmented JSON.
    """
    print("="*80)
    print("REINSERTING PARAGRAPH DELIMITERS")
    print("="*80)
    print(f"Word doc: {docx_path}")
    print(f"Segmented JSON: {segmented_json_path}")
    print()

    # Step 1: Extract Word document structure
    print("Step 1: Extracting paragraph structure from Word doc...")
    structure = extract_docx_structure(docx_path)
    elements = structure.get('elements', [])

    # Get all paragraph texts in order
    paragraphs = []
    for elem in elements:
        if elem.get('type') == 'paragraph':
            text = elem.get('text', '')
            if text.strip():
                paragraphs.append(text)

    print(f"  Found {len(paragraphs)} paragraphs in Word doc")

    # Step 2: Load segmented JSON
    print("\nStep 2: Loading segmented JSON...")
    with open(segmented_json_path, 'r') as f:
        segmented = json.load(f)

    # Step 3: Process each entry, adding delimiters
    print("\nStep 3: Inserting delimiters into entries...")

    entries_processed = 0
    entries_with_delimiters = 0
    total_delimiters = 0

    for group in segmented.get('groups', []):
        # Process main group entries
        for entry in group.get('entries', []):
            entries_processed += 1
            text = entry.get('text_snippet', '')

            # Skip if already has delimiters
            if ' | ' in text:
                continue

            # Skip short entries (likely single items)
            if len(text) < 500:
                continue

            # Find paragraph boundaries
            boundaries = find_paragraph_boundaries(text, paragraphs)

            if len(boundaries) > 0:
                # Insert delimiters
                new_text = insert_delimiters(text, boundaries, " | ")
                entry['text_snippet'] = new_text

                delimiter_count = new_text.count(' | ')
                if delimiter_count > 0:
                    entries_with_delimiters += 1
                    total_delimiters += delimiter_count

        # Process subgroup entries
        for subgroup in group.get('subgroups', []):
            for entry in subgroup.get('entries', []):
                entries_processed += 1
                text = entry.get('text_snippet', '')

                if ' | ' in text:
                    continue

                if len(text) < 500:
                    continue

                boundaries = find_paragraph_boundaries(text, paragraphs)

                if len(boundaries) > 0:
                    new_text = insert_delimiters(text, boundaries, " | ")
                    entry['text_snippet'] = new_text

                    delimiter_count = new_text.count(' | ')
                    if delimiter_count > 0:
                        entries_with_delimiters += 1
                        total_delimiters += delimiter_count

    # Step 4: Save output
    print("\nStep 4: Saving output...")
    with open(output_path, 'w') as f:
        json.dump(segmented, f, indent=2, ensure_ascii=False)

    print()
    print("="*80)
    print("DELIMITER INSERTION COMPLETE")
    print("="*80)
    print(f"Entries processed: {entries_processed}")
    print(f"Entries with new delimiters: {entries_with_delimiters}")
    print(f"Total delimiters inserted: {total_delimiters}")
    print(f"Output saved to: {output_path}")
    print("="*80)

    return segmented


def main():
    parser = argparse.ArgumentParser(
        description='Reinsert paragraph delimiters into Stage 1 segmented output'
    )
    parser.add_argument('--docx', required=True, help='Original Word document')
    parser.add_argument('--segmented', required=True, help='Segmented JSON file')
    parser.add_argument('--output', required=True, help='Output path for delimited JSON')

    args = parser.parse_args()

    reinsert_delimiters_from_word(args.docx, args.segmented, args.output)


if __name__ == '__main__':
    main()
