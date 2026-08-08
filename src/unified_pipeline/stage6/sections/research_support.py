"""Section M2: research support -- current, past and pending funding (#398).

Unlike every other section here, M2 does not render into one template table. It
builds an individual label/value table per grant under the M2A/M2B/M2C headers,
which is why `_create_grant_table` is the largest thing in the module.

The section also re-buckets its own records. A grant coded M2A whose end date is
already past belongs under Past (Completed) Funding, and one whose status text
reads "Under review" belongs under Pending regardless of its dates. The status
rule lives in `normalization.grant_status_rebucket_target`, imported below; the
date rule is applied by the writer, which annotates the move as a comment.

Known gap, unchanged by this move: `_create_grant_table` is a fixed-slot
`fields.get(...)` enumeration, so a stage-4 field it does not name is dropped
with no warning (`grant_number` is the standing example). Adding a field means
editing this file, not just stage 4.
"""
import re
import sys
from datetime import datetime
from typing import Dict, List, Optional

try:
    from docx.table import Table
except ImportError:  # pragma: no cover - mirrors stage_6_word_template
    print("Error: python-docx not installed. Install with: pip install python-docx lxml")
    sys.exit(1)

from ..formatting import (
    _format_currency,
    _set_cell_vertical_alignment,
    _set_font,
    _set_table_border,
    format_date_range,
)
from ..normalization import (
    _deduplicate_repeated_content,
    grant_status_rebucket_target,
)
from ..resolution import _get_cv_owner_name
from ..sorting import sort_entries_reverse_chronological


