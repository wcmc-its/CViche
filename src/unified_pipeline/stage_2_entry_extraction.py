#!/usr/bin/env python3
"""
Stage 2: Entry Extraction (Combined Delimiter Detection + Text Extraction)

Uses LLM to identify individual entries within each CV section and extracts their full text.
This stage combines the former Stage 2a (delimiter detection) and Stage 2b (text extraction).

Input: Stage 1b hierarchy JSON + Word document
Output: JSON with extracted entries including full text and hierarchy context
"""

import bisect
import collections
import itertools
import json
import logging
import os
import re
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from functools import partial
from pathlib import Path
from typing import NamedTuple

from docx import Document

# Add to path
sys.path.insert(0, str(Path(__file__).parent))

from core.docx_structure_extractor import (
    extract_docx_structure,
    extract_unified_elements,
    join_row_cells,
    open_source_docx,
    row_cell_texts,
)
from core.output_manager import OutputManager
from core.template_boilerplate import (
    is_foreign_template_instruction,
    is_near_template_instruction,
    is_template_instruction,
)

from unified_pipeline.core.batch_pool import (
    make_progress_printer,
    map_in_order,
    workers_from_config,
)
from unified_pipeline.llm.retry import LLMOutageError
from unified_pipeline.llm_client import call_llm
from unified_pipeline.log_redaction import redacted_error_label

logger = logging.getLogger(__name__)


def get_hierarchy_path(node: dict, current_path: list[str] | None = None) -> list[str]:
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


def _header_row_count(element: dict) -> int:
    """Rows at the top of a table element that are a column-label header (#424):
    1 when the reader flagged row 0, else 0. Those rows never become entries,
    and stay out of the text a whole-table span is rebuilt from, so a header
    line cannot leave a whole-table parent looking like it has an uncovered
    remainder (`remove_subset_delimiters`)."""
    return 1 if element.get("header_row") else 0


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
        # Legacy table format - flatten data into text. Stays tab-joined (#488):
        # stage 6 and the doctor branch on '\t' in this text, and a pipe join
        # turns an all-empty wide row into a separator line the #418 dedup
        # counts as uncovered content.
        rows = element.get("data", [])[_header_row_count(element):]
        row_texts = []
        for row in rows:
            cell_texts = [cell.get("text", "") for cell in row]
            row_texts.append("\t".join(cell_texts))
        return "\n".join(row_texts)
    else:
        return element.get("text", "").strip()


