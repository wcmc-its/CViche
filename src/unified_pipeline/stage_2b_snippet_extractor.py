#!/usr/bin/env python3
"""
Stage 2b: Snippet Extraction (No LLM)

Uses delimiters from Stage 2a to extract actual text snippets from the Word document.
Simple text slicing - no LLM calls needed.

Input: Stage 2a delimiters JSON + Word document
Output: JSON with extracted text for each entry
"""

import sys
import json
from pathlib import Path
from docx import Document

# Add to path
sys.path.insert(0, str(Path(__file__).parent))

from core.output_manager import OutputManager


def collect_header_indices(hierarchy_data: dict) -> set:
    """
    Collect all element indices that represent section headers.

    These should be excluded from entry extraction since they're
    structural elements, not actual CV entries.

    Args:
        hierarchy_data: Stage 1b hierarchy mapping data

    Returns:
        Set of element indices that are section headers
    """
    header_indices = set()

    def walk_hierarchy(items):
        for item in items:
            if "element_idx" in item:
                header_indices.add(item["element_idx"])
            if "children" in item:
                walk_hierarchy(item["children"])

    if "hierarchy_with_indices" in hierarchy_data:
        walk_hierarchy(hierarchy_data["hierarchy_with_indices"])

    return header_indices


def collect_header_texts(hierarchy_data: dict) -> set:
    """
    Collect all header text strings from the hierarchy.

    Used to filter out entries whose text exactly matches a section header.

    Args:
        hierarchy_data: Stage 1b hierarchy mapping data

    Returns:
        Set of header text strings (normalized)
    """
    header_texts = set()

    def walk_hierarchy(items):
        for item in items:
            if "text" in item:
                # Normalize: lowercase, strip whitespace
                header_texts.add(item["text"].strip().lower())
            if "children" in item:
                walk_hierarchy(item["children"])

    if "hierarchy_with_indices" in hierarchy_data:
        walk_hierarchy(hierarchy_data["hierarchy_with_indices"])

    return header_texts


def remove_subset_delimiters(delimiters: list) -> list:
    """
    Remove delimiters that are subsets of larger delimiters.

    When the LLM returns both a composite entry (e.g., 19-25) AND its sub-parts
    (e.g., 19-19, 20-20), we keep only the largest non-overlapping entries.

    Args:
        delimiters: List of delimiter dicts with element_idx_start/end

    Returns:
        Filtered list with subset entries removed
    """
    if not delimiters:
        return delimiters

    # Sort by start index, then by span size (largest first)
    sorted_delims = sorted(
        delimiters,
        key=lambda d: (d["element_idx_start"], -(d["element_idx_end"] - d["element_idx_start"]))
    )

    kept = []
    for delim in sorted_delims:
        start = delim["element_idx_start"]
        end = delim["element_idx_end"]

        # Check if this delimiter is a subset of any already-kept delimiter
        is_subset = False
        for kept_delim in kept:
            kept_start = kept_delim["element_idx_start"]
            kept_end = kept_delim["element_idx_end"]

            # Check if current is fully contained within kept
            if start >= kept_start and end <= kept_end:
                # It's a subset (or exact duplicate) - skip it
                is_subset = True
                break

        if not is_subset:
            kept.append(delim)

    return kept


def extract_text_from_delimiter(doc: Document, delimiter: dict) -> str:
    """
    Extract text from Word document using delimiter indices.

    Uses tab-separated format for cleaner output (instead of preserving Word formatting).

    Args:
        doc: Word document
        delimiter: Delimiter dict with element_idx_start, element_idx_end, element_type

    Returns:
        Extracted text in tab-separated format
    """
    start_idx = delimiter["element_idx_start"]
    end_idx = delimiter["element_idx_end"]
    element_type = delimiter.get("element_type", "paragraph")

    if element_type == "paragraph" or element_type in ["table", "table_row"]:
        # Extract paragraphs (tables are also stored as paragraphs in Word)
        # Use tab separation for cleaner output
        text_parts = []
        for i in range(start_idx, min(end_idx + 1, len(doc.paragraphs))):
            text = doc.paragraphs[i].text.strip()
            if text:
                # Normalize internal whitespace but preserve tabs
                # This handles cases like "Year\t\tDiscipline\t\tInstitution"
                text_parts.append(text)

        # Join multi-paragraph entries with tab separator for compact format
        return "\t".join(text_parts)

    else:
        return f"[Unknown element type '{element_type}' at {start_idx}-{end_idx}]"


