"""Section I: professional organizations and society memberships (#398).

Two columns -- organization, dates -- and one recurring shape problem. Source
CVs write memberships as a two-column table, and extraction flattens that into a
single entry whose lines interleave the columns:

    "Member\nElected Member | Org1\nOrg2 | date1\ndate2"

More than two lines is the signal to hand the entry to
`_parse_multi_membership_entry` and emit a row per membership rather than trust
the `extracted_fields`, which describe only the first.

Either way the organization cell is built the same: the membership type is
prefixed only when it is not already inside the organization name, so "Fellow,
American College of Surgeons" does not become "Fellow, Fellow of the American
College of Surgeons". An entry with no `organization` field falls back to its
first 150 characters of raw text -- a truncated membership is still a
membership; a missing one is a loss.

Dates go through `_membership_dates_cell` on both paths. The multi-membership
parser hands back one raw range string ("2015-present") while the single path
has separate `start_date`/`end_date` fields; `_split_date_range` reduces the
first shape to the second so both end at the same `format_date_range(..., 'I')`
call and equivalent memberships cannot render two different date formats.

Finding the table takes two tries. The heading text has changed across template
revisions ("PROFESSIONAL ORGANIZATIONS", "SOCIETY MEMBERSHIPS", "MEMBERSHIPS"),
and if none of them match, the fallback searches for a table whose first cell
says "Organization" -- then checks that its SECOND column header says "Date"
before writing into it, because "Organization" alone also matches tables
belonging to other sections.

Extraction keeps the source table's own header row, so header-shaped entries are
dropped explicitly rather than rendered as a membership called "Organization".

`_add_table_row` is the generic row writer, and it lives here because this is
the only section that calls it. Everything else either writes cells directly or
uses one of the shared `_add_table_row_with_*` variants, which stay on
`WCMTemplateGenerator`. It owns `stats['entries_inserted']` outright -- the
callers below must not also increment it -- and it refuses a `data` list longer
than the table has columns instead of dropping the surplus cells.
"""
import re
from typing import List

try:
    from docx.table import Table
except ImportError as exc:
    raise ImportError(
        "python-docx is required for stage 6. Install with: pip install python-docx lxml"
    ) from exc

from ..formatting import (
    _clear_table_data,
    _set_cell_vertical_alignment,
    _set_font,
    format_date_range,
)
from ..parsing import _is_table_header_entry, _parse_multi_membership_entry
from ..sorting import sort_entries_reverse_chronological
from unified_pipeline.core.render_check import entry_lines

# Section I's taxonomy code -- the key into formatting/dates.py's DATE_FORMATS,
# which maps it to 'yyyy'. Named because it appears in the one date-formatting
# helper below and nowhere else; a bare 'I' at a call site reads like an index.
_MEMBERSHIPS_TAXONOMY_CODE = 'I'

# One raw range string -> (start, end). Hyphen, en dash and em dash only: a
# slash is part of a date ("1/1997"), not a separator between two.
_DATE_RANGE_SPLIT_RE = re.compile(r'\s*[-–—]\s*')


class MembershipsRowShapeError(ValueError):
    """`_add_table_row` was handed more values than the table has columns.

    A caller/template programming error, never CV content: both call sites in
    `_fill_memberships` pass a two-element list, and the memberships table the
    section writes into has two columns on every path that reaches the writer
    (the WCM template's own table, or a fallback table the header guard already
    rejected unless it had at least two columns). Content decides what goes IN
    the two cells, never how many cells there are -- so this cannot abort a
    document over a bad CV, and failing loudly beats emitting a document that
    silently lost a column (#476 review).
    """


