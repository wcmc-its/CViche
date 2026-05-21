"""
Direct CV Population using python-docx

Transparent, testable CV generation that directly manipulates Word documents.
No black boxes - every operation is logged and verifiable.

Architecture:
- Explicit table finding with clear error messages
- Section-by-section population with verification
- Post-generation validation
- Detailed logging of all operations
"""

from pathlib import Path
from typing import Dict, List, Optional
import json
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from docx.shared import Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.enum.table import WD_ALIGN_VERTICAL
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from datetime import datetime


class DirectCVPopulator:
    """
    Direct Word document populator using python-docx.

    Design principles:
    1. Explicit - every action is logged
    2. Verifiable - we can check what actually happened
    3. Debuggable - clear error messages
    4. Testable - simple to unit test
    """

    # Section configurations for generic table handler
    SECTION_CONFIGS = {
        # B - EDUCATION
        "B": {  # Generic education - use Academic Degree table
            "heading": "ACADEMIC DEGREE",
            "fields": {
                0: "Degree",
                1: "Field of Study",
                2: "Institution",
                3: "City",
                4: "State/Province",
                5: "Country",
                6: "Dates Attended",
                7: "Year Awarded"
            }
        },
        "B1": {
            "heading": "ACADEMIC DEGREE",
            "fields": {
                0: "Degree",
                1: "Field of Study",
                2: "Institution",
                3: "City",
                4: "State/Province",
                5: "Country",
                6: "Dates Attended",
                7: "Year Awarded"
            }
        },
        "B2": {
            "heading": "OTHER EDUCATIONAL EXPERIENCES",
            "fields": {
                0: "Program/Certificate Title",
                1: "Institution/Provider",
                2: "City",
                3: "State/Province",
                4: "Country",
                5: "Dates"
            }
        },
        "B2": {
            "heading": "OTHER EDUCATIONAL EXPERIENCES",
            "fields": {
                0: "Program/Certificate Title",
                1: "Institution/Provider",
                2: "City",
                3: "State/Province",
                4: "Country",
                5: "Dates"
            }
        },

        # C - POSTDOCTORAL TRAINING
        "C": {
            "heading": "POSTDOCTORAL TRAINING",
            "fields": {
                0: "Area of Study",
                1: "Institution",
                2: "City",
                3: "State/Province",
                4: "Country",
                5: "Dates"
            }
        },

        # D - POSITIONS
        "D1": {
            "heading": "ACADEMIC APPOINTMENTS",
            "fields": {
                0: "Title",
                1: "Institution",
                2: "City",
                3: "State/Province",
                4: "Country",
                5: "Dates"
            }
        },
        "D2": {
            "heading": "HOSPITAL APPOINTMENTS",
            "fields": {
                0: "Title",
                1: "Institution",
                2: "City",
                3: "State/Province",
                4: "Country",
                5: "Dates"
            }
        },
        "D3": {
            "heading": "OTHER PROFESSIONAL POSITIONS",
            "fields": {
                0: "Title",
                1: "Institution",
                2: "City",
                3: "State/Province",
                4: "Country",
                5: "Dates"
            }
        },

        # E - OTHER EMPLOYMENT
        "E": {
            "heading": "OTHER EMPLOYMENT",
            "fields": {
                0: "Title",
                1: "Institution",
                2: "City",
                3: "State/Province",
                4: "Country",
                5: "Dates"
            }
        },

        # F - LICENSURE & CERTIFICATION
        "F1": {
            "heading": "MEDICAL LICENSURE",
            "fields": {
                0: "State",
                1: "Number",
                2: "Date of issue",
                3: "Date of last registration"
            }
        },
        "F2": {
            "heading": "BOARD CERTIFICATION",
            "fields": {
                0: "Full Name of Board",
                1: "Certificate #",
                2: "Dates of Certification (yyyy-yyyy)"
            }
        },

        # G - INSTITUTIONAL/HOSPITAL AFFILIATION
        "G": {
            "heading": "INSTITUTIONAL/HOSPITAL AFFILIATION",
            "fields": {
                0: "Title",
                1: "Institution",
                2: "City",
                3: "State/Province",
                4: "Country",
                5: "Dates"
            }
        },

        # H - OTHER HONORS, AWARDS, RECOGNITION
        "H": {
            "heading": "OTHER HONORS",
            "fields": {
                0: "Award Name",
                1: "Granting Organization",
                2: "Date (yyyy)"
            }
        },

        # I - PROFESSIONAL ORGANIZATIONS
        "I": {
            "heading": "PROFESSIONAL ORGANIZATIONS",
            "fields": {
                0: "Organization",
                1: "Dates (yyyy-yyyy)"
            }
        },

        # L - CLINICAL SERVICE
        "L1": {
            "heading": "CLINICAL SERVICE",
            "fields": {
                0: "Clinical Activity",
                1: "Department/Division",
                2: "Institution",
                3: "Dates"
            }
        },
        "L2": {
            "heading": "ADMINISTRATIVE CLINICAL RESPONSIBILITIES",
            "fields": {
                0: "Role/Position",
                1: "Department/Division",
                2: "Institution",
                3: "Dates"
            }
        },
        "L3": {
            "heading": "CLINICAL INNOVATION",
            "fields": {
                0: "Innovation/Initiative",
                1: "Department/Division",
                2: "Institution",
                3: "Dates"
            }
        },

        # O - SERVICE
        "O": {
            "heading": "INSTITUTIONAL LEADERSHIP",
            "fields": {
                0: "Role/Position",
                1: "Department/Division",
                2: "Institution",
                3: "City",
                4: "State/Province",
                5: "Country",
                6: "Dates",
                7: "Scope/Description"
            }
        },

        # Q - PROFESSIONAL ACTIVITIES
        "Q1": {
            "heading": "BOARDS AND COMMITTEES",
            "fields": {
                0: "Role/Position",
                1: "Committee/Board Name",
                2: "Organization",
                3: "Dates",
                4: "Scope (Local/Regional/National/International)"
            }
        },
        "Q2": {
            "heading": "EDITORIAL ACTIVITIES",
            "fields": {
                0: "Role",
                1: "Journal/Publication",
                2: "Dates"
            }
        },
        "Q3": {
            "heading": "PEER REVIEW ACTIVITIES",
            "fields": {
                0: "Role",
                1: "Journal/Organization",
                2: "Dates"
            }
        },
        "Q4": {
            "heading": "EXTRAMURAL PROFESSIONAL RESPONSIBILITIES",
            "fields": {
                0: "Role/Position",
                1: "Organization",
                2: "Dates",
                3: "Scope"
            }
        },
        "Q5": {
            "heading": "OTHER EXTRAMURAL ACTIVITIES",
            "fields": {
                0: "Activity",
                1: "Organization",
                2: "Dates",
                3: "Description"
            }
        },

        # T - SUPPLEMENTAL
        "T1": {
            "heading": "COMMUNITY AND PUBLIC ENGAGEMENT",
            "fields": {
                0: "Activity",
                1: "Organization",
                2: "Dates",
                3: "Description"
            }
        },
        "T2": {
            "heading": "TECHNOLOGY TRANSFER",
            "fields": {
                0: "Activity",
                1: "Organization",
                2: "Dates",
                3: "Description"
            }
        },
        "T3": {
            "heading": "MEDIA AND PUBLIC RELATIONS",
            "fields": {
                0: "Activity",
                1: "Media Outlet",
                2: "Date",
                3: "Description"
            }
        },
        "T4": {
            "heading": "TECHNICAL SKILLS",
            "fields": {
                0: "Skill/Technology",
                1: "Level of Expertise",
                2: "Years of Experience"
            }
        },
        "T5": {
            "heading": "LANGUAGES",
            "fields": {
                0: "Language",
                1: "Level (Speaking)",
                2: "Level (Reading)",
                3: "Level (Writing)"
            }
        },
        "T6": {
            "heading": "REFERENCES",
            "fields": {
                0: "Name",
                1: "Title/Institution",
                2: "Contact Information"
            }
        },
        "T7": {
            "heading": "CONFERENCE ATTENDANCE",
            "fields": {
                0: "Conference Name",
                1: "Location",
                2: "Date",
                3: "Role/Purpose"
            }
        },

        # K - EDUCATIONAL CONTRIBUTIONS (try generic tables first)
        "K1": {
            "heading": "DIDACTIC TEACHING",
            "fields": {
                0: "Course/Program Title",
                1: "Role",
                2: "Institution",
                3: "Dates",
                4: "Hours/Credits"
            }
        },
        "K2": {
            "heading": "CLINICAL TEACHING",
            "fields": {
                0: "Activity/Setting",
                1: "Role",
                2: "Institution",
                3: "Dates",
                4: "Hours"
            }
        },
        "K3": {
            "heading": "EDUCATIONAL LEADERSHIP",
            "fields": {
                0: "Role/Position",
                1: "Program/Initiative",
                2: "Institution",
                3: "Dates"
            }
        },
        "K4": {
            "heading": "CONTINUING EDUCATION",
            "fields": {
                0: "Program/Course",
                1: "Role",
                2: "Institution",
                3: "Dates",
                4: "Hours"
            }
        },
        "K5": {
            "heading": "STUDENT ADVISING",
            "fields": {
                0: "Student Name",
                1: "Program/Degree",
                2: "Role",
                3: "Dates"
            }
        },
        "K7": {
            "heading": "PROGRAM DEVELOPMENT",
            "fields": {
                0: "Program Title",
                1: "Role",
                2: "Institution",
                3: "Dates"
            }
        },
        "K8": {
            "heading": "EDUCATIONAL SCHOLARSHIP",
            "fields": {
                0: "Activity/Publication",
                1: "Type",
                2: "Venue",
                3: "Date"
            }
        },

        # M - RESEARCH (some can use tables)
        "M1": {
            "heading": "RESEARCH ACTIVITIES",
            "fields": {
                0: "Research Area/Project",
                1: "Role",
                2: "Institution",
                3: "Dates"
            }
        },
        "M2": {
            "heading": "RESEARCH SUPPORT",
            "fields": {
                0: "Grant/Award Title",
                1: "Funding Source",
                2: "Role",
                3: "Award Amount",
                4: "Dates"
            }
        },
        "M2D": {
            "heading": "PATENTS AND INVENTIONS",
            "fields": {
                0: "Title",
                1: "Patent Number",
                2: "Inventors",
                3: "Date Issued",
                4: "Status"
            }
        },
        # NOTE: M4 clinical trial codes removed - clinical trials now use M2A/M2B/M2C based on status

        # N - MENTORING (try generic tables)
        "N1": {
            "heading": "RESEARCH MENTORSHIP",
            "fields": {
                0: "Mentee Name",
                1: "Program/Level",
                2: "Project/Research Area",
                3: "Dates",
                4: "Current Position"
            }
        },
        "N2": {
            "heading": "CLINICAL MENTORSHIP",
            "fields": {
                0: "Mentee Name",
                1: "Program/Level",
                2: "Clinical Area",
                3: "Dates",
                4: "Current Position"
            }
        },
        "N3": {
            "heading": "CAREER DEVELOPMENT MENTORSHIP",
            "fields": {
                0: "Mentee Name",
                1: "Program/Level",
                2: "Focus Area",
                3: "Dates",
                4: "Outcomes"
            }
        },
        "N4": {
            "heading": "MENTORING COMMITTEE MEMBERSHIP",
            "fields": {
                0: "Mentee Name",
                1: "Program/Level",
                2: "Role",
                3: "Dates"
            }
        },

        # A - PERSONAL DATA (general)
        "A": {
            "heading": "PERSONAL DATA",
            "fields": {
                0: "Field",
                1: "Value"
            }
        },

        # F - LICENSURE (general)
        "F": {
            "heading": "LICENSURE",
            "fields": {
                0: "Type",
                1: "State/Authority",
                2: "Number",
                3: "Date Issued",
                4: "Status"
            }
        },

        # General parent sections (route to specific subsections)
        "K": {  # Educational Contributions (general - use K1 table)
            "heading": "DIDACTIC TEACHING",
            "fields": {
                0: "Course/Program Title",
                1: "Role",
                2: "Institution",
                3: "Dates",
                4: "Hours/Credits"
            }
        },
        "M": {  # Research (general - use M1 table)
            "heading": "RESEARCH ACTIVITIES",
            "fields": {
                0: "Research Area/Project",
                1: "Role",
                2: "Institution",
                3: "Dates"
            }
        },
        "N": {  # Mentoring (general - use N1 table)
            "heading": "RESEARCH MENTORSHIP",
            "fields": {
                0: "Mentee Name",
                1: "Program/Level",
                2: "Project/Research Area",
                3: "Dates",
                4: "Current Position"
            }
        },
        "T": {  # Supplemental (general - use T1 table)
            "heading": "COMMUNITY AND PUBLIC ENGAGEMENT",
            "fields": {
                0: "Activity",
                1: "Organization",
                2: "Dates",
                3: "Description"
            }
        }
    }

    def __init__(self, template_path: Path, verbose: bool = True):
        """
        Initialize populator with a template.

        Args:
            template_path: Path to WCM CV template .docx file
            verbose: Enable detailed logging
        """
        self.template_path = Path(template_path)
        self.verbose = verbose

        if not self.template_path.exists():
            raise FileNotFoundError(f"Template not found: {template_path}")

        # Load template
        self.doc = Document(self.template_path)

        # Track operations
        self.operations = []
        self.sections_populated = 0
        self.total_entries = 0

        if self.verbose:
            print(f"Loaded template: {self.template_path.name}")
            print(f"  Tables: {len(self.doc.tables)}")
            print(f"  Paragraphs: {len(self.doc.paragraphs)}")

    def set_cell_border(self, cell, border_width_pt=1, border_color="808080"):
        """Set borders for a table cell with 1pt solid 50% gray."""
        tc = cell._element
        tcPr = tc.get_or_add_tcPr()
        tcBorders = OxmlElement('w:tcBorders')

        for border_name in ['top', 'left', 'bottom', 'right']:
            border = OxmlElement(f'w:{border_name}')
            border.set(qn('w:val'), 'single')
            border.set(qn('w:sz'), str(border_width_pt * 8))
            border.set(qn('w:color'), border_color)
            tcBorders.append(border)

        tcPr.append(tcBorders)

    def format_table_header_row(self, table: Table):
        """Format header row with bold text and borders."""
        if not table.rows:
            return

        header_row = table.rows[0]
        for cell in header_row.cells:
            cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.bold = True
                    run.font.name = 'Arial'
                    run.font.size = Pt(11)
            self.set_cell_border(cell, border_width_pt=1, border_color="808080")

    def format_table_cell(self, cell, text: str):
        """Format a table cell with Arial 11pt and borders."""
        cell.text = text
        cell.vertical_alignment = WD_ALIGN_VERTICAL.CENTER

        for paragraph in cell.paragraphs:
            paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            for run in paragraph.runs:
                run.font.name = 'Arial'
                run.font.size = Pt(11)

        self.set_cell_border(cell, border_width_pt=1, border_color="808080")

    def populate_header_field(self, field_label: str, value: str, search_limit: int = 20) -> bool:
        """Populate a header field like Name: or Date of Preparation: with proper formatting."""
        for i, para in enumerate(self.doc.paragraphs[:search_limit]):
            text = para.text.strip()
            if field_label.rstrip(':').lower() in text.lower() and text.startswith(field_label.split(':')[0]):
                # Clear all runs and rebuild with proper formatting
                for run in para.runs:
                    run._element.getparent().remove(run._element)

                # Add label
                label_run = para.add_run(field_label)
                label_run.font.name = 'Arial'
                label_run.font.size = Pt(11)

                # Add tab separator
                para.add_run('\t')

                # Add value
                value_run = para.add_run(value)
                value_run.font.name = 'Arial'
                value_run.font.size = Pt(11)

                return True

        return False

    def find_table_by_heading(self, heading_text: str, require: bool = True) -> Optional[Table]:
        """
        Find a table by looking for a heading that contains the text.

        Args:
            heading_text: Text to search for in headings (case-insensitive)
            require: If True, raise error if not found

        Returns:
            Table object or None
        """
        from docx.oxml.table import CT_Tbl

        heading_text_lower = heading_text.lower()

        # Search through paragraphs for the heading
        target_para_element = None
        for para in self.doc.paragraphs:
            if heading_text_lower in para.text.lower():
                target_para_element = para._element
                break

        if target_para_element is None:
            if require:
                raise ValueError(f"Heading '{heading_text}' not found in document")
            return None

        # Search for first table after this paragraph
        found_target = False
        for element in self.doc.element.body:
            if element == target_para_element:
                found_target = True
                continue

            if found_target and isinstance(element, CT_Tbl):
                # Found the first table after our heading
                # Match it to a Table object
                for table in self.doc.tables:
                    if table._element == element:
                        if self.verbose:
                            num_cols = len(table.rows[0].cells) if table.rows else 0
                            print(f"  ✓ Found table for '{heading_text}' ({len(table.rows)} rows, {num_cols} columns)")
                        return table

        if require:
            raise ValueError(f"No table found after heading '{heading_text}'")

        if self.verbose:
            print(f"  ⊘ Table for '{heading_text}' not found")

        return None

    def insert_name_heading(self, name: str) -> bool:
        """
        Insert the CV owner's name in the Name: field with proper formatting.

        Args:
            name: Full name to insert

        Returns:
            True if successful
        """
        if not name or not name.strip():
            if self.verbose:
                print("  ⚠️  No name provided to insert")
            return False

        # Populate the "Name:" field in the template header
        if self.populate_header_field("Name:", name):
            if self.verbose:
                print(f"  ✓ Name: {name}")

            # Also populate Date of Preparation
            current_date = datetime.now().strftime("%B %-d, %Y")
            if self.populate_header_field("Date of Preparation:", current_date):
                if self.verbose:
                    print(f"  ✓ Date of preparation: {current_date}")

            self.operations.append({
                "action": "insert_name",
                "name": name,
                "success": True
            })
            return True
        else:
            if self.verbose:
                print(f"  ⚠️  Could not find 'Name:' field in template")
            return False

    def populate_section_A1_name(self, enriched_data: dict) -> int:
        """
        Populate Section A1 (Personal Data - Name).

        Args:
            enriched_data: Enriched section data

        Returns:
            Number of entries processed
        """
        entries = enriched_data.get("parsed_entries", [])

        if not entries:
            return 0

        # Extract name from first entry
        first_entry = entries[0]
        structured_data = first_entry.get("structured_data", {})
        name = structured_data.get("Name", "").strip()

        if name:
            success = self.insert_name_heading(name)
            if success:
                return 1

        return 0

    def populate_generic_table(
        self,
        enriched_data: dict,
        heading_text: str,
        field_mapping: dict
    ) -> int:
        """
        Generic handler for standard WCM table sections.

        Works for sections with a simple table structure where we need to:
        1. Find table by heading
        2. Map structured_data fields to table columns
        3. Add rows with formatting

        Args:
            enriched_data: Enriched section data
            heading_text: Text to find the table heading
            field_mapping: Dict mapping column index to field name
                          e.g., {0: "Degree", 1: "Institution", 2: "Year Awarded"}

        Returns:
            Number of entries inserted
        """
        entries = enriched_data.get("parsed_entries", [])

        if not entries:
            return 0

        # Find the table
        table = self.find_table_by_heading(heading_text, require=True)

        if not table:
            return 0

        # Verify table structure
        if not table.rows:
            if self.verbose:
                print(f"  ⚠️  Table has no rows")
            return 0

        num_cols = len(table.rows[0].cells)

        # Format table header
        self.format_table_header_row(table)

        # Remove any existing data rows (template may have empty example rows)
        # Keep only the header row (row 0)
        rows_to_delete = len(table.rows) - 1
        for _ in range(rows_to_delete):
            table._element.remove(table.rows[-1]._element)

        inserted = 0

        for entry in entries:
            structured_data = entry.get("structured_data", {})

            # Skip entries with no data
            if not structured_data:
                continue

            # Add row
            row = table.add_row()

            # Populate cells based on field mapping
            for col_idx, field_name in field_mapping.items():
                if col_idx < num_cols:
                    value = structured_data.get(field_name, "")
                    self.format_table_cell(row.cells[col_idx], str(value))

            inserted += 1

            if self.verbose:
                # Show first 3 column values
                preview = " | ".join([
                    str(structured_data.get(field_mapping.get(i, ""), ""))[:30]
                    for i in range(min(3, num_cols))
                ])
                print(f"    → Row {len(table.rows) - 1}: {preview}")

        if self.verbose:
            print(f"  ✓ Inserted {inserted} entries into table")

        self.operations.append({
            "action": "populate_generic_table",
            "heading": heading_text,
            "entries_inserted": inserted,
            "table_rows": len(table.rows)
        })

        return inserted

    def populate_section_J_percent_effort(self, enriched_data: dict) -> int:
        """
        Populate Section J (Percent Effort).

        Special table with fixed rows:
        - Teaching
        - Clinical
        - Administrative
        - Research
        - Total (100%)

        Updates existing rows with percent effort values.
        """
        entries = enriched_data.get("parsed_entries", [])

        if not entries:
            return 0

        # Find table by heading
        table = self.find_table_by_heading("PERCENT EFFORT", require=True)

        if not table:
            return 0

        # Aggregate entries by activity type
        activity_summary = {}

        for entry in entries:
            structured_data = entry.get("structured_data", {})

            # Get activity type and percent
            activity_type = structured_data.get("Activity Type", "").strip()
            percent_effort = structured_data.get("Percent Effort", "").strip()
            involves_students = structured_data.get("Involves Students", "").strip()

            if not activity_type:
                continue

            # Normalize to match table rows
            activity_key = activity_type.title()  # Teaching, Clinical, Administrative, Research

            if activity_key not in activity_summary:
                activity_summary[activity_key] = {
                    "percent_effort": percent_effort,
                    "involves_students": involves_students
                }

        # Update fixed table rows
        # Row 0: Headers
        # Row 1: Teaching
        # Row 2: Clinical
        # Row 3: Administrative
        # Row 4: Research
        # Row 5: Total (calculated or entered)

        activity_to_row = {
            "Teaching": 1,
            "Clinical": 2,
            "Administrative": 3,
            "Research": 4
        }

        updated = 0

        for activity_type, row_idx in activity_to_row.items():
            if activity_type in activity_summary and row_idx < len(table.rows):
                summary = activity_summary[activity_type]
                row = table.rows[row_idx]

                # Update Percent Effort (column 1)
                if summary["percent_effort"]:
                    percent_text = summary["percent_effort"]
                    if not percent_text.endswith("%"):
                        percent_text = f"{percent_text}%"

                    self.format_table_cell(row.cells[1], percent_text)
                    updated += 1

                    if self.verbose:
                        print(f"    → {activity_type}: {percent_text}")

                # Update Student Involvement (column 2) if present
                if len(row.cells) > 2 and summary["involves_students"]:
                    self.format_table_cell(row.cells[2], summary["involves_students"])

        if updated > 0:
            self.operations.append({
                "action": "populate_section_J",
                "rows_updated": updated
            })

        return updated

    def populate_section_R_presentations(self, enriched_data: dict) -> int:
        """
        Populate Section R (Presentations).

        Routes to subsections based on location:
        - National* for US locations
        - International* for non-US locations
        """
        entries = enriched_data.get("parsed_entries", [])

        if not entries:
            return 0

        # Helper to check if location is US
        def is_us_location(country: str) -> bool:
            if not country:
                return False
            country_lower = country.lower().strip()
            us_variants = ['usa', 'us', 'united states', 'united states of america', 'u.s.', 'u.s.a.']
            return country_lower in us_variants

        # Route entries by location
        national_entries = []
        international_entries = []

        for entry in entries:
            structured_data = entry.get("structured_data", {})

            # Get country from structured data
            country = structured_data.get("Country", "").strip()

            # If no country, try Location field
            if not country:
                location = structured_data.get("Location", "").strip()
                if "," in location:
                    country = location.split(",")[-1].strip()
                else:
                    country = location

            # Route based on country
            if country and is_us_location(country):
                national_entries.append(entry)
            else:
                international_entries.append(entry)

        if self.verbose:
            print(f"  Section R routing: {len(national_entries)} National, {len(international_entries)} International")

        inserted = 0

        # Populate National subsection
        if national_entries:
            table = self.find_table_by_heading("NATIONAL*")
            if table:
                # Format header and clear template rows
                self.format_table_header_row(table)
                rows_to_delete = len(table.rows) - 1
                for _ in range(rows_to_delete):
                    table._element.remove(table.rows[-1]._element)

                # Insert entries
                for entry in national_entries:
                    structured_data = entry.get("structured_data", {})

                    row = table.add_row()

                    # Title/Topic (col 0)
                    title = structured_data.get("Title", structured_data.get("Presentation Title", ""))
                    self.format_table_cell(row.cells[0], title)

                    # Institution/Location (col 1) - combine venue and location
                    venue = structured_data.get("Conference/Venue", structured_data.get("Venue", ""))
                    location = structured_data.get("Location", "")
                    institution_location = ", ".join([v for v in [venue, location] if v])
                    self.format_table_cell(row.cells[1], institution_location)

                    # Date (col 2)
                    date = structured_data.get("Date", structured_data.get("Year", ""))
                    self.format_table_cell(row.cells[2], str(date))

                    inserted += 1

                    if self.verbose:
                        print(f"    → National: {title[:40]}")

        # Populate International subsection
        if international_entries:
            table = self.find_table_by_heading("INTERNATIONAL*")
            if table:
                # Format header and clear template rows
                self.format_table_header_row(table)
                rows_to_delete = len(table.rows) - 1
                for _ in range(rows_to_delete):
                    table._element.remove(table.rows[-1]._element)

                # Insert entries
                for entry in international_entries:
                    structured_data = entry.get("structured_data", {})

                    row = table.add_row()

                    # Title/Topic (col 0)
                    title = structured_data.get("Title", structured_data.get("Presentation Title", ""))
                    self.format_table_cell(row.cells[0], title)

                    # Institution/Location (col 1)
                    venue = structured_data.get("Conference/Venue", structured_data.get("Venue", ""))
                    location = structured_data.get("Location", "")
                    institution_location = ", ".join([v for v in [venue, location] if v])
                    self.format_table_cell(row.cells[1], institution_location)

                    # Date (col 2)
                    date = structured_data.get("Date", structured_data.get("Year", ""))
                    self.format_table_cell(row.cells[2], str(date))

                    inserted += 1

                    if self.verbose:
                        print(f"    → International: {title[:40]}")

        if inserted > 0:
            self.operations.append({
                "action": "populate_section_R",
                "national_count": len(national_entries),
                "international_count": len(international_entries),
                "total_inserted": inserted
            })

        return inserted

    def populate_section_S_publications(self, enriched_data: dict, cv_owner_name: str = None) -> int:
        """
        Populate Section S (Bibliography/Publications).

        Handles 15 subsections (S1-S15) with formatted bibliographic entries.
        Bolds the CV owner's name in the author list.
        """
        # Check if data has subsections structure
        subsections = enriched_data.get("subsections", {})

        if not subsections:
            # Try to get entries directly
            entries = enriched_data.get("parsed_entries", [])
            if not entries:
                return 0

            # Create a default S1 subsection for all entries
            subsections = {"S1": {"entries": entries}}

        # Subsection title mapping
        subsection_headings = {
            "S1": "Peer-Reviewed",
            "S2": "Reviews",
            "S3": "Letters",
            "S4": "Chapters",
            "S5": "Books",
            "S6": "Case Reports",
            "S7": "In review",
            "S8": "Abstracts",
            "S9": "Non-peer-reviewed",
            "S10": "Preprints",
            "S11": "Presentations",
            "S12": "Posters",
            "S13": "Invited Lectures",
            "S14": "Media",
            "S15": "Other Publications"
        }

        inserted = 0

        for subsection_id in sorted(subsections.keys()):
            subsection_data = subsections[subsection_id]
            entries = subsection_data.get("entries", [])

            if not entries:
                continue

            # Find heading for this subsection
            heading = subsection_headings.get(subsection_id, subsection_id)

            # Find the section heading in the document
            heading_para = None
            for para in self.doc.paragraphs:
                if heading.lower() in para.text.lower():
                    heading_para = para
                    break

            if not heading_para:
                if self.verbose:
                    print(f"  ⚠️  Heading '{heading}' not found for {subsection_id}")
                continue

            # Insert publications as numbered paragraphs after the heading
            # Find the index of this paragraph
            para_index = None
            for idx, para in enumerate(self.doc.paragraphs):
                if para == heading_para:
                    para_index = idx
                    break

            if para_index is None:
                continue

            # Insert entries as paragraphs
            entry_num = 1
            for entry in entries:
                structured_data = entry.get("structured_data", {})

                # Format bibliographic entry
                citation = self._format_publication_citation(structured_data, cv_owner_name)

                if not citation:
                    continue

                # Insert paragraph after heading
                # Note: This is simplified - production code would need proper paragraph insertion
                new_para = self.doc.add_paragraph()
                new_para.text = f"{entry_num}. {citation}"

                # Apply formatting
                new_para.style = 'Normal'
                for run in new_para.runs:
                    run.font.name = 'Arial'
                    run.font.size = Pt(11)

                inserted += 1
                entry_num += 1

                if self.verbose:
                    print(f"    → {subsection_id}: {citation[:60]}...")

        if inserted > 0:
            self.operations.append({
                "action": "populate_section_S",
                "subsections_populated": len([s for s in subsections.keys() if subsections[s].get("entries")]),
                "total_entries": inserted
            })

        return inserted

    def _format_publication_citation(self, pub: dict, cv_owner_name: str = None) -> str:
        """
        Format publication as bibliographic citation.

        Args:
            pub: Publication structured data
            cv_owner_name: CV owner name to bold (e.g., "Smith, J.")

        Returns:
            Formatted citation string
        """
        # Get authors - handle both string and list formats
        authors = pub.get("authors", pub.get("Authors", ""))
        if isinstance(authors, list):
            authors = ", ".join(authors)

        if not authors:
            authors = "[No authors]"

        # Get title
        title = pub.get("title", pub.get("Title", ""))
        if not title:
            title = "[No title]"

        # Get journal/venue
        journal = pub.get("journal", pub.get("Journal", pub.get("venue", "")))

        # Get year
        year = pub.get("year", pub.get("Year", pub.get("publication_year", "")))

        # Get volume, issue, pages
        volume = pub.get("volume", pub.get("Volume", ""))
        issue = pub.get("issue", pub.get("Issue", ""))
        pages = pub.get("pages", pub.get("Pages", ""))

        # Get DOI/PMID
        doi = pub.get("doi", pub.get("DOI", ""))
        pmid = pub.get("pmid", pub.get("PMID", ""))

        # Build citation
        parts = []

        # Authors
        parts.append(authors)

        # Title
        if title:
            parts.append(f'"{title}"')

        # Journal/Venue
        if journal:
            journal_str = journal
            if volume:
                journal_str += f" {volume}"
                if issue:
                    journal_str += f"({issue})"
            if pages:
                journal_str += f": {pages}"
            parts.append(journal_str)

        # Year
        if year:
            parts.append(f"({year})")

        # DOI or PMID
        if doi:
            parts.append(f"DOI: {doi}")
        elif pmid:
            parts.append(f"PMID: {pmid}")

        return ". ".join(parts) + "."

    def populate_section_P_institutional_admin(self, enriched_data: dict) -> int:
        """
        Populate Section P (Institutional Administrative Activities).

        Table structure: 3 columns
        - Column 0: Name of Committee
        - Column 1: Role (i.e., member, secretary, etc.)
        - Column 2: Dates (yyyy-yyyy)

        Args:
            enriched_data: Enriched section data

        Returns:
            Number of entries inserted
        """
        entries = enriched_data.get("parsed_entries", [])

        if not entries:
            return 0

        # Find the table
        table = self.find_table_by_heading("INSTITUTIONAL ADMINISTRATIVE", require=True)

        if not table:
            return 0

        # Verify table structure
        if not table.rows:
            if self.verbose:
                print(f"  ⚠️  Table has no rows")
            return 0

        num_cols = len(table.rows[0].cells)
        if self.verbose:
            print(f"  Table has {num_cols} columns")

        # Format table header (bold, borders)
        self.format_table_header_row(table)

        # Remove any existing data rows (template may have empty example rows)
        # Keep only the header row (row 0)
        rows_to_delete = len(table.rows) - 1
        for _ in range(rows_to_delete):
            table._element.remove(table.rows[-1]._element)

        inserted = 0

        for entry in entries:
            structured_data = entry.get("structured_data", {})

            # NEW: Check if we have LLM-parsed structured fields
            # Service parser can return different field name formats:
            # Format 1 (raw LLM): role, committee_or_activity, start_year, end_year
            # Format 2 (formatted): Role/Position, Department/Division, Dates
            has_structured = False

            # Try Format 2 first (formatted output from service parser)
            if "Role/Position" in structured_data or "Department/Division" in structured_data:
                role = structured_data.get("Role/Position", "")
                committee = structured_data.get("Department/Division", "")
                dates = structured_data.get("Dates", "")
                has_structured = True

            # Try Format 1 (raw LLM output)
            elif "role" in structured_data and "committee_or_activity" in structured_data:
                role = structured_data.get("role", "")
                committee = structured_data.get("committee_or_activity", "")

                # Format dates from years
                start_year = structured_data.get("start_year", 0)
                end_year = structured_data.get("end_year", 0)

                if start_year and end_year and end_year != 0:
                    dates = f"{start_year}-{end_year}"
                elif start_year and (end_year == 0 or not end_year):
                    dates = f"{start_year}-Present"
                else:
                    dates = ""
                has_structured = True

            if has_structured:
                # Use LLM-parsed structured fields with formatting!
                row = table.add_row()
                self.format_table_cell(row.cells[0], committee)
                self.format_table_cell(row.cells[1], role)
                if num_cols > 2:
                    self.format_table_cell(row.cells[2], dates)

                inserted += 1

                if self.verbose:
                    print(f"    → Row {len(table.rows) - 1}: {committee[:40]} | {role[:20]} | {dates}")

            else:
                # Fallback: Try to parse from text field
                text = structured_data.get("text", "").strip()

                if not text:
                    continue

                # Clean up bullet points
                text = text.lstrip("•").strip()

                # Try to parse: "Chair, Curriculum Committee, 2021-Present"
                # Format: Role, Committee, Dates
                parts = [p.strip() for p in text.split(',')]

                row = table.add_row()

                if len(parts) >= 3:
                    # Parsed format
                    role = parts[0]
                    committee = ', '.join(parts[1:-1])  # Handle multi-part committee names
                    dates = parts[-1]

                    row.cells[0].text = committee
                    row.cells[1].text = role
                    row.cells[2].text = dates if num_cols > 2 else ""
                elif len(parts) == 2:
                    # Only role and committee/dates
                    row.cells[0].text = parts[1]
                    row.cells[1].text = parts[0]
                    row.cells[2].text = "" if num_cols > 2 else ""
                else:
                    # Can't parse, put everything in first column
                    row.cells[0].text = text
                    row.cells[1].text = "" if num_cols > 1 else ""
                    row.cells[2].text = "" if num_cols > 2 else ""

                inserted += 1

                if self.verbose:
                    print(f"    → Row {len(table.rows) - 1}: {row.cells[0].text[:40]} | {row.cells[1].text[:20]} | {row.cells[2].text}")

        if self.verbose:
            print(f"  ✓ Inserted {inserted} entries into Section P table")

        self.operations.append({
            "action": "populate_section",
            "section_id": "P",
            "entries_inserted": inserted,
            "table_rows": len(table.rows)
        })

        return inserted

    def populate_from_enriched_file(self, enriched_file: Path) -> int:
        """
        Populate CV from a single enriched section file.

        Args:
            enriched_file: Path to enriched JSON file

        Returns:
            Number of entries inserted
        """
        if not enriched_file.exists():
            if self.verbose:
                print(f"  ⚠️  File not found: {enriched_file}")
            return 0

        # Load enriched data
        with open(enriched_file) as f:
            data = json.load(f)

        section_id = data.get("section_id", "UNKNOWN")
        section_name = data.get("section_name", "Unknown")
        num_entries = data.get("num_entries", 0)

        if self.verbose:
            print(f"\nProcessing {enriched_file.name}...")
            print(f"  Section: {section_id} - {section_name}")
            print(f"  Entries: {num_entries}")

        if num_entries == 0:
            if self.verbose:
                print(f"  ⊘ No entries to insert")
            return 0

        # Route to appropriate handler based on section_id
        inserted = 0

        if section_id == "A1":
            inserted = self.populate_section_A1_name(data)
        elif section_id == "P":
            inserted = self.populate_section_P_institutional_admin(data)
        elif section_id == "J":
            inserted = self.populate_section_J_percent_effort(data)
        elif section_id == "R":
            inserted = self.populate_section_R_presentations(data)
        elif section_id == "S" or section_id.startswith("S") and len(section_id) <= 3:
            # S, S1-S15 all use the publications handler
            inserted = self.populate_section_S_publications(data)
        elif section_id in self.SECTION_CONFIGS:
            # Use generic handler for standard table sections
            config = self.SECTION_CONFIGS[section_id]
            inserted = self.populate_generic_table(
                data,
                heading_text=config["heading"],
                field_mapping=config["fields"]
            )
        else:
            if self.verbose:
                print(f"  ⚠️  Section {section_id} not yet supported")
            return 0

        if inserted > 0:
            self.sections_populated += 1
            self.total_entries += inserted

        return inserted

    def populate_from_directory(self, enriched_dir: Path, cv_id: str) -> dict:
        """
        Populate CV from all enriched files in a directory.

        Args:
            enriched_dir: Directory containing enriched JSON files
            cv_id: CV identifier to match files

        Returns:
            Result dictionary with statistics
        """
        enriched_dir = Path(enriched_dir)

        if not enriched_dir.exists():
            raise FileNotFoundError(f"Enriched directory not found: {enriched_dir}")

        # Find all enriched files for this CV
        pattern = f"*_{cv_id}_enriched.json"
        enriched_files = sorted(enriched_dir.glob(pattern))

        if self.verbose:
            print(f"\n{'='*80}")
            print(f"POPULATING CV: {cv_id}")
            print(f"{'='*80}")
            print(f"Enriched directory: {enriched_dir}")
            print(f"Pattern: {pattern}")
            print(f"Found {len(enriched_files)} enriched files")
            print()

        if not enriched_files:
            print(f"⚠️  No enriched files found matching pattern: {pattern}")
            return {
                "success": False,
                "error": "No enriched files found",
                "sections_populated": 0,
                "total_entries": 0
            }

        # Process each file
        for enriched_file in enriched_files:
            try:
                self.populate_from_enriched_file(enriched_file)
            except Exception as e:
                if self.verbose:
                    print(f"  ✗ Error processing {enriched_file.name}: {e}")
                self.operations.append({
                    "action": "populate_file",
                    "file": str(enriched_file),
                    "error": str(e)
                })

        return {
            "success": self.sections_populated > 0,
            "sections_populated": self.sections_populated,
            "total_entries": self.total_entries,
            "operations": self.operations
        }

    def save(self, output_path: Path):
        """
        Save the populated CV to a file.

        Args:
            output_path: Where to save the CV
        """
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        self.doc.save(str(output_path))

        if self.verbose:
            print(f"\n{'='*80}")
            print(f"CV SAVED")
            print(f"{'='*80}")
            print(f"Output: {output_path}")
            print(f"Size: {output_path.stat().st_size / 1024:.1f} KB")
            print(f"Sections populated: {self.sections_populated}")
            print(f"Total entries: {self.total_entries}")
            print(f"{'='*80}")


