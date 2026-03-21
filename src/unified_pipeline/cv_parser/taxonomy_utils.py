"""
Taxonomy Utility Functions for Section Code Access

Provides a clean API for working with section codes instead of section IDs.
This module is the new standard interface for taxonomy lookups.

Usage:
    from src.unified_pipeline.cv_parser.taxonomy_utils import (
        get_section_by_code,
        get_subsections,
        validate_section_code,
        code_to_canonical_name,
        code_to_section_id
    )

    # Look up a section by code
    section = get_section_by_code("P")  # Institutional Administration
    print(section['canonical'])  # "Institutional Administrative Activities"

    # Get all subsections of a parent
    subs = get_subsections("S")  # Returns ['S1', 'S2', ..., 'S9']

    # Validate a code exists
    if validate_section_code("B1"):
        print("B1 is valid!")

    # Get display name
    name = code_to_canonical_name("M6")  # "Clinical Trials"
"""

import logging
from typing import Dict, List, Optional, Any
from .cv_taxonomy_wcm import CV_SECTIONS

logger = logging.getLogger(__name__)

# Build lookup indexes on module import for fast access
_CODE_TO_SECTION: Dict[str, Dict[str, Any]] = {}
_ID_TO_SECTION: Dict[str, Dict[str, Any]] = {}
_CODE_TO_IDS: Dict[str, List[str]] = {}  # Handle codes that map to multiple IDs (like B1)

def _build_indexes():
    """Build lookup indexes from CV_SECTIONS."""
    global _CODE_TO_SECTION, _ID_TO_SECTION, _CODE_TO_IDS

    for section in CV_SECTIONS:
        section_id = section['id']
        section_code = section.get('section_code')

        # Index by ID
        _ID_TO_SECTION[section_id] = section

        # Index by code
        if section_code:
            if section_code not in _CODE_TO_SECTION:
                _CODE_TO_SECTION[section_code] = section

            # Track all IDs that map to this code
            if section_code not in _CODE_TO_IDS:
                _CODE_TO_IDS[section_code] = []
            _CODE_TO_IDS[section_code].append(section_id)

    logger.debug(f"Built taxonomy indexes: {len(_CODE_TO_SECTION)} codes, {len(_ID_TO_SECTION)} IDs")

# Build indexes on import
_build_indexes()


def get_section_by_code(code: str) -> Optional[Dict[str, Any]]:
    """
    Get section metadata by section code.

    Args:
        code: Section code (e.g., "A", "B1", "S", "P")

    Returns:
        Section dictionary or None if not found

    Example:
        >>> section = get_section_by_code("P")
        >>> print(section['canonical'])
        'Institutional Administrative Activities'
    """
    return _CODE_TO_SECTION.get(code)


def get_section_by_id(section_id: str) -> Optional[Dict[str, Any]]:
    """
    Get section metadata by section ID (legacy compatibility).

    Args:
        section_id: Section ID (e.g., "institutional_administration")

    Returns:
        Section dictionary or None if not found
    """
    return _ID_TO_SECTION.get(section_id)


def get_all_sections_for_code(code: str) -> List[Dict[str, Any]]:
    """
    Get all sections that share the same code.

    Some codes map to multiple section IDs (e.g., B1 includes undergraduate,
    graduate, doctoral, medical, and combined degrees).

    Args:
        code: Section code (e.g., "B1")

    Returns:
        List of section dictionaries

    Example:
        >>> sections = get_all_sections_for_code("B1")
        >>> for s in sections:
        ...     print(s['canonical'])
        'Undergraduate Education'
        'Graduate Education'
        'Doctoral Degree'
        'Medical Degree'
        'Combined Degrees'
    """
    ids = _CODE_TO_IDS.get(code, [])
    return [_ID_TO_SECTION[id] for id in ids if id in _ID_TO_SECTION]


def get_subsections(parent_code: str) -> List[str]:
    """
    Get all subsection codes for a parent section.

    Args:
        parent_code: Parent section code (e.g., "S", "B", "M")

    Returns:
        List of subsection codes (e.g., ["S1", "S2", ..., "S9"])

    Example:
        >>> get_subsections("S")
        ['S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9']
    """
    section = get_section_by_code(parent_code)
    if section:
        return section.get('subsection_codes', [])
    return []


def validate_section_code(code: str) -> bool:
    """
    Check if a section code exists in the taxonomy.

    Args:
        code: Section code to validate

    Returns:
        True if code exists, False otherwise

    Example:
        >>> validate_section_code("P")
        True
        >>> validate_section_code("Z99")
        False
    """
    return code in _CODE_TO_SECTION


def code_to_canonical_name(code: str) -> Optional[str]:
    """
    Get the canonical display name for a section code.

    Args:
        code: Section code (e.g., "P", "S1")

    Returns:
        Canonical name or None if code not found

    Example:
        >>> code_to_canonical_name("P")
        'Institutional Administrative Activities'
        >>> code_to_canonical_name("S1")
        'Peer-reviewed Research Articles'
    """
    section = get_section_by_code(code)
    return section['canonical'] if section else None


