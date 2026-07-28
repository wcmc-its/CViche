"""Reading structure out of raw CV text (#398).

Six methods on WCMTemplateGenerator that never touched `self` -- free functions
by behaviour, methods only by where they happened to be written. Each takes text
(or an entry's text) and answers a question about its shape: which year does it
mention, is it a table header rather than data, is it a structural label the WCM
template already provides, how many memberships are fused into it.

The boundary that matters: nothing here builds output. These functions read, they
do not write -- no python-docx object is touched, no WCM section is chosen, no
value is reformatted for display. That is what makes them safe to call from any
section writer and trivial to test with a string literal.

Names keep their leading underscore deliberately. Renaming and relocating in the
same change means a failure cannot be attributed to either; the rename is a
separate, mechanical follow-up.
"""
import re
from typing import Dict, List, Optional, Tuple

def _extract_name_from_uid(uid: str) -> str:
    """Extract formatted name from document UID."""
    # Remove year prefix (e.g., "2015_Wende" -> "Wende")
    parts = uid.replace('CV_', '').split('_')

    # Filter out year
    parts = [p for p in parts if not p.isdigit() and len(p) > 2]

    if len(parts) >= 2:
        # Assume "First_Last" or "Last_First"
        return ' '.join(parts).title()
    elif parts:
        return parts[0].title()
    return uid


def _extract_last_name_from_uid(uid: str) -> str:
    """Extract last name from document UID for author matching."""
    # Remove year prefix (e.g., "2015_Wende" -> "Wende")
    parts = uid.replace('CV_', '').split('_')

    # Filter out years and very short parts
    parts = [p for p in parts if not p.isdigit() and len(p) > 2]

    if parts:
        # Last part is typically the last name
        last_name = parts[-1]
        # Handle cases like "Albrechtjs" -> "Albrecht" (initials appended)
        if len(last_name) > 5:
            # Check if last 2-3 chars look like initials
            for suffix_len in [2, 3]:
                suffix = last_name[-suffix_len:]
                if suffix.islower() or suffix.isupper():
                    base = last_name[:-suffix_len]
                    if len(base) >= 3:
                        return base.title()
        return last_name.title()
    return ''


def _extract_year_from_text(text: str) -> Optional[str]:
    """Extract year from raw text as fallback when not in extracted_fields.

    Looks for patterns like:
    - "August 2021"
    - "December 2017"
    - "May 2015"
    - "(2021)"
    - "2019-2021"
    """
    if not text:
        return None

    # Pattern 1: Month Year (e.g., "August 2021", "December 2017")
    month_year = re.search(r'(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})', text)
    if month_year:
        return month_year.group(2)

    # Pattern 2: Year in parentheses at end (e.g., "(2021)")
    paren_year = re.search(r'\((\d{4})\)\s*$', text)
    if paren_year:
        return paren_year.group(1)

    # Pattern 3: Year range - take the end year (e.g., "2019-2021")
    year_range = re.search(r'(\d{4})\s*[-–—]\s*(\d{4})', text)
    if year_range:
        return year_range.group(2)

    # Pattern 4: Single year in text
    single_year = re.search(r'\b(19\d{2}|20\d{2})\b', text)
    if single_year:
        return single_year.group(1)

    return None


