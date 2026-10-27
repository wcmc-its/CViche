"""One free-form K or L entry read as a card (#2019): its cells as lines in
source order, and the span of its title to bold.

Partner feedback (2026-10-26) asked for the source CV's own layout in place of
a run-on line; Paul's review of the first rendering (2026-10-27) asked for the
date inline, where the source has it, and for only the title in bold:

    • Attending Surgeon, Resident Teaching, Northfield Health, 2025-Sep 2026
      Orthopaedic Surgery Residency; clinical and operative teaching

No model writes any of it. Stage 4's title and role fields are used only as
ANCHORS: they say where in the text the entry's head is, and which span of
its first line is the title. Every line holds the text's own characters, so
every line is the source's words by construction. Dates come from the text
alone, because stage 4 stores them ISO-normalized.

The slot rule decides whether an entry is a card:

1. Head. The first anchor that starts in the leading segment (no tab, " | "
   or ": " before it, at most `MAX_WORDS_BEFORE_ANCHOR` words before it). The
   head runs from the start of the text to the anchor's end, or, when the
   anchor is not a whole segment there, on to the next separator outside
   brackets and quotes. A whole-segment date at the very start ("2020<TAB>RN,
   ICU...") is the card's date, and the head starts after it.
2. Organization. An organization field whose span follows the head across
   nothing but separator punctuation.
3. Date. The first whole-segment date after the head (and organization), not
   inside brackets.
4. Details. Everything else, one item per cell (a tab or stage 2's " | ").

A head or an organization never crosses a cell break or a column gap.
`entry_card` returns None, and the caller keeps its current rendering, when
there is no head, when the head is the whole entry (one line already), when
the details hold a second whole-segment date (a second record), when a
detail cell ending in a colon labels a sub-list, when a head with no
organization or date leads a list of peers, or when the lines' words are not
exactly the text's words. That last check is what makes "no word lost or
added" a guarantee rather than a property of the rule above.

The layout keeps the source's order (`_card_lines`): each cell is a line,
except that the cells holding the head and organization share line 1, and a
cell that is nothing but the date joins the line before it (or, leading the
text, line 1), with `CELL_JOIN`. The bold is `title_span`: the verbatim span
of a title or role field, never the words around it (a K4 presenter list).

Pure: strings in, a frozen record out; no Word object is touched (§1.2).
"""
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass

# Stage-4 fields that name an entry's title or role, tried as head anchors in
# this order (K: course/activity/program titles and roles; L: clinical and
# leadership roles, L2 project names).
HEAD_ANCHOR_FIELDS = ('course_title', 'activity_title', 'program_name', 'project_name',
                      'role', 'teaching_role', 'clinical_role', 'leadership_role')
# Stage-4 fields that name where the entry happened, tried for line 1's
# organization in this order.
ORG_ANCHOR_FIELDS = ('institution', 'location', 'unit_program', 'setting')

# How many words may precede the head's anchor ("Co-Creator and Presenter of
# <course_title>"). The scout measured 0 against 6: 0 halves K1 and K4
# coverage, because most heads are "Role of Title" or "Title Role".
MAX_WORDS_BEFORE_ANCHOR = 6
# A head longer than this is a sentence, not a title, and the entry keeps its
# old rendering (#2019 review: 78 farm cards had a bold head of 13-25 words).
MAX_HEAD_WORDS = 12
# The most separator characters between the head and its organization (", ",
# " | ", " - ").
MAX_ORG_GAP_CHARS = 4
# A head with neither organization nor date followed by more items than this
# is the first of a list of peers (a K1 row of 18 tab-separated topics), not
# a card's title; bolding it would set one topic above the rest.
MAX_UNDATED_DETAIL_ITEMS = 3
# Three spaces in a head is a column gap the reader flattened, not one title.
COLUMN_GAP = '   '
# Where one cell of the entry ends and the next begins: a tab, or the " | "
# stage 2 joins a table row's cells with. A head or an organization never
# spans one, and each detail line is one cell.
_CELL_BREAK = re.compile(r'\t| \| ')
# A detail cell ending in a colon ("Selected Initiatives:") labels the cells
# after it as its own sub-list. A card would print them as its peers, so the
# entry keeps the old rendering, which indents them.
_SUB_LIST_LABEL = re.compile(r'[^\W_][^\t|]*:[ ]*(?:\t| \| )')

