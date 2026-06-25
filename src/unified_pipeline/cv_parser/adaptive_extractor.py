"""
Adaptive PDF extraction that learns from document structure
Optimized for arbitrary CV formats
"""
import pdfplumber
import re
from typing import Dict, List, Tuple, Set
from pathlib import Path
import logging
from collections import Counter

logger = logging.getLogger(__name__)


class AdaptivePDFExtractor:
    """
    Adaptive extractor that learns document patterns.

    Key optimizations for arbitrary CVs:
    1. Auto-detect body text font size (not hardcoded)
    2. Adaptive threshold based on document's font distribution
    3. Context-aware pattern matching (learns from document)
    4. Whitespace/indentation analysis
    5. Numeric pattern detection (publication numbering schemes)
    """

    def __init__(self, pdf_path: str):
        """Initialize adaptive PDF extractor."""
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

    def extract_with_adaptation(self) -> Dict:
        """
        Extract with adaptive section detection.

        Returns:
            Dictionary with text, formatting, and detected sections
        """
        logger.info(f"Adaptive extraction from {self.pdf_path.name}")

        with pdfplumber.open(self.pdf_path) as pdf:
            all_lines = []
            line_number = 0

            for page_num, page in enumerate(pdf.pages):
                chars = page.chars
                if not chars:
                    continue

                lines_on_page = self._group_chars_into_lines(chars)

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
                        'y_position': line_data['y_position'],
                        'x_position': line_data['x_position'],
                        'indent_level': line_data['indent_level']
                    })
                    line_number += 1

        # Step 1: Analyze document patterns
        doc_patterns = self._analyze_document_patterns(all_lines)

        # Step 2: Detect sections using learned patterns
        sections = self._detect_sections_adaptive(all_lines, doc_patterns)

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

    def _group_chars_into_lines(self, chars: List[Dict]) -> List[Dict]:
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
                    lines.append(self._analyze_line(current_line))
                current_line = [char]
                current_y = char['top']

        if current_line:
            lines.append(self._analyze_line(current_line))

        return lines

    def _analyze_line(self, chars: List[Dict]) -> Dict:
        """Analyze a line of characters."""
        if not chars:
            return {
                'text': '', 'avg_size': 0, 'max_size': 0,
                'is_bold': False, 'is_italic': False,
                'y_position': 0, 'x_position': 0, 'indent_level': 0
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

        # Calculate indentation
        x_position = chars[0]['x0']
        y_position = chars[0]['top']

        # Indent level (rough estimate in 20-pixel increments)
        indent_level = int(x_position / 20)

        return {
            'text': text.strip(),
            'avg_size': avg_size,
            'max_size': max_size,
            'is_bold': is_bold,
            'is_italic': is_italic,
            'y_position': y_position,
            'x_position': x_position,
            'indent_level': indent_level
        }

    def _analyze_document_patterns(self, lines: List[Dict]) -> Dict:
        """
        Learn patterns from the document itself.

        Adaptive features:
        - Detect most common font size (body text)
        - Identify outlier sizes (likely headers)
        - Find numbering schemes (1., a., i., etc.)
        - Detect indentation patterns
        - Identify capitalization patterns
        """
        text_lines = [l for l in lines if l['text'] and len(l['text']) > 3]

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
        # If most text is one size, headers are noticeably larger
        # If sizes vary, use more conservative threshold
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

        return {
            'body_size': body_size,
            'header_threshold': header_threshold,
            'p75_size': p75_size,
            'p90_size': p90_size,
            'size_distribution': dict(size_counts.most_common(10)),
            'numbering_patterns': numbering_patterns,
            'section_keywords': section_keywords,
            'base_indent': base_indent,
            'size_variance': size_variance
        }

    def _detect_numbering_schemes(self, lines: List[Dict]) -> List[str]:
        """Detect numbering patterns used in document."""
        patterns = []

        # Check for common patterns
        scheme_patterns = [
            (r'^\d+\.', 'numeric'),  # 1. 2. 3.
            (r'^\d+\)', 'numeric_paren'),  # 1) 2) 3)
            (r'^[a-z]\.', 'alpha_lower'),  # a. b. c.
            (r'^[A-Z]\.', 'alpha_upper'),  # A. B. C.
            (r'^[ivxlcdm]+\.', 'roman_lower'),  # i. ii. iii.
            (r'^[IVXLCDM]+\.', 'roman_upper'),  # I. II. III.
            (r'^\(\d+\)', 'paren_numeric'),  # (1) (2) (3)
            (r'^\d+\.\d+', 'hierarchical'),  # 1.1 1.2 2.1
        ]

        for pattern, name in scheme_patterns:
            matches = sum(1 for l in lines[:100] if re.match(pattern, l['text']))
            if matches >= 3:  # Found at least 3 instances
                patterns.append(name)

        return patterns

    def _extract_section_keywords(self, lines: List[Dict]) -> Set[str]:
        """Extract likely section keywords from the document."""
        keywords = set()

        # Look for all-caps lines (potential sections)
        caps_lines = [l['text'] for l in lines if l['is_all_caps'] and 5 <= len(l['text']) <= 50]

        for line in caps_lines[:50]:  # First 50 caps lines
            # Extract main words (ignore dates, numbers)
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
            'size_variance': 0
        }

    def _detect_sections_adaptive(self, lines: List[Dict], patterns: Dict) -> List[Dict]:
        """
        Adaptive section detection using learned patterns.
        """
        sections = []
        prev_y = None

        # Universal section patterns (works across CVs)
        universal_patterns = [
            r'^(?:PUBLICATIONS?|BIBLIOGRAPHY)',
            r'^(?:EDUCATION|ACADEMIC|DEGREES?)',
            r'^(?:EXPERIENCE|EMPLOYMENT|APPOINTMENTS?|POSITIONS?|WORK\s+HISTORY)',
            r'^(?:GRANTS?|FUNDING|RESEARCH\s+SUPPORT)',
            r'^(?:HONORS?|AWARDS?|RECOGNITION|DISTINCTIONS?)',
            r'^(?:TRAINING|FELLOWSHIP|RESIDENCY|CERTIFICATIONS?)',
            r'^(?:SKILLS?|EXPERTISE|COMPETENCIES)',
            r'^(?:PRESENTATIONS?|TALKS?|SPEAKING)',
            r'^(?:TEACHING|EDUCATIONAL|INSTRUCTION)',
            r'^(?:SERVICE|LEADERSHIP|ADMINISTRATIVE)',
            r'^(?:MEMBERSHIPS?|AFFILIATIONS?|PROFESSIONAL\s+ORGANIZATIONS)',
            r'^(?:REFERENCES?)',
            r'^(?:SUMMARY|PROFILE|OBJECTIVE)',
            r'^(?:CONTACT|PERSONAL\s+(?:DATA|INFORMATION))',
        ]

        # Exclusion patterns
        exclusion_patterns = [
            r'^Funding:\s*\$',
            r'^Total:\s*\$',
            r'^\d{4}[-/]\d{2}',  # Dates
            r'^Page\s+\d+',  # Page numbers
            r'PMID:\s*\d+',  # PubMed IDs (anywhere in line)
            r'^\d+\.\s*PMID',  # Numbered PMID
            r'^PMID\s+\d+',  # Standalone PMID lines
            r'^\d{4}\s+PMID:',  # Year PMID: format (e.g., "2019 PMID: 31723049")
            r'^\d+\s+\d{4}\.\s+PMID:',  # Page/vol year. PMID: format
            r'^:\d+-\d+\.\s+PMID:',  # Page range. PMID: format (e.g., ":145-151. PMID: 27932607")
            r'^[A-Z]?\d+:\d+-\d+\s+\d{4}\.\s+PMID:',  # Vol:pages year. PMID: format
            r'^\d+\([A-Z0-9]+\):',  # Vol(issue): at start (catches publication citations like "18(6A):")
            r'^\d{4}\s+\d+\([A-Z0-9]+\):\d+-\d+\.',  # Year vol(issue):pages. format
            r'^PMC\d+',  # PubMed Central IDs
            r'PMCID:\s*PMC',  # Full PMCID format
            r'^DOI:',  # DOI identifiers
            r'^ISBN:',  # Book identifiers
            r'^\d{1,4};',  # Year; or vol; (publication format)
            r'^\d+:\d+-\d+',  # Page ranges (123:45-67)
            r'^[A-Z]{2,5}\s+[A-Z0-9-]+\s+\d{4}-\d{4}$',  # Grant IDs: "NIH R01NS123 2020-2024"
        ]

        # Add document-specific numbering patterns to exclusions
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

            # Signal 1: Universal patterns
            for pattern in universal_patterns:
                if re.match(pattern, text, re.IGNORECASE):
                    is_header = True
                    confidence_reasons.append('universal_pattern')
                    break

            # Signal 2: Document-learned keywords
            if line['is_all_caps'] and any(kw in text for kw in patterns['section_keywords']):
                confidence_reasons.append('learned_keyword')

            # Signal 3: Font size (adaptive threshold)
            if line['max_size'] >= patterns['header_threshold']:
                is_header = True
                confidence_reasons.append('large_font')

            # Signal 4: Bold + caps + reasonable length
            if line['is_bold'] and line['is_all_caps'] and 5 <= len(text) <= 80:
                is_header = True
                confidence_reasons.append('bold_caps')

            # Signal 5: All caps + no indentation
            if line['is_all_caps'] and 5 <= len(text) <= 100:
                if line['indent_level'] == patterns['base_indent']:
                    is_header = True
                    confidence_reasons.append('caps_base_indent')

            # Signal 6: Large spacing + formatting
            if spacing > 15:
                if line['is_all_caps'] or line['is_bold']:
                    confidence_reasons.append('spacing')

            # Signal 7: Special formatting
            if re.match(r'^[A-Z\s&,/]+\s*\(.*\)$', text):
                is_header = True
                confidence_reasons.append('formatted_header')

            # Require minimum confidence (except for universal patterns)
            # Universal patterns = strong signal, can stand alone
            # Everything else needs 3+ signals for high precision
            if is_header:
                if 'universal_pattern' in confidence_reasons:
                    # Universal pattern can have low confidence, but benefit from others
                    pass  # Keep it
                elif len(confidence_reasons) < 3:
                    # Need at least 3 signals for non-universal patterns
                    is_header = False

            if is_header:
                sections.append({
                    'line_number': line['line_number'],
                    'header': text,
                    'header_upper': text.upper(),
                    'font_size': line['avg_size'],
                    'is_bold': line['is_bold'],
                    'is_all_caps': line['is_all_caps'],
                    'indent_level': line['indent_level'],
                    'confidence_signals': confidence_reasons,
                    'confidence': len(confidence_reasons)
                })

            prev_y = line['y_position']

        logger.info(f"Detected {len(sections)} sections (adaptive method)")
        return sections


def extract_adaptive(pdf_path: str) -> Dict:
    """Convenience function for adaptive extraction."""
    extractor = AdaptivePDFExtractor(pdf_path)
    return extractor.extract_with_adaptation()
