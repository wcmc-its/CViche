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

An institution field of the literal string "none" is treated as absent; field
extraction emits it. An entry with neither a program name nor an institution is
skipped, since the remaining columns would say nothing.

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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..formatting import (
    _clear_table_data,
    format_date_for_section,
    format_date_range,
)
from ..normalization import _get_cleaned_institution_name
from ..parsing import _extract_year_from_text
from ..resolution import _get_institution_location
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

# A stage-4 institution value that means "none extracted", not an institution.
MISSING_INSTITUTION_SENTINEL = 'none'

# Track-change authors for the two attributable cell parts.
INSTITUTION_ENRICHMENT_REASON = "Institution Enrichment"
TEXT_EXTRACTION_REASON = "Text Extraction"


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


def _resolve_b2_dates(fields: Mapping[str, Any], raw_text: str) -> tuple[str, bool]:
    """(formatted dates, came-from-raw-text) by the three-step precedence:
    a start/end range, then a single year field (year, then year_awarded),
    then a year recovered from the entry text. Each step wins outright when
    it has anything at all, so a start date beats a year beats the text.

    A start with no end is one attended occasion, so it renders as the bare
    date; `raw_text` is passed so a source that writes the range open
    ("2020-", "present") still renders "<start>-Present" (#1220).
    """
    start = fields.get('start_date', '')
    end = fields.get('end_date', '')
    if start or end:
        return format_date_range(start, end, B2_TAXONOMY_CODE, raw_text), False
    year = fields.get('year', '') or fields.get('year_awarded', '')
    if year:
        return format_date_for_section(year, B2_TAXONOMY_CODE), False
    extracted_year = _extract_year_from_text(raw_text) if raw_text else ''
    if extracted_year:
        return format_date_for_section(extracted_year, B2_TAXONOMY_CODE), True
    return '', False


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

    # Institution: the stage-5b cleaned name wins over the raw field; the
    # literal "none" is an absent value.
    institution = fields.get('institution', '')
    if institution and institution.lower() == MISSING_INSTITUTION_SENTINEL:
        institution = ''
    institution = _get_cleaned_institution_name(entry) or institution

    location, location_is_enriched = _get_institution_location(entry)
    dates, dates_from_text = _resolve_b2_dates(fields, raw_text)

    return OtherEducationRecord(
        program=program,
        institution=institution,
        location=location,
        location_is_enriched=location_is_enriched,
        dates=dates,
        dates_from_text=dates_from_text,
        source_entry=entry,
    )


def _b2_institution_content(record: OtherEducationRecord) -> list[tuple[str, bool, str]]:
    """The runs of the institution cell: (text, is_tracked_insertion, author).
    An enriched location is a tracked insertion after the institution; an
    extracted one is plain text joined onto it."""
    if record.location and record.location_is_enriched:
        if record.institution:
            return [(record.institution, False, ""),
                    (f", {record.location}", True, INSTITUTION_ENRICHMENT_REASON)]
        return [(record.location, True, INSTITUTION_ENRICHMENT_REASON)]
    if record.location:
        institution_full = (f"{record.institution}, {record.location}"
                            if record.institution else record.location)
        return [(institution_full, False, "")]
    return [(record.institution, False, "")]


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

        _clear_table_data(table, keep_header=True)
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
