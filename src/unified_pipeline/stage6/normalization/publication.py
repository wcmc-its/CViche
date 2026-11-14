"""A raw publication entry in, one resolved record out (#481, #659, PR #737).

Everything `_format_citation` used to do *before* it could render a character
happens here, once: the None guards, enrichment-over-extracted precedence,
coercion of a non-text stage-4 value, author normalization, and the stage-5d
reconciliation. The renderer receives a `ResolvedPublication` whose every
field is already the text it will print, and so no longer names a single
pipeline key -- no `extracted_fields`, no `enrichment_data`, no
`formatting_source`, no `stage_5d_llm` (round-4 review of PR #737, points
1/2/3, 8-11 and 13-15).

Why that separation is worth a module rather than a few helpers in the
renderer:

- **The precedence rule was written seven times.** `enrichment.get('pubmed_x')
  or fields.get('x')` appeared once per field, so adding a field meant
  remembering a rule that lived nowhere. It is now one expression shape
  applied in one place.
- **The type guards were per-field, and two fields had none.** Stage 4 stores
  raw LLM JSON against no schema (#442, #450, #554), so any field can arrive
  as a list or a dict. `editors` and `publisher` were guarded with
  `isinstance` after a list-shaped `editors` rendered its repr into a
  citation; `authors`, `title`, `journal` and `book_title` sat on the same
  branch with no guard at all, where a list would have raised `TypeError`
  out of `_normalize_author_names` or `.rstrip`. The fix is not four more
  `isinstance` calls: it is that a value reaches the renderer as text or not
  at all, which is what `_text`/`_scalar_text` below are for. That sentence
  was not true when it was first written: `year` was still carried across as
  `str(fields.get('year', ''))`, one bare `str()` that turned every shape --
  a list, a dict, `True`, `None` -- into its own repr and shipped it to the
  page. It is routed through `_scalar_text` as of round 5, so the claim now
  holds of every field this module returns, and the corpus effect of closing
  it is measured on `resolve_publication` below.
- **Normalization ran during rendering.** `_normalize_author_names` was
  called inside the assembly loop, so "render" was not a purely
  representational step (point 14). It runs once, here, while the record is
  being constructed.

    PublicationFields     what the renderer expects `extracted_fields` to hold
    PubMedEnrichment      what it expects `enrichment_data` to hold
    ResolvedPublication   what it actually gets: every field already text
    resolve_publication   the one function that reads raw pipeline shapes
"""
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, NamedTuple, TypedDict

from unified_pipeline.stage3b.io import GROUP_YEAR_KEY

from ..parsing.dates import _MONTH_NAME_TO_NUM, _parse_date_components
from .authors import _normalize_author_names, carry_cv_suffixes
from .citation_matching import (
    _append_missing_stage5d_values,
    _complete_stage5d_trailer,
    _insert_missing_stage5d_year,
    _restore_stage5d_owner,
)

#: The value `extracted_fields['formatting_source']` carries when stage 5d's
#: LLM wrote `formatted_citation`. It is the only source the renderer trusts
#: to have produced a whole citation; anything else means the entry is
#: assembled from its own fields (§8.2 -- the literal decided a branch and
#: appeared in the renderer, `records.py` and the tests with no name).
_STAGE_5D_FORMATTING_SOURCE = 'stage_5d_llm'
#: Stage 5's record of an article PubMed does not index (#1872): Crossref's
#: volume, issue and pages complete the trailer of 5d's citation.
_CROSSREF_MATCH_KEY = 'crossref_match'


class PublicationFields(TypedDict, total=False):
    """The `extracted_fields` keys a bibliography entry is read for.

    `total=False` because stage 4 writes whichever of its S1-S9 schema fields
    the LLM returned, and no key is guaranteed -- 12,109 of the 20,582 local
    farm entries carry no `authors` key at all. The annotations say what the
    renderer *expects*; `resolve_publication` is what enforces it, because
    nothing type-checks a `json.load` (§8.1's four incidents are all this
    shape). A key stage 4 writes and this does not list is a key the
    bibliography cannot render -- `tests/test_stage6_publication_resolution.py`
    pins the difference against `stage4/schemas.py` so it cannot grow
    silently, which is the "stage 6 drops unnamed fields" class.
    """
    authors: str
    title: str
    #: The S4 book-chapter schema writes the chapter's own title here and
    #: never writes `title` at all, so a renderer that reads only `title`
    #: renders the book and silently loses the chapter (#728). Resolved into
    #: the `title` slot below rather than given a slot of its own: it is the
    #: same thing under a schema-specific name, and a second slot would make
    #: every downstream renderer decide between them.
    chapter_title: str
    journal: str
    book_title: str
    editors: str
    publisher: str
    year: str
    #: An S8 meeting and its place. Both render on an entry with no stage-5d
    #: citation (#1570), and `location` also places a group-filled year in
    #: one that has a citation (#1810).
    conference_name: str
    location: str
    #: An S8 abstract's own number ("K-20", "W59"). Printed by the meeting
    #: trailer only when the entry's line writes it (#1570).
    abstract_number: str
    volume: str
    issue: str
    pages: str
    doi: str
    pmid: str
    pmcid: str
    target_name: str
    formatted_citation: str
    formatting_source: str


