"""Section R: invitations to speak and present (#398).

Three columns -- title, institution/location, year -- across three sibling
tables: Regional, National, International. The routing between them is the whole
section. Scope is not a field on the entry; it is inferred from where the talk
was given relative to where the CV owner works, which is why the writer reads
`cv_owner_location` and delegates to `_classify_geographic_scope` (shared, so it
stays on `WCMTemplateGenerator`).

Locating the three tables is done by walking forward from the section heading
and matching a paragraph that IS the scope word, allowing a trailing asterisk
("National*" carries a template footnote). A substring match would hit the
heading text itself. The window is capped at 25 paragraphs so a template missing
one subsection cannot capture a table belonging to the section below.

A template with no scope subsections at all degrades to one National table, and
an entry whose scope has no table falls back to National as well -- an
unclassifiable talk is published in the middle bucket rather than dropped.

Dates arrive under four different field names and field extraction emits the
STRING "None" often enough that it is checked for explicitly at both the `year`
and the `start_date` fallback; a literal "None" in the year column is worse than
a blank one. A title-less entry falls back to its first 150 characters of raw
text.
"""

from ..formatting import _clear_table_data, _set_font, format_date_for_section
from ..sorting import sort_entries_reverse_chronological


class PresentationsSection:
    """Section R writers, mixed into `WCMTemplateGenerator`."""

    def _fill_presentations(self, entries: list[dict]):
        """Fill R. INVITATIONS TO SPEAK/PRESENT section.

        WCM template has tables for Regional/National/International:
        Table structure: Title | Institution/Location | Dates (yyyy)

        Uses cv_owner_location to classify geographic scope of each entry.
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Invited Presentations ({len(entries)} entries)...")

        # Find Presentations section
        section_idx = self._find_paragraph_with_text("INVITATIONS TO SPEAK")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("Invited Presentations")
        if section_idx is None:
            return

        # Find Regional, National, and International subsection tables
        tables_by_scope = {}
        for scope in ['Regional', 'National', 'International']:
            # Look for scope header (may have * suffix like "National*")
            scope_idx = None
            for i in range(section_idx, min(section_idx + 25, len(self.doc.paragraphs))):
                para_text = self.doc.paragraphs[i].text.strip()
                if para_text == scope or para_text == f"{scope}*":
                    scope_idx = i
                    break

            if scope_idx is not None:
                table = self._find_table_after_paragraph(scope_idx)
                if table:
                    tables_by_scope[scope] = table

        # Fall back to National if we couldn't find specific tables
        if not tables_by_scope:
            table = self._find_table_after_paragraph(section_idx)
            if table:
                tables_by_scope['National'] = table

        if not tables_by_scope:
            return

        # Clear tables and mark as populated
        for scope, table in tables_by_scope.items():
            _clear_table_data(table, keep_header=True)
            self.stats['tables_populated'] += 1

        # Classify and route entries by geographic scope
        entries_by_scope = {'Regional': [], 'National': [], 'International': []}
        for entry in entries:
            scope = self._classify_geographic_scope(entry)
            entries_by_scope[scope].append(entry)

        if self.verbose and self.cv_owner_location:
            regional_count = len(entries_by_scope['Regional'])
            national_count = len(entries_by_scope['National'])
            intl_count = len(entries_by_scope['International'])
            print(f"  Presentations: {regional_count} Regional, {national_count} National, {intl_count} International")

        # Fill each table with its entries
        for scope, scope_entries in entries_by_scope.items():
            if not scope_entries:
                continue

            # Find the table for this scope (fall back to National)
            table = tables_by_scope.get(scope) or tables_by_scope.get('National')
            if not table:
                continue

            sorted_entries = sort_entries_reverse_chronological(scope_entries)

            for entry in sorted_entries:
                fields = entry.get('extracted_fields', {}) or {}
                taxonomy_code = entry.get('taxonomy_code', 'R')

                title = fields.get('title') or fields.get('presentation_title') or ''
                institution = fields.get('institution') or fields.get('location') or fields.get('venue') or ''

                # Get date, falling back to start_date/end_date for multi-date entries
                raw_date = fields.get('year') or fields.get('date') or ''
                # Handle the string "None" from field extraction
                if str(raw_date).strip().lower() == 'none':
                    raw_date = ''
                if not raw_date:
                    # Fall back to start_date for entries with date ranges
                    raw_date = fields.get('start_date') or ''
                    if str(raw_date).strip().lower() == 'none':
                        raw_date = ''
                # Format date as yyyy per WCM template requirements
                formatted_date = format_date_for_section(raw_date, 'R') if raw_date else ''

                if not title:
                    title = entry.get('text', '')[:150]

                # The R block is Title | Institution/Location | Dates, so the
                # meeting that hosted the talk has no column of its own and
                # belongs with the venue: "ASMBS 2022 Presidential Grand Rounds,
                # Dallas, TX". event_name was read nowhere in this file, so
                # 2,194 of 2,956 extracted values across 79 CVs reached no part
                # of the rendered document.
                event_name = (fields.get('event_name') or '').strip()
                if event_name and event_name.casefold() not in f"{institution} {title}".casefold():
                    institution = f"{event_name}, {institution}" if institution else event_name

                row = table.add_row()
                num_cols = len(row.cells)
                if num_cols >= 3:
                    row.cells[0].text = title or ''
                    row.cells[1].text = institution or ''
                    row.cells[2].text = formatted_date
                elif num_cols >= 2:
                    row.cells[0].text = title or ''
                    row.cells[1].text = formatted_date

                for cell in row.cells:
                    for para in cell.paragraphs:
                        for run in para.runs:
                            _set_font(run)
                self.stats['entries_inserted'] += 1
