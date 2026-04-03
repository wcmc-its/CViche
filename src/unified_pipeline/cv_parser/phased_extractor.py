"""
Phased CV section extraction:
Phase 1: Detect headers using font formatting (high confidence)
Phase 2: Map to taxonomy (standardize names)
Phase 3: Fill gaps if needed (low coverage)
"""
import pdfplumber
import re
from typing import Dict, List, Tuple, Set, Optional
from pathlib import Path
import logging
from collections import Counter

from unified_pipeline.llm_client import call_llm

# Import taxonomy
try:
    from cv_taxonomy import CV_SECTIONS, build_taxonomy_index
except ImportError:
    from cv_parser.cv_taxonomy import CV_SECTIONS, build_taxonomy_index

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class PhasedCVExtractor:
    """
    Three-phase extraction strategy:
    1. Font-based detection (high precision)
    2. Taxonomy mapping (standardization)
    3. Gap filling (if coverage is low)
    """

    def __init__(self, pdf_path: str, min_section_threshold: int = 8, use_scoring: bool = False, use_style_clustering: bool = False, show_grouping: bool = False, use_llm_detection: bool = False):
        """
        Initialize phased extractor.

        Args:
            pdf_path: Path to PDF file
            min_section_threshold: Minimum sections before gap filling (default: 8)
            use_scoring: Use scoring-based detection instead of hard thresholds (default: False)
            use_style_clustering: Use style-based clustering for within-document consistency (default: False)
            show_grouping: Show diagnostic output for style grouping process (default: False)
            use_llm_detection: Use LLM (GPT-4o-mini) for section header evaluation (default: False)
        """
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        self.min_section_threshold = min_section_threshold
        self.use_scoring = use_scoring
        self.use_style_clustering = use_style_clustering
        self.show_grouping = show_grouping
        self.use_llm_detection = use_llm_detection
        self.taxonomy = build_taxonomy_index()

    def extract_with_phases(self) -> Dict:
        """
        Extract CV sections using phased approach.

        Returns:
            Dictionary with sections and metadata
        """
        logger.info(f"Phased extraction from {self.pdf_path.name}")

        with pdfplumber.open(self.pdf_path) as pdf:
            all_lines = []
            line_number = 0
            page_widths = []
            all_horizontal_lines = []

            for page_num, page in enumerate(pdf.pages):
                chars = page.chars
                if not chars:
                    continue

                page_widths.append(page.width)

                # Extract horizontal lines/rectangles from the page
                page_h_lines = self._extract_horizontal_lines(page, page_num)
                all_horizontal_lines.extend(page_h_lines)

                lines_on_page = self._group_chars_into_lines(chars, page.width)

                for line_data in lines_on_page:
                    all_lines.append({
                        'line_number': line_number,
                        'page': page_num + 1,
                        'text': line_data['text'],
                        'avg_size': line_data['avg_size'],
                        'max_size': line_data['max_size'],
                        'is_bold': line_data['is_bold'],
                        'is_italic': line_data['is_italic'],
                        'is_all_caps': line_data['text'].isupper() if line_data['text'] else False,
                        'is_underlined': line_data['is_underlined'],
                        'is_centered': line_data['is_centered'],
                        'y_position': line_data['y_position'],
                        'x_position': line_data['x_position'],
                        'indent_level': line_data['indent_level'],
                        'page_width': page.width
                    })
                    line_number += 1

        avg_page_width = sum(page_widths) / len(page_widths) if page_widths else 612

        # Detect underlines
        self._detect_underlines(all_lines)

        # Detect horizontal rules near text
        self._detect_horizontal_rules(all_lines, all_horizontal_lines, avg_page_width)

        # Analyze document patterns
        doc_patterns = self._analyze_document_patterns(all_lines, avg_page_width)

        # PHASE 1: Font-based detection (high confidence)
        logger.info("Phase 1: Font-based section detection")
        if self.use_llm_detection:
            logger.info("Using LLM-based detection (GPT-4o-mini)")
            phase1_sections = self._phase1_llm_detection(all_lines, doc_patterns)
        elif self.use_style_clustering:
            logger.info("Using style-clustering approach")
            phase1_sections = self._phase1_style_clustering(all_lines, doc_patterns)
        elif self.use_scoring:
            logger.info("Using scoring-based detection")
            phase1_sections = self._phase1_font_detection_scored(all_lines, doc_patterns)
        else:
            phase1_sections = self._phase1_font_detection(all_lines, doc_patterns)
        logger.info(f"Phase 1 detected: {len(phase1_sections)} sections")

        # PHASE 2: Taxonomy mapping
        logger.info("Phase 2: Taxonomy mapping")
        phase2_sections = self._phase2_taxonomy_mapping(phase1_sections)

        # Count major sections (high confidence)
        major_sections = [s for s in phase2_sections if s['confidence'] >= 4]
        logger.info(f"Phase 2 mapped: {len(phase2_sections)} sections ({len(major_sections)} high confidence)")

        # PHASE 3: Gap filling (if needed)
        final_sections = phase2_sections
        if len(major_sections) < self.min_section_threshold:
            logger.info(f"Phase 3: Gap filling (only {len(major_sections)} major sections found)")
            phase3_additions = self._phase3_gap_filling(all_lines, phase2_sections, doc_patterns)
            logger.info(f"Phase 3 added: {len(phase3_additions)} additional sections")
            final_sections = phase2_sections + phase3_additions
        else:
            logger.info(f"Phase 3: Skipped (sufficient coverage with {len(major_sections)} sections)")

        # Build full text
        raw_text = '\n'.join([line['text'] for line in all_lines if line['text']])

        logger.info(f"Total sections detected: {len(final_sections)}")

        return {
            'raw_text': raw_text,
            'lines': all_lines,
            'doc_patterns': doc_patterns,
            'sections': final_sections,
            'page_count': len(pdf.pages),
            'phase_stats': {
                'phase1_sections': len(phase1_sections),
                'phase2_sections': len(phase2_sections),
                'phase3_added': len(final_sections) - len(phase2_sections),
                'major_sections': len(major_sections)
            }
        }

    def _group_chars_into_lines(self, chars: List[Dict], page_width: float) -> List[Dict]:
        """Group characters into lines based on y-coordinate."""
        if not chars:
            return []

        sorted_chars = sorted(chars, key=lambda c: (c['top'], c['x0']))

        lines = []
        current_line = []
        current_y = None
        y_tolerance = 2

        for char in sorted_chars:
            if current_y is None or abs(char['top'] - current_y) <= y_tolerance:
                current_line.append(char)
                current_y = char['top']
            else:
                if current_line:
                    lines.append(self._analyze_line(current_line, page_width))
                current_line = [char]
                current_y = char['top']

        if current_line:
            lines.append(self._analyze_line(current_line, page_width))

        return lines

    def _analyze_line(self, chars: List[Dict], page_width: float) -> Dict:
        """Analyze a line of characters."""
        if not chars:
            return {
                'text': '', 'avg_size': 0, 'max_size': 0,
                'is_bold': False, 'is_italic': False, 'is_underlined': False,
                'is_centered': False, 'y_position': 0, 'x_position': 0, 'indent_level': 0
            }

        chars = sorted(chars, key=lambda c: c['x0'])

        text = ''.join([c['text'] for c in chars])
        sizes = [c.get('size', 10) for c in chars]
        avg_size = sum(sizes) / len(sizes) if sizes else 10
        max_size = max(sizes) if sizes else 10

        # Check font styles
        bold_indicators = ['Bold', 'Heavy', 'Black', 'Semibold']
        italic_indicators = ['Italic', 'Oblique']
        font_names = [c.get('fontname', '') for c in chars]

        is_bold = any(any(ind in fn for ind in bold_indicators) for fn in font_names if fn)
        is_italic = any(any(ind in fn for ind in italic_indicators) for fn in font_names if fn)

        # Check for underlined text
        is_underlined = self._is_underline_chars(text)

        # Calculate position metrics
        x_position = chars[0]['x0']
        y_position = chars[0]['top']
        line_width = chars[-1]['x0'] + chars[-1]['width'] - chars[0]['x0']

        # Detect centered text
        center_x = page_width / 2
        line_center = x_position + (line_width / 2)
        tolerance = page_width * 0.2
        is_centered = abs(line_center - center_x) < tolerance and x_position > page_width * 0.2

        # Indent level
        indent_level = int(x_position / 20)

        return {
            'text': text.strip(),
            'avg_size': avg_size,
            'max_size': max_size,
            'is_bold': is_bold,
            'is_italic': is_italic,
            'is_underlined': is_underlined,
            'is_centered': is_centered,
            'y_position': y_position,
            'x_position': x_position,
            'indent_level': indent_level
        }

    def _is_underline_chars(self, text: str) -> bool:
        """Check if line consists mostly of underline/dash characters."""
        if len(text) < 3:
            return False
        underline_chars = '_-–—=~'
        underline_count = sum(1 for c in text if c in underline_chars)
        return underline_count / len(text) > 0.7

    def _extract_horizontal_lines(self, page, page_num: int) -> List[Dict]:
        """
        Extract horizontal lines/rectangles from a PDF page.
        These are graphical elements (like borders), not text underlines.
        """
        horizontal_lines = []

        # Extract lines (thin graphical elements)
        if hasattr(page, 'lines') and page.lines:
            for line in page.lines:
                # Check if line is horizontal (y0 ≈ y1)
                if abs(line.get('y0', 0) - line.get('y1', 0)) < 2:
                    horizontal_lines.append({
                        'page': page_num + 1,
                        'y': line.get('y0', 0),
                        'x0': line.get('x0', 0),
                        'x1': line.get('x1', 0),
                        'width': abs(line.get('x1', 0) - line.get('x0', 0)),
                        'type': 'line'
                    })

        # Extract rectangles (filled or bordered boxes)
        if hasattr(page, 'rects') and page.rects:
            for rect in page.rects:
                # Check if rectangle is very thin (essentially a horizontal line)
                height = abs(rect.get('y1', 0) - rect.get('y0', 0))
                if height < 5:  # Less than 5 points tall = horizontal rule
                    horizontal_lines.append({
                        'page': page_num + 1,
                        'y': rect.get('y0', 0),
                        'x0': rect.get('x0', 0),
                        'x1': rect.get('x1', 0),
                        'width': abs(rect.get('x1', 0) - rect.get('x0', 0)),
                        'type': 'rect'
                    })

        return horizontal_lines

    def _detect_horizontal_rules(self, lines: List[Dict], h_lines: List[Dict],
                                  avg_page_width: float) -> None:
        """
        Detect text lines that have horizontal rules above/below them.
        This is a strong signal for section headers.
        """
        if not h_lines:
            return

        # Filter for significant horizontal lines (>40% of page width)
        min_width = avg_page_width * 0.4
        significant_lines = [hl for hl in h_lines if hl['width'] >= min_width]

        if not significant_lines:
            return

        # For each text line, check if there are horizontal rules nearby
        for line in lines:
            if not line['text'] or len(line['text']) < 3:
                continue

            # Get horizontal lines on the same page
            page_h_lines = [hl for hl in significant_lines if hl['page'] == line['page']]

            if not page_h_lines:
                continue

            text_y = line['y_position']
            has_rule_above = False
            has_rule_below = False

            # Check for rules within 10 points above/below
            tolerance = 10

            for h_line in page_h_lines:
                h_y = h_line['y']

                # Rule above the text (h_y < text_y)
                if 0 < (text_y - h_y) < tolerance:
                    has_rule_above = True

                # Rule below the text (h_y > text_y)
                # Estimate text height as ~font_size
                text_height = line['avg_size'] * 1.2
                if 0 < (h_y - text_y) < (text_height + tolerance):
                    has_rule_below = True

            line['has_horizontal_rule'] = has_rule_above or has_rule_below
            line['has_rule_above'] = has_rule_above
            line['has_rule_below'] = has_rule_below

    def _detect_underlines(self, lines: List[Dict]) -> None:
        """Detect lines that have underlines/HRs immediately below them."""
        for i in range(len(lines) - 1):
            current = lines[i]
            next_line = lines[i + 1]

            if next_line.get('is_underlined') and not current.get('is_underlined'):
                current['has_underline_below'] = True
            else:
                current['has_underline_below'] = False

        if lines:
            lines[-1]['has_underline_below'] = False

    def _analyze_document_patterns(self, lines: List[Dict], avg_page_width: float) -> Dict:
        """Learn patterns from the document."""
        text_lines = [l for l in lines if l['text'] and len(l['text']) > 3 and not l.get('is_underlined', False)]

        if not text_lines:
            return self._default_patterns()

        # Font size analysis
        sizes = [l['avg_size'] for l in text_lines]
        size_counts = Counter(round(s, 1) for s in sizes)

        body_size = size_counts.most_common(1)[0][0]

        sorted_sizes = sorted(sizes)
        p75_size = sorted_sizes[int(len(sorted_sizes) * 0.75)]
        p90_size = sorted_sizes[int(len(sorted_sizes) * 0.90)]

        size_variance = len(size_counts)
        if size_variance < 5:
            header_threshold = body_size * 1.1
        else:
            header_threshold = p75_size

        # Detect numbering schemes
        numbering_patterns = self._detect_numbering_schemes(text_lines)

        # Indentation analysis
        indents = [l['indent_level'] for l in text_lines]
        base_indent = Counter(indents).most_common(1)[0][0]

        return {
            'body_size': body_size,
            'header_threshold': header_threshold,
            'p75_size': p75_size,
            'p90_size': p90_size,
            'size_distribution': dict(size_counts.most_common(10)),
            'numbering_patterns': numbering_patterns,
            'base_indent': base_indent,
            'size_variance': size_variance,
            'avg_page_width': avg_page_width
        }

    def _detect_numbering_schemes(self, lines: List[Dict]) -> List[str]:
        """Detect numbering patterns used in document."""
        patterns = []
        scheme_patterns = [
            (r'^\d+\.', 'numeric'),
            (r'^\d+\)', 'numeric_paren'),
            (r'^[a-z]\.', 'alpha_lower'),
            (r'^[A-Z]\.', 'alpha_upper'),
            (r'^\d+\.\d+', 'hierarchical'),
        ]

        for pattern, name in scheme_patterns:
            matches = sum(1 for l in lines[:100] if re.match(pattern, l['text']))
            if matches >= 3:
                patterns.append(name)

        return patterns

    def _default_patterns(self) -> Dict:
        """Default patterns if document analysis fails."""
        return {
            'body_size': 12.0,
            'header_threshold': 13.8,
            'p75_size': 12.0,
            'p90_size': 12.0,
            'size_distribution': {},
            'numbering_patterns': [],
            'base_indent': 0,
            'size_variance': 0,
            'avg_page_width': 612
        }

    def _phase1_font_detection(self, lines: List[Dict], patterns: Dict) -> List[Dict]:
        """
        PHASE 1: Detect sections using ONLY font formatting.
        High precision, no taxonomy patterns.
        """
        sections = []
        prev_y = None

        # Strict exclusion patterns (false positives)
        exclusion_patterns = [
            r'^Funding:\s*\$',
            r'^Total:\s*\$',
            r'^\d{4}[-/]\d{2}',  # Dates
            r'^Page\s+\d+',
            r'PMID:\s*\d+',
            r'^\d+\.\s*PMID',
            r'^PMID\s+\d+',
            r'^\d{4}\s+PMID:',
            r'^\d+\s+\d{4}\.\s+PMID:',
            r'^:\d+-\d+\.\s+PMID:',
            r'^[A-Z]?\d+:\d+-\d+\s+\d{4}\.\s+PMID:',
            r'^\d+\([A-Z0-9]+\):',
            r'^\d{4}\s+\d+\([A-Z0-9]+\):\d+-\d+\.',
            r'^PMC\d+',
            r'PMCID:\s*PMC',
            r'^\d+\.\s*PMC\d+',  # Numbered PMC IDs like "86. PMC5218926."
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
                prev_y = line['y_position']
                continue

            # Skip underline lines
            if line.get('is_underlined', False):
                prev_y = line['y_position']
                continue

            # Check exclusions
            if any(re.match(pat, text) for pat in exclusion_patterns):
                prev_y = line['y_position']
                continue

            # Calculate spacing
            spacing = 0
            if prev_y is not None:
                spacing = line['y_position'] - prev_y

            is_header = False
            confidence_signals = []

            # SIGNAL 1: Bold + ALL CAPS + reasonable length + at or left of base indent
            # (Headers are often left-aligned while body text is indented)
            if (line['is_bold'] and line['is_all_caps'] and
                5 <= len(text) <= 80 and
                line['indent_level'] <= patterns['base_indent']):
                is_header = True
                confidence_signals.append('bold_caps_base')

            # SIGNAL 2: Large font (significantly larger than body)
            if line['max_size'] >= patterns['header_threshold']:
                confidence_signals.append('large_font')

            # SIGNAL 3: Underlined (strong signal)
            if line.get('has_underline_below', False):
                is_header = True
                confidence_signals.append('underlined')

            # SIGNAL 4: Large spacing + bold
            if spacing > 15 and line['is_bold']:
                confidence_signals.append('spacing_bold')

            # SIGNAL 5: ALL CAPS + large font + base indent
            if (line['is_all_caps'] and
                line['max_size'] >= patterns['body_size'] and
                5 <= len(text) <= 100 and
                line['indent_level'] == patterns['base_indent']):
                confidence_signals.append('caps_font_base')

            # SIGNAL 6: Special formatting (e.g., "SECTION (details)")
            if re.match(r'^[A-Z\s&,/]+\s*\(.*\)$', text):
                is_header = True
                confidence_signals.append('formatted_header')

            # SIGNAL 7: Taxonomy EXACT match + large font (for simple CVs like Huangfu)
            taxonomy_match = self._match_taxonomy_exact(text)
            if (taxonomy_match and
                taxonomy_match['specificity_rank'] >= 50 and
                'large_font' in confidence_signals and
                len(text) <= 50):
                is_header = True
                confidence_signals.append('taxonomy_exact_large_font')

            # SIGNAL 9: ALL CAPS + taxonomy fuzzy match (for CVs with corrupted text extraction)
            # Example: "DUCATION" should match "education", "OSITIONS" matches "positions"
            fuzzy_match = self._match_taxonomy_fuzzy(text)
            if (line['is_all_caps'] and
                5 <= len(text) <= 80 and
                fuzzy_match and
                fuzzy_match['specificity_rank'] >= 50):  # Lower threshold for fuzzy matches
                is_header = True
                confidence_signals.append('caps_taxonomy_fuzzy')
                # Store the fuzzy match for later use
                if not taxonomy_match:
                    taxonomy_match = fuzzy_match

            # SIGNAL 10: Bold + high-specificity taxonomy match (for title-case headers)
            # Example: "Teaching", "Publications", "Honors and Awards" (not all caps, but clearly headers)
            if (line['is_bold'] and
                5 <= len(text) <= 50 and
                taxonomy_match and
                taxonomy_match['specificity_rank'] >= 50 and
                line['indent_level'] <= patterns['base_indent']):
                is_header = True
                confidence_signals.append('bold_taxonomy')

            # SIGNAL 11: Plain-text taxonomy match with colon (for unformatted CVs like Patton)
            # Example: "Education:", "Faculty Appointments:", "Postgraduate Training:"
            # These CVs have no formatting, just taxonomy-matched text + colon at base indent
            if (not taxonomy_match):
                taxonomy_match = self._match_taxonomy_exact(text)

            if (taxonomy_match and
                taxonomy_match['specificity_rank'] >= 60 and
                text.endswith(':') and
                5 <= len(text) <= 60 and
                line['indent_level'] <= patterns['base_indent'] and
                not line['is_bold'] and  # Only for plain text CVs
                patterns['size_variance'] < 3):  # CV has minimal formatting
                is_header = True
                confidence_signals.append('plain_taxonomy_colon')

            # SIGNAL 8: Horizontal rules (strong signal - lines above/below text)
            # But NOT strong enough alone - must combine with other signals
            if line.get('has_horizontal_rule', False):
                confidence_signals.append('horizontal_rules')

            # Require strong evidence: specific strong signals OR 3+ weak signals
            if is_header:
                if 'bold_caps_base' in confidence_signals:
                    pass  # This alone is strong enough
                elif 'underlined' in confidence_signals:
                    pass  # This alone is strong enough
                elif 'caps_taxonomy_fuzzy' in confidence_signals:
                    pass  # ALL CAPS + fuzzy taxonomy match is strong enough
                elif 'bold_taxonomy' in confidence_signals:
                    pass  # Bold + high-specificity taxonomy match is strong enough
                elif 'plain_taxonomy_colon' in confidence_signals:
                    pass  # Plain-text taxonomy + colon is strong enough (for unformatted CVs)
                elif 'taxonomy_exact_large_font' in confidence_signals:
                    # Taxonomy EXACT match + large font (for simple CVs)
                    pass
                elif 'formatted_header' in confidence_signals and len(confidence_signals) >= 2:
                    pass  # Formatted + 1 other signal
                elif len(confidence_signals) < 3:
                    is_header = False
            # Special case: horizontal_rules + ALL CAPS (even without bold)
            # This catches CVs where CIDFonts don't report bold correctly
            elif ('horizontal_rules' in confidence_signals and
                  line['is_all_caps'] and
                  5 <= len(text) <= 80 and
                  not re.match(r'^[A-Z]{1,3},', text) and  # Not "AZ," or similar abbreviations
                  not re.match(r'^\d', text)):  # Not starting with number
                is_header = True
                confidence_signals.append('h_rules_caps')  # Special combined signal

            if is_header:
                sections.append({
                    'line_number': line['line_number'],
                    'header': text,
                    'header_upper': text.upper(),
                    'font_size': line['avg_size'],
                    'is_bold': line['is_bold'],
                    'is_all_caps': line['is_all_caps'],
                    'is_centered': line.get('is_centered', False),
                    'has_underline': line.get('has_underline_below', False),
                    'indent_level': line['indent_level'],
                    'confidence_signals': confidence_signals,
                    'confidence': len(confidence_signals),
                    'detection_phase': 1
                })

            prev_y = line['y_position']

        return sections

    def _score_header_candidate(self, line: Dict, taxonomy_match: Optional[Dict],
                                 spacing: float, patterns: Dict, all_lines: List[Dict]) -> Tuple[int, List[str], Optional[Dict], str]:
        """
        Calculate likelihood score for a line being a header.

        Philosophy: "Beggars can't be choosers" - score everything and take the best available.

        Returns:
            tuple: (score, signals, taxonomy_match, cleaned_text)
        """
        score = 0
        signals = []
        text = line['text']

        # === TEXT CLEANING ===
        # Substitute spaces for internal tabs
        cleaned_text = re.sub(r'\t+', ' ', text)
        cleaned_text = re.sub(r' {2,}', ' ', cleaned_text).strip()

        # Store original for pattern matching, use cleaned for display
        original_text = cleaned_text

        # Strip numbering prefixes but use colon before stripping
        has_colon = cleaned_text.endswith(':')

        # Remove leading numbering (1., A., III., etc.) but keep rest
        cleaned_text = re.sub(r'^[IVXivx]+\.\s+', '', cleaned_text)  # Roman numerals
        cleaned_text = re.sub(r'^[A-Z]\.\s+', '', cleaned_text)      # Single letter
        cleaned_text = re.sub(r'^\d+\.\s+', '', cleaned_text)        # Numbers

        # Remove "(continued)" suffix
        cleaned_text = re.sub(r'\s*\(continued\)$', '', cleaned_text, flags=re.I)

        # Remove trailing colon (but we already recorded its presence)
        if cleaned_text.endswith(':'):
            cleaned_text = cleaned_text[:-1].strip()

        # === FORMATTING SIGNALS ===
        if line.get('is_bold', False):
            score += 20
            signals.append('bold:20')

        is_caps = original_text.isupper() and 5 <= len(original_text) <= 100
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
        if not taxonomy_match:
            taxonomy_match = self._match_taxonomy_exact(original_text)
        if not taxonomy_match:
            taxonomy_match = self._match_taxonomy_fuzzy(original_text)

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

        if has_colon:
            score += 10
            signals.append('colon:10')

        if 5 <= len(cleaned_text) <= 50:
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

        # Special bonus for numbered section headers with Roman numerals or Arabic numbers + ALL CAPS
        # Patterns: "I. EDUCATION", "II. TRAINING:", "1. PUBLICATIONS", "2. GRANTS:"
        # Must be left-justified or centered
        is_left_justified = line.get('indent_level', 999) <= patterns['base_indent']
        is_centered_line = line.get('is_centered', False)

        if (is_left_justified or is_centered_line):
            # Check for Roman numeral + ALL CAPS: "I. EDUCATION", "II. TRAINING", "III. GRANTS:"
            # Allow optional colon at the end
            if re.match(r'^[IVX]+\.\s+[A-Z\s&,/]+:?$', original_text):
                score += 25
                signals.append('BONUS:roman_numeral_caps:25')
            # Check for Arabic number + ALL CAPS: "1. EDUCATION", "2. TRAINING", "3. GRANTS:"
            # Allow optional colon at the end
            elif re.match(r'^\d+\.\s+[A-Z\s&,/]+:?$', original_text):
                score += 25
                signals.append('BONUS:arabic_numeral_caps:25')

        # === PENALTIES ===

        # Standalone years or dates (2014:, February 2020, etc.)
        if re.match(r'^\d{4}:?$', original_text) or re.match(r'^(January|February|March|April|May|June|July|August|September|October|November|December),?\s+\d{4}$', original_text, re.I):
            score -= 50
            signals.append('PENALTY:year_date:-50')

        # Lines starting with years
        if re.match(r'^\d{4}\s+[A-Z]', original_text):
            score -= 35
            signals.append('PENALTY:year_prefix:-35')

        if re.match(r'^\d{4}[-/]', original_text):
            score -= 30
            signals.append('PENALTY:date:-30')

        if re.search(r'PMID|PMC\d+|DOI:', original_text):
            score -= 40
            signals.append('PENALTY:citation:-40')

        if len(original_text) > 80:
            score -= 20
            signals.append('PENALTY:too_long:-20')

        if len(original_text) < 5:
            score -= 15
            signals.append('PENALTY:too_short:-15')

        if re.match(r'^(Page|PAGE)\s+\d+', original_text):
            score -= 50
            signals.append('PENALTY:page_num:-50')

        # NEW PENALTIES based on user feedback

        # Numbered list items (publication/grant records: "1. Author...", "2. Smith...")
        # BUT exclude numbered section headers with ALL CAPS (those get a bonus instead)
        is_numbered_section_header = (
            (re.match(r'^[IVX]+\.\s+[A-Z\s&,/]+:?$', original_text) or
             re.match(r'^\d+\.\s+[A-Z\s&,/]+:?$', original_text)) and
            original_text.isupper()
        )
        if re.match(r'^\d+\.\s+[A-Z]', original_text) and not taxonomy_match and not is_numbered_section_header:
            score -= 45
            signals.append('PENALTY:numbered_item:-45')

        # Person names with credentials (extended pattern)
        # Matches: "Edward S. Peters, DMD, SM, ScD", "BARBARA C. WALLACE, PH.D."
        if re.search(r',\s+(MD|DO|Ph\.?D\.?|DDS|DVM|MPH|MS|MA|ScD|DMD|SM|EdD|FAPTA)\b', original_text, re.I):
            score -= 45
            signals.append('PENALTY:credentials:-45')

        # Full name patterns (Title Case Name with initials/credentials, often centered)
        # Matches: "Edward S. Peters, DMD, SM, ScD", "James Gordon, PT, EdD"
        if re.match(r'^[A-Z][a-z]+(\s+[A-Z]\.?)+ [A-Z][a-z]+', original_text) and line.get('is_centered', False):
            score -= 40
            signals.append('PENALTY:person_name:-40')

        # Check for repetition (page headers with person names)
        text_count = sum(1 for l in all_lines if l.get('text', '').strip() == original_text)
        if text_count >= 3:
            score -= 35
            signals.append(f'PENALTY:repeated_{text_count}x:-35')

        # Role titles (Chair, Panelist, etc.)
        if re.match(r'^(Chair|Co-Chair|Panelist|Lecturer|Moderator|Presenter|Speaker)\s*[-:]', original_text, re.I):
            score -= 35
            signals.append('PENALTY:role_title:-35')

        # Fully parenthetical text
        if re.match(r'^\(.*\)$', original_text):
            score -= 30
            signals.append('PENALTY:parenthetical:-30')

        # Long title-case text without colon (likely descriptions)
        word_count = len(original_text.split())
        is_title_case = original_text[0].isupper() and not original_text.isupper()
        if word_count > 8 and is_title_case and not has_colon:
            score -= 25
            signals.append('PENALTY:long_titlecase:-25')

        # Quotation marks (usually paper/presentation titles, not section headers)
        if '"' in original_text or '"' in original_text or "'" in original_text:
            score -= 15
            signals.append('PENALTY:quoted:-15')

        # "with [Name]" pattern (collaborator lists)
        if re.match(r'^with\s+[A-Z]', original_text):
            score -= 30
            signals.append('PENALTY:with_name:-30')

        # Document title headers - STRENGTHENED (not section headers)
        # Matches: "Curriculum Vitae", "CURRICULUM VITAE OF", "CV", etc.
        if re.search(r'\bCURRICULUM\s+VITAE\b', original_text, re.I) or re.match(r'^(RESUME|CV|BIOGRAPHICAL SKETCH)$', original_text, re.I):
            score -= 60
            signals.append('PENALTY:doc_title:-60')

        # Generic "Other" word (too vague to be useful section)
        if re.match(r'^Other$', cleaned_text, re.I):
            score -= 40
            signals.append('PENALTY:generic_other:-40')

        # PDF extraction errors: very long strings with no spaces
        if len(original_text) > 40 and ' ' not in original_text:
            score -= 50
            signals.append('PENALTY:no_spaces:-50')

        # Single-word ALL CAPS watermarks/labels
        if len(original_text.split()) == 1 and original_text.isupper() and len(original_text) > 8 and not taxonomy_match:
            score -= 25
            signals.append('PENALTY:watermark:-25')

        # NEW ROUND 2 PENALTIES based on additional user feedback

        # ALL CAPS centered names without taxonomy match (page headers)
        # Matches: "DEBORAH C. MARSHALL" - centered, ALL CAPS, no taxonomy
        if is_caps and line.get('is_centered', False) and not taxonomy_match and len(original_text.split()) >= 2:
            score -= 45
            signals.append('PENALTY:caps_centered_name:-45')

        # Single generic words without taxonomy (Refereed, development, etc.)
        # But exclude if it's a plural taxonomy match
        if (len(original_text.split()) == 1 and
            not taxonomy_match and
            not re.match(r'^[A-Z]+$', original_text) and  # Not all caps abbreviations
            len(original_text) > 3):
            score -= 30
            signals.append('PENALTY:single_generic_word:-30')

        # Author list patterns (initials with commas, asterisks)
        # Matches: "VMB Silenzio, CA Irvine, B Bregman" or "*Snyder A, Alsauskas Z"
        if re.search(r'\b[A-Z]{1,3}\s+[A-Z][a-z]+,', original_text) or re.match(r'^\*[A-Z]', original_text):
            score -= 45
            signals.append('PENALTY:author_list:-45')

        # Date ranges and year patterns
        # Matches: "2007-2010", "V, 1980.", "Freelance Writer, Producer (2007-2010)"
        if re.search(r'\b\d{4}\s*[-–]\s*\d{4}\b', original_text) or re.search(r',\s+\d{4}\.?$', original_text):
            score -= 40
            signals.append('PENALTY:date_range:-40')

        # Multiple commas (2+) - likely detailed descriptions/records
        # Matches: "Fellowships: Hodin Lab, Dep of Surgery, Massachusetts"
        comma_count = original_text.count(',')
        if comma_count >= 2:
            score -= 30
            signals.append(f'PENALTY:multi_comma_{comma_count}:-30')

        # NEW ROUND 3 PENALTIES based on additional user feedback

        # Simple name patterns: [Capital][lowercase] [Capital letters]
        # Matches: "Seale JP", "Smith AB", "Johnson CD"
        if re.match(r'^[A-Z][a-z]{2,12}\s+[A-Z]{1,3}$', original_text):
            score -= 50
            signals.append('PENALTY:simple_name:-50')

        # Phone numbers
        # Matches: "TELEPHONE: (970) 491 0878", "(555) 123-4567", "Phone: 555-1234"
        if re.search(r'\b\d{3}[-.)]\s*\d{3}[-.\s]\d{4}\b', original_text) or re.match(r'^(TELEPHONE|PHONE|TEL|FAX):', original_text, re.I):
            score -= 50
            signals.append('PENALTY:phone:-50')

        # All lowercase text (very bad sign for header)
        # Matches: "solving skills.", "programs", "review"
        if original_text.islower():
            score -= 50
            signals.append('PENALTY:all_lowercase:-50')

        # Too long (>58 characters) - 95% of headers are 8-58 chars
        if len(original_text) > 58:
            score -= 35
            signals.append('PENALTY:too_long_58:-35')

        # Unbalanced parentheses - ends with ) but no opening (
        # Matches: "review)", "poster)."
        if original_text.rstrip('.').endswith(')') and '(' not in original_text:
            score -= 45
            signals.append('PENALTY:unbalanced_paren:-45')

        # Continent names (geographic subdivisions, not sections)
        # Matches: "NORTH AMERICA", "EUROPE", "ASIA", "MIDDLE EAST", "CENTRAL/SOUTH AMERICA"
        continent_pattern = r'^(NORTH\s+AMERICA|SOUTH\s+AMERICA|CENTRAL[\s/]+SOUTH\s+AMERICA|EUROPE|ASIA|AFRICA|AUSTRALIA|MIDDLE\s+EAST|OCEANIA)$'
        if re.match(continent_pattern, original_text, re.I):
            score -= 50
            signals.append('PENALTY:continent:-50')

        # Standalone "Refereed" without context (sub-section markers)
        if re.match(r'^Refereed$', cleaned_text, re.I):
            score -= 45
            signals.append('PENALTY:standalone_refereed:-45')

        # Years in middle of text (not just at start) - strengthened
        # Matches: "M.D., 1984 (AOA)", "June 1988 Volunteer", "May 2010 Nightingale Award"
        if re.search(r'[A-Za-z,]\s+\d{4}\s+[A-Z]', original_text) or re.search(r',\s+\d{4}\s+\(', original_text):
            score -= 45
            signals.append('PENALTY:year_in_text:-45')

        # Text enclosed in brackets [...] - STRENGTHENED
        # Matches: "[3/10/20; DW]", "[3/21/20 NTDTV]", "[3/13/20; National Public Radio]"
        if re.match(r'^\[.*\]$', original_text):
            score -= 100
            signals.append('PENALTY:bracketed:-100')

        # Comprehensive date penalties - ALL variations
        # Bracketed dates with content: [M/D/YY; Source], [M/D/YYYY Source]
        if re.search(r'\[\d{1,2}[/-]\d{1,2}[/-]\d{2,4}[;\s]', original_text):
            score -= 80
            signals.append('PENALTY:bracketed_date:-80')

        # Years in middle of sentences: "article in JAMA in 2020", "published 2019 study"
        if re.search(r'\b(in|published|circa|ca\.?)\s+\d{4}\b', original_text, re.I):
            score -= 70
            signals.append('PENALTY:year_in_sentence:-70')

        # Date formats: M/D/YY, M/D/YYYY, MM/DD/YYYY
        if re.search(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b', original_text):
            score -= 75
            signals.append('PENALTY:date_format:-75')

        # Month Day, Year: "March 13, 2020", "Jan 15 2019"
        month_pattern = r'\b(January|February|March|April|May|June|July|August|September|October|November|December|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\.?\s+\d{1,2},?\s+\d{4}\b'
        if re.search(month_pattern, original_text, re.I):
            score -= 75
            signals.append('PENALTY:month_day_year:-75')

        # Year ranges with dash/en-dash: "2007-2010", "2015–2020"
        if re.search(r'\b\d{4}\s*[-–—]\s*\d{4}\b', original_text):
            score -= 70
            signals.append('PENALTY:year_range:-70')

        # Years with parenthetical info: "2020 (ongoing)", "2018 (revised)"
        if re.search(r'\d{4}\s+\([^)]+\)', original_text):
            score -= 65
            signals.append('PENALTY:year_parenthetical:-65')

        # Unbalanced punctuation detection
        # Unbalanced quotes (single or double)
        double_quote_count = original_text.count('"') + original_text.count('"') + original_text.count('"')
        single_quote_count = original_text.count("'") + original_text.count("'") + original_text.count("'")
        open_bracket_count = original_text.count('[')
        close_bracket_count = original_text.count(']')
        open_brace_count = original_text.count('{')
        close_brace_count = original_text.count('}')

        is_unbalanced = False
        if double_quote_count % 2 != 0:  # Odd number of double quotes
            is_unbalanced = True
        if single_quote_count % 2 != 0 and not original_text.strip().endswith("'s"):  # Odd single quotes (not possessive)
            is_unbalanced = True
        if open_bracket_count != close_bracket_count:  # Unbalanced brackets
            is_unbalanced = True
        if open_brace_count != close_brace_count:  # Unbalanced braces
            is_unbalanced = True

        if is_unbalanced:
            score -= 60
            signals.append('PENALTY:unbalanced_punctuation:-60')

        # Colon in middle of text (not just at end) - indicates description, not header
        # Matches: "Role: Co-captain for Team", "Grant: NIH R01"
        # But we already give bonus for trailing colon, so penalize internal colons
        if ':' in original_text[:-1]:  # Colon not at the very end
            score -= 20
            signals.append('PENALTY:internal_colon:-20')

        # "continued" at end (should fold into parent section) - STRENGTHENED
        # Matches: "International University Service continued"
        if re.search(r'\bcontinued$', cleaned_text, re.I):
            score -= 50
            signals.append('PENALTY:continued_end:-50')

        # Website/URL patterns
        # Matches: "Website: http://www.example.com", "http://", "https://", "www."
        if re.search(r'(https?://|www\.|\.(com|org|edu|gov|net)/)', original_text, re.I) or re.match(r'^(WEBSITE|URL|HOMEPAGE):', original_text, re.I):
            score -= 50
            signals.append('PENALTY:website:-50')

        # Too many periods (3+) - likely citations or abbreviations
        # Matches: "Innovation and Thought Leadership. (2015). Sydney, Australia."
        period_count = original_text.count('.')
        if period_count >= 3:
            score -= 40
            signals.append(f'PENALTY:multi_period_{period_count}:-40')

        # Ending with & (incomplete text)
        # Matches: "Timeframe &", "Research &"
        if original_text.rstrip().endswith('&'):
            score -= 40
            signals.append('PENALTY:trailing_ampersand:-40')

        # === BONUSES ===

        # Plural forms ending in 's' or 'S' - more likely to be sections
        # Matches: "Book Chapters", "Grants and Contracts", "Scholarships"
        # Exception: don't apply if it's a name pattern or already low score
        if (cleaned_text.endswith('s') or cleaned_text.endswith('S')) and score >= 40:
            score += 5
            signals.append('BONUS:plural:5')

        # Non-caps text gets penalty (lowercase or mixed case less likely to be headers)
        # Matches: "programs", "solving skills."
        # But don't penalize if has strong taxonomy match
        if (not is_caps and
            not line.get('is_bold', False) and
            (not taxonomy_match or taxonomy_match['specificity_rank'] < 70)):
            score -= 15
            signals.append('PENALTY:non_caps:-15')

        return score, signals, taxonomy_match, cleaned_text

    def _phase1_font_detection_scored(self, lines: List[Dict], patterns: Dict) -> List[Dict]:
        """
        PHASE 1: Score-based detection - rank all candidates and take best available.

        Tiers:
        - Tier 1 (≥100 pts): Definite headers - always accept
        - Tier 2 (70-99 pts): Very likely headers - accept
        - Tier 3 (50-69 pts): Likely headers - accept if <8 sections from higher tiers
        - Tier 4 (30-49 pts): Possible headers - accept ONLY if <3 sections total
        """
        candidates = []
        prev_y = None

        # Strict exclusion patterns
        exclusion_patterns = [
            r'^Funding:\s*\$',
            r'^Total:\s*\$',
            r'^\d{4}[-/]\d{2}',
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

            if line.get('is_underlined', False):
                prev_y = line.get('y_position')
                continue

            if any(re.match(pat, text) for pat in exclusion_patterns):
                prev_y = line.get('y_position')
                continue

            spacing = 0
            if prev_y is not None:
                spacing = line.get('y_position', 0) - prev_y

            score, signals, taxonomy, cleaned_text = self._score_header_candidate(
                line, None, spacing, patterns, lines
            )

            if score >= 30:
                candidates.append({
                    'line': line,
                    'score': score,
                    'signals': signals,
                    'taxonomy': taxonomy,
                    'cleaned_text': cleaned_text
                })

            prev_y = line.get('y_position')

        # Sort by score descending
        candidates.sort(key=lambda x: x['score'], reverse=True)

        # Tier-based selection
        tier1 = [c for c in candidates if c['score'] >= 100]
        tier2 = [c for c in candidates if 70 <= c['score'] < 100]
        tier3 = [c for c in candidates if 50 <= c['score'] < 70]
        tier4 = [c for c in candidates if 30 <= c['score'] < 50]

        final_sections = []
        final_sections.extend(tier1)
        final_sections.extend(tier2)

        if len(final_sections) < 8:
            final_sections.extend(tier3)

        if len(final_sections) < 3:
            final_sections.extend(tier4)

        # Convert to section format
        sections = []
        for candidate in final_sections:
            line = candidate['line']
            cleaned_text = candidate['cleaned_text']

            section = {
                'line_number': line['line_number'],
                'header': cleaned_text,  # Use cleaned text for display
                'header_upper': cleaned_text.upper(),
                'font_size': line.get('avg_size', 0),
                'is_bold': line.get('is_bold', False),
                'is_all_caps': line['text'].isupper(),
                'is_centered': line.get('is_centered', False),
                'has_underline': line.get('has_underline_below', False),
                'indent_level': line.get('indent_level', 0),
                'confidence_signals': candidate['signals'],
                'confidence_score': candidate['score'],
                'confidence': candidate['score'],
                'detection_phase': 1
            }

            if candidate['taxonomy']:
                section['taxonomy_id'] = candidate['taxonomy']['id']
                section['taxonomy_canonical'] = candidate['taxonomy']['canonical']
                section['taxonomy_specificity'] = candidate['taxonomy']['specificity_rank']
                section['taxonomy_parent'] = candidate['taxonomy'].get('parent')

            sections.append(section)

        return sections

    def _create_style_fingerprint(self, line: Dict, patterns: Dict) -> str:
        """
        Create a formatting fingerprint for a line.

        Returns a string key representing the style (font size bucket, bold, caps, indent, etc.)
        Lines with the same fingerprint share the same formatting style.
        """
        # Bucket font size to nearest 0.5pt to handle minor variations
        size_bucket = round(line.get('avg_size', 0) * 2) / 2

        # Boolean flags
        is_bold = line.get('is_bold', False)
        is_caps = line.get('is_all_caps', False)
        is_centered = line.get('is_centered', False)
        has_underline = line.get('has_underline_below', False)
        has_h_rule = line.get('has_horizontal_rule', False)

        # Indent level (bucket to reduce noise)
        indent = line.get('indent_level', 0)

        # Create fingerprint string
        fingerprint = f"size:{size_bucket}|bold:{is_bold}|caps:{is_caps}|center:{is_centered}|under:{has_underline}|hrule:{has_h_rule}|indent:{indent}"

        return fingerprint

    def _phase1_style_clustering(self, lines: List[Dict], patterns: Dict) -> List[Dict]:
        """
        PHASE 1: Style-based clustering approach.

        Strategy:
        1. Score all candidate lines (use existing scoring logic)
        2. Group candidates by formatting style fingerprint
        3. Validate each style group against taxonomy
        4. Identify h2/h3 styles (groups with strong taxonomy matches)
        5. Extract ALL lines matching validated styles
        """
        logger.info("Step 1: Scoring all candidates")

        # Step 1: Score all candidates (reuse existing scoring logic)
        candidates = []
        prev_y = None

        # Strict exclusion patterns
        exclusion_patterns = [
            r'^Funding:\s*\$',
            r'^Total:\s*\$',
            r'^\d{4}[-/]\d{2}',
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

            if line.get('is_underlined', False):
                prev_y = line.get('y_position')
                continue

            if any(re.match(pat, text) for pat in exclusion_patterns):
                prev_y = line.get('y_position')
                continue

            spacing = 0
            if prev_y is not None:
                spacing = line.get('y_position', 0) - prev_y

            # Score this candidate
            score, signals, taxonomy, cleaned_text = self._score_header_candidate(
                line, None, spacing, patterns, lines
            )

            # Accept candidates with score >= 30 (same as before)
            if score >= 30:
                fingerprint = self._create_style_fingerprint(line, patterns)
                candidates.append({
                    'line': line,
                    'line_number': line['line_number'],
                    'score': score,
                    'signals': signals,
                    'taxonomy': taxonomy,
                    'cleaned_text': cleaned_text,
                    'style_fingerprint': fingerprint
                })

            prev_y = line.get('y_position')

        logger.info(f"Found {len(candidates)} scored candidates")

        # Step 2: Group by style fingerprint
        logger.info("Step 2: Grouping by style fingerprint")
        style_groups = {}
        for candidate in candidates:
            fp = candidate['style_fingerprint']
            if fp not in style_groups:
                style_groups[fp] = []
            style_groups[fp].append(candidate)

        logger.info(f"Found {len(style_groups)} distinct formatting styles")

        # Step 3: Score each style group based on taxonomy matches
        logger.info("Step 3: Validating style groups with taxonomy")
        validated_styles = []

        for fingerprint, group in style_groups.items():
            # Count taxonomy matches in this group
            taxonomy_matches = [c for c in group if c['taxonomy'] is not None]
            taxonomy_count = len(taxonomy_matches)
            taxonomy_ratio = taxonomy_count / len(group) if group else 0

            # Calculate average score and taxonomy specificity
            avg_score = sum(c['score'] for c in group) / len(group)
            avg_specificity = sum(c['taxonomy']['specificity_rank'] for c in taxonomy_matches) / len(taxonomy_matches) if taxonomy_matches else 0

            # Calculate group quality score (0-100)
            # Combines taxonomy match rate, specificity, and formatting score
            quality_score = (
                taxonomy_ratio * 50 +  # Taxonomy match rate (0-50 points)
                (avg_specificity / 100) * 30 +  # Avg specificity (0-30 points)
                (min(avg_score, 100) / 100) * 20  # Avg formatting score (0-20 points)
            )

            # Group validation criteria:
            # - At least 2 members (to establish pattern)
            # - At least 30% taxonomy match rate OR avg score >= 70
            # - Average specificity >= 60 if using taxonomy
            # - Quality score >= 30 (overall confidence threshold)
            is_valid_style = (
                len(group) >= 2 and
                (taxonomy_ratio >= 0.30 or avg_score >= 70) and
                (taxonomy_ratio == 0 or avg_specificity >= 60) and
                quality_score >= 30
            )

            if is_valid_style:
                validated_styles.append({
                    'fingerprint': fingerprint,
                    'group': group,
                    'size': len(group),
                    'taxonomy_count': taxonomy_count,
                    'taxonomy_ratio': taxonomy_ratio,
                    'avg_score': avg_score,
                    'avg_specificity': avg_specificity,
                    'quality_score': quality_score  # NEW: Overall group quality
                })
                logger.info(f"  Valid style: {fingerprint[:60]}... ({len(group)} members, {taxonomy_ratio:.1%} taxonomy, quality={quality_score:.0f})")

        logger.info(f"Validated {len(validated_styles)} style groups")

        # Step 4: Identify h2 and h3 styles (top 2 validated styles by size/score)
        # Sort by: taxonomy_ratio desc, size desc, avg_score desc
        validated_styles.sort(key=lambda x: (x['taxonomy_ratio'], x['size'], x['avg_score']), reverse=True)

        # Take top 2-3 styles as h2/h3
        header_styles = validated_styles[:3]
        header_fingerprints = {s['fingerprint'] for s in header_styles}

        logger.info(f"Step 4: Identified {len(header_styles)} header style(s)")
        for i, style in enumerate(header_styles, 1):
            logger.info(f"  h{i+1} style: {len(style['group'])} sections, {style['taxonomy_ratio']:.1%} taxonomy")

        # Step 5: Extract ALL lines matching validated header styles
        logger.info("Step 5: Extracting all lines with header styles")
        final_sections = []

        for line in lines:
            text = line['text']
            if not text or len(text) < 3:
                continue

            if line.get('is_underlined', False):
                continue

            if any(re.match(pat, text) for pat in exclusion_patterns):
                continue

            # Check if this line matches a validated header style
            fingerprint = self._create_style_fingerprint(line, patterns)

            if fingerprint in header_fingerprints:
                # Re-score this line to get cleaned text and taxonomy
                spacing = 0  # Don't have prev_y here, but spacing is less important now
                score, signals, taxonomy, cleaned_text = self._score_header_candidate(
                    line, None, spacing, patterns, lines
                )

                # Apply our penalty filters one more time
                if score < 0:  # Heavily penalized
                    continue

                section = {
                    'line_number': line['line_number'],
                    'header': cleaned_text,
                    'header_upper': cleaned_text.upper(),
                    'font_size': line.get('avg_size', 0),
                    'is_bold': line.get('is_bold', False),
                    'is_all_caps': line.get('is_all_caps', False),
                    'is_centered': line.get('is_centered', False),
                    'has_underline': line.get('has_underline_below', False),
                    'indent_level': line.get('indent_level', 0),
                    'confidence_signals': signals + ['style_validated'],
                    'confidence_score': score,
                    'confidence': score,
                    'detection_phase': 1,
                    'style_fingerprint': fingerprint
                }

                if taxonomy:
                    section['taxonomy_id'] = taxonomy['id']
                    section['taxonomy_canonical'] = taxonomy['canonical']
                    section['taxonomy_specificity'] = taxonomy['specificity_rank']
                    section['taxonomy_parent'] = taxonomy.get('parent')

                final_sections.append(section)

        logger.info(f"Extracted {len(final_sections)} total sections using style clustering")

        # Step 6: Deduplicate consecutive identical section headers
        logger.info("Step 6: Deduplicating consecutive identical headers")
        if final_sections:
            deduplicated_sections = [final_sections[0]]  # Keep first section

            for i in range(1, len(final_sections)):
                current = final_sections[i]
                previous = deduplicated_sections[-1]

                # Check if consecutive sections have identical header text
                if current['header'].upper() == previous['header'].upper():
                    # Same header repeated - this is a page break continuation
                    logger.info(f"  Skipping duplicate: '{current['header']}' at line {current['line_number']}")
                    continue

                deduplicated_sections.append(current)

            logger.info(f"Removed {len(final_sections) - len(deduplicated_sections)} duplicate headers")
            final_sections = deduplicated_sections

        # Add style group quality scores to sections
        style_quality_map = {s['fingerprint']: s['quality_score'] for s in header_styles}
        for section in final_sections:
            fp = section.get('style_fingerprint', '')
            if fp in style_quality_map:
                section['style_group_quality'] = style_quality_map[fp]

        return final_sections

    def _phase1_llm_detection(self, lines: List[Dict], patterns: Dict) -> List[Dict]:
        """
        PHASE 1: Hybrid detection - taxonomy + formatting first, LLM for borderline cases.

        Strategy:
        1. Score all candidates using existing logic
        2. Auto-accept high-confidence matches (score ≥ 90)
        3. Auto-reject low-confidence (score < 40)
        4. Use LLM for borderline cases (40 ≤ score < 90)
        """
        try:
            from cv_parser.llm_section_evaluator import evaluate_section_header
        except ImportError:
            from llm_section_evaluator import evaluate_section_header

        # Basic exclusion patterns
        exclusion_patterns = [
            r'^Funding:\s*\$',
            r'^Total:\s*\$',
            r'^\d{4}[-/]\d{2}',  # Dates
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
        ]

        # Step 1: Score all candidates using existing scoring logic
        logger.info("Step 1: Scoring candidates with taxonomy + formatting")
        candidates = []
        prev_y = None

        for line in lines:
            text = line['text']
            if not text or len(text) < 3:
                prev_y = line.get('y_position')
                continue

            # Skip underline lines
            if line.get('is_underlined', False):
                prev_y = line.get('y_position')
                continue

            # Check exclusions
            if any(re.match(pat, text) for pat in exclusion_patterns):
                prev_y = line.get('y_position')
                continue

            # Calculate spacing
            spacing = 0
            if prev_y is not None:
                spacing = line.get('y_position', 0) - prev_y

            # Score this candidate using existing scoring function
            score, signals, taxonomy, cleaned_text = self._score_header_candidate(
                line, None, spacing, patterns, lines
            )

            # Collect candidates with score ≥ 30 (same as scoring approach)
            if score >= 30:
                candidates.append({
                    'line': line,
                    'line_number': line['line_number'],
                    'text': cleaned_text,
                    'original_text': text,
                    'score': score,
                    'signals': signals,
                    'taxonomy_match': taxonomy,
                    'font_size': line.get('avg_size', patterns['body_size']),
                    'is_bold': line.get('is_bold', False),
                    'is_caps': line.get('is_all_caps', False),
                    'has_colon': text.endswith(':'),
                    'is_centered': line.get('is_centered', False)
                })

            prev_y = line.get('y_position')

        logger.info(f"Found {len(candidates)} scored candidates")

        # Step 2: Categorize by confidence
        auto_accept = [c for c in candidates if c['score'] >= 90]
        borderline = [c for c in candidates if 40 <= c['score'] < 90]
        auto_reject = [c for c in candidates if c['score'] < 40]

        logger.info(f"  Auto-accept (score ≥90): {len(auto_accept)}")
        logger.info(f"  Borderline (40-89): {len(borderline)} - will use LLM")
        logger.info(f"  Auto-reject (<40): {len(auto_reject)}")

        sections = []

        # Step 3: Add auto-accepted sections
        for candidate in auto_accept:
            line = candidate['line']
            sections.append({
                'line_number': line['line_number'],
                'header': candidate['text'],
                'header_upper': candidate['text'].upper(),
                'font_size': candidate['font_size'],
                'is_bold': candidate['is_bold'],
                'is_all_caps': candidate['is_caps'],
                'is_centered': candidate['is_centered'],
                'has_underline': line.get('has_underline_below', False),
                'indent_level': line.get('indent_level', 0),
                'confidence_signals': candidate['signals'] + ['auto_accept_high_score'],
                'confidence': candidate['score'],
                'detection_phase': 1,
                'detection_method': 'taxonomy+formatting'
            })

            # Add taxonomy info if we have it
            if candidate['taxonomy_match']:
                sections[-1]['taxonomy_id'] = candidate['taxonomy_match']['id']
                sections[-1]['taxonomy_canonical'] = candidate['taxonomy_match']['canonical']
                sections[-1]['taxonomy_specificity'] = candidate['taxonomy_match']['specificity_rank']

        # Step 4: Evaluate borderline cases with LLM
        if borderline:
            logger.info(f"Step 4: Evaluating {len(borderline)} borderline cases with LLM")

            for i, candidate in enumerate(borderline):
                if (i + 1) % 10 == 0:
                    logger.info(f"  Evaluated {i + 1}/{len(borderline)} borderline cases")

                llm_result = evaluate_section_header(
                    line_text=candidate['original_text'],
                    font_size=candidate['font_size'],
                    body_size=patterns['body_size'],
                    is_bold=candidate['is_bold'],
                    is_caps=candidate['is_caps'],
                    has_colon=candidate['has_colon'],
                    is_centered=candidate['is_centered'],
                )

                # Accept if LLM confirms with confidence ≥ 70
                if llm_result['is_header'] and llm_result['confidence'] >= 70 and llm_result['error'] is None:
                    line = candidate['line']
                    sections.append({
                        'line_number': line['line_number'],
                        'header': candidate['text'],
                        'header_upper': candidate['text'].upper(),
                        'font_size': candidate['font_size'],
                        'is_bold': candidate['is_bold'],
                        'is_all_caps': candidate['is_caps'],
                        'is_centered': candidate['is_centered'],
                        'has_underline': line.get('has_underline_below', False),
                        'indent_level': line.get('indent_level', 0),
                        'confidence_signals': candidate['signals'] + ['llm_confirmed'],
                        'confidence': llm_result['confidence'],
                        'detection_phase': 1,
                        'detection_method': 'llm_borderline',
                        'llm_reasoning': llm_result['reasoning'],
                        'pre_llm_score': candidate['score']
                    })

                    # Add taxonomy info if we have it
                    if candidate['taxonomy_match']:
                        sections[-1]['taxonomy_id'] = candidate['taxonomy_match']['id']
                        sections[-1]['taxonomy_canonical'] = candidate['taxonomy_match']['canonical']
                        sections[-1]['taxonomy_specificity'] = candidate['taxonomy_match']['specificity_rank']

        # Sort by line number
        sections.sort(key=lambda s: s['line_number'])

        logger.info(f"Total accepted: {len(sections)} sections ({len(auto_accept)} auto, {sum(1 for s in sections if s.get('detection_method') == 'llm_borderline')} via LLM)")

        # Step 5: Deduplicate consecutive identical headers
        if sections:
            deduplicated = [sections[0]]
            for section in sections[1:]:
                if section['header'].upper() != deduplicated[-1]['header'].upper():
                    deduplicated.append(section)
                else:
                    logger.info(f"  Skipping duplicate: '{section['header']}' at line {section['line_number']}")
            logger.info(f"Removed {len(sections) - len(deduplicated)} duplicate headers")
            sections = deduplicated

        return sections

    def _phase2_taxonomy_mapping(self, sections: List[Dict]) -> List[Dict]:
        """
        PHASE 2: Map detected sections to taxonomy.
        Standardize names but don't change detection.
        """
        for section in sections:
            text = section['header']

            # Try to match against taxonomy
            taxonomy_match = self._match_taxonomy(text)

            if taxonomy_match:
                section['taxonomy_id'] = taxonomy_match['id']
                section['taxonomy_canonical'] = taxonomy_match['canonical']
                section['taxonomy_specificity'] = taxonomy_match['specificity_rank']
                section['taxonomy_parent'] = taxonomy_match['parent']
                # Add small confidence boost for taxonomy match
                section['confidence'] = min(section['confidence'] + 1, 10)
                section['confidence_signals'].append('taxonomy_mapped')

        # Combine contiguous sections if they form better matches
        sections = self._combine_contiguous_headers(sections)

        return sections

    def _combine_contiguous_headers(self, sections: List[Dict]) -> List[Dict]:
        """
        Combine adjacent section headers if they form a better taxonomy match together.
        Example: "INVITED" + "TALKS" → "INVITED TALKS"
        """
        if len(sections) <= 1:
            return sections

        combined_sections = []
        i = 0

        while i < len(sections):
            current = sections[i]

            # Look ahead to see if next section should be combined
            if i + 1 < len(sections):
                next_section = sections[i + 1]

                # Check if sections are consecutive (within 2 line numbers)
                line_gap = next_section['line_number'] - current['line_number']

                if 1 <= line_gap <= 2:
                    # Try combining the headers
                    combined_text = f"{current['header']} {next_section['header']}"
                    combined_match = self._match_taxonomy(combined_text)

                    # Check if combined version has better taxonomy match
                    current_has_tax = 'taxonomy_id' in current
                    next_has_tax = 'taxonomy_id' in next_section

                    should_combine = False

                    # Case 1: Neither has taxonomy, combined does
                    if not current_has_tax and not next_has_tax and combined_match:
                        should_combine = True
                        logger.info(f"Combining '{current['header']}' + '{next_section['header']}' → '{combined_text}' (new taxonomy match)")

                    # Case 2: Combined has higher specificity than either individual
                    elif combined_match:
                        current_spec = current.get('taxonomy_specificity', 0) if current_has_tax else 0
                        next_spec = next_section.get('taxonomy_specificity', 0) if next_has_tax else 0
                        combined_spec = combined_match['specificity_rank']

                        if combined_spec > max(current_spec, next_spec):
                            should_combine = True
                            logger.info(f"Combining '{current['header']}' + '{next_section['header']}' → '{combined_text}' (higher specificity: {combined_spec} vs {max(current_spec, next_spec)})")

                    if should_combine:
                        # Create combined section
                        combined_section = {
                            'line_number': current['line_number'],
                            'header': combined_text,
                            'header_upper': combined_text.upper(),
                            'font_size': current.get('font_size', 0),
                            'is_bold': current.get('is_bold', False) or next_section.get('is_bold', False),
                            'is_all_caps': current.get('is_all_caps', False) and next_section.get('is_all_caps', False),
                            'is_centered': current.get('is_centered', False),
                            'has_underline': current.get('has_underline', False),
                            'indent_level': current.get('indent_level', 0),
                            'confidence_signals': current.get('confidence_signals', []) + ['combined_contiguous'],
                            'confidence': max(current.get('confidence', 0), next_section.get('confidence', 0)) + 5,
                            'detection_phase': current.get('detection_phase', 1),
                            'taxonomy_id': combined_match['id'],
                            'taxonomy_canonical': combined_match['canonical'],
                            'taxonomy_specificity': combined_match['specificity_rank'],
                            'taxonomy_parent': combined_match.get('parent')
                        }

                        combined_sections.append(combined_section)
                        i += 2  # Skip both sections
                        continue

            # No combination, keep current section as-is
            combined_sections.append(current)
            i += 1

        if len(combined_sections) < len(sections):
            logger.info(f"Combined {len(sections) - len(combined_sections)} pairs of contiguous headers")

        return combined_sections

    def _match_taxonomy(self, text: str) -> Optional[Dict]:
        """Match text against CV taxonomy."""
        text_lower = text.lower().strip()
        # Replace & with 'and' before removing punctuation
        text_normalized = text_lower.replace('&', 'and')
        text_normalized = re.sub(r'[^\w\s]', ' ', text_normalized)
        text_normalized = re.sub(r'\s+', ' ', text_normalized).strip()

        matches = []

        # Try exact alias match
        if text_lower in self.taxonomy['alias_map']:
            matches.extend(self.taxonomy['alias_map'][text_lower])

        # Try normalized alias match
        if text_normalized in self.taxonomy['alias_map']:
            matches.extend(self.taxonomy['alias_map'][text_normalized])

        # Try pattern matching (weaker)
        for compiled_pattern, section in self.taxonomy['pattern_map']:
            if compiled_pattern.search(text):
                matches.append(section)

        if not matches:
            return None

        # Prefer highest specificity_rank, then priority
        best_match = max(matches, key=lambda s: (s['specificity_rank'], s['priority']))
        return best_match

    def _match_taxonomy_exact(self, text: str) -> Optional[Dict]:
        """
        Match text against taxonomy using ONLY exact aliases (no patterns).
        Used for high-precision detection in Phase 1 to avoid false positives.
        """
        text_lower = text.lower().strip()
        # Replace & with 'and' before removing punctuation
        text_normalized = text_lower.replace('&', 'and')
        text_normalized = re.sub(r'[^\w\s]', ' ', text_normalized)
        text_normalized = re.sub(r'\s+', ' ', text_normalized).strip()

        matches = []

        # Only exact alias matches (no pattern matching)
        if text_lower in self.taxonomy['alias_map']:
            matches.extend(self.taxonomy['alias_map'][text_lower])

        if text_normalized in self.taxonomy['alias_map']:
            matches.extend(self.taxonomy['alias_map'][text_normalized])

        if not matches:
            return None

        # Prefer highest specificity_rank, then priority
        best_match = max(matches, key=lambda s: (s['specificity_rank'], s['priority']))
        return best_match

    def _match_taxonomy_fuzzy(self, text: str) -> Optional[Dict]:
        """
        Fuzzy/substring match against taxonomy.
        Used as last resort for corrupted text extraction (e.g., "DUCATION" -> "education").
        Only returns high-confidence matches where text is substantial substring.
        """
        text_lower = text.lower().strip()
        text_normalized = text_lower.replace('&', 'and')
        text_normalized = re.sub(r'[^\w\s]', ' ', text_normalized)
        text_normalized = re.sub(r'\s+', ' ', text_normalized).strip()

        if len(text_normalized) < 5:
            return None

        matches = []

        # Check all aliases for substring matches
        for alias, sections in self.taxonomy['alias_map'].items():
            alias_normalized = alias.lower().strip()

            # Skip very short aliases that could cause false matches
            if len(alias_normalized) < 5:
                continue

            # Check if text is a substantial substring of alias (e.g., "ducation" in "education")
            # OR alias is a substantial substring of text
            if len(text_normalized) >= 5:
                # Calculate similarity threshold - more lenient for shorter words
                min_ratio = 0.6 if len(alias_normalized) <= 10 else 0.7

                # Text is missing beginning: "ducation" should match "education", "ddress" matches "address"
                if (alias_normalized.endswith(text_normalized) and
                    len(text_normalized) >= len(alias_normalized) * min_ratio):
                    matches.extend(sections)
                # Text contains the alias
                elif (text_normalized in alias_normalized and
                      len(text_normalized) >= len(alias_normalized) * min_ratio):
                    matches.extend(sections)
                # Alias is in text
                elif (alias_normalized in text_normalized and
                      len(alias_normalized) >= len(text_normalized) * min_ratio):
                    matches.extend(sections)

        if not matches:
            return None

        # Prefer highest specificity_rank, then priority
        best_match = max(matches, key=lambda s: (s['specificity_rank'], s['priority']))
        return best_match

    def _phase3_gap_filling(self, lines: List[Dict], existing_sections: List[Dict],
                            patterns: Dict) -> List[Dict]:
        """
        PHASE 3: Fill gaps if we don't have enough sections.
        Use weaker signals: centered text, taxonomy patterns, spacing.
        """
        # Get line numbers already detected
        detected_lines = {s['line_number'] for s in existing_sections}

        additional_sections = []
        prev_y = None

        for line in lines:
            # Skip already detected
            if line['line_number'] in detected_lines:
                prev_y = line['y_position']
                continue

            text = line['text']
            if not text or len(text) < 5 or len(text) > 100:
                prev_y = line['y_position']
                continue

            # Skip underlines
            if line.get('is_underlined', False):
                prev_y = line['y_position']
                continue

            spacing = 0
            if prev_y is not None:
                spacing = line['y_position'] - prev_y

            confidence_signals = []
            is_header = False

            # Check taxonomy match (now as detection signal)
            taxonomy_match = self._match_taxonomy(text)
            if taxonomy_match and taxonomy_match['specificity_rank'] >= 75:
                confidence_signals.append('taxonomy_pattern')
                is_header = True

            # Centered text
            if line.get('is_centered', False):
                confidence_signals.append('centered')

            # Large spacing
            if spacing > 20:
                confidence_signals.append('large_spacing')

            # ALL CAPS
            if line['is_all_caps']:
                confidence_signals.append('all_caps')

            # Bold
            if line['is_bold']:
                confidence_signals.append('bold')

            # Require 3+ signals for gap filling
            if is_header and len(confidence_signals) >= 3:
                section_data = {
                    'line_number': line['line_number'],
                    'header': text,
                    'header_upper': text.upper(),
                    'font_size': line['avg_size'],
                    'is_bold': line['is_bold'],
                    'is_all_caps': line['is_all_caps'],
                    'is_centered': line.get('is_centered', False),
                    'has_underline': line.get('has_underline_below', False),
                    'indent_level': line['indent_level'],
                    'confidence_signals': confidence_signals,
                    'confidence': len(confidence_signals),
                    'detection_phase': 3
                }

                if taxonomy_match:
                    section_data['taxonomy_id'] = taxonomy_match['id']
                    section_data['taxonomy_canonical'] = taxonomy_match['canonical']
                    section_data['taxonomy_specificity'] = taxonomy_match['specificity_rank']

                additional_sections.append(section_data)

            prev_y = line['y_position']

        return additional_sections


def extract_phased(pdf_path: str, min_section_threshold: int = 8) -> Dict:
    """Convenience function for phased extraction."""
    extractor = PhasedCVExtractor(pdf_path, min_section_threshold)
    return extractor.extract_with_phases()
