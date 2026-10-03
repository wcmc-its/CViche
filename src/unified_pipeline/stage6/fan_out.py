"""#983: one entry, several sibling records -> one entry per record.

Stage 4 sometimes separates the records of a multi-record entry correctly and
then files them under a key the taxonomy code's schema does not define:
`awards: [3 dicts]` on an H entry, `committees: [4 dicts]` / `entries: [3
dicts]` on a P entry, `mentees`, `degrees`, `licenses`, `journals` ... The
schema's own fields (`award_name`, `committee_name`, ...) stay empty, so every
section renderer, which reads the schema's keys and nothing else, falls back to
the entry's raw text: one table row with literal tabs, empty Role/Date cells
and (until #983's second half) a cut-off tail.

`fan_out_multi_record_entries` runs once per render, before the #820 PII pass
and before dedup, and replaces such an entry with one child entry per record,
so both of those see -- and the renderers render -- each record on its own.

Pure and dependency-free. The schema is PASSED IN (`{code: field names}`)
rather than imported: nothing under `stage6/` may import `stage4`
(`tests/test_stage6_normalization_import_direction.py` pins that absence), so
`stage_6_word_template.py` hands over `stage4.schemas.FIELD_SCHEMAS`.

What is deliberately NOT fanned out:

- a list that is not made of dicts (a list of strings is a list of values).
  A one-item list IS a record (`_MIN_LIST_ITEMS`), and a list is looked for
  one level down too, inside an object under a key the schema does not define;
- a list any of whose dicts shares no key with the schema (plus the
  `_EXTRA_RECORD_KEYS` the code's items are known to carry) -- web181's K2
  `mentees: [{name, year}]` is the named example. Its items name nothing the K2
  renderer reads, so a child built from one would render empty and the entry's
  raw text would be lost;
- an entry a stage-5 formatter already rendered whole (`formatted_text` /
  `formatted_citation`), which would repeat that rendering on every child;
- an entry whose text has a token no rendered field holds (`_fields_carry_text`);
- an entry that carries more than one such list, or an item that carries a key
  outside the schema next to schema keys (web228's K4 `sessions: [{date, title,
  duration}]`: `title` is the session's own name and no K4 renderer reads it, so
  each child would render the parent's activity title and the date and lose the
  session title).

A second, separate source of records (`records_key`, stage 4's
`STAGE4_RECORDS_KEY`, handed in by stage 6 like the schema): when the LLM
returned 2+ items for one entry, stage 4 keeps them all there and leaves the
entry's scalar fields as the LAST item, which is all the entry rendered before.
`_stage4_children` splits that list with its own rules, since the parent is
known to be the last record: the last child is the parent, scalars and text
as they stand, and every earlier child is its own record alone. The children then
render everything the parent rendered plus the earlier records, so none of the
guards above that compare a child against the entry's TEXT apply; only the
ones about what a child can render do (a code missing from `_RENDERED_FIELDS`,
a stage-5 rendering, a record with no rendered value). A declined list is
handed to the generic rules above as if that key were absent, so they decide
what they decided before the key existed.
"""
from __future__ import annotations

import copy
import re
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from types import MappingProxyType
from typing import Any

from unified_pipeline.core.text_norm import norm
from unified_pipeline.core.two_digit_year import expand_two_digit_year
from unified_pipeline.stage6.formatting.dates import format_date_for_section
from unified_pipeline.stage6.parsing.dates import CURRENT_DATE_VALUES, _MONTH_NAME_TO_NUM

# Provenance key written on every child. Shows which list the record came from
# and where in it: `{'key': 'awards', 'index': 1, 'count': 3}`.
FANNED_OUT_FROM = 'fanned_out_from'

# The same provenance, written instead on the last child of stage 4's records
# list, which is the parent itself (`_stage4_children`). That child keeps the
# parent's whole text and no `FANNED_OUT_FROM`, so dedup weighs it as it
# weighed the parent; dedup and the doctor do not read this key. The scope
# rules read it (`record_scope`, `record_text`): the last record is a record
# of the list like its siblings (EBYSBC E34, BZZNRL 137).
LAST_STAGE4_RECORD = 'last_stage4_record'

# A list of records has at least this many; below it the entry is one record.
# Stage 4's own records list (`records_key`) is held to it.
_MIN_RECORDS = 2

# A list under a key the schema does not define is a record list from one item
# up: `additional_roles: [{role, start_date, end_date}]` beside the parent's own role is
# a second record (MRJDWE 101), as #1291 already treats a single object, and
# `appointments: [{...}]` under a parent holding only its dates is the record
# itself (the pilot's BFSUMA, #1187). `_parent_is_own_record` tells them apart.
_MIN_LIST_ITEMS = 1

# Where a record list sits in `extracted_fields`: its key, or (EBYSBC E6,
# ZGBCIT 36) an object's key and the key inside it
# (`additional_info.telehealth_licensures`). Named in `FANNED_OUT_FROM` and in
# the warning with the parts joined by `_PATH_SEPARATOR`.
ListPath = tuple[str, ...]
_PATH_SEPARATOR = '.'

# The text stage 2 fused N paragraphs into is tab-joined.
_TEXT_SEGMENT_SEPARATOR = '\t'