class PubMedEnrichment(TypedDict, total=False):
    """The `enrichment_data` keys that outrank their extracted counterparts.

    Stage 5 writes these from PubMed (`stage_5_pubmed_enrichment.py:655`);
    an entry PubMed could not match carries none of them, which is why the
    precedence rule has to treat absent, `None` and `''` alike.
    """
    pubmed_authors: str
    pubmed_title: str
    pubmed_journal: str
    pubmed_volume: str
    pubmed_issue: str
    pubmed_pages: str


class WrittenDate(NamedTuple):
    """A month as an entry's line writes it beside a stored bare year, with
    that year ("2008 October"), and the day or days it writes beside the
    month ("21-24", '' for none)."""
    year_month: str = ''
    days: str = ''

    def is_month_written_in(self, text: str) -> bool:
        """True when `text` writes this date's month, in any spelling
        `_MONTH_NAME_TO_NUM` names ("Apr", "APRIL"): a row that prints
        `text` beside the date would write the month twice (VDFTOD 106-108,
        118; #1570 round 4)."""
        if not self.year_month:
            return False
        month = _MONTH_NAME_TO_NUM[self.year_month.split()[1].lower()]
        return any(_MONTH_NAME_TO_NUM[word.lower()] == month
                   for word in _MONTH_NAME_RE.findall(text))


@dataclass(frozen=True, slots=True)
class ResolvedPublication:
    """One publication entry, resolved: every field is the text to print.

    Frozen because a renderer must not be able to edit the record it was
    handed -- the entry it came from is shared with the caller's own
    provenance reads (`enrichment_status`, `text`, the classification
    comment), and #454's class of defect is one section reaching into
    another's state. `enriched_fields` is a tuple for the same reason: a list
    on a frozen dataclass is a mutable field wearing an immutable label.

    An absent value is `''`, never `None`, so every consumer is a truthiness
    test and never a `None` check -- except `target_name`, which stays
    `str | None` because the caller distinguishes "no target to bold" from
    "an empty target name" and `_add_citation_with_bold_author` takes
    `Optional[str]`.
    """
    authors: str = ''
    title: str = ''
    journal: str = ''
    book_title: str = ''
    editors: str = ''
    publisher: str = ''
    year: str = ''
    #: An S8 meeting and its place (#1570). Before they were resolved, an
    #: S8 entry without a stage-5d citation rendered its title and year and
    #: lost both, so 5d could not reject a citation without losing them too.
    conference_name: str = ''
    location: str = ''
    #: An S8 abstract's number, kept only when the entry's line writes it
    #: outside its leading CV list number (`_abstract_number`, #1570).
    abstract_number: str = ''
    #: An S8 meeting's date with the month, and the day, the entry's line
    #: writes: "2023 October" for a stored "2023-10" (`_meeting_date`, #1570).
    #: '' when the line names no such month; the meeting trailer then prints
    #: `year` as it always did.
    meeting_date: str = ''
    #: The month, and the day or days, the entry's line writes beside a
    #: stored bare year: ("2008 October", "21-24") for a stored "2008"
    #: (`_bare_year_date`, #1570). Empty for any other stored year; the
    #: meeting trailer then prints `year`, or `meeting_date`.
    bare_year_date: WrittenDate = WrittenDate()
    #: The entry's own line, which the S8 fallback checks its abstract number
    #: and meeting month against so it prints no word twice (#1570).
    source_text: str = ''
    volume: str = ''
    issue: str = ''
    pages: str = ''
    doi: str = ''
    pmid: str = ''
    pmcid: str = ''
    #: The book series a chapter appeared in, when PubMed indexes the series
    #: as a journal (#481: NTCULM 184, ZCTARO 280, "Methods in enzymology",
    #: "Vitamins and hormones"). Set only by `_chapter_series`; `journal` keeps
    #: the same value so dedup's identity keys do not move. A renderer that
    #: finds it set prints the chapter's book and cites the series after it.
    series: str = ''
    #: Non-empty only for a stage-5d LLM citation, and already topped up with
    #: the editors/publisher its own text omitted (#481). A renderer that
    #: finds it set prints it and stops; it never has to ask who wrote it.
    formatted_citation: str = ''
    target_name: str | None = None
    enriched_fields: tuple[str, ...] = ()


