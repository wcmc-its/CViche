#!/usr/bin/env python3
"""
Audit Fix Validators - Generalizable Post-Processing Fixes

This module implements validators to address systematic mapping issues identified
in CV parsing audits. Each validator is designed to be generalizable across CVs.

Implemented Fixes:
1. ✓ FIX 1: Leadership Detection (P → O) - IN confusion_matrix.py
2. FIX 2: Publication Subsection Classifier (S → S1/S2/S7/S10)
3. FIX 3: Media Content Detector (R → T)
4. FIX 4: Mentee Recipient Detection (M2 → N)
5. FIX 5: Fellowship Type Classifier (H vs C)
"""

import re
from typing import Dict, List, Optional


# ==============================================================================
# FIX 2: Publication Subsection Classifier
# ==============================================================================

def classify_publication_subsection(entry_text: str, label: str = "") -> str:
    """
    Classify publication into S subsections based on content analysis.

    Priority order (highest to lowest):
    1. S10: Preprints (specific platforms)
    2. S7: In Press / Accepted
    3. S2: Reviews / Meta-analyses
    4. S1: Research articles (default)

    Args:
        entry_text: Text content of the publication entry
        label: Optional label/header context

    Returns:
        Subsection ID: 'S1', 'S2', 'S7', or 'S10'
    """
    text_lower = (entry_text + " " + label).lower()

    # S10: Preprints (highest priority - specific platforms)
    preprint_patterns = [
        'biorxiv', 'medrxiv', 'arxiv', 'ssrn',
        'preprint', 'pre-print',
        'doi.org/10.1101',  # bioRxiv DOI prefix
        'not peer-reviewed'
    ]
    if any(p in text_lower for p in preprint_patterns):
        return 'S10'

    # S7: In Press / Accepted
    in_press_patterns = [
        'in press', 'in-press',
        'accepted', 'forthcoming', 'to appear',
        'ahead of print', 'online first',
        'epub ahead'
    ]
    if any(p in text_lower for p in in_press_patterns):
        return 'S7'

    # S2: Reviews
    review_patterns = [
        'review', 'meta-analysis', 'systematic review',
        'scoping review', 'literature review',
        'meta analysis'  # Handle spacing variations
    ]
    if any(p in text_lower for p in review_patterns):
        return 'S2'

    # S1: Research articles (default)
    return 'S1'


def refine_publication_subsections(mappings: List[Dict]) -> tuple[List[Dict], int]:
    """
    Post-process mappings to refine S (Bibliography) into subsections.

    This validator runs AFTER initial mapping and refines any groups
    mapped to 'S' into specific subsections based on entry content.

    Args:
        mappings: List of mapping dictionaries

    Returns:
        Tuple of (updated mappings, count of refined mappings)
    """
    refined_count = 0

    for mapping in mappings:
        final_id = mapping.get('final_section_id') or mapping.get('section_id')

        # Only process S (Bibliography) parent mappings
        if final_id != 'S':
            continue

        # Get entries for analysis
        entries = mapping.get('entries', [])
        if not entries:
            continue

        # Analyze entries to determine subsection
        # Use majority voting if multiple entries
        subsection_votes = {'S1': 0, 'S2': 0, 'S7': 0, 'S10': 0}

        for entry in entries[:10]:  # Sample first 10 entries
            text = entry.get('text_snippet', '') or entry.get('text', '')
            subsection = classify_publication_subsection(text, mapping.get('source_label', ''))
            subsection_votes[subsection] += 1

        # Determine winning subsection
        winner = max(subsection_votes.items(), key=lambda x: x[1])[0]

        # Update mapping
        mapping['final_section_id'] = winner
        mapping['final_canonical_name'] = _get_subsection_name(winner)
        mapping['subsection_refined'] = True
        mapping['subsection_refinement_votes'] = subsection_votes

        refined_count += 1

    return mappings, refined_count


def _get_subsection_name(subsection_id: str) -> str:
    """Get canonical name for publication subsection."""
    names = {
        'S1': 'Peer-Reviewed Research Articles',
        'S2': 'Review Articles',
        'S7': 'Publications In Press',
        'S10': 'Preprints / Working Papers'
    }
    return names.get(subsection_id, 'Bibliography')


# ==============================================================================
# FIX 3: Media Content Detector
# ==============================================================================

