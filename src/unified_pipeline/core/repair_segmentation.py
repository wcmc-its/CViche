"""
Segmentation Repair - Fix Structural Issues in Segmented CVs

Addresses ChatGPT-identified structural issues in Word CV segmentation:

Priority Fixes (Blakely):
  1. Header scoping / false subgroups - ALL-CAPS headers should start new top-level groups
  2. Education ladder drift - Degree types should constrain routing
  3. Funding split - Numbered grants should stay within FUNDING section
  4. Grants mis-nesting - Children should inherit parent section
  5. Preprints child routing - Talk-like entries under S10 should be promoted
  6. Contact fragmentation - Consecutive contact lines should be merged

This is a structural pre-processing step that runs AFTER segmentation
and BEFORE taxonomy mapping to improve downstream classification accuracy.

Usage:
    python repair_segmentation.py input_segmented.json output_repaired.json

    # Or as module:
    from repair_segmentation import repair_segmentation
    repaired_cv = repair_segmentation(segmented_cv)
"""

import json
import re
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Set
from copy import deepcopy

from unified_pipeline.llm_client import call_llm
from datetime import datetime


# ============================================================================
# DETECTION PATTERNS
# ============================================================================

# CV Header patterns - for demoting document-level metadata
CV_HEADER_PATTERNS = [
    r'^CURRICULUM\s+VITAE$',
    r'^CURRICULUM\s+VITA$',
    r'^CV$',
    r'^RESUME$',
    r'^VITAE$',
    r'^C\.V\.$',
]

CONTACT_PATTERNS = [
    r'^https?://',  # URL
    r'[\w\.-]+@[\w\.-]+\.\w+',  # Email - improved to capture more formats
    r'^\d+\s+\w+\s+(Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Boulevard|Blvd)',  # Street address
    r'^Room\s+\d+|^Rm\s+\d+',  # Room number
    r',\s*[A-Z]{2}\s+\d{5}',  # City, State ZIP
    r'^\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})$',  # Phone - improved to capture (555) 123-4567, 555-123-4567, 555.123.4567
    r'^(\d{3})[-.\s]?(\d{4})$',  # Short phone - 555-1234
    r'(University|Institute|School|College|Hospital|Medical Center|Laboratory|Lab|Dept|Department)(?!\s+of)',  # Institution names
    r'^[A-Z][a-z]+,\s+[A-Z]{2}\s+\d{5}',  # City, STATE ZIP
    r'\b(?:phone|tel|telephone|mobile|cell|fax)\s*:',  # Phone/fax keywords
    r'^(?:Name|Full Name|Author)\s*[\|:]',  # NEW FIX #9: Name header pattern like "Name | John Doe, MD"
    r',\s*(?:MD|PhD|MPH|RN|DDS|DMD|MBA|MS|MA|JD|DO|PharmD)\s*$',  # Professional credentials at end of name
]

# NEW FIX #11: Institutional affiliation sequence patterns
INSTITUTIONAL_AFFILIATION_KEYWORDS = [
    'department', 'dept', 'division', 'section', 'unit',
    'college', 'school', 'faculty',
    'university', 'institute', 'academy',
    'hospital', 'medical center', 'clinic', 'health system',
    'laboratory', 'lab', 'center', 'centre'
]

ALL_CAPS_PATTERN = r'^[A-Z0-9&/\-\s,\'()]+$'

FUNDING_KEYWORDS = ['FUNDING', 'GRANTS', 'SUPPORT', 'AWARDS']
EDUCATION_KEYWORDS = ['EDUCATION', 'TRAINING', 'DEGREE']

NUMBERED_LINE_PATTERN = r'^\s*\d+\.'

# Education degree type patterns
UNDERGRAD_KEYWORDS = ['bachelor', 'b.s.', 'b.a.', 'undergraduate', 'college']
GRAD_KEYWORDS = ['master', 'm.s.', 'm.a.', 'graduate', 'doctoral', 'ph.d.', 'phd']
POSTDOC_KEYWORDS = ['postdoc', 'post-doc', 'fellow', 'fellowship', 'hhmi']
HIGHSCHOOL_KEYWORDS = ['high school', 'secondary school']


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def is_all_caps_header(label: str, allow_single_word: bool = False) -> bool:
    """
    Check if label is ALL CAPS (likely a section header).

    PHASE 1 FIX #3: Enhanced to catch single-word ALL-CAPS headers.

    Args:
        label: The label text to check
        allow_single_word: If True, allow single-word ALL-CAPS (with min length 4)

    Returns:
        True if label is an ALL-CAPS header
    """
    if not label or len(label) < 3:
        return False

    # EXCLUDE numbered lines (grants, etc.) - they're not headers
    if re.match(NUMBERED_LINE_PATTERN, label.strip()):
        return False

    # Remove common punctuation and check
    cleaned = re.sub(r'[—–\-:.,()&/]', ' ', label).strip()
    words = cleaned.split()

    # PHASE 1 FIX #3: Allow single-word ALL-CAPS with minimum length
    if len(words) == 1:
        if not allow_single_word:
            return False
        # Single word must be at least 4 chars to avoid spurious matches (e.g., "CV", "MD")
        if len(cleaned) < 4:
            return False

    # Must have at least 1 word (was 2, now allows single with flag)
    if len(words) < 1:
        return False

    return bool(re.match(ALL_CAPS_PATTERN, cleaned))


def is_cv_header(label: str) -> bool:
    """Check if label is a CV document header (e.g., 'CURRICULUM VITAE')."""
    label_normalized = label.strip().upper()
    for pattern in CV_HEADER_PATTERNS:
        if re.match(pattern, label_normalized, re.IGNORECASE):
            return True
    return False


def is_contact_line(text: str) -> bool:
    """Check if text looks like contact information."""
    for pattern in CONTACT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def is_numbered_line(text: str) -> bool:
    """Check if line starts with a number (e.g., '1. Grant title')."""
    return bool(re.match(NUMBERED_LINE_PATTERN, text))


def contains_funding_keyword(label: str) -> bool:
    """Check if label contains funding-related keywords."""
    label_upper = label.upper()
    return any(kw in label_upper for kw in FUNDING_KEYWORDS)


def contains_education_keyword(label: str) -> bool:
    """Check if label contains education-related keywords."""
    label_upper = label.upper()
    return any(kw in label_upper for kw in EDUCATION_KEYWORDS)


def get_education_level(text: str) -> Optional[str]:
    """Detect education level from text."""
    text_lower = text.lower()

    if any(kw in text_lower for kw in HIGHSCHOOL_KEYWORDS):
        return 'highschool'
    elif any(kw in text_lower for kw in POSTDOC_KEYWORDS):
        return 'postdoc'
    elif any(kw in text_lower for kw in GRAD_KEYWORDS):
        return 'graduate'
    elif any(kw in text_lower for kw in UNDERGRAD_KEYWORDS):
        return 'undergraduate'

    return None


def is_talk_like_entry(text: str) -> bool:
    """Check if text looks like a talk/presentation rather than publication."""
    text_lower = text.lower()
    talk_indicators = [
        'presentation', 'keynote', 'invited talk', 'seminar',
        'grand rounds', 'plenary', 'symposium', 'conference'
    ]

    # Has talk indicators but NOT publication indicators
    has_talk = any(ind in text_lower for ind in talk_indicators)
    has_pub = any(ind in text_lower for ind in ['doi:', 'pmid:', 'volume', 'issue', 'pp.', 'pages'])

    return has_talk and not has_pub


def extract_latest_year(text: str, today_year: int = 2025) -> Dict[str, Any]:
    """
    NEW FIX #17: Extract latest year from entry text.

    Deterministic parser for temporal metadata extraction from CV entries.
    Handles year ranges, "present" indicators, shorthand notation, and noise filtering.

    Args:
        text: Entry text to parse
        today_year: Current year for "present" interpretation (default: 2025)

    Returns:
        Dict with:
            years_found: List of valid years extracted
            latest_year: Maximum year ≤ today_year
            is_current: True if "present" or ongoing indicators detected
            parse_notes: List of parsing decisions (e.g., "present-detected", "shorthand-expanded")

    Examples:
        "1/2008-present" → {years_found: [2008], latest_year: 2025, is_current: True}
        "2014-15" → {years_found: [2014, 2015], latest_year: 2015, is_current: False}
        "7/2999-11/2000" → {years_found: [2000], latest_year: 2000, parse_notes: ["typo-discarded"]}
    """
    years_found = []
    parse_notes = []
    is_current = False

    # Normalize text
    normalized = text.lower()
    # Normalize various dash types to hyphen
    normalized = re.sub(r'[–—]', '-', normalized)

    # Check for "present" indicators
    present_patterns = [
        r'\bpresent\b', r'\bcurrent\b', r'\bongoing\b',
        r'\bto\s+date\b', r'-present\b', r'—present\b'
    ]
    if any(re.search(p, normalized) for p in present_patterns):
        is_current = True
        parse_notes.append("present-detected")

    # Extract 4-digit years
    full_year_pattern = r'\b(19|20)\d{2}\b'
    full_years = re.findall(full_year_pattern, normalized)

    # Extract slash dates (e.g., 1/2008, 6/30/2014)
    slash_date_pattern = r'\b\d{1,2}/(?:\d{1,2}/)?((?:19|20)\d{2})\b'
    slash_years = re.findall(slash_date_pattern, normalized)

    # Combine all found years
    all_year_strings = full_years + slash_years

    # Handle shorthand ranges (e.g., 2014-15, 1997-98)
    # Find patterns like YYYY-YY
    shorthand_pattern = r'\b((19|20)\d{2})-(\d{2})\b'
    for match in re.finditer(shorthand_pattern, normalized):
        start_year = match.group(1)
        end_two_digits = match.group(3)

        # Expand shorthand: 2014-15 → 2015
        century_prefix = start_year[:2]  # "20" from "2014"
        expanded_end = century_prefix + end_two_digits  # "2015"

        all_year_strings.append(start_year)
        all_year_strings.append(expanded_end)
        parse_notes.append("shorthand-expanded")

    # Convert to integers and filter
    for year_str in all_year_strings:
        try:
            year = int(year_str)

            # Sanity check: 1900-2100
            if 1900 <= year <= 2100:
                years_found.append(year)
            else:
                parse_notes.append("typo-discarded")
        except ValueError:
            continue

    # Remove duplicates and sort
    years_found = sorted(set(years_found))

    # Determine latest year
    if is_current:
        latest_year = today_year
    elif years_found:
        # Take max year ≤ today_year
        valid_years = [y for y in years_found if y <= today_year]
        latest_year = max(valid_years) if valid_years else None
    else:
        latest_year = None

    return {
        'years_found': years_found,
        'latest_year': latest_year,
        'is_current': is_current,
        'parse_notes': parse_notes
    }


def enrich_entries_with_years(groups: List[Dict], today_year: int = 2025) -> List[Dict]:
    """
    NEW FIX #17 (enrichment): Add temporal metadata to all entries.

    Recursively processes all entries in all groups and adds year extraction metadata.
    This enrichment is used downstream for chronology-aware merging and analysis.

    Args:
        groups: List of groups to enrich
        today_year: Current year (default: 2025)

    Returns:
        Groups with enriched entry metadata
    """
    def enrich_group(group: Dict) -> Dict:
        """Recursively enrich entries in group."""
        # Enrich entries
        if group.get('entries'):
            for entry in group['entries']:
                text = entry.get('text_snippet', '')
                if text:
                    year_data = extract_latest_year(text, today_year)
                    entry['meta'] = entry.get('meta', {})
                    entry['meta']['temporal'] = year_data

        # Recursively enrich subgroups
        if group.get('subgroups'):
            group['subgroups'] = [enrich_group(sg) for sg in group['subgroups']]

        return group

    return [enrich_group(g) for g in groups]


