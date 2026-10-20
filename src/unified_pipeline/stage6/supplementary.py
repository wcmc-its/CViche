"""Supplementary prose as a tracked-deleted sub-point (#1205). On by default
since the owner's Word review of 2026-10-09; CVICHE_SUPPLEMENTARY_SUBPOINTS=0
turns it off.

An entry's supplementary prose -- a course description, a grant's aims, the
duties under an appointment -- has no field stage 4 extracts, so no section
renderer writes it and it reaches no part of the document. This pass runs
after every section and the #221 recovery have rendered. For each entry of a
code in `SUBPOINT_CODES`, it takes the entry's prose fragments that the
document provably does not contain and writes them under the entry's rendered
line as a Word tracked DELETION: the text came from the faculty member's own
CV and the WCM template has no slot for it, so "proposed removal" says what it
is. Reject the deletion to keep it; accept all changes to get the template.

Where it goes, by what the entry's rendered line is:

- a body paragraph (a teaching entry): a paragraph after it, one list level
  deeper, whose run AND paragraph mark are deleted, so accepting leaves no
  empty bullet;
- a table row (an appointment, a grant table, a service row): a row
  spanning the table, marked as a deleted row, after the entry's row -- or,
  for a label/value table holding one grant or one mentee, at the table's
  end. Accepting removes the row.

Only a fragment `_record_rendered` verifies absent is written, never one
that is merely reformatted, so a section that already prints its entry's
text gets nothing twice. A fragment that opens with a date range is a
record, not prose, and is left to `_recover_unrendered_records`.

The pass runs BEFORE the content-overflow routing and that recovery. The
overflow pass skips an entry that has a sub-point, and the recovery reads
each sub-point with its line (`SubPoint.as_rendered_line`), so an entry whose
prose is now a sub-point is not also re-emitted whole -- as a blue overflow
bullet or into the Appendix (owner decision 2026-10-08: duty prose goes under
the appointment, not to the Appendix).

The duties #1641 splits off an appointment title are written by the
positions writer itself, as a deleted row under the appointment
(`write_row_subpoint`), and recorded in the same lines and entry ids before
this pass runs, so they join its haystack and are not planned again.

The doctor reads the sub-points through one shared test
(`doctor.shared.offered_as_subpoint`): under_extraction and
unrendered_records count the prose as delivered for review, offschema_fields
still reports a value stage 4 misfiled, as INFO with a note. The
render-warnings sidecar records whose each sub-point is
(`SUBPOINT_PLACEMENTS_KEY`), so the doctor counts a sub-point only for the
entry it was written for, not for another entry it shares words with. The
render gate compares arms; its XML fingerprint sees every sub-point as a
change.

Codes: K1-K5 and M2A-M2D first, where loss is highest (#1205 body), plus
D1-D3 duties (owner decision 2026-10-08, lifting the D exclusion for this
variant). Then one code family at a time, each the one a stage-6 replay
measured losing the most prose of those left: Q1-Q2 extramural service next
(the remit, the meeting or the session title behind a committee or panel
row), then H honors (an award's criteria, or the paper it was for), then
N3A-N3B mentees (the mentee's awards and outputs, the owner's part), then
B1 degrees (the thesis or dissertation, a minor, a research topic), then O
institutional leadership (what a role oversaw or achieved), then P
institutional committees (a committee's remit, the member's part on it),
then R invited presentations (the audience, the session and its
co-presenters, what the owner organized). The
decision's exclusions (A, T, M1, E/G/J/N1/N4/S0/L1/L2, F1, Q4D,
B2) are not in `SUBPOINT_CODES`, so nothing else is touched. A Q2 entry
the service section reroutes into the Journal Reviewing table keeps its Q2
code, so Q4D's exclusion is also applied by where an entry rendered: the
generator passes that table as `excluded_tables`.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from typing import NamedTuple

from docx.document import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.oxml.xmlchemy import BaseOxmlElement
from docx.table import Table
from docx.text.paragraph import Paragraph

from unified_pipeline.core.template_boilerplate import (
    is_source_boilerplate,
    is_template_instruction,
)

from .dedup import _is_sibling_of
from .fan_out import _RENDERED_FIELDS, LAST_STAGE4_RECORD, _segments
from .formatting import (
    DATE_SPAN_SEPARATOR,
    format_date_list,
    further_date_spans,
    plain_entry_left_twips,
    plain_entry_level,
)
from .normalization import _pii_fragments, _squash
from .normalization.pii import _BARE_PHONE_SHAPE
from .parsing import _looks_like_multiple_records, _refold_shattered
from .parsing.text import (
    _RECORD_DATE_MONTH,
    _RECORD_YEAR,
    DATE_LED_MIN_RECORDS,
    _line_carries_record_date,
    _opens_dated_record,
)
from .pii_pass import PII_WITHHELD_KEY
from .render_check import (
    _RECORD_DATE_PREFIX_RE,
    _RENDER_TOKEN_RE,
    _norm,
    _record_rendered,
)

#: The switch both drivers read. Unset, it is `SUBPOINT_FLAG_DEFAULT` (on); an
#: off word (`_SUBPOINT_OFF_VALUES`, "0" for ops) renders exactly as before the
#: pass existed.
SUBPOINT_FLAG_ENV = "CVICHE_SUPPLEMENTARY_SUBPOINTS"
#: On: tracked deletion is the default once the spike passed (owner decisions
#: on #1205, 2026-10-02 and 2026-10-09).
SUBPOINT_FLAG_DEFAULT = "1"
#: The values that turn the switch off, read case-blind. "false" and "no"
#: are what an unquoted YAML `false` / `no` reach the reader as.
_SUBPOINT_OFF_VALUES = frozenset({"0", "false", "no", "off"})

TEACHING_CODES = frozenset({'K1', 'K2', 'K3', 'K4', 'K5'})
GRANT_CODES = frozenset({'M2A', 'M2B', 'M2C', 'M2D'})
APPOINTMENT_CODES = frozenset({'D1', 'D2', 'D3'})
#: Extramural service: leadership in an organization (Q1) and service on a
#: board or committee (Q2). Both render as table rows in Q's tables, so a
#: sub-point is a deleted row under the entry's row. Q4D stays excluded, and
#: so does a Q2 entry the section reroutes into the Journal Reviewing table:
#: `plan_subpoints` skips an anchor in an `excluded_tables` table.
SERVICE_CODES = frozenset({'Q1', 'Q2'})
#: Honors and awards: the award's criteria, what it was for, the paper or
#: poster it recognised. The honors section writes every H entry as a row of
#: its three-column table, so a sub-point is a deleted row under the award's
#: row. An entry rerouted to another code before rendering is grouped under
#: that code and follows its rule; one rerouted into H follows this one.
HONOR_CODES = frozenset({'H'})
#: Mentees, current (N3A) and past (N3B): the owner's part in the mentoring,
#: the mentee's programme, awards and outputs. The mentoring section writes
#: each named mentee as a six-row "Label:" table of its own, so a sub-point
#: is a deleted row at the end of that table (`_insert_row_after`), and only
#: that table's rows can anchor it (`_own_mentee_rows`): an entry naming no
#: mentee renders as a line of its whole text and gets none. A line listing
#: several mentees offers each one only its own part (`_own_mentee_line`).
#: The section files a mentee under either heading by its dates, not its
#: code, so both codes are placed over both headings' spans. An entry
#: rerouted to another code before rendering is grouped under that code and
#: follows its rule (N1 is excluded); one rerouted into N3A or N3B follows
#: this one.
MENTEE_CODES = frozenset({'N3A', 'N3B'})
#: Academic degrees: the thesis or dissertation, a minor, a research topic.
#: The degree's cell already prints its field, honors and advisor, so a
#: fragment mostly restating them is not offered (`_DEGREE_CELL_FIELDS`). The
#: education section writes each B1 entry as a row of
#: its four-column table, so a sub-point is a deleted row under the degree's
#: row. Only a row the education writer recorded for that entry can anchor
#: it (`_own_degree_rows`, the row-receipt ledger): two degrees of one
#: institution share every distinctive word of their rows, and an entry the
#: section skips (no degree and no institution) writes no row. That one row
#: is taken without a word test (`_pick_anchor`). An entry naming no degree
#: gets none: its row is an institution or a field alone. The last of
#: stage 4's records offers only its own segment of the line, and a line
#: holding several dated records offers none (`_own_record_line`). An entry
#: rerouted to another code before rendering is grouped under that code and
#: follows its rule (B2 is excluded, and C is not offered: its heading lookup
#: finds the title block, not its section); one rerouted into B1 follows
#: this one.
EDUCATION_CODES = frozenset({'B1'})
#: Institutional leadership (WCM table 20): what the role oversaw or
#: achieved -- the programmes led, a budget, the staff, an initiative and its
#: outcome, the scope of a directorship. The leadership section writes each O
#: entry as a row of its three-column table (role, the division and
#: institution, dates), so a sub-point is a deleted row under the role's row.
#: The row also prints stage 4's off-schema role and organization when the
#: schema's are empty (`LEADERSHIP_ROLE_FIELDS`). As for a degree, only a row
#: the leadership writer recorded for that entry can anchor it (`_own_rows`,
#: the row-receipt ledger): one title held twice at one institution shares
#: every distinctive word of its rows, as does a later title holding an
#: earlier one's words ("Assistant Director" over "Director"), and the dates
#: that tell them apart are digits. One such row is taken without a word test
#: (`_pick_anchor`), and the several rows the writer splits a flattened table
#: into are read by words. An entry the section wrote no row for (a dedup
#: loser, a table it would not bind to) gets none. The last of stage 4's
#: records offers only its own segment of the line, and a line holding
#: several dated records offers none (`_own_record_line`). An entry rerouted
#: to another code before rendering is grouped under that code and follows
#: its rule (N1 is excluded, and C, F2 and I are not offered: their heading
#: lookup finds the title block, not their section); one rerouted into O
#: follows this one.
LEADERSHIP_CODES = frozenset({'O'})
#: Institutional administrative activities and committee service (WCM table
#: 21): what the committee or activity did and what the member did on it --
#: its remit or purpose, the duties and terms, its sub-committees, the
#: constituency represented, the cases heard. The administrative-activities
#: section writes each P entry as a row of its three-column table (the
#: committee with its institution folded in, the role, the dates), so a
#: sub-point is a deleted row under the committee's row. The row prints stage
#: 4's off-schema committee names when the schema's is empty
#: (`ADMINISTRATIVE_ACTIVITY_FIELDS`). Only the one row the writer recorded
#: for that entry can anchor it (`_own_committee_row`, the row-receipt
#: ledger): committees of one institution, or held under one title, share
#: their rows' words. An entry holding several records gets none, as which of
#: them a line is about cannot be shown: one the writer prints as several
#: rows (a list of records under a committee name, #208/#248; a block of
#: dated lines it re-parses, #660), and one that one row prints though it
#: holds several (`_own_committee_line`): its parts hold several dated
#: records, a part names another committee, or stage 4 listed several, one
#: naming no committee and so printing no row (#256). The last of stage
#: 4's records offers only its own segment of the line. An entry rerouted to
#: another code before rendering is grouped under that code and follows its
#: rule (N1 is excluded, and C, F2 and I are not offered: their heading
#: lookup finds the title block, not their section); one rerouted into P
#: follows this one.
ADMINISTRATIVE_CODES = frozenset({'P'})
#: Invitations to speak and present: what a talk was beyond its fields --
#: its audience (how many, who they were), the session or panel and its
#: co-presenters, a continuing-education or sponsor note, a visit the owner
#: organized. The presentations section writes each R entry as one row of
#: its Regional, National or International table (the title, the venue with
#: the role and meeting folded in, the dates), so a sub-point is a deleted
#: row under the talk's row. The row prints stage 4's off-schema title,
#: venue, meeting and date names when the schema's are empty
#: (`PRESENTATION_TITLE_FIELDS` and the three after it), and a talk with no
#: title is titled from its line (`_untitled_talk_title`), which the document
#: then holds, so it is not offered again. Only the one row the writer
#: recorded for that entry can anchor it (`_own_rows`, the row-receipt
#: ledger, taken with no word test): two talks at one venue, or one title
#: given twice, share their rows' words. An entry holding several talks gets
#: none, as which of them a line is about cannot be shown
#: (`_own_talk_line`): the last of stage 4's records reads only its own
#: segment, and a line holding talks the row does not print offers nothing.
#: One talk given on several dates is one record when its row prints those
#: dates. An entry rerouted to another code before rendering is grouped under
#: that code and follows its rule (an excluded code offers nothing, and C,
#: F2 and I are not offered: their heading lookup finds the title block, not
#: their section); one rerouted into R follows this one.
PRESENTATION_CODES = frozenset({'R'})
#: The families whose writer records each entry's rows in the row-receipt
#: ledger, and whose prose goes only under one of those rows (`_anchor_scope`).
_OWN_ROW_CODES = EDUCATION_CODES | LEADERSHIP_CODES | ADMINISTRATIVE_CODES | PRESENTATION_CODES
#: The codes whose prose is offered. An allowlist, not the decision's
#: exclusion list: codes join one family at a time, as each is measured.
SUBPOINT_CODES = (TEACHING_CODES | GRANT_CODES | APPOINTMENT_CODES | SERVICE_CODES
                  | HONOR_CODES | MENTEE_CODES | EDUCATION_CODES | LEADERSHIP_CODES
                  | ADMINISTRATIVE_CODES | PRESENTATION_CODES)

#: A fragment needs this many distinctive tokens (`_RENDER_TOKEN_RE`, 5+
#: letters) to count as prose rather than a date, a label or a name.
SUBPOINT_MIN_TOKENS = 4

#: The anchor must hold at least this share of the entry's rendered field
#: tokens, so a sub-point never lands under an unrelated line.
SUBPOINT_ANCHOR_MIN_OVERLAP = 0.5
#: ... and at least this many of them: an entry whose only rendered token is
#: the owner's surname would otherwise anchor under the Personal Data name.
SUBPOINT_ANCHOR_MIN_TOKENS = 2

#: A fragment whose distinctive tokens are at least this share the entry's own
#: rendered field values is the record line itself, reformatted (a grant's
#: "Agency, Principal Investigator 2005-2007" line, rendered as table rows),
#: not prose: it is not offered.
SUBPOINT_FIELD_TOKEN_MAX_SHARE = 0.5

#: Revision author in Word's review pane, so a reviewer can tell these from
#: the enrichment and formatter changes and from their own edits.
SUBPOINT_AUTHOR = 'CViche: source text with no template field'

#: The render-warnings sidecar's key for the sub-points stage 6 wrote, one
#: `SubpointPlacement` record each. The doctor reads it so that a sub-point
#: vouches only for its own entry (`doctor.shared.offered_as_subpoint`). A
#: sidecar without it, written before the record existed, leaves the doctor
#: matching every sub-point in the document.
SUBPOINT_PLACEMENTS_KEY = 'subpoint_placements'

#: A link, an e-mail address or a phone number -- a contact line fused into a
#: position entry, a webinar's tracking URL (EBYSBC) -- is not prose, and an
#: address or a number is not offered back to the reader in a review pane. A
#: routed code's PII scope withholds no phone number in the middle of a line
#: ("... staff. Home phone: <number>"), so the shape is read here.
_LINK_OR_CONTACT_RE = re.compile(rf'https?://|www\.|mailto:|HYPERLINK|\S@\S+\.\w|{_BARE_PHONE_SHAPE}',
                                 re.IGNORECASE)

#: The entry text's own line and cell separators.
_FRAGMENT_SEPARATORS = ('\t', '\n', '|')

#: A table whose every first cell ends with this is a label/value table (one
#: grant): its sub-point goes at the table's end, not after the anchor row.
_LABEL_SUFFIX = ':'

#: A mentee table's name row label (`mentoring._mentee_table_rows`), and the
#: field keys its value is read from, in `_normalize_mentee`'s order. Two
#: mentees' tables often share every other row's words ("Site/Position: PhD
#: student"), so the name row is what says whose table a row is.
_MENTEE_NAME_LABEL = 'Name:'
_MENTEE_NAME_FIELDS = ('name', 'mentee_name')

#: The fields the education writer prints in a degree's cell that
#: `_RENDERED_FIELDS` cannot list, as its probe marks only schema fields:
#: `major` or `field_of_study`, stage 4's off-schema `honors` (#817), and the
#: advisor under its two off-schema names (#1245, `ADVISOR_LABELS`). A
#: fragment restating them is the degree's row reformatted
#: (`_holds_field_values`). Literals, as `_MENTEE_NAME_FIELDS` are: importing
#: `sections.education` here imports `sections.positions`, which imports this
#: module.
_DEGREE_CELL_FIELDS = ('major', 'field_of_study', 'honors', 'major_professor', 'mentor')
#: The fields the leadership writer reads a role's role cell and its
#: institution cell from, each the first that holds text: the schema's, then
#: stage 4's off-schema names. The writer reads them from here, so the row
#: and `_value_tokens` cannot drift apart.
LEADERSHIP_ROLE_FIELDS = ('leadership_role', 'role', 'position')
LEADERSHIP_INSTITUTION_FIELDS = ('institution', 'organization')
#: The off-schema ones among them, which `_RENDERED_FIELDS` cannot list (its
#: probe marks only schema fields): `_value_tokens` adds them for O.
_LEADERSHIP_ROW_FIELDS = tuple(name for name in (*LEADERSHIP_ROLE_FIELDS,
                                                 *LEADERSHIP_INSTITUTION_FIELDS)
                               if name not in _RENDERED_FIELDS['O'])
#: The fields the administrative-activities writer reads a committee's name
#: cell from, in its one precedence (#625): the schema's, then stage 4's two
#: off-schema names. The writer reads them from here, so the row and
#: `_value_tokens` cannot drift apart. The institution it folds into that
#: cell (#985) is a schema field, which `_RENDERED_FIELDS` lists.
ADMINISTRATIVE_ACTIVITY_FIELDS = ('committee_name', 'committee', 'activity')
#: The off-schema ones among them: `_value_tokens` adds them for P.
_ADMINISTRATIVE_ROW_FIELDS = tuple(name for name in ADMINISTRATIVE_ACTIVITY_FIELDS
                                   if name not in _RENDERED_FIELDS['P'])
#: The kinds of body a record of committee service names, capitalised as a
#: name is ("Admissions Appeals Committee", "Faculty Council"). A part of a
#: committee's line naming another one is another record
#: (`_names_another_committee`), dated or not.
_COMMITTEE_BODIES = ('Committee', 'Subcommittee', 'Council', 'Board', 'Panel', 'Commission',
                     'Senate', 'Task Force', 'Taskforce', 'Working Group', 'Workgroup')
_NAME_WORD = r"[A-Z][\w'’&.-]*"
#: A committee's name as a line writes it: the capitalised words before one
#: of `_COMMITTEE_BODIES`, and those an "on", "of" or "for" adds after it
#: ("Committee on Admissions"). A word that only begins with one ("Senator",
#: "Panelist") names none.
_COMMITTEE_NAME_RE = re.compile(
    rf"(?:{_NAME_WORD}\s+)*(?:{'|'.join(_COMMITTEE_BODIES)})\b"
    rf"(?:\s+(?:on|of|for)\s+(?:the\s+)?{_NAME_WORD}(?:\s+{_NAME_WORD})*)?")
#: A committee name more than this share of whose distinctive words are in
#: the entry's own committee name is that committee: one the CV typed with a
#: typo, or wrapped onto the next part ("<words> Steering\tCommittee,
#: <institution>"), still shares most of its words, and another committee
#: little more than the word for its kind ("Committee").
_OWN_COMMITTEE_MIN_SHARE = 0.5
#: The fields the presentations writer reads a talk's row from, each cell's
#: in its one precedence: the Title cell's (the schema's, then stage 4's
#: off-schema name), the venue's (`institution`, then the schema's
#: `location`, then `venue`), the meeting it folds into the venue (R's own,
#: then S8's, for an abstract a hierarchy mismatch moved here, #1570), and
#: the talk's own date (the first that holds one, which an `end_date` makes
#: a range). The writer reads them from here, so the row and `_value_tokens`
#: cannot drift apart. Its `role` is a schema field, which `_RENDERED_FIELDS`
#: lists.
PRESENTATION_TITLE_FIELDS = ('title', 'presentation_title')
PRESENTATION_VENUE_FIELDS = ('institution', 'location', 'venue')
PRESENTATION_EVENT_FIELDS = ('event_name', 'conference_name')
PRESENTATION_DATE_FIELDS = ('year', 'date', 'start_date')
PRESENTATION_END_DATE_FIELD = 'end_date'
#: The off-schema ones among them: `_value_tokens` adds them for R.
_PRESENTATION_ROW_FIELDS = tuple(
    name for name in (*PRESENTATION_TITLE_FIELDS, *PRESENTATION_VENUE_FIELDS,
                      *PRESENTATION_EVENT_FIELDS, *PRESENTATION_DATE_FIELDS,
                      PRESENTATION_END_DATE_FIELD)
    if name not in _RENDERED_FIELDS['R'])
#: A part of a talk's line split before each date it holds, so a date
#: inside the part can be read as opening a record (`_opens_dated_record`),
#: as one opening the part is: a CV that writes its talks in one paragraph
#: ("2003 <venue>: <title>, <city> 2003 <venue>: <title>") reaches stage 6 as
#: one part, which stage 2's tabs never split.
_BEFORE_A_DATE_RE = re.compile(rf'\s+(?={_RECORD_DATE_MONTH}{_RECORD_YEAR})')
#: A title as a line quotes it, in curly or straight quotes.
_QUOTED_RE = re.compile(r'“([^”]+)”|"([^"]+)"')
#: A quoted phrase of at least this many distinctive words can be a title;
#: a shorter one is a word or a name the line puts in quotes ("a 'Student
#: Choice' seminar").
_QUOTED_TITLE_MIN_TOKENS = 3
#: A quoted title more than this share of whose distinctive words are the
#: talk's own title or meeting is that title, punctuated or typed
#: differently; another talk's title shares little more than a topic word.
_OWN_TITLE_MIN_SHARE = 0.5

_XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'

#: The elements a section writer recorded for an entry, by identity
#: (`RowReceiptLedger.elements_for`): its rows, or its first cells'
#: paragraphs. Read for the families that anchor only on those
#: (`_OWN_ROW_CODES`).
RowsWritten = Callable[[Mapping], Iterable[BaseOxmlElement]]


class AnchorCandidate(NamedTuple):
    """A rendered line a sub-point can go under: a body paragraph, or a table
    row read as one line (its first paragraph stands for the row).
    `position` is its top-level body element's index, for `SectionSpan`."""
    paragraph: Paragraph
    text: str
    tokens: set[str]
    position: int