def detect_media_content(entry_text: str, label: str = "") -> bool:
    """
    Detect if content is media coverage/interview vs scientific presentation.

    Args:
        entry_text: Entry text to analyze
        label: Optional group label

    Returns:
        True if media content (should be T not R)
    """
    text_lower = (entry_text + " " + label).lower()

    media_signals = [
        'interviewed by', 'interviews by', 'interview with', 'interview in',
        'featured in', 'profiled in', 'appeared in',
        'press', 'news', 'media coverage', 'media article',
        'newspaper', 'magazine', 'journal article about',
        'herald', 'times', 'post', 'tribune', 'gazette',
        'cnn', 'npr', 'bbc', 'reuters', 'associated press'
    ]

    return any(signal in text_lower for signal in media_signals)


def refine_media_mappings(mappings: List[Dict]) -> tuple[List[Dict], int]:
    """
    Refine R (Invited Presentations) mappings that are actually media coverage.

    Args:
        mappings: List of mapping dictionaries

    Returns:
        Tuple of (updated mappings, count of refined mappings)
    """
    refined_count = 0

    for mapping in mappings:
        final_id = mapping.get('final_section_id') or mapping.get('section_id')

        # Only process R (Invited Presentations) mappings
        if not final_id or not final_id.startswith('R'):
            continue

        label = mapping.get('source_label', '')
        entries = mapping.get('entries', [])

        # Check label first (for header-only groups)
        if detect_media_content('', label):
            mapping['final_section_id'] = 'T'
            mapping['final_canonical_name'] = 'Other (Structural Header)'
            mapping['media_content_detected'] = True
            mapping['media_detection_source'] = 'label'
            mapping['original_section_id'] = final_id
            refined_count += 1
            continue

        # If no entries, skip further checks
        if not entries:
            continue

        # Check if entries are media content
        media_count = 0
        for entry in entries[:5]:  # Sample first 5
            text = entry.get('text_snippet', '') or entry.get('text', '')
            if detect_media_content(text, label):
                media_count += 1

        # If majority are media, remap to T
        if media_count >= len(entries[:5]) * 0.6:  # 60% threshold
            mapping['final_section_id'] = 'T'
            mapping['final_canonical_name'] = 'Other (Structural Header)'
            mapping['media_content_detected'] = True
            mapping['media_detection_source'] = 'entries'
            mapping['original_section_id'] = final_id
            refined_count += 1

    return mappings, refined_count


# ==============================================================================
# FIX 4: Mentee Recipient Detection
# ==============================================================================

def detect_mentee_recipient(entry_text: str) -> bool:
    """
    Detect if award/grant is TO a mentee (not PI's own funding).

    Patterns:
    - "fellowship to [Name]"
    - "awarded to Ms./Mr./Dr. [Name]"
    - "recipient: [Name]"

    Args:
        entry_text: Entry text to analyze

    Returns:
        True if award is to a mentee
    """
    # Pattern: "to [Title] [FirstName] [LastName]"
    mentee_patterns = [
        r'to\s+(Dr\.|Ms\.|Mr\.|Mrs\.)?\s*[A-Z][a-z]+\s+[A-Z]',
        r'awarded\s+to\s+[A-Z][a-z]+',
        r'recipient:\s*[A-Z][a-z]+',
        r'fellowship\s+for\s+[A-Z][a-z]+\s+[A-Z]',
        r'grant\s+to\s+[A-Z][a-z]+',
        r'award\s+to\s+[A-Z][a-z]+'
    ]

    for pattern in mentee_patterns:
        if re.search(pattern, entry_text):
            return True

    return False


def refine_mentee_grants(mappings: List[Dict]) -> tuple[List[Dict], int]:
    """
    Refine M2 (Grants) mappings that are actually mentee awards.

    Args:
        mappings: List of mapping dictionaries

    Returns:
        Tuple of (updated mappings, count of refined mappings)
    """
    refined_count = 0

    for mapping in mappings:
        final_id = mapping.get('final_section_id') or mapping.get('section_id')

        # Only process M2 (Research Support - Grants)
        if final_id != 'M2':
            continue

        # Check label for mentee keywords
        label = (mapping.get('source_label', '') + ' ' +
                mapping.get('label', '')).lower()

        mentee_label_signals = [
            'mentee', 'trainee', 'to lab members',
            'awarded to', 'fellows supervised',
            'students', 'postdoc', 'predoctoral'
        ]

        has_mentee_label = any(s in label for s in mentee_label_signals)

        # If label indicates mentees, reclassify immediately (even for header-only groups)
        if has_mentee_label:
            mapping['final_section_id'] = 'N3'  # Current Mentees
            mapping['final_canonical_name'] = 'Current Mentees'
            mapping['mentee_recipient_detected'] = True
            mapping['mentee_detection_source'] = 'label'
            mapping['original_section_id'] = 'M2'
            refined_count += 1
            continue

        # Check entries if no label match
        entries = mapping.get('entries', [])
        if not entries:
            continue

        mentee_entry_count = 0
        for entry in entries[:5]:
            text = entry.get('text_snippet', '') or entry.get('text', '')
            if detect_mentee_recipient(text):
                mentee_entry_count += 1

        # Remap if majority of entries have mentee recipients
        if mentee_entry_count >= len(entries[:5]) * 0.5:
            mapping['final_section_id'] = 'N3'  # Current Mentees
            mapping['final_canonical_name'] = 'Current Mentees'
            mapping['mentee_recipient_detected'] = True
            mapping['mentee_detection_source'] = 'entries'
            mapping['original_section_id'] = 'M2'
            refined_count += 1

    return mappings, refined_count


