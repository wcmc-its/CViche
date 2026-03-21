"""
Enhanced PDF extraction with font and layout analysis
"""
import pdfplumber
import re
from typing import Dict, List, Tuple
from pathlib import Path
import logging
from collections import Counter

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class EnhancedPDFExtractor:
    """Extract text with font size, style, and layout analysis for better section detection."""

    def __init__(self, pdf_path: str):
        """Initialize enhanced PDF extractor."""
        self.pdf_path = Path(pdf_path)
        if not self.pdf_path.exists():
            raise FileNotFoundError(f"PDF not found: {pdf_path}")

    def extract_with_formatting(self) -> Dict:
        """
        Extract text with font and formatting information.

        Returns:
            Dictionary with text, formatting, and detected sections
        """
        logger.info(f"Extracting with formatting from {self.pdf_path.name}")

        with pdfplumber.open(self.pdf_path) as pdf:
            all_lines = []
            line_number = 0

            for page_num, page in enumerate(pdf.pages):
                # Get chars with font info
                chars = page.chars

                if not chars:
                    continue

                # Group chars into lines based on y-coordinate
                lines_on_page = self._group_chars_into_lines(chars)

                for line_data in lines_on_page:
                    all_lines.append({
                        'line_number': line_number,
                        'page': page_num + 1,
                        'text': line_data['text'],
                        'avg_size': line_data['avg_size'],
                        'max_size': line_data['max_size'],
                        'is_bold': line_data['is_bold'],
                        'is_all_caps': line_data['text'].isupper() if line_data['text'] else False,
                        'y_position': line_data['y_position']
                    })
                    line_number += 1

        # Analyze font sizes to detect headers
        font_stats = self._analyze_font_sizes(all_lines)

        # Detect sections using multiple signals
        sections = self._detect_sections_enhanced(all_lines, font_stats)

        # Build full text
        raw_text = '\n'.join([line['text'] for line in all_lines if line['text']])

        logger.info(f"Extracted {len(all_lines)} lines, detected {len(sections)} sections")

        return {
            'raw_text': raw_text,
            'lines': all_lines,
            'font_stats': font_stats,
            'sections': sections,
            'page_count': len(pdf.pages)
        }

    def _group_chars_into_lines(self, chars: List[Dict]) -> List[Dict]:
        """Group characters into lines based on y-coordinate."""
        if not chars:
            return []

        # Sort by y position (top to bottom)
        sorted_chars = sorted(chars, key=lambda c: c['top'])

        lines = []
        current_line = []
        current_y = None
        y_tolerance = 2  # Pixels tolerance for same line

        for char in sorted_chars:
            if current_y is None or abs(char['top'] - current_y) <= y_tolerance:
                current_line.append(char)
                current_y = char['top']
            else:
                # Save previous line
                if current_line:
                    lines.append(self._analyze_line(current_line))
                # Start new line
                current_line = [char]
                current_y = char['top']

        # Don't forget last line
        if current_line:
            lines.append(self._analyze_line(current_line))

        return lines

    def _analyze_line(self, chars: List[Dict]) -> Dict:
        """Analyze a line of characters."""
        if not chars:
            return {'text': '', 'avg_size': 0, 'max_size': 0, 'is_bold': False, 'y_position': 0}

        # Sort chars by x position (left to right)
        chars = sorted(chars, key=lambda c: c['x0'])

        text = ''.join([c['text'] for c in chars])
        sizes = [c.get('size', 10) for c in chars]
        avg_size = sum(sizes) / len(sizes) if sizes else 10
        max_size = max(sizes) if sizes else 10

        # Check if bold (look for Bold, Heavy, Black in font name)
        bold_indicators = ['Bold', 'Heavy', 'Black', 'Semibold']
        font_names = [c.get('fontname', '') for c in chars]
        is_bold = any(any(ind in fn for ind in bold_indicators) for fn in font_names if fn)

        y_position = chars[0]['top']

        return {
            'text': text.strip(),
            'avg_size': avg_size,
            'max_size': max_size,
            'is_bold': is_bold,
            'y_position': y_position
        }

    def _analyze_font_sizes(self, lines: List[Dict]) -> Dict:
        """Analyze font size distribution to identify header sizes."""
        sizes = [line['avg_size'] for line in lines if line['text']]

        if not sizes:
            return {'body_size': 10, 'header_threshold': 12}

        # Find most common size (likely body text)
        size_counts = Counter(round(s, 1) for s in sizes)
        body_size = size_counts.most_common(1)[0][0]

        # Header threshold: anything significantly larger than body
        header_threshold = body_size * 1.15  # 15% larger

        return {
            'body_size': body_size,
            'header_threshold': header_threshold,
            'size_distribution': dict(size_counts.most_common(10))
        }

    def _detect_sections_enhanced(self, lines: List[Dict], font_stats: Dict) -> List[Dict]:
        """
        Detect sections using multiple signals:
        - Font size (larger than body text)
        - Bold text
        - All caps
        - Known patterns (PUBLICATIONS, EDUCATION, etc.)
        - Spacing (significant gap before line)
        """
        sections = []
        header_threshold = font_stats['header_threshold']
        prev_y = None

        # Known section patterns (must be all caps for section headers)
        section_patterns = [
            r'^(?:PUBLICATIONS?|BIBLIOGRAPHY)',
            r'^(?:EDUCATION|ACADEMIC BACKGROUND)',
            r'^(?:EXPERIENCE|EMPLOYMENT|APPOINTMENTS?|POSITIONS?)',
            r'^(?:GRANT\s+FUNDING|RESEARCH\s+SUPPORT)',  # More specific
            r'^(?:HONORS?|AWARDS?|RECOGNITION)',
            r'^(?:TRAINING|FELLOWSHIP|RESIDENCY)',
            r'^(?:CERTIFICATIONS?|LICENSURE)',
            r'^(?:SKILLS?|EXPERTISE)',
            r'^(?:PRESENTATIONS?|TALKS?|SPEAKING)',
            r'^(?:TEACHING|EDUCATIONAL)',
            r'^(?:SERVICE|LEADERSHIP|ADMINISTRATIVE)',
            r'^(?:MEMBERSHIPS?|PROFESSIONAL ORGANIZATIONS)',
        ]

        for line in lines:
            text = line['text']
            if not text or len(text) < 3:
                prev_y = line['y_position']
                continue

            # Calculate spacing from previous line
            spacing = 0
            if prev_y is not None:
                spacing = line['y_position'] - prev_y

            is_header = False
            confidence_reasons = []

            # Exclusion patterns (lines that look like headers but aren't)
            exclusion_patterns = [
                r'^Funding:\s*\$',  # Funding amounts
                r'^\d+\.\s+',  # Numbered items (publications)
            ]

            # Check exclusions first
            is_excluded = any(re.match(pat, text) for pat in exclusion_patterns)
            if is_excluded:
                prev_y = line['y_position']
                continue

            # Signal 1: Matches known patterns
            for pattern in section_patterns:
                if re.match(pattern, text, re.IGNORECASE):
                    is_header = True
                    confidence_reasons.append('pattern_match')
                    break

            # Signal 2: Font size
            if line['max_size'] >= header_threshold:
                is_header = True
                confidence_reasons.append('large_font')

            # Signal 3: Bold text (only if also all caps or very short)
            if line['is_bold']:
                # Bold alone isn't enough, but combined with other signals...
                if line['is_all_caps'] and 5 <= len(text) <= 80:
                    confidence_reasons.append('bold')
                    is_header = True

            # Signal 4: All caps + reasonable length
            if line['is_all_caps'] and 5 <= len(text) <= 100:
                is_header = True
                confidence_reasons.append('all_caps')

            # Signal 5: Large spacing before (likely section break)
            if spacing > 15:  # Significant vertical gap
                if line['is_all_caps'] and 5 <= len(text) <= 100:
                    confidence_reasons.append('spacing')
                    # Don't auto-mark as header, just add confidence

            # Signal 6: Special formatting patterns
            # e.g., "PUBLICATIONS (h-index: 73)"
            if re.match(r'^[A-Z\s&,]+\s*\(.*\)$', text):
                is_header = True
                confidence_reasons.append('formatted_header')

            # Require at least 2 confidence signals (except for pattern_match which is strong)
            if is_header and 'pattern_match' not in confidence_reasons and len(confidence_reasons) < 2:
                is_header = False

            if is_header:
                sections.append({
                    'line_number': line['line_number'],
                    'header': text,
                    'header_upper': text.upper(),
                    'font_size': line['avg_size'],
                    'is_bold': line['is_bold'],
                    'is_all_caps': line['is_all_caps'],
                    'confidence_signals': confidence_reasons,
                    'confidence': len(confidence_reasons)
                })

            prev_y = line['y_position']

        logger.info(f"Detected {len(sections)} section headers using enhanced analysis")

        # Log some examples
        for section in sections[:10]:
            logger.debug(f"  {section['header'][:60]} - signals: {section['confidence_signals']}")

        return sections

    def extract_publications_smart(self, raw_text: str) -> List[str]:
        """
        Extract publications using PMID as boundary marker.

        Publications typically end with "PMID: XXXXXXXX"
        """
        publications = []

        # Split by PMID markers
        pmid_pattern = r'PMID:\s*\d+'
        parts = re.split(pmid_pattern, raw_text)

        # Rejoin with PMID markers
        pmid_matches = re.findall(pmid_pattern, raw_text)

        current_pub = ""
        for i, part in enumerate(parts[1:], 0):  # Skip first part (before any PMID)
            if i < len(pmid_matches):
                # This part ends with a PMID
                pub_text = (current_pub + part).strip()
                if pub_text:
                    publications.append(pub_text + " " + pmid_matches[i])
                current_pub = ""

        return publications


def extract_with_formatting(pdf_path: str) -> Dict:
    """Convenience function for enhanced extraction."""
    extractor = EnhancedPDFExtractor(pdf_path)
    return extractor.extract_with_formatting()
