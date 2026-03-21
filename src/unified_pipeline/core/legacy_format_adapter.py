"""
Legacy Format Adapter - Bridge Between Unified and Legacy Pipelines

This module converts unified pipeline section files (stage_3_sections/) to the
legacy format expected by populate_cv.py, ensuring ALL taxonomy-mapped data
makes it into the final WCM template.

Architecture:
    Unified Format (stage_3_sections/):
        section_{section_id}_{cv_name}_parsed.json
        {
            "document_uid": "6_8XAA_2079_Zahida",
            "section_id": "contact_information",
            "section_name": "Personal Data / Contact Information",
            "entries": [{"id": "G1-E1", "text_snippet": "...", ...}]
        }

    Legacy Format (what populate_cv.py expects):
        section_{SECTION_ID}_{cv_id}_enriched.json
        {
            "cv_id": "CV_2079_Zahida_...",
            "section_id": "A",
            "section_name": "Personal Data",
            "parsed_entries": [
                {
                    "structured_data": {...},
                    "original_text": "...",
                    "confidence": 0.9
                }
            ]
        }

Usage:
    from legacy_format_adapter import UnifiedToLegacyAdapter

    adapter = UnifiedToLegacyAdapter()
    legacy_files = adapter.convert_all_sections(
        unified_sections_dir="outputs/6_8XAA/stage_3_sections",
        cv_id="CV_2079_Zahida",
        output_dir="outputs/6_8XAA/legacy_format"
    )
"""

import json
from pathlib import Path
from typing import Dict, List, Any, Optional
import re

# Import taxonomy utilities for section code lookups
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from cv_parser.taxonomy_utils import section_id_to_code, get_section_by_code


