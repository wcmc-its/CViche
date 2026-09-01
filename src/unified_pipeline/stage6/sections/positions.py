"""Section D: academic, hospital and other professional appointments (#398).

Three template tables (D1 academic, D2 hospital, D3 other) filled by one writer,
because the row shape is identical across them and only the source code list
differs.

Most of this module is not rendering. It is repair work on stage-4 output, and
it is why the section is 389 lines for an 85-line writer:

- `_propagate_institution_to_subentries` -- source CVs indent sub-positions
  under an employer heading, and field extraction sees each bullet on its own,
  so the institution has to be carried forward in document order.
- `_merge_grouped_appointments` -- the same appointment arrives split into a
  title-less "employer + dates" row and one or more date-less "title only"
  rows. Rendered straight that produces blank-TITLE rows, which read as
  active/"Present" once sorted, and blank-DATES rows.

`_position_title` and `_position_has_dates` are the predicates that merge pass
reads each record through, and `_PLACEHOLDER_TITLES` is the column-header
vocabulary `_position_title` filters against -- field extraction emits "Title"
or "Role" as a title when the source CV had a table header there. All three are
reached only from this module.
"""
import re
from typing import Dict, List

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


class PositionsSection:
    """Section D writers, mixed into `WCMTemplateGenerator`."""

    @staticmethod
    def _propagate_institution_to_subentries(entries: List[Dict], verbose: bool = False) -> List[Dict]:
        """Fill blank institutions from the nearest preceding entry that has one.

        Source CVs often list sub-positions as indented bullets under a parent
        institution.  Stage 4 field extraction treats each bullet as a separate
        entry but can't see the parent's institution.  This forward-propagates
        institution (and its enrichment data) in document order so sub-entries
        inherit their parent context.
        """
        if not entries:
            return entries
        # Sort by document order (element_idx_start) to ensure parent comes first
        ordered = sorted(entries, key=lambda e: element_idx_sort_key(e.get('element_idx_start')))
        last_institution = None
        last_enrichment = None
        propagated = 0
        for entry in ordered:
            fields = entry.get('extracted_fields', {}) or {}
            inst = fields.get('institution') or fields.get('organization') or ''
            if inst:
                last_institution = inst
                last_enrichment = entry.get('institution_enrichment')
            elif last_institution:
                # This entry has no institution — inherit from parent
                if not fields:
                    entry['extracted_fields'] = fields = {}
                fields['institution'] = last_institution
                # Also propagate enrichment if available
                if last_enrichment and not entry.get('institution_enrichment'):
                    entry['institution_enrichment'] = dict(last_enrichment)
                propagated += 1
        if verbose and propagated > 0:
            print(f"    Propagated institution to {propagated} sub-entries")
        return entries

    # Title strings that field extraction sometimes emits when the source CV had
    # a column header instead of a real role (mirrors the filter in
    # ``_add_position_row``). Treated as "no title" for grouping purposes.
    _PLACEHOLDER_TITLES = frozenset({'title', 'position', 'role', 'name',
                                     'description', 'activity'})

    @classmethod
    def _position_title(cls, entry: Dict) -> str:
        """Real title for a position entry, with column-header placeholders removed."""
        fields = entry.get('extracted_fields', {}) or {}
        title = (fields.get('title') or '').strip()
        if title.lower() in cls._PLACEHOLDER_TITLES:
            return ''
        return title

    @staticmethod
    def _position_has_dates(entry: Dict) -> bool:
        """True if the entry carries any date of its own (start or end)."""
        fields = entry.get('extracted_fields', {}) or {}
        return bool(fields.get('start_date') or fields.get('end_date'))

    @classmethod
    def _merge_grouped_appointments(cls, entries: List[Dict],
                                    verbose: bool = False) -> List[Dict]:
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

        def _copy_dates(src: Dict, dst: Dict) -> None:
            src_f = src.get('extracted_fields', {}) or {}
            dst_f = dst.get('extracted_fields')
            if not dst_f:
                dst['extracted_fields'] = dst_f = {}
            if not (dst_f.get('start_date') or dst_f.get('end_date')):
                dst_f['start_date'] = src_f.get('start_date', '')
                dst_f['end_date'] = src_f.get('end_date', '')

        def _employer(e: Dict) -> str:
            f = e.get('extracted_fields', {}) or {}
            return (f.get('institution') or f.get('organization') or '').strip().lower()

        dropped = set()  # id() of header entries fully absorbed by children
        merged = 0

        # Pass 1 — Rule 2: a title-less dated entry is an employer header; the
        # immediately-following title-only rows are the roles held there. Copy the
        # header's dates onto each child, then drop the redundant bare header.
        # Children must share the header's employer (institution propagation has
        # already pushed the header's institution onto its sub-rows, so a mismatch
        # means the run has reached a different employer). Done before Rule 1 so a
        # header is never mistaken for a lone adjacent dates row.
        for i, entry in enumerate(ordered):
            if id(entry) in dropped:
                continue
            if cls._position_title(entry) or not cls._position_has_dates(entry):
                continue
            header_employer = _employer(entry)
            children = []
            for nxt in ordered[i + 1:]:
                if id(nxt) in dropped:
                    continue
                nxt_employer = _employer(nxt)
                same_employer = (not nxt_employer or not header_employer
                                 or nxt_employer == header_employer)
                if (cls._position_title(nxt) and not cls._position_has_dates(nxt)
                        and same_employer):
                    children.append(nxt)
                else:
                    break
            if children:
                for child in children:
                    _copy_dates(entry, child)
                    merged += 1
                dropped.add(id(entry))

        # Pass 2 — Rule 1: a title-only row immediately adjacent (in document
        # order) to a remaining bare dates row, in either order, is one
        # appointment split across two lines (e.g. "Staff Nurse" /
        # "Medical/Surgical Unit (07/04-04/10)"). Physical adjacency is the
        # fingerprint; the bare dates row often carries a sub-unit/department in
        # its institution field rather than a distinct employer, so the employer
        # strings need not match here.
        for i, entry in enumerate(ordered):
            if id(entry) in dropped:
                continue
            if not cls._position_title(entry) or cls._position_has_dates(entry):
                continue

            def _date_neighbor(cand):
                if cand is None or id(cand) in dropped:
                    return None
                if cls._position_title(cand) or not cls._position_has_dates(cand):
                    return None
                return cand

            neighbor = _date_neighbor(ordered[i + 1] if i + 1 < len(ordered) else None)
            if neighbor is None:
                neighbor = _date_neighbor(ordered[i - 1] if i > 0 else None)
            if neighbor is not None:
                _copy_dates(neighbor, entry)
                dropped.add(id(neighbor))
                merged += 1

        # Rule 3: a title-less dated "employer summary" header whose date range is
        # already represented by titled sub-positions at the same employer is
        # redundant — its only content (institution + a date span) reappears, with
        # a title, on the rows beneath it.  Drop it so it does not render as a
        # blank-TITLE row.  Requires an overlapping *titled* sibling at the same
        # institution; a header with no such sibling is the sole record and kept.
        for entry in ordered:
            if id(entry) in dropped:
                continue
            if cls._position_title(entry) or not cls._position_has_dates(entry):
                continue
            employer = _employer(entry)
            if not employer:
                continue
            for other in ordered:
                if other is entry or id(other) in dropped:
                    continue
                if not cls._position_title(other) or not cls._position_has_dates(other):
                    continue
                if _employer(other) != employer:
                    continue
                if _dates_overlap_or_match(entry, other):
                    dropped.add(id(entry))
                    merged += 1
                    break

        if not dropped:
            if verbose and merged:
                print(f"    Merged dates into {merged} fragmented appointment rows")
            return entries

        result = [e for e in ordered if id(e) not in dropped]
        if verbose:
            print(f"    Merged {len(dropped)} fragmented appointment row(s); "
                  f"propagated dates to {merged} role row(s)")
        return result

    def _fill_positions(self, entries_by_code: Dict[str, List[Dict]]):
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
                sorted_d1 = sort_entries_reverse_chronological(d1_entries)
                for entry in sorted_d1:
                    self._add_position_row(acad_table, entry)

        # Fill Hospital Appointments table (D2)
        hosp_idx = self._find_paragraph_with_text("Hospital Appointments")
        if hosp_idx is not None and d2_entries:
            hosp_table = self._find_table_after_paragraph(hosp_idx)
            if hosp_table:
                _clear_table_data(hosp_table, keep_header=True)
                self.stats['tables_populated'] += 1
                sorted_d2 = sort_entries_reverse_chronological(d2_entries)
                for entry in sorted_d2:
                    self._add_position_row(hosp_table, entry)

        # Fill Other Professional Positions table (D3)
        other_idx = self._find_paragraph_with_text("Other Professional Positions")
        if other_idx is not None and d3_entries:
            other_table = self._find_table_after_paragraph(other_idx)
            if other_table:
                _clear_table_data(other_table, keep_header=True)
                self.stats['tables_populated'] += 1
                sorted_d3 = sort_entries_reverse_chronological(d3_entries)
                for entry in sorted_d3:
                    self._add_position_row(other_table, entry)

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
            sorted_entries = sort_entries_reverse_chronological(all_entries)
            for entry in sorted_entries:
                self._add_position_row(table, entry)

    def _add_position_row(self, table, entry: Dict):
        """Add a single position entry to a table."""
        original_text = entry.get('text', '')
        fields = entry.get('extracted_fields', {}) or {}

        # Check if we have valid extracted fields - if so, use them even if text looks like a header
        has_valid_fields = bool(
            fields.get('title') or
            fields.get('institution') or
            fields.get('organization') or
            (fields.get('start_date') and fields.get('end_date'))
        )

        # Skip table header entries that were mistakenly extracted as data
        # BUT only if we don't have valid extracted fields to work with
        if not has_valid_fields and _is_table_header_entry(original_text, ['title', 'institution', 'organization', 'dates', 'city', 'state', 'position']):
            if self.verbose:
                print(f"  Skipping position header entry: '{original_text[:50]}...'")
            return

        title = fields.get('title') or ''
        # Detect placeholder values that are actually column headers from source CV tables
        # e.g., field extraction returning "Title" when the CV had "Title | Institution | Dates"
        title_lower = title.strip().lower()
        if title_lower in ('title', 'position', 'role', 'name', 'description', 'activity'):
            title = ''
        # D3 entries often use 'organization' instead of 'institution' in field extraction
        raw_institution = fields.get('institution', '') or fields.get('organization', '')
        department = fields.get('department', '')

        # Only try to recover institution from raw text if it's actually EMPTY
        # Don't overwrite valid extracted institutions like "Weill Cornell Medical College"
        # just because they don't include city/state (that comes from enrichment)
        if not raw_institution:
            raw_text = entry.get('text', '')
            # Try to extract institution from tab-separated or newline-separated text
            if '\t' in raw_text or '\n' in raw_text:
                parts = re.split(r'[\t\n]', raw_text)
                for part in parts:
                    part = part.strip()
                    # Skip parts that look like titles, dates, or headers
                    if not part or len(part) < 3:
                        continue
                    if re.match(r'^(title|institution|dates?|city|state|\d)', part.lower()):
                        continue
                    # Check for location patterns (City, State or Organization City, State)
                    if re.search(r',\s*[A-Z]{2}\b', part) or re.search(r'\b[A-Z][a-z]+,\s*[A-Z][A-Za-z]', part):
                        raw_institution = part
                        break

        # Use LLM-cleaned institution name (strips embedded location); fall back to raw field
        institution = _get_cleaned_institution_name(entry) or raw_institution

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
