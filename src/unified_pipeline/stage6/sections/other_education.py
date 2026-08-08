"""Section B2: other educational experiences (#398).

Everything that is training but not a conferred degree -- fellowship courses,
certificate programs, workshops. Four columns' worth of content rendered into
three: program, institution + location, dates. B1 next door is the degrees; the
split exists because a certificate listed among degrees reads as a degree.

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
"SPECIAL TRAINING", "ADDITIONAL TRAINING"). When none of them is present the
section returns rather than inventing one -- these entries then reach the
appendix, which is a visible loss rather than a silent one.
"""
from typing import Dict, List

from ..formatting import (
    _clear_table_data,
    format_date_for_section,
    format_date_range,
)
from ..normalization import _get_cleaned_institution_name
from ..parsing import _extract_year_from_text
from ..resolution import _get_institution_location
from ..sorting import sort_entries_reverse_chronological


class OtherEducationSection:
    """Section B2 writers, mixed into `WCMTemplateGenerator`."""

    def _fill_other_education(self, entries: List[Dict]):
        """Fill Other Educational Experiences section (B2 entries).

        B2 entries are training programs, certifications, workshops - not formal degrees.
        These go in a separate section from the main Education table.
        """
        if not entries:
            return

        if self.verbose:
            print(f"Filling Other Educational Experiences ({len(entries)} entries)...")

        # Try to find the "OTHER EDUCATIONAL" or similar section
        section_idx = self._find_paragraph_with_text("OTHER EDUCATIONAL")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("SPECIAL TRAINING")
        if section_idx is None:
            section_idx = self._find_paragraph_with_text("ADDITIONAL TRAINING")

        if section_idx is None:
            # No dedicated section found - these entries will need to go elsewhere
            # For now, skip them (they could go in appendix or we could create a section)
            if self.verbose:
                print(f"  No 'Other Educational Experiences' section found in template")
            return

        table = self._find_table_after_paragraph(section_idx)
        if not table:
            return

        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        # Sort entries reverse chronologically (most recent first)
        sorted_entries = sort_entries_reverse_chronological(entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {})
            raw_text = entry.get('text', '')

            # Program/Training name
            program_name = (fields.get('program_name', '') or
                          fields.get('program_type', '') or
                          fields.get('title', ''))

            # Institution - use cleaned_name from enrichment if available
            institution = fields.get('institution', '')
            if institution and institution.lower() == 'none':
                institution = ''
            cleaned = _get_cleaned_institution_name(entry)
            if cleaned:
                institution = cleaned

            # Skip entries with no meaningful content
            if not program_name and not institution:
                continue

            # Location from enrichment
            location, location_is_enriched = _get_institution_location(entry)

            # Dates - format according to B2 requirements (mm/yy – mm/yy)
            start = fields.get('start_date', '')
            end = fields.get('end_date', '')
            year = fields.get('year', '') or fields.get('year_awarded', '')
            year_is_enriched = False

            # Try to build date range, or fall back to single year
            if start or end:
                dates = format_date_range(start, end, 'B2')
            elif year:
                dates = format_date_for_section(year, 'B2')
            else:
                # Try to extract from raw text
                extracted_year = _extract_year_from_text(raw_text) if raw_text else ''
                if extracted_year:
                    dates = format_date_for_section(extracted_year, 'B2')
                    year_is_enriched = True
                else:
                    dates = ''

            # Build cell contents
            program_content = [(program_name, False, "")]

            if location and location_is_enriched:
                if institution:
                    institution_content = [
                        (institution, False, ""),
                        (f", {location}", True, "Institution Enrichment")
                    ]
                else:
                    institution_content = [(location, True, "Institution Enrichment")]
            elif location:
                institution_full = f"{institution}, {location}" if institution else location
                institution_content = [(institution_full, False, "")]
            else:
                institution_content = [(institution, False, "")]

            dates_content = [(dates, year_is_enriched, "Text Extraction")]

            self._add_table_row_with_mixed_content(
                table,
                [program_content, institution_content, dates_content],
                entry=entry
            )
