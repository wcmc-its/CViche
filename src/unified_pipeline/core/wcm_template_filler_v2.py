"""
WCM Template Filler v2 - Robust template population

Properly fills the official WCM CV template with parsed data.
Handles all sections, uses Arial font, and works for any CV.

Usage:
    python wcm_template_filler_v2.py <data_directory> [--output OUTPUT.docx]
"""

import os
import sys
import json
import argparse
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime

try:
    from docx import Document
    from docx.shared import Pt, RGBColor
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    from docx.enum.text import WD_PARAGRAPH_ALIGNMENT
except ImportError:
    print("Error: python-docx not installed. Install with: pip install python-docx")
    sys.exit(1)


class WCMTemplateFiller:
    """Fills WCM template with parsed CV data."""

    def __init__(self, template_path: str):
        if not os.path.exists(template_path):
            raise FileNotFoundError(f"Template not found: {template_path}")

        self.doc = Document(template_path)

    def _set_arial_font(self, run, size=11):
        """Set Arial font for a run."""
        run.font.name = 'Arial'
        run.font.size = Pt(size)

    def _find_paragraph_with_text(self, search_text: str, case_sensitive: bool = False) -> Optional[int]:
        """Find paragraph index containing text."""
        # Normalize apostrophes for matching (convert curly quotes to straight quotes)
        # U+2019 (') → U+0027 (')
        search = search_text if case_sensitive else search_text.lower()

        for i, para in enumerate(self.doc.paragraphs):
            para_text_norm = para.text.replace('\u2019', "'")  # Replace right single quote
            para_text = para_text_norm if case_sensitive else para_text_norm.lower()
            if search in para_text:
                return i
        return None

    def _find_table_after_paragraph(self, para_idx: int) -> Optional[Table]:
        """Find first table after a specific paragraph index."""
        # Get the paragraph element
        if para_idx >= len(self.doc.paragraphs):
            return None

        target_para = self.doc.paragraphs[para_idx]
        para_elem = target_para._element

        # Find this element in the body using index-based approach
        # (element equality can be unreliable after document modifications)
        body_elements = list(self.doc.element.body)

        try:
            para_body_idx = body_elements.index(para_elem)
            # Look for first table after this paragraph
            for i in range(para_body_idx + 1, len(body_elements)):
                if body_elements[i].tag.endswith('tbl'):
                    return Table(body_elements[i], self.doc)
        except ValueError:
            # Fallback: couldn't find by element equality
            pass

        return None

    def _clear_table_data(self, table: Table, keep_header: bool = True):
        """Remove all data rows from table."""
        if not table:
            return

        start_row = 1 if keep_header else 0

        # Remove rows in reverse order
        for i in range(len(table.rows) - 1, start_row - 1, -1):
            table._element.remove(table.rows[i]._element)

    def _add_table_row(self, table: Table, data: List[str]):
        """Add row to table with Arial font."""
        if not table:
            return

        row = table.add_row()
        for i, value in enumerate(data):
            if i < len(row.cells):
                cell = row.cells[i]
                cell.text = str(value) if value else ""
                # Set Arial font for cell content
                for paragraph in cell.paragraphs:
                    for run in paragraph.runs:
                        self._set_arial_font(run, 10)

    def fill_personal_data(self, segmented_data: Dict[str, Any]):
        """Fill header and personal data section."""
        # Extract name from document_uid
        doc_uid = segmented_data.get("document_uid", "")

        # Parse UID: CV_FirstName_MiddleInitial_LastName_Degree
        parts = doc_uid.replace("CV_", "").split("_")

        if len(parts) >= 2:
            first_name = parts[0] if len(parts) > 0 else ""
            last_name = parts[-1]

            # Check for degree suffix
            degree = ""
            if last_name in ["MD", "PhD", "DO"]:
                degree = last_name
                last_name = parts[-2] if len(parts) > 1 else ""

            # Middle initial
            middle = ""
            if len(parts) > 2 and parts[1] not in ["MD", "PhD", "DO"]:
                middle = parts[1]

            # Format full name
            if degree == "MD":
                degree = "M.D."
            elif degree == "PhD":
                degree = "Ph.D."
            elif degree == "DO":
                degree = "D.O."

            full_name = f"{first_name} {middle} {last_name}".strip()
            if degree:
                full_name += f", {degree}"

            # Fill Name field
            name_idx = self._find_paragraph_with_text("Name:")
            if name_idx is not None:
                para = self.doc.paragraphs[name_idx]
                para.clear()
                run = para.add_run(f"Name: {full_name}")
                run.bold = True
                self._set_arial_font(run, 11)

            # Fill Date of Preparation
            date_idx = self._find_paragraph_with_text("Date of Preparation:")
            if date_idx is not None:
                para = self.doc.paragraphs[date_idx]
                para.clear()
                run = para.add_run(f"Date of Preparation: {datetime.now().strftime('%m/%Y')}")
                run.bold = True
                self._set_arial_font(run, 11)

    def fill_education(self, education_data: List[Dict[str, Any]]):
        """Fill education section tables."""
        # Find "Academic Degree(s)" section
        edu_idx = self._find_paragraph_with_text("Academic Degree(s) (Bachelor's and higher)")
        if edu_idx is None:
            print("Warning: Could not find Education section")
            return

        # Get table after this heading
        table = self._find_table_after_paragraph(edu_idx)
        if not table:
            print("Warning: Could not find Education table")
            return

        # Clear existing data
        self._clear_table_data(table, keep_header=True)

        # Sort by end year (chronological)
        sorted_edu = sorted(
            [e for e in education_data if e.get("confidence", 0) >= 0.5],
            key=lambda x: x.get("end_year", 0) if x.get("end_year", 0) > 0 else 0
        )

        for edu in sorted_edu:
            # Column 1: Degree, field
            degree_field = edu.get("degree", "")
            if edu.get("major_field") and edu["major_field"] not in ["Medicine", "N/A", ""]:
                degree_field += f", {edu['major_field']}"

            # Column 2: Institution, location
            institution = edu.get("institution", "")
            if edu.get("location"):
                institution += f", {edu['location']}"

            # Column 3: Dates attended (mm/yyyy-mm/yyyy format)
            start = edu.get("start_year", 0)
            end = edu.get("end_year", 0)

            if end > 0 and start > 0:
                dates_attended = f"{start:04d}-{end:04d}"
            elif end > 0:
                dates_attended = f"{end:04d}"
            else:
                dates_attended = ""

            # Column 4: Year Awarded (just the end year)
            year_awarded = f"{end:04d}" if end > 0 else ""

            self._add_table_row(table, [degree_field, institution, dates_attended, year_awarded])

    def fill_positions(self, positions_data: List[Dict[str, Any]]):
        """Fill professional positions sections."""
        # Academic Appointments
        acad_idx = self._find_paragraph_with_text("Academic Appointments (Teaching and research")
        if acad_idx is not None:
            acad_table = self._find_table_after_paragraph(acad_idx)
            if acad_table:
                self._clear_table_data(acad_table, keep_header=True)

                academic_pos = [p for p in positions_data
                              if p.get("position_type") == "academic_faculty"
                              and p.get("confidence", 0) >= 0.5]

                # Sort chronologically
                academic_pos.sort(key=lambda x: x.get("start_year", 0))

                for pos in academic_pos:
                    title = pos.get("title", "")

                    institution = pos.get("institution", "")
                    if pos.get("location"):
                        institution += f", {pos['location']}"

                    # Format dates
                    start = pos.get("start_year", 0)
                    end = pos.get("end_year", 0)

                    if end == 9999:
                        dates = f"{start:04d}-Present" if start > 0 else "Present"
                    elif end > 0 and start > 0:
                        dates = f"{start:04d}-{end:04d}"
                    elif start > 0:
                        dates = f"{start:04d}"
                    else:
                        dates = ""

                    self._add_table_row(acad_table, [title, institution, dates])

        # Hospital/Clinical Appointments
        hosp_idx = self._find_paragraph_with_text("Hospital Appointments (Clinical")
        if hosp_idx is not None:
            hosp_table = self._find_table_after_paragraph(hosp_idx)
            if hosp_table:
                self._clear_table_data(hosp_table, keep_header=True)

                clinical_pos = [p for p in positions_data
                              if p.get("position_type") == "clinical"
                              and p.get("confidence", 0) >= 0.5]

                for pos in clinical_pos:
                    title = pos.get("title", "")
                    institution = pos.get("institution", "")
                    if pos.get("location"):
                        institution += f", {pos['location']}"
                    dates = "Present" if pos.get("is_current") else ""

                    self._add_table_row(hosp_table, [title, institution, dates])

    def fill_bibliography(self, publications_data: List[Dict[str, Any]]):
        """Fill bibliography section (section S)."""
        # Find BIBLIOGRAPHY section (must be standalone, not in instructions)
        # Look for paragraph that is JUST "BIBLIOGRAPHY" or starts with "S."
        bib_idx = None
        for i, para in enumerate(self.doc.paragraphs):
            text = para.text.strip()
            if text == "BIBLIOGRAPHY" or text.startswith("S.") and "BIBLIOGRAPHY" in text:
                bib_idx = i
                break

        if bib_idx is None:
            # Fallback to simple search
            bib_idx = self._find_paragraph_with_text("BIBLIOGRAPHY")

        if bib_idx is None:
            print("Warning: Could not find BIBLIOGRAPHY section")
            return

        # Find where to insert (after instructions)
        insert_idx = bib_idx + 1

        # Skip instruction paragraphs (look for "Number the entries")
        while insert_idx < len(self.doc.paragraphs):
            if "Number the entries" in self.doc.paragraphs[insert_idx].text:
                insert_idx += 1
                break
            if self.doc.paragraphs[insert_idx].text.strip() == "":
                break
            insert_idx += 1

        # Sort publications (most recent first as per WCM)
        sorted_pubs = sorted(
            [p for p in publications_data if p.get("confidence", 0) >= 0.5],
            key=lambda x: x.get("year", 0),
            reverse=True
        )

        # Insert publications
        for idx, pub in enumerate(sorted_pubs, 1):
            # Build citation
            parts = []

            # Authors
            authors = pub.get("authors", [])
            target_idx = pub.get("target_author_index")

            if authors:
                author_str = ", ".join(authors)
                parts.append(author_str + ".")

            # Title
            if pub.get("title"):
                parts.append(pub["title"] + ".")

            # Journal (should be italic but we'll keep it simple)
            if pub.get("journal"):
                parts.append(pub["journal"] + ".")

            # Year, volume, issue, pages
            cit = []
            if pub.get("year"):
                cit.append(str(pub["year"]))
            if pub.get("volume"):
                cit.append(f";{pub['volume']}")
            if pub.get("issue"):
                cit.append(f"({pub['issue']})")
            if pub.get("pages"):
                cit.append(f":{pub['pages']}")

            if cit:
                parts.append("".join(cit) + ".")

            # DOI/PMID
            ids = []
            if pub.get("doi"):
                ids.append(f"doi:{pub['doi']}")
            if pub.get("pmid"):
                ids.append(f"PMID:{pub['pmid']}")
            if ids:
                parts.append(" ".join(ids))

            # Create paragraph
            citation_text = f"{idx}. " + " ".join(parts)

            # Insert before next section
            para = self.doc.paragraphs[insert_idx].insert_paragraph_before(citation_text)

            # Format with Arial (NOT bold)
            for run in para.runs:
                run.bold = False
                self._set_arial_font(run, 11)

            # Bold target author if found
            if target_idx is not None and target_idx < len(authors):
                target_author = authors[target_idx]
                # Re-create runs to bold target author
                para.clear()

                # Split citation by author
                if target_author in citation_text:
                    before = citation_text.split(target_author)[0]
                    after = citation_text.split(target_author, 1)[1]

                    # Add before (not bold)
                    run1 = para.add_run(before)
                    run1.bold = False
                    self._set_arial_font(run1, 11)

                    # Add author (bold)
                    run2 = para.add_run(target_author)
                    run2.bold = True
                    self._set_arial_font(run2, 11)

                    # Add after (not bold)
                    run3 = para.add_run(after)
                    run3.bold = False
                    self._set_arial_font(run3, 11)
                else:
                    # Couldn't split, just add without bolding
                    run = para.add_run(citation_text)
                    run.bold = False
                    self._set_arial_font(run, 11)

            insert_idx += 1

    def fill_research_support(self, grants_data: List[Dict[str, Any]]):
        """Fill research support section."""
        # Find Research Support section
        support_idx = self._find_paragraph_with_text("Research Support:")
        if support_idx is None:
            return

        table = self._find_table_after_paragraph(support_idx)
        if not table:
            return

        self._clear_table_data(table, keep_header=False)

        valid_grants = [g for g in grants_data if g.get("confidence", 0) >= 0.6]

        for grant in valid_grants:
            # Build grant entry (WCM wants specific format)
            title = grant.get("title", "")
            agency = grant.get("agency", "")
            award = grant.get("award_number", "")
            role = grant.get("role", "")
            amount = grant.get("amount", "")

            start = grant.get("start_year", 0)
            end = grant.get("end_year", 0)

            if end == 9999:
                dates = f"{start}-Present" if start > 0 else "Present"
            elif end > 0 and start > 0:
                dates = f"{start}-{end}"
            else:
                dates = ""

            # Add to table
            self._add_table_row(table, [title, agency, award, role, amount, dates])

    def save(self, output_path: str):
        """Save filled document."""
        self.doc.save(output_path)


