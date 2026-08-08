"""Section N: mentoring -- current mentees, past mentees, outcomes (#398).

The WCM template gives each mentee an individual table rather than one row in a
shared table, so this section builds document structure instead of filling it,
and the insert order is inverted throughout: every table goes in immediately
after its header and pushes the previous one down, so the writers iterate
`reversed(...)` to end up in the intended order.

Before any of that it re-partitions its own input, three times, and each pass
exists because rendering the stage-4 codes literally lost content (#261):

- an N3B (past) mentee whose end date says "present", or that has a start date
  and no end date at all, is really current -- rendered as-is it reads
  "2019-present" under Past Mentees.
- an N3A/N3B entry that names no mentee is an aggregate count, not a mentee.
  `_create_mentee_table` returns None for it, so before this partition it
  vanished; it now renders as a plain line via `_insert_mentoring_line`.
- N4 outcome narrative arrives disguised as a current mentee and has no table in
  the template at all. It is reclaimed here and written under the section header.

`_create_mentee_table_with_spacing` is the one the writer actually calls;
`_create_mentee_table` is the table itself, kept separate because the spacing
paragraph has to be inserted between the header and the table.
"""
import sys
from typing import Dict, List, Optional

try:
    from docx.table import Table
except ImportError:  # pragma: no cover - mirrors stage_6_word_template
    print("Error: python-docx not installed. Install with: pip install python-docx lxml")
    sys.exit(1)

from ..formatting import (
    _format_mentee_duration,
    _set_cell_vertical_alignment,
    _set_font,
    _set_table_border,
)
from ..normalization import _clean_inline_tabs
from ..parsing import _is_mentee_record, _is_mentoring_outcome