# What stage 4 joins several values of one field with when it puts them all
# in the parent ("Alpha Fund; Beta Trust"): see `_own_values`.
_JOINED_VALUE_SEPARATOR = ';'

# The built-text separator: the same one stage 2 uses between table cells, so
# a child's built text reads as the record line it stands for and stays visible
# to `_recover_unrendered_records`, which verifies pipe/tab record lines.
_BUILT_TEXT_SEPARATOR = ' | '

# A child renders from its fields, so a token no RENDERED field holds is lost:
# a paragraph on committee service that stage 4 reduced to six committees
# (web185), a service entry that also describes a grant (web240), or a field
# the target renderer never writes (P's `institution`, until #985 made P append
# it to the name cell: web240's group prefixes). The entry is
# fanned out only when EVERY token of its own text is held, with at least the
# multiplicity the text has, by a field the target code's renderer writes
# (`_RENDERED_FIELDS`). Zero uncovered tokens, not a tolerance: any
# "qualifier" a tolerance forgave was content the output no longer had.
# A token is a run of letters or digits, lowercased. `_STOPWORDS` are the
# connectives that carry no content and that a field never holds ("Chair of
# the Board" against `role: Chair`, `committee_name: Board`).
_STOPWORDS = frozenset({'a', 'an', 'and', 'at', 'for', 'in', 'of', 'on', 'the', 'to'})
_TOKEN_RE = re.compile(r'[a-z0-9]+')

# EBYSBC E6 (#1187): what the coverage test does NOT count as content, on
# both sides of the comparison (the entry's text and every rendered value).
# Each one kept a correctly extracted record list whole, and the list then
# rendered as nothing (KYOPUV 32, XELRLZ 19) or as one fused row (MIFYLG 136):
# - a month. The date column writes a month as a number or drops it, by the
#   section's format (`format_date_for_section`), for a fanned and an unfanned
#   record alike, so a date is compared by its year: "March 1988", "03/88" and
#   "3/1988" all read 1988 (a month name is dropped only beside a number, so
#   "May" in a name stays a word);
# - a two-digit year: "'88", the "96" of "1993-96" and the "02" of "3/02" are
#   the years 1988, 1996 and 2002, read through the same pivot stage 4 uses;
# - an open end: "current", "ongoing", "now" are what the date column writes
#   as "Present";
# - a list number ("1.", "2)") at the start of the text or of a tab segment:
#   furniture, not content (MRJDWE 99);
# - a section label the CV writes in front of its first record's date
#   ("Positions      March '88-May '89 Lecturer ...", the KYOPUV 32 shape): one to
#   three words set off by a colon, a tab or a run of spaces, then a date.
#   Dropped only when every record carries its own `_ROLE_FIELDS` value, so
#   the label cannot be the role the records left out ("Chair: 2010-2012
#   Alpha Board, Beta Board" keeps the entry whole; so does every entry of a
#   code with no such field).
_OPEN_END_TOKEN = 'present'
_MONTH_WORDS = '|'.join(sorted(_MONTH_NAME_TO_NUM, key=len, reverse=True))
_MONTH_NAME_RE = re.compile(rf"\b(?:{_MONTH_WORDS})\b\.?(?=[\s,]*['‘’`]?\d)", re.IGNORECASE)
_APOSTROPHE_YEAR_RE = re.compile(r"['‘’`](\d{2})(?!\d)")
_MONTH_YEAR_RE = re.compile(r'(?<![\d/])(\d{1,2})/(?:(\d{4})|(\d{2}))(?![\d/])')
_SHORT_RANGE_END_RE = re.compile(r'(?<!\d)(\d{4})(\s*[-–—]\s*)(\d{2})(?![\d/])')
_LIST_NUMBER_RE = re.compile(r'(^|\t|\n)\s*(?:\d{1,3}[.)]|[a-z]\))(?=\s)', re.IGNORECASE)
_LEADING_LABEL_RE = re.compile(
    r"^\s*[A-Za-z]+(?: [A-Za-z]+){0,2}(?:\s*:\s*|\t\s*| {2,})"
    rf"(?=['‘’`]?\d|(?:{_MONTH_WORDS})\b)", re.IGNORECASE)
# The fields that name a record's role or title, per `_RENDERED_FIELDS`.
_ROLE_FIELDS = frozenset({'leadership_role', 'membership_type', 'role', 'title'})
_MONTHS_IN_YEAR = 12
_CENTURY = 100
# "2019-07" is July 2019, not 2019-2107: a two-digit end read as a span
# longer than this is not a year range and is left as written.
_MAX_SHORT_RANGE_YEARS = 50

