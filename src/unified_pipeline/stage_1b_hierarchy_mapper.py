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

import bisect
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, NotRequired, TypedDict

# Add to path
sys.path.insert(0, str(Path(__file__).parent))

from core.docx_structure_extractor import (
    extract_docx_structure,
    extract_unified_elements,
)
from core.output_manager import OutputManager

_PREAMBLE_SECTION_ALIASES = frozenset({
    "personal data", "personal information", "contact information",
    "contact", "profile", "header",
})


# ------------------------------------------------------------- typed structures
# TypedDict is structural documentation only here (#851 review): no runtime
# change, and no dataclass/Pydantic model, because every read in this module
# is already an `s["key"]` / `s.get("key", default)` lookup on a plain dict
# that also gets `json.dump`-ed verbatim as this stage's output -- swapping
# the storage type would touch every one of those reads and the serializer,
# which is the wrong tool for a pure type-annotation pass.

class HierarchyNode(TypedDict, total=False):
    """One node of the Stage 1 hierarchy this module consumes (not owned by
    it -- the extractor's contract). Every field is read via `.get()`."""
    text: str
    level: str
    children: list[HierarchyNode]
    text_metadata: dict[str, Any]
    paragraph_index: int


class MappedNode(TypedDict):
    """A `HierarchyNode` after `map_hierarchy_node` resolves it to a
    document element index (or `None` if no match was found)."""
    text: str
    level: str
    element_idx: int | None
    synthetic: bool
    children: NotRequired[list[MappedNode]]


class SectionRecord(TypedDict):
    """A flattened, bounded section as `compute_section_boundaries` emits
    it. `synthetic` is only present on the preamble section
    `_apply_preamble_handling` may insert."""
    hierarchy: list[str]
    element_idx_start: int
    element_idx_end: int
    level: str
    has_children: bool
    synthetic: NotRequired[bool]


# Typographic quotes and apostrophes folded to their ASCII forms before
# matching. Stage 1a writes a page-break heading such as "(CONT'D)" with an
# ASCII apostrophe, while the source line carries a typographic one, so without
# the fold the node never matched its own line (#916, #1178).
_PUNCTUATION_FOLD = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u02bc": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"',
})


def normalize_text(text: str) -> str:
    """Normalize text for matching (lowercase, strip whitespace, punctuation, etc.)"""
    import re
    text = text.translate(_PUNCTUATION_FOLD)
    text = re.sub(r'[:\\.,-]', '', text)
    return " ".join(text.lower().strip().split())


# Characters a heading paragraph may open with before the heading itself: a
# rule of asterisks or underscores typed on the same line ("***** EDUCATION:").
# The run counted toward the strict-mode length cap, so the heading was never
# placed (#1252, EBYSBC class E17). normalize_text already deletes '-'; it is
# listed so the set reads as the separators a CV uses, not as what survives
# normalization.
_LEADING_SEPARATOR_CHARS = "*-_= "


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

    para_text = para_text.lstrip(_LEADING_SEPARATOR_CHARS)

    # An empty paragraph is a substring of every string, so the "contained"
    # branch below would otherwise match it against ANY header (#851). Every
    # caller already skips empty text before calling this function, but the
    # function itself must not silently say yes.
    if not para_text:
        return False

    # Exact match - always accept
    if para_text == expected_header:
        return True

    # The paragraph is contained in the expected header (header might be longer)
    # e.g., para_text="education" matches expected_header="education and training"
    # Only accept if paragraph is very short (< 2x header length)
    # The paragraph must be whole words inside the header: a bare substring made
    # the sibling header "National" satisfy the expected header "International",
    # so the geographic sub-label bound to the wrong header paragraph (#429).
    # Lookarounds, not \b: \b needs a word char at the phrase end, so a paragraph
    # ending in a symbol ("research &", "k+") could never match whole-phrase.
    if re.search(r'(?<!\w)' + re.escape(para_text) + r'(?!\w)', expected_header):
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


