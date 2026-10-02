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

from .fan_out import _FORMATTED_KEYS, _RENDERED_FIELDS, FANNED_OUT_FROM
from .normalization import _squash
from .parsing import _dates_overlap_or_match
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
_ROMAN_PHASE = {'i': '1', 'ii': '2', 'iii': '3', 'iv': '4'}


def _trial_phases(text: str) -> set[str]:
    """Every trial phase the text names, Roman numerals as digits."""
    return {_ROMAN_PHASE.get(n.lower(), n)
            for group in _TRIAL_PHASE_RE.findall(text)
            for n in re.findall(_PHASE_NUMERAL, group, re.IGNORECASE)}


def _dates_compatible(dropped_text: str, kept_text: str) -> bool:
    """False when the dropped text states a date the kept text does not carry.

    Each dropped range must overlap a RANGE the kept text states (a kept entry
    that only mentions the two boundary years in passing does not vouch for it);
    each bare dropped year must appear in the kept text or fall inside one of
    its ranges. Undated dropped text is compatible with anything. Doubt keeps
    the entry: a surviving duplicate is visible to a reader, a lost record is not.
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
    return True


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
# society, a D2 hospital, an S3 textbook title. The other name keys (R location,
# H award, Q3 panel, P committee) were 0 real on IPXFBA, because a fused row's
# fragment reads as a different name.
_DECISION_NAME_ANY_CODE = ('journal_name',)
_DECISION_NAME_BY_CODE = {'I': ('organization',), 'D2': ('institution',),
                          'S3': ('title',)}
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
      (`_distinct_bare_names`): "Widgets" is not "Widgets Quarterly".

    Each shape also needs that no other entry of `document` carries the
    record, so keeping this copy cannot print it twice. Entries in
    `dropped_ids` (dropped already, this group or an earlier one) carry
    nothing: two identical copies must not each vouch for the other's drop.
    One more shape needs no such check: the fields say it is another record
    (`_distinct_record_label`: another institution, a companion title, a
    grant's title alone, a bare place). Doubt drops, as it did before.
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
    if _distinct_record_label(code, dropped_fields, kept_fields):
        return True
    if ((verbatim and not _significant_words(name) <= _rendered_words(kept_fields, rendered))
            or _names_a_sibling(kept_entry, kept_fields, rendered, name)):
        return not any(_names_record(entry.get('text') or '', name)
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
# ("Senior Fellow") at two hospitals is two appointments.
_INSTITUTION_FIELD_BY_CODE = {'D1': 'institution', 'D2': 'institution', 'D3': 'organization'}


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
    Regional" share every word and are still two clinics. A longer
    abbreviation ("Univ." for "University"), a name split in two ("North
    Shore", "Northshore") and one half of a dual name are not recognised:
    they keep both rows (the safe side).
    """
    key = _INSTITUTION_FIELD_BY_CODE.get(code or '')
    dropped, kept = dropped_fields.get(key), kept_fields.get(key)
    if not (isinstance(dropped, str) and isinstance(kept, str)):
        return None
    dropped_words, kept_words = _ordered_words(dropped), _ordered_words(kept)
    if (not dropped_words or not kept_words
            or _is_subsequence(dropped_words, kept_words)
            or _is_subsequence(kept_words, dropped_words)):
        return None
    return dropped


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


def _distinct_record_label(code: str | None, dropped_fields: dict,
                           kept_fields: dict) -> str | None:
    """What the dropped record is called when its fields say it is not the kept
    record, else None."""
    return (_different_institution(code, dropped_fields, kept_fields)
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


# `_lists_name`: the separators between the items of a listed text.
_LIST_ITEM_SEPARATOR_RE = re.compile(r'[,;:|\t\n]')


def _lists_name(entry: dict, name: str, key: str, code: str | None,
                dropped_text: str) -> bool:
    """True when `entry` carries the record called `name` itself.

    Two ways, and no other (#666: a fellowship's `specialty`, a degree's
    `degree` or a citation's journal vouched for a reviewer row that only
    shared a word with them):

    - an item of its text is exactly that name, and the entry is of the same
      code (the record's own list: "Gizmo Review, Widgets");
    - its `key` field, the one that named the dropped record, holds exactly
      that name and the row says the same about it as the dropped row
      (`_same_row`). A longer name holding it ("Widgets Quarterly") is
      another record.
    """
    target = _alnum(name)
    text = entry.get('text') or ''
    if entry.get('taxonomy_code') == code and any(
            _alnum(item) == target for item in _LIST_ITEM_SEPARATOR_RE.split(text)):
        return True
    fields = entry.get('extracted_fields') or {}
    return (_alnum(str(fields.get(key) or '')) == target
            and _same_row(text, dropped_text, name))


def _same_row(text: str, dropped_text: str, name: str) -> bool:
    """True when `text` says no more about `name` than the dropped row does: the
    letters left of each once the name is cut out are the same (role and
    status; the dates may differ), or `text` is the bare name. "Widgets,
    Editorial Board" is not "Widgets, Associate Editor"."""
    residue = _row_residue(text, name)
    return residue == '' or (residue is not None
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
    to re-verify."""
    dropped_squashed = _squash(dropped_entry.get('text', ''))
    verbatim = bool(dropped_squashed
                    and dropped_squashed in _squash(kept_entry.get('text', '')))
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
    if verbatim:
        return not _record_would_be_lost(dropped_entry, kept_entry, code,
                                         document, True, dropped_ids or set())
    if _different_mentees(dropped_entry, kept_entry):
        return False  # #1181: a different mentee is a different record
    if not _dates_compatible(dropped_entry.get('text') or '',
                             kept_entry.get('text') or ''):
        return False  # #666: a different date is a different record
    if not _trial_phases(dropped_entry.get('text') or '') <= _trial_phases(
            kept_entry.get('text') or ''):
        return False  # #1106: a different trial phase is a different trial
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
    sigs = [_entry_signature_words(e) for e in entries]
    titles = [_entry_title_words(e) for e in entries]
    drop_indices = set()

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
                dropped_ids.add(id(entries[drop]))
                if drop == i:
                    # i is gone: it must not keep vouching to drop later j's
                    # (observed over-drop vector in the 2Q1_ZQ S8 trace, #227)
                    break

    if drop_indices:
        return [e for idx, e in enumerate(entries) if idx not in drop_indices]
    return entries
