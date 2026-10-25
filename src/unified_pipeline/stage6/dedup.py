"""Near-duplicate removal within a taxonomy-code group (#398).

Pure move out of `stage_6_word_template.py` (which re-exports every name here,
so existing callers are unchanged). `deduplicate_entries` is called once per
code group before rendering; `_drop_is_safe` is the #227 guard that only lets
a drop through when the loss is provably recoverable. The docstrings and the
constants' comments carry the accuracy history (#227, #208, C0ZGFW, 2Q1_ZQ)
and are the spec.

Depends one level down on `render_check` (`_record_lines` and its
fused-multi-record threshold) because "is this a fused blob" is the same
question both answer, and on `fan_out` for the fields each section renderer
writes (`_RENDERED_FIELDS`, `_FORMATTED_KEYS`), because "would this record
reach the page" is the question #983's fan-out answers too. Nothing here may
import `stage_6_word_template`.
"""
import re
from types import MappingProxyType
from typing import NamedTuple

from unified_pipeline.core.text_norm import is_placeholder_title, norm

from .fan_out import (
    _FORMATTED_KEYS,
    _RENDERED_FIELDS,
    FANNED_OUT_FROM,
    LAST_STAGE4_RECORD,
)
from .normalization import _squash
from .normalization.authors import (
    _GROUP_AUTHOR_RE,
    _SURNAME_PARTICLES,
    _letters,
    _one_edit_apart,
)
from .normalization.publication import ResolvedPublication, resolve_publication
from .parsing import (
    _MONTH_NAME_TO_NUM,
    _dates_overlap_or_match,
    _get_entry_date_range,
    _parse_date_components,
)
from .render_check import UNRENDERED_MIN_RECORD_LINES, _record_lines

_STOP_WORDS = frozenset({
    'a', 'an', 'and', 'as', 'at', 'be', 'by', 'for', 'from', 'i', 'in',
    'is', 'it', 'of', 'on', 'or', 'the', 'to', 'was', 'with',
})


def _significant_words(text: str) -> set:
    """Extract significant words from text, stripping stop words and punctuation."""
    tokens = re.findall(r'[a-z0-9]+', text.lower())
    return {t for t in tokens if t not in _STOP_WORDS and len(t) > 1}


def _entry_signature_words(entry: dict) -> set:
    """Extract significant words from an entry's full text."""
    return _significant_words(entry.get('text') or '')


def _entry_title_words(entry: dict) -> set:
    """Extract significant words from the title/activity portion of an entry.

    Tries multiple strategies to isolate the meaningful title:
    1. Text before first tab (structured entries)
    2. Quoted text (presentation titles often in quotes)
    3. extracted_fields 'title' or 'activity_title'
    4. Fallback to first 100 chars
    """
    text = (entry.get('text') or '')
    if '\t' in text:
        title = text.split('\t')[0]
    else:
        # Try to find quoted title (common for presentations)
        quoted = re.findall(r'["\u201c](.+?)["\u201d]', text)
        if quoted:
            title = ' '.join(quoted)
        else:
            # Try extracted fields
            fields = entry.get('extracted_fields', {}) or {}
            title = (fields.get('title') or fields.get('activity_title') or
                     fields.get('presentation_title') or '')
            if not title:
                title = text[:100]
    return _significant_words(title)


# _drop_is_safe: a reworded true duplicate ("Associate Professor, HPE, USUHS"
# inside "...Department of Health Professions Education (HPE) Uniformed
# Services University...") has EVERY significant word contained in the kept
# entry — but so does a 3-token degree line whose distinguishing token the
# tokenizer destroyed ('M.S' vs 'PhD', the 2Q1_ZQ B1 loss). Full containment
# only proves duplication when the dropped entry carries enough tokens.
DEDUP_FULL_CONTAINMENT_MIN_TOKENS = 5

# _drop_is_safe token-containment is a TRUE-DUPLICATE signal only when the kept
# entry is itself a single record. When the kept entry is a FUSED multi-record
# blob (a whole layout table captured atomically, #208), a distinct single
# record is fully token-contained in it merely because the blob swallowed it —
# dropping it is real content loss, not deduplication (C0ZGFW: 35 invited
# presentations + 3 teaching records dropped into "Title/Institution/Dates"
# table blobs of 52 and 13 record-lines). A blob this size is the fusion bug,
# not a duplicate. ponytail: gate on record-line count; the source fix is
# de-fusing the table in stage 2 (#208/#248).
DEDUP_FUSED_BLOB_RECORD_LINES = 5

# #666 (WZSMUO Q4D): `deduplicate_entries`' containment test divides by the
# smaller entry's significant words, so a one-word entry is "contained" in
# every longer entry that shares its word: the journal "Cell" was dropped
# beside "Cell Reports" (jaccard 0.50, containment 1.00). Stage 4 had
# extracted no field for "Cell", so `_record_would_be_lost`, which reads the
# dropped entry's name field, never ran. The kept entry's journal name still
# tells the two apart: it holds every word of the dropped text and more, so
# the dropped text is a shorter journal name, not a copy ("Cell" beside "Cell
# Reports" or "Cancer Cell"). Only an entry this short: a longer text is a
# row, whose extra words a name need not hold. Only a journal name: a talk
# title or an organization list is long, and a table cell or a line break
# cuts it into short fragments that are not records (RLADNC R, NJIKGI Q2 in
# the corpus gate).
DEDUP_UNNAMED_SHORT_MAX_WORDS = 2
_SHORTER_NAME_FIELD = 'journal_name'


def _shorter_journal_than_kept(dropped_entry: dict, kept_entry: dict,
                               code: str | None) -> bool:
    """True when the dropped entry names no record its section renders
    (`_record_name`), has at most `DEDUP_UNNAMED_SHORT_MAX_WORDS` significant
    words, and they are a strict subset of the words of the kept entry's
    rendered journal name: "Cell" beside a kept "Cell Reports"."""
    rendered = _RENDERED_FIELDS.get(code or '', frozenset())
    kept_name = (kept_entry.get('extracted_fields') or {}).get(_SHORTER_NAME_FIELD)
    if _SHORTER_NAME_FIELD not in rendered or not isinstance(kept_name, str):
        return False
    if _record_name(dropped_entry.get('extracted_fields') or {}, rendered) is not None:
        return False
    dropped_words = _entry_signature_words(dropped_entry)
    return (0 < len(dropped_words) <= DEDUP_UNNAMED_SHORT_MAX_WORDS
            and dropped_words < _significant_words(kept_name))


# #666: every text-similarity signal `deduplicate_entries` uses is blind to a
# date difference, so "Chair, X Committee, 2015-2018" is token-contained in a
# later "Chair, X Committee, 2021-2024 ..." entry that also mentions 2015 and
# 2018 in passing. `_drop_is_safe` therefore compares the year spans the two
# texts state. A span is a range ("2015-2018", "2015 to 2018", "2015-18",
# "2012-present"; an open end reaches `_YEAR_OPEN_END`) or a bare year. Text,
# not `extracted_fields`, because a fused or prose entry has no single
# start/end and the text is what would be lost.
_YEAR_PATTERN = r'(?:19|20)\d{2}'
_YEAR_RE = re.compile(rf'\b{_YEAR_PATTERN}\b')
# The end of a range may carry a month before its year ("Jun 2021", "06/2009");
# a bare two-digit end ("2015-18") must not be the month of "2008 - 06/2009" or
# the month/day of a full date range ("07/01/2021-06/30/2024" is not 2021-2030).
_YEAR_RANGE_RE = re.compile(
    rf'\b({_YEAR_PATTERN})\s*(?:[-\u2013\u2014]|to|through|until)\s*'
    rf'(?:(?:[A-Za-z]{{3,9}}\.?|\d{{1,2}}[/.])\s*)?'
    rf'({_YEAR_PATTERN}|\d{{2}}(?![/\d])|present|current|now|ongoing)\b',
    re.IGNORECASE)
_YEAR_OPEN_END = 9999


def _year_ranges(text: str) -> list[tuple[int, int]]:
    """(first year, last year) of every year range the text states."""
    ranges = []
    for match in _YEAR_RANGE_RE.finditer(text):
        start, end = match.group(1), match.group(2).lower()
        if end.isdigit():
            end_year = int(start[:2] + end) if len(end) == 2 else int(end)
        else:
            end_year = _YEAR_OPEN_END
        ranges.append((int(start), end_year))
    return ranges


# "Phase I" / "phase 2" / "Phase II/III": the one mark that tells two trials of
# the same drug apart (#1106). A one-character numeral is not a significant
# word, so the token-containment branch below cannot see it. A combined-phase
# design names every numeral in its list, so it covers a copy naming any one.
_PHASE_NUMERAL = r'(?:[1-4]|iv|i{1,3})'
_TRIAL_PHASE_RE = re.compile(
    rf'\bphases?\s*({_PHASE_NUMERAL}(?:\s*(?:/|-|&|,|and)\s*{_PHASE_NUMERAL})*)\b',
    re.IGNORECASE)
_ROMAN_NUMERALS = MappingProxyType({
    'i': '1', 'ii': '2', 'iii': '3', 'iv': '4', 'v': '5', 'vi': '6',
    'vii': '7', 'viii': '8', 'ix': '9', 'x': '10'})


def _trial_phases(text: str) -> set[str]:
    """Every trial phase the text names, Roman numerals as digits."""
    return {_ROMAN_NUMERALS.get(n.lower(), n)
            for group in _TRIAL_PHASE_RE.findall(text)
            for n in re.findall(_PHASE_NUMERAL, group, re.IGNORECASE)}


# #666 (EBYSBC): "Part I" / "Part 2" tells two parts of one series apart the
# way a phase tells two trials apart, and is as invisible to token
# containment ("i" is a stop word, a digit too short): XWNZWW lost a talk's
# "Part I" beside its "Part II". A listing of several parts names each, so it
# covers a copy naming any one.
_PART_NUMERAL = r'(?:\d{1,2}|[ivx]{1,4})'
_PART_NUMBER_RE = re.compile(
    rf'\bparts?\s*({_PART_NUMERAL}(?:\s*(?:/|-|&|,|and)\s*{_PART_NUMERAL})*)\b',
    re.IGNORECASE)


# #666 (VGHNZD): a count with its unit tells two occasions of one activity
# apart the way a date does -- "Tobacco Cessation 3 hrs, 143 students" beside
# "... 4 hrs, 143 students" is the course another year -- and is as invisible
# to token containment (a digit is too short to be a word). Only a number
# carrying a unit counts: a bare one is often a list number a renumbered copy
# changes ("(9)" beside "(11)").
_COUNT_RE = re.compile(
    r'\b(\d+(?:\.\d+)?)\s*(hrs?|hours?|students?|lectures?|sessions?|residents?|'
    r'fellows?|trainees?|participants?|attendees?|learners?|credits?|weeks?|days?)\b',
    re.IGNORECASE)


def _counts(text: str) -> set[tuple[str, str]]:
    """Every count the text states, as (number, unit), the unit singular and lower-case."""
    return {(n, 'hour' if unit.lower().startswith('h') else unit.lower().rstrip('s'))
            for n, unit in _COUNT_RE.findall(text)}


def _part_numbers(text: str) -> set[str]:
    """Every part number the text names, Roman numerals as digits."""
    return {_ROMAN_NUMERALS.get(n.lower(), n.lstrip('0') or n)
            for group in _PART_NUMBER_RE.findall(text)
            for n in re.findall(_PART_NUMERAL, group, re.IGNORECASE)}


# A Roman numeral standing on its own tells two records of one series apart
# ("Example Seminar II" beside "Example Seminar III", "Sample Course I"
# beside "Sample Course II") where no "Part" or "Phase" word precedes it, so
# `_part_numbers` and `_trial_phases` cannot read it, and token containment
# cannot either ("i" is a stop word): EBYSBC OIYKZE-01, and OIEPQD YYVITM 425,
# a "Compounding I" section-lead row dropped beside its "Compounding II" row.
# Upper case only, and V and X never alone: a citation's author initials are
# single capitals. A lone "I" counts where a title segment ends after it or a
# date follows it ("Compounding I November 2017"), and, after a capitalised
# word, where a dash, an open parenthesis or another capitalised word follows
# ("Pharmacology I - Section Lead", "Pharmacology I (Fall 2019)",
# "Pharmacology I Lecturer"); never before a lower-case word ("I taught"), and
# never after one ("where I Lectured"). A lone "I" before a slash, an
# ampersand or "and" and another numeral is read too, so a listing names each
# of its marks ("Pharmacology I/II" and "Pharmacology I and II" are {I, II}:
# a kept listing covers a copy naming either, a kept "II" does not cover a
# dropped listing). A byline of two or more
# "Surname INITIALS" authors is blanked first, so an author's lone initial is
# not read as one ("Kim I, Lee J." beside "Kim IS, Lee J."). A numeral after
# "Part" or "Phase" is left to those readers, which also read a listing
# ("Parts I and II") as a whole. Shared with the doctor's `dedup_drops` lint,
# which reads a drop the same way.
_NUMERAL_DATE_FOLLOWS = (
    r'\s+(?:(?:19|20)\d{2}\b|\d{1,2}/|(?:'
    + '|'.join(sorted((month.capitalize() for month in _MONTH_NAME_TO_NUM),
                      key=len, reverse=True))
    + r')\b)')
_MULTI_LETTER_NUMERALS = r'II|III|IV|VI|VII|VIII|IX'
_NUMERAL_LISTING_FOLLOWS = rf'(?:\s*[/&]\s*|\s+and\s+)(?:{_MULTI_LETTER_NUMERALS})\b'
_STANDALONE_NUMERAL_RE = re.compile(
    rf'\b(?:{_MULTI_LETTER_NUMERALS})\b'
    rf'|\bI(?=\s*(?:$|[\t,.;:)"”’])|{_NUMERAL_DATE_FOLLOWS}|{_NUMERAL_LISTING_FOLLOWS})')
