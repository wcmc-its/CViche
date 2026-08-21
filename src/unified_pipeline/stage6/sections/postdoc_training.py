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

The section runs as three steps, and only the last one touches the document:

    entries_by_code
        -> _resolve_postdoc_training    gather, stamp the routing code, sort
        -> _normalize_training_entry    one PostdocTrainingRecord per entry
        -> _fill_postdoc_training       writes the records into the Word table

The first two take no `docx` object and no `self.stats`, so the business
rules below -- taxonomy defaults, institution recovery, the location
de-duplication, date formatting -- are unit-testable without a document, and
a change to Word table handling cannot reach them (#624 review).
`_fill_postdoc_training` reads no `extracted_fields` at all: every stage-4
field alias is resolved in exactly one place, `_normalize_training_entry`.

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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..formatting import _clear_table_data, format_date_range
from ..normalization import _get_cleaned_institution_name
from ..resolution import (
    _get_institution_location,
    _recover_institution_from_nearby_entries,
)
from ..sorting import sort_entries_reverse_chronological

# The generic postdoctoral code, used both as a member of the table below and
# as the fallback when an entry carries no routing code of its own.
GENERIC_TRAINING_CODE = 'C'

# Every taxonomy code the Postdoctoral Training table renders: C generic,
# C1 postdoctoral research, C2 residency, C3 fellowship. C3 was omitted
# from the old inline gather (and from RENDER_ROUTED_CODES and
# DATE_FORMATS) even though stage 3b can emit it, so every C3 entry fell to
# the Appendix by construction -- the same class of gap as N2 (#529, #573).
POSTDOC_TRAINING_CODES = (GENERIC_TRAINING_CODE, 'C1', 'C2', 'C3')

# What an entry outside POSTDOC_TRAINING_CODES is called when it has no
# extracted title. Also the generic code's own label.
FALLBACK_TRAINING_TYPE = 'Postdoctoral'

# Missing training_type/title used to fall back to the single word
# "Postdoctoral" for every code, which mislabels a C2 residency or C3
# fellowship. Keyed by taxonomy code so the fallback names the actual
# training type (#573 review).
DEFAULT_TRAINING_TYPES = {
    GENERIC_TRAINING_CODE: FALLBACK_TRAINING_TYPE,
    'C1': 'Postdoctoral Research',
    'C2': 'Residency',
    'C3': 'Fellowship',
}

INSTITUTION_ENRICHMENT_REASON = "Institution Enrichment"


@dataclass(frozen=True)
class PostdocTrainingRecord:
    """One row of the Postdoctoral Training table, fully resolved.

    Built only by `_normalize_training_entry`. Nothing downstream reads
    `extracted_fields`, so there is one place -- and one place only -- where
    'training_type' vs 'title', or 'field_of_study' vs 'specialty', is
    decided.

    `location` is already '' when the institution string names that city, so
    the renderer needs no de-duplication rule of its own; `dates` is already
    through the code's own date rule. `source_entry` is the stamped stage-4
    dict, carried through only because `_add_table_row_with_mixed_content`
    reads it to attach upstream pipeline comments to the row.
    """

    taxonomy_code: str = GENERIC_TRAINING_CODE
    training_type: str = ''
    institution: str = ''
    location: str = ''
    location_is_enriched: bool = False
    dates: str = ''
    source_entry: Mapping[str, Any] | None = None


def _join_if_list(value: Any) -> str:
    """A fused multi-record entry (one training block extracted from
    several source records) extracts a field like training_type as a list,
    one item per record, instead of a string (corpus CV FIHL8A). Any other
    non-string value is coerced too, so downstream string methods never see
    a non-str."""
    if isinstance(value, (list, tuple)):
        return ', '.join(str(v).strip() for v in value if str(v).strip())
    return str(value or '')


def _location_already_in_institution(location: str, institution: str) -> bool:
    """True when the institution string already names the location's city.

    Compared on the CITY alone rather than the whole location: the state is
    nearly never present in an institution name and would defeat the match.
    Word-boundary anchored so a short city name cannot match inside an
    unrelated longer word.
    """
    if not (location and institution):
        return False
    location_parts = location.split(',')
    if not location_parts:
        return False
    city = location_parts[0].strip()
    if not city:
        return False
    return bool(re.search(r'\b' + re.escape(city) + r'\b',
                          institution, re.IGNORECASE))


def _normalize_training_entry(
    entry: Mapping[str, Any],
    taxonomy_code: str,
    all_entries: Sequence[Mapping[str, Any]] | None = None,
) -> PostdocTrainingRecord:
    """Resolve one raw stage-4 training dict into a typed record.

    The `or` chains are load-bearing and are deliberately not
    `.get(key, default)`: stage 4 writes an alias as an empty string about as
    often as it omits the key, and only an `or` chain falls through on both.
    Swapping in a `.get` default would keep the empty string and stop the
    next alias ever being consulted -- and, for training_type, would render a
    row with a blank type instead of the taxonomy default.

    `extracted_fields` is sometimes explicitly `None` rather than absent,
    which a bare `.get('extracted_fields', {})` does not cover.

    `taxonomy_code` is passed in rather than read off the entry because
    `entries_by_code`'s key is the authoritative routing code -- see
    `_resolve_postdoc_training`.
    """
    fields = entry.get('extracted_fields') or {}

    # Training type/title (clean up tabs that may have been extracted).
    # Default by taxonomy code -- C1/C2/C3 are postdoctoral research,
    # residency and fellowship respectively, so a blanket "Postdoctoral"
    # mislabels a residency or fellowship with no extracted title.
    # A fused multi-record entry extracts these as a list, one item per
    # record (corpus CV FIHL8A), not a string -- join before any string
    # method sees it.
    training_type = _join_if_list(
        fields.get('training_type') or fields.get('title')
        or DEFAULT_TRAINING_TYPES.get(taxonomy_code, FALLBACK_TRAINING_TYPE))
    # Replace tabs with comma-space for cleaner display
    if '\t' in training_type:
        training_type = ', '.join(part.strip()
                                  for part in training_type.split('\t')
                                  if part.strip())
    field_of_study = _join_if_list(fields.get('field_of_study')
                                   or fields.get('specialty'))
    if field_of_study and field_of_study.casefold() not in training_type.casefold():
        training_type = (f"{training_type}, {field_of_study}"
                         if training_type else field_of_study)

    # Institution: stage-5b cleaned name (strips embedded location), then the
    # raw field, then the neighbouring entries of the source CV.
    institution = _get_cleaned_institution_name(entry) or fields.get('institution', '')
    if not institution and all_entries:
        institution = _recover_institution_from_nearby_entries(entry, all_entries)

    # Location from Stage 5b enrichment. Dropped outright when the
    # institution already names the city, so the renderer has no
    # de-duplication rule of its own to keep in step with this one.
    location, location_is_enriched = _get_institution_location(entry)
    if _location_already_in_institution(location, institution):
        location = ''

    return PostdocTrainingRecord(
        taxonomy_code=taxonomy_code,
        training_type=training_type,
        institution=institution,
        location=location,
        location_is_enriched=location_is_enriched,
        # Dates - format according to C/C1/C2/C3 requirements (mm/yy - mm/yy)
        dates=format_date_range(fields.get('start_date', ''),
                                fields.get('end_date', ''), taxonomy_code),
        source_entry=entry,
    )


def _resolve_postdoc_training(
    entries_by_code: Mapping[str, Sequence[Mapping[str, Any]]],
    all_entries: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[PostdocTrainingRecord, ...]:
    """Decide the whole of section C without touching a document.

    Gathers the four codes, orders the entries reverse chronologically and
    normalizes each one. Takes no `docx` object and no stats counter, so a
    change to Word table handling cannot reach these rules and vice versa
    (#624 review).
    """
    return tuple(
        _normalize_training_entry(
            entry, entry.get('taxonomy_code', GENERIC_TRAINING_CODE), all_entries)
        for entry in sort_entries_reverse_chronological(
            _gather_postdoc_entries(entries_by_code))
    )


def _gather_postdoc_entries(
    entries_by_code: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """The section's entries, each stamped with the code it was routed under.

    entries_by_code's key is the authoritative routing code -- stamp it onto a
    shallow copy of each entry rather than trusting (or mutating) whatever the
    entry's own 'taxonomy_code' field says, so a C3 entry can't silently fall
    back to the generic C date rule if that field is missing or disagrees with
    how it was routed (#573 review).

    Reads no field but 'taxonomy_code', so it cannot raise on a malformed
    stage-4 entry. `_fill_postdoc_training` relies on that: it needs the
    section's entry count before it knows whether the template even has a
    Postdoctoral table, and normalizing that early would turn a template
    without the section from a quiet return into a stage-6 abort (#624
    review).
    """
    return [
        {**entry, 'taxonomy_code': code}
        for code in POSTDOC_TRAINING_CODES
        for entry in entries_by_code.get(code, [])
    ]


def _institution_content(
    record: PostdocTrainingRecord,
) -> list[tuple[str, bool, str]]:
    """The runs of the institution cell: (text, is_tracked_insertion, author).

    An enriched location is a tracked insertion so the reviewer can see what
    the pipeline added; an extracted one is plain text joined onto the
    institution. A location `_normalize_training_entry` already dropped as a
    duplicate leaves the institution alone.
    """
    if not record.location:
        return [(record.institution, False, "")]
    if record.location_is_enriched:
        # Institution is normal text, ", City, State" is track change
        if record.institution:
            return [(record.institution, False, ""),
                    (f", {record.location}", True, INSTITUTION_ENRICHMENT_REASON)]
        return [(record.location, True, INSTITUTION_ENRICHMENT_REASON)]
    institution_full = (f"{record.institution}, {record.location}"
                        if record.institution else record.location)
    return [(institution_full, False, "")]


class PostdocTrainingSection:
    """Section C / C1 / C2 / C3 writers, mixed into `WCMTemplateGenerator`."""

    def _fill_postdoc_training(
        self,
        entries_by_code: Mapping[str, Sequence[Mapping[str, Any]]],
        all_entries: Sequence[Mapping[str, Any]] | None = None,
    ) -> None:
        """Fill postdoctoral training table with track changes for enriched content.

        Track changes are used for city/state from institution enrichment only.

        Rendering only: `_normalize_training_entry` resolves the stage-4 field
        aliases, recovers the institution and formats the dates, so nothing
        below reads `extracted_fields`. It is called per row rather than up
        front on purpose -- see `_gather_postdoc_entries`.
        """
        training_entries = _gather_postdoc_entries(entries_by_code)

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

        for entry in sort_entries_reverse_chronological(training_entries):
            record = _normalize_training_entry(
                entry, entry.get('taxonomy_code', GENERIC_TRAINING_CODE), all_entries)
            self._add_table_row_with_mixed_content(
                table,
                [[(record.training_type, False, "")],
                 _institution_content(record),
                 [(record.dates, False, "")]],
                entry=record.source_entry,
            )
