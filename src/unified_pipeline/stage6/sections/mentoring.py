"""Section N: mentoring -- current mentees, past mentees, outcomes (#398).

The WCM template gives each mentee an individual table rather than one row in a
shared table, so this section builds document structure instead of filling it,
and the insert order is inverted throughout: every table goes in immediately
after its heading and pushes the previous one down, so the writers iterate
`reversed(...)` to end up in the intended order.

The section is two layers (#739 review):

- `_partition_mentoring_entries` decides WHAT renders where and touches no
  document. It re-partitions the stage-4 codes three times, and each pass
  exists because rendering the codes literally lost content (#261): an N3B
  (past) mentee whose end date says "present", or that has a start date and no
  end date at all, is really current (`_is_ongoing_mentorship`); an N3A/N3B
  entry that names no mentee is an aggregate count, not a mentee, and renders
  as a plain line; N4 outcome narrative arrives disguised as a current mentee
  and is reclaimed for the section header. `_normalize_mentee` then resolves
  one entry's field aliases into a `MenteeRecord`, so the table renderer reads
  no `extracted_fields` at all.
- The `MentoringSection` methods render. Every insert goes through
  `_insert_after` against an anchor ELEMENT resolved once per heading -- never
  a paragraph index, which the inserts themselves shift. A detached anchor
  raises `DetachedAnchorError` out of the section rather than leaving the
  content at the end of the document unreported.

`_create_mentee_table_with_spacing` is the one the writer actually calls;
`_create_mentee_table` is the table itself, kept separate because the spacing
paragraph has to be inserted between the heading and the table.
"""
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

try:
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.oxml.xmlchemy import BaseOxmlElement
    from docx.table import Table
    from docx.text.paragraph import Paragraph
except ImportError as exc:
    raise ImportError(
        "python-docx is required for stage 6. Install with: pip install python-docx lxml"
    ) from exc

from ..formatting import (
    _format_mentee_duration,
    _insert_after,
    _set_cell_vertical_alignment,
    _set_font,
    _set_table_border,
    format_date_range,
)
from ..normalization import _clean_inline_tabs
from ..parsing import _is_mentee_record, _is_mentoring_outcome

logger = logging.getLogger(__name__)

# Template paragraphs the section anchors on, exact text (case-insensitive,
# stripped). "MENTORING" doubles as the N4 outcome anchor and, with the
# "Mentees" substring, as the fallback when neither mentee heading exists.
CURRENT_MENTEES_HEADING = "Current Mentees:"
PAST_MENTEES_HEADING = "Past Mentees:"
MENTORING_HEADING = "MENTORING"
MENTEES_FALLBACK_TEXT = "Mentees"

# N1/N2 template slots, immediately above the mentee headings (#529). Both
# strings are unique substrings of the template's own boilerplate (scout
# C2-mentoring.md §8), so _find_paragraph_exact carries no collision risk
# the way a bare "mentoring"/"leadership" search would.
N1_HEADING = "Leadership and mentoring in programs (Describe activity; include dates)"
N2_HEADING = "Institutional Training Grants and Mentored Trainee Grants"

# Spacing paragraph between mentee tables: 6pt before and after, in
# twentieths of a point (w:spacing units).
MENTEE_TABLE_SPACING_TWIPS = '120'


@dataclass(frozen=True)
class MenteeRecord:
    """One per-mentee table, fully resolved.

    Built only by `_normalize_mentee`, so 'name' vs 'mentee_name', how
    mentee_level and site_position combine, and where the mentee's awards
    go are each decided in one place. `source_entry` is the stage-4 dict,
    carried through only so `_add_entry_comments` can attach the upstream
    pipeline comments to the table.
    """

    name: str = ''
    site_position: str = ''
    mentoring_period: str = ''
    project: str = ''
    current_position: str = ''
    supervision_type: str = ''
    source_entry: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class MentoringPartition:
    """Where each section-N entry renders, decided without a document.

    `current`/`past` are mentee entries (one table each); the two summary
    tuples are the aggregate lines under the same two headings; `outcomes`
    are the N4 lines under the section header. `moved_to_current` is how
    many N3B entries the ongoing-mentorship rule reclassified.
    """

    current: tuple[Mapping[str, Any], ...] = ()
    past: tuple[Mapping[str, Any], ...] = ()
    current_summaries: tuple[Mapping[str, Any], ...] = ()
    past_summaries: tuple[Mapping[str, Any], ...] = ()
    outcomes: tuple[Mapping[str, Any], ...] = ()
    moved_to_current: int = 0

    @property
    def mentee_count(self) -> int:
        return len(self.current) + len(self.past)

    @property
    def line_count(self) -> int:
        return (len(self.current_summaries) + len(self.past_summaries)
                + len(self.outcomes))

    @property
    def is_empty(self) -> bool:
        return self.mentee_count + self.line_count == 0