_TITLE_WORD_LONE_I_RE = re.compile(
    r"\b[A-Z][\w'’-]*\s+(I)(?=\s*[(\-–—]|\s+[A-Z][a-z])")
_BYLINE_AUTHOR = rf"[A-Z][\w'’-]+\s+(?!(?:{_MULTI_LETTER_NUMERALS})\b)[A-Z]{{1,3}}\b"
_BYLINE_RE = re.compile(
    rf"{_BYLINE_AUTHOR}(?:\s*,\s*(?:(?:and|&)\s+)?{_BYLINE_AUTHOR})+")
# #666 (IZJADE 942): a single capital letter marks a member of a series the
# same way, where it stands after a capitalised word and a title segment ends
# after it, or a dash or a hyphen after a space follows it ("Committee Q,"
# beside "Committee R,"; "Group B - Lead"), and is as invisible to token
# containment (a one-letter word is not a significant word). The article "A"
# is never one: a word follows it. Nor is a letter a hyphen joins to a word
# ("B-cell"), nor one before a period: an initial ends with one ("Smith J.").
# "I" is the numeral reader's. But a capital letter is also an initial, and a
# byline the byline blank cannot read is shaped the same ("Plover M, Quill,
# MB, ..."), so a letter is read with the word it marks and tells two records
# apart only beside another letter on that word (`_uncovered_letters`). A kept
# text naming the dropped letter anywhere on its own covers it: a listing
# joins its members in more ways than a reader can name ("Committee R and
# Committee Q", "Hepatitis B and C", "Committee R - Chair; Committee Q"), and
# doubt drops. A comma listing ("Committee R, Committee Q") is read as a
# byline and blanked, so it letters no word and refuses nothing.
_LETTER_MARK_RE = re.compile(
    r'\b([A-Z][a-z]+)\s+([A-HJ-Z])(?=\s*(?:$|[\t,;:)–—])|\s+-)')
_LONE_LETTER_RE = re.compile(r'\b[A-HJ-Z]\b')


class _SeriesMarks(NamedTuple):
    """The series marks of a text (`_series_marks`)."""
    numerals: frozenset[str]
    # (the word the letter marks, lower-cased; the letter)
    letters: frozenset[tuple[str, str]]


def _series_marks(text: str) -> _SeriesMarks:
    """Every series mark `text` names outside a byline and a part or phase
    listing: each Roman numeral standing on its own (`_STANDALONE_NUMERAL_RE`,
    `_TITLE_WORD_LONE_I_RE`) and each letter marking a word (`_LETTER_MARK_RE`)."""
    bare = _BYLINE_RE.sub(' ', _TRIAL_PHASE_RE.sub(' ', _PART_NUMBER_RE.sub(' ', text)))
    numerals = set(_STANDALONE_NUMERAL_RE.findall(bare)) | set(_TITLE_WORD_LONE_I_RE.findall(bare))
    letters = {(word.lower(), letter) for word, letter in _LETTER_MARK_RE.findall(bare)}
    return _SeriesMarks(frozenset(numerals), frozenset(letters))


def _standalone_numerals(text: str) -> set[str]:
    """Every Roman numeral `text` names on its own (`_series_marks`)."""
    return set(_series_marks(text).numerals)


def _uncovered_letters(dropped_text: str, kept_text: str) -> set[str]:
    """Each letter the dropped text marks a word with (`_LETTER_MARK_RE`) where
    the kept text marks that word with a letter too and names this one nowhere
    on its own (`_LONE_LETTER_RE`): "Q" for "Committee Q" beside "Committee R"."""
    kept_lettered = {word for word, _letter in _series_marks(kept_text).letters}
    named = set(_LONE_LETTER_RE.findall(kept_text))
    return {letter for word, letter in _series_marks(dropped_text).letters
            if word in kept_lettered and letter not in named}


def _other_series_member(dropped_text: str, kept_text: str) -> bool:
    """True when the dropped text names a member of a series the kept text
    does not: a numeral the kept text lacks ("Compounding I" beside
    "Compounding II"), or an uncovered letter (`_uncovered_letters`). A kept
    text listing both members covers either."""
    return (not _standalone_numerals(dropped_text) <= _standalone_numerals(kept_text)
            or bool(_uncovered_letters(dropped_text, kept_text)))


# #666 (EBYSBC): a date stated to the month, or the day, tells two records
# apart where the year cannot: DPEHSZ lost five dated instances of a resident
# lecture given again in another month of the same year, and KDAZOM a
# teaching session given again on another day ("3/2029" beside "9/2029",
# "4/8/27" beside "2/11/27"). A numeric date ("3/2029", "4/8/27",
# "6/30/2019") is read by the shared parser; a named month may carry a day
# ("August 2nd, 2031"). A bare "8/27" is not read: it may be a day, not a
# year (#867).
_NUMERIC_MONTH_DATE_RE = re.compile(
    r'(?<![\d/.])\d{1,2}/(?:\d{1,2}/(?:\d{4}|\d{2})|\d{4})(?![\d/])')
_NAMED_MONTH_DATE_RE = re.compile(
    r'\b([a-z]{3,9})\.?\s+(?:(\d{1,2})(?:st|nd|rd|th)?,?\s+)?((?:19|20)\d{2})\b',
    re.IGNORECASE)
_DAYS_IN_LONGEST_MONTH = 31
# What joins the two ends of a range, and an open end after it.
_MONTH_RANGE_JOIN_RE = re.compile(r'\s*(?:[-–—]|to|through|until)\s*',
                                  re.IGNORECASE)
_MONTH_OPEN_END_RE = re.compile(
    r'\s*(?:[-–—]|to)\s*(?:present|current|now|ongoing)\b', re.IGNORECASE)

# (year, month, day or None)
_MonthDate = tuple[int, int, int | None]


def _month_dates(text: str) -> list[tuple[_MonthDate, int, int]]:
    """Every date `text` states to the month, with its start and end offset,
    in the order the text states them."""
    found = []
    for match in _NUMERIC_MONTH_DATE_RE.finditer(text):
        year, month, day = _parse_date_components(match.group())
        if year and month:
            found.append(((year, month, day), match.start(), match.end()))
    for match in _NAMED_MONTH_DATE_RE.finditer(text):
        month = _MONTH_NAME_TO_NUM.get(match.group(1).lower())
        day = int(match.group(2)) if match.group(2) else None
        if month:
            if day is not None and not 1 <= day <= _DAYS_IN_LONGEST_MONTH:
                day = None
            found.append(((int(match.group(3)), month, day), match.start(), match.end()))
    return sorted(found, key=lambda date: date[1])


def _month_spans(text: str) -> list[tuple[_MonthDate, _MonthDate | None]]:
    """(first, last) of every date or range `text` states to the month; a
    lone date is its own range, an open end ("6/2029-present") is None."""
    dates = _month_dates(text)
    spans = []
    index = 0
    while index < len(dates):
        date, _start, end = dates[index]
        following = dates[index + 1] if index + 1 < len(dates) else None
        if following and _MONTH_RANGE_JOIN_RE.fullmatch(text[end:following[1]]):
            spans.append((date, following[0]))
            index += 2
            continue
        spans.append((date, None if _MONTH_OPEN_END_RE.match(text, end) else date))
        index += 1
    return spans


def _month_date_before(earlier: _MonthDate, later: _MonthDate) -> bool:
    """True when `earlier` provably precedes `later`: an earlier month, or the
    same month and an earlier day when both state one."""
    if earlier[:2] != later[:2]:
        return earlier[:2] < later[:2]
    return earlier[2] is not None and later[2] is not None and earlier[2] < later[2]


def _month_spans_overlap(span: tuple[_MonthDate, _MonthDate | None],
                         other: tuple[_MonthDate, _MonthDate | None]) -> bool:
    (start, end), (other_start, other_end) = span, other
    return not ((end is not None and _month_date_before(end, other_start))
                or (other_end is not None and _month_date_before(other_end, start)))


# #666 (LTTYWI 297, 307, 311, 351): a teaching award a class votes names the
# class ("Teacher of the Year (Class of 2031)"), and that year is no date of
# the award. So two awards of another class are two awards, in the same year
# or not, and a class year in the kept text does not vouch for the dropped
# award's year ("... 2031" beside "(Class of 2031) ... 2029"). Only for an
# award: a degree's "Class of" year is its date. And only when the kept text
# states a year outside its class labels: when the class year is its one year,
# it dates the award ("Gizmo Honor Society, Class of 2031" beside "Gizmo Honor
# Society, 2031" is one honor).
_CLASS_YEAR_RE = re.compile(rf'\bclass\s+of\s+({_YEAR_PATTERN})\b', re.IGNORECASE)
_CLASS_YEAR_CODES = frozenset({'H'})


def _class_year_apart(dropped_text: str, kept_text: str, code: str | None) -> bool:
    """True, for an award code, when the dropped text names a class year the
    kept text does not, or, when the kept text states a year outside its class
    labels, the dropped text states a date the kept text does not carry once
    every "Class of <year>" is cut out of both (`_dates_compatible`)."""
    if code not in _CLASS_YEAR_CODES:
        return False
    if not set(_CLASS_YEAR_RE.findall(dropped_text)) <= set(_CLASS_YEAR_RE.findall(kept_text)):
        return True
    kept_dates = _CLASS_YEAR_RE.sub(' ', kept_text)
    return bool(_YEAR_RE.search(kept_dates)) and not _dates_compatible(
        _CLASS_YEAR_RE.sub(' ', dropped_text), kept_dates)


def _dates_compatible(dropped_text: str, kept_text: str) -> bool:
    """False when the dropped text states a date the kept text does not carry.

    Each dropped range must overlap a RANGE the kept text states (a kept entry
    that only mentions the two boundary years in passing does not vouch for it);
    each bare dropped year must appear in the kept text or fall inside one of
    its ranges. When the kept text states dates to the month, each date the
    dropped text states to the month must fall in one of them too, to the day
    when both state one (#666, EBYSBC). Undated dropped text is compatible
    with anything. Doubt keeps the entry: a surviving duplicate is visible to
    a reader, a lost record is not.
    """
    kept_ranges = _year_ranges(kept_text)
    for low, high in _year_ranges(dropped_text):
        if not any(low <= kept_high and kept_low <= high
                   for kept_low, kept_high in kept_ranges):
            return False
    kept_years = {int(y) for y in _YEAR_RE.findall(kept_text)}
    for year in _YEAR_RE.findall(_YEAR_RANGE_RE.sub(' ', dropped_text)):
        if int(year) not in kept_years and not any(
                low <= int(year) <= high for low, high in kept_ranges):
            return False
    kept_spans = _month_spans(kept_text)
    return not kept_spans or all(
        any(_month_spans_overlap(span, kept) for kept in kept_spans)
        for span in _month_spans(dropped_text))


# #666: the verbatim branch below proves the dropped TEXT sits inside the kept
# entry's text, but most sections render the kept entry from its extracted
# FIELDS (`_RENDERED_FIELDS`, read off each renderer), and those describe one
# record. When the kept text fused several records (a row of grants, board
# memberships or citations captured as one entry), the others never reached
# the page: web26 lost two M2A sub-grants and an S1 citation that way. So a
# verbatim drop also needs the kept entry's rendered fields to carry the
# dropped record's name: the one of these fields the code's section writes
# (its title-like FIELD_SCHEMAS key; no field-rendered code writes two). A code
# whose section renders the entry's text is not in `_RENDERED_FIELDS`, and its
# verbatim drops stand.
_RECORD_NAME_FIELDS = (
    'title', 'chapter_title', 'grant_title', 'course_title', 'award_name',
    'leadership_role', 'committee_name', 'panel_name', 'program_name',
    'mentee_name', 'journal_name', 'degree', 'specialty',
    # Last, so a code that fills a field above keeps it: I and Q1 name a row by
    # its organization, F1 by its state, an R row with no title by its location.
    'organization', 'state_country', 'location')
# A grant whose amount or number the kept entry also carries is the same grant,
# whatever its title field holds (a description can land in `title`).
_RECORD_ID_FIELDS = (
    'grant_number', 'total_funding', 'annual_funding', 'doi', 'pmid', 'pmcid',
    'isbn', 'patent_number', 'license_number', 'abstract_number')
# Fewer digits than this is a year or a volume, not an identifier.
_RECORD_ID_MIN_DIGITS = 4


def _record_name_key(fields: dict, rendered: frozenset[str]) -> str | None:
    """The first `_RECORD_NAME_FIELDS` key the section renders and `fields`
    fills, or None when it fills none."""
    for key in _RECORD_NAME_FIELDS:
        value = fields.get(key)
        if key in rendered and isinstance(value, str) and value.strip():
            return key
    return None


def _record_name(fields: dict, rendered: frozenset[str]) -> str | None:
    """The record's name: its first filled `_RECORD_NAME_FIELDS` value the
    section renders, or None when it fills none."""
    key = _record_name_key(fields, rendered)
    return fields[key] if key else None