# ==============================================================================
# FIX 5: Fellowship Type Classifier
# ==============================================================================

def classify_fellowship_type(entry_text: str) -> Optional[str]:
    """
    Classify fellowship as training (C) vs honor (H).

    Args:
        entry_text: Entry text to analyze

    Returns:
        'C' for training fellowship, 'H' for honor, None if unclear
    """
    text_lower = entry_text.lower()

    # Training fellowship patterns (should be C)
    training_patterns = [
        'postdoctoral fellowship', 'postdoc fellowship', 'post-doctoral',
        'nrsa', 'f32', 'f31', 't32',
        'k award', 'k01', 'k08', 'k23', 'k99',
        'training grant', 'research fellowship',
        'max-planck fellowship', 'fulbright fellowship',
        'marie curie fellowship'
    ]

    if any(p in text_lower for p in training_patterns):
        return 'C'

    # Honor/award patterns (should be H)
    honor_patterns = [
        'excellence', 'achievement', 'distinguished',
        'lifetime', 'career award', 'recognition',
        'prize', 'medal', 'honorary'
    ]

    if any(p in text_lower for p in honor_patterns):
        return 'H'

    return None


def refine_fellowship_honors(mappings: List[Dict]) -> tuple[List[Dict], int]:
    """
    Refine H (Honors) mappings that are actually training fellowships.

    Args:
        mappings: List of mapping dictionaries

    Returns:
        Tuple of (updated mappings, count of refined mappings)
    """
    refined_count = 0

    for mapping in mappings:
        final_id = mapping.get('final_section_id') or mapping.get('section_id')

        # Only process H (Honors and Awards)
        if final_id != 'H':
            continue

        entries = mapping.get('entries', [])
        if not entries:
            continue

        # Classify entries
        training_count = 0
        honor_count = 0

        for entry in entries[:10]:
            text = entry.get('text_snippet', '') or entry.get('text', '')
            classification = classify_fellowship_type(text)
            if classification == 'C':
                training_count += 1
            elif classification == 'H':
                honor_count += 1

        # If majority are training fellowships, remap to C
        if training_count > honor_count and training_count >= 2:
            mapping['final_section_id'] = 'C1'
            mapping['final_canonical_name'] = 'Postdoctoral Training'
            mapping['fellowship_reclassified'] = True
            mapping['original_section_id'] = 'H'
            refined_count += 1

    return mappings, refined_count


# ==============================================================================
# Master Validator Function
# ==============================================================================

def apply_audit_fix_validators(mappings: List[Dict], verbose: bool = False) -> Dict:
    """
    Apply all audit fix validators to mapping results.

    Args:
        mappings: List of mapping dictionaries
        verbose: Print validation statistics

    Returns:
        Dict with updated mappings and statistics
    """
    stats = {
        'publication_subsections_refined': 0,
        'media_content_detected': 0,
        'mentee_grants_reclassified': 0,
        'fellowships_reclassified': 0
    }

    # Apply validators in sequence
    mappings, count = refine_publication_subsections(mappings)
    stats['publication_subsections_refined'] = count

    mappings, count = refine_media_mappings(mappings)
    stats['media_content_detected'] = count

    mappings, count = refine_mentee_grants(mappings)
    stats['mentee_grants_reclassified'] = count

    mappings, count = refine_fellowship_honors(mappings)
    stats['fellowships_reclassified'] = count

    if verbose:
        print()
        print("=" * 80)
        print("AUDIT FIX VALIDATORS APPLIED")
        print("=" * 80)
        print(f"  Publication subsections refined: {stats['publication_subsections_refined']}")
        print(f"  Media content detected: {stats['media_content_detected']}")
        print(f"  Mentee grants reclassified: {stats['mentee_grants_reclassified']}")
        print(f"  Fellowships reclassified: {stats['fellowships_reclassified']}")
        print("=" * 80)
        print()

    return {
        'mappings': mappings,
        'validation_stats': stats
    }