def _text(value: object) -> str:
    """A field value as the string that will appear in a cell: '' for any
    falsy value (None, '', 0), `str()` of anything else."""
    return str(value) if value else ''


def _is_ongoing_mentorship(fields: Mapping[str, Any]) -> bool:
    """The "N3B + present = current" rule: an end date that says the
    relationship is still running, or a start date with no end date at all
    (which would otherwise display as "2019-present" under Past Mentees).
    """
    end_date = str(fields.get('end_date', '') or '').strip()
    start_date = str(fields.get('start_date', '') or '').strip()
    end_lower = end_date.lower()
    if 'present' in end_lower or end_lower in ('ongoing', 'current', 'now'):
        return True
    return bool(start_date and not end_date)


def _training_grant_rows(fields: Mapping[str, Any]) -> list[tuple[str, str]]:
    """The three (label, value) rows of an N2 training-grant table, same
    labels as the template's own placeholder table (#529).

    N2's live extraction schema (config-merged, `field_schemas_v1.1.json`)
    has no `title`/`pi_role` keys the way M2A/B/C do -- the real keys are
    `grant_title` and `role` -- so this does not share `research_support.py`'s
    `_create_grant_table` field reads; it is a smaller, N2-specific set.
    The role has no row of its own in the template's 3-row table, so it is
    folded into Award Source as a parenthetical -- the instruction line asks
    for PI-vs-Mentor, and this is the only cell that can carry it (judgement
    call, see the PR description).

    Duration calls `format_date_range` directly (not the `self`-bound
    `_format_grant_duration` research_support.py's grant table uses) so this
    stays a pure, document-free function; it is the same underlying
    formatter either way (`_format_grant_duration` is exactly this call).
    """
    agency = str(fields.get('agency') or '').strip()
    grant_number = str(fields.get('grant_number') or '').strip()
    role = str(fields.get('role') or '').strip()

    award_source = agency
    if grant_number and grant_number.casefold() not in award_source.casefold():
        award_source = f"{award_source} ({grant_number})" if award_source else grant_number
    if role:
        award_source = f"{award_source} ({role})" if award_source else f"({role})"

    title = (str(fields.get('grant_title') or '').strip()
             or str(fields.get('title') or '').strip()
             or str(fields.get('text') or '').strip())

    start = str(fields.get('start_date') or '').strip()
    end = str(fields.get('end_date') or '').strip()
    duration = format_date_range(start, end, 'N2')

    return [
        ('Award Source (funding agency, type of grant):', award_source),
        ('Project title:', title),
        ('Duration of support (mm/yyyy-mm/yyyy):', duration),
    ]


def _looks_like_training_grant_table(table: Table) -> bool:
    """True when `table`'s header row looks like N2's own placeholder --
    row 0, cell 0 reading "Award Source (funding agency, type of grant):".

    `_first_table_after`'s forward scan (used by `_remove_template_table_after`
    for N3A/N3B) has no awareness of what table it lands on, and a
    pre-existing, out-of-scope cascade in `research_support.py`
    (`_fill_research_support`'s "Past (Completed) Funding"/"Pending
    Funding" buckets, whose own unbounded `_find_table_after_paragraph`
    scan finds no table in their own template section and instead walks
    into MENTORING's) already removes this exact table on every real
    render, before `_fill_mentoring` ever runs. So the table N2's own scan
    finds is, in practice, always some LATER, unrelated section's
    placeholder -- removing it unconditionally would extend that cascade
    one link further. Same defensive shape check `_looks_like_leadership_table`
    (leadership.py) uses for the same reason (#664 item 2).
    """
    if not table.rows:
        return False
    header_cells = table.rows[0].cells
    if not header_cells:
        return False
    return 'award source' in header_cells[0].text.strip().lower()


