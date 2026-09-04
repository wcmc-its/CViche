"""Section D: academic, hospital and other professional appointments (#398).

Three template tables (D1 academic, D2 hospital, D3 other) filled by one writer,
because the row shape is identical across them and only the source code list
differs.

Most of this module is not rendering. It is repair work on stage-4 output, and
it is why the section is this long for an 85-line writer:

- `_propagate_institution_to_subentries` -- source CVs indent sub-positions
  under an employer heading, and field extraction sees each bullet on its own,
  so the institution has to be carried forward in document order. It stops at
  a source-structure boundary (`_crosses_source_boundary`) and records the
  parent it took each institution from.
- `_merge_grouped_appointments` -- the same appointment arrives split into a
  title-less "employer + dates" row and one or more date-less "title only"
  rows. Rendered straight that produces blank-TITLE rows, which read as
  active/"Present" once sorted, and blank-DATES rows. It merges only where
  `_is_one_appointment` finds evidence the two rows are one appointment;
  adjacency alone is not evidence.
- `_normalized_positions` -- the single place that decides how many rows a
  table gets: sort, drop a source column-header row, and recover a tab-joined
  child appointment (`_child_position_records`) as a record of its own. The
  render loop is then one row per record, with no early return under it.

`_position_title` and `_position_has_dates` are the predicates that merge pass
reads each record through, and `_PLACEHOLDER_TITLES` is the column-header
vocabulary `_position_title` filters against -- field extraction emits "Title"
or "Role" as a title when the source CV had a table header there. All three are
reached only from this module.
"""
import re

from unified_pipeline.core.render_check import entry_fragments

from ..formatting import _clear_table_data, format_date_range
from ..normalization import _get_cleaned_institution_name
from ..parsing import _dates_overlap_or_match, _is_table_header_entry
from ..resolution import _get_institution_location
from ..sorting import element_idx_sort_key, sort_entries_reverse_chronological

# Section D taxonomy codes, in template table order (docs/CODING_STANDARDS.md
# §8.2): D1 Academic Appointments, D2 Hospital Appointments, D3 Other
# Professional Positions.
POSITION_TAXONOMY_CODES = ('D1', 'D2', 'D3')
ACADEMIC_APPOINTMENT_CODE, HOSPITAL_APPOINTMENT_CODE, OTHER_POSITION_CODE = POSITION_TAXONOMY_CODES

# #476: glyphs that mark a tab-joined child fragment as its own
# career-progression row (a title promoted/re-titled within the same
# appointment -- see _tab_joined_child_fragments below).
_CHILD_BULLET_GLYPHS = ('•', '-', '–', '*')

# Key `_propagate_institution_to_subentries` writes on a record whose
# institution it filled in from a parent row, holding that parent's
# `element_idx_start`. It is the only parent/child link this section has: the
# stage-4 records carry no parent identifier of their own, so the merge pass
# reads this key to tell an employer a record NAMED from one it INHERITED
# (#476 review items 1 and 2).
INHERITED_INSTITUTION_KEY = 'institution_propagated_from'

# A child fragment's own date range, e.g. "07/2002 - 06/2003" or
# "2002 - present" -- loose enough to accept the en/em dash the corpus uses
# interchangeably with a hyphen, strict enough that ordinary prose never
# matches (#476 measurement: the only farm entries with a tab followed by a
# bullet glyph are already this exact shape).
_CHILD_DATE_RANGE_RE = re.compile(
    r'^(\d{1,2}/\d{4}|\d{4})\s*[-–—]\s*'
    r'(\d{1,2}/\d{4}|\d{4}|present|current|ongoing)$',
    re.IGNORECASE,
)


def _split_child_date_range(fragment: str) -> tuple[str, str] | None:
    """(start, end) raw date strings out of a child fragment, or None when
    the fragment isn't shaped like a date range."""
    m = _CHILD_DATE_RANGE_RE.match(fragment.strip())
    return (m.group(1), m.group(2)) if m else None


