"""
Valid WCM Taxonomy Codes for Enum Validation

This module defines all valid taxonomy codes for the WCM CV structure.
Used by taxonomy_mapper_v2.py for strict enum validation in JSON schemas.

Generated: 2025-11-20
Purpose: Eliminate 15% invalid taxonomy code error rate
"""

# Parent section codes (A-T, 20 sections)
VALID_PARENT_CODES = [
    'A',  # Personal Data / Contact Information
    'B',  # Education
    'C',  # Postdoctoral Training
    'D',  # Professional Positions & Employment
    'E',  # Licensure and Certification
    'F',  # (Reserved)
    'G',  # Institutional / Hospital Affiliation
    'H',  # Honors and Awards
    'I',  # Professional Organizations
    'J',  # Percent Effort & Institutional Responsibilities
    'K',  # Educational Contributions
    'L',  # (Reserved)
    'M',  # Research
    'N',  # Mentoring
    'O',  # Institutional Administrative Activities
    'P',  # Clinical Practice
    'Q',  # Extramural Professional Responsibilities
    'R',  # Invitations to Speak/Present
    'S',  # Bibliography
    'T',  # Appendix / Other
]

# Child section codes (subsections within parent sections)
VALID_CHILD_CODES = [
    # A - Personal Data / Contact Information
    'A1',  # Name
    'A2',  # Email Address
    'A3',  # Phone Numbers

    # B - Education
    'B1',  # Undergraduate Education
    'B2',  # Graduate Education (Master's, PhD, ScD, MD, DO)

    # C - Postdoctoral Training
    'C1',  # Postdoctoral Research Positions
    'C2',  # Residency Training
    'C3',  # Fellowship Training

    # D - Professional Positions & Employment
    'D1',  # Current Academic Appointments
    'D2',  # Previous Academic Appointments

    # E - Licensure and Certification (no subsections)

    # F - Reserved (no subsections)

    # G - Institutional / Hospital Affiliation (no subsections)

    # H - Honors and Awards (no subsections)

    # I - Professional Organizations (no subsections)

    # J - Percent Effort & Institutional Responsibilities (no subsections)

    # K - Educational Contributions
    'K1',  # Group Teaching
    'K2',  # Curriculum Development
    'K3',  # Clinical Teaching
    'K4',  # Other Educational Contributions

    # L - Reserved (no subsections)

    # M - Research
    'M1',  # Research Activities / Mission Statement
    'M2',  # Research Support (Grants - parent)
    'M2A', # Current Research Funding (active grants)
    'M2B', # Past Research Funding (completed grants)
    'M2C', # Pending Research Funding (submitted grants)
    'M2D', # Patents & Innovations

    # N - Mentoring
    'N1',  # Current Mentees
    'N3',  # Past Mentees
    'N4',  # Mentoring Philosophy/Statement

    # O - Institutional Administrative Activities (no subsections)

    # P - Clinical Practice (no subsections)

    # Q - Extramural Professional Responsibilities
    'Q1',  # Professional Service
    'Q2',  # Editorial Activities
    'Q3',  # Other Extramural Activities

    # R - Invitations to Speak/Present
    'R1',  # Invited Talks/Lectures
    'R2',  # Conference Presentations

    # S - Bibliography
    'S1',  # Peer-Reviewed Research Articles
    'S2',  # Books and Monographs
    'S3',  # Book Chapters
    'S4',  # Reviews, Editorials, and Letters
    'S6',  # Conference Abstracts and Posters
    'S7',  # Non-Peer-Reviewed Publications
    'S8',  # Other Publications

    # T - Appendix / Other
    'T5',  # Other
]

# All valid codes (parent + child)
VALID_ALL_CODES = sorted(set(VALID_PARENT_CODES + VALID_CHILD_CODES))

# Sections with no subsections (Pass 2 not needed)
SECTIONS_WITHOUT_SUBSECTIONS = ['E', 'F', 'G', 'H', 'I', 'J', 'L', 'O', 'P']

# Mapping of parent to valid children
PARENT_TO_CHILDREN = {
    'A': ['A1', 'A2', 'A3'],
    'B': ['B1', 'B2'],
    'C': ['C1', 'C2', 'C3'],
    'D': ['D1', 'D2'],
    'E': [],  # No subsections
    'F': [],  # Reserved
    'G': [],  # No subsections
    'H': [],  # No subsections
    'I': [],  # No subsections
    'J': [],  # No subsections
    'K': ['K1', 'K2', 'K3', 'K4'],
    'L': [],  # Reserved
    'M': ['M1', 'M2', 'M2A', 'M2B', 'M2C', 'M2D'],
    'N': ['N1', 'N3', 'N4'],
    'O': [],  # No subsections
    'P': [],  # No subsections
    'Q': ['Q1', 'Q2', 'Q3'],
    'R': ['R1', 'R2'],
    'S': ['S1', 'S2', 'S3', 'S4', 'S6', 'S7', 'S8'],
    'T': ['T5'],
}

def validate_taxonomy_code(code: str, parent_code: str = None) -> bool:
    """
    Validate that a taxonomy code is valid.

    Args:
        code: Taxonomy code to validate (e.g., 'A', 'S1')
        parent_code: Optional parent code for child validation (e.g., 'S' for 'S1')

    Returns:
        True if code is valid, False otherwise
    """
    # Check if it's a valid code at all
    if code not in VALID_ALL_CODES:
        return False

    # If parent specified, check if child belongs to parent
    if parent_code and len(code) > 1:
        return code in PARENT_TO_CHILDREN.get(parent_code, [])

    return True

def get_valid_children(parent_code: str) -> list:
    """
    Get list of valid child codes for a parent section.

    Args:
        parent_code: Parent section code (e.g., 'S')

    Returns:
        List of valid child codes (e.g., ['S1', 'S2', ...])
    """
    return PARENT_TO_CHILDREN.get(parent_code, [])

def has_subsections(parent_code: str) -> bool:
    """
    Check if a parent section has subsections.

    Args:
        parent_code: Parent section code (e.g., 'S')

    Returns:
        True if section has subsections, False otherwise
    """
    return parent_code not in SECTIONS_WITHOUT_SUBSECTIONS