# The doctor's `dedup_drops` lint (#666) reads each decision's extracted name:
# the text alone cannot tell a dropped record from a duplicate once the kept
# text contains all of its words. Only the field that names the record is
# written, and only for the codes where a name conflict has been a real drop:
# a journal name on any code (Q4D "Optics" beside "European Optics"), an I
# society, a D2 hospital, an S3 textbook title, and (EBYSBC: SEKQUI-01,
# VNUAHA-01/02, NDXXAD-01, VYICGW-02, none of which the lint could see) a D1
# title, an H award and a P committee. R location and Q3 panel were 0 real on
# IPXFBA, because a fused row's fragment reads as a different name.
_DECISION_NAME_ANY_CODE = ('journal_name',)
_DECISION_NAME_BY_CODE = {'I': ('organization',), 'D1': ('title',),
                          'D2': ('institution',), 'H': ('award_name',),
                          'P': ('committee_name',), 'S3': ('title',)}
_DECISION_FIELD_MAX_CHARS = 120


def _decision_fields(entry: dict, code: str | None) -> dict:
    """The entry's filled name fields for `code`, clipped for the sidecar."""
    fields = entry.get('extracted_fields') or {}
    keys = _DECISION_NAME_ANY_CODE + _DECISION_NAME_BY_CODE.get(code, ())
    return {key: fields[key].strip()[:_DECISION_FIELD_MAX_CHARS]
            for key in keys
            if isinstance(fields.get(key), str) and fields[key].strip()}


def _shares_record_id(dropped_fields: dict, kept_fields: dict) -> bool:
    """True when both entries carry the same amount, grant number, DOI or other id."""
    for key in _RECORD_ID_FIELDS:
        dropped_id = re.sub(r'\D', '', str(dropped_fields.get(key) or ''))
        if (len(dropped_id) >= _RECORD_ID_MIN_DIGITS
                and dropped_id == re.sub(r'\D', '', str(kept_fields.get(key) or ''))):
            return True
    return False


def _rendered_words(fields: dict, rendered: frozenset[str]) -> set[str]:
    """Every significant word the section writes for an entry with `fields`.

    All rendered fields together, not one: "X Committee, Y Society" is the
    kept record when its fields split it into committee "X Committee" and
    organization "Y Society", and keeping the dropped copy would print that
    row twice. A stage-5 rendering of the whole entry (`_FORMATTED_KEYS`)
    counts as written too. A rendered key holding a list or number vouches
    with the words of its printed form: over-vouching only lets a drop stand,
    as it did before #666, where under-vouching could print a record twice.
    """
    return set().union(*(_significant_words(str(value))
                         for key, value in fields.items()
                         if (key in rendered or key in _FORMATTED_KEYS) and value))


def _record_would_be_lost(dropped_entry: dict, kept_entry: dict,
                          code: str | None, document: list[dict] | None,
                          verbatim: bool, dropped_ids: set[int]) -> bool:
    """True when dropping a contained entry would lose its record.

    It can when the dropped entry names a record (`_record_name`), the kept
    entry has fields, and no shared id ties the two together. Then it would
    in three shapes:

    - verbatim, and the kept entry's rendered fields do not carry every word
      of the name: the kept text fused it, the kept fields describe another;
    - either branch, and the kept text names the record as a run of words
      outside every value its section writes (`_names_a_sibling`): a fused
      row whose sibling the kept fields leave out, reworded or not;
    - either branch, and both entries are bare names that differ
      (`_distinct_bare_names`): "Widgets" is not "Widgets Quarterly";
    - either branch, and the two entries' extracted dates are disjoint
      (`_dates_overlap_or_match`): the kept entry renders its fields' date, so
      a fused kept text vouches for no other date it holds (#666, EBYSBC
      VNUAHA-02: a 2013 award against a fused row whose fields are 2012's).
    - either branch, and the two award names are apart (`_other_award_name`,
      #666 CYOFWJ 40).

    Each shape also needs that no other entry of `document` carries the
    record (`_record_carried`: the name, on the dropped record's dates or on
    none; a bare name only by listing it), so keeping this copy cannot print
    it twice. Entries in
    `dropped_ids` (dropped already, this group or an earlier one) carry
    nothing: two identical copies must not each vouch for the other's drop.
    One more shape needs no such check: the fields say it is another record
    (`_distinct_record_label`: another institution, venue, society, rank or
    book, a companion title, a grant's title alone, a bare place; or
    `_bare_occasion_apart`; or `_narrower_committee`, a committee name the
    kept one qualifies). Doubt drops, as it did before.
    """
    rendered = _RENDERED_FIELDS.get(code or '', frozenset())
    dropped_fields = dropped_entry.get('extracted_fields') or {}
    kept_fields = kept_entry.get('extracted_fields') or {}
    name_key = _record_name_key(dropped_fields, rendered)
    name = dropped_fields[name_key] if name_key else None
    if (not name or not kept_fields
            or _shares_record_id(dropped_fields, kept_fields)):
        return False
    dropped_text = dropped_entry.get('text') or ''
    others = [entry for entry in document or ()
              if entry is not dropped_entry and entry is not kept_entry
              and id(entry) not in dropped_ids]
    if (_distinct_record_label(code, dropped_fields, kept_fields)
            or _bare_occasion_apart(code, dropped_entry, kept_entry)
            or _narrower_committee(code, dropped_entry, kept_entry)):
        return True
    if ((verbatim and not _significant_words(name) <= _rendered_words(kept_fields, rendered))
            or _names_a_sibling(kept_entry, kept_fields, rendered, name)
            or not _dates_overlap_or_match(dropped_entry, kept_entry)
            or _other_award_name(code, dropped_fields, kept_fields)):
        return not any(_record_carried(entry, name, name_key, code, dropped_text)
                       for entry in others)
    if (_distinct_bare_names(dropped_entry, kept_entry,
                             name, _record_name(kept_fields, rendered))
            or _other_journal_same_row(dropped_entry, kept_entry, name, kept_fields)):
        return not any(_lists_name(entry, name, name_key, code, dropped_text)
                       for entry in others)
    return False


# `_distinct_record_label`: records the text-similarity signals cannot tell apart
# but the fields can (#666). Each helper names one way two records of a code
# differ and returns what the dropped record is called, or None.

# The field that tells two records of an appointment code apart: the same title
# ("Senior Fellow") at two hospitals is two appointments. For a talk (R) it is
# the venue: one lecture given in two towns is two talks (#666, EBYSBC RNKYST-01).
# For a membership (I) it is the society: "Widget Society on Gizmos" beside
# "International Society for Applied Widget Gizmos" over the same years is two
# memberships, the shorter name's words all inside the longer, out of order
# (#666, LFFKVV 112).
_INSTITUTION_FIELD_BY_CODE = {'D1': 'institution', 'D2': 'institution', 'D3': 'organization',
                              'I': 'organization', 'R': 'location'}


def _ordered_words(text: str) -> list[str]:
    """The significant words of `text`, in order."""
    return [t for t in re.findall(r'[a-z0-9]+', text.lower())
            if t not in _STOP_WORDS and len(t) > 1]


def _is_subsequence(short: list[str], long: list[str]) -> bool:
    """True when the words of `short` occur in `long`, in order, gaps allowed."""
    remaining = iter(long)
    return all(word in remaining for word in short)


def _different_institution(code: str | None, dropped_fields: dict,
                           kept_fields: dict) -> str | None:
    """The dropped record's institution when the kept record's is another one.

    One name is the other reworded (cut short, a city or a word inserted, a
    one-letter initial spelled out) when its words occur in the other's, in
    order. "Acme Regional Clinic" and "Gadget Clinic for Children at Acme
    Regional" share every word and are still two clinics. For a society (I),
    its words reordered are the same name too (`_REORDERED_NAME_CODES`). A
    longer abbreviation ("Univ." for "University"), a name split in two
    ("North Shore", "Northshore") and one half of a dual name are not
    recognised: they keep both rows (the safe side).
    """
    key = _INSTITUTION_FIELD_BY_CODE.get(code or '')
    dropped, kept = dropped_fields.get(key), kept_fields.get(key)
    if not (isinstance(dropped, str) and isinstance(kept, str)):
        return None
    return dropped if _names_apart(dropped, kept, code) else None


# A society's or an award's name restated with its words in another order is
# the same name: "ASWG - American Society of Widget Gizmos" beside "American
# Society of Widget Gizmos (ASWG)", "Dean's Gizmo Award" beside "Gizmo Award
# (Dean's)". Not for a hospital, a university or a venue, whose word order is
# part of the name: "University of Acme Medical Center" and "Acme University
# Medical Center" are two hospitals.
_REORDERED_NAME_CODES = frozenset({'I', 'H'})


def _names_apart(name: str, other: str, code: str | None) -> bool:
    """True when neither name's words occur in the other's in order
    (`_different_institution`), and, for a `_REORDERED_NAME_CODES` code, the
    two names are not the same words reordered. A name with no words occurs
    in any."""
    words, other_words = _ordered_words(name), _ordered_words(other)
    if code in _REORDERED_NAME_CODES and set(words) == set(other_words):
        return False
    return not _is_subsequence(words, other_words) and not _is_subsequence(other_words, words)


# #666 (CYOFWJ 40): an award's name tells two awards of one year apart:
# "1990 Gizmo Scholar, Acme Gizmo University" beside "1990 Jane Roe Scholar,
# Acme Gizmo University", the shorter name's other word coming from the
# university's name. A name holding the other's words in order is the same
# award reworded ("Gizmo Scholar Award"). Unlike an institution, an award
# named apart still drops when another entry carries it on its date: the
# kept row may be a fused list of awards whose fields name one of them, and
# another listing of the dropped award prints it (EBYSBC VNUAHA-02).
_AWARD_NAME_FIELD_BY_CODE = MappingProxyType({'H': 'award_name'})


def _other_award_name(code: str | None, dropped_fields: dict, kept_fields: dict) -> bool:
    """True when the code names an award and the two award names are apart
    (`_names_apart`)."""
    key = _AWARD_NAME_FIELD_BY_CODE.get(code or '')
    dropped, kept = dropped_fields.get(key), kept_fields.get(key)
    return isinstance(dropped, str) and isinstance(kept, str) and _names_apart(dropped, kept, code)


# A word that makes a title another rank, or a name another award or body:
# "Assistant Professor" is not "Professor", the "Research Committee" is not the
# "Committee", and a committee of a center is not the center (#666, EBYSBC:
# SEKQUI-01 Clinical, KDAZOM-01 and VNUAHA-01 Associate/Assistant, NDXXAD-01
# Outreach, VYICGW-02 Research, RNKYST-01 Executive Committee). An
# abbreviation maps to the word it shortens. "Board" is not one: "Chair and
# Treasurer, Board of Governors" is still the chair.
# ponytail: a closed word list read off the fields. A qualifier not on it
# ("Regional", a spelled-out "Codirector") still lets the drop through; if
# that recurs, stage 4 should extract the rank as a field of its own.
_RANK_QUALIFIERS = MappingProxyType({
    'assistant': 'assistant', 'asst': 'assistant',
    'associate': 'associate', 'assoc': 'associate', 'asso': 'associate',
    'clinical': 'clinical', 'clin': 'clinical', 'adjunct': 'adjunct',
    'visiting': 'visiting', 'research': 'research', 'emeritus': 'emeritus',
    'interim': 'interim', 'acting': 'acting', 'vice': 'vice', 'deputy': 'deputy',
    'co': 'co', 'senior': 'senior', 'executive': 'executive', 'outreach': 'outreach',
    'committee': 'committee', 'subcommittee': 'subcommittee', 'council': 'council'})
# The fields that hold a record's rank, for the codes whose records have one.
_RANK_FIELDS_BY_CODE = MappingProxyType({
    'D1': ('title',), 'D2': ('title',), 'D3': ('title',), 'H': ('award_name',),
    'O': ('leadership_role',), 'P': ('committee_name', 'role'), 'Q1': ('role',)})


def _rank_qualifiers(fields: dict, keys: tuple[str, ...]) -> set[str] | None:
    """The `_RANK_QUALIFIERS` words the `keys` fields hold; None when the
    fields fill none of the keys."""
    values = [fields[key] for key in keys
              if isinstance(fields.get(key), str) and fields[key].strip()]
    if not values:
        return None
    return {_RANK_QUALIFIERS[word] for value in values
            for word in re.findall(r'[a-z]+', value.lower()) if word in _RANK_QUALIFIERS}


def _different_rank(code: str | None, dropped_fields: dict,
                    kept_fields: dict) -> str | None:
    """The dropped record's rank-bearing value when the kept record's carries
    other qualifier words ("Professor" beside "Associate Professor")."""
    keys = _RANK_FIELDS_BY_CODE.get(code or '', ())
    dropped = _rank_qualifiers(dropped_fields, keys)
    kept = _rank_qualifiers(kept_fields, keys)
    if dropped is None or kept is None or dropped == kept:
        return None
    return next(dropped_fields[key] for key in keys
                if isinstance(dropped_fields.get(key), str) and dropped_fields[key].strip())


# One chapter title in two books is two chapters: a module written for two
# boards' curricula, one board's name a word longer than the other's (#666,
# EBYSBC TAUBPU-02). A word added counts, unlike for an institution: a book's
# title is copied, not reworded.
_BOOK_FIELD_BY_CODE = MappingProxyType({'S4': 'book_title'})


def _different_book(code: str | None, dropped_fields: dict,
                    kept_fields: dict) -> str | None:
    """The dropped record's book title when the kept record's differs in a
    significant word."""
    key = _BOOK_FIELD_BY_CODE.get(code or '')
    dropped, kept = dropped_fields.get(key), kept_fields.get(key)
    if (isinstance(dropped, str) and isinstance(kept, str)
            and _significant_words(dropped) and _significant_words(kept)
            and _significant_words(dropped) != _significant_words(kept)):
        return dropped
    return None


# A title that sits inside a longer one after "<companion> for" is the companion
# of that work, not the work: "Study Workbook for <Title>", "Guide to
# <Title>". Only these nouns say so; "Fundamentals of <Title>" may be the same
# book reworded.
_COMPANION_NOUNS = ('guide', 'workbook', 'companion', 'supplement', 'handbook',
                    'manual', 'syllabus', 'key')
