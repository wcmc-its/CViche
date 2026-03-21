# Implementation Guide: Phase 1 Priority Fixes

**Date**: 2025-11-08
**Based on**: ChatGPT feedback aggregation from 18 CVs
**Priority**: Top 5 fixes with best Impact × Frequency / Effort ratio

---

## Executive Summary

This guide provides detailed implementation instructions for the top 5 priority fixes identified from Phase 1 validation:

1. **Email Detection Regex** (Impact: 8/10, Effort: 2/10)
2. **Phone/Fax Signal Templates** (Impact: 6/10, Effort: 2/10)
3. **ALL-CAPS Header Promotion** (Impact: 9/10, Effort: 3/10)
4. **Grant Keyword Fallback** (Impact: 8/10, Effort: 2/10)
5. **Contact Line Deduplication** (Impact: 8/10, Effort: 2/10)

**Expected Results**:
- **+10-15%** improvement in hint coverage
- **-20-30%** reduction in "Unknown" groups
- **+5-10%** improvement in taxonomy precision

---

## Fix #1: Email Detection Regex

###Problem
Email patterns missed in 22% of CVs (4/18), resulting in lost contact metadata.

### Implementation

**File**: `signal_library.py` (or equivalent signals module)

```python
import re

# Add to signal library
SIGNALS = {
    # ... existing signals ...

    'email_address': {
        'pattern': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
        'flags': re.IGNORECASE,
        'hint': 'contact_email',
        'confidence_boost': 0.15,
        'description': 'Detects email addresses in any format'
    },

    'email_label_pattern': {
        'pattern': r'(?:email|e-mail|electronic mail)\s*:\s*([A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,})',
        'flags': re.IGNORECASE,
        'hint': 'contact_email_labeled',
        'confidence_boost': 0.20,
        'description': 'Detects labeled email addresses (Email: xxx@yyy.zzz)'
    },
}
```

**Testing**:
```python
test_strings = [
    "Email: john.doe@university.edu",
    "Contact: jane_smith@medical-center.org",
    "malmar9@jhu.edu",
    "E-mail: researcher.name+tag@example.co.uk"
]

for test in test_strings:
    assert re.search(SIGNALS['email_address']['pattern'], test, re.IGNORECASE)
```

---

## Fix #2: Phone/Fax Signal Templates

### Problem
Phone patterns missed in 28% of CVs (5/18), fax patterns rarely detected.

### Implementation

**File**: `signal_library.py`

```python
SIGNALS = {
    # ... existing signals ...

    'phone_number': {
        'pattern': r'(?:phone|tel|telephone|cell|mobile)\s*:?\s*(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})',
        'flags': re.IGNORECASE,
        'hint': 'contact_phone',
        'confidence_boost': 0.15,
        'description': 'Detects phone numbers with optional labels'
    },

    'fax_number': {
        'pattern': r'(?:fax|facsimile)\s*:?\s*(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})',
        'flags': re.IGNORECASE,
        'hint': 'contact_fax',
        'confidence_boost': 0.15,
        'description': 'Detects fax numbers with labels'
    },

    'phone_standalone': {
        'pattern': r'\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}',
        'hint': 'contact_phone_unlabeled',
        'confidence_boost': 0.10,
        'description': 'Detects standalone phone numbers (lower confidence)'
    },
}
```

**Testing**:
```python
test_cases = [
    ("Phone: (203) 691-0371", 'phone_number'),
    ("Fax: 203.691.0372", 'fax_number'),
    ("Cell: +1-203-691-0373", 'phone_number'),
    ("203-691-0374", 'phone_standalone'),
]

for text, signal_name in test_cases:
    pattern = SIGNALS[signal_name]['pattern']
    flags = SIGNALS[signal_name].get('flags', 0)
    assert re.search(pattern, text, flags)
```

---

## Fix #3: ALL-CAPS Header Promotion

### Problem
ALL-CAPS single-word or single-line headers not promoted to top-level, causing fragmentation.

### Implementation

**File**: `repair_segmentation.py`

