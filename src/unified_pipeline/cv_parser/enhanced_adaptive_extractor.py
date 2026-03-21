"""
Enhanced adaptive PDF extraction with:
- Underline/HR detection
- Centered text detection
- Structured taxonomy matching
- Page width analysis for centering
"""
import pdfplumber
import re
from typing import Dict, List, Tuple, Set, Optional
from pathlib import Path
import logging
from collections import Counter

# Import taxonomy
try:
    from cv_taxonomy import CV_SECTIONS, build_taxonomy_index
except ImportError:
    from cv_parser.cv_taxonomy import CV_SECTIONS, build_taxonomy_index

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EnhancedAdaptivePDFExtractor:
    """
    Enhanced adaptive extractor with:
    1. Underline/HR detection (lines of ___ or --- below headers)
    2. Centered text detection
    3. Structured taxonomy matching
    4. All previous adaptive features
    """

    def __init__(self, pdf_path: str):
        """Initialize enhanced adaptive PDF extractor."""
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

        # Build taxonomy index for fast lookups
        self.taxonomy = build_taxonomy_index()

    def extract_with_adaptation(self) -> Dict:
        """
        Extract with enhanced adaptive section detection.

        Returns:
            Dictionary with text, formatting, and detected sections
        """
        logger.info(f"Enhanced adaptive extraction from {self.pdf_path.name}")

        with pdfplumber.open(self.pdf_path) as pdf:
            all_lines = []
            line_number = 0
            page_widths = []

            for page_num, page in enumerate(pdf.pages):
                chars = page.chars
                if not chars:
                    continue

                # Track page width for centering calculation
                page_widths.append(page.width)

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

        # Calculate average page width
        avg_page_width = sum(page_widths) / len(page_widths) if page_widths else 612

        # Step 1: Detect underlines/HRs (lines following potential headers)
        self._detect_underlines(all_lines)

        # Step 2: Analyze document patterns
        doc_patterns = self._analyze_document_patterns(all_lines, avg_page_width)

        # Step 3: Detect sections using enhanced adaptive method
        sections = self._detect_sections_enhanced(all_lines, doc_patterns)

        # Build full text
        raw_text = '\n'.join([line['text'] for line in all_lines if line['text']])

        logger.info(f"Extracted {len(all_lines)} lines, detected {len(sections)} sections")

        return {
            'raw_text': raw_text,
            'lines': all_lines,
            'doc_patterns': doc_patterns,
            'sections': sections,
            'page_count': len(pdf.pages)
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
        """Analyze a line of characters with enhanced detection."""
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

        # Check for underlined text (pdfplumber doesn't always detect this well)
        # We'll detect this separately by looking for lines of underscores or dashes
        is_underlined = self._is_underline_chars(text)

        # Calculate position metrics
        x_position = chars[0]['x0']
        y_position = chars[0]['top']
        line_width = chars[-1]['x0'] + chars[-1]['width'] - chars[0]['x0']

        # Detect centered text
        # A line is "centered" if it starts near the middle of the page
        # Allow for some tolerance (within 20% of center on either side)
        center_x = page_width / 2
        line_center = x_position + (line_width / 2)
        tolerance = page_width * 0.2
        is_centered = abs(line_center - center_x) < tolerance and x_position > page_width * 0.2

        # Indent level (rough estimate in 20-pixel increments)
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

    def _detect_underlines(self, lines: List[Dict]) -> None:
        """Detect lines that have underlines/HRs immediately below them."""
        for i in range(len(lines) - 1):
            current = lines[i]
            next_line = lines[i + 1]

            # Check if next line is an underline
            if next_line.get('is_underlined') and not current.get('is_underlined'):
                # Mark current line as having an underline below it
                current['has_underline_below'] = True
            else:
                current['has_underline_below'] = False

        # Last line can't have underline below
        if lines:
            lines[-1]['has_underline_below'] = False

    def _analyze_document_patterns(self, lines: List[Dict], avg_page_width: float) -> Dict:
        """Learn patterns from the document with enhanced features."""
        text_lines = [l for l in lines if l['text'] and len(l['text']) > 3 and not l.get('is_underlined', False)]

        if not text_lines:
            return self._default_patterns()

        # Font size analysis
        sizes = [l['avg_size'] for l in text_lines]
        size_counts = Counter(round(s, 1) for s in sizes)

        # Body text = most common size
        body_size = size_counts.most_common(1)[0][0]

        # Calculate percentiles for adaptive thresholds
        sorted_sizes = sorted(sizes)
        p75_size = sorted_sizes[int(len(sorted_sizes) * 0.75)]
        p90_size = sorted_sizes[int(len(sorted_sizes) * 0.90)]

        # Header threshold: adaptive based on distribution
        size_variance = len(size_counts)
        if size_variance < 5:  # Consistent sizing
            header_threshold = body_size * 1.1  # 10% larger
        else:  # Varied sizing
            header_threshold = p75_size  # Top 25% of sizes

        # Detect numbering schemes in document
        numbering_patterns = self._detect_numbering_schemes(text_lines)

        # Detect common section words (learn from document)
        section_keywords = self._extract_section_keywords(text_lines)

        # Indentation analysis
        indents = [l['indent_level'] for l in text_lines]
        base_indent = Counter(indents).most_common(1)[0][0]

        # Centered text analysis
        centered_count = sum(1 for l in text_lines if l.get('is_centered', False))
        centered_percentage = (centered_count / len(text_lines) * 100) if text_lines else 0

        return {
            'body_size': body_size,
            'header_threshold': header_threshold,
            'p75_size': p75_size,
            'p90_size': p90_size,
            'size_distribution': dict(size_counts.most_common(10)),
            'numbering_patterns': numbering_patterns,
            'section_keywords': section_keywords,
            'base_indent': base_indent,
            'size_variance': size_variance,
            'avg_page_width': avg_page_width,
            'centered_percentage': centered_percentage
        }

    def _detect_numbering_schemes(self, lines: List[Dict]) -> List[str]:
        """Detect numbering patterns used in document."""
        patterns = []

        scheme_patterns = [
            (r'^\d+\.', 'numeric'),
            (r'^\d+\)', 'numeric_paren'),
            (r'^[a-z]\.', 'alpha_lower'),
            (r'^[A-Z]\.', 'alpha_upper'),
            (r'^[ivxlcdm]+\.', 'roman_lower'),
            (r'^[IVXLCDM]+\.', 'roman_upper'),
            (r'^\(\d+\)', 'paren_numeric'),
            (r'^\d+\.\d+', 'hierarchical'),
        ]

        for pattern, name in scheme_patterns:
            matches = sum(1 for l in lines[:100] if re.match(pattern, l['text']))
            if matches >= 3:
                patterns.append(name)

        return patterns

    def _extract_section_keywords(self, lines: List[Dict]) -> Set[str]:
        """Extract likely section keywords from the document."""
        keywords = set()

        caps_lines = [l['text'] for l in lines if l['is_all_caps'] and 5 <= len(l['text']) <= 50]

        for line in caps_lines[:50]:
            words = re.findall(r'[A-Z]{2,}', line)
            keywords.update(words)

        return keywords

    def _default_patterns(self) -> Dict:
        """Default patterns if document analysis fails."""
        return {
            'body_size': 12.0,
            'header_threshold': 13.8,
            'p75_size': 12.0,
            'p90_size': 12.0,
            'size_distribution': {},
            'numbering_patterns': [],
            'section_keywords': set(),
            'base_indent': 0,
            'size_variance': 0,
            'avg_page_width': 612,
            'centered_percentage': 0
        }

    def _match_taxonomy(self, text: str) -> Optional[Dict]:
        """
        Match text against CV taxonomy.

        Returns:
            Best matching section dict from taxonomy, or None
        """
        text_lower = text.lower().strip()
        text_normalized = re.sub(r'[^\w\s]', ' ', text_lower)
        text_normalized = re.sub(r'\s+', ' ', text_normalized).strip()

        matches = []

        # Step 1: Try exact alias match
        if text_lower in self.taxonomy['alias_map']:
            matches.extend(self.taxonomy['alias_map'][text_lower])

        # Step 2: Try normalized alias match
        if text_normalized in self.taxonomy['alias_map']:
            matches.extend(self.taxonomy['alias_map'][text_normalized])

        # Step 3: Try pattern matching
        for compiled_pattern, section in self.taxonomy['pattern_map']:
            if compiled_pattern.search(text):
                matches.append(section)

        if not matches:
            return None

        # Prefer highest specificity_rank, then priority
        best_match = max(matches, key=lambda s: (s['specificity_rank'], s['priority']))
        return best_match

    def _detect_sections_enhanced(self, lines: List[Dict], patterns: Dict) -> List[Dict]:
        """
        Enhanced section detection with:
        - Underline/HR signals
        - Centered text signals
        - Taxonomy matching
        """
        sections = []
        prev_y = None

        # Build exclusion patterns (same as before)
        exclusion_patterns = [
            r'^Funding:\s*\$',
            r'^Total:\s*\$',
            r'^\d{4}[-/]\d{2}',
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

            # Skip underline lines themselves
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
            confidence_reasons = []
            matched_taxonomy = None

            # Signal 1: Taxonomy match (high confidence!)
            taxonomy_match = self._match_taxonomy(text)
            if taxonomy_match:
                matched_taxonomy = taxonomy_match
                confidence_reasons.append('taxonomy_match')
                # Taxonomy match is strong evidence
                if taxonomy_match['specificity_rank'] >= 70:
                    is_header = True

            # Signal 2: Underline below (strong indicator)
            if line.get('has_underline_below', False):
                is_header = True
                confidence_reasons.append('underlined')

            # Signal 3: Centered text + formatting
            if line.get('is_centered', False):
                confidence_reasons.append('centered')
                if line['is_all_caps'] or line['is_bold']:
                    is_header = True

            # Signal 4: Font size (adaptive threshold)
            if line['max_size'] >= patterns['header_threshold']:
                is_header = True
                confidence_reasons.append('large_font')

            # Signal 5: Bold + caps + reasonable length
            if line['is_bold'] and line['is_all_caps'] and 5 <= len(text) <= 80:
                is_header = True
                confidence_reasons.append('bold_caps')

            # Signal 6: All caps + base indentation
            if line['is_all_caps'] and 5 <= len(text) <= 100:
                if line['indent_level'] == patterns['base_indent']:
                    is_header = True
                    confidence_reasons.append('caps_base_indent')

            # Signal 7: Document-learned keywords
            if line['is_all_caps'] and any(kw in text for kw in patterns['section_keywords']):
                confidence_reasons.append('learned_keyword')

            # Signal 8: Large spacing + formatting
            if spacing > 15:
                if line['is_all_caps'] or line['is_bold']:
                    confidence_reasons.append('spacing')

            # Signal 9: Special formatting (e.g., "SECTION (details)")
            if re.match(r'^[A-Z\s&,/]+\s*\(.*\)$', text):
                is_header = True
                confidence_reasons.append('formatted_header')

            # Confidence requirements:
            # - Taxonomy match with HIGH specificity + formatting = good
            # - Underlined + other signals = good
            # - Otherwise need 4+ signals for high precision
            if is_header:
                # Strong evidence: taxonomy + high specificity + formatting
                if ('taxonomy_match' in confidence_reasons and
                    matched_taxonomy['specificity_rank'] >= 70 and
                    ('bold_caps' in confidence_reasons or 'large_font' in confidence_reasons)):
                    pass  # Keep it
                # Underline is strong but needs support
                elif 'underlined' in confidence_reasons and len(confidence_reasons) >= 2:
                    pass  # Keep it
                # Centered text needs strong support (e.g., bold+caps or large+spacing)
                elif 'centered' in confidence_reasons and len(confidence_reasons) >= 4:
                    pass  # Centered + 3 other signals
                # Otherwise need 4+ signals (raised from 3)
                elif len(confidence_reasons) < 4:
                    is_header = False

            if is_header:
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
                    'confidence_signals': confidence_reasons,
                    'confidence': len(confidence_reasons)
                }

                # Add taxonomy match info if available
                if matched_taxonomy:
                    section_data['taxonomy_id'] = matched_taxonomy['id']
                    section_data['taxonomy_canonical'] = matched_taxonomy['canonical']
                    section_data['taxonomy_specificity'] = matched_taxonomy['specificity_rank']

                sections.append(section_data)

            prev_y = line['y_position']

        logger.info(f"Detected {len(sections)} sections (enhanced adaptive method)")
        return sections


def extract_enhanced_adaptive(pdf_path: str) -> Dict:
    """Convenience function for enhanced adaptive extraction."""
    extractor = EnhancedAdaptivePDFExtractor(pdf_path)
    return extractor.extract_with_adaptation()
