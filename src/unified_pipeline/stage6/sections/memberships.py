"""Section I: professional organizations and society memberships (#398).

Two columns -- organization, dates -- and one recurring shape problem. Source
CVs write memberships as a two-column table, and extraction flattens that into a
single entry whose lines interleave the columns:

    "Member\nElected Member | Org1\nOrg2 | date1\ndate2"

More than two lines is the signal to hand the entry to
`_parse_multi_membership_entry` and emit a row per membership rather than trust
the `extracted_fields`, which describe only the first.

Either way the organization cell is built the same: the membership type is
prefixed only when it is not already inside the organization name, so "Fellow,
American College of Surgeons" does not become "Fellow, Fellow of the American
College of Surgeons". An entry with no `organization` field falls back to its
first 150 characters of raw text -- a truncated membership is still a
membership; a missing one is a loss.

Finding the table takes two tries. The heading text has changed across template
revisions ("PROFESSIONAL ORGANIZATIONS", "SOCIETY MEMBERSHIPS", "MEMBERSHIPS"),
and if none of them match, the fallback searches for a table whose first cell
says "Organization" -- then checks that its SECOND column header says "Date"
before writing into it, because "Organization" alone also matches tables
belonging to other sections.

Extraction keeps the source table's own header row, so header-shaped entries are
dropped explicitly rather than rendered as a membership called "Organization".

`_add_table_row` is the generic row writer, and it lives here because this is
the only section that calls it. Everything else either writes cells directly or
uses one of the shared `_add_table_row_with_*` variants, which stay on
`WCMTemplateGenerator`.
"""
import sys
from typing import Dict, List

try:
    from docx.table import Table
except ImportError:  # pragma: no cover - mirrors stage_6_word_template
    print("Error: python-docx not installed. Install with: pip install python-docx lxml")
    sys.exit(1)

from ..formatting import (
    _clear_table_data,
    _set_cell_vertical_alignment,
    _set_font,
    format_date_range,
)
from ..parsing import _is_table_header_entry, _parse_multi_membership_entry
from ..sorting import sort_entries_reverse_chronological


class MembershipsSection:
    """Section I writers, mixed into `WCMTemplateGenerator`."""

    def _fill_memberships(self, entries: List[Dict]):
        """Fill I. PROFESSIONAL ORGANIZATIONS AND SOCIETY MEMBERSHIPS section.

        Entries have fields: organization, membership_type, start_date, end_date
        Uses table with columns: Organization, Date (yyyy-yyyy)
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Memberships ({len(entries)} entries)...")

        # Find the MEMBERSHIPS section
        memberships_idx = self._find_paragraph_with_text("PROFESSIONAL ORGANIZATIONS")
        if memberships_idx is None:
            memberships_idx = self._find_paragraph_with_text("SOCIETY MEMBERSHIPS")
        if memberships_idx is None:
            memberships_idx = self._find_paragraph_with_text("MEMBERSHIPS")
        if memberships_idx is None:
            return

        # Find the table after the section header
        table = self._find_table_after_paragraph(memberships_idx)
        if not table:
            # Fall back to finding table with "Organization" header
            table = self._find_table_with_cell_text("Organization")
            if table and "Date" not in table.rows[0].cells[1].text:
                table = None  # Wrong table

        if not table:
            if self.verbose:
                print("  Warning: Could not find memberships table")
            return

        # Clear existing data rows
        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        # Sort by date (most recent first)
        sorted_entries = sort_entries_reverse_chronological(entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {}) or {}
            original_text = entry.get('text', '')

            # Skip table header entries
            if _is_table_header_entry(original_text, ['organization', 'membership', 'society', 'date', 'member']):
                if self.verbose:
                    print(f"  Skipping header entry: '{original_text[:50]}...'")
                continue

            # Check if this entry contains multiple memberships (newline-separated)
            # Pattern: "Member\nElected Member | Org1\nOrg2 | date1\ndate2"
            lines = [l.strip() for l in original_text.split('\n') if l.strip()]

            # Detect multi-membership pattern: multiple organization names or membership types
            if len(lines) > 2:
                # Try to parse multiple memberships
                memberships = _parse_multi_membership_entry(lines)
                if memberships:
                    for mem_type, org, dates in memberships:
                        org_text = f"{mem_type}, {org}" if mem_type and mem_type.lower() not in org.lower() else org
                        self._add_table_row(table, [org_text, dates], entry=entry)
                        self.stats['entries_inserted'] += 1
                    continue

            # Single membership - use extracted fields
            organization = fields.get('organization', '')
            membership_type = fields.get('membership_type', '')
            start_date = fields.get('start_date', '')
            end_date = fields.get('end_date', '')

            if not organization:
                organization = original_text[:150]

            # Format: Membership Type, Organization
            if membership_type and membership_type.lower() not in organization.lower():
                org_text = f"{membership_type}, {organization}"
            else:
                org_text = organization

            # Format date range for table column
            date_str = format_date_range(start_date, end_date, 'I') if (start_date or end_date) else ''

            # Add row to table
            self._add_table_row(table, [org_text, date_str], entry=entry)
            self.stats['entries_inserted'] += 1

    def _add_table_row(self, table: Table, data: List[str], is_header: bool = False, entry: Dict = None):
        """Add a row to a table with proper formatting.

        Args:
            table: The table to add to
            data: List of cell values
            is_header: Whether this is a header row
            entry: Optional entry dict - if provided, adds comments from upstream pipeline
        """
        if not table:
            return
        row = table.add_row()
        first_cell_para = None
        for i, value in enumerate(data):
            if i < len(row.cells):
                cell = row.cells[i]
                cell.text = str(value) if value else ""
                # Set vertical alignment to center (middle)
                _set_cell_vertical_alignment(cell, 'center')
                for para in cell.paragraphs:
                    if i == 0 and first_cell_para is None:
                        first_cell_para = para
                    for run in para.runs:
                        # Always 11pt Arial, bold for headers
                        _set_font(run, size=11, bold=is_header)

        # Add comments to the first cell if entry provided
        if entry and first_cell_para:
            self._add_entry_comments(first_cell_para, entry)

        self.stats['entries_inserted'] += 1