```python
def promote_all_caps_headers(cv_data: dict) -> dict:
    """
    Promote ALL-CAPS single-line headers to top-level groups.

    Handles cases like:
    - EDUCATION
    - EMPLOYMENT HISTORY
    - HONORS AND AWARDS
    """
    changes_made = []

    for group in cv_data.get('groups', []):
        # Check if group label is all caps and relatively short
        label = group.get('label', '').strip()

        # Criteria for promotion:
        # 1. All uppercase (excluding punctuation/numbers)
        # 2. Length 3-50 characters
        # 3. Not already at top level (depth > 0)
        # 4. Single line (no newlines)

        if (label.isupper() and
            3 <= len(label) <= 50 and
            group.get('depth', 0) > 0 and
            '\n' not in label):

            # Additional check: must contain letters (not just numbers/punctuation)
            if re.search(r'[A-Z]{2,}', label):
                # Promote to top level
                old_depth = group['depth']
                group['depth'] = 0
                group['promoted_by'] = 'all_caps_header_promotion'

                changes_made.append({
                    'group_id': group.get('id'),
                    'label': label,
                    'old_depth': old_depth,
                    'new_depth': 0
                })

    if changes_made:
        print(f"  ✓ Promoted {len(changes_made)} ALL-CAPS headers to top level")
        for change in changes_made:
            print(f"    • {change['label']} (depth {change['old_depth']} → 0)")

    return cv_data
```

**Integration into repair pipeline**:
```python
def repair_segmentation_with_v2(cv_data: dict) -> dict:
    """Main repair function with all fixes."""

    # Existing repairs
    cv_data = contact_coalescing(cv_data)
    cv_data = education_metadata_tagging(cv_data)
    cv_data = funding_containment(cv_data)

    # NEW: ALL-CAPS header promotion
    cv_data = promote_all_caps_headers(cv_data)

    # Additional repairs
    cv_data = talk_promotion_from_publications(cv_data)

    return cv_data
```

**Testing**:
```python
test_cv = {
    'groups': [
        {'id': 'G1', 'label': 'John Doe', 'depth': 0},
        {'id': 'G2', 'label': 'EDUCATION', 'depth': 1},  # Should promote
        {'id': 'G3', 'label': 'Ph.D. Biology', 'depth': 2},
        {'id': 'G4', 'label': 'HONORS AND AWARDS', 'depth': 1},  # Should promote
        {'id': 'G5', 'label': 'Best Paper 2020', 'depth': 2},
        {'id': 'G6', 'label': 'contact info', 'depth': 1},  # Should NOT promote (not all caps)
    ]
}

result = promote_all_caps_headers(test_cv)

# Assertions
assert result['groups'][1]['depth'] == 0  # EDUCATION promoted
assert result['groups'][3]['depth'] == 0  # HONORS AND AWARDS promoted
assert result['groups'][5]['depth'] == 1  # contact info NOT promoted
```

---

## Fix #4: Grant Keyword Fallback

### Problem
Research support and grant sections often misclassified as "Unknown" when keywords are present.

### Implementation

**File**: `taxonomy_mapper_v2.py` (in Pass 2 fallback logic)

```python
def apply_keyword_fallback_rules(group_data: dict, entries: List[dict]) -> str:
    """
    Apply keyword-based fallback rules when LLM classification has low confidence.

    Returns: section_id or None
    """
    # Combine all text in group for keyword matching
    all_text = ' '.join([
        group_data.get('label', ''),
        *[entry.get('text', '') for entry in entries]
    ]).lower()

    # Rule 1: Grant/Funding keywords → Research Support
    grant_keywords = [
        'grant', 'fellowship', 'funding', 'award',
        'nih', 'nsf', 'r01', 'r21', 'k99', 'k23',
        'principal investigator', 'co-investigator',
        'sponsored research', 'research support'
    ]

    if any(keyword in all_text for keyword in grant_keywords):
        # Check for dollar amounts or grant numbers
        if re.search(r'\$[\d,]+|[A-Z]{2,3}[-\s]?\d{5,}', all_text):
            return 'S05'  # Research Support (Grants/Funding)

    # Rule 2: Teaching keywords → Educational Contributions
    teaching_keywords = [
        'course', 'lecture', 'seminar', 'teaching',
        'instructor', 'professor', 'syllabus'
    ]

    if any(keyword in all_text for keyword in teaching_keywords):
        if re.search(r'\d{3,4}[A-Z]?', all_text):  # Course numbers
            return 'J01'  # Didactic Teaching

    # Rule 3: Committee/Service keywords → Service
    service_keywords = [
        'committee', 'board', 'panel', 'council',
        'chair', 'member', 'reviewer', 'editorial'
    ]

    if any(keyword in all_text for keyword in service_keywords):
        if 'editorial' in all_text or 'journal' in all_text:
            return 'N02'  # Editorial Board Membership
        else:
            return 'N06'  # Service on Boards and/or Committees

    return None  # No fallback match
```

