"""Section B2: other educational experiences (#398).

Everything that is training but not a conferred degree -- fellowship courses,
certificate programs, workshops. Four columns' worth of content rendered into
three: program, institution + location, dates. B1 next door is the degrees; the
split exists because a certificate listed among degrees reads as a degree.

Two layers (#739 review): `_normalize_other_education_entry` resolves one raw
stage-4 dict into an `OtherEducationRecord` -- which field alias names the
program, whether the institution came from enrichment, which of three date
sources won -- and touches no document. `_fill_other_education` only checks
the template has the structure it expects and writes rows.

Like B1, cells are MIXED content -- part plain text, part tracked insertion --
so a reader can see which words the pipeline supplied.
`_add_table_row_with_mixed_content` is shared and stays on
`WCMTemplateGenerator`; this writer only builds the per-cell run lists. Two
things can be attributed:

- the location, from institution enrichment, appended inside the institution
  cell as ", City, ST" and marked "Institution Enrichment";
- the date, when no start/end/year field exists and `_extract_year_from_text`
  recovers one from the raw text. Marked "Text Extraction", because a year that
  was inferred rather than parsed should be visible as such.

The date falls back in three steps -- a start/end range, then a single year
field, then the recovered year -- and each is formatted for B2 (mm/yy).

A record's one date that its line writes after an expiry or renewal label
("Exp. 4/2027", "last renewal February 2021") is no date attended, and the
column is "Dates attended": it is printed once, with its label. When the
Description already writes it after its label, the Dates cell is left empty;
otherwise the cell writes the label before the date, "Expires 04/27" (#1577:
4SOTEA 37, web211 36/37 and web190 953/954 printed bare future dates, and
VQFSDI 139 printed its renewal date twice).

An institution field of the literal string "none" is treated as absent; field
extraction emits it. An entry with neither a program name nor an institution is
skipped, since the remaining columns would say nothing.

An institution the Description already writes whole is not printed again in
the Institution cell, which keeps only the place the Description does not say
(#1577: "Diplomate, <board> | <board> | <year>" on 45 of the real-record
A/B's 215 rows). The Description keeps it, because there it is part of the
credential's own name as the line writes it ("Certified by <commission>",
"Candidate, <board> (<exam step>)", "<board>, #<number>"); cut out of it, the
name would be left as "Certified by" or "Candidate, (<exam step>)".

A program name that is only the label of the credential list the line sat in
("Certifications", "Board Certification": stage 4 took the line's lead label
for the name, CAOACN 40 and SVYSGY 44) names nothing; the record's own line,
less the dates its Dates cell shows, is its Description instead (#1577).

The heading has three names across template revisions ("OTHER EDUCATIONAL",
"SPECIAL TRAINING", "ADDITIONAL TRAINING"). When none of them is present, or
the heading has no table after it, or that table does not have the three
columns this writer fills, the section logs a warning with the entry count
and renders nothing. It does NOT hand the entries to the appendix: B2 is in
RENDER_ROUTED_CODES, so `generate()` treats it as rendered, and
`_recover_unrendered_records` only re-emits fused multi-record entries. The
warning is the record that content was dropped.
"""
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from unified_pipeline.core.credential_lines import is_credential_list_label
from unified_pipeline.core.honor_lines import YEAR_RE
from unified_pipeline.core.text_norm import fold_quotes, norm
from unified_pipeline.core.two_digit_year import expand_two_digit_year

from ..fan_out import (
    BUILT_TEXT_SEPARATOR,
    FOLDED_DATES_KEY,
    fallback_text,
    has_built_text,
)
from ..formatting import (
    DATE_SPAN_SEPARATOR,
    EXPIRES_PREFIX,
    RENEWED_PREFIX,
    format_date_for_section,
    format_date_range,
)
from ..normalization import _get_cleaned_institution_name, line_without_edge_dates
from ..parsing import _extract_year_from_text, _parse_date_components
from ..parsing.dates import EXPIRY_LABEL, RENEWAL_LABEL
from ..resolution import _get_institution_location, _location_remainder
from ..sorting import sort_entries_reverse_chronological

logger = logging.getLogger(__name__)

B2_TAXONOMY_CODE = 'B2'