def merge_consecutive_same_label_groups(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #18: Merge consecutive groups with identical labels OR matching temporal metadata.

    Reduces fragmentation by consolidating adjacent groups that share the same
    inferred label and meet merge safety criteria. Particularly useful for:
    - Research subgroups (G40.1, G40.2, ..., G40.10 → single Research group)
    - Bibliography clusters scattered across multiple groups
    - Education container consolidation

    Merge Criteria (ALL must be met):
    1. Label equality: Same label_inferred (case-insensitive)
       OR Temporal equivalence: Both "Unknown" with matching latest_year
    2. Depth equality: Same hierarchy level
    3. Contiguity: No intervening groups with different labels
    4. Protected headers: NOT on exclusion list

    Temporal Merge Enhancement:
    - When both labels are "Unknown", uses Fix #17 temporal metadata
    - Merges consecutive groups with matching latest_year
    - Example: Research entries from 2015 merge together, separate from 2016 entries
    - Adds merge_rationale.temporal_merge=True to metadata

    Protected Headers (do NOT merge):
    - Editorial Positions
    - SERVICE ON JOURNALS/PUBLICATIONS
    - Additional Training
    - Honors and Awards (when next to Grants)
    - NAME, PRESENT TITLE, ADDRESS (contact blocks)
    - Certification, Grants (when headers differ)

    Args:
        groups: List of groups to process

    Returns:
        List of groups with consecutive same-label groups merged
    """
    # Protected header tokens that should prevent merging
    PROTECTED_HEADERS = {
        'editorial positions', 'service on journals', 'service on publications',
        'additional training', 'honors and awards', 'grants',
        'name', 'present title', 'address', 'email', 'phone',
        'certification', 'licensure'
    }

    # Labels that are mergeable by default
    MERGEABLE_LABELS = {
        'research', 'bibliography', 'publications', 'education',
        'professional organizations', 'memberships', 'teaching',
        'service', 'presentations', 'talks'
    }

    def normalize_label(label: str) -> str:
        """Normalize label for comparison."""
        return label.lower().strip()

    def has_protected_header(group: Dict) -> str:
        """Check if group has a protected header. Returns the protected token if found."""
        label = normalize_label(group.get('label_inferred', ''))

        for protected in PROTECTED_HEADERS:
            if protected in label:
                return protected

        # Check first entry text for protected headers
        entries = group.get('entries', [])
        if entries:
            first_text = entries[0].get('text_snippet', '').lower()
            for protected in PROTECTED_HEADERS:
                if protected in first_text[:100]:  # Check first 100 chars
                    return protected

        return None

    def get_group_latest_year(group: Dict) -> Optional[int]:
        """Extract latest year from group's entries' temporal metadata."""
        latest_years = []
        for entry in group.get('entries', []):
            temporal = entry.get('meta', {}).get('temporal', {})
            year = temporal.get('latest_year')
            if year:
                latest_years.append(year)

        # Also check subgroups
        for subgroup in group.get('subgroups', []):
            sub_year = get_group_latest_year(subgroup)
            if sub_year:
                latest_years.append(sub_year)

        return max(latest_years) if latest_years else None

    def can_merge(group1: Dict, group2: Dict) -> Tuple[bool, str]:
        """
        Check if two groups can be merged.

        Returns:
            (can_merge: bool, reason: str)
        """
        label1 = normalize_label(group1.get('label_inferred', ''))
        label2 = normalize_label(group2.get('label_inferred', ''))

        # Must have same label
        if label1 != label2:
            return (False, "different_labels")

        # Must be at same depth
        depth1 = group1.get('level', 1)
        depth2 = group2.get('level', 1)
        if depth1 != depth2:
            return (False, "different_depths")

        # Check if label is mergeable
        is_mergeable = any(m in label1 for m in MERGEABLE_LABELS)

        # ENHANCEMENT: If both labels are "unknown", use temporal metadata as merge signal
        if not is_mergeable and label1 == 'unknown':
            year1 = get_group_latest_year(group1)
            year2 = get_group_latest_year(group2)

            # If both have temporal data and years match, allow merge
            if year1 and year2 and year1 == year2:
                return (True, "temporal_match_unknown_labels")
            elif year1 and year2:
                return (False, "temporal_mismatch")
            else:
                return (False, "no_temporal_data")

        if not is_mergeable:
            return (False, "not_mergeable_label")

        # Check protected headers
        protected1 = has_protected_header(group1)
        protected2 = has_protected_header(group2)

        if protected1 and protected2 and protected1 != protected2:
            return (False, f"protected_headers_differ: {protected1} vs {protected2}")

        if protected1 or protected2:
            # Allow merge only if both have same protected header
            if protected1 == protected2:
                return (True, "same_protected_header")
            else:
                return (False, "protected_header_present")

        return (True, "mergeable")

    def merge_groups(run: List[Dict], merge_reason: str = "mergeable") -> Dict:
        """Merge a run of groups into a single group."""
        if not run:
            return None

        if len(run) == 1:
            return run[0]

        # Create merged group
        merged = deepcopy(run[0])

        # Collect all entries and subgroups
        all_entries = []
        all_subgroups = []
        merged_ids = [run[0].get('group_id', '')]
        merge_years = []

        for group in run:
            all_entries.extend(group.get('entries', []))
            all_subgroups.extend(group.get('subgroups', []))
            if group != run[0]:
                merged_ids.append(group.get('group_id', ''))

            # Track years for temporal merges
            group_year = get_group_latest_year(group)
            if group_year:
                merge_years.append(group_year)

        merged['entries'] = all_entries
        merged['subgroups'] = all_subgroups

        # Add merge metadata
        merged['meta'] = merged.get('meta', {})
        merged['meta']['merged_from'] = merged_ids
        merged['meta']['merge_count'] = len(run)
        merged['meta']['merge_rationale'] = {
            'label_equal': True,
            'depth_equal': True,
            'contiguous': True,
            'merge_reason': merge_reason
        }

        # Add temporal metadata if this was a temporal merge
        if merge_reason == 'temporal_match_unknown_labels' and merge_years:
            merged['meta']['merge_rationale']['temporal_year'] = merge_years[0]
            merged['meta']['merge_rationale']['temporal_merge'] = True

        return merged

    # Process groups at current level
    result = []
    i = 0

    while i < len(groups):
        current = groups[i]

        # Try to extend a run starting from current
        run = [current]
        merge_reason = "mergeable"
        j = i + 1

        while j < len(groups):
            can_merge_next, reason = can_merge(current, groups[j])

            if can_merge_next:
                run.append(groups[j])
                merge_reason = reason  # Track the merge reason
                j += 1
            else:
                break

        # If we have a run of 2+ groups, merge them
        if len(run) >= 2:
            merged = merge_groups(run, merge_reason)
            result.append(merged)
            i = j
        else:
            # No merge, keep as-is
            result.append(current)
            i += 1

    # Recursively process subgroups
    for group in result:
        if group.get('subgroups'):
            group['subgroups'] = merge_consecutive_same_label_groups(group['subgroups'])

    return result


# ============================================================================
# REPAIR FUNCTIONS
# ============================================================================

def is_likely_name_block(group: Dict) -> bool:
    """
    Check if a group likely contains a person's name.

    Indicators:
    - All caps with few words (1-4 words)
    - Contains title (Dr., Prof., MD)
    - Followed by contact information
    - Short text (< 100 chars)
    """
    label = group.get('label_inferred', '')
    entries = group.get('entries', [])

    if not entries:
        return False

    first_text = entries[0].get('text_snippet', '').strip()

    # Check for name patterns
    # All caps, 1-4 words, < 100 chars
    if label.isupper() and len(label.split()) <= 4 and len(label) < 100:
        return True

    # Contains professional titles
    if re.search(r'\b(Dr|Prof|Professor|MD|PhD|MPH|RN|DDS|DMD|MBA)\.?\b', first_text):
        return True

    # Single entry, short text, looks like a name
    if len(entries) == 1 and len(first_text) < 100:
        # Check if it's mostly letters and spaces (name-like)
        alpha_ratio = sum(c.isalpha() or c.isspace() for c in first_text) / len(first_text) if first_text else 0
        if alpha_ratio > 0.8 and not any(kw in first_text.lower() for kw in ['education', 'experience', 'publication', 'award', 'grant']):
            return True

    return False


def merge_contact_groups(groups: List[Dict], hierarchical: bool = True) -> List[Dict]:
    """
    FIX #6: Merge consecutive contact lines into single Personal Info group.

    PHASE 2 FIX #6 ENHANCEMENT: Can create hierarchical structure with Email/Phone/Fax/Address subgroups.
    NEW ENHANCEMENT: Better integration of name blocks with contact info.

    Example (hierarchical=False):
        G3: "Stiles-Nicholson Brain Institute"
        G4: "Rm 208G, MC-22"
        G5: "Florida Atlantic University"
        G6: "Jupiter, FL 33458"
        G7: "rblakely@health.fau.edu"

    Becomes:
        G_CONTACT: "Personal Information" (merged all)

    Example (hierarchical=True):
        Personal Information
          ├─ Name: "John Doe, MD"
          ├─ Email: "rblakely@health.fau.edu"
          ├─ Phone: ...
          └─ Address: "Stiles-Nicholson Brain Institute", "Rm 208G", ...

    Args:
        groups: List of groups
        hierarchical: If True, create nested Email/Phone/Fax/Address structure
    """
    if not groups:
        return groups

    merged = []
    contact_buffer = []
    last_was_name = False

    for i, group in enumerate(groups):
        label = group.get('label_inferred', '')
        entries = group.get('entries', [])

        # Check if this is a name block
        is_name = is_likely_name_block(group)

        # Check if this is a contact line
        is_contact = False
        if entries:
            first_entry_text = entries[0].get('text_snippet', '')
            is_contact = is_contact_line(first_entry_text) or is_contact_line(label)

        # Special case: date headers can sometimes contain contact info
        # "Date June 8, 2025" followed by contact → merge contact, not date
        is_date_header = re.search(r'\b(date|updated|revised)\b', label.lower()) and len(entries) <= 1

        # Look ahead: if name is followed by contact, include name in buffer
        if is_name:
            # Look ahead for contact info
            next_is_contact = False
            if i + 1 < len(groups):
                next_group = groups[i + 1]
                next_entries = next_group.get('entries', [])
                if next_entries:
                    next_text = next_entries[0].get('text_snippet', '')
                    next_is_contact = is_contact_line(next_text) or is_contact_line(next_group.get('label_inferred', ''))

            if next_is_contact:
                # Include name in contact buffer
                contact_buffer.append(group)
                last_was_name = True
                continue

        # Skip date headers - they shouldn't be merged with contact
        if is_date_header and not is_contact:
            # Flush buffer if any
            if contact_buffer:
                if hierarchical:
                    hierarchical_contact = create_hierarchical_contact_group(contact_buffer)
                    if hierarchical_contact:
                        merged.append(hierarchical_contact)
                    else:
                        merged_contact = _merge_groups(contact_buffer, "Personal Information")
                        merged.append(merged_contact)
                else:
                    merged_contact = _merge_groups(contact_buffer, "Personal Information")
                    merged.append(merged_contact)
                contact_buffer = []
                last_was_name = False

            merged.append(group)
            continue

        if is_contact:
            contact_buffer.append(group)
        else:
            # Flush contact buffer
            if contact_buffer:
                if hierarchical:
                    # PHASE 2 FIX #6: Create hierarchical structure
                    hierarchical_contact = create_hierarchical_contact_group(contact_buffer)
                    if hierarchical_contact:
                        merged.append(hierarchical_contact)
                    else:
                        # Fallback to flat merge if hierarchical fails
                        merged_contact = _merge_groups(contact_buffer, "Personal Information")
                        merged.append(merged_contact)
                else:
                    # Flat merge (original Phase 1 behavior)
                    merged_contact = _merge_groups(contact_buffer, "Personal Information")
                    merged.append(merged_contact)
                contact_buffer = []
                last_was_name = False

            merged.append(group)

    # Flush remaining contacts
    if contact_buffer:
        if hierarchical:
            # PHASE 2 FIX #6: Create hierarchical structure
            hierarchical_contact = create_hierarchical_contact_group(contact_buffer)
            if hierarchical_contact:
                merged.append(hierarchical_contact)
            else:
                # Fallback to flat merge if hierarchical fails
                merged_contact = _merge_groups(contact_buffer, "Personal Information")
                merged.append(merged_contact)
        else:
            # Flat merge (original Phase 1 behavior)
            merged_contact = _merge_groups(contact_buffer, "Personal Information")
            merged.append(merged_contact)

    return merged


def _merge_groups(groups: List[Dict], new_label: str) -> Dict:
    """
    Merge multiple groups into one.

    PHASE 1 FIX #5: Enhanced with deduplication to avoid duplicate contact lines.
    """
    if len(groups) == 1:
        return groups[0]

    # PHASE 1 FIX #5: Combine entries with deduplication
    merged_entries = []
    seen_texts = set()  # Track case-insensitive text to avoid duplicates

    for group in groups:
        for entry in group.get('entries', []):
            entry_text = entry.get('text_snippet', '').strip()
            text_normalized = entry_text.lower()

            # Only add if not seen before
            if text_normalized and text_normalized not in seen_texts:
                merged_entries.append(entry)
                seen_texts.add(text_normalized)

    # Take first group as base
    merged = deepcopy(groups[0])
    merged['label_inferred'] = new_label
    merged['entries'] = merged_entries
    merged['meta'] = merged.get('meta', {})
    merged['meta']['merged_from'] = [g.get('id') for g in groups]
    merged['meta']['repair_applied'] = 'contact_coalescing'
    merged['meta']['duplicates_removed'] = len(groups) - len(merged_entries)  # Track deduplication

    return merged


def promote_all_caps_subgroups(groups: List[Dict]) -> List[Dict]:
    """
    FIX #1: Promote ALL-CAPS subgroups to top-level when they represent
    new section boundaries.

    PHASE 1 FIX #3: Enhanced to catch single-word ALL-CAPS and nested headers.

    Example:
        G19: PATENTS
            G19.1: PROFESSIONAL SOCIETIES  <- Should be G20 (top-level)
            G19.2: INDUSTRY CONSULTING     <- Should be G21 (top-level)
            G19.3: Some Group
                G19.3.1: EDUCATION         <- Should be G22 (nested ALL-CAPS)

    After repair:
        G19: PATENTS
        G20: PROFESSIONAL SOCIETIES (promoted)
        G21: INDUSTRY CONSULTING (promoted)
        G22: EDUCATION (promoted from nested)
    """
    repaired = []
    next_id_counter = len(groups) + 1

    def recursive_promote(group_list: List[Dict], parent_level: int = 0) -> List[Dict]:
        """Recursively find and collect ALL-CAPS headers at any depth."""
        nonlocal next_id_counter
        promoted_from_deep = []

        for group in group_list:
            subgroups = group.get('subgroups', [])
            if not subgroups:
                continue

            # Recursively check subgroups first (depth-first)
            deep_promoted = recursive_promote(subgroups, parent_level + 1)
            promoted_from_deep.extend(deep_promoted)

        return promoted_from_deep

    for group in groups:
        # First, recursively check all nested subgroups for ALL-CAPS
        subgroups = group.get('subgroups', [])

        # Find ALL-CAPS subgroups at this level to promote
        to_promote = []
        remaining_subgroups = []

        for subgroup in subgroups:
            label = subgroup.get('label_inferred', '')
            # PHASE 1 FIX #3: Use allow_single_word=True to catch single-word headers
            if is_all_caps_header(label, allow_single_word=True):
                # Promote to top-level
                promoted = deepcopy(subgroup)
                promoted['level'] = 1
                promoted['id'] = f"G{next_id_counter}"
                promoted['parent_id'] = None
                promoted['meta'] = promoted.get('meta', {})
                promoted['meta']['repair_applied'] = 'all_caps_promotion'
                promoted['meta']['original_parent'] = group.get('id')

                to_promote.append(promoted)
                next_id_counter += 1
            else:
                # Keep this subgroup, but check its children recursively
                if subgroup.get('subgroups'):
                    nested_promoted = promote_all_caps_from_nested(subgroup, next_id_counter)
                    to_promote.extend(nested_promoted[0])
                    next_id_counter = nested_promoted[1]

                remaining_subgroups.append(subgroup)

        # Update current group
        group['subgroups'] = remaining_subgroups
        repaired.append(group)

        # Add promoted groups immediately after parent
        repaired.extend(to_promote)

    return repaired


def promote_all_caps_from_nested(group: Dict, id_counter: int) -> tuple:
    """
    Helper for Fix #3: Recursively extract ALL-CAPS headers from nested subgroups.

    Args:
        group: Group to search recursively
        id_counter: Current ID counter

    Returns:
        Tuple of (promoted_groups, new_id_counter)
    """
    promoted = []
    subgroups = group.get('subgroups', [])
    remaining = []

    for subgroup in subgroups:
        label = subgroup.get('label_inferred', '')
        if is_all_caps_header(label, allow_single_word=True):
            # Promote this nested header to top-level
            promoted_group = deepcopy(subgroup)
            promoted_group['level'] = 1
            promoted_group['id'] = f"G{id_counter}"
            promoted_group['parent_id'] = None
            promoted_group['meta'] = promoted_group.get('meta', {})
            promoted_group['meta']['repair_applied'] = 'all_caps_promotion_nested'
            promoted_group['meta']['original_parent'] = group.get('id')

            promoted.append(promoted_group)
            id_counter += 1
        else:
            # Check this subgroup's children
            if subgroup.get('subgroups'):
                nested_result = promote_all_caps_from_nested(subgroup, id_counter)
                promoted.extend(nested_result[0])
                id_counter = nested_result[1]

            remaining.append(subgroup)

    # Update original group's subgroups
    group['subgroups'] = remaining

    return (promoted, id_counter)


def enforce_funding_containment(groups: List[Dict]) -> List[Dict]:
    """
    FIX #3: Keep numbered grant lines within FUNDING section.

    Example:
        G30: FUNDING
        G32: "1. NIH/NINDS R01 NS33373, PI"  <- Should be child of G30
        G33: "2. NIMH R01 MH58921, PI"       <- Should be child of G30

    After repair:
        G30: FUNDING
            G30.1: "1. NIH/NINDS R01 NS33373, PI"
            G30.2: "2. NIMH R01 MH58921, PI"
    """
    repaired = []
    in_funding_section = False
    current_funding_group = None

    for i, group in enumerate(groups):
        label = group.get('label_inferred', '')

        # Check if this is start of funding section
        if contains_funding_keyword(label):
            in_funding_section = True
            current_funding_group = group
            repaired.append(group)
            continue

        # If in funding section, check for numbered lines
        if in_funding_section:
            # Check LABEL (not entry text which has [N] prefix)
            if is_numbered_line(label):
                # Move this group under current funding group as subgroup
                group['parent_id'] = current_funding_group['id']
                group['level'] = 2
                group['meta'] = group.get('meta', {})
                group['meta']['repair_applied'] = 'funding_containment'

                current_funding_group['subgroups'].append(group)
                continue

        # Check if we've left funding section (new ALL-CAPS header)
        if in_funding_section and is_all_caps_header(label):
            in_funding_section = False
            current_funding_group = None

        repaired.append(group)

    return repaired


def add_education_metadata(groups: List[Dict]) -> List[Dict]:
    """
    FIX #2: Add metadata about education level to help routing.

    This doesn't change structure, just adds hints for taxonomy mapper.
    """
    in_education = False

    for group in groups:
        label = group.get('label_inferred', '')

        if contains_education_keyword(label):
            in_education = True
            group['meta'] = group.get('meta', {})
            group['meta']['is_education_section'] = True
        elif is_all_caps_header(label):
            in_education = False

        if in_education:
            entries = group.get('entries', [])
            if entries:
                entry_text = ' '.join(e.get('text_snippet', '') for e in entries)
                edu_level = get_education_level(entry_text)
                if edu_level:
                    group['meta'] = group.get('meta', {})
                    group['meta']['education_level'] = edu_level
                    group['meta']['repair_applied'] = 'education_metadata'

        # Recursively process subgroups
        if group.get('subgroups'):
            add_education_metadata(group['subgroups'])

    return groups


def split_mixed_education_postdoc_groups(groups: List[Dict]) -> tuple[List[Dict], int]:
    """
    NEW FIX: Split groups that contain both formal degrees (B) and postdoctoral positions (C).

    Problem: Groups labeled "Education" sometimes mix degree entries (Ph.D., B.S., M.S.)
    with postdoctoral fellowship positions. These should be in separate groups for correct
    taxonomy mapping.

    Solution: Detect mixed groups and split them into:
    - One group for formal degrees → B (Education and Training)
    - One group for postdoc positions → C (Postdoctoral Training)

    Args:
        groups: List of groups to check and potentially split

    Returns:
        Tuple of (modified groups list, count of groups split)
    """
    split_count = 0
    result_groups = []

    # Degree indicators (formal education)
    DEGREE_PATTERNS = [
        'ph.d.', 'phd', 'ph.d', 'doctorate',
        'm.d.', 'md', 'm.d',
        'b.s.', 'bs', 'b.s', 'bachelor', 'b.a.', 'ba',
        'm.s.', 'ms', 'm.s', 'master', 'm.a.', 'ma',
        'degree', 'graduated', 'diploma', 'dvm', 'd.v.m.'
    ]

    # Postdoc indicators (training positions)
    POSTDOC_PATTERNS = [
        'post-doctoral', 'postdoctoral', 'post doc', 'postdoc',
        'post doctoral', 'post-doc'
    ]

    def is_degree_entry(entry: Dict) -> bool:
        """Check if entry describes a formal degree."""
        text = entry.get('text_snippet', '').lower()

        # Strong degree signals (pattern-based detection)
        for pattern in DEGREE_PATTERNS:
            if pattern in text:
                # Make sure it's not describing someone else's degree
                # (like "Mentor: John Doe, Ph.D.")
                if 'mentor' not in text[:50]:  # Check first part
                    return True

        return False

    def is_postdoc_entry(entry: Dict) -> bool:
        """Check if entry describes a postdoctoral position."""
        text = entry.get('text_snippet', '').lower()

        # Check for postdoc patterns
        for pattern in POSTDOC_PATTERNS:
            if pattern in text:
                return True

        # Check for fellowship/fellow language (common in postdoc positions)
        # Combined with institutional/research context
        if ('fellow' in text or 'fellowship' in text):
            # Additional context check to distinguish from other fellowships
            research_context = any(ctx in text for ctx in [
                'research', 'university', 'institute', 'laboratory', 'lab',
                'medical', 'hospital', 'center', 'department'
            ])
            if research_context:
                return True

        return False

    def should_split_group(group: Dict) -> bool:
        """Check if group contains both degrees and postdoc positions."""
        label = group.get('label_inferred', '').lower()

        # Only check groups labeled as education/training
        if not any(keyword in label for keyword in ['education', 'training', 'academic background']):
            return False

        entries = group.get('entries', [])
        if len(entries) < 2:  # Need at least 2 entries to split
            return False

        has_degrees = False
        has_postdocs = False

        for entry in entries:
            if is_degree_entry(entry):
                has_degrees = True
            if is_postdoc_entry(entry):
                has_postdocs = True

        # Only split if we have BOTH types
        return has_degrees and has_postdocs

    def split_group(group: Dict) -> tuple[Dict, Dict]:
        """Split a mixed group into degree group and postdoc group."""
        entries = group.get('entries', [])

        degree_entries = []
        postdoc_entries = []

        for entry in entries:
            if is_postdoc_entry(entry):
                postdoc_entries.append(entry)
            else:
                # Default to degree group (safer assumption)
                degree_entries.append(entry)

        # Create degree group (keep original ID)
        degree_group = {
            'id': group['id'],
            'label_inferred': 'Education',
            'level': group.get('level', 1),
            'entries': degree_entries,
            'subgroups': [],
            'meta': {
                **group.get('meta', {}),
                'repair_applied': 'education_postdoc_split',
                'split_type': 'degree',
                'original_label': group.get('label_inferred', 'Education')
            }
        }

        # Create postdoc group (new ID with suffix)
        postdoc_group = {
            'id': f"{group['id']}_postdoc",
            'label_inferred': 'Postdoctoral Training',
            'level': group.get('level', 1),
            'entries': postdoc_entries,
            'subgroups': [],
            'meta': {
                **group.get('meta', {}),
                'repair_applied': 'education_postdoc_split',
                'split_type': 'postdoc',
                'original_label': group.get('label_inferred', 'Education')
            }
        }

        return degree_group, postdoc_group

    # Process groups
    for group in groups:
        # Check if this group should be split
        if should_split_group(group):
            degree_group, postdoc_group = split_group(group)
            result_groups.append(degree_group)
            result_groups.append(postdoc_group)
            split_count += 1
        else:
            # Keep group as-is, but recursively process subgroups
            if group.get('subgroups'):
                subgroups, sub_split_count = split_mixed_education_postdoc_groups(group['subgroups'])
                group['subgroups'] = subgroups
                split_count += sub_split_count
            result_groups.append(group)

    return result_groups, split_count


def promote_talk_entries_from_publications(groups: List[Dict]) -> List[Dict]:
    """
    FIX #5: Promote talk-like entries under publication sections.

    Example:
        G62: Preprints (S10)
            G62.1: "Keynote at Annual Conference" <- Looks like R2, not S10

    After repair:
        G62: Preprints (S10)
        G63: Invited Talks (promoted from G62.1)
    """
    repaired = []
    next_id_counter = len(groups) + 1

    for group in groups:
        label = group.get('label_inferred', '').upper()

        # Check if this is a publication section
        is_pub_section = any(kw in label for kw in ['PUBLICATION', 'PREPRINT', 'ARTICLE', 'PAPER'])

        if is_pub_section:
            subgroups = group.get('subgroups', [])
            to_promote = []
            remaining = []

            for subgroup in subgroups:
                entries = subgroup.get('entries', [])
                if entries:
                    entry_text = ' '.join(e.get('text_snippet', '') for e in entries)
                    if is_talk_like_entry(entry_text):
                        # Promote to top-level talks section
                        promoted = deepcopy(subgroup)
                        promoted['level'] = 1
                        promoted['id'] = f"G{next_id_counter}"
                        promoted['parent_id'] = None
                        promoted['label_inferred'] = "Invited Talks"
                        promoted['meta'] = promoted.get('meta', {})
                        promoted['meta']['repair_applied'] = 'talk_promotion_from_publication'
                        promoted['meta']['original_parent'] = group.get('id')

                        to_promote.append(promoted)
                        next_id_counter += 1
                    else:
                        remaining.append(subgroup)

            group['subgroups'] = remaining
            repaired.append(group)
            repaired.extend(to_promote)
        else:
            repaired.append(group)

    return repaired


def merge_adjacent_institution_headers(groups: List[Dict]) -> List[Dict]:
    """
    PHASE 2 FIX #8: Merge adjacent ALL-CAPS institution headers.

    Example:
        G3: UMSOM
        G4: UNIVERSITY OF MARYLAND SCHOOL OF MEDICINE
        → Merged to: G3: University of Maryland School of Medicine (UMSOM)

    Args:
        groups: List of top-level groups

    Returns:
        Groups with adjacent institution headers merged
    """
    if not groups:
        return groups

    merged = []
    i = 0

    while i < len(groups):
        current = groups[i]
        current_label = current.get('label_inferred', '')

        # Check if current is ALL-CAPS and short (likely acronym)
        is_short_caps = is_all_caps_header(current_label, allow_single_word=True) and len(current_label) < 15

        # Look ahead for expanded version
        if is_short_caps and i + 1 < len(groups):
            next_group = groups[i + 1]
            next_label = next_group.get('label_inferred', '')

            # Check if next is also ALL-CAPS and longer (likely full name)
            is_long_caps = is_all_caps_header(next_label) and len(next_label) > 15

            # Check if current label appears as acronym in next label
            words_in_next = next_label.split()
            potential_acronym = ''.join([w[0] for w in words_in_next if w])

            if is_long_caps and (potential_acronym.upper() == current_label.upper() or
                                 current_label.upper() in next_label.upper()):
                # Merge them
                merged_group = deepcopy(next_group)
                merged_group['label_inferred'] = f"{next_label.title()} ({current_label})"

                # Combine entries
                merged_group['entries'] = current.get('entries', []) + next_group.get('entries', [])
                merged_group['subgroups'] = current.get('subgroups', []) + next_group.get('subgroups', [])

                merged_group['meta'] = merged_group.get('meta', {})
                merged_group['meta']['repair_applied'] = 'institution_header_merge'
                merged_group['meta']['merged_ids'] = [current.get('id'), next_group.get('id')]

                merged.append(merged_group)
                i += 2  # Skip both
                continue

        # No merge, keep current
        merged.append(current)
        i += 1

    return merged


def merge_address_blocks(groups: List[Dict]) -> List[Dict]:
    """
    PHASE 2 FIX #9: Merge contiguous ALL-CAPS single-line address blocks.

    Example:
        G3: "STILES-NICHOLSON BRAIN INSTITUTE"
        G4: "RM 208G, MC-22"
        G5: "FLORIDA ATLANTIC UNIVERSITY"
        G6: "JUPITER, FL 33458"
        → Merged to: G3: Personal Information (address block)

    Args:
        groups: List of groups

    Returns:
        Groups with address blocks merged
    """
    if not groups:
        return groups

    merged = []
    address_buffer = []

    for group in groups:
        label = group.get('label_inferred', '')
        entries = group.get('entries', [])

        # Check if this looks like address component
        is_address_component = False
        if entries:
            text = entries[0].get('text_snippet', '')
            is_address_component = (
                # Room/suite numbers
                bool(re.search(r'\b(rm|room|suite|ste|floor|bldg)\b\.?\s*\d+', text, re.I)) or
                # City, State ZIP
                bool(re.search(r'\b[A-Z][a-z]+,\s*[A-Z]{2}\s*\d{5}', text)) or
                # Street addresses
                bool(re.search(r'\d+\s+[A-Za-z\s]+\b(street|st|avenue|ave|road|rd|drive|dr|blvd)\b', text, re.I)) or
                # Building/institution names (all caps, single line)
                (is_all_caps_header(label) and len(entries) == 1 and len(text) < 100)
            )

        if is_address_component:
            address_buffer.append(group)
        else:
            # Flush buffer if we have 2+ consecutive address components
            if len(address_buffer) >= 2:
                address_group = _merge_groups(address_buffer, "Personal Information")
                address_group['meta']['address_block'] = True
                merged.append(address_group)
                address_buffer = []
            elif address_buffer:
                # Single item, not a block
                merged.extend(address_buffer)
                address_buffer = []

            merged.append(group)

    # Flush remaining buffer
    if len(address_buffer) >= 2:
        address_group = _merge_groups(address_buffer, "Personal Information")
        address_group['meta']['address_block'] = True
        merged.append(address_group)
    elif address_buffer:
        merged.extend(address_buffer)

    return merged


def is_institutional_acronym(label: str) -> bool:
    """
    Check if label is likely an institutional acronym (e.g., UMSOM, NIH, UCLA).

    Indicators:
    - All caps
    - Short (2-10 chars)
    - No spaces
    - Mostly letters
    """
    if not label:
        return False

    stripped = label.strip()

    # Must be all caps
    if not stripped.isupper():
        return False

    # Must be short (acronyms are typically 2-10 chars)
    if len(stripped) < 2 or len(stripped) > 10:
        return False

    # Must not have spaces (acronyms don't have spaces)
    if ' ' in stripped:
        return False

    # Must be mostly letters (allow some numbers)
    letter_count = sum(c.isalpha() for c in stripped)
    if letter_count < len(stripped) * 0.7:
        return False

    return True


def deduplicate_repeated_headers(groups: List[Dict]) -> List[Dict]:
    """
    PHASE 2 FIX #12: Deduplicate repeated headers.
    ENHANCED: More aggressive deduplication for institutional acronyms.

    Example:
        G10: Grants
        G15: Grants
        G20: Grants
        → Merged to: G10: Grants (with all entries)

    Example (institutional acronyms):
        G12: UMSOM
        G18: UMSOM
        G25: UMSOM
        → Merged to: G12: UMSOM (with all entries and subgroups)

    Args:
        groups: List of groups

    Returns:
        Groups with duplicates merged
    """
    if not groups:
        return groups

    # Group by normalized label
    label_to_groups = {}

    for group in groups:
        label = group.get('label_inferred', '')
        # Normalize: lowercase, remove punctuation, strip
        normalized = re.sub(r'[^\w\s]', '', label.lower()).strip()

        if normalized not in label_to_groups:
            label_to_groups[normalized] = []
        label_to_groups[normalized].append(group)

    # Merge duplicates
    result = []
    for normalized_label, group_list in label_to_groups.items():
        if len(group_list) == 1:
            result.append(group_list[0])
        else:
            # Check if this is an institutional acronym - if so, always merge
            original_label = group_list[0].get('label_inferred', '')
            is_acronym = is_institutional_acronym(original_label)

            # Merge if: (1) institutional acronym, OR (2) normal duplicate header
            if is_acronym or len(group_list) >= 2:
                # Merge all with same label
                primary = deepcopy(group_list[0])

                # Combine entries and subgroups from all duplicates
                for other in group_list[1:]:
                    primary['entries'].extend(other.get('entries', []))
                    primary['subgroups'].extend(other.get('subgroups', []))

                primary['meta'] = primary.get('meta', {})
                primary['meta']['repair_applied'] = 'repeated_header_deduplication'
                primary['meta']['merged_count'] = len(group_list)
                primary['meta']['merged_ids'] = [g.get('id') for g in group_list]
                if is_acronym:
                    primary['meta']['institutional_acronym_merged'] = True

                result.append(primary)
            else:
                # Keep as is
                result.append(group_list[0])

    return result


def deduplicate_identical_entries(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #7 (enhanced #32): Deduplicate identical text_snippets within same section.

    Addresses issue where identical entries appear multiple times in same section,
    creating redundancy. Now includes semantic deduplication via DOI/PMID identifiers.

    Example:
        G6: Academic Appointments
            E1: "Assistant Professor, 2020-present"
            E2: "Assistant Professor, 2020-present"
        → Merged to:
        G6: Academic Appointments
            E1: "Assistant Professor, 2020-present"

    NEW FIX #32: Now also matches publications with same DOI or PMID:
        G10: Publications
            E1: "Smith J. Nature 2020. DOI: 10.1234/abc"
            E2: "Smith J. (2020) Nature. doi:10.1234/abc"
        → Merged to:
        G10: Publications
            E1: "Smith J. Nature 2020. DOI: 10.1234/abc"

    Args:
        groups: List of groups

    Returns:
        Groups with duplicate entries removed
    """
    def extract_publication_identifiers(text: str) -> dict:
        """
        NEW FIX #32: Extract DOI, PMID, and PMCID identifiers from text.

        Returns dict with normalized identifiers for semantic deduplication.
        """
        # DOI pattern: more flexible to catch variations
        doi_match = re.search(r'doi:\s*([^\s,;]+)', text, re.IGNORECASE)
        if not doi_match:
            # Try without "doi:" prefix - look for 10.xxxx/yyyy pattern
            doi_match = re.search(r'\b(10\.\d{4,}/[^\s,;]+)', text, re.IGNORECASE)

        # PMID pattern
        pmid_match = re.search(r'PMID:\s*(\d+)', text, re.IGNORECASE)
        if not pmid_match:
            # Try variations like "PubMed ID:" or just raw PMID
            pmid_match = re.search(r'(?:PubMed\s*ID|PMID):\s*(\d+)', text, re.IGNORECASE)

        # PMCID pattern
        pmcid_match = re.search(r'PMC(?:ID)?:\s*(\d+)', text, re.IGNORECASE)

        return {
            'doi': doi_match.group(1).lower().strip() if doi_match else None,
            'pmid': pmid_match.group(1) if pmid_match else None,
            'pmcid': pmcid_match.group(1) if pmcid_match else None
        }

    def normalize_text_for_comparison(text: str) -> str:
        """Normalize text for duplicate detection."""
        # Remove extra whitespace, lowercase
        normalized = re.sub(r'\s+', ' ', text.lower()).strip()
        return normalized

    def deduplicate_within_group(group: Dict) -> Dict:
        """Recursively deduplicate entries within a group."""
        # Deduplicate entries
        if group.get('entries'):
            seen_texts = {}
            seen_dois = {}       # NEW FIX #32: Track DOI identifiers
            seen_pmids = {}      # NEW FIX #32: Track PMID identifiers
            seen_pmcids = {}     # NEW FIX #32: Track PMCID identifiers
            unique_entries = []
            semantic_dupes = 0   # NEW FIX #32: Count identifier-based duplicates
            text_dupes = 0       # Track text-based duplicates separately

            for entry in group['entries']:
                text = entry.get('text_snippet', '')

                # NEW FIX #32: First check for publication identifiers
                identifiers = extract_publication_identifiers(text)
                is_duplicate = False
                dup_reason = None

                # Priority 1: Check DOI (most specific identifier)
                if identifiers['doi'] and identifiers['doi'] in seen_dois:
                    is_duplicate = True
                    dup_reason = f"DOI: {identifiers['doi']}"
                    semantic_dupes += 1
                # Priority 2: Check PMID
                elif identifiers['pmid'] and identifiers['pmid'] in seen_pmids:
                    is_duplicate = True
                    dup_reason = f"PMID: {identifiers['pmid']}"
                    semantic_dupes += 1
                # Priority 3: Check PMCID
                elif identifiers['pmcid'] and identifiers['pmcid'] in seen_pmcids:
                    is_duplicate = True
                    dup_reason = f"PMCID: {identifiers['pmcid']}"
                    semantic_dupes += 1
                # Priority 4: Fall back to text matching
                else:
                    normalized = normalize_text_for_comparison(text)
                    if normalized in seen_texts:
                        is_duplicate = True
                        dup_reason = "text_match"
                        text_dupes += 1

                # Keep first occurrence, discard duplicates
                if not is_duplicate:
                    # Register this entry's identifiers
                    if identifiers['doi']:
                        seen_dois[identifiers['doi']] = entry
                    if identifiers['pmid']:
                        seen_pmids[identifiers['pmid']] = entry
                    if identifiers['pmcid']:
                        seen_pmcids[identifiers['pmcid']] = entry
                    # Register normalized text
                    normalized = normalize_text_for_comparison(text)
                    seen_texts[normalized] = entry
                    unique_entries.append(entry)

            # Update group with deduplicated entries
            duplicates_removed = len(group['entries']) - len(unique_entries)
            group['entries'] = unique_entries

            if duplicates_removed > 0:
                group['meta'] = group.get('meta', {})
                group['meta']['duplicates_removed'] = duplicates_removed
                # NEW FIX #32: Track semantic vs text duplicates
                if semantic_dupes > 0:
                    group['meta']['semantic_duplicates_removed'] = semantic_dupes
                if text_dupes > 0:
                    group['meta']['text_duplicates_removed'] = text_dupes

        # Recursively process subgroups
        if group.get('subgroups'):
            group['subgroups'] = [deduplicate_within_group(sg) for sg in group['subgroups']]

        return group

    # Process all groups
    result = [deduplicate_within_group(g) for g in groups]

    return result


def merge_institutional_affiliation_sequences(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #11: Merge sequential institutional affiliation lines into contact blocks.

    Addresses issue where Department → College → University → City → Tel → Email
    lines are treated as separate groups instead of being nested under Contact Information.

    Example:
        G1: Department of Community and Behavioral Health
        G2: College of Public Health
        G3: University of Iowa
        G4: Iowa City, IA, USA
        G5: Tel: +1-319-XXX-XXXX
        G6: Email: user@iowa.edu
        → Merged to:
        G1: Contact Information
            E1: "Department of Community and Behavioral Health"
            E2: "College of Public Health"
            E3: "University of Iowa"
            E4: "Iowa City, IA, USA"
            E5: "Tel: +1-319-XXX-XXXX"
            E6: "Email: user@iowa.edu"

    Args:
        groups: List of groups

    Returns:
        Groups with affiliation sequences merged
    """
    def is_institutional_affiliation(label: str) -> bool:
        """Check if label is an institutional affiliation line."""
        label_lower = label.lower()
        return any(kw in label_lower for kw in INSTITUTIONAL_AFFILIATION_KEYWORDS)

    def is_city_state(label: str) -> bool:
        """Check if label is a city/state line."""
        # Pattern: City, STATE or City, ST ZIP
        return bool(re.search(r'^[A-Za-z\s]+,\s+[A-Z]{2}(\s+\d{5})?$', label.strip()))

    def has_contact_info(label: str) -> bool:
        """Check if label contains contact info (tel, email, etc.)."""
        label_lower = label.lower()
        return any(keyword in label_lower for keyword in ['tel:', 'phone:', 'email:', 'fax:', '@'])

    result = []
    i = 0

    while i < len(groups):
        group = groups[i]
        label = group.get('label_inferred', '')

        # Check if this starts an institutional affiliation sequence
        if is_institutional_affiliation(label) or is_city_state(label) or has_contact_info(label):
            # Look ahead to gather consecutive affiliation/contact lines
            affiliation_groups = [group]
            j = i + 1

            while j < len(groups):
                next_group = groups[j]
                next_label = next_group.get('label_inferred', '')

                # Continue if next line is also affiliation/contact
                if (is_institutional_affiliation(next_label) or
                    is_city_state(next_label) or
                    has_contact_info(next_label)):
                    affiliation_groups.append(next_group)
                    j += 1
                else:
                    break

            # If we found a sequence (2+), merge them
            if len(affiliation_groups) >= 2:
                merged_entries = []

                for aff_group in affiliation_groups:
                    # Convert label to entry if group has no entries
                    if not aff_group.get('entries'):
                        entry = {
                            'text_snippet': aff_group.get('label_inferred', ''),
                            'entry_type': 'contact_info'
                        }
                        merged_entries.append(entry)
                    else:
                        # Add existing entries
                        merged_entries.extend(aff_group['entries'])

                # Create merged contact group
                merged_group = {
                    'group_id': affiliation_groups[0].get('group_id', 'G_contact'),
                    'label_inferred': 'Contact Information',
                    'level': 1,
                    'entries': merged_entries,
                    'subgroups': [],
                    'meta': {
                        'repair_applied': 'institutional_affiliation_merge',
                        'merged_count': len(affiliation_groups)
                    }
                }

                result.append(merged_group)
                i = j  # Skip past merged groups
            else:
                # Single affiliation line, keep as-is
                result.append(group)
                i += 1
        else:
            # Not affiliation, keep as-is
            result.append(group)
            i += 1

    return result


def is_noise_entry(text: str) -> bool:
    """
    Check if an entry is noise that should be filtered out.

    Noise entries include:
    - Isolated numbers (list markers, page numbers)
    - Lone pagination ("pp. 123-145", "123-145")
    - Parenthetical fragments ("(continued)", "(see above)")
    - Single punctuation or symbols
    - List markers without content ("1.", "2)", "a.", "•")
    - Junk header stubs ("Any Other:", "Others:", "TBD", "N/A", "None")

    Args:
        text: The entry text to check

    Returns:
        True if the entry is noise and should be filtered
    """
    import re

    if not text:
        return True

    text = text.strip()

    # Empty after stripping
    if not text:
        return True

    # Too short to be meaningful (less than 3 chars)
    if len(text) < 3:
        return True

    # Isolated numbers (with optional list markers)
    # Matches: "1", "41", "1.", "1)", "(1)", "1:", "#1"
    if re.match(r'^[\(\[\#]?\d+[\)\]\.\:\,]?$', text):
        return True

    # Letter list markers without content
    # Matches: "a.", "b)", "(a)", "A."
    if re.match(r'^[\(\[]?[a-zA-Z][\)\]\.\:]?$', text):
        return True

    # Bullet/symbol only
    if text in ['•', '●', '○', '■', '□', '▪', '▫', '-', '–', '—', '*', '·']:
        return True

    # Lone pagination patterns
    # Matches: "pp. 123-145", "pp.123", "123-145", "p. 45"
    if re.match(r'^p{1,2}\.?\s*\d+[\-–—]?\d*$', text, re.IGNORECASE):
        return True

    # Just a page range (3-4 digits, dash, 3-4 digits)
    if re.match(r'^\d{1,4}[\-–—]\d{1,4}$', text):
        return True

    # Parenthetical fragments only
    # Matches: "(continued)", "(cont.)", "(see above)", "(ibid)"
    if re.match(r'^\([^)]{1,20}\)$', text):
        inner = text[1:-1].lower().strip()
        noise_parentheticals = [
            'continued', 'cont', 'cont.', 'see above', 'see below',
            'ibid', 'ibid.', 'op. cit.', 'op cit', 'loc. cit.',
            'supra', 'infra', 'id.', 'id'
        ]
        if inner in noise_parentheticals:
            return True

    # Roman numerals only (i, ii, iii, iv, v, vi, vii, viii, ix, x, etc.)
    if re.match(r'^[ivxlcdm]+[\.\)\:]?$', text.lower()):
        return True

    # Year only (1900-2099)
    if re.match(r'^(19|20)\d{2}$', text):
        return True

    # Date range only (2019-2020, 2019-present, 2019-)
    if re.match(r'^(19|20)\d{2}\s*[\-–—]\s*((19|20)\d{2}|present|current|ongoing)?$', text, re.IGNORECASE):
        return True

    # Truncated author list (names with commas but no title/venue/year)
    # Pattern: "Name1, Name2, Name3..." or "Name1, Name2, Name3 …"
    # Must have at least 2 comma-separated segments that look like names
    # and NOT contain typical publication indicators (year, journal, volume, pages)
    if ',' in text:
        # Check if it looks like just author names
        segments = [s.strip() for s in text.split(',')]
        if len(segments) >= 2:
            # Check for publication indicators that would make this NOT noise
            has_year = bool(re.search(r'\b(19|20)\d{2}\b', text))
            has_volume = bool(re.search(r'\b\d+\s*[\(:]\s*\d+', text))  # volume(issue) or volume:page
            has_pages = bool(re.search(r'\b\d+\s*[-–—]\s*\d+\b', text))  # page range
            has_journal_indicators = bool(re.search(r'\b(journal|proc|ann|rev|med|sci|health|res)\b', text.lower()))

            # If no publication indicators, check if segments look like names
            if not (has_year or has_volume or has_pages or has_journal_indicators):
                # Check if most segments look like names (capitalized words, possibly with initials)
                name_like_count = 0
                for seg in segments:
                    # Name pattern: starts with capital, may have initials, period, or single letters
                    if re.match(r'^[A-Z][a-z]*(\s+[A-Z]\.?)*(\s+[A-Z][a-z]*)*\.?\.?\.?$', seg.strip()):
                        name_like_count += 1
                # If most segments look like names and there's no title, it's likely truncated
                if name_like_count >= len(segments) * 0.6 and len(text) < 200:
                    # Additional check: ends with ellipsis or author name pattern
                    if text.rstrip().endswith('...') or text.rstrip().endswith('…') or re.search(r'[A-Z][a-z]+\s*$', text):
                        return True

    # Junk header stubs - placeholder labels without meaningful content
    # These often appear as section headers for empty/optional sections
    # Matches: "Any Other:", "Others:", "Other:", "TBD", "N/A", "None", etc.
    # BUT NOT "Other Peer Reviewed Publications" (has meaningful content after "Other")
    text_lower = text.lower().strip().rstrip(':').strip()
    junk_headers = [
        'any other', 'any others', 'others', 'other',
        'to be updated', 'to be determined', 'tbd',
        'n/a', 'na', 'not applicable', 'not available',
        'none', 'nil', 'nothing', 'empty',
        'see above', 'see below', 'see attached',
        'pending', 'forthcoming', 'in progress',
        'no entries', 'no items', 'no data',
        'not yet', 'coming soon', 'under construction'
    ]
    if text_lower in junk_headers:
        return True

    # Catch patterns like "Any Other (optional):" or "Other: N/A" or "Other (if applicable)"
    # But NOT "Other Peer Reviewed Publications" - the parenthetical must be short metadata
    # Only match if what follows "other" is parenthetical/bracketed (max 20 chars) or n/a/none
    if re.match(r'^(any\s+)?other[s]?\s*(\([^)]{1,20}\)|\[[^\]]{1,20}\])?\s*:?\s*(n/?a|none|nil|tbd)?$', text_lower):
        return True

    return False


def filter_empty_entries(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #12: Filter out empty entries and noise entries.

    Addresses issues where:
    - Grant/publication sections have entries with empty text_snippet fields
    - Isolated numbers, pagination, or list markers extracted as entries
    - Parenthetical fragments like "(continued)" extracted as entries

    Example:
        G10: Research Support
            E1: "NIH R01 2020-2025" (confidence: 0.95)
            E2: "" (confidence: 0.80)  [REMOVED - empty]
            E3: "41" (confidence: 0.85)  [REMOVED - noise]
            E4: "NSF Award 2021-2024" (confidence: 0.90)
        → Cleaned to:
        G10: Research Support
            E1: "NIH R01 2020-2025" (confidence: 0.95)
            E2: "NSF Award 2021-2024" (confidence: 0.90)

    Args:
        groups: List of groups

    Returns:
        Groups with empty and noise entries filtered
    """
    def filter_within_group(group: Dict) -> Dict:
        """Recursively filter empty and noise entries within a group."""
        # Filter entries
        if group.get('entries'):
            original_count = len(group['entries'])
            filtered_entries = []
            empty_removed = 0
            noise_removed = 0

            for entry in group['entries']:
                text = entry.get('text_snippet', '').strip()

                # Check for empty
                if not text:
                    # Keep if high confidence (>= 0.95) even if empty (might be placeholder)
                    confidence = entry.get('confidence', 1.0)
                    if confidence >= 0.95:
                        filtered_entries.append(entry)
                    else:
                        empty_removed += 1
                    continue

                # Check for noise
                if is_noise_entry(text):
                    noise_removed += 1
                    continue

                # Keep the entry
                filtered_entries.append(entry)

            # Update group
            group['entries'] = filtered_entries

            total_removed = empty_removed + noise_removed
            if total_removed > 0:
                group['meta'] = group.get('meta', {})
                group['meta']['entries_filtered'] = {
                    'empty_removed': empty_removed,
                    'noise_removed': noise_removed,
                    'total_removed': total_removed
                }

        # Recursively process subgroups
        if group.get('subgroups'):
            group['subgroups'] = [filter_within_group(sg) for sg in group['subgroups']]

        return group

    # Process all groups
    result = [filter_within_group(g) for g in groups]

    return result


def deduplicate_tabular_blocks(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #13: Deduplicate repeated tabular blocks using checksum detection.

    Addresses issue where line-wrapped teaching tables create duplicate blocks
    of entries (e.g., course schedules with "Qtr | Academic Yr" headers).

    Example:
        G20: Teaching
            E1: "BI 211 | Fall | 2018-19"
            E2: "BI 212 | Winter | 2018-19"
            E3: "BI 213 | Spring | 2018-19"
            E4: "BI 211 | Fall | 2018-19"  [DUPLICATE BLOCK START]
            E5: "BI 212 | Winter | 2018-19"
            E6: "BI 213 | Spring | 2018-19"
        → Cleaned to:
        G20: Teaching
            E1: "BI 211 | Fall | 2018-19"
            E2: "BI 212 | Winter | 2018-19"
            E3: "BI 213 | Spring | 2018-19"

    Args:
        groups: List of groups

    Returns:
        Groups with duplicate tabular blocks removed
    """
    import hashlib

    def compute_block_hash(entries: List[Dict], block_size: int, start_idx: int) -> str:
        """Compute hash for a block of entries."""
        block_texts = []
        for i in range(start_idx, min(start_idx + block_size, len(entries))):
            text = entries[i].get('text_snippet', '').strip().lower()
            # Normalize whitespace
            text = re.sub(r'\s+', ' ', text)
            block_texts.append(text)

        # Create hash of concatenated block
        block_str = '|||'.join(block_texts)
        return hashlib.md5(block_str.encode()).hexdigest()

    def is_likely_tabular(entries: List[Dict]) -> bool:
        """Detect if entries look like tabular data."""
        if len(entries) < 3:
            return False

        # Look for tabular indicators in first few entries
        sample_texts = [e.get('text_snippet', '') for e in entries[:5]]
        sample_str = ' '.join(sample_texts).lower()

        # Tabular indicators
        tabular_patterns = [
            r'\|',  # Pipe separator
            r'\t',  # Tab separator
            r'qtr|quarter|semester|term',  # Academic terms
            r'fall|winter|spring|summer',  # Seasons
            r'\d{4}-\d{2,4}',  # Academic years (2018-19, 2018-2019)
            r'^\s*\d+\.',  # Numbered lists
        ]

        matches = sum(1 for p in tabular_patterns if re.search(p, sample_str, re.I))
        return matches >= 2

    def deduplicate_blocks_within_group(group: Dict) -> Dict:
        """Recursively deduplicate tabular blocks within a group."""
        if group.get('entries') and len(group['entries']) >= 6:
            entries = group['entries']

            # Only try block deduplication if entries look tabular
            if not is_likely_tabular(entries):
                # Recursively process subgroups
                if group.get('subgroups'):
                    group['subgroups'] = [deduplicate_blocks_within_group(sg) for sg in group['subgroups']]
                return group

            # Try different block sizes (3-10 entries)
            for block_size in range(3, min(11, len(entries) // 2 + 1)):
                block_hashes = {}
                duplicate_indices = set()

                # Compute hashes for all possible blocks
                for i in range(len(entries) - block_size + 1):
                    block_hash = compute_block_hash(entries, block_size, i)

                    if block_hash in block_hashes:
                        # Found duplicate block - mark for removal
                        for j in range(i, min(i + block_size, len(entries))):
                            duplicate_indices.add(j)
                    else:
                        block_hashes[block_hash] = i

                # If we found duplicates with this block size, remove them
                if duplicate_indices:
                    original_count = len(entries)
                    filtered_entries = [e for idx, e in enumerate(entries) if idx not in duplicate_indices]
                    group['entries'] = filtered_entries

                    # Track metadata
                    removed_count = original_count - len(filtered_entries)
                    if removed_count > 0:
                        group['meta'] = group.get('meta', {})
                        group['meta']['tabular_block_duplicates_removed'] = removed_count
                        group['meta']['block_size_detected'] = block_size

                    # Stop after first successful deduplication
                    break

        # Recursively process subgroups
        if group.get('subgroups'):
            group['subgroups'] = [deduplicate_blocks_within_group(sg) for sg in group['subgroups']]

        return group

    # Process all groups
    result = [deduplicate_blocks_within_group(g) for g in groups]
    return result


def relabel_unknown_contact_groups(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #14: Relabel Unknown groups with strong contact signals to "Contact Information".

    Addresses issue where groups containing email/phone/address are labeled "Unknown"
    and rely on LLM fallback for classification instead of deterministic signals.

    Example:
        G1: Unknown
            E1: "Email: user@example.com"
        → Relabeled to:
        G1: Contact Information
            E1: "Email: user@example.com"

    Args:
        groups: List of groups

    Returns:
        Groups with Unknown+contact relabeled
    """
    def has_strong_contact_signals(group: Dict) -> bool:
        """Check if group has strong contact indicators."""
        label = group.get('label_inferred', '').lower()
        entries = group.get('entries', [])

        # Combine label and entry text
        combined_text = label + ' '
        for entry in entries[:3]:  # Check first 3 entries
            combined_text += entry.get('text_snippet', '') + ' '

        combined_text = combined_text.lower()

        # Strong contact signals (explicit labels)
        strong_signals = [
            r'\bemail\s*:',  # "Email:"
            r'\be-mail\s*:',  # "E-mail:"
            r'\btel\s*:',  # "Tel:"
            r'\btelephone\s*:',  # "Telephone:"
            r'\bphone\s*:',  # "Phone:"
            r'\boffice\s*:',  # "Office:"
            r'\bmobile\s*:',  # "Mobile:"
            r'\bcell\s*:',  # "Cell:"
            r'\bfax\s*:',  # "Fax:"
            r'\baddress\s*:',  # "Address:"
        ]

        signal_count = sum(1 for p in strong_signals if re.search(p, combined_text, re.I))

        # Also check for email/phone patterns
        has_email = bool(re.search(r'[\w\.-]+@[\w\.-]+\.\w+', combined_text))
        has_phone = bool(re.search(r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}', combined_text))

        # Strong signal if:
        # 1. Has explicit label (email:, tel:, etc.) OR
        # 2. Has both email and phone patterns
        return signal_count >= 1 or (has_email and has_phone)

    def relabel_within_group(group: Dict) -> Dict:
        """Recursively relabel unknown contact groups."""
        label = group.get('label_inferred', '').lower()

        # Check if this is an Unknown group with contact signals
        if 'unknown' in label and has_strong_contact_signals(group):
            group['label_inferred'] = 'Contact Information'
            group['meta'] = group.get('meta', {})
            group['meta']['relabeled_from_unknown'] = True
            group['meta']['relabel_reason'] = 'strong_contact_signals'

        # Recursively process subgroups
        if group.get('subgroups'):
            group['subgroups'] = [relabel_within_group(sg) for sg in group['subgroups']]

        return group

    # Process all groups
    result = [relabel_within_group(g) for g in groups]
    return result


def promote_allcaps_colon_headers(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #15: Promote all-caps entries with colons to section headers.

    Addresses issue where structured metadata lines like "PRESENT TITLE:",
    "E-MAIL ADDRESS:", "OFFICE PHONE:" are labeled "Unknown" instead of
    being recognized as semantic section headers.

    Examples:
        "PRESENT TITLE: Associate Professor" → becomes a section header
        "E-MAIL ADDRESS: john@example.edu" → becomes a section header
        "OFFICE PHONE: 555-1234" → becomes a section header

    This fix runs early in the pipeline to establish proper section structure
    before downstream repairs attempt to merge or classify these groups.

    Args:
        groups: List of groups

    Returns:
        Groups with all-caps colon entries promoted to headers
    """
    def has_allcaps_colon_header(group: Dict) -> tuple:
        """
        Check if group starts with an all-caps colon entry.

        Returns:
            (True, header_text) if found, else (False, None)
        """
        entries = group.get('entries', [])
        if not entries:
            return (False, None)

        # Check first entry for all-caps with colon pattern
        first_text = entries[0].get('text_snippet', '').strip()

        # Pattern: ALL CAPS followed by colon (optionally with content after)
        # Examples: "PRESENT TITLE:", "E-MAIL ADDRESS: john@example.edu"
        match = re.match(r'^([A-Z][A-Z\s\-]+):\s*(.*)?$', first_text)
        if match:
            header_part = match.group(1).strip()
            # Ensure it's at least 2 words or 1 long word (min 4 chars)
            if len(header_part.split()) >= 2 or len(header_part) >= 4:
                return (True, header_part)

        return (False, None)

    def promote_within_group(group: Dict) -> Dict:
        """Recursively check and promote all-caps colon headers."""
        has_header, header_text = has_allcaps_colon_header(group)

        if has_header:
            # Relabel group with extracted header
            group['label_inferred'] = header_text
            group['meta'] = group.get('meta', {})
            group['meta']['allcaps_colon_promoted'] = True
            group['meta']['original_label'] = group.get('label_inferred', 'Unknown')

        # Recursively check subgroups
        if group.get('subgroups'):
            group['subgroups'] = [promote_within_group(sg) for sg in group['subgroups']]

        return group

    result = [promote_within_group(g) for g in groups]
    return result


def enhance_email_pattern_detection(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #16: Enhance email pattern detection in signal system.

    Addresses issue where email addresses are present in text but not flagged
    by the signal detection system, leading to incomplete contact metadata.

    This fix scans all entries and adds explicit email metadata when patterns
    are detected, improving downstream identity linking and contact extraction.

    Args:
        groups: List of groups

    Returns:
        Groups with enhanced email detection metadata
    """
    # Comprehensive email regex pattern
    EMAIL_PATTERN = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'

    def detect_emails_in_group(group: Dict) -> Dict:
        """Recursively detect and flag emails in group entries."""
        entries = group.get('entries', [])
        emails_found = []

        for entry in entries:
            text = entry.get('text_snippet', '')
            matches = re.findall(EMAIL_PATTERN, text)
            if matches:
                emails_found.extend(matches)
                # Add email detection metadata to entry
                entry['meta'] = entry.get('meta', {})
                entry['meta']['emails_detected'] = matches

        # If emails found, add group-level metadata
        if emails_found:
            group['meta'] = group.get('meta', {})
            group['meta']['emails_detected'] = list(set(emails_found))  # Deduplicate
            group['meta']['email_count'] = len(emails_found)

        # Recursively check subgroups
        if group.get('subgroups'):
            group['subgroups'] = [detect_emails_in_group(sg) for sg in group['subgroups']]

        return group

    result = [detect_emails_in_group(g) for g in groups]
    return result


def detect_and_elevate_contact_information(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #19: Detect and elevate contact information to top-level Personal Data.

    Addresses issue where contact information (email, phone, address) is present
    but not properly classified, leading to 0% contact coverage in some CVs.

    This fix:
    1. Detects email, phone, and address patterns
    2. Relabels groups with strong contact signals as "Contact Information"
    3. Prioritizes top-level groups (especially G1-G5) for contact detection
    4. Adds contact_info_type metadata for downstream processing

    Examples:
        G1: Unknown
            E1: "john.doe@university.edu"
            E2: "Phone: (555) 123-4567"
        → Relabeled to:
        G1: Contact Information
            E1: "john.doe@university.edu" [meta: contact_info_type=email]
            E2: "Phone: (555) 123-4567" [meta: contact_info_type=phone]

    Args:
        groups: List of groups

    Returns:
        Groups with contact information detected and elevated
    """
    EMAIL_PATTERN = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    PHONE_PATTERN = r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'

    # Address indicators
    ADDRESS_KEYWORDS = [
        r'\bstreet\b', r'\bst\.?\b', r'\bavenue\b', r'\bave\.?\b',
        r'\broad\b', r'\brd\.?\b', r'\bdrive\b', r'\bdr\.?\b',
        r'\blane\b', r'\bln\.?\b', r'\bsuite\b', r'\bste\.?\b',
        r'\bbuilding\b', r'\broom\b', r'\bfloor\b',
        r'\bzip\b', r'\bpostal\b', r'\bcity\b', r'\bstate\b',
        r'\b\d{5}(?:-\d{4})?\b'  # ZIP code
    ]

    def detect_contact_patterns(text: str) -> Dict[str, bool]:
        """Detect contact patterns in text."""
        text_lower = text.lower()
        return {
            'has_email': bool(re.search(EMAIL_PATTERN, text)),
            'has_phone': bool(re.search(PHONE_PATTERN, text)),
            'has_address': any(re.search(pattern, text_lower, re.I) for pattern in ADDRESS_KEYWORDS)
        }

    def analyze_group_for_contact(group: Dict) -> Dict[str, Any]:
        """Analyze group for contact information signals."""
        label = group.get('label_inferred', '').lower()
        entries = group.get('entries', [])

        # Collect all text
        all_text = label + ' '
        for entry in entries:
            all_text += entry.get('text_snippet', '') + ' '

        patterns = detect_contact_patterns(all_text)

        # Check for explicit contact labels
        contact_labels = [
            r'\bemail\b', r'\be-mail\b', r'\btel\b', r'\btelephone\b',
            r'\bphone\b', r'\boffice\b', r'\bmobile\b', r'\bcell\b',
            r'\bfax\b', r'\baddress\b', r'\bcontact\b'
        ]

        has_contact_label = any(re.search(pattern, all_text, re.I) for pattern in contact_labels)

        # Calculate contact score
        score = 0
        if patterns['has_email']:
            score += 3
        if patterns['has_phone']:
            score += 2
        if patterns['has_address']:
            score += 2
        if has_contact_label:
            score += 2

        return {
            'score': score,
            'patterns': patterns,
            'has_label': has_contact_label,
            'is_top_level': group.get('level', 1) == 1
        }

    def elevate_contact_groups(grps: List[Dict]) -> List[Dict]:
        """Elevate groups with strong contact signals."""
        result = []

        for group in grps:
            analysis = analyze_group_for_contact(group)

            # Threshold for contact classification:
            # - Score >= 5: Strong contact signals (email + phone, or email + address + label)
            # - Score >= 3 AND top-level: Moderate signals at document root (likely contact section)
            should_elevate = (
                analysis['score'] >= 5 or
                (analysis['score'] >= 3 and analysis['is_top_level'])
            )

            if should_elevate:
                # Relabel as Contact Information
                group['label_inferred'] = 'Contact Information'
                group['meta'] = group.get('meta', {})
                group['meta']['elevated_to_contact'] = True
                group['meta']['contact_score'] = analysis['score']
                group['meta']['contact_patterns'] = analysis['patterns']

                # Annotate individual entries
                for entry in group.get('entries', []):
                    text = entry.get('text_snippet', '')
                    patterns = detect_contact_patterns(text)

                    entry['meta'] = entry.get('meta', {})
                    if patterns['has_email']:
                        entry['meta']['contact_info_type'] = 'email'
                    elif patterns['has_phone']:
                        entry['meta']['contact_info_type'] = 'phone'
                    elif patterns['has_address']:
                        entry['meta']['contact_info_type'] = 'address'

            # Recursively process subgroups
            if group.get('subgroups'):
                group['subgroups'] = elevate_contact_groups(group['subgroups'])

            result.append(group)

        return result

    return elevate_contact_groups(groups)


def disambiguate_g1_contact_mapping(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #20: Disambiguate G1 mapping between Contact and Educational Contributions.

    Addresses issue where the first group (G1) can be ambiguously mapped to either
    "Personal Data / Contact Information" or "Educational Contributions", causing
    inconsistent root-level taxonomy.

    This fix prioritizes Contact Information mapping when email/phone/address signals
    are present in G1, ensuring consistent contact section detection.

    Priority Rules:
    1. If G1 has email or phone patterns → Force "Contact Information" label
    2. If G1 has address + contact keywords → Force "Contact Information" label
    3. Otherwise → Keep existing label

    Args:
        groups: List of groups

    Returns:
        Groups with G1 properly disambiguated
    """
    if not groups or len(groups) == 0:
        return groups

    # Only process the first group (G1)
    g1 = groups[0]
    g1_id = g1.get('group_id', '')

    # Check if this is actually G1
    if not (g1_id == 'G1' or g1_id.startswith('G1')):
        return groups

    # Check current label
    current_label = g1.get('label_inferred', '').lower()

    # If already Contact Information, no need to change
    if 'contact' in current_label:
        return groups

    # Check for contact signals
    meta = g1.get('meta', {})
    has_contact_elevation = meta.get('elevated_to_contact', False)
    has_email_detected = meta.get('emails_detected', [])

    # If Fix #19 already elevated this to contact, keep it
    if has_contact_elevation or has_email_detected:
        g1['label_inferred'] = 'Contact Information'
        g1['meta'] = g1.get('meta', {})
        g1['meta']['g1_disambiguation_applied'] = True
        g1['meta']['disambiguation_reason'] = 'contact_signals_present'

    return groups


def promote_early_page_contact_blocks(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #21: Promote blocks in first 1000 characters containing contact patterns.

    Addresses issue where 'PRESENT TITLE', 'PRESENT OFFICE ADDRESS', and similar
    contact blocks in the first page/early sections are misclassified as "Other"
    instead of "Contact Information".

    Detection Rules:
    1. Group appears in first 3-5 top-level groups (typical first page)
    2. Contains patterns: E-MAIL, ADDRESS, TITLE, OFFICE, institutional names
    3. Text length suggests contact block (< 500 chars per entry)

    Args:
        groups: List of groups

    Returns:
        Groups with early contact blocks promoted to Contact Information
    """
    CONTACT_KEYWORDS = [
        r'\be-?mail\b', r'\baddress\b', r'\btitle\b', r'\boffice\b',
        r'\bphone\b', r'\btel\b', r'\bfax\b', r'\bcitizenship\b',
        r'\buniversity\b', r'\bdepartment\b', r'\bschool\b', r'\bcollege\b'
    ]

    # Only check first 5 top-level groups (typical first page)
    MAX_GROUPS_TO_CHECK = 5

    for i, group in enumerate(groups[:MAX_GROUPS_TO_CHECK]):
        # Skip if already Contact Information
        label = group.get('label_inferred', '').lower()
        if 'contact' in label:
            continue

        # Collect text from group
        group_text = label + ' '
        for entry in group.get('entries', [])[:3]:  # Check first 3 entries
            group_text += entry.get('text_snippet', '') + ' '

        group_text_lower = group_text.lower()

        # Count contact keyword matches
        keyword_matches = sum(1 for kw in CONTACT_KEYWORDS if re.search(kw, group_text_lower, re.I))

        # Promote if:
        # 1. Has 2+ contact keywords AND
        # 2. Text is reasonably short (contact blocks are usually brief)
        if keyword_matches >= 2 and len(group_text) < 2000:
            group['label_inferred'] = 'Contact Information'
            group['meta'] = group.get('meta', {})
            group['meta']['early_page_contact_promotion'] = True
            group['meta']['contact_keyword_count'] = keyword_matches
            group['meta']['detected_position'] = i + 1

    return groups


def detect_board_boundaries(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #22: Detect boundaries for 'Boards of Directors' style headers.

    Addresses issue where sections like "Boards of Directors/Trustees Positions"
    are incorrectly merged with following sections due to inline semicolons or
    weak separators.

    Detection Rules:
    1. Look for board-related keywords: board, director, trustee
    2. If next section starts immediately after semicolon, split it
    3. Preserve hierarchical relationships

    Args:
        groups: List of groups

    Returns:
        Groups with board sections properly bounded
    """
    BOARD_KEYWORDS = [
        r'\bboard\b', r'\bdirector\b', r'\btrustee\b',
        r'\bgovernor\b', r'\badvisory\b'
    ]

    def has_board_pattern(text: str) -> bool:
        """Check if text contains board-related keywords."""
        text_lower = text.lower()
        return any(re.search(kw, text_lower, re.I) for kw in BOARD_KEYWORDS)

    def split_on_semicolon_boundary(group: Dict) -> List[Dict]:
        """Split a group if it contains board keyword followed by semicolon."""
        label = group.get('label_inferred', '')

        # Check if label contains board keyword and semicolon
        if has_board_pattern(label) and ';' in label:
            # Split on first semicolon
            parts = label.split(';', 1)
            if len(parts) == 2:
                # Create two groups
                board_group = deepcopy(group)
                board_group['label_inferred'] = parts[0].strip()
                board_group['meta'] = board_group.get('meta', {})
                board_group['meta']['board_boundary_split'] = True

                other_group = deepcopy(group)
                other_group['label_inferred'] = parts[1].strip()
                other_group['group_id'] = group.get('group_id', '') + '_split'

                return [board_group, other_group]

        return [group]

    result = []
    for group in groups:
        split_groups = split_on_semicolon_boundary(group)
        result.extend(split_groups)

        # Recursively process subgroups
        for sg in split_groups:
            if sg.get('subgroups'):
                sg['subgroups'] = detect_board_boundaries(sg['subgroups'])

    return result


def normalize_year_ranges(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #23: Normalize year ranges for consistent chronology.

    Addresses issue where year ranges appear in inconsistent formats:
    - 2001-2002 (full years)
    - 2001-02 (shorthand)
    - 7/2001–6/2002 (with slashes)
    - 2017–2019 (en dash)

    This fix normalizes all to a standard format and adds metadata for
    temporal analytics and chronological sorting.

    Pattern: (\\d{4})[-–/](\\d{2,4})

    Args:
        groups: List of groups

    Returns:
        Groups with normalized year range metadata
    """
    # Pattern to match year ranges
    YEAR_RANGE_PATTERN = r'(\d{1,2}/)?(\d{4})[-–/](\d{1,2}/)?(\d{2,4})'

    def normalize_year_range_in_text(text: str) -> Dict[str, Any]:
        """Extract and normalize year ranges from text."""
        matches = []

        for match in re.finditer(YEAR_RANGE_PATTERN, text):
            start_prefix = match.group(1) or ''  # Optional month/day
            start_year = int(match.group(2))
            end_prefix = match.group(3) or ''
            end_year_str = match.group(4)

            # NEW FIX #33: Smart temporal normalization - handle MM/DD shorthands
            # If end_prefix exists (e.g., "08/" in "01/2018-08/31"), then end_year_str is a day, not a year
            if end_prefix:
                # Month/day shorthand detected - end year is same as start year
                end_year = start_year
            elif len(end_year_str) == 2:
                # Shorthand: 2001-02 → 2002
                century = str(start_year)[:2]
                end_year = int(century + end_year_str)
            else:
                end_year = int(end_year_str)

            # NEW FIX #41: Guard against reversed year ranges (Issue 2033_I01)
            # Handles cases like "2017-03" parsed as 2017-2003 due to shorthand logic
            if end_year < start_year:
                # Swap years to maintain chronological order
                start_year, end_year = end_year, start_year

            # Only keep valid ranges
            if 1900 <= start_year <= 2100 and 1900 <= end_year <= 2100:
                matches.append({
                    'original': match.group(0),
                    'start_year': start_year,
                    'end_year': end_year,
                    'normalized': f'{start_year}-{end_year}'
                })

        return {
            'has_year_range': len(matches) > 0,
            'ranges': matches,
            'earliest_year': min([m['start_year'] for m in matches]) if matches else None,
            'latest_year': max([m['end_year'] for m in matches]) if matches else None
        }

    def enrich_group_with_year_ranges(group: Dict) -> Dict:
        """Add year range normalization to group entries."""
        for entry in group.get('entries', []):
            text = entry.get('text_snippet', '')
            if text:
                year_data = normalize_year_range_in_text(text)
                if year_data['has_year_range']:
                    entry['meta'] = entry.get('meta', {})
                    entry['meta']['year_range_normalized'] = year_data

        # Recursively process subgroups
        if group.get('subgroups'):
            group['subgroups'] = [enrich_group_with_year_ranges(sg) for sg in group['subgroups']]

        return group

    return [enrich_group_with_year_ranges(g) for g in groups]

# NEW FIX #24, #25, #26 - To be inserted after Fix #23

def merge_similar_unknown_groups(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #24: Merge consecutive 'Unknown' groups with similar semantic patterns.
    
    Addresses over-segmentation where multiple 'Unknown' groups end up mapping
    to the same canonical section (Education, Research, Bibliography, etc.).
    
    Merging Strategy:
    1. Identify consecutive 'Unknown' siblings (same parent level)
    2. Use keyword signals to determine semantic similarity:
       - Education: degree, university, college, graduated, diploma, phd, master
       - Research: research, grant, funding, pi, investigator, project
       - Bibliography: published, journal, proceedings, conference, book, article
       - Teaching: course, teaching, lecture, instruction, curriculum
    3. Merge if both groups have 2+ matching keywords from same semantic category
    
    Args:
        groups: List of groups
    
    Returns:
        Groups with consecutive similar Unknown groups merged
    """
    SEMANTIC_PATTERNS = {
        'education': [
            r'\bdegree\b', r'\buniversity\b', r'\bcollege\b', r'\bgraduated\b',
            r'\bdiploma\b', r'\bphd\b', r'\bmaster\b', r'\bbachelor\b',
            r'\bschool\b', r'\bacademic\b', r'\bstudies\b', r'\bstudent\b'
        ],
        'research': [
            r'\bresearch\b', r'\bgrant\b', r'\bfunding\b', r'\bpi\b',
            r'\binvestigator\b', r'\bproject\b', r'\bstudy\b', r'\btrial\b',
            r'\bnih\b', r'\bnsf\b', r'\baward\b', r'\bsponsored\b'
        ],
        'bibliography': [
            r'\bpublished\b', r'\bjournal\b', r'\bproceedings\b', r'\bconference\b',
            r'\bbook\b', r'\barticle\b', r'\bpaper\b', r'\bauthor\b',
            r'\bvolume\b', r'\bissue\b', r'\bpages?\b', r'\bcitation\b'
        ],
        'teaching': [
            r'\bcourse\b', r'\bteaching\b', r'\blecture\b', r'\binstruction\b',
            r'\bcurriculum\b', r'\bseminar\b', r'\bworkshop\b', r'\beducation\b',
            r'\bstudents?\b', r'\bclass\b', r'\btraining\b'
        ],
        'employment': [
            r'\bposition\b', r'\bemployment\b', r'\bprofessor\b', r'\bassistant\b',
            r'\bassociate\b', r'\bdirector\b', r'\bmanager\b', r'\bchief\b',
            r'\bdepartment\b', r'\binstitute\b', r'\bhospital\b', r'\bcenter\b'
        ]
    }
    
    def get_semantic_category(group: Dict) -> tuple:
        """
        Determine semantic category and confidence.
        
        Returns: (category, match_count) or (None, 0)
        """
        # Collect all text from group
        text = group.get('label_inferred', '') + ' '
        for entry in group.get('entries', [])[:5]:  # Check first 5 entries
            text += entry.get('text_snippet', '') + ' '
        
        text_lower = text.lower()
        
        # Score each category
        best_category = None
        best_count = 0
        
        for category, patterns in SEMANTIC_PATTERNS.items():
            count = sum(1 for pattern in patterns if re.search(pattern, text_lower, re.I))
            if count > best_count:
                best_count = count
                best_category = category
        
        # Require at least 2 matches for confidence
        if best_count >= 2:
            return (best_category, best_count)
        
        return (None, 0)
    
    def merge_two_groups(g1: Dict, g2: Dict) -> Dict:
        """Merge g2 into g1."""
        merged = deepcopy(g1)
        merged['entries'].extend(g2.get('entries', []))
        merged['subgroups'].extend(g2.get('subgroups', []))
        merged['meta'] = merged.get('meta', {})
        merged['meta']['unknown_groups_merged'] = merged['meta'].get('unknown_groups_merged', 0) + 1
        return merged
    
    result = []
    i = 0
    
    while i < len(groups):
        current = groups[i]
        current_label = current.get('label_inferred', '').lower().strip()
        
        # Only process 'Unknown' groups
        if 'unknown' not in current_label:
            # Recursively process subgroups
            if current.get('subgroups'):
                current['subgroups'] = merge_similar_unknown_groups(current['subgroups'])
            result.append(current)
            i += 1
            continue
        
        # Check if next sibling is also Unknown with same semantic pattern
        if i + 1 < len(groups):
            next_group = groups[i + 1]
            next_label = next_group.get('label_inferred', '').lower().strip()
            
            if 'unknown' in next_label:
                # Check semantic similarity
                curr_category, curr_count = get_semantic_category(current)
                next_category, next_count = get_semantic_category(next_group)
                
                if curr_category and next_category and curr_category == next_category:
                    # Merge!
                    merged = merge_two_groups(current, next_group)
                    result.append(merged)
                    i += 2  # Skip both groups
                    continue
        
        # No merge, just add current
        if current.get('subgroups'):
            current['subgroups'] = merge_similar_unknown_groups(current['subgroups'])
        result.append(current)
        i += 1
    
    return result


def enhance_contact_signal_detection(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #25: Enhanced contact signal override for email/phone/address detection.
    
    This fix strengthens Fix #21 by adding more aggressive email, phone, and
    address pattern detection in the first 5 top-level groups. It prevents
    contact information from being misrouted to "Employment" sections.
    
    Detection Patterns:
    - Email: standard email regex
    - Phone: (XXX) XXX-XXXX, XXX-XXX-XXXX, +X formats
    - Address: Street, City, State, ZIP patterns
    - Office keywords: office, room, building, suite, floor
    
    Args:
        groups: List of groups
    
    Returns:
        Groups with enhanced contact signal promotion
    """
    EMAIL_PATTERN = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
    PHONE_PATTERN = r'(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}'
    ADDRESS_KEYWORDS = [
        r'\bstreet\b', r'\bavenue\b', r'\bdrive\b', r'\broad\b', r'\blane\b',
        r'\bboulevard\b', r'\bparkway\b', r'\bcircle\b', r'\bsuite\b',
        r'\broom\b', r'\bbuilding\b', r'\bfloor\b', r'\boffice\b',
        r'\bzip\b', r'\bpostal\b', r'\bcity\b', r'\bstate\b',
        r'\bcountry\b', r'\baddress\b', r'\blocation\b', r'\bmail\b'
    ]
    
    MAX_GROUPS_TO_CHECK = 5
    
    for i, group in enumerate(groups[:MAX_GROUPS_TO_CHECK]):
        label = group.get('label_inferred', '').lower()
        
        # Skip if already labeled as contact
        if 'contact' in label:
            continue
        
        # Collect text from group
        group_text = label + ' '
        for entry in group.get('entries', []):
            group_text += entry.get('text_snippet', '') + ' '
        
        # Count signal matches
        email_matches = len(re.findall(EMAIL_PATTERN, group_text))
        phone_matches = len(re.findall(PHONE_PATTERN, group_text))
        address_matches = sum(1 for kw in ADDRESS_KEYWORDS if re.search(kw, group_text, re.I))
        
        total_contact_signals = email_matches + phone_matches + (1 if address_matches >= 2 else 0)
        
        # Promote if we have strong contact signals
        if total_contact_signals >= 2 or email_matches >= 1:
            group['label_inferred'] = 'Contact Information'
            group['meta'] = group.get('meta', {})
            group['meta']['enhanced_contact_signal'] = True
            group['meta']['contact_signals'] = {
                'email_count': email_matches,
                'phone_count': phone_matches,
                'address_keywords': address_matches,
                'position': i + 1
            }
    
    return groups


def add_funding_vs_honors_signal(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #26: Add funding signal to separate grants from honors.
    
    Addresses issue where grant/funding sections (Research Support) are
    incorrectly routed to "Honors and Awards" due to proximity and shared
    keywords like "award", "recipient", etc.
    
    Detection Strategy:
    1. Look for funding keywords: grant, funding, support, sponsor, PI, NIH, NSF
    2. Look for role keywords: principal investigator, co-investigator, PI, co-PI
    3. Look for amount keywords: $, USD, budget, total, direct, indirect
    4. If funding signals > honor signals, relabel to "Research Support"
    
    Args:
        groups: List of groups
    
    Returns:
        Groups with funding vs honors disambiguation
    """
    FUNDING_KEYWORDS = [
        r'\bgrant\b', r'\bfunding\b', r'\bsupport\b', r'\bsponsor\b',
        r'\bnih\b', r'\bnsf\b', r'\br01\b', r'\br21\b', r'\bk23\b',
        r'\bprincipal investigator\b', r'\bpi\b', r'\bco-pi\b',
        r'\bco-investigator\b', r'\baward number\b', r'\bgrant number\b',
        r'\bbudget\b', r'\bdirect costs\b', r'\bindirect costs\b',
        r'\btotal costs\b', r'\bproject period\b', r'\bresearch support\b'
    ]
    
    HONOR_KEYWORDS = [
        r'\bhonor\b', r'\baward\b', r'\bprize\b', r'\bfellow\b',
        r'\brecognition\b', r'\bdistinguished\b', r'\bmerit\b',
        r'\bachievement\b', r'\bexcellence\b', r'\bscholarship\b',
        r'\bmedal\b', r'\bcertificate\b', r'\brecipient\b'
    ]
    
    def classify_group_as_funding_or_honor(group: Dict) -> str:
        """
        Determine if group is funding or honor.
        
        Returns: 'funding', 'honor', or 'unknown'
        """
        # Collect text
        text = group.get('label_inferred', '') + ' '
        for entry in group.get('entries', [])[:10]:  # Check first 10 entries
            text += entry.get('text_snippet', '') + ' '
        
        text_lower = text.lower()
        
        # Count keyword matches
        funding_score = sum(1 for kw in FUNDING_KEYWORDS if re.search(kw, text_lower, re.I))
        honor_score = sum(1 for kw in HONOR_KEYWORDS if re.search(kw, text_lower, re.I))
        
        # Check for dollar amounts (strong funding signal)
        if re.search(r'\$[\d,]+', text):
            funding_score += 2
        
        # Disambiguate
        if funding_score >= 3 and funding_score > honor_score:
            return 'funding'
        elif honor_score > funding_score:
            return 'honor'
        else:
            return 'unknown'
    
    def process_group(group: Dict) -> Dict:
        """Process a single group."""
        label = group.get('label_inferred', '').lower().strip()
        
        # Check if this might be confused between funding and honors
        if any(kw in label for kw in ['award', 'honor', 'recognition', 'support', 'grant', 'funding']):
            classification = classify_group_as_funding_or_honor(group)
            
            if classification == 'funding':
                group['label_inferred'] = 'Research Support'
                group['meta'] = group.get('meta', {})
                group['meta']['funding_signal_detected'] = True
            elif classification == 'honor':
                group['label_inferred'] = 'Honors and Awards'
                group['meta'] = group.get('meta', {})
                group['meta']['honor_signal_detected'] = True
        
        # Recursively process subgroups
        if group.get('subgroups'):
            group['subgroups'] = [process_group(sg) for sg in group['subgroups']]
        
        return group
    
    return [process_group(g) for g in groups]


def _infer_label_from_entry_types(entries: List[Dict]) -> str:
    """
    FIX #30.1: Infer appropriate label for _non_contact groups based on semantic content.

    V6 UPDATE: entry_type removed - now uses pure semantic content detection.

    Args:
        entries: List of entries

    Returns:
        Appropriate label string
    """
    if not entries:
        return 'Unknown'

    # Use semantic content detection (entry_type no longer available in V6)
    temp_group = {'entries': entries, 'label_inferred': 'Unknown'}
    content_category = detect_content_category(temp_group)

    # Map content categories to labels
    CATEGORY_TO_LABEL = {
        'publications': 'Publications',
        'awards': 'Awards and Honors',
        'research': 'Research',
        'education': 'Education',
        'teaching': 'Teaching',
        'clinical': 'Clinical Activities',
        'administrative': 'Administrative Activities',
        'memberships': 'Professional Memberships'
    }

    return CATEGORY_TO_LABEL.get(content_category, 'Unknown')


def detect_content_category(group: Dict) -> str:
    """
    Detect the semantic category of a group based on entry content.

    This is a generalizable content classification function that prevents
    inappropriate merging by identifying what type of content a group contains.

    Categories: contact, education, research, publications, teaching,
                clinical, administrative, awards, memberships, unknown

    Returns:
        Category string
    """
    label = group.get('label_inferred', '').lower()
    entries = group.get('entries', [])

    # Quick label-based detection for obvious cases
    if 'publication' in label or 'article' in label or 'bibliography' in label or 'journal' in label:
        return 'publications'
    if 'education' in label and 'postdoc' not in label:
        return 'education'
    if 'grant' in label or 'funding' in label:
        return 'research'
    if 'award' in label or 'honor' in label:
        return 'awards'
    if 'teaching' in label or 'course' in label:
        return 'teaching'
    if 'committee' in label or 'service' in label:
        return 'administrative'

    if not entries:
        return 'unknown'

    # Sample entries (up to first 5)
    sample_size = min(5, len(entries))
    sample_entries = entries[:sample_size]

    category_signals = {
        'contact': 0,
        'education': 0,
        'research': 0,
        'publications': 0,
        'teaching': 0,
        'clinical': 0,
        'administrative': 0,
        'awards': 0,
        'memberships': 0
    }

    for entry in sample_entries:
        text = entry.get('text_snippet', '').lower()
        entry_type = entry.get('entry_type', '').lower()

        # Publication signals (VERY DISTINCTIVE - check first!)
        if any(s in text for s in ['pmid:', 'doi:', 'et al', ') "', 'journal of', 'proc natl', 'published in']):
            category_signals['publications'] += 3
        if entry_type == 'publication':
            category_signals['publications'] += 3

        # If we have strong publication signals, short-circuit
        if category_signals['publications'] >= 6:
            return 'publications'

        # Contact signals (but exclude if in publication context)
        if any(s in text for s in ['phone:', 'fax:', 'tel:', 'address:']):
            category_signals['contact'] += 2
        # Email is weak signal (could be author email in publication)
        if '@' in text and 'et al' not in text:
            category_signals['contact'] += 1

        # Education signals
        if any(s in text for s in ['ph.d.', 'b.s.', 'm.s.', 'm.d.', 'university', 'college', 'degree']):
            if not any(s in text for s in ['fellow', 'postdoc', 'post-doctoral']):
                category_signals['education'] += 2

        # Research/grant signals
        if any(s in text for s in ['nih', 'nsf', 'grant', 'r01', 'funding', 'principal investigator', 'pi:']):
            category_signals['research'] += 2

        # Teaching signals
        if any(s in text for s in ['course', 'taught', 'instructor', 'lecture', 'seminar']):
            if 'invited' not in text:
                category_signals['teaching'] += 2

        # Clinical signals
        if any(s in text for s in ['patient', 'clinic', 'hospital', 'diagnosis', 'treatment']):
            category_signals['clinical'] += 2

        # Administrative signals
        if any(s in text for s in ['committee', 'chair', 'director', 'board member']):
            category_signals['administrative'] += 2

        # Awards signals
        if any(s in text for s in ['award', 'honor', 'prize', 'recognition', 'recipient']):
            category_signals['awards'] += 2

        # Memberships signals
        if any(s in text for s in ['member', 'society', 'association', 'organization']):
            if 'board' not in text:  # Distinguish from board membership (administrative)
                category_signals['memberships'] += 2

    # Find category with highest score
    max_category = max(category_signals.items(), key=lambda x: x[1])

    if max_category[1] >= 3:  # Need at least 3 signals
        return max_category[0]

    return 'unknown'


def should_consolidate_into_contact(group: Dict) -> bool:
    """
    Determine if a group should be consolidated into contact information.

    Uses content-based semantic analysis to prevent inappropriate merging
    (e.g., publications, awards, research sections).

    Returns:
        True if group should be treated as contact information
    """
    label = group.get('label_inferred', '').lower()

    # Explicit label check
    has_contact_label = any(kw in label for kw in ['contact', 'personal information'])

    # Detect actual content category
    content_category = detect_content_category(group)

    # CRITICAL: If content is clearly NOT contact, reject
    if content_category in ['publications', 'research', 'awards', 'education',
                            'teaching', 'clinical', 'administrative', 'memberships']:
        return False

    # If explicitly labeled as contact, accept
    if has_contact_label:
        return True

    # If content category is contact, accept
    if content_category == 'contact':
        return True

    # For unknown category, check for contact signals
    if content_category == 'unknown':
        entries = group.get('entries', [])
        if entries:
            for entry in entries[:3]:
                text = entry.get('text_snippet', '').lower()
                # Strong contact signals (not email, which could be in publications)
                if any(s in text for s in ['phone:', 'fax:', 'tel:', 'address:', 'office:']):
                    return True

        # Check metadata
        meta = group.get('meta', {})
        if any(k in meta for k in ['elevated_to_contact', 'enhanced_contact_signal']):
            return True

    return False


def final_contact_consolidation(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #28 (REFINED #30): Final consolidation of contact/personal information.

    Runs LATE in the pipeline (after all other fixes) to merge any remaining
    "Personal Information", "Contact Information", or contact-signal groups
    that were separated by intermediate fixes.

    **FIX #30 REFINEMENT**: Only merge CONTACT-TYPE entries, not entire groups.
    This prevents publications, awards, and other content from being absorbed
    into the contact section.

    **FIX #30.1 ADDITION**: Infer proper labels for _non_contact groups based on
    their entry types to avoid cosmetic metadata confusion.

    Detection Strategy:
    1. Find ALL groups labeled "Personal Information" or "Contact Information"
    2. Also find groups with strong contact signals (email, phone, address)
    3. Extract ONLY contact-type entries from these groups
    4. Leave non-contact entries (publications, awards, etc.) in separate groups

    Entry Type Filtering:
    - MERGE: contact, other, unknown, None (actual contact info)
    - EXCLUDE: publication, award, grant, presentation, position, education

    Args:
        groups: List of groups

    Returns:
        Groups with contact information consolidated, non-contact content preserved
    """
    # Only check first 15 groups (contact info should be at top)
    MAX_GROUPS_TO_CHECK = 15

    # V6 UPDATE: entry_type removed - use semantic content detection instead
    # Contact information patterns (semantic detection)
    CONTACT_PATTERNS = [
        'email', 'phone', 'address', 'fax', 'office',
        '@', 'tel:', 'department of', 'university',
        'school of', 'college of', 'institute'
    ]

    # Strong non-contact patterns (semantic detection)
    NON_CONTACT_PATTERNS = {
        'publications': ['doi:', 'pmid:', 'journal', 'published in', 'vol.', 'pp.'],
        'awards': ['award', 'prize', 'honor', 'recognition', 'fellow of'],
        'grants': ['r01', 'r21', 'u01', 'k award', 'grant #', 'funded by'],
        'positions': ['professor', 'director', 'chair', 'dean', 'appointed'],
        'education': ['ph.d.', 'm.d.', 'b.s.', 'b.a.', 'm.s.', 'degree']
    }

    def is_non_contact_entry(entry: Dict) -> bool:
        """Detect if entry is non-contact content (publication, award, grant, etc.)."""
        text = entry.get('text_snippet', '').lower()

        # Check for strong non-contact patterns
        for category, patterns in NON_CONTACT_PATTERNS.items():
            for pattern in patterns:
                if pattern in text:
                    return True

        return False

    contact_groups_with_idx = []

    # NEW: Use semantic content validation to prevent publications/awards/etc from being merged
    for i, group in enumerate(groups[:MAX_GROUPS_TO_CHECK]):
        if should_consolidate_into_contact(group):
            contact_groups_with_idx.append((i, group))

    # Only consolidate if we found 2+ contact groups
    if len(contact_groups_with_idx) < 2:
        return groups

    # Extract groups and indices
    contact_groups = [g for _, g in contact_groups_with_idx]
    contact_indices = {idx for idx, _ in contact_groups_with_idx}
    first_contact_idx = contact_groups_with_idx[0][0]

    # Create consolidated contact group
    consolidated = {
        'id': f'G{first_contact_idx+1}_contact_consolidated',
        'level': 1,
        'label_inferred': 'Contact Information',
        'entries': [],
        'subgroups': [],
        'meta': {
            'final_contact_consolidation_applied': True,
            'consolidated_count': len(contact_groups),
            'original_labels': [g.get('label_inferred') for g in contact_groups],
            'entries_filtered_by_type': True  # NEW: indicates Fix #30 applied
        }
    }

    # Track groups that have non-contact entries to preserve
    groups_with_non_contact = {}

    # FIX #30: Filter entries by semantic content before merging (V6: entry_type removed)
    for idx, contact_group in zip([i for i, _ in contact_groups_with_idx], contact_groups):
        contact_entries = []
        non_contact_entries = []

        for entry in contact_group.get('entries', []):
            # V6: Use semantic detection instead of entry_type
            if is_non_contact_entry(entry):
                # This is a non-contact entry (publication, award, etc.)
                non_contact_entries.append(entry)
            else:
                # Assume contact entry (default to contact if ambiguous)
                contact_entries.append(entry)

        # Add contact entries to consolidated group
        consolidated['entries'].extend(contact_entries)

        # If there are non-contact entries, preserve them in a separate group
        if non_contact_entries:
            groups_with_non_contact[idx] = {
                'id': contact_group.get('id', f'G{idx+1}') + '_non_contact',
                'level': contact_group.get('level', 1),
                'label_inferred': _infer_label_from_entry_types(non_contact_entries),  # FIX #30.1
                'entries': non_contact_entries,
                'subgroups': contact_group.get('subgroups', []),
                'meta': {
                    'split_from_contact_consolidation': True,
                    'original_group_id': contact_group.get('id')
                }
            }
        else:
            # All entries were contact-type, so merge subgroups too
            consolidated['subgroups'].extend(contact_group.get('subgroups', []))

    # Reconstruct groups list
    result = []
    consolidated_inserted = False

    for i, group in enumerate(groups):
        if i in contact_indices:
            # This was a contact group
            if not consolidated_inserted:
                # Insert consolidated contact group at first position
                result.append(consolidated)
                consolidated_inserted = True

            # If this group had non-contact entries, add them as separate group
            if i in groups_with_non_contact:
                result.append(groups_with_non_contact[i])
        else:
            # Not a contact group - keep it
            result.append(group)

    return result


def consolidate_professional_headers(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #27: Consolidate multi-line professional headers into single group.

    Addresses issue where consecutive position titles at document top
    (e.g., "Professor, Social Work", "Director of Research") are treated
    as distinct top-level sections instead of being nested under a single
    "Academic Appointments" or "Professional Positions" parent.

    Detection Strategy:
    1. Identify consecutive position-like groups in first 10 top-level groups
    2. Look for patterns: Professor, Director, Associate, Chair, etc.
    3. Must have entry_type="position" or position keywords in label
    4. Merge under parent "Academic Appointments" group

    Args:
        groups: List of groups

    Returns:
        Groups with professional headers consolidated
    """
    POSITION_KEYWORDS = [
        r'\bprofessor\b', r'\bassociate professor\b', r'\bassistant professor\b',
        r'\bfull professor\b', r'\badjunct professor\b', r'\bprofessor emeritus\b',
        r'\bdirector\b', r'\bchair\b', r'\bchairman\b', r'\bchairperson\b',
        r'\bdean\b', r'\bassociate dean\b', r'\bassistant dean\b',
        r'\bvice president\b', r'\bvp\b', r'\bpresident\b',
        r'\bcoordinator\b', r'\bmanager\b', r'\bsupervisor\b',
        r'\bchief\b', r'\bhead\b', r'\bleader\b',
        r'\bresearch scientist\b', r'\bsenior scientist\b',
        r'\bfaculty\b', r'\bstaff\b', r'\bclinician\b',
        r'\blecturer\b', r'\binstructor\b',
        r'\bfellow\b', r'\bscholar\b',
        r'\bacademic\b', r'\bappointments?\b',  # NEW: Added to catch "ACADEMIC APPOINTMENTS"
        r'\bpositions?\b', r'\bemployment\b',  # NEW: Position-related terms
        r'\bdepartment\b', r'\bdivision\b', r'\bcenter\b'  # Often paired with titles
    ]

    def is_career_section(group: Dict) -> bool:
        """
        Check if group is a career history section (should NOT be consolidated).

        Career sections have structured content: multiple entries, subgroups,
        and labels indicating sections like "Service", "Teaching", "Appointments".
        """
        label = group.get('label_inferred', '').lower()
        entries = group.get('entries', [])
        subgroups = group.get('subgroups', [])

        # Labels indicating career history sections (not contact headers)
        career_section_keywords = [
            'service', 'teaching', 'mentoring', 'appointments',
            'professional activities', 'committee', 'memberships',
            'review', 'reviewer', 'peer review', 'editorial',
            'undergraduate', 'graduate', 'postgraduate'
        ]

        # If labeled as a career section, exclude
        if any(kw in label for kw in career_section_keywords):
            return True

        # If has subgroups, it's a structured section (not a header line)
        if subgroups:
            return True

        # If has many entries (>5), it's a list section (not a header)
        if len(entries) > 5:
            return True

        return False

    def has_position_signal(group: Dict) -> bool:
        """Check if group appears to be a position/title (contact header line)."""
        # FIRST: Exclude career sections
        if is_career_section(group):
            return False

        # Check label first (works even for groups with no entries)
        label = group.get('label_inferred', '').lower()
        if any(re.search(kw, label, re.I) for kw in POSITION_KEYWORDS):
            return True

        # Check entry type if entries exist
        entries = group.get('entries', [])
        if entries and entries[0].get('entry_type') == 'position':
            return True

        return False

    # Only check first 10 groups (typical header region)
    MAX_GROUPS_TO_CHECK = 10

    # Find position groups (don't require consecutive - contact coalescing may have separated them)
    position_groups_with_idx = []

    for i, group in enumerate(groups[:MAX_GROUPS_TO_CHECK]):
        if has_position_signal(group):
            position_groups_with_idx.append((i, group))

    # Only consolidate if we found 2+ position groups
    if len(position_groups_with_idx) < 2:
        return groups

    # Extract just the groups and find first/last indices
    position_groups = [g for _, g in position_groups_with_idx]
    first_position_idx = position_groups_with_idx[0][0]
    last_position_idx = position_groups_with_idx[-1][0]

    # Create parent "Academic Appointments" group
    parent_group = {
        'id': f'G{first_position_idx+1}_academic_appts',
        'level': 1,
        'label_inferred': 'Academic Appointments',
        'entries': [],
        'subgroups': [],
        'meta': {
            'consolidate_professional_headers_applied': True,
            'consolidated_count': len(position_groups),
            'original_positions': [g.get('label_inferred') for g in position_groups]
        }
    }

    # Move position entries to parent group (flattened)
    for pos_group in position_groups:
        parent_group['entries'].extend(pos_group.get('entries', []))
        # Also add subgroups if any
        parent_group['subgroups'].extend(pos_group.get('subgroups', []))

    # Reconstruct groups list, removing position groups and adding consolidated parent
    # Get set of position group indices for quick lookup
    position_indices = {idx for idx, _ in position_groups_with_idx}

    result = []
    consolidated_inserted = False

    for i, group in enumerate(groups):
        if i in position_indices:
            # This is a position group - skip it, but insert consolidated parent at first position
            if not consolidated_inserted:
                result.append(parent_group)
                consolidated_inserted = True
        else:
            # Not a position group - keep it
            result.append(group)

    return result


def relabel_unknown_groups(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #29: Deterministic relabeling of Unknown groups based on content patterns.

    Addresses issue where Unknown groups/subgroups are successfully mapped but have
    confusing lineage (source_label='Unknown'). This function analyzes entry content
    and applies deterministic heuristics to provide better label hints BEFORE taxonomy
    mapping, regularizing the mapping lineage.

    Detection Strategy:
    1. Recursively find ALL Unknown groups (including subgroups)
    2. Analyze first 3-5 entries for content patterns
    3. Apply pattern matching for common CV sections:
       - Position/Employment keywords → "Employment/Positions"
       - Publication patterns (authors, years) → "Publications"
       - Award/Honor keywords → "Awards/Honors"
       - Grant/Funding keywords → "Research Support/Grants"
       - Teaching/Course keywords → "Teaching"
       - Committee/Service keywords → "Service/Committees"
       - Presentation keywords → "Presentations"
    4. Update label_inferred with better hint (keeps "Unknown" prefix for tracking)

    Priority: 40 (effort 6, impact 8)
    Impact: Regularizes mapping lineage, eliminates inconsistent unknown origins

    Args:
        groups: List of groups (may contain subgroups)

    Returns:
        Groups with Unknown labels enhanced with content-based hints
    """
    # Content pattern definitions
    POSITION_PATTERNS = [
        r'\b(professor|associate professor|assistant professor|adjunct|lecturer|instructor)\b',
        r'\b(director|chair|dean|vice president|president|coordinator|manager)\b',
        r'\b(faculty|staff|clinician|researcher|scientist|fellow)\b',
        r'\b(employment|position|appointment|title)\b'
    ]

    PUBLICATION_PATTERNS = [
        r'\b\d{4}\b',  # Year
        r'[A-Z][a-z]+,\s*[A-Z]\.?',  # Author format (e.g., "Smith, J.")
        r'\b(journal|published|article|paper|author|vol\.|volume|issue|doi)\b',
        r'\bpp?\.\s*\d+',  # Page numbers
        r'\b(et al\.|et al)\b'
    ]

    AWARD_PATTERNS = [
        r'\b(award|honor|prize|medal|fellow|fellowship|recognition|achievement)\b',
        r'\b(recipient|winner|awardee|honored|recognized)\b',
        r'\b(grant|scholarship|distinction)\b'
    ]

    GRANT_PATTERNS = [
        r'\b(grant|funding|support|award|R\d{2}|K\d{2}|P\d{2}|U\d{2})\b',  # NIH grant codes
        r'\$([\d,]+)',  # Dollar amounts
        r'\b(PI|principal investigator|co-investigator|funded)\b',
        r'\b(NIH|NSF|foundation|agency|sponsor)\b'
    ]

    TEACHING_PATTERNS = [
        r'\b(course|teaching|instructor|syllabus|curriculum|lecture|seminar)\b',
        r'\b(students?|class|enrollment|credit)\b',
        r'\b(undergraduate|graduate|doctoral)\b'
    ]

    SERVICE_PATTERNS = [
        r'\b(committee|board|council|panel|task force|working group)\b',
        r'\b(member|chair|co-chair|representative|advisor)\b',
        r'\b(service|review|editorial|peer)\b'
    ]

    PRESENTATION_PATTERNS = [
        r'\b(presentation|talk|lecture|keynote|invited|speaker)\b',
        r'\b(conference|symposium|workshop|seminar|colloquium)\b',
        r'\b(presented|speaking)\b'
    ]

    def count_pattern_matches(text: str, patterns: List[str]) -> int:
        """Count how many patterns match in the text."""
        count = 0
        text_lower = text.lower()
        for pattern in patterns:
            if re.search(pattern, text_lower, re.I):
                count += 1
        return count

    def suggest_label_from_content(group: Dict) -> Optional[str]:
        """
        Analyze group entries and suggest a better label based on content patterns.

        Returns: Suggested label or None if no strong pattern detected
        """
        entries = group.get('entries', [])
        if not entries:
            return None

        # Sample first 5 entries (or all if fewer)
        sample_entries = entries[:5]
        combined_text = ' '.join([e.get('text_snippet', '') for e in sample_entries])

        # Count pattern matches for each category
        scores = {
            'Employment/Positions': count_pattern_matches(combined_text, POSITION_PATTERNS),
            'Publications': count_pattern_matches(combined_text, PUBLICATION_PATTERNS),
            'Awards/Honors': count_pattern_matches(combined_text, AWARD_PATTERNS),
            'Research Support/Grants': count_pattern_matches(combined_text, GRANT_PATTERNS),
            'Teaching': count_pattern_matches(combined_text, TEACHING_PATTERNS),
            'Service/Committees': count_pattern_matches(combined_text, SERVICE_PATTERNS),
            'Presentations': count_pattern_matches(combined_text, PRESENTATION_PATTERNS)
        }

        # Find category with highest score (must have at least 2 matches)
        max_score = max(scores.values())
        if max_score >= 2:
            for label, score in scores.items():
                if score == max_score:
                    return label

        return None

    def process_group(group: Dict) -> Dict:
        """Recursively process group and subgroups."""
        label = group.get('label_inferred', '')

        # Check if this is an Unknown group
        if 'unknown' in label.lower():
            suggested_label = suggest_label_from_content(group)

            if suggested_label:
                # Update label with suggestion (keep "Unknown" prefix for lineage tracking)
                original_label = label
                group['label_inferred'] = f"Unknown ({suggested_label})"

                # Add metadata
                group['meta'] = group.get('meta', {})
                group['meta']['unknown_relabeling_applied'] = True
                group['meta']['original_unknown_label'] = original_label
                group['meta']['suggested_category'] = suggested_label

        # Recursively process subgroups
        if group.get('subgroups'):
            group['subgroups'] = [process_group(sg) for sg in group['subgroups']]

        return group

    # Process all top-level groups recursively
    result = [process_group(g) for g in groups]

    return result


def demote_na_placeholders(groups: List[Dict]) -> List[Dict]:
    """
    PHASE 2 FIX #13: Demote or remove N/A placeholder groups.

    Args:
        groups: List of groups

    Returns:
        Groups with N/A placeholders demoted or removed
    """
    def is_na_placeholder(group: Dict) -> bool:
        """Check if group is N/A placeholder."""
        label = group.get('label_inferred', '').strip()
        entries = group.get('entries', [])

        # Check label
        if label.upper() in ['N/A', 'NA', 'NONE', 'NOT APPLICABLE']:
            return True

        # Check if only entry is "N/A"
        if len(entries) == 1:
            text = entries[0].get('text_snippet', '').strip()
            if text.upper() in ['N/A', 'NA', 'NONE']:
                return True

        return False

    result = []

    for group in groups:
        if is_na_placeholder(group):
            # If it has subgroups, keep the subgroups but discard the N/A parent
            if group.get('subgroups'):
                # Promote subgroups to top-level
                for subgroup in group['subgroups']:
                    subgroup['level'] = 1
                    subgroup['parent_id'] = None
                    subgroup['meta'] = subgroup.get('meta', {})
                    subgroup['meta']['promoted_from_na'] = True
                    result.append(subgroup)
            # If no subgroups, just discard entirely
        else:
            # Not N/A, keep it
            # But recursively check subgroups
            if group.get('subgroups'):
                group['subgroups'] = demote_na_placeholders(group['subgroups'])
            result.append(group)

    return result


def enforce_unique_group_ids(groups: List[Dict]) -> tuple[List[Dict], int]:
    """
    NEW FIX #31: Enforce unique group IDs across all groups and subgroups.

    Multiple repair fixes can create groups with duplicate IDs (e.g., G21_non_contact appearing twice)
    or null/None IDs. This fix ensures all group IDs are globally unique.

    Args:
        groups: List of groups (may contain duplicates)

    Returns:
        Tuple of (groups with unique IDs, number of IDs that were renamed)
    """
    id_registry = set()
    rename_count = 0
    next_generated_id = 1

    def ensure_unique_id_recursive(group: Dict) -> None:
        """Recursively ensure unique IDs for group and all subgroups."""
        nonlocal rename_count, next_generated_id

        current_id = group.get('id')

        # Handle null/None IDs
        if current_id is None or current_id == 'null':
            while f'G_GENERATED_{next_generated_id}' in id_registry:
                next_generated_id += 1
            new_id = f'G_GENERATED_{next_generated_id}'
            group['id'] = new_id
            id_registry.add(new_id)
            rename_count += 1
            next_generated_id += 1
        # Handle duplicate IDs
        elif current_id in id_registry:
            # Find unique suffix
            suffix = 1
            while f'{current_id}_dup{suffix}' in id_registry:
                suffix += 1
            new_id = f'{current_id}_dup{suffix}'
            group['id'] = new_id
            id_registry.add(new_id)
            rename_count += 1
        else:
            # ID is unique, register it
            id_registry.add(current_id)

        # Recursively process subgroups
        if group.get('subgroups'):
            for subgroup in group['subgroups']:
                ensure_unique_id_recursive(subgroup)

    # Process all top-level groups
    for group in groups:
        ensure_unique_id_recursive(group)

    return groups, rename_count


def deduplicate_roman_numeral_headers(groups: List[Dict]) -> List[Dict]:
    """
    PHASE 2 FIX #15: Deduplicate Roman numeral ALL-CAPS headers.

    Example:
        G3: I. GENERAL INFORMATION
        G15: I. GENERAL INFORMATION
        → Keep only first occurrence

    Args:
        groups: List of groups

    Returns:
        Groups with Roman numeral duplicates removed
    """
    def extract_roman_section(label: str) -> tuple:
        """
        Extract Roman numeral and section name.

        Returns: (roman_numeral, section_name) or (None, label)
        """
        match = re.match(r'^([IVX]+)\.\s*(.+)$', label.strip())
        if match:
            return (match.group(1), match.group(2).strip())
        return (None, label)

    seen_sections = {}
    result = []

    for group in groups:
        label = group.get('label_inferred', '')
        roman, section_name = extract_roman_section(label)

        if roman:
            # Normalize section name
            normalized = section_name.lower().strip()

            if normalized in seen_sections:
                # Duplicate - merge with first occurrence
                first_group = seen_sections[normalized]
                first_group['entries'].extend(group.get('entries', []))
                first_group['subgroups'].extend(group.get('subgroups', []))
                first_group['meta'] = first_group.get('meta', {})
                first_group['meta']['roman_duplicates_merged'] = first_group['meta'].get('roman_duplicates_merged', 0) + 1
            else:
                # First occurrence - keep it
                seen_sections[normalized] = group
                result.append(group)
        else:
            # Not a Roman numeral header - keep it
            result.append(group)

    return result


def demote_cv_headers(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX: Demote CV document headers to metadata.

    Addresses recurring issue where "CURRICULUM VITAE" headers are misclassified
    as content sections (Appendix, Unknown, Didactic Teaching) instead of being
    treated as document-level metadata.

    Example:
        G1: CURRICULUM VITAE
            G1.1: Name
            G1.2: Email
        → Demote G1, promote children to top-level

    Args:
        groups: List of groups

    Returns:
        Groups with CV headers demoted
    """
    result = []

    for group in groups:
        label = group.get('label_inferred', '')

        # Check if this is a CV header
        if is_cv_header(label):
            # Demote by promoting children if they exist
            subgroups = group.get('subgroups', [])
            entries = group.get('entries', [])

            # If has children, promote them
            if subgroups:
                for subgroup in subgroups:
                    subgroup['level'] = 1
                    subgroup['parent_id'] = None
                    subgroup['meta'] = subgroup.get('meta', {})
                    subgroup['meta']['promoted_from_cv_header'] = True
                    subgroup['meta']['original_parent'] = group.get('id')
                    result.append(subgroup)
            # If has only entries (contact info, name), convert to "Personal Information"
            elif entries:
                group_copy = deepcopy(group)
                group_copy['label_inferred'] = 'Personal Information'
                group_copy['meta'] = group_copy.get('meta', {})
                group_copy['meta']['demoted_cv_header'] = True
                group_copy['meta']['original_label'] = label
                result.append(group_copy)
            # If empty, just discard
        else:
            # Not a CV header - keep it but process subgroups recursively
            if group.get('subgroups'):
                group['subgroups'] = demote_cv_headers(group['subgroups'])
            result.append(group)

    return result


def create_hierarchical_contact_group(contact_groups: List[Dict]) -> Optional[Dict]:
    """
    PHASE 2 FIX #6: Create hierarchical contact structure.

    Transform flat contact lines into nested structure:
        Personal Information (parent)
          ├─ Email
          ├─ Phone
          ├─ Fax
          └─ Address

    Args:
        contact_groups: List of contact-related groups

    Returns:
        Hierarchical parent group with nested subgroups, or None if no contacts
    """
    if not contact_groups:
        return None

    # Categorize contact lines by type using improved regex patterns
    emails = []
    phones = []
    faxes = []
    addresses = []
    other = []

    for group in contact_groups:
        entries = group.get('entries', [])
        if not entries:
            continue

        first_text = entries[0].get('text_snippet', '')
        label_text = group.get('label_inferred', '')
        combined_text = first_text + ' ' + label_text

        # Use regex patterns for more accurate detection
        # Email detection - use improved pattern
        if re.search(r'[\w\.-]+@[\w\.-]+\.\w+', combined_text):
            emails.append(group)
        # Phone detection - use improved patterns
        elif (re.search(r'\(?(\d{3})\)?[-.\s]?(\d{3})[-.\s]?(\d{4})', combined_text) or
              re.search(r'(\d{3})[-.\s]?(\d{4})', combined_text) or
              re.search(r'\b(?:phone|tel|telephone|mobile|cell|office)\s*:', combined_text, re.I)):
            phones.append(group)
        # Fax detection
        elif re.search(r'\b(?:fax|facsimile)\b', combined_text, re.I):
            faxes.append(group)
        # Address detection - keywords
        elif re.search(r'\b(?:address|street|suite|room|floor|building|avenue|road|drive|blvd)\b', combined_text, re.I):
            addresses.append(group)
        # City, State ZIP pattern
        elif re.search(r',\s*[A-Z]{2}\s+\d{5}', combined_text):
            addresses.append(group)
        else:
            # Check if it's an institution/location name (all caps, short)
            if is_all_caps_header(group.get('label_inferred', '')) and len(entries) == 1:
                addresses.append(group)
            else:
                other.append(group)

    # If no categorized contacts, return None
    if not (emails or phones or faxes or addresses):
        return None

    # Build hierarchical structure
    parent_group = deepcopy(contact_groups[0])
    parent_group['label_inferred'] = 'Personal Information'
    parent_group['entries'] = []  # Parent has no direct entries
    parent_group['subgroups'] = []
    parent_group['level'] = 1

    next_subgroup_id = 1

    # Create typed subgroups
    if emails:
        email_group = _merge_groups(emails, 'Email')
        email_group['level'] = 2
        email_group['id'] = f"{parent_group.get('id', 'G_CONTACT')}.{next_subgroup_id}"
        email_group['parent_id'] = parent_group.get('id')
        parent_group['subgroups'].append(email_group)
        next_subgroup_id += 1

    if phones:
        phone_group = _merge_groups(phones, 'Phone')
        phone_group['level'] = 2
        phone_group['id'] = f"{parent_group.get('id', 'G_CONTACT')}.{next_subgroup_id}"
        phone_group['parent_id'] = parent_group.get('id')
        parent_group['subgroups'].append(phone_group)
        next_subgroup_id += 1

    if faxes:
        fax_group = _merge_groups(faxes, 'Fax')
        fax_group['level'] = 2
        fax_group['id'] = f"{parent_group.get('id', 'G_CONTACT')}.{next_subgroup_id}"
        fax_group['parent_id'] = parent_group.get('id')
        parent_group['subgroups'].append(fax_group)
        next_subgroup_id += 1

    if addresses:
        address_group = _merge_groups(addresses, 'Address')
        address_group['level'] = 2
        address_group['id'] = f"{parent_group.get('id', 'G_CONTACT')}.{next_subgroup_id}"
        address_group['parent_id'] = parent_group.get('id')
        parent_group['subgroups'].append(address_group)
        next_subgroup_id += 1

    # Add other uncategorized contacts as subgroups
    for group in other:
        group['level'] = 2
        group['parent_id'] = parent_group.get('id')
        parent_group['subgroups'].append(group)

    parent_group['meta'] = parent_group.get('meta', {})
    parent_group['meta']['repair_applied'] = 'hierarchical_contact_grouping'
    parent_group['meta']['contact_types_found'] = {
        'email': len(emails) > 0,
        'phone': len(phones) > 0,
        'fax': len(faxes) > 0,
        'address': len(addresses) > 0,
        'other': len(other) > 0
    }

    return parent_group


def merge_duplicate_employment_titles(groups: List[Dict]) -> List[Dict]:
    """
    NEW FIX #6: Merge duplicate employment titles within same timeframe.

    Addresses issue where same position appears multiple times under Employment,
    creating redundant entries.

    Example:
        G42: Employment
            G42.1: "Attending General Orthopaedic Surgeon (2015-2020)"
            G42.2: "Attending General Orthopaedic Surgeon (2015-2020)"
        → Merged to:
        G42: Employment
            G42.1: "Attending General Orthopaedic Surgeon (2015-2020)" (combined entries)

    Args:
        groups: List of groups

    Returns:
        Groups with duplicate employment titles merged
    """
    def extract_title_and_dates(text: str) -> tuple:
        """
        Extract job title and date range from entry text.

        Returns: (title, date_str) or (text, None)
        """
        # Common date patterns in CVs
        date_patterns = [
            r'(\d{4})\s*[-–—]\s*(\d{4})',  # 2015-2020, 2015–2020
            r'(\d{4})\s*[-–—]\s*(?:present|current)',  # 2015-present
            r'(\d{1,2}/\d{4})\s*[-–—]\s*(\d{1,2}/\d{4})',  # 01/2015-12/2020
        ]

        for pattern in date_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                # Extract title (everything before the date)
                title = text[:match.start()].strip()
                # Remove trailing punctuation and parentheses
                title = re.sub(r'[,:\(\)]+$', '', title).strip()
                date_str = match.group(0)
                return (title, date_str)

        # No date found
        return (text.strip(), None)

    def normalize_title(title: str) -> str:
        """Normalize title for comparison."""
        # Remove extra whitespace, lowercase, remove punctuation
        normalized = re.sub(r'\s+', ' ', title.lower())
        normalized = re.sub(r'[^\w\s]', '', normalized)
        return normalized.strip()

    def is_employment_section(group: Dict) -> bool:
        """Check if group is an employment section."""
        label = group.get('label_inferred', '').lower()
        employment_keywords = [
            'employment', 'position', 'appointment', 'experience',
            'work history', 'professional experience', 'career'
        ]
        return any(kw in label for kw in employment_keywords)

    def merge_within_group(group: Dict) -> Dict:
        """Recursively merge duplicate employment titles within a group."""
        # Process subgroups first
        if group.get('subgroups'):
            merged_subgroups = []
            title_map = {}  # normalized_title -> {date_str -> [subgroups]}

            for subgroup in group['subgroups']:
                # Recursively process
                subgroup = merge_within_group(subgroup)

                # Extract title and dates from entries
                entries = subgroup.get('entries', [])
                if entries:
                    # Use first entry as representative
                    first_text = entries[0].get('text_snippet', '')
                    title, date_str = extract_title_and_dates(first_text)
                    normalized = normalize_title(title)

                    # Group by title
                    if normalized not in title_map:
                        title_map[normalized] = {}

                    # Group by date
                    date_key = date_str if date_str else 'no_date'
                    if date_key not in title_map[normalized]:
                        title_map[normalized][date_key] = []

                    title_map[normalized][date_key].append(subgroup)
                else:
                    # No entries, keep as is
                    merged_subgroups.append(subgroup)

            # Merge duplicates within each title+date combination
            for normalized_title, date_groups in title_map.items():
                for date_key, subgroup_list in date_groups.items():
                    if len(subgroup_list) == 1:
                        # No duplicates
                        merged_subgroups.append(subgroup_list[0])
                    else:
                        # Merge duplicates
                        primary = deepcopy(subgroup_list[0])

                        # Combine entries from all duplicates
                        for other in subgroup_list[1:]:
                            primary['entries'].extend(other.get('entries', []))
                            primary['subgroups'].extend(other.get('subgroups', []))

                        primary['meta'] = primary.get('meta', {})
                        primary['meta']['repair_applied'] = 'duplicate_employment_merge'
                        primary['meta']['merged_count'] = len(subgroup_list)
                        primary['meta']['merged_ids'] = [sg.get('id') for sg in subgroup_list]

                        merged_subgroups.append(primary)

            group['subgroups'] = merged_subgroups

        return group

    # Process all groups
    result = []
    for group in groups:
        # Check if this is an employment section
        if is_employment_section(group):
            # Merge duplicates within this section
            group = merge_within_group(group)

        # Also check subgroups recursively
        if group.get('subgroups'):
            for i, subgroup in enumerate(group['subgroups']):
                if is_employment_section(subgroup):
                    group['subgroups'][i] = merge_within_group(subgroup)

        result.append(group)

    return result


def contain_grants_under_research_support(groups: List[Dict]) -> List[Dict]:
    """
    PHASE 2 FIX #10: Nest grant entries under unified Research Support parent.

    Collects all grant-related groups and nests them under a single parent.

    Args:
        groups: List of groups

    Returns:
        Groups with grants properly nested
    """
    grant_groups = []
    non_grant_groups = []
    research_support_parent = None

    def is_grant_group(group: Dict) -> bool:
        """Check if group contains grant content."""
        label = group.get('label_inferred', '').lower()
        entries = group.get('entries', [])

        # Check label for grant keywords
        grant_keywords = ['grant', 'funding', 'research support', 'extramural',
                         'sponsored research', 'research funding']
        if any(kw in label for kw in grant_keywords):
            return True

        # Check entries for grant indicators
        grant_count = 0
        for entry in entries[:5]:  # Check first 5 entries
            text = entry.get('text_snippet', '').lower()
            # Grant indicators: NIH codes, agency names, PI roles, dollar amounts
            if any(indicator in text for indicator in
                   ['grant', 'nih', 'nsf', 'r01', 'r21', 'k99', 'k23', 'u01', 'p01',
                    'pi:', 'co-i:', 'principal investigator', 'co-investigator',
                    '$', 'funding', 'award', 'fellowship']):
                grant_count += 1

        return grant_count >= 2  # At least 2 entries with grant indicators

    # Separate grant from non-grant groups
    for group in groups:
        label_lower = group.get('label_inferred', '').lower()

        if 'research support' in label_lower or 'funding' in label_lower:
            # This is likely the research support parent
            if not research_support_parent:
                research_support_parent = group
            else:
                # Multiple research support sections - merge them
                grant_groups.append(group)
        elif is_grant_group(group):
            grant_groups.append(group)
        else:
            non_grant_groups.append(group)

    # If we have grants but no parent, create one
    if grant_groups:
        if not research_support_parent:
            research_support_parent = {
                'id': f'G_RESEARCH_SUPPORT',
                'label_inferred': 'Research Support',
                'level': 1,
                'entries': [],
                'subgroups': [],
                'meta': {
                    'repair_applied': 'grant_containment_created_parent'
                }
            }

        # Nest grants under parent
        for grant_group in grant_groups:
            grant_group['level'] = 2
            grant_group['parent_id'] = research_support_parent.get('id')

            # If grant_group already has subgroups, keep them
            if grant_group not in research_support_parent.get('subgroups', []):
                research_support_parent['subgroups'].append(grant_group)

        research_support_parent['meta'] = research_support_parent.get('meta', {})
        research_support_parent['meta']['repair_applied'] = 'grant_containment'
        research_support_parent['meta']['grants_nested'] = len(grant_groups)

        # Rebuild group list
        result = non_grant_groups + [research_support_parent]
        return result

    # No grants found, return original
    return groups


# ============================================================================
# MAIN REPAIR PIPELINE
# ============================================================================

def llm_context_repair_for_unknowns(
    groups: List[Dict],
    cv_context: Optional[Dict] = None,
    verbose: bool = False
) -> List[Dict]:
    """
    PHASE 2 FIX #21: LLM-based context repair for Unknown groups.

    Uses GPT-4o-mini to analyze Unknown groups in context and suggest better labels.
    Only enabled when needed for low-coverage CVs (e.g., Yisheng with ~30% hint coverage).

    Args:
        groups: List of segmented groups
        cv_context: Optional CV metadata (name, field, etc.) - not currently used
        verbose: Print repair details

    Returns:
        Groups with Unknown sections reclassified where possible

    Expected Impact:
        - Major improvement for low-coverage CVs (Yisheng: 3/10 → 9/10)
        - Cost: ~$0.01-0.03 per CV with many unknowns
        - Uses ~50 tokens per unknown section
    """
    unknown_groups = []
    other_groups = []

    # Separate unknowns from other groups
    for group in groups:
        label = group.get('label_inferred', '')
        if 'unknown' in label.lower():
            unknown_groups.append(group)
        else:
            other_groups.append(group)

    if not unknown_groups:
        return groups  # No unknowns to repair

    # Build context from nearby sections
    context_labels = [g.get('label_inferred', '') for g in other_groups[:10]]
    context_str = ', '.join(context_labels) if context_labels else 'No other sections detected'

    repaired_unknowns = []
    llm_calls = 0

    for unknown_group in unknown_groups:
        entries = unknown_group.get('entries', [])

        if not entries:
            # No content to analyze - keep as unknown
            repaired_unknowns.append(unknown_group)
            continue

        # Sample up to 3 entries (up to 200 chars each)
        sample_texts = []
        for entry in entries[:3]:
            text = entry.get('text_snippet', '')
            if text:
                sample_texts.append(text[:200])

        if not sample_texts:
            repaired_unknowns.append(unknown_group)
            continue

        sample_text = '\n'.join(sample_texts)

        # Build LLM prompt
        prompt = f"""This is an "Unknown" section from an academic CV. Based on the content and surrounding context, what is the most likely section type?

CV Context: Nearby sections include: {context_str}

Unknown Section Content:
{sample_text}

Classify this section as one of:
- Education
- Employment/Positions
- Publications
- Grants/Research Support
- Awards/Honors
- Teaching
- Service/Committee
- Presentations
- Other (specify)

Respond with just the section name."""

        try:
            result = call_llm(stage="core_repair_segmentation", messages=[{"role": "user", "content": prompt}])

            suggested_label = result["content"].strip()
            llm_calls += 1

            # Update group with LLM suggestion
            original_label = unknown_group.get('label_inferred', 'Unknown')
            unknown_group['label_inferred'] = suggested_label
            unknown_group['meta'] = unknown_group.get('meta', {})
            unknown_group['meta']['llm_context_repair'] = True
            unknown_group['meta']['original_label'] = original_label

            if verbose:
                print(f"    LLM repaired: '{original_label}' → '{suggested_label}'")

            repaired_unknowns.append(unknown_group)

        except Exception as e:
            # If LLM call fails, keep as Unknown
            if verbose:
                print(f"    LLM repair failed for unknown group: {e}")
            repaired_unknowns.append(unknown_group)

    if verbose and llm_calls > 0:
        print(f"  ✓ PHASE 2 FIX #21: LLM context repair ({llm_calls} unknowns reclassified)")

    return other_groups + repaired_unknowns


def repair_segmentation(
    segmented_cv: Dict[str, Any],
    verbose: bool = True,
    enable_llm_repair: bool = False
) -> Dict[str, Any]:
    """
    Apply all structural repairs to segmented CV.

    Args:
        segmented_cv: Segmented CV dictionary
        verbose: Print repair statistics
        enable_llm_repair: Enable PHASE 2 FIX #21 (LLM context repair for unknowns)
                          Only use for low-coverage CVs. Costs ~$0.01-0.03 per CV.

    Returns:
        Repaired CV dictionary
    """
    if verbose:
        print("="*80)
        print("SEGMENTATION REPAIR - STRUCTURAL FIXES")
        print("="*80)
        print()

    repaired_cv = deepcopy(segmented_cv)
    groups = repaired_cv.get('groups', [])

    original_count = len(groups)

    # Apply repairs in priority order
    if verbose:
        print("Applying fixes:")

    # NEW FIX: CV header demotion (FIRST - before other repairs)
    pre_count = len(groups)
    groups = demote_cv_headers(groups)
    if verbose:
        demoted = pre_count - len(groups)
        promoted = len(groups) - pre_count
        if demoted > 0:
            print(f"  ✓ NEW FIX: CV header demotion ({demoted} headers demoted)")
        elif promoted > 0:
            print(f"  ✓ NEW FIX: CV header demotion ({promoted} children promoted)")
        else:
            print(f"  ✓ NEW FIX: CV header demotion (0 headers found)")

    # PHASE 2 FIX #12: Repeated header deduplication (EARLY - before other merges)
    pre_count = len(groups)
    groups = deduplicate_repeated_headers(groups)
    if verbose:
        merged = pre_count - len(groups)
        print(f"  ✓ PHASE 2 FIX #12: Repeated header dedup ({merged} duplicates merged)")

    # PHASE 2 FIX #15: Roman numeral header deduplication
    pre_count = len(groups)
    groups = deduplicate_roman_numeral_headers(groups)
    if verbose:
        merged = pre_count - len(groups)
        print(f"  ✓ PHASE 2 FIX #15: Roman numeral dedup ({merged} duplicates merged)")

    # NEW FIX #15: All-caps colon header promotion (EARLY - before contact coalescing)
    groups = promote_allcaps_colon_headers(groups)
    if verbose:
        # Count promoted groups
        promoted_count = 0
        def count_promoted(grps):
            nonlocal promoted_count
            for g in grps:
                if g.get('meta', {}).get('allcaps_colon_promoted'):
                    promoted_count += 1
                if g.get('subgroups'):
                    count_promoted(g['subgroups'])
        count_promoted(groups)
        if promoted_count > 0:
            print(f"  ✓ NEW FIX #15: All-caps colon header promotion ({promoted_count} headers promoted)")
        else:
            print(f"  ✓ NEW FIX #15: All-caps colon header promotion (0 headers found)")

    # PHASE 2 FIX #8: Institution header merging
    pre_count = len(groups)
    groups = merge_adjacent_institution_headers(groups)
    if verbose:
        merged = pre_count - len(groups)
        print(f"  ✓ PHASE 2 FIX #8: Institution header merge ({merged} merged)")

    # FIX #6: Contact coalescing
    groups = merge_contact_groups(groups)
    if verbose:
        print(f"  ✓ FIX #6: Contact coalescing ({original_count} → {len(groups)} groups)")

    # NEW FIX #27: Consolidate professional headers (AFTER contact coalescing to avoid absorption)
    pre_count = len(groups)
    groups = consolidate_professional_headers(groups)
    if verbose:
        consolidated = pre_count - len(groups)
        if consolidated > 0:
            # Find the consolidated group to get count
            consolidated_count = 0
            for g in groups:
                if g.get('meta', {}).get('consolidate_professional_headers_applied'):
                    consolidated_count = g['meta']['consolidated_count']
                    break
            print(f"  ✓ NEW FIX #27: Professional header consolidation ({consolidated_count} headers merged into 1 group)")
        else:
            print(f"  ✓ NEW FIX #27: Professional header consolidation (0 headers found)")

    # NEW FIX #11: Institutional affiliation sequence merging
    pre_count = len(groups)
    groups = merge_institutional_affiliation_sequences(groups)
    if verbose:
        merged = pre_count - len(groups)
        if merged > 0:
            print(f"  ✓ NEW FIX #11: Institutional affiliation merge ({merged} sequences merged)")
        else:
            print(f"  ✓ NEW FIX #11: Institutional affiliation merge (0 sequences found)")

    # PHASE 2 FIX #9: Address block merging
    pre_count = len(groups)
    groups = merge_address_blocks(groups)
    if verbose:
        merged = pre_count - len(groups)
        print(f"  ✓ PHASE 2 FIX #9: Address block merge ({merged} blocks merged)")

    # FIX #3: Funding containment (BEFORE ALL-CAPS to preserve numbered grant labels)
    pre_count = len(groups)
    groups = enforce_funding_containment(groups)
    if verbose:
        contained = pre_count - len(groups)
        print(f"  ✓ FIX #3: Funding containment ({contained} grants re-nested)")

    # FIX #1: ALL-CAPS promotion (AFTER funding so grants are already nested)
    pre_count = len(groups)
    groups = promote_all_caps_subgroups(groups)
    if verbose:
        promoted = len(groups) - pre_count
        print(f"  ✓ FIX #1: ALL-CAPS promotion ({promoted} subgroups promoted)")

    # FIX #2: Education metadata
    groups = add_education_metadata(groups)
    if verbose:
        print(f"  ✓ FIX #2: Education metadata added")

    # NEW FIX: Split mixed education/postdoc groups
    groups, split_count = split_mixed_education_postdoc_groups(groups)
    if verbose:
        if split_count > 0:
            print(f"  ✓ NEW FIX: Education/postdoc split ({split_count} mixed groups split)")
        else:
            print(f"  ✓ NEW FIX: Education/postdoc split (0 mixed groups found)")

    # FIX #5: Talk promotion from publications
    pre_count = len(groups)
    groups = promote_talk_entries_from_publications(groups)
    if verbose:
        promoted = len(groups) - pre_count
        print(f"  ✓ FIX #5: Talk promotion from pubs ({promoted} promoted)")

    # PHASE 2 FIX #10: Grant containment under Research Support
    pre_count = len(groups)
    groups = contain_grants_under_research_support(groups)
    if verbose:
        contained = pre_count - len(groups)
        print(f"  ✓ PHASE 2 FIX #10: Grant containment ({contained} grants nested)")

    # NEW FIX #6: Duplicate employment title merging
    pre_count = len(groups)
    groups = merge_duplicate_employment_titles(groups)
    if verbose:
        # Count merged entries by checking metadata
        merge_count = 0
        def count_merges(grps):
            nonlocal merge_count
            for g in grps:
                if g.get('meta', {}).get('repair_applied') == 'duplicate_employment_merge':
                    merge_count += g.get('meta', {}).get('merged_count', 1) - 1
                if g.get('subgroups'):
                    count_merges(g['subgroups'])
        count_merges(groups)
        print(f"  ✓ NEW FIX #6: Duplicate employment merge ({merge_count} duplicates merged)")

    # NEW FIX #7: Duplicate text snippet deduplication
    groups = deduplicate_identical_entries(groups)
    if verbose:
        # Count removed duplicates
        removed_count = 0
        def count_removed(grps):
            nonlocal removed_count
            for g in grps:
                removed_count += g.get('meta', {}).get('duplicates_removed', 0)
                if g.get('subgroups'):
                    count_removed(g['subgroups'])
        count_removed(groups)
        print(f"  ✓ NEW FIX #7: Duplicate entry deduplication ({removed_count} duplicates removed)")

    # NEW FIX #13: Tabular block deduplication
    groups = deduplicate_tabular_blocks(groups)
    if verbose:
        # Count removed tabular block duplicates
        tabular_count = 0
        def count_tabular(grps):
            nonlocal tabular_count
            for g in grps:
                tabular_count += g.get('meta', {}).get('tabular_block_duplicates_removed', 0)
                if g.get('subgroups'):
                    count_tabular(g['subgroups'])
        count_tabular(groups)
        if tabular_count > 0:
            print(f"  ✓ NEW FIX #13: Tabular block deduplication ({tabular_count} block duplicates removed)")
        else:
            print(f"  ✓ NEW FIX #13: Tabular block deduplication (0 blocks found)")

    # NEW FIX #12: Empty entry filtering (LATE - cleanup step)
    groups = filter_empty_entries(groups)
    if verbose:
        # Count removed empty and noise entries
        empty_count = 0
        noise_count = 0
        def count_filtered(grps):
            nonlocal empty_count, noise_count
            for g in grps:
                filtered_meta = g.get('meta', {}).get('entries_filtered', {})
                empty_count += filtered_meta.get('empty_removed', 0)
                noise_count += filtered_meta.get('noise_removed', 0)
                # Also check legacy field for backwards compatibility
                empty_count += g.get('meta', {}).get('empty_entries_removed', 0)
                if g.get('subgroups'):
                    count_filtered(g['subgroups'])
        count_filtered(groups)
        total_filtered = empty_count + noise_count
        if noise_count > 0:
            print(f"  ✓ NEW FIX #12: Entry filtering ({empty_count} empty + {noise_count} noise = {total_filtered} removed)")
        else:
            print(f"  ✓ NEW FIX #12: Empty entry filtering ({empty_count} empty entries removed)")

    # NEW FIX #14: Relabel unknown groups with contact signals (LATE - before taxonomy)
    groups = relabel_unknown_contact_groups(groups)
    if verbose:
        # Count relabeled groups
        relabeled_count = 0
        def count_relabeled(grps):
            nonlocal relabeled_count
            for g in grps:
                if g.get('meta', {}).get('relabeled_from_unknown'):
                    relabeled_count += 1
                if g.get('subgroups'):
                    count_relabeled(g['subgroups'])
        count_relabeled(groups)
        if relabeled_count > 0:
            print(f"  ✓ NEW FIX #14: Unknown contact relabeling ({relabeled_count} groups relabeled)")
        else:
            print(f"  ✓ NEW FIX #14: Unknown contact relabeling (0 groups found)")

    # NEW FIX #16: Enhance email pattern detection (LATE - metadata enrichment)
    groups = enhance_email_pattern_detection(groups)
    if verbose:
        # Count groups with emails detected
        email_groups = 0
        total_emails = 0
        def count_emails(grps):
            nonlocal email_groups, total_emails
            for g in grps:
                if g.get('meta', {}).get('emails_detected'):
                    email_groups += 1
                    total_emails += g['meta']['email_count']
                if g.get('subgroups'):
                    count_emails(g['subgroups'])
        count_emails(groups)
        if email_groups > 0:
            print(f"  ✓ NEW FIX #16: Email pattern detection ({total_emails} emails in {email_groups} groups)")
        else:
            print(f"  ✓ NEW FIX #16: Email pattern detection (0 emails found)")

    # NEW FIX #19: Detect and elevate contact information (LATE - after email detection)
    groups = detect_and_elevate_contact_information(groups)
    if verbose:
        # Count elevated contact groups
        contact_elevated_count = 0
        def count_contact_elevation(grps):
            nonlocal contact_elevated_count
            for g in grps:
                if g.get('meta', {}).get('elevated_to_contact'):
                    contact_elevated_count += 1
                if g.get('subgroups'):
                    count_contact_elevation(g['subgroups'])
        count_contact_elevation(groups)
        if contact_elevated_count > 0:
            print(f"  ✓ NEW FIX #19: Contact information elevation ({contact_elevated_count} groups elevated)")
        else:
            print(f"  ✓ NEW FIX #19: Contact information elevation (0 groups found)")

    # NEW FIX #20: Disambiguate G1 contact mapping (LATE - after contact elevation)
    groups = disambiguate_g1_contact_mapping(groups)
    if verbose:
        # Check if G1 was disambiguated
        g1_disambiguated = False
        if groups and groups[0].get('meta', {}).get('g1_disambiguation_applied'):
            g1_disambiguated = True
        if g1_disambiguated:
            print(f"  ✓ NEW FIX #20: G1 contact disambiguation (G1 resolved to Contact Information)")
        else:
            print(f"  ✓ NEW FIX #20: G1 contact disambiguation (no disambiguation needed)")

    # NEW FIX #21: Promote early-page contact blocks (LATE - after contact elevation)
    groups = promote_early_page_contact_blocks(groups)
    if verbose:
        # Count promoted groups
        early_contact_count = 0
        def count_early_contact(grps):
            nonlocal early_contact_count
            for g in grps:
                if g.get('meta', {}).get('early_page_contact_promotion'):
                    early_contact_count += 1
                if g.get('subgroups'):
                    count_early_contact(g['subgroups'])
        count_early_contact(groups)
        if early_contact_count > 0:
            print(f"  ✓ NEW FIX #21: Early-page contact promotion ({early_contact_count} groups promoted)")
        else:
            print(f"  ✓ NEW FIX #21: Early-page contact promotion (0 groups found)")

    # NEW FIX #22: Detect board boundaries (LATE - structural splitting)
    pre_count = len(groups)
    groups = detect_board_boundaries(groups)
    if verbose:
        split_count = len(groups) - pre_count
        # Count split groups
        board_splits = 0
        def count_board_splits(grps):
            nonlocal board_splits
            for g in grps:
                if g.get('meta', {}).get('board_boundary_split'):
                    board_splits += 1
                if g.get('subgroups'):
                    count_board_splits(g['subgroups'])
        count_board_splits(groups)
        if board_splits > 0:
            print(f"  ✓ NEW FIX #22: Board boundary detection ({board_splits} sections split)")
        else:
            print(f"  ✓ NEW FIX #22: Board boundary detection (0 splits needed)")

    # NEW FIX #23: Normalize year ranges (LATE - metadata enrichment)
    groups = normalize_year_ranges(groups)
    if verbose:
        # Count entries with normalized ranges
        range_count = 0
        def count_ranges(grps):
            nonlocal range_count
            for g in grps:
                for e in g.get('entries', []):
                    if e.get('meta', {}).get('year_range_normalized'):
                        range_count += 1
                if g.get('subgroups'):
                    count_ranges(g['subgroups'])
        count_ranges(groups)
        if range_count > 0:
            print(f"  ✓ NEW FIX #23: Year range normalization ({range_count} ranges normalized)")
        else:
            print(f"  ✓ NEW FIX #23: Year range normalization (0 ranges found)")

    # NEW FIX #24: Merge similar Unknown groups (EARLY - reduces over-segmentation)
    pre_merge_count = len(groups)
    groups = merge_similar_unknown_groups(groups)
    if verbose:
        post_merge_count = len(groups)
        merged_count = pre_merge_count - post_merge_count
        if merged_count > 0:
            print(f"  ✓ NEW FIX #24: Unknown group merging ({merged_count} groups merged)")
        else:
            print(f"  ✓ NEW FIX #24: Unknown group merging (0 groups merged)")

    # NEW FIX #25: Enhanced contact signal detection (EARLY - improves routing)
    groups = enhance_contact_signal_detection(groups)
    if verbose:
        contact_promoted = 0
        def count_contact_signals(grps):
            nonlocal contact_promoted
            for g in grps:
                if g.get('meta', {}).get('enhanced_contact_signal'):
                    contact_promoted += 1
                if g.get('subgroups'):
                    count_contact_signals(g['subgroups'])
        count_contact_signals(groups)
        if contact_promoted > 0:
            print(f"  ✓ NEW FIX #25: Enhanced contact signal ({contact_promoted} groups promoted)")
        else:
            print(f"  ✓ NEW FIX #25: Enhanced contact signal (0 groups promoted)")

    # NEW FIX #26: Funding vs honors disambiguation (EARLY - improves classification)
    groups = add_funding_vs_honors_signal(groups)
    if verbose:
        funding_detected = 0
        honor_detected = 0
        def count_funding_honors(grps):
            nonlocal funding_detected, honor_detected
            for g in grps:
                if g.get('meta', {}).get('funding_signal_detected'):
                    funding_detected += 1
                if g.get('meta', {}).get('honor_signal_detected'):
                    honor_detected += 1
                if g.get('subgroups'):
                    count_funding_honors(g['subgroups'])
        count_funding_honors(groups)
        if funding_detected > 0 or honor_detected > 0:
            print(f"  ✓ NEW FIX #26: Funding/honors disambiguation ({funding_detected} funding, {honor_detected} honors)")
        else:
            print(f"  ✓ NEW FIX #26: Funding/honors disambiguation (0 changes)")

    # NEW FIX #17: Enrich entries with temporal metadata (LATE - metadata enrichment)
    groups = enrich_entries_with_years(groups, today_year=2025)
    if verbose:
        # Count enriched entries
        enriched_count = 0
        def count_enriched(grps):
            nonlocal enriched_count
            for g in grps:
                for e in g.get('entries', []):
                    if e.get('meta', {}).get('temporal'):
                        enriched_count += 1
                if g.get('subgroups'):
                    count_enriched(g['subgroups'])
        count_enriched(groups)
        if enriched_count > 0:
            print(f"  ✓ NEW FIX #17: Temporal metadata enrichment ({enriched_count} entries enriched)")
        else:
            print(f"  ✓ NEW FIX #17: Temporal metadata enrichment (0 entries found)")

    # NEW FIX #18: Merge consecutive same-label groups (LATE - structural consolidation)
    pre_count = len(groups)
    groups = merge_consecutive_same_label_groups(groups)
    if verbose:
        merged = pre_count - len(groups)
        # Count merge metadata
        merge_count = 0
        temporal_merge_count = 0
        def count_merges(grps):
            nonlocal merge_count, temporal_merge_count
            for g in grps:
                if g.get('meta', {}).get('merged_from'):
                    merge_count += g.get('meta', {}).get('merge_count', 1) - 1  # subtract 1 since original is kept
                    # Check if this was a temporal merge
                    if g.get('meta', {}).get('merge_rationale', {}).get('temporal_merge'):
                        temporal_merge_count += 1
                if g.get('subgroups'):
                    count_merges(g['subgroups'])
        count_merges(groups)
        if merged > 0:
            temporal_note = f" [{temporal_merge_count} temporal]" if temporal_merge_count > 0 else ""
            print(f"  ✓ NEW FIX #18: Consecutive label merging ({merged} groups merged, {merge_count} consolidations{temporal_note})")
        else:
            print(f"  ✓ NEW FIX #18: Consecutive label merging (0 groups merged)")

    # NEW FIX #28: Final contact consolidation (LATE - after all other fixes)
    pre_count = len(groups)
    groups = final_contact_consolidation(groups)
    if verbose:
        consolidated = pre_count - len(groups)
        if consolidated > 0:
            # Find the consolidated group to get count
            contact_count = 0
            for g in groups:
                if g.get('meta', {}).get('final_contact_consolidation_applied'):
                    contact_count = g['meta']['consolidated_count']
                    break
            print(f"  ✓ NEW FIX #28: Final contact consolidation ({contact_count} groups merged into 1)")
        else:
            print(f"  ✓ NEW FIX #28: Final contact consolidation (0 contact groups found)")

    # NEW FIX #29: Deterministic Unknown group relabeling (LATE - before mapping)
    groups = relabel_unknown_groups(groups)
    if verbose:
        # Count relabeled groups (including subgroups recursively)
        relabeled_count = 0
        def count_relabeled(grps):
            nonlocal relabeled_count
            for g in grps:
                if g.get('meta', {}).get('unknown_relabeling_applied'):
                    relabeled_count += 1
                if g.get('subgroups'):
                    count_relabeled(g['subgroups'])
        count_relabeled(groups)
        if relabeled_count > 0:
            print(f"  ✓ NEW FIX #29: Unknown group relabeling ({relabeled_count} groups relabeled with content hints)")
        else:
            print(f"  ✓ NEW FIX #29: Unknown group relabeling (0 unknown groups found)")

    # PHASE 2 FIX #13: N/A placeholder demotion (LATE - cleanup step)
    pre_count = len(groups)
    groups = demote_na_placeholders(groups)
    if verbose:
        removed = pre_count - len(groups)
        print(f"  ✓ PHASE 2 FIX #13: N/A placeholder demotion ({removed} placeholders removed)")

    # PHASE 2 FIX #21: LLM context repair for unknowns (OPTIONAL - only for low-coverage CVs)
    if enable_llm_repair:
        groups = llm_context_repair_for_unknowns(groups, verbose=verbose)

    # NEW FIX #31: Enforce unique group IDs (FINAL - ensure no duplicate IDs)
    groups, renamed_ids = enforce_unique_group_ids(groups)
    if verbose:
        if renamed_ids > 0:
            print(f"  ✓ NEW FIX #31: Unique ID enforcement ({renamed_ids} IDs renamed)")
        else:
            print(f"  ✓ NEW FIX #31: Unique ID enforcement (all IDs already unique)")

    repaired_cv['groups'] = groups

    # Update metadata
    if 'meta' not in repaired_cv:
        repaired_cv['meta'] = {}

    fixes_applied = [
        'cv_header_demotion',  # NEW FIX: Demote "CURRICULUM VITAE" headers
        'professional_header_consolidation',  # NEW FIX #27: Consolidate multi-line position headers
        'repeated_header_deduplication',  # PHASE 2 FIX #12
        'roman_numeral_deduplication',  # PHASE 2 FIX #15
        'allcaps_colon_header_promotion',  # NEW FIX #15: Promote all-caps colon headers
        'institution_header_merge',  # PHASE 2 FIX #8
        'contact_coalescing',
        'institutional_affiliation_merge',  # NEW FIX #11: Merge institutional affiliation sequences
        'address_block_merge',  # PHASE 2 FIX #9
        'all_caps_promotion',
        'funding_containment',
        'education_metadata',
        'education_postdoc_split',  # NEW FIX: Split mixed education/postdoc groups into separate groups
        'talk_promotion_from_publications',
        'grant_containment_under_research_support',  # PHASE 2 FIX #10
        'duplicate_employment_merge',  # NEW FIX #6: Merge duplicate employment titles
        'duplicate_entry_deduplication',  # NEW FIX #7 (enhanced #32): Remove duplicate text snippets + DOI/PMID matching
        'tabular_block_deduplication',  # NEW FIX #13: Remove duplicate tabular blocks
        'empty_entry_filtering',  # NEW FIX #12: Filter out empty entries
        'unknown_contact_relabeling',  # NEW FIX #14: Relabel Unknown groups with contact signals
        'email_pattern_detection',  # NEW FIX #16: Enhance email pattern detection
        'contact_information_elevation',  # NEW FIX #19: Detect and elevate contact information
        'g1_contact_disambiguation',  # NEW FIX #20: Disambiguate G1 mapping using contact signals
        'early_page_contact_promotion',  # NEW FIX #21: Promote early-page contact blocks
        'board_boundary_detection',  # NEW FIX #22: Detect board boundaries for proper splitting
        'year_range_normalization',  # NEW FIX #23 (enhanced #33): Normalize year ranges, handle MM/DD shorthands
        'unknown_group_merging',  # NEW FIX #24: Merge similar Unknown groups to reduce over-segmentation
        'enhanced_contact_signal',  # NEW FIX #25: Enhanced contact signal detection for email/phone/address
        'funding_honors_disambiguation',  # NEW FIX #26: Disambiguate funding/grants from honors/awards
        'temporal_metadata_enrichment',  # NEW FIX #17: Extract latest year from entries
        'consecutive_label_merging',  # NEW FIX #18: Merge consecutive same-label groups
        'final_contact_consolidation',  # FIX #28/30: Final contact consolidation with entry-type filtering
        'unknown_group_relabeling',  # NEW FIX #29: Deterministic Unknown group relabeling with content hints
        'na_placeholder_demotion',  # PHASE 2 FIX #13
        'unique_id_enforcement'  # NEW FIX #31: Ensure all group IDs are globally unique
    ]

    if enable_llm_repair:
        fixes_applied.append('llm_context_repair_for_unknowns')  # PHASE 2 FIX #21

    repaired_cv['meta']['repair'] = {
        'original_groups': original_count,
        'repaired_groups': len(groups),
        'fixes_applied': fixes_applied
    }

    # NEW FIX #35: Recalculate actual entry count (metadata was over-reporting)
    def count_entries_recursive(group_list):
        """Recursively count all entries across groups and subgroups."""
        total = 0
        for grp in group_list:
            total += len(grp.get('entries', []))
            subgroups = grp.get('subgroups', [])
            if subgroups:
                total += count_entries_recursive(subgroups)
        return total

    actual_entry_count = count_entries_recursive(groups)
    repaired_cv['meta']['total_entries'] = actual_entry_count

    # Also recalculate total_groups_including_subgroups
    def count_groups_recursive(group_list):
        """Recursively count all groups including subgroups."""
        total = len(group_list)
        for grp in group_list:
            subgroups = grp.get('subgroups', [])
            if subgroups:
                total += count_groups_recursive(subgroups)
        return total

    total_groups = count_groups_recursive(groups)
    repaired_cv['meta']['total_groups_including_subgroups'] = total_groups

    if verbose:
        print()
        print(f"Final: {original_count} → {len(groups)} top-level groups")
        print()

    return repaired_cv


# ============================================================================
# CLI
# ============================================================================

def main():
    """Command-line interface."""
    if len(sys.argv) < 2:
        print("Segmentation Repair - Fix Structural Issues")
        print()
        print("Usage: python repair_segmentation.py <input_segmented.json> [output_repaired.json]")
        print()
        print("Fixes applied:")
        print("  Phase 1:")
        print("    #6: Contact line coalescing")
        print("    #1: ALL-CAPS header promotion")
        print("    #3: Funding section containment")
        print("    #2: Education level metadata")
        print("    #5: Talk promotion from publications")
        print()
        print("  Phase 2 Session 2:")
        print("    #12: Repeated header deduplication")
        print("    #15: Roman numeral header deduplication")
        print("    #8: Institution header merging")
        print("    #9: Address block merging")
        print("    #13: N/A placeholder demotion")
        print()
        print("  Phase 2 Session 3:")
        print("    #10: Grant containment under Research Support")
        print()
        print("  Phase 2 Session 5 (optional, disabled by default):")
        print("    #21: LLM context repair for unknowns")
        print("         (enable with enable_llm_repair=True)")
        print()
        sys.exit(1)

    input_path = Path(sys.argv[1])

    if len(sys.argv) > 2:
        output_path = Path(sys.argv[2])
    else:
        output_path = input_path.parent / (input_path.stem + '_repaired.json')

    # Load
    print(f"Input: {input_path}")
    print()

    with open(input_path, 'r') as f:
        segmented_cv = json.load(f)

    # Repair
    repaired_cv = repair_segmentation(segmented_cv, verbose=True)

    # Save
    with open(output_path, 'w') as f:
        json.dump(repaired_cv, f, indent=2)

    print(f"✓ Repaired CV saved to: {output_path}")
    print()


if __name__ == '__main__':
    main()
