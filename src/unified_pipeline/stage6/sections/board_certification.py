"""Section F2: board certification (#398).

Three columns -- specialty, certificate number, date -- and the section is small
enough that all of its difficulty is in one place: several certifications
routinely arrive merged into a single entry.

The writer detects that by splitting `certificate_number` on commas and
semicolons. More than one number means the entry describes more than one
certification, and its `extracted_fields` are a flattened mixture that cannot be
rendered as a single row, so it is handed to
`_parse_and_add_multiple_certifications`, which goes back to the raw text. The
same helper is the fallback when there are no structured fields at all.

That parser cannot rely on order or on separators, because the source is a table
that lost its columns, so it classifies each token by shape and rebuilds the
columns by position: a bare 4-digit token is a year, an all-digits-and-hyphens
token is a certificate number, "MOC" marks a maintenance-of-certification date,
and anything not starting with a digit is a specialty. Header rows ("Name of
Specialty", "Board Certificate #", "Date of Certification") are dropped first,
since extraction keeps them.

The section header is located by scanning for a paragraph that IS "Board
Certification" rather than by `_find_paragraph_with_text`. The template's parent
header reads "LICENSURE, BOARD CERTIFICATION", so a substring search matches it
first and would attach the certifications to the licensure table.
"""
import re
from typing import Dict, List, Literal, Optional

from ..formatting import _clear_table_data, _set_font, format_date_for_section
from unified_pipeline.core.render_check import entry_lines

# Token shapes for `_classify_cert_token`. Named module constants because each
# used to be written out twice, once per branch of
# `_parse_and_add_multiple_certifications` (#572).
YEAR_PATTERN = re.compile(r'^\d{4}$')
CERTIFICATE_NUMBER_PATTERN = re.compile(r'^[\d\-]+$')
# `\d`, not str.isdigit(): isdigit() also accepts superscripts and circled
# digits, which the original inline `re.match(r'^\d', ...)` did not.
_LEADING_DIGIT = re.compile(r'^\d')

CertTokenType = Optional[Literal['year', 'cert_number', 'specialty']]


def _classify_cert_token(token: str) -> CertTokenType:
    """Classify one token of a flattened board-certification table.

    A bare 4-digit token is a year, an all-digits-and-hyphens token is a
    certificate number, "MOC" marks a maintenance-of-certification date (kept
    with the years), and anything not starting with a digit is a specialty.
    Empty tokens and digit-led tokens matching none of the shapes classify as
    None and are dropped.
    """
    if YEAR_PATTERN.match(token):
        return 'year'
    if CERTIFICATE_NUMBER_PATTERN.match(token):
        return 'cert_number'
    if 'MOC' in token:
        return 'year'
    if token and not _LEADING_DIGIT.match(token):
        return 'specialty'
    return None


