"""
Scoring-based header detection methods for CV extraction.
These methods implement a sliding-scale scoring approach instead of hard thresholds.
"""

def _score_header_candidate(self, line, taxonomy_match, spacing, patterns):
    """
    Calculate likelihood score for a line being a header.

    Philosophy: "Beggars can't be choosers" - score everything and take the best available.

    Args:
        line: Line dictionary with text and properties
        taxonomy_match: Taxonomy match result (or None)
        spacing: Vertical spacing before this line
        patterns: Document-wide patterns

    Returns:
        tuple: (score, signals, taxonomy_match)
    """
    score = 0
    signals = []
    text = line['text']

    # === FORMATTING SIGNALS (Strong indicators) ===

    if line.get('is_bold', False):
        score += 20
        signals.append('bold:20')

    is_caps = text.isupper() and 5 <= len(text) <= 100
    if is_caps:
        score += 15
        signals.append('all_caps:15')

    is_large_font = line.get('avg_size', 0) >= patterns['header_threshold']
    if is_large_font:
        score += 25
        signals.append('large_font:25')

    if line.get('has_underline_below', False):
        score += 30
        signals.append('underlined:30')

    if line.get('has_horizontal_rule', False):
        score += 20
        signals.append('h_rules:20')

    # === LAYOUT SIGNALS ===

    at_base_indent = line.get('indent_level', 999) <= patterns['base_indent']
    if at_base_indent:
        score += 10
        signals.append('base_indent:10')

    if line.get('is_centered', False):
        score += 15
        signals.append('centered:15')

    if spacing > 15:
        score += 10
        signals.append('large_spacing:10')

    # === CONTENT SIGNALS ===

    # Try exact match if not already matched
    if not taxonomy_match:
        taxonomy_match = self._match_taxonomy_exact(text)

    # Also try fuzzy match for corrupted text
    if not taxonomy_match:
        taxonomy_match = self._match_taxonomy_fuzzy(text)

    if taxonomy_match:
        spec = taxonomy_match['specificity_rank']
        if spec >= 70:
            score += 30
            signals.append(f'taxonomy_high:{30}')
        elif spec >= 60:
            score += 20
            signals.append(f'taxonomy_med:{20}')
        elif spec >= 50:
            score += 10
            signals.append(f'taxonomy_low:{10}')

    if text.endswith(':'):
        score += 10
        signals.append('colon:10')

    if 5 <= len(text) <= 50:
        score += 5
        signals.append('good_length:5')

    # === COMBINATION BONUSES ===

    is_bold = line.get('is_bold', False)

    if is_bold and taxonomy_match and taxonomy_match['specificity_rank'] >= 60:
        score += 15
        signals.append('BONUS:bold+taxonomy:15')

    if is_caps and taxonomy_match and taxonomy_match['specificity_rank'] >= 60:
        score += 15
        signals.append('BONUS:caps+taxonomy:15')

    if is_caps and at_base_indent:
        score += 10
        signals.append('BONUS:caps+base:10')

    if is_large_font and taxonomy_match and taxonomy_match['specificity_rank'] >= 70:
        score += 20
        signals.append('BONUS:large+taxonomy:20')

    # === PENALTIES ===

    import re

    if re.match(r'^\d{4}[-/]', text):
        score -= 30
        signals.append('PENALTY:date:-30')

    if re.search(r'PMID|PMC\d+|DOI:', text):
        score -= 40
        signals.append('PENALTY:citation:-40')

    if len(text) > 80:
        score -= 20
        signals.append('PENALTY:too_long:-20')

    if len(text) < 5:
        score -= 15
        signals.append('PENALTY:too_short:-15')

    if re.match(r'^(Page|PAGE)\s+\d+', text):
        score -= 50
        signals.append('PENALTY:page_num:-50')

    return score, signals, taxonomy_match


