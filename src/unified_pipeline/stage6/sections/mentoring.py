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
  (past) mentee whose end date says "present", or whose source text leaves its
  start year open ("2019-", "2019 - present"), is really current
  (`_is_ongoing_mentorship`), and an N3A (current) mentee whose dates
  ended before this year is really past (`_n3a_entry_has_ended`); an N3A/N3B
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
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
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
    _source_leaves_year_open,
    format_date_range,
)
from ..normalization import _clean_inline_tabs
from ..parsing import _is_mentee_record, _is_mentoring_outcome
from ..parsing.dates import _parse_date_components

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

# The instruction paragraph directly under N2_HEADING (template para 141),
# and N2's real insertion anchor (#529 round 2, F1): the template ships
# heading -> instruction -> placeholder table, and the tables this section
# builds belong after the instruction, not between it and the heading.
# Falls back to N2_HEADING when this paragraph is missing.
N2_INSTRUCTION = ("Duplicate table below as needed. Examples include serving "
                   "as PI or Mentor on T32, K01, K08, K23 or other mentored grants.")

# Spacing paragraph between mentee tables: 6pt before and after, in
# twentieths of a point (w:spacing units).
MENTEE_TABLE_SPACING_TWIPS = '120'

#: A "Role: <owner's role>" line on a mentee entry (web202: "Role: MPH
#: Advisor"). Stage 2 folds it into the mentee's entry (#986, PR #1017), but
#: the N3A/N3B schema has no role field and the table no role row, so it was
#: dropped; it now joins Type of Supervision.
_ROLE_LINE_RE = re.compile(r'(?:^|[\t\n])\s*Role:\s*([^\t\n]+)')

#: The WCM mentee table's own "Type of Supervision (Research, clinical,
#: teaching, leadership) | <value>" row, flattened into the entry text by
#: stage 2. The N3A/N3B schema has no supervision field, so stage 4 never
#: extracts it and `_infer_supervision_type` guessed from the level instead:
#: FINSIS rendered "Clinical" for residents whose row read "Research +
#: Teaching", and blank for mentees with no level.
_SUPERVISION_ROW_RE = re.compile(r'Type of Supervision[^|\t\n]*\| *([^|\t\n]+)', re.I)

#: An end_date value, whole, that means the mentorship is still running.
#: Any end_date containing "present" ("2019-present") counts as well.
_ONGOING_END_WORDS = frozenset({'ongoing', 'current', 'now'})

#: A year followed by a range-end word that says the range is still open:
#: "2019-present", "2019 - Current", "2019 to date". Anchored on the year
#: because a mentee row routinely says "now Assistant Professor at ..." or
#: "Current position: ..." about the MENTEE, which says nothing about whether
#: the mentoring is still going on.
_ONGOING_RANGE_END_RE = re.compile(
    r'(?<!\d)\d{4}\s*(?:[-\u2013\u2014]|\bto\b)\s*(?:present|current|now|ongoing|date)\b',
    re.IGNORECASE)

