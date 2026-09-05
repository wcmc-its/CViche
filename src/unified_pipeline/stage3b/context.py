"""Hierarchy context for stage 3b classification (#522).

Moved verbatim from `stage_3b_entry_classifier.py`: `TaxonomyContext`,
`build_mapping_index`, `get_taxonomy_context`. Stage 3a's header hierarchy
comes in; the taxonomy suggestions for one entry's hierarchy path come out.

The `taxonomy_options` dicts this module reads (``o["code"]``,
``o["confidence"]``) are not validated here. They are validated once, at the
configuration boundary, by `stage3b/io.py`'s `load_stage_3a_mappings()` /
`_normalize_taxonomy_mappings()` -- the sole production source of the
`mappings` this module's `build_mapping_index()` indexes -- which drops any
option missing a `code` and coerces `confidence` to a float in [0, 1] before
this module ever sees it. Bare subscripts below rely on that guarantee.
"""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TaxonomyContext:
    """Taxonomy suggestions for a hierarchy path."""
    subsection: dict | None = None  # Most specific (H3)
    section: dict | None = None      # Parent (H2)
    meta_section: dict | None = None # Top-level (H1)

    def get_primary_codes(self) -> list[str]:
        """Get primary suggested codes from most specific level."""
        if self.subsection and self.subsection.get("taxonomy_options"):
            return [o["code"] for o in self.subsection["taxonomy_options"]]
        if self.section and self.section.get("taxonomy_options"):
            return [o["code"] for o in self.section["taxonomy_options"]]
        if self.meta_section and self.meta_section.get("taxonomy_options"):
            return [o["code"] for o in self.meta_section["taxonomy_options"]]
        return []

    def get_all_suggested_codes(self) -> list[str]:
        """Get ALL suggested codes from ALL hierarchy levels (for taxonomy filtering).

        Order matters: classify.py's `_classify_one_batch` falls back to
        ``all_suggested_codes[0]`` as the classification for an all-empty
        batch, so this must return the most-specific (subsection) codes
        first, then section, then meta_section, deduplicated without
        disturbing that order. A `set` iterates in an implementation-
        dependent order and would make that fallback code nondeterministic.
        """
        codes: list[str] = []
        for level in (self.subsection, self.section, self.meta_section):
            if level and level.get("taxonomy_options"):
                for o in level["taxonomy_options"]:
                    code = o["code"]
                    if code not in codes:
                        codes.append(code)
        return codes

    def format_context_string(self) -> str:
        """Format hierarchy context for LLM prompt with clear framing."""
        lines = []

        # Build hierarchy lines from top to bottom
        if self.meta_section:
            opts = self.meta_section.get("taxonomy_options", [])
            title = self.meta_section.get("title", "?")
            if opts:
                codes = ", ".join([f"{o['code']} {o['confidence']:.0%}" for o in opts[:3]])
                lines.append(f"  Top-level: {title}")
                lines.append(f"      → Suggested: {codes}")

        if self.section:
            opts = self.section.get("taxonomy_options", [])
            title = self.section.get("title", "?")
            if opts:
                codes = ", ".join([f"{o['code']} {o['confidence']:.0%}" for o in opts[:3]])
                lines.append(f"")
                lines.append(f"    Section: {title}")
                lines.append(f"      → Suggested: {codes}")

        if self.subsection:
            opts = self.subsection.get("taxonomy_options", [])
            title = self.subsection.get("title", "?")
            if opts:
                codes = ", ".join([f"{o['code']} {o['confidence']:.0%}" for o in opts])
                lines.append(f"")
                lines.append(f"      Subsection: {title}")
                lines.append(f"        → Suggested: {codes}")

        return "\n".join(lines) if lines else "  (No hierarchy context available)"


def build_mapping_index(mappings: list[dict], index: dict = None, path: list[str] = None) -> dict:
    """
    Build an index from header title to taxonomy mapping.

    Returns dict mapping title -> mapping node

    Two keys are stored per node: the short `title` (ambiguous if the same
    title recurs under different parents -- last node wins, logged when it
    happens) and the full-path tuple (unambiguous, since two nodes cannot
    share a path). `get_taxonomy_context()` looks up the full-path key first
    and only falls back to the short-title key when no exact path match
    exists, so the ambiguous key is a fallback, not the primary lookup.
    """
    if index is None:
        index = {}
    if path is None:
        path = []

    for node in mappings:
        title = node.get("title", "")
        # Store with full path for disambiguation
        full_path = tuple(path + [title])
        if title in index and index[title] is not node:
            logger.warning(
                "build_mapping_index: title %r appears under more than one "
                "parent -- short-title lookup will resolve to %s; full-path "
                "lookups for either branch are unaffected", title, full_path)
        index[title] = node
        index[full_path] = node

        # Recurse into children
        children = node.get("children", [])
        if children:
            build_mapping_index(children, index, path + [title])

    return index


def get_taxonomy_context(hierarchy: list[str], mapping_index: dict) -> TaxonomyContext:
    """
    Get taxonomy context for an entry's hierarchy path.

    Args:
        hierarchy: Entry's hierarchy path, e.g., ["RESEARCH AND SCHOLARSHIP", "Publications", "Books"]
        mapping_index: Index from build_mapping_index

    Returns:
        TaxonomyContext with mappings at each level
    """
    context = TaxonomyContext()

    if not hierarchy:
        return context

    # Try to match each level. Top-level sections have no parent to
    # disambiguate against, so only the bare title is looked up there.
    if len(hierarchy) >= 1:
        context.meta_section = mapping_index.get(hierarchy[0])

    if len(hierarchy) >= 2:
        # Full path first: it is the unambiguous key. The bare title is
        # ambiguous when the same title recurs under a different parent
        # (see build_mapping_index), so it is only a fallback for when the
        # taxonomy tree has no exact path match for this hierarchy.
        context.section = mapping_index.get(tuple(hierarchy[:2]))
        if context.section is None:
            context.section = mapping_index.get(hierarchy[1])

    if len(hierarchy) >= 3:
        context.subsection = mapping_index.get(tuple(hierarchy[:3]))
        if context.subsection is None:
            context.subsection = mapping_index.get(hierarchy[2])

    return context