def _text(value: object) -> str:
    """A stage-4 value that has to be prose -- a name, a title, a publisher.

    A number is not a title and a list is not a publisher, so anything that
    is not already a string is not this value: it becomes `''` and the
    renderer omits it. That is the whole of the fix for the repr-in-the-
    document class (#442, #450, #554) on these fields, and it is why no
    renderer downstream carries an `isinstance` call.
    """
    return value if isinstance(value, str) else ''


def _scalar_text(value: object) -> str:
    """A stage-4 value that is a number or an identifier rendered as text --
    a year, a volume, an issue, a page range, a DOI, a PMID.

    Unlike `_text` this accepts a number, because stage 4 legitimately writes
    one: 69 `int` years and 1 `int` volume across the 20,582-entry local farm.
    A bool is not a number here -- it is an `int` subclass that would print
    "True" -- and a list or dict is not a value at all. A falsy input returns
    `''` so that "no value" and "zero" reach the renderer the same way its
    `or` chain always delivered them.
    """
    if not value:
        return ''
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
        return ''
    return str(value)


#: The number that opens an entry's line, which is the CV's own list number
#: in every shape the corpus writes one: "10. ", "*56. ", "61.* ",
#: "112.Analgesia", "2 Rook J", "[56] ", "(56) ", "56) ", "608: ". Wider than
#: stage 5d's `_LEADING_LIST_NUMBER_RE`, which strips only "N." "N)" "(N)"
#: followed by a space: stage 4 stores the other shapes as the abstract
#: number too (YUYVIG LTTYWI 387, TQXZNI 130; NECRGC WYNUCX; #1570).
_OPENING_NUMBER_RE = re.compile(r'\s*\*?\s*[\[(]?\s*(?P<number>\d+)')

#: A month name or abbreviation. The `\.?\s+` after it in the pattern below
#: makes the alternation's order immaterial: "Sep" in "Sept." backtracks.
_MONTH_WORD = '|'.join(_MONTH_NAME_TO_NUM)
#: A month name or abbreviation as a word of its own (`WrittenDate`).
_MONTH_NAME_RE = re.compile(rf'\b(?:{_MONTH_WORD})\b', re.IGNORECASE)
#: A month written next to its year in an entry's line, with the day on
#: either side when the line gives one: "October 2023", "Nov. 2023",
#: "12 September 2018", "May 22, 2000".
_WRITTEN_MONTH_DATE_RE = re.compile(
    rf'\b(?:(?P<day_before>\d{{1,2}})\s+)?(?P<month>{_MONTH_WORD})\.?\s+'
    r'(?:(?P<day_after>\d{1,2}),?\s+)?(?P<year>\d{4})(?!\d)', re.IGNORECASE)
#: A day, or a range of days, written beside a month: "12", "21-24", "3 – 5".
_WRITTEN_DAYS = r'\d{1,2}(?:\s*[-–]\s*\d{1,2})?'
#: `_WRITTEN_MONTH_DATE_RE` with a range of days on either side, read for a
#: stored bare year, which has no day of its own to check them against:
#: "21-24 October 2008", "May 3-5, 2017".
_WRITTEN_MONTH_DAYS_RE = re.compile(
    rf'\b(?:(?P<day_before>{_WRITTEN_DAYS})\s+)?(?P<month>{_MONTH_WORD})\.?\s+'
    rf'(?:(?P<day_after>{_WRITTEN_DAYS}),?\s+)?(?P<year>\d{{4}})(?!\d)', re.IGNORECASE)
