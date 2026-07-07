"""
Data structurer - Extract and structure entities from CV sections
"""
import json
import logging
from typing import Dict, List, Optional, Any

from unified_pipeline.llm_client import call_llm
from .config import ENTITY_EXTRACTION_PROMPT
from .utils import extract_dates, extract_email, is_likely_publication

logger = logging.getLogger(__name__)


class DataStructurer:
    """Extract structured data from classified CV sections."""

    def __init__(self, use_llm: bool = True):
        """
        Initialize data structurer.

        Args:
            use_llm: Whether to use LLM for entity extraction
        """
        self.use_llm = use_llm

    def structure_sections(self, merged_sections: Dict[str, List[Dict]]) -> Dict[str, Any]:
        """
        Extract structured data from all sections.

        Args:
            merged_sections: Dictionary of section types to content

        Returns:
            Dictionary of structured data organized by section
        """
        logger.info("Structuring data from classified sections")

        structured_data = {}

        for section_type, section_contents in merged_sections.items():
            logger.info(f"Processing section: {section_type}")

            # Combine all content for this section type
            combined_content = "\n\n".join([s['content'] for s in section_contents])

            # Structure based on section type
            if section_type == "PERSONAL DATA":
                structured_data[section_type] = self._structure_personal_data(combined_content)
            elif section_type == "EDUCATION":
                structured_data[section_type] = self._structure_education(combined_content)
            elif section_type in ["PROFESSIONAL POSITIONS & EMPLOYMENT", "POSTDOCTORAL TRAINING"]:
                structured_data[section_type] = self._structure_positions(combined_content, section_type)
            elif section_type == "HONORS, AWARDS":
                structured_data[section_type] = self._structure_honors(combined_content)
            elif section_type == "BIBLIOGRAPHY":
                structured_data[section_type] = self._structure_publications(combined_content)
            else:
                # Generic structuring for other sections
                structured_data[section_type] = self._structure_generic(combined_content, section_type)

        logger.info(f"Structured {len(structured_data)} sections")
        return structured_data

    def _structure_personal_data(self, content: str) -> Dict:
        """Structure personal data section."""
        data = {
            'name': None,
            'office_address': None,
            'office_phone': None,
            'work_email': None,
            'home_address': None,
            'cell_phone': None,
            'personal_email': None,
            'visa_status': None
        }

        # Extract email addresses
        email = extract_email(content)
        if email:
            # Guess if work or personal based on domain
            if any(domain in email.lower() for domain in ['edu', 'cornell', 'weill', 'hospital']):
                data['work_email'] = email
            else:
                data['personal_email'] = email

        # Use LLM for more complex extraction
        if self.use_llm:
            llm_data = self._llm_extract_entities("PERSONAL DATA", content)
            if llm_data:
                data.update(llm_data)

        return data

    def _structure_education(self, content: str) -> List[Dict]:
        """Structure education section."""
        if self.use_llm:
            llm_data = self._llm_extract_entities("EDUCATION", content)
            if llm_data and isinstance(llm_data, list):
                return llm_data

        # Fallback: basic parsing
        entries = []
        dates = extract_dates(content)

        # Simple heuristic: each paragraph is an entry
        for paragraph in content.split('\n\n'):
            if paragraph.strip():
                entries.append({
                    'raw_text': paragraph.strip(),
                    'dates': extract_dates(paragraph),
                    'degree': None,
                    'institution': None,
                    'location': None
                })

        return entries

    def _structure_positions(self, content: str, section_type: str) -> List[Dict]:
        """Structure positions/employment section."""
        if self.use_llm:
            llm_data = self._llm_extract_entities(section_type, content)
            if llm_data and isinstance(llm_data, list):
                return llm_data

        # Fallback: basic parsing
        entries = []
        for paragraph in content.split('\n\n'):
            if paragraph.strip():
                entries.append({
                    'raw_text': paragraph.strip(),
                    'dates': extract_dates(paragraph),
                    'title': None,
                    'institution': None,
                    'location': None
                })

        return entries

    def _structure_honors(self, content: str) -> List[Dict]:
        """Structure honors and awards section."""
        if self.use_llm:
            llm_data = self._llm_extract_entities("HONORS, AWARDS", content)
            if llm_data and isinstance(llm_data, list):
                return llm_data

        # Fallback
        entries = []
        for line in content.split('\n'):
            line = line.strip()
            if line and len(line) > 10:  # Skip very short lines
                entries.append({
                    'raw_text': line,
                    'dates': extract_dates(line),
                    'award_name': None,
                    'organization': None
                })

        return entries

    def _structure_publications(self, content: str) -> List[Dict]:
        """Structure publications/bibliography section."""
        entries = []

        # Publications are typically one per line or paragraph
        # Look for publication indicators
        lines = content.split('\n')
        current_pub = []

        for line in lines:
            line = line.strip()

            # Check if this looks like a new publication
            if is_likely_publication(line) and current_pub:
                # Save previous publication
                pub_text = ' '.join(current_pub)
                entries.append(self._parse_publication(pub_text))
                current_pub = [line]
            elif line:
                current_pub.append(line)

        # Don't forget the last one
        if current_pub:
            pub_text = ' '.join(current_pub)
            entries.append(self._parse_publication(pub_text))

        # Use LLM to enhance publication data if needed
        if self.use_llm and len(entries) > 0 and len(entries) < 50:  # Only for reasonable numbers
            # Process in batches
            for i, entry in enumerate(entries[:10]):  # Limit to first 10 for cost
                enhanced = self._llm_extract_publication(entry['raw_text'])
                if enhanced:
                    entries[i].update(enhanced)

        return entries

    def _parse_publication(self, text: str) -> Dict:
        """Basic publication parsing."""
        return {
            'raw_text': text,
            'authors': None,
            'title': None,
            'journal': None,
            'year': None,
            'doi': None,
            'pmid': None,
            'dates': extract_dates(text)
        }

    def _structure_generic(self, content: str, section_type: str) -> Dict:
        """Generic structuring for miscellaneous sections."""
        data = {
            'raw_content': content,
            'entries': []
        }

        # Split into entries (paragraphs or lines)
        if '\n\n' in content:
            entries = content.split('\n\n')
        else:
            entries = [line for line in content.split('\n') if line.strip()]

        for entry in entries:
            if entry.strip():
                data['entries'].append({
                    'text': entry.strip(),
                    'dates': extract_dates(entry)
                })

        return data

    def _llm_extract_entities(self, section_type: str, content: str) -> Optional[Any]:
        """
        Use LLM to extract structured entities.

        Args:
            section_type: Type of section
            content: Section content

        Returns:
            Extracted entities as dict or list
        """
        # Truncate very long content
        max_length = 3000
        if len(content) > max_length:
            content = content[:max_length] + "..."

        prompt = ENTITY_EXTRACTION_PROMPT.format(
            section_type=section_type,
            section_text=content
        )

        try:
            llm_result = call_llm(
                stage="cv_parser_structurer",
                messages=[
                    {"role": "system", "content": "You are a CV data extraction assistant. Extract structured data and return ONLY valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0,
                max_tokens=1500
            )

            result_text = llm_result["content"].strip()

            # Try to parse as JSON
            # Remove markdown code blocks if present
            if result_text.startswith('```'):
                result_text = result_text.split('```')[1]
                if result_text.startswith('json'):
                    result_text = result_text[4:]
                result_text = result_text.strip()

            data = json.loads(result_text)
            logger.debug(f"LLM extracted entities for {section_type}")
            return data

        except json.JSONDecodeError as e:
            logger.warning(f"LLM returned invalid JSON for {section_type}: {e}")
            return None
        except Exception as e:
            logger.error(f"LLM extraction failed for {section_type}: {e}")
            return None

    def _llm_extract_publication(self, pub_text: str) -> Optional[Dict]:
        """Extract structured publication data using LLM."""
        prompt = f"""Extract publication information from this citation:

{pub_text}

Return JSON with these fields (use null if not found):
- authors: list of author names
- title: article title
- journal: journal name
- year: publication year
- volume: volume number
- issue: issue number
- pages: page range
- doi: DOI if present
- pmid: PubMed ID if present

Return ONLY valid JSON.
"""

        try:
            llm_result = call_llm(
                stage="cv_parser_structurer",
                messages=[
                    {"role": "system", "content": "You are a publication citation parser. Return ONLY valid JSON."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0,
                max_tokens=500
            )

            result_text = llm_result["content"].strip()

            # Clean markdown
            if result_text.startswith('```'):
                result_text = result_text.split('```')[1]
                if result_text.startswith('json'):
                    result_text = result_text[4:]
                result_text = result_text.strip()

            data = json.loads(result_text)
            return data

        except Exception as e:
            logger.warning(f"Failed to parse publication: {e}")
            return None