def _element_text_or_fallback(idx: int, element_index_map: dict, doc: Document) -> str:
    """Text for a unified element index, falling back to doc.paragraphs
    (`doc` as `open_source_docx` opened it, #1656).

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
    When no header follows a parent (its children all precede it), its whole
    body up to its own element_idx_end is that gap (#1693).

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
                parent_sections[hierarchy_path] = {"start": start_idx, "end": end_idx}

    # Precompute, once, what the parent-gap branch below used to rescan
    # `section_boundaries` for on every parent (#916 rework):
    # - `starts_sorted` turns "smallest start strictly greater than X" into
    #   a single `bisect_right` per parent instead of an O(n) filter+min.
    # - `first_index_at_start` / `leaf_at_start` turn the same-start
    #   suppression check into two dict lookups instead of an O(n) `any(...)`
    #   that compared by object identity. Index comparison (position in
    #   `section_boundaries`, via `first_index_at_start`) replaces that
    #   identity test: a boundary is suppressed by an earlier same-start
    #   entry when it is not the first index recorded for that start, which
    #   also correctly tells apart two structurally-equal-but-distinct dicts
    #   at the same start (a duplicated parent record) without relying on
    #   `is`.
    starts_sorted = sorted(
        b["element_idx_start"] for b in section_boundaries
        if b.get("element_idx_start") is not None
    )
    first_index_at_start: dict[int, int] = {}
    leaf_at_start: dict[int, bool] = {}
    for i, b in enumerate(section_boundaries):
        s = b.get("element_idx_start")
        if s is None:
            continue
        if s not in first_index_at_start:
            first_index_at_start[s] = i
        if not b.get("has_children", False):
            leaf_at_start[s] = True

    for idx, boundary in enumerate(section_boundaries):
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
            # Parent w/ children - gap ends just before the next STRICTLY
            # LATER header (any boundary, any depth). A same-start boundary
            # suppresses it only when it's a leaf, or was listed earlier --
            # the earlier tie-owner emits it, so same-start parents don't
            # cancel each other and drop the content between them (#916).
            parent_path = tuple(hierarchy_path)
            if parent_path in parent_sections:
                suppressed = (
                    leaf_at_start.get(start_idx, False)
                    or first_index_at_start[start_idx] != idx
                )
                next_pos = bisect.bisect_right(starts_sorted, start_idx)
                # No later header: P is last, its children all precede it, so its body runs to its own end (#1693).
                gap_end = starts_sorted[next_pos] - 1 if next_pos < len(starts_sorted) else end_idx
                if not suppressed and gap_end > start_idx:
                    # There's a gap between parent header and the next header
                    # This gap contains content that belongs to the parent section
                    # (e.g., journal articles before "Book" subsection in PUBLICATIONS)
                    print(f"  [DEBUG] Found gap in '{hierarchy_path[-1]}': elements {start_idx + 1} to {gap_end}")
                    leaf_sections.append((hierarchy_path, start_idx + 1, gap_end))

    return leaf_sections


def collect_header_indices(hierarchy_with_indices: list[dict]) -> set:
    """
    Recursively collect all element indices that are section/subsection headers.
    A run-in heading's line is left out: it also holds the section's first
    record, which stage 2 must extract (#1252).

    Args:
        hierarchy_with_indices: Hierarchy structure from Stage 1b with element_idx

    Returns:
        Set of element indices that are headers
    """
    header_indices = set()

    def walk_hierarchy(items):
        for item in items:
            if item.get("element_idx") is not None and not item.get("run_in"):
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


# How much text may follow an unlocated heading's own words on its line and
# still count as that heading -- room for a parenthetical note such as
# "(selected)" or "(2015-2020)", not for a content line that merely opens
# with the same word.
_MAX_HEADING_SUFFIX_CHARS = 80


class _UnlocatedHeading(NamedTuple):
    """A stage-1a heading stage 1b kept in its hierarchy but could not place
    on a document line (``element_idx`` None, not synthetic)."""
    path: tuple[str, ...]


def collect_unlocated_headings(hierarchy_with_indices: list[dict]) -> list[_UnlocatedHeading]:
    """Every non-synthetic heading in stage 1b's hierarchy with no element_idx,
    with its full hierarchy path, in document-outline order."""
    found: list[_UnlocatedHeading] = []
    pending = [(item, ()) for item in reversed(hierarchy_with_indices)]
    while pending:
        item, parent_path = pending.pop()
        text = (item.get("text") or "").strip()
        path = parent_path + (text,) if text else parent_path
        if text and item.get("element_idx") is None and not item.get("synthetic"):
            found.append(_UnlocatedHeading(path))
        pending.extend((child, path) for child in reversed(item.get("children") or []))
    return found


def _collapse_whitespace(text: str) -> str:
    return " ".join(text.split())


# Characters a heading line may open with before the heading itself: a rule of
# asterisks, dashes, underscores or equals signs typed on the same line
# ("***** EDUCATION:", EBYSBC class E17, #1254).
_HEADING_LEAD_SEPARATORS = "*-_= "


def _line_is_heading(line_text: str, heading_text: str) -> bool:
    """True when the line is the heading, or the heading followed by a short
    non-word suffix ("ABSTRACTS (selected)"), after any leading separator run.
    Case-sensitive: 1a copies the heading's text from the line itself."""
    line = _collapse_whitespace(line_text).lstrip(_HEADING_LEAD_SEPARATORS)
    heading = _collapse_whitespace(heading_text)
    if not heading or not line.startswith(heading):
        return False
    suffix = line[len(heading):]
    if not suffix:
        return True
    return not suffix[0].isalnum() and len(suffix) <= _MAX_HEADING_SUFFIX_CHARS


def _repath_section_tail(entries: list[dict], heading_pos: int, new_path: list[str]) -> int:
    """Move the entries after ``entries[heading_pos]`` that share its section
    under ``new_path``, stopping at the next header or section change.
    Returns how many entries moved."""
    old_path = entries[heading_pos]["hierarchy"]
    moved = 0
    for entry in entries[heading_pos + 1:]:
        if entry["element_type"] == "header" or entry["hierarchy"] != old_path:
            break
        entry["hierarchy"] = list(new_path)
        moved += 1
    return moved


def promote_unlocated_headings(entries: list[dict], hierarchy_with_indices: list[dict]) -> list[dict]:
    """Restore a heading stage 1b failed to place (autopsy 2026-10-02, class 6).

    1b leaves such a heading's line inside the previous section, the LLM
    rightly claims no entry for it, and it was emitted as a ``break`` -- so
    every entry under it inherited the previous section's hierarchy (an
    ABSTRACTS heading lost, so 3b coded its abstracts as articles). A break whose
    line is that heading becomes a ``header`` carrying the heading's path, and
    the rest of its section moves under it. Only a heading with exactly one
    matching break is promoted; an ambiguous one is logged and left alone.
    ``entries`` must be sorted by element index; it is updated in place and
    returned.
    """
    for heading in collect_unlocated_headings(hierarchy_with_indices):
        positions = [
            pos for pos, entry in enumerate(entries)
            if entry["element_type"] == "break" and _line_is_heading(entry.get("text") or "", heading.path[-1])
        ]
        if len(positions) != 1:
            if positions:
                logger.warning(f"  Unlocated heading {list(heading.path)} matches {len(positions)} break lines; left as breaks")
            continue
        pos = positions[0]
        moved = _repath_section_tail(entries, pos, list(heading.path))
        entries[pos]["element_type"] = "header"
        entries[pos]["hierarchy"] = list(heading.path)
        logger.info(f"  Promoted break at {entries[pos]['element_idx_start']} to unlocated heading {list(heading.path)}; {moved} entries moved under it")
    return entries


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
        """Convert index to a sortable tuple, one float per dotted part:
        (main_idx, row) or, for a pseudo-row, (main_idx, row, pseudo_row).
        "92.5.10" must not read as "92.5.1" -- float("5.10") is 5.1."""
        if isinstance(idx, str):
            if "." in idx:
                return tuple(float(part) for part in idx.split("."))
            elif idx.startswith("table_"):
                return (1000000 + int(idx.split("_")[1]), 0)
            else:
                return (float(idx), 0)
        return (float(idx), 0)

    def is_bare(idx):
        """True when idx carries no ".row" sub-index (i.e. a whole element)."""
        return not (isinstance(idx, str) and "." in idx)

    def normalize_span(d: dict) -> tuple[tuple[float, ...], tuple[float, ...]]:
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


class _ExactNumber(float):
    """A JSON decimal that remembers the digits the model wrote.

    A table row's index is the text ``"<table>.<row>"``, and the model writes it
    as a bare JSON number: ``474.10`` is row 10 of table 474. As a float it is
    474.1, which is row 1 (#1228) -- the same row key as the model's own row-1
    entries, so a range starting at row 10 read as a range starting at row 1.
    The literal is the only thing that names the row.
    """

    __slots__ = ("literal",)

    def __new__(cls, literal: str) -> _ExactNumber:
        number = super().__new__(cls, literal)
        number.literal = literal
        return number


def parse_delimiter_reply(reply_text: str) -> list | dict:
    """``json.loads`` for a stage-2 reply, with every decimal keeping its literal
    text for ``delimiter_index_text``. Numbers still behave as floats."""
    return json.loads(reply_text, parse_float=_ExactNumber)


def delimiter_index_text(value: object) -> str:
    """The row-index text the model wrote: ``474.10`` for a decimal parsed by
    ``parse_delimiter_reply``, ``str(value)`` for anything else."""
    return value.literal if isinstance(value, _ExactNumber) else str(value)


def unreverse_paragraph_spans(delimiters: object, where: str) -> object:
    """The reply's delimiters with every reversed paragraph span put in order.

    The model sometimes writes a span end-first: ``1178`` to ``1176``. The text
    is then built from an empty ``range(1178, 1177)``, the entry is dropped as
    noise and its lines fall to ``break``. GNONHA lost 12 correctly grouped
    records (23 lines) that way (#1738). Only integer spans are swapped;
    ``row_range_entry`` already handles a reversed table-row span. Anything
    that is not a list is returned as it came, so a malformed reply fails the
    batch exactly as before.
    """
    if not isinstance(delimiters, list):
        return delimiters
    ordered = []
    swapped = 0
    for delim in delimiters:
        start = delim.get("element_idx_start") if isinstance(delim, dict) else None
        end = delim.get("element_idx_end") if isinstance(delim, dict) else None
        if type(start) is int and type(end) is int and end < start:  # bool is not an index
            delim = {**delim, "element_idx_start": end, "element_idx_end": start}
            swapped += 1
        ordered.append(delim)
    if swapped:
        logger.warning(f"    ⚠ Swapped {swapped} reversed entry span(s) (end before start) in {where}")
    return ordered


def row_range_entry(start_key: str, end_key: str, batch_elem_lookup: dict,
                    confidence: float) -> tuple[dict | None, list[str]]:
    """The entry for a delimiter on table sub-rows, and the row keys it claims.

    The model may group several rows of one table into one entry ("80.0" to
    "80.2"). The entry's text must then hold every row in that span: its span
    is what remove_subset_delimiters trusts, and a span wider than its text
    let that drop the #420-recovered rows as "contained" -- AV00TQ's 24
    teaching lines in row 80.2 vanished behind a header-row-only entry
    (#1126).

    A span may also run on past its table, through the paragraphs and tables
    after it: one WCM grant is a five-row table, a "Your role" line and a
    two-row table, and the model answers "236.0" to "238.1" (YDSVDB, #1873).
    The span is then every batch element from the start row to the end, in
    batch order, and the text holds each of them.

    When the span can't be resolved (an end before the start, or an end or a
    row missing from the batch), the entry shrinks to its start row, so the
    span stays honest and #420 recovery emits the rest. The keys must be the
    index text the model wrote (``delimiter_index_text``): a float-parsed
    "80.10" is "80.1", row 1.

    Returns (None, []) when the start row is not in the batch.
    """
    if start_key not in batch_elem_lookup:
        return None, []
    keys = [start_key]
    start_parent, _, start_row = start_key.partition(".")
    end_parent, _, end_row = end_key.partition(".")
    if (end_key != start_key and start_parent == end_parent
            and start_row.isdigit() and end_row.isdigit()
            and int(end_row) > int(start_row)):
        span = [f"{start_parent}.{r}" for r in range(int(start_row), int(end_row) + 1)]
        if all(k in batch_elem_lookup for k in span):
            keys = span
    elif start_parent != end_parent and end_key in batch_elem_lookup:
        order = list(batch_elem_lookup)  # the batch's own element order
        start, end = order.index(start_key), order.index(end_key)
        if end > start:
            keys = order[start:end + 1]
    elem = batch_elem_lookup[start_key]
    texts = [str(batch_elem_lookup[k].get("full_text", batch_elem_lookup[k].get("text", "")))
             for k in keys]
    entry = {
        "element_idx_start": start_key,
        "element_idx_end": keys[-1],
        "element_type": "table_row",
        "confidence": confidence,
        "text": "\n".join(t for t in texts if t.strip()),
        "table_index": elem.get("table_index"),
        "row_index": elem.get("row_index"),
        "parent_idx": elem.get("parent_idx"),
    }
    return entry, keys


def recover_unclaimed_table_rows(batch_elements: list, claimed_row_keys: set) -> list:
    """Return entries for table rows no delimiter claimed (#420).

    The model returns sub-row indices as JSON NUMBERS, so a row index with a
    trailing zero used to collapse: "114.10" parsed to the float 114.1, ``str()``
    rendered it back as "114.1", and the lookup landed on row 1 -- row 10 was
    unreachable. C0ZGFW element 114 lost rows 10/20/30/40/50 exactly this way,
    and element 109 lost row 10. ``parse_delimiter_reply`` now keeps the index
    text (#1228), so this recovers only rows the model simply omitted, which
    disappear identically. Either way the content survived only inside whatever
    whole-table entry the model happened to emit, which is what made those
    blobs load-bearing (and what made #227's dedup drop real content loss).

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
    int 30, float 30.0, str "30.0", sub-row "22.2", pseudo-row "22.2.1" (one
    float per dotted part, so "22.2.10" is not "22.2.1"), and "table_3".
    """
    if isinstance(idx, str):
        if idx.startswith("table_"):
            try:
                return (1000000 + int(idx.split("_")[1]), 0.0)
            except (ValueError, IndexError):
                return (str(idx), 0.0)
        if "." in idx:
            try:
                return tuple(float(part) for part in idx.split("."))
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


# Record-aligned batch cut (#1226). Each stage-2 batch is a separate LLM call
# with no context from the one before, so a record cut by a fixed batch edge
# reaches the model as a tail with no head and every later entry shifts. A full
# batch therefore ends at a record edge when one sits in its last
# BATCH_CUT_LOOKBACK elements: a blank line first, else the last line that
# opens with the batch's first line's label ("Agency:"). Otherwise the hard
# cut at STAGE2_BATCH_SIZE stands, so a batch with no such edge is unchanged.
STAGE2_BATCH_SIZE = 50
BATCH_CUT_LOOKBACK = 15
# A short leading "Label:" -- letters first, at most 40 characters before the colon.
_LEADING_LABEL_RE = re.compile(r"\s*([A-Za-z][A-Za-z .&/()'-]{0,40}?)\s*:")


def _leading_label(elem: dict) -> str | None:
    """The lower-cased leading ``Label:`` of a paragraph or table row, else None."""
    if elem.get("type") not in ("paragraph", "table_row"):
        return None
    match = _LEADING_LABEL_RE.match(elem.get("text", ""))
    return match.group(1).lower() if match else None


def _record_aligned_end(elements: list[dict], start: int, batch_size: int) -> int:
    """Exclusive end of the batch that starts at ``elements[start]``."""
    hard_end = start + batch_size
    if hard_end >= len(elements):
        return len(elements)
    if elements[hard_end - 1].get("type") == "empty" or elements[hard_end].get("type") == "empty":
        return hard_end
    # The batch's last BATCH_CUT_LOOKBACK elements, newest first: indices
    # hard_end - BATCH_CUT_LOOKBACK .. hard_end - 1, never before start.
    window = range(hard_end - 1, max(start, hard_end - BATCH_CUT_LOOKBACK) - 1, -1)
    for idx in window:
        if elements[idx].get("type") == "empty":
            return idx + 1  # the batch ends on the blank
    label = _leading_label(elements[start])
    if label is None or _leading_label(elements[hard_end]) == label:
        return hard_end
    for idx in window:
        if idx > start and _leading_label(elements[idx]) == label:
            return idx  # the next batch opens on the label; this one is never empty
    return hard_end


def make_record_aligned_batches(elements: list[dict], batch_size: int = STAGE2_BATCH_SIZE) -> list[list[dict]]:
    """Split a section's elements into batches of at most ``batch_size`` whose
    edges fall between records where the layout shows one (#1226)."""
    if batch_size < 1:
        raise ValueError(f"make_record_aligned_batches: batch_size must be >= 1, got {batch_size}")
    batches = []
    start = 0
    while start < len(elements):
        end = _record_aligned_end(elements, start, batch_size)
        batches.append(elements[start:end])
        start = end
    return batches


# Carry the last record across a batch edge (#1226). The cut above still lands
# inside a record where the layout shows no edge, and a batch that opens on a
# record's tail is grouped one line off to its end. So once a batch's reply is
# in, the next batch re-opens on the latest-starting entry the model returned:
# a line the model itself chose as a record head, with that record's whole text
# in view. That entry, and anything after it in the batch, is held back and
# replaced by the next batch's reply. No carry when it would make no progress
# (one entry spans the batch), when the batch has no model entry, or when the
# carry is longer than STAGE2_CARRY_MAX -- the same horizon as the cut's own
# lookback; the next batch then opens at this batch's end, as before.
STAGE2_CARRY_MAX = BATCH_CUT_LOOKBACK
CARRY_SKIP_NO_ENTRY = "no_entry"
CARRY_SKIP_NO_PROGRESS = "no_progress"
CARRY_SKIP_OVER_MAX = "over_max"


@dataclass
class BatchCarryCounts:
    """What happened at a section's batch edges (#1226): written to the stage-2
    artifact so a skipped carry, a failed batch and a restored tail are each
    visible (§5.3). ``edges`` is always ``carried`` plus the three skip counts.

    ``failed_batches`` counts every batch whose call or reply failed, the last
    batch and a single-batch section's included. ``restored_after_failure`` and
    ``restored_unclaimed`` count held-back entries put back because the batch
    that re-listed them failed, or replied without claiming any of their lines.
    ``gap_filled_entries`` and ``gap_filled_attached`` count the content lines
    a batch's reply left unclaimed, put back as their own entries or appended
    to the entry they continue (``_gap_fill``).
    """
    edges: int = 0
    carried: int = 0
    skipped_no_entry: int = 0
    skipped_no_progress: int = 0
    skipped_over_max: int = 0
    failed_batches: int = 0
    restored_after_failure: int = 0
    restored_unclaimed: int = 0
    gap_filled_entries: int = 0
    gap_filled_attached: int = 0

    def any(self) -> bool:
        """True when there is anything to report: an edge, a failed batch or a
        gap-filled line."""
        return bool(self.edges or self.failed_batches or self.gap_filled_entries or self.gap_filled_attached)

    def record(self, skip_reason: str | None) -> None:
        self.edges += 1
        if skip_reason is None:
            self.carried += 1
        elif skip_reason == CARRY_SKIP_NO_ENTRY:
            self.skipped_no_entry += 1
        elif skip_reason == CARRY_SKIP_NO_PROGRESS:
            self.skipped_no_progress += 1
        elif skip_reason == CARRY_SKIP_OVER_MAX:
            self.skipped_over_max += 1
        else:
            raise ValueError(f"BatchCarryCounts.record: unknown skip reason {skip_reason!r}")

    def add(self, other: BatchCarryCounts) -> None:
        self.edges += other.edges
        self.carried += other.carried
        self.skipped_no_entry += other.skipped_no_entry
        self.skipped_no_progress += other.skipped_no_progress
        self.skipped_over_max += other.skipped_over_max
        self.failed_batches += other.failed_batches
        self.restored_after_failure += other.restored_after_failure
        self.restored_unclaimed += other.restored_unclaimed
        self.gap_filled_entries += other.gap_filled_entries
        self.gap_filled_attached += other.gap_filled_attached


class _CarrySplit(NamedTuple):
    kept: list[dict]      # entries that stand: they start before the carry
    pending: list[dict]   # entries the next batch's reply replaces
    carry: int            # batch position the next batch opens on; len(batch) for no carry
    skip_reason: str | None


def _carry_split(batch_entries: list[dict], batch_elements: list[dict], carry_max: int) -> _CarrySplit:
    """Split one batch's entries at the record the next batch re-opens on.

    The carry is the batch position of the latest-starting model-attested
    entry. #420-recovered rows never set it -- the model did not choose them --
    but those at or after it are held back with the rest. Row keys compare as
    the text the model wrote (``delimiter_index_text``), so ``474.10`` stays
    row 10. An entry whose start is not in this batch is kept and never sets it.
    """
    positions = {str(elem["idx"]): pos for pos, elem in enumerate(batch_elements)}

    def position(entry: dict) -> int | None:
        return positions.get(delimiter_index_text(entry["element_idx_start"]))

    heads = [pos for entry in batch_entries
             if not entry.get("recovered_row") and (pos := position(entry)) is not None]
    no_carry = len(batch_elements)
    if not heads:
        return _CarrySplit(batch_entries, [], no_carry, CARRY_SKIP_NO_ENTRY)
    carry = max(heads)
    if carry == 0:
        return _CarrySplit(batch_entries, [], no_carry, CARRY_SKIP_NO_PROGRESS)
    if len(batch_elements) - carry > carry_max:
        return _CarrySplit(batch_entries, [], no_carry, CARRY_SKIP_OVER_MAX)
    kept, pending = [], []
    for entry in batch_entries:
        pos = position(entry)
        (pending if pos is not None and pos >= carry else kept).append(entry)
    return _CarrySplit(kept, pending, carry, None)


def _entry_keys(entry: dict, batch_elements: list[dict]) -> set[str]:
    """The element keys one entry covers: its paragraph range, or the batch
    rows from its start key to its end key. A reversed range covers nothing."""
    start, end = entry["element_idx_start"], entry["element_idx_end"]
    if isinstance(start, int) and isinstance(end, int):
        return {str(idx) for idx in range(start, end + 1)}
    keys = [str(elem["idx"]) for elem in batch_elements]
    start_text, end_text = delimiter_index_text(start), delimiter_index_text(end)
    if start_text not in keys:
        return {start_text}
    first = keys.index(start_text)
    last = keys.index(end_text) if end_text in keys else first
    return set(keys[first:max(first, last) + 1])


def _unclaimed_pending(pending: list[dict], batch_entries: list[dict],
                       batch_elements: list[dict]) -> tuple[list[dict], list[dict]]:
    """(the held-back entries this batch's reply left alone, the batch's
    entries less any #420-recovered row those entries already hold).

    The next batch re-lists the held-back tail so its reply can replace it.
    A reply that claims none of a held-back entry's lines -- an empty ``[]``,
    or one that opens past the carried lines -- has replaced nothing, so that
    entry stands. Only model-attested entries claim; a row #420 recovered in
    this batch is the same row the restored entry holds, and is dropped.
    """
    claimed = set().union(*(_entry_keys(e, batch_elements) for e in batch_entries if not e.get("recovered_row")))
    restored = [e for e in pending if not _entry_keys(e, batch_elements) & claimed]
    held = set().union(*(_entry_keys(e, batch_elements) for e in restored))
    kept = [e for e in batch_entries
            if not (e.get("recovered_row") and _entry_keys(e, batch_elements) <= held)]
    return restored, kept


# Gap-fill (#1226, generalised for unclaimed lines). The model is shown every
# line of a batch and may leave real records unclaimed: on the 83 stored
# production runs 61 record lines in 11 runs fell to break that way -- whole
# employment histories and appointment lists, and one CV's two independent
# replies dropped the same 8 appointments. On a carried batch the reply may
# open well past the record it re-opens on (LFFKVV: 32 evaluation lines). So
# once a batch's reply is in, each unclaimed record line the batch settles is
# put back as its own one-line entry, or appended to the entry right above it
# when it visibly continues that entry (``_attaches_to``). A batch settles its
# lines up to its carry; the carried tail is settled by the next batch, or by
# this one's reply when the next batch fails. Only paragraphs (#420 recovers
# table rows). A line that is a heading 1b could not place stays break, for
# promote_unlocated_headings. run_stage_2's filter_extraction_noise and
# template-instruction filter then read these entries as they read every other.
#
# Most of what a reply leaves unclaimed is not a record: of the 548
# text-bearing lines the stored replies left unclaimed on 102 runs, 83 are.
# The rest is page furniture, the CV's own title, "(continued)" headings,
# legends, "Last updated" stamps, column-header rows, names and sub-headings.
# So a line is a record only when it is none of that noise (``_is_gap_noise``)
# and is long, or holds a number (a date, a score) and is not short
# (``_gap_line_is_content``). A line joins the entry above it only when it is
# none of that noise either.
GAP_FILL_CONFIDENCE = 0.5  # not model-attested, like a #420-recovered row
GAP_FILL_REPEAT_MAX_WORDS = 6  # a repeated line this short is a running header or footer
GAP_FILL_REPEAT_MIN_SPACING = 8  # a running line recurs once a page, not within a few lines
GAP_FILL_MIN_WORDS = 8  # a record line with no number has at least this many words
GAP_FILL_MIN_NUMBERED_WORDS = 5  # one with a date or a score ("3/2020 Example State Clinical Practice Permit")
_LETTER_RE = re.compile(r"[^\W\d_]")
_PAGE_NUMBER_RE = re.compile(r"^\W*\d+(?:\s*(?:of|/)\s*\d+)?\W*$|\bpage\s*\d+", re.IGNORECASE)
# Word field codes left in running headers and footers, and the CV's own title.
_FURNITURE_RE = re.compile(r"MERGEFORMAT|NUMPAGES|HYPERLINK|\bPAGE\b|DATE\s*\\@|(?i:\bcurriculum\s+vita)")
# "*Denotes a trainee author", "# denotes equal contribution", "KEY: † indicates ..."
_LEGEND_RE = re.compile(r"^(?:key:\s*)?\S{0,3}\s*(?:denotes?|designates?|indicates?|marks?)\b", re.IGNORECASE)
# "Last updated 1/9/2031", "Date Prepared: 7/2011", "Current h-index: 12"
_CV_METADATA_RE = re.compile(
    r"^\W*(?:(?:last\s+)?(?:updated|revised|date\s+prepared)\b|rev\.)|\bh-index\b|\bi-?10[- ]index\b|\bcitations\s*=",
    re.IGNORECASE)
_COLUMN_HEADER_PUNCTUATION = frozenset(",.;:()")
# Words that name a column in a header row. A capitalised line naming fewer
# than COLUMN_HEADER_MIN_FIELDS of them is a name ("Example Regional Hospital").
_COLUMN_FIELD_WORDS = frozenset({
    "activity", "awarded", "course", "date", "dates", "degree", "dissertation", "enrollment", "granted",
    "inclusive", "institution", "location", "name", "organisation", "organization", "position", "rank",
    "role", "thesis", "title", "year", "years"})
COLUMN_HEADER_MIN_FIELDS = 2
_WORD_RE = re.compile(r"[^\W\d_]+")
_LINE_END_PUNCTUATION = (".", "!", "?", ";", ":")


def _normalised(text: str) -> str:
    return " ".join(text.split())


def _is_furniture(text: str) -> bool:
    """A page number or field code, the CV's title, a legend, a CV stamp, or
    a heading that ends in a colon: never a record, nor part of one."""
    return bool(_PAGE_NUMBER_RE.search(text) or _FURNITURE_RE.search(text) or text.endswith(":")
                or _LEGEND_RE.match(text) or _CV_METADATA_RE.search(text))


def _is_column_header(text: str) -> bool:
    """``Employer Role/Title Start Dates`` -- every word capitalised,
    with no digit and no punctuation a record would carry, naming at least
    two columns."""
    fields = sum(word.lower() in _COLUMN_FIELD_WORDS for word in _WORD_RE.findall(text))
    return (fields >= COLUMN_HEADER_MIN_FIELDS
            and not any(ch.isdigit() or ch in _COLUMN_HEADER_PUNCTUATION for ch in text)
            and all(not word[:1].islower() for word in text.split()))


def _is_caps_heading(text: str) -> bool:
    """``PUBLICATIONS`` -- all capitals and no digit: a heading, not a record."""
    return text.isupper() and not any(ch.isdigit() for ch in text)


def _is_gap_noise(text: str, repeated: set[str]) -> bool:
    """Furniture, a column-header row, an all-caps heading, or a short line
    repeated in the section (a running header or footer): never a record,
    nor part of one. ``text`` is normalised."""
    return (_is_furniture(text) or _is_column_header(text) or _is_caps_heading(text)
            or (len(text.split()) <= GAP_FILL_REPEAT_MAX_WORDS and text in repeated))


def _gap_line_is_content(line: str, repeated: set[str]) -> bool:
    """Whether an unclaimed paragraph reads as a record line."""
    text = _normalised(line)
    words = len(text.split())
    if not _LETTER_RE.search(text) or _is_gap_noise(text, repeated):
        return False
    if any(ch.isdigit() for ch in text):
        return words >= GAP_FILL_MIN_NUMBERED_WORDS
    return words >= GAP_FILL_MIN_WORDS


def _repeated_lines(section_elements: list[dict]) -> set[str]:
    """Paragraph texts that occur more than once in the section, each time at
    least GAP_FILL_REPEAT_MIN_SPACING elements after the last: a running
    header or footer, which recurs once a page. A line that recurs sooner is
    a field of consecutive records -- the same city under two posts."""
    positions = collections.defaultdict(list)
    for pos, elem in enumerate(section_elements):
        if elem.get("type") == "paragraph":
            positions[_normalised(elem["text"])].append(pos)
    return {text for text, at in positions.items()
            if len(at) > 1 and all(b - a >= GAP_FILL_REPEAT_MIN_SPACING for a, b in itertools.pairwise(at))}


def _continues_line(entry_text: str, line: str) -> bool:
    """``line`` runs on from the entry's last line: that line ends without
    closing punctuation and ``line`` opens lower-case."""
    last = entry_text.split("\t")[-1].rstrip()
    return bool(last) and not last.endswith(_LINE_END_PUNCTUATION) and line[:1].islower()


def _attaches_to(above: dict, line: str, repeated: set[str]) -> bool:
    """``line`` belongs to the entry right above it: it runs on from that
    entry's last line, or it completes a gap-filled record -- an undated line
    under a dated one ("Example Regional Hospital" under "March 2004 - June
    2004 Staff Technologist"), or a bare date under an undated one. Noise
    never attaches (``_is_gap_noise``), and a line that is a record on its own
    (``_gap_line_is_content``) is its own entry, not part of the one above."""
    text = _normalised(line)
    if _is_gap_noise(text, repeated):
        return False
    if _continues_line(above["text"], line):
        return True
    if not above.get("gap_filled") or _gap_line_is_content(text, repeated):
        return False
    if _YEAR_RE.search(above["text"]):
        return _YEAR_RE.search(text) is None and _LETTER_RE.search(text) is not None
    return BARE_DATE_RE.match(text) is not None


class _GapFillContext(NamedTuple):
    """What every batch of one section's gap-fill reads."""
    repeated: set[str]  # the section's repeated lines (_repeated_lines)
    unlocated_headings: tuple[str, ...]  # headings 1b could not place: never filled
    end: int  # the first index past the section: the model is shown it, but it is not this section's


def _gap_fill(entries: list[dict], batch_elements: list[dict], settled: int,
              context: _GapFillContext, counts: BatchCarryCounts) -> list[dict]:
    """``entries`` plus each content line in ``batch_elements[:settled]`` that
    none of them claims, appended to the entry it continues or made its own
    entry. ``entries`` is every entry the section holds so far. Counted in
    ``counts``."""
    claimed = set().union(*(_entry_keys(e, batch_elements) for e in entries))
    out = list(entries)
    by_end = {e["element_idx_end"]: i for i, e in enumerate(out) if isinstance(e["element_idx_end"], int)}
    for pos in range(settled):
        elem = batch_elements[pos]
        if (elem.get("type") != "paragraph" or str(elem["idx"]) in claimed or elem["idx"] >= context.end
                or any(_line_is_heading(elem["text"], heading) for heading in context.unlocated_headings)):
            continue
        idx, text = elem["idx"], elem["text"]  # stripped by _section_elements
        above = by_end.pop(idx - 1, None)
        if above is not None and _attaches_to(out[above], text, context.repeated):
            out[above] = {**out[above], "element_idx_end": idx, "text": f"{out[above]['text']}\t{text}"}
            by_end[idx] = above
            counts.gap_filled_attached += 1
            continue
        if not _gap_line_is_content(text, context.repeated):
            continue
        out.append({"element_idx_start": idx, "element_idx_end": idx, "element_type": "paragraph",
                    "confidence": GAP_FILL_CONFIDENCE, "text": text, "gap_filled": True})
        by_end[idx] = len(out) - 1
        counts.gap_filled_entries += 1
    return out


def _table_row_elements(idx: int, rows: Iterable[tuple[int, object]], table_index: object) -> list[dict]:
    """The section elements for one table's rows: a ``table_row`` per row, or
    per pseudo-row where a row holds several merged entries."""
    elements = []
    for row_idx, row in rows:
        # Check if this row contains merged entries that should be split
        if isinstance(row, list):
            pseudo_rows = split_merged_row_into_pseudo_rows(row)
            if pseudo_rows:
                # Row contains multiple merged entries - create pseudo-rows
                for pseudo_idx, pseudo_row in enumerate(pseudo_rows):
                    row_text = join_row_cells(pseudo_row)
                    if row_text:
                        elements.append({
                            "idx": f"{idx}.{row_idx}.{pseudo_idx}",  # Sub-sub-index
                            "text": row_text[:300] + ("..." if len(row_text) > 300 else ""),
                            "type": "table_row",
                            "full_text": row_text,
                            "table_index": table_index,
                            "row_index": row_idx,
                            "pseudo_row_index": pseudo_idx,
                            "parent_idx": idx
                        })
                continue
            # Normal row - flatten cells to text
            row_text = join_row_cells(row_cell_texts(row))
        elif isinstance(row, dict):
            row_text = row.get("text", "").strip()
        else:
            row_text = str(row).strip()
        if row_text:
            elements.append({
                "idx": f"{idx}.{row_idx}",  # Sub-index for rows
                "text": row_text[:300] + ("..." if len(row_text) > 300 else ""),
                "type": "table_row",
                "full_text": row_text,
                "table_index": table_index,
                "row_index": row_idx,
                "parent_idx": idx
            })
    return elements


def _section_elements(doc_elements: list[dict], start_elem_idx: int, end_elem_idx: int,
                      header_indices: set) -> list[dict]:
    """The elements stage 2 shows the model for one section, headers excluded:
    paragraphs, blank-line markers, table rows, and whole tables."""
    section_elements = []
    for elem in doc_elements:
        # Use unified_idx (from extract_unified_elements) or fall back to idx
        idx = elem.get("unified_idx", elem.get("idx"))
        elem_type = elem.get("type", "")

        # Skip elements outside our section range; a non-integer index
        # shouldn't happen with unified elements
        if not isinstance(idx, int) or idx < start_elem_idx or idx > end_elem_idx:
            continue
        if idx in header_indices:
            continue

        # Handle different element types from extract_unified_elements
        if elem_type == "paragraph":
            text = elem.get("text", "").strip()
            if text:
                section_elements.append({"idx": idx, "text": text, "type": "paragraph"})
        elif elem_type == "empty":
            # Include empty paragraphs as break markers - these help the LLM
            # identify natural entry boundaries (blank lines between entries)
            section_elements.append({"idx": idx, "text": "", "type": "empty"})
        elif elem_type == "table_header":
            # Table headers are section markers - usually skip as they're in header_indices
            # But include if not marked as header (rare case)
            text = elem.get("text", "").strip()
            if text:
                section_elements.append({"idx": idx, "text": text, "type": "table_header"})
        elif elem_type == "table_content":
            # Table content contains the actual data rows; break the table into
            # individual rows for row-level processing
            table_data = elem.get("data", [])  # List of row data
            table_index = elem.get("table_index")
            if isinstance(table_data, list) and table_data:
                section_elements.extend(_table_row_elements(idx, enumerate(table_data), table_index))
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
            table_idx = elem.get("table_index", 0)
            table_data = elem.get("data", [])
            if isinstance(table_data, list) and len(table_data) > 1:
                # Multi-row table: break into individual rows so the LLM can
                # identify individual entries (grants, publications, etc.)
                header_rows = _header_row_count(elem)  # #424: never an entry
                rows = enumerate(table_data[header_rows:], start=header_rows)
                section_elements.extend(_table_row_elements(idx, rows, table_idx))
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
    return section_elements


def _batch_element_line(elem: dict) -> str:
    """One element's line in the prompt's raw-data list."""
    idx = elem['idx']
    text = elem['text']
    elem_type = elem.get('type', 'paragraph')
    if elem_type == 'table_row':
        # Individual table row - show as ROW
        return f"[{idx}] (ROW) {text[:200]}{'...' if len(text) > 200 else ''}"
    if elem_type == 'table_content':
        # Table content - show as table with row count
        # 'rows' may be an integer count or a list; handle both
        rows_data = elem.get('rows', 0)
        row_count = len(rows_data) if isinstance(rows_data, list) else (rows_data if isinstance(rows_data, int) else 0)
        return f"[{idx}] (TABLE {row_count} rows) {text[:200]}{'...' if len(text) > 200 else ''}"
    if elem_type == 'table':
        # Legacy table format
        rows = elem.get('rows', 0)
        cols = elem.get('cols', 0)
        return f"[{idx}] (TABLE {rows}x{cols}) {text[:200]}{'...' if len(text) > 200 else ''}"
    if elem_type == 'table_header':
        return f"[{idx}] (HEADER) {text[:150]}{'...' if len(text) > 150 else ''}"
    if elem_type == 'empty':
        # Empty paragraph - show as blank line marker to help LLM identify entry boundaries
        return f"[{idx}] (BLANK LINE)"
    # paragraph
    return f"[{idx}] {text[:150]}{'...' if len(text) > 150 else ''}"


def _batch_messages(batch_elements: list[dict], full_hierarchy: str) -> list[dict]:
    """The system and user messages for one batch's call."""
    paragraph_list = "\n".join(_batch_element_line(elem) for elem in batch_elements)

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

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ]


