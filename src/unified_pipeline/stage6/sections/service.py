"""Section Q: extramural professional responsibilities (#398).

The one section in this package that is really four, and they are kept together
because they are not independent: `_fill_service` is a router, and the routing
is the section's actual logic.

    _fill_service_boards          Q2       Service on Boards -- Regional /
                                           National / International, chosen by
                                           comparing the entry's location
                                           against the CV owner's
    _fill_journal_reviewing       Q4D      Journal / ad hoc reviewing
    _fill_other_service           Q1, Q3,  bullet lists, and it hands Q1 on to
                                  Q4, Q4A- _fill_extramural_leadership
                                  Q4C
    _fill_extramural_leadership   Q1       Leadership in Extramural Organizations

`_fill_service` reroutes before it dispatches: a Q2 entry whose text is really
journal reviewing is moved to Q4D, and a multi-line Q2 entry mixing both is
split line by line. Separating the writers into four modules would put the
dispatcher in one file and the thing it corrects in another, which is the split
that would have to be undone first to understand either.

`_add_extramural_row` and `_parse_extramural_leadership_lines` are shared by two
of the four writers and by nothing outside the section.
"""
import logging
import re
from typing import Dict, List

from ..formatting import _clear_table_data, _set_font, format_date_range
from ..sorting import sort_entries_reverse_chronological
from ..normalization import _squash

logger = logging.getLogger(__name__)
from unified_pipeline.core.render_check import entry_lines

# Taxonomy codes for Section Q: EXTRAMURAL PROFESSIONAL RESPONSIBILITIES (docs/CODING_STANDARDS.md §8.2).
SERVICE_TAXONOMY_CODES = ('Q1', 'Q2', 'Q3', 'Q4', 'Q4A', 'Q4B', 'Q4C', 'Q4D')  # every code this section routes
BOARD_SERVICE_CODE = 'Q2'               # Service on Boards and/or Committees
JOURNAL_REVIEWING_CODE = 'Q4D'          # Journal / ad hoc reviewing
LEADERSHIP_TAXONOMY_CODE = 'Q1'         # Leadership in Extramural Organizations
GRANT_REVIEWING_CODE = 'Q3'             # Grant Reviewing / Study Sections
EDITORIAL_BOARD_CODES = ('Q4B', 'Q4C')  # Editorial Board Membership roles

# Q3/Q4/Q4A/Q4B/Q4C -> (WCM template section display name, header search-text candidates)
OTHER_SERVICE_SECTION_ROUTING = {
    GRANT_REVIEWING_CODE: ('Grant Reviewing', ['Grant Reviewing', 'Study Sections']),
    'Q4': ('Professional Service', ['EXTRAMURAL PROFESSIONAL RESPONSIBILITIES', 'Leadership in Extramural']),
    'Q4A': ('Editor/Co-Editor', ['Editor/Co-Editor', 'Journals/Textbooks/Books']),
    'Q4B': ('Editorial Board', ['Editorial Board Membership', 'Editorial Activities']),
    'Q4C': ('Editorial Board', ['Editorial Board Membership', 'Editorial Activities']),
}


