# Phase 2 Fixes - Complete Implementation Guide

**Date**: 2025-11-08
**Status**: 🎯 **SPECIFICATION READY** - 18 fixes documented

This document provides complete implementation specifications for all 18 remaining priority fixes identified from ChatGPT feedback analysis.

---

## Quick Reference

**Total Fixes**: 18 (organized in 5 tiers by priority)

**Files to Modify**:
- `confusion_matrix.py` - 4 new signal functions + enhancements
- `repair_segmentation.py` - 8 new repair functions + enhancements
- `taxonomy_mapper_v2.py` - 6 classification improvements

**Estimated Implementation Time**: 8-12 hours
**Expected Overall Impact**: +15-25% additional improvement beyond Phase 1

---

## TIER 1: High-Impact Contact & Structure Fixes (4 fixes)

### Fix #6: Contact Merging/Hierarchical Grouping
**Priority**: 1.5 | **Effort**: 3/10 | **Impact**: 8/10
**CVs Affected**: Bush, Frank_Lau, Petersen, Ncebner_Nov

**Current Problem**: Phone/fax/email/address lines remain as separate flat groups instead of being nested under a unified Personal Information parent.

**File**: `repair_segmentation.py`

**Implementation**:
```python
def create_hierarchical_contact_group(contact_groups: List[Dict]) -> Dict:
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
        Hierarchical parent group with nested subgroups
    """
    if not contact_groups:
        return None

    # Categorize contact lines by type
    emails = []
    phones = []
    faxes = []
    addresses = []
    other = []

    for group in contact_groups:
        entries = group.get('entries', [])
        if not entries:
            continue

        first_text = entries[0].get('text_snippet', '').lower()

        if 'email' in first_text or '@' in first_text:
            emails.append(group)
        elif any(kw in first_text for kw in ['phone', 'tel', 'mobile', 'cell']):
            phones.append(group)
        elif 'fax' in first_text:
            faxes.append(group)
        elif any(kw in first_text for kw in ['address', 'street', 'suite', 'room']):
            addresses.append(group)
        else:
            other.append(group)

    # Build hierarchical structure
    parent_group = deepcopy(contact_groups[0])
    parent_group['label_inferred'] = 'Personal Information'
    parent_group['entries'] = []  # Parent has no direct entries
    parent_group['subgroups'] = []

    # Create typed subgroups
    if emails:
        email_group = _merge_groups(emails, 'Email')
        email_group['level'] = 2
        parent_group['subgroups'].append(email_group)

    if phones:
        phone_group = _merge_groups(phones, 'Phone')
        phone_group['level'] = 2
        parent_group['subgroups'].append(phone_group)

    if faxes:
        fax_group = _merge_groups(faxes, 'Fax')
        fax_group['level'] = 2
        parent_group['subgroups'].append(fax_group)

    if addresses:
        address_group = _merge_groups(addresses, 'Address')
        address_group['level'] = 2
        parent_group['subgroups'].append(address_group)

    if other:
        for group in other:
            group['level'] = 2
            parent_group['subgroups'].append(group)

    parent_group['meta'] = parent_group.get('meta', {})
    parent_group['meta']['repair_applied'] = 'hierarchical_contact_grouping'
    parent_group['meta']['contact_types_found'] = {
        'email': len(emails) > 0,
        'phone': len(phones) > 0,
        'fax': len(faxes) > 0,
        'address': len(addresses) > 0
    }

    return parent_group
```

**Integration Point**: Call in `merge_contact_groups()` after collecting contact buffer.

**Expected Impact**: Cleaner, more organized contact sections in 4+ CVs.

---

### Fix #7: Contact Signal Enhancement (Force Promotion)
**Priority**: 1.7 | **Effort**: 3/10 | **Impact**: 9/10
**CVs Affected**: Oliver_Hobert, Almasri

**Current Problem**: Email/URL/ORCID patterns not forcing promotion to Personal Information section.