def _find_header_element(
    elements: list[dict[str, Any]],
    expected_header: str,
    search_start: int,
    search_end: int,
    partial_floor: int = 0,
    excluded_headers: frozenset[str] = frozenset(),
    exact_only: bool = False,
) -> tuple[int, int] | None:
    """Locate `expected_header` among elements[search_start:search_end].

    `partial_floor` and `excluded_headers` constrain only PARTIAL matches, never
    an exact one: a partial match is skipped when its element index is below
    `partial_floor` or its text is in `excluded_headers` (#1178). An exact
    paragraph is always accepted, wherever it sits. `exact_only` skips every
    partial match.

    Returns (position in `elements`, element index to report), or None. A
    paragraph whose normalized text IS the header wins over an earlier
    paragraph that is only a fragment of it (a short whole-word paragraph such
    as "PhD:" inside the longer header "PhD advisees"). A first match that
    is longer than the header (the header inside a content-like paragraph)
    still wins immediately, and with no exact paragraph in range the first
    fragment wins, as before (#429).
    """
    first_partial: tuple[int, int] | None = None
    for i in range(search_start, search_end):
        elem = elements[i]
        elem_text = elem.get('text', '')

        # Search in paragraphs AND table_header elements (from extract_unified_elements)
        if elem.get('type') not in ('paragraph', 'table_header') or not elem_text:
            continue

        para_text = normalize_text(elem_text)
        if not para_text:
            continue

        # strict=True prevents matching content paragraphs like
        # "Advanced Health Education Mammography Center" when looking for "Education"
        found = (i, elem.get('unified_idx', elem.get('idx', i)))
        if para_text == expected_header:
            return found
        if exact_only or found[1] < partial_floor or para_text in excluded_headers:
            continue
        if first_partial is None and is_header_match(expected_header, para_text, strict=True):
            if len(para_text) > len(expected_header):
                # The header sits inside a longer paragraph: the old
                # first-match behaviour, no exact paragraph is sought.
                return found
            first_partial = found
    return first_partial


def find_header_in_sequence(
    elements: list[dict[str, Any]],
    hierarchy_sequence: list[str],
    start_idx: int = 0
) -> list[tuple[str, int]] | None:
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
    matches: list[tuple[str, int]] = []
    current_search_idx = start_idx

    last = len(normalized_sequence) - 1
    for pos, expected_header in enumerate(normalized_sequence):
        # Search forward from current position. Only the node itself (the last
        # entry) may match partially: a parent is an anchor, and a later partial
        # hit on its text sent the child to the next section's same-named line
        # (#916).
        hit = _find_header_element(
            elements, expected_header, current_search_idx, len(elements),
            exact_only=pos < last,
        )
        if hit is None:
            # Sequence broken - return None
            return None
        i, elem_idx = hit
        matches.append((hierarchy_sequence[len(matches)], elem_idx))
        current_search_idx = i + 1

    return matches


def _is_fragment_hit(elements: list[dict[str, Any]], pos: int, expected_header: str) -> bool:
    """True when the paragraph at `pos` is only a fragment of the header (a bare
    "Research" for "Pending Research Grants"), the weakest kind of partial."""
    return len(normalize_text(elements[pos].get('text', ''))) < len(expected_header)


