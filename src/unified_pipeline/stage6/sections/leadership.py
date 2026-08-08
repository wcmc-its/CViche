"""Section O: institutional leadership activities (#398).

Three columns -- role/position, institution/location, dates -- and the writer's
real job is deciding whether an entry is one leadership role or several.

Source CVs list leadership as a table, and when entry extraction reads that
table it frequently returns the whole block as ONE entry whose
`extracted_fields` describe only the first row. Rendering that straight loses
every row but the first, so the writer looks past the fields at the raw text and
counts lines: 3+ lines, or 2 lines with no extracted role, means re-parse rather
than trust the fields.

`_add_multiline_leadership_rows` is that re-parse, and its shape is dictated by
what a flattened source table looks like once the column structure is gone:

    "Committee (Chair 1999-2010)"       parenthetical role + date range
    "Committee | 1996-Present"          pipe-separated date column
    "Committee    1999-2010"            trailing date
    "1999-2010"                         a date whose activity is on another line

The last case is why `dates_pool` exists. When column 1 and column 2 of a source
table are extracted as separate runs of lines, the dates arrive orphaned; they
are matched back to date-less items by position, forward, because both columns
come from the same table and are therefore in the same order.

A line carrying several parentheticals ("(Vice Chair 2006-2008) (Chair
2008-2010)") is one role held under changing titles, so it becomes one row: the
latest date range, with every title collected into the activity text.
"""
import re
from typing import Dict, List

from ..formatting import _clear_table_data, _set_font, format_date_range
from ..sorting import sort_entries_reverse_chronological
from unified_pipeline.core.render_check import entry_lines


