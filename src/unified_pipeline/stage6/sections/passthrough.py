"""Sections E, G and J: the passthrough sections (#398).

    E. EMPLOYMENT STATUS
    G. INSTITUTIONAL/HOSPITAL AFFILIATION
    J. PERCENT EFFORT AND INSTITUTIONAL RESPONSIBILITIES

Every other section in the template is rebuilt from stage-4 fields. These
three are short, already structured in the source CV, and structured the same
way the WCM template wants them, so they are copied across instead -- which is
what "passthrough" means here and why the three share one module and one entry
point. `_fill_passthrough_sections` is dispatched once from `generate` and is
nothing but the three calls.

All three writers select their input by HIERARCHY rather than by taxonomy
code: none of the three sections has a code of its own on the live path, so
the only signal that an entry belongs here is the source heading it was found
under. J is the one exception: it ALSO accepts a bare `taxonomy_code == 'J'`
match regardless of hierarchy, because stage 3b's own per-code dedup can drop
every correctly-hierarchied copy of an over-segmented source table and leave
only a copy filed under an unrelated heading (a real S3 run over-segmented one
J table into two copies -- one under the expected PERCENT EFFORT hierarchy,
one mis-hierarchied under 'TRAINING' -- and dedup kept the TRAINING copies).
The existing parse/activity-map/percent guards make this safe: a J-coded
entry that isn't a parseable, recognized activity row still stays
Appendix-bound exactly as before.

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

J writes into a table too, and the table is fixed-row (Teaching, Clinical,
Administrative, Research, Total) -- it is never cleared and never grown, only
matched by row label and filled (#260). The source's own table survives into
stage 3b as ordinary T-coded rows sharing the same hierarchy, column-header
row included, so `_fill_percent_effort` parses every candidate row itself
rather than trusting its taxonomy code; see the function's own docstring for
the parse/match rules and the header-row and `Total | 100% |` traps a coarser
filter fell into.

None of the three has a taxonomy code of its own that `generate()`'s
RENDER_ROUTED_CODES dispatch can key on, so each writer here reports back
exactly which entry dicts it actually wrote (never merely matched -- a
refused E label or an unmapped J activity is not in that list). `generate()`
uses that to keep a written entry out of the Appendix a second time (#294,
#260) without keying anything off taxonomy code at all.
"""
import logging
import re
from typing import NamedTuple

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


# --- J. Percent Effort (#260) ------------------------------------------

# Synonyms for the template's five fixed row labels, keyed by the activity
# text after `_normalize_percent_effort_activity` -- lower-cased, punctuation
# stripped to a single space. An activity that maps to none of these is left
# unwritten and un-consumed (still Appendix-bound); it is never guessed at.
_PERCENT_EFFORT_ACTIVITY_SYNONYMS: dict[str, str] = {
    'teaching': 'Teaching',
    'clinical': 'Clinical',
    'clinical care': 'Clinical',
    'patient care': 'Clinical',
    'clinical practice': 'Clinical',
    'administrative': 'Administrative',
    'administration': 'Administrative',
    'admin': 'Administrative',
    'research': 'Research',
    'total': 'Total',
}

# A percent cell as stage 2 emits it: digits then '%', an optional space
# between ("10%" or "10 %"). Matched cell-by-cell against the row's non-
# activity cells; the FIRST such cell wins, so a stray '%' anywhere else in
# the row (e.g. a Yes/No cell can't have one, but a header cell can) doesn't
# get picked over a genuine percent cell.
_PERCENT_CELL_RE = re.compile(r'^(\d{1,3})\s*%$')
# A bare integer cell, used ONLY when no cell in the row contains a literal
# '%' at all -- so a malformed percent cell (e.g. "10 pct") never silently
# falls back to reading some unrelated numeric cell as the percentage.
_BARE_INT_CELL_RE = re.compile(r'^(\d{1,3})$')


class PercentEffortRow(NamedTuple):
    """One parsed J-table source row, before activity-name mapping.

    `activity` is the raw first-cell text, unmapped. `percent` is normalized
    to "NN%" text, or None if no cell parsed as a percentage. `involves_trainees`
    is the exact "Yes"/"No" text of whichever cell matched that, or None when
    no cell was exactly (case-insensitively) "yes" or "no".
    """
    activity: str
    percent: str | None
    involves_trainees: str | None


def _normalize_percent_effort_activity(raw: str) -> str:
    """Lower-case, punctuation-stripped, single-spaced -- for synonym lookup.
    "Clinical Care", "clinical-care" and "Clinical  Care:" all normalize the
    same way."""
    stripped = re.sub(r'[^\w\s]', ' ', raw)
    return re.sub(r'\s+', ' ', stripped).strip().lower()


