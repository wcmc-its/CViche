"""Section P: institutional administrative activities (#398).

Three columns -- activity/committee, role, dates -- and, like section O next
door, the writer spends most of its time working out how many rows one entry is
actually worth. Committee service is written in source CVs as a table or as a
run of lines, and both survive extraction as a single entry.

Three different repairs, in the order the writer tries them:

1. A LIST under `committee_name` / `committee` / `activity`. Stage 4 packs a
   multi-committee entry as a list of record dicts (#208/#248 fusion). Each
   record becomes its own row. Before this, the list went into a Word cell whole
   and python-docx raised deep in the XML layer, aborting the entire document
   (#256) -- which is also why `_add_committee_row` runs every value through
   `_committee_cell_text` rather than trusting its caller.
2. No dates in the fields, but a parenthetical in the raw text. "(Chair
   2011-2013)" carries both the role and the range; "(2010-present)" carries
   only the range. Either way the parenthetical is stripped back out of the
   activity text so the committee name renders clean.
3. 3+ lines of raw text, or 2 lines with no extracted activity, means the fields
   describe one item out of many and `_add_multiline_committee_rows` re-parses
   the text per line.

That last helper drops bare date lines outright. They are orphans from a source
table's date column, and there is no way to tell which committee each belongs
to -- unlike section O, where the columns arrive in matched order and can be
paired back up.
"""
import re
from typing import Dict, List

from ..formatting import _clear_table_data, _set_font, format_date_range
from ..normalization import _committee_cell_text
from ..sorting import sort_entries_reverse_chronological
from unified_pipeline.core.render_check import entry_lines


