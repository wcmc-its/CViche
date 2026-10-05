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
three are tried before the range is formatted. When none yields a range, a
STRING `dates_attended` is written as stage 4 gave it (#1187).

The degree cell is "Degree, field of study" as the template asks: `major` /
`field_of_study`, then stage 4's `discipline`, each only when the text so far
does not already hold it (#1187). Stage 4's off-schema `honors` follows in
parentheses, under the same rule (#817).

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


_NON_ALNUM_RUN = re.compile(r'[^a-z0-9]+')


def _words(text: str) -> str:
    """`text` lowercased with every run of punctuation/space as one space, so
    "Ph.D., Neuro-science" and "ph d neuro science" compare equal."""
    return _NON_ALNUM_RUN.sub(' ', text.lower()).strip()


def _already_in(part: str, whole: str) -> bool:
    """Whether `part` appears in `whole` as whole words, case- and
    punctuation-insensitively."""
    needle = _words(part)
    return bool(needle) and f' {needle} ' in f' {_words(whole)} '


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


def _with_discipline(degree: str, fields: Mapping) -> str:
    """The degree cell with stage 4's `discipline` appended ("PhD, Neuroscience"),
    which the WCM template's "Degree, include field of study" column asks for
    (#1187). Never repeated when the degree text already holds it."""
    discipline = _field_text(fields.get('discipline')).strip()
    if not discipline or _already_in(discipline, degree):
        return degree
    return f"{degree}, {discipline}" if degree else discipline


# Stage 4 keeps a degree's Latin honors and honor societies under `honors`, a
# key the B1 schema does not define, and no column read it: the honors reached
# neither Education nor Honors (EBYSBC E14, VVRTUC-02).
HONORS_KEY = 'honors'


def _honors_text(fields: Mapping) -> str:
    """Stage 4's off-schema B1 `honors`, as one string: a string as given, a
    list of strings comma-joined. Anything else yields ''."""
    value = fields.get(HONORS_KEY)
    if isinstance(value, list):
        value = ', '.join(item.strip() for item in value if isinstance(item, str) and item.strip())
    return value.strip() if isinstance(value, str) else ''


def _with_honors(degree: str, fields: Mapping) -> str:
    """The degree cell with the degree's honors after it in parentheses
    ("AB (summa cum laude)"), so they read as a distinction of that degree and
    not as a field of study. Never repeated when the cell already holds them."""
    honors = _honors_text(fields)
    if not honors or _already_in(honors, degree):
        return degree
    return f"{degree} ({honors})" if degree else honors


def _string_dates_attended(fields: Mapping, year_awarded: str) -> str:
    """A string `dates_attended`, formatted like every other B1 Dates cell
    (mm/yyyy), for the Dates cell when no start/end range could be built
    (#1187). Empty when it only repeats the Year Awarded cell, so the cell is
    filled only if it adds information. Not run through format_date_range: a
    bare "2010" must not become an invented "2010-Present"."""
    value = fields.get('dates_attended')
    if not isinstance(value, str) or not value.strip():
        return ''
    text = format_date_for_section(value.strip(), 'B1')
    return '' if text.strip() == str(year_awarded or '').strip() else text


# A degree cell that opens with a training title names a training post, not
# a conferred degree: an internship or residency coded B1 rendered "Year
# Awarded" from its attendance end (RCBKFG GKAQHB 17/20, #1415). An
# internship or residency may carry one modifier word first ("Surgical
# Internship", AKPQEB 12). Bare "Fellow" is left out -- a society fellowship
# ("Fellow of the Royal College ...") is a conferred credential.
_TRAINING_TITLE_RE = re.compile(
    r'^\W*(?:(?:chief\s+|senior\s+)?'
    r'(?:intern(?:ship)?|resident|residency|post-?doc(?:toral)?'
    r'|(?:clinical|research)\s+fellow(?:ship)?|fellowship|fellow\s+in)'
    r'|[a-z-]+\s+(?:internship|residency))\b',
    re.IGNORECASE)


def _names_a_degree(degree: str) -> bool:
    """Whether stage 4's `degree` text names something that is awarded: any
    text that does not open with a training title."""
    text = degree.strip()
    return bool(text) and not _TRAINING_TITLE_RE.match(text)


def _year_awarded(fields: Mapping, end: object, raw_text: str, *, degree_named: bool) -> tuple[object, bool]:
    """The Year Awarded value before formatting, and whether it was recovered
    from raw text (shown as a "Text Extraction" insertion).

    Stage 4's `year_awarded`/`year` always win. The two fallbacks -- the end
    of the attendance range, then a year found in the raw text -- infer an
    award, so they apply only when the row names a degree. A row with no
    degree (schooling, a year of study, a major alone) has nothing that was
    awarded, and filling the column from its attendance end asserted a degree
    year the CV never states (EBYSBC E33: HZGJFM-06, ZDCXIV-08). Nor does a
    training row whose degree cell is its title ("Intern", "Resident"):
    `_names_a_degree`.
    """
    stated = fields.get('year_awarded') or fields.get('year')
    if stated or not degree_named:
        return stated or '', False
    if end:
        return end, False
    extracted_year = _extract_year_from_text(raw_text) if raw_text else None
    if extracted_year:
        return extracted_year, True
    return '', False


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
            logger.info("Filling Education (%s entries)...", len(entries))

        edu_idx = self._find_header_paragraph("EDUCATION")
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

            # After the skip test above: a discipline alone must not create a
            # row that was skipped before.
            degree = _with_honors(_with_discipline(degree, fields), fields)

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
            year_awarded, year_is_enriched = _year_awarded(
                fields, end, raw_text, degree_named=_names_a_degree(_field_text(fields.get('degree'))),
            )

            # Format year_awarded - B1 uses mm/yyyy but year awarded column is just yyyy
            if year_awarded:
                year_awarded = format_date_for_section(year_awarded, 'H')  # H uses yyyy format

            if not dates:
                dates = _string_dates_attended(fields, year_awarded)

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
