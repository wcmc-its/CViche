"""
Section classifier - Identify and classify CV sections
"""
import re
import logging
from typing import Dict, List, Optional

from unified_pipeline.llm_client import call_llm
from .config import (
    WCM_SECTIONS,
    SECTION_ALIASES,
    SECTION_KEYWORDS,
    SECTION_CLASSIFICATION_PROMPT
)
from .utils import clean_text, normalize_section_name, is_section_header

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class SectionClassifier:
    """Classify CV sections using rule-based and LLM approaches."""

    def __init__(self, use_llm: bool = True):
        """
        Initialize section classifier.

        Args:
            use_llm: Whether to use LLM for ambiguous cases
        """
        self.use_llm = use_llm

    def parse_sections(self, text: str, preliminary_sections: List[Dict] = None) -> List[Dict]:
        """
        Parse text into classified sections.

        Args:
            text: Full CV text
            preliminary_sections: Pre-detected section headers from PDF extraction

        Returns:
            List of section dictionaries with:
                - header: Section header text
                - classified_as: WCM section name
                - content: Section content
                - confidence: Classification confidence (rule-based/llm)
        """
        logger.info("Parsing sections from CV text")

        # Split text into lines
        lines = text.split('\n')

        # If no preliminary sections provided, detect them
        if not preliminary_sections:
            preliminary_sections = self._detect_section_headers(lines)

        # Extract content between headers
        sections = []
        for i, section_info in enumerate(preliminary_sections):
            header = section_info['header']
            start_line = section_info['line_number']

            # Find end line (next section or end of document)
            if i + 1 < len(preliminary_sections):
                end_line = preliminary_sections[i + 1]['line_number']
            else:
                end_line = len(lines)

            # Extract content
            content_lines = lines[start_line + 1:end_line]
            content = '\n'.join(content_lines).strip()

            # Classify section
            classified_name, confidence = self._classify_section(header, content)

            sections.append({
                'header': header,
                'classified_as': classified_name,
                'content': content,
                'confidence': confidence,
                'line_range': (start_line, end_line)
            })

        logger.info(f"Parsed {len(sections)} sections")
        return sections

    def _detect_section_headers(self, lines: List[str]) -> List[Dict]:
        """Detect section headers in lines of text."""
        headers = []

        for i, line in enumerate(lines):
            cleaned = clean_text(line)
            if cleaned and is_section_header(cleaned):
                headers.append({
                    'line_number': i,
                    'header': cleaned,
                    'header_upper': cleaned.upper()
                })

        return headers

    def _classify_section(self, header: str, content: str) -> tuple[str, str]:
        """
        Classify a section using rule-based then LLM approach.

        Args:
            header: Section header text
            content: Section content

        Returns:
            Tuple of (classified_section_name, confidence_level)
        """
        # Try rule-based classification first
        classified = self._rule_based_classification(header, content)
        if classified:
            return classified, "rule-based"

        # Fall back to LLM if enabled
        if self.use_llm:
            classified = self._llm_classification(header, content)
            if classified and classified != "UNKNOWN":
                return classified, "llm"

        # Default to unknown
        return "UNKNOWN", "none"

    def _rule_based_classification(self, header: str, content: str) -> Optional[str]:
        """
        Classify section using rules and keywords.

        Args:
            header: Section header
            content: Section content

        Returns:
            Classified section name or None
        """
        # Try exact match on header
        normalized = normalize_section_name(header, SECTION_ALIASES)
        if normalized:
            logger.debug(f"Exact match: {header} -> {normalized}")
            return normalized

        # Try keyword matching on header
        header_lower = header.lower()
        for section_type, keywords in SECTION_KEYWORDS.items():
            for keyword in keywords:
                if keyword in header_lower:
                    # Get canonical name
                    if section_type in SECTION_ALIASES:
                        canonical = SECTION_ALIASES[section_type][0]
                        logger.debug(f"Keyword match: {header} -> {canonical}")
                        return canonical

        # Try keyword matching on content (first 500 chars)
        content_sample = content[:500].lower()
        keyword_scores = {}

        for section_type, keywords in SECTION_KEYWORDS.items():
            score = sum(1 for keyword in keywords if keyword in content_sample)
            if score > 0:
                keyword_scores[section_type] = score

        # If we have a clear winner (at least 3 keyword matches)
        if keyword_scores:
            best_section = max(keyword_scores, key=keyword_scores.get)
            if keyword_scores[best_section] >= 3:
                if best_section in SECTION_ALIASES:
                    canonical = SECTION_ALIASES[best_section][0]
                    logger.debug(f"Content keyword match: {header} -> {canonical}")
                    return canonical

        return None

    def _llm_classification(self, header: str, content: str) -> Optional[str]:
        """
        Classify section using LLM.

        Args:
            header: Section header
            content: Section content (will be truncated if too long)

        Returns:
            Classified section name or None
        """
        # Truncate content if too long
        max_content_length = 1000
        content_sample = content[:max_content_length]
        if len(content) > max_content_length:
            content_sample += "..."

        # Create section text
        section_text = f"Header: {header}\n\nContent:\n{content_sample}"

        # Format prompt
        section_list = "\n".join([f"- {section}" for section in WCM_SECTIONS])
        prompt = SECTION_CLASSIFICATION_PROMPT.format(
            section_list=section_list,
            section_text=section_text
        )

        try:
            llm_result = call_llm(
                stage="cv_parser_classifier",
                messages=[
                    {"role": "system", "content": "You are a CV section classifier. Respond with only the section name."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0,
                max_tokens=50
            )

            result = llm_result["content"].strip()

            # Validate result is in WCM_SECTIONS
            if result in WCM_SECTIONS:
                logger.debug(f"LLM classification: {header} -> {result}")
                return result
            else:
                logger.warning(f"LLM returned invalid section: {result}")
                return None

        except Exception as e:
            logger.error(f"LLM classification failed: {e}")
            return None

    def merge_sections(self, sections: List[Dict]) -> Dict[str, List[Dict]]:
        """
        Merge parsed sections by their classified type.

        Args:
            sections: List of parsed section dictionaries

        Returns:
            Dictionary mapping WCM section names to lists of content
        """
        merged = {}

        for section in sections:
            classified_as = section['classified_as']

            if classified_as not in merged:
                merged[classified_as] = []

            merged[classified_as].append({
                'original_header': section['header'],
                'content': section['content'],
                'confidence': section['confidence']
            })

        logger.info(f"Merged into {len(merged)} distinct section types")
        return merged

    def get_section_mapping_report(self, sections: List[Dict]) -> str:
        """
        Generate a report of section mappings.

        Args:
            sections: List of parsed sections

        Returns:
            Formatted report string
        """
        report_lines = ["=== Section Classification Report ===\n"]

        for section in sections:
            header = section['header']
            classified = section['classified_as']
            confidence = section['confidence']
            content_preview = section['content'][:100].replace('\n', ' ')

            report_lines.append(
                f"'{header}' -> '{classified}' [{confidence}]\n"
                f"  Preview: {content_preview}...\n"
            )

        # Summary statistics
        total = len(sections)
        classified = sum(1 for s in sections if s['classified_as'] != 'UNKNOWN')
        unknown = total - classified

        report_lines.append(f"\nSummary: {classified}/{total} classified, {unknown} unknown")

        return '\n'.join(report_lines)
