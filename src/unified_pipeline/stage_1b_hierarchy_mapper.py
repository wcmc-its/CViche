#!/usr/bin/env python3
"""
Stage 1b: Map Hierarchy to Element Indices (Python only, no LLM)

Takes the hierarchy from Stage 1 and finds the exact element index for each
header in the Word document.

IMPORTANT: Uses the structure extractor's element indices, which include both
paragraphs AND tables. This ensures consistency with Stage 2 and later stages.

Uses sequence-based matching since headers may not be unique.
Falls back to string search if needed.

Input: Stage 1 hierarchy JSON + Word document
Output: Hierarchy with element indices (the "fenceposts" for sections)
"""

import sys
import json
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple

# Add to path
sys.path.insert(0, str(Path(__file__).parent))

from core.output_manager import OutputManager
from core.docx_structure_extractor import extract_docx_structure, extract_unified_elements


def normalize_text(text: str) -> str:
    """Normalize text for matching (lowercase, strip whitespace, punctuation, etc.)"""
    import re
    text = re.sub(r'[:\\.,-]', '', text)
    return " ".join(text.lower().strip().split())


def is_header_match(expected_header: str, para_text: str, strict: bool = False) -> bool:
    """
    Check if the paragraph text matches the expected header.

    This uses word-boundary matching to avoid false positives like
    "representations" matching "presentations".

    Args:
        expected_header: Normalized header text to look for
        para_text: Normalized paragraph text
        strict: If True, only match if paragraph is very short (for sequence matching)
                If False, allow somewhat longer paragraphs (for single header fallback)

    Returns:
        True if this is a valid header match
    """
    import re

    # Exact match - always accept
    if para_text == expected_header:
        return True

    # The paragraph is contained in the expected header (header might be longer)
    # e.g., para_text="education" matches expected_header="education and training"
    # Only accept if paragraph is very short (< 2x header length)
    if para_text in expected_header:
        if len(para_text) <= len(expected_header) * 2:
            return True

    # The expected header appears as a complete word/phrase in the paragraph
    # Use word boundary matching to avoid "representations" matching "presentations"
    pattern = r'\b' + re.escape(expected_header) + r'\b'
    if re.search(pattern, para_text):
        # In strict mode (sequence matching), only accept very short paragraphs
        # that are likely actual headers, not content paragraphs
        if strict:
            # Header should be short: no more than 3x the expected header length
            # This prevents "ASHA's continuing education program" from matching "education"
            if len(para_text) <= len(expected_header) * 3:
                return True
        else:
            # In non-strict mode (single header fallback), allow longer paragraphs
            # but still require them to be reasonably short
            if len(para_text) < 150:
                return True

    return False


def find_header_in_sequence(
    elements: List[Dict],
    hierarchy_sequence: List[str],
    start_idx: int = 0
) -> Optional[List[Tuple[str, int]]]:
    """
    Find a sequence of headers in the document elements starting from start_idx.

    Args:
        elements: List of document elements from structure extractor
        hierarchy_sequence: List of header texts in order
        start_idx: Where to start searching

    Returns:
        List of (header_text, element_idx) tuples, or None if not found
    """
    if not hierarchy_sequence:
        return []

    # Normalize the sequence
    normalized_sequence = [normalize_text(h) for h in hierarchy_sequence]

    # Try to find the sequence
    matches = []
    current_search_idx = start_idx

    for expected_header in normalized_sequence:
        found = False

        # Search forward from current position
        for i in range(current_search_idx, len(elements)):
            elem = elements[i]
            elem_text = elem.get('text', '')

            # Search in paragraphs AND table_header elements (from extract_unified_elements)
            if elem.get('type') not in ('paragraph', 'table_header') or not elem_text:
                continue

            para_text = normalize_text(elem_text)

            # Skip empty paragraphs
            if not para_text:
                continue

            # Check if this paragraph matches the header (using strict word boundary matching)
            # Strict mode prevents matching content paragraphs like "continuing education program"
            if is_header_match(expected_header, para_text, strict=True):
                # Use unified_idx if available (from extract_unified_elements)
                elem_idx = elem.get('unified_idx', elem.get('idx', i))
                matches.append((hierarchy_sequence[len(matches)], elem_idx))
                current_search_idx = i + 1
                found = True
                break

        if not found:
            # Sequence broken - return None
            return None

    return matches