# Per taxonomy code, the `extracted_fields` keys its section renderer writes
# into the document, read off the renderer by rendering one entry per code with
# a unique marker in every schema field and listing the markers that reach the
# .docx while the entry's own text is a neutral line
# (`test_rendered_fields_match_what_each_section_writes` repeats that probe, so a
# renderer that starts or stops reading a field fails it). `narrative` is in no
# set: nothing writes it. A code missing here is never fanned out. That includes
# every code whose section writes the entry's TEXT and no field (E, G, J, K2-K5,
# L1, L2, M1, M2, N1, N3, N4, S0, T; `_TEXT_RENDERED_CODES`): a child of one would
# render only its built text, which does not carry the parent's scalars.
_RENDERED_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType({
    'B1': frozenset({'degree', 'discipline', 'institution', 'year'}),
    'B2': frozenset({'institution', 'program_name', 'year'}),
    'C': frozenset({'end_date', 'institution', 'role', 'specialty', 'start_date', 'training_type'}),
    'D1': frozenset({'department', 'end_date', 'institution', 'start_date', 'title'}),
    'D2': frozenset({'department', 'end_date', 'institution', 'start_date', 'title'}),
    'D3': frozenset({'department', 'end_date', 'organization', 'start_date', 'title'}),
    'F1': frozenset({'expiration_date', 'issue_date', 'license_number', 'state_country'}),
    'F2': frozenset({'certifying_board', 'recertification_date', 'specialty', 'year_certified'}),
    'H': frozenset({'award_name', 'date', 'granting_body'}),
    'I': frozenset({'end_date', 'membership_type', 'organization', 'start_date'}),
    'K1': frozenset({'course_code', 'course_title', 'institution', 'role'}),
    'L3': frozenset({'end_date', 'institution', 'leadership_role', 'start_date', 'unit_program'}),
    'M2A': frozenset({'agency', 'annual_funding', 'end_date', 'grant_number', 'notes', 'percent_effort', 'pi_name', 'pi_role', 'start_date', 'status', 'title', 'total_funding'}),
    'M2B': frozenset({'agency', 'end_date', 'grant_number', 'notes', 'percent_effort', 'pi_name', 'pi_role', 'start_date', 'status', 'title', 'total_funding'}),
    'M2C': frozenset({'agency', 'grant_number', 'notes', 'pi_name', 'pi_role', 'status', 'submission_date', 'title', 'total_funding_requested'}),
    'M2D': frozenset({'assignee', 'filing_date', 'inventors', 'issue_date', 'patent_number', 'status', 'title'}),
    'N2': frozenset({'agency', 'end_date', 'grant_number', 'grant_title', 'role', 'start_date'}),
    'N3A': frozenset({'mentee_level', 'mentee_name', 'research_focus', 'start_date'}),
    'N3B': frozenset({'current_position', 'end_date', 'mentee_level', 'mentee_name', 'start_date'}),
    'O': frozenset({'division_department', 'end_date', 'institution', 'leadership_role', 'start_date'}),
    'P': frozenset({'committee_name', 'end_date', 'institution', 'role', 'start_date'}),
    'Q1': frozenset({'end_date', 'organization', 'role', 'start_date'}),
    'Q2': frozenset({'committee_name', 'end_date', 'organization', 'role', 'start_date'}),
    'Q3': frozenset({'agency', 'end_date', 'panel_name', 'role', 'start_date'}),
    'Q4': frozenset({'end_date', 'journal_name', 'role', 'start_date'}),
    'Q4A': frozenset({'end_date', 'journal_name', 'role', 'start_date'}),
    'Q4B': frozenset({'end_date', 'journal_name', 'role', 'start_date'}),
    'Q4C': frozenset({'end_date', 'journal_name', 'start_date'}),
    'Q4D': frozenset({'journal_name', 'year'}),
    'R': frozenset({'date', 'event_name', 'location', 'role', 'title'}),
    'S1': frozenset({'authors', 'doi', 'issue', 'journal', 'pages', 'pmcid', 'pmid', 'title', 'volume', 'year'}),
    'S2': frozenset({'authors', 'doi', 'issue', 'journal', 'pages', 'pmcid', 'pmid', 'title', 'volume', 'year'}),
    'S3': frozenset({'authors', 'publisher', 'title', 'year'}),
    'S4': frozenset({'authors', 'book_title', 'chapter_title', 'doi', 'editors', 'pages', 'publisher', 'year'}),
    'S5': frozenset({'authors', 'title', 'year'}),
    'S6': frozenset({'authors', 'doi', 'journal', 'pages', 'pmcid', 'pmid', 'title', 'volume', 'year'}),
    'S7': frozenset({'authors', 'title', 'year'}),
    'S8': frozenset({'authors', 'doi', 'title', 'year'}),
    'S9': frozenset({'authors', 'title', 'year'}),
})

# The codes whose section renders the entry's text, not its fields (see above).
# Pinned by `test_text_rendered_codes_write_the_text_and_no_field`.
_TEXT_RENDERED_CODES = frozenset({'E', 'G', 'J', 'K2', 'K3', 'K4', 'K5', 'L1', 'L2', 'M1', 'M2', 'N1', 'N3', 'N4', 'S0', 'T'})

# Keys a stage-5 formatter writes for the WHOLE entry (5c teaching prose, 5d
# citation). An entry that carries one already has a rendering of all its
# records; a child copying it would repeat the whole list once per record.
_FORMATTED_KEYS = ('formatted_text', 'formatted_citation')

# Stage 4's free-text remark about the entry as a whole ("Entry contains three
# tab-separated roles ..."). Not a field of any record, so children do not
# inherit it.
_ENTRY_REMARK_KEY = 'notes'