def _training_grant_is_sparse(fields: Mapping[str, Any]) -> bool:
    """True when an N2 entry has nothing a table would show: no title (by
    any of the three precedence keys), no agency, no grant number. Renders
    as a plain line instead -- the table path is N2's only route out of the
    Appendix, so a sparse entry must not be dropped (#529)."""
    return not (
        fields.get('grant_title') or fields.get('title') or fields.get('text')
        or fields.get('agency') or fields.get('grant_number')
    )


def _program_leadership_line(fields: Mapping[str, Any], text: str) -> str:
    """One N1 line: the non-empty parts of role/program_name/institution,
    joined with ', ', then the date range in parentheses when either date is
    present. All five keys empty -> the entry's own `text` (#529)."""
    role = str(fields.get('role') or '').strip()
    program_name = str(fields.get('program_name') or '').strip()
    institution = str(fields.get('institution') or '').strip()
    start = str(fields.get('start_date') or '').strip()
    end = str(fields.get('end_date') or '').strip()

    if not (role or program_name or institution or start or end):
        return (text or '').strip()

    line = ', '.join(part for part in (role, program_name, institution) if part)
    if start or end:
        date_range = format_date_range(start, end, 'N1')
        if date_range:
            line = f"{line} ({date_range})" if line else f"({date_range})"
    return line


def _partition_mentoring_entries(
    entries_by_code: Mapping[str, Sequence[Mapping[str, Any]]],
) -> MentoringPartition:
    """Decide the whole of section N without touching a document.

    Order matters: the ongoing-mentorship reshuffle runs first because it keys
    off dates a summary never has, so the mentee/summary split cannot change
    its outcome; the N4 reclaim runs last because `_is_mentoring_outcome`
    matches entries the mismatch-corrector rewrote to N3A, which only the
    summary split has isolated by then.
    """
    n3a_entries = list(entries_by_code.get('N3A', []))
    n3b_entries = list(entries_by_code.get('N3B', []))
    n4_entries = list(entries_by_code.get('N4', []))

    moved = [entry for entry in n3b_entries
             if _is_ongoing_mentorship(entry.get('extracted_fields') or {})]
    n3b_entries = [entry for entry in n3b_entries
                   if not _is_ongoing_mentorship(entry.get('extracted_fields') or {})]
    n3a_entries.extend(moved)

    # Aggregate summaries (no mentee named) get a line, not a table.
    n3a_summaries = [e for e in n3a_entries if not _is_mentee_record(e)]
    n3b_summaries = [e for e in n3b_entries if not _is_mentee_record(e)]
    n3a_entries = [e for e in n3a_entries if _is_mentee_record(e)]
    n3b_entries = [e for e in n3b_entries if _is_mentee_record(e)]

    # Outcome narrative arrives disguised as a current mentee (see
    # _is_mentoring_outcome). Reclaim it for the section header rather than
    # "Current Mentees:", where it does not belong.
    n4_entries += [e for e in n3a_summaries + n3b_summaries
                   if _is_mentoring_outcome(e)]
    n3a_summaries = [e for e in n3a_summaries if not _is_mentoring_outcome(e)]
    n3b_summaries = [e for e in n3b_summaries if not _is_mentoring_outcome(e)]

    return MentoringPartition(
        current=tuple(n3a_entries),
        past=tuple(n3b_entries),
        current_summaries=tuple(n3a_summaries),
        past_summaries=tuple(n3b_summaries),
        outcomes=tuple(n4_entries),
        moved_to_current=len(moved),
    )


def _infer_supervision_type(level_text: str) -> str:
    """Supervision type from the mentee's level/position when stage 4 gave
    none. Checked in this order on purpose: a "clinical fellow" is Research
    (fellow) before it is Clinical, and the short 'ms'/'ma' tokens are tried
    last so they cannot pre-empt 'resident' inside a longer word."""
    level_lower = level_text.lower()
    if any(x in level_lower for x in ['phd', 'thesis', 'dissertation', 'doctoral']):
        return 'Research'
    if any(x in level_lower for x in ['postdoc', 'fellow']):
        return 'Research'
    if any(x in level_lower for x in ['resident', 'clinical']):
        return 'Clinical'
    if any(x in level_lower for x in ['master', 'ms', 'ma']):
        return 'Research'
    return ''