_COMPANION_CONNECTORS = ('for', 'to', 'of', 'on', 'with')
# Only a book (S3) has a companion volume; a talk or paper "Guide to X" beside
# "X" is more likely the same record reworded.
_COMPANION_CODES = frozenset({'S3'})


def _companion_title(code: str | None, dropped_fields: dict,
                     kept_fields: dict) -> str | None:
    """The dropped book's title when the kept title holds it after "<companion> for"."""
    name, kept_name = dropped_fields.get('title'), kept_fields.get('title')
    if not (code in _COMPANION_CODES and isinstance(name, str) and isinstance(kept_name, str)):
        return None
    words = re.findall(r'[a-z0-9]+', name.lower())
    if not words:
        return None
    pattern = (r'(?<![a-z0-9])(?:' + '|'.join(_COMPANION_NOUNS) + r')[^a-z0-9]+(?:'
               + '|'.join(_COMPANION_CONNECTORS) + r')[^a-z0-9]+'
               + r'[^a-z0-9]+'.join(words) + r'(?![a-z0-9])')
    return name if re.search(pattern, kept_name.lower()) else None


def _is_word_run(short: list[str], long: list[str]) -> bool:
    """True when `short` is a run of consecutive words of `long`."""
    size = len(short)
    return size > 0 and any(long[i:i + size] == short for i in range(len(long) - size + 1))


# A grant row of which only the title was extracted is a fragment of a row
# whose other cells went elsewhere, not a copy of the kept grant when its title
# is not the kept grant's.
_GRANT_CODES = frozenset({'M2A', 'M2B', 'M2C'})
_GRANT_FRAGMENT_FIELDS = frozenset({'title', 'pi_role'})


def _filled_keys(fields: dict) -> set[str]:
    return {key for key, value in fields.items() if value not in (None, '', [], {})}


def _title_only_fragment(code: str | None, dropped_fields: dict,
                         kept_fields: dict) -> str | None:
    """The dropped title when it is the one thing extracted of a grant row and
    differs in a significant word from the kept grant's title and is not a
    run of consecutive words of it (the kept title plus a suffix is the same
    grant)."""
    title, kept_title = dropped_fields.get('title'), kept_fields.get('title')
    if (code in _GRANT_CODES and isinstance(title, str) and isinstance(kept_title, str)
            and {'title'} <= _filled_keys(dropped_fields) <= _GRANT_FRAGMENT_FIELDS
            and _significant_words(title) != _significant_words(kept_title)
            and not _is_word_run(_ordered_words(title), _ordered_words(kept_title))):
        return title
    return None


# An invited-talk row that is a place and nothing else names no event: against a
# kept row that carries a date it is another occasion at that place (a seminar
# list's undated line between two years), not a copy of it.
_PLACE_ONLY_EVENT_FIELDS = frozenset({'location'})
_EVENT_DATE_FIELDS = ('date', 'start_date')


def _place_only_event(code: str | None, dropped_fields: dict,
                      kept_fields: dict) -> str | None:
    """The dropped place when it is all an R row says and the kept row is dated."""
    if (code == 'R' and _filled_keys(dropped_fields) == _PLACE_ONLY_EVENT_FIELDS
            and any(kept_fields.get(key) for key in _EVENT_DATE_FIELDS)):
        return dropped_fields['location']
    return None


# An invited-talk row that is a date and a place and nothing else takes its
# event from the heading it is listed under: under another heading than the
# kept row it is another occasion, even on that day in that city (#666, EBYSBC
# TAUBPU: a course's later offering, listed under the course, beside a
# conference talk in the same city that day).
_BARE_OCCASION_FIELDS = frozenset({'location', 'date'})


def _bare_occasion_apart(code: str | None, dropped_entry: dict,
                         kept_entry: dict) -> str | None:
    """The dropped place when the R row is a place and a date only and the
    kept row sits under another source heading."""
    fields = dropped_entry.get('extracted_fields') or {}
    if (code == 'R' and fields.get('location')
            and _filled_keys(fields) <= _BARE_OCCASION_FIELDS
            and dropped_entry.get('hierarchy') != kept_entry.get('hierarchy')):
        return fields['location']
    return None


# #666 (JEFZWH 295, BLBVPD 94, WYMVGU 557): a committee whose name the kept
# committee's name holds, in order, with a word added that the dropped row
# states nowhere -- "Widget Committee" beside "Widget Planning Committee" or
# "Widget Committee of the Department of Gizmos" -- is a narrower body, not a
# copy: the qualifier is the one thing that tells the two apart, and token
# containment cannot see a word the dropped side lacks. Words added only in
# front ("Acme University, Clinic Services Committee") are where the committee
# sits, and so are segments that follow the whole dropped name after a comma,
# a semicolon, a dash or a hyphen after a space ("Clinic Services Committee,
# Acme University": stage 4 folds a P row's institution into the name that
# way, as P extracts no institution), or are its short name ("... Committee -
# CSC"). An acronym in parentheses is its short name, and a plural is the same
# word; none of these makes another committee. A word the dropped row states,
# in its text or in a field its section renders, is no qualifier. A name of
# one significant word ("Committee", a letter cut off) is a fragment, not a
# narrower body.
_COMMITTEE_NAME_FIELD = 'committee_name'
_NARROWER_COMMITTEE_MIN_WORDS = 2
_NAME_SEGMENT_SEPARATOR_RE = re.compile(r'[,;–—]|\s-')
# A plural ending is dropped so "Committees" reads as "Committee". Words this
# short or shorter keep their final s.
_PLURAL_MIN_CHARS = 4


def _singular(word: str) -> str:
    """`word` without a plural s (`_PLURAL_MIN_CHARS`)."""
    return word[:-1] if len(word) > _PLURAL_MIN_CHARS and word.endswith('s') else word


def _singular_words(text: str) -> list[str]:
    """`_ordered_words` of `text`, each `_singular`."""
    return [_singular(word) for word in _ordered_words(text)]


def _name_through(kept_name: str, words: list[str]) -> list[str] | None:
    """`_singular_words` of `kept_name`, parentheses out, up to the end of the
    segment (`_NAME_SEGMENT_SEPARATOR_RE`) where it has held all of `words` in
    order; None when it never does."""
    kept_words: list[str] = []
    for segment in _NAME_SEGMENT_SEPARATOR_RE.split(_PARENTHETICAL_RE.sub(' ', kept_name)):
        kept_words += _singular_words(segment)
        if _is_subsequence(words, kept_words):
            return kept_words
    return None


def _narrower_committee(code: str | None, dropped_entry: dict,
                        kept_entry: dict) -> str | None:
    """The dropped committee's name when its section renders a committee name
    and the kept committee's name is the dropped one with words added, not
    only in front of it or in segments after it (`_name_through`), of which
    the dropped row states at least one nowhere."""
    rendered = _RENDERED_FIELDS.get(code or '', frozenset())
    if _COMMITTEE_NAME_FIELD not in rendered:
        return None
    dropped_fields = dropped_entry.get('extracted_fields') or {}
    name = dropped_fields.get(_COMMITTEE_NAME_FIELD)
    kept_name = (kept_entry.get('extracted_fields') or {}).get(_COMMITTEE_NAME_FIELD)
    if not (isinstance(name, str) and isinstance(kept_name, str)):
        return None
    words = _singular_words(_PARENTHETICAL_RE.sub(' ', name))
    if len(words) < _NARROWER_COMMITTEE_MIN_WORDS:
        return None
    kept_words = _name_through(kept_name, words)
    if kept_words is None or kept_words[-len(words):] == words:
        return None
    stated = {_singular(word) for word in (_significant_words(dropped_entry.get('text') or '')
                                           | _rendered_words(dropped_fields, rendered))}
    return name if set(kept_words) - stated else None


def _distinct_record_label(code: str | None, dropped_fields: dict,
                           kept_fields: dict) -> str | None:
    """What the dropped record is called when its fields say it is not the kept
    record, else None."""
    return (_different_institution(code, dropped_fields, kept_fields)
            or _different_rank(code, dropped_fields, kept_fields)
            or _different_book(code, dropped_fields, kept_fields)
            or _companion_title(code, dropped_fields, kept_fields)
            or _title_only_fragment(code, dropped_fields, kept_fields)
            or _place_only_event(code, dropped_fields, kept_fields))


def _word_run_pattern(text: str) -> str | None:
    """A regex matching the words of `text` in order, any punctuation or
    spacing between them, never part of a longer word; None for no words."""
    words = re.findall(r'[a-z0-9]+', text.lower())
    if not words:
        return None
    return r'(?<![a-z0-9])' + r'[^a-z0-9]+'.join(words) + r'(?![a-z0-9])'


def _names_a_sibling(kept_entry: dict, kept_fields: dict,
                     rendered: frozenset[str], name: str) -> bool:
    """True when the kept text names the record `name` outside every value
    the kept entry's section writes (the `_rendered_words` fields).

    A fused row ("Chair, Gadget Committee<TAB>Member, Sprocket Council") keeps
    its sibling's name once the written values are cut out; a same-record
    rewording ("Northern Tinkerers, Gadget Committee" against a kept
    "Gadget Committee<TAB>Northern Tinkerers") does not, because its words
    never stand together in the kept text.
    """
    name_pattern = _word_run_pattern(name)
    written = [str(value).lower() for key, value in kept_fields.items()
               if value and (key in rendered or key in _FORMATTED_KEYS)]
    if not name_pattern or any(re.search(name_pattern, value) for value in written):
        return False
    residual = (kept_entry.get('text') or '').lower()
    for value in written:
        value_pattern = _word_run_pattern(value)
        if value_pattern:
            residual = re.sub(value_pattern, '\x00', residual)
    return bool(re.search(name_pattern, residual))


# A parenthetical after a name is its acronym or a qualifier ("(ACME)"), not
# another word of the name.
_PARENTHETICAL_RE = re.compile(r'\([^)]*\)')


# What a long name says after "formerly" is its history, not another record.
_FORMERLY_RE = re.compile(r'\b(?:formerly|f/k/a)\b.*', re.IGNORECASE | re.DOTALL)


def _core_words(name: str) -> set:
    """The significant words of `name` outside its parentheticals and its
    "formerly ..." history."""
    return _significant_words(_FORMERLY_RE.sub(' ', _PARENTHETICAL_RE.sub(' ', name)))


# A trailing "journal" is how a row calls the journal, not part of its name:
# "Widgets Record journal" is "Widgets Record".
_TRAILING_JOURNAL_RE = re.compile(r'\s+journal\s*$', re.IGNORECASE)


def _journal_core_words(name: str) -> set:
    """`_core_words` of a journal name without a trailing "journal"."""
    return _core_words(_TRAILING_JOURNAL_RE.sub('', name))


# A word that makes what follows a name another body: "Acme Society - Gadget
# Society Exchange Program" joins two societies, "Acme Society - Council on
# Widgets" names a council of the one.
_OTHER_BODY_WORDS = frozenset({
    'association', 'society', 'college', 'academy', 'federation', 'union',
    'foundation', 'institute', 'program', 'exchange'})


def _is_unit_of(kept_name: str, name: str) -> bool:
    """True when the kept name opens with the dropped name's words and what
    follows names no other body: a council or section of the same society, of
    which a bare "Acme Society" is the parallel listing, not another record."""
    clean = lambda text: _ordered_words(_FORMERLY_RE.sub(' ', _PARENTHETICAL_RE.sub(' ', text)))
    words, kept = clean(name), clean(kept_name)
    return (bool(words) and kept[:len(words)] == words
            and not _OTHER_BODY_WORDS & set(kept[len(words):]))


def _distinct_bare_names(dropped_entry: dict, kept_entry: dict,
                         name: str, kept_name: str | None) -> bool:
    """True when the dropped entry's text is nothing but its record's name and
    the names differ in a significant word: a journal list's "Widgets" is
    contained in "Widgets Quarterly" and is still another journal. The kept
    entry is either another bare name or a row whose name holds the dropped
    one and more ("Acme Society" in the "Gadget Society - Acme Society
    Exchange Program" row), unless the longer name is a unit of the same body
    ("Acme Society - Council on Widgets": the bare listing is a duplicate). Any other text (a date, a role, a venue) is a record a name
    alone cannot tell apart, and "The Widgets" is still "Widgets"."""
    if not kept_name or _alnum(dropped_entry.get('text') or '') != _alnum(name):
        return False
    dropped_words, kept_words = _core_words(name), _core_words(kept_name)
    if dropped_words == kept_words:
        return False
    return (_alnum(kept_entry.get('text') or '') == _alnum(kept_name)
            or (dropped_words < kept_words and not _is_unit_of(kept_name, name)))


# `_other_journal_same_row`: the field that names a journal, and what a row
# keeps of its text once that name is cut out (its letters: a role and a
# status such as "Ad hoc ... Present", no dates, no punctuation).
_JOURNAL_NAME_FIELD = 'journal_name'
_NON_LETTERS_RE = re.compile(r'[^a-z]')


def _row_residue(text: str, name: str) -> str | None:
    """The letters of `text` left once the first run of `name`'s words is cut
    out; None when `name` is not in `text` as a run of words."""
    pattern = _word_run_pattern(name)
    if not pattern or not re.search(pattern, text.lower()):
        return None
    return _NON_LETTERS_RE.sub('', re.sub(pattern, '', text.lower(), count=1))