def _fallback_header_search(
    elements: list[dict[str, Any]],
    expected_header: str,
    start_search_idx: int,
    parent_idx: int,
    excluded_headers: frozenset[str],
) -> int | None:
    """Plain header search for a node the sequence match could not place.

    Forward from `start_search_idx` first. The hierarchy may not match document
    order, so a miss, or a forward hit that is only a fragment of the header,
    gives an exact paragraph behind the cursor the chance to win. After that the
    backward search accepts a partial match only at or after the parent's own
    line and never on a top-level heading (#916, #1178).
    """
    forward = _find_header_element(elements, expected_header, start_search_idx, len(elements))
    if forward is not None and not _is_fragment_hit(elements, forward[0], expected_header):
        return forward[1]
    if start_search_idx > 0:
        exact = _find_header_element(
            elements, expected_header, 0, start_search_idx, exact_only=True
        )
        if exact is not None:
            return exact[1]
    if forward is not None:
        return forward[1]
    if start_search_idx > 0:
        partial = _find_header_element(
            elements, expected_header, 0, start_search_idx,
            partial_floor=parent_idx, excluded_headers=excluded_headers,
        )
        return partial[1] if partial is not None else None
    return None


def map_hierarchy_node(
    node: HierarchyNode,
    elements: list[dict[str, Any]],
    parent_path: list[str] | None = None,
    start_search_idx: int = 0,
    parent_idx: int = 0,
    top_level_headers: frozenset[str] = frozenset(),
    parent_mapped: bool = False,
) -> tuple[MappedNode, int]:
    """
    Map a single hierarchy node and its children to element indices.

    Args:
        node: Hierarchy node from Stage 1
        elements: List of document elements from structure extractor
        parent_path: Path of parent headers for context
        start_search_idx: Where to start searching in document
        parent_idx: Element index of the parent's header. The backward
            fallback accepts a partial match only at or after it (#1178).
        top_level_headers: Normalized texts of the top-level headers. For a
            child, the backward fallback never partial-matches one of them.
        parent_mapped: True when the parent header has its own element index.

    Returns:
        Tuple of (mapped_node, next_search_idx)
    """
    if parent_path is None:
        parent_path = []

    node_text = node.get("text", "").strip()
    current_path = parent_path + ([node_text] if node_text else [])

    # Check if this is a synthetic header (added by LLM, not in original document)
    is_synthetic = _is_synthetic_node(node)

    # Try to find this node's header in the document
    element_idx: int | None = None

    if node_text and not is_synthetic:
        # Sequence-based matching with parent context, only when the parent
        # has no element of its own. A mapped parent is already the anchor:
        # re-searching its text from the cursor found a LATER line with the
        # same name and dragged the child past its own line (#916). A node
        # with no usable sequence goes to the fallback search below.
        search_sequence = current_path[-min(3, len(current_path)):]  # Use last 3 headers for context
        use_sequence = len(search_sequence) > 1 and not parent_mapped
        matches = (
            find_header_in_sequence(elements, search_sequence, start_search_idx)
            if use_sequence
            else None
        )

        if matches:
            # Found the sequence - use the last match (this node)
            element_idx = matches[-1][1]
        else:
            element_idx = _fallback_header_search(
                elements,
                normalize_text(node_text),
                start_search_idx,
                parent_idx,
                top_level_headers if parent_path else frozenset(),
            )

    # Build mapped node
    mapped_node: MappedNode = {
        "text": node_text,
        "level": node.get("level", ""),
        "element_idx": element_idx,
        "synthetic": is_synthetic
    }

    # Track where to search next
    next_search_idx = element_idx + 1 if element_idx is not None else start_search_idx

    # Process children
    if "children" in node and node["children"]:
        mapped_children: list[MappedNode] = []

        # For synthetic headers, children should search from the original start position
        # because the LLM may have grouped items out of document order
        child_search_start = start_search_idx if is_synthetic else next_search_idx
        child_parent_idx = parent_idx if element_idx is None else element_idx

        for child in node["children"]:
            mapped_child, child_next_idx = map_hierarchy_node(
                child,
                elements,
                current_path,
                child_search_start,
                child_parent_idx,
                top_level_headers,
                element_idx is not None,
            )
            mapped_children.append(mapped_child)

            # For synthetic parents, each child searches from the same start
            # For real parents, children search sequentially
            if not is_synthetic:
                child_search_start = child_next_idx

        # Update next_search_idx to be after all children
        child_indices = [
            idx for c in mapped_children if (idx := c.get("element_idx")) is not None
        ]
        if child_indices:
            next_search_idx = max(max(child_indices) + 1, next_search_idx)

        mapped_node["children"] = mapped_children

    return mapped_node, next_search_idx