# What joins two cells that share a line: the head's cell and the
# organization's, or a date cell and the line before it. The source's own
# separator there is a tab or a " | ", which no line prints.
CELL_JOIN = ', '

_WORD = re.compile(r'[^\W_]+')
_MONTH = (r'(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?'
          r'|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b\.?')
_DAY = r'\d{1,2}(?:st|nd|rd|th)?\b'
# One date token, as stage 5c's word check reads one (`_DATE_SPAN` there),
# but case-insensitive: 5c matches it against lower-cased text only.
_DATE_TOKEN = re.compile(
    rf'\b{_MONTH}(?:\s*{_DAY}(?:\s*[-–]\s*{_DAY})?)?'
    rf'|\b{_DAY}\s+{_MONTH}'
    r'|(?<!\d)\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}(?!\d)'
    r'|(?<!\d)\d{1,2}/(?:19|20)?\d{2}(?!\d)'
    r'|(?<!\d)(?:19|20)\d{2}(?:-\d{1,2}(?!\d)){0,2}(?!\d)'
    r'|\b(?:current|ongoing|present)\b', re.IGNORECASE)
# What joins two date tokens into one run ("August 2020 – June 2022",
# "September 2023, January 2024, and March 2024").
_DATE_JOIN = re.compile(r'[\s\-–—,/]*(?:to|and|&)?[\s\-–—]*', re.IGNORECASE)
# An open range's dash after its last date ("2015-", "2015 –"): part of the
# date, or the card would print a range still running as one closed year.
_OPEN_RANGE_DASH = re.compile(r'\s*[-–—]+(?=\s*(?:$|[,;|\t)]))')
# The characters a whole segment may sit after, and before.
_SEGMENT_BEFORE = frozenset(',;|\t(:.–—-')
_SEGMENT_AFTER = frozenset(',;|\t).:–—-')
# A boundary between cells of the leading segment.
_STRONG_SEPARATOR = re.compile(r'\t|\s\|\s|:\s')
_SOFT_SEPARATORS = frozenset(':;.,')
_OPENERS, _CLOSERS = '(“[', ')”]'
_SEPARATORS_ONLY = re.compile(r'[\s\W_]*')
_LEADING_SEPARATORS = re.compile(r'^[\s,;|:.–—\-]+')
_TRAILING_SEPARATORS = re.compile(r'[\s,;|:–—\-]+$')


@dataclass(frozen=True)
class EntryCard:
    """An entry's lines in source order, the span of line 1 to bold, and
    whether the slot rule found detail items beyond the head, organization
    and date (an L3 role line takes a card only without them)."""
    lines: tuple[str, ...]
    title: tuple[int, int] | None
    has_details: bool


@dataclass(frozen=True)
class _Slots:
    """Where the slot rule found the head, organization and date in the
    text, and the detail items it left: what decides whether the entry is a
    card at all."""
    head: tuple[int, int]
    org: tuple[int, int] | None
    date: tuple[int, int] | None
    details: tuple[str, ...]


def words(text: str) -> Counter:
    """The case-folded word multiset of `text`: what no card may change."""
    return Counter(word.casefold() for word in _WORD.findall(text))


def _field_value(fields: Mapping, key: str) -> str | None:
    """A field's one string value: a string, or a one-string list."""
    value = fields.get(key)
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    return value if isinstance(value, str) and value.strip() else None