**File**: `confusion_matrix.py`

**Implementation**:
```python
def _score_url_pattern(text: str) -> float:
    """
    PHASE 2 FIX #7a: Detect URL patterns.

    Returns 1.0 if URL detected, 0.0 otherwise.
    """
    if not text:
        return 0.0

    # Match http://, https://, www., or domain.com patterns
    url_patterns = [
        r'https?://[^\s]+',
        r'www\.[^\s]+',
        r'\b[a-z0-9-]+\.(com|edu|org|gov|net)\b'
    ]

    for pattern in url_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return 1.0

    return 0.0


def _score_orcid_pattern(text: str) -> float:
    """
    PHASE 2 FIX #7b: Detect ORCID patterns.

    Returns 1.0 if ORCID detected, 0.0 otherwise.
    """
    if not text:
        return 0.0

    # ORCID format: 0000-0002-1825-0097 or orcid.org/0000-...
    orcid_patterns = [
        r'\bORCID\b',
        r'\b\d{4}-\d{4}-\d{4}-\d{3}[0-9X]\b',
        r'orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[0-9X]'
    ]

    for pattern in orcid_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return 1.0

    return 0.0
```

**Add to scores dict in `compute_structural_hints()`**:
```python
# PHASE 2 FIXES - Contact signal enhancements
'url_pattern': _score_url_pattern(entry_text),
'orcid_pattern': _score_orcid_pattern(entry_text),
```

**Add hint messages**:
```python
if scores['url_pattern'] >= 0.8 or scores['orcid_pattern'] >= 0.8:
    triggered_hints.append(
        "STRUCTURAL HINT: URL or ORCID detected "
        "→ STRONGLY suggests A (Personal Information/Contact), force promotion to top-level."
    )
```

**Expected Impact**: +9% improvement in contact section detection.

---

### Fix #8: Institution Header Merging
**Priority**: 2.2 | **Effort**: 2/10 | **Impact**: 8/10
**CVs Affected**: Albrecht

**Current Problem**: Adjacent ALL-CAPS institution headers (e.g., "UMSOM", "UNIVERSITY OF MARYLAND SCHOOL OF MEDICINE") create redundant top-level sections.

**File**: `repair_segmentation.py`

**Implementation**:
```python
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
```

**Integration Point**: Call early in `apply_all_repairs()` before other repairs.

**Expected Impact**: Cleaner top-level structure, -10-15% reduction in redundant headers.

---

### Fix #9: Address Block Merging
**Priority**: 1.7 | **Effort**: 3/10 | **Impact**: 9/10
**CVs Affected**: Dunkel_Schetter

**Current Problem**: Contiguous all-caps single-line address blocks remain fragmented.

**File**: `repair_segmentation.py`

**Implementation**:
```python
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
```

**Integration Point**: Call after `merge_contact_groups()`.

**Expected Impact**: Cleaner address consolidation in 3-5 CVs.

---

## TIER 2: Grant/Funding Improvements (3 fixes)

### Fix #10: Grant Containment
**Priority**: 1.1 | **Effort**: 4/10 | **Impact**: 9/10
**CVs Affected**: Pardini, Dunkel_Schetter, Mucci_March, Wende

**Current Problem**: Grant entries scattered across Unknown groups instead of nested under "Research Support" parent.

**File**: `repair_segmentation.py`

**Implementation**:
```python
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

        # Check label
        if any(kw in label for kw in ['grant', 'funding', 'research support', 'extramural']):
            return True

        # Check entries
        grant_count = 0
        for entry in entries[:5]:
            text = entry.get('text_snippet', '').lower()
            if any(kw in text for kw in ['grant', 'nih', 'nsf', 'r01', 'k99', 'pi:', 'co-i:', '$']):
                grant_count += 1

        return grant_count >= 2  # At least 2 entries with grant indicators

    # Separate grant from non-grant groups
    for group in groups:
        if is_grant_group(group):
            grant_groups.append(group)
        elif 'research support' in group.get('label_inferred', '').lower():
            research_support_parent = group
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
            research_support_parent['subgroups'].append(grant_group)

        research_support_parent['meta'] = research_support_parent.get('meta', {})
        research_support_parent['meta']['repair_applied'] = 'grant_containment'
        research_support_parent['meta']['grants_nested'] = len(grant_groups)

        # Rebuild group list
        result = non_grant_groups + [research_support_parent]
        return result

    return groups
```