def map_hierarchy_node(
    node: Dict,
    elements: List[Dict],
    parent_path: List[str] = None,
    start_search_idx: int = 0
) -> Tuple[Dict, int]:
    """
    Map a single hierarchy node and its children to element indices.

    Args:
        node: Hierarchy node from Stage 1
        elements: List of document elements from structure extractor
        parent_path: Path of parent headers for context
        start_search_idx: Where to start searching in document

    Returns:
        Tuple of (mapped_node, next_search_idx)
    """
    if parent_path is None:
        parent_path = []

    node_text = node.get("text", "").strip()
    current_path = parent_path + ([node_text] if node_text else [])

    # Check if this is a synthetic header (added by LLM, not in original document)
    is_synthetic = (
        node.get("text_metadata", {}).get("synthetic", False) or
        node.get("paragraph_index") == -1
    )

    # Try to find this node's header in the document
    element_idx = None

    if node_text and not is_synthetic:
        # Try sequence-based matching with parent context
        search_sequence = current_path[-min(3, len(current_path)):]  # Use last 3 headers for context

        matches = find_header_in_sequence(elements, search_sequence, start_search_idx)

        if matches:
            # Found the sequence - use the last match (this node)
            element_idx = matches[-1][1]
        else:
            # Fallback: Simple text search using word-boundary matching
            # First try searching forward from start_search_idx
            # If not found, try searching from the beginning (handles out-of-order hierarchies)
            normalized_target = normalize_text(node_text)

            def search_for_header(search_start: int, search_end: int) -> Optional[int]:
                """Search for header in a range of elements."""
                for i in range(search_start, search_end):
                    elem = elements[i]
                    elem_text = elem.get('text', '')

                    # Search in paragraphs AND table_header elements
                    if elem.get('type') not in ('paragraph', 'table_header') or not elem_text:
                        continue

                    para_text = normalize_text(elem_text)

                    # Use strict word-boundary matching
                    # strict=True ensures we don't match content paragraphs like
                    # "Advanced Health Education Mammography Center" when looking for "Education"
                    if is_header_match(normalized_target, para_text, strict=True):
                        # Return unified_idx if available
                        return elem.get('unified_idx', elem.get('idx', i))
                return None

            # Try forward search first
            element_idx = search_for_header(start_search_idx, len(elements))

            # If not found and we didn't start from the beginning,
            # try searching from the beginning (Stage 1a hierarchy may not match document order)
            if element_idx is None and start_search_idx > 0:
                element_idx = search_for_header(0, start_search_idx)

    # Build mapped node
    mapped_node = {
        "text": node_text,
        "level": node.get("level", ""),
        "element_idx": element_idx,
        "synthetic": is_synthetic
    }

    # Track where to search next
    next_search_idx = element_idx + 1 if element_idx is not None else start_search_idx

    # Process children
    if "children" in node and node["children"]:
        mapped_children = []

        # For synthetic headers, children should search from the original start position
        # because the LLM may have grouped items out of document order
        child_search_start = start_search_idx if is_synthetic else next_search_idx

        for child in node["children"]:
            mapped_child, child_next_idx = map_hierarchy_node(
                child,
                elements,
                current_path,
                child_search_start
            )
            mapped_children.append(mapped_child)

            # For synthetic parents, each child searches from the same start
            # For real parents, children search sequentially
            if not is_synthetic:
                child_search_start = child_next_idx

        # Update next_search_idx to be after all children
        child_indices = [c.get("element_idx") for c in mapped_children if c.get("element_idx") is not None]
        if child_indices:
            next_search_idx = max(max(child_indices) + 1, next_search_idx)

        mapped_node["children"] = mapped_children

    return mapped_node, next_search_idx


def get_first_child_element_idx(children: List[Dict]) -> Optional[int]:
    """
    Recursively find the first element_idx in a list of children.

    This handles cases where a synthetic parent (element_idx=null) has children
    with actual element indices. Returns the minimum element_idx found.

    Args:
        children: List of child nodes

    Returns:
        The first (minimum) element_idx found, or None if none exist
    """
    indices = []
    for child in children:
        if child.get("element_idx") is not None:
            indices.append(child["element_idx"])
        # Also check grandchildren
        if "children" in child and child["children"]:
            grandchild_idx = get_first_child_element_idx(child["children"])
            if grandchild_idx is not None:
                indices.append(grandchild_idx)

    return min(indices) if indices else None


