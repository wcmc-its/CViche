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
3. Raw text that holds more than one dated record (`_looks_like_multiple_records`,
   #660 -- counted per dated line, not per line), or 2+ lines with no extracted
   activity, means the fields describe one item out of many and
   `_multiline_committee_rows` re-parses the text per line. A complete
   activity+dates record over text with at most one date is trusted at any length.

That last helper runs section O's line parser,
`_parse_flattened_committee_lines` in `stage6.parsing` (#572 -- P used to run
a drifted copy that had no pipe branch, no trailing-date branch and no
orphaned-date pairing, so those shapes rendered with an empty Dates column).
P keeps its own row writer because its table has a Role column O's does not:
parsed parenthetical titles go there instead of back into the activity text.
It also leaves the parser's `institution_column` off (#664): a P pipe row reads
"Activity | Role | Dates", so its middle cell is not an institution -- P's own
institution comes from the extracted field (`_name_with_institution`, #985).

Two boundary decisions from the #625 review, both local to this file:
`committee_name`/`committee`/`activity` are three extraction aliases for one
domain concept, resolved by `_CommitteeRecord.from_raw` in one explicit
precedence order everywhere the alias chain is read (committee_name wins,
then committee, then activity); and `_fill_administrative_activities` parses
every entry into plain rows before it clears or writes a single Word table
row, so an exception partway through parsing leaves the existing table
content untouched instead of half-rewritten.

Every row written is recorded in the row-receipt ledger against the entry it
renders (#534), so the #1205 sub-point pass anchors a committee's prose only
under that entry's own row.

A record fanned out of stage 4's records list (`is_split_record`) is one row
from its own fields and is never re-parsed (#2040): the last of the records
keeps the parent's whole text, and re-parsing it printed every sibling a
second time, names and dates paired by position. A record of nothing but
dates writes no row, nor does a source table's own header row. No source word
leaves the document for it: the lines of the parent's text that no row
prints, and a skipped header row, are written under the family's last row
(`_unrendered_source_lines`), as a tracked-deleted sub-point when sub-points
are on.
"""
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

from docx.oxml.xmlchemy import BaseOxmlElement
from docx.table import Table

from unified_pipeline.core.render_check import entry_lines

from ..dedup import _is_sibling_of
from ..fan_out import (
    LAST_STAGE4_RECORD,
    OWN_TEXT_KEY,
    fallback_text,
    is_split_record,
    record_text,
)
from ..formatting import (
    _set_font,
    format_date_range,
    with_extra_date_spans,
)
from ..normalization import (
    _committee_cell_text,
    _line_without_shown_role_and_dates,
    names_non_seat_activity,
    own_activity_role,
)
from ..parsing import (
    _is_table_header_entry,
    _looks_like_multiple_records,
    _parse_flattened_committee_lines,
)
from ..sorting import sort_entries_reverse_chronological
from ..supplementary import (
    ADMINISTRATIVE_ACTIVITY_FIELDS,
    Revision,
    SubpointPlacement,
    write_row_subpoint,
)

logger = logging.getLogger(__name__)


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
# 3850014030). The order is `supplementary.ADMINISTRATIVE_ACTIVITY_FIELDS`:
# the #1205 sub-point pass counts the same fields as the row's.
_COMMITTEE_ALIAS_KEYS = ADMINISTRATIVE_ACTIVITY_FIELDS

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
    institution: str = ''

    @classmethod
    def from_raw(cls, raw, *, name_fallback: bool = False) -> _CommitteeRecord:
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
            institution=_committee_cell_text(raw.get('institution')),
        )


# Stage 4 sometimes hedges an institution it could not read off the entry:
# "Duke University (implied)", "Not provided". Those words are the model's
# own, not the CV's, so they must never reach the rendered cell (#985).
_HEDGED_INSTITUTION_RE = re.compile(
    r'\b(?:implied|inferred|not\s+provided)\b', re.IGNORECASE)
_WORD_RE = re.compile(r'\w+')
_PARENTHETICAL_RE = re.compile(r'\([^)]*\)')
# Function words that do not make an institution a different thing ("Department
# of Genetics" is already in "Genetics Department Faculty Meeting").
_INSTITUTION_STOPWORDS = frozenset({'of', 'the', 'for', 'and', 'at', 'in'})


def _content_words(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text.casefold())
            if w not in _INSTITUTION_STOPWORDS]


def _name_with_institution(activity: str, institution: str) -> str:
    """Fold the extracted institution into the committee-name cell (#985).

    The P table has no Institution column, so an extracted `institution` had
    nowhere to render and was dropped. Append it as "Committee, Institution",
    except (name returned unchanged) when there is no name to attach it to,
    the institution is a stage-4 hedge rather than CV text, the name already
    carries every content word of the institution (exact containment and
    near-duplicates), or the name -- ignoring a parenthetical acronym such as
    "(CENO)" -- is itself contained in the institution.
    """
    if not activity or _HEDGED_INSTITUTION_RE.search(institution):
        return activity
    name_words = _content_words(activity)
    inst_words = _content_words(institution)
    # An empty institution has no words, so it falls out here too.
    if set(inst_words) <= set(name_words):
        return activity
    core = ' '.join(_content_words(_PARENTHETICAL_RE.sub(' ', activity)))
    if f" {core} " in f" {' '.join(inst_words)} ":
        return activity
    return f"{activity.strip()}, {institution}"


# The column-label words of a source CV's own committee table header row
# ("Administrative duties (including committees) | Dates"), scored by
# `_is_table_header_entry` on top of its shared filler words.
_HEADER_KEYWORDS = ['administrative', 'activity', 'activities', 'committee',
                    'duties', 'responsibilities', 'including', 'role', 'date']


def _is_source_header_row(fields: Mapping, text: str) -> bool:
    """Is this entry the source table's column-header row, not a committee?

    Only on positive evidence from both sides: stage 4 found no committee,
    role or date in it, and every cell of its text is column-label vocabulary
    (`_is_table_header_entry`). A header row has nothing for stage 4 to
    extract, so an entry with any extracted value is a committee whatever its
    words (#2040: it rendered as a committee named after the header)."""
    if any(fields.get(key) for key in (*_COMMITTEE_ALIAS_KEYS, 'role', 'start_date', 'end_date')):
        return False
    return _is_table_header_entry(text, _HEADER_KEYWORDS)


# The fields a P row's Dates cell writes.
_DATE_FIELDS = ('start_date', 'end_date')


def _names_nothing(fields: Mapping) -> bool:
    """Whether `fields` hold no value but dates: no committee, role or any
    other value a row could name. Stage 4 returns such records when a stacked
    table cell lists more dates than names (#2040, #2041)."""
    return not any(_committee_cell_text(value) for key, value in fields.items()
                   if key not in _DATE_FIELDS)


def _own_record_text(entry: _AdminActivityEntry) -> str:
    """The line of the one record a fanned-out `entry` stands for: its own
    text (`fallback_text`), except for the last of stage 4's records when it
    has no line of its own (`OWN_TEXT_KEY` None): then the line built from
    its fields (`record_text`), never the parent's whole text, which is every
    sibling's line too."""
    marker = entry.get(LAST_STAGE4_RECORD)
    if isinstance(marker, Mapping) and marker.get(OWN_TEXT_KEY) is None:
        return record_text(entry)
    return fallback_text(entry)


def _row_role(entry: _AdminActivityEntry, role: str) -> str:
    """The Role cell of a P row: stage 4's `role`, except on a row that is
    not a seat on a committee (`names_non_seat_activity`, #1580), which shows
    the role its line or its nearest heading writes, else nothing (ZASOTV
    645: "Dissertation Committee Member" for an oral-exam participant)."""
    text = str(entry.get('text') or '')
    headings = entry.get('hierarchy') or []
    if isinstance(headings, str):
        headings = [headings]
    if not names_non_seat_activity(text, [str(heading) for heading in headings]):
        return role
    return own_activity_role(role, text, str(headings[-1]) if headings else '')


def _record_dates(record: _CommitteeRecord, fields: Mapping, code: str, text: str) -> str:
    """The Dates cell of a row written from `fields`: its range, then any
    further span (`with_extra_date_spans`)."""
    return with_extra_date_spans(
        format_date_range(record.start_date, record.end_date, code, text) or '', fields, code)


def _routes_to_line_parser(text: str, activity: str, dates: str) -> bool:
    """Whether `text` goes through the shared #572 line parser
    (`_multiline_committee_rows` -> `_parse_flattened_committee_lines`), given
    the `activity` and `dates` extraction produced for it. It does when:

    - the text holds 3+ lines and no complete structured record covers it (a
      merged multi-record block -- field extraction only captured one item
      out of many), or
    - it is 2 lines with no activity extracted, or
    - #627: it still carries an unresolved pipe-separated date column (e.g.
      "Committee (Chair 2002-present) | 1996-Present") -- the parenthetical
      fallback has no pipe branch, so it strips only the paren and leaves
      "| 1996-Present" stuck in the activity while taking the (wrong) paren
      date. (A record fanned out of a multi-record entry is never re-parsed:
      its line, when built from its fields, has no such column.)

    #660: a complete activity+dates record is trusted however the text wraps
    across display lines (a description under an otherwise clean single
    committee entry, say), and a fully-resolved record is never rerouted
    just because its text holds a `|`. Whether the text holds ONE record is
    read off the text itself, per dated line (`_looks_like_multiple_records`),
    not off its length. PR #714 capped trust at 3 lines because a genuine
    multi-committee mega-block, where extraction captured only the first of
    25+ merged committees, also yields a non-empty activity+dates pair, and
    trusting it dropped the other 24. A line count both over-trusted (3 dated
    lines) and under-trusted (a 5-line wrapped description with one date); a
    block with two or more dated lines is a burst at any length and still
    reparses.
    """
    lines = entry_lines(text)
    structured_complete = bool(activity) and bool(dates) and not _looks_like_multiple_records(lines)
    return ((not structured_complete and len(lines) >= 3)
            or (len(lines) > 1 and not activity)
            or (not structured_complete and '|' in text))


_TOKEN_RE = re.compile(r'[^\W_]+')


def _tokens(text: str) -> set[str]:
    """`text`'s letter and digit runs, case-folded."""
    return set(_TOKEN_RE.findall(text.casefold()))


def _is_family(entry: Mapping, other: Mapping) -> bool:
    """Whether `other` is `entry` or, for the last of stage 4's records, one
    of its earlier records (`_is_sibling_of`)."""
    return other is entry or (LAST_STAGE4_RECORD in entry and _is_sibling_of(other, entry))


def _parent_text_was_reparsed(entry: Mapping, fields: Mapping, text: str) -> bool:
    """Whether the writer, before #2040, re-parsed the last of stage 4's
    records' whole `text` line by line (`_routes_to_line_parser`): only then
    did the section print the lines its records do not hold."""
    record = _CommitteeRecord.from_raw(fields)
    dates = _record_dates(record, fields, str(entry.get('taxonomy_code', 'P')), text)
    return _routes_to_line_parser(text, record.activity, dates)


def _unrendered_source_lines(
        entries: list[_AdminActivityEntry],
        rows: list[tuple[_AdminActivityEntry, tuple[str, str, str]]],
) -> list[tuple[_AdminActivityEntry, tuple[str, ...]]]:
    """The source lines no P row prints, per entry they are offered under
    (#2040). Two kinds, each the CV's own text that its writer no longer
    shows:

    - a source table's column-header row (`_is_source_header_row`), whole;
    - for the last of stage 4's records whose parent text the writer used to
      re-parse (`_parent_text_was_reparsed`), the lines of that text that
      hold a word or a number no row of the section prints. Its records now
      render a row each and the text is never re-parsed, so a line the
      records left out (a month, a second year, a line stage 1 moved in from
      a neighbouring row) is kept here instead. Any other text is left to
      the sub-point pass's own rules, as before.
    """
    printed = set().union(*(_tokens(' '.join(cells)) for _entry, cells in rows))
    unrendered = []
    for entry in entries:
        raw_fields = entry.get('extracted_fields')
        fields = raw_fields if isinstance(raw_fields, Mapping) else {}
        text = str(entry.get('text') or '')
        if not is_split_record(entry) and _is_source_header_row(fields, text):
            lines = [text.strip()]
        elif LAST_STAGE4_RECORD in entry and _parent_text_was_reparsed(entry, fields, text):
            lines = [line for line in entry_lines(text) if _tokens(line) - printed]
        else:
            continue
        if lines:
            unrendered.append((entry, tuple(lines)))
    return unrendered


class AdministrativeActivitiesSection:
    """Section P writers, mixed into `WCMTemplateGenerator`."""

    def _fill_administrative_activities(self, entries: list[_AdminActivityEntry]) -> None:
        """Fill P. INSTITUTIONAL ADMINISTRATIVE ACTIVITIES section.

        WCM template has table with: Activity/Committee | Role | Dates
        """
        if not entries:
            return

        if self.verbose:
            logger.info("Filling Administrative Activities (%s entries)...", len(entries))

        # Find Administrative section
        section_idx = self._find_header_paragraph("INSTITUTIONAL ADMINISTRATIVE")
        if section_idx is None:
            section_idx = self._find_header_paragraph("ADMINISTRATIVE ACTIVITIES")
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
        unrendered = _unrendered_source_lines(sorted_entries, rows)

        self._clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1
        written = []
        for entry, (activity, role, dates) in rows:
            self._row_receipts.begin_entry(entry)  # the row below is its receipt (#534, #1205)
            written.append((entry, self._add_committee_row(table, activity, role, dates)))
        self._row_receipts.end_entry()  # a later row is not the last entry's (#534)
        for entry, lines in unrendered:
            # Under the last row its family wrote; under the table's header
            # row when it wrote none (a source header row).
            anchor = next((tr for other, tr in reversed(written) if _is_family(entry, other)),
                          table.rows[0]._tr)
            self._add_unrendered_lines(table, anchor, entry, lines)

    def _add_unrendered_lines(self, table: Table, anchor: BaseOxmlElement,
                              entry: _AdminActivityEntry, lines: tuple[str, ...]) -> None:
        """Source lines no P row prints (`_unrendered_source_lines`), under
        `anchor`. With sub-points on (`supplementary_subpoints` and track
        changes) they are the entry's tracked-deleted sub-point, recorded as
        the positions writer records its duties (`_add_title_duties`), so the
        sub-point pass, the overflow routing and the #221 recovery read them
        as already offered. Otherwise they are plain rows, a line each."""
        if not (self.supplementary_subpoints and self.emit_track_changes):
            for line in reversed(lines):
                anchor.addnext(self._add_committee_row(table, line, '', ''))
            return
        revision = Revision(self._revision_id, datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'))
        texts = tuple(self._sanitize_run_text(line) for line in lines)
        self._subpoint_lines.append(write_row_subpoint(anchor, texts, revision))
        self._subpoint_entry_ids.add(id(entry))
        self._subpoint_placements.append(SubpointPlacement(
            entry.get('element_idx_start'), entry.get('taxonomy_code'), texts))
        self._revision_id = revision.next_id
        self.stats['track_changes_added'] += 1

    def _parse_administrative_activity_rows(
        self, sorted_entries: list[_AdminActivityEntry]
    ) -> list[tuple[_AdminActivityEntry, tuple[str, str, str]]]:
        """Parse every entry into (activity, role, dates) rows, each paired
        with the entry it renders: one entry can give several rows (a list
        of records, a re-parsed block), and each is that entry's receipt.

        Pure with respect to the Word document -- nothing here touches a
        table. See `_fill_administrative_activities` for why that ordering
        matters (review thread 3850029915).
        """
        rows: list[tuple[_AdminActivityEntry, tuple[str, str, str]]] = []

        for entry in sorted_entries:
            # Guard the type, not just falsiness: a truthy non-dict here (a
            # stray list, say) would AttributeError on every fields.get()
            # below.
            raw_fields = entry.get('extracted_fields')
            fields = raw_fields if isinstance(raw_fields, Mapping) else {}
            taxonomy_code = entry.get('taxonomy_code', 'P')
            # A record fanned out of a multi-record entry is ONE record, read
            # from its own line (`_own_record_text`) and never re-parsed: the
            # last of stage 4's records keeps the parent's whole text, every
            # sibling's line, and reading that rendered each sibling a second
            # time, its names and dates paired by position (#2040). A record
            # of nothing but dates has no committee to list them under, and a
            # source header row is no committee; the lines either leaves
            # unprinted are kept by `_unrendered_source_lines`.
            split = is_split_record(entry)
            original_text = _own_record_text(entry) if split else entry.get('text', '')
            if (split and _names_nothing(fields)) or _is_source_header_row(fields, original_text):
                continue

            # Stage 4 packs a multi-committee entry as a LIST of record dicts
            # under committee_name/committee/activity (#208/#248 fusion).
            # Expand each into its own row rather than dumping a list into
            # one cell (#256 crash).
            record_list = _first_committee_alias(fields, as_list=True)
            if record_list:
                for rec in record_list:
                    record = _CommitteeRecord.from_raw(rec, name_fallback=True)
                    dates = format_date_range(
                        record.start_date, record.end_date, taxonomy_code,
                        original_text) or ''
                    if record.activity:
                        rows.append((entry, (
                            _name_with_institution(
                                record.activity, record.institution),
                            _row_role(entry, record.role), dates)))
                continue

            record = _CommitteeRecord.from_raw(fields)
            activity, role = record.activity, _row_role(entry, record.role)
            # What extraction actually produced, kept apart from `activity`
            # itself: the parenthetical fallback below overwrites `activity`
            # with the stripped raw text (including any second line) when
            # extraction gave none, which hid line 2 from the reroute check
            # further down (review thread 3915848371 item 2). Routing must
            # look at what extraction produced, not at the fallback's
            # rewrite.
            extracted_activity = activity
            dates = _record_dates(record, fields, taxonomy_code, original_text)

            # Routing is decided off what extraction produced, BEFORE any
            # raw-text fallback below touches `activity`/`dates` (#660).
            lines = entry_lines(original_text)
            reparse = _routes_to_line_parser(original_text, extracted_activity, dates)

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

            if reparse and not split:
                parsed_rows = self._multiline_committee_rows(lines)
            else:
                parsed_rows = []

            if parsed_rows:
                rows.extend((entry, row) for row in parsed_rows)
            else:
                if not activity:
                    # The Role and Dates cells already show those parts of
                    # the line; the whole line printed them twice (#1346,
                    # KJJVVO 240 "<society> Advisor — 2018–Present").
                    activity = _line_without_shown_role_and_dates(
                        original_text, role, record.start_date, record.end_date)
                rows.append((entry, (
                    _name_with_institution(activity, record.institution),
                    role, dates)))

        return rows

    def _add_committee_row(self, table: Table, activity: str, role: str,
                           dates: str) -> BaseOxmlElement:
        """Add a single committee row with proper column handling, recorded
        against the entry `_fill_administrative_activities` is on
        (`begin_entry`): the row-receipt ledger, by which the #1205 sub-point
        pass anchors a committee's prose only under its own row."""
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
        self._row_receipts.wrote(row._tr)
        return row._tr

    def _multiline_committee_rows(self, lines: list[str]) -> list[tuple[str, str, str]]:
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
        rows: list[tuple[str, str, str]] = []
        for item in _parse_flattened_committee_lines(lines):
            role = '; '.join(item.roles)
            # Skip if nothing renderable remains after date extraction
            if not item.activity and not role:
                continue
            rows.append((item.activity, role, item.dates))
        return rows