def _other_journal_same_row(dropped_entry: dict, kept_entry: dict,
                            name: str, kept_fields: dict) -> bool:
    """True when both entries are a row for a journal, the rows say the same
    thing about it (role and status; the dates may differ) and the journal
    names differ in a significant word: "Ad hoc Widgets, 2013-" beside "Ad hoc
    Widgets Quarterly, 2013-" are two journals, and "Gizmos" is not "Acme
    Gizmos". Unlike `_distinct_bare_names` the text may hold more than the
    name, but only what both rows hold. A journal's name is exact where a
    committee's is not ("Acme University, Review Committee" is "Review
    Committee"), which is why this stops at journals."""
    kept_name = kept_fields.get(_JOURNAL_NAME_FIELD)
    dropped_residue = _row_residue(dropped_entry.get('text') or '', name)
    return bool(isinstance(kept_name, str)
                and (dropped_entry.get('extracted_fields') or {}).get(_JOURNAL_NAME_FIELD) == name
                and dropped_residue is not None
                and dropped_residue == _row_residue(kept_entry.get('text') or '', kept_name)
                and _journal_core_words(name) != _journal_core_words(kept_name))


# `_lists_name`: the separators between the items of a listed text. A title in
# double quotation marks is one item, whatever separators it holds: the row of
# a reviewed book titled “Widgets: Gizmos, Sprockets and Cogs” lists no
# journal "Widgets" (#666, LFFKVV 1487).
_LIST_ITEM_SEPARATOR_RE = re.compile(r'[,;:|\t\n]')
_QUOTED_TITLE_RE = re.compile(r'“[^”]*”|"[^"]*"')


def _list_items(text: str) -> list[str]:
    """The items of `text` (`_LIST_ITEM_SEPARATOR_RE`), a quoted title one item."""
    unsplit = _QUOTED_TITLE_RE.sub(lambda title: _LIST_ITEM_SEPARATOR_RE.sub(' ', title.group()), text)
    return _LIST_ITEM_SEPARATOR_RE.split(unsplit)


def _lists_name(entry: dict, name: str, key: str, code: str | None,
                dropped_text: str) -> bool:
    """True when `entry` carries the record called `name` itself.

    Two ways, and no other (#666: a fellowship's `specialty`, a degree's
    `degree` or a citation's journal vouched for a reviewer row that only
    shared a word with them):

    - an item of its text (`_list_items`) is exactly that name, and the entry
      is of the same code (the record's own list: "Gizmo Review, Widgets");
    - its `key` field, the one that named the dropped record, holds exactly
      that name and the row says the same about it as the dropped row
      (`_same_row`). A longer name holding it ("Widgets Quarterly") is
      another record.
    """
    target = _alnum(name)
    text = entry.get('text') or ''
    same_code = entry.get('taxonomy_code') == code
    if same_code and any(_alnum(item) == target for item in _list_items(text)):
        return True
    fields = entry.get('extracted_fields') or {}
    return (_alnum(str(fields.get(key) or '')) == target
            and _same_row(text, dropped_text, name, same_code))


def _same_row(text: str, dropped_text: str, name: str, same_code: bool) -> bool:
    """True when `text` says no more about `name` than the dropped row does: the
    letters left of each once the name is cut out are the same (role and
    status; the dates may differ), or `text` is the bare name in the dropped
    row's own code. "Widgets, Editorial Board" is not "Widgets, Associate
    Editor", and a bare "Widgets" under another code is that code's record:
    an ad hoc reviewer list's "Widgets" is not the editorial board's
    "Widgets, Review Editor" (#666, LFFKVV 1434)."""
    residue = _row_residue(text, name)
    return (residue == '' and same_code) or (residue is not None
                                             and residue == _row_residue(dropped_text, name))


# `_names_record`: a long name counts as named by a text that holds this share
# of its significant words. A CV that lists one abstract twice rewords its
# title the second time (a qualifier added, an acronym spelled out): web228
# had two such pairs, and keeping the dropped copy printed each twice.
_NAMED_ELSEWHERE_WORD_SHARE = 0.8


def _names_record(text: str, name: str) -> bool:
    """True when `text` mentions the record called `name`.

    A name of `DEDUP_FULL_CONTAINMENT_MIN_TOKENS` or more significant words is
    mentioned when the text holds `_NAMED_ELSEWHERE_WORD_SHARE` of them; a
    shorter one only as a run of the same letters and digits, since two
    common words ("Widget Services") are in many unrelated entries.
    """
    name_words = _significant_words(name)
    if len(name_words) >= DEDUP_FULL_CONTAINMENT_MIN_TOKENS:
        shared = len(name_words & _significant_words(text))
        return shared >= _NAMED_ELSEWHERE_WORD_SHARE * len(name_words)
    return _alnum(name) in _alnum(text)


def _carries_record(text: str, name: str, dropped_text: str) -> bool:
    """True when `text` names the record called `name` (`_names_record`) on
    the dates the dropped text states, or states no year at all: another
    year's "Widget Award" does not carry this year's (#666, EBYSBC VNUAHA-02,
    where four other years of one award vouched for a dropped fifth)."""
    return _names_record(text, name) and (
        not _YEAR_RE.search(text) or _dates_compatible(dropped_text, text))


def _record_carried(entry: dict, name: str, key: str, code: str | None,
                    dropped_text: str) -> bool:
    """True when `entry` carries the dropped record called `name`: by
    `_carries_record`, or, when the dropped text is the bare name and nothing
    else, only by listing that name itself (`_lists_name`). A bare name's
    words stand in longer names of other records, so naming it is not
    carrying it (#666, ZASOTV Q4D: the journal "Widgets" dropped against its
    lead-in line "Reviewer for widgets journals:", then vouched for by the
    "Journal of Widgets" listed under it)."""
    if _alnum(dropped_text) == _alnum(name):
        return _lists_name(entry, name, key, code, dropped_text)
    return _carries_record(entry.get('text') or '', name, dropped_text)


def _alnum(text: str) -> str:
    """Lowercased letters and digits only: punctuation and spacing ignored."""
    return re.sub(r'[^a-z0-9]', '', text.lower())


# A mentee "name" that names nobody: template placeholders the CV left unfilled.
_PLACEHOLDER_NAMES = frozenset({'', 'na', 'none', 'tbd', 'tba'})


def _different_mentees(dropped_entry: dict, kept_entry: dict) -> bool:
    """#1181: True when both entries name a mentee and the names differ.

    WCM mentee tables repeat the same field labels for every mentee, so two
    residents at one site score as near-duplicates whatever their names
    (jaccard 0.92 on FINSIS). A person's name is not reworded between two
    copies of one record the way a title is, so a different name is a
    different mentee."""
    names = {_alnum(str((entry.get('extracted_fields') or {}).get('mentee_name') or ''))
             for entry in (dropped_entry, kept_entry)}
    return len(names) == 2 and not names & _PLACEHOLDER_NAMES


def _verbatim_contained(text: str, container: str) -> bool:
    """True when `text`, whitespace ignored (`_squash`), stands in `container`
    starting and ending on a word boundary there. Squashed, "Part II" sits
    inside "Part III" (#666, EBYSBC OIYKZE-01) and "Page 2" inside "Page 21";
    neither ends on a boundary. Spacing inside the text is still ignored, so a
    footnote mark glued on ("Studiesb" for "Studies b") stays verbatim."""
    squashed = _squash(text)
    if not squashed or squashed not in _squash(container):
        return False
    lead = r'(?<![a-z0-9])' if squashed[0].isalnum() else ''
    trail = r'(?![a-z0-9])' if squashed[-1].isalnum() else ''
    body = r'\s*'.join(re.escape(char) for char in squashed)
    return re.search(lead + body + trail, container.lower()) is not None


# #666 (EBYSBC BZZNRL-04): a teaching or talk list grouped under employer lines
# ("<institution> (2001-2006)", later "<institution> (2006 to present)") or
# year lines ("2006") repeats one activity under each, and only the group line
# tells the two apart: stage 3b leaves that line a row of its own, not a field
# of the rows below it (#985). So such a line standing between the two
# entries, in the same source section, makes them two records. A name with a
# single year is not a group line: "Grand Rounds 2030" is a record.
# ponytail: a group line is recognised by shape alone. One whose year sits on
# the group's first row ("2002 Course A ...") is not seen; the upgrade is #985,
# carrying the group line into the rows below it as context.
_GROUP_HEADED_CODES = frozenset({'K1', 'K2', 'K3', 'K4', 'K5', 'R'})
_GROUP_YEAR_RANGE = (r'(?:19|20)\d{2}\s*(?:[-–—]|to)\s*'
                     r'(?:(?:19|20)\d{2}|present|current)')
_GROUP_HEADER_RE = re.compile(
    rf'[^\d,;:|\t\n]{{3,120}}?[\s,(]*{_GROUP_YEAR_RANGE}\s*\)?\.?'
    rf'|(?:{_GROUP_YEAR_RANGE}|(?:19|20)\d{{2}})\s*(?:\([^)]*\))?\.?', re.IGNORECASE)


def _group_header_between(dropped_entry: dict, kept_entry: dict,
                          code: str | None, document: list[dict] | None) -> bool:
    """True when an entry of `document` whose whole text is a group line
    (`_GROUP_HEADER_RE`: a name and a year range, or a year or year range
    alone) stands between the two entries in source order, in their source
    section."""
    hierarchy = dropped_entry.get('hierarchy')
    positions = (dropped_entry.get('element_idx_start'), kept_entry.get('element_idx_start'))
    if (code not in _GROUP_HEADED_CODES or not hierarchy
            or kept_entry.get('hierarchy') != hierarchy
            or not all(isinstance(position, int) for position in positions)):
        return False
    low, high = sorted(positions)
    return any(isinstance(entry.get('element_idx_start'), int)
               and low < entry['element_idx_start'] < high
               and entry.get('hierarchy') == hierarchy
               and _GROUP_HEADER_RE.fullmatch((entry.get('text') or '').strip())
               for entry in document or ())


# #666 (LTTYWI 175, 179): a dated list whose year heads a group's first row
# ("1997-2002 Member, Gadget Committee", then "Member, Widget Committee" on a
# line of its own) gives the undated rows below it that first row's years. So
# an undated row and a row of the same name under another year head are two
# terms of one committee, not two copies: "Member, Widget Committee" under
# 1997-2002 beside "2002-2003 Member, Widget Committee". The head of a row is
# the row itself when its text opens with a year, else the nearest earlier row
# of its source section that states a year, when that row opens with it and
# the lines from it down to the row run on with no line between them that is
# not a row of the section: a blank line ends a group, and an undated line
# after one is a heading of the rows below it ("Acme Society" above "2000-03
# Member, Acme Society"), not a row of the group above. A row stating a year
# anywhere else heads no group (`_YEAR_LED_RE`), and an undated row of a
# dated list is the shape only when it names a record its section renders: a
# fragment of a row names none. This is the "year sits on the group's first
# row" case `_group_header_between` cannot see.
_YEAR_LED_RE = re.compile(rf'^\W*{_YEAR_PATTERN}\b')


def _states_no_date(entry: dict) -> bool:
    """True when neither the entry's text nor its date fields state a year."""
    return not _YEAR_RE.search(entry.get('text') or '') and not _field_dates_text(entry)


def _year_head(entry: dict, section: list[dict]) -> dict | None:
    """The row whose leading year dates `entry` (see `_YEAR_LED_RE` above):
    the entry itself, or the nearest earlier row of `section` stating a year,
    reached through rows each ending on the line before the next; None when
    that row does not open with its year or a line between is no row."""
    row = entry
    while not _YEAR_RE.search(row.get('text') or ''):
        start = row['element_idx_start']
        row = max((other for other in section if other['element_idx_start'] < start),
                  key=lambda other: other['element_idx_start'], default=None)
        if row is None or row.get('element_idx_end', row['element_idx_start']) != start - 1:
            return None
    return row if _YEAR_LED_RE.match(row.get('text') or '') else None


def _under_another_year_head(dropped_entry: dict, kept_entry: dict,
                             code: str | None, document: list[dict] | None) -> bool:
    """True when the dropped row states no date, names a record its section
    renders, and its year head (`_year_head`) is not the kept row's, both
    rows in one source section."""
    hierarchy = dropped_entry.get('hierarchy')
    rendered = _RENDERED_FIELDS.get(code or '', frozenset())
    if (not hierarchy or kept_entry.get('hierarchy') != hierarchy
            or not all(isinstance(entry.get('element_idx_start'), int)
                       for entry in (dropped_entry, kept_entry))
            or not _states_no_date(dropped_entry)
            or _record_name(dropped_entry.get('extracted_fields') or {}, rendered) is None):
        return False
    section = [entry for entry in document or ()
               if entry.get('hierarchy') == hierarchy
               and isinstance(entry.get('element_idx_start'), int)]
    dropped_head = _year_head(dropped_entry, section)
    kept_head = _year_head(kept_entry, section)
    return dropped_head is not None and kept_head is not None and dropped_head is not kept_head


# #666 (BLBVPD 213, IXJMKS 866): an ad hoc review for a panel and a standing
# seat on it are two records, and nothing else in the two rows need differ:
# "1990 Widget Review Committee" beside "1990 Ad hoc Reviewer, Widget Review
# Committee, Special Panel", or an agency listed under an "Ad Hoc Widget
# Reviews" heading beside that agency's study-section row under "Standing
# Widget Panels". A row is ad hoc when its text, its role or a heading it is listed
# under says so. Only a row naming an agency or a panel: a role line alone is
# a fragment. A panel listed twice in one year, once as ad hoc, cannot be told
# from these two records, so it prints twice: doubt keeps.
_AD_HOC_CODES = frozenset({'Q3'})
_AD_HOC_RE = re.compile(r'\bad[\s-]*hoc\b', re.IGNORECASE)
_REVIEW_RECORD_FIELDS = ('agency', 'panel_name')


