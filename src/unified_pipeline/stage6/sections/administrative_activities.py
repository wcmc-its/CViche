"""Section P: institutional administrative activities (#398).

Three columns -- activity/committee, role, dates -- and, like section O next
door, the writer spends most of its time working out how many rows one entry is
actually worth. Committee service is written in source CVs as a table or as a
run of lines, and both survive extraction as a single entry.

Three different repairs, in the order the writer tries them:

1. A LIST under `committee_name` / `committee` / `activity`. Stage 4 packs a
   multi-committee entry as a list of record dicts (#208/#248 fusion). Each
   record becomes its own row. Before this, the list went into a Word cell whole
   and python-docx raised deep in the XML layer, aborting the entire document
   (#256) -- which is also why `_add_committee_row` runs every value through
   `_committee_cell_text` rather than trusting its caller.
2. No dates in the fields, but a parenthetical in the raw text. "(Chair
   2011-2013)" carries both the role and the range; "(2010-present)" carries
   only the range. Either way the parenthetical is stripped back out of the
   activity text so the committee name renders clean.
3. 3+ lines of raw text, or 2 lines with no extracted activity, means the fields
   describe one item out of many and `_multiline_committee_rows` re-parses
   the text per line.

That last helper runs section O's line parser,
`_parse_flattened_committee_lines` in `stage6.parsing` (#572 -- P used to run
a drifted copy that had no pipe branch, no trailing-date branch and no
orphaned-date pairing, so those shapes rendered with an empty Dates column).
P keeps its own row writer because its table has a Role column O's does not:
parsed parenthetical titles go there instead of back into the activity text.

Two boundary decisions from the #625 review, both local to this file:
`committee_name`/`committee`/`activity` are three extraction aliases for one
domain concept, resolved by `_CommitteeRecord.from_raw` in one explicit
precedence order everywhere the alias chain is read (committee_name wins,
then committee, then activity); and `_fill_administrative_activities` parses
every entry into plain rows before it clears or writes a single Word table
row, so an exception partway through parsing leaves the existing table
content untouched instead of half-rewritten.
"""
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import List, Tuple, TypedDict

from ..formatting import _clear_table_data, _set_font, format_date_range
from ..normalization import _committee_cell_text
from ..parsing import _parse_flattened_committee_lines
from ..sorting import sort_entries_reverse_chronological
from unified_pipeline.core.render_check import entry_lines


class _AdminActivityEntry(TypedDict, total=False):
    """One stage-4 entry as `_fill_administrative_activities` actually reads
    it (review thread 3850039661) -- documents the contract this renderer
    relies on, in place of the `List[Dict]` signature that gave a reader no
    idea what shape the dicts were. `total=False`: stage 4's JSON has no
    schema enforcing any of these keys exist, so this is not a validation
    layer -- see `normalization/fields.py`'s docstring for why this codebase
    coerces at read time rather than rejecting."""
    text: str
    extracted_fields: dict
    taxonomy_code: str


# Stage 4 names the same domain concept three different ways depending on how
# extraction structured a given entry: committee_name is the most specific
# key, committee is the medium-specific one, activity is the generic
# catch-all used when extraction found no committee structure at all. Every
# place below that resolves one of the three uses this SAME order, so a
# value under committee_name always wins over the same entry also being
# populated under committee or activity (review threads 3850009796,
# 3850014030).
_COMMITTEE_ALIAS_KEYS = ('committee_name', 'committee', 'activity')

# "(Chair 2011-2013)" -- role text plus a year range.
_ROLE_AND_YEAR_RANGE_RE = re.compile(
    r'\(([^)]*?)(\d{4})\s*[-–]\s*(\d{4}|present)\)', re.IGNORECASE)
# "(2010-present)" -- a year range with no role text.
_YEAR_RANGE_ONLY_RE = re.compile(
    r'\((\d{4})\s*[-–]\s*(\d{4}|present)\)', re.IGNORECASE)
# Any parenthetical that contains a 4-digit year -- used to strip the matched
# date (and, in the role branch, role) metadata back out of activity text.
_PARENTHETICAL_WITH_YEAR_RE = re.compile(r'\s*\([^)]*\d{4}[^)]*\)')


def _first_committee_alias(fields: Mapping, *, as_list: bool = False):
    """Resolve committee_name/committee/activity to one value, using the
    explicit precedence declared by `_COMMITTEE_ALIAS_KEYS`.

    `as_list=True` returns the first alias whose value is a `list` (detects a
    stage-4 multi-committee record burst, #208/#248 fusion) -- this path's
    precedence (committee_name, then committee, then activity) matches the
    order the list-detection code already used, so it is unchanged.

    The scalar (as_list=False) path -- the top-level `activity`/`role`/dates
    resolution `_CommitteeRecord.from_raw` calls -- is NOT a no-op rewrite of
    the `activity or committee or committee_name` chain it replaced. That
    chain was activity-first; this function is committee_name-first, the
    same order as the list-detection path, because the reviewer asked for
    ONE stated precedence used everywhere the alias chain is read rather
    than two different orders in the same file (review threads 3850009796,
    3850014030). The two orders agree unless an entry has more than one
    alias populated with a truthy value -- when it does, this now returns
    the `committee_name` value where the old chain would have returned
    `activity`. That is the intended behavior change this round, not a
    bug: keep it, don't revert it back to activity-first."""
    for key in _COMMITTEE_ALIAS_KEYS:
        value = fields.get(key)
        if as_list:
            if isinstance(value, list):
                return value
        elif value:
            return value
    return None