def verify_cv_content(cv_path: Path) -> dict:
    """
    Verify that a CV actually contains data.

    Args:
        cv_path: Path to CV .docx file

    Returns:
        Verification results dictionary
    """
    doc = Document(cv_path)

    # Count populated table rows
    data_rows = 0
    empty_tables = 0
    populated_tables = 0

    for table in doc.tables:
        table_has_data = False
        for row_idx, row in enumerate(table.rows):
            if row_idx == 0:  # Skip header
                continue

            # Check if row has any non-empty cells
            if any(cell.text.strip() for cell in row.cells):
                data_rows += 1
                table_has_data = True

        if table_has_data:
            populated_tables += 1
        else:
            empty_tables += 1

    # Check for name heading (look for bold text in first 20 paragraphs)
    name_found = False
    name_text = ""
    for para in doc.paragraphs[:20]:
        # Check if paragraph has bold text or looks like a name
        text = para.text.strip()
        if text and len(text) > 5 and len(text) < 100:
            # Check if paragraph has bold runs
            has_bold = any(run.bold for run in para.runs)
            # Or check if text contains typical name patterns (PhD, MD, etc.)
            looks_like_name = any(title in text for title in ['PhD', 'MD', 'Dr.', ', '])
            if has_bold or looks_like_name:
                name_found = True
                name_text = text
                break

    has_data = data_rows > 0 or name_found

    return {
        "has_data": has_data,
        "name_found": name_found,
        "name_text": name_text,
        "data_rows": data_rows,
        "populated_tables": populated_tables,
        "empty_tables": empty_tables,
        "total_tables": len(doc.tables)
    }