def _reply_delimiters(result: list | dict) -> list:
    """The delimiter list in a parsed reply -- the LLM may return it under
    different keys, or as a bare list."""
    if isinstance(result, dict):
        return (
            result.get("delimiters") or
            result.get("entries") or
            result.get("result") or
            result.get("data") or
            []
        )
    return result


def _validated_entries(
    delimiters: list,
    batch_elements: list[dict],
    start_elem_idx: int,
    end_elem_idx: int,
    element_index_map: dict,
) -> tuple[list[dict], set[str]]:
    """The entries a batch's delimiters name, with their full text, and the
    table-row keys they claim. A delimiter that names nothing in the batch or
    the section is skipped."""
    entries = []
    # Build a lookup for batch elements by index (use string keys for consistency)
    batch_elem_lookup = {str(elem['idx']): elem for elem in batch_elements}
    claimed_row_keys = set()

    for delim in delimiters:
        if "element_idx_start" not in delim or "element_idx_end" not in delim:
            continue
        start_idx = delim["element_idx_start"]
        end_idx = delim["element_idx_end"]

        # Handle table indices (strings like "table_0")
        if isinstance(start_idx, str) and start_idx.startswith("table_"):
            # Table entry - get full text from batch elements
            if start_idx in batch_elem_lookup:
                elem = batch_elem_lookup[start_idx]
                full_text = elem.get("full_text", elem.get("text", ""))
                entries.append({
                    "element_idx_start": start_idx,
                    "element_idx_end": end_idx,
                    "element_type": "table",
                    "confidence": delim.get("confidence", 1.0),
                    "text": full_text
                })
            continue

        # Handle row sub-indices (floats like 22.2 or strings like "22.2")
        # These come from table_content rows with format "parent_idx.row_idx"
        start_idx_str = delimiter_index_text(start_idx)
        end_idx_str = delimiter_index_text(end_idx)
        if "." in start_idx_str or "." in end_idx_str:
            # Row sub-index - look up in batch elements by string key
            entry, row_keys = row_range_entry(
                start_idx_str, end_idx_str, batch_elem_lookup,
                delim.get("confidence", 1.0))
            if entry is not None:
                entries.append(entry)
                claimed_row_keys.update(row_keys)
            continue

        # Handle paragraph indices (integers), within the section's bounds
        if not isinstance(start_idx, int) or not isinstance(end_idx, int):
            continue
        if start_idx >= start_elem_idx and end_idx <= end_elem_idx:
            entries.append({
                "element_idx_start": start_idx,
                "element_idx_end": end_idx,
                "element_type": delim.get("element_type", "paragraph"),
                "confidence": delim.get("confidence", 1.0),
                "text": _paragraph_span_text(start_idx, end_idx, element_index_map),
            })
    return entries, claimed_row_keys