# #1187: keys stage 4 emits on a code's records that its `FIELD_SCHEMAS` entry
# does not define. The schema is also the stage-4 extraction prompt, so the
# allowance lives here, not there. B1 carries its attendance dates under
# `dates_attended` (a string or a `{start_date, end_date}` dict), flat
# `dates_attended_start_date` / `_end_date`, or plain `start_date` / `end_date`
# (the three shapes `sections/education.py` reads); a multi-degree `degrees`
# list whose items hold one failed `_record_list` on that key alone and both
# degrees vanished. Counted over the 126 stage-4 artifacts: the only extra key
# a B1 list item carries is `dates_attended`; the flat and generic keys sit on
# the parent and are listed so a child's own copy is accepted too.
_EXTRA_RECORD_KEYS: Mapping[str, frozenset[str]] = MappingProxyType({
    'B1': frozenset({'dates_attended', 'dates_attended_start_date',
                     'dates_attended_end_date', 'start_date', 'end_date'}),
})

# #1187: codes whose renderer writes nothing for a record list left whole --
# `sections/education.py` skips a B1 row with no top-level degree or
# institution, so an unsplit `degrees` list vanishes; the appointment,
# licence, membership and service renderers write a row from the schema
# fields alone, so an unsplit `appointments` / `committees` /
# `additional_roles` list is not in the document either (EBYSBC E6: 23
# records in 5 CVs, KYOPUV and XELRLZ losing the owner's current rank).
# Codes that keep the entry's text (K2/K4 refuse to split by design) lose
# nothing to a declined list; warning on them put 31 false alarms in 16
# corpus CVs.
_LIST_LOST_WHEN_KEPT_WHOLE = frozenset({'B1', 'D1', 'D2', 'D3', 'F1', 'I', 'Q1', 'Q2'})

# #1187: the attendance dates `sections/education.py` writes into B1's Dates
# column on top of `_RENDERED_FIELDS['B1']`: flat, generic, and the nested
# `dates_attended: {start_date, end_date}` dict. A STRING `dates_attended` is
# written too, but only when no start/end builds a range (`_is_written_date`). Without these a degree child whose dates the text also
# names would always be refused by `_fields_carry_text`, and the entry would
# still vanish.
_RENDERED_DATE_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType({
    'B1': frozenset({'dates_attended_start_date', 'dates_attended_end_date',
                     'start_date', 'end_date'}),
})
_NESTED_DATES_KEY = 'dates_attended'

# Entry-level keys a stage-5 pass writes about the entry's own scalar fields:
# the institution 5b cleaned and located, the publication record stage 5
# matched, and which fields either one filled in. The first six were every key
# the 163-CV render set's stage-5d entries carry that its stage-4 entries do
# not; `in_press_note` and `in_press_superseded` came later (stage 5's in-press
# title search).
# On a stage-4 records list the scalars are the LAST record, so these describe
# that record only and an earlier child does not inherit them: 5b's cleaned
# name replaces the raw institution cell, so a first B2 record rendered the
# last record's institution until they were dropped.
_STAGE5_ENTRY_KEYS = frozenset({
    'enriched_fields', 'enrichment_data', 'enrichment_rejected',
    'enrichment_source', 'enrichment_status', 'institution_enrichment',
    'in_press_note', 'in_press_superseded'})

# Render-warning record for a record list that was not fanned out (#1187).
REJECTED_LIST_CHECK = 'fan_out_record_list_rejected'
_WARN_SEVERITY = 'WARN'
_EVIDENCE_KEYS_SHOWN = 8

# EBYSBC E34 (BZZNRL 137): the geographic scopes the service and presentation
# sections have a table for (`_classify_geographic_scope`). A CV files a whole
# line of records under a heading that names its scope ("National"). Stage 4
# split one such line into three meetings, one abroad; the last record, which
# carries the parent's whole text, was classified on that text, whose first
# record is the meeting abroad, and a US meeting went under International. A
# record keeps the scope its heading names unless the classifier, asked about
# that record alone, puts it in another country (`_ABROAD_SCOPE`).
_SCOPES = ('Regional', 'National', 'International')
_SCOPE_RES = tuple((scope, re.compile(rf'\b{scope}\b', re.IGNORECASE)) for scope in _SCOPES)
_ABROAD_SCOPE = 'International'

_DATE_KEY_SUFFIX = '_date'
_BARE_DATE_KEYS = frozenset({'date', 'year'})
# `dates_attended` and its flat `dates_attended_start_date` / `_end_date` (#1187).
_DATES_ATTENDED_PREFIX = 'dates_attended'


def _is_date_key(key: str) -> bool:
    return (key in _BARE_DATE_KEYS or key.endswith(_DATE_KEY_SUFFIX)
            or key.startswith(_DATES_ATTENDED_PREFIX))


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and not value.strip()) \
        or (isinstance(value, (list, dict)) and not value)


def _four_digit_year(match: re.Match[str]) -> str:
    return str(expand_two_digit_year(int(match.group(1))))


def _year_of_month_year(match: re.Match[str]) -> str:
    """'3/02' -> '2002', '11/1988' -> '1988'; not a month -> as written."""
    if not 1 <= int(match.group(1)) <= _MONTHS_IN_YEAR:
        return match.group(0)
    return match.group(2) or str(expand_two_digit_year(int(match.group(3))))


