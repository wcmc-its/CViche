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

Enriched location is then appended as a tracked insertion, unless the
institution string already ends in it -- "Massachusetts General Hospital,
Boston, MA" would otherwise gain a second Boston. That predicate lives in
`resolution._location_already_in_institution`, shared with education and
positions (#897: the earlier city-word check dropped the location from every
"New York University" / "New York Presbyterian" entry).

A tab inside `training_type` is the extractor having flattened a table cell
boundary, so tabs become ", " rather than rendering as a run of whitespace.
`field_of_study` is appended only when it is not already contained in the type,
which is common once the tab join has run.
A stage-4 `role` ("Chief Resident") trails the type cell the same way: the template has
no role column, and the field was dropped from the row until #946.
The supervisor the entry's own line labels ("Supervisor: Dr. A. Example") trails
the type cell last, as "; Mentor: <name as the CV writes it>" whatever the label
(owner ruling 2026-11-06, #1245): `supplementary.training_mentor` reads it, and
`_with_mentor` leaves out the owner's own name and a name a cell already prints.
The owner is per-run state, so it is passed in from `generate()`, never kept here.
"""
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..formatting import format_date_range
from ..normalization import _get_cleaned_institution_name
from ..parsing.text import supervisor_name
from ..resolution import (
    _get_institution_location,
    _location_already_in_institution,
    _recover_institution_from_nearby_entries,
)
from ..sorting import sort_entries_reverse_chronological
from ..supplementary import (
    POSTDOC_DATE_FIELDS,
    POSTDOC_FIELD_OF_STUDY_FIELDS,
    POSTDOC_TYPE_FIELDS,
    first_field_value,
    training_mentor,
)

logger = logging.getLogger(__name__)

# The generic postdoctoral code, used both as a member of the table below and
# as the fallback when an entry carries no routing code of its own.
GENERIC_TRAINING_CODE = 'C'

# The template heading the section's table follows. The writer looks it up,
# and so does the #1205 sub-point pass for the section's span
# (`_SUBPOINT_SECTION_HEADERS`), so the two cannot name different headings.
POSTDOC_TRAINING_HEADING = 'POSTDOCTORAL'

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

# What the type cell calls a position's supervisor, whichever label the CV
# gives it (Mentor, Supervisor, Advisor, Preceptor, Fellowship Director: owner
# ruling 2026-11-06, #1245), and what separates it from the type, field and
# role before it.
MENTOR_LABEL = 'Mentor'
MENTOR_SEPARATOR = '; '
# A word of a name as `_with_mentor` compares names: two letters or more, so
# an initial or a period never decides it.
_NAME_WORD_RE = re.compile(r'[a-z]{2,}')


@dataclass(frozen=True)
class PostdocTrainingRecord:
    """One row of the Postdoctoral Training table, fully resolved.

    Built only by `_normalize_training_entry`. Nothing downstream reads
    `extracted_fields`, so there is one place -- and one place only -- where
    'training_type' vs 'title', or 'field_of_study' vs 'specialty', is
    decided.

    `location` is already '' when the institution string names that city, so
    the renderer needs no de-duplication rule of its own; `dates` is already
    through the code's own date rule. `source_entry` is the stage-4 dict as
    `_gather_postdoc_entries` hands it on, carried through because
    `_add_table_row_with_mixed_content` reads it to attach upstream pipeline
    comments to the row and records the row against it.
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


def _name_words(value: object) -> set[str]:
    """The lowercased words of `value` (`_NAME_WORD_RE`); none for a value
    that is not a string."""
    return set(_NAME_WORD_RE.findall(value.lower())) if isinstance(value, str) else set()


def _names_the_owner(name: str, cv_owner: Mapping[str, object] | None) -> bool:
    """Whether `name` is the CV owner's: it holds every word of stage 4's
    `last_name` and, when a `first_name` is known, a word of that. A line
    labelling the owner names no supervisor. Preventive: no farm line does."""
    owner = cv_owner or {}
    last, first = _name_words(owner.get('last_name')), _name_words(owner.get('first_name'))
    words = _name_words(name)
    return bool(last) and last <= words and (not first or bool(first & words))


def _with_mentor(training_type: str, entry: Mapping[str, Any], cells: Sequence[object],
                 cv_owner: Mapping[str, object] | None) -> str:
    """The type cell with the supervisor the entry's line labels
    (`training_mentor`) after it, as `MENTOR_LABEL`: "Fellowship, Zymology;
    Mentor: Dr. A. Example". Not when the name is the owner's
    (`_names_the_owner`), nor when the row's `cells` already hold every word
    of it (a stage-4 role that is the label line)."""
    mentor = training_mentor(entry)
    if not mentor:
        return training_type
    name = supervisor_name(mentor)
    printed = set().union(*(_name_words(cell) for cell in cells))
    if _names_the_owner(name, cv_owner) or _name_words(name) <= printed:
        return training_type
    labelled = f"{MENTOR_LABEL}: {mentor}"
    return f"{training_type}{MENTOR_SEPARATOR}{labelled}" if training_type else labelled


def _normalize_training_entry(
    entry: Mapping[str, Any],
    taxonomy_code: str,
    all_entries: Sequence[Mapping[str, Any]] | None = None,
    cv_owner: Mapping[str, object] | None = None,
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
    `_resolve_postdoc_training`. `cv_owner` is the run's, for `_with_mentor`.
    """
    fields = entry.get('extracted_fields') or {}

    # Training type/title (clean up tabs that may have been extracted).
    # Default by taxonomy code -- C1/C2/C3 are postdoctoral research,
    # residency and fellowship respectively, so a blanket "Postdoctoral"
    # mislabels a residency or fellowship with no extracted title.
    # A fused multi-record entry extracts these as a list, one item per
    # record (corpus CV FIHL8A), not a string -- join before any string
    # method sees it.
    # Each cell's aliases, in order, and the alias it prints are
    # `supplementary`'s, so the #1205 sub-point pass counts what this row
    # prints and no other alias (`_value_tokens`).
    training_type = _join_if_list(
        first_field_value(fields, POSTDOC_TYPE_FIELDS)
        or DEFAULT_TRAINING_TYPES.get(taxonomy_code, FALLBACK_TRAINING_TYPE))
    # Replace tabs with comma-space for cleaner display
    if '\t' in training_type:
        training_type = ', '.join(part.strip()
                                  for part in training_type.split('\t')
                                  if part.strip())
    field_of_study = _join_if_list(first_field_value(fields, POSTDOC_FIELD_OF_STUDY_FIELDS))
    if field_of_study and field_of_study.casefold() not in training_type.casefold():
        training_type = (f"{training_type}, {field_of_study}"
                         if training_type else field_of_study)
    # A role held within the training ("Chief Resident") has no template
    # column, so it trails the type cell. Skipped when the cell already says
    # it, and when the extractor wrote the string "None" for no role (#946).
    role = _join_if_list(fields.get('role')).strip()
    if (role and role.casefold() != 'none'
            and role.casefold() not in training_type.casefold()):
        training_type = f"{training_type}, {role}" if training_type else role

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
    # The labelled supervisor goes last, once every cell it is checked against is known.
    training_type = _with_mentor(training_type, entry, (training_type, institution, location), cv_owner)
    start_date, end_date = (fields.get(name, '') for name in POSTDOC_DATE_FIELDS)

    return PostdocTrainingRecord(
        taxonomy_code=taxonomy_code,
        training_type=training_type,
        institution=institution,
        location=location,
        location_is_enriched=location_is_enriched,
        # Dates - format according to C/C1/C2/C3 requirements (mm/yy - mm/yy)
        dates=format_date_range(start_date, end_date, taxonomy_code,
                                entry.get('text', '')),
        source_entry=entry,
    )


def _resolve_postdoc_training(
    entries_by_code: Mapping[str, Sequence[Mapping[str, Any]]],
    all_entries: Sequence[Mapping[str, Any]] | None = None,
    cv_owner: Mapping[str, object] | None = None,
) -> tuple[PostdocTrainingRecord, ...]:
    """Decide the whole of section C without touching a document.

    Gathers the four codes, orders the entries reverse chronologically and
    normalizes each one. Takes no `docx` object and no stats counter, so a
    change to Word table handling cannot reach these rules and vice versa
    (#624 review).
    """
    return tuple(
        _normalize_training_entry(
            entry, entry.get('taxonomy_code', GENERIC_TRAINING_CODE), all_entries, cv_owner)
        for entry in sort_entries_reverse_chronological(
            _gather_postdoc_entries(entries_by_code))
    )


def _gather_postdoc_entries(
    entries_by_code: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    """The section's entries, each carrying the code it was routed under.

    entries_by_code's key is the authoritative routing code -- an entry whose
    own 'taxonomy_code' field is missing or disagrees with it is stamped with
    it on a shallow copy, rather than trusted or mutated, so a C3 entry can't
    silently fall back to the generic C date rule (#573 review).

    An entry whose field already says it is handed on itself, not a copy. Its
    row is then recorded against the entry stage 6 grouped (the row-receipt
    ledger's `wrote`, by identity), which the #1205 sub-point pass reads to
    put a training position's prose under its own row only, and which the
    content-overflow pass reads to skip an entry whose prose is a sub-point.
    A stamped copy's row anchors no sub-point. Reroutes stamp the entry
    itself (`_correct_mismatch_if_needed`), so a copy is the exception: none
    of the 263 C entries the 102 farm renders draw is one.

    Reads no field but 'taxonomy_code', so it cannot raise on a malformed
    stage-4 entry. `_fill_postdoc_training` relies on that: it needs the
    section's entry count before it knows whether the template even has a
    Postdoctoral table, and normalizing that early would turn a template
    without the section from a quiet return into a stage-6 abort (#624
    review).
    """
    return [
        entry if entry.get('taxonomy_code') == code else {**entry, 'taxonomy_code': code}
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
        cv_owner: Mapping[str, object] | None = None,
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

        logger.info("Filling Postdoctoral Training (%d entries)...", len(training_entries))

        # Try to find the POSTDOCTORAL section
        training_idx = self._find_header_paragraph(POSTDOC_TRAINING_HEADING)
        if training_idx is None:
            training_idx = self._find_header_paragraph("TRAINING")
        if training_idx is None:
            logger.warning(
                "Postdoctoral Training: section heading not found in "
                "template; %d entries not rendered", len(training_entries))
            return

        table = self._find_table_after_paragraph(training_idx)
        if not table:
            logger.warning(
                "Postdoctoral Training: table not found after section "
                "heading; %d entries not rendered", len(training_entries))
            return

        self._clear_table_data(table, keep_header=True)
        self.stats['tables_populated'] += 1

        for entry in sort_entries_reverse_chronological(training_entries):
            record = _normalize_training_entry(
                entry, entry.get('taxonomy_code', GENERIC_TRAINING_CODE), all_entries, cv_owner)
            # Its comment hook records the row against `source_entry` (#534, #1205).
            self._add_table_row_with_mixed_content(
                table,
                [[(record.training_type, False, "")],
                 _institution_content(record),
                 [(record.dates, False, "")]],
                entry=record.source_entry,
            )