@dataclass
class _CommitteeRecord:
    """One committee/activity record the way this renderer actually reads
    it: the typed boundary review thread 3850039661 asked for, scoped to
    this renderer only (#567 tracks the stage6-wide version separately --
    this does not attempt to establish that convention).

    `from_raw` is also the one place the committee_name/committee/activity
    alias chain gets resolved into a record (review threads 3850009796,
    3850014030), and the one place a raw stage-4 value gets coerced to plain
    text for this renderer (review thread 3850017930): every field below is
    already a plain string by the time a caller reads it, which makes
    `_add_committee_row`'s own `_committee_cell_text` calls a final
    serialization safeguard rather than the only place a malformed shape
    gets handled."""
    activity: str = ''
    role: str = ''
    start_date: str = ''
    end_date: str = ''

    @classmethod
    def from_raw(cls, raw, *, name_fallback: bool = False) -> '_CommitteeRecord':
        """Build a record from one raw stage-4 value.

        `raw` is normally a dict (or dict-like Mapping); `name_fallback=True`
        also tries a `name` key once the three canonical aliases are
        exhausted, matching the extra fallback a multi-committee burst
        record (#208/#248 fusion) can carry that a top-level `fields` dict
        does not. Never raises: an unfamiliar shape degrades to a
        `_committee_cell_text`-coerced blob rather than aborting the row
        (#256)."""
        if not isinstance(raw, Mapping):
            return cls(activity=_committee_cell_text(raw))
        activity = _first_committee_alias(raw)
        if not activity and name_fallback:
            activity = raw.get('name')
        return cls(
            activity=_committee_cell_text(activity),
            role=_committee_cell_text(raw.get('role')),
            start_date=raw.get('start_date') or '',
            end_date=raw.get('end_date') or '',
        )