class MentoringSection:
    """Section N writers, mixed into `WCMTemplateGenerator`."""

    def _insert_mentoring_line(self, text: str, insert_after_idx: int, entry: Dict = None):
        """Insert a plain mentoring paragraph directly after ``insert_after_idx``.

        Used for content that belongs in MENTORING but has no per-mentee table to
        live in: aggregate counts (N3A/N3B with no name) and outcome narrative (N4).
        Positioned with the same body-splice the mentee tables use.
        """
        para = self.doc.add_paragraph()
        run = para.add_run(text)
        _set_font(run)
        if entry:
            self._add_entry_comments(para, entry)

        body = self.doc.element.body
        try:
            target = self.doc.paragraphs[insert_after_idx]._element
            body.insert(list(body).index(target) + 1, para._element)
        except (ValueError, IndexError):
            pass
        self.stats['entries_inserted'] += 1
        return para

    def _fill_mentoring(self, entries_by_code: Dict[str, List[Dict]]):
        """Fill mentoring section with individual tables per mentee.

        Creates tables for N3A (current mentees) and N3B (past mentees).
        Overrides: If end_date contains 'present', mentee is treated as current.

        N3A/N3B entries that name no mentee are aggregate summaries and render as
        plain lines instead of tables; N4 (mentoring outcomes) has no table at all
        and renders under the section header (#261). Without this, all three were
        dropped silently: _create_mentee_table returns None with no name, and N4 has
        no entry in TAXONOMY_TO_SECTION.
        """
        n3a_entries = list(entries_by_code.get('N3A', []))
        n3b_entries = list(entries_by_code.get('N3B', []))
        n4_entries = list(entries_by_code.get('N4', []))

        # Override: Move N3B entries to current if the relationship appears ongoing
        # If end_date contains "present" OR (has start_date but no end_date), treat as current
        # Rationale: If there's no end date, the displayed duration would show "-present"
        entries_to_move = []
        for entry in n3b_entries:
            fields = entry.get('extracted_fields', {})
            end_date = str(fields.get('end_date', '') or '').strip()
            start_date = str(fields.get('start_date', '') or '').strip()

            # Check if end_date indicates ongoing
            end_lower = end_date.lower()
            is_ongoing = ('present' in end_lower or end_lower in ('ongoing', 'current', 'now'))

            # Also treat as ongoing if there's a start but no end (would display as "-present")
            if not is_ongoing and start_date and not end_date:
                is_ongoing = True

            if is_ongoing:
                entries_to_move.append(entry)

        for entry in entries_to_move:
            n3b_entries.remove(entry)
            n3a_entries.append(entry)

        # Split off aggregate summaries (no mentee named) — they get a line, not a
        # table. Done after the ongoing-reshuffle above, which keys off dates a
        # summary never has, so the partition cannot change that outcome.
        n3a_summaries = [e for e in n3a_entries if not _is_mentee_record(e)]
        n3b_summaries = [e for e in n3b_entries if not _is_mentee_record(e)]
        n3a_entries = [e for e in n3a_entries if _is_mentee_record(e)]
        n3b_entries = [e for e in n3b_entries if _is_mentee_record(e)]

        # Outcome narrative arrives disguised as a current mentee (see
        # _is_mentoring_outcome). Reclaim it and render it under the section header
        # rather than beneath "Current Mentees:", where it does not belong.
        n4_entries += [e for e in n3a_summaries + n3b_summaries
                       if _is_mentoring_outcome(e)]
        n3a_summaries = [e for e in n3a_summaries if not _is_mentoring_outcome(e)]
        n3b_summaries = [e for e in n3b_summaries if not _is_mentoring_outcome(e)]

        total_mentees = len(n3a_entries) + len(n3b_entries)
        total_extra = len(n3a_summaries) + len(n3b_summaries) + len(n4_entries)
        if total_mentees + total_extra == 0:
            return

        if self.verbose:
            print(f"Filling Mentoring ({total_mentees} mentees, {total_extra} summary/outcome lines)...")
            if entries_to_move:
                print(f"  Moved {len(entries_to_move)} mentees from Past to Current (end_date=present)")

        # Find "Current Mentees:" and "Past Mentees:" insertion points
        # These are more specific than "MENTORING" which can match other content
        current_mentees_idx = self._find_paragraph_exact("Current Mentees:")
        past_mentees_idx = self._find_paragraph_exact("Past Mentees:")

        # Fallback to section header if specific markers not found
        if current_mentees_idx is None and past_mentees_idx is None:
            mentoring_idx = self._find_paragraph_exact("MENTORING")
            if mentoring_idx is None:
                mentoring_idx = self._find_paragraph_with_text("Mentees")
            if mentoring_idx is None:
                return
            # Insert both current and past after the section header
            current_mentees_idx = mentoring_idx
            past_mentees_idx = mentoring_idx

        # Fill Current Mentees (N3A)
        if (n3a_entries or n3a_summaries) and current_mentees_idx is not None:
            # Remove any existing template table after "Current Mentees:"
            existing_table = self._find_table_after_paragraph(current_mentees_idx)
            if existing_table:
                existing_table._element.getparent().remove(existing_table._element)

            # Create tables for each current mentee (in REVERSE order so final order is correct)
            # Each table is inserted right after the header, pushing earlier ones down
            for entry in reversed(n3a_entries):
                fields = entry.get('extracted_fields', {})
                self._create_mentee_table_with_spacing(fields, current_mentees_idx, entry)
                self.stats['tables_populated'] += 1
                self.stats['entries_inserted'] += 1

            # Summaries go in last so they land directly under the header, above the
            # tables (each insert pushes the previous one down).
            self._insert_mentoring_summaries(n3a_summaries, current_mentees_idx)

        # Fill Past Mentees (N3B)
        if n3b_entries or n3b_summaries:
            # Re-find Past Mentees index since it may have shifted after current mentee insertion
            past_mentees_idx = self._find_paragraph_exact("Past Mentees:")
            if past_mentees_idx is not None:
                # Remove any existing template table after "Past Mentees:"
                existing_table = self._find_table_after_paragraph(past_mentees_idx)
                if existing_table:
                    existing_table._element.getparent().remove(existing_table._element)

                # Create tables for each past mentee (in REVERSE order so final order is correct)
                for entry in reversed(n3b_entries):
                    fields = entry.get('extracted_fields', {})
                    self._create_mentee_table_with_spacing(fields, past_mentees_idx, entry)
                    self.stats['tables_populated'] += 1
                    self.stats['entries_inserted'] += 1

                self._insert_mentoring_summaries(n3b_summaries, past_mentees_idx)

        # Mentoring outcomes (N4) have no table in the WCM template. Render them
        # under the section header, re-found because the inserts above shifted it.
        if n4_entries:
            mentoring_idx = self._find_paragraph_exact("MENTORING")
            if mentoring_idx is not None:
                self._insert_mentoring_summaries(n4_entries, mentoring_idx)

    def _insert_mentoring_summaries(self, entries: List[Dict], insert_after_idx: int):
        """Render summary/outcome entries as plain lines after ``insert_after_idx``.

        Reversed so that, with each insert landing immediately after the header and
        pushing the previous one down, the final document order matches ``entries``.
        """
        for entry in reversed(entries):
            text = _clean_inline_tabs((entry.get('text') or '').strip())
            if text:
                self._insert_mentoring_line(text, insert_after_idx, entry)

    def _create_mentee_table(self, fields: Dict, insert_after_idx: int, entry: Dict = None) -> Optional[Table]:
        """Create an individual mentee table matching WCM template structure.

        WCM Template expects:
        - Name
        - Site/Position (your role/title during mentorship, or degree program)
        - Mentoring Period (mm/yyyy-mm/yyyy)
        - Project/Accomplishments (dissertation title, research focus)
        - Current Position
        - Type of Supervision (research, clinical, teaching, leadership)
        """
        # Build Site/Position from available data
        # Prefer mentee_level (degree type) + site_position if both available
        site_position = ''
        mentee_level = fields.get('mentee_level', '')  # e.g., "PhD, MBSB"
        site_pos_raw = fields.get('site_position', '')  # e.g., "Thesis" or "Ph.D., Human Genetics"

        if mentee_level and site_pos_raw:
            # Combine if they're different
            if mentee_level.lower() not in site_pos_raw.lower():
                site_position = f"{mentee_level} - {site_pos_raw}"
            else:
                site_position = mentee_level or site_pos_raw
        else:
            site_position = mentee_level or site_pos_raw

        # Build Project/Accomplishments from research_focus (dissertation title)
        project = fields.get('research_focus', '') or fields.get('dissertation_title', '')

        # Awards and fellowships the mentee won belong in this row: the WCM template's
        # footnote for Project/Accomplishments reads "Optional: List publications,
        # awards, grants ... arising directly from the mentoring activity." Stage 4
        # writes them to awards/funding_source, which nothing in this file read, so
        # 133 corpus values were extracted and then dropped.
        mentee_awards = (fields.get('awards') or fields.get('funding_source') or '').strip()
        if mentee_awards and mentee_awards.casefold() not in project.casefold():
            project = f"{project}\nAwards: {mentee_awards}" if project else f"Awards: {mentee_awards}"

        # Determine supervision type - default to "Research" for thesis/dissertation mentees
        supervision_type = fields.get('supervision_type', '')
        if not supervision_type:
            # Infer from site_position or mentee_level
            level_lower = (mentee_level or site_pos_raw or '').lower()
            if any(x in level_lower for x in ['phd', 'thesis', 'dissertation', 'doctoral']):
                supervision_type = 'Research'
            elif any(x in level_lower for x in ['postdoc', 'fellow']):
                supervision_type = 'Research'
            elif any(x in level_lower for x in ['resident', 'clinical']):
                supervision_type = 'Clinical'
            elif any(x in level_lower for x in ['master', 'ms', 'ma']):
                supervision_type = 'Research'

        # Build all rows - include blank values for consistency with other sections
        rows = [
            ('Name:', fields.get('name') or fields.get('mentee_name', '')),
            ('Site/Position:', site_position),
            ('Mentoring Period:', _format_mentee_duration(fields)),
            ('Project/Accomplishments:', project),
            ('Current Position:', fields.get('current_position', '')),
            ('Type of Supervision:', supervision_type),
        ]

        # Must have at least a name
        if not rows[0][1]:
            return None

        # Create table
        table = self.doc.add_table(rows=len(rows), cols=2)
        _set_table_border(table, color='808080', size=4)

        first_cell_para = None
        for i, (label, value) in enumerate(rows):
            row = table.rows[i]
            # Label cell (bold)
            label_cell = row.cells[0]
            label_cell.text = label
            _set_cell_vertical_alignment(label_cell, 'center')
            for para in label_cell.paragraphs:
                if i == 0 and first_cell_para is None:
                    first_cell_para = para
                for run in para.runs:
                    _set_font(run, bold=True)

            # Value cell
            value_cell = row.cells[1]
            value_cell.text = str(value) if value else ''
            _set_cell_vertical_alignment(value_cell, 'center')
            for para in value_cell.paragraphs:
                for run in para.runs:
                    _set_font(run)

        # Add comments from entry
        if entry and first_cell_para:
            self._add_entry_comments(first_cell_para, entry)

        # Position table in document
        body = self.doc.element.body
        if insert_after_idx < len(self.doc.paragraphs):
            target_para = self.doc.paragraphs[insert_after_idx]._element
            body_elements = list(body)
            try:
                para_idx = body_elements.index(target_para)
                body.insert(para_idx + 1, table._tbl)
            except (ValueError, IndexError):
                pass

        return table

    def _create_mentee_table_with_spacing(self, fields: Dict, insert_after_idx: int, entry: Dict = None) -> Optional[Table]:
        """Create an individual mentee table with spacing paragraph after it.

        Inserts: [header para] -> [spacing para] -> [table]
        Since we insert in reverse order, the final document shows:
        [header para] -> [table] -> [spacing para] -> [table] -> [spacing para] ...
        """
        from docx.oxml.ns import qn
        from docx.oxml import OxmlElement

        # First create the table
        table = self._create_mentee_table(fields, insert_after_idx, entry)
        if not table:
            return None

        # Now insert a spacing paragraph AFTER the table (which means BEFORE in insertion order)
        # Create a blank paragraph element
        body = self.doc.element.body
        spacing_para = OxmlElement('w:p')

        # Add paragraph properties for spacing
        pPr = OxmlElement('w:pPr')
        spacing = OxmlElement('w:spacing')
        spacing.set(qn('w:before'), '120')  # 6pt before
        spacing.set(qn('w:after'), '120')   # 6pt after
        pPr.append(spacing)
        spacing_para.append(pPr)

        # Insert the spacing paragraph right after the table
        # The table was inserted at para_idx + 1, so spacing goes at para_idx + 2
        if insert_after_idx < len(self.doc.paragraphs):
            target_para = self.doc.paragraphs[insert_after_idx]._element
            body_elements = list(body)
            try:
                para_idx = body_elements.index(target_para)
                # Table is at para_idx + 1, so insert spacing at para_idx + 2
                body.insert(para_idx + 2, spacing_para)
            except (ValueError, IndexError):
                pass

        return table
