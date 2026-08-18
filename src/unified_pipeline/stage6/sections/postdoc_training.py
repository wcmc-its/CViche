"""Section C: postdoctoral training, including C1, C2 and C3 (#398).

Four taxonomy codes into one table -- postdoctoral research (C1), residency
(C2) and fellowship (C3) are separate codes because they classify
differently, but the WCM template lists them together. The code is kept per
entry so that `format_date_range` can apply the right per-section date rule
to each row.

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
import re
from typing import Dict, List

from ..formatting import _clear_table_data, format_date_range
from ..normalization import _get_cleaned_institution_name
from ..resolution import (
    _get_institution_location,
    _recover_institution_from_nearby_entries,
)
from ..sorting import sort_entries_reverse_chronological

# Every taxonomy code the Postdoctoral Training table renders: C generic,
# C1 postdoctoral research, C2 residency, C3 fellowship. C3 was omitted
# from the old inline gather (and from RENDER_ROUTED_CODES and
# DATE_FORMATS) even though stage 3b can emit it, so every C3 entry fell to
# the Appendix by construction -- the same class of gap as N2 (#529, #573).
POSTDOC_TRAINING_CODES = ('C', 'C1', 'C2', 'C3')


def _join_if_list(value) -> str:
    """A fused multi-record entry (one training block extracted from
    several source records) extracts a field like training_type as a list,
    one item per record, instead of a string (corpus CV FIHL8A). Any other
    non-string value is coerced too, so downstream string methods never see
    a non-str."""
    if isinstance(value, (list, tuple)):
        return ', '.join(str(v).strip() for v in value if str(v).strip())
    return str(value or '')

# Missing training_type/title used to fall back to the single word
# "Postdoctoral" for every code, which mislabels a C2 residency or C3
# fellowship. Keyed by taxonomy code so the fallback names the actual
# training type (#573 review).
DEFAULT_TRAINING_TYPES = {
    'C': 'Postdoctoral',
    'C1': 'Postdoctoral Research',
    'C2': 'Residency',
    'C3': 'Fellowship',
}

INSTITUTION_ENRICHMENT_REASON = "Institution Enrichment"


class PostdocTrainingSection:
    """Section C / C1 / C2 / C3 writers, mixed into `WCMTemplateGenerator`."""

    def _fill_postdoc_training(self, entries_by_code: Dict[str, List[Dict]], all_entries: List[Dict] = None):
        """Fill postdoctoral training table with track changes for enriched content.

        Track changes are used for city/state from institution enrichment only.
        """
        # entries_by_code's key is the authoritative routing code -- stamp it
        # onto a shallow copy of each entry rather than trusting (or
        # mutating) whatever the entry's own 'taxonomy_code' field says, so
        # a C3 entry can't silently fall back to the generic C date rule if
        # that field is missing or disagrees with how it was routed (#573
        # review).
        training_entries = [
            {**entry, 'taxonomy_code': code}
            for code in POSTDOC_TRAINING_CODES
            for entry in entries_by_code.get(code, [])
        ]

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
            fields = entry.get('extracted_fields') or {}
            # entries_by_code stamped this above; it's authoritative.
            taxonomy_code = entry.get('taxonomy_code', 'C')

            # Training type/title (clean up tabs that may have been extracted).
            # Default by taxonomy code -- C1/C2/C3 are postdoctoral research,
            # residency and fellowship respectively, so a blanket "Postdoctoral"
            # mislabels a residency or fellowship with no extracted title.
            training_type = (fields.get('training_type', '') or fields.get('title', '')
                             or DEFAULT_TRAINING_TYPES.get(taxonomy_code, 'Postdoctoral'))
            # A fused multi-record entry extracts these as a list, one item
            # per record (corpus CV FIHL8A), not a string -- join before any
            # string method sees it.
            training_type = _join_if_list(training_type)
            # Replace tabs with comma-space for cleaner display
            if '\t' in training_type:
                training_type = ', '.join(part.strip() for part in training_type.split('\t') if part.strip())
            field_of_study = _join_if_list(fields.get('field_of_study', '') or fields.get('specialty', ''))
            if field_of_study and field_of_study.casefold() not in training_type.casefold():
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
                    # Word-boundary match so a short city name can't match
                    # inside an unrelated longer word in the institution name.
                    if city and re.search(r'\b' + re.escape(city) + r'\b',
                                          institution, re.IGNORECASE):
                        location_already_present = True

            if location and location_is_enriched and not location_already_present:
                # Institution is normal text, ", City, State" is track change
                if institution:
                    institution_content = [
                        (institution, False, ""),
                        (f", {location}", True, INSTITUTION_ENRICHMENT_REASON)
                    ]
                else:
                    institution_content = [(location, True, INSTITUTION_ENRICHMENT_REASON)]
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