class AdministrativeActivitiesSection:
    """Section P writers, mixed into `WCMTemplateGenerator`."""

    def _fill_administrative_activities(self, entries: List[_AdminActivityEntry]):
        """Fill P. INSTITUTIONAL ADMINISTRATIVE ACTIVITIES section.

        WCM template has table with: Activity/Committee | Role | Dates
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Administrative Activities ({len(entries)} entries)...")

        # Find Administrative section
        section_idx = self._find_paragraph_with_text("INSTITUTIONAL ADMINISTRATIVE")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("ADMINISTRATIVE ACTIVITIES")
        if section_idx is None:
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            return

        # Parse every entry into plain rows FIRST, and only clear/write the
        # table once the full render model exists (review thread 3850029915):
        # an exception partway through parsing now leaves the table's
        # existing content untouched instead of cleared with only some of
        # the real rows written in its place.
        sorted_entries = sort_entries_reverse_chronological(entries)
        rows = self._parse_administrative_activity_rows(sorted_entries)

        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1
        for activity, role, dates in rows:
            self._add_committee_row(table, activity, role, dates)

    def _parse_administrative_activity_rows(
        self, sorted_entries: List[_AdminActivityEntry]
    ) -> List[Tuple[str, str, str]]:
        """Parse every entry into (activity, role, dates) rows.

        Pure with respect to the Word document -- nothing here touches a
        table. See `_fill_administrative_activities` for why that ordering
        matters (review thread 3850029915).
        """
        rows: List[Tuple[str, str, str]] = []

        for entry in sorted_entries:
            # Guard the type, not just falsiness: a truthy non-dict here (a
            # stray list, say) would AttributeError on every fields.get()
            # below.
            raw_fields = entry.get('extracted_fields')
            fields = raw_fields if isinstance(raw_fields, Mapping) else {}
            taxonomy_code = entry.get('taxonomy_code', 'P')
            original_text = entry.get('text', '')

            # Stage 4 packs a multi-committee entry as a LIST of record dicts
            # under committee_name/committee/activity (#208/#248 fusion).
            # Expand each into its own row rather than dumping a list into
            # one cell (#256 crash).
            record_list = _first_committee_alias(fields, as_list=True)
            if record_list:
                for rec in record_list:
                    record = _CommitteeRecord.from_raw(rec, name_fallback=True)
                    dates = format_date_range(
                        record.start_date, record.end_date, taxonomy_code) or ''
                    if record.activity:
                        rows.append((record.activity, record.role, dates))
                continue

            record = _CommitteeRecord.from_raw(fields)
            activity, role = record.activity, record.role
            dates = format_date_range(record.start_date, record.end_date, taxonomy_code) or ''

            # If dates not extracted, try to parse from parenthetical patterns in original text
            # Common patterns: "(Chair 2011-2013)", "(2010-present)", "(Member 1999-2012)"
            if not dates and original_text:
                # Pattern 1: (Role YYYY-YYYY) or (Role YYYY-present)
                paren_match = _ROLE_AND_YEAR_RANGE_RE.search(original_text)
                if paren_match:
                    potential_role = paren_match.group(1).strip()
                    start_year = paren_match.group(2)
                    end_year = paren_match.group(3)
                    # Route the parsed years through the same formatter every
                    # extracted date uses, instead of hand-building the
                    # display string here (review thread 3849996402) -- keeps
                    # "(...-present)" rendering "Present" with a capital P
                    # like every other end date on this taxonomy code. The
                    # `or` fallback is the permissive path required for any
                    # change that routes through a stricter formatter: if
                    # format_date_range ever returns '' for some unseen
                    # taxonomy code, the plain range still renders instead of
                    # the dates silently vanishing.
                    dates = format_date_range(start_year, end_year, taxonomy_code) \
                        or f"{start_year}-{end_year}"
                    # Extract role if present (e.g., "Chair", "Member")
                    if potential_role and not role:
                        role = potential_role.rstrip(',').strip()
                    # Strip the same parenthetical out of the activity text
                    # whether it was pre-populated by extraction or is about
                    # to fall back to the raw text below, so Role/Dates don't
                    # also stay duplicated inside the Activity column (review
                    # thread 3850003492). Scoped to a parenthetical that
                    # contains a year, so an unrelated "(Emeritus)" survives.
                    activity = _PARENTHETICAL_WITH_YEAR_RE.sub(
                        '', activity or original_text).strip()
                else:
                    # Pattern 2: Just (YYYY-YYYY) without role
                    paren_match = _YEAR_RANGE_ONLY_RE.search(original_text)
                    if paren_match:
                        start_year, end_year = paren_match.group(1), paren_match.group(2)
                        dates = format_date_range(start_year, end_year, taxonomy_code) \
                            or f"{start_year}-{end_year}"
                        activity = _PARENTHETICAL_WITH_YEAR_RE.sub(
                            '', activity or original_text).strip()

            # Check if this entry contains multiple items (newline-separated)
            lines = entry_lines(original_text)

            # Use multi-line parsing when the text contains 3+ lines — this catches
            # mega-blocks where field extraction only captured one item from many.
            if len(lines) >= 3:
                # Multiple items merged - split them into separate rows
                rows.extend(self._multiline_committee_rows(lines))
            elif len(lines) > 1 and not activity:
                # Two lines, no extracted activity - still try multi-line parsing
                rows.extend(self._multiline_committee_rows(lines))
            else:
                if not activity:
                    activity = original_text[:150]
                rows.append((activity, role, dates))

        return rows

    def _add_committee_row(self, table, activity: str, role: str, dates: str):
        """Add a single committee row with proper column handling."""
        # Final serialization safeguard, not the primary validation
        # mechanism (review thread 3850017930): `_CommitteeRecord.from_raw`
        # already coerces every domain value to plain text where it is read
        # out of `fields`, so by the time a caller reaches this method these
        # three are normally already strings. This stays anyway -- never
        # write a non-str (dict/list) into a Word cell, it raises deep in
        # python-docx and aborts the whole document (#256).
        activity = _committee_cell_text(activity)
        role = _committee_cell_text(role)
        dates = _committee_cell_text(dates)
        row = table.add_row()
        num_cols = len(row.cells)

        if num_cols >= 3:
            row.cells[0].text = activity or ''
            row.cells[1].text = role or ''
            row.cells[2].text = dates or ''
        elif num_cols >= 2:
            row.cells[0].text = f"{activity} ({role})" if role else activity
            row.cells[1].text = dates or ''
        else:
            row.cells[0].text = f"{activity} - {dates}" if dates else activity

        for cell in row.cells:
            for para in cell.paragraphs:
                for run in para.runs:
                    _set_font(run)
        self.stats['entries_inserted'] += 1

    def _multiline_committee_rows(self, lines: List[str]) -> List[Tuple[str, str, str]]:
        """Parse multiline content into (activity, role, dates) rows.

        Pure with respect to the Word document -- see
        `_parse_administrative_activity_rows`, its one caller, for why
        (review thread 3850029915).

        The line parser is `_parse_flattened_committee_lines`, shared with
        section O (#572), so pipe-separated date columns, trailing dates and
        orphaned date lines all resolve into the Dates cell instead of being
        left inside (or dropped from) the Activity cell. Parsed role titles go
        into P's Role column.
        """
        rows: List[Tuple[str, str, str]] = []
        for item in _parse_flattened_committee_lines(lines):
            role = '; '.join(item.roles)
            # Skip if nothing renderable remains after date extraction
            if not item.activity and not role:
                continue
            rows.append((item.activity, role, item.dates))
        return rows
