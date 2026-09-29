"""
Reasoning-Code Consistency Checker

Addresses the bug where classification_reasoning suggests one code
but taxonomy_code contains a different code.

Example from feedback:
- Text: "CURRICULUM VITAE"
- taxonomy_code: "A"
- classification_reasoning: "T is appropriate as an appendix/other structural element"

The reasoning SAYS "T is appropriate" but the code is "A" - this is a bug
in the final assignment step that we can catch and auto-correct.

This validator parses the reasoning text for:
1. Explicit code mentions: "classify as S1", "should be M2", "T is appropriate"
2. Code conflicts: reasoning mentions code X but assigned code is Y
3. High-confidence reasoning: "clearly", "must be", "definitely"

When a conflict is detected with high confidence, auto-correct the code.
"""

import re
from dataclasses import dataclass


@dataclass
class ConsistencyResult:
    """Result of reasoning-code consistency check."""
    has_conflict: bool
    original_code: str
    reasoning_suggests: str | None
    confidence: float
    conflict_type: str  # 'explicit_mention', 'semantic_mismatch', 'none'
    evidence: str


# Patterns to extract explicit code mentions from reasoning
CODE_MENTION_PATTERNS = [
    # "T is appropriate", "S1 is correct", "M2 applies"
    r'\b([A-Z]\d?[A-Z]?)\s+is\s+(appropriate|correct|suitable|the\s+right|applicable)',
    # "classify as S1", "should be M2", "assign T"
    r'\b(classify|categorize|assign|code)\s+(as|to|with)?\s*([A-Z]\d?[A-Z]?)\b',
    # "belongs in S1", "fits M2", "goes under T"
    r'\b(belongs?|fits?|goes?|falls?)\s+(in|under|into|to)?\s*([A-Z]\d?[A-Z]?)\b',
    # "should be S1", "must be M2", "is T"
    r'\bshould\s+be\s+([A-Z]\d?[A-Z]?)\b',
    r'\bmust\s+be\s+([A-Z]\d?[A-Z]?)\b',
    # "→ S1", "-> M2" (arrow notation)
    r'(?:→|->)\s*([A-Z]\d?[A-Z]?)\b',
    # "this is S1", "this is an M2"
    r'\bthis\s+is\s+(an?\s+)?([A-Z]\d?[A-Z]?)\b',
    # "not S1", "not an M2" (exclusions - we want the opposite)
    # r'\bnot\s+(an?\s+)?([A-Z]\d?[A-Z]?)\b',  # Skip exclusions for now
]

# Valid taxonomy codes (to filter false positives)
VALID_CODES = {
    'A', 'B', 'B1', 'B2', 'C', 'C1', 'C2', 'C3',
    'D', 'D1', 'D2', 'D3',
    'E', 'E1', 'E2',
    'G',
    'H',
    'I',
    'J',
    'K', 'K1', 'K2', 'K3', 'K4', 'K5',
    'M', 'M1', 'M2', 'M2A', 'M2B', 'M2C', 'M2D',  # NOTE: M3/M4 removed - M2D=patents, clinical trials use M2A/M2B/M2C
    'N', 'N1', 'N2', 'N3', 'N3A', 'N3B',
    'O',
    'P',
    'Q', 'Q1', 'Q2', 'Q3', 'Q4', 'Q4A', 'Q4B', 'Q4C', 'Q4D',
    'R',
    'S', 'S0', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9', 'S10',
    'T',
}


def extract_codes_from_reasoning(reasoning: str) -> list[tuple[str, str]]:
    """
    Extract taxonomy codes mentioned in reasoning text.

    Returns list of (code, context) tuples.
    """
    if not reasoning:
        return []

    found_codes = []

    for pattern in CODE_MENTION_PATTERNS:
        matches = re.finditer(pattern, reasoning, re.IGNORECASE)
        for match in matches:
            # Extract the code from the match groups
            groups = match.groups()
            for g in groups:
                if g and g.upper() in VALID_CODES:
                    code = g.upper()
                    # Get surrounding context
                    start = max(0, match.start() - 20)
                    end = min(len(reasoning), match.end() + 20)
                    context = reasoning[start:end]
                    found_codes.append((code, context))
                    break

    return found_codes