def _map_percent_effort_activity(raw: str) -> str | None:
    """The template row label `raw` names, or None if it names none of them."""
    return _PERCENT_EFFORT_ACTIVITY_SYNONYMS.get(_normalize_percent_effort_activity(raw))


def _parse_percent_effort_row(text: str) -> PercentEffortRow | None:
    """Parse one pipe-joined J source row (stage 2's table-row shape, e.g.
    "Teaching | 10% | Yes") into a `PercentEffortRow`. Returns None only when
    there is no activity cell at all -- an empty string can't name a row and
    is never worth logging.
    """
    cells = [c.strip() for c in text.split('|')]
    activity = cells[0] if cells else ''
    if not activity:
        return None
    other_cells = cells[1:]

    has_percent_sign = any('%' in c for c in other_cells)
    percent = None
    for cell in other_cells:
        m = _PERCENT_CELL_RE.match(cell)
        if m and 0 <= int(m.group(1)) <= 100:
            percent = f"{int(m.group(1))}%"
            break
    if percent is None and not has_percent_sign:
        for cell in other_cells:
            m = _BARE_INT_CELL_RE.match(cell)
            if m and 0 <= int(m.group(1)) <= 100:
                percent = f"{int(m.group(1))}%"
                break

    involves_trainees = None
    for cell in other_cells:
        low = cell.lower()
        if low == 'yes':
            involves_trainees = 'Yes'
            break
        if low == 'no':
            involves_trainees = 'No'
            break

    return PercentEffortRow(activity=activity, percent=percent, involves_trainees=involves_trainees)


def _is_percent_effort_header_row(mapped_activity: str | None, text: str) -> bool:
    """Whether `text` is the source table's OWN column-header row, coded T
    (or, since candidates can now also be selected by `taxonomy_code == 'J'`,
    coded J) like ordinary content in real runs (e.g. "Current percent
    effort | Percent effort % | Does the activity involve WMC
    students/researchers? (Yes/No)"). Matched by a POSITIVE header signature
    -- an activity cell that maps to nothing, row text naming the columns,
    AND at least 2 pipes (both real header rows have exactly 2) -- not by
    "all cells are template labels", which is the filter #260's own
    investigation found eating "Total | 100% |" (Total maps to a known
    activity, so this never fires on it). The pipe-count check matters more
    now that J-coded prose can reach here too: without it, a J-coded prose
    paragraph that merely mentions "percent effort" would be consumed and
    silently vanish from the Appendix -- content loss, not a header.
    """
    if mapped_activity is not None:
        return False
    if text.count('|') < 2:
        return False
    upper = text.upper()
    return 'PERCENT EFFORT' in upper or 'YES/NO' in upper


def _percent_effort_table_is_valid(table) -> bool:
    """Guard against writing into a stranger's table, the same way G does:
    the table's own first row must mention the columns this section has, or
    failing that, some row's first cell must be a recognizable Teaching row.
    """
    if not table or not table.rows:
        return False
    header_text = ' '.join(c.text.strip().lower() for c in table.rows[0].cells)
    if any(kw in header_text for kw in ('activity', 'percent', 'effort')):
        return True
    return any(
        row.cells and _map_percent_effort_activity(row.cells[0].text) == 'Teaching'
        for row in table.rows
    )


def _percent_effort_row_index(table) -> dict[str, int]:
    """Map each known activity to the template row that names it. Skips row
    0 (the header) so a header cell that happened to normalize to a known
    activity could never be treated as that activity's row.
    """
    mapping: dict[str, int] = {}
    for i, row in enumerate(table.rows):
        if i == 0 or not row.cells:
            continue
        activity = _map_percent_effort_activity(row.cells[0].text)
        if activity is not None and activity not in mapping:
            mapping[activity] = i
    return mapping


