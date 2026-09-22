#!/usr/bin/env python3
"""
Stage 2: Entry Extraction (Combined Delimiter Detection + Text Extraction)

Uses LLM to identify individual entries within each CV section and extracts their full text.
This stage combines the former Stage 2a (delimiter detection) and Stage 2b (text extraction).

Input: Stage 1b hierarchy JSON + Word document
Output: JSON with extracted entries including full text and hierarchy context
"""

import os
import sys
import json
import logging
import time
from functools import partial
from pathlib import Path
from collections.abc import Callable
from typing import NamedTuple
from docx import Document

# Add to path
sys.path.insert(0, str(Path(__file__).parent))

from unified_pipeline.llm_client import call_llm
from unified_pipeline.core.batch_pool import make_batches, map_in_order, workers_from_config
from core.output_manager import OutputManager
from core.docx_structure_extractor import extract_docx_structure, extract_unified_elements
from core.template_boilerplate import is_template_instruction

logger = logging.getLogger(__name__)


def get_hierarchy_path(node: dict, current_path: list[str] = None) -> list[str]:
    """Build full hierarchy path for a node"""
    if current_path is None:
        current_path = []

    text = node.get("text", "").strip()
    if text:
        current_path.append(text)

    return current_path


def build_element_index_map(doc_structure: dict) -> dict[int, dict]:
    """
    Build a map from element index to element data from extract_unified_elements output.

    The unified document structure uses unified_idx for all elements:
    - Paragraphs have unified_idx (integer)
    - Table headers have unified_idx (integer)
    - Table content has unified_idx (integer)
    - Empty elements have unified_idx (integer)

    This function creates a map where all elements can be looked up by unified_idx.

    Returns:
        Dict mapping unified_idx (int) -> element dict
    """
    element_map = {}

    for elem in doc_structure.get("elements", []):
        # Use unified_idx if available (from extract_unified_elements)
        # Fall back to idx for backward compatibility
        idx = elem.get("unified_idx", elem.get("idx"))

        if isinstance(idx, int):
            element_map[idx] = elem
        elif isinstance(idx, str) and idx.startswith("table_"):
            # Legacy table index format - store with string key
            element_map[idx] = elem

    return element_map


def get_element_text(element: dict) -> str:
    """
    Extract text from an element (paragraph, table_header, table_content, table, or empty).

    For table content, uses the pre-flattened text or concatenates cell text.
    """
    elem_type = element.get("type", "")

    if elem_type in ("paragraph", "empty", "table_header"):
        return element.get("text", "").strip()
    elif elem_type == "table_content":
        # Table content already has flattened text
        return element.get("text", "").strip()
    elif elem_type == "table":
        # Legacy table format - flatten data into text
        rows = element.get("data", [])
        row_texts = []
        for row in rows:
            cell_texts = [cell.get("text", "") for cell in row]
            row_texts.append("\t".join(cell_texts))
        return "\n".join(row_texts)
    else:
        return element.get("text", "").strip()


def _element_text_or_fallback(idx: int, element_index_map: dict, doc: Document) -> str:
    """Text for a unified element index, falling back to doc.paragraphs.

    element_index_map should hold every int index in [0, doc_length) by
    construction (build_element_index_map maps every element with an int
    unified_idx). The fallback exists for when it doesn't: measured on the
    111-uid local corpus, 2 uids had a stage-1b hierarchy whose header
    indices (260, 262) fell past a fresh extraction's doc_length (260) --
    a stale-hierarchy-vs-current-extraction mismatch, not a code bug this
    PR should paper over. Logs so a real gap surfaces instead of silently
    producing a blank header/break entry.
    """
    if idx in element_index_map:
        return get_element_text(element_index_map[idx])
    logger.warning(f"    Index {idx} missing from element_index_map; falling back to doc.paragraphs")
    return doc.paragraphs[idx].text.strip() if idx < len(doc.paragraphs) else ""


def split_merged_row_into_pseudo_rows(row: list) -> list:
    """
    Detect and split table rows where multiple entries were merged into one row.

    If all cells in a row contain the same number of \n\n-separated segments,
    this indicates the row should have been multiple rows. We split each cell
    and zip them together to create pseudo-rows.

    Args:
        row: List of cell dicts with 'text' keys

    Returns:
        List of pseudo-rows (each is a list of cell texts)
    """
    import re

    if not isinstance(row, list) or len(row) < 2:
        return None  # Can't detect pattern with single cell

    # Extract text from each cell and split on \n\n+ pattern
    cell_segments = []
    for cell in row:
        cell_text = cell.get("text", "") if isinstance(cell, dict) else str(cell)
        # Split on 2+ newlines (blank line separator)
        segments = re.split(r'\n\n+', cell_text)
        segments = [s.strip() for s in segments if s.strip()]
        cell_segments.append(segments)

    # Check if all cells have the same number of segments (> 1)
    segment_counts = [len(segs) for segs in cell_segments]
    if len(set(segment_counts)) != 1 or segment_counts[0] <= 1:
        return None  # Not a merged row pattern

    num_pseudo_rows = segment_counts[0]

    # Zip segments together to create pseudo-rows
    pseudo_rows = []
    for i in range(num_pseudo_rows):
        pseudo_row = [segs[i] if i < len(segs) else "" for segs in cell_segments]
        pseudo_rows.append(pseudo_row)

    return pseudo_rows


def extract_leaf_sections_with_boundaries(
    section_boundaries: list[dict],
    hierarchy: list[dict]
) -> list[tuple[list[str], int, int]]:
    """
    Extract all leaf sections with their boundaries from Stage 1b output.

    Also handles gaps between parent sections and their first child - these
    contain content that belongs to the parent section before any subsections.

    Args:
        section_boundaries: List of section boundary dicts from Stage 1b
        hierarchy: Hierarchy structure (for reference)

    Returns:
        List of (hierarchy_path, element_idx_start, element_idx_end) tuples
    """
    leaf_sections = []

    # First, collect all parent sections that have children
    # We need to process the gap between parent start and first child start
    parent_sections = {}
    for boundary in section_boundaries:
        if boundary.get("has_children", False):
            hierarchy_path = tuple(boundary.get("hierarchy", []))
            start_idx = boundary.get("element_idx_start")
            end_idx = boundary.get("element_idx_end")
            if start_idx is not None:
                parent_sections[hierarchy_path] = {
                    "start": start_idx,
                    "end": end_idx,
                    "first_child_start": None
                }

    # Find the first child start for each parent
    for boundary in section_boundaries:
        if not boundary.get("has_children", False):
            hierarchy_path = boundary.get("hierarchy", [])
            start_idx = boundary.get("element_idx_start")

            # Check if this is a child of any parent
            if len(hierarchy_path) > 1:
                parent_path = tuple(hierarchy_path[:-1])
                if parent_path in parent_sections:
                    current_first = parent_sections[parent_path]["first_child_start"]
                    if current_first is None or start_idx < current_first:
                        parent_sections[parent_path]["first_child_start"] = start_idx

    for boundary in section_boundaries:
        hierarchy_path = boundary.get("hierarchy", [])
        start_idx = boundary.get("element_idx_start")
        end_idx = boundary.get("element_idx_end")

        # Only include if we have valid boundaries
        if start_idx is None or end_idx is None:
            continue

        if not boundary.get("has_children", False):
            # Leaf section - include as-is
            leaf_sections.append((hierarchy_path, start_idx, end_idx))
        else:
            # Parent section with children - check for gap before first child
            parent_path = tuple(hierarchy_path)
            if parent_path in parent_sections:
                first_child_start = parent_sections[parent_path]["first_child_start"]
                if first_child_start is not None and first_child_start > start_idx + 1:
                    # There's a gap between parent header and first child
                    # This gap contains content that belongs to the parent section
                    # (e.g., journal articles before "Book" subsection in PUBLICATIONS)
                    gap_end = first_child_start - 1
                    print(f"  [DEBUG] Found gap in '{hierarchy_path[-1]}': elements {start_idx + 1} to {gap_end}")
                    leaf_sections.append((hierarchy_path, start_idx + 1, gap_end))

    return leaf_sections