**Integration Point**: Call late in `apply_all_repairs()` after structure is stabilized.

**Expected Impact**: +10% improvement in grant organization across 4 CVs.

---

### Fix #11: Grant Signal Detection
**Priority**: 0.8 | **Effort**: 5/10 | **Impact**: 7/10
**CVs Affected**: Christopher_Contag

**Current Problem**: Missing specific NIH/NSF grant pattern signals.

**File**: `confusion_matrix.py`

**Implementation**:
```python
def _score_grant_patterns(text: str) -> float:
    """
    PHASE 2 FIX #11: Detect specific grant patterns (NIH, NSF, R-series, etc.).

    Returns 1.0 if grant pattern detected, 0.0 otherwise.
    """
    if not text:
        return 0.0

    # NIH grant codes: R01, R21, K99, K23, P01, U01, etc.
    nih_pattern = r'\b[RPKUTF]\d{2}[-\s]?[A-Z]{2}\d{5,7}\b'

    # NSF grant numbers: typically start with NSF or have format like 1234567
    nsf_pattern = r'\b(NSF|DBI|MCB|DMS|CHE)[-\s]?\d{7,8}\b'

    # Grant role indicators
    role_pattern = r'\b(Principal Investigator|Co-Investigator|PI|Co-PI|Co-I|Multiple PI)\b'

    # Agency keywords
    agency_pattern = r'\b(NIH|NIAID|NCI|NHLBI|NIDA|NSF|DOD|DOE|NASA)\b'

    if re.search(nih_pattern, text, re.IGNORECASE):
        return 1.0
    if re.search(nsf_pattern, text, re.IGNORECASE):
        return 1.0
    if re.search(role_pattern, text, re.IGNORECASE) and re.search(agency_pattern, text, re.IGNORECASE):
        return 1.0

    return 0.0
```

**Add to scores dict**:
```python
'grant_patterns': _score_grant_patterns(entry_text),
```

**Add hint message**:
```python
if scores['grant_patterns'] >= 0.8:
    triggered_hints.append(
        "STRUCTURAL HINT: Grant pattern detected (NIH/NSF codes, PI role) "
        "→ strongly suggests M (Research Support - Grants/Funding)."
    )
```

**Expected Impact**: +7% improvement in grant detection.

---

### Fix #12: Repeated Header Deduplication
**Priority**: 1.4 | **Effort**: 4/10 | **Impact**: 10/10
**CVs Affected**: Ncebner_Nov

**Current Problem**: Repeated headers like "Grants", "Fellowships, Honors, Awards" appear multiple times as separate top-level sections.

**File**: `repair_segmentation.py`

**Implementation**:
```python
def deduplicate_repeated_headers(groups: List[Dict]) -> List[Dict]:
    """
    PHASE 2 FIX #12: Deduplicate repeated headers.

    Example:
        G10: Grants
        G15: Grants
        G20: Grants
        → Merged to: G10: Grants (with all entries)

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

            result.append(primary)

    return result
```

**Integration Point**: Call early in `apply_all_repairs()`.

**Expected Impact**: Major reduction in redundant sections (Ncebner_Nov had ~100 unknown groups, many duplicates).

---

## TIER 3: Metadata & Structural (3 fixes)

### Fix #13: N/A Placeholder Demotion
**Priority**: 1.5 | **Effort**: 3/10 | **Impact**: 8/10
**CVs Affected**: Yisheng