class BoardCertificationSection:
    """Section F2 writers, mixed into `WCMTemplateGenerator`."""

    def _fill_board_certification(self, entries: List[Dict]):
        """Fill F2. BOARD CERTIFICATION section.

        WCM template has table with: Name of specialty | Board Certificate # | Date of Certification

        Handles cases where multiple certifications are merged into one entry.
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Board Certification ({len(entries)} entries)...")

        # Find Board Certification section - need to find the subsection header,
        # not the main "LICENSURE, BOARD CERTIFICATION" section header
        section_idx = None
        for i, para in enumerate(self.doc.paragraphs):
            text = para.text.strip()
            # Look for exact match or starts with "Board Certification"
            if text == "Board Certification" or text.startswith("Board Certification:"):
                section_idx = i
                break
        if section_idx is None:
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            return

        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        for entry in entries:
            fields = entry.get('extracted_fields', {}) or {}
            original_text = entry.get('text', '')

            certifying_board = fields.get('certifying_board', '')
            certificate_number = fields.get('certificate_number', '')
            year_certified = fields.get('year_certified', '')
            recertification_date = fields.get('recertification_date', '')

            # Handle certificate_number being a list (from LLM extraction) or string
            if isinstance(certificate_number, list):
                cert_numbers = [str(n).strip() for n in certificate_number if n]
                certificate_number = ', '.join(cert_numbers)  # Also update for single cert case
            else:
                certificate_number = str(certificate_number) if certificate_number else ''

            # Check if we have structured fields
            if certifying_board or certificate_number:
                # Check if multiple certifications were merged (multiple cert numbers)
                cert_numbers = []
                if certificate_number:
                    # Split on semicolons or commas
                    cert_numbers = [n.strip() for n in certificate_number.replace(';', ',').split(',') if n.strip()]

                if len(cert_numbers) > 1:
                    # Multiple certifications merged - try to parse from original text
                    # Original text pattern: "Specialty1\n\nSpecialty2 | CertNum1\n\nCertNum2 | Year1\nYear2"
                    self._parse_and_add_multiple_certifications(table, original_text, entry)
                else:
                    # Single certification - format dates as yyyy-yyyy per WCM template
                    # Use start_date/end_date if available, fall back to year_certified/recertification_date
                    start_date = fields.get('start_date') or year_certified
                    end_date = fields.get('end_date') or recertification_date

                    start_fmt = format_date_for_section(str(start_date), 'F2') if start_date else ''
                    end_fmt = format_date_for_section(str(end_date), 'F2') if end_date else ''

                    # Build date range string
                    if start_fmt and end_fmt:
                        if end_fmt.lower() == 'present':
                            date_str = f"{start_fmt}-Present"
                        elif end_fmt != start_fmt:
                            date_str = f"{start_fmt}-{end_fmt}"
                        else:
                            date_str = start_fmt
                    elif start_fmt:
                        date_str = start_fmt
                    elif end_fmt:
                        date_str = end_fmt
                    else:
                        date_str = ''

                    self._add_board_cert_row(table, certifying_board, certificate_number, date_str)
            else:
                # No structured fields - try to parse from text
                self._parse_and_add_multiple_certifications(table, original_text, entry)

    def _parse_and_add_multiple_certifications(self, table, text: str, entry: Dict):
        """Parse multiple board certifications from raw text and add rows."""
        if not text:
            return


        # Handle pipe-separated format: "Specialty | CertNum | Year"
        # First, split on newlines and filter empty lines
        lines = entry_lines(text)

        # Skip header lines
        header_keywords = ['name of specialty', 'board certificate', 'date of certification']
        lines = [l for l in lines if not any(kw in l.lower() for kw in header_keywords)]

        if not lines:
            return

        # Try to parse pipe-separated rows first
        # Format might be: "Specialty | CertNum | Year" on each line
        # Or mixed format from extraction artifacts

        specialties: list[str] = []
        cert_numbers: list[str] = []
        years: list[str] = []
        buckets: dict[str, list[str]] = {
            'specialty': specialties, 'cert_number': cert_numbers, 'year': years,
        }

        for line in lines:
            # A pipe-separated line carries several tokens; a bare line is one
            tokens = [p.strip() for p in line.split('|')] if '|' in line else [line.strip()]
            for token in tokens:
                token_type = _classify_cert_token(token)
                if token_type is not None:
                    buckets[token_type].append(token)

        # Match specialties with cert numbers (assume same order)
        num_certs = max(len(specialties), len(cert_numbers), 1)
        for i in range(num_certs):
            specialty = specialties[i] if i < len(specialties) else ''
            cert_num = cert_numbers[i] if i < len(cert_numbers) else ''
            # For years, try to match or use available
            year = ''
            if i < len(years):
                year = years[i]
            elif years:
                # Use last year if we have fewer years than certs
                year = years[-1] if i >= len(years) else years[i]

            # Format year as yyyy per WCM template
            if year:
                year = format_date_for_section(year, 'F2')

            if specialty or cert_num:
                self._add_board_cert_row(table, specialty, cert_num, year)

    def _add_board_cert_row(self, table, specialty: str, cert_number: str, dates: str):
        """Add a single board certification row to the table."""
        row = table.add_row()
        num_cols = len(row.cells)

        if num_cols >= 3:
            row.cells[0].text = specialty or ''
            row.cells[1].text = cert_number or ''
            row.cells[2].text = dates or ''
        elif num_cols >= 2:
            row.cells[0].text = specialty or ''
            row.cells[1].text = f"{cert_number} ({dates})" if dates else (cert_number or '')

        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    _set_font(run)
        self.stats['entries_inserted'] += 1