def _word_bounded(text: str, start: int, end: int) -> bool:
    """No letter or digit runs on across either end of `text[start:end]`."""
    return ((start == 0 or not text[start - 1].isalnum())
            and (end == len(text) or not text[end].isalnum()))


def _verbatim_span(text: str, value: str, start: int = 0) -> tuple[int, int] | None:
    """Where `value` sits in `text` at or after `start`, character for
    character and word-bounded."""
    at = text.find(value, start)
    while at >= 0:
        if _word_bounded(text, at, at + len(value)):
            return at, at + len(value)
        at = text.find(value, at + 1)
    return None


def find_span(text: str, value: str, start: int = 0) -> tuple[int, int] | None:
    """Where `value` sits in `text` at or after `start`, word-bounded:
    exactly, else as the same words joined by any non-word characters
    (stage 4 changed the case or the spacing)."""
    value = value.strip()
    tokens = _WORD.findall(value)
    if not tokens:
        return None
    exact = _verbatim_span(text, value, start)
    if exact:
        return exact
    pattern = r'(?<![^\W_])' + r'[\W_]+'.join(map(re.escape, tokens)) + r'(?![^\W_])'
    match = re.compile(pattern, re.IGNORECASE).search(text, start)
    return match.span() if match else None


def _is_segment(text: str, start: int, end: int) -> bool:
    """`text[start:end]` is a whole segment: separators (or the ends) on both sides."""
    before, after = text[:start].rstrip(' '), text[end:].lstrip(' ')
    return ((not before or before[-1] in _SEGMENT_BEFORE)
            and (not after or after[0] in _SEGMENT_AFTER))


def _in_brackets(text: str, start: int, end: int) -> bool:
    """`text[start:end]` opens right after a "(" or closes right before a ")"."""
    return text[:start].rstrip(' ').endswith('(') or text[end:].lstrip(' ').startswith(')')


def date_runs(text: str) -> list[tuple[int, int]]:
    """Every run of date tokens in `text`, adjacent tokens merged into one,
    each with the dash of an open range it ends on."""
    runs: list[tuple[int, int]] = []
    for match in _DATE_TOKEN.finditer(text):
        if runs and _DATE_JOIN.fullmatch(text, runs[-1][1], match.start()):
            runs[-1] = (runs[-1][0], match.end())
        else:
            runs.append(match.span())
    return [_with_open_range_dash(text, run) for run in runs]


def _with_open_range_dash(text: str, run: tuple[int, int]) -> tuple[int, int]:
    """`run`, extended over the dash right after it when nothing but a
    separator or the end of `text` follows that dash."""
    dash = _OPEN_RANGE_DASH.match(text, run[1])
    return (run[0], dash.end()) if dash else run


def _segment_dates(text: str) -> list[tuple[int, int]]:
    """The date runs of `text` that are whole segments outside brackets."""
    return [(start, end) for start, end in date_runs(text)
            if _is_segment(text, start, end) and not _in_brackets(text, start, end)]


def _segment_end(text: str, start: int, scan_from: int) -> int:
    """The first separator at or after `scan_from` outside brackets and
    quotes (counting those `text[start:scan_from]` opened): a tab, " | ",
    or ':;.,' followed by a space or the end."""
    opened = text[start:scan_from]
    depth = max(0, sum(map(opened.count, _OPENERS)) - sum(map(opened.count, _CLOSERS)))
    quoted = opened.count('"') % 2 == 1
    for at in range(scan_from, len(text)):
        char = text[at]
        if char in _OPENERS:
            depth += 1
        elif char in _CLOSERS:
            depth = max(0, depth - 1)
        elif char == '"':
            quoted = not quoted
        elif depth == 0 and not quoted and _separates(text, at):
            return at
    return len(text)


def _separates(text: str, at: int) -> bool:
    """Whether the character at `at` ends a segment."""
    if _CELL_BREAK.match(text, at):
        return True
    return text[at] in _SOFT_SEPARATORS and (at + 1 == len(text) or text[at + 1] in ' \t')