def _entry_parts(text: str) -> List[str]:
    """Parts of a memberships entry (#476), scoped to exactly the
    newline-blind case: a text with no literal newline at all keeps
    `entry_lines`'s own single opaque part today, even when it is really a
    "Type1 | Org1 | Date1 | Type2 | Org2 | Date2" fused entry that
    `_parse_multi_membership_entry`'s own '|' handling (parsing/text.py:209)
    could otherwise see as more than one membership.

    A genuinely multi-line entry (entry_lines already returns >1 part) is
    returned UNCHANGED. Reading the farm's own newline-blind uids proved
    this matters: 2071_Zuschlag_Cv's "2009-2012\\t\\tStudent Osteopathic
    Surgical Association\\t\\t\\n\\n2009-2012\\t\\tFlorida Osteopathic Medical
    Association" already has 2 lines by newline alone, each carrying its own
    unsplit tabs; running the multi-membership parser on that produces two
    "organizations" that are really unparsed tab-laden blobs (date prefix and
    literal tab baked into the org cell, dates column empty) -- worse than
    today's single clean row. This fix is narrower than that entry's real
    bug (a genuine 2-record fusion the #221 recovery pass papers over with an
    Appendix bullet); it isn't newline-blind and isn't this issue's to fix.
    """
    lines = entry_lines(text)
    if len(lines) != 1:
        return lines
    return [p.strip() for p in lines[0].split('|') if p.strip()]


def _split_date_range(dates: str) -> tuple[str, str]:
    """One raw membership date cell -> the (start, end) pair the formatter wants.

    `_parse_multi_membership_entry` returns a range already joined into one
    string; the single-membership path has the two halves as separate fields.
    Reducing the first shape to the second is what lets both reach the same
    `format_date_range` call.
    """
    if not dates:
        return '', ''
    halves = _DATE_RANGE_SPLIT_RE.split(dates.strip(), maxsplit=1)
    return halves[0].strip(), (halves[1].strip() if len(halves) > 1 else '')


def _membership_dates_cell(start_date: str, end_date: str) -> str:
    """The ONE place a section I date cell is formatted (#476 review).

    Both the single- and multi-membership paths end here, so equivalent
    memberships cannot render different date formats depending on which branch
    the entry took.
    """
    if not (start_date or end_date):
        return ''
    return format_date_range(start_date, end_date, _MEMBERSHIPS_TAXONOMY_CODE)