def collect_header_indices(hierarchy_with_indices: list[dict]) -> set:
    """
    Recursively collect all element indices that are section/subsection headers.

    Args:
        hierarchy_with_indices: Hierarchy structure from Stage 1b with element_idx

    Returns:
        Set of element indices that are headers
    """
    header_indices = set()

    def walk_hierarchy(items):
        for item in items:
            if "element_idx" in item and item["element_idx"] is not None:
                header_indices.add(item["element_idx"])
            if "children" in item:
                walk_hierarchy(item["children"])

    walk_hierarchy(hierarchy_with_indices)
    return header_indices


def collect_header_info(hierarchy_with_indices: list[dict]) -> dict[int, list[str]]:
    """
    Collect header indices mapped to their hierarchy paths.

    Args:
        hierarchy_with_indices: Hierarchy structure from Stage 1b with element_idx

    Returns:
        Dict mapping element_idx -> hierarchy path (list of strings)
    """
    header_info = {}

    def walk_hierarchy(items, parent_path=None):
        if parent_path is None:
            parent_path = []

        for item in items:
            text = item.get("text", "").strip()
            current_path = parent_path + [text] if text else parent_path

            if "element_idx" in item and item["element_idx"] is not None:
                header_info[item["element_idx"]] = current_path

            if "children" in item:
                walk_hierarchy(item["children"], current_path)

    walk_hierarchy(hierarchy_with_indices)
    return header_info


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

    def normalize_idx(idx):
        """Convert index to sortable tuple (main_idx, sub_idx)."""
        if isinstance(idx, str):
            if "." in idx:
                parts = idx.split(".", 1)
                return (float(parts[0]), float(parts[1]))
            elif idx.startswith("table_"):
                return (1000000 + int(idx.split("_")[1]), 0)
            else:
                return (float(idx), 0)
        return (float(idx), 0)

    def is_bare(idx):
        """True when idx carries no ".row" sub-index (i.e. a whole element)."""
        return not (isinstance(idx, str) and "." in idx)

    def normalize_span(d: dict) -> tuple[tuple[float, float], tuple[float, float]]:
        """Return (start, end) sort keys for a delimiter.

        A mixed delimiter -- a sub-indexed start ("9.1") paired with a bare
        int end (9) -- normalizes its end to (9, 0), which sorts BELOW the
        start's own (9, 1). The delimiter's own "end" then reads as earlier
        than its "start", so it tests as fully contained inside an unrelated
        sibling row and is silently dropped (#854). The LLM does return this
        shape (start carries the row sub-index the model resolved, end does
        not), so this is not a defensive case -- it is an observed one.

        When the end is bare but the start is sub-indexed and the raw
        (uncorrected) end would sort below the start, treat the end as equal
        to the start's own key instead. This never touches a delimiter whose
        end is itself sub-indexed (the true-subset case is unaffected), and
        never touches a delimiter that is bare on both ends (ordinary int
        spans are unaffected). The delimiter's own element_idx_end field is
        left untouched -- only the sort/containment key changes.
        """
        start = normalize_idx(d["element_idx_start"])
        end = normalize_idx(d["element_idx_end"])
        if (
            is_bare(d["element_idx_end"])
            and not is_bare(d["element_idx_start"])
            and end < start
        ):
            end = start
        return start, end

    def span_size(d):
        """Calculate span size, handling string indices."""
        start, end = normalize_span(d)
        # For row entries (same parent), span is end[1] - start[1]
        # For regular entries, span is end[0] - start[0]
        if start[0] == end[0]:
            return end[1] - start[1]
        return end[0] - start[0]

    def content_lines(text):
        """Non-trivial content lines of an entry.

        Rows are newline-joined inside a whole-table blob but cells are joined
        with ' | ' or a tab inside a single row, so both separators have to be
        broken to compare a parent against its rows like with like. Lines under
        12 chars are dropped as headers/dates/noise.
        """
        out = set()
        for raw in str(text).replace("\t", "\n").split("\n"):
            s = " ".join(raw.split())
            if len(s) >= 12:
                out.add(s.lower())
        return out

    # A table split into sub-rows ("109.4") can ALSO come back from the LLM as a
    # bare whole-table span ("109..109"). normalize_idx maps that bare parent to
    # the POINT (109, 0), so its own rows -- (109, 4) and up -- never test as
    # contained below and BOTH survive: the table is emitted twice, and stage 6
    # then renders it twice (#418).
    #
    # Dropping the parent outright is NOT safe: measured over the S3 corpus, 44 of
    # 59 such parents carry at least one record that never became a sibling row,
    # so an unconditional drop silently loses content (e.g. a $2.5M grant on
    # MKEQKW, four records on C0ZGFW). Only drop a parent whose every content line
    # is already present in its own surviving rows -- a provable duplicate. A
    # parent with an uncovered remainder is left alone; it still double-renders,
    # which is the splitter gap tracked separately, but no content is lost here.
    #
    # ponytail: strict all-or-nothing coverage. The finer fix is to subtract the
    # covered rows and keep only the remainder, which needs text surgery on the
    # parent -- do that only if double-rendering proves worse than the risk.
    sub_row_parents = {}
    for d in delimiters:
        if not is_bare(d["element_idx_start"]):
            key = normalize_idx(d["element_idx_start"])[0]
            sub_row_parents.setdefault(key, []).append(d)

    if sub_row_parents:
        def is_redundant_table_parent(d):
            start, end = d["element_idx_start"], d["element_idx_end"]
            if not (is_bare(start) and is_bare(end)):
                return False
            main = normalize_idx(start)[0]
            if main != normalize_idx(end)[0] or main not in sub_row_parents:
                return False
            rows = sub_row_parents[main]
            haystack = " \n ".join(str(r.get("text", "")) for r in rows)
            haystack = " ".join(haystack.replace("\t", " ").split()).lower()
            lines = content_lines(d.get("text", ""))
            if not lines:
                # Every line of the parent's own text was under the 12-char
                # noise floor (e.g. "PI\n2020\nWCM"): all(... for x in <empty
                # set>) is vacuously True, which would read as "every content
                # line already proven present in the sibling rows" when in
                # fact nothing was checked at all. Not proven -> not
                # redundant; leave the parent in place (#855).
                return False
            return all(line in haystack for line in lines)

        delimiters = [d for d in delimiters if not is_redundant_table_parent(d)]

    # Sort by start index, then by span size (largest first)
    sorted_delims = sorted(
        delimiters,
        key=lambda d: (normalize_span(d)[0], -span_size(d))
    )

    kept = []
    for delim in sorted_delims:
        start, end = normalize_span(delim)

        # Check if this delimiter is a subset of any already-kept delimiter
        is_subset = False
        for kept_delim in kept:
            kept_start, kept_end = normalize_span(kept_delim)

            # Check if current is fully contained within kept
            if start >= kept_start and end <= kept_end:
                # It's a subset (or exact duplicate) - skip it
                is_subset = True
                break

        if not is_subset:
            kept.append(delim)

    return kept