**Integration into taxonomy mapper**:
```python
def map_cv_sections_v2(cv_path: str, output_path: str, model='gpt-4o-mini'):
    """Enhanced taxonomy mapping with keyword fallbacks."""

    # ... existing code ...

    # Pass 2: Child category classification
    for group in groups_needing_pass2:
        llm_result = call_llm_for_classification(group)

        # If confidence is low, try keyword fallback
        if llm_result['confidence'] < 0.70:
            fallback_section = apply_keyword_fallback_rules(
                group,
                group['entries']
            )

            if fallback_section:
                print(f"    ↳ Keyword fallback applied: {fallback_section}")
                llm_result['section_id'] = fallback_section
                llm_result['confidence'] = 0.75  # Medium confidence
                llm_result['fallback_applied'] = True

        # Store result
        group['final_section_id'] = llm_result['section_id']
        group['final_confidence'] = llm_result['confidence']

    # ... rest of code ...
```

---

## Fix #5: Contact Line Deduplication

### Problem
`contact_coalescing` leaves duplicate contact lines or misses scattered phone/fax/email.

### Implementation

**File**: `repair_segmentation.py`

```python
def enhanced_contact_coalescing(cv_data: dict) -> dict:
    """
    Enhanced contact coalescing that:
    1. Merges all contact-related groups (email, phone, fax, address)
    2. Deduplicates identical lines
    3. Creates unified "Contact Information" block
    """
    contact_indicators = [
        'email', 'e-mail', 'phone', 'telephone', 'fax',
        'address', 'office', 'mail', '@', 'tel:',
        '\\d{3}[-.]?\\d{3}[-.]?\\d{4}'  # Phone pattern
    ]

    contact_groups = []
    non_contact_groups = []

    for group in cv_data.get('groups', []):
        label = group.get('label', '').lower()
        entries_text = ' '.join([
            e.get('text', '') for e in group.get('entries', [])
        ]).lower()

        # Check if group contains contact information
        is_contact = any(
            re.search(indicator, label + ' ' + entries_text, re.IGNORECASE)
            for indicator in contact_indicators
        )

        if is_contact and group.get('depth', 0) <= 1:
            contact_groups.append(group)
        else:
            non_contact_groups.append(group)

    if len(contact_groups) > 1:
        # Merge all contact groups
        unified_contact = {
            'id': 'G_CONTACT_UNIFIED',
            'label': 'Contact Information',
            'depth': 0,
            'entries': [],
            'merged_from': [g['id'] for g in contact_groups],
            'repair_applied': 'enhanced_contact_coalescing'
        }

        # Collect all entries and deduplicate
        seen_text = set()
        for group in contact_groups:
            for entry in group.get('entries', []):
                text = entry.get('text', '').strip()
                if text and text.lower() not in seen_text:
                    unified_contact['entries'].append(entry)
                    seen_text.add(text.lower())

        # Insert unified contact at beginning
        cv_data['groups'] = [unified_contact] + non_contact_groups

        print(f"  ✓ Coalesced {len(contact_groups)} contact groups into 1")
        print(f"    Deduplicated to {len(unified_contact['entries'])} unique entries")

    return cv_data
```

---

## Testing Strategy

### Unit Tests

Create `test_phase1_fixes.py`:

```python
import pytest
from repair_segmentation import (
    promote_all_caps_headers,
    enhanced_contact_coalescing
)
from taxonomy_mapper_v2 import apply_keyword_fallback_rules
from signal_library import SIGNALS

class TestPhase1Fixes:

    def test_email_detection(self):
        """Test email regex patterns."""
        pattern = SIGNALS['email_address']['pattern']

        valid_emails = [
            "john.doe@university.edu",
            "researcher_name@medical-center.org",
            "test+tag@example.co.uk",
        ]

        for email in valid_emails:
            assert re.search(pattern, email, re.IGNORECASE)

    def test_phone_detection(self):
        """Test phone number patterns."""
        pattern = SIGNALS['phone_number']['pattern']

        valid_phones = [
            "Phone: (203) 691-0371",
            "Tel: 203-691-0371",
            "Telephone: +1 203.691.0371",
        ]

        for phone in valid_phones:
            assert re.search(pattern, phone, re.IGNORECASE)

    def test_all_caps_promotion(self):
        """Test ALL-CAPS header promotion."""
        test_cv = {
            'groups': [
                {'id': 'G1', 'label': 'EDUCATION', 'depth': 1},
                {'id': 'G2', 'label': 'not caps', 'depth': 1},
            ]
        }

        result = promote_all_caps_headers(test_cv)

        assert result['groups'][0]['depth'] == 0  # Promoted
        assert result['groups'][1]['depth'] == 1  # Not promoted

    def test_grant_keyword_fallback(self):
        """Test grant keyword fallback."""
        test_group = {
            'label': 'Research Funding',
        }
        test_entries = [
            {'text': 'NIH Grant R01-12345, PI: John Doe, $500,000'}
        ]

        section_id = apply_keyword_fallback_rules(test_group, test_entries)

        assert section_id == 'S05'  # Research Support

    def test_contact_deduplication(self):
        """Test contact line deduplication."""
        test_cv = {
            'groups': [
                {
                    'id': 'G1',
                    'label': 'Email',
                    'depth': 0,
                    'entries': [
                        {'text': 'john@example.com'},
                        {'text': 'john@example.com'},  # Duplicate
                    ]
                },
                {
                    'id': 'G2',
                    'label': 'Phone',
                    'depth': 0,
                    'entries': [
                        {'text': '203-691-0371'}
                    ]
                }
            ]
        }

        result = enhanced_contact_coalescing(test_cv)

        # Should have 1 unified group with 2 unique entries
        assert len(result['groups']) == 1
        assert result['groups'][0]['label'] == 'Contact Information'
        assert len(result['groups'][0]['entries']) == 2  # Deduplicated
```

