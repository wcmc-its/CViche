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
- an entry whose text has a token no rendered field holds (`_fields_carry_text`),
  except a code that writes no text at all (`_NO_TEXT_COVERAGE_CODES`, B1);
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
a 5c `formatted_text` rendering, a record with no rendered value). A 5d
citation that formats one record does not decline the list: it goes to that
record's child alone (#1243, `_citation_record`). A declined list is
handed to the generic rules above as if that key were absent, so they decide
what they decided before the key existed.
"""
from __future__ import annotations

import copy
import re
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from unified_pipeline.core.text_norm import norm
from unified_pipeline.core.two_digit_year import expand_two_digit_year
from unified_pipeline.stage6.formatting.dates import format_date_for_section
from unified_pipeline.stage6.parsing.dates import (
    _MONTH_NAME_TO_NUM,
    CURRENT_DATE_VALUES,
)

# Provenance key written on every child. Shows which list the record came from
# and where in it: `{'key': 'awards', 'index': 1, 'count': 3}`.
FANNED_OUT_FROM = 'fanned_out_from'

# The same provenance, written instead on the last child of stage 4's records
# list, which is the parent itself (`_stage4_children`). That child keeps the
# parent's whole text and no `FANNED_OUT_FROM`, so dedup weighs it as it
# weighed the parent; the doctor does not read this key. The scope rules read
# it (`record_scope`, `record_text`): the last record is a record of the list
# like its siblings (EBYSBC E34, BZZNRL 137). Dedup reads it to drop the
# siblings of a parent it drops (`dedup._drop_split_siblings`, #1445).
LAST_STAGE4_RECORD = 'last_stage4_record'
# #1445: the key in `LAST_STAGE4_RECORD` holding the last record's own line,
# the one a renderer falls back to (`fallback_text`) for a cell its fields
# leave empty. None when the records' fields do not hold every word of the
# parent's text (`_fields_carry_text`): that text is then the only place the
# words no field holds are kept, so the parent's whole line stays the
# fallback (EOAHMI BRUSUZ 196: venue names stage 4 left out of every record).
OWN_TEXT_KEY = 'text'

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

# Set True on a child whose text is that built line (`_child_texts`), not a
# line of the CV. Its pipes are `_BUILT_TEXT_SEPARATOR`, so a renderer that
# re-parses a source pipe column out of the text (#627) must not: the fields
# the line was built from are the record (#1556 e: OIEPQD BMYYOX 63, the
# role rendered inside the name cell). Read it through `has_built_text`.
BUILT_TEXT_KEY = 'text_built_from_fields'

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
# EBYSBC E6 residual (#1187, KYOPUV 32 re-run as WTKSYX 32): a dated list
# whose text ends (or begins) with undated lines of shared context -- a
# section name, then the institution -- that apply to every record. One stage-4
# run folded the section name into every record's institution and the list
# fanned out; the re-run left it out, so its words were held by no field and
# all five appointments were lost. An undated edge line is that context, and
# not content the children must hold, when (`_shared_context_segments`):
# - it lies before the first or after the last tab segment that holds a
#   year (an undated line between records stays content), and the segments
#   holding a year number exactly one per child, so a dated record the list
#   missed shows up as a count mismatch;
# - one undated edge line is held whole by a value every child carries
#   identically (the institution line), which is what marks the edge block as
#   shared context rather than records;
# - it is no longer than `_MAX_CONTEXT_TOKENS` content words (a unit name, not
#   a narrative);
# - the children are distinct records, not one record's periods: some child
#   differs from another in a rendered value other than its dates (so a lone
#   record never qualifies). A list of extra terms of one post (VNUAHA 142's
#   `additional_dates`) is EBYSBC E22's multi-span shape and is left to it.
# Every dated segment, i.e. each record's own words, is still compared.
_MAX_CONTEXT_TOKENS = 6
_YEAR_TOKEN_RE = re.compile(r'\d{4}')
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
# set: the probe leaves it unmarked, and only the mentee table writes it. A
# code missing here is never fanned out. That includes every code whose
# section writes the entry's TEXT and no field (E, G, J, K2-K5, L1, L2, M1, M2,
# N1, N4, S0, T; `_TEXT_RENDERED_CODES`): a child of one would render only its
# built text, which does not carry the parent's scalars. (N4
# may reorder a scrambled line by its fields, but only when they hold exactly
# the text's words -- `mentoring._outcome_line_from_fields`, #1434.)
_RENDERED_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType({
    'B1': frozenset({'advisor', 'degree', 'discipline', 'institution', 'year'}),
    'B2': frozenset({'institution', 'program_name', 'year'}),
    'C': frozenset({'end_date', 'institution', 'role', 'specialty', 'start_date', 'training_type'}),
    'D1': frozenset({'department', 'end_date', 'institution', 'start_date', 'title'}),
    'D2': frozenset({'department', 'end_date', 'institution', 'start_date', 'title'}),
    'D3': frozenset({'department', 'end_date', 'organization', 'start_date', 'title'}),
    'F1': frozenset({'expiration_date', 'issue_date', 'license_number', 'license_type', 'state_country', 'status'}),
    'F2': frozenset({'certifying_board', 'recertification_date', 'specialty', 'year_certified'}),
    'H': frozenset({'award_name', 'date', 'granting_body'}),
    'I': frozenset({'end_date', 'membership_type', 'organization', 'start_date'}),
    'K1': frozenset({'course_code', 'course_title', 'institution', 'role'}),
    'L3': frozenset({'end_date', 'institution', 'leadership_role', 'start_date', 'unit_program'}),
    'M2A': frozenset({'agency', 'annual_funding', 'end_date', 'grant_number', 'notes', 'percent_effort', 'pi_name', 'pi_role', 'start_date', 'status', 'title', 'total_funding'}),
    'M2B': frozenset({'agency', 'annual_funding', 'end_date', 'grant_number', 'notes', 'percent_effort', 'pi_name', 'pi_role', 'start_date', 'status', 'title', 'total_funding'}),
    'M2C': frozenset({'agency', 'annual_funding', 'grant_number', 'notes', 'pi_name', 'pi_role', 'status', 'submission_date', 'title', 'total_funding_requested'}),
    'M2D': frozenset({'assignee', 'filing_date', 'inventors', 'issue_date', 'patent_number', 'status', 'title'}),
    'N2': frozenset({'agency', 'end_date', 'grant_number', 'grant_title', 'role', 'start_date'}),
    # A bare N3 that names a mentee renders as one (`_route_bare_mentee`, #1574).
    'N3': frozenset({'current_position', 'end_date', 'mentee_level', 'mentee_name', 'start_date', 'thesis_title'}),
    'N3A': frozenset({'mentee_level', 'mentee_name', 'research_focus', 'start_date'}),
    'N3B': frozenset({'current_position', 'end_date', 'mentee_level', 'mentee_name', 'start_date', 'thesis_title'}),
    'O': frozenset({'division_department', 'end_date', 'institution', 'leadership_role', 'start_date'}),
    'P': frozenset({'committee_name', 'end_date', 'institution', 'role', 'start_date'}),
    'Q1': frozenset({'end_date', 'organization', 'role', 'start_date'}),
    'Q2': frozenset({'committee_name', 'end_date', 'organization', 'role', 'start_date'}),
    'Q3': frozenset({'agency', 'end_date', 'panel_name', 'role', 'start_date'}),
    'Q4': frozenset({'end_date', 'journal_name', 'role', 'start_date'}),
    'Q4A': frozenset({'end_date', 'journal_name', 'role', 'start_date'}),
    'Q4B': frozenset({'end_date', 'journal_name', 'role', 'section', 'start_date'}),
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
    # #1570: a meeting with no 5d citation renders its meeting and place.
    'S8': frozenset({'authors', 'conference_name', 'doi', 'location', 'title', 'year'}),
    'S9': frozenset({'authors', 'title', 'year'}),
})

# Rendered fields that do not on their own make a record worth its own item.
# S8 prints a meeting and its place (#1570), but a record holding nothing
# else has no title or year, and the bibliography sends such an item to the
# Appendix (`_lacks_title_and_year`, #446). Splitting a list of bare
# meetings put one meeting there twice, once alone and once in the whole
# line the last child keeps (NDMRSO QFQLNF 181), so such a list stays whole,
# as it did before S8 rendered those fields.
_NON_ANCHOR_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType({
    'S8': frozenset({'conference_name', 'location'}),
})

# The codes whose section renders the entry's text, not its fields (see above).
# Pinned by `test_text_rendered_codes_write_the_text_and_no_field`.
_TEXT_RENDERED_CODES = frozenset({'E', 'G', 'J', 'K2', 'K3', 'K4', 'K5', 'L1', 'L2', 'M1', 'M2', 'N1', 'N4', 'S0', 'T'})

# Keys a stage-5 formatter writes for the WHOLE entry (5c teaching prose, 5d
# citation). An entry that carries one already has a rendering of all its
# records; a child copying it would repeat the whole list once per record.
_FORMATTED_KEYS = ('formatted_text', 'formatted_citation')

# #1243 (NDMRSO VYRDHN 607/613, EOAHMI LOOTTE 603/606/612): stage 5d formats
# ONE citation per entry, from the entry's text, so an entry stage 4 split
# into 2+ publication records carries a citation of one of them -- the first,
# in every farm case -- while its scalars are the last. Declining that split
# rendered the one citation and lost every other record. Such a split now
# fans out: the citation goes to the one record whose title it writes
# (`_citation_record`), and every other record renders from its own fields.
# A citation that writes several records' titles already renders them all,
# with venue and place their fields may not hold (EOAHMI GHCIXA S8: a
# fan-out lost "Vancouver, Canada"), so that split, and one whose citation
# writes no record's title, is still declined. Records that share one title
# are the exception: the citation's years can name one of them
# (`_hit_cited_by_year`).
_FORMATTED_TEXT_KEY = 'formatted_text'
_FORMATTED_CITATION_KEY = 'formatted_citation'
# What 5d writes beside the citation (`apply_formatted_fields`); the
# publication resolver trusts a citation only with this provenance.
_CITATION_KEYS = (_FORMATTED_CITATION_KEY, 'formatting_source')
# A publication record's title: `title`, or S4's `chapter_title`.
_TITLE_KEYS = ('title', 'chapter_title')

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
# P, Q3 and R were silent. Their sections write the parent's fields and
# nothing from a list left whole: on the 302-CV render set P's `committees`
# and R's `additional_presentations` values are absent from the document
# (P also writes the stripped entry text, so a kept-whole P entry loses role
# and date detail rather than the list). Q3 has no corpus case; its section is
# the same fields-only kind. A period-only `additional_dates` list on P or R
# is rendered (`with_extra_date_spans`, `further_date_spans`), so such a
# warning can be a false alarm, as it can for Q1.
# O stays out: its `additional_dates` (`leadership.py`, `with_extra_date_spans`)
# is the list its section does write, so on the corpus every O warning was a
# false alarm (VNUAHA O 142 renders the spans).
_LIST_LOST_WHEN_KEPT_WHOLE = frozenset({
    'B1', 'D1', 'D2', 'D3', 'F1', 'I', 'P', 'Q1', 'Q2', 'Q3', 'R'})

# #1187: codes whose declined list warns only when its records are not in the
# rendered document (`DeclinedList`, `unrendered_list_warnings`). Each section
# writes its row from the fields `_RENDERED_FIELDS` names, so a list left
# whole is not written as a list. But when the parent holds no name of its
# own the row falls back to the entry's text, which carries every record:
# on the 238-CV render set every such list (15 entries in 8 CVs: N3B
# `mentees` 6, M2A `sub_awards` / `sub_projects` 2, Q4C / Q4D `journals` 4,
# and 3 S8 records lists, below) is in the document, so an unconditional
# warning here would have been a false alarm every time.
_LIST_CHECKED_AFTER_RENDER = frozenset({
    'B2', 'C', 'F2', 'H', 'L3', 'M2A', 'M2B', 'M2C', 'M2D', 'N2', 'N3', 'N3A',
    'N3B', 'Q4', 'Q4A', 'Q4B', 'Q4C', 'Q4D'})

# #1187: H's date cell (`honors._record_date`) writes a list of dates under
# one of these keys -- the first one that holds any -- and only when the
# record has none of `_H_OWN_DATE_KEYS` itself, so such a list is not a
# declined list at all (HZGJFM H 261-263). Any other H record list left whole
# (`awards`, or a date list beside the record's own `date`) is checked
# after the render. Read by `sections/honors.py`, so this is its one
# definition.
H_DATE_LIST_KEYS = ('dates', 'additional_dates')
_H_CODE = 'H'
_H_OWN_DATE_KEYS = ('date', 'year', 'start_date', 'end_date')

# #1187: codes whose stage-4 records list (`records_key`) is checked after the
# render when `_stage4_children` declines it: the entry then renders its
# scalars, which are the last record only. Not checked when stage 5 rendered
# the whole entry (`_stage4_list_rendered_whole`): K1's 5c `formatted_text`
# writes one line per record (`teaching._record_blocks`; DTFNOR, VGHNZD,
# WYMVGU, ZDCXIV), and a 5d citation that writes 2+ records' titles prints
# them all (GHCIXA S8 170 / 178). The warnings above already name
# `records_key` for the codes they cover.
_STAGE4_LIST_CHECKED_AFTER_RENDER = frozenset({
    'K1', 'S1', 'S2', 'S3', 'S4', 'S5', 'S6', 'S7', 'S8', 'S9'})

# #1187: those codes are checked the same way for any other record list they
# keep whole, such as an S3 book's `chapters_authored` beside its own title
# (web204 S3 1802, 1814: the row prints the book and none of the chapters).
_GENERIC_LIST_CHECKED_AFTER_RENDER = _LIST_CHECKED_AFTER_RENDER | _STAGE4_LIST_CHECKED_AFTER_RENDER

# A record of a declined list is in the rendered document when one line of it
# holds at least this share of the record's content words (`_tokens`;
# `_record_in_document`). One line, not the document pooled: VYNARH's 19
# reviewed journals, each named in its citations too, matched word by word
# across the publication list while not one journal row was printed. A
# record a line prints only in part (a reformatted date, a name 5b cleaned)
# still clears half.
_MIN_RECORD_SHARE_IN_LINE = 0.5

# A word shorter than this is an initial ("D. B."), which names no one, so
# `_distinct_record_words` does not count it as evidence for a record.
_MIN_DISTINCT_WORD_LENGTH = 2

# #1187: a code whose section writes none of an entry's text and none of a
# record list left whole, so `_fields_carry_text` has nothing to protect: the
# words no field holds are missing from the document whether the entry fans
# out or not, and declining only loses the records too. B1:
# `sections/education.py` writes a row from the degree and institution fields
# and nothing from the text. Four corpus entries (`degrees` lists whose text
# names a section label such as "Graduate:" or a country or city no B1 field
# holds) were kept whole by that test and lost every degree in them.
_NO_TEXT_COVERAGE_CODES = frozenset({'B1'})

# Date fields a section writes on top of its `_RENDERED_FIELDS`, which names
# schema fields only: stage 4 emits these from the active (config-file) schema
# or a field description, not from `FIELD_SCHEMAS`.
# #1187: the attendance dates `sections/education.py` writes into B1's Dates
# column: flat, generic, and the nested `dates_attended: {start_date,
# end_date}` dict. A STRING `dates_attended` is written too, but only when no
# start/end builds a range (`_is_written_date`). Without these a degree child
# whose dates the text also names would always be refused by
# `_fields_carry_text`, and the entry would still vanish.
# #1556 (d): the start/end range `sections/other_education.py` (B2) and
# `sections/service.py` `_fill_journal_reviewing` (Q4D) write into their Dates
# column. Without them two records of one journal with different spans
# (OIEPQD XAYKXA entry 134) printed alike to `_repeated_rows`, and the earlier
# span was dropped.
# #1901: the start/end range `sections/honors.py` `_record_date` writes into
# H's date cell when the schema's `date` does not win (`_h_writes_bound`).
# Without them two records of one award with different spans (NECRGC EYTHNB
# entry 164: "2007-2012, 2015-2018") printed alike, and the earlier span was
# dropped.
_RENDERED_DATE_FIELDS: Mapping[str, frozenset[str]] = MappingProxyType({
    'B1': frozenset({'dates_attended_start_date', 'dates_attended_end_date',
                     'start_date', 'end_date'}),
    'B2': frozenset({'start_date', 'end_date'}),
    'H': frozenset({'start_date', 'end_date'}),
    'Q4D': frozenset({'start_date', 'end_date'}),
})
# The one code whose date cell shows its single date over a start/end range.
_DATE_WINS_CODE = 'H'
_DATE_WINS_KEYS = ('date', 'year')
_START_KEY = 'start_date'
_NESTED_DATES_KEY = 'dates_attended'
# The one code whose section writes a nested or string `dates_attended`.
_NESTED_DATES_CODE = 'B1'

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
    'in_press_note', 'in_press_superseded', 'crossref_match', 'citation_note'})

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
# #1579 (YUYVIG IZABPD, SXPHOG, SQMWHM): a CV also files talks under "State",
# "Local" and "Presentations (State & Local)", which the template's Regional
# table holds; "Federal" names a national reach. A heading word -> the table.
_SCOPE_WORDS = {
    'Regional': ('regional', 'local', 'state', 'statewide', 'provincial'),
    'National': ('national', 'federal'),
    'International': ('international',),
}
_SCOPE_RES = tuple((scope, re.compile(rf"\b(?:{'|'.join(words)})\b", re.IGNORECASE))
                   for scope, words in _SCOPE_WORDS.items())
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


def _is_dated(segment: str) -> bool:
    """Whether a tab segment names a year or an open end ('present')."""
    return any(_YEAR_TOKEN_RE.fullmatch(token) or token == _OPEN_END_TOKEN
               for token in _tokens(segment))


def _identity(fields: Mapping[str, Any], rendered: frozenset[str]) -> dict[str, Any]:
    """A child's rendered, non-date, non-blank values: what tells it apart
    from a sibling other than its dates."""
    return {key: value for key, value in fields.items()
            if key in rendered and not _is_date_key(key) and not _is_blank(value)}


def _common_values(child_fields: Sequence[Mapping[str, Any]],
                   rendered: frozenset[str], code: str) -> list[Counter[str]]:
    """The tokens of each rendered, non-date value every child holds
    identically (the institution all of a list's records share)."""
    first, *rest = child_fields
    return [_tokens(_rendered_text(key, value, code))
            for key, value in _identity(first, rendered).items()
            if all(fields.get(key) == value for fields in rest)]


def _shared_context_segments(segments: Sequence[str],
                             child_fields: Sequence[Mapping[str, Any]],
                             rendered: frozenset[str], code: str) -> set[int]:
    """The indexes of the undated edge segments that are context every record
    shares, not a record's own words (see `_MAX_CONTEXT_TOKENS` above); empty
    when the entry is not a dated list with such an edge block."""
    dated = [i for i, segment in enumerate(segments) if _is_dated(segment)]
    if len(dated) != len(child_fields):
        return set()
    identities = [_identity(fields, rendered) for fields in child_fields]
    if all(identity == identities[0] for identity in identities[1:]):
        return set()
    edges = [i for i in range(len(segments)) if i < dated[0] or i > dated[-1]]
    common = _common_values(child_fields, rendered, code)
    if not any(_tokens(segments[i]) and not _tokens(segments[i]) - value
               for i in edges for value in common):
        return set()
    return {i for i in edges if sum(_tokens(segments[i]).values()) <= _MAX_CONTEXT_TOKENS}


def _text_to_cover(text: str, child_fields: Sequence[Mapping[str, Any]],
                   rendered: frozenset[str], code: str) -> str:
    """The entry's text less its list numbers, when every child names its own
    role a leading section label (see `_LEADING_LABEL_RE` above), and the
    shared context lines around a dated list (`_shared_context_segments`)."""
    text = _LIST_NUMBER_RE.sub(r'\1', text)
    roles = _ROLE_FIELDS & rendered
    if all(any(not _is_blank(fields.get(key)) for key in roles) for fields in child_fields):
        text = _LEADING_LABEL_RE.sub('', text, count=1)
    segments = text.split(_TEXT_SEGMENT_SEPARATOR)
    context = _shared_context_segments(segments, child_fields, rendered, code)
    return _TEXT_SEGMENT_SEPARATOR.join(
        segment for i, segment in enumerate(segments) if i not in context)


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
    """Whether the code's renderer writes this date field outside its
    `_RENDERED_FIELDS` (`_RENDERED_DATE_FIELDS`). B1's string
    `dates_attended` is written only when no flat or generic start/end is
    there to build the range from (#1187); H's start/end only when its
    `date` does not win (`_h_writes_bound`, #1901)."""
    bounds = _RENDERED_DATE_FIELDS.get(code, frozenset())
    if key in bounds:
        return code != _DATE_WINS_CODE or _h_writes_bound(key, fields)
    if code != _NESTED_DATES_CODE or key != _NESTED_DATES_KEY:
        return False
    if isinstance(value, Mapping):
        return True
    return isinstance(value, str) and not any(fields.get(k) for k in bounds)


def _h_writes_bound(key: str, fields: Mapping[str, Any]) -> bool:
    """Whether H's `honors._record_date` writes this start/end bound (#1901).
    A `date` (or `year`) is the cell when a start sits beside it or no end
    does; otherwise the cell is the range from the start, or from the date,
    to the end. So a start is written only with no date, and an end unless
    a date and a start are both there."""
    dated = any(not _is_blank(fields.get(name)) for name in _DATE_WINS_KEYS)
    if key == _START_KEY:
        return not dated
    return not (dated and not _is_blank(fields.get(_START_KEY)))


def _fields_carry_text(entry: Mapping[str, Any],
                       child_fields: Sequence[Mapping[str, Any]]) -> bool:
    """Whether the fields the renderer writes, summed over the children (each
    child writes the scalars it inherited, so they count once per child), hold
    every token of the entry's text -- see `_STOPWORDS` for what is not counted.
    A schema key the renderer never reads, and a key outside the schema, hold
    nothing."""
    uncovered = _uncovered_tokens(entry, child_fields)
    return uncovered is not None and not uncovered


def _uncovered_tokens(entry: Mapping[str, Any],
                      child_fields: Sequence[Mapping[str, Any]]) -> Counter[str] | None:
    """The tokens of the entry's text that no field the renderer writes holds
    (`_fields_carry_text` is "none"); None for a code with no
    `_RENDERED_FIELDS`, whose text cannot be checked."""
    code = str(entry.get('taxonomy_code'))
    rendered = _RENDERED_FIELDS.get(code)
    if rendered is None:
        return None
    held: Counter[str] = Counter()
    for fields in child_fields:
        for key, value in fields.items():
            if key in rendered:
                held += _tokens(_rendered_text(key, value, code))
            elif _is_written_date(code, key, value, fields):
                held += _tokens(_leaf_text(value))
    text = _text_to_cover(str(entry.get('text') or ''), child_fields, rendered, code)
    return _tokens(text) - held


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


def _texts_are_built(text: object, records: Sequence[Mapping[str, Any]]) -> bool:
    """Whether `_child_texts` builds each record's text from its values: two
    or more records whose count the text's tab segments do not match."""
    return len(records) > 1 and len(_segments(text)) != len(records)


def _child_texts(text: object, records: Sequence[Mapping[str, Any]]) -> list[str]:
    """Each record's text: its own tab segment when the segments line up one to
    one with the records, else a line built from the record's values (a record
    that wrapped across lines makes the counts disagree, and pairing segments
    by position then would hand a record another record's tail). A lone
    record -- a one-item list the parent adds nothing to -- is the whole text."""
    if len(records) == 1:
        return [str(text or '')]
    if _texts_are_built(text, records):
        return [_built_text(record) for record in records]
    return _segments(text)


def _child(entry: Mapping[str, Any], fields: dict[str, Any], text: str,
           key: str, index: int, count: int, *, built: bool) -> dict[str, Any]:
    """One record of `entry`; `built` says its text is `_built_text` (`BUILT_TEXT_KEY`)."""
    child = copy.deepcopy(dict(entry))
    child['text'] = text
    child['extracted_fields'] = fields
    child[FANNED_OUT_FROM] = {'key': key, 'index': index, 'count': count}
    if built:
        child[BUILT_TEXT_KEY] = True
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


def _entries_noun(count: int) -> str:
    return 'entry' if count == 1 else 'entries'


def _list_warning(code: str, key: str, count: int, message: str,
                  stray: Sequence[str] = ()) -> dict[str, Any]:
    """One sidecar WARN about the `key` lists of `count` `code` entries;
    `message` follows the count and names no value, only key names."""
    noun = _entries_noun(count)
    return {
        'check': REJECTED_LIST_CHECK,
        'code': code,
        'section': None,
        'message': f"{count} {code} {noun}: {message}",
        'evidence': [f"{code}.{key}: {count} {noun}", *stray[:_EVIDENCE_KEYS_SHOWN]],
        'severity': _WARN_SEVERITY,
    }


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
        out.append(_list_warning(
            code, key, len(cases),
            f"`{key}` holds records that were not split into separate rows ({why}); "
            "a record may be missing from the output", stray))
    return out


def _stage4_decline_warnings(declined: Mapping[tuple[str, str], int]) -> list[dict[str, Any]]:
    """One sidecar WARN per (code, records key) for the entries whose
    declined stage-4 records list is not all in the document (#1187)."""
    return [_list_warning(
        code, key, count,
        f"`{key}` holds records that were not split into separate rows (stage 4's "
        "records were kept whole and only the last one renders); a record may be "
        "missing from the output")
        for (code, key), count in sorted(declined.items())]


def _unlisted_year_warnings(unlisted: Mapping[tuple[str, str], int]) -> list[dict[str, Any]]:
    """One sidecar WARN per (code, list key) for entries that fanned out
    without the text coverage test (`_NO_TEXT_COVERAGE_CODES`) while their text
    names a year no record holds: a dated record stage 4 left out of the list
    (#1187). Not raised for stage 4's own records list."""
    return [_list_warning(
        code, key, count,
        f"`{key}` was split into separate rows, but the entry's text names a "
        "year no record holds; a record may be missing from the output")
        for (code, key), count in sorted(unlisted.items())]


def _lists_a_year_no_record_holds(entry: Mapping[str, Any],
                                  children: Sequence[Mapping[str, Any]]) -> bool:
    """Whether the entry's text names a year (a dated record) that none of its
    children's fields hold (#1187)."""
    uncovered = _uncovered_tokens(entry, [child['extracted_fields'] for child in children])
    return any(_YEAR_TOKEN_RE.fullmatch(token) for token in uncovered or ())


def _lists_an_unlisted_year(entry: Mapping[str, Any], children: Sequence[Mapping[str, Any]] | None,
                            records_key: str | None) -> bool:
    """Whether an entry fanned out of a generic list without the coverage test
    while its text names a year no child holds. Only a generic list: stage
    4's own records list reads its years from the reply, and B1 attendance
    ranges there are not lost records."""
    return bool(children) and entry.get('taxonomy_code') in _NO_TEXT_COVERAGE_CODES \
        and FANNED_OUT_FROM in children[0] \
        and children[0][FANNED_OUT_FROM]['key'] != records_key \
        and _lists_a_year_no_record_holds(entry, children)


def _declined_record_lists(entry: Mapping[str, Any], schema: frozenset[str]) -> dict[str, list[str]]:
    """Every record-list key of an entry `_fan_out_entry` declined: the lists
    `_record_list` refused (with their stray item keys) plus the ones it
    accepted that a later guard then declined (no stray keys)."""
    fields = entry.get('extracted_fields')
    if not isinstance(fields, Mapping):
        return {}
    return {**{_path_name(path): [] for path in _record_keys(fields, schema)},
            **_rejected_record_lists(fields, schema)}


def _h_renders_list(fields: Mapping[str, Any], key: str) -> bool:
    """Whether H's date cell writes the list at `key` (`H_DATE_LIST_KEYS`):
    it is the first of those keys holding a value, every item of it is
    dates only, and the record has no date of its own (#1187)."""
    if any(not _is_blank(fields.get(own)) for own in _H_OWN_DATE_KEYS):
        return False
    first = next((name for name in H_DATE_LIST_KEYS if not _is_blank(fields.get(name))), None)
    if key != first:
        return False
    items = fields[key]
    return isinstance(items, list) and all(
        isinstance(item, Mapping) and all(_is_date_key(name) for name in item)
        for item in items)


@dataclass(frozen=True)
class DeclinedList:
    """A record list fan-out kept whole for a `_GENERIC_LIST_CHECKED_AFTER_RENDER`
    code, or the stage-4 records list of a `_STAGE4_LIST_CHECKED_AFTER_RENDER`
    one (#1187). Whether its records reached the document is known only
    after the render, so stage 6 hands the rendered text to
    `unrendered_list_warnings`, which warns for the lists some record of
    which is not there."""

    code: str
    key: str
    # Item keys outside the code's schema (key names, never values).
    stray: tuple[str, ...]
    records: tuple[Mapping[str, Any], ...]
    # True for stage 4's own records list (`records_key`).
    stage4: bool = False


def _checked_declined_lists(entry: Mapping[str, Any], schema: frozenset[str],
                            records_key: str | None) -> list[DeclinedList]:
    """The declined record lists of an entry `_fan_out_entry` returned None
    for, when its code is checked after the render: every one, less the
    date list H's date cell writes (`_h_renders_list`) and, for a
    `_STAGE4_LIST_CHECKED_AFTER_RENDER` code, stage 4's records list, which
    `_declined_stage4_list` checks."""
    code = str(entry.get('taxonomy_code'))
    if code not in _GENERIC_LIST_CHECKED_AFTER_RENDER:
        return []
    fields = entry.get('extracted_fields')
    out = []
    for name, stray in _declined_record_lists(entry, schema).items():
        if code == _H_CODE and _h_renders_list(fields, name):
            continue
        if code in _STAGE4_LIST_CHECKED_AFTER_RENDER and name == records_key:
            continue
        records = _list_at(fields, tuple(name.split(_PATH_SEPARATOR)))
        out.append(DeclinedList(code, name, tuple(stray), tuple(records)))
    return out


def _stage4_list_rendered_whole(fields: Mapping[str, Any],
                                records: Sequence[Mapping[str, Any]]) -> bool:
    """Whether stage 5 rendered every record of a stage-4 records list in
    the entry's own output: 5c's `formatted_text`, or a 5d citation that
    writes two or more of the records' titles (#1187)."""
    return bool(fields.get(_FORMATTED_TEXT_KEY)) or len(_cited_records(fields, records)) > 1


def _declined_stage4_list(entry: Mapping[str, Any], children: Sequence[Mapping[str, Any]] | None,
                          records_key: str | None) -> DeclinedList | None:
    """The stage-4 records list of a `_STAGE4_LIST_CHECKED_AFTER_RENDER`
    entry that `_stage4_children` declined (no child is its last record)
    and stage 5 did not render whole, or None (#1187)."""
    code = str(entry.get('taxonomy_code'))
    if records_key is None or code not in _STAGE4_LIST_CHECKED_AFTER_RENDER:
        return None
    fields = entry.get('extracted_fields')
    if not isinstance(fields, Mapping) or not _is_dict_list(fields.get(records_key)):
        return None
    if children is not None and any(LAST_STAGE4_RECORD in child for child in children):
        return None
    records = fields[records_key]
    if _stage4_list_rendered_whole(fields, records):
        return None
    return DeclinedList(code, records_key, (), tuple(records), stage4=True)


def _distinct_record_words(records: Sequence[Mapping[str, Any]]) -> list[frozenset[str]]:
    """Each record's content words (`_tokens`: dates read as their year), less
    one-letter initials and, in a list of two or more, the words every record
    holds: what tells the record apart from its siblings and from other
    entries naming the same people. web204 S3 1814's chapters all carry
    "Allison, D. B.", whose words, with a coauthor's, vouched for every
    chapter from the publication list while none was printed (#1187)."""
    words = [frozenset(_tokens(_leaf_text(record))) for record in records]
    shared = frozenset.intersection(*words) if len(words) > 1 else frozenset()
    return [frozenset(word for word in own - shared if len(word) >= _MIN_DISTINCT_WORD_LENGTH)
            for own in words]


def _field_count(record: Mapping[str, Any]) -> int:
    """How many fields of `record` hold a value."""
    return sum(1 for value in record.values() if not _is_blank(value))


def _row_windows(table_rows: Sequence[Sequence[frozenset[str]]], size: int) -> list[frozenset[str]]:
    """The words of every run of `size` consecutive rows of each table: a
    block that writes a record one field per row (a grant's, a mentee's;
    web26 M2A) spans no more rows than the record has fields."""
    return [frozenset().union(*rows[i:i + size]) for rows in table_rows for i in range(len(rows))]


def _record_in_document(words: frozenset[str], line_words: Sequence[frozenset[str]]) -> bool:
    """Whether one rendered line holds at least `_MIN_RECORD_SHARE_IN_LINE` of
    a record's distinct words (`_distinct_record_words`). A record with no
    such word has nothing to lose."""
    return not words or any(len(words & line) >= _MIN_RECORD_SHARE_IN_LINE * len(words)
                            for line in line_words)


def _list_in_document(found: DeclinedList, line_words: Sequence[frozenset[str]],
                      table_rows: Sequence[Sequence[frozenset[str]]]) -> bool:
    """Whether every record of `found` is in the document: on one rendered
    line, or in a run of table rows no longer than its field count."""
    windows: dict[int, list[frozenset[str]]] = {}
    for record, words in zip(found.records, _distinct_record_words(found.records), strict=True):
        size = max(1, _field_count(record))
        if size not in windows:
            windows[size] = _row_windows(table_rows, size)
        if not _record_in_document(words, [*line_words, *windows[size]]):
            return False
    return True


def unrendered_list_warnings(declined: Sequence[DeclinedList], document_lines: Sequence[str],
                             table_rows: Sequence[Sequence[str]] = ()) -> list[dict[str, Any]]:
    """The render warnings (`REJECTED_LIST_CHECK`) for the `declined` lists a
    record of which the rendered document does not hold -- in no line of
    `document_lines`, and in no run of rows of `table_rows` (each table's
    rows' text) -- one per (code, list key), as `fan_out_multi_record_entries`
    raises for the codes it warns on directly (#1187)."""
    if not declined:
        return []
    line_words = [frozenset(_tokens(line)) for line in document_lines]
    row_words = [[frozenset(_tokens(row)) for row in rows] for rows in table_rows]
    rejected: dict[tuple[str, str], list[list[str]]] = {}
    stage4: Counter[tuple[str, str]] = Counter()
    for found in declined:
        if _list_in_document(found, line_words, row_words):
            continue
        if found.stage4:
            stage4[(found.code, found.key)] += 1
        else:
            rejected.setdefault((found.code, found.key), []).append(list(found.stray))
    return [*_rejection_warnings(rejected), *_stage4_decline_warnings(stage4)]


def _renders_something(record: Mapping[str, Any], rendered: frozenset[str]) -> bool:
    """Whether `record` holds a value under a field its section writes."""
    return any(not _is_blank(record.get(key)) for key in rendered)


def _date_tokens(record: Mapping[str, Any], code: str) -> set[str]:
    """What a record's date fields put in the code's date column, as `_tokens`
    reads it: its years, an open end as 'present'."""
    return set().union(*(_tokens(_rendered_text(key, value, code))
                         for key, value in record.items()
                         if _is_date_key(key) and not _is_blank(value)))


def _names_of_record(record: Mapping[str, Any], records: Sequence[Mapping[str, Any]]) -> list[str]:
    """`record`'s non-date string values, normalized, that no sibling's value
    holds: what names this record and no other. A value written inside a
    sibling's ("President" in "Vice President", "Glade" in "Glade Society
    Chair") would find that sibling's line (#1342)."""
    siblings = [norm(value) for other in records if other is not record
                for value in other.values() if isinstance(value, str)]
    names = [norm(value) for key, value in record.items()
             if isinstance(value, str) and value.strip() and not _is_date_key(key)]
    return [name for name in names if not any(name in sibling for sibling in siblings)]


def _begins_with_name(segment: str, name: str) -> bool:
    """Whether `segment` opens with `name` as whole words, a hyphenated word
    whole too: "President" does not open "President-elect 2010-2012"."""
    return re.match(rf'{re.escape(name)}(?![\w-])', norm(segment)) is not None


def _dated_on_own_line(record: Mapping[str, Any], records: Sequence[Mapping[str, Any]],
                       segments: Sequence[str], code: str) -> bool:
    """Whether one segment both opens with one of `record`'s own names and
    holds every year its dates show: "Co-leader of ... program 2020-2021
    Member of ..." for the co-leader's 2020-2021, though the segments are
    out of step with the records above it (OIEPQD TVQZIP 116, #1342). A name
    further into a line ("Member" in "Executive Committee Member 2010-2012")
    does not make it that record's line. Never when a sibling carries the
    same dates: a date copied from a sibling always equals the sibling's, so
    such a record falls back to the nearest-line rule and loses it (#1342)."""
    dates = _date_tokens(record, code)
    if any(_date_tokens(other, code) == dates for other in records if other is not record):
        return False
    own = _names_of_record(record, records)
    return any(dates <= set(_tokens(segment)) and any(_begins_with_name(segment, name) for name in own)
               for segment in segments)


def _unsourced_date_records(records: Sequence[Mapping[str, Any]], text: object,
                            code: str) -> set[int]:
    """The indexes of the records whose dates their own line does not give
    them (#1445, EOAHMI WYMVGU 865).

    Read only when the entry's tab segments line up one to one with the
    records, so each segment is one record's line. An undated line under a
    dated one may share that line's dates -- a year printed once over two
    talks -- so a record whose own line names no year keeps dates that the
    nearest dated line above it names. Dates it took from anywhere else (the
    board term two lines up, copied onto an undated "Past-President" below
    two later terms) are not in the source for that record. A record above
    every dated line, and every record of an entry whose lines do not line
    up, is left as stage 4 wrote it. So is a record whose dates sit on a
    line that names it (`_dated_on_own_line`), wherever that line falls.
    """
    segments = _segments(text)
    if len(segments) != len(records):
        return set()
    unsourced = set()
    nearest_dated: str | None = None
    for i, (record, segment) in enumerate(zip(records, segments)):
        if _is_dated(segment):
            nearest_dated = segment
        elif nearest_dated is not None \
                and not _date_tokens(record, code) <= set(_tokens(nearest_dated)) \
                and not _dated_on_own_line(record, records, segments, code):
            unsourced.add(i)
    return unsourced


def _without_dates(fields: Mapping[str, Any]) -> dict[str, Any]:
    """`fields` with every date field cleared, so the row's date cell is empty."""
    return {key: None if _is_date_key(key) else value for key, value in fields.items()}


def _row_identity(record: Mapping[str, Any], rendered: frozenset[str],
                  code: str) -> tuple[tuple[str, str], ...]:
    """What a record's row shows: every rendered value as its cell writes it,
    a date as the code's date column shows it -- the written dates outside
    `rendered` included (`_is_written_date`, #1556 d), so two spans of one
    journal are two rows."""
    return tuple(sorted((key, norm(_rendered_text(key, value, code)))
                        for key, value in record.items()
                        if (key in rendered or _is_written_date(code, key, value, record))
                        and not _is_blank(value)))


def _repeated_rows(records: Sequence[Mapping[str, Any]], rendered: frozenset[str],
                   code: str) -> set[int]:
    """The indexes of the records whose row a LATER record of the same entry
    would print identically (#1445, EOAHMI QTATUP 980: seven dates of one
    talk series in 2003, in a column that shows the year only, printed seven
    identical rows). The later copy is the one kept, so the last record --
    the parent, which dedup weighs -- always stays."""
    identities = [_row_identity(record, rendered, code) for record in records]
    return {i for i, identity in enumerate(identities) if identity in identities[i + 1:]}


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
    instead, and holds its own line (`OWN_TEXT_KEY`) for the renderers'
    fallbacks. Every earlier child is its record alone, with its own
    segment or built line, and without the parent's `_STAGE5_ENTRY_KEYS`: a
    scalar or an enrichment inherited from the last record would put that
    record's value on another one. Declined for a code whose section
    renders the text rather than the fields, for an entry stage 5c rendered
    whole (`formatted_text`), and when a record holds no value its section
    writes (its row would be empty).

    #1243: stage 5d's `formatted_citation` (and its `formatting_source`)
    stays only on the child of the record whose title it writes
    (`_citation_record`); every other child, the last included, renders
    from its own fields. A citation that writes no single record's title
    declines the split, as every citation did before.

    #1445: a record keeps no dates its own line does not give it
    (`_unsourced_date_records`), and an earlier record whose row a later one
    prints identically is not emitted (`_repeated_rows`).
    """
    code = str(entry.get('taxonomy_code'))
    rendered = _RENDERED_FIELDS.get(code)
    records = fields[records_key]
    if rendered is None or fields.get(_FORMATTED_TEXT_KEY):
        return None
    anchors = rendered - _NON_ANCHOR_FIELDS.get(code, frozenset())
    if not all(_renders_something(record, anchors) for record in records):
        return None
    cited = _citation_record(fields, records)
    if fields.get(_FORMATTED_CITATION_KEY) and cited is None:
        return None
    texts = _child_texts(entry.get('text'), records)
    built = _texts_are_built(entry.get('text'), records)
    own_text = texts[-1] if _fields_carry_text(entry, records) else None
    unsourced = _unsourced_date_records(records, entry.get('text'), code)
    records = [_without_dates(record) if i in unsourced else dict(record)
               for i, record in enumerate(records)]
    repeated = _repeated_rows(records, rendered, code)
    citation = {key: fields[key] for key in _CITATION_KEYS if key in fields}
    bare = {key: value for key, value in entry.items() if key not in _STAGE5_ENTRY_KEYS}
    earlier = [_child(bare, {**copy.deepcopy(record), **(citation if i == cited else {})},
                      text, records_key, i, len(records), built=built)
               for i, (record, text) in enumerate(zip(records[:-1], texts))
               if i not in repeated]
    last = copy.deepcopy(dict(entry))
    last_cited = cited == len(records) - 1
    last['extracted_fields'] = {key: value for key, value in last['extracted_fields'].items()
                                if key != records_key and (last_cited or key not in _CITATION_KEYS)}
    if len(records) - 1 in unsourced:
        last['extracted_fields'] = _without_dates(last['extracted_fields'])
    last[LAST_STAGE4_RECORD] = {'key': records_key, 'index': len(records) - 1,
                                'count': len(records), OWN_TEXT_KEY: own_text}
    return [*earlier, last]


def _title_words(text: object) -> str:
    """`text`'s words, lowercased and space-joined: a title compared with the
    citation that writes it, whatever its punctuation and quotes."""
    return ' '.join(_TOKEN_RE.findall(norm(str(text or ''))))


def _citation_record(fields: Mapping[str, Any],
                     records: Sequence[Mapping[str, Any]]) -> int | None:
    """The index of the one record whose title stage 5d's citation writes,
    or None: no citation, no record's title in it, or several (a citation
    of the whole list, which no single record may carry) that the citation's
    years do not tell apart (`_hit_cited_by_year`)."""
    hits = _cited_records(fields, records)
    if len(hits) == 1:
        return hits[0]
    return _hit_cited_by_year(fields, records, hits)


def _cited_records(fields: Mapping[str, Any],
                   records: Sequence[Mapping[str, Any]]) -> list[int]:
    """The indexes of the records whose title stage 5d's citation writes;
    empty when there is no citation."""
    citation = _title_words(fields.get(_FORMATTED_CITATION_KEY))
    if not citation:
        return []
    titles = [_record_title(record) for record in records]
    return [i for i, title in enumerate(titles) if title and f' {title} ' in f' {citation} ']


def _record_title(record: Mapping[str, Any]) -> str:
    """`record`'s title (`_TITLE_KEYS`), as `_title_words` reads it."""
    return _title_words(next((record[key] for key in _TITLE_KEYS if record.get(key)), ''))


