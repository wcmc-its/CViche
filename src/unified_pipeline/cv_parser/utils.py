"""
Utility functions for CV parsing
"""
import re
from typing import List, Dict, Optional


def clean_text(text: str) -> str:
    """Clean and normalize text."""
    # Remove excessive whitespace
    text = re.sub(r'\s+', ' ', text)
    # Remove non-printable characters
    text = ''.join(char for char in text if char.isprintable() or char in '\n\t')
    return text.strip()


def is_section_header(text: str, min_length: int = 5, max_length: int = 100) -> bool:
    """
    Determine if a line of text is likely a section header.

    Args:
        text: Text to check
        min_length: Minimum length for header
        max_length: Maximum length for header

    Returns:
        True if likely a section header
    """
    text = text.strip()

    # Check length bounds
    if len(text) < min_length or len(text) > max_length:
        return False

    # Check if all uppercase (common for section headers)
    if text.isupper():
        return True

    # Check if matches common header patterns
    header_patterns = [
        r'^[A-Z][A-Za-z\s,&-]+:$',  # Title Case with colon
        r'^\d+\.\s+[A-Z][A-Za-z\s,&-]+$',  # Numbered sections
        r'^[IVX]+\.\s+[A-Z][A-Za-z\s,&-]+$',  # Roman numeral sections
    ]

    for pattern in header_patterns:
        if re.match(pattern, text):
            return True

    return False


def extract_dates(text: str) -> List[str]:
    """Extract date patterns from text."""
    date_patterns = [
        r'\b\d{4}\b',  # Year only (2020)
        r'\b\d{1,2}/\d{4}\b',  # MM/YYYY
        r'\b\d{1,2}/\d{1,2}/\d{4}\b',  # MM/DD/YYYY
        r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}\b',  # Month YYYY
    ]

    dates = []
    for pattern in date_patterns:
        dates.extend(re.findall(pattern, text, re.IGNORECASE))

    return dates


def chunk_text(text: str, max_chunk_size: int = 4000) -> List[str]:
    """
    Split text into chunks for LLM processing.

    Args:
        text: Text to chunk
        max_chunk_size: Maximum size per chunk

    Returns:
        List of text chunks
    """
    if len(text) <= max_chunk_size:
        return [text]

    # Split by paragraphs first
    paragraphs = text.split('\n\n')
    chunks = []
    current_chunk = ""

    for para in paragraphs:
        if len(current_chunk) + len(para) + 2 <= max_chunk_size:
            current_chunk += para + "\n\n"
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = para + "\n\n"

    if current_chunk:
        chunks.append(current_chunk.strip())

    return chunks


def normalize_section_name(section_name: str, section_aliases: Dict[str, List[str]]) -> Optional[str]:
    """
    Normalize a section name to its canonical form.

    Args:
        section_name: Raw section name from CV
        section_aliases: Dictionary of canonical names to aliases

    Returns:
        Canonical section name or None if no match
    """
    section_name_clean = section_name.strip().upper()

    # Direct match
    for canonical, aliases in section_aliases.items():
        if section_name_clean in [a.upper() for a in aliases]:
            return aliases[0]  # Return first (canonical) name

    # Fuzzy match on keywords
    for canonical, aliases in section_aliases.items():
        for alias in aliases:
            if alias.upper() in section_name_clean or section_name_clean in alias.upper():
                return aliases[0]

    return None


def format_phone(phone: str) -> str:
    """Format phone number consistently."""
    # Remove all non-numeric characters
    digits = re.sub(r'\D', '', phone)

    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    elif len(digits) == 11 and digits[0] == '1':
        return f"+1 ({digits[1:4]}) {digits[4:7]}-{digits[7:]}"
    else:
        return phone  # Return original if format unclear


def extract_email(text: str) -> Optional[str]:
    """Extract email address from text."""
    email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    matches = re.findall(email_pattern, text)
    return matches[0] if matches else None


def is_likely_publication(text: str) -> bool:
    """Check if text is likely a publication citation."""
    publication_indicators = [
        r'\bDOI\b',
        r'\bPMID\b',
        r'\bet al\b',
        r'\bJ\s+[A-Z][a-z]+',  # Journal abbreviations
        r'\d{4};\d+\(',  # Volume/issue pattern
        r':\d+-\d+',  # Page numbers
    ]

    for pattern in publication_indicators:
        if re.search(pattern, text, re.IGNORECASE):
            return True

    return False