def _normalize_mentee(entry: Mapping[str, Any]) -> MenteeRecord:
    """Resolve one raw stage-4 mentee dict into a `MenteeRecord`.

    `extracted_fields` is sometimes explicitly `None` rather than absent
    (#659), which a bare `.get('extracted_fields', {})` does not cover.

    Site/Position prefers mentee_level (degree type) combined with
    site_position when they differ. Awards and fellowships the mentee won
    belong in Project/Accomplishments: the WCM template's footnote for that
    row reads "Optional: List publications, awards, grants ... arising
    directly from the mentoring activity", and stage 4 writes them to
    awards/funding_source, which nothing read before -- 133 corpus values
    were extracted and then dropped.
    """
    fields = entry.get('extracted_fields') or {}

    mentee_level = _text(fields.get('mentee_level'))       # e.g. "PhD, MBSB"
    site_pos_raw = _text(fields.get('site_position'))      # e.g. "Thesis"
    if mentee_level and site_pos_raw and mentee_level.lower() not in site_pos_raw.lower():
        site_position = f"{mentee_level} - {site_pos_raw}"
    else:
        site_position = mentee_level or site_pos_raw

    project = _text(fields.get('research_focus') or fields.get('dissertation_title'))
    mentee_awards = _text(fields.get('awards') or fields.get('funding_source')).strip()
    if mentee_awards and mentee_awards.casefold() not in project.casefold():
        project = f"{project}\nAwards: {mentee_awards}" if project else f"Awards: {mentee_awards}"

    supervision_type = _text(fields.get('supervision_type'))
    if not supervision_type:
        supervision_type = _infer_supervision_type(mentee_level or site_pos_raw)

    return MenteeRecord(
        name=_text(fields.get('name') or fields.get('mentee_name')),
        site_position=site_position,
        mentoring_period=_format_mentee_duration(fields),
        project=project,
        current_position=_text(fields.get('current_position')),
        supervision_type=supervision_type,
        source_entry=entry,
    )


def _mentee_table_rows(record: MenteeRecord) -> tuple[tuple[str, str], ...]:
    """The six (label, value) rows of a mentee table, in template order.
    Blank values are kept so every table has the same shape."""
    return (
        ('Name:', record.name),
        ('Site/Position:', record.site_position),
        ('Mentoring Period:', record.mentoring_period),
        ('Project/Accomplishments:', record.project),
        ('Current Position:', record.current_position),
        ('Type of Supervision:', record.supervision_type),
    )


def _first_table_after(anchor: BaseOxmlElement) -> BaseOxmlElement | None:
    """The first `w:tbl` among the anchor's following siblings, or None."""
    for sibling in anchor.itersiblings():
        if sibling.tag == qn('w:tbl'):
            return sibling
    return None


def _mentee_spacing_paragraph() -> BaseOxmlElement:
    """A blank paragraph with 6pt spacing before and after, for the gap
    between consecutive mentee tables."""
    spacing_para = OxmlElement('w:p')
    pPr = OxmlElement('w:pPr')
    spacing = OxmlElement('w:spacing')
    spacing.set(qn('w:before'), MENTEE_TABLE_SPACING_TWIPS)
    spacing.set(qn('w:after'), MENTEE_TABLE_SPACING_TWIPS)
    pPr.append(spacing)
    spacing_para.append(pPr)
    return spacing_para