class SectionSpan(NamedTuple):
    """The body element indexes a code's section runs over: from its heading
    up to, not including, the next major section's heading (`end` None: to
    the Appendix)."""
    start: int
    end: int | None

    def holds(self, candidate: AnchorCandidate) -> bool:
        return candidate.position >= self.start and (self.end is None or candidate.position < self.end)


class SubpointPlacement(NamedTuple):
    """One sub-point stage 6 wrote, as the sidecar records it
    (`SUBPOINT_PLACEMENTS_KEY`, one `_asdict()` per sub-point): the
    `element_idx_start` of the entry it was written for, which is the index
    the doctor reads stage 4's entries by (fan-out children keep it); the
    code the entry rendered as; and the paragraphs as written.

    The index is not an identity: stage-4 entries split from one source
    element share it, and their sub-points then count for each other, as
    fan-out siblings' do. The doctor matches on the index and the text only.
    The code is for a reader of the sidecar, such as a run autopsy: it names
    the table the sub-point sits in, which for a rerouted entry (a K2
    trainee rendered as N3B) is not its stage-4 code."""
    element_idx_start: object
    code: str
    paragraphs: tuple[str, ...]

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> SubpointPlacement:
        """A placement read back from its sidecar record."""
        paragraphs = record.get('paragraphs')
        return cls(record.get('element_idx_start'), str(record.get('code') or ''),
                   tuple(map(str, paragraphs)) if isinstance(paragraphs, (list, tuple)) else ())


