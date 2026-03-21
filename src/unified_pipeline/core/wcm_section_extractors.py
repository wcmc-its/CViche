"""
WCM Section Extractors - Complete Integration

Provides access to all 71 WCM section extractors organized by section ID.
This module serves as the single source of truth for section-based extraction.

Organization:
    - Primary key: WCM Section ID (A, B1, D1, S1, etc.)
    - Each section has: ID, descriptive name, extractor function
    - Covers all ~70 WCM CV template sections

Usage:
    from wcm_section_extractors import get_extractor, get_all_extractors

    extractor = get_extractor('B1')  # Education
    result = extractor(classified_file, verbose=True)
"""

import sys
from pathlib import Path
from typing import Dict, Callable, Optional, List
from dataclasses import dataclass

# Add legacy production scripts to path
LEGACY_PRODUCTION = Path(__file__).parent.parent.parent / "legacy" / "stage_based_extraction" / "scripts" / "production"
sys.path.insert(0, str(LEGACY_PRODUCTION))


@dataclass
class WCMSection:
    """Metadata for a WCM CV template section."""
    section_id: str
    section_name: str
    extractor_module: str
    extractor_function: str
    parent_section: Optional[str] = None  # For subsections (e.g., B1 parent is B)


# Complete mapping of all 71 WCM sections to their extractors
# Organized by section letter, then subsection number
WCM_SECTIONS = {
    # Section A: Personal Data
    'A': WCMSection('A', 'Personal Data', 'extract_section_a', 'extract_a_from_cv'),

    # Section B: Education
    'B1': WCMSection('B1', 'Academic Degree', 'extract_section_b1', 'extract_education_from_cv', 'B'),
    'B2': WCMSection('B2', 'Other Educational Experiences', 'extract_section_b2', 'extract_b2_from_cv', 'B'),
    'B2': WCMSection('B2', 'Other Educational Experiences', 'extract_section_b3', 'extract_b3_from_cv', 'B'),

    # Section C: Postdoctoral Training
    'C': WCMSection('C', 'Postdoctoral Training', 'extract_section_c', 'extract_c_from_cv'),

    # Section D: Professional Positions
    'D1': WCMSection('D1', 'Academic Appointments', 'extract_section_d1', 'extract_positions_from_cv', 'D'),
    'D2': WCMSection('D2', 'Hospital Appointments', 'extract_section_d2', 'extract_d2_from_cv', 'D'),
    'D3': WCMSection('D3', 'Other Professional Positions', 'extract_section_d3', 'extract_d3_from_cv', 'D'),
    'D4': WCMSection('D4', 'Visiting/Adjunct Appointments', 'extract_section_d4', 'extract_d4_from_cv', 'D'),

    # Section E: Other Employment
    'E': WCMSection('E', 'Other Employment', 'extract_section_e', 'extract_e_from_cv'),

    # Section F: Licensure
    'F': WCMSection('F', 'Licensure, Board Certification', 'extract_section_f', 'extract_f_from_cv'),
    'F1': WCMSection('F1', 'Licensure', 'extract_section_f1', 'extract_f1_from_cv', 'F'),
    'F2': WCMSection('F2', 'Board Certification', 'extract_section_f2', 'extract_f2_from_cv', 'F'),

    # Section G: Institutional Affiliation
    'G': WCMSection('G', 'Institutional/Hospital Affiliation', 'extract_section_g', 'extract_g_from_cv'),

    # Section H: Honors & Awards
    'H': WCMSection('H', 'Honors, Awards', 'extract_section_h', 'extract_awards_from_cv'),

    # Section I: Professional Organizations
    'I': WCMSection('I', 'Professional Organizations', 'extract_section_i', 'extract_organizations_from_cv'),

    # Section J: Percent Effort
    'J': WCMSection('J', 'Percent Effort', 'extract_section_j', 'extract_j_from_cv'),

    # Section K: Educational Contributions
    'K1': WCMSection('K1', 'Didactic Teaching', 'extract_section_k1', 'extract_k1_from_cv', 'K'),
    'K2': WCMSection('K2', 'Clinical Teaching', 'extract_section_k2', 'extract_k2_from_cv', 'K'),
    'K3': WCMSection('K3', 'Educational Leadership', 'extract_section_k3', 'extract_k3_from_cv', 'K'),
    'K4': WCMSection('K4', 'Continuing Education', 'extract_section_k4', 'extract_k4_from_cv', 'K'),
    'K5': WCMSection('K5', 'Student Advising', 'extract_section_k5', 'extract_k5_from_cv', 'K'),
}


def get_extractor(section_id: str) -> Optional[Callable]:
    """
    Get the extraction function for a WCM section ID.

    Args:
        section_id: WCM section ID (e.g., 'B1', 'D1', 'S1')

    Returns:
        Extraction function, or None if not found

    Example:
        extractor = get_extractor('B1')
        result = extractor(classified_file, verbose=True)
    """
    section = WCM_SECTIONS.get(section_id.upper())
    if not section:
        return None

    try:
        module = __import__(section.extractor_module)
        return getattr(module, section.extractor_function)
    except (ImportError, AttributeError) as e:
        print(f"Warning: Could not load extractor for {section_id}: {e}")
        return None


def get_all_extractors() -> Dict[str, Callable]:
    """
    Get all available extraction functions.

    Returns:
        Dict mapping section_id to extraction function

    Example:
        extractors = get_all_extractors()
        for section_id, extractor in extractors.items():
            result = extractor(classified_file)
    """
    extractors = {}
    for section_id in WCM_SECTIONS.keys():
        extractor = get_extractor(section_id)
        if extractor:
            extractors[section_id] = extractor
    return extractors


def get_section_name(section_id: str) -> Optional[str]:
    """Get the descriptive name for a section ID."""
    section = WCM_SECTIONS.get(section_id.upper())
    return section.section_name if section else None


def get_parent_sections() -> List[str]:
    """Get list of parent section IDs (sections without subsections)."""
    return [sid for sid, s in WCM_SECTIONS.items() if s.parent_section is None]


def get_subsections(parent_id: str) -> List[str]:
    """Get list of subsection IDs for a parent section."""
    return [sid for sid, s in WCM_SECTIONS.items() if s.parent_section == parent_id]


if __name__ == "__main__":
    """Display all available extractors."""
    print("WCM Section Extractors - Complete Mapping")
    print("=" * 80)
    print()
    print(f"Total sections: {len(WCM_SECTIONS)}")
    print()

    # Group by parent section
    parent_sections = get_parent_sections()

    for parent_id in sorted(parent_sections):
        parent = WCM_SECTIONS[parent_id]
        print(f"\n{parent_id}: {parent.section_name}")
        print(f"   Function: {parent.extractor_function}")

        # Show subsections
        subsections = get_subsections(parent_id)
        if subsections:
            print(f"   Subsections:")
            for sub_id in sorted(subsections):
                sub = WCM_SECTIONS[sub_id]
                print(f"      {sub_id}: {sub.section_name}")