def has_mapped_children(node: Dict) -> bool:
    """
    Check if a hierarchy node has any children with actual element indices.

    A node should only be marked as has_children=True for entry extraction purposes
    if at least one child has an element_idx mapped. Otherwise, the parent section
    should be treated as a leaf for entry extraction.

    Args:
        node: Hierarchy node with potential children

    Returns:
        True if any descendant has an element_idx assigned
    """
    children = node.get("children", [])
    if not children:
        return False

    for child in children:
        if child.get("element_idx") is not None:
            return True
        # Recursively check grandchildren
        if has_mapped_children(child):
            return True

    return False


def compute_section_boundaries(mapped_hierarchy: List[Dict], doc_length: int) -> List[Dict]:
    """
    Compute start/end element indices for each section.

    Algorithm:
    1. A section starts at its header's element_idx
    2. A section ends just before the next section at the SAME level starts
       (sibling sections are disjoint and sequential)
    3. Parent sections contain all their children
       (parent end >= last child end)
    4. PREAMBLE HANDLING: If there's content before the first section (indices 0 to first_section_start-1),
       create a synthetic "Personal Data" section to capture it. If "Personal Data" already exists,
       extend its range to include the preamble.

    Args:
        mapped_hierarchy: Hierarchy with element_idx for each node
        doc_length: Total number of elements in document

    Returns:
        Flattened list of sections with boundaries
    """
    sections = []

    def compute_bounds(
        nodes: List[Dict],
        parent_path: List[str] = None,
        default_end: int = None
    ):
        """
        Compute boundaries for nodes at the same level.
        Returns the maximum end index seen (for parent to use).
        """
        if parent_path is None:
            parent_path = []
        if default_end is None:
            default_end = doc_length - 1

        max_end_seen = 0

        for i, node in enumerate(nodes):
            node_text = node.get("text", "")
            current_path = parent_path + ([node_text] if node_text else [])
            element_idx = node.get("element_idx")

            if element_idx is not None:
                # Find this section's end:
                # - If there's a next sibling with an index, end just before it
                # - Otherwise, use default_end (from parent or doc end)

                end_idx = default_end

                # Look for next sibling
                for j in range(i + 1, len(nodes)):
                    next_sibling_idx = nodes[j].get("element_idx")
                    if next_sibling_idx is not None:
                        end_idx = next_sibling_idx - 1
                        break
                    # If sibling has no element_idx but has children, use first child's index
                    elif "children" in nodes[j] and nodes[j]["children"]:
                        first_child_idx = get_first_child_element_idx(nodes[j]["children"])
                        if first_child_idx is not None:
                            end_idx = first_child_idx - 1
                            break

                # If this node has children, compute their bounds first
                # and extend this section's end to include them
                if "children" in node and node["children"]:
                    children_max_end = compute_bounds(
                        node["children"],
                        current_path,
                        default_end=end_idx
                    )
                    # Parent must contain all children
                    end_idx = max(end_idx, children_max_end)

                # Ensure end_idx >= element_idx (can happen with out-of-order hierarchies)
                # If next sibling appears before this section in document, use default_end instead
                if end_idx < element_idx:
                    end_idx = default_end

                section = {
                    "hierarchy": current_path,
                    "element_idx_start": element_idx,
                    "element_idx_end": end_idx,  # Inclusive
                    "level": node.get("level", ""),
                    # Only mark as has_children if children actually have mapped element indices
                    # This ensures sections like "Publications" get processed even if their
                    # hierarchy children (like "Articles") weren't found in the document
                    "has_children": has_mapped_children(node)
                }
                sections.append(section)

                max_end_seen = max(max_end_seen, end_idx)

            else:
                # No element_idx but has children - process them
                # For synthetic nodes, we need to compute the proper end boundary
                # by looking at the next sibling (same as we do for real nodes)
                if "children" in node and node["children"]:
                    # Calculate bounded_end for children the same way as for real nodes
                    bounded_end = default_end
                    for j in range(i + 1, len(nodes)):
                        next_sibling_idx = nodes[j].get("element_idx")
                        if next_sibling_idx is not None:
                            bounded_end = next_sibling_idx - 1
                            break
                        elif "children" in nodes[j] and nodes[j]["children"]:
                            first_child_idx = get_first_child_element_idx(nodes[j]["children"])
                            if first_child_idx is not None:
                                bounded_end = first_child_idx - 1
                                break

                    children_max_end = compute_bounds(
                        node["children"],
                        current_path,
                        default_end=bounded_end  # Use bounded end, not raw default_end
                    )
                    max_end_seen = max(max_end_seen, children_max_end)

        return max_end_seen

    compute_bounds(mapped_hierarchy)

    # POST-PROCESS: Fix invalid boundaries caused by out-of-order hierarchies
    # For each section, if end < start, find the actual next section in document order
    # and set end to just before it
    if sections:
        # Sort sections by start index to find actual document order
        sections_by_start = sorted(
            [(i, s["element_idx_start"]) for i, s in enumerate(sections)],
            key=lambda x: x[1]
        )

        for i, section in enumerate(sections):
            if section["element_idx_end"] < section["element_idx_start"]:
                # Find the next section in document order (by start index)
                current_start = section["element_idx_start"]

                # Find all sections that start after this one
                next_starts = [
                    s["element_idx_start"]
                    for s in sections
                    if s["element_idx_start"] > current_start
                ]

                if next_starts:
                    # End just before the next section starts
                    section["element_idx_end"] = min(next_starts) - 1
                else:
                    # No next section - extend to document end
                    section["element_idx_end"] = doc_length - 1

    # PREAMBLE HANDLING: Check for unmapped content at the beginning of the document
    if sections:
        # Find the earliest element_idx_start among all sections
        first_section_start = min(s["element_idx_start"] for s in sections)

        if first_section_start > 0:
            # There's content before the first section (preamble)
            preamble_end = first_section_start - 1

            # Check if "Personal Data" section already exists
            personal_data_sections = [
                s for s in sections
                if s["hierarchy"] and s["hierarchy"][0].lower().strip() in [
                    "personal data", "personal information", "contact information",
                    "contact", "profile", "header"
                ]
            ]

            if personal_data_sections:
                # Extend existing Personal Data section to include preamble
                # Find the one with the earliest start
                pd_section = min(personal_data_sections, key=lambda s: s["element_idx_start"])
                if pd_section["element_idx_start"] > 0:
                    # Extend backwards to include preamble
                    pd_section["element_idx_start"] = 0
                    # Update parent section if exists
                    for s in sections:
                        if s["has_children"] and pd_section["hierarchy"][0] in s["hierarchy"]:
                            s["element_idx_start"] = min(s["element_idx_start"], 0)
            else:
                # Create synthetic "Personal Data" section for preamble
                preamble_section = {
                    "hierarchy": ["Personal Data"],
                    "element_idx_start": 0,
                    "element_idx_end": preamble_end,
                    "level": "H1",
                    "has_children": False,
                    "synthetic": True  # Flag to indicate this was auto-generated
                }
                # Insert at the beginning of sections list
                sections.insert(0, preamble_section)
    elif doc_length > 0:
        # No sections found at all, but document has content
        # Create a single "Personal Data" section for entire document
        preamble_section = {
            "hierarchy": ["Personal Data"],
            "element_idx_start": 0,
            "element_idx_end": doc_length - 1,
            "level": "H1",
            "has_children": False,
            "synthetic": True
        }
        sections.append(preamble_section)

    return sections