class SubPoint(NamedTuple):
    """Prose an entry's render leaves out, and the line it goes under.
    `entry_id` is `id()` of the entry dict, for the overflow pass to skip;
    `code` is the code the entry rendered as and `element_idx_start` its
    index, for its `SubpointPlacement`."""
    anchor: AnchorCandidate
    paragraphs: tuple[str, ...]
    entry_id: int
    code: str
    element_idx_start: object

    def as_rendered_line(self) -> str:
        """The entry as a reader who rejects the deletion sees it: its line,
        then its prose. `_recover_unrendered_records` reads this, so a record
        whose prose is now a sub-point is not also sent to the Appendix."""
        return ' '.join((self.anchor.text, *self.paragraphs))


def subpoints_enabled(value: object) -> bool:
    """`SUBPOINT_FLAG_ENV`'s value read as the switch: off only for an off word
    (`_SUBPOINT_OFF_VALUES`); unset or blank is `SUBPOINT_FLAG_DEFAULT`. A
    config file can hand it an unquoted YAML int or bool, so it is read as
    text first."""
    text = str(value if value is not None else '').strip().lower() or SUBPOINT_FLAG_DEFAULT
    return text not in _SUBPOINT_OFF_VALUES


def _tokens(text: str) -> set[str]:
    return set(_RENDER_TOKEN_RE.findall(_norm(text)))