**Current Problem**: "N/A" placeholder groups promoted to top-level when they should be demoted or removed.

**File**: `repair_segmentation.py`

**Implementation**:
```python
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
```

**Integration Point**: Call late in `apply_all_repairs()` as cleanup step.

**Expected Impact**: Cleaner structure, removal of meaningless placeholders.

---

### Fix #14: Date-only Line Classification
**Priority**: 1.4 | **Effort**: 2/10 | **Impact**: 5/10
**CVs Affected**: Petersen

**Current Problem**: Lines containing only dates (e.g., "Updated: November 2024") classified as content instead of metadata.

**File**: `confusion_matrix.py`

**Implementation**:
```python
def _score_date_only_line(text: str) -> float:
    """
    PHASE 2 FIX #14: Detect date-only lines (metadata, not content).

    Returns 1.0 if line is date-only metadata, 0.0 otherwise.
    """
    if not text or len(text) > 100:
        return 0.0

    # Check for date-only patterns
    date_only_patterns = [
        r'^(Updated|Revised|As of|Current as of|Last updated):\s*[A-Z][a-z]+\s+\d{1,2},?\s+\d{4}$',
        r'^\d{1,2}/\d{1,2}/\d{4}$',
        r'^[A-Z][a-z]+\s+\d{4}$',  # "November 2024"
        r'^Date:\s*\d{1,2}/\d{1,2}/\d{4}$'
    ]

    for pattern in date_only_patterns:
        if re.match(pattern, text.strip(), re.IGNORECASE):
            return 1.0

    return 0.0
```

**Add to scores dict**:
```python
'date_only_line': _score_date_only_line(entry_text),
```

**Add hint message**:
```python
if scores['date_only_line'] >= 0.8:
    triggered_hints.append(
        "STRUCTURAL HINT: Date-only line detected "
        "→ suggests document metadata, NOT substantive content section."
    )
```

**Expected Impact**: Better metadata filtering.

---

### Fix #15: Roman Numeral Deduplication
**Priority**: 1.0 | **Effort**: 4/10 | **Impact**: 7/10
**CVs Affected**: Bush

**Current Problem**: ALL-CAPS headers with Roman numerals (e.g., "I. GENERAL INFORMATION", "II. EDUCATION") appear duplicated.

**File**: `repair_segmentation.py`

**Implementation**:
```python
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
```

**Integration Point**: Call after initial header promotion.

**Expected Impact**: Cleaner structure in CVs with Roman numeral formatting.

---

## TIER 4: Taxonomy Classification (5 fixes)

### Fix #16: Keynote/Invited Talk Promotion
**Priority**: 1.0 | **Effort**: 4/10 | **Impact**: 7/10
**CVs Affected**: Oliver_Hobert

**Current Problem**: Keynote and invited talks not reliably classified to R (Professional Presentations).

**File**: `confusion_matrix.py`

**Implementation**: Enhance existing `_score_keynote_indicators()` function.

```python
def _score_keynote_indicators(text: str) -> float:
    """
    PHASE 2 FIX #16 ENHANCEMENT: Improved keynote/invited talk detection.

    Returns 1.0 if keynote/invited indicators present, 0.0 otherwise.
    """
    if not text:
        return 0.0

    # Expanded keynote patterns
    keynote_patterns = [
        'keynote',
        'invited talk',
        'invited speaker',
        'invited lecture',
        'invited presentation',
        'plenary',
        'distinguished lecture',
        'named lecture',
        'commencement'
    ]

    text_lower = text.lower()
    for pattern in keynote_patterns:
        if pattern in text_lower:
            return 1.0

    return 0.0
```

**Add stronger hint message**:
```python
if scores['keynote_indicators'] >= 0.8:
    triggered_hints.append(
        "STRUCTURAL HINT: Keynote/invited talk detected "
        "→ STRONGLY suggests R (Professional Presentations - Invited/Keynote), NOT S8 (Publications)."
    )
```