def _is_ad_hoc(entry: dict) -> bool:
    """True when the entry's text, role or a source heading says "ad hoc"."""
    role = (entry.get('extracted_fields') or {}).get('role')
    sources = [entry.get('text') or '', role if isinstance(role, str) else '',
               *(heading for heading in entry.get('hierarchy') or () if isinstance(heading, str))]
    return any(_AD_HOC_RE.search(source) for source in sources)


def _ad_hoc_apart(dropped_entry: dict, kept_entry: dict, code: str | None) -> bool:
    """True when a review row naming an agency or a panel is ad hoc and the
    kept row is not, or the other way round (`_is_ad_hoc`)."""
    fields = dropped_entry.get('extracted_fields') or {}
    return (code in _AD_HOC_CODES
            and any(isinstance(fields.get(key), str) and fields[key].strip()
                    for key in _REVIEW_RECORD_FIELDS)
            and _is_ad_hoc(dropped_entry) != _is_ad_hoc(kept_entry))


def _split_record_family(entry: dict, document: list[dict] | None) -> list[dict]:
    """`entry` and every other record of `document` split out of the same
    multi-record entry: the same source position and the same records list
    (`FANNED_OUT_FROM`, or `LAST_STAGE4_RECORD` on the last record, which
    keeps the parent's whole text)."""
    provenance = entry.get(FANNED_OUT_FROM) or {}
    family = [entry]
    for other in document or ():
        marker = other.get(FANNED_OUT_FROM) or other.get(LAST_STAGE4_RECORD) or {}
        if (other is not entry
                and other.get('element_idx_start') == entry.get('element_idx_start')
                and marker.get('key') == provenance.get('key')
                and marker.get('count') == provenance.get('count')):
            family.append(other)
    return family


def _field_dates_text(entry: dict) -> str:
    """The date span `entry`'s fields state, as text `_dates_compatible`
    reads ("2019", "2015 - 2018"), or '' when they state none."""
    values = (str(value).strip() for value in _get_entry_date_range(entry))
    return ' - '.join(dict.fromkeys(filter(None, values)))


def _undated_split_record_absorbs_date(dropped_entry: dict, kept_entry: dict,
                                       document: list[dict] | None) -> bool:
    """True when the kept entry is an undated record split out of a
    multi-record entry and the dropped entry's fields state a date that
    entry states nowhere: in no record's text and no record's date fields
    (#666, OWACZM 255: a 2019 talk dropped as a copy of the undated first
    line of a two-line entry 196).

    A split record carries none of its parent's context (#983), so its lack
    of a date is not evidence of the same date; `_dates_overlap_or_match`'s
    "one side undated, cannot prove different" lets every dated listing of
    the same venue fall into it. When the multi-record entry does state the
    dropped date somewhere, the drop stands as before. Two whole entries,
    one dated and one not, are not this shape and still collapse."""
    if not kept_entry.get(FANNED_OUT_FROM) or _field_dates_text(kept_entry):
        return False
    dropped_dates = _field_dates_text(dropped_entry)
    family_text = ' '.join(f"{entry.get('text') or ''} {_field_dates_text(entry)}"
                           for entry in _split_record_family(kept_entry, document))
    return not _dates_compatible(dropped_dates, family_text)


def _drop_is_safe(dropped_entry: dict, kept_entry: dict,
                  code: str | None = None,
                  document: list[dict] | None = None,
                  dropped_ids: set[int] | None = None) -> bool:
    """#227 guard: only drop an entry when the loss is provably recoverable.

    Safe when the dropped text is verbatim-contained in the kept entry, or
    every significant word of a token-rich dropped entry appears in the kept
    entry (both are true-duplicate shapes) AND the kept entry is not a fused
    multi-record blob, or the dropped entry is a fused multi-record candidate —
    those the #221/#225 recovery pass (`WCMTemplateGenerator._recover_unrendered_records`,
    `stage_6_word_template.py`, called on the PRE-dedup snapshot so a dropped
    entry's lines are still checked) re-verifies line by line against the
    rendered document. A single-line entry that merely SCORES similar is the
    #227 loss class: distinct records sharing role/date/venue boilerplate (7 of
    8 drops on 2Q1_ZQ were real content loss, all single-line); a distinct
    record swallowed by a fused table blob is the same loss class (C0ZGFW).

    #666: the two non-verbatim branches also require `_dates_compatible` --
    a dropped entry that states a date range the kept entry does not carry is a
    different record (a second committee term, a second grant), and no text
    similarity signal can see that. For a `code` whose section renders
    fields, the verbatim and token-containment branches also refuse a drop
    that would lose the dropped record (`_record_would_be_lost`): one the
    kept text fused beside the record its fields describe, or a bare name
    inside a longer bare name ("Widgets" in "Widgets Quarterly"), unless
    another entry of `document` (every pre-dedup entry of the run, less the
    `dropped_ids` dedup has already dropped) carries it.
    `_record_lines()` finds no line in prose for the #221/#225 recovery pass
    to re-verify.

    #666 (EBYSBC): verbatim containment starts and ends on word boundaries
    (`_verbatim_contained`), the date test reads months and days, a differing
    part number, standalone Roman numeral or lettered series member
    (`_other_series_member`) refuses like a trial phase, and in a teaching or
    talk list a group line between the two entries refuses
    (`_group_header_between`).

    #666 (WZSMUO): a short unnamed entry whose words are fewer than the kept
    entry's journal name and all inside it is another journal
    (`_shorter_journal_than_kept`): "Cell" beside "Cell Reports".

    #666 (LTTYWI, BLBVPD, IXJMKS): an undated row under another year head
    than the kept row is another term (`_under_another_year_head`), an ad
    hoc review beside a standing seat is another record (`_ad_hoc_apart`),
    and an award of another class is another award (`_class_year_apart`)."""
    dropped_text = dropped_entry.get('text') or ''
    kept_text = kept_entry.get('text') or ''
    verbatim = _verbatim_contained(dropped_text, kept_text)
    if dropped_entry.get(FANNED_OUT_FROM):
        # #983: a record fanned out of a multi-record entry is a single short
        # line, so the two branches below (token containment; fused-blob
        # recovery) approve dropping it against any longer entry that shares
        # its words -- "Co-Leader, Cancer Epidemiology (2012-)" against a
        # "Co-Leader, Cancer Epidemiology Program (2012-2015)" -- and the
        # recovery pass never re-checks it, because a single segment is not a
        # record line. Its text also carries none of the context it inherited
        # from its parent, so the same mentee listed under two fellowships has
        # identical text and different fields. Only a verbatim copy with the
        # same fields may go; anything else is kept, like the other
        # single-record case (#227).
        return verbatim and (dropped_entry.get('extracted_fields')
                             == kept_entry.get('extracted_fields'))
    if _group_header_between(dropped_entry, kept_entry, code, document):
        return False  # #666: listed under two group lines, so two records
    if _under_another_year_head(dropped_entry, kept_entry, code, document):
        return False  # #666: an undated row takes another year head's years
    if _ad_hoc_apart(dropped_entry, kept_entry, code):
        return False  # #666: an ad hoc review and a standing seat are two records
    if _undated_split_record_absorbs_date(dropped_entry, kept_entry, document):
        return False  # #666: an undated split record does not vouch for a date
    if _shorter_journal_than_kept(dropped_entry, kept_entry, code):
        return False  # #666: a shorter journal name is another journal
    if verbatim:
        return not _record_would_be_lost(dropped_entry, kept_entry, code,
                                         document, True, dropped_ids or set())
    if _different_mentees(dropped_entry, kept_entry):
        return False  # #1181: a different mentee is a different record
    if not _dates_compatible(dropped_text, kept_text):
        return False  # #666: a different date is a different record
    if _class_year_apart(dropped_text, kept_text, code):
        return False  # #666: another class's award, or a class year is no date
    if not _trial_phases(dropped_text) <= _trial_phases(kept_text):
        return False  # #1106: a different trial phase is a different trial
    if not _part_numbers(dropped_text) <= _part_numbers(kept_text):
        return False  # #666: a different part is a different record
    if _other_series_member(dropped_text, kept_text):
        return False  # #666: another numeral or letter of a series is another record
    if not _counts(dropped_text) <= _counts(kept_text):
        return False  # #666: a different hours or student count is another occasion
    dropped_sig = _entry_signature_words(dropped_entry)
    if (len(dropped_sig) >= DEDUP_FULL_CONTAINMENT_MIN_TOKENS
            and dropped_sig <= _entry_signature_words(kept_entry)
            and len(_record_lines(kept_entry.get('text', ''))) < DEDUP_FUSED_BLOB_RECORD_LINES):
        return not _record_would_be_lost(dropped_entry, kept_entry, code,
                                         document, False, dropped_ids or set())
    return len(_record_lines(dropped_entry.get('text', ''))) >= UNRENDERED_MIN_RECORD_LINES


# A recovered table row's cell separator, as `recover_unclaimed_table_rows`
# (stage_2_entry_extraction.py, #420) always renders one: " | " when a
# physical table row's cells are joined, or a bare "\t" on the
# tab-separated fallback path. A row with more than two physical columns
# (Label, StartDate, EndDate) survives as one string with MORE than one
# separator in it -- `_recovered_row_value_cells` below relies on that to
# split every value cell out on its own, not just the first.
_CELL_SEPARATOR_RE = re.compile(r'[|\t]')


def _recovered_row_value_cells(text: str) -> list[str]:
    """Every VALUE cell of a stage-2 structurally recovered table row's raw
    text (`recover_unclaimed_table_rows`, #420): every cell after the row's
    own first cell (its label), split on `_CELL_SEPARATOR_RE`.

    A recovered row is usually one label and one value ("Award Source: |
    Fictional Research Foundation"), but `recover_unclaimed_table_rows`
    joins the WHOLE physical table row regardless of column count -- a
    3-column row (Label, StartDate, EndDate) survives as one string with
    TWO separators in it ("Duration of support: | 00/2021 | 00/2022"), and
    both halves of that date range are their own value cell, checked
    independently by `recovered_row_already_rendered` below rather than
    rejoined into one string: a rejoined "00/2021 | 00/2022" can never match
    the rendered document verbatim, since nothing renders the raw separator
    character, so treating it as a single cell would only ever hide a real
    match, never produce a false one.

    A row with no separator at all (malformed -- `recover_unclaimed_table_rows`
    always emits label|value) has no label to split off, so the whole text
    is itself the one value cell: there is no safer fallback, and returning
    no cells at all would make the caller treat the row as vacuously safe to
    drop (see `recovered_row_already_rendered`'s own `if not cells` guard).
    """
    cells = [c.strip() for c in _CELL_SEPARATOR_RE.split(text or '')]
    return cells[1:] if len(cells) > 1 else cells


def _is_trivial_value_cell(cell: str) -> bool:
    """A value cell with no alphanumeric character (blank, or
    separator/punctuation-only -- e.g. the empty second cell of
    "Non-financial support: | ") carries no content that dropping the row
    could lose, so it never has to be found rendered anywhere. Symmetrically,
    it must never by itself justify a drop either: a row whose every cell is
    trivial has nothing confirmed rendered and stays (the `if not cells`
    guard in `recovered_row_already_rendered`)."""
    return not any(ch.isalnum() for ch in cell)


def _collapse_and_fold(text: str) -> str:
    """Casefold + whitespace-COLLAPSED (never whitespace-deleted)
    normalization for `recovered_row_already_rendered`'s containment check.

    Deliberately not this module's own `_squash` (whitespace-FREE, used by
    `_drop_is_safe` above): `recovered_row_already_rendered` joins every
    already-rendered line of the document into ONE string before searching
    it, and a `_squash`-style join would delete the very whitespace that
    keeps two unrelated adjacent lines apart -- gluing "...Foundation" and
    "1%..." into "...Foundation1%..." risks a match that never existed as
    contiguous rendered text. Collapsing each run of whitespace to a single
    space keeps a real word boundary at every line join instead of removing
    it. Kept as its own small copy rather than importing
    `normalization/pii.py`'s near-identical `_collapse_whitespace`, for the
    same reason `_value_contained_in_text` below doesn't import that
    module's containment helper either: that module decides whether a value
    is PROTECTED personal data, a data-governance question; this one decides
    whether a value RENDERED, a content-loss question, and the two must stay
    free to diverge (module docstring: nothing here may import
    `stage_6_word_template`, and the same boundary applies one level down to
    the PII module).
    """
    return re.sub(r'\s+', ' ', str(text or '')).strip().casefold()


def _value_contained_in_text(value: str, text: str) -> bool:
    """Whitespace-collapsed, case-folded containment of `value` in `text`,
    aligned on a word boundary at BOTH ends: an alphanumeric edge of `value`
    may not sit against another alphanumeric character in `text`.

    Without the boundary, '5%' is a literal substring of '25%', and '2021'
    is a literal substring of '20215' at the TRAILING edge -- a short value
    from one record could read as "already rendered" merely because a
    longer, unrelated value happens to contain the same characters, at
    either end. Same shape as `normalization/pii.py`'s
    `_pii_containment_pattern`, kept as its own copy for the reason
    `_collapse_and_fold` above gives.
    """
    folded_value = _collapse_and_fold(value)
    if not folded_value:
        return False
    folded_text = _collapse_and_fold(text)
    body = re.escape(folded_value)
    lead = r'(?<![a-z0-9])' if folded_value[0].isalnum() else ''
    trail = r'(?![a-z0-9])' if folded_value[-1].isalnum() else ''
    return re.search(lead + body + trail, folded_text) is not None


