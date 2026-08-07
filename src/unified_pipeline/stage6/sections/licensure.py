"""Section F1: licensure, plus the DEA and NPI numbers (#398).

The licensure table is four columns -- state, number, date of issue, date of
last registration -- but the section's real work is that three different kinds
of identifier arrive under one taxonomy code. A state medical licence belongs in
the table; a DEA registration and an NPI belong in a separate two-row table
further down the template, which is why `_fill_dea_npi` lives here rather than
anywhere else: it is the second half of one section's output.

Sorting the two out is done by SHAPE first and text second, because CVs label
them inconsistently or not at all:

- an NPI is 10 or 11 digits;
- a DEA number is two letters followed by seven alphanumerics;
- failing that, the word "NPI" or "DEA" anywhere in the entry's raw text.

Either match consumes the entry -- it is pulled out of the licence list, not
copied -- so a DEA number never also appears as a row in the state table. The
unstructured fallback is guarded by the same two keywords for the same reason.

`_fill_dea_npi` finds its table by scanning every table in the document for a
cell containing "DEA number", not by position. The template revision history has
moved it, and matching on content survives that. It then matches each row by
its own label, so a template that lists NPI first still fills correctly.

The row writer copes with a template whose licensure table has been narrowed:
four columns get the full record, two get state and number, and anything
narrower is left alone rather than raising.

`_fill_licensure` is pinned on the class surface by
`tests/test_stage6_import_surface.py`, and `tests/test_stage6_unrendered_recovery.py`
calls it on an instance. The mixin keeps it resolving through the MRO, which is
what that guard checks.
"""
import re
from typing import Dict, List

from ..formatting import _clear_table_data, _set_font, format_date_for_section
from ..sorting import sort_entries_reverse_chronological


class LicensureSection:
    """Section F1 writers, mixed into `WCMTemplateGenerator`."""

    def _fill_licensure(self, entries: List[Dict]):
        """Fill F1. LICENSURE section.

        WCM template has table with: State | Number | Date of issue | Date of last registration
        Also fills DEA and NPI numbers in a separate table (Table 9).
        """

        if not entries:
            return

        if self.verbose:
            print(f"Filling Licensure ({len(entries)} entries)...")

        # Find Licensure section
        section_idx = self._find_paragraph_with_text("Licensure")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("LICENSURE")
        if section_idx is None:
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            return

        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        sorted_entries = sort_entries_reverse_chronological(entries)

        # Track NPI and DEA numbers to fill separately
        npi_number = None
        dea_number = None
        regular_licenses = []

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {}) or {}
            original_text = entry.get('text', '')

            state = fields.get('state_country') or fields.get('state') or fields.get('jurisdiction') or ''
            license_number = fields.get('license_number') or fields.get('number') or ''
            issue_date = fields.get('issue_date') or fields.get('date') or ''
            expiration_date = fields.get('expiration_date') or ''

            # Detect NPI number: 10 digits, or text mentions "NPI"
            if license_number and (re.match(r'^\d{10,11}$', license_number) or
                                   'NPI' in original_text.upper()):
                npi_number = license_number
                continue

            # Detect DEA number: 2 letters + 7 alphanumeric, or text mentions "DEA"
            if license_number and (re.match(r'^[A-Za-z]{2}[A-Za-z0-9]{7}$', license_number) or
                                   'DEA' in original_text.upper()):
                dea_number = license_number
                continue

            # Regular license entry - format dates as mm/dd/yyyy per WCM template
            if state or license_number:
                regular_licenses.append({
                    'state': state,
                    'license_number': license_number,
                    'issue_date': format_date_for_section(issue_date, 'F1') if issue_date else '',
                    'expiration_date': format_date_for_section(expiration_date, 'F1') if expiration_date else ''
                })
            elif original_text and not any(kw in original_text.upper() for kw in ['NPI', 'DEA']):
                # Fallback to raw text for unstructured entries
                regular_licenses.append({
                    'state': original_text[:100],
                    'license_number': '',
                    'issue_date': '',
                    'expiration_date': ''
                })

        # Fill regular licenses table
        for lic in regular_licenses:
            row = table.add_row()
            num_cols = len(row.cells)
            if num_cols >= 4:
                row.cells[0].text = lic['state'] or ''
                row.cells[1].text = lic['license_number'] or ''
                row.cells[2].text = lic['issue_date'] or ''
                row.cells[3].text = lic['expiration_date'] or ''
            elif num_cols >= 2:
                row.cells[0].text = lic['state'] or ''
                row.cells[1].text = lic['license_number'] or ''

            for cell in row.cells:
                for para in cell.paragraphs:
                    for run in para.runs:
                        _set_font(run)
            self.stats['entries_inserted'] += 1

        # Fill DEA/NPI table (Table 9 in template)
        self._fill_dea_npi(dea_number, npi_number)

    def _fill_dea_npi(self, dea_number: str, npi_number: str):
        """Fill DEA and NPI numbers in their dedicated table.

        The WCM template has a 2-row table:
        Row 0: DEA number: (optional) | [value]
        Row 1: NPI number: (optional) | [value]
        """
        if not dea_number and not npi_number:
            return

        # Find the DEA/NPI table by looking for a table containing "DEA number"
        dea_npi_table = None
        for table in self.doc.tables:
            for row in table.rows:
                for cell in row.cells:
                    if 'DEA number' in cell.text:
                        dea_npi_table = table
                        break
                if dea_npi_table:
                    break
            if dea_npi_table:
                break

        if not dea_npi_table:
            return

        # Fill in the values
        for row in dea_npi_table.rows:
            if len(row.cells) >= 2:
                label = row.cells[0].text.lower()
                if 'dea' in label and dea_number:
                    row.cells[1].text = dea_number
                    for para in row.cells[1].paragraphs:
                        for run in para.runs:
                            _set_font(run)
                elif 'npi' in label and npi_number:
                    row.cells[1].text = npi_number
                    for para in row.cells[1].paragraphs:
                        for run in para.runs:
                            _set_font(run)
