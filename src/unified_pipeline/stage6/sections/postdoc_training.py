"""Section C: postdoctoral training, including C1 and C2 (#398).

Three taxonomy codes into one table -- residency, fellowship and other
postdoctoral training are separate codes because they classify differently, but
the WCM template lists them together. The code is kept per entry so that
`format_date_range` can apply the right per-section date rule to each row.

Three columns: training type, institution + location, dates. Cells are MIXED
content, as in B1 and B2, so enrichment is visible on the page;
`_add_table_row_with_mixed_content` is shared and stays on
`WCMTemplateGenerator`.

The institution is resolved in three descending steps, because it is the field
most often missing:

1. the stage-5b cleaned name, which strips a location the source had embedded;
2. the raw `institution` field;
3. `_recover_institution_from_nearby_entries` -- a training block in a source CV
   usually names its institution once, above several lines, and extraction
   attaches it only to the first.

Enriched location is then appended as a tracked insertion, but only after
checking the CITY is not already inside the institution string. "Massachusetts
General Hospital, Boston, MA" would otherwise gain a second Boston. The check is
on the city alone rather than the whole location, since the state is nearly
never present in the institution name and would defeat the comparison.

A tab inside `training_type` is the extractor having flattened a table cell
boundary, so tabs become ", " rather than rendering as a run of whitespace.
`field_of_study` is appended only when it is not already contained in the type,
which is common once the tab join has run.
"""
from typing import Dict, List

from ..formatting import _clear_table_data, format_date_range
from ..normalization import _get_cleaned_institution_name
from ..resolution import (
    _get_institution_location,
    _recover_institution_from_nearby_entries,
)
from ..sorting import sort_entries_reverse_chronological

# Postdoctoral training codes (docs/CODING_STANDARDS.md §8.2). Missing 'C3' is a
# known, separately tracked gap (#573, #624) -- do not add it here; this constant
# names the set exactly as it renders today.
POSTDOC_CODES = ('C', 'C1', 'C2')


class PostdocTrainingSection:
    """Section C / C1 / C2 writers, mixed into `WCMTemplateGenerator`."""

    def _fill_postdoc_training(self, entries_by_code: Dict[str, List[Dict]], all_entries: List[Dict] = None):
        """Fill postdoctoral training table with track changes for enriched content.

        Track changes are used for city/state from institution enrichment only.
        """
        training_entries = []
        for code in POSTDOC_CODES:
            training_entries += entries_by_code.get(code, [])

        if not training_entries:
            return

        if self.verbose:
            print(f"Filling Postdoctoral Training ({len(training_entries)} entries)...")

        # Try to find the POSTDOCTORAL section
        training_idx = self._find_paragraph_with_text("POSTDOCTORAL")
        if training_idx is None:
            training_idx = self._find_paragraph_with_text("TRAINING")
        if training_idx is None:
            return

        table = self._find_table_after_paragraph(training_idx)
        if not table:
            return

        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        # Sort entries reverse chronologically (most recent first)
        sorted_entries = sort_entries_reverse_chronological(training_entries)

        for entry in sorted_entries:
            fields = entry.get('extracted_fields', {})

            # Training type/title (clean up tabs that may have been extracted)
            training_type = fields.get('training_type', '') or fields.get('title', 'Postdoctoral')
            # Replace tabs with comma-space for cleaner display
            if '\t' in training_type:
                training_type = ', '.join(part.strip() for part in training_type.split('\t') if part.strip())
            field_of_study = fields.get('field_of_study', '') or fields.get('specialty', '')
            if field_of_study and field_of_study not in training_type:
                training_type = f"{training_type}, {field_of_study}" if training_type else field_of_study

            # Institution with location from enrichment
            raw_institution = fields.get('institution', '')

            # Use LLM-cleaned institution name (strips embedded location); fall back to raw field
            institution = _get_cleaned_institution_name(entry) or raw_institution

            # If institution is missing, try to recover from nearby entries in original CV
            if not institution and all_entries:
                institution = _recover_institution_from_nearby_entries(entry, all_entries)

            # Location from Stage 5b enrichment
            location, location_is_enriched = _get_institution_location(entry)

            # Get taxonomy code for this entry (C, C1, or C2)
            taxonomy_code = entry.get('taxonomy_code', 'C')

            # Dates - format according to C/C1/C2 requirements (mm/yy - mm/yy)
            start = fields.get('start_date', '')
            end = fields.get('end_date', '')
            dates = format_date_range(start, end, taxonomy_code)

            # Build cell contents with mixed normal/track-change content
            training_content = [(training_type, False, "")]

            # Check if location is already present in institution to avoid duplication
            location_already_present = False
            if location and institution:
                # Check if city is already in the institution string
                location_parts = location.split(',')
                if location_parts:
                    city = location_parts[0].strip()
                    # Check for city name in institution (case-insensitive)
                    if city.lower() in institution.lower():
                        location_already_present = True

            if location and location_is_enriched and not location_already_present:
                # Institution is normal text, ", City, State" is track change
                if institution:
                    institution_content = [
                        (institution, False, ""),
                        (f", {location}", True, "Institution Enrichment")
                    ]
                else:
                    institution_content = [(location, True, "Institution Enrichment")]
            elif location and not location_already_present:
                institution_full = f"{institution}, {location}" if institution else location
                institution_content = [(institution_full, False, "")]
            else:
                institution_content = [(institution, False, "")]

            dates_content = [(dates, False, "")]

            self._add_table_row_with_mixed_content(
                table,
                [training_content, institution_content, dates_content],
                entry=entry
            )