def _balanced(text: str) -> bool:
    """Brackets and quotes in `text` all close."""
    return (text.count('(') == text.count(')') and text.count('[') == text.count(']')
            and text.count('“') == text.count('”') and text.count('"') % 2 == 0)


def _is_one_cell(text: str) -> bool:
    """`text` crosses no cell break and no column gap."""
    return not _CELL_BREAK.search(text) and COLUMN_GAP not in text


def _is_head(text: str) -> bool:
    """Whether `text` can be a card's bold head."""
    count = len(_WORD.findall(text))
    return (_balanced(text) and 0 < count <= MAX_HEAD_WORDS and _is_one_cell(text)
            and not _DATE_TOKEN.match(text.strip()))


def _anchors(text: str, fields: Mapping) -> list[tuple[int, int]]:
    """The spans of the head anchor fields found in `text`, in text order."""
    spans = (find_span(text, value) for key in HEAD_ANCHOR_FIELDS
             if (value := _field_value(fields, key)))
    return sorted(span for span in spans if span)


def _head(text: str, fields: Mapping, start: int) -> tuple[int, int] | None:
    """The head's span: from `start` through the first usable anchor."""
    for anchor_start, anchor_end in _anchors(text, fields):
        lead = text[start:anchor_start]
        if (anchor_start < start or _STRONG_SEPARATOR.search(lead)
                or len(_WORD.findall(lead)) > MAX_WORDS_BEFORE_ANCHOR):
            continue
        whole = anchor_start == start and _is_segment(text, anchor_start, anchor_end)
        end = anchor_end if whole else _segment_end(text, start, anchor_end)
        if _is_head(text[start:end]):
            return start, end
    return None


def _org(text: str, fields: Mapping, head_end: int) -> tuple[int, int] | None:
    """The span of an organization that follows the head across separators."""
    for key in ORG_ANCHOR_FIELDS:
        value = _field_value(fields, key)
        span = find_span(text, value, head_end) if value else None
        if (span and span[0] - head_end <= MAX_ORG_GAP_CHARS
                and _SEPARATORS_ONLY.fullmatch(text, head_end, span[0])
                and _is_segment(text, *span) and _is_one_cell(text[slice(*span)])):
            return span
    return None


def _trimmed(text: str) -> str:
    """`text` without separator punctuation at either end."""
    return _TRAILING_SEPARATORS.sub('', _LEADING_SEPARATORS.sub('', text)).strip()


def _detail_items(text: str, line_end: int, date: tuple[int, int] | None) -> list[str]:
    """The text after line 1, the date cut out, one item per cell."""
    if date and date[0] >= line_end:
        pieces = [text[line_end:date[0]], text[date[1]:]]
    else:
        pieces = [text[line_end:]]
    return [item for piece in pieces for cell in _CELL_BREAK.split(piece)
            if _WORD.search(item := _trimmed(cell))]


def _holds_second_record(items: list[str]) -> bool:
    """An item holding a date of its own as a whole segment ("Module Leader,
    Lab Sessions, 2012-2014"): a second record (a sub-role, an office), which
    the #221 recovery reads by its years, not a detail of this one."""
    return any(_segment_dates(item) for item in items)


def _leading_date(text: str, dates: list[tuple[int, int]]) -> tuple[int, int] | None:
    """The whole-segment date the text opens with, if any."""
    start = len(text) - len(text.lstrip())
    return dates[0] if dates and dates[0][0] == start else None