def _full_range_end(match: re.Match[str]) -> str:
    """'1993-96' -> '1993-1996', '1998-01' -> '1998-2001'; anything that
    would not be a forward span of at most `_MAX_SHORT_RANGE_YEARS` is left."""
    start = int(match.group(1))
    end = start - start % _CENTURY + int(match.group(3))
    if end <= start:
        end += _CENTURY
    if end - start > _MAX_SHORT_RANGE_YEARS:
        return match.group(0)
    return f'{match.group(1)}{match.group(2)}{end}'


def _comparable(text: str) -> str:
    """`text` with every date reduced to its four-digit year (the comment on
    `_OPEN_END_TOKEN` says why)."""
    text = _MONTH_NAME_RE.sub(' ', text)
    text = _APOSTROPHE_YEAR_RE.sub(_four_digit_year, text)
    text = _MONTH_YEAR_RE.sub(_year_of_month_year, text)
    return _SHORT_RANGE_END_RE.sub(_full_range_end, text)


def _tokens(text: str) -> Counter[str]:
    """The content words of `text`, lowercased, dates compared by year and an
    open end ('current', 'now') counted as the 'Present' it renders as."""
    words = (t for t in _TOKEN_RE.findall(_comparable(text).lower()) if t not in _STOPWORDS)
    return Counter(_OPEN_END_TOKEN if t in CURRENT_DATE_VALUES else t for t in words)


def _text_to_cover(text: str, child_fields: Sequence[Mapping[str, Any]],
                   rendered: frozenset[str]) -> str:
    """The entry's text less its list numbers and, when every child names its
    own role, a leading section label (see `_LEADING_LABEL_RE` above)."""
    text = _LIST_NUMBER_RE.sub(r'\1', text)
    roles = _ROLE_FIELDS & rendered
    if all(any(not _is_blank(fields.get(key)) for key in roles) for fields in child_fields):
        text = _LEADING_LABEL_RE.sub('', text, count=1)
    return text


def _leaf_text(value: object) -> str:
    """Every string, number and nested string inside `value`, space-joined."""
    if isinstance(value, Mapping):
        return ' '.join(_leaf_text(v) for v in value.values())
    if isinstance(value, list):
        return ' '.join(_leaf_text(v) for v in value)
    return '' if value is None else str(value)


def _rendered_text(key: str, value: object, code: str) -> str:
    """What the renderer writes for one field: a date as the code's date column
    shows it (year only for most, so a month the text names is not held), any
    other value as it stands."""
    text = _leaf_text(value)
    return format_date_for_section(text, code) if _is_date_key(key) else text


def _is_written_date(code: str, key: str, value: object,
                     fields: Mapping[str, Any]) -> bool:
    """Whether B1's renderer writes this attendance-date field (#1187). A
    string `dates_attended` is written only when no flat or generic start/end
    is there to build the range from."""
    bounds = _RENDERED_DATE_FIELDS.get(code, frozenset())
    if key in bounds:
        return True
    if code not in _RENDERED_DATE_FIELDS or key != _NESTED_DATES_KEY:
        return False
    if isinstance(value, Mapping):
        return True
    return isinstance(value, str) and not any(fields.get(k) for k in bounds)


def _fields_carry_text(entry: Mapping[str, Any],
                       child_fields: Sequence[Mapping[str, Any]]) -> bool:
    """Whether the fields the renderer writes, summed over the children (each
    child writes the scalars it inherited, so they count once per child), hold
    every token of the entry's text -- see `_STOPWORDS` for what is not counted.
    A schema key the renderer never reads, and a key outside the schema, hold
    nothing."""
    code = str(entry.get('taxonomy_code'))
    rendered = _RENDERED_FIELDS.get(code)
    if rendered is None:
        return False
    held: Counter[str] = Counter()
    for fields in child_fields:
        for key, value in fields.items():
            if key in rendered:
                held += _tokens(_rendered_text(key, value, code))
            elif _is_written_date(code, key, value, fields):
                held += _tokens(_leaf_text(value))
    return not _tokens(_text_to_cover(str(entry.get('text') or ''), child_fields, rendered)) - held


def _record_list(value: object, schema: frozenset[str],
                 minimum: int = _MIN_LIST_ITEMS) -> bool:
    """True when `value` is `minimum`+ dicts, every one of which is made of
    schema keys and shares at least one with the schema."""
    return _is_dict_list(value, minimum) and all(set(item) <= schema for item in value)


def _record_schema(code: object, schema: frozenset[str]) -> frozenset[str]:
    """The keys a record of `code` may carry: the stage-4 schema plus the
    `_EXTRA_RECORD_KEYS` stage 4 is known to add (#1187)."""
    return schema | _EXTRA_RECORD_KEYS.get(str(code), frozenset())


def _list_candidates(fields: Mapping[str, Any],
                     schema: frozenset[str]) -> Iterator[tuple[ListPath, object]]:
    """`(path, value)` for every key outside the schema and, inside an object
    under such a key, every key outside the schema one level down."""
    for key, value in fields.items():
        if key in schema:
            continue
        yield (key,), value
        if isinstance(value, Mapping):
            for inner, nested in value.items():
                if inner not in schema:
                    yield (key, inner), nested