def top_level_header_texts(hierarchy: list[HierarchyNode]) -> frozenset[str]:
    """Normalized text of every non-empty top-level header."""
    return frozenset(
        normalize_text(n.get("text", "")) for n in hierarchy if n.get("text", "").strip()
    )


def _header_occurs_in_document(header_text: str, normalized_lines: list[str]) -> bool:
    """True when a line could carry the header: it contains the normalized
    header anywhere (a run-in or separator-prefixed heading stage 2 may still
    promote), or 1b's own strict match accepts it (a short fragment of a
    longer 1a header)."""
    expected = normalize_text(header_text)
    return any(
        expected in line or is_header_match(expected, line, strict=True)
        for line in normalized_lines
    )


def _is_synthetic_node(node: HierarchyNode) -> bool:
    """Same test map_hierarchy_node applies: an LLM-added node with no line."""
    return node.get("text_metadata", {}).get("synthetic", False) or node.get("paragraph_index") == -1


def _prune_absent_header(
    node: HierarchyNode, normalized_lines: list[str], dropped: list[str]
) -> HierarchyNode | None:
    """The node with its absent headers removed, or None when it goes too.
    Appends each dropped header's text to ``dropped``."""
    original_children = node.get("children") or []
    children = [
        kept for child in original_children
        if (kept := _prune_absent_header(child, normalized_lines, dropped)) is not None
    ]
    if not children:
        text = (node.get("text") or "").strip()
        if _is_synthetic_node(node):
            if original_children:
                return None
        elif text and not _header_occurs_in_document(text, normalized_lines):
            dropped.append(text)
            return None
    if len(children) == len(original_children):
        return node
    return {**node, "children": children}


def drop_headers_absent_from_document(
    hierarchy: list[HierarchyNode], elements: list[dict[str, Any]]
) -> tuple[list[HierarchyNode], list[str]]:
    """Remove the stage-1a headers whose text occurs on no document line.

    1a can return headings the document does not contain (EBYSBC class E17:
    a converted CV kept no heading lines, yet 1a returned 13 headings and 1b
    placed none). Such a node can never be placed here nor promoted by stage
    2, so it is dropped before mapping. A node is dropped only when nothing
    under it survives: a real header keeps its absent parent as a grouping
    node, and a synthetic grouping node is dropped once every child it had is
    gone. A synthetic node with no children (the PERSONAL DATA preamble) is
    always kept. Every element type counts as a line, table cells included.

    Returns (kept hierarchy, texts of the dropped headers, each child before
    its parent).
    """
    normalized_lines = [
        line for line in (normalize_text(e.get("text") or "") for e in elements) if line
    ]
    dropped: list[str] = []
    kept_nodes = [
        kept for node in hierarchy
        if (kept := _prune_absent_header(node, normalized_lines, dropped)) is not None
    ]
    return kept_nodes, dropped


def get_first_child_element_idx(children: list[MappedNode]) -> int | None:
    """
    Recursively find the first element_idx in a list of children.

    This handles cases where a synthetic parent (element_idx=null) has children
    with actual element indices. Returns the minimum element_idx found.

    Args:
        children: List of child nodes

    Returns:
        The first (minimum) element_idx found, or None if none exist
    """
    indices: list[int] = []
    for child in children:
        child_element_idx = child.get("element_idx")
        if child_element_idx is not None:
            indices.append(child_element_idx)
        # Also check grandchildren
        if "children" in child and child["children"]:
            grandchild_idx = get_first_child_element_idx(child["children"])
            if grandchild_idx is not None:
                indices.append(grandchild_idx)

    return min(indices) if indices else None