def recovered_row_already_rendered(entry: dict, rendered_lines: list[str]) -> bool:
    """True when a stage-2 structurally-recovered table row (#420,
    `recover_unclaimed_table_rows`) is safe to drop from the Appendix because
    EVERY non-trivial value cell of its own raw text already appears,
    verbatim (word-boundary, whitespace-collapsed, case-folded), somewhere
    in the document's ALREADY-RENDERED body -- never the Appendix itself,
    which has not been written yet when this runs (`_drop_recovered_row_duplicates`,
    stage_6_word_template.py, always calls this before
    `_add_remaining_to_appendix`).

    A row with zero non-trivial value cells (its only content is a bare
    label) is never dropped by this: there is no value to confirm, so
    "confirmed rendered" cannot be true, and the row stays. Because this
    only ever REQUIRES more matches before allowing a drop, it can never
    treat an unrendered value as rendered -- the one failure mode that
    would actually lose content.

    Deliberately provenance-blind (round 3): earlier rounds required a
    row's raw text to ALSO be a verbatim substring of one specific parent
    entry (`recovered_row_duplicates_parent`, now removed), then scoped the
    render check to that one parent's own rendered block
    (`_parent_rendered_block`, also removed) -- both meant to stop an
    unrelated entry's render from vouching for a row it had nothing to do
    with. That scoping was itself the bug: it picked the first rendered
    block carrying ANY one of a parent's identifying field values, and a
    value shared across records -- most commonly a funding agency, shared
    across a faculty member's own grants -- resolved two different parents
    to the SAME block. A grant whose own table rendered nothing for a field
    could still have its recovered row dropped because a same-agency
    sibling's block happened to match: real content loss, the exact failure
    this function exists to prevent, and no content-keyed scoping is safe
    against it, because `extracted_fields` carries no way to tell two
    records' shared values apart.

    The fix drops the identity question entirely: this never asks WHICH
    entry rendered a value, only whether the value is somewhere in the
    document the reader will already see. That is also the actual guarantee
    an Appendix drop needs -- a duplicate line adds noise, a missing one
    loses content, and "printed by a different record" is still printed.
    A row can therefore be dropped even when the match is coincidental (two
    grants that happen to share one field's exact text); the trade is a
    little provenance precision for a rule that is unconditionally simpler
    and unconditionally content-safe. The inverse case -- a value stage 6
    REFORMATS on the way to a render slot (a raw "00/2021" cell rendered as
    "2021") -- will not verbatim-match and so is correctly NOT confirmed:
    the row stays, printed once more than strictly necessary. Content
    duplication, never content loss, is the only direction this function is
    allowed to be wrong in.

    `entry.get('recovered_row')` gates this exactly as every earlier round
    did: only stage 2's structural backstop sets that flag, so an ordinary
    model-attested entry that happens to be a text subset of another still
    goes through `deduplicate_entries`'s Jaccard/containment path above,
    never this one.
    """
    if not entry.get('recovered_row'):
        return False
    cells = [c for c in _recovered_row_value_cells(entry.get('text', '') or '')
             if not _is_trivial_value_cell(c)]
    if not cells:
        return False
    rendered_text = '\n'.join(rendered_lines)
    return all(_value_contained_in_text(cell, rendered_text) for cell in cells)


# #446: a citation's identity, compared before any text similarity. One paper
# listed twice (EBYSBC SJWASY-01, VVRTUC-06) or split by stage 2 into two
# entries that 5d completed into the same citation (MIFYLG-02, RNKYST-05,
# NDXXAD-02's PMID-only tail) renders twice: the copies differ by role notes,
# typos and author-list cuts, so the word-similarity loop below either misses
# them or `_drop_is_safe` refuses them. Bibliography codes only; S0 is the
# researcher-profile list, not citations.
_CITATION_CODE_RE = re.compile(r'S[1-9]')
_DOI_PREFIX_RE = re.compile(r'^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)', re.IGNORECASE)
_DOI_SHAPE_RE = re.compile(r'10\.\d{4,}/\S+')
# Two titles of one paper share at least this share of their significant words
# (Jaccard): a source typo or a PubMed spelling still agrees, and two abstracts
# under one supplement DOI (0.04-0.10 on the farm) or two protocol reports
# that differ by a patient group (0.71) do not.
_CITATION_TITLE_AGREEMENT = 0.8


def _citation_doi(pub: ResolvedPublication) -> str:
    """The DOI, lower-cased with any URL or `doi:` prefix and trailing period
    removed, or '' when the value is not DOI-shaped."""
    doi = _DOI_PREFIX_RE.sub('', pub.doi.strip()).rstrip('.').lower()
    return doi if _DOI_SHAPE_RE.fullmatch(doi) else ''


def _citation_pmid(pub: ResolvedPublication) -> str:
    """The PMID's digits, or '' when it has fewer than an identifier's."""
    pmid = re.sub(r'\D', '', pub.pmid)
    return pmid if len(pmid) >= _RECORD_ID_MIN_DIGITS else ''


def _citation_year(pub: ResolvedPublication) -> str:
    """The four-digit year the citation states, or ''."""
    match = _YEAR_RE.search(pub.year)
    return match.group(0) if match else ''


def _citation_identity_keys(entry: dict, pub: ResolvedPublication) -> set[tuple[str, str]]:
    """Keys that name one paper: its PMID, its DOI, its normalized title +
    journal + year, and its whole 5d citation when that names a title. Every
    text key needs a real title (not a 5d placeholder), so two thin fragments
    never share one by accident."""
    keys = {('pmid', pmid) for pmid in (_citation_pmid(pub),) if pmid}
    keys |= {('doi', doi) for doi in (_citation_doi(pub),) if doi}
    title = _alnum(pub.title)
    if not title or is_placeholder_title(pub.title, entry.get('text') or ''):
        return keys
    journal = _alnum(pub.journal or pub.book_title)
    year = _citation_year(pub)
    if journal and year:
        keys.add(('title_journal_year', f'{title}|{journal}|{year}'))
    if pub.formatted_citation:
        keys.add(('citation', _alnum(pub.formatted_citation)))
    return keys


def _first_author(pub: ResolvedPublication) -> str:
    """The first author's first word, lower-cased ('' when no author list)."""
    match = re.match(r"[^\W\d_][\w'-]*", pub.authors.strip())
    return match.group(0).lower() if match else ''


# The key kinds that name a paper by an identifier. A titled commentary carries
# the paper's own DOI or PMID ("Editorial: <its title>", by another author),
# and a Part I and Part II can share one DOI, so on these two kinds the first
# author and the part number still tell two records apart (#446).
_IDENTIFIER_KEY_KINDS = frozenset({'pmid', 'doi'})

# Where one author ends and the next begins in an author list: a comma or
# semicolon, or an "and" / "&" before the next author.
_AUTHOR_END_RE = re.compile(r'[,;]|\s(?:and|&)\s', re.IGNORECASE)

# "et al", "et al." or "and others", wherever it stands and with or without a
# comma before it: no author's name, so it is struck out of the list before
# any name is read (a comma left behind only ends an empty author).
_ET_AL_RE = re.compile(r'\b(?:et\.?\s*al\b\.?|and\s+others\b)', re.IGNORECASE)

# An all-capitals word this short is a run of initials ("JA", "JAB"), not a
# surname; a mixed-case one ("Li", "Wu") is read as a name.
_INITIALS_MAX_LETTERS = 3

# What splits an author's name into words: spaces and hyphens, so the parts
# of "Garcia-Lopez" are words of their own.
_NAME_WORD_SPLIT_RE = re.compile(r'[\s\-\u2010-\u2015]+')


def _author_names(author: str) -> set[str]:
    """One author's name words, initials left out, as accent-folded,
    casefolded letters, plus the whole name joined.

    Order-blind, so "J Xander" and "Xander J" both give {"xander"}. A
    hyphenated surname gives each part and the whole ("Garcia-Lopez M":
    {"garcia", "lopez", "garcialopez"}). A particle ("van", "der") is no name
    word of its own but stays in the joined name, so "van der Berg J" and
    "Vanderberg J" share "vanderberg". Empty when no word can be read as a
    name, or the author is a group ("The Toy Consortium", `_GROUP_AUTHOR_RE`)
    that one copy credits and the other leaves out; an empty set tells no two
    copies apart."""
    if _GROUP_AUTHOR_RE.search(author):
        return set()
    words = (re.sub(r'[^\w]', '', word) for word in _NAME_WORD_SPLIT_RE.split(author))
    letters = [_letters(norm(word)) for word in words
               if _letters(word) and not (word.isupper() and len(word) <= _INITIALS_MAX_LETTERS)]
    joined = ''.join(letters)
    return {word for word in letters if word not in _SURNAME_PARTICLES} | ({joined} - {''})


def _author_name_sets(pub: ResolvedPublication) -> list[set[str]]:
    """`_author_names` of each author in the list, first author first, with
    "et al" and "and others" struck out (`_ET_AL_RE`)."""
    authors = _ET_AL_RE.sub('', pub.authors.strip())
    return [_author_names(author) for author in _AUTHOR_END_RE.split(authors)]


def _first_author_names(pub: ResolvedPublication) -> set[str]:
    """`_author_names` of the first author in the list."""
    return _author_name_sets(pub)[0]


def _one_slip_apart(a: str, b: str) -> bool:
    """`_one_edit_apart`, or two neighbouring letters swapped ("xander" and
    "xadner")."""
    if _one_edit_apart(a, b):
        return True
    diff = [i for i, (x, y) in enumerate(zip(a, b, strict=False)) if x != y]
    return (len(a) == len(b) and len(diff) == 2 and diff[1] == diff[0] + 1
            and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])


def _names_an_author(names: set[str], author_sets: list[set[str]]) -> bool:
    """A name word in `names` is a name word of some author in `author_sets`,
    or one slip off it (`_one_slip_apart`)."""
    return any(a == b or _one_slip_apart(a, b)
               for a in names for author in author_sets for b in author)


def _first_authors_differ(first: ResolvedPublication, second: ResolvedPublication) -> bool:
    """Both copies name a first author, and neither copy's first author is an
    author of the other copy (`_names_an_author`). A CV's own spelling
    ("Xandr" or "Xadner" for "Xander", "Ibanez" for the accented form, "Garcia"
    for "Garcia-Lopez"), name order, or author order (co-first authors listed
    the other way round) is not a second author (RXYBVF)."""
    lists = _author_name_sets(first), _author_name_sets(second)
    return bool(lists[0][0] and lists[1][0]) and not (
        _names_an_author(lists[0][0], lists[1]) or _names_an_author(lists[1][0], lists[0]))


def _part_numbers_differ(first: tuple[dict, ResolvedPublication],
                         second: tuple[dict, ResolvedPublication]) -> bool:
    """Both printed titles, or both of the CV's own titles, name a part number
    (`_part_numbers`), and the numbers differ: a Part I and a Part II under one
    DOI. A title that names no part says nothing about which part it is."""
    (first_entry, first_pub), (second_entry, second_pub) = first, second
    return any(a and b and a != b for a, b in (
        (_part_numbers(first_pub.title), _part_numbers(second_pub.title)),
        (_part_numbers(_source_title(first_entry)), _part_numbers(_source_title(second_entry)))))


def _source_title(entry: dict) -> str:
    """The title stage 4 read off the CV itself ('' when it read none)."""
    fields = entry.get('extracted_fields') or {}
    title = fields.get('title') or fields.get('chapter_title')
    return title if isinstance(title, str) else ''


def _source_title_words(entry: dict) -> set[str]:
    """The significant words of the title stage 4 read off the CV itself,
    before any PubMed title replaced it."""
    return _significant_words(_source_title(entry))


def _titles_disagree(first: set[str], second: set[str]) -> bool:
    """Both titles known, and sharing under `_CITATION_TITLE_AGREEMENT` of their words."""
    return bool(first and second
                and len(first & second) / len(first | second) < _CITATION_TITLE_AGREEMENT)


def _citations_conflict(kind: str, first: tuple[dict, ResolvedPublication],
                        second: tuple[dict, ResolvedPublication]) -> bool:
    """True when two copies sharing a `kind` key say they are two records.

    The same printed citation (`citation`) is one record on the page whatever
    the fields say: a stage-2 split half can carry the wrong title field and
    the same 5d citation (VGHNZD). Any other shared key is a coincidence when
    the PMIDs, DOIs or years differ, or the titles disagree
    (`_titles_disagree`): the printed ones, because a journal supplement gives
    every abstract in it one DOI, and the CV's own, because PubMed enrichment
    can resolve two papers to one record and print one title twice (AQCLHS-02,
    VQFSDI). A shared title, journal and year is weaker still, so a different
    volume, pages or first author also refuses: a meeting lists posters with
    one title by different groups, and a letter and its reply share a title,
    journal and year (farm replay, 63 runs). A PMID or DOI match ignores those
    three: a CV's own volume typo or author spelling is not a second paper
    (RXYBVF). It still refuses when neither copy's first author is an author
    of the other copy (`_first_authors_differ`) or the part numbers differ
    (`_part_numbers_differ`): a titled commentary by another author under the
    paper's DOI, or a Part I and Part II under one DOI (#446)."""
    if kind == 'citation':
        return False
    (first_entry, first_pub), (second_entry, second_pub) = first, second
    if (_titles_disagree(_significant_words(first_pub.title), _significant_words(second_pub.title))
            or _titles_disagree(_source_title_words(first_entry), _source_title_words(second_entry))):
        return True
    pairs = [(_citation_pmid(first_pub), _citation_pmid(second_pub)),
             (_citation_doi(first_pub), _citation_doi(second_pub)),
             (_citation_year(first_pub), _citation_year(second_pub))]
    if kind == 'title_journal_year':
        pairs += [(_alnum(first_pub.volume), _alnum(second_pub.volume)),
                  (_alnum(first_pub.pages), _alnum(second_pub.pages)),
                  (_first_author(first_pub), _first_author(second_pub))]
    if kind in _IDENTIFIER_KEY_KINDS and (_first_authors_differ(first_pub, second_pub)
                                          or _part_numbers_differ(first, second)):
        return True
    return any(a and b and a != b for a, b in pairs)