# A year a citation or a record's date field writes, matched by
# `_hit_cited_by_year`; a letter may follow it ("2019a", a second paper of a
# year in an author-year list).
_CITATION_YEAR_RE = re.compile(r'\b((?:19|20)\d{2})[a-z]?\b')
# A DOI or a link inside a citation: its digits ("10.1000/zq.2009.1") are no
# year of the record.
_CITATION_LINK_RE = re.compile(r'\b10\.\d{4,9}/\S+|https?://\S+|\bwww\.\S+', re.IGNORECASE)


def _record_years(record: Mapping[str, Any]) -> frozenset[str]:
    """The years `record`'s date fields write (`_is_date_key`)."""
    return frozenset(year for key, value in record.items() if _is_date_key(key)
                     for year in _CITATION_YEAR_RE.findall(str(value)))


def _hit_cited_by_year(fields: Mapping[str, Any], records: Sequence[Mapping[str, Any]],
                       hits: Sequence[int]) -> int | None:
    """The one of `hits`, the records whose title the citation writes, that it
    names by year, or None.

    #1243 (HNQBOI 19): stage 4 split one title written for two years into a
    record per year, and 5d cited it once, with one of the years; the citation
    writes both records' title, so the split was declined and the entry's
    other records were lost. When every hit has one title, the hit whose date years
    all appear in the citation is the one it names, if exactly one does.
    Hits with different titles are a citation of the whole list. A citation
    that writes the year of every hit (one article split twice in one year,
    BCTOGR 2166; a talk cited with all its venues and years, EYTHNB 89) names
    no single one, and neither does one beside an undated hit: no years are
    in every citation."""
    if len({_record_title(records[i]) for i in hits}) != 1:
        return None
    citation = _CITATION_LINK_RE.sub(' ', str(fields.get(_FORMATTED_CITATION_KEY) or ''))
    cited_years = frozenset(_CITATION_YEAR_RE.findall(citation))
    named = [i for i in hits if _record_years(records[i]) <= cited_years]
    return named[0] if len(named) == 1 else None


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
    if str(entry.get('taxonomy_code')) not in _NO_TEXT_COVERAGE_CODES \
            and not _fields_carry_text(entry, child_fields):
        return None
    texts = _child_texts(entry.get('text'), records)
    built = _texts_are_built(entry.get('text'), records)
    return [_child(entry, fields, text, _path_name(paths[0]), i, len(records), built=built)
            for i, (fields, text) in enumerate(zip(child_fields, texts))]


