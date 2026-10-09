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

- a body paragraph (a teaching bullet): a paragraph after it, one list level
  deeper, whose run AND paragraph mark are deleted, so accepting leaves no
  empty bullet;
- a table row (an appointment, a grant table, a service row): a row
  spanning the table, marked as a deleted row, after the entry's row -- or,
  for a label/value table holding one grant, at the table's end. Accepting
  removes the row.

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
still reports a value stage 4 misfiled, as INFO with a note. The render gate
compares arms; its XML fingerprint sees every sub-point as a change.

Codes: K1-K5 and M2A-M2D first, where loss is highest (#1205 body), plus
D1-D3 duties (owner decision 2026-10-08, lifting the D exclusion for this
variant). Then one code family at a time, each the one a stage-6 replay
measured losing the most prose of those left: Q1-Q2 extramural service next
(the remit, the meeting or the session title behind a committee or panel
row). The decision's exclusions (A, T, M1, E/G/J/N1/N4/S0/L1/L2, F1, Q4D,
B2) are not in `SUBPOINT_CODES`, so nothing else is touched.
"""
from __future__ import annotations

import copy
import re
from collections.abc import Iterable, Iterator, Mapping
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

from .fan_out import _RENDERED_FIELDS
from .normalization import _squash
from .parsing import _refold_shattered
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
#: sub-point is a deleted row under the entry's row. Q4D stays excluded.
SERVICE_CODES = frozenset({'Q1', 'Q2'})
#: The codes whose prose is offered. An allowlist, not the decision's
#: exclusion list: codes join one family at a time, as each is measured.
SUBPOINT_CODES = TEACHING_CODES | GRANT_CODES | APPOINTMENT_CODES | SERVICE_CODES

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

#: A link or an e-mail address -- a contact line fused into a position entry,
#: a webinar's tracking URL (EBYSBC) -- is not prose, and an address is not
#: offered back to the reader in a review pane.
_LINK_OR_ADDRESS_RE = re.compile(r'https?://|www\.|mailto:|HYPERLINK|\S@\S+\.\w', re.IGNORECASE)

#: The entry text's own line and cell separators.
_FRAGMENT_SEPARATORS = ('\t', '\n', '|')

#: A table whose every first cell ends with this is a label/value table (one
#: grant): its sub-point goes at the table's end, not after the anchor row.
_LABEL_SUFFIX = ':'

_XML_SPACE = '{http://www.w3.org/XML/1998/namespace}space'


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


class SubPoint(NamedTuple):
    """Prose an entry's render leaves out, and the line it goes under.
    `entry_id` is `id()` of the entry dict, for the overflow pass to skip."""
    anchor: AnchorCandidate
    paragraphs: tuple[str, ...]
    entry_id: int

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
            and not _LINK_OR_ADDRESS_RE.search(fragment)
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
    (`_RENDERED_FIELDS`), or of every scalar field for a code rendered from
    its text."""
    fields = entry.get('extracted_fields')
    if not isinstance(fields, Mapping):
        return set()
    names = _RENDERED_FIELDS.get(code) or fields.keys()
    values = [fields.get(name) for name in names]
    return set().union(*(_tokens(str(value)) for value in values
                         if isinstance(value, (str, int, float)) and value))


def _holds_field_values(fragment: str, value_tokens: set[str]) -> bool:
    tokens = _tokens(fragment)
    return len(tokens & value_tokens) >= SUBPOINT_FIELD_TOKEN_MAX_SHARE * len(tokens)


def unrendered_prose(entry: Mapping, code: str, haystack: _Haystack) -> list[str]:
    """The entry's prose fragments that hold no rendered field value and that
    the document provably does not contain."""
    value_tokens = _value_tokens(entry, code)
    return [fragment for fragment in prose_fragments(entry.get('text') or '')
            if not _holds_field_values(fragment, value_tokens)
            and _record_rendered(fragment, haystack.text, haystack.line_tokens) is False]


def plan_subpoints(entries_by_code: Mapping[str, list[Mapping]], haystack: _Haystack,
                   candidates: list[AnchorCandidate],
                   spans: Mapping[str, SectionSpan]) -> list[SubPoint]:
    """One `SubPoint` per entry of a `SUBPOINT_CODES` code that has unrendered
    prose and a rendered line in its own section (`spans`; a code with no
    span is not placed) to hang it under. A fragment is offered once, under
    the first entry that carries it (fan-out children and dedup survivors
    share their parent's text)."""
    planned, offered = [], set()
    for code in sorted(entries_by_code):
        if code not in SUBPOINT_CODES or code not in spans:
            continue
        in_section = [c for c in candidates if spans[code].holds(c)]
        for entry in entries_by_code[code]:
            fragments = [f for f in unrendered_prose(entry, code, haystack) if f not in offered]
            if not fragments:
                continue
            anchor_tokens = _value_tokens(entry, code) or (
                _tokens(entry.get('text') or '') - set().union(*(_tokens(f) for f in fragments)))
            anchor = best_anchor(anchor_tokens, in_section)
            if anchor is None:
                continue
            offered.update(fragments)
            planned.append(SubPoint(anchor, tuple(fragments), id(entry)))
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


def _insert_after_paragraph(anchor: Paragraph, texts: tuple[str, ...],
                            revision: Revision) -> None:
    """Deleted paragraphs after `anchor`, with its paragraph properties; a
    list paragraph goes one level deeper, so it reads as a sub-bullet. The
    last one keeps its mark when no paragraph follows it (`_keep_mark`)."""
    ppr = copy.deepcopy(anchor._p.pPr) if anchor._p.pPr is not None else None
    if ppr is not None:
        level = ppr.find(f"{qn('w:numPr')}/{qn('w:ilvl')}")
        if level is not None:
            level.set(qn('w:val'), str(int(level.get(qn('w:val'), '0')) + 1))
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