#: A stored year and nothing else: "2015", not "Fall 2015" or "2015-10".
_BARE_YEAR_RE = re.compile(r'\d{4}')
#: Text ending in a dash: a date right after it ends a range that began in
#: another month ("September 30 - October 3, 2012").
_RANGE_DASH_END_RE = re.compile(r'[-–]\s*$')
#: A journal locator ("177: A116", "12(3):45-67"): a date anywhere before
#: or after one may be the journal issue's, not the meeting's (GNONHA,
#: #1570; BLBVPD 2126: a volume, issue and page, then the meeting in
#: brackets).
_JOURNAL_LOCATOR_RE = re.compile(r'\d+\s*(?:\([^)]*\))?\s*:\s*[A-Za-z]?\d')
#: A journal locator with no colon (#1570 round 3: a volume, issue and
#: lettered page, then "Part 1" and the issue's date): a volume and its issue
#: in brackets ("20 (4)", "50(Suppl. 1)", "86 (16 Supplement)"), or a page or
#: range of pages with a letter prefix ("A586", "S12-S14", "e1234"). A
#: lettered page stands right after a volume, an issue or a supplement (a
#: number, a closing bracket, or "Vol."/"Issue"/"Suppl.", with
#: "p."/"pp."/"Page" between if the line writes one), and before
#: punctuation, the date, or "Part"/"Pt"/"Suppl". A letter and number after
#: any other word is a room, a poster or a title's own ("Hall B2", "Poster
#: P12", "p53"), and "Session 4" has no letter.
_JOURNAL_ISSUE_OR_PAGE_RE = re.compile(
    r'\d+\s*\(\s*(?:suppl(?:ement)?\.?\s*)?\d+[A-Za-z]?(?:\s*[-–]\s*\d+)?'
    r'(?:\s*suppl(?:ement)?\.?)?\s*\)'
    r'|(?:\d|\)|\b(?:vol(?:ume)?|issue|suppl(?:ement)?)\.?)\s*[,.:;]?\s*(?:(?:p|pp|page)\.?\s*)?'
    r'(?<![\w-])[A-Za-z]\d+(?:\s*[-–]\s*[A-Za-z]?\d+)?(?=\s*(?:[.,;)]|$|(?:part|pt|suppl)\b))',
    re.IGNORECASE)
#: A journal's volume, issue, number or supplement with its number or token
#: (#1570 round 4: "Vol 138, Number Suppl_1, November 2018", where the month
#: is the journal issue's): "Vol. 47", "Vol (13)", "Volume 197", "Issue 2",
#: "No. 5", "Number 3", "Suppl_1", "Supplement_1", "Suppl. 2" ("Number
#: Suppl_1" by its token). A number after "Abstract", "Poster", "Board" or
#: "Session" numbers the meeting's item, not a journal's ("Poster No. 232"),
#: and "No" with no period is as often the word.
_JOURNAL_PART_RE = re.compile(
    r'(?<![\w-])(?:vol(?:ume)?|issue|suppl(?:ement)?'
    r'|(?<!abstract )(?<!poster )(?<!board )(?<!session )(?:no\.|number))'
    r'(?:_\d|\.?\s*[(#]?\s*\d)', re.IGNORECASE)
#: A page marker with its page: "p. 69", "pp.1498-1501", "Page A4577",
#: "Pages 311" (#1570 round 4). A capital "P." is an author's initial ("Rook
#: P. 2015"), and "p53" with no period a title's gene.
_PAGE_MARKER_RE = re.compile(r'(?<![\w.])(?:pp?\.|(?i:pages?)\b[.:]?)\s*[A-Za-z]?\d')
#: A range of pages with the same letter at both ends ("570A-571A", "S744 -
#: S745"), or a lettered page numbered within its session ("S11.002"), as a
#: journal's meeting supplement prints them (#1570 round 4: C6HDHA 35,
#: QZAGWC 221). A letter on one end only is as often a session's code
#: ("Symposium S2-26"), and two different letters a title's ("3D-T2").
_LETTERED_PAGES_RE = re.compile(
    r'(?<![\w.-])(?:(?P<before>[A-Za-z])\d+\s*[-–]\s*(?P=before)\d+'
    r'|\d+(?P<after>[A-Za-z])\s*[-–]\s*\d+(?P=after)'
    r'|[A-Za-z]\d+\.\d{3})(?![\w-])', re.IGNORECASE)
#: Every shape of journal locator `_writes_a_journal_locator` reads.
_JOURNAL_LOCATORS = (_JOURNAL_LOCATOR_RE, _JOURNAL_ISSUE_OR_PAGE_RE, _JOURNAL_PART_RE,
                     _PAGE_MARKER_RE, _LETTERED_PAGES_RE)
