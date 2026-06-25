"""
Template mapper - Map structured CV data to Word template
"""
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from docx import Document
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

logger = logging.getLogger(__name__)


class TemplateMapper:
    """Map structured CV data to WCM Word template."""

    def __init__(self, template_path: str):
        """
        Initialize template mapper.

        Args:
            template_path: Path to WCM Word template
        """
        self.template_path = Path(template_path)
        if not self.template_path.exists():
            raise FileNotFoundError(f"Template not found: {template_path}")

        # Load template
        self.doc = Document(str(self.template_path))
        logger.info(f"Loaded template from {self.template_path.name}")

    def map_data(self, structured_data: Dict[str, Any]) -> None:
        """
        Map structured CV data to template.

        Args:
            structured_data: Dictionary of structured CV data by section
                            Keys can be lowercase (from parsed data) or uppercase (from segmented data)
        """
        logger.info("Mapping structured data to template")

        # Map each section - handle both lowercase (parsed) and uppercase (segmented) keys
        for section_type, data in structured_data.items():
            logger.info(f"Mapping section: {section_type} ({len(data) if isinstance(data, list) else 'N/A'} items)")

            # Normalize section type to lowercase for consistent handling
            section_lower = section_type.lower()

            if section_lower == "personal data" or section_type == "PERSONAL DATA":
                self._map_personal_data(data)
            elif section_lower == "education" or section_type == "EDUCATION":
                self._map_education(data)
            elif section_lower == "positions" or "position" in section_lower or section_type == "PROFESSIONAL POSITIONS & EMPLOYMENT":
                self._map_positions(data)
            elif section_lower == "training" or section_type == "POSTDOCTORAL TRAINING":
                self._map_training(data)
            elif section_lower == "honors" or "award" in section_lower or section_type == "HONORS, AWARDS":
                self._map_honors(data)
            elif section_lower == "publications" or section_type == "BIBLIOGRAPHY":
                self._map_publications(data)
            elif section_lower == "grants" or "grant" in section_lower:
                self._map_grants(data)
            else:
                # Generic mapping for other sections
                self._map_generic_section(section_type, data)

    def _find_section_in_template(self, section_name: str) -> Optional[int]:
        """
        Find the paragraph index where a section starts in the template.

        Args:
            section_name: Name of section to find

        Returns:
            Paragraph index or None
        """
        for i, para in enumerate(self.doc.paragraphs):
            text = para.text.strip().upper()
            if section_name.upper() in text:
                return i
        return None

    def _map_personal_data(self, data: Dict) -> None:
        """Map personal data to template."""
        # Find placeholders in template and replace
        replacements = {
            'Name:': data.get('name', 'N/A'),
            'Office address:': data.get('office_address', 'N/A'),
            'Office telephone:': data.get('office_phone', 'N/A'),
            'Work email:': data.get('work_email', 'N/A'),
            'Home address:': data.get('home_address', 'N/A'),
            'Cell phone:': data.get('cell_phone', 'N/A'),
            'Personal email:': data.get('personal_email', 'N/A'),
        }

        for para in self.doc.paragraphs:
            for placeholder, value in replacements.items():
                if placeholder in para.text and value:
                    para.text = para.text.replace(placeholder, f"{placeholder} {value}")

    def _map_education(self, entries: List[Dict]) -> None:
        """Map education entries to template."""
        section_idx = self._find_section_in_template("EDUCATION")
        if section_idx is None:
            logger.warning("Education section not found in template")
            return

        # Find the table after the education section
        # In WCM template, education is typically in a table
        table = self._find_table_after_paragraph(section_idx)

        if table and len(entries) > 0:
            logger.info(f"Adding {len(entries)} education entries to table")
            # Skip header row, add data rows
            for entry in entries:
                row = table.add_row()
                cells = row.cells

                # Map to table columns: Degree, field | Institution, city state | Dates attended | Year Awarded
                if len(cells) >= 4:
                    # Column 0: Degree and field
                    degree = entry.get('degree', '')
                    field = entry.get('major_field', '')
                    if degree and field and field != 'N/A':
                        cells[0].text = f"{degree} in {field}"
                    else:
                        cells[0].text = degree

                    # Column 1: Institution, city state
                    institution = entry.get('institution', '')
                    location = entry.get('location', '')
                    if institution and location:
                        cells[1].text = f"{institution}, {location}"
                    else:
                        cells[1].text = institution

                    # Column 2: Dates attended (mm/yyyy-mm/yyyy format)
                    start_year = entry.get('start_year', 0)
                    end_year = entry.get('end_year', 0)
                    if start_year and end_year:
                        cells[2].text = f"{start_year} - {end_year}"
                    elif start_year:
                        cells[2].text = str(start_year)
                    else:
                        cells[2].text = ''

                    # Column 3: Year Awarded
                    if end_year:
                        cells[3].text = str(end_year)
                    else:
                        cells[3].text = ''
        else:
            if not table:
                logger.warning("No table found after education section")
            if not entries:
                logger.info("No education entries to map")

    def _map_positions(self, entries: List[Dict]) -> None:
        """Map professional positions to template."""
        # Look for "Academic Appointments" subsection first (right after PROFESSIONAL POSITIONS & EMPLOYMENT)
        section_idx = self._find_section_in_template("Academic Appointments")
        if section_idx is None:
            # Fall back to main section
            section_idx = self._find_section_in_template("PROFESSIONAL POSITIONS")

        if section_idx is None:
            logger.warning("Professional positions section not found in template")
            return

        table = self._find_table_after_paragraph(section_idx)

        if table and len(entries) > 0:
            logger.info(f"Adding {len(entries)} position entries to table")
            for entry in entries:
                row = table.add_row()
                cells = row.cells

                # Map to table columns: Title | Institution, city state | Dates
                if len(cells) >= 3:
                    # Column 0: Title
                    cells[0].text = entry.get('title', '')

                    # Column 1: Institution, city state
                    institution = entry.get('institution', '')
                    location = entry.get('location', '')
                    if institution and location:
                        cells[1].text = f"{institution}, {location}"
                    else:
                        cells[1].text = institution

                    # Column 2: Dates (mm/yy - mm/yy format)
                    start_year = entry.get('start_year', 0)
                    end_year = entry.get('end_year', 0)
                    is_current = entry.get('is_current', False)

                    if start_year:
                        if is_current or end_year == 0:
                            cells[2].text = f"{start_year} - Present"
                        elif end_year:
                            cells[2].text = f"{start_year} - {end_year}"
                        else:
                            cells[2].text = str(start_year)
                    else:
                        cells[2].text = ''
        else:
            if not table:
                logger.warning("No table found after positions section")
            if not entries:
                logger.info("No position entries to map")

    def _map_training(self, entries: List[Dict]) -> None:
        """Map postdoctoral training to template."""
        section_idx = self._find_section_in_template("POSTDOCTORAL TRAINING")
        if section_idx is None:
            logger.warning("Training section not found in template")
            return

        table = self._find_table_after_paragraph(section_idx)

        if table and len(entries) > 0:
            for entry in entries:
                row = table.add_row()
                cells = row.cells

                # Map to table columns: Title | Institution | Dates
                if len(cells) >= 3:
                    cells[0].text = entry.get('title', entry.get('raw_text', '')[:50])
                    cells[1].text = entry.get('institution', '')
                    dates = entry.get('dates', [])
                    cells[2].text = f"{dates[0]} - {dates[-1]}" if len(dates) >= 2 else str(dates[0]) if dates else ''

    def _map_honors(self, entries: List[Dict]) -> None:
        """Map honors and awards to template."""
        section_idx = self._find_section_in_template("HONORS, AWARDS")
        if section_idx is None:
            logger.warning("Honors section not found in template")
            return

        table = self._find_table_after_paragraph(section_idx)

        if table and len(entries) > 0:
            for entry in entries:
                row = table.add_row()
                cells = row.cells

                # Map to table columns: Award | Organization | Date
                if len(cells) >= 3:
                    cells[0].text = entry.get('award_name', entry.get('raw_text', '')[:100])
                    cells[1].text = entry.get('organization', '')
                    dates = entry.get('dates', [])
                    cells[2].text = dates[-1] if dates else ''

    def _map_publications(self, entries: List[Dict]) -> None:
        """Map publications to template."""
        section_idx = self._find_section_in_template("BIBLIOGRAPHY")
        if section_idx is None:
            logger.warning("Bibliography section not found in template")
            return

        # Publications are typically added as numbered paragraphs
        # Find insertion point (after section header)
        insertion_idx = section_idx + 1

        logger.info(f"Adding {len(entries)} publications to bibliography")

        for i, entry in enumerate(entries, 1):
            # Format publication
            if entry.get('authors') and entry.get('title'):
                # Structured format
                # Handle authors - could be a list or string
                authors = entry.get('authors', '')
                if isinstance(authors, list):
                    # Join authors with commas
                    authors_str = ', '.join(authors)
                else:
                    authors_str = str(authors)

                pub_text = f"{i}. {authors_str}. {entry.get('title', '')}. "
                pub_text += f"{entry.get('journal', '')}. "
                if entry.get('year'):
                    pub_text += f"{entry['year']};"
                if entry.get('volume'):
                    pub_text += f"{entry['volume']}"
                if entry.get('issue'):
                    pub_text += f"({entry['issue']})"
                if entry.get('pages'):
                    pub_text += f":{entry['pages']}"
                if entry.get('doi'):
                    pub_text += f" doi:{entry['doi']}"
            else:
                # Use raw text
                pub_text = f"{i}. {entry.get('original_text', entry.get('raw_text', ''))}"

            # Insert paragraph
            para = self.doc.paragraphs[insertion_idx].insert_paragraph_before(pub_text)
            insertion_idx += 1

    def _map_grants(self, entries: List[Dict]) -> None:
        """Map grants/research support to template."""
        section_idx = self._find_section_in_template("RESEARCH SUPPORT")
        if section_idx is None:
            logger.warning("Research support section not found in template")
            return

        table = self._find_table_after_paragraph(section_idx)

        if table and len(entries) > 0:
            logger.info(f"Adding {len(entries)} grant entries to table")
            for entry in entries:
                row = table.add_row()
                cells = row.cells

                # Map to table columns: Grant Title | Agency | Role | Dates
                if len(cells) >= 4:
                    cells[0].text = entry.get('title', entry.get('grant_title', ''))
                    cells[1].text = entry.get('agency', entry.get('funding_agency', ''))
                    cells[2].text = entry.get('role', entry.get('investigator_role', ''))

                    # Handle dates
                    start_year = entry.get('start_year', 0)
                    end_year = entry.get('end_year', 0)
                    if start_year and end_year:
                        cells[3].text = f"{start_year} - {end_year}"
                    elif start_year:
                        cells[3].text = str(start_year)
                    else:
                        cells[3].text = ''
        else:
            if not table:
                logger.warning("No table found after research support section")
            if not entries:
                logger.info("No grant entries to map")

    def _map_generic_section(self, section_name: str, data: Any) -> None:
        """Map generic section data to template."""
        section_idx = self._find_section_in_template(section_name)
        if section_idx is None:
            logger.warning(f"Section '{section_name}' not found in template")
            return

        # If data has entries, add them
        if isinstance(data, dict) and 'entries' in data:
            insertion_idx = section_idx + 1
            for entry in data['entries']:
                text = entry.get('text', '')
                if text:
                    self.doc.paragraphs[insertion_idx].insert_paragraph_before(text)
                    insertion_idx += 1

    def _find_table_after_paragraph(self, para_idx: int) -> Optional[Any]:
        """
        Find the first table that appears after a given paragraph.

        Args:
            para_idx: Paragraph index

        Returns:
            Table object or None
        """
        # In python-docx, we need to check the document's element tree to find
        # tables that come after a specific paragraph

        if para_idx >= len(self.doc.paragraphs):
            return None

        target_para = self.doc.paragraphs[para_idx]
        target_elem = target_para._element

        # Find the next table element after this paragraph
        for element in target_elem.getparent().iterchildren():
            # Skip until we find our paragraph
            if element == target_elem:
                # Found our paragraph, now look for next table
                for next_elem in element.itersiblings():
                    if next_elem.tag.endswith('}tbl'):  # Table element
                        # Find which table object this corresponds to
                        for table in self.doc.tables:
                            if table._element == next_elem:
                                return table

        return None

    def save(self, output_path: str) -> None:
        """
        Save the populated template.

        Args:
            output_path: Path for output file
        """
        output_path = Path(output_path)
        self.doc.save(str(output_path))
        logger.info(f"Saved output to {output_path}")

    def get_unmapped_sections(self, structured_data: Dict[str, Any]) -> List[str]:
        """
        Get list of sections that couldn't be mapped.

        Args:
            structured_data: Structured CV data

        Returns:
            List of unmapped section names
        """
        unmapped = []

        for section_name in structured_data.keys():
            if self._find_section_in_template(section_name) is None:
                unmapped.append(section_name)

        return unmapped


def map_cv_to_template(template_path: str, structured_data: Dict[str, Any], output_path: str) -> str:
    """
    Convenience function to map CV data to template and save.

    Args:
        template_path: Path to template
        structured_data: Structured CV data
        output_path: Output file path

    Returns:
        Output file path
    """
    mapper = TemplateMapper(template_path)
    mapper.map_data(structured_data)
    mapper.save(output_path)
    return output_path