def inherited_scope(entry: Mapping[str, Any]) -> str | None:
    """The geographic scope an entry takes from the CV: the one scope its
    nearest heading naming any names ("National", "NATIONAL SERVICE ROLES",
    "Presentations (State & Local)"). None when that heading names two scopes
    ("International/National") or no heading names one."""
    for heading in reversed(entry.get('hierarchy') or []):
        named = [scope for scope, pattern in _SCOPE_RES if pattern.search(str(heading))]
        if named:
            return named[0] if len(named) == 1 else None
    return None


def record_scope(entry: Mapping[str, Any], classify: Callable[[Mapping[str, Any]], str]) -> str:
    """The scope `entry` files under. An entry the CV files under a scope
    heading (`inherited_scope`) takes it, and `classify` is not asked (#1579).
    A record fanned out of a multi-record entry keeps its heading too, unless
    `classify`, asked about that record alone, puts it abroad (EBYSBC E34). Any
    other entry takes `classify`'s answer."""
    heading = inherited_scope(entry)
    if heading is not None and not is_split_record(entry):
        return heading
    own_scope = classify(entry)
    if heading is None or own_scope == _ABROAD_SCOPE:
        return own_scope
    return heading


def fallback_text(entry: Mapping[str, Any]) -> str:
    """The text a renderer falls back to for a cell `entry`'s fields leave
    empty: its own text, except for the last of stage 4's records, whose text
    is the parent's whole line. That one falls back to its own line
    (`OWN_TEXT_KEY`), so a cell does not print the other records again
    (#1445: EOAHMI DUTAVD 82, WYMVGU 931, BRUSUZ 265) -- unless the records'
    fields leave words of that line unheld, when the whole line stays."""
    own = (entry.get(LAST_STAGE4_RECORD) or {}).get(OWN_TEXT_KEY)
    return str(entry.get('text') or '') if own is None else own