def check_reasoning_consistency(entry: dict) -> ConsistencyResult:
    """
    Check if an entry's reasoning is consistent with its assigned code.

    Args:
        entry: Classified entry with 'taxonomy_code' and 'classification_reasoning'

    Returns:
        ConsistencyResult indicating if there's a conflict
    """
    current_code = entry.get('taxonomy_code', '')
    reasoning = entry.get('classification_reasoning', '')

    if not reasoning:
        return ConsistencyResult(
            has_conflict=False,
            original_code=current_code,
            reasoning_suggests=None,
            confidence=0.0,
            conflict_type='none',
            evidence='No reasoning provided'
        )

    # Extract codes mentioned in reasoning
    mentioned_codes = extract_codes_from_reasoning(reasoning)

    if not mentioned_codes:
        return ConsistencyResult(
            has_conflict=False,
            original_code=current_code,
            reasoning_suggests=None,
            confidence=0.0,
            conflict_type='none',
            evidence='No codes found in reasoning'
        )

    # Check for conflicts
    for code, context in mentioned_codes:
        # Skip if the mentioned code matches the assigned code
        if code == current_code:
            continue

        # Skip if the mentioned code is a parent of the assigned code
        # e.g., if assigned S1 and reasoning mentions S, that's fine
        if len(code) == 1 and current_code.startswith(code):
            continue

        # Skip if the assigned code is a parent of the mentioned code
        # e.g., if assigned S and reasoning mentions S1, that's a refinement
        if len(current_code) == 1 and code.startswith(current_code):
            # This might be a refinement suggestion - check if it's strong
            reasoning_lower = reasoning.lower()
            if any(kw in reasoning_lower for kw in ['should be', 'must be', 'is appropriate', 'classify as']):
                # Strong suggestion to use more specific code
                return ConsistencyResult(
                    has_conflict=True,
                    original_code=current_code,
                    reasoning_suggests=code,
                    confidence=0.75,
                    conflict_type='refinement_suggestion',
                    evidence=f"Reasoning suggests more specific code {code}: '{context}'"
                )
            continue

        # Check for strong conflict indicators
        reasoning_lower = reasoning.lower()
        strong_indicators = [
            f'{code.lower()} is appropriate',
            f'{code.lower()} is correct',
            f'classify as {code.lower()}',
            f'should be {code.lower()}',
            f'must be {code.lower()}',
            f'→ {code.lower()}',
            f'-> {code.lower()}',
        ]

        is_strong = any(ind in reasoning_lower for ind in strong_indicators)

        # We have a conflict
        confidence = 0.90 if is_strong else 0.70

        return ConsistencyResult(
            has_conflict=True,
            original_code=current_code,
            reasoning_suggests=code,
            confidence=confidence,
            conflict_type='explicit_mention',
            evidence=f"Reasoning mentions {code} but assigned {current_code}: '{context}'"
        )

    return ConsistencyResult(
        has_conflict=False,
        original_code=current_code,
        reasoning_suggests=None,
        confidence=1.0,
        conflict_type='none',
        evidence='Reasoning consistent with assigned code'
    )


def apply_reasoning_corrections(
    entries: list[dict],
    min_confidence: float = 0.80
) -> tuple[list[dict], dict]:
    """
    Apply reasoning-based corrections to classified entries.

    Only corrects when confidence is above threshold to avoid
    false positives from ambiguous reasoning.

    Args:
        entries: List of classified entry dicts
        min_confidence: Minimum confidence to apply correction (default 0.80)

    Returns:
        Tuple of (corrected_entries, stats)
    """
    corrections_made = 0
    conflicts_found = 0
    skipped_low_confidence = 0
    correction_details = []

    for entry in entries:
        result = check_reasoning_consistency(entry)

        if result.has_conflict:
            conflicts_found += 1

            if result.confidence >= min_confidence:
                # Apply correction
                old_code = entry['taxonomy_code']
                entry['taxonomy_code'] = result.reasoning_suggests
                entry['reasoning_correction'] = {
                    'original_code': old_code,
                    'suggested_code': result.reasoning_suggests,
                    'confidence': result.confidence,
                    'conflict_type': result.conflict_type,
                    'evidence': result.evidence
                }
                corrections_made += 1

                correction_details.append({
                    'entry_id': entry.get('entry_id'),
                    'text_preview': entry.get('text', '')[:60],
                    'original': old_code,
                    'corrected_to': result.reasoning_suggests,
                    'confidence': result.confidence,
                    'evidence': result.evidence
                })
            else:
                skipped_low_confidence += 1
                # Log but don't correct
                entry['reasoning_conflict_detected'] = {
                    'suggested_code': result.reasoning_suggests,
                    'confidence': result.confidence,
                    'evidence': result.evidence,
                    'action': 'skipped_low_confidence'
                }

    stats = {
        'entries_checked': len(entries),
        'conflicts_found': conflicts_found,
        'corrections_made': corrections_made,
        'skipped_low_confidence': skipped_low_confidence,
        'min_confidence_threshold': min_confidence,
        'correction_details': correction_details
    }

    return entries, stats


# For testing
if __name__ == "__main__":
    test_entries = [
        # Clear conflict: reasoning says T but code is A
        {
            "text": "CURRICULUM VITAE",
            "taxonomy_code": "A",
            "classification_reasoning": "This is a document-level header. T is appropriate as an appendix/other structural element."
        },
        # No conflict: reasoning matches code
        {
            "text": "Professor of Medicine",
            "taxonomy_code": "D1",
            "classification_reasoning": "Faculty appointment, D1 is appropriate for academic positions."
        },
        # Conflict: reasoning suggests more specific code
        {
            "text": "NIH R01 Grant, 2020-2025, $1.5M",
            "taxonomy_code": "M2",
            "classification_reasoning": "Active grant funding, should be classified as M2A for current funding."
        },
        # Conflict with arrow notation
        {
            "text": "Editorial Board, Journal of Medicine",
            "taxonomy_code": "H",
            "classification_reasoning": "Editorial service → Q4C (not an honor/award)"
        },
    ]

    corrected, stats = apply_reasoning_corrections(test_entries)

    print(f"Conflicts found: {stats['conflicts_found']}")
    print(f"Corrections made: {stats['corrections_made']}")
    print(f"Skipped (low confidence): {stats['skipped_low_confidence']}")
    print()

    for detail in stats['correction_details']:
        print(f"  {detail['original']} → {detail['corrected_to']} (conf: {detail['confidence']:.2f})")
        print(f"    Text: {detail['text_preview']}")
        print(f"    Evidence: {detail['evidence']}")
        print()
