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
routes each entry to the template row whose label means the same thing as the
entry's own label (#571), keeps the template's own label, and appends the value
after a tab -- the template line reads "Name of Current Employer(s):" and must
keep reading that way. An entry whose label matches no row is logged and NOT
written: filing a value under a label that is not its own is a wrong factual
claim in a delivered CV, worse than an omission. It prefers the employer
paragraph to the section header as the scan anchor, because the header is a
heading with nothing to fill.

G writes into a table, but only after checking that it is the right one.
`_find_table_after_paragraph` returns whatever table comes next in the document,
so the first cell is tested for "hospital", "affiliation" or "primary" before
anything is cleared; a template whose affiliation section has no table falls
back to bullets under the header instead of writing into a stranger's table.

Percent effort is deliberately absent. It is in the same part of the template
and looks like it belongs, but it is filled by hand.

Neither section has a taxonomy code of its own that `generate()`'s
RENDER_ROUTED_CODES dispatch can key on, so each writer here reports back
exactly which entry dicts it actually wrote (never merely matched -- a
refused E label is not in that list). `generate()` uses that to keep a
written entry out of the Appendix a second time (#294) without keying
anything off taxonomy code at all.
"""
import logging

from ..formatting import _clear_table_data, _set_font
from ..normalization import _squash

logger = logging.getLogger(__name__)

# The Employment Status template rows, recognized by keyword because source CVs
# word the labels loosely ("Name of Employer(s)", "Current Employer", ...) and
# template revisions reword them too. Keywords are tested against a _squash()ed
# label, so wording, spacing and case differences all collapse. A row's
# keywords must ALL appear in the label (not just one) -- a lone generic word
# like "status" or "date" also shows up in unrelated labels ("Application
# Status", "Date of Birth") that have nothing to do with employment, so each
# row pairs its generic word with "employ" to stay specific to this section.
# Rows are checked in this dict order and the first full match wins; the
# keyword sets don't overlap so order shouldn't matter in practice, but if a
# future keyword addition makes two rows match the same label, dict order is
# the tiebreak. The same classifier runs on the entry's label and on the
# template paragraph's label; an entry is written only into the row that
# classifies the SAME way, never into whichever row happens to be found
# first (#571).
_EMPLOYMENT_ROW_KEYWORDS: dict[str, tuple[str, ...]] = {
    'employer': ('employer',),
    'employment status': ('status', 'employ'),
    'position/title': ('position',),
    'dates of employment': ('date', 'employ'),
}

# How many paragraphs past the section anchor may hold its label rows. The
# October-2022 template's block is the employer row, the status row, then a
# dozen colon-free status options; the bound keeps a "Title:" or "Date:"
# paragraph belonging to a LATER section out of reach.
_EMPLOYMENT_ROW_SCAN_WINDOW = 12


def _employment_row_key(label: str) -> str | None:
    """Which Employment Status template row a "Label:" belongs to, or None."""
    squashed = _squash(label)
    for row_key, keywords in _EMPLOYMENT_ROW_KEYWORDS.items():
        if all(keyword in squashed for keyword in keywords):
            return row_key
    return None


class PassthroughSection:
    """Section E and G writers, mixed into `WCMTemplateGenerator`."""

    def _fill_passthrough_sections(self, all_entries: list[dict]) -> list[dict]:
        """Fill sections that can be copied directly from source CV when format matches.

        These are short, structured sections in the WCM template that may already exist
        in the source CV in the same format. If found, we copy them directly.

        Sections handled:
        - E. EMPLOYMENT STATUS
        - G. INSTITUTIONAL/HOSPITAL AFFILIATION

        Note: PERCENT EFFORT is complex and typically filled manually.

        Args:
            all_entries: All entries from the pipeline (to search by hierarchy)

        Returns:
            The union of entry dicts each writer actually wrote into the
            document -- not merely matched by hierarchy. `generate()` uses
            this to keep a consumed entry out of the Appendix a second time
            (#294); an entry a writer matched but refused (e.g. an E label
            with no known template row, #571) is NOT in this list, so it
            still reaches the Appendix as before.
        """
        return self._fill_employment_status(all_entries) + self._fill_hospital_affiliation(all_entries)

    def _fill_employment_status(self, all_entries: list[dict]) -> list[dict]:
        """Fill E. EMPLOYMENT STATUS section.

        Looks for entries with hierarchy containing 'EMPLOYMENT STATUS' and
        text in 'Label: Value' format (e.g., 'Name of Employer(s): Weill Cornell').

        Returns the entries actually written (#294) -- an entry whose label
        names no known row, or whose row is not found in the template, is
        matched but not written, and is excluded from this list.
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
            return []

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
            return []

        if self.verbose:
            print(f"  Passthrough: Filling Employment Status ({len(matching_entries)} entries)")

        # Route each entry to the template row its OWN label names. The first
        # 'employer' paragraph must not win for every entry -- that files a
        # position or a date under "Name of Current Employer(s):" (#571).
        consumed = []
        for entry in matching_entries:
            text = entry.get('text', '').strip()
            if ':' not in text:
                continue
            parts = text.split(':', 1)
            label = parts[0].strip()
            value = parts[1].strip()

            row_key = _employment_row_key(label)
            if row_key is None:
                logger.warning(
                    "Employment Status entry label %r names no known template row; "
                    "entry not written", label)
                continue
            if self._write_employment_row(target_idx, row_key, value):
                consumed.append(entry)
            else:
                logger.warning(
                    "Employment Status entry %r matched no template row near the "
                    "section; entry not written", label)
        return consumed

    def _write_employment_row(self, anchor_idx: int, row_key: str, value: str) -> bool:
        """Write `value` into the Employment Status row that classifies as `row_key`.

        Scans forward from the section anchor (bounded, so a look-alike label in
        a later section is unreachable), keeps the template's own label text,
        and appends the value after a tab. Returns False -- and counts nothing --
        when no row in the window matches, so `entries_inserted` never reports a
        write that did not happen.
        """
        window = self.doc.paragraphs[anchor_idx:anchor_idx + _EMPLOYMENT_ROW_SCAN_WINDOW]
        for para in window:
            para_text = para.text.strip()
            if ':' not in para_text:
                continue
            template_label = para_text[:para_text.find(':')]
            if _employment_row_key(template_label) != row_key:
                continue
            para.clear()
            run = para.add_run(f"{template_label}:\t{value}")
            _set_font(run)
            self.stats['entries_inserted'] += 1
            return True
        return False

    def _fill_hospital_affiliation(self, all_entries: list[dict]) -> list[dict]:
        """Fill G. INSTITUTIONAL/HOSPITAL AFFILIATION section.

        Looks for dedicated hospital affiliation entries or extracts from D2 positions.

        Returns the entries actually written (#294): every entry that matches
        the section's hierarchy gets a row or a bullet UNLESS the section or
        its table cannot be located at all, in which case nothing is written
        and this returns [].
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
            return []

        # Find the affiliation table
        section_idx = self._find_paragraph_with_text('INSTITUTIONAL/HOSPITAL AFFILIATION')
        if section_idx is None:
            section_idx = self._find_paragraph_with_text('HOSPITAL AFFILIATION')

        if section_idx is None:
            if self.verbose:
                print("  Passthrough: Could not find Hospital Affiliation section")
            return []

        # Find table after the section
        table = self._find_table_after_paragraph(section_idx)

        consumed = []

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
                    consumed.append(entry)
            else:
                # No table - insert as bullet points after the section header
                for i, entry in enumerate(matching_entries):
                    text = entry.get('text', '').strip()
                    if text:
                        insert_idx = section_idx + 1 + i
                        self._insert_bulleted_entry(insert_idx, text, entry, add_blank_before=(i == 0), list_level=0)
                        consumed.append(entry)
        return consumed