def _split_fragments(text: str) -> list[str]:
    parts = [str(text or '')]
    for separator in _FRAGMENT_SEPARATORS:
        parts = [piece for part in parts for piece in part.split(separator)]
    return [' '.join(part.split()) for part in parts if part.strip()]


def prose_fragments(text: str) -> list[str]:
    """The entry text's fragments long enough to be prose, after joining a
    paragraph its source broke at printed lines back into one
    (`_refold_shattered`). Template instructions and source boilerplate are
    never offered."""
    return [fragment for fragment in _refold_shattered(_split_fragments(text))
            if len(_tokens(fragment)) >= SUBPOINT_MIN_TOKENS
            and not _RECORD_DATE_PREFIX_RE.match(fragment)
            and not _LINK_OR_CONTACT_RE.search(fragment)
            and not is_template_instruction(fragment)
            and not is_source_boilerplate(fragment)]


def _row_candidates(table: Table, position: int) -> Iterator[AnchorCandidate]:
    for row in table.rows:
        cells = list(dict.fromkeys(row.cells))  # a merged cell repeats
        paragraphs = [p for cell in cells for p in cell.paragraphs]
        text = ' '.join(p.text for p in paragraphs)
        if text.strip():
            yield AnchorCandidate(paragraphs[0], ' '.join(text.split()), _tokens(text), position)
        for cell in cells:
            for nested in cell.tables:
                yield from _row_candidates(nested, position)


def anchor_candidates(doc: Document, stop_before: BaseOxmlElement | None = None,
                      ) -> Iterator[AnchorCandidate]:
    """Body paragraphs and table rows (nested tables included) in document
    order, up to the body element `stop_before` -- the Appendix heading,
    whose bullets repeat entry text and are no place for a sub-point. A row,
    not a cell: an appointment's title and employer sit in different cells,
    and a cell alone can share more words with another row's employer."""
    for position, element in enumerate(doc.element.body.iterchildren()):
        if element is stop_before:
            return
        if element.tag == qn('w:p'):
            paragraph = Paragraph(element, doc._body)
            if paragraph.text.strip():
                yield AnchorCandidate(paragraph, paragraph.text, _tokens(paragraph.text), position)
        elif element.tag == qn('w:tbl'):
            yield from _row_candidates(Table(element, doc._body), position)


def best_anchor(entry_tokens: set[str],
                candidates: Iterable[AnchorCandidate]) -> AnchorCandidate | None:
    """The first candidate holding the largest share of `entry_tokens`, or
    None when none reaches `SUBPOINT_ANCHOR_MIN_OVERLAP` with at least
    `SUBPOINT_ANCHOR_MIN_TOKENS` of them."""
    if len(entry_tokens) < SUBPOINT_ANCHOR_MIN_TOKENS:
        return None
    best, best_share = None, SUBPOINT_ANCHOR_MIN_OVERLAP
    for candidate in candidates:
        shared = len(entry_tokens & candidate.tokens)
        share = shared / len(entry_tokens)
        if shared >= SUBPOINT_ANCHOR_MIN_TOKENS and (
                share > best_share or (best is None and share >= best_share)):
            best, best_share = candidate, share
    return best


class _Haystack(NamedTuple):
    """The rendered document as `_record_rendered` reads it."""
    text: str
    line_tokens: list[set[str]]


def _value_tokens(entry: Mapping, code: str) -> set[str]:
    """Distinctive tokens of the entry's field values its section renders
    (`_RENDERED_FIELDS`, and for a degree `_DEGREE_CELL_FIELDS`, for a
    leadership role `_LEADERSHIP_ROW_FIELDS`, for a committee
    `_ADMINISTRATIVE_ROW_FIELDS`, for a talk `_PRESENTATION_ROW_FIELDS`), or
    of every scalar field for a code rendered from its text."""
    fields = entry.get('extracted_fields')
    if not isinstance(fields, Mapping):
        return set()
    names = _RENDERED_FIELDS.get(code) or fields.keys()
    if code in EDUCATION_CODES:
        names = (*names, *_DEGREE_CELL_FIELDS)
    elif code in LEADERSHIP_CODES:
        names = (*names, *_LEADERSHIP_ROW_FIELDS)
    elif code in ADMINISTRATIVE_CODES:
        names = (*names, *_ADMINISTRATIVE_ROW_FIELDS)
    elif code in PRESENTATION_CODES:
        names = (*names, *_PRESENTATION_ROW_FIELDS)
    values = [fields.get(name) for name in names]
    return set().union(*(_tokens(str(value)) for value in values
                         if isinstance(value, (str, int, float)) and value))