class MentoringSection:
    """Section N writers, mixed into `WCMTemplateGenerator`."""

    def _fill_mentoring(
        self,
        entries_by_code: Mapping[str, Sequence[Mapping[str, Any]]],
    ) -> None:
        """Render section N: a table per mentee under "Current Mentees:" and
        "Past Mentees:", aggregate lines under the same headings, N4 outcome
        lines under the MENTORING header, N1 lines under "Leadership and
        mentoring in programs..." and N2 tables under "Institutional
        Training Grants..." (#529).

        Rendering only -- `_partition_mentoring_entries` has already decided
        which entry goes where. A heading the template lacks is logged with
        the count it drops; nothing returns silently.
        """
        # N1/N2 have their own template anchors and no interaction with the
        # mentee partition below -- called first so a CV with ONLY N1/N2
        # entries (no N3A/N3B/N4) still renders instead of hitting the
        # partition's early return just below.
        self._fill_program_leadership(entries_by_code.get('N1', []))
        self._fill_training_grants(entries_by_code.get('N2', []))

        partition = _partition_mentoring_entries(entries_by_code)
        if partition.is_empty:
            return

        logger.info("Filling Mentoring (%d mentees, %d summary/outcome lines)...",
                    partition.mentee_count, partition.line_count)
        if partition.moved_to_current:
            logger.info("  Moved %d mentees from Past to Current (end_date=present)",
                        partition.moved_to_current)

        current_anchor, past_anchor = self._mentoring_anchors()
        if current_anchor is None and past_anchor is None:
            logger.warning(
                "Mentoring: none of '%s', '%s' or %s found in template; "
                "%d entries not rendered", CURRENT_MENTEES_HEADING,
                PAST_MENTEES_HEADING, MENTORING_HEADING,
                partition.mentee_count + partition.line_count)
            return

        # Past before current: in the single-heading fallback both groups
        # share one anchor and every insert lands directly under it, so the
        # group rendered last is the one that ends up on top.
        groups = [
            (mentees, summaries, anchor, heading)
            for mentees, summaries, anchor, heading in (
                (partition.past, partition.past_summaries, past_anchor,
                 PAST_MENTEES_HEADING),
                (partition.current, partition.current_summaries, current_anchor,
                 CURRENT_MENTEES_HEADING),
            )
            if mentees or summaries
        ]

        # Placeholder tables first, all of them, before anything renders:
        # `_first_table_after` scans to the next table anywhere below its
        # heading, so once one group has rendered, the other heading's scan
        # would find those new tables instead of the template's. Once per
        # anchor, for the same reason under a shared fallback anchor.
        cleared: list[BaseOxmlElement] = []
        for mentees, summaries, anchor, heading in groups:
            if anchor is None:
                logger.warning(
                    "Mentoring: '%s' heading not found in template; "
                    "%d entries not rendered", heading, len(mentees) + len(summaries))
            elif not any(anchor is seen for seen in cleared):
                self._remove_template_table_after(anchor)
                cleared.append(anchor)

        for mentees, summaries, anchor, _heading in groups:
            if anchor is not None:
                self._render_mentee_group(mentees, summaries, anchor)

        # Mentoring outcomes (N4) have no table in the WCM template; they go
        # under the section header.
        if partition.outcomes:
            mentoring_anchor = self._paragraph_element(
                self._find_paragraph_exact(MENTORING_HEADING))
            if mentoring_anchor is None:
                logger.warning(
                    "Mentoring: %s heading not found in template; "
                    "%d outcome lines not rendered", MENTORING_HEADING,
                    len(partition.outcomes))
            else:
                self._insert_mentoring_summaries(partition.outcomes, mentoring_anchor)

    def _paragraph_element(self, index: int | None) -> BaseOxmlElement | None:
        """The body element behind a `_find_paragraph_*` result, or None.
        Resolved once and carried as the anchor: the element stays valid
        across inserts, the index does not."""
        if index is None:
            return None
        return self.doc.paragraphs[index]._element

    def _mentoring_anchors(self) -> tuple[BaseOxmlElement | None, BaseOxmlElement | None]:
        """(current anchor, past anchor). The exact mentee headings are
        preferred -- "MENTORING" as a substring matches other content. When
        neither exists both groups anchor on the section header, or on the
        first paragraph mentioning "Mentees"."""
        current = self._paragraph_element(self._find_paragraph_exact(CURRENT_MENTEES_HEADING))
        past = self._paragraph_element(self._find_paragraph_exact(PAST_MENTEES_HEADING))
        if current is not None or past is not None:
            return current, past
        fallback_idx = self._find_paragraph_exact(MENTORING_HEADING)
        if fallback_idx is None:
            fallback_idx = self._find_paragraph_with_text(MENTEES_FALLBACK_TEXT)
        fallback = self._paragraph_element(fallback_idx)
        return fallback, fallback

    def _remove_template_table_after(self, anchor: BaseOxmlElement) -> None:
        """Drop the template's placeholder mentee table under a heading, if
        one is there, before the real tables go in."""
        table_element = _first_table_after(anchor)
        if table_element is not None:
            table_element.getparent().remove(table_element)

    def _fill_program_leadership(self, entries: Sequence[Mapping[str, Any]]) -> None:
        """N1 ("Leadership and Mentoring in Programs"): one plain line per
        entry under its own template slot, directly above N2's (#529). A
        missing anchor falls back to the MENTORING header itself, same as
        every other content this section renders when its specific heading
        is gone -- entries are never silently dropped.
        """
        if not entries:
            return
        anchor = self._paragraph_element(self._find_paragraph_exact(N1_HEADING))
        if anchor is None:
            logger.warning(
                "Mentoring: '%s' heading not found in template; "
                "%d entries not rendered", N1_HEADING, len(entries))
            anchor = self._paragraph_element(self._find_paragraph_exact(MENTORING_HEADING))
            if anchor is None:
                return
        for entry in reversed(entries):
            fields = entry.get('extracted_fields') or {}
            line = _clean_inline_tabs(_program_leadership_line(fields, entry.get('text') or ''))
            if line:
                self._insert_mentoring_line(line, anchor, entry)

    def _fill_training_grants(self, entries: Sequence[Mapping[str, Any]]) -> None:
        """N2 ("Institutional Training Grants and Mentored Trainee Grants"):
        one 3-row table per entry, same shape as the template's own
        placeholder, which is removed once before the first table goes in
        (#529). A sparse entry (no title, agency or grant number -- nothing
        a table would show) renders as a plain line instead; N2 has no other
        route out of the Appendix, so it is never dropped. Missing-anchor
        fallback matches `_fill_program_leadership`.
        """
        if not entries:
            return
        anchor = self._paragraph_element(self._find_paragraph_exact(N2_HEADING))
        if anchor is None:
            logger.warning(
                "Mentoring: '%s' heading not found in template; "
                "%d entries not rendered", N2_HEADING, len(entries))
            anchor = self._paragraph_element(self._find_paragraph_exact(MENTORING_HEADING))
            if anchor is None:
                return
        else:
            placeholder = _first_table_after(anchor)
            if placeholder is not None and _looks_like_training_grant_table(
                    Table(placeholder, self.doc)):
                placeholder.getparent().remove(placeholder)

        for entry in reversed(entries):
            fields = entry.get('extracted_fields') or {}
            if _training_grant_is_sparse(fields):
                text = _clean_inline_tabs((entry.get('text') or '').strip())
                if text:
                    self._insert_mentoring_line(text, anchor, entry)
                continue
            self._create_training_grant_table(fields, entry, anchor)

    def _create_training_grant_table(
        self,
        fields: Mapping[str, Any],
        entry: Mapping[str, Any],
        anchor: BaseOxmlElement,
    ) -> Table:
        """One N2 grant's 3-row label/value table, built fresh (Pattern B --
        no template table survives to clone) and placed directly after
        ``anchor`` with a spacer, exactly as `_create_mentee_table_with_spacing`
        does one heading over (#529)."""
        rows = _training_grant_rows(fields)
        table = self.doc.add_table(rows=len(rows), cols=2)
        _set_table_border(table, color='808080', size=4)

        first_cell_para = None
        for i, (label, value) in enumerate(rows):
            row = table.rows[i]
            label_cell = row.cells[0]
            label_cell.text = label
            _set_cell_vertical_alignment(label_cell, 'center')
            for para in label_cell.paragraphs:
                if i == 0 and first_cell_para is None:
                    first_cell_para = para
                for run in para.runs:
                    _set_font(run, bold=True)

            value_cell = row.cells[1]
            value_cell.text = value
            _set_cell_vertical_alignment(value_cell, 'center')
            for para in value_cell.paragraphs:
                for run in para.runs:
                    _set_font(run)

        if first_cell_para:
            self._add_entry_comments(first_cell_para, entry)

        _insert_after(anchor, table._tbl)
        _insert_after(table._tbl, _mentee_spacing_paragraph())
        self.stats['tables_populated'] += 1
        self.stats['entries_inserted'] += 1
        return table

    def _render_mentee_group(
        self,
        mentees: Sequence[Mapping[str, Any]],
        summaries: Sequence[Mapping[str, Any]],
        anchor: BaseOxmlElement,
    ) -> None:
        """One heading's worth of content: the mentee tables in reverse so the
        document order matches `mentees`, then the summary lines, which go in
        last so they land directly under the heading, above the tables."""
        for entry in reversed(mentees):
            self._create_mentee_table_with_spacing(_normalize_mentee(entry), anchor)
        self._insert_mentoring_summaries(summaries, anchor)

    def _insert_mentoring_summaries(
        self,
        entries: Sequence[Mapping[str, Any]],
        anchor: BaseOxmlElement,
    ) -> None:
        """Render summary/outcome entries as plain lines after ``anchor``.

        Reversed so that, with each insert landing immediately after the anchor
        and pushing the previous one down, the final document order matches
        ``entries``.
        """
        for entry in reversed(entries):
            text = _clean_inline_tabs((entry.get('text') or '').strip())
            if text:
                self._insert_mentoring_line(text, anchor, entry)

    def _insert_mentoring_line(
        self,
        text: str,
        anchor: BaseOxmlElement,
        entry: Mapping[str, Any] | None = None,
    ) -> Paragraph:
        """Insert a plain mentoring paragraph directly after ``anchor``.

        Used for content that belongs in MENTORING but has no per-mentee table to
        live in: aggregate counts (N3A/N3B with no name) and outcome narrative (N4).
        """
        para = self.doc.add_paragraph()
        run = para.add_run(text)
        _set_font(run)
        if entry:
            self._add_entry_comments(para, entry)
        _insert_after(anchor, para._element)
        self.stats['entries_inserted'] += 1
        return para

    def _create_mentee_table(
        self,
        record: MenteeRecord,
        anchor: BaseOxmlElement,
    ) -> Table | None:
        """Build one mentee's 2-column table and place it directly after
        ``anchor``. None when the record names no mentee -- there is nothing
        to head the table with. Counts nothing; the caller that owns the
        spacing paragraph owns the stats too.

        WCM Template rows: Name; Site/Position; Mentoring Period;
        Project/Accomplishments; Current Position; Type of Supervision.
        """
        if not record.name:
            return None

        rows = _mentee_table_rows(record)
        table = self.doc.add_table(rows=len(rows), cols=2)
        _set_table_border(table, color='808080', size=4)

        first_cell_para = None
        for i, (label, value) in enumerate(rows):
            row = table.rows[i]
            label_cell = row.cells[0]
            label_cell.text = label
            _set_cell_vertical_alignment(label_cell, 'center')
            for para in label_cell.paragraphs:
                if i == 0 and first_cell_para is None:
                    first_cell_para = para
                for run in para.runs:
                    _set_font(run, bold=True)

            value_cell = row.cells[1]
            value_cell.text = _text(value)
            _set_cell_vertical_alignment(value_cell, 'center')
            for para in value_cell.paragraphs:
                for run in para.runs:
                    _set_font(run)

        if record.source_entry and first_cell_para:
            self._add_entry_comments(first_cell_para, record.source_entry)

        _insert_after(anchor, table._tbl)
        return table

    def _create_mentee_table_with_spacing(
        self,
        record: MenteeRecord,
        anchor: BaseOxmlElement,
    ) -> Table | None:
        """Create an individual mentee table with a spacing paragraph after it,
        and count it.

        Inserts: [anchor] -> [table] -> [spacing para]. Since tables are
        inserted in reverse order, the final document shows
        [heading] -> [table] -> [spacing] -> [table] -> [spacing] ...

        The stats move only on a table that was actually built: a record with
        no name renders nothing and counts nothing.
        """
        table = self._create_mentee_table(record, anchor)
        if table is None:
            return None
        _insert_after(table._tbl, _mentee_spacing_paragraph())
        self.stats['tables_populated'] += 1
        self.stats['entries_inserted'] += 1
        return table
