"""Section L: clinical practice, innovation and leadership (#398).

One writer over three subsections -- L1 practice, L2 innovations, L3 leadership
-- because each is the same two-branch shape and only the field names and the
header text differ:

    find the subsection header -> find the table under it -> validate the table
    -> fill rows, or fall back to bullets when there is no usable table.

The validation step is the part worth knowing about. `_find_table_after_paragraph`
returns whatever table comes next in the document, and in CVs where a clinical
subsection is empty that is the *funding* table from a later section (the blank
WCM template ships no table of its own under any of L1/L2/L3 -- verified via
python-docx, #625 review). Each of the three branches therefore classifies row 0
via `_clinical_header_match`: a header matching the WCM template's standard
"Title | Institution/Location | Dates" convention, or an ambiguous one, is
accepted; only a header confidently saying "Award Source" or "Funding" is
refused. An unrecognized header is never rejected outright, so a table this
function can't positively classify still gets filled rather than silently
dropping content.

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
from ..normalization import _committee_cell_text
from ..parsing import _is_structural_label
from ..sorting import sort_entries_reverse_chronological
from unified_pipeline.core.render_check import entry_lines

# Section L taxonomy codes (docs/CODING_STANDARDS.md §8.2): L1 Clinical
# Practice, L2 Clinical Innovations, L3 Clinical Leadership.
CLINICAL_TAXONOMY_CODES = ('L1', 'L2', 'L3')
CLINICAL_PRACTICE_CODE, CLINICAL_INNOVATION_CODE, CLINICAL_LEADERSHIP_CODE = CLINICAL_TAXONOMY_CODES

# Positive-match target header for the L1/L2/L3 subsection tables.
#
# Verified against key_files/wcm_cv_template_faculty_october_2022_final.docx
# via python-docx (#625 review): the *blank* template ships no table at all
# under Clinical Practice/Innovations/Leadership -- paragraphs 96-107 are
# pure instruction prose, and the next actual table in the document is the
# "Award Source" funding table the guard below already excludes. So this
# exact string cannot be read off an in-place Clinical Practice table the
# way, say, Section O's canonical header can. It is the WCM template's
# standard 3-column "Title | Institution/Location | Dates" convention used
# by every other single-item-per-row subsection table in the same document
# (byte-identical on doc.tables[30:33], the Invitations to Speak Regional/
# National/International tables; the sibling convention 'Role(s)/Position |
# Institution/Location | Dates' is Section O's own table, see leadership.py)
# -- the best available real-header source, not a byte-exact Clinical
# Practice header. Because of that, a mismatch here is treated as merely
# ambiguous, never as a rejection (see `_clinical_header_match`).
_CLINICAL_TABLE_HEADER = ('Title', 'Institution/Location', 'Dates (yyyy)')

# Header substrings that confidently mean "this is a funding/grant table, not
# a clinical one" -- the negative check `_clinical_header_match` still runs
# first, unchanged from the pre-existing per-renderer guard (#625 review: was
# duplicated three times, now lives once here).
_FUNDING_HEADER_INDICATORS = ('award source', 'funding')


def _clinical_header_match(first_row_cells) -> Optional[bool]:
    """Classify a table's header row against the real subsection header.

    Returns True on a positive, tolerant match to `_CLINICAL_TABLE_HEADER`
    (case-insensitive; a real CV table may have been hand-edited by the
    faculty member, so this only requires the first cell to start with
    "title" and the second to mention "institution" or "location" -- not a
    byte-exact match), False when the header is confidently a funding table
    ("Award Source"/"Funding"), and None otherwise.

    An ambiguous header (education, membership, awards, or anything else
    `_find_table_after_paragraph` might have handed back) stays None so the
    caller can fall back to the existing permissive accept-by-default
    behaviour rather than reject a table it isn't sure about -- failing
    closed here would silently drop real clinical content into no table at
    all (HARD SAFETY GATE, #625 review).
    """
    texts = [c.text.strip().lower() for c in first_row_cells]
    joined = ' '.join(texts)
    if any(indicator in joined for indicator in _FUNDING_HEADER_INDICATORS):
        return False
    expected_first_cell = _CLINICAL_TABLE_HEADER[0].lower()  # 'title'
    if len(texts) >= 2 and texts[0].startswith(expected_first_cell) and (
        'institution' in texts[1] or 'location' in texts[1]
    ):
        return True
    return None


class ClinicalPracticeSection:
    """Section L writers, mixed into `WCMTemplateGenerator`."""

    def _add_table_row(self, table, three_col: List[str], two_col: List[str], one_col: List[str]):
        """Add a row to `table`, populate it according to its actual column
        count, and apply the standard cell font to every run.

        Centralizes the row-creation/text-write/font-apply sequence that was
        previously duplicated across the L1/L2/L3 renderers below (#625
        review). `three_col`, `two_col`, and `one_col` are the cell values to
        use for a 3-, 2-, and 1-column table respectively -- this module's
        tables are always 3-column in the real WCM template; the narrower
        branches exist only so a differently-shaped table still renders
        something instead of raising. Every value is run through
        `_committee_cell_text` before the write, so a non-string extracted
        field (list/dict; #256, #442, #450) can never reach `cell.text =`
        and abort the whole document.
        """
        row = table.add_row()
        n = len(row.cells)
        if n >= 3:
            values = three_col
        elif n >= 2:
            values = two_col
        else:
            values = one_col
        for cell, value in zip(row.cells, values):
            cell.text = _committee_cell_text(value)
        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    _set_font(run)
        return row

    def _fill_clinical_practice(self, entries_by_code: Dict[str, List[Dict]]):
        """Fill L. CLINICAL PRACTICE, INNOVATION, and LEADERSHIP section.

        This section has three subsections:
        - L1: Clinical Practice (patient care activities)
        - L2: Clinical Innovations (new approaches to care)
        - L3: Clinical Leadership (director/head roles)

        Each subsection uses a simple bulleted or table format. The three
        subsection bodies are extracted into their own methods below -- see
        each method's docstring for what it renders and how; this method only
        finds the entry lists and dispatches to them.
        """
        l1_entries = entries_by_code.get(CLINICAL_PRACTICE_CODE, [])
        l2_entries = entries_by_code.get(CLINICAL_INNOVATION_CODE, [])
        l3_entries = entries_by_code.get(CLINICAL_LEADERSHIP_CODE, [])

        total = len(l1_entries) + len(l2_entries) + len(l3_entries)
        if total == 0:
            return

        if self.verbose:
            print(f"Filling Clinical Practice ({len(l1_entries)} L1, {len(l2_entries)} L2, {len(l3_entries)} L3)...")

        self._fill_clinical_practice_l1(l1_entries)
        self._fill_clinical_practice_l2(l2_entries)
        self._fill_clinical_practice_l3(l3_entries)

    def _fill_clinical_practice_l1(self, l1_entries: List[Dict]):
        """Fill the L1 Clinical Practice subsection: table rows, or a bullet
        fallback when no valid table is found.

        Extracted from `_fill_clinical_practice` as a pure relocation -- the
        section-finder (exact match, then a "starts with" scan that excludes
        Innovations/Leadership), the pipe-fallback column count, and the table
        column order/merge style are each deliberately different from L2/L3's
        versions below; see the module docstring for why they were not unified.
        """
        if not l1_entries:
            return

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

            # Validate table is actually for Clinical Practice, not a different section.
            # A positive match against the real header confirms it; a
            # confident non-match (funding table) rejects it; anything
            # ambiguous falls back to the previous permissive accept
            # (#625 review -- see `_clinical_header_match`).
            table_is_valid = False
            if table and table.rows:
                header_match = _clinical_header_match(table.rows[0].cells)
                if header_match is False:
                    if self.verbose:
                        print(f"  Skipping table (appears to be grant table, not clinical practice)")
                else:
                    table_is_valid = True

            sorted_entries = sort_entries_reverse_chronological(l1_entries)

            if table and table_is_valid:
                _clear_table_data(table, keep_header=True)
                self.stats['tables_populated'] += 1
                for entry in sorted_entries:
                    fields = entry.get('extracted_fields', {}) or {}
                    original_text = entry.get('text', '')

                    # Extract fields - clinical practice entries typically have:
                    # activity/location, institution, dates
                    # Normalize here (not just at the eventual cell write) so a
                    # non-string field never reaches the "Activity - Location"
                    # string-concatenation fallback below either (#625 review).
                    activity = _committee_cell_text(
                        fields.get('activity') or fields.get('role') or fields.get('title') or '')
                    location = _committee_cell_text(
                        fields.get('location') or fields.get('institution') or '')
                    start_date = fields.get('start_date') or ''
                    end_date = fields.get('end_date') or ''
                    dates = format_date_range(start_date, end_date, 'L1') or ''

                    # Fallback to parsing original text if fields are empty
                    if not activity and original_text:
                        # Parse "Activity | Location | Dates" format, all three
                        # columns explicitly -- parts[1] used to be dropped on
                        # the floor here (#625 review).
                        parts = original_text.split('|')
                        if len(parts) >= 2:
                            activity = parts[0].strip()
                            if not location:
                                location = parts[1].strip()
                            if len(parts) >= 3:
                                dates = parts[-1].strip() if not dates else dates

                    if activity or location:
                        # Typical clinical practice table: Activity/Type | Location | Dates
                        self._add_table_row(
                            table,
                            three_col=[activity, location, dates],
                            two_col=[activity if activity else location, dates],
                            one_col=[f"{activity} - {location} ({dates})" if dates else f"{activity} - {location}"],
                        )
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

    def _fill_clinical_practice_l2(self, l2_entries: List[Dict]):
        """Fill the L2 Clinical Innovations subsection: table rows, or a
        bullet fallback when no valid table is found.

        Extracted from `_fill_clinical_practice` as a pure relocation -- see
        `_fill_clinical_practice_l1` and the module docstring for why this
        subsection's table layout and fallback text are not unified with L1/L3.
        """
        if not l2_entries:
            return

        section_idx = self._find_paragraph_with_text("Clinical Innovations")
        if section_idx is not None:
            table = self._find_table_after_paragraph(section_idx)

            # Validate the table is actually for innovations, not a grant/funding table
            # (see `_clinical_header_match`: positive match confirms it, a
            # confident non-match rejects it, ambiguous falls back to accept).
            table_is_valid = False
            if table and table.rows:
                header_match = _clinical_header_match(table.rows[0].cells)
                if header_match is False:
                    if self.verbose:
                        print(f"  Skipping table (appears to be grant table, not clinical innovations)")
                else:
                    table_is_valid = True

            sorted_entries = sort_entries_reverse_chronological(l2_entries)

            if table and table_is_valid:
                _clear_table_data(table, keep_header=True)
                self.stats['tables_populated'] += 1

                for entry in sorted_entries:
                    fields = entry.get('extracted_fields', {}) or {}
                    original_text = entry.get('text', '')

                    # Normalize at extraction (#625 review) so a non-string
                    # field is clean before any string concatenation below.
                    title = _committee_cell_text(fields.get('title') or fields.get('innovation') or '')
                    role = _committee_cell_text(fields.get('role') or '')
                    description = _committee_cell_text(fields.get('description') or '')
                    start_date = fields.get('start_date') or fields.get('date') or ''
                    dates = format_date_for_section(start_date, 'L2') if start_date else ''

                    if not title and original_text:
                        # Preserve the complete source text on the fallback
                        # path -- an arbitrary 100-char cut here used to
                        # discard clinically relevant information with no
                        # business rule behind the number (#625 review).
                        title = original_text.split('|')[0].strip() if '|' in original_text else original_text

                    if title:
                        # Typical innovation table: Date | Title/Location | Role/Description
                        self._add_table_row(
                            table,
                            three_col=[dates, title, f"{role}. {description}".strip('. ') if role or description else ''],
                            two_col=[dates, title],
                            one_col=[f"{dates}: {title}" if dates else title],
                        )
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

    def _fill_clinical_practice_l3(self, l3_entries: List[Dict]):
        """Fill the L3 Clinical Leadership subsection: table rows, or a
        bullet fallback when no valid table is found.

        Extracted from `_fill_clinical_practice` as a pure relocation -- see
        `_fill_clinical_practice_l1` and the module docstring for why this
        subsection's bullet-fallback text construction is fundamentally
        different from L1/L2's (deliberate, not accidental).
        """
        if not l3_entries:
            return

        section_idx = self._find_paragraph_with_text("Clinical Leadership")
        if section_idx is not None:
            table = self._find_table_after_paragraph(section_idx)

            # Validate the table is actually a leadership table, not a grant/funding table
            # (see `_clinical_header_match`: positive match confirms it, a
            # confident non-match rejects it, ambiguous falls back to accept).
            table_is_valid = False
            if table and table.rows:
                header_match = _clinical_header_match(table.rows[0].cells)
                if header_match is False:
                    if self.verbose:
                        print(f"  Skipping table (appears to be grant table, not clinical leadership)")
                else:
                    table_is_valid = True

            sorted_entries = sort_entries_reverse_chronological(l3_entries)

            if table and table_is_valid:
                _clear_table_data(table, keep_header=True)
                self.stats['tables_populated'] += 1

                for entry in sorted_entries:
                    fields = entry.get('extracted_fields', {}) or {}
                    original_text = entry.get('text', '')

                    # Normalize at extraction (#625 review) so a non-string
                    # field is clean before any string concatenation below.
                    role = _committee_cell_text(
                        fields.get('role') or fields.get('leadership_role') or fields.get('title') or '')
                    institution = _committee_cell_text(
                        fields.get('institution') or fields.get('organization') or '')
                    description = _committee_cell_text(fields.get('description') or fields.get('program') or '')
                    start_date = fields.get('start_date') or ''
                    end_date = fields.get('end_date') or ''
                    dates = format_date_range(start_date, end_date, 'L3') or ''

                    if not role and original_text:
                        parts = original_text.split('|')
                        if len(parts) >= 1:
                            role = parts[0].strip()

                    if role:
                        # Typical leadership table: Year(s) | Role | Description
                        self._add_table_row(
                            table,
                            three_col=[dates, role, f"{institution}. {description}".strip('. ') if institution or description else ''],
                            two_col=[dates, f"{role} - {institution}" if institution else role],
                            one_col=[f"{dates}: {role}" if dates else role],
                        )
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

                    # Normalize at extraction (#625 review): this text feeds
                    # a bullet paragraph, not a table cell, but the same
                    # non-string-field hazard applies to the f-string below.
                    role = _committee_cell_text(
                        fields.get('role') or fields.get('leadership_role') or fields.get('title') or '')
                    institution = _committee_cell_text(
                        fields.get('institution') or fields.get('organization') or '')
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