def has_mapped_children(node: MappedNode) -> bool:
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


def _next_start_lookup(sections: list[SectionRecord]) -> Callable[[int], int | None]:
    """
    Build a "smallest start strictly greater than X" lookup over `sections`,
    from a single sort of every section's `element_idx_start`.

    Shared by `_repair_out_of_order_section_bounds` and
    `_clip_leaves_to_next_header` (#916 review): both previously rebuilt a
    filtered `next_starts` list over the whole `sections` collection and
    took its `min` for every section they touched, an O(n) scan per
    section. Sorting once here and using `bisect.bisect_right` per lookup
    turns that into a single O(n log n) pass. `bisect_right` skips past
    every section sharing the same start, matching the strict `>`
    comparison both call sites used before -- ties are resolved the same
    way as today (see `_clip_leaves_to_next_header` for whether ties are
    reachable at all).
    """
    ordered_starts = sorted(s["element_idx_start"] for s in sections)

    def _lookup(start: int) -> int | None:
        idx = bisect.bisect_right(ordered_starts, start)
        return ordered_starts[idx] if idx < len(ordered_starts) else None

    return _lookup


def _repair_out_of_order_section_bounds(sections: list[SectionRecord], doc_length: int) -> None:
    """
    Fix invalid boundaries caused by out-of-order hierarchies.

    A section's naive end (computed in `compute_bounds`) can inherit a
    `default_end` from before a later sibling actually appears, leaving
    `element_idx_end < element_idx_start`. For each such section, find the
    next section in document order (by start index) and end just before it,
    or run to the document end if none follows.

    Mutates `sections` in place.
    """
    next_start_after = _next_start_lookup(sections)
    for section in sections:
        if section["element_idx_end"] < section["element_idx_start"]:
            next_start = next_start_after(section["element_idx_start"])
            if next_start is not None:
                section["element_idx_end"] = next_start - 1
            else:
                section["element_idx_end"] = doc_length - 1


def _extend_ancestors_to_cover_repaired_children(
    sections: list[SectionRecord],
    direct_children_by_id: dict[int, list[SectionRecord]],
) -> None:
    """
    Re-extend every ancestor's end to cover its full descendant subtree.

    `compute_bounds` extends a parent's end to its children's max end BEFORE
    `_repair_out_of_order_section_bounds` runs, so a child repaired above can
    grow past the bound its parent already settled on, violating this
    function's docstring invariant #3 ("parent end >= last child end").

    `sections` is built in document-order-of-completion (DFS post-order): a
    node's own section is appended only after every section in its subtree
    has already been appended, so descendants always precede their ancestor
    in the list. Walking the list in order and extending each section by
    only its DIRECT children (looked up by object identity in
    `direct_children_by_id`, keyed by `id(parent_section)`) therefore still
    cascades correctly through multi-level nesting in one pass: a grandchild
    repair has already been folded into its parent's end by the time the
    grandparent is visited.

    Matching by object identity (rather than by hierarchy-path prefix) keeps
    this scoped to the actual tree: two different sections that happen to
    share a hierarchy name at the same depth (duplicate-named siblings) must
    never be treated as one another's descendants.

    Mutates `sections` in place.
    """
    for section in sections:
        if not section["has_children"]:
            continue
        children = direct_children_by_id.get(id(section), [])
        if children:
            child_max_end = max(child["element_idx_end"] for child in children)
            section["element_idx_end"] = max(section["element_idx_end"], child_max_end)