#: Text ending in a day and what joins it to the next day of a list, or of a
#: range the date patterns do not read whole: a comma, "&", "/", an em dash,
#: "and", "to", "through" or "thru" ("20, 21 October 2012"; #1570 round 4).
_DAY_LIST_END_RE = re.compile(
    r'(?<!\d)(?P<day>\d{1,2})\s*(?:[,&/—]|and|to|through|thru)\s*$', re.IGNORECASE)
#: Words that number a part of a journal, a book or a meeting. A number
#: right after one is not a day: "Part 2 MAR 8 2006" printed "2006 MAR 2"
#: (web204 2363's shape, #1570), and "No. 5 April 2010" and "Board 12 May 2015"
#: read the same way.
_NUMBERING_WORDS = ('part', 'vol', 'volume', 'no', 'number', 'issue', 'suppl',
                    'supplement', 'session', 'abstract', 'board')
#: Text ending in a numbering word ("Part ", "No. ", "#").
_NUMBERING_WORD_END_RE = re.compile(
    rf"(?:\b(?:{'|'.join(_NUMBERING_WORDS)})\.?|#)\s*$", re.IGNORECASE)
#: The days a month has at most: a larger number beside it is not a day.
_LAST_DAY_OF_MONTH = 31


def _abstract_number(value: object, source_text: str) -> str:
    """An S8 `abstract_number` that the entry's own line writes, else ''.

    The value has to stand in the line as its own token, and must not be the
    number the line opens with: that is the CV's list number, which stage 4
    stores as the abstract number ("56." in YUYVIG ECXGAT 369, 489), and
    printed, it would be a number the CV never gave the abstract (#1570).
    """
    number = _scalar_text(value).strip().rstrip('.')
    if not number:
        return ''
    opening = _OPENING_NUMBER_RE.match(source_text)
    if opening and number.isdigit() and int(number) == int(opening['number']):
        return ''
    return number if re.search(rf'(?<!\w){re.escape(number)}(?!\w)', source_text) else ''


def _plausible_days(source_text: str, match: re.Match[str], group: str) -> str:
    """The day or days `match`'s `group` writes, or '' when they are not
    plausibly days: a number outside 1-31, or one written right after a
    numbering word (`_NUMBERING_WORDS`)."""
    days = match[group]
    if not days or _NUMBERING_WORD_END_RE.search(source_text, 0, match.start(group)):
        return ''
    numbers = [int(number) for number in re.findall(r'\d+', days)]
    return days if all(1 <= number <= _LAST_DAY_OF_MONTH for number in numbers) else ''


def _writes_a_journal_locator(source_text: str, start: int, end: int) -> bool:
    """True when `source_text` writes a journal locator (`_JOURNAL_LOCATORS`)
    before `start` or after `end`, the date's span: the month the date
    writes may then be the journal issue's, not the meeting's (#1570 round
    4: C6HDHA 34-37 before it, QZAGWC 221 after it). The date itself is not
    read, so its own year is no volume ("March 15-19, 2022: 69th Annual
    Meeting")."""
    return any(locator.search(source_text, 0, start) or locator.search(source_text, end)
               for locator in _JOURNAL_LOCATORS)


def _ends_a_day_list(source_text: str, date: re.Match[str]) -> bool:
    """True when the day `date` writes before its month is the last of a
    list of days, or of a range, that a plausible day opens
    (`_DAY_LIST_END_RE`, `_plausible_days`): the date would print that last
    day alone ("20, 21 October 2012" printed "2012 October 21", TMQETQ 421,
    422; #1570 round 4)."""
    listed = _DAY_LIST_END_RE.search(source_text, 0, date.start('day_before'))
    return bool(listed) and bool(_plausible_days(source_text, listed, 'day'))