def _holds_field_values(fragment: str, value_tokens: set[str]) -> bool:
    tokens = _tokens(fragment)
    return len(tokens & value_tokens) >= SUBPOINT_FIELD_TOKEN_MAX_SHARE * len(tokens)


def unrendered_prose(entry: Mapping, code: str, haystack: _Haystack,
                     text: str | None = None) -> list[str]:
    """The prose fragments of `text` (by default the entry's own) that hold
    none of the entry's rendered field values and that the document provably
    does not contain."""
    value_tokens = _value_tokens(entry, code)
    return [fragment for fragment in prose_fragments(entry.get('text') or '' if text is None else text)
            if not _holds_field_values(fragment, value_tokens)
            and _record_rendered(fragment, haystack.text, haystack.line_tokens) is False]


def _in_tables(anchor: AnchorCandidate, tables: Collection[BaseOxmlElement]) -> bool:
    # `is`, not `in`: the caller holds each table's proxy, so lxml hands back
    # that same object for the ancestor; `id()` of a dropped proxy is not
    # identity.
    return any(ancestor is table for ancestor in anchor.paragraph._p.iterancestors(qn('w:tbl'))
               for table in tables)


def _section_candidates(code: str, candidates: list[AnchorCandidate],
                        spans: Mapping[str, SectionSpan]) -> list[AnchorCandidate]:
    """The candidates in `code`'s section, or for a mentee code in either
    mentee heading's: the section files a mentee by its dates, not its code."""
    codes = MENTEE_CODES if code in MENTEE_CODES else {code}
    held = [spans[c] for c in codes if c in spans]
    return [c for c in candidates if any(span.holds(c) for span in held)]


def _mentee_name(entry: Mapping) -> str:
    """The entry's mentee name (`_MENTEE_NAME_FIELDS`), case-folded and its
    spacing collapsed, as a name row is compared; '' when it names none."""
    fields = entry.get('extracted_fields')
    if not isinstance(fields, Mapping):
        return ''
    name = next((fields[key] for key in _MENTEE_NAME_FIELDS if fields.get(key)), '')
    return ' '.join(str(name).split()).casefold()


def _own_mentee_rows(entry: Mapping, candidates: list[AnchorCandidate]) -> list[AnchorCandidate]:
    """The candidates that are rows of a table whose name row names the
    entry's mentee (`_MENTEE_NAME_LABEL`): none when the entry names no
    mentee or no table names it."""
    name = _mentee_name(entry)
    if not name:
        return []
    name_row = f'{_MENTEE_NAME_LABEL} {name}'.casefold()
    tables = [table for c in candidates if c.text.casefold() == name_row
              for table in c.paragraph._p.iterancestors(qn('w:tbl'))]
    return [c for c in candidates if _in_tables(c, tables)]


def _own_segment(entry: Mapping) -> str | None:
    """The entry's own text, except for the last of stage 4's records
    (`LAST_STAGE4_RECORD`). That one keeps its source line's whole text, the
    other records in it, though each of them renders on its own. It reads its
    own tab segment when the line's segments pair one to one with its records,
    as fan-out pairs the others, and is None when they do not: its family's
    rule decides (`_own_mentee_line`, `_own_record_line`)."""
    text = str(entry.get('text') or '')
    marker = entry.get(LAST_STAGE4_RECORD)
    if not isinstance(marker, Mapping):
        return text
    segments = _segments(text)
    return segments[-1] if len(segments) == marker.get('count') else None


def _own_mentee_line(entry: Mapping, entries_by_code: Mapping[str, list[Mapping]]) -> str:
    """The text a mentee's prose is read from (`_own_segment`). The last of a
    line's records whose segments do not pair with them (YUYVIG XNWSZN 188:
    twelve postdocs on one line, offered under the twelfth) reads the whole
    line when every record names its mentee (one mentee's two periods, YUYVIG
    ECXGAT 1479), and nothing otherwise, as which mentee a word is about
    cannot be told."""
    own = _own_segment(entry)
    if own is not None:
        return own
    others = [other for group in entries_by_code.values() for other in group
              if _is_sibling_of(other, entry)]
    text = str(entry.get('text') or '')
    return text if all(_mentee_name(other) == _mentee_name(entry) for other in others) else ''


def _row_of(element: BaseOxmlElement) -> BaseOxmlElement | None:
    """`element` when it is a table row, else the nearest row holding it."""
    return element if element.tag == qn('w:tr') else next(element.iterancestors(qn('w:tr')), None)


def _names_degree(entry: Mapping) -> bool:
    """Whether stage 4's `degree` for the entry holds any text."""
    fields = entry.get('extracted_fields')
    return isinstance(fields, Mapping) and bool(str(fields.get('degree') or '').strip())


def _own_rows(entry: Mapping, candidates: list[AnchorCandidate],
              rows_written: RowsWritten | None) -> list[AnchorCandidate]:
    """The candidates that are a row a writer recorded for `entry` itself
    (`rows_written`, the row-receipt ledger): none when it recorded none, or
    with no ledger. Words cannot tell apart two rows that share every
    distinctive word: two degrees of one institution ("Ph.D." and "M.S."
    hold no distinctive token, nor does a year; YUYVIG FMIGLR 19, an M.S.
    thesis under the Ph.D. row), or two leadership roles of one title and
    institution held in different years. Nor can they see that a section
    skipped an entry, which leaves that entry no row."""
    if rows_written is None:
        return []
    rows = [row for row in map(_row_of, rows_written(entry)) if row is not None]
    return [c for c in candidates if any(_row_of(c.paragraph._p) is row for row in rows)]


def _own_degree_rows(entry: Mapping, candidates: list[AnchorCandidate],
                     rows_written: RowsWritten | None) -> list[AnchorCandidate]:
    """A degree's own rows (`_own_rows`): the education section skips an
    entry naming no degree and no institution.

    An entry naming no degree (`_names_degree`) has none either. Its row is
    an institution or a field alone, and its prose is of no degree there: a
    thesis citation stage 4 coded B1, whose master's degree renders as a row
    of its own (EBYSBC VGHNZD 1861)."""
    return _own_rows(entry, candidates, rows_written) if _names_degree(entry) else []


def _holds_dated_records(text: str) -> bool:
    """Whether `DATE_LED_MIN_RECORDS` or more of `text`'s parts each open a
    dated record (`_opens_dated_record`, #983): one source line holding two
    roles or two degrees, each with its own dates, of which stage 4
    extracted one."""
    return sum(map(_opens_dated_record, _split_fragments(text))) >= DATE_LED_MIN_RECORDS


def _own_record_line(entry: Mapping) -> str:
    """The text a degree's or a leadership role's prose is read from
    (`_own_segment`), and nothing for the last of a line's records whose
    segments do not pair with them, or for a line holding several dated
    records (`_holds_dated_records`): which degree a thesis is of, or which
    role a line is about, cannot be told, and the other record's prose would
    go under the entry's row."""
    own = _own_segment(entry)
    return '' if own is None or _holds_dated_records(own) else own