def _clip_leaves_to_next_header(sections: list[SectionRecord]) -> None:
    """
    Clip every leaf section's end to just before the next mapped section
    header in document order, whatever the tree says about siblings.

    Supersedes, for LEAVES only, the effect of three mechanisms that let
    leaf ranges overlap (#916): the out-of-order fallback (`compute_bounds`'s
    `end_idx < element_idx` branch and `_repair_out_of_order_section_bounds`),
    sibling disorder (a section's end taken from the next *listed* sibling
    rather than the next sibling in document order), and the
    late-Personal-Data preamble extension swallowing earlier sections. It
    does not retire those mechanisms: `_repair_out_of_order_section_bounds`
    and `_extend_ancestors_to_cover_repaired_children` still run first and
    remain load-bearing for PARENT ranges (`has_children == True`, the #851
    invariant "parent end >= last child end") -- this function only ever
    rewrites records with `has_children == False`. Deleting either of those
    two passes changes `has_children == True` records on the corpus; adding
    this clip alone does not (verified by the stage-1b recompute in the
    PR: 0 parent records changed on 111/111 CVs from the clip, non-zero
    from deleting either pass -- see the mutation checks for both).

    Same-start ties are reachable -- #916's named residual is 90 leaf pairs
    on 19 CVs sharing one `element_idx_start` -- and deliberately left
    unbounded here: `_next_start_lookup`'s `bisect_right` skips every
    section at the same start, so neither bounds the other. Choosing a
    label between two names mapped to one header is stage 1a's call, not
    this function's.

    The trailing leaf in document order (no start strictly after it) is
    left at whatever end it already had going into this function -- usually
    `compute_bounds`'s own `default_end` (the document end, or a bounding
    ancestor's `default_end` passed down the recursion), but it can also be
    `doc_length - 1` from `_repair_out_of_order_section_bounds` when that
    inherited `default_end` precedes the leaf's own start (e.g.
    `A@10 > [A1@50], B@20, doc=100`: A1 inherits `default_end` 19 from A;
    19 < 50, so `_repair_out_of_order_section_bounds` reassigns A1's end to
    `doc_length - 1` = 99; corpus incidence 0/111). Mechanism #1 above can
    therefore reach a trailing leaf too -- but having no start after it, a
    trailing leaf cannot overlap another leaf regardless of where its end
    came from, so this boundary case needs no clip.

    Mutates `sections` in place.
    """
    next_start_after = _next_start_lookup(sections)
    for section in sections:
        if section["has_children"]:
            continue
        next_start = next_start_after(section["element_idx_start"])
        if next_start is not None:
            # `min` only ever shrinks: a leaf already tighter than the next
            # header (e.g. from `_repair_out_of_order_section_bounds`) keeps
            # its own smaller end -- this never re-extends a range.
            section["element_idx_end"] = min(section["element_idx_end"], next_start - 1)