# Heading text across template revisions, tried in this order (substring,
# case-insensitive).
B2_SECTION_HEADINGS = ("OTHER EDUCATIONAL", "SPECIAL TRAINING", "ADDITIONAL TRAINING")

# The template table is Description | Institution, city and state | Dates
# attended. The writer always builds a row of exactly this many cells, so a
# template with a different grid would put content in the wrong column.
B2_TABLE_COLUMNS = 3

# A B1 row stage 6 moves here (a degree-less pre-collegiate school, EBYSBC
# E33) keeps B1's nested `dates_attended: {start_date, end_date}` dates.
B1_DATES_ATTENDED_KEY = 'dates_attended'

# A stage-4 institution value that means "none extracted", not an institution.
MISSING_INSTITUTION_SENTINEL = 'none'

# Track-change authors for the two attributable cell parts.
INSTITUTION_ENRICHMENT_REASON = "Institution Enrichment"
TEXT_EXTRACTION_REASON = "Text Extraction"

# How far past its label a labelled date's year may sit: "June 28, " before
# "2026", "8/09/" before "21".
LABELLED_DATE_MAX_CHARS = 20
# A date a line writes after an expiry or renewal label (`EXPIRY_LABEL`,
# `RENEWAL_LABEL`), read up to its year: four digits, or the two after a
# slash, dot or hyphen that end the date ("Exp. 4/2027", "Expires 8/09/21",
# "(expires June 28, 2026)", "last renewal February 2021"). The label is a
# whole word, so "experience 2010" names no expiry.
_LABELLED_DATE_RE = re.compile(
    rf"\b(?:(?P<expiry>{EXPIRY_LABEL})|{RENEWAL_LABEL})\b\.?(?:\s+date)?\s*:?\s*"
    rf"[^;|\t\n]{{0,{LABELLED_DATE_MAX_CHARS}}}?"
    rf"(?:(?P<year>{YEAR_RE.pattern})|(?<=[/.-])(?P<yy>\d{{2}})(?![\d/.-]))",
    re.IGNORECASE)


@dataclass(frozen=True)
class OtherEducationRecord:
    """One row of the B2 table, fully resolved.

    Built only by `_normalize_other_education_entry`. `dates_from_text` is
    True when the date came from `_extract_year_from_text` rather than a
    field, so the renderer marks it as a tracked insertion. `source_entry`
    is the stage-4 dict, carried through only because
    `_add_table_row_with_mixed_content` reads it to attach upstream
    pipeline comments to the row.
    """

    program: str = ''
    institution: str = ''
    location: str = ''
    location_is_enriched: bool = False
    dates: str = ''
    dates_from_text: bool = False
    source_entry: Mapping[str, Any] | None = None

    @property
    def is_renderable(self) -> bool:
        """An entry with neither a program nor an institution would fill a
        row that says nothing."""
        return bool(self.program or self.institution)


def _date_label(text: object, year: int | None) -> str:
    """The word a date cell writes before a date in `year` that `text`
    writes after an expiry or renewal label (`_LABELLED_DATE_RE`):
    `EXPIRES_PREFIX` or `RENEWED_PREFIX`; '' when it writes none, or is no
    string (a program stage 4 wrote as a list)."""
    if not isinstance(text, str):
        return ''
    for match in _LABELLED_DATE_RE.finditer(text):
        found = int(match['year']) if match['year'] else expand_two_digit_year(int(match['yy']))
        if found == year:
            return EXPIRES_PREFIX if match['expiry'] else RENEWED_PREFIX
    return ''


def _labelled_dates(dates: str, lone_date: object, raw_text: str, program: str) -> str:
    """`dates`, the Dates cell of a record whose one date is `lone_date`,
    unless the line writes that date after an expiry or renewal label
    (#1577): '' when the Description already writes it after its label, so
    it is printed once; else the cell with the label before it ("Expires
    04/27"), so it reads as no date attended. `dates` as it is for a date
    the line labels no such way."""
    year = _parse_date_components(str(lone_date or ''))[0]
    if _date_label(program, year):
        return ''
    label = _date_label(raw_text, year)
    return f"{label} {dates}" if label else dates