### Integration Test

Test on a sample CV:

```bash
# Run on Denckla CV (which had 29.7% hint coverage)
python3 test_fixes_on_sample.py validation_CV_2025_Denckla_preprocessed.json

# Expected improvements:
# - Hint coverage: 29.7% → 40%+
# - Unknown groups: 22 → < 15
# - Email detected: Yes (was missed)
# - Phone detected: Yes (was missed)
```

---

## Deployment Plan

### Phase 1: Individual Fix Testing (Week 1)

1. **Day 1-2**: Implement and test email/phone signals
2. **Day 3-4**: Implement and test ALL-CAPS promotion
3. **Day 5**: Implement and test grant fallback + contact deduplication

### Phase 2: Integration Testing (Week 2)

1. Run all fixes on 3 sample CVs (Denckla, Bush, Albrecht)
2. Compare metrics before/after
3. Adjust thresholds if needed

### Phase 3: Batch Validation (Week 2-3)

1. Re-run same 20 CVs with all fixes
2. Generate new METRICS files
3. Compare aggregate statistics:
   - Avg hint coverage improvement
   - Reduction in "Unknown" groups
   - No regression in confidence scores

### Success Criteria

- **Hint coverage**: +10% or more
- **Unknown groups**: -20% or more
- **Confidence scores**: No decrease (maintain >= 0.92)
- **Processing time**: No significant increase (< +5%)

---

## Rollback Plan

If any fix causes regressions:

1. **Disable individual fix** via feature flag
2. **Re-run batch** without problematic fix
3. **Investigate** root cause in test cases
4. **Iterate** and re-deploy

```python
# Feature flags in config
ENABLE_EMAIL_DETECTION = True
ENABLE_PHONE_DETECTION = True
ENABLE_ALL_CAPS_PROMOTION = True
ENABLE_GRANT_FALLBACK = True
ENABLE_CONTACT_DEDUPLICATION = True
```

---

## Monitoring & Validation

After deployment, monitor these metrics:

```python
def validate_fixes(old_metrics: dict, new_metrics: dict) -> dict:
    """Compare before/after metrics."""

    improvements = {
        'hint_coverage_delta': new_metrics['hint_coverage_pct'] - old_metrics['hint_coverage_pct'],
        'unknown_groups_delta': old_metrics['unknown_groups'] - new_metrics['unknown_groups'],
        'confidence_delta': new_metrics['avg_confidence'] - old_metrics['avg_confidence'],
    }

    # Success criteria
    success = (
        improvements['hint_coverage_delta'] >= 10.0 and
        improvements['unknown_groups_delta'] >= 5 and
        improvements['confidence_delta'] >= -0.02  # Allow small decrease
    )

    return {
        'success': success,
        'improvements': improvements
    }
```

---

## Next Steps After Implementation

1. **Document results** in `PHASE1_FIXES_RESULTS.md`
2. **Update** `PHASE1_COMPLETION_SUMMARY.md` with before/after comparison
3. **Prepare** for Phase 2 (broader validation with 50+ CVs)
4. **Consider** LLM re-processing for 8 flagged CVs

---

**Created**: 2025-11-08
**Last Updated**: 2025-11-08
**Status**: Ready for implementation