class ServiceSection:
    """Section Q writers, mixed into `WCMTemplateGenerator`."""

    def _fill_service(self, entries_by_code: Dict[str, List[Dict]]):
        """Fill Q. EXTRAMURAL PROFESSIONAL RESPONSIBILITIES sections using tables.

        WCM template structure:
        - Leadership in Extramural Organizations: Table with Organization, Role, Dates
        - Service on Boards: Tables for Regional/National/International with Committee, Role, Org, Dates
        - Grant Reviewing/Study Sections: Table with Role, Organization, Dates
        - Editorial Activities: Multiple tables for Editor, Editorial Board, Journal Reviewing
        """
        # Collect all Q entries
        q_entries = []
        for code in SERVICE_TAXONOMY_CODES:
            q_entries.extend(entries_by_code.get(code, []))

        if not q_entries:
            return

        if self.verbose:
            print(f"Filling Service Activities ({len(q_entries)} entries)...")

        # Reroute Q2 entries that are actually journal reviewing to Q4D
        # This handles misclassified entries where "Reviewer" role for a journal was coded as Q2
        # Also handles multi-line entries that contain mixed activities
        q2_entries = list(entries_by_code.get(BOARD_SERVICE_CODE, []))
        q4d_entries = list(entries_by_code.get(JOURNAL_REVIEWING_CODE, []))

        journal_keywords = ['journal', 'j.', 'j ', 'pediatrics', 'lancet', 'jama',
                           'perinatology', 'neonatology', 'oncology', 'cardiology', 'neurology',
                           'editorial board', 'ad hoc reviewer', 'manuscript review']
        reviewer_patterns = ['abstract reviewer', 'reviewer for', 'manuscript reviewer',
                            'peer reviewer', 'ad hoc reviewer']
        board_keywords = ['committee', 'board member', 'panel member', 'council', 'task force',
                         'working group', 'planning committee', 'advisory', 'moderator']

        rerouted_to_journal = []
        actual_board_entries = []

        for entry in q2_entries:
            text = entry.get('text', '')
            text_lower = text.lower()
            fields = entry.get('extracted_fields', {}) or {}
            role = (fields.get('role', '') or '').lower()
            committee = (fields.get('committee_name', '') or '').lower()
            org = (fields.get('organization', '') or '').lower()

            # Check if this is a multi-line entry with mixed activities
            lines = entry_lines(text)
            if len(lines) > 1:
                # Split into journal reviewing and board entries
                journal_lines = []
                board_lines = []

                for line in lines:
                    line_lower = line.lower()
                    is_reviewer_line = any(p in line_lower for p in reviewer_patterns)
                    is_board_line = any(kw in line_lower for kw in board_keywords)

                    if is_reviewer_line and not is_board_line:
                        journal_lines.append(line)
                    else:
                        board_lines.append(line)

                # Create separate entries for journal reviewing lines
                for jline in journal_lines:
                    new_entry = {
                        'text': jline,
                        'taxonomy_code': 'Q4D',
                        'rerouted_from_q2': True,
                        'extracted_fields': {'organization': jline}
                    }
                    rerouted_to_journal.append(new_entry)

                # Keep remaining lines as board entry (if any)
                if board_lines:
                    # Update the original entry to only contain board lines
                    entry['text'] = '\n'.join(board_lines)
                    actual_board_entries.append(entry)

            else:
                # Single-line entry - classify based on content
                is_journal_reviewer = (
                    (role == 'reviewer' and any(kw in text_lower for kw in journal_keywords[:10])) or
                    any(kw in text_lower for kw in ['editorial board', 'ad hoc reviewer', 'manuscript review']) or
                    any(p in text_lower for p in reviewer_patterns) or
                    (role == 'reviewer' and 'j ' in committee) or
                    (role == 'reviewer' and 'journal' in org)
                )

                # Check if this is clearly a board/committee entry
                is_board_entry = any(kw in text_lower for kw in board_keywords)

                if is_journal_reviewer and not is_board_entry:
                    # This looks like journal reviewing, reroute to Q4D
                    entry['taxonomy_code'] = 'Q4D'
                    entry['rerouted_from_q2'] = True
                    rerouted_to_journal.append(entry)
                else:
                    actual_board_entries.append(entry)

        if rerouted_to_journal and self.verbose:
            print(f"  Rerouted {len(rerouted_to_journal)} Q2 entries/lines to Journal Reviewing")

        q4d_entries.extend(rerouted_to_journal)
        q2_entries = actual_board_entries

        # Q2 entries go to "Service on Boards and/or Committees" - use National table by default
        if q2_entries:
            self._fill_service_boards(q2_entries)

        # Q4D entries go to "Journal Reviewing/Ad hoc Reviewing" table
        if q4d_entries:
            self._fill_journal_reviewing(q4d_entries)

        # Q4C and other Q4 entries (not Q4D) go to a general service table or bullet list
        other_q_entries = []
        for code in ['Q1', 'Q3', 'Q4', 'Q4A', 'Q4B', 'Q4C']:
            other_q_entries.extend(entries_by_code.get(code, []))

        if other_q_entries:
            self._fill_other_service(other_q_entries)

    def _fill_service_boards(self, entries: List[Dict]):
        """Fill Service on Boards and/or Committees tables.

        WCM template has tables for Regional/National/International.
        Uses cv_owner_location to classify geographic scope of each entry.
        Table structure: Name of Committee | Role | Organization | Dates
        """
        if not entries:
            return

        # Find the Service on Boards section
        service_idx = self._find_paragraph_with_text("Service on Boards")
        if service_idx is None:
            if self.verbose:
                print(f"  Warning: Could not find section for Service on Boards")
            return

        # Find Regional, National, and International subsection tables
        tables_by_scope = {}
        for scope in ['Regional', 'National', 'International']:
            scope_idx = None
            for i in range(service_idx, min(service_idx + 30, len(self.doc.paragraphs))):
                para_text = self.doc.paragraphs[i].text.strip()
                if para_text == scope:
                    scope_idx = i
                    break

            if scope_idx is not None:
                table = self._find_table_after_paragraph(scope_idx)
                if table:
                    tables_by_scope[scope] = table

        # Fall back to National if we couldn't find specific tables
        if not tables_by_scope:
            table = self._find_table_after_paragraph(service_idx)
            if table:
                tables_by_scope['National'] = table

        if not tables_by_scope:
            if self.verbose:
                print(f"  Warning: Could not find any table for Service on Boards")
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
            print(f"  Service on Boards: {regional_count} Regional, {national_count} National, {intl_count} International")

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
                taxonomy_code = entry.get('taxonomy_code', 'Q2')

                committee = fields.get('committee_name') or ''
                role = fields.get('role') or 'Member'
                organization = fields.get('organization') or ''

                # When Stage 4 merges committee name into the role field
                # (e.g., role="Chair, Ultrasound Committee"), split them apart
                if not committee and ', ' in role:
                    role_parts = role.split(', ', 1)
                    role_word = role_parts[0].strip().lower()
                    # Only split if the first part looks like a role title
                    if role_word in ('chair', 'co-chair', 'deputy chair', 'vice chair',
                                     'member', 'secretary', 'treasurer', 'president',
                                     'vice president', 'director', 'advisor', 'liaison',
                                     'representative', 'reviewer', 'editor', 'delegate'):
                        role = role_parts[0].strip()
                        committee = role_parts[1].strip()

                # If still no committee, fall back to organization
                if not committee:
                    committee = organization
                    organization = ''
                start_date = fields.get('start_date') or ''
                end_date = fields.get('end_date') or ''
                dates = format_date_range(start_date, end_date, taxonomy_code) or ''

                # If we don't have structured fields, parse from raw text
                if not committee:
                    text = entry.get('text', '')[:150]
                    committee = text

                # Add row to table
                row = table.add_row()
                num_cols = len(row.cells)

                # Populate based on number of columns
                # WCM template has 4 columns: Committee, Role, Organization, Dates
                if num_cols >= 4:
                    row.cells[0].text = committee or ''
                    row.cells[1].text = role or ''
                    row.cells[2].text = organization or ''
                    row.cells[3].text = dates or ''
                elif num_cols >= 3:
                    row.cells[0].text = committee or ''
                    row.cells[1].text = role or ''
                    row.cells[2].text = dates or ''
                else:
                    row.cells[0].text = f"{committee} ({role})" if role else (committee or '')
                    if num_cols > 1:
                        row.cells[1].text = dates or ''

                # Apply font formatting to each cell
                for cell in row.cells:
                    for para in cell.paragraphs:
                        for run in para.runs:
                            _set_font(run)
                self.stats['entries_inserted'] += 1

    def _fill_extramural_leadership(self, entries: List[Dict]):
        """Fill Leadership in Extramural Organizations table (Q1 entries).

        Table structure: Organization | Role | Dates
        Handles multi-line entries that need splitting.
        """
        if not entries:
            return

        # Find Leadership in Extramural Organizations section
        section_idx = self._find_paragraph_with_text("Leadership in Extramural Organizations")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("Leadership in Extramural")
        if section_idx is None:
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            return

        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1


        for entry in entries:
            original_text = entry.get('text', '')
            fields = entry.get('extracted_fields', {}) or {}

            # Check if extracted_fields has valid data - prefer using LLM extraction over raw parsing
            organization = fields.get('organization', '')
            role = fields.get('role', '')
            start_date = fields.get('start_date', '')
            end_date = fields.get('end_date', '')

            # If we have at least organization or role from extraction, use that
            # The LLM extraction is more reliable than trying to parse garbled table text
            if organization or role:
                dates = format_date_range(start_date, end_date, 'Q1')
                if not organization:
                    organization = original_text[:100]
                self._add_extramural_row(table, organization, role, dates)
            else:
                # No useful extracted fields - try to parse from raw text
                lines = entry_lines(original_text)
                if len(lines) > 3:
                    # Multiple items merged - parse and split them
                    self._parse_extramural_leadership_lines(table, lines)
                else:
                    # Single entry without extracted fields - use raw text
                    self._add_extramural_row(table, original_text[:100], '', '')

    def _parse_extramural_leadership_lines(self, table, lines: List[str]):
        """Parse multiple extramural leadership lines and add rows.

        Handles complex patterns like:
        - Organization name followed by indented roles
        - Date ranges at end of lines or in separate date block
        - Two-column table extractions where roles and dates are in separate columns
        """

        # Patterns for date detection
        year_only_pattern = re.compile(r'^(\d{4})\s*$')
        date_range_pattern = re.compile(r'^(\d{4}(?:\s*[-–]\s*(?:\d{4}|present|current))?(?:\s*,\s*\d{4}(?:\s*[-–]\s*(?:\d{4}|present|current))?)*)$', re.IGNORECASE)
        embedded_date_pattern = re.compile(r'\|\s*(\d{4}(?:\s*[-–]\s*(?:\d{4}|present|current))?)\s*$', re.IGNORECASE)
        trailing_date_pattern = re.compile(r'(\d{4}(?:\s*[-–]\s*(?:\d{4}|present|current))?)\s*$', re.IGNORECASE)

        # Role indicators
        role_keywords = ['member', 'chair', 'reviewer', 'liaison', 'mentor', 'committee',
                         'board', 'council', 'advisor', 'director', 'leader', 'representative']

        # Known organizations for context
        known_orgs = [
            'Association of Pediatric Program Directors', 'APPD',
            'American Academy of Pediatrics', 'AAP',
            'Academic Pediatric Association', 'APA',
            'Pediatric Academic Society', 'PAS',
            'National Board of Medical Examiners', 'NBME',
            'American Medical Association', 'AMA',
            'American Board of Pediatrics', 'ABP',
            'ACGME', 'Lenox Hill', 'American College',
            'Society', 'Association', 'Academy', 'Board', 'Institute'
        ]

        # First pass: categorize each line
        content_lines = []  # (text, embedded_date, is_org, is_role, original_idx)
        date_only_lines = []  # standalone dates

        for idx, line in enumerate(lines):
            line = line.strip()
            if not line or line.lower() in ['dates', 'organization', 'role', 'position']:
                continue

            # Check for date-only line (possibly multiple dates on one line)
            if date_range_pattern.match(line):
                # Split if multiple dates separated by newlines within the line
                date_parts = re.split(r'\s*\n\s*', line)
                for dp in date_parts:
                    dp = dp.strip()
                    if dp:
                        date_only_lines.append(dp)
                continue

            # Check for pipe-separated format: "Role | Date" or "Org | Role | Date"
            embedded_date = ''
            embedded_match = embedded_date_pattern.search(line)
            if embedded_match:
                embedded_date = embedded_match.group(1)
                line = line[:embedded_match.start()].strip().rstrip('|').strip()

            # Determine if this is an organization or a role
            line_lower = line.lower()
            is_org = any(org.lower() in line_lower for org in known_orgs)
            is_role = any(kw in line_lower for kw in role_keywords) and not is_org

            # Indented lines are usually sub-items (roles under an org)
            is_indented = lines[idx].startswith('   ') or lines[idx].startswith('\t')
            if is_indented:
                is_role = True
                is_org = False

            content_lines.append((line, embedded_date, is_org, is_role, idx))

        # Second pass: build items with org-role pairing
        items = []
        current_org = None

        for line, embedded_date, is_org, is_role, _ in content_lines:
            if is_org:
                current_org = line
                # If org has embedded date, it's a standalone membership
                if embedded_date:
                    items.append((current_org, 'Member', embedded_date))
                else:
                    # Just setting context, will get roles below
                    pass
            elif is_role and current_org:
                # Role under current organization
                items.append((current_org, line, embedded_date))
            elif is_role:
                # Standalone role (no org context)
                items.append((line, '', embedded_date))
            else:
                # Generic content - could be org or description
                if len(line) > 50:  # Long text is probably a description
                    items.append((line, '', embedded_date))
                else:
                    current_org = line
                    if embedded_date:
                        items.append((current_org, '', embedded_date))

        # Third pass: match date-only lines to items without dates
        # Strategy: try to match dates in order they appear
        items_needing_dates = [(i, item) for i, item in enumerate(items) if not item[2]]

        if date_only_lines and items_needing_dates:
            # If roughly equal counts, match 1:1 in order
            if abs(len(date_only_lines) - len(items_needing_dates)) <= 2:
                for (item_idx, _), date in zip(items_needing_dates, date_only_lines):
                    org, role, _ = items[item_idx]
                    items[item_idx] = (org, role, date)
            else:
                # More dates than items or vice versa - match from top
                for i, (item_idx, _) in enumerate(items_needing_dates):
                    if i < len(date_only_lines):
                        org, role, _ = items[item_idx]
                        items[item_idx] = (org, role, date_only_lines[i])

        # Add rows for each item
        for org, role, date in items:
            # Skip items with no meaningful content
            if not org and not role:
                continue
            self._add_extramural_row(table, org, role, date)

    def _add_extramural_row(self, table, organization: str, role: str, dates: str):
        """Add a single row to extramural leadership table."""
        row = table.add_row()
        num_cols = len(row.cells)

        if num_cols >= 3:
            row.cells[0].text = organization or ''
            row.cells[1].text = role or ''
            row.cells[2].text = dates or ''
        elif num_cols >= 2:
            combined = f"{organization} - {role}" if role else organization
            row.cells[0].text = combined or ''
            row.cells[1].text = dates or ''
        else:
            row.cells[0].text = f"{organization} ({role}) {dates}".strip()

        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    _set_font(run)
        self.stats['entries_inserted'] += 1

    def _fill_journal_reviewing(self, entries: List[Dict]):
        """Fill Journal Reviewing/Ad hoc Reviewing table.

        Table structure: Journal / Organization Name | Dates
        """
        if not entries:
            return

        # Filter out header-like entries (e.g., just "Reviewer", "Journal", etc.)
        header_patterns = ['reviewer', 'journal', 'ad hoc', 'editorial', 'organization']
        filtered_entries = []
        for entry in entries:
            text = entry.get('text', '').strip().lower()
            # Skip if it's a single word that looks like a header
            if text and len(text.split()) <= 2 and any(text == h for h in header_patterns):
                continue
            filtered_entries.append(entry)

        if not filtered_entries:
            return

        # Find the Journal Reviewing section
        section_idx = self._find_paragraph_with_text("Journal Reviewing")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("Ad hoc Reviewing")

        if section_idx is None:
            if self.verbose:
                print(f"  Warning: Could not find Journal Reviewing section")
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            if self.verbose:
                print(f"  Warning: Could not find Journal Reviewing table")
            return

        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        sorted_entries = sort_entries_reverse_chronological(filtered_entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {}) or {}
            taxonomy_code = entry.get('taxonomy_code', 'Q4D')

            journal = fields.get('journal_name', '') or fields.get('organization', '') or fields.get('committee_name', '')
            start_date = fields.get('start_date', '') or fields.get('year', '')
            end_date = fields.get('end_date', '')
            dates = format_date_range(start_date, end_date, taxonomy_code)

            if not journal:
                # Parse from raw text, but clean up common patterns
                raw_text = entry.get('text', '')[:100]
                # Remove "Reviewer" prefix if present
                journal = re.sub(r'^(?:Reviewer|Ad hoc Reviewer)[,:\s]*', '', raw_text, flags=re.IGNORECASE).strip()

            # Skip if still empty or too short
            if not journal or len(journal.strip()) < 3:
                continue

            row = table.add_row()
            row.cells[0].text = journal
            if len(row.cells) > 1:
                row.cells[1].text = dates

            # Apply font formatting to each cell
            for cell in row.cells:
                for para in cell.paragraphs:
                    for run in para.runs:
                        _set_font(run)
            self.stats['entries_inserted'] += 1

    def _fill_other_service(self, entries: List[Dict]):
        """Fill other Q entries that don't have specific tables.

        Uses bullet list format under appropriate section headers.
        """
        if not entries:
            return

        # Handle Q1 separately - it goes to Leadership in Extramural Organizations
        q1_entries = [e for e in entries if e.get('taxonomy_code') == LEADERSHIP_TAXONOMY_CODE]
        if q1_entries:
            self._fill_extramural_leadership(q1_entries)

        # Filter out Q1 from remaining entries
        entries = [e for e in entries if e.get('taxonomy_code') != LEADERSHIP_TAXONOMY_CODE]
        if not entries:
            return

        # Group by section
        # Q4B = Associate/Guest Editor roles, Q4C = Editorial Board Member
        # These should go to Editorial Activities section, not generic Professional Service
        # Q4A is Editor-in-Chief / Senior Editor / Co-Editor (stage_3b:807), i.e.
        # editorial -- not extramural leadership. It used to share Q1's anchor,
        # and 'EXTRAMURAL PROFESSIONAL RESPONSIBILITIES' resolves to the
        # top-level Q header whose first following table is the Leadership table
        # Q1 had just filled. The clear below then wiped it: 0 of 76 Q1
        # organizations reached their table on the 10 corpus CVs carrying both,
        # and 39 entries on 6 CVs vanished from the document entirely (#454).
        # 'Editor/Co-Editor' is Q4A's own template table and was previously dead.
        sections = OTHER_SERVICE_SECTION_ROUTING

        entries_by_section = {}
        for entry in entries:
            code = entry.get('taxonomy_code', 'Q4')
            if code in sections:
                section_name = sections[code][0]
                if section_name not in entries_by_section:
                    entries_by_section[section_name] = {'entries': [], 'search_texts': sections[code][1]}
                entries_by_section[section_name]['entries'].append(entry)

        for section_name, data in entries_by_section.items():
            section_entries = data['entries']
            search_texts = data['search_texts']

            section_idx = None
            for search_text in search_texts:
                section_idx = self._find_paragraph_with_text(search_text)
                if section_idx is not None:
                    break

            if section_idx is None:
                continue

            # Try to find and use a table first
            table = self._find_table_after_paragraph(section_idx)
            if table and id(table._element) in self._cleared_tables:
                # Another filler already owns this table. Appending is wrong but
                # recoverable; clearing destroys content that has no appendix
                # fallback, because these Q codes are all in mapped_codes. This
                # is the backstop for #454 -- with Q4A routed correctly it should
                # never fire, so say so loudly if it does.
                logger.warning(
                    "section %r resolved to a table already filled by another "
                    "code; appending instead of clearing to avoid destroying it",
                    section_name)
                self.stats['tables_populated'] += 1
            elif table:
                _clear_table_data(table, keep_header=True)
                self.stats['tables_populated'] += 1
            if table:

                sorted_entries = sort_entries_reverse_chronological(section_entries)
                for entry in sorted_entries:
                    fields = entry.get('extracted_fields', {}) or {}
                    taxonomy_code = entry.get('taxonomy_code', 'Q4')

                    role = fields.get('role', '')
                    # Q3 uses 'agency', others use 'organization' or 'committee_name'
                    # Q4B/Q4C (editorial) use 'journal_name'
                    organization = (fields.get('organization', '') or
                                    fields.get('committee_name', '') or
                                    fields.get('agency', '') or
                                    fields.get('journal_name', ''))

                    # Q3 keeps the study-section name in 'panel_name', which no
                    # code here ever read: every Grant Reviewing row rendered as
                    # a bare agency ("Reviewer | NIH | 2018-2020") and the
                    # identifier -- the entire content of the line -- was
                    # dropped. 279 of the corpus's Q3 entries across 35 CVs
                    # carry one, and 190 of those appear nowhere in the output
                    # document (#466).
                    #
                    # Appended, deliberately, rather than promoted into the
                    # chain above: agency and panel_name are BOTH populated on
                    # 286 of 380 corpus Q3 entries, so preferring panel_name
                    # would evict the agency on 269 of them and trade one
                    # omission for another. Gated on Q3 because that is the only
                    # code measured to carry the field -- panel_name is absent
                    # from all 309 Q1, 24 Q4A, 105 Q4B and 157 Q4C entries, so
                    # today the gate is a no-op that pins the intent.
                    panel_name = fields.get('panel_name', '')
                    if taxonomy_code == GRANT_REVIEWING_CODE and panel_name:
                        if not organization:
                            organization = panel_name
                        elif _squash(panel_name) not in _squash(organization):
                            organization = f"{organization} - {panel_name}"

                    start_date = fields.get('start_date', '')
                    end_date = fields.get('end_date', '')
                    dates = format_date_range(start_date, end_date, taxonomy_code)

                    # For Q4B/Q4C entries, try to parse role from raw text if missing
                    if not role and taxonomy_code in EDITORIAL_BOARD_CODES:
                        raw_text = entry.get('text', '')
                        # Pattern: "date | role | journal" or "role | journal"
                        # Match various editor/board roles
                        role_match = re.search(
                            r'\|\s*((?:Associate|Guest|Deputy|Senior|Managing|Executive|Review|Reviewing)\s*'
                            r'(?:Editor|editorial\s*board\s*member)|Editorial\s*(?:Board|board)\s*(?:Member|member)?)\s*\|',
                            raw_text, re.IGNORECASE
                        )
                        if role_match:
                            role = role_match.group(1).strip()
                        elif 'Editor' in raw_text or 'editor' in raw_text or 'Editorial' in raw_text:
                            # Try to extract role another way - check specific patterns
                            if 'Associate Editor' in raw_text:
                                role = 'Associate Editor'
                            elif 'Guest Editor' in raw_text:
                                role = 'Guest Editor'
                            elif 'Review editorial board member' in raw_text.lower():
                                role = 'Review Editorial Board Member'
                            elif 'Editorial board member' in raw_text.lower():
                                role = 'Editorial Board Member'
                            elif 'Editorial board' in raw_text.lower():
                                role = 'Editorial Board'

                    if not organization:
                        # Parse from raw text as fallback, but strip date prefix
                        raw_text = entry.get('text', '')
                        # Remove common date patterns from beginning
                        organization = re.sub(r'^\d{4}[-–]?\d{0,4}\s*\|?\s*', '', raw_text)[:100]
                        # If role already contains most of the organization text, don't duplicate
                        if role and organization and role.lower()[:30] in organization.lower():
                            organization = ''

                    row = table.add_row()
                    num_cols = len(row.cells)
                    if num_cols >= 3:
                        row.cells[0].text = role or ''
                        row.cells[1].text = organization or ''
                        row.cells[2].text = dates or ''
                    elif num_cols >= 2:
                        # Avoid duplicating content when role already contains full description
                        if role and organization and role.lower()[:20] in organization.lower():
                            row.cells[0].text = role
                        elif role and organization:
                            row.cells[0].text = f"{role}, {organization}"
                        else:
                            row.cells[0].text = role or organization
                        row.cells[1].text = dates
                    else:
                        row.cells[0].text = f"{organization} ({dates})" if dates else organization

                    # Apply font formatting to each cell
                    for cell in row.cells:
                        for para in cell.paragraphs:
                            for run in para.runs:
                                _set_font(run)
                    self.stats['entries_inserted'] += 1
