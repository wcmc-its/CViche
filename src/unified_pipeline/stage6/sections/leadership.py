"""Section O: institutional leadership activities (#398).

Three columns -- role/position, institution/location, dates -- and the writer's
real job is deciding whether an entry is one leadership role or several.

Source CVs list leadership as a table, and when entry extraction reads that
table it frequently returns the whole block as ONE entry whose
`extracted_fields` describe only the first row. Rendering that straight loses
every row but the first, so the writer looks past the fields at the raw text and
counts lines: 3+ lines, or 2 lines with no extracted role, means re-parse rather
than trust the fields.

`_add_multiline_leadership_rows` is that re-parse. The line parser itself is
`_parse_flattened_committee_lines` in `stage6.parsing`, shared with section P
next door (#572 -- P used to run a drifted copy that lost dates); its docstring
describes the flattened-table shapes it handles. O's own job is folding the
parsed role titles back into the activity text, because its table has no role
column for them.
"""
from typing import Dict, List

from ..formatting import _clear_table_data, _set_font, format_date_range
from ..normalization import _committee_cell_text
from ..parsing import _parse_flattened_committee_lines
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
        # Defensive: never write a non-str (dict/list) into a Word cell -- it
        # raises deep in python-docx and aborts the whole document (#256).
        # Sibling P's _add_committee_row already does this (#625 review).
        role = _committee_cell_text(role)
        institution = _committee_cell_text(institution)
        dates = _committee_cell_text(dates)
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

        The line parser is `_parse_flattened_committee_lines`, shared with
        section P (#572). O's table has no role column, so parenthetical role
        titles are folded back into the activity text: "Committee (Chair)".
        """
        for item in _parse_flattened_committee_lines(lines):
            if item.roles:
                activity = f"{item.activity} ({'; '.join(item.roles)})"
            else:
                activity = item.activity
            # Skip if nothing renderable remains -- matches P's guard on the
            # same shared parser's output (#625 review); a role-and-date-free
            # parenthetical (e.g. a bare "(2010-2013)") would otherwise add a
            # blank leadership row.
            if not activity:
                continue
            self._add_leadership_row(table, activity, '', item.dates)
