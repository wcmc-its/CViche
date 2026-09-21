"""Section B1: education -- conferred degrees (#398).

Four columns -- degree, institution + location, dates attended, year awarded --
and the only section whose cells are written as MIXED content: parts of a cell
are normal text and parts are tracked insertions, so a reader can see exactly
which words the pipeline added. `_add_table_row_with_mixed_content` (shared, so
it stays on `WCMTemplateGenerator`) takes the per-cell run lists this writer
builds.

Two fields can be enriched, and each is attributed separately:

- the location, from institution enrichment. It is appended as ", City, ST"
  inside the institution cell and marked as an insertion by "Institution
  Enrichment" -- but only after checking the city name is not already inside the
  institution string, since "University of Pittsburgh, Pittsburgh, PA" would
  otherwise gain a second copy.
- the award year, when `_extract_year_from_text` recovers it from raw text that
  field extraction did not parse. Attributed to "Text Extraction".

`dates_attended` arrives in three different shapes from field extraction (flat
`dates_attended_start_date`, a nested dict, or plain `start_date`), and all
three are tried before the range is formatted.

`_degree_is_in_progress` and its `_IN_PROGRESS_DEGREE_PATTERN` vocabulary are
here because a year in the "Year Awarded" column asserts the degree was
conferred. A CV that says "PhD, expected May 2030" has a year, and printing it
bare turns an anticipated degree into a granted one. Two general signals mark
it: an explicit marker word, or an award year later than the run year. Neither
is tied to a particular CV. The column then reads "Expected 2030".

The marker match is word-bounded, not a bare substring (#549): 'present' as a
bare substring matched inside "presented", "presentation", "presently", and
'candidate' or 'pending' as an ordinary noun matched inside unrelated prose,
so a conferred degree could render "Expected <year>". `candidate` and
`present` are dropped from the vocabulary entirely rather than just bounded --
neither earned its place once bounded: `\b`-bounded `candidate` still matches
whole-word uses like "Candidate for Honors" (an award name, not a degree
status), and `present` as a genuine *degree* marker normally shows up in a
date range ("2019-present"), a case the fallback in `_fill_education` already
folds into `year_awarded` and that the future-year branch below covers when a
real year is present. `pending` is kept because it does not have the same
noun-collision problem in practice.

`_degree_is_in_progress` is pinned on the class surface by
`tests/test_stage6_import_surface.py` -- the suite calls it on an instance. The
mixin keeps it resolving through the MRO, which is what that guard checks.

Stage 4 emits an explicit ``None`` for a missing degree/institution/major
rather than omitting the key (#659). `degree: None` with a `major` present
used to raise `TypeError` at `major not in degree`, and the farm has ten such
entries; five more carry `institution: None`. `_field_text` coerces every raw
field to a string at the point it is read so no non-str value -- `None` or
otherwise -- reaches a `.lower()` or an `in` test. A truthy non-dict
`extracted_fields` (a stray list) is guarded the same way `extracted_fields`
already is throughout stage6 (`isinstance(..., Mapping)`), and a whole entry
that is not a mapping at all is skipped with a warning rather than crashing
on `entry.get(...)`.
"""
import logging
import re
from collections.abc import Mapping
from datetime import datetime

from ..formatting import (
    _clear_table_data,
    format_date_for_section,
    format_date_range,
)
from ..normalization import _get_cleaned_institution_name
from ..parsing import _extract_year_from_text
from ..resolution import _get_institution_location, _location_already_in_institution
from ..sorting import sort_entries_reverse_chronological

logger = logging.getLogger(__name__)


def _field_text(value: object) -> str:
    """Coerce one raw stage-4 education field to plain text.

    Stage 4 sets `degree`/`major`/`institution` to an explicit ``None``
    rather than omitting the key when a field wasn't extracted, so
    `dict.get(key, '')`'s own default never fires for those entries. This
    covers that case, plus the general one -- a field that came back as
    something other than a string. Never raises -- stringifying an
    unexpected shape keeps the entry rendering instead of aborting the whole
    Stage 6 render (#659)."""
    if value is None:
        return ''
    if isinstance(value, str):
        return value
    return str(value)