def _phase1_font_detection_scored(self, lines, patterns):
    """
    PHASE 1: Score-based detection - rank all candidates and take best available.

    This replaces hard thresholds with a sliding-scale approach:
    - Tier 1 (≥100 pts): Definite headers - always accept
    - Tier 2 (70-99 pts): Very likely headers - accept
    - Tier 3 (50-69 pts): Likely headers - accept if <8 sections from higher tiers
    - Tier 4 (30-49 pts): Possible headers - accept ONLY if <3 sections total
    """
    import re

    candidates = []
    prev_y = None

    # Strict exclusion patterns (false positives)
    exclusion_patterns = [
        r'^Funding:\s*\$',
        r'^Total:\s*\$',
        r'^\d{4}[-/]\d{2}',  # Dates at start
        r'^Page\s+\d+',
        r'PMID:\s*\d+',
        r'^\d+\.\s*PMID',
        r'^PMID\s+\d+',
        r'^\d{4}\s+PMID:',
        r'^PMC\d+',
        r'PMCID:\s*PMC',
        r'^\d+\.\s*PMC\d+',
        r'^DOI:',
        r'^ISBN:',
        r'^\d{1,4};',
        r'^\d+:\d+-\d+',
        r'^[A-Z]{2,5}\s+[A-Z0-9-]+\s+\d{4}-\d{4}$',
    ]

    # Add document-specific numbering patterns
    for scheme in patterns['numbering_patterns']:
        if scheme == 'numeric':
            exclusion_patterns.append(r'^\d+\.\s+\w')
        elif scheme == 'hierarchical':
            exclusion_patterns.append(r'^\d+\.\d+')

    for line in lines:
        text = line['text']
        if not text or len(text) < 3:
            prev_y = line.get('y_position')
            continue

        # Skip underline lines
        if line.get('is_underlined', False):
            prev_y = line.get('y_position')
            continue

        # Check exclusions - skip if matches
        if any(re.match(pat, text) for pat in exclusion_patterns):
            prev_y = line.get('y_position')
            continue

        # Calculate spacing
        spacing = 0
        if prev_y is not None:
            spacing = line.get('y_position', 0) - prev_y

        # Score this candidate
        score, signals, taxonomy = self._score_header_candidate(
            line, None, spacing, patterns
        )

        # Minimum threshold to even be considered
        if score >= 30:
            candidates.append({
                'line': line,
                'score': score,
                'signals': signals,
                'taxonomy': taxonomy
            })

        prev_y = line.get('y_position')

    # Sort by score descending
    candidates.sort(key=lambda x: x['score'], reverse=True)

    # Tier-based selection
    tier1 = [c for c in candidates if c['score'] >= 100]  # Definite
    tier2 = [c for c in candidates if 70 <= c['score'] < 100]  # Very likely
    tier3 = [c for c in candidates if 50 <= c['score'] < 70]   # Likely
    tier4 = [c for c in candidates if 30 <= c['score'] < 50]   # Possible

    # Build final selection
    final_sections = []

    # Always take Tier 1 + Tier 2
    final_sections.extend(tier1)
    final_sections.extend(tier2)

    # Add Tier 3 if we have <8 sections so far
    if len(final_sections) < 8:
        final_sections.extend(tier3)

    # Add Tier 4 ONLY if we have <3 sections total
    if len(final_sections) < 3:
        final_sections.extend(tier4)

    # Convert to section format
    sections = []
    for candidate in final_sections:
        line = candidate['line']
        section = {
            'line_number': line['line_number'],
            'header': line['text'],
            'header_upper': line['text'].upper(),
            'font_size': line.get('avg_size', 0),
            'is_bold': line.get('is_bold', False),
            'is_all_caps': line['text'].isupper(),
            'is_centered': line.get('is_centered', False),
            'has_underline': line.get('has_underline_below', False),
            'indent_level': line.get('indent_level', 0),
            'confidence_signals': candidate['signals'],
            'confidence_score': candidate['score'],
            'confidence': candidate['score'],  # For compatibility with phase 2
            'detection_phase': 1
        }

        # Add taxonomy if matched
        if candidate['taxonomy']:
            section['taxonomy_id'] = candidate['taxonomy']['id']
            section['taxonomy_canonical'] = candidate['taxonomy']['canonical']
            section['taxonomy_specificity'] = candidate['taxonomy']['specificity_rank']
            section['taxonomy_parent'] = candidate['taxonomy'].get('parent')

        sections.append(section)

    return sections