def _committee_records(entry: Mapping) -> list[object]:
    """The records the entry's committee fields hold, as the writer reads
    them: the list stage 4 packed under the first of
    `ADMINISTRATIVE_ACTIVITY_FIELDS` that holds one (#208/#248), else the
    fields themselves, one record."""
    fields = entry.get('extracted_fields')
    fields = fields if isinstance(fields, Mapping) else {}
    listed = next((value for name in ADMINISTRATIVE_ACTIVITY_FIELDS
                   if isinstance(value := fields.get(name), list)), None)
    return listed or [fields]


def _committee_name_tokens(record: object) -> set[str]:
    """The distinctive tokens of the record's committee name, its
    `ADMINISTRATIVE_ACTIVITY_FIELDS` text; none for a record that is not a
    mapping (a bare name in a list)."""
    if not isinstance(record, Mapping):
        return set()
    return _tokens(' '.join(str(record.get(name) or '') for name in ADMINISTRATIVE_ACTIVITY_FIELDS))


def _names_another_committee(part: str, own_name: set[str]) -> bool:
    """Whether a part of a committee's line names another committee
    (`_COMMITTEE_NAME_RE`) than the entry's own (`own_name`,
    `_committee_name_tokens`): one no more than `_OWN_COMMITTEE_MIN_SHARE` of
    whose words are the entry's. A part holding every word of the entry's
    own name is its record, and a sub-committee it names one of its details.
    With no name to hold, whose a named committee is cannot be shown."""
    if own_name and own_name <= _tokens(part):
        return False
    for match in _COMMITTEE_NAME_RE.finditer(part):
        named = _tokens(match.group())
        if len(named & own_name) <= _OWN_COMMITTEE_MIN_SHARE * len(named):
            return True
    return False


def _own_committee_line(entry: Mapping) -> str:
    """The text a committee's prose is read from (`_own_record_line`), and
    nothing when it holds another record than the one the writer prints
    for it, as that record's text would be offered as the committee's prose,
    under its row:

    - its parts hold several dated records by the writer's own #660 test
      (`_looks_like_multiple_records`), read per tab, line and pipe part
      rather than per line. Stage 2 joins a list's paragraphs with tabs, so
      committees each followed by their years reach stage 6 as one line,
      which the writer prints as one row, of the record stage 4 extracted
      (EBYSBC YOXXOH 74: six committees, two of them offered under the
      sixth's row);
    - a part names another committee (`_names_another_committee`), dated or
      not: an undated sibling, or one dated sibling next to a committee
      whose own years are in its fields only, which that test reads as one
      record;
    - stage 4 packed several records in a list (`_committee_records`,
      #208/#248). The writer prints a row for each one naming a committee,
      and `_own_committee_row` then sees several, but none for one naming
      none (#256): the entry has one row though it holds two.

    A sibling record in the same part as the committee's own ("<other>,
    2001-2004; <own>, 2008-2012") is not seen."""
    own = _own_record_line(entry)
    records = _committee_records(entry)
    parts = _split_fragments(own)
    if len(records) > 1 or _looks_like_multiple_records(parts):
        return ''
    own_name = _committee_name_tokens(records[0])
    return '' if any(_names_another_committee(part, own_name) for part in parts) else own


def _own_committee_row(entry: Mapping, candidates: list[AnchorCandidate],
                       rows_written: RowsWritten | None) -> list[AnchorCandidate]:
    """A committee's own row (`_own_rows`) when the writer recorded exactly
    one for it. Several are several records the entry held -- a list of
    records under its committee name (#208/#248) or a block of dated lines
    the writer re-parsed one row per line (#660, `_multiline_committee_rows`)
    -- and which of them a prose line is about cannot be shown."""
    rows = _own_rows(entry, candidates, rows_written)
    return rows if len(rows) == 1 else []


def _talk_records(line: str) -> int:
    """How many talks the dates of a talk's line mark: its tab, line and pipe
    parts the writer-side #660 test reads as dated
    (`_line_carries_record_date`, a list of talks each ending in its date),
    or the pieces a date opens (`_opens_dated_record`, read before each date
    of a part, `_BEFORE_A_DATE_RE`), whichever is more."""
    parts = _split_fragments(line)
    dated_parts = sum(map(_line_carries_record_date, parts))
    opened = sum(_opens_dated_record(piece) for part in parts
                 for piece in _BEFORE_A_DATE_RE.split(part))
    return max(dated_parts, opened)


def _talk_dates(fields: Mapping, code: str) -> int:
    """How many dates the talk's Dates cell prints, as the presentations
    writer builds it (`_talk_date_cell`): the talk's own date
    (`PRESENTATION_DATE_FIELDS`), or each of a list of them under one key
    (`format_date_list`), and each further span stage 4 kept
    (`further_date_spans`)."""
    own = next((fields[name] for name in PRESENTATION_DATE_FIELDS if fields.get(name)), None)
    listed = format_date_list(own, code) if own is not None else ''
    own_dates = len(listed.split(DATE_SPAN_SEPARATOR)) if listed else int(own is not None)
    return own_dates + len(further_date_spans(fields, code))


def _quotes_another_talk(line: str, fields: Mapping) -> bool:
    """Whether the line quotes a title (`_QUOTED_RE`) that is not the talk's
    own: one no more than `_OWN_TITLE_MIN_SHARE` of whose distinctive words
    are in its title or meeting fields. With no title or meeting to hold,
    whose a quoted title is cannot be shown."""
    own = _tokens(' '.join(str(fields.get(name) or '') for name in
                           (*PRESENTATION_TITLE_FIELDS, *PRESENTATION_EVENT_FIELDS)))
    for match in _QUOTED_RE.finditer(line):
        quoted = _tokens(match.group(1) or match.group(2))
        if (len(quoted) >= _QUOTED_TITLE_MIN_TOKENS
                and len(quoted & own) <= _OWN_TITLE_MIN_SHARE * len(quoted)):
            return True
    return False


def _lists_talks(fields: Mapping) -> bool:
    """Whether stage 4 listed talks under one of the entry's fields: a list
    holding a record that names a title, a meeting or a venue (R's
    `additional_presentations`, which fan-out keeps whole when the text holds
    words no record does, #983). The writer prints the entry's own fields
    only (`_LIST_LOST_WHEN_KEPT_WHOLE`). A list of date spans names none."""
    names = (*PRESENTATION_TITLE_FIELDS, *PRESENTATION_EVENT_FIELDS, *PRESENTATION_VENUE_FIELDS)
    return any(isinstance(item, Mapping) and any(item.get(name) for name in names)
               for value in fields.values() if isinstance(value, list) for item in value)


def _own_talk_line(entry: Mapping, code: str) -> str:
    """The text a talk's prose is read from (`_own_segment`: the last of
    stage 4's records reads its own segment, and nothing when the segments
    do not pair with them), and nothing when it holds another talk than the
    one the writer prints for it, as that talk's text would be offered as
    this one's prose, under its row:

    - its dates mark more talks than its row prints dates (`_talk_records`
      against `_talk_dates`): talks each with its own date, joined by tabs or
      written in one paragraph (EBYSBC BMAMWE 1440: three talks of one year,
      offered under the first one's row). One talk given on several dates or
      at several venues is one record when stage 4 kept those dates, as the
      Dates cell then prints each of them, so the parts' dates are read
      against that cell rather than by `_own_record_line`'s count alone;
    - it quotes another title than the talk's own (`_quotes_another_talk`);
    - stage 4 listed several talks under one entry (`_lists_talks`).

    A talk the line names with no date and no quotes ("<title A>, <venue>.
    <title B>, <venue>, 2012") is not seen."""
    own = _own_segment(entry)
    fields = entry.get('extracted_fields')
    fields = fields if isinstance(fields, Mapping) else {}
    if (own is None
            or _talk_records(own) >= max(DATE_LED_MIN_RECORDS, _talk_dates(fields, code) + 1)
            or _quotes_another_talk(own, fields) or _lists_talks(fields)):
        return ''
    return own