class UnifiedToLegacyAdapter:
    """
    Converts unified pipeline section files to legacy format.

    This adapter enables the proven legacy populate_cv.py system to work
    with unified pipeline output, ensuring all ~70 WCM sections are populated.

    NOTE: Section code mappings are now retrieved from the centralized taxonomy
    via taxonomy_utils instead of hardcoded mappings. This ensures consistency
    across the entire pipeline.
    """

    def __init__(self):
        """Initialize the adapter."""
        # No longer need hardcoded mapping - using taxonomy_utils
        pass

    def convert_section_file(
        self,
        unified_file: Path,
        cv_id: str,
        output_dir: Path
    ) -> Optional[Path]:
        """
        Convert a single unified section file to legacy format.

        Args:
            unified_file: Path to unified section file
            cv_id: CV identifier (e.g., "CV_2079_Zahida")
            output_dir: Directory to write legacy-format file

        Returns:
            Path to created legacy file, or None if conversion failed
        """
        # Load unified format
        with open(unified_file, 'r') as f:
            unified_data = json.load(f)

        # Extract section info
        section_id = unified_data.get("section_id", "")
        section_name = unified_data.get("section_name", "")
        entries = unified_data.get("entries", [])

        if not entries:
            # Skip empty sections
            return None

        # Get section code from taxonomy (NEW: using centralized taxonomy)
        # First check if the file already has section_code (from new pipeline)
        section_code = unified_data.get("section_code")

        if not section_code:
            # Fallback: Look up section code from section_id using taxonomy
            section_code = section_id_to_code(section_id)

        if not section_code:
            # Last resort fallback for unmapped sections
            section_code = section_id.upper()
            print(f"  WARNING: No section code found for '{section_id}', using fallback: {section_code}")

        # Legacy section ID is the same as section code (they were always the same)
        legacy_section_id = section_code

        # Convert entries to legacy format
        legacy_entries = []
        for entry in entries:
            # Extract structured data from text_snippet using simple parsing
            text = entry.get("text_snippet", "")

            # CRITICAL: Check if entry already has LLM-parsed structured data
            if entry.get("has_structured_data") and "structured_data" in entry:
                # Use LLM-parsed structured data, but convert field names to WCM format
                raw_structured_data = entry["structured_data"]

                # Determine entity type from section_id for field name conversion
                entity_type_map = {
                    "education": "education",
                    "education_and_training": "education",
                    "doctoral_degree": "education",
                    "positions": "positions",
                    "service": "service",
                    "institutional_administration": "service",
                    "honors": "honors",
                    "memberships": "memberships",
                    "certifications": "certifications",
                    "licensure": "licensure",
                    "mentoring": "mentoring"
                }

                entity_type = entity_type_map.get(section_id, "unknown")

                # Convert field names to WCM format
                structured_data = self._convert_to_legacy_field_names(raw_structured_data, entity_type)
                print(f"    ✓ Using LLM-parsed structured data for entry {entry.get('id', '')[:10]}")
            else:
                # Fallback to simple text parsing
                structured_data = self._parse_entry_to_structured_data(text, section_id)

            # Create legacy entry structure
            legacy_entry = {
                "structured_data": structured_data,
                "original_text": text,
                "confidence": entry.get("confidence", 0.9),
                "entry_id": entry.get("id", "")
            }
            legacy_entries.append(legacy_entry)

        # Build legacy format
        legacy_data = {
            "cv_id": cv_id,
            "section_id": legacy_section_id,
            "section_name": section_name,
            "parsed_entries": legacy_entries,
            "num_entries": len(legacy_entries),
            "source": "unified_pipeline",
            "adapter_version": "1.0"
        }

        # Write to output directory
        output_dir.mkdir(parents=True, exist_ok=True)
        output_file = output_dir / f"section_{legacy_section_id}_{cv_id}_enriched.json"

        with open(output_file, 'w') as f:
            json.dump(legacy_data, f, indent=2)

        return output_file

    def _parse_entry_to_structured_data(self, text: str, section_id: str) -> Dict[str, Any]:
        """
        Parse text entry into structured data fields.

        This is a simple parser - for complex parsing, the unified pipeline
        should use specialized parsers (publications_parser, education_parser, etc.)

        Args:
            text: Entry text
            section_id: Section identifier

        Returns:
            Dictionary of structured fields
        """
        # Special handling for Section A (Personal Data / Name)
        if section_id in ["name", "contact_information", "1"]:
            # Extract name from text (may have credentials like PhD, MD)
            name = text.strip()

            return {
                "Name": name,
                "Email": "",
                "Phone": "",
                "Address": "",
                "parsed": True,
                "notes": "Name extracted from personal data section"
            }

        # For most sections, store as simple text field
        # Specialized parsers (publications, education, etc.) should be handled
        # by the entity-specific parsers in Stage 3, not here

        return {
            "text": text,
            "parsed": False,  # Indicates simple text storage
            "notes": f"Text from {section_id} section"
        }

    def _deduplicate_entries(self, entries: List[Dict], entity_type: str) -> List[Dict]:
        """
        Remove duplicate entries based on key identifying fields.

        Args:
            entries: List of legacy-formatted entries
            entity_type: Type of entity (education, positions, etc.)

        Returns:
            Deduplicated list of entries
        """
        if not entries:
            return entries

        seen = set()
        unique_entries = []

        for entry in entries:
            data = entry.get("structured_data", {})

            # Create unique key based on entity type
            if entity_type == "education":
                # Key: (Degree, Institution, Year Awarded)
                # Normalize degree to handle "BEd" vs "B.Ed" variations
                degree = data.get("Degree", "").strip().lower()
                degree = degree.replace(".", "").replace(" ", "")  # Remove periods and spaces

                institution = data.get("Institution", "").strip().lower()

                # Normalize year awarded - treat empty, "N/A", "0" as equivalent
                year = str(data.get("Year Awarded", "")).strip()
                if year in ["", "N/A", "0", "None"]:
                    year = ""  # Normalize all missing years to empty string

                # Also try to extract year from Dates Attended if year is missing
                if not year:
                    dates = data.get("Dates Attended", "")
                    if dates:
                        # Extract all 4-digit years using regex
                        import re
                        years = re.findall(r'\b\d{4}\b', dates)
                        if years:
                            year = years[-1]  # Take the last year (end year)

                key = (degree, institution, year)
            elif entity_type == "positions":
                # Key: (Title, Institution, Dates)
                key = (
                    data.get("Title", "").strip().lower(),
                    data.get("Institution", "").strip().lower(),
                    data.get("Dates", "").strip()
                )
            elif entity_type == "publications":
                # Key: (title, year, journal)
                key = (
                    data.get("title", "").strip().lower(),
                    str(data.get("year", "")).strip(),
                    data.get("journal", "").strip().lower()
                )
            elif entity_type == "grants":
                # Key: (Grant Title, Agency, Dates)
                key = (
                    data.get("Grant Title", "").strip().lower(),
                    data.get("Agency", "").strip().lower(),
                    data.get("Dates", "").strip()
                )
            else:
                # For unknown types, use JSON representation
                import json
                key = json.dumps(data, sort_keys=True)

            if key not in seen:
                seen.add(key)
                unique_entries.append(entry)

        return unique_entries

    def _extract_degree_from_program_title(self, program_title: str) -> str:
        """
        Extract degree from 'Program/Certificate Title' field.

        Examples:
            "MPhil in Biotechnology" -> "MPhil"
            "BS (Hons) Biotechnology" -> "BS"
            "MBA (Professional)" -> "MBA"

        Args:
            program_title: Program/Certificate Title string

        Returns:
            Extracted degree abbreviation
        """
        if not program_title:
            return ""

        # Split on common separators
        parts = program_title.split()
        if parts:
            # First word is usually the degree
            degree = parts[0].strip()
            # Remove trailing punctuation
            degree = degree.rstrip(',.:;')
            return degree

        return ""

    def _extract_field_from_program_title(self, program_title: str) -> str:
        """
        Extract field of study from 'Program/Certificate Title' field.

        Examples:
            "MPhil in Biotechnology" -> "Biotechnology"
            "BS (Hons) Biotechnology" -> "Biotechnology"
            "MBA (Professional)" -> "Professional"

        Args:
            program_title: Program/Certificate Title string

        Returns:
            Extracted field of study
        """
        if not program_title:
            return ""

        # Remove degree prefix
        text = program_title

        # Try to find "in" keyword
        if " in " in text:
            parts = text.split(" in ", 1)
            if len(parts) == 2:
                return parts[1].strip()

        # Try to extract from parentheses
        if "(" in text and ")" in text:
            import re
            match = re.search(r'\(([^)]+)\)', text)
            if match:
                field = match.group(1).strip()
                # Skip common non-field parentheticals
                if field.lower() not in ['hons', 'honors', 'honours']:
                    return field

        # Otherwise, take everything after first word
        parts = text.split(None, 1)
        if len(parts) == 2:
            field = parts[1].strip()
            # Remove parentheticals
            import re
            field = re.sub(r'\([^)]*\)', '', field).strip()
            return field

        return ""

    def _categorize_publications_into_subsections(self, legacy_entries: List[Dict]) -> Dict[str, Dict]:
        """
        Categorize publications into WCM subsections (S1, S2, etc.).

        Args:
            legacy_entries: List of legacy-formatted publication entries

        Returns:
            Dict mapping subsection IDs to entries
        """
        subsections = {
            'S1': {'entries': [], 'label': 'Peer-Reviewed Research Articles'},
            'S2': {'entries': [], 'label': 'Reviews and Editorials'},
            'S4': {'entries': [], 'label': 'Chapters'},
            'S5': {'entries': [], 'label': 'Books'},
            'S6': {'entries': [], 'label': 'Case Reports'},
            'S7': {'entries': [], 'label': 'Manuscripts (Submitted/In Press)'},
            'S8': {'entries': [], 'label': 'Abstracts'},
        }

        for entry in legacy_entries:
            pub_data = entry.get("structured_data", {})
            pub_type = pub_data.get('publication_type', 'journal_article').lower()

            # Map publication type to subsection
            if pub_type in ['journal_article', 'article', 'research_article']:
                subsection_id = 'S1'
            elif pub_type in ['review', 'editorial']:
                subsection_id = 'S2'
            elif pub_type in ['chapter', 'book_chapter']:
                subsection_id = 'S4'
            elif pub_type in ['book']:
                subsection_id = 'S5'
            elif pub_type in ['case_report']:
                subsection_id = 'S6'
            elif pub_type in ['preprint', 'in_press', 'submitted']:
                subsection_id = 'S7'
            elif pub_type in ['abstract', 'conference', 'conference_paper']:
                subsection_id = 'S8'
            else:
                # Default to peer-reviewed
                subsection_id = 'S1'

            subsections[subsection_id]['entries'].append(entry)

        # Remove empty subsections
        return {k: v for k, v in subsections.items() if v['entries']}

    def _convert_to_legacy_field_names(self, item: Dict[str, Any], entity_type: str) -> Dict[str, Any]:
        """
        Convert unified parser field names to legacy format field names.

        Args:
            item: Parsed item with lowercase field names
            entity_type: Type of entity (education, positions, grants, publications)

        Returns:
            Dictionary with legacy field names
        """
        if entity_type == "education":
            # B1 expects: Degree, Field of Study, Institution, City, State/Province, Country, Dates Attended, Year Awarded

            # Handle multiple field name formats from different parsers
            # Format 1 (unified): lowercase fields (degree, major_field, institution)
            # Format 2 (extractor): Title Case fields (Degree, Field of Study, Institution)
            # Format 3 (alternative): Program/Certificate Title, Institution/Provider

            # Get degree - try multiple field names
            degree = (item.get("degree", "") or
                     item.get("Degree", "") or
                     self._extract_degree_from_program_title(item.get("Program/Certificate Title", "")))

            # Get field of study
            field = (item.get("major_field", "") or
                    item.get("Field of Study", "") or
                    self._extract_field_from_program_title(item.get("Program/Certificate Title", "")))

            # Get institution
            institution = (item.get("institution", "") or
                          item.get("Institution", "") or
                          item.get("Institution/Provider", ""))

            # Get location - try both formats
            location = item.get("location", "")
            city = item.get("City", "")
            state = item.get("State/Province", "")
            country = item.get("Country", "")

            # If we have location string, parse it
            if location and not city:
                parts = [p.strip() for p in location.split(',')]
                if len(parts) == 1:
                    city = parts[0]
                elif len(parts) == 2:
                    city, state = parts if len(parts[1]) <= 3 else (parts[0], parts[1])
                    if len(parts[1]) > 3:
                        city, country = parts
                elif len(parts) >= 3:
                    city, state, country = parts[0], parts[1], parts[2]

            # Get dates - try multiple formats
            start_year = str(item.get("start_year", "")) if item.get("start_year") else ""
            end_year = str(item.get("end_year", "")) if item.get("end_year") else ""
            year_awarded = str(item.get("Year Awarded", "")) if item.get("Year Awarded") else ""
            dates_attended_str = item.get("Dates Attended", "")

            # Build dates_attended
            if dates_attended_str:
                dates_attended = dates_attended_str
                # Extract year_awarded from dates if not already set
                if not year_awarded and '-' in dates_attended_str:
                    parts = dates_attended_str.split('-')
                    if len(parts) == 2:
                        year_awarded = parts[1].strip()
                elif not year_awarded:
                    year_awarded = dates_attended_str.strip()
            elif start_year and end_year:
                dates_attended = f"{start_year}-{end_year}"
                if not year_awarded:
                    year_awarded = end_year
            elif end_year:
                dates_attended = end_year
                if not year_awarded:
                    year_awarded = end_year
            elif start_year:
                dates_attended = start_year
            else:
                dates_attended = ""

            # Final year_awarded fallback
            if not year_awarded:
                year_awarded = end_year

            return {
                "Degree": degree,
                "Field of Study": field,
                "Institution": institution,
                "City": city,
                "State/Province": state,
                "Country": country,
                "Dates Attended": dates_attended,
                "Year Awarded": year_awarded
            }

        elif entity_type == "positions":
            # D1 expects: Title, Institution, City, State/Province, Country, Dates

            # Get title - try multiple field names
            title = item.get("title", "") or item.get("Title", "")

            # Get institution - try multiple field names
            institution = item.get("institution", "") or item.get("Institution", "")

            # Get location
            location = item.get("location", "")
            city = item.get("City", "")
            state = item.get("State/Province", "")
            country = item.get("Country", "")

            # Parse location string if we don't have individual fields
            if location and not city:
                parts = [p.strip() for p in location.split(',')]
                if len(parts) == 1:
                    city = parts[0]
                elif len(parts) == 2:
                    city, state = parts if len(parts[1]) <= 3 else (parts[0], parts[1])
                    if len(parts[1]) > 3:
                        city, country = parts
                elif len(parts) >= 3:
                    city, state, country = parts[0], parts[1], parts[2]

            # Get dates - handle multiple formats
            dates_str = item.get("Dates", "")
            if dates_str:
                # Already has "Dates" field - just clean it
                dates = dates_str
            else:
                # Build from start_year/end_year
                start_year = item.get("start_year", "")
                end_year = item.get("end_year", "Present" if item.get("is_current") else "")
                dates = f"{start_year}-{end_year}" if start_year else ""

            # Replace 9999 sentinel value with blank but keep the dash (e.g., "2019-9999" → "2019-")
            dates = dates.replace("9999", "").replace("Present", "")

            return {
                "Title": title,
                "Institution": institution,
                "City": city,
                "State/Province": state,
                "Country": country,
                "Dates": dates
            }

        elif entity_type == "grants":
            # M expects: Grant Title, Agency, Role, Dates, Amount
            start_year = item.get("start_year", "")
            end_year = item.get("end_year", "")
            dates = f"{start_year}-{end_year}" if start_year and end_year else end_year or start_year or ""

            return {
                "Grant Title": item.get("title", item.get("grant_title", "")),
                "Agency": item.get("agency", item.get("funding_agency", "")),
                "Role": item.get("role", item.get("investigator_role", "")),
                "Dates": dates,
                "Amount": item.get("amount", "")
            }

        elif entity_type == "publications":
            # Publications need special handling: authors must be a STRING, not a list
            authors = item.get("authors", [])
            if isinstance(authors, list):
                # Convert list to comma-separated string
                authors_str = ", ".join(authors)
            else:
                authors_str = str(authors)

            # Return publication with authors as string
            result = item.copy()
            result["authors"] = authors_str
            return result

        elif entity_type == "service":
            # O expects WCM field names:
            # Role/Position, Department/Division, Institution, City, State/Province, Country, Dates, Scope/Description

            # Map role to Role/Position
            role = item.get("role", item.get("Role/Position", ""))

            # Map committee_or_activity to Department/Division
            dept = item.get("committee_or_activity", item.get("Department/Division", ""))

            # Map organization to Institution
            org = item.get("organization", item.get("Institution", ""))

            # Location fields (usually empty for service)
            city = item.get("City", "")
            state = item.get("State/Province", "")
            country = item.get("Country", "")

            # Format dates from start_year/end_year
            start_year = item.get("start_year", 0)
            end_year = item.get("end_year", 0)

            if start_year and start_year > 0:
                if end_year and end_year > 0:
                    dates = f"{start_year}–{end_year}"
                else:
                    dates = f"{start_year}–Present"
            else:
                dates = item.get("Dates", "")

            # Map service_type to Scope/Description
            service_type = item.get("service_type", "")
            scope = service_type.replace("_", " ").title() if service_type and service_type != "other" else item.get("Scope/Description", "")

            return {
                "Role/Position": role,
                "Department/Division": dept,
                "Institution": org,
                "City": city,
                "State/Province": state,
                "Country": country,
                "Dates": dates,
                "Scope/Description": scope
            }

        elif entity_type == "honors":
            # H expects: Award Name, Granting Organization, Date (yyyy)
            award_name = item.get("award_name", item.get("Award Name", ""))
            organization = item.get("organization", item.get("Granting Organization", ""))
            year = item.get("year_awarded", item.get("Date (yyyy)", ""))

            return {
                "Award Name": award_name,
                "Granting Organization": organization,
                "Date (yyyy)": str(year) if year and year > 0 else ""
            }

        elif entity_type == "memberships":
            # I expects: Organization, Dates (yyyy–yyyy)
            organization = item.get("organization", item.get("Organization", ""))
            start_year = item.get("start_year", 0)
            end_year = item.get("end_year", 0)

            if start_year and start_year > 0:
                if end_year and end_year > 0:
                    dates = f"{start_year}–{end_year}"
                else:
                    dates = f"{start_year}–Present"
            else:
                dates = item.get("Dates (yyyy–yyyy)", "")

            return {
                "Organization": organization,
                "Dates (yyyy–yyyy)": dates
            }

        elif entity_type == "certifications":
            # F2 expects: Full Name of Board, Certificate #, Dates of Certification (yyyy–yyyy)
            board_name = item.get("board_name", item.get("Full Name of Board", ""))
            cert_number = item.get("certificate_number", item.get("Certificate #", ""))
            start_year = item.get("start_year", 0)
            end_year = item.get("end_year", 0)

            if start_year and start_year > 0:
                if end_year and end_year > 0:
                    dates = f"{start_year}–{end_year}"
                else:
                    dates = f"{start_year}–Present"
            else:
                dates = item.get("Dates of Certification (yyyy–yyyy)", "")

            return {
                "Full Name of Board": board_name,
                "Certificate #": cert_number,
                "Dates of Certification (yyyy–yyyy)": dates
            }

        elif entity_type == "licensure":
            # F1 expects: State, Number, Date of issue, Date of last registration
            state = item.get("state", item.get("State", ""))
            license_number = item.get("license_number", item.get("Number", ""))
            date_issued = item.get("date_issued", item.get("Date of issue", ""))
            date_renewed = item.get("date_renewed", item.get("Date of last registration", ""))

            return {
                "State": state,
                "Number": license_number,
                "Date of issue": str(date_issued) if date_issued else "",
                "Date of last registration": str(date_renewed) if date_renewed else ""
            }

        elif entity_type == "mentoring":
            # N3/N4 expect: Name, Site/Position, Expected Period, Project/Accomplishments, Goals/Current Position
            mentee_name = item.get("mentee_name", item.get("Name", ""))
            current_position = item.get("current_position", item.get("Site/Position", ""))
            start_year = item.get("start_year", 0)
            end_year = item.get("end_year", 0)

            if start_year and start_year > 0:
                if end_year and end_year > 0:
                    period = f"{start_year}–{end_year}"
                else:
                    period = f"{start_year}–Present"
            else:
                period = item.get("Expected Period", "")

            project = item.get("project", item.get("Project/Accomplishments", ""))

            return {
                "Name": mentee_name,
                "Site/Position": current_position,
                "Expected Period": period,
                "Project/Accomplishments": project,
                "Goals/Current Position": ""  # Usually filled in manually
            }

        else:
            # For other types, return as-is
            return item

    def convert_all_sections(
        self,
        unified_sections_dir: Path,
        cv_id: str,
        output_dir: Path,
        verbose: bool = True
    ) -> List[Path]:
        """
        Convert all unified section files to legacy format.

        Args:
            unified_sections_dir: Directory containing section_*.json files
            cv_id: CV identifier
            output_dir: Directory to write legacy files
            verbose: Print progress

        Returns:
            List of created legacy file paths
        """
        from datetime import datetime

        adapter_start = datetime.now()
        print(f"[TRACE-ADAPTER] convert_all_sections() called at: {adapter_start.strftime('%H:%M:%S.%f')[:-3]}")

        unified_sections_dir = Path(unified_sections_dir)
        output_dir = Path(output_dir)

        print(f"[TRACE-ADAPTER] unified_sections_dir: {unified_sections_dir}")
        print(f"[TRACE-ADAPTER] output_dir: {output_dir}")
        print(f"[TRACE-ADAPTER] cv_id: {cv_id}")
        print(f"[TRACE-ADAPTER] unified_sections_dir exists: {unified_sections_dir.exists()}")

        # Find all section files (match both old and new naming conventions)
        # Old: section_{section_id}_{cv_name}_parsed.json
        # New: {code}_{section_id}_{cv_name}_parsed.json
        glob_pattern = "*_parsed.json"
        print(f"[TRACE-ADAPTER] Glob pattern: {glob_pattern}")

        glob_start = datetime.now()
        section_files = sorted(unified_sections_dir.glob(glob_pattern))
        glob_end = datetime.now()

        print(f"[TRACE-ADAPTER] Glob took: {(glob_end - glob_start).total_seconds():.3f}s")
        print(f"[TRACE-ADAPTER] Found {len(section_files)} files matching pattern:")
        for f in section_files:
            print(f"[TRACE-ADAPTER]   - {f.name}")

        # Ensure output directory exists
        output_dir.mkdir(parents=True, exist_ok=True)
        print(f"[TRACE-ADAPTER] Created/verified output_dir: {output_dir}")

        if verbose:
            print(f"Converting {len(section_files)} section files to legacy format...")
            print()

        created_files = []
        for idx, section_file in enumerate(section_files, 1):
            if verbose:
                print(f"  Converting {section_file.name}...")

            convert_start = datetime.now()
            print(f"[TRACE-ADAPTER] [{idx}/{len(section_files)}] Converting: {section_file.name}")

            legacy_file = self.convert_section_file(section_file, cv_id, output_dir)

            convert_end = datetime.now()
            print(f"[TRACE-ADAPTER] Conversion took: {(convert_end - convert_start).total_seconds():.3f}s")

            if legacy_file:
                created_files.append(legacy_file)
                print(f"[TRACE-ADAPTER] Created: {legacy_file} (exists: {legacy_file.exists()})")
                if verbose:
                    print(f"    → {legacy_file.name}")
            else:
                print(f"[TRACE-ADAPTER] Returned None (skipped)")
                if verbose:
                    print(f"    ⊘ Skipped (empty)")

        adapter_end = datetime.now()
        print(f"[TRACE-ADAPTER] convert_all_sections() completed at: {adapter_end.strftime('%H:%M:%S.%f')[:-3]}")
        print(f"[TRACE-ADAPTER] Total adapter time: {(adapter_end - adapter_start).total_seconds():.3f}s")
        print(f"[TRACE-ADAPTER] Created {len(created_files)} files total")

        if verbose:
            print()
            print(f"✓ Created {len(created_files)} legacy section files")

        return created_files

    def convert_entity_parser_files(
        self,
        entity_files: Dict[str, Path],
        cv_id: str,
        output_dir: Path,
        verbose: bool = True
    ) -> List[Path]:
        """
        Convert entity-specific parser files (publications, education, etc.)
        to legacy enriched format.

        These are already well-parsed, so we just need format conversion.

        Args:
            entity_files: Dict mapping entity type to file path
                         {"publications": Path(...), "education": Path(...), ...}
            cv_id: CV identifier
            output_dir: Directory to write legacy files
            verbose: Print progress

        Returns:
            List of created legacy file paths
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        # Section ID mapping for entity types
        entity_to_section = {
            "publications": "S",
            "education": "B1",      # B1 = Academic Degree (B itself is not supported)
            "positions": "D1",      # D1 = Academic Appointments (D itself is not supported)
            "grants": "M",          # M = Research (includes grants as subsections)
            "service": "O",         # O = Service
            "certifications": "C",  # C = Certificates, Other Degrees
            "honors": "I",          # I = Honors and Awards
            "memberships": "P",     # P = Professional Memberships
            "licensure": "F",       # F = Licensure
            "mentoring": "N"        # N = Mentoring and Advising
        }

        created_files = []

        for entity_type, file_path in entity_files.items():
            if not file_path or not Path(file_path).exists():
                continue

            # Load entity data
            with open(file_path, 'r') as f:
                data = json.load(f)

            items = data.get(entity_type, [])

            if not items:
                continue

            # Convert to legacy enriched format
            legacy_entries = []
            for item in items:
                # Entity parsers produce structured data with lowercase keys
                # Legacy system needs Title Case field names
                structured_data = self._convert_to_legacy_field_names(item, entity_type)

                legacy_entry = {
                    "structured_data": structured_data,
                    "original_text": item.get("original_text", ""),
                    "confidence": item.get("confidence", 0.9),
                    "entry_id": item.get("entry_id", "")
                }
                legacy_entries.append(legacy_entry)

            # Deduplicate entries (multiple extractors may produce duplicates)
            legacy_entries = self._deduplicate_entries(legacy_entries, entity_type)

            # Get legacy section ID
            legacy_section_id = entity_to_section.get(entity_type, entity_type.upper())

            # Build legacy file
            # Special handling for publications - needs subsections structure
            if entity_type == "publications":
                # Categorize into subsections
                subsections = self._categorize_publications_into_subsections(legacy_entries)
                legacy_data = {
                    "cv_id": cv_id,
                    "section_id": legacy_section_id,
                    "section_name": "Bibliography",
                    "subsections": subsections,
                    "num_entries": len(legacy_entries),
                    "source": "unified_pipeline_entity_parser",
                    "adapter_version": "1.0"
                }
            else:
                legacy_data = {
                    "cv_id": cv_id,
                    "section_id": legacy_section_id,
                    "section_name": entity_type.capitalize(),
                    "parsed_entries": legacy_entries,
                    "num_entries": len(legacy_entries),
                    "source": "unified_pipeline_entity_parser",
                    "adapter_version": "1.0"
                }

            # Write file
            output_file = output_dir / f"section_{legacy_section_id}_{cv_id}_enriched.json"

            with open(output_file, 'w') as f:
                json.dump(legacy_data, f, indent=2)

            created_files.append(output_file)

            if verbose:
                print(f"  ✓ Converted {entity_type}: {len(legacy_entries)} entries → {output_file.name}")

        return created_files


def extract_cv_id_from_document_uid(document_uid: str) -> str:
    """
    Extract legacy CV ID format from unified document_uid.

    Examples:
        "6_8XAA_2079_Zahida" → "CV_2079_Zahida"
        "CV_2068_Yount_Kathryn_PhD" → "CV_2068_Yount_Kathryn_PhD"

    Args:
        document_uid: Document UID from unified pipeline

    Returns:
        Legacy CV ID format
    """
    # If already in CV_ format, return as-is
    if document_uid.startswith("CV_"):
        return document_uid

    # Otherwise, extract meaningful parts
    # Pattern: "{run_id}_{number}_{name}"
    parts = document_uid.split("_")

    if len(parts) >= 3:
        # Skip first part (run_id), use rest
        return "CV_" + "_".join(parts[1:])

    # Fallback: just prepend CV_
    return "CV_" + document_uid


if __name__ == "__main__":
    """Test the adapter with sample data."""
    import sys

    if len(sys.argv) < 2:
        print("Legacy Format Adapter - Unified to Legacy Bridge")
        print()
        print("Usage:")
        print("  python legacy_format_adapter.py <unified_sections_dir>")
        print()
        print("Example:")
        print("  python legacy_format_adapter.py web_interface/outputs/6_8XAA/stage_3_sections")
        sys.exit(1)

    unified_dir = Path(sys.argv[1])

    if not unified_dir.exists():
        print(f"Error: Directory not found: {unified_dir}")
        sys.exit(1)

    # Try to extract CV ID from directory structure
    cv_name = unified_dir.parent.name
    cv_id = extract_cv_id_from_document_uid(cv_name)

    print(f"Detected CV ID: {cv_id}")
    print()

    # Create output directory
    output_dir = unified_dir.parent / "legacy_format"

    # Convert
    adapter = UnifiedToLegacyAdapter()
    files = adapter.convert_all_sections(unified_dir, cv_id, output_dir, verbose=True)

    print()
    print(f"✓ Conversion complete!")
    print(f"  Created {len(files)} legacy section files in: {output_dir}")