def _bare_year_date(value: object, source_text: str) -> WrittenDate:
    """The month, and the day or days, the entry's line writes beside a
    stored bare year (`WrittenDate`), or an empty one when the year is not
    bare or the line does not pin the month down.

    Stage 4 stores "21-24 October 2008" as "2008" too, and the S8 fallback
    then lost the month and days 5d's citation kept (QQGKXR 483, ECXGAT
    980; #1570 round 3). Every month the line writes with that year has to
    be the same month, because a line that writes two does not say which is
    the meeting's. The first is taken, unless it ends a range that began in
    another month, or the line writes a journal locator before it or after
    it (`_writes_a_journal_locator`), where the month may be the issue's: a
    number before the month that is no day is the locator's, not the
    date's ("Vol 3 May 2015"). Its days are taken as the line writes them
    when they stand on one side of the month and are plausibly days
    (`_plausible_days`); days on both sides leave which is the meeting's
    open ("12 October 12 2016"), so only the month is taken. Days that end
    a list the date would cut (`_ends_a_day_list`) leave the year bare.
    """
    stored = _scalar_text(value)
    if not _BARE_YEAR_RE.fullmatch(stored):
        return WrittenDate()
    dates = [match for match in _WRITTEN_MONTH_DAYS_RE.finditer(source_text)
             if match['year'] == stored]
    if len({_MONTH_NAME_TO_NUM[match['month'].lower()] for match in dates}) != 1:
        return WrittenDate()
    first = dates[0]
    before = _plausible_days(source_text, first, 'day_before')
    after = _plausible_days(source_text, first, 'day_after')
    start = first.start() if before else first.start('month')
    if (_RANGE_DASH_END_RE.search(source_text, 0, first.start())
            or _writes_a_journal_locator(source_text, start, first.end())
            or before and _ends_a_day_list(source_text, first)):
        return WrittenDate()
    return WrittenDate(f"{stored} {first['month']}", '' if before and after else before or after)


def written_month_dates(text: str) -> set[tuple[str, int]]:
    """The year and month of each date `text` writes with both
    (`_WRITTEN_MONTH_DAYS_RE`), but one that ends a range begun in another
    month: "30 September 2012 - 3 October 2012" is one meeting's dates, and
    two are two meetings' (YIFJUS 228; #1570 round 4)."""
    return {(match['year'], _MONTH_NAME_TO_NUM[match['month'].lower()])
            for match in _WRITTEN_MONTH_DAYS_RE.finditer(text)
            if not _RANGE_DASH_END_RE.search(text, 0, match.start())}


def _meeting_date(value: object, source_text: str) -> str:
    """Stage 4's `year`, as "<year> <Month>[ <day>]" with the month the
    entry's line writes, or '' when it holds no month the line names.

    Stage 4 stores "October 2023" as "2023-10", which the S8 fallback
    printed as written: the month was a source word the line lost (QQGKXR
    158/159, SQMWHM 425, #1570). The month is the line's own word, in the
    line's own spelling, so the fallback adds no word the CV lacks; the day
    is printed only when the line writes the same day next to that month.
    The order is Vancouver's, the one stage 5d's S8 citations use.
    """
    year, month, day = _parse_date_components(_scalar_text(value))
    if not (year and month):
        return ''
    for match in _WRITTEN_MONTH_DATE_RE.finditer(source_text):
        if (int(match['year']) != year
                or _MONTH_NAME_TO_NUM[match['month'].lower()] != month):
            continue
        written_day = match['day_before'] or match['day_after']
        if day and written_day and int(written_day) == day:
            return f"{year} {match['month']} {day}"
        return f"{year} {match['month']}"
    return ''


def _resolved_authors(entry: dict[str, Any], fields: dict, enrichment: dict) -> str:
    """PubMed's author list when stage 5 kept one, with the CV's generational
    suffixes put back (#1438); else stage 4's."""
    pubmed_authors = _text(enrichment.get('pubmed_authors'))
    if pubmed_authors:
        return carry_cv_suffixes(pubmed_authors, _text(entry.get('text')))
    return _text(fields.get('authors'))


def _group_filled_year(entry: dict[str, Any], fields: dict) -> str:
    """The entry's year when stage 4 took it from the year group stage 3b
    filed the line under (#1803), else ''. Only that year is put into a
    stage-5d citation (#1810): 5d formatted the line, which has no year.
    Any other year 5d left out may be one stage 4 misread, so it stays out."""
    group_year = entry.get(GROUP_YEAR_KEY)
    year = _scalar_text(fields.get('year'))
    return year if year == group_year else ''


#: A word of a title, for `_same_work`'s comparison.
_TITLE_WORD_RE = re.compile(r"[^\W_]+")


def _title_words(title: str) -> str:
    """`title` casefolded to its words, single-spaced."""
    return " ".join(_TITLE_WORD_RE.findall(title.casefold()))