def is_split_record(entry: Mapping[str, Any]) -> bool:
    """Whether `entry` is one record fanned out of a multi-record entry."""
    return FANNED_OUT_FROM in entry or LAST_STAGE4_RECORD in entry


def has_built_text(entry: Mapping[str, Any]) -> bool:
    """Whether `entry`'s text is a line built from its record's fields
    (`BUILT_TEXT_KEY`), whose pipes separate field values, not CV columns."""
    return entry.get(BUILT_TEXT_KEY) is True


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
        records_key: str | None = None,
        declined: list[DeclinedList] | None = None) -> list[dict[str, Any]]:
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
    (`REJECTED_LIST_CHECK`), as it is for a B1 list whose text names a year
    no record holds (`_lists_an_unlisted_year`). For a code in
    `_GENERIC_LIST_CHECKED_AFTER_RENDER`, and a declined stage-4 records list of a
    `_STAGE4_LIST_CHECKED_AFTER_RENDER` code, when `declined` is given, a
    `DeclinedList` is appended to it instead, for the caller to check
    against the rendered document (`unrendered_list_warnings`).

    `records_key` names the list stage 4 keeps when the LLM returned several
    records for one entry (`stage4.schemas.STAGE4_RECORDS_KEY`); None splits
    no such list. `_stage4_children` says how that list fans out.
    """
    out: list[dict[str, Any]] = []
    rejected: dict[tuple[str, str], list[list[str]]] = {}
    unlisted: Counter[tuple[str, str]] = Counter()
    for entry in entries:
        code = entry.get('taxonomy_code')
        schema = _record_schema(code, frozenset(schema_fields.get(code, {}).get('fields', ())))
        children = _fan_out_entry(entry, schema, records_key)
        if _lists_an_unlisted_year(entry, children, records_key):
            unlisted[(str(code), children[0][FANNED_OUT_FROM]['key'])] += 1
        if children is None and code in _LIST_LOST_WHEN_KEPT_WHOLE:
            for key, stray in _declined_record_lists(entry, schema).items():
                rejected.setdefault((str(code), key), []).append(stray)
        if declined is not None:
            if children is None:
                declined.extend(_checked_declined_lists(entry, schema, records_key))
            stage4_list = _declined_stage4_list(entry, children, records_key)
            if stage4_list is not None:
                declined.append(stage4_list)
        out.extend(children if children is not None else [entry])
    if warnings is not None:
        warnings.extend(_rejection_warnings(rejected))
        warnings.extend(_unlisted_year_warnings(unlisted))
    return out