def _paragraph_span_text(start_idx: int, end_idx: int, element_index_map: dict) -> str:
    """The non-empty texts of the elements ``start_idx``..``end_idx``
    (inclusive), tab-separated for compact format."""
    texts = (get_element_text(element_index_map[idx])
             for idx in range(start_idx, end_idx + 1) if idx in element_index_map)
    return "\t".join(text for text in texts if text)


def _starts_inside(entry: dict, prev: dict) -> bool:
    """Both are paragraph spans and ``entry`` starts on or before ``prev``'s
    last line. An int start means a paragraph span: a table row's or a whole
    table's start is a string, and an int start always pairs with an int end."""
    paragraphs = isinstance(prev["element_idx_start"], int) and isinstance(entry["element_idx_start"], int)
    return paragraphs and entry["element_idx_start"] <= prev["element_idx_end"]


def merge_overlapping_spans(entries: list[dict], element_index_map: dict) -> list[dict]:
    """``entries`` (sorted by start, as remove_subset_delimiters returns them)
    with each paragraph span that starts inside the one before it merged into
    that one: the earlier start, the later end, the text of the whole range.

    Over a flattened table (a column of dates, then a column of roles) the
    model can return sliding windows -- 598-606, 600-608, 602-610, each one
    two lines later (#1433). None contains another, so remove_subset_delimiters
    keeps all three: the shared lines then render in two or three records and
    the dates pair with the wrong roles. The merged entry holds each line
    once; stage 4 splits it into its records."""
    merged: list[dict] = []
    for entry in entries:
        if merged and _starts_inside(entry, merged[-1]):
            prev, end = merged[-1], entry["element_idx_end"]  # no subsets left: ends only grow
            merged[-1] = {**prev, "element_idx_end": end,
                          "text": _paragraph_span_text(prev["element_idx_start"], end, element_index_map)}
        else:
            merged.append(entry)
    return merged