def _record_keys(fields: Mapping[str, Any], schema: frozenset[str],
                 minimum: int = _MIN_LIST_ITEMS) -> list[ListPath]:
    """The paths in `extracted_fields`, outside the schema, of a record list."""
    return [path for path, value in _list_candidates(fields, schema)
            if _record_list(value, schema, minimum)]


def _list_at(fields: Mapping[str, Any], path: ListPath) -> list[Mapping[str, Any]]:
    """The record list `_record_keys` found at `path`."""
    outer, *inner = path
    value = fields[outer]
    return value[inner[0]] if inner else value


def _without(fields: Mapping[str, Any], path: ListPath) -> dict[str, Any]:
    """`fields` less the value at `path`; an object the removal empties goes too."""
    outer, *inner = path
    rest = {key: value for key, value in fields.items() if key != outer}
    if inner:
        container = {key: value for key, value in fields[outer].items() if key != inner[0]}
        if container:
            rest[outer] = container
    return rest


def _path_name(path: ListPath) -> str:
    return _PATH_SEPARATOR.join(path)


def _parent_is_own_record(scalars: Mapping[str, Any],
                          items: Sequence[Mapping[str, Any]],
                          schema: frozenset[str], segment_count: int) -> bool:
    """True when the parent's own scalar fields are a record the list does not
    repeat (a Co-Leader row whose `additional_roles` are the later posts).

    Needs an identity value first: a non-date field the items also use. A
    `mentee_level` the items never carry is shared context, and start/end dates
    the items repeat are the same span, so a parent with neither is not a record.
    Items that carry nothing but dates are the parent's other periods
    (`additional_dates: [{start_date, end_date}]`), so there any non-date
    field of the parent is its identity.
    Given one, the entry's own text settles it when it can: one more tab
    segment than list items means the parent is the extra record, exactly as
    many means the list already holds every one of them (a first post stage 4
    also left in the parent's scalars). A single segment says nothing -- every
    one-line entry has one -- so it does not count for a one-item list. When
    the counts say neither, a parent whose values some item repeats is that
    item, not emitted twice.
    """
    item_keys = set().union(*items)
    periods_only = all(_is_date_key(key) for key in item_keys)
    identity = {key: value for key, value in scalars.items()
                if key in schema and (periods_only or key in item_keys)
                and not _is_date_key(key) and not _is_blank(value)}
    if not identity:
        return False
    if segment_count > 1 and segment_count in (len(items), len(items) + 1):
        return segment_count == len(items) + 1
    return not any(all(item.get(key) == value for key, value in identity.items())
                   for item in items)