def _slots(text: str, fields: Mapping) -> _Slots | None:
    """The slot rule (module docstring) on `text`, or None when it is not a card."""
    dates = _segment_dates(text)
    date = _leading_date(text, dates)
    start = len(text) - len(text.lstrip())
    if date:
        start = len(text) - len(_LEADING_SEPARATORS.sub('', text[date[1]:]))
    head = _head(text, fields, start)
    if head is None:
        return None
    org = _org(text, fields, head[1])
    line_end = org[1] if org else head[1]
    date = date or next((span for span in dates if span[0] >= line_end), None)
    items = _detail_items(text, line_end, date)
    if (not (org or date or items) or _holds_second_record(items)
            or _SUB_LIST_LABEL.search(text, line_end)):
        return None
    if not (org or date) and len(items) > MAX_UNDATED_DETAIL_ITEMS:
        return None
    return _Slots(head=head, org=org, date=date, details=tuple(items))


def _cells(text: str) -> list[tuple[int, int]]:
    """The spans of `text` between its cell breaks (`_CELL_BREAK`)."""
    spans, at = [], 0
    for brk in _CELL_BREAK.finditer(text):
        spans.append((at, brk.start()))
        at = brk.end()
    spans.append((at, len(text)))
    return spans


def _trimmed_span(text: str, start: int, end: int,
                  keep: tuple[int, int] | None) -> tuple[int, int]:
    """`text[start:end]` without separator punctuation at either end, but
    never cut into `keep` (the date, whose open-range dash is part of it)."""
    cell = text[start:end]
    lead = len(cell) - len(_LEADING_SEPARATORS.sub('', cell))
    trail = _TRAILING_SEPARATORS.search(cell)
    first, last = start + lead, start + trail.start() if trail else end
    if keep and start <= keep[0] and keep[1] <= end:
        first, last = min(first, keep[0]), max(last, keep[1])
    return first, last


def _unchanged(date: str) -> str:
    """The date as the source writes it."""
    return date


def _card_lines(text: str, slots: _Slots, date_text: Callable[[str], str]) -> list[str]:
    """The card's lines in source order (module docstring), the date slot
    written as `date_text` returns it."""
    line1_end = (slots.org or slots.head)[1]
    groups: list[list[str]] = []
    for start, end in _cells(text):
        first, last = _trimmed_span(text, start, end, slots.date)
        if not _WORD.search(text, first, last):
            continue
        piece = text[first:last]
        if slots.date and first <= slots.date[0] and slots.date[1] <= last:
            piece = (text[first:slots.date[0]] + date_text(text[slice(*slots.date)])
                     + text[slots.date[1]:last])
        if start < line1_end or (groups and (first, last) == slots.date):
            groups = groups or [[]]
            groups[-1].append(piece)
        else:
            groups.append([piece])
    return [CELL_JOIN.join(group) for group in groups]


def title_span(line: str, fields: Mapping | None) -> tuple[int, int] | None:
    """The span of `line` to bold: the first title or role field, in
    `HEAD_ANCHOR_FIELDS` order (titles before roles), found in `line`
    character for character. Never a field longer than a title
    (`MAX_HEAD_WORDS`), a date, or one crossing a cell break; never the words
    around the field, such as a K4 workshop's presenter list."""
    for key in HEAD_ANCHOR_FIELDS:
        value = _field_value(fields or {}, key)
        if (not value or len(_WORD.findall(value)) > MAX_HEAD_WORDS
                or _DATE_TOKEN.match(value.strip())):
            continue
        span = _verbatim_span(line, value.strip())
        if span and _is_one_cell(line[slice(*span)]):
            return span
    return None


def entry_card(text: str, fields: Mapping | None,
               date_text: Callable[[str], str] | None = None) -> EntryCard | None:
    """`text` as a card, or None when it does not read as one (module
    docstring). `date_text` rewrites the date slot where it stands (the
    writer's `card_date`); the word check reads the source's date."""
    if not text or '\n' in text:
        return None
    slots = _slots(text, fields or {})
    if slots is None or words(' '.join(_card_lines(text, slots, _unchanged))) != words(text):
        return None
    lines = _card_lines(text, slots, date_text or _unchanged)
    return EntryCard(lines=tuple(lines), title=title_span(lines[0], fields),
                     has_details=bool(slots.details))