def _tab_joined_child_fragments(text: str) -> list[tuple[str, tuple[str, str]]]:
    """Bullet-prefixed child appointments tab-joined onto a D1/D2/D3 entry's
    header row (#476) -- e.g. the -DAZFA D2 entry "...| 07/2002 - 12/2006\t*
    Attending Physician | 07/2002 - 06/2003\t* Attending Physician &
    Assistant Director | 06/2003 - 12/2006", which today renders only the
    header (positions.py is not an `entry_lines` call site; this is the
    tab-recovery path the issue names).

    `entry_fragments` flattens the whole text through '\\n', then '|', then
    '\\t' -- so a child's bullet-prefixed title and its own date range end up
    as ADJACENT fragments in the flat list even though the pipe split (which
    runs before the tab split) cuts across the tab boundary between the
    header's date field and the child's title. Scanning for a bullet-glyph
    fragment immediately followed by a date-range fragment recovers exactly
    the child unit, without re-deriving the tab/pipe nesting by hand.

    Gated on a literal tab in the raw text (not just the bullet+date shape)
    to keep this from ever firing on an entry that stage 4 already split
    into its own separate D-code records (MNZ7IA/ZZLKMA render the same
    Borman source as three such records, each with a bullet still in its raw
    text but no tab) -- the caller's duplicate-of-the-parent check is a
    second, independent guard for that case, not the only one.
    """
    if '\t' not in text:
        return []
    frags = entry_fragments(text)
    children = []
    i = 0
    while i < len(frags) - 1:
        frag = frags[i].strip()
        if frag[:1] in _CHILD_BULLET_GLYPHS:
            date_range = _split_child_date_range(frags[i + 1])
            if date_range:
                title = frag[1:].strip()
                if title:
                    children.append((title, date_range))
                i += 2
                continue
        i += 1
    return children


# Shortest text fragment that could name an employer -- anything below this is
# a stray delimiter or an initial, never an institution.
_MIN_INSTITUTION_FRAGMENT_CHARS = 3

# A source table's column header emitted as data ("Title", "Dates", "City").
_COLUMN_HEADER_FRAGMENT_RE = re.compile(r'^(title|institution|dates?|city|state|\d)')

# A location at the END of a fragment: ", NY", ", New York", ", Qatar". Anchored
# on purpose -- a location in the middle of a fragment means the fragment is a
# whole record line, not an employer (#476 review item 5).
_LOCATION_TAIL_RE = re.compile(r',\s*(?:[A-Z]{2}|[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\.?\s*$')

# A four-digit year anywhere in the fragment. An employer name does not carry
# one; a record line ("Aug 2019-Dec 2023, Associate Director, ...") does, and
# that is the shape the pre-#476 fallback kept mistaking for an institution.
_YEAR_IN_FRAGMENT_RE = re.compile(r'\b(?:19|20)\d{2}\b')


def _child_position_records(entry: dict) -> list[dict]:
    """The tab-joined child appointments of `entry`, as position records of
    their own (#476 review item 6).

    Rendering used to discover these mid-render, after the parent row had
    already been written, so the number of rows a section produced could not
    be read off its record list. They are recovered during normalization
    instead, and each one renders through the same `_add_position_row` as any
    other record.

    A child is the same appointment as its parent under a later title, so its
    record copies the parent's employer fields and enrichment verbatim and its
    Institution cell comes out identical to the parent's. It copies nothing
    else: no classification, coverage or comment fields, so a child row
    carries no Word comments, which is what the pre-#476 child rows did by
    passing `entry=None`. `entry` itself is never modified.

    A child whose title and formatted dates are the parent's own is dropped:
    stage 4 sometimes promotes a bullet-prefixed fragment to its own record
    (MNZ7IA, ZZLKMA), and that record arrives here as its own parent.
    """
    text = entry.get('text', '') or ''
    children = _tab_joined_child_fragments(text)
    if not children:
        return []
    fields = entry.get('extracted_fields', {}) or {}
    taxonomy_code = entry.get('taxonomy_code', ACADEMIC_APPOINTMENT_CODE)
    parent_title = PositionsSection._position_title(entry).lower()
    parent_dates = format_date_range(fields.get('start_date', ''),
                                     fields.get('end_date', ''), taxonomy_code)
    enrichment = entry.get('institution_enrichment')
    records = []
    for title, (start, end) in children:
        if (title.lower() == parent_title
                and format_date_range(start, end, taxonomy_code) == parent_dates):
            continue
        records.append({
            'text': text,
            'taxonomy_code': taxonomy_code,
            'institution_enrichment': dict(enrichment) if isinstance(enrichment, dict) else enrichment,
            'extracted_fields': {
                'title': title,
                'institution': fields.get('institution', ''),
                'organization': fields.get('organization', ''),
                'department': fields.get('department', ''),
                'start_date': start,
                'end_date': end,
            },
        })
    return records