class PassthroughSection:
    """Section E, G and J writers, mixed into `WCMTemplateGenerator`."""

    def _fill_passthrough_sections(self, all_entries: list[dict]) -> list[dict]:
        """Fill sections that can be copied directly from source CV when format matches.

        These are short, structured sections in the WCM template that may already exist
        in the source CV in the same format. If found, we copy them directly.

        Sections handled:
        - E. EMPLOYMENT STATUS
        - G. INSTITUTIONAL/HOSPITAL AFFILIATION
        - J. PERCENT EFFORT AND INSTITUTIONAL RESPONSIBILITIES (#260)

        Args:
            all_entries: All entries from the pipeline (to search by hierarchy)

        Returns:
            The union of entry dicts each writer actually wrote into the
            document -- not merely matched by hierarchy. `generate()` uses
            this to keep a consumed entry out of the Appendix a second time
            (#294, #260); an entry a writer matched but refused (e.g. an E
            label with no known template row, #571, or a J row with no
            recognized activity) is NOT in this list, so it still reaches
            the Appendix as before.
        """
        return (self._fill_employment_status(all_entries)
                + self._fill_hospital_affiliation(all_entries)
                + self._fill_percent_effort(all_entries))

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

    def _fill_percent_effort(self, all_entries: list[dict]) -> list[dict]:
        """Fill J. PERCENT EFFORT AND INSTITUTIONAL RESPONSIBILITIES (#260).

        Selects candidates by hierarchy, the same way E and G do -- the
        source's own effort table (its column-header row and its
        `Total | 100% |` row included) reaches stage 3b as ordinary T-coded
        entries sharing the J hierarchy, so taxonomy code alone is not a
        sufficient filter here (see #260's real-run traps in the module
        docstring). It ALSO accepts `taxonomy_code == 'J'` regardless of
        hierarchy: a real S3 run over-segmented one source table into a
        PERCENT-EFFORT-hierarchy copy and a mis-hierarchied 'TRAINING' copy,
        both taxonomy_code 'J', and generate()'s per-code dedup
        (stage_6_word_template.py) kept the TRAINING copies -- hierarchy-only
        selection left the table with only its header row and Total as
        candidates, so the data rows rendered nowhere and were duplicated
        into the Appendix under "From \"TRAINING\":". The parse/activity-map/
        percent guards below make a J-coded candidate just as safe as a
        hierarchy-matched one: anything that isn't a parseable, recognized
        activity row is left alone and stays Appendix-bound.

        Each candidate is parsed (`_parse_percent_effort_row`), its activity
        mapped to a template row (`_map_percent_effort_activity`), and only
        written when both a percent and a known row are found. An unmapped
        row that is the source table's own header (`_is_percent_effort_header_row`)
        is still consumed -- excluded from the Appendix -- even though
        nothing is written for it, since it is not CV content.

        Returns the entries actually written OR recognized as the header row
        (#260, #294) -- an entry with an unmapped activity that is not the
        header, or a mapped activity with no percent, is neither, and stays
        Appendix-bound.
        """
        candidates = [
            entry for entry in all_entries
            if 'PERCENT EFFORT' in ' '.join(entry.get('hierarchy', [])).upper()
            or entry.get('taxonomy_code') == 'J'
        ]
        if not candidates:
            return []

        header_idx = self._find_header_paragraph('PERCENT EFFORT')
        if header_idx is None:
            header_idx = self._find_paragraph_with_text('PERCENT EFFORT')
        if header_idx is None:
            if self.verbose:
                print("  Passthrough: Could not find Percent Effort section")
            return []

        table = self._find_table_after_paragraph(header_idx)
        if not _percent_effort_table_is_valid(table):
            if self.verbose:
                print("  Passthrough: Could not find Percent Effort table")
            return []

        row_index = _percent_effort_row_index(table)

        consumed = []
        for entry in candidates:
            text = entry.get('text', '').strip()
            parsed = _parse_percent_effort_row(text)
            if parsed is None:
                continue

            activity = _map_percent_effort_activity(parsed.activity)
            if activity is None:
                if _is_percent_effort_header_row(activity, text):
                    consumed.append(entry)
                continue
            if parsed.percent is None:
                continue

            row_idx = row_index.get(activity)
            if row_idx is None:
                logger.warning(
                    "Percent Effort entry activity %r maps to %r, which has "
                    "no row in the template table; entry not written",
                    parsed.activity, activity)
                continue

            self._write_percent_effort_row(table, row_idx, parsed)
            consumed.append(entry)
        return consumed

    def _write_percent_effort_row(self, table, row_idx: int, parsed: PercentEffortRow) -> None:
        """Write `parsed`'s percent (and Yes/No, if parsed) into the
        template's EXISTING row at `row_idx`. Never clears the table, never
        adds a row, never touches the header row (row 0 is never passed in
        here -- `_percent_effort_row_index` excludes it) -- only the two
        non-label cells of an already-fixed row.
        """
        row = table.rows[row_idx]
        if len(row.cells) > 1:
            row.cells[1].text = parsed.percent
            for para in row.cells[1].paragraphs:
                for run in para.runs:
                    _set_font(run)
        if parsed.involves_trainees is not None and len(row.cells) > 2:
            row.cells[2].text = parsed.involves_trainees
            for para in row.cells[2].paragraphs:
                for run in para.runs:
                    _set_font(run)
        self.stats['entries_inserted'] += 1
