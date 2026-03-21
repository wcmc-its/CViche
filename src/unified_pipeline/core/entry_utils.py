"""
Utilities for standardizing CV entry formats across the pipeline.

This module provides helpers to convert between legacy string entries and modern
dictionary entries, ensuring consistent data structures throughout the pipeline.
"""

from typing import Union, List, Dict, Any
import logging

logger = logging.getLogger(__name__)


def normalize_entry(entry: Union[str, Dict[str, Any]]) -> Dict[str, Any]:
    """
    Convert any entry format to the standardized dictionary format.

    Args:
        entry: Either a string (legacy format) or dict (modern format)

    Returns:
        Dictionary with standardized fields:
        - text_snippet: The entry text
        - entry_type: Type classification (or 'unknown' for legacy)
        - confidence: Confidence score (0.0 for legacy)

    Examples:
        >>> normalize_entry("MD, Yale, 2013")
        {'text_snippet': 'MD, Yale, 2013', 'entry_type': 'unknown', 'confidence': 0.0}

        >>> normalize_entry({'text_snippet': 'MD, Yale, 2013', 'entry_type': 'education', 'confidence': 1.0})
        {'text_snippet': 'MD, Yale, 2013', 'entry_type': 'education', 'confidence': 1.0}
    """
    if isinstance(entry, str):
        # Legacy string format - convert to modern format
        logger.debug(f"Converting legacy string entry to dict format: {entry[:50]}...")
        return {
            'text_snippet': entry,
            'entry_type': 'unknown',
            'confidence': 0.0
        }
    elif isinstance(entry, dict):
        # Already modern format - ensure required fields exist
        if 'text_snippet' not in entry:
            logger.warning(f"Dict entry missing 'text_snippet' field: {entry}")
            entry['text_snippet'] = str(entry)
        if 'entry_type' not in entry:
            entry['entry_type'] = 'unknown'
        if 'confidence' not in entry:
            entry['confidence'] = 0.0
        return entry
    else:
        # Unexpected type - convert to string then dict
        logger.warning(f"Unexpected entry type {type(entry)}: {entry}")
        return {
            'text_snippet': str(entry),
            'entry_type': 'unknown',
            'confidence': 0.0
        }


def normalize_entries(entries: List[Union[str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """
    Normalize a list of entries to standardized dictionary format.

    Args:
        entries: List of entries in any format

    Returns:
        List of normalized dictionary entries

    Examples:
        >>> normalize_entries(["MD, Yale", {"text_snippet": "PhD, MIT", "entry_type": "education"}])
        [
            {'text_snippet': 'MD, Yale', 'entry_type': 'unknown', 'confidence': 0.0},
            {'text_snippet': 'PhD, MIT', 'entry_type': 'education', 'confidence': 0.0}
        ]
    """
    return [normalize_entry(entry) for entry in entries]


def extract_text_snippets(entries: List[Union[str, Dict[str, Any]]]) -> List[str]:
    """
    Extract text snippets from entries, handling both formats.

    This is a convenience function for code that only needs the text content.

    Args:
        entries: List of entries in any format

    Returns:
        List of text strings

    Examples:
        >>> extract_text_snippets(["MD, Yale", {"text_snippet": "PhD, MIT"}])
        ['MD, Yale', 'PhD, MIT']
    """
    result = []
    for entry in entries:
        if isinstance(entry, str):
            result.append(entry)
        elif isinstance(entry, dict):
            result.append(entry.get('text_snippet', ''))
        else:
            result.append(str(entry))
    return result


def is_modern_format(entry: Any) -> bool:
    """
    Check if an entry uses the modern dictionary format.

    Args:
        entry: Entry to check

    Returns:
        True if entry is a dict with required fields
    """
    return isinstance(entry, dict) and 'text_snippet' in entry


def validate_entry_structure(entry: Dict[str, Any], strict: bool = False) -> bool:
    """
    Validate that an entry has the expected structure.

    Args:
        entry: Entry dictionary to validate
        strict: If True, require all optional fields

    Returns:
        True if valid, False otherwise

    Logs warnings for invalid structures.
    """
    if not isinstance(entry, dict):
        logger.warning(f"Entry is not a dictionary: {type(entry)}")
        return False

    # Required fields
    if 'text_snippet' not in entry:
        logger.warning("Entry missing required 'text_snippet' field")
        return False

    # Optional but recommended fields
    if strict:
        if 'entry_type' not in entry:
            logger.warning("Entry missing 'entry_type' field (strict mode)")
            return False
        if 'confidence' not in entry:
            logger.warning("Entry missing 'confidence' field (strict mode)")
            return False

    return True