def _names_employer_and_location(part: str) -> bool:
    """True when a text fragment names an employer AND its location, as in
    "Lincoln Hospital, Bronx, NY".

    The pre-#476 fallback took any City/State-shaped text as an institution
    candidate, so a bare "Bronx, NY" -- or a "Smith, John" -- could become the
    employer of a record that had none (#476 review item 5). Require the
    location to be a tail on something else: three or more comma-separated
    components, the last two being city and state/country, and a head that
    carries a word. And reject any fragment carrying a year, which makes it
    one of the record lines the entry's text is a list of, not a name.
    """
    if not _LOCATION_TAIL_RE.search(part):
        return False
    if _YEAR_IN_FRAGMENT_RE.search(part):
        return False
    components = [component.strip() for component in part.split(',')]
    if len(components) < 3:
        return False
    return bool(re.search(r'[A-Za-z]{3}', ' '.join(components[:-2])))


def _institution_from_raw_text(text: str) -> str:
    """Last-resort employer for a record whose extracted fields and stage-5b
    enrichment both name none: the first tab- or newline-separated fragment of
    its raw text that names an employer and a location, '' when none does.

    Structured extraction and enrichment are preferred and the caller checks
    both before calling this (#476 review item 5); this only decides what
    counts as a candidate once they have come back empty.
    """
    if '\t' not in text and '\n' not in text:
        return ''
    for part in re.split(r'[\t\n]', text):
        part = part.strip()
        if len(part) < _MIN_INSTITUTION_FRAGMENT_CHARS:
            continue
        if _COLUMN_HEADER_FRAGMENT_RE.match(part.lower()):
            continue
        if _names_employer_and_location(part):
            return part
    return ''


def _source_hierarchy(entry: dict) -> tuple[str, ...]:
    """The record's stage-1b heading path, as a comparable tuple."""
    hierarchy = entry.get('hierarchy')
    if isinstance(hierarchy, (list, tuple)):
        return tuple(str(level) for level in hierarchy)
    return (str(hierarchy),) if hierarchy else ()


def _crosses_source_boundary(donor: dict, entry: dict) -> bool:
    """True when `entry` sits in a different block of the source CV than
    `donor`, so an employer carried forward from `donor` is out of scope.

    Two boundaries, both from the source structure rather than from document
    order (#476 review item 3): a different heading path, and a different
    source table. A heading paragraph followed by the table of roles beneath
    it is NOT a boundary -- same heading path, and only one of the two sits
    in a table.

    Coarse by construction: stage 1b gives many CVs a single heading for the
    whole document, and paragraph records carry no table index, so on those
    records this gate allows what it allowed before. It stops propagation
    where the source says there is a break, which is the case the pass could
    not see at all before -- not everywhere one might exist.
    """
    if _source_hierarchy(donor) != _source_hierarchy(entry):
        return True
    donor_table = donor.get('table_index')
    entry_table = entry.get('table_index')
    return (donor_table is not None and entry_table is not None
            and donor_table != entry_table)


def _entry_employer(entry: dict) -> str:
    """The record's employer, lowercased for comparison, '' when it has none.
    D3 records carry it as `organization` rather than `institution`."""
    fields = entry.get('extracted_fields', {}) or {}
    return (fields.get('institution') or fields.get('organization') or '').strip().lower()


def _inherited_institution(entry: dict) -> bool:
    """True when this record's institution was filled in from a parent row by
    `_propagate_institution_to_subentries`, which records the parent's element
    index. This is the explicit source parent/child link #476 review items 1
    and 2 ask for in place of physical adjacency.

    Membership, not truthiness: a parent whose own `element_idx_start` is
    missing still records the link, and the record still counts as having
    inherited."""
    return INHERITED_INSTITUTION_KEY in entry


def _employer_claim(entry: dict) -> str:
    """The employer this record itself named, '' when it named none.

    An inherited institution is not a claim: the record named no employer, it
    was given one by its parent. Keeping the two apart is what lets the merge
    refuse a pair whose employers genuinely disagree without also refusing a
    sub-position that never named an employer at all.
    """
    return '' if _inherited_institution(entry) else _entry_employer(entry)


def _employers_match(first: str, second: str) -> bool:
    """True when two employer strings are BOTH known and name one employer.

    A missing employer never matches (#476 review item 2): unknown is not
    "equal by default", it is no evidence at all. The comparison is on
    alphanumeric word sets, so punctuation and word order do not matter, and
    a sub-unit of the same employer -- "Lincoln Hospital" against "Lincoln
    Hospital, Department of Emergency Medicine" -- still matches by subset,
    which is the case the pre-#476 rule was written for. Two employers with
    no word in common never match.
    """
    words_first = set(re.findall(r'[a-z0-9]+', first.lower()))
    words_second = set(re.findall(r'[a-z0-9]+', second.lower()))
    if not words_first or not words_second:
        return False
    return words_first <= words_second or words_second <= words_first