def code_to_section_id(code: str) -> Optional[str]:
    """
    Get the primary section ID for a code.

    For codes that map to multiple IDs (like B1), returns the first one.
    Use get_all_sections_for_code() if you need all IDs.

    Args:
        code: Section code

    Returns:
        Primary section ID or None if code not found

    Example:
        >>> code_to_section_id("P")
        'institutional_administration'
    """
    section = get_section_by_code(code)
    return section['id'] if section else None


def section_id_to_code(section_id: str) -> Optional[str]:
    """
    Get the section code for a section ID (migration helper).

    Args:
        section_id: Section ID (e.g., "institutional_administration")

    Returns:
        Section code (e.g., "P") or None if not found

    Example:
        >>> section_id_to_code("institutional_administration")
        'P'
        >>> section_id_to_code("peer_reviewed_articles")
        'S1'
    """
    section = get_section_by_id(section_id)
    return section.get('section_code') if section else None


def get_parent_code(code: str) -> Optional[str]:
    """
    Get the parent section code for a subsection.

    Args:
        code: Subsection code (e.g., "S1", "B1")

    Returns:
        Parent code (e.g., "S", "B") or None if top-level section

    Example:
        >>> get_parent_code("S1")
        'S'
        >>> get_parent_code("S")  # Top-level section
        None
    """
    section = get_section_by_code(code)
    return section.get('parent_section_code') if section else None


def is_top_level(code: str) -> bool:
    """
    Check if a code represents a top-level section (no parent).

    Args:
        code: Section code

    Returns:
        True if top-level section, False if subsection

    Example:
        >>> is_top_level("S")
        True
        >>> is_top_level("S1")
        False
    """
    parent = get_parent_code(code)
    return parent is None


def get_all_top_level_codes() -> List[str]:
    """
    Get all top-level section codes (A-T).

    Returns:
        List of top-level codes in sorted order

    Example:
        >>> get_all_top_level_codes()
        ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L',
         'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T']
    """
    return sorted([
        code for code, section in _CODE_TO_SECTION.items()
        if section.get('parent_section_code') is None
    ])


def get_wcm_section_number(code: str) -> Optional[int]:
    """
    Get the WCM template section number for a code.

    Args:
        code: Section code

    Returns:
        WCM section number (1-19) or None if custom section

    Example:
        >>> get_wcm_section_number("P")
        16
        >>> get_wcm_section_number("T1")  # Custom section
        None
    """
    section = get_section_by_code(code)
    return section.get('wcm_section_number') if section else None


def is_wcm_required(code: str) -> bool:
    """
    Check if a section is required in the WCM template.

    Args:
        code: Section code

    Returns:
        True if required, False otherwise

    Example:
        >>> is_wcm_required("A")
        True
        >>> is_wcm_required("T1")
        False
    """
    section = get_section_by_code(code)
    return section.get('wcm_required', False) if section else False


def get_sections_by_wcm_number(wcm_number: int) -> List[Dict[str, Any]]:
    """
    Get all sections that belong to a WCM section number.

    Args:
        wcm_number: WCM section number (1-19)

    Returns:
        List of section dictionaries

    Example:
        >>> sections = get_sections_by_wcm_number(19)  # Bibliography
        >>> [s['section_code'] for s in sections]
        ['S', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9']
    """
    return [
        section for section in CV_SECTIONS
        if section.get('wcm_section_number') == wcm_number
    ]


# Statistics and debugging functions

def get_taxonomy_stats() -> Dict[str, Any]:
    """
    Get statistics about the taxonomy.

    Returns:
        Dictionary with taxonomy statistics
    """
    top_level = get_all_top_level_codes()
    subsection_count = sum(len(get_subsections(code)) for code in top_level)

    return {
        'total_sections': len(CV_SECTIONS),
        'unique_codes': len(_CODE_TO_SECTION),
        'top_level_codes': len(top_level),
        'top_level_list': top_level,
        'subsection_count': subsection_count,
        'wcm_official_sections': len([s for s in CV_SECTIONS if s.get('wcm_section_number')]),
        'custom_sections': len([s for s in CV_SECTIONS if not s.get('wcm_section_number')]),
        'shared_codes': {
            code: ids for code, ids in _CODE_TO_IDS.items() if len(ids) > 1
        }
    }


def print_taxonomy_tree():
    """Print a tree view of the taxonomy for debugging."""
    print("WCM CV Taxonomy (Section Codes)")
    print("=" * 70)

    for code in get_all_top_level_codes():
        section = get_section_by_code(code)
        wcm_num = section.get('wcm_section_number', 'Custom')
        print(f"\n{code}: {section['canonical']} [WCM {wcm_num}]")

        subsections = get_subsections(code)
        for sub_code in subsections:
            sub_section = get_section_by_code(sub_code)
            if sub_section:
                print(f"  ├─ {sub_code}: {sub_section['canonical']}")

                # Check for multiple IDs mapped to same code
                all_sections = get_all_sections_for_code(sub_code)
                if len(all_sections) > 1:
                    for i, s in enumerate(all_sections[1:], 1):
                        print(f"  │  └─ Also: {s['canonical']}")


# Module initialization message
logger.debug(f"Taxonomy utils loaded: {len(_CODE_TO_SECTION)} section codes indexed")