def _prose_text(code: str, entry: Mapping,
                entries_by_code: Mapping[str, list[Mapping]]) -> str | None:
    """The text `code`'s rule reads the entry's prose from; None for its own."""
    if code in MENTEE_CODES:
        return _own_mentee_line(entry, entries_by_code)
    if code in ADMINISTRATIVE_CODES:
        return _own_committee_line(entry)
    if code in PRESENTATION_CODES:
        return _own_talk_line(entry, code)
    if code in _OWN_ROW_CODES:
        return _own_record_line(entry)
    return None


def _anchor_scope(code: str, entry: Mapping, in_section: list[AnchorCandidate],
                  rows_written: RowsWritten | None) -> list[AnchorCandidate]:
    """The candidates of the entry's section that `code`'s rule lets it
    anchor under."""
    if code in MENTEE_CODES:
        return _own_mentee_rows(entry, in_section)
    if code in EDUCATION_CODES:
        return _own_degree_rows(entry, in_section, rows_written)
    if code in LEADERSHIP_CODES:
        return _own_rows(entry, in_section, rows_written)
    if code in ADMINISTRATIVE_CODES:
        return _own_committee_row(entry, in_section, rows_written)
    if code in PRESENTATION_CODES:
        return _own_rows(entry, in_section, rows_written)
    return in_section


def _pick_anchor(code: str, entry_tokens: set[str],
                 scope: list[AnchorCandidate]) -> AnchorCandidate | None:
    """The line of `scope` the entry's prose goes under. A degree's, a
    leadership role's, a committee's or a talk's one own row (`_own_rows`) is
    taken as it is: the writer's record already says whose it is, and a
    short row ("PhD", "Kell University", "2010"; a "Dean" of an acronym)
    holds too few distinctive words for `best_anchor`, as does a degree's
    row whose stage-5b institution name shares few words with the field. Any
    other scope is read by words."""
    if code in _OWN_ROW_CODES and len(scope) == 1:
        return scope[0]
    return best_anchor(entry_tokens, scope)


def _withholds_prose(entry: Mapping) -> bool:
    """Whether the entry's prose is kept off the page as protected data. The
    PII pass touched the entry (`PII_WITHHELD_KEY`): it cut a fragment of its
    text, or dropped a field named for protected data ("spouse_name") or one
    holding it (#892). What is left can still name the withheld item, which
    is why the honors writer renders no row for an award it emptied, and its
    words could anchor it under another record's line. Or its text holds a
    fragment the full Personal Data / Appendix scope withholds
    (`_pii_fragments`), as `_split_date_led_siblings` reads it: a routed
    code's scope cuts only the rows unambiguous as a label, enough while its
    section prints only fields, but a sub-point prints text no section did,
    as the Appendix does."""
    return bool(entry.get(PII_WITHHELD_KEY)) or bool(_pii_fragments(str(entry.get('text') or '')))


def plan_subpoints(entries_by_code: Mapping[str, list[Mapping]], haystack: _Haystack,
                   candidates: list[AnchorCandidate],
                   spans: Mapping[str, SectionSpan],
                   excluded_tables: Collection[BaseOxmlElement] = (),
                   rows_written: RowsWritten | None = None) -> list[SubPoint]:
    """One `SubPoint` per entry of a `SUBPOINT_CODES` code that has unrendered
    prose and a rendered line in its own section (`spans`; a code with no
    span is not placed) to hang it under. A fragment is offered once, under
    the first entry that carries it (fan-out children and dedup survivors
    share their parent's text). A mentee's prose is read from its own line
    (`_own_mentee_line`), and its line must be a row of its own table
    (`_own_mentee_rows`), under either mentee heading. A degree's or a
    leadership role's prose is read from its own line (`_own_record_line`),
    and its line must be a row its writer recorded for it (`rows_written`,
    `_own_rows`), which it takes with no word test when it is the only one
    (`_pick_anchor`). A committee's is read the same way, and none is
    offered when its line holds another record than the one its row prints
    (`_own_committee_line`) or its writer recorded it several rows
    (`_own_committee_row`). A talk's is read the same way, and none is
    offered when its line holds another talk (`_own_talk_line`).

    An entry whose line is a row of an `excluded_tables` table gets none: it
    rendered as an excluded code (a Q2 entry rerouted to Journal Reviewing
    still carries Q2), so what rendered decides, not the code. Its prose is
    not offered under any later entry either.

    Nor does an entry holding protected data (`_withholds_prose`)."""
    planned, offered = [], set()
    for code in sorted(entries_by_code):
        if code not in SUBPOINT_CODES or code not in spans:
            continue
        in_section = _section_candidates(code, candidates, spans)
        for entry in entries_by_code[code]:
            text = _prose_text(code, entry, entries_by_code)
            fragments = [f for f in unrendered_prose(entry, code, haystack, text) if f not in offered]
            if not fragments or _withholds_prose(entry):
                continue
            anchor_tokens = _value_tokens(entry, code) or (
                _tokens(entry.get('text') or '') - set().union(*(_tokens(f) for f in fragments)))
            anchor = _pick_anchor(code, anchor_tokens,
                                  _anchor_scope(code, entry, in_section, rows_written))
            if anchor is None:
                continue
            offered.update(fragments)
            if excluded_tables and _in_tables(anchor, excluded_tables):
                continue
            planned.append(SubPoint(anchor, tuple(fragments), id(entry), code,
                                    entry.get('element_idx_start')))
    return planned


def rendered_haystack(lines: list[str], doc: Document) -> _Haystack:
    """`_Haystack` over the generator's rendered lines, built the way
    `_recover_unrendered_records` builds its own, plus one token set per
    label/value table: a grant renders one fact per row, so a line naming
    the grant's funder, number and role is rendered though no single row
    holds most of its tokens (YUYVIG DYLJXC: a "<funder> award (<number>),
    <role> <years>" line, rendered as its own grant table)."""
    token_sets = [_tokens(line) for line in lines]
    token_sets += [_tokens(' '.join(t.text or '' for t in tbl.iter(qn('w:t'))))
                   for tbl in doc.element.body.iter(qn('w:tbl')) if _is_label_table(tbl)]
    return _Haystack("\x00".join(_squash(line) for line in lines), token_sets)


# --- writing -----------------------------------------------------------------

class Revision:
    """Hands out the w:id, author and date every tracked change carries."""

    def __init__(self, next_id: int, date: str) -> None:
        self.next_id = next_id
        self.date = date

    def element(self, tag: str) -> BaseOxmlElement:
        elem = OxmlElement(tag)
        elem.set(qn('w:id'), str(self.next_id))
        elem.set(qn('w:author'), SUBPOINT_AUTHOR)
        elem.set(qn('w:date'), self.date)
        self.next_id += 1
        return elem


def _deleted_mark_ppr(ppr: BaseOxmlElement | None, revision: Revision) -> BaseOxmlElement:
    """A copy of `ppr` (or a bare w:pPr) whose paragraph mark is a tracked deletion."""
    ppr = copy.deepcopy(ppr) if ppr is not None else OxmlElement('w:pPr')
    for old in ppr.findall(qn('w:rPr')):
        ppr.remove(old)
    mark = OxmlElement('w:rPr')
    mark.append(revision.element('w:del'))
    ppr.append(mark)
    return ppr