def _same_source_element(first: dict, second: dict) -> bool:
    """True when two records came out of the SAME source paragraph or table cell.

    This is the structural evidence #476 review item 1 asks for in place of
    physical adjacency: two records sharing one `element_idx_start`, or one
    (table, row) cell, are a single source line that field extraction split,
    not two appointments that merely sit next to each other in the document.
    """
    idx = first.get('element_idx_start')
    if idx is not None and idx == second.get('element_idx_start'):
        return True
    cell = (first.get('table_index'), first.get('row_index'))
    return (cell[0] is not None and cell[1] is not None
            and cell == (second.get('table_index'), second.get('row_index')))


def _is_one_appointment(first: dict, second: dict) -> bool:
    """True when there is evidence that two records are halves of ONE
    appointment. Document adjacency on its own is not evidence (#476 review
    items 1 and 2): in employment history a false merge invents a plausible
    appointment the CV never claimed and deletes the row it took the dates
    from, which is worse than leaving two rows unmerged.

    Three ways to qualify, weakest last:

    1. the two records came out of one source element — one line that field
       extraction split, so they cannot be two appointments;
    2. both records name an employer, and the two names match;
    3. one names an employer and the other named none but INHERITED one from
       a parent row, which `_propagate_institution_to_subentries` recorded —
       a sub-position under an employer heading, not a competing employer.

    Everything else fails closed, including the case the old rule was loosest
    on: two records that both name an employer and disagree, and two records
    that neither name nor inherit one.

    Rule 3 is the one that is still evidence-thin, and deliberately so. A
    record inherits from the nearest preceding row that named an employer,
    which is not always the row it is then compared against, so the rule also
    admits a title-only row that inherited employer X sitting next to a
    bare-dates row naming employer Y. Requiring the two effective employers to
    match instead was tried and reverted: the #156 fixture (I5NKUG) is exactly
    that shape and the merge there is the RIGHT one -- "Staff Nurse", which
    inherited "New York Presbyterian Hospital", merges with the bare-dates row
    of "Medical/Surgical Unit", a ward inside it that shares no word with its
    name. Refusing on the name mismatch put back the title-less dated row that
    issue is about. The stage-4 fields cannot tell a ward of the inherited
    employer from a different employer, so the merge stands and
    `_merge_grouped_appointments` counts it instead (the `unmatched_employer`
    tally there).
    """
    if _same_source_element(first, second):
        return True
    claim_first, claim_second = _employer_claim(first), _employer_claim(second)
    if claim_first and claim_second:
        return _employers_match(claim_first, claim_second)
    if claim_first or claim_second:
        return _inherited_institution(first) or _inherited_institution(second)
    return False


