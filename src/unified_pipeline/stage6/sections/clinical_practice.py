"""Section L: clinical practice, innovation and leadership (#398).

One writer over three subsections -- L1 practice, L2 innovations, L3 leadership
-- because each is the same two-branch shape and only the field names and the
header text differ:

    find the subsection header -> find the table under it -> validate the table
    -> fill rows, or fall back to bullets when there is no usable table.

The validation step is the part worth knowing about. `_find_table_after_paragraph`
returns whatever table comes next in the document, and in CVs where a clinical
subsection is empty that is the *funding* table from a later section. Each of the
three branches therefore reads row 0 cell 0 and refuses a table whose header says
"Award Source" or "Funding" -- otherwise clinical entries render as grant rows.

`_insert_multiline_as_bullets` is the bullet fallback used by all three
subsections (L2 joined L1 and L3 in #572; it used the single-bullet inserter,
which collapsed a multi-line entry into one list paragraph with soft line
breaks). It splits on newlines so a multi-line source entry becomes one bullet
per line rather than one bullet containing embedded newlines, and attaches the
entry's Word comments to the first bullet only.
"""
from typing import Dict, List, Optional

from ..formatting import (
    _clear_table_data,
    _set_font,
    format_date_for_section,
    format_date_range,
)
from ..parsing import _is_structural_label
from ..sorting import sort_entries_reverse_chronological
from unified_pipeline.core.render_check import entry_lines