def _deleted_paragraph(text: str, revision: Revision,
                       ppr: BaseOxmlElement | None = None) -> BaseOxmlElement:
    """A w:p whose paragraph mark and one run are tracked deletions, so
    accepting it removes the whole paragraph."""
    p = OxmlElement('w:p')
    p.append(_deleted_mark_ppr(ppr, revision))
    deletion = revision.element('w:del')
    run = OxmlElement('w:r')
    rpr = OxmlElement('w:rPr')
    fonts = OxmlElement('w:rFonts')
    fonts.set(qn('w:ascii'), 'Arial')
    fonts.set(qn('w:hAnsi'), 'Arial')
    rpr.append(fonts)
    size = OxmlElement('w:sz')
    size.set(qn('w:val'), '22')  # 11pt, as `_add_track_change_deletion`
    rpr.append(size)
    run.append(rpr)
    del_text = OxmlElement('w:delText')
    del_text.text = text
    del_text.set(_XML_SPACE, 'preserve')
    run.append(del_text)
    deletion.append(run)
    p.append(deletion)
    return p


def _is_subpoint(elem: BaseOxmlElement | None) -> bool:
    """A paragraph or row this module wrote: its mark, row or run is deleted
    by `SUBPOINT_AUTHOR`."""
    if elem is None:
        return False
    if elem.tag == qn('w:p'):
        marks = [elem.find(f"{qn('w:pPr')}/{qn('w:rPr')}/{qn('w:del')}"), elem.find(qn('w:del'))]
    elif elem.tag == qn('w:tr'):
        marks = [elem.find(f"{qn('w:trPr')}/{qn('w:del')}")]
    else:
        return False
    return any(mark is not None and mark.get(qn('w:author')) == SUBPOINT_AUTHOR
               for mark in marks)


def _after_subpoints(elem: BaseOxmlElement) -> BaseOxmlElement:
    """`elem`, or the last sub-point written straight after it, so several
    sub-points under one anchor keep their order."""
    while _is_subpoint(elem.getnext()):
        elem = elem.getnext()
    return elem


def _has_deleted_mark(p: BaseOxmlElement) -> bool:
    return p.find(f"{qn('w:pPr')}/{qn('w:rPr')}/{qn('w:del')}") is not None


def _keep_mark(p: BaseOxmlElement) -> None:
    """Undelete `p`'s mark and drop its numbering. Accepting a deleted mark
    merges the paragraph into the next one; with a table or the section
    properties next there is none, so the last sub-point keeps its mark and
    accepting leaves one empty plain paragraph, never an empty bullet."""
    ppr = p.find(qn('w:pPr'))
    for tag in ('w:rPr', 'w:numPr'):
        for old in ppr.findall(qn(tag)):
            ppr.remove(old)


def _deepen_plain_entry(ppr: BaseOxmlElement) -> None:
    """One level deeper for an entry written without a bullet (#1943: K and
    L), which shows its level by a left indent on the bullet-text grid
    rather than `w:ilvl`. Any other indent is left as it is."""
    ind = ppr.find(qn('w:ind'))
    left = None if ind is None else ind.get(qn('w:left'))
    level = plain_entry_level(int(left)) if left is not None and left.isdigit() else None
    if level is not None:
        ind.set(qn('w:left'), str(plain_entry_left_twips(level + 1)))


def _insert_after_paragraph(anchor: Paragraph, texts: tuple[str, ...],
                            revision: Revision) -> None:
    """Deleted paragraphs after `anchor`, with its paragraph properties; a
    list paragraph goes one level deeper, so it reads as a sub-bullet, and so
    does an unbulleted entry (`_deepen_plain_entry`). The last one keeps its
    mark when no paragraph follows it (`_keep_mark`)."""
    ppr = copy.deepcopy(anchor._p.pPr) if anchor._p.pPr is not None else None
    if ppr is not None:
        level = ppr.find(f"{qn('w:numPr')}/{qn('w:ilvl')}")
        if level is not None:
            level.set(qn('w:val'), str(int(level.get(qn('w:val'), '0')) + 1))
        else:
            _deepen_plain_entry(ppr)
    after = _after_subpoints(anchor._p)
    if after is not anchor._p and not _has_deleted_mark(after):
        # An earlier sub-point kept its mark; one now follows it, so delete it again.
        after.replace(after.find(qn('w:pPr')), _deleted_mark_ppr(ppr, revision))
    for text in texts:
        p = _deleted_paragraph(text, revision, ppr)
        after.addnext(p)
        after = p
    following = after.getnext()
    if following is None or following.tag != qn('w:p'):
        _keep_mark(after)


def _first_cell_text(row: BaseOxmlElement) -> str:
    cell = row.find(qn('w:tc'))
    return '' if cell is None else ''.join(t.text or '' for t in cell.iter(qn('w:t')))


def _is_label_table(tbl: BaseOxmlElement) -> bool:
    """Every row (sub-points aside) opens with a "Label:" cell: one grant."""
    rows = [row for row in tbl.findall(qn('w:tr')) if not _is_subpoint(row)]
    return bool(rows) and all(_first_cell_text(row).strip().endswith(_LABEL_SUFFIX)
                              for row in rows)


def _grid_columns(tbl: BaseOxmlElement) -> int:
    grid = tbl.find(qn('w:tblGrid'))
    return max(1, len(grid.findall(qn('w:gridCol'))) if grid is not None else 1)


def _insert_row_after(row: BaseOxmlElement, texts: tuple[str, ...],
                      revision: Revision) -> None:
    """A deleted row spanning the table after `row`: accepting removes it."""
    tbl = row.getparent()
    if _is_label_table(tbl):
        row = tbl.findall(qn('w:tr'))[-1]
    tr = OxmlElement('w:tr')
    tr_pr = OxmlElement('w:trPr')
    tr_pr.append(revision.element('w:del'))
    tr.append(tr_pr)
    tc = OxmlElement('w:tc')
    tc_pr = OxmlElement('w:tcPr')
    span = OxmlElement('w:gridSpan')
    span.set(qn('w:val'), str(_grid_columns(tbl)))
    tc_pr.append(span)
    tc.append(tc_pr)
    for text in texts:
        tc.append(_deleted_paragraph(text, revision))
    tr.append(tc)
    _after_subpoints(row).addnext(tr)


def write_row_subpoint(row: BaseOxmlElement, texts: tuple[str, ...],
                       revision: Revision) -> str:
    """A deleted row spanning the table after `row`, for a writer that already
    holds its entry's row: the duties #1641 splits off an appointment title.
    Returns the entry as a reader who rejects the deletion sees it, the line
    `SubPoint.as_rendered_line` gives for a planned sub-point."""
    _insert_row_after(row, texts, revision)
    cells = (''.join(t.text or '' for t in tc.iter(qn('w:t'))) for tc in row.iter(qn('w:tc')))
    return ' '.join((*(' '.join(cell.split()) for cell in cells if cell.strip()), *texts))


def write_subpoint(subpoint: SubPoint, revision: Revision) -> None:
    """Write one `SubPoint` under its anchor (module docstring)."""
    anchor = subpoint.anchor.paragraph
    parent = anchor._p.getparent()
    if parent is not None and parent.tag == qn('w:tc'):
        _insert_row_after(parent.getparent(), subpoint.paragraphs, revision)
    else:
        _insert_after_paragraph(anchor, subpoint.paragraphs, revision)