def _apply_preamble_handling(sections: list[SectionRecord], doc_length: int) -> None:
    """
    Capture any unmapped content before the first real section.

    If there's content before the first section (indices 0 to
    first_section_start - 1), extend an existing "Personal Data"-like
    section to cover it, or create a synthetic one. If no sections exist at
    all but the document has content, create a single fallback section for
    the whole document.

    A pure move out of `compute_section_boundaries` (no behaviour change --
    #851 R2, to keep that function under the §3.2 / ratchet line threshold
    after the tree-identity rewrite added lines to it).

    Mutates `sections` in place.
    """
    if sections:
        # Find the earliest element_idx_start among all sections
        first_section_start = min(s["element_idx_start"] for s in sections)

        if first_section_start > 0:
            # There's content before the first section (preamble)
            preamble_end = first_section_start - 1

            # Check if "Personal Data" section already exists
            personal_data_sections = [
                s for s in sections
                if s["hierarchy"] and s["hierarchy"][0].lower().strip() in _PREAMBLE_SECTION_ALIASES
            ]

            # Find the one with the earliest start, or None if none exists.
            pd_section = (
                min(personal_data_sections, key=lambda s: s["element_idx_start"])
                if personal_data_sections else None
            )

            # Only extend an existing Personal-Data-like section backwards
            # over the preamble when it is itself the first section in
            # document order -- a LATER one (content precedes it) must not
            # be stretched back over those earlier sections; synthesise the
            # preamble instead (#916).
            if pd_section is not None and pd_section["element_idx_start"] == first_section_start:
                if pd_section["element_idx_start"] > 0:
                    # Extend backwards to include preamble
                    pd_section["element_idx_start"] = 0
                    # Update parent section if exists
                    for s in sections:
                        if s["has_children"] and pd_section["hierarchy"][0] in s["hierarchy"]:
                            s["element_idx_start"] = min(s["element_idx_start"], 0)
            else:
                # Create synthetic "Personal Data" section for preamble
                preamble_section: SectionRecord = {
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


def compute_section_boundaries(mapped_hierarchy: list[MappedNode], doc_length: int) -> list[SectionRecord]:
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
    sections: list[SectionRecord] = []
    # Maps id(parent_section) -> the list of that section's own direct child
    # section dicts (by object identity, never by hierarchy-name -- #851).
    # A synthetic node with no element_idx creates no section of its own, so
    # its real children are flattened into its parent's direct-children list
    # (see the `else` branch below).
    direct_children_by_id: dict[int, list[SectionRecord]] = {}

    def compute_bounds(
        nodes: list[MappedNode],
        parent_path: list[str] | None = None,
        default_end: int | None = None
    ) -> tuple[int, list[SectionRecord]]:
        """
        Compute boundaries for nodes at the same level.

        Returns (max end index seen, the section dicts created at this level
        -- for parent to use as its own direct-children list).
        """
        if parent_path is None:
            parent_path = []
        if default_end is None:
            default_end = doc_length - 1

        max_end_seen = 0
        level_sections: list[SectionRecord] = []

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
                child_sections: list[SectionRecord] = []
                if "children" in node and node["children"]:
                    children_max_end, child_sections = compute_bounds(
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

                section: SectionRecord = {
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
                if child_sections:
                    direct_children_by_id[id(section)] = child_sections
                level_sections.append(section)

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

                    children_max_end, children_level_sections = compute_bounds(
                        node["children"],
                        current_path,
                        default_end=bounded_end  # Use bounded end, not raw default_end
                    )
                    max_end_seen = max(max_end_seen, children_max_end)
                    # This synthetic node created no section of its own, so
                    # its real children are this level's direct children too.
                    level_sections.extend(children_level_sections)

        return max_end_seen, level_sections

    compute_bounds(mapped_hierarchy)

    # POST-PROCESS: fix invalid boundaries caused by out-of-order hierarchies,
    # then re-extend every ancestor to cover any child the repair just grew
    # (docstring invariant #3: parent end >= last child end -- #851).
    if sections:
        _repair_out_of_order_section_bounds(sections, doc_length)
        _extend_ancestors_to_cover_repaired_children(sections, direct_children_by_id)

    # PREAMBLE HANDLING: Check for unmapped content at the beginning of the document
    _apply_preamble_handling(sections, doc_length)

    # Clip every leaf to end just before the next mapped header in document
    # order -- runs last, after the repairs and preamble handling above,
    # since it supersedes what those mechanisms leave behind for leaves (#916).
    _clip_leaves_to_next_header(sections)

    return sections


def run_stage_1b(docx_path: str, hierarchy_json_path: str | Path | None = None) -> tuple[dict[str, Any], Path]:
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
    with open(hierarchy_json_path, encoding="utf-8") as f:
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
    mapped_hierarchy: list[MappedNode] = []
    next_search_idx = 0

    top_level, absent_headers = drop_headers_absent_from_document(
        hierarchy_data.get("hierarchy", []), elements
    )
    top_level_headers = top_level_header_texts(top_level)
    for node in top_level:
        mapped_node, next_search_idx = map_hierarchy_node(
            node, elements, [], next_search_idx, top_level_headers=top_level_headers
        )
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
            },
            "headers_absent_from_document": absent_headers,
        }
    }

    with open(output_path, 'w', encoding="utf-8") as f:
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