**Expected Impact**: +7% improvement in keynote/invited classification.

---

### Fix #17: Committee/Service Mapping
**Priority**: 0.9 | **Effort**: 4/10 | **Impact**: 7/10
**CVs Affected**: Cvsir, Albrecht

**Current Problem**: Committee and Service roles incorrectly mapped to Administrative sections instead of P (Committee Service) or Q (Extramural Service).

**File**: `confusion_matrix.py`

**Implementation**:
```python
def _score_committee_service(text: str) -> float:
    """
    PHASE 2 FIX #17: Detect committee and service roles.

    Returns 1.0 if committee/service detected, 0.5 for weak match, 0.0 otherwise.
    """
    if not text:
        return 0.0

    text_lower = text.lower()

    # Strong committee indicators
    strong_committee = [
        'committee member',
        'committee chair',
        'steering committee',
        'advisory board',
        'editorial board',
        'review panel',
        'search committee',
        'curriculum committee'
    ]

    for pattern in strong_committee:
        if pattern in text_lower:
            return 1.0

    # Service indicators
    service_indicators = [
        'service',
        'volunteer',
        'board member',
        'reviewer',
        'editor',
        'organizer'
    ]

    for pattern in service_indicators:
        if pattern in text_lower:
            return 0.5

    return 0.0
```

**Add to scores dict**:
```python
'committee_service': _score_committee_service(entry_text),
```

**Add hint message**:
```python
if scores['committee_service'] >= 0.8:
    triggered_hints.append(
        "STRUCTURAL HINT: Committee/service role detected "
        "→ suggests P (Committee Service) or Q (Extramural Service), NOT O (Administrative)."
    )
```

**Expected Impact**: Better distinction between administrative vs service roles.

---

### Fix #18: Signal-to-Taxonomy Override
**Priority**: 0.9 | **Effort**: 5/10 | **Impact**: 8/10
**CVs Affected**: Ncebner_Nov

**Current Problem**: Strong signals (DOI, grant_amount) not overriding weak LLM classifications.

**File**: `taxonomy_mapper_v2.py`

**Implementation**: Add signal-based override in `classify_pass1_parent()`.

```python
def apply_signal_overrides(
    pass1_result: Dict[str, Any],
    signal_scores: Dict[str, float],
    entry_texts: List[str]
) -> Dict[str, Any]:
    """
    PHASE 2 FIX #18: Apply signal-to-taxonomy overrides.

    If confidence < 0.7 and strong signals present, override classification.

    Args:
        pass1_result: Original Pass 1 classification
        signal_scores: Aggregated signal scores from entries
        entry_texts: Sample entry texts

    Returns:
        Modified pass1_result (or unchanged if no override)
    """
    if pass1_result['confidence'] >= 0.7:
        return pass1_result  # High confidence, no override

    # Override rules based on signal patterns
    overrides = []

    # DOI → Publications (S)
    if signal_scores.get('doi', 0) >= 0.6:
        overrides.append(('S', 'Bibliography (Publications)', 0.85, 'DOI pattern'))

    # PMID → Publications (S)
    if signal_scores.get('pmid', 0) >= 0.6:
        overrides.append(('S', 'Bibliography (Publications)', 0.85, 'PMID pattern'))

    # Grant amount → Research Support (M)
    if signal_scores.get('grant_amount', 0) >= 0.6:
        overrides.append(('M', 'Research Support', 0.80, 'Grant amount'))

    # Grant patterns → Research Support (M)
    if signal_scores.get('grant_patterns', 0) >= 0.8:
        overrides.append(('M', 'Research Support', 0.80, 'Grant pattern'))

    # Keynote indicators → Presentations (R)
    if signal_scores.get('keynote_indicators', 0) >= 0.8:
        overrides.append(('R', 'Professional Presentations', 0.80, 'Keynote indicator'))

    # Committee service → Committee Service (P)
    if signal_scores.get('committee_service', 0) >= 0.8:
        overrides.append(('P', 'Committee Service', 0.75, 'Committee pattern'))

    # Apply highest-confidence override
    if overrides:
        overrides.sort(key=lambda x: x[2], reverse=True)  # Sort by confidence
        section_id, section_name, confidence, reason = overrides[0]

        original = f"{pass1_result['parent_section_id']} ({pass1_result['parent_canonical_name']})"
        pass1_result['parent_section_id'] = section_id
        pass1_result['parent_canonical_name'] = section_name
        pass1_result['confidence'] = confidence
        pass1_result['reasoning'] += f" [OVERRIDE by PHASE 2 FIX #18: {reason} signal. Original: {original}]"

    return pass1_result
```