def _field_dates(fields: Mapping[str, Any], raw_text: str) -> tuple[str, object]:
    """(formatted dates, the one date) of a record's date fields, by the
    first two steps of `_resolve_b2_dates`'s precedence; the one date is ''
    for a range, and both are '' for a record with no date field."""
    start = fields.get('start_date', '')
    end = fields.get('end_date', '')
    attended = fields.get(B1_DATES_ATTENDED_KEY)
    if not (start or end) and isinstance(attended, Mapping):
        start = attended.get('start_date') or ''
        end = attended.get('end_date') or ''
    if start or end:
        lone = '' if start and end and start != end else start or end
        return format_date_range(start, end, B2_TAXONOMY_CODE, raw_text), lone
    year = fields.get('year', '') or fields.get('year_awarded', '')
    if year:
        return format_date_for_section(year, B2_TAXONOMY_CODE), year
    return '', ''


def _folded_dates(folded: object) -> str:
    """The Dates cell of a row fan-out folded several records of one entry
    into (`fan_out.FOLDED_DATES_KEY`, #1577): each record's dates
    (`_field_dates`) in order, repeats dropped, joined with
    `DATE_SPAN_SEPARATOR`; '' when fewer than two records were folded.
    Each is one occasion: the entry's line is not read for an open range,
    since its "<year> - <name>" opens one record of several, not a range
    (HXBPCT 138 read "1998-Present")."""
    if not isinstance(folded, list) or len(folded) < 2:
        return ''
    shown = [_field_dates(fields, '')[0] for fields in folded]
    return DATE_SPAN_SEPARATOR.join(dict.fromkeys(dates for dates in shown if dates))


def _resolve_b2_dates(fields: Mapping[str, Any], raw_text: str, program: str,
                      folded: object = None) -> tuple[str, bool]:
    """(formatted dates, came-from-raw-text) by the three-step precedence:
    a start/end range (B1's nested `dates_attended` when the flat keys are
    empty), then a single year field (year, then year_awarded),
    then a year recovered from the entry text. Each step wins outright when
    it has anything at all, so a start date beats a year beats the text.

    A start with no end is one attended occasion, so it renders as the bare
    date; `raw_text` is passed so a source that writes the range open
    ("2020-", "present") still renders "<start>-Present" (#1220). A record's
    one date that its line labels an expiry or a renewal keeps its label,
    or is left to the Description (`_labelled_dates`, #1577). A row folded
    from several records lists all their dates (`_folded_dates`, #1577).
    """
    folded_dates = _folded_dates(folded)
    if folded_dates:
        return folded_dates, False
    dates, lone = _field_dates(fields, raw_text)
    if dates:
        return (_labelled_dates(dates, lone, raw_text, program) if lone else dates), False
    extracted_year = _extract_year_from_text(raw_text) if raw_text else ''
    if extracted_year:
        dates = _labelled_dates(format_date_for_section(extracted_year, B2_TAXONOMY_CODE),
                                extracted_year, raw_text, program)
        return dates, bool(dates)
    return '', False


def _line_as_program(entry: Mapping[str, Any], fields: Mapping[str, Any]) -> str:
    """The record's own line (`fan_out.fallback_text`; a line built from a
    split record's fields reads its values comma-joined) less the dates its
    Dates cell shows, whitespace collapsed: the program of a record whose
    name is only its list's label (#1577)."""
    line = fallback_text(entry)
    if has_built_text(entry):
        line = line.replace(BUILT_TEXT_SEPARATOR, ', ')
    start = fields.get('start_date') or fields.get('year') or fields.get('year_awarded')
    return ' '.join(line_without_edge_dates(line, start, fields.get('end_date')).split())