class LeadershipSection:
    """Section O writers, mixed into `WCMTemplateGenerator`."""

    def _fill_leadership(self, entries: List[Dict]):
        """Fill O. INSTITUTIONAL LEADERSHIP ACTIVITIES section.

        WCM template has table with: Role(s)/Position | Institution/Location | Dates
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Institutional Leadership ({len(entries)} entries)...")

        # Find Leadership section
        section_idx = self._find_paragraph_with_text("INSTITUTIONAL LEADERSHIP")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("Leadership")
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
            taxonomy_code = entry.get('taxonomy_code', 'O')
            original_text = entry.get('text', '')

            # Check for leadership_role field (O code schema) as well as generic role/position
            role = fields.get('leadership_role') or fields.get('role') or fields.get('position') or ''
            institution = fields.get('institution') or fields.get('organization') or ''
            # Also check division_department for O codes
            if not institution:
                institution = fields.get('division_department') or ''
            start_date = fields.get('start_date') or ''
            end_date = fields.get('end_date') or ''
            dates = format_date_range(start_date, end_date, taxonomy_code) or ''

            # Check if this entry contains multiple items (newline-separated)
            lines = entry_lines(original_text)

            # Use multi-line parsing when the text contains 3+ lines — this catches
            # mega-blocks where field extraction only captured one item from many.
            # For single/double-line entries, use extracted fields normally.
            if len(lines) >= 3:
                # Multiple items merged - split them into separate rows
                self._add_multiline_leadership_rows(table, lines)
            elif len(lines) > 1 and not role:
                # Two lines, no extracted role - still try multi-line parsing
                self._add_multiline_leadership_rows(table, lines)
            else:
                if not role and not institution:
                    role = original_text[:100]

                self._add_leadership_row(table, role, institution, dates)

    def _add_leadership_row(self, table, role: str, institution: str, dates: str):
        """Add a single row to leadership table."""
        row = table.add_row()
        num_cols = len(row.cells)
        if num_cols >= 3:
            row.cells[0].text = role or ''
            row.cells[1].text = institution or ''
            row.cells[2].text = dates or ''
        elif num_cols >= 2:
            row.cells[0].text = f"{role}, {institution}" if institution else (role or '')
            row.cells[1].text = dates or ''

        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    _set_font(run)
        self.stats['entries_inserted'] += 1

    def _add_multiline_leadership_rows(self, table, lines: List[str]):
        """Parse multiple leadership/committee lines and add separate rows.

        Handles patterns like:
        - "Committee Name (Chair 1999-2010)" - parenthetical role+date
        - "Committee Name | 1999-2010" - pipe-separated date column
        - "Committee Name    1999-2010" - trailing date
        - Lines followed by date-only lines (from table column extraction)
        """

        # Date patterns
        year_pattern = re.compile(r'^(\d{4}(?:\s*[-–]\s*(?:\d{4}|present))?)$', re.IGNORECASE)
        trailing_date = re.compile(r'(\d{4}(?:\s*[-–]\s*(?:\d{4}|present))?)\s*$', re.IGNORECASE)
        # Parenthetical with role+date: "(Chair 1999-2010)" or "(Vice Chair 2006-2008)"
        paren_role_date = re.compile(r'\(([^)]*?)(\d{4})\s*[-–]\s*(\d{4}|present)\s*\)', re.IGNORECASE)

        items = []  # (activity_text, institution, date)
        dates_pool = []

        for line in lines:
            # Skip empty or header-like lines
            if not line or line.lower() in ['dates', 'role', 'committee', 'institution']:
                continue

            # Handle pipe separator from table column extraction
            # e.g., "Committee (Chair 2002-present) | 1996-Present"
            if '|' in line:
                parts = [p.strip() for p in line.split('|') if p.strip()]
                if len(parts) >= 2 and trailing_date.match(parts[-1]):
                    # Last part is a date, rest is the activity
                    activity = ' | '.join(parts[:-1])
                    pipe_date = parts[-1]
                    # Also extract any parenthetical role+date from the activity
                    paren_match = paren_role_date.search(activity)
                    if paren_match:
                        role_text = paren_match.group(1).strip().rstrip(',')
                        clean_activity = paren_role_date.sub('', activity).strip()
                        if role_text:
                            clean_activity = f"{clean_activity} ({role_text})"
                    else:
                        clean_activity = activity
                    items.append((clean_activity, '', pipe_date))
                    continue
                elif len(parts) == 1:
                    line = parts[0]
                # else fall through to normal processing

            # Check if this is a date-only line
            if year_pattern.match(line):
                dates_pool.append(line)
                continue

            # Check for parenthetical role+date: "Committee (Chair 1999-2010)"
            paren_match = paren_role_date.search(line)
            if paren_match:
                role_text = paren_match.group(1).strip().rstrip(',')
                start_year = paren_match.group(2)
                end_year = paren_match.group(3)
                item_date = f"{start_year}-{end_year}"
                # Clean the activity text: remove the parenthetical
                clean_activity = paren_role_date.sub('', line).strip()
                if role_text:
                    clean_activity = f"{clean_activity} ({role_text})"
                # Check for multiple parentheticals on same line
                # e.g., "(Vice Chair 2006-2008) (Chair 2008-2010)"
                all_parens = list(paren_role_date.finditer(line))
                if len(all_parens) > 1:
                    # Take the latest date range
                    last = all_parens[-1]
                    item_date = f"{last.group(2)}-{last.group(3)}"
                    # Reconstruct clean activity with all roles
                    clean_activity = paren_role_date.sub('', line).strip()
                    roles = [m.group(1).strip().rstrip(',') for m in all_parens if m.group(1).strip()]
                    if roles:
                        clean_activity = f"{clean_activity} ({'; '.join(roles)})"
                items.append((clean_activity, '', item_date))
                continue

            # Check if line has embedded date at the end (not in parentheses)
            date_match = trailing_date.search(line)
            if date_match:
                item_text = line[:date_match.start()].strip()
                item_date = date_match.group(1)
                if item_text:
                    items.append((item_text, '', item_date))
                else:
                    # Just a date with no text - add to pool
                    dates_pool.append(item_date)
                continue

            # Plain text line - no date found
            items.append((line, '', ''))

        # Match dates_pool to items without dates using forward mapping.
        # Both items and dates come from the same source table (column 1 → items,
        # column 2 → dates), so they're always in the same order.
        date_idx = 0
        for i in range(len(items)):
            if not items[i][2] and date_idx < len(dates_pool):
                items[i] = (items[i][0], items[i][1], dates_pool[date_idx])
                date_idx += 1

        # Add rows for each item
        for role, institution, item_date in items:
            self._add_leadership_row(table, role, institution, item_date)