**Integration**: Call after `classify_pass1_parent()` but before grant keyword fallback.

**Expected Impact**: +8% improvement in signal-driven classification.

---

### Fix #19: Conference Pattern Signal
**Priority**: 0.7 | **Effort**: 5/10 | **Impact**: 6/10
**CVs Affected**: Almasri

**Current Problem**: Conference presentations not detected.

**File**: `confusion_matrix.py`

**Implementation**:
```python
def _score_conference_pattern(text: str) -> float:
    """
    PHASE 2 FIX #19: Detect conference presentation patterns.

    Returns 1.0 if conference pattern detected, 0.0 otherwise.
    """
    if not text:
        return 0.0

    text_lower = text.lower()

    # Conference indicators
    conference_patterns = [
        'annual meeting',
        'international conference',
        'symposium',
        'workshop',
        'congress',
        'summit',
        'colloquium',
        r'\bconference\b',
        'society for',
        'association for'
    ]

    for pattern in conference_patterns:
        if re.search(pattern, text_lower):
            return 1.0

    # Check for year + location pattern (e.g., "Boston, MA, 2023")
    if re.search(r'\b\d{4}\b.*,\s*[A-Z]{2}\b', text) or re.search(r'[A-Z][a-z]+,\s*[A-Z]{2}.*\d{4}', text):
        # Likely a presentation with location and date
        return 0.5

    return 0.0
```

**Add to scores dict**:
```python
'conference_pattern': _score_conference_pattern(entry_text),
```

**Add hint message**:
```python
if scores['conference_pattern'] >= 0.8:
    triggered_hints.append(
        "STRUCTURAL HINT: Conference pattern detected "
        "→ suggests R (Professional Presentations) or possibly S8 (Abstracts/Posters)."
    )
```

**Expected Impact**: +6% improvement in conference presentation detection.

---

### Fix #20: Award/Teaching Signal Enhancement
**Priority**: 0.7 | **Effort**: 5/10 | **Impact**: 6/10
**CVs Affected**: Dabelko-Schoeny

**Current Problem**: Existing award_honor and teaching_role signals have low coverage.

**File**: `confusion_matrix.py`

**Implementation**: Enhance existing functions.

```python
def _score_award_honor_keywords(text: str) -> float:
    """
    PHASE 2 FIX #20a ENHANCEMENT: Expanded award/honor detection.

    Returns 1.0 if strong award keywords present, 0.0 otherwise.
    """
    if not text:
        return 0.0

    text_lower = text.lower()

    # Expanded award keywords
    award_keywords = [
        'award',
        'prize',
        'recognition',
        'honor',
        'fellow',
        'fellowship',  # When not in Education context
        'distinguished',
        'outstanding',
        'excellence',
        'achievement',
        'medal',
        'certificate of',
        'honoree',
        'recipient'
    ]

    for keyword in award_keywords:
        if keyword in text_lower:
            return 1.0

    return 0.0


def _score_teaching_role(text: str) -> float:
    """
    PHASE 2 FIX #20b ENHANCEMENT: Expanded teaching role detection.

    Returns 1.0 if teaching role detected, 0.0 otherwise.
    """
    if not text:
        return 0.0

    text_lower = text.lower()

    # Expanded teaching keywords
    teaching_keywords = [
        'instructor',
        'lecturer',
        'teaching assistant',
        'professor',
        'course director',
        'taught',
        'teaching',
        'curriculum',
        'syllabus',
        'guest lecture',
        'seminar leader',
        'lab instructor'
    ]

    for keyword in teaching_keywords:
        if keyword in text_lower:
            return 1.0

    return 0.0
```