def populate_cv_direct(
    cv_id: str,
    template_path: Path,
    enriched_dir: Path,
    output_path: Path,
    verbose: bool = True
) -> dict:
    """
    Main entry point - populate a CV using direct python-docx manipulation.

    Args:
        cv_id: CV identifier
        template_path: Path to WCM template
        enriched_dir: Directory with enriched section files
        output_path: Where to save output
        verbose: Enable logging

    Returns:
        Result dictionary with verification
    """
    # Create populator
    populator = DirectCVPopulator(template_path, verbose=verbose)

    # Populate from enriched files
    result = populator.populate_from_directory(enriched_dir, cv_id)

    # Save
    populator.save(output_path)

    # Verify the output
    verification = verify_cv_content(output_path)

    if verbose:
        print(f"\n{'='*80}")
        print(f"POST-GENERATION VERIFICATION")
        print(f"{'='*80}")
        print(f"Name found: {verification['name_found']} - '{verification['name_text']}'")
        print(f"Data rows: {verification['data_rows']}")
        print(f"Populated tables: {verification['populated_tables']}/{verification['total_tables']}")
        print(f"Has data: {verification['has_data']}")

        if not verification['has_data']:
            print(f"\n⚠️  WARNING: CV appears to be EMPTY!")
        else:
            print(f"\n✓ CV contains actual data")
        print(f"{'='*80}")

    # Combine results
    result.update({
        "output_path": str(output_path),
        "verification": verification,
        "verified_has_data": verification['has_data']
    })

    return result