class EducationSection:
    """Section B1 writers, mixed into `WCMTemplateGenerator`."""

    # Word-bounded phrases a CV uses to mark a degree that has not yet been
    # conferred. Matched with \b on both sides so "presented" or "Candidate
    # for Honors" cannot fire this the way a bare substring test would (#549).
    _IN_PROGRESS_DEGREE_PATTERN = re.compile(
        r'\b(expected|anticipated|in[- ]progress|ongoing|'
        r'to be conferred|to be awarded|pending)\b',
        re.IGNORECASE,
    )

    def _degree_is_in_progress(self, raw_text: str, year_awarded: str) -> bool:
        """Return True when a degree has not yet been conferred.

        Two general signals, neither tied to any specific CV:
        1. The source line carries an explicit, word-bounded "not yet
           awarded" marker ("expected", "anticipated", "in progress", ...).
        2. The award year parses to a year later than the current (run) year, so
           it cannot already have been conferred.
        """
        text = raw_text or ''
        if self._IN_PROGRESS_DEGREE_PATTERN.search(text):
            return True

        # Future award year => not yet conferred. year_awarded is already
        # normalized to a 4-digit year by format_date_for_section(..., 'H').
        match = re.search(r'(19|20)\d{2}', str(year_awarded))
        if match:
            try:
                if int(match.group(0)) > datetime.now().year:
                    return True
            except ValueError:
                pass
        return False

    def _fill_education(self, entries: list[dict]) -> None:
        """Fill education table with track changes for enriched content.

        Track changes are used for:
        - City/state from institution enrichment (only the location part, not institution name)
        - Years extracted from raw text when not in extracted_fields
        """
        if self.verbose:
            print(f"Filling Education ({len(entries)} entries)...")

        edu_idx = self._find_paragraph_with_text("EDUCATION")
        if edu_idx is None:
            return

        table = self._find_table_after_paragraph(edu_idx)
        if not table:
            return

        _clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        # A malformed entry (not even a mapping) can't reach entry.get(...)
        # below or in sort_entries_reverse_chronological's own sort key, so
        # it's filtered out here rather than inside the loop (#659).
        mapping_entries = []
        for idx, entry in enumerate(entries):
            if isinstance(entry, Mapping):
                mapping_entries.append(entry)
            else:
                logger.warning(
                    "Skipping education entry %d: expected a mapping, got %s",
                    idx, type(entry).__name__,
                )

        # Sort entries reverse chronologically (most recent first)
        sorted_entries = sort_entries_reverse_chronological(mapping_entries)

        for entry in sorted_entries:
            # A truthy non-dict extracted_fields (a stray list) must not
            # reach .get() below.
            raw_fields = entry.get('extracted_fields')
            fields = raw_fields if isinstance(raw_fields, Mapping) else {}
            raw_text = _field_text(entry.get('text', ''))

            # Degree column (not enriched)
            degree = _field_text(fields.get('degree', ''))
            major = _field_text(fields.get('major')) or _field_text(fields.get('field_of_study', ''))
            if major and major not in degree:
                degree = f"{degree}, {major}" if degree else major

            # Institution - use cleaned_name from enrichment if available
            institution = _field_text(fields.get('institution', ''))
            if institution and institution.lower() == 'none':
                institution = ''
            cleaned = _get_cleaned_institution_name(entry)
            if cleaned:
                institution = cleaned

            # Skip entries that have no degree AND no institution
            # These are likely training items that don't fit the education table format
            if not degree and not institution:
                continue

            # Location from enrichment
            location, location_is_enriched = _get_institution_location(entry)
            location = _field_text(location)

            # Dates - format according to B1 requirements (mm/yyyy-mm/yyyy)
            # Field extraction may use three different structures:
            #   1. Flat: 'dates_attended_start_date' / 'dates_attended_end_date'
            #   2. Nested dict: 'dates_attended': {'start_date': '...', 'end_date': '...'}
            #   3. Generic: 'start_date' / 'end_date'
            start = fields.get('dates_attended_start_date', '') or fields.get('start_date', '')
            end = fields.get('dates_attended_end_date', '') or fields.get('end_date', '')

            # Check for nested dict structure
            if not start and not end:
                dates_attended = fields.get('dates_attended', {})
                if isinstance(dates_attended, dict):
                    start = dates_attended.get('start_date', '') or ''
                    end = dates_attended.get('end_date', '') or ''
            if start or end:
                dates = format_date_range(start, end, 'B1')
            else:
                dates = ''

            # Year awarded - try to extract from raw text if missing
            year_awarded = fields.get('year_awarded') or fields.get('year') or end or ''
            year_is_enriched = False

            # If year_awarded is still empty, try to extract from raw text
            if not year_awarded and raw_text:
                extracted_year = _extract_year_from_text(raw_text)
                if extracted_year:
                    year_awarded = extracted_year
                    year_is_enriched = True  # Mark as enriched since we extracted it

            # Format year_awarded - B1 uses mm/yyyy but year awarded column is just yyyy
            if year_awarded:
                year_awarded = format_date_for_section(year_awarded, 'H')  # H uses yyyy format

            # A degree that is still in progress ("expected May 2026", a future
            # award year, etc.) must NOT be presented as a conferred year. Mark it
            # as anticipated so the reader can tell it has not been awarded yet.
            if year_awarded and self._degree_is_in_progress(raw_text, year_awarded):
                year_awarded = f"Expected {year_awarded}"

            # Build cell contents with mixed normal/track-change content
            # Cell 0: Degree (never enriched)
            degree_content = [(degree, False, "")]

            # "University of Pittsburgh, Pittsburgh, PA" must not gain a second
            # ", Pittsburgh, PA"; "New York University" must still gain its
            # ", New York, NY" (#897) -- one shared tail predicate decides.
            location_already_present = _location_already_in_institution(location, institution)

            # Cell 1: Institution + Location (location may be enriched)
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
                # Location exists but not from enrichment - all normal text
                institution_full = f"{institution}, {location}" if institution else location
                institution_content = [(institution_full, False, "")]
            else:
                # No location or location already present
                institution_content = [(institution, False, "")]

            # Cell 2: Dates (never enriched for now)
            dates_content = [(dates, False, "")]

            # Cell 3: Year awarded (may be enriched if extracted from text)
            year_content = [(year_awarded, year_is_enriched, "Text Extraction")]

            # Add the row with mixed content
            self._add_table_row_with_mixed_content(
                table,
                [degree_content, institution_content, dates_content, year_content],
                entry=entry
            )