**Expected Impact**: +6% improvement in award/teaching hint coverage.

---

## TIER 5: Advanced/LLM-based (3 fixes)

### Fix #21: LLM Context Repair
**Priority**: 0.8 | **Effort**: 6/10 | **Impact**: 9/10
**CVs Affected**: Yisheng

**Current Problem**: Ambiguous "Unknown" groups need LLM-based contextual repair.

**File**: `repair_segmentation.py`

**Implementation**:
```python
def llm_context_repair_for_unknowns(groups: List[Dict], cv_context: Dict) -> List[Dict]:
    """
    PHASE 2 FIX #21: LLM-based context repair for Unknown groups.

    Uses LLM to analyze Unknown groups in context and reclassify.

    Args:
        groups: List of groups
        cv_context: Overall CV context (name, field, etc.)

    Returns:
        Groups with Unknown sections reclassified
    """
    from openai import OpenAI
    import os

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY_WORK"))

    unknown_groups = []
    other_groups = []

    for group in groups:
        if 'unknown' in group.get('label_inferred', '').lower():
            unknown_groups.append(group)
        else:
            other_groups.append(group)

    if not unknown_groups:
        return groups  # No unknowns to repair

    # Build context for LLM
    context_labels = [g.get('label_inferred', '') for g in other_groups[:10]]
    context_str = ', '.join(context_labels)

    repaired = []
    for unknown_group in unknown_groups:
        entries = unknown_group.get('entries', [])
        sample_text = '\n'.join([e.get('text_snippet', '')[:200] for e in entries[:3]])

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
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=50
            )

            suggested_label = response.choices[0].message.content.strip()

            # Update group with LLM suggestion
            unknown_group['label_inferred'] = suggested_label
            unknown_group['meta'] = unknown_group.get('meta', {})
            unknown_group['meta']['llm_context_repair'] = True
            unknown_group['meta']['original_label'] = 'Unknown'

            repaired.append(unknown_group)
        except Exception as e:
            # If LLM call fails, keep as Unknown
            repaired.append(unknown_group)

    return other_groups + repaired
```

**Integration Point**: Call as optional cleanup step in `apply_all_repairs()` if hint coverage < 30%.

**Expected Impact**: Major improvement for low-coverage CVs (Yisheng, Ut_Format).

---

### Fix #22: Research Activities vs Outputs
**Priority**: 0.9 | **Effort**: 5/10 | **Impact**: 8/10
**CVs Affected**: Cook_Cv

**Current Problem**: Narrative "Research Activities/Interests" sections misclassified as "Research Output" (publications).

**File**: `taxonomy_mapper_v2.py`

**Implementation**: Add post-processing check.