def _normalize_other_education_entry(entry: Mapping[str, Any]) -> OtherEducationRecord:
    """Resolve one raw stage-4 B2 dict into a typed record.

    `extracted_fields` is sometimes explicitly `None` rather than absent
    (#659), which a bare `.get('extracted_fields', {})` does not cover.
    The `or` chains fall through on an empty string as well as a missing
    key, which is how stage 4 writes an unused alias.
    """
    fields = entry.get('extracted_fields') or {}
    raw_text = entry.get('text', '')

    program = (fields.get('program_name', '') or fields.get('program_type', '')
               or fields.get('title', ''))
    if is_credential_list_label(program):
        program = _line_as_program(entry, fields) or program

    # Institution: the stage-5b cleaned name wins over the raw field; the
    # literal "none" is an absent value.
    institution = fields.get('institution', '')
    if institution and institution.lower() == MISSING_INSTITUTION_SENTINEL:
        institution = ''
    institution = _get_cleaned_institution_name(entry) or institution

    location, location_is_enriched = _get_institution_location(entry)
    dates, dates_from_text = _resolve_b2_dates(fields, raw_text, program, entry.get(FOLDED_DATES_KEY))

    return OtherEducationRecord(
        program=program,
        institution=institution,
        location=location,
        location_is_enriched=location_is_enriched,
        dates=dates,
        dates_from_text=dates_from_text,
        source_entry=entry,
    )


def _named_in_program(institution: str, program: object) -> bool:
    """Whether the Description (`program`) writes the whole `institution`
    as words of its own, case, accents, quotes and spacing aside (#1577)."""
    if not (institution and isinstance(program, str)):
        return False
    name = re.escape(norm(fold_quotes(institution)))
    return re.search(rf'(?<!\w){name}(?!\w)', norm(fold_quotes(program))) is not None


def _b2_institution_content(record: OtherEducationRecord) -> list[tuple[str, bool, str]]:
    """The runs of the institution cell: (text, is_tracked_insertion, author).
    An enriched location is a tracked insertion after the institution; an
    extracted one is plain text joined onto it. Only the part of the location
    the institution does not already say is added (`_location_remainder`):
    a row whose institution IS the place no longer repeats it (#1257). An
    institution the Description already writes is left out, its location
    kept (`_named_in_program`, #1577)."""
    location = _location_remainder(record.location, record.institution)
    institution = '' if _named_in_program(record.institution, record.program) else record.institution
    if location and record.location_is_enriched:
        if institution:
            return [(institution, False, ""),
                    (f", {location}", True, INSTITUTION_ENRICHMENT_REASON)]
        return [(location, True, INSTITUTION_ENRICHMENT_REASON)]
    if location:
        institution_full = (f"{institution}, {location}"
                            if institution else location)
        return [(institution_full, False, "")]
    return [(institution, False, "")]


class OtherEducationSection:
    """Section B2 writers, mixed into `WCMTemplateGenerator`."""

    def _fill_other_education(self, entries: Sequence[Mapping[str, Any]]) -> None:
        """Fill Other Educational Experiences section (B2 entries).

        B2 entries are training programs, certifications, workshops - not formal degrees.
        These go in a separate section from the main Education table.

        Rendering only: `_normalize_other_education_entry` has resolved every
        field, so nothing below reads `extracted_fields`. A template that
        lacks the heading, the table, or the three-column grid is a template
        mismatch, not an empty-data condition: it is logged with the entry
        count and nothing is written.
        """
        if not entries:
            return

        logger.info("Filling Other Educational Experiences (%d entries)...", len(entries))

        section_idx = None
        for heading in B2_SECTION_HEADINGS:
            section_idx = self._find_header_paragraph(heading)
            if section_idx is not None:
                break
        if section_idx is None:
            logger.warning(
                "Other Educational Experiences: section heading not found in "
                "template; %d entries not rendered", len(entries))
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            logger.warning(
                "Other Educational Experiences: table not found after section "
                "heading; %d entries not rendered", len(entries))
            return
        if len(table.columns) != B2_TABLE_COLUMNS:
            logger.warning(
                "Other Educational Experiences: expected a %d-column table, "
                "found %d columns; %d entries not rendered",
                B2_TABLE_COLUMNS, len(table.columns), len(entries))
            return

        self._clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        for entry in sort_entries_reverse_chronological(entries):
            record = _normalize_other_education_entry(entry)
            if not record.is_renderable:
                continue
            self._add_table_row_with_mixed_content(
                table,
                [[(record.program, False, "")],
                 _b2_institution_content(record),
                 [(record.dates, record.dates_from_text, TEXT_EXTRACTION_REASON)]],
                entry=record.source_entry,
            )
