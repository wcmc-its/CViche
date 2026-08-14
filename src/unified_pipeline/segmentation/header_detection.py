"""Pass 1a header detection for chunked Word segmentation.

Split out of word_chunked.py (#315 follow-up).
"""

from typing import Any, Dict, List

try:
    from ..core.docx_structure_extractor import extract_unified_elements
except (ImportError, ValueError):
    from core.docx_structure_extractor import extract_unified_elements

try:
    from .cv_headers import KNOWN_CV_HEADERS_SET, KNOWN_SUBSECTION_TERMS
except (ImportError, ValueError):
    from cv_headers import KNOWN_CV_HEADERS_SET, KNOWN_SUBSECTION_TERMS

# Composite-scoring confidence weights. Calibrated empirically against the
# corpus dry-run process in docs/DEV_WORKFLOW.md -- change a value only with
# a before/after corpus comparison, not just unit tests (PR #600 review).
TABLE_HEADER_DEFAULT_CONFIDENCE = 0.60
HEADING_STYLE_CONFIDENCE = 0.95
HEADING_STYLE_NAME_CONFIDENCE = 0.90
BASE_CONFIDENCE_SHORT_TEXT = 0.30
CENTERED_BOOST = 0.50
ALL_CAPS_BOOST = 0.45
BOLD_BEFORE_PLAIN_BOOST = 0.40
BOLD_FOLLOWED_BY_BOLD_BOOST = 0.25
BOLD_LAST_ELEMENT_BOOST = 0.30
ENDS_COLON_BOOST = 0.35
UNDERLINE_BOOST = 0.25
LARGE_FONT_BOOST = 0.30
NO_INDENT_BOOST = 0.15
WHITESPACE_PATTERN_BOOST = 0.10
KNOWN_HEADER_BOOST = 0.45
HEADER_CONFIDENCE_THRESHOLD = 0.60