#: Any open-range marker anywhere in an entry's text: a dash or "to" before
#: present/pres/current/ongoing/date, whatever precedes it ("04-present",
#: "2019-pres", "MM/YYYY <U+2500> current" with a box-drawing dash), or a
#: dash after a digit that closes the line or meets stage 2's "|" cell
#: separator ("2005 - | <next cell>"). Looser than `_ONGOING_RANGE_END_RE`
#: on purpose, and used only to keep an N3A entry where stage 3b put it
#: (`_n3a_entry_has_ended`): a false match leaves the entry under Current
#: Mentees, as it rendered before, while the strict regex decides the other
#: direction, where a false match would move a past mentee. Each shape is an
#: N3A row whose source says it is ongoing and that would otherwise have
#: moved to Past -- four CVs in the s7ab batch and the census corpus.
_N3A_OPEN_MARKER_RE = re.compile(
    r'(?:[-\u2013\u2014\u2500]|\bto\b)\s*(?:pres(?:ent)?|current|ongoing|date)\b'
    r'|\d\s*[-\u2013\u2014\u2500]\s*(?:\||$)',
    re.IGNORECASE | re.MULTILINE)


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
    many N3B entries the ongoing-mentorship rule reclassified, and
    `moved_to_past` how many N3A entries `_n3a_entry_has_ended` did.
    """

    current: tuple[Mapping[str, Any], ...] = ()
    past: tuple[Mapping[str, Any], ...] = ()
    current_summaries: tuple[Mapping[str, Any], ...] = ()
    past_summaries: tuple[Mapping[str, Any], ...] = ()
    outcomes: tuple[Mapping[str, Any], ...] = ()
    moved_to_current: int = 0
    moved_to_past: int = 0

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


def _source_says_ongoing(source_text: str, start_date: str) -> bool:
    """True when the entry's own text writes its range as still open: a
    year followed by present/current/now/ongoing/"to date", or `start_date`'s
    year followed by an open dash ("2019-", "(2019- )")."""
    if _ONGOING_RANGE_END_RE.search(source_text):
        return True
    start_year = _parse_date_components(start_date)[0]
    return _source_leaves_year_open(source_text, start_year)


def _is_ongoing_mentorship(fields: Mapping[str, Any], source_text: str) -> bool:
    """The "N3B + present = current" rule: an end date that says the
    relationship is still running, or a start date with no end date whose
    source text leaves the range open.

    A start date alone is NOT enough. Stage 4 stores a mentee's lone year --
    a completion, visit or class year -- as start_date with no end, and
    reading that as "still running" moved 121 past mentees on 4 CVs under
    Current Mentees as "YYYY-present" (class 1, 2026-10-02 s7ab autopsy;
    residue of #556 and #1220).
    """
    end_date = str(fields.get('end_date', '') or '').strip()
    start_date = str(fields.get('start_date', '') or '').strip()
    end_lower = end_date.lower()
    if 'present' in end_lower or end_lower in _ONGOING_END_WORDS:
        return True
    if not start_date or end_date:
        return False
    return _source_says_ongoing(source_text, start_date)


def _entry_is_ongoing(entry: Mapping[str, Any]) -> bool:
    """`_is_ongoing_mentorship` for one raw stage-4 entry."""
    return _is_ongoing_mentorship(entry.get('extracted_fields') or {},
                                  _text(entry.get('text')))


def _n3a_entry_has_ended(entry: Mapping[str, Any], current_year: int) -> bool:
    """True when an N3A (current) entry's own dates say the mentorship is
    over: it is not ongoing (`_is_ongoing_mentorship`), and the last year it
    states -- the end date's, else the start date's -- is before
    `current_year`.

    The mirror of the N3B rule. Stage 3b codes N3A for a lone past year
    ("2015 <mentee>, dissertation") and for a closed period ("2023-2024"),
    and every N3A row rendered under Current Mentees, a lone year as
    "<year>-present" (EBYSBC autopsy, class E9). A year that is not before
    `current_year` stays current: a lone year can be an expected completion
    ("defense 2027"), and a period ending this year may not be over yet.
    A date with no readable year says nothing, and neither does a text with
    any open-range marker in it (`_N3A_OPEN_MARKER_RE`), so either entry
    stays where 3b put it.
    """
    if _entry_is_ongoing(entry) or _N3A_OPEN_MARKER_RE.search(_text(entry.get('text'))):
        return False
    fields = entry.get('extracted_fields') or {}
    last_date = _text(fields.get('end_date')).strip() or _text(fields.get('start_date')).strip()
    last_year = _parse_date_components(last_date)[0] if last_date else None
    return last_year is not None and last_year < current_year


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
    stays a pure, document-free function. Not exactly the same call (#529
    round 2, F5): `_format_grant_duration` (research_support.py:889-900)
    also falls back to `fields.get('date')` when `start_date` is absent, for
    clinical-trial schemas that key duration off `date` instead. N2's schema
    has no `date` key, so the two agree in practice, but the claim that they
    are the same call was wrong.
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
    pre-existing, out-of-scope cascade in `research_support.py` (issue #836:
    `_fill_research_support`'s "Past (Completed) Funding"/"Pending
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
    """One N1 line: the entry's own `text`, stripped, rendered verbatim.

    Rounds 1-2 assembled the line from role/program_name/institution/dates,
    which silently shortened any entry where stage 4 extracted only SOME of
    those fields -- a role-only entry rendered as one word even though its
    source line said more. N1's template slot is a free-text line ("Describe
    activity; include dates"), not a table, exactly like the N4 outcome
    lines one heading down already render `text` verbatim, so this does the
    same (#529 round 3): no field assembly when there is text to use as-is.

    Falls back to the non-empty role/program_name/institution/date-range
    fields joined with ', ' ONLY when `text` itself is empty or missing --
    the sole remaining use of `extracted_fields` here, kept so a stage-4
    record with fields but no narrative `text` still renders a line instead
    of being silently dropped.
    """
    stripped = (text or '').strip()
    if stripped:
        return stripped

    role = str(fields.get('role') or '').strip()
    program_name = str(fields.get('program_name') or '').strip()
    institution = str(fields.get('institution') or '').strip()
    start = str(fields.get('start_date') or '').strip()
    end = str(fields.get('end_date') or '').strip()
    date_range = format_date_range(start, end, 'N1') if (start or end) else ''

    return ', '.join(
        part for part in (role, program_name, institution, date_range) if part)


# An N4 outcome line rebuilt from its stage-4 fields, in the source table's
# column order (date, outcome, project, mentee) and joined the way
# `_clean_inline_tabs` joins a table row's cells (#1434).
_OUTCOME_PART_SEPARATOR = ' — '
_OUTCOME_REQUIRED_FIELDS = ('output_type', 'mentee_name')
_OUTCOME_TITLE_FIELD = 'title'
_OUTCOME_DATE_FIELD = 'date'
_OUTCOME_CODE = 'N4'

# A content word for comparing an outcome line with its fields: a run of
# letters or digits, case-folded, so punctuation and the separators the
# reader or this module adds never count as a difference.
_OUTCOME_WORD_RE = re.compile(r'[^\W_]+', re.UNICODE)


def _outcome_words(text: str) -> list[str]:
    """`text` reduced to its case-folded content words, in order."""
    return _OUTCOME_WORD_RE.findall(str(text or '').casefold())


def _is_contiguous_in(words: Sequence[str], source: Sequence[str]) -> bool:
    """True when `words` occurs in `source` as one unbroken run."""
    span = len(words)
    return any(source[i:i + span] == words for i in range(len(source) - span + 1))


def _outcome_date(fields: Mapping[str, Any]) -> str:
    """The outcome's date as stage 4 wrote it, or its start/end range."""
    date = _plain_text(fields.get(_OUTCOME_DATE_FIELD))
    if date:
        return date
    start = _plain_text(fields.get('start_date'))
    end = _plain_text(fields.get('end_date'))
    return format_date_range(start, end, _OUTCOME_CODE) if (start or end) else ''


def _outcome_line_from_fields(entry: Mapping[str, Any]) -> str | None:
    """The N4 line rebuilt from stage-4 fields, or None to render the text.

    A source table whose cells wrap over several lines reaches stage 2 with
    the cells' lines interleaved ("2021 Travel Award Spray drying of Jane Roe
    <TAB>Grant protein<TAB>formulations"), and the verbatim text reads as
    word salad, while stage 4 untangled the columns correctly (#1434). The
    fields replace the text only when they say EXACTLY what the text says --
    the same content words, the same number of times -- and at least one
    field's words are scattered in the text rather than in one run. So a
    partial extraction (a word the fields lost), a placeholder ("Not
    specified") or a normalised value (a date rewritten) keeps the text, and
    a line that already reads in order is left as written.
    """
    if not _is_mentoring_outcome(entry):
        return None
    fields = entry.get('extracted_fields') or {}
    output_type, mentee = (_plain_text(fields.get(key)) for key in _OUTCOME_REQUIRED_FIELDS)
    if not (output_type and mentee):
        return None
    parts = [part for part in (_outcome_date(fields), output_type,
                               _plain_text(fields.get(_OUTCOME_TITLE_FIELD)), mentee)
             if part]
    source = _outcome_words(entry.get('text') or '')
    if sorted(_outcome_words(' '.join(parts))) != sorted(source):
        return None
    if all(_is_contiguous_in(_outcome_words(part), source) for part in parts):
        return None
    return _OUTCOME_PART_SEPARATOR.join(parts)


def _partition_mentoring_entries(
    entries_by_code: Mapping[str, Sequence[Mapping[str, Any]]],
    *,
    current_year: int,
) -> MentoringPartition:
    """Decide the whole of section N without touching a document.

    Order matters: the ongoing-mentorship reshuffle runs first because it keys
    off dates a summary never has, so the mentee/summary split cannot change
    its outcome; the N4 reclaim runs last because `_is_mentoring_outcome`
    matches entries the mismatch-corrector rewrote to N3A, which only the
    summary split has isolated by then. The reshuffle runs both ways, each
    on the entries stage 3b coded: an N3B entry the source leaves open moves
    to Current, an N3A entry that ended before `current_year` moves to Past.
    """
    n3a_coded = list(entries_by_code.get('N3A', []))
    n3b_coded = list(entries_by_code.get('N3B', []))
    n4_entries = list(entries_by_code.get('N4', []))

    moved = [entry for entry in n3b_coded if _entry_is_ongoing(entry)]
    ended = [entry for entry in n3a_coded if _n3a_entry_has_ended(entry, current_year)]
    n3a_entries = [entry for entry in n3a_coded
                   if not _n3a_entry_has_ended(entry, current_year)] + moved
    n3b_entries = [entry for entry in n3b_coded if not _entry_is_ongoing(entry)] + ended

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
        moved_to_past=len(ended),
    )


def _contains_words(text: str, words: str) -> bool:
    """True when `words` appears in `text` as whole words, ignoring case:
    "Fellow" is in "Transplant Fellow", "Postdoc" is not in "Postdoctoral
    advisor" -- there the level is a different word, so it is no part of
    what `text` already says."""
    return re.search(rf'(?<!\w){re.escape(words)}(?!\w)', text, re.IGNORECASE) is not None


# Off-schema keys stage 4 puts a mentee's institution and advisor under
# (EBYSBC E14: HFAJCC-09 `advisor`/`advisors`, ZCTARO-07 `institution`).
# No other row reads them, so each joins Site/Position, in this order, with
# the label the advisor keys get; the same block's other rows carry
# "Advisor: <name>" inside `site_position` itself.
_SITE_EXTRA_KEYS: tuple[tuple[str, str], ...] = (
    ('institution', ''),
    ('advisor', 'Advisor: '),
    ('advisors', 'Advisors: '),
)
_SITE_PART_SEPARATOR = ', '
_LIST_VALUE_SEPARATOR = '; '


def _plain_text(value: object) -> str:
    """A string field, or a list of strings joined, stripped; '' for
    anything else (a dict is no cell value)."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return _LIST_VALUE_SEPARATOR.join(
            v.strip() for v in value if isinstance(v, str) and v.strip())
    return ''


def _with_site_extras(site_position: str, fields: Mapping[str, Any]) -> str:
    """`site_position` with the mentee's institution and advisor appended
    when stage 4 kept them under off-schema keys (`_SITE_EXTRA_KEYS`). A
    value Site/Position already holds as whole words is not repeated."""
    parts = [site_position] if site_position else []
    for key, label in _SITE_EXTRA_KEYS:
        value = _plain_text(fields.get(key))
        if value and not _contains_words(site_position, value):
            parts.append(f"{label}{value}")
    return _SITE_PART_SEPARATOR.join(parts)


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


def _normalize_mentee(entry: Mapping[str, Any], *, ongoing: bool) -> MenteeRecord:
    """Resolve one raw stage-4 mentee dict into a `MenteeRecord`.

    `ongoing` is whether the entry renders under Current Mentees, which is
    the only place a start date with no end date reads as "-present".

    `extracted_fields` is sometimes explicitly `None` rather than absent
    (#659), which a bare `.get('extracted_fields', {})` does not cover.

    Site/Position prefers mentee_level (degree type) combined with
    site_position when they differ. When site_position already contains
    mentee_level as whole words (`_contains_words`) it is the longer, more
    specific value and renders alone: "Transplant Fellow" with level
    "Fellow" is "Transplant Fellow", not "Fellow" (EBYSBC autopsy, class
    E14: 12 rows on one CV lost the fellowship type). Awards and
    fellowships the mentee won
    belong in Project/Accomplishments: the WCM template's footnote for that
    row reads "Optional: List publications, awards, grants ... arising
    directly from the mentoring activity", and stage 4 writes them to
    awards/funding_source, which nothing read before -- 133 corpus values
    were extracted and then dropped.
    """
    fields = entry.get('extracted_fields') or {}

    mentee_level = _text(fields.get('mentee_level'))       # e.g. "PhD, MBSB"
    site_pos_raw = _text(fields.get('site_position'))      # e.g. "Thesis"
    if mentee_level and site_pos_raw and not _contains_words(site_pos_raw, mentee_level):
        site_position = f"{mentee_level} - {site_pos_raw}"
    else:
        site_position = site_pos_raw or mentee_level
    site_position = _with_site_extras(site_position, fields)

    project = _text(fields.get('research_focus') or fields.get('dissertation_title'))
    mentee_awards = _text(fields.get('awards') or fields.get('funding_source')).strip()
    if mentee_awards and mentee_awards.casefold() not in project.casefold():
        project = f"{project}\nAwards: {mentee_awards}" if project else f"Awards: {mentee_awards}"

    supervision_type = _text(fields.get('supervision_type'))
    if not supervision_type:
        row_match = _SUPERVISION_ROW_RE.search(_text(entry.get('text')))
        supervision_type = row_match.group(1).strip() if row_match else ''
    role_match = _ROLE_LINE_RE.search(_text(entry.get('text')))
    role = role_match.group(1).strip() if role_match else ''
    if not supervision_type:
        supervision_type = _infer_supervision_type(mentee_level or site_pos_raw)
    if role and role.casefold() not in supervision_type.casefold():
        supervision_type = f"{supervision_type} ({role})" if supervision_type else role

    return MenteeRecord(
        name=_text(fields.get('name') or fields.get('mentee_name')),
        site_position=site_position,
        mentoring_period=_format_mentee_duration(
            fields, ongoing=ongoing, source_text=_text(entry.get('text'))),
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


def _first_cell_text(table_element: BaseOxmlElement) -> str:
    """The raw text of `table_element`'s first `w:tc`, or '' if it has none.

    Reads the XML directly rather than wrapping in `docx.table.Table` --
    `_first_table_after` hands us the bare element and this only needs one
    cell's text, not the table object. Joins every `w:t` descendant's own
    `.text` rather than using `itertext()`: python-docx's `CT_P`/`CT_R`
    element classes override `.text` as a Python property that already
    aggregates their own descendants' text, so `itertext()` walks those
    overridden values too and repeats each run's text once per ancestor --
    a lone "Name" run inside one paragraph inside one cell comes back
    "NameNameName", not "Name".
    """
    first_tc = table_element.find(f'.//{qn("w:tc")}')
    if first_tc is None:
        return ''
    return ''.join(t.text or '' for t in first_tc.findall(f'.//{qn("w:t")}'))


def _looks_like_mentee_placeholder(table_element: BaseOxmlElement) -> bool:
    """True when `table_element`'s first cell is exactly "Name" (#836, same
    pattern as `leadership._looks_like_leadership_table`).

    Exact match, not substring: other template tables' row-0 cell-0 CONTAINS
    "name" without being a mentee placeholder -- "Name of Committee" (section
    P) and "Name of award" (section elsewhere) -- so a substring test would
    keep the very bug this guard exists to stop.
    """
    return _first_cell_text(table_element).strip().lower() == 'name'


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
        current_year: int | None = None,
    ) -> None:
        """Render section N: a table per mentee under "Current Mentees:" and
        "Past Mentees:", aggregate lines under the same headings, N4 outcome
        lines under the MENTORING header, N1 lines under "Leadership and
        mentoring in programs..." and N2 tables under "Institutional
        Training Grants..." (#529).

        Rendering only -- `_partition_mentoring_entries` has already decided
        which entry goes where. A heading the template lacks is logged with
        the count it drops; nothing returns silently.

        The mentee placeholder removal below is unconditional: a heading
        with no mentees, or a CV with no mentees at all, still gets its
        template placeholder(s) stripped, same as every other section
        already strips its own empty placeholder -- the delivered document
        is a finished record, not a form (#845).

        `current_year` is the year an N3A period must end before to render
        under Past Mentees (`_n3a_entry_has_ended`). It defaults to the
        system clock, as `_fill_research_support`'s does; pass it to make
        the boundary reproducible.
        """
        if current_year is None:
            current_year = datetime.now().year
        # N1/N2 have their own template anchors and no interaction with the
        # mentee partition below -- called first so a CV with ONLY N1/N2
        # entries (no N3A/N3B/N4) still renders instead of hitting the
        # partition's early return just below.
        self._fill_program_leadership(entries_by_code.get('N1', []))
        self._fill_training_grants(entries_by_code.get('N2', []))

        partition = _partition_mentoring_entries(entries_by_code, current_year=current_year)
        current_anchor, past_anchor = self._mentoring_anchors()

        # Placeholder removal happens before the empty-partition return
        # below, and before anything renders: `_first_table_after` scans to
        # the next table anywhere below its heading, so once one group has
        # rendered, the other heading's scan would find those new tables
        # instead of the template's. Once per distinct anchor, so a shared
        # fallback anchor (both headings missing) is not cleared twice.
        cleared: list[BaseOxmlElement] = []
        for anchor in (current_anchor, past_anchor):
            if anchor is not None and not any(anchor is seen for seen in cleared):
                self._remove_template_table_after(anchor)
                cleared.append(anchor)

        if partition.is_empty:
            return

        logger.info("Filling Mentoring (%d mentees, %d summary/outcome lines)...",
                    partition.mentee_count, partition.line_count)
        if partition.moved_to_current:
            logger.info("  Moved %d mentees from Past to Current (end_date=present)",
                        partition.moved_to_current)
        if partition.moved_to_past:
            logger.info("  Moved %d mentees from Current to Past (ended before %d)",
                        partition.moved_to_past, current_year)

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
            (mentees, summaries, anchor, heading, ongoing)
            for mentees, summaries, anchor, heading, ongoing in (
                (partition.past, partition.past_summaries, past_anchor,
                 PAST_MENTEES_HEADING, False),
                (partition.current, partition.current_summaries, current_anchor,
                 CURRENT_MENTEES_HEADING, True),
            )
            if mentees or summaries
        ]

        # Only warn about a missing heading when there was something to
        # render under it; the placeholder removal above already ran for
        # every anchor regardless of content.
        for mentees, summaries, anchor, heading, _ongoing in groups:
            if anchor is None:
                logger.warning(
                    "Mentoring: '%s' heading not found in template; "
                    "%d entries not rendered", heading, len(mentees) + len(summaries))

        for mentees, summaries, anchor, _heading, ongoing in groups:
            if anchor is not None:
                self._render_mentee_group(mentees, summaries, anchor, ongoing=ongoing)

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
            fallback_idx = self._find_header_paragraph(MENTEES_FALLBACK_TEXT)
        fallback = self._paragraph_element(fallback_idx)
        return fallback, fallback

    def _remove_template_table_after(self, anchor: BaseOxmlElement) -> None:
        """Drop the template's placeholder mentee table under a heading, if
        one is there, before the real tables go in.

        `_first_table_after` has no section boundary -- it returns the first
        table anywhere below the anchor, which on a research-support cascade
        upstream (#836) could be some other section's table entirely. The
        shape guard keeps this removal to a table that is actually a mentee
        placeholder.
        """
        table_element = _first_table_after(anchor)
        if table_element is not None and _looks_like_mentee_placeholder(table_element):
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
                "Mentoring: '%s' heading not found; %d entries rendered "
                "under MENTORING instead", N1_HEADING, len(entries))
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
        placeholder, inserted after the instruction paragraph ("Duplicate
        table below as needed...") rather than the heading itself, so
        document order stays heading -> instruction -> tables, the order
        the template ships with (#529 round 2, F1). Falls back to the
        heading when the instruction paragraph is missing.

        The placeholder removal is unconditional-but-guarded (#529 round 2,
        F3): `_remove_training_grant_placeholder` runs whenever an anchor
        is found at all, whether or not there are N2 entries to render, and
        only ever deletes a table that actually looks like N2's own. On the
        real pipeline this is a no-op either way: issue #836's cascade in
        `research_support.py` has already removed the table before
        `_fill_mentoring` runs, on every render, N2 content or not.

        A sparse entry (no title, agency or grant number -- nothing a table
        would show) renders as a plain line instead; N2 has no other route
        out of the Appendix, so it is never dropped. Missing-anchor
        fallback (only reached when there are entries to place) matches
        `_fill_program_leadership`.
        """
        anchor_idx = self._find_paragraph_exact(N2_INSTRUCTION)
        if anchor_idx is None:
            anchor_idx = self._find_paragraph_exact(N2_HEADING)
        anchor = self._paragraph_element(anchor_idx)
        if anchor is not None:
            self._remove_training_grant_placeholder(anchor)

        if not entries:
            return

        if anchor is None:
            logger.warning(
                "Mentoring: '%s' heading not found; %d entries rendered "
                "under MENTORING instead", N2_HEADING, len(entries))
            anchor = self._paragraph_element(self._find_paragraph_exact(MENTORING_HEADING))
            if anchor is None:
                return

        for entry in reversed(entries):
            fields = entry.get('extracted_fields') or {}
            if _training_grant_is_sparse(fields):
                text = _clean_inline_tabs((entry.get('text') or '').strip())
                if text:
                    self._insert_mentoring_line(text, anchor, entry)
                continue
            self._create_training_grant_table(fields, entry, anchor)

    def _remove_training_grant_placeholder(self, anchor: BaseOxmlElement) -> None:
        """Remove the table after ``anchor`` only when its header row reads
        N2's own placeholder label (#529 round 2, F3). Unconditional (called
        whether or not N2 has entries to render) but guarded: never deletes
        a table it does not recognize as its own -- the #836 cascade this
        guards against found and emptied an unrelated section's table on
        `web199` when the removal was unconditional AND unguarded."""
        table_element = _first_table_after(anchor)
        if table_element is not None and _looks_like_training_grant_table(
                Table(table_element, self.doc)):
            table_element.getparent().remove(table_element)

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
        *,
        ongoing: bool,
    ) -> None:
        """One heading's worth of content: the mentee tables in reverse so the
        document order matches `mentees`, then the summary lines, which go in
        last so they land directly under the heading, above the tables.
        `ongoing` is True under Current Mentees (see `_normalize_mentee`)."""
        for entry in reversed(mentees):
            self._create_mentee_table_with_spacing(
                _normalize_mentee(entry, ongoing=ongoing), anchor)
        self._insert_mentoring_summaries(summaries, anchor)

    def _insert_mentoring_summaries(
        self,
        entries: Sequence[Mapping[str, Any]],
        anchor: BaseOxmlElement,
    ) -> None:
        """Render summary/outcome entries as plain lines after ``anchor``.

        Reversed so that, with each insert landing immediately after the anchor
        and pushing the previous one down, the final document order matches
        ``entries``. An N4 line whose text is a scrambled table row renders
        from its fields instead (`_outcome_line_from_fields`, #1434).
        """
        for entry in reversed(entries):
            source = _outcome_line_from_fields(entry) or (entry.get('text') or '').strip()
            text = _clean_inline_tabs(source)
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