def recover_unclaimed_table_rows(batch_elements: list, claimed_row_keys: set) -> list:
    """Return entries for table rows no delimiter claimed (#420).

    The model returns sub-row indices as JSON NUMBERS, so a row index with a
    trailing zero collapses: "114.10" parses to the float 114.1, ``str()``
    renders it back as "114.1", and the lookup lands on row 1 -- row 10 is
    unreachable. C0ZGFW element 114 lost rows 10/20/30/40/50 exactly this way.
    Rows the model simply omitted disappear identically. Either way the content
    survived only inside whatever whole-table entry the model happened to emit,
    which is what made those blobs load-bearing.

    Recovery is structural, so the entries are marked ``recovered_row`` and given
    a lower confidence than model-attested ones.
    """
    recovered = []
    for elem in batch_elements:
        if elem.get("type") != "table_row":
            continue
        key = str(elem.get("idx"))
        if key in claimed_row_keys:
            continue
        row_text = str(elem.get("full_text", elem.get("text", ""))).strip()
        # A row of empty cells flattens to separators only ("|", "| |"): not empty
        # by len(), but carrying nothing. Require at least one alphanumeric char.
        if not any(ch.isalnum() for ch in row_text):
            continue
        recovered.append({
            "element_idx_start": key,
            "element_idx_end": key,
            "element_type": "table_row",
            "confidence": 0.5,  # not model-attested; recovered structurally
            "text": row_text,
            "table_index": elem.get("table_index"),
            "row_index": elem.get("row_index"),
            "parent_idx": elem.get("parent_idx"),
            "recovered_row": True,
        })
    return recovered


def _dedup_idx_key(idx):
    """Normalize an element index to a comparable (main, sub) tuple.

    Handles the mixed representations stage 2 emits for the SAME element:
    int 30, float 30.0, str "30.0", sub-row "22.2", and "table_3".
    """
    if isinstance(idx, str):
        if idx.startswith("table_"):
            try:
                return (1000000 + int(idx.split("_")[1]), 0.0)
            except (ValueError, IndexError):
                return (str(idx), 0.0)
        if "." in idx:
            parts = idx.split(".", 1)
            try:
                return (float(parts[0]), float(parts[1]))
            except ValueError:
                return (str(idx), 0.0)
    try:
        return (float(idx), 0.0)
    except (TypeError, ValueError):
        return (str(idx), 0.0)


def filter_extraction_noise(entries: list[dict]) -> list[dict]:
    """Drop noise from the final entry list (#211): empty content entries and
    exact duplicates. Every survivor rides through the 3b/4/5 LLM stages, so
    noise here is paid for several times over downstream.

    - Empty text: content entries (paragraph/table/table_row) with no text
      carry nothing downstream. Structural ``header``/``break`` records are
      kept regardless of text (breaks are legitimately empty).
    - Duplicates: the same element emitted more than once — e.g. a table cell
      that spans two sub-section boundaries gets one copy per sub-section,
      with conflicting hierarchies, and each copy is then classified and
      field-extracted separately. Key = (element_type, normalized text,
      normalized element_idx_start); the first copy in document order wins.
      Same text at a DIFFERENT index is kept: repeated names/lines are real
      (e.g. the same mentee listed under two degree programs).
    """
    seen = set()
    kept = []
    dropped_empty = dropped_dup = 0
    for entry in entries:
        etype = entry.get("element_type", "")
        text = " ".join(str(entry.get("text", "")).split())
        if etype not in ("header", "break") and not text:
            dropped_empty += 1
            continue
        key = (etype, text.lower(), _dedup_idx_key(entry.get("element_idx_start")))
        if key in seen:
            dropped_dup += 1
            continue
        seen.add(key)
        kept.append(entry)
    if dropped_empty or dropped_dup:
        print(f"Filtered extraction noise: {dropped_empty} empty, {dropped_dup} duplicate entries")
    return kept