def _is_table_header_entry(text: str, header_keywords: List[str], threshold: int = 2) -> bool:
    """Detect if an entry is actually a table header that was mistakenly extracted as data.

    Table headers are characterized by:
    - Multiple header-like words (e.g., "Name of award", "Date", "Organization")
    - Tab or pipe-separated columns
    - No substantive content (just column labels)

    Args:
        text: The entry text to check
        header_keywords: List of keywords that typically appear in headers for this section
        threshold: Minimum number of header keywords required to classify as header

    Returns:
        True if this appears to be a table header, False otherwise
    """
    if not text:
        return False

    # Normalize text for checking
    text_lower = text.lower().strip()

    # If text is very short, it might be header-like
    # But only if it matches header patterns
    if len(text_lower) < 100:
        # Count how many header keywords appear
        keyword_count = sum(1 for kw in header_keywords if kw.lower() in text_lower)

        # Check for common header patterns
        header_patterns = [
            r'\bname\s+of\s+',  # "Name of award", "Name of organization"
            r'\bdate\s*(awarded|received|of|issued)?\b',  # "Date awarded", "Date of issue"
            r'\b(organization|institution)\s*(name)?\b',  # "Organization", "Institution name"
            r'\btitle\b.*\b(institution|organization|dates?)\b',  # "Title | Institution | Dates"
            r'\bdates?\s*\(?[mdy/]+\)?',  # "Dates (mm/yy)"
        ]

        pattern_matches = sum(1 for p in header_patterns if re.search(p, text_lower))

        # If multiple header keywords AND pattern matches, likely a header
        if keyword_count >= threshold and pattern_matches >= 1:
            return True

        # Also check for tab/pipe-separated header-only content
        if ('\t' in text or '|' in text):
            parts = re.split(r'[\t|]', text_lower)
            # If all parts are short and most match header keywords, it's a header
            if all(len(p.strip()) < 30 for p in parts if p.strip()):
                parts_matching = sum(1 for p in parts if any(kw in p for kw in header_keywords))
                if parts_matching >= len(parts) * 0.5:
                    return True

    return False


def _is_structural_label(entry: Dict) -> bool:
    """Check if an entry is a structural label from the source CV rather than actual content.

    Source CVs contain section headers, sub-headers, and structural labels
    (e.g., "CLINICAL PRACTICE ACTIVITIES", "Direct Teaching/Precepting/Supervision")
    that sometimes get extracted as entries. These should not appear as content
    in the WCM output — the WCM template provides its own structure.

    Checks:
    1. All-caps text longer than 3 characters (section headers)
    2. Entry text that exactly matches one of its own hierarchy labels
    """
    text = (entry.get('text', '') or '').strip()
    if not text:
        return True

    # All-caps text (section headers like "CLINICAL PRACTICE ACTIVITIES")
    if text == text.upper() and len(text) > 3 and not any(c.isdigit() for c in text):
        return True

    # Text that exactly matches one of its hierarchy labels
    hierarchy = entry.get('hierarchy', [])
    for label in hierarchy:
        if text.strip().lower() == label.strip().lower():
            return True

    return False


def _parse_multi_membership_entry(lines: List[str]) -> List[Tuple[str, str, str]]:
    """Parse multiple memberships from merged entry lines.

    Handles patterns like:
    - "Member | Org1 | date1" per line
    - "Member\\nElected Member | Org1\\nOrg2 | date1\\ndate2"

    Returns:
        List of (membership_type, organization, dates) tuples
    """
    memberships = []
    membership_types = []
    organizations = []
    dates = []

    # Common membership type indicators
    membership_keywords = ['member', 'fellow', 'diplomat', 'associate', 'elected', 'honorary']
    date_pattern = re.compile(r'^(\d{1,2}/?\d{0,4}\s*-\s*(?:present|\d{1,2}/?\d{0,4}))$|^(\d{4}\s*-\s*(?:present|\d{4}))$', re.IGNORECASE)

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Check if line has pipe separators (structured format)
        if '|' in line:
            parts = [p.strip() for p in line.split('|')]
            for part in parts:
                if not part:
                    continue
                if any(kw in part.lower() for kw in membership_keywords) and len(part.split()) <= 3:
                    membership_types.append(part)
                elif date_pattern.match(part) or re.match(r'^\d{1,2}/\d{4}', part):
                    dates.append(part)
                else:
                    organizations.append(part)
        else:
            # No pipe - classify by content
            if any(kw in line.lower() for kw in membership_keywords) and len(line.split()) <= 3:
                membership_types.append(line)
            elif date_pattern.match(line) or re.match(r'^\d{1,2}/\d{4}', line):
                dates.append(line)
            elif len(line) > 5:  # Likely organization name
                organizations.append(line)

    # Match up memberships - pair organizations with types and dates
    if organizations:
        for i, org in enumerate(organizations):
            mem_type = membership_types[i] if i < len(membership_types) else ''
            date = dates[i] if i < len(dates) else ''
            memberships.append((mem_type, org, date))

    return memberships