@dataclass
class _BatchUsage:
    """A section's summed call cost and tokens. A call is counted as soon as
    it returns, so a reply that then fails to parse or validate is still
    counted -- it was paid for (§5.4)."""
    cost: float = 0.0
    total_tokens: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def add(self, llm_result: dict) -> None:
        self.cost += llm_result["cost"]
        self.total_tokens += llm_result["total_tokens"]
        self.prompt_tokens += llm_result["prompt_tokens"]
        self.completion_tokens += llm_result["completion_tokens"]


def _run_batch(
    batch_elements: list[dict],
    full_hierarchy: str,
    batch_no: int,
    start_elem_idx: int,
    end_elem_idx: int,
    element_index_map: dict,
    usage: _BatchUsage,
) -> list[dict]:
    """One batch's validated entries: the model-attested ones, then any
    #420-recovered rows. Raises on any failure; the call's cost is added to
    ``usage`` once it returns, before the reply is read."""
    llm_result = call_llm(
        stage="stage_2",
        messages=_batch_messages(batch_elements, full_hierarchy),
        response_format={"type": "json_object"},
        temperature=0.1
    )
    usage.add(llm_result)
    result = parse_delimiter_reply(llm_result["content"])
    entries, claimed_row_keys = _validated_entries(
        unreverse_paragraph_spans(_reply_delimiters(result), f"batch {batch_no} [{full_hierarchy}]"),
        batch_elements, start_elem_idx, end_elem_idx, element_index_map)

    # Backstop: emit any table row this batch never claimed (#420).
    #
    # Why rows go unclaimed: recover_unclaimed_table_rows' docstring (#420).
    # Recovering the rows here is deterministic and costs no extra LLM
    # call. It also runs BEFORE remove_subset_delimiters, so a whole-table
    # blob whose rows are now all present becomes a provable duplicate and
    # is collapsed by the #418 coverage check.
    #
    # The indices no longer round trip through floats (#1228): this loop
    # only recovers rows the model genuinely skipped. A row key that is
    # not exactly the text the model wrote would bring the old collapse
    # back, which is why delimiter_index_text exists.
    recovered = recover_unclaimed_table_rows(batch_elements, claimed_row_keys)
    if recovered:
        logger.info(f"    Recovered {len(recovered)} unclaimed table row(s) "
                    f"in batch {batch_no} [{full_hierarchy}]")
    return entries + recovered