def _citation_fullness(entry: dict, pub: ResolvedPublication) -> tuple[bool, int, int]:
    """Sort key for which copy of a citation to keep: a PubMed-enriched copy,
    then the one filling more fields, then the longer source text."""
    filled = (pub.authors, pub.title, pub.journal, pub.book_title, pub.year, pub.volume,
              pub.issue, pub.pages, pub.doi, pub.pmid, pub.pmcid)
    return (entry.get('enrichment_status') == 'enriched', sum(map(bool, filled)),
            len(entry.get('text') or ''))


def _citation_identity_text(kind: str, pub: ResolvedPublication) -> str:
    """What the `kind` key matched on: the 5d citation for `citation`, else the
    title and year (the 5d citation or the authors when there is no title).
    Venue and authors are left out because each copy spells them its own way
    (an abbreviated journal, an "et al." cut)."""
    if kind == 'citation' or not pub.title:
        return pub.formatted_citation or pub.authors
    return '. '.join(part for part in (pub.title, _citation_year(pub)) if part)


def _citation_decision(kind: str, dropped: dict, kept: dict, code: str | None) -> dict:
    """The sidecar record of one identity drop, in `deduplicate_entries`'s
    shape so the doctor's `dedup_drops` lint reads it like any other.

    Its texts are what the two copies matched on, not their source lines:
    a stage-2 split half shares no source words with its other half, and a
    second listing carries the CV's own role notes, so on source text the
    lint would call every identity drop a lost record (farm A/B: 22 of 26 on
    one CV). On those it still warns when the two name different papers."""
    return {"metric": f"citation_{kind}", "jaccard": None, "containment": None,
            "title_containment": None,
            "dropped_text": _citation_identity_text(kind, resolve_publication(dropped))[:500],
            "kept_text": _citation_identity_text(kind, resolve_publication(kept))[:500],
            "dropped_fields": _decision_fields(dropped, code),
            "kept_fields": _decision_fields(kept, code)}


def _drop_citation_copies(entries: list[dict], code: str | None,
                          decisions: list[dict] | None,
                          dropped_ids: set[int]) -> list[dict]:
    """Keep one copy of each citation in a bibliography group (#446).

    Two entries are one paper when they share a `_citation_identity_keys` key
    and `_citations_conflict` finds nothing apart. The fuller copy
    (`_citation_fullness`) is kept wherever it sits; the section sorts by
    year, so position does not matter. A fanned-out record (#983) is left to
    the similarity loop's own rule, and so is an in-press listing stage 5
    superseded: stage 6 renders it as a tracked deletion of the published
    copy it names, so keeping it over that copy would lose the paper once the
    deletion is accepted (BMAMWE)."""
    kept_by_key: dict[tuple[str, str], int] = {}
    pubs = [resolve_publication(entry) for entry in entries]
    dropped: set[int] = set()
    for index, entry in enumerate(entries):
        if entry.get(FANNED_OUT_FROM) or entry.get('in_press_superseded') is True:
            continue
        keys = _citation_identity_keys(entry, pubs[index])
        match = next(((key, kept_by_key[key]) for key in sorted(keys)
                      if key in kept_by_key and kept_by_key[key] not in dropped
                      and not _citations_conflict(key[0], (entry, pubs[index]),
                                                  (entries[kept_by_key[key]],
                                                   pubs[kept_by_key[key]]))),
                     None)
        if match is not None:
            key, other = match
            keep, drop = sorted((other, index),
                                key=lambda i: _citation_fullness(entries[i], pubs[i]),
                                reverse=True)
            dropped.add(drop)
            dropped_ids.add(id(entries[drop]))
            if decisions is not None:
                decisions.append(_citation_decision(key[0], entries[drop],
                                                    entries[keep], code))
            if keep == other:
                continue
        for key in keys:
            kept_by_key.setdefault(key, index)
            if kept_by_key[key] in dropped:
                kept_by_key[key] = index
    return [entry for index, entry in enumerate(entries) if index not in dropped]


# #1445: the decision metric `_drop_split_siblings` writes, so the sidecar
# tells a sibling dropped with its parent from a similarity drop.
SPLIT_SIBLING_METRIC = 'split_sibling_of_dropped_parent'


def _is_sibling_of(entry: dict, last_record: dict) -> bool:
    """Whether `entry` is an earlier record of the stage-4 records list whose
    last record (the parent, `LAST_STAGE4_RECORD`) is `last_record`."""
    provenance = entry.get(FANNED_OUT_FROM) or {}
    marker = last_record[LAST_STAGE4_RECORD]
    return (provenance.get('key') == marker.get('key')
            and provenance.get('count') == marker.get('count')
            and entry.get('element_idx_start') == last_record.get('element_idx_start'))


def _sibling_is_held(sibling: dict, kept_entry: dict, code: str | None) -> bool:
    """Whether every word `sibling`'s row would print, its years included, is
    already in the kept entry's text."""
    rendered = _RENDERED_FIELDS.get(code or '', frozenset())
    words = _rendered_words(sibling.get('extracted_fields') or {}, rendered)
    return words <= _significant_words(kept_entry.get('text') or '')


def _drop_split_siblings(entries: list[dict], drops: list[tuple[int, int]],
                         drop_indices: set[int], code: str | None,
                         decisions: list[dict] | None, dropped_ids: set[int]) -> None:
    """Drop the earlier records of a stage-4 records list whose parent dedup
    dropped, when the entry it was dropped against holds them too (#1445,
    EOAHMI GHCIXA 70).

    The last record carries the parent's whole text, so dedup weighs the
    parent through it and drops it when another entry holds that text. The
    earlier records carry their own short lines and, as fanned-out records,
    are never dropped by similarity (`_drop_is_safe`), so each one rendered
    on as an orphan copy of a row the kept entry already prints. A sibling
    goes only when every word its row would print is in the kept entry
    (`_sibling_is_held`); one that adds anything stays.
    """
    for dropped, kept in drops:
        if LAST_STAGE4_RECORD not in entries[dropped]:
            continue
        for index, entry in enumerate(entries):
            if (index in drop_indices or index == kept
                    or not _is_sibling_of(entry, entries[dropped])
                    or not _sibling_is_held(entry, entries[kept], code)):
                continue
            drop_indices.add(index)
            dropped_ids.add(id(entry))
            if decisions is not None:
                decisions.append({
                    "metric": SPLIT_SIBLING_METRIC,
                    "jaccard": None, "containment": None, "title_containment": None,
                    "dropped_text": (entry.get('text') or '')[:500],
                    "kept_text": (entries[kept].get('text') or '')[:500],
                    "dropped_fields": _decision_fields(entry, code),
                    "kept_fields": _decision_fields(entries[kept], code),
                })


def deduplicate_entries(entries: list[dict], verbose: bool = False,
                        require_date_overlap: bool = False,
                        decisions: list[dict] | None = None,
                        code: str | None = None,
                        document: list[dict] | None = None,
                        dropped_ids: set[int] | None = None) -> list[dict]:
    """Remove near-duplicate entries within a code group.

    Uses two metrics to catch duplicates:
    1. Jaccard similarity (symmetric) — catches similar-length entries
    2. Containment (asymmetric) — catches when a short entry is a subset
       of a longer one (e.g., brief mention vs. detailed description)

    When two entries are duplicates, the longer / more detailed one is kept.

    If require_date_overlap is True, text-similar entries are only deduped when
    their date ranges match or overlap.  This prevents false positives on career
    progression sequences (e.g., Intern -> Resident -> Chief Resident at same
    institution) where word overlap is high but dates differ.

    If decisions is a list, every drop is appended to it as a dict (metric
    values plus dropped/kept text) so the caller can persist the decision
    trail for the run doctor (#227: at these thresholds a drop is not always
    a true duplicate).

    `code` is the group's taxonomy code and `document` every entry of the
    run, all codes, before dedup: the #666 fused-record check in
    `_drop_is_safe` reads the code's rendered fields and searches the
    document for another mention of a record before refusing a drop. With no
    code that check never refuses; with no document no other entry is known.
    `dropped_ids` holds the `id()` of every entry of `document` dedup has
    dropped so far; this call adds its own drops, so a caller that shares one
    set across its groups keeps a dropped entry from vouching for another
    drop in any later group, and two identical copies keep exactly one.

    A bibliography code (S1-S9) first keeps one copy of each citation by
    identity (`_drop_citation_copies`, #446): PMID, DOI, title + journal +
    year, or the whole 5d citation.

    Pairwise and order-dependent by design, not clustered: entries are
    compared left-to-right and a drop removes that index from further
    comparison (see the `break` below), so for A~B~C where A and C aren't
    themselves similar enough to pair directly, which of {A, B} survives
    depends on iteration order. Deliberate trade-off, not an oversight —
    building duplicate clusters and picking one canonical record per cluster
    would need its own corpus-verified safety pass; documented here instead
    of changed blind.
    """
    if len(entries) <= 1:
        return entries

    if dropped_ids is None:
        dropped_ids = set()
    if code and _CITATION_CODE_RE.fullmatch(code):
        entries = _drop_citation_copies(entries, code, decisions, dropped_ids)
    sigs = [_entry_signature_words(e) for e in entries]
    titles = [_entry_title_words(e) for e in entries]
    drop_indices = set()
    drops: list[tuple[int, int]] = []

    for i in range(len(entries)):
        if i in drop_indices:
            continue
        for j in range(i + 1, len(entries)):
            if j in drop_indices:
                continue
            if not sigs[i] or not sigs[j]:
                continue
            intersection = sigs[i] & sigs[j]
            union = sigs[i] | sigs[j]
            smaller = min(len(sigs[i]), len(sigs[j]))

            jaccard = len(intersection) / len(union) if union else 0
            containment = len(intersection) / smaller if smaller else 0

            # Also check title-only similarity (text before first tab).
            # This catches cases where both entries describe the same activity
            # but have very different narrative descriptions.
            # Require at least 4 significant words in the smaller title to avoid
            # false positives from short generic titles like "Emergency Medicine".
            title_containment = 0.0
            if titles[i] and titles[j]:
                title_smaller = min(len(titles[i]), len(titles[j]))
                if title_smaller >= 4:
                    title_inter = titles[i] & titles[j]
                    title_containment = len(title_inter) / title_smaller if title_smaller else 0

            is_dup = jaccard >= 0.6 or containment >= 0.75 or title_containment >= 0.8

            # Safety check: if full-text metrics trigger but titles are clearly
            # different, these are likely distinct items at the same venue (e.g.,
            # two different talks at the same grand rounds session).
            if is_dup and title_containment < 0.8 and titles[i] and titles[j]:
                title_union = titles[i] | titles[j]
                title_jaccard = (len(titles[i] & titles[j]) / len(title_union)
                                 if title_union else 0)
                if title_jaccard <= 0.25 and min(len(titles[i]), len(titles[j])) >= 3:
                    if verbose:
                        print(f"    Dedup: skipping (different titles, "
                              f"title_jaccard={title_jaccard:.2f}) "
                              f"[{entries[i].get('text', '')[:50]}...]")
                    is_dup = False

            # For career-progression codes, require date overlap to confirm
            if is_dup and require_date_overlap:
                if not _dates_overlap_or_match(entries[i], entries[j]):
                    if verbose:
                        print(f"    Dedup: skipping (dates differ) "
                              f"[{entries[i].get('text', '')[:50]}...]")
                    is_dup = False
            if is_dup:
                # Keep the longer (more detailed) entry
                len_i = len(entries[i].get('text', ''))
                len_j = len(entries[j].get('text', ''))
                drop = j if len_i >= len_j else i
                kept = i if drop == j else j
                if not _drop_is_safe(entries[drop], entries[kept],
                                     code, document, dropped_ids):
                    if verbose:
                        print(f"    Dedup: skipping (similar but not "
                              f"verbatim-contained, single record — keeping "
                              f"both, #227) "
                              f"[{entries[drop].get('text', '')[:50]}...]")
                    continue
                if jaccard >= 0.6:
                    metric = f"jaccard={jaccard:.2f}"
                elif containment >= 0.75:
                    metric = f"containment={containment:.2f}"
                else:
                    metric = f"title={title_containment:.2f}"
                if verbose:
                    print(f"    Dedup: dropping entry ({metric}), "
                          f"keeping [{entries[kept].get('text', '')[:60]}...]")
                if decisions is not None:
                    decisions.append({
                        "metric": metric,
                        "jaccard": round(jaccard, 2),
                        "containment": round(containment, 2),
                        "title_containment": round(title_containment, 2),
                        "dropped_text": entries[drop].get('text', '')[:500],
                        "kept_text": entries[kept].get('text', '')[:500],
                        "dropped_fields": _decision_fields(entries[drop], code),
                        "kept_fields": _decision_fields(entries[kept], code),
                    })
                drop_indices.add(drop)
                drops.append((drop, kept))
                dropped_ids.add(id(entries[drop]))
                if drop == i:
                    # i is gone: it must not keep vouching to drop later j's
                    # (observed over-drop vector in the 2Q1_ZQ S8 trace, #227)
                    break

    _drop_split_siblings(entries, drops, drop_indices, code, decisions, dropped_ids)
    if drop_indices:
        return [e for idx, e in enumerate(entries) if idx not in drop_indices]
    return entries