def _own_values(scalars: Mapping[str, Any],
                items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The parent's own record: `scalars` less every `_JOINED_VALUE_SEPARATOR`
    part of a value that an item holds as its own value of the same field.
    EBYSBC E34 (CTWLTR 55): stage 4 put all three consultancies, joined, in
    the parent's `organization` and two of them in items too, so they rendered
    twice. A value no item repeats a part of is kept whole, and so is one
    every part of which an item holds (nothing would be left of its own)."""
    own = dict(scalars)
    for key, value in scalars.items():
        if not isinstance(value, str) or _JOINED_VALUE_SEPARATOR not in value:
            continue
        named = {norm(item[key]) for item in items if isinstance(item.get(key), str)}
        parts = [part.strip() for part in value.split(_JOINED_VALUE_SEPARATOR) if part.strip()]
        kept = [part for part in parts if norm(part) not in named]
        if kept and len(kept) < len(parts):
            own[key] = f'{_JOINED_VALUE_SEPARATOR} '.join(kept)
    return own


def _segments(text: object) -> list[str]:
    return [part.strip() for part in str(text or '').split(_TEXT_SEGMENT_SEPARATOR)
            if part.strip()]


def _built_text(record: Mapping[str, Any]) -> str:
    """One line out of a record's own string values, in the record's key order."""
    return _BUILT_TEXT_SEPARATOR.join(
        str(value).strip() for value in record.values()
        if isinstance(value, (str, int, float)) and str(value).strip())


def _child_texts(text: object, records: Sequence[Mapping[str, Any]]) -> list[str]:
    """Each record's text: its own tab segment when the segments line up one to
    one with the records, else a line built from the record's values (a record
    that wrapped across lines makes the counts disagree, and pairing segments
    by position then would hand a record another record's tail). A lone
    record -- a one-item list the parent adds nothing to -- is the whole text."""
    if len(records) == 1:
        return [str(text or '')]
    segments = _segments(text)
    if len(segments) == len(records):
        return segments
    return [_built_text(record) for record in records]


def _child(entry: Mapping[str, Any], fields: dict[str, Any], text: str,
           key: str, index: int, count: int) -> dict[str, Any]:
    child = copy.deepcopy(dict(entry))
    child['text'] = text
    child['extracted_fields'] = fields
    child[FANNED_OUT_FROM] = {'key': key, 'index': index, 'count': count}
    return child


def _is_dict_list(value: object, minimum: int = _MIN_RECORDS) -> bool:
    """`minimum`+ non-empty dicts: record-like, whatever their keys."""
    return (isinstance(value, list) and len(value) >= minimum
            and all(isinstance(item, Mapping) and item for item in value))


def _rejected_record_lists(fields: Mapping[str, Any],
                           schema: frozenset[str]) -> dict[str, list[str]]:
    """`{path: item keys outside the schema}` for every list of record-like
    dicts outside the schema that `_record_list` refused (#1187). Key names
    only; never a value."""
    return {_path_name(path): sorted({k for item in value for k in item} - schema)
            for path, value in _list_candidates(fields, schema)
            if _is_dict_list(value, _MIN_LIST_ITEMS) and not _record_list(value, schema)}


def _rejection_warnings(rejected: Mapping[tuple[str, str], list[list[str]]]) -> list[dict[str, Any]]:
    """One sidecar WARN per (code, list key), counting the entries and, when
    item keys outside the schema kept the list from fanning out, naming them.
    An empty key list means the list was record-shaped and the entry was
    declined by the other guards (text a rendered field does not carry, a
    stage-5 rendering, several lists). Sorted, so the sidecar is deterministic."""
    out = []
    for (code, key), cases in sorted(rejected.items()):
        stray = sorted({k for extra in cases for k in extra})
        why = (f"item keys outside the {code} schema: {', '.join(stray[:_EVIDENCE_KEYS_SHOWN])}"
               if stray else "the entry's text holds content its fields do not carry")
        out.append({
            'check': REJECTED_LIST_CHECK,
            'code': code,
            'section': None,
            'message': (f"{len(cases)} {code} entr{'y' if len(cases) == 1 else 'ies'}: "
                        f"`{key}` holds records that were not split into "
                        f"separate rows ({why}); a record may be missing from "
                        "the output"),
            'evidence': [f"{code}.{key}: {len(cases)} entr{'y' if len(cases) == 1 else 'ies'}",
                         *stray[:_EVIDENCE_KEYS_SHOWN]],
            'severity': _WARN_SEVERITY,
        })
    return out


def _declined_record_lists(entry: Mapping[str, Any], schema: frozenset[str]) -> dict[str, list[str]]:
    """Every record-list key of an entry `_fan_out_entry` declined: the lists
    `_record_list` refused (with their stray item keys) plus the ones it
    accepted that a later guard then declined (no stray keys)."""
    fields = entry.get('extracted_fields')
    if not isinstance(fields, Mapping):
        return {}
    return {**{_path_name(path): [] for path in _record_keys(fields, schema)},
            **_rejected_record_lists(fields, schema)}


def _renders_something(record: Mapping[str, Any], rendered: frozenset[str]) -> bool:
    """Whether `record` holds a value under a field its section writes."""
    return any(not _is_blank(record.get(key)) for key in rendered)


def _stage4_children(entry: Mapping[str, Any], fields: Mapping[str, Any],
                     records_key: str) -> list[dict[str, Any]] | None:
    """One child per record stage 4 kept under `records_key`, or None.

    The parent's scalars ARE the last record, so the last child is the parent
    itself: its scalars as they stand (a stage-5 addition included) and its
    whole text, so every stage-6 check that weighs a rendered row against the
    entry's text (the low-coverage overflow that re-emits a narrative the
    row does not carry, `_recover_unrendered_records`) sees what it saw
    before. It carries no `FANNED_OUT_FROM` either, so dedup weighs it as it
    weighed the parent; `LAST_STAGE4_RECORD` marks it for the scope rules
    instead. Every earlier child is its record alone, with its own
    segment or built line, and without the parent's `_STAGE5_ENTRY_KEYS`: a
    scalar or an enrichment inherited from the last record would put that
    record's value on another one. Declined for a code whose section
    renders the text rather than the fields, for an entry a stage-5 formatter
    rendered whole, and when a record holds no value its section writes (its
    row would be empty).
    """
    rendered = _RENDERED_FIELDS.get(str(entry.get('taxonomy_code')))
    records = fields[records_key]
    if rendered is None or any(fields.get(k) for k in _FORMATTED_KEYS):
        return None
    if not all(_renders_something(record, rendered) for record in records):
        return None
    texts = _child_texts(entry.get('text'), records)
    bare = {key: value for key, value in entry.items() if key not in _STAGE5_ENTRY_KEYS}
    earlier = [_child(bare, copy.deepcopy(dict(record)), text, records_key, i, len(records))
               for i, (record, text) in enumerate(zip(records[:-1], texts))]
    last = copy.deepcopy(dict(entry))
    last['extracted_fields'] = {key: value for key, value in last['extracted_fields'].items()
                                if key != records_key}
    last[LAST_STAGE4_RECORD] = {'key': records_key, 'index': len(records) - 1,
                                'count': len(records)}
    return [*earlier, last]


def _fan_out_stage4_records(entry: Mapping[str, Any], schema: frozenset[str],
                            records_key: str) -> tuple[list[dict[str, Any]] | None, Mapping[str, Any]]:
    """`(children, entry)`: the children of an entry carrying stage 4's
    records list, when `_stage4_children` splits it, and the entry the generic
    rules should see otherwise -- `entry` without that key, so a declined
    list changes nothing. An entry whose own scalars also hold a list of two
    or more records is left to the generic rules, which split that list as
    before."""
    fields = entry['extracted_fields']
    own = {key: value for key, value in fields.items() if key != records_key}
    if _is_dict_list(fields[records_key]) and not _record_keys(own, schema, _MIN_RECORDS):
        children = _stage4_children(entry, fields, records_key)
        if children is not None:
            return children, entry
    return None, {**entry, 'extracted_fields': own}


def _fan_out_entry(entry: Mapping[str, Any], schema: frozenset[str],
                   records_key: str | None = None) -> list[dict[str, Any]] | None:
    """The children of `entry`, or None when it is not a multi-record entry."""
    fields = entry.get('extracted_fields')
    if not isinstance(fields, Mapping):
        return None
    if records_key is not None and records_key in fields:
        children, entry = _fan_out_stage4_records(entry, schema, records_key)
        if children is not None:
            return children
        fields = entry['extracted_fields']
    paths = _record_keys(fields, schema)
    if len(paths) != 1 or any(fields.get(k) for k in _FORMATTED_KEYS):
        return None
    items = _list_at(fields, paths[0])
    scalars = {k: v for k, v in _without(fields, paths[0]).items() if k != _ENTRY_REMARK_KEY}
    own = _parent_is_own_record(scalars, items, schema, len(_segments(entry.get('text'))))
    records = ([_own_values(scalars, items)] if own else []) \
        + [dict(item) for item in items]
    child_fields = [{**copy.deepcopy(scalars), **copy.deepcopy(record)} for record in records]
    if not _fields_carry_text(entry, child_fields):
        return None
    texts = _child_texts(entry.get('text'), records)
    return [_child(entry, fields, text, _path_name(paths[0]), i, len(records))
            for i, (fields, text) in enumerate(zip(child_fields, texts))]


def inherited_scope(entry: Mapping[str, Any]) -> str | None:
    """The geographic scope a fanned-out child takes from its parent: the one
    scope its nearest CV heading names ("National", "NATIONAL SERVICE ROLES").
    Every child counts, the last of stage 4's records included. None when the
    entry is not a child, or when that heading names two scopes
    ("International/National") or none does."""
    if FANNED_OUT_FROM not in entry and LAST_STAGE4_RECORD not in entry:
        return None
    for heading in reversed(entry.get('hierarchy') or []):
        named = [scope for scope, pattern in _SCOPE_RES if pattern.search(str(heading))]
        if named:
            return named[0] if len(named) == 1 else None
    return None


def record_scope(entry: Mapping[str, Any], own_scope: str) -> str:
    """The scope `entry` files under, given `own_scope`, the classifier's
    answer about its `record_text`: a child keeps the scope its heading names
    (`inherited_scope`) unless the classifier puts the record abroad; any
    other entry takes `own_scope`."""
    heading = inherited_scope(entry)
    if heading is None or own_scope == _ABROAD_SCOPE:
        return own_scope
    return heading


def record_text(entry: Mapping[str, Any]) -> str:
    """The text of the one record `entry` stands for: its own text, except for
    the last of stage 4's records, whose text is the parent's whole line (every
    record of the list); that one is the line built from its own fields."""
    if LAST_STAGE4_RECORD in entry:
        return _built_text(entry.get('extracted_fields') or {})
    return str(entry.get('text') or '')


def fan_out_multi_record_entries(
        entries: Sequence[Mapping[str, Any]],
        schema_fields: Mapping[str, Mapping[str, Any]],
        warnings: list[dict[str, Any]] | None = None,
        records_key: str | None = None) -> list[dict[str, Any]]:
    """`entries` with every multi-record entry replaced, in place and in order,
    by one child per record. Everything else is returned untouched.

    `schema_fields` is `{taxonomy_code: {'fields': [...]}}`, i.e.
    `stage4.schemas.FIELD_SCHEMAS`. Each child gets the parent's scalar
    `extracted_fields` plus its own record, the record winning any conflict;
    the parent's other keys are copied; `FANNED_OUT_FROM` names the list it
    came from.

    A list of record-like dicts that is not fanned out -- `_record_list`
    refused it, or a later guard declined the entry -- is not dropped silently
    (#1187): for a code in `_LIST_LOST_WHEN_KEPT_WHOLE`, when `warnings` is
    given, one render-warning dict per (code, list path) is appended to it
    (`REJECTED_LIST_CHECK`).

    `records_key` names the list stage 4 keeps when the LLM returned several
    records for one entry (`stage4.schemas.STAGE4_RECORDS_KEY`); None splits
    no such list. `_stage4_children` says how that list fans out.
    """
    out: list[dict[str, Any]] = []
    rejected: dict[tuple[str, str], list[list[str]]] = {}
    for entry in entries:
        code = entry.get('taxonomy_code')
        schema = _record_schema(code, frozenset(schema_fields.get(code, {}).get('fields', ())))
        children = _fan_out_entry(entry, schema, records_key)
        if children is None and code in _LIST_LOST_WHEN_KEPT_WHOLE:
            for key, stray in _declined_record_lists(entry, schema).items():
                rejected.setdefault((str(code), key), []).append(stray)
        out.extend(children if children is not None else [entry])
    if warnings is not None:
        warnings.extend(_rejection_warnings(rejected))
    return out