def is_header_entry(text: str, start_idx: int, header_indices: set, header_texts: set) -> tuple:
    """
    Check if an entry is actually a section header, not real content.

    Detects headers by:
    1. Entry starts at a known header index
    2. Entry text exactly matches a header text

    Also detects merged header+content entries where the header text appears
    at the start of the entry.

    Args:
        text: Extracted entry text
        start_idx: Element index where entry starts
        header_indices: Set of element indices that are headers
        header_texts: Set of normalized header text strings

    Returns:
        Tuple of (is_header_only, cleaned_text):
        - is_header_only: True if this entry is ONLY a header (should be skipped)
        - cleaned_text: Text with header prefix removed (if merged), or original text
    """
    # Normalize the text for comparison
    normalized_text = text.strip().lower()

    # Check if entry starts at a header index
    if start_idx in header_indices:
        # Check for exact match
        if normalized_text in header_texts:
            return True, text

        # Check if text starts with a header followed by newline (merged header+content)
        for header in header_texts:
            if normalized_text.startswith(header + "\n"):
                # Extract the content after the header
                # Find the position after the header in original text (case-insensitive)
                header_len = len(header)
                # Skip past the header and any following newlines/whitespace
                content_start = header_len
                original_lower = text.lower()
                while content_start < len(text) and text[content_start] in '\n\r\t ':
                    content_start += 1
                cleaned = text[content_start:].strip()
                if cleaned:
                    return False, cleaned
                else:
                    return True, text

    return False, text


def run_stage_2b(docx_path: str, delimiters_json_path: str = None):
    """
    Main Stage 2b: Extract text snippets using delimiters (no LLM)

    Args:
        docx_path: Path to Word document
        delimiters_json_path: Optional path to Stage 2a delimiters JSON
    """

    print("="*80)
    print("STAGE 2B: SNIPPET EXTRACTION (NO LLM)")
    print("="*80)
    print(f"Input: {docx_path}")
    print()

    # Setup
    om = OutputManager(docx_path)

    # Load delimiters
    if delimiters_json_path is None:
        delimiters_json_path = om.get_stage2a_path()

    print(f"Loading delimiters: {delimiters_json_path}")
    with open(delimiters_json_path) as f:
        delimiters_data = json.load(f)

    # Load hierarchy data to identify headers
    hierarchy_path = om.get_stage1b_path()
    header_indices = set()
    header_texts = set()
    if Path(hierarchy_path).exists():
        with open(hierarchy_path) as f:
            hierarchy_data = json.load(f)
        header_indices = collect_header_indices(hierarchy_data)
        header_texts = collect_header_texts(hierarchy_data)
        print(f"Loaded {len(header_indices)} header indices for filtering")

    # Load Word document
    doc = Document(docx_path)

    # Extract snippets
    all_entries = []
    total_extracted = 0
    headers_filtered = 0
    headers_cleaned = 0

    for section in delimiters_data.get("delimiters", []):
        hierarchy = section.get("hierarchy", [])
        section_name = " > ".join(hierarchy)

        print(f"Extracting: {section_name}")

        # Remove subset/duplicate delimiters before extraction
        raw_delimiters = section.get("entry_delimiters", [])
        deduped_delimiters = remove_subset_delimiters(raw_delimiters)

        if len(deduped_delimiters) < len(raw_delimiters):
            print(f"  (Removed {len(raw_delimiters) - len(deduped_delimiters)} subset entries)")

        section_entries = 0
        for delimiter in deduped_delimiters:
            # Extract text
            text = extract_text_from_delimiter(doc, delimiter)
            start_idx = delimiter["element_idx_start"]
            end_idx = delimiter["element_idx_end"]

            # Check for header entries (single element entries at header indices)
            if start_idx == end_idx:
                is_header_only, cleaned_text = is_header_entry(text, start_idx, header_indices, header_texts)
                if is_header_only:
                    headers_filtered += 1
                    continue
                if cleaned_text != text:
                    # Header was stripped from merged entry
                    text = cleaned_text
                    headers_cleaned += 1

            entry = {
                "hierarchy": hierarchy,
                "element_idx_start": start_idx,
                "element_idx_end": end_idx,
                "element_type": delimiter.get("element_type", "paragraph"),
                "confidence": delimiter.get("confidence", 1.0),
                "text": text
            }

            all_entries.append(entry)
            total_extracted += 1
            section_entries += 1

        print(f"  ✓ Extracted {section_entries} entries")

    filter_msgs = []
    if headers_filtered > 0:
        filter_msgs.append(f"{headers_filtered} header-only entries filtered")
    if headers_cleaned > 0:
        filter_msgs.append(f"{headers_cleaned} merged header prefixes cleaned")
    if filter_msgs:
        print(f"\n  ({', '.join(filter_msgs)})")

    # Save output
    output_path = om.get_stage2b_path()

    output_data = {
        "document_uid": delimiters_data.get("document_uid"),
        "total_entries": total_extracted,
        "entries": all_entries
    }

    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    print()
    print("="*80)
    print("STAGE 2B COMPLETE")
    print("="*80)
    print(f"Output: {output_path}")
    print(f"Total entries extracted: {total_extracted}")
    print("="*80)

    return output_data, output_path


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python stage_2b_snippet_extractor.py <docx_path> [delimiters_json]")
        print()
        print("Example:")
        print("  python stage_2b_snippet_extractor.py data/sample_cvs/word/2071_Zuschlag_Cv.docx")
        sys.exit(1)

    docx_path = sys.argv[1]
    delimiters_json = sys.argv[2] if len(sys.argv) > 2 else None

    run_stage_2b(docx_path, delimiters_json)