class ClinicalPracticeSection:
    """Section L writers, mixed into `WCMTemplateGenerator`."""

    def _fill_clinical_practice(self, entries_by_code: Dict[str, List[Dict]]):
        """Fill L. CLINICAL PRACTICE, INNOVATION, and LEADERSHIP section.

        This section has three subsections:
        - L1: Clinical Practice (patient care activities)
        - L2: Clinical Innovations (new approaches to care)
        - L3: Clinical Leadership (director/head roles)

        Each subsection uses a simple bulleted or table format.
        """
        l1_entries = entries_by_code.get('L1', [])
        l2_entries = entries_by_code.get('L2', [])
        l3_entries = entries_by_code.get('L3', [])

        total = len(l1_entries) + len(l2_entries) + len(l3_entries)
        if total == 0:
            return

        if self.verbose:
            print(f"Filling Clinical Practice ({len(l1_entries)} L1, {len(l2_entries)} L2, {len(l3_entries)} L3)...")

        # L1: Clinical Practice
        if l1_entries:
            # Find the "Clinical Practice" subsection (not the main "CLINICAL PRACTICE, INNOVATION..." header)
            # Use exact match first, then fall back to contains match
            section_idx = self._find_paragraph_exact("Clinical Practice")
            if section_idx is None:
                # Try to find a paragraph that starts with "Clinical Practice" but not the full section header
                for i, para in enumerate(self.doc.paragraphs):
                    text = para.text.strip()
                    if text == "Clinical Practice" or (
                        text.lower().startswith("clinical practice") and
                        "innovation" not in text.lower() and
                        "leadership" not in text.lower()
                    ):
                        section_idx = i
                        break

            if section_idx is not None:
                table = self._find_table_after_paragraph(section_idx)

                # Validate table is actually for Clinical Practice, not a different section
                # Check if first cell contains "Award Source" which indicates a grant table
                table_is_valid = False
                if table and table.rows:
                    first_cell_text = table.rows[0].cells[0].text.lower() if table.rows[0].cells else ''
                    # Grant tables have "Award Source", not clinical practice tables
                    if 'award source' not in first_cell_text and 'funding' not in first_cell_text:
                        table_is_valid = True
                    elif self.verbose:
                        print(f"  Skipping table (appears to be grant table, not clinical practice)")

                sorted_entries = sort_entries_reverse_chronological(l1_entries)

                if table and table_is_valid:
                    _clear_table_data(table, keep_header=True)
                    self.stats['tables_populated'] += 1
                    for entry in sorted_entries:
                        fields = entry.get('extracted_fields', {}) or {}
                        original_text = entry.get('text', '')

                        # Extract fields - clinical practice entries typically have:
                        # activity/location, institution, dates
                        activity = fields.get('activity') or fields.get('role') or fields.get('title') or ''
                        location = fields.get('location') or fields.get('institution') or ''
                        start_date = fields.get('start_date') or ''
                        end_date = fields.get('end_date') or ''
                        dates = format_date_range(start_date, end_date, 'L1') or ''

                        # Fallback to parsing original text if fields are empty
                        if not activity and original_text:
                            # Try to parse "Activity | Location | Dates" format
                            parts = original_text.split('|')
                            if len(parts) >= 2:
                                activity = parts[0].strip()
                                if len(parts) >= 3:
                                    dates = parts[-1].strip() if not dates else dates

                        if activity or location:
                            row = table.add_row()
                            # Typical clinical practice table: Activity/Type | Location | Dates
                            if len(row.cells) >= 3:
                                row.cells[0].text = activity
                                row.cells[1].text = location
                                row.cells[2].text = dates
                            elif len(row.cells) >= 2:
                                row.cells[0].text = f"{activity}" if activity else location
                                row.cells[1].text = dates
                            else:
                                row.cells[0].text = f"{activity} - {location} ({dates})" if dates else f"{activity} - {location}"

                            for cell in row.cells:
                                for para in cell.paragraphs:
                                    for run in para.runs:
                                        _set_font(run)
                            self.stats['entries_inserted'] += 1
                else:
                    # No valid table found - insert as bullet points after section header
                    if self.verbose:
                        print(f"  No Clinical Practice table found, inserting as bullet points")
                    bullet_count = 0
                    for entry in sorted_entries:
                        original_text = entry.get('text', '').strip()

                        # Skip entries that are structural labels from the source CV
                        if _is_structural_label(entry):
                            continue

                        # L1 clinical practice entries are narrative summaries —
                        # use the full original text rather than just the extracted
                        # clinical_role label, which loses the descriptive detail.
                        # Clean up tab-delimited format from source CV.
                        bullet_text = original_text.replace('\t', ' — ', 1).replace('\t', ' ') if '\t' in original_text else original_text

                        if bullet_text:
                            # Use multiline helper to properly split entries with multiple lines
                            inserted = self._insert_multiline_as_bullets(
                                section_idx + 1 + bullet_count, bullet_text, entry,
                                add_blank_before=(bullet_count == 0)
                            )
                            bullet_count += inserted

        # L2: Clinical Innovations
        if l2_entries:
            section_idx = self._find_paragraph_with_text("Clinical Innovations")
            if section_idx is not None:
                table = self._find_table_after_paragraph(section_idx)

                # Validate the table is actually for innovations, not a grant/funding table
                table_is_valid = False
                if table and table.rows:
                    first_cell_text = table.rows[0].cells[0].text.lower() if table.rows[0].cells else ''
                    if 'award source' not in first_cell_text and 'funding' not in first_cell_text:
                        table_is_valid = True
                    elif self.verbose:
                        print(f"  Skipping table (appears to be grant table, not clinical innovations)")

                sorted_entries = sort_entries_reverse_chronological(l2_entries)

                if table and table_is_valid:
                    _clear_table_data(table, keep_header=True)
                    self.stats['tables_populated'] += 1

                    for entry in sorted_entries:
                        fields = entry.get('extracted_fields', {}) or {}
                        original_text = entry.get('text', '')

                        title = fields.get('title') or fields.get('innovation') or ''
                        role = fields.get('role') or ''
                        description = fields.get('description') or ''
                        start_date = fields.get('start_date') or fields.get('date') or ''
                        dates = format_date_for_section(start_date, 'L2') if start_date else ''

                        if not title and original_text:
                            title = original_text.split('|')[0].strip() if '|' in original_text else original_text[:100]

                        if title:
                            row = table.add_row()
                            # Typical innovation table: Date | Title/Location | Role/Description
                            if len(row.cells) >= 3:
                                row.cells[0].text = dates
                                row.cells[1].text = title
                                row.cells[2].text = f"{role}. {description}".strip('. ') if role or description else ''
                            elif len(row.cells) >= 2:
                                row.cells[0].text = dates
                                row.cells[1].text = title
                            else:
                                row.cells[0].text = f"{dates}: {title}" if dates else title

                            for cell in row.cells:
                                for para in cell.paragraphs:
                                    for run in para.runs:
                                        _set_font(run)
                            self.stats['entries_inserted'] += 1
                else:
                    # No valid table found — insert as bullet points
                    if self.verbose:
                        print(f"  No Clinical Innovations table found, inserting as bullet points")
                    bullet_count = 0
                    for entry in sorted_entries:
                        original_text = entry.get('text', '').strip()
                        if _is_structural_label(entry):
                            continue
                        bullet_text = original_text.replace('\t', ' — ', 1).replace('\t', ' ') if '\t' in original_text else original_text
                        if bullet_text:
                            # Use multiline helper so a multi-line entry becomes
                            # one bullet per line, matching L1 and L3 (#572)
                            inserted = self._insert_multiline_as_bullets(
                                section_idx + 1 + bullet_count, bullet_text, entry,
                                add_blank_before=(bullet_count == 0)
                            )
                            bullet_count += inserted

        # L3: Clinical Leadership
        if l3_entries:
            section_idx = self._find_paragraph_with_text("Clinical Leadership")
            if section_idx is not None:
                table = self._find_table_after_paragraph(section_idx)

                # Validate the table is actually a leadership table, not a grant/funding table
                table_is_valid = False
                if table and table.rows:
                    first_cell_text = table.rows[0].cells[0].text.lower() if table.rows[0].cells else ''
                    if 'award source' not in first_cell_text and 'funding' not in first_cell_text:
                        table_is_valid = True
                    elif self.verbose:
                        print(f"  Skipping table (appears to be grant table, not clinical leadership)")

                sorted_entries = sort_entries_reverse_chronological(l3_entries)

                if table and table_is_valid:
                    _clear_table_data(table, keep_header=True)
                    self.stats['tables_populated'] += 1

                    for entry in sorted_entries:
                        fields = entry.get('extracted_fields', {}) or {}
                        original_text = entry.get('text', '')

                        role = fields.get('role') or fields.get('leadership_role') or fields.get('title') or ''
                        institution = fields.get('institution') or fields.get('organization') or ''
                        description = fields.get('description') or fields.get('program') or ''
                        start_date = fields.get('start_date') or ''
                        end_date = fields.get('end_date') or ''
                        dates = format_date_range(start_date, end_date, 'L3') or ''

                        if not role and original_text:
                            parts = original_text.split('|')
                            if len(parts) >= 1:
                                role = parts[0].strip()

                        if role:
                            row = table.add_row()
                            # Typical leadership table: Year(s) | Role | Description
                            if len(row.cells) >= 3:
                                row.cells[0].text = dates
                                row.cells[1].text = role
                                row.cells[2].text = f"{institution}. {description}".strip('. ') if institution or description else ''
                            elif len(row.cells) >= 2:
                                row.cells[0].text = dates
                                row.cells[1].text = f"{role} - {institution}" if institution else role
                            else:
                                row.cells[0].text = f"{dates}: {role}" if dates else role

                            for cell in row.cells:
                                for para in cell.paragraphs:
                                    for run in para.runs:
                                        _set_font(run)
                            self.stats['entries_inserted'] += 1
                else:
                    # No valid table found — insert as bullet points
                    if self.verbose:
                        print(f"  No Clinical Leadership table found, inserting as bullet points")
                    bullet_count = 0
                    for entry in sorted_entries:
                        fields = entry.get('extracted_fields', {}) or {}
                        original_text = entry.get('text', '').strip()

                        if _is_structural_label(entry):
                            continue

                        role = fields.get('role') or fields.get('leadership_role') or fields.get('title') or ''
                        institution = fields.get('institution') or fields.get('organization') or ''
                        start_date = fields.get('start_date') or ''
                        end_date = fields.get('end_date') or ''
                        dates = format_date_range(start_date, end_date, 'L3') or ''

                        if not role and original_text:
                            role = original_text.split('\t')[0].strip()

                        if role and institution and dates:
                            bullet_text = f"{role}, {institution}, {dates}"
                        elif role and dates:
                            bullet_text = f"{role}, {dates}"
                        elif role:
                            bullet_text = role
                        else:
                            bullet_text = original_text

                        if bullet_text:
                            # Use multiline helper to properly split entries with multiple lines
                            inserted = self._insert_multiline_as_bullets(
                                section_idx + 1 + bullet_count, bullet_text, entry,
                                add_blank_before=(bullet_count == 0)
                            )
                            bullet_count += inserted

    def _insert_multiline_as_bullets(self, insert_idx: int, text: str, entry: Optional[Dict] = None,
                                       add_blank_before: bool = False) -> int:
        """Insert multi-line text as separate bullets, one per line.

        This is the STANDARD method for inserting bulleted content. It respects
        the original document's line structure - if the source had multiple lines,
        each becomes its own bullet.

        Args:
            insert_idx: Index of paragraph to insert before
            text: The text content (may contain newlines)
            entry: Optional entry dict for adding comments (attached to first bullet only)
            add_blank_before: If True, add a blank line before the first entry

        Returns:
            Number of bullets inserted
        """
        lines = entry_lines(text)
        if not lines:
            return 0

        # Insert in reverse order since we're inserting before insert_idx
        for j, line_text in enumerate(reversed(lines)):
            is_last = (j == len(lines) - 1)  # Last in reversed = first in original
            self._insert_bulleted_entry(
                insert_idx, line_text,
                entry if is_last else None,  # Attach entry/comments to first bullet
                add_blank_before=add_blank_before and is_last, list_level=0
            )

        return len(lines)
