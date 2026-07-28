"""Deciding whose CV this is (#398).

Same responsibility as `institution.py`, different subject: the owner's name is
needed to auto-fill PI fields, and it may arrive as a structured record, or only
as a document uid like "2015_Wende", or not at all.
"""
import re
from typing import Dict


def _get_cv_owner_name(cv_owner: Dict = None, document_uid: str = '') -> str:
    """Extract the CV owner's full name for auto-filling PI fields.

    Args:
        cv_owner: Dict with keys like 'last_name', 'first_name', etc.
        document_uid: Document UID like "2015_Wende" to extract name from

    Returns:
        Full name string (e.g., "Adam Wende") or last name if first not available
    """
    if cv_owner:
        first = cv_owner.get('first_name', '')
        last = cv_owner.get('last_name', '')
        if first and last:
            return f"{first} {last}"
        elif last:
            return last

    # Fall back to extracting from document_uid
    if document_uid:
        # Handle patterns like "2015_Wende" or "2003_Albrechtjs_Cv"
        parts = document_uid.split('_')
        if len(parts) >= 2:
            # Second part is usually the name
            name_part = parts[1]
            # Remove common suffixes
            name_part = re.sub(r'(js|cv|CV|Cv)$', '', name_part, flags=re.IGNORECASE)
            # Capitalize properly
            return name_part.capitalize()

    return ''
