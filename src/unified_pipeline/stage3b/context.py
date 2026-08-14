"""Hierarchy context for stage 3b classification (#522).

Moved verbatim from `stage_3b_entry_classifier.py`: `TaxonomyContext`,
`build_mapping_index`, `get_taxonomy_context`. Stage 3a's header hierarchy
comes in; the taxonomy suggestions for one entry's hierarchy path come out.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class TaxonomyContext:
    """Taxonomy suggestions for a hierarchy path."""
    subsection: Optional[Dict] = None  # Most specific (H3)
    section: Optional[Dict] = None      # Parent (H2)
    meta_section: Optional[Dict] = None # Top-level (H1)

    def get_primary_codes(self) -> List[str]:
        """Get primary suggested codes from most specific level."""
        if self.subsection and self.subsection.get("taxonomy_options"):
            return [o["code"] for o in self.subsection["taxonomy_options"]]
        if self.section and self.section.get("taxonomy_options"):
            return [o["code"] for o in self.section["taxonomy_options"]]
        if self.meta_section and self.meta_section.get("taxonomy_options"):
            return [o["code"] for o in self.meta_section["taxonomy_options"]]
        return []

    def get_all_suggested_codes(self) -> List[str]:
        """Get ALL suggested codes from ALL hierarchy levels (for taxonomy filtering)."""
        codes = set()
        if self.subsection and self.subsection.get("taxonomy_options"):
            codes.update(o["code"] for o in self.subsection["taxonomy_options"])
        if self.section and self.section.get("taxonomy_options"):
            codes.update(o["code"] for o in self.section["taxonomy_options"])
        if self.meta_section and self.meta_section.get("taxonomy_options"):
            codes.update(o["code"] for o in self.meta_section["taxonomy_options"])
        return list(codes)

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


def build_mapping_index(mappings: List[Dict], index: Dict = None, path: List[str] = None) -> Dict:
    """
    Build an index from header title to taxonomy mapping.

    Returns dict mapping title -> mapping node
    """
    if index is None:
        index = {}
    if path is None:
        path = []

    for node in mappings:
        title = node.get("title", "")
        # Store with full path for disambiguation
        full_path = tuple(path + [title])
        index[title] = node
        index[full_path] = node

        # Recurse into children
        children = node.get("children", [])
        if children:
            build_mapping_index(children, index, path + [title])

    return index


def get_taxonomy_context(hierarchy: List[str], mapping_index: Dict) -> TaxonomyContext:
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

    # Try to match each level
    if len(hierarchy) >= 1:
        context.meta_section = mapping_index.get(hierarchy[0])

    if len(hierarchy) >= 2:
        context.section = mapping_index.get(hierarchy[1])
        # Try full path if simple lookup fails
        if not context.section:
            context.section = mapping_index.get(tuple(hierarchy[:2]))

    if len(hierarchy) >= 3:
        context.subsection = mapping_index.get(hierarchy[2])
        if not context.subsection:
            context.subsection = mapping_index.get(tuple(hierarchy[:3]))

    return context