class ResearchSupportSection:
    """Section M2 writers, mixed into `WCMTemplateGenerator`."""

    def _fill_research_support(self, entries_by_code: Dict[str, List[Dict]], cv_owner: Dict = None, document_uid: str = ''):
        """Fill research support section with individual tables per grant.

        Creates a table for each grant with the WCM data model:
        - Award Source (funding agency)
        - Project title
        - Annual direct costs
        - Non-financial support
        - Duration of support (mm/yyyy-mm/yyyy)
        - Name of Principal Investigator
        - Your role
        - Your percent (%) effort
        - Major goals (optional)

        Grants are organized under WCM template headers:
        - Current Research Funding (M2A)
        - Past (Completed) Funding (M2B)
        - Pending Funding (M2C)

        Reclassification: Grants classified as M2A (current) but with end dates
        before the current year are moved to M2B (completed) with a comment.
        """
        # Get CV owner name for auto-filling PI when role is Principal Investigator
        owner_name = _get_cv_owner_name(cv_owner, document_uid)
        current_year = datetime.now().year

        # Filter out role/effort header entries and extract percent effort metadata
        # These are entries like "Individual's role in project including percent effort\nProject Name 0.01"
        role_effort_pattern = re.compile(
            r"(?:Individual's role|your role|role in project|percent effort)",
            re.IGNORECASE
        )
        effort_lookup = {}  # project_name_normalized -> percent_effort

        def filter_and_extract_effort(entries):
            """Filter out role/effort headers and build effort lookup."""
            filtered = []
            for entry in entries:
                text = entry.get('text', '')
                # Check if this is a role/effort header entry
                if role_effort_pattern.search(text[:60]):
                    # Parse project names and their percent efforts from this entry
                    # Pattern: "Project Title 0.01" or "Project Title .08FTE"
                    lines = text.split('\n')
                    for line in lines[1:]:  # Skip the header line
                        line = line.strip()
                        if not line:
                            continue
                        # Match pattern: "Project Name 0.01" or "Project Name .08FTE"
                        effort_match = re.search(r'^(.+?)\s+(\d*\.?\d+)\s*(?:FTE)?$', line, re.IGNORECASE)
                        if effort_match:
                            project_name = effort_match.group(1).strip().lower()
                            effort_value = effort_match.group(2)
                            # Normalize to percentage (0.01 -> 1%, .08 -> 8%)
                            try:
                                effort_float = float(effort_value)
                                if effort_float <= 1:
                                    effort_pct = f"{int(effort_float * 100)}%"
                                else:
                                    effort_pct = f"{int(effort_float)}%"
                                effort_lookup[project_name] = effort_pct
                            except ValueError:
                                pass
                    if self.verbose:
                        print(f"  Filtered role/effort header entry, extracted {len(effort_lookup)} effort values")
                    continue  # Skip this entry, don't add to filtered
                filtered.append(entry)
            return filtered

        # Reclassify M2A grants with past end dates to M2B
        m2a_entries = filter_and_extract_effort(list(entries_by_code.get('M2A', [])))
        m2b_entries = filter_and_extract_effort(list(entries_by_code.get('M2B', [])))
        m2c_entries = filter_and_extract_effort(list(entries_by_code.get('M2C', [])))

        # Apply extracted percent effort to matching grants
        def apply_effort_to_grants(entries):
            for entry in entries:
                fields = entry.get('extracted_fields', {})
                title = (fields.get('title', '') or '').lower().strip()
                if title and not fields.get('percent_effort'):
                    # Try to find matching effort in lookup
                    for project_name, effort in effort_lookup.items():
                        if project_name in title or title in project_name:
                            fields['percent_effort'] = effort
                            if self.verbose:
                                print(f"  Matched effort {effort} to '{title[:40]}...'")
                            break

        apply_effort_to_grants(m2a_entries)
        apply_effort_to_grants(m2b_entries)
        apply_effort_to_grants(m2c_entries)

        # Rebucket by each grant's own extracted status BEFORE date inference:
        # an explicit "Under review" / "Not funded" beats everything (#210).
        bucket_lists = {'M2B': m2b_entries, 'M2C': m2c_entries}
        for source_code, source_list in (('M2A', m2a_entries), ('M2B', m2b_entries)):
            for entry in list(source_list):
                fields = entry.get('extracted_fields') or {}
                target, note = grant_status_rebucket_target(fields.get('status'))
                if target and target != source_code:
                    source_list.remove(entry)
                    entry.setdefault('reclassification_note', note)
                    bucket_lists[target].append(entry)
                    if self.verbose:
                        title = str(fields.get('title') or 'Unknown')
                        print(f"  Status rebucket {source_code}->{target}: '{title[:40]}'")

        # Check each M2A entry for past end dates
        entries_to_move = []
        for entry in m2a_entries:
            fields = entry.get('extracted_fields', {})
            end_date = fields.get('end_date', '')

            # Parse end date to check if it's in the past
            if end_date and end_date.lower() not in ('present', 'current', 'ongoing', ''):
                # Try to extract year from end date
                year_match = re.search(r'(\d{4})', str(end_date))
                if year_match:
                    end_year = int(year_match.group(1))
                    if end_year < current_year:
                        # This grant has ended - reclassify to M2B
                        entries_to_move.append((entry, end_date, end_year))

        # Move entries and add reclassification comments
        for entry, end_date, end_year in entries_to_move:
            m2a_entries.remove(entry)

            # Add reclassification note to the entry
            if 'reclassification_note' not in entry:
                entry['reclassification_note'] = f"Reclassified from Current (M2A) to Completed (M2B): end date {end_date} is before {current_year}"

            m2b_entries.append(entry)

            if self.verbose:
                title = entry.get('extracted_fields', {}).get('title') or 'Unknown'
                print(f"  Reclassified to M2B: '{title[:40]}...' (ended {end_year})")

        # Map taxonomy codes to WCM template section headers
        # These must match the exact text in the official WCM template
        categories = [
            ('M2A', 'Current Research Funding', m2a_entries),
            ('M2B', 'Past (Completed) Funding', m2b_entries),
            ('M2C', 'Pending Funding', m2c_entries),
        ]

        total_grants = sum(len(entries) for _, _, entries in categories)
        if self.verbose:
            print(f"Filling Research Support ({total_grants} grants)...")

        # Process each category - find the corresponding section in the template
        for code, section_header, entries in categories:
            # Find the section header in the template
            section_idx = self._find_paragraph_with_text(section_header)
            if section_idx is None:
                if self.verbose:
                    print(f"  Warning: Could not find section header '{section_header}'")
                continue

            # Remove any existing template table after this section
            # (even if no entries, to avoid leaving empty template tables)
            existing_table = self._find_table_after_paragraph(section_idx)
            if existing_table:
                existing_table._element.getparent().remove(existing_table._element)

            if not entries:
                continue

            # Sort entries reverse chronologically (most recent first)
            sorted_entries = sort_entries_reverse_chronological(entries)

            if self.verbose:
                print(f"  {code}: {len(entries)} grants -> '{section_header}'")

            # Create a table for each grant with spacing between them
            # Track the last inserted element (as actual XML element reference)
            last_element = self.doc.paragraphs[section_idx]._element

            for i, entry in enumerate(sorted_entries):
                fields = entry.get('extracted_fields', {})

                # Create grant table - pass the element to insert after and owner name
                grant_table = self._create_grant_table(fields, code, entry, insert_after_element=last_element, owner_name=owner_name)
                if grant_table:
                    self.stats['tables_populated'] += 1
                    self.stats['entries_inserted'] += 1

                    # Update last_element to the newly inserted table
                    last_element = grant_table._tbl

                    # Add blank paragraph after each grant table for spacing
                    # (except after the last one in each category)
                    if i < len(sorted_entries) - 1:
                        spacing_para = self._add_spacing_paragraph(after_element=grant_table._tbl)
                        if spacing_para is not None:
                            last_element = spacing_para

    def _create_grant_table(self, fields: Dict, code: str, entry: Dict = None, insert_after_element=None, owner_name: str = '') -> Optional[Table]:
        """Create an individual grant table with the WCM data model.

        Args:
            fields: Extracted field data for the grant
            code: Taxonomy code (M2A, M2B, M2C)
            entry: Full entry dict for comments/metadata
            insert_after_element: XML element to insert after. If None, falls back to RESEARCH SUPPORT.
            owner_name: CV owner's name for auto-filling PI when role is Principal Investigator

        Returns:
            Table object, or None if entry is too sparse to create a useful table
        """
        # Validate minimum required fields - skip header-like entries
        # A valid grant should have at least a title OR (agency + role/dates)
        # Check multiple title field names since clinical trials use trial_title/study_title/text
        title = fields.get('title', '') or fields.get('trial_title', '') or fields.get('study_title', '') or fields.get('text', '')

        # Clean up title - remove repeated content from merged table cells
        # e.g., "Title .08FTE | Title .08FTE | Title .08FTE" -> "Title .08FTE"
        title = _deduplicate_repeated_content(title)

        agency = fields.get('agency') or fields.get('funding_source', '') or fields.get('sponsor', '')
        total_funding = fields.get('total_funding', '') or fields.get('annual_direct_costs', '')

        # Detect and fix cross-field duplication where the same content appears in multiple fields
        # This happens when Stage 4 incorrectly puts the same text in agency, title, AND funding
        if title and agency and title.strip().lower() == agency.strip().lower():
            # Agency and title are identical - keep as title only, clear agency
            agency = ''
        if title and total_funding and title.strip().lower() == total_funding.strip().lower():
            # Funding is same as title - clear funding
            total_funding = ''
            fields['total_funding'] = ''
            fields['annual_direct_costs'] = ''
        role = fields.get('pi_role') or fields.get('role', '') or fields.get('description', '')
        start_date = fields.get('start_date', '') or fields.get('date', '')
        end_date = fields.get('end_date', '')

        has_title = bool(title and len(title.strip()) > 10)
        # For clinical trials: if we have a title and a date, that's substantive enough
        has_substantive_info = bool((agency and (role or start_date or end_date)) or (title and start_date))

        if not has_title and not has_substantive_info:
            # This is likely a header like "Funding: National Cancer Institute" - skip it
            if self.verbose:
                text = entry.get('text', '')[:50] if entry else ''
                print(f"  Skipping sparse grant entry: '{text}...'")
            return None

        # Get role and determine PI name
        role = fields.get('pi_role') or fields.get('role', '')
        percent_effort = fields.get('percent_effort', '')

        # Fix: If role looks like a percent value (just a number, possibly with %), it was misextracted
        # The LLM sometimes puts percent effort in the pi_role field
        if role and not percent_effort:
            role_stripped = role.strip().rstrip('%').strip()
            if role_stripped.isdigit() or (role_stripped.replace('.', '', 1).isdigit()):
                # This looks like a percent value, not a role description
                percent_effort = role
                role = ''

        pi_name = fields.get('pi_name') or fields.get('principal_investigator', '') or fields.get('co_investigators', '')

        # Parse PI name from raw text if not in extracted fields
        # Common format: "Agency | Amount | Dates | PI Name"
        raw_text = entry.get('text', '') if entry else ''
        if not pi_name and raw_text and '|' in raw_text:
            parts = [p.strip() for p in raw_text.split('|')]
            # Skip if all parts are identical (repeated content from merged cells)
            unique_parts = set(p.lower() for p in parts if p)
            if len(parts) >= 4 and len(unique_parts) > 1:
                # The last part is often the PI name
                potential_name = parts[-1].strip()
                # Check if it looks like a name:
                # - Not just digits/dates
                # - Matches name pattern (First Last or Last, First)
                # - Short enough to be a name (< 50 chars)
                # - Doesn't contain project/grant keywords
                project_keywords = ['project', 'study', 'grant', 'research', 'program', 'trial',
                                    'investigation', 'promotion', 'implementation', 'development']
                if (potential_name and
                    len(potential_name) < 50 and
                    not re.match(r'^[\d\-/]+$', potential_name) and
                    not any(kw in potential_name.lower() for kw in project_keywords)):
                    # Check for name patterns (First Last or Last, First)
                    if re.match(r'^[A-Z][a-z]+(?:\s+[A-Z]\.?)?\s+[A-Z][a-z]+$', potential_name):
                        pi_name = potential_name

        # Auto-fill PI name when role indicates Principal Investigator and no PI name specified
        if not pi_name and owner_name and 'principal' in role.lower() and 'investigator' in role.lower():
            pi_name = owner_name

        # Format costs as currency
        costs = fields.get('annual_direct_costs') or fields.get('total_funding', '')
        costs_formatted = _format_currency(costs)

        # Carry the grant/award identifier in Award Source. The WCM template has no
        # grant-number row -- its block is exactly these 8 rows plus optional goals --
        # but the Award Source label itself reads "(funding agency ...; type of grant)",
        # so the identifier belongs there. Without this, stage 4 extracts grant_number
        # and no renderer ever consumes it: 528 of 537 corpus values reached no render.
        grant_number = (fields.get('grant_number') or '').strip()
        if grant_number and grant_number.casefold() not in f"{agency} {title}".casefold():
            agency = f"{agency} ({grant_number})" if agency else grant_number

        # Define the grant data model rows
        # Use the extracted title/agency variables (which check multiple field names) instead of just fields.get()
        rows = [
            ('Award Source:', agency),
            ('Project title:', title),
            ('Annual direct costs:', costs_formatted),
            ('Non-financial support:', fields.get('non_financial_support', '')),
            ('Duration of support:', self._format_grant_duration(fields, code)),
            ('Name of Principal Investigator:', pi_name),
            ('Your role:', role),
            ('Your percent (%) effort:', percent_effort),
        ]

        # Add optional major goals if present (check major_goals, description, or narrative)
        goals = fields.get('major_goals') or fields.get('description', '') or fields.get('narrative', '')
        if goals and len(goals.strip()) > 10:  # Only if substantive
            rows.append(('Major project goals:', goals))

        # Create a new table with 2 columns
        table = self.doc.add_table(rows=len(rows), cols=2)

        # Set table borders and formatting
        _set_table_border(table, color='808080', size=4)

        # Fill in the table
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

        # Move table to correct position
        body = self.doc.element.body
        body_elements = list(body)

        # Determine insertion point
        if insert_after_element is not None:
            insert_element = insert_after_element
        else:
            # Fallback: find RESEARCH SUPPORT
            support_idx = self._find_paragraph_with_text("RESEARCH SUPPORT")
            if support_idx is None:
                return table
            insert_element = self.doc.paragraphs[support_idx]._element

        # Find where to insert and place table after the element
        try:
            elem_idx = body_elements.index(insert_element)
            body.insert(elem_idx + 1, table._tbl)
        except (ValueError, IndexError):
            pass

        return table

    def _format_grant_duration(self, fields: Dict, taxonomy_code: str = 'M2A') -> str:
        """Format grant duration according to WCM requirements.

        Grants use mm/yy format per the template.
        Clinical trials may use 'date' instead of 'start_date'.
        """
        start = fields.get('start_date', '') or fields.get('date', '')
        end = fields.get('end_date', '')

        return format_date_range(start, end, taxonomy_code)