class AdministrativeActivitiesSection:
    """Section P writers, mixed into `WCMTemplateGenerator`."""

    def _fill_administrative_activities(self, entries: List[Dict]):
        """Fill P. INSTITUTIONAL ADMINISTRATIVE ACTIVITIES section.

        WCM template has table with: Activity/Committee | Role | Dates
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Administrative Activities ({len(entries)} entries)...")

        # Find Administrative section
        section_idx = self._find_paragraph_with_text("INSTITUTIONAL ADMINISTRATIVE")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("ADMINISTRATIVE ACTIVITIES")
        if section_idx is None:
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            return

        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        sorted_entries = sort_entries_reverse_chronological(entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {}) or {}
            taxonomy_code = entry.get('taxonomy_code', 'P')
            original_text = entry.get('text', '')

            # Stage 4 packs a multi-committee entry as a LIST of record dicts under
            # committee_name/committee/activity (#208/#248 fusion). Expand each into
            # its own row rather than dumping a list into one cell (#256 crash).
            record_list = next(
                (v for v in (fields.get('committee_name'), fields.get('committee'),
                             fields.get('activity')) if isinstance(v, list)), None)
            if record_list:
                for rec in record_list:
                    if isinstance(rec, dict):
                        a = _committee_cell_text(rec.get('committee_name') or rec.get('committee')
                                                 or rec.get('activity') or rec.get('name'))
                        r = _committee_cell_text(rec.get('role'))
                        d = format_date_range(rec.get('start_date') or '',
                                              rec.get('end_date') or '', taxonomy_code) or ''
                    else:
                        a, r, d = _committee_cell_text(rec), '', ''
                    if a:
                        self._add_committee_row(table, a, r, d)
                continue

            activity = fields.get('activity') or fields.get('committee') or fields.get('committee_name') or ''
            role = fields.get('role') or ''
            start_date = fields.get('start_date') or ''
            end_date = fields.get('end_date') or ''
            dates = format_date_range(start_date, end_date, taxonomy_code) or ''

            # If dates not extracted, try to parse from parenthetical patterns in original text
            # Common patterns: "(Chair 2011-2013)", "(2010-present)", "(Member 1999-2012)"
            if not dates and original_text:
                # Pattern 1: (Role YYYY-YYYY) or (Role YYYY-present)
                paren_match = re.search(r'\(([^)]*?)(\d{4})\s*[-–]\s*(\d{4}|present)\)', original_text, re.IGNORECASE)
                if paren_match:
                    potential_role = paren_match.group(1).strip()
                    start_year = paren_match.group(2)
                    end_year = paren_match.group(3)
                    dates = f"{start_year}-{end_year}"
                    # Extract role if present (e.g., "Chair", "Member")
                    if potential_role and not role:
                        role = potential_role.rstrip(',').strip()
                    # Clean activity by removing the parenthetical
                    if not activity:
                        activity = re.sub(r'\s*\([^)]*\d{4}[^)]*\)', '', original_text).strip()
                else:
                    # Pattern 2: Just (YYYY-YYYY) without role
                    paren_match = re.search(r'\((\d{4})\s*[-–]\s*(\d{4}|present)\)', original_text, re.IGNORECASE)
                    if paren_match:
                        dates = f"{paren_match.group(1)}-{paren_match.group(2)}"
                        if not activity:
                            activity = re.sub(r'\s*\([^)]*\d{4}[^)]*\)', '', original_text).strip()

            # Check if this entry contains multiple items (newline-separated)
            lines = entry_lines(original_text)

            # Use multi-line parsing when the text contains 3+ lines — this catches
            # mega-blocks where field extraction only captured one item from many.
            if len(lines) >= 3:
                # Multiple items merged - split them into separate rows
                self._add_multiline_committee_rows(table, lines)
            elif len(lines) > 1 and not activity:
                # Two lines, no extracted activity - still try multi-line parsing
                self._add_multiline_committee_rows(table, lines)
            else:
                if not activity:
                    activity = original_text[:150]

                self._add_committee_row(table, activity, role, dates)

    def _add_committee_row(self, table, activity: str, role: str, dates: str):
        """Add a single committee row with proper column handling."""
        # Defensive: never write a non-str (dict/list) into a Word cell — it
        # raises deep in python-docx and aborts the whole document (#256).
        activity = _committee_cell_text(activity)
        role = _committee_cell_text(role)
        dates = _committee_cell_text(dates)
        row = table.add_row()
        num_cols = len(row.cells)

        if num_cols >= 3:
            row.cells[0].text = activity or ''
            row.cells[1].text = role or ''
            row.cells[2].text = dates or ''
        elif num_cols >= 2:
            row.cells[0].text = f"{activity} ({role})" if role else activity
            row.cells[1].text = dates or ''
        else:
            row.cells[0].text = f"{activity} - {dates}" if dates else activity

        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    _set_font(run)
        self.stats['entries_inserted'] += 1

    def _add_multiline_committee_rows(self, table, lines: List[str]):
        """Add multiple committee rows from multiline content, parsing dates from each line."""

        # Pattern for bare date lines (orphaned from table extraction)
        bare_date_pattern = re.compile(r'^\s*\|?\s*\d{4}(?:\s*[-–]\s*(?:\d{4}|present))?\s*$', re.IGNORECASE)

        for line in lines:
            line = line.strip()
            if not line:
                continue

            # Skip bare date lines - they're orphaned from table extraction
            # and we can't associate them with any committee
            if bare_date_pattern.match(line):
                continue

            activity = line
            role = ''
            dates = ''

            # Try to parse "(Role YYYY-YYYY)" or "(YYYY-YYYY)" pattern
            paren_match = re.search(r'\(([^)]*?)(\d{4})\s*[-–]\s*(\d{4}|present)\)', line, re.IGNORECASE)
            if paren_match:
                potential_role = paren_match.group(1).strip()
                start_year = paren_match.group(2)
                end_year = paren_match.group(3)
                dates = f"{start_year}-{end_year}"
                if potential_role:
                    role = potential_role.rstrip(',').strip()
                activity = re.sub(r'\s*\([^)]*\d{4}[^)]*\)', '', line).strip()
            else:
                # Try simpler pattern: just (YYYY-YYYY)
                paren_match = re.search(r'\((\d{4})\s*[-–]\s*(\d{4}|present)\)', line, re.IGNORECASE)
                if paren_match:
                    dates = f"{paren_match.group(1)}-{paren_match.group(2)}"
                    activity = re.sub(r'\s*\([^)]*\d{4}[^)]*\)', '', line).strip()

            # Skip if activity is empty after date extraction
            if not activity:
                continue

            self._add_committee_row(table, activity, role, dates)