```python
def distinguish_research_narrative_vs_output(
    pass1_result: Dict[str, Any],
    section_label: str,
    sample_entries: List[str]
) -> Dict[str, Any]:
    """
    PHASE 2 FIX #22: Distinguish narrative research sections from outputs.

    Research Activities/Interests → E (Education - Research Experience)
    Research Output → S (Bibliography)

    Args:
        pass1_result: Pass 1 classification
        section_label: Section header
        sample_entries: Sample entry texts

    Returns:
        Modified classification if needed
    """
    # Only apply if classified as S (Bibliography)
    if pass1_result['parent_section_id'] != 'S':
        return pass1_result

    section_lower = section_label.lower()

    # Check for narrative indicators in header
    narrative_keywords = ['interest', 'activities', 'experience', 'focus', 'area']
    has_narrative_header = any(kw in section_lower for kw in narrative_keywords)

    if not has_narrative_header:
        return pass1_result  # Likely legitimate publications

    # Check entry content - publications have citations, narratives don't
    citation_count = 0
    narrative_count = 0

    for entry in sample_entries[:5]:
        text_lower = entry.lower()

        # Citation indicators
        if any(indicator in text_lower for indicator in ['et al', 'journal of', 'doi:', 'pmid:', 'vol.', 'pp.']):
            citation_count += 1

        # Narrative indicators
        if any(indicator in text_lower for indicator in ['focus on', 'interested in', 'research in', 'study of', 'investigate']):
            narrative_count += 1

    # If mostly narrative, reclassify to E (Education - Research Experience)
    if narrative_count > citation_count:
        pass1_result['parent_section_id'] = 'E'
        pass1_result['parent_canonical_name'] = 'Education'
        pass1_result['reasoning'] += " [RECLASSIFIED by PHASE 2 FIX #22: Research narrative vs output distinction]"

    return pass1_result
```

**Integration**: Call after `classify_pass1_parent()`.

**Expected Impact**: +8% improvement in research section classification.

---

### Fix #23: LLM Routing Weight Recalibration
**Priority**: 0.7 | **Effort**: 5/10 | **Impact**: 6/10
**CVs Affected**: Ut_Format

**Current Problem**: LLM routing weights need adjustment to better distinguish personal info vs employment.

**File**: `taxonomy_contexts.py` (or add to confusion_matrix.py)

**Implementation**: Adjust disambiguation guidance.

```python
# PHASE 2 FIX #23: Enhanced disambiguation guidance

# Add to confusion matrix or taxonomy contexts:
PERSONAL_INFO_VS_EMPLOYMENT_RULES = """
CRITICAL DISTINCTION: Personal Information (A) vs Employment (D)

Personal Information (A) ONLY includes:
- Contact details: email, phone, fax, address
- ORCID, URLs, social media
- Current title/affiliation (single line at top of CV)
- Name, credentials

Employment/Positions (D) includes:
- Historical positions with date ranges
- Job descriptions
- Multiple positions over time
- Academic appointments with responsibilities

RED FLAGS for misclassification:
- If entry has DATE RANGE (e.g., "2015-2020") → D (Employment), NOT A
- If entry is INSTITUTION NAME only → might be A (address) or D (employment), check for dates
- If entry has job responsibilities/duties → D (Employment), NOT A
"""

# Add to Pass 1 system prompt when confidence < 0.7:
user_prompt += f"\n\nDISAMBIGUATION GUIDANCE:\n{PERSONAL_INFO_VS_EMPLOYMENT_RULES}\n"
```

**Expected Impact**: +6% improvement in personal info vs employment distinction.

---

## Summary & Implementation Order

**Recommended Implementation Order**:
1. **Session 1** (Tier 1 - Signals): Fixes #7, #11, #14, #19, #20 (Add all signals)
2. **Session 2** (Tier 1 - Structure): Fixes #8, #9, #15, #12, #13 (Structural repairs)
3. **Session 3** (Tier 2): Fixes #6, #10 (Complex grouping)
4. **Session 4** (Tier 4): Fixes #16, #17, #18 (Taxonomy improvements)
5. **Session 5** (Tier 5): Fixes #21, #22, #23 (LLM-based - most complex)

**Testing Checkpoints**:
- After Session 1: Test signal coverage improvement
- After Session 2: Test structural cleanliness
- After Session 3: Test contact/grant organization
- After Session 4: Test classification accuracy
- After Session 5: Full integration test on all 20 CVs

---

**Status**: ✅ **Specification Complete** - Ready for systematic implementation

**Next Action**: Begin Session 1 (Signal additions) or test Phase 1 fixes first

---

**Document Version**: 1.0
**Created**: 2025-11-08
**Last Updated**: 2025-11-08
**Author**: Claude (Anthropic)
