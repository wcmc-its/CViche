"""Sections E and G: the passthrough sections (#398).

    E. EMPLOYMENT STATUS
    G. INSTITUTIONAL/HOSPITAL AFFILIATION

Every other section in the template is rebuilt from stage-4 fields. These two
are short, already structured in the source CV, and structured the same way the
WCM template wants them, so they are copied across instead -- which is what
"passthrough" means here and why the two share one module and one entry point.
`_fill_passthrough_sections` is dispatched once from `generate` and is nothing
but the two calls.

Both writers select their input by HIERARCHY rather than by taxonomy code:
neither section has a code of its own, so the only signal that an entry belongs
here is the source heading it was found under.

E writes into paragraphs, not a table. It matches on the "Label: Value" shape,
keeps the template's own label, and appends the value after a tab -- the
template line reads "Name of Current Employer(s):" and must keep reading that
way. It prefers that specific paragraph to the section header, because the
header is a heading with nothing to fill.

G writes into a table, but only after checking that it is the right one.
`_find_table_after_paragraph` returns whatever table comes next in the document,
so the first cell is tested for "hospital", "affiliation" or "primary" before
anything is cleared; a template whose affiliation section has no table falls
back to bullets under the header instead of writing into a stranger's table.

Percent effort is deliberately absent. It is in the same part of the template
and looks like it belongs, but it is filled by hand.
"""
from typing import Dict, List

from ..formatting import _clear_table_data, _set_font