def _detect_in_batches(
    section_elements: list[dict],
    full_hierarchy: str,
    start_elem_idx: int,
    end_elem_idx: int,
    element_index_map: dict,
    unlocated_headings: tuple[str, ...] = (),
) -> tuple[list[dict], dict]:
    """A section's entries, one call per batch, each batch after the first
    re-opening on the record the batch before it may have cut (#1226).

    A batch whose call fails is skipped, as before. The entries it would have
    replaced are restored -- so are any its reply leaves unclaimed: a carried
    batch never loses what the batch before it produced. Each reply's unclaimed
    content lines are gap-filled (``_gap_fill``). ``LLMOutageError`` still
    fails the section.
    """
    entries = []
    pending = []  # the previous batch's tail, which this batch's reply replaces
    usage = _BatchUsage()
    carry_counts = BatchCarryCounts()
    multi_batch = len(section_elements) > STAGE2_BATCH_SIZE
    gap_context = _GapFillContext(_repeated_lines(section_elements), unlocated_headings, end_elem_idx)
    start = 0
    batch_no = 0
    unsettled = 0  # this batch's leading lines, carried: the previous reply saw them

    while start < len(section_elements):
        end = _record_aligned_end(section_elements, start, STAGE2_BATCH_SIZE)
        batch_elements = section_elements[start:end]
        batch_no += 1
        if multi_batch:
            logger.info(f"    Processing batch {batch_no} (elements {start + 1}-{end} of {len(section_elements)})")

        try:
            batch_entries = _run_batch(batch_elements, full_hierarchy, batch_no,
                                       start_elem_idx, end_elem_idx, element_index_map, usage)
        except LLMOutageError:  # provider down past the outage budget (#810): fail the run, don't degrade
            raise
        except Exception as e:
            # Not full_hierarchy: it is the CV's own header text (§4.7, #639).
            logger.warning("    ⚠ Error in batch %d: %s", batch_no, redacted_error_label(e))
            logger.debug("    ⚠ Error in batch %d: %s", batch_no, e)
            carry_counts.failed_batches += 1
            carry_counts.restored_after_failure += len(pending)
            entries.extend(pending)  # nothing replaced the carried tail: it stands
            batch_entries = []
            settled = unsettled  # only the previous reply saw these lines
        else:
            restored, batch_entries = _unclaimed_pending(pending, batch_entries, batch_elements)
            carry_counts.restored_unclaimed += len(restored)
            entries.extend(restored)
            settled = len(batch_elements)
        pending = []

        if end >= len(section_elements):
            entries.extend(batch_entries)
            entries = _gap_fill(entries, batch_elements, settled, gap_context, carry_counts)
            break
        split = _carry_split(batch_entries, batch_elements, STAGE2_CARRY_MAX)
        carry_counts.record(split.skip_reason)
        entries.extend(split.kept)
        entries = _gap_fill(entries, batch_elements, min(settled, split.carry), gap_context, carry_counts)
        pending = split.pending
        unsettled = len(batch_elements) - split.carry
        start += split.carry

    cost_info = {
        "cost": usage.cost,
        "tokens": usage.total_tokens,
        "prompt_tokens": usage.prompt_tokens,
        "completion_tokens": usage.completion_tokens,
    }
    if carry_counts.any():
        cost_info["batch_carry"] = carry_counts
    return entries, cost_info