class MembershipsSection:
    """Section I writers, mixed into `WCMTemplateGenerator`."""

    def _fill_memberships(self, entries: list[dict]):
        """Fill I. PROFESSIONAL ORGANIZATIONS AND SOCIETY MEMBERSHIPS section.

        Entries have fields: organization, membership_type, start_date, end_date
        Uses table with columns: Organization, Date (yyyy-yyyy)
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Memberships ({len(entries)} entries)...")

        # Find the MEMBERSHIPS section
        memberships_idx = self._find_paragraph_with_text("PROFESSIONAL ORGANIZATIONS")
        if memberships_idx is None:
            memberships_idx = self._find_paragraph_with_text("SOCIETY MEMBERSHIPS")
        if memberships_idx is None:
            memberships_idx = self._find_paragraph_with_text("MEMBERSHIPS")
        if memberships_idx is None:
            return

        # Find the table after the section header
        table = self._find_table_after_paragraph(memberships_idx)
        if not table:
            # Fall back to finding table with "Organization" header
            table = self._find_table_with_cell_text("Organization")
            if table and (
                not table.rows
                or len(table.rows[0].cells) < 2
                or "Date" not in table.rows[0].cells[1].text
            ):
                table = None  # Wrong table

        if not table:
            if self.verbose:
                print("  Warning: Could not find memberships table")
            return

        # Clear existing data rows
        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        # Sort by date (most recent first)
        sorted_entries = sort_entries_reverse_chronological(entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {}) or {}
            original_text = entry.get('text', '')

            # Skip table header entries
            if _is_table_header_entry(original_text, ['organization', 'membership', 'society', 'date', 'member']):
                if self.verbose:
                    print(f"  Skipping header entry: '{original_text[:50]}...'")
                continue

            # Check if this entry contains multiple memberships (newline- or,
            # for a fully blind entry, '|'-separated -- see _entry_parts,
            # #476). Pattern: "Member\nElected Member | Org1\nOrg2 |
            # date1\ndate2", or fully blind: "Type1 | Org1 | Date1 | Type2 |
            # Org2 | Date2".
            lines = entry_lines(original_text)
            parts = _entry_parts(original_text)

            # Detect multi-membership pattern: multiple organization names or
            # membership types. `_parse_multi_membership_entry` already does
            # its own '|' splitting and keyword/date classification per part.
            #
            # Two gates, and which one applies depends on whether this entry
            # is one #476 newly splits:
            #
            #  - already multi-line (`len(lines) > 2`): unchanged from before
            #    #476 -- any parse result at all renders, including a
            #    single-membership one. `parts` IS `lines` here, so this is
            #    byte-for-byte the old `if len(lines) > 2: ... if memberships:`
            #    behaviour, deliberately preserved rather than folded into
            #    the stricter gate below.
            #  - newly split by '|' out of one blind line: gate on the PARSED
            #    COUNT, not just the raw part count. A single membership whose
            #    fields happen to split into exactly three parts ("Fellow |
            #    American Academy of Pediatrics | 1/1997-present", farm
            #    entries on 1FRABQ and others) crosses the >2 threshold but
            #    must still resolve to one membership, not be misread as
            #    several.
            memberships = _parse_multi_membership_entry(parts) if len(parts) > 2 else []
            if len(memberships) > 1 or (memberships and len(lines) > 2):
                for mem_type, org, dates in memberships:
                    org_text = f"{mem_type}, {org}" if mem_type and mem_type.lower() not in org.lower() else org
                    # `_add_table_row` owns stats['entries_inserted'] -- do not
                    # increment it here as well (#476 review).
                    self._add_table_row(
                        table,
                        [org_text, _membership_dates_cell(*_split_date_range(dates))],
                        entry=entry,
                    )
                continue

            # Single membership - use extracted fields
            organization = fields.get('organization', '')
            membership_type = fields.get('membership_type', '')
            start_date = fields.get('start_date', '')
            end_date = fields.get('end_date', '')

            if not organization:
                organization = original_text[:150]

            # Format: Membership Type, Organization
            if membership_type and membership_type.lower() not in organization.lower():
                org_text = f"{membership_type}, {organization}"
            else:
                org_text = organization

            # Format date range for table column
            date_str = _membership_dates_cell(start_date, end_date)

            # Add row to table
            self._add_table_row(table, [org_text, date_str], entry=entry)

    def _add_table_row(self, table: Table, data: list[str], is_header: bool = False,
                       entry: dict | None = None):
        """Add a row to a table with proper formatting.

        Owns `stats['entries_inserted']`: incremented here, once per row
        actually written, and never by the callers as well. Both layers used to
        increment it, so one rendered membership reported two inserted entries
        (#476 review).

        Args:
            table: The table to add to
            data: List of cell values
            is_header: Whether this is a header row
            entry: Optional entry dict - if provided, adds comments from upstream pipeline

        Raises:
            MembershipsRowShapeError: `data` is longer than the table is wide.
        """
        if not table:
            return

        # Validate before adding the row so a rejected call leaves no stray
        # empty row behind. `add_row()` creates exactly one cell per grid
        # column, so this count is the added row's cell count.
        columns = len(table.columns)
        if len(data) > columns:
            raise MembershipsRowShapeError(
                f"memberships row writer got {len(data)} value(s) for a table with "
                f"{columns} column(s); the surplus value(s) would be dropped silently"
            )

        row = table.add_row()
        first_cell_para = None
        for i, value in enumerate(data):
            cell = row.cells[i]
            cell.text = str(value) if value else ""
            # Set vertical alignment to center (middle)
            _set_cell_vertical_alignment(cell, 'center')
            for para in cell.paragraphs:
                if i == 0 and first_cell_para is None:
                    first_cell_para = para
                for run in para.runs:
                    # Always 11pt Arial, bold for headers
                    _set_font(run, size=11, bold=is_header)

        # Add comments to the first cell if entry provided
        if entry and first_cell_para:
            self._add_entry_comments(first_cell_para, entry)

        self.stats['entries_inserted'] += 1