def detect_entries_for_section(
    section_hierarchy: list[str],
    doc_elements: list[dict],
    start_elem_idx: int,
    end_elem_idx: int,
    document_uid: str = None,
    header_indices: set = None,
    element_index_map: dict = None
) -> tuple[list[dict], dict]:
    """
    Use LLM to detect and extract entries within a section.

    Args:
        section_hierarchy: Full path (e.g., ["Research Experience", "Publications", "Peer-Reviewed"])
        doc_elements: List of elements from extract_docx_structure (paragraphs + tables)
        start_elem_idx: Starting element index for this section
        end_elem_idx: Ending element index for this section (before next section)
        document_uid: Document identifier for logging
        header_indices: Set of element indices that are section/subsection headers (to exclude)
        element_index_map: Map from element index to element data

    Returns:
        Tuple of (entries_list, cost_info_dict)
    """
    if header_indices is None:
        header_indices = set()
    if element_index_map is None:
        element_index_map = {}

    # Extract elements for this section, excluding headers
    # We now handle unified elements: paragraph, table_header, table_content, empty
    section_elements = []
    skipped_headers = 0

    for elem in doc_elements:
        # Use unified_idx (from extract_unified_elements) or fall back to idx
        idx = elem.get("unified_idx", elem.get("idx"))
        elem_type = elem.get("type", "")

        # Skip elements outside our section range
        if isinstance(idx, int):
            if idx < start_elem_idx or idx > end_elem_idx:
                continue
            if idx in header_indices:
                skipped_headers += 1
                continue
        else:
            # Non-integer index (shouldn't happen with unified elements)
            continue

        # Handle different element types from extract_unified_elements
        if elem_type == "paragraph":
            text = elem.get("text", "").strip()
            if text:
                section_elements.append({
                    "idx": idx,
                    "text": text,
                    "type": "paragraph"
                })
        elif elem_type == "empty":
            # Include empty paragraphs as break markers - these help the LLM
            # identify natural entry boundaries (blank lines between entries)
            section_elements.append({
                "idx": idx,
                "text": "",
                "type": "empty"
            })
        elif elem_type == "table_header":
            # Table headers are section markers - usually skip as they're in header_indices
            # But include if not marked as header (rare case)
            text = elem.get("text", "").strip()
            if text:
                section_elements.append({
                    "idx": idx,
                    "text": text,
                    "type": "table_header"
                })
        elif elem_type == "table_content":
            # Table content contains the actual data rows
            # Extract individual rows for row-level processing
            table_data = elem.get("data", [])  # List of row data
            table_index = elem.get("table_index")

            if isinstance(table_data, list) and table_data:
                # Break table into individual rows for LLM processing
                for row_idx, row in enumerate(table_data):
                    # Check if this row contains merged entries that should be split
                    if isinstance(row, list):
                        pseudo_rows = split_merged_row_into_pseudo_rows(row)
                        if pseudo_rows:
                            # Row contains multiple merged entries - create pseudo-rows
                            for pseudo_idx, pseudo_row in enumerate(pseudo_rows):
                                row_text = " | ".join(pseudo_row).strip()
                                if row_text:
                                    section_elements.append({
                                        "idx": f"{idx}.{row_idx}.{pseudo_idx}",  # Sub-sub-index
                                        "text": row_text[:300] + ("..." if len(row_text) > 300 else ""),
                                        "type": "table_row",
                                        "full_text": row_text,
                                        "table_index": table_index,
                                        "row_index": row_idx,
                                        "pseudo_row_index": pseudo_idx,
                                        "parent_idx": idx
                                    })
                        else:
                            # Normal row - flatten cells to text
                            row_text = " | ".join(
                                cell.get("text", "") if isinstance(cell, dict) else str(cell)
                                for cell in row
                            ).strip()
                            if row_text:
                                section_elements.append({
                                    "idx": f"{idx}.{row_idx}",  # Sub-index for rows
                                    "text": row_text[:300] + ("..." if len(row_text) > 300 else ""),
                                    "type": "table_row",
                                    "full_text": row_text,
                                    "table_index": table_index,
                                    "row_index": row_idx,
                                    "parent_idx": idx
                                })
                    elif isinstance(row, dict):
                        row_text = row.get("text", "").strip()
                        if row_text:
                            section_elements.append({
                                "idx": f"{idx}.{row_idx}",
                                "text": row_text[:300] + ("..." if len(row_text) > 300 else ""),
                                "type": "table_row",
                                "full_text": row_text,
                                "table_index": table_index,
                                "row_index": row_idx,
                                "parent_idx": idx
                            })
                    else:
                        row_text = str(row).strip()
                        if row_text:
                            section_elements.append({
                                "idx": f"{idx}.{row_idx}",
                                "text": row_text[:300] + ("..." if len(row_text) > 300 else ""),
                                "type": "table_row",
                                "full_text": row_text,
                                "table_index": table_index,
                                "row_index": row_idx,
                                "parent_idx": idx
                            })
            else:
                # Fallback: treat as single element if no row data
                text = elem.get("text", "").strip()
                if text:
                    section_elements.append({
                        "idx": idx,
                        "text": text[:500] + ("..." if len(text) > 500 else ""),
                        "type": "table_content",
                        "full_text": text,
                        "rows": elem.get("rows", 0),
                        "table_index": table_index
                    })
        elif elem_type == "table":
            # Legacy table format (from extract_docx_structure)
            # Split table into individual rows for proper entry detection
            table_idx = elem.get("table_index", 0)
            table_data = elem.get("data", [])

            if isinstance(table_data, list) and len(table_data) > 1:
                # Multi-row table: break into individual rows for LLM processing
                # This allows the LLM to identify individual entries (grants, publications, etc.)
                for row_idx, row in enumerate(table_data):
                    # Check if this row contains merged entries that should be split
                    if isinstance(row, list):
                        pseudo_rows = split_merged_row_into_pseudo_rows(row)
                        if pseudo_rows:
                            # Row contains multiple merged entries - create pseudo-rows
                            for pseudo_idx, pseudo_row in enumerate(pseudo_rows):
                                row_text = " | ".join(pseudo_row).strip()
                                if row_text:
                                    section_elements.append({
                                        "idx": f"{idx}.{row_idx}.{pseudo_idx}",  # Sub-sub-index
                                        "text": row_text[:300] + ("..." if len(row_text) > 300 else ""),
                                        "type": "table_row",
                                        "full_text": row_text,
                                        "table_index": table_idx,
                                        "row_index": row_idx,
                                        "pseudo_row_index": pseudo_idx,
                                        "parent_idx": idx
                                    })
                        else:
                            # Normal row - flatten cells to text
                            row_text = " | ".join(
                                cell.get("text", "") if isinstance(cell, dict) else str(cell)
                                for cell in row
                            ).strip()
                            if row_text:
                                section_elements.append({
                                    "idx": f"{idx}.{row_idx}",  # Sub-index for rows
                                    "text": row_text[:300] + ("..." if len(row_text) > 300 else ""),
                                    "type": "table_row",
                                    "full_text": row_text,
                                    "table_index": table_idx,
                                    "row_index": row_idx,
                                    "parent_idx": idx
                                })
                    elif isinstance(row, dict):
                        row_text = row.get("text", "").strip()
                        if row_text:
                            section_elements.append({
                                "idx": f"{idx}.{row_idx}",
                                "text": row_text[:300] + ("..." if len(row_text) > 300 else ""),
                                "type": "table_row",
                                "full_text": row_text,
                                "table_index": table_idx,
                                "row_index": row_idx,
                                "parent_idx": idx
                            })
                    else:
                        row_text = str(row).strip()
                        if row_text:
                            section_elements.append({
                                "idx": f"{idx}.{row_idx}",
                                "text": row_text[:300] + ("..." if len(row_text) > 300 else ""),
                                "type": "table_row",
                                "full_text": row_text,
                                "table_index": table_idx,
                                "row_index": row_idx,
                                "parent_idx": idx
                            })
            else:
                # Single-row table or no data: treat as single element
                table_text = get_element_text(elem)
                if table_text:
                    section_elements.append({
                        "idx": idx if isinstance(idx, int) else f"table_{table_idx}",
                        "text": table_text[:500] + ("..." if len(table_text) > 500 else ""),
                        "type": "table",
                        "full_text": table_text,
                        "rows": elem.get("rows", 0),
                        "cols": elem.get("cols", 0)
                    })

    if not section_elements:
        return [], {"cost": 0, "tokens": 0}

    # Build context for LLM
    section_name = section_hierarchy[-1] if section_hierarchy else "Unknown Section"
    full_hierarchy = " > ".join(section_hierarchy)

    # Process in batches if section is large
    BATCH_SIZE = 50
    all_validated_entries = []
    total_cost = 0.0
    total_tokens = 0
    prompt_tokens = 0
    completion_tokens = 0

    batches = make_batches(section_elements, BATCH_SIZE)
    num_batches = len(batches)

    for batch_idx, batch_elements in enumerate(batches):
        batch_start = batch_idx * BATCH_SIZE
        batch_end = batch_start + len(batch_elements)

        if num_batches > 1:
            logger.info(f"    Processing batch {batch_idx + 1}/{num_batches} (elements {batch_start + 1}-{batch_end} of {len(section_elements)})")

        # Create element list for LLM (handles paragraphs, table_content, and legacy tables)
        element_list_parts = []
        for elem in batch_elements:
            idx = elem['idx']
            text = elem['text']
            elem_type = elem.get('type', 'paragraph')

            # Format based on element type
            if elem_type == 'table_row':
                # Individual table row - show as ROW
                element_list_parts.append(f"[{idx}] (ROW) {text[:200]}{'...' if len(text) > 200 else ''}")
            elif elem_type == 'table_content':
                # Table content - show as table with row count
                # 'rows' may be an integer count or a list; handle both
                rows_data = elem.get('rows', 0)
                row_count = len(rows_data) if isinstance(rows_data, list) else (rows_data if isinstance(rows_data, int) else 0)
                element_list_parts.append(f"[{idx}] (TABLE {row_count} rows) {text[:200]}{'...' if len(text) > 200 else ''}")
            elif elem_type == 'table':
                # Legacy table format
                rows = elem.get('rows', 0)
                cols = elem.get('cols', 0)
                element_list_parts.append(f"[{idx}] (TABLE {rows}x{cols}) {text[:200]}{'...' if len(text) > 200 else ''}")
            elif elem_type == 'table_header':
                element_list_parts.append(f"[{idx}] (HEADER) {text[:150]}{'...' if len(text) > 150 else ''}")
            elif elem_type == 'empty':
                # Empty paragraph - show as blank line marker to help LLM identify entry boundaries
                element_list_parts.append(f"[{idx}] (BLANK LINE)")
            else:
                # paragraph
                element_list_parts.append(f"[{idx}] {text[:150]}{'...' if len(text) > 150 else ''}")

        paragraph_list = "\n".join(element_list_parts)

        system_prompt = """You are an intelligent parser analyzing a CV section. Your goal is to identify individual **logical entries** (e.g., a single publication, position, award, or course) from a list of raw paragraphs or table rows. Always respond with valid JSON only."""

        user_prompt = f"""## Input Data

**CV Section Header:** `{full_hierarchy}`

**Raw Data (Paragraphs):**
{paragraph_list}

-----

## Instructions

### 1. Grouping Logic (Detecting Multi-Part Entries)

A single logical entry may span multiple lines (paragraphs or table rows). You must determine if a line starts a **new entry** or is a **continuation** of the previous one.

**Merge consecutive lines into a SINGLE entry when:**

* **The First Line (Start):** Introduces the main item (contains the date, role, title, course, or event).
* **The Following Line (Continuation):** Does **NOT** introduce a new date, role, or top-level identifier. Instead, it provides dependent details (descriptions, notes, durations, session counts).
    * *Rule of thumb:* If the second line would be confusing or incomplete when read alone, but clearly belongs to the line above, group them.

**BLANK LINE markers indicate entry boundaries:**

* Lines marked `(BLANK LINE)` represent empty paragraphs in the original document
* These typically separate distinct entries - do NOT group across blank lines unless the content clearly belongs together
* Ignore blank line indices when setting element_idx_start and element_idx_end

**Set Indices Accordingly:**

* `element_idx_start`: Index of the line introducing the item.
* `element_idx_end`: Index of the last line containing details for that same item.
* If an entry is a single line, start and end indices are identical.

### 2. Handling Tables

* **Whole Table as Entry:** If a table describes a *single* summary item (e.g., a summary of one grant), treat the entire table as one entry (`element_type: "table"`).
* **Rows as Entries:** If a table lists *multiple* items (e.g., a list of courses), treat each row (or group of rows based on the logic above) as a separate entry (`element_type: "table_row"`).

### 3. Output Format

Respond **only** with a JSON array containing the identified entries. If no entries are found, return `[]`.

**JSON Structure:**

```json
[
  {{
    "element_idx_start": 120,
    "element_idx_end": 121,
    "element_type": "paragraph",
    "confidence": 0.95,
    "reasoning": "Details in 121 belong to item in 120"
  }}
]
```

*Note: `element_type` must be "paragraph", "table", or "table_row".*

**IMPORTANT:**
- Use the exact paragraph indices shown in brackets [idx]
- Confidence should be 0.0 to 1.0
- Keep reasoning brief (<60 chars), especially for multi-line groupings
"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        try:
            llm_result = call_llm(
                stage="stage_2",
                messages=messages,
                response_format={"type": "json_object"},
                temperature=0.1
            )

            result_text = llm_result["content"]
            result = json.loads(result_text)

            # Accumulate cost
            total_cost += llm_result["cost"]
            total_tokens += llm_result["total_tokens"]
            prompt_tokens += llm_result["prompt_tokens"]
            completion_tokens += llm_result["completion_tokens"]

            # Extract delimiters array - LLM may return in different keys
            if isinstance(result, dict):
                # Try common keys the LLM might use
                delimiters = (
                    result.get("delimiters") or
                    result.get("entries") or
                    result.get("result") or
                    result.get("data") or
                    []
                )
            else:
                delimiters = result

            # Build a lookup for batch elements by index (use string keys for consistency)
            batch_elem_lookup = {str(elem['idx']): elem for elem in batch_elements}
            claimed_row_keys = set()

            # Validate delimiters and extract full text
            for delim in delimiters:
                if "element_idx_start" in delim and "element_idx_end" in delim:
                    start_idx = delim["element_idx_start"]
                    end_idx = delim["element_idx_end"]

                    # Handle table indices (strings like "table_0")
                    if isinstance(start_idx, str) and start_idx.startswith("table_"):
                        # Table entry - get full text from batch elements
                        if start_idx in batch_elem_lookup:
                            elem = batch_elem_lookup[start_idx]
                            full_text = elem.get("full_text", elem.get("text", ""))
                            entry = {
                                "element_idx_start": start_idx,
                                "element_idx_end": end_idx,
                                "element_type": "table",
                                "confidence": delim.get("confidence", 1.0),
                                "text": full_text
                            }
                            all_validated_entries.append(entry)
                        continue

                    # Handle row sub-indices (floats like 22.2 or strings like "22.2")
                    # These come from table_content rows with format "parent_idx.row_idx"
                    start_idx_str = str(start_idx)
                    end_idx_str = str(end_idx)
                    if "." in start_idx_str or "." in end_idx_str:
                        # Row sub-index - look up in batch elements by string key
                        if start_idx_str in batch_elem_lookup:
                            elem = batch_elem_lookup[start_idx_str]
                            full_text = elem.get("full_text", elem.get("text", ""))
                            entry = {
                                "element_idx_start": start_idx_str,
                                "element_idx_end": end_idx_str,
                                "element_type": "table_row",
                                "confidence": delim.get("confidence", 1.0),
                                "text": full_text,
                                "table_index": elem.get("table_index"),
                                "row_index": elem.get("row_index"),
                                "parent_idx": elem.get("parent_idx")
                            }
                            all_validated_entries.append(entry)
                            claimed_row_keys.add(start_idx_str)
                        continue

                    # Handle paragraph indices (integers)
                    if not isinstance(start_idx, int) or not isinstance(end_idx, int):
                        continue

                    # Ensure indices are within bounds
                    if start_idx >= start_elem_idx and end_idx <= end_elem_idx:
                        # Extract full text for all elements in the entry range
                        full_text_parts = []
                        for idx in range(start_idx, end_idx + 1):
                            if idx in element_index_map:
                                elem = element_index_map[idx]
                                elem_text = get_element_text(elem)
                                if elem_text:
                                    full_text_parts.append(elem_text)

                        # Build entry with full text
                        entry = {
                            "element_idx_start": start_idx,
                            "element_idx_end": end_idx,
                            "element_type": delim.get("element_type", "paragraph"),
                            "confidence": delim.get("confidence", 1.0),
                            "text": "\t".join(full_text_parts)  # Tab-separated for compact format
                        }
                        all_validated_entries.append(entry)

            # Backstop: emit any table row this batch never claimed (#420).
            #
            # The model returns sub-row indices as JSON NUMBERS, so a row index
            # with a trailing zero collapses: "114.10" parses to the float 114.1,
            # str() renders it back as "114.1", and the lookup lands on row 1 --
            # row 10 is unreachable. C0ZGFW element 114 lost rows 10/20/30/40/50
            # exactly this way, and element 109 lost row 10. Rows the model simply
            # omitted disappear identically. Either way the content survived only
            # inside whatever whole-table entry the model happened to emit, which
            # is what made those blobs load-bearing (and what made #227's dedup
            # drop real content loss).
            #
            # Recovering the rows here is deterministic and costs no extra LLM
            # call. It also runs BEFORE remove_subset_delimiters, so a whole-table
            # blob whose rows are now all present becomes a provable duplicate and
            # is collapsed by the #418 coverage check.
            #
            # ponytail: a backstop, not a cure. The real fix is to stop round
            # tripping these indices through JSON numbers -- emit them as strings
            # ("114.10") or renumber rows to unique ints. Do that and this loop
            # only ever recovers rows the model genuinely skipped.
            recovered = recover_unclaimed_table_rows(batch_elements, claimed_row_keys)
            all_validated_entries.extend(recovered)
            if recovered:
                logger.info(f"    Recovered {len(recovered)} unclaimed table row(s) "
                            f"in batch {batch_idx + 1} [{full_hierarchy}]")

        except Exception as e:
            logger.warning(f"    ⚠ Error in batch {batch_idx + 1}: {e} [{full_hierarchy}]")
            continue

    # Remove subset/duplicate entries from all batches
    all_validated_entries = remove_subset_delimiters(all_validated_entries)

    # Return entries and cost info
    cost_info = {
        "cost": total_cost,
        "tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
    }
    return all_validated_entries, cost_info


def _bound_coverage_indices(
    all_assigned_indices: set[int | str], doc_length: int
) -> tuple[set[int], set[int]]:
    """Coverage indices bounded to doc_length's own index space (#856).

    `all_assigned_indices` mixes real integer paragraph positions with
    table-row string sub-indices ("2.1") and the artificial int parent
    marker added alongside each claimed row's string index -- a set already
    de-dupes those two against each other, but a table's own unified index
    can still land outside doc_length's paragraph-only count (doc_length is
    built from num_paragraphs/num_empty only, which never counts a table as
    a paragraph slot). Intersecting with `range(doc_length)` keeps both the
    gap check and the coverage numerator subsets of doc_length, so a caller
    computing `len(covered) / doc_length` can never exceed 100%.

    Returns (unaccounted_indices, covered_doc_indices).
    """
    all_doc_indices = set(range(doc_length))
    integer_assigned = {idx for idx in all_assigned_indices if isinstance(idx, int)}
    return all_doc_indices - integer_assigned, all_doc_indices & integer_assigned


# I/O-bound stage (LLM round trips, not CPU); the default is sized under the
# per-pod semaphore so one run cannot starve the others admitted alongside it
# (#881). Knob: CVICHE_STAGE2_SECTION_WORKERS, env var or llm yaml key.
STAGE2_SECTION_WORKERS = workers_from_config("CVICHE_STAGE2_SECTION_WORKERS")


class _SectionResult(NamedTuple):
    entries: list[dict]
    cost_info: dict
    assigned: set
    lines: list[str]


def _section_progress_printer(hierarchy_paths: list[list[str]]) -> Callable[[int, _SectionResult], None]:
    """Build a map_in_order ``on_result`` callback: one atomic print per
    finished section, numbered by completion.

    map_in_order guarantees ``on_result`` fires only on the CALLING thread,
    one call at a time -- both its serial path and its ``as_completed`` loop
    invoke it inline, never from a pool thread -- so despite the pool
    underneath, this closure is single-threaded: no lock, no ``nonlocal``
    gymnastics beyond the one ``done`` counter needs as a closure variable.
    The ``[N/M] Processing:`` line is a parsed contract -- orchestrator.py's
    PROGRESS_PATTERNS read it into the progress bar -- so N counts sections
    *finished*, which stays monotonic however the pool orders completions,
    and the whole block goes out in one print so two sections' lines cannot
    splice (#881).
    """
    done = 0

    def on_result(index: int, result: _SectionResult) -> None:
        nonlocal done
        _, _, _, lines = result
        done += 1
        print("\n".join([
            f"[{done}/{len(hierarchy_paths)}] Processing: {' > '.join(hierarchy_paths[index])}",
            *lines,
        ]))

    return on_result


def _extract_section(
    hierarchy_path: list[str],
    start_idx: int,
    end_idx: int,
    *,
    doc_elements: list[dict],
    header_indices: set,
    element_index_map: dict,
    header_info: dict[int, list[str]],
    doc: Document,
    document_uid: str | None,
    cancel_check: Callable[[], None] | None,
) -> _SectionResult:
    """One section's body of run_stage_2's loop: detect its entries, assign
    headers/breaks around it, and report what happened -- the per-section
    unit map_in_order fans out over (#881).

    ``cancel_check`` runs first, on whichever thread this call lands on, so a
    cancelled run raises before each section's LLM call: map_in_order cancels
    the queue on the first raise, and any section a pool thread dequeues
    before that shutdown lands raises here too, since the orchestrator's
    cancel is persistent. Sections already in flight finish.

    Returns ``(section_entries_in_order, cost_info, section_assigned, lines)``
    instead of mutating run_stage_2's shared ``all_entries`` /
    ``all_assigned_indices`` / ``total_cost`` / ``total_tokens``, and instead
    of printing. A print here would still reach the run's progress capture --
    since #883 orchestrator.py's _RoutedStdout also routes by a ContextVar
    that map_in_order's per-call copy_context() carries onto the pool
    thread -- but not correctly: the ``[N/M] Processing:`` line must be
    numbered by *completion*, which only the calling thread (running
    _section_progress_printer as map_in_order's on_result) knows, and each
    section's block must land as one atomic print so two sections finishing
    close together cannot splice their lines together.
    """
    if cancel_check is not None:
        cancel_check()

    # Detect and extract entries using document structure (paragraphs + tables)
    entries, cost_info = detect_entries_for_section(
        hierarchy_path,
        doc_elements,
        start_idx,
        end_idx + 1,  # end_idx is inclusive, so add 1 for range
        document_uid=document_uid,
        header_indices=header_indices,
        element_index_map=element_index_map
    )

    # Track which indices are assigned to entries
    section_assigned = set()
    for entry in entries:
        start_idx_entry = entry["element_idx_start"]
        end_idx_entry = entry["element_idx_end"]

        # Handle string indices (table rows like "22.2"): section_assigned is
        # later diffed against section_range = set(range(...)), which holds
        # only ints, so only the parent table index (an int) protects
        # anything here -- the string form itself is never read by any
        # consumer (#856's _bound_coverage_indices filters int-only by
        # construction too). Mark the parent table index as assigned to
        # prevent it from being added as a "break" entry.
        if isinstance(start_idx_entry, str) or isinstance(end_idx_entry, str):
            start_idx_str = str(start_idx_entry)
            if "." in start_idx_str:
                parent_idx = int(start_idx_str.split(".")[0])
                section_assigned.add(parent_idx)
        else:
            # Integer range for paragraph entries
            for idx in range(start_idx_entry, end_idx_entry + 1):
                section_assigned.add(idx)

    # Find unassigned indices in this section's range
    section_range = set(range(start_idx, end_idx + 1))
    unassigned_in_section = section_range - section_assigned - header_indices

    # Add header entries for this section
    section_headers = []
    for idx in sorted(section_range & header_indices):
        header_text = _element_text_or_fallback(idx, element_index_map, doc)
        header_entry = {
            "element_idx_start": idx,
            "element_idx_end": idx,
            "element_type": "header",
            "confidence": 1.0,
            "text": header_text,
            "hierarchy": header_info.get(idx, hierarchy_path)
        }
        section_headers.append(header_entry)
        section_assigned.add(idx)

    # Add break entries for unassigned indices (blank lines, etc.)
    break_entries = []
    for idx in sorted(unassigned_in_section):
        para_text = _element_text_or_fallback(idx, element_index_map, doc)
        break_entry = {
            "element_idx_start": idx,
            "element_idx_end": idx,
            "element_type": "break",
            "confidence": 1.0,
            "text": para_text,  # Usually empty, but capture if not
            "hierarchy": hierarchy_path
        }
        break_entries.append(break_entry)
        section_assigned.add(idx)

    # Add hierarchy to content entries
    for entry in entries:
        entry["hierarchy"] = hierarchy_path

    content_count = len(entries)
    header_count = len(section_headers)
    break_count = len(break_entries)

    if content_count > 0:
        summary = f"  ✓ Extracted {content_count} entries, {header_count} headers, {break_count} breaks (${cost_info.get('cost', 0):.4f})"
    else:
        summary = f"  - No content entries ({header_count} headers, {break_count} breaks)"

    lines = [f"  Elements: {start_idx} to {end_idx}", summary, ""]

    # Combine all entries for this section: headers -> content -> breaks,
    # the same order the pre-#881 loop extended all_entries in.
    section_entries_in_order = section_headers + entries + break_entries

    return _SectionResult(section_entries_in_order, cost_info, section_assigned, lines)


def run_stage_2(
    docx_path: str,
    hierarchy_json_path: str = None,
    cancel_check: Callable[[], None] | None = None,
    strip_template_instructions: bool = True,
    workers: int = STAGE2_SECTION_WORKERS,
):
    """
    Main Stage 2: Extract entries from CV sections using LLM

    Args:
        docx_path: Path to Word document
        hierarchy_json_path: Optional path to Stage 1b hierarchy JSON with boundaries
                            (if not provided, will use OutputManager to find it)
        cancel_check: Optional zero-arg callable invoked at the top of each
                            section iteration. It should raise to abort the run
                            (the web orchestrator passes its check_cancelled,
                            which raises CancelledException). This stage makes
                            one LLM call per section, so without an intra-stage
                            check a cancel would not land until all ~86 sections
                            finished. None (the standalone CLI default) is a
                            no-op, leaving CLI behavior unchanged.
        strip_template_instructions: When True (default), drop WCM-template
                            instruction boilerplate from the extracted entries.
                            When False, keep the instruction text so it survives
                            into the output.
        workers: Sections extracted at once (default STAGE2_SECTION_WORKERS).
                            1 reproduces the pre-#881 serial loop.
    """

    print(f"Input: {docx_path}")
    print()

    # Setup
    om = OutputManager(docx_path)

    # Load Stage 1b hierarchy with boundaries
    if hierarchy_json_path is None:
        hierarchy_json_path = om.get_stage1b_path()

    print(f"Loading hierarchy with boundaries: {hierarchy_json_path}")
    with open(hierarchy_json_path) as f:
        hierarchy_data = json.load(f)

    # Extract document structure using unified elements (matches Stage 1b)
    # This gives us paragraphs + table_header + table_content with unified indices
    print(f"Extracting document structure from: {docx_path}")
    doc_structure = extract_unified_elements(docx_path)
    doc_elements = doc_structure.get("elements", [])
    element_index_map = build_element_index_map(doc_structure)

    print(f"  Paragraphs: {doc_structure['meta']['num_paragraphs']}")
    print(f"  Tables: {doc_structure['meta']['num_tables']}")
    print(f"  Table headers: {doc_structure['meta']['num_table_headers']}")
    print(f"  Empty elements: {doc_structure['meta']['num_empty']}")
    print()

    # Also load Document for backward compatibility with paragraph-based indexing
    doc = Document(docx_path)

    # Extract leaf sections with boundaries from Stage 1b
    section_boundaries = hierarchy_data.get("section_boundaries", [])
    hierarchy = hierarchy_data.get("hierarchy_with_indices", [])

    leaf_sections = extract_leaf_sections_with_boundaries(section_boundaries, hierarchy)

    # Collect all header indices to exclude from paragraph lists
    header_indices = collect_header_indices(hierarchy)
    print(f"Collected {len(header_indices)} header indices to exclude from entry detection")

    # Find the first section start index to process "Personal Data" section
    first_section_idx = min([start for _, start, _ in leaf_sections]) if leaf_sections else 0

    # Add "Personal Data" section for elements before the first section
    sections_to_process = []
    if first_section_idx > 0:
        sections_to_process.append((["Personal Data"], 0, first_section_idx - 1))

    sections_to_process.extend(leaf_sections)

    print(f"Found {len(leaf_sections)} leaf sections to analyze")
    if first_section_idx > 0:
        print(f"  + Personal Data section (elements 0-{first_section_idx - 1})")
    print()

    # Build a map of header indices to their hierarchy paths
    header_info = collect_header_info(hierarchy)

    # Process each section and collect all entries
    all_entries = []
    total_cost = 0.0
    total_tokens = 0
    document_uid = hierarchy_data.get("document_uid")
    # Index space is the unified element stream, tables included (#870).
    doc_length = len(doc_elements)

    # Track all assigned indices for coverage analysis
    all_assigned_indices = set()

    # Add parent header entries (headers of non-leaf sections)
    # These are headers that are at the start of sections with children
    parent_header_indices = set()
    for section in section_boundaries:
        if section.get("has_children", False):
            idx = section.get("element_idx_start")
            if idx is not None and idx in header_indices:
                parent_header_indices.add(idx)

    for idx in sorted(parent_header_indices):
        header_text = _element_text_or_fallback(idx, element_index_map, doc)
        parent_header_entry = {
            "element_idx_start": idx,
            "element_idx_end": idx,
            "element_type": "header",
            "confidence": 1.0,
            "text": header_text,
            "hierarchy": header_info.get(idx, [header_text])
        }
        all_entries.append(parent_header_entry)
        all_assigned_indices.add(idx)

    # Add break entries for gaps between parent headers and their first child's start
    # These are typically empty lines after a parent header
    leaf_starts = set(start for _, start, _ in sections_to_process)
    for section in section_boundaries:
        if section.get("has_children", False):
            parent_start = section.get("element_idx_start")
            parent_end = section.get("element_idx_end")
            if parent_start is not None:
                # Find the first leaf section that starts within this parent's range
                first_child_start = None
                for leaf_start in sorted(leaf_starts):
                    if parent_start < leaf_start <= parent_end:
                        first_child_start = leaf_start
                        break

                # Add break entries for indices between parent header and first child
                if first_child_start is not None:
                    for gap_idx in range(parent_start + 1, first_child_start):
                        if gap_idx not in all_assigned_indices:
                            gap_text = _element_text_or_fallback(gap_idx, element_index_map, doc)
                            gap_entry = {
                                "element_idx_start": gap_idx,
                                "element_idx_end": gap_idx,
                                "element_type": "break",
                                "confidence": 1.0,
                                "text": gap_text,
                                "hierarchy": header_info.get(parent_start, section.get("hierarchy", []))
                            }
                            all_entries.append(gap_entry)
                            all_assigned_indices.add(gap_idx)

    # Extract every section's entries on a thread pool, section-level grain
    # (#881); workers=1 reproduces the pre-#881 serial loop exactly, one
    # context, no pool. Results come back in SUBMISSION order regardless of
    # completion order -- required so the accumulation below, and therefore
    # the output artifact, is byte-identical to the old serial loop; the
    # later `all_entries.sort(key=sort_key)` is stable but this must not
    # rely on that to hide an accumulation-order bug.
    results = map_in_order(
        partial(
            _extract_section,
            doc_elements=doc_elements,
            header_indices=header_indices,
            element_index_map=element_index_map,
            header_info=header_info,
            doc=doc,
            document_uid=document_uid,
            cancel_check=cancel_check,
        ),
        [(path, s, e) for path, s, e in sections_to_process],
        workers,
        on_result=_section_progress_printer([p for p, _, _ in sections_to_process]),
    )
    for section_entries, cost_info, section_assigned, _lines in results:
        total_cost += cost_info.get("cost", 0)
        total_tokens += cost_info.get("tokens", 0)
        all_assigned_indices |= section_assigned
        all_entries.extend(section_entries)

    # Sort entries by element_idx_start for consistent output
    # Handle mixed int/string indices (e.g., 22 vs "22.2")
    def sort_key(entry):
        idx = entry["element_idx_start"]
        if isinstance(idx, str) and "." in idx:
            # Row sub-index like "22.2" -> (22, 2)
            parts = idx.split(".", 1)
            return (float(parts[0]), float(parts[1]) if len(parts) > 1 else 0)
        elif isinstance(idx, str):
            # String index like "table_0" -> (1000000 + idx number)
            if idx.startswith("table_"):
                return (1000000 + int(idx.split("_")[1]), 0)
            return (float(idx), 0)
        else:
            return (float(idx), 0)

    all_entries.sort(key=sort_key)

    # Calculate coverage statistics
    content_entries = [e for e in all_entries if e["element_type"] not in ("header", "break")]
    header_entries = [e for e in all_entries if e["element_type"] == "header"]
    break_entries = [e for e in all_entries if e["element_type"] == "break"]

    # Check for any gaps in coverage; the coverage numerator is bounded to
    # doc_length's own index space (see _bound_coverage_indices -- #856).
    unaccounted_indices, covered_doc_indices = _bound_coverage_indices(all_assigned_indices, doc_length)

    # Drop WCM-template instruction boilerplate (Layer 1, primary filter).
    # Faculty leave the blank template's instruction scaffolding in their CVs;
    # those blocks get parsed as entries and pollute downstream output. The
    # detector is precision-biased (never drops real CV content). Section
    # headers are intentionally NOT dropped here. Gated on the user's choice:
    # when strip_template_instructions is False, the instruction text is kept.
    if strip_template_instructions:
        _pre_filter_count = len(all_entries)
        all_entries = [
            e for e in all_entries
            if not is_template_instruction(e.get("text", ""))
        ]
        _filtered_count = _pre_filter_count - len(all_entries)
        if _filtered_count:
            print(f"Filtered {_filtered_count} WCM-template instruction entries")

    # Drop empty content entries and exact duplicates (#211). Unconditional:
    # unlike the instruction filter above, this never removes real content.
    all_entries = filter_extraction_noise(all_entries)

    # Recompute coverage buckets after filtering so reported counts are accurate.
    content_entries = [e for e in all_entries if e["element_type"] not in ("header", "break")]
    header_entries = [e for e in all_entries if e["element_type"] == "header"]
    break_entries = [e for e in all_entries if e["element_type"] == "break"]

    # Save output
    output_path = om.get_stage2_path()

    output_data = {
        "document_uid": document_uid,
        "document_length": doc_length,
        "total_entries": len(all_entries),
        "total_cost": total_cost,
        "total_tokens": total_tokens,
        "coverage": {
            "content_entries": len(content_entries),
            "header_entries": len(header_entries),
            "break_entries": len(break_entries),
            "assigned_indices": len(covered_doc_indices),  # bounded to doc_length -- see _bound_coverage_indices (#856)
            "unaccounted_indices": sorted(list(unaccounted_indices)) if unaccounted_indices else [],
            "coverage_percentage": round(len(covered_doc_indices) / doc_length * 100, 1) if doc_length > 0 else 100.0
        },
        "entries": all_entries
    }

    with open(output_path, 'w') as f:
        json.dump(output_data, f, indent=2)

    print("="*80)
    print("STAGE 2 COMPLETE")
    print("="*80)
    print(f"Output: {output_path}")
    print(f"Document length: {doc_length} paragraphs")
    print(f"Coverage: {output_data['coverage']['coverage_percentage']}%")
    print(f"  Content entries: {len(content_entries)}")
    print(f"  Header entries: {len(header_entries)}")
    print(f"  Break entries: {len(break_entries)}")
    if unaccounted_indices:
        print(f"  ⚠ Unaccounted indices: {sorted(list(unaccounted_indices))[:10]}{'...' if len(unaccounted_indices) > 10 else ''}")
    print(f"Total cost: ${total_cost:.4f}")
    print(f"Total tokens: {total_tokens:,}")
    print("="*80)

    return output_data, output_path


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python stage_2_entry_extraction.py <docx_path> [hierarchy_json]")
        print()
        print("Example:")
        print("  python stage_2_entry_extraction.py data/sample_cvs/word/2071_Zuschlag_Cv.docx")
        sys.exit(1)

    docx_path = sys.argv[1]
    hierarchy_json = sys.argv[2] if len(sys.argv) > 2 else None

    run_stage_2(docx_path, hierarchy_json)