def _same_work(pubmed_title: str, cv_title: str) -> bool:
    """True when PubMed's title and the CV's title name the same work: one's
    words run inside the other's ("Chapter 6: Modulation of TRAIL Signaling
    Complex" holds "Modulation of TRAIL signaling complex")."""
    pubmed, cv = _title_words(pubmed_title), _title_words(cv_title)
    return bool(pubmed and cv) and (pubmed in cv or cv in pubmed)


#: A year inside a stage-4 publisher value: stage 4 ran on past the publisher
#: into the line's own citation tail ("Humana Press, Inc., Totowa, NJ. Methods
#: Mol Biol. 2001;165:257-67. PMID: 11217392", ECXGAT 297).
_YEAR_IN_PUBLISHER_RE = re.compile(r"\b(?:19|20)\d\d\b")


def _chapter_series(fields: dict, enrichment: dict) -> str:
    """The PubMed journal, when it is the series of the chapter stage 4 read.

    PubMed indexes some book series as journals, so an enriched chapter
    carries a `pubmed_journal` and rendered as a journal article, without
    its book, editors or publisher (#481: the pilot's NTCULM 184, EBYSBC's
    ZCTARO 280). Stage 4's record is a chapter when it names a book and its
    publisher: an article with a stray publisher names no book, so it stays
    an article. And PubMed's record must be that chapter. One stored CV
    line holds two chapters, PubMed matched the first and stage 4 read the
    book of the second (farm 2054 396). Without the title check its
    citation would join one chapter's title to the other's book. A publisher
    that carries a year is a citation tail stage 4 swept in, not a
    publisher; the book trailer would print it whole, so the PubMed journal
    form stays.
    """
    series = _text(enrichment.get('pubmed_journal'))
    publisher = _text(fields.get('publisher'))
    if not (series and _text(fields.get('book_title')) and publisher):
        return ''
    if _YEAR_IN_PUBLISHER_RE.search(publisher):
        return ''
    cv_title = _text(fields.get('chapter_title')) or _text(fields.get('title'))
    return series if _same_work(_text(enrichment.get('pubmed_title')), cv_title) else ''