def detect_section_headers(structure: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Pass 1: Detect section headers using Word document structure.

    Identifies headers based on VISUAL CUES (not just text):
    - Heading styles (Heading 1, Heading 2, etc.)
    - ALL CAPS paragraphs
    - Bold text followed by non-bold content
    - Centered alignment (major headers)
    - Font size differences (larger = headers)
    - Underline formatting
    - Indentation patterns
    - Ends with colon

    Returns list of headers with metadata:
    - text: Header text
    - idx: Paragraph index in document
    - level: Hierarchical level (1=main section, 2=subsection)
    - confidence: How certain this is a header (0.0-1.0)
    """

    headers = []
    elements = structure['elements']

    # Calculate average font size for context (helps detect "larger" headers)
    font_sizes = [e.get('font_size') for e in elements if e.get('type') == 'paragraph' and e.get('font_size')]
    avg_font_size = sum(font_sizes) / len(font_sizes) if font_sizes else 12.0

    for i, elem in enumerate(elements):
        # Process paragraphs AND table_header elements (from extract_unified_elements)
        if elem['type'] not in ('paragraph', 'table_header'):
            continue

        text = elem['text'].strip()
        if not text or len(text) < 2:
            continue

        # COMPOSITE SCORING: Start with base, add signals
        confidence = 0.0
        level = 1
        signals_detected = []

        # === TABLE HEADER SIGNAL (from extract_unified_elements) ===
        # Table headers already passed header detection, so give them a boost
        if elem['type'] == 'table_header':
            confidence = elem.get('header_confidence', TABLE_HEADER_DEFAULT_CONFIDENCE)
            signals_detected.append('table_header')
            # Continue to apply other signals to potentially boost further

        # === STRONGEST SIGNALS (decisive) ===

        # 1. Heading style (STRONGEST - Word's native structure)
        if elem.get('outline_level') is not None:
            confidence = HEADING_STYLE_CONFIDENCE
            level = elem['outline_level']
            signals_detected.append('heading_style')

        # 2. Style name contains "Heading"
        elif 'Heading' in elem.get('style', ''):
            confidence = HEADING_STYLE_NAME_CONFIDENCE
            try:
                level = int(elem['style'].split()[-1])
            except (ValueError, IndexError):
                level = 1
            signals_detected.append('heading_style_name')

        # === STRONG VISUAL SIGNALS (additive if no heading style) ===
        else:
            # Start with base confidence for short text
            # BUT don't reset if we already have confidence from table_header
            if len(text) < 150 and confidence == 0.0:
                confidence = BASE_CONFIDENCE_SHORT_TEXT

            # 3. Centered alignment (major headers like "CURRICULUM VITAE")
            if elem.get('alignment') == 'center' and len(text) < 100:
                confidence += CENTERED_BOOST
                level = 1
                signals_detected.append('centered')

            # 4. ALL CAPS (common for section headers)
            if text.isupper() and len(text) < 150:
                confidence += ALL_CAPS_BOOST
                signals_detected.append('all_caps')

            # 5. Bold text
            if elem.get('bold') and len(text) < 150 and elem.get('list_level') is None:
                # Check if next element is not bold (header followed by content)
                if i + 1 < len(elements):
                    next_elem = elements[i + 1]
                    if next_elem.get('type') == 'paragraph' and not next_elem.get('bold'):
                        confidence += BOLD_BEFORE_PLAIN_BOOST
                        # Determine level: Major sections (longer, descriptive) are L1
                        # Short subsection headers (single words) are L2
                        if text.endswith(':'):
                            # Remove colon and whitespace for length check
                            text_core = text.rstrip(':').strip()
                            # Major sections: "Academic Appointments", "Postdoctoral Training"
                            # Subsections: "Local", "National", "International"
                            if len(text_core) > 15 or ' ' in text_core:
                                level = 1  # Major section (multi-word or long)
                            else:
                                level = 2  # Subsection (single short word)
                        else:
                            level = 2  # Default subsection
                        signals_detected.append('bold_before_plain')
                    else:
                        confidence += BOLD_FOLLOWED_BY_BOLD_BOOST
                        signals_detected.append('bold')
                else:
                    confidence += BOLD_LAST_ELEMENT_BOOST
                    signals_detected.append('bold')

            # 6. Ends with colon (common CV pattern: "Education:")
            if text.endswith(':') and len(text) < 100:
                confidence += ENDS_COLON_BOOST
                signals_detected.append('ends_colon')

            # 7. Underline formatting
            if elem.get('underline') and len(text) < 150:
                confidence += UNDERLINE_BOOST
                signals_detected.append('underline')

            # 8. Larger font size (headers often bigger)
            if elem.get('font_size') and elem.get('font_size') > avg_font_size * 1.2:
                confidence += LARGE_FONT_BOOST
                signals_detected.append('large_font')

            # 9. Zero indentation + short (left-aligned headers)
            if elem.get('indent_left') == 0.0 and len(text) < 80 and elem.get('list_level') is None:
                confidence += NO_INDENT_BOOST
                signals_detected.append('no_indent')

            # 10. Unusual spacing/whitespace patterns (lots of spaces = formatted header)
            if '   ' in text or '\t' in text:  # Multiple spaces or tabs
                confidence += WHITESPACE_PATTERN_BOOST
                signals_detected.append('whitespace')

            # 11. Known CV section header (V6: WCM taxonomy-based)
            text_lower = text.lower().rstrip(':').strip()
            if text_lower in KNOWN_CV_HEADERS_SET:
                confidence += KNOWN_HEADER_BOOST  # Strong boost for recognized section titles
                signals_detected.append('known_header')

        # Cap confidence at 1.0
        confidence = min(confidence, 1.0)

        # Only include if confidence above threshold
        if confidence >= HEADER_CONFIDENCE_THRESHOLD:
            headers.append({
                'text': text,
                # Use unified_idx if available (from extract_unified_elements), else fall back to idx
                'idx': elem.get('unified_idx', elem.get('idx')),
                'level': level,
                'confidence': round(confidence, 3),
                'style': elem.get('style'),
                'bold': elem.get('bold', False),
                'signals': signals_detected,  # For debugging
                'source_type': elem['type'],  # Track if from paragraph or table_header
                'table_index': elem.get('table_index'),  # Track table index if from table
            })

    # Post-process: Enforce consistent hierarchy for known subsection patterns
    headers = _enforce_hierarchy_consistency(headers)

    return headers


def _enforce_hierarchy_consistency(headers: List[Dict]) -> List[Dict]:
    """
    Post-process headers to enforce consistent hierarchy levels.

    Mutates and returns the same `headers` list/dicts passed in (in place) --
    it does not copy. Callers that need the pre-normalization levels should
    snapshot them before calling `detect_section_headers`/this function.

    Fixes common issues:
    1. Geographic terms (International/National/Regional/Local) orphaned as H1
       should be demoted to H2 or H3 based on context
    2. Repeated subsection patterns should maintain consistent levels

    Example fix:
        Before:
            [H2] Scientific presentations
              [H3] International
              [H2] National      ← inconsistent
            [H1] Regional        ← orphaned
        After:
            [H2] Scientific presentations
              [H3] International
              [H3] National      ← fixed
              [H3] Regional      ← re-parented
    """
    if not headers:
        return headers

    # Build lookup of text to expected level based on first occurrence
    # This ensures consistency: if "National" first appears as L3, subsequent ones are also L3
    first_occurrence_level = {}

    for i, header in enumerate(headers):
        text_lower = header['text'].lower().rstrip(':').strip()

        # Check if this is a known subsection term that shouldn't be H1
        if text_lower in KNOWN_SUBSECTION_TERMS and header['level'] == 1:
            # This branch only runs when header['level'] == 1, so a scan for
            # "the nearest preceding header strictly shallower than this
            # header's own level" can never succeed (no header level is < 1)
            # -- that comparison is a contradiction, not just an edge case,
            # since the level we'd compare against is the very thing this
            # block is about to overwrite. #400's own fix sketch names the
            # alternative directly: fall back to the most recent preceding
            # L1 header, and treat the new header as that section's sibling
            # subsection (one level below it).
            parent_level = None
            for j in range(i - 1, -1, -1):
                if headers[j]['level'] == 1:
                    parent_level = headers[j]['level']
                    break

            # Demote to one level below parent (or L2 if no parent found)
            if parent_level is not None:
                header['level'] = min(parent_level + 1, 3)
            else:
                header['level'] = 2

            if 'signals' in header and 'demoted_subsection' not in header['signals']:
                header['signals'].append('demoted_subsection')

        # Track first occurrence for consistency
        if text_lower not in first_occurrence_level:
            first_occurrence_level[text_lower] = header['level']
        else:
            # Enforce consistency with first occurrence
            expected_level = first_occurrence_level[text_lower]
            if header['level'] != expected_level:
                # Only adjust if current level is HIGHER (more top-level) than expected
                # This prevents demoting intentionally different structures
                if header['level'] < expected_level:
                    header['level'] = expected_level
                    if 'signals' in header and 'level_normalized' not in header['signals']:
                        header['signals'].append('level_normalized')

    return headers