def detect_entries_for_section(
    section_hierarchy: list[str],
    doc_elements: list[dict],
    start_elem_idx: int,
    end_elem_idx: int,
    document_uid: str | None = None,
    header_indices: set | None = None,
    element_index_map: dict | None = None,
    unlocated_headings: tuple[str, ...] = (),
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
        unlocated_headings: Headings stage 1b kept but could not place; a line
            that is one is never gap-filled, so promote_unlocated_headings
            still finds it as a break

    Returns:
        Tuple of (entries_list, cost_info_dict). The cost_info of a
        multi-batch section, or of one with a failed batch, also carries
        ``batch_carry``, its BatchCarryCounts.
    """
    if header_indices is None:
        header_indices = set()
    if element_index_map is None:
        element_index_map = {}

    section_elements = _section_elements(doc_elements, start_elem_idx, end_elem_idx, header_indices)
    if not section_elements:
        return [], {"cost": 0, "tokens": 0}

    full_hierarchy = " > ".join(section_hierarchy)
    entries, cost_info = _detect_in_batches(
        section_elements, full_hierarchy, start_elem_idx, end_elem_idx, element_index_map, unlocated_headings)

    # Remove subset/duplicate entries from all batches, then merge the
    # overlapping windows that survive it (#1433)
    return merge_overlapping_spans(remove_subset_delimiters(entries), element_index_map), cost_info


# Detail-line fold (#986). A dateless line that opens with one of these labels
# belongs to the entry above it: left alone, the detector or the unassigned-line
# fallback makes it a sibling entry that 3b codes and routes away from its
# parent. Compared lower-cased against the line's start. Indent is deliberately
# not a trigger: adjacent siblings (a student list, a course list) share an
# indent, so an indent rule fused separate records on the corpus.
DETAIL_LINE_LABELS = ("project:", "role:")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_FOLDABLE_ELEMENT_TYPES = ("paragraph", "break")


def _is_foldable_paragraph(entry: dict, element_index_map: dict) -> bool:
    """A non-empty paragraph/break entry whose start element is a plain paragraph
    (never a table row or header) -- a boundary the fold must not cross."""
    return (
        entry.get("element_type") in _FOLDABLE_ELEMENT_TYPES
        and isinstance(entry.get("element_idx_end"), int)
        and bool(entry.get("text"))
        and element_index_map.get(entry.get("element_idx_start"), {}).get("type") == "paragraph"
    )


def _is_adjacent_dateless_line(prev: dict, cur: dict, element_index_map: dict) -> bool:
    """``cur`` is a single line with no year of its own, on the line right after
    ``prev`` (no blank line or header between) in the same section."""
    return (
        _is_foldable_paragraph(prev, element_index_map)
        and _is_foldable_paragraph(cur, element_index_map)
        and cur["element_idx_start"] == cur["element_idx_end"]
        and cur["element_idx_start"] == prev["element_idx_end"] + 1
        and prev.get("hierarchy") == cur.get("hierarchy")
        and not _YEAR_RE.search(cur["text"])
    )


def _is_template_text(text: str) -> bool:
    """WCM-template instruction text, exact or reworded. ``_drop_template_instructions``
    runs after the fold, so folding one into a real entry would drop that entry."""
    return (is_template_instruction(text) or is_near_template_instruction(text)
            or is_foreign_template_instruction(text))


def _continues_previous_entry(prev: dict, cur: dict, element_index_map: dict) -> bool:
    """True when ``cur`` is a dateless line that continues ``prev``: it starts
    with a detail label and has content after it.

    Never for template instruction text on either side, and never for a bare
    label with nothing after the colon or into a parent whose first line ends
    in a colon: either one is a sub-header heading the lines after it, not a
    record the detail line completes.
    """
    return (
        _is_adjacent_dateless_line(prev, cur, element_index_map)
        and cur["text"].lower().startswith(DETAIL_LINE_LABELS)
        and cur["text"].strip().lower() not in DETAIL_LINE_LABELS
        and not prev["text"].split("\t", 1)[0].rstrip().endswith(":")
        and not _is_template_text(prev["text"])
        and not _is_template_text(cur["text"])
    )


def fold_labelled_detail_entries(entries: list[dict], element_index_map: dict) -> list[dict]:
    """Fold each dateless labelled detail line into the entry directly above it (#986).

    Runs on one section's entries (content entries plus the non-empty unassigned
    lines 3b also classifies) after detection and validation, so it behaves the
    same whether the detector split a detail line off or missed it. The folded
    entry keeps the parent's type, confidence and hierarchy; its range extends to
    the detail line and its text is joined with the tab stage 2 already uses.
    Returns ``entries`` itself, unsorted and untouched, when nothing folds.
    """
    ordered = sorted(
        entries,
        key=lambda e: e["element_idx_start"] if isinstance(e["element_idx_start"], int) else float("inf"),
    )
    folded: list[dict] = []
    fold_count = 0
    for entry in ordered:
        if folded and _continues_previous_entry(folded[-1], entry, element_index_map):
            parent = folded[-1]
            folded[-1] = {
                **parent,
                "element_idx_end": entry["element_idx_end"],
                "text": parent["text"] + "\t" + entry["text"],
            }
            fold_count += 1
        else:
            folded.append(entry)
    if fold_count:
        logger.info(f"    Folded {fold_count} labelled detail line(s) into the entry above")
        return folded
    return entries


# Bare trailing-date fold (#1226). A carried batch opens on a whole record
# but shows no column header and no earlier record, and the model can then
# emit a record's trailing date as an entry of its own ("Date for prior
# entry"): stage 6 renders that record undated (XUTTKJ-01). A one-line entry
# that is only a date folds into the entry above it when only blank lines sit
# between them and that entry's full text has no year of its own. Never when
# the next entry is a bare date too: a run of them is a stacked date column
# (#259), not one record's tail. Judged on each entry's full text, never the
# prompt's 150-character truncation, where a cut-off year reads as missing.
_MONTH = (r"(?:January|February|March|April|May|June|July|August|September|October|November|December"
          r"|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)")
_YEAR4 = r"(?:19|20)\d{2}"
_DATE_TOKEN = (rf"(?:{_MONTH}\.?\s+(?:\d{{1,2}},?\s*)?{_YEAR4}|\d{{1,2}}/\d{{1,2}}/(?:{_YEAR4}|\d{{2}})"
               rf"|\d{{1,2}}/{_YEAR4}|{_YEAR4})")
BARE_DATE_RE = re.compile(
    rf"^\s*{_DATE_TOKEN}(?:\s*(?:,|[-\u2013\u2014]|to)\s*(?:{_DATE_TOKEN}|present|current|now|ongoing))*\s*[,.]?\s*$",
    re.IGNORECASE)


def _is_paragraph_entry(entry: dict, element_index_map: dict) -> bool:
    """A content entry over a paragraph range: integer bounds, its first
    element a paragraph (never a table row, header or break)."""
    return (
        entry.get("element_type") not in _STRUCTURAL_ENTRY_TYPES
        and isinstance(entry.get("element_idx_start"), int)
        and isinstance(entry.get("element_idx_end"), int)
        and element_index_map.get(entry["element_idx_start"], {}).get("type") == "paragraph"
    )


def _is_bare_date_entry(entry: dict, element_index_map: dict) -> bool:
    """A one-line paragraph entry whose whole text is a date or date range."""
    return (
        _is_paragraph_entry(entry, element_index_map)
        and entry["element_idx_start"] == entry["element_idx_end"]
        and bool(BARE_DATE_RE.match(entry.get("text", "")))
    )


def _only_blank_lines_between(prev_end: int, cur_start: int, element_index_map: dict) -> bool:
    return all(
        element_index_map.get(idx, {}).get("type") in ("empty", "paragraph")
        and not get_element_text(element_index_map[idx])
        for idx in range(prev_end + 1, cur_start)
    )


def _bare_date_completes_previous(prev: dict, cur: dict, nxt: dict | None, element_index_map: dict) -> bool:
    """True when ``cur`` is a bare date that ends ``prev``'s record (the rule
    above the constants). ``prev`` is a paragraph entry already."""
    return (
        _is_bare_date_entry(cur, element_index_map)
        and prev.get("hierarchy") == cur.get("hierarchy")
        and _only_blank_lines_between(prev["element_idx_end"], cur["element_idx_start"], element_index_map)
        and not _YEAR_RE.search(prev.get("text", ""))
        and not (nxt is not None and _is_bare_date_entry(nxt, element_index_map))
    )


def fold_bare_trailing_dates(entries: list[dict], element_index_map: dict) -> list[dict]:
    """Fold each isolated bare trailing date into the record above it (#1226).

    Runs on one section's entries, like ``fold_labelled_detail_entries``. The
    folded entry keeps the record's type, confidence and hierarchy; its range
    extends to the date and the blank lines between, whose break entries go.
    Returns ``entries`` itself, untouched, when nothing folds.
    """
    content = sorted((e for e in entries if _is_paragraph_entry(e, element_index_map)),
                     key=lambda e: e["element_idx_start"])
    folded: list[dict] = []
    spans: list[tuple[int, int]] = []
    for pos, entry in enumerate(content):
        nxt = content[pos + 1] if pos + 1 < len(content) else None
        if folded and _bare_date_completes_previous(folded[-1], entry, nxt, element_index_map):
            parent = folded[-1]
            folded[-1] = {**parent, "element_idx_end": entry["element_idx_end"],
                          "text": parent["text"] + "\t" + entry["text"]}
            spans.append((parent["element_idx_end"] + 1, entry["element_idx_end"]))
        else:
            folded.append(entry)
    if not spans:
        return entries

    def absorbed(entry: dict) -> bool:  # only blank-line breaks sit in a folded gap
        start = entry.get("element_idx_start")
        return isinstance(start, int) and any(lo <= start <= hi for lo, hi in spans)

    rest = [e for e in entries if not _is_paragraph_entry(e, element_index_map) and not absorbed(e)]
    logger.info(f"    Folded {len(spans)} bare date line(s) into the record above")
    return rest + folded


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
    assigned: set[int]
    lines: list[str]


class _DocumentContext(NamedTuple):
    """The values every _extract_section call shares across a whole
    run_stage_2 invocation -- built once before the pool, read but never
    written by any section (#915 review item 6)."""
    doc_elements: list[dict]
    header_indices: set
    element_index_map: dict
    header_info: dict[int, list[str]]
    doc: Document
    unlocated_headings: tuple[str, ...] = ()  # gap-fill leaves these lines for promote_unlocated_headings


def _section_progress_printer(hierarchy_paths: list[list[str]]) -> Callable[[int, _SectionResult], None]:
    """``[N/M] Processing: <path>`` then the section's own lines, via the
    shared make_progress_printer (#923). The ``[N/M]`` line is a parsed
    contract -- orchestrator.py's PROGRESS_PATTERNS read it into the
    progress bar; pinned by test_section_progress_line_is_read_by_progress_patterns.
    N is ``done`` (completion count), never ``index`` (which only names the
    section that finished).
    """
    return make_progress_printer(lambda done, index, result: [
        f"[{done}/{len(hierarchy_paths)}] Processing: {' > '.join(hierarchy_paths[index])}",
        *result.lines,
    ])


def _extract_section(
    context: _DocumentContext,
    hierarchy_path: list[str],
    start_idx: int,
    end_idx: int,
    *,
    document_uid: str | None,
    cancel_check: Callable[[], None] | None,
) -> _SectionResult:
    """One section's body of run_stage_2's loop: detect its entries, assign
    headers/breaks around it, and report what happened -- the per-section
    unit map_in_order fans out over (#881).

    ``context`` bundles the values (doc_elements, header_indices,
    element_index_map, header_info, doc, unlocated_headings) that are constant for every call
    in a given run_stage_2 invocation; only ``hierarchy_path``/``start_idx``/
    ``end_idx`` (this section's own slice) and ``document_uid``/
    ``cancel_check`` vary per call.

    ``cancel_check`` runs first, on whichever thread this call lands on, so a
    cancelled run raises before each section's LLM call: map_in_order cancels
    the queue on the first raise, and any section a pool thread dequeues
    before that shutdown lands raises here too, since the orchestrator's
    cancel is persistent. Sections already in flight finish.

    If ``detect_entries_for_section`` itself raises (it normally does not --
    its own per-batch ``try``/``except`` swallows an LLM/JSON/network error
    into a logged warning and moves to the next batch -- but a bug outside
    that guard, e.g. in ``remove_subset_delimiters``, would propagate), this
    call is uncaught here too, so it propagates out to map_in_order exactly
    like a ``cancel_check`` raise: the first exception cancels the queued
    sections and re-raises; sections already in flight still finish, since a
    thread cannot be interrupted, so at most ``workers`` calls complete
    after a failure (``core/batch_pool.py``'s own docstring). The exception
    then propagates out of run_stage_2's own (unguarded) ``map_in_order``
    call -- before the ``json.dump`` that writes stage 2's output file, so
    no artifact is written for a run that fails this way.

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
        context.doc_elements,
        start_idx,
        end_idx + 1,  # end_idx is inclusive, so add 1 for range
        document_uid=document_uid,
        header_indices=context.header_indices,
        element_index_map=context.element_index_map,
        unlocated_headings=context.unlocated_headings,
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
        # prevent it from being added as a "break" entry; for a span that runs
        # on past its table (#1873), every element up to the end's parent.
        if isinstance(start_idx_entry, str) or isinstance(end_idx_entry, str):
            start_parent = str(start_idx_entry).split(".")[0]
            if start_parent.isdigit():
                parent_idx = int(start_parent)
                end_parent = str(end_idx_entry).split(".")[0]
                last = int(end_parent) if end_parent.isdigit() else parent_idx
                section_assigned.update(range(parent_idx, max(parent_idx, last) + 1))
        else:
            # Integer range for paragraph entries
            for idx in range(start_idx_entry, end_idx_entry + 1):
                section_assigned.add(idx)

    # Find unassigned indices in this section's range
    section_range = set(range(start_idx, end_idx + 1))
    unassigned_in_section = section_range - section_assigned - context.header_indices

    # Add header entries for this section
    section_headers = []
    for idx in sorted(section_range & context.header_indices):
        header_text = _element_text_or_fallback(idx, context.element_index_map, context.doc)
        header_entry = {
            "element_idx_start": idx,
            "element_idx_end": idx,
            "element_type": "header",
            "confidence": 1.0,
            "text": header_text,
            "hierarchy": context.header_info.get(idx, hierarchy_path)
        }
        section_headers.append(header_entry)
        section_assigned.add(idx)

    # Add break entries for unassigned indices (blank lines, etc.)
    break_entries = []
    for idx in sorted(unassigned_in_section):
        para_text = _element_text_or_fallback(idx, context.element_index_map, context.doc)
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
    section_entries_in_order = fold_bare_trailing_dates(
        fold_labelled_detail_entries(section_headers + entries + break_entries, context.element_index_map),
        context.element_index_map,
    )

    return _SectionResult(section_entries_in_order, cost_info, section_assigned, lines)


def _drop_template_instructions(entries: list[dict]) -> list[dict]:
    """Drop WCM-template instruction boilerplate (Layer 1, primary filter).

    Faculty leave the blank template's instruction scaffolding in their CVs;
    those blocks get parsed as entries and pollute downstream output. The
    detector is precision-biased (never drops real CV content). Section
    headers are intentionally NOT dropped here.

    The near-match catches another template revision's rewording of a long
    instruction; left in, the licensure note ("...must have a New York State
    license...") was classified as a license and rendered one the faculty
    member does not hold (#829).
    """
    kept = [
        e for e in entries
        if not (is_template_instruction(e.get("text", ""))
                or is_near_template_instruction(e.get("text", "")))
    ]
    kept = _drop_foreign_template_instructions(kept)
    if len(kept) != len(entries):
        print(f"Filtered {len(entries) - len(kept)} WCM-template instruction entries")
    return kept


# Entry types whose text is structure, not content: a header or break is left
# for the hierarchy to place even when its text reads like an instruction.
_STRUCTURAL_ENTRY_TYPES = ("header", "break")


def _drop_foreign_template_instructions(entries: list[dict]) -> list[dict]:
    """Drop another institution's template scaffolding ("C. Appointments
    (include institution, title, dates)", "1. Sabbatical Leave: N/A") from the
    content entries (#530). Detected by shape, not phrase; see
    `core.template_boilerplate.is_foreign_template_instruction`."""
    return [
        e for e in entries
        if e.get("element_type") in _STRUCTURAL_ENTRY_TYPES
        or not is_foreign_template_instruction(e.get("text", ""))
    ]


def _entry_sort_key(entry: dict) -> tuple[float, float]:
    """Sort key over mixed int/string element indices (e.g., 22 vs "22.2")."""
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


def _batch_carry_report(cost_infos: list[dict], sections: list[tuple[list[str], int, int]]
                        ) -> tuple[dict, list[dict]]:
    """The run's summed BatchCarryCounts, and each section's own counts where
    it has any: which section failed a batch or restored a tail (§5.3)."""
    total = BatchCarryCounts()
    per_section = []
    for cost_info, (path, start, _end) in zip(cost_infos, sections, strict=True):
        counts = cost_info.get("batch_carry")
        if counts is not None:
            total.add(counts)
            per_section.append({"hierarchy": path, "element_idx_start": start, **asdict(counts)})
    return asdict(total), per_section


def run_stage_2(
    docx_path: str,
    hierarchy_json_path: str | None = None,
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
    with open(hierarchy_json_path, encoding="utf-8") as f:
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

    # Paragraph-based fallback index, opened as stage 1 opened it (#1656)
    doc = open_source_docx(docx_path)

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
    document_context = _DocumentContext(
        doc_elements=doc_elements,
        header_indices=header_indices,
        element_index_map=element_index_map,
        header_info=header_info,
        doc=doc, unlocated_headings=tuple(heading.path[-1] for heading in collect_unlocated_headings(hierarchy)),
    )
    results = map_in_order(
        partial(
            _extract_section,
            document_context,  # positional: precedes each call's own (hierarchy_path, start_idx, end_idx)
            document_uid=document_uid,
            cancel_check=cancel_check,
        ),
        [(path, s, e) for path, s, e in sections_to_process],
        workers,
        on_result=_section_progress_printer([p for p, _, _ in sections_to_process]),
    )
    batch_carry, batch_carry_sections = _batch_carry_report([r.cost_info for r in results], sections_to_process)
    for section_entries, cost_info, section_assigned, _lines in results:
        total_cost += cost_info.get("cost", 0)
        total_tokens += cost_info.get("tokens", 0)
        all_assigned_indices |= section_assigned
        all_entries.extend(section_entries)

    # Sort entries by element_idx_start for consistent output
    all_entries.sort(key=_entry_sort_key)

    # Calculate coverage statistics
    content_entries = [e for e in all_entries if e["element_type"] not in ("header", "break")]
    header_entries = [e for e in all_entries if e["element_type"] == "header"]
    break_entries = [e for e in all_entries if e["element_type"] == "break"]

    # Check for any gaps in coverage; the coverage numerator is bounded to
    # doc_length's own index space (see _bound_coverage_indices -- #856).
    unaccounted_indices, covered_doc_indices = _bound_coverage_indices(all_assigned_indices, doc_length)

    # Gated on the user's choice: when strip_template_instructions is False,
    # the instruction text is kept.
    if strip_template_instructions:
        all_entries = _drop_template_instructions(all_entries)

    # Drop empty content entries and exact duplicates (#211). Unconditional:
    # unlike the instruction filter above, this never removes real content.
    all_entries = filter_extraction_noise(all_entries)
    all_entries = promote_unlocated_headings(all_entries, hierarchy)

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
        "batch_carry": batch_carry,  # #1226: carries, skipped carries, failed batches, restored tails
        "batch_carry_sections": batch_carry_sections,  # the same counts for each section that has any
        "entries": all_entries
    }

    with open(output_path, 'w', encoding="utf-8") as f:
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
        print("  python stage_2_entry_extraction.py data/sample_cvs/word/9999_Doe_Jane_CV.docx")
        sys.exit(1)

    docx_path = sys.argv[1]
    hierarchy_json = sys.argv[2] if len(sys.argv) > 2 else None

    run_stage_2(docx_path, hierarchy_json)