def resolve_publication(entry: dict[str, Any]) -> ResolvedPublication:
    """Read one raw bibliography entry and return the record a renderer prints.

    `entry` is raw stage-4/stage-5 JSON and carries more than this reads
    (`enrichment_status`, `classification_reasoning`, the taxonomy code),
    which is why it is annotated as the mapping it is rather than a
    TypedDict that would describe only part of it. The keys taken here are
    `extracted_fields` (`PublicationFields`), `enrichment_data`
    (`PubMedEnrichment`), `enriched_fields`, `text` -- the CV's own
    line, which the stage-5d owner restore reads when stage 4's author list
    is damaged (#1259), coerced by `_text` like any other value -- and
    stage 3b's `GROUP_YEAR_KEY`, which says a year came from the line's
    year group and not the line (#1810).

    Each is guarded with `or` and then a type check, not with a `.get`
    default (#659): a default only applies when the key is *absent*, so an
    entry carrying the key with an explicit `None` hands the `None` straight
    back and the first `.get` on it raises `AttributeError`.

    Both guards are hardening on the shapes this corpus actually carries, and
    that is stated rather than assumed. Measured over the 20,582 local farm
    entries: `enrichment_data` is *absent* in 18,913 and a dict in the other
    1,669 -- never `''` and never null (`grep -roh '"enrichment_data": *""'`
    and the same for `null` both return 0); `enriched_fields` is absent in
    18,369 and a non-empty list in 2,213; `extracted_fields` is a dict in all
    20,582. So no farm entry exercises either the `or` or the `isinstance`
    today. They stay because the shape is not enforced anywhere upstream --
    stage 4 writes raw LLM JSON against no schema (#442, #450, #554) and a
    single explicit null would raise `AttributeError` out of the first `.get`
    on the web path, which has no handler.

    Precedence: a PubMed value outranks the extracted one, and absent, `None`
    and `''` all fall through to the extracted value -- an empty enrichment
    field is what stage 5 writes when PubMed had no such field, not an
    instruction to render nothing. `''` is the live one of those three: the
    six `pubmed_*` keys are present on every enrichment dict, and 331 of them
    across the farm are empty strings.
    """
    fields = entry.get('extracted_fields') or {}
    enrichment = entry.get('enrichment_data') or {}
    enriched_fields = entry.get('enriched_fields') or []
    if not isinstance(fields, dict):
        fields = {}
    if not isinstance(enrichment, dict):
        enrichment = {}
    if not isinstance(enriched_fields, list):
        enriched_fields = []

    editors = _text(fields.get('editors'))
    publisher = _text(fields.get('publisher'))
    source_text = _text(entry.get('text'))

    # The stage-5d reconciliation, done here so the renderer receives a
    # citation that is already whole (points 2/10/11). A citation 5d's LLM
    # formatted without the editors or publisher the entry carries has no
    # later stage that can add them -- except this one (#481).
    formatted_citation = _text(fields.get('formatted_citation'))
    if formatted_citation and fields.get('formatting_source') == _STAGE_5D_FORMATTING_SOURCE:
        # 5d's "first 6, et al." rule cut the CV owner from their own
        # citations; stage 4's list or the CV's own line still has them (#1259).
        formatted_citation = _restore_stage5d_owner(
            formatted_citation, _text(fields.get('authors')), _text(entry.get('text')),
            _text(fields.get('target_name')))
        formatted_citation = _append_missing_stage5d_values(
            formatted_citation, editors, publisher)
        formatted_citation = _insert_missing_stage5d_year(
            formatted_citation, _group_filled_year(entry, fields), _text(fields.get('location')))
        crossref = entry.get(_CROSSREF_MATCH_KEY)
        if isinstance(crossref, dict):
            formatted_citation = _complete_stage5d_trailer(
                formatted_citation, _scalar_text(crossref.get('volume')),
                _scalar_text(crossref.get('issue')), _scalar_text(crossref.get('pages')))
    else:
        formatted_citation = ''

    target_name = fields.get('target_name')

    return ResolvedPublication(
        # Normalized once, here, rather than inside the render loop (point 14).
        authors=_normalize_author_names(_resolved_authors(entry, fields, enrichment)),
        # #728. Stage 4's S4 (book chapter) schema has no `title` key at all:
        # it writes the chapter's title to `chapter_title` and the book's to
        # `book_title`. The renderer read only `title`, so every chapter that
        # reached the deterministic path rendered "Authors. In: Book." and
        # dropped the chapter -- 53 of the 95 farm S4 entries. The other 42
        # carry a `title` written back by stage 5d and take the formatted-
        # citation branch above, so this fall-through cannot double-render:
        # `chapter_title` is read only when there is no title to render.
        #
        # 53 lost it; 49 change. The four that do not: one carries
        # `chapter_title: null`, and three already had a PubMed title, which
        # outranks both extracted names and always did.
        title=(_text(enrichment.get('pubmed_title'))
               or _text(fields.get('title'))
               or _text(fields.get('chapter_title'))),
        journal=_text(enrichment.get('pubmed_journal')) or _text(fields.get('journal')),
        book_title=_text(fields.get('book_title')),
        series=_chapter_series(fields, enrichment),
        editors=editors,
        publisher=publisher,
        # Through the same coercer as every other scalar. The renderer this
        # module replaced read `str(fields.get('year', ''))`, which is not a
        # coercion at all: it printed whatever repr the value had. 381 farm
        # entries carry `year: null` and 299 of them rendered the literal
        # text "None" into the citation ("18. None."), and a list or a dict
        # would have rendered its brackets the same way. An absent year and
        # a null year now both render no year.
        year=_scalar_text(fields.get('year')),
        conference_name=_text(fields.get('conference_name')),
        location=_text(fields.get('location')),
        abstract_number=_abstract_number(fields.get('abstract_number'), source_text),
        meeting_date=_meeting_date(fields.get('year'), source_text),
        bare_year_date=_bare_year_date(fields.get('year'), source_text),
        source_text=source_text,
        volume=(_scalar_text(enrichment.get('pubmed_volume'))
                or _scalar_text(fields.get('volume'))),
        issue=(_scalar_text(enrichment.get('pubmed_issue'))
               or _scalar_text(fields.get('issue'))),
        pages=(_scalar_text(enrichment.get('pubmed_pages'))
               or _scalar_text(fields.get('pages'))),
        doi=_scalar_text(fields.get('doi')),
        pmid=_scalar_text(fields.get('pmid')),
        pmcid=_scalar_text(fields.get('pmcid')),
        formatted_citation=formatted_citation,
        target_name=target_name if isinstance(target_name, str) else None,
        enriched_fields=tuple(enriched_fields),
    )