class PositionsSection:
    """Section D writers, mixed into `WCMTemplateGenerator`."""

    @staticmethod
    def _propagate_institution_to_subentries(entries: list[dict], verbose: bool = False) -> list[dict]:
        """Fill blank institutions from the nearest preceding entry that has one.

        Source CVs often list sub-positions as indented bullets under a parent
        institution.  Stage 4 field extraction treats each bullet as a separate
        entry but can't see the parent's institution.  This forward-propagates
        institution (and its enrichment data) in document order so sub-entries
        inherit their parent context.

        Document order alone is not enough to say two records belong together
        (#476 review item 3): if extraction loses an institution boundary, an
        employer would otherwise stay in scope for every later record in the
        list. `_crosses_source_boundary` stops the carry at a heading-path or
        source-table change, and the carried employer is dropped there rather
        than resumed after the unrelated record. Each filled-in institution
        records its parent under `INHERITED_INSTITUTION_KEY`, which is also
        what `_is_one_appointment` reads to tell an inherited institution from
        one the record named itself.
        """
        if not entries:
            return entries
        # Sort by document order (element_idx_start) to ensure parent comes first
        ordered = sorted(entries, key=lambda e: element_idx_sort_key(e.get('element_idx_start')))
        parent = None
        last_institution = None
        last_enrichment = None
        propagated = 0
        stopped = 0
        for entry in ordered:
            fields = entry.get('extracted_fields', {}) or {}
            inst = fields.get('institution') or fields.get('organization') or ''
            if inst:
                parent = entry
                last_institution = inst
                last_enrichment = entry.get('institution_enrichment')
                continue
            if parent is None:
                continue
            if _crosses_source_boundary(parent, entry):
                # A different block of the source CV: the employer carried
                # this far is out of scope, and stays out — a later record is
                # not re-attached to it across the unrelated one.
                parent = None
                last_institution = None
                last_enrichment = None
                stopped += 1
                continue
            # This entry has no institution — inherit from parent
            if not fields:
                entry['extracted_fields'] = fields = {}
            fields['institution'] = last_institution
            # Record which row it came from. Two passes need it: this one
            # only to be honest about provenance, and the merge pass to
            # tell an inherited institution from an employer the record
            # named itself (#476 review items 1 and 2).
            entry[INHERITED_INSTITUTION_KEY] = parent.get('element_idx_start')
            # Also propagate enrichment if available
            if last_enrichment and not entry.get('institution_enrichment'):
                entry['institution_enrichment'] = dict(last_enrichment)
            propagated += 1
        if verbose and propagated > 0:
            print(f"    Propagated institution to {propagated} sub-entries")
        if verbose and stopped > 0:
            print(f"    Stopped institution propagation at {stopped} source-structure boundaries")
        return entries

    # Title strings that field extraction sometimes emits when the source CV had
    # a column header instead of a real role (mirrors the filter in
    # ``_add_position_row``). Treated as "no title" for grouping purposes.
    _PLACEHOLDER_TITLES = frozenset({'title', 'position', 'role', 'name',
                                     'description', 'activity'})

    @classmethod
    def _position_title(cls, entry: dict) -> str:
        """Real title for a position entry, with column-header placeholders removed."""
        fields = entry.get('extracted_fields', {}) or {}
        title = (fields.get('title') or '').strip()
        if title.lower() in cls._PLACEHOLDER_TITLES:
            return ''
        return title

    @staticmethod
    def _position_has_dates(entry: dict) -> bool:
        """True if the entry carries any date of its own (start or end)."""
        fields = entry.get('extracted_fields', {}) or {}
        return bool(fields.get('start_date') or fields.get('end_date'))

    @classmethod
    def _merge_grouped_appointments(cls, entries: list[dict],
                                    verbose: bool = False) -> list[dict]:
        """Reassemble appointments fragmented across title / employer rows.

        Source CVs commonly list one employer with a date range on its own line
        and the several roles held there on the lines beneath it (or vice-versa:
        a role line followed by the unit + date range).  Stage 4 field extraction
        treats each line as a separate entry, so the same appointment is split
        into a title-less "employer + dates" row and one or more date-less
        "title only" rows.  Rendered straight, that produces blank-TITLE rows
        (which read as active/"Present" once sorted) and blank-DATES rows.

        This pass works in document order on a single taxonomy-code list (run
        after institution propagation) and applies three general rules:

        Rule 2 (header + children): a title-less dated entry immediately followed
            by one or more title-only entries at the same employer is an employer
            header over the roles held there — copy its dates onto each child and
            drop the now-redundant bare header.
        Rule 1 (adjacent pair): a remaining title-only entry document-adjacent to
            a title-less dated entry (either order) is one appointment split in
            two — copy the dates onto the titled row and drop the bare dates row.
        Rule 3 (employer summary): a title-less dated header whose date span is
            already covered by an overlapping *titled* row at the same employer is
            redundant — drop it (but keep it if it is the only record).

        Rules 2 and 1 both require `_is_one_appointment` — a known, matching
        employer or a shared source element — on top of adjacency (#476 review
        items 1 and 2). Adjacency alone used to be enough, and a missing
        employer used to count as a match, so a title-only row could inherit
        the dates of an unrelated employer's row and delete it: that is a
        plausible appointment the CV never claimed, and it happens on the
        corpus farm today (one D1 pair, employers with no word in common).

        Rules 2 then 1 run as separate passes so a header is never mistaken for a
        lone adjacent dates row. Dates are only ever *copied into* a row that
        lacks them; an entry that already carries its own dates is never
        overwritten. No titles or dates are fabricated — a row stays blank if the
        group genuinely has no source.
        """
        if not entries or len(entries) < 2:
            return entries

        ordered = sorted(entries,
                         key=lambda e: element_idx_sort_key(e.get('element_idx_start')))

        def _copy_dates(src: dict, dst: dict) -> None:
            src_f = src.get('extracted_fields', {}) or {}
            dst_f = dst.get('extracted_fields')
            if not dst_f:
                dst['extracted_fields'] = dst_f = {}
            if not (dst_f.get('start_date') or dst_f.get('end_date')):
                dst_f['start_date'] = src_f.get('start_date', '')
                dst_f['end_date'] = src_f.get('end_date', '')

        # Positions in `ordered`, never id() of the dicts (#476 review item 4):
        # the bookkeeping then reads as "row 4 was absorbed by row 3" -- the
        # thing the rules are actually about -- and a caller can reproduce it
        # without holding the same objects the pass ran on.
        dropped: set[int] = set()  # indices of header rows absorbed by children
        merged = 0
        # Merges that rest on rule 3 of `_is_one_appointment` alone: one row
        # inherited its employer from a parent row and the other names an
        # employer whose name does not match it. The pair is one appointment
        # when the named one is a ward or unit inside the inherited one (the
        # #156 fixture), and is not when it is a different employer -- a
        # distinction the stage-4 fields do not carry. Counted rather than
        # refused, so a run says how much of its merging rests on it.
        unmatched_employer = 0

        # Pass 1 — Rule 2: a title-less dated entry is an employer header; the
        # immediately-following title-only rows are the roles held there. Copy the
        # header's dates onto each child, then drop the redundant bare header.
        # A child must be evidently the header's own row: institution
        # propagation runs first and has already pushed the header's
        # institution onto its sub-rows, so a legitimate child arrives here
        # carrying that employer. A child whose employer is missing or
        # different ends the run instead of joining it -- unknown is not a
        # match (#476 review item 2). Done before Rule 1 so a header is never
        # mistaken for a lone adjacent dates row.
        for i, entry in enumerate(ordered):
            if i in dropped:
                continue
            if cls._position_title(entry) or not cls._position_has_dates(entry):
                continue
            children = []
            for j in range(i + 1, len(ordered)):
                if j in dropped:
                    continue
                nxt = ordered[j]
                if (cls._position_title(nxt) and not cls._position_has_dates(nxt)
                        and _is_one_appointment(entry, nxt)):
                    children.append(nxt)
                else:
                    break
            if children:
                for child in children:
                    _copy_dates(entry, child)
                    merged += 1
                    if not _employers_match(_entry_employer(entry),
                                            _entry_employer(child)):
                        unmatched_employer += 1
                dropped.add(i)

        # Pass 2 — Rule 1: a title-only row immediately adjacent (in document
        # order) to a remaining bare dates row, in either order, is one
        # appointment split across two lines (e.g. "Staff Nurse" /
        # "Medical/Surgical Unit (07/04-04/10)") -- but only where
        # `_is_one_appointment` finds the two halves actually related.
        # Adjacency alone is not the fingerprint (#476 review item 1); the
        # sub-unit/department case the loose rule was written for still
        # merges, because `_employers_match` matches a sub-unit against its
        # parent employer by word subset.
        def _date_neighbor(anchor: dict, j: int) -> int | None:
            """Index of a surviving bare-dates row at `j` that belongs to the
            same appointment as `anchor`, or None."""
            if not 0 <= j < len(ordered) or j in dropped:
                return None
            cand = ordered[j]
            if cls._position_title(cand) or not cls._position_has_dates(cand):
                return None
            if not _is_one_appointment(anchor, cand):
                return None
            return j

        for i, entry in enumerate(ordered):
            if i in dropped:
                continue
            if not cls._position_title(entry) or cls._position_has_dates(entry):
                continue
            neighbor = _date_neighbor(entry, i + 1)
            if neighbor is None:
                neighbor = _date_neighbor(entry, i - 1)
            if neighbor is not None:
                _copy_dates(ordered[neighbor], entry)
                dropped.add(neighbor)
                merged += 1
                if not _employers_match(_entry_employer(entry),
                                        _entry_employer(ordered[neighbor])):
                    unmatched_employer += 1

        # Rule 3: a title-less dated "employer summary" header whose date range is
        # already represented by titled sub-positions at the same employer is
        # redundant — its only content (institution + a date span) reappears, with
        # a title, on the rows beneath it.  Drop it so it does not render as a
        # blank-TITLE row.  Requires an overlapping *titled* sibling at the same
        # institution; a header with no such sibling is the sole record and kept.
        for i, entry in enumerate(ordered):
            if i in dropped:
                continue
            if cls._position_title(entry) or not cls._position_has_dates(entry):
                continue
            employer = _entry_employer(entry)
            if not employer:
                continue
            for j, other in enumerate(ordered):
                if j == i or j in dropped:
                    continue
                if not cls._position_title(other) or not cls._position_has_dates(other):
                    continue
                if _entry_employer(other) != employer:
                    continue
                if _dates_overlap_or_match(entry, other):
                    dropped.add(i)
                    merged += 1
                    break

        if verbose and unmatched_employer:
            print(f"    {unmatched_employer} of those merges matched no employer "
                  f"name; one row had inherited its employer from a parent row")
        if not dropped:
            if verbose and merged:
                print(f"    Merged dates into {merged} fragmented appointment rows")
            return entries

        result = [e for k, e in enumerate(ordered) if k not in dropped]
        if verbose:
            print(f"    Merged {len(dropped)} fragmented appointment row(s); "
                  f"propagated dates to {merged} role row(s)")
        return result

    def _fill_positions(self, entries_by_code: dict[str, list[dict]]):
        """Fill positions tables with track changes for enriched content.

        The WCM template has THREE separate position tables:
        1. Academic Appointments (D1) - faculty positions
        2. Hospital Appointments (D2) - clinical positions
        3. Other Professional Positions (D3) - non-academic positions

        Track changes are used for city/state from institution enrichment only.
        """
        d1_entries = entries_by_code.get(ACADEMIC_APPOINTMENT_CODE, [])
        d2_entries = entries_by_code.get(HOSPITAL_APPOINTMENT_CODE, [])
        d3_entries = entries_by_code.get(OTHER_POSITION_CODE, [])

        # Propagate institution from parent entries to blank sub-entries, then
        # reassemble appointments that were fragmented into separate title /
        # employer+dates rows (see _merge_grouped_appointments).
        for code, entry_list in zip(POSITION_TAXONOMY_CODES, (d1_entries, d2_entries, d3_entries)):
            self._propagate_institution_to_subentries(entry_list, verbose=self.verbose)
            merged = self._merge_grouped_appointments(entry_list, verbose=self.verbose)
            if merged is not entry_list:
                entry_list[:] = merged
                entries_by_code[code] = entry_list

        total_positions = len(d1_entries) + len(d2_entries) + len(d3_entries)
        if self.verbose:
            print(f"Filling Positions ({total_positions} entries)...")
            if d1_entries:
                print(f"  D1 Academic: {len(d1_entries)} entries")
            if d2_entries:
                print(f"  D2 Hospital: {len(d2_entries)} entries")
            if d3_entries:
                print(f"  D3 Other: {len(d3_entries)} entries")

        # Fill Academic Appointments table (D1)
        acad_idx = self._find_paragraph_with_text("Academic Appointments")
        if acad_idx is not None and d1_entries:
            acad_table = self._find_table_after_paragraph(acad_idx)
            if acad_table:
                _clear_table_data(acad_table, keep_header=True)
                self.stats['tables_populated'] += 1
                for position in self._normalized_positions(d1_entries):
                    self._add_position_row(acad_table, position)

        # Fill Hospital Appointments table (D2)
        hosp_idx = self._find_paragraph_with_text("Hospital Appointments")
        if hosp_idx is not None and d2_entries:
            hosp_table = self._find_table_after_paragraph(hosp_idx)
            if hosp_table:
                _clear_table_data(hosp_table, keep_header=True)
                self.stats['tables_populated'] += 1
                for position in self._normalized_positions(d2_entries):
                    self._add_position_row(hosp_table, position)

        # Fill Other Professional Positions table (D3)
        other_idx = self._find_paragraph_with_text("Other Professional Positions")
        if other_idx is not None and d3_entries:
            other_table = self._find_table_after_paragraph(other_idx)
            if other_table:
                _clear_table_data(other_table, keep_header=True)
                self.stats['tables_populated'] += 1
                for position in self._normalized_positions(d3_entries):
                    self._add_position_row(other_table, position)

        # Fallback: If no specific subsection tables found, use the generic PROFESSIONAL POSITIONS table
        if acad_idx is None and hosp_idx is None and other_idx is None:
            pos_idx = self._find_paragraph_with_text("PROFESSIONAL POSITIONS")
            if pos_idx is None:
                return

            table = self._find_table_after_paragraph(pos_idx)
            if not table:
                return

            _clear_table_data(table, keep_header=True)
            self.stats['tables_populated'] += 1

            # Combine all and sort
            all_entries = d1_entries + d2_entries + d3_entries
            for position in self._normalized_positions(all_entries):
                self._add_position_row(table, position)

    def _is_source_column_header(self, entry: dict) -> bool:
        """True for a source table's column-header row that field extraction
        emitted as a data record -- "Title | Institution | Dates" and the like.

        Lives here rather than in `_add_position_row` (#476 review items 6 and
        7): what does and does not become a row is a normalization decision,
        and taking it before rendering is what lets the render loop increment
        the row counter once per iteration with no early return under it.
        """
        fields = entry.get('extracted_fields', {}) or {}
        # Valid extracted fields win, even if the text looks like a header
        has_valid_fields = bool(
            fields.get('title') or
            fields.get('institution') or
            fields.get('organization') or
            (fields.get('start_date') and fields.get('end_date'))
        )
        if has_valid_fields:
            return False
        original_text = entry.get('text', '')
        if not _is_table_header_entry(original_text, ['title', 'institution', 'organization', 'dates', 'city', 'state', 'position']):
            return False
        if self.verbose:
            print(f"  Skipping position header entry: '{original_text[:50]}...'")
        return True

    def _normalized_positions(self, entries: list[dict]) -> list[dict]:
        """The position records a table renders, in rendered order.

        Everything that decides HOW MANY rows the section produces happens
        here (#476 review item 6): the reverse-chronological sort, dropping a
        source column-header row extraction mistook for data, and recovering a
        tab-joined child appointment as a record of its own, immediately after
        the parent it came from.

        So the caller is `for position in positions: self._add_position_row(...)`
        with nothing under it that can skip or add a row, and
        `entries_inserted` -- incremented once, at the end of
        `_add_table_row_with_mixed_content` (stage_6_word_template.py) --
        counts exactly one per physical row, child rows included (review item
        7). This method never modifies the records it is given.
        """
        positions = []
        for entry in sort_entries_reverse_chronological(entries):
            if self._is_source_column_header(entry):
                continue
            positions.append(entry)
            positions.extend(_child_position_records(entry))
        return positions

    def _add_position_row(self, table, entry: dict):
        """Render one normalized position record as one table row.

        Renders unconditionally: every record `_normalized_positions` yields
        becomes exactly one row (#476 review item 7).
        """
        fields = entry.get('extracted_fields', {}) or {}

        title = fields.get('title') or ''
        # Detect placeholder values that are actually column headers from source CV tables
        # e.g., field extraction returning "Title" when the CV had "Title | Institution | Dates"
        title_lower = title.strip().lower()
        if title_lower in ('title', 'position', 'role', 'name', 'description', 'activity'):
            title = ''
        # D3 entries often use 'organization' instead of 'institution' in field extraction
        raw_institution = fields.get('institution', '') or fields.get('organization', '')
        department = fields.get('department', '')

        # Structured sources first (#476 review item 5): the stage-5b cleaned
        # name, then the extracted field. The raw text is read only when both
        # come back empty -- the cleaned name already won over anything the
        # text scan produced, so consulting it first drops a scan whose result
        # was going to be discarded, and nothing else.
        # Don't overwrite valid extracted institutions like "Weill Cornell Medical College"
        # just because they don't include city/state (that comes from enrichment)
        cleaned_institution = _get_cleaned_institution_name(entry)
        if not raw_institution and not cleaned_institution:
            raw_institution = _institution_from_raw_text(entry.get('text', ''))

        # Use LLM-cleaned institution name (strips embedded location); fall back to raw field
        institution = cleaned_institution or raw_institution

        # Build base institution string with department
        institution_base = institution
        if department:
            institution_base = f"{institution}, {department}"

        # Location from Stage 5b enrichment
        location, location_is_enriched = _get_institution_location(entry)

        # Get taxonomy code for this entry (D1, D2, or D3)
        taxonomy_code = entry.get('taxonomy_code', 'D1')

        # Dates - format according to D1/D2/D3 requirements (mm/yy - mm/yy)
        start = fields.get('start_date', '')
        end = fields.get('end_date', '')
        dates = format_date_range(start, end, taxonomy_code)

        # Build cell contents with mixed normal/track-change content
        title_content = [(title, False, "")]

        # Check if location is already present in institution_base to avoid duplication
        # e.g., "University of Pittsburgh, Pittsburgh, PA" shouldn't get ", Pittsburgh, PA" appended again
        location_already_present = False
        if location and institution_base:
            # Check if city is already in the institution string
            location_parts = location.split(',')
            if location_parts:
                city = location_parts[0].strip()
                # Check for city name in institution (case-insensitive)
                if city.lower() in institution_base.lower():
                    location_already_present = True

        if location and location_is_enriched and not location_already_present:
            # Institution/dept is normal text, ", City, State" is track change
            if institution_base:
                institution_content = [
                    (institution_base, False, ""),
                    (f", {location}", True, "Institution Enrichment")
                ]
            else:
                institution_content = [(location, True, "Institution Enrichment")]
        elif location and not location_already_present:
            institution_full = f"{institution_base}, {location}" if institution_base else location
            institution_content = [(institution_full, False, "")]
        else:
            institution_content = [(institution_base, False, "")]

        dates_content = [(dates, False, "")]

        self._add_table_row_with_mixed_content(
            table,
            [title_content, institution_content, dates_content],
            entry=entry
            )