def run_stage_1b(docx_path: str, hierarchy_json_path: str = None):
    """
    Main Stage 1b: Map hierarchy headers to element indices

    Args:
        docx_path: Path to Word document
        hierarchy_json_path: Optional path to Stage 1 hierarchy JSON
    """

    print("="*80)
    print("STAGE 1B: HIERARCHY TO ELEMENT INDEX MAPPING (NO LLM)")
    print("="*80)
    print(f"Input: {docx_path}")
    print()

    # Setup
    om = OutputManager(docx_path)

    # Load hierarchy from Stage 1
    if hierarchy_json_path is None:
        # Try to find Stage 1 output
        # First check if we have the signature_segmented file
        stage1_candidates = [
            Path("data/sample_cvs/word") / f"{om.file_handle}_signature_segmented.json",
            om.get_stage1_json_path()
        ]

        hierarchy_json_path = None
        for candidate in stage1_candidates:
            if candidate.exists():
                hierarchy_json_path = candidate
                break

        if hierarchy_json_path is None:
            print(f"Error: Could not find Stage 1 hierarchy JSON")
            sys.exit(1)

    print(f"Loading hierarchy: {hierarchy_json_path}")
    with open(hierarchy_json_path) as f:
        hierarchy_data = json.load(f)

    # Extract document structure using the unified element extractor
    # This gives us elements (paragraphs + table_header + table_content) with consistent indices
    # Table headers are split out so they can be matched like paragraphs
    print(f"Extracting document structure (unified)...")
    structure = extract_unified_elements(docx_path)
    elements = structure['elements']
    doc_length = len(elements)

    print(f"Document has {doc_length} elements:")
    print(f"  - Paragraphs: {structure['meta']['num_paragraphs']}")
    print(f"  - Tables: {structure['meta']['num_tables']}")
    print(f"  - Table headers: {structure['meta']['num_table_headers']}")
    print(f"  - Empty: {structure['meta']['num_empty']}")
    print()

    # Map hierarchy to element indices
    print("Mapping headers to element indices...")
    mapped_hierarchy = []
    next_search_idx = 0

    for node in hierarchy_data.get("hierarchy", []):
        mapped_node, next_search_idx = map_hierarchy_node(node, elements, [], next_search_idx)
        mapped_hierarchy.append(mapped_node)

    print(f"✓ Mapped {len(mapped_hierarchy)} top-level sections")
    print()

    # Compute section boundaries
    print("Computing section boundaries...")
    sections = compute_section_boundaries(mapped_hierarchy, doc_length)
    print(f"✓ Computed boundaries for {len(sections)} sections")
    print()

    # Validate coverage - ensure all indices are mapped
    covered_indices = set()
    for section in sections:
        for idx in range(section["element_idx_start"], section["element_idx_end"] + 1):
            covered_indices.add(idx)

    all_indices = set(range(doc_length))
    unmapped_indices = all_indices - covered_indices
    coverage_percentage = (len(covered_indices) / doc_length * 100) if doc_length > 0 else 100.0

    # Check for synthetic sections
    synthetic_sections = [s for s in sections if s.get("synthetic", False)]

    print("Coverage validation:")
    print(f"  - Document indices: 0 to {doc_length - 1}")
    print(f"  - Covered indices: {len(covered_indices)}/{doc_length} ({coverage_percentage:.1f}%)")
    if unmapped_indices:
        print(f"  - ⚠ Unmapped indices: {sorted(unmapped_indices)[:10]}{'...' if len(unmapped_indices) > 10 else ''}")
    else:
        print(f"  - ✓ Full coverage achieved")
    if synthetic_sections:
        print(f"  - Synthetic sections added: {len(synthetic_sections)}")
        for s in synthetic_sections:
            print(f"    → '{s['hierarchy'][0]}' (indices {s['element_idx_start']}-{s['element_idx_end']})")
    print()

    # Save output
    output_path = om.get_stage1b_path()

    output_data = {
        "document_uid": hierarchy_data.get("document_uid"),
        "document_length": doc_length,
        "hierarchy_with_indices": mapped_hierarchy,
        "section_boundaries": sections,
        "meta": {
            "total_sections": len(sections),
            "leaf_sections": len([s for s in sections if not s["has_children"]]),
            "coverage": {
                "covered_indices": len(covered_indices),
                "total_indices": doc_length,
                "percentage": coverage_percentage,
                "unmapped_count": len(unmapped_indices),
                "synthetic_sections_added": len(synthetic_sections)
            }
        }
    }

    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    # Print sample sections
    print("="*80)
    print("SAMPLE SECTION BOUNDARIES (first 5)")
    print("="*80)
    for section in sections[:5]:
        hierarchy_str = " > ".join(section["hierarchy"])
        print(f"\n[{section['level']}] {hierarchy_str}")
        print(f"  Elements: {section['element_idx_start']} to {section['element_idx_end']}")
        print(f"  Leaf: {not section['has_children']}")

    print()
    print("="*80)
    print("STAGE 1B COMPLETE")
    print("="*80)
    print(f"Output: {output_path}")
    print(f"Total sections: {len(sections)}")
    print(f"Leaf sections (for Stage 2a): {output_data['meta']['leaf_sections']}")
    print(f"Coverage: {coverage_percentage:.1f}% ({len(covered_indices)}/{doc_length} indices)")
    if synthetic_sections:
        print(f"Synthetic sections: {len(synthetic_sections)} (preamble auto-mapped to Personal Data)")
    print("="*80)

    return output_data, output_path


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python stage_1b_hierarchy_mapper.py <docx_path> [hierarchy_json]")
        print()
        print("Example:")
        print("  python stage_1b_hierarchy_mapper.py data/sample_cvs/word/2071_Zuschlag_Cv.docx")
        sys.exit(1)

    docx_path = sys.argv[1]
    hierarchy_json = sys.argv[2] if len(sys.argv) > 2 else None

    run_stage_1b(docx_path, hierarchy_json)