def main():
    parser = argparse.ArgumentParser(description="WCM Template Filler v2")
    parser.add_argument("data_dir", type=str, help="Directory with parsed data")
    parser.add_argument("--template", type=str,
                       default="/Users/paulalbert/Library/CloudStorage/Dropbox/Index/ReCiter/Scholar Signals - An LLM Pipeline/CV parsing - AI project/examples/template/wcm_cv_template_faculty_october_2022_final .docx",
                       help="WCM template path")
    parser.add_argument("--output", type=str, default=None, help="Output path")

    args = parser.parse_args()

    data_dir = Path(args.data_dir)

    # Find files
    segmented_files = list(data_dir.glob("*_segmented.json"))
    if not segmented_files:
        print(f"Error: No segmented file in {data_dir}")
        sys.exit(1)

    segmented_path = str(segmented_files[0])
    base_name = segmented_files[0].stem.replace("_segmented", "")

    pubs_path = str(data_dir / f"{base_name}_publications_parsed.json")
    edu_path = str(data_dir / f"{base_name}_education_parsed.json")
    pos_path = str(data_dir / f"{base_name}_positions_parsed.json")
    grants_path = str(data_dir / f"{base_name}_grants_parsed.json")

    output_path = args.output or str(data_dir / f"{base_name}_WCM_Official.docx")

    print("="*80)
    print("WCM TEMPLATE FILLER V2")
    print("="*80)
    print(f"Template: {Path(args.template).name}")
    print(f"Data: {data_dir}")
    print()

    # Load data
    with open(segmented_path) as f:
        segmented = json.load(f)

    pubs = []
    if os.path.exists(pubs_path):
        with open(pubs_path) as f:
            pubs = json.load(f).get("publications", [])
        print(f"✓ Publications: {len(pubs)}")

    edu = []
    if os.path.exists(edu_path):
        with open(edu_path) as f:
            edu = json.load(f).get("education", [])
        print(f"✓ Education: {len(edu)}")

    pos = []
    if os.path.exists(pos_path):
        with open(pos_path) as f:
            pos = json.load(f).get("positions", [])
        print(f"✓ Positions: {len(pos)}")

    grants = []
    if os.path.exists(grants_path):
        with open(grants_path) as f:
            grants = json.load(f).get("grants", [])
        print(f"✓ Grants: {len(grants)}")

    print()
    print("Filling template...")

    # Fill template
    filler = WCMTemplateFiller(args.template)
    filler.fill_personal_data(segmented)
    filler.fill_education(edu)
    filler.fill_positions(pos)
    filler.fill_bibliography(pubs)
    filler.fill_research_support(grants)
    filler.save(output_path)

    print()
    print("="*80)
    print("COMPLETE")
    print("="*80)
    print(f"✓ WCM CV: {output_path}")
    print()


if __name__ == '__main__':
    main()