class PassthroughSection:
    """Section E and G writers, mixed into `WCMTemplateGenerator`."""

    def _fill_passthrough_sections(self, all_entries: List[Dict]):
        """Fill sections that can be copied directly from source CV when format matches.

        These are short, structured sections in the WCM template that may already exist
        in the source CV in the same format. If found, we copy them directly.

        Sections handled:
        - E. EMPLOYMENT STATUS
        - G. INSTITUTIONAL/HOSPITAL AFFILIATION

        Note: PERCENT EFFORT is complex and typically filled manually.

        Args:
            all_entries: All entries from the pipeline (to search by hierarchy)
        """
        self._fill_employment_status(all_entries)
        self._fill_hospital_affiliation(all_entries)

    def _fill_employment_status(self, all_entries: List[Dict]):
        """Fill E. EMPLOYMENT STATUS section.

        Looks for entries with hierarchy containing 'EMPLOYMENT STATUS' and
        text in 'Label: Value' format (e.g., 'Name of Employer(s): Weill Cornell').
        """
        # Find entries from Employment Status section
        matching_entries = []
        for entry in all_entries:
            hierarchy = entry.get('hierarchy', [])
            hierarchy_str = ' '.join(hierarchy).upper()

            # Match entries specifically from Employment Status section
            if 'EMPLOYMENT STATUS' in hierarchy_str or (
                'EMPLOYMENT' in hierarchy_str and 'H.' in hierarchy_str
            ):
                text = entry.get('text', '').strip()
                # Accept "Label: Value" format entries
                if text and ':' in text:
                    parts = text.split(':', 1)
                    value = parts[1].strip() if len(parts) > 1 else ''
                    # Must have meaningful value after colon
                    if len(value) > 2:
                        matching_entries.append(entry)

        if not matching_entries:
            return

        # Find "Name of Current Employer" paragraph in template (more specific than section header)
        target_idx = None
        for i, para in enumerate(self.doc.paragraphs):
            text = para.text.lower()
            if 'name of current employer' in text or 'name of employer' in text:
                target_idx = i
                break

        if target_idx is None:
            # Fall back to EMPLOYMENT STATUS section header
            target_idx = self._find_paragraph_with_text('EMPLOYMENT STATUS')

        if target_idx is None:
            if self.verbose:
                print("  Passthrough: Could not find Employment Status section")
            return

        if self.verbose:
            print(f"  Passthrough: Filling Employment Status ({len(matching_entries)} entries)")

        # Update the target paragraph with the employer info
        for entry in matching_entries:
            text = entry.get('text', '').strip()
            if ':' in text:
                # Parse "Label: Value" and update the corresponding template paragraph
                parts = text.split(':', 1)
                label = parts[0].strip()
                value = parts[1].strip()

                # Find and update the matching label in template
                for i, para in enumerate(self.doc.paragraphs):
                    para_text = para.text.strip()
                    # Match paragraphs with similar labels (e.g., "Name of Current Employer(s):")
                    if 'employer' in para_text.lower() and ':' in para_text:
                        # Update the paragraph: keep the label, add the value
                        label_end = para_text.find(':')
                        existing_label = para_text[:label_end + 1]
                        # Clear and rewrite
                        para.clear()
                        run = para.add_run(f"{existing_label}\t{value}")
                        _set_font(run)
                        self.stats['entries_inserted'] += 1
                        break

    def _fill_hospital_affiliation(self, all_entries: List[Dict]):
        """Fill G. INSTITUTIONAL/HOSPITAL AFFILIATION section.

        Looks for dedicated hospital affiliation entries or extracts from D2 positions.
        """
        # Find entries from Affiliation sections
        matching_entries = []
        for entry in all_entries:
            hierarchy = entry.get('hierarchy', [])
            hierarchy_str = ' '.join(hierarchy).upper()

            if ('AFFILIATION' in hierarchy_str and 'HOSPITAL' in hierarchy_str) or \
               ('INSTITUTIONAL' in hierarchy_str and 'AFFILIATION' in hierarchy_str):
                text = entry.get('text', '').strip()
                if text and len(text) > 5:
                    matching_entries.append(entry)

        if not matching_entries:
            # No dedicated affiliation entries - skip (D2 positions are handled elsewhere)
            return

        # Find the affiliation table
        section_idx = self._find_paragraph_with_text('INSTITUTIONAL/HOSPITAL AFFILIATION')
        if section_idx is None:
            section_idx = self._find_paragraph_with_text('HOSPITAL AFFILIATION')

        if section_idx is None:
            if self.verbose:
                print("  Passthrough: Could not find Hospital Affiliation section")
            return

        # Find table after the section
        table = self._find_table_after_paragraph(section_idx)

        # Validate this is the affiliation table (should have "Primary Hospital" or similar)
        if table and table.rows:
            first_cell = table.rows[0].cells[0].text.lower() if table.rows[0].cells else ''
            if 'hospital' not in first_cell and 'affiliation' not in first_cell and 'primary' not in first_cell:
                # Wrong table - don't modify
                if self.verbose:
                    print(f"  Passthrough: Skipping non-affiliation table (first cell: '{first_cell[:30]}')")
                table = None

            if table:
                # Clear existing table data and fill with matched entries
                _clear_table_data(table, keep_header=True)
                self.stats['tables_populated'] += 1

                for entry in matching_entries:
                    text = entry.get('text', '').strip()
                    fields = entry.get('extracted_fields', {}) or {}

                    # Try to extract structured content
                    if ':' in text:
                        # Parse "Label: Value" format
                        parts = text.split(':', 1)
                        label = parts[0].strip()
                        value = parts[1].strip() if len(parts) > 1 else ''

                        # Add as row: [Label, Value]
                        row = table.add_row()
                        row.cells[0].text = label
                        if len(row.cells) > 1:
                            row.cells[1].text = value
                    else:
                        # Add as single-cell row
                        row = table.add_row()
                        row.cells[0].text = text

                    # Apply font formatting
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            for run in para.runs:
                                _set_font(run)

                    self.stats['entries_inserted'] += 1
            else:
                # No table - insert as bullet points after the section header
                for i, entry in enumerate(matching_entries):
                    text = entry.get('text', '').strip()
                    if text:
                        insert_idx = section_idx + 1 + i
                        self._insert_bulleted_entry(insert_idx, text, entry, add_blank_before=(i == 0), list_level=0)
